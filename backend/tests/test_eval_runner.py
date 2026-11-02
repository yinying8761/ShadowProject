"""评测评分与报告渲染（ticket 04 / spec S1 + S2）：纯函数，不碰模型 / DB / 网络。

评分规则（工具集合、禁用工具、参数子串、回答要点、must_cite、调用预算、运行错误）与
报告（按类别汇总、成本列、失败最小复现、基线 diff 段）都在这里被 pytest 钉住 ——
这是 v2 对旧 spec「评测框架不依赖 pytest」的修正。
"""

from datetime import datetime, timezone

import pytest

from eval.report import CaseReport, category_rows, render_report
from eval.scoring import CaseTrace, cited_urls, score_case
from eval.test_cases import EVAL_CASES, EvalCase


def _case(**overrides) -> EvalCase:
    base = {"id": "Tx", "query": "q", "description": "d"}
    base.update(overrides)
    return EvalCase(**base)


def _score(*, reply: str = "", trace: CaseTrace | None = None, **case_kwargs):
    return score_case(_case(**case_kwargs), trace or CaseTrace(reply=reply))


class TestScoringAnswer:
    def test_or_only_hit(self):
        assert _score(reply="我是你的小伙伴", answer_should_contain_any=["伙伴", "朋友", "助手"]).passed

    def test_or_only_miss(self):
        verdict = _score(reply="我是 AI", answer_should_contain_any=["伙伴", "朋友", "助手"])

        assert not verdict.passed
        assert "answer" in verdict.reasons

    def test_and_or_combo(self):
        assert _score(
            reply="水由氢和氧组成，我是你的朋友",
            answer_should_contain=["氢", "氧"],
            answer_should_contain_any=["伙伴", "朋友"],
        ).passed
        # AND 满足但 OR 一个都没有 → 挂
        assert not _score(
            reply="水由氢和氧组成",
            answer_should_contain=["氢", "氧"],
            answer_should_contain_any=["伙伴", "朋友"],
        ).passed

    def test_empty_or_is_always_true(self):
        assert _score(reply="水由氢和氧组成", answer_should_contain=["氢"]).passed
        assert not _score(reply="水由碳组成", answer_should_contain=["氢"]).passed

    def test_not_still_enforced(self):
        assert not _score(
            reply="我是你的伙伴 error",
            answer_should_contain_any=["伙伴"],
            answer_should_not_contain=["error"],
        ).passed

    def test_no_answer_assertions_requires_a_non_empty_reply(self):
        assert _score(reply="随便说点什么").passed
        assert not _score(reply="   ").passed


class TestScoringTools:
    def test_tool_set_ignores_order_by_default(self):
        assert _score(
            expected_tools=["list_directory", "read_file"],
            trace=CaseTrace(tools=("read_file", "list_directory"), reply="ok"),
        ).passed

    def test_ordered_cases_require_the_sequence(self):
        case_kwargs = dict(expected_tools=["list_directory", "read_file"], ordered=True)
        assert _score(
            **case_kwargs,
            trace=CaseTrace(tools=("list_directory", "read_file"), reply="ok"),
        ).passed
        assert not _score(
            **case_kwargs,
            trace=CaseTrace(tools=("read_file", "list_directory"), reply="ok"),
        ).passed

    def test_an_unexpected_tool_fails(self):
        verdict = _score(
            expected_tools=["read_file"],
            trace=CaseTrace(tools=("read_file", "write_file"), reply="ok"),
        )

        assert not verdict.passed
        assert "tools" in verdict.reasons

    def test_calling_a_tool_when_none_is_expected_fails(self):
        assert not _score(
            expected_tools=[], trace=CaseTrace(tools=("research",), reply="ok")
        ).passed

    def test_a_forbidden_tool_fails_even_when_the_expected_set_matches(self):
        verdict = _score(
            expected_tools=["read_file"],
            forbidden_tools=["write_file"],
            trace=CaseTrace(tools=("read_file", "write_file"), reply="ok"),
        )

        assert not verdict.passed
        assert "forbidden" in verdict.reasons and "tools" in verdict.reasons


