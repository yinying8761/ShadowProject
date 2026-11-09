"""报告渲染（ticket 04 / spec S2）：**纯函数** —— 结果进去，markdown 出来。

`eval_results.md` 的路径不变（仓库根，历史位置），内容升级成：

- 按类别汇总（类别 / 通过 / 总数 / 比例）+ 逐项检查通过率；`needs_review` 单列、
  不进通过率分母（ADR-0012 补充条款：语义判断报告而不断言）
- 成本列：每例的模型调用数、token（来自 `llm_usage` 表，不额外埋点）与
  **`repeated_calls`**（最长一段连续同名工具调用，与 `tools` 断言分开读）
- 失败用例的**最小复现**：工具调用序列（带参数）+ 回复首尾片段
- 与上次基线的 diff（新挂 / 新过 / 仍未过 / 增删用例）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from eval.scoring import (
    CHECK_ANSWER,
    CHECK_ARGS,
    CHECK_BUDGET,
    CHECK_CITATION,
    CHECK_CREDIBLE,
    CHECK_FORBIDDEN,
    CHECK_NOT_COMPLETED,
    CHECK_RUN,
    CHECK_TOOLS,
    CaseTrace,
    CaseVerdict,
    answer_text,
    cited_urls,
    dump_args,
    longest_same_tool_run,
)

#: 检查项在报告里的展示顺序与标题。
CHECK_ORDER = (
    CHECK_RUN,
    CHECK_TOOLS,
    CHECK_FORBIDDEN,
    CHECK_ARGS,
    CHECK_ANSWER,
    CHECK_NOT_COMPLETED,
    CHECK_CITATION,
    CHECK_CREDIBLE,
    CHECK_BUDGET,
)
CHECK_LABELS = {
    CHECK_RUN: "Run",
    CHECK_TOOLS: "Tools",
    CHECK_FORBIDDEN: "Forbidden",
    CHECK_ARGS: "Args",
    CHECK_ANSWER: "Answer",
    CHECK_NOT_COMPLETED: "Not completed",
    CHECK_CITATION: "Citation",
    CHECK_CREDIBLE: "Credible source",
    CHECK_BUDGET: "Budget",
}

CATEGORY_LABELS = {
    "tool_selection": "Tool Selection",
    "argument_accuracy": "Argument Accuracy",
    "answer_quality": "Answer Quality",
    "edge_case": "Edge Cases",
    "memory": "Memory Operations",
    "multi_turn": "Multi-turn",
    "recovery": "Failure Recovery",
    "vision": "Vision",
    "search": "Search",
    "group": "Group Chat",
}
#: 报告里的类别顺序（先放老五类，新类别按优先级排在后面）。
CATEGORY_ORDER = (
    "tool_selection",
    "argument_accuracy",
    "answer_quality",
    "edge_case",
    "memory",
    "multi_turn",
    "recovery",
    "vision",
    "search",
    "group",
)

#: 最小复现里回复片段保留多少字
SNIPPET_CHARS = 200
#: 工具参数在复现块里的最大长度
ARGS_CHARS = 120


@dataclass(frozen=True)
class CaseReport:
    """一条用例的结果：用例 + 实际表现 + 评分。"""

    case: object
    trace: CaseTrace
    verdict: CaseVerdict


def _check_mark(value: bool) -> str:
    return "✅" if value else "❌"


def _render_keyword_checks(
    lines: list[str],
    header: str,
    keywords: Iterable[str],
    lowered_text: str,
    *,
    negate: bool = False,
) -> None:
    """Append a per-keyword ✓/✗ block to *lines* (no-op when *keywords* empty).

    `lowered_text` 必须已经小写（调用方统一 `reply.lower()`），这里不再重复转换。
    """
    keywords = list(keywords)
    if not keywords:
        return
    lines.append(header)
    for kw in keywords:
        found = kw.lower() in lowered_text
        lines.append(f"- `{kw}`: {_check_mark(not found if negate else found)}")
    lines.append("")


def category_rows(reports: Sequence[CaseReport]) -> list[tuple[str, int, int, int]]:
    """(类别, 通过数, 进分母的总数, needs_review 数)，按 `CATEGORY_ORDER` 排序。

    `needs_review` 只有语义判断能定（报告而不断言）→ 单列、**不进分母**；这样把两个
    等价叙述的用例从"假红"移出失败集，也不会顺手把通过率做大。
    """
    counts: dict[str, list[int]] = {}
    for report in reports:
        bucket = counts.setdefault(report.case.category, [0, 0, 0])
        if report.verdict.status == "needs_review":
            bucket[2] += 1
            continue
        bucket[1] += 1
        if report.verdict.status == "pass":
            bucket[0] += 1

    def order(category: str) -> tuple[int, str]:
        try:
            return (CATEGORY_ORDER.index(category), "")
        except ValueError:
            return (len(CATEGORY_ORDER), category)

    return [
        (c, counts[c][0], counts[c][1], counts[c][2]) for c in sorted(counts, key=order)
    ]


def _render_summary(lines: list[str], reports: Sequence[CaseReport]) -> None:
    rows = category_rows(reports)
    total_pass = sum(1 for r in reports if r.verdict.status == "pass")
    total = sum(1 for r in reports if r.verdict.counted)
    review = [r.case.id for r in reports if r.verdict.status == "needs_review"]

    lines.append("## Summary\n")
    lines.append("| Category | Pass | Total | Rate | Needs review |")
    lines.append("|---|---|---|---|---|")
    for category, passed, count, review_count in rows:
        rate = passed / count * 100 if count else 0.0
        lines.append(
            f"| {CATEGORY_LABELS.get(category, category)} | {passed} | {count} | "
            f"{rate:.1f}% | {review_count} |"
        )
    overall = total_pass / total * 100 if total else 0.0
    lines.append(
        f"| **Combined** | **{total_pass}** | **{total}** | **{overall:.1f}%** | "
        f"**{len(review)}** |"
    )
    lines.append("")
    lines.append(
        "> 通过率分母**不含** `needs_review` 用例（语义判断交给评分模型/人工，"
        "报告而不断言，不进默认 CI 的通过率）。"
    )
    if review:
        lines.append(f">\n> Needs review：{_ids(review)}")
    lines.append("")

    # 逐项检查的通过率（工具/参数/回答/must_cite/预算，以及不变量名），便于一眼看出退在哪儿
    lines.append("### Check breakdown\n")
    lines.append("| Check | Pass | Total |")
    lines.append("|---|---|---|")
    known = [c for c in CHECK_ORDER if any(c in r.verdict.checks for r in reports)]
    extra = sorted(
        {c for r in reports for c in r.verdict.checks if c not in CHECK_ORDER}
    )
    for check in [*known, *extra]:
        applicable = [r for r in reports if check in r.verdict.checks]
        if not applicable:
            continue
        passed = sum(1 for r in applicable if r.verdict.checks[check])
        lines.append(f"| {CHECK_LABELS.get(check, check)} | {passed} | {len(applicable)} |")
    lines.append("")


def _repeated_calls_label(trace: CaseTrace) -> str:
    """报告里的 `repeated_calls` 列：最长的连续同名调用，如 `read_file×4`。

    和 `tools` 断言**分开读**：R01/R04/R06 的"工具选对了但一直在重试"要在这里一眼
    看出来，而不是混进"选错工具"的结论里。
    """
    name, count = longest_same_tool_run(trace.tools)
    return f"{name}×{count}" if count >= 2 else "—"


def _render_cost(lines: list[str], reports: Sequence[CaseReport]) -> None:
    calls = sum(int(r.trace.model_calls) for r in reports)
    tokens = sum(int(r.trace.tokens) for r in reports)
    lines.append("## Cost\n")
    lines.append(f"- 模型调用合计：**{calls}**")
    lines.append(f"- token 合计：**{tokens}**")
    lines.append(
        "- `Repeated calls` = 连续调用同一工具的最长一段（重试多 ≠ 选错工具，"
        "与 `Tools` 断言分开看）。"
    )
    lines.append("")
    lines.append("| Case | Model calls | Tokens | Repeated calls |")
    lines.append("|---|---|---|---|")
    for report in reports:
        lines.append(
            f"| {report.case.id} | {report.trace.model_calls} | {report.trace.tokens} | "
            f"{_repeated_calls_label(report.trace)} |"
        )
    lines.append("")


def _render_diff(lines: list[str], diff) -> None:
    lines.append("## Baseline diff\n")
    lines.append(f"- **新挂（上次过 → 这次挂）**：{_ids(diff.new_fail)}")
    lines.append(f"- 新过（上次挂 → 这次过）：{_ids(diff.new_pass)}")
    lines.append(f"- 仍未过：{_ids(diff.still_fail)}")
    lines.append(f"- 仍通过（未变）：{len(diff.still_pass)} 条")
    lines.append(f"- 新增用例：{_ids(diff.only_in_new)}")
    lines.append(f"- 已删除用例：{_ids(diff.only_in_old)}")
    lines.append("")
    if diff.has_regressions:
        lines.append("> ⚠️ 有回归（新挂用例）。")
        lines.append("")


def _ids(items: Iterable[str]) -> str:
    items = list(items)
    if not items:
        return "（无）"
    return "、".join(f"`{i}`" for i in items)


def _render_repro(lines: list[str], report: CaseReport) -> None:
    """失败用例的最小复现：工具调用序列（带参数）+ 回复首尾。"""
    trace = report.trace
    lines.append("<details>")
    lines.append("<summary>最小复现（工具序列 / 回复首尾）</summary>\n")

    calls = list(trace.calls) or [
        (name, trace.args.get(name, {})) for name in trace.tools
    ]
    if calls:
        rendered = []
        for name, args in calls:
            payload = dump_args(args)
            if len(payload) > ARGS_CHARS:
                payload = payload[:ARGS_CHARS] + "…"
            rendered.append(f"`{name}({payload})`")
        lines.append(f"- 工具序列：{' → '.join(rendered)}")
    else:
        lines.append("- 工具序列：（没有调用工具）")

    reply = (trace.reply or "").strip()
    if reply:
        lines.append(f"- 回复开头：`{reply[:SNIPPET_CHARS]}`")
        if len(reply) > SNIPPET_CHARS:
            lines.append(f"- 回复结尾：`{reply[-SNIPPET_CHARS:]}`")
    else:
        lines.append("- 回复：（空）")

    lines.append(f"- 模型调用 / token：{trace.model_calls} / {trace.tokens}")
    if trace.error:
        lines.append(f"- 错误：`{trace.error}`")
    lines.append("\n</details>\n")


def _render_case(lines: list[str], report: CaseReport) -> None:
    case = report.case
    trace = report.trace
    verdict = report.verdict

    lines.append(f"### {case.id} — {case.description}\n")
    turns = getattr(case, "user_turns", None) or [getattr(case, "query", "")]
    if getattr(case, "kind", "") == "invariant":
        lines.append(f"**Scenario:** `{getattr(case, 'scenario', '')}`\n")
    if len(turns) == 1:
        lines.append(f"**Query:** {turns[0]}\n")
    elif turns:
        lines.append("**Turns:**\n")
        for index, turn in enumerate(turns, start=1):
            lines.append(f"{index}. {turn}")
        lines.append("")

    if case.expected_tools is not None:
        exp_tools = ", ".join(f"`{t}`" for t in case.expected_tools) or "(none)"
        act_tools = ", ".join(f"`{t}`" for t in trace.tools) or "(none)"
        lines.append("| | Tools |")
        lines.append("|---|---|")
        lines.append(f"| Expected | {exp_tools} |")
        lines.append(f"| Actual | {act_tools} |")
        lines.append("")

    if case.expected_args_contain:
        lines.append("**Argument checks:**")
        for tool_name, substrings in case.expected_args_contain.items():
            if isinstance(substrings, str):
                substrings = [substrings]
            actual = dump_args(trace.args.get(tool_name, {}))
            for substring in substrings or []:
                lines.append(
                    f"- `{tool_name}` arg contains `{substring}`: "
                    f"{_check_mark(substring in actual)}"
                )
        lines.append("")

    # 与评分用同一段文字（多轮只看最后一轮）—— 否则报告会与结论自相矛盾
    lowered_text = answer_text(case, trace).lower()
    _render_keyword_checks(
        lines,
        "**Answer checks (should contain):**",
        case.answer_should_contain,
        lowered_text,
    )
    if case.answer_should_contain_any:
        any_ok = any(kw.lower() in lowered_text for kw in case.answer_should_contain_any)
        lines.append("**Answer checks (should contain ANY):**")
        lines.append(f"- any keyword present: {_check_mark(any_ok)}")
        for kw in case.answer_should_contain_any:
            lines.append(f"- `{kw}`: {_check_mark(kw.lower() in lowered_text)}")
        lines.append("")
    _render_keyword_checks(
        lines,
        "**Answer checks (should NOT contain):**",
        case.answer_should_not_contain,
        lowered_text,
        negate=True,
    )

    if case.must_cite:
        sources = cited_urls(trace.reply)
        lines.append(
            "**Cited sources:** "
            + (", ".join(f"`{u}`" for u in sources) if sources else "（无）")
        )
        lines.append("")

    checks = [c for c in verdict.checks]
    if checks:
        lines.append("| " + " | ".join(CHECK_LABELS.get(c, c) for c in checks) + " |")
        lines.append("|" + "---|" * len(checks))
        lines.append("| " + " | ".join(_check_mark(verdict.checks[c]) for c in checks) + " |")
        lines.append("")

    if verdict.status == "needs_review":
        reason = getattr(case, "needs_review_reason", "") or (
            "剩下的问题只有语义判断（评分模型/人工）能定"
        )
        lines.append(f"**Needs review（不进通过率分母）：** {reason}\n")

    if len(turns) > 1 and trace.turn_replies:
        lines.append("**Turn replies:**")
        for index, turn_reply in enumerate(trace.turn_replies, start=1):
            snippet = (turn_reply or "（空）").replace("\n", " ")[:SNIPPET_CHARS]
            lines.append(f"{index}. {snippet}")
        lines.append("")

    if verdict.status == "fail":
        lines.append(f"**Failed checks:** {', '.join(verdict.reasons)}\n")
        _render_repro(lines, report)

    if trace.reply:
        preview = trace.reply[:300]
        lines.append(
            "<details>\n<summary>Response (first 300 chars)</summary>\n\n"
            f"```\n{preview}\n```\n</details>\n"
        )

    if trace.error:
        lines.append(f"⚠️ **Error:** `{trace.error}`\n")

    lines.append("---\n")


def render_report(
    reports: Sequence[CaseReport],
    *,
    model: str,
    started_at: datetime,
    baseline_diff=None,
    skipped: Sequence[str] = (),
    note: str | None = None,
) -> str:
    """Build the full ``eval_results.md`` as a string."""
    reports = list(reports)
    lines: list[str] = []

    lines.append("# Agent Eval Results\n")
    lines.append(f"**Model**: `{model}`  ")
    lines.append(f"**Date**: {started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}  ")
    lines.append(f"**Cases**: {len(reports)}  ")
    if skipped:
        lines.append(f"**Skipped**: {', '.join(skipped)}  ")
    if note:
        lines.append(f"**Note**: {note}  ")
    lines.append("\n---\n")

    _render_summary(lines, reports)
    _render_cost(lines, reports)

    if baseline_diff is not None:
        _render_diff(lines, baseline_diff)

    for category, _passed, _total, _review in category_rows(reports):
        lines.append(f"## {CATEGORY_LABELS.get(category, category)}\n")
        for report in reports:
            if report.case.category == category:
                _render_case(lines, report)

    failed = [r for r in reports if r.verdict.status == "fail"]
    if failed:
        lines.append("## Failed Cases\n")
        for report in failed:
            lines.append(
                f"- **{report.case.id}** ({report.case.category}): "
                f"{', '.join(report.verdict.reasons)}"
            )
        lines.append("")

    review = [r for r in reports if r.verdict.status == "needs_review"]
    if review:
        lines.append("## Needs Review（不进通过率分母）\n")
        for report in review:
            reason = getattr(report.case, "needs_review_reason", "") or "语义判断"
            lines.append(f"- **{report.case.id}** ({report.case.category}): {reason}")
        lines.append("")

    return "\n".join(lines)
