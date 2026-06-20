"""
Screen capture + vision tool.

Pipeline:
  mss screenshot -> Pillow resize/compress -> vision_service describes it
  -> short text returned to the character agent as a tool result.

The image is held only in memory; it is never written to disk and
never put into the character agent's conversation history.
"""
import asyncio
import io
import json
import traceback
from collections import deque
from typing import Optional

from services.vision_service import vision_service


MAX_DIMENSION = 1280  # Resize so longest side <= 1280 before JPEG encoding
JPEG_QUALITY = 70


def _capture_sync(monitor_index: int = 0) -> tuple[bytes, int, int]:
    """
    Synchronous capture via mss.
    monitor_index: 0 = all monitors combined, 1+ = individual monitor.
    Returns (jpeg_bytes, width, height).
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor_index < 0 or monitor_index >= len(monitors):
            monitor_index = 1 if len(monitors) > 1 else 0
        mon = monitors[monitor_index]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    # Downscale for token efficiency
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_DIMENSION:
        scale = MAX_DIMENSION / longest
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    data = buf.getvalue()
    return data, img.size[0], img.size[1]


# --- Perceptual hash (dHash) for screen change detection ---

DHASH_COLS = 9
DHASH_ROWS = 8
DHASH_BITS = 64
DHASH_THRESHOLD = 10  # Hamming distance: ≤10 = same scene, >10 = new scene


def _capture_dhash_sync(monitor_index: int = 0) -> int:
    """
    Capture screen and return a 64-bit dHash (perceptual hash).

    Algorithm: resize screen to 9×8 grayscale, then compare each pixel
    with its right neighbor. If left < right, set bit 1, else 0.
    This yields 64 bits (8 rows × 8 comparisons) that represent the
    *structure* of the image — robust against small pixel-level changes
    (clock ticks, cursor move) but sensitive to actual scene changes.

    Runs synchronously (~10ms) — no vision model call.
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor_index < 0 or monitor_index >= len(monitors):
            monitor_index = 1 if len(monitors) > 1 else 0
        mon = monitors[monitor_index]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    img_gray = img.convert("L")
    img_small = img_gray.resize((DHASH_COLS, DHASH_ROWS), Image.LANCZOS)
    pixels = list(img_small.getdata())

    hash_int = 0
    for row in range(DHASH_ROWS):
        for col in range(DHASH_COLS - 1):
            left = pixels[row * DHASH_COLS + col]
            right = pixels[row * DHASH_COLS + col + 1]
            if left < right:
                hash_int |= 1 << (row * (DHASH_COLS - 1) + col)

    return hash_int


def hamming_distance(h1: int, h2: int) -> int:
    """Number of differing bits between two 64-bit hashes."""
    return (h1 ^ h2).bit_count()


class ScreenFingerprintStore:
    """
    FIFO queue of recently-seen screen fingerprints.

    When a new screenshot arrives, it is compared against every stored
    fingerprint.  If at least one fingerprint is within DHASH_THRESHOLD
    (Hamming distance ≤ 10), the screen is considered "already seen"
    and a vision call is skipped.

    The queue has a fixed capacity (default 5) — when full, the oldest
    entry is evicted, so the store tracks the last N distinct scenes.
    """

    def __init__(self, maxlen: int = 5):
        self._hashes: deque[int] = deque(maxlen=maxlen)
        self._threshold = DHASH_THRESHOLD

    def is_known(self, new_hash: int) -> bool:
        """True if new_hash is perceptually similar to any stored hash."""
        for h in self._hashes:
            if hamming_distance(new_hash, h) <= self._threshold:
                return True
        return False

    def add(self, hash_int: int) -> None:
        """Always store the fingerprint (even if already known)."""
        self._hashes.append(hash_int)

    def check_and_add(self, new_hash: int) -> bool:
        """
        Check whether the screen is new, then always store it.
        Returns True if this is a NEW scene (vision call warranted).
        """
        known = self.is_known(new_hash)
        self.add(new_hash)
        return not known

    def __len__(self) -> int:
        return len(self._hashes)


# --- Public tool ---

async def see_screen(focus: Optional[str] = None, monitor: int = 0) -> str:
    """
    Capture the screen and have the vision agent describe it.

    Args:
        focus: Optional question/aspect for the vision agent to focus on,
               e.g. "用户在玩什么游戏" or "屏幕上有报错吗".
        monitor: Monitor index. 0 = full virtual desktop, 1 = primary, 2+ = others.

    Returns:
        JSON string with the description and metadata.
    """
    try:
        image_bytes, width, height = await asyncio.to_thread(_capture_sync, monitor)
        print(f"[SCREEN] captured {width}x{height} {len(image_bytes)} bytes", flush=True)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[SCREEN] capture failed: {type(e).__name__}: {e}\n{tb}", flush=True)
        return json.dumps({"error": f"Screen capture failed: {type(e).__name__}: {e}"})

    description = await vision_service.describe_image(
        image_bytes,
        mime_type="image/jpeg",
        focus=focus,
    )

    return json.dumps({
        "description": description,
        "image_size": f"{width}x{height}",
        "bytes": len(image_bytes),
    }, ensure_ascii=False)
