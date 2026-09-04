# LLM Config UI — 审查修复清单

> 日期：2026-08-19 | 关联 spec：`docs/specs/llm-config-ui.md` | ADR：`docs/adr/0001-llm-config-layout.md`
>
> 审查固定点：`origin/master`（`b785ba1`）之后的 5 个未推送提交（#29–#32 + docs）。
> 本文只记录需要修复的两个问题（US13 与 base_url），其余审查结论见对话记录。

---

## 问题 1（关键）：US13 供应商切换时 key 串写

**现象**：切换到新供应商时留空 key 字段（「留空保持」），上一个供应商的 key 会被写进**新**供应商的 `<PROVIDER>_API_KEY`，且内存里的 key 也保持旧值 → 下次调用用旧 key 打新接口，401。

### 根因（三处串联）

1. `frontend/src/components/settings/ModelSettings.tsx:138`

   ```ts
   api_key: apiKey.trim() ? apiKey.trim() : null, // null = keep existing key
   ```

2. `backend/api/llm_config.py`（`update_llm_config`）——`api_key is None` 时跳过赋值，`runtime_config.api_key` 仍存**旧供应商**的 key：

   ```python
   if data.api_key is not None:
       runtime_config.api_key = data.api_key  # "" clears, else sets
   ```

3. `backend/services/llm_config.py`（`ConfigStore.save`）——用**新** provider 算 env 变量名，把旧 key 写进去：

   ```python
   env_key = _provider_env_var(cfg.provider)   # 新供应商的 <ID>_API_KEY
   if cfg.api_key:                              # 旧 key，truthy
       set_key(str(self._env_path), env_key, cfg.api_key)
   ```

### 修复

`backend/api/llm_config.py` 的 `update_llm_config`，放在 `config_store.save()` 之前：

```python
    if data.api_key is not None:
        runtime_config.api_key = data.api_key  # "" clears, else sets
    else:
        # api_key null = 保持 → 按（可能已切换的）新供应商重新解析它自己的 key，
        # 而不是沿用上一个供应商的旧 key（US13）。provider 未变时是幂等 no-op。
        runtime_config.api_key = config_store.get_provider_key(runtime_config.provider)
```

（`config_store` 已在该文件顶部 import。此分支在 `runtime_config.provider` 更新之后执行，故用的是新 provider。）

---

## 问题 2（硬性偏离）：内置供应商默认 base_url 被冻结进 config.yaml

**现象**：保存一个**内置**供应商时，其预设默认 base_url（如 `https://api.deepseek.com/v1`）会被写进 `data/config.yaml`，违背 ADR「内置预设硬编码在 `config.py`、只有自定义供应商进 yaml」。后果：以后 `config.py` 里改预设 base_url，已保存过的用户收不到（被 yaml 里的旧值覆盖）。

### 根因

1. `frontend/src/components/settings/ModelSettings.tsx:60` 对内置供应商也取到预设 base_url：

   ```ts
   const resolvedBaseUrl = providers.find((p) => p.id === provider)?.base_url ?? '';
   ```

2. `ModelSettings.tsx:137` 无条件把它发出去：

   ```ts
   base_url: resolvedBaseUrl,
   ```

3. 后端 `update_llm_config` → `runtime_config.base_url = data.base_url`；`ConfigStore.save` 里 `if cfg.base_url:` 恒真 → 写入 yaml。

### 修复

前端 `ModelSettings.tsx`，内置供应商不传 base_url：

```ts
const handleSave = async () => {
  const isCustom = providers.find((p) => p.id === provider)?.is_custom;
  await api.updateLlmConfig({
    llm_provider: provider,
    llm_model: model,
    // 内置供应商：传空串（后端 save 里 `if cfg.base_url` 为假 → 不写入 yaml），
    // 预设默认 base_url 由 config.py 的 PROVIDER_PRESETS 在 resolve 时提供；
    // 仅自定义供应商才写它自己的 base_url。
    base_url: isCustom ? resolvedBaseUrl : '',
    api_key: apiKey.trim() ? apiKey.trim() : null,
    custom_providers: customProviders,
  });
  ...
};
```

> 注意用 `''` 而非 `null`：后端 `base_url is None` 是「保持」（会残留上一次的 base_url），`''` 才会在 `save()` 里被跳过、并让 `get_base_url()` 回落到预设默认值。

---

## 修复后建议补的测试

1. 切换供应商 + `api_key=null`（留空）时，新供应商的 `<NEW_PROVIDER>_API_KEY` 不被写成旧 key，且 `runtime_config.api_key` 重新解析为新供应商自己的 key。
2. 保存一个内置供应商后，`config.yaml` 不含 `base_url`（预设默认值不落 yaml）。
