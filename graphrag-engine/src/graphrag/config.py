from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMON_TENANT_ROOT = PROJECT_ROOT.parent / "tenants" / "_common"


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env(PROJECT_ROOT / ".env")


@dataclass
class Settings:
    normalized_dir: Path
    vector_index_dir: Path
    graph_db_dir: Path
    cache_dir: Path
    vector_backend: str
    graph_backend: str
    embedding_default_provider: str
    embedding_local_model: str
    extraction_provider: str
    extraction_model: str
    answer_provider: str
    answer_model: str
    vector_top_k: int
    graph_hops: int
    rerank_top_n: int
    sensitivity_rules: list[tuple[str, str]]
    visibility_rules: list[tuple[str, str]]
    chunking_default: dict
    chunking_by_doc_type: dict
    default_project: str | None = None
    # [{"path": "D:\\...\\some-repo", "label": "some-repo"}, ...] — thư mục source code THẬT
    # (không phải bản copy) để ingestion/source_code.py đọc trực tiếp lúc build. Chỉ đọc, không
    # bao giờ ghi vào đây. Rỗng = tenant này chưa khai nguồn source code nào.
    source_code_paths: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path = PROJECT_ROOT, tenant_root: Path | None = None) -> "Settings":
        """`root`: nơi đọc config/settings.yaml + config/chunking.yaml (engine-wide, dùng chung
        mọi tenant — embedding/llm/retrieval/chunking). `tenant_root`: nếu truyền vào, 4 path field
        (normalized_dir/vector_index_dir/graph_db_dir/cache_dir) tính theo tenant_root thay vì theo
        paths.* trong settings.yaml — mỗi tenant tự có normalized/ + data/ riêng, KHÔNG cần tenant
        tự có bản settings.yaml đầy đủ riêng. Nếu tenant_root/config/settings.yaml tồn tại (optional
        overlay), chỉ 3 khoá sensitivity_rules/visibility_rules/default_project trong đó được
        dùng để override — gắn với nội dung riêng của tenant, không phải config engine chung.
        `default_project`: fallback cho SourceDocument.project khi frontmatter không có `project:`
        (ingestion/loader.py) — ảnh hưởng chất lượng retrieval (rerank/keyword so khớp theo field
        này), không chỉ để hiển thị. Không có overlay thì mặc định = tên thư mục tenant_root."""
        raw = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
        chunking_raw = yaml.safe_load((root / "config" / "chunking.yaml").read_text(encoding="utf-8"))
        paths = raw["paths"]

        if tenant_root is not None:
            tenant_root = tenant_root.resolve()
            normalized_dir = (tenant_root / "normalized").resolve()
            vector_index_dir = (tenant_root / "data" / "vector_index").resolve()
            graph_db_dir = (tenant_root / "data" / "graph_db").resolve()
            cache_dir = (tenant_root / "data" / "cache").resolve()

            default_project = tenant_root.name
            source_code_paths: list[dict] = []
            tenant_settings_path = tenant_root / "config" / "settings.yaml"
            if tenant_settings_path.exists():
                tenant_raw = yaml.safe_load(tenant_settings_path.read_text(encoding="utf-8")) or {}
                if "sensitivity_rules" in tenant_raw:
                    raw["sensitivity_rules"] = tenant_raw["sensitivity_rules"]
                if "visibility_rules" in tenant_raw:
                    raw["visibility_rules"] = tenant_raw["visibility_rules"]
                if "default_project" in tenant_raw:
                    default_project = tenant_raw["default_project"]
                if "source_code_paths" in tenant_raw:
                    source_code_paths = tenant_raw["source_code_paths"]
        else:
            normalized_dir = (root / paths["normalized_dir"]).resolve()
            vector_index_dir = (root / paths["vector_index_dir"]).resolve()
            graph_db_dir = (root / paths["graph_db_dir"]).resolve()
            cache_dir = (root / paths["cache_dir"]).resolve()
            default_project = None
            source_code_paths = []

        return cls(
            normalized_dir=normalized_dir,
            vector_index_dir=vector_index_dir,
            graph_db_dir=graph_db_dir,
            cache_dir=cache_dir,
            vector_backend=raw["vector_store"]["backend"],
            graph_backend=raw["graph_store"]["backend"],
            embedding_default_provider=raw["embedding"]["default_provider"],
            embedding_local_model=raw["embedding"]["local_model"],
            extraction_provider=raw["llm"]["extraction_provider"],
            extraction_model=raw["llm"]["extraction_model"],
            answer_provider=raw["llm"]["answer_provider"],
            answer_model=raw["llm"]["answer_model"],
            vector_top_k=raw["retrieval"]["vector_top_k"],
            graph_hops=raw["retrieval"]["graph_hops"],
            rerank_top_n=raw["retrieval"]["rerank_top_n"],
            sensitivity_rules=[
                (r["prefix"], r["sensitivity"]) for r in raw.get("sensitivity_rules", [])
            ],
            visibility_rules=[
                (r["prefix"], r["visibility"]) for r in raw.get("visibility_rules", [])
            ],
            chunking_default=chunking_raw["default"],
            chunking_by_doc_type=chunking_raw.get("by_doc_type", {}),
            default_project=default_project,
            source_code_paths=source_code_paths,
        )


def update_llm_models(
    extraction_model: str | None = None,
    answer_model: str | None = None,
    root: Path = PROJECT_ROOT,
) -> None:
    """Sửa trực tiếp llm.extraction_model / llm.answer_model trong config/settings.yaml bằng
    regex thay thế tại chỗ — KHÔNG dùng yaml.dump (sẽ xoá hết comment tiếng Việt trong file, vốn
    là tài liệu vận hành quan trọng, không phải yaml phát sinh). Dùng cho endpoint POST
    /model-info (dashboard Settings panel)."""
    path = root / "config" / "settings.yaml"
    text = path.read_text(encoding="utf-8")

    def _replace(text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}:\s*)\S+")
        new_text, n = pattern.subn(lambda m: m.group(1) + value, text, count=1)
        if n == 0:
            raise ValueError(f"Không tìm thấy khoá {key!r} trong {path}")
        return new_text

    if extraction_model:
        text = _replace(text, "extraction_model", extraction_model)
    if answer_model:
        text = _replace(text, "answer_model", answer_model)
    path.write_text(text, encoding="utf-8")
