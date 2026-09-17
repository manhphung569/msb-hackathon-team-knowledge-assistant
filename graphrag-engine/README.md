# graphrag-engine

Lớp thứ 3 của kho tri thức, sau `artifacts/` (nguồn gốc) và `normalized/` (text đã chuẩn hoá):

```
artifacts/  →  normalized/  →  graphrag-engine/
(nguồn gốc)    (text sạch)      (index + truy vấn)
```

Xem thiết kế đầy đủ ở [ARCHITECTURE.md](./ARCHITECTURE.md).

## Trạng thái

**Giai đoạn 1 (vector RAG)**: xong, chạy trên dữ liệu thật — `normalized/` có 39 file, đã index đầy đủ vào `data/vector_index/` (LanceDB, embedding local `paraphrase-multilingual-MiniLM-L12-v2`). `graphrag build` tự phát hiện + xoá chunk của file đã bị xoá khỏi `normalized/` (so trực tiếp với `list_doc_ids()` thật trong LanceDB, không chỉ dựa `state.json` — trước 2026-08-04 có bug để lại chunk "ma" vĩnh viễn nếu xoá file, đã fix và verify).

**Giai đoạn 2 (graph layer)**: code xong và đã build thật (`graph_store/kuzu_store.py`, schema sinh động từ `config/graph_schema.yaml`; `graph_extraction/` qua `llm.extraction_provider`/`extraction_model` trong `config/settings.yaml`, hiện là OpenAI `gpt-5.4`).

⚠️ **Đã kiểm chứng bằng dữ liệu thật và KHÔNG khuyến nghị tin trực tiếp graph fact**: build 2 lần độc lập (`gpt-4o-mini` và `gpt-5.4`) trên cùng corpus cho ra **~0% quan hệ trùng nhau** (`scripts/cross_check_graph.py`), và trên câu hỏi thật, câu trả lời dựa vào graph fact bị chính LLM tự flag "không được văn bản xác nhận" — trong khi `--no-graph` (thuần vector) cho câu trả lời có trích dẫn trang cụ thể, kiểm tra lại được ngay. Kết luận: extraction hiện tại recall cao nhưng precision thấp, không đủ tin cậy làm nguồn sự thật.

**Giai đoạn 3 (hybrid retrieval)**: code xong — `retrieval/graph_retriever.py` + `retrieval/fusion.py`, nối vào `graphrag query`. **Mặc định TẮT** (đổi ngày 2026-08-04 sau kiểm chứng ở trên) — dùng `--graph` để bật thử nghiệm, không dùng mặc định cho tới khi cải thiện được precision của extraction (siết prompt / thêm bước lọc evidence yếu).

**Giai đoạn 4 (vận hành nền)**: `graphrag build --watch` đã nối vào `ingestion/watcher.py`.

**Tối ưu luồng `--no-answer` cho Claude Code (2026-08-06)** — soát lại cách Claude Code (VS Code) dùng `graphrag query --no-answer` cho thấy `build_context()` đã tính sẵn text+kênh/ngày nhưng CLI trước đây chỉ in path, buộc phải Read lại file (xem `ARCHITECTURE_DIAGRAMS.html` view 08–09). Đã sửa:
- `--show-context` (mặc định **bật** khi dùng `--no-answer`) in kèm nội dung đã build sẵn, tắt bằng `--no-show-context` nếu chỉ cần path nhanh (hành vi cũ).
- Mỗi path hiện kèm `[x]`/`[ ]` — đánh dấu chunk có thực sự lọt vào context (trong ngân sách 6000 token) hay bị cắt, tránh hiểu nhầm "còn trong danh sách" = "chắc chắn được dùng".
- Mỗi path hiện kèm `(dòng start-end)` — số dòng trong file thật trên đĩa (`Chunk.start_line`/`end_line`, mới thêm vào schema LanceDB), dùng thẳng với `Read(offset=start_line, limit=end_line-start_line+1)` thay vì đọc cả file.
- Cảnh báo `⚠ file không tồn tại` nếu 1 path trả về đã bị move/xoá khỏi `normalized/` nhưng index chưa được purge (xem quy trình purge trong `CLAUDE.md`).

