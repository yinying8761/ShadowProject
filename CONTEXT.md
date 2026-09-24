# CONTEXT.md — ShadowProject Architecture & Conventions

> Living, agent-facing context document. Read this before working on the code.
> It is the canonical summary of the current architecture; specs and tickets live
> in `docs/specs/` and `docs/Tickets/`. Non-trivial design decisions go to
> `docs/adr/` (see `docs/agents/domain.md`; ADR-0001…0004 so far).

## 1. What this is

ShadowProject is a **desktop AI companion** (角色扮演 AI 伙伴): an Electron +
React + TypeScript frontend over a Python FastAPI backend. The user configures
one or more roleplay characters (角色); the app streams chat through a WebSocket,
gives the LLM a set of tools (file read/write, web search, screen look, memory),
and layers on a memory system, voice synthesis, proactive companioning, and a
daily greeting.

| Layer | Tech |
|---|---|
| Frontend | Electron + React + TypeScript + Tailwind + Zustand |
| Backend | Python FastAPI + WebSocket + SQLAlchemy 2 (async) + aiosqlite |
| AI | Anthropic / OpenAI / DeepSeek / Qwen / Zhipu / Moonshot (multi-provider) |
| Voice | GPT-SoVITS (optional, external) |
| Search | DuckDuckGo / SearXNG / Bing (via `ddgs`) |

Backend serves on `127.0.0.1:8722`; the frontend dev server is `:16173`; the
Electron renderer talks to `8722` directly (`ws://<host>:8722/ws/chat/<conversation_id>`).

## 2. Repo layout

```
backend/
  main.py                 # FastAPI entry: register_tools(), lifespan, singleton wiring
  config.py               # pydantic-settings; provider/vision/embedding/search presets
  database.py             # async engine, Base, ADDITIVE_MIGRATIONS, init_db()
  core/                   # agent orchestration (no HTTP)
    agent.py              # Agent: main LLM ↔ tool loop (the orchestrator)
    tool_registry.py      # ToolRegistry: name→{description,parameters,handler,require_approval}
    tool_runtime.py       # ToolRuntime: wraps ToolRegistry (tracing/sandbox/retry/circuit-breaker)
    circuit_breaker.py    # pure CircuitBreaker state machine
    conversation_manager.py  # message persistence + sliding-window context + summarization
    prompt_manager.py     # system-prompt rendering (Jinja2) + relative dates + greeting prompt
    sub_agent.py          # SubAgent base (independent LLM + tool loop)
    search_agent.py       # SearchAgent(SubAgent) — implements the `research` tool
    router_agent.py       # RouterAgent — RESERVED placeholder (multi-agent routing)
    router.py             # search-intent keyword router (advisory; ADR-0003)
    greeting_orchestrator.py  # daily-greeting orchestration
  services/               # IO / external integrations
    llm_service.py        # LLM abstraction (chat_sync + stream_chat, retry-wrapped)
    retry.py              # pure exponential-backoff retry helper
    log_hub.py            # log bus: ring buffer + stdout tee + rotated file (ADR-0002)
    command_executor.py   # debug-console whitelist commands (clear/status/mcp/config)
    mcp_manager.py        # MCP client manager (connect/health-check/reconnect/tools)
    memory_service.py     # MemoryService facade → store/retriever/extractor (singleton)
    memory_store.py       # FTS5 + CRUD + prune
    memory_retriever.py   # hybrid search (FTS5 + embedding RRF + weighting)
    memory_extractor.py   # LLM memory extraction + dedup + store
    tool_trace_store.py   # async writer for tool_runs
    embedding_service.py  # embeddings (API or local fastembed)
    vision_service.py     # legacy screen-understanding vision (fallback to MCP)
    screen_capture_gate.py # gate that asks approval before capturing screen
    message_augmenter.py  # search hint + POI injection before agent.run (ADR-0003)
    tts_service.py, weather_service.py, location_service.py
    proactive_watcher.py  # idle/scheduled proactive triggers (TierConfig state machine)
    proactive_session.py  # per-WS proactive companion session
    formatters/           # provider-specific message formatting (anthropic_formatter)
  api/                    # FastAPI routers
    chat.py               # POST /api/chat/send + WS /ws/chat/{id} (the big one)
    logs.py               # WS /ws/logs debug channel: history + live tail + commands
    character.py, conversation.py, group.py, config.py, user_profile.py, tts.py, tool_logs.py
  models/                 # SQLAlchemy ORM: character, conversation, group, message, memory,
                          #   user_config, user_profile, tool_run
  tools/                  # tool handlers: file_tools, search_tools, memory_tools,
                          #   screen_tools, time_tools
  eval/                   # hand-annotated test cases + runner
  tests/                  # pytest suite (mirrors core/services seams)
frontend/
  electron/               # main.cjs (entry), preload*.cjs, floating.html
                          #   F12 → real DevTools (dev + packaged); Ctrl+Shift+D → log panel + [cmd]
  src/
    components/{shell,chat,character,settings,debug}/  # React components
    hooks/                # useWebSocket, useChat, useCharacters, useTTS, useGeolocation, ...
    stores/               # Zustand: appStore, chatStore
    services/             # api.ts (REST), tts.ts
    i18n/                 # zh/en translations
scripts/                  # dev.bat / install.bat / setup-tts.bat
docs/
  specs/                  # PRDs (mark `(done)` once tickets are written)
  Tickets/                # implementation tickets (see §6)
  agents/                 # issue-tracker.md, domain.md (this doc's consumers)
  adr/                    # architecture decision records (0001–0004)
```

