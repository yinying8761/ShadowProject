"""`see_screen` 工具里的看图（ticket 05 / spec: multimodal-vision）。

角色自己中途想看屏幕时，用**主模型**看图，把描述作为文字返回 —— 工具契约不变
（`dispatch -> str`）。走哪条路由 `services/vision_route.plan_vision_route` 决定
（和会话级挂图同一处、同一条规则，不在这里另写一套）：

- 主模型能看图 → 让它看；**形状配置有问题、或它什么都没返回** → 明确报错
  （不悄悄换个模型顶替：那是另一个模型在回答，用户以为是角色的眼睛）
- 主模型**明确**看不了 → 专家视觉模型（配了才算）；它失败/空回复 → 明确报错
- 两者都没有 → 明确的不可用错误（不是空回复，也不是编造）

那套"为单个 MCP 视觉 server 写适配"的代码已删除（ADR-0010：MCP 是配置即插即拔）。
工具路径**不去重**：角色想再看一眼就得真的再截一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.formatters.images import text_part
from services.vision_route import plan_vision_route
from services.vision_service import DEFAULT_PROMPT, VisionUnavailable

#: 两种后端都不可用时的说法（工具结果给模型看；模型据此如实告诉用户）。
NO_BACKEND_MESSAGE = (
    "视觉不可用：当前模型不支持看图，也没有配置备用视觉模型。"
    "请如实告诉用户这次没看成，可以换一个支持视觉的模型，或在设置里配置视觉专家模型。"
)

#: 能看、但我们这边配错了形状（未知 image_api / 与 SDK 不匹配）——**报错，不降级**。
SHAPE_PROBLEM_MESSAGE = (
    "视觉不可用：当前模型的看图方式配置有问题（{reason}）。"
    "请如实告诉用户这次没看成，并提示到 设置 → 模型 → 模型能力 里检查图片格式。"
)

#: 主模型该看却没返回内容。
EMPTY_REPLY_MESSAGE = (
    "视觉不可用：模型这次没有返回屏幕描述。请如实告诉用户这次没看成，可以稍后再试。"
)

#: 注入用的哨兵：`specialist=_AUTO` 表示"用配好的那个"，`specialist=None` 表示"没有"。
_AUTO = object()


@dataclass(frozen=True)
class ScreenDescription:
    """一次看屏幕的结果：要么有描述 + 后端，要么有一条明确的错误。"""

    description: str | None = None
    backend: Literal["main", "specialist"] | None = None
    error: str | None = None


def _default_llm():
    """共享的 LLMService（进程内一个实例，缓存 SDK client —— 见 llm_service.shared_llm）。"""
    from services.llm_service import shared_llm

    return shared_llm()


def _configured_specialist():
    """配好的专家视觉模型（没配就是 None）。

    抽成函数是为了让测试换掉它（`monkeypatch.setattr(..., "_configured_specialist", ...)`），
    而不是去改全局 `settings` —— 测试不该伸手到被测代码之外。
    """
    from config import settings

    if not settings.vision_enabled():
        return None
    from services.vision_service import vision_service

    return vision_service


def _question(focus: str | None) -> str:
    prompt = (
        "这是用户当前的屏幕截图。请用 1-3 句话描述屏幕上的内容："
        "用户正在用什么应用、在做什么、有没有值得注意的文字或状态。"
    )
    if focus:
        prompt += f"\n重点关注：{focus}"
    return prompt


async def describe_screen(
    *,
    image_bytes: bytes,
    mime: str = "image/jpeg",
    focus: str | None = None,
    runtime=None,
    llm=None,
    specialist=_AUTO,
):
    """按 `plan_vision_route` 选的那条路看图：主模型 / 专家模型 / 明确错误。

    主模型的**运行期失败**（限流、断网）直接抛出 —— 那是故障，不该悄悄换个模型顶替；
    `see_screen` 会把它变成一条诚实的工具错误。
    """
    if runtime is None:
        from services.llm_config import runtime_config as runtime

    route = plan_vision_route(runtime.vision_should_attempt(), runtime.image_api())

    if route.mount_images:
        llm = llm or _default_llm()
        try:
            part = llm.image_part(image_bytes, mime)
        except ValueError as e:
            print(f"[VISION] main model shape problem: {e}", flush=True)
            return ScreenDescription(error=SHAPE_PROBLEM_MESSAGE.format(reason=e))
        messages = [
            {"role": "system", "content": DEFAULT_PROMPT},
            {"role": "user", "content": [text_part(_question(focus)), part]},
        ]
        reply = (
            await llm.chat_sync(messages, max_tokens=400, temperature=0.2) or ""
        ).strip()
        if not reply:
            print("[VISION] main model returned nothing", flush=True)
            return ScreenDescription(error=EMPTY_REPLY_MESSAGE)
        print(f"[VISION] described by main model ({len(reply)} chars)", flush=True)
        return ScreenDescription(reply, "main")

    if route.use_fallback:
        specialist = _configured_specialist() if specialist is _AUTO else specialist
        if specialist is None:
            print("[VISION] model cannot see and no specialist is configured", flush=True)
            return ScreenDescription(error=NO_BACKEND_MESSAGE)
        try:
            description = (
                await specialist.describe_image(image_bytes, mime_type=mime, focus=focus)
                or ""
            ).strip()
        except VisionUnavailable as e:
            print(f"[VISION] specialist unavailable: {e}", flush=True)
            return ScreenDescription(error=f"{e}。请如实告诉用户这次没看成。")
        except Exception as e:      # 兜底：任何异常都不能变成一段"描述"
            print(f"[VISION] specialist failed: {type(e).__name__}: {e}", flush=True)
            return ScreenDescription(
                error=f"视觉调用失败（{type(e).__name__}: {e}）。请如实告诉用户这次没看成。"
            )
        if not description:
            return ScreenDescription(error=NO_BACKEND_MESSAGE)
        print(f"[VISION] described by specialist ({len(description)} chars)", flush=True)
        return ScreenDescription(description, "specialist")

    # 能看、但不知道按哪种形状发图（plan_vision_route: UNAVAILABLE）—— 明确报错
    print(f"[VISION] shape unavailable ({route.unavailable_reason})", flush=True)
    return ScreenDescription(
        error=SHAPE_PROBLEM_MESSAGE.format(
            reason=route.unavailable_reason or "image_api 未知"
        )
    )
