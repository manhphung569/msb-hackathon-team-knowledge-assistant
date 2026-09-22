"""Wrapper để .mcp.json khởi động mcp-atlassian (cài trong scripts/.venv-mcp-atlassian) với biến
môi trường nạp từ scripts/.env.mcp-atlassian (đã gitignore) — cùng minimal .env loader pattern
với jira_sync.py/confluence_sync.py, vì mcp-atlassian tự đọc biến từ process env, không có flag
--env-file như khi chạy qua Docker (xem docs/decisions/2026-09-21_add-atlassian-mcp-live-search.md
mục pivot Docker -> venv, do virtualization bị tắt trong firmware máy lúc thiết lập).

Không dùng subprocess.Popen với stdin/stdout=PIPE — để mcp-atlassian.exe thừa hưởng THẲNG stdio
của chính process này (do Claude Code cấp khi spawn server MCP qua stdio transport), tránh phải
tự relay 2 chiều thủ công.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ENV_FILE = _SCRIPTS_DIR / ".env.mcp-atlassian"
_EXE = _SCRIPTS_DIR / ".venv-mcp-atlassian" / "Scripts" / "mcp-atlassian.exe"

env = dict(os.environ)
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

sys.exit(subprocess.run([str(_EXE)], env=env).returncode)
