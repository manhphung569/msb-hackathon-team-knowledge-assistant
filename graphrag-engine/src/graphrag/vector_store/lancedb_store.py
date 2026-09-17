from __future__ import annotations

from pathlib import Path

import lancedb
import pyarrow as pa

from ..types import Chunk
from .base import VectorStore

_TABLE_NAME = "chunks"


class LanceDBStore(VectorStore):
    def __init__(self, db_dir: Path, dim: int):
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(db_dir))
        self._dim = dim
        self._table = self._db.open_table(_TABLE_NAME) if _TABLE_NAME in self._db.table_names() else None

    def _ensure_table(self) -> None:
        if self._table is not None:
            return
        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("doc_id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("section", pa.string()),
                pa.field("source_path", pa.string()),
                pa.field("project", pa.string()),
                pa.field("doc_type", pa.string()),
                pa.field("sensitivity", pa.string()),
                pa.field("visibility", pa.string()),
                pa.field("date", pa.string()),
                pa.field("source_channel", pa.string()),
                pa.field("reliability", pa.string()),
                pa.field("ticket_ids", pa.list_(pa.string())),
                pa.field("people_mentions", pa.list_(pa.string())),
                pa.field("chunk_index", pa.int32()),
                pa.field("start_line", pa.int32()),
                pa.field("end_line", pa.int32()),
                pa.field("vector", pa.list_(pa.float32(), self._dim)),
            ]
        )
        self._table = self._db.create_table(_TABLE_NAME, schema=schema)

    def upsert(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        self._ensure_table()
        ids = ", ".join(f"'{c.chunk_id}'" for c in chunks)
        self._table.delete(f"chunk_id IN ({ids})")
        rows = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "text": c.text,
                "section": c.section,
                "source_path": c.source_path,
                "project": c.project,
                "doc_type": c.doc_type,
                "sensitivity": c.sensitivity,
                "visibility": c.visibility,
                "date": c.date,
                "source_channel": c.source_channel,
                "reliability": c.reliability,
                "ticket_ids": c.ticket_ids,
                "people_mentions": c.people_mentions,
                "chunk_index": c.chunk_index,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "vector": c.embedding,
            }
            for c in chunks
        ]
        self._table.add(rows)

    def delete_by_doc_id(self, doc_id: str) -> None:
        if self._table is None:
            return
        self._table.delete(f"doc_id = '{doc_id}'")

    def search(self, query_vector: list[float], k: int, filters: dict | None = None) -> list[Chunk]:
        if self._table is None:
            return []
        q = self._table.search(query_vector).limit(k)
        if filters:
            q = q.where(" AND ".join(f"{key} = '{value}'" for key, value in filters.items()))
        rows = q.to_list()
        return [
            Chunk(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                text=r["text"],
                section=r["section"],
                source_path=r["source_path"],
                project=r["project"],
                doc_type=r["doc_type"],
                sensitivity=r["sensitivity"],
                visibility=r["visibility"],
                date=r["date"],
                source_channel=r["source_channel"],
                reliability=r.get("reliability", "unknown"),
                ticket_ids=r.get("ticket_ids", []),
                people_mentions=r.get("people_mentions", []),
                chunk_index=r["chunk_index"],
                start_line=r.get("start_line", 0),
                end_line=r.get("end_line", 0),
                embedding=None,
            )
            for r in rows
        ]

    def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids or self._table is None:
            return []
        ids = ", ".join(f"'{cid}'" for cid in chunk_ids)
        rows = self._table.search().where(f"chunk_id IN ({ids})").limit(len(chunk_ids)).to_list()
        return self._rows_to_chunks(rows)

    def all_chunks(self, filters: dict | None = None) -> list[Chunk]:
        if self._table is None:
            return []
        q = self._table.search()
        if filters:
            q = q.where(" AND ".join(f"{key} = '{value}'" for key, value in filters.items()))
        rows = q.limit(1_000_000).to_list()
        return self._rows_to_chunks(rows)

    def _rows_to_chunks(self, rows: list[dict]) -> list[Chunk]:
        return [
            Chunk(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                text=r["text"],
                section=r["section"],
                source_path=r["source_path"],
                project=r["project"],
                doc_type=r["doc_type"],
                sensitivity=r["sensitivity"],
                visibility=r["visibility"],
                date=r["date"],
                source_channel=r["source_channel"],
                reliability=r.get("reliability", "unknown"),
                ticket_ids=r.get("ticket_ids", []),
                people_mentions=r.get("people_mentions", []),
                chunk_index=r["chunk_index"],
                start_line=r.get("start_line", 0),
                end_line=r.get("end_line", 0),
                embedding=None,
            )
            for r in rows
        ]

    def list_doc_ids(self) -> set[str]:
        if self._table is None:
            return set()
        rows = self._table.search().limit(1_000_000).to_list()
        return {r["doc_id"] for r in rows}

    def reset(self) -> None:
        if self._table is not None or _TABLE_NAME in self._db.table_names():
            self._db.drop_table(_TABLE_NAME)
        self._table = None
