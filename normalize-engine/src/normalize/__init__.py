"""normalize-engine: chuyển artifacts/ (pdf, docx, pptx, xlsx, msg) sang normalized/ (.md)."""

import sys as _sys

# Console Windows mặc định dùng codepage cp1252, không encode được tiếng Việt có dấu.
for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from .env import load_env as _load_env  # noqa: E402

_load_env()
