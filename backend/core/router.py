"""
Search intent router — decides whether a user message needs web search.
Advisory only: keyword matching, no LLM call (ADR-0003).
"""

# ── Keywords ──────────────────────────────────────────────────────

HARD_TRIGGERS: list[str] = [
    # price
    "多少钱", "价格", "售价", "报价",
    # time-sensitive — 只保留强意图的完整短语。"今天/最新/最近/刚刚/日期/
    # 时间/发布" 这类日常高频词单独出现时误触发率过高（"今天好累啊"、
    # "刚刚测试一下"、"你这个版本真好玩"），一律移除；漏掉的搜索需求由
    # Agent 自主调用 research 工具兜底。
    "今天天气", "今天几号", "现在几点", "几点了", "最新消息", "刚刚发布",
    "什么时候出", "什么时候发布", "什么时候更新", "哪一年",
    "新闻",
    # fact lookup
    "是什么意思", "什么是",
    # hardware / products
    "跑分", "评测",
    # software
    "官网", "下载", "安装", "怎么用",
    # finance
    "股票", "汇率", "涨了", "跌了", "行情",
    # location
    "地址", "怎么去", "营业时间",
    # game
    "新版本", "更新内容", "卡池",
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
