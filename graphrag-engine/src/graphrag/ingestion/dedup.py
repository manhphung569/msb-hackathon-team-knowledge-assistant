from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from ..types import SourceDocument

_STATE_FILE = "index_state.json"
_META_FILE = "index_meta.json"


def load_state(cache_dir: Path) -> dict[str, str]:
    path = cache_dir / _STATE_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(cache_dir: Path, state: dict[str, str]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / _STATE_FILE).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def filter_changed(docs: Iterable[SourceDocument], state: dict[str, str]) -> Iterator[SourceDocument]:
    """So hash với lần index trước — chỉ trả về doc mới hoặc đã đổi nội dung."""
    for doc in docs:
        if state.get(doc.doc_id) != doc.content_hash:
            yield doc


def load_index_meta(cache_dir: Path) -> dict | None:
    path = cache_dir / _META_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_index_meta(cache_dir: Path, provider: str, dim: int, local_model: str | None = None) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / _META_FILE).write_text(
        json.dumps({"provider": provider, "dim": dim, "local_model": local_model}, indent=2),
        encoding="utf-8",
    )
