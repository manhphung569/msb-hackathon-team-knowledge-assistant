from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_CHARS_PER_TOKEN = 4  # xấp xỉ, đủ dùng để giới hạn kích thước chunk không cần tokenizer thật


@dataclass
class RawSection:
    heading: str
    text: str
    # 1-indexed, tính trong `text` truyền vào split_by_heading (doc.text, đã bỏ frontmatter) —
    # metadata.py cộng thêm doc.line_offset để ra đúng dòng trong file thật trên đĩa.
    start_line: int = 1
    end_line: int = 1


def _line_at(text: str, char_offset: int) -> int:
    """Số dòng (1-indexed) chứa vị trí ký tự char_offset trong text."""
    return text.count("\n", 0, char_offset) + 1


def _end_line(text: str, end: int) -> int:
    """Số dòng (1-indexed) của điểm cuối 1 khoảng [.., end) trong text. Khi end nằm giữa text,
    lấy dòng ngay trước điểm cắt tiếp theo. Khi end == len(text) và text kết thúc bằng "\\n",
    trừ thêm 1 — nếu không, dòng "ma" rỗng sau dấu xuống dòng cuối cùng sẽ bị tính lố thành 1
    dòng nội dung, làm end_line lệch 1 dòng so với file thật trên đĩa."""
    if end < len(text):
        return _line_at(text, end) - 1
    if text.endswith("\n"):
        return _line_at(text, end) - 1
    return _line_at(text, end)


def split_by_heading(text: str) -> list[RawSection]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [RawSection(heading="", text=text, start_line=1, end_line=max(1, _end_line(text, len(text))))]
    sections: list[RawSection] = []
    if matches[0].start() > 0:
        head_end = matches[0].start()
        sections.append(RawSection(heading="", text=text[:head_end], start_line=1, end_line=max(1, _end_line(text, head_end))))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(
            RawSection(
                heading=m.group(2).strip(),
                text=text[start:end],
                start_line=_line_at(text, start),
                end_line=max(_line_at(text, start), _end_line(text, end)),
            )
        )
    return [s for s in sections if s.text.strip()]


def split_recursive(text: str, chunk_size: int, chunk_overlap: int) -> list[tuple[str, int, int]]:
    """Trả về list (piece, start_line, end_line) — dòng tính TRONG `text` truyền vào (1-indexed),
    caller (chunk_document) cộng thêm offset của section cha để ra dòng thật trong toàn văn."""
    size = chunk_size * _CHARS_PER_TOKEN
    overlap = chunk_overlap * _CHARS_PER_TOKEN
    if len(text) <= size:
        return [(text, 1, max(1, _end_line(text, len(text))))]
    pieces: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        start_line = _line_at(text, start)
        end_line = _end_line(text, end)
        pieces.append((text[start:end], start_line, max(start_line, end_line)))
        if end == len(text):
            break
        start = end - overlap
    return pieces


def chunk_sections(sections: list[RawSection], chunk_size: int, chunk_overlap: int) -> list[RawSection]:
    """Áp cắt-theo-kích-thước (fallback khi 1 section vẫn dài hơn chunk_size) lên 1 danh sách
    RawSection ĐÃ tách sẵn theo ranh giới có ý nghĩa (heading markdown, hoặc hàm/class code —
    xem code_splitter.py) — tách riêng khỏi chunk_document() để 2 nguồn tách-ranh-giới khác
    nhau (markdown vs code) dùng chung đúng 1 lớp an toàn kích thước, không copy-paste logic."""
    result: list[RawSection] = []
    for section in sections:
        base = section.start_line - 1  # dòng ngay trước khi section bắt đầu
        for piece_text, p_start, p_end in split_recursive(section.text, chunk_size, chunk_overlap):
            result.append(
                RawSection(heading=section.heading, text=piece_text, start_line=base + p_start, end_line=base + p_end)
            )
    return result


def chunk_document(text: str, chunk_size: int, chunk_overlap: int) -> list[RawSection]:
    """Cắt theo heading markdown trước; nếu 1 section vẫn dài hơn chunk_size thì cắt tiếp theo độ dài."""
    return chunk_sections(split_by_heading(text), chunk_size, chunk_overlap)
