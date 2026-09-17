from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import yaml

from .config import PROJECT_ROOT
from .timeline_sync import (
    build_slide19_lane_rows,
    load_timeline_context,
    materialize_slide19_xml,
    rewrite_zip_member,
)

KNOWLEDGE_ROOT = PROJECT_ROOT.parent
_REPORTS_CONFIG_PATH = PROJECT_ROOT / "config" / "reports.yaml"


def _resolve_roots(tenant_root: Path | None) -> tuple[Path, Path]:
    """tenant_root=None -> hành vi cũ (KNOWLEDGE_ROOT toàn cục, reports.yaml của engine).
    tenant_root=<tenant> -> report platform hoạt động trong phạm vi 1 tenant, đọc
    <tenant_root>/config/reports.yaml thay vì config/reports.yaml của engine."""
    if tenant_root is None:
        return KNOWLEDGE_ROOT, _REPORTS_CONFIG_PATH
    tenant_root = tenant_root.resolve()
    return tenant_root, tenant_root / "config" / "reports.yaml"
_DATE_IN_NAME_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_PAGE_RE = re.compile(r"^## Trang (\d+)\s*$", re.M)
_PAGE1_DATE_RE = re.compile(r"CIO Report\s*\|\s*(\d{2}/\d{2}/\d{4})")
_DONE_TOTAL_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+SOsRequirement", re.I)
_BURN_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+SO cam kết", re.I)
_PAGE11_METRIC_RE = re.compile(
    r"Metric chính Q3:\s*(\d+)\s+SOsRequirement.*?,\s*(\d+)\s+Done.*?Burn rate cam kết:\s*(\d+)\s*/\s*(\d+)",
    re.I | re.S,
)
_PERCENT_RE = re.compile(r"(\d+)%")
_LANE_ROW_RE = re.compile(
    r"^(MSB Pay|DIP(?:_BAU)?|DIP|MConnect|Magnet_BAU|E-KYC(?: \(Bankwide\))?|TỔNG)\s+(\d+)\s+(\d+)\s+([^\n]+)$",
    re.M,
)
_PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


class ReportError(RuntimeError):
    """Raised when a report build cannot continue safely."""


@dataclass
class ReportDefinition:
    report_id: str
    title: str
    kind: str
    enabled: bool
    description: str
    template_path: Path
    source_markdown_glob: str
    output_path: Path
    snapshot_path: Path
    build_report_path: Path
    strict_template_date_match: bool = True


