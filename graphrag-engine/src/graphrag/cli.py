from __future__ import annotations

import json
from pathlib import Path

import typer

from .config import COMMON_TENANT_ROOT, Settings
from .ingestion.watcher import watch
from .ingestion.health import build_health_report
from .pipeline.build_index import build_index as _build_index
from .pipeline.dashboard import build_dashboard as _build_dashboard
from .pipeline.query import query as _query
from .reporting import build_report as _build_report, list_report_summaries

app = typer.Typer(add_completion=False, help="graphrag: index + truy vấn cho normalized/")


@app.command()
def build(
    force: bool = typer.Option(False, help="Build lại toàn bộ, bỏ qua cache."),
    provider: str = typer.Option(None, help="Ép dùng 'voyage' hoặc 'local' thay vì tự chọn."),
    watch_flag: bool = typer.Option(
        False, "--watch", help="Sau khi build xong, chạy nền và tự build lại khi normalized/ đổi."
    ),
    graph: bool = typer.Option(
        False,
        "--graph",
        help=(
            "Cũng chạy entity/relation extraction cập nhật graph layer. Mặc định TẮT (đổi "
            "2026-08-04) — kiểm chứng thực tế cho thấy graph fact chưa đủ tin cậy, xem README "
            "mục Trạng thái. Bật lại chỉ khi chủ động muốn thử nghiệm/cải thiện graph."
        ),
    ),
    tenant_root: Path = typer.Option(
        None,
        "--tenant-root",
        help=(
            "Build index cho 1 tenant riêng thay vì normalized/ toàn cục — trỏ tới thư mục tenant "
            "(vd tenants/msb/dip), bên trong tự có normalized/ + data/. Bỏ trống = hành vi cũ."
        ),
    ),
) -> None:
    settings = Settings.load(tenant_root=tenant_root)
    try:
        _build_index(settings, force=force, provider_override=provider, build_graph=graph)
    except RuntimeError as e:
        typer.echo(f"Lỗi: {e}", err=True)
        raise typer.Exit(1)

    if watch_flag:
        typer.echo(f"\nĐang theo dõi {settings.normalized_dir} — Ctrl+C để dừng.")
        watch(
            settings.normalized_dir,
            on_change=lambda: _build_index(settings, provider_override=provider, build_graph=graph),
        )


@app.command()
def query(
    question: str,
    no_answer: bool = typer.Option(
        False, "--no-answer", help="Chỉ show kết quả retrieval, không gọi LLM sinh câu trả lời."
    ),
    show_context: bool = typer.Option(
        True,
        "--show-context/--no-show-context",
        help=(
            "Chỉ áp dụng cùng --no-answer: in kèm nội dung chunk (text + kênh/ngày) mà "
            "build_context() đã tính sẵn, thay vì chỉ path — tránh phải Read lại file. Tắt bằng "
            "--no-show-context nếu chỉ cần danh sách path nhanh (hành vi cũ)."
        ),
    ),
    graph: bool = typer.Option(
        False,
        "--graph",
        help=(
            "Bật hybrid retrieval (graph + vector). Mặc định TẮT — kiểm chứng thực tế cho thấy "
            "graph fact hiện chưa đủ tin cậy, xem README mục Trạng thái."
        ),
    ),
    tenant_root: Path = typer.Option(
        None,
        "--tenant-root",
        help="Query trên index của 1 tenant riêng (vd tenants/msb/dip). Bỏ trống = hành vi cũ.",
    ),
    federate_common: bool = typer.Option(
        True,
        "--common/--no-common",
        help=(
            "Tự động gộp thêm kết quả từ tenants/_common (Strategy&Methodology dùng chung mọi "
            "org, xem CLAUDE.md) song song với tenant đang query, trọng số thấp hơn để không lấn "
            "át kết quả riêng của tenant (Phase 5 federation). Tắt bằng --no-common nếu chỉ cần "
            "đúng phạm vi 1 tenant. Tự bỏ qua nếu đang query chính tenants/_common."
        ),
    ),
) -> None:
    settings = Settings.load(tenant_root=tenant_root)
    try:
        result = _query(
            question, settings, generate=not no_answer, use_graph=graph, federate_common=federate_common
        )
    except RuntimeError as e:
        typer.echo(f"Lỗi: {e}", err=True)
        raise typer.Exit(1)

    planner_mode = result.get("planner_mode", "unknown")
    retrieval_trace = " -> ".join(result.get("retrieval_trace", [])) or "(không ghi nhận)"
    included = set(result["context_chunk_ids"])
    typer.echo(f"\nPlanner mode: {planner_mode}")
    typer.echo(f"Retrieval trace: {retrieval_trace}")
    typer.echo(f"\n{len(result['chunks'])} chunk liên quan:\n")
    common_normalized_dir = COMMON_TENANT_ROOT / "normalized"
    for c in result["chunks"]:
        mark = "x" if c.chunk_id in included else " "
        loc = f" (dòng {c.start_line}-{c.end_line})" if c.start_line else ""
        # Federation (Phase 5): chunk tu tenants/_common co source_path tuong doi theo normalized/
        # CUA _common, khong phai cua tenant dang query (settings.normalized_dir) - phai check
        # dung root, khac se bao sai "file không tồn tại" cho moi chunk _common. KHONG dung
        # c.project de nhan dien - field nay co the stale (chunk chua duoc re-index tu khi loader.py
        # doi cach tinh project, project van con la ten file cu tu ban build truoc) - check thang
        # xem file that su nam o root nao, dang tin cay hon metadata luu san trong index.
        primary_path = settings.normalized_dir / c.source_path
        from_common = not primary_path.exists() and (common_normalized_dir / c.source_path).exists()
        root = common_normalized_dir if from_common else settings.normalized_dir
        tag = " [_common]" if from_common else ""
        line = f"  [{mark}] [{c.source_path}#{c.section or 'root'}]{loc}{tag}"
        if not (root / c.source_path).exists():
            line += "  ⚠ file không tồn tại (đã move/xoá? index có thể cần purge — xem CLAUDE.md)"
        typer.echo(line)

    dropped = len(result["chunks"]) - len(included)
    if dropped > 0:
        typer.echo(
            f"\n({dropped} chunk đánh dấu [ ] ở trên KHÔNG nằm trong context bên dưới — bị bỏ vì "
            "vượt ngân sách token của build_context, chỉ mang tính tham khảo thêm nếu cần.)"
        )

    if no_answer and show_context and result["context"]:
        typer.echo(f"\n--- Nội dung (đã build sẵn — dùng để tự tổng hợp câu trả lời) ---\n{result['context']}\n")

    if result["subgraph_edges"]:
        typer.echo(f"\n{len(result['subgraph_edges'])} cạnh graph liên quan (subgraph facts đã đưa vào prompt).")

    if result["answer"]:
        typer.echo(f"\n--- Trả lời ---\n{result['answer']}\n")


