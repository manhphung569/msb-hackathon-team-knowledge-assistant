from __future__ import annotations

import os
import time

from ..chunking.metadata import build_chunks
from ..config import Settings
from ..embedding.embedder import get_embedder
from ..graph_extraction.entity_extractor import extract_entities
from ..graph_extraction.relation_extractor import extract_relations
from ..graph_extraction.resolver import EntityResolver
from ..graph_store import load_ontology
from ..graph_store import try_open as try_open_graph_store
from ..ingestion.build_history import append_build_event
from ..ingestion.dedup import filter_changed, load_index_meta, load_state, save_index_meta, save_state
from ..ingestion.loader import SourceDocument, iter_documents
from ..types import Chunk, GraphEdge, GraphNode
from ..vector_store.lancedb_store import LanceDBStore


def _resolve_build_provider(docs: list[SourceDocument], settings: Settings) -> str:
    # Một index dùng một provider duy nhất (LanceDB cần vector cùng chiều dài trong 1 bảng).
    # Nếu corpus có bất kỳ tài liệu confidential nào, hoặc thiếu API key, build local toàn bộ.
    if any(d.sensitivity == "confidential" for d in docs):
        return "local"
    if not os.environ.get("VOYAGE_API_KEY"):
        return "local"
    return settings.embedding_default_provider


