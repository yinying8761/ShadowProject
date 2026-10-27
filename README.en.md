# ShadowProject

[中文](README.md) | English

A desktop AI companion. It lives on your desktop, reads and writes files, searches the web, looks at your screen, and remembers what you've told it across sessions. Multiple characters, group chat.

Backend: 83 Python modules, 11,720 lines (excluding tests). Frontend: Electron + React + TypeScript. 625 pytest cases (619 passing + 6 opt-in live cases), 11 ADRs, 30 specs. The case count moves with development; this is the order of magnitude, not a live figure.

## Design notes

### Tool calls and approval

The model can't execute anything directly. Every tool call runs the whole chain:

```
assemble context → streaming call → parse tool request → user approval → async execution → feed result back
```

Approval is a serial gate: one request at a time, queued when several characters run concurrently, and a timeout or a dropped connection counts as a rejection. Ten built-in tools (read/write file, list directory, search files by name, time, screen, fetch URL, web search, memory read/write). External MCP tools come in over stdio or SSE, with tool names prefixed as `mcp__{server}__{tool}`. Long-running search is delegated to a SearchAgent sub-agent; the main loop chains at most 5 rounds.

### Reliability

Tool execution always goes through schema validation, circuit breaking, backoff retries and a timeout sandbox:

- Arguments that fail JSON Schema validation don't raise. They return an explanation so the model can fix the arguments and retry by itself.
- Retries are classified by error: 429 / 5xx are retryable, argument errors are not; whether a retry replays the operation depends on whether it's idempotent.
- The breaker is a CLOSED / OPEN / HALF_OPEN state machine. After repeated failures the tool gets isolated, and both the degraded and the recovery paths have tests.

### Concurrent group chat

Several characters think at the same time. The design is in [ADR-0009](docs/adr/0009-concurrent-group-turns.md); a few parts that weren't obvious:

- Arrival order under concurrency isn't speaking order, and the database timestamp isn't precise enough to break a tie. So each session gets a monotonic sequence number and every read path sorts by it.
- Waking excludes the speaker themselves, otherwise the model just continues its own last message.
- Cost is bounded by two limits (thinking rounds per member, total messages stored), with quota reserved before generation starts, so nothing is generated and then thrown away.
- In-flight generations are never cancelled. Each round re-reads the whole conversation, so messages stored mid-thinking naturally reach the next round's context.
- Scheduling state is only written by the parent task and child tasks return text only, so no locks are needed.

### Observability

Token usage is metered by session, character and model; every tool call is traced into a table. Logs go through a custom hub: stdout is teed into an in-memory ring buffer, a rotating file, and a live push to the frontend ([ADR-0002](docs/adr/0002-stdout-log-hub.md)).

## Features

- **Long-term memory**: SQLite FTS5 full-text index plus vector embeddings, two-way recall fused with RRF. Memories are extracted automatically, deduplicated, decayed over time and isolated per character.
- **Multiple providers**: Anthropic's native SDK and OpenAI-compatible endpoints sit behind one streaming interface; per-provider message shape differences are confined to `MessageFormatter`.
- **Voice**: GPT-SoVITS zero-shot voice cloning, a separate voice per character.
- **Proactive companion**: idle detection plus screen fingerprint dedup (dHash, Hamming ≤ 10 counts as the same screen), combined with time, weather and location to start a conversation.
- **Screen awareness**: screenshots are never written to disk or to the database, and only exist in memory for that one request.

## Screenshots

| Token usage | Model configuration | Proactive companion |
|:---:|:---:|:---:|
| ![Token usage](docs/screenshots/usage.png) | ![Model configuration](docs/screenshots/model-config.png) | ![Proactive companion](docs/screenshots/proactive.png) |

## Status

