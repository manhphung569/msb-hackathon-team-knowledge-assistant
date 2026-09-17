from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_BUILD_HISTORY_FILE = "build_history.jsonl"


def append_build_event(
    cache_dir: Path,
    *,
    provider: str,
    local_model: str | None,
    force: bool,
    build_graph: bool,
    changed_docs: int,
    deleted_docs: int,
    n_chunks: int,
    duration_seconds: float,
    success: bool,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "local_model": local_model,
        "force": force,
        "build_graph": build_graph,
        "changed_docs": changed_docs,
        "deleted_docs": deleted_docs,
        "n_chunks": n_chunks,
        "duration_seconds": round(duration_seconds, 3),
        "success": success,
    }
    with open(cache_dir / _BUILD_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_build_history(cache_dir: Path, limit: int = 20) -> list[dict]:
    path = cache_dir / _BUILD_HISTORY_FILE
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events
