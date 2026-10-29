"""搜索后端链（ticket 03 / 06）：顺序计算、失败降级、全链失败不抛、原生抓取。

`search_backend_order` 是纯函数（能力/配置 → 顺序）；降级用假后端验证真实链路：
native 失败 → ddgs → bing_web → …，全失败返回空结果（**不抛错**）。ticket 06 补上
fetch_url 的原生抓取链路与 gemini / deepseek 两个原生分支。
"""

import json
from types import SimpleNamespace

import pytest

from config import settings
from services.llm_config import LLMRuntimeConfig
from services.llm_service import LLMService
from services.search_backends import search_backend_order
from tools import search_tools


class TestBackendOrder:
    def test_native_leads_the_local_chain(self):
        assert search_backend_order(
            prefer_native=True,
            native_available=True,
            configured="duckduckgo",
            has_searxng=False,
            has_bing_key=False,
        ) == ("native", "duckduckgo", "bing_web")

    def test_turning_native_off_returns_the_local_chain_only(self):
        order = search_backend_order(
            prefer_native=False,
            native_available=True,
            configured="duckduckgo",
            has_searxng=False,
            has_bing_key=False,
        )

        assert "native" not in order
        assert order == ("duckduckgo", "bing_web")

    def test_an_incapable_model_skips_native(self):
        order = search_backend_order(
            prefer_native=True,
            native_available=False,
            configured="duckduckgo",
            has_searxng=False,
            has_bing_key=False,
        )

        assert order == ("duckduckgo", "bing_web")

    def test_unconfigured_backends_are_dropped(self):
        order = search_backend_order(
            prefer_native=False,
            native_available=False,
            configured="duckduckgo",
            has_searxng=False,
            has_bing_key=True,
        )

        assert "searxng" not in order          # 没配 SEARXNG_URL
        assert order[-1] == "bing"             # 有 BING_API_KEY 才在链尾

    def test_the_configured_backend_goes_first_among_locals(self):
        order = search_backend_order(
            prefer_native=True,
            native_available=True,
            configured="searxng",
            has_searxng=True,
            has_bing_key=False,
        )

        assert order == ("native", "searxng", "duckduckgo", "bing_web")

    def test_an_unknown_setting_falls_back_to_the_default_order(self):
        order = search_backend_order(
            prefer_native=False,
            native_available=False,
            configured="whatever",
            has_searxng=False,
            has_bing_key=False,
        )

        assert order == ("duckduckgo", "bing_web")


class FakeNativeLLM:
    """假的原生搜索后端（`None` = 这条后端失败）。"""

    def __init__(self, hits=None, *, available=True, api: str = "anthropic"):
        self._hits = hits
        self._available = available
        self._api = api
        self.calls: list[str] = []

    def native_search_available(self) -> bool:
        return self._available

    def native_search_api(self) -> str:
        return self._api

    async def native_search(self, query, limit=5):
        self.calls.append(query)
        return self._hits


def _hit(url: str, source: str) -> dict:
    return {"title": url, "url": url, "snippet": "", "source": source}


def _native_hit(url: str) -> "object":
    """原生后端返回 `SearchHit`（与真 `LLMService.native_search` 同一个契约）。"""
    from services.search_hits import SearchHit

    return SearchHit(url, url, "", "openai")


