"""评测里的可控故障注入（ticket 07 / spec US8）。

失败恢复用例要测的是"工具坏了以后 Agent 会不会优雅降级、如实告知"，不能靠真实网络抖动
或真实断网来制造。这里给 `ToolRuntime` 加一层**只影响指定工具**的故障注入：

| 故障 | 模拟什么 | 注入结果（与真实失败同形） |
|---|---|---|
| `error` | 工具抛错 / 后端 500 | `{"error": "<tool> failed: injected fault (eval)"}` |
| `timeout` | 沙箱超时 | `{"error": "Timeout after 30s"}` |
| `empty` | 工具成功但什么都没返回 | `""`（模型必须自己承认没内容，而不是编） |
| `circuit_open` | 熔断已打开 | `{"error": "Circuit breaker open for <tool>"}` |

工具定义、审批语义、tracing 全部原样继承 —— 所以"新增恢复用例只写数据"成立。
"""

from __future__ import annotations

import json

from core.tool_runtime import ToolRuntime

#: 支持的故障类型（写进用例的 `fail_tools` 值里）
FAULT_KINDS = ("error", "timeout", "empty", "circuit_open")


def fault_envelope(tool_name: str, kind: str) -> str:
    """某种故障在工具结果里的形状（与真实失败保持一致，便于模型/评分识别）。"""
    if kind == "empty":
        return ""
    if kind == "timeout":
        return json.dumps({"error": "Timeout after 30s"}, ensure_ascii=False)
    if kind == "circuit_open":
        return json.dumps(
            {"error": f"Circuit breaker open for {tool_name}"}, ensure_ascii=False
        )
    if kind == "error":
        return json.dumps(
            {"error": f"{tool_name} failed: injected fault (eval)"}, ensure_ascii=False
        )
    raise ValueError(f"unknown fault kind {kind!r}; expected one of {FAULT_KINDS}")


class FaultInjectingRuntime(ToolRuntime):
    """按用例声明注入故障的工具运行时（未声明的工具照常执行）。"""

    def __init__(self, *, registry=None, faults: dict[str, str] | None = None, **kwargs):
        super().__init__(registry=registry, **kwargs)
        self._faults: dict[str, str] = {}
        for tool_name, kind in (faults or {}).items():
            if kind not in FAULT_KINDS:
                raise ValueError(
                    f"unknown fault kind {kind!r} for {tool_name!r}; "
                    f"expected one of {FAULT_KINDS}"
                )
            if self._registry.get_handler(tool_name) is None:
                # 打错名字的故障会变成"看起来像真失败"的假象：这一类必须当场炸掉
                raise ValueError(
                    f"cannot inject a fault into unknown tool {tool_name!r}"
                )
            self._faults[tool_name] = kind

    async def dispatch(
        self,
        name: str,
        arguments: dict,
        *,
        conversation_id: str | None = None,
    ) -> str:
        kind = self._faults.get(name)
        if kind is None:
            return await super().dispatch(
                name, arguments, conversation_id=conversation_id
            )
        print(f"[Eval] injected fault: {name}={kind}", flush=True)
        return fault_envelope(name, kind)


def validate_case(case, registry) -> None:
    """检查一条用例的故障声明是否与真实工具表对得上（错就抛，别静默失效）。

    - `fail_tools` 的名字必须是已注册的工具；
    - `deny_tools` 的名字必须是**需要审批**的工具（否则拒绝分支根本不会被走到）。
    """
    for tool_name in case.fail_tools:
        if registry.get_handler(tool_name) is None:
            raise ValueError(f"{case.id}: fail_tools 里有未注册的工具 {tool_name!r}")
    for tool_name in case.deny_tools:
        if registry.get_handler(tool_name) is None:
            raise ValueError(f"{case.id}: deny_tools 里有未注册的工具 {tool_name!r}")
        if not registry.needs_approval(tool_name):
            raise ValueError(
                f"{case.id}: deny_tools 里的 {tool_name!r} 不需要审批，拒绝不会生效"
            )
