from __future__ import annotations

from ..config import COMMON_TENANT_ROOT, Settings
from ..embedding.embedder import get_embedder
from ..generation.answer_generator import generate_answer
from ..generation.context_builder import build_context
from ..graph_store import try_open as try_open_graph_store
from ..ingestion.dedup import load_index_meta
from ..people import expand_query_terms as expand_people_terms
from ..processes import expand_query_terms as expand_process_terms, has_explicit_process_code
from ..retrieval import graph_retriever
from ..retrieval.exact_retriever import retrieve_exact, should_use_exact_first
from ..retrieval.fusion import fuse_chunks
from ..retrieval.hybrid import fuse_ranked_lists
from ..retrieval.keyword_retriever import extract_keyword_terms, retrieve_keyword, should_use_keyword
from ..retrieval.reranker import rerank_chunks
from ..retrieval.vector_retriever import retrieve
from ..systems import expand_query_terms as expand_system_terms
from ..vector_store.lancedb_store import LanceDBStore

_PEOPLE_OPS_HINTS = (
    "ai ",
    "phu trach",
    "dau moi",
    "spoc",
    "review",
    "thu tuc",
    "van hanh",
    "tham gia",
    "handover",
)
_TIMELINE_HINTS = (
    "timeline",
    "tien do",
    "ke hoach",
    "moc",
    "deadline",
    "bao gio",
    "khi nao",
    "thoi diem",
)
_GENERIC_PROCESS_TERMS = {
    "quy",
    "trinh",
    "golive",
    "go",
    "live",
    "production",
    "uat",
    "release",
    "test",
    "ho",
    "so",
    "thu",
    "tuc",
    "review",
    "van",
    "hanh",
    "handover",
    "blocker",
    "project",
    "du",
    "an",
    "tiep",
    "nhan",
    "yeu",
    "cau",
    "phat",
    "trien",
    "cong",
    "nghe",
    "fast",
    "lane",
    "demand",
    "intake",
    "backlog",
    "sprint",
    "pending",
    "recent",
    "change",
}
_SHORT_ANCHOR_ALLOWLIST = {"ocr", "sdk", "tch", "cmp", "cdp", "api"}
# tenants/_common la nguon BO SUNG (methodology/strategy dung chung moi org, xem
# tenants/registry.yaml) - giam trong so tuong doi so voi tenant dang query de khong lan at ket
# qua rieng cua tenant do trong fuse_ranked_lists (Phase 5 federation, 2026-08-24).
_COMMON_WEIGHT_SCALE = 0.6


def _format_subgraph_facts(edges) -> str:
    facts = [e for e in edges if e.relation != "MENTIONS"]
    if not facts:
        return "(khong co - graph layer chua build, khong tim duoc entity lien quan, hoac chi co quan he MENTIONS)"
    lines = [
        f"- {e.from_name or e.from_id} {e.relation} {e.to_name or e.to_id}"
        + (f" - {e.evidence}" if e.evidence else "")
        for e in facts
    ]
    return "\n".join(lines)


def _select_planner_mode(question: str, variants: list[str], exact_mode: bool) -> str:
    word_count = len([part for part in question.split() if part.strip()])
    if exact_mode and len(variants) > 1:
        return "mixed"
    if exact_mode:
        return "exact"
    if len(variants) > 1 or word_count >= 4:
        return "mixed"
    return "semantic"


def _should_expand_process_terms(question: str, exact_mode: bool) -> bool:
    words = [part for part in question.split() if part.strip()]
    normalized = question.lower()
    if has_explicit_process_code(question):
        return True
    if exact_mode and len(words) <= 3:
        return False
    keyword_terms = extract_keyword_terms(question)
    anchor_terms = [
        term
        for term in keyword_terms
        if term not in _GENERIC_PROCESS_TERMS and (len(term) >= 4 or term in _SHORT_ANCHOR_ALLOWLIST)
    ]
    has_ops_or_timeline_hint = any(hint in normalized for hint in (*_PEOPLE_OPS_HINTS, *_TIMELINE_HINTS))
    # Query da co tin hieu project/ops cu the ("kiosk", "goldmark", "ocr", "handover"...)
    # thi khong nen tu bung sang QT.IT.019/009 generic chi vi co tu "golive".
    if len(anchor_terms) >= 2 or (anchor_terms and has_ops_or_timeline_hint):
        return False
    return True


def _collect_variant_results(question_variants: list[str], limit: int, fetcher) -> list:
    seen: set[str] = set()
    results: list = []
    for variant in question_variants:
        if len(results) >= limit:
            break
        for chunk in fetcher(variant):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            results.append(chunk)
            if len(results) >= limit:
                break
    return results


