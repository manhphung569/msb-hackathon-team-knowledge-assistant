# normalize-engine

Chuyển `../artifacts/` (pdf, docx, pptx, xlsx, msg) sang `../normalized/` (`.md`, cùng đường dẫn tương đối, chỉ đổi extension). Xem lý do trong `../bang-file-type-ai-tool.md`.

## Cài đặt

```bash
cd normalize-engine
python -m venv .venv
./.venv/Scripts/pip install -e .
cp .env.example .env      # chỉ cần nếu dùng `normalize ocr` — điền OPENAI_API_KEY
```

## Chạy

```bash
./.venv/Scripts/normalize run              # convert file mới/đổi, không tốn API (dedup theo hash)
./.venv/Scripts/normalize run --force       # convert lại toàn bộ

./.venv/Scripts/normalize ocr               # OCR các PDF scan/ảnh (mà `run` báo needs_ocr) qua OpenAI vision API — TỐN PHÍ
./.venv/Scripts/normalize ocr --model gpt-5.4 --force
```

`normalize run` và `normalize ocr` dùng chung 1 cache (`data/cache/convert_state.json`) — file đã OCR xong sẽ không bị convert lại ở lần chạy sau trừ khi nội dung gốc đổi hoặc dùng `--force`.

## Giới hạn hiện tại

- `normalize ocr` gửi ảnh từng trang PDF lên OpenAI — nội dung nội bộ MSB (hợp đồng, biên bản nghiệm thu, hoá đơn...) sẽ rời máy cá nhân. Đã được người dùng xác nhận chấp nhận cho batch OCR này; cân nhắc lại nếu áp dụng cho tài liệu nhạy cảm hơn sau này.
- Kiểm tra `--model` còn được OpenAI hỗ trợ trước khi chạy hàng loạt — model vision có thể đổi theo thời gian.
- `.msg`: chỉ trích subject/from/to/date/body + liệt kê tên file đính kèm, chưa tự giải nén nội dung file đính kèm.
- `.xlsx`/`.pptx` có bảng phức tạp (merge cell, nhiều cấp) → convert sang markdown table đơn giản, có thể mất một phần cấu trúc.
