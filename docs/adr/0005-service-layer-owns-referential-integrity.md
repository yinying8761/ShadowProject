# Referential integrity is owned by the service layer, not the database

The SQLite engine never enables `PRAGMA foreign_keys` (`backend/database.py` — no
connect event, no engine setting), which ADR-0004 already recorded as a
prerequisite of the group rebuild migration. Reviewing the group-chat tickets
(#50–#52) showed the cost of that state: **every `ondelete=` in the models was
decorative**. Deleting a character left orphan conversations (2), orphan messages
(6) and dangling `memories.character_id` (10) in the real `data/companion.db`,
`PRAGMA foreign_key_check` reported 18 violations, and the chat API accepted any
`conversation_id` — so a stale id wrote messages nothing owned. Full forensics:
`docs/analysis/group-chat-review-and-orphan-data.md` §B2.

## Decision

- **FK enforcement stays OFF.** No connect-time PRAGMA, no engine setting. It is
  a single documented switch, not a per-table accident.
- **The inert `ondelete=` clauses are removed** from every model, from
  `ADDITIVE_MIGRATIONS` and from the `conversations` rebuild DDL. An FK
  declaration now means "reference" (joins, relationships, documentation) and
  never "enforced behaviour". The schema-parity test keeps the rebuild DDL and
  `models.conversation` identical, `on_delete` included.
- **The declared intent moves to the service layer**, explicitly and in one
  place per parent object:
  - character deletion → `api/character.py::_delete_character_dependents`:
    the character's conversations and their messages, its memories, `NULL` on
    `messages.speaker_id` and on `memories.source_conversation_id` pointing at
    what it removed, its `group_members` rows, its per-character
    `user_profiles` row;
  - conversation deletion → `api/conversation.py::delete_conversation`:
    `memories.source_conversation_id` `→ NULL` (memories stay per-character);
  - `messages.conversation_id` keeps the ORM relationship cascade
    (`cascade="all, delete-orphan"`) — the message rows really do follow, which
    is why "delete conversation" never leaked.
- **A turn is only accepted for a conversation that exists**: HTTP `404`,
  WebSocket `error{code:"conversation_not_found"}` + close `4404`, re-checked
  every turn because the conversation can be deleted while a socket is open
  (the multi-window case that produced the orphan messages). The frontend
  recovers by creating a fresh conversation and retrying the message
  (`frontend/src/services/conversationRecovery.ts`) instead of dropping text.
- **`llm_usage.conversation_id` keeps no FK and is never cleaned**: it is an
  append-only accounting log (it holds ids of long-deleted conversations on
  purpose — a usage record must outlive what it measured).

## Considered Options

- **Enable `PRAGMA foreign_keys=ON`** — rejected for now. (a) The existing 18
  violations would start rejecting new writes, so legacy rows must be cleaned
  first. (b) `PRAGMA foreign_keys` is a no-op inside a transaction, but the
  ADR-0004 rebuild *requires* FK-off and its guard refuses to run when FK is on —
  enabling enforcement means redesigning that path (a dedicated FK-off
  connection) in the same change, or an un-rebuilt database fails at startup.
  (c) Three tickets into a feature branch is the wrong moment to widen the blast
  radius. Revisit deliberately: clean violations → rework the rebuild path → one
  ADR.
- **Keep the `ondelete=` clauses as documentation** — rejected: a declaration
  that silently does nothing is worse than none. It is exactly how the orphan
  data happened: the code reads `ondelete="CASCADE"` and concludes that deletion
  is handled.
- **Enforce the rules with SQLite triggers instead** — rejected: business rules
  hidden in schema objects are invisible to the code that owns them, and there is
  no migration story for editing them.

## Consequences

- Deleting a character is now a multi-table operation that must stay in one
  transaction (it is), and **any new table referencing a character or a
  conversation must be added to `_delete_character_dependents`** — the
  compensating cost of not having the DB enforce it.
- A group can end up with **zero members** (deleting the last member removes the
  membership rows); the group and its group conversations are kept, because
  "编辑群" can add members back while deleting a group would take the user's
  group history with it. No group-delete endpoint exists yet (out of #52's scope).
- Legacy rows predating this decision are cleaned by
  `scripts/cleanup_orphan_data.py` (backup → single transaction →
  `PRAGMA foreign_key_check` must return 0 rows). The real `data/companion.db`
  was migrated and cleaned as part of this work.
- `PRAGMA foreign_key_check` remains the **audit** that measures dangling rows
  without enforcing anything — it is how the orphan list was produced and how the
  cleanup is verified. `llm_usage` is the only known dangling-reference class
  that is intentional.
- ADR-0004's guard ("the rebuild refuses to run when FK enforcement is ON")
  becomes a permanent invariant of the rebuild path rather than a temporary
  precaution.
