"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

import json
import os
from urllib.parse import urlparse

from .db import _db
from .llm import _bl_call_llm_ex

# ---- source line 3033 (_elicit_sessions_for_change) ----
def _elicit_sessions_for_change(change_key: str) -> list:
    """Return all elicit conversations linked to a change_key, with message counts."""
    with _db() as c:
        rows = c.execute("""
            SELECT cv.id, cv.title, cv.role, cv.mode, cv.created_at, cv.updated_at,
                   u.display_name, u.username,
                   COUNT(m.id)                                          AS msg_count_total,
                   SUM(CASE WHEN m.role='user'      THEN 1 ELSE 0 END) AS msg_count_user,
                   SUM(CASE WHEN m.role='assistant' THEN 1 ELSE 0 END) AS msg_count_ai
            FROM conversations cv
            JOIN users u ON cv.user_id = u.id
            LEFT JOIN messages m ON m.conversation_id = cv.id
            WHERE cv.change_key = ? AND cv.mode = 'elicit'
            GROUP BY cv.id
            ORDER BY cv.updated_at DESC
        """, (change_key,)).fetchall()
    return [
        {
            "id": r["id"], "title": r["title"], "role": r["role"],
            "user": r["display_name"] or r["username"],
            "msg_count":      r["msg_count_user"] or 0,   # questions only — matches synthesis
            "msg_count_ai":   r["msg_count_ai"]   or 0,
            "msg_count_total":r["msg_count_total"] or 0,
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
        for r in rows
    ]


# ---- source line 3062 (_conv_title_from) ----
def _conv_title_from(question: str) -> str:
    """Generate a short conversation title from the first question."""
    q = question.strip()
    # Strip Vietnamese greeting words
    for greet in ("xin chào", "chào", "hello", "hi", "hey"):
        if q.lower().startswith(greet):
            q = q[len(greet):].strip("!, ")
    title = q[:60].strip()
    return title or "New Conversation"


# ---- source line 3073 (_get_or_create_conv) ----
def _get_or_create_conv(user_id: int, conv_id: int | None,
                        project: str, role: str, mode: str,
                        question: str, change_key: str = '') -> int:
    """Return existing conv_id or create a new one. Updates updated_at."""
    with _db() as c:
        if conv_id:
            row = c.execute(
                "SELECT id FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user_id)
            ).fetchone()
            if row:
                # Update change_key if newly provided for an existing conversation
                if change_key:
                    c.execute("UPDATE conversations SET updated_at=datetime('now'), change_key=? WHERE id=?",
                              (change_key, conv_id))
                else:
                    c.execute("UPDATE conversations SET updated_at=datetime('now') WHERE id=?",
                              (conv_id,))
                return conv_id
        # Create new
        title = _conv_title_from(question)
        cur = c.execute(
            "INSERT INTO conversations(user_id,title,project,role,mode,change_key) VALUES(?,?,?,?,?,?)",
            (user_id, title, project, role, mode, change_key or '')
        )
        return cur.lastrowid


# ---- source line 3101 (_save_messages) ----
def _save_messages(conv_id: int, question: str, answer: str,
                   sources: list, provider: str, model: str,
                   usage: dict) -> None:
    """Persist user question + assistant answer to messages table."""
    with _db() as c:
        c.execute(
            "INSERT INTO messages(conversation_id,role,content) VALUES(?,?,?)",
            (conv_id, "user", question)
        )
        c.execute(
            "INSERT INTO messages(conversation_id,role,content,"
            "sources_json,provider,model,tokens_in,tokens_out,tokens_total)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (conv_id, "assistant", answer,
             json.dumps(sources, ensure_ascii=False),
             provider, model,
             usage.get("in", 0), usage.get("out", 0), usage.get("total", 0))
        )