| Capability | Status | Notes |
|---|---|---|
| Tool loop and approval gate | Done | 10 built-in tools + MCP, up to 5 chained rounds |
| Reliability pipeline | Done | validation, breaker, retry, timeout; the model corrects bad arguments itself |
| Concurrent group chat | Done | event wake-ups, two caps, monotonic sequence |
| Memory retrieval | Done | FTS5 plus vector recall, RRF fusion |
| Observability | Done | token metering, tool tracing, log hub |
| Eval v1 | Done | 15 single-turn cases, automatic scoring |
| Voice cloning, proactive companion, screen awareness | Done | GPT-SoVITS, dHash dedup, in-memory screenshots |
| Model capability table | Planned | [ADR-0011](docs/adr/0011-provider-capability-matrix.md), split into tickets #61–#69; vision and search not wired up yet |
| Main model reads the screen directly | Planned | [spec](docs/specs/multimodal-vision.md) |
| Native web search | Planned | [spec](docs/specs/native-web-search.md) |
| Eval v2 | Planned | [spec](docs/specs/agent-eval-v2.md) |

### Three changes in progress

**Vision.** Today a screenshot is handed to a separate vision model that writes a paragraph, and that paragraph is pasted into the conversation. The main model never sees the image: information is lost, every screenshot costs an extra call, and the vision models behind it have been retired. After the change the image goes out as a content block with the current request and the main model looks at it directly; one private adapter written for a single MCP server gets deleted along the way, closing out [ADR-0010](docs/adr/0010-mcp-server-isolation.md).

**Search.** Results currently come from scraping HTML ourselves (ddgs metasearch / Bing result-page scraping / searxng / Azure Bing). Anti-scraping, blocked networks and page redesigns can take the whole capability down, and citations aren't reliable. Provider-native search moves to the first position in the chain, with local scraping kept entirely as fallback. Implementation branches on `search_api`, not on provider name.

**Eval.** Behavioural cases (single-turn, multi-turn, failure recovery, vision, search) are compared against expectations with a real model. Non-deterministic systems like group chat switch to invariant assertions covering eight properties (rounds per member, total messages, `<silent>` not persisted, monotonic sequence, and so on), driven by a scripted fake model, so they finish in seconds and cost no tokens. Plus baseline diffing and a cost column, otherwise after a prompt change all you see is "73% today" with no idea whether that's better or worse.

All three share one model-level capability table (`supports_vision` / `image_api` / `native_search` / `search_api`). There's one deliberate asymmetry: an unknown `image_api` must raise, because the user explicitly asked to look at the screen and silently dropping the image isn't acceptable; an unknown `search_api` degrades instead, because search is an optional addition and a missing config entry shouldn't interrupt the conversation.

## How it's built

- Every non-trivial decision gets an ADR (11 so far), recording the alternatives, the trade-off and the cost.
- New features start as a spec and get split into tickets (30 specs plus `docs/Tickets/`); files get a `(done)` suffix once implemented.
- Important behaviour is pinned by tests (625 cases today, 6 of them opt-in live cases). Invariant tests first construct a violating scenario to confirm the assertion can actually fail. The count tracks development and is not kept exactly in sync.
- Larger changes get a dedicated review written around them, under `docs/analysis/`; the three reviews from the group-chat concurrency round total about 90KB.
- The project is built with AI assistance (Claude Code / Codex / DeepSeek Harness); architecture, interface contracts, test design and code review are mine. A large share of those ~16k lines of backend and frontend source was AI-generated — the ADRs, specs and tests above are the constraints that keep it stable.

## Limitations

- Windows only. Screen capture uses `mss`, window exclusion relies on `WDA_EXCLUDEFROMCAPTURE`, and the floating window is Electron main-process logic.
- Single machine, single user, no accounts. Approval is a runtime constraint, not a permission system.
- Frontend components have a higher share of AI involvement than the backend; I own the UI requirements, interface contracts and acceptance.
- Vision and search are still on the old path, as described above.
- The eval suite is 15 single-turn cases with narrow coverage, usually only re-run after a feature change.
- No CI. Real-model evaluation is costly and non-deterministic; only invariant cases are a good fit for a pipeline.
- Voice requires a local GPT-SoVITS deployment and doesn't work out of the box.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Electron + React 18 + TypeScript + Tailwind CSS + Zustand |
| Backend | Python 3.10+ / FastAPI + WebSocket + SQLAlchemy + aiosqlite |
| AI | Anthropic / OpenAI / DeepSeek / Gemini / Qwen / Zhipu / Moonshot |
| Retrieval | SQLite FTS5 + vector embeddings, RRF fusion |
| Voice | GPT-SoVITS zero-shot voice cloning |

