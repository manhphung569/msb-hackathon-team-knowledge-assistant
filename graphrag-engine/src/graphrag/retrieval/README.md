# retrieval

Lõi của phần "Hybrid": kết hợp vector search và graph traversal.

- `vector_retriever.py` — top-k chunk theo cosine similarity với câu hỏi.
- `graph_retriever.py` — lấy entity xuất hiện trong top-k chunk, mở rộng k-hop quanh các entity đó để lấy thêm chunk/fact liên quan mà vector search có thể bỏ sót (vd câu hỏi multi-hop kiểu "hệ thống nào phụ thuộc vào MSBPay?").
- `fusion.py` — hợp nhất candidate từ 2 nguồn, loại trùng.
- `hybrid.py` — weighted rank fusion cho exact / keyword / vector candidate lists.
- `reranker.py` — rerank nhẹ, deterministic theo exact-term / keyword-term / metadata, dùng ngay trong query path trước khi pack context.
