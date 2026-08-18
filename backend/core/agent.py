import asyncio
import json
from typing import AsyncIterator, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession

from core.prompt_manager import PromptManager
from core.conversation_manager import ConversationManager
from core.tool_runtime import ToolRuntime
from services.llm_service import LLMService
from services.memory_service import memory_service, pop_memory_notifications
from models.character import CharacterProfile
from models.conversation import Conversation

ApprovalCallback = Callable[[str, dict], Awaitable[bool]]


class Agent:
    """Central orchestrator: prompt assembly -> LLM call -> tool execution loop."""

    def __init__(
        self,
        llm_service: LLMService | None = None,
        tool_registry: ToolRuntime | None = None,
        usage_store: "LLMUsageStore | None" = None,
    ):
        self.prompt_manager = PromptManager()
        self.conversation_manager = ConversationManager()
        self.llm_service = llm_service or LLMService()
        self.tool_registry = tool_registry or ToolRuntime()
        self._usage_store = usage_store

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
        mode: str = "chat",
        extra_context: dict | None = None,
        client_message_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """
        Execute the agent loop, yielding events.

        Parameters
        ----------
        mode:
            ``"chat"`` (default) — full agent loop with tools, memory
            retrieval, conversation history, and location context from DB.

            ``"greeting"`` — context-rich daily greeting.  No tools, no
            agent loop, no history.  Context (location, weather, memories,
            days_since_last) is drawn from *extra_context* rather than
            fetched internally.  Events carry ``daily_greeting: True``.
        extra_context:
            Used when *mode* is ``"greeting"``.  Expected keys:
            ``location``, ``weather``, ``days_since_last``, ``memories``.
        client_message_id:
            Optional client-side temporary id for the user message.  When
            a user message is persisted, a ``message_ack`` event echoes it
            back together with the real message UUID.
        """
        character = await session.get(CharacterProfile, character_id)
        if not character:
            yield {"type": "error", "message": f"Character {character_id} not found"}
            return

        # ── Greeting mode ────────────────────────────────────────────
        if mode == "greeting":
            extra = extra_context or {}
            greeting_prompt = self.prompt_manager.build_greeting_prompt(
                character_name=character.name,
                location=extra.get("location"),
                weather=extra.get("weather"),
                days_since_last=extra.get("days_since_last", 0),
                memories=extra.get("memories"),
            )

            messages = [
                {"role": "system", "content": greeting_prompt},
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
                    if full_response.strip():
                        msg = await self.conversation_manager.add_message(
                            session, conversation_id, "assistant", full_response
                        )
                        yield {"type": "done", "message_id": msg.id, "daily_greeting": True, "partial_error": True}
                    yield event
                    return

            if full_response.strip():
                msg = await self.conversation_manager.add_message(
                    session, conversation_id, "assistant", full_response
                )
                yield {"type": "done", "message_id": msg.id, "daily_greeting": True}
            else:
                print(
                    f"[Agent] greeting was empty or SKIP, full_response={repr(full_response[:100])}",
                    flush=True,
                )
                yield {"type": "daily_greeting_skip"}
            return

        # ── Chat / proactive mode ─────────────────────────────────────
        # Emit any pending memory-update notifications from prior background tasks
        pending_count = pop_memory_notifications(conversation_id)
        if pending_count > 0:
            yield {"type": "memory_updated", "count": pending_count}

        # Load conversation summary from DB
        conv = await session.get(Conversation, conversation_id)
        conversation_summary = None
        if conv and conv.summary:
            date_label = PromptManager.format_relative_date(conv.updated_at)
            conversation_summary = f"(截至{date_label}) {conv.summary}"

        # Trigger summarization in background when conversation grows long
        msg_count = await self.conversation_manager.count_messages(
            session, conversation_id
        )
        if msg_count > 30:
            asyncio.create_task(
                self._summarize_background(conversation_id)
            )

        # Retrieve relevant memories
        retrieved_memories = await memory_service.search(
            session,
            query=user_message or "",
            top_k=3,
            character_id=character_id,
        )
        memory_texts = [
            f"({PromptManager.format_relative_date(m.created_at)}) {m.content}"
            for m in retrieved_memories
        ]

        # Load location context from cached UserConfig
        location_context = ""
        try:
            from models.user_config import UserConfig
            cfg = await session.get(UserConfig, 1)
            if cfg and cfg.location_city:
                parts = [f"用户当前在{cfg.location_city}"]
                if cfg.location_weather:
                    parts.append(f"当地天气：{cfg.location_weather}")
                parts.append("在对话中自然地运用这些信息——比如聊到吃的可以结合当地特色，聊到天气可以提一下实际天气。不要刻意强调你知道位置。")
                location_context = "。".join(parts)
        except Exception:
            pass

        # Load user profile for this character (fallback to default)
        user_profile = None
        try:
            from models.user_profile import UserProfile
            from sqlalchemy import select, desc
            result = await session.execute(
                select(UserProfile)
                .where(UserProfile.character_id == character_id)
                .order_by(desc(UserProfile.updated_at))
                .limit(1)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                result = await session.execute(
                    select(UserProfile)
                    .where(UserProfile.character_id.is_(None))
                    .order_by(desc(UserProfile.updated_at))
                    .limit(1)
                )
                profile = result.scalar_one_or_none()
            if profile:
                user_profile = {
                    "user_name": profile.user_name,
                    "user_gender": profile.user_gender,
                    "user_occupation": profile.user_occupation,
                    "user_relationship": profile.user_relationship,
                    "user_bio": profile.user_bio,
                }
        except Exception:
            pass

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
            user_profile=user_profile,
        )

        if user_message:
            user_msg = await self.conversation_manager.add_message(
                session, conversation_id, "user", user_message
            )
            # Ack the persisted user message's real UUID so the client can
            # replace its temporary local id (issue #10 / ticket #11).
            # Emitted before the first token so deletion works mid-stream.
            # Only emitted when the client opted in with a client_message_id.
            if client_message_id:
                yield {
                    "type": "message_ack",
                    "client_message_id": client_message_id,
                    "message_id": user_msg.id,
                }

        history = await self.conversation_manager.get_context_messages(
            session, conversation_id
        )

        if location_context:
            system_prompt += f"\n\n## 位置与环境\n{location_context}"

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

        tools = self.tool_registry.get_tool_definitions()

        max_tool_rounds = 5 if not is_proactive else 1
        full_response = ""

        for round_num in range(max_tool_rounds):
            tool_use_blocks: list[dict] = []
            round_text = ""
            usage = None

            # Pre-estimate the prompt tokens about to be sent (Workflow G).
            # Estimation never interrupts the request — tokenizer problems
            # degrade to a character heuristic internally.
            estimated_prompt_tokens = await self.llm_service.estimate_prompt_tokens(messages)

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
                elif event["type"] == "usage":
                    usage = event
                elif event["type"] == "error":
                    # Stream error — save partial response so the frontend
                    # can finalize the streaming message, then surface the error.
                    if round_text.strip():
                        msg = await self.conversation_manager.add_message(
                            session, conversation_id, "assistant", round_text
                        )
                        done = {"type": "done", "message_id": msg.id, "partial_error": True}
                        if is_proactive:
                            done["proactive"] = True
                        yield done
                    yield event
                    return

            # Persist the authoritative usage for this round once the stream
            # completed and the API reported it (Workflow G). Stream errors
            # return above, so a missing usage event simply means no record.
            if usage is not None:
                try:
                    await self._get_usage_store().save(
                        conversation_id=conversation_id,
                        round_num=round_num,
                        model=usage.get("model", ""),
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                        estimated_prompt_tokens=estimated_prompt_tokens,
                    )
                except Exception as exc:
                    print(f"[Agent] usage save failed: {exc}", flush=True)

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
                    return

                msg = await self.conversation_manager.add_message(
                    session, conversation_id, "assistant", full_response
                )
                yield {"type": "done", "message_id": msg.id}
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
                tool_use_blocks, approval_callback, character_id
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

    async def _summarize_background(self, conversation_id: str):
        """Summarize old messages in background — avoids blocking the user."""
        try:
            from database import async_session

            async with async_session() as bg_session:
                await self.conversation_manager.summarize_and_trim(
                    bg_session,
                    conversation_id,
                    keep_count=20,
                    llm_service=self.llm_service,
                )
        except Exception as e:
            print(f"[Agent] background summarization failed: {e}", flush=True)

    def _resolve_handler(self, tool_name: str):
        """Return the callable that handles *tool_name*.

        Currently returns ``self.tool_registry.dispatch`` directly.
        When RouterAgent is enabled (≥2 sub-agents), it can intercept
        here and route to the appropriate sub-agent.
        """
        return self.tool_registry.dispatch

    async def _execute_tools_with_approval(
        self,
        tool_use_blocks: list[dict],
        approval_callback: ApprovalCallback | None,
        character_id: str | None = None,
    ) -> list[tuple[dict, dict]]:
        """Execute tools, requesting user approval for those that require it."""
        out: list[tuple[dict, dict]] = []

        for tb in tool_use_blocks:
            # Inject character_id for memory-related tools (LLM doesn't know
            # about it — the agent injects it from the current context).
            # Copy arguments to avoid mutating the original tool_use_blocks dict.
            if character_id and tb["name"] in ("save_memory", "search_memory"):
                tb["arguments"] = {**tb["arguments"], "character_id": character_id}

            needs_approval = self.tool_registry.needs_approval(tb["name"])

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
                handler = self._resolve_handler(tb["name"])
                result = await handler(tb["name"], tb["arguments"])
                content = str(result)
                is_error = False
                # dispatch() returns JSON error strings for tool failures
                # (unknown tool, handler exception) — unwrap them so the
                # caller sees a clean error message and is_error: True.
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "error" in parsed:
                        is_error = True
                        content = parsed["error"]
                except (json.JSONDecodeError, TypeError):
                    pass
                event = {
                    "type": "tool_result",
                    "name": tb["name"],
                    "result": content,
                    "is_error": is_error,
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

    def _get_usage_store(self) -> "LLMUsageStore":
        """Lazy-init the token usage store (avoids import at module level)."""
        if self._usage_store is None:
            from services.usage_store import LLMUsageStore
            self._usage_store = LLMUsageStore()
        return self._usage_store
