---
source: confluence-api
page_id: '589825'
space: ~712020d8789b70cc11486fb023a92282816d38
date: '2026-09-18'
project: teamassistant
doc_type: solution_doc
sensitivity: internal
reliability: cao
basis: Đồng bộ trực tiếp qua Confluence REST API (scripts/confluence_sync.py), không
  phải nghe kể lại.
---

# Kien truc notify-service

## Muc dich

Kien truc notify-service - dich vu gui thong bao thanh toan qua push/SMS, co retry khi loi.
Tai lieu nay mo ta thiet ke lien quan toi TKA-1 (thong bao thanh toan bi mat am tham sau khi het retry).

## Cac thanh phan

- send_with_retry - dieu phoi retry toi da 3 lan, lien quan truc tiep TKA-1
- _send_once - goi that xuong gateway (stub trong ban demo)

## Bang tom tat

| Thanh phan | Trach nhiem |
| --- | --- |
| notify-service | Gui thong bao, xu ly retry (TKA-1) |
| payment-gateway | Gateway thuc su gui push/SMS (ngoai pham vi demo) |
