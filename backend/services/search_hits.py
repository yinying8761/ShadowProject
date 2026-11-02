"""搜索结果与原生搜索响应解析（ticket 03 / 06 / spec: native-web-search）。

统一结构 `SearchHit(title, url, snippet, source)`。原生搜索的**形状**按 `search_api`
分叉，业务代码不按厂商名判断（ADR-0011）：这里是一张 parser 表，`parser_for(api)` 给
出对应的解析器，没有就走本地链。

已落地：

- `openai` —— Responses API 的 `web_search`（也认 Chat 搜索模型的 `message` 注解）
- `anthropic` —— 服务端 `web_search` 的 `web_search_result` 块
- `gemini` —— 原生 `generateContent` + `google_search` 的 `groundingMetadata`
- `deepseek` —— Responses 兼容端点的 `web_search`，来源在 `web_search_call`
- 抓取：`anthropic` 的 `web_fetch_tool_result` → `PageText`（DeepSeek 不回传正文，
  所以没有抓取 parser）

**未实现的 `search_api` 一律降级**（搜索是可选增强，不该因为配置缺项或形状不支持而
打断对话 —— 与视觉"未知形状必须报错"正好相反，见 ADR-0011）。

解析必须是**防御性**的：厂商的私有返回形状不受我们控制，缺字段就当没有，绝不像视觉那样
把错误文本当成结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit


@dataclass(frozen=True)
class SearchHit:
    """一条搜索结果（所有后端最终都归到这个形状）。"""

    title: str
    url: str
    snippet: str = ""
    #: 结果从哪儿来：本地后端用后端名（duckduckgo / bing_web / …），原生搜索用它的
    #: **形状名**（openai / anthropic / gemini / deepseek —— 日志里的 `backend=native api=…` 是
    #: 同一件事）。目前只写进日志/评测，`SearchAgent` 只读 title/url/snippet。
    source: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
        }


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """读字段：兼容 SDK 对象与 dict（测试快照用 dict 更贴近真实响应体）。"""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    unique = []
    for hit in hits:
        if not hit.url or hit.url in seen:
            continue
        seen.add(hit.url)
        unique.append(hit)
    return unique


# ── OpenAI: `web_search` → url_citation 注解 ─────────────────────────────────


def _citation(annotation: Any) -> tuple[str, str, int | None, int | None]:
    """取引用的 (url, title, start, end)。

    Responses API 把字段平铺在注解上；Chat Completions 的搜索模型把 url/title/索引
    再包一层 `url_citation` —— 两种都认（spec: Responses/Chat 两种形态）。
    """
    inner = _get(annotation, "url_citation") or {}
    url = _get(annotation, "url") or _get(inner, "url") or ""
    title = _get(annotation, "title") or _get(inner, "title") or url
    start = _get(annotation, "start_index", _get(inner, "start_index"))
    end = _get(annotation, "end_index", _get(inner, "end_index"))
    return url, title, start, end


def _hit_from_annotation(annotation: Any, text: str) -> SearchHit | None:
    url, title, start, end = _citation(annotation)
    if not url:
        return None
    snippet = ""
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        snippet = text[start:end]
    return SearchHit(title, url, snippet, "openai")


def _parse_openai(response: Any) -> list[SearchHit]:
    """先看 Responses 的 `output[].content[].annotations`，再看 Chat 的
    `choices[].message.annotations`；都没有 → 没有命中（调用方会降级）。"""
    hits: list[SearchHit] = []
    for item in _get(response, "output") or []:
        for block in _get(item, "content") or []:
            text = _get(block, "text") or ""
            for annotation in _get(block, "annotations") or []:
                if _get(annotation, "type") != "url_citation":
                    continue
                hit = _hit_from_annotation(annotation, text)
                if hit:
                    hits.append(hit)

    if not hits:
        # Chat Completions（如 gpt-4o-search-preview）：注解挂在 message 上
        for choice in _get(response, "choices") or []:
            message = _get(choice, "message") or {}
            text = _get(message, "content") or ""
            for annotation in _get(message, "annotations") or []:
                if _get(annotation, "type") != "url_citation":
                    continue
                hit = _hit_from_annotation(annotation, text)
                if hit:
                    hits.append(hit)

    return _dedupe(hits)


# ── Anthropic: 服务端 `web_search` → web_search_result 块 ────────────────────


def _parse_anthropic(response: Any) -> list[SearchHit]:
    """从 `content[]` 的 `web_search_tool_result` 块里取结果（结果没有摘要文本）。"""
    hits: list[SearchHit] = []
    for block in _get(response, "content") or []:
        if _get(block, "type") != "web_search_tool_result":
            continue
        for result in _get(block, "content") or []:
            if _get(result, "type") != "web_search_result":
                continue
            url = _get(result, "url") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    _get(result, "title") or url,
                    url,
                    "",
                    "anthropic",
                )
            )
    return _dedupe(hits)


# ── Gemini: `google_search` grounding → groundingMetadata ────────────────────


def _parse_gemini(response: Any) -> list[SearchHit]:
    """原生 `generateContent` + `google_search`：来源在
    `candidates[0].groundingMetadata.groundingChunks[].web`，摘要取同一索引的
    `groundingSupports[].segment.text`（缺失就留空）。"""
    candidates = _get(response, "candidates") or []
    if not candidates:
        return []
    metadata = _get(candidates[0], "groundingMetadata") or {}
    chunks = _get(metadata, "groundingChunks") or []

    snippets: dict[int, str] = {}
    for support in _get(metadata, "groundingSupports") or []:
        segment = _get(support, "segment") or {}
        text = _get(segment, "text") or ""
        for index in _get(support, "groundingChunkIndices") or []:
            if isinstance(index, int) and text:
                snippets.setdefault(index, text)

    hits: list[SearchHit] = []
    for index, chunk in enumerate(chunks):
        web = _get(chunk, "web") or {}
        url = _get(web, "uri") or _get(web, "url") or ""
        if not url:
            continue
        hits.append(
            SearchHit(
                _get(web, "title") or url,
                url,
                snippets.get(index, ""),
                "gemini",
            )
        )
    return _dedupe(hits)


# ── DeepSeek: Responses 的 `web_search_call` → open_page 来源 ───────────────


def _strip_ws_fragment(url: str) -> str:
    """去掉 `#ws_call_id=…`：它是 DeepSeek 内部把抓取关联回调用链的标记，不是锚点。"""
    parts = urlsplit(url)
    if not parts.fragment.startswith("ws_call_id"):
        return url
    return urlunsplit(parts._replace(fragment=""))


def _title_from_url(url: str) -> str:
    """DeepSeek 不给标题：用 `host · 末段路径` 拼一个可读标题（末段做人性化）。"""
    parts = urlsplit(url)
    host = parts.netloc or url
    segment = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    segment = re.sub(r"\.(?:html?|php|aspx?|shtml)$", "", segment, flags=re.IGNORECASE)
    segment = re.sub(r"[-_]+", " ", segment).strip()
    return f"{host} · {segment}" if segment else host


def _parse_deepseek_search(response: Any) -> list[SearchHit]:
    """DeepSeek 的 `/responses` 只把来源放在 `output[]` 的 `web_search_call` 里。

    实测（2026-10-29）：`message.content[].annotations` **永远是空的**（没有
    `url_citation`），真正打开过的页面是 `action.type == "open_page"` 那几条的
    `action.url`；URL 末尾带 `#ws_call_id=…` 的内部标记，去掉才是可引用的地址。
    DeepSeek 不回传标题与摘要，标题由 URL 拼出来，摘要留空。
    """
    hits: list[SearchHit] = []
    for item in _get(response, "output") or []:
        if _get(item, "type") != "web_search_call":
            continue
        action = _get(item, "action") or {}
        if _get(action, "type") != "open_page":
            continue
        raw = _get(action, "url") or ""
        if not isinstance(raw, str) or not raw.strip():
            continue
        url = _strip_ws_fragment(raw.strip())
        hits.append(SearchHit(_title_from_url(url), url, "", "deepseek"))
    return _dedupe(hits)


_PARSERS: dict[str, Callable[[Any], list[SearchHit]]] = {
    "openai": _parse_openai,
    "anthropic": _parse_anthropic,
    "gemini": _parse_gemini,
    "deepseek": _parse_deepseek_search,
}

#: 每种形状由谁来发请求（None = 不走 SDK，直接用 REST）。用于"形状 ↔ 底层 SDK"的一致性
#: 检查：把 `anthropic` 的形状塞进 openai SDK 是配错了，不是能跑的组合。
SHAPE_SDKS: dict[str, str | None] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": None,            # 原生 generateContent 走 REST（不依赖 SDK）
    "deepseek": "openai",      # /responses 走 OpenAI SDK，只是响应形状不同（见上面的 parser）
}

#: 网页正文上限：原生抓取与本地 HTML 抽取共用同一个数（ticket 06）。
MAX_PAGE_TEXT = 6000


@dataclass(frozen=True)
class PageText:
    """一次原生抓取的结果（标题 + 已截断的正文）。"""

    title: str
    text: str


def parser_for(search_api: str | None) -> Callable[[Any], list[SearchHit]] | None:
    """`search_api` → 解析器；`None`/未知/未实现 → None（调用方据此**降级**，不报错）。"""
    return _PARSERS.get(search_api or "")


def parse_search_hits(search_api: str | None, response: Any) -> list[SearchHit]:
    """把一份原生响应解析成统一结构；没有对应 parser 时返回空列表。"""
    parser = parser_for(search_api)
    return parser(response) if parser else []


# ── 原生抓取：按 `search_api` 分叉（ticket 06） ───────────────────────────────


def _parse_anthropic_fetch(response: Any) -> PageText | None:
    """从 `web_fetch_tool_result` 里取标题 + 正文（没有结果就 None）。

    文档形状（`web_fetch_20250910`）：结果块里是
    `{"type": "web_fetch_result", "content": {"type": "document",
      "source": {"type": "text", "data": "<正文>"}, "title": "…"}}` ——
    正文在 **`content.source.data`**，不是 `content.text`。旧的平铺变体也认（防御性）。
    """
    for block in _get(response, "content") or []:
        if _get(block, "type") != "web_fetch_tool_result":
            continue
        result = _get(block, "content")
        if _get(result, "type") != "web_fetch_result":
            continue
        document = _get(result, "content") or {}
        source = _get(document, "source") or {}
        text = (
            _get(source, "data")
            or _get(document, "text")
            or _get(document, "data")
            or ""
        )
        if not isinstance(text, str) or not text.strip():
            return None
        title = (
            _get(document, "title")
            or _get(result, "title")
            or _get(result, "url")
            or ""
        )
        return PageText(title=str(title), text=text[:MAX_PAGE_TEXT])
    return None


_FETCH_PARSERS: dict[str, Callable[[Any], PageText | None]] = {
    "anthropic": _parse_anthropic_fetch,
}


def fetch_parser_for(search_api: str | None) -> Callable[[Any], PageText | None] | None:
    """`search_api` → 原生抓取解析器；没有就 None（回落本地正文抽取）。

    目前只有 Anthropic 有结构化的服务端抓取结果；别的形状没有"抓一个 URL 拿正文"的
    等价能力，直接走本地抽取 —— 这也是 `native_fetch` 能力位与形状分开声明的原因。
    """
    return _FETCH_PARSERS.get(search_api or "")
