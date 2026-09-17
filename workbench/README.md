# Workbench — migrated Vault subsystem

Multi-user AI chat + backlog/artifact-orchestration web app, migrated from `pdlc-core/adlc/tools/dashboard/server.py`
(2026-09-14). **This is not the GraphRAG tenant-knowledge system documented in this repo's root `CLAUDE.md`** —
it's a separate product that happens to also be branded "Vault" to its users. See
`docs/decisions/2026-09-14_migrate-dashboard-vault-subsystem-from-pdlc-core.md` for why, and
`EXTRACTION_NOTES.md` for exactly what was moved/fixed and what's been verified so far.

## What it is

- Auth: real user accounts (SQLite, `data/vault.db`), sessions, admin panel, quota system.
- AI chat with citation annotation (`/discovery/ask` — the route name is historical/misleading,
  kept as-is because the frontend calls it directly).
- Backlog management + LLM-driven artifact orchestration (proposal/spec/testcase generation).
- Teams bridge (present in code, inert — no `VAULT_TEAMS_*` env configured anywhere yet).
- 4 standalone Discovery-browse routes (`/discovery/{tree,file,workspace,workspaces}`) — ported,
  not proxied, so this app has **zero runtime dependency on pdlc-core**.

Content it searches/cites is **unchanged by the migration**: still `pdlc-artifacts/<workspace>/<project>/openspec/`
(Discovery/ADLC spec files) — not this repo's own `tenants/`. Unifying that is a possible future
phase, not decided.

## Setup

```
cd workbench
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then fill in one LLM provider key
```

## Run

```
python -m workbench.server.main --port 8766
```

Opens `http://localhost:8766/vault/`. `--no-open` skips the browser launch.

## ⚠️ Before touching `data/vault.db`

That file is (or will be, post-cutover) a copy of **real production data** — real user
accounts, real conversations, real backlog items. `db.py`'s `_init_db()` only seeds a default
`admin`/`admin123` account when the `users` table has zero rows matching `username='admin'` —
if you ever see the log line `[Vault] ✅ Admin user created` on a run that was supposed to use
the migrated data, **stop immediately**: it means `_init_db()` ran against an empty/wrong file,
not the real one.

## Known gaps (see EXTRACTION_NOTES.md for detail)

- No `.venv` built yet — nothing has been exercised that needs `anthropic`/`requests`/etc.
- `/vault/orchestrate/*` and conversation-summarize (LLM-backed) not smoke-tested yet.
- Phase 2 (full parity testing) and Phase 3 (cutover) not started.
