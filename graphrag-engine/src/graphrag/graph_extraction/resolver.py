from __future__ import annotations

import hashlib
import re

from ..types import GraphNode


def canonicalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()


class EntityResolver:
    """Gộp entity trùng nhưng viết khác nhau (vd "MSBPay" / "MSB Pay" / "MSBPAY") trong 1 lần
    `graphrag build`, trước khi ghi vào graph_store — tránh graph phân mảnh vì mỗi lần LLM
    trích lại ra 1 node riêng cho cùng 1 thực thể.

    Chỉ gộp theo alias mà chính LLM đã báo hoặc trùng canonical name (casefold + collapse
    whitespace) — không dùng fuzzy/embedding matching: quy mô cá nhân (~vài chục thực thể)
    chưa cần, và tránh gộp nhầm 2 thực thể tên gần giống nhưng khác nghĩa."""

    def __init__(self) -> None:
        self._alias_to_id: dict[tuple[str, str], str] = {}
        self._nodes: dict[str, GraphNode] = {}

    def resolve(self, type_: str, name: str, aliases: list[str] | None = None) -> str:
        aliases = aliases or []
        candidate_keys = [(type_, canonicalize(c)) for c in (name, *aliases)]

        node_id = next((self._alias_to_id[k] for k in candidate_keys if k in self._alias_to_id), None)
        if node_id is None:
            node_id = hashlib.sha1(f"{type_}:{canonicalize(name)}".encode("utf-8")).hexdigest()[:16]
            self._nodes[node_id] = GraphNode(id=node_id, type=type_, name=name, aliases=list(aliases))
        else:
            node = self._nodes[node_id]
            for a in aliases:
                if a != node.name and a not in node.aliases:
                    node.aliases.append(a)

        for key in candidate_keys:
            self._alias_to_id[key] = node_id
        return node_id

    def nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())
