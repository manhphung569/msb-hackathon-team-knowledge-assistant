"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager

from .config import VAULT_DB

# ---- source line 2622 (_db) ----
# NOTE: @contextmanager was dropped by the mechanical ast-based extraction (Python 3.8+
# ast.FunctionDef.lineno points at `def`, not the decorator line) — restored by hand,
# 2026-09-14. Every other extracted function was checked and has no decorator (verified).
@contextmanager
def _db():
    """Thread-safe SQLite connection context manager."""
    conn = sqlite3.connect(str(VAULT_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- source line 2637 (_init_db) ----
def _init_db() -> None:
    """Create tables and seed default admin on first run."""
    VAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            display_name  TEXT    NOT NULL DEFAULT '',
            password_hash TEXT    NOT NULL,
            salt          TEXT    NOT NULL,
            roles_json    TEXT    NOT NULL DEFAULT '["BA"]',
            token_quota   INTEGER NOT NULL DEFAULT 50000,
            token_used    INTEGER NOT NULL DEFAULT 0,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            active        INTEGER NOT NULL DEFAULT 1,
            must_change_pw INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token      TEXT    UNIQUE NOT NULL,
            expires_at TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS token_requests (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount_requested INTEGER NOT NULL,
            reason           TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            admin_note       TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions(user_id);

        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title       TEXT    NOT NULL DEFAULT 'New Conversation',
            project     TEXT    NOT NULL DEFAULT '',
            role        TEXT    NOT NULL DEFAULT 'BA',
            mode        TEXT    NOT NULL DEFAULT 'discover',
            change_key  TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT    NOT NULL,
            content         TEXT    NOT NULL,
            sources_json    TEXT,
            provider        TEXT,
            model           TEXT,
            tokens_in       INTEGER DEFAULT 0,
            tokens_out      INTEGER DEFAULT 0,
            tokens_total    INTEGER DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_conv_user      ON conversations(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_msg_conv       ON messages(conversation_id);

        CREATE TABLE IF NOT EXISTS backlog_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title        TEXT    NOT NULL DEFAULT 'Backlog',
            status       TEXT    NOT NULL DEFAULT 'draft',
            source_file  TEXT    NOT NULL DEFAULT '',
            project      TEXT    NOT NULL DEFAULT '',
            workspace    TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            finalized_at TEXT
        );
        CREATE TABLE IF NOT EXISTS backlog_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   INTEGER NOT NULL REFERENCES backlog_sessions(id) ON DELETE CASCADE,
            title        TEXT    NOT NULL,
            description  TEXT    NOT NULL DEFAULT '',
            priority     TEXT    NOT NULL DEFAULT 'medium',
            category     TEXT    NOT NULL DEFAULT 'feature',
            status       TEXT    NOT NULL DEFAULT 'pending',
            ai_reasoning TEXT    NOT NULL DEFAULT '',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_bl_session ON backlog_items(session_id, sort_order);
        """)
        # Migration: add change_key to conversations (safe — column may already exist)
        try:
            c.execute("ALTER TABLE conversations ADD COLUMN change_key TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_conv_change ON conversations(change_key, mode)")
        except Exception:
            pass
        # Add version_id to orch_runs (milestone tracking)
        try:
            c.execute("ALTER TABLE orch_runs ADD COLUMN version_id INTEGER REFERENCES orch_versions(id) ON DELETE SET NULL")
        except Exception:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_orch_runs_ver ON orch_runs(version_id)")
        except Exception:
            pass
        # Add domain/phase columns if missing (safe migration)
        for col, typedef in [("domain","TEXT NOT NULL DEFAULT ''"),
                              ("phase","INTEGER NOT NULL DEFAULT 0"),
                              ("phase_reason","TEXT NOT NULL DEFAULT ''"),
                              ("vault_coverage","TEXT NOT NULL DEFAULT '{}'")]:
            try:
                c.execute(f"ALTER TABLE backlog_items ADD COLUMN {col} {typedef}")
            except Exception:
                pass
        c.executescript("""

        CREATE TABLE IF NOT EXISTS changes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            change_key   TEXT    NOT NULL UNIQUE,
            backlog_item_id INTEGER REFERENCES backlog_items(id) ON DELETE SET NULL,
            session_id   INTEGER REFERENCES backlog_sessions(id) ON DELETE SET NULL,
            title        TEXT    NOT NULL,
            description  TEXT    NOT NULL DEFAULT '',
            priority     TEXT    NOT NULL DEFAULT 'medium',
            category     TEXT    NOT NULL DEFAULT 'feature',
            status       TEXT    NOT NULL DEFAULT 'open',
            project      TEXT    NOT NULL DEFAULT '',
            workspace    TEXT    NOT NULL DEFAULT '',
            vault_coverage TEXT  NOT NULL DEFAULT '{}',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_changes_status ON changes(status);
        CREATE INDEX IF NOT EXISTS idx_changes_session ON changes(session_id);

        CREATE TABLE IF NOT EXISTS orch_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            backlog_item_id INTEGER REFERENCES backlog_items(id) ON DELETE CASCADE,
            system          TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS orch_artifacts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER NOT NULL REFERENCES orch_runs(id) ON DELETE CASCADE,
            artifact_key TEXT NOT NULL,
            name         TEXT NOT NULL,
            icon         TEXT NOT NULL DEFAULT '📄',
            output_type  TEXT NOT NULL DEFAULT 'Markdown (.md)',
            content      TEXT NOT NULL DEFAULT '',
            version      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_orch_runs_item ON orch_runs(backlog_item_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_orch_arts_run  ON orch_artifacts(run_id);

        CREATE TABLE IF NOT EXISTS orch_versions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            backlog_item_id INTEGER REFERENCES backlog_items(id) ON DELETE CASCADE,
            version_number  INTEGER NOT NULL DEFAULT 1,
            label           TEXT NOT NULL DEFAULT '',
            artifact_snapshot TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_orch_ver_item ON orch_versions(backlog_item_id, created_at DESC);

        -- ── Orchestration channel registry ─────────────────────────────────────
        CREATE TABLE IF NOT EXISTS orch_channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_key TEXT    UNIQUE NOT NULL,
            icon        TEXT    NOT NULL DEFAULT '⚙️',
            label       TEXT    NOT NULL,
            is_system   INTEGER NOT NULL DEFAULT 0,  -- 1 = built-in, 0 = user-created
            mode        TEXT    NOT NULL DEFAULT 'public', -- 'public'|'private'
            enabled     INTEGER NOT NULL DEFAULT 1,  -- shown in Backlog combobox
            created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orch_channel_artifacts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id     INTEGER NOT NULL REFERENCES orch_channels(id) ON DELETE CASCADE,
            artifact_key   TEXT    NOT NULL,
            sort_order     INTEGER NOT NULL DEFAULT 0,
            enabled        INTEGER NOT NULL DEFAULT 1,
            icon           TEXT    NOT NULL DEFAULT '📄',
            name           TEXT    NOT NULL,
            description    TEXT    NOT NULL DEFAULT '',
            output_type    TEXT    NOT NULL DEFAULT 'Markdown (.md)',
            sources_json   TEXT    NOT NULL DEFAULT '[]',
            ai_prompt      TEXT    NOT NULL DEFAULT '',
            template       TEXT    NOT NULL DEFAULT '',
            checklist_json TEXT    NOT NULL DEFAULT 'null',
            created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(channel_id, artifact_key)
        );
        CREATE INDEX IF NOT EXISTS idx_orch_ch_arts ON orch_channel_artifacts(channel_id, sort_order);

        CREATE TABLE IF NOT EXISTS vault_agents (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            agent_key           TEXT    NOT NULL UNIQUE,
            name                TEXT    NOT NULL,
            description         TEXT    NOT NULL DEFAULT '',
            seed_channel_key    TEXT    REFERENCES orch_channels(channel_key) ON DELETE SET NULL,
            status              TEXT    NOT NULL DEFAULT 'draft',
            mode                TEXT    NOT NULL DEFAULT 'draft_only',
            trigger_mode        TEXT    NOT NULL DEFAULT 'manual',
            knowledge_scope_json TEXT   NOT NULL DEFAULT '[]',
            policy_json         TEXT    NOT NULL DEFAULT '{}',
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_vault_agents_owner ON vault_agents(owner_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS external_accounts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider            TEXT    NOT NULL,
            tenant_id           TEXT    NOT NULL DEFAULT '',
            external_user_id    TEXT    NOT NULL DEFAULT '',
            external_username   TEXT    NOT NULL DEFAULT '',
            scopes_json         TEXT    NOT NULL DEFAULT '[]',
            access_token_enc    TEXT    NOT NULL DEFAULT '',
            refresh_token_enc   TEXT    NOT NULL DEFAULT '',
            expires_at          TEXT    NOT NULL DEFAULT '',
            status              TEXT    NOT NULL DEFAULT 'connected',
            last_error          TEXT    NOT NULL DEFAULT '',
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, provider, tenant_id, external_user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ext_accounts_user ON external_accounts(user_id, provider);

        CREATE TABLE IF NOT EXISTS teams_chat_bindings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id            INTEGER NOT NULL REFERENCES vault_agents(id) ON DELETE CASCADE,
            external_account_id INTEGER NOT NULL REFERENCES external_accounts(id) ON DELETE CASCADE,
            tenant_id           TEXT    NOT NULL,
            chat_id             TEXT    NOT NULL,
            chat_topic          TEXT    NOT NULL DEFAULT '',
            chat_type           TEXT    NOT NULL DEFAULT 'groupchat',
            sync_mode           TEXT    NOT NULL DEFAULT 'polling',
            postback_mode       TEXT    NOT NULL DEFAULT 'none',
            ingest_enabled      INTEGER NOT NULL DEFAULT 1,
            last_cursor         TEXT    NOT NULL DEFAULT '',
            last_message_at     TEXT    NOT NULL DEFAULT '',
            status              TEXT    NOT NULL DEFAULT 'active',
            created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(agent_id, tenant_id, chat_id)
        );
        CREATE INDEX IF NOT EXISTS idx_teams_bindings_account ON teams_chat_bindings(external_account_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS connector_events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            binding_id          INTEGER NOT NULL REFERENCES teams_chat_bindings(id) ON DELETE CASCADE,
            provider            TEXT    NOT NULL,
            external_message_id TEXT    NOT NULL,
            thread_key          TEXT    NOT NULL DEFAULT '',
            sender_external_id  TEXT    NOT NULL DEFAULT '',
            sender_display      TEXT    NOT NULL DEFAULT '',
            direction           TEXT    NOT NULL DEFAULT 'inbound',
            message_type        TEXT    NOT NULL DEFAULT 'text',
            sent_at             TEXT    NOT NULL DEFAULT '',
            content_text        TEXT    NOT NULL DEFAULT '',
            raw_json            TEXT    NOT NULL DEFAULT '{}',
            event_status        TEXT    NOT NULL DEFAULT 'new',
            processed_at        TEXT    NOT NULL DEFAULT '',
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(binding_id, external_message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_connector_events_binding ON connector_events(binding_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_connector_events_status ON connector_events(event_status, created_at ASC);

        CREATE TABLE IF NOT EXISTS connector_threads (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            binding_id          INTEGER NOT NULL REFERENCES teams_chat_bindings(id) ON DELETE CASCADE,
            thread_key          TEXT    NOT NULL,
            title               TEXT    NOT NULL DEFAULT '',
            memory_summary      TEXT    NOT NULL DEFAULT '',
            last_external_message_id TEXT NOT NULL DEFAULT '',
            last_summarized_at  TEXT    NOT NULL DEFAULT '',
            state_json          TEXT    NOT NULL DEFAULT '{}',
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(binding_id, thread_key)
        );
        CREATE INDEX IF NOT EXISTS idx_connector_threads_binding ON connector_threads(binding_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS agent_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id            INTEGER NOT NULL REFERENCES vault_agents(id) ON DELETE CASCADE,
            binding_id          INTEGER REFERENCES teams_chat_bindings(id) ON DELETE SET NULL,
            trigger_kind        TEXT    NOT NULL,
            trigger_event_id    INTEGER REFERENCES connector_events(id) ON DELETE SET NULL,
            thread_id           INTEGER REFERENCES connector_threads(id) ON DELETE SET NULL,
            output_kind         TEXT    NOT NULL DEFAULT 'ignore',
            output_ref_id       INTEGER,
            provider            TEXT    NOT NULL DEFAULT '',
            model               TEXT    NOT NULL DEFAULT '',
            tokens_in           INTEGER NOT NULL DEFAULT 0,
            tokens_out          INTEGER NOT NULL DEFAULT 0,
            tokens_total        INTEGER NOT NULL DEFAULT 0,
            status              TEXT    NOT NULL DEFAULT 'running',
            error_text          TEXT    NOT NULL DEFAULT '',
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            completed_at        TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS outbound_drafts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id            INTEGER NOT NULL REFERENCES vault_agents(id) ON DELETE CASCADE,
            binding_id          INTEGER NOT NULL REFERENCES teams_chat_bindings(id) ON DELETE CASCADE,
            thread_id           INTEGER REFERENCES connector_threads(id) ON DELETE SET NULL,
            run_id              INTEGER REFERENCES agent_runs(id) ON DELETE SET NULL,
            draft_kind          TEXT    NOT NULL DEFAULT 'reply',
            content_md          TEXT    NOT NULL DEFAULT '',
            content_text        TEXT    NOT NULL DEFAULT '',
            approval_status     TEXT    NOT NULL DEFAULT 'pending',
            approved_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
            approved_at         TEXT    NOT NULL DEFAULT '',
            posted_at           TEXT    NOT NULL DEFAULT '',
            post_result_json    TEXT    NOT NULL DEFAULT '{}',
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_outbound_drafts_binding ON outbound_drafts(binding_id, approval_status, created_at DESC);
        """)
        # Seed admin user if not exists
        if not c.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            salt     = secrets.token_hex(16)
            pw_hash  = hashlib.sha256((salt + "admin123").encode()).hexdigest()
            c.execute(
                "INSERT INTO users(username,display_name,password_hash,salt,"
                "roles_json,token_quota,is_admin,must_change_pw) VALUES(?,?,?,?,?,?,?,?)",
                ("admin", "Administrator", pw_hash, salt,
                 '["PO","BA","SA","EA","TL","DEV","TEST"]', 999_999_999, 1, 1),
            )
            print("[Vault] ✅ Admin user created — login: admin / admin123  (đổi mật khẩu ngay!)")


# ---- source line 2986 (_hash_pw) ----
def _hash_pw(salt: str, password: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


# ---- source line 2990 (_get_user_by_token) ----
def _get_user_by_token(token: str) -> dict | None:
    """Return user dict if session valid, else None."""
    if not token:
        return None
    with _db() as c:
        row = c.execute("""
            SELECT u.id, u.username, u.display_name, u.roles_json,
                   u.token_quota, u.token_used, u.is_admin, u.must_change_pw
            FROM   users u
            JOIN   sessions s ON s.user_id = u.id
            WHERE  s.token = ?
              AND  s.expires_at > datetime('now')
              AND  u.active = 1
        """, (token,)).fetchone()
    return dict(row) if row else None


