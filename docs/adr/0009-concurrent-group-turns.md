# Group turns: members think concurrently, new messages wake them

ADR-0006 settled how a group turn runs and recorded the serial rule outright:
one LLM stream at a time, `run()` refuses re-entry, and "parallel group turns are
a bug, not a race to tolerate". Living with it exposed the cost of that rule: within
a turn information only flows *forward*. Whoever speaks first cannot see what the
later members say — the first speaker answers the user, the second can react to the
first, but the first never reacts back until the *next* user message. The room feels
like a queue, not a conversation.

The wanted behaviour is the opposite: everybody starts thinking at once, a member
that finishes sees whatever landed while it was thinking and gets another round, and
the exchange stays bounded so it cannot run away.

## Decision

- **All members start together; new messages wake the rest.** A user message starts
  every member thinking concurrently. After that, each newly persisted message wakes
  every member that is idle and has not hit its cap.
- **In-flight generation is never cancelled.** A member finishes its current round;
  the messages that landed meanwhile are already in the history it re-reads, so the
  "inject what arrived while you were thinking" step needs **no new mechanism** — each
  thinking round re-reads the conversation from the database. Only the scheduling and
  the caps change.
- **Two caps, and no separate call cap.** Per member per user turn: at most **K**
  thinking rounds (N≤4 → K=3, N>4 → K=2). Globally per user turn: at most **M = 9**
  role messages. Thinking rounds are naturally bounded by N×K, so the LLM-call ceiling
  follows from K — silence still costs a round, which is why the cap is on rounds.
- **One message per thinking round.** "继续说" is removed: saying more is expressed as
  being woken again (bounded by K), not as an inner loop. The `<silent>` skip stays —
  a silent round produces no message, is not persisted, and still consumes a round.
- **The message budget is reserved before a round starts** — never "generate, then
  discover the budget is gone and drop it".
- **A new user message is a brand-new user turn**: K and M reset, every member is
  woken, and in-flight members finish first.
- **Convergence**: every member has spent its K rounds, or no member produced a
  message and nothing is in flight.
- **Tool approvals are serialized**: at most one approval prompt is in front of the
  user at a time; the rest queue.
- **Group messages carry a monotonic per-conversation sequence.** `created_at` is
  SQLite second precision, so concurrent writes can tie and the transcript order
  would be non-deterministic — the sequence, not the timestamp, fixes "who spoke
  first".
- **Kept from ADR-0006**: the orchestrator is pure logic with an injected "run one
  member's round" callable; the skip decision precedes persistence
  (`persist_reply=False`); the driver is the only writer.

## Considered Options

- **Stay serial (ADR-0006)** — rejected: the first speaker can never see the later
  members within a turn, and "everyone talking at once" is impossible by construction.
- **Fixed rounds with a barrier** — rejected: it degrades into "one wave at a time";
  the "reply while others are still talking" feel is exactly what was asked for.
- **Start once, no mutual waking** — rejected: that drops cross-talk, the point of the
  feature.
- **Cancel and regenerate when a new message arrives** — rejected: it throws away text
  already generated and breaks the existing "never interrupt an in-flight reply" rule.
- **Cap only the global message count** — rejected: silence costs money too, so a
  9-message turn could hide N× as many silent rounds; the cap has to count rounds.
- **Deduct the budget when a message lands** — rejected: some rounds would generate a
  reply only to have it dropped, wasting calls and producing messages the user never
  sees.

## Consequences

- **Duplicate answers get more likely in the first round**: simultaneous starters
  cannot see each other, so two members may answer the same user message the same way.
  That is the price of "everyone starts at once"; waking can only repair the *second*
  round, never a reply already sent.
- Cost per user message is bounded by **N×K thinking rounds** and **M=9 messages**
  (N≤4, K=3 → at most 12 rounds for a 4-member group).
- Transcript order is decided by the new sequence column; any code that ordered group
  messages by `created_at` must move to it.
- The frontend must render several concurrent streams (more than one "typing" member)
  and order incoming `group_message` events by the sequence.
- The removed continuation leaves `GROUP_CONTINUATION` and `is_continuation` as dead
  code — retire in two steps (stop calling it, then delete it) so a test failure is
  attributable. **`user_nudge` must stay**: it is the DeepSeek thinking-mode 400 fix
  (a tool-carrying request may not end on an assistant message), and every round needs
  it, not just continuations.
- ADR-0006's serial contract is **superseded by this ADR**; its "pure orchestrator",
  "skip precedes persistence" and "the driver is the only writer" decisions stand.
- **Delivered wire contract (tickets #09/#10).** The frontend consequence above is
  spelled out as events: each thinking round is bracketed by
  `group_thinking{character_id, speaker, thinking}` — so several members can be "typing"
  at once and a `<silent>` round is still cleared — every `group_message` carries the
  per-conversation `seq`, and `message_ack` plus the history API carry `seq` too, so the
  client can place every message (including the user's own) on the sequence. Tool
  approvals are serialized by `services/approval_gate.py`: one `ApprovalGate` per chat
  socket puts "send the prompt → wait for the answer" under a lock, so N
  concurrently-thinking members queue instead of showing N dialogs (120 s unanswered
  = denied, unchanged). `turn_end{group,pending}` survives as an informational
  per-user-turn signal; the per-member "typing" state comes from `group_thinking`.
  A user turn is still one **batch** at a time (a new user message winds the current
  batch down) — the concurrency is *within* a batch, which is what this ADR decided.
