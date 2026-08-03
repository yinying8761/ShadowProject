# 02 — 画像生效：AI 在对话中使用用户画像

**What to build:** Agent 每次对话前加载用户画像，PromptManager 将画像以键值格式注入系统提示词。用户修改画像后下一句话即时生效。更新画像时自动 upsert 一条 Memory（source=user_stated），确保记忆检索也能命中画像信息。

**Blocked by:** 01 — 画像读写

**Status:** ready-for-agent

- [ ] `PromptManager.DEFAULT_SYSTEM_PROMPT` 模板：`## About The User` 段改为 Jinja2 条件渲染（有 user_profile 用键值，没有回退旧格式）
- [ ] `build_system_prompt()` 新增 `user_profile: dict | None` 参数
- [ ] `Agent.run()` 在构建 system prompt 前加载 UserProfile（取 character_id 对应画像，无则 fallback 默认）
- [ ] PUT 画像时联动 Memory：upsert 一条 `source=user_stated`、`memory_type=user_fact`、`importance=8` 的记忆
- [ ] 再次 PUT 修改 bio 时更新已有记忆（content 相似度检测），不新增
- [ ] Seam 3 测试：Agent 加载 UserProfile 并正确传入 build_system_prompt
- [ ] Seam 4 测试：画像 Memory 联动（创建 + 更新去重）
- [ ] 手动冒烟验证：改画像 → 下一句话 AI 用新名字 → 问"还记得我是谁吗"AI 能找到画像
