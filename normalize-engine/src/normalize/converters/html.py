from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag


def _cell_text(cell: Tag) -> str:
    """Flatten 1 ô <td>/<th> thành text phẳng: giữ link dạng 'text (url)', <br> -> '; ',
    badge/span chỉ lấy text bên trong. Không cố giữ HTML/CSS (màu, class) vì không có ý
    nghĩa khi tìm kiếm ngữ nghĩa."""
    parts: list[str] = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                parts.append(text)
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append("; ")
            elif child.name == "a" and child.get("href"):
                label = child.get_text(strip=True)
                parts.append(f"{label} ({child['href']})" if label else child["href"])
            else:
                text = child.get_text(" ", strip=True)
                if text:
                    parts.append(text)
    joined = " ".join(p for p in parts if p)
    # gọn lại dấu "; " lặp/khoảng trắng thừa do ghép <br>
    while "  " in joined:
        joined = joined.replace("  ", " ")
    return joined.strip(" ;")


def _table_to_markdown(table: Tag) -> str:
    rows_md: list[str] = []
    header: list[str] | None = None
    n_cols = 0

    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue

        # Hàng section-header dùng colspan để chia nhóm (vd "DONE", "IN PROGRESS") ->
        # không phải dữ liệu bảng thật, in ra như 1 dòng heading riêng thay vì hàng bảng lệch cột.
        if len(cells) == 1 and cells[0].get("colspan"):
            text = _cell_text(cells[0])
            if text:
                rows_md.append(f"\n**{text}**\n")
            continue

        values = [_cell_text(c) for c in cells]
        is_header_row = header is None and all(c.name == "th" for c in cells)

        if is_header_row:
            header = values
            n_cols = len(values)
            rows_md.append("| " + " | ".join(header) + " |")
            rows_md.append("| " + " | ".join(["---"] * n_cols) + " |")
            continue

        if header is None:
            # bảng không có <th> -> dùng hàng dữ liệu đầu tiên áp làm cột đếm, không bịa header
            n_cols = len(values)
            header = []

        if n_cols and len(values) != n_cols:
            values = (values + [""] * n_cols)[:n_cols]
        rows_md.append("| " + " | ".join(v.replace("|", "/") for v in values) + " |")

    return "\n".join(rows_md) if rows_md else "(bảng trống)"


def convert(path: Path) -> str:
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    parts: list[str] = []

    title = soup.find("h1")
    if title:
        parts.append(f"# {title.get_text(strip=True)}")

    # Đoạn intro/subtitle ngay sau h1 (thường ghi nguồn + ngày cập nhật), lấy các <p> đầu
    # trước bảng/summary-box đầu tiên.
    if title:
        for sib in title.find_next_siblings():
            if sib.name == "p":
                text = sib.get_text(" ", strip=True)
                if text:
                    parts.append(text)
            elif sib.name in ("table", "div", "h2"):
                break

    # summary-card: số liệu tổng quan dạng thẻ, gom thành 1 dòng list ngắn
    for box in soup.find_all(class_="summary-box"):
        cards = []
        for card in box.find_all(class_="summary-card"):
            text = card.get_text(" ", strip=True)
            if text:
                cards.append(text)
        if cards:
            parts.append("Tổng quan: " + " · ".join(cards))

    # Duyệt tuần tự các heading (h2/h3) + table theo đúng thứ tự xuất hiện trong body,
    # để giữ đúng ngữ cảnh "bảng nào thuộc mục nào" thay vì gom hết bảng vào 1 khối.
    body = soup.body or soup
    for el in body.find_all(["h2", "h3", "table"]):
        if el.name in ("h2", "h3"):
            level = "##" if el.name == "h2" else "###"
            text = el.get_text(strip=True)
            if text:
                parts.append(f"{level} {text}")
        else:
            parts.append(_table_to_markdown(el))

    return "\n\n".join(parts)
