# 04 — 测试（test_llm_config.py + test_llm_config_api.py）

**What to build:** 覆盖 S1（运行时配置注入 + client 失效）、S2（ConfigStore 往返）、
S3（HTTP + 代理端点）。复用现有注入缝，不依赖真实 LLM API / 真实 `.env` / 真实 yaml 网络。

**Blocked by:** #01, #02

**Status:** ready-for-agent

**参照实现：** 假 SDK client 见 `test_llm_retry.py`（`FakeStream`/`FakeChunk`/`SimpleNamespace`）；
HTTP 端点见 `test_tool_runtime.py::tool_logs_client`（`ASGITransport` + dependency override）；
纯函数/临时目录见 `test_relative_dates.py`。

## Checklist

- [ ] 新建 `backend/tests/test_llm_config.py`
- [ ] **S1 运行时配置注入**：`LLMService(runtime_config=fake)` + 假 client；改 fake 的 provider/model
      → 下一次 `chat_sync`/`stream_chat` 用新 model；改 base_url/key → `invalidate_clients()` 后
      重建 client（断言 client 工厂被再次调用 / 新 base_url 生效）
- [ ] **S2 ConfigStore 往返**：临时目录写 yaml → load 回来 provider/model/custom_providers 一致；
      `get_provider_key`：`<ID>_API_KEY` 命中 → 返回；未命中 → 回退 `LLM_API_KEY`；写 `.env` 用
      `python-dotenv` 验证 key 落盘
- [ ] 新建 `backend/tests/test_llm_config_api.py`
- [ ] **GET /api/config**：返回 `llm_provider`/`llm_model`/`has_api_key`/`api_key_hint`（尾号 4 位），
      **不回完整 key**
- [ ] **PUT /api/llm-config 三态**：`api_key` null → key 不变；非空 → 覆盖；`""` → 清除
- [ ] **GET /api/providers**：内置预设 + 自定义供应商合并返回
- [ ] **POST /api/llm/models**：假 SDK `models.list()` 返回列表 → 端点返回 `{models:[...]}`；
      假 client 抛错/超时 → 返回 `{error:...}`，不 500 崩溃
- [ ] **POST /api/llm/test**：假 `chat.completions` 成功 → `{ok:true, latency_ms}`；假 client 抛
      `status_code=401` → `{ok:false, error 含 401}`；`status_code=404` → error 含模型不存在
- [ ] 全量回归：
      `python -m pytest tests/test_llm_config.py tests/test_llm_config_api.py tests/test_llm_retry.py tests/test_agent_tools.py tests/core/test_agent_tools.py -v`

**假 client 参考（沿用 test_llm_retry.py 风格）：**

```python
class FakeOpenAI:
    def __init__(self, models=None, chat_result=None, exc=None):
        self._models = models or ["m1", "m2"]
        self._chat_result = chat_result or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
        self._exc = exc
        self.models = self
        self.chat = self

    async def list(self):
        if self._exc: raise self._exc
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self._models])

    async def completions(self, **kw):
        return self  # stream=False path
    async def create(self, **kw):
        if self._exc: raise self._exc
        return self._chat_result
```
