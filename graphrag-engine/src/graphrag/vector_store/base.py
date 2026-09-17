from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import Chunk


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[Chunk]) -> None: ...

    @abstractmethod
    def delete_by_doc_id(self, doc_id: str) -> None: ...

    @abstractmethod
    def search(self, query_vector: list[float], k: int, filters: dict | None = None) -> list[Chunk]: ...

    @abstractmethod
    def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]: ...

    @abstractmethod
    def all_chunks(self, filters: dict | None = None) -> list[Chunk]: ...

    @abstractmethod
    def list_doc_ids(self) -> set[str]: ...

    @abstractmethod
    def reset(self) -> None: ...
