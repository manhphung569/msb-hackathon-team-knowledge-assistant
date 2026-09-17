---
source: artifacts/source-code/notify-service/retry.py
converted_at: 2026-09-17T00:00:00
converter: manual-code-transcription
doc_type: solution_doc
project: teamassistant
---

> Ghi chú công cụ: `.py` không có converter tự động trong `normalize-engine` (chỉ hỗ trợ
> pdf/docx/pptx/xlsx/msg/eml/html) — file này được đọc trực tiếp và transcribe thủ công sang
> markdown theo đúng tiền lệ `converter: manual-*` đã có trong repo cho `.drawio`/`.csv`/ảnh rời
> (xem CLAUDE.md "Định dạng file & tri thức chưa đọc được"), áp dụng thêm cho source code.

## Mục đích module

`notify-service/retry.py` chịu trách nhiệm gửi thông báo thanh toán cho khách hàng qua
push/SMS, có cơ chế thử lại (retry) khi gateway gửi lỗi.

## Hằng số cấu hình

- `MAX_RETRIES = 3` — số lần thử lại tối đa trước khi bỏ cuộc.
- `RETRY_BACKOFF_SECONDS = 2` — thời gian chờ giữa các lần thử, tăng dần theo số lần thử
  (`RETRY_BACKOFF_SECONDS * attempt`).

## Hàm `send_with_retry(notification)`

Gửi 1 `PaymentNotification`, thử tối đa `MAX_RETRIES` (= 3) lần. Mỗi lần lỗi (`NotifyDeliveryError`)
đều được log ở mức `warning` kèm số lần thử hiện tại.

**Vấn đề đang track ở TKA-1**: khi hết cả 3 lần thử mà vẫn lỗi, hàm chỉ log ở mức `error` rồi
`return False` — **không bắn alert nào ra ngoài** (không Slack, không PagerDuty, không nâng mức
độ nghiêm trọng). Vì vậy đội vận hành không biết thông báo đã "chết âm thầm" cho tới khi khách
hàng chủ động gọi lên hỏi vì sao không nhận được thông báo thanh toán.

## Hàm `_send_once(notification)`

Gọi thật xuống push/SMS gateway (lược bỏ implementation thật cho bản demo) — raise
`NotifyDeliveryError` nếu gateway lỗi/timeout.

## Full source

```python
"""notify-service — gửi thông báo thanh toán cho khách hàng qua kênh push/SMS.

Module giả lập cho demo MSB AI Hackathon 2026 (tenant tenants/demo/teamassistant) — không phải
code thật của MSB."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("notify-service")

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


@dataclass
class PaymentNotification:
    customer_id: str
    payment_id: str
    message: str


class NotifyDeliveryError(Exception):
    pass


def _send_once(notification: PaymentNotification) -> None:
    """Gọi thật xuống push/SMS gateway — raise NotifyDeliveryError nếu gateway trả lỗi/timeout."""
    raise NotImplementedError("gateway client thật nằm ở notify_gateway.py, lược bỏ cho demo")


def send_with_retry(notification: PaymentNotification) -> bool:
    """Gửi thông báo thanh toán, thử lại tối đa MAX_RETRIES lần nếu gateway lỗi.

    Bug đang track ở TKA-1: hết MAX_RETRIES mà vẫn lỗi thì hàm này CHỈ log rồi return False —
    không có bất kỳ cảnh báo nào bắn ra ngoài (không alert, không nâng cấp mức độ nghiêm trọng),
    nên đội vận hành không biết thông báo đã "chết âm thầm" cho tới khi khách hàng gọi lên hỏi.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _send_once(notification)
            return True
        except NotifyDeliveryError as e:
            logger.warning(
                "Gửi thông báo thất bại (lần %d/%d) cho payment_id=%s: %s",
                attempt, MAX_RETRIES, notification.payment_id, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    # TODO(TKA-1): hết retry mà vẫn lỗi -> hiện tại chỉ log, KHÔNG bắn alert (Slack/PagerDuty)
    # nào cho đội vận hành. Đây chính là nguyên nhân "thông báo thanh toán mất âm thầm".
    logger.error(
        "Gửi thông báo THẤT BẠI sau %d lần thử, payment_id=%s — không có alert nào được gửi.",
        MAX_RETRIES, notification.payment_id,
    )
    return False
```
