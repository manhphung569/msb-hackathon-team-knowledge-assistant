"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from .db import _db, _get_user_by_token


# ---- source line 386 (_sanitize_project_key) ----
def _sanitize_project_key(project: str | Path) -> str:
    safe = str(Path(str(project).lstrip("/").replace("..", ""))).replace("\\", "/").strip()
    return "." if safe in ("", ".") else safe


# ---- source line 3007 (_create_session) ----
def _create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as c:
        c.execute(
            "INSERT INTO sessions(user_id,token,expires_at) VALUES(?,?,?)",
            (user_id, token, expires),
        )
    return token


class SharedMixin:
    # ---- source line 3411 (Handler._send_json) ----
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- source line 4262 (Handler._session_token) ----
    def _session_token(self) -> str:
        return self.headers.get("X-Session", "").strip()

    # ---- source line 4265 (Handler._require_auth) ----
    def _require_auth(self) -> "dict | None":
        """Returns user dict or sends 401 and returns None."""
        user = _get_user_by_token(self._session_token())
        if not user:
            self._send_json({"error": "unauthorized", "hint": "Login required"}, 401)
            return None
        return user

    # ---- source line 4273 (Handler._require_admin) ----
    def _require_admin(self) -> "dict | None":
        user = self._require_auth()
        if user and not user["is_admin"]:
            self._send_json({"error": "forbidden", "hint": "Admin required"}, 403)
            return None
        return user

    # ---- source line 4280 (Handler._read_json_body) ----
    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))

