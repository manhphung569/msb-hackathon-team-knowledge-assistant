"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import secrets
import sqlite3

from .config import VAULT_DB, VAULT_PAGE_DIR
from .db import _db, _hash_pw


class AdminMixin:
    # ---- source line 5239 (Handler._serve_vault_admin) ----
    def _serve_vault_admin(self):
        """GET /vault/admin → serve vault_admin.html (client-side auth check)."""
        admin_page = VAULT_PAGE_DIR / "vault_admin.html"
        if not admin_page.exists():
            return self._send_json({"error": "Admin panel not found"}, 404)
        data = admin_page.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- source line 5252 (Handler._handle_vault_admin_stats) ----
    def _handle_vault_admin_stats(self):
        """GET /vault/admin/stats → aggregate usage numbers."""
        if not self._require_admin():
            return
        with _db() as c:
            users_count  = c.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
            total_tokens = c.execute("SELECT COALESCE(SUM(token_used),0) FROM users").fetchone()[0]
            convs_count  = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            msgs_count   = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            pending_reqs = c.execute(
                "SELECT COUNT(*) FROM token_requests WHERE status='pending'").fetchone()[0]
            top_users = c.execute(
                "SELECT username,display_name,token_used,token_quota,is_admin,active "
                "FROM users ORDER BY token_used DESC LIMIT 10"
            ).fetchall()
            # Daily totals (last 14 days) — sum tokens_total from messages per day
            daily = c.execute("""
                SELECT date(m.created_at) as day, COALESCE(SUM(m.tokens_total),0) as tokens
                FROM messages m
                WHERE m.created_at >= date('now','-14 days')
                GROUP BY day ORDER BY day
            """).fetchall()
        return self._send_json({
            "users_count":        users_count,
            "total_tokens":       total_tokens,
            "conversations_count": convs_count,
            "messages_count":     msgs_count,
            "pending_requests":   pending_reqs,
            "top_users":          [dict(r) for r in top_users],
            "daily_tokens":       [dict(r) for r in daily],
        })

    # ---- source line 5284 (Handler._handle_vault_admin_usage_csv) ----
    def _handle_vault_admin_usage_csv(self):
        """GET /vault/admin/usage.csv → export token usage per user."""
        if not self._require_admin():
            return
        with _db() as c:
            rows = c.execute(
                "SELECT username,display_name,token_used,token_quota,is_admin,active,created_at "
                "FROM users ORDER BY token_used DESC"
            ).fetchall()
        lines = ["username,display_name,token_used,token_quota,pct,is_admin,active,created_at"]
        for r in rows:
            pct = round(r["token_used"]/max(r["token_quota"],1)*100, 1)
            lines.append(f'{r["username"]},{r["display_name"]},{r["token_used"]},'
                         f'{r["token_quota"]},{pct},{r["is_admin"]},{r["active"]},{r["created_at"]}')
        csv_bytes = "\n".join(lines).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="vault_usage.csv"')
        self.send_header("Content-Length", str(len(csv_bytes)))
        self.end_headers()
        self.wfile.write(csv_bytes)

    # ---- source line 5307 (Handler._serve_vault_setup) ----
    def _serve_vault_setup(self):
        """GET /vault/setup → first-run wizard (only if no non-admin users exist)."""
        if VAULT_DB.exists():
            with _db() as c:
                user_count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if user_count > 1:
                # Already set up — redirect to vault
                self.send_response(302)
                self.send_header("Location", "/vault/")
                self.end_headers()
                return
        setup_page = VAULT_PAGE_DIR / "vault_setup.html"
        if setup_page.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(setup_page.read_bytes())
            return
        # Fallback — redirect to admin
        self.send_response(302)
        self.send_header("Location", "/vault/admin")
        self.end_headers()

    # ---- source line 5330 (Handler._handle_vault_setup_post) ----
    def _handle_vault_setup_post(self):
        """POST /vault/setup — create users during first-run wizard."""
        try:
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)
        action = body.get("action", "")
        if action == "create_user":
            username = (body.get("username") or "").strip().lower()
            password = (body.get("password") or "").strip()
            roles    = body.get("roles") or ["BA"]
            quota    = int(body.get("token_quota", 50000))
            if not username or len(password) < 6:
                return self._send_json({"error": "Thiếu username hoặc password ≥6 ký tự"}, 400)
            salt = secrets.token_hex(16)
            pw_hash = _hash_pw(salt, password)
            try:
                with _db() as c:
                    c.execute(
                        "INSERT INTO users(username,display_name,password_hash,salt,"
                        "roles_json,token_quota,is_admin) VALUES(?,?,?,?,?,?,?)",
                        (username, username, pw_hash, salt, json.dumps(roles), quota, 0),
                    )
            except sqlite3.IntegrityError:
                return self._send_json({"error": f"Username '{username}' đã tồn tại"}, 409)
            return self._send_json({"ok": True})
        return self._send_json({"error": "unknown action"}, 400)

