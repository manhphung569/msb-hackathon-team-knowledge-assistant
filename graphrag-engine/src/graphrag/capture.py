from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from .config import Settings

_NOTE_DIR_NAMES = ("_chat-notes", "_zalo-notes")

# Khớp dòng header extension tự chèn đầu mỗi note (xem popup.js/content.js
# `headerParts`) — "[Nhóm: <tên> | ID: <groupId>]" (group chat, có ID) hoặc
# "[Người: <tên>]" (chat 1-1, Zalo không lộ group ID nên không có phần ID).
_HEADER_RE = re.compile(r"^\[(?:Nhóm|Người): (?P<name>.+?)(?: \| ID: (?P<id>\d+))?\]\s*$")


def list_capture_folders(settings: Settings) -> list[str]:
    """Danh mục folder cho dropdown extension — quét sub-folder ticket/project ngay bên trong
    tenant hiện tại (settings.normalized_dir). Kể từ khi tách tenants/ (2026-08-22), mỗi
    `graphrag serve` chỉ phục vụ đúng 1 tenant (--tenant-root) nên không còn quét chéo nhiều
    org/project như trước. "" (chuỗi rỗng) = lưu thẳng vào gốc tenant, không gắn ticket cụ thể.
    Chỉ liệt kê 1 cấp (không đệ quy sâu hơn) — khớp đúng cấu trúc thật: 1 tenant có vài file
    backlog phẳng + vài thư mục ticket, ticket không có thêm cấp con nào đáng liệt kê ngoài
    chính _chat-notes/_zalo-notes/_project-logs (đã loại)."""
    root = settings.normalized_dir
    folders = [""]
    if root.exists():
        for p in sorted(root.iterdir()):
            if p.is_dir() and p.name not in _NOTE_DIR_NAMES and not p.name.startswith("_"):
                folders.append(p.name)
    return folders


def _write_captured_note(folder: str, text: str, settings: Settings, source: str, warning: str) -> Path:
    """Khung chung cho mọi note capture từ Zalo (text hay ảnh OCR) — sensitivity/reliability
    hardcode cứng, không cho override, xem write_note()."""
    text = text.strip()
    if not text:
        raise ValueError("Nội dung rỗng.")

    # folder đến từ request của client (extension) — bắt buộc phải khớp đúng 1 giá trị
    # list_capture_folders() trả về, không tin thẳng chuỗi client gửi. Thiếu check này thì
    # folder="../../../etc" ghép thẳng vào Path sẽ ghi file RA NGOÀI normalized/ (đã tự test
    # xác nhận lỗ hổng này có thật trước khi thêm check).
    if folder not in list_capture_folders(settings):
        raise ValueError(f"Folder không hợp lệ: {folder!r}")

    if folder == "":
        target_dir = settings.normalized_dir / "_zalo-notes"
    else:
        target_dir = settings.normalized_dir / folder / "_zalo-notes"
    # project LUÔN là tên tenant/workspace (settings.default_project), KHÔNG phải tên
    # sub-folder ticket — kể cả khi note được lưu vào 1 ticket cụ thể bên trong tenant. Đúng quy
    # ước đã có từ trước khi tách tenants/ (xem note cũ trong _lane_of() — ghi project theo tên
    # ticket từng bị coi là "lỗi dữ liệu"), giữ nguyên ở đây để không tái phạm.
    project = settings.default_project or "general"
    target_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    path = target_dir / f"{now.strftime('%Y-%m-%d_%H%M%S')}.md"

    frontmatter = (
        "---\n"
        f"source: {source}\n"
        f"date: {now.strftime('%Y-%m-%d')}\n"
        f"project: {project}\n"
        "sensitivity: confidential\n"
        "reliability: chưa đánh giá — capture thô từ Zalo, cần tự soát lại\n"
        "---\n\n"
        f"> **{warning}**\n\n"
        "## Nội dung\n"
        f"{text}\n"
    )
    path.write_text(frontmatter, encoding="utf-8")
    return path


def _split_header_and_messages(text: str) -> tuple[str | None, list[str]]:
    """Tách dòng header "[Nhóm: ...]"/"[Người: ...]" (nếu có) khỏi các đoạn tin nhắn — dùng
    chung cho write_note() (ghi thật) và preview_capture() (xem trước, không ghi) để 2 nơi luôn
    hiểu "1 tin nhắn" giống hệt nhau, tránh lệch logic dẫn tới đếm/hash sai."""
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    header = paragraphs[0] if paragraphs and paragraphs[0].startswith("[") else None
    messages = paragraphs[1:] if header else paragraphs
    return header, messages