class TestDegradation:
    """native → ddgs → bing_web；`None` 继续降级，`[]` 是「成功但没结果」。"""

    @pytest.fixture(autouse=True)
    def _settings(self, monkeypatch):
        monkeypatch.setattr(settings, "search_prefer_native", True)
        monkeypatch.setattr(settings, "search_backend", "duckduckgo")
        monkeypatch.setattr(settings, "searxng_url", "")
        monkeypatch.setattr(settings, "bing_api_key", "")

    def _patch(self, monkeypatch, *, native, ddgs=None, bing_web=None):
        calls: list[str] = []

        async def fake_ddgs(query, limit):
            calls.append("duckduckgo")
            return ddgs

        async def fake_bing_web(query, limit):
            calls.append("bing_web")
            return bing_web

        monkeypatch.setattr("services.llm_service.shared_llm", lambda: native)
        # 后端注册表就是"新增一个后端"要改的那一处 —— 测试也在这里换实现
        monkeypatch.setitem(search_tools._BACKEND_RUNNERS, "duckduckgo", fake_ddgs)
        monkeypatch.setitem(search_tools._BACKEND_RUNNERS, "bing_web", fake_bing_web)
        return calls

    @pytest.mark.asyncio
    async def test_native_results_win_and_no_local_backend_runs(self, monkeypatch):
        native = FakeNativeLLM([_native_hit("https://n.example/")])
        calls = self._patch(monkeypatch, native=native, ddgs=[_hit("https://d.example/", "duckduckgo")])

        hits = await search_tools._do_search("python 3.14")

        assert [h["url"] for h in hits] == ["https://n.example/"]
        assert native.calls == ["python 3.14"]
        assert calls == []

    @pytest.mark.asyncio
    async def test_a_failing_native_backend_falls_to_ddgs(self, monkeypatch):
        native = FakeNativeLLM(None)          # 不支持/失败
        calls = self._patch(monkeypatch, native=native, ddgs=[_hit("https://d.example/", "duckduckgo")])

        hits = await search_tools._do_search("python 3.14")

        assert [h["source"] for h in hits] == ["duckduckgo"]
        assert calls == ["duckduckgo"]

    @pytest.mark.asyncio
    async def test_ddgs_failure_falls_to_bing_web(self, monkeypatch):
        calls = self._patch(
            monkeypatch,
            native=FakeNativeLLM(None),
            ddgs=None,                        # 失败
            bing_web=[_hit("https://b.example/", "bing_web")],
        )

        hits = await search_tools._do_search("python 3.14")

        assert [h["source"] for h in hits] == ["bing_web"]
        assert calls == ["duckduckgo", "bing_web"]

    @pytest.mark.asyncio
    async def test_an_empty_result_stops_the_chain(self, monkeypatch):
        """`[]` 是「成功但没结果」，不再往下试（spec 的约定）。"""
        calls = self._patch(
            monkeypatch,
            native=FakeNativeLLM(None),
            ddgs=[],                          # 成功，但没有结果
            bing_web=[_hit("https://b.example/", "bing_web")],
        )

        assert await search_tools._do_search("nothing at all") == []
        assert calls == ["duckduckgo"]

    @pytest.mark.asyncio
    async def test_the_whole_chain_failing_returns_empty_without_raising(self, monkeypatch):
        self._patch(monkeypatch, native=FakeNativeLLM(None), ddgs=None, bing_web=None)

        assert await search_tools._do_search("python 3.14") == []

    @pytest.mark.asyncio
    async def test_the_log_line_names_the_backend_and_the_api(self, monkeypatch, capsys):
        """spec：一行日志说清楚实际后端与 api（排障的第一手证据）。"""
        native = FakeNativeLLM([_native_hit("https://n.example/")])
        self._patch(monkeypatch, native=native)

        await search_tools._do_search("python 3.14")

        out = capsys.readouterr().out
        assert "backend=native" in out
        assert "api=anthropic" in out
        assert "hits=1" in out
        assert "fallbacks=none" in out


