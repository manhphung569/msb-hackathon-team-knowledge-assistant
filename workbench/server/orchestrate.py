"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import os
import traceback
from datetime import datetime
from urllib.parse import urlparse

from .conversations import _get_or_create_conv, _save_messages
from .db import _db

# _parse_retry_after/_bl_call_llm_ex/_bl_call_llm moved to llm.py (2026-09-14) to break a
# circular import with conversations.py — see llm.py's module docstring.
from .llm import _bl_call_llm, _bl_call_llm_ex, _parse_retry_after  # noqa: F401 (re-exported for callers using orchestrate._bl_call_llm)


class OrchestrateMixin:
    # ---- source line 7475 (Handler._handle_orchestrate) ----
    def _handle_orchestrate(self):
        try:
            return self.__orchestrate_inner()
        except Exception as e:
            import traceback; traceback.print_exc()
            return self._send_json({"error": f"Server error: {e}"}, 500)

    # ---- source line 7482 (Handler.__orchestrate_inner) ----
    def __orchestrate_inner(self):
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        item_id  = body.get("backlog_item_id")
        system   = body.get("system", "")
        ws       = body.get("workspace", "")
        proj     = body.get("project", "")
        artifacts = [a for a in body.get("artifacts", []) if a.get("on")]

        if not item_id or not system or not artifacts:
            return self._send_json({"error": "Missing backlog_item_id, system or artifacts"}, 400)

        with _db() as c:
            row = c.execute("""SELECT bi.*, bs.title as session_title,
                               bs.workspace as bs_workspace, bs.project as bs_project
                FROM backlog_items bi JOIN backlog_sessions bs ON bs.id=bi.session_id
                WHERE bi.id=?""", (int(item_id),)).fetchone()
        if not row:
            return self._send_json({"error": "Backlog item not found"}, 404)
        item = dict(row)

        eff_ws   = ws   or item.get("bs_workspace") or ""
        eff_proj = proj or item.get("bs_project")   or ""
        vault_root = self._resolve_vault_root(eff_ws, eff_proj)
        total_tokens_used = 0

        # Create run record
        with _db() as c:
            cur = c.execute("INSERT INTO orch_runs(backlog_item_id,system) VALUES(?,?)",
                            (int(item_id), system))
            run_id = cur.lastrowid

        results = []
        for art in artifacts:
            try:
                vault_ctx = self._search_vault_sources(art.get("src", []), vault_root, item["title"])
                content, usage = _bl_call_llm_ex(
                    *self._build_artifact_prompt(item, art, vault_ctx), max_tokens=3000)
                total_tokens_used += usage.get("total", 0)
                status = "done"
                error  = None
            except Exception as e:
                content, usage, status, error = "", {}, "error", str(e)

            # Compute version number for this artifact_key in this item
            with _db() as c:
                prev = c.execute("""SELECT COUNT(*) FROM orch_artifacts oa
                    JOIN orch_runs r ON r.id=oa.run_id
                    WHERE r.backlog_item_id=? AND oa.artifact_key=?""",
                    (int(item_id), art["id"])).fetchone()[0]
                version = prev + 1
                cur = c.execute(
                    "INSERT INTO orch_artifacts(run_id,artifact_key,name,icon,output_type,content,version) VALUES(?,?,?,?,?,?,?)",
                    (run_id, art["id"], art["name"], art.get("icon","📄"),
                     art.get("out","Markdown (.md)"), content, version))
                art_db_id = cur.lastrowid

            rec = {"id": art["id"], "db_id": art_db_id, "run_id": run_id,
                   "name": art["name"], "icon": art.get("icon","📄"),
                   "out": art.get("out","Markdown (.md)"),
                   "content": content, "version": version, "status": status}
            if error: rec["error"] = error
            results.append(rec)

        quota = self._charge_tokens(user, {"total": total_tokens_used})
        return self._send_json({"system": system, "backlog_item": item,
                                "run_id": run_id, "artifacts": results,
                                "total_tokens": total_tokens_used, "quota": quota})

    # ---- source line 7664 (Handler._build_artifact_prompt) ----
    def _build_artifact_prompt(self, item: dict, artifact: dict, vault_ctx: dict) -> tuple:
        """Returns (sys_prompt, user_msg) for LLM calls."""
        vault_text = "\n\n".join(
            f"=== VAULT: {s.upper()} ===\n{c}" for s, c in vault_ctx.items()
        ) or "[No vault knowledge — apply domain expertise and best practices]"
        sys_prompt = (
            "You are a senior business analyst and technical architect with deep domain expertise. "
            "Your task: generate a COMPLETE, PRODUCTION-READY artifact for a software backlog item.\n\n"
            "APPROACH (follow strictly):\n"
            "1. ANALYZE the vault knowledge and backlog context to understand the full picture.\n"
            "2. PROPOSE the best possible approach — even if some information is missing, apply domain expertise, "
            "industry best practices, and reasonable assumptions to fill the gaps.\n"
            "3. GENERATE the artifact in FULL — every section must be complete and professional. "
            "Never write '[AI điền...]', '[TBD]', '[...]', or any placeholder. "
            "If information is truly unknown, write your best expert recommendation clearly.\n"
            "4. The output must be ready to use immediately — stakeholders should be able to act on it without further editing.\n\n"
            "Language: match the template language (Vietnamese if template is in Vietnamese, English if English).\n"
            "Output ONLY the artifact content. No preamble, no explanation, no meta-commentary."
        )
        user_msg = (
            f"## BACKLOG ITEM\n"
            f"**Title:** {item['title']}\n"
            f"**Description:** {item.get('description','(no description)')}\n"
            f"**Priority:** {item.get('priority','medium')} | **Domain:** {item.get('domain','') or 'General'}\n\n"
            f"## VAULT KNOWLEDGE (toàn bộ Vault — specs, scenarios, patterns, testcases, knowledge, diagrams, integration, changes)\n{vault_text}\n\n"
            f"## SYNTHESIS INSTRUCTION\n"
            f"{artifact.get('prompt','Generate the best possible artifact based on available knowledge.')}\n\n"
            f"## OUTPUT STRUCTURE (use this as the skeleton — fill every section completely)\n"
            f"{artifact.get('tpl','')}\n\n"
            f"## TARGET FORMAT\n{artifact.get('out','Markdown')}\n\n"
            f"Now generate the complete, production-ready artifact:"
        )
        return sys_prompt, user_msg

    # ---- source line 7698 (Handler._synthesize_artifact) ----
    def _synthesize_artifact(self, item: dict, artifact: dict, vault_ctx: dict) -> str:
        """Backward-compat: returns content string only (no token tracking)."""
        sys_prompt, user_msg = self._build_artifact_prompt(item, artifact, vault_ctx)
        content, _ = _bl_call_llm_ex(sys_prompt, user_msg, max_tokens=3000)
        return content

    # ---- source line 7704 (Handler._handle_orch_version_create) ----
    def _handle_orch_version_create(self):
        """POST /vault/orchestrate/version/create — tạo milestone version mới với snapshot rỗng.
        Snapshot chỉ được populate sau khi có runs thực sự gắn version_id này.
        """
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        item_id     = body.get("item_id")
        description = (body.get("description") or "").strip()[:200]
        if not item_id:
            return self._send_json({"error": "missing item_id"}, 400)

        with _db() as c:
            # Next version number
            last = c.execute(
                "SELECT MAX(version_number) as mv FROM orch_versions WHERE backlog_item_id=?",
                (int(item_id),)
            ).fetchone()
            next_ver = (last["mv"] or 0) + 1
            label = f"v{next_ver}" + (f": {description}" if description else "")
            # Snapshot rỗng — sẽ được cập nhật khi có runs thuộc version này
            snapshot = []

            cur = c.execute(
                "INSERT INTO orch_versions(backlog_item_id,version_number,label,artifact_snapshot) VALUES(?,?,?,?)",
                (int(item_id), next_ver, label, json.dumps(snapshot, ensure_ascii=False))
            )
            ver_id = cur.lastrowid

        return self._send_json({
            "version_id": ver_id, "version_number": next_ver, "label": label,
            "docs": snapshot
        })

    # ---- source line 7738 (Handler._handle_orch_version_refresh_snapshot) ----
    def _handle_orch_version_refresh_snapshot(self):
        """POST /vault/orchestrate/version/refresh-snapshot
        Cập nhật snapshot của version với latest artifacts hiện có cho backlog item.
        """
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        version_id = body.get("version_id")
        item_id    = body.get("item_id")
        if not version_id or not item_id:
            return self._send_json({"error": "missing version_id or item_id"}, 400)
        with _db() as c:
            # Lấy latest artifact per key từ các runs thuộc version này (milestone)
            rows = c.execute("""
                SELECT a.id, a.artifact_key, a.name, a.icon, a.version,
                       length(a.content) as size
                FROM orch_artifacts a
                JOIN orch_runs r ON r.id = a.run_id
                WHERE r.version_id = ? AND a.content IS NOT NULL AND a.content != ''
                ORDER BY a.version DESC
            """, (int(version_id),)).fetchall()
            seen: set = set()
            snapshot = []
            for row in rows:
                k = row["artifact_key"]
                if k not in seen:
                    snapshot.append({
                        "artifact_key": k, "name": row["name"],
                        "icon": row["icon"], "db_id": row["id"],
                        "version": row["version"], "size": row["size"]
                    })
                    seen.add(k)
            c.execute(
                "UPDATE orch_versions SET artifact_snapshot=? WHERE id=?",
                (json.dumps(snapshot, ensure_ascii=False), int(version_id))
            )
        return self._send_json({"docs": snapshot, "docs_count": len(snapshot)})

    # ---- source line 7776 (Handler._handle_orch_version_list) ----
    def _handle_orch_version_list(self, path: str):
        """GET /vault/orchestrate/versions/{item_id} — list versions for an item."""
        user = self._require_auth()
        if not user: return
        parts = path.rstrip("/").split("/")
        item_id = parts[-1]
        if not item_id.isdigit():
            return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            rows = c.execute("""
                SELECT id, version_number, label, artifact_snapshot, created_at
                FROM orch_versions WHERE backlog_item_id=?
                ORDER BY version_number DESC
            """, (int(item_id),)).fetchall()
        result = []
        for r in rows:
            try:
                snap = json.loads(r["artifact_snapshot"] or "[]")
            except Exception:
                snap = []
            result.append({
                "id": r["id"], "version_number": r["version_number"],
                "label": r["label"], "docs": snap,
                "created_at": r["created_at"]
            })
        return self._send_json(result)

    # ---- source line 7803 (Handler._handle_orch_cross_review) ----
    def _handle_orch_cross_review(self):
        """POST /vault/orchestrate/cross-review — rà soát nhất quán giữa các artifact.
        Luôn dùng LATEST version của từng artifact_key cho cùng backlog item.
        Current-run artifacts (body.artifacts) override DB artifacts cho cùng key.
        """
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        version_id   = body.get("version_id")      # review theo version cụ thể
        item_title   = (body.get("item_title") or "").strip()

        if not version_id:
            return self._send_json({"error": "missing version_id — hãy tạo Version trước"}, 400)

        # Load artifact snapshot từ version đã chọn, rồi fetch content từ DB
        with _db() as c:
            ver_row = c.execute(
                "SELECT label, version_number, artifact_snapshot FROM orch_versions WHERE id=?",
                (int(version_id),)
            ).fetchone()
        if not ver_row:
            return self._send_json({"error": "Version không tồn tại"}, 404)

        try:
            snapshot = json.loads(ver_row["artifact_snapshot"] or "[]")
        except Exception:
            snapshot = []
        version_label = ver_row["label"] or f"v{ver_row['version_number']}"

        # Fetch content for each artifact in snapshot
        artifacts_for_review = []
        with _db() as c:
            for s in snapshot:
                row = c.execute(
                    "SELECT a.name, a.content, a.version FROM orch_artifacts a WHERE a.id=?",
                    (s["db_id"],)
                ).fetchone()
                if row and row["content"]:
                    artifacts_for_review.append({
                        "name": row["name"], "content": row["content"],
                        "version": row["version"]
                    })

        if len(artifacts_for_review) < 2:
            return self._send_json({
                "review": "ℹ️ Chưa đủ tài liệu để rà soát (cần ≥2). Hãy tạo thêm tài liệu khác cho backlog item này.",
                "docs_count": len(artifacts_for_review)
            })

        # Truncate each artifact to keep total context manageable
        PER_DOC_LIMIT = 4000
        docs_block = "\n\n".join(
            f"=== {a['name'].upper()} (v{a['version']}) ===\n{a['content'][:PER_DOC_LIMIT]}"
            + (" …[truncated]" if len(a['content']) > PER_DOC_LIMIT else "")
            for a in artifacts_for_review
        )
        if not docs_block:
            return self._send_json({"review": "⚠️ Không có nội dung tài liệu để rà soát."})

        sys_prompt = (
            "Bạn là senior technical reviewer. Nhiệm vụ: rà soát tính nhất quán giữa nhiều tài liệu phần mềm "
            "được sinh ra cho cùng một backlog item.\n\n"
            "Kiểm tra các loại vấn đề:\n"
            "1. **Thuật ngữ không nhất quán** — cùng khái niệm nhưng tên khác nhau giữa các tài liệu\n"
            "2. **Xung đột scope/yêu cầu** — một tài liệu bao gồm/loại trừ tính năng mà tài liệu khác không đề cập\n"
            "3. **Requirement gap** — yêu cầu trong BRD/URD nhưng Test Plan không có test case tương ứng\n"
            "4. **Mâu thuẫn logic** — một tài liệu nói X, tài liệu khác nói ngược lại\n"
            "5. **Missing traceability** — test case/design không liên kết được về requirement gốc\n\n"
            "Chỉ báo cáo vấn đề THỰC SỰ phát hiện được. Nếu không có vấn đề, ghi rõ '✅ Các tài liệu nhất quán.'\n"
            "Viết bằng tiếng Việt. Ngắn gọn, dùng emoji để phân loại."
        )
        user_msg = (
            f"## Backlog Item: {item_title}\n\n"
            f"## Các tài liệu cần rà soát\n\n{docs_block}\n\n"
            "## Yêu cầu\n"
            "Liệt kê từng vấn đề phát hiện được theo format:\n"
            "- **[Loại]** Mô tả vấn đề cụ thể (nêu rõ tài liệu A nói gì, tài liệu B nói gì)\n\n"
            "Phân loại dùng: ⚠️ Thuật ngữ | ❌ Xung đột | 🔗 Gap | 🔄 Mâu thuẫn | 📎 Traceability"
        )

        content, usage = _bl_call_llm_ex(sys_prompt, user_msg, max_tokens=1500)
        quota = self._charge_tokens(user, usage)
        doc_labels = [f"{a['name']} v{a['version']}" for a in artifacts_for_review]
        return self._send_json({
            "review": content, "version_label": version_label,
            "docs_count": len(artifacts_for_review),
            "doc_labels": doc_labels,
            "usage": usage, "quota": quota
        })

    # ---- source line 7893 (Handler._current_chat_provider) ----
    def _current_chat_provider(self) -> str:
        forced   = os.environ.get("VAULT_CHAT_PROVIDER", "").lower()
        qwen_key = os.environ.get("DASHSCOPE_API_KEY", "")
        anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
        gh_key   = os.environ.get("GITHUB_TOKEN", "")
        oai_key  = os.environ.get("OPENAI_API_KEY", "")

        if forced == "qwen" or (not forced and qwen_key and not anth_key and not gh_key):
            return "qwen"
        if forced == "anthropic" or (not forced and anth_key):
            return "anthropic"
        if forced == "github" or (not forced and gh_key and not oai_key):
            return "github"
        if forced == "openai" or (not forced and oai_key):
            return "openai"
        return "unknown"

    # ---- source line 7910 (Handler._coerce_review_checklist) ----
    def _coerce_review_checklist(self, raw_checklist, artifact_name: str) -> list[str]:
        import re as _re

        items: list[str] = []
        seen: set[str] = set()

        def _push(value):
            if value is None:
                return
            if isinstance(value, dict):
                for key, sub in value.items():
                    if isinstance(sub, list):
                        for entry in sub:
                            _push(f"{key}: {entry}")
                    elif sub:
                        _push(f"{key}: {sub}")
                return
            if isinstance(value, list):
                for entry in value:
                    _push(entry)
                return
            text = str(value).strip()
            if not text or text.lower() == "null":
                return
            if text.startswith("[") or text.startswith("{"):
                try:
                    _push(json.loads(text))
                    return
                except Exception:
                    pass
            for part in _re.split(r"\n+|\|", text):
                cleaned = _re.sub(r"^[\s\-\*•\d\.)\(\[\]xX]+", "", part).strip()
                if not cleaned:
                    continue
                key = cleaned.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append(cleaned)

        _push(raw_checklist)
        if items:
            return items

        fallback = self._get_artifact_checklist(artifact_name)
        m = _re.search(r"\]\s*(.*?)\s*\[/CHECKLIST\]", fallback, _re.S)
        if m:
            _push(m.group(1))
        return items

    # ---- source line 7960 (Handler._review_artifact_candidates) ----
    def _review_artifact_candidates(self, backlog_item_id: int, attachments: list) -> list[dict]:
        import re as _re
        import unicodedata as _uc

        stop_words = {
            "the", "and", "for", "with", "from", "this", "that", "draft",
            "tai", "lieu", "document", "file", "ban", "nhap", "thu", "mau",
            "yeu", "cau", "nghiep", "vu", "thiet", "ke", "kiem", "thu",
            "markdown", "docx", "pdf", "output", "files",
        }

        def _norm(text: str) -> str:
            text = _uc.normalize("NFD", text or "")
            text = "".join(ch for ch in text if _uc.category(ch) != "Mn")
            text = _re.sub(r"[^a-z0-9]+", " ", text.lower())
            return _re.sub(r"\s+", " ", text).strip()

        def _tokens(text: str) -> list[str]:
            return [tok for tok in _norm(text).split() if len(tok) >= 3 and tok not in stop_words]

        file_name_blob = " ".join(str(att.get("name") or "") for att in attachments[:3])
        content_blob = "\n".join(str(att.get("content") or "")[:2500] for att in attachments[:2])
        file_norm = _norm(file_name_blob)
        content_norm = _norm(content_blob[:8000])

        with _db() as c:
            existing_keys = {
                row["artifact_key"]
                for row in c.execute(
                    """SELECT DISTINCT oa.artifact_key
                       FROM orch_artifacts oa
                       JOIN orch_runs r ON r.id = oa.run_id
                       WHERE r.backlog_item_id = ?""",
                    (int(backlog_item_id),)
                ).fetchall()
            }

        candidates: list[dict] = []
        for channel in self._channels_get(with_artifacts=True):
            if not channel.get("enabled"):
                continue
            for art in channel.get("artifacts") or []:
                if not art.get("enabled"):
                    continue
                artifact_key = str(art.get("artifact_key") or "").strip()
                artifact_name = str(art.get("name") or artifact_key or "Artifact").strip()
                template = str(art.get("template") or "")
                description = str(art.get("description") or "")
                checklist_items = self._coerce_review_checklist(art.get("checklist_json"), artifact_name)
                keywords = set(_tokens(artifact_key) + _tokens(artifact_name) + _tokens(description))
                for line in template.splitlines()[:10]:
                    if line.strip().startswith(("#", "*", "-")):
                        keywords.update(_tokens(line))
                for item in checklist_items[:12]:
                    keywords.update(_tokens(item))

                score = 0
                hits: list[str] = []
                art_name_norm = _norm(artifact_name)
                art_key_norm = _norm(artifact_key)
                if art_name_norm and art_name_norm in file_norm:
                    score += 24
                    hits.append("artifact-name")
                if art_key_norm and art_key_norm in file_norm:
                    score += 18
                    hits.append("artifact-key")
                if artifact_key in existing_keys:
                    score += 8
                    hits.append("orchestrate-history")
                for kw in sorted(keywords, key=len, reverse=True)[:40]:
                    if kw in file_norm:
                        score += 6
                        hits.append(kw)
                    elif kw in content_norm:
                        score += 3
                        hits.append(kw)
                for heading in [ln.strip("#*- ") for ln in template.splitlines() if ln.strip().startswith("#")][:6]:
                    heading_norm = _norm(heading)
                    if heading_norm and heading_norm in content_norm:
                        score += 5
                        hits.append(heading)

                candidates.append({
                    "channel_key": channel.get("channel_key"),
                    "channel_label": channel.get("label") or channel.get("channel_key") or "Output",
                    "artifact_key": artifact_key,
                    "name": artifact_name,
                    "icon": art.get("icon") or "📄",
                    "output_type": art.get("output_type") or "Markdown (.md)",
                    "description": description,
                    "prompt": str(art.get("ai_prompt") or ""),
                    "template": template,
                    "sources_json": art.get("sources_json"),
                    "checklist_items": checklist_items,
                    "score": score,
                    "hits": hits[:8],
                    "has_history": artifact_key in existing_keys,
                })

        candidates.sort(key=lambda item: (-item["score"], -int(item["has_history"]), item["name"].lower()))
        return candidates

    # ---- source line 8062 (Handler._latest_elicit_synthesis_for_item) ----
    def _latest_elicit_synthesis_for_item(self, backlog_item_id: int):
        with _db() as c:
            row = c.execute(
                """SELECT oa.id, oa.version, oa.content, oa.created_at
                   FROM orch_artifacts oa
                   JOIN orch_runs r ON r.id = oa.run_id
                   WHERE r.backlog_item_id = ?
                     AND oa.artifact_key = 'elicit-synthesis'
                   ORDER BY oa.version DESC, oa.id DESC
                   LIMIT 1""",
                (int(backlog_item_id),)
            ).fetchone()
        return dict(row) if row else None

    # ---- source line 8076 (Handler._latest_artifact_for_item) ----
    def _latest_artifact_for_item(self, backlog_item_id: int, artifact_key: str):
        with _db() as c:
            row = c.execute(
                """SELECT oa.id, oa.version, oa.content, oa.created_at
                   FROM orch_artifacts oa
                   JOIN orch_runs r ON r.id = oa.run_id
                   WHERE r.backlog_item_id = ? AND oa.artifact_key = ?
                   ORDER BY oa.version DESC, oa.id DESC
                   LIMIT 1""",
                (int(backlog_item_id), str(artifact_key or ""))
            ).fetchone()
        return dict(row) if row else None

    # ---- source line 8089 (Handler._handle_orch_review_upload) ----
    def _handle_orch_review_upload(self):
        user = self._require_auth()
        if not user:
            return

        body = self._read_json_body()
        question = (body.get("question") or "").strip()
        attachments = [
            att for att in (body.get("attachments") or [])
            if isinstance(att, dict) and str(att.get("content") or "").strip()
        ]
        change_key = (body.get("changeKey") or "").strip()[:120]
        req_conv_id = body.get("conversationId") or None
        chat_mode = (body.get("mode") or "discover").strip()[:20]
        user_role = (body.get("role") or "BA").strip()[:20]
        item_id_raw = body.get("backlogItemId") or body.get("backlog_item_id") or 0
        try:
            backlog_item_id = int(item_id_raw or 0)
        except Exception:
            backlog_item_id = 0

        if not question:
            return self._send_json({"error": "missing question"}, 400)
        if not attachments:
            return self._send_json({"error": "missing reviewable attachment"}, 400)
        if backlog_item_id <= 0 and not change_key:
            return self._send_json({
                "error": "missing backlog context",
                "hint": "Chọn Change/backlog trước khi dùng chế độ review upload để map đúng Output artifact.",
            }, 400)

        with _db() as c:
            item_row = None
            if backlog_item_id > 0:
                item_row = c.execute(
                    """SELECT bi.id, bi.title, bi.description, bi.priority, bi.session_id,
                              bs.project, bs.workspace, ch.change_key
                       FROM backlog_items bi
                       JOIN backlog_sessions bs ON bs.id = bi.session_id
                       LEFT JOIN changes ch ON ch.backlog_item_id = bi.id
                       WHERE bi.id = ?
                       ORDER BY ch.id DESC
                       LIMIT 1""",
                    (backlog_item_id,)
                ).fetchone()
            elif change_key:
                item_row = c.execute(
                    """SELECT bi.id, bi.title, bi.description, bi.priority, bi.session_id,
                              bs.project, bs.workspace, ch.change_key
                       FROM changes ch
                       JOIN backlog_items bi ON bi.id = ch.backlog_item_id
                       JOIN backlog_sessions bs ON bs.id = bi.session_id
                       WHERE ch.change_key = ?
                       LIMIT 1""",
                    (change_key,)
                ).fetchone()

        if not item_row:
            return self._send_json({"error": "backlog item not found"}, 404)

        item = dict(item_row)
        backlog_item_id = int(item["id"])
        change_key = item.get("change_key") or change_key
        candidates = self._review_artifact_candidates(backlog_item_id, attachments)
        if not candidates:
            return self._send_json({"error": "no enabled orchestrate artifacts available for review"}, 404)

        matched = candidates[0]
        try:
            matched_sources = matched.get("sources_json")
            if isinstance(matched_sources, str):
                matched_sources = json.loads(matched_sources or "[]")
            if not isinstance(matched_sources, list):
                matched_sources = []
        except Exception:
            matched_sources = []

        vault_root = self._resolve_vault_root(item.get("workspace") or "", item.get("project") or "")
        vault_ctx = self._search_vault_sources(matched_sources, vault_root, item.get("title") or question)
        vault_text = "\n\n".join(
            f"=== VAULT: {src.upper()} ===\n{content}"
            for src, content in vault_ctx.items()
            if str(content or "").strip()
        )[:12000]
        vault_facts = self._extract_vault_facts(vault_ctx, item.get("title") or "")[:5000]
        latest_pv = self._latest_elicit_synthesis_for_item(backlog_item_id)
        latest_artifact = self._latest_artifact_for_item(backlog_item_id, matched.get("artifact_key") or "")

        attachment_block = "\n\n".join(
            f"=== UPLOAD: {att.get('name', 'file')} ===\n{str(att.get('content') or '')[:9000]}"
            + ("\n...[truncated]" if att.get("truncated") else "")
            for att in attachments[:2]
        )
        checklist_block = "\n".join(
            f"- {item_text}" for item_text in (matched.get("checklist_items") or [])[:20]
        ) or "- Không có checklist riêng trong cấu hình artifact. Hãy review theo template + best practice."
        candidate_block = "\n".join(
            f"- {cand['name']} [{cand['channel_label']}] score={cand['score']} hits={', '.join(cand['hits']) or 'n/a'}"
            for cand in candidates[:3]
        )
        pv_block = "[Chưa có PV synthesis cho backlog này]"
        if latest_pv:
            pv_block = (
                f"### PV SYNTHESIS v{latest_pv['version']} ({latest_pv['created_at']})\n"
                f"{latest_pv['content'][:5000]}"
                + ("\n...[truncated]" if len(latest_pv.get("content") or "") > 5000 else "")
            )
        history_block = "[Chưa có Output artifact cùng loại trong history]"
        if latest_artifact:
            history_block = (
                f"### LATEST OUTPUT {matched['name']} v{latest_artifact['version']} ({latest_artifact['created_at']})\n"
                f"{latest_artifact['content'][:4000]}"
                + ("\n...[truncated]" if len(latest_artifact.get("content") or "") > 4000 else "")
            )

        specialist = self._get_section_agent(matched.get("name") or "", matched.get("name") or "")
        system_prompt = (
            "Bạn là reviewer tài liệu chuyên sâu cho luồng AI Chat upload review trong Vault/Orchestrate.\n"
            "Nhiệm vụ: map file upload vào đúng Output artifact, sau đó review cực kỳ thực dụng dựa trên checklist,\n"
            "Vault knowledge, PV/interview synthesis, backlog context, và lịch sử output trước đó nếu có.\n\n"
            f"SPECIALIST LENS (reuse cùng kiểu agent khi generate artifact):\n{specialist}\n\n"
            "QUY TẮC REVIEW:\n"
            "1. Chỉ review theo artifact đã được map ở input. Không đổi artifact nếu không có bằng chứng rất mạnh.\n"
            "2. Nêu rõ chỗ nào PASS / MISSING / INCORRECT / AMBIGUOUS.\n"
            "3. Ưu tiên bằng chứng từ Vault và PV. Nếu thiếu bằng chứng, ghi rõ 'Thiếu nguồn xác nhận'.\n"
            "4. Nếu draft mâu thuẫn với Vault/PV, chỉ rõ mâu thuẫn và khuyến nghị sửa.\n"
            "5. Trả lời bằng tiếng Việt, Markdown gọn, không meta commentary.\n"
            "6. Kết thúc bằng verdict một dòng: ✅ Pass / ⚠️ Pass có điều kiện / ❌ Cần sửa lớn."
        )
        user_msg = (
            f"## Yêu cầu review từ người dùng\n{question}\n\n"
            f"## Backlog context\n"
            f"- Backlog item: {item['title']}\n"
            f"- Change: {change_key or '(chưa public change)'}\n"
            f"- Project: {item.get('project') or '-'}\n"
            f"- Workspace: {item.get('workspace') or '-'}\n"
            f"- Priority: {item.get('priority') or '-'}\n"
            f"- Description: {item.get('description') or '(không có mô tả)'}\n\n"
            f"## Artifact được map\n"
            f"- Channel: {matched['channel_label']}\n"
            f"- Artifact: {matched['name']}\n"
            f"- Artifact key: {matched['artifact_key']}\n"
            f"- Output type: {matched['output_type']}\n"
            f"- Mapping evidence: {', '.join(matched['hits']) or 'keyword heuristic'}\n\n"
            f"## Top candidate mapping\n{candidate_block}\n\n"
            f"## Checklist áp dụng\n{checklist_block}\n\n"
            f"## Artifact template\n{matched.get('template') or '[Không có template riêng]'}\n\n"
            f"## Vault key facts\n{vault_facts or '[Không trích xuất được fact cụ thể từ Vault]'}\n"
            f"## Vault context\n{vault_text or '[Không có Vault context]'}\n\n"
            f"## PV / interview synthesis\n{pv_block}\n\n"
            f"## Lịch sử output gần nhất cùng loại\n{history_block}\n\n"
            f"## Nội dung file upload cần review\n{attachment_block}\n\n"
            "## Output format bắt buộc\n"
            "# Review Summary\n"
            "## Mapping\n"
            "## Checklist Coverage\n"
            "Dùng bảng: | Checklist item | Status (PASS/MISSING/INCORRECT/AMBIGUOUS) | Evidence | Fix |\n"
            "## Findings\n"
            "Nhóm theo Critical / Major / Minor.\n"
            "## Questions / Missing Inputs\n"
            "## Verdict\n"
        )

        try:
            raw_review, raw_usage = _bl_call_llm_ex(system_prompt, user_msg, max_tokens=2200)
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

        usage = {
            "in": raw_usage.get("prompt", 0),
            "out": raw_usage.get("completion", 0),
            "total": raw_usage.get("total", 0),
        }
        quota = self._charge_tokens(user, usage)
        provider = self._current_chat_provider()
        model = raw_usage.get("model", "")

        header = (
            f"# Review — {matched['name']}\n\n"
            f"- Uploaded file: {attachments[0].get('name', 'file')}\n"
            f"- Backlog item: {item['title']}\n"
            f"- Change: {change_key or '(chưa public change)'}\n"
            f"- Output artifact: {matched['channel_label']} / {matched['name']}\n"
            f"- Review time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
        answer = header + raw_review.strip()

        review_artifact = {
            "id": f"review-{matched['artifact_key']}",
            "name": f"Review — {matched['name']}",
            "icon": "🧾",
            "out": "Markdown (.md)",
        }

        with _db() as c:
            run_id = c.execute(
                "INSERT INTO orch_runs(backlog_item_id,system) VALUES(?,?)",
                (backlog_item_id, "review-upload")
            ).lastrowid
            prev_count = c.execute(
                """SELECT COUNT(*)
                   FROM orch_artifacts oa
                   JOIN orch_runs r ON r.id = oa.run_id
                   WHERE r.backlog_item_id = ? AND oa.artifact_key = ?""",
                (backlog_item_id, review_artifact["id"])
            ).fetchone()[0]
            version = prev_count + 1
            db_id = c.execute(
                "INSERT INTO orch_artifacts(run_id,artifact_key,name,icon,output_type,content,version) VALUES(?,?,?,?,?,?,?)",
                (run_id, review_artifact["id"], review_artifact["name"], review_artifact["icon"],
                 review_artifact["out"], answer, version)
            ).lastrowid

        saved_sources = [
            f"upload:{attachments[0].get('name', 'file')}",
            f"artifact:{matched['name']}",
        ]
        if latest_pv:
            saved_sources.append(f"pv:elicit-synthesis-v{latest_pv['version']}")
        if latest_artifact:
            saved_sources.append(f"history:{matched['artifact_key']}-v{latest_artifact['version']}")

        saved_conv_id = None
        try:
            saved_conv_id = _get_or_create_conv(
                user["id"], req_conv_id,
                item.get("project") or (body.get("project") or ""),
                user_role, chat_mode or "discover", question,
                change_key=change_key,
            )
            _save_messages(saved_conv_id, question, answer, saved_sources, provider, model, usage)
        except Exception as e:
            print(f"[Vault] WARN review-upload conv save: {e}")

        return self._send_json({
            "answer": answer,
            "sources": saved_sources,
            "provider": provider,
            "model": model,
            "usage": usage,
            "quota_remaining": quota.get("token_remaining") if quota else None,
            "conversationId": saved_conv_id,
            "reviewArtifact": {
                "mappedArtifact": {
                    "artifact_key": matched["artifact_key"],
                    "name": matched["name"],
                    "channel": matched["channel_label"],
                    "score": matched["score"],
                    "hits": matched["hits"],
                },
                "savedArtifact": {
                    "id": db_id,
                    "artifact_key": review_artifact["id"],
                    "name": review_artifact["name"],
                    "version": version,
                    "run_id": run_id,
                },
                "candidates": [
                    {
                        "artifact_key": cand["artifact_key"],
                        "name": cand["name"],
                        "channel": cand["channel_label"],
                        "score": cand["score"],
                    }
                    for cand in candidates[:3]
                ],
            },
        })

    # ---- source line 8417 (Handler._handle_orch_provider_info) ----
    def _handle_orch_provider_info(self):
        """GET /vault/orchestrate/provider — return LLM provider info and rate limits."""
        user = self._require_auth()
        if not user: return
        forced   = os.environ.get("VAULT_CHAT_PROVIDER", "").lower()
        qwen_key = os.environ.get("DASHSCOPE_API_KEY", "")
        anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
        gh_key   = os.environ.get("GITHUB_TOKEN", "")
        oai_key  = os.environ.get("OPENAI_API_KEY", "")
        model_ov = os.environ.get("VAULT_CHAT_MODEL", "")

        if   forced == "qwen"      or (not forced and qwen_key and not anth_key and not gh_key):
            provider = "qwen"
        elif forced == "anthropic" or (not forced and anth_key):
            provider = "anthropic"
        elif forced == "github"    or (not forced and gh_key and not oai_key):
            provider = "github"
        elif forced == "openai"    or (not forced and oai_key):
            provider = "openai"
        else:
            provider = "unknown"

        # Rate limit characteristics per provider
        limits = {
            "qwen":      {"rpm": 60,  "rpd": 50000, "delay_ms": 1200, "full_doc_mode": False},
            "anthropic": {"rpm": 50,  "rpd": 50000, "delay_ms": 800,  "full_doc_mode": False},
            "openai":    {"rpm": 500, "rpd": 200000,"delay_ms": 300,  "full_doc_mode": False},
            "github":    {"rpm": 5,   "rpd": 150,   "delay_ms": 13000,"full_doc_mode": False},
            "unknown":   {"rpm": 10,  "rpd": 1000,  "delay_ms": 6000, "full_doc_mode": False},
        }
        info = limits.get(provider, limits["unknown"])
        model = model_ov or {"qwen":"qwen-plus","anthropic":"claude-sonnet-4-6",
                             "github":"gpt-4o-mini","openai":"gpt-4o-mini"}.get(provider,"unknown")
        return self._send_json({
            "provider": provider, "model": model,
            "rpm": info["rpm"], "rpd": info["rpd"],
            "section_delay_ms": info["delay_ms"],
            "full_doc_mode": info["full_doc_mode"],
        })

    # ---- source line 8457 (Handler._handle_orch_full) ----
    def _handle_orch_full(self):
        """POST /vault/orchestrate/full — generate an ENTIRE artifact in one LLM call.
        Used for low-RPM providers (GitHub Models) to minimise API calls."""
        try:
            user = self._require_auth()
            if not user: return
            body = self._read_json_body()
            item_id  = body.get("backlog_item_id")
            artifact = body.get("artifact", {})
            ws       = body.get("workspace", "")
            proj     = body.get("project", "")
            elicit_synthesis      = (body.get("elicit_synthesis") or "").strip()
            elicit_sessions_count = int(body.get("elicit_sessions_count") or 0)
            elicit_meta           = body.get("elicit_meta") or {}

            if not item_id or not artifact:
                return self._send_json({"error": "Missing params"}, 400)

            with _db() as c:
                row = c.execute("""SELECT bi.*, bs.workspace as bs_workspace, bs.project as bs_project
                    FROM backlog_items bi JOIN backlog_sessions bs ON bs.id=bi.session_id
                    WHERE bi.id=?""", (int(item_id),)).fetchone()
            if not row: return self._send_json({"error": "Not found"}, 404)
            item = dict(row)

            # Guard: same as _handle_orch_section — block if interviews exist but not synthesised
            if elicit_sessions_count > 0 and not elicit_synthesis:
                return self._send_json({
                    "error": "NEEDS_SYNTHESIS",
                    "message": (
                        f"Có {elicit_sessions_count} phỏng vấn chưa được tổng hợp.\n"
                        "Vui lòng tổng hợp phỏng vấn trước khi sinh tài liệu."
                    ),
                }, 409)

            eff_ws   = ws   or item.get("bs_workspace") or ""
            eff_proj = proj or item.get("bs_project")   or ""
            vault_root = self._resolve_vault_root(eff_ws, eff_proj)
            vault_ctx  = self._search_vault_sources(artifact.get("src", []), vault_root, item["title"])
            vault_facts_block = self._extract_vault_facts(vault_ctx, item["title"])

            has_elicit = bool(elicit_synthesis)
            sessions_desc = ""
            if elicit_meta.get("sessions"):
                sessions_desc = ", ".join(
                    f"{s.get('user','?')} ({s.get('role','?')}) {s.get('date','')[:10]}"
                    for s in elicit_meta["sessions"]
                )

            elicit_block = ""
            if has_elicit:
                elicit_block = (
                    f"## INTERVIEW SYNTHESIS (prioritize for business rules)\n"
                    f"Interviewees: {sessions_desc or 'see synthesis'}\n\n"
                    f"{elicit_synthesis[:6000]}\n\n"
                )

            sys_prompt = (
                f"You are a senior business analyst. Generate a COMPLETE, PRODUCTION-READY document "
                f"filling every section of the template below.\n"
                f"Use VAULT KEY FACTS as primary source. Mark each statement:\n"
                f"  <!-- [VAULT: confidence=HIGH|MEDIUM, src=filename] --> from vault\n"
                f"  <!-- [INTERVIEW: who/role/date] --> from interview\n"
                f"  <!-- [INFERRED: reason] --> from domain expertise\n"
                f"  <!-- [NEEDS_INFO: question] --> only for project-specific unknowns\n"
                f"Write in the same language as vault content (Vietnamese if vault is Vietnamese)."
            )

            vault_knowledge = vault_facts_block or ""
            user_msg = (
                f"## BACKLOG ITEM\nTitle: {item['title']}\n"
                f"Description: {item.get('description','') or '(none)'}\n\n"
                + elicit_block
                + (f"## VAULT KEY FACTS\n{vault_knowledge}\n\n" if vault_knowledge else "")
                + f"## DOCUMENT TEMPLATE (fill every section completely)\n"
                + (artifact.get("tpl","") or "## Content\n[Generate complete content]")
                + f"\n\nNow generate the complete filled document:"
            )

            # Full-doc generates the entire artifact in one call.
            # Note: GitHub provider caps output at 1000 tokens regardless of this value.
            content, usage = _bl_call_llm_ex(sys_prompt, user_msg, max_tokens=2000)
            content = self._post_annotate_vault(content, vault_ctx)
            quota = self._charge_tokens(user, usage)
            return self._send_json({"content": content, "artifact_name": artifact.get("name",""),
                                    "usage": usage, "quota": quota})
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                import urllib.error as _ue4
                if isinstance(e, _ue4.HTTPError) and e.code == 429:
                    return self._send_json({"error": "RATE_LIMIT", "retry_after": _parse_retry_after(e)}, 429)
            except Exception: pass
            return self._send_json({"error": str(e)}, 500)

    # ---- source line 8552 (Handler._handle_orch_section) ----
    def _handle_orch_section(self):
        """Generate one section of an artifact using a specialized agent persona."""
        try:
            user = self._require_auth()
            if not user: return
            body                  = self._read_json_body()
            item_id               = body.get("backlog_item_id")
            artifact              = body.get("artifact", {})
            section               = body.get("section", {})   # {title, template, index, total}
            prev_ctx              = body.get("previous", "")  # already-generated sections
            ws                    = body.get("workspace", "")
            proj                  = body.get("project", "")
            elicit_synthesis      = (body.get("elicit_synthesis") or "").strip()
            elicit_sessions_count = int(body.get("elicit_sessions_count") or 0)
            elicit_meta           = body.get("elicit_meta") or {}   # {sessions:[{user,role,date}]}

            if not item_id or not section:
                return self._send_json({"error": "Missing params"}, 400)

            # ── Guard: sessions exist but NOT yet synthesized → block ─────────
            if elicit_sessions_count > 0 and not elicit_synthesis:
                return self._send_json({
                    "error": "NEEDS_SYNTHESIS",
                    "message": (
                        f"Có {elicit_sessions_count} phỏng vấn cho change này chưa được tổng hợp.\n"
                        "Vui lòng tổng hợp phỏng vấn trước khi sinh tài liệu để đảm bảo output chính xác."
                    ),
                    "hint": "Mở popup 📋 trong Backlog → nhấn 'Tổng hợp tài liệu phỏng vấn' → quay lại Orchestrate."
                }, 409)

            with _db() as c:
                row = c.execute("""SELECT bi.*, bs.workspace as bs_workspace, bs.project as bs_project
                    FROM backlog_items bi
                    JOIN backlog_sessions bs ON bs.id = bi.session_id
                    WHERE bi.id=?""", (int(item_id),)).fetchone()
            if not row: return self._send_json({"error": "Not found"}, 404)
            item = dict(row)

            # Fallback: use workspace/project from the backlog session if not provided by client
            eff_ws   = ws   or item.get("bs_workspace") or ""
            eff_proj = proj or item.get("bs_project")   or ""

            vault_root = self._resolve_vault_root(eff_ws, eff_proj)
            vault_ctx  = self._search_vault_sources(artifact.get("src", []), vault_root, item["title"])
            vault_text = "\n\n".join(
                f"=== VAULT: {s.upper()} ===\n{c}" for s, c in vault_ctx.items()
            ) or "[No vault knowledge — apply domain expertise and best practices]"

            print(f"[ORCH] ws={eff_ws!r} proj={eff_proj!r} vault_root={vault_root} "
                  f"src={artifact.get('src',[])} vault_keys={list(vault_ctx.keys())} "
                  f"vault_bytes={sum(len(v) for v in vault_ctx.values())}")

            agent_persona = self._get_section_agent(section.get("title",""), artifact.get("name",""))
            idx   = int(section.get("index") or 0)  # 0 = missing → skip checklist injection
            total = section.get("total", 1)

            # ── Annotation mode ───────────────────────────────────────────────
            has_vault   = vault_text and "No vault knowledge" not in vault_text
            has_elicit  = bool(elicit_synthesis)
            mode_label  = ("Vault + Interview" if (has_vault and has_elicit)
                           else ("Interview only" if has_elicit
                           else ("Vault only" if has_vault
                           else "Domain expertise only")))

            sessions_desc = ""
            if elicit_meta.get("sessions"):
                sessions_desc = ", ".join(
                    f"{s.get('user','?')} ({s.get('role','?')}) {s.get('date','')[:10]}"
                    for s in elicit_meta["sessions"]
                )

            annotation_rules = (
                "ANNOTATION RULES — apply to EVERY bullet/sentence. Rendered as review badges.\n\n"
                "STEP 1 — First line: single-line checklist of 5-8 required elements for this section:\n"
                "<!-- [CHECKLIST: " + section.get('title','') + "] element1 | element2 | element3 | ... [/CHECKLIST] -->\n\n"
                "STEP 2 — Annotate EACH bullet/sentence with ONE comment on the SAME LINE using these rules:\n\n"
                "  USE <!-- [VAULT: confidence=HIGH|MEDIUM|LOW, src=<filename>] -->  WHEN:\n"
                "    → HIGH: vault explicitly states this fact or requirement\n"
                "    → MEDIUM: vault implies this, or you adapted it from vault content\n"
                "    → LOW: vault provides background/context that informed this point\n"
                "    IMPORTANT: If vault knowledge is in your context and you used it to write\n"
                "    this section (even to understand the domain), use VAULT LOW minimum.\n"
                "    Use the file name from '### folder/filename' headings in the vault section.\n\n"
                "  USE <!-- [INTERVIEW: " + (sessions_desc or "see synthesis") + "] -->  WHEN:\n"
                "    → this point comes from the interview synthesis above\n\n"
                "  USE <!-- [INFERRED: <brief reason>] -->  WHEN:\n"
                "    → you derive this from domain expertise, industry standards, or engineering best practices\n"
                "    → THIS IS THE DEFAULT when vault/interview don't cover the point — do NOT use NEEDS_INFO for this\n"
                "    → examples: standard REST conventions, well-known UX patterns, typical security requirements\n\n"
                "  USE <!-- [NEEDS_INFO: <specific question>] -->  ONLY WHEN:\n"
                "    → the information is PROJECT-SPECIFIC and ONLY the client/stakeholder can provide it\n"
                "    → examples: exact SLA numbers, budget, client-specific business rules, target user count,\n"
                "      integration credentials, brand guidelines, legal deadlines, naming conventions chosen by client\n"
                "    → DO NOT use for things you can reasonably infer from domain expertise\n\n"
                "Confidence: HIGH = vault explicitly states it | MEDIUM = vault implies it | LOW = weak connection.\n\n"
                "IMPORTANT: Most sentences should be VAULT or INFERRED. "
                "NEEDS_INFO should be rare — only for true project-specific unknowns.\n"
                "DO NOT skip annotations. Every bullet point must have exactly one annotation comment."
            )

            sys_prompt = (
                f"{agent_persona}\n\n"
                "RULES (non-negotiable):\n"
                "- Generate ONLY the content for the assigned section. Do NOT repeat other sections.\n"
                "- Output must be COMPLETE — no [TBD], no [AI điền...]. Fill every gap with domain expertise.\n"
                "- For gaps without vault/interview source: write solid best-practice content and mark INFERRED.\n"
                "- Reserve NEEDS_INFO only for project-specific facts only stakeholders can confirm.\n"
                "- Match the language of the template (Vietnamese or English).\n"
                "- For ANY diagram: output MERMAID syntax inside ```mermaid ... ``` fences.\n"
                "- Output ONLY the section content. No preamble.\n\n"
                f"{annotation_rules}"
            )

            # ── Build user message ────────────────────────────────────────────
            elicit_block = ""
            if has_elicit:
                elicit_block = (
                    f"## INTERVIEW SYNTHESIS (PRIMARY source — prioritize over Vault for business rules)\n"
                    f"Interviewees: {sessions_desc or 'see synthesis'}\n\n"
                    f"{elicit_synthesis[:8000]}\n\n"
                )
            elif elicit_sessions_count == 0:
                elicit_block = (
                    "## INTERVIEW CONTEXT\n"
                    "No interviews conducted yet. Base output on Vault knowledge and domain expertise.\n"
                    "Mark points derived from domain expertise as INFERRED. "
                    "Use NEEDS_INFO only for truly project-specific facts (exact numbers, client-specific rules, etc.).\n\n"
                )

            # Pre-extract key facts from vault for the LLM to cite.
            # Server-side _post_annotate_vault handles keyword matching,
            # so we DON'T send the full vault_text to the LLM — only the key facts.
            # This keeps the prompt small enough for Qwen-turbo (8K) and avoids 413.
            vault_facts_block = self._extract_vault_facts(vault_ctx, item["title"])
            has_facts = bool(vault_facts_block.strip())

            # Only include full vault_text when no key facts were extracted
            vault_knowledge_block = (
                vault_facts_block if has_facts
                else (f"## VAULT KNOWLEDGE\n{vault_text}\n\n" if vault_text and "No vault knowledge" not in vault_text else "")
            )

            user_msg = (
                f"## DOCUMENT CONTEXT\n"
                f"Document: {artifact.get('name','')}\n"
                f"Backlog item: {item['title']}\n"
                f"Description: {item.get('description','') or '(none)'}\n"
                f"Knowledge source mode: {mode_label}\n\n"
                + elicit_block
                + vault_knowledge_block
                + (f"## PREVIOUSLY GENERATED SECTIONS (do NOT repeat)\n{prev_ctx[:3000]}\n\n" if prev_ctx else "")
                + f"## YOUR ASSIGNMENT\n"
                f"Agent {idx}/{total}. Generate section: **{section.get('title','')}**\n"
                f"Template:\n{section.get('template','')}\n\n"
                f"Instruction: {artifact.get('prompt','Generate based on available knowledge.')}\n\n"
                f"OUTPUT FORMAT:\n"
                f"1. First line MUST be: <!-- [CHECKLIST: {section.get('title','')}] item1 | item2 | item3 | ... (5-8 required elements for this section) [/CHECKLIST] -->\n"
                f"2. Then write COMPREHENSIVE, DETAILED content filling EVERY required element above.\n"
                f"   Be exhaustive — use sub-sections, tables, numbered lists. Minimum 300 words.\n"
                f"3. Append ONE annotation after each sentence/bullet on the SAME line:\n"
                f"   · Vault fact → <!-- [VAULT: confidence=HIGH|MEDIUM|LOW, src=<filename>] -->\n"
                + (f"   · Interview → <!-- [INTERVIEW: {sessions_desc or 'see synthesis'}] -->\n" if has_elicit else "")
                + f"   · Best practice → <!-- [INFERRED: <reason>] -->\n"
                f"   · Must confirm → <!-- [NEEDS_INFO: <question>] -->\n"
                f"4. Language: match the vault/template (Vietnamese if vault is Vietnamese).\n"
                f"5. Use REQ-IDs from VAULT KEY FACTS when referencing requirements.\n"
                f"Generate now:"
            )
            raw_content, usage = _bl_call_llm_ex(sys_prompt, user_msg, max_tokens=3000)
            # Section 1: prepend server-generated checklist ONLY if template doesn't already embed one
            if idx == 1 and "[CHECKLIST:" not in section.get("template", ""):
                doc_cl = self._get_artifact_checklist(artifact.get("name", ""))
                if doc_cl:
                    raw_content = doc_cl + raw_content
            content = self._post_annotate_vault(raw_content, vault_ctx)
            quota = self._charge_tokens(user, usage)
            return self._send_json({"content": content, "section_title": section.get("title",""),
                                    "usage": usage, "quota": quota})
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                import urllib.error as _ue3
                if isinstance(e, _ue3.HTTPError) and e.code == 429:
                    return self._send_json({"error": "RATE_LIMIT", "retry_after": _parse_retry_after(e)}, 429)
            except Exception: pass
            return self._send_json({"error": str(e)}, 500)

    # ---- source line 8871 (Handler._get_artifact_checklist) ----
    def _get_artifact_checklist(self, artifact_name: str) -> str:
        """Server-side comprehensive document requirements checklist — injected into section 1.
        Always shown regardless of LLM behavior. Based on industry standards."""
        art = artifact_name.lower()

        _CL = {
            "requirements": [
                "I. Tóm Tắt Điều Hành",
                "II. Bối Cảnh & Vấn Đề Nghiệp Vụ",
                "III. Mục Tiêu Kinh Doanh",
                "IV. Phạm Vi",
                "V. Stakeholders",
                "VI. Yêu Cầu Nghiệp Vụ",
                "VII. Yêu Cầu Phi Chức Năng",
                "VIII. Quy Tắc Nghiệp Vụ",
                "IX. Ràng Buộc & Giả Định",
                "X. Rủi Ro Nghiệp Vụ",
                "XI. Chi Phí & ROI",
                "XII. Kịch Bản Người Dùng",
                "XIII. Tiêu Chí Nghiệm Thu",
                "XIV. Glossary & Change Log",
                "XV. Ma Trận Truy Xuất",
                "XVI. Phê Duyệt",
            ],
            "urd": [
                "I. Personas Người Dùng",
                "II. Use Cases & Luồng Sử Dụng",
                "III. User Stories",
                "IV. Kịch Bản Chi Tiết",
                "V. Yêu Cầu Giao Diện (UI/UX)",
                "VI. Yêu Cầu Accessibility",
                "VII. Yêu Cầu Hiệu Năng UX",
                "VIII. Tiêu Chí Nghiệm Thu",
                "IX. Ma Trận Truy Xuất",
                "X. Glossary",
            ],
            "design": [
                "I. Tổng Quan Kiến Trúc",
                "II. Quyết Định Kiến Trúc (ADR)",
                "III. Sơ Đồ Component",
                "IV. Sequence Diagram",
                "V. Thiết Kế Dữ Liệu",
                "VI. API Contracts",
                "VII. Bảo Mật & Phân Quyền",
                "VIII. Hạ Tầng & Triển Khai",
                "IX. Observability",
                "X. Migration & Backward Compatibility",
                "XI. Rủi Ro Kỹ Thuật",
                "XII. Non-functional Requirements",
            ],
            "test": [
                "I. Phạm Vi Kiểm Thử",
                "II. Chiến Lược Kiểm Thử",
                "III. Môi Trường Kiểm Thử",
                "IV. Dữ Liệu Kiểm Thử",
                "V. Test Cases Happy Path",
                "VI. Test Cases Boundary & Negative",
                "VII. Test Cases Security",
                "VIII. Test Cases Performance",
                "IX. Test Cases Integration",
                "X. Regression Checklist",
                "XI. Traceability Matrix",
                "XII. Entry & Exit Criteria",
                "XIII. Defect Severity Matrix",
            ],
            "meeting": [
                "Thông tin cuộc họp (ngày, giờ, địa điểm, hình thức)",
                "Danh sách tham dự & vắng mặt (có lý do)",
                "Chương trình họp (Agenda) với time-box",
                "Nội dung thảo luận từng mục — đầy đủ context",
                "Quyết định được đưa ra (kèm lý do & người quyết định)",
                "Action Items (What / Who / By-when / Priority)",
                "Vấn đề còn mở — Parking Lot",
                "Rủi ro & escalation items",
                "Tài liệu tham khảo & đính kèm",
                "Lịch họp tiếp theo",
            ],
            "slide": [
                "Problem Statement & Business Context",
                "Giải pháp đề xuất & differentiator",
                "Lợi ích định lượng (ROI, cost saving, time saving)",
                "Demo / Screenshots / Mockups",
                "Timeline & Milestones",
                "Nguồn lực & Investment cần thiết",
                "Rủi ro & kế hoạch giảm thiểu",
                "Competitive landscape (nếu có)",
                "Call to action / Next steps rõ ràng",
                "Q&A / Backup slides",
            ],
            "release": [
                "Release summary & phiên bản (version + date)",
                "Highlights — top 3 điểm nổi bật",
                "Tính năng mới — mô tả từng tính năng cho end-user",
                "Cải tiến & bug fixes (kèm ticket ID)",
                "Breaking changes & deprecations",
                "Hướng dẫn nâng cấp step-by-step",
                "Known issues & workarounds",
                "Rollback procedure",
                "Performance & security improvements",
                "Contact, support & documentation links",
            ],
        }

        items = None
        if any(k in art for k in ["urd", "user requirement", "yêu cầu người dùng"]):
            items = _CL.get("urd", _CL["requirements"])
        elif any(k in art for k in ["brd", "business requirement", "yêu cầu nghiệp vụ", "requirement", "srs"]):
            items = _CL["requirements"]
        elif any(k in art for k in ["kiến trúc", "architecture", "sad", "design", "thiết kế"]):
            items = _CL["design"]
        elif any(k in art for k in ["test plan", "test", "kiểm thử", "qa"]):
            items = _CL["test"]
        elif any(k in art for k in ["meeting", "minutes", "biên bản", "họp"]):
            items = _CL["meeting"]
        elif any(k in art for k in ["slide", "presentation", "pitch"]):
            items = _CL["slide"]
        elif any(k in art for k in ["release", "phát hành"]):
            items = _CL["release"]

        if not items:
            return ""
        return "<!-- [CHECKLIST: " + artifact_name + " — Tiêu chuẩn tài liệu] " + " | ".join(items) + " [/CHECKLIST] -->\n\n"

    # ---- source line 8994 (Handler._get_section_agent) ----
    def _get_section_agent(self, section_title: str, artifact_name: str) -> str:
        """Return specialized agent persona based on section title (primary) then artifact name.
        23 specialists + 1 default fallback = 24 agent personas total."""
        title = section_title.lower()
        art   = artifact_name.lower()

        # ── 1. Executive Summary / Overview → Product Manager ────────────────
        if any(k in title for k in ["overview", "tóm tắt điều hành", "executive", "summary",
                                     "mục tiêu", "objective", "tổng quan", "bối cảnh",
                                     "background", "problem statement", "vấn đề"]):
            return (
                "You are a Chief Product Officer with 15 years shipping B2B and B2C products. "
                "Write exclusively for C-level and board-level readers. "
                "Structure: (1) one-paragraph problem statement with quantified pain, "
                "(2) proposed solution and strategic fit, "
                "(3) expected outcomes with measurable KPIs (revenue, cost, NPS, time-to-value), "
                "(4) investment ask and payback period. "
                "Use plain business language — zero engineering jargon. "
                "Every claim must be backed by data or a named business driver."
            )

        # ── 2. Business Requirements / Functional Spec → Business Analyst ────
        if any(k in title for k in ["yêu cầu nghiệp vụ", "yêu cầu chức năng", "requirement",
                                     "functional", "nghiệp vụ", "business rule", "quy tắc",
                                     "ràng buộc", "phê duyệt", "approval"]) or \
           any(k in art for k in ["spec", "requirement", "brd", "backlog"]):
            return (
                "You are a Senior Business Analyst with 12 years of enterprise requirements engineering. "
                "Write requirements that are specific, measurable, achievable, relevant, time-bound (SMART). "
                "Use RFC-2119 keywords (MUST/SHOULD/MAY/SHALL). "
                "For every requirement provide: unique ID (REQ-xxx), description, rationale, "
                "acceptance criterion, priority (MoSCoW), source stakeholder, and dependency list. "
                "Separate functional requirements from business rules. "
                "Flag ambiguous requirements with [CLARIFICATION NEEDED]."
            )

        # ── 3. User Scenarios / Stories / Actors → Product Owner + UX ────────
        if any(k in title for k in ["scenario", "user stor", "actor", "stakeholder", "persona",
                                     "người dùng", "kịch bản", "scope", "phạm vi",
                                     "use case", "journey", "hành trình"]):
            return (
                "You are an Agile Product Owner and certified UX researcher (Nielsen-Norman trained). "
                "For each persona: name, role, goals, pain points, and technical literacy level. "
                "Write user stories as: 'As a <persona>, I want <specific action>, so that <measurable benefit>.' "
                "Include: acceptance criteria (Given/When/Then), story points estimate, "
                "edge cases, and out-of-scope boundaries. "
                "Map stories to the customer journey: Awareness → Onboarding → Core Use → Retention. "
                "Highlight stories with highest user value and lowest implementation risk first."
            )

        # ── 4. Test / QA / Acceptance → QA Lead ──────────────────────────────
        if any(k in title for k in ["test", "kiểm thử", "acceptance", "tiêu chí nghiệm thu",
                                     "nghiệm thu", "coverage", "qa", "quality",
                                     "scenario coverage", "coverage gap", "chất lượng"]):
            return (
                "You are a QA Lead, ISTQB-certified test architect, and automation engineer. "
                "For every feature write test cases covering: "
                "(1) happy path with exact input/output values, "
                "(2) boundary values and equivalence partitions, "
                "(3) negative cases (invalid input, missing fields, wrong types), "
                "(4) concurrency and race conditions, "
                "(5) security (injection, XSS, IDOR), "
                "(6) performance under load. "
                "Each test case: TC-ID, preconditions, numbered steps, expected result, "
                "actual result column (blank), pass/fail, automation priority (P0/P1/P2). "
                "End with a traceability matrix linking TC-IDs to REQ-IDs."
            )

        # ── 5. Architecture / Design / ADR → Solution Architect ──────────────
        if any(k in title for k in ["architect", "kiến trúc", "design", "thiết kế",
                                     "pattern", "applied pattern", "decision", "consequences",
                                     "context diagram", "diagram", "layer", "adr",
                                     "technical approach", "hướng tiếp cận"]):
            return (
                "You are a Principal Solution Architect with 15 years of enterprise distributed systems. "
                "Structure every design decision with: Context → Drivers → Options Considered → "
                "Decision → Consequences (pros/cons) → Open Questions. "
                "Reference named patterns explicitly (CQRS, Event Sourcing, Saga, Strangler Fig, "
                "Circuit Breaker, Outbox, BFF, SIDECAR). "
                "All diagrams MUST use Mermaid syntax in ```mermaid fences (C4, sequence, flowchart). "
                "Include: SLA targets, scalability ceiling, failure modes, and fallback strategy. "
                "State explicitly what the design does NOT support (non-goals)."
            )

        # ── 6. API / Integration / Contracts → API Architect ─────────────────
        if any(k in title for k in ["api", "endpoint", "integration", "tích hợp",
                                     "contract", "interface", "error handling",
                                     "webhook", "openapi", "rest", "graphql", "grpc"]):
            return (
                "You are an API Architect and OpenAPI 3.0 specialist with deep REST, GraphQL, gRPC expertise. "
                "For every endpoint document: HTTP method, full path with path params, "
                "query parameters (name/type/required/default), request body schema (JSON), "
                "response schemas for 200/201/400/401/403/404/409/429/500, "
                "rate limit headers, idempotency requirements, and auth scope. "
                "Include a complete cURL example for each endpoint. "
                "Define versioning strategy, deprecation policy, and backward-compatibility guarantees. "
                "For async APIs: document event schema, topic/queue name, retry policy, and DLQ."
            )

        # ── 7. Performance / SLA / Optimization → Performance Engineer ───────
        if any(k in title for k in ["performance", "hiệu năng", "sla", "slo", "latency",
                                     "throughput", "benchmark", "caching", "tải",
                                     "load", "tốc độ", "response time", "tối ưu",
                                     "optimization", "scalab", "capacity"]):
            return (
                "You are a Performance Engineering Lead with expertise in distributed systems optimization. "
                "Define concrete SLA targets: p50/p95/p99 latency (ms), throughput (req/s), "
                "error budget (%), and availability (nines). "
                "For each bottleneck: identify root cause (CPU/IO/network/DB), "
                "proposed fix (caching layer, DB indexing, async processing, CDN, connection pooling), "
                "expected improvement (%), and measurement method. "
                "Include: load test plan (tool, ramp-up, steady-state, spike scenario), "
                "cache strategy (TTL, eviction, invalidation), and auto-scaling policy. "
                "Define performance regression gate for CI pipeline."
            )

        # ── 8. Security / Auth / RBAC → Security Architect ───────────────────
        if any(k in title for k in ["security", "bảo mật", "auth", "permission",
                                     "rbac", "phân quyền", "xác thực", "mã hóa",
                                     "encryption", "vulnerability", "threat", "pentest"]):
            return (
                "You are a Security Architect, CISSP and OSCP certified, with AppSec and cloud security expertise. "
                "Apply STRIDE threat modelling (Spoofing/Tampering/Repudiation/Info disclosure/DoS/Elevation). "
                "For every threat: threat actor, attack vector (MITRE ATT&CK), impact (CIA triad), "
                "likelihood (1-5), severity (CVSS score), control (preventive/detective/corrective), "
                "implementation detail, and verification test. "
                "Cover: OWASP Top-10, authentication flows (OAuth2/OIDC/SAML), RBAC matrix "
                "(role × permission × resource), secrets management, encryption at rest/in transit, "
                "audit logging requirements, and incident response runbook."
            )

        # ── 9. Data / Schema / Database → Data Architect ─────────────────────
        if any(k in title for k in ["data", "schema", "database", "model", "entity",
                                     "erd", "cơ sở dữ liệu", "table", "bảng dữ liệu",
                                     "migration", "partition", "index", "query"]):
            return (
                "You are a Data Architect with expertise in OLTP, OLAP, and hybrid data platforms. "
                "For every entity: table name, all columns (name/type/nullable/default/constraints), "
                "primary key, foreign keys, unique constraints, and check constraints. "
                "Provide: ERD in Mermaid erDiagram syntax, indexing strategy "
                "(which columns, why, covering vs partial), partitioning strategy for large tables, "
                "migration script outline (up/down), and data retention/archival policy. "
                "Address: normalization level (3NF/BCNF), denormalization trade-offs for read performance, "
                "soft-delete pattern, audit columns (created_at/updated_at/created_by), "
                "and multi-tenancy isolation approach."
            )

        # ── 10. Risk / Mitigation → Risk Manager ─────────────────────────────
        if any(k in title for k in ["risk", "rủi ro", "mitigation", "assumption",
                                     "dependency", "issue log",
                                     "rủi ro kỹ thuật", "rủi ro nghiệp vụ"]):
            return (
                "You are a Risk Management specialist, PMI-RMP and ISO 31000 certified. "
                "For every risk: Risk-ID, category (technical/business/operational/external), "
                "description, root cause, probability (1-5), impact (1-5), exposure score (P×I), "
                "risk owner, trigger event, mitigation action (preventive), "
                "contingency plan (reactive), residual risk after mitigation, and review date. "
                "Present risks in a prioritized register table sorted by exposure score descending. "
                "Distinguish risks (uncertain events) from issues (known problems) and assumptions. "
                "Include a risk heat-map description and escalation threshold."
            )

        # ── 11. DevOps / Deployment / CI-CD → DevOps/SRE ─────────────────────
        if any(k in title for k in ["deploy", "triển khai", "release", "ci/cd", "pipeline",
                                     "devops", "rollout", "rollback", "blue/green", "canary",
                                     "build", "artifact", "environment", "infra as code"]):
            return (
                "You are a Senior DevOps Engineer and Site Reliability Engineer (SRE) with "
                "extensive experience in zero-downtime deployments and GitOps. "
                "Design the full CI/CD pipeline: trigger → build → unit test → SAST → "
                "container build → push → staging deploy → integration test → prod deploy → smoke test. "
                "For every environment (dev/staging/prod): resource specs, config management, "
                "secret injection method, and access controls. "
                "Define deployment strategy (blue/green, canary, rolling) with traffic split percentages, "
                "automated rollback conditions (error rate threshold, latency SLA breach), "
                "feature flag integration, and post-deploy runbook. "
                "Include: MTTR target, change failure rate baseline, and on-call escalation path."
            )

        # ── 12. Monitoring / Observability → Observability Engineer ──────────
        if any(k in title for k in ["monitor", "observab", "logging", "log", "tracing",
                                     "giám sát", "alert", "grafana", "prometheus",
                                     "apm", "telemetry", "health check", "dashboard ops",
                                     "elk", "splunk", "nhật ký"]):
            return (
                "You are an Observability Engineer specializing in the three pillars: "
                "metrics, logs, and distributed traces. "
                "Define: (1) Golden Signals dashboard (latency p50/p99, traffic req/s, "
                "error rate %, saturation CPU/mem), (2) structured log schema "
                "(fields: timestamp, level, trace_id, span_id, service, event, duration_ms, user_id), "
                "(3) distributed tracing instrumentation points and sampling rate, "
                "(4) alert rules with condition, threshold, severity (P1/P2/P3), "
                "notification channel, and runbook URL. "
                "Cover: SLO burn-rate alerts, anomaly detection, audit trail requirements "
                "(who/what/when/from-where for every write operation), and log retention policy."
            )

        # ── 13. Cloud / Infrastructure → Cloud Architect ─────────────────────
        if any(k in title for k in ["cloud", "infrastructure", "kubernetes", "k8s", "docker",
                                     "container", "aws", "azure", "gcp", "network", "vpc",
                                     "hạ tầng", "server", "scaling", "auto-scal", "ha",
                                     "load balanc", "cdn", "firewall"]):
            return (
                "You are a Cloud Solutions Architect (AWS/GCP/Azure certified) specializing in "
                "resilient, cost-optimized infrastructure. "
                "Document every infrastructure component: service name, tier (compute/storage/network), "
                "size/SKU, redundancy level (AZ/region), cost estimate ($/month), and justification. "
                "Design for: HA (multi-AZ, RTO/RPO targets), auto-scaling policy "
                "(min/max/target metric), network topology (VPC, subnets, security groups, peering), "
                "and disaster recovery plan. "
                "Include: IaC tool choice (Terraform/Pulumi/CDK), state management, "
                "cost optimization opportunities (reserved instances, spot, rightsizing), "
                "and security baseline (least privilege IAM, encryption, WAF, DDoS protection)."
            )

        # ── 14. Business Process / Workflow → Process Analyst ────────────────
        if any(k in title for k in ["process", "workflow", "quy trình", "luồng",
                                     "bpmn", "swimlane", "procedure", "flow",
                                     "luồng xử lý", "state machine", "trạng thái",
                                     "business flow", "nghiệp vụ luồng"]):
            return (
                "You are a Business Process Analyst and CBAP-certified with BPM/BPMN 2.0 expertise. "
                "For every process: name, trigger event, process owner, SLA target (end-to-end time), "
                "actors (human/system), and KPIs (throughput, error rate, cycle time). "
                "Document each step: step ID, description, actor, input, output, business rule applied, "
                "exception path, and escalation condition. "
                "Produce Mermaid flowchart or stateDiagram-v2 for state machines. "
                "Identify: manual handoffs (bottleneck risk), decision gates with criteria, "
                "parallel branches, and automation opportunities. "
                "Distinguish happy path (80% case) from exception paths with frequency estimates."
            )

        # ── 15. UI / UX / Wireframe → UX Designer ────────────────────────────
        if any(k in title for k in ["ui", "ux", "wireframe", "mockup", "giao diện",
                                     "màn hình", "screen", "layout", "navigation",
                                     "user flow", "usability", "prototype", "interaction",
                                     "component", "design system", "accessibility"]):
            return (
                "You are a Senior UX Designer and interaction design specialist "
                "(Nielsen-Norman UX certified, WCAG 2.1 AA compliant). "
                "For every screen: screen name, entry points, primary user goal, "
                "key interactions, and success metric. "
                "Describe layout in detail: header/nav structure, primary content zone, "
                "sidebar/panel, footer, and responsive breakpoints (mobile/tablet/desktop). "
                "For each interactive element: label, type (button/input/dropdown/toggle), "
                "state variants (default/hover/active/disabled/error/loading), "
                "and micro-interaction behavior. "
                "Cover: empty states, error states, loading skeletons, "
                "accessibility requirements (ARIA labels, keyboard navigation, color contrast ≥4.5:1), "
                "and usability heuristics violated/satisfied."
            )

        # ── 16. Tech Debt / Code Quality → Staff Engineer ────────────────────
        if any(k in title for k in ["debt", "tech-debt", "nợ kỹ thuật", "remediation",
                                     "refactor", "tái cấu trúc", "legacy", "related change",
                                     "improvement", "cải tiến", "code quality", "coupling",
                                     "complexity", "smell"]):
            return (
                "You are a Staff Engineer specializing in large-scale refactoring and tech debt reduction. "
                "For every debt item: TD-ID, category (architecture/code/test/dependency/security/infra), "
                "description, affected files/modules, discovery method (static analysis/incident/review), "
                "impact quantification (DORA metrics: deployment frequency, MTTR, change failure rate; "
                "plus developer cognitive load, onboarding time, incident rate). "
                "Prioritize by ROI = (impact × blast_radius) / effort (T-shirt: S/M/L/XL). "
                "Remediation plan: current state → target state, step-by-step approach, "
                "safe refactoring techniques (Strangler Fig, Branch by Abstraction), "
                "test coverage requirement before/after, estimated effort (dev-days), and owner. "
                "Include rollback strategy if refactoring causes regression."
            )

        # ── 17. Analytics / Reporting → Analytics Expert ─────────────────────
        if any(k in title for k in ["report", "báo cáo", "analytic", "dashboard",
                                     "kpi", "metric", "statistic", "filter", "export",
                                     "chart", "biểu đồ", "insight", "pivot", "drill",
                                     "thống kê", "số liệu"]):
            return (
                "You are a Data Analytics Lead and business intelligence architect. "
                "For every report/dashboard: name, audience, refresh frequency, "
                "and decision it enables. "
                "Define every metric: name, formula, data source, granularity (daily/weekly/monthly), "
                "target value, alert threshold, and owner. "
                "For each visualization: chart type, X-axis, Y-axis, grouping, color encoding, "
                "filter dimensions, and drill-down path. "
                "Cover: data lineage (source system → ETL/pipeline → warehouse → BI layer), "
                "access control (who can see what), export formats (CSV/Excel/PDF), "
                "scheduled email delivery, and data freshness SLA. "
                "Flag metrics that require MDM (master data management) alignment."
            )

        # ── 18. Notifications / Events / Messaging → Event Specialist ─────────
        if any(k in title for k in ["notification", "thông báo", "event", "sự kiện",
                                     "message", "queue", "kafka", "rabbitmq", "pub/sub",
                                     "async", "email", "sms", "push", "webhook",
                                     "topic", "subscription", "broadcast"]):
            return (
                "You are an Event-Driven Architecture specialist and messaging systems expert. "
                "For every notification type: trigger event, recipient (user/role/system), "
                "channel (in-app/email/SMS/push/webhook), template with all variables, "
                "timing (immediate/scheduled/batched), deduplication key, and opt-out mechanism. "
                "For async messaging: topic/queue name, schema (Avro/Protobuf/JSON), "
                "partition key, retention period, consumer group, and ordering guarantee. "
                "Define: retry policy (max attempts, backoff strategy, DLQ), "
                "idempotency handling, at-least-once vs exactly-once semantics, "
                "message tracing (correlation_id), and rate limiting per recipient."
            )

        # ── 19. Cost / Financial / ROI → Financial Analyst ───────────────────
        if any(k in title for k in ["cost", "chi phí", "budget", "roi", "tài chính",
                                     "pricing", "revenue", "doanh thu", "investment",
                                     "tco", "financial", "benefit", "lợi ích kinh tế",
                                     "payback", "npv", "irr"]):
            return (
                "You are a Technology Financial Analyst and business case specialist. "
                "Structure the financial analysis: (1) Total Cost of Ownership (TCO) breakdown "
                "(one-time: dev/infra/license; recurring: ops/support/SaaS), "
                "(2) benefit quantification (hard: cost reduction, revenue uplift, FTE saved; "
                "soft: risk reduction, customer satisfaction, compliance), "
                "(3) ROI calculation (NPV, IRR, payback period), "
                "(4) sensitivity analysis (best/base/worst case with key assumption changes). "
                "Include: cost per environment (dev/staging/prod), "
                "cost scaling model with user growth, "
                "make-vs-buy comparison if applicable, "
                "and budget phasing by quarter."
            )

        # ── 20. Compliance / Legal / Privacy → Compliance Analyst ────────────
        if any(k in title for k in ["compliance", "pháp lý", "legal", "gdpr", "pdpa",
                                     "regulation", "audit", "soc2", "iso", "privacy",
                                     "data protection", "bảo vệ dữ liệu", "consent",
                                     "certification", "tuân thủ"]):
            return (
                "You are a Compliance Architect and Data Protection Officer (CIPP/E certified). "
                "Map every data flow to applicable regulations (GDPR/PDPA/SOC2/ISO27001). "
                "For each compliance requirement: regulation article, obligation, "
                "current gap, remediation action, owner, and deadline. "
                "Cover: data inventory (what PII collected, purpose, legal basis, retention), "
                "consent management (capture, withdrawal, audit trail), "
                "data subject rights (access/rectification/erasure/portability — response SLA), "
                "breach notification procedure (72-hour rule), "
                "third-party processor agreements (DPA checklist), "
                "and annual audit evidence checklist."
            )

        # ── 21. Project Planning / Timeline → Project Manager ────────────────
        if any(k in title for k in ["timeline", "milestone", "roadmap", "kế hoạch",
                                     "schedule", "lịch trình", "sprint", "phase", "phase plan",
                                     "gantt", "deliverable", "action item", "meeting",
                                     "agenda", "biên bản", "minutes", "họp"]):
            return (
                "You are a Senior Project Manager and PMI-PMP/PSM-certified Scrum practitioner. "
                "For every phase/sprint: name, start/end date, goals, deliverables, "
                "team members, dependencies (predecessor tasks), and definition of done. "
                "For milestones: milestone name, success criteria, stakeholders to sign off, "
                "and risk if delayed. "
                "For meeting minutes: attendees, agenda items with owner and time-box, "
                "decisions made (with rationale), action items "
                "(what/who/by-when/priority), parking lot, and next meeting date. "
                "Highlight critical path, buffer zones, and resource conflicts. "
                "Include velocity baseline and capacity assumptions."
            )

        # ── 22. Product Strategy / Vision → Product Strategist ───────────────
        if any(k in title for k in ["vision", "strategy", "chiến lược", "okr",
                                     "market", "thị trường", "competitive", "competitor",
                                     "positioning", "value proposition", "giá trị",
                                     "go-to-market", "north star"]):
            return (
                "You are a VP of Product Strategy with experience in product-led growth and market expansion. "
                "Structure: (1) North Star metric and why it matters, "
                "(2) customer segmentation (ICP, TAM/SAM/SOM), "
                "(3) competitive landscape (at least 3 alternatives, feature comparison table), "
                "(4) differentiation and moat (network effect/data/switching cost/brand), "
                "(5) OKR framework (3 objectives × 3 key results each, measurable), "
                "(6) go-to-market motion (PLG/SLG/channel), "
                "(7) 12-month product roadmap with themes, not features. "
                "Every strategic choice must have a stated assumption and invalidation condition."
            )

        # ── 23. Change Management / Impact → Change Manager ──────────────────
        if any(k in title for k in ["change management", "impact", "ảnh hưởng", "adoption",
                                     "training", "đào tạo", "communication plan",
                                     "thay đổi tổ chức", "organizational", "readiness",
                                     "stakeholder management", "resistance", "transition"]):
            return (
                "You are an Organizational Change Management specialist (Prosci ADKAR certified). "
                "Apply the ADKAR model: Awareness → Desire → Knowledge → Ability → Reinforcement. "
                "For each stakeholder group: current state, desired state, resistance level (1-5), "
                "key concerns, engagement strategy, and communication frequency. "
                "Communication plan: message, channel, sender, audience, frequency, and feedback loop. "
                "Training plan: audience, format (workshop/e-learning/job aid), duration, "
                "trainer, success metric (assessment score ≥80%), and schedule. "
                "Define: go-live readiness checklist (≥90% completion required), "
                "hypercare support period, adoption KPIs at 30/60/90 days, "
                "and escalation path for low adoption."
            )

        # ── Default fallback — Senior Technical Writer ────────────────────────
        return (
            "You are a Senior Technical Writer and domain expert producing production-grade documentation. "
            "Structure content with clear hierarchy (H2 sections → H3 subsections → bullet details). "
            "Be exhaustively specific: no placeholders, no vague statements like 'as needed' or 'TBD'. "
            "Every claim needs either a concrete example, a measurable value, or a named reference. "
            "Apply the Minto Pyramid Principle: conclusion first, then supporting evidence. "
            "Use tables for comparisons, numbered lists for sequences, bullet lists for unordered sets. "
            "End the section with a 'Key Decisions / Open Questions' sub-section if applicable."
        )

    # ---- source line 9401 (Handler._charge_tokens) ----
    def _charge_tokens(self, user: dict, usage: dict) -> dict:
        """Deduct tokens from user quota. Returns {token_used, token_quota, token_remaining}."""
        total = usage.get("total", 0) if usage else 0
        if not total or not user:
            return {}
        with _db() as c:
            c.execute("UPDATE users SET token_used = token_used + ? WHERE id=?",
                      (total, user["id"]))
            row = c.execute("SELECT token_quota, token_used FROM users WHERE id=?",
                            (user["id"],)).fetchone()
        if row:
            return {"token_used": row["token_used"], "token_quota": row["token_quota"],
                    "token_remaining": max(0, row["token_quota"] - row["token_used"])}
        return {}

    # ---- source line 9416 (Handler._handle_orch_save_run) ----
    def _handle_orch_save_run(self):
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        item_id    = body.get("backlog_item_id")
        system     = body.get("system","")
        version_id = body.get("version_id") or None
        if not item_id: return self._send_json({"error":"Missing params"},400)
        with _db() as c:
            cur = c.execute(
                "INSERT INTO orch_runs(backlog_item_id,system,version_id) VALUES(?,?,?)",
                (int(item_id), system, int(version_id) if version_id else None))
            run_id = cur.lastrowid
        return self._send_json({"run_id": run_id})

    # ---- source line 9431 (Handler._handle_orch_save_artifact) ----
    def _handle_orch_save_artifact(self):
        user = self._require_auth()
        if not user: return
        body     = self._read_json_body()
        run_id   = body.get("run_id")
        artifact = body.get("artifact",{})
        content  = body.get("content","")
        item_id  = body.get("backlog_item_id")
        if not run_id or not artifact: return self._send_json({"error":"Missing params"},400)
        with _db() as c:
            prev = c.execute("""SELECT COUNT(*) FROM orch_artifacts oa
                JOIN orch_runs r ON r.id=oa.run_id
                WHERE r.backlog_item_id=? AND oa.artifact_key=?""",
                (int(item_id), artifact["id"])).fetchone()[0]
            version = prev + 1
            cur = c.execute(
                "INSERT INTO orch_artifacts(run_id,artifact_key,name,icon,output_type,content,version) VALUES(?,?,?,?,?,?,?)",
                (run_id, artifact["id"], artifact.get("name",""), artifact.get("icon","📄"),
                 artifact.get("out","Markdown (.md)"), content, version))
            art_db_id = cur.lastrowid
        return self._send_json({"db_id": art_db_id, "version": version})

    # ---- source line 9453 (Handler._handle_orch_history) ----
    def _handle_orch_history(self, path: str):
        user = self._require_auth()
        if not user: return
        parts = urlparse(path).path.rstrip("/").split("/")
        item_id = parts[-1]
        if not item_id.isdigit():
            return self._send_json({"error": "invalid id"}, 400)
        full = "full=1" in (urlparse(self.path).query or "")
        art_cols = "id, artifact_key, name, icon, output_type, version, content, length(content) as size, created_at" \
                   if full else \
                   "id, artifact_key, name, icon, output_type, version, length(content) as size, created_at"
        with _db() as c:
            runs = c.execute("""SELECT r.id, r.system, r.created_at, r.version_id,
                COALESCE(v.label,'') as version_label,
                COUNT(a.id) as artifact_count
                FROM orch_runs r
                LEFT JOIN orch_artifacts a ON a.run_id=r.id
                LEFT JOIN orch_versions v ON v.id=r.version_id
                WHERE r.backlog_item_id=?
                GROUP BY r.id ORDER BY r.created_at DESC""",
                (int(item_id),)).fetchall()
            result = []
            for r in runs:
                arts = c.execute(f"SELECT {art_cols} FROM orch_artifacts WHERE run_id=? ORDER BY id",
                    (r["id"],)).fetchall()
                result.append({**dict(r), "artifacts": [dict(a) for a in arts]})
        return self._send_json(result)

    # ---- source line 9481 (Handler._handle_orch_artifact_versions) ----
    def _handle_orch_artifact_versions(self, path: str):
        user = self._require_auth()
        if not user: return
        parts = urlparse(path).path.rstrip("/").split("/")
        artifact_key = parts[-1]
        item_id = self.headers.get("X-Item-Id","")
        if not item_id.isdigit():
            return self._send_json({"error": "X-Item-Id header required"}, 400)
        with _db() as c:
            rows = c.execute("""SELECT a.id, a.artifact_key, a.name, a.icon, a.output_type,
                a.content, a.version, a.created_at, r.system
                FROM orch_artifacts a JOIN orch_runs r ON r.id=a.run_id
                WHERE r.backlog_item_id=? AND a.artifact_key=?
                ORDER BY a.version DESC""",
                (int(item_id), artifact_key)).fetchall()
        return self._send_json([dict(r) for r in rows])

    # ---- source line 9498 (Handler._handle_orch_artifact_update) ----
    def _handle_orch_artifact_update(self, path: str):
        user = self._require_auth()
        if not user: return
        parts = urlparse(path).path.rstrip("/").split("/")
        art_id = parts[-1]
        if not art_id.isdigit():
            return self._send_json({"error": "invalid id"}, 400)
        body = self._read_json_body()
        content = body.get("content","")
        with _db() as c:
            c.execute("UPDATE orch_artifacts SET content=? WHERE id=?",
                      (content, int(art_id)))
        return self._send_json({"ok": True})

    # ---- source line 9512 (Handler._handle_orch_regen) ----
    def _handle_orch_regen(self):
        """Regenerate a single artifact for an existing run."""
        try:
            user = self._require_auth()
            if not user: return
            body = self._read_json_body()
            item_id  = body.get("backlog_item_id")
            ws       = body.get("workspace","")
            proj     = body.get("project","")
            artifact = body.get("artifact",{})
            if not item_id or not artifact:
                return self._send_json({"error":"Missing params"},400)
            with _db() as c:
                row = c.execute("""SELECT bi.*, bs.workspace as bs_workspace, bs.project as bs_project
                    FROM backlog_items bi JOIN backlog_sessions bs ON bs.id=bi.session_id
                    WHERE bi.id=?""", (int(item_id),)).fetchone()
            if not row: return self._send_json({"error":"Not found"},404)
            item = dict(row)
            eff_ws   = ws   or item.get("bs_workspace") or ""
            eff_proj = proj or item.get("bs_project")   or ""
            vault_root = self._resolve_vault_root(eff_ws, eff_proj)
            vault_ctx = self._search_vault_sources(artifact.get("src",[]), vault_root, item["title"])
            content   = self._synthesize_artifact(item, artifact, vault_ctx)
            with _db() as c:
                prev = c.execute("""SELECT COUNT(*) FROM orch_artifacts oa
                    JOIN orch_runs r ON r.id=oa.run_id
                    WHERE r.backlog_item_id=? AND oa.artifact_key=?""",
                    (int(item_id), artifact["id"])).fetchone()[0]
                version = prev + 1
                # Insert as new version in a new run
                cur = c.execute("INSERT INTO orch_runs(backlog_item_id,system) VALUES(?,?)",
                                (int(item_id), body.get("system","__output__")))
                run_id = cur.lastrowid
                cur2 = c.execute(
                    "INSERT INTO orch_artifacts(run_id,artifact_key,name,icon,output_type,content,version) VALUES(?,?,?,?,?,?,?)",
                    (run_id, artifact["id"], artifact["name"], artifact.get("icon","📄"),
                     artifact.get("out","Markdown (.md)"), content, version))
                art_db_id = cur2.lastrowid
            return self._send_json({"db_id": art_db_id, "content": content,
                                    "version": version, "status":"done"})
        except Exception as e:
            import traceback; traceback.print_exc()
            return self._send_json({"error": str(e)}, 500)