## 3. Backend: the runtime shape

### 3.1 Tool system (most important for agent work)

```
ToolRegistry (core/tool_registry.py)   — pure registry, no IO
  register(name, description, parameters: JSONSchema, handler, require_approval)
  get_handler / get_tool_definitions / needs_approval / unregister / dispatch

ToolRuntime (core/tool_runtime.py)     — wraps ToolRegistry, SAME public surface
  dispatch(name, arguments, *, conversation_id=None) -> str
  adds, in order: schema validation (Workflow F) → circuit breaker →
  retry → sandbox timeout → handler → trace to tool_runs
```

- **10 first-party tools** registered in `main.py::register_tools()`:
  `read_file`, `write_file`, `list_directory`, `search_files`, `get_current_time`,
  `see_screen`, `fetch_url`, `research` (SearchAgent), `search_memory`, `save_memory`.
- **MCP tools** are registered by `McpManager` as `mcp__<server>__<tool>` with
  `parameters = inputSchema`, `require_approval=True`.
- **Handler contract**: `async def handler(**kwargs) -> str`. On failure a handler
  returns `json.dumps({"error": "..."})` (NOT raising, usually); `dispatch` also
  catches raised exceptions and wraps them the same way.
- **Error envelope** (critical convention): tool results are JSON strings. A dict
  with an `"error"` key means failure. `ToolRuntime.dispatch` inspects the returned
  string to set `success=False`; `Agent._execute_tools_with_approval` unwraps
  `{"error": ...}` into `is_error=True` for the `tool_result` event.
- **ToolRuntime feature flags** (constructor kwargs): `enable_tracing=True`,
  `enable_sandbox=True`, `enable_circuit_breaker=False` (enabled only in
  `main.py`), `enable_rate_limit=False` (reserved), `circuit_threshold=5`,
  `circuit_open_sec=60`. Tests pass `enable_tracing=False, enable_sandbox=False`.
- **Per-tool configs**: `sandbox_config={"timeout_sec": float}`,
  `retry_config={"max_retries": int, "retryable_exceptions": tuple}`. Idempotent
  tools retry once by default; write tools never retry.

### 3.2 Agent loop (`core/agent.py`)

`Agent.run(...)` is an async generator yielding events (see §5). Chat mode flow:

1. Load character, conversation summary, memories (`memory_service.search`),
   location context, user profile → assemble `messages` (system + history).
2. `tools = tool_registry.get_tool_definitions()`.
3. `max_tool_rounds = 5` (chat) or `1` (proactive). Each round:
   - `stream_chat(messages, tools)` → yields `token` / `tool_use` / `error`.
   - If tool calls: append assistant `tool_calls` message, then
     `_execute_tools_with_approval(blocks, approval_callback, character_id)`,
     append each `tool` result message, continue.
   - If no tool calls: persist assistant message, yield `done`, return.
4. Round budget exhausts → persist whatever was produced, yield `done`.

`_execute_tools_with_approval` is the tool-execution seam: it checks
`needs_approval`, calls the `approval_callback`, and — **gotcha** — injects
`character_id` into `save_memory` / `search_memory` arguments before dispatch
(the LLM never sees `character_id`; the agent adds it from context).

### 3.3 Sub-agents

