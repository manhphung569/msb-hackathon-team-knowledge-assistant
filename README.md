# Team Knowledge Assistant — MSB AI Hackathon 2026

**Track:** AI FOR MY TEAM. Team: Phùng Đức Mạnh (DM&UDCNS) + Nguyễn Văn Bằng Nam (PTGPQLBH).

Một trợ lý tri thức hội tụ 4 nguồn phân mảnh — **source code**, **Jira backlog**,
**Confluence (BRD/kiến trúc/thiết kế)**, và **meeting/chat/OCR** — vào **1 index tìm kiếm ngữ
nghĩa duy nhất**, trả lời qua 1 giao diện chat có trích dẫn nguồn.

> ⚠️ Toàn bộ dữ liệu trong `tenants/demo/` là **giả lập 100%** (kịch bản "NotifyHub retry bug")
> — không chứa dữ liệu thật của MSB/TNTalent/TNEX. Xem `tenants/demo/README.md`.

## Kiến trúc

```
[source code]  [Jira Cloud]  [Confluence]  [Zalo chat + OCR / meeting notes]
      │              │             │                    │
      │       jira_sync.py  confluence_sync.py   zalo-capture-extension
      │       (+ tải đính kèm    │                       │
      │        Jira -> normalize)│                       │
      └──────────────┴───────────┴───────────┬───────────┘
                                          ▼
                    tenants/demo/teamassistant/normalized/  (Markdown, có frontmatter)
                                          │
                                  graphrag build
                                          ▼
                              LanceDB vector index (graphrag-engine)
                                          │
                                graphrag serve — /query API
                                          │
                                          ▼
                        workbench — chat UI đa người dùng (rewired để
                        gọi /query thay vì tự grep file)
```

- **graphrag-engine/** — engine index + truy vấn ngữ nghĩa (LanceDB), FastAPI `serve` (port 8000).
- **normalize-engine/** — convert file thô (pdf/docx/pptx/xlsx/msg/eml/html + OCR) thành Markdown.
- **workbench/** — app chat đa người dùng + backlog CRUD (port 8766), đã rewire để dùng chung
  index của graphrag-engine thay vì tự tìm kiếm bằng keyword-grep riêng — xem
  `workbench/server/graphrag_client.py` và `workbench/SMOKE_TEST_HACKATHON.md`.
- **zalo-capture-extension/** — Chrome extension bắt chat/ảnh Zalo, OCR local (Tesseract), ghi
  thẳng vào index qua `graphrag serve`.
- **scripts/jira_sync.py** — đồng bộ issue Jira Cloud (REST API v3) thành note trong index, kèm
  tải file đính kèm (pdf/docx/pptx/xlsx/msg/eml/html tự convert qua `normalize run`).
- **scripts/confluence_sync.py** — đồng bộ trang Confluence (BRD/URD/SRS/kiến trúc/thiết kế)
  thành note trong index — dùng chung credential với Jira nếu cùng site Atlassian.
- **scripts/sync_hackathon_submission.py** (chạy trong `pdlc-vault`, không phải ở đây) — cơ chế
  giữ repo này khớp với `pdlc-vault` (repo dev, có dữ liệu MSB thật) mỗi khi có cập nhật, kèm
  gate tự động chặn rò rỉ dữ liệu thật trước khi commit.

## Setup nhanh

```bash
# 1) graphrag-engine
cd graphrag-engine
python -m venv .venv
.venv\Scripts\pip install -e ".[api]"
copy .env.example .env    # điền ít nhất 1 LLM key (OpenAI/Claude) + GUEST_API_KEY/OWNER_API_KEY

# 2) Build + serve index cho tenant demo
.venv\Scripts\graphrag build --tenant-root ../tenants/demo/teamassistant
.venv\Scripts\graphrag serve --tenant-root ../tenants/demo/teamassistant --port 8000

# 3) workbench (terminal khác)
cd ../workbench
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env    # điền GRAPHRAG_API_KEY = đúng GUEST_API_KEY ở bước 1
.venv\Scripts\python -m workbench.server.main --port 8766
```
Mở `http://localhost:8766/vault/` — đăng nhập `admin`/`admin123` (đổi ngay), hỏi thử:

> "Theo TKA-1, vì sao thông báo thanh toán bị mất âm thầm, code hiện tại xử lý retry thế nào,
> và team đã quyết định thay đổi gì trong cuộc họp để khắc phục?"

Câu trả lời sẽ trích dẫn cả 4 nguồn: Jira (`_jira-notes/TKA-1.md`), Confluence
(`_confluence-docs/...`), source code (`source-code/notify-service/retry.md`), và MoM cuộc họp
(`_chat-notes/...`).

## Đồng bộ Jira + Confluence (tuỳ chọn, cần site Atlassian riêng)

```bash
copy scripts/.env.example scripts/.env   # điền JIRA_BASE_URL/JIRA_API_TOKEN/JIRA_EMAIL

graphrag-engine/.venv/Scripts/python scripts/jira_sync.py \
  --tenant-root tenants/demo/teamassistant --jql "project = TKA"
  # --no-attachments để bỏ qua tải file đính kèm

graphrag-engine/.venv/Scripts/python scripts/confluence_sync.py \
  --tenant-root tenants/demo/teamassistant --cql 'space = "YOUR_SPACE" AND type = page'
```
Sau khi tải file đính kèm Jira, chạy thêm `normalize run --root tenants/demo/teamassistant`
rồi `graphrag build` lại để đưa vào index.

## GreenNode AI Agent Platform

Code đã sẵn sàng tích hợp GreenNode ở **6 điểm gọi LLM/embedding** (giả định API tương thích
OpenAI — `base_url` override), nhưng **chưa kích hoạt** vì chưa nhận được credential chính thức
tại thời điểm nộp bài. Xem `graphrag-engine/src/graphrag/embedding/embedder.py`
(`GreenNodeEmbedder`), `generation/answer_generator.py`, `graph_extraction/_llm_util.py`,
`normalize-engine/src/normalize/converters/ocr.py`, và `workbench/server/{llm.py,chat.py}`.
Bật bằng cách set `GREENNODE_API_KEY`/`GREENNODE_BASE_URL` trong `.env` + đổi
`embedding.default_provider`/`llm.answer_provider`/`llm.extraction_provider` sang `greennode`
trong `graphrag-engine/config/settings.yaml`.

## Demo / Pitch deck / Video

- Demo link: *(điền sau khi deploy public)*
- Pitch deck: *(điền link)*
- Video demo: *(điền link)*
