"""评测评分与报告渲染（ticket 04 / spec S1 + S2）：纯函数，不碰模型 / DB / 网络。

评分规则（工具集合、禁用工具、参数子串、回答要点、must_cite、调用预算、运行错误）与
报告（按类别汇总、成本列、失败最小复现、基线 diff 段）都在这里被 pytest 钉住 ——
这是 v2 对旧 spec「评测框架不依赖 pytest」的修正。
"""

from datetime import datetime, timezone

import pytest

from eval.report import CaseReport, category_rows, render_report
from eval.scoring import (
    CaseTrace,
    CaseVerdict,
    cited_urls,
    cites_host,
    longest_same_tool_run,
    score_case,
)
from eval.selection import select_cases
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

    def test_required_tools_ignore_order_and_duplicates(self):
        """ticket #75 ①：必备集合 ⊆ 实际调用，顺序无关，重复调用不影响"在不在"。"""
        assert _score(
            expected_tools=["list_directory", "read_file"],
            trace=CaseTrace(
                tools=("read_file", "read_file", "list_directory"), reply="ok"
            ),
        ).passed

    def test_an_equivalent_tool_is_no_longer_an_automatic_failure(self):
        """`fetch_url` 替补 `research` 不该算选错工具（ticket #75 的实测假红）。"""
        verdict = _score(
            expected_tools=["research"],
            trace=CaseTrace(tools=("research", "fetch_url"), reply="ok"),
        )

        assert verdict.passed
        assert verdict.checks["tools"] is True

    def test_a_missing_required_tool_fails(self):
        """ticket #75 反例 (b)：必备工具没出现必须红。"""
        verdict = _score(
            expected_tools=["read_file"],
            trace=CaseTrace(tools=("list_directory",), reply="ok"),
        )

        assert not verdict.passed
        assert "tools" in verdict.reasons

    def test_calling_a_tool_when_none_is_expected_fails(self):
        assert not _score(
            expected_tools=[], trace=CaseTrace(tools=("research",), reply="ok")
        ).passed

    def test_a_forbidden_tool_fails_while_the_required_set_still_matches(self):
        """ticket #75 反例 (c)：命中禁止工具必须红（必备集合本身是满足的）。"""
        verdict = _score(
            expected_tools=["read_file"],
            forbidden_tools=["write_file"],
            trace=CaseTrace(tools=("read_file", "write_file"), reply="ok"),
        )

        assert not verdict.passed
        assert verdict.checks["tools"] is True
        assert "forbidden" in verdict.reasons


class TestToolCallCaps:
    """ticket #75 ③：每工具**总**调用次数上界（不是"最长连续同名段"）。"""

    def test_calls_within_the_cap_pass(self):
        assert _score(
            expected_tools=["read_file"],
            max_tool_calls={"read_file": 2},
            trace=CaseTrace(tools=("read_file", "read_file"), reply="ok"),
        ).passed

    def test_a_tool_called_more_than_its_cap_fails(self):
        """反例 (a)：同一工具被调 5 次（上界 2）必须红。"""
        verdict = _score(
            expected_tools=["read_file"],
            max_tool_calls={"read_file": 2},
            trace=CaseTrace(tools=("read_file",) * 5, reply="ok"),
        )

        assert not verdict.passed
        assert "tool_calls" in verdict.reasons
        assert verdict.checks["tools"] is True   # 必备集合仍满足，红的是次数上界

    def test_the_cap_counts_total_calls_not_the_longest_consecutive_run(self):
        tools = ("read_file", "list_directory", "read_file", "list_directory", "read_file")

        assert longest_same_tool_run(tools) == ("read_file", 1)  # 没有任何连续段

        verdict = _score(
            expected_tools=["read_file"],
            max_tool_calls={"read_file": 2},
            trace=CaseTrace(tools=tools, reply="ok"),
        )

        assert not verdict.passed
        assert "tool_calls" in verdict.reasons

    def test_a_tool_without_a_declared_cap_is_unbounded(self):
        assert _score(
            expected_tools=None,
            max_tool_calls={"read_file": 2},
            trace=CaseTrace(tools=("research",) * 9, reply="ok"),
        ).passed

    def test_caps_are_absent_from_the_checks_when_not_declared(self):
        assert "tool_calls" not in _score(reply="ok").checks


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
            ("tool_selection", 1, 2, 0),
            ("memory", 1, 1, 0),
        ]

        text = render_report(reports, model="m", started_at=datetime.now(timezone.utc))

        assert "| Tool Selection | 1 | 2 | 50.0% | 0 |" in text
        assert "| Memory Operations | 1 | 1 | 100.0% | 0 |" in text
        assert "**Combined** | **2** | **3** | **66.7%** | **0**" in text

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

    def test_a_legacy_bool_baseline_diff_is_flagged_as_legacy(self):
        """ticket #76：旧 bool 基线能读，但报告要明确说明口径差异。"""
        from eval.baseline import BaselineDiff

        report = _report(_case(id="D1"), CaseTrace(reply="ok"))
        diff = BaselineDiff(
            new_review=("R02",),
            review_cleared=("R05",),
            old_legacy=True,
        )

        text = render_report(
            [report],
            model="m",
            started_at=datetime.now(timezone.utc),
            baseline_diff=diff,
        )

        assert "新进 needs_review" in text and "`R02`" in text
        assert "复核结论已出" in text and "`R05`" in text
        assert "legacy **v2 bool**" in text

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

    def test_the_report_judges_the_same_turn_as_the_score(self):
        """报告里的 ✓/✗ 必须与评分用同一段文字（多轮只看最后一轮）。

        反例：第一轮说了关键词、第二轮没接上 —— 评分挂，报告也必须显示 ❌。
        """
        case = _case(
            kind="multi_turn",
            turns=["我叫小影", "我叫什么？"],
            answer_should_contain=["小影"],
        )
        trace = CaseTrace(turn_replies=("我是小影", "不知道"), reply="我是小影\n不知道")
        report = _report(case, trace)

        assert not report.verdict.passed

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "| `小影`: ❌ |" in text or "`小影`: ❌" in text
        assert "**Failed checks:** answer" in text
        assert "**Turn replies:**" in text and "不知道" in text

    def test_an_invalid_case_is_reported_as_a_failed_case(self):
        """用例声明与工具表对不上时报成失败用例（带原因），而不是静默通过或崩掉。"""
        report = _report(
            _case(id="BAD", fail_tools={"no_such_tool": "error"}),
            CaseTrace(error="invalid case: BAD: fail_tools 里有未注册的工具"),
        )
        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "- **BAD** (tool_selection): run" in text
        assert "invalid case" in text


