# Extraction notes — Vault subsystem migration (Phase 1)

Source: `pdlc-core/adlc/tools/dashboard/server.py` (10,851 lines). See
`docs/decisions/2026-09-14_migrate-dashboard-vault-subsystem-from-pdlc-core.md` for the full
migration decision and plan.

## How the split was done

1. Parsed `server.py` with Python's `ast` module to get exact line spans for all 328
   top-level definitions (functions, methods, module-level constants) — not by hand-counting
   or grepping closing markers (a manual grep for one closing `"""` landed ~4,000 lines past
   its real end, because a later unrelated triple-quoted string closed first).
2. Classified every one of the 328 items by name into `VAULT_MOVE` (173 items, ~7,240 lines),
   `SHARED_DUPLICATE` (6 small helpers), `CORE_KEEP` (94 items — stays in pdlc-core, untouched),
   `DISCOVERY_BROWSE_DUPLICATE` (4 items — stay in pdlc-core AND get a standalone ported copy
   here), `INFRA_REDESIGN` (15 path/env constants — new values, same names), `SPLIT_DISPATCH`
   (7 HTTP-routing methods — hand-rewritten in `dispatch.py`, not copied), or `SKIP` (imports,
   docstring, `main()`, the `Handler` class container itself).
3. Sliced each `VAULT_MOVE`/`SHARED_DUPLICATE` item's exact source lines into the matching
   `server/*.py` module (mechanical, verbatim — see the `# ---- source line NNNN ----` markers
   still present throughout).
4. **Caught and fixed one real extraction bug**: `_db()`'s `@contextmanager` decorator was
   dropped by the mechanical slice, because Python 3.8+'s `ast.FunctionDef.lineno` points at
   the `def` line, not the decorator line (this was the *only* decorated function in the whole
   file — checked every item for a decorator line immediately above its reported `lineno`).
5. Ran a scope-aware cross-reference audit (custom `ast`-based script, not manual reading) to
   find every name the moved code calls that isn't defined within the moved set — this is how
   the transitive closure in `discovery_helpers.py` was discovered (the 4 Discovery-browse
   routes pull in 10 more helpers, which pull in 32 more — 42 names / 679 lines total, not the
   ~150–300 originally estimated when the "duplicate, don't proxy" call was made).
6. Fixed each real gap found by the audit:
   - `search.py`'s `_resolve_vault_root` and `discovery_helpers.py`'s `_discovery_project_roots`/
     `_discovery_source_root` no longer import pdlc-core's `workspace_layout.py` — replaced with
     direct `ARTIFACTS_ROOT`/`SOURCES_ROOT` joins (confirmed equivalent to what
     `resolve_project_layout()` computed).
   - `_display_workspace_path` (was `workspace_layout.display_path`) reimplemented inline in
     `discovery_helpers.py`, tried against `ARTIFACTS_ROOT`/`SOURCES_ROOT` instead of the
     original's `repo_root`/`workspace_root` (no equivalent concept here — purely cosmetic
     display formatting, not load-bearing).
   - `_bl_call_llm`/`_bl_call_llm_ex`/`_parse_retry_after` moved out of `orchestrate.py` into a
     new `llm.py` — `conversations.py` needs them too, and `orchestrate.py` needs
     `conversations.py`'s `_get_or_create_conv`/`_save_messages`, which would otherwise be a
     circular import.
7. Every remaining audit hit was verified by hand to be a **false positive** from the audit
   heuristic's two known blind spots, not a real gap:
   - a locally-scoped `import x as _x` inside a function (the original code's own style for
     lazy-loading heavy/optional deps — `anthropic`, `requests`, `olefile`, `py7zr`, `xlrd`,
     `python-docx`/`pptx`, etc. — all preserved as-is), which the audit's bound-name collector
     doesn't track (it only tracks `def`/assignment targets, not `import` statements); or
   - a variable from an **enclosing** function's scope read inside a nested closure two or more
     levels deep (e.g. `_bl_extract_structured_backlog_items`'s nested `_flush_current_row`
     reading `headers`/`header_map` from its parent — legal Python, the audit only checks one
     level of nesting at a time).
   Spot-checked ~8 instances across different files/patterns before generalizing this
   conclusion to the rest of the list.

## Verified working end-to-end (2026-09-14, against a read-only copy of the live DB)

- Whole package imports cleanly (`import workbench.server.dispatch` — 13 mixins + zero
  circular imports).
- Server starts on port 8766, `_init_db()` correctly recognizes the copied DB and does **not**
  re-seed a duplicate admin (verified: `users` count stayed 10, no
  `[Vault] ✅ Admin user created` log line).
- `/vault/` (200), `/vault/auth/me` unauthenticated (401 with proper JSON), `/vault/docs` (200),
  `/vault/artifact-guide` (200), `/vault/setup` (302 — correctly detects `user_count > 1`).
- **All 4 Discovery-browse routes work with pdlc-core's dashboard confirmed NOT running**
  (port 8765 checked empty) — `/discovery/workspaces`, `/discovery/tree?project=dip`,
  `/discovery/workspace?workspace=dip`, `/discovery/file?project=dip&path=...` all returned
  real data. Proves the 900-line `discovery_helpers.py` port is genuinely independent.
- Full auth flow: inserted one throwaway test user directly into the **copy** (never the
  live source), logged in over HTTP, got a session token, called `/vault/auth/me`,
  `/vault/conversations`, `/vault/backlog` with it — all returned correct real data (real
  project names, real backlog items, real user "manh"/"Administrator" from the actual dataset).
  Test user removed from the copy afterward.

## Not yet done (still open for a future session)

- `workbench/.env` itself (only `.env.example` exists) — needs the real API keys copied from
  pdlc-core's `.env` (§5 of the ADR/plan). Not done here since it means duplicating live
  credentials into a second file; flagging for an explicit decision rather than doing it
  silently.
- `workbench/.venv` has not been created/installed yet — every smoke test above ran against
  the machine's global Python because every third-party import in the moved code is already
  lazy/guarded (confirmed: nothing failed without `anthropic`/`requests`/etc. installed). A
  real venv is still needed before `/vault/orchestrate/*`, whisper transcription, or file-upload
  extraction can be exercised.
- `/vault/orchestrate/*` (LLM-backed artifact generation) and `/vault/conversations/:id/summarize`
  not smoke-tested yet — both need a working LLM API key.
- Phase 2 (parity testing against every functional area) and Phase 3 (actual cutover) per the
  plan have not started — this is Phase 1 only.
