"""原生 Gemini `generateContent` 的最小 REST 传输（ticket 06）。

为什么要单独一个类：ADR-0011 规定"知道厂商的只有 config 和缝的实现"。Gemini 的原生
grounding 没有 SDK 依赖，只有一个 REST 端点，所以把 **host / 认证头 / URL 拼装** 收在
这里，业务代码只调 `generate(model, payload)`。

预设里的 Gemini base_url 是**OpenAI 兼容端点**（`…/v1beta/openai/`）；原生形状要的是
`…/v1beta/models/{model}:generateContent`，所以构造时把结尾的 `/openai` 去掉 ——
用户若自己填了原生地址，原样使用。
"""

from __future__ import annotations

from config import GEMINI_API_KEY_HEADER, GEMINI_NATIVE_BASE_URL


class GeminiRestClient:
    """`generateContent` 的 HTTP 客户端（只有这一个方法，只服务 gemini 形状）。"""

    def __init__(self, api_key: str, base_url: str | None = None):
        self._api_key = api_key
        base = (base_url or GEMINI_NATIVE_BASE_URL).rstrip("/")
        if base.endswith("/openai"):
            base = base[: -len("/openai")]
        self._base = base

    def generate_url(self, model: str) -> str:
        return f"{self._base}/models/{model}:generateContent"

    async def generate(self, model: str, payload: dict) -> dict:
        import httpx

        from services.llm_service import _llm_timeout

        async with httpx.AsyncClient(timeout=_llm_timeout()) as client:
            response = await client.post(
                self.generate_url(model),
                json=payload,
                headers={GEMINI_API_KEY_HEADER: self._api_key},
            )
            response.raise_for_status()
            return response.json()
