import asyncio
import os
import stat
import time
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from astro_app.auth import config as C
from astro_app.auth import hostid, oauth
from astro_app.auth.loopback import CallbackListener, free_port
from astro_app.auth.session import AuthSession, NotSignedIn, PlanUsageDisabled
from astro_app.auth.store import CredentialStore
from fake_openai import FakeOpenAI

pytestmark = pytest.mark.anyio
REDIRECT = C.redirect_uri(1455)
HOST = "urn:uuid:3f0e3f8a-2b3c-4d5e-8f60-718293a4b5c6"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "creds")


@pytest.fixture
def fake():
    return FakeOpenAI()


async def _sign_in(fake, store, account=None):
    pending, url = oauth.start(REDIRECT, HOST, account)
    params = fake.authorize(url)
    async with fake.client() as http:
        return await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http))


def _q(url):
    return dict(parse_qsl(urlsplit(url).query))


# ---------------------------------------------------------------- authorization URL
def test_new_registration_url():
    pending, url = oauth.start(REDIRECT, HOST)
    q = _q(url)
    assert url.startswith(C.AUTHORIZE_URL + "?")
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "astro-app"
    assert q["ext_agent_host_id"] == HOST and q["resource"] == "https://api.openai.com/v1"
    assert q["scope"].split() == ["openid", "profile", "email", "offline_access", "resource.invoke",
                                  "chatgpt.tokens.use.direct"]
    assert q["code_challenge_method"] == "S256" and "=" not in q["code_challenge"]
    assert q["redirect_uri"] == "http://127.0.0.1:1455/auth/callback"
    assert "id_token_hint" not in q and "prompt" not in q
    assert pending.is_registration


async def test_reauthorization_url_uses_issued_client_and_hints(fake, store):
    cred = await _sign_in(fake, store)
    _, url = oauth.start(REDIRECT, HOST, cred)
    q = _q(url)
    assert q["client_id"] == fake.issued_client_id and "agent_name_hint" not in q
    assert "id_token_hint" not in q and q["login_hint"] == fake.email  # no tokens in browser URLs


def test_each_attempt_is_fresh():
    a, _ = oauth.start(REDIRECT, HOST)
    b, _ = oauth.start(REDIRECT, HOST)
    assert len({a.state, b.state}) == 2 and a.nonce != b.nonce and a.code_verifier != b.code_verifier


# ---------------------------------------------------------------- callback + exchange
async def test_sign_in_saves_protected_credential(fake, store):
    cred = await _sign_in(fake, store)
    assert cred.client_id == fake.issued_client_id != "dynamic_agent_client"
    assert cred.subject == fake.subject and cred.email == fake.email and cred.plan_enabled
    assert cred.ext_agent_host_id == HOST and store.active().client_id == cred.client_id
    mode = stat.S_IMODE(os.stat(store.dir / f"{cred.client_id}.json").st_mode)
    assert mode == 0o600


@pytest.mark.parametrize("tamper,exc", [
    (lambda p: {**p, "state": "wrong"}, oauth.AuthStateError),
    (lambda p: {k: v for k, v in p.items() if k != "client_id"}, oauth.RegistrationIncomplete),
    (lambda p: {**p, "client_id": "dynamic_agent_client"}, oauth.RegistrationIncomplete),
    (lambda p: {k: v for k, v in p.items() if k != "code"}, oauth.AuthError),
])
async def test_callback_rejections(fake, store, tamper, exc):
    pending, url = oauth.start(REDIRECT, HOST)
    params = tamper(fake.authorize(url))
    async with fake.client() as http:
        with pytest.raises(exc):
            await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http))
    assert store.accounts() == []


async def test_declined_consent(fake, store):
    pending, url = oauth.start(REDIRECT, HOST)
    async with fake.client() as http:
        with pytest.raises(oauth.AuthDenied):
            await oauth.complete(pending, fake.authorize(url, approve=False), http, store, oauth.IdTokenVerifier(http))


async def test_expired_attempt(fake, store):
    pending, url = oauth.start(REDIRECT, HOST)
    params = fake.authorize(url)
    async with fake.client() as http:
        with pytest.raises(oauth.AuthStateError):
            await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http),
                                 now=time.time() + C.PENDING_TTL_S + 1)


