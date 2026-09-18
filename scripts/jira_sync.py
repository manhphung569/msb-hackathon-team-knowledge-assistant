"""Đồng bộ issue từ Jira Cloud (REST API v3) thành note .md trong normalized/_jira-notes/ của
1 tenant graphrag-engine — dựng cho MSB AI Hackathon 2026 (tenant demo/teamassistant, xem
tenants/demo/README.md). Nguồn Jira Cloud ở đây LÀ giả lập/demo, KHÔNG phải Jira thật của MSB.

Ghi trực tiếp vào normalized/, KHÔNG qua artifacts/ và normalize-engine's pipeline.py — đúng
"ngoại lệ note" đã có sẵn trong repo (xem graphrag-engine/src/graphrag/capture.py
_write_captured_note): pipeline.py's converter loop chỉ xử lý file cục bộ 1:1, không có khái
niệm fetch từ API, nên fetch-rồi-ghi-thẳng-.md là cách khớp đúng pattern hiện có, không cần
sửa pipeline.py.

Auth: Jira Cloud dùng HTTP Basic (email + API token), theo đúng tài liệu chính thức Atlassian
(https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/). Token đọc từ
biến môi trường JIRA_API_TOKEN — không bao giờ truyền qua command line (lộ trong shell
history/process list).

Phụ thuộc: chỉ cần thêm PyYAML ngoài stdlib (build frontmatter an toàn qua yaml.safe_dump thay
vì ghép f-string thô — status/assignee/summary tới từ Jira thật, có thể chứa `:` phá cú pháp
YAML nếu ghép tay). graphrag-engine/.venv đã có sẵn PyYAML — chạy bằng venv đó cho tiện, không
cần cài riêng.

Config đọc tự động từ scripts/.env (JIRA_BASE_URL/JIRA_API_TOKEN/JIRA_EMAIL — KHÔNG commit,
đã gitignore, xem scripts/.env.example cho khuôn mẫu) — mirror đúng minimal-loader pattern đã
dùng ở graphrag-engine/config.py và workbench/config.py. --jira-base-url/--jira-email vẫn nhận
qua CLI nếu muốn override, nhưng token LUÔN LUÔN chỉ đọc từ env/.env, không nhận qua CLI (lộ
trong shell history/process list).

Cách dùng (đã điền scripts/.env thì chỉ cần):
    graphrag-engine/.venv/Scripts/python.exe scripts/jira_sync.py \\
        --tenant-root tenants/demo/teamassistant --jql "project = TKA"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Minimal .env loader (KHÔNG cài python-dotenv — cùng pattern graphrag-engine/config.py và
# workbench/server/config.py đã dùng). os.environ đã set sẵn (vd export tay) luôn thắng .env.
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

_FIELDS = "summary,status,assignee,priority,description,updated,issuetype,attachment"

# normalize-engine tự convert được đúng các đuôi này (xem normalize-engine/src/normalize/
# pipeline.py _CONVERTERS) — file khác đuôi vẫn tải về (để không mất dữ liệu) nhưng KHÔNG tự
# vào index cho tới khi có converter thủ công, xem CLAUDE.md "Định dạng file & tri thức chưa
# đọc được".
_CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".msg", ".eml", ".html", ".htm"}


class JiraSyncError(RuntimeError):
    pass


def _flatten_adf(node) -> str:
    """Atlassian Document Format (description field) -> plain text. Chỉ cần đủ để hiển thị
    trong note, không cần tái tạo format — duyệt đệ quy content[].content[].text, nối đoạn văn
    bằng dòng trống. Không dùng lib ADF riêng vì input chỉ có vài kiểu node đơn giản (paragraph/
    text/hardBreak) cho issue demo tự tạo."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        parts = [_flatten_adf(child) for child in node.get("content", [])]
        return "\n".join(p for p in parts if p)
    if isinstance(node, list):
        return "\n\n".join(p for p in (_flatten_adf(n) for n in node) if p)
    return ""


