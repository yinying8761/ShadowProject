"""原生搜索响应 → 统一结构（ticket 03 / spec S2 + S3）。

每份固定响应体都是**真实的形状**（OpenAI Responses 的 `url_citation` 注解 /
Anthropic 的 `web_search_tool_result` 块），解析器必须防御性地处理缺字段与空结果 ——
厂商的私有契约不受我们控制，解析失败只能降级，不能抛错，更不能把错误当结果。
"""

from types import SimpleNamespace

import pytest

from services.search_hits import SearchHit, parse_search_hits, parser_for

#: OpenAI Responses `web_search` 的真实形状（含 url_citation 注解与索引）
OPENAI_RESPONSE = {
    "id": "resp_abc",
    "output": [
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "Python 3.14 已经发布，可以从官网下载。",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://www.python.org/downloads/",
                            "title": "Download Python",
                            "start_index": 0,
                            "end_index": 11,
                        }
                    ],
                }
            ],
        }
    ],
}

#: Anthropic 服务端 `web_search` 的真实形状（server_tool_use + result 块）
ANTHROPIC_RESPONSE = {
    "id": "msg_abc",
    "content": [
        {"type": "text", "text": "我先查一下。"},
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "python 3.14"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://docs.python.org/3.14/whatsnew/3.14.html",
                    "title": "What's New In Python 3.14",
                    "encrypted_content": "…",
                    "page_age": "2 days",
                },
                {
                    "type": "web_search_result",
                    "url": "https://www.python.org/downloads/",
                    "title": "Download Python",
                    "encrypted_content": "…",
                    "page_age": "2 days",
                },
            ],
        },
    ],
}


class TestOpenAIShape:
    def test_url_citations_become_hits_with_the_quoted_snippet(self):
        hits = parse_search_hits("openai", OPENAI_RESPONSE)

        assert hits == [
            SearchHit(
                "Download Python",
                "https://www.python.org/downloads/",
                "Python 3.14",          # start/end 索引切出的那段原文
                "openai",
            )
        ]

    def test_every_hit_carries_a_source_url(self):
        """用户要的是「答案带出处」—— 没有 url 的注解一律丢弃。"""
        assert all(hit.url for hit in parse_search_hits("openai", OPENAI_RESPONSE))

    def test_duplicate_urls_collapse(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "abc",
                            "annotations": [
                                {"type": "url_citation", "url": "https://a.example/", "title": "A"},
                                {"type": "url_citation", "url": "https://a.example/", "title": "A again"},
                                {"type": "url_citation", "url": "https://b.example/", "title": "B"},
                            ],
                        }
                    ],
                }
            ]
        }

        assert [h.url for h in parse_search_hits("openai", response)] == [
            "https://a.example/",
            "https://b.example/",
        ]

    def test_other_annotation_types_are_ignored(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "x",
                            "annotations": [
                                {"type": "file_citation", "url": "https://c.example/"},
                                {"type": "url_citation", "url": "https://d.example/", "title": "D"},
                            ],
                        }
                    ],
                }
            ]
        }

        assert [h.url for h in parse_search_hits("openai", response)] == ["https://d.example/"]

    @pytest.mark.parametrize(
        "response",
        [
            {},
            {"output": []},
            {"output": [{"type": "message", "content": []}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "no annotations"}]}]},
        ],
    )
    def test_empty_shapes_yield_no_hits(self, response):
        assert parse_search_hits("openai", response) == []

    def test_sdk_objects_and_dicts_both_work(self):
        """真实响应是 SDK 对象，测试快照是 dict —— 解析器两种都要认。"""
        obj = SimpleNamespace(
            output=[
                SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            text="hello world",
                            annotations=[
                                SimpleNamespace(
                                    type="url_citation",
                                    url="https://e.example/",
                                    title="E",
                                    start_index=0,
                                    end_index=5,
                                )
                            ],
                        )
                    ]
                )
            ]
        )

        assert parse_search_hits("openai", obj) == [
            SearchHit("E", "https://e.example/", "hello", "openai")
        ]


class TestOpenAIChatShape:
    """spec 说 Responses/Chat 两种形态都要认：Chat 的搜索模型把注解挂在 message 上。"""

    CHAT_RESPONSE = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Python 3.14 已发布，见官网。",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://www.python.org/downloads/",
                                "title": "Download Python",
                                "start_index": 0,
                                "end_index": 6,
                            },
                        }
                    ],
                }
            }
        ]
    }

    def test_chat_completions_citations_become_hits(self):
        hits = parse_search_hits("openai", self.CHAT_RESPONSE)

        assert hits == [
            SearchHit("Download Python", "https://www.python.org/downloads/", "Python", "openai")
        ]

    def test_responses_shape_still_wins_when_present(self):
        assert parse_search_hits("openai", OPENAI_RESPONSE)[0].title == "Download Python"


class TestAnthropicShape:
    def test_result_blocks_become_hits(self):
        hits = parse_search_hits("anthropic", ANTHROPIC_RESPONSE)

        assert [h.url for h in hits] == [
            "https://docs.python.org/3.14/whatsnew/3.14.html",
            "https://www.python.org/downloads/",
        ]
        assert hits[0].title == "What's New In Python 3.14"
        assert hits[0].source == "anthropic"
        assert hits[0].snippet == ""      # 结果块本身不带摘要文本

    def test_an_error_result_block_yields_no_hits(self):
        """`content` 是错误对象（如 max_uses_exceeded）时不能当成结果。"""
        response = {
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"},
                }
            ]
        }

        assert parse_search_hits("anthropic", response) == []

    def test_plain_text_answers_yield_no_hits(self):
        assert parse_search_hits("anthropic", {"content": [{"type": "text", "text": "答案"}]}) == []


class TestAllHitsAreUniform:
    def test_both_shapes_produce_dicts_with_the_same_keys(self):
        keys = {"title", "url", "snippet", "source"}
        for api, response in (("openai", OPENAI_RESPONSE), ("anthropic", ANTHROPIC_RESPONSE)):
            hits = parse_search_hits(api, response)
            assert hits and all(set(h.as_dict()) == keys for h in hits)


class TestDispatch:
    @pytest.mark.parametrize("api", ["openai", "anthropic"])
    def test_known_apis_have_a_parser(self, api):
        assert parser_for(api) is not None

    @pytest.mark.parametrize("api", [None, "", "none", "gemini", "deepseek", "nonsense"])
    def test_unknown_or_missing_api_does_not_try_native(self, api):
        """搜索与视觉相反：不认识的形状**降级**（不抛错、不尝试原生）。"""
        assert parser_for(api) is None
        assert parse_search_hits(api, OPENAI_RESPONSE) == []
