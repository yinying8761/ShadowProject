"""基线对比（ticket 04 / spec S3）：纯函数 + 文件往返，不碰模型 / DB / 网络。

基线只存「摘要 + 逐例通过位」：能进版本库、能 diff、换模型后可直接对比。格式不认识时
必须给出**清晰的错**（指明版本与怎么修），而不是静默当成全挂 / 全过。
"""

import json

import pytest

from eval.baseline import (
    merge_baselines,
    BASELINE_VERSION,
    BaselineDiff,
    baseline_payload,
    diff_baselines,
    read_baseline,
    validate_baseline,
    write_baseline,
)
from eval.report import CaseReport
from eval.scoring import CaseTrace, score_case
from eval.test_cases import EvalCase


def _report(case_id: str, *, passed: bool, calls: int = 1, tokens: int = 10) -> CaseReport:
    case = EvalCase(id=case_id, query="q", description="d")
    trace = CaseTrace(reply="ok" if passed else "", model_calls=calls, tokens=tokens)
    return CaseReport(case=case, trace=trace, verdict=score_case(case, trace))


def _payload(*, passed: dict[str, bool], model: str = "m") -> dict:
    return {
        "version": BASELINE_VERSION,
        "model": model,
        "generated_at": "2026-11-02T00:00:00+00:00",
        "summary": {"total": len(passed), "passed": sum(passed.values())},
        "cases": dict(passed),
    }


class TestBaselinePayload:
    def test_payload_records_summary_and_per_case_bits(self):
        reports = [
            _report("A1", passed=True, calls=2, tokens=100),
            _report("A2", passed=False, calls=3, tokens=50),
        ]

        payload = baseline_payload(reports, model="deepseek", generated_at="2026-11-02T00:00:00+00:00")

        assert payload["version"] == BASELINE_VERSION
        assert payload["model"] == "deepseek"
        assert payload["cases"] == {"A1": True, "A2": False}
        assert payload["summary"] == {
            "total": 2,
            "passed": 1,
            "model_calls": 5,
            "tokens": 150,
        }

    def test_payload_has_no_reply_text(self):
        """基线要能进版本库：只存通过位，不存回复正文。"""
        payload = baseline_payload(
            [_report("A1", passed=True)], model="m", generated_at="now"
        )

        assert set(payload) == {"version", "model", "generated_at", "summary", "cases"}


class TestDiffBaselines:
    def test_new_fail_new_pass_still_fail_still_pass(self):
        old = _payload(passed={"A": True, "B": False, "C": False, "D": True})
        new = _payload(passed={"A": False, "B": True, "C": False, "D": True})

        diff = diff_baselines(old, new)

        assert diff == BaselineDiff(
            new_fail=("A",),
            new_pass=("B",),
            still_fail=("C",),
            still_pass=("D",),
        )
        assert diff.has_regressions is True
        assert diff.is_empty is False

    def test_added_and_removed_cases_are_reported(self):
        diff = diff_baselines(
            _payload(passed={"A": True, "GONE": True}),
            _payload(passed={"A": True, "NEW": False}),
        )

        assert diff.only_in_new == ("NEW",)
        assert diff.only_in_old == ("GONE",)
        assert diff.new_fail == () and diff.new_pass == ()

    def test_identical_baselines_are_empty_and_not_a_regression(self):
        payload = _payload(passed={"A": True, "B": False})

        diff = diff_baselines(payload, payload)

        assert diff.is_empty is True
        assert diff.has_regressions is False

    def test_ids_are_sorted_for_stable_output(self):
        diff = diff_baselines(
            _payload(passed={"B": True, "A": True}),
            _payload(passed={"B": False, "A": False}),
        )

        assert diff.new_fail == ("A", "B")


class TestValidateBaseline:
    def test_a_wrong_version_is_rejected_with_guidance(self):
        with pytest.raises(ValueError) as excinfo:
            validate_baseline({"version": 1, "cases": {}})

        message = str(excinfo.value)
        assert "版本不兼容" in message
        assert str(BASELINE_VERSION) in message
        assert "--baseline" in message

    def test_cases_must_be_a_bool_map(self):
        with pytest.raises(ValueError):
            validate_baseline({"version": BASELINE_VERSION, "cases": {"A": "yes"}})
        with pytest.raises(ValueError):
            validate_baseline({"version": BASELINE_VERSION, "cases": []})

    def test_non_object_payload_is_rejected(self):
        with pytest.raises(ValueError):
            validate_baseline([])


class TestMergeBaselines:
    """部分运行（--only / --live / --group）也要能安全更新基线（ticket 09）。"""

    def test_cases_not_in_this_run_are_preserved(self):
        old = _payload(passed={"A": True, "B": False, "GONE": True})
        new = _payload(passed={"A": False})

        merged = merge_baselines(old, new)

        assert merged["cases"] == {"A": False, "B": False, "GONE": True}
        assert merged["summary"]["total"] == 3
        assert merged["summary"]["passed"] == 1

    def test_this_runs_bits_win(self):
        merged = merge_baselines(
            _payload(passed={"A": True}), _payload(passed={"A": False})
        )

        assert merged["cases"] == {"A": False}

    def test_a_live_prefix_baseline_does_not_delete_the_rest(self):
        """先跑 --live（只有 live 用例），再合并：群聊/单轮那些位不能被删掉。"""
        old = _payload(passed={"T01": True, "G01": True})
        new = _payload(passed={"V01": True, "S01": False})

        merged = merge_baselines(old, new)

        assert merged["cases"] == {
            "T01": True, "G01": True, "V01": True, "S01": False,
        }

    def test_the_summary_cost_fields_come_from_this_run(self):
        old = _payload(passed={"A": True})
        new = _payload(passed={"A": True})
        new["summary"].update({"model_calls": 7, "tokens": 900})

        merged = merge_baselines(old, new)

        assert merged["summary"]["model_calls"] == 7
        assert merged["summary"]["tokens"] == 900

    def test_an_incompatible_old_baseline_is_refused(self):
        with pytest.raises(ValueError):
            merge_baselines({"version": 1, "cases": {}}, _payload(passed={"A": True}))


class TestBaselineRoundTrip:
    def test_write_then_read_gives_the_same_baseline(self, tmp_path):
        payload = baseline_payload(
            [_report("A1", passed=True), _report("A2", passed=False)],
            model="m",
            generated_at="2026-11-02T00:00:00+00:00",
        )
        path = tmp_path / "eval_baseline.json"

        write_baseline(path, payload)

        assert read_baseline(path) == payload
        # 文件是给人看/给 git diff 的 JSON
        assert json.loads(path.read_text(encoding="utf-8"))["cases"] == {"A1": True, "A2": False}

    def test_writing_an_incompatible_payload_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            write_baseline(tmp_path / "b.json", {"version": 0, "cases": {}})

    def test_reading_a_corrupt_file_raises_json_error(self, tmp_path):
        path = tmp_path / "b.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            read_baseline(path)
