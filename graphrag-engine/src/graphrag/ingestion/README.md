# ingestion

Đọc dữ liệu nguồn từ `../../../normalized/` (lớp text/markdown đã chuẩn hoá).

- `loader.py` — duyệt cây thư mục `normalized/`, sinh `doc_id` ổn định (hash theo path + nội dung).
- `watcher.py` — phát hiện file mới/đổi để re-index tăng dần (incremental), tránh build lại toàn bộ.
- `dedup.py` — bỏ qua file không đổi dựa trên hash đã lưu ở lần index trước (`data/cache/`).

Không đọc trực tiếp từ `artifacts/` — mọi thứ phải đi qua `normalized/` trước, để hệ thống không phụ thuộc vào khả năng đọc từng loại file gốc (xem `bang-file-type-ai-tool.md`).
