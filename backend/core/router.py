"""
Search intent router — decides whether a user message needs web search.
Uses keyword matching (no LLM call). Search results are cached for 5 min.
"""

import time

# ── Keywords ──────────────────────────────────────────────────────

HARD_TRIGGERS: list[str] = [
    # price
    "多少钱", "价格", "售价", "报价", "多少钱一斤", "多少钱一个",
    # time-sensitive
    "今天天气", "今天", "最新", "最近", "新闻", "发布", "刚刚",
    "现在几点", "几点了", "今天几号", "日期", "时间",
    # fact lookup
    "是什么意思", "什么是", "定义", "解释一下",
    # hardware / products
    "参数", "配置", "性能", "跑分", "评测",
    "什么时候出", "什么时候发布", "哪一年", "什么时候更新",
    # software
    "版本", "官网", "下载", "安装", "怎么用",
    # finance
    "股票", "汇率", "涨了", "跌了", "行情",
    # location
    "地址", "在哪里", "怎么去", "营业时间",
    # game
    "什么时候更新", "新版本", "更新内容", "卡池",
]

SOFT_TRIGGERS: list[str] = [
    # product names (likely facts needed)
    "RTX", "5090", "5080", "4090", "iPhone", "华为", "小米", "红米",
    "MacBook", "ThinkPad", "ROG", "Steam", "任天堂", "PS5", "Xbox",
    # games
    "原神", "明日方舟", "终末地", "星穹铁道", "绝区零", "鸣潮", "王者荣耀",
    "深岩银河", "异动核心", "Rogue Core",
    # tech
    "Python", "React", "API", "Github", "Docker", "Linux", "Windows",
    "Java", "TypeScript", "Rust", "Go", "AI", "ChatGPT", "Claude",
    "DeepSeek", "OpenAI", "Anthropic",
    # anime / culture
    "动漫", "新番", "声优", "CV",
]


def need_search(text: str) -> bool:
    """Return True if this message likely needs web search."""
    t = text.strip()
    for kw in HARD_TRIGGERS:
        if kw in t:
            return True
    for kw in SOFT_TRIGGERS:
        if kw in t:
            return True
    return False


# ── Simple search result cache ────────────────────────────────────

_cache: dict[str, tuple[float, str]] = {}
CACHE_TTL = 300  # 5 minutes


def cache_get(query: str) -> str | None:
    entry = _cache.get(query)
    if entry:
        ts, result = entry
        if time.time() - ts < CACHE_TTL:
            return result
        del _cache[query]
    return None


def cache_set(query: str, result: str):
    _cache[query] = (time.time(), result)
    # Keep cache small
    if len(_cache) > 50:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]
