"""Đồng bộ pdlc-vault (dev, có dữ liệu MSB thật) -> msb-hackathon-team-knowledge-assistant
(bản nộp thi, phải 100% giả lập) — chạy lại được nhiều lần, an toàn, có gate chặn tự động nếu
phát hiện dấu hiệu rò rỉ dữ liệu thật.

BỐI CẢNH: lần đóng gói đầu tiên (2026-09-17) làm thủ công, phát hiện dọc đường nhiều nguồn rò rỉ
không nằm trong danh sách loại trừ ban đầu (chỉ .venv/data/__pycache__/.env) — config/people.yaml
+ people_ignore.yaml (tên/email nhân viên MSB thật), config/systems.yaml (kiến trúc hệ thống MSB
thật), tests/fixtures/sample_normalized/ (mô tả MSBPay thật), graphrag-engine/scripts/ +
templates/project-logs/ (gắn với chu kỳ báo cáo/incident thật), và 1 map cứng SOURCE_META trong
generation/dashboard_renderer.py (tiêu đề họp + tên nhóm Zalo thật, không xoá được cả file vì bị
api.py import). Script này ENCODE LẠI toàn bộ danh sách đó — không cần nhớ lại/khám phá lại mỗi
lần đồng bộ tiếp theo.

KHÔNG tự commit/push — chỉ đồng bộ file + chạy gate kiểm tra, dừng lại để người review `git diff`
trong repo đích trước khi tự tay commit. Xem README ở cuối file này (docstring `main`) để biết
cách chạy.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent  # pdlc-vault root
DST = SRC.parent / "msb-hackathon-team-knowledge-assistant"

# ---- (1) Thư mục copy nguyên khối, kèm exclude riêng từng cái ----------------------------
_COMMON_EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "*.egg-info"}

COPY_SPECS: list[tuple[str, str, set[str]]] = [
    # (rel_path nguồn, rel_path đích, thêm exclude dir riêng ngoài _COMMON_EXCLUDE_DIRS)
    ("graphrag-engine", "graphrag-engine", {"data"}),
    ("normalize-engine", "normalize-engine", set()),
    ("workbench", "workbench", {"data"}),
    ("zalo-capture-extension", "zalo-capture-extension", set()),
    ("scripts", "scripts", set()),
    ("tenants/demo", "tenants/demo", {"data"}),
]

_EXCLUDE_FILENAMES = {".env"}  # không bao giờ copy, bất kể nằm thư mục nào

# ---- (2) File/thư mục loại bỏ tuyệt đối SAU khi copy (biết chắc chứa dữ liệu thật) --------
# Path tính từ DST root.
HARD_EXCLUDE_PATHS = [
    "graphrag-engine/config/people.yaml",
    "graphrag-engine/config/people_ignore.yaml",
    "graphrag-engine/config/systems.yaml",
    "graphrag-engine/tests/fixtures/sample_normalized",
    "graphrag-engine/tests/smoke_test.py",
    "graphrag-engine/scripts",
    "graphrag-engine/templates",
    "graphrag-engine/tests/.smoke_data",
    "graphrag-engine/tests/.smoke_data_hackathon",
]

# ---- (3) Redact tại chỗ (không xoá được cả file vì bị import) -----------------------------
_SOURCE_META_RE = re.compile(r"  var SOURCE_META = \{.*?\n  \};", re.S)
_SOURCE_META_REPLACEMENT = (
    "  // (MSB AI Hackathon 2026 submission: bản gốc có 1 map fallback tay chứa tiêu đề họp/"
    "nhóm Zalo\n"
    "  // thật của MSB tại đây — đã xoá, không dùng cho bản nộp thi. inferSourceInfo() bên dưới "
    "vẫn\n"
    "  // hoạt động đúng nhờ nhánh fallback generic (không có SOURCE_META[path] nào khớp).)\n"
    "  var SOURCE_META = {};"
)


def _redact_people_py(dst_root: Path) -> None:
    """1 dòng comment ví dụ format email thật (bangnx1@msb.com.vn) — không phải data thật theo
    nghĩa nghiêm trọng (chỉ minh hoạ regex), nhưng đổi sang email giả cho sạch, nhất quán với
    _redact_dashboard_renderer thay vì dựa mãi vào _KNOWN_SAFE_FILES."""
    path = dst_root / "graphrag-engine/src/graphrag/people.py"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    new_text = text.replace(
        'Bang Nguyen Xuan (CN-CG.MSB PAY) [bangnx1@msb.com.vn|mailto:bangnx1@msb.com.vn]',
        'Nguyen Van A (Phong B) [nguyenvana@example.com|mailto:nguyenvana@example.com]',
    )
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        print("  Redact ví dụ email trong people.py: OK")


def _redact_dashboard_renderer(dst_root: Path) -> None:
    path = dst_root / "graphrag-engine/src/graphrag/generation/dashboard_renderer.py"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    new_text, n = _SOURCE_META_RE.subn(_SOURCE_META_REPLACEMENT, text)
    if n == 0:
        print(f"  [CẢNH BÁO] Không tìm thấy khối SOURCE_META để redact trong {path} — "
              "file nguồn có thể đã đổi cấu trúc, CẦN TỰ TAY kiểm tra lại (grep 'MSBPAY LEADERs' "
              "hoặc tên nhóm Zalo/tiêu đề họp thật khác trong file này trước khi commit).",
              file=sys.stderr)
        return
    path.write_text(new_text, encoding="utf-8")
    print(f"  Redact SOURCE_META trong dashboard_renderer.py: OK ({n} chỗ)")


def _copy_tree(src: Path, dst: Path, extra_exclude_dirs: set[str]) -> None:
    """Xoá-rồi-copy-lại toàn bộ dst, NHƯNG giữ nguyên dst/.venv qua các lần chạy (dst.venv nằm
    trong 1 thư mục bị loại khỏi src nên rmtree+copytree bình thường sẽ xoá mất vĩnh viễn,
    không có gì phục hồi lại — buộc phải venv lại từ đầu mỗi lần sync, rất phí thời gian)."""
    exclude_dirs = _COMMON_EXCLUDE_DIRS | extra_exclude_dirs
    venv_backup: Path | None = None
    dst_venv = dst / ".venv"
    if dst_venv.exists():
        venv_backup = dst.parent / f"_venv_backup_{dst.name}"
        if venv_backup.exists():
            shutil.rmtree(venv_backup)
        shutil.move(str(dst_venv), str(venv_backup))

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns(*exclude_dirs, *_EXCLUDE_FILENAMES),
    )

    if venv_backup is not None:
        shutil.move(str(venv_backup), str(dst_venv))


def _remove_hard_excludes(dst_root: Path) -> None:
    for rel in HARD_EXCLUDE_PATHS:
        p = dst_root / rel
        if p.is_dir():
            shutil.rmtree(p)
            print(f"  Loại thư mục: {rel}")
        elif p.is_file():
            p.unlink()
            print(f"  Loại file: {rel}")


# ---- (4) Gate an toàn — chạy SAU cùng, chặn lại nếu còn dấu hiệu rò rỉ ---------------------
LEAK_PATTERNS = [
    r"DMUDCNS", r"QLYC-\d", r"msb\.com\.vn", r"MSBPAY LEADERs", r"MConnect phụ thuộc",
    r"@msb\.com\.vn",
]
# Ngoại lệ đã biết là an toàn (code/comment mang tính minh hoạ, không phải data thật) — xem
# README/lịch sử redact ở trên. Thêm dòng mới vào đây CHỈ khi đã tự xác nhận không phải data thật.
_KNOWN_SAFE_FILES = {
    "graphrag-engine/src/graphrag/ingestion/metadata_rules.py",  # regex pattern QLYC-\d+, không phải data
    "graphrag-engine/config/graph_schema.yaml",                    # ontology schema, chỉ nêu tên project làm ví dụ
    "graphrag-engine/src/graphrag/capture.py",                     # 1 dòng comment ví dụ bug DOM whitespace
    "graphrag-engine/src/graphrag/tenant_browser.py",
    "graphrag-engine/src/graphrag/timeline_sync.py",
    "graphrag-engine/src/graphrag/people.py",
    "graphrag-engine/src/graphrag/generation/dashboard_renderer.py",  # sau redact vẫn còn text UI placeholder "Ví dụ: QLYC-..."
    "README.md",  # README tự viết của bản nộp thi, tự nhắc luật "không dùng dữ liệu thật MSB"
    "tenants/demo/README.md",
    "graphrag-engine/ARCHITECTURE_DIAGRAMS.html",  # 1 label "· DMUDCNS" trong header trang, không phải data
    "scripts/sync_hackathon_submission.py",  # chính script này — LEAK_PATTERNS/docstring tự nhắc tên các pattern đang chặn
}

_MAX_NOTE_BYTES = 5000  # note thật (test Zalo trước đó) nặng 39KB, note giả lập ta viết đều <2KB


def _run_leak_gate(dst_root: Path) -> list[str]:
    problems: list[str] = []
    combined = re.compile("|".join(LEAK_PATTERNS), re.I)
    for path in dst_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(dst_root).as_posix()
        if rel.startswith((".git/",)):
            continue
        if rel in _KNOWN_SAFE_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue  # file nhị phân (lance/db) — bỏ qua, đã loại data/ ở bước copy
        if combined.search(text):
            problems.append(f"pattern rò rỉ khớp trong: {rel}")

    for note_dir in ("_zalo-notes", "_chat-notes"):
        for path in dst_root.rglob(f"{note_dir}/*.md"):
            size = path.stat().st_size
            if size > _MAX_NOTE_BYTES:
                rel = path.relative_to(dst_root).as_posix()
                problems.append(
                    f"note bất thường lớn ({size} bytes > {_MAX_NOTE_BYTES}) — nghi là capture "
                    f"thật chưa dọn: {rel}"
                )
    return problems


def main() -> None:
    print(f"SRC = {SRC}")
    print(f"DST = {DST}")
    if not DST.exists():
        print(f"DST chưa tồn tại — chạy lần đầu thì tự tạo repo git ở đó trước (xem README "
              "gốc), script này chỉ đồng bộ NỘI DUNG, không tự git init.", file=sys.stderr)
        sys.exit(1)

    print("\n=== (1) Copy từng subsystem ===")
    for src_rel, dst_rel, extra_exclude in COPY_SPECS:
        src = SRC / src_rel
        dst = DST / dst_rel
        if not src.exists():
            print(f"  Bỏ qua (không tồn tại ở nguồn): {src_rel}")
            continue
        _copy_tree(src, dst, extra_exclude)
        print(f"  {src_rel} -> {dst_rel}")

    print("\n=== (2) Loại bỏ file/thư mục biết chắc chứa dữ liệu thật ===")
    _remove_hard_excludes(DST)

    print("\n=== (3) Redact nội dung nhạy còn sót trong code ===")
    _redact_dashboard_renderer(DST)
    _redact_people_py(DST)

    print("\n=== (4) Gate an toàn — quét lại toàn bộ DST ===")
    problems = _run_leak_gate(DST)
    if problems:
        print("\n!!! GATE CHẶN LẠI — phát hiện nghi vấn rò rỉ, CHƯA commit gì cả:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nTự kiểm tra thủ công từng dòng trên. Nếu xác nhận là false positive (vd code "
            "comment mang tính minh hoạ, không phải data thật), thêm đường dẫn đó vào "
            "_KNOWN_SAFE_FILES trong chính script này rồi chạy lại. Nếu là data thật, xoá/redact "
            "rồi chạy lại.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  Không phát hiện gì — sạch.")

    print("\n=== (5) Diff so với commit gần nhất trong DST (để bạn tự review trước khi commit) ===")
    result = subprocess.run(
        ["git", "-C", str(DST), "status", "--short"],
        capture_output=True, text=True,
    )
    print(result.stdout or "  (không có gì thay đổi so với commit gần nhất)")
    print(
        "\nXong bước đồng bộ tự động. BƯỚC TIẾP THEO LÀ THỦ CÔNG (script này không tự làm):\n"
        f"  cd {DST}\n"
        "  git diff --stat          # xem tổng quan đổi gì\n"
        "  git add -A && git commit -m \"...\"   # tự viết message, tự quyết định push hay chưa"
    )


if __name__ == "__main__":
    main()