class TestScoringArguments:
    def test_argument_substrings_must_appear_in_that_tools_args(self):
        args = {"read_file": {"path": "F:/ShadowProject/README.md"}}

        assert _score(
            expected_tools=["read_file"],
            expected_args_contain={"read_file": ["README.md"]},
            trace=CaseTrace(tools=("read_file",), args=args, reply="ok"),
        ).passed

        verdict = _score(
            expected_tools=["read_file"],
            expected_args_contain={"read_file": ["NOTES.md"]},
            trace=CaseTrace(tools=("read_file",), args=args, reply="ok"),
        )
        assert not verdict.passed and "arguments" in verdict.reasons

    def test_args_of_the_wrong_tool_do_not_count(self):
        assert not _score(
            expected_tools=["read_file"],
            expected_args_contain={"read_file": ["README.md"]},
            trace=CaseTrace(
                tools=("read_file",),
                args={"list_directory": {"path": "README.md"}},
                reply="ok",
            ),
        ).passed


class TestScoringCitation:
    def test_must_cite_requires_a_source_url(self):
        assert not _score(
            reply="Python 3.14 已经发布。", must_cite=True
        ).passed
        assert _score(
            reply="Python 3.14 已经发布，见 https://www.python.org/downloads/ 。",
            must_cite=True,
        ).passed

    def test_must_cite_is_off_by_default(self):
        assert _score(reply="没有链接也没关系").passed

    def test_cited_urls_are_deduped_and_trim_trailing_punctuation(self):
        reply = "见 https://a.example/x，以及 https://b.example/ 。重复 https://a.example/x"

        assert cited_urls(reply) == ["https://a.example/x", "https://b.example/"]


class TestScoringRobustness:
    """评分是纯函数，但输入来自模型与假工具 —— 畸形输入不能让评测崩掉。"""

    def test_a_none_reply_does_not_crash(self):
        assert not score_case(_case(answer_should_contain=["氢"]), CaseTrace(reply=None)).passed

    def test_a_bare_string_argument_expectation_is_one_substring(self):
        """写成 `{"read_file": "README.md"}`（漏了列表）不该按字符迭代。"""
        args = {"read_file": {"path": "F:/README.md"}}

        assert score_case(
            _case(expected_tools=["read_file"], expected_args_contain={"read_file": "README.md"}),
            CaseTrace(tools=("read_file",), args=args, reply="ok"),
        ).passed

        assert not score_case(
            _case(expected_tools=["read_file"], expected_args_contain={"read_file": "NOTES.md"}),
            CaseTrace(tools=("read_file",), args=args, reply="ok"),
        ).passed

    def test_non_json_argument_values_do_not_crash(self):
        verdict = score_case(
            _case(expected_tools=["t"], expected_args_contain={"t": ["zzz-missing"]}),
            CaseTrace(tools=("t",), args={"t": {"when": object()}}, reply="ok"),
        )

        assert "arguments" in verdict.reasons

    def test_ordered_only_applies_to_single_turn(self):
        """spec：顺序比较只在 `kind == single_turn` 且显式 ordered 时生效。"""
        case = _case(
            kind="multi_turn",
            turns=["a", "b"],
            expected_tools=["list_directory", "read_file"],
            ordered=True,
        )

        assert score_case(
            case, CaseTrace(tools=("read_file", "list_directory"), reply="ok")
        ).passed

    def test_reasons_are_derived_from_the_checks(self):
        verdict = score_case(
            _case(expected_tools=["read_file"], answer_should_contain=["氢"]),
            CaseTrace(tools=(), reply="没有"),
        )

        assert set(verdict.reasons) == {c for c, ok in verdict.checks.items() if not ok}
        assert not verdict.passed


