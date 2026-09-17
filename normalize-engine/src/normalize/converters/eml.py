from __future__ import annotations

import email
from email import policy
from pathlib import Path

from bs4 import BeautifulSoup


def _html_to_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def convert(path: Path) -> str:
    with path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)

    body_part = msg.get_body(preferencelist=("plain", "html"))
    content = ""
    if body_part is not None:
        content = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            content = _html_to_text(content)

    lines = [
        f"**Từ:** {msg.get('From', '')}",
        f"**Đến:** {msg.get('To', '')}",
        f"**Ngày:** {msg.get('Date', '')}",
        f"**Tiêu đề:** {msg.get('Subject', '')}",
        "",
        content.strip(),
    ]

    attachments = [part.get_filename() or "?" for part in msg.iter_attachments()]
    if attachments:
        lines += ["", "**File đính kèm (chưa trích nội dung):** " + ", ".join(attachments)]

    return "\n".join(lines)
