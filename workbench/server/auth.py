"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import secrets
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

from .db import _db, _hash_pw
from .shared import _create_session

# ---- source line 3018 (_user_to_json) ----
def _user_to_json(user: dict) -> dict:
    import json as _json
    return {
        "id":            user["id"],
        "username":      user["username"],
        "display_name":  user["display_name"] or user["username"],
        "roles":         _json.loads(user["roles_json"] or '["BA"]'),
        "token_quota":   user["token_quota"],
        "token_used":    user["token_used"],
        "token_remaining": max(0, user["token_quota"] - user["token_used"]),
        "is_admin":      bool(user["is_admin"]),
        "must_change_pw": bool(user["must_change_pw"]),
    }


class AuthMixin:
    # ---- source line 4795 (Handler._handle_vault_login) ----
    def _handle_vault_login(self):
        try:
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)

        username = (body.get("username") or "").strip().lower()
        password = (body.get("password") or "").strip()
        if not username or not password:
            return self._send_json({"error": "Thiếu username hoặc password"}, 400)

        with _db() as c:
            row = c.execute(
                "SELECT * FROM users WHERE username=? AND active=1", (username,)
            ).fetchone()

        if not row:
            return self._send_json({"error": "Tài khoản không tồn tại hoặc đã bị khóa"}, 401)

        expected = _hash_pw(row["salt"], password)
        if expected != row["password_hash"]:
            return self._send_json({"error": "Mật khẩu không đúng"}, 401)

        token = _create_session(row["id"])
        return self._send_json({"token": token, "user": _user_to_json(dict(row))})

    # ---- source line 4822 (Handler._handle_vault_logout) ----
    def _handle_vault_logout(self):
        token = self._session_token()
        if token:
            with _db() as c:
                c.execute("DELETE FROM sessions WHERE token=?", (token,))
        return self._send_json({"ok": True})

    # ---- source line 4830 (Handler._handle_vault_me) ----
    def _handle_vault_me(self):
        user = self._require_auth()
        if not user:
            return
        # Refresh latest quota/usage from DB
        with _db() as c:
            fresh = c.execute(
                "SELECT id,username,display_name,roles_json,token_quota,token_used,is_admin,must_change_pw "
                "FROM users WHERE id=?", (user["id"],)
            ).fetchone()
        if fresh:
            return self._send_json({"user": _user_to_json(dict(fresh))})
        self._send_json({"error": "user not found"}, 404)

    # ---- source line 4845 (Handler._handle_vault_change_password) ----
    def _handle_vault_change_password(self):
        user = self._require_auth()
        if not user:
            return
        try:
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)

        old_pw  = (body.get("old_password") or "").strip()
        new_pw  = (body.get("new_password") or "").strip()
        if not new_pw or len(new_pw) < 6:
            return self._send_json({"error": "Mật khẩu mới phải ít nhất 6 ký tự"}, 400)

        with _db() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if _hash_pw(row["salt"], old_pw) != row["password_hash"]:
                return self._send_json({"error": "Mật khẩu cũ không đúng"}, 401)
            new_salt = secrets.token_hex(16)
            new_hash = _hash_pw(new_salt, new_pw)
            c.execute(
                "UPDATE users SET password_hash=?, salt=?, must_change_pw=0 WHERE id=?",
                (new_hash, new_salt, user["id"]),
            )
        return self._send_json({"ok": True})

    # ---- source line 4872 (Handler._handle_vault_token_request) ----
    def _handle_vault_token_request(self):
        user = self._require_auth()
        if not user:
            return
        try:
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)

        amount = int(body.get("amount", 0))
        reason = (body.get("reason") or "").strip()[:500]
        if amount <= 0:
            return self._send_json({"error": "Số token phải > 0"}, 400)

        with _db() as c:
            # Check no pending request already
            pending = c.execute(
                "SELECT id FROM token_requests WHERE user_id=? AND status='pending'",
                (user["id"],)
            ).fetchone()
            if pending:
                return self._send_json({"error": "Bạn đã có yêu cầu đang chờ duyệt"}, 409)
            c.execute(
                "INSERT INTO token_requests(user_id,amount_requested,reason) VALUES(?,?,?)",
                (user["id"], amount, reason),
            )
        return self._send_json({"ok": True, "message": f"Đã gửi yêu cầu cấp thêm {amount:,} tokens"})

    # ---- source line 4901 (Handler._handle_vault_admin_users_get) ----
    def _handle_vault_admin_users_get(self):
        if not self._require_admin():
            return
        with _db() as c:
            rows = c.execute(
                "SELECT id,username,display_name,roles_json,token_quota,token_used,"
                "is_admin,active,created_at FROM users ORDER BY created_at"
            ).fetchall()
        return self._send_json({"users": [dict(r) for r in rows]})

    # ---- source line 4912 (Handler._handle_vault_admin_users_post) ----
    def _handle_vault_admin_users_post(self):
        if not self._require_admin():
            return
        try:
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)

        username = (body.get("username") or "").strip().lower()
        password = (body.get("password") or "").strip()
        display  = (body.get("display_name") or username).strip()
        roles    = body.get("roles") or ["BA"]
        quota    = int(body.get("token_quota", 50000))
        is_admin = int(bool(body.get("is_admin", False)))

        if not username or not password:
            return self._send_json({"error": "Thiếu username hoặc password"}, 400)
        if len(password) < 6:
            return self._send_json({"error": "Password phải ít nhất 6 ký tự"}, 400)

        salt    = secrets.token_hex(16)
        pw_hash = _hash_pw(salt, password)
        try:
            with _db() as c:
                c.execute(
                    "INSERT INTO users(username,display_name,password_hash,salt,"
                    "roles_json,token_quota,is_admin) VALUES(?,?,?,?,?,?,?)",
                    (username, display, pw_hash, salt, json.dumps(roles), quota, is_admin),
                )
        except sqlite3.IntegrityError:
            return self._send_json({"error": f"Username '{username}' đã tồn tại"}, 409)
        return self._send_json({"ok": True, "username": username})

    # ---- source line 4946 (Handler._handle_vault_admin_users_put) ----
    def _handle_vault_admin_users_put(self):
        if not self._require_admin():
            return
        uid_str = urlparse(self.path).path.split("/")[-1]
        try:
            uid  = int(uid_str)
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)

        with _db() as c:
            if "roles" in body:
                c.execute("UPDATE users SET roles_json=? WHERE id=?",
                          (json.dumps(body["roles"]), uid))
            if "token_quota" in body:
                c.execute("UPDATE users SET token_quota=? WHERE id=?",
                          (int(body["token_quota"]), uid))
            if "active" in body:
                c.execute("UPDATE users SET active=? WHERE id=?",
                          (int(bool(body["active"])), uid))
            if "display_name" in body:
                c.execute("UPDATE users SET display_name=? WHERE id=?",
                          (str(body["display_name"]).strip(), uid))
            if "new_password" in body:
                new_pw = str(body["new_password"]).strip()
                if len(new_pw) < 6:
                    return self._send_json({"error": "Password phải ít nhất 6 ký tự"}, 400)
                s = secrets.token_hex(16)
                h = _hash_pw(s, new_pw)
                c.execute("UPDATE users SET password_hash=?,salt=?,must_change_pw=1 WHERE id=?",
                          (h, s, uid))
        return self._send_json({"ok": True})

    # ---- source line 4980 (Handler._handle_vault_admin_token_requests_get) ----
    def _handle_vault_admin_token_requests_get(self):
        if not self._require_admin():
            return
        with _db() as c:
            rows = c.execute("""
                SELECT tr.*, u.username, u.token_quota, u.token_used
                FROM   token_requests tr
                JOIN   users u ON u.id = tr.user_id
                ORDER  BY tr.created_at DESC LIMIT 100
            """).fetchall()
        return self._send_json({"requests": [dict(r) for r in rows]})

    # ---- source line 4993 (Handler._handle_vault_admin_token_request_put) ----
    def _handle_vault_admin_token_request_put(self):
        if not self._require_admin():
            return
        rid_str = urlparse(self.path).path.split("/")[-1]
        try:
            rid  = int(rid_str)
            body = self._read_json_body()
        except Exception:
            return self._send_json({"error": "bad request"}, 400)

        action     = (body.get("action") or "").strip()   # approve | deny
        admin_note = (body.get("note") or "").strip()[:300]
        approved_amount = int(body.get("amount", 0))

        if action not in ("approve", "deny"):
            return self._send_json({"error": "action phải là approve hoặc deny"}, 400)

        with _db() as c:
            req = c.execute("SELECT * FROM token_requests WHERE id=?", (rid,)).fetchone()
            if not req:
                return self._send_json({"error": "Request không tồn tại"}, 404)
            if req["status"] != "pending":
                return self._send_json({"error": "Request đã được xử lý rồi"}, 409)

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            status = "approved" if action == "approve" else "denied"
            c.execute(
                "UPDATE token_requests SET status=?,admin_note=?,resolved_at=? WHERE id=?",
                (status, admin_note, now, rid),
            )
            if action == "approve" and approved_amount > 0:
                c.execute(
                    "UPDATE users SET token_quota = token_quota + ? WHERE id=?",
                    (approved_amount, req["user_id"]),
                )

        return self._send_json({"ok": True, "status": status})

