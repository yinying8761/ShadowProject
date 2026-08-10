# Spec: 用户画像 — 让 AI 知道用户是谁

**状态:** `ready-for-agent`
**日期:** 2026-08-02
**来源:** `/grill-with-docs` → 迭代计划 Workflow A

---

## Problem Statement

AI Companion 目前不知道用户是谁。`user_name` 硬编码为 `"User"`，`relationship` 硬编码为 `"friend"`。用户换了角色聊天，AI 对用户的认知完全不变。用户无法告诉 AI 自己的名字、性别、身份、自述——AI 只能从对话中零散地猜测，猜不对也没办法纠正。

更关键的是：**一个用户面对不同角色时应该能有不同的人设**。对"小樱"可以说自己是大学生小明，对"工作助手"可以说自己是打工人张总。当前系统完全不支持这种分面。

## Solution

新建 `UserProfile` 模型，一条画像绑定一个角色（`character_id` FK），`character_id=NULL` 的行作为默认 fallback。提供 REST API 读写画像。系统提示词注入时，用键值格式呈现用户信息；`user_bio`（自述）始终以自然语言段落注入，同时 upsert 到 Memory（`source=user_stated`）确保记忆检索也能命中。

前端在设置面板新增用户画像编辑表单，用户可随时修改并即时生效。

## User Stories

1. As a user, I want to tell the AI my name, so that it calls me by the right name instead of "User".
2. As a user, I want to tell the AI my gender, so that it uses the correct pronouns when talking about me.
3. As a user, I want to tell the AI my occupation/identity (大学生/打工人/...), so that it understands my life context and gives relevant responses.
4. As a user, I want to write a self-introduction (bio), so that the AI knows key facts about me without me repeating them every conversation.
5. As a user, I want to define my relationship with the AI (朋友/助手和用户/...), so that the AI adopts the right tone and boundaries.
6. As a user with multiple characters, I want each character to have a different view of me (different name/identity/bio per character), so that I can maintain different personas.
7. As a user, I want my profile changes to take effect immediately in the next message, so that I don't need to restart the app.
8. As a user, I want my self-introduction to be findable via memory search, so that even when the AI searches its memories it knows these basic facts about me.
9. As a user who hasn't set up a profile for a specific character, I want the AI to fall back to my default profile, so that I don't have to configure every character.
10. As a developer, I want to test user profile injection independently of the Agent/LLM/WebSocket, so that I can verify prompt rendering without spinning up the full system.
11. As a developer, I want the UserProfile API to follow the same patterns as existing APIs (character, config), so that the codebase stays consistent and maintainable.

## Implementation Decisions

### 决策 1：数据模型 — 独立 `UserProfile` 表

新建 `user_profiles` 表，不复用 `UserConfig`（它已经是 30 列的 UI 偏好单体）。

| 列 | 类型 | 说明 |
|---|------|------|
| `id` | String(36) PK | UUID |
| `character_id` | String(36) FK → `character_profiles.id`, nullable | NULL = 默认画像 |
| `user_name` | String(50) | AI 对用户的称呼 |
| `user_gender` | String(10), nullable | `男` / `女` / `其他` / 留空 |
| `user_occupation` | String(80), nullable | `大学生` / `打工人` / `自由职业` ... |
| `user_bio` | Text, nullable | 用户自述，自由文本 |
| `user_relationship` | String(50) | 用户与 AI 角色的关系 |
| `created_at`, `updated_at` | DateTime | 时间戳 |

- 一对多：一个用户面对不同角色可有不同画像
- `character_id=NULL` 的默认行作为 fallback
- `character_id` + `user_name` 无唯一约束——同一角色可以有多个画像行（允许用户自己管理），加载时取最新一条

### 决策 2：API 设计 — 独立端点

```
GET  /api/user-profile?character_id=xxx   → 获取画像（无则 fallback 到默认）
PUT  /api/user-profile?character_id=xxx   → 创建/更新画像
```

独立端点而非嵌套在 character/config 下，因为：
- 跟 character CRUD 是不同的关注点
- 跟 UserConfig 的 singleton pattern 不同（UserProfile 是多行的）
- 未来可能需要 `GET /api/user-profile?character_id=xxx` 带 multiple results 或 DELETE

### 决策 3：注入格式 — 键值 + 自然语言混合

**基本信息用键值（清晰、结构化、AI 容易解析）：**
```
## About The User
- Name: 小明
- Gender: 男
- Identity: 大学生
- Relationship: 朋友
```

**`user_bio` 用自然语言段落（保持温度、沉浸感）：**
跟在键值块之后，不加前缀标签，直接一段文字。

角色人格格式保持自然语言（"You are 小樱, a 邻家姐姐 female..."），不做改动——键值格式会让角色失去沉浸感。

### 决策 4：Memory 联动

PUT 画像时，自动 upsert 一条 `source=user_stated` 的记忆：
- content 由后端格式化为日记体，例如："用户告诉我他叫小明，是一名大学生。他这样描述自己：我喜欢 Rust 和数学。"
- memory_type = `user_fact`，importance = 8
- 如果已存在 `user_stated` 的画像记忆（通过 content 相似度检测），更新而非新增
- 这样用户在对话中说"你还记得我是谁吗"，AI 通过 `search_memory` 也能找到画像

### 决策 5：注入位置 — 系统提示词，每次请求实时加载

UserProfile 在 `Agent.run()` 中加载（在 `build_system_prompt()` 调用前），每次请求都从 DB 读。不做缓存——用户可能随时修改画像，必须即时生效。

