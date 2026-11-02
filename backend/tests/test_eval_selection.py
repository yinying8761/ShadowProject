"""用例选择（ticket 04 / spec S5）：类别 / live / 群聊三个开关的纯函数。

默认必须**只**跑便宜、确定、不联网的用例：贵用例要显式打开，否则一次默认评测既烧钱
又 flaky。这条在真实用例表上也要成立。
"""

from eval.selection import INVARIANT_KIND, select_cases
from eval.test_cases import EVAL_CASES, EvalCase


def _case(case_id: str, *, category: str = "tool_selection", kind: str = "single_turn", live: bool = False) -> EvalCase:
    return EvalCase(id=case_id, query="q", description="d", category=category, kind=kind, live=live)


CASES = [
    _case("A", category="tool_selection"),
    _case("B", category="memory"),
    _case("C", category="search", live=True),
    _case("D", category="group", kind=INVARIANT_KIND),
    _case("E", category="multi_turn", kind="multi_turn"),
]


def _ids(cases) -> list[str]:
    return [c.id for c in cases]


class TestDefaults:
    def test_default_drops_live_and_invariant_cases(self):
        assert _ids(select_cases(CASES)) == ["A", "B", "E"]

    def test_case_order_is_preserved(self):
        assert _ids(select_cases(CASES, include_live=True)) == ["A", "B", "C", "E"]


class TestFlags:
    def test_live_flag_includes_expensive_cases(self):
        assert _ids(select_cases(CASES, include_live=True)) == ["A", "B", "C", "E"]

    def test_group_flag_includes_invariant_cases(self):
        assert _ids(select_cases(CASES, include_group=True)) == ["A", "B", "D", "E"]

    def test_both_flags_include_everything(self):
        assert _ids(select_cases(CASES, include_live=True, include_group=True)) == [
            "A", "B", "C", "D", "E",
        ]


class TestCategories:
    def test_a_single_category_filters(self):
        assert _ids(select_cases(CASES, categories=["memory"])) == ["B"]

    def test_several_categories_filter(self):
        assert _ids(select_cases(CASES, categories=["memory", "multi_turn"])) == ["B", "E"]

    def test_unknown_category_selects_nothing(self):
        assert select_cases(CASES, categories=["nope"]) == []

    def test_category_and_live_combine(self):
        assert select_cases(CASES, categories=["search"]) == []
        assert _ids(select_cases(CASES, categories=["search"], include_live=True)) == ["C"]

    def test_empty_category_entries_are_ignored(self):
        assert _ids(select_cases(CASES, categories=["", " "])) == ["A", "B", "E"]

    def test_short_category_aliases_from_the_spec_are_accepted(self):
        """spec 的词汇是 argument / answer / edge；用例表里是 *_accuracy / *_quality / *_case。"""
        cases = [
            _case("A", category="argument_accuracy"),
            _case("B", category="answer_quality"),
            _case("C", category="edge_case"),
        ]

        assert _ids(select_cases(cases, categories=["argument"])) == ["A"]
        assert _ids(select_cases(cases, categories=["answer"])) == ["B"]
        assert _ids(select_cases(cases, categories=["edge"])) == ["C"]
        assert _ids(select_cases(cases, categories=["argument", "edge"])) == ["A", "C"]


class TestRealCaseTable:
    def test_the_default_selection_runs_every_non_live_case(self):
        """默认命令跑完行为类用例；群聊不变量要 --group、视觉/搜索要 --live。"""
        selected = select_cases(EVAL_CASES)

        assert len(EVAL_CASES) == 39          # 15 单轮 + 6 多轮 + 7 恢复 + 6 不变量 + 5 live
        assert len(selected) == 28
        assert [c.id for c in selected] == [
            c.id for c in EVAL_CASES if c.kind != "invariant" and not c.live
        ]

    def test_group_flag_adds_the_invariant_cases(self):
        selected = select_cases(EVAL_CASES, include_group=True)

        assert len(selected) == 34
        assert [c.id for c in selected if c.kind == "invariant"] == [
            "G01", "G02", "G03", "G04", "G05", "G06",
        ]

    def test_live_flag_adds_the_vision_and_search_cases(self):
        selected = select_cases(EVAL_CASES, include_live=True)

        assert len(selected) == 33
        assert [c.id for c in selected if c.live] == [
            "V01", "V02", "S01", "S02", "S03",
        ]

    def test_both_flags_select_every_case(self):
        assert len(select_cases(EVAL_CASES, include_live=True, include_group=True)) == 39

    def test_category_filter_works_on_the_real_table(self):
        assert _ids(select_cases(EVAL_CASES, categories=["memory"])) == ["T14", "T15"]
        assert len(select_cases(EVAL_CASES, categories=["multi_turn"])) == 6
        assert len(select_cases(EVAL_CASES, categories=["recovery"])) == 7
        assert len(
            select_cases(EVAL_CASES, categories=["group"], include_group=True)
        ) == 6
        assert len(
            select_cases(EVAL_CASES, categories=["vision"], include_live=True)
        ) == 2
        assert _ids(
            select_cases(EVAL_CASES, categories=["search"], include_live=True)
        ) == ["S01", "S02", "S03"]
