"""Standalone read-only port of 4 Discovery-browse routes from pdlc-core (RAW EXTRACTION, needs SOURCES_ROOT/PDLC_ARTIFACTS_ROOT rewiring — see workbench/EXTRACTION_NOTES.md)."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import ROOT
from .discovery_helpers import (
    _discovery_artifact_root,
    _discovery_openspec_root,
    _display_workspace_path,
    _find_discovery_projects,
    _find_discovery_workspaces,
    _format_discovery_target_arg,
    _list_source_projects,
    _normalize_discovery_target,
    _project_ui_meta,
    _source_project_exists,
    _workspace_exists,
    _workspace_key_for_path,
)
from .shared import _sanitize_project_key


class DiscoveryBrowseMixin:
    # ---- ported from pdlc-core server.py line 3697 (_serve_discovery_workspaces) ----
    def _serve_discovery_workspaces(self):
        return self._send_json(_find_discovery_workspaces())

    # ---- ported from pdlc-core server.py line 3827 (_serve_discovery_file) ----
    def _serve_discovery_file(self):
        """Serve a single artifact file under <project>/openspec/.

        Query params:
          project=<rel-path>   project root (e.g. 'inbd')
          path=<rel-path>      artifact path RELATIVE to project (e.g. 'openspec/specs/orders/spec.md')

        Safety: rejects .. traversal, paths must start with 'openspec/'.
        """
        qs      = parse_qs(urlparse(self.path).query)
        project = (qs.get("project") or [None])[0]
        rel     = (qs.get("path")    or [None])[0]
        if not project or not rel:
            return self._send_json({"error": "missing ?project= or ?path="}, 400)
        project, rel = _normalize_discovery_target(project, rel)
        if ".." in rel.replace("\\", "/").split("/"):
            return self._send_json({"error": "path traversal blocked"}, 400)
        rel_norm = rel.lstrip("/").replace("\\", "/")
        if not rel_norm.startswith("openspec/"):
            return self._send_json({"error": "only openspec/* allowed"}, 403)

        safe_proj = _sanitize_project_key(project)
        target    = (_discovery_artifact_root(safe_proj) / rel_norm).resolve()
        # Ensure target is within ROOT/<project>/openspec/
        proj_root = _discovery_openspec_root(safe_proj).resolve()
        try:
            target.relative_to(proj_root)
        except ValueError:
            return self._send_json({"error": "escape blocked"}, 403)

        if not target.exists() or not target.is_file():
            return self._send_json({
                "error": "not found",
                "projectPath": safe_proj,
                "path": rel_norm,
                "fullPath": _display_workspace_path(ROOT, target),
            }, 404)
        try:
            text = target.read_text(encoding="utf-8")
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)
        return self._send_json({
            "projectPath": safe_proj,
            "path": rel_norm,
            "fullPath": _display_workspace_path(ROOT, target),
            "size":     target.stat().st_size,
            "content":  text,
        })

    # ---- ported from pdlc-core server.py line 3877 (_serve_discovery_tree) ----
    def _serve_discovery_tree(self):
        """Return full file tree under <project>/openspec/ as a nested dict.

        Skips: .obsidian/, .state/, hidden files, *.json (machine-readable).
        Includes: *.md, *.txt files only.
        """
        qs = parse_qs(urlparse(self.path).query)
        project = (qs.get("project") or [None])[0]
        if not project:
            return self._send_json({"error": "missing ?project="}, 400)
        safe_proj = _sanitize_project_key(project)
        root = _discovery_openspec_root(safe_proj).resolve()
        if not root.exists() or not root.is_dir():
            if _source_project_exists(safe_proj):
                return self._send_json({
                    "project": project,
                    "projectPath": safe_proj,
                    "tree": {"name": "openspec", "type": "dir", "children": []},
                    "notScannedYet": True,
                    "hint": f"Run discovery {safe_proj} --phase=A to generate openspec artifacts.",
                })
            return self._send_json({"error": "openspec dir not found"}, 404)

        SKIP_DIRS = {".obsidian", ".state", "_attachments", "node_modules"}
        ALLOWED_EXT = {".md", ".txt"}

        def walk(dir_path: Path, rel_prefix: str = "") -> dict:
            entries = []
            try:
                children = sorted(dir_path.iterdir(),
                                   key=lambda x: (not x.is_dir(), x.name.lower()))
            except PermissionError:
                return {"name": dir_path.name, "type": "dir", "children": []}
            for child in children:
                if child.name.startswith(".") or child.name in SKIP_DIRS:
                    continue
                rel = (rel_prefix + "/" + child.name) if rel_prefix else child.name
                if child.is_dir():
                    if child.is_symlink():  # skip symlinks to prevent infinite recursion
                        continue
                    sub = walk(child, rel)
                    if sub["children"]:
                        entries.append(sub)
                else:
                    if child.suffix.lower() not in ALLOWED_EXT:
                        continue
                    entries.append({
                        "name": child.name,
                        "type": "file",
                        "path": "openspec/" + rel,
                        "size": child.stat().st_size,
                    })
            return {"name": dir_path.name, "type": "dir", "children": entries}

        tree = walk(root)
        tree["name"] = "openspec"
        return self._send_json({
            "project":     project,
            "projectPath": safe_proj,
            "tree":        tree,
        })

    # ---- ported from pdlc-core server.py line 4208 (_serve_discovery_workspace) ----
    def _serve_discovery_workspace(self):
        """List all projects under a workspace directory -- scanned AND unscanned.

        Query: ?workspace=dip   (workspace key; omit or "*" for all scanned)
        Returns: [{project, projectPath, scanned, phases_done, phases_total, nextAction, ...}]
        """
        qs = parse_qs(urlparse(self.path).query)
        workspace = (qs.get("workspace") or [None])[0]

        scanned_map = {p["projectPath"]: p for p in _find_discovery_projects()}

        results = []
        if workspace and workspace != "*":
            safe_ws = _workspace_key_for_path(workspace)
            source_projects = _list_source_projects(safe_ws)
            if not source_projects and not _workspace_exists(safe_ws):
                return self._send_json({"error": f"workspace not found: {workspace}"}, 404)
            source_rels: set[str] = set()
            for rel, label in source_projects:
                source_rels.add(rel)
                if rel in scanned_map:
                    p = scanned_map[rel]
                    results.append({
                        **p,
                        "project": p.get("project") or label,
                        "scanned": p.get("phases_done", 0) > 0,
                        **_project_ui_meta(rel),
                    })
                else:
                    results.append({
                        "project":      label,
                        "projectPath":  rel,
                        "scanned":      False,
                        "phases_done":  0,
                        "phases_total": 4,
                        "agents_done":  0,
                        "agents_total": 0,
                        "lastRunAt":    None,
                        "nextAction":   f"discovery {_format_discovery_target_arg(rel)} --phase=A",
                        **_project_ui_meta(rel),
                    })
            # Fallback: include scanned artifacts-only projects not in sources
            for proj_path, p in scanned_map.items():
                if "/" not in proj_path:
                    continue
                ws = proj_path.split("/")[0]
                if ws == safe_ws and proj_path not in source_rels:
                    results.append({**p, "scanned": p.get("phases_done", 0) > 0})
        else:
            results = [{**p, "scanned": p.get("phases_done", 0) > 0} for p in _find_discovery_projects() if "/" in p.get("projectPath", "")]

        return self._send_json(results)