Đã build lại toàn bộ index thật (`data/vector_index/`, xoá + `graphrag build --force`) để áp schema mới — miễn phí vì đang dùng embedding local.

**Giai đoạn 8 (bảo mật)**: `ePay/` (trong tenant `tenants/msb/ekyc/`) đã đánh dấu `confidential` trong overlay `tenants/msb/ekyc/config/settings.yaml` (`sensitivity_rules` — từ 2026-08-22 không còn nằm ở `config/settings.yaml` dùng chung nữa, mỗi tenant tự có overlay riêng). Extraction tự skip chunk confidential, không gửi qua provider cloud.

**API cho người khác truy vấn (`graphrag serve`)**: xong — xem mục API bên dưới. Có access control thật (lọc ở tầng vector search, không phải dặn LLM), khác hẳn `sensitivity` (chỉ quyết định routing cloud API). Mặc định **toàn bộ private** — không tài liệu nào lộ ra ngoài cho tới khi bạn tự mở từng path qua `visibility_rules`.

**Giai đoạn 9 (multi-tenant + query-time federation)**: từ 2026-08-22, mỗi tenant dưới `../tenants/<org>/<ws>/` tự có `artifacts/`/`normalized/`/`data/` riêng (`Settings.load(tenant_root=...)`, `--tenant-root` trên `build`/`query`/`health`/`serve` — xem `ARCHITECTURE.md`, `CLAUDE.md`). Từ 2026-08-24: `graphrag query --tenant-root <tenant>` **tự động federate** thêm `tenants/_common` (methodology dùng chung mọi org) song song với tenant đang hỏi, trọng số thấp hơn (`pipeline/query.py` `_COMMON_WEIGHT_SCALE`) để không lấn át kết quả riêng — tắt bằng `--no-common`. Chunk từ `_common` được đánh dấu `[_common]` trong output CLI. **Lưu ý guest API**: `tenants/_common` chưa có `config/settings.yaml` overlay nên mọi chunk của nó mặc định `visibility: owner` — federation với `_common` hiện chỉ có tác dụng cho owner (CLI, `/query-debug`), KHÔNG có tác dụng cho `/query` (guest) tới khi ai đó chủ động thêm `visibility_rules` cho `_common`.

**Timeline + kênh nguồn**: mỗi chunk có `date` + `source_channel` (đoán từ frontmatter — `zalo-capture`/`chat-capture`/`zalo-image-ocr`/`email`/`document`/`pdf-ocr`), hiện trong context đưa cho LLM (`[kênh | ngày | path#section]`) để tự cân nhắc recency khi 2 nguồn mâu thuẫn — không tự động rerank/xoá gì.

**Dedup**: 2 cơ chế tách biệt, không cái nào tự gộp/xoá dữ liệu — `scripts/find_near_duplicates.py` chỉ **báo cáo** cặp chunk nghi trùng xuyên tài liệu (semantic, cần bạn tự soát); capture Zalo tự **lọc chính xác** tin đã capture trước đó (hash từng tin, tất định — không phải suy đoán).

## Cài đặt

```bash
cd graphrag-engine
python -m venv .venv
./.venv/Scripts/pip install -e .              # package cơ bản (vector RAG)
./.venv/Scripts/pip install -e ".[graph]"     # + graph layer (Kuzu)
cp .env.example .env                            # rồi điền OPENAI_API_KEY (và/hoặc ANTHROPIC_API_KEY / VOYAGE_API_KEY)
```

