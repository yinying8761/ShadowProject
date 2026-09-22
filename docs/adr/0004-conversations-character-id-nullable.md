# Conversations.character_id becomes nullable — group conversations have no single character

`conversations.character_id` was NOT NULL: every conversation belonged to exactly one character, and SQLite's schema (created by `create_all`) baked that constraint in. The group-chat spec (docs/specs/group-chat.md) needs a conversation whose speakers are a fixed character set (a 群) — there is no single owner character. SQLite cannot alter a column constraint in place, so the change requires a one-time rebuild migration (create new table → copy rows → drop old → rename), departing from the repo's add-column-only `ADDITIVE_MIGRATIONS` convention.

## Considered Options

- **Separate `group_conversations` table alongside `conversations`** — rejected: every consumer (history, context assembly, auto-title, compact, transcript rendering) would need two query surfaces and two render paths forever; the shared transcript renderer and memory pipelines assume one conversation model.
- **Keep NOT NULL, point group conversations at a pseudo-character** — rejected: a fake "group character" pollutes the character list, memory is per-`character_id` so it would grow a garbage identity, and "who am I talking to" logic would need special cases everywhere.
- **Nullable `character_id` + `group_id` on the same table (chosen)** — one conversation model with two explicit shapes: `group_id` NULL ⇒ 1:1 (behavior byte-for-byte unchanged), `group_id` set ⇒ group conversation (`character_id` NULL). The split is queryable and cheap to branch on.

## Consequences

- The rebuild lives in `database.ensure_group_schema`: idempotent (no-op when `character_id` is already nullable), ordered after `_apply_additive_migrations` (its INSERT..SELECT needs the additively-added `group_id`/`last_extract_at`), and run inside `init_db`'s transaction (any failure rolls the whole migration back). The whole sequence is `run_migration_sequence` — the single authoritative order, reused verbatim by tests and the acceptance script.
- The file snapshot (`companion.db.bak-group-<ts>`) is taken BEFORE the migration transaction opens, so it captures an untouched file; backup failure aborts startup instead of risking an unbacked rebuild. The rebuild also refuses to run when `PRAGMA foreign_keys` is ON — under enforcement, `DROP TABLE` degrades to a cascading implicit DELETE on `messages` (this app never enables FK enforcement, and the guard makes that a prerequisite, not an accident).
- The rebuilt table's hand-written DDL is kept column-for-column identical to `models.conversation` (order, types, NOT NULL, defaults, FKs); a schema-parity test compares it against a fresh `create_all` so future model changes cannot silently drift.
- Blocking file IO for the snapshot runs via `asyncio.to_thread` per the async-everywhere convention.
- `ADDITIVE_MIGRATIONS` remains the tool for new columns; constraint changes now have a second, explicitly non-additive path. Future constraint changes should copy this shape: probe → backup → rebuild → rename, inside one transaction.
- 1:1 is untouched: `group_id`-NULL conversations keep `character_id` set and all existing queries behave exactly as before (verified by the full suite).
- Code that assumed `conversation.character_id` is always set must now tolerate NULL on the group path — the chat WS wiring (ticket #53/#04) routes group conversations away from the 1:1 agent loop, which keeps every 1:1 code path NULL-free in practice.