async def test_reauth_rejects_different_client_id(fake, store):
    cred = await _sign_in(fake, store)
    pending, url = oauth.start(REDIRECT, HOST, cred)
    params = {**fake.authorize(url), "client_id": "oaiapp_other"}
    async with fake.client() as http:
        with pytest.raises(oauth.ClientMismatch):
            await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http))


async def test_reauth_rejects_other_account(fake, store):
    cred = await _sign_in(fake, store)
    fake.subject = "someone-else"
    with pytest.raises(oauth.IdentityMismatch):
        await _sign_in(fake, store, cred)
    assert store.load(cred.client_id).access_token == cred.access_token  # untouched


@pytest.mark.parametrize("override", [{"nonce": "wrong"}, {"aud": "oaiapp_other"}, {"azp": "oaiapp_other"}, {"iss": "https://evil.example"},
                                      {"exp": int(time.time()) - 600}, "badsig"])
async def test_id_token_validation(fake, store, override):
    if override == "badsig":
        fake.sign_with_other_key = True
    else:
        fake.id_token_overrides = override
    with pytest.raises(oauth.AuthError):
        await _sign_in(fake, store)
    assert not any(a.signed_in for a in store.accounts())  # at most the issued registration is kept


async def test_without_plan_scope_sign_in_kept_but_inference_disabled(fake, store):
    fake.granted_scope = "openid profile email offline_access"
    cred = await _sign_in(fake, store)
    assert not cred.plan_enabled
    async with fake.client() as http:
        with pytest.raises(PlanUsageDisabled):
            await AuthSession(store, http).access_token()


# ---------------------------------------------------------------- session, refresh, revoke
async def test_not_signed_in(store):
    async with httpx.AsyncClient() as http:
        with pytest.raises(NotSignedIn):
            await AuthSession(store, http).access_token()


async def test_refresh_near_expiry_rotates_token(fake, store):
    cred = await _sign_in(fake, store)
    async with fake.client() as http:
        s = AuthSession(store, http, clock=lambda: cred.saved_at + 3600 - 60)
        token = await s.access_token()
    new = store.active()
    assert token == new.access_token != cred.access_token
    assert new.refresh_token != cred.refresh_token and fake.refresh_tokens[cred.refresh_token] is False


async def test_no_refresh_while_fresh(fake, store):
    cred = await _sign_in(fake, store)
    async with fake.client() as http:
        assert await AuthSession(store, http).access_token() == cred.access_token
    assert ("POST", C.TOKEN_URL) in fake.calls and sum(1 for c in fake.calls if c[1] == C.TOKEN_URL) == 1


async def test_concurrent_refreshes_are_serialised(fake, store):
    cred = await _sign_in(fake, store)
    async with fake.client() as http:
        s = AuthSession(store, http)
        tokens = await asyncio.gather(*(s.access_token(rejected=cred.access_token) for _ in range(5)))
        assert await s.access_token(rejected=cred.access_token) == tokens[0]  # stale rejection: no new refresh
    assert len(set(tokens)) == 1
    assert sum(1 for c in fake.calls if c[1] == C.TOKEN_URL) == 2  # 1 code exchange + exactly 1 refresh


async def test_unusable_refresh_token_clears_tokens_keeps_registration(fake, store):
    cred = await _sign_in(fake, store)
    fake.refresh_tokens[cred.refresh_token] = False  # e.g. reused elsewhere
    async with fake.client() as http:
        with pytest.raises(oauth.InvalidGrant):
            await AuthSession(store, http).access_token(rejected=cred.access_token)
    kept = store.load(cred.client_id)
    assert kept is not None and not kept.signed_in and kept.subject == cred.subject


async def test_sign_out_revokes_and_clears(fake, store):
    cred = await _sign_in(fake, store)
    async with fake.client() as http:
        assert await AuthSession(store, http).sign_out() is True
    kept = store.load(cred.client_id)
    assert not kept.signed_in and kept.client_id == cred.client_id
    assert fake.refresh_tokens[cred.refresh_token] is False


async def test_sign_out_unconfirmed_still_clears_locally(fake, store):
    cred = await _sign_in(fake, store)
    fake.revoke_status = [503]

    async def no_sleep(_):
        pass

    async with fake.client() as http:
        assert await oauth.revoke(cred, http, store, sleep=no_sleep) is False
    assert not store.load(cred.client_id).signed_in


