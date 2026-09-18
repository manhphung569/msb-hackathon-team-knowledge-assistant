from __future__ import annotations

import re

import yaml

from .capture import _HEADER_RE
from .config import PROJECT_ROOT, Settings

_PEOPLE_PATH = PROJECT_ROOT / "config" / "people.yaml"
_IGNORE_PATH = PROJECT_ROOT / "config" / "people_ignore.yaml"
# Khớp nhãn "Tên: " ở đầu dòng, BẮT BUỘC có tiền tố "[timestamp] " đứng trước — mỗi tin nhắn
# zalo-capture luôn có dạng "[YYYY-MM-DD HH:MM:SS] Tên: nội dung" (xem
# zalo-capture-extension/popup.js:150-151, messageTimestamp() luôn trả ts cho tin nhắn thật).
# Bắt buộc tiền tố này (trước đây để tuỳ chọn) để loại các dòng KHÔNG có timestamp — chính là
# các dòng nội dung bên trong 1 tin nhắn dài nhiều dòng (vd ai đó paste hướng dẫn "Bước 1:
# ...\nBước 2: ...\n" vào 1 tin) mà regex cũ nhận nhầm thành tên người gửi mới, vì mẫu
# "Chữ: " ở đầu dòng không phân biệt được "dòng mở đầu tin nhắn" với "dòng nội dung trông
# giống vậy" nếu không có bằng chứng thêm (thiếu timestamp) để loại trừ.
_SENDER_LINE = re.compile(r"^\[[^\]]*\]\s*([A-Za-zÀ-ỹ][^\n:()\[\]]{0,40}):\s", re.MULTILINE)

# Dòng header email — "Từ:/Đến:/Tới:/Cc:" (tiếng Việt, có thể bọc "**" do
# normalize-engine/converters/msg.py ghi cho file .msg gốc) hoặc "From:/To:/Cc:" (tiếng Anh,
# xuất hiện khi email được export/in ra PDF rồi convert — phần lớn email trong kho là dạng
# này, không phải .msg thật). Không khớp "Email :"/"Mobile :" trong chữ ký cuối thư vì các
# nhãn đó không nằm trong danh sách trên.
_EMAIL_HEADER_LINE = re.compile(r"^\*{0,2}(?:Từ|Đến|Tới|Cc|From|To)\*{0,2}:\s*(.*)$", re.MULTILINE)
# 1 dòng header có thể chứa NHIỀU người (phân tách bằng ";" hay ","), mỗi người có thể ở dạng
# "Tên" <email>, Tên (Phòng) <email>, Tên (Phòng) email (không ngoặc), hoặc bọc thêm
# "<mailto:email>" lặp lại ngay sau (PDF export giữ nguyên link). finditer quét lần lượt từng
# cặp tên+email trên dòng — đoạn không có email thật (vd chỉ mention kiểu Zalo "@Tên") tự
# nhiên không khớp gì, không cần loại riêng.
_INLINE_NAME_EMAIL = re.compile(
    r'"?([^";<>,]{0,80}?)"?\s*<?\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})'
    r'(?:\s*<mailto:[^>]*>)?\s*>?'
)

# Không phải mọi email trong kho đều nằm sau nhãn Từ/Đến/Tới/Cc/From/To — bảng export
# Jira/Confluence (.xlsx) đặt "Tên (Phòng) <email>" thẳng trong 1 cell, không có nhãn nào cả.
# Quét TỪNG DÒNG riêng (không phải toàn văn 1 lần — "\s*" khớp cả "\n", nếu chạy trên toàn văn
# sẽ lem tên qua dòng kế/thậm chí qua cả 1 dòng "## Trang N" ở giữa), bắt buộc có "<...>" bọc
# quanh email (không bắt email trần) để tránh vơ nhầm chữ ký cuối thư "Email : x@y". Loại
# thêm ":" khỏi tên để không dính luôn nhãn "Cc:"/"From:" phía trước khi dòng chỉ có 1 người
# (không có dấu ";"/"," phía trước để chặn tự nhiên).
_LOOSE_BRACKETED_EMAIL = re.compile(
    r'([^\n"<>\[\]|,;:]{0,80}?)\s*<\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})'
    r'\s*(?:<mailto:[^>]*>)?\s*>'
)

