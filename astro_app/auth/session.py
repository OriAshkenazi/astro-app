"""Active ChatGPT account and a valid access token for it."""
from __future__ import annotations

import time

import httpx

from . import oauth
from .store import Credential, CredentialStore
from .config import REFRESH_MARGIN_S


class NotSignedIn(oauth.AuthError):
    user_message = "Sign in with ChatGPT to continue."


class PlanUsageDisabled(oauth.AuthError):
    user_message = ("ChatGPT plan usage was not granted. Enable it from Settings to generate readings "
                    "with your ChatGPT plan.")


class AuthSession:
    def __init__(self, store: CredentialStore, http: httpx.AsyncClient, clock=time.time):
        self.store, self.http, self.clock = store, http, clock

    def current(self) -> Credential | None:
        return self.store.active()

    def _needs_refresh(self, c: Credential) -> bool:
        now = self.clock()
        if c.earliest_refresh_at and now < c.earliest_refresh_at:
            return False
        return now >= c.expires_at - REFRESH_MARGIN_S

    async def access_token(self, rejected: str | None = None) -> str:
        """A usable access token for inference.

        Refreshes when close to expiry, or when `rejected` (a token the API just answered 401 to)
        is still the stored one. If another task already replaced it, the newer token is returned."""
        c = self.current()
        if c is None or not c.signed_in:
            raise NotSignedIn("no active signed-in account")
        if not c.plan_enabled:
            raise PlanUsageDisabled("chatgpt.tokens.use.direct not granted")
        if (rejected is not None and c.access_token == rejected) or self._needs_refresh(c):
            c = await oauth.refresh(c, self.http, self.store, now=self.clock())
        return c.access_token  # type: ignore[return-value]

    async def sign_out(self) -> bool:
        c = self.current()
        if c is None:
            return True
        return await oauth.revoke(c, self.http, self.store)
