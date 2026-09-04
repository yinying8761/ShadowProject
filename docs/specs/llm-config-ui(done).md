# LLM Config UI — 供应商/模型/密钥 在设置面板可配置

> 日期：2026-08-17 | 状态：待实现 | 关联 ADR：`docs/adr/0001-llm-config-layout.md`

## Problem Statement

当前要换 LLM 供应商或模型，只能手改 `backend/.env` 里的 `LLM_PROVIDER` / `LLM_MODEL` /
`LLM_BASE_URL` / `LLM_API_KEY`，然后重启后端。前端设置面板里的「Model」标签页是**只读**的，
只显示当前 provider/model/"key 已配置"，并提示"去 `.env` 改"。

用户的实际痛点（已确认）：deepseek 官方 key 没余额，换成 opencode-go 中转站这类供应商时，
每次都要手改 `.env` 重启，体验很差；而且 `.env` 里的 `settings` 是启动时读一次就固化的
pydantic 单例，根本无法在 UI 里改。

## Solution

做成 **Hermes 式**（参照 `~/.hermes/config.yaml` + `.env` 的拆分）：

- **`.env` 只存密钥**：每供应商一个 `<PROVIDER>_API_KEY`（`LLM_API_KEY` 留作兜底）。
- **`data/config.yaml` 存生效配置**（非密钥）：当前供应商 + 当前模型 + 自定义供应商列表。
- 引入一个**可变运行时配置对象**，`LLMService` 改读它；UI 改 = 写 yaml + 写 .env + 刷新内存
  → **立即生效，不重启**。
- 前端「Model」标签页从只读改成可编辑表单：选供应商 → 填 key → 拉模型列表 → 选模型 →
  测试连接 → 保存。

不建 DB 表存 LLM 配置；vision/embedding 继续留 `.env`（不在本 spec）。

## User Stories

1. 作为用户，我想在设置面板里用下拉框选择供应商（官方渠道 + 中转站 + 自定义），so that 不用手改 `.env`。
2. 作为用户，我想添加一个自定义供应商（名字 + URL），so that 像 opencode-go 这类中转站能成为列表里的条目。
3. 作为用户，我想为当前供应商填写/更换 API Key，so that 能正常调用它。
4. 作为用户，当我只想改模型而不想动 key 时，我希望留空 key 字段就表示"保持原 key 不变"，so that 不会误改。
5. 作为用户，我想看到一个 key 是否已配置以及它的尾号（如 `sk-…abcd`），so that 我能区分当前配的是哪个 key。
6. 作为用户，我想手动清除一个 key，so that 能移除不再使用的凭据。
7. 作为用户，我想点「拉取模型列表」从当前供应商获取可用模型，so that 不用手打模型名。
8. 作为用户，当拉取模型列表失败/超时/该供应商不支持时，我希望退回自由文本输入，so that 不会卡住。
9. 作为用户，我想在模型下拉框里选模型，也能手填任意模型名，so that 覆盖中转站的特殊模型名。
10. 作为用户，我想点「测试连接」验证 key + 接口 + 模型三者都正确，so that 保存前就知道能不能用。
11. 作为用户，当测试失败时，我希望看到具体原因（401=key 错、404=模型不存在、超时等），so that 我能对症修复。
12. 作为用户，我希望改完配置后**立即生效**（下一条消息就用新配置），so that 不用重启应用。
13. 作为用户，切换供应商时，该供应商自己的 key 能自动跟上，so that 不用每次重填。
14. 作为用户，重启应用后配置仍在（持久化到 `.env` + `config.yaml`），so that 不用反复配。
15. 作为老用户，我希望升级后不用管旧的 `.env` 里的 `LLM_PROVIDER`/`LLM_MODEL`，只需在 UI 里重选一次。
16. 作为开发者，我希望 LLM 配置读写是独立、可单测的模块，so that 不依赖真实 LLM API 或真实 `.env`。
17. 作为开发者，我希望模型列表拉取和测试连接由**后端代理**，so that 规避浏览器 CORS。

## Implementation Decisions

### 配置存储（Hermes 式拆分）

- **`.env` = 密钥**：每供应商一个 `<PROVIDER>_API_KEY`（自定义供应商按 id 蛇形化，如
  `opencode-go` → `OPENCODE_GO_API_KEY`）；可选 `<PROVIDER>_BASE_URL` 覆盖内置 base_url；
  `LLM_API_KEY` 保留作兜底（兼容现有配置）。