# Dòng dạng wiki-link "[chữ hiển thị|mailto:email]" — cũng do export Jira/Confluence sinh ra
# (vd "Nguyen Van A (Phong B) [nguyenvana@example.com|mailto:nguyenvana@example.com]") — cùng
# lý do quét từng dòng riêng như trên.
_WIKI_MAILTO_LINK = re.compile(
    r'([^\n\[\]|<>:]{0,80}?)\s*\[[^\[\]|]*\|mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\]'
)

# Transcript cuộc họp Teams (convert từ .docx) — mỗi lượt nói là 1 dòng riêng "Tên (Phòng)
# MM:SS" hoặc "Tên (Phòng) HH:MM:SS" (2+ dấu cách trước giờ, không có "(Phòng)" cũng được, vd
# "Tho Nguyen   35:32"), theo sau là nội dung nói ở dòng/đoạn kế tiếp. Không có dấu hiệu nào
# khác (không "@", không ":" ngay sau tên) nên nhận diện đúng bằng đúng dạng "...MM:SS" cuối
# dòng, kèm điều kiện file phải có dòng "started transcription" (marker cố định do Teams tự
# sinh) để không vơ nhầm bảng/text khác có định dạng "chữ  số:số" cuối dòng.
_TEAMS_SPEAKER_LINE = re.compile(r"^(.+?)\s{2,}\d{1,2}:\d{2}(?::\d{2})?\s*$", re.MULTILINE)


def _people_path(settings: Settings):
    return _PEOPLE_PATH


def load_registry(settings: Settings) -> list[dict]:
    path = _people_path(settings)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("people", [])


def save_registry(settings: Settings, people: list[dict]) -> None:
    path = _people_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"people": people}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def upsert_person(settings: Settings, canonical: str, aliases: list[str]) -> list[dict]:
    people = load_registry(settings)
    for p in people:
        if p["canonical"] == canonical:
            merged = list(dict.fromkeys([*p.get("aliases", []), *aliases]))
            p["aliases"] = merged
            save_registry(settings, people)
            return people
    people.append({"canonical": canonical, "aliases": list(dict.fromkeys(aliases))})
    save_registry(settings, people)
    return people


def delete_person(settings: Settings, canonical: str) -> list[dict]:
    people = [p for p in load_registry(settings) if p["canonical"] != canonical]
    save_registry(settings, people)
    return people


