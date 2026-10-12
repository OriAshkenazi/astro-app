"""Standalone loopback callback listener for `astro-app login` (the web server mounts its own route).

Binds 127.0.0.1 only, accepts exactly one GET on the callback path, answers with a short page
and returns the query parameters. The listener is started before the browser is opened.
"""
from __future__ import annotations

import asyncio
import hashlib
import socket
import time
from urllib.parse import parse_qsl, urlsplit

from . import config as C

PAGE = ("<!doctype html><meta charset=utf-8><title>astro-app</title>"
        "<body style='font-family:sans-serif;margin:3rem'><h1>{title}</h1><p>{msg}</p></body>")


def free_port(preferred: int = C.DEFAULT_PORT) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((C.CALLBACK_HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise OSError("no free loopback port")


def _describe(parts: list[str], target) -> str:
    """One redacted line per request: time, method, path, parameter names, code length + fingerprint."""
    stamp = f"{time.monotonic():.3f}"
    if target is None:
        return f"{stamp} {parts[0] if parts else '?'} (ignored)"
    q = dict(parse_qsl(target.query))
    fp = ""
    if "code" in q:
        fp = f" code[len={len(q['code'])} sha256:{hashlib.sha256(q['code'].encode()).hexdigest()[:8]}]"
    return f"{stamp} {parts[0]} {target.path} params={sorted(q)}{fp}"


class CallbackListener:
    def __init__(self, port: int):
        self.port = port
        self._result: asyncio.Future[dict] | None = None
        self._server: asyncio.base_events.Server | None = None
        self.hits: list[str] = []  # redacted log of every request, for --debug

    @property
    def redirect_uri(self) -> str:
        return C.redirect_uri(self.port)

    async def __aenter__(self):
        self._result = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, C.CALLBACK_HOST, self.port)
        self.port = self._server.sockets[0].getsockname()[1]  # the bound port when 0 was requested
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def wait(self, timeout: float = C.PENDING_TTL_S) -> dict:
        return await asyncio.wait_for(self._result, timeout)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
            parts = line.split()
            target = urlsplit(parts[1]) if len(parts) >= 2 and parts[0] == "GET" else None
            self.hits.append(_describe(parts, target))
            if target is None or target.path != C.CALLBACK_PATH or self._result.done():
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            params = dict(parse_qsl(target.query))
            ok = "code" in params
            body = PAGE.format(title="Signed in" if ok else "Sign-in not completed",
                               msg="You can close this tab and return to astro-app." if ok
                               else "Return to astro-app to try again.").encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                         b"Cache-Control: no-store\r\nConnection: close\r\n"
                         + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self._result.set_result(params)
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            try:
                await writer.drain()
            except ConnectionError:
                pass
            writer.close()
