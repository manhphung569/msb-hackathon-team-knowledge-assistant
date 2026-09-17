from __future__ import annotations

import hashlib

from ..config import Settings
from ..types import Chunk, SourceDocument
from .splitter import chunk_document


def build_chunks(doc: SourceDocument, settings: Settings) -> list[Chunk]:
    cfg = settings.chunking_by_doc_type.get(doc.doc_type, settings.chunking_default)
    sections = chunk_document(doc.text, cfg["chunk_size"], cfg["chunk_overlap"])
    chunks: list[Chunk] = []
    for i, section in enumerate(sections):
        text = section.text.strip()
        if not text:
            continue
        chunk_id = hashlib.sha1(f"{doc.doc_id}:{i}".encode("utf-8")).hexdigest()[:16]
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                text=text,
                section=section.heading,
                source_path=doc.rel_path,
                project=doc.project,
                doc_type=doc.doc_type,
                sensitivity=doc.sensitivity,
                visibility=doc.visibility,
                date=doc.date,
                source_channel=doc.source_channel,
                reliability=doc.reliability,
                ticket_ids=doc.ticket_ids,
                people_mentions=doc.people_mentions,
                chunk_index=i,
                start_line=doc.line_offset + section.start_line,
                end_line=doc.line_offset + section.end_line,
            )
        )
    return chunks
