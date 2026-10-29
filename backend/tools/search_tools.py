"""
Web search backends (driven by SearchAgent via _do_search) plus fetch_url
(read a user-specified URL).

Backend chain (ticket 03 / spec: native-web-search) — first success wins:
  - native:     provider-native web search (OpenAI Responses `web_search`,
                Anthropic server-side `web_search`); needs native_search=True
                and a parser for the model's `search_api`. `None` → fall through.
  - duckduckgo: ddgs>=9 metasearch. Engine list is pinned to China-accessible
                engines (bing/mojeek) — see _DDGS_ENGINES.
  - bing_web:   free, scrapes Bing.com HTML (works in China without proxy)
  - searxng:    self-hosted, configurable via SEARXNG_URL
  - bing:       Microsoft Azure, configurable via BING_API_KEY

A backend returns `[]` for "no results but it worked" and `None` for "failed" —
only `None` continues the chain (spec: `[]` is a successful empty answer).
"""

import json
import re as _re

import httpx
from bs4 import BeautifulSoup

from config import SEARXNG_DEFAULT_URL, settings
from services.search_backends import search_backend_order
from services.search_hits import SearchHit

_proxy = settings.search_proxy or None
_client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, proxy=_proxy)

MAX_PAGE_TEXT = 6000
SEARCH_RESULTS = 5


def _local_hit(title: str, url: str, snippet: str, source: str) -> dict:
    """本地抓取的结果也走**同一个**结构（`SearchHit`），别处不再手写这四个键。"""
    return SearchHit(title, url, snippet, source).as_dict()


# ── HTML → text ────────────────────────────────────────────────────────────

def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "iframe"]):
        tag.decompose()
    body = soup.find("body")
    text = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    text = _re.sub(r"[ \t]{3,}", "  ", text)
    return text[:MAX_PAGE_TEXT]


def _extract_title(html: str) -> str:
    try:
        t = BeautifulSoup(html, "lxml").find("title")
        return t.get_text(strip=True) if t else ""
    except Exception:
        return ""


# ── Search backends ─────────────────────────────────────────────────────────

# ddgs>=9 "auto" fans out to ~11 engines, but from China without a proxy most
# are blocked (google/brave/duckduckgo/yandex/wikipedia) or actively reset bot
# requests (yahoo, os error 10054). Pin to engines reachable from China (bing,
# mojeek); add duckduckgo back when a proxy is configured.
_DDGS_ENGINES = "duckduckgo,bing,mojeek" if _proxy else "bing,mojeek"
_DDGS_TIMEOUT = 6  # per-engine timeout (seconds)

def _search_duckduckgo_sync(query: str, limit: int) -> list[dict] | None:
    """`None` = 这个后端失败了（继续下一个）；`[]` = 成功但没结果。"""
    from ddgs import DDGS
    results = []
    try:
        with DDGS(proxy=_proxy, timeout=_DDGS_TIMEOUT) as ddgs:
            for r in ddgs.text(query, max_results=limit, backend=_DDGS_ENGINES):
                results.append(
                    _local_hit(
                        r.get("title", ""),
                        r.get("href", ""),
                        r.get("body", ""),
                        "duckduckgo",
                    )
                )
    except Exception as e:
        print(f"[Search] duckduckgo failed ({_DDGS_ENGINES}): {e}", flush=True)
        return None
    return results


async def _search_duckduckgo(query: str, limit: int) -> list[dict] | None:
    """ddgs 是同步库：丢线程池里跑，别堵事件循环。"""
    import asyncio

    return await asyncio.to_thread(_search_duckduckgo_sync, query, limit)


async def _search_searxng(query: str, limit: int) -> list[dict] | None:
    url = settings.searxng_url.rstrip("/")
    try:
        resp = await _client.get(
            f"{url}/search",
            params={"q": query, "format": "json", "categories": "general"},
        )
        resp.raise_for_status()
        return [
            _local_hit(
                r.get("title", ""), r.get("url", ""), r.get("content", ""), "searxng"
            )
            for r in resp.json().get("results", [])[:limit]
        ]
    except Exception as e:
        print(f"[Search] searxng failed: {e}", flush=True)
        return None