class TestSearxngAvailability:
    """默认的 `http://127.0.0.1:8080` 只是占位符：不显式选中就不该进链白等超时。"""

    def test_a_custom_url_counts_as_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_url", "http://search.local:9000")
        monkeypatch.setattr(settings, "search_backend", "duckduckgo")

        assert search_tools._searxng_available() is True

    def test_the_default_url_is_not_tried_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_url", "http://127.0.0.1:8080")
        monkeypatch.setattr(settings, "search_backend", "duckduckgo")

        assert search_tools._searxng_available() is False

    def test_the_default_url_is_tried_when_explicitly_chosen(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_url", "http://127.0.0.1:8080")
        monkeypatch.setattr(settings, "search_backend", "searxng")

        assert search_tools._searxng_available() is True

    def test_an_empty_url_is_unconfigured(self, monkeypatch):
        monkeypatch.setattr(settings, "searxng_url", "")

        assert search_tools._searxng_available() is False


class TestNativeSearchClient:
    """`LLMService.native_search`：形状按 `search_api` 发，失败一律 None（降级用）。"""

    ANTHROPIC_RESPONSE = {
        "content": [
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://docs.python.org/3.14/whatsnew/3.14.html",
                        "title": "What's New In Python 3.14",
                    }
                ],
            }
        ]
    }

    OPENAI_RESPONSE = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Python 3.14 released",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://www.python.org/downloads/",
                                "title": "Download Python",
                                "start_index": 0,
                                "end_index": 6,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    @staticmethod
    def _llm(provider: str, client, **runtime_kwargs):
        from services.llm_config import LLMRuntimeConfig
        from services.llm_service import LLMService

        sdk = "anthropic" if provider == "anthropic" else "openai"
        return LLMService(
            clients={sdk: client},
            runtime_config=LLMRuntimeConfig(provider=provider, **runtime_kwargs),
        )

    @pytest.mark.asyncio
    async def test_anthropic_server_side_search_becomes_hits(self):
        class Messages:
            async def create(self, **kwargs):
                assert kwargs["tools"][0]["type"] == "web_search_20250305"
                return TestNativeSearchClient.ANTHROPIC_RESPONSE

        llm = self._llm("anthropic", SimpleNamespace(messages=Messages()))

        hits = await llm.native_search("python 3.14")

        assert hits is not None
        assert hits[0].url == "https://docs.python.org/3.14/whatsnew/3.14.html"
        assert hits[0].source == "anthropic"

    @pytest.mark.asyncio
    async def test_openai_responses_api_becomes_hits(self):
        class Responses:
            async def create(self, **kwargs):
                assert kwargs["tools"] == [{"type": "web_search"}]
                return TestNativeSearchClient.OPENAI_RESPONSE

        llm = self._llm("openai", SimpleNamespace(responses=Responses()))

        hits = await llm.native_search("python 3.14")

        assert [h.url for h in hits] == ["https://www.python.org/downloads/"]
        assert hits[0].snippet == "Python"

    @pytest.mark.asyncio
    async def test_a_failing_provider_returns_none(self):
        class Messages:
            async def create(self, **kwargs):
                raise RuntimeError("429")

        llm = self._llm("anthropic", SimpleNamespace(messages=Messages()))

        assert await llm.native_search("python") is None

    @pytest.mark.asyncio
    async def test_a_response_without_usable_hits_degrades(self):
        """解析不出带来源的命中 → None（让本地链去补），不是「搜过了没有」。"""
        class Messages:
            async def create(self, **kwargs):
                return {"content": [{"type": "text", "text": "没有引用"}]}

        llm = self._llm("anthropic", SimpleNamespace(messages=Messages()))

        assert await llm.native_search("python") is None

    @pytest.mark.asyncio
    async def test_a_model_without_the_capability_never_calls_the_client(self):
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("不该发起原生搜索请求")

        llm = self._llm("custom", Boom())

        assert await llm.native_search("python") is None

    @pytest.mark.asyncio
    async def test_a_shape_that_does_not_match_the_sdk_is_skipped(self):
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("不该发起原生搜索请求")

        llm = self._llm(
            "custom",
            Boom(),
            capability_overrides={"custom:": {"native_search": True, "search_api": "anthropic"}},
        )

        assert await llm.native_search("python") is None


# ── ticket 06：gemini / deepseek 分支 ───────────────────────────────────────

GEMINI_GROUNDING = {
    "candidates": [
        {
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"uri": "https://www.python.org/downloads/", "title": "Download Python"}}
                ],
                "groundingSupports": [],
            }
        }
    ]
}

