# ShadowProject — Agent Instructions

Desktop AI companion app: Electron + React + TypeScript frontend, Python FastAPI backend.

Cross-tool entry point for AI coding agents (Claude Code, Codex, Cursor, DeepSeek Harness, …).
This file is deliberately small — the project's agent conventions live in the files below, read them as needed:

- `CLAUDE.md` — project overview + Claude Code skill index
- `CONTEXT.md` — single-context project domain doc (read before non-trivial changes)
- `docs/adr/` — architecture decision records (add one for any non-trivial design decision)
- `docs/agents/issue-tracker.md` — GitHub Issues workflow (`gh` CLI)
- `docs/agents/domain.md` — domain doc / ADR conventions

Working rule: read `CONTEXT.md` and check `docs/adr/` before making non-trivial changes.