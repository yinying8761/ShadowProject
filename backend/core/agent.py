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