class TestNativeSearchBranches:
    @pytest.mark.asyncio
    async def test_gemini_uses_native_grounding_over_rest(self):
        class FakeGemini:
            def __init__(self):
                self.calls: list[tuple[str, dict]] = []

            async def generate(self, model, payload):
                self.calls.append((model, payload))
                return GEMINI_GROUNDING

        gemini = FakeGemini()
        llm = LLMService(
            clients={"gemini": gemini},
            runtime_config=LLMRuntimeConfig(
                provider="gemini",
                model="gemini-2.5-flash",
                capability_overrides={
                    "gemini:gemini-2.5-flash": {
                        "native_search": True,
                        "search_api": "gemini",
                    }
                },
            ),
        )

        hits = await llm.native_search("python 3.14")

        assert gemini.calls[0][0] == "gemini-2.5-flash"
        assert gemini.calls[0][1]["tools"] == [{"google_search": {}}]
        assert [h.source for h in hits] == ["gemini"]

    @pytest.mark.asyncio
    async def test_gemini_degrades_when_the_rest_call_fails(self):
        class BoomGemini:
            async def generate(self, model, payload):
                raise RuntimeError("404")

        llm = LLMService(
            clients={"gemini": BoomGemini()},
            runtime_config=LLMRuntimeConfig(
                provider="gemini",
                model="",
                capability_overrides={
                    "gemini:": {"native_search": True, "search_api": "gemini"}
                },
            ),
        )

        assert await llm.native_search("python") is None

    @pytest.mark.asyncio
    async def test_deepseek_has_no_native_branch_so_it_degrades(self):
        """DeepSeek 的搜索响应形状官方没公开：不发请求、直接降级（不编一个假形状）。"""
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("DeepSeek 的原生分支还没实现，不该发请求")

        llm = LLMService(
            clients={"openai": Boom()},
            runtime_config=LLMRuntimeConfig(provider="deepseek"),
        )

        assert llm.native_search_available() is False
        assert await llm.native_search("python") is None