# ---------------------------------------------------------------- host id, loopback
def test_host_id_is_stable_and_opaque(tmp_path):
    a = hostid.host_id(tmp_path)
    assert a == hostid.host_id(tmp_path) and hostid.HOST_ID_RE.match(a)
    assert stat.S_IMODE(os.stat(tmp_path / "host_id").st_mode) == 0o600


async def test_loopback_listener_returns_callback_params():
    async with CallbackListener(0) as lis:
        port = lis.port
        assert port > 0 and lis.redirect_uri == f"http://127.0.0.1:{port}/auth/callback"
        async with httpx.AsyncClient() as http:
            miss = await http.get(f"http://127.0.0.1:{port}/other")
            hit = await http.get(f"{lis.redirect_uri}?code=abc&state=xyz&client_id=oaiapp_1")
        params = await lis.wait(timeout=5)
    assert miss.status_code == 404 and hit.status_code == 200 and "close this tab" in hit.text
    assert params == {"code": "abc", "state": "xyz", "client_id": "oaiapp_1"}


async def test_rejected_code_exchange_is_reported_with_details(fake, store):
    pending, url = oauth.start(REDIRECT, HOST)
    params = fake.authorize(url)
    fake.codes.clear()  # server no longer knows the code
    async with fake.client() as http:
        with pytest.raises(oauth.CodeExchangeRejected) as e:
            await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http))
    assert "error=invalid_grant" in str(e.value) and "sign-in code" in e.value.user_message
    assert params["code"] not in str(e.value)


async def test_rejected_registration_exchange_keeps_issued_client(fake, store):
    pending, url = oauth.start(REDIRECT, HOST)
    params = fake.authorize(url)
    fake.codes.clear()
    async with fake.client() as http:
        with pytest.raises(oauth.CodeExchangeRejected) as e:
            await oauth.complete(pending, params, http, store, oauth.IdTokenVerifier(http))
    saved = store.load(fake.issued_client_id)
    assert e.value.client_id == fake.issued_client_id and saved.registered_only and not saved.signed_in
    # documented recovery: re-authorize with the issued client id, which then completes
    cred = await _sign_in(fake, store, saved)
    assert cred.client_id == fake.issued_client_id and cred.subject == fake.subject and cred.signed_in
    assert len(store.accounts()) == 1


# ---------------------------------------------------------------- interactive flow
def _browser(fake, port_holder):
    """Simulated browser: visits the authorize URL, then follows the redirect to the loopback callback."""
    tasks = []

    def open_browser(url):
        q = _q(url)
        params = fake.authorize(url)

        async def follow():
            async with httpx.AsyncClient() as real:
                await real.get(q["redirect_uri"], params=params)
        tasks.append(asyncio.ensure_future(follow()))
        port_holder.append(q)
        return True
    return open_browser


async def test_interactive_sign_in_recovers_from_rejected_first_exchange(fake, store):
    from astro_app.auth.flow import sign_in
    fake.reject_next_exchanges = 1
    seen, logs = [], []
    async with fake.client() as http:
        cred = await sign_in(store, http, _browser(fake, seen), log=logs.append, port=0, timeout=5)
    assert cred.signed_in and cred.client_id == fake.issued_client_id
    assert [q["client_id"] for q in seen] == ["dynamic_agent_client", fake.issued_client_id]
    assert "agent_name_hint" not in seen[1] and any("once more" in m for m in logs)
    assert len(store.accounts()) == 1


async def test_interactive_sign_in_gives_up_after_second_rejection(fake, store):
    from astro_app.auth.flow import sign_in
    fake.reject_next_exchanges = 2
    async with fake.client() as http:
        with pytest.raises(oauth.CodeExchangeRejected):
            await sign_in(store, http, _browser(fake, []), port=0, timeout=5)
    assert [a.registered_only for a in store.accounts()] == [True]


def test_identity_only_url_requests_no_plan_scopes():
    _, url = oauth.start(REDIRECT, HOST, identity_only=True)
    assert _q(url)["scope"] == "openid profile email"


def test_host_id_can_be_omitted():
    _, url = oauth.start(REDIRECT, HOST, send_host_id=False)
    assert "ext_agent_host_id" not in _q(url)
