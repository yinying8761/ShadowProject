# Group turns: a pure orchestrator decides, the driver persists

Ticket #53 (群轮编排) had to answer "who speaks when, how often, and when does the turn
stop" for a group conversation while reusing the existing `Agent` (persona, memory,
tools, approvals) — the spec's user story 27 forbids a second LLM path. Two decisions
were not obvious, and both are visible in every line of the group code, so they are
recorded here: **the orchestrator is pure logic with an injected "run one member's
turn" callable**, and **the Agent hands the reply back instead of persisting it**.

## Decision

- **`core/group_turn.py` owns the state machine and nothing else.** Member order,
  `<silent>` skips, the chain budget, and interjection handling live there; the LLM,
  the DB, the clock and the websocket are injected or absent. Its input is a
  synchronous `submit(text)` plus `run(member_ids)`; a conversation has exactly one
  orchestrator, and `run` refuses to be re-entered (parallel group turns are a bug,
  not a race to tolerate).
- **The skip decision precedes persistence.** A character that has nothing to add
  answers `<silent>`; the spec requires that reply to be neither stored nor pushed
  ("不落库、不推送"). So the Agent runs with `persist_reply=False`: the `done` event
  carries the text (`deferred: true`) and no `message_id`, the orchestrator drops it
  via the single `is_silent()` predicate, and the driver persists what survives with
  `speaker_id` = that member (`services/group_chat.py`). The orchestrator's
  `on_utterance` sink is the only writer, so "counted by the orchestrator" and
  "stored by the driver" can never disagree.
- **"一轮" = one LLM stream.** When the user interjects, the in-flight stream finishes
  (never cancelled mid-answer) and the turn winds down immediately: no continuation
  for that member, no remaining members on the old messages. Queued user messages
  merge into one new turn with a fresh budget.
- **The driver owns serialization.** `GroupChatSession.handle_user_message` is
  synchronous and starts at most one turn task per conversation, so a burst of user
  messages cannot open two parallel turns; user messages are persisted before any
  character reads the history (`_settle_persists`), which is what makes a queued
  message visible to the turn that answers it.
- **Group conversations stay off the 1:1 paths**: no proactive session, `daily_greeting`
  answers with a skip, the HTTP `/api/chat/send` fallback refuses them (its reply would
  be written with `speaker_id=NULL`), and every turn passes the same
  "conversation must exist" gate as 1:1 (ADR-0005).
- Agent surface added for this: `persist_reply`, `system_suffix` (the scenario brief
  — who is in the room, when to stay silent) and `memory_query` (retrieval uses the
  current turn's user messages, since the caller persists them).

## Considered Options

- **Stream tokens and retract on `<silent>`** — rejected: a skip must never reach the
  client, and retracting streamed text is worse than a slightly later message.
- **Let the Agent persist and delete the message when it turns out to be a skip** —
  rejected: that is not "不落库" (a crash or a second reader sees the row), and it adds
  churn plus an ack that may already have been sent.
- **A queue-worker per conversation that persists everything itself** — rejected: the
  interjection/budget semantics are a state machine over the *in-flight* turn, which a
  worker blocked on `await turn()` cannot wind down.
- **A separate group LLM path** — rejected by spec US27 (reuse the Agent, tools,
  memory and compact rather than a second mechanism).

## Consequences

- Group replies are delivered whole (`group_message`), not token-by-token: the client
  can only show a member's message once the skip question is settled. A "typing"
  indicator is left to the UI ticket (#54) — the backend needs no new event for it.
- Anything that wants to speak in a group must go through the orchestrator's sink;
  `speak` implementations must not persist (the driver is the single writer).
- A failed member turn aborts the turn burst but **keeps** the queued messages: they
  belong to user messages that were already stored, so dropping them would silently
  lose a turn.
- The protocol additions (`group_message`, `done{group:true}`, chat without
  `character_id`) are part of the wire contract documented in CONTEXT.md §5.1/§5.2.