def load_ignored(settings: Settings) -> list[str]:
    """Danh sách cụm từ đã bị đánh dấu "không phải người" trên /people-ui (vd "Bước 1" —
    dòng "Nhãn: giá trị" trong nội dung tin nhắn bị regex sender-line nhận nhầm, xem
    _SENDER_LINE) — loại khỏi mọi list_known_*_senders() ở dưới, không hiện lại nữa."""
    if not _IGNORE_PATH.exists():
        return []
    raw = yaml.safe_load(_IGNORE_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("ignored", [])


def save_ignored(settings: Settings, names: list[str]) -> None:
    _IGNORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IGNORE_PATH.write_text(
        yaml.safe_dump({"ignored": names}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def add_ignored(settings: Settings, names: list[str]) -> list[str]:
    merged = list(dict.fromkeys([*load_ignored(settings), *names]))
    save_ignored(settings, merged)
    return merged


def remove_ignored(settings: Settings, name: str) -> list[str]:
    remaining = [n for n in load_ignored(settings) if n != name]
    save_ignored(settings, remaining)
    return remaining


def _scan_zalo_senders_with_groups(settings: Settings) -> dict[str, set[str]]:
    """Quét toàn bộ note capture từ Zalo, trả về {tên người gửi: {tên nhóm/người đã chat cùng
    xuất hiện}} — dùng cả cho danh sách "chưa map" và để gợi ý thêm ngữ cảnh (người này hoạt
    động ở nhóm chat nào) giúp người dùng nhận diện trước khi gộp xuyên kênh, vì tên hiển thị
    Zalo (vd "SM Khai") không tự nói lên đây là ai ngoài đời."""
    result: dict[str, set[str]] = {}
    for path in settings.normalized_dir.rglob("_zalo-notes/*.md"):
        text = path.read_text(encoding="utf-8")
        # Chỉ quét phần SAU "## Nội dung" — bỏ qua frontmatter YAML (các dòng "date:",
        # "project:"... cũng khớp pattern "Tên: " nếu quét cả file, xem _write_captured_note
        # trong capture.py luôn tạo heading này).
        lines = text.split("## Nội dung", 1)[-1].splitlines()

        # Header luôn là dòng (không rỗng) đầu tiên của phần nội dung, nếu có (xem
        # capture.py: header = paragraphs[0] nếu bắt đầu bằng "[") — cho biết đoạn note này
        # capture từ nhóm hay chat 1-1 nào.
        group_name = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            m = _HEADER_RE.match(stripped)
            group_name = m.group("name") if m else None
            break

        # Bỏ dòng header TRƯỚC khi quét sender — nếu tên nhóm tự chứa "[" lồng (vd "[Nhóm:
        # [QLYC-11886] Backlog... | ID: ...]"), _SENDER_LINE chỉ "nuốt" tới cặp "]" gần nhất
        # rồi khớp nhầm phần còn lại ("Backlog... | ID") thành tên người gửi.
        body = "\n".join(line for line in lines if not _HEADER_RE.match(line.strip()))
        for m in _SENDER_LINE.finditer(body):
            name = m.group(1).strip()
            if not name:
                continue
            groups = result.setdefault(name, set())
            if group_name:
                groups.add(group_name)
    return result


def list_known_zalo_senders_with_groups(settings: Settings) -> list[dict]:
    """Như _scan_zalo_senders_with_groups() nhưng đã lọc bỏ tên có trong registry hoặc đã bị
    đánh dấu "không phải người" — dữ liệu thật cho cột Zalo ở /people-ui, mỗi mục kèm danh
    sách nhóm/chat 1-1 mà tên đó xuất hiện để người dùng nhận diện trước khi gộp."""
    registry = load_registry(settings)
    known = {registry_name for p in registry for registry_name in (p["canonical"], *p.get("aliases", []))}
    known |= set(load_ignored(settings))

    scanned = _scan_zalo_senders_with_groups(settings)
    return [
        {"name": name, "groups": sorted(groups)}
        for name, groups in sorted(scanned.items())
        if name not in known
    ]


def _scan_teams_speakers_with_meetings(settings: Settings) -> dict[str, set[str]]:
    """Quét transcript cuộc họp Teams (convert từ .docx) — trả về {tên người nói: {tên file
    transcript đã xuất hiện}}, cùng vai trò với _scan_zalo_senders_with_groups() nhưng cho
    kênh Teams. Chỉ quét file có dòng "started transcription" (marker Teams tự sinh) để không
    vơ nhầm nội dung khác."""
    result: dict[str, set[str]] = {}
    for path in settings.normalized_dir.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if "started transcription" not in text:
            continue
        meeting = path.stem
        for m in _TEAMS_SPEAKER_LINE.finditer(text):
            name = m.group(1).strip()
            if not name:
                continue
            result.setdefault(name, set()).add(meeting)
    return result


def list_known_teams_speakers_with_meetings(settings: Settings) -> list[dict]:
    """Như list_known_zalo_senders_with_groups() nhưng cho kênh Teams — mỗi mục kèm tên file
    transcript (buổi họp) người đó xuất hiện."""
    registry = load_registry(settings)
    known = {registry_name for p in registry for registry_name in (p["canonical"], *p.get("aliases", []))}
    known |= set(load_ignored(settings))

    scanned = _scan_teams_speakers_with_meetings(settings)
    return [
        {"name": name, "groups": sorted(meetings)}
        for name, meetings in sorted(scanned.items())
        if name not in known
    ]


def _is_name_fragment(name: str) -> bool:
    """PDF export đôi khi xuống dòng ngay giữa 1 tên (vd "Hinh Pham Duc" ở dòng trên, "(CN -
    DM&UDCNS) <email>" rơi xuống dòng dưới) — quét theo dòng không thể ghép lại được, nên
    tên trích ra chỉ còn phần đuôi. Lọc 2 dấu hiệu rõ nhất: bắt đầu bằng "(" (chỉ còn phần
    phòng ban, mất hẳn tên) hoặc có ")" mà không có "(" (rớt giữa cụm ngoặc phòng ban)."""
    return name.startswith("(") or (")" in name and "(" not in name)


def _extract_email_identities(header_line_value: str) -> list[str]:
    """Tách 1 dòng "Từ:/Đến:/Tới:/Cc:/From:/To:" (đã bỏ nhãn) thành các tên/email riêng — xem
    _INLINE_NAME_EMAIL. 1 dòng có thể có nhiều người; đoạn không đủ dạng "tên+email" (vd chỉ
    có tên, hoặc chỉ mention không email) tự nhiên bị finditer bỏ qua."""
    identities: list[str] = []
    for m in _INLINE_NAME_EMAIL.finditer(header_line_value):
        name = m.group(1).strip(' "\'')
        email = m.group(2).strip()
        if name and not _is_name_fragment(name):
            identities.append(name)
        identities.append(email)
    return identities


def list_known_email_identities(settings: Settings) -> list[str]:
    """Quét TOÀN BỘ normalized/ (không riêng file .msg gốc — phần lớn email trong kho là email
    đã export/in sang PDF/docx/xlsx rồi mới convert, không phải .msg thật) tìm tên/email theo 3
    dạng đã thấy trong dữ liệu thật: (1) dòng header "Từ:/Đến:/Tới:/Cc:"/"From:/To:/Cc:" của
    email export PDF, (2) "Tên <email>" nằm thẳng trong cell bảng export Jira/Confluence
    (.xlsx, không có nhãn nào), (3) link wiki "[chữ|mailto:email]" cùng nguồn export đó — lấy
    tên/email mà CHƯA có trong registry và CHƯA bị đánh dấu "không phải người". Không bắt được
    trường hợp chữ ký cuối thư "Email : x@y" (tên nằm ở dòng khác, không liền kề) — giới hạn đã
    biết, chấp nhận vì hiếm và khó trích an toàn hơn các dạng trên."""
    registry = load_registry(settings)
    known = {registry_name for p in registry for registry_name in (p["canonical"], *p.get("aliases", []))}
    known |= set(load_ignored(settings))

    found: set[str] = set()
    for path in settings.normalized_dir.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for m in _EMAIL_HEADER_LINE.finditer(text):
            for candidate in _extract_email_identities(m.group(1)):
                if candidate and candidate not in known:
                    found.add(candidate)
        for line in text.splitlines():
            for pattern in (_LOOSE_BRACKETED_EMAIL, _WIKI_MAILTO_LINK):
                for m in pattern.finditer(line):
                    name = m.group(1).strip(' "\'')
                    email = m.group(2).strip()
                    if name and not _is_name_fragment(name) and name not in known:
                        found.add(name)
                    if email not in known:
                        found.add(email)
    return sorted(found)


def list_unmapped_by_channel(settings: Settings) -> dict[str, list[dict]]:
    """Dữ liệu cho /people-ui: mỗi kênh liên lạc (zalo, teams, email...) 1 cột tên/định danh
    chưa map — người dùng tự tick chọn/kéo-thả tên cùng 1 người ở các cột khác nhau rồi gộp
    tay, không tự động đoán xuyên kênh (tên hiển thị khác nhau quá nhiều để so khớp chuỗi cục
    bộ đáng tin). Mỗi mục có "groups" — với zalo là nhóm/chat 1-1, với teams là tên file
    transcript cuộc họp, giúp nhận diện là ai ngoài đời trước khi gộp; email không có khái
    niệm này nên luôn rỗng."""
    return {
        "zalo": list_known_zalo_senders_with_groups(settings),
        "teams": list_known_teams_speakers_with_meetings(settings),
        "email": [{"name": n, "groups": []} for n in list_known_email_identities(settings)],
    }


def expand_query_terms(question: str, settings: Settings) -> list[str]:
    """Nếu câu hỏi chứa tên/alias nào trong registry, trả về các biến thể câu hỏi đã thay
    bằng từng alias khác (kể cả câu gốc) để mở rộng tìm kiếm — không khớp gì thì trả về
    [question] như cũ, không tốn thêm gì cho câu hỏi không liên quan.

    So khớp theo TỪ TRỌN VẸN (word boundary), không phải substring thô — nếu không, alias
    ngắn (vd "Nam") sẽ khớp nhầm vào giữa từ khác (vd "Namkhoa") và làm hỏng câu hỏi khi thay
    thế. Lưu ý: vẫn không tránh được trường hợp alias trùng nguyên 1 từ có nghĩa khác (vd
    "Nam" alias cho 1 người nhưng câu hỏi nói "Việt Nam") — đây là giới hạn cố hữu của cách so
    khớp theo tên, người tự đặt alias nên tránh chọn từ quá ngắn/phổ biến."""
    people = load_registry(settings)
    variants = {question}
    for p in people:
        all_names = [p["canonical"], *p.get("aliases", [])]
        matched = next(
            (n for n in all_names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", question, re.IGNORECASE)),
            None,
        )
        if not matched:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(matched)}(?!\w)", re.IGNORECASE)
        for other in all_names:
            if other == matched:
                continue
            variants.add(pattern.sub(other, question))
    return list(variants)
