"""Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source line numbers noted per block). RAW EXTRACTION — not yet import-fixed or adapted; see workbench/EXTRACTION_NOTES.md."""

from pathlib import Path
from .config import VAULT_PAGE_DIR, VAULT_PAGE_FILE, VAULT_DOCS_FILE, STATIC_DIR

# ---- source line 287 (VAULT_PAGE_TEMPLATE) ----
VAULT_PAGE_TEMPLATE = """<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Vault</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --brand: #FF7A3D; --brand-bg: rgba(255,122,61,.11); --brand-border: rgba(255,122,61,.24);
            --bg: #FFF9F1; --panel: rgba(255,252,246,.92); --border: #E9DAC5; --text: #2D241C; --muted: #7D6C5F;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body { height: 100%; background: radial-gradient(circle at top left,#FFF2DE 0,#FFF9F1 42%,#ECFBFF 100%); font: 13px/1.5 'Inter', 'Segoe UI', sans-serif; color: var(--text); display: flex; flex-direction: column; }
        .vault-header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 0 20px; background: var(--panel);
            border-bottom: 1px solid var(--border);
            box-shadow: 0 12px 26px rgba(121,77,27,.08); flex-shrink: 0; backdrop-filter: blur(16px);
        }
        .vault-header-left { display: flex; align-items: center; gap: 16px; }
        .vault-logo { font-size: 15px; font-weight: 700; padding: 12px 0; white-space: nowrap; display: flex; align-items: center; gap: 10px; }
        .vault-logo em { color: var(--brand); font-style: normal; }
        .vault-logo-icon { width: 38px; height: 38px; border-radius: 12px; display: inline-flex; align-items: center; justify-content: center; background: radial-gradient(circle at 30% 30%,#FFF7E9 0,#FFD6A3 34%,#FF9C63 72%,#FF6B46 100%); box-shadow: 0 12px 28px rgba(255,122,61,.22), inset 0 1px 0 rgba(255,255,255,.75); border: 1px solid rgba(255,145,78,.36); }
        .vault-logo-icon svg { width: 24px; height: 24px; display: block; filter: drop-shadow(0 2px 6px rgba(157,68,24,.18)); }
        .vault-nav { display: flex; gap: 2px; }
        .vault-tab {
            padding: 13px 16px; font: 12px/1 'Inter', sans-serif; font-weight: 500;
            color: var(--muted); border: none; border-bottom: 2px solid transparent;
            background: none; cursor: pointer; white-space: nowrap;
            text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
            transition: color .15s, border-color .15s;
        }
        .vault-tab:hover { color: var(--text); }
        .vault-tab.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
        .vault-header-right { display: flex; align-items: center; gap: 12px; }
        .back-link { font-size: 12px; font-weight: 600; color: var(--brand); text-decoration: none; white-space: nowrap; }
        .back-link:hover { text-decoration: underline; }
        iframe { flex: 1; width: 100%; border: 0; background: #fff; display: block; }
        iframe.hidden { display: none; }
        @media (max-width: 760px) {
            .vault-header { padding: 10px 14px; flex-wrap: wrap; align-items: flex-start; gap: 10px; }
            .vault-header-left { flex-wrap: wrap; gap: 10px; }
            .vault-nav { width: 100%; overflow-x: auto; }
            .vault-header-right { width: 100%; justify-content: flex-start; }
            .vault-tab { padding: 10px 12px; }
        }
    </style>
</head>
<body>
    <div class="vault-header">
        <div class="vault-header-left">
            <div class="vault-logo"><span class="vault-logo-icon" aria-hidden="true"><svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M30 14c-4-4-10.6-4-14.6 0s-4 10.6 0 14.6c-4 4-4 10.6 0 14.6s10.6 4 14.6 0l2-2V16l-2-2Z" fill="#FF7A3D"/><path d="M34 14c4-4 10.6-4 14.6 0s4 10.6 0 14.6c4 4 4 10.6 0 14.6s-10.6 4-14.6 0l-2-2V16l2-2Z" fill="#FFB14D"/><path d="M24 20c-1.8 0-3.3 1.8-3.3 4s1.5 4 3.3 4m0 0c-2.2 0-4 2-4 4.7S21.8 41 24 41" stroke="#FFF8F1" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><path d="M40 20c1.8 0 3.3 1.8 3.3 4s-1.5 4-3.3 4m0 0c2.2 0 4 2 4 4.7S42.2 41 40 41" stroke="#FFF8F1" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><path d="M32 17v24" stroke="#FFE8D6" stroke-width="3" stroke-linecap="round"/><path d="M29 42v8c0 1.7 1.3 3 3 3s3-1.3 3-3v-8" fill="#1DB4A7"/><path d="M27 51h10" stroke="#0D9488" stroke-width="3" stroke-linecap="round"/></svg></span>Vault <em>Knowledge</em></div>
            <nav class="vault-nav" id="vault-nav">
                <button class="vault-tab active" data-pane="browser" onclick="switchPane('browser')">🗂 Knowledge Browser</button>
                <a class="vault-tab" href="/vault/docs" id="docs-link">📚 Vault Docs</a>
            </nav>
        </div>
        <div class="vault-header-right">
            <a class="back-link" href="/"><- Dashboard</a>
        </div>
    </div>
    <iframe id="vault-frame" title="Vault Knowledge Browser"></iframe>
    <script>
        (function () {
            const params = new URLSearchParams(window.location.search);
            const target = new URL("/", window.location.origin);
            target.searchParams.set("standalone", "vault");
            if (params.get("workspace")) target.searchParams.set("workspace", params.get("workspace"));
            if (params.get("project"))   target.searchParams.set("project",   params.get("project"));
            document.getElementById("vault-frame").src = target.toString();
        })();
        function switchPane(pane) {
            document.querySelectorAll('.vault-tab').forEach(t => t.classList.remove('active'));
            document.querySelector('[data-pane="' + pane + '"]').classList.add('active');
        }
    </script>
</body>
</html>"""


