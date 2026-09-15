# Search decision rights: keyword router as hint, not auto-execution

The message augmenter (`services/message_augmenter.py` Stage 1) used to
auto-execute a web search whenever the keyword router (`core/router.py`
`need_search()`) matched: it called the old `tools/search_tools.research()`
(one search + one independent LLM summary call) and injected the result into
the main context *before* the main LLM ever saw the message. Keyword matching
cannot read context ("今天好累" vs "今天天气" both hit), so HARD_TRIGGERS were
pruned repeatedly to fight false positives, and one real hit ("刚刚测试一下…"
→ dictionary definitions of "刚刚", confidence=low) wasted a full search +
LLM call. Full problem analysis: `docs/specs/search-augmenter-degrade.md`.

## Decision

`need_search()` hit → inject only a hint into context ("用户消息可能涉及时效
或外部信息，可考虑调用 research 工具核实"); the **main LLM decides** whether
to actually search. The keyword router is advisory only, never executes:

- 要不要搜 → 主 LLM 判断；怎么搜 → SearchAgent 内的小 LLM 判断
- HARD_TRIGGERS **and** SOFT_TRIGGERS both go on the hint path
- Delete the old `tools/search_tools.research()` (SearchAgent is the single
  search implementation; `_do_search`/`_search_*`/`fetch_url` stay, SearchAgent
  still uses them) and the 5-min result cache in `router.py`

## Considered Options

- **Maintain auto-search + keep trimming keywords** — done as a transition
  (13 words removed, probes 13/13, tests 322/322); rejected as an end state:
  blacklist patching never finishes and cannot fix "keywords can't read
  context"
- **Auto-search + LLM second confirmation** — every hit costs an extra LLM
  call to judge; cost ≈ just searching; meaningless
- **Hint injection (chosen)** — keeps a cheap nudge for strong signals;
  mis-trigger cost drops to one hint string
- **Pure LLM / delete the router** — same real-need cost as hint injection
  (+1 round trip); the only real difference is whether the nudge rescues
  "should-search-but-didn't". Kept as the **fallback path**, not the choice.

## Consequences

- Mis-trigger cost: "1 search + 1 independent LLM call" → one hint string;
  real search needs: +1 tool round + 1 main LLM call
- The main LLM holds search decision rights (it demonstrably uses `research`
  well — eval T03); the augmenter never pre-executes searches for the user
- Old `tools.search_tools.research()` is removed; SearchAgent becomes the only
  search implementation; the result cache is gone
- POI injection (Stage 2) and proactive/greeting flows are unaffected
- **Fallback condition**: if measured "should-search-but-didn't" rises
  materially after this change, degrade to pure LLM (delete the router,
  rely on the system prompt positioning `research` as the primary tool)
- Future idea (not now): inject the current time as a deterministic
  time-sensitivity signal instead of keyword guessing — non-conflicting
  with the hint path, stackable