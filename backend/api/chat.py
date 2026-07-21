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


from services.screen_capture_gate import detects_screen_intent


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    character_id: str
    model: str | None = None


@router.post("/api/chat/send")
async def send_chat(req: ChatRequest, session: AsyncSession = Depends(get_session)):
    conv_id = req.conversation_id
    if not conv_id:
        conv = await conv_manager.get_or_create_conversation(session, req.character_id)
        conv_id = conv.id

    full_response = ""
    try:
        async for event in agent.run(
            session=session,
            user_message=req.message,
            conversation_id=conv_id,
            character_id=req.character_id,
        ):
            if event["type"] == "token":
                full_response += event["content"]
            elif event["type"] == "done":
                return {
                    "conversation_id": conv_id,
                    "message_id": event["message_id"],
                    "content": full_response,
                }
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

    try:
        from database import async_session
        from models.conversation import Conversation

        async with async_session() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv:
                last_known_character_id = conv.character_id
                print(
                    f"[WS] resolved character_id={last_known_character_id} from conversation",
                    flush=True,
                )
    except Exception as e:
        print(f"[WS] character_id lookup failed: {e}", flush=True)

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
        """Gather context then delegate to GreetingOrchestrator."""
        print("[DAILY] handler invoked", flush=True)
        char_id = last_known_character_id
        if not char_id:
            print("[DAILY] no character_id known, skipping", flush=True)
            await websocket.send_json({"type": "daily_greeting_skip", "reason": "no_character"})
            return

        from datetime import datetime, timezone
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

            # ── Gather memories ────────────────────────────────────
            memories = await memory_service.search(session, query="", top_k=3, character_id=char_id)
            memory_texts = [m.content for m in memories]

            # ── Days since last message ────────────────────────────
            from sqlalchemy import select, desc
            from models.message import Message

            result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(desc(Message.created_at))
                .limit(1)
            )
            last_msg = result.scalar_one_or_none()
            days_since_last = 0
            if last_msg:
                delta = datetime.now(timezone.utc) - last_msg.created_at.replace(tzinfo=timezone.utc)
                days_since_last = delta.days
            print(f"[DAILY] days_since_last={days_since_last}", flush=True)

            # ── Persist location cache ─────────────────────────────
            if cfg is None:
                print("[DAILY] no UserConfig found, skipping", flush=True)
                await websocket.send_json({"type": "daily_greeting_skip", "reason": "no_config"})
                return
            if location_info:
                cfg.location_city = location_info.get("city", "")
                cfg.location_country = location_info.get("country", "")

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
            print("[WS] dispatching to handle_daily_greeting", flush=True)
            asyncio.create_task(handle_daily_greeting())
            return

        if msg_type == "update_location":
            asyncio.create_task(handle_location_update(data))
            return

        if msg_type != "chat":
            return

        content = data.get("content", "")
        character_id = data.get("character_id", "")
        force_vision = bool(data.get("force_vision"))

        if not content or not character_id:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "content and character_id are required",
                }
            )
            return

        nonlocal last_known_character_id
        last_known_character_id = character_id
        proactive_session.reset_idle()

        # ── Message augmentation: search routing + POI injection ──
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

        from database import async_session

        try:
            async with async_session() as session:
                async for event in agent.run(
                    session=session,
                    user_message=augmented_message,
                    conversation_id=conversation_id,
                    character_id=character_id,
                    approval_callback=approval_callback,
                ):
                    await websocket.send_json(event)
            proactive_session.reset_idle()
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