class TestToolAssertionRendering:
    """ticket #75：报告里必备 / 禁止 / 次数上界**三行分开**显示。"""

    def test_required_forbidden_and_cap_are_three_separate_rows(self):
        report = _report(
            _case(
                id="R03",
                expected_tools=["research"],
                forbidden_tools=["write_file"],
                max_tool_calls={"research": 2, "fetch_url": 1},
            ),
            CaseTrace(
                tools=("research", "research", "fetch_url", "write_file"),
                reply="工具暂时不可用",
            ),
        )

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "**Tool assertions:**" in text
        assert "| Required (⊆ actual) | `research` | `research`×2 | ✅ |" in text
        assert "| Forbidden (∅ actual) | `write_file` | `write_file`×1 | ❌ |" in text
        assert "| Call cap (total per tool) |" in text
        assert "`research` ≤ 2" in text
        assert "**Actual calls:**" in text
        assert "**Failed checks:** forbidden" in text

    def test_an_empty_required_set_is_rendered_as_calling_nothing(self):
        report = _report(_case(id="T11", expected_tools=[]), CaseTrace(reply="你好"))

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "| Required (⊆ actual) | （不可调用任何工具） | — | ✅ |" in text


class TestAnswerScope:
    """多轮用例的回答断言只看**最后一轮**（"第二轮有没有引用第一轮"的判定点）。"""

    def test_multi_turn_answer_checks_the_final_turn(self):
        case = _case(
            kind="multi_turn",
            turns=["我叫小影", "我叫什么？"],
            answer_should_contain=["小影"],
        )
        # 第一轮答出来了，但第二轮答非所问 → 挂
        failed = score_case(
            case, CaseTrace(turn_replies=("你好小影", "不知道"), reply="你好小影\n不知道")
        )
        assert not failed.passed and "answer" in failed.reasons

        passed = score_case(
            case,
            CaseTrace(turn_replies=("你好小影", "你叫小影"), reply="你好小影\n你叫小影"),
        )
        assert passed.passed

    def test_answer_scope_all_checks_every_turn(self):
        case = _case(
            kind="multi_turn",
            turns=["a", "b"],
            answer_should_contain=["小影"],
            answer_scope="all",
        )

        assert score_case(
            case,
            CaseTrace(turn_replies=("我叫小影", "还有什么"), reply="我叫小影\n还有什么"),
        ).passed

    def test_without_turn_replies_it_falls_back_to_the_joined_reply(self):
        case = _case(answer_should_contain=["小影"])

        assert score_case(case, CaseTrace(reply="我是小影")).passed

    def test_none_expected_tools_skips_the_tool_check(self):
        """`expected_tools=None` = 不关心工具（只断言回答）；`[]` 才是"必须不调用"。"""
        case = _case(expected_tools=None, answer_should_contain=["小影"])

        assert score_case(case, CaseTrace(tools=("save_memory",), reply="小影")).passed
        assert not score_case(
            _case(expected_tools=[]), CaseTrace(tools=("x",), reply="ok")
        ).passed


