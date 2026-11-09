"""评测评分（ticket 04 / spec S1）：**纯函数**，不碰模型、不碰数据库、不碰网络。

`score_case(case, trace)` 把"一条用例 + 一次实际表现"变成一个 `CaseVerdict`：
哪几项过了、为什么没过（机器可读的原因名）、以及总的 passed。报告与基线都消费它。

旧版把这个逻辑埋在 `runner._evaluate_case` 里且只能跑单轮；v2 把它拆出来，是因为
"评分规则"本身要被 pytest 锁住（旧 spec 说"评测不依赖 pytest"，那条已经作废）。

**断言规则（ADR-0012 补充条款 / ticket #70、#73）**：只约束**可判定的性质** ——
(1) 有没有来源/引用、(2) 有没有说明未完成、(3) 有没有停手、(4) 调用次数上界。
关键词/字符串只许用于硬事实（精确 URL、数字、版本、协议标记）；"说得像不像"属于语义
判断，交给评分模型或人：这类结论记为 `CaseVerdict.needs_review`，**报告但不进通过率
分母**，绝不悄悄算通过。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

#: 发现的来源 URL（`must_cite` 用）。容忍中文标点/括号包住的情况。
_URL_RE = re.compile(r"https?://[^\s)\]<>\"'，。；）]+")

#: 检查项名字 —— 报告、基线、原因都按它展示（保持稳定，别随实现改）。
CHECK_RUN = "run"          # 这一轮本身有没有跑成（错误 / 超时）
CHECK_TOOLS = "tools"      # **必备**工具是否都在实际调用里（ticket #75；空集合 = 一个都不许调）
CHECK_FORBIDDEN = "forbidden"   # 不该出现的工具
CHECK_TOOL_CALLS = "tool_calls"  # 每工具**总**调用次数上界（ticket #75）
CHECK_ARGS = "arguments"   # 关键参数子串
CHECK_ANSWER = "answer"    # 回答要点（含 / 任一 / 不含；没有要点时 = 回答非空）
CHECK_NOT_COMPLETED = "not_completed"  # 相关工具被尝试且失败/被拒/返回空 + 回答非空（性质 2）
CHECK_CITATION = "citation"     # 必须带来源 URL（性质 1：有没有来源）
CHECK_CREDIBLE = "credible_source"  # 来源属于用例**声明的**可信集合（域名后缀，硬事实）
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
    #: 工具结果 `is_error=True` 的工具名（按发生顺序，可重复）——"工具失败了"是事实
    failed_tools: tuple[str, ...] = ()
    #: 其中由用户拒绝审批造成的（`tool_result` 里带 `denied`）
    denied_tools: tuple[str, ...] = ()
    #: 成功执行但**结果为空**的工具名（按发生顺序，可重复）—— 例如 `empty` 故障；
    #: 空结果也是"没有拿到内容"这一可判定事实，不是措辞判断（ticket #76 / R06）
    empty_tools: tuple[str, ...] = ()
    #: 每一轮的用户消息对应的回复（多轮用例的断言看最后一轮）
    turn_replies: tuple[str, ...] = ()
    reply: str = ""
    #: 报了 usage 的轮数（= `llm_usage` 行数；流式报错的那一轮不会有行）
    model_calls: int = 0
    tokens: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CaseVerdict:
    """一次评分的结论：逐项结果（失败原因由 checks 派生，不存两份状态）。

    `needs_review` 是**第三态**：可判定检查都过了，但还剩一项只有语义判断（评分模型/
    人工）才能定 —— 例如"回答里的措辞是否说清了没完成"。报告单列、**不进通过率分母**；
    它是"报告而不断言"，不是"通过"（ADR-0012 补充条款）。
    """

    checks: Mapping[str, bool] = field(default_factory=dict)
    needs_review: bool = False

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(check for check, ok in self.checks.items() if not ok)

    @property
    def passed(self) -> bool:
        """所有**可判定**检查都过了（needs_review 不算失败，也不算通过）。"""
        return all(self.checks.values())

    @property
    def status(self) -> str:
        """`pass` / `fail` / `needs_review` —— 报告与基线按它分流。"""
        if not self.passed:
            return "fail"
        return "needs_review" if self.needs_review else "pass"

    @property
    def counted(self) -> bool:
        """进不进通过率分母：只有 needs_review 不进。"""
        return self.status != "needs_review"


def dump_args(args: Mapping) -> str:
    """把工具参数序列化成可比较/可展示的字符串（非 JSON 值也不炸）。"""
    return json.dumps(args or {}, ensure_ascii=False, default=str)


def tool_call_counts(tools: Sequence[str]) -> dict[str, int]:
    """工具名 → 这条 trace 里**总共**被调用的次数（ticket #75 的上界口径）。"""
    counts: dict[str, int] = {}
    for name in tools:
        counts[name] = counts.get(name, 0) + 1
    return counts


