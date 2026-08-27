"""
Tests for eval answer assertions — `answer_should_contain_any` (OR semantics).

Pure-function tests for ``eval.runner._evaluate_case`` covering the
AND / OR / NOT answer-assertion combinations, plus report rendering of the
"should contain ANY" block. No LLM / DB / HTTP involved.
"""

from datetime import datetime, timezone

from eval.test_cases import EvalCase
from eval.runner import CaseResult, _evaluate_case, _generate_report


def _answer_pass(response, *, contain=(), contain_any=(), not_contain=()):
    """Run ``_evaluate_case`` with the given assertion groups and return the
    ``answer_pass`` verdict only."""
    case = EvalCase(
        id="Tx",
        query="q",
        description="d",
        answer_should_contain=list(contain),
        answer_should_contain_any=list(contain_any),
        answer_should_not_contain=list(not_contain),
    )
    result = CaseResult(case=case, final_response=response)
    _evaluate_case(case, result)
    return result.answer_pass


class TestEvaluateCaseAnswer:
    def test_or_only_hit(self):
        """OR-only: any one keyword appearing passes."""
        assert _answer_pass(
            "我是你的小伙伴", contain_any=["伙伴", "朋友", "助手"]
        ) is True

    def test_or_only_miss(self):
        """OR-only: none appearing fails."""
        assert _answer_pass(
            "我是 AI", contain_any=["伙伴", "朋友", "助手"]
        ) is False

    def test_and_or_combo(self):
        """AND + OR combined: both groups must hold."""
        assert _answer_pass(
            "水由氢和氧组成，我是你的朋友",
            contain=["氢", "氧"],
            contain_any=["伙伴", "朋友"],
        ) is True
        # AND satisfied but OR keyword missing → fail
        assert _answer_pass(
            "水由氢和氧组成",
            contain=["氢", "氧"],
            contain_any=["伙伴", "朋友"],
        ) is False

    def test_empty_or_is_always_true(self):
        """Empty OR list is trivially satisfied (doesn't force a match)."""
        assert _answer_pass("水由氢和氧组成", contain=["氢"]) is True
        assert _answer_pass("水由碳组成", contain=["氢"]) is False

    def test_not_still_enforced(self):
        """NOT keywords still fail even when OR hits."""
        assert _answer_pass(
            "我是你的伙伴 error",
            contain_any=["伙伴"],
            not_contain=["error"],
        ) is False

    def test_and_miss_with_or_hit_still_fails(self):
        """AND miss + OR hit = still fails (AND gate wins)."""
        assert _answer_pass(
            "我是你的伙伴",
            contain=["氢"],
            contain_any=["伙伴"],
        ) is False


class TestReportRendering:
    def test_report_includes_should_contain_any_block(self):
        case = EvalCase(
            id="T09",
            query="你好",
            description="自我介绍",
            answer_should_contain_any=["伙伴", "朋友", "助手"],
            category="answer_quality",
        )
        result = CaseResult(case=case, final_response="我是你的小伙伴")
        _evaluate_case(case, result)

        report = _generate_report([result], "test-model", datetime.now(timezone.utc))
        assert "should contain ANY" in report
        assert "any keyword present" in report
        assert "`伙伴`" in report
        assert "`朋友`" in report
        assert "`助手`" in report
