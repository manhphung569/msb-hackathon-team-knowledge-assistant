"""Regression check cho "1 index tri thức duy nhất" (MSB AI Hackathon 2026, Team Knowledge
Assistant) — build index từ 3 fixture đại diện đúng 3 trụ cột (source code / Jira backlog /
meeting-chat) rồi xác nhận câu hỏi golden-path retrieve đủ cả 3, không cần API key (embedding
local, generate=False — chỉ kiểm tra retrieval, không gọi LLM sinh câu trả lời).

Copy-modify của smoke_test.py (cùng pattern: dataclasses.replace() override path, build vào
.smoke_data/ riêng, không đụng data/ thật) — build_graph=False vì bài kiểm tra này chỉ quan tâm
vector retrieval, không cần graph layer."""

from __future__ import annotations

import dataclasses
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from graphrag.config import Settings  # noqa: E402
from graphrag.pipeline.build_index import build_index  # noqa: E402
from graphrag.pipeline.query import query  # noqa: E402

WORK = ROOT / ".smoke_data_hackathon"
shutil.rmtree(WORK, ignore_errors=True)

settings = dataclasses.replace(
    Settings.load(),
    normalized_dir=ROOT / "fixtures" / "hackathon_pillars",
    vector_index_dir=WORK / "vector_index",
    graph_db_dir=WORK / "graph_db",
    cache_dir=WORK / "cache",
)

print("=== build_index (3 pillar fixtures: source-code / _jira-notes / _chat-notes) ===")
build_index(settings, force=True, provider_override="local", build_graph=False)

GOLDEN_QUESTION = (
    "Theo TKA-1, vì sao thông báo thanh toán bị mất âm thầm, code hiện tại xử lý retry thế "
    "nào, và team đã quyết định thay đổi gì trong cuộc họp để khắc phục?"
)

print(f"\n=== query: {GOLDEN_QUESTION!r} ===")
result = query(GOLDEN_QUESTION, settings, generate=False)
chunks = result["chunks"]
print(f"Tìm được {len(chunks)} chunk:")
for c in chunks:
    print(f" - [{c.source_path}#{c.section}]")

EXPECTED_PREFIXES = ["source-code/", "_jira-notes/", "_chat-notes/"]
found_prefixes = {
    prefix for prefix in EXPECTED_PREFIXES
    if any(c.source_path.startswith(prefix) for c in chunks)
}
missing = [p for p in EXPECTED_PREFIXES if p not in found_prefixes]

print(f"\nPillar có mặt trong kết quả: {sorted(found_prefixes)}")
if missing:
    print(f"FAIL — thiếu pillar: {missing}")
    sys.exit(1)

print("PASS — cả 3 pillar (source-code / Jira / meeting-chat) đều có mặt trong retrieval.")
