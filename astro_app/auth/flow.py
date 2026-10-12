"""Interactive sign-in: loopback listener + browser + documented recovery.

If the first code exchange of a new registration is rejected (invalid_grant), the issued client
ID has already been saved, and the flow re-authorizes once with it, as the SIWC docs prescribe.
That second pass shows the account picker only: no new registration, no consent screen.
"""
from __future__ import annotations

import base64
import hashlib
import time
from typing import Awaitable, Callable

import httpx

from . import hostid, oauth
from .loopback import CallbackListener, free_port
from .store import Credential, CredentialStore

OpenBrowser = Callable[[str], Awaitable[bool] | bool]
Log = Callable[[str], None]


def redacted(params: dict, redirect: str) -> str:
    """Callback summary that is safe to share: parameter names, client-id prefix, scopes, never the code."""
    shown = {k: ("<present>" if k in ("code", "state") else v) for k, v in params.items()}
    if "client_id" in shown:
        shown["client_id"] = shown["client_id"][:10] + "…"
    return f"callback on {redirect} with {shown}"


def _pkce_ok(pending: oauth.PendingAuth, url: str) -> bool:
    from urllib.parse import parse_qsl, urlsplit
    sent = dict(parse_qsl(urlsplit(url).query)).get("code_challenge")
    calc = base64.urlsafe_b64encode(hashlib.sha256(pending.code_verifier.encode()).digest()).rstrip(b"=").decode()
    return sent == calc


async def sign_in(store: CredentialStore, http: httpx.AsyncClient, open_browser: OpenBrowser,
                  account: Credential | None = None, enable_plan: bool = False, log: Log = lambda m: None,
                  debug: Log | None = None, port: int | None = None, timeout: float | None = None,
                  identity_only: bool = False) -> Credential:
    host = hostid.host_id(store.dir.parent)
    for attempt in (1, 2):
        async with CallbackListener(port if port is not None else free_port()) as listener:
            pending, url = oauth.start(listener.redirect_uri, host, account, force_consent=enable_plan,
                                       identity_only=identity_only)
            opened = open_browser(url)
            if hasattr(opened, "__await__"):
                opened = await opened
            if not opened:
                log(f"Open this address in your browser:\n{url}")
            params = await (listener.wait(timeout) if timeout else listener.wait())
            got = time.monotonic()
            if debug:
                debug(f"attempt {attempt}: " + redacted(params, listener.redirect_uri))
                debug(f"pkce self-check: {_pkce_ok(pending, url)}; verifier length {len(pending.code_verifier)}")
            try:
                return await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http))
            except oauth.CodeExchangeRejected as e:
                if debug:
                    debug(f"{type(e).__name__}: {e}")
                    debug(f"exchange sent {time.monotonic() - got:.2f}s after the callback")
                    for h in listener.hits:
                        debug(f"listener saw: {h}")
                if attempt == 2:
                    raise
                account = store.load(e.client_id)
                log("ChatGPT asked to confirm once more. Choose the same account in the browser…")
    raise AssertionError("unreachable")