def _resolve_common_settings(settings: Settings) -> Settings | None:
    """Tra ve Settings cho tenants/_common de federation (Phase 5, 2026-08-24), hoac None neu
    khong nen federate: dang query chinh _common (tranh tu-federate voi chinh no), hoac _common
    chua co index (chua chay `graphrag build` cho _common - khong coi la loi, chi bo qua)."""
    tenant_root = settings.normalized_dir.parent
    if tenant_root.resolve() == COMMON_TENANT_ROOT.resolve():
        return None
    if not (COMMON_TENANT_ROOT / "data" / "cache" / "index_meta.json").exists():
        return None
    return Settings.load(tenant_root=COMMON_TENANT_ROOT)


def _load_embedder_and_store(
    settings: Settings, reuse_embedder=None, reuse_signature: tuple | None = None
):
    """Tra ve (meta, embedder, store) cho 1 tenant, hoac (None, None, None) neu chua co index.
    `reuse_embedder`/`reuse_signature`: tai su dung embedder da tao san neu cung (provider,
    local_model) - nap model embedding local (fastembed/ONNX) do luong thuc te ton ~4.7s/lan
    (xem review Phase 5, 2026-08-25; da kiem chung chay song song qua ThreadPoolExecutor KHONG
    giup gi - ONNX session init khong parallelize tot qua Python thread, thâm chi hơi cham hon).
    Vi engine dung 1 bo config embedding CHUNG cho moi tenant (config.py Settings.load chi cho
    tenant overlay 2 doi sensitivity_rules/visibility_rules/default_project, KHONG cho embedding),
    tenant chinh + _common hom nay LUON cung signature - tai su dung an toan vi embedder khong
    giu state rieng theo tenant, chi la 1 ham embed(texts) -> vectors thuan tuy."""
    meta = load_index_meta(settings.cache_dir)
    if meta is None:
        return None, None, None
    signature = (meta["provider"], meta.get("local_model"))
    if reuse_embedder is not None and reuse_signature == signature:
        embedder = reuse_embedder
    else:
        embedder = get_embedder(meta["provider"], settings, local_model=meta.get("local_model"))
    store = LanceDBStore(settings.vector_index_dir, dim=meta["dim"])
    return meta, embedder, store


def _retrieve_from_store(
    question: str,
    question_variants: list[str],
    embedder,
    store,
    settings: Settings,
    guest: bool,
    exact_mode: bool,
    planner_mode: str,
    keyword_mode: bool,
    weight_scale: float = 1.0,
    label_suffix: str = "",
):
    """Chay exact/exact-variant/keyword/vector retrieval tren dung 1 tenant (embedder/store da
    duoc caller nap san qua _load_embedder_and_store - xem ham do de biet ly do tach rieng).
    `weight_scale`/`label_suffix` dung khi goi cho tenant BO SUNG (_common) de phan biet voi
    tenant chinh trong retrieval_trace va khong lan at ket qua tenant chinh khi fuse chung.
    Tra ve (weighted_lists, retrieval_trace)."""
    filters = {"visibility": "shared"} if guest else None

    trace: list[str] = []
    weighted_lists: list[tuple[str, float, list]] = []

    exact_chunks: list = []
    if exact_mode:
        trace.append(f"exact{label_suffix}")
        exact_chunks = retrieve_exact(question, store, k=settings.vector_top_k, filters=filters)
    exact_enough = len(exact_chunks) >= min(3, settings.vector_top_k)
    if exact_chunks:
        weighted_lists.append((f"exact{label_suffix}", 4.0 * weight_scale, exact_chunks))

    if planner_mode == "mixed":
        variant_exact_chunks = _collect_variant_results(
            [variant for variant in question_variants if variant != question],
            settings.vector_top_k,
            lambda variant: retrieve_exact(variant, store, k=settings.vector_top_k, filters=filters),
        )
        if variant_exact_chunks:
            trace.append(f"exact-variant{label_suffix}")
            weighted_lists.append((f"exact-variant{label_suffix}", 3.5 * weight_scale, variant_exact_chunks))

    if keyword_mode and (planner_mode != "exact" or not exact_enough):
        keyword_chunks = _collect_variant_results(
            question_variants,
            settings.vector_top_k,
            lambda variant: retrieve_keyword(variant, store, k=settings.vector_top_k, filters=filters),
        )
        if keyword_chunks:
            trace.append(f"keyword{label_suffix}")
            weighted_lists.append((f"keyword{label_suffix}", 2.0 * weight_scale, keyword_chunks))

    need_vector = len(exact_chunks) < settings.vector_top_k and (planner_mode != "exact" or not exact_enough)
    if need_vector:
        vector_chunks = _collect_variant_results(
            question_variants,
            settings.vector_top_k,
            lambda variant: retrieve(variant, embedder, store, k=settings.vector_top_k, filters=filters),
        )
        if vector_chunks:
            trace.append(f"vector{label_suffix}")
            weighted_lists.append((f"vector{label_suffix}", 3.0 * weight_scale, vector_chunks))

    return weighted_lists, trace


