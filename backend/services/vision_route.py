"""看屏幕走哪条路（ticket 02 / spec: multimodal-vision）。

一次「看屏幕」只有三种走法，互斥：

| 情况 | 怎么走 |
|---|---|
| 主模型能看图（`vision_should_attempt()` 为真，未知也试） | **挂图**：截图作为图片块进当轮请求，主模型自己看（零额外调用） |
| 主模型**明确**不能看图 | **转述**：走保留的专家视觉模型（`vision_service`），拿一段文字 |
| 能看但不知道按哪种形状发图 | **明确报错**：告诉用户，绝不静默发一个纯文本请求 |

"未知也试"这条规则只在 `capabilities.should_attempt_vision()` 一处（通过
`runtime_config.vision_should_attempt()` 取），这里不再重复判断。

纯函数：不碰屏幕 / 网络 / DB。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 三条互斥的路。
MOUNT = "mount"
FALLBACK = "fallback"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class VisionRoute:
    """这一轮「看屏幕」怎么走 —— `kind` 单一字段承载互斥性，另外两个是只读派生。"""

    kind: str
    #: 仅 `UNAVAILABLE` 时有值：机器可读的理由（当前只有 `image_api_unknown`）
    reason: str | None = None

    @property
    def mount_images(self) -> bool:
        return self.kind == MOUNT

    @property
    def use_fallback(self) -> bool:
        return self.kind == FALLBACK

    @property
    def unavailable_reason(self) -> str | None:
        return self.reason if self.kind == UNAVAILABLE else None


def plan_vision_route(can_see: bool, image_api: str | None) -> VisionRoute:
    """给能力值，选一条路。

    `can_see` 应当直接来自 `runtime_config.vision_should_attempt()` —— 它已经把
    "未知也试一次"的规则算进去了（ADR-0011 的不对称，别在这里再写一遍）。
    """
    if not can_see:
        return VisionRoute(FALLBACK)
    if image_api is None:
        return VisionRoute(UNAVAILABLE, reason="image_api_unknown")
    return VisionRoute(MOUNT)