- **`data/config.yaml` = 生效配置**（非密钥，schema 来自设计稿）：

```yaml
# data/config.yaml
model:
  provider: deepseek      # 当前供应商 id（内置预设 或 providers 里的自定义 id）
  model: deepseek-chat    # 当前模型
providers:                # 自定义供应商（名字 + URL）；内置预设硬编码在 config.py
  - id: opencode-go
    name: opencode-go
    base_url: https://xxx/v1
```

- 内置官方预设（deepseek/qwen/zhipu/moonshot/openai/anthropic）**仍硬编码在 `config.py`
  的 `PROVIDER_PRESETS`，不进 yaml**；只有"自定义供应商"进 `providers:`。
- 术语按 `CONTEXT.md`：供应商 = 官方渠道 + 中转站；自定义供应商 = 名字 + URL。

### 运行时可变配置对象（最高缝）

- 新增一个可变配置对象（如 `LLMRuntimeConfig`）+ 一个 `ConfigStore`（负责读/写
  `data/config.yaml` + `.env`）。
- 启动：`ConfigStore` 读 `.env`（种子/兜底）+ `config.yaml` → 填充 `LLMRuntimeConfig` 单例。
- `LLMService` 改为读 `LLMRuntimeConfig`（构造函数注入 `runtime_config=`），不再直接读静态
  `settings.*`；当 base_url / api_key 变化时**失效缓存的 SDK client**（下次调用重建）。
- `api/config.py` 的 GET（`llm_provider`/`llm_model`/`has_api_key`）改读运行时配置；
  `eval/runner.py` 的模型名 label 同步改读运行时配置。
- 语义：`provider` + 显式 `model`/`base_url` 存 yaml；`sdk_type` 与默认 base_url 由
  `provider` 经预设表解析得出（`custom` 供应商 sdk_type 固定 `openai`）。

### API 合约

- **`GET /api/config`**（形状不变，字段来源改为运行时配置）：
  `llm_provider` / `llm_model` / `has_api_key` 照旧，新增 `api_key_hint`（如 `sk-…abcd`，
  只回尾号 4 位，永不回完整 key）。
- **`GET /api/providers`**（已有，扩展）：内置预设 + `config.yaml` 里的自定义供应商
  （id/name/base_url），供前端下拉框。
- **`PUT /api/llm-config`**（新增）：更新 LLM 生效配置。字段 `llm_provider`、`llm_model`、
  `base_url`、`api_key`、`custom_providers`。写入 yaml + `.env` + 刷新内存运行时配置。
  `api_key` 用**三态**：`null`=保持、非空=覆盖、`""`=清除。
- **`POST /api/llm/models`**（新增，后端代理）：body `{provider, base_url?, api_key}` →
  用对应 SDK `models.list()` 拉取 → 返回模型 id 列表。8s 超时，失败返回明确错误。
- **`POST /api/llm/test`**（新增，后端代理）：body `{provider, base_url?, api_key, model}` →
  发一个 `max_tokens=1` 的最小 chat 请求 → 返回 `{ok, latency_ms, error?}`。10s 超时；
  401=key 错、404=模型不存在等错误信息透传。
- 代理端点用**请求里未保存的表单值**（不是已存配置），以便"填 key → 拉列表 → 测 → 保存"一气呵成。

### 前端交互（ModelSettings 表单）

- 供应商下拉：内置预设 + 自定义供应商 + 「添加自定义供应商（名字 + URL）」。
- Key：密码输入框（掩码），显示"已配置（尾号 abcd）"，留空=保持，另有「清除」入口。
- 模型：下拉（拉取的列表）+ 可自由手输（预填预设 default_model）。
- 按钮：「拉取模型列表」「测试连接」「保存」。
- 保存后 `chatStore`/`appStore` 的 provider/model 即时刷新（无需重启）。

### 迁移

- **不自动迁移**：旧的 `LLM_PROVIDER`/`LLM_MODEL`/`LLM_BASE_URL` 弃用；用户在新 UI 里重选
  一次。`LLM_API_KEY` 仍被读取作兜底。

## Testing Decisions

### 测试原则

- 只测外部行为：测"改配置后下一次调用生效 / client 是否重建 / yaml+env 是否正确往返 /
  代理端点返回是否符合预期"，不测内部循环写法。
