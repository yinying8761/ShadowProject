# LLM config: `.env` (keys) + `config.yaml` (effective config), mutable at runtime

LLM provider/model/key were all read from `.env` at startup into a static
pydantic-settings singleton, so they could not be edited from the UI. We split
them: secrets stay in `.env` as per-provider `<PROVIDER>_API_KEY`; the effective
selection (active provider, model, custom providers) moves to `data/config.yaml`;
a mutable in-memory config object makes UI edits take effect immediately without a
restart.

## Considered Options

- **DB-primary + `.env` fallback** — runtime reads a DB table first, `.env` seeds
  it. Rejected: two sources of truth risk divergence, and `.env` was wanted to
  remain the portable source of truth.
- **Write back to `.env` + hot-reload** — UI rewrites the env file and reloads
  settings. Rejected: parsing/rewriting `.env` (comments, ordering) is fragile and
  pydantic-settings reload is awkward.
- **`providers.json` for the provider list** — rejected: it would mix structured
  config with secrets; `config.yaml` in `data/` matches the existing
  `mcp_servers.json` pattern and the Hermes `config.yaml` precedent.

## Consequences

- Provider/key/model become editable in the UI with immediate effect (no restart).
- Legacy `LLM_PROVIDER`/`LLM_MODEL`/`LLM_BASE_URL` are deprecated; users re-select
  once in the new UI. `LLM_API_KEY` remains as a fallback.
- `.env` holds only secrets (per-provider keys + optional per-provider `BASE_URL`
  overrides), no non-secret LLM config.
