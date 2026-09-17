from __future__ import annotations

from pathlib import Path
from typing import Iterator

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


def _iter_block_items(doc: Document) -> Iterator[Paragraph | Table]:
    """Duyệt đoạn văn và bảng theo đúng thứ tự xuất hiện trong file."""
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, doc)
        elif child.tag.endswith("}tbl"):
            yield Table(child, doc)


def _table_to_markdown(table: Table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = rows[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def convert(path: Path) -> str:
    doc = Document(str(path))
    parts: list[str] = []
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = ((block.style.name if block.style else None) or "").lower()
            if style.startswith("heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "2"
                parts.append(f"{'#' * min(int(level) + 1, 6)} {text}")
            else:
                parts.append(text)
        else:
            table_md = _table_to_markdown(block)
            if table_md:
                parts.append(table_md)
    return "\n\n".join(parts)