def _hash_message(message: str) -> str:
    # \xa0 (non-breaking space) thay vì space thường — cùng lỗi Zalo render DOM đã ghi nhận ở
    # _normalize_name() cho tên đoạn chat, nhưng tên người gửi lồng ngay trong text mỗi tin
    # nhắn (vd "MSBPAY LEADERs: ...") cũng bị y hệt vì cùng cơ chế DOM. Chuẩn hoá trước khi hash
    # để 2 lần đọc cùng 1 tin (nhưng NBSP/space chưa chắc luôn nhất quán) vẫn ra cùng 1 hash —
    # KHÔNG dùng .split()/" ".join() chuẩn hoá mọi khoảng trắng vì sẽ gộp mất xuống dòng thật
    # bên trong tin nhắn nhiều dòng.
    return hashlib.sha1(message.strip().replace("\xa0", " ").encode("utf-8")).hexdigest()


def _seen_message_hashes(settings: Settings) -> set[str]:
    """Hash từng tin nhắn (đoạn cách nhau bởi "\\n\\n", đúng cách popup.js nối các tin) đã
    có trong mọi note capture Zalo trước đó — dùng để lọc bỏ tin đã lưu khi bôi đen lại đoạn
    trùng ở lần capture sau (vd hôm qua đã lấy 1 phần, hôm nay bôi đen rộng hơn đè lên).

    Dùng chung `_split_header_and_messages()` với write_note()/preview_capture() (chỉ bỏ đúng
    PARAGRAPH ĐẦU TIÊN nếu nó là header) — TRƯỚC ĐÂY hàm này tự lọc bằng
    `not paragraph.startswith("[")` áp cho MỌI paragraph, tưởng chỉ loại header "[Nhóm: ...]"
    nhưng từ khi mỗi tin nhắn có tiền tố "[YYYY-MM-DD HH:MM:SS]" (thêm 2026-08-05, xem
    popup.js), điều kiện đó vô tình loại luôn TOÀN BỘ tin nhắn thật — khiến cơ chế lọc trùng
    gần như không hoạt động cho mọi note capture từ ngày đó (phát hiện qua test viết cho
    preview_capture(), 2026-08-25)."""
    hashes: set[str] = set()
    for path in settings.normalized_dir.rglob("_zalo-notes/*.md"):
        body = path.read_text(encoding="utf-8").split("## Nội dung", 1)[-1]
        _, messages = _split_header_and_messages(body)
        for message in messages:
            hashes.add(_hash_message(message))
    return hashes


def _normalize_name(name: str) -> str:
    """Zalo render tên đoạn chat bằng non-breaking space (\\xa0) thay vì space thường trong
    DOM (`.textContent` lấy nguyên xi) — chuẩn hoá cả 2 phía (lúc ghi note lẫn lúc tra status)
    về space thường trước khi so sánh, tránh miss-match chỉ vì khác loại whitespace."""
    return " ".join(name.replace("\xa0", " ").split())


def _iter_zalo_note_headers(settings: Settings):
    """Yield (path, name, group_id) cho mọi note Zalo có dòng header nhận diện được — note ghi
    bằng textarea tự do hoặc ảnh OCR (write_image_note) không có header này, bị bỏ qua."""
    for path in settings.normalized_dir.rglob("_zalo-notes/*.md"):
        body = path.read_text(encoding="utf-8").split("## Nội dung", 1)[-1].strip()
        if not body:
            continue
        m = _HEADER_RE.match(body.splitlines()[0])
        if m:
            yield path, _normalize_name(m.group("name")), m.group("id")


def capture_status(group_id: str, group_name: str, settings: Settings) -> dict:
    """Đoạn đang mở trên Zalo Web đã được capture chưa — dùng cho badge extension. Note có
    group_id (group chat) chỉ so theo ID (đáng tin hơn tên, tên nhóm có thể đổi); note không có
    ID (chat 1-1, Zalo không lộ group ID trong trường hợp này) so theo tên hiển thị."""
    group_name = _normalize_name(group_name)
    matched: list[Path] = []
    for path, name, note_id in _iter_zalo_note_headers(settings):
        if note_id:
            if group_id and note_id == group_id:
                matched.append(path)
        elif group_name and name == group_name:
            matched.append(path)

    if not matched:
        return {"captured": False, "last_captured_at": None, "note_count": 0}

    last = datetime.fromtimestamp(max(p.stat().st_mtime for p in matched))
    return {
        "captured": True,
        "last_captured_at": last.strftime("%Y-%m-%d %H:%M:%S"),
        "note_count": len(matched),
    }


