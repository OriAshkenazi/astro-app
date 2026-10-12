"""astro-app command line.

  astro-app login            Continue with ChatGPT (opens the browser, loopback callback)
  astro-app accounts         list saved ChatGPT account registrations
  astro-app models           models available on the active account's ChatGPT plan
  astro-app ping             one tiny inference on the ChatGPT plan, end to end
  astro-app logout           revoke the session and clear local tokens
  astro-app chart ...        build a chart offline and write the wheel + sections 1-5
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import webbrowser
from pathlib import Path

import httpx

from . import __version__
from .auth import config as C
from .auth import oauth
from .auth.session import AuthSession
from .auth.store import CredentialStore


def _out(msg: str = "") -> None:
    print(msg, flush=True)


async def _login(args) -> int:
    from .auth.flow import sign_in
    store = CredentialStore()
    saved = store.accounts()
    account = None
    if args.account:
        account = next((a for a in saved if args.account in (a.client_id, a.email)), None)
        if account is None:
            _out(f"No saved account matches {args.account!r}. Run `astro-app accounts`.")
            return 2
    elif not args.new and len(saved) == 1:
        account = saved[0]  # reuse the existing registration instead of registering the app again
    elif not args.new and len(saved) > 1:
        _out("Several ChatGPT accounts are saved; choose one with --account, or add one with --new:")
        return _accounts(args) or 2
    _out("Opening your browser to Continue with ChatGPT…")
    debug = (lambda m: _out("debug: " + m)) if args.debug else None
    async with httpx.AsyncClient() as http:
        try:
            cred = await sign_in(store, http, webbrowser.open, account, enable_plan=args.enable_plan, log=_out,
                                 debug=debug, identity_only=args.identity_only,
                                 send_host_id=not args.no_host_id)
        except asyncio.TimeoutError:
            _out("Sign-in timed out after 10 minutes.")
            return 1
        except oauth.AuthError as e:
            _out(e.user_message)
            if isinstance(e, oauth.CodeExchangeRejected) and not args.identity_only:
                _out("If the browser said \"A required permission is unavailable\", your ChatGPT plan cannot share "
                     "usage with astro-app (Free plans cannot, for apps OpenAI has not verified). Go, Plus and Pro "
                     "can. Use `astro-app login --identity-only` to sign in without plan usage.")
            if not args.debug:
                _out("Run `astro-app login --debug` for details to report.")
            return 1
    _out(f"Signed in as {cred.label()}.")
    _out("ChatGPT plan usage: " + ("enabled." if cred.plan_enabled else
                                   "NOT granted. Run `astro-app login --enable-plan` to grant it."))
    return 0


def _accounts(args) -> int:
    store = CredentialStore()
    active = store.active()
    rows = store.accounts()
    if not rows:
        _out("No ChatGPT accounts yet. Run `astro-app login`.")
    for a in rows:
        mark = "*" if active and a.client_id == active.client_id else " "
        state = "signed in" if a.signed_in else ("registered, sign-in not finished" if a.registered_only
                                                 else "signed out")
        plan = "plan usage on" if a.plan_enabled else "plan usage off"
        _out(f"{mark} {a.label()}  [{state}, {plan}]")
    return 0


async def _with_client(fn) -> int:
    from .llm import LLMError, SIWCClient
    store = CredentialStore()
    async with httpx.AsyncClient() as http:
        client = SIWCClient(AuthSession(store, http), http)
        try:
            return await fn(client)
        except oauth.AuthError as e:
            _out(e.user_message)
        except LLMError as e:
            _out(f"{e.user_message}\n  ({e})")
    return 1


async def _models(args) -> int:
    async def run(client):
        for m in await client.list_models():
            _out(f"{m.slug:30s} {m.display_name}")
        return 0
    return await _with_client(run)


async def _ping(args) -> int:
    from .llm import LLMRequest

    async def run(client):
        client.model = args.model
        r = await client.complete(LLMRequest(instructions="Reply with exactly the requested text.",
                                             input="Say exactly: Hello from astro-app", purpose="ping"))
        _out(f"{r.model}: {r.text}")
        _out(f"request id {r.request_id}, usage {json.dumps(r.usage)}")
        return 0
    return await _with_client(run)


async def _logout(args) -> int:
    store = CredentialStore()
    async with httpx.AsyncClient() as http:
        if store.active() is None:
            _out("Not signed in.")
            return 0
        confirmed = await AuthSession(store, http).sign_out()
    _out("Signed out." if confirmed else
         "Signed out locally, but ChatGPT did not confirm revocation. You can disconnect astro-app in "
         "ChatGPT Settings.")
    return 0


def _chart(args) -> int:
    from .core.canonical import BirthInput, GateFailed, build_chart
    from .core.tables import deterministic_sections
    from .core.timeutil import LocalTimeError
    b = BirthInput(date=dt.date.fromisoformat(args.date),
                   time=dt.time.fromisoformat(args.time) if args.time else None,
                   lat=args.lat, lon=args.lon, tz_name=args.tz, place=args.place, name=args.name, fold=args.fold)
    try:
        chart, _ = build_chart(b)
    except LocalTimeError as e:
        _out(f"The local time is {e.kind} because of a clock change. Re-run with --fold 0 or --fold 1:")
        for o in e.options:
            _out(f"  --fold {o['fold']}: UTC{o['utc_offset_hours']:+g} ({o['abbrev']}), {o['utc']}")
        return 2
    except GateFailed as e:
        _out("The two calculation engines disagree; the chart was not produced:\n  " + "\n  ".join(e.report.issues))
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    secs = deterministic_sections(chart)
    (out / "chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "sections_1-5.md").write_text("\n".join(s.markdown for s in secs), encoding="utf-8")
    (out / "wheel.svg").write_text(secs[4].data["svg"], encoding="utf-8")
    _out(f"Wrote chart.json, sections_1-5.md and wheel.svg to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="astro-app", description="Local natal-chart readings on your ChatGPT plan.")
    p.add_argument("--version", action="version", version=f"astro-app {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login", help="Continue with ChatGPT")
    s.add_argument("--account", help="email or client id of a saved account (default: register a new one)")
    s.add_argument("--enable-plan", action="store_true", help="ask again for ChatGPT plan usage after a decline")
    s.add_argument("--new", action="store_true", help="register another ChatGPT account")
    s.add_argument("--debug", action="store_true", help="print redacted diagnostics if sign-in fails")
    s.add_argument("--identity-only", action="store_true",
                   help="diagnostic: sign in without the ChatGPT plan permission (readings stay disabled)")
    s.add_argument("--no-host-id", action="store_true",
                   help="diagnostic: omit ext_agent_host_id, like OpenAI's reference client by default")
    sub.add_parser("accounts", help="list saved ChatGPT accounts")
    sub.add_parser("models", help="list models on your ChatGPT plan")
    s = sub.add_parser("ping", help="one tiny inference on your ChatGPT plan")
    s.add_argument("--model", help="model slug (default: first listed)")
    sub.add_parser("logout", help="sign out of the active account")
    s = sub.add_parser("chart", help="build a chart offline (no sign-in needed)")
    s.add_argument("--date", required=True, help="YYYY-MM-DD")
    s.add_argument("--time", help="HH:MM local time (omit if unknown)")
    s.add_argument("--lat", type=float, required=True)
    s.add_argument("--lon", type=float, required=True)
    s.add_argument("--tz", required=True, help="IANA zone, e.g. Europe/London")
    s.add_argument("--place", required=True)
    s.add_argument("--name")
    s.add_argument("--fold", type=int, choices=(0, 1))
    s.add_argument("--out", default="astro-app-output")
    args = p.parse_args(argv)
    if args.cmd == "accounts":
        return _accounts(args)
    if args.cmd == "chart":
        return _chart(args)
    try:
        return asyncio.run({"login": _login, "models": _models, "ping": _ping, "logout": _logout}[args.cmd](args))
    except KeyboardInterrupt:
        _out("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
