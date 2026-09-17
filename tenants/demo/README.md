# tenants/demo — Hackathon submission tenant(s)

**Mọi tenant dưới `tenants/demo/` chỉ chứa nội dung giả lập (synthetic)** — dựng riêng cho
MSB AI Hackathon 2026 (track "AI FOR MY TEAM" — Team Knowledge Assistant), KHÔNG chứa bất kỳ
dữ liệu thật nào của MSB/TNTalent/TNEX (đúng luật cuộc thi).

**Không copy/paste nội dung từ `tenants/msb/*`, `tenants/minmo/*`, hay `tenants/_dept/*` vào
đây** dưới bất kỳ hình thức nào (kể cả để "làm mẫu nhanh") — kể cả tên dự án, tên hệ thống,
tên người thật. Mọi fact trong `tenants/demo/` phải là hư cấu 100%, kể cả khi lấy cảm hứng
cấu trúc từ dữ liệu thật.

## `tenants/demo/teamassistant`

Kịch bản demo "NotifyHub retry bug" — 3 trụ cột tri thức hội tụ vào 1 index:
- **Source code**: `normalized/source-code/notify-service/retry.md` (transcribe thủ công từ
  `artifacts/source-code/notify-service/retry.py`, xem frontmatter `converter:
  manual-code-transcription`).
- **Jira backlog**: `normalized/_jira-notes/` — sync từ Jira Cloud site giả lập (project `TKA`)
  qua `scripts/jira_sync.py`, không phải dữ liệu Jira thật của MSB.
- **Meetings/chat/OCR**: `normalized/_chat-notes/`, `normalized/_zalo-notes/` — MoM giả lập +
  1 capture OCR thật qua `zalo-capture-extension` (ảnh whiteboard giả lập, không phải cuộc họp
  MSB thật).

Xem kế hoạch đầy đủ tại plan file của phiên làm việc tạo ra tenant này (2026-09-17).
