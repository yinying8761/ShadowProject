from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models.character import CharacterProfile

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
    existing = await session.execute(
        select(CharacterProfile).where(CharacterProfile.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Character '{data.name}' already exists")

    char = CharacterProfile(
        name=data.name,
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

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(char, field, value)

    await session.commit()
    return {"id": char.id, "name": char.name, "status": "updated"}


@router.delete("/{character_id}")
async def delete_character(
    character_id: str, session: AsyncSession = Depends(get_session)
):
    char = await session.get(CharacterProfile, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
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
