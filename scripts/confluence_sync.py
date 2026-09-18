"""Đồng bộ trang Confluence Cloud (REST API v1, Content Search bằng CQL) thành note .md trong
normalized/_confluence-docs/ của 1 tenant graphrag-engine — dựng cho MSB AI Hackathon 2026
(Team Knowledge Assistant), bổ sung nguồn BRD/URD/SRS/kiến trúc/thiết kế bên cạnh source code
và Jira backlog đã có (xem scripts/jira_sync.py).

Cùng "ngoại lệ note" như jira_sync.py — ghi thẳng vào normalized/, không qua artifacts/ và
normalize-engine's pipeline.py.

Auth: Confluence Cloud CÙNG cơ chế Basic Auth (email + API token) như Jira, và ĐÃ XÁC NHẬN
THẬT (2026-09-18) cùng 1 token vừa dùng cho Jira cũng gọi được Confluence trên cùng site
msb-ai.atlassian.net — không cần xin token riêng. Mặc định đọc lại JIRA_EMAIL/JIRA_API_TOKEN
trong scripts/.env, có thể override bằng CONFLUENCE_EMAIL/CONFLUENCE_API_TOKEN nếu khác site/
tài khoản.

Base URL Confluence Cloud = <site>/wiki (khác Jira, không có /wiki) — mặc định tự suy ra từ
JIRA_BASE_URL, override bằng CONFLUENCE_BASE_URL nếu Confluence nằm ở domain khác.

Nội dung trang lấy dạng "storage format" (XHTML riêng của Confluence) qua CQL search, tự
convert sang markdown bằng bộ walker nhỏ tự viết (KHÔNG tái dùng
normalize-engine/converters/html.py — file đó thiết kế riêng cho 1 dạng HTML report cụ thể,
không khớp cấu trúc storage format của Confluence). Macro Confluence phức tạp (ảnh nhúng,
bảng nội dung tự động...) bị bỏ qua best-effort — đủ cho văn bản/heading/bảng/danh sách, KHÔNG
đầy đủ 100% mọi loại macro.

Cách dùng (đã điền scripts/.env thì chỉ cần):
    graphrag-engine/.venv/Scripts/python.exe scripts/confluence_sync.py \\
        --tenant-root tenants/demo/teamassistant --cql 'space = "SD" AND type = "page"'
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml
from bs4 import BeautifulSoup, NavigableString, Tag

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

_PAGE_SIZE = 25


class ConfluenceSyncError(RuntimeError):
    pass


# ---- storage format (XHTML riêng Confluence) -> markdown, best-effort ---------------------

def _cell_text(cell: Tag) -> str:
    parts: list[str] = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                parts.append(text)
        elif isinstance(child, Tag):
            text = child.get_text(" ", strip=True)
            if text:
                parts.append(text)
    return " ".join(p for p in parts if p).strip()


def _table_to_markdown(table: Tag) -> str:
    rows: list[str] = []
    header: list[str] | None = None
    n_cols = 0
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        values = [_cell_text(c) for c in cells]
        if header is None:
            header = values
            n_cols = len(values)
            rows.append("| " + " | ".join(header) + " |")
            rows.append("| " + " | ".join(["---"] * n_cols) + " |")
            continue
        if n_cols and len(values) != n_cols:
            values = (values + [""] * n_cols)[:n_cols]
        rows.append("| " + " | ".join(v.replace("|", "/") for v in values) + " |")
    return "\n".join(rows) if rows else "(bảng trống)"


_HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}


def _storage_to_markdown(storage_html: str) -> str:
    """Duyệt tuần tự body theo đúng thứ tự xuất hiện — giữ heading/paragraph/list/table đúng
    ngữ cảnh. Macro Confluence (ac:structured-macro...) không có xử lý riêng — BeautifulSoup
    coi như thẻ lạ, get_text() vẫn lấy được phần chữ bên trong (best-effort, không giữ layout)."""
    soup = BeautifulSoup(storage_html, "html.parser")
    parts: list[str] = []
    seen: set[int] = set()

    for el in soup.find_all(list(_HEADING_TAGS) + ["p", "ul", "ol", "table"]):
        if id(el) in seen or any(id(p) in seen for p in el.parents):
            continue
        if el.name in _HEADING_TAGS:
            text = el.get_text(strip=True)
            if text:
                parts.append(f"{_HEADING_TAGS[el.name]} {text}")
        elif el.name == "p":
            text = el.get_text(" ", strip=True)
            if text:
                parts.append(text)
        elif el.name in ("ul", "ol"):
            items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
            if items:
                parts.append("\n".join(f"- {i}" for i in items if i))
            seen.add(id(el))
        elif el.name == "table":
            parts.append(_table_to_markdown(el))
            seen.add(id(el))

    return "\n\n".join(parts) if parts else "(trang không có nội dung text nhận diện được)"


# ---- Confluence REST API (v1 content search, CQL) ------------------------------------------

def _fetch_pages(base_url: str, email: str, token: str, cql: str) -> list[dict]:
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    pages: list[dict] = []
    start = 0
    while True:
        query = urllib.parse.urlencode({
            "cql": cql, "expand": "body.storage,version,space",
            "limit": _PAGE_SIZE, "start": start,
        })
        url = f"{base_url.rstrip('/')}/wiki/rest/api/content/search?{query}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise ConfluenceSyncError(f"Confluence trả lỗi HTTP {e.code}: {body[:500]}") from e
        except urllib.error.URLError as e:
            raise ConfluenceSyncError(f"Không kết nối được tới {base_url}: {e}") from e

        raw_batch = data.get("results", [])
        # CQL không lọc "type = page" theo mặc định (dù người dùng không viết rõ trong --cql)
        # có thể trả về CẢ attachment (vd ảnh đính kèm trong trang) — object đó không có
        # body.storage, xử lý như page sẽ ra note rỗng vô nghĩa. Tự lọc lại ở đây, không dựa
        # vào người dùng nhớ thêm "AND type = page" trong CQL. Dùng độ dài raw_batch (KHÔNG
        # phải sau lọc) để biết còn trang kế tiếp hay không — nếu lọc rồi mới đếm sẽ dừng phân
        # trang sớm khi 1 trang kết quả toàn attachment bị lọc sạch.
        pages.extend(item for item in raw_batch if item.get("type") == "page")
        if len(raw_batch) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
    return pages


_SLUG_RE = re.compile(r"[^\w\-]+", re.UNICODE)


def _page_to_note(page: dict, project: str) -> tuple[str, str]:
    page_id = page["id"]
    title = page.get("title", "").strip() or f"page-{page_id}"
    space = (page.get("space") or {}).get("key", "")
    updated = ((page.get("version") or {}).get("when") or "")[:10]
    storage_html = ((page.get("body") or {}).get("storage") or {}).get("value", "")
    body_md = _storage_to_markdown(storage_html)

    meta = {
        "source": "confluence-api",
        "page_id": page_id,
        "space": space,
        "date": updated,
        "project": project,
        "doc_type": "solution_doc",
        "sensitivity": "internal",
        "reliability": "cao",
        "basis": "Đồng bộ trực tiếp qua Confluence REST API (scripts/confluence_sync.py), "
                 "không phải nghe kể lại.",
    }
    frontmatter_yaml = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)

    content = f"---\n{frontmatter_yaml}---\n\n# {title}\n\n{body_md}\n"
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")[:60] or "untitled"
    filename = f"{page_id}_{slug}"
    return filename, content


def sync(tenant_root: Path, base_url: str, email: str, token: str, cql: str) -> int:
    project = tenant_root.name
    out_dir = tenant_root / "normalized" / "_confluence-docs"
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = _fetch_pages(base_url, email, token, cql)
    if not pages:
        print("Không tìm thấy trang nào khớp CQL — kiểm tra lại --cql hoặc quyền truy cập token.")
        return 0

    for page in pages:
        filename, content = _page_to_note(page, project)
        path = out_dir / f"{filename}.md"
        path.write_text(content, encoding="utf-8")
        print(f"  {page.get('title', page['id'])} -> {path.relative_to(tenant_root.parent.parent)}")

    print(f"\nĐã đồng bộ {len(pages)} trang Confluence vào {out_dir}")
    return len(pages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-root", required=True, type=Path,
                         help="vd tenants/demo/teamassistant")
    parser.add_argument(
        "--confluence-base-url",
        default=os.environ.get("CONFLUENCE_BASE_URL") or os.environ.get("JIRA_BASE_URL", ""),
        help="mặc định suy từ JIRA_BASE_URL trong scripts/.env (site Atlassian giống nhau)",
    )
    parser.add_argument("--confluence-email",
                         default=os.environ.get("CONFLUENCE_EMAIL") or os.environ.get("JIRA_EMAIL", ""))
    parser.add_argument("--cql", required=True,
                         help='vd \'space = "SD" AND type = "page"\' — CQL Confluence, không có JQL rỗng an toàn '
                              '(quét cả site rất lớn), BẮT BUỘC chỉ định')
    args = parser.parse_args()

    token = os.environ.get("CONFLUENCE_API_TOKEN") or os.environ.get("JIRA_API_TOKEN", "")
    missing = [name for name, val in (
        ("CONFLUENCE_API_TOKEN/JIRA_API_TOKEN (env/scripts/.env)", token),
        ("--confluence-base-url/CONFLUENCE_BASE_URL/JIRA_BASE_URL", args.confluence_base_url),
        ("--confluence-email/CONFLUENCE_EMAIL/JIRA_EMAIL", args.confluence_email),
    ) if not val]
    if missing:
        print(f"Thiếu: {', '.join(missing)}. Điền vào scripts/.env hoặc truyền qua CLI.", file=sys.stderr)
        sys.exit(1)

    tenant_root = args.tenant_root.resolve()
    if not tenant_root.exists():
        print(f"tenant-root không tồn tại: {tenant_root}", file=sys.stderr)
        sys.exit(1)

    try:
        sync(tenant_root, args.confluence_base_url, args.confluence_email, token, args.cql)
    except ConfluenceSyncError as e:
        print(f"Lỗi đồng bộ Confluence: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
