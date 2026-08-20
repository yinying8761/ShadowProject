# 01 — 运行时配置对象 + ConfigStore + LLMService 改造

**What to build:** 新增可变 `LLMRuntimeConfig` + `ConfigStore`（读写 `data/config.yaml` 和 `.env`），
让 `LLMService` 改读运行时配置而非静态 `settings`，配置变更时失效缓存 client。这是整个功能的地基
（ADR-0001）。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

**关键语义：**

- `.env` = 密钥（每供应商 `<PROVIDER>_API_KEY`，`LLM_API_KEY` 兜底）；`data/config.yaml` = 生效配置。
- `LLMService` 构造注入 `runtime_config=`，不再 `from config import settings` 读 LLM 字段。
- 改 base_url / api_key / sdk_type 时，**失效已缓存的 client**（`self._clients.clear()` 或按 key 删），
  下次调用重建。

## Checklist

- [ ] 新建 `backend/services/llm_config.py`，含：

```python
class LLMRuntimeConfig:
    """Mutable LLM runtime config.  Seeds from .env; UI writes yaml+.env and
    refreshes this object so changes take effect without a restart."""
    def __init__(self):
        self.provider: str = "custom"
        self.model: str = ""
        self.base_url: str = ""
        self.api_key: str = ""
        self.custom_providers: list[dict] = []  # [{id, name, base_url}]

    def resolve(self):
        """Return (sdk_type, base_url, model) resolved through PROVIDER_PRESETS
        + custom_providers.  'custom' provider sdk_type == 'openai'."""
        ...


class ConfigStore:
    """Read/write data/config.yaml + per-provider keys in .env."""
    def load(self) -> LLMRuntimeConfig: ...
    def save(self, cfg: LLMRuntimeConfig) -> None: ...  # write yaml + .env
    def get_provider_key(self, provider_id: str) -> str: ...  # <ID>_API_KEY, fallback LLM_API_KEY
```

- [ ] `ConfigStore` 用 `python-dotenv`（`dotenv.set_key`/`get_key`）读写 `.env`；yaml 用 `pyyaml`
      （若未装则加入 `requirements.txt`，或复用现有 yaml 依赖——先查 `requirements.txt`）
- [ ] `main.py` 启动时：`cfg = ConfigStore().load()` → 填充模块级 `runtime_config` 单例
- [ ] `LLMService.__init__(..., runtime_config=None)`：`self._runtime = runtime_config or default_runtime()`
- [ ] `LLMService` 内所有 `settings.get_model()/get_sdk_type()/get_base_url()/llm_api_key` 改为读
      `self._runtime`（`_get_openai_client`/`_get_anthropic_client`/`_get_formatter`/`chat_sync`/
      `_stream_openai`/`_stream_anthropic`）
- [ ] 新增 `LLMService.invalidate_clients()`（或 config 变更回调）清空 `self._clients`
- [ ] `api/config.py` 的 GET 改读 `runtime_config`（`llm_provider`/`llm_model`/`has_api_key`）
- [ ] `eval/runner.py` 模型名 label 改读 `runtime_config`
- [ ] 现有测试全绿（关键回归）：
      `python -m pytest tests/test_llm_retry.py tests/test_agent_tools.py tests/core/test_agent_tools.py -v`
- [ ] 手动冒烟：改内存运行时配置的 provider/model → `LLMService.stream_chat` 下一次调用用新值；
      改 base_url → 缓存 client 被重建
