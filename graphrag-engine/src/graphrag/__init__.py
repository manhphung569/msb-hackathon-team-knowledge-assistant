"""graphrag-engine: index + truy vấn cho lớp normalized/ của kho tri thức."""

import sys as _sys

# Console Windows mặc định dùng codepage cp1252, không encode được tiếng Việt có dấu.
# Ép stdout/stderr sang utf-8 ngay khi import package, trước khi bất kỳ module nào print().
for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
