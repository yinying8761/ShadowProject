"""
Vision specialist (fallback only).

Since ticket #05 the main model looks at the screen itself; this module survives as the
**specialist fallback** for models that cannot see images. It takes raw image bytes plus
an optional focus question and returns a short text description.

Contract (deliberate): it either returns a real description or **raises**
`VisionUnavailable`. It never returns an error string as if it were a description —
that is exactly how a 429 once got recited as screen content for days (2026-10-20).

The image is never written to disk and never enters the conversation history.
"""
import traceback

from config import settings
from services.formatters.images import image_part, text_part


class VisionUnavailable(RuntimeError):
    """专家视觉模型用不了（没配置 / 调用失败 / 什么都没返回）。"""


#: 观察屏幕用的提示词 —— **唯一一份**：主模型看屏（services/vision_tool.py）与专家
#: 模型共用。结尾那段"忽略自己的浮窗"很重要，否则模型会去描述它自己。
DEFAULT_PROMPT = (
    "你是一个屏幕观察助手。请简洁、客观地描述屏幕上的内容，"
    "重点包括：用户正在使用的应用、当前在做什么、屏幕上的关键文字或元素、"
    "任何明显的状态（错误提示、进度、对话等）。"
    "用 1-3 句话总结，不要主观评论或建议，只描述事实。\n\n"
    "重要：请完全忽略屏幕上的 AI 助手浮窗本身（一个位于角落的半透明对话窗口，"
    "内有动漫风格立绘、对话气泡、输入框，可能显示'操作确认'、'查看屏幕'、"
    "'允许执行'、'拒绝'等按钮和文字）。这是你所在的应用界面，不属于用户在做的事，"
    "也不要在描述中提及它。如果整个屏幕只有它，回答'屏幕被 AI 助手窗口遮挡，"
    "看不到其他内容'。"
)


class VisionService:
    """Lightweight wrapper around a vision-capable LLM (the specialist)."""

    def __init__(self):
        self._clients: dict[str, object] = {}

    def _get_openai_client(self):
        if "openai" not in self._clients:
            from openai import AsyncOpenAI
            base_url = settings.get_vision_base_url() or None
            self._clients["openai"] = AsyncOpenAI(
                api_key=settings.vision_api_key,
                base_url=base_url,
            )
        return self._clients["openai"]

    def _get_anthropic_client(self):
        if "anthropic" not in self._clients:
            import anthropic
            self._clients["anthropic"] = anthropic.AsyncAnthropic(
                api_key=settings.vision_api_key
            )
        return self._clients["anthropic"]

    async def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        focus: str | None = None,
        max_tokens: int = 400,
    ) -> str:
        """Describe the screen. Returns a description or raises `VisionUnavailable`.

        Caller must pre-compress the image to keep token cost down.
        """
        if not settings.vision_enabled():
            raise VisionUnavailable(
                "没有配置备用视觉模型（VISION_API_KEY 为空），而当前模型也不能看图"
            )

        prompt = DEFAULT_PROMPT
        if focus:
            prompt += f"\n\n用户关心的方面：{focus}"

        sdk_type = settings.get_vision_sdk_type()
        model = settings.get_vision_model()

        print(
            f"[VISION] specialist model={model} sdk={sdk_type} "
            f"bytes={len(image_bytes)} focus={focus!r}",
            flush=True,
        )

        try:
            if sdk_type == "anthropic":
                result = await self._describe_anthropic(
                    image_bytes, mime_type, prompt, model, max_tokens
                )
            else:
                result = await self._describe_openai(
                    image_bytes, mime_type, prompt, model, max_tokens
                )
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[VISION] specialist ERROR: {type(e).__name__}: {e}\n{tb}", flush=True)
            raise VisionUnavailable(f"视觉模型调用失败（{type(e).__name__}: {e}）") from e

        if not result.strip():
            raise VisionUnavailable("视觉模型没有返回任何描述")
        print(f"[VISION] specialist success: {result[:120]}...", flush=True)
        return result

    async def _describe_openai(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int,
    ) -> str:
        part = image_part("openai", image_bytes, mime_type)
        # 这张图已经被降采样过；再让 provider 按"低细节"计费，省一半图片 token。
        part["image_url"]["detail"] = "low"
        client = self._get_openai_client()
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": [text_part(prompt), part]}],
        )
        return (resp.choices[0].message.content or "").strip()

    async def _describe_anthropic(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int,
    ) -> str:
        client = self._get_anthropic_client()
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        image_part("anthropic", image_bytes, mime_type),
                        text_part(prompt),
                    ],
                }
            ],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


vision_service = VisionService()
