"""HTTP routing for the migrated Vault server — trimmed from pdlc-core's Handler.do_GET/
do_POST/do_DELETE/do_PATCH/do_PUT (server.py:3128-3407). Only branches whose target moved to
workbench are kept, in the SAME order or as the original (route matching is first-match-wins,
order matters). Routes removed here are ADLC/Discovery-dashboard features that stay in
pdlc-core untouched — see docs/decisions/2026-09-14_migrate-dashboard-vault-subsystem-from-pdlc-core.md.
"""

import http.server
import re
import sys
from urllib.parse import urlparse

from .config import STATIC_DIR
from .admin import AdminMixin
from .auth import AuthMixin
from .backlog import BacklogMixin
from .channels import ChannelsMixin
from .chat import ChatMixin
from .conversations import ConversationsMixin
from .discovery_browse import DiscoveryBrowseMixin
from .extraction import ExtractionMixin
from .orchestrate import OrchestrateMixin
from .pages import PagesMixin
from .search import SearchMixin
from .shared import SharedMixin
from .teams import TeamsMixin


class VaultHandler(
    AdminMixin, AuthMixin, BacklogMixin, ChannelsMixin, ChatMixin, ConversationsMixin,
    DiscoveryBrowseMixin, ExtractionMixin, OrchestrateMixin, PagesMixin, SearchMixin,
    SharedMixin, TeamsMixin,
    http.server.SimpleHTTPRequestHandler,
):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC_DIR), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- GET (from server.py do_GET, vault-relevant branches only) ----
    def do_GET(self):
        req_path = urlparse(self.path).path
        if req_path == "/vault/auth/me":
            return self._handle_vault_me()
        if req_path in ("/vault/admin/users",):
            return self._handle_vault_admin_users_get()
        if req_path in ("/vault/admin/token-requests",):
            return self._handle_vault_admin_token_requests_get()
        if req_path == "/vault/conversations":
            return self._handle_vault_conv_list()
        if req_path.startswith("/vault/conversations/"):
            return self._handle_vault_conv_get()
        if req_path == "/vault/channels":
            return self._handle_channels_list()
        if req_path == "/integrations/agents":
            return self._handle_vault_agent_list()
        if req_path in ("/integrations/teams/callback", "/integrations/teams/callback/"):
            return self._handle_teams_connect_callback()
        if req_path == "/integrations/teams/accounts":
            return self._handle_teams_accounts_list()
        if req_path == "/integrations/teams/chats":
            return self._handle_teams_chats_list()
        if req_path == "/integrations/teams/bindings":
            return self._handle_teams_bindings_list()
        m = re.match(r"^/integrations/teams/bindings/(\d+)/events$", req_path)
        if m:
            return self._handle_teams_binding_events(int(m.group(1)))
        m = re.match(r"^/integrations/teams/bindings/(\d+)/threads$", req_path)
        if m:
            return self._handle_teams_binding_threads(int(m.group(1)))
        if req_path == "/integrations/teams/drafts":
            return self._handle_teams_drafts_list()
        if req_path == "/vault/backlog":
            return self._handle_backlog_list()
        if req_path.startswith("/vault/backlog/") and "/item" not in req_path:
            return self._handle_backlog_get(req_path)
        if req_path == "/vault/whisper-key":
            return self._handle_whisper_key()
        if req_path == "/vault/orchestrate/provider":
            return self._handle_orch_provider_info()
        if req_path.startswith("/vault/orchestrate/versions/"):
            return self._handle_orch_version_list(req_path)
        if req_path.startswith("/vault/orchestrate/history/"):
            return self._handle_orch_history(req_path)
        if req_path.startswith("/vault/orchestrate/artifact/"):
            return self._handle_orch_artifact_versions(req_path)
        if req_path.startswith("/vault/elicit/sessions/"):
            ck = req_path.split("/vault/elicit/sessions/", 1)[-1].strip("/")
            return self._handle_elicit_sessions(ck)
        if req_path.startswith("/vault/elicit/syntheses/"):
            ck = req_path.split("/vault/elicit/syntheses/", 1)[-1].strip("/")
            return self._handle_elicit_syntheses(ck)
        if req_path.startswith("/vault/elicit/synthesis/"):
            did = req_path.split("/vault/elicit/synthesis/", 1)[-1].strip("/")
            return self._handle_elicit_synthesis_get(did)
        if req_path == "/vault/changes":
            return self._handle_changes_list()
        if req_path in ("/vault/admin", "/vault/admin/"):
            return self._serve_vault_admin()
        if req_path == "/vault/admin/stats":
            return self._handle_vault_admin_stats()
        if req_path == "/vault/admin/usage.csv":
            return self._handle_vault_admin_usage_csv()
        if req_path in ("/vault/setup", "/vault/setup/"):
            return self._serve_vault_setup()
        if req_path in ("/vault", "/vault/", "/vault/index.html"):
            return self._serve_vault_page()
        if req_path in ("/vault/docs", "/vault/docs.html"):
            return self._serve_vault_docs()
        if req_path == "/vault/artifact-guide":
            return self._serve_vault_artifact_guide()
        # ---- the 4 ported (independent) Discovery-browse routes ----
        if req_path in ("/discovery/workspaces", "/discovery/workspaces/"):
            return self._serve_discovery_workspaces()
        if self.path.startswith("/discovery/file"):
            return self._serve_discovery_file()
        if self.path.startswith("/discovery/tree"):
            return self._serve_discovery_tree()
        if self.path.startswith("/discovery/workspace"):
            return self._serve_discovery_workspace()
        # everything else (ADLC/Discovery dashboard, quartz, config, api/versions) stays
        # in pdlc-core — falls through to static-file serving here, same as upstream.
        return super().do_GET()

    # ---- POST ----
    def do_POST(self):
        if self.path == "/vault/whisper-transcribe":
            return self._handle_whisper_transcribe()
        if self.path.startswith("/vault/auth/login"):
            return self._handle_vault_login()
        if self.path.startswith("/vault/auth/logout"):
            return self._handle_vault_logout()
        if self.path.startswith("/vault/auth/change-password"):
            return self._handle_vault_change_password()
        if self.path.startswith("/vault/token-request"):
            return self._handle_vault_token_request()
        if self.path.startswith("/vault/admin/users"):
            return self._handle_vault_admin_users_post()
        if self.path.startswith("/vault/elicit/synthesize/"):
            ck = self.path.split("/vault/elicit/synthesize/", 1)[-1].split("?")[0].strip("/")
            return self._handle_elicit_synthesize(ck)
        req_path = urlparse(self.path).path
        if self.path.startswith("/vault/conversations/") and self.path.endswith("/summarize"):
            return self._handle_vault_conv_summarize()
        if req_path == "/vault/orchestrate":
            return self._handle_orchestrate()
        if req_path == "/vault/orchestrate/regen":
            return self._handle_orch_regen()
        if req_path == "/vault/orchestrate/section":
            return self._handle_orch_section()
        if req_path == "/vault/orchestrate/full":
            return self._handle_orch_full()
        if req_path == "/vault/orchestrate/cross-review":
            return self._handle_orch_cross_review()
        if req_path == "/vault/orchestrate/review-upload":
            return self._handle_orch_review_upload()
        if req_path == "/vault/orchestrate/version/create":
            return self._handle_orch_version_create()
        if req_path == "/vault/orchestrate/version/refresh-snapshot":
            return self._handle_orch_version_refresh_snapshot()
        if req_path == "/vault/orchestrate/save":
            return self._handle_orch_save_run()
        if req_path == "/vault/orchestrate/artifact/save":
            return self._handle_orch_save_artifact()
        if self.path == "/vault/backlog/rename":
            return self._handle_backlog_rename()
        if self.path == "/vault/backlog":
            return self._handle_backlog_create()
        if re.match(r"^/vault/backlog/item/\d+/finalize$", req_path):
            return self._handle_backlog_item_finalize(self.path)
        if self.path.startswith("/vault/backlog/") and self.path.endswith("/finalize") and "/item/" not in req_path:
            return self._handle_backlog_finalize(self.path)
        if self.path.startswith("/vault/backlog/") and self.path.endswith("/refine"):
            return self._handle_backlog_refine(self.path)
        if self.path == "/vault/backlog/item":
            return self._handle_backlog_item_create()
        if self.path == "/vault/channels/sync":
            return self._handle_channels_sync()
        if self.path == "/vault/channels":
            return self._handle_channel_create()
        if re.match(r"^/vault/channels/[^/]+/artifacts$", urlparse(self.path).path):
            key = urlparse(self.path).path.split("/")[3]
            return self._handle_channel_artifact_add(key)
        if self.path == "/integrations/agents":
            return self._handle_vault_agent_create()
        if self.path == "/integrations/teams/connect":
            return self._handle_teams_connect_start()
        if self.path == "/integrations/teams/bindings":
            return self._handle_teams_binding_create()
        m = re.match(r"^/integrations/teams/bindings/(\d+)/sync$", req_path)
        if m:
            return self._handle_teams_binding_sync(int(m.group(1)))
        m = re.match(r"^/integrations/teams/drafts/(\d+)/(approve|reject|post)$", req_path)
        if m:
            action = m.group(2)
            if action == "approve":
                return self._handle_teams_draft_approve(int(m.group(1)))
            if action == "reject":
                return self._handle_teams_draft_reject(int(m.group(1)))
            return self._handle_teams_draft_post(int(m.group(1)))
        if self.path.startswith("/vault/setup"):
            return self._handle_vault_setup_post()
        if self.path.startswith("/vault/extract-file"):
            return self._handle_vault_extract_file()
        if self.path.startswith("/discovery/ask"):
            return self._serve_discovery_ask()
        self.send_error(404)

    # ---- DELETE (every original branch is vault-owned — kept in full) ----
    def do_DELETE(self):
        req_path = urlparse(self.path).path
        if req_path.startswith("/vault/conversations/"):
            return self._handle_vault_conv_delete()
        if req_path.startswith("/vault/backlog/item/"):
            return self._handle_backlog_item_delete(req_path)
        if req_path.startswith("/vault/backlog/") and "/item" not in req_path:
            return self._handle_backlog_delete(req_path)
        m = re.match(r"^/vault/channels/([^/]+)/artifacts/(\d+)$", req_path)
        if m:
            return self._handle_channel_artifact_delete(m.group(1), int(m.group(2)))
        m = re.match(r"^/vault/channels/([^/]+)$", req_path)
        if m:
            return self._handle_channel_delete(m.group(1))
        m = re.match(r"^/integrations/agents/(\d+)$", req_path)
        if m:
            return self._handle_vault_agent_delete(int(m.group(1)))
        m = re.match(r"^/integrations/teams/accounts/(\d+)$", req_path)
        if m:
            return self._handle_teams_account_delete(int(m.group(1)))
        m = re.match(r"^/integrations/teams/bindings/(\d+)$", req_path)
        if m:
            return self._handle_teams_binding_delete(int(m.group(1)))
        self.send_error(404)

    # ---- PATCH (all vault-owned except /discovery/review-item, dropped — CORE_KEEP) ----
    def do_PATCH(self):
        req_path = urlparse(self.path).path
        m = re.match(r"^/vault/channels/([^/]+)$", req_path)
        if m:
            return self._handle_channel_update(m.group(1))
        m = re.match(r"^/integrations/agents/(\d+)$", req_path)
        if m:
            return self._handle_vault_agent_update(int(m.group(1)))
        m = re.match(r"^/integrations/teams/bindings/(\d+)$", req_path)
        if m:
            return self._handle_teams_binding_update(int(m.group(1)))
        self.send_error(404)

    # ---- PUT (every original branch is vault-owned — kept in full) ----
    def do_PUT(self):
        req_path = urlparse(self.path).path
        if req_path.startswith("/vault/admin/users/"):
            return self._handle_vault_admin_users_put()
        if req_path.startswith("/vault/admin/token-requests/"):
            return self._handle_vault_admin_token_request_put()
        if req_path.startswith("/vault/backlog/") and not req_path.startswith("/vault/backlog/item/"):
            return self._handle_backlog_update(req_path)
        if req_path.startswith("/vault/backlog/item/") and req_path.endswith("/coverage"):
            return self._handle_backlog_item_coverage(req_path)
        if req_path.startswith("/vault/backlog/item/"):
            return self._handle_backlog_item_update(req_path)
        if req_path.startswith("/vault/orchestrate/artifact/"):
            return self._handle_orch_artifact_update(req_path)
        m = re.match(r"^/vault/channels/([^/]+)/artifacts/(\d+)$", req_path)
        if m:
            return self._handle_channel_artifact_update(m.group(1), int(m.group(2)))
        self.send_error(404)
