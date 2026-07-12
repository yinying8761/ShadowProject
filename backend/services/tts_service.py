"""TTS service — calls GPT-SoVITS API for neural voice synthesis."""

import re
import httpx
from config import settings


# GPT-SoVITS API config
TTS_API_URL = getattr(settings, "tts_api_url", "http://127.0.0.1:9880/tts")
TTS_TEMPERATURE = 0.6
TTS_TOP_K = 30
TTS_SPLIT_METHOD = "cut5"

def _clean_char(c: str) -> str:
    """Keep only speakable characters for TTS — CJK + ASCII + punctuation."""
    cp = ord(c)
    if cp < 0x80: return c                        # ASCII
    if 0x2000 <= cp <= 0x206F: return c           # General Punctuation
    if 0x2E80 <= cp <= 0x2FDF: return c           # CJK Radicals
    if 0x3000 <= cp <= 0x303F: return c           # CJK Symbols
    if 0x3200 <= cp <= 0x4DBF: return c           # Enclosed CJK
    if 0x4E00 <= cp <= 0x9FFF: return c           # CJK Unified
    if 0xF900 <= cp <= 0xFAFF: return c           # CJK Compatibility
    if 0xFE10 <= cp <= 0xFE1F: return c           # Vertical Forms
    if 0xFE30 <= cp <= 0xFE4F: return c           # CJK Compatibility Forms
    if 0xFF00 <= cp <= 0xFFEF: return c           # Halfwidth/Fullwidth
    return ''  # everything else (emoji, symbols, etc.) is stripped


def clean_text(text: str) -> str:
    """Strip emoji, stage directions, and other non-speakable content for TTS."""
    # Remove parenthetical stage directions: （微微一笑～😊） → ""
    text = re.sub(r"（[^）]*）", "", text)
    # Strip emoji
    text = "".join(_clean_char(c) for c in text).strip()
    # Collapse multiple newlines
    text = re.sub(r"\n{2,}", "。", text)
    return text


async def synthesize(
    text: str,
    ref_audio_path: str,
    prompt_text: str,
    speed: float = 1.0,
) -> bytes | None:
    """Generate speech audio from text using character's reference voice.
    Returns WAV bytes or None on failure.
    """
    if not ref_audio_path or not text.strip():
        return None

    clean = clean_text(text.strip())
    if not clean:
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                TTS_API_URL,
                json={
                    "text": clean,
                    "text_lang": "zh",
                    "ref_audio_path": ref_audio_path,
                    "prompt_lang": "zh",
                    "prompt_text": prompt_text,
                    "speed_factor": speed,
                    "top_k": TTS_TOP_K,
                    "temperature": TTS_TEMPERATURE,
                    "text_split_method": TTS_SPLIT_METHOD,
                },
            )
            if resp.status_code == 200:
                return resp.content
            detail = resp.json()
            print(f"[TTS] API error: {detail.get('message')} | Exception: {detail.get('Exception')} | text_len={len(text)}", flush=True)
            return None
    except Exception as e:
        print(f"[TTS] request failed: {e}", flush=True)
        return None
