# CONTEXT.md — ShadowProject Architecture & Conventions

> Living, agent-facing context document. Read this before working on the code.
> It is the canonical summary of the current architecture; specs and tickets live
> in `docs/specs/` and `docs/Tickets/`. Non-trivial design decisions go to
> `docs/adr/` (see `docs/agents/domain.md`; ADR-0001…0011 so far).

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
  adr/                    # architecture decision records (0001–0011)
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
- `McpManager` — **one actor task per server** (`_serve`): that task enters and
  exits the transport/session context managers, and the connect timeout is an
  `anyio` scope inside it, so one dead or hung server can never break the others
  (ADR-0010). The manager never cancels an actor — on timeout it sets a stop
  event and moves on; servers connect concurrently. Plus periodic health check +
  auto-reconnect + circuit-breaker reset.
  **Version isolation is per server, via its own venv**: an entry's `command` may
  point at a different interpreter by absolute path, so a server that needs a
  newer `mcp` SDK does not force one on the app (which is pinned to
  `mcp 1.8.1` by `amap-mcp-server`, and cannot move anyway because
  `mcp>=1.30` on py3.14 needs `starlette>=0.48` while `fastapi` needs `<0.47`).
  `data/mcp_servers.json` is local (gitignored); `.venv-vision/` is the vision
  server's interpreter.

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
  single authoritative order (`create_all` → additive → rebuild → 序号回填);
  `init_db`, tests and `scripts/check_group_migration.py` all go through it.
  A migration that writes data (the rebuild, the `messages.seq` backfill) makes
  `init_db` snapshot the DB file **outside** the migration transaction first.
- Models: `CharacterProfile`, `Conversation` (`character_id` nullable since
  ADR-0004; group conversations carry `group_id` + `last_extract_at` instead),
  `Group`/`GroupMember` (fixed member set, `position` = speaking order),
  `Message` (has `tool_calls`, `tool_call_id`, `token_count`, `speaker_id`,
  `seq` = the per-conversation monotonic number that fixes "who spoke first"),
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