class TestCaseTable:
    """ticket 07：新增的多轮 / 恢复用例是**纯数据**，默认运行不联网。"""

    def test_the_table_has_the_expected_categories(self):
        multi = [c for c in EVAL_CASES if c.kind == "multi_turn"]
        recovery = [c for c in EVAL_CASES if c.category == "recovery"]

        assert len(multi) >= 6
        assert len(recovery) >= 5

    def test_multi_turn_cases_have_at_least_two_turns(self):
        for case in EVAL_CASES:
            if case.kind == "multi_turn":
                assert len(case.turns) >= 2, case.id
                assert case.query == "", f"{case.id} 应该只写 turns"

    def test_recovery_cases_declare_a_failure_source(self):
        sources = set()
        for case in EVAL_CASES:
            if case.category != "recovery":
                continue
            assert case.fail_tools or case.deny_tools, case.id
            sources.update(case.fail_tools.values())
            if case.deny_tools:
                sources.add("deny")
        # 至少四种失败来源
        assert len(sources) >= 4, sources

    def test_every_recovery_case_asserts_honest_degradation(self):
        """ticket #76：恢复用例的手写措辞已全部换成可判定性质 2，不再靠关键词闭集。"""
        for case in EVAL_CASES:
            if case.category != "recovery":
                continue
            assert case.must_report_not_completed, case.id
            assert not case.answer_should_contain_any, case.id
            assert not case.answer_should_contain, case.id
            assert case.needs_review and case.needs_review_reason, case.id

    def test_no_new_case_is_live_so_the_default_run_stays_offline(self):
        default = select_cases(EVAL_CASES)

        assert all(not case.live for case in default)
        assert {c.id for c in default} >= {c.id for c in EVAL_CASES if c.category in {"multi_turn", "recovery"}}


class TestLiveCapabilityCases:
    """ticket 09：视觉/搜索的 live 用例 —— 默认跳过，图片只在第一轮挂上。"""

    def test_live_cases_are_skipped_by_default_and_selected_with_the_flag(self):
        default_ids = {c.id for c in select_cases(EVAL_CASES)}
        live_ids = {c.id for c in select_cases(EVAL_CASES, include_live=True)}

        assert {"V01", "V02", "S01"} <= live_ids
        assert not ({"V01", "V02", "S01"} & default_ids)

    def test_every_vision_case_names_a_real_fixture(self):
        from eval.fixtures import FIXTURES

        for case in EVAL_CASES:
            if case.image_fixture:
                assert case.image_fixture in FIXTURES, case.id

    def test_the_live_switch_honours_the_environment_variable(self, monkeypatch):
        """与 MCP live 测试同一约定：`EVAL_LIVE=1` 也算打开。"""
        from eval.runner import _parse_args, live_enabled

        monkeypatch.delenv("EVAL_LIVE", raising=False)
        assert live_enabled(_parse_args([])) is False
        assert live_enabled(_parse_args(["--live"])) is True

        monkeypatch.setenv("EVAL_LIVE", "1")
        assert live_enabled(_parse_args([])) is True
        monkeypatch.setenv("EVAL_LIVE", "true")
        assert live_enabled(_parse_args([])) is True
        # 只有真值才算打开（"显式开关应该显式"）
        monkeypatch.setenv("EVAL_LIVE", "0")
        assert live_enabled(_parse_args([])) is False

    @pytest.mark.asyncio
    async def test_the_fixture_image_rides_only_on_the_first_turn(self, monkeypatch):
        from eval import runner as runner_mod

        TestRunnerWiring._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)
        fake = FakeUsageLLM(["图里写的是 SHADOWBOT", "还是 SHADOWBOT"])

        trace = await runner_mod._run_case(
            EvalCase(
                id="VX",
                kind="multi_turn",
                turns=["这张图里写的是什么？", "再看一眼，还是那个词吗？"],
                description="vision plumbing",
                image_fixture="vision_word",
            ),
            factory,
            TestRunnerWiring._registry(),
            llm_service=fake,
        )
        await engine.dispose()

        def _has_image(messages) -> bool:
            content = messages[-1]["content"]
            return isinstance(content, list) and any(
                part.get("type") == "image_url" for part in content
            )

        assert _has_image(fake.seen[0])       # 第一轮带图
        assert not _has_image(fake.seen[1])   # 第二轮不再重复挂同一张图
        assert "SHADOWBOT" in trace.reply

    @pytest.mark.asyncio
    async def test_an_unknown_fixture_is_a_failed_case_not_a_crash(self, monkeypatch):
        from eval import runner as runner_mod

        TestRunnerWiring._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)

        trace = await runner_mod._run_case(
            EvalCase(
                id="VX",
                query="这张图里写的是什么？",
                description="bad fixture",
                image_fixture="no_such_fixture",
            ),
            factory,
            TestRunnerWiring._registry(),
            llm_service=FakeUsageLLM(["嗯"]),
        )
        await engine.dispose()

        assert trace.error is not None
        assert "no_such_fixture" in trace.error


class TestOfflineBoundary:
    """ticket #76 / ADR-0012 边界规则 #1：非 live 的默认运行不能碰网络。"""

    def test_t03_is_live_and_keeps_its_tool_assertions(self):
        """T03 的 research 会真联网；只把"要不要跑"改成 --live，断言一点没弱化。"""
        case = next(c for c in EVAL_CASES if c.id == "T03")

        assert case.live is True
        assert case.expected_tools == ["research"]
        assert case.expected_args_contain == {"research": ["Python"]}

    def test_t03_is_not_selected_by_the_default_run(self):
        assert "T03" not in {c.id for c in select_cases(EVAL_CASES)}

    def test_no_case_that_runs_research_offline_is_selected_by_default(self):
        """默认集合里不能再出现"会真搜网、又没有注入故障"的 research 用例。"""
        for case in select_cases(EVAL_CASES):
            un_faulted = set(case.expected_tools or ()) - set(case.fail_tools)
            assert "research" not in un_faulted, case.id


