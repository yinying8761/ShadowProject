"""基线对比（ticket 04 / spec S3 + ticket #76）：纯函数 + 文件往返，不碰模型 / DB / 网络。

基线只存「摘要 + 逐例状态」：能进版本库、能 diff、换模型后可直接对比。ticket #76 把
逐例位从 bool 升级成三态（`pass` / `fail` / `needs_review`），与报告的通过率分母对齐；
旧的 v2 bool 基线仍能读、能合并，但新代码只写 v3。格式不认识时必须给出**清晰的错**
（指明版本与怎么修），而不是静默当成全挂 / 全过。
"""

import json

import pytest

from eval.baseline import (
    BASELINE_VERSION,
    CASE_FAIL,
    CASE_NEEDS_REVIEW,
    CASE_PASS,
    LEGACY_BASELINE_VERSION,
    BaselineDiff,
    baseline_payload,
    diff_baselines,
    is_legacy_baseline,
    merge_baselines,
    normalized_cases,
    read_baseline,
    validate_baseline,
    write_baseline,
)
from eval.report import CaseReport
from eval.scoring import CaseTrace, score_case
from eval.test_cases import EvalCase


def _report(case_id: str, *, status: str = CASE_PASS, calls: int = 1, tokens: int = 10) -> CaseReport:
    case = EvalCase(
        id=case_id,
        query="q",
        description="d",
        needs_review=(status == CASE_NEEDS_REVIEW),
    )
    trace = CaseTrace(
        reply="" if status == CASE_FAIL else "ok",
        model_calls=calls,
        tokens=tokens,
    )
    report = CaseReport(case=case, trace=trace, verdict=score_case(case, trace))
    assert report.verdict.status == status, (case_id, report.verdict.status)
    return report


def _summary(cases: dict[str, str]) -> dict:
    return {
        "total": len(cases),
        "passed": sum(1 for s in cases.values() if s == CASE_PASS),
        "failed": sum(1 for s in cases.values() if s == CASE_FAIL),
        "needs_review": sum(1 for s in cases.values() if s == CASE_NEEDS_REVIEW),
    }


def _payload(*, cases: dict[str, str], model: str = "m") -> dict:
    return {
        "version": BASELINE_VERSION,
        "model": model,
        "generated_at": "2026-11-02T00:00:00+00:00",
        "summary": {
            **_summary(cases),
            "model_calls": 0,
            "tokens": 0,
        },
        "cases": dict(cases),
    }


def _legacy_payload(*, passed: dict[str, bool], model: str = "m") -> dict:
    return {
        "version": LEGACY_BASELINE_VERSION,
        "model": model,
        "generated_at": "2026-11-04T00:00:00+00:00",
        "summary": {"total": len(passed), "passed": sum(passed.values())},
        "cases": dict(passed),
    }


class TestBaselinePayload:
    def test_payload_records_the_three_states_and_summary(self):
        reports = [
            _report("A1", status=CASE_PASS, calls=2, tokens=100),
            _report("A2", status=CASE_FAIL, calls=3, tokens=50),
            _report("A3", status=CASE_NEEDS_REVIEW),
        ]

        payload = baseline_payload(
            reports, model="deepseek", generated_at="2026-11-02T00:00:00+00:00"
        )

        assert payload["version"] == BASELINE_VERSION
        assert payload["model"] == "deepseek"
        assert payload["cases"] == {
            "A1": CASE_PASS,
            "A2": CASE_FAIL,
            "A3": CASE_NEEDS_REVIEW,
        }
        assert payload["summary"] == {
            "total": 3,
            "passed": 1,
            "failed": 1,
            "needs_review": 1,
            "model_calls": 6,
            "tokens": 160,
        }

    def test_payload_has_no_reply_text(self):
        """基线要能进版本库：只存状态，不存回复正文。"""
        payload = baseline_payload(
            [_report("A1")], model="m", generated_at="now"
        )

        assert set(payload) == {"version", "model", "generated_at", "summary", "cases"}


