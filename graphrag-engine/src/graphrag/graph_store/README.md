# graph_store

Interface lưu & truy vấn graph.

- `base.py` — interface chung: `upsert_nodes`, `upsert_edges`, `k_hop(entity_id, hops, edge_types)`.
- `kuzu_store.py` — MVP: embedded, file-based (`data/graph_db/`), hỗ trợ Cypher, không cần server.
- `neo4j_store.py` — dùng khi scale: multi-user, có Neo4j Browser/Bloom để người không kỹ thuật tự xem graph.

Migrate qua `scripts/migrate_to_neo4j.py` khi cần.
