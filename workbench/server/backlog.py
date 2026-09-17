"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import os
import re
import traceback
from urllib.parse import parse_qs, urlparse

from .db import _db
from .discovery_helpers import _is_workspace_key, _workspace_key_for_path
from .knowledge_export import delete_backlog_item_export, export_backlog_item
from .llm import _bl_call_llm
from .shared import _sanitize_project_key

# ---- source line 10532 (_bl_norm_text) ----
def _bl_norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


# ---- source line 10536 (_bl_match_header) ----
def _bl_match_header(headers, keywords):
    for header in headers:
        norm = _bl_norm_text(header)
        if any(keyword in norm for keyword in keywords):
            return header
    return None


# ---- source line 10544 (_bl_sheet_label) ----
def _bl_sheet_label(sheet_name: str) -> str:
    label = re.sub(r"^[0-9]+[_\-\s]*", "", str(sheet_name or "")).strip(" _-")
    return label or str(sheet_name or "").strip()


# ---- source line 10549 (_bl_guess_priority) ----
def _bl_guess_priority(raw_value: str, fallback_text: str = "") -> str:
    norm = _bl_norm_text(raw_value)
    if any(token in norm for token in ["high", "cao", "critical", "p0", "p1", "q1", "quý 1", "quy 1", "q2", "quý 2", "quy 2"]):
        return "high"
    if any(token in norm for token in ["medium", "trung", "p2", "q3", "quý 3", "quy 3"]):
        return "medium"
    if any(token in norm for token in ["low", "thấp", "thap", "p3", "p4", "q4", "quý 4", "quy 4"]):
        return "low"
    fallback_norm = _bl_norm_text(fallback_text)
    if any(token in fallback_norm for token in ["khẩn", "urgent", "critical", "gấp", "gap"]):
        return "high"
    return "medium"


# ---- source line 10563 (_bl_guess_category) ----
def _bl_guess_category(title: str, description: str, raw_value: str = "") -> str:
    norm = _bl_norm_text(" ".join([title, description, raw_value]))
    if any(token in norm for token in ["bug", "lỗi", "loi", "fix", "sửa"]):
        return "bug"
    if any(token in norm for token in ["tech debt", "technical debt", "refactor", "cleanup", "nợ kỹ thuật", "no ky thuat"]):
        return "tech-debt"
    if any(token in norm for token in ["tối ưu", "toi uu", "cải tiến", "cai tien", "hoàn thiện", "hoan thien", "nâng cấp", "nang cap", "ui/ux"]):
        return "improvement"
    return "feature"


# ---- source line 10574 (_bl_map_structured_headers) ----
def _bl_map_structured_headers(headers):
    title = _bl_match_header(headers, [
        "nhóm tính năng", "nhom tinh nang", "hành trình", "hanh trinh",
        "feature", "title", "epic", "story", "task", "hạng mục", "hang muc"
    ])
    description = _bl_match_header(headers, [
        "nội dung đã chuẩn", "noi dung da chuan", "nội dung", "noi dung",
        "mô tả", "mo ta", "description", "tóm tắt", "tom tat",
        "chi tiết yêu cầu", "chi tiet yeu cau"
    ])
    if not title or not description:
        return None
    return {
        "title": title,
        "description": description,
        "priority": _bl_match_header(headers, ["priority", "ưu tiên", "uu tien", "mức độ", "muc do"]),
        "category": _bl_match_header(headers, ["category", "loại", "loai", "type"]),
        "quarter": _bl_match_header(headers, ["quý", "quy", "quarter", "phase", "sprint"]),
        "count": _bl_match_header(headers, ["số yêu cầu", "so yeu cau", "count", "estimate", "size"]),
        "notes": _bl_match_header(headers, ["ghi chú", "ghi chu", "note"]),
    }


