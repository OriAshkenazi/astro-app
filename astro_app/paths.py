"""Per-user locations. Everything the app writes lives under one directory.

ASTRO_HOME     base directory (default ~/.astro-app)
ASTRO_EPHE_DIR ephemeris files (default $ASTRO_HOME/ephe); de421.bsp is downloaded here on first use
"""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    p = Path(os.environ.get("ASTRO_HOME", Path.home() / ".astro-app"))
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


def ephe_dir() -> Path:
    p = Path(os.environ.get("ASTRO_EPHE_DIR", home() / "ephe"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def credentials_dir() -> Path:
    p = home() / "credentials"
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p
