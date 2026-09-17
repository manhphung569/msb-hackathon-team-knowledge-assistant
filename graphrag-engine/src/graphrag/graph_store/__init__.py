from __future__ import annotations

from pathlib import Path

import yaml

from ..config import PROJECT_ROOT
from .base import GraphStore

_SCHEMA_PATH = PROJECT_ROOT / "config" / "graph_schema.yaml"


def load_ontology() -> tuple[list[str], dict[str, tuple[list[str], list[str]]]]:
    """Đọc config/graph_schema.yaml — dùng chung bởi kuzu_store (sinh schema DDL) và
    build_index (validate cặp from_type/to_type trước khi upsert, tránh Kuzu Binder exception
    nếu LLM trích ra 1 relation với type không khớp ontology, vd Person ATTENDED Person thay
    vì Person ATTENDED Document)."""
    raw = yaml.safe_load(_SCHEMA_PATH.read_text(encoding="utf-8"))
    node_types = list(raw["nodes"].keys())
    edges: dict[str, tuple[list[str], list[str]]] = {}
    for name, spec in raw["edges"].items():
        f = spec["from"]
        t = spec["to"]
        edges[name] = (f if isinstance(f, list) else [f], t if isinstance(t, list) else [t])
    return node_types, edges


def try_open(db_dir: Path) -> GraphStore | None:
    """Mở KuzuStore nếu extras đã cài (`pip install -e ".[graph]"`), None nếu chưa — cho phép
    build/query tiếp tục vector-only thay vì crash khi máy chưa cài graph extras."""
    try:
        from .kuzu_store import KuzuStore
    except ImportError:
        return None
    return KuzuStore(db_dir)
