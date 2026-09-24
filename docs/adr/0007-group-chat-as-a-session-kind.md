# Group chat is a third session kind in the UI: `activeGroup` beside `activeCharacter`

The frontend modelled exactly one session kind: `appStore.activeCharacter` decided the
sidebar, the conversation list and which conversation the single WebSocket talked to.
Group chat (ticket #05) needs a second kind — a session whose conversation list belongs
to a 群 and whose messages have several speakers — **without** a second socket, without
a second message pipeline, and without disturbing 1:1.

## Decision

- **`appStore.activeGroup` is the third session kind.** `activeGroup === null` means
  1:1 and every previous code path stays byte-for-byte identical.
- **Entering/leaving a group is a mode move.** `enterGroup` closes the settings panel,
  remembers the current `layoutMode` and forces `full`; `leaveGroup` restores the
  remembered mode. Entering another group *from inside* a group does not overwrite the
  remembered mode (you still return to the mode you had before the first group).
- **"Groups are full-mode only" is a store invariant**, not a convention:
  `setLayoutMode` refuses `compact` while a group is active, and the title-bar toggle
  is hidden there — so no caller (today's shortcut or tomorrow's feature) can break it.
- **`useChat` keys its session init on `char:<id>` / `group:<id>`.** That is what makes
  leaving a group re-initialise the 1:1 conversation even though the active character
  never changed.
- **Group conversations reuse the single WebSocket.** The conversation id in the URL is
  already what tells the backend which conversation it is — the backend now also uses
  it to run the group turn (`group_id` ≠ NULL), so no new channel and no client-side
  routing table.
- **`GroupSidebar` replaces `ConversationSidebar`; both share `ConversationList`.**
  Member list in speaking order + 「退出」 go in the group sidebar's header; select /
  rename / delete / 「新对话」 are the shared list — only the loader differs.
- **The speaker is resolved once, server-side.** Live `group_message` events carry
  `speaker`, the history API carries `speaker`, and the client only falls back to its
  own character list when the backend has no name (a deleted character) — the same
  rule as the transcript renderer, not a second one.
- **Interjection stays reachable in the UI**: the input is *not* disabled while a group
  turn is in flight (spec US16 — the backend queues it and answers after this turn).
- **Group TTS stays off** behind `useTTS`'s `GROUP_TTS_ENABLED` switch (spec: Out of
  Scope — overlapping voices; reopen when serial speech lands).

## Considered Options

- **A second WebSocket (or a second store) for groups** — rejected: the existing bridge
  is already conversation-scoped, and a second pipeline would fork streaming, errors,
  approvals and TTS decisions.
- **A pseudo-character standing in for the group** — rejected in ADR-0004 for the data
  model, and the same argument holds in the UI: it would pollute the character list and
  every "who am I talking to" branch.
- **A generic session abstraction (`kind`, id, loader, title) up front** — deferred, not
  rejected: with two kinds the `activeGroup ? … : …` branch is smaller than the
  abstraction. **A third kind (e.g. 用户↔多角色场景) is the trigger to build it** — the
  branch currently appears at ~8 call sites, which is the cost being accepted here.

## Consequences

- A new table/state that is "per session" must decide which kinds it belongs to; the
  store guard is the single place that keeps group layout honest.
- Group replies are not token-streamed (ADR-0006), so the group `done` event — which
  carries no `message_id` — is what clears the "thinking" state in the UI.
- There is no delete-group UI: the backend has no endpoint (ADR-0005), and deleting a
  group would take its conversation history with it. Editing (rename / add / remove
  members) is the management surface.
