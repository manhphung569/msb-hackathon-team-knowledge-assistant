from __future__ import annotations

from ..embedding.embedder import Embedder
from ..types import Chunk
from ..vector_store.base import VectorStore


def retrieve(
    query: str, embedder: Embedder, store: VectorStore, k: int, filters: dict | None = None
) -> list[Chunk]:
    query_vector = embedder.embed([query])[0]
    return store.search(query_vector, k=k, filters=filters)