def preview_capture(text: str, settings: Settings) -> dict:
    """Xem trước (KHÔNG ghi gì) tin nào trong `text` đã từng được capture — dùng chung
    `_seen_message_hashes()`/`_hash_message()` với write_note() nên kết quả khớp chính xác 100%
    với những gì write_note() sẽ thực sự lọc nếu bạn bấm Lưu ngay bây giờ.

    Dùng cho extension kiểm tra "độ phủ" TRƯỚC khi lưu — thay cho cách cũ (tự đoán qua khoảng
    thời gian tin nhắn, lưu cache riêng ở chrome.storage.local): đáng tin hơn vì so khớp đúng
    TỪNG TIN NHẮN THẬT theo hash nội dung, không suy đoán qua timestamp, và không cần cache
    client (server luôn là nguồn sự thật, tính lại mỗi lần hỏi).

    `oldest_already_seen`: tin CŨ NHẤT trong `text` (paragraph đầu tiên sau header — popup.js
    duyệt .chat-item theo đúng thứ tự DOM cũ->mới nên vị trí này luôn là tin cũ nhất) đã từng
    được capture chưa. Đây là tín hiệu chính để biết đã "chạm" tới vùng nội dung từng lưu trước
    đó hay chưa — nếu đúng, mọi thứ giữa lần capture trước và lần đọc này coi như không có
    khoảng trống (vì bạn đã đọc lùi tới tận nơi lần trước dừng lại)."""
    _, messages = _split_header_and_messages(text)
    if not messages:
        return {"total": 0, "new_count": 0, "seen_count": 0, "oldest_already_seen": None}
    seen = _seen_message_hashes(settings)
    flags = [_hash_message(m) in seen for m in messages]
    return {
        "total": len(messages),
        "new_count": flags.count(False),
        "seen_count": flags.count(True),
        "oldest_already_seen": flags[0],
    }


def write_note(folder: str, text: str, settings: Settings) -> tuple[Path, int]:
    """Ghi nguyên văn phần người dùng tự chọn (bôi đen) từ Zalo Web vào 1 file .md mới —
    KHÔNG qua LLM nào (kể cả tóm tắt), vì bản thân nội dung có thể chứa lời người khác chưa
    từng đồng ý gửi ra cloud API. sensitivity/reliability hardcode cứng, không cho override.

    Lọc bỏ tin nhắn đã capture ở lần trước (so hash từng tin, không phải LLM/similarity) —
    trả về (path, số tin đã lọc bỏ vì trùng)."""
    if not text.strip():
        raise ValueError("Nội dung rỗng — chưa bôi đen gì trên trang.")

    header, messages = _split_header_and_messages(text)

    seen = _seen_message_hashes(settings)
    new_messages = [m for m in messages if _hash_message(m) not in seen]
    skipped = len(messages) - len(new_messages)

    if not new_messages:
        raise ValueError("Toàn bộ nội dung đã được capture ở lần trước — không có gì mới để lưu.")

    final_text = "\n\n".join(([header] if header else []) + new_messages)
    if skipped:
        final_text += f"\n\n_(đã lọc bỏ {skipped} tin trùng với lần capture trước)_"

    path = _write_captured_note(
        folder,
        final_text,
        settings,
        source="zalo-capture",
        warning=(
            "Nguồn: Zalo Web (capture qua extension) — có thể chứa lời người khác ngoài bạn, "
            "mặc định `confidential`/`owner`: không gửi cloud API, không bao giờ lộ ra "
            "`graphrag serve` cho người khác."
        ),
    )
    return path, skipped


def write_image_note(folder: str, ocr_text: str, settings: Settings) -> Path:
    """Ghi kết quả OCR local (Tesseract, xem local_ocr.py) từ 1 ảnh Zalo — ảnh gốc KHÔNG gửi
    qua cloud OCR nào, chỉ xử lý trên máy."""
    if not ocr_text.strip():
        raise ValueError("OCR không đọc được chữ nào trong ảnh.")
    return _write_captured_note(
        folder,
        ocr_text,
        settings,
        source="zalo-image-ocr",
        warning=(
            "Nguồn: ảnh từ Zalo Web, OCR local (Tesseract) — không qua cloud API nào. Có thể "
            "chứa lời/thông tin người khác, mặc định `confidential`/`owner`."
        ),
    )
