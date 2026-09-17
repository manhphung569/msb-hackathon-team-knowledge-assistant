"""Ported (duplicated) from pdlc-core/adlc/tools/dashboard/server.py — the full transitive closure of Discovery workspace/project-scanning helpers needed by discovery_browse.py and chat.py to be fully independent of pdlc-core at runtime. RAW PORT — not yet import-fixed. See workbench/EXTRACTION_NOTES.md.

NOTE: this duplicates pdlc-core logic on purpose (explicit user decision, 2026-09-14) — if pdlc-core changes its Discovery scanning behavior, this copy will drift and needs manual re-sync.
"""

import json
import re
import threading
import time
from pathlib import Path

from .config import ARTIFACTS_ROOT, ROOT, SOURCES_ROOT
from .shared import _sanitize_project_key


def _display_workspace_path(root, path) -> str:
    """Best-effort relative-path display string for the UI.
    Ported from pdlc-core's workspace_layout.display_path(), simplified: that version tried
    relative_to(repo_root) then relative_to(workspace_root) — workbench has no equivalent
    'workspace_root' concept, so this tries ARTIFACTS_ROOT then SOURCES_ROOT instead, which
    are the only roots Discovery-scanning display paths in workbench are ever relative to."""
    resolved = Path(path).resolve()
    for base in (ARTIFACTS_ROOT, SOURCES_ROOT):
        try:
            return str(resolved.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
    return str(resolved).replace("\\", "/")


# ---- source line 261 (_CACHE_LOCK) ----
_CACHE_LOCK = threading.Lock()


# ---- source line 262 (_CACHE) ----
_CACHE: dict[str, tuple[float, object]] = {}


# ---- source line 263 (_MANIFEST_TTL) ----
_MANIFEST_TTL = 30.0   # workspace manifests rarely change


# ---- source line 264 (_PROJECTS_TTL) ----
_PROJECTS_TTL = 5.0    # project list + enrichment (live discovery runs update in ≤5 s)


# ---- source line 381 (DISC_STATE_NAME) ----
DISC_STATE_NAME = "progress.json"


# ---- source line 382 (DISC_STATE_DIR) ----
DISC_STATE_DIR  = "openspec/.state"


# ---- source line 383 (DISCOVERY_MANIFEST_FILES) ----
DISCOVERY_MANIFEST_FILES = ("discovery.workspace.json", "discovery.workspace.yaml")


# ---- source line 391 (_extract_workspace_manifest_info) ----
def _extract_workspace_manifest_info(manifest_file: Path) -> dict | None:
    try:
        raw = manifest_file.read_text(encoding="utf-8-sig")
    except Exception:
        return None

    name = None
    project_paths: list[str] = []
    if manifest_file.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        if isinstance(data, dict):
            name = data.get("name")
            for proj in data.get("projects", []):
                if not isinstance(proj, dict):
                    continue
                path = str(proj.get("path") or "").strip()
                if path and path.upper() != "EXTERNAL":
                    project_paths.append(path)
    else:
        match = re.search(r"^name:\s*(.+)$", raw, flags=re.MULTILINE)
        if match:
            name = match.group(1).strip().strip('"').strip("'")
        for match in re.finditer(
            r'^\s*path:\s*(?:"([^"]+)"|\'([^\']+)\'|([^#\n]+?))\s*(?:#.*)?$',
            raw,
            flags=re.MULTILINE,
        ):
            path = next((group for group in match.groups() if group is not None), "").strip()
            if path and path.upper() != "EXTERNAL":
                project_paths.append(path)

    prefixes = sorted({
        path.replace("\\", "/").split("/", 1)[0]
        for path in project_paths
        if path and path.upper() != "EXTERNAL"
    })
    if len(prefixes) == 1:
        key = prefixes[0]
    elif manifest_file.parent != ROOT:
        key = manifest_file.parent.name
    else:
        key = None

    if not key:
        return None

    return {
        "key": key,
        "name": name or key,
        "file": manifest_file,
        "root": manifest_file.parent,
    }


# ---- source line 448 (_workspace_manifest_map) ----
def _workspace_manifest_map() -> dict[str, dict]:
    _KEY = "manifest_map"
    with _CACHE_LOCK:
        entry = _CACHE.get(_KEY)
        if entry and time.monotonic() - entry[0] < _MANIFEST_TTL:
            return entry[1]  # type: ignore[return-value]

    manifests: dict[str, dict] = {}
    candidates: list[Path] = []
    for name in DISCOVERY_MANIFEST_FILES:
        root_manifest = ROOT / name
        if root_manifest.exists():
            candidates.append(root_manifest)
        candidates.extend(sorted(ROOT.glob(f"*/{name}")))

    for manifest_file in candidates:
        info = _extract_workspace_manifest_info(manifest_file)
        if not info:
            continue
        manifests.setdefault(info["key"], info)

    with _CACHE_LOCK:
        _CACHE[_KEY] = (time.monotonic(), manifests)
    return manifests


# ---- source line 474 (_list_workspace_keys) ----
def _list_workspace_keys() -> list[str]:
    keys = set(_workspace_manifest_map().keys())
    for base in (SOURCES_ROOT, ARTIFACTS_ROOT):
        if not base.exists() or not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name == "_workspace":
                continue
            keys.add(entry.name)
    return sorted(keys)


# ---- source line 486 (_default_workspace_key) ----
def _default_workspace_key() -> str | None:
    manifests = _workspace_manifest_map()
    for key, info in manifests.items():
        if info.get("root") == ROOT:
            return key
    keys = sorted(manifests) or _list_workspace_keys()
    return keys[0] if keys else None


# ---- source line 495 (_normalize_discovery_key) ----
def _normalize_discovery_key(project: str | Path) -> str:
    safe = _sanitize_project_key(project)
    if safe == ".":
        return _default_workspace_key() or "."
    return safe


# ---- source line 502 (_workspace_key_for_path) ----
def _workspace_key_for_path(project: str | Path) -> str:
    safe = _normalize_discovery_key(project)
    return safe.split("/", 1)[0] if safe not in ("", ".") else (safe or ".")


# ---- source line 507 (_workspace_exists) ----
def _workspace_exists(workspace: str | Path) -> bool:
    safe = _workspace_key_for_path(workspace)
    return safe in _list_workspace_keys()


# ---- source line 512 (_is_workspace_key) ----
def _is_workspace_key(project: str | Path) -> bool:
    safe = _normalize_discovery_key(project)
    return safe not in ("", ".") and "/" not in safe and _workspace_exists(safe)


# ---- source line 517 (_workspace_display_name) ----
def _workspace_display_name(workspace: str | Path) -> str:
    safe = _workspace_key_for_path(workspace)
    info = _workspace_manifest_map().get(safe)
    return str(info.get("name") if info else safe)


# ---- source line 528 (_discovery_workspace_roots) ----
def _discovery_workspace_roots(workspace: str | Path) -> tuple[Path, Path]:
    safe_ws = _workspace_key_for_path(workspace)
    artifact_root = (ARTIFACTS_ROOT / safe_ws).resolve()
    artifact_openspec = artifact_root / "openspec"
    source_root = (SOURCES_ROOT / safe_ws).resolve()
    source_openspec = source_root / "openspec"
    manifest_info = _workspace_manifest_map().get(safe_ws)
    legacy_root = ROOT if manifest_info and manifest_info.get("root") == ROOT and (ROOT / "openspec").exists() else None

    if artifact_openspec.exists():
        return artifact_root, artifact_openspec
    if source_openspec.exists():
        return source_root, source_openspec
    if legacy_root is not None:
        return legacy_root, legacy_root / "openspec"
    if artifact_root.exists():
        return artifact_root, artifact_openspec
    if source_root.exists():
        return source_root, source_openspec
    return artifact_root, artifact_openspec


# ---- source line 550 (_discovery_project_roots) ----
def _discovery_project_roots(project: str | Path) -> tuple[Path, Path]:
    safe = _normalize_discovery_key(project)
    if _is_workspace_key(safe):
        return _discovery_workspace_roots(safe)

    # DECOUPLED (2026-09-14): was resolve_project_layout(ROOT, safe) from pdlc-core's
    # workspace_layout.py — replaced with the same ARTIFACTS_ROOT/SOURCES_ROOT join
    # _discovery_workspace_roots() above already uses (confirmed equivalent).
    artifact_root = (ARTIFACTS_ROOT / safe).resolve()
    artifact_openspec = artifact_root / "openspec"
    source_root = (SOURCES_ROOT / safe).resolve()
    source_openspec = source_root / "openspec"

    if artifact_openspec.exists():
        return artifact_root, artifact_openspec
    if source_openspec.exists():
        return source_root, source_openspec
    if artifact_root.exists():
        return artifact_root, artifact_openspec
    return source_root, source_openspec


# ---- source line 570 (_discovery_artifact_root) ----
def _discovery_artifact_root(project: str | Path) -> Path:
    return _discovery_project_roots(project)[0]


# ---- source line 574 (_discovery_source_root) ----
def _discovery_source_root(project: str | Path) -> Path:
    safe = _normalize_discovery_key(project)
    if _is_workspace_key(safe):
        return _discovery_workspace_roots(safe)[0]
    return (SOURCES_ROOT / safe).resolve()  # was resolve_project_layout(ROOT, safe).source_root


# ---- source line 581 (_discovery_openspec_root) ----
def _discovery_openspec_root(project: str | Path) -> Path:
    return _discovery_project_roots(project)[1]


# ---- source line 780 (_iter_discovery_state_files) ----
def _iter_discovery_state_files() -> list[tuple[Path, str]]:
    state_files: list[tuple[Path, str]] = []
    seen: set[str] = set()

    for base in (ARTIFACTS_ROOT, SOURCES_ROOT):
        if not base.exists() or not base.is_dir():
            continue
        for state_file in sorted(base.glob(f"**/{DISC_STATE_DIR}/{DISC_STATE_NAME}")):
            try:
                rel = state_file.parent.parent.parent.relative_to(base).as_posix()
            except ValueError:
                continue
            rel = _sanitize_project_key(rel)
            if rel in seen or rel in ("", "."):
                continue
            state_files.append((state_file, rel))
            seen.add(rel)

    workspace_state = ROOT / DISC_STATE_DIR / DISC_STATE_NAME
    default_ws = _default_workspace_key() or "."
    if workspace_state.exists() and default_ws not in seen:
        state_files.append((workspace_state, default_ws))
        seen.add(default_ws)

    return state_files


# ---- source line 814 (_SOURCE_PROJECT_MARKERS) ----
_SOURCE_PROJECT_MARKERS: frozenset[str] = frozenset({
    "pom.xml",
    "package.json",
    "build.gradle",
    "build.gradle.kts",
    "pubspec.yaml",
    "pyproject.toml",
    "requirements.txt",
    "composer.json",
    "go.mod",
})


# ---- source line 826 (_SOURCE_SKIP_DIRS) ----
_SOURCE_SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "target",
    "dist",
    "build",
    "coverage",
    "vendor",
    "__pycache__",
    "example",
    "examples",
    "sample",
    "samples",
})


