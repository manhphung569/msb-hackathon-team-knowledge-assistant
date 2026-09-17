from __future__ import annotations

import io


def is_available() -> bool:
    """Kiểm tra Tesseract binary có cài và pytesseract tìm được không — dùng để trả lỗi rõ
    ràng (503) ở API thay vì âm thầm rơi về cloud OCR, vi phạm nguyên tắc "Zalo không ra cloud"
    (xem ARCHITECTURE.md §8.1)."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_image(image_bytes: bytes, lang: str = "vie+eng") -> str:
    """OCR hoàn toàn local qua Tesseract — không gọi API cloud nào. `lang="vie+eng"` cần gói
    ngôn ngữ tiếng Việt đã cài (`tesseract-ocr-vie`), nếu thiếu Tesseract vẫn chạy nhưng độ
    chính xác chữ có dấu sẽ kém — báo lỗi ở caller nếu is_available() False trước khi gọi."""
    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image, lang=lang).strip()
