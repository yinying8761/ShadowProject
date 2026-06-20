from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models.user_config import UserConfig
from core.tool_registry import tool_registry
from config import settings, PROVIDER_PRESETS

router = APIRouter(prefix="/api", tags=["config"])


class ConfigUpdate(BaseModel):
    theme: str | None = None
    always_on_top: bool | None = None
    font_size: int | None = None
    show_floating_icon: bool | None = None
    floating_icon_x: int | None = None
    floating_icon_y: int | None = None
    proactive_chat_level: str | None = None
    proactive_silent_tool_approval: bool | None = None
    proactive_auto_see_screen: bool | None = None
    proactive_daily_limit: int | None = None
    proactive_fixed_schedule_enabled: bool | None = None
    proactive_system_notification: bool | None = None
    language: str | None = None


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.get("/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(UserConfig).where(UserConfig.id == 1))
    config = result.scalar_one_or_none()

    if config is None:
        config = UserConfig(id=1)
        session.add(config)
        await session.commit()

    return {
        "theme": config.theme,
        "always_on_top": config.always_on_top,
        "font_size": config.font_size,
        "show_floating_icon": config.show_floating_icon,
        "floating_icon_x": config.floating_icon_x,
        "floating_icon_y": config.floating_icon_y,
        "proactive_chat_level": config.proactive_chat_level,
        "proactive_silent_tool_approval": config.proactive_silent_tool_approval,
        "proactive_auto_see_screen": config.proactive_auto_see_screen,
        "proactive_daily_limit": config.proactive_daily_limit,
        "proactive_fixed_schedule_enabled": config.proactive_fixed_schedule_enabled,
        "proactive_system_notification": config.proactive_system_notification,
        "language": config.language,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.get_model(),
        "has_api_key": bool(settings.llm_api_key),
    }


@router.put("/config")
async def update_config(
    data: ConfigUpdate, session: AsyncSession = Depends(get_session)
):
    result = await session.execute(select(UserConfig).where(UserConfig.id == 1))
    config = result.scalar_one_or_none()

    if config is None:
        config = UserConfig(id=1)
        session.add(config)

    if data.theme is not None:
        config.theme = data.theme
    if data.always_on_top is not None:
        config.always_on_top = data.always_on_top
    if data.font_size is not None:
        config.font_size = data.font_size
    if data.show_floating_icon is not None:
        config.show_floating_icon = data.show_floating_icon
    if data.floating_icon_x is not None:
        config.floating_icon_x = data.floating_icon_x
    if data.floating_icon_y is not None:
        config.floating_icon_y = data.floating_icon_y
    if data.proactive_chat_level is not None:
        config.proactive_chat_level = data.proactive_chat_level
    if data.proactive_silent_tool_approval is not None:
        config.proactive_silent_tool_approval = data.proactive_silent_tool_approval
    if data.proactive_auto_see_screen is not None:
        config.proactive_auto_see_screen = data.proactive_auto_see_screen
    if data.proactive_daily_limit is not None:
        config.proactive_daily_limit = max(0, data.proactive_daily_limit)
    if data.proactive_fixed_schedule_enabled is not None:
        config.proactive_fixed_schedule_enabled = data.proactive_fixed_schedule_enabled
    if data.proactive_system_notification is not None:
        config.proactive_system_notification = data.proactive_system_notification
    if data.language is not None:
        config.language = data.language

    await session.commit()
    return {"status": "updated"}


@router.get("/providers")
async def list_providers():
    """Return available provider presets for the frontend dropdown."""
    return {
        "providers": [
            {"id": k, "description": v["description"], "default_model": v["default_model"]}
            for k, v in PROVIDER_PRESETS.items()
        ],
        "current": settings.llm_provider,
        "current_model": settings.get_model(),
    }


@router.get("/tools")
async def list_tools():
    return {"tools": tool_registry.get_tool_definitions()}


@router.get("/memories")
async def list_memories(session: AsyncSession = Depends(get_session)):
    """Return all memories as JSON for the memory viewer."""
    from models.memory import Memory
    from sqlalchemy import select as _select

    result = await session.execute(
        _select(Memory).order_by(Memory.created_at.desc())
    )
    memories = result.scalars().all()
    return {
        "memories": [
            {
                "id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "importance": m.importance,
                "access_count": m.access_count,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                "last_accessed_at": m.last_accessed_at.isoformat() if m.last_accessed_at else None,
                "source_conversation_id": m.source_conversation_id,
            }
            for m in memories
        ],
        "total": len(memories),
    }
