# pipeline

Orchestration — nơi các module trên được ghép lại thành lệnh chạy được.

- `build_index.py` — chạy toàn bộ luồng ingest → chunk → embed → extract → ghi vào vector_store + graph_store. Hỗ trợ chạy full hoặc incremental (chỉ file đổi, nhờ `ingestion/watcher.py`).
- `query.py` — entrypoint hỏi-đáp: nhận câu hỏi → retrieval hybrid → generation → trả lời + trích dẫn.

`cli.py` (ở `src/graphrag/`) expose hai lệnh này ra command line: `graphrag build`, `graphrag query "..."`.
