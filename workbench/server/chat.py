"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import os
import re
from pathlib import Path

from .config import ROOT, VAULT_DB
from .conversations import _get_or_create_conv, _save_messages
from .db import _db, _get_user_by_token
from .discovery_helpers import (
    _discovery_openspec_root,
    _display_workspace_path,
    _find_discovery_projects,
)
from .graphrag_client import GraphragError, query_graphrag
from .shared import _sanitize_project_key


class ChatMixin:
    # ---- source line 5392 (Handler._serve_discovery_ask) ----
    def _serve_discovery_ask(self):
        """POST /discovery/ask  {project, question, history?}
        Search relevant artifacts then call an LLM to answer.
        Provider auto-detected from env vars:
          DASHSCOPE_API_KEY  -> Qwen  (default model: qwen-plus)
          ANTHROPIC_API_KEY  -> Claude (default model: claude-haiku-4-5-20251001)
          OPENAI_API_KEY     -> OpenAI-compatible (default model: gpt-4o-mini)
        Override with VAULT_CHAT_PROVIDER=qwen|anthropic|openai
                   and VAULT_CHAT_MODEL=<model-name>
        """
        import urllib.request as _ur
        import urllib.error  as _ue

        # ── Auth check (only when vault.db exists and has users) ─────────────
        _vault_user: dict | None = None
        if VAULT_DB.exists():
            _vault_user = _get_user_by_token(self._session_token())
            if _vault_user is None:
                return self._send_json({"error": "unauthorized", "hint": "Login required"}, 401)

        try:
            length     = int(self.headers.get("Content-Length", 0))
            raw        = self.rfile.read(length)
            body       = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            return self._send_json({"error": f"bad request: {e}"}, 400)

        # ── Quota check ───────────────────────────────────────────────────────
        if _vault_user and not _vault_user["is_admin"]:
            used  = _vault_user["token_used"]
            quota = _vault_user["token_quota"]
            if used >= quota:
                return self._send_json({
                    "error": "quota_exceeded",
                    "hint": f"Bạn đã dùng {used:,}/{quota:,} tokens. Gửi yêu cầu cấp thêm để tiếp tục.",
                    "quota_used": used, "quota_total": quota,
                }, 429)

        project       = (body.get("project") or "").strip()
        projects_list = body.get("projects") or []
        question      = (body.get("question") or "").strip()
        history       = body.get("history") or []
        review_ctx    = (body.get("reviewContext") or "").strip()[:2000]
        review_id     = (body.get("reviewItemId")  or "").strip()[:120]
        chat_mode     = (body.get("mode") or "discover").strip()   # discover | elicit | generate
        user_role     = (body.get("role") or "BA").strip()[:20]
        doc_type      = (body.get("docType") or "user_story").strip()[:40]
        attachments        = body.get("attachments") or []        # [{name, content, truncated}]
        image_attachments  = body.get("imageAttachments") or []   # [{name, dataUrl}]
        selected_change_body = body.get("selectedChange") or {}
        available_changes_body = body.get("availableChanges") or []
        _req_conv_id  = body.get("conversationId") or None        # P8 — persist chat turn
        _req_change_key = (body.get("changeKey") or "").strip()[:120]  # link elicit to a Change
        try:
            _req_backlog_item_id = int(body.get("backlogItemId") or body.get("backlog_item_id") or 0)
        except Exception:
            _req_backlog_item_id = 0
        _covered_dims = [d for d in (body.get("coveredDims") or []) if isinstance(d, str)]  # cross-session covered dims

        def _strip_chat_context_tags(text: str) -> str:
            cleaned = str(text or "")
            for _ in range(8):
                updated = re.sub(r"^\s*\[[^\]\n]{1,220}\]\s*", "", cleaned).strip()
                if updated == cleaned:
                    break
                cleaned = updated
            return cleaned.strip()

        def _upload_search_seed(text_files: list, image_files: list) -> str:
            heading_re = re.compile(
                r"(?i)^(#{1,6}\s+.+|"
                r"(title|overview|summary|executive summary|business objectives|stakeholders|"
                r"functional requirements|non[- ]functional requirements|acceptance criteria|"
                r"user story|constraints|assumptions|out of scope|open questions|test cases?|"
                r"scenarios?|api|data model|architecture|muc tieu|tong quan|"
                r"yeu cau|quy trinh|nghiep vu|pham vi|cau hoi mo)\b.*)$"
            )
            parts: list[str] = []
            for att in text_files[:3]:
                name = str(att.get("name") or "").strip()
                if name:
                    parts.append(Path(name).stem.replace("_", " ").replace("-", " "))
                content = str(att.get("content") or "")
                if not content:
                    continue
                picked: list[str] = []
                for raw_line in content.splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if heading_re.match(line):
                        picked.append(line.strip("#*- \t")[:140])
                    if len(picked) >= 8:
                        break
                if not picked:
                    fallback_lines = [ln.strip() for ln in content.splitlines() if ln.strip()][:4]
                    if fallback_lines:
                        picked.append(" ".join(fallback_lines)[:320])
                parts.extend(picked[:6])
            for img in image_files[:2]:
                name = str(img.get("name") or "").strip()
                if name:
                    parts.append(Path(name).stem.replace("_", " ").replace("-", " "))
            return " ".join(parts)[:1600]

        def _has_explicit_upload_intent(text: str) -> bool:
            plain = _strip_chat_context_tags(text).lower().strip()
            if not plain:
                return False
            if plain in {
                "file này", "file nay", "đây", "day", "này", "nay",
                "xem file này", "xem file nay", "gửi file này", "gui file nay",
            }:
                return False
            explicit_patterns = [
                r"\breview\b", r"rà soát", r"thẩm định", r"đánh giá", r"kiểm tra",
                r"\btóm tắt\b", r"\bsummary\b", r"\bcompare\b", r"so sánh", r"đối chiếu",
                r"\bextract\b", r"trích", r"rút ra", r"liệt kê", r"xác định",
                r"\bfind\b", r"\btìm\b", r"tra cứu", r"\bsearch\b",
                r"\bmap\b", r"\bgắn\b", r"\bconvert\b", r"chuyển thành",
                r"\bgenerate\b", r"\btạo\b", r"\bviết\b", r"\blập\b", r"\bdraft\b",
                r"\btranslate\b", r"\bdịch\b", r"giải thích", r"phân tích",
                r"\blưu\b", r"\bsave\b", r"\bxuất\b", r"tạo backlog", r"tạo ticket",
            ]
            if any(re.search(pattern, plain, re.I) for pattern in explicit_patterns):
                return True

            # Natural phrasing like "file này nói gì?" or "trong file có requirement nào?"
            # should execute directly instead of falling back to upload intent clarification.
            file_reference_patterns = [
                r"\bfile\b", r"tài liệu", r"tai lieu", r"document", r"attachment",
                r"upload", r"pdf", r"docx?", r"pptx?", r"html?",
            ]
            file_content_question_patterns = [
                r"\bđọc\b", r"\bdoc\b", r"nội dung", r"noi dung", r"ý chính", r"y chinh",
                r"main points?", r"key points?", r"nói gì", r"noi gi", r"có gì", r"co gi",
                r"gồm (những )?gì", r"gom (nhung )?gi", r"đề cập", r"de cap",
                r"bao gồm", r"bao gom", r"what does", r"what is in", r"what's in",
                r"requirement", r"yêu cầu", r"yeu cau", r"user stor(y|ies)",
                r"acceptance criteria", r"open questions?", r"rủi ro", r"rui ro",
                r"tóm lược", r"tom luoc", r"trả lời", r"tra loi", r"cho tôi biết", r"cho toi biet",
            ]
            mentions_file = any(re.search(pattern, plain, re.I) for pattern in file_reference_patterns)
            asks_about_content = any(re.search(pattern, plain, re.I) for pattern in file_content_question_patterns)
            if mentions_file and asks_about_content:
                return True

            # When a file is attached, direct content questions without repeating "file"
            # should still count as explicit intent.
            direct_content_question_patterns = [
                r"^nội dung.*là gì", r"^noi dung.*la gi", r"^ý chính.*là gì", r"^y chinh.*la gi",
                r"requirement nào", r"yeu cau nao", r"các yêu cầu", r"cac yeu cau",
                r"tóm tắt", r"tom tat", r"phân tích", r"phan tich", r"giải thích", r"giai thich",
            ]
            return any(re.search(pattern, plain, re.I) for pattern in direct_content_question_patterns)

        def _describe_upload(text_files: list, image_files: list) -> tuple[str, list[str]]:
            if image_files and not text_files:
                names = [str(img.get("name") or "image") for img in image_files[:2]]
                return ("ảnh/screenshot", names)

            sample = "\n".join(str(att.get("content") or "")[:2500] for att in text_files[:2])
            sample_lower = sample.lower()
            clue_lines: list[str] = []
            for raw_line in sample.splitlines():
                line = raw_line.strip().strip("#*- \t")
                if not line:
                    continue
                if raw_line.strip().startswith("#") or raw_line.strip().endswith(":"):
                    clue_lines.append(line[:90])
                elif re.match(r"(?i)^(executive summary|business objectives|stakeholders|functional requirements|non-functional requirements|acceptance criteria|user story|test plan|test cases?|api|data model|open questions)\b", line):
                    clue_lines.append(line[:90])
                if len(clue_lines) >= 4:
                    break
            if text_files:
                first_name = str(text_files[0].get("name") or "").strip()
                if first_name:
                    clue_lines.insert(0, first_name)

            if "executive summary" in sample_lower and "business objectives" in sample_lower:
                kind = "Business Requirements Document (BRD)"
            elif "user story" in sample_lower or "acceptance criteria" in sample_lower:
                kind = "tài liệu yêu cầu / user story"
            elif "test plan" in sample_lower or "test scenario" in sample_lower or "test case" in sample_lower:
                kind = "Test Plan / tài liệu kiểm thử"
            elif "architecture" in sample_lower or "data model" in sample_lower or "component design" in sample_lower:
                kind = "tài liệu thiết kế kỹ thuật"
            elif "functional requirements" in sample_lower or "stakeholders" in sample_lower:
                kind = "tài liệu yêu cầu nghiệp vụ"
            else:
                kind = "tài liệu nghiệp vụ/kỹ thuật"
            return (kind, clue_lines[:4])

        def _sanitize_change_entry(raw) -> dict | None:
            if not isinstance(raw, dict):
                return None
            key = str(raw.get("change_key") or raw.get("key") or "").strip()[:120]
            title = str(raw.get("title") or "").strip()[:240]
            if not key or not title:
                return None
            norm_project, norm_workspace = self._normalize_scope_pair(
                str(raw.get("project") or raw.get("session_project") or "").strip(),
                str(raw.get("workspace") or raw.get("session_workspace") or "").strip(),
            )
            try:
                backlog_item_id = int(raw.get("backlog_item_id") or raw.get("backlogItemId") or 0)
            except Exception:
                backlog_item_id = 0
            return {
                "change_key": key,
                "title": title,
                "status": str(raw.get("status") or "open").strip()[:40],
                "description": str(raw.get("description") or "").strip()[:500],
                "priority": str(raw.get("priority") or "").strip()[:40],
                "backlog_item_id": backlog_item_id,
                "project": norm_project,
                "workspace": norm_workspace,
            }

        def _sanitize_change_candidates(raw_list) -> list[dict]:
            if not isinstance(raw_list, list):
                return []
            result: list[dict] = []
            seen: set[str] = set()
            for raw in raw_list[:12]:
                item = _sanitize_change_entry(raw)
                if not item or item["change_key"] in seen:
                    continue
                seen.add(item["change_key"])
                result.append(item)
            return result

        def _extract_change_key(text: str) -> str:
            plain = _strip_chat_context_tags(text).upper().strip()
            if not plain:
                return ""
            match = re.search(r"\bCHG-\d{3}-\d{2}\b", plain, re.I)
            return match.group(0).upper() if match else ""

        def _change_work_intent(text: str) -> bool:
            plain = _strip_chat_context_tags(text).lower().strip()
            if not plain:
                return False
            if _extract_change_key(plain):
                return True
            patterns = [
                r"\bchange\b", r"\bbacklog\b", r"\bticket\b", r"yêu cầu mới", r"yêu cầu này",
                r"tính năng mới", r"feature mới", r"chức năng mới", r"thêm tính năng",
                r"thêm chức năng", r"bổ sung", r"bổ xung", r"\bsửa\b", r"chỉnh sửa",
                r"cập nhật", r"thay đổi", r"\bđổi\b", r"điều chỉnh", r"nâng cấp", r"refactor",
                r"xây dựng", r"phát triển", r"implement", r"build", r"create", r"đề xuất",
                r"làm change", r"mở change", r"public change", r"change này",
            ]
            return any(re.search(pat, plain, re.I) for pat in patterns)

        def _format_change_candidate_lines(items: list[dict]) -> str:
            return "\n".join(
                f"- **{item['change_key']}** — {item['title']} ({item['status'] or 'open'})"
                for item in items[:8]
            )

        def _default_change_follow_up(change_ctx: dict | None) -> str:
            change_key = str((change_ctx or {}).get("change_key") or "change này").strip() or "change này"
            change_desc = str((change_ctx or {}).get("description") or "").strip()
            if change_desc:
                return (
                    f"Ở {change_key}, phần nào cần tôi đào sâu tiếp để giúp triển khai đúng nhất: "
                    "luồng xử lý, business rule, hay phạm vi ảnh hưởng?"
                )
            return (
                f"Với {change_key}, bạn muốn tôi làm rõ trước phần nào: "
                "mục tiêu nghiệp vụ, impacted modules, hay các rule xử lý chính?"
            )

        def _scope_seed() -> tuple[str, str, str]:
            scope = ""
            if project:
                scope = str(project).strip()
            elif isinstance(projects_list, list) and len(projects_list) == 1:
                scope = str(projects_list[0]).strip()
            ws_key = scope.split("/", 1)[0] if "/" in scope else scope
            proj_key = scope.rsplit("/", 1)[-1] if scope else ""
            return scope, ws_key, proj_key

        def _list_relevant_changes(limit: int = 8) -> list[dict]:
            scope, ws_key, proj_key = _scope_seed()
            with _db() as c:
                rows = c.execute(
                    """
                    SELECT ch.change_key, ch.title, ch.status, ch.priority, ch.backlog_item_id,
                           COALESCE(bi.description, ch.description, '') AS description,
                           COALESCE(bs.workspace, '') AS workspace,
                           COALESCE(bs.project, '')   AS project
                    FROM changes ch
                    LEFT JOIN backlog_items bi ON bi.id = ch.backlog_item_id
                    LEFT JOIN backlog_sessions bs ON bs.id = ch.session_id
                    WHERE (? = ''
                           OR COALESCE(bs.workspace, '') = ?
                           OR COALESCE(bs.project, '') = ?
                           OR (COALESCE(bs.workspace, '') || '/' || COALESCE(bs.project, '')) = ?)
                    ORDER BY ch.priority DESC, ch.created_at DESC
                    LIMIT ?
                    """,
                    (scope, ws_key, proj_key, scope, int(limit)),
                ).fetchall()
            return [
                {
                    "change_key": str(r["change_key"] or ""),
                    "title": str(r["title"] or ""),
                    "status": str(r["status"] or "open"),
                    "description": str(r["description"] or ""),
                    "priority": str(r["priority"] or ""),
                    "backlog_item_id": int(r["backlog_item_id"] or 0),
                }
                for r in rows if str(r["change_key"] or "").strip() and str(r["title"] or "").strip()
            ]

        def _load_selected_change_context(change_key: str, backlog_item_id: int) -> dict | None:
            if not change_key and backlog_item_id <= 0:
                return None
            with _db() as c:
                if change_key:
                    row = c.execute(
                        """
                        SELECT ch.change_key, ch.title, ch.status, ch.priority, ch.backlog_item_id,
                               COALESCE(bi.description, ch.description, '') AS description,
                               COALESCE(bs.workspace, '') AS workspace,
                               COALESCE(bs.project, '')   AS project
                        FROM changes ch
                        LEFT JOIN backlog_items bi ON bi.id = ch.backlog_item_id
                        LEFT JOIN backlog_sessions bs ON bs.id = ch.session_id
                        WHERE ch.change_key = ?
                        ORDER BY ch.id DESC
                        LIMIT 1
                        """,
                        (change_key,),
                    ).fetchone()
                else:
                    row = c.execute(
                        """
                        SELECT ch.change_key, ch.title, ch.status, ch.priority, ch.backlog_item_id,
                               COALESCE(bi.description, ch.description, '') AS description,
                               COALESCE(bs.workspace, '') AS workspace,
                               COALESCE(bs.project, '')   AS project
                        FROM changes ch
                        LEFT JOIN backlog_items bi ON bi.id = ch.backlog_item_id
                        LEFT JOIN backlog_sessions bs ON bs.id = ch.session_id
                        WHERE ch.backlog_item_id = ?
                        ORDER BY ch.id DESC
                        LIMIT 1
                        """,
                        (int(backlog_item_id),),
                    ).fetchone()
            return _sanitize_change_entry(dict(row)) if row else None

        def _load_change_private_context(backlog_item_id: int, attachments_for_turn: list[dict]) -> dict | None:
            if backlog_item_id <= 0:
                return None
            latest_synthesis = self._latest_elicit_synthesis_for_item(backlog_item_id)
            artifact_candidates = self._review_artifact_candidates(backlog_item_id, attachments_for_turn or [])
            preferred_candidates = [
                cand for cand in artifact_candidates
                if cand.get("has_history") or int(cand.get("score") or 0) > 0
            ][:3]
            if not preferred_candidates:
                preferred_candidates = artifact_candidates[:2]

            output_history: list[dict] = []
            for cand in preferred_candidates[:3]:
                artifact_key = str(cand.get("artifact_key") or "").strip()
                if not artifact_key:
                    continue
                latest_artifact = self._latest_artifact_for_item(backlog_item_id, artifact_key)
                if not latest_artifact or not str(latest_artifact.get("content") or "").strip():
                    continue
                output_history.append({
                    "artifact_key": artifact_key,
                    "name": str(cand.get("name") or artifact_key or "Artifact").strip(),
                    "version": int(latest_artifact.get("version") or 0),
                    "created_at": str(latest_artifact.get("created_at") or "").strip(),
                    "content": str(latest_artifact.get("content") or "")[:1800],
                })
            return {
                "latest_synthesis": latest_synthesis,
                "artifact_candidates": preferred_candidates,
                "output_history": output_history,
            }

        has_uploads = bool(attachments or image_attachments)
        plain_question = _strip_chat_context_tags(question)
        _question_change_key = ""
        if not _req_change_key:
            _question_change_key = _extract_change_key(plain_question or question)
            if _question_change_key:
                _req_change_key = _question_change_key
        upload_search_seed = _upload_search_seed(attachments, image_attachments) if has_uploads else ""
        search_query = " ".join(part for part in [plain_question, upload_search_seed] if part).strip()
        upload_intent_explicit = _has_explicit_upload_intent(question) if has_uploads else True
        available_change_candidates = _sanitize_change_candidates(available_changes_body) or _list_relevant_changes(8)
        selected_change_ctx = _load_selected_change_context(_req_change_key, _req_backlog_item_id)
        selected_change_private_ctx: dict | None = None
        if not selected_change_ctx:
            selected_change_ctx = _sanitize_change_entry(selected_change_body)
        change_guidance_candidates: list[dict] = []
        change_selection_hint = bool(
            not selected_change_ctx
            and available_change_candidates
            and (has_uploads or _change_work_intent(plain_question or question))
        )
        if selected_change_ctx:
            _req_change_key = selected_change_ctx.get("change_key") or _req_change_key
            if not _req_backlog_item_id:
                try:
                    _req_backlog_item_id = int(selected_change_ctx.get("backlog_item_id") or 0)
                except Exception:
                    _req_backlog_item_id = 0
            selected_workspace = str(selected_change_ctx.get("workspace") or "").strip()
            if selected_workspace:
                project = selected_workspace
                projects_list = []
            selected_change_private_ctx = _load_change_private_context(_req_backlog_item_id, attachments)
        elif not has_uploads and available_change_candidates and _change_work_intent(plain_question or question):
            change_guidance_candidates = available_change_candidates[:8]

        if not question and not has_uploads:
            return self._send_json({"error": "missing question"}, 400)

        change_code_only_question = re.sub(r"\bCHG-\d{3}-\d{2}\b", "", plain_question, flags=re.I).strip(" \t:-,.;")
        if not has_uploads and _question_change_key and selected_change_ctx and not change_code_only_question:
            change_title = selected_change_ctx.get("title") or _question_change_key
            change_desc = (selected_change_ctx.get("description") or "").strip()
            answer = (
                f"Đã chuyển sang **{_question_change_key} — {change_title}**.\n\n"
                + (f"Mô tả hiện có: {change_desc}\n\n" if change_desc else "")
                + "Tôi sẽ bám theo change này trong các câu tiếp theo. Bạn muốn tôi giúp gì trước: làm rõ mục tiêu, review yêu cầu hiện có, xác định impacted modules, hay draft spec?"
            )
            usage = {"in": 0, "out": 0, "total": 0}
            quota_remaining: int | None = None
            if _vault_user:
                with _db() as c:
                    row = c.execute(
                        "SELECT token_quota, token_used FROM users WHERE id=?",
                        (_vault_user["id"],)
                    ).fetchone()
                if row:
                    quota_remaining = max(0, row["token_quota"] - row["token_used"])

            _saved_conv_id: int | None = None
            if _vault_user and answer:
                try:
                    _saved_conv_id = _get_or_create_conv(
                        _vault_user["id"], _req_conv_id,
                        project, user_role, "discover",
                        question,
                        change_key=_req_change_key,
                    )
                    _save_messages(
                        _saved_conv_id,
                        question,
                        answer,
                        [f"change:{_req_change_key}"],
                        "system",
                        "change-boundary",
                        usage,
                    )
                except Exception as _e_conv:
                    print(f"[Vault] WARN conv save: {_e_conv}")

            resp: dict = {
                "answer": answer,
                "sources": [f"change:{_req_change_key}"],
                "filesScanned": 0,
                "provider": "system",
                "model": "change-boundary",
                "usage": usage,
                "selected_change": selected_change_ctx,
            }
            if quota_remaining is not None:
                resp["quota_remaining"] = quota_remaining
            if _saved_conv_id is not None:
                resp["conversationId"] = _saved_conv_id
            return self._send_json(resp)

        # ── Unified knowledge index (graphrag-engine LanceDB) ──────────────────
        # Discover-mode, no uploads: retrieval goes through the ONE shared vector index
        # (graphrag-engine, see graphrag_client.py) instead of keyword-grep over ARTIFACTS_ROOT
        # below. elicit/generate modes and any turn with uploads are untouched — they still fall
        # through to the keyword-grep path further down. No silent fallback: a graphrag-engine
        # failure surfaces as a 502, never a quiet revert to keyword-grep (would hide real
        # outages behind seemingly-fine-but-wrong answers during a live demo).
        if chat_mode == "discover" and not has_uploads:
            try:
                _gr_result = query_graphrag(question)
            except GraphragError as e:
                return self._send_json({"error": str(e)}, 502)

            answer = _gr_result.get("answer", "")
            sources = _gr_result.get("sources", [])
            usage = {"in": 0, "out": 0, "total": 0}  # graphrag /query doesn't report token usage
            quota_remaining = None
            if _vault_user:
                with _db() as c:
                    row = c.execute(
                        "SELECT token_quota, token_used FROM users WHERE id=?",
                        (_vault_user["id"],),
                    ).fetchone()
                if row:
                    quota_remaining = max(0, row["token_quota"] - row["token_used"])

            _saved_conv_id = None
            if _vault_user:
                try:
                    _saved_conv_id = _get_or_create_conv(
                        _vault_user["id"], _req_conv_id, project, user_role, chat_mode,
                        question, change_key=_req_change_key,
                    )
                    _save_messages(_saved_conv_id, question, answer, sources, "graphrag", "graphrag-engine", usage)
                except Exception as _e_conv:
                    print(f"[Vault] WARN conv save: {_e_conv}")

            resp = {
                "answer": answer, "sources": sources, "filesScanned": len(sources),
                "provider": "graphrag", "model": "graphrag-engine", "usage": usage,
                "selected_change": selected_change_ctx,
            }
            if quota_remaining is not None:
                resp["quota_remaining"] = quota_remaining
            if _saved_conv_id is not None:
                resp["conversationId"] = _saved_conv_id
            return self._send_json(resp)

        # ── Resolve which openspec directories to search ─────────────────────
        skip = {".state", ".backups", "node_modules"}
        multi_mode = project in ("*", "__all__") or projects_list in (["*"], ["__all__"])

        if multi_mode:
            all_proj = _find_discovery_projects()
            openspecs = [_discovery_openspec_root(p["projectPath"]) for p in all_proj
                         if _discovery_openspec_root(p["projectPath"]).exists()]
            if not openspecs:
                return self._send_json({"error": "no scanned projects found"}, 404)
        elif projects_list:
            openspecs = []
            for pp in projects_list:
                safe = _sanitize_project_key(pp)
                od = _discovery_openspec_root(safe)
                if od.exists():
                    openspecs.append(od)
            if not openspecs:
                return self._send_json({"error": "no openspec directories found for given projects"}, 404)
        else:
            if not project:
                return self._send_json({"error": "missing project or question"}, 400)
            safe_proj = _sanitize_project_key(project)
            openspec  = _discovery_openspec_root(safe_proj)
            if not openspec.exists():
                return self._send_json({"error": "openspec not found"}, 404)
            openspecs = [openspec]

        # ── Detect provider early (to set context budget) ────────────────────
        _forced   = os.environ.get("VAULT_CHAT_PROVIDER", "").lower()
        _qwen_key = os.environ.get("DASHSCOPE_API_KEY", "")
        _anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
        _gh_key   = os.environ.get("GITHUB_TOKEN", "")
        _oai_key  = os.environ.get("OPENAI_API_KEY", "")
        _gn_key   = os.environ.get("GREENNODE_API_KEY", "")
        _gn_base  = os.environ.get("GREENNODE_BASE_URL", "")
        _model_ov = os.environ.get("VAULT_CHAT_MODEL", "")

        # greennode (MSB AI Hackathon 2026 co-organizer platform) opt-in only — same precedence
        # rule as llm.py's _bl_call_llm_ex: explicit VAULT_CHAT_PROVIDER=greennode, or lowest-
        # priority auto-detect so it never silently overrides an existing working setup.
        if   _forced == "qwen"      or (not _forced and _qwen_key and not _anth_key and not _gh_key):
            _early_provider = "qwen"
        elif _forced == "anthropic" or (not _forced and _anth_key):
            _early_provider = "anthropic"
        elif _forced == "github"    or (not _forced and _gh_key and not _oai_key):
            _early_provider = "github"
        elif _forced == "openai"    or (not _forced and _oai_key):
            _early_provider = "openai"
        elif _forced == "greennode" or (not _forced and _gn_key and _gn_base):
            _early_provider = "greennode"
        else:
            _early_provider = "unknown"

        # Context char budget per provider (chars ÷ 4 ≈ tokens)
        # GitHub Models: ALL models have 8k total token limit (input+output)
        # Budget: 8000 − 600(output) − 500(system) − 800(history) = ~6100 → ~5600 chars safe
        _CTX_CAP = {
            "github":    5600,    # 8k hard limit for ALL GitHub Models
            "qwen":      28000,
            "openai":    48000,
            "anthropic": 96000,
            "greennode": 16000,   # chưa rõ giới hạn thật — dùng tạm mức "unknown" cho tới khi có docs
            "unknown":   16000,
        }
        max_ctx_chars = _CTX_CAP.get(_early_provider, 16000)

        # GitHub Models: ALL models (gpt-4o, gpt-4o-mini, Llama...) share 8k total limit
        _is_tight = (_early_provider == "github")

        # ── Keyword extraction ────────────────────────────────────────────────
        words = [w.lower() for w in re.split(r"\W+", search_query or question) if len(w) > 2]

        # ── Tiered context budget based on query complexity ───────────────────
        # Fewer keywords = simpler question = less context needed
        # This prevents "hi" (0 keywords) from loading 8 files × 2500 chars
        _STOP = {"the","and","for","are","was","were","has","have","had","this","that",
                 "với","của","cho","trong","về","từ","được","không","có","là","thì",
                 "một","những","các","và","hay","hoặc","đã","sẽ","đang"}
        sig_words = [w for w in words if w not in _STOP]

        if len(sig_words) == 0:          # greeting / chitchat / single word
            max_files, chars_per_file = 0, 0
        elif len(sig_words) <= 2:        # short / vague question
            max_files, chars_per_file = 3, 1200
        elif len(sig_words) <= 5:        # medium question
            max_files, chars_per_file = 5, 1800
        else:                            # detailed / technical question
            max_files, chars_per_file = 8, 2500

        # Elicit / generate modes always need some context for system continuity
        if chat_mode in ("elicit", "generate") and max_files == 0:
            max_files, chars_per_file = 3, 1200

        # Multi-project: spread budget across projects
        top_limit = min(max_files, 16 if multi_mode else max_files)

        # ── Keyword relevance search ──────────────────────────────────────────
        scored: list[tuple[int, Path, str]] = []
        if max_files > 0 and words:
            for openspec in openspecs:
                for md in openspec.rglob("*.md"):
                    if any(d in md.parts for d in skip):
                        continue
                    try:
                        text  = md.read_text(encoding="utf-8", errors="ignore")
                        score = sum(text.lower().count(w) for w in words)
                        if score > 0:
                            scored.append((score, md, text))
                    except Exception:
                        pass
            scored.sort(key=lambda x: -x[0])

        if multi_mode and len(openspecs) > 1 and scored:
            seen_proj: dict[str, int] = {}
            top: list[tuple[int, Path, str]] = []
            per_proj = max(2, top_limit // len(openspecs))
            for item in scored:
                proj_key = str(item[1].parent.parent.parent)
                if seen_proj.get(proj_key, 0) < per_proj:
                    top.append(item)
                    seen_proj[proj_key] = seen_proj.get(proj_key, 0) + 1
                if len(top) >= top_limit:
                    break
        else:
            top = scored[:top_limit]

        # Fallback ONLY for elicit/generate (they need grounding context)
        # For discover with 0 results → send no context, AI answers from general knowledge
        if not top and chat_mode in ("elicit", "generate"):
            for openspec in openspecs:
                for fb in [openspec / "knowledge" / "00-overview" / "project-map.md",
                           openspec / "specs"]:
                    if fb.is_file():
                        top.append((0, fb, fb.read_text(encoding="utf-8", errors="ignore")))
                    elif fb.is_dir():
                        for md in list(fb.rglob("*.md"))[:2]:
                            top.append((0, md, md.read_text(encoding="utf-8", errors="ignore")))
                    if len(top) >= 3:
                        break

        # ── Build context ────────────────────────────────────────────────────
        context = ""
        sources = []
        for _, md, text in top[:top_limit]:
            rel = _display_workspace_path(ROOT, md)
            context += f"=== {rel} ===\n{text[:chars_per_file]}\n\n"
            sources.append(rel)

        # ── Trim context to provider budget ───────────────────────────────────
        if len(context) > max_ctx_chars:
            # Cut at last clean "===" boundary to avoid mid-file truncation
            trimmed = context[:max_ctx_chars]
            last_sep = trimmed.rfind("\n===")
            if last_sep > max_ctx_chars * 0.5:
                context = trimmed[:last_sep]
                # Drop sources that were trimmed away
                kept = context.count("===")
                sources = sources[:max(1, kept - 1)]
            else:
                context = trimmed

        ws_note = (f" You are answering about a MULTI-PROJECT workspace ({len(openspecs)} projects). "
                   "Distinguish between projects clearly when answering.") if multi_mode else ""

        if has_uploads and not upload_intent_explicit:
            upload_kind, upload_clues = _describe_upload(attachments, image_attachments)
            clue_block = "\n".join(f"- {clue}" for clue in upload_clues if clue)
            related_block = "\n".join(f"- {src}" for src in sources[:5])
            if not related_block:
                related_block = "- Chưa tìm thấy artifact Vault trùng mạnh; tôi có thể dò sâu hơn sau khi bạn nói rõ mục tiêu với file này."

            answer = (
                f"Tôi đã đọc file upload. Có vẻ đây là **{upload_kind}**.\n\n"
                + (f"**Dấu hiệu tôi nhận ra từ file:**\n{clue_block}\n\n" if clue_block else "")
                + f"**Thông tin / Vault artifacts có vẻ liên quan:**\n{related_block}\n\n"
                + "Bạn muốn tôi làm gì với file này?\n"
                + "- Review theo checklist\n"
                + "- Tóm tắt nội dung\n"
                + "- Trích requirement / user stories\n"
                + "- So sánh với Vault hoặc artifact hiện có\n"
                + "- Map vào backlog/change hoặc Output artifact\n\n"
                + "Chỉ cần trả lời ngắn kiểu: `review giúp`, `tóm tắt`, `trích requirement`, hoặc `so sánh với Vault`."
            )

            usage = {"in": 0, "out": 0, "total": 0}
            quota_remaining: int | None = None
            if _vault_user:
                with _db() as c:
                    row = c.execute(
                        "SELECT token_quota, token_used FROM users WHERE id=?",
                        (_vault_user["id"],)
                    ).fetchone()
                if row:
                    quota_remaining = max(0, row["token_quota"] - row["token_used"])

            _saved_conv_id: int | None = None
            if _vault_user and answer:
                try:
                    _saved_conv_id = _get_or_create_conv(
                        _vault_user["id"], _req_conv_id,
                        project, user_role, chat_mode,
                        effective_question if 'effective_question' in locals() else (question or "[Uploaded file pending intent]"),
                        change_key=_req_change_key,
                    )
                    _save_messages(
                        _saved_conv_id,
                        effective_question if 'effective_question' in locals() else (question or "[Uploaded file pending intent]"),
                        answer,
                        sources,
                        "system",
                        "upload-intent-clarifier",
                        usage,
                    )
                except Exception as _e_conv:
                    print(f"[Vault] WARN conv save: {_e_conv}")

            resp: dict = {
                "answer": answer,
                "sources": sources,
                "filesScanned": len(scored),
                "provider": "system",
                "model": "upload-intent-clarifier",
                "usage": usage,
                "selected_change": selected_change_ctx,
            }
            if quota_remaining is not None:
                resp["quota_remaining"] = quota_remaining
            if _saved_conv_id is not None:
                resp["conversationId"] = _saved_conv_id
            return self._send_json(resp)

        # ── System prompt per mode ───────────────────────────────────────────
        _DOC_STRUCTURES = {
            "user_story": (
                "User Story + Acceptance Criteria\n\n"
                "## User Story\n**As a** [role] **I want** [goal] **so that** [benefit]\n\n"
                "## Acceptance Criteria\n"
                "**Scenario [ID]: [name]**\n- Given [context]\n- When [action]\n- Then [outcome]\n\n"
                "## Business Rules\n## Assumptions & Dependencies\n## Out of Scope\n## Open Questions ⚠️"
            ),
            "technical_design": (
                "Technical Design Document\n\n"
                "## Overview & Goals\n## Architecture Decision\n## Component Design\n"
                "## API Contracts\n## Data Model Changes\n## Integration Points\n"
                "## Error Handling\n## Performance Considerations\n## Security Considerations\n"
                "## Migration Plan\n## Open Questions ⚠️"
            ),
            "implementation_plan": (
                "Implementation Plan\n\n"
                "## Objective\n## Scope\n## Technical Tasks (breakdown with estimate)\n"
                "## Dependencies & Blockers\n## Risk Register\n## Definition of Done\n"
                "## Rollback Plan\n## Open Questions ⚠️"
            ),
            "test_plan": (
                "Test Plan\n\n"
                "## Test Scope\n## Test Scenarios (functional)\n## Edge Cases & Negative Tests\n"
                "## Performance Tests\n## Security Tests\n## Regression Scope\n"
                "## UAT Acceptance Criteria\n## Open Questions ⚠️"
            ),
            "impact_assessment": (
                "Impact Assessment\n\n"
                "## Change Summary\n## Impacted Services/Components\n## API Breaking Changes\n"
                "## Database Changes\n## Downstream Effects\n## Risk Level & Mitigation\n"
                "## Rollout Strategy\n## Open Questions ⚠️"
            ),
            "brd": (
                "Business Requirements Document\n\n"
                "## Executive Summary\n## Business Objectives & KPIs\n## Stakeholders\n"
                "## Functional Requirements (REQ-XXX: MUST/SHOULD/MAY)\n"
                "## Non-Functional Requirements\n## Constraints & Assumptions\n"
                "## Out of Scope\n## Open Questions ⚠️"
            ),
        }
        _doc_structure = _DOC_STRUCTURES.get(doc_type, _DOC_STRUCTURES["user_story"])

        _ROLE_DESCS = {
            "PO": "Product Owner — tập trung vào business value, ROI, stakeholder",
            "BA": "Business Analyst — tập trung vào business rules, use cases, acceptance criteria",
            "SA": "Solution Architect — tập trung vào kiến trúc, API, data model, integration",
            "EA": "Enterprise Architect — tập trung vào governance, standards, ADR",
            "TL": "Tech Lead — tập trung vào technical risks, tasks, DoD",
            "DEV": "Developer — tập trung vào implementation detail, code, API spec",
            "TEST": "QA/Tester — tập trung vào test scenarios, edge cases, coverage",
        }
        _role_desc = _ROLE_DESCS.get(user_role, f"Role: {user_role}")

        if chat_mode == "elicit":
            _no_change_note = (
                "\n⚠️ LƯU Ý: Người dùng CHƯA chọn Change context. "
                "Nếu họ chưa nêu rõ tên tính năng đang khai thác, hãy hỏi ngay câu đầu tiên: "
                "'Bạn đang muốn khai thác yêu cầu cho tính năng nào?'\n"
            ) if not _req_change_key else ""
            _dim_names = {
                "GOAL":    "Mục tiêu kinh doanh (GOAL)",
                "ACTORS":  "Actors / Users (ACTORS)",
                "FLOW":    "Luồng xử lý (FLOW)",
                "RULES":   "Business Rules (RULES)",
                "EDGE":    "Edge Cases / Exceptions (EDGE)",
                "IMPACT":  "Impact / Dependencies (IMPACT)",
                "QUALITY": "Quality Gates (QUALITY)",
            }
            _all_dims = list(_dim_names.keys())
            _valid_covered = [d for d in _covered_dims if d in _dim_names]
            _remaining     = [d for d in _all_dims if d not in _valid_covered]

            if _valid_covered:
                _covered_str = ", ".join(_dim_names[d] for d in _valid_covered)
                if _remaining:
                    _remaining_str = "\n".join(f"{i+1}. {_dim_names[d]}" for i, d in enumerate(_remaining))
                    _no_reask_block = (
                        f"\n⛔ ĐÃ KHAI THÁC ĐỦ (TUYỆT ĐỐI KHÔNG HỎI LẠI):\n{_covered_str}\n\n"
                        f"✅ CẦN HỎI TIẾP ({len(_remaining)} chiều còn lại):\n{_remaining_str}\n"
                    )
                else:
                    _no_reask_block = (
                        f"\n✅ ĐÃ KHAI THÁC ĐỦ CẢ 7 CHIỀU: {_covered_str}\n"
                        "Hãy thông báo hoàn tất bằng dòng: '✅ Elicitation hoàn tất — đã đủ thông tin để tạo tài liệu.'\n"
                    )
            else:
                _no_reask_block = "\n- Ghi nhớ những gì đã được trả lời trong hội thoại này, không hỏi lại.\n"

            system_prompt = (
                "Bạn là Senior Business/Solution Analyst đang phỏng vấn khai thác yêu cầu phần mềm.\n"
                f"Người dùng hiện tại: {_role_desc}.\n"
                + _no_change_note +
                "Bạn có knowledge artifacts của hệ thống hiện tại trong context bên dưới — dùng chúng để hỏi câu hỏi CỤ THỂ liên quan đến hệ thống thực tế.\n\n"
                "NHIỆM VỤ: Dẫn dắt phỏng vấn có cấu trúc, hỏi ĐÚNG MỘT câu mỗi lần, bao phủ 7 chiều:\n"
                "1. GOAL — Mục tiêu kinh doanh, lý do cần làm, KPI thành công\n"
                "2. ACTORS — Ai dùng? Roles, systems tương tác?\n"
                "3. FLOW — Happy path từng bước, điểm bắt đầu/kết thúc\n"
                "4. BUSINESS RULES — Ràng buộc, validation, tính toán, chính sách\n"
                "5. EDGE CASES — Lỗi, exception, boundary values, rollback\n"
                "6. IMPACT — Service/feature nào bị ảnh hưởng? Breaking changes?\n"
                "7. QUALITY GATES — Performance, security, compliance requirements\n"
                + _no_reask_block +
                "\nQUY TẮC:\n"
                "- Hỏi ĐỘC LẬP từng câu — không liệt kê nhiều câu hỏi cùng lúc\n"
                "- Dùng thông tin từ artifacts để hỏi câu hỏi có ngữ cảnh\n"
                "- Sau khi đủ 7 chiều, kết thúc bằng dòng: '✅ Elicitation hoàn tất — đã đủ thông tin để tạo tài liệu.'\n"
                + ws_note
            )
        elif chat_mode == "generate":
            system_prompt = (
                f"Bạn là technical writer tạo tài liệu phần mềm chuyên nghiệp.\n"
                f"Dựa trên toàn bộ cuộc hội thoại khai thác yêu cầu đã có, hãy tạo tài liệu: **{_doc_structure.splitlines()[0]}**\n\n"
                f"Cấu trúc tài liệu cần theo:\n{_doc_structure}\n\n"
                "YÊU CẦU:\n"
                "- Đầy đủ: cover TẤT CẢ khía cạnh đã thảo luận, kể cả implicit\n"
                "- Đánh dấu gap bằng '⚠️ Cần làm rõ: ...' cho mục còn thiếu thông tin\n"
                "- Format: Markdown chuẩn, có thể paste thẳng vào Confluence/Notion\n"
                "- Gắn Requirement ID (REQ-XXX) cho mỗi requirement\n"
                "- Tiếng Việt cho prose, tiếng Anh cho technical terms\n"
                "- Thêm metadata header: Feature, Author role, Date, Status: Draft\n"
                + ws_note
            )
        else:  # discover (default)
            review_note = ""
            if review_id and review_ctx:
                review_note = (
                    f" You are focused on a specific review item '{review_id}'."
                    " Help determine if the inference is correct, find confirming evidence, or identify what to ask."
                )
            change_behavior_note = ""
            if selected_change_ctx:
                change_behavior_note = (
                    " You are currently helping with one ACTIVE CHANGE."
                    " Use the selected change's private backlog context actively: backlog details, checklist/template mapping, interview synthesis, and orchestrate output history when available."
                    " If the user asks what context or sources you are using for the change, explicitly name the available private source types instead of mentioning only the change key/title."
                    " RESPONSE CONTRACT: first answer the user's current question directly and concretely."
                    " Then ask exactly one follow-up question that is most useful next for that active change."
                    " Never reply with only questions. If information is partial, say what is known first, then ask the follow-up."
                    " Use this shape whenever possible: 'Trả lời: ...' then 'Câu hỏi tiếp theo: ...?'."
                    " Keep the interaction natural and grounded in the selected change."
                )
            elif change_guidance_candidates:
                change_behavior_note = (
                    " The user appears to be asking to work on a change, but no change has been selected yet."
                    " Stay in discovery mode for this turn."
                    " First answer the current question directly using the artifact context."
                    " Then add a short section titled 'Change có thể chọn' using the provided candidate list."
                    " Finish with one sentence saying that if the user does not choose a change, you will continue answering in discovery mode."
                    " Do not ask the user to choose a change before answering the current question."
                )
            system_prompt = (
                "You are an AI assistant embedded in a codebase knowledge dashboard."
                + ws_note + review_note + change_behavior_note +
                " Follow the current user request as the highest-priority instruction for this turn."
                " Use ONLY the sources provided in the turn context."
                " Source priority for this turn:"
                " (1) current user request controls the task,"
                " (2) uploaded files/images are the primary evidence when present,"
                " (3) selected change/backlog private context such as checklist, template, interview synthesis, orchestrate outputs, and backlog details,"
                " (4) recent chat history for continuity only,"
                " (5) Vault general knowledge as supporting context."
                " Reliability rule: prefer the most specific source that directly supports the user's requested task;"
                " never let chat history or Vault general knowledge override the current request or contradict uploaded/private backlog evidence."
                " If no change is selected, still answer the current request first and only then briefly suggest selecting a change for richer private context."
                " Be concise and precise. Cite file paths in backticks when referencing sources."
                " If the answer is not in the context, say so clearly."
            )

        if has_uploads:
            # HARD INVARIANT: any uploaded file/image is the primary input for the turn.
            # Future refactors must preserve attachment-first handling before normal chat logic.
            attachment_mode_note = (
                "\n\nHARD UPLOAD-FIRST INVARIANT FOR THIS TURN:\n"
                "- If any file/image attachment exists, you MUST read attachment content before using Vault context, change context, chat history, or asking follow-up questions.\n"
                "- Treat uploaded content as the PRIMARY input for the turn; Vault artifacts are secondary supporting context.\n"
                "- Never answer as if the upload were unseen, skipped, or optional.\n"
                "- First state briefly what the upload appears to be and which Vault areas look relevant.\n"
            )
            if upload_intent_explicit:
                attachment_mode_note += (
                    "- The user already gave a concrete intent. Execute that intent directly using the uploaded content plus Vault context.\n"
                    "- If the request asks to summarize, review, extract requirements, compare, rewrite, translate, classify, or validate the upload, do that exact task directly.\n"
                    "- Ask a follow-up only if one blocking detail prevents a reliable answer.\n"
                )
            else:
                attachment_mode_note += (
                    "- The user's intent is missing or ambiguous. Do NOT assume the final task.\n"
                    "- Respond in this order: (1) short file understanding, (2) short list of relevant Vault findings, (3) one clear clarifying question asking what the user wants done with the file.\n"
                    "- Do not persist, publish, or create final artifacts yet.\n"
                )
            if chat_mode == "elicit":
                attachment_mode_note += "- Do not continue normal elicitation questioning until the file intent is clarified or fulfilled.\n"
            system_prompt += attachment_mode_note

        # GitHub gpt-4o-mini has 8k total (input+output) limit → be conservative
        if _is_tight:
            max_tokens = 1500 if chat_mode == "generate" else 800
        else:
            max_tokens = 3000 if chat_mode == "generate" else 1800

        # ── Build file attachment block (text) ──────────────────────────────────
        file_block = ""
        if attachments:
            parts = []
            _max_files   = 2   if _is_tight else 5
            _max_content = 3000 if _is_tight else 15000
            for att in attachments[:_max_files]:
                name    = att.get("name", "file")
                content = (att.get("content") or "")[:_max_content]
                note    = " (đã cắt bớt)" if att.get("truncated") or len(att.get("content","")) > _max_content else ""
                parts.append(f"=== FILE: {name}{note} ===\n{content}")
            file_block = "\n\n" + "\n\n".join(parts)
            system_prompt += (
                "\n\nTEXT/DOCUMENT UPLOADS:\n"
                "- Analyze uploaded text/document content carefully.\n"
                "- Reference relevant Vault artifacts when useful for comparison or context.\n"
                "- If the user's intent is review/validation, point out issues, gaps, and inconsistencies clearly.\n"
            )

        # ── Parse image attachments (base64 DataURL) ────────────────────────────
        parsed_images = []  # [{name, media_type, b64data}]
        for img in image_attachments[:3]:  # max 3 images
            try:
                dataurl = img.get("dataUrl", "")
                if "," not in dataurl:
                    continue
                header, b64data = dataurl.split(",", 1)
                # header: "data:image/png;base64"
                media_type = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
                if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                    media_type = "image/png"
                parsed_images.append({"name": img.get("name", "image"), "media_type": media_type, "b64data": b64data})
            except Exception:
                pass
        if parsed_images:
            system_prompt += (
                "\n\nIMAGE UPLOADS:\n"
                "- Carefully inspect attached screenshots/images/diagrams before answering.\n"
                "- If the image shows UI, workflow, or diagrams, relate it to the closest Vault artifacts and explain that mapping.\n"
            )

        msgs = []
        _hist_max  = (4 if _is_tight else 12) if chat_mode in ("elicit", "generate") else (2 if _is_tight else 6)
        _hist_trim = 800 if _is_tight else None   # truncate each history turn for tight providers
        for h in history[-_hist_max:]:
            if h.get("role") in ("user", "assistant"):
                content = h["content"]
                if _hist_trim and len(content) > _hist_trim:
                    content = content[:_hist_trim] + "…"
                msgs.append({"role": h["role"], "content": content})
        review_block = f"\n\nREVIEW ITEM CONTEXT:\n{review_ctx}" if review_ctx and chat_mode == "discover" else ""
        change_context_block = ""
        if selected_change_ctx:
            change_context_block = (
                "\n\nACTIVE CHANGE CONTEXT:\n"
                f"- Change key: {selected_change_ctx.get('change_key') or '-'}\n"
                f"- Title: {selected_change_ctx.get('title') or '-'}\n"
                f"- Status: {selected_change_ctx.get('status') or '-'}\n"
                f"- Priority: {selected_change_ctx.get('priority') or '-'}\n"
                f"- Description: {selected_change_ctx.get('description') or '(không có mô tả)'}\n"
            )
            latest_change_synthesis = (selected_change_private_ctx or {}).get("latest_synthesis")
            if latest_change_synthesis:
                change_context_block += (
                    "\nLATEST CHANGE SYNTHESIS:\n"
                    + str(latest_change_synthesis.get("content") or "")[:3000]
                    + ("\n...[truncated]" if len(str(latest_change_synthesis.get("content") or "")) > 3000 else "")
                )
            private_candidates = (selected_change_private_ctx or {}).get("artifact_candidates") or []
            if private_candidates:
                private_lines = []
                for cand in private_candidates[:2]:
                    template_cues = [
                        line.strip() for line in str(cand.get("template") or "").splitlines()
                        if line.strip()
                    ][:4]
                    checklist_cues = [
                        str(item).strip() for item in (cand.get("checklist_items") or [])
                        if str(item).strip()
                    ][:6]
                    private_lines.append(
                        f"- {cand.get('name') or cand.get('artifact_key') or 'Artifact'} [{cand.get('channel_label') or '-'}]"
                        f" | key={cand.get('artifact_key') or '-'}"
                        f" | history={'yes' if cand.get('has_history') else 'no'}\n"
                        f"  Template cues: {' | '.join(template_cues) if template_cues else '[không có template riêng]'}\n"
                        f"  Checklist cues: {' | '.join(checklist_cues) if checklist_cues else '[không có checklist riêng]'}"
                    )
                change_context_block += (
                    "\n\nBACKLOG PRIVATE CONTEXT - CHECKLIST/TEMPLATE MAPPING:\n"
                    + "\n".join(private_lines)
                )
            output_history = (selected_change_private_ctx or {}).get("output_history") or []
            private_source_labels = ["backlog details"]
            if latest_change_synthesis:
                private_source_labels.append("interview/orchestrate synthesis")
            if private_candidates:
                private_source_labels.append("artifact checklist/template mapping")
            if output_history:
                private_source_labels.append("orchestrate output history")
            change_context_block += (
                "\n\nACTIVE CHANGE PRIVATE SOURCE SUMMARY:\n"
                f"- Available private source types: {', '.join(private_source_labels)}"
            )
            if output_history:
                history_lines = []
                for item in output_history[:2]:
                    history_lines.append(
                        f"- {item.get('name') or item.get('artifact_key') or 'Artifact'}"
                        f" v{item.get('version') or 0} ({item.get('created_at') or '-'})\n"
                        + str(item.get("content") or "")[:1200]
                        + ("\n...[truncated]" if len(str(item.get("content") or "")) > 1200 else "")
                    )
                change_context_block += (
                    "\n\nBACKLOG PRIVATE CONTEXT - LATEST ORCHESTRATE OUTPUTS:\n"
                    + "\n\n".join(history_lines)
                )
        change_candidate_block = ""
        if change_guidance_candidates and not selected_change_ctx:
            change_candidate_block = (
                "\n\nAVAILABLE CHANGE CANDIDATES:\n"
                + _format_change_candidate_lines(change_guidance_candidates)
            )
        effective_question = question
        if has_uploads and not plain_question:
            effective_question = "[Người dùng vừa gửi file nhưng chưa nêu ý định cụ thể. Hãy đọc file, tìm thông tin liên quan trong Vault, rồi hỏi lại họ muốn làm gì với file này.]"

        user_request_for_model = (plain_question or effective_question or question).strip()
        change_selection_status_block = "CHANGE SELECTION STATUS:\n"
        if selected_change_ctx:
            change_selection_status_block += (
                f"- Active change: {selected_change_ctx.get('change_key') or '-'}"
                f" — {selected_change_ctx.get('title') or '-'}"
            )
        else:
            change_selection_status_block += (
                "- No change selected for this turn."
                " Answer the current request first; if a richer backlog-private answer would help,"
                " briefly suggest selecting a change afterward."
            )

        source_priority_block = (
            "SOURCE PRIORITY FOR THIS TURN:\n"
            "1. Current user request controls the task.\n"
            "2. Uploaded files/images are primary evidence when present.\n"
            "3. Selected change/backlog private context (checklist, template, synthesis, output history, backlog details).\n"
            "4. Recent chat history is continuity only.\n"
            "5. Vault general knowledge is supporting context."
        )

        upload_primary_block = ""
        if has_uploads:
            primary_parts = ["UPLOADED CONTENT (PRIMARY INPUT - ALWAYS READ THIS FIRST):"]
            if file_block:
                primary_parts.append(file_block.strip())
            if image_attachments:
                image_names = ", ".join(
                    str(img.get("name") or "image").strip() or "image"
                    for img in image_attachments[:3]
                )
                primary_parts.append(f"ATTACHED IMAGES: {image_names}")
            upload_primary_block = "\n\n".join(part for part in primary_parts if part).strip() + "\n\n"

        text_content = (
            f"{upload_primary_block}"
            f"CURRENT USER REQUEST (HIGHEST PRIORITY):\n{user_request_for_model}\n\n"
            f"{source_priority_block}\n\n"
            f"{change_selection_status_block}\n\n"
            f"Knowledge artifacts context:\n\n{context}\n"
            f"---{review_block}{change_context_block}{change_candidate_block}\n\n"
            f"Question: {user_request_for_model}"
        )
        # Base text-only message (provider-specific vision handled below per-provider)
        msgs.append({"role": "user", "content": text_content})

        # ── Provider detection ───────────────────────────────────────────────
        forced   = os.environ.get("VAULT_CHAT_PROVIDER", "").lower()
        qwen_key = os.environ.get("DASHSCOPE_API_KEY", "")
        anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
        oai_key  = os.environ.get("OPENAI_API_KEY", "")
        gh_key   = os.environ.get("GITHUB_TOKEN", "")
        gn_key   = os.environ.get("GREENNODE_API_KEY", "")
        gn_base  = os.environ.get("GREENNODE_BASE_URL", "")
        model_ov = os.environ.get("VAULT_CHAT_MODEL", "")

        if forced == "qwen"      or (not forced and qwen_key and not anth_key and not gh_key):
            provider = "qwen"
        elif forced == "anthropic" or (not forced and anth_key):
            provider = "anthropic"
        elif forced == "github"  or (not forced and gh_key and not oai_key):
            provider = "github"
        elif forced == "openai"  or (not forced and oai_key):
            provider = "openai"
        elif forced == "greennode" or (not forced and gn_key and gn_base):
            provider = "greennode"
        else:
            return self._send_json({
                "error": "No LLM API key found",
                "hint": (
                    "Thêm một trong các key sau vào .env:\n"
                    "  GITHUB_TOKEN=ghp_...        (GitHub Models — dùng với Copilot)\n"
                    "  ANTHROPIC_API_KEY=sk-ant-... (Claude)\n"
                    "  OPENAI_API_KEY=sk-...        (OpenAI)\n"
                    "  DASHSCOPE_API_KEY=sk-...     (Qwen)\n"
                    "  GREENNODE_API_KEY=... + GREENNODE_BASE_URL=... (GreenNode — MSB AI Hackathon 2026)"
                ),
            }, 503)

        # ── Call LLM ────────────────────────────────────────────────────────
        try:
            answer = ""

            usage: dict = {}   # {in, out, total} populated per-provider

            if provider == "qwen":
                if parsed_images and not model_ov:
                    model = "qwen-vl-plus"
                else:
                    model = model_ov or "qwen-plus"
                url = os.environ.get("DASHSCOPE_BASE_URL",
                    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1") + "/chat/completions"
                if parsed_images:
                    user_content: list[dict] = []
                    for img in parsed_images:
                        dataurl = f"data:{img['media_type']};base64,{img['b64data']}"
                        user_content.append({"type": "image_url", "image_url": {"url": dataurl}})
                    user_content.append({"type": "text", "text": text_content})
                    qwen_msgs = msgs[:-1] + [{"role": "user", "content": user_content}]
                else:
                    qwen_msgs = msgs
                payload = json.dumps({
                    "model": model,
                    "messages": [{"role": "system", "content": system_prompt}] + qwen_msgs,
                    "max_tokens": max_tokens,
                }).encode()
                req = _ur.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {qwen_key}",
                    "Content-Type": "application/json",
                })
                with _ur.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                answer = data["choices"][0]["message"]["content"]
                u = data.get("usage", {})
                usage = {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                         "total": u.get("total_tokens", 0)}

            elif provider == "anthropic":
                try:
                    import anthropic as _ant  # type: ignore
                except ImportError:
                    return self._send_json({"error": "pip install anthropic required"}, 503)
                default_model = "claude-sonnet-4-6" if parsed_images else "claude-haiku-4-5-20251001"
                model  = model_ov or default_model
                if parsed_images:
                    ant_content: list[dict] = []
                    for img in parsed_images:
                        ant_content.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": img["media_type"], "data": img["b64data"]},
                        })
                    ant_content.append({"type": "text", "text": text_content})
                    ant_msgs = msgs[:-1] + [{"role": "user", "content": ant_content}]
                else:
                    ant_msgs = msgs
                client = _ant.Anthropic(api_key=anth_key)
                resp   = client.messages.create(
                    model=model, max_tokens=max_tokens,
                    system=system_prompt, messages=ant_msgs,
                )
                answer = resp.content[0].text
                u = resp.usage
                usage = {"in": u.input_tokens, "out": u.output_tokens,
                         "total": u.input_tokens + u.output_tokens}

            elif provider == "openai":
                model = model_ov or ("gpt-4o" if parsed_images else "gpt-4o-mini")
                url   = os.environ.get("OPENAI_BASE_URL",
                                       "https://api.openai.com/v1") + "/chat/completions"
                if parsed_images:
                    oai_content: list[dict] = []
                    for img in parsed_images:
                        dataurl = f"data:{img['media_type']};base64,{img['b64data']}"
                        oai_content.append({"type": "image_url", "image_url": {"url": dataurl}})
                    oai_content.append({"type": "text", "text": text_content})
                    oai_msgs = msgs[:-1] + [{"role": "user", "content": oai_content}]
                else:
                    oai_msgs = msgs
                payload = json.dumps({
                    "model": model,
                    "messages": [{"role": "system", "content": system_prompt}] + oai_msgs,
                    "max_completion_tokens": max_tokens,
                }).encode()
                req = _ur.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {oai_key}",
                    "Content-Type": "application/json",
                })
                with _ur.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                answer = data["choices"][0]["message"]["content"]
                u = data.get("usage", {})
                usage = {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                         "total": u.get("total_tokens", 0)}

            elif provider == "github":
                # GitHub Models — OpenAI-compatible, auth via GitHub PAT
                # Endpoint: https://models.inference.ai.azure.com
                # Supported models: gpt-4o, gpt-4o-mini, o1-mini, Llama-3.1-*, Mistral-*...
                if parsed_images and not model_ov:
                    model = "gpt-4o"           # only gpt-4o supports vision
                else:
                    model = model_ov or "gpt-4o-mini"
                url = "https://models.inference.ai.azure.com/chat/completions"
                if parsed_images:
                    gh_content: list[dict] = []
                    for img in parsed_images:
                        dataurl = f"data:{img['media_type']};base64,{img['b64data']}"
                        gh_content.append({"type": "image_url", "image_url": {"url": dataurl}})
                    gh_content.append({"type": "text", "text": text_content})
                    gh_msgs = msgs[:-1] + [{"role": "user", "content": gh_content}]
                else:
                    gh_msgs = msgs
                gh_payload_obj = {
                    "model": model,
                    "messages": [{"role": "system", "content": system_prompt}] + gh_msgs,
                    "max_completion_tokens": max_tokens,
                }
                payload = json.dumps(gh_payload_obj).encode()
                _total_chars = sum(len(str(m.get("content",""))) for m in gh_payload_obj["messages"])
                print(f"[GitHub] model={model} max_completion_tokens={max_tokens} "
                      f"total_chars={_total_chars} (~{_total_chars//4} tokens) "
                      f"payload_bytes={len(payload)} is_tight={_is_tight}")
                req = _ur.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {gh_key}",
                    "Content-Type": "application/json",
                })
                with _ur.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                answer = data["choices"][0]["message"]["content"]
                u = data.get("usage", {})
                usage = {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                         "total": u.get("total_tokens", 0)}

            elif provider == "greennode":
                # MSB AI Hackathon 2026 co-organizer platform — docs/keys pending at time of
                # writing, assumed OpenAI-compatible /chat/completions (base_url from env). No
                # image/vision support wired here (unconfirmed whether GreenNode's API supports
                # it) — text-only for now; extend once real docs land, same as the other
                # providers' image branches above.
                model = model_ov or os.environ.get("GREENNODE_MODEL", "greennode-default")
                url = gn_base.rstrip("/") + "/chat/completions"
                payload = json.dumps({
                    "model": model,
                    "messages": [{"role": "system", "content": system_prompt}] + msgs,
                    "max_tokens": max_tokens,
                }).encode()
                req = _ur.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {gn_key}",
                    "Content-Type": "application/json",
                })
                with _ur.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                answer = data["choices"][0]["message"]["content"]
                u = data.get("usage", {})
                usage = {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                         "total": u.get("total_tokens", 0)}

            if selected_change_ctx and answer:
                answer = answer.strip()
                if re.fullmatch(r"(?:[^\n]*\?\s*){1,3}", answer, re.S):
                    answer = (
                        "Trả lời: Tôi vẫn đang bám theo change hiện tại, nhưng phần artifacts đang nạp chưa đủ để kết luận chắc hơn ngay ở lượt này.\n\n"
                        + "Câu hỏi tiếp theo: " + _default_change_follow_up(selected_change_ctx)
                    )
                elif not re.search(r"câu hỏi tiếp theo\s*:", answer, re.I) and "?" not in answer:
                    answer += "\n\nCâu hỏi tiếp theo: " + _default_change_follow_up(selected_change_ctx)

            if change_guidance_candidates and not selected_change_ctx and answer:
                if not re.search(r"change có thể chọn", answer, re.I):
                    answer = (
                        answer.strip()
                        + "\n\n**Change có thể chọn:**\n"
                        + _format_change_candidate_lines(change_guidance_candidates)
                        + "\n\nNếu bạn chưa chọn change nào, tôi sẽ tiếp tục trả lời ở chế độ khám phá."
                    )
                elif "chế độ khám phá" not in answer.lower():
                    answer = answer.strip() + "\n\nNếu bạn chưa chọn change nào, tôi sẽ tiếp tục trả lời ở chế độ khám phá."
            elif change_selection_hint and answer:
                normalized_answer = answer.strip()
                if "chọn change" not in normalized_answer.lower() and "change có thể chọn" not in normalized_answer.lower():
                    answer = (
                        normalized_answer
                        + "\n\nNếu bạn chọn một Change cụ thể, tôi có thể dùng thêm checklist/template, tổng hợp phỏng vấn, output orchestrate và backlog context riêng của Change đó để trả lời sát hơn."
                    )

            # ── Update token usage in DB ──────────────────────────────────────
            tokens_used = usage.get("total", 0)
            quota_remaining: int | None = None
            if _vault_user and tokens_used > 0:
                with _db() as c:
                    c.execute(
                        "UPDATE users SET token_used = token_used + ? WHERE id=?",
                        (tokens_used, _vault_user["id"]),
                    )
                    row = c.execute(
                        "SELECT token_quota, token_used FROM users WHERE id=?",
                        (_vault_user["id"],)
                    ).fetchone()
                if row:
                    quota_remaining = max(0, row["token_quota"] - row["token_used"])

            # ── P8 — Auto-save conversation turn ─────────────────────────────
            _saved_conv_id: int | None = None
            if _vault_user and answer:
                try:
                    _saved_conv_id = _get_or_create_conv(
                        _vault_user["id"], _req_conv_id,
                        project, user_role, chat_mode, effective_question,
                        change_key=_req_change_key,
                    )
                    _save_messages(_saved_conv_id, effective_question, answer,
                                   sources, provider, model, usage)
                except Exception as _e_conv:
                    print(f"[Vault] WARN conv save: {_e_conv}")

            resp: dict = {
                "answer": answer,
                "sources": sources,
                "filesScanned": len(scored),
                "provider": provider,
                "model": model,
                "usage": usage,
                "selected_change": selected_change_ctx,
            }
            if quota_remaining is not None:
                resp["quota_remaining"] = quota_remaining
            if _saved_conv_id is not None:
                resp["conversationId"] = _saved_conv_id
            return self._send_json(resp)

        except _ue.HTTPError as e:
            body_err = e.read().decode("utf-8", errors="ignore")
            # Parse provider error → extract readable message
            msg = body_err
            err_code = ""
            err_type = ""
            try:
                err_data = json.loads(body_err)
                inner = err_data.get("error") or {}
                if isinstance(inner, dict):
                    msg      = inner.get("message", body_err)
                    err_code = str(inner.get("code", ""))
                    err_type = str(inner.get("type", ""))
                elif isinstance(inner, str):
                    msg = inner
            except Exception:
                pass

            # Build helpful hint per error category
            hint = None
            low = (msg + err_code + err_type).lower()
            if "freetieronly" in low or "free tier" in low or "allocation" in low:
                hint = (
                    "Qwen free tier đã hết quota.\n\n"
                    "**Cách fix:**\n"
                    "1. Vào DashScope Console → tắt **Use free tier only**\n"
                    "2. Hoặc thêm `ANTHROPIC_API_KEY=sk-ant-...` vào `.env` để dùng Claude\n"
                    "3. Hoặc thêm `OPENAI_API_KEY=sk-...` vào `.env` để dùng GPT"
                )
            elif e.code == 401 or "unauthorized" in low or "invalid api key" in low:
                hint = "API key không hợp lệ hoặc đã hết hạn — kiểm tra lại key trong `.env`"
            elif e.code == 429 or "rate limit" in low or "too many" in low:
                if "86400" in msg or "userbymodelby" in low or "per day" in low:
                    # Parse wait time if available
                    import re as _re2
                    _wait = _re2.search(r'wait\s+(\d+)\s+second', msg, _re2.I)
                    _hrs  = f" (~{int(_wait.group(1))//3600} giờ)" if _wait else ""
                    hint  = (
                        f"⏳ **Hết quota ngày của GitHub Models** (50 req/ngày){_hrs}.\n\n"
                        "**Cách khắc phục:**\n"
                        "1. Thêm `ANTHROPIC_API_KEY=sk-ant-...` vào `.env` → dùng Claude (không giới hạn ngày)\n"
                        "2. Thêm `OPENAI_API_KEY=sk-...` vào `.env` → dùng OpenAI\n"
                        "3. Hoặc chờ đến ngày mai để quota GitHub Models reset"
                    )
                else:
                    hint = "Rate limit — thử lại sau vài giây"
            elif e.code == 402 or "insufficient" in low or "billing" in low:
                hint = "Tài khoản chưa nạp credit — vào console của provider để nạp tiền"

            out: dict = {"error": msg}
            if hint:
                out["hint"] = hint
            return self._send_json(out, e.code)

        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