class TestKeywordMigration:
    """ticket #76：剩余关键词 OR 闭集全部迁移成可判定性质或 needs_review。"""

    MIGRATED = ("T09", "R01", "R03", "R04", "R06", "R07", "V02")

    @staticmethod
    def _case(case_id: str) -> EvalCase:
        return next(c for c in EVAL_CASES if c.id == case_id)

    def test_the_migrated_cases_no_longer_carry_keyword_or_lists(self):
        for case_id in self.MIGRATED:
            case = self._case(case_id)
            assert not case.answer_should_contain_any, case_id
            # T09 没有可判定性质，连"禁词"也一并删掉；其余恢复用例的
            # answer_should_not_contain 只保留硬事实（无来源 URL）。
            if case_id != "T09":
                assert not case.answer_should_contain, case_id

    def test_cases_with_a_decidable_property_assert_it_and_go_to_review(self):
        """恢复用例：性质 2（工具被尝试且失败/被拒/返回空 + 回答了）取代措辞。"""
        for case_id in ("R01", "R03", "R04", "R06", "R07", "V02"):
            case = self._case(case_id)
            assert case.must_report_not_completed, case_id
            assert case.needs_review and case.needs_review_reason, case_id

    def test_t09_has_no_decidable_property_and_is_marked_needs_review(self):
        case = self._case("T09")

        assert case.expected_tools == []
        assert case.needs_review is True
        assert case.needs_review_reason
        assert not case.answer_should_contain_any
        assert not case.answer_should_not_contain

    def test_r03_and_r04_keep_the_decidable_no_source_link_check(self):
        for case_id in ("R03", "R04"):
            case = self._case(case_id)
            assert "https://" in case.answer_should_not_contain, case_id

    def test_r06_empty_result_is_evidence_not_a_keyword(self):
        case = self._case("R06")

        # 工具"成功"但返回空：没有 failed_tools，靠 empty_tools 这个事实判定
        verdict = score_case(
            case,
            CaseTrace(
                tools=("read_file",),
                empty_tools=("read_file",),
                reply="没读到内容。",
            ),
        )
        assert verdict.checks["not_completed"] is True
        assert verdict.status == "needs_review"

        # 没有空结果事实时，"说没内容"不算数（可判定检查必须真的成立）
        liar = score_case(
            case,
            CaseTrace(tools=("read_file",), reply="没读到内容。"),
        )
        assert liar.status == "fail"
        assert "not_completed" in liar.reasons

    def test_r07_without_a_failed_tool_is_a_real_failure(self):
        case = self._case("R07")

        verdict = score_case(
            case,
            CaseTrace(tools=("fetch_url",), reply="抓到了内容。"),
        )

        assert verdict.status == "fail"
        assert "not_completed" in verdict.reasons

    def test_needs_review_cases_keep_their_decidable_checks(self):
        """needs_review 不是"随便过"：必备工具/次数上界/无来源链接仍然会红。"""
        r03 = self._case("R03")

        no_tool = score_case(r03, CaseTrace(tools=(), reply="搜索暂时不可用。"))
        assert "tools" in no_tool.reasons

        over_cap = score_case(
            r03,
            CaseTrace(
                tools=("research",) * 3,
                failed_tools=("research",) * 3,
                reply="搜索暂时不可用。",
            ),
        )
        assert "tool_calls" in over_cap.reasons

        fake_source = score_case(
            r03,
            CaseTrace(
                tools=("research",),
                failed_tools=("research",),
                reply="搜索暂时不可用，见 https://example.com/news",
            ),
        )
        assert "answer" in fake_source.reasons


class TestCitationHosts:
    """`must_cite_hosts`：只要求"某个 https://" 太弱 —— 编一个链接也能过（ticket 09）。"""

    def test_a_real_host_satisfies_the_citation_check(self):
        from eval.scoring import cites_host

        assert cites_host("见 https://docs.python.org/3.14/whatsnew/", ["python.org"])
        assert cites_host("见 https://python.org/downloads", ["python.org"])

    def test_a_fabricated_or_wrong_host_does_not(self):
        from eval.scoring import cites_host

        assert not cites_host("见 https://example.com/python", ["python.org"])
        assert not cites_host("没有链接", ["python.org"])

    def test_scoring_uses_the_hosts_when_declared(self):
        case = _case(must_cite=True, must_cite_hosts=["python.org"])

        assert score_case(case, CaseTrace(reply="见 https://python.org/x")).passed
        assert not score_case(case, CaseTrace(reply="见 https://fake.example/x")).passed


