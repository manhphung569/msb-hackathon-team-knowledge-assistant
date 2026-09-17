---
source: zalo-image-ocr
date: 2026-09-19
project: teamassistant
sensitivity: internal
reliability: cao
basis: Ảnh chụp whiteboard buổi sprint planning, OCR local (Tesseract) qua zalo-capture-extension
  — không qua cloud API. Nội dung giả lập cho demo MSB AI Hackathon 2026.
---

> **Nguồn: ảnh whiteboard chụp trong buổi sprint planning 2026-09-19, OCR local (Tesseract) qua
> zalo-capture-extension** — minh hoạ pillar OCR của Team Knowledge Assistant. Nội dung giả lập,
> không phải cuộc họp MSB thật.

## Nội dung

NOTIFY-SERVICE RETRY FLOW (sơ đồ tay)

[Payment event] -> send_with_retry()
  attempt 1 -> fail -> wait 2s
  attempt 2 -> fail -> wait 4s
  attempt 3 -> fail -> log error, return False
                        ^^^ KHONG CO ALERT (van de TKA-1)

FIX:
  MAX_RETRIES 3 -> 5
  + Slack alert khi het retry (#notify-service-alerts)
  owner: Nam
