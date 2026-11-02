"""图片内容块（ticket 02 / ADR-0011）：形状只有两种，且**住在 formatter 缝上**。

ADR-0011 / spec: multimodal-vision 要求图片块加在既有的 `MessageFormatter` 缝上，
不另起一套机制：

- `MessageFormatter.image_part()` —— 默认就是 **OpenAI 兼容**形状（大多数厂商）
- `AnthropicFormatter.image_part()` —— Claude 的 `source` 形状

原生 Gemini 的 `inline_data` 形状已删除：`sdk_type` 只有 openai / anthropic，它永远
过不了 `LLMService.image_part` 的 SDK 一致性检查（Gemini 走 OpenAI 兼容端点）。

这个模块只放两样东西：① 与厂商无关的小工具（base64 / data URL / 文本块）；
② `image_part(image_api, …)` 这个"按能力表取值挑一个 formatter"的分派器 ——
和搜索那边的 `SEARCH_PARSERS[search_api]` 是同一个形状：**能力表给名字，缝给实现**。
"""

from __future__ import annotations

import base64


def b64(data: bytes) -> str:
    """图片字节 → base64（ASCII 的 `str`，各家 SDK 都要这个形态）。"""
    return base64.b64encode(data).decode("ascii")


def data_url(mime: str, data: bytes) -> str:
    """`data:<mime>;base64,<…>` —— OpenAI 兼容端点的图片引用形式。"""
    return f"data:{mime};base64,{b64(data)}"


def text_part(text: str) -> dict:
    """多模态消息里的文本块（两种形状下都是 `{"type": "text", "text": …}`）。"""
    return {"type": "text", "text": text}


def image_part(image_api: str | None, data: bytes, mime: str) -> dict:
    """把图片变成**这个模型**能接受的 content block。

    未知 / 缺失的 `image_api` 抛 `ValueError` —— 视觉是用户明确要求的，静默发一个
    纯文本请求等于骗人（ADR-0011 的不对称：视觉报错，搜索才降级）。
    """
    from services.formatters import get_image_formatter

    formatter = get_image_formatter(image_api)
    if formatter is None:
        raise ValueError(
            f"unknown image_api: {image_api!r} —— 不知道怎么把图片发给这个模型"
        )
    return formatter.image_part(data, mime)
