"""
Screen capture + vision tool (ticket 05 / spec: multimodal-vision).

Pipeline:
  mss screenshot -> Pillow resize/compress -> **主模型**看图（它看不了才用专家视觉模型）
  -> 描述文字作为工具结果返回（`dispatch -> str` 契约不变）。

The image is held only in memory; it is never written to disk and never put into
the character agent's conversation history.

**不为任何单个 MCP server 写适配**（ADR-0010）：这里没有"某个视觉 server 的工具名/
参数名/环境变量"的知识 —— MCP 是配置即插即拔。之前那套适配（`_try_mcp_vision`、
`VISION_MCP_TOOL`、`vision_mcp_tool`）随本票删除。
"""
import asyncio
import io
import json
import traceback
from collections import deque
from typing import Optional

from services.vision_tool import describe_screen


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


def _dhash_of(img) -> int:
    """一张 PIL 图的 64-bit dHash（`_capture_dhash_sync` 与 `dhash_from_jpeg` 共用）。"""
    from PIL import Image

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

    return _dhash_of(img)


def dhash_from_jpeg(image_bytes: bytes) -> int:
    """已经拍好的这张图的 dHash（不再为了指纹重新截屏）。

    与 `_capture_dhash_sync` 同一套算法、同一个阈值语义，只是输入换成 JPEG 字节 ——
    ticket #02 的"把图挂到当轮请求"路径已经拿到图了，不该为了指纹再抓一次屏幕。
    """
    import io

    from PIL import Image

    return _dhash_of(Image.open(io.BytesIO(image_bytes)))


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
    """Capture the screen and let a model describe it.

    **主模型优先**（ticket #05）：角色自己中途想看时，用主模型看图，把描述作为文字
    返回 —— 工具契约不变（`dispatch -> str`，JSON 里带 `description`）。主模型看不了
    （能力明确为否 / 不知道按哪种形状发图）才回落到专家视觉模型；两者都没有则返回
    **明确的不可用错误**。

    **不去重**：角色想再看一眼就得真的再截一次（指纹去重只服务主动陪伴那条路）。

    Args:
        focus: Optional question/aspect for the model to focus on,
               e.g. "用户在玩什么游戏" or "屏幕上有报错吗".
        monitor: Monitor index. 0 = full virtual desktop, 1 = primary, 2+ = others.

    Returns:
        JSON string with the description and metadata, or an ``error`` key.
    """
    try:
        image_bytes, width, height = await asyncio.to_thread(_capture_sync, monitor)
        print(f"[SCREEN] captured {width}x{height} {len(image_bytes)} bytes", flush=True)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[SCREEN] capture failed: {type(e).__name__}: {e}\n{tb}", flush=True)
        return json.dumps({"error": f"Screen capture failed: {type(e).__name__}: {e}"})

    try:
        result = await describe_screen(image_bytes=image_bytes, focus=focus)
    except Exception as e:
        print(f"[SCREEN] vision call failed: {type(e).__name__}: {e}", flush=True)
        return json.dumps(
            {
                "error": (
                    f"视觉调用失败（{type(e).__name__}: {e}）。"
                    "不要编造屏幕内容；如实告诉用户这次没看成，可以稍后再试。"
                )
            },
            ensure_ascii=False,
        )

    if result.error or not result.description:
        reason = (result.error or "视觉没有返回内容").rstrip("。. ")
        return json.dumps(
            {"error": f"{reason}。不要编造屏幕内容；如实告诉用户这次没看成。"},
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "description": result.description,
            "image_size": f"{width}x{height}",
            "bytes": len(image_bytes),
            "vision_backend": result.backend,
        },
        ensure_ascii=False,
    )
