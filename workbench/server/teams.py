"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

from .db import _db

# ---- source line 44 (_TEAMS_REQUIRED_ENV) ----
_TEAMS_REQUIRED_ENV = (
    "VAULT_TEAMS_CLIENT_ID",
    "VAULT_TEAMS_CLIENT_SECRET",
    "VAULT_TEAMS_TENANT_ID",
    "VAULT_TEAMS_REDIRECT_URI",
    "VAULT_TEAMS_TOKEN_KEY",
)


# ---- source line 52 (_TEAMS_OAUTH_STATE_TTL_SECONDS) ----
_TEAMS_OAUTH_STATE_TTL_SECONDS = 900


# ---- source line 53 (_TEAMS_DEFAULT_SCOPES) ----
_TEAMS_DEFAULT_SCOPES = "openid offline_access profile User.Read Chat.Read"


# ---- source line 56 (_teams_oauth_config) ----
def _teams_oauth_config() -> dict:
    present = {name: bool(os.environ.get(name)) for name in _TEAMS_REQUIRED_ENV}
    missing = [name for name, ok in present.items() if not ok]
    return {
        "configured": not missing,
        "requiredEnv": list(_TEAMS_REQUIRED_ENV),
        "presentEnv": present,
        "missingEnv": missing,
    }


# ---- source line 67 (_teams_token_cipher) ----
def _teams_token_cipher():
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise RuntimeError("cryptography package is required for Teams token encryption") from exc
    secret = os.environ.get("VAULT_TEAMS_TOKEN_KEY", "")
    if not secret:
        raise RuntimeError("VAULT_TEAMS_TOKEN_KEY is not configured")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


# ---- source line 80 (_teams_encrypt_secret) ----
def _teams_encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _teams_token_cipher().encrypt(plaintext.encode("utf-8")).decode("utf-8")


# ---- source line 86 (_teams_decrypt_secret) ----
def _teams_decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _teams_token_cipher().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# ---- source line 92 (_teams_scope_list) ----
def _teams_scope_list() -> list[str]:
    raw = os.environ.get("VAULT_TEAMS_SCOPES", _TEAMS_DEFAULT_SCOPES)
    return [part for part in re.split(r"[\s,]+", raw.strip()) if part]


# ---- source line 97 (_teams_oauth_tenant) ----
def _teams_oauth_tenant() -> str:
    return os.environ.get("VAULT_TEAMS_TENANT_ID", "common").strip() or "common"