`docs/specs/group-chat(done).md` defines a **群** (fixed character set + shared
transcript) whose conversations hold multiple speakers. Delivered so far: the
shared timestamped-transcript renderer (Phase 1 — `core/transcript.py`, feeding
both LLM context and the history API), the group data model + rebuild migration
(ticket #51/ADR-0004), and the group entity API (`api/group.py`: create / edit /
list / create group conversation / **delete**), the turn orchestration (ticket #53), the UI
(ticket #54: `activeGroup` session kind — ADR-0007), and the memory catch-up
(ticket #55, below). Shape:

- **Turn orchestration — concurrent (tickets #09/#10, ADR-0009).** `core/group_turn.py`
  is the **concurrent scheduler**, still pure logic (`speak` injected, no DB / network
  / clock): a user message starts **every** member thinking at once; each message that
  lands wakes every idle member that has not spent its cap — **except the speaker
  itself** (being woken by your own message is just 继续说 renamed; ADR-0009's rule is
  "new messages wake *the rest*"). One message per thinking round; `<silent>` produces
  nothing but still burns a round. Caps per user turn: **K** rounds per member
  (N≤4 → 3, N>4 → 2, `rounds_cap`) and **M = 9** role messages (`GROUP_MESSAGE_BUDGET`);
  the slot is **reserved before a round starts** (`stored + in-flight < M`), so a
  generation is never dropped after the fact. Nobody producing a message means no wake
  event, so an all-silent turn costs one round each. In-flight streams are never
  cancelled; convergence is "everyone at K, or nobody left to start and nothing in
  flight". `run()` awaits every in-flight round (and cancels them only if the batch
  itself is cancelled, e.g. the socket closed).
- Interjections: a new user message never interrupts an in-flight reply — the current
  batch stops opening new rounds, the in-flight members finish, and the queued
  messages merge into a **fresh user turn** (K/M reset, everyone woken).
- `services/group_chat.py` is the wiring — one character's round goes through the
  shared `Agent` (`persist_reply=False`, so a skip is never stored; the driver
  persists with `speaker_id` and pushes `group_message` with `seq`), user messages are
  persisted/acked by the driver, and one conversation still runs only one batch at a
  time. Because members generate concurrently, each `_speak` opens its own session
  and `_settle_persists` makes **all** concurrent callers wait for the same persist
  batch (otherwise a member could read a history without the user's message). The
  **parent task is the only writer of scheduler state** — children only return their
  text — so the replies a batch produces are accepted in member order and the pushed
  order matches the persisted order. Each round brackets itself with
  `group_thinking{thinking:true/false}`, so the client can show one indicator per
  member.
- **Tool approvals are serialized** (`services/approval_gate.py`, ticket #10): one
  `ApprovalGate` per chat socket puts "send the prompt → wait for the answer" under a
  lock, so N concurrently-thinking members queue up instead of showing N dialogs.
  Timeout semantics are unchanged (120 s unanswered = denied), and a stale/duplicate
  `approval_response` resolves nothing (the WS layer logs it).
- **Message order is the sequence, not the clock (ticket #56 / ADR-0009):**
  every write goes through `ConversationManager.add_message`, which assigns
  `Message.seq` = `MAX(seq)+1` **within that conversation under SQLite's write
  lock** (it touches `conversations.updated_at` first — that lock is what makes a
  concurrent burst get 1,2,3… instead of all 1s). `Message.chronological_order()`
  is the ONE ordering key, and every read path uses it: LLM context
  (`get_context_messages`), the history API, `ensure_title`, `summarize_and_trim`
  (so compact trims the right end) and the memory window (`load_window`). A row
  without a sequence (pre-migration leftovers, hand-built fixtures) counts as 0, so
  an all-legacy table still reads in `created_at` order — the change is a pure
  increment for 1:1.
- **Pending:** none — tickets #56 / #08 / #09 / #10 / #11 delivered ADR-0009.
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
  Group turns are not token-streamed, so **per-member "typing" comes from
  `group_thinking`** (ticket #11): `chatStore.groupThinking` holds the members currently
  thinking and `MessageList` renders one `GroupTypingIndicator` each, so several members
  can be "typing" at once and each disappears on its own round end (a `<silent>` round
  clears it too, via `thinking:false`, and a reply clears it as it appears).
  `turn_end{pending}` is informational and drives no indicator; the group `done` event
  deliberately does **not** clear them (the driver releases `_turn_task` before it sends
  `done`, so the next batch's `thinking:true` can overtake it) — `error`, a socket close
  and `clearMessages` are the fallbacks that do.
  Live messages are inserted by `seq` (`utils/messageOrder.ts::insertBySequence`, checked
  by `cd frontend && npm run check:message-order`), not appended — concurrent arrivals
  and a replayed event land in the right place; the optimistic user message is appended
  first and then re-placed once `message_ack` supplies its own `seq`.
  TTS stays off in groups behind `useTTS`'s reserved `GROUP_TTS_ENABLED` switch.
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
| `turn_end` | `group: true`, `pending` | one **user turn** finished inside a group burst; `pending` = more queued interjections are about to run. `pending:false` means "nothing queued *right now*" (review P3). Informational since ticket #10 — per-member "thinking" is `group_thinking`, not this |
| `group_thinking` | `character_id`, `speaker`, `thinking` | a group member started (`true`) / finished (`false`) one thinking round (ticket #10). The frontend shows one "typing" indicator **per member** from this; a round that ends in `<silent>` is cleared by `thinking:false`, never by a message |
| `group_message` | `message_id`, `character_id`, `speaker`, `content`, `seq` | one group member's message was persisted (persist first, push second). `seq` is the per-conversation monotonic number (ticket #56) — concurrent arrivals are ordered by it, not by arrival time |
| `error` | `message` | unrecoverable error |
| `message_ack` | `client_message_id`, `message_id`, `seq` | user message persisted (id swap). `seq` lets the client place its own message in the sequence (ticket #10) |
| `memory_updated` | `count` | background memory extraction finished |
| `llm_retry` | `attempt`, `max_retries` | LLM retry in progress (transient, chat path only) |
| `proactive_skip` / `daily_greeting_skip` | `reason` | proactive/greeting aborted. `daily_greeting_skip` reasons: `already_greeted` (the only one meaning "today is done") / `group_conversation` / `no_character` / `disabled` / `no_config` / `in_flight` / `empty` (review N1) |

### 5.2 WebSocket inbound (client → server)

`{type}` is one of: `chat` (`content`, `character_id`, `force_vision?`,
`client_message_id?`), `approval_response` (`request_id`, `approved`),
`daily_greeting`, `update_location` (`lat`, `lng`).

For a **group** conversation `character_id` is not used (the room has no single
speaker); a `daily_greeting` is answered with
`{type:"daily_greeting_skip", reason:"group_conversation"}` and no proactive
session is started. Group replies are delivered whole (no `token` stream): a
`<silent>` skip must never be pushed, so the driver decides after the stream ends.
The history API (`GET /api/conversations/{id}/messages`) carries `seq` per message
for the same reason.

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
- **A tool-carrying LLM request must not end on an assistant message.** The DeepSeek
  thinking mode requires `reasoning_content` to be replayed for tool-carrying requests,
  and answers a request whose last message is an assistant message with
  `400 … reasoning_content in the thinking mode must be passed back`. Since we never
  capture that field, every caller with no real user message of its own must close the
  request with a synthetic **user** turn: greeting builds `[system, user]` directly,
  proactive passes `proactive_hint`, and a group turn passes `Agent.run(user_nudge=…)`
  (`services/group_chat.py::GROUP_TURN_OPEN` / `GROUP_CONTINUATION`). Without it the
  second request in a group burst always fails — that was the 2026-09-27 outage.
- **Tests**: `pytest` + `pytest.mark.asyncio`; in-memory DB via
  `create_async_engine("sqlite+aiosqlite://")` + `Base.metadata.create_all`;
  fakes: `FakeLLMService` (yields preset events), `FakeClock`, `RecordingSleep`,
  `monkeypatch` on `memory_service.search`. Test external behavior only, not
  implementation detail. See `backend/tests/test_tool_runtime.py`,
  `test_agent_tools.py`, `test_circuit_breaker.py`, `test_tool_retry.py` for
  the canonical patterns. **Concurrency** needs a *file* DB (`ws_env` / `file_client`)
  — the in-memory engine uses StaticPool (one shared connection), so a "concurrent"
  test there proves nothing. The frontend has no test infra (manual acceptance), except
  one re-runnable check for the message-ordering rule:
  `cd frontend && node scripts/check_message_order.mjs`.
- **One transcript renderer**: `core/transcript.py` — the LLM context and the
  history view must render
  messages through the *same* timestamped-transcript formatter (§7 时间戳对话记录)
  — never a second copy of the formatting rules. `render_transcript` returns the
  text lines (LLM context); `render_lines` returns speaker + text together (the
  history API needs both, and renders once). Turning those lines into one block of
  text (**render → drop the empty tool-pipeline lines → join**) is
  `render_transcript_text` — group summary and group catch-up both call it instead
  of re-implementing the join.
- **One message order**: `Message.chronological_order()` is the only ordering key for
  message reads — never `order_by(Message.created_at)` again (ADR-0009 / ticket #56).
  `created_at` is second-precision SQLite, so concurrent writes tie; a second ordering
  rule anywhere is exactly what makes two views of one conversation disagree. The
  sequence is assigned in `add_message` under the write lock, and legacy rows are
  backfilled once by `database.backfill_message_sequence`.
- **Names are trimmed at the API edge**: `POST/PUT /api/characters` reject a blank
  name with 400 and store `name.strip()` — `" A "` becomes `"A"` (review P2; the
  speaker label would otherwise render as `[   ]`). Same for group names
  (`api/group.py::_clean_group_name`).
- **Default conversation title lives on the model**: `Conversation.DEFAULT_TITLE` is the
  one value (the column default, plus a `ConversationManager.DEFAULT_TITLE` alias for
  existing callers); `ConversationManager.is_default_title(title)` is the one predicate.
  List **and** single-conversation endpoints send `is_default_title`, so the frontend
  never compares against its own copy — and a rename response carries the refreshed
  flag, so the list can update that row in place (review S6).
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
| 群聊 | Group chat | 群内的多角色对话：成员**同时思考**、可互相接话、可沉默；每人受思考轮上限 K，全局受消息上限 M（ADR-0009）|
| 发言者 | Speaker | 消息的说话人（用户或某角色）。群聊消息必带；1:1 由会话角色派生 |
| 链预算 | Chain budget | 每「用户轮」内角色消息**总量**上限（**M = 9**），到顶或无人再开口即静默 |
| 思考轮 | Thinking round | 一个成员的一次生成（产出 0 或 1 条消息；`<silent>` 也算一轮）。每成员每用户轮上限 **K**：N≤4 → 3，N>4 → 2 |
| 唤醒 | Wake | 新消息落库后，让「空闲且未用满 K」的**其他**成员开始一次新思考（说话者自己除外 —— 被自己的消息唤醒就是「继续说」换个名字）|
| 时间戳对话记录 | Timestamped transcript | 喂 LLM 与历史记录共用的统一格式：`YYYY/M/D HH:MM [说话人]: 内容`（系统本地时区） |
| 消息序号 | Message sequence | 会话内**单调递增**的整数（`Message.seq`）：`created_at` 只有秒级精度，并发写入同秒会并列 —— "谁先说的"由序号定，所有读路径按它排序（ADR-0009 / ticket #56）|
