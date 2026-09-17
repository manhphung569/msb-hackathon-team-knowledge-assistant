from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterator

import yaml

from ..config import Settings
from ..ingestion.metadata_rules import extract_people_mentions, extract_ticket_ids
from ..types import SourceDocument

_CONVERTER_CHANNEL = {
    "msg": "email",
    "pdf": "document",
    "docx": "document",
    "pptx": "document",
    "xlsx": "document",
}

_DOC_TYPE_RULES = [
    ("mom", "mom"),
    ("backlog", "backlog"),
    ("baocao", "report"),
    ("report", "report"),
]


def _guess_doc_type(filename: str) -> str:
    lower = filename.lower()
    for needle, doc_type in _DOC_TYPE_RULES:
        if needle in lower:
            return doc_type
    return "solution_doc"


def _guess_sensitivity(rel_path: str, settings: Settings) -> str:
    for prefix, sensitivity in settings.sensitivity_rules:
        if rel_path.startswith(prefix):
            return sensitivity
    return "internal"


def _guess_visibility(rel_path: str, settings: Settings) -> str:
    for prefix, visibility in settings.visibility_rules:
        if rel_path.startswith(prefix):
            return visibility
    return "owner"


def _guess_source_channel(frontmatter: dict) -> str:
    """`source:` bị dùng 2 kiểu khác nhau trong corpus: normalize-engine ghi đường dẫn artifact
    gốc (`source: artifacts/<rel_path>`), còn chat-notes/zalo-notes ghi thẳng nhãn kênh
    (`source: zalo-capture`...) — phải phân biệt 2 kiểu này, không lấy path làm "kênh"."""
    source = frontmatter.get("source", "") or ""
    if source and not source.startswith("artifacts/"):
        return source
    converter = str(frontmatter.get("converter", ""))
    if converter.startswith("pdf-ocr"):
        return "pdf-ocr"
    return _CONVERTER_CHANNEL.get(converter, "document")


def _guess_date(frontmatter: dict, path: Path) -> str:
    if frontmatter.get("date"):
        return str(frontmatter["date"])
    if frontmatter.get("converted_at"):
        return str(frontmatter["converted_at"])[:10]
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _guess_reliability(frontmatter: dict) -> str:
    reliability = frontmatter.get("reliability")
    if reliability:
        return str(reliability)
    return "unknown"


def _split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Tách YAML frontmatter (nếu có) khỏi nội dung — frontmatter là metadata, không nên
    lẫn vào text đem đi chunk/embed. Field trong frontmatter (project/doc_type/sensitivity)
    override lại giá trị suy đoán từ path nếu có khai báo tường minh.

    Trả về thêm `line_offset` = số dòng trong `text` gốc (kể cả frontmatter) trước khi body
    thật sự bắt đầu — dùng để dịch start_line/end_line của Chunk (tính trên body) về đúng số
    dòng trong file thật trên đĩa, cho Read(offset=...) hoạt động đúng."""
    if not text.startswith("---"):
        return {}, text, 0
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text, 0
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    raw_rest = parts[2]
    body = raw_rest.lstrip("\n")
    header = text[: len(text) - len(raw_rest)]  # "---\n<frontmatter>\n---"
    line_offset = header.count("\n") + (len(raw_rest) - len(body))
    return meta, body, line_offset


def iter_documents(settings: Settings) -> Iterator[SourceDocument]:
    """Duyệt normalized_dir, bỏ qua README.md (chỉ dùng để orient người đọc, không phải nội dung)."""
    root = settings.normalized_dir
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        if path.name == "README.md":
            continue
        rel_path = path.relative_to(root).as_posix()
        raw_text = path.read_text(encoding="utf-8")
        if not raw_text.strip():
            continue
        frontmatter, body, line_offset = _split_frontmatter(raw_text)
        if not body.strip():
            continue
        # Hash theo body (không tính frontmatter) — frontmatter có converted_at đổi mỗi lần
        # normalize chạy lại dù nội dung không đổi, nếu hash cả frontmatter sẽ tái-index thừa.
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        doc_id = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]

        sensitivity = frontmatter.get("sensitivity") or _guess_sensitivity(rel_path, settings)
        visibility = frontmatter.get("visibility") or _guess_visibility(rel_path, settings)
        if sensitivity == "confidential":
            # Ràng buộc an toàn cứng, không cho override qua frontmatter/visibility_rules —
            # nội dung confidential (hợp đồng, hoá đơn) không bao giờ lọt ra API cho người khác
            # (graphrag serve), dù ai đó lỡ gắn visibility: shared ở đâu đó.
            visibility = "owner"

        yield SourceDocument(
            doc_id=doc_id,
            path=path,
            rel_path=rel_path,
            project=frontmatter.get("project") or settings.default_project or "general",
            doc_type=frontmatter.get("doc_type") or _guess_doc_type(path.stem),
            sensitivity=sensitivity,
            visibility=visibility,
            date=_guess_date(frontmatter, path),
            source_channel=_guess_source_channel(frontmatter),
            reliability=_guess_reliability(frontmatter),
            ticket_ids=extract_ticket_ids(body, rel_path),
            people_mentions=extract_people_mentions(body),
            text=body,
            content_hash=content_hash,
            line_offset=line_offset,
        )