class ConversationsMixin:
    # ---- source line 4505 (Handler._handle_elicit_sessions) ----
    def _handle_elicit_sessions(self, change_key: str):
        user = self._require_auth()
        if not user: return
        sessions = _elicit_sessions_for_change(change_key)
        self._send_json({"sessions": sessions, "change_key": change_key})

    # ---- source line 4512 (Handler._handle_elicit_synthesize) ----
    def _handle_elicit_synthesize(self, change_key: str):
        user = self._require_auth()
        if not user: return

        sessions = _elicit_sessions_for_change(change_key)
        if not sessions:
            return self._send_json({"error": "Chưa có session khai thác nào cho change này."}, 404)

        # ── Build structured interview blocks ─────────────────────────────────
        # Each block = one interview session with full metadata + Q&A transcript
        interview_blocks: list[str] = []
        session_meta: list[str] = []   # for the header table

        with _db() as c:
            for idx, s in enumerate(sessions, 1):
                msgs = c.execute(
                    "SELECT role, content, created_at FROM messages "
                    "WHERE conversation_id=? ORDER BY id ASC",
                    (s["id"],)
                ).fetchall()
                if not msgs:
                    continue

                interview_date  = (s.get("created_at") or s.get("updated_at") or "")[:10]
                updated_date    = (s.get("updated_at") or "")[:10]
                interviewer     = s["user"]
                role_label      = s["role"]
                n_qa            = sum(1 for m in msgs if m["role"] == "user")

                session_meta.append(
                    f"| PV{idx:02d} | {interviewer} | {role_label} | {interview_date} | {updated_date} | {n_qa} câu |"
                )

                # Build the Q&A transcript, keeping all messages (truncate long ones)
                transcript_lines = []
                for m in msgs:
                    if m["role"] == "assistant":
                        label = "🤖 AI"
                        content = m["content"][:800].strip()
                    else:
                        label = f"👤 {interviewer} ({role_label})"
                        content = m["content"][:600].strip()
                    transcript_lines.append(f"{label}: {content}")

                block = (
                    f"═══ PHỎNG VẤN PV{idx:02d} ═══\n"
                    f"Người trả lời : {interviewer}  |  Vai trò: {role_label}\n"
                    f"Ngày bắt đầu  : {interview_date}  |  Cập nhật lần cuối: {updated_date}\n"
                    f"Số lượt hỏi   : {n_qa} câu hỏi\n"
                    f"{'─'*60}\n"
                    + "\n".join(transcript_lines)
                )
                interview_blocks.append(block)

        if not interview_blocks:
            return self._send_json({"error": "Không có nội dung hội thoại để tổng hợp."}, 404)

        n_sessions      = len(interview_blocks)
        all_users       = [s["user"] for s in sessions if s.get("msg_count", 0) > 0]
        roles_set       = sorted({s["role"] for s in sessions})
        roles_str       = ", ".join(roles_set)
        interviews_text = "\n\n".join(interview_blocks)
        meta_table      = "\n".join(session_meta)

        # ── Synthesis prompt ──────────────────────────────────────────────────
        synthesis_prompt = f"""Dưới đây là {n_sessions} cuộc phỏng vấn khai thác yêu cầu cho Change **{change_key}**.
Mỗi phỏng vấn có mã PV01, PV02... — hãy trích dẫn mã này khi nhắc đến nguồn.

DANH SÁCH PHỎNG VẤN:
| Mã | Người trả lời | Vai trò | Ngày PV | Cập nhật | Số câu |
|----|--------------|---------|---------|----------|--------|
{meta_table}

---

Hãy sinh tài liệu tổng hợp theo mẫu sau. Với mỗi thông tin, GHI RÕ nguồn [PVxx] ngay sau.
Nếu có xung đột giữa các phỏng vấn, bôi đậm ⚡ và ghi rõ ai nói gì.

---

# Tài liệu tổng hợp phỏng vấn khai thác yêu cầu

**Change:** `{change_key}`
**Tổng hợp từ:** {n_sessions} phỏng vấn  ·  **Vai trò tham gia:** {roles_str}
**Người tham gia:** {', '.join(all_users)}

---

## 📋 Danh sách phỏng vấn

| Mã | Người trả lời | Vai trò | Ngày phỏng vấn | Trạng thái |
|----|--------------|---------|----------------|-----------|
{meta_table.replace('| PV', '| PV').replace(' câu |', ' câu | Đã ghi nhận |')}

---

## 🔍 Phân tích theo 7 chiều

### 🎯 GOAL — Mục tiêu nghiệp vụ

> Điền: tổng hợp mục tiêu từ các phỏng vấn, trích nguồn [PVxx].
> Ví dụ: "Cần thêm trang báo cáo doanh thu theo ngày [PV01, PV02]. SA bổ sung thêm nhu cầu export CSV [PV03]."
> Nếu xung đột: "⚡ PV01 (BA) muốn A, nhưng PV02 (PO) muốn B."

### 👥 ACTORS — Người dùng & Hệ thống

> Điền: liệt kê actors, ai đề cập ở phỏng vấn nào [PVxx].

### 🔄 FLOW — Luồng xử lý chính

> Điền: mô tả luồng, trích nguồn [PVxx] từng bước.
> Nếu các vai trò mô tả luồng khác nhau: ghi rõ từng phiên bản.

### 📏 RULES — Quy tắc nghiệp vụ & Validation

> Điền: liệt kê từng rule, trích nguồn [PVxx].
> Format: "- [Rule] nội dung rule [PVxx]"

### ⚠️ EDGE_CASES — Trường hợp ngoại lệ

> Điền: liệt kê edge cases, ai đề cập [PVxx].

### 💥 IMPACT — Tác động hệ thống

> Điền: modules/tính năng bị ảnh hưởng, nguồn [PVxx].

### 🛡 QUALITY — Yêu cầu phi chức năng

> Điền: hiệu năng, bảo mật, UX đã đề cập, nguồn [PVxx].

---

## ⚡ Xung đột & Điểm cần làm rõ

> Liệt kê TẤT CẢ các điểm mà các phỏng vấn viên có ý kiến KHÁC NHAU hoặc MÂU THUẪN.
> Format:
> | # | Nội dung xung đột | Quan điểm 1 | Quan điểm 2 | Cần hỏi thêm |
> |---|-----------------|-------------|-------------|--------------|
> | 1 | Chủ đề xung đột | [PVxx] Tên: ý kiến... | [PVyy] Tên: ý kiến... | Câu hỏi cần làm rõ |
>
> Nếu KHÔNG có xung đột, ghi: "✅ Không phát hiện xung đột đáng kể giữa các phỏng vấn."

---

## ✅ Quyết định đã đồng thuận

> Liệt kê những điểm TẤT CẢ đều đồng ý, kèm nguồn [PVxx].
> | # | Quyết định | Nguồn đồng thuận |
> |---|-----------|-----------------|

---

## 📝 Requirements gợi ý (từ phỏng vấn)

> Dựa trên phỏng vấn, đề xuất requirements theo RFC-2119. Ghi nguồn [PVxx].
> - **[MUST]** Hệ thống PHẢI... [PVxx]
> - **[SHOULD]** Hệ thống NÊN... [PVxx]
> - **[MAY]** Hệ thống CÓ THỂ... [PVxx]

---

## 🚦 Đánh giá độ phủ 7 chiều

| Chiều | Trạng thái | Nguồn | Ghi chú |
|-------|-----------|-------|---------|
| 🎯 GOAL | ✅/⚠️/❌ | [PVxx] | |
| 👥 ACTORS | ✅/⚠️/❌ | [PVxx] | |
| 🔄 FLOW | ✅/⚠️/❌ | [PVxx] | |
| 📏 RULES | ✅/⚠️/❌ | [PVxx] | |
| ⚠️ EDGE_CASES | ✅/⚠️/❌ | [PVxx] | |
| 💥 IMPACT | ✅/⚠️/❌ | [PVxx] | |
| 🛡 QUALITY | ✅/⚠️/❌ | [PVxx] | |

> ✅ Đã khai thác đầy đủ  ·  ⚠️ Khai thác một phần  ·  ❌ Chưa khai thác

===

NỘI DUNG CÁC CUỘC PHỎNG VẤN:

{interviews_text}
"""

        system_prompt = (
            "Bạn là Business Analyst chuyên nghiệp với 10 năm kinh nghiệm phân tích yêu cầu. "
            "Nhiệm vụ: tổng hợp nhiều cuộc phỏng vấn khai thác yêu cầu thành một tài liệu chuẩn, "
            "trích dẫn nguồn rõ ràng (mã PVxx), phát hiện xung đột giữa các stakeholder, "
            "và đánh giá độ phủ 7 chiều. Không bỏ sót bất kỳ thông tin quan trọng nào."
        )

        try:
            synthesis, usage = _bl_call_llm_ex(system_prompt, synthesis_prompt, max_tokens=4000)

            # ── Deduct tokens ─────────────────────────────────────────────────
            if not user["is_admin"]:
                with _db() as c:
                    c.execute("UPDATE users SET token_used=token_used+? WHERE id=?",
                              (usage.get("total", 0), user["id"]))

            # ── Auto-save synthesis to orch_artifacts ─────────────────────────
            db_id = None
            version = 1
            try:
                with _db() as c:
                    ch_row = c.execute(
                        "SELECT backlog_item_id FROM changes WHERE change_key=?",
                        (change_key,)
                    ).fetchone()
                    item_id = ch_row["backlog_item_id"] if ch_row else None

                    run_id = c.execute(
                        "INSERT INTO orch_runs(backlog_item_id,system) VALUES(?,?)",
                        (item_id, "elicit")
                    ).lastrowid

                    prev_count = c.execute(
                        """SELECT COUNT(*) FROM orch_artifacts oa
                           JOIN orch_runs r ON oa.run_id=r.id
                           WHERE r.backlog_item_id = ?
                             AND oa.artifact_key='elicit-synthesis'""",
                        (item_id,)
                    ).fetchone()[0]
                    version = prev_count + 1

                    db_id = c.execute(
                        "INSERT INTO orch_artifacts(run_id,artifact_key,name,icon,output_type,content,version)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (run_id, "elicit-synthesis", "Tổng hợp phỏng vấn",
                         "📋", "Markdown (.md)", synthesis, version)
                    ).lastrowid
            except Exception as _e_save:
                print(f"[Vault] WARN elicit save: {_e_save}")

            self._send_json({
                "synthesis": synthesis,
                "sessions_count": n_sessions,
                "change_key": change_key,
                "usage": usage,
                "db_id": db_id,
                "version": version,
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ---- source line 4756 (Handler._handle_elicit_syntheses) ----
    def _handle_elicit_syntheses(self, change_key: str):
        user = self._require_auth()
        if not user: return
        with _db() as c:
            rows = c.execute(
                """SELECT oa.id, oa.version, oa.created_at,
                          substr(oa.content,1,200) AS preview
                   FROM orch_artifacts oa
                   JOIN orch_runs r ON oa.run_id = r.id
                   JOIN changes ch ON ch.backlog_item_id = r.backlog_item_id
                   WHERE ch.change_key=?
                     AND oa.artifact_key='elicit-synthesis'
                     AND r.system='elicit'
                   ORDER BY oa.created_at DESC
                   LIMIT 20""",
                (change_key,)
            ).fetchall()
        self._send_json({
            "syntheses": [{"id":r["id"],"version":r["version"],
                           "created_at":r["created_at"],"preview":r["preview"]}
                          for r in rows],
            "change_key": change_key,
        })

    # ---- source line 4781 (Handler._handle_elicit_synthesis_get) ----
    def _handle_elicit_synthesis_get(self, db_id: str):
        user = self._require_auth()
        if not user: return
        with _db() as c:
            row = c.execute(
                "SELECT id,version,content,created_at FROM orch_artifacts WHERE id=? AND artifact_key='elicit-synthesis'",
                (int(db_id),)
            ).fetchone()
        if not row:
            return self._send_json({"error": "Not found"}, 404)
        self._send_json({"id":row["id"],"version":row["version"],
                         "content":row["content"],"created_at":row["created_at"]})

    # ---- source line 5067 (Handler._handle_vault_conv_list) ----
    def _handle_vault_conv_list(self):
        """GET /vault/conversations?limit=40&offset=0"""
        user = self._require_auth()
        if not user:
            return
        qs = dict(x.split("=", 1) for x in (urlparse(self.path).query or "").split("&") if "=" in x)
        limit  = min(int(qs.get("limit",  "40")), 100)
        offset = int(qs.get("offset", "0"))
        with _db() as c:
            rows = c.execute(
                "SELECT id, title, project, role, mode, change_key, created_at, updated_at "
                "FROM conversations WHERE user_id=? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (user["id"], limit, offset)
            ).fetchall()
        return self._send_json({"conversations": [dict(r) for r in rows]})

    # ---- source line 5084 (Handler._handle_vault_conv_get) ----
    def _handle_vault_conv_get(self):
        """GET /vault/conversations/:id  → {conversation, messages}"""
        user = self._require_auth()
        if not user:
            return
        req_path = urlparse(self.path).path
        try:
            conv_id = int(req_path.split("/")[-1])
        except ValueError:
            return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            conv = c.execute(
                "SELECT id,title,project,role,mode,change_key,created_at,updated_at "
                "FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user["id"])
            ).fetchone()
            if not conv:
                return self._send_json({"error": "not found"}, 404)
            msgs = c.execute(
                "SELECT id,role,content,sources_json,provider,model,"
                "tokens_in,tokens_out,tokens_total,created_at "
                "FROM messages WHERE conversation_id=? ORDER BY id",
                (conv_id,)
            ).fetchall()
        msg_list = []
        for m in msgs:
            d = dict(m)
            if d.get("sources_json"):
                try:
                    d["sources"] = json.loads(d["sources_json"])
                except Exception:
                    d["sources"] = []
            del d["sources_json"]
            msg_list.append(d)
        return self._send_json({"conversation": dict(conv), "messages": msg_list})

    # ---- source line 5120 (Handler._handle_vault_conv_delete) ----
    def _handle_vault_conv_delete(self):
        """DELETE /vault/conversations/:id"""
        user = self._require_auth()
        if not user:
            return
        req_path = urlparse(self.path).path
        try:
            conv_id = int(req_path.split("/")[-1])
        except ValueError:
            return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            row = c.execute(
                "SELECT id FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user["id"])
            ).fetchone()
            if not row:
                return self._send_json({"error": "not found"}, 404)
            c.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        return self._send_json({"ok": True})

    # ---- source line 5141 (Handler._handle_vault_conv_summarize) ----
    def _handle_vault_conv_summarize(self):
        """POST /vault/conversations/:id/summarize → {summary}
        Calls LLM to summarize the conversation (no RAG needed)."""
        import urllib.request as _ur, urllib.error as _ue
        user = self._require_auth()
        if not user:
            return
        req_path = urlparse(self.path).path
        try:
            conv_id = int(req_path.split("/")[-2])
        except (ValueError, IndexError):
            return self._send_json({"error": "invalid id"}, 400)
        with _db() as c:
            conv = c.execute(
                "SELECT id,title,role FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user["id"])
            ).fetchone()
            if not conv:
                return self._send_json({"error": "not found"}, 404)
            msgs = c.execute(
                "SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id",
                (conv_id,)
            ).fetchall()
        if not msgs:
            return self._send_json({"summary": "Không có nội dung để tóm tắt."})

        # Build transcript (last 20 messages to save tokens)
        recent = list(msgs)[-20:]
        transcript = "\n\n".join(
            f"**{'User' if m['role']=='user' else 'AI'}:** {m['content'][:400]}"
            for m in recent
        )
        system = (
            "Bạn là trợ lý tóm tắt. Đọc đoạn hội thoại dưới đây và trả lời bằng tiếng Việt "
            "theo đúng format:\n\n"
            "**Cuộc hội thoại này đã cover:**\n- [điểm 1]\n- [điểm 2]\n...\n\n"
            "**Công việc dở dang / câu hỏi cuối:**\n[mô tả ngắn]\n\n"
            "**Gợi ý tiếp tục:**\n[câu gợi ý ngắn cho user]\n\n"
            "Tóm tắt ngắn gọn, tối đa 200 từ."
        )
        user_msg = f"Tóm tắt cuộc hội thoại:\n\n{transcript}"

        forced   = os.environ.get("VAULT_CHAT_PROVIDER", "").lower()
        qwen_key = os.environ.get("DASHSCOPE_API_KEY", "")
        anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
        gh_key   = os.environ.get("GITHUB_TOKEN", "")
        oai_key  = os.environ.get("OPENAI_API_KEY", "")
        model_ov = os.environ.get("VAULT_CHAT_MODEL", "")

        if forced == "qwen" or (not forced and qwen_key and not anth_key and not gh_key):
            provider = "qwen"
        elif forced == "anthropic" or (not forced and anth_key):
            provider = "anthropic"
        elif forced == "github" or (not forced and gh_key and not oai_key):
            provider = "github"
        elif forced == "openai" or (not forced and oai_key):
            provider = "openai"
        else:
            return self._send_json({"summary": "Không có LLM API để tóm tắt."})

        try:
            summary = ""
            if provider == "qwen":
                url = os.environ.get("DASHSCOPE_BASE_URL",
                    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1") + "/chat/completions"
                payload = json.dumps({"model": model_ov or "qwen-plus", "max_tokens": 600,
                    "messages": [{"role":"system","content":system},
                                 {"role":"user","content":user_msg}]}).encode()
                req = _ur.Request(url, data=payload,
                    headers={"Authorization": f"Bearer {qwen_key}", "Content-Type": "application/json"})
                with _ur.urlopen(req, timeout=30) as r:
                    summary = json.loads(r.read())["choices"][0]["message"]["content"]
            elif provider == "anthropic":
                import anthropic as _ant
                client = _ant.Anthropic(api_key=anth_key)
                resp = client.messages.create(model=model_ov or "claude-haiku-4-5-20251001",
                    max_tokens=600, system=system,
                    messages=[{"role":"user","content":user_msg}])
                summary = resp.content[0].text
            elif provider in ("github", "openai"):
                url = ("https://models.inference.ai.azure.com" if provider=="github"
                       else os.environ.get("OPENAI_BASE_URL","https://api.openai.com/v1")
                       ) + "/chat/completions"
                key = gh_key if provider == "github" else oai_key
                model = model_ov or "gpt-4o-mini"
                payload = json.dumps({"model": model, "max_completion_tokens": 600,
                    "messages": [{"role":"system","content":system},
                                 {"role":"user","content":user_msg}]}).encode()
                req = _ur.Request(url, data=payload,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                with _ur.urlopen(req, timeout=30) as r:
                    summary = json.loads(r.read())["choices"][0]["message"]["content"]
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

        return self._send_json({"summary": summary})