# ---- source line 101 (_teams_sign_state) ----
def _teams_sign_state(payload: dict) -> str:
    secret = os.environ.get("VAULT_TEAMS_TOKEN_KEY", "")
    if not secret:
        raise RuntimeError("VAULT_TEAMS_TOKEN_KEY is not configured")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    packed = json.dumps({"p": payload, "s": sig}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(packed).decode("ascii")


# ---- source line 111 (_teams_verify_state) ----
def _teams_verify_state(state_token: str) -> dict:
    secret = os.environ.get("VAULT_TEAMS_TOKEN_KEY", "")
    if not secret:
        raise RuntimeError("VAULT_TEAMS_TOKEN_KEY is not configured")
    try:
        packed = base64.urlsafe_b64decode(state_token.encode("ascii"))
        data = json.loads(packed.decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid state") from exc
    payload = data.get("p")
    sig = data.get("s") or ""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("invalid state signature")
    issued_at = int(payload.get("iat", 0) or 0)
    if not issued_at or (int(time.time()) - issued_at) > _TEAMS_OAUTH_STATE_TTL_SECONDS:
        raise ValueError("expired state")
    return payload


# ---- source line 132 (_teams_oauth_authorize_url) ----
def _teams_oauth_authorize_url(state_token: str) -> str:
    tenant = _teams_oauth_tenant()
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    query = urlencode({
        "client_id": os.environ.get("VAULT_TEAMS_CLIENT_ID", ""),
        "response_type": "code",
        "redirect_uri": os.environ.get("VAULT_TEAMS_REDIRECT_URI", ""),
        "response_mode": "query",
        "scope": " ".join(_teams_scope_list()),
        "state": state_token,
        "prompt": "select_account",
    })
    return f"{base}?{query}"


# ---- source line 147 (_teams_exchange_code) ----
def _teams_exchange_code(code: str) -> dict:
    import urllib.request as _ur
    import urllib.error as _ue

    tenant = _teams_oauth_tenant()
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    payload = urlencode({
        "client_id": os.environ.get("VAULT_TEAMS_CLIENT_ID", ""),
        "client_secret": os.environ.get("VAULT_TEAMS_CLIENT_SECRET", ""),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": os.environ.get("VAULT_TEAMS_REDIRECT_URI", ""),
        "scope": " ".join(_teams_scope_list()),
    }).encode("utf-8")
    req = _ur.Request(url, data=payload, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with _ur.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except _ue.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(body)
            err = details.get("error_description") or details.get("error") or body
        except Exception:
            err = body or str(exc)
        raise RuntimeError(f"token exchange failed: {err}") from exc


# ---- source line 175 (_teams_graph_me) ----
def _teams_graph_me(access_token: str) -> dict:
    import urllib.request as _ur
    import urllib.error as _ue

    req = _ur.Request(
        "https://graph.microsoft.com/v1.0/me?$select=id,displayName,userPrincipalName,mail",
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with _ur.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except _ue.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(body)
            err = details.get("error", {}).get("message") or body
        except Exception:
            err = body or str(exc)
        raise RuntimeError(f"graph /me failed: {err}") from exc


class TeamsMixin:
    # ---- source line 4284 (Handler._teams_stub_meta) ----
    def _teams_stub_meta(self) -> dict:
        return {
            "implemented": False,
            "mode": "delegated-bridge-skeleton",
            "oauth": _teams_oauth_config(),
        }

    # ---- source line 4291 (Handler._teams_not_ready) ----
    def _teams_not_ready(self, hint: str, extra: dict | None = None, code: int = 501):
        payload = {"error": "not implemented", "hint": hint, **self._teams_stub_meta()}
        if extra:
            payload.update(extra)
        return self._send_json(payload, code)

    # ---- source line 4297 (Handler._vault_agent_to_json) ----
    def _vault_agent_to_json(self, row) -> dict:
        item = dict(row)
        try:
            item["knowledge_scope"] = json.loads(item.pop("knowledge_scope_json", "[]") or "[]")
        except Exception:
            item["knowledge_scope"] = []
        try:
            item["policy"] = json.loads(item.pop("policy_json", "{}") or "{}")
        except Exception:
            item["policy"] = {}
        return item

    # ---- source line 4309 (Handler._teams_account_to_json) ----
    def _teams_account_to_json(self, row) -> dict:
        item = dict(row)
        item.pop("access_token_enc", None)
        item.pop("refresh_token_enc", None)
        try:
            item["scopes"] = json.loads(item.pop("scopes_json", "[]") or "[]")
        except Exception:
            item["scopes"] = []
        return item

    # ---- source line 4319 (Handler._teams_binding_to_json) ----
    def _teams_binding_to_json(self, row) -> dict:
        return dict(row)

    # ---- source line 4322 (Handler._safe_local_return_to) ----
    def _safe_local_return_to(self, value: str | None) -> str:
        candidate = str(value or "/vault/").strip()[:300] or "/vault/"
        if not candidate.startswith("/") or candidate.startswith("//"):
            return "/vault/"
        return candidate

    # ---- source line 4328 (Handler._append_local_query) ----
    def _append_local_query(self, location: str, params: dict[str, str | int]) -> str:
        sep = "&" if "?" in location else "?"
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if not clean:
            return location
        return location + sep + urlencode(clean)

    # ---- source line 4335 (Handler._redirect_location) ----
    def _redirect_location(self, location: str, code: int = 302):
        self.send_response(code)
        self.send_header("Location", location)
        self.end_headers()

    # ---- source line 4340 (Handler._set_teams_account_error) ----
    def _set_teams_account_error(self, account_id: int, message: str = ""):
        with _db() as c:
            c.execute(
                "UPDATE external_accounts SET last_error=?, updated_at=datetime('now') WHERE id=?",
                (str(message or "")[:1000], account_id)
            )

    # ---- source line 4347 (Handler._teams_account_access_token) ----
    def _teams_account_access_token(self, account: dict) -> str:
        if str(account.get("provider") or "") != "microsoft":
            raise RuntimeError("unsupported Teams provider")
        if str(account.get("status") or "") == "disconnected":
            raise RuntimeError("Teams account is disconnected")
        expires_at = str(account.get("expires_at") or "").strip()
        if expires_at and expires_at <= datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"):
            raise RuntimeError("Teams access token expired; reconnect the account from Vault")
        token = _teams_decrypt_secret(str(account.get("access_token_enc") or ""))
        if not token:
            raise RuntimeError("Teams account has no access token; reconnect the account from Vault")
        return token

    # ---- source line 4360 (Handler._teams_graph_get_json) ----
    def _teams_graph_get_json(self, access_token: str, url: str) -> dict:
        import urllib.request as _ur
        import urllib.error as _ue

        req = _ur.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with _ur.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except _ue.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                details = json.loads(body)
                err = details.get("error", {}).get("message") or details.get("error_description") or body
            except Exception:
                err = body or str(exc)
            raise RuntimeError(f"graph request failed: {err}") from exc

    # ---- source line 4381 (Handler._teams_chat_to_json) ----
    def _teams_chat_to_json(self, item: dict) -> dict:
        chat_id = str(item.get("id") or "").strip()
        chat_type = str(item.get("chatType") or "").strip()
        topic = str(item.get("topic") or "").strip()
        title = topic
        if not title:
            title = {
                "oneOnOne": "Direct chat",
                "group": "Group chat",
                "meeting": "Meeting chat",
            }.get(chat_type, chat_id or "Untitled chat")
        return {
            "id": chat_id,
            "title": title,
            "topic": topic,
            "chatType": chat_type,
            "webUrl": str(item.get("webUrl") or "").strip(),
            "createdDateTime": item.get("createdDateTime"),
            "lastUpdatedDateTime": item.get("lastUpdatedDateTime"),
            "tenantId": str(item.get("tenantId") or "").strip(),
        }

    # ---- source line 4403 (Handler._upsert_teams_external_account) ----
    def _upsert_teams_external_account(self, user_id: int, token_data: dict, profile: dict):
        access_token = str(token_data.get("access_token") or "")
        if not access_token:
            raise RuntimeError("token exchange did not return access_token")
        refresh_token = str(token_data.get("refresh_token") or "")
        scopes_raw = str(token_data.get("scope") or " ".join(_teams_scope_list()))
        scopes = [part for part in re.split(r"[\s,]+", scopes_raw.strip()) if part]
        external_user_id = str(profile.get("id") or "").strip()
        if not external_user_id:
            raise RuntimeError("graph /me did not return user id")
        external_username = (
            str(profile.get("userPrincipalName") or "").strip()
            or str(profile.get("mail") or "").strip()
            or str(profile.get("displayName") or "").strip()
            or external_user_id
        )
        tenant_id = _teams_oauth_tenant()
        expires_in = int(token_data.get("expires_in") or 3600)
        expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).strftime("%Y-%m-%d %H:%M:%S")
        enc_access = _teams_encrypt_secret(access_token)
        enc_refresh = _teams_encrypt_secret(refresh_token) if refresh_token else ""
        with _db() as c:
            row = c.execute(
                "SELECT id, refresh_token_enc FROM external_accounts WHERE user_id=? AND provider='microsoft' AND tenant_id=? AND external_user_id=?",
                (user_id, tenant_id, external_user_id)
            ).fetchone()
            if row:
                if not enc_refresh:
                    enc_refresh = row["refresh_token_enc"] or ""
                c.execute(
                    "UPDATE external_accounts SET external_username=?, scopes_json=?, access_token_enc=?, refresh_token_enc=?, expires_at=?, status='connected', last_error='', updated_at=datetime('now') WHERE id=?",
                    (external_username, json.dumps(scopes, ensure_ascii=False), enc_access, enc_refresh, expires_at, row["id"])
                )
                saved = c.execute("SELECT * FROM external_accounts WHERE id=?", (row["id"],)).fetchone()
            else:
                cur = c.execute(
                    "INSERT INTO external_accounts(user_id,provider,tenant_id,external_user_id,external_username,scopes_json,access_token_enc,refresh_token_enc,expires_at,status,last_error) VALUES(?,?,?,?,?,?,?,?,?,'connected','')",
                    (user_id, "microsoft", tenant_id, external_user_id, external_username, json.dumps(scopes, ensure_ascii=False), enc_access, enc_refresh, expires_at)
                )
                saved = c.execute("SELECT * FROM external_accounts WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(saved)

    # ---- source line 4445 (Handler._get_owned_agent) ----
    def _get_owned_agent(self, user: dict, agent_id: int):
        with _db() as c:
            row = c.execute("SELECT * FROM vault_agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            self._send_json({"error": "agent not found"}, 404)
            return None
        if row["owner_id"] != user["id"] and not user["is_admin"]:
            self._send_json({"error": "forbidden"}, 403)
            return None
        return dict(row)

    # ---- source line 4456 (Handler._get_owned_external_account) ----
    def _get_owned_external_account(self, user: dict, account_id: int):
        with _db() as c:
            row = c.execute("SELECT * FROM external_accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            self._send_json({"error": "account not found"}, 404)
            return None
        if row["user_id"] != user["id"] and not user["is_admin"]:
            self._send_json({"error": "forbidden"}, 403)
            return None
        return dict(row)

    # ---- source line 4467 (Handler._get_owned_binding) ----
    def _get_owned_binding(self, user: dict, binding_id: int):
        with _db() as c:
            row = c.execute(
                "SELECT b.*, a.owner_id AS agent_owner_id, ea.user_id AS account_owner_id "
                "FROM teams_chat_bindings b "
                "JOIN vault_agents a ON a.id = b.agent_id "
                "JOIN external_accounts ea ON ea.id = b.external_account_id "
                "WHERE b.id=?",
                (binding_id,)
            ).fetchone()
        if not row:
            self._send_json({"error": "binding not found"}, 404)
            return None
        if not user["is_admin"] and user["id"] not in (row["agent_owner_id"], row["account_owner_id"], row["created_by"]):
            self._send_json({"error": "forbidden"}, 403)
            return None
        return dict(row)

    # ---- source line 4485 (Handler._get_owned_draft) ----
    def _get_owned_draft(self, user: dict, draft_id: int):
        with _db() as c:
            row = c.execute(
                "SELECT d.*, a.owner_id AS agent_owner_id, b.created_by AS binding_creator, ea.user_id AS account_owner_id "
                "FROM outbound_drafts d "
                "JOIN vault_agents a ON a.id = d.agent_id "
                "JOIN teams_chat_bindings b ON b.id = d.binding_id "
                "JOIN external_accounts ea ON ea.id = b.external_account_id "
                "WHERE d.id=?",
                (draft_id,)
            ).fetchone()
        if not row:
            self._send_json({"error": "draft not found"}, 404)
            return None
        if not user["is_admin"] and user["id"] not in (row["agent_owner_id"], row["binding_creator"], row["account_owner_id"]):
            self._send_json({"error": "forbidden"}, 403)
            return None
        return dict(row)

    # ---- source line 9949 (Handler._handle_vault_agent_list) ----
    def _handle_vault_agent_list(self):
        user = self._require_auth()
        if not user: return
        with _db() as c:
            if user["is_admin"]:
                rows = c.execute("SELECT * FROM vault_agents ORDER BY updated_at DESC").fetchall()
            else:
                rows = c.execute("SELECT * FROM vault_agents WHERE owner_id=? ORDER BY updated_at DESC", (user["id"],)).fetchall()
        self._send_json({"items": [self._vault_agent_to_json(r) for r in rows]})

    # ---- source line 9959 (Handler._handle_vault_agent_create) ----
    def _handle_vault_agent_create(self):
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        name = str(body.get("name") or "").strip()[:120]
        if not name:
            return self._send_json({"error": "name is required"}, 400)
        agent_key = str(body.get("agentKey") or f"agt_{int(time.time()*1000)}")[:80]
        description = str(body.get("description") or "")[:1000]
        seed_channel_key = str(body.get("seedChannelKey") or "").strip()[:64] or None
        status = str(body.get("status") or "draft")
        if status not in ("draft", "active", "paused", "archived"):
            status = "draft"
        mode = str(body.get("mode") or "draft_only")
        if mode not in ("summary_only", "draft_only", "approve_then_send", "send_as_user"):
            mode = "draft_only"
        trigger_mode = str(body.get("triggerMode") or "manual")
        if trigger_mode not in ("manual", "poll", "scheduled"):
            trigger_mode = "manual"
        knowledge_scope = body.get("knowledgeScope") if isinstance(body.get("knowledgeScope"), list) else []
        policy = body.get("policy") if isinstance(body.get("policy"), dict) else {}
        with _db() as c:
            if seed_channel_key:
                seed = c.execute("SELECT channel_key FROM orch_channels WHERE channel_key=?", (seed_channel_key,)).fetchone()
                if not seed:
                    return self._send_json({"error": "seed channel not found"}, 404)
            try:
                cur = c.execute(
                    "INSERT INTO vault_agents(owner_id,agent_key,name,description,seed_channel_key,status,mode,trigger_mode,knowledge_scope_json,policy_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (user["id"], agent_key, name, description, seed_channel_key, status, mode, trigger_mode,
                     json.dumps(knowledge_scope, ensure_ascii=False), json.dumps(policy, ensure_ascii=False))
                )
            except sqlite3.IntegrityError:
                return self._send_json({"error": "agent key already exists"}, 409)
            row = c.execute("SELECT * FROM vault_agents WHERE id=?", (cur.lastrowid,)).fetchone()
        self._send_json({"item": self._vault_agent_to_json(row)}, 201)

    # ---- source line 9996 (Handler._handle_vault_agent_update) ----
    def _handle_vault_agent_update(self, agent_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_agent(user, agent_id)
        if not row: return
        body = self._read_json_body()
        updates, vals = [], []
        if "name" in body:
            updates.append("name=?")
            vals.append(str(body["name"]).strip()[:120])
        if "description" in body:
            updates.append("description=?")
            vals.append(str(body["description"]).strip()[:1000])
        if "seedChannelKey" in body:
            seed_channel_key = str(body["seedChannelKey"] or "").strip()[:64] or None
            updates.append("seed_channel_key=?")
            vals.append(seed_channel_key)
        if "status" in body:
            status = str(body["status"])
            if status in ("draft", "active", "paused", "archived"):
                updates.append("status=?")
                vals.append(status)
        if "mode" in body:
            mode = str(body["mode"])
            if mode in ("summary_only", "draft_only", "approve_then_send", "send_as_user"):
                updates.append("mode=?")
                vals.append(mode)
        if "triggerMode" in body:
            trigger_mode = str(body["triggerMode"])
            if trigger_mode in ("manual", "poll", "scheduled"):
                updates.append("trigger_mode=?")
                vals.append(trigger_mode)
        if "knowledgeScope" in body and isinstance(body["knowledgeScope"], list):
            updates.append("knowledge_scope_json=?")
            vals.append(json.dumps(body["knowledgeScope"], ensure_ascii=False))
        if "policy" in body and isinstance(body["policy"], dict):
            updates.append("policy_json=?")
            vals.append(json.dumps(body["policy"], ensure_ascii=False))
        if updates:
            with _db() as c:
                vals.append(agent_id)
                c.execute(f"UPDATE vault_agents SET {', '.join(updates)}, updated_at=datetime('now') WHERE id=?", vals)
                fresh = c.execute("SELECT * FROM vault_agents WHERE id=?", (agent_id,)).fetchone()
            return self._send_json({"item": self._vault_agent_to_json(fresh)})
        return self._send_json({"item": self._vault_agent_to_json(row)})

    # ---- source line 10042 (Handler._handle_vault_agent_delete) ----
    def _handle_vault_agent_delete(self, agent_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_agent(user, agent_id)
        if not row: return
        with _db() as c:
            c.execute("DELETE FROM vault_agents WHERE id=?", (agent_id,))
        self._send_json({"ok": True})

    # ---- source line 10051 (Handler._handle_teams_connect_start) ----
    def _handle_teams_connect_start(self):
        user = self._require_auth()
        if not user: return
        body = {}
        try:
            body = self._read_json_body()
        except Exception:
            body = {}
        return_to = self._safe_local_return_to(body.get("returnTo") or "/vault/")
        config = _teams_oauth_config()
        if not config["configured"]:
            return self._teams_not_ready("Teams OAuth env is missing required variables.", {"returnTo": return_to}, 501)
        try:
            state_token = _teams_sign_state({
                "user_id": int(user["id"]),
                "return_to": return_to,
                "iat": int(time.time()),
            })
            auth_url = _teams_oauth_authorize_url(state_token)
        except Exception as exc:
            return self._send_json({"error": "teams oauth init failed", "hint": str(exc), "returnTo": return_to}, 500)
        return self._send_json({
            "authUrl": auth_url,
            "returnTo": return_to,
            "expiresIn": _TEAMS_OAUTH_STATE_TTL_SECONDS,
            "implemented": True,
            "oauth": config,
        })

    # ---- source line 10080 (Handler._handle_teams_connect_callback) ----
    def _handle_teams_connect_callback(self):
        params = {k: (v[-1] if isinstance(v, list) and v else "") for k, v in parse_qs(urlparse(self.path).query).items()}
        return_to = "/vault/"
        state_token = params.get("state") or ""
        if state_token:
            try:
                payload = _teams_verify_state(state_token)
                return_to = self._safe_local_return_to(payload.get("return_to") or "/vault/")
            except Exception:
                return_to = "/vault/"
        if params.get("error"):
            err = params.get("error_description") or params.get("error") or "oauth_error"
            return self._redirect_location(self._append_local_query(return_to, {
                "teams": "error",
                "teams_error": err[:240],
            }))
        try:
            payload = _teams_verify_state(state_token)
        except Exception as exc:
            return self._redirect_location(self._append_local_query(return_to, {
                "teams": "error",
                "teams_error": str(exc)[:240],
            }))
        user_id = int(payload.get("user_id") or 0)
        if not user_id:
            return self._redirect_location(self._append_local_query(return_to, {
                "teams": "error",
                "teams_error": "missing_user_in_state",
            }))
        code = params.get("code") or ""
        if not code:
            return self._redirect_location(self._append_local_query(return_to, {
                "teams": "error",
                "teams_error": "missing_authorization_code",
            }))
        with _db() as c:
            user_row = c.execute("SELECT id, active FROM users WHERE id=?", (user_id,)).fetchone()
        if not user_row or not int(user_row["active"] or 0):
            return self._redirect_location(self._append_local_query(return_to, {
                "teams": "error",
                "teams_error": "vault_user_not_found_or_inactive",
            }))
        try:
            token_data = _teams_exchange_code(code)
            profile = _teams_graph_me(str(token_data.get("access_token") or ""))
            account = self._upsert_teams_external_account(user_id, token_data, profile)
        except Exception as exc:
            return self._redirect_location(self._append_local_query(return_to, {
                "teams": "error",
                "teams_error": str(exc)[:240],
            }))
        return self._redirect_location(self._append_local_query(return_to, {
            "teams": "connected",
            "provider": "microsoft",
            "accountId": account["id"],
        }))

    # ---- source line 10137 (Handler._handle_teams_accounts_list) ----
    def _handle_teams_accounts_list(self):
        user = self._require_auth()
        if not user: return
        with _db() as c:
            if user["is_admin"]:
                rows = c.execute("SELECT * FROM external_accounts ORDER BY updated_at DESC").fetchall()
            else:
                rows = c.execute("SELECT * FROM external_accounts WHERE user_id=? ORDER BY updated_at DESC", (user["id"],)).fetchall()
        self._send_json({"items": [self._teams_account_to_json(r) for r in rows], **self._teams_stub_meta()})

    # ---- source line 10147 (Handler._handle_teams_account_delete) ----
    def _handle_teams_account_delete(self, account_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_external_account(user, account_id)
        if not row: return
        with _db() as c:
            c.execute(
                "UPDATE external_accounts SET status='disconnected', access_token_enc='', refresh_token_enc='', updated_at=datetime('now') WHERE id=?",
                (account_id,)
            )
            c.execute(
                "UPDATE teams_chat_bindings SET status='revoked', ingest_enabled=0, updated_at=datetime('now') WHERE external_account_id=?",
                (account_id,)
            )
        self._send_json({"ok": True})

    # ---- source line 10163 (Handler._handle_teams_chats_list) ----
    def _handle_teams_chats_list(self):
        user = self._require_auth()
        if not user: return
        qs = parse_qs(urlparse(self.path).query)
        account_id = int((qs.get("accountId") or ["0"])[0] or 0)
        try:
            top = int((qs.get("top") or ["50"])[0] or 50)
        except Exception:
            top = 50
        top = max(1, min(top, 100))
        if not account_id:
            return self._send_json({"error": "accountId is required"}, 400)
        account = self._get_owned_external_account(user, account_id)
        if not account: return
        try:
            access_token = self._teams_account_access_token(account)
            graph = self._teams_graph_get_json(
                access_token,
                f"https://graph.microsoft.com/v1.0/me/chats?$top={top}"
            )
            items = [
                self._teams_chat_to_json(item)
                for item in (graph.get("value") or [])
                if isinstance(item, dict)
            ]
            items.sort(
                key=lambda item: str(item.get("lastUpdatedDateTime") or item.get("createdDateTime") or ""),
                reverse=True,
            )
            self._set_teams_account_error(account_id, "")
        except Exception as exc:
            self._set_teams_account_error(account_id, str(exc))
            return self._send_json({
                **self._teams_stub_meta(),
                "implemented": True,
                "mode": "delegated-bridge-graph",
                "error": "teams chat discovery failed",
                "details": str(exc)[:240],
                "account": self._teams_account_to_json(account),
            }, 502)
        self._send_json({
            **self._teams_stub_meta(),
            "implemented": True,
            "mode": "delegated-bridge-graph",
            "items": items,
            "count": len(items),
            "nextLink": str(graph.get("@odata.nextLink") or ""),
            "account": self._teams_account_to_json(account),
        })

    # ---- source line 10213 (Handler._handle_teams_bindings_list) ----
    def _handle_teams_bindings_list(self):
        user = self._require_auth()
        if not user: return
        with _db() as c:
            if user["is_admin"]:
                rows = c.execute(
                    "SELECT b.*, a.name AS agent_name, ea.external_username, ea.provider "
                    "FROM teams_chat_bindings b "
                    "JOIN vault_agents a ON a.id = b.agent_id "
                    "JOIN external_accounts ea ON ea.id = b.external_account_id "
                    "ORDER BY b.updated_at DESC"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT b.*, a.name AS agent_name, ea.external_username, ea.provider "
                    "FROM teams_chat_bindings b "
                    "JOIN vault_agents a ON a.id = b.agent_id "
                    "JOIN external_accounts ea ON ea.id = b.external_account_id "
                    "WHERE a.owner_id=? OR ea.user_id=? OR b.created_by=? "
                    "ORDER BY b.updated_at DESC",
                    (user["id"], user["id"], user["id"])
                ).fetchall()
        self._send_json({"items": [self._teams_binding_to_json(r) for r in rows], **self._teams_stub_meta()})

    # ---- source line 10237 (Handler._handle_teams_binding_create) ----
    def _handle_teams_binding_create(self):
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        agent_id = int(body.get("agentId", 0) or 0)
        account_id = int(body.get("accountId", 0) or 0)
        chat_id = str(body.get("chatId") or "").strip()[:255]
        if not agent_id or not account_id or not chat_id:
            return self._send_json({"error": "agentId, accountId and chatId are required"}, 400)
        agent = self._get_owned_agent(user, agent_id)
        if not agent: return
        account = self._get_owned_external_account(user, account_id)
        if not account: return
        sync_mode = str(body.get("syncMode") or "polling")
        if sync_mode not in ("manual", "polling"):
            sync_mode = "polling"
        postback_mode = str(body.get("postbackMode") or "none")
        if postback_mode not in ("none", "draft", "approve_then_send", "send_as_user"):
            postback_mode = "none"
        chat_topic = str(body.get("chatTopic") or "")[:255]
        chat_type = str(body.get("chatType") or "groupchat")[:40]
        tenant_id = str(body.get("tenantId") or account.get("tenant_id") or "")[:120]
        with _db() as c:
            try:
                cur = c.execute(
                    "INSERT INTO teams_chat_bindings(agent_id,external_account_id,tenant_id,chat_id,chat_topic,chat_type,sync_mode,postback_mode,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                    (agent_id, account_id, tenant_id, chat_id, chat_topic, chat_type, sync_mode, postback_mode, user["id"])
                )
            except sqlite3.IntegrityError:
                return self._send_json({"error": "binding already exists"}, 409)
            row = c.execute("SELECT * FROM teams_chat_bindings WHERE id=?", (cur.lastrowid,)).fetchone()
        self._send_json({"item": self._teams_binding_to_json(row)}, 201)

    # ---- source line 10270 (Handler._handle_teams_binding_update) ----
    def _handle_teams_binding_update(self, binding_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_binding(user, binding_id)
        if not row: return
        body = self._read_json_body()
        updates, vals = [], []
        if "chatTopic" in body:
            updates.append("chat_topic=?")
            vals.append(str(body["chatTopic"])[:255])
        if "lastCursor" in body:
            updates.append("last_cursor=?")
            vals.append(str(body["lastCursor"])[:255])
        if "lastMessageAt" in body:
            updates.append("last_message_at=?")
            vals.append(str(body["lastMessageAt"])[:64])
        if "syncMode" in body:
            sync_mode = str(body["syncMode"])
            if sync_mode in ("manual", "polling"):
                updates.append("sync_mode=?")
                vals.append(sync_mode)
        if "postbackMode" in body:
            postback_mode = str(body["postbackMode"])
            if postback_mode in ("none", "draft", "approve_then_send", "send_as_user"):
                updates.append("postback_mode=?")
                vals.append(postback_mode)
        if "status" in body:
            status = str(body["status"])
            if status in ("active", "paused", "error", "revoked"):
                updates.append("status=?")
                vals.append(status)
        if "ingestEnabled" in body:
            updates.append("ingest_enabled=?")
            vals.append(1 if body["ingestEnabled"] else 0)
        if updates:
            with _db() as c:
                vals.append(binding_id)
                c.execute(f"UPDATE teams_chat_bindings SET {', '.join(updates)}, updated_at=datetime('now') WHERE id=?", vals)
                fresh = c.execute("SELECT * FROM teams_chat_bindings WHERE id=?", (binding_id,)).fetchone()
            return self._send_json({"item": self._teams_binding_to_json(fresh)})
        return self._send_json({"item": self._teams_binding_to_json(row)})

    # ---- source line 10312 (Handler._handle_teams_binding_delete) ----
    def _handle_teams_binding_delete(self, binding_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_binding(user, binding_id)
        if not row: return
        with _db() as c:
            c.execute("DELETE FROM teams_chat_bindings WHERE id=?", (binding_id,))
        self._send_json({"ok": True})

    # ---- source line 10321 (Handler._handle_teams_binding_sync) ----
    def _handle_teams_binding_sync(self, binding_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_binding(user, binding_id)
        if not row: return
        return self._teams_not_ready(
            "Microsoft Graph sync worker is not implemented yet.",
            {"binding": self._teams_binding_to_json(row), "queuedEvents": 0},
            501,
        )

    # ---- source line 10332 (Handler._handle_teams_binding_events) ----
    def _handle_teams_binding_events(self, binding_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_binding(user, binding_id)
        if not row: return
        with _db() as c:
            events = c.execute(
                "SELECT id, provider, external_message_id, thread_key, sender_display, direction, message_type, sent_at, content_text, event_status, processed_at, created_at "
                "FROM connector_events WHERE binding_id=? ORDER BY created_at DESC LIMIT 100",
                (binding_id,)
            ).fetchall()
        self._send_json({"items": [dict(e) for e in events], "binding": self._teams_binding_to_json(row)})

    # ---- source line 10345 (Handler._handle_teams_binding_threads) ----
    def _handle_teams_binding_threads(self, binding_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_binding(user, binding_id)
        if not row: return
        with _db() as c:
            threads = c.execute(
                "SELECT id, thread_key, title, memory_summary, last_external_message_id, last_summarized_at, state_json, created_at, updated_at "
                "FROM connector_threads WHERE binding_id=? ORDER BY updated_at DESC LIMIT 100",
                (binding_id,)
            ).fetchall()
        items = []
        for t in threads:
            item = dict(t)
            try:
                item["state"] = json.loads(item.pop("state_json", "{}") or "{}")
            except Exception:
                item["state"] = {}
            items.append(item)
        self._send_json({"items": items, "binding": self._teams_binding_to_json(row)})

    # ---- source line 10366 (Handler._handle_teams_drafts_list) ----
    def _handle_teams_drafts_list(self):
        user = self._require_auth()
        if not user: return
        qs = parse_qs(urlparse(self.path).query)
        binding_filter = int((qs.get("bindingId") or ["0"])[0] or 0)
        with _db() as c:
            if user["is_admin"]:
                base_sql = (
                    "SELECT d.*, a.name AS agent_name, b.chat_topic, b.chat_id "
                    "FROM outbound_drafts d "
                    "JOIN vault_agents a ON a.id = d.agent_id "
                    "JOIN teams_chat_bindings b ON b.id = d.binding_id "
                )
                if binding_filter:
                    rows = c.execute(base_sql + "WHERE d.binding_id=? ORDER BY d.created_at DESC LIMIT 100", (binding_filter,)).fetchall()
                else:
                    rows = c.execute(base_sql + "ORDER BY d.created_at DESC LIMIT 100").fetchall()
            else:
                base_sql = (
                    "SELECT d.*, a.name AS agent_name, b.chat_topic, b.chat_id "
                    "FROM outbound_drafts d "
                    "JOIN vault_agents a ON a.id = d.agent_id "
                    "JOIN teams_chat_bindings b ON b.id = d.binding_id "
                    "LEFT JOIN external_accounts ea ON ea.id = b.external_account_id "
                    "WHERE (a.owner_id=? OR b.created_by=? OR ea.user_id=?) "
                )
                params = [user["id"], user["id"], user["id"]]
                if binding_filter:
                    base_sql += "AND d.binding_id=? "
                    params.append(binding_filter)
                rows = c.execute(base_sql + "ORDER BY d.created_at DESC LIMIT 100", params).fetchall()
        self._send_json({"items": [dict(r) for r in rows]})

    # ---- source line 10399 (Handler._handle_teams_draft_approve) ----
    def _handle_teams_draft_approve(self, draft_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_draft(user, draft_id)
        if not row: return
        with _db() as c:
            c.execute(
                "UPDATE outbound_drafts SET approval_status='approved', approved_by=?, approved_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
                (user["id"], draft_id)
            )
            fresh = c.execute("SELECT * FROM outbound_drafts WHERE id=?", (draft_id,)).fetchone()
        self._send_json({"item": dict(fresh)})

    # ---- source line 10412 (Handler._handle_teams_draft_reject) ----
    def _handle_teams_draft_reject(self, draft_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_draft(user, draft_id)
        if not row: return
        body = {}
        try:
            body = self._read_json_body()
        except Exception:
            body = {}
        note = str(body.get("note") or "")[:1000]
        with _db() as c:
            c.execute(
                "UPDATE outbound_drafts SET approval_status='rejected', post_result_json=?, updated_at=datetime('now') WHERE id=?",
                (json.dumps({"note": note}, ensure_ascii=False), draft_id)
            )
            fresh = c.execute("SELECT * FROM outbound_drafts WHERE id=?", (draft_id,)).fetchone()
        self._send_json({"item": dict(fresh)})

    # ---- source line 10431 (Handler._handle_teams_draft_post) ----
    def _handle_teams_draft_post(self, draft_id: int):
        user = self._require_auth()
        if not user: return
        row = self._get_owned_draft(user, draft_id)
        if not row: return
        return self._teams_not_ready(
            "Teams outbound postback is not implemented yet.",
            {"draft": dict(row)},
            501,
        )

