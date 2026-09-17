from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from ..ingestion.build_history import load_build_history
from ..ingestion.dedup import load_state
from ..ingestion.loader import iter_documents
from ..vector_store.lancedb_store import LanceDBStore

_CONVERTIBLE_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".msg", ".html", ".htm"}


def build_health_report(settings: Settings) -> dict:
    # settings.normalized_dir = <tenant_root>/normalized (Settings.load(tenant_root=...)) kể từ
    # khi tách tenants/ (2026-08-22) — artifacts/ luôn nằm ngang hàng, KHÔNG còn 1 artifacts/ toàn
    # cục ở repo root nữa (đã xoá cùng đợt move).
    artifact_root = settings.normalized_dir.parent / "artifacts"
    normalized_root = settings.normalized_dir
    artifacts = [p for p in artifact_root.rglob("*") if p.is_file() and p.suffix.lower() in _CONVERTIBLE_EXTS]
    normalized_files = [p for p in normalized_root.rglob("*.md") if p.name != "README.md"]
    docs = list(iter_documents(settings))
    index_state = load_state(settings.cache_dir)
    index_meta = _load_json(settings.cache_dir / "index_meta.json")
    # convert_state.json chuyển sang per-tenant (<tenant_root>/data/cache/) từ Phase 1 (2026-08-22)
    # — không còn nằm ở normalize-engine/data/cache/ toàn cục nữa.
    convert_state = _load_json(settings.cache_dir / "convert_state.json")
    build_history = load_build_history(settings.cache_dir, limit=10)

    indexed_doc_ids = set()
    if index_meta:
        store = LanceDBStore(settings.vector_index_dir, dim=index_meta["dim"])
        indexed_doc_ids = store.list_doc_ids()

    missing_normalized: list[dict] = []
    stale_normalized: list[dict] = []
    for artifact in artifacts:
        rel = artifact.relative_to(artifact_root)
        expected = (normalized_root / rel).with_suffix(".md")
        if not expected.exists():
            missing_normalized.append(
                {"artifact_path": rel.as_posix(), "expected_normalized_path": expected.relative_to(normalized_root).as_posix()}
            )
        elif artifact.stat().st_mtime > expected.stat().st_mtime:
            stale_normalized.append(
                {
                    "artifact_path": rel.as_posix(),
                    "normalized_path": expected.relative_to(normalized_root).as_posix(),
                }
            )

    orphaned_normalized: list[dict] = []
    stale_build: list[dict] = []
    missing_indexed: list[dict] = []
    for doc in docs:
        if _source_artifact_rel(doc.path) is not None:
            source_rel = _source_artifact_rel(doc.path)
            source_path = artifact_root / source_rel
            if not source_path.exists():
                orphaned_normalized.append({"normalized_path": doc.rel_path, "missing_artifact_path": source_rel})
        if index_state.get(doc.doc_id) != doc.content_hash:
            stale_build.append({"normalized_path": doc.rel_path, "doc_id": doc.doc_id})
        if doc.doc_id not in indexed_doc_ids:
            missing_indexed.append({"normalized_path": doc.rel_path, "doc_id": doc.doc_id})

    return {
        "status": "ok",
        "generated_at": _now_iso(),
        "summary": {
            "artifact_files": len(artifacts),
            "normalized_files": len(normalized_files),
            "indexed_docs": len(indexed_doc_ids),
            "docs_needing_normalization": len(missing_normalized) + len(stale_normalized),
            "docs_needing_build": len(stale_build) + len(missing_indexed),
            "orphaned_normalized_files": len(orphaned_normalized),
        },
        "metadata_coverage": _metadata_coverage(docs),
        "build_history": build_history,
        "index_meta": index_meta,
        "convert_state_entries": len(convert_state) if isinstance(convert_state, dict) else 0,
        "stale_files": {
            "missing_normalized": missing_normalized,
            "stale_normalized": stale_normalized,
            "stale_build": stale_build,
            "missing_indexed": missing_indexed,
            "orphaned_normalized": orphaned_normalized,
        },
    }


def _metadata_coverage(docs) -> dict:
    total = len(docs) or 1
    return {
        "project": _ratio(sum(1 for d in docs if d.project), total),
        "source_channel": _ratio(sum(1 for d in docs if d.source_channel), total),
        "date": _ratio(sum(1 for d in docs if d.date), total),
        "reliability": _ratio(sum(1 for d in docs if d.reliability and d.reliability != "unknown"), total),
        "ticket_ids": _ratio(sum(1 for d in docs if d.ticket_ids), total),
        "people_mentions": _ratio(sum(1 for d in docs if d.people_mentions), total),
        "document_type": _ratio(sum(1 for d in docs if d.doc_type), total),
    }


def _ratio(count: int, total: int) -> dict:
    return {"count": count, "total": total, "pct": round((count / total) * 100, 1)}


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _source_artifact_rel(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") and text != "---":
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None
    for line in lines[1:end_idx]:
        if line.startswith("source:"):
            source = line.partition(":")[2].strip()
            if source.startswith("artifacts/"):
                return source.removeprefix("artifacts/")
    return None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
