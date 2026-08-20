# 02 — 后端 API：config 扩展 + llm-config/models/test 代理端点

**What to build:** 在 #01 之上补齐 HTTP 面：`GET /api/config` 加 `api_key_hint`、
`GET /api/providers` 合并自定义供应商、新增 `PUT /api/llm-config`、`POST /api/llm/models`、
`POST /api/llm/test`（后两者后端代理，规避 CORS）。

**Blocked by:** #01

**Status:** ready-for-agent

**关键语义：**

- `api_key` 三态：`null`=保持、非空=覆盖、`""`=清除。
- 代理端点用**请求体里的表单值**（未保存的也测），不是已存配置。
- 代理用 SDK：OpenAI 兼容 `AsyncOpenAI(...).models.list()` / `chat.completions.create(...)`；
  anthropic 走对应 SDK。

## Checklist

- [ ] `GET /api/config`：`llm_provider`/`llm_model`/`has_api_key` 读运行时配置；新增
      `api_key_hint`（尾号 4 位，如 `sk-…abcd`），**永不回完整 key**
- [ ] `GET /api/providers`：内置 `PROVIDER_PRESETS` + `runtime_config.custom_providers` 合并返回
      `[{id, name, base_url?, default_model?, sdk_type?}]`
- [ ] 新建 `backend/api/llm_config.py`，`PUT /api/llm-config`：

```python
class LlmConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    base_url: str | None = None
    api_key: str | None = None          # null=keep, ""=clear, else set
    custom_providers: list[dict] | None = None
```

  → 更新 `runtime_config` 各字段 → `ConfigStore().save(cfg)`（写 yaml + .env）→
  `llm_service.invalidate_clients()` → 返回 `{"status":"updated"}`
- [ ] `POST /api/llm/models`：body `{provider, base_url?, api_key}`；用对应 SDK `models.list()`
      拉取 → `{"models": ["..."]}`；8s 超时，失败返回 `{"error": "..."}`（HTTP 400/502 均可，
      只要前端能显示）
- [ ] `POST /api/llm/test`：body `{provider, base_url?, api_key, model}`；发 `max_tokens=1` 的
      `{"role":"user","content":"ping"}` → `{"ok": true, "latency_ms": 123}` 或
      `{"ok": false, "error": "401 ..."}`；10s 超时；错误信息透传（401=key 错、404=模型不存在）
- [ ] `main.py` 注册 llm_config 路由
- [ ] 手动冒烟：curl 三个端点验证（见 spec「运行方式」）
