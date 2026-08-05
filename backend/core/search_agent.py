"""
SearchAgent — independent search sub-agent with its own LLM + tools.

Replaces the ``research()`` function in ``tools/search_tools.py``.
Has a cheaper LLM instance, can do multi-step retrieval: search →
pick promising results → fetch_url for details → search again if needed.
"""

from __future__ import annotations

import json
import re

from core.sub_agent import SubAgent
from services.llm_service import LLMService


SEARCH_SYSTEM_PROMPT = """你是搜索助手。用户有一个问题需要你帮忙搜索。

你可以使用以下工具：
- search: 搜索网页，获取标题、链接和摘要
- fetch_url: 打开一个链接，获取完整内容

工作流程：
1. 先 search 用户的问题
2. 看搜索结果，如果某个结果看起来很有用就 fetch_url 打开它看详细内容
3. 如果需要补充信息，可以再 search 一次
4. 最后用自然、清晰的语言总结你找到的信息

要求：
- 像帮朋友查东西一样说话，口语化，不要百科腔
- 引用信息时标明来源 [1] [2]
- 如果搜到的东西互相矛盾，诚实说出来
- 不要太长，说清楚就好
- 广告、垃圾内容直接忽略
- 最后加一行: [confidence: high/medium/low]"""


class SearchAgent(SubAgent):
    """Independent search sub-agent.

    Registered as the handler for the ``research`` tool — from the
    outside it looks like any other tool, but internally it runs its
    own LLM + tool-calling loop.
    """

    def __init__(self, *, llm_service: LLMService | None = None):
        super().__init__(llm_service=llm_service, max_tool_rounds=2)

        # Register search tools — these are what the sub-agent's LLM
        # will call during its tool loop.
        self.register_tool(
            name="search",
            description="搜索网页，返回标题、链接和摘要",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                },
                "required": ["query"],
            },
            handler=self._search_tool,
        )

        self.register_tool(
            name="fetch_url",
            description="打开一个链接，获取网页上的完整文本内容",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要打开的链接",
                    },
                },
                "required": ["url"],
            },
            handler=self._fetch_url_tool,
        )

    # ── Public API ────────────────────────────────────────────────────

    async def run(self, query: str) -> str:
        """Search and summarise.  Returns the same JSON shape as the old
        ``research()`` function for backward compatibility.

        Parameters
        ----------
        query:
            The search query — called by ToolRuntime as
            ``handler(query="...")``.

        Returns
        -------
        str
            JSON: ``{"answer": ..., "sources": [...], "confidence": "..."}``
        """
        result_text = await self._run_tool_loop(
            system_prompt=SEARCH_SYSTEM_PROMPT,
            user_prompt=query,
            temperature=0.3,
        )

        return self._format_result(result_text, query)

    # ── Tool handlers (called by the sub-agent's LLM) ─────────────────

    @staticmethod
    async def _search_tool(query: str) -> str:
        """Search the web and return formatted results."""
        from tools.search_tools import _do_search

        try:
            results = await _do_search(query)
        except Exception as e:
            return json.dumps({"error": f"搜索失败: {e}"}, ensure_ascii=False)

        if not results:
            return json.dumps({"message": "没有搜到相关结果"}, ensure_ascii=False)

        formatted = []
        for i, r in enumerate(results):
            formatted.append(
                f"[{i+1}] {r['title']}\n    链接: {r['url']}\n    摘要: {r['snippet']}"
            )
        return "\n\n".join(formatted)

    @staticmethod
    async def _fetch_url_tool(url: str) -> str:
        """Fetch a URL and return its text content."""
        from tools.search_tools import fetch_url as _fetch

        return await _fetch(url, extract_text=True)

    # ── Result formatting ─────────────────────────────────────────────

    def _format_result(self, raw: str, query: str) -> str:
        """Parse the sub-agent's final response into structured JSON."""
        if not raw or not raw.strip():
            return json.dumps({
                "answer": None,
                "error": "没能搜到相关内容，试试换个说法？",
                "sources": [],
                "confidence": "low",
            }, ensure_ascii=False)

        confidence = "medium"
        if "confidence: high" in raw.lower() or "置信度：高" in raw:
            confidence = "high"
        elif "confidence: low" in raw.lower() or "置信度：低" in raw:
            confidence = "low"

        answer = re.sub(
            r"\[confidence:\s*(high|medium|low)\]",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()

        return json.dumps({
            "answer": answer,
            "sources": [],  # sub-agent doesn't track sources separately
            "confidence": confidence,
        }, ensure_ascii=False)
