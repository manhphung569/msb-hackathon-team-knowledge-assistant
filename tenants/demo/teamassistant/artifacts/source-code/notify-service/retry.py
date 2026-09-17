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