async def _search_bing_web(query: str, limit: int) -> list[dict] | None:
    """Scrape Bing.com HTML search results. Works without API key, accessible in China."""
    try:
        resp = await _client.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "zh-Hans"},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for li in soup.select("li.b_algo"):
            a_tag = li.select_one("h2 a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            url = a_tag.get("href", "")
            snippet_tag = li.select_one(".b_caption p, .b_lineclamp2")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if title and url:
                results.append(_local_hit(title, url, snippet, "bing_web"))
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        print(f"[Search] bing_web failed: {e}", flush=True)
        return None


async def _search_bing(query: str, limit: int) -> list[dict] | None:
    key = settings.bing_api_key
    if not key:
        return None
    try:
        resp = await _client.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": limit, "mkt": "zh-CN"},
            headers={"Ocp-Apim-Subscription-Key": key},
        )
        resp.raise_for_status()
        return [
            _local_hit(
                r.get("name", ""), r.get("url", ""), r.get("snippet", ""), "bing"
            )
            for r in resp.json().get("webPages", {}).get("value", [])[:limit]
        ]
    except Exception as e:
        print(f"[Search] bing failed: {e}", flush=True)
        return None


def _searxng_available() -> bool:
    """searxng 只有在"用户真配了"时才进链。

    默认值（`SEARXNG_DEFAULT_URL`）是个占位符：留着它却去连一个没启动的实例，会让整条
    链在别人失败后白等一次超时。所以默认地址只有在用户显式选中 searxng 时才试。
    """
    url = (settings.searxng_url or "").strip().rstrip("/")
    if not url:
        return False
    if url != SEARXNG_DEFAULT_URL:
        return True
    return (settings.search_backend or "").strip().lower() == "searxng"


#: 本地后端 → 调用（一个后端一条，新增后端只需要在这里加一行）
_BACKEND_RUNNERS = {
    "duckduckgo": _search_duckduckgo,
    "bing_web": _search_bing_web,
    "searxng": _search_searxng,
    "bing": _search_bing,
}


def _native_search_available() -> bool:
    """原生搜索此刻能不能用（规则在 LLMService，与真正调用同一处）。"""
    try:
        from services.llm_service import shared_llm

        return shared_llm().native_search_available()
    except Exception as e:      # 算链顺序本身绝不能把搜索打断
        print(f"[Search] native availability check failed: {type(e).__name__}: {e}", flush=True)
        return False


async def _run_backend(backend: str, query: str, limit: int) -> tuple[list[dict] | None, str]:
    """跑一个后端：`(hits, 失败原因)`；hits 为 `None` 表示失败（继续下一个）。"""
    try:
        if backend == "native":
            from services.llm_service import shared_llm

            hits = await shared_llm().native_search(query, limit)
            if hits is None:
                return None, "unsupported or failed"
            return [h.as_dict() for h in hits], ""
        runner = _BACKEND_RUNNERS.get(backend)
        if runner is None:
            return None, "unknown backend"
        hits = await runner(query, limit)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    return (hits, "") if hits is not None else (None, "failed")


async def _do_search(query: str, limit: int = SEARCH_RESULTS) -> list[dict]:
    """按后端链依次尝试：原生 → 本地抓取，第一个成功即返回（ticket 03）。

    一个后端返回 `[]` 视为「成功但没结果」——停在这里，不再往下试（spec 的约定）；
    只有 `None`（失败/不支持）才继续下一个。全链失败返回 `[]`，**不抛错**。
    """
    order = search_backend_order(
        prefer_native=settings.search_prefer_native,
        native_available=_native_search_available(),
        configured=settings.search_backend,
        has_searxng=_searxng_available(),
        has_bing_key=bool(settings.bing_api_key),
    )

    failures: list[str] = []
    for backend in order:
        hits, error = await _run_backend(backend, query, limit)
        if hits is None:
            failures.append(f"{backend}: {error}")
            continue
        detail = ""
        if backend == "native":
            from services.llm_service import shared_llm

            detail = f" api={shared_llm().native_search_api()}"
        print(
            f"[Search] backend={backend}{detail} query={query!r} "
            f"hits={len(hits)} fallbacks={', '.join(failures) if failures else 'none'}",
            flush=True,
        )
        return hits

    print(
        f"[Search] no backend produced results: {', '.join(failures) or 'none available'}",
        flush=True,
    )
    return []


# ── Public tools ────────────────────────────────────────────────────────────

async def fetch_url(url: str, extract_text: bool = True) -> str:
    """Fetch a URL and return extracted text content."""
    try:
        resp = await _client.get(url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "text/html" in ct and extract_text:
            text = _extract_text(resp.text)
            return json.dumps(
                {"url": str(resp.url), "text": text, "title": _extract_title(resp.text)},
                ensure_ascii=False,
            )
        return json.dumps(
            {"url": str(resp.url), "text": resp.text[:MAX_PAGE_TEXT], "content_type": ct},
            ensure_ascii=False,
        )
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}"})
    except Exception as e:
        return json.dumps({"error": f"Fetch failed: {type(e).__name__}: {e}"})
