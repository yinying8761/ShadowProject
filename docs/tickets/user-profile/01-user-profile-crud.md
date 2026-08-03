# 01 — 画像读写：用户能在设置里创建和编辑自己的画像

**What to build:** UserProfile 数据模型 + REST API + 前端设置表单。用户在设置面板填写名字、性别、身份、与 AI 的关系、自述，保存后刷新页面数据不丢失。每个角色可拥有独立画像，无画像时 fallback 到默认行。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] UserProfile 数据模型创建（独立表，character_id FK，nullable）
- [ ] 数据库表注册 + init_db 引入
- [ ] `GET /api/user-profile?character_id=xxx` 返回画像 JSON，无对应角色画像时 fallback 到默认行
- [ ] `PUT /api/user-profile?character_id=xxx` 创建/更新画像
- [ ] 种子数据：启动时创建 `character_id=NULL` 的默认画像行（user_name="User", user_relationship="friend"）
- [ ] 前端 TypeScript 类型定义（UserProfile 接口）
- [ ] 前端 API client（`fetchUserProfile`, `updateUserProfile`）
- [ ] SettingsPanel 新增画像编辑表单（名字 / 性别下拉 / 身份 / 关系 / 自述 textarea）
- [ ] 切换角色时表单自动加载对应角色的画像
- [ ] Seam 1 测试：`PromptManager.build_system_prompt()` 接收 user_profile dict 正确渲染
- [ ] Seam 2 测试：API 端点 CRUD + fallback 逻辑