class TestUserTurns:
    def test_single_turn_sends_only_the_query(self):
        case = _case(query="第一句", turns=["第一句", "第二句"])

        assert case.user_turns == ["第一句"]

    def test_single_turn_without_a_query_falls_back_to_the_first_turn(self):
        case = _case(query="", turns=["只有这一句", "不该发出去"])

        assert case.user_turns == ["只有这一句"]

    def test_multi_turn_sends_every_turn(self):
        case = _case(kind="multi_turn", query="", turns=["a", "b"])

        assert case.user_turns == ["a", "b"]


class TestScoringBudgetAndRun:
    def test_model_call_budget(self):
        case_kwargs = {"max_model_calls": 3}
        assert _score(
            **case_kwargs, trace=CaseTrace(reply="ok", model_calls=3)
        ).passed
        verdict = _score(**case_kwargs, trace=CaseTrace(reply="ok", model_calls=4))
        assert not verdict.passed and "budget" in verdict.reasons

    def test_no_budget_means_unbounded(self):
        assert _score(trace=CaseTrace(reply="ok", model_calls=99)).passed

    def test_an_error_fails_the_case(self):
        verdict = _score(trace=CaseTrace(reply="ok", error="TimeoutError: boom"))

        assert not verdict.passed
        assert "run" in verdict.reasons


class TestVerdictChecks:
    def test_checks_are_reported_per_applicable_rule(self):
        verdict = _score(reply="见 https://a.example/", must_cite=True, max_model_calls=2)

        assert verdict.checks == {
            "run": True,
            "tools": True,
            "forbidden": True,
            "arguments": True,
            "answer": True,
            "citation": True,
            "budget": True,
        }

    def test_optional_rules_are_absent_from_checks(self):
        verdict = _score(reply="ok")

        assert "citation" not in verdict.checks and "budget" not in verdict.checks


def _report(case: EvalCase, trace: CaseTrace) -> CaseReport:
    return CaseReport(case=case, trace=trace, verdict=score_case(case, trace))


