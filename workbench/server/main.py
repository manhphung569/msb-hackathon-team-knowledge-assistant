"""Entry point for the migrated Vault server. Adapted from pdlc-core's server.py main()
(source line 10811) — see docs/decisions/2026-09-14_migrate-dashboard-vault-subsystem-from-pdlc-core.md.
Run: python -m workbench.server.main --port 8766
"""

import argparse
import socketserver
import webbrowser

from .config import DATA_DIR, DEFAULT_PORT, STATIC_DIR
from .db import _init_db
from .dispatch import VaultHandler


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    _init_db()  # create vault.db tables + seed admin if needed (no-op if already migrated)

    with ReusableTCPServer(("0.0.0.0", args.port), VaultHandler) as httpd:
        url = f"http://localhost:{args.port}/vault/"
        print(f"Vault -> {url}")
        print("  Ctrl+C to stop.")
        if not args.no_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye.")
        except Exception as _ex:
            import traceback
            print(f"\n[ERROR] serve_forever crashed: {_ex}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