def _fetch_issues(base_url: str, email: str, token: str, jql: str) -> list[dict]:
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    query = urllib.parse.urlencode({"jql": jql, "fields": _FIELDS, "maxResults": 100})
    # /rest/api/3/search bị Atlassian remove (HTTP 410, xác nhận thật lúc chạy 2026-09-17) —
    # endpoint thay thế /rest/api/3/search/jql, cùng shape request/response (GET + query params),
    # chỉ đổi path. Xem https://developer.atlassian.com/changelog/#CHANGE-2046.
    url = f"{base_url.rstrip('/')}/rest/api/3/search/jql?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise JiraSyncError(f"Jira trả lỗi HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise JiraSyncError(f"Không kết nối được tới {base_url}: {e}") from e
    return data.get("issues", [])


def _download_attachments(
    issue: dict, tenant_root: Path, email: str, token: str
) -> tuple[list[str], list[str]]:
    """Tải file đính kèm của 1 issue vào artifacts/_jira-attachments/<KEY>/<filename> — ĐÚNG
    quy ước artifacts/ đã có, để normalize-engine's pipeline.py tự convert bằng converter sẵn
    có (không viết converter riêng ở đây — tái dùng 100% pdf/docx/pptx/xlsx đã có). Trả về
    (danh sách file convert được, danh sách file KHÔNG convert được — vẫn tải về nhưng cần xử
    lý thủ công, xem CLAUDE.md)."""
    attachments = (issue.get("fields") or {}).get("attachment") or []
    if not attachments:
        return [], []

    key = issue["key"]
    out_dir = tenant_root / "artifacts" / "_jira-attachments" / key
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    convertible: list[str] = []
    unconvertible: list[str] = []

    for att in attachments:
        filename = att.get("filename") or ""
        content_url = att.get("content") or ""
        if not filename or not content_url:
            continue
        ext = Path(filename).suffix.lower()
        req = urllib.request.Request(
            content_url, headers={"Authorization": f"Basic {auth}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print(f"    [CẢNH BÁO] Không tải được {filename} ({key}): {e}", file=sys.stderr)
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        # Tên file y hệt Jira đặt — có thể trùng tên giữa nhiều lần đính kèm, chấp nhận ghi đè
        # (idempotent, giống cách sync() ghi đè note issue mỗi lần chạy lại).
        (out_dir / filename).write_bytes(data)
        rel = f"artifacts/_jira-attachments/{key}/{filename}"
        if ext in _CONVERTIBLE_EXTENSIONS:
            convertible.append(rel)
        else:
            unconvertible.append(rel)

    return convertible, unconvertible


def _issue_to_note(issue: dict, project: str) -> tuple[str, str]:
    """Trả về (issue_key, nội dung file .md). Frontmatter build qua yaml.safe_dump — KHÔNG
    hand-format f-string trực tiếp vào khối YAML: summary/assignee/status tới từ Jira thật (dù
    là site demo), có thể chứa `:`/`#`/newline phá cú pháp YAML nếu ghép chuỗi thô, khiến
    ingestion/loader.py's _split_frontmatter() âm thầm rớt về frontmatter={} (yaml.safe_load
    bọc try/except) và mất hết status/assignee/priority/doc_type — không crash nhưng mất dữ
    liệu, khó nhận ra lúc demo."""
    key = issue["key"]
    fields = issue.get("fields", {})
    summary = fields.get("summary", "").strip()
    status = (fields.get("status") or {}).get("name", "Unknown")
    assignee = (fields.get("assignee") or {}).get("displayName", "Chưa gán")
    priority = (fields.get("priority") or {}).get("name", "Unknown")
    updated = str(fields.get("updated", ""))[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    description = _flatten_adf(fields.get("description")).strip() or "(không có mô tả)"

    meta = {
        "source": "jira-api",
        "issue_key": key,
        "status": status,
        "assignee": assignee,
        "priority": priority,
        "date": updated,
        "project": project,
        "doc_type": "backlog",
        "sensitivity": "internal",
        "reliability": "cao",
        "basis": "Đồng bộ trực tiếp qua Jira REST API (scripts/jira_sync.py), không phải nghe kể lại.",
    }
    frontmatter_yaml = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)

    content = (
        f"---\n{frontmatter_yaml}---\n\n"
        f"## {key} — {summary}\n"
        f"**Status:** {status} | **Priority:** {priority} | **Assignee:** {assignee}\n\n"
        "## Mô tả\n"
        f"{description}\n"
    )
    return key, content


def sync(
    tenant_root: Path, base_url: str, email: str, token: str, jql: str,
    download_attachments: bool = True,
) -> int:
    project = tenant_root.name
    out_dir = tenant_root / "normalized" / "_jira-notes"
    out_dir.mkdir(parents=True, exist_ok=True)

    issues = _fetch_issues(base_url, email, token, jql)
    if not issues:
        print("Không tìm thấy issue nào khớp JQL — kiểm tra lại --jql hoặc quyền truy cập token.")
        return 0

    all_convertible: list[str] = []
    all_unconvertible: list[str] = []
    for issue in issues:
        key, content = _issue_to_note(issue, project)
        path = out_dir / f"{key}.md"
        path.write_text(content, encoding="utf-8")
        print(f"  {key} -> {path.relative_to(tenant_root.parent.parent)}")

        if download_attachments:
            convertible, unconvertible = _download_attachments(issue, tenant_root, email, token)
            for rel in convertible:
                print(f"    + đính kèm (sẽ tự convert): {rel}")
            for rel in unconvertible:
                print(f"    + đính kèm (CẦN xử lý thủ công, đuôi chưa hỗ trợ): {rel}")
            all_convertible += convertible
            all_unconvertible += unconvertible

    print(f"\nĐã đồng bộ {len(issues)} issue vào {out_dir}")
    if all_convertible:
        print(
            f"Đã tải {len(all_convertible)} file đính kèm convert được — chạy tiếp:\n"
            f"  normalize run --root {tenant_root}\n"
            "để sinh bản .md tương ứng trong normalized/ trước khi graphrag build."
        )
    if all_unconvertible:
        print(
            f"CẢNH BÁO: {len(all_unconvertible)} file đính kèm đuôi chưa hỗ trợ tự động "
            "(xem CLAUDE.md mục \"Định dạng file & tri thức chưa đọc được\") — vẫn nằm trong "
            "artifacts/_jira-attachments/, cần xử lý thủ công nếu muốn đưa vào tri thức."
        )
    return len(issues)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-root", required=True, type=Path,
                         help="vd tenants/demo/teamassistant")
    parser.add_argument("--jira-base-url", default=os.environ.get("JIRA_BASE_URL", ""),
                         help="mặc định đọc JIRA_BASE_URL trong scripts/.env — vd https://your-site.atlassian.net")
    parser.add_argument("--jira-email", default=os.environ.get("JIRA_EMAIL", ""),
                         help="mặc định đọc JIRA_EMAIL trong scripts/.env")
    parser.add_argument("--jql", default="", help='vd "project = TKA" — rỗng = mọi issue token thấy được')
    parser.add_argument("--no-attachments", action="store_true",
                         help="bỏ qua tải file đính kèm (mặc định CÓ tải)")
    args = parser.parse_args()

    token = os.environ.get("JIRA_API_TOKEN", "")
    missing = [name for name, val in (
        ("JIRA_API_TOKEN (env/scripts/.env)", token),
        ("--jira-base-url/JIRA_BASE_URL", args.jira_base_url),
        ("--jira-email/JIRA_EMAIL", args.jira_email),
    ) if not val]
    if missing:
        print(f"Thiếu: {', '.join(missing)}. Điền vào scripts/.env (xem scripts/.env.example) "
              "hoặc truyền qua CLI.", file=sys.stderr)
        sys.exit(1)

    tenant_root = args.tenant_root.resolve()
    if not tenant_root.exists():
        print(f"tenant-root không tồn tại: {tenant_root}", file=sys.stderr)
        sys.exit(1)

    try:
        sync(tenant_root, args.jira_base_url, args.jira_email, token, args.jql,
             download_attachments=not args.no_attachments)
    except JiraSyncError as e:
        print(f"Lỗi đồng bộ Jira: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