# ---- source line 844 (_format_discovery_target_arg) ----
def _format_discovery_target_arg(project: str | Path) -> str:
    safe = _normalize_discovery_key(project)
    if safe in ("", "."):
        return safe or "."
    return f'"{safe}"' if re.search(r'[^A-Za-z0-9_./:-]', safe) else safe


# ---- source line 851 (_project_display_path) ----
def _project_display_path(project: str | Path) -> str:
    safe = _normalize_discovery_key(project)
    if safe in ("", "."):
        return "."
    workspace = _workspace_key_for_path(safe)
    if safe == workspace:
        return workspace
    prefix = workspace + "/"
    if safe.startswith(prefix):
        return safe[len(prefix):]
    return safe


# ---- source line 864 (_project_leaf_name) ----
def _project_leaf_name(project: str | Path) -> str:
    safe = _normalize_discovery_key(project)
    if safe in ("", "."):
        return _workspace_display_name(_default_workspace_key() or "") or "workspace"
    return safe.rstrip("/").split("/")[-1]


# ---- source line 871 (_project_ui_meta) ----
def _project_ui_meta(project: str | Path) -> dict[str, str]:
    safe = _normalize_discovery_key(project)
    if safe in ("", "."):
        workspace = _default_workspace_key() or ""
        label = _workspace_display_name(workspace) if workspace else "Workspace"
        return {
            "displayName": label,
            "displayPath": ".",
            "leafName": label,
        }
    display = _project_display_path(safe)
    return {
        "displayName": display,
        "displayPath": display,
        "leafName": _project_leaf_name(safe),
    }