def missing_required_tools(
    required: Sequence[str], tools: Sequence[str]
) -> tuple[str, ...]:
    """必备工具里没出现的那些（顺序无关；重复调用不影响"在不在"）。"""
    actual = set(tools)
    return tuple(tool for tool in required if tool not in actual)


def called_forbidden_tools(
    forbidden: Sequence[str], tools: Sequence[str]
) -> tuple[str, ...]:
    """声明为禁止、却真的出现了的工具（按声明顺序去重）。"""
    actual = set(tools)
    seen: list[str] = []
    for tool in forbidden:
        if tool in actual and tool not in seen:
            seen.append(tool)
    return tuple(seen)


def over_tool_caps(
    tools: Sequence[str], caps: Mapping[str, int]
) -> dict[str, int]:
    """超过每工具总调用上界的工具 → 实际次数（只返回越界的）。"""
    counts = tool_call_counts(tools)
    return {
        tool: counts.get(tool, 0)
        for tool, cap in caps.items()
        if counts.get(tool, 0) > cap
    }


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


def answer_text(case, trace: CaseTrace) -> str:
    """回答断言要检查的那段文字。

    多轮用例默认只看**最后一轮**的回复（"第二轮有没有引用第一轮"的判定点）；`all`
    则把每轮回复拼起来。没有逐轮数据时退回 `reply`（单轮 / 旧快照）。
    """
    replies = trace.turn_replies or ((trace.reply,) if trace.reply else ())
    if getattr(case, "answer_scope", "final") == "all":
        return "\n".join(replies)
    return replies[-1] if replies else ""


def score_case(case, trace: CaseTrace) -> CaseVerdict:
    """给一条用例的表现打分（纯函数）。"""
    checks: dict[str, bool] = {}
    reply = trace.reply or ""

    checks[CHECK_RUN] = trace.error is None

    # ticket #75：`expected_tools` 是**必备集合**（⊆ 实际调用；顺序无关、允许重复）。
    # `None` = 这条用例不关心调了哪些工具（比如只测"记住了没有"）；
    # `[]` = 必须**不**调用任何工具（空集是"一个都不许"，不是"随便"）。
    # `ordered=True` 的单轮用例仍按顺序精确比较（唯一还比"序列"的地方，保持旧契约）。
    if case.expected_tools is None:
        checks[CHECK_TOOLS] = True
    elif case.ordered and getattr(case, "kind", "single_turn") == "single_turn":
        checks[CHECK_TOOLS] = list(trace.tools) == list(case.expected_tools)
    elif not case.expected_tools:
        checks[CHECK_TOOLS] = not trace.tools
    else:
        checks[CHECK_TOOLS] = not missing_required_tools(
            case.expected_tools, trace.tools
        )

    checks[CHECK_FORBIDDEN] = not called_forbidden_tools(
        case.forbidden_tools, trace.tools
    )

    # ticket #75 ③：每工具**总**调用次数上界（不是"连续同名"—— 中间夹别的工具也算）。
    caps = getattr(case, "max_tool_calls", None) or {}
    if caps:
        checks[CHECK_TOOL_CALLS] = not over_tool_caps(trace.tools, caps)

    args_ok = True
    for tool_name, substrings in case.expected_args_contain.items():
        # 写成裸字符串是常见的笔误：当成单条子串，别按字符迭代
        if isinstance(substrings, str):
            substrings = [substrings]
        payload = dump_args(trace.args.get(tool_name, {}))
        args_ok = args_ok and all(s in payload for s in (substrings or []))
    checks[CHECK_ARGS] = args_ok

    checks[CHECK_ANSWER] = _answer_ok(case, answer_text(case, trace).lower())

    # 性质 2：「说明未完成」——只看 trace 上的事实 + 回答非空，不看措辞
    if getattr(case, "must_report_not_completed", False):
        checks[CHECK_NOT_COMPLETED] = reported_not_completed(case, trace)

    # 引用检查看整段对话（来源可能在任意一轮给出）
    if case.must_cite:
        # 性质 1a：**有没有**来源（是不是 https:// 链接）
        checks[CHECK_CITATION] = bool(_URL_RE.search(reply))
        # 性质 1b：来源是否属于用例**声明**的可信集合（域名后缀匹配，仍是硬事实比较；
        # "这个域名可不可信"由用例数据拍板，不交给评分器猜）
        hosts = getattr(case, "must_cite_hosts", []) or []
        if hosts:
            checks[CHECK_CREDIBLE] = cites_host(reply, hosts)

    if case.max_model_calls is not None:
        checks[CHECK_BUDGET] = trace.model_calls <= case.max_model_calls

    return CaseVerdict(
        checks=checks,
        needs_review=bool(getattr(case, "needs_review", False)),
    )


