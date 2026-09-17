# embedding

Sinh vector embedding cho từng chunk.

- `embedder.py` — wrapper thống nhất, chọn provider theo `chunk.sensitivity`:
  - `public`/`internal` (tuỳ chính sách) → Voyage AI (`voyage-3`, tối ưu cho Claude)
  - `confidential` → model local qua Ollama (vd `bge-m3`), không gọi ra ngoài
- `cache.py` — cache embedding theo hash nội dung chunk, tránh gọi lại API khi re-index.
