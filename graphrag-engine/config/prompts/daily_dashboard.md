Bạn tổng hợp tình hình công việc của lane **{{lane}}** từ các nguồn tri thức nội bộ (Zalo, chat-note, tài liệu/email) dưới đây, để dựng 1 trang dashboard buổi sáng.

Đây là bản tổng hợp NHANH (1 lần đọc, không đối chiếu chéo nhiều vòng) — vì vậy PHẢI thận trọng: chỉ ghi những gì có căn cứ rõ trong ngữ cảnh, thà để trống/ghi "chưa rõ" còn hơn bịa.

Quy tắc trích xuất từng task:
- **pic**: chỉ điền tên người khi rõ ràng ai được @mention/giao việc trực tiếp trong đoạn liên quan — không suy diễn từ vai trò/phòng ban. Không rõ thì để `"Chưa rõ"`.
- **support**: đúng nghĩa người HỖ TRỢ (đồng nghiệp cùng làm/cùng họp với PIC) — KHÔNG dùng cho đối tác phụ thuộc, công cụ, hay người phê duyệt/giao việc. Không có thì để `"Chưa nêu rõ"`.
- **priority**: `"Cao"` (chiến lược/tuân thủ/cam kết gấp/blocker nghiêm trọng) | `"TB"` | `"Thấp"`.
- **dependency**: điều kiện phải xong trước, nếu không nêu thì `"Không nêu rõ"`; nếu rõ ràng không phụ thuộc gì thì `"Không có"`.
- **status**: `"Mới mở"` | `"Đang xử lý"` | `"Hoàn thành"` | `"Blocked"` (Blocked chỉ khi ngữ cảnh nói rõ đang chờ 1 việc/người khác).
- **deadline**: quy đổi ngày tương đối ("mai", "tuần sau", "T5"...) sang ngày thật dựa theo ngày của đoạn nguồn (ghi kèm trong `[...]` đầu mỗi đoạn). Nếu ngữ cảnh chỉ nhắc lịch họp chứ không cam kết ngày hoàn thành thì ghi `"Chưa chốt mốc"`.
- **channel**: `"Zalo"` | `"Chat-note"` | `"Tài liệu"` | `"Email"` — suy từ loại nguồn của đoạn bạn dùng (xem tiền tố đường dẫn: `_zalo-notes/` → Zalo, `_chat-notes/` → Chat-note, còn lại → Tài liệu/Email tuỳ nội dung).
- **source_path**: copy đúng `source_path` (phần trong `[...]` đầu đoạn) bạn dùng làm căn cứ.

Quy tắc phần `risks` (rủi ro/vướng mắc nổi bật — không phải task nào cũng có risk riêng):
- Chỉ liệt kê rủi ro có căn cứ rõ (blocker đang chờ, đối tác chưa sẵn sàng, deadline sắp tới mà chưa xong, mâu thuẫn giữa các nguồn...).
- `severity`: `"Cao"` | `"TB"` | `"Thấp"`.

{{cio_note}}

Trả lời **DUY NHẤT 1 khối JSON hợp lệ**, đúng schema sau, không thêm text nào khác ngoài JSON (không markdown code fence):
{"summary": "2-4 câu tình hình chung của lane", "tasks": [{"task": "", "pic": "", "support": "", "priority": "", "dependency": "", "status": "", "deadline": "", "channel": "", "source_path": ""}], "risks": [{"description": "", "severity": "", "related_task": "", "source_path": ""}]}

---
Ngữ cảnh (mỗi đoạn bắt đầu bằng `[nguồn | ngày | source_path]`):

{{context}}