Không có API key vẫn chạy được phần vector: hệ thống tự động dùng embedding local (`fastembed`, offline, không tốn phí) khi thiếu `VOYAGE_API_KEY` hoặc khi tài liệu đánh dấu `sensitivity: confidential`. Sinh câu trả lời cuối (`graphrag query`) cần key khớp `llm.answer_provider`; nếu chỉ muốn xem chunk liên quan, dùng `--no-answer`. Graph layer (entity/relation extraction lúc `graphrag build`) cần key khớp `llm.extraction_provider` — thiếu thì build tự bỏ qua graph, chỉ index vector. Lưu ý model OpenAI đời mới (`gpt-5.4`) dùng tham số `max_completion_tokens` thay vì `max_tokens` — code đã xử lý, chỉ cần biết nếu tự thêm provider khác.

## Chạy

```bash
./.venv/Scripts/graphrag build                      # index toàn bộ normalized/ (vector + graph nếu có key/extras)
./.venv/Scripts/graphrag build --force               # build lại từ đầu, bỏ qua cache
./.venv/Scripts/graphrag build --provider local       # ép dùng embedding local thay vì tự chọn
./.venv/Scripts/graphrag build --watch                # build 1 lần rồi tự build lại khi normalized/ đổi

./.venv/Scripts/graphrag query "MSBPay phụ thuộc vào những hệ thống nào?"
./.venv/Scripts/graphrag query "..." --no-answer       # xem chunk + nội dung đã build sẵn, không gọi LLM (dùng cho Claude Code)
./.venv/Scripts/graphrag query "..." --no-answer --no-show-context  # chỉ danh sách path nhanh, không in nội dung
./.venv/Scripts/graphrag query "..." --graph           # bật thử hybrid (graph + vector) — xem cảnh báo ở mục Trạng thái
```

## API cho người khác truy vấn (`graphrag serve`)

Cho phép 1 người khác (vd đồng nghiệp) hỏi tri thức qua HTTP, **không phải** qua CLI/Claude Code
như bạn — với dữ liệu bị lọc trước khi vào context của LLM, không chỉ dặn LLM đừng nói:

1. **Phân loại `visibility` (`owner` / `shared`)** — trục hoàn toàn khác `sensitivity`. Mặc định
   **mọi tài liệu là `owner`** (private, chỉ CLI của bạn thấy) cho tới khi bạn tự thêm rule vào
   `visibility_rules` trong `config/settings.yaml` (cùng cú pháp `sensitivity_rules`, theo path
   prefix). Tài liệu `sensitivity: confidential` **luôn bị ép `owner`** bất kể `visibility_rules`
   nói gì (`ingestion/loader.py._guess_visibility`) — dù lỡ set rule sai, hợp đồng/hoá đơn không
   bao giờ lọt ra API.
2. Sau khi sửa `visibility_rules`, chạy lại `graphrag build --force` (đổi schema, cần build lại
   — embedding local nên miễn phí).
3. Cài extras + set key, rồi chạy:
   ```bash
   ./.venv/Scripts/pip install -e ".[api]"
   # thêm GUEST_API_KEY vào .env (chuỗi ngẫu nhiên dài, đưa riêng cho người cần dùng)
   ./.venv/Scripts/graphrag serve --port 8000
   ```
   Gọi thử:
   ```bash
   curl -X POST http://<máy-bạn>:8000/query \
     -H "Content-Type: application/json" -H "X-API-Key: <GUEST_API_KEY>" \
     -d '{"question": "..."}'
   ```
4. **Chỉ mở trong mạng nội bộ/VPN** — `graphrag serve` không tự bảo vệ khỏi việc bị truy cập từ
   internet công khai, đó là trách nhiệm firewall/VPN của bạn, không phải code này.
5. Mỗi request được log nhẹ (thời gian, câu hỏi, số chunk trả về) vào `data/cache/api_access.log`
   để bạn tự soát sau này.

## Capture tri thức từ Zalo Web (extension)

`zalo-capture-extension/` (ngang cấp `graphrag-engine/`) — Chrome extension đọc phần bạn **tự
bôi đen** trên chat.zalo.me, lưu nguyên văn vào `normalized/<folder>/_zalo-notes/`. Dùng chung
server với mục API ở trên, nhưng **2 endpoint riêng, key riêng**:

- `GET /folders`, `POST /capture`, `GET /capture-status` — owner-only, cần `OWNER_API_KEY`
  (khác `GUEST_API_KEY`). **`OWNER_API_KEY` có quyền ghi — giữ bí mật tuyệt đối, không đưa
  cho ai kể cả đồng nghiệp đang dùng `GUEST_API_KEY`.** Nếu chạy `graphrag serve --host
  0.0.0.0` để đồng nghiệp dùng `/query`, các endpoint này cũng nghe trên cùng port — key là
  ranh giới bảo mật duy nhất.
- Nội dung capture **luôn hardcode** `sensitivity: confidential` (`src/graphrag/capture.py`) —
  không cho override, vì tin nhắn Zalo luôn có thể chứa lời người khác chưa từng đồng ý. Nhờ
  đó tự động: không gửi cloud API (embedding/extraction), không bao giờ lộ qua `/query` cho
  đồng nghiệp (visibility bị ép `owner` cứng, xem `ingestion/loader.py._guess_visibility`).
- **Không có bước tóm tắt qua LLM** — nội dung được ghi đúng nguyên văn phần bạn chọn, không
  gửi ra bất kỳ đâu để xử lý trước. Việc bôi đen thủ công tự nó đã là 1 bước chọn lọc.
- **Mỗi tin nhắn có timestamp chính xác đến giây** — dạng `[YYYY-MM-DD HH:MM:SS] Tên: nội dung`.
  Lấy từ epoch millisecond nhúng sẵn trong `data-qid` của mỗi tin (đã đối chiếu khớp chính xác
  với giờ hiển thị trên UI Zalo), không phải giờ lúc bạn bấm capture — nên vẫn đúng dù xem lại
  lịch sử chat cũ. Đủ để dựng timeline chính xác khi nhiều tin nhắn/nguồn nói về cùng 1 việc.
- Cài extension: `chrome://extensions` → bật Developer mode → Load unpacked →
  chọn thư mục `zalo-capture-extension/`. Mở popup lần đầu để nhập `apiBase`
  (mặc định `http://127.0.0.1:8000`) và `OWNER_API_KEY`.
- Cách dùng: bôi đen đoạn muốn lưu trên chat.zalo.me → click icon extension → chọn folder →
  "Lưu vào tri thức". Server tự `graphrag build` lại ngay sau khi ghi, tra cứu được luôn.
- Popup cũng tự tìm ảnh trong vùng bôi đen (trừ ảnh đại diện) — mỗi ảnh có nút "OCR vào tri
  thức" riêng, xem mục OCR local bên dưới. **Suy đoán selector ảnh chưa kiểm chứng bằng ảnh
  Zalo thật** — báo lại nếu không tìm được ảnh hoặc lấy nhầm.
- **Tự lọc tin trùng lần capture trước**: bôi đen lại đoạn hôm qua đã lấy (vd chọn rộng hơn,
  đè lên phần cũ) sẽ tự bỏ tin đã có (so hash chính xác từng tin, xem `capture.write_note`),
  chỉ ghi phần thật sự mới. Trùng 100% → báo lỗi rõ ràng, không tạo note rỗng.
- **Đoạn đang mở đã capture chưa** (thêm 2026-08-04, đổi thiết kế cùng ngày): mở popup lên là
  thấy ngay dòng trạng thái ở đầu — **✓ xanh** = đã có note capture (khớp `groupId` của đoạn
  đang mở qua `GET /capture-status`, hoặc khớp tên hiển thị cho chat 1-1 vì Zalo không lộ group
  ID trong trường hợp đó), **● đỏ** = chưa capture, **xám** = không xác định được (chưa cấu
  hình `OWNER_API_KEY`, hoặc server chưa chạy). Đọc bằng `chrome.scripting.executeScript` +
  `activeTab` giống hệt cách đọc vùng bôi đen — **chỉ chạy đúng lúc bạn tự mở popup**, không có
  content script/background service worker chạy thường trực trên `chat.zalo.me`. Cân nhắc đã
  đổi từ bản đầu (content script tự động + badge icon luôn cập nhật) sang thiết kế này vì Điều
  4 mục 7 Điều khoản Zalo (zalo.vn/dieukhoan/) cấm dùng "phần mềm bên thứ ba... không được
  Zalo phát triển/cấp quyền" để tương tác với dịch vụ — một script chỉ chạy khi người dùng chủ
  động mở popup an toàn hơn nhiều so với 1 script theo dõi DOM liên tục 24/7.