class TestDiffBaselines:
    def test_new_fail_new_pass_still_fail_still_pass(self):
        old = _payload(cases={
            "A": CASE_PASS, "B": CASE_FAIL, "C": CASE_FAIL, "D": CASE_PASS,
        })
        new = _payload(cases={
            "A": CASE_FAIL, "B": CASE_PASS, "C": CASE_FAIL, "D": CASE_PASS,
        })

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
            _payload(cases={"A": CASE_PASS, "GONE": CASE_PASS}),
            _payload(cases={"A": CASE_PASS, "NEW": CASE_FAIL}),
        )

        assert diff.only_in_new == ("NEW",)
        assert diff.only_in_old == ("GONE",)
        assert diff.new_fail == () and diff.new_pass == ()

    def test_identical_baselines_are_empty_and_not_a_regression(self):
        payload = _payload(cases={"A": CASE_PASS, "B": CASE_FAIL})

        diff = diff_baselines(payload, payload)

        assert diff.is_empty is True
        assert diff.has_regressions is False

    def test_ids_are_sorted_for_stable_output(self):
        diff = diff_baselines(
            _payload(cases={"B": CASE_PASS, "A": CASE_PASS}),
            _payload(cases={"B": CASE_FAIL, "A": CASE_FAIL}),
        )

        assert diff.new_fail == ("A", "B")

    def test_needs_review_transitions_are_their_own_category(self):
        """三态 diff：进/出 needs_review 不能被算成普通的 pass/fail 翻转。"""
        old = _payload(cases={
            "A": CASE_PASS, "B": CASE_NEEDS_REVIEW,
            "C": CASE_FAIL, "D": CASE_NEEDS_REVIEW,
        })
        new = _payload(cases={
            "A": CASE_NEEDS_REVIEW, "B": CASE_PASS,
            "C": CASE_NEEDS_REVIEW, "D": CASE_FAIL,
        })

        diff = diff_baselines(old, new)

        assert diff.new_review == ("A", "C")
        assert diff.review_cleared == ("B", "D")
        assert diff.new_pass == ()          # B 是从 needs_review 出来的，不是 fail → pass
        assert diff.new_fail == ("D",)      # needs_review → fail 是真的挂了

    def test_a_legacy_bool_baseline_is_diffed_against_the_new_three_states(self):
        old = _legacy_payload(passed={"A": True, "B": False, "C": True})
        new = _payload(cases={
            "A": CASE_PASS, "B": CASE_FAIL, "C": CASE_NEEDS_REVIEW,
        })

        diff = diff_baselines(old, new)

        assert diff.old_legacy is True
        assert diff.new_review == ("C",)    # 旧 bool 没有这个状态，新基线第一次单列
        assert diff.new_fail == () and diff.new_pass == ()
        assert diff.still_pass == ("A",) and diff.still_fail == ("B",)


class TestValidateBaseline:
    def test_an_unsupported_version_is_rejected_with_guidance(self):
        with pytest.raises(ValueError) as excinfo:
            validate_baseline({"version": 1, "cases": {}})

        message = str(excinfo.value)
        assert "版本不兼容" in message
        assert str(BASELINE_VERSION) in message
        assert str(LEGACY_BASELINE_VERSION) in message
        assert "--baseline" in message

    def test_v3_cases_must_be_three_state_strings(self):
        with pytest.raises(ValueError):
            validate_baseline({"version": BASELINE_VERSION, "cases": {"A": True}})
        with pytest.raises(ValueError):
            validate_baseline({"version": BASELINE_VERSION, "cases": {"A": "yes"}})
        with pytest.raises(ValueError):
            validate_baseline({"version": BASELINE_VERSION, "cases": []})

    def test_v2_cases_must_still_be_bools(self):
        validate_baseline(_legacy_payload(passed={"A": True}))
        with pytest.raises(ValueError):
            validate_baseline({
                "version": LEGACY_BASELINE_VERSION,
                "cases": {"A": CASE_PASS},
            })

    def test_non_object_payload_is_rejected(self):
        with pytest.raises(ValueError):
            validate_baseline([])

    def test_legacy_detection_is_explicit(self):
        assert is_legacy_baseline(_legacy_payload(passed={"A": True})) is True
        assert is_legacy_baseline(_payload(cases={"A": CASE_PASS})) is False
        assert is_legacy_baseline({"version": 1, "cases": {}}) is False


