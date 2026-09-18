# Kịch bản video demo — Team Knowledge Assistant

Tổng thời lượng mục tiêu: **3:30–4:00 phút**. Quay màn hình + giọng nói (không cần diễn, chỉ cần rõ ràng, đúng nhịp). Có thể quay từng đoạn riêng rồi ghép, không cần 1 lần liền mạch.

---

## Cảnh 1 — Vấn đề (0:00–0:25)

**Hình**: mặt người nói hoặc slide `problem` của pitch deck.

**Lời thoại** (gợi ý, chỉnh theo giọng tự nhiên của bạn):
> "Trong 1 dự án thật, tri thức nằm ở 4 nơi khác nhau: code trong repo, backlog trong Jira, tài liệu kiến trúc trong Confluence, và quyết định thật thì nằm trong đầu người vừa họp xong. Người mới vào phải hỏi lại người cũ. Người cũ nghỉ, tri thức đi theo luôn."

---

## Cảnh 2 — Giải pháp, kiến trúc (0:25–0:55)

**Hình**: slide `solution` + `architecture` của pitch deck (chuyển nhanh qua 2 slide).

**Lời thoại**:
> "Team Knowledge Assistant gộp cả 4 nguồn — source code, Jira, Confluence, và meeting/chat — vào đúng 1 index tìm kiếm ngữ nghĩa. Hỏi 1 câu bằng tiếng Việt tự nhiên, trả lời trích dẫn đúng nguồn."

---

## Cảnh 3 — Demo thật (0:55–2:45) — PHẦN QUAN TRỌNG NHẤT

**Chuẩn bị trước khi quay** (không quay phần này, chỉ chuẩn bị):
```bash
# Terminal 1
cd graphrag-engine
.venv\Scripts\graphrag serve --tenant-root ../tenants/demo/teamassistant --port 8000

# Terminal 2
cd workbench
.venv\Scripts\python -m workbench.server.main --port 8766
```
Mở sẵn `http://localhost:8766/vault/`, đăng nhập `admin`/`admin123`, để cửa sổ chat sẵn sàng trước khi bấm quay.

**Hình**: quay màn hình trực tiếp giao diện chat workbench.

**Bước 1 (0:55–1:10)** — Gõ câu hỏi:
> "Theo TKA-1, vì sao thông báo thanh toán bị mất âm thầm, code hiện tại xử lý retry thế nào, và team đã quyết định thay đổi gì trong cuộc họp để khắc phục?"

**Lời thoại lúc gõ**:
> "Đây là câu hỏi thật, chạm cả 4 nguồn cùng lúc — không có nguồn nào riêng lẻ trả lời đủ."

**Bước 2 (1:10–2:20)** — Chờ trả lời, con trỏ dừng ở phần trích dẫn nguồn.

**Lời thoại khi đọc câu trả lời** (đọc chậm, chỉ tay vào từng nguồn trên màn hình):
> "Câu trả lời trích dẫn đúng 4 nguồn: Jira TKA-1 cho biết đây là bug thật đang track; Confluence cho biết kiến trúc notify-service; source code cho thấy chính xác dòng nào xử lý retry; và ghi chú cuộc họp cho biết team đã quyết định nâng số lần thử và thêm cảnh báo Slack."

**Bước 3 (2:20–2:45)** — Lướt nhanh qua 1-2 câu hỏi phụ khác (tuỳ chọn, nếu còn thời gian) để cho thấy hệ thống không chỉ trả lời đúng 1 câu đã luyện sẵn — ví dụ hỏi riêng "MAX_RETRIES hiện tại là bao nhiêu?" để thấy trả lời ngắn gọn, đúng trọng tâm.

---

## Cảnh 4 — Vì sao đáng tin (2:45–3:10)

**Hình**: slide `reliability` của pitch deck.

**Lời thoại**:
> "Điểm khác biệt quan trọng: hệ thống không sinh ra bản tóm tắt code rồi lưu lại — source code luôn là nguồn duy nhất của sự thật. Mọi giải thích được tổng hợp mới ngay lúc hỏi, từ code hiện tại, không có gì bị đóng băng có thể lệch khỏi thực tế."

---

## Cảnh 5 — GreenNode + lời kết (3:10–3:45)

**Hình**: slide `greennode` rồi `thanks` của pitch deck.

**Lời thoại**:
> "Toàn bộ code đã sẵn sàng tích hợp GreenNode AI Agent Platform ở 6 điểm gọi LLM/embedding, đang chờ credential chính thức từ ban tổ chức để kích hoạt. Cảm ơn Ban Giám Khảo đã lắng nghe."

---

## Ghi chú kỹ thuật khi quay

- Nếu graphrag-engine trả lời chậm (embedding local lần đầu load model ~5-10s) — quay thử trước 1 lần để "làm nóng", đừng để khoảng chờ dài xuất hiện trong bản quay thật.
- Nếu muốn câu trả lời tiếng Việt tự nhiên hơn, đảm bảo `OPENAI_API_KEY` hoặc `ANTHROPIC_API_KEY` đã set trong `graphrag-engine/.env` (không dùng `provider_override="local"` — đó chỉ là chế độ test không cần key).
- Dự phòng nếu 1 nguồn không lên trong câu trả lời lúc quay: hỏi lại 1 lần (retrieval có thể lệch nhẹ theo cách diễn đạt câu hỏi) — không phải lỗi hệ thống, chỉ là exact-match ưu tiên khác đi.
