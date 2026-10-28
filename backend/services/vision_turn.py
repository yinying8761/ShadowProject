"""一次「看屏幕」的落地（ticket 02 / spec: multimodal-vision）。

`plan_vision_route` 只做决定；这里把决定变成这一轮要带的东西：截图（+极短说明）、
备用视觉模型的转述（+说明），或者一条明确的错误。`api/chat.py` 只负责"要不要看"和
把结果交给 `Agent.run(images=…, vision_note=…)`。

所有说明（note）**都不落库**：它们只进这一次请求 —— 历史里只留用户原话与角色回复
（2026-10-20 的教训：把屏幕描述拼进用户消息，然后在上下文里被复读了两天）。

依赖注入：屏幕闸门与能力值都由调用方给，这里不碰配置文件也不碰屏幕。
"""

from __future__ import annotations

from dataclasses import dataclass

from services.vision_route import plan_vision_route

#: 挂图时给模型的极短说明。
MOUNT_NOTE = (
    "（系统：用户请求看屏幕，截图已作为图片附在本轮用户消息里。"
    "请直接描述你看到的内容；不要声称看不到，也不要再调用 see_screen。）"
)

#: 主模型看不了时，备用视觉模型的转述。
DESCRIPTION_NOTE = (
    "（系统：下面是你通过备用视觉模型看到的屏幕内容，据此自然回答；"
    "不要再说自己看不到屏幕，也不要再调用 see_screen。）\n{description}"
)

#: 没拍成（用户拒绝 / 截图失败）：如实说没看成，别编。
CAPTURE_FAILED_NOTE = (
    "（系统：这次截屏没有成功（用户拒绝或截图失败）。"
    "如实告诉用户这次没看成，不要编造屏幕内容。）"
)

#: 形状未知：明确报错（用户看得到），同时告诉模型为什么这一轮没有图。
UNAVAILABLE_MESSAGE = (
    "当前模型没有可用的看图方式（image_api 未设置或与供应商不匹配）："
    "这次没有截图。请到 设置 → 模型 → 模型能力 里指定图片格式后重试。"
)
UNAVAILABLE_NOTE = (
    "（系统：用户请求看屏幕，但当前模型没有可用的看图方式（image_api）。"
    "请如实告诉用户这次看不了，并提示到 设置 → 模型 → 模型能力 里指定图片格式；"
    "不要编造屏幕内容。）"
)


@dataclass(frozen=True)
class VisionTurn:
    """这一轮要交给 `Agent.run` 的东西。"""

    images: tuple[tuple[bytes, str], ...] = ()
    note: str | None = None
    #: 需要**向用户明确报出**的错误（只有"形状未知"这一种）
    error: str | None = None


async def prepare_vision_turn(
    *, gate, can_see: bool, image_api: str | None
) -> VisionTurn:
    """拍图 / 转述 / 报错 —— 按 `plan_vision_route` 选的那条路执行。"""
    route = plan_vision_route(can_see, image_api)

    if route.mount_images:
        captured = await gate.capture_images()
        if captured is None:
            print("[SCREEN] route=mount capture denied or failed", flush=True)
            return VisionTurn(note=CAPTURE_FAILED_NOTE)
        print("[SCREEN] route=mount (main-model vision)", flush=True)
        return VisionTurn(images=(captured,), note=MOUNT_NOTE)

    if route.use_fallback:
        description = await gate.try_capture()
        if not description:
            print("[SCREEN] route=fallback specialist failed", flush=True)
            return VisionTurn(note=CAPTURE_FAILED_NOTE)
        print("[SCREEN] route=fallback (specialist description)", flush=True)
        return VisionTurn(note=DESCRIPTION_NOTE.format(description=description))

    print(f"[SCREEN] route=unavailable ({route.unavailable_reason})", flush=True)
    return VisionTurn(note=UNAVAILABLE_NOTE, error=UNAVAILABLE_MESSAGE)