class TestMergeBaselines:
    """部分运行（--only / --live / --group）也要能安全更新基线（ticket 09）。"""

    def test_cases_not_in_this_run_are_preserved(self):
        old = _payload(cases={"A": CASE_PASS, "B": CASE_FAIL, "GONE": CASE_PASS})
        new = _payload(cases={"A": CASE_FAIL})

        merged = merge_baselines(old, new)

        assert merged["cases"] == {
            "A": CASE_FAIL, "B": CASE_FAIL, "GONE": CASE_PASS,
        }
        assert merged["summary"]["total"] == 3
        assert merged["summary"]["passed"] == 1
        assert merged["summary"]["failed"] == 2

    def test_this_runs_states_win(self):
        merged = merge_baselines(
            _payload(cases={"A": CASE_PASS}), _payload(cases={"A": CASE_FAIL})
        )

        assert merged["cases"] == {"A": CASE_FAIL}

    def test_a_live_prefix_baseline_does_not_delete_the_rest(self):
        """先跑 --live（只有 live 用例），再合并：群聊/单轮那些位不能被删掉。"""
        old = _payload(cases={"T01": CASE_PASS, "G01": CASE_PASS})
        new = _payload(cases={"V01": CASE_PASS, "S01": CASE_FAIL})

        merged = merge_baselines(old, new)

        assert merged["cases"] == {
            "T01": CASE_PASS, "G01": CASE_PASS,
            "V01": CASE_PASS, "S01": CASE_FAIL,
        }

    def test_the_summary_cost_fields_come_from_this_run(self):
        old = _payload(cases={"A": CASE_PASS})
        new = _payload(cases={"A": CASE_PASS})
        new["summary"].update({"model_calls": 7, "tokens": 900})

        merged = merge_baselines(old, new)

        assert merged["summary"]["model_calls"] == 7
        assert merged["summary"]["tokens"] == 900

    def test_a_legacy_bool_baseline_is_upgraded_to_v3(self):
        old = _legacy_payload(passed={"A": True, "B": False})
        new = _payload(cases={"A": CASE_NEEDS_REVIEW, "C": CASE_PASS})

        merged = merge_baselines(old, new)

        assert merged["version"] == BASELINE_VERSION
        assert merged["cases"] == {
            "A": CASE_NEEDS_REVIEW, "B": CASE_FAIL, "C": CASE_PASS,
        }
        assert merged["summary"]["needs_review"] == 1
        assert merged["summary"]["passed"] == 1
        assert merged["summary"]["failed"] == 1

    def test_an_incompatible_old_baseline_is_refused(self):
        with pytest.raises(ValueError):
            merge_baselines({"version": 1, "cases": {}}, _payload(cases={"A": CASE_PASS}))


class TestBaselineRoundTrip:
    def test_write_then_read_gives_the_same_baseline(self, tmp_path):
        payload = baseline_payload(
            [_report("A1"), _report("A2", status=CASE_FAIL)],
            model="m",
            generated_at="2026-11-02T00:00:00+00:00",
        )
        path = tmp_path / "eval_baseline.json"

        write_baseline(path, payload)

        assert read_baseline(path) == payload
        # 文件是给人看/给 git diff 的 JSON
        assert json.loads(path.read_text(encoding="utf-8"))["cases"] == {
            "A1": CASE_PASS, "A2": CASE_FAIL,
        }

    def test_a_legacy_bool_file_still_loads_and_is_flagged_as_legacy(self, tmp_path):
        path = tmp_path / "eval_baseline.json"
        path.write_text(
            json.dumps(_legacy_payload(passed={"A": True, "B": False})),
            encoding="utf-8",
        )

        payload = read_baseline(path)

        assert is_legacy_baseline(payload) is True
        assert normalized_cases(payload) == {"A": CASE_PASS, "B": CASE_FAIL}

    def test_writing_a_legacy_payload_is_refused(self, tmp_path):
        """ticket #76：新代码只写 v3，别静默写一份版本不匹配的基线。"""
        with pytest.raises(ValueError) as excinfo:
            write_baseline(
                tmp_path / "b.json",
                _legacy_payload(passed={"A": True}),
            )

        assert "legacy" in str(excinfo.value).lower()

    def test_writing_an_incompatible_payload_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            write_baseline(tmp_path / "b.json", {"version": 0, "cases": {}})

    def test_reading_a_corrupt_file_raises_json_error(self, tmp_path):
        path = tmp_path / "b.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            read_baseline(path)