`SubAgent` (base) runs an independent LLM + tool loop with its own `ToolRegistry`
(no approval, no memory). `SearchAgent(SubAgent)` implements the `research` tool
and is registered as a plain handler. `RouterAgent` is a reserved placeholder for
future multi-agent routing; `Agent._resolve_handler()` is the interception point.

### 3.4 Memory pipeline

`MemoryService` is a backward-compatible singleton that delegates to three deep
modules sharing one `MemoryStore`:
- **MemoryStore** — FTS5 virtual table setup, embedding pack/unpack, CRUD, prune.
- **MemoryRetriever** — hybrid search: FTS5 → embedding cosine → RRF merge →
  importance/recency weighting → character filter.
- **MemoryExtractor** — LLM extraction, split into `load_window` (messages in a
  time window) → `extract` (**one** LLM call → candidate items, no storage) →
  `store` (dedup, embed, persist). `extract_and_store` is still the 1:1 / daily
  entry point (all three in a row); group chat calls `extract` once and `store`s
  the same batch **once per member** (ticket #55).

Memories are per-`character_id`, and dedup is scoped the same way
(`MemoryStore.find_similar(..., character_id=)`): the same fact recorded for A
must still be recorded for B — cross-character dedup is memory pollution.
Sources: `user_stated` / `ai_summarized`.
Background extraction pushes to a module-level notification queue; `Agent.run`
pops it and emits `memory_updated`.

### 3.5 Resilience (Workflow E, already implemented)

- `services/retry.py` — pure exponential-backoff `retry(fn, max_retries, base_delay,
  retryable, sleep)`; `is_retryable` classifies 429/5xx/transient vs 4xx.
- `LLMService` wraps `chat_sync` / stream establishment in retry; mid-stream
  breaks are NOT retried (partial text preserved via `partial_error`).
- `core/circuit_breaker.py` — pure `CircuitBreaker` (CLOSED→OPEN→HALF_OPEN),
  injectable `clock`, per-tool instance in `ToolRuntime`.
- `McpManager` — per-server isolation, periodic health check + auto-reconnect +
  circuit-breaker reset.

### 3.6 Persistence

- SQLite via `sqlite+aiosqlite`; `data/companion.db`; async sessions from
  `database.async_session`.
- `Base.metadata.create_all` creates missing tables only. **Schema evolution uses
  `ADDITIVE_MIGRATIONS`** in `database.py` — a list of `(table, column, decl)`
  probed with `PRAGMA table_info` and `ALTER TABLE ADD COLUMN` on miss. Add new
  columns there, never assume `create_all` alters existing tables. For a
  **constraint** change SQLite cannot do additively, there is a second,
  explicitly non-additive path: `database.ensure_group_schema` (probe → backup →
  rebuild → rename, one transaction — ADR-0004). `run_migration_sequence` is the
  single authoritative order (`create_all` → additive → rebuild); `init_db`,
  tests and `scripts/check_group_migration.py` all go through it.
- Models: `CharacterProfile`, `Conversation` (`character_id` nullable since
  ADR-0004; group conversations carry `group_id` + `last_extract_at` instead),
  `Group`/`GroupMember` (fixed member set, `position` = speaking order),
  `Message` (has `tool_calls`, `tool_call_id`, `token_count`, `speaker_id`),
  `Memory`, `UserProfile`, `UserConfig`, `ToolRun` (tracing, has `retry_count`).
- **Referential integrity is service-layer, not DB-enforced** (ADR-0005): FK
  enforcement is off, so models carry **no `ondelete=`** — the declaration would
  be a lie. Deleting a parent is an explicit multi-table operation
  (`api/character.py::_delete_character_dependents`,
  `api/conversation.py::delete_conversation`); a new table referencing a
  character or conversation must be added there. `PRAGMA foreign_key_check` is
  the audit; `scripts/cleanup_orphan_data.py` is the one-shot cleanup.
- **A turn needs an existing conversation**: chat HTTP → 404,
  `/ws/chat/{id}` → `error{code:"conversation_not_found"}` + close 4404,
  re-checked every turn (a socket can outlive the conversation it was opened
  for). The frontend then creates a fresh conversation and resends
  (`services/conversationRecovery.ts`) — nothing is ever persisted against a
  conversation that does not exist.

### 3.7 Group chat (spec shipped: Phase 1 + entities + turns + UI + memory catch-up)

`docs/specs/group-chat.md` defines a **群** (fixed character set + shared
transcript) whose conversations hold multiple speakers. Delivered so far: the
shared timestamped-transcript renderer (Phase 1 — `core/transcript.py`, feeding
both LLM context and the history API), the group data model + rebuild migration
(ticket #51/ADR-0004), and the group entity API (`api/group.py`: create / edit /
list / create group conversation), the turn orchestration (ticket #53), the UI
(ticket #54: `activeGroup` session kind — ADR-0007), and the memory catch-up
(ticket #55, below). Shape:

- **Serial** turn orchestration (shipped, ticket #53): `core/group_turn.py`
  decides who speaks when — member order, `<silent>` skips (dropped, never stored
  or pushed), a **chain budget** (6 role messages per user turn), and interjection
  handling (a new user message queues, the in-flight stream finishes, the old turn
  winds down, queued messages merge into one new turn with a fresh budget). It is
  pure logic: the "run one member's turn" callable is injected.
  `services/group_chat.py` is the wiring — one character's turn goes through the
  shared `Agent` (`persist_reply=False`, so a skip is never stored; the driver
  persists with `speaker_id` and pushes `group_message`), user messages are
  persisted/acked by the driver, and one conversation never runs two turns at once.
- User interjections never interrupt an in-flight reply — they queue and become
  a **new turn** (budget reset); several interjections merge into one turn.
- Daily greeting + proactive are **disabled** for group conversations; group TTS
  is disabled behind a reserved switch.
- Opening a group conversation schedules a background **extract → compact**
  (`services/group_memory.py`, ticket #55): `batch_windows` splits
  `[last_extract_at, now)` into at most `MAX_BATCHES` day-windows (a long
  absence merges its oldest days), each extracted **once** with the
  `group_user_facts` prompt (user facts only — never a character's opinions),
  then `store`d once per member with `created_at` = that day. The anchor only
  advances past windows that really succeeded, and compact runs **after**
  extraction — a failed extraction must not delete unextracted messages.
  `schedule_catch_up` dedups in flight per conversation (reconnect / several
  windows do not redo it), and the task is not tied to the socket. Each memory carries
  its own `occurred_on` (parsed off the transcript) so a merged window still dates
  correctly; the anchor only advances past windows that succeeded, and compact runs only
  when nothing failed. ADR-0008 records the trade-offs.
- Memory stays per-character; one extraction batch is stored once per member
  (prompt limited to user facts, never a character's opinions).
- Schema: new `Group` / `GroupMember`, `Conversation.group_id` +
  `last_extract_at`, `Message.speaker_id`, and `Conversation.character_id`
  becomes **nullable** (one-time rebuild migration, not additive).
- Refusing work for a conversation that no longer exists, and cleaning up after
  a deleted character (orphan conversations/messages/memories), landed with the
  review fixes — see ADR-0005 and
  `docs/analysis/group-chat-review-and-orphan-data.md`.
- Speaker resolution (Phase 1, already live): user → that conversation's
  `UserProfile.user_name` (else the `character_id=NULL` default, else 用户);
  1:1 assistant → the conversation's character; group assistant →
  `Message.speaker_id` looked up as a character name (by the message's own
  speaker, **not** by current group membership — membership is editable). Tool-role
  messages and tool-call carriers never carry a prefix.

## 4. Frontend shape

- **Zustand stores**: `appStore` (characters, config, layout mode compact/full,
  overlays) and `chatStore` (messages, streaming state, pending approval, tool
  status strip, WS bridge handle).
- **Single global WebSocket** lives in `hooks/useWebSocket.ts`
  (`useWebSocketBridge`), used once at the `App` root. It publishes
  `sendMessage` / `sendApprovalResponse` / `sendJson` into `chatStore` so other
  components send without owning a connection. Reconnects up to 5×.
- **Layout**: `LayoutProvider` + `TitleBar` + `CompactView` (desktop companion)
  vs `FullView` (chat panel + sidebar), toggled by `appStore.layoutMode`.
- **Overlays** shared across modes: `ApprovalDialog`, `HistoryOverlay`,
  `SettingsPanel`, `MemoryViewer`, `CharacterEditor`.
- **Group chat UI** (`docs/Tickets/group-chat/issues/05-group-chat-ui.md`):
  `appStore.activeGroup` is a third session kind next to "active character".
  `enterGroup` closes settings, remembers the current layout mode and forces `full`;
  `leaveGroup` restores it — and `setLayoutMode` refuses `compact` while a group is
  active, because compact has no group view. `FullView` swaps `ConversationSidebar`
  for `GroupSidebar` (member list in speaking order, the group's conversations, 退出)
  and both sidebars share `ConversationList` (select / rename / delete / 新对话) —
  only the loader differs. Group messages carry `speaker_id`: the history path resolves
  names server-side, live `group_message` events are labelled client-side, and
  `MessageBubble` renders a per-speaker name + stable colour (`utils/speakerStyle.ts`).
  Group turns are not token-streamed, so the group `done` event (no `message_id`)
  is what clears the "thinking" state. TTS stays off in groups behind `useTTS`'s
  reserved `GROUP_TTS_ENABLED` switch.
- **i18n** via `i18n/translations.ts` + `useTranslation` (zh default).

## 5. Protocols (the contracts you must not break)

### 5.1 Agent event stream (`Agent.run` yields dicts)

| type | fields | meaning |
|---|---|---|
| `token` | `content` | streamed text delta |
| `tool_use` | `id`, `name`, `arguments` | LLM requested a tool call |
| `tool_result` | `name`, `result`, `is_error`, `denied?` | tool finished (or was denied) |
| `done` | `message_id`, `partial_error?`, `proactive?`, `daily_greeting?` | assistant message persisted |
| `done` (group) | `group: true` | group turn burst finished (`persist_reply=False` replies carry `content` + `deferred: true`, no `message_id`) |
| `group_message` | `message_id`, `character_id`, `content` | one group member's message was persisted |
| `error` | `message` | unrecoverable error |
| `message_ack` | `client_message_id`, `message_id` | user message persisted (id swap) |
| `memory_updated` | `count` | background memory extraction finished |
| `llm_retry` | `attempt`, `max_retries` | LLM retry in progress (transient, chat path only) |
| `proactive_skip` / `daily_greeting_skip` | | proactive/greeting aborted |

### 5.2 WebSocket inbound (client → server)

`{type}` is one of: `chat` (`content`, `character_id`, `force_vision?`,
`client_message_id?`), `approval_response` (`request_id`, `approved`),
`daily_greeting`, `update_location` (`lat`, `lng`).

For a **group** conversation `character_id` is not used (the room has no single
speaker); a `daily_greeting` is answered with
`{type:"daily_greeting_skip", reason:"group_conversation"}` and no proactive
session is started. Group replies are delivered whole (no `token` stream): a
`<silent>` skip must never be pushed, so the driver decides after the stream ends.

The debug channel `/ws/logs` (ADR-0002) is separate from the chat socket and
takes `renderer_log` (`level`, `message`) and `command` (`command`, one of the
whitelisted debug commands). See §2 `api/logs.py`.

### 5.3 Approval flow

Tools with `require_approval=True` (`write_file`, `see_screen`, all MCP) trigger
`approval_callback` → server sends `{type:"approval_request", request_id, name,
arguments}` → client answers `approval_response` → callback resolves the future
(120s timeout ⇒ deny). Denied tools yield `tool_result` with `denied: True` and
**do not** reach `ToolRuntime.dispatch` (so denials never count as tool failures
or circuit-breaker failures).

## 6. Conventions to follow

- **Seams / dependency injection** (highest-leverage convention): everything is
  constructor-injected so tests can substitute fakes —
  `Agent(llm_service=..., tool_registry=...)`, `ToolRuntime(registry=...)`,
  `LLMService(clients=..., sleep=...)`, `ToolTraceStore(session_factory=...)`,
  `CircuitBreaker(clock=...)`, `McpManager(registry)`. Prefer existing seams;
  add a new one at the highest point, and keep the number of seams low.
- **Module-level singletons** are lazily imported inside functions to avoid
  import cycles: `settings`, `async_session`, `memory_service`,
  `core.tool_registry.tool_registry`, `core.tool_runtime` (via `main.register_tools`).
- **Async everywhere** in backend handlers/services; use `asyncio.to_thread` for
  blocking calls on the event loop.
- **Never break the `dispatch -> str` contract**: `ToolRuntime.dispatch` returns
  a JSON-encodable string, not raises (except sandbox/retry internals which it
  catches). Schema validation (Workflow F) must follow this too.
- **Error messages are user/LLM-facing**: keep them short and self-correctable;
  the LLM sees `tool_result.result` and is expected to fix its args.
- **Tests**: `pytest` + `pytest.mark.asyncio`; in-memory DB via
  `create_async_engine("sqlite+aiosqlite://")` + `Base.metadata.create_all`;
  fakes: `FakeLLMService` (yields preset events), `FakeClock`, `RecordingSleep`,
  `monkeypatch` on `memory_service.search`. Test external behavior only, not
  implementation detail. See `backend/tests/test_tool_runtime.py`,
  `test_agent_tools.py`, `test_circuit_breaker.py`, `test_tool_retry.py` for
  the canonical patterns.
- **One transcript renderer**: `core/transcript.py` — the LLM context and the
  history view must render
  messages through the *same* timestamped-transcript formatter (§7 时间戳对话记录)
  — never a second copy of the formatting rules. `render_transcript` returns the
  text lines (LLM context); `render_lines` returns speaker + text together (the
  history API needs both, and renders once).
- **Specs & tickets**: a spec (`docs/specs/<name>.md`) describes the PRD; tickets
  (`docs/Tickets/<name>/issues/NN-*.md`) are the ready-for-agent checklists
  (state `Blocked by:` explicitly). The `(done)` filename suffix marks a
  workflow whose implementation is **already completed** — do not add it to a
  spec whose work is still pending.

## 7. Domain glossary (ubiquitous language)

| Term | EN | Meaning |
|---|---|---|
| 角色 | Character | `CharacterProfile`; name/gender/personality/archetype/voice |
| 会话 | Conversation | `Conversation`; owns `messages`, has `summary` |
| 会话标题 | Title | 会话的显示名。规则：AI 在首答后自动命名一次（title 仍为默认值时才生成）；用户改名后永久生效，AI 不再覆盖；AI 失败则保持默认、下次对话再试 |
| 消息 | Message | `role` user/assistant; optional `tool_calls`/`tool_call_id` |
| 记忆 | Memory | per-character; `user_fact`/`user_preference`/`important_event`; `source` user_stated/ai_summarized |
| 用户画像 | User profile | learned facts about the user, per character (or global fallback) |
| 工具 | Tool | name + description + JSON Schema `parameters` + handler + `require_approval` |
| 工具运行时 | ToolRuntime | tracing + sandbox + retry + circuit breaker + (soon) schema validation |
| Agent 主循环 | Agent loop | LLM ↔ tools for up to `max_tool_rounds` |
| 子智能体 | Sub-agent | independent LLM + tool loop (SearchAgent, RouterAgent reserved) |
| 主动陪伴 | Proactive | idle/scheduled triggers from `ProactiveWatcher`/`ProactiveSession` |
| 每日问候 | Daily greeting | first-open contextual greeting via `GreetingOrchestrator` |
| 熔断 | Circuit breaker | per-tool CLOSED/OPEN/HALF_OPEN fault isolation |
| 增量迁移 | Additive migration | `database.ADDITIVE_MIGRATIONS` column additions（约束变更走非增量重建路径，见 ADR-0004） |
| 供应商 | Provider | 官方 API 渠道 + 中转站（如 opencode-go、AIcodeMirror）；内置预设硬编码，自定义的可增删。_Avoid_: 提供方、服务商 |
| 自定义供应商 | Custom provider | 用户自建的供应商条目：名字 + base_url，存于 `config.yaml` 的 `providers:` |
| 模型 | Model | 实际调用的模型 id 字符串（如 `deepseek-chat`） |
| 模型列表 | Model list | 从供应商 `GET /v1/models` 拉取到的可用模型 id 集合 |
| 密钥 | API Key | 每供应商一个 `<PROVIDER>_API_KEY`，存于 `.env` |
| 接口地址 | Base URL | OpenAI 兼容 endpoint 根地址（形如 `…/v1`） |
| 生效配置 | Effective config | `data/config.yaml` 里的当前供应商 + 模型选择 |
| LLM 运行时配置 | Runtime LLM config | 内存中的可变配置对象；UI 写 yaml/.env 后刷新，立即生效 |
| 群 | Group | 固定角色集合 + 共享对话流；一个群可开多条「群对话」（spec: `group-chat`） |
| 群聊 | Group chat | 群内的多角色对话：成员按顺序依次发言，可跳过、可续说，每轮受链预算约束 |
| 发言者 | Speaker | 消息的说话人（用户或某角色）。群聊消息必带；1:1 由会话角色派生 |
| 链预算 | Chain budget | 每「用户轮」内角色消息总量上限（默认 6 条），到顶或全体跳过即静默 |
| 时间戳对话记录 | Timestamped transcript | 喂 LLM 与历史记录共用的统一格式：`YYYY/M/D HH:MM [说话人]: 内容`（系统本地时区） |
