"""基线对比（ticket 04 / spec S3）：**纯函数** —— 这次和上次比，谁新挂了、谁新过了。

基线文件是 JSON（`eval_baseline.json`），只存"摘要 + 逐例通过位"，不存回复正文：
它要能进版本库、能 diff、能在换模型后对比。格式不认识时**给清晰的错**，别猜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: 基线格式版本：结构变了就 +1（旧文件会被明确拒绝，而不是静默误读）。
BASELINE_VERSION = 2


@dataclass(frozen=True)
class BaselineDiff:
    """两次基线的差异（都按用例 id 排序，便于稳定输出）。"""

    new_fail: tuple[str, ...] = ()      # 上次过 → 这次挂
    new_pass: tuple[str, ...] = ()      # 上次挂 → 这次过
    still_fail: tuple[str, ...] = ()    # 两次都挂
    still_pass: tuple[str, ...] = ()    # 两次都过
    only_in_new: tuple[str, ...] = ()   # 新增用例
    only_in_old: tuple[str, ...] = ()   # 被删掉的用例

    @property
    def has_regressions(self) -> bool:
        return bool(self.new_fail)

    @property
    def is_empty(self) -> bool:
        return not (self.new_fail or self.new_pass or self.only_in_new or self.only_in_old)


def baseline_payload(reports, *, model: str, generated_at: str) -> dict:
    """把这次的结果压成基线（`reports` 是 `eval.report.CaseReport` 序列）。"""
    cases = {r.case.id: bool(r.verdict.passed) for r in reports}
    return {
        "version": BASELINE_VERSION,
        "model": model,
        "generated_at": generated_at,
        "summary": {
            "total": len(cases),
            "passed": sum(1 for ok in cases.values() if ok),
            "model_calls": sum(int(r.trace.model_calls) for r in reports),
            "tokens": sum(int(r.trace.tokens) for r in reports),
        },
        "cases": cases,
    }


def validate_baseline(payload: dict) -> None:
    """检查基线格式；不对就抛带指引的 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError("baseline 不是 JSON 对象")
    version = payload.get("version")
    if version != BASELINE_VERSION:
        raise ValueError(
            f"baseline 格式版本不兼容（文件是 {version!r}，本版要 {BASELINE_VERSION}）："
            "请用新的 runner 重跑一次生成基线，或去掉 --baseline"
        )
    cases = payload.get("cases")
    if not isinstance(cases, dict) or not all(
        isinstance(k, str) and isinstance(v, bool) for k, v in cases.items()
    ):
        raise ValueError("baseline 的 cases 必须是 {用例 id: 是否通过}")


def read_baseline(path: str | Path) -> dict:
    """读基线文件（格式不对抛 ValueError / JSONDecodeError）。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_baseline(payload)
    return payload


def write_baseline(path: str | Path, payload: dict) -> None:
    validate_baseline(payload)
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def diff_baselines(old: dict, new: dict) -> BaselineDiff:
    """比较两份基线（纯函数）：新挂 / 新过 / 仍未过 / 仍过 / 新增 / 删除。"""
    validate_baseline(old)
    validate_baseline(new)
    old_cases: dict[str, bool] = old["cases"]
    new_cases: dict[str, bool] = new["cases"]

    shared = sorted(set(old_cases) & set(new_cases))
    return BaselineDiff(
        new_fail=tuple(i for i in shared if old_cases[i] and not new_cases[i]),
        new_pass=tuple(i for i in shared if not old_cases[i] and new_cases[i]),
        still_fail=tuple(i for i in shared if not old_cases[i] and not new_cases[i]),
        still_pass=tuple(i for i in shared if old_cases[i] and new_cases[i]),
        only_in_new=tuple(sorted(set(new_cases) - set(old_cases))),
        only_in_old=tuple(sorted(set(old_cases) - set(new_cases))),
    )


def merge_baselines(old: dict, new: dict) -> dict:
    """把这次的结果**合并**进旧基线（本次跑到的用例覆盖，没跑的保留）。

    这样 `--live` / `--group` / `--only` 的部分运行也能安全地更新基线：不会把没跑的
    用例当成"已删除"，live 的结果也能真正积累下来（ticket 09 的"纳入基线对比"）。
    `summary` 里 total/passed 按合并后的通过位重算；calls/tokens 记的是**本次运行**的
    开销（基线本来就有这两列，混跑时以最近一次为准）。
    """
    validate_baseline(old)
    validate_baseline(new)
    cases = {**old["cases"], **new["cases"]}
    return {
        "version": BASELINE_VERSION,
        "model": new.get("model") or old.get("model", ""),
        "generated_at": new.get("generated_at", ""),
        "summary": {
            "total": len(cases),
            "passed": sum(1 for ok in cases.values() if ok),
            "model_calls": int(new.get("summary", {}).get("model_calls", 0)),
            "tokens": int(new.get("summary", {}).get("tokens", 0)),
        },
        "cases": cases,
    }
