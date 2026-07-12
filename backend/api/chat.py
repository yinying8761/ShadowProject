import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent import Agent
from core.conversation_manager import ConversationManager
from database import get_session
from models.user_config import UserConfig
from services.proactive_watcher import ProactiveWatcher
from tools.screen_tools import (
    ScreenFingerprintStore,
    _capture_dhash_sync,
    see_screen,
)
from tools.time_tools import get_current_time

router = APIRouter()
agent = Agent()
conv_manager = ConversationManager()


SCREEN_KEYWORDS = [
    "看看屏幕",
    "看下屏幕",
    "看一下屏幕",
    "看屏幕",
    "看看我屏幕",
    "看看我的屏幕",
    "看下我的屏幕",
    "看一下我的屏幕",
    "看我在干",
    "看我在玩",
    "看我现在",
    "你看",
    "瞄一眼",
    "扫一眼",
]


def detects_screen_intent(text: str) -> bool:
    stripped = text.strip()
    return any(kw in stripped for kw in SCREEN_KEYWORDS)


def build_tool_context_message(
    tool_name: str,
    content: str,
    tool_call_id: str,
    arguments: dict | None = None,
) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments or {}, ensure_ascii=False),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        },
    ]


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
    _last_vision_time: float = 0.0
    _MIN_VISION_INTERVAL: float = 300.0  # 5 min between vision calls
    cached_config = {
        "level": "medium",
        "daily_limit": 10,
        "schedule_enabled": True,
    }

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

    async def refresh_cached_config() -> None:
        cfg = await get_user_config()
        if not cfg:
            return
        cached_config["level"] = cfg.proactive_chat_level
        cached_config["daily_limit"] = cfg.proactive_daily_limit
        cached_config["schedule_enabled"] = cfg.proactive_fixed_schedule_enabled

    def level_getter() -> str:
        return str(cached_config["level"])

    def daily_limit_getter() -> int:
        return int(cached_config["daily_limit"])

    def schedule_enabled_getter() -> bool:
        return bool(cached_config["schedule_enabled"])

    async def load_watcher_state() -> tuple[str, int, set[str]]:
        cfg = await get_user_config()
        if not cfg:
            return "", 0, set()
        slots = {s for s in (cfg.proactive_scheduled_slots or "").split(",") if s}
        return cfg.proactive_state_date or "", cfg.proactive_daily_count, slots

    async def save_watcher_state(state_date: str, daily_count: int, scheduled_slots: set[str]) -> None:
        from database import async_session

        async with async_session() as session:
            config = await session.get(UserConfig, 1)
            if config is None:
                config = UserConfig(id=1)
                session.add(config)
            config.proactive_state_date = state_date
            config.proactive_daily_count = daily_count
            config.proactive_scheduled_slots = ",".join(sorted(scheduled_slots))
            await session.commit()

    async def refresh_level_loop():
        while True:
            try:
                await refresh_cached_config()
            except Exception:
                pass
            await asyncio.sleep(20)

    try:
        await refresh_cached_config()
        print(f"[WS] initial proactive_level={cached_config['level']}", flush=True)
    except Exception:
        pass

    level_task = asyncio.create_task(refresh_level_loop())

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

    async def emit_tool_result(name: str, result: str, is_error: bool, denied: bool = False):
        await websocket.send_json(
            {
                "type": "tool_result",
                "name": name,
                "result": result,
                "is_error": is_error,
                "denied": denied,
            }
        )

    async def run_proactive_tool(
        tool_name: str,
        arguments: dict,
        runner,
        silent_approval: bool,
    ) -> str | None:
        needs_approval = tool_name == "see_screen"
        if needs_approval and not silent_approval:
            approved = await approval_callback(tool_name, arguments)
            if not approved:
                await emit_tool_result(tool_name, "User denied this proactive tool call.", False, True)
                return None

        await websocket.send_json(
            {
                "type": "tool_use",
                "name": tool_name,
                "arguments": arguments,
            }
        )

        try:
            result = await runner()
        except Exception as e:
            error_result = f"Error: {e}"
            await emit_tool_result(tool_name, error_result, True)
            return None

        await emit_tool_result(tool_name, result, False)
        return result

    async def build_proactive_context() -> list[dict]:
        nonlocal _last_vision_time
        cfg = await get_user_config()
        silent_approval = cfg.proactive_silent_tool_approval if cfg else False
        auto_see_screen = cfg.proactive_auto_see_screen if cfg else True

        context: list[dict] = []

        time_result = await run_proactive_tool(
            "get_current_time",
            {},
            get_current_time,
            silent_approval=True,
        )
        if time_result:
            context.extend(
                build_tool_context_message(
                    "get_current_time",
                    time_result,
                    f"proactive-time-{uuid.uuid4().hex[:8]}",
                )
            )

        if auto_see_screen:
            now = time.monotonic()
            elapsed = now - _last_vision_time

            if elapsed < _MIN_VISION_INTERVAL:
                print(
                    f"[PROACTIVE] vision throttle: {elapsed:.0f}s < {_MIN_VISION_INTERVAL:.0f}s, "
                    f"skipping screen capture",
                    flush=True,
                )
            else:
                # Capture and compute perceptual hash (cheap, no vision API)
                screen_hash = await asyncio.to_thread(_capture_dhash_sync)
                is_new_scene = screen_fingerprints.check_and_add(screen_hash)

                if not is_new_scene:
                    print(
                        f"[PROACTIVE] screen fingerprint known (queue={len(screen_fingerprints)}), "
                        f"skipping vision call",
                        flush=True,
                    )
                else:
                    _last_vision_time = now
                    print(
                        f"[PROACTIVE] new screen scene detected (queue={len(screen_fingerprints)}), "
                        f"calling vision model",
                        flush=True,
                    )
                    focus = "Describe what the user is currently doing and any notable on-screen context."
                    screen_result = await run_proactive_tool(
                        "see_screen",
                        {"focus": focus},
                        lambda: see_screen(focus=focus),
                        silent_approval=silent_approval,
                    )
                    if screen_result:
                        context.extend(
                            build_tool_context_message(
                                "see_screen",
                                screen_result,
                                f"proactive-screen-{uuid.uuid4().hex[:8]}",
                                {"focus": focus},
                            )
                        )

        return context

    async def proactive_trigger(trigger_type: str):
        char_id = last_known_character_id
        if not char_id:
            print("[PROACTIVE] no character_id known, skipping", flush=True)
            return

        print(
            f"[PROACTIVE] triggering type={trigger_type} char={char_id} conv={conversation_id}",
            flush=True,
        )

        from database import async_session

        try:
            async with async_session() as session:
                sent_anything = False
                proactive_context = await build_proactive_context()
                if trigger_type == "scheduled":
                    proactive_hint = (
                        "到了平常会来找你聊天的时间点。像平时一样自然地出现，打个招呼、"
                        "关心一下用户现在在做什么，不用提时间安排、不用提工具。"
                    )
                else:
                    proactive_hint = (
                        "有一阵子没说话了。看看现在几点了，如果有屏幕画面也看看用户在干嘛——"
                        "然后随性地发起一个话题。可以分享心情、问问近况、吐槽点什么，"
                        "或者看到用户在做什么就顺着聊下去。自然就好，不用提工具。"
                    )

                async for event in agent.run(
                    session=session,
                    user_message=None,
                    conversation_id=conversation_id,
                    character_id=char_id,
                    approval_callback=approval_callback,
                    proactive_hint=proactive_hint,
                    force_tool_context=proactive_context,
                    suppress_tool_calls=True,
                ):
                    if event.get("type") == "proactive_skip":
                        print("[PROACTIVE] model returned __SKIP__, not sending", flush=True)
                        return
                    tagged = {**event, "proactive": True}
                    await websocket.send_json(tagged)
                    sent_anything = True

                print(f"[PROACTIVE] complete (sent={sent_anything})", flush=True)
                if sent_anything:
                    watcher.reset_idle()
        except Exception as e:
            import traceback

            print(f"[PROACTIVE] EXCEPTION: {e}\n{traceback.format_exc()}", flush=True)

    watcher = ProactiveWatcher(
        get_level=level_getter,
        get_daily_limit=daily_limit_getter,
        get_schedule_enabled=schedule_enabled_getter,
        load_state=load_watcher_state,
        save_state=save_watcher_state,
        on_trigger=proactive_trigger,
        name=f"watcher[{conversation_id[:8]}]",
    )
    watcher.start()

    async def force_screen_capture(focus: str | None = None) -> str | None:
        cfg = await get_user_config()
        silent_approval = cfg.proactive_silent_tool_approval if cfg else False
        if not silent_approval:
            approved = await approval_callback("see_screen", {"focus": focus or ""})
            if not approved:
                await emit_tool_result("see_screen", "User denied this operation.", False, True)
                return None

        await websocket.send_json(
            {
                "type": "tool_use",
                "name": "see_screen",
                "arguments": {"focus": focus or ""},
            }
        )

        try:
            result_json = await see_screen(focus=focus or None)
        except Exception as e:
            await emit_tool_result("see_screen", f"Screen capture failed: {e}", True)
            return None

        # Record fingerprint so proactive watcher doesn't re-describe this scene
        try:
            screen_hash = await asyncio.to_thread(_capture_dhash_sync)
            screen_fingerprints.add(screen_hash)
        except Exception:
            pass

        try:
            parsed = json.loads(result_json)
        except json.JSONDecodeError:
            parsed = {"description": result_json}

        description = parsed.get("description") or parsed.get("error") or ""
        is_error = "error" in parsed and "description" not in parsed
        await emit_tool_result("see_screen", result_json, is_error)
        return description if not is_error else None

    async def handle_daily_greeting():
        """Trigger once-per-day greeting with location/weather/memory context."""
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

        if not app_settings.daily_greeting_enabled:
            await websocket.send_json({"type": "daily_greeting_skip", "reason": "disabled"})
            return

        today = datetime.now().strftime("%Y-%m-%d")

        # Check if already greeted today (persisted in DB)
        async with async_session() as session:
            cfg = await session.get(UserConfig, 1)
            if cfg and cfg.last_daily_greeting_date == today:
                print("[DAILY] already greeted today, skip", flush=True)
                await websocket.send_json({"type": "daily_greeting_skip", "reason": "already_greeted"})
                return

            # Location: try IP/manual config first, fall back to cached browser geolocation
            location_info = await get_location()
            if not location_info and cfg and cfg.location_city:
                print(f"[DAILY] using cached location: {cfg.location_city}", flush=True)
                location_info = {"city": cfg.location_city, "country": cfg.location_country or "中国", "adcode": "", "province": ""}

            weather_info = None
            if location_info:
                weather_info = await get_weather(
                    adcode=location_info.get("adcode", ""),
                    city=location_info.get("city", ""),
                )

            # Search relevant memories (only for this character)
            memories = await memory_service.search(session, query="", top_k=3, character_id=char_id)
            memory_texts = [m.content for m in memories]

            # Calculate days since last message
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

            # Persist location cache
            if cfg is None:
                print("[DAILY] no UserConfig found, skipping", flush=True)
                await websocket.send_json({"type": "daily_greeting_skip", "reason": "no_config"})
                return
            if location_info:
                cfg.location_city = location_info.get("city", "")
                cfg.location_country = location_info.get("country", "")
            cfg.last_daily_greeting_date = today
            cfg.last_daily_greeting_at = datetime.now().isoformat()
            await session.commit()

            # Run greeting
            async for event in agent.run_daily_greeting(
                session=session,
                conversation_id=conversation_id,
                character_id=char_id,
                location=location_info,
                weather=weather_info,
                days_since_last=days_since_last,
                memories=memory_texts,
            ):
                await websocket.send_json(event)

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
        watcher.reset_idle()

        augmented_message = content
        should_capture = force_vision or detects_screen_intent(content)
        if should_capture:
            print("[CHAT] screen intent detected, forcing capture", flush=True)
            description = await force_screen_capture()
            if description:
                augmented_message = (
                    f"{content}\n\n"
                    f"[System captured fresh screen context: {description}]\n"
                    f"Reply naturally based on this description and do not call see_screen again."
                )
            else:
                augmented_message = (
                    f"{content}\n\n"
                    f"[Screen capture request did not succeed, likely denied or failed.]"
                )

        # Detect food/restaurant intent → inject nearby POI results
        FOOD_KEYWORDS = [
            "吃什么", "推荐", "好吃的", "美食", "附近", "餐厅", "饭店",
            "外卖", "外卖点", "点外卖", "饿了", "吃饭", "想吃", "请客",
            "特色", "小吃", "夜宵", "早餐", "午餐", "晚餐", "火锅",
            "烧烤", "面馆", "奶茶", "咖啡", "甜品",
        ]
        if any(kw in content for kw in FOOD_KEYWORDS):
            from database import async_session as _db_async
            from services.location_service import search_nearby_places, nearby_to_context
            from models.user_config import UserConfig as _UC

            async with _db_async() as _s:
                _cfg = await _s.get(_UC, 1)
                if _cfg and _cfg.location_lat and _cfg.location_lng:
                    _places = await search_nearby_places(
                        _cfg.location_lat, _cfg.location_lng,
                        keywords="餐饮|美食|小吃|特色",
                    )
                    if _places:
                        _ctx = nearby_to_context(_places)
                        augmented_message = (
                            f"{augmented_message}\n\n"
                            f"[帮助AI回答的本地参考信息，自然地融入回复，不要照念：\n{_ctx}]"
                        )
                        print(f"[POI] injected {len(_places)} nearby places", flush=True)

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
            watcher.reset_idle()
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
        watcher.stop()
        level_task.cancel()