- 复用现有注入缝：假 SDK client（`test_llm_retry.py` 的 `FakeStream`/`FakeChunk`）、
  `TestClient` + 内存 DB、临时目录读写文件。

### 测试缝

| 缝 | 位置 | 测试内容 | 参照 |
|---|---|---|---|
| S1 运行时配置注入 | `LLMService(runtime_config=FakeRuntimeConfig)` | 改 provider/key/model 下一次调用生效；base_url/key 变更后 client 失效重建 | `test_llm_retry.py` 的假 client |
| S2 ConfigStore 持久化 | `ConfigStore` + 临时目录 | yaml 往返、`<PROVIDER>_API_KEY`、`LLM_API_KEY` 兜底、自定义供应商列表 | `test_relative_dates.py` 的纯函数风格 |
| S3 HTTP + 代理 | `TestClient` + 注入假 SDK client | GET/PUT /config（key 尾号 + 三态）、POST /api/llm/models、POST /api/llm/test（成功/401/404/超时） | `test_tool_runtime.py` 的 `tool_logs_client` |
| S4 前端 | 无测试框架 | 手动冒烟 | — |

### 测试文件

- `backend/tests/test_llm_config.py` — S1 + S2（运行时配置、ConfigStore 往返、client 失效）。
- `backend/tests/test_llm_config_api.py` — S3（config/providers/models/test 端点）。

### 回归

`test_llm_retry.py`（llm_service 改动）、`test_agent_tools.py` / `core/test_agent_tools.py`
（Agent 经 LLMService 间接受影响）必须全绿。

## Out of Scope

- **vision / embedding 的 UI 配置**（继续留 `.env`；embedding 失败仍优雅降级 FTS5）。
- **DB 表存 LLM 配置**（见 ADR-0001，明确不做）。
- **key 加密 / 系统钥匙串**（本地单用户桌面应用，明文与现有 `.env` 同级）。
- **多个命名供应商的管理界面增强**（本版已支持"自定义供应商列表"，但不做拖拽/分组等）。
- **金额换算 / token 成本**（是 Workflow G 的领域，且本版不做金额）。
- **provider 市场 / 在线下载供应商列表**（内置预设 + 手填自定义即可）。

## Further Notes

### 改动清单

| 文件 | 改动 |
|---|---|
| `backend/services/llm_config.py`（新建） | `LLMRuntimeConfig`（可变）+ `ConfigStore`（yaml + .env 读写）+ 单例 |
| `backend/services/llm_service.py` | 读 `runtime_config` 而非 `settings.*`；构造函数注入 `runtime_config=`；base_url/key 变更时失效缓存 client |
| `backend/api/config.py` | GET /config 读运行时配置 + 新增 `api_key_hint`；GET /providers 合并自定义供应商 |
| `backend/api/llm_config.py`（新建） | `PUT /api/llm-config`、`POST /api/llm/models`、`POST /api/llm/test` |
| `backend/main.py` | 启动时初始化 `ConfigStore` + `LLMRuntimeConfig`；注册 llm_config 路由 |
| `backend/eval/runner.py` | 模型名 label 读运行时配置 |
| `data/config.yaml`（新建/种子） | 默认生效配置 |
| `frontend/src/components/settings/ModelSettings.tsx` | 只读 → 可编辑表单 |
| `frontend/src/stores/appStore.ts` | config 增 `apiKeyHint`、自定义供应商/模型列表状态 |
| `frontend/src/services/api.ts` | `fetchProviders`/`fetchModels`/`testConnection`/`updateLlmConfig` |
| `frontend/src/types/index.ts` | 新类型 |
| `frontend/src/i18n/translations.ts` | 新文案 |
| `backend/tests/test_llm_config.py`（新建） | S1 + S2 |
| `backend/tests/test_llm_config_api.py`（新建） | S3 |

### 验证命令

```bash
cd backend && python -m pytest tests/test_llm_config.py tests/test_llm_config_api.py -v
cd backend && python -m pytest tests/test_llm_retry.py tests/test_agent_tools.py tests/core/test_agent_tools.py -v
```

### 运行方式

```bash
# 改配置后（无需重启）验证：
curl http://127.0.0.1:8722/api/config          # 看 llm_provider/llm_model/has_api_key/api_key_hint
curl -X POST http://127.0.0.1:8722/api/llm/test \
  -H "Content-Type: application/json" \
  -d '{"provider":"custom","base_url":"https://xxx/v1","api_key":"sk-...","model":"deepseek-chat"}'
```