# ---- source line 889 (_list_source_projects) ----
def _list_source_projects(workspace: str | None = None) -> list[tuple[str, str]]:
    """Enumerate logical project keys from SOURCES_ROOT.

    Supports nested layouts such as sources/<workspace>/group/subgroup/project.
    Returns tuples of (project_key, display_name).
    """
    if not SOURCES_ROOT.exists() or not SOURCES_ROOT.is_dir():
        return []

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add_project(rel_key: str) -> None:
        safe_rel = rel_key.replace("\\", "/").strip("/")
        if not safe_rel or safe_rel in seen or _is_workspace_key(safe_rel):
            return
        seen.add(safe_rel)
        results.append((safe_rel, _project_display_path(safe_rel)))

    def _scan_workspace(ws_root: Path, safe_ws: str) -> None:
        start_count = len(results)

        def _walk(dir_path: Path, rel_parts: tuple[str, ...]) -> None:
            try:
                children = sorted(dir_path.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))
            except (OSError, PermissionError):
                return

            marker_names = {child.name for child in children if child.is_file()}
            if rel_parts and marker_names.intersection(_SOURCE_PROJECT_MARKERS):
                _add_project(f"{safe_ws}/{'/'.join(rel_parts)}")
                return

            for child in children:
                if not child.is_dir() or child.name.startswith(".") or child.name in _SOURCE_SKIP_DIRS:
                    continue
                _walk(child, rel_parts + (child.name,))

        _walk(ws_root, ())

        if len(results) > start_count:
            return

        for entry in sorted(ws_root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            _add_project(f"{safe_ws}/{entry.name}")

    if workspace and workspace not in ("*", "."):
        safe_ws = _sanitize_project_key(workspace)
        ws_root = SOURCES_ROOT / safe_ws
        if not ws_root.exists() or not ws_root.is_dir():
            return []
        _scan_workspace(ws_root, safe_ws)
        return sorted(results, key=lambda item: item[0].lower())

    for root_entry in sorted(SOURCES_ROOT.iterdir()):
        if not root_entry.is_dir() or root_entry.name.startswith("."):
            continue
        _scan_workspace(root_entry, root_entry.name)

    return sorted(results, key=lambda item: item[0].lower())


# ---- source line 953 (_source_project_exists) ----
def _source_project_exists(project: str | Path) -> bool:
    safe = _normalize_discovery_key(project)
    if safe in ("", "."):
        return True
    if _is_workspace_key(safe):
        return _workspace_exists(safe)
    return _discovery_source_root(safe).exists()


# ---- source line 1056 (_find_discovery_workspaces) ----
def _find_discovery_workspaces() -> list[dict]:
    manifests = _workspace_manifest_map()
    scanned_entries = _find_discovery_projects()
    grouped: dict[str, list[dict]] = {}
    for entry in scanned_entries:
        ws_key = _workspace_key_for_path(entry["projectPath"])
        grouped.setdefault(ws_key, []).append(entry)

    workspaces: list[dict] = []
    for ws_key in _list_workspace_keys():
        projects = grouped.get(ws_key, [])
        workspace_entry = next((p for p in projects if p["projectPath"] == ws_key), None)
        scanned_projects = [p for p in projects if p["projectPath"] != ws_key]
        workspaces.append({
            "workspace": ws_key,
            "name": _workspace_display_name(ws_key),
            "manifest": manifests.get(ws_key, {}).get("file") and _display_workspace_path(ROOT, manifests[ws_key]["file"]),
            "projectCount": len(_list_source_projects(ws_key)),
            "scannedProjectCount": sum(1 for p in scanned_projects if p.get("phases_done", 0) > 0),
            "workspaceState": workspace_entry,
        })

    return workspaces


# ---- source line 1330 (_AGENT_PARTIAL_SENTINEL) ----
_AGENT_PARTIAL_SENTINEL: dict = {
    "discovery-tech-debt":             "openspec/tech-debt",   # items/*.md present before REGISTER.md
    # discovery-proposal-writer intentionally absent: full sentinel is identical path ("openspec/changes")
    # so a partial entry here would be dead code -- it can never fire without the full sentinel also firing.
    "discovery-architecture-overview": "openspec/_workspace",  # any workspace output started
}


# ---- source line 1337 (_AGENT_SENTINEL) ----
_AGENT_SENTINEL: dict = {
    "discovery-phase-a-scanner":       "openspec/knowledge/00-overview/project-map.md",
    "discovery-spec-writer":           "openspec/specs",
    "discovery-assessment":            "openspec/assessment/REPORT.md",
    "discovery-complexity-profiler":   "openspec/assessment/COMPLEXITY.md",
    "discovery-pattern-extractor":     "openspec/patterns",
    "discovery-skill-extractor":       "openspec/skills",
    "discovery-design-writer":         "openspec/knowledge/20-architecture",
    "discovery-workflow-diagrammer":   "openspec/diagrams",
    "discovery-coding-standards":      "openspec/standards",
    "discovery-integration-standards": "openspec/integration",
    "discovery-scenario-extractor":    "openspec/scenarios",
    "discovery-testcase-extractor":    "openspec/testcases",
    "discovery-tech-debt":             "openspec/tech-debt/REGISTER.md",
    "discovery-knowledge-builder":     "openspec/knowledge/INDEX.md",
    "discovery-proposal-writer":       "openspec/changes",
    "discovery-doc-reviewer":          "openspec/REVIEW.md",
    "discovery-cross-referencer":      "openspec/_workspace",
    "discovery-architecture-overview": "openspec/_workspace/architecture-overview",
    "discovery-contract-validator":    "openspec/_workspace/contract-validation.md",
}


# ---- source line 1359 (_EP_AGENT_LAYER) ----
_EP_AGENT_LAYER: dict = {
    "discovery-phase-a-scanner": "A",
    "discovery-spec-writer": "B1",   "discovery-assessment": "B1",
    "discovery-complexity-profiler": "B1", "discovery-pattern-extractor": "B1",
    "discovery-skill-extractor": "B1",    "discovery-design-writer": "B1",
    "discovery-workflow-diagrammer": "B1", "discovery-coding-standards": "B1",
    "discovery-integration-standards": "B1",
    "discovery-scenario-extractor": "B2", "discovery-testcase-extractor": "B2",
    "discovery-tech-debt": "B2",
    "discovery-knowledge-builder": "B3",
    "discovery-proposal-writer": "C",
    "discovery-doc-reviewer": "D",
    "discovery-cross-referencer": "E", "discovery-architecture-overview": "E",
    "discovery-contract-validator": "E",
}


# ---- source line 1375 (_EP_LAYER_AGENTS) ----
_EP_LAYER_AGENTS: dict = {
    "A":  {"discovery-phase-a-scanner"},
    "B1": {"discovery-spec-writer", "discovery-assessment", "discovery-complexity-profiler",
           "discovery-pattern-extractor", "discovery-skill-extractor", "discovery-design-writer",
           "discovery-workflow-diagrammer", "discovery-coding-standards",
           "discovery-integration-standards"},
    "B2": {"discovery-scenario-extractor", "discovery-testcase-extractor", "discovery-tech-debt"},
    "B3": {"discovery-knowledge-builder"},
    "C":  {"discovery-proposal-writer"},
    "D":  {"discovery-doc-reviewer"},
    "E":  {"discovery-cross-referencer", "discovery-architecture-overview",
           "discovery-contract-validator"},
}


# ---- source line 1393 (_WORKSPACE_E_AGENTS) ----
_WORKSPACE_E_AGENTS: frozenset = frozenset({
    "discovery-cross-referencer",
    "discovery-architecture-overview",
    "discovery-contract-validator",
})


# ---- source line 1401 (_EP_DISPLAY_RANK) ----
_EP_DISPLAY_RANK: dict = {
    "completed": 5, "in-progress": 4, "partial": 3,
    "failed": 2, "pending": 1, "not-started": 0, "": 0,
}


# ---- source line 1408 (_EP_COMPLETE_RANK) ----
_EP_COMPLETE_RANK: dict = {
    "completed": 4, "partial": 3, "in-progress": 2,
    "failed": 1, "pending": 0, "not-started": 0, "": 0,
}


# ---- source line 1701 (_sentinel_exists) ----
def _sentinel_exists(project_path: Path, rel: str) -> bool:
    """True when the sentinel path exists as a file, or as a dir with ≥1 .md."""
    p = project_path / rel
    if p.is_file():
        return True
    if p.is_dir():
        try:
            next(p.rglob("*.md"))
            return True
        except StopIteration:
            pass
    return False


# ---- source line 1715 (_enrich_phases) ----
def _enrich_phases(data: dict, project_path: Path, is_workspace: bool = False) -> None:
    """Read-time enrichment: derive agent and phase statuses from sentinel files on disk.

    Modifies *data* in-memory only -- never writes to disk.
    Rules:
      - "in-progress" agents (live state from orchestrator) are NEVER touched.
      - "completed" / "partial" agents are NEVER downgraded.
      - "not-started" / "pending" / "failed" agents are upgraded to "completed"
        when their sentinel file/dir exists on disk.
      - Phase/layer status uses *display* priority where in-progress > partial,
        so a live run is always visible even if some earlier work completed.
      - Phase E agents (cross-referencer, architecture-overview, contract-validator)
        are WORKSPACE-level.  They are only processed when is_workspace=True.
    Safe to call on every /discovery/state.json request.
    """
    agents = data.setdefault("subAgents", {})
    phases = data.setdefault("phases", {})

    # Pre-compute cost lookup once -- used to enrich sentinel-inferred agents.
    # costSummary.actual.by_agent[name] = {cost_usd, tier, calls}
    _by_agent_cost: dict = (
        data.get("costSummary", {}).get("actual", {}).get("by_agent", {}) or {}
    )

    # ── Step 1a: Synthesise "completed" from full sentinel files ────────────────
    # Skip "in-progress" (live state), "completed", and "partial" -- trust them.
    # Phase E agents are WORKSPACE-level -- skip entirely for per-project runs.
    # When is_workspace=True, project_path == ROOT so sentinels resolve correctly.
    for agent_name, sentinel in _AGENT_SENTINEL.items():
        if not is_workspace and agent_name in _WORKSPACE_E_AGENTS:
            continue  # E agents belong to workspace, never per-project
        existing = agents.get(agent_name)
        cur_st = existing.get("status", "not-started") if isinstance(existing, dict) else "not-started"
        if cur_st in ("in-progress", "completed", "partial"):
            continue  # trust orchestrator live state or already terminal
        sentinel_hit = _sentinel_exists(project_path, sentinel)
        if sentinel_hit:
            base = dict(existing) if isinstance(existing, dict) else {}
            # If no token data, pull from costSummary.actual.by_agent (best-effort)
            if not base.get("tokens") and agent_name in _by_agent_cost:
                ca = _by_agent_cost[agent_name]
                base["tokens"] = {
                    "cost_usd": ca.get("cost_usd"),
                    "tier":     ca.get("tier", ""),
                    "input":    ca.get("input", 0),
                    "output":   ca.get("output", 0),
                    "inferred": True,
                }
            agents[agent_name] = {
                **base,
                "status": "completed",
                "layer":  base.get("layer") or _EP_AGENT_LAYER.get(agent_name, "unknown"),
            }

    # ── Step 1b: Synthesise "in-progress" from partial sentinel files ─────────
    # When partial output exists but the completion sentinel is absent,
    # the agent was interrupted mid-run.  Show as "in-progress" so the dashboard
    # reflects reality instead of hiding it as "not-started".
    for agent_name, partial_sent in _AGENT_PARTIAL_SENTINEL.items():
        if not is_workspace and agent_name in _WORKSPACE_E_AGENTS:
            continue  # E agents belong to workspace, never per-project
        existing = agents.get(agent_name)
        cur_st = existing.get("status", "not-started") if isinstance(existing, dict) else "not-started"
        if cur_st in ("in-progress", "completed", "partial"):
            continue  # already set -- do not touch
        full_sent = _AGENT_SENTINEL.get(agent_name)
        if full_sent and _sentinel_exists(project_path, full_sent):
            continue  # full sentinel present -> handled as completed in Step 1a
        if _sentinel_exists(project_path, partial_sent):
            base = dict(existing) if isinstance(existing, dict) else {}
            agents[agent_name] = {
                **base,
                "status": "in-progress",
                "layer":  base.get("layer") or _EP_AGENT_LAYER.get(agent_name, "unknown"),
            }

    # ── Step 2: Compute layer status from agent statuses ─────────────────────
    def _layer_status(layer_key: str) -> str:
        """Roll up all known agents in a layer to a single status."""
        known = _EP_LAYER_AGENTS.get(layer_key, set())
        if not known:
            return "pending"
        done = in_prog = failed = skipped = 0
        for n in known:
            a  = agents.get(n)
            st = a.get("status", "not-started") if isinstance(a, dict) else "not-started"
            if st == "completed":      done    += 1
            elif st == "in-progress":  in_prog += 1
            elif st == "failed":       failed  += 1
            elif st in ("skipped",):   skipped += 1
            # not-started / pending / absent -> not-done
        if in_prog:
            return "in-progress"
        if done == 0:
            return "failed" if failed else "pending"
        return "completed" if done + skipped == len(known) else "in-progress"

    def _merge(obj: dict, key: str, computed: str) -> None:
        """Set obj[key] = best of (stored, computed) using DISPLAY rank.

        "completed" is never downgraded.  "in-progress" beats "partial" (stored legacy).
        """
        stored = obj.get(key, "pending")
        if _EP_COMPLETE_RANK.get(stored, 0) >= _EP_COMPLETE_RANK["completed"]:
            return  # never downgrade from completed
        if _EP_DISPLAY_RANK.get(computed, 0) > _EP_DISPLAY_RANK.get(stored, 0):
            obj[key] = computed

    # ── Step 3: Apply to phases/layers ───────────────────────────────────────
    # Per-project runs: compute A, B (with sub-layers), C, D.
    # Workspace runs:   compute E only -- A/B/C/D don't exist at workspace level.
    if not is_workspace:
        # Strip Phase E if it was persisted in the raw state file -- it belongs to
        # workspace level only and must NOT appear in per-project views.
        phases.pop("E", None)

        # Phase A
        _merge(phases.setdefault("A", {}), "status", _layer_status("A"))

        # Phase B: sub-layers first, then overall from sub-layers
        ph_b     = phases.setdefault("B", {})
        b_layers = ph_b.setdefault("layers", {})
        for lk in ("B1", "B2", "B3"):
            _merge(b_layers.setdefault(lk, {}), "status", _layer_status(lk))

        b_sts = [b_layers.get(lk, {}).get("status", "pending") for lk in ("B1", "B2", "B3")]
        if all(s == "completed" for s in b_sts):
            _merge(ph_b, "status", "completed")
        elif "in-progress" in b_sts:
            _merge(ph_b, "status", "in-progress")
        elif any(s == "completed" for s in b_sts):
            _merge(ph_b, "status", "in-progress")

        # Phases C, D
        for ph_key in ("C", "D"):
            _merge(phases.setdefault(ph_key, {}), "status", _layer_status(ph_key))
    else:
        # Workspace (DIP Platform): Phase E only
        _merge(phases.setdefault("E", {}), "status", _layer_status("E"))


# ---- source line 1856 (_find_discovery_projects) ----
def _find_discovery_projects() -> list[dict]:
    """List workspace-level and per-project Discovery entries.

    Projects with a progress.json are enriched from state; projects that only
    exist under SOURCES_ROOT are still returned so the UI can show unscanned
    services in Discovery and Vault selectors. Per-project state can live in
    either the new artifacts root or the legacy source-root openspec folder.
    """
    _KEY = "discovery_projects"
    with _CACHE_LOCK:
        entry = _CACHE.get(_KEY)
        if entry and time.monotonic() - entry[0] < _PROJECTS_TTL:
            return entry[1]  # type: ignore[return-value]

    results = []
    seen_paths: set[str] = set()
    state_files = _iter_discovery_state_files()

    for p, rel in state_files:
        project_path = _discovery_artifact_root(rel)
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            # is_workspace: legacy "." entry OR a bare workspace key (e.g. "dip")
            # whose progress.json lives at artifacts/<ws>/openspec/.state/
            is_workspace = (rel == "." or rel == "" or _is_workspace_key(rel))
            # Apply the same read-time enrichment used by _serve_discovery_state.
            # is_workspace controls whether Phase E agents/phases are processed.
            _enrich_phases(data, project_path, is_workspace=is_workspace)
            phases = data.get("phases", {})
            agents = data.get("subAgents", {})
            # phases_done / phases_total: for per-project exclude Phase E (workspace-level).
            # Since _enrich_phases(is_workspace=False) no longer creates phases.E,
            # the filter is a belt-and-suspenders guard for any stale stored state.
            if is_workspace:
                done   = sum(1 for v in phases.values() if isinstance(v, dict) and v.get("status") == "completed")
                total  = len(phases)
                done_a = sum(1 for v in agents.values() if isinstance(v, dict) and v.get("status") == "completed")
                total_a = len(agents)
            else:
                proj_phases = {k: v for k, v in phases.items() if k != "E"}
                done   = sum(1 for v in proj_phases.values() if isinstance(v, dict) and v.get("status") == "completed")
                total  = len(proj_phases)
                done_a = sum(1 for v in agents.values() if isinstance(v, dict) and v.get("status") == "completed")
                total_a = len(agents)
            results.append({
                "project":      data.get("project", rel),
                "projectPath":  rel,
                "lastRunAt":    data.get("lastRunAt"),
                "lastRunBy":    data.get("lastRunBy"),
                "phases_done":  done,
                "phases_total": total,
                "agents_done":  done_a,
                "agents_total": total_a,
                "nextAction":   data.get("nextAction", ""),
                "state_file":   _display_workspace_path(ROOT, p),
                **_project_ui_meta(rel),
            })
            seen_paths.add(rel)
        except Exception as e:
            results.append({
                "project": rel, "projectPath": rel, "error": str(e),
                "phases_done": 0, "phases_total": 0,
                "agents_done": 0, "agents_total": 0,
                **_project_ui_meta(rel),
            })
            seen_paths.add(rel)

    for rel, label in _list_source_projects():
        if rel in seen_paths:
            continue
        results.append({
            "project":      label,
            "projectPath":  rel,
            "lastRunAt":    None,
            "lastRunBy":    None,
            "phases_done":  0,
            "phases_total": 4,
            "agents_done":  0,
            "agents_total": 0,
            "nextAction":   f"discovery {_format_discovery_target_arg(rel)} --phase=A",
            **_project_ui_meta(rel),
        })
        seen_paths.add(rel)

    with _CACHE_LOCK:
        _CACHE[_KEY] = (time.monotonic(), results)
    return results


# ---- source line 1985 (_normalize_discovery_target) ----
def _normalize_discovery_target(project: str, rel: str) -> tuple[str, str]:
    """Normalize malformed project/path pairs coming from workspace arch-overview links.

    Supports cases like:
    - project='__arch_overview__/dip/dsp-eb-webapp', rel='openspec/...'
    - project='__arch_overview__', rel='dip/dsp-eb-webapp/openspec/...'
    - project='', rel='dip/dsp-eb-webapp/openspec/...'
    """
    project = (project or "").replace("\\", "/").strip().strip("/")
    rel = (rel or "").replace("\\", "/").lstrip("/")

    if project.startswith("__arch_overview__/") and project != "__arch_overview__":
        project = project[len("__arch_overview__/"):]

    if "/openspec/" in project:
        project = project.split("/openspec/", 1)[0]

    if rel and not rel.startswith("openspec/") and "/openspec/" in rel:
        proj_from_rel, rel_tail = rel.split("/openspec/", 1)
        if not project or project == "__arch_overview__":
            project = proj_from_rel.strip("/")
        rel = "openspec/" + rel_tail

    return project, rel