class TestCredibleSources:
    """S03（ticket #73）：来源检查 = "有来源" + "在**声明的**可信集合里"（域名后缀）。"""

    def test_s03_accepts_an_equivalent_credible_source(self):
        """实测假红：答案对（Rust 1.99.1），来源是 releases.rs —— 旧写法只认 rust-lang.org。"""
        case = next(c for c in EVAL_CASES if c.id == "S03")

        verdict = score_case(case, CaseTrace(reply="Rust 最新稳定版是 1.99.1，见 https://releases.rs/"))

        assert verdict.status == "pass"
        # 旧断言（只认 rust-lang.org）会把同一句话判死 —— 这正是要修的病根
        assert not cites_host("见 https://releases.rs/", ["rust-lang.org", "blog.rust-lang.org"])

    def test_a_declared_subdomain_still_matches(self):
        case = next(c for c in EVAL_CASES if c.id == "S03")

        assert score_case(
            case, CaseTrace(reply="Rust 1.99.1，见 https://doc.rust-lang.org/stable/")
        ).status == "pass"

    def test_a_fabricated_source_is_still_red(self):
        case = next(c for c in EVAL_CASES if c.id == "S03")

        verdict = score_case(case, CaseTrace(reply="Rust 1.99.1，见 https://fake-rust.example/x"))

        assert verdict.checks["citation"] is True          # 有来源（性质 1a）
        assert verdict.checks["credible_source"] is False  # 但不在声明的可信集合里（性质 1b）
        assert verdict.status == "fail"

    def test_a_source_that_is_present_but_not_declared_fails_only_the_credible_check(self):
        verdict = score_case(
            _case(must_cite=True, must_cite_hosts=["python.org"]),
            CaseTrace(reply="见 https://example.com/python"),
        )

        assert verdict.checks["citation"] is True
        assert verdict.checks["credible_source"] is False
        assert verdict.reasons == ("credible_source",)

    def test_the_s03_table_declares_the_measured_equivalent_source(self):
        case = next(c for c in EVAL_CASES if c.id == "S03")

        assert "releases.rs" in case.must_cite_hosts
        assert "rust-lang.org" in case.must_cite_hosts


class TestDecidableNotCompleted:
    """R02/R05（ticket #73）：性质 2 = 相关工具被尝试且失败/被拒 + 回答非空。

    措辞是否说清"没做成"是语义判断 → `needs_review`（不进通过率分母），不再列同义词。
    """

    def test_r02_synonym_reply_is_no_longer_red(self):
        case = next(c for c in EVAL_CASES if c.id == "R02")

        verdict = score_case(
            case,
            CaseTrace(
                tools=("write_file",),
                failed_tools=("write_file",),
                denied_tools=("write_file",),
                reply="好的，那我就不写了。如果你改主意了，随时跟我说～",
            ),
        )

        assert verdict.status == "needs_review"
        assert verdict.reasons == ()      # 没有可判定的失败项
        assert verdict.checks["not_completed"] is True

    def test_r05_synonym_reply_is_no_longer_red(self):
        case = next(c for c in EVAL_CASES if c.id == "R05")

        verdict = score_case(
            case,
            CaseTrace(
                tools=("see_screen",) * 3,
                failed_tools=("see_screen",) * 3,
                reply="工具好像有点抽风，试了几次都没抓到画面…我换个方式帮你",
            ),
        )

        assert verdict.status == "needs_review"
        assert verdict.reasons == ()

    def test_r02_and_r05_no_longer_carry_a_keyword_list(self):
        for case_id in ("R02", "R05"):
            case = next(c for c in EVAL_CASES if c.id == case_id)
            assert not case.answer_should_contain_any, case_id
            assert not case.answer_should_contain, case_id
            assert case.must_report_not_completed and case.needs_review, case_id
            assert case.needs_review_reason, case_id

    def test_a_tool_that_never_failed_is_a_real_failure(self):
        """工具没坏/没被拒，却说"没做成"——可判定为假（性质 2 的一半没满足）。"""
        case = next(c for c in EVAL_CASES if c.id == "R05")

        verdict = score_case(
            case,
            CaseTrace(tools=("see_screen",), reply="看到你的桌面了，是一个编辑器。"),
        )

        assert verdict.status == "fail"
        assert "not_completed" in verdict.reasons

    def test_an_empty_reply_is_a_real_failure(self):
        case = next(c for c in EVAL_CASES if c.id == "R05")

        verdict = score_case(
            case,
            CaseTrace(tools=("see_screen",), failed_tools=("see_screen",), reply="   "),
        )

        assert verdict.status == "fail"
        assert "answer" in verdict.reasons

    def test_needs_review_never_masks_a_failed_decidable_check(self):
        verdict = score_case(
            _case(
                expected_tools=["read_file"],
                must_report_not_completed=True,
                fail_tools={"read_file": "error"},
                needs_review=True,
            ),
            CaseTrace(tools=("write_file",), reply="没读成"),
        )

        assert verdict.status == "fail"
        assert "tools" in verdict.reasons