def _resolve_path(value: str, knowledge_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (knowledge_root / path).resolve()


def _parse_ddmmyyyy_from_name(path: Path) -> datetime:
    match = _DATE_IN_NAME_RE.search(path.name)
    if not match:
        return datetime.fromtimestamp(path.stat().st_mtime)
    day, month, year = match.groups()
    return datetime(int(year), int(month), int(day))


def _load_pages(markdown_text: str) -> dict[int, str]:
    matches = list(_PAGE_RE.finditer(markdown_text))
    pages: dict[int, str] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        page_no = int(match.group(1))
        pages[page_no] = markdown_text[start:end].strip()
    return pages


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_template_date(template_path: Path) -> str | None:
    slide1_path = "ppt/slides/slide1.xml"
    if not template_path.exists():
        return None
    with zipfile.ZipFile(template_path) as archive:
        raw = archive.read(slide1_path).decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", raw)
    match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    return match.group(1) if match else None


def _extract_pptx_tables(pptx_path: Path, slide_number: int) -> list[list[list[str]]]:
    if not pptx_path.exists():
        return []
    slide_path = f"ppt/slides/slide{slide_number}.xml"
    with zipfile.ZipFile(pptx_path) as archive:
        raw = archive.read(slide_path)
    root = ET.fromstring(raw)
    tables = []
    for tbl in root.findall(".//a:tbl", _PPT_NS):
        rows = []
        for tr in tbl.findall("./a:tr", _PPT_NS):
            row = []
            for tc in tr.findall("./a:tc", _PPT_NS):
                texts = [node.text.strip() for node in tc.findall(".//a:t", _PPT_NS) if node.text and node.text.strip()]
                row.append(_normalize_space(" ".join(texts)))
            rows.append(row)
        tables.append(rows)
    return tables


def load_reports(tenant_root: Path | None = None) -> list[ReportDefinition]:
    knowledge_root, reports_config_path = _resolve_roots(tenant_root)
    raw = yaml.safe_load(reports_config_path.read_text(encoding="utf-8")) or {}
    reports = []
    for item in raw.get("reports", []):
        reports.append(
            ReportDefinition(
                report_id=item["id"],
                title=item["title"],
                kind=item["kind"],
                enabled=bool(item.get("enabled", True)),
                description=item.get("description", "").strip(),
                template_path=_resolve_path(item["template_path"], knowledge_root),
                source_markdown_glob=item["source_markdown_glob"],
                output_path=_resolve_path(item["output_path"], knowledge_root),
                snapshot_path=_resolve_path(item["snapshot_path"], knowledge_root),
                build_report_path=_resolve_path(item["build_report_path"], knowledge_root),
                strict_template_date_match=bool(item.get("strict_template_date_match", True)),
            )
        )
    return reports


def get_report(report_id: str, tenant_root: Path | None = None) -> ReportDefinition:
    for report in load_reports(tenant_root):
        if report.report_id == report_id:
            return report
    raise KeyError(report_id)


def _resolve_latest_source(report: ReportDefinition, tenant_root: Path | None = None) -> Path:
    knowledge_root, _ = _resolve_roots(tenant_root)
    matches = sorted(knowledge_root.glob(report.source_markdown_glob), key=_parse_ddmmyyyy_from_name, reverse=True)
    if not matches:
        raise ReportError(f"Không tìm thấy source markdown theo glob: {report.source_markdown_glob}")
    return matches[0]


def _extract_page1_date(page1: str) -> str | None:
    match = _PAGE1_DATE_RE.search(page1)
    return match.group(1) if match else None


def _parse_page3_table(page3: str) -> list[dict[str, str]]:
    rows = []
    for lane, total, done, rate in _LANE_ROW_RE.findall(page3):
        rows.append({"lane": lane, "total": total, "done": done, "rate": _normalize_space(rate)})
    return rows


def _parse_page11_table(page11: str) -> list[dict[str, str]]:
    rows = []
    pattern = re.compile(
        r"^(MSB Pay|DIP|MConnect|Magnet_BAU|E-KYC)\s+(\d+)\s+(\d+)\s+([^\n]+)$",
        re.M,
    )
    for lane, start_q3, current, note in pattern.findall(page11):
        rows.append(
            {
                "lane": lane,
                "start_q3": start_q3,
                "current": current,
                "note": _normalize_space(note),
            }
        )
    return rows


def _extract_bullets(page: str) -> list[str]:
    bullets = []
    for line in page.splitlines():
        stripped = line.strip()
        if stripped.startswith(("•", "-", "⚠", "✅", "🔴", "⬤")):
            bullets.append(_normalize_space(stripped))
    return bullets


def _extract_goals(page13: str) -> list[str]:
    goals = []
    for match in re.finditer(r"\d+\.\s+(.+?)(?=(?:\n\d+\.\s)|\Z)", page13, re.S):
        goals.append(_normalize_space(match.group(1)))
    return goals


def _build_snapshot(report: ReportDefinition, source_path: Path) -> dict[str, Any]:
    raw_text = source_path.read_text(encoding="utf-8")
    pages = _load_pages(raw_text)
    missing_pages = [page for page in range(1, 20) if page not in pages]
    page1_date = _extract_page1_date(pages.get(1, ""))
    template_date = _extract_template_date(report.template_path)
    page3_match = _DONE_TOTAL_RE.search(pages.get(3, ""))
    page4_burn_match = _BURN_RE.search(pages.get(4, ""))
    page11_metric_match = _PAGE11_METRIC_RE.search(pages.get(11, ""))
    if not page3_match:
        raise ReportError("Không parse được metric Done/Total từ Trang 3.")
    if not page4_burn_match:
        raise ReportError("Không parse được Burn metric từ Trang 4.")
    if not page11_metric_match:
        raise ReportError("Không parse được metric narrative ở Trang 11.")

    page3_rate_match = _PERCENT_RE.search(pages.get(3, ""))
    page4_rate_match = _PERCENT_RE.search(pages.get(4, ""))

    page11_rows = _parse_page11_table(pages.get(11, ""))
    if len(page11_rows) < 5 and page1_date and template_date and page1_date == template_date:
        ppt_tables = _extract_pptx_tables(report.template_path, slide_number=11)
        if ppt_tables:
            page11_rows = [
                {
                    "lane": row[0],
                    "start_q3": row[1],
                    "current": row[2],
                    "note": row[3],
                }
                for row in ppt_tables[0][1:]
                if len(row) >= 4 and row[0] and row[0] != "Lane"
            ]

    snapshot = {
        "report_id": report.report_id,
        "title": report.title,
        "source_path": str(source_path),
        "template_path": str(report.template_path),
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "report_date": page1_date,
        "template_date": template_date,
        "pages_present": sorted(pages.keys()),
        "missing_pages": missing_pages,
        "metrics": {
            "page3_done": int(page3_match.group(1)),
            "page3_total": int(page3_match.group(2)),
            "page3_rate": int(page3_rate_match.group(1)) if page3_rate_match else None,
            "page4_burn_done": int(page4_burn_match.group(1)),
            "page4_burn_total": int(page4_burn_match.group(2)),
            "page4_burn_rate": int(page4_rate_match.group(1)) if page4_rate_match else None,
            "page11_done": int(page11_metric_match.group(2)),
            "page11_total": int(page11_metric_match.group(1)),
            "page11_burn_done": int(page11_metric_match.group(3)),
            "page11_burn_total": int(page11_metric_match.group(4)),
        },
        "lane_tables": {
            "page3": _parse_page3_table(pages.get(3, "")),
            "page11": page11_rows,
        },
        "lane_focus": {
            "mconnect": _extract_bullets(pages.get(6, "")),
            "msbpay": _extract_bullets(pages.get(7, "")),
            "dip": _extract_bullets(pages.get(8, "")),
            "magnet": _extract_bullets(pages.get(9, "")),
            "ekyc": _extract_bullets(pages.get(10, "")),
        },
        "goals": _extract_goals(pages.get(13, "")),
        "raw_sections": {
            "page12": pages.get(12, ""),
            "page14": pages.get(14, ""),
            "page15": pages.get(15, ""),
            "page16": pages.get(16, ""),
            "page17": pages.get(17, ""),
            "page18": pages.get(18, ""),
            "page19": pages.get(19, ""),
        },
    }
    return snapshot


def _validate_snapshot(report: ReportDefinition, snapshot: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    missing_pages = snapshot.get("missing_pages", [])
    if missing_pages:
        issues.append(
            {
                "level": "error",
                "code": "missing_pages",
                "message": f"Thiếu các trang bắt buộc: {', '.join(str(p) for p in missing_pages)}",
            }
        )
    report_date = snapshot.get("report_date")
    template_date = snapshot.get("template_date")
    if not report_date:
        issues.append({"level": "error", "code": "report_date_missing", "message": "Không parse được ngày báo cáo từ Trang 1."})
    if not template_date:
        issues.append({"level": "error", "code": "template_date_missing", "message": "Không parse được ngày trong file template PPTX."})
    if report.strict_template_date_match and report_date and template_date and report_date != template_date:
        issues.append(
            {
                "level": "error",
                "code": "template_date_mismatch",
                "message": (
                    "Template PPTX và source markdown không cùng ngày. "
                    f"Source={report_date}, template={template_date}. Chặn build để tránh dùng nhầm timeline/bảng tĩnh."
                ),
            }
        )

    metrics = snapshot["metrics"]
    if metrics["page3_done"] != metrics["page11_done"] or metrics["page3_total"] != metrics["page11_total"]:
        issues.append(
            {
                "level": "error",
                "code": "metric_mismatch_done_total",
                "message": "Metric Done/Total giữa Trang 3 và Trang 11 không khớp.",
            }
        )
    if metrics["page4_burn_done"] != metrics["page11_burn_done"] or metrics["page4_burn_total"] != metrics["page11_burn_total"]:
        issues.append(
            {
                "level": "error",
                "code": "metric_mismatch_burn",
                "message": "Metric Burn giữa Trang 4 và Trang 11 không khớp.",
            }
        )

    page3_rows = snapshot["lane_tables"]["page3"]
    page11_rows = snapshot["lane_tables"]["page11"]
    if len(page3_rows) < 6:
        issues.append(
            {
                "level": "error",
                "code": "page3_table_short",
                "message": f"Trang 3 parse được quá ít dòng lane ({len(page3_rows)}).",
            }
        )
    if len(page11_rows) < 5:
        issues.append(
            {
                "level": "error",
                "code": "page11_table_short",
                "message": f"Trang 11 parse được quá ít dòng lane ({len(page11_rows)}).",
            }
        )

    if not snapshot.get("goals"):
        issues.append(
            {
                "level": "error",
                "code": "goals_missing",
                "message": "Không parse được danh sách mục tiêu trọng tâm ở Trang 13.",
            }
        )

    for section_name, section_text in snapshot.get("raw_sections", {}).items():
        if section_name != "page12" and not section_text:
            issues.append(
                {
                    "level": "error",
                    "code": f"{section_name}_empty",
                    "message": f"{section_name} không có nội dung để dựng report.",
                }
            )
    return issues


def build_report(report_id: str, tenant_root: Path | None = None) -> dict[str, Any]:
    report = get_report(report_id, tenant_root)
    if not report.enabled:
        raise ReportError(f"Report {report.report_id} đang bị disable.")
    source_path = _resolve_latest_source(report, tenant_root)
    snapshot = _build_snapshot(report, source_path)
    issues = _validate_snapshot(report, snapshot)
    status = "ok" if not any(issue["level"] == "error" for issue in issues) else "failed"

    manifest = {
        "report_id": report.report_id,
        "title": report.title,
        "kind": report.kind,
        "status": status,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_path": str(source_path),
        "template_path": str(report.template_path),
        "output_path": str(report.output_path),
        "snapshot_path": str(report.snapshot_path),
        "build_report_path": str(report.build_report_path),
        "report_date": snapshot.get("report_date"),
        "template_date": snapshot.get("template_date"),
        "issues": issues,
        "summary": {
            "pages_present": len(snapshot.get("pages_present", [])),
            "lane_rows_page3": len(snapshot["lane_tables"]["page3"]),
            "lane_rows_page11": len(snapshot["lane_tables"]["page11"]),
            "goals": len(snapshot.get("goals", [])),
        },
    }

    report.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    report.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    if status == "ok":
        report.output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report.template_path, report.output_path)
        timeline_context = load_timeline_context(source_path)
        slide19_rows = build_slide19_lane_rows(source_path)
        with zipfile.ZipFile(report.output_path) as archive:
            slide19_xml = archive.read("ppt/slides/slide19.xml")
        updated_slide19 = materialize_slide19_xml(slide19_xml, timeline_context, slide19_rows)
        rewrite_zip_member(report.output_path, {"ppt/slides/slide19.xml": updated_slide19})
        manifest["artifact_written"] = True
        manifest["artifact_size_bytes"] = report.output_path.stat().st_size
        manifest["timeline_materialized"] = True
    else:
        manifest["artifact_written"] = False

    report.build_report_path.parent.mkdir(parents=True, exist_ok=True)
    report.build_report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_report_summaries(tenant_root: Path | None = None) -> list[dict[str, Any]]:
    reports = []
    for report in load_reports(tenant_root):
        build_manifest = _read_json_if_exists(report.build_report_path)
        snapshot = _read_json_if_exists(report.snapshot_path)
        latest_ok = (build_manifest or {}).get("status") == "ok"
        reports.append(
            {
                "id": report.report_id,
                "title": report.title,
                "kind": report.kind,
                "enabled": report.enabled,
                "description": report.description,
                "template_path": str(report.template_path),
                "output_path": str(report.output_path),
                "status": (build_manifest or {}).get("status", "never-built"),
                "report_date": (snapshot or {}).get("report_date"),
                "last_generated_at": (build_manifest or {}).get("generated_at"),
                "artifact_exists": report.output_path.exists() and latest_ok,
            }
        )
    return reports


def get_report_detail(report_id: str, tenant_root: Path | None = None) -> dict[str, Any]:
    report = get_report(report_id, tenant_root)
    build_manifest = _read_json_if_exists(report.build_report_path)
    return {
        "definition": {
            "id": report.report_id,
            "title": report.title,
            "kind": report.kind,
            "enabled": report.enabled,
            "description": report.description,
            "template_path": str(report.template_path),
            "source_markdown_glob": report.source_markdown_glob,
            "output_path": str(report.output_path),
            "snapshot_path": str(report.snapshot_path),
            "build_report_path": str(report.build_report_path),
            "strict_template_date_match": report.strict_template_date_match,
        },
        "build_manifest": build_manifest,
        "snapshot": _read_json_if_exists(report.snapshot_path),
        "artifact_exists": report.output_path.exists() and (build_manifest or {}).get("status") == "ok",
    }


def get_report_artifact_path(report_id: str, tenant_root: Path | None = None) -> Path:
    report = get_report(report_id, tenant_root)
    build_manifest = _read_json_if_exists(report.build_report_path)
    if (build_manifest or {}).get("status") != "ok":
        raise ReportError(
            f"Artifact của report {report_id} không được mở vì lần build gần nhất không pass quality gate."
        )
    if not report.output_path.exists():
        raise ReportError(f"Chưa có artifact cho report {report_id}. Hãy build trước.")
    return report.output_path
