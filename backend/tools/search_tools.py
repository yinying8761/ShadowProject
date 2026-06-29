"""
Search tools: fetch_url (read a user-specified URL) and research (web search + LLM summary).

Search backends — tried in order, first success wins:
  - duckduckgo: free, zero-setup, uses ddgs package (needs proxy in China)
  - bing_web:   free, scrapes Bing.com HTML (works in China without proxy)
  - searxng:    self-hosted, configurable via SEARXNG_URL
  - bing:       Microsoft Azure, configurable via BING_API_KEY
"""

import json
import re as _re

import httpx
from bs4 import BeautifulSoup

from config import settings
from services.llm_service import LLMService

_proxy = settings.search_proxy or None
_client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, proxy=_proxy)
_llm = LLMService()

MAX_PAGE_TEXT = 6000
SEARCH_RESULTS = 5


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

def _search_duckduckgo_sync(query: str, limit: int) -> list[dict]:
    from ddgs import DDGS
    results = []
    try:
        with DDGS(proxy=_proxy) as ddgs:
            for r in ddgs.text(query, max_results=limit):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        print(f"[Search] duckduckgo failed: {e}", flush=True)
    return results


async def _search_searxng(query: str, limit: int) -> list[dict]:
    url = settings.searxng_url.rstrip("/")
    try:
        resp = await _client.get(
            f"{url}/search",
            params={"q": query, "format": "json", "categories": "general"},
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in resp.json().get("results", [])[:limit]
        ]
    except Exception as e:
        print(f"[Search] searxng failed: {e}", flush=True)
        return []


async def _search_bing_web(query: str, limit: int) -> list[dict]:
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
                results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        print(f"[Search] bing_web failed: {e}", flush=True)
        return []


async def _search_bing(query: str, limit: int) -> list[dict]:
    key = settings.bing_api_key
    if not key:
        return []
    try:
        resp = await _client.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": limit, "mkt": "zh-CN"},
            headers={"Ocp-Apim-Subscription-Key": key},
        )
        resp.raise_for_status()
        return [
            {"title": r.get("name", ""), "url": r.get("url", ""), "snippet": r.get("snippet", "")}
            for r in resp.json().get("webPages", {}).get("value", [])[:limit]
        ]
    except Exception as e:
        print(f"[Search] bing failed: {e}", flush=True)
        return []


async def _do_search(query: str, limit: int = SEARCH_RESULTS) -> list[dict]:
    """Try backends in order. First success wins."""
    import asyncio

    # Default fallback chain: duckduckgo → bing_web (free, works in China)
    if settings.search_backend == "duckduckgo":
        backends = ["duckduckgo", "bing_web"]
    else:
        backends = [settings.search_backend]

    for backend in backends:
        results = []
        if backend == "duckduckgo":
            results = await asyncio.to_thread(_search_duckduckgo_sync, query, limit)
        elif backend == "searxng":
            results = await _search_searxng(query, limit)
        elif backend == "bing":
            results = await _search_bing(query, limit)
        elif backend == "bing_web":
            results = await _search_bing_web(query, limit)
        else:
            continue
        if results:
            print(f"[Search] backend={backend} results={len(results)}", flush=True)
            return results

    print("[Search] all backends failed", flush=True)
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


async def research(query: str) -> str:
    """Search the web and return a summarized answer with sources."""
    results = await _do_search(query)

    if not results:
        return json.dumps({
            "answer": None,
            "error": "没能搜到相关内容，试试换个说法？",
            "sources": [],
            "confidence": "low",
        }, ensure_ascii=False)

    snippets_lines = []
    for i, r in enumerate(results):
        snippets_lines.append(
            f"[{i+1}] {r['title']}\n    链接: {r['url']}\n    摘要: {r['snippet']}"
        )
    snippets = "\n\n".join(snippets_lines)
    sources = [{"title": r["title"], "url": r["url"]} for r in results]

    summary_prompt = (
        f"用户在聊天中问了这个问题，需要你帮忙查一下：\n{query}\n\n"
        f"以下是搜索结果：\n{snippets}\n\n"
        "请用自然、好懂的语言直接回答这个问题。要求：\n"
        "- 像朋友帮忙查完东西后告诉他一样，口语化，不要百科腔\n"
        "- 不要太长，说清楚就好\n"
        "- 哪里看到的信息就在那里顺手标个 [1] [2]\n"
        "- 如果搜到的东西互相矛盾，诚实说出来\n"
        "- 广告、垃圾内容直接忽略\n"
        "- 最后加个置信度: [confidence: high/medium/low]"
    )

    try:
        raw = await _llm.chat_sync(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是用户的帮手，正在帮用户查点东西。你的语气应该友好、清晰、直接——"
                        "就像你帮朋友查完资料后告诉他结果那样。不要说「根据搜索结果」这种话，"
                        "直接说查到了什么。用户用什么语言问，你就用什么语言答。"
                    ),
                },
                {"role": "user", "content": summary_prompt},
            ],
            max_tokens=800,
            temperature=0.3,
        )
    except Exception as e:
        return json.dumps({
            "answer": None,
            "error": f"LLM 总结失败: {e}",
            "sources": sources,
            "confidence": "low",
        }, ensure_ascii=False)

    confidence = "medium"
    if "confidence: high" in raw.lower() or "置信度：高" in raw:
        confidence = "high"
    elif "confidence: low" in raw.lower() or "置信度：低" in raw:
        confidence = "low"

    answer = _re.sub(r"\[confidence:\s*(high|medium|low)\]", "", raw, flags=_re.IGNORECASE).strip()

    print(f"[Search] query=" + query[:80] + " | confidence=" + confidence + " | summary=" + answer[:200], flush=True)

    return json.dumps({
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
    }, ensure_ascii=False)