_EXTRACTION_API_KEY_ENV = {"openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}


def _get_graph_store(settings: Settings):
    """None nếu graph layer chưa sẵn sàng (thiếu extras hoặc API key) — build vẫn tiếp tục
    vector-only, giống cách embedder fallback sang local khi thiếu VOYAGE_API_KEY. Khác với
    graph_store.try_open() (dùng cho query, chỉ cần đọc): build cần thêm API key của
    extraction_provider vì phải gọi LLM để extract entity/relation."""
    key_env = _EXTRACTION_API_KEY_ENV.get(settings.extraction_provider, "")
    if key_env and not os.environ.get(key_env):
        print(f"Thiếu {key_env} trong .env — bỏ qua graph layer (cần cho entity/relation extraction).")
        return None
    store = try_open_graph_store(settings.graph_db_dir)
    if store is None:
        print('Graph extras chưa cài (`pip install -e ".[graph]"`) — bỏ qua graph layer, chỉ build vector.')
    return store


def _extract_graph_for_chunk(
    chunk: Chunk,
    doc: SourceDocument,
    settings: Settings,
    resolver: EntityResolver,
    ontology_edges: dict[str, tuple[list[str], list[str]]],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    # Không gửi chunk confidential ra Claude API — mirror đúng nguyên tắc _resolve_build_provider
    # dùng cho embedding (ARCHITECTURE.md §8).
    if chunk.sensitivity == "confidential":
        return [], []

    raw_entities = extract_entities(chunk.text, settings)
    if not raw_entities:
        return [], []

    name_to_id: dict[str, str] = {}
    name_to_type: dict[str, str] = {}
    seen_names: set[str] = set()
    edges: list[GraphEdge] = []
    for e in raw_entities:
        node_id = resolver.resolve(e["type"], e["name"], e["aliases"])
        for candidate in (e["name"], *e["aliases"]):
            name_to_id[candidate] = node_id
            name_to_type[candidate] = e["type"]
        if e["name"] not in seen_names:
            seen_names.add(e["name"])
            edges.append(
                GraphEdge(
                    from_id=chunk.chunk_id,
                    from_type="Chunk",
                    to_id=node_id,
                    to_type=e["type"],
                    relation="MENTIONS",
                    evidence=e["evidence"],
                    doc_id=doc.doc_id,
                )
            )

    for r in extract_relations(chunk.text, raw_entities, settings):
        from_id = name_to_id.get(r["from"])
        to_id = name_to_id.get(r["to"])
        if not from_id or not to_id:
            continue
        from_type = name_to_type[r["from"]]
        to_type = name_to_type[r["to"]]
        valid_from, valid_to = ontology_edges.get(r["relation"], ([], []))
        if from_type not in valid_from or to_type not in valid_to:
            # LLM đôi khi gán relation không khớp ontology (vd Person ATTENDED Person thay vì
            # Person ATTENDED Document) — Kuzu sẽ Binder-exception nếu cứ upsert, nên bỏ qua
            # thay vì crash cả build vì 1 relation sai của 1 chunk.
            print(
                f"    graph: bỏ qua relation không khớp ontology: "
                f"{from_type} {r['relation']} {to_type} (chunk {chunk.chunk_id})"
            )
            continue
        edges.append(
            GraphEdge(
                from_id=from_id,
                from_type=name_to_type[r["from"]],
                to_id=to_id,
                to_type=name_to_type[r["to"]],
                relation=r["relation"],
                evidence=r["evidence"],
                doc_id=doc.doc_id,
            )
        )

    chunk_node = GraphNode(id=chunk.chunk_id, type="Chunk", name="", doc_id=doc.doc_id)
    return [chunk_node], edges


def build_index(
    settings: Settings, force: bool = False, provider_override: str | None = None, build_graph: bool = False
) -> int:
    started = time.perf_counter()
    all_docs = list(iter_documents(settings))
    if not all_docs:
        print(f"Không tìm thấy tài liệu .md nào trong {settings.normalized_dir}")
        return 0

    provider = provider_override or _resolve_build_provider(all_docs, settings)
    embedder = get_embedder(provider, settings)
    local_model = getattr(embedder, "model_name", None)

    existing_meta = load_index_meta(settings.cache_dir)
    config_changed = existing_meta is not None and (
        existing_meta.get("provider") != provider or existing_meta.get("local_model") != local_model
    )

    if config_changed and not force:
        raise RuntimeError(
            f"Index hiện tại dùng provider={existing_meta.get('provider')!r} "
            f"local_model={existing_meta.get('local_model')!r}, build này chọn "
            f"provider={provider!r} local_model={local_model!r}. "
            "Chạy lại với --force để build lại toàn bộ (dim vector có thể khác, không trộn được)."
        )

    state = {} if (force or config_changed) else load_state(settings.cache_dir)
    changed_docs = all_docs if (force or config_changed) else list(filter_changed(all_docs, state))

    store = LanceDBStore(settings.vector_index_dir, dim=embedder.dim)
    # So trực tiếp với doc_id ĐANG THẬT SỰ có trong LanceDB (không dựa vào state.json) để tìm
    # tài liệu đã bị XOÁ khỏi normalized/ — trước đây thiếu bước này nên file xoá khỏi ổ đĩa
    # vẫn "ma" trong index mãi mãi. Dùng state.json để so sánh không đủ tin cậy: mỗi lần
    # `--force` ghi đè state.json chỉ còn doc hiện tại, làm mất lịch sử — phát hiện thật khi
    # soát lại index sau nhiều lần capture/xoá thử, thấy chunk "ma" dù state.json đã "sạch".
    current_doc_ids = {doc.doc_id for doc in all_docs}
    deleted_doc_ids = store.list_doc_ids() - current_doc_ids if not config_changed else set()

    if not changed_docs and not deleted_doc_ids:
        print("Không có tài liệu mới/thay đổi/xoá — index đã cập nhật.")
        append_build_event(
            settings.cache_dir,
            provider=provider,
            local_model=local_model,
            force=force,
            build_graph=build_graph,
            changed_docs=0,
            deleted_docs=0,
            n_chunks=0,
            duration_seconds=time.perf_counter() - started,
            success=True,
        )
        return 0

    if config_changed or force:
        store.reset()
    print(f"Dùng embedding provider: {provider}" + (f" (model={local_model})" if local_model else "") + f" dim={embedder.dim}")

    # Graph layer mặc định TẮT (đổi 2026-08-04) — kiểm chứng thực tế cho thấy extraction hiện
    # chưa đủ tin cậy (xem README mục Trạng thái: 2 lần build độc lập gần như không trùng
    # quan hệ nào). Bật lại bằng build_index(..., build_graph=True) / `graphrag build --graph`
    # khi cần thử nghiệm, không chạy ngầm định để khỏi tốn API call cho graph không dùng tới.
    graph_store = _get_graph_store(settings) if build_graph else None
    resolver = EntityResolver() if graph_store is not None else None
    ontology_edges = load_ontology()[1] if graph_store is not None else {}
    upserted_node_ids: set[str] = set()

    if deleted_doc_ids:
        # config_changed đã store.reset() ở trên (xoá sạch bảng) nên deleted_doc_ids luôn rỗng
        # trong nhánh đó — vòng lặp dưới đây chỉ thật sự chạy khi build bình thường/--force mà
        # KHÔNG đổi provider.
        for doc_id in deleted_doc_ids:
            store.delete_by_doc_id(doc_id)
            if graph_store is not None:
                graph_store.delete_by_doc_id(doc_id)
            state.pop(doc_id, None)
        print(f"Đã xoá {len(deleted_doc_ids)} tài liệu không còn tồn tại khỏi index.")

    n_chunks = 0
    for doc in changed_docs:
        chunks = build_chunks(doc, settings)
        if not chunks:
            print(f"  {doc.rel_path}: bỏ qua (không cắt được chunk nào)")
            continue
        vectors = embedder.embed([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector
        store.delete_by_doc_id(doc.doc_id)
        store.upsert(chunks)
        n_chunks += len(chunks)
        state[doc.doc_id] = doc.content_hash
        print(f"  {doc.rel_path}: {len(chunks)} chunk")

        if graph_store is not None:
            graph_store.delete_by_doc_id(doc.doc_id)

            project_id = resolver.resolve("Project", doc.project)
            doc_node = GraphNode(
                id=doc.doc_id,
                type="Document",
                name=doc.rel_path,
                props={"doc_type": doc.doc_type, "sensitivity": doc.sensitivity},
            )
            doc_edges = [
                GraphEdge(
                    from_id=doc.doc_id, from_type="Document",
                    to_id=project_id, to_type="Project",
                    relation="BELONGS_TO", doc_id=doc.doc_id,
                )
            ]

            chunk_nodes: list[GraphNode] = []
            chunk_edges: list[GraphEdge] = []
            for chunk in chunks:
                nodes_c, edges_c = _extract_graph_for_chunk(chunk, doc, settings, resolver, ontology_edges)
                chunk_nodes.extend(nodes_c)
                chunk_edges.extend(edges_c)

            # Entity node (Project/System/Person/...) phải ghi trước — MERGE cạnh cần node
            # đích đã tồn tại trong Kuzu, nếu không MATCH không khớp gì và cạnh bị bỏ qua lặng lẽ.
            new_entity_nodes = [n for n in resolver.nodes() if n.id not in upserted_node_ids]
            graph_store.upsert_nodes(new_entity_nodes)
            upserted_node_ids.update(n.id for n in new_entity_nodes)

            graph_store.upsert_nodes([doc_node, *chunk_nodes])
            graph_store.upsert_edges([*doc_edges, *chunk_edges])
            print(f"    graph: +{len(new_entity_nodes)} entity mới, {len(doc_edges) + len(chunk_edges)} cạnh")

    save_state(settings.cache_dir, state)
    save_index_meta(settings.cache_dir, provider, embedder.dim, local_model=local_model)
    append_build_event(
        settings.cache_dir,
        provider=provider,
        local_model=local_model,
        force=force,
        build_graph=build_graph,
        changed_docs=len(changed_docs),
        deleted_docs=len(deleted_doc_ids),
        n_chunks=n_chunks,
        duration_seconds=time.perf_counter() - started,
        success=True,
    )
    deleted_note = f", đã xoá {len(deleted_doc_ids)} tài liệu cũ" if deleted_doc_ids else ""
    print(f"Đã index {len(changed_docs)} tài liệu, {n_chunks} chunk{deleted_note}.")
    return n_chunks
