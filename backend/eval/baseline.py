"""基线对比（ticket 04 / spec S3）：**纯函数** —— 这次和上次比，谁新挂了、谁新过了。

基线文件是 JSON（`eval_baseline.json`），只存"摘要 + 逐例状态"，不存回复正文：
它要能进版本库、能 diff、能在换模型后对比。格式不认识时**给清晰的错**，别猜。

格式版本（ticket #76）：

- **v3（当前）**：逐例状态是 `pass` / `fail` / `needs_review` 三态，与报告的
  分母口径一致（`needs_review` 单列、不计入通过率）。`summary` 也分开记
  `passed` / `failed` / `needs_review`。
- **v2（legacy）**：逐例是 bool（`true` = 通过位）。旧文件仍能**读**，也能参与 diff
  与合并；读到时调用方可以用 `is_legacy_baseline()` 明确提示"这是旧格式，
  `needs_review` 当时被记成未失败"。
- 新代码**只写 v3**；`write_baseline()` 拒绝写 legacy 版本，避免静默覆盖
  （ticket #76：不要再悄悄写一份版本不匹配的基线）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: 当前基线格式版本：结构变了就 +1。
BASELINE_VERSION = 3
#: 还能**读**的旧版本（bool 逐例位）。读到后由 `merge_baselines` 升级为 v3。
LEGACY_BASELINE_VERSION = 2
SUPPORTED_BASELINE_VERSIONS = (LEGACY_BASELINE_VERSION, BASELINE_VERSION)

#: v3 的逐例状态（与 `eval.scoring.CaseVerdict.status` 同一套字符串）。
CASE_PASS = "pass"
CASE_FAIL = "fail"
CASE_NEEDS_REVIEW = "needs_review"
CASE_STATUSES = (CASE_PASS, CASE_FAIL, CASE_NEEDS_REVIEW)


@dataclass(frozen=True)
class BaselineDiff:
    """两次基线的差异（都按用例 id 排序，便于稳定输出）。

    状态是**三态**：`pass` / `fail` / `needs_review`。diff 也按三态分流，
    这样基线的分母（不含 `needs_review`）与报告的分母不会各说各的。
    """

    new_fail: tuple[str, ...] = ()      # 上次不是 fail → 这次 fail
    new_pass: tuple[str, ...] = ()      # 上次 fail → 这次 pass
    still_fail: tuple[str, ...] = ()    # 两次都 fail
    still_pass: tuple[str, ...] = ()    # 两次都 pass
    new_review: tuple[str, ...] = ()    # 这次新进 needs_review（不计入分母）
    still_review: tuple[str, ...] = ()  # 两次都 needs_review
    review_cleared: tuple[str, ...] = ()  # 上次 needs_review → 这次 pass/fail
    only_in_new: tuple[str, ...] = ()   # 新增用例
    only_in_old: tuple[str, ...] = ()   # 被删掉的用例
    #: 旧基线是 v2 bool 格式（needs_review 当时记为"未失败"）
    old_legacy: bool = False

    @property
    def has_regressions(self) -> bool:
        return bool(self.new_fail)

    @property
    def is_empty(self) -> bool:
        return not (
            self.new_fail
            or self.new_pass
            or self.new_review
            or self.review_cleared
            or self.only_in_new
            or self.only_in_old
        )


def _as_status(value) -> str:
    """把 v2 的 bool 或 v3 的状态字符串统一成三态之一。"""
    if isinstance(value, bool):
        return CASE_PASS if value else CASE_FAIL
    return value


def baseline_version(payload) -> int | None:
    """基线的格式版本；不是对象/没有整数 version 时返回 None。"""
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def is_legacy_baseline(payload) -> bool:
    """是不是 v2 bool 旧格式（能读、但需要在报告里明确标注）。"""
    return baseline_version(payload) == LEGACY_BASELINE_VERSION


def baseline_payload(reports, *, model: str, generated_at: str) -> dict:
    """把这次的结果压成 v3 基线（`reports` 是 `eval.report.CaseReport` 序列）。

    逐例记 `verdict.status`（三态）。`summary.passed` 只数 pass，
    `summary.needs_review` 单列 —— 与报告的通过率分母一致。
    """
    cases = {r.case.id: r.verdict.status for r in reports}
    passed = sum(1 for status in cases.values() if status == CASE_PASS)
    failed = sum(1 for status in cases.values() if status == CASE_FAIL)
    review = sum(1 for status in cases.values() if status == CASE_NEEDS_REVIEW)
    return {
        "version": BASELINE_VERSION,
        "model": model,
        "generated_at": generated_at,
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": failed,
            "needs_review": review,
            "model_calls": sum(int(r.trace.model_calls) for r in reports),
            "tokens": sum(int(r.trace.tokens) for r in reports),
        },
        "cases": cases,
    }


def validate_baseline(payload: dict) -> None:
    """检查基线格式；不对就抛带指引的 ValueError（两种版本都认）。"""
    if not isinstance(payload, dict):
        raise ValueError("baseline 不是 JSON 对象")
    version = payload.get("version")
    if version not in SUPPORTED_BASELINE_VERSIONS:
        raise ValueError(
            f"baseline 格式版本不兼容（文件是 {version!r}，本版支持 "
            f"{LEGACY_BASELINE_VERSION}（legacy bool）/{BASELINE_VERSION}）："
            "请用新的 runner 重跑一次生成基线（会升级为三态），或去掉 --baseline"
        )
    cases = payload.get("cases")
    if not isinstance(cases, dict):
        raise ValueError("baseline 的 cases 必须是 {用例 id: 状态}")
    if version == LEGACY_BASELINE_VERSION:
        ok = all(isinstance(k, str) and isinstance(v, bool) for k, v in cases.items())
        if not ok:
            raise ValueError(
                "legacy v2 baseline 的 cases 必须是 {用例 id: 是否通过}（bool）"
            )
        return
    ok = all(
        isinstance(k, str) and isinstance(v, str) and v in CASE_STATUSES
        for k, v in cases.items()
    )
    if not ok:
        raise ValueError(
            f"baseline v{BASELINE_VERSION} 的 cases 必须是 "
            "{用例 id: pass/fail/needs_review}"
        )


def normalized_cases(payload: dict) -> dict[str, str]:
    """把任一受支持版本的逐例状态统一成 v3 三态（bool → pass/fail）。"""
    validate_baseline(payload)
    return {case_id: _as_status(value) for case_id, value in payload["cases"].items()}


def read_baseline(path: str | Path) -> dict:
    """读基线文件（格式不对抛 ValueError / JSONDecodeError）。

    v2 bool 旧文件也会**成功**返回（内容不改写）；调用方用 `is_legacy_baseline()`
    判断是否需要提示"这是 legacy 格式"。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_baseline(payload)
    return payload


