# File cần OCR thủ công

Không còn file nào — 13 PDF scan trước đây đã được OCR qua `normalize ocr` (OpenAI vision,
`gpt-4o-mini`) lúc 2026-08-03T14:16 (xem frontmatter `converter: pdf-ocr:gpt-4o-mini` trong
các file `.md` tương ứng ở `normalized/**/`). `normalize run` xác nhận toàn bộ 35 file
convertible trong `artifacts/` (pdf/docx/pptx/xlsx/msg) đều đã có bản `.md` tương ứng.

Nếu `artifacts/` có thêm PDF scan mới sau này, chạy `normalize run` trước — file nào không
trích được text sẽ báo `needs_ocr`, rồi chạy `normalize ocr` để xử lý riêng.