# ---- source line 10597 (_bl_extract_structured_backlog_items) ----
def _bl_extract_structured_backlog_items(content: str):
    if "=== Sheet:" not in content or "\t" not in content:
        return []

    items = []
    seen = set()
    current_sheet = ""
    headers = None
    header_map = None
    current_cells = None

    def _flush_current_row():
        nonlocal current_cells
        if not current_cells or not headers or not header_map:
            current_cells = None
            return

        cells = [cell.strip() for cell in current_cells]
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            cells = cells[:len(headers) - 1] + [" ".join(cells[len(headers) - 1:]).strip()]

        row = {header: value for header, value in zip(headers, cells)}
        title = (row.get(header_map["title"], "") or "").strip(" •\t-")
        description = (row.get(header_map["description"], "") or "").strip()
        if not title or not description:
            current_cells = None
            return
        if _bl_norm_text(title) in {"tổng", "tong", "total"} or _bl_norm_text(title).startswith("tổng "):
            current_cells = None
            return

        sheet_label = _bl_sheet_label(current_sheet)
        quarter = (row.get(header_map.get("quarter") or "", "") or "").strip()
        detail_count = (row.get(header_map.get("count") or "", "") or "").strip()
        notes = (row.get(header_map.get("notes") or "", "") or "").strip()
        raw_priority = (row.get(header_map.get("priority") or "", "") or "").strip()
        raw_category = (row.get(header_map.get("category") or "", "") or "").strip()

        full_title = title
        if sheet_label and _bl_norm_text(sheet_label) not in _bl_norm_text(title):
            full_title = f"{sheet_label} - {title}"

        desc_parts = []
        if quarter:
            desc_parts.append(f"Kỳ: {quarter}")
        if detail_count:
            desc_parts.append(f"{detail_count} yêu cầu chi tiết")
        desc_parts.append(description)
        if notes and _bl_norm_text(notes) not in _bl_norm_text(description):
            desc_parts.append(f"Ghi chú: {notes}")

        final_description = ". ".join(part.rstrip(" .") for part in desc_parts if part).strip()
        dedupe_key = (_bl_norm_text(full_title), _bl_norm_text(final_description))
        if dedupe_key in seen:
            current_cells = None
            return
        seen.add(dedupe_key)

        priority = _bl_guess_priority(raw_priority or quarter, f"{title} {description} {sheet_label}")
        category = _bl_guess_category(title, description, raw_category)
        reasoning = f"Imported directly from structured sheet '{sheet_label or current_sheet or 'sheet'}'"
        if quarter:
            reasoning += f", kỳ {quarter}"

        items.append({
            "title": full_title[:200],
            "description": final_description,
            "priority": priority,
            "category": category,
            "ai_reasoning": reasoning[:300],
        })
        current_cells = None

    for raw_line in content.splitlines():
        line = raw_line.strip("\ufeff")
        stripped = line.strip()
        if not stripped:
            continue

        sheet_match = re.match(r"^===\s*Sheet:\s*(.*?)\s*===\s*$", stripped)
        if sheet_match:
            _flush_current_row()
            current_sheet = sheet_match.group(1).strip()
            headers = None
            header_map = None
            continue

        if headers is None:
            if "\t" not in line:
                continue

            cells = [cell.strip() for cell in line.split("\t")]
            mapped = _bl_map_structured_headers(cells)
            if mapped:
                headers = cells
                header_map = mapped
            continue

        if not header_map:
            continue

        if "\t" in line:
            _flush_current_row()
            current_cells = [cell.strip() for cell in line.split("\t")]
            continue

        if current_cells is None:
            continue

        desc_header = header_map.get("description")
        desc_idx = headers.index(desc_header) if desc_header in headers else len(headers) - 1
        if len(current_cells) <= desc_idx:
            current_cells += [""] * (desc_idx + 1 - len(current_cells))
        current_cells[desc_idx] = (current_cells[desc_idx] + "\n" + stripped).strip()

    _flush_current_row()
    return items


