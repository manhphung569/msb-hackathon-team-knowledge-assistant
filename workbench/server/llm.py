"""Generic LLM-calling helpers, split out of orchestrate.py (2026-09-14) to break a circular
import: conversations.py needs these for elicit-synthesize, orchestrate.py needs them for
artifact generation, and orchestrate.py also needs conversations.py's _get_or_create_conv/
_save_messages — so these can't live in orchestrate.py without a cycle.
Extracted verbatim from pdlc-core/adlc/tools/dashboard/server.py (source lines 10717-10806)."""

import json
import os

# ---- source line 10717 (_parse_retry_after) ----
def _parse_retry_after(exc) -> int:
    """Extract Retry-After seconds from a 429 HTTPError. Defaults to 12s."""
    try:
        import urllib.error as _ue
        if isinstance(exc, _ue.HTTPError):
            ra = exc.headers.get("Retry-After") or ""  # HTTPMessage.get is case-insensitive
            if ra.strip().isdigit():
                return max(5, int(ra.strip()))
    except Exception:
        pass
    return 12  # default: safe floor for 5 RPM providers (60s / 5 = 12s)


# ---- source line 10729 (_bl_call_llm_ex) ----
def _bl_call_llm_ex(system: str, user_msg: str, max_tokens: int = 3000):
    """Call configured LLM. Returns (content: str, usage: dict)."""
    import urllib.request as _ur
    import urllib.error as _ue
    forced   = os.environ.get("VAULT_CHAT_PROVIDER", "").lower()
    qwen_key = os.environ.get("DASHSCOPE_API_KEY", "")
    anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
    gh_key   = os.environ.get("GITHUB_TOKEN", "")
    oai_key  = os.environ.get("OPENAI_API_KEY", "")
    gn_key   = os.environ.get("GREENNODE_API_KEY", "")
    gn_base  = os.environ.get("GREENNODE_BASE_URL", "")
    model_ov = os.environ.get("VAULT_CHAT_MODEL", "")

    # greennode (MSB AI Hackathon 2026 co-organizer platform, docs/keys pending) is opt-in only:
    # explicit VAULT_CHAT_PROVIDER=greennode, or auto-detected last (lowest priority) so it never
    # silently overrides an already-working qwen/anthropic/github/openai setup for anyone who
    # hasn't asked for it.
    if   forced == "qwen"      or (not forced and qwen_key and not anth_key and not gh_key): provider = "qwen"
    elif forced == "anthropic" or (not forced and anth_key):                                  provider = "anthropic"
    elif forced == "github"    or (not forced and gh_key and not oai_key):                   provider = "github"
    elif forced == "openai"    or (not forced and oai_key):                                  provider = "openai"
    elif forced == "greennode" or (not forced and gn_key and gn_base):                        provider = "greennode"
    else: raise RuntimeError("No LLM API key configured")

    if provider == "github": max_tokens = min(max_tokens, 1000)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]

    def _call_once():
        if provider in ("github", "openai"):
            url = ("https://models.inference.ai.azure.com" if provider == "github"
                   else os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")) + "/chat/completions"
            key = gh_key if provider == "github" else oai_key
            model = model_ov or "gpt-4o-mini"
            payload = json.dumps({"model": model, "messages": msgs, "max_completion_tokens": max_tokens}).encode()
            req = _ur.Request(url, data=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            u = data.get("usage", {})
            return (data["choices"][0]["message"]["content"],
                    {"prompt": u.get("prompt_tokens",0), "completion": u.get("completion_tokens",0),
                     "total": u.get("total_tokens",0), "model": model})
        elif provider == "anthropic":
            import anthropic as _ant
            client = _ant.Anthropic(api_key=anth_key)
            model = model_ov or "claude-sonnet-4-6"
            resp = client.messages.create(model=model, max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": user_msg}])
            u = resp.usage
            return (resp.content[0].text,
                    {"prompt": u.input_tokens, "completion": u.output_tokens,
                     "total": u.input_tokens + u.output_tokens, "model": model})
        elif provider == "qwen":
            url = os.environ.get("DASHSCOPE_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1") + "/chat/completions"
            model = model_ov or "qwen-plus"
            payload = json.dumps({"model": model, "messages": msgs, "max_tokens": max_tokens}).encode()
            req = _ur.Request(url, data=payload, headers={"Authorization": f"Bearer {qwen_key}", "Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            u = data.get("usage", {})
            return (data["choices"][0]["message"]["content"],
                    {"prompt": u.get("input_tokens", u.get("prompt_tokens",0)),
                     "completion": u.get("output_tokens", u.get("completion_tokens",0)),
                     "total": u.get("total_tokens",0), "model": model})
        else:  # greennode — assumed OpenAI-compatible /chat/completions, base_url from env.
               # If the real API differs, only this branch needs to change.
            url = gn_base.rstrip("/") + "/chat/completions"
            model = model_ov or os.environ.get("GREENNODE_MODEL", "greennode-default")
            payload = json.dumps({"model": model, "messages": msgs, "max_tokens": max_tokens}).encode()
            req = _ur.Request(url, data=payload, headers={"Authorization": f"Bearer {gn_key}", "Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            u = data.get("usage", {})
            return (data["choices"][0]["message"]["content"],
                    {"prompt": u.get("prompt_tokens",0), "completion": u.get("completion_tokens",0),
                     "total": u.get("total_tokens",0), "model": model})

    for attempt in range(3):
        try:
            return _call_once()
        except _ue.HTTPError as e:
            if e.code == 429:
                # Don't sleep server-side (blocks single-threaded server).
                # Return 429 immediately so the frontend can handle retry with countdown.
                raise
            if e.code == 413 and attempt < 2:
                # Prompt too large — trim user_msg by 30% and retry
                trim = int(len(msgs[1]["content"]) * 0.7)
                msgs[1]["content"] = msgs[1]["content"][:trim] + "\n...[trimmed for length]"
                continue
            raise


# ---- source line 10803 (_bl_call_llm) ----
def _bl_call_llm(system: str, user_msg: str, max_tokens: int = 3000) -> str:
    """Backward-compat wrapper — returns content string only."""
    content, _ = _bl_call_llm_ex(system, user_msg, max_tokens)
    return content
