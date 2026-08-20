# 03 — 前端 ModelSettings 可编辑表单

**What to build:** 把只读的 `ModelSettings.tsx` 重写为可编辑表单：供应商下拉（含自定义）、key 输入
（掩码/三态）、模型下拉+自由文本、「拉取模型列表」「测试连接」「保存」。前端无测试框架 → 手动冒烟。

**Blocked by:** #02（需要后端端点）

**Status:** ready-for-agent

## Checklist

- [ ] `types/index.ts` 新增：

```typescript
export interface ProviderPreset {
  id: string; name: string; base_url?: string;
  default_model?: string; sdk_type?: string;
}
export interface LlmModelsResponse { models: string[] }
export interface LlmTestResponse { ok: boolean; latency_ms?: number; error?: string }
export interface LlmConfigUpdate {
  llm_provider?: string; llm_model?: string; base_url?: string;
  api_key?: string | null; custom_providers?: { id: string; name: string; base_url: string }[];
}
```

- [ ] `api.ts` 新增：`fetchProviders()`（GET /api/providers）、`fetchModels(body)`（POST /api/llm/models）、
      `testConnection(body)`（POST /api/llm/test）、`updateLlmConfig(body)`（PUT /api/llm-config）
- [ ] `appStore.ts`：config 增 `apiKeyHint?: string`；加 `providers: ProviderPreset[]`、
      `models: string[]` 及 setter（或本地组件 state，二选一，保持简单）
- [ ] `ModelSettings.tsx` 重写为表单：
  - **供应商下拉**：内置预设 + 自定义供应商 + 「＋ 添加自定义供应商」（名字 + URL 两个输入）
  - **Key**：`type=password` 掩码输入；显示"已配置（尾号 abcd）"（用 `apiKeyHint`）；
    占位"留空保持原 key"；「清除 key」按钮 → 提交 `api_key: ""`
  - **模型**：下拉（`models`）+ 可编辑自由文本（`<input list=...>` 或可写 select），
    预填 `default_model`
  - **拉取模型列表**：用当前表单 provider/base_url/key → `fetchModels`；失败提示 + 保持手填可用
  - **测试连接**：用当前表单值 → `testConnection`；显示成功/失败 + `latency_ms`/`error`
  - **保存**：`updateLlmConfig({llm_provider, llm_model, base_url, api_key: 留空则 null, custom_providers})`
- [ ] 保存成功后刷新 `appStore.config.llmProvider/llmModel/apiKeyHint`
- [ ] `i18n/translations.ts`：为「Provider / Model / API Key / 拉取模型列表 / 测试连接 / 添加自定义供应商 /
      已配置(尾号) / 留空保持原 key / 清除」等补中/英文案
- [ ] 手动冒烟：改供应商 → 拉模型列表 → 选模型 → 测试连接 → 保存 → 发一条消息确认用新模型回复；
      重启应用确认配置仍在
