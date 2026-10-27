# Provider capabilities: one model-level table, two-step onboarding

Three features now need per-model facts that business code must not branch on:
the shape of an image payload, the shape of a native-search response, and (already
today) the wire quirks of an SDK. Today those facts are scattered:

- `config.LLM_PRESETS` — provider level only (`sdk_type`, `base_url`, default
  model); it says nothing about what the model can *do*.
- `config.VISION_PRESETS` — a **second**, unrelated table, provider level, which
  is how a retired model (`qwen-vl-max`) stayed in the product for weeks.
- `services/formatters/` — switches on `sdk_type` for message shape, and has no
  notion of images at all.
- Call sites decide per feature by name or not at all: `screen_tools` hardcoded
  one MCP vision server's tool name, argument names *and* env var; `search_tools`
  hardcodes its backend list.

Two incidents came out of that: the vision path kept failing silently against a
retired model, and the only way to make a broken third-party server work was to
pin the whole app's SDK (impossible — `mcp>=1.30` on Python 3.14 forces
`starlette>=0.48`, `fastapi` wills `<0.47`, see ADR-0010). The pattern behind both
is the same: **facts about a provider were encoded as code, in several places.**

## Decision

- **One table, and it is model-level.** `config.LLM_PRESETS` entries carry, next
  to `sdk_type` / `base_url` / `default_model`:
  - `supports_vision: bool | None` + `image_api: "openai" | "anthropic" | "gemini"`
  - `native_search: bool` + `native_fetch: bool` + `search_api: "openai" | "anthropic" | "gemini" | "deepseek" | "none"`
  Provider presets supply **defaults**; the effective value is resolved per model
  (user override → preset → unknown). The same vendor really does ship both
  (`gpt-4o` can see, older models cannot; `glm-4v` vs `glm-4`; `qwen-vl` vs
  `qwen-plus`), so a provider-level flag would be wrong about half the time.
- **`None` means unknown, and every consumer must handle it.** Vision is a
  *required* capability — the user asked to see the screen, so unknown resolves to
  "try it, and if the model rejects the image, say so plainly". Search is an
  *optional* enhancement — unknown resolves to "do not attempt native, go to the
  local chain". **The asymmetry is the decision, not an oversight.**
- **Wire shapes live behind the existing seams**, never at call sites:
  - images → `MessageFormatter.image_part(bytes, mime)` (base = OpenAI shape,
    `AnthropicFormatter` overrides, a future `GeminiFormatter` adds the native
    one — the seam `docs/specs/message-formatter(done).md` already reserved).
  - search hits → `SEARCH_PARSERS[search_api]`, each returning the single
    `SearchHit` structure.
- **Onboarding a vendor is two fields.** Fill `supports_vision` + `image_api`
  (and/or `native_search` + `search_api`). Only when the wire shape is not one of
  the known ones do you add a formatter subclass or a parser. **No change to
  `Agent`, the driver, `see_screen` or `SearchAgent`.**
- **Providers may be described by shape, not by name.** Business code must not
  contain `if provider == "deepseek"`; the only places allowed to know a vendor
  are `config.py` (the table) and the seam implementations (formatter / parser).
- **Gemini defaults to its OpenAI-compatible endpoint** for both images and
  search, so it uses the `openai` shape; the native `generateContent` /
  `google_search` grounding variants are optional branches behind the same seams.
- **SDK-behaviour quirks stay out of the table.** DeepSeek's thinking-mode
  `user_nudge` changes *how a request is framed*, not *what the model can do*; it
  remains an explicit parameter at the call site rather than a capability flag.

## Considered Options

- **Keep one table per feature** (`VISION_PRESETS`, a future `SEARCH_PRESETS`) —
  rejected: that is the arrangement that already produced a dead preset nobody
  noticed, and each table needs its own override, UI and tests.
- **Branch on the provider name at each call site** — rejected: every new vendor
  then edits `Agent` and the screen tool, which is exactly the adaptation we just
  deleted from the vision path.
- **Probe the model at startup** (send a tiny image / search request and see) —
  rejected: costs money on every start, and a rejection tells you "unsupported"
  but not which payload shape to send; it also interacts badly with streaming.
- **Provider-level capability only** — rejected: see the model-level list above;
  users routinely type model names the preset does not know.
- **Always error on unknown capability** — rejected for search: a missing
  `search_api` must never break a chat turn.
- **Always degrade on unknown capability** — rejected for vision: quietly sending
  a text-only request when the user asked to see the screen is a lie, and it is
  how a 429 ended up in the conversation as if it were the screen content.
- **A capability *plugin* system (entry points per provider)** — rejected for
  now: the whole vocabulary is four enumerations; a registry of classes would be
  more machinery than the problem, and can be introduced later without changing
  call sites.

## Consequences

- Adding a vendor (or a model within a vendor) is a table edit; the standing proof
  is the vision spec's S6 test: `custom` + `supports_vision=True` +
  `image_api="openai"` must take the main-model path with **zero** code change.
- `runtime_config` gains a capabilities accessor; the LLM settings panel must show
  whether a capability came from the preset or from the user's override, otherwise
  users cannot tell why something is unavailable.
- Three image shapes and four search parsers become maintained surface, each
  needing a fixed-response snapshot test: the wire formats are vendor contracts we
  do not control, so a change must surface as a failing test, not a silent
  paragraph of error text.
- The asymmetry (vision errors, search degrades) has to be documented at both call
  sites; a future "cleanup" that unifies them will silently regress one of them.
  It is also the reason both specs repeat the rule.
- `VISION_PRESETS` survives, but demoted: it answers "which specialist model do we
  use when the main model cannot see", not "what can this provider do".
- ADR-0003 is untouched: the main LLM still decides *whether* to search, the
  SearchAgent decides *how*. This ADR governs only the *how*.
- The table is now the first place to look when a feature "mysteriously"
  degrades on a new model; the second is the formatter / parser seam.
- **Where the user override lives, and what it is keyed by (ticket 01).** The override
  is stored in `data/config.yaml` under `capabilities:` (the same file, and the same
  save path, as provider/model — no DB migration), keyed **`provider:model`**, because
  this ADR resolves per model: an override set for `openai:gpt-4o` must not silently
  apply to `openai:gpt-3.5`. Writing is a **PATCH** (absent = keep, `null` = clear one
  capability) and is validated **before** any other field is touched, so a rejected
  batch cannot leave the runtime config half-updated. Reading is deliberately tolerant:
  a hand-edited yaml value that is not a known shape / not a boolean is treated as
  "not overridden" rather than surfacing garbage — otherwise the panel would render an
  option it cannot represent and the next save would 400.
- **No model-name inference.** A provider whose models differ declares `None`
  ("I don't know") and the user override answers per model. Guessing from the model
  string (`*vl*`, `*4o*`) is the same name-branching this ADR rejects, one layer down.
- **The vocabulary is shipped, not duplicated.** `GET /api/config` returns
  `capability_table` (name / kind / allowed shapes, in display order) next to
  `capabilities` (value + source), so the settings panel never re-declares the table —
  adding a capability is still a one-file change.
