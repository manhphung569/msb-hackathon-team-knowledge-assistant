from __future__ import annotations

from pathlib import Path

import typer

from . import pipeline as pipeline_module

app = typer.Typer(add_completion=False, help="normalize: chuyển artifacts/ -> normalized/ (.md)")

_ROOT_HELP = (
    "Convert artifacts/normalized bên trong 1 tenant root riêng (vd tenants/msb/dip) thay vì "
    "cây artifacts/normalized toàn cục cạnh normalize-engine/. Bỏ trống = hành vi cũ."
)


def _print_summary(results, ok_statuses: set[str], artifacts_dir: Path) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status not in ok_statuses and r.status != "skipped_unchanged":
            rel = r.source.relative_to(artifacts_dir)
            typer.echo(f"  [{r.status}] {rel} — {r.detail}")
    typer.echo("\nTổng kết:")
    for status, n in sorted(counts.items()):
        typer.echo(f"  {status}: {n}")


@app.command("run")
def run_command(
    force: bool = typer.Option(False, "--force", help="Convert lại toàn bộ, bỏ qua cache."),
    root: Path = typer.Option(None, "--root", help=_ROOT_HELP),
) -> None:
    """Convert artifacts/ -> normalized/ (pdf text-based, docx, pptx, xlsx, msg, eml, html). Không tốn API."""
    results = pipeline_module.run(force=force, root=root)
    artifacts_dir, _, _ = pipeline_module.resolve_roots(root)
    _print_summary(results, ok_statuses={"converted"}, artifacts_dir=artifacts_dir)


@app.command("ocr")
def ocr_command(
    force: bool = typer.Option(False, "--force", help="OCR lại toàn bộ, bỏ qua cache."),
    model: str = typer.Option("gpt-5.4", "--model", help="Model vision của OpenAI dùng để OCR."),
    root: Path = typer.Option(None, "--root", help=_ROOT_HELP),
) -> None:
    """OCR các PDF scan/ảnh mà `run` không trích được text, qua OpenAI vision API. TỐN PHÍ,
    cần OPENAI_API_KEY trong .env. Kiểm tra model --model có còn được OpenAI hỗ trợ trước khi chạy hàng loạt."""
    results = pipeline_module.run_ocr(model=model, force=force, root=root)
    artifacts_dir, _, _ = pipeline_module.resolve_roots(root)
    _print_summary(results, ok_statuses={"converted_ocr"}, artifacts_dir=artifacts_dir)


if __name__ == "__main__":
    app()
