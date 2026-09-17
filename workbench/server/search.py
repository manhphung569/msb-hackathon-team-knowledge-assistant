"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

class SearchMixin:
    # ---- source line 7553 (Handler._resolve_vault_root) ----
    def _resolve_vault_root(self, workspace: str, project: str):
        """Return Path to openspec root for given workspace/project.
        S.proj is already a full key like 'sale/inbd' — never double-prefix with workspace.

        DECOUPLED (2026-09-14, migration plan §3): originally resolved via pdlc-core's
        workspace_layout.py (sys.path.insert + resolve_project_layout/load_workspace_roots).
        Replaced with a direct join against config.ARTIFACTS_ROOT — this reproduces exactly
        what resolve_project_layout computed (layout.openspec_root == artifacts_root/key/openspec),
        with no cross-repo import. Content source unchanged: still pdlc-artifacts/<key>/openspec/."""
        from pathlib import Path as _Path
        from .config import ARTIFACTS_ROOT
        try:
            # S.proj is the full key ("sale/inbd"); ws is just the namespace ("sale")
            key = project or workspace or ""
            candidate_path = _Path(ARTIFACTS_ROOT) / key / "openspec"

            # Path is valid and has at least one content folder → use it
            content_dirs = {"specs","scenarios","patterns","skills","knowledge","diagrams","integration"}
            def _has_content(p):
                return p.exists() and any((p / d).exists() for d in content_dirs)

            if _has_content(candidate_path):
                return candidate_path

            # Fallback: scan all openspec dirs under artifacts_root
            all_osps = []
            try:
                for p in sorted(_Path(ARTIFACTS_ROOT).rglob("openspec")):
                    if p.is_dir() and _has_content(p) and '.backups' not in str(p):
                        all_osps.append(p)
            except Exception as _rg_err:
                pass

            ws_prefix = str(_Path(ARTIFACTS_ROOT) / workspace) if workspace else None
            best = (next((p for p in all_osps if ws_prefix and str(p).startswith(ws_prefix)), None)
                    or next(iter(all_osps), None))
            if best:
                return best

            # Return original even if empty — caller shows "[No X found]" messages
            return candidate_path
        except Exception as _e:
            return None

    # ---- source line 7598 (Handler._search_vault_sources) ----
    def _search_vault_sources(self, src_list: list, vault_root, query: str) -> dict:
        """Read relevant files from ALL openspec dirs, return {src: text}.
        src_list is ignored — always searches full Vault for comprehensive context.
        Files are ranked by relevance to query (backlog item title): matching files first.
        HTML collapse tags stripped so LLM sees actual content, not metadata wrappers.
        """
        import re as _re
        from pathlib import Path

        src_dir = {
            "specs": "specs", "scenarios": "scenarios", "patterns": "patterns",
            "skills": "skills", "testcases": "testcases", "knowledge": "knowledge",
            "diagrams": "diagrams", "integration": "integration",
            "tech-debt": "tech-debt", "changes": "changes",
        }
        # Always search full Vault regardless of artifact's src field
        src_list = [s for s in src_dir if (Path(vault_root) / src_dir[s]).exists()] if vault_root else list(src_dir)

        def _clean(text: str) -> str:
            """Strip HTML collapse wrappers; keep headings and prose."""
            text = _re.sub(r'<details[^>]*>', '', text)
            text = _re.sub(r'</details>', '', text)
            text = _re.sub(r'<summary[^>]*>.*?</summary>', '', text, flags=_re.S)
            text = _re.sub(r'<!--.*?-->', '', text, flags=_re.S)
            text = _re.sub(r'\n{3,}', '\n\n', text)
            return text.strip()

        def _relevance(path: Path, q: str) -> int:
            """Score 0–3: higher = more relevant to query."""
            stem = path.stem.lower().replace('-', ' ').replace('_', ' ')
            parent = path.parent.name.lower().replace('-', ' ').replace('_', ' ')
            q_words = set(q.lower().split())
            score = sum(1 for w in q_words if w in stem or w in parent)
            return score

        result = {}
        if not vault_root:
            for s in src_list:
                result[s] = "[Vault not configured — no workspace/project selected]"
            return result

        vr = Path(vault_root)
        for src in src_list:
            folder = vr / src_dir.get(src, src)
            if not folder.exists():
                result[src] = f"[No {src} artifacts found in Vault at {folder}]"
                continue

            # Collect all files, sort by relevance DESC then name
            all_files = [f for f in folder.rglob("*.md") if '.backups' not in str(f)]
            all_files.sort(key=lambda f: (-_relevance(f, query), str(f)))

            texts, total = [], 0
            for f in all_files[:6]:
                try:
                    raw = f.read_text(encoding="utf-8", errors="ignore")
                    cleaned = _clean(raw)
                    snippet = cleaned[:1500]
                    texts.append(f"### {f.parent.name}/{f.stem}\n{snippet}")
                    total += len(snippet)
                    if total > 5000: break
                except Exception:
                    pass
            result[src] = "\n\n".join(texts) if texts else f"[No content in {src}]"
        return result

    # ---- source line 8739 (Handler._extract_vault_facts) ----
    def _extract_vault_facts(self, vault_ctx: dict, item_title: str) -> str:
        """Pre-extract concrete facts/requirements from vault so LLM cites specific files.
        Vault content already has ### folder/file headers. We extract per-file sections
        and pull out Purpose + REQ lines + Given scenarios."""
        import re as _re

        if not vault_ctx or all(v.startswith('[') for v in vault_ctx.values()):
            return ""

        lines_out = []
        for src_key, combined in vault_ctx.items():
            if combined.startswith('['):
                continue
            # Split combined content into individual file sections (headers are "### folder/stem")
            # Must have "/" in the header to distinguish from "### REQ-xxx" subsections
            file_sections = _re.split(r'\n(?=### [^\n]+/[^\n]+\n)', combined)
            for section in file_sections:
                # First line is "### folder/filename"
                hdr_m = _re.match(r'### (.+)', section)
                if not hdr_m:
                    continue
                file_ref = hdr_m.group(1).strip()
                src_display = file_ref.split('/')[-1] if '/' in file_ref else file_ref
                body = section[hdr_m.end():]

                # Purpose paragraph (after "## Purpose" heading)
                purpose_m = _re.search(r'##\s*Purpose\s*\n+(.+?)(?=\n##|\Z)', body, _re.S)
                purpose_text = ""
                if purpose_m:
                    purpose_text = purpose_m.group(1).strip()[:200]

                # REQ-xxx lines with description (blank line between heading and Mô tả is ok)
                req_blocks = _re.findall(
                    r'###\s+(REQ-[A-Z0-9\-]+[^\n]*)\n[\s\S]*?\*\*Mô tả:\*\*\s*([^\n]{0,150})',
                    body)

                # Given/When/Then scenarios
                givens = _re.findall(r'\*\*Given:\*\*\s*([^\n]{0,100})', body)[:3]

                if purpose_text or req_blocks or givens:
                    lines_out.append(f"\n**{file_ref}** → cite as `src={src_display}`:")
                    if purpose_text:
                        lines_out.append(f"  PURPOSE: {purpose_text[:160]}")
                    for req_id, desc in req_blocks[:5]:
                        lines_out.append(f"  REQ `{req_id.strip()}`: {desc.strip()[:120]}")
                    for g in givens[:2]:
                        lines_out.append(f"  SCENARIO Given: {g.strip()[:100]}")

        if not lines_out:
            return ""

        return (
            "## VAULT KEY FACTS — CITE THESE IN YOUR ANNOTATIONS\n"
            "Content from these vault files MUST be cited with VAULT annotations "
            "when you use the knowledge they contain:\n"
            + "\n".join(lines_out)
            + "\n\n"
        )

    # ---- source line 8798 (Handler._post_annotate_vault) ----
    def _post_annotate_vault(self, content: str, vault_ctx: dict) -> str:
        """Post-process LLM output: replace INFERRED annotations with VAULT where vault
        content actually supports the claim. Uses keyword matching against vault text."""
        import re as _re

        if not vault_ctx or all(v.startswith('[') for v in vault_ctx.values()):
            return content

        # Build keyword index: {phrase_lower: src_filename}
        kw_index = {}  # longer phrases first (more specific)
        for src_key, combined in vault_ctx.items():
            src_display = src_key.split('/')[-1] if '/' in src_key else src_key
            # File sections within combined text
            for file_section in _re.split(r'\n(?=### [^\n]+/[^\n]+\n)', combined):
                hdr = _re.match(r'### ([^\n]+)', file_section)
                file_name = hdr.group(1).split('/')[-1] if hdr else src_display
                body = file_section[hdr.end():] if hdr else file_section

                # REQ IDs (highest priority)
                for req in _re.findall(r'REQ-[A-Z0-9\-]+', body):
                    kw_index[req.lower()] = (file_name, 'HIGH')

                # API endpoints
                for ep in _re.findall(r'(?:GET|POST|PUT|DELETE|PATCH)\s+(/[\w\-/{}.?=&]+)', body):
                    if len(ep) > 4:
                        kw_index[ep.lower()] = (file_name, 'HIGH')

                # Domain-specific Vietnamese noun phrases (3+ chars, from Purpose paragraphs)
                purpose_m = _re.search(r'##\s*Purpose\s*\n+(.+?)(?=\n##|\Z)', body, _re.S)
                if purpose_m:
                    # Extract significant noun phrases from purpose text
                    words = _re.findall(r'[\wÀ-ɏḀ-ỿ]{4,}', purpose_m.group(1))
                    for w in set(words):
                        if w.lower() not in ('must','should','will','hệ','thống','được','phép','theo'):
                            kw_index.setdefault(w.lower(), (file_name, 'MEDIUM'))

                # Technical class/DTO names
                for cls in _re.findall(r'`([A-Z][a-zA-Z]{3,}(?:DTO|Service|Request|Response|Entity))`', body):
                    kw_index[cls.lower()] = (file_name, 'HIGH')

        if not kw_index:
            return content

        # Normalize kw_index keys to NFC to ensure Vietnamese diacritic matching
        import unicodedata as _uc
        kw_index = {_uc.normalize('NFC', k): v for k, v in kw_index.items()}

        # Sort keywords by length descending (match longest first)
        sorted_kw = sorted(kw_index.keys(), key=len, reverse=True)

        def _annotate_line(line: str) -> str:
            """Replace <!-- [INFERRED: ...] --> with VAULT if the line matches vault keywords."""
            if '<!-- [INFERRED:' not in line and '<!-- [VAULT:' not in line:
                return line
            if '<!-- [VAULT:' in line:
                return line  # already has vault annotation
            import unicodedata as _uc2
            line_lower = _uc2.normalize('NFC', line.lower())
            for kw in sorted_kw:
                if kw in line_lower:
                    file_name, conf = kw_index[kw]
                    return _re.sub(
                        r'\s*<!--\s*\[INFERRED:[^\]]*\]\s*-->',
                        f' <!-- [VAULT: confidence={conf}, src={file_name}] -->',
                        line
                    )
            return line  # keep INFERRED if no match

        result = []
        for line in content.split('\n'):
            result.append(_annotate_line(line))
        return '\n'.join(result)