### 决策 6：角色设定变更的感知

角色设定变更（PUT `/api/characters/{id}`）后，下一次 `Agent.run()` 自然会读到新的 `CharacterProfile` 字段。无额外机制，因为 Agent 每次请求都 `session.get(CharacterProfile, character_id)`。

### 决策 7：前端的用户画像编辑 UI

在 `SettingsPanel` 中新增一个 section，放在 Characters 和 Model 之间。表单字段：
- 名字（text input）
- 性别（下拉：男/女/其他/不设置）
- 身份（text input + placeholder 建议）
- 与 AI 的关系（text input + placeholder 建议）
- 自述（textarea，3-5 行）

当前角色的画像和默认画像分开显示，切换角色时表单自动切换到该角色的画像。

### 决策 8：PromptManager 接口变更

`build_system_prompt()` 新增 `user_profile: dict | None` 参数。DEFAULT_SYSTEM_PROMPT 模板的 `## About The User` 段改为 Jinja2 条件渲染：

```jinja2
{% if user_profile %}
## About The User
- Name: {{ user_profile.user_name }}
{% if user_profile.user_gender %}- Gender: {{ user_profile.user_gender }}
{% endif %}{% if user_profile.user_occupation %}- Identity: {{ user_profile.user_occupation }}
{% endif %}- Relationship: {{ user_profile.user_relationship }}
{% if user_profile.user_bio %}
{{ user_profile.user_bio }}{% endif %}
{% else %}
The user you are talking to is named {{ user_name }}. Treat them as a close {{ relationship | default('friend') }}.
{% endif %}
```

## Testing Decisions

### 测试哲学

只测试外部行为，不测试实现细节。使用 fake 替代真实依赖（DB 用内存 SQLite，LLM 用假对象）。

### Seam 1 — `PromptManager.build_system_prompt()` 用户画像渲染

- **假依赖**：无（纯函数）
- **测试用例**：
  - 传入 `user_profile={user_name: "小明", user_gender: "男", user_occupation: "大学生", user_bio: "我喜欢 Rust"}` → 断言输出包含 `- Name: 小明`、`- Gender: 男`、`- Identity: 大学生`、`我喜欢 Rust`
  - 不传 `user_profile` → 断言回退到旧格式（`user_name` + `relationship`）
  - `user_bio` 为空 → 断言不渲染空段落
  - `user_gender` 为空 → 断言不渲染 Gender 行

### Seam 2 — `GET/PUT /api/user-profile` REST 端点

- **假依赖**：内存 SQLite（`aiosqlite` + `sqlalchemy` 的内存连接）
- **测试模式**：FastAPI `TestClient` + `httpx.ASGITransport`，参考角色 CRUD 的测试模式
- **测试用例**：
  - PUT 创建 → GET 返回 → 断言字段一致
  - GET 不存在的 character_id → 返回 fallback（默认行）
  - PUT 更新已有画像 → 断言字段更新
  - PUT 缺少必填字段 → 422 错误

### Seam 3 — `Agent.run()` 加载 UserProfile

- **假依赖**：Fake session（预填充 UserProfile 行）+ Fake LLM（不真正调用，记录 system prompt）
- **测试用例**：
  - 会话有 character_id + 对应画像存在 → `build_system_prompt` 收到 user_profile
  - 会话有 character_id + 无对应画像 → fallback 到默认画像
  - 无任何画像 → 使用旧格式（user_name/relationship 默认值）

### Seam 4 — 画像 Memory 联动

- **假依赖**：内存 SQLite
- **测试用例**：
  - PUT 带 user_bio → 断言 Memory 表出现 `source=user_stated` 的行
  - 再次 PUT 修改 bio → 断言旧记忆被更新（通过 content 相似度检测）而非新增
  - PUT 不带 user_bio → 不创建 memory

### 已有测试先例

项目目前使用 `backend/eval/runner.py` 作为评测框架——内存 SQLite + auto-approve callback。单元测试将使用 `pytest` + `pytest-asyncio`，遵循 `docs/specs/decompose-chat-god-module.md` 中定义的测试模式（只测外部行为、用 fake 替代真实依赖）。

## Out of Scope

- **多用户支持** — 项目是单用户桌面应用，不支持用户注册/登录
- **画像导入/导出** — 不做 JSON 导入导出
- **画像自动推断** — 不做基于对话的自动画像补全（留给未来的 Workflow）
- **画像版本历史** — 不做画像修改记录/回滚
- **角色切换时的画像迁移** — 不做"把角色 A 的画像复制到角色 B"
- **前端画像表单的实时校验** — 不做客户端校验，仅依赖后端 Pydantic 校验

## Further Notes

- 这是四个 Workflow 中的第一个（A），不依赖其他 Workflow，预计 1-2 天
- 完成后 unblock Workflow B（上下文管理）
- `UserProfile` 的 `character_id` FK 使用 `ondelete="CASCADE"`——删除角色时自动清理关联画像
- seed 时创建一条 `character_id=NULL` 的默认画像行（`user_name="User"`, `user_relationship="friend"`），确保无画像时系统仍可运行
- 前端画像表单使用 `api.fetchUserProfile(characterId)` / `api.updateUserProfile(characterId, data)` 两个新 API 函数
- 此 spec 的 GitHub Issue 对应实现任务，遵循 `docs/specs/iteration-plan-20260731.md` 中 Workflow A 的设计
