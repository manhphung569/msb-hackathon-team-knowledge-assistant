# generation

- `context_builder.py` — ghép chunk text + subgraph fact (dạng `Entity A --[quan hệ]--> Entity B`) vào prompt, trong giới hạn token budget, ưu tiên theo điểm rerank. `build_context()` trả về `(context, included_chunk_ids)` — `included_chunk_ids` cho caller (CLI, API) biết chunk nào thực sự lọt vào ngân sách 6000 token, phân biệt với chunk bị bỏ khi vượt ngân sách (trước 2026-08-06 không có cách nào biết, xem `cli.py` mục `--show-context`).
- `answer_generator.py` — gọi Claude sinh câu trả lời, luôn kèm trích dẫn nguồn (`source_path`, section) để người dùng verify lại tài liệu gốc.
