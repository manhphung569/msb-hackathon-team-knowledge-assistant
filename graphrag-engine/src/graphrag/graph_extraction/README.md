# graph_extraction

Trích entity & relation từ chunk bằng LLM, theo ontology định nghĩa ở `config/graph_schema.yaml`.

- `entity_extractor.py` — trích entity theo các loại node đã định nghĩa (Document, Project, System, Person, Concept...), dùng prompt ở `config/prompts/entity_extraction.md`.
- `relation_extractor.py` — trích quan hệ giữa các entity trong cùng chunk (`config/prompts/relation_extraction.md`).
- `resolver.py` — entity resolution: gộp các entity trùng nhưng viết khác nhau (vd "MSBPay" / "MSB Pay" / "MSBPAY") trước khi ghi vào graph, tránh graph bị phân mảnh.

Cũng áp dụng quy tắc `sensitivity` như ở bước embedding: chunk confidential nên dùng LLM local thay vì Claude API.