class TestReportRendering:
    def test_category_summary_groups_and_counts(self):
        reports = [
            _report(_case(id="A1", category="tool_selection"), CaseTrace(reply="ok")),
            _report(
                _case(id="A2", category="tool_selection", answer_should_contain=["氢"]),
                CaseTrace(reply="没有"),
            ),
            _report(_case(id="B1", category="memory"), CaseTrace(reply="ok")),
        ]

        assert category_rows(reports) == [
            ("tool_selection", 1, 2),
            ("memory", 1, 1),
        ]

        text = render_report(reports, model="m", started_at=datetime.now(timezone.utc))

        assert "| Tool Selection | 1 | 2 | 50.0% |" in text
        assert "| Memory Operations | 1 | 1 | 100.0% |" in text
        assert "**Combined** | **2** | **3** | **66.7%**" in text

    def test_cost_columns_per_case_and_totals(self):
        reports = [
            _report(
                _case(id="C1"),
                CaseTrace(reply="ok", model_calls=2, tokens=1200),
            ),
            _report(
                _case(id="C2"),
                CaseTrace(reply="ok", model_calls=1, tokens=800),
            ),
        ]

        text = render_report(reports, model="m", started_at=datetime.now(timezone.utc))

        assert "模型调用合计：**3**" in text
        assert "token 合计：**2000**" in text
        assert "| C1 | 2 | 1200 |" in text
        assert "| C2 | 1 | 800 |" in text

    def test_failed_case_has_a_minimal_repro(self):
        report = _report(
            _case(id="F1", expected_tools=[], query="读文件"),
            CaseTrace(
                tools=("read_file",),
                args={"read_file": {"path": "F:/x.md"}},
                reply="这是很长的回答" * 50,
                model_calls=2,
                tokens=99,
            ),
        )

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "最小复现" in text
        assert "`read_file({\"path\": \"F:/x.md\"})`" in text
        assert "回复开头" in text and "回复结尾" in text
        assert "模型调用 / token：2 / 99" in text

    def test_passing_cases_do_not_render_a_repro(self):
        report = _report(_case(id="P1"), CaseTrace(reply="ok"))

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "最小复现" not in text

    def test_should_contain_any_block_is_still_rendered(self):
        report = _report(
            _case(
                id="T09",
                category="answer_quality",
                description="自我介绍",
                answer_should_contain_any=["伙伴", "朋友", "助手"],
            ),
            CaseTrace(reply="我是你的小伙伴"),
        )

        text = render_report([report], model="test-model", started_at=datetime.now(timezone.utc))

        assert "should contain ANY" in text
        assert "any keyword present" in text
        assert "`伙伴`" in text and "`助手`" in text

    def test_multi_turn_cases_list_every_turn(self):
        report = _report(
            _case(id="M1", kind="multi_turn", turns=["第一句", "第二句"]),
            CaseTrace(reply="ok"),
        )

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "**Turns:**" in text and "1. 第一句" in text and "2. 第二句" in text

    def test_baseline_diff_section_marks_regressions(self):
        from eval.baseline import BaselineDiff

        report = _report(_case(id="D1"), CaseTrace(reply="ok"))
        diff = BaselineDiff(
            new_fail=("D9",),
            new_pass=("D8",),
            still_fail=("D7",),
            still_pass=("D1", "D2"),
        )

        text = render_report(
            [report],
            model="m",
            started_at=datetime.now(timezone.utc),
            baseline_diff=diff,
        )

        assert "## Baseline diff" in text
        assert "`D9`" in text and "`D8`" in text and "`D7`" in text
        assert "仍通过（未变）：2 条" in text
        assert "有回归" in text

    def test_cited_sources_are_listed_for_search_cases(self):
        report = _report(
            _case(id="Q1", category="search", must_cite=True, query="python 3.14"),
            CaseTrace(reply="见 https://www.python.org/downloads/ 和 https://b.example/x"),
        )

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "**Cited sources:**" in text
        assert "https://www.python.org/downloads/" in text

    def test_repro_uses_the_full_call_sequence(self):
        """同一个工具连调两次时，复现要按当时各自的参数显示，而不是最后一份。"""
        report = _report(
            _case(id="R1", expected_tools=[]),
            CaseTrace(
                tools=("read_file", "read_file"),
                args={"read_file": {"path": "second.md"}},
                calls=(
                    ("read_file", {"path": "first.md"}),
                    ("read_file", {"path": "second.md"}),
                ),
                reply="ok",
            ),
        )

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "first.md" in text and "second.md" in text
        assert text.index("first.md") < text.index("second.md")

    def test_skipped_and_note_appear_in_the_header(self):
        report = _report(_case(id="S1"), CaseTrace(reply="ok"))

        text = render_report(
            [report],
            model="m",
            started_at=datetime.now(timezone.utc),
            skipped=["G1(invariant)"],
            note="live 用例未包含",
        )

        assert "**Skipped**: G1(invariant)" in text
        assert "**Note**: live 用例未包含" in text


class TestExistingCasesStillScore:
    """回归：现有单轮用例的期望字段没被 v2 改坏（默认选择的完整性在 selection 测试里）。"""

    def test_a_passing_trace_scores_1_for_the_tool_selection_case(self):
        case = next(c for c in EVAL_CASES if c.id == "T02")

        verdict = score_case(
            case,
            CaseTrace(
                tools=("read_file",),
                args={"read_file": {"path": "F:/ShadowProject/README.md"}},
                reply="读到了",
            ),
        )

        assert verdict.passed