def reported_not_completed(case, trace: CaseTrace) -> bool:
    """可判定性质 2：**相关工具确实被尝试且失败/被拒/返回空**，并且给出了一句非空回答。

    - "相关工具" = 用例声明的 `fail_tools` / `deny_tools`（没声明就无从判定 → False）；
    - "失败/被拒/空结果" 是 trace 上的事实（`tool_result` 的 `is_error` / `denied` /
      空串），不是措辞；空结果同样属于"没有拿到内容"；
    - 回答是否**说清**了未完成、有没有假装成功，属于语义判断 —— 交给 `needs_review`
      （评分模型/人工），这里不猜、也不列同义词。
    """
    relevant = set(case.fail_tools) | set(case.deny_tools)
    evidence = set(trace.failed_tools) | set(trace.denied_tools) | set(trace.empty_tools)
    if not relevant or not (relevant & evidence):
        return False
    return bool(answer_text(case, trace).strip())


def longest_same_tool_run(tools: Sequence[str]) -> tuple[str, int]:
    """工具事件流里**最长的一段连续同名调用** → `(工具名, 次数)`。

    与 `tools` 断言分开看：连续重试同一个工具是"不放弃/重试过多"的证据，不是"选错
    工具"。空序列返回 `("", 0)`；没有连续重复时次数为 1。
    """
    best_name, best_len = "", 0
    current_name, current_len = "", 0
    for name in tools:
        if name == current_name:
            current_len += 1
        else:
            current_name, current_len = name, 1
        if current_len > best_len:
            best_name, best_len = current_name, current_len
    return best_name, best_len


def cited_urls(reply: str) -> list[str]:
    """回答里出现的来源 URL（按出现顺序去重）。"""
    seen: list[str] = []
    for url in _URL_RE.findall(reply or ""):
        if url not in seen:
            seen.append(url)
    return seen


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def cites_host(reply: str, hosts: Sequence[str]) -> bool:
    """回答里的来源是否落在用例**声明的可信来源集合**里（域名后缀匹配）。

    只要求"有个 https://" 太弱——编一个不存在的链接也能过。这里的"可信"不是评分器
    的判断，而是用例数据里写死的一张域名表（硬事实）：`python.org` 也认
    `docs.python.org`；S03 的 `releases.rs` 这类等价来源由用例显式声明。
    """
    wanted = [h.lower().lstrip(".") for h in hosts if h]
    if not wanted:
        return True
    for url in cited_urls(reply):
        host = _host_of(url)
        if any(host == w or host.endswith("." + w) for w in wanted):
            return True
    return False
