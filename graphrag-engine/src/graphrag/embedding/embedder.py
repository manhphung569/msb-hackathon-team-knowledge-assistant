from __future__ import annotations

import os
from abc import ABC, abstractmethod

from ..config import Settings


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VoyageEmbedder(Embedder):
    def __init__(self, model: str = "voyage-3"):
        import voyageai

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise RuntimeError("VOYAGE_API_KEY chưa được set trong .env")
        self._client = voyageai.Client(api_key=api_key)
        self._model = model
        self.dim = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings


class GreenNodeEmbedder(Embedder):
    """MSB AI Hackathon 2026 co-organizer platform — API docs/keys pending at time of writing.
    Written speculatively assuming an OpenAI-compatible /embeddings endpoint (base_url override
    on the openai SDK client, same shape as _generate_openai in generation/answer_generator.py)
    since that's the most common shape for these platforms. If GreenNode's real API turns out to
    use a different request/response envelope, only this class's body needs to change — nothing
    that calls get_embedder("greennode", ...) needs to change."""

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        api_key = os.environ.get("GREENNODE_API_KEY")
        base_url = os.environ.get("GREENNODE_BASE_URL")
        if not api_key or not base_url:
            raise RuntimeError(
                "GREENNODE_API_KEY / GREENNODE_BASE_URL chưa được set trong .env — "
                "xem config/settings.yaml comment cho embedding.default_provider: greennode."
            )
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model or os.environ.get("GREENNODE_EMBED_MODEL", "greennode-embed-default")
        # TODO: xác nhận dim thật khi có key/docs — 1024 chỉ là giá trị tạm để code chạy được,
        # KHÔNG dùng để build index thật trước khi verify (dim sai sẽ làm LanceDB lưu vector lệch).
        self.dim = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]


class LocalEmbedder(Embedder):
    """Chạy offline qua fastembed (ONNX, không cần GPU) — dùng khi thiếu API key
    hoặc tài liệu sensitivity=confidential. Model override qua env GRAPHRAG_LOCAL_EMBED_MODEL."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model_name = os.environ.get("GRAPHRAG_LOCAL_EMBED_MODEL", model_name)
        self._model = TextEmbedding(model_name=self.model_name)
        # fastembed không expose dim trực tiếp trước khi embed — suy ra bằng 1 lần chạy thử
        self.dim = len(next(self._model.embed(["_"])).tolist())

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts)]


def get_embedder(provider: str, settings: Settings, local_model: str | None = None) -> Embedder:
    if provider == "voyage":
        return VoyageEmbedder()
    if provider == "greennode":
        return GreenNodeEmbedder()
    if provider == "local":
        return LocalEmbedder(local_model or settings.embedding_local_model)
    raise ValueError(f"Unknown embedding provider: {provider}")