class BacklogMixin:
    # ---- source line 7127 (Handler._handle_changes_list) ----
    def _handle_changes_list(self):
        user = self._require_auth()
        if not user: return
        query = parse_qs(urlparse(self.path).query)
        workspace = _workspace_key_for_path((query.get("workspace") or [""])[0]) if query.get("workspace") else ""
        with _db() as c:
            self._backfill_missing_finalized_changes(c)
            self._normalize_legacy_change_scope_metadata(c)
            rows = c.execute("""
                WITH visible_sessions AS (
                    SELECT s.id, s.title, s.project, s.workspace
                    FROM backlog_sessions s
                    WHERE s.status != 'archived'
                                            AND (
                                                s.status = 'finalized'
                                                OR s.owner_id = ?
                                                OR ?
                                                OR EXISTS(SELECT 1 FROM changes ch WHERE ch.session_id = s.id)
                                            )
                )
                SELECT c.*, vs.title as session_title,
                       vs.project as session_project,
                       vs.workspace as session_workspace
                FROM changes c
                JOIN visible_sessions vs ON vs.id = c.session_id
                WHERE (? = '' OR COALESCE(vs.workspace, '') = ?)
                ORDER BY c.priority DESC, c.created_at DESC
            """, (user["id"], 1 if user["is_admin"] else 0, workspace, workspace)).fetchall()
        return self._send_json([dict(r) for r in rows])

    # ---- source line 7157 (Handler._handle_backlog_item_coverage) ----
    def _handle_backlog_item_coverage(self, path: str):
        """PUT /vault/backlog/item/{id}/coverage — save vault coverage JSON for an item."""
        user = self._require_auth()
        if not user: return
        parts = urlparse(path).path.rstrip("/").split("/")
        iid = parts[-2] if parts[-1] == "coverage" else None
        if not iid or not iid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        body = self._read_json_body()
        coverage_json = json.dumps(body.get("coverage") or {})
        with _db() as c:
            item = c.execute("""SELECT bi.id, bs.owner_id FROM backlog_items bi
                JOIN backlog_sessions bs ON bs.id=bi.session_id WHERE bi.id=?""", (int(iid),)).fetchone()
            if not item: return self._send_json({"error": "not found"}, 404)
            c.execute("UPDATE backlog_items SET status=COALESCE(status,'pending') WHERE id=?", (int(iid),))
            c.execute("UPDATE changes SET vault_coverage=? WHERE backlog_item_id=?", (coverage_json, int(iid)))
        # Also store on the item using a text column we piggy-back in description... actually store in changes table is enough
        return self._send_json({"ok": True})

    # ---- source line 7175 (Handler._normalize_scope_pair) ----
    def _normalize_scope_pair(self, project: str | None, workspace: str | None) -> tuple[str, str]:
        safe_project = _sanitize_project_key((project or "").strip())
        if safe_project == ".":
            safe_project = ""
        raw_workspace = (workspace or "").strip()
        safe_workspace = _workspace_key_for_path(raw_workspace) if raw_workspace else ""
        if safe_project and (_is_workspace_key(safe_project) or safe_project == safe_workspace):
            safe_project = ""
        if safe_project:
            derived_workspace = _workspace_key_for_path(safe_project)
            if derived_workspace not in ("", "."):
                safe_workspace = derived_workspace
        return safe_project, safe_workspace

    # ---- source line 7189 (Handler._normalize_legacy_change_scope_metadata) ----
    def _normalize_legacy_change_scope_metadata(self, c) -> dict:
        rows = c.execute("""
            SELECT bs.id AS session_id,
                   COALESCE(bs.project, '')   AS session_project,
                   COALESCE(bs.workspace, '') AS session_workspace,
                   ch.id AS change_id,
                   ch.change_key,
                   COALESCE(ch.project, '')   AS change_project,
                 COALESCE(ch.workspace, '') AS change_workspace
            FROM backlog_sessions bs
            JOIN changes ch ON ch.session_id = bs.id
             ORDER BY bs.id, ch.id
        """).fetchall()

        sessions: dict[int, dict] = {}
        changes: dict[int, dict] = {}

        for row in rows:
            session_id = int(row["session_id"])
            change_id = int(row["change_id"])
            session_state = sessions.setdefault(session_id, {
                "project": str(row["session_project"] or ""),
                "workspace": str(row["session_workspace"] or ""),
            })
            change_state = changes.setdefault(change_id, {
                "session_id": session_id,
                "project": str(row["change_project"] or ""),
                "workspace": str(row["change_workspace"] or ""),
            })

        normalized_sessions = 0
        normalized_changes = 0
        session_targets: dict[int, tuple[str, str]] = {}

        for session_id, session_state in sessions.items():
            target_project, target_workspace = self._normalize_scope_pair(
                session_state["project"],
                session_state["workspace"],
            )
            session_targets[session_id] = (target_project, target_workspace)
            if target_project != session_state["project"] or target_workspace != session_state["workspace"]:
                c.execute(
                    "UPDATE backlog_sessions SET project=?, workspace=? WHERE id=?",
                    (target_project, target_workspace, session_id),
                )
                normalized_sessions += 1

        for change_id, change_state in changes.items():
            target_project, target_workspace = self._normalize_scope_pair(
                change_state["project"],
                change_state["workspace"],
            )
            if not target_project:
                target_project, target_workspace = session_targets.get(
                    change_state["session_id"],
                    (target_project, target_workspace),
                )
            if target_project != change_state["project"] or target_workspace != change_state["workspace"]:
                c.execute(
                    "UPDATE changes SET project=?, workspace=?, updated_at=datetime('now') WHERE id=?",
                    (target_project, target_workspace, change_id),
                )
                normalized_changes += 1

        return {"sessions": normalized_sessions, "changes": normalized_changes}

    # ---- source line 7257 (Handler._handle_backlog_list) ----
    def _handle_backlog_list(self):
        user = self._require_auth()
        if not user: return
        with _db() as c:
            self._normalize_legacy_change_scope_metadata(c)
            rows = c.execute("""
                WITH session_visibility AS (
                    SELECT s.id, s.owner_id, s.title, s.status, s.source_file, s.project, s.workspace,
                           s.created_at, s.finalized_at, u.display_name as owner_name,
                           CASE
                               WHEN s.status = 'finalized' OR s.owner_id = ? OR ? THEN 1
                               ELSE 0
                           END as can_view_private
                    FROM backlog_sessions s
                    JOIN users u ON u.id = s.owner_id
                    WHERE s.status != 'archived'
                      AND (
                        s.status = 'finalized'
                        OR s.owner_id = ?
                        OR ?
                        OR EXISTS(SELECT 1 FROM changes ch WHERE ch.session_id = s.id)
                      )
                )
                SELECT sv.id, sv.owner_id, sv.title, sv.status, sv.source_file, sv.project, sv.workspace,
                       sv.created_at, sv.finalized_at, sv.owner_name,
                       COALESCE(SUM(CASE
                           WHEN sv.can_view_private = 1 AND i.id IS NOT NULL THEN 1
                           WHEN sv.can_view_private = 0 AND EXISTS(
                               SELECT 1 FROM changes ch WHERE ch.backlog_item_id = i.id
                           ) THEN 1
                           ELSE 0
                       END), 0) as item_count,
                       COUNT(i.id) as total_item_count,
                       COALESCE(SUM(CASE
                           WHEN EXISTS(SELECT 1 FROM changes ch WHERE ch.backlog_item_id = i.id) THEN 1
                           ELSE 0
                       END), 0) as public_item_count
                FROM session_visibility sv
                LEFT JOIN backlog_items i ON i.session_id = sv.id
                GROUP BY sv.id ORDER BY sv.created_at DESC
                    """, (
                        user["id"], 1 if user["is_admin"] else 0,
                        user["id"], 1 if user["is_admin"] else 0,
                    )).fetchall()
        return self._send_json([dict(r) for r in rows])

    # ---- source line 7303 (Handler._handle_backlog_get) ----
    def _handle_backlog_get(self, path: str):
        user = self._require_auth()
        if not user: return
        sid = urlparse(path).path.rstrip("/").split("/")[-1]
        if not sid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            self._normalize_legacy_change_scope_metadata(c)
            s = c.execute("SELECT * FROM backlog_sessions WHERE id=?", (int(sid),)).fetchone()
            if not s: return self._send_json({"error": "not found"}, 404)
            can_view_private = bool(
                s["status"] == "finalized"
                or s["owner_id"] == user["id"]
                or user["is_admin"]
            )
            has_public_items = c.execute(
                "SELECT 1 FROM changes WHERE session_id=? LIMIT 1",
                (int(sid),),
            ).fetchone() is not None
            if not can_view_private and not has_public_items:
                return self._send_json({"error": "forbidden"}, 403)
            items = c.execute("""
                SELECT bi.*,
                       COALESCE((SELECT ch.change_key
                                 FROM changes ch
                                 WHERE ch.backlog_item_id = bi.id
                                 ORDER BY ch.id DESC LIMIT 1), '') as change_key,
                       CASE WHEN EXISTS(
                         SELECT 1 FROM changes ch WHERE ch.backlog_item_id = bi.id
                       ) THEN 1 ELSE 0 END as is_public
                FROM backlog_items bi
                WHERE bi.session_id=?
                  AND (? OR EXISTS(
                    SELECT 1 FROM changes ch WHERE ch.backlog_item_id = bi.id
                  ))
                ORDER BY bi.sort_order, bi.id
            """, (int(sid), 1 if can_view_private else 0)).fetchall()
            session_data = dict(s)
            session_data["can_edit"] = bool(s["owner_id"] == user["id"] or user["is_admin"])
            session_data["public_only_view"] = bool(not can_view_private and s["status"] == "draft")
        return self._send_json({"session": session_data, "items": [dict(i) for i in items]})

    # ---- source line 7344 (Handler._handle_backlog_create) ----
    def _handle_backlog_create(self):
        try:
            user = self._require_auth()
            if not user: return
            try:
                body = self._read_json_body()
            except Exception as e:
                return self._send_json({"error": f"Invalid JSON body: {e}"}, 400)
            content    = (body.get("content") or "").strip()
            title      = (body.get("title") or "Backlog").strip()[:120]
            source_file= (body.get("source_file") or "").strip()[:120]
            workspace  = (body.get("workspace") or "").strip()
            _, workspace = self._normalize_scope_pair("", workspace)
            if not content:
                return self._send_json({"error": "content required"}, 400)
            if not workspace:
                return self._send_json({"error": "workspace required"}, 400)
            items_data = _bl_extract_structured_backlog_items(content)
            if not items_data:
                # Free-form backlog text still goes through AI summarization.
                max_content = 4000 if os.environ.get("GITHUB_TOKEN") and not os.environ.get("OPENAI_API_KEY") else 8000
                system = (
                    "Bạn là Product Manager phân tích backlog. Đọc danh sách yêu cầu và:\n"
                    "1. Tổng hợp, gộp yêu cầu trùng lặp\n"
                    "2. Viết lại title ngắn gọn (max 10 từ), description rõ ràng (2-3 câu)\n"
                    "3. Gợi ý priority: high/medium/low và category: feature/bug/improvement/tech-debt\n"
                    "4. ai_reasoning: 1 câu giải thích priority\n\n"
                    "Trả về JSON array CHÍNH XÁC, không có text nào khác:\n"
                    '[{"title":"...","description":"...","priority":"high","category":"feature","ai_reasoning":"..."}]'
                )
                try:
                    answer = _bl_call_llm(system, f"Backlog:\n\n{content[:max_content]}")
                    m = re.search(r'\[[\s\S]*\]', answer)
                    items_data = json.loads(m.group(0)) if m else []
                except Exception as e:
                    return self._send_json({"error": f"AI error: {e}"}, 500)
            with _db() as c:
                cur = c.execute("INSERT INTO backlog_sessions(owner_id,title,status,source_file,project,workspace) VALUES(?,?,?,?,?,?)",
                                (user["id"], title, "draft", source_file, "", workspace))
                sid = cur.lastrowid
                for i, item in enumerate(items_data):
                    c.execute("INSERT INTO backlog_items(session_id,title,description,priority,category,ai_reasoning,sort_order) VALUES(?,?,?,?,?,?,?)",
                              (sid, str(item.get("title",""))[:200], str(item.get("description","")),
                               item.get("priority","medium"), item.get("category","feature"),
                               str(item.get("ai_reasoning",""))[:300], i))
            return self._send_json({"session_id": sid, "item_count": len(items_data)})
        except Exception as e:
            import traceback; traceback.print_exc()
            return self._send_json({"error": f"Server error: {e}"}, 500)

    # ---- source line 7394 (Handler._handle_backlog_refine) ----
    def _handle_backlog_refine(self, path: str):
        """POST /vault/backlog/{id}/refine — AI groups items by DDD domain and phases by value."""
        try:
            user = self._require_auth()
            if not user: return
            parts = urlparse(path).path.rstrip("/").split("/")
            sid = parts[-2] if parts[-1] == "refine" else None
            if not sid or not sid.isdigit():
                return self._send_json({"error": "invalid id"}, 400)
            with _db() as c:
                s = c.execute("SELECT * FROM backlog_sessions WHERE id=?", (int(sid),)).fetchone()
                if not s: return self._send_json({"error": "not found"}, 404)
                items = c.execute(
                    "SELECT id,title,description,priority,category,vault_coverage FROM backlog_items "
                    "WHERE session_id=? ORDER BY sort_order", (int(sid),)
                ).fetchall()

            if not items:
                return self._send_json({"error": "Không có items để phân tích"}, 400)

            # Build items summary for AI (trim to avoid token limit)
            items_json = json.dumps([{
                "id": r["id"],
                "title": r["title"],
                "priority": r["priority"],
                "category": r["category"],
                "vault_pct": round(
                    sum(1 for v in json.loads(r["vault_coverage"] or "{}").values() if v.get("covered"))
                    / 7 * 100
                ) if r["vault_coverage"] else 0,
            } for r in items], ensure_ascii=False)

            system = (
                "Bạn là Solution Architect phân tích backlog theo Domain-Driven Design và Value Delivery.\n\n"
                "NHIỆM VỤ:\n"
                "1. PHÂN LOẠI theo Bounded Context (DDD): nhóm features liên quan vào cùng domain (vd: 'Quản lý đơn hàng', 'Thanh toán', 'Người dùng')\n"
                "2. PHASING theo giá trị và năng lực:\n"
                "   - phase=1: Core domain + quick win (vault_pct cao = Vault đã có năng lực → ít effort → ưu tiên)\n"
                "   - phase=2: Supporting domain, business value trung bình\n"
                "   - phase=3: Generic/infrastructure, có thể làm sau\n"
                "3. Điều chỉnh priority nếu phù hợp hơn\n\n"
                "QUY TẮC:\n"
                "- vault_pct cao (>50%) = Vault đã có specs/patterns → ít effort → tăng ưu tiên\n"
                "- Gom features cùng domain vào 1 phase nếu có thể\n"
                "- Phase 1 không quá 40% tổng items\n\n"
                "Trả về JSON array CHÍNH XÁC (không có text khác):\n"
                '[{"id":<number>,"domain":"<tên domain>","phase":<1|2|3>,"priority":"high|medium|low","phase_reason":"<lý do ngắn>"}]'
            )
            try:
                answer = _bl_call_llm(system, f"Backlog items cần phân tích:\n{items_json[:4000]}")
                m = re.search(r'\[[\s\S]*\]', answer)
                if not m:
                    return self._send_json({"error": "AI không trả về JSON hợp lệ"}, 500)
                refined = json.loads(m.group(0))
            except Exception as e:
                return self._send_json({"error": f"AI error: {e}"}, 500)

            # Apply updates to DB
            with _db() as c:
                for item in refined:
                    iid = item.get("id")
                    if not iid: continue
                    c.execute(
                        "UPDATE backlog_items SET domain=?,phase=?,priority=?,phase_reason=? WHERE id=? AND session_id=?",
                        (str(item.get("domain",""))[:80], int(item.get("phase",0)),
                         str(item.get("priority","medium")), str(item.get("phase_reason",""))[:200],
                         int(iid), int(sid))
                    )

            # Return updated items
            with _db() as c:
                updated = c.execute(
                    "SELECT * FROM backlog_items WHERE session_id=? ORDER BY phase, sort_order", (int(sid),)
                ).fetchall()
            return self._send_json({"ok": True, "items": [dict(r) for r in updated]})
        except Exception as e:
            import traceback; traceback.print_exc()
            return self._send_json({"error": f"Server error: {e}"}, 500)

    # ---- source line 9556 (Handler._ensure_change_for_backlog_item) ----
    def _ensure_change_for_backlog_item(self, c, session_row, item_row):
        sid = int(session_row["id"])
        item_id = int(item_row["id"])
        change_key = f"CHG-{sid:03d}-{int(item_row['sort_order'])+1:02d}"
        session_project, session_workspace = self._normalize_scope_pair(
            str(session_row["project"] or ""),
            str(session_row["workspace"] or ""),
        )
        existing = c.execute(
            "SELECT id, change_key FROM changes WHERE backlog_item_id=? OR change_key=? ORDER BY id LIMIT 1",
            (item_id, change_key)
        ).fetchone()
        update_fields = (
            item_id,
            sid,
            item_row["title"],
            item_row["description"],
            item_row["priority"],
            item_row["category"],
            session_project,
            session_workspace,
            item_row["vault_coverage"] or "{}",
        )
        if existing:
            c.execute("""
                UPDATE changes
                SET backlog_item_id=?, session_id=?, title=?, description=?, priority=?, category=?,
                    project=?, workspace=?, vault_coverage=?, updated_at=datetime('now')
                WHERE id=?
            """, (*update_fields, existing["id"]))
            return existing["change_key"] or change_key, False
        c.execute("""
            INSERT INTO changes(change_key,backlog_item_id,session_id,title,description,
                                priority,category,status,project,workspace,vault_coverage)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (change_key, item_id, sid, item_row["title"], item_row["description"],
                item_row["priority"], item_row["category"], "open",
            session_project, session_workspace, item_row["vault_coverage"] or "{}"))
        return change_key, True

    # ---- source line 9596 (Handler._backfill_missing_finalized_changes) ----
    def _backfill_missing_finalized_changes(self, c, session_id: int | None = None):
        params = []
        sql = """
            SELECT bi.*, bs.project, bs.workspace
            FROM backlog_items bi
            JOIN backlog_sessions bs ON bs.id = bi.session_id
            LEFT JOIN changes ch ON ch.backlog_item_id = bi.id
            WHERE bs.status = 'finalized' AND ch.id IS NULL
        """
        if session_id is not None:
            sql += " AND bs.id = ?"
            params.append(int(session_id))
        sql += " ORDER BY bi.session_id, bi.sort_order, bi.id"
        missing_rows = c.execute(sql, params).fetchall()
        created = 0
        for item in missing_rows:
            _, was_created = self._ensure_change_for_backlog_item(c, {
                "id": item["session_id"],
                "project": item["project"],
                "workspace": item["workspace"],
            }, item)
            if was_created:
                created += 1
        return created

    # ---- source line 9621 (Handler._handle_backlog_finalize) ----
    def _handle_backlog_finalize(self, path: str):
        user = self._require_auth()
        if not user: return
        parts = urlparse(path).path.rstrip("/").split("/")
        sid = parts[-2] if len(parts) >= 2 and parts[-1] == "finalize" else None
        if not sid or not sid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            s = c.execute("SELECT * FROM backlog_sessions WHERE id=?", (int(sid),)).fetchone()
            if not s: return self._send_json({"error": "not found"}, 404)
            if s["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            c.execute("UPDATE backlog_sessions SET status='finalized', finalized_at=datetime('now') WHERE id=?", (int(sid),))
            # Create a Change for each backlog item
            items = c.execute("SELECT * FROM backlog_items WHERE session_id=? ORDER BY sort_order", (int(sid),)).fetchall()
            created = 0
            for item in items:
                _, was_created = self._ensure_change_for_backlog_item(c, s, item)
                if was_created:
                    created += 1
        return self._send_json({"ok": True, "changes_created": created})

    # ---- source line 9642 (Handler._handle_backlog_item_finalize) ----
    def _handle_backlog_item_finalize(self, path: str):
        user = self._require_auth()
        if not user: return
        parts = urlparse(path).path.rstrip("/").split("/")
        iid = parts[-2] if len(parts) >= 2 and parts[-1] == "finalize" else None
        if not iid or not iid.isdigit():
            return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            item = c.execute("SELECT * FROM backlog_items WHERE id=?", (int(iid),)).fetchone()
            if not item:
                return self._send_json({"error": "not found"}, 404)
            session_row = c.execute(
                "SELECT id, owner_id, project, workspace FROM backlog_sessions WHERE id=?",
                (int(item["session_id"]),)
            ).fetchone()
            if not session_row:
                return self._send_json({"error": "session not found"}, 404)
            if session_row["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            change_key, created = self._ensure_change_for_backlog_item(c, session_row, item)
        return self._send_json({"ok": True, "change_key": change_key, "created": created})

    # ---- source line 9664 (Handler._rename_backlog_session) ----
    def _rename_backlog_session(self, sid: int, title: str, user):
        if not title:
            return self._send_json({"error": "title required"}, 400)
        with _db() as c:
            s = c.execute("SELECT owner_id FROM backlog_sessions WHERE id=?", (int(sid),)).fetchone()
            if not s: return self._send_json({"error": "not found"}, 404)
            if s["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            c.execute("UPDATE backlog_sessions SET title=? WHERE id=?", (title, int(sid)))
        return self._send_json({"ok": True, "title": title})

    # ---- source line 9675 (Handler._handle_backlog_rename) ----
    def _handle_backlog_rename(self):
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        try:
            sid = int(body.get("session_id", 0))
        except Exception:
            sid = 0
        if sid <= 0:
            return self._send_json({"error": "invalid id"}, 400)
        title = str(body.get("title") or "").strip()[:120]
        return self._rename_backlog_session(sid, title, user)

    # ---- source line 9688 (Handler._handle_backlog_update) ----
    def _handle_backlog_update(self, path: str):
        user = self._require_auth()
        if not user: return
        sid = urlparse(path).path.rstrip("/").split("/")[-1]
        if not sid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        body = self._read_json_body()
        title = str(body.get("title") or "").strip()[:120]
        return self._rename_backlog_session(int(sid), title, user)

    # ---- source line 10444 (Handler._handle_backlog_delete) ----
    def _handle_backlog_delete(self, path: str):
        user = self._require_auth()
        if not user: return
        sid = urlparse(path).path.rstrip("/").split("/")[-1]
        if not sid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            s = c.execute("SELECT owner_id FROM backlog_sessions WHERE id=?", (int(sid),)).fetchone()
            if not s: return self._send_json({"error": "not found"}, 404)
            if s["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            c.execute("UPDATE backlog_sessions SET status='archived' WHERE id=?", (int(sid),))
        return self._send_json({"ok": True})

    # ---- source line 10457 (Handler._handle_backlog_item_create) ----
    def _handle_backlog_item_create(self):
        user = self._require_auth()
        if not user: return
        body = self._read_json_body()
        sid = int(body.get("session_id", 0))
        with _db() as c:
            s = c.execute("SELECT owner_id, status, project, workspace FROM backlog_sessions WHERE id=?", (sid,)).fetchone()
            if not s: return self._send_json({"error": "session not found"}, 404)
            if s["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            max_ord = (c.execute("SELECT COALESCE(MAX(sort_order),0) FROM backlog_items WHERE session_id=?", (sid,)).fetchone()[0] or 0)
            cur = c.execute("INSERT INTO backlog_items(session_id,title,description,priority,category,sort_order) VALUES(?,?,?,?,?,?)",
                            (sid, str(body.get("title","Yêu cầu mới"))[:200], str(body.get("description","")),
                             body.get("priority","medium"), body.get("category","feature"), max_ord + 1))
            iid = cur.lastrowid
            response = {"id": iid, "published": False}
            new_item_row = c.execute("SELECT * FROM backlog_items WHERE id=?", (iid,)).fetchone()
            if s["status"] == "finalized":
                change_key, created = self._ensure_change_for_backlog_item(c, {
                    "id": sid,
                    "project": s["project"],
                    "workspace": s["workspace"],
                }, new_item_row)
                response.update({"published": True, "change_key": change_key, "created": created})
        export_backlog_item(dict(new_item_row))
        return self._send_json(response)

    # ---- source line 10483 (Handler._handle_backlog_item_update) ----
    def _handle_backlog_item_update(self, path: str):
        user = self._require_auth()
        if not user: return
        iid = urlparse(path).path.rstrip("/").split("/")[-1]
        if not iid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        body = self._read_json_body()
        with _db() as c:
            item = c.execute("""SELECT bi.*, bs.owner_id, bs.status AS session_status,
                                     bs.project AS session_project, bs.workspace AS session_workspace,
                                     CASE WHEN EXISTS(
                                         SELECT 1 FROM changes ch WHERE ch.backlog_item_id = bi.id
                                     ) THEN 1 ELSE 0 END AS has_change
                FROM backlog_items bi
                JOIN backlog_sessions bs ON bs.id=bi.session_id
                WHERE bi.id=?""", (int(iid),)).fetchone()
            if not item: return self._send_json({"error": "not found"}, 404)
            if item["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            updates = {k: body[k] for k in ("title","description","priority","category","status","sort_order") if k in body}
            if updates:
                set_sql = ", ".join(f"{k}=?" for k in updates)
                c.execute(f"UPDATE backlog_items SET {set_sql} WHERE id=?", (*updates.values(), int(iid)))
            updated_item_row = c.execute("SELECT * FROM backlog_items WHERE id=?", (int(iid),)).fetchone()
            if body.get("publish") or item["session_status"] == "finalized" or item["has_change"]:
                change_key, created = self._ensure_change_for_backlog_item(c, {
                    "id": updated_item_row["session_id"],
                    "project": item["session_project"],
                    "workspace": item["session_workspace"],
                }, updated_item_row)
                export_backlog_item(dict(updated_item_row))
                return self._send_json({"ok": True, "change_key": change_key, "created": created})
        export_backlog_item(dict(updated_item_row))
        return self._send_json({"ok": True})

    # ---- source line 10515 (Handler._handle_backlog_item_delete) ----
    def _handle_backlog_item_delete(self, path: str):
        user = self._require_auth()
        if not user: return
        iid = urlparse(path).path.rstrip("/").split("/")[-1]
        if not iid.isdigit(): return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            item = c.execute("""SELECT bi.id, bs.owner_id FROM backlog_items bi
                JOIN backlog_sessions bs ON bs.id=bi.session_id WHERE bi.id=?""", (int(iid),)).fetchone()
            if not item: return self._send_json({"error": "not found"}, 404)
            if item["owner_id"] != user["id"] and not user["is_admin"]:
                return self._send_json({"error": "forbidden"}, 403)
            c.execute("DELETE FROM backlog_items WHERE id=?", (int(iid),))
        delete_backlog_item_export(int(iid))
        return self._send_json({"ok": True})

