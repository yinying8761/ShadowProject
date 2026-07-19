"""TTS API — synthesize speech from text using character's voice clone."""

import os
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models.character import CharacterProfile
from services.tts_service import synthesize, clean_text, start_api, stop_api, is_api_running
from config import settings

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    character_id: str
    text: str
    speed: float = 1.0


class ApplyVoiceRequest(BaseModel):
    character_id: str
    ref_audio: str
    prompt_text: str


def _resolve_ref_path(rel_path: str) -> str:
    if not rel_path:
        return ""
    p = Path(rel_path)
    if p.is_absolute():
        return str(p)
    if rel_path.replace("\\", "/").startswith("references/"):
        return f"{settings.tts_ref_base}/{rel_path}"
    return str(settings.resolve_data_dir() / rel_path)


def _refs_dir() -> Path:
    """Get the GPT-SoVITS references directory."""
    return Path(settings.tts_ref_base) / "references"


@router.get("/status")
async def tts_status():
    """Check if GPT-SoVITS API is reachable."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("http://127.0.0.1:9880/")
            return {
                "connected": r.status_code in (200, 404, 405),
                "running": is_api_running(),
                "tts_ref_base": settings.tts_ref_base,
            }
    except Exception:
        return {"connected": False, "running": is_api_running(), "tts_ref_base": settings.tts_ref_base}


@router.post("/control/path")
async def tts_set_path(data: dict):
    """Override the TTS base path at runtime. Body: {"path": "F:/..."}"""
    p = data.get("path", "")
    if p:
        settings.tts_ref_base = p
        print(f"[TTS] path saved: {p}", flush=True)
    return {"tts_ref_base": settings.tts_ref_base}


@router.post("/control/start")
async def tts_start():
    """Start the GPT-SoVITS API process."""
    if is_api_running():
        return {"status": "already_running"}
    ok = start_api()
    return {"status": "started" if ok else "failed"}


@router.post("/control/stop")
async def tts_stop():
    """Stop the GPT-SoVITS API process."""
    stop_api()
    return {"status": "stopped"}


@router.post("/clone/test")
async def clone_test(
    file: UploadFile = File(...),
    prompt_text: str = Form(""),
    test_text: str = Form("你好，这是我的声音测试。"),
    session: AsyncSession = Depends(get_session),
):
    """Upload reference audio, generate test speech. Returns WAV."""
    if not settings.tts_ref_base:
        raise HTTPException(status_code=400, detail="TTS not configured")

    # Save uploaded file to references dir
    refs = _refs_dir()
    refs.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix or ".wav"
    filename = f"ref_{uuid.uuid4().hex[:8]}{ext}"
    filepath = refs / filename
    content = await file.read()
    filepath.write_bytes(content)

    # Synthesize test
    audio = await synthesize(
        text=clean_text(test_text),
        ref_audio_path=str(filepath),
        prompt_text=prompt_text,
    )
    if audio is None:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"X-Ref-Audio": f"references/{filename}", "X-Prompt-Text": prompt_text}
    )


@router.post("/clone/apply")
async def clone_apply(
    data: ApplyVoiceRequest,
    session: AsyncSession = Depends(get_session),
):
    """Apply a cloned voice to a character."""
    char = await session.get(CharacterProfile, data.character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    char.tts_ref_audio = data.ref_audio
    char.tts_prompt_text = data.prompt_text
    await session.commit()

    return {"status": "ok", "character": char.name, "ref_audio": data.ref_audio}


@router.get("/characters")
async def list_characters_for_tts(session: AsyncSession = Depends(get_session)):
    """List all characters with their TTS config status."""
    result = await session.execute(select(CharacterProfile).order_by(CharacterProfile.name))
    chars = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "has_voice": bool(c.tts_ref_audio),
            "tts_ref_audio": c.tts_ref_audio,
        }
        for c in chars
    ]


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
        text=clean_text(data.text),
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
