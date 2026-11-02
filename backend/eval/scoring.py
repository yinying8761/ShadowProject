"""评测评分（ticket 04 / spec S1）：**纯函数**，不碰模型、不碰数据库、不碰网络。

`score_case(case, trace)` 把"一条用例 + 一次实际表现"变成一个 `CaseVerdict`：
哪几项过了、为什么没过（机器可读的原因名）、以及总的 passed。报告与基线都消费它。

旧版把这个逻辑埋在 `runner._evaluate_case` 里且只能跑单轮；v2 把它拆出来，是因为
"评分规则"本身要被 pytest 锁住（旧 spec 说"评测不依赖 pytest"，那条已经作废）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping

#: 发现的来源 URL（`must_cite` 用）。容忍中文标点/括号包住的情况。
_URL_RE = re.compile(r"https?://[^\s)\]<>\"'，。；）]+")

#: 检查项名字 —— 报告、基线、原因都按它展示（保持稳定，别随实现改）。
CHECK_RUN = "run"          # 这一轮本身有没有跑成（错误 / 超时）
CHECK_TOOLS = "tools"      # 工具集合（或顺序，当 ordered）
CHECK_FORBIDDEN = "forbidden"   # 不该出现的工具
CHECK_ARGS = "arguments"   # 关键参数子串
CHECK_ANSWER = "answer"    # 回答要点（含 / 任一 / 不含）
CHECK_CITATION = "citation"     # 必须带来源 URL
CHECK_BUDGET = "budget"    # 模型调用数上限


@dataclass(frozen=True)
class CaseTrace:
    """一次用例实际发生了什么（runner 采集；测试里直接构造）。"""

    #: 按发生顺序的工具名（`calls` 为空时的回退视图）
    tools: tuple[str, ...] = ()
    #: 工具名 → **最后一次**调用的参数（重复调用时只留最后一份，完整序列看 `calls`）
    args: Mapping[str, Mapping] = field(default_factory=dict)
    #: 完整调用序列（名字 + 当时的参数）—— 最小复现要的是这个，不是"最后一份参数"
    calls: tuple[tuple[str, Mapping], ...] = ()
    reply: str = ""
    #: 报了 usage 的轮数（= `llm_usage` 行数；流式报错的那一轮不会有行）
    model_calls: int = 0
    tokens: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CaseVerdict:
    """一次评分的结论：逐项结果（失败原因由 checks 派生，不存两份状态）。"""

    checks: Mapping[str, bool] = field(default_factory=dict)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(check for check, ok in self.checks.items() if not ok)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def dump_args(args: Mapping) -> str:
    """把工具参数序列化成可比较/可展示的字符串（非 JSON 值也不炸）。"""
    return json.dumps(args or {}, ensure_ascii=False, default=str)


def _answer_ok(case, lowered_text: str) -> bool:
    """回答要点：AND 全中、OR 至少一中、NOT 全不中（输入须已小写）。

    用例没写任何回答断言时，要求回答非空（旧行为：总得说了点什么）。
    """
    if not (
        case.answer_should_contain
        or case.answer_should_contain_any
        or case.answer_should_not_contain
    ):
        return bool(lowered_text.strip())
    return (
        all(k.lower() in lowered_text for k in case.answer_should_contain)
        and (
            not case.answer_should_contain_any
            or any(k.lower() in lowered_text for k in case.answer_should_contain_any)
        )
        and all(k.lower() not in lowered_text for k in case.answer_should_not_contain)
    )


def score_case(case, trace: CaseTrace) -> CaseVerdict:
    """给一条用例的表现打分（纯函数）。"""
    checks: dict[str, bool] = {}
    reply = trace.reply or ""

    checks[CHECK_RUN] = trace.error is None

    # `ordered` 只对单轮用例有意义（多轮的"顺序"是轮次之间的事）
    if case.ordered and getattr(case, "kind", "single_turn") == "single_turn":
        checks[CHECK_TOOLS] = list(trace.tools) == list(case.expected_tools)
    else:
        checks[CHECK_TOOLS] = set(trace.tools) == set(case.expected_tools)

    checks[CHECK_FORBIDDEN] = not [
        t for t in case.forbidden_tools if t in trace.tools
    ]

    args_ok = True
    for tool_name, substrings in case.expected_args_contain.items():
        # 写成裸字符串是常见的笔误：当成单条子串，别按字符迭代
        if isinstance(substrings, str):
            substrings = [substrings]
        payload = dump_args(trace.args.get(tool_name, {}))
        args_ok = args_ok and all(s in payload for s in (substrings or []))
    checks[CHECK_ARGS] = args_ok

    checks[CHECK_ANSWER] = _answer_ok(case, reply.lower())

    if case.must_cite:
        checks[CHECK_CITATION] = bool(_URL_RE.search(reply))

    if case.max_model_calls is not None:
        checks[CHECK_BUDGET] = trace.model_calls <= case.max_model_calls

    return CaseVerdict(checks=checks)


def cited_urls(reply: str) -> list[str]:
    """回答里出现的来源 URL（按出现顺序去重）。"""
    seen: list[str] = []
    for url in _URL_RE.findall(reply or ""):
        if url not in seen:
            seen.append(url)
    return seen
