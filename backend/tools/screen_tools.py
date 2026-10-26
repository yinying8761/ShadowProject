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


# --- MCP vision dispatch (set by main.py) ---

_mcp_dispatch = None
"""Module-level reference to ToolRuntime.dispatch, set by main.py
after ToolRuntime and MCP servers are initialised."""


def set_mcp_dispatch(dispatch) -> None:
    """Wire the MCP tool dispatcher so :func:`see_screen` can route
    vision calls through MCP when available."""
    global _mcp_dispatch
    _mcp_dispatch = dispatch


# --- Public tool ---

#: Default name of the MCP vision tool. The server name is whatever the user
#: called it in data/mcp_servers.json, so this is overridable via
#: ``VISION_MCP_TOOL`` in .env — a mismatch here is silent (the dispatch just
#: errors and see_screen falls back), which is exactly how the vision path
#: stayed broken while the MCP server was connecting fine.
VISION_MCP_TOOL = "mcp__vision__analyze_image"


def _vision_mcp_tool() -> str:
    """Resolve the MCP vision tool name (settings override → default)."""
    from config import settings

    return settings.vision_mcp_tool or VISION_MCP_TOOL


#: Why the last MCP vision attempt produced nothing. ``see_screen`` reports this
#: to the agent instead of letting a provider error masquerade as a description.
_last_vision_error: str | None = None

#: Deadline for the MCP vision call. It must stay **below** the sandbox timeout
#: for ``see_screen`` (40s, ``tool_runtime._default_timeout_sec``): if the
#: sandbox fires first it cancels the MCP ``call_tool`` mid-flight, and
#: cancelling inside the SDK is what corrupts its session (ADR-0010). Here the
#: wait is abandoned instead — see ``_try_mcp_vision``.
VISION_TIMEOUT_SEC = 25.0

#: Provider-side failures that arrive as *ordinary text* (the vision server
#: answers a 429 / missing key as a normal tool result, not ``isError``), so
#: they have to be recognised by content or they get returned as a "description".
_VISION_ERROR_MARKERS = (
    "视觉服务链全部失败",
    "视觉模型调用失败",
    "视觉请求失败",
    "余额不足",
    "访问量过大",
    "缺少 VISION_API_KEY",
    "Error code:",
    "HTTP 400",
    "HTTP 401",
    "HTTP 429",
)


def _looks_like_vision_error(text: str) -> bool:
    """True when *text* is the vision backend complaining, not describing."""
    return any(marker in text for marker in _VISION_ERROR_MARKERS)


