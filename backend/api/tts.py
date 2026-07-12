"""TTS API — synthesize speech from text using character's voice clone."""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models.character import CharacterProfile
from services.tts_service import synthesize
from config import settings

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    character_id: str
    text: str
    speed: float = 1.0


def _resolve_ref_path(rel_path: str) -> str:
    """Resolve character's tts_ref_audio to absolute path.
    If it starts with references/, prefix with GPT-SoVITS base dir.
    Otherwise treat as absolute or relative to data dir.
    """
    if not rel_path:
        return ""
    p = Path(rel_path)
    if p.is_absolute():
        return str(p)
    if rel_path.replace("\\", "/").startswith("references/"):
        return f"{settings.tts_ref_base}/{rel_path}"
    return str(settings.resolve_data_dir() / rel_path)


@router.post("/speak")
async def speak_text(data: TTSRequest, session: AsyncSession = Depends(get_session)):
    """Generate speech audio for the given text using the character's voice."""
    char = await session.get(CharacterProfile, data.character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    if not char.tts_ref_audio or not char.tts_prompt_text:
        raise HTTPException(status_code=400, detail="Character has no voice configured")

    full_ref_path = _resolve_ref_path(char.tts_ref_audio)
    audio_bytes = await synthesize(
        text=data.text,
        ref_audio_path=full_ref_path,
        prompt_text=char.tts_prompt_text,
        speed=data.speed,
    )

    if audio_bytes is None:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")

    return Response(content=audio_bytes, media_type="audio/wav")


@router.put("/{character_id}/voice-config")
async def set_voice_config(
    character_id: str,
    ref_audio: str = "",
    prompt_text: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Set reference audio path and prompt text for a character."""
    char = await session.get(CharacterProfile, character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    char.tts_ref_audio = ref_audio
    char.tts_prompt_text = prompt_text
    await session.commit()

    return {"status": "ok", "tts_ref_audio": ref_audio, "tts_prompt_text": prompt_text}
