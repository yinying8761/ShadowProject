# 01 — Schema 校验核心（jsonschema 依赖 + get_parameters + _validate_args + dispatch 集成）

**What to build:** 在 `ToolRuntime.dispatch()` 调用 handler 之前，用工具注册时的 JSON Schema
校验 `arguments`。校验失败 → 不执行 handler、返回 `{"error": "Schema validation failed: ..."}`、
写一条 `success=False` 的 trace；校验通过 → 照常执行。错误经现有 `tool_result` 回传给 LLM，
LLM 在下一轮自纠。不新增表、不改 API、不改 Agent。

**Blocked by:** None — 可直接开始（依赖 Workflow E 已就位的 `ToolRuntime` / `ToolTraceStore`）。

**Status:** ready-for-agent

**关键语义（实现前必读）：**

- 校验**放在熔断检查之前、retry 循环之外**；校验是确定性的，**不重试**。
- 校验失败**不调用 handler、不触碰熔断器**（不 `record_failure`）——参数错误是 LLM 的责任，
  不是工具健康信号。
- 用 `jsonschema.validate` 的**默认宽松行为**：只校验 required + 已声明字段的
  type/enum/min/max；**不要**加 `additionalProperties: false`（否则 `Agent` 注入的
  `character_id` 会误杀 `save_memory` / `search_memory`）。
- 工具未知 / schema 为空 → 跳过校验，走现有 "Unknown tool" / 正常执行路径。

## Checklist

- [ ] `backend/requirements.txt` 新增 `jsonschema>=4.20`（唯一新依赖），并 `pip install`
- [ ] `core/tool_registry.py` 新增只读 getter：

```python
def get_parameters(self, name: str) -> dict | None:
    """Return the JSON Schema registered for *name*, or None if unknown."""
    tool = self._tools.get(name)
    return tool["parameters"] if tool else None
```

- [ ] `core/tool_runtime.py` 顶部 `import jsonschema`（`asyncio` / `json` 已导入）
- [ ] `core/tool_runtime.py` 模块级新增格式化函数：

```python
def _format_validation_error(exc: jsonschema.exceptions.ValidationError) -> str:
    """Turn a jsonschema error into a short, LLM-friendly message."""
    loc = "/".join(str(p) for p in exc.path) if exc.path else "$"
    return f"Schema validation failed: {loc} — {exc.message}"
```

- [ ] `ToolRuntime` 新增方法 `_validate_args`：

```python
async def _validate_args(self, name: str, arguments: dict) -> str | None:
    """Validate *arguments* against *name*'s registered JSON Schema.

    Returns an error message when invalid, or None when they pass
    (or the tool is unknown / has no schema — handled elsewhere).
    """
    schema = self._registry.get_parameters(name)
    if not schema:
        return None
    try:
        await asyncio.to_thread(jsonschema.validate, instance=arguments, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        return _format_validation_error(exc)
    return None
```

- [ ] `dispatch()` 中，在 `retry_count = 0` 初始化之后、`breaker = self._breaker_for(name)`
      之前，插入校验步骤：

```python
        # Schema validation (Workflow F): reject malformed arguments before the
        # handler runs, so the LLM can correct them on a later round.
        validation_error = await self._validate_args(name, arguments)
        if validation_error is not None:
            success = False
            error_msg = validation_error
            result = json.dumps({"error": validation_error}, ensure_ascii=False)
            await self._persist_trace(
                call_id, name, arguments, result, start, success, error_msg,
                conversation_id, retry_count,
            )
            return result
```

- [ ] 确认 `_validate_args` 只捕获 `ValidationError`——schema 本身非法抛出的 `SchemaError`
      不吞掉，交给 `dispatch()` 现有 `except Exception` 兜底
- [ ] 现有测试全绿（校验不得破坏现有 dispatch 行为）：
      `python -m pytest tests/test_tool_runtime.py tests/test_tool_retry.py tests/test_circuit_breaker.py tests/core/test_agent_tools.py -v`
- [ ] 手动冒烟：在隔离 registry 里注册一个 `read_file`（schema 要求 `path: string`），
      `dispatch("read_file", {"path": 123})` 返回
      `{"error": "Schema validation failed: path — '123' is not of type 'string'"}`，
      且 handler 未被调用；`dispatch("read_file", {"path": "/tmp/x"})` 正常执行