def _consume_task_result(task: asyncio.Task) -> None:
    """Done-callback for an abandoned call: never leave its exception unread."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"[SCREEN] late MCP vision result failed: {exc!r}", flush=True)


async def _try_mcp_vision(image_bytes: bytes, focus: str | None) -> str | None:
    """Try to describe *image_bytes* via MCP vision tool.

    Returns the description text, or *None* when the MCP tool is unavailable or
    the call failed (the reason is left in ``_last_vision_error``).

    The call is awaited with ``asyncio.wait`` instead of ``wait_for``: on
    timeout it is *abandoned*, never cancelled. A cancelled ``call_tool`` tears
    the MCP client's anyio scope down exactly like a cancelled connect did, and
    a hung vision server must not wedge the app's tool loop (2026-10-20: the 15s
    sandbox timeout fired mid-call and the backend went away).
    """
    global _last_vision_error
    _last_vision_error = None

    if _mcp_dispatch is None:
        _last_vision_error = "未接入 MCP 视觉服务"
        return None

    import base64 as _b64

    try:
        b64 = _b64.b64encode(image_bytes).decode("ascii")
        # Argument names follow the vision server's own schema
        # (`analyze_image(image: str, prompt: str | None = None, task=...)`):
        # an image URL/path/base64 string, not a dict of bytes.
        task = asyncio.create_task(_mcp_dispatch(
            _vision_mcp_tool(),
            {
                "image": f"data:image/jpeg;base64,{b64}",
                "prompt": focus or "",
            },
        ))
        done, _pending = await asyncio.wait({task}, timeout=VISION_TIMEOUT_SEC)
        if task not in done:
            _last_vision_error = f"视觉服务 {VISION_TIMEOUT_SEC:.0f}s 内没有响应"
            print(f"[SCREEN] MCP vision timed out: {_last_vision_error}", flush=True)
            task.add_done_callback(_consume_task_result)
            return None

        result_str = task.result()
        # MCP handler returns JSON — try to extract the description
        try:
            data = json.loads(result_str)
            if isinstance(data, dict):
                # The vision server reports failures *inside* an ordinary tool
                # result: {"success": false, "result": "<error text>",
                # "error": "VISION_API_ERROR"}. Its `result` field holds the
                # provider complaint, so the flags must be honoured *before* any
                # field is treated as a description (2026-10-22: this exact
                # shape put a 429 stack into a user message and the character
                # recited it for days).
                text = data.get("description") or data.get("result") or data.get("text")
                failed = data.get("success") is False or bool(data.get("error"))
                if failed or (text and _looks_like_vision_error(str(text))):
                    reason = str(text or data.get("error") or "").strip()[:300]
                    _last_vision_error = reason or "视觉服务返回失败"
                    print(f"[SCREEN] MCP vision failed: {_last_vision_error}", flush=True)
                    return None
                if text:
                    return str(text)
        except (json.JSONDecodeError, TypeError):
            pass
        # Non-JSON result — use as-is if it looks like a description. A
        # provider failure also arrives this way, and handing it to the agent as
        # a "description" is how a 429 turned into "the model saw the screen".
        if result_str and _looks_like_vision_error(result_str):
            _last_vision_error = result_str[:300]
            print(f"[SCREEN] MCP vision failed: {_last_vision_error}", flush=True)
            return None
        if result_str and len(result_str) > 10:
            return result_str
    except Exception as e:
        _last_vision_error = f"{type(e).__name__}: {e}"
        print(f"[SCREEN] MCP vision call failed: {_last_vision_error}", flush=True)

    return None


async def see_screen(focus: Optional[str] = None, monitor: int = 0) -> str:
    """
    Capture the screen and have the vision agent describe it.

    Tries MCP vision first. When MCP is wired it is authoritative: a failure
    returns an explicit ``error`` (never the retired legacy model, and never a
    provider complaint dressed up as a description). The legacy
    ``VisionService`` is used only when MCP is not configured at all.

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

    # ── Try MCP vision ────────────────────────────────────────────
    mcp_wired = _mcp_dispatch is not None
    description = await _try_mcp_vision(image_bytes, focus)
    if description is not None:
        print(f"[SCREEN] described via MCP vision ({len(description)} chars)", flush=True)
        return json.dumps({
            "description": description,
            "image_size": f"{width}x{height}",
            "bytes": len(image_bytes),
            "vision_backend": "mcp",
        }, ensure_ascii=False)

    if mcp_wired:
        # MCP is the configured vision path, so the legacy model is NOT tried:
        # .env still names a retired model (qwen-vl-max), and a provider error
        # must never be handed to the agent as if it were the screen content.
        # The agent gets an explicit failure and says so honestly.
        reason = _last_vision_error or "MCP 视觉工具调用失败"
        print(f"[SCREEN] vision unavailable via MCP: {reason}", flush=True)
        return json.dumps({
            "error": (
                f"视觉服务暂时不可用（{reason}）。"
                "不要编造屏幕内容；如实告诉用户这次没看成，可以稍后再试。"
            ),
        }, ensure_ascii=False)

    # ── Legacy vision_service (only when MCP is not configured at all) ────
    description = await vision_service.describe_image(
        image_bytes,
        mime_type="image/jpeg",
        focus=focus,
    )
    print("[SCREEN] described via legacy vision model", flush=True)

    return json.dumps({
        "description": description,
        "image_size": f"{width}x{height}",
        "bytes": len(image_bytes),
        "vision_backend": "legacy",
    }, ensure_ascii=False)
