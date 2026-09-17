"""Path/config constants for the migrated Vault subsystem.

Migrated from pdlc-core/adlc/tools/dashboard/server.py (2026-09-14, see
docs/decisions/2026-09-14_migrate-dashboard-vault-subsystem-from-pdlc-core.md).

Names ROOT/ARTIFACTS_ROOT/SOURCES_ROOT/VAULT_DB/VAULT_PAGE_DIR/VAULT_PAGE_FILE/VAULT_DOCS_FILE
are kept identical to the originals in pdlc-core's server.py so the extracted/ported code
(search.py, discovery_browse.py, discovery_helpers.py, chat.py, admin.py, db.py, pages.py)
needs no further renaming — only their *values* changed to fit workbench's own layout.
"""

import os
from pathlib import Path

# workbench/server/config.py -> parents[0]=server, [1]=workbench
WORKBENCH_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = WORKBENCH_ROOT / "static"
# Override via WORKBENCH_DATA_DIR for a throwaway/synthetic run (e.g. hackathon demo testing)
# WITHOUT ever touching data/vault.db — that file is (post-cutover, or right now during Phase 1
# rehearsal) a real copy of production data: real users, real conversations, real backlog items.
# See "Before touching data/vault.db" in workbench/README.md.
DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR") or (WORKBENCH_ROOT / "data"))

# Minimal .env loader (ported from pdlc-core's server.py:369-378 — no python-dotenv
# dependency needed). Runs at import time so os.environ.get(...) calls elsewhere
# (llm.py, teams.py, etc.) see these values.
_env_file = WORKBENCH_ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

# sibling-repo layout: D:\Projects\repos\{pdlc-core, pdlc-vault, pdlc-artifacts, sources}
_REPOS_ROOT = Path(__file__).resolve().parents[3]

# Content source is UNCHANGED by this migration (explicit decision, see ADR): the
# migrated engine still reads Discovery/ADLC spec content from pdlc-artifacts, not from
# this repo's own tenants/. Override via env if the sibling-repo assumption doesn't hold.
PDLC_ARTIFACTS_ROOT = Path(os.environ.get("PDLC_ARTIFACTS_ROOT") or (_REPOS_ROOT / "pdlc-artifacts"))
PDLC_SOURCES_ROOT = Path(os.environ.get("PDLC_SOURCES_ROOT") or (_REPOS_ROOT / "sources"))

# ---- aliases matching the original pdlc-core names (see module docstring) ----
ROOT = WORKBENCH_ROOT
ARTIFACTS_ROOT = PDLC_ARTIFACTS_ROOT
SOURCES_ROOT = PDLC_SOURCES_ROOT

VAULT_DB = DATA_DIR / "vault.db"
VAULT_PAGE_DIR = STATIC_DIR
VAULT_PAGE_FILE = STATIC_DIR / "index.html"
VAULT_DOCS_FILE = STATIC_DIR / "docs.html"

DEFAULT_PORT = int(os.environ.get("WORKBENCH_PORT", "8766"))

# graphrag-engine's guest /query API (LanceDB-backed, single tenant per process — see
# graphrag-engine/src/graphrag/api.py) — the one shared knowledge index chat.py's discover-mode
# retrieval calls into instead of its own keyword-grep over ARTIFACTS_ROOT. Point at whichever
# tenant's `graphrag serve --tenant-root ...` instance is running (hackathon demo:
# tenants/demo/teamassistant, GUEST_API_KEY from that tenant's .env).
GRAPHRAG_API_BASE = os.environ.get("GRAPHRAG_API_BASE", "http://127.0.0.1:8000")
GRAPHRAG_API_KEY = os.environ.get("GRAPHRAG_API_KEY", "")

# Where knowledge_export.py writes backlog-item notes (see that module) — must match whichever
# tenant GRAPHRAG_API_BASE is currently serving, or exported items won't show up in query results
# until the next `graphrag build` on the RIGHT tenant. WORKBENCH_ROOT.parent is this repo's own
# root (pdlc-vault) — unlike ARTIFACTS_ROOT/SOURCES_ROOT above, this intentionally reads from
# THIS repo's tenants/, not a sibling repo (hackathon demo tenant lives here, not in pdlc-artifacts).
GRAPHRAG_DEMO_TENANT_ROOT = Path(
    os.environ.get("GRAPHRAG_DEMO_TENANT_ROOT")
    or (WORKBENCH_ROOT.parent / "tenants" / "demo" / "teamassistant")
)
