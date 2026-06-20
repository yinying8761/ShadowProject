"""
Vision specialist agent.

Takes raw image bytes + an optional focus question and returns
a short text description that the character agent can consume
as a tool result. The character agent never sees the image itself —
this keeps vision-model tokens isolated from the dialogue context.
"""
import base64
import traceback
from config import settings


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
    """Lightweight wrapper around a vision-capable LLM."""

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
        """
        Send an image to the vision model. Returns a short text description.
        Caller must pre-compress the image to keep token cost down.
        """
        if not settings.vision_enabled():
            return (
                "(视觉功能未启用：请在 .env 配置 VISION_API_KEY "
                "和 VISION_PROVIDER，例如 qwen + qwen-vl-max)"
            )

        prompt = DEFAULT_PROMPT
        if focus:
            prompt += f"\n\n用户关心的方面：{focus}"

        sdk_type = settings.get_vision_sdk_type()
        model = settings.get_vision_model()

        print(f"[VISION] calling model={model} sdk={sdk_type} "
              f"bytes={len(image_bytes)} focus={focus!r}", flush=True)

        try:
            if sdk_type == "anthropic":
                result = await self._describe_anthropic(
                    image_bytes, mime_type, prompt, model, max_tokens
                )
            else:
                result = await self._describe_openai(
                    image_bytes, mime_type, prompt, model, max_tokens
                )
            print(f"[VISION] success: {result[:120]}...", flush=True)
            return result
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[VISION] ERROR: {type(e).__name__}: {e}\n{tb}", flush=True)
            return f"(视觉模型调用失败: {type(e).__name__}: {e})"

    async def _describe_openai(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int,
    ) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64}"
        client = self._get_openai_client()
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "low"},
                        },
                    ],
                }
            ],
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
        b64 = base64.b64encode(image_bytes).decode("ascii")
        client = self._get_anthropic_client()
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


vision_service = VisionService()