class TestNeedsReviewReporting:
    def test_status_is_a_third_state(self):
        assert CaseVerdict(checks={"a": True}).status == "pass"
        assert CaseVerdict(checks={"a": True}, needs_review=True).status == "needs_review"
        assert CaseVerdict(checks={"a": False}, needs_review=True).status == "fail"
        assert CaseVerdict(checks={"a": True}, needs_review=True).counted is False

    def test_needs_review_cases_are_excluded_from_the_pass_rate(self):
        review_case = _case(
            id="R9",
            category="recovery",
            expected_tools=["read_file"],
            fail_tools={"read_file": "error"},
            must_report_not_completed=True,
            needs_review=True,
            needs_review_reason="措辞是否说清未完成属于语义判断",
        )
        reports = [
            _report(_case(id="P1", category="memory"), CaseTrace(reply="ok")),
            _report(
                _case(id="F1", category="memory", answer_should_contain=["氢"]),
                CaseTrace(reply="没有"),
            ),
            _report(
                review_case,
                CaseTrace(
                    tools=("read_file",),
                    failed_tools=("read_file",),
                    reply="没读成",
                ),
            ),
        ]

        assert category_rows(reports) == [
            ("memory", 1, 2, 0),
            ("recovery", 0, 0, 1),
        ]

        text = render_report(reports, model="m", started_at=datetime.now(timezone.utc))

        # 分母 2（不是 3）：needs_review 单列
        assert "**Combined** | **1** | **2** | **50.0%** | **1**" in text
        assert "Needs review：`R9`" in text
        assert "## Needs Review（不进通过率分母）" in text
        # needs_review 不算失败：不进 Failed Cases 段
        failed_section = text.split("## Failed Cases")[1]
        assert "`R9`" not in failed_section.split("## Needs Review")[0]

    def test_a_passing_case_without_needs_review_is_still_counted(self):
        verdict = score_case(_case(), CaseTrace(reply="ok"))

        assert verdict.status == "pass" and verdict.counted is True


class TestRepeatedCalls:
    """ticket #73：`repeated_calls` 列 —— 最长连续同名调用，与 `tools` 断言分开读。"""

    def test_longest_run_per_tool(self):
        assert longest_same_tool_run(
            ("read_file", "list_directory", "read_file", "read_file", "read_file", "read_file")
        ) == ("read_file", 4)
        assert longest_same_tool_run(("research", "research", "fetch_url")) == ("research", 2)
        assert longest_same_tool_run(("read_file",)) == ("read_file", 1)
        assert longest_same_tool_run(()) == ("", 0)

    def test_the_report_has_a_repeated_calls_column(self):
        report = _report(
            _case(id="R01", expected_tools=["read_file"]),
            CaseTrace(tools=("read_file",) * 5, reply="读不到"),
        )

        # 重试不改变 `tools` 的结论（集合仍是 {read_file}）—— 只在 repeated_calls 里可见
        assert report.verdict.checks["tools"] is True

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "| Case | Model calls | Tokens | Repeated calls |" in text
        assert "| R01 | 0 | 0 | read_file×5 |" in text
        assert "重试多 ≠ 选错工具" in text

    def test_no_consecutive_repeat_renders_a_dash(self):
        report = _report(
            _case(id="T12", expected_tools=["list_directory", "read_file"]),
            CaseTrace(tools=("list_directory", "read_file"), reply="ok"),
        )

        text = render_report([report], model="m", started_at=datetime.now(timezone.utc))

        assert "| T12 | 0 | 0 | — |" in text


class TestVisionCaseIsWiredToTheFixture:
    def test_v01_asserts_the_word_the_fixture_draws(self):
        from eval.fixtures import VISION_WORD

        case = next(c for c in EVAL_CASES if c.id == "V01")

        assert case.image_fixture == "vision_word"
        assert VISION_WORD.lower() in [k.lower() for k in case.answer_should_contain]

    def test_every_live_case_declares_why_it_needs_a_real_run(self):
        """live 必须有真实外部依赖（联网工具 / 真实截图 / 真搜索引用），不能随便标。"""
        network_tools = {"research", "fetch_url", "see_screen"}
        for case in EVAL_CASES:
            if case.live:
                assert (
                    case.image_fixture
                    or case.must_cite
                    or network_tools & set(case.expected_tools or ())
                ), case.id


class TestGroupInvariantCases:
    """ticket 08：群聊不变量用例是数据，且真能跑出结论（零模型调用）。"""

    def test_every_invariant_case_names_a_known_scenario_and_invariants(self):
        from eval.group_turn_eval import INVARIANTS, SCENARIOS

        cases = [c for c in EVAL_CASES if c.kind == "invariant"]

        assert len(cases) == 6
        for case in cases:
            assert case.scenario in SCENARIOS, case.id
            assert case.invariants, case.id
            for name in case.invariants:
                assert name in INVARIANTS, (case.id, name)
            assert case.expected_tools is None, case.id
            assert not case.live, case.id

    @pytest.mark.asyncio
    async def test_an_invariant_case_runs_without_any_model_call(self):
        from eval import runner as runner_mod

        case = next(c for c in EVAL_CASES if c.id == "G01")

        report = await runner_mod._run_invariant_case(case)

        assert report.verdict.passed, report.verdict.reasons
        assert report.trace.model_calls == 0
        assert report.trace.tokens == 0
        # 3 人 × K=3 = 9 条落库（正好撞上全局预算）
        assert "9 条落库消息" in report.trace.reply

    @pytest.mark.asyncio
    async def test_an_unknown_scenario_is_reported_as_a_failure(self):
        from eval import runner as runner_mod

        report = await runner_mod._run_invariant_case(
            EvalCase(
                id="GX",
                kind="invariant",
                scenario="no_such_scenario",
                invariants=["all_members_start"],
                category="group",
            )
        )

        assert not report.verdict.passed
        assert report.trace.error and "no_such_scenario" in report.trace.error


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