def query(
    question: str,
    settings: Settings,
    generate: bool = True,
    use_graph: bool = False,
    guest: bool = False,
    federate_common: bool = True,
) -> dict:
    meta, embedder, store = _load_embedder_and_store(settings)
    if meta is None:
        raise RuntimeError("Chua co index - chay `graphrag build` truoc.")
    signature = (meta["provider"], meta.get("local_model"))

    exact_mode = should_use_exact_first(question)
    process_variants = (
        expand_process_terms(question, settings) if _should_expand_process_terms(question, exact_mode) else [question]
    )
    question_variants = list(
        dict.fromkeys(
            [
                *expand_people_terms(question, settings),
                *expand_system_terms(question, settings),
                *process_variants,
            ]
        )
    )
    keyword_mode = should_use_keyword(question)
    planner_mode = _select_planner_mode(question, question_variants, exact_mode)

    weighted_lists, retrieval_trace = _retrieve_from_store(
        question, question_variants, embedder, store, settings, guest, exact_mode, planner_mode, keyword_mode
    )

    # Federation (Phase 5, 2026-08-24): tenants/_common luon duoc tra cung tenant dang query, tru
    # khi tat qua federate_common=False hoac dang query chinh _common - xem _resolve_common_settings.
    common_settings = _resolve_common_settings(settings) if federate_common else None
    if common_settings is not None:
        common_meta, common_embedder, common_store = _load_embedder_and_store(
            common_settings, reuse_embedder=embedder, reuse_signature=signature
        )
        if common_meta is not None:
            common_lists, common_trace = _retrieve_from_store(
                question,
                question_variants,
                common_embedder,
                common_store,
                common_settings,
                guest,
                exact_mode,
                planner_mode,
                keyword_mode,
                weight_scale=_COMMON_WEIGHT_SCALE,
                label_suffix="-common",
            )
            weighted_lists = weighted_lists + common_lists
            retrieval_trace = retrieval_trace + common_trace

    # RUI RO LY THUYET (chua xay ra thuc te, xem review Phase 5 2026-08-25): chunk_id = sha1(doc_id:i)
    # va doc_id = sha1(rel_path) - CHI tinh theo path tuong doi trong tenant do, khong namespace theo
    # tenant. Neu tenant dang query va _common TRUNG rel_path 1 file (vd ca 2 cung co "MoM.md"),
    # chunk_id se DUNG, va fuse_ranked_lists (dedup theo chunk_id) se gop nham lam 1 - noi dung cua
    # ben den SAU bi mat, chi con diem so cong don. Hien tai 5 file cua _common co ten rieng biet,
    # khong trung voi tenant nao nen chua xay ra - neu tenant/_common ton tai file trung ten tuong
    # doi that, can namespace lai chunk_id truoc khi fuse (chua lam vi hien khong can thiet).
    chunks = fuse_ranked_lists(weighted_lists, settings.vector_top_k) if weighted_lists else []

    subgraph_edges = []
    if use_graph and chunks:
        graph_store = try_open_graph_store(settings.graph_db_dir)
        if graph_store is not None:
            subgraph_edges = graph_retriever.expand(
                [c.chunk_id for c in chunks], graph_store, hops=settings.graph_hops
            )
            chunks = fuse_chunks(chunks, subgraph_edges, store, top_n=settings.rerank_top_n)

    chunks = rerank_chunks(question, chunks, top_n=settings.rerank_top_n)

    context, context_chunk_ids = build_context(chunks)

    result = {
        "chunks": chunks,
        "context": context,
        "context_chunk_ids": context_chunk_ids,
        "answer": None,
        "subgraph_edges": subgraph_edges,
        "planner_mode": planner_mode,
        "retrieval_trace": retrieval_trace,
    }
    if generate and chunks:
        result["answer"] = generate_answer(
            question, context, settings, subgraph_facts=_format_subgraph_facts(subgraph_edges)
        )
    return result
