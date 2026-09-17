# vector_store

Interface lưu & tìm kiếm vector, có thể swap implementation mà không đổi code gọi.

- `base.py` — interface chung: `upsert(chunks)`, `search(query_vector, k, filters)`.
- `lancedb_store.py` — MVP: embedded, file-based (`data/vector_index/`), không cần server/Docker.
- `qdrant_store.py` — dùng khi scale lên nhiều người dùng: self-host qua Docker, filter mạnh hơn, hỗ trợ concurrent write.

Migrate giữa hai implementation qua `scripts/migrate_to_qdrant.py` khi cần.
