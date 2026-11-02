"""用例选择（ticket 04 / spec S5）：**纯函数** —— 类别 / live / 群聊三个开关。

默认只跑"便宜、确定、不联网"的用例：`live` 与 `kind="invariant"`（群聊不变量，靠脚本化
假模型）都要显式打开，避免一次默认运行既烧钱又 flaky。
"""

from __future__ import annotations

from typing import Iterable, Sequence

#: 群聊不变量用例的 kind（由脚本化假模型驱动，见 spec §不变量注册表）。
INVARIANT_KIND = "invariant"

#: `--only` 的短名字 → 用例表里的类别名（spec 的词汇是 argument / answer / edge，
#: 早期数据用的是 argument_accuracy / answer_quality / edge_case，两个都认）。
CATEGORY_ALIASES = {
    "argument": "argument_accuracy",
    "answer": "answer_quality",
    "edge": "edge_case",
    "tools": "tool_selection",
}


def select_cases(
    cases: Sequence,
    *,
    categories: Iterable[str] = (),
    include_live: bool = False,
    include_group: bool = False,
) -> list:
    """按开关挑出这次要跑的用例（顺序保持用例表里的顺序）。

    - `categories` 为空 = 全部类别；否则按 `case.category` 匹配（接受短名字别名）
    - `include_live=False`（默认）→ 丢掉 `live=True` 的用例
    - `include_group=False`（默认）→ 丢掉 `kind="invariant"` 的群聊用例
    """
    wanted = {
        CATEGORY_ALIASES.get(raw.strip(), raw.strip())
        for raw in categories
        if raw and raw.strip()
    }
    selected = []
    for case in cases:
        if wanted and case.category not in wanted:
            continue
        if getattr(case, "kind", "single_turn") == INVARIANT_KIND and not include_group:
            continue
        if getattr(case, "live", False) and not include_live:
            continue
        selected.append(case)
    return selected
