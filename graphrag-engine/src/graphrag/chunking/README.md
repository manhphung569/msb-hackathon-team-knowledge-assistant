# chunking

Cắt nội dung đã load thành các đoạn (chunk) phù hợp để embed và trích entity.

- `splitter.py` — chunking theo cấu trúc heading của markdown trước, fallback recursive theo độ dài nếu section quá lớn.
- `metadata.py` — gắn metadata cho mỗi chunk: `doc_id`, `chunk_id`, `source_path`, `section`, `project` (từ frontmatter nếu file có ghi rõ, không thì `settings.default_project` — đọc từ overlay `tenants/<org>/<ws>/config/settings.yaml` nếu tenant tự set, mặc định là tên thư mục tenant — xem `ingestion/loader.py`/`config.py`; không còn suy từ path kể từ khi tách `tenants/`, 2026-08-22), `sensitivity` (public/internal/confidential), `start_line`/`end_line` (số dòng trong file thật trên đĩa — cộng `doc.line_offset` để bù phần frontmatter đã bị `loader.py` strip khỏi `doc.text` trước khi chunk; dùng để Read(offset=start_line, limit=end_line-start_line+1) nhảy thẳng tới đúng đoạn thay vì đọc cả file).

`sensitivity` là field quan trọng nhất ở bước này — nó quyết định chunk có được gửi qua API cloud (embedding/LLM) hay bắt buộc xử lý bằng model local ở các bước sau.
