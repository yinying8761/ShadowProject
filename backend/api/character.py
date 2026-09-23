from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update

from database import get_session
from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import GroupMember
from models.memory import Memory
from models.message import Message
from models.user_profile import UserProfile

router = APIRouter(prefix="/api/characters", tags=["characters"])


class CharacterCreate(BaseModel):
    name: str
    gender: str | None = None
    personality: str = ""
    role: str = "companion"
    archetype: str = "friend"
    voice_style: str | None = None
    system_prompt_template: str | None = None


class CharacterUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    personality: str | None = None
    role: str | None = None
    archetype: str | None = None
    voice_style: str | None = None
    system_prompt_template: str | None = None


def _clean_character_name(name: str) -> str:
    """角色名必填：空白名字会渲染成 `[ ]` / `[AI]`（审查 §B1.1）。

    `name: str` 只保证"字段在"，`""` 与 `"   "` 都能建出角色 —— 而说话人
    取自 `CharacterProfile.name`，于是它的每句话都被渲染成无主语的一行。
    """
    stripped = (name or "").strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Character name cannot be empty")
    return stripped


@router.get("")
async def list_characters(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(CharacterProfile).order_by(CharacterProfile.created_at)
    )
    characters = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "gender": c.gender,
            "personality": c.personality,
            "role": c.role,
            "archetype": c.archetype,
            "voice_style": c.voice_style,
            "avatar_path": c.avatar_path,
            "tts_ref_audio": c.tts_ref_audio,
            "tts_prompt_text": c.tts_prompt_text,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in characters
    ]


@router.post("")
async def create_character(
    data: CharacterCreate, session: AsyncSession = Depends(get_session)
):
    name = _clean_character_name(data.name)
    existing = await session.execute(
        select(CharacterProfile).where(CharacterProfile.name == name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Character '{name}' already exists")

    char = CharacterProfile(
        name=name,
        gender=data.gender,
        personality=data.personality,
        role=data.role,
        archetype=data.archetype,
        voice_style=data.voice_style,
        system_prompt_template=data.system_prompt_template or "",
    )
    session.add(char)
    await session.commit()
    await session.refresh(char)
    return {"id": char.id, "name": char.name}


@router.get("/{character_id}")
async def get_character(
    character_id: str, session: AsyncSession = Depends(get_session)
):
    char = await session.get(CharacterProfile, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    return {
        "id": char.id,
        "name": char.name,
        "gender": char.gender,
        "personality": char.personality,
        "role": char.role,
        "archetype": char.archetype,
        "voice_style": char.voice_style,
        "system_prompt_template": char.system_prompt_template,
        "avatar_path": char.avatar_path,
        "created_at": char.created_at.isoformat() if char.created_at else None,
        "updated_at": char.updated_at.isoformat() if char.updated_at else None,
    }


@router.put("/{character_id}")
async def update_character(
    character_id: str,
    data: CharacterUpdate,
    session: AsyncSession = Depends(get_session),
):
    char = await session.get(CharacterProfile, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    fields = data.model_dump(exclude_none=True)
    if "name" in fields:
        fields["name"] = _clean_character_name(fields["name"])
    for field, value in fields.items():
        setattr(char, field, value)

    await session.commit()
    return {"id": char.id, "name": char.name, "status": "updated"}


async def _delete_character_dependents(session: AsyncSession, character_id: str) -> None:
    """显式清理所有指向该角色的行 —— 服务层复刻模型曾声明过的级联语义。

    SQLite 上 `PRAGMA foreign_keys` 从不开启，模型里的 `ondelete=` 只是装饰
    （ADR-0005），所以"父行没了、子行还在"必须在这里兜住 —— 否则就是孤儿
    会话/消息与悬空 memories（审查 §B2.4 实测复现的正是这条路径）。

    群成员资格随之消失，因此变成 0 成员的群**保留**："编辑群"可以再把人加
    回来，而删群会连带用户的群对话历史，代价更大。
    """
    conv_ids = (await session.execute(
        select(Conversation.id).where(Conversation.character_id == character_id)
    )).scalars().all()

    if conv_ids:
        await session.execute(
            delete(Message).where(Message.conversation_id.in_(conv_ids))
        )
        # 记忆按角色保留，但不再指向已删除的会话（声明语义是 SET NULL）。
        await session.execute(
            update(Memory)
            .where(Memory.source_conversation_id.in_(conv_ids))
            .values(source_conversation_id=None)
        )
        await session.execute(delete(Conversation).where(Conversation.id.in_(conv_ids)))

    # 该角色的记忆：角色没了就永远检索不到（存量清理时实测 10 条悬空即此），整体删除。
    await session.execute(delete(Memory).where(Memory.character_id == character_id))
    # 别处群消息里指向它的 speaker_id → NULL（声明语义是 SET NULL）。
    await session.execute(
        update(Message).where(Message.speaker_id == character_id).values(speaker_id=None)
    )
    await session.execute(
        delete(GroupMember).where(GroupMember.character_id == character_id)
    )
    # 该角色的专属用户画像（character_id IS NULL 的全局回退画像不受影响）。
    await session.execute(
        delete(UserProfile).where(UserProfile.character_id == character_id)
    )


@router.delete("/{character_id}")
async def delete_character(
    character_id: str, session: AsyncSession = Depends(get_session)
):
    """删除角色，并清理它名下的一切（见 `_delete_character_dependents`）。"""
    char = await session.get(CharacterProfile, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    await _delete_character_dependents(session, character_id)
    await session.delete(char)
    await session.commit()
    return {"status": "deleted"}


@router.post("/{character_id}/avatar")
async def upload_avatar(
    character_id: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
):
    char = await session.get(CharacterProfile, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    from pathlib import Path
    from config import settings

    data_dir = settings.resolve_data_dir()
    avatars_dir = data_dir / "characters"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".png"
    filename = f"{character_id}{ext}"
    filepath = avatars_dir / filename

    content = await file.read()
    filepath.write_bytes(content)

    char.avatar_path = f"characters/{filename}"
    await session.commit()

    return {"avatar_path": char.avatar_path}
