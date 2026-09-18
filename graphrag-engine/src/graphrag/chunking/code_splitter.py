"""Tách source code thành chunk theo ranh giới hàm/class — THAY vì heading markdown
(`split_by_heading` trong splitter.py không hiểu cú pháp code, sẽ cắt ngang giữa 1 hàm nếu
dùng thẳng cho .py). Ranh giới lấy qua `ast` (Python built-in) — 100% máy móc, KHÔNG suy luận,
KHÔNG gọi LLM — text mỗi chunk là NGUYÊN VĂN code gốc (qua `ast.get_source_segment`), không
tóm tắt/diễn giải gì. Xem thiết kế đầy đủ trong hội thoại 2026-09-18: bỏ hẳn ý tưởng sinh file
markdown/LLM tóm tắt lúc index — giải thích tự nhiên xảy ra lúc TRẢ LỜI câu hỏi (generate_answer,
đã có sẵn), không lúc index.

v1: chỉ Python (qua `ast`, có sẵn trong Python, không cần cài thêm). Ngôn ngữ khác cần parser
riêng (tree-sitter) — CHƯA triển khai, xem ingestion/source_code.py's LANGUAGE_PARSERS."""

from __future__ import annotations

import ast

from .splitter import RawSection

MODULE_HEADER_HEADING = "Đầu file (import, hằng số, docstring module)"


def _segment(source: str, node: ast.AST) -> str | None:
    return ast.get_source_segment(source, node)


def _func_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, qualifier: str = "") -> str:
    args = [a.arg for a in node.args.args]
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    name = f"{qualifier}.{node.name}" if qualifier else node.name
    return f"{prefix} {name}({', '.join(args)})"


def split_python_by_function(source: str) -> list[RawSection]:
    """Trả về 1 RawSection cho: đầu file (import/hằng số/docstring module, gộp chung 1 khối),
    mỗi hàm top-level, mỗi class (docstring + thuộc tính, KHÔNG gồm thân method), và mỗi method
    trong class (tách riêng — đây là phần hay được hỏi tới nhất, vd "hàm nào xử lý retry").

    Không parse được (SyntaxError — file lỗi cú pháp hoặc không phải Python thật dù đuôi .py):
    trả về 1 RawSection duy nhất chứa nguyên văn file, để vẫn index được (thô, không tách chunk),
    KHÔNG raise — 1 file lỗi không được phép làm crash cả lượt build (xem CLAUDE.md nguyên tắc
    "no silent fallback" cho lỗi NGHIÊM TRỌNG, nhưng đây là suy giảm chất lượng chunk, không phải
    mất dữ liệu — chunk thô vẫn tìm được bằng full-text, chỉ kém chính xác ranh giới)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        end_line = max(1, source.count("\n") + 1)
        return [RawSection(
            heading=f"Full source (không parse được cú pháp: {e})",
            text=source, start_line=1, end_line=end_line,
        )]

    sections: list[RawSection] = []
    header_end_line = 0

    body = tree.body
    first_def_idx = next(
        (i for i, n in enumerate(body) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
        len(body),
    )
    header_nodes = body[:first_def_idx]
    if header_nodes:
        header_end_line = header_nodes[-1].end_lineno or header_nodes[-1].lineno
        header_text = "\n".join(_segment(source, n) or "" for n in header_nodes)
        if header_text.strip():
            sections.append(RawSection(
                heading=MODULE_HEADER_HEADING, text=header_text, start_line=1, end_line=header_end_line,
            ))

    for node in body[first_def_idx:]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = _segment(source, node)
            if text:
                sections.append(RawSection(
                    heading=f"Hàm `{_func_signature(node)}`",
                    text=text, start_line=node.lineno, end_line=node.end_lineno or node.lineno,
                ))
        elif isinstance(node, ast.ClassDef):
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            non_method_end = methods[0].lineno - 1 if methods else (node.end_lineno or node.lineno)
            class_header_lines = source.splitlines()[node.lineno - 1 : non_method_end]
            class_header_text = "\n".join(class_header_lines)
            if class_header_text.strip():
                sections.append(RawSection(
                    heading=f"Class `{node.name}`",
                    text=class_header_text, start_line=node.lineno, end_line=max(node.lineno, non_method_end),
                ))
            for m in methods:
                text = _segment(source, m)
                if text:
                    sections.append(RawSection(
                        heading=f"Hàm `{_func_signature(m, qualifier=node.name)}`",
                        text=text, start_line=m.lineno, end_line=m.end_lineno or m.lineno,
                    ))

    if not sections:
        # File chỉ có import/hằng số, không có hàm/class nào (vd __init__.py rỗng, config module)
        end_line = max(1, source.count("\n") + 1)
        sections.append(RawSection(heading=MODULE_HEADER_HEADING, text=source, start_line=1, end_line=end_line))

    return sections
