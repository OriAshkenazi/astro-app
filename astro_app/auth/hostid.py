"""Stable, opaque ext_agent_host_id for this installation.

Chosen and persisted before the first sign-in and reused for every later one. It is an
identifier, not a credential, and carries nothing user-identifying.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from .. import paths
from .store import atomic_write

HOST_ID_RE = re.compile(r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def host_id(base: Path | None = None) -> str:
    p = (base or paths.home()) / "host_id"
    if p.is_file():
        value = p.read_text(encoding="utf-8").strip()
        if HOST_ID_RE.match(value):
            return value
    value = f"urn:uuid:{uuid.uuid4()}"
    atomic_write(p, value + "\n")
    return value


def exists(base: Path | None = None) -> bool:
    return os.path.isfile((base or paths.home()) / "host_id")
