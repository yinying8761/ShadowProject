"""搜索结果与原生搜索响应解析（ticket 03 / spec: native-web-search）。

统一结构 `SearchHit(title, url, snippet, source)`。原生搜索的**形状**按 `search_api`
分叉，业务代码不按厂商名判断（ADR-0011）：这里是一张 parser 表，`parser_for(api)` 给
出对应的解析器，没有就走本地链。

先落地两种（ticket 03）：

- `openai` —— Responses API 的 `web_search`，来源在 `url_citation` 注解里
- `anthropic` —— 服务端 `web_search` 工具，来源在 `web_search_result` 块里

`gemini` / `deepseek` 在 ticket 06。**未实现的 `search_api` 一律降级**（搜索是可选增强，
不该因为配置缺项或形状不支持而打断对话 —— 与视觉"未知形状必须报错"正好相反，见 ADR-0011）。

解析必须是**防御性**的：厂商的私有返回形状不受我们控制，缺字段就当没有，绝不像视觉那样
把错误文本当成结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class SearchHit:
    """一条搜索结果（所有后端最终都归到这个形状）。"""

    title: str
    url: str
    snippet: str = ""
    #: 结果从哪儿来：本地后端用后端名（duckduckgo / bing_web / …），原生搜索用它的
    #: **形状名**（openai / anthropic —— 日志里的 `backend=native api=…` 是同一件事）。
    #: 目前只写进日志/评测，`SearchAgent` 只读 title/url/snippet。
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


_PARSERS: dict[str, Callable[[Any], list[SearchHit]]] = {
    "openai": _parse_openai,
    "anthropic": _parse_anthropic,
}


def parser_for(search_api: str | None) -> Callable[[Any], list[SearchHit]] | None:
    """`search_api` → 解析器；`None`/未知/未实现 → None（调用方据此**降级**，不报错）。"""
    return _PARSERS.get(search_api or "")


def parse_search_hits(search_api: str | None, response: Any) -> list[SearchHit]:
    """把一份原生响应解析成统一结构；没有对应 parser 时返回空列表。"""
    parser = parser_for(search_api)
    return parser(response) if parser else []
