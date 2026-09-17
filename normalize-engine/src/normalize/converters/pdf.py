from __future__ import annotations

from pathlib import Path

import pdfplumber

from ..errors import NeedsOCR


def convert(path: Path) -> str:
    parts: list[str] = []
    extracted_any = False
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                extracted_any = True
            parts.append(f"## Trang {i}\n\n{text}")
    if not extracted_any:
        raise NeedsOCR(f"{path.name}: không trích được text ở trang nào — có thể là bản scan/ảnh")
    return "\n\n".join(parts)
