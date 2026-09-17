---
source: chat-capture
date: 2026-09-19
project: teamassistant
doc_type: mom
reliability: cao
basis: Quyết định chốt trực tiếp trong buổi sprint planning của team notify-service, có 3 người
  tham dự cùng xác nhận ngay trong cuộc họp (không phải nghe kể lại).
corroborated_by: [Manh, Nam, PM Hương]
---

> **Độ tin cậy: Cao** — quyết định chốt trực tiếp trong sprint planning, 3 người tham dự cùng
> xác nhận (Manh, Nam, PM Hương), không phải nghe kể lại.

## Nội dung

**Cuộc họp**: Sprint Planning — team notify-service, 2026-09-19.
**Tham dự**: Manh, Nam, PM Hương.

### Bối cảnh

Bug TKA-1 (thông báo thanh toán mất âm thầm sau khi hết retry) được đưa ra thảo luận đầu
buổi. Nam trình bày lại luồng xử lý hiện tại trong `notify-service/retry.py`: retry tối đa 3
lần, hết retry chỉ log lỗi chứ không bắn cảnh báo nào ra ngoài.

### Quyết định (để khắc phục TKA-1)

1. **Nâng `MAX_RETRIES` từ 3 lên 5** — giảm khả năng thông báo bị bỏ cuộc quá sớm khi gateway
   chỉ lỗi thoáng qua (transient error).
2. **Thêm cảnh báo Slack** khi hết retry mà vẫn thất bại — bắn message vào kênh
   `#notify-service-alerts` kèm `payment_id` để đội vận hành biết ngay, không phải đợi khách
   hàng gọi lên hỏi.
3. **Owner**: Nam — chịu trách nhiệm implement cả 2 thay đổi trên, target xong trước buổi demo.

### Việc tiếp theo

- Nam cập nhật `retry.py`: đổi `MAX_RETRIES = 5`, thêm lời gọi Slack webhook trong nhánh hết
  retry (chỗ hiện đang chỉ có `logger.error(...)`).
- PM Hương theo dõi tiến độ, xác nhận lại với bên vận hành khi thay đổi lên production.
