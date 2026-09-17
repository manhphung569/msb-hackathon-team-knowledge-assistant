from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SourceDocument:
    doc_id: str
    path: Path
    rel_path: str
    project: str
    doc_type: str
    sensitivity: str
    visibility: str
    date: str
    source_channel: str
    reliability: str
    ticket_ids: list[str]
    people_mentions: list[str]
    text: str
    content_hash: str
    # Số dòng trong file thật trên đĩa (source_path, có frontmatter) trước khi `text` (đã strip
    # frontmatter) bắt đầu — cộng vào start_line/end_line của Chunk để 2 con số đó dùng thẳng
    # được với Read(offset=...) trên file gốc, không lệch dòng vì frontmatter đã bị cắt.
    line_offset: int = 0


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    section: str
    source_path: str
    project: str
    doc_type: str
    sensitivity: str
    visibility: str
    date: str
    source_channel: str
    reliability: str
    ticket_ids: list[str]
    people_mentions: list[str]
    chunk_index: int
    embedding: list[float] | None = None
    # 1-indexed, tính trên file thật trên đĩa (source_path) — cho phép Read(offset=start_line,
    # limit=end_line-start_line+1) nhảy thẳng tới đúng đoạn thay vì đọc cả file (xem
    # ARCHITECTURE_DIAGRAMS.html view 08-09, mục "không có định vị theo dòng"). Xấp xỉ, không
    # tuyệt đối chính xác ở ranh giới cuối cùng của mỗi sub-chunk khi 1 section bị cắt tiếp theo
    # độ dài — đủ dùng để định vị gần đúng, không cần tokenizer/parser chính xác tuyệt đối.
    start_line: int = 0
    end_line: int = 0


@dataclass
class GraphNode:
    """Node cho graph layer — type phải khớp 1 trong các node type ở config/graph_schema.yaml.
    doc_id chỉ dùng cho node Chunk (tài liệu cha, để dọn dẹp khi rebuild) — Document tự có id = doc_id."""

    id: str
    type: str
    name: str
    aliases: list[str] = field(default_factory=list)
    props: dict = field(default_factory=dict)
    doc_id: str = ""


@dataclass
class GraphEdge:
    """Edge cho graph layer — relation phải khớp 1 trong các edge type ở config/graph_schema.yaml.
    from_type/to_type cần cho upsert (Kuzu yêu cầu label tường minh khi MATCH để MERGE quan hệ);
    doc_id là tài liệu nguồn của quan hệ, dùng để xoá đúng phần khi rebuild theo doc."""

    from_id: str
    from_type: str
    to_id: str
    to_type: str
    relation: str
    evidence: str = ""
    doc_id: str = ""
    from_name: str = ""
    to_name: str = ""
