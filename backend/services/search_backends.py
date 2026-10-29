"""后端链计算（ticket 03 / spec S1）：能力 + 配置 → 这一查询该按什么顺序试。

纯函数，不读全局配置、不发请求：调用方把事实传进来，测试直接喂组合。
顺序（spec）：`native → duckduckgo → bing_web → searxng → bing`；
不可用的项**去掉**（没配 searxng / 没 bing key / 能力不支持 / 关掉了原生），
用户显式配置的那个本地后端排到本地链最前（沿用 `SEARCH_BACKEND` 的历史语义）。
"""

from __future__ import annotations

#: 本地抓取后端（顺序即默认优先级）。
LOCAL_BACKENDS = ("duckduckgo", "bing_web", "searxng", "bing")

#: 需要额外配置的后端 → 调用方要给的那个布尔事实（不在表里的恒可用）。
_REQUIRES = {"searxng": "has_searxng", "bing": "has_bing_key"}

#: `SEARCH_BACKEND` 的取值 → 链里的后端名（historic names kept for the env var）。
_BACKEND_ALIASES = {
    "ddgs": "duckduckgo",
    "duckduckgo": "duckduckgo",
    "bing_web": "bing_web",
    "searxng": "searxng",
    "bing": "bing",
    "azure_bing": "bing",
}


def search_backend_order(
    *,
    prefer_native: bool,
    native_available: bool,
    configured: str,
    has_searxng: bool,
    has_bing_key: bool,
) -> tuple[str, ...]:
    """算出这一次查询要按顺序尝试的后端。

    - `native` 只在「用户没关 + 模型能力/形状可用」时进链首
    - `searxng` 需要 `SEARXNG_URL`、`bing` 需要 `BING_API_KEY`，缺了就不进链
    - `duckduckgo` 与 `bing_web` 无需配置（免费、国内可用），恒在链里
    """
    chain: list[str] = []
    if prefer_native and native_available:
        chain.append("native")

    facts = {"has_searxng": has_searxng, "has_bing_key": has_bing_key}
    local = [b for b in LOCAL_BACKENDS if facts.get(_REQUIRES.get(b, ""), True)]

    # 用户配置的那个排到本地链最前（配错了就按默认顺序）
    wanted = _BACKEND_ALIASES.get((configured or "").strip().lower())
    if wanted in local:
        local.remove(wanted)
        local.insert(0, wanted)

    return tuple(chain + local)