## Mapping người xuyên kênh (Zalo ↔ email ↔ ...) — `/people-ui`

Không dùng LLM tự gộp tên (đã kiểm chứng không đáng tin ở graph layer — xem cảnh báo Giai
đoạn 2 phía trên). Thay vào đó là 1 registry bạn tự duyệt tay (`config/people.yaml`), dùng để
**mở rộng câu hỏi** lúc truy vấn (không rewrite dữ liệu đã lưu, không cần rebuild):

1. Mở `http://127.0.0.1:8000/people-ui` trên trình duyệt (server đang chạy), dán `OWNER_API_KEY`.
2. Màn hình liệt kê: registry hiện tại (sửa/xoá được), và tên/địa chỉ chưa map — tách riêng
   theo từng cột theo kênh (`people.list_unmapped_by_channel`: hiện có `zalo` — quét
   `_zalo-notes/*.md`, và `email` — quét note có frontmatter `converter: msg`, tách "Tên
   <email>" thành 2 mục riêng).
3. Tick chọn các mục cùng 1 người ở 1 hay nhiều cột (vd tên Zalo + tên và email từ mail) → đặt
   tên chính ở ô dưới → "Gộp thành 1 người". Hoặc tự thêm tay ở mục "Thêm người mới".
   - Nếu mục tick chọn KHÔNG phải tên người (regex sender-line ở `_SENDER_LINE` nhận nhầm dòng
     nội dung dạng "Nhãn: giá trị" trong tin nhắn dài, vd "Bước 1", "Email", "Trạng thái") →
     nhấn "Không phải người" để ẩn hẳn khỏi danh sách (lưu ở `config/people_ignore.yaml`, có
     thể "bỏ ẩn" lại ở mục "Đã ẩn" nếu đánh dấu nhầm).
4. Từ lần query tiếp theo, hỏi bằng bất kỳ tên/alias nào đã map đều tự tìm thêm theo các alias
   khác — `pipeline/query.py` gọi `retrieve()` nhiều lần theo từng biến thể rồi hợp nhất, câu
   hỏi không khớp registry thì không tốn thêm gì.

## Registry hệ thống/đối tác (`config/systems.yaml`) — "hippocampal index" thủ công

Cùng tinh thần `people.yaml` — không LLM tự gộp/suy đoán quan hệ, chỉ ghi tay những gì đã tự tra
cứu và xác nhận trong tài liệu thật (xem `ARCHITECTURE_DIAGRAMS.html` view 11). Hiện chưa có UI
riêng như `/people-ui` — sửa trực tiếp file YAML, mỗi mục gồm `canonical`, `type` (`system` |
`partner`), `aliases`, `depends_on` (canonical của entity khác mà entity này gọi/dùng trực tiếp),
`note` (bằng chứng ngắn).

`pipeline/query.py` gọi `systems.expand_query_terms()` song song `people.expand_query_terms()` —
ngoài thay alias, nếu câu hỏi khớp 1 canonical có `depends_on`, tự thêm mỗi entity phụ thuộc
thành 1 biến thể tìm kiếm riêng (đi đúng 1 bước theo quan hệ đã xác nhận tay, không multi-hop tự
động). Ví dụ đã verify: hỏi "Insider hoạt động thế nào" tự mở rộng thêm biến thể `Woay` và `CMP`,
kéo về tài liệu chỉ nói về Woay/CMP dù không nhắc "Insider".

**Lưu ý khi seed thêm entity mới**: PHẢI `graphrag query` tra cứu trước khi ghi `depends_on` —
seed ban đầu (2026-08-07) cố tình *không* đưa "MConnect" vào dù xuất hiện trong ví dụ minh hoạ ở
`ARCHITECTURE.md` §6, vì tra thật trong corpus không tìm thấy bằng chứng nào — đúng bài học từ
graph layer (đừng lặp lại lỗi suy đoán quan hệ không có bằng chứng).

## OCR local cho ảnh Zalo (Tesseract — không qua cloud)

Cùng nguyên tắc "nội dung Zalo không ra cloud" áp cho phần text capture — ảnh cũng vậy.

1. **Cần tự cài trước** (không phải pip package thuần): Tesseract OCR engine — Windows: dùng
   installer [UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) hoặc
   `choco install tesseract`, nhớ tick/cài thêm gói ngôn ngữ **`vie`** (tiếng Việt) lúc cài,
   nếu không độ chính xác chữ có dấu sẽ kém.
2. `./.venv/Scripts/pip install -e ".[local-ocr]"` (cài `pytesseract` + `Pillow` trong venv).
3. Nếu Tesseract chưa cài đúng, `POST /capture-image` trả **503 rõ ràng** kèm hướng dẫn — không
   bao giờ âm thầm rơi về OCR cloud.
4. Note ghi ra có `source: zalo-image-ocr`, vẫn `sensitivity: confidential` cứng như text capture.

## Timeline + kênh nguồn

`context_builder.py` hiện `[source_channel | date | path#section]` cho mỗi chunk trong context
đưa vào prompt sinh câu trả lời — để LLM tự thấy tin nào mới/cũ hơn, từ kênh nào, khi 2 nguồn
nói khác nhau. Không tự động rerank/loại tin cũ ở code (giữ minh bạch, người đọc câu trả lời tự
thấy nguồn trích dẫn). `source_channel` suy từ frontmatter (`ingestion/loader.py._guess_source_channel`):
nhãn kênh trực tiếp (`zalo-capture`, `chat-capture`, `zalo-image-ocr`) nếu có, hoặc suy từ
`converter:` cho tài liệu convert từ `artifacts/` (`.msg` → `email`, pdf/docx/pptx/xlsx →
`document`, OCR → `pdf-ocr`).

## Phát hiện trùng xuyên kênh (chẩn đoán, không tự gộp)

`scripts/find_near_duplicates.py [--threshold 0.92]` — so cosine similarity giữa mọi cặp chunk
thuộc 2 **tài liệu khác nhau** (bỏ qua chunk liền kề cùng 1 doc), in ra cặp nghi trùng để bạn tự
soát (vd file gửi cả Zalo lẫn email). Đã test trên corpus thật: bắt đúng 2 file trùng thật
(`Report_MSBPAY_07042026.md` và bản `(1)`), kèm ít nhiễu từ chunk quá ngắn (vd chỉ có "## Trang
1") — điều chỉnh `--threshold` nếu cần lọc bớt nhiễu, không có ngưỡng "đúng tuyệt đối".

## So sánh nhiều lần extraction (cross-check)

`scripts/cross_check_graph.py <dir_a> <dir_b>` so 2 graph_db build độc lập (model/run khác nhau),
báo cáo quan hệ được cả 2 bên xác nhận vs chỉ 1 bên tìm thấy — dùng để đánh giá độ tin cậy trước
khi coi graph là nguồn sự thật, không phải để tự động merge.

## Test nhanh không cần dữ liệu thật

`tests/smoke_test.py` build + query trên vài tài liệu mẫu ở `tests/fixtures/sample_normalized/` (dữ liệu giả lập, không phải nội dung MSB thật), ghi index tạm vào `tests/.smoke_data/` — không đụng tới `data/` hay `normalized/` thật:

```bash
./.venv/Scripts/python tests/smoke_test.py
```

Phần vector đã xác nhận hoạt động đúng: retrieval xếp hạng đúng đoạn chứa quan hệ MSBPay → MConnect/ePay lên đầu kết quả trên dữ liệu mẫu sạch. Phần graph trong smoke test tự skip nếu thiếu API key khớp `extraction_provider`.