class TestGeminiRestUrl:
    """原生 Gemini 的 URL 由缝拼装：预设的 `/v1beta/openai/` 要还原成 `/v1beta/models/…`。"""

    def test_the_preset_compat_url_is_reduced_to_the_native_one(self):
        from services.gemini_rest import GeminiRestClient

        client = GeminiRestClient(
            api_key="k",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

        assert client.generate_url("gemini-2.5-flash") == (
            "https://generativelanguage.googleapis.com/v1beta"
            "/models/gemini-2.5-flash:generateContent"
        )

    def test_an_explicit_native_url_is_used_as_is(self):
        from services.gemini_rest import GeminiRestClient

        client = GeminiRestClient(
            api_key="k", base_url="https://proxy.example/v1beta/"
        )

        assert client.generate_url("m") == "https://proxy.example/v1beta/models/m:generateContent"

    def test_the_default_host_is_used_when_none_is_configured(self):
        from config import GEMINI_NATIVE_BASE_URL
        from services.gemini_rest import GeminiRestClient

        assert GeminiRestClient(api_key="k").generate_url("m") == (
            f"{GEMINI_NATIVE_BASE_URL}/models/m:generateContent"
        )


# ── ticket 06：fetch_url 的原生抓取链路 ────────────────────────────────────

FETCH_RESPONSE = {
    "content": [
        {
            "type": "web_fetch_tool_result",
            "content": {
                "type": "web_fetch_result",
                "url": "https://example.com/post",
                "content": {
                    "type": "document",
                    "title": "Example Post",
                    "text": "这是正文。",
                },
            },
        }
    ]
}


class TestNativeFetchClient:
    @pytest.mark.asyncio
    async def test_anthropic_fetch_returns_page_text(self):
        class Messages:
            async def create(self, **kwargs):
                assert kwargs["tools"][0]["type"] == "web_fetch_20250910"
                return FETCH_RESPONSE

        llm = LLMService(
            clients={"anthropic": SimpleNamespace(messages=Messages())},
            runtime_config=LLMRuntimeConfig(provider="anthropic"),
        )

        page = await llm.native_fetch("https://example.com/post")

        assert page.title == "Example Post"
        assert page.text == "这是正文。"

    @pytest.mark.asyncio
    async def test_a_failing_provider_returns_none(self):
        class Messages:
            async def create(self, **kwargs):
                raise RuntimeError("403")

        llm = LLMService(
            clients={"anthropic": SimpleNamespace(messages=Messages())},
            runtime_config=LLMRuntimeConfig(provider="anthropic"),
        )

        assert await llm.native_fetch("https://example.com/post") is None

    @pytest.mark.asyncio
    async def test_without_the_capability_the_client_is_never_called(self):
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("不该发起原生抓取")

        llm = LLMService(clients={"openai": Boom()}, runtime_config=LLMRuntimeConfig(provider="custom"))

        assert await llm.native_fetch("https://example.com/post") is None

    @pytest.mark.asyncio
    async def test_a_shape_without_fetch_support_is_skipped(self):
        """openai 有原生搜索、但没有"抓一个 URL"的等价能力 → 不尝试，回落本地抽取。"""
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("不该发起原生抓取")

        llm = LLMService(clients={"openai": Boom()}, runtime_config=LLMRuntimeConfig(provider="openai"))

        assert llm.native_fetch_api() is None
        assert await llm.native_fetch("https://example.com/post") is None


class _FakeResponse:
    def __init__(self, text: str, content_type: str = "text/html"):
        self.text = text
        self.headers = {"content-type": content_type}
        self.url = "https://example.com/post"

    def raise_for_status(self) -> None:
        return None


class _FakeFetcher:
    """假的原生抓取（`native_fetch_api` 给日志用）。"""

    def __init__(self, page):
        self._page = page
        self.calls: list[str] = []

    def native_fetch_api(self):
        return "anthropic"

    async def native_fetch(self, url):
        self.calls.append(url)
        return self._page


class TestFetchUrlChain:
    @pytest.mark.asyncio
    async def test_native_fetch_wins_and_html_is_never_fetched(self, monkeypatch):
        from services.search_hits import PageText

        fetcher = _FakeFetcher(PageText("Example Post", "原生正文"))
        monkeypatch.setattr("services.llm_service.shared_llm", lambda: fetcher)

        async def boom(url):
            raise AssertionError("原生成功时不该抓 HTML")

        monkeypatch.setattr(search_tools._client, "get", boom)

        payload = json.loads(await search_tools.fetch_url("https://example.com/post"))

        assert payload == {
            "url": "https://example.com/post",
            "text": "原生正文",
            "title": "Example Post",
        }
        assert fetcher.calls == ["https://example.com/post"]

    @pytest.mark.asyncio
    async def test_a_failed_native_fetch_falls_back_to_extraction(self, monkeypatch):
        fetcher = _FakeFetcher(None)          # 原生没拿到 → 回落
        monkeypatch.setattr("services.llm_service.shared_llm", lambda: fetcher)

        async def get(url):
            return _FakeResponse(
                "<html><head><title>Page</title></head><body><p>Hello 世界</p></body></html>"
            )

        monkeypatch.setattr(search_tools._client, "get", get)

        payload = json.loads(await search_tools.fetch_url("https://example.com/post"))

        assert "Hello 世界" in payload["text"]
        assert payload["title"] == "Page"
        assert fetcher.calls == ["https://example.com/post"]

    @pytest.mark.asyncio
    async def test_both_paths_failing_returns_an_error_envelope(self, monkeypatch):
        fetcher = _FakeFetcher(None)
        monkeypatch.setattr("services.llm_service.shared_llm", lambda: fetcher)

        async def get(url):
            raise RuntimeError("boom")

        monkeypatch.setattr(search_tools._client, "get", get)

        payload = json.loads(await search_tools.fetch_url("https://example.com/post"))

        assert "error" in payload
        assert "boom" in payload["error"]

    @pytest.mark.asyncio
    async def test_extract_text_false_skips_native_fetch(self, monkeypatch):
        fetcher = _FakeFetcher(None)
        monkeypatch.setattr("services.llm_service.shared_llm", lambda: fetcher)

        async def get(url):
            return _FakeResponse("raw body", content_type="text/plain")

        monkeypatch.setattr(search_tools._client, "get", get)

        payload = json.loads(
            await search_tools.fetch_url("https://example.com/post", extract_text=False)
        )

        assert payload["text"] == "raw body"
        assert fetcher.calls == []           # 要原始文本时不走原生抓取