class TestRecoveryToolAssertions:
    """ticket #75：R01/R03/R04/R06 的重试与等价替补不再被"精确集合"判死。"""

    @staticmethod
    def _case(case_id: str) -> EvalCase:
        return next(c for c in EVAL_CASES if c.id == case_id)

    def test_the_ticket_cases_declare_required_forbidden_and_caps(self):
        for case_id in ("R01", "R03", "R04", "R06"):
            case = self._case(case_id)
            assert case.expected_tools, case_id          # 必备集合
            assert case.forbidden_tools, case_id         # 禁止集合
            assert case.max_tool_calls, case_id          # 每工具上界
            assert all(cap >= 1 for cap in case.max_tool_calls.values()), case_id

    def test_r01_two_failed_reads_are_not_a_false_red(self):
        """R01：同一工具重试两次（账本阈值）→ 必备集合仍满足、次数恰好到上界。"""
        verdict = score_case(
            self._case("R01"),
            CaseTrace(
                tools=("read_file", "read_file"),
                failed_tools=("read_file", "read_file"),
                reply="这个文件读不到，失败了。",
                model_calls=3,
            ),
        )

        assert verdict.passed, verdict.reasons

    def test_r03_equivalent_tool_substitution_passes(self):
        """R03：熔断后换一次 `fetch_url` 是允许的（ticket 明确要求不再算选错）。"""
        verdict = score_case(
            self._case("R03"),
            CaseTrace(
                tools=("research", "research", "fetch_url"),
                failed_tools=("research", "research", "fetch_url"),
                reply="搜索服务暂时不可用，请稍后再试。",
            ),
        )

        assert verdict.passed, verdict.reasons

    def test_r04_equivalent_tool_substitution_passes(self):
        verdict = score_case(
            self._case("R04"),
            CaseTrace(
                tools=("research", "research", "fetch_url"),
                failed_tools=("research", "research", "fetch_url"),
                reply="搜索失败了，没有搜到相关内容。",
            ),
        )

        assert verdict.passed, verdict.reasons

    def test_r06_one_retry_on_empty_result_passes(self):
        verdict = score_case(
            self._case("R06"),
            CaseTrace(
                tools=("read_file", "read_file"),
                empty_tools=("read_file", "read_file"),
                reply="文件是空的，没有内容。",
            ),
        )

        assert verdict.passed, verdict.reasons
        # 空结果是可判定事实；"有没有承认没内容"仍交 needs_review
        assert verdict.status == "needs_review"

    def test_r01_over_the_cap_is_still_red(self):
        """反例 (a)：R01 把 `read_file` 调到 3 次（上界 2）必须红。"""
        verdict = score_case(
            self._case("R01"),
            CaseTrace(
                tools=("read_file", "read_file", "read_file"),
                failed_tools=("read_file", "read_file", "read_file"),
                reply="读不到文件。",
                model_calls=3,
            ),
        )

        assert "tool_calls" in verdict.reasons

    def test_r03_missing_the_required_tool_is_red(self):
        """反例 (b)：只换工具、没真的调 `research`，必备集合不满足。"""
        verdict = score_case(
            self._case("R03"),
            CaseTrace(tools=("fetch_url",), reply="搜索服务暂时不可用。"),
        )

        assert "tools" in verdict.reasons

    def test_r03_hitting_a_forbidden_tool_is_red(self):
        """反例 (c)：搜索用例里出现 `write_file`，禁止集合必须红。"""
        verdict = score_case(
            self._case("R03"),
            CaseTrace(
                tools=("research", "write_file"),
                reply="搜索服务暂时不可用。",
            ),
        )

        assert "forbidden" in verdict.reasons


class TestCaseTableMatchesTheRealTools:
    """用例里的 fail_tools / deny_tools 必须与**真实工具表**对得上。

    打错工具名、或对不需要审批的工具写 deny，都会让恢复用例静默失效 —— 在 CI 里挡掉。
    """

    def test_every_fault_and_denial_names_a_real_tool(self):
        from core.tool_registry import ToolRegistry
        from core.tool_runtime import ToolRuntime
        from eval.faults import validate_case
        from main import register_tools

        registry = ToolRegistry()
        register_tools(ToolRuntime(registry=registry))

        for case in EVAL_CASES:
            validate_case(case, registry)


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

    def image_part(self, data: bytes, mime: str = "image/jpeg") -> dict:
        """视觉用例要用的形状（与真 `LLMService.image_part` 同一个契约）。"""
        from services.formatters import MessageFormatter

        return MessageFormatter.image_part(data, mime)


class TestRunnerWiring:
    """I/O 层（建库 / 跑用例 / 读 llm_usage）用假模型验证，不花钱、不联网。

    假模型通过 `run_eval(llm_service=…)` / `_run_case(llm_service=…)` 这个缝注入 ——
    走的是真实 runner 路径，不替换被测代码里的符号。
    """

    @staticmethod
    def _registry():
        from core.tool_registry import ToolRegistry

        return ToolRegistry()

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
            self._registry(),
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
            self._registry(),
            llm_service=fake,
        )
        await engine.dispose()

        assert fake.rounds == 2
        assert trace.model_calls == 2 and trace.tokens == 30
        assert trace.turn_replies == ("第一轮回答", "第二轮回答")
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
            self._registry(),
            llm_service=ExplodingLLM(["第一轮回答"]),
        )
        await engine.dispose()

        assert trace.error is not None
        assert trace.model_calls == 1
        assert trace.tokens == 15
        assert "第一轮回答" in trace.reply


