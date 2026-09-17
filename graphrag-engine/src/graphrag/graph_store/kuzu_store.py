from __future__ import annotations

import json
from pathlib import Path

import kuzu

from ..types import GraphEdge, GraphNode
from . import load_ontology
from .base import GraphStore


class KuzuStore(GraphStore):
    """Graph store MVP — embedded, file-based (data/graph_db/), Cypher qua Kuzu.
    Schema (node/rel table) sinh động từ config/graph_schema.yaml lúc khởi tạo, idempotent
    (bỏ qua table đã tồn tại) để mở lại db nhiều lần (mỗi lần `graphrag build`) không lỗi.
    Property ngoài id/name dùng chung 1 cột `props` (JSON) thay vì tạo cột riêng cho từng
    node type — quy mô cá nhân không cần schema chặt hơn, và tránh phải migrate cột khi
    graph_schema.yaml đổi danh sách props."""

    def __init__(self, db_dir: Path):
        db_dir.parent.mkdir(parents=True, exist_ok=True)
        # Kuzu tự tạo cấu trúc file tại db_dir và lỗi nếu path đã là 1 thư mục có sẵn — dọn
        # thư mục placeholder rỗng (scaffold ban đầu của ARCHITECTURE.md tạo data/graph_db/
        # rỗng trước khi có code, không phải dữ liệu thật) trước khi mở/tạo db lần đầu.
        if db_dir.is_dir() and not any(db_dir.iterdir()):
            db_dir.rmdir()
        self._db = kuzu.Database(str(db_dir))
        self._conn = kuzu.Connection(self._db)
        self._node_types, self._edges = load_ontology()
        self._ensure_schema()

    def _existing_tables(self) -> set[str]:
        res = self._conn.execute("CALL show_tables() RETURN *")
        names = set()
        while res.has_next():
            row = res.get_next()
            names.add(row[1])
        return names

    def _ensure_schema(self) -> None:
        existing = self._existing_tables()
        for node_type in self._node_types:
            if node_type in existing:
                continue
            if node_type == "Chunk":
                self._conn.execute("CREATE NODE TABLE Chunk(id STRING, doc_id STRING, PRIMARY KEY(id))")
            else:
                self._conn.execute(
                    f"CREATE NODE TABLE {node_type}(id STRING, name STRING, props STRING, PRIMARY KEY(id))"
                )
        existing = self._existing_tables()
        for relation, (from_types, to_types) in self._edges.items():
            if relation in existing:
                continue
            pairs = ", ".join(f"FROM {f} TO {t}" for f in from_types for t in to_types)
            self._conn.execute(f"CREATE REL TABLE {relation}({pairs}, evidence STRING, doc_id STRING)")

    def upsert_nodes(self, nodes: list[GraphNode]) -> None:
        for n in nodes:
            if n.type == "Chunk":
                self._conn.execute(
                    "MERGE (c:Chunk {id: $id}) ON CREATE SET c.doc_id = $doc_id ON MATCH SET c.doc_id = $doc_id",
                    {"id": n.id, "doc_id": n.doc_id},
                )
                continue
            self._conn.execute(
                f"MERGE (e:{n.type} {{id: $id}}) "
                "ON CREATE SET e.name = $name, e.props = $props "
                "ON MATCH SET e.name = $name, e.props = $props",
                {"id": n.id, "name": n.name, "props": json.dumps(n.props, ensure_ascii=False)},
            )

    def upsert_edges(self, edges: list[GraphEdge]) -> None:
        for e in edges:
            self._conn.execute(
                f"MATCH (a:{e.from_type} {{id: $f}}), (b:{e.to_type} {{id: $t}}) "
                f"MERGE (a)-[r:{e.relation}]->(b) "
                "SET r.evidence = $evidence, r.doc_id = $doc_id",
                {"f": e.from_id, "t": e.to_id, "evidence": e.evidence, "doc_id": e.doc_id},
            )

    def delete_by_doc_id(self, doc_id: str) -> None:
        # Chỉ xoá cạnh/chunk gắn với doc này — entity (Project/System/Person...) có thể được
        # nhắc tới ở nhiều doc khác nên không xoá, chỉ cập nhật lại khi upsert lần build kế tiếp.
        for relation in self._edges:
            self._conn.execute(
                f"MATCH ()-[r:{relation} {{doc_id: $doc_id}}]->() DELETE r", {"doc_id": doc_id}
            )
        self._conn.execute("MATCH (c:Chunk {doc_id: $doc_id}) DETACH DELETE c", {"doc_id": doc_id})
        self._conn.execute("MATCH (d:Document {id: $doc_id}) DETACH DELETE d", {"doc_id": doc_id})

    def k_hop(self, entity_ids: list[str], hops: int) -> list[GraphEdge]:
        """BFS thủ công qua nhiều query 1-hop thay vì 1 query variable-length path — đơn giản
        hơn để trích đúng relation/evidence/node type ở từng cạnh (Kuzu trả path dạng list
        quan hệ lồng nhau, khó tách bằng Python API hơn là lặp tay), và corpus quy mô cá nhân
        (~vài nghìn chunk) không cần tối ưu thành 1 query.

        Dùng 2 query có hướng (outgoing/incoming) thay vì 1 query vô hướng `-[r]-`: Kuzu không
        có hàm `startNode`/`endNode` để lấy chiều thật của cạnh khi match vô hướng, nên match
        vô hướng sẽ báo cùng 1 cạnh 2 lần với from/to đảo ngược nhau tuỳ node nào là anchor —
        sai lệch chiều quan hệ (vd DEPENDS_ON) khi format thành câu cho LLM."""
        seen_edges: set[tuple[str, str, str]] = set()
        edges: list[GraphEdge] = []
        frontier = set(entity_ids)
        visited = set(entity_ids)
        for _ in range(max(1, hops)):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for entity_id in frontier:
                rows = []
                res_out = self._conn.execute(
                    "MATCH (a {id: $id})-[r]->(b) "
                    "RETURN a.id, a.name, label(r), r.evidence, r.doc_id, b.id, label(b), b.name",
                    {"id": entity_id},
                )
                while res_out.has_next():
                    rows.append(res_out.get_next())
                res_in = self._conn.execute(
                    "MATCH (a {id: $id})<-[r]-(b) "
                    "RETURN b.id, b.name, label(r), r.evidence, r.doc_id, a.id, label(a), a.name",
                    {"id": entity_id},
                )
                while res_in.has_next():
                    rows.append(res_in.get_next())

                for from_id, from_name, relation, evidence, doc_id, to_id, to_type, to_name in rows:
                    key = (from_id, relation, to_id)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append(
                            GraphEdge(
                                from_id=from_id,
                                from_type="",
                                to_id=to_id,
                                to_type=to_type,
                                relation=relation,
                                evidence=evidence or "",
                                doc_id=doc_id or "",
                                from_name=from_name or "",
                                to_name=to_name or "",
                            )
                        )
                    other = to_id if from_id == entity_id else from_id
                    if other not in visited:
                        next_frontier.add(other)
            visited |= next_frontier
            frontier = next_frontier
        return edges

    def reset(self) -> None:
        for relation in self._edges:
            try:
                self._conn.execute(f"MATCH ()-[r:{relation}]->() DELETE r")
            except RuntimeError:
                pass
        for node_type in self._node_types:
            try:
                self._conn.execute(f"MATCH (n:{node_type}) DETACH DELETE n")
            except RuntimeError:
                pass