## Project layout

```
├── backend/
│   ├── main.py              # FastAPI entry, tool registration, MCP setup
│   ├── core/                # agent loop, sub-agents, tool registry and runtime, breaker
│   ├── services/            # LLM, vision, memory, group orchestration, approval, retry, observability
│   ├── tools/               # files, search, screen, memory, time
│   ├── api/                 # REST + WebSocket routes (including tool_logs)
│   ├── models/              # SQLAlchemy ORM
│   └── eval/                # labelled test set + automatic scoring
├── frontend/
│   ├── electron/            # main process, preload, floating window
│   └── src/                 # components / hooks / stores / services / i18n
├── docs/
│   ├── adr/                 # architecture decision records (11)
│   ├── specs/               # feature specs, `(done)` once implemented
│   ├── analysis/            # focused technical reviews
│   └── Tickets/             # requirement breakdown
├── scripts/                 # dev.bat / install.bat / setup-tts.bat and other helpers
└── .env.example
```

## Getting started

### Requirements

- Windows 10/11
- Python 3.10+ (3.10.6 recommended)
- Node.js 18+
- [Optional] NVIDIA GPU + GPT-SoVITS (voice)

### Install

```bash
scripts\install.bat
```

Or manually:

```bash
cd backend && pip install -r requirements.txt
cd frontend && npm install
```

### Configuration

```bash
cp .env.example .env
```

`LLM_API_KEY` is the only required entry; the rest are optional. The full list is in `.env.example`:

| Key | Description |
|---|---|
| `LLM_*` | provider, model, base_url, api_key for the chat model |
| `VISION_*` | fallback vision model, used when the main model can't see images |
| `SEARCH_BACKEND` | local search backend: duckduckgo / searxng / bing / bing_web (duckduckgo by default) |
| `AMAP_API_KEY` | Amap key (location + weather, free) |
| `TTS_REF_BASE` | GPT-SoVITS install path |
| `MAX_CONTEXT_TOKENS` | context window; 16000 in `.env.example`, 8000 as the code default |

### Run

```bash
scripts\dev.bat                      # start both

# or separately
cd backend && python main.py         # backend :8722
cd frontend && npm run electron:dev  # frontend :16173
```

### Voice (optional)

```bash
scripts\setup-tts.bat
```

This clones the repo, creates a virtualenv, installs PyTorch and dependencies, and downloads the pretrained models. If the model download fails, search for a "GPT-SoVITS 整合包" package on Bilibili and download it manually.

To clone a voice for a character: prepare a clean 3-10 second single-speaker clip (no BGM, no noise), put it in the GPT-SoVITS directory and note down what the clip says; start `runtime\python.exe api_v2.py` from the GPT-SoVITS directory; then point the character at the audio path and its transcript. Recommended parameters: `temperature=0.6`, `top_k=30`, `text_split_method=cut5`.

A neutral, gentle reference clip works best, ideally 5-8 seconds. A Japanese voice sample can also clone into Chinese speech (set `prompt_lang` to `ja`).

### Fallback vision for screen capture

Copy `mcp_servers.example.json` to `data/mcp_servers.json` and set `VISION_API_KEY` in the `vision` entry to use a free GLM vision model (`VISION_BASE_URL` and `VISION_MODEL` already point at Zhipu's OpenAI-compatible endpoint and `glm-4.6v-flash`). The free tier answers 429 (`code 1305`, over capacity) fairly often; that's upstream rate limiting worth a retry, not a misconfiguration. This path contains an adapter written for the parameter names of one specific MCP server, and will be removed once the vision rework lands.

## Docs

| Directory | Contents |
|---|---|
| [`docs/adr/`](docs/adr) | architecture decision records; start with [0009 group chat concurrency](docs/adr/0009-concurrent-group-turns.md) and [0011 capability table](docs/adr/0011-provider-capability-matrix.md) |
| [`docs/specs/`](docs/specs) | feature specs; a `(done)` suffix means implemented |
| [`docs/analysis/`](docs/analysis) | focused reviews, including the three group-chat concurrency reviews and an architecture retrospective |
| [`docs/Tickets/`](docs/Tickets) | requirement breakdown and progress |

## License

MIT
