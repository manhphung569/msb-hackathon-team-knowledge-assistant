class NeedsOCR(Exception):
    """PDF không trích được text ở bất kỳ trang nào — khả năng là bản scan/ảnh, cần OCR thủ công."""
