"""
Message augmentation pipeline — enriches user messages before they reach the agent.

Pipeline stages (executed sequentially):
  1. Search hint: detect search intent → append an advisory hint (no search)
  2. POI injector: detect food/restaurant keywords → query nearby places

All stages are transparent to the caller — just call `augment(content)` and get
back the augmented string ready to pass to the agent.
"""

from core.router import need_search

# Advisory nudge appended when the keyword router fires (ADR-0003). It must read
# as a suggestion, never as if results had already been gathered — the main LLM
# holds the search decision and calls the `research` tool when it sees fit.
SEARCH_HINT = (
    "[提示：这条消息可能涉及时效性或外部信息，如有必要可调用 research 工具核实；"
    "若只是日常闲聊，忽略本条即可。]"
)

FOOD_KEYWORDS = [
    "吃什么", "推荐", "好吃的", "美食", "附近", "餐厅", "饭店",
    "外卖", "外卖点", "点外卖", "饿了", "吃饭", "想吃", "请客",
    "特色", "小吃", "夜宵", "早餐", "午餐", "晚餐", "火锅",
    "烧烤", "面馆", "奶茶", "咖啡", "甜品",
]


async def augment(content: str) -> str:
    """
    Run the full augmentation pipeline on the user message.

    Returns the original content if no augmentation applies, or the
    content with appended search / POI context blocks otherwise.
    """
    augmented = content

    # ── Stage 1: Search hint ───────────────────────────────────────
    # Advisory only — a keyword hit never executes a search (ADR-0003).
    if need_search(content):
        print(f"[MessageAugmenter] search hint for: {content[:60]}", flush=True)
        augmented = _append_block(augmented, SEARCH_HINT)

    # ── Stage 2: POI / food injection ──────────────────────────────
    if any(kw in content for kw in FOOD_KEYWORDS):
        try:
            from database import async_session
            from services.location_service import search_nearby_places, nearby_to_context
            from models.user_config import UserConfig

            async with async_session() as session:
                cfg = await session.get(UserConfig, 1)
                if cfg and cfg.location_lat and cfg.location_lng:
                    places = await search_nearby_places(
                        cfg.location_lat,
                        cfg.location_lng,
                        keywords="餐饮|美食|小吃|特色",
                    )
                    if places:
                        ctx = nearby_to_context(places)
                        augmented = _inject_context(augmented, ctx)
                        print(
                            f"[MessageAugmenter] injected {len(places)} nearby places",
                            flush=True,
                        )
        except Exception as e:
            print(f"[MessageAugmenter] POI injection failed: {e}", flush=True)

    return augmented


def _append_block(base: str, block: str) -> str:
    """Append a block of context to the base message."""
    return f"{base}\n\n{block}"


def _inject_context(base: str, context: str) -> str:
    """Append a search-result block to the base message."""
    return _append_block(
        base,
        f"[帮助AI回答的搜索参考资料，请自然地融入回复，不要照念：\n{context}]",
    )
