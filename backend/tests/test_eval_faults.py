"""可控故障注入（ticket 07 / spec US8）：失败恢复用例靠它，而不是靠真实网络抖动。

三个必须成立的点：
1. 声明的故障返回**与真实失败同形**的结果（error / timeout / empty / circuit_open）；
2. 没声明的工具**照常执行**（注入不能顺手把别的工具弄坏）；
3. 故障类型写错要在构造时就炸（别等到跑评测才静默失效）。
"""

import json

import pytest

from core.tool_registry import ToolRegistry
from eval.faults import (
    FAULT_KINDS,
    FaultInjectingRuntime,
    fault_envelope,
    validate_case,
)
from eval.test_cases import EvalCase


def _registry_with_echo() -> ToolRegistry:
    registry = ToolRegistry()

    async def echo(**kwargs) -> str:
        return json.dumps({"echo": kwargs}, ensure_ascii=False)

    registry.register(
        name="echo",
        description="echo back",
        parameters={"type": "object", "properties": {}},
        handler=echo,
    )
    return registry


def _runtime(faults: dict[str, str] | None = None) -> FaultInjectingRuntime:
    return FaultInjectingRuntime(
        registry=_registry_with_echo(),
        faults=faults,
        enable_tracing=False,
        enable_sandbox=False,
    )


class TestFaultEnvelopes:
    def test_error_is_a_json_error_envelope(self):
        payload = json.loads(fault_envelope("read_file", "error"))

        assert "error" in payload
        assert "read_file" in payload["error"]

    def test_timeout_matches_the_runtime_timeout_shape(self):
        assert json.loads(fault_envelope("fetch_url", "timeout")) == {
            "error": "Timeout after 30s"
        }

    def test_circuit_open_matches_the_breaker_message(self):
        assert json.loads(fault_envelope("research", "circuit_open")) == {
            "error": "Circuit breaker open for research"
        }

    def test_empty_is_an_empty_result(self):
        assert fault_envelope("read_file", "empty") == ""

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            fault_envelope("read_file", "explode")

    def test_the_fault_vocabulary_is_stable(self):
        assert FAULT_KINDS == ("error", "timeout", "empty", "circuit_open")


class TestFaultInjectingRuntime:
    @pytest.mark.asyncio
    async def test_a_declared_fault_short_circuits_the_real_tool(self):
        runtime = _runtime({"echo": "error"})

        result = await runtime.dispatch("echo", {"text": "hi"})

        assert json.loads(result)["error"].startswith("echo failed")

    @pytest.mark.asyncio
    async def test_undeclared_tools_still_run(self):
        runtime = _runtime({"echo": "error"})

        result = await runtime.dispatch("some_other_tool", {})

        assert json.loads(result) == {"error": "Unknown tool: some_other_tool"}
        # 真正注册过的工具照常走原实现
        assert json.loads(await runtime.dispatch("echo", {"x": 1}))["error"]

    @pytest.mark.asyncio
    async def test_a_healthy_tool_passes_through(self):
        runtime = _runtime({})

        result = await runtime.dispatch("echo", {"text": "hi"})

        assert json.loads(result) == {"echo": {"text": "hi"}}

    @pytest.mark.asyncio
    async def test_each_fault_kind_is_reproducible(self):
        for kind in FAULT_KINDS:
            runtime = _runtime({"echo": kind})

            result = await runtime.dispatch("echo", {"text": "hi"})

            assert result == fault_envelope("echo", kind)

    def test_an_unknown_fault_kind_fails_at_construction(self):
        with pytest.raises(ValueError):
            _runtime({"echo": "explode"})

    def test_a_fault_on_an_unregistered_tool_fails_at_construction(self):
        """名字打错的故障会伪装成"真失败"——必须在构造时就炸掉。"""
        with pytest.raises(ValueError):
            _runtime({"reed_file": "error"})


class TestValidateCase:
    """用例的故障声明必须与真实工具表对得上（否则拒绝/故障会静默失效）。"""

    def _registry(self):
        from core.tool_registry import ToolRegistry

        registry = ToolRegistry()

        async def handler(**_kwargs) -> str:
            return "ok"

        registry.register(
            name="read_file",
            description="read",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
        registry.register(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}},
            handler=handler,
            require_approval=True,
        )
        return registry

    def test_a_fault_on_a_registered_tool_is_fine(self):
        case = EvalCase(id="R1", fail_tools={"read_file": "error"})

        validate_case(case, self._registry())      # 不抛就是通过

    def test_a_fault_on_an_unknown_tool_is_rejected(self):
        case = EvalCase(id="R1", fail_tools={"nope": "error"})

        with pytest.raises(ValueError):
            validate_case(case, self._registry())

    def test_a_denial_of_an_approval_tool_is_fine(self):
        case = EvalCase(id="R2", deny_tools=["write_file"])

        validate_case(case, self._registry())

    def test_a_denial_of_a_non_approval_tool_is_rejected(self):
        """不需要审批的工具永远不会走到"拒绝"分支 —— 这必须是错，不能静默。"""
        case = EvalCase(id="R2", deny_tools=["read_file"])

        with pytest.raises(ValueError) as excinfo:
            validate_case(case, self._registry())
        assert "不需要审批" in str(excinfo.value)

    def test_a_denial_of_an_unknown_tool_is_rejected(self):
        case = EvalCase(id="R2", deny_tools=["nope"])

        with pytest.raises(ValueError):
            validate_case(case, self._registry())
