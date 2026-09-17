"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
from datetime import datetime

from .db import _db


class ChannelsMixin:
    # ---- source line 9701 (Handler._channels_get) ----
    def _channels_get(self, with_artifacts=True):
        """Return all channels with optional artifact lists."""
        with _db() as c:
            rows = c.execute(
                "SELECT id, channel_key, icon, label, is_system, mode, enabled, created_by "
                "FROM orch_channels ORDER BY is_system DESC, id"
            ).fetchall()
            result = []
            for r in rows:
                ch = dict(r)
                if with_artifacts:
                    arts = c.execute(
                        "SELECT id, artifact_key, sort_order, enabled, icon, name, description, "
                        "output_type, sources_json, ai_prompt, template, checklist_json "
                        "FROM orch_channel_artifacts WHERE channel_id=? ORDER BY sort_order",
                        (ch["id"],)
                    ).fetchall()
                    ch["artifacts"] = [dict(a) for a in arts]
                result.append(ch)
        return result

    # ---- source line 9722 (Handler._handle_channels_list) ----
    def _handle_channels_list(self):
        """GET /vault/channels"""
        user = self._require_auth()
        if not user: return
        self._send_json(self._channels_get())

    # ---- source line 9728 (Handler._handle_channels_sync) ----
    def _handle_channels_sync(self):
        """POST /vault/channels/sync
        Bulk-upsert entire channel config from client (ps_data structure).
        Body: { channels: [ {channel_key, icon, label, is_system, mode, enabled,
                              artifacts: [{artifact_key, enabled, icon, name, description,
                                           output_type, sources_json, ai_prompt, template,
                                           checklist_json}] } ] }
        """
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        channels = body.get("channels", [])
        with _db() as c:
            for ch in channels:
                key = str(ch.get("channel_key", ""))[:64]
                if not key: continue
                is_sys = 1 if ch.get("is_system") else 0
                mode   = "public" if ch.get("mode") == "public" else "private"
                enabled = 1 if ch.get("enabled", True) else 0
                icon  = str(ch.get("icon", "⚙️"))[:8]
                label = str(ch.get("label", key))[:120]
                # created_by: system channels → NULL, custom → caller
                created_by = None if is_sys else (ch.get("created_by") or user["id"])
                row = c.execute("SELECT id, is_system, created_by FROM orch_channels WHERE channel_key=?", (key,)).fetchone()
                if row:
                    # NULL created_by = legacy/unowned → any authenticated user may update
                    if not row["is_system"] and row["created_by"] is not None \
                            and row["created_by"] != user["id"] and not user["is_admin"]:
                        continue
                    c.execute(
                        "UPDATE orch_channels SET icon=?, label=?, mode=?, enabled=?, updated_at=datetime('now') WHERE id=?",
                        (icon, label, mode, enabled, row["id"])
                    )
                    ch_id = row["id"]
                else:
                    cur = c.execute(
                        "INSERT INTO orch_channels(channel_key,icon,label,is_system,mode,enabled,created_by) VALUES(?,?,?,?,?,?,?)",
                        (key, icon, label, is_sys, mode, enabled, created_by)
                    )
                    ch_id = cur.lastrowid
                # Upsert artifacts
                kept_keys = []
                for idx, a in enumerate(ch.get("artifacts", [])):
                    akey = str(a.get("artifact_key", ""))[:64]
                    if not akey: continue
                    kept_keys.append(akey)
                    exists = c.execute(
                        "SELECT id FROM orch_channel_artifacts WHERE channel_id=? AND artifact_key=?",
                        (ch_id, akey)
                    ).fetchone()
                    fields = (
                        1 if a.get("enabled", True) else 0,
                        str(a.get("icon","📄"))[:8],
                        str(a.get("name",""))[:200],
                        str(a.get("description",""))[:500],
                        str(a.get("output_type","Markdown (.md)"))[:100],
                        json.dumps(a.get("sources_json") if isinstance(a.get("sources_json"), list) else []),
                        str(a.get("ai_prompt","")),
                        str(a.get("template","")),
                        json.dumps(a.get("checklist_json")) if a.get("checklist_json") is not None else "null",
                        idx,
                    )
                    if exists:
                        c.execute("""UPDATE orch_channel_artifacts SET
                            enabled=?,icon=?,name=?,description=?,output_type=?,
                            sources_json=?,ai_prompt=?,template=?,checklist_json=?,
                            sort_order=?,updated_at=datetime('now')
                            WHERE id=?""", (*fields, exists["id"]))
                    else:
                        c.execute("""INSERT INTO orch_channel_artifacts
                            (channel_id,artifact_key,enabled,icon,name,description,output_type,
                             sources_json,ai_prompt,template,checklist_json,sort_order)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (ch_id, akey, *fields))
                # Delete artifacts removed from payload
                # Never touch a system channel's artifacts (protects pre-seeded defaults)
                if kept_keys and not is_sys:
                    placeholders = ",".join("?" * len(kept_keys))
                    c.execute(
                        f"DELETE FROM orch_channel_artifacts WHERE channel_id=? AND artifact_key NOT IN ({placeholders})",
                        (ch_id, *kept_keys)
                    )
                elif not kept_keys and not is_sys:
                    # Only clear custom channels when payload explicitly sends empty artifacts
                    c.execute("DELETE FROM orch_channel_artifacts WHERE channel_id=?", (ch_id,))
        self._send_json({"ok": True, "channels": self._channels_get()})

    # ---- source line 9815 (Handler._handle_channel_create) ----
    def _handle_channel_create(self):
        """POST /vault/channels — create a custom channel."""
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        key  = "cx_" + str(body.get("channel_key", ""))[:50] or "cx_" + str(int(datetime.utcnow().timestamp()*1000))
        icon  = str(body.get("icon","⚙️"))[:8]
        label = str(body.get("label","New Channel"))[:120]
        mode  = "public" if body.get("mode") == "public" else "private"
        with _db() as c:
            if c.execute("SELECT id FROM orch_channels WHERE channel_key=?", (key,)).fetchone():
                return self._send_json({"error": "key exists"}, 409)
            cur = c.execute(
                "INSERT INTO orch_channels(channel_key,icon,label,is_system,mode,enabled,created_by) VALUES(?,?,?,0,?,1,?)",
                (key, icon, label, mode, user["id"])
            )
            ch_id = cur.lastrowid
        self._send_json({"id": ch_id, "channel_key": key})

    # ---- source line 9834 (Handler._handle_channel_update) ----
    def _handle_channel_update(self, key: str):
        """PATCH /vault/channels/{key} — update channel meta."""
        user = self._require_auth()
        if not user: return
        with _db() as c:
            row = c.execute("SELECT id, is_system, created_by FROM orch_channels WHERE channel_key=?", (key,)).fetchone()
            if not row: return self._send_json({"error": "not found"}, 404)
            if not row["is_system"] and row["created_by"] is not None \
                    and row["created_by"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            body = self._read_json_body()
            updates, vals = [], []
            for field in ("icon","label","mode","enabled"):
                if field in body:
                    updates.append(f"{field}=?")
                    val = body[field]
                    if field == "enabled": val = 1 if val else 0
                    vals.append(val)
            if updates:
                vals.append(row["id"])
                c.execute(f"UPDATE orch_channels SET {', '.join(updates)}, updated_at=datetime('now') WHERE id=?", vals)
        self._send_json({"ok": True})

    # ---- source line 9857 (Handler._handle_channel_delete) ----
    def _handle_channel_delete(self, key: str):
        """DELETE /vault/channels/{key} — delete custom channel (not system)."""
        user = self._require_auth()
        if not user: return
        with _db() as c:
            row = c.execute("SELECT id, is_system, created_by FROM orch_channels WHERE channel_key=?", (key,)).fetchone()
            if not row: return self._send_json({"error": "not found"}, 404)
            if row["is_system"]: return self._send_json({"error": "cannot delete system channel"}, 403)
            # NULL created_by = legacy/unowned → any authenticated user may delete
            if row["created_by"] is not None and row["created_by"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            c.execute("DELETE FROM orch_channels WHERE id=?", (row["id"],))
        self._send_json({"ok": True})

    # ---- source line 9871 (Handler._handle_channel_artifact_add) ----
    def _handle_channel_artifact_add(self, key: str):
        """POST /vault/channels/{key}/artifacts"""
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        with _db() as c:
            ch = c.execute("SELECT id, is_system, created_by FROM orch_channels WHERE channel_key=?", (key,)).fetchone()
            if not ch: return self._send_json({"error": "channel not found"}, 404)
            if not ch["is_system"] and ch["created_by"] is not None \
                    and ch["created_by"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            akey = str(body.get("artifact_key",""))[:64] or ("art_" + str(int(datetime.utcnow().timestamp()*1000)))
            max_ord = (c.execute("SELECT COALESCE(MAX(sort_order),0) FROM orch_channel_artifacts WHERE channel_id=?", (ch["id"],)).fetchone()[0] or 0)
            cur = c.execute("""INSERT INTO orch_channel_artifacts
                (channel_id,artifact_key,sort_order,enabled,icon,name,description,output_type,sources_json,ai_prompt,template,checklist_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ch["id"], akey, max_ord+1,
                 1 if body.get("enabled",True) else 0,
                 str(body.get("icon","📄"))[:8],
                 str(body.get("name","New Artifact"))[:200],
                 str(body.get("description",""))[:500],
                 str(body.get("output_type","Markdown (.md)"))[:100],
                 json.dumps(body.get("sources_json") if isinstance(body.get("sources_json"),list) else []),
                 str(body.get("ai_prompt","")),
                 str(body.get("template","")),
                 json.dumps(body.get("checklist_json")) if body.get("checklist_json") is not None else "null",
                ))
            art_id = cur.lastrowid
        self._send_json({"id": art_id, "artifact_key": akey})

    # ---- source line 9901 (Handler._handle_channel_artifact_update) ----
    def _handle_channel_artifact_update(self, key: str, art_id: int):
        """PUT /vault/channels/{key}/artifacts/{id}"""
        user = self._require_auth()
        if not user: return
        with _db() as c:
            ch = c.execute("SELECT id, is_system, created_by FROM orch_channels WHERE channel_key=?", (key,)).fetchone()
            if not ch: return self._send_json({"error": "not found"}, 404)
            if not ch["is_system"] and ch["created_by"] is not None \
                    and ch["created_by"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            body = self._read_json_body()
            updates, vals = [], []
            field_map = {"enabled": ("enabled", lambda v: 1 if v else 0),
                         "icon":    ("icon",    lambda v: str(v)[:8]),
                         "name":    ("name",    lambda v: str(v)[:200]),
                         "description": ("description", lambda v: str(v)[:500]),
                         "output_type": ("output_type", lambda v: str(v)[:100]),
                         "sources_json": ("sources_json", lambda v: json.dumps(v) if isinstance(v,list) else "[]"),
                         "ai_prompt":    ("ai_prompt",  str),
                         "template":     ("template",   str),
                         "checklist_json": ("checklist_json", lambda v: json.dumps(v) if v is not None else "null"),
                         "sort_order": ("sort_order", int)}
            for k, (col, cast) in field_map.items():
                if k in body:
                    updates.append(f"{col}=?")
                    vals.append(cast(body[k]))
            if updates:
                vals += [art_id, ch["id"]]
                c.execute(f"UPDATE orch_channel_artifacts SET {', '.join(updates)}, updated_at=datetime('now') WHERE id=? AND channel_id=?", vals)
        self._send_json({"ok": True})

    # ---- source line 9932 (Handler._handle_channel_artifact_delete) ----
    def _handle_channel_artifact_delete(self, key: str, art_id: int):
        """DELETE /vault/channels/{key}/artifacts/{id}"""
        user = self._require_auth()
        if not user: return
        with _db() as c:
            ch = c.execute("SELECT id, is_system, created_by FROM orch_channels WHERE channel_key=?", (key,)).fetchone()
            if not ch: return self._send_json({"error": "not found"}, 404)
            if not ch["is_system"] and ch["created_by"] is not None \
                    and ch["created_by"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            c.execute("DELETE FROM orch_channel_artifacts WHERE id=? AND channel_id=?", (art_id, ch["id"]))
        self._send_json({"ok": True})