def write_baseline(path: str | Path, payload: dict) -> None:
    """写 v3 基线；拒绝写 legacy 版本（ticket #76：不静默写版本不匹配的基线）。"""
    validate_baseline(payload)
    version = payload.get("version")
    if version != BASELINE_VERSION:
        raise ValueError(
            f"拒绝写入 legacy v{version} 基线：本版只写 v{BASELINE_VERSION}"
            "（pass/fail/needs_review 三态）。请先用 merge_baselines() 升级，"
            "或删掉旧文件后重跑。"
        )
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def diff_baselines(old: dict, new: dict) -> BaselineDiff:
    """比较两份基线（纯函数）：三态之间的迁移 + 新增/删除。"""
    validate_baseline(old)
    validate_baseline(new)
    old_cases = normalized_cases(old)
    new_cases = normalized_cases(new)

    shared = sorted(set(old_cases) & set(new_cases))
    return BaselineDiff(
        new_fail=tuple(
            i for i in shared
            if new_cases[i] == CASE_FAIL and old_cases[i] != CASE_FAIL
        ),
        new_pass=tuple(
            i for i in shared
            if new_cases[i] == CASE_PASS and old_cases[i] == CASE_FAIL
        ),
        still_fail=tuple(
            i for i in shared
            if old_cases[i] == CASE_FAIL and new_cases[i] == CASE_FAIL
        ),
        still_pass=tuple(
            i for i in shared
            if old_cases[i] == CASE_PASS and new_cases[i] == CASE_PASS
        ),
        new_review=tuple(
            i for i in shared
            if new_cases[i] == CASE_NEEDS_REVIEW
            and old_cases[i] != CASE_NEEDS_REVIEW
        ),
        still_review=tuple(
            i for i in shared
            if old_cases[i] == CASE_NEEDS_REVIEW
            and new_cases[i] == CASE_NEEDS_REVIEW
        ),
        review_cleared=tuple(
            i for i in shared
            if old_cases[i] == CASE_NEEDS_REVIEW
            and new_cases[i] != CASE_NEEDS_REVIEW
        ),
        only_in_new=tuple(sorted(set(new_cases) - set(old_cases))),
        only_in_old=tuple(sorted(set(old_cases) - set(new_cases))),
        old_legacy=is_legacy_baseline(old),
    )


def merge_baselines(old: dict, new: dict) -> dict:
    """把这次的结果**合并**进旧基线（本次跑到的用例覆盖，没跑的保留）。

    这样 `--live` / `--group` / `--only` 的部分运行也能安全地更新基线：不会把没跑的
    用例当成"已删除"，live 的结果也能真正积累下来（ticket 09 的"纳入基线对比"）。
    旧基线的 bool 逐例位会先被归一成三态；合并结果**总是 v3**。
    `summary` 里 total/passed/failed/needs_review 按合并后的状态重算；calls/tokens
    记的是**本次运行**的开销（基线本来就有这两列，混跑时以最近一次为准）。
    """
    validate_baseline(old)
    validate_baseline(new)
    cases = {**normalized_cases(old), **normalized_cases(new)}
    return {
        "version": BASELINE_VERSION,
        "model": new.get("model") or old.get("model", ""),
        "generated_at": new.get("generated_at", ""),
        "summary": {
            "total": len(cases),
            "passed": sum(1 for s in cases.values() if s == CASE_PASS),
            "failed": sum(1 for s in cases.values() if s == CASE_FAIL),
            "needs_review": sum(
                1 for s in cases.values() if s == CASE_NEEDS_REVIEW
            ),
            "model_calls": int(new.get("summary", {}).get("model_calls", 0)),
            "tokens": int(new.get("summary", {}).get("tokens", 0)),
        },
        "cases": cases,
    }
