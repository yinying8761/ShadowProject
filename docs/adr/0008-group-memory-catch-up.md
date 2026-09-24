# Group memory catch-up: day windows, one extraction, one copy per member

**Opening a group conversation must catch up on the days you were away**: turn what
was said into per-character memories, then compact — in that order, because compaction
deletes messages. Ticket #55 (spec: `group-chat.md` Phase 2 · 群记忆与 compact) settled
several non-obvious points.

## Decision

- **Extraction is split from storage.** `MemoryExtractor` is now `load_window` (messages
  in a time window) → `extract` (**one** LLM call → candidate items, nothing stored) →
  `store` (dedup, embed, persist). `extract_and_store` remains the 1:1 / daily entry
  point, and its prompt and behaviour are unchanged on purpose: ticket #55 asked for a
  split, not a re-prompt. Group chat calls `extract` **once**, then `store`s the same
  batch **once per member** — never one extraction per member, which would let members'
  memories disagree.
- **Dedup is scoped per character.** `MemoryStore.find_similar(..., character_id=)`
  filters by the memory's owner. This is a deliberate semantic change to a shared
  method: a fact recorded for A must still be recorded for B, and cross-character dedup
  is exactly the memory pollution the spec warns about (CONTEXT §3.4 records it).
- **Backfill windows are days, bounded by an LLM-call budget.** `batch_windows` walks
  `[last_extract_at, now)` one day at a time, at most `MAX_BATCHES` (7) windows; a longer
  absence merges its **oldest** days into one window. The cap bounds LLM cost, not
  content: a merged window still covers every day.
- **Every memory carries its own date.** The group prompt asks the model for
  `occurred_on`, read off the timestamped transcript, and `store` prefers it over the
  window's date. Without this, a merged window would date September content to January —
  the exact failure the ticket names ("不要把三天前说成今天"). A missing or malformed
  date falls back to the window's day, so a weaker model degrades instead of failing.
- **The anchor only advances over work that actually succeeded.** `last_extract_at` moves
  to the end of the last window whose extraction did not raise, and compaction runs
  **only if no window failed** — a failed extraction must never be followed by deleting
  the messages it was supposed to capture. A conversation that was never backfilled
  (anchor NULL) resolves to its **oldest message**, so a long history is not partially
  extracted and then compacted away.
- **The catch-up is a background service with per-conversation in-flight dedup.**
  `schedule_catch_up(conversation_id)` reuses a running task (reconnect, several
  windows) and the task is not tied to the websocket — leaving the group does not cancel
  the work. The 1:1 path is untouched: it still extracts/compacts for the active
  character.
- **Notifications are aggregated.** `MemoryStore.add(notify=)` lets the store suppress
  its per-row push; the catch-up pushes the total once, because
  `memory_updated` is a UI counter, not an audit log.
- **The group summary input goes through the shared renderer** and branches on the
  conversation kind (`conv.group_id is not None`), not on "did we resolve names" — a
  group whose speakers were deleted, or whose window holds only user messages, must not
  silently fall back to the 1:1 `[用户]/[角色]` format and lose 谁说了什么.

## Considered Options

- **One extraction per member** — rejected: N× cost and members whose memories disagree
  about the same conversation.
- **One extraction for the whole absence, dated by the anchor** — rejected: it dates
  everything to the first day back; the ticket exists to prevent exactly that.
- **Merging old days and dating them to the merged window's first day** — rejected during
  review (a probe dated September content to January); per-memory `occurred_on` fixed it.
- **Extracting into a pending table and folding it in later** — rejected: a second memory
  lifecycle for one feature.
- **Re-prompting the 1:1 diary extraction while splitting it** — rejected: out of scope
  and a silent behaviour change to the daily path.

## Consequences

- `MAX_BATCHES` is a cost knob; raising it buys date precision for very long absences,
  not correctness.
- A partial failure leaves the anchor at the last good day, so the next open retries the
  failed window; the same day is re-scanned, and dedup absorbs the repeats.
- The module-level notification queue is shared state — tests must drain it before
  asserting on a count.
- `last_extract_at` is conversation-level, so a group conversation's catch-up state is
  one timestamp; it is not per member (memories are).
