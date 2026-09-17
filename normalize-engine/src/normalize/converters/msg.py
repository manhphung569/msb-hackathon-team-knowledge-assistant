from __future__ import annotations

from pathlib import Path

import extract_msg


def convert(path: Path) -> str:
    msg = extract_msg.Message(str(path))
    try:
        lines = [
            f"**Từ:** {msg.sender or ''}",
            f"**Đến:** {msg.to or ''}",
            f"**Ngày:** {msg.date or ''}",
            f"**Tiêu đề:** {msg.subject or ''}",
            "",
            (msg.body or "").strip(),
        ]
        if msg.attachments:
            names = [a.longFilename or a.shortFilename or "?" for a in msg.attachments]
            lines += ["", "**File đính kèm (chưa trích nội dung):** " + ", ".join(names)]
        return "\n".join(lines)
    finally:
        msg.close()