class FakeUsageLLM:
    """最小假模型：每轮吐一句话 + 一条 usage，够验证 runner 的 I/O 接线。"""

    def __init__(self, replies: list[str] | None = None):
        self._replies = list(replies or ["好的"])
        self.rounds = 0
        #: 每一轮收到的 messages（用来验证多轮确实带了上一轮的内容）
        self.seen: list[list[dict]] = []

    async def stream_chat(self, messages, tools=None, model=None, on_retry=None):
        self.seen.append(list(messages))
        reply = self._replies[min(self.rounds, len(self._replies) - 1)]
        self.rounds += 1
        yield {"type": "token", "content": reply}
        yield {
            "type": "usage",
            "model": "fake",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    async def estimate_prompt_tokens(self, messages) -> int:
        return 11


class TestRunnerWiring:
    """I/O 层（建库 / 跑用例 / 读 llm_usage）用假模型验证，不花钱、不联网。

    假模型通过 `run_eval(llm_service=…)` / `_run_case(llm_service=…)` 这个缝注入 ——
    走的是真实 runner 路径，不替换被测代码里的符号。
    """

    @staticmethod
    def _tool_runtime():
        from core.tool_registry import ToolRegistry
        from core.tool_runtime import ToolRuntime

        return ToolRuntime(
            registry=ToolRegistry(), enable_tracing=False, enable_sandbox=False
        )

    @staticmethod
    def _quiet_memory(monkeypatch):
        import services.memory_service as memory_mod

        async def no_memories(*_args, **_kwargs):
            return []

        monkeypatch.setattr(memory_mod.memory_service, "search", no_memories)

    @pytest.mark.asyncio
    async def test_a_single_turn_case_records_reply_calls_and_tokens(self, monkeypatch):
        from eval import runner as runner_mod

        self._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)

        trace = await runner_mod._run_case(
            EvalCase(id="X1", query="1+1 等于几？", description="smoke"),
            factory,
            self._tool_runtime(),
            llm_service=FakeUsageLLM(["答案是 2"]),
        )
        await engine.dispose()

        assert trace.reply == "答案是 2"
        assert trace.tools == ()
        assert trace.model_calls == 1
        assert trace.tokens == 15
        assert trace.error is None

    @pytest.mark.asyncio
    async def test_a_multi_turn_case_sends_every_turn_in_one_conversation(self, monkeypatch):
        from eval import runner as runner_mod

        self._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)
        fake = FakeUsageLLM(["第一轮回答", "第二轮回答"])

        trace = await runner_mod._run_case(
            EvalCase(
                id="M1",
                kind="multi_turn",
                turns=["第一句", "第二句"],
                description="multi-turn smoke",
            ),
            factory,
            self._tool_runtime(),
            llm_service=fake,
        )
        await engine.dispose()

        assert fake.rounds == 2
        assert trace.model_calls == 2 and trace.tokens == 30
        assert "第一轮回答" in trace.reply and "第二轮回答" in trace.reply
        # 第二轮真的看到了第一轮：否则"多轮"只是把两条消息各问一遍
        second_round = "\n".join(str(m.get("content", "")) for m in fake.seen[1])
        assert "第一轮回答" in second_round

    @pytest.mark.asyncio
    async def test_a_crashed_case_still_reports_the_cost_it_burned(self, monkeypatch):
        """第二轮崩了也要把第一轮已经花掉的用量记下来，否则失败用例的成本被低报成 0。"""
        from eval import runner as runner_mod

        self._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)

        class ExplodingLLM(FakeUsageLLM):
            async def stream_chat(self, messages, tools=None, model=None, on_retry=None):
                if self.rounds >= 1:
                    raise RuntimeError("provider exploded")
                async for event in super().stream_chat(
                    messages, tools=tools, model=model
                ):
                    yield event

        trace = await runner_mod._run_case(
            EvalCase(
                id="E1",
                kind="multi_turn",
                turns=["第一句", "第二句"],
                description="crash smoke",
            ),
            factory,
            self._tool_runtime(),
            llm_service=ExplodingLLM(["第一轮回答"]),
        )
        await engine.dispose()

        assert trace.error is not None
        assert trace.model_calls == 1
        assert trace.tokens == 15
        assert "第一轮回答" in trace.reply
