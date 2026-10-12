"""Protected local credential records, one per issued client_id (= one ChatGPT account registration).

Files are written atomically with owner-only permissions. Signing out clears tokens but keeps
the account/client mapping so a later sign-in reuses the same registration.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import paths
from .config import ISSUER, PLAN_SCOPE


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@dataclass
class Credential:
    client_id: str
    subject: str
    ext_agent_host_id: str
    email: str | None = None
    name: str | None = None
    issuer: str = ISSUER
    id_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int = 0
    earliest_refresh_at: float | None = None
    scopes: list[str] = field(default_factory=list)
    saved_at: float = 0.0

    @property
    def signed_in(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    @property
    def plan_enabled(self) -> bool:
        return PLAN_SCOPE in self.scopes

    @property
    def expires_at(self) -> float:
        return self.saved_at + self.expires_in

    def with_tokens(self, tok: dict, now: float | None = None) -> "Credential":
        """Replace access token, expiry, scopes and the rotating refresh token together."""
        now = time.time() if now is None else now
        d = asdict(self)
        d.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token", self.refresh_token),
                 token_type=tok.get("token_type", "Bearer"), expires_in=int(tok.get("expires_in", 3600)),
                 earliest_refresh_at=_ts(tok.get("earliest_refresh_at")), saved_at=now)
        if tok.get("scope"):
            d["scopes"] = sorted(tok["scope"].split())
        if tok.get("id_token"):
            d["id_token"] = tok["id_token"]
        return Credential(**d)

    def signed_out(self) -> "Credential":
        d = asdict(self)
        d.update(access_token=None, refresh_token=None, id_token=None, expires_in=0, earliest_refresh_at=None)
        return Credential(**d)

    @property
    def registered_only(self) -> bool:
        """Issued client ID saved, but no successful sign-in yet."""
        return not self.subject

    def label(self) -> str:
        return f"{self.email or self.name or 'ChatGPT account'} ({self.client_id[-6:]})"


def _ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from datetime import datetime
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


class CredentialStore:
    def __init__(self, base: Path | None = None):
        self.dir = base or paths.credentials_dir()
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, client_id: str) -> Path:
        if not client_id or "/" in client_id or client_id.startswith("."):
            raise ValueError("invalid client_id")
        return self.dir / f"{client_id}.json"

    def save(self, cred: Credential) -> None:
        atomic_write(self._path(cred.client_id), json.dumps(asdict(cred), indent=2))

    def load(self, client_id: str) -> Credential | None:
        p = self._path(client_id)
        return Credential(**json.loads(p.read_text(encoding="utf-8"))) if p.is_file() else None

    def accounts(self) -> list[Credential]:
        return [Credential(**json.loads(p.read_text(encoding="utf-8"))) for p in sorted(self.dir.glob("*.json"))]

    def find_subject(self, subject: str) -> list[Credential]:
        return [c for c in self.accounts() if c.subject == subject]

    def set_active(self, client_id: str | None) -> None:
        p = self.dir / "active"
        if client_id is None:
            p.unlink(missing_ok=True)
        else:
            self._path(client_id)  # validate
            atomic_write(p, client_id)

    def active(self) -> Credential | None:
        p = self.dir / "active"
        return self.load(p.read_text(encoding="utf-8").strip()) if p.is_file() else None

    def lock_path(self, client_id: str) -> Path:
        return self.dir / f".{client_id}.lock"
