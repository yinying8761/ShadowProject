"""模型能力表（ticket 01 / ADR-0011）—— 「这个模型能做什么」的唯一来源。

能力按**模型**声明（同一家厂商里也有不支持视觉的模型），厂商预设只提供默认值，
用户可以按当前模型覆盖。解析顺序固定为 **用户覆盖 → 预设默认 → 未知**，并且结果
带上来源 —— 设置面板必须能说清「支持视觉」是预设说的还是你自己开的。

业务代码（`see_screen`、搜索链、`Agent`）只通过 `LLMRuntimeConfig` 的访问器问能力，
**不允许**出现 `if provider == "deepseek"` 这类分支（ADR-0011）。

一处刻意的不对称（ADR-0011 的决定，别"统一"掉）：

- **视觉未知 → 试一次**（`should_attempt_vision`）。用户明确要求「看屏幕」，静默发一个
  纯文本请求等于骗人 —— 试失败再报错。
- **原生搜索未知 → 不走原生**（`should_attempt_native_search`）。搜索只是可选增强，
  绝不能因为配置缺项就打断一轮对话 —— 直接走本地降级链。

纯逻辑：不碰 DB / 网络 / 配置对象，只做表解析。**词表也只有这一份**
（`capability_schema()` 由 API 下发给前端，前端不再自己抄一遍顺序/取值）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: 能力名称（顺序即 UI 展示顺序）。
CAPABILITY_NAMES: tuple[str, ...] = (
    "supports_vision",
    "image_api",
    "native_search",
    "native_fetch",
    "search_api",
)

#: 布尔型能力（三态：True / False / None=未知）。
BOOLEAN_CAPABILITIES: tuple[str, ...] = (
    "supports_vision",
    "native_search",
    "native_fetch",
)

#: 图片载荷形状（与厂商名无关：三种形状覆盖所有已知厂商）。
IMAGE_APIS: tuple[str, ...] = ("openai", "anthropic", "gemini")

#: 原生搜索的响应形状；`none` = 明确不尝试原生（直接降级）。
SEARCH_APIS: tuple[str, ...] = ("openai", "anthropic", "gemini", "deepseek", "none")

#: 形状型能力 → 允许的取值。未知/非法取值一律当作"没说过"（读宽容）。
KNOWN_API_SHAPES: dict[str, tuple[str, ...]] = {
    "image_api": IMAGE_APIS,
    "search_api": SEARCH_APIS,
}


@dataclass(frozen=True)
class CapabilityValue:
    """一项能力的**生效值**与**来源**（UI 要区分「预设给的」和「你手动开的」）。"""

    value: bool | str | None
    source: str  # "user" | "preset" | "unknown"

    def as_dict(self) -> dict:
        """给 API / 前端的最小形状。"""
        return {"value": self.value, "source": self.source}


def capability_schema() -> list[dict]:
    """下发给设置面板的词表：顺序、类型、可选值（前端不再自己抄一份）。

    返回 `[{"name": ..., "kind": "boolean"|"shape", "options": [...]}, ...]`，
    `options` 对布尔能力为空（三态由消费者加「自动」选项）。
    """
    return [
        {
            "name": name,
            "kind": "shape" if name in KNOWN_API_SHAPES else "boolean",
            "options": list(KNOWN_API_SHAPES.get(name, ())),
        }
        for name in CAPABILITY_NAMES
    ]


def _is_valid(name: str, value: object) -> bool:
    """这个值是不是该项能力表里的合法取值（`None` 永远合法 = 未知）。"""
    if value is None:
        return True
    if name in BOOLEAN_CAPABILITIES:
        return isinstance(value, bool)
    return isinstance(value, str) and value in KNOWN_API_SHAPES[name]


def resolve_capabilities(
    preset: Mapping | None, overrides: Mapping | None
) -> dict[str, CapabilityValue]:
    """解析当前模型的能力：**用户覆盖 → 预设默认 → 未知**。

    `preset` 是 `config.PROVIDER_PRESETS[provider]`（或其等价物），`overrides` 是用户
    按当前模型填的覆盖。两边都允许缺项 / `None`（`None` 在覆盖里等于"没覆盖"）。

    读入口是**宽容**的：覆盖里的未知键、类型不对的值（手改 yaml 写成 `"true"`）、
    不认识的形状，全部当作"没覆盖"，回落到预设；写入口（`normalize_overrides`）
    才是严格的 —— 否则一次手滑就会留下一个永远不生效、也永远删不掉的键。
    """
    resolved: dict[str, CapabilityValue] = {}
    for name in CAPABILITY_NAMES:
        override = overrides.get(name) if overrides else None
        if override is not None and _is_valid(name, override):
            resolved[name] = CapabilityValue(override, "user")
            continue
        default = preset.get(name) if preset else None
        if default is not None and _is_valid(name, default):
            resolved[name] = CapabilityValue(default, "preset")
            continue
        resolved[name] = CapabilityValue(None, "unknown")
    return resolved


def apply_overrides(current: Mapping | None, patch: Mapping | None) -> dict:
    """把一次覆盖**补丁**合并到已有覆盖上：`None` = 删掉该项，缺项 = 保持不变。

    这就是写入口的语义（PATCH，而不是整批替换）：只改你说到的能力，其余不动 ——
    前端因此不需要先把整张表读回来再原样写回去（那正是"一次保存清空别的项"的来源）。
    """
    merged = dict(current or {})
    for name, value in (patch or {}).items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


def normalize_overrides(overrides: Mapping) -> dict:
    """校验并规范化用户覆盖（写入口）：未知能力名 / 类型不对 / 未知形状一律报错。

    `None` 会被**保留**在结果里，表示「清除这一项」（由调用方用 `apply_overrides` 合并）。
    """
    normalized: dict[str, bool | str | None] = {}
    for name, value in (overrides or {}).items():
        if name not in CAPABILITY_NAMES:
            raise ValueError(f"unknown capability: {name!r}")
        if not _is_valid(name, value):
            if name in BOOLEAN_CAPABILITIES:
                raise ValueError(f"{name} must be a boolean")
            raise ValueError(
                f"{name} must be one of {list(KNOWN_API_SHAPES[name])}"
            )
        normalized[name] = value
    return normalized


def resolve_api_shape(name: str, value: object) -> str | None:
    """把一项形状能力收敛成已知形状或 None（未知形状 = 不尝试，绝不猜一种发出去）。

    `image_api` 的调用方（视觉）在值为 None 时要**报错**；`search_api` 的调用方
    （搜索）在值为 None 时**降级** —— 这个不对称由调用方按上面的规则决定，
    这里只负责"是不是一个我们认识的形状"。
    """
    allowed = KNOWN_API_SHAPES.get(name)
    if allowed is None:
        raise ValueError(f"{name!r} is not an API-shape capability")
    return value if isinstance(value, str) and value in allowed else None


def should_attempt_vision(capability: CapabilityValue) -> bool:
    """要不要把图发给这个模型？**未知也试**（视觉是必需能力，静默丢图不可接受）。"""
    return capability.value is not False


def should_attempt_native_search(capability: CapabilityValue) -> bool:
    """要不要尝试原生搜索？**只有明确支持才试**（搜索是可选增强，未知就降级）。"""
    return capability.value is True
