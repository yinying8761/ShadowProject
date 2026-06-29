import asyncio
import json
from typing import AsyncIterator, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession

from core.prompt_manager import PromptManager
from core.conversation_manager import ConversationManager
from core.tool_registry import tool_registry
from services.llm_service import LLMService
from services.memory_service import memory_service, pop_memory_notifications, push_memory_notification
from models.character import CharacterProfile
from models.conversation import Conversation

ApprovalCallback = Callable[[str, dict], Awaitable[bool]]


class Agent:
    """Central orchestrator: prompt assembly -> LLM call -> tool execution loop."""

    def __init__(self, llm_service: LLMService | None = None):
        self.prompt_manager = PromptManager()
        self.conversation_manager = ConversationManager()
        self.llm_service = llm_service or LLMService()

    async def run(
        self,
        session: AsyncSession,
        user_message: str | None,
        conversation_id: str,
        character_id: str,
        approval_callback: ApprovalCallback | None = None,
        proactive_hint: str | None = None,
        force_tool_context: list[dict] | None = None,
        suppress_tool_calls: bool = False,
    ) -> AsyncIterator[dict]:
        """
        Execute the agent loop, yielding events.

        If user_message is None and proactive_hint is provided, runs in
        proactive mode: no user message is added to history, the system
        prompt instructs the character to spontaneously initiate a short
        message. If the model returns the literal string "__SKIP__", we
        emit an "abort" event instead of saving the response.
        """
        character = await session.get(CharacterProfile, character_id)
        if not character:
            yield {"type": "error", "message": f"Character {character_id} not found"}
            return

        # Emit any pending memory-update notifications from prior background tasks
        pending_count = pop_memory_notifications(conversation_id)
        if pending_count > 0:
            yield {"type": "memory_updated", "count": pending_count}

        # Load conversation summary from DB
        conv = await session.get(Conversation, conversation_id)
        conversation_summary = conv.summary if conv else None

        # Trigger summarization when conversation grows long
        msg_count = await self.conversation_manager.count_messages(
            session, conversation_id
        )
        if msg_count > 30:
            await self.conversation_manager.summarize_and_trim(
                session, conversation_id,
                keep_count=20,
                llm_service=self.llm_service,
            )
            # Reload summary after summarization
            await session.refresh(conv) if conv else None
            conversation_summary = conv.summary if conv else None

        # Retrieve relevant memories
        retrieved_memories = await memory_service.search(
            session,
            query=user_message or "",
            top_k=3,
        )
        memory_texts = [m.content for m in retrieved_memories]

        system_prompt = self.prompt_manager.build_system_prompt(
            character_name=character.name,
            personality=character.personality,
            role=character.role,
            archetype=character.archetype,
            gender=character.gender,
            voice_style=character.voice_style,
            proactive_hint=proactive_hint,
            conversation_summary=conversation_summary,
            retrieved_memories=memory_texts,
        )

        if user_message:
            await self.conversation_manager.add_message(
                session, conversation_id, "user", user_message
            )

        history = await self.conversation_manager.get_context_messages(
            session, conversation_id
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        # In proactive mode, prepend a synthetic user nudge so the
        # OpenAI/Anthropic API has something to respond to even when
        # there is no real user input this turn. The hint already lives
        # in the system prompt; this is just to satisfy the API contract.
        is_proactive = user_message is None and proactive_hint is not None
        if is_proactive:
            messages.append({
                "role": "user",
                "content": "(系统：现在轮到你主动说话，请遵守 system prompt 的主动陪伴规则。)",
            })

        if force_tool_context:
            messages.extend(force_tool_context)

        tools = tool_registry.get_tool_definitions()

        max_tool_rounds = 5 if not is_proactive else 1
        full_response = ""

        for round_num in range(max_tool_rounds):
            tool_use_blocks: list[dict] = []
            round_text = ""

            async for event in self.llm_service.stream_chat(
                messages=messages,
                tools=None if suppress_tool_calls or is_proactive else (tools if tools else None),
            ):
                if event["type"] == "token":
                    round_text += event["content"]
                    yield event
                elif event["type"] == "tool_use":
                    tool_use_blocks.append({
                        "id": event.get("id", f"call_{len(tool_use_blocks)}"),
                        "name": event["name"],
                        "arguments": event["arguments"],
                    })
                    yield event
                elif event["type"] == "error":
                    yield event
                    return

            full_response += round_text

            if not tool_use_blocks:
                # Proactive SKIP path: don't persist, signal abort.
                if is_proactive:
                    if "__SKIP__" in full_response.strip():
                        yield {"type": "proactive_skip"}
                        return
                    msg = await self.conversation_manager.add_message(
                        session, conversation_id, "assistant", full_response
                    )
                    yield {"type": "done", "message_id": msg.id, "proactive": True}
                    asyncio.create_task(
                        self._extract_memories_background(conversation_id)
                    )
                    return

                msg = await self.conversation_manager.add_message(
                    session, conversation_id, "assistant", full_response
                )
                yield {"type": "done", "message_id": msg.id}
                asyncio.create_task(
                    self._extract_memories_background(conversation_id)
                )
                return

            messages.append({
                "role": "assistant",
                "content": round_text,
                "tool_calls": [
                    {
                        "id": tb["id"],
                        "type": "function",
                        "function": {
                            "name": tb["name"],
                            "arguments": json.dumps(tb["arguments"]),
                        },
                    }
                    for tb in tool_use_blocks
                ],
            })

            tool_results = await self._execute_tools_with_approval(
                tool_use_blocks, approval_callback
            )
            for tr_event, tool_msg in tool_results:
                yield tr_event
                messages.append(tool_msg)
                if tr_event.get("name") == "save_memory" and not tr_event.get("is_error"):
                    yield {"type": "memory_updated", "count": 1}

        msg = await self.conversation_manager.add_message(
            session, conversation_id, "assistant",
            full_response or "I've used several tools but reached the limit."
        )
        yield {"type": "done", "message_id": msg.id}

        # Schedule background memory extraction (fire-and-forget)
        asyncio.create_task(
            self._extract_memories_background(conversation_id)
        )

    async def run_daily_greeting(
        self,
        session: AsyncSession,
        conversation_id: str,
        character_id: str,
        location: dict | None = None,
        weather: dict | None = None,
        days_since_last: int = 0,
        memories: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        """Generate a context-rich daily greeting. Only once per day (4am reset)."""
        from datetime import datetime

        char = await session.get(CharacterProfile, character_id)
        if not char:
            yield {"type": "error", "message": f"Character {character_id} not found"}
            return

        now = datetime.now()
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        hour = now.hour

        # Time-of-day hint
        if 5 <= hour < 9:
            time_hint = "早上"
        elif 9 <= hour < 11:
            time_hint = "上午"
        elif 11 <= hour < 13:
            time_hint = "中午/饭点"
        elif 13 <= hour < 18:
            time_hint = "下午"
        elif 18 <= hour < 22:
            time_hint = "晚上"
        else:
            time_hint = "深夜"

        # Build context-rich greeting prompt
        prompt_parts = [
            f"你是{char.name}。现在是{time_str}，{time_hint}时段。",
            "这是用户今天第一次打开窗口和你见面。请主动、自然地打个招呼。",
        ]

        if days_since_last >= 2:
            prompt_parts.append(
                f"用户已经{days_since_last}天没来了——表达一下想念，但不要夸张，"
                "保持在角色性格范围内。"
            )
        elif days_since_last == 1:
            prompt_parts.append('用户昨天来过，今天又来了。可以简单说一句「又见面了」之类的话。')

        if location:
            city = location.get("city", "")
            prompt_parts.append(f"用户在{city}。")

        if weather:
            prompt_parts.append(
                f"当地天气：{weather['condition']}，{weather['temp']}°C，"
                f"湿度{weather['humidity']}%，{weather['wind']}。"
            )

        if memories:
            prompt_parts.append("你记得这些事情：")
            for m in memories:
                prompt_parts.append(f"· {m}")

        prompt_parts.extend([
            "",
            "要求：",
            "- 1-3句话即可，自然、温暖、保持你的人设。",
            "- 根据时段搭话：饭点可以聊吃的（结合当地特色菜），深夜关心休息，早上可以问好。",
            "- 如果天气特别（下雨、高温、寒潮），顺带提一句。",
            '- 如果记得上次聊的事，自然追问「上次那个XX后来怎么样了？」',
            '- 不要提工具、不要提AI、不要用「检测到」「根据系统」之类的词。',
            "- 不要调用任何工具，纯聊天。",
        ])

        system_prompt = "\n".join(prompt_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "（系统：现在是每日问候时刻，请主动和用户打招呼。）"},
        ]

        full_response = ""
        async for event in self.llm_service.stream_chat(
            messages=messages,
            tools=None,  # no tools for greeting
        ):
            if event["type"] == "token":
                full_response += event["content"]
                yield {"type": "token", "content": event["content"], "daily_greeting": True}
            elif event["type"] == "error":
                yield event
                return

        if full_response.strip():
            msg = await self.conversation_manager.add_message(
                session, conversation_id, "assistant", full_response
            )
            yield {"type": "done", "message_id": msg.id, "daily_greeting": True}
        else:
            yield {"type": "daily_greeting_skip"}

    async def _extract_memories_background(self, conversation_id: str):
        """Run memory extraction in background after the agent responds."""
        try:
            await memory_service.extract_and_store(
                conversation_id=conversation_id,
                llm_service=self.llm_service,
            )
        except Exception as e:
            print(f"[Agent] background memory extraction failed: {e}", flush=True)

    async def _execute_tools_with_approval(
        self,
        tool_use_blocks: list[dict],
        approval_callback: ApprovalCallback | None,
    ) -> list[tuple[dict, dict]]:
        """Execute tools, requesting user approval for those that require it."""
        out: list[tuple[dict, dict]] = []

        for tb in tool_use_blocks:
            needs_approval = tool_registry.needs_approval(tb["name"])

            if needs_approval and approval_callback:
                approved = await approval_callback(tb["name"], tb["arguments"])
                if not approved:
                    content = "User denied this operation."
                    event = {
                        "type": "tool_result",
                        "name": tb["name"],
                        "result": content,
                        "is_error": True,
                        "denied": True,
                    }
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tb["id"],
                        "content": content,
                    }
                    out.append((event, tool_msg))
                    continue

            try:
                result = await tool_registry.dispatch(tb["name"], tb["arguments"])
                content = str(result)
                event = {
                    "type": "tool_result",
                    "name": tb["name"],
                    "result": content,
                    "is_error": False,
                }
            except Exception as e:
                content = f"Error: {e}"
                event = {
                    "type": "tool_result",
                    "name": tb["name"],
                    "result": content,
                    "is_error": True,
                }

            tool_msg = {
                "role": "tool",
                "tool_call_id": tb["id"],
                "content": content,
            }
            out.append((event, tool_msg))

        return out
