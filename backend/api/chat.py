import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent import Agent
from core.conversation_manager import ConversationManager
from database import get_session
from models.user_config import UserConfig
from tools.screen_tools import ScreenFingerprintStore

router = APIRouter()
agent = Agent()
conv_manager = ConversationManager()

#: 会话不存在的统一拒绝载荷（HTTP 走 404，WS 发这个再关连接，code 4404）。
#: 成因与复现见 docs/analysis/group-chat-review-and-orphan-data.md §B2.3。
CONVERSATION_NOT_FOUND_EVENT = {
    "type": "error",
    "code": "conversation_not_found",
    "message": "Conversation not found",
}


from services.screen_capture_gate import detects_screen_intent


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    character_id: str
    model: str | None = None
    client_message_id: str | None = None


@router.post("/api/chat/send")
async def send_chat(req: ChatRequest, session: AsyncSession = Depends(get_session)):
    conv_id = req.conversation_id
    if conv_id:
        # 会话必须真实存在：不存在就 404 拒绝。否则 agent.run 会宽容地继续、
        # 往一个没人拥有的 conversation_id 里落库 —— 那正是孤儿消息的来源。
        from models.conversation import Conversation

        if await session.get(Conversation, conv_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conv = await conv_manager.get_or_create_conversation(session, req.character_id)
        conv_id = conv.id

    full_response = ""
    user_message_id = ""
    try:
        async for event in agent.run(
            session=session,
            user_message=req.message,
            conversation_id=conv_id,
            character_id=req.character_id,
            client_message_id=req.client_message_id,
        ):
            if event["type"] == "token":
                full_response += event["content"]
            elif event["type"] == "message_ack":
                # Real UUID of the persisted user message (issue #10 / ticket #11)
                user_message_id = event["message_id"]
            elif event["type"] == "done":
                resp = {
                    "conversation_id": conv_id,
                    "message_id": event["message_id"],
                    "content": full_response,
                }
                # Only added when the client opted in with client_message_id,
                # keeping the response shape unchanged otherwise.
                if req.client_message_id:
                    resp["user_message_id"] = user_message_id
                return resp
            elif event["type"] == "error":
                raise HTTPException(status_code=500, detail=event["message"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"conversation_id": conv_id, "content": full_response}


@router.websocket("/ws/chat/{conversation_id}")
async def ws_chat(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    print(f"[WS] connected conversation={conversation_id}", flush=True)

    pending_approvals: dict[str, asyncio.Future] = {}
    _daily_greeting_tasks: dict[str, asyncio.Task] = {}
    _title_tasks: dict[str, asyncio.Task] = {}
    last_known_character_id: str | None = None
    screen_fingerprints = ScreenFingerprintStore(maxlen=5)

    async def get_user_config() -> UserConfig | None:
        from database import async_session

        try:
            async with async_session() as session:
                result = await session.execute(select(UserConfig).where(UserConfig.id == 1))
                return result.scalar_one_or_none()
        except Exception:
            return None

    async def refuse_missing_conversation() -> None:
        """会话不存在：拒绝并关掉这条连接（调用方随即 return）。"""
        print(f"[WS] conversation {conversation_id} does not exist — refusing", flush=True)
        await websocket.send_json(CONVERSATION_NOT_FOUND_EVENT)
        await websocket.close(code=4404)

    conv = None
    lookup_failed = False
    try:
        from database import async_session
        from models.conversation import Conversation

        async with async_session() as session:
            conv = await session.get(Conversation, conversation_id)
    except Exception as e:
        # 查询本身出错（DB 抖动）时保持原有的宽容行为：只有"行确实不存在"
        # 才拒绝 —— 否则一次瞬时故障会把用户踢进新会话、平白丢掉上下文。
        lookup_failed = True
        print(f"[WS] conversation lookup failed: {e}", flush=True)

    if conv is None and not lookup_failed:
        await refuse_missing_conversation()
        return
    if conv is not None:
        last_known_character_id = conv.character_id
        print(
            f"[WS] resolved character_id={last_known_character_id} from conversation",
            flush=True,
        )

    def _char_id_getter() -> str | None:
        return last_known_character_id

    async def approval_callback(tool_name: str, arguments: dict) -> bool:
        request_id = f"approval-{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future[bool] = loop.create_future()
        pending_approvals[request_id] = future

        print(f"[APPROVAL] requesting tool={tool_name} id={request_id}", flush=True)
        await websocket.send_json(
            {
                "type": "approval_request",
                "request_id": request_id,
                "name": tool_name,
                "arguments": arguments,
            }
        )

        try:
            approved = await asyncio.wait_for(future, timeout=120.0)
            print(f"[APPROVAL] resolved id={request_id} approved={approved}", flush=True)
        except asyncio.TimeoutError:
            print(f"[APPROVAL] TIMEOUT id={request_id}", flush=True)
            approved = False
        finally:
            pending_approvals.pop(request_id, None)

        return approved

    from services.screen_capture_gate import ScreenCaptureGate

    screen_gate = ScreenCaptureGate(
        send_json=websocket.send_json,
        approval_callback=approval_callback,
        get_user_config=get_user_config,
        fingerprints=screen_fingerprints,
    )

    from services.proactive_session import ProactiveSession

    proactive_session = ProactiveSession(
        conversation_id=conversation_id,
        char_id_getter=_char_id_getter,
        agent=agent,
        send_json=websocket.send_json,
        approval_callback=approval_callback,
        get_user_config=get_user_config,
        screen_fingerprints=screen_fingerprints,
        screen_gate=screen_gate,
    )
    await proactive_session.start()

    async def handle_daily_greeting():
        """Gather context then delegate to GreetingOrchestrator.

        Memory extraction + compact run in a background asyncio task so the
        greeting is not blocked by slow LLM calls.  A *snapshot* timestamp
        taken before greeting generation is passed to the extractor as an
        upper bound (``before``), preventing the greeting message itself
        from being ingested as a memory.
        """
        print("[DAILY] handler invoked", flush=True)
        char_id = last_known_character_id
        if not char_id:
            print("[DAILY] no character_id known, skipping", flush=True)
            await websocket.send_json({"type": "daily_greeting_skip", "reason": "no_character"})
            return

        from datetime import datetime, timezone, date as _date
        from config import settings as app_settings
        from database import async_session
        from services.memory_service import memory_service
        from services.location_service import get_location
        from services.weather_service import get_weather
        from core.greeting_orchestrator import GreetingOrchestrator

        if not app_settings.daily_greeting_enabled:
            await websocket.send_json({"type": "daily_greeting_skip", "reason": "disabled"})
            return

        async with async_session() as session:
            # ── Gather location ────────────────────────────────────
            cfg = await session.get(UserConfig, 1)
            location_info = None
            if cfg and cfg.location_city:
                print(f"[DAILY] using cached location: {cfg.location_city}", flush=True)
                location_info = {"city": cfg.location_city, "country": cfg.location_country or "中国", "adcode": "", "province": ""}
            if not location_info:
                location_info = await get_location()
            if not location_info and app_settings.user_city:
                location_info = {"city": app_settings.user_city, "province": "", "country": "中国", "adcode": ""}

            # ── Gather weather ─────────────────────────────────────
            weather_info = None
            if location_info:
                weather_info = await get_weather(
                    adcode=location_info.get("adcode", ""),
                    city=location_info.get("city", ""),
                )

            # ── Persist location cache ─────────────────────────────
            if cfg is None:
                print("[DAILY] no UserConfig found, skipping", flush=True)
                await websocket.send_json({"type": "daily_greeting_skip", "reason": "no_config"})
                return
            if location_info:
                cfg.location_city = location_info.get("city", "")
                cfg.location_country = location_info.get("country", "")

            # ── Snapshot before greeting (anti-leak guard) ─────────
            snapshot = datetime.now(timezone.utc)

            # ── Background task: extract + compact ─────────────────
            from models.character import CharacterProfile

            today_str = _date.today().isoformat()
            char = await session.get(CharacterProfile, char_id)
            last_date = char.last_daily_greeting_date if char else ""

            print(
                f"[DAILY] last_date={last_date} today_str={today_str} "
                f"should_extract={last_date != today_str and bool(last_date)}",
                flush=True,
            )
            if last_date != today_str and last_date:
                async def _background_extract_and_compact():
                    """Extract memories + compact across ALL conversations for this character.
                    Runs concurrently with greeting generation; failures are
                    logged but never propagated to the user."""
                    print("[DAILY] background task STARTED", flush=True)
                    try:
                        async with async_session() as bg_session:
                            from sqlalchemy import select
                            from services.llm_service import LLMService
                            from models.conversation import Conversation

                            # ── Query all conversations for this character ──
                            result = await bg_session.execute(
                                select(Conversation)
                                .where(Conversation.character_id == char_id)
                                .order_by(Conversation.updated_at.desc())
                            )
                            all_convs = result.scalars().all()

                            if not all_convs:
                                print(f"[DAILY] no conversations for char {char_id}", flush=True)
                                return

                            extract_llm = LLMService()

                            for conv in all_convs:
                                cid = conv.id
                                print(f"[DAILY] processing conversation {cid}", flush=True)

                                # ── Extract memories ──
                                try:
                                    extracted = await memory_service.extract_and_store(
                                        cid,
                                        extract_llm,
                                        character_id=char_id,
                                        since_date=last_date,
                                        before=snapshot,
                                    )
                                    print(
                                        f"[DAILY] extracted {len(extracted)} memories from {cid} since {last_date}",
                                        flush=True,
                                    )
                                except Exception as e:
                                    print(f"[DAILY] extract failed for {cid}: {e}", flush=True)

                                # ── Compact ──
                                try:
                                    compact_result = await conv_manager.summarize_and_trim(
                                        bg_session, cid,
                                        keep_count=12,
                                        llm_service=extract_llm,
                                    )
                                    print(
                                        f"[DAILY] compact {cid}: deleted {compact_result['deleted']} messages, "
                                        f"summary_len={len(compact_result['summary'])}",
                                        flush=True,
                                    )
                                except Exception as e:
                                    print(f"[DAILY] compact failed for {cid}: {e}", flush=True)

                    except Exception as e:
                        print(f"[DAILY] background extraction failed: {e}", flush=True)

                asyncio.create_task(_background_extract_and_compact())

            # ── Gather memories (user_stated only) ─────────────────
            # ai_summarized memories are NOT used because extraction
            # runs concurrently and may not have finished yet.  Recent
            # messages provide conversational context instead.
            from sqlalchemy import select as _sel
            from models.memory import Memory, SOURCE_USER_STATED

            user_memories = await session.execute(
                _sel(Memory)
                .where(Memory.character_id == char_id, Memory.source == SOURCE_USER_STATED)
                .order_by(Memory.importance.desc(), Memory.last_accessed_at.desc())
                .limit(3)
            )
            memory_texts = [m.content for m in user_memories.scalars().all()]

            # ── Recent messages as conversational context ──────────
            from sqlalchemy import select, desc
            from models.message import Message

            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(desc(Message.created_at))
                .limit(5)
            )
            recent_msgs = list(result.scalars().all())
            for msg in reversed(recent_msgs):  # chronological order
                role_label = "用户" if msg.role == "user" else "角色"
                memory_texts.append(f"[{role_label}]: {msg.content or '(tool)'}")

            # ── Days since last message ────────────────────────────
            last_msg = recent_msgs[0] if recent_msgs else None
            days_since_last = 0
            if last_msg:
                delta = datetime.now(timezone.utc) - last_msg.created_at.replace(tzinfo=timezone.utc)
                days_since_last = delta.days
            print(f"[DAILY] days_since_last={days_since_last}", flush=True)

            # ── Delegate to orchestrator ───────────────────────────
            orch = GreetingOrchestrator()
            await orch.run(
                session=session,
                char_id=char_id,
                conv_id=conversation_id,
                agent=agent,
                send_json=websocket.send_json,
                location=location_info,
                weather=weather_info,
                days_since_last=days_since_last,
                memories=memory_texts,
            )

    async def handle_location_update(data: dict):
        """Receive browser geolocation, reverse-geocode via Amap, cache results."""
        lat = data.get("lat")
        lng = data.get("lng")
        if lat is None or lng is None:
            return

        from database import async_session
        from services.location_service import geocode_reverse
        from services.weather_service import get_weather

        location_info = await geocode_reverse(float(lat), float(lng))
        if not location_info:
            print("[LOC] geocode_reverse returned nothing", flush=True)
            return

        weather_info = await get_weather(
            adcode=location_info.get("adcode", ""),
            city=location_info.get("city", ""),
        )

        async with async_session() as session:
            cfg = await session.get(UserConfig, 1)
            if cfg is None:
                cfg = UserConfig(id=1)
                session.add(cfg)
            cfg.location_city = location_info.get("city", "")
            cfg.location_country = location_info.get("country", "")
            cfg.location_lat = float(lat)
            cfg.location_lng = float(lng)
            if weather_info:
                cfg.location_weather = (
                    f"{weather_info['condition']} {weather_info['temp']}°C "
                    f"湿度{weather_info['humidity']}%"
                )
            await session.commit()

        print(
            f"[LOC] updated: {location_info['city']} "
            f"{location_info.get('district', '')} "
            f"weather={'ok' if weather_info else 'none'}",
            flush=True,
        )

    async def _ensure_title_bg(conv_id: str):
        try:
            from database import async_session
            from services.llm_service import LLMService

            async with async_session() as bg_session:
                llm = LLMService()
                await conv_manager.ensure_title(bg_session, conv_id, llm)
        except Exception as e:
            print(f"[WS] background title generation failed: {e}", flush=True)

    async def handle_message(data: dict):
        msg_type = data.get("type", "chat")

        if msg_type == "approval_response":
            request_id = data.get("request_id", "")
            approved = data.get("approved", False)
            print(f"[APPROVAL] received response id={request_id} approved={approved}", flush=True)
            future = pending_approvals.get(request_id)
            if future and not future.done():
                future.set_result(approved)
            else:
                print(f"[APPROVAL] WARNING future not found id={request_id}", flush=True)
            return

        if msg_type == "daily_greeting":
            existing = _daily_greeting_tasks.get(conversation_id)
            if existing and not existing.done():
                print("[WS] daily_greeting already in flight, skipping", flush=True)
                await websocket.send_json({"type": "daily_greeting_skip", "reason": "in_flight"})
                return
            print("[WS] dispatching to handle_daily_greeting", flush=True)
            task = asyncio.create_task(handle_daily_greeting())
            _daily_greeting_tasks[conversation_id] = task
            task.add_done_callback(lambda _t: _daily_greeting_tasks.pop(conversation_id, None))
            return

        if msg_type == "update_location":
            asyncio.create_task(handle_location_update(data))
            return

        if msg_type != "chat":
            return

        content = data.get("content", "")
        character_id = data.get("character_id", "")
        force_vision = bool(data.get("force_vision"))
        client_message_id = data.get("client_message_id")

        if not content or not character_id:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "content and character_id are required",
                }
            )
            return

        # 会话可能在连接期间被删掉（多窗口挂着一个已删除的会话 id 继续聊，
        # 或后端换了数据目录）：每一轮再确认一次，绝不往不存在/已删除的会话落库。
        from database import async_session
        from models.conversation import Conversation

        async with async_session() as session:
            if await session.get(Conversation, conversation_id) is None:
                await refuse_missing_conversation()
                return

        nonlocal last_known_character_id
        last_known_character_id = character_id
        proactive_session.reset_idle()

        # ── Message augmentation: search hint + POI injection ─────
        from services.message_augmenter import augment as augment_message

        augmented_message = await augment_message(content)

        # ── Screen capture on intent ──────────────────────────────
        should_capture = force_vision or detects_screen_intent(content)
        if should_capture:
            print("[CHAT] screen intent detected, forcing capture", flush=True)
            description = await screen_gate.try_capture()
            if description:
                augmented_message = (
                    f"{augmented_message}\n\n"
                    f"[System captured fresh screen context: {description}]\n"
                    f"Reply naturally based on this description and do not call see_screen again."
                )
            else:
                augmented_message = (
                    f"{augmented_message}\n\n"
                    f"[Screen capture request did not succeed, likely denied or failed.]"
                )

        # LLM retry progress → transient WS event.  The callback fires inside
        # the retry loop's await chain, so it dispatches via create_task rather
        # than the agent event stream (pure UI transient; CONTEXT.md §5.1).
        # Only the chat path wires this — proactive/greeting stay silent.
        _retry_send_tasks: set[asyncio.Task] = set()

        async def _send_llm_retry(attempt: int, max_retries: int) -> None:
            try:
                await websocket.send_json(
                    {
                        "type": "llm_retry",
                        "attempt": attempt,
                        "max_retries": max_retries,
                    }
                )
            except Exception as e:
                # WS closed / client gone mid-retry — progress is moot; swallow
                # so the fire-and-forget task never raises "exception was
                # never retrieved" noise.
                print(f"[WS] llm_retry send skipped: {e}", flush=True)

        def _on_llm_retry(attempt: int, max_retries: int, exc: Exception) -> None:
            # Hold a strong reference until the task finishes so it cannot be
            # garbage-collected mid-flight (asyncio fire-and-forget pitfall).
            task = asyncio.create_task(_send_llm_retry(attempt, max_retries))
            _retry_send_tasks.add(task)
            task.add_done_callback(_retry_send_tasks.discard)

        try:
            async with async_session() as session:
                done_event = None
                async for event in agent.run(
                    session=session,
                    user_message=augmented_message,
                    conversation_id=conversation_id,
                    character_id=character_id,
                    approval_callback=approval_callback,
                    client_message_id=client_message_id,
                    on_llm_retry=_on_llm_retry,
                ):
                    await websocket.send_json(event)
                    if event.get("type") == "done":
                        done_event = event
            proactive_session.reset_idle()

            if done_event and not done_event.get("daily_greeting") and not done_event.get("proactive"):
                existing = _title_tasks.get(conversation_id)
                if not existing or existing.done():
                    task = asyncio.create_task(_ensure_title_bg(conversation_id))
                    _title_tasks[conversation_id] = task
                    task.add_done_callback(lambda _t: _title_tasks.pop(conversation_id, None))
        except Exception as e:
            import traceback
            print(f"[WS] handle_message error: {e}\n{traceback.format_exc()}", flush=True)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            print(f"[WS] received type={data.get('type')}", flush=True)
            asyncio.create_task(handle_message(data))

    except WebSocketDisconnect:
        print(f"[WS] disconnected conversation={conversation_id}", flush=True)
    except Exception as e:
        print(f"[WS] error: {e}", flush=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await proactive_session.stop()