@app.command()
def dashboard() -> None:
    """Tổng hợp đầu việc/rủi ro/priority cross-lane thành reports/DASHBOARD.html (script + 1 lần
    gọi LLM/lane — xem cảnh báo trong chính file HTML về giới hạn so với báo cáo CIO thủ công).
    Dùng để chạy tay ngoài lịch 6h (xem scripts/daily-sync.ps1), hoặc kiểm tra lại kết quả.
    Không có --tenant-root — dashboard luôn tổng hợp CROSS-TENANT (mỗi lane MSB tự đọc đúng
    tenant riêng bên trong, xem pipeline/dashboard.py _LANE_TO_TENANT_ROOT), khác build/query/
    dashboard vốn chạy trong phạm vi 1 tenant."""
    try:
        path = _build_dashboard()
    except RuntimeError as e:
        typer.echo(f"Lỗi: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Đã ghi {path}")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Chỉ nên mở trong mạng nội bộ/VPN, không public ra internet."),
    port: int = typer.Option(8000, help="Port lắng nghe."),
    tenant_root: Path = typer.Option(
        None,
        "--tenant-root",
        help="Serve API cho 1 tenant riêng (vd tenants/msb/dip) — bắt buộc set kể từ khi tách "
        "tenants/, không còn 1 index chung ở vị trí cũ để dùng mặc định nữa.",
    ),
) -> None:
    """Chạy API cho người khác truy vấn (đọc-hạn-chế, chỉ chunk visibility='shared') — xem
    README mục API. Cần cài extras: pip install -e ".[api]", và GUEST_API_KEY trong .env."""
    import os

    if not os.environ.get("GUEST_API_KEY"):
        typer.echo("Lỗi: GUEST_API_KEY chưa được set trong .env — xem .env.example.", err=True)
        raise typer.Exit(1)
    try:
        import uvicorn
    except ImportError:
        typer.echo('Lỗi: chưa cài extras. Chạy: pip install -e ".[api]"', err=True)
        raise typer.Exit(1)

    if tenant_root is not None:
        os.environ["GRAPHRAG_TENANT_ROOT"] = str(tenant_root.resolve())

    typer.echo(f"Đang chạy graphrag API tại http://{host}:{port} — chỉ nên truy cập qua mạng nội bộ/VPN.")
    if tenant_root is not None:
        typer.echo(f"Tenant: {tenant_root}")
    uvicorn.run("graphrag.api:app", host=host, port=port)


@app.command()
def health(
    tenant_root: Path = typer.Option(
        None,
        "--tenant-root",
        help="Health report cho 1 tenant riêng. Bỏ trống = hành vi cũ.",
    ),
) -> None:
    """In báo cáo health/freshness cục bộ cho artifacts/ -> normalized/ -> index."""
    settings = Settings.load(tenant_root=tenant_root)
    report = build_health_report(settings)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("report-list")
def report_list(
    tenant_root: Path = typer.Option(
        None, "--tenant-root", help="Liệt kê report của 1 tenant riêng. Bỏ trống = hành vi cũ."
    ),
) -> None:
    """Liệt kê các report đã đăng ký trong report platform."""
    typer.echo(json.dumps(list_report_summaries(tenant_root), ensure_ascii=False, indent=2))


@app.command("report-build")
def report_build(
    report_id: str,
    tenant_root: Path = typer.Option(
        None, "--tenant-root", help="Build report của 1 tenant riêng. Bỏ trống = hành vi cũ."
    ),
) -> None:
    """Build 1 report deterministic qua report platform."""
    try:
        manifest = _build_report(report_id, tenant_root)
    except RuntimeError as e:
        typer.echo(f"Lỗi: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
