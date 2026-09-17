---
source: jira-api
issue_key: TKA-1
status: To Do
assignee: Phùng Đức Mạnh
priority: Medium
date: '2026-09-17'
project: teamassistant
doc_type: backlog
sensitivity: internal
reliability: cao
basis: Đồng bộ trực tiếp qua Jira REST API (scripts/jira_sync.py), không phải nghe
  kể lại.
---

## TKA-1 — Thong bao thanh toan bi mat am tham sau khi het retry
**Status:** To Do | **Priority:** Medium | **Assignee:** Phùng Đức Mạnh

## Mô tả
Khi notify-service/retry.py het MAX_RETRIES (=3) ma van loi, ham send_with_retry() chi log loi roi return False, khong bat alert nao ra ngoai (khong Slack, khong PagerDuty). Doi van hanh khong biet thong bao da chet am tham cho toi khi khach hang goi len hoi vi sao khong nhan duoc thong bao thanh toan.