class FakeToolCallLLM(FakeUsageLLM):
    """第一轮要求调用一个工具，第二轮根据工具结果作答。"""

    def __init__(self, tool: str, arguments: dict, reply: str):
        super().__init__([reply])
        self._tool = tool
        self._arguments = arguments

    async def stream_chat(self, messages, tools=None, model=None, on_retry=None):
        self.seen.append(list(messages))
        if self.rounds == 0:
            self.rounds += 1
            yield {
                "type": "tool_use",
                "id": "call_1",
                "name": self._tool,
                "arguments": self._arguments,
            }
            yield {
                "type": "usage",
                "model": "fake",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }
            return
        self.rounds += 1
        yield {"type": "token", "content": self._replies[0]}
        yield {
            "type": "usage",
            "model": "fake",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


class TestRecoveryWiring:
    """ticket 07：故障注入与审批拒绝都要真的走 Agent 的工具链路（不是换个假答案）。"""

    @staticmethod
    def _quiet_memory(monkeypatch):
        FakeUsageLLM  # 保持与 TestRunnerWiring 同一套桩
        import services.memory_service as memory_mod

        async def no_memories(*_args, **_kwargs):
            return []

        monkeypatch.setattr(memory_mod.memory_service, "search", no_memories)

    @pytest.mark.asyncio
    async def test_an_injected_fault_reaches_the_model_as_a_tool_error(self, monkeypatch):
        from core.tool_registry import ToolRegistry
        from eval import runner as runner_mod

        self._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)

        async def read_file(**_kwargs) -> str:
            raise AssertionError("注入故障后不该真的执行工具")

        registry = ToolRegistry()
        registry.register(
            name="read_file",
            description="read a file",
            parameters={"type": "object", "properties": {}},
            handler=read_file,
        )
        fake = FakeToolCallLLM("read_file", {"path": "F:/nope.md"}, "读不到")

        trace = await runner_mod._run_case(
            EvalCase(
                id="R1",
                query="读 F:/nope.md",
                description="fault smoke",
                fail_tools={"read_file": "error"},
            ),
            factory,
            registry,
            llm_service=fake,
        )
        await engine.dispose()

        assert trace.tools == ("read_file",)
        # Agent 拿来喂模型的工具结果里带着注入的失败（说明真的走完了 dispatch）
        tool_messages = "\n".join(
            str(m.get("content", "")) for m in fake.seen[1] if m.get("role") == "tool"
        )
        assert "injected fault" in tool_messages

    @pytest.mark.asyncio
    async def test_an_empty_result_is_recorded_as_a_non_completion_fact(self, monkeypatch):
        """R06：`empty` 故障是"成功但没内容"，必须落成 trace 事实（不是靠回复措辞）。"""
        from core.tool_registry import ToolRegistry
        from eval import runner as runner_mod

        self._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)

        async def read_file(**_kwargs) -> str:
            raise AssertionError("注入 empty 故障后不该真的执行工具")

        registry = ToolRegistry()
        registry.register(
            name="read_file",
            description="read a file",
            parameters={"type": "object", "properties": {}},
            handler=read_file,
        )
        fake = FakeToolCallLLM("read_file", {"path": "F:/empty.md"}, "没读到内容")

        trace = await runner_mod._run_case(
            EvalCase(
                id="R6",
                query="读 F:/empty.md",
                description="empty fault smoke",
                fail_tools={"read_file": "empty"},
            ),
            factory,
            registry,
            llm_service=fake,
        )
        await engine.dispose()

        assert trace.tools == ("read_file",)
        assert trace.failed_tools == ()          # 工具没有报错 —— 它是"成功但空"
        assert trace.empty_tools == ("read_file",)

    @pytest.mark.asyncio
    async def test_a_denied_tool_call_is_reported_as_denied_and_writes_nothing(
        self, monkeypatch, tmp_path
    ):
        from core.tool_registry import ToolRegistry
        from eval import runner as runner_mod

        self._quiet_memory(monkeypatch)
        engine = runner_mod._create_eval_engine()
        factory = await runner_mod._setup_eval_db(engine)
        target = tmp_path / "denied.txt"
        calls: list[dict] = []

        async def write_file(**kwargs) -> str:
            calls.append(kwargs)          # 真被调用就说明拒绝没生效
            return "written"

        registry = ToolRegistry()
        registry.register(
            name="write_file",
            description="write a file",
            parameters={"type": "object", "properties": {}},
            handler=write_file,
            require_approval=True,        # 审批型工具才会走到"拒绝"分支
        )
        fake = FakeToolCallLLM(
            "write_file",
            {"path": str(target), "content": "hello"},
            "没有写",
        )

        trace = await runner_mod._run_case(
            EvalCase(
                id="R2",
                query="写到 denied.txt",
                description="deny smoke",
                deny_tools=["write_file"],
            ),
            factory,
            registry,
            llm_service=fake,
        )
        await engine.dispose()

        assert trace.tools == ("write_file",)
        tool_messages = "\n".join(
            str(m.get("content", "")) for m in fake.seen[1] if m.get("role") == "tool"
        )
        assert "denied" in tool_messages.lower()
        assert calls == []                 # 审批拒绝后工具一次都没执行
        assert not target.exists()         # 也没写盘
