"""Nạp source code TRỰC TIẾP vào index — KHÔNG qua bước sinh file markdown/LLM tóm tắt trung
gian (quyết định thiết kế 2026-09-18, xem hội thoại: source code phải luôn là nguồn duy nhất
của sự thật, không sinh thêm file có thể lệch khỏi thực tế). `SourceDocument.path`/`rel_path`
trỏ THẲNG vào file .py thật trên đĩa — Read(offset=...) trên citation trả về mở đúng file gốc,
không phải bản sao. Chunk boundary tách bằng `ast` (chunking/code_splitter.py), máy móc 100%,
không LLM. Giải thích ngôn ngữ tự nhiên chỉ xảy ra lúc TRẢ LỜI câu hỏi (generate_answer, đã có
sẵn cho mọi loại tài liệu khác), không lúc index.

v1: chỉ Python. Ngôn ngữ khác cần parser riêng (tree-sitter, chưa triển khai) — file không phải
.py bị bỏ qua, không lỗi."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ..config import Settings
from ..types import SourceDocument
from .loader import _guess_sensitivity, _guess_visibility
from .metadata_rules import extract_people_mentions, extract_ticket_ids

_SKIP_DIR_NAMES = {
    ".venv", "venv", "__pycache__", ".git", "node_modules", ".pytest_cache",
    "dist", "build", ".mypy_cache", ".ruff_cache",
}
_SKIP_DIR_SUFFIXES = (".egg-info",)


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES or name.startswith(".") or any(name.endswith(s) for s in _SKIP_DIR_SUFFIXES)


def _iter_python_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if any(_should_skip_dir(part) for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def iter_source_code_documents(settings: Settings) -> Iterator[SourceDocument]:
    """Duyệt mọi entry trong settings.source_code_paths (config source_code_paths của tenant,
    xem config.py) — mỗi entry {"path": "...", "label": "..."}. Đường dẫn không tồn tại: in
    cảnh báo, bỏ qua entry đó (không raise — 1 tenant config sai đường dẫn không được chặn build
    của toàn bộ tài liệu khác trong CÙNG tenant, đúng nguyên tắc cách ly đã bàn khi thiết kế)."""
    for entry in settings.source_code_paths:
        raw_path = entry.get("path", "")
        label = entry.get("label") or Path(raw_path).name or "source"
        root = Path(raw_path)
        if not root.exists():
            print(f"  [CẢNH BÁO] source_code_paths: không tồn tại, bỏ qua: {raw_path}")
            continue

        for file_path in _iter_python_files(root):
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError) as e:
                print(f"  [CẢNH BÁO] Không đọc được {file_path}: {e} — bỏ qua file này.")
                continue
            if not text.strip():
                continue

            rel_in_root = file_path.relative_to(root).as_posix()
            rel_path = f"source-code/{label}/{rel_in_root}"
            doc_id = hashlib.sha1(f"source-code:{label}:{rel_in_root}".encode("utf-8")).hexdigest()[:16]
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            sensitivity = _guess_sensitivity(rel_path, settings)
            visibility = _guess_visibility(rel_path, settings)
            if sensitivity == "confidential":
                visibility = "owner"  # cùng ràng buộc cứng như loader.py cho mọi tài liệu khác

            yield SourceDocument(
                doc_id=doc_id,
                path=file_path,
                rel_path=rel_path,
                project=settings.default_project or "general",
                doc_type="source_code",
                sensitivity=sensitivity,
                visibility=visibility,
                date=datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d"),
                source_channel="source-code",
                reliability="cao",  # nguyên văn code thật, không phải suy luận/nghe kể
                ticket_ids=extract_ticket_ids(text, rel_path),
                people_mentions=extract_people_mentions(text),
                text=text,
                content_hash=content_hash,
                line_offset=0,  # đọc thẳng file thật, không qua frontmatter -> không lệch dòng
            )
