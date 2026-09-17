from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .converters import docx as docx_conv
from .converters import eml as eml_conv
from .converters import html as html_conv
from .converters import msg as msg_conv
from .converters import ocr as ocr_conv
from .converters import pdf as pdf_conv
from .converters import pptx as pptx_conv
from .converters import xlsx as xlsx_conv
from .errors import NeedsOCR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = PROJECT_ROOT.parent / "artifacts"
NORMALIZED_DIR = PROJECT_ROOT.parent / "normalized"
_CACHE_FILE = PROJECT_ROOT / "data" / "cache" / "convert_state.json"

_CONVERTERS = {
    ".pdf": pdf_conv.convert,
    ".docx": docx_conv.convert,
    ".pptx": pptx_conv.convert,
    ".xlsx": xlsx_conv.convert,
    ".msg": msg_conv.convert,
    ".eml": eml_conv.convert,
    ".html": html_conv.convert,
    ".htm": html_conv.convert,
}


@dataclass
class ConvertResult:
    source: Path
    target: Path
    status: str  # "converted" | "converted_ocr" | "skipped_unchanged" | "needs_ocr" | "error"
    detail: str = ""


def _load_state(cache_file: Path) -> dict[str, str]:
    if not cache_file.exists():
        return {}
    return json.loads(cache_file.read_text(encoding="utf-8"))


def _save_state(cache_file: Path, state: dict[str, str]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _write_target(target: Path, rel: Path, converter_name: str, body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = (
        "---\n"
        f"source: artifacts/{rel.as_posix()}\n"
        f"converted_at: {datetime.now().isoformat(timespec='seconds')}\n"
        f"converter: {converter_name}\n"
        "---\n\n"
    )
    target.write_text(frontmatter + body, encoding="utf-8")


def resolve_roots(root: Path | None) -> tuple[Path, Path, Path]:
    """root=None -> hành vi cũ (cây artifacts/normalized cạnh normalize-engine/, cache global).
    root=<tenant_root> -> artifacts/normalized bên trong tenant_root, cache RIÊNG trong
    tenant_root/data/cache/ (không dùng chung _CACHE_FILE global — key cache chỉ là path tương
    đối, không có tiền tố tenant, nên 2 tenant có cache chung sẽ đụng key nếu trùng tên file)."""
    if root is None:
        return ARTIFACTS_DIR, NORMALIZED_DIR, _CACHE_FILE
    root = root.resolve()
    return (root / "artifacts").resolve(), (root / "normalized").resolve(), (
        root / "data" / "cache" / "convert_state.json"
    ).resolve()


def run(force: bool = False, root: Path | None = None) -> list[ConvertResult]:
    """Duyệt artifacts/, convert file có converter tương ứng sang .md cùng path tương đối
    trong normalized/. Bỏ qua file không đổi (hash) trừ khi force=True. Không tốn API — mọi
    converter ở đây chạy offline. PDF scan/ảnh -> status "needs_ocr", dùng run_ocr() để xử lý riêng.
    `root`: nếu truyền vào (vd tenant root), convert artifacts/normalized bên trong đó thay vì
    cây toàn cục — dùng khi build từng tenant riêng."""
    artifacts_dir, normalized_dir, cache_file = resolve_roots(root)
    state = {} if force else _load_state(cache_file)
    results: list[ConvertResult] = []

    for src in sorted(artifacts_dir.rglob("*")):
        if src.is_dir() or src.name == "README.md":
            continue
        converter = _CONVERTERS.get(src.suffix.lower())
        if converter is None:
            continue

        rel = src.relative_to(artifacts_dir)
        target = (normalized_dir / rel).with_suffix(".md")
        key = rel.as_posix()
        try:
            file_hash = _content_hash(src)
        except OSError as e:  # file đang bị khoá (vd lock file ~$... của Office đang mở), không nên làm dừng cả batch
            results.append(ConvertResult(src, target, "error", f"{type(e).__name__}: {e}"))
            continue

        if not force and state.get(key) == file_hash and target.exists():
            results.append(ConvertResult(src, target, "skipped_unchanged"))
            continue

        try:
            body = converter(src)
        except NeedsOCR as e:
            results.append(ConvertResult(src, target, "needs_ocr", str(e)))
            continue
        except Exception as e:  # file hỏng, định dạng không đúng chuẩn... không nên làm dừng cả batch
            results.append(ConvertResult(src, target, "error", f"{type(e).__name__}: {e}"))
            continue

        _write_target(target, rel, src.suffix.lower().lstrip("."), body)
        state[key] = file_hash
        results.append(ConvertResult(src, target, "converted"))

    _save_state(cache_file, state)
    return results


def run_ocr(model: str, force: bool = False, root: Path | None = None) -> list[ConvertResult]:
    """Chỉ xử lý PDF mà run() không trích được text (scan/ảnh) — gọi OpenAI vision API để OCR.
    TỐN PHÍ, cần OPENAI_API_KEY. Dùng chung 1 state với run() (theo cùng root) nên file đã OCR
    xong sẽ không bị run() hay run_ocr() convert lại trừ khi force=True hoặc nội dung file gốc đổi."""
    artifacts_dir, normalized_dir, cache_file = resolve_roots(root)
    state = {} if force else _load_state(cache_file)
    results: list[ConvertResult] = []

    for src in sorted(artifacts_dir.rglob("*.pdf")):
        rel = src.relative_to(artifacts_dir)
        target = (normalized_dir / rel).with_suffix(".md")
        key = rel.as_posix()
        file_hash = _content_hash(src)

        if not force and state.get(key) == file_hash and target.exists():
            results.append(ConvertResult(src, target, "skipped_unchanged"))
            continue

        try:
            pdf_conv.convert(src)
            # Trích text thường được -> không cần OCR, để run() xử lý, bỏ qua ở đây.
            continue
        except NeedsOCR:
            pass

        try:
            body = ocr_conv.convert(src, model=model)
        except Exception as e:
            results.append(ConvertResult(src, target, "error", f"{type(e).__name__}: {e}"))
            continue

        _write_target(target, rel, f"pdf-ocr:{model}", body)
        state[key] = file_hash
        results.append(ConvertResult(src, target, "converted_ocr"))

    _save_state(cache_file, state)
    return results