# ---- source line 807 (_ensure_vault_page) ----
def _ensure_vault_page() -> Path:
    VAULT_PAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not VAULT_PAGE_FILE.exists() or VAULT_PAGE_FILE.read_text(encoding="utf-8") != VAULT_PAGE_TEMPLATE:
        VAULT_PAGE_FILE.write_text(VAULT_PAGE_TEMPLATE, encoding="utf-8")
    return VAULT_PAGE_FILE


class PagesMixin:
    # ---- source line 3420 (Handler._serve_text_file) ----
    def _serve_text_file(self, file_path: Path, content_type: str, code: int = 200):
        body = file_path.read_bytes()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- source line 3429 (Handler._serve_vault_page) ----
    def _serve_vault_page(self):
        try:
            ui = STATIC_DIR / "vault_ui.html"  # was: ROOT / "adlc/tools/dashboard/vault_ui.html" in pdlc-core
            target = ui if ui.exists() else _ensure_vault_page()
            return self._serve_text_file(target, "text/html; charset=utf-8")
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

    # ---- source line 3437 (Handler._serve_vault_docs) ----
    def _serve_vault_docs(self):
        try:
            if not VAULT_DOCS_FILE.exists():
                return self._send_json({"error": "Vault Docs page not found. Run server from pdlc root."}, 404)
            return self._serve_text_file(VAULT_DOCS_FILE, "text/html; charset=utf-8")
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

    # ---- source line 3445 (Handler._serve_vault_artifact_guide) ----
    def _serve_vault_artifact_guide(self):
        """GET /vault/artifact-guide — serve artifact-guide.md as plain text for docs renderer"""
        try:
            guide = STATIC_DIR / "artifact-guide.md"  # was: ARTIFACTS_ROOT/_workspace/vault/... in pdlc-core
            if not guide.exists():
                self.send_response(404)
                self.end_headers()
                return
            content = guide.read_text(encoding="utf-8", errors="replace")
            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

