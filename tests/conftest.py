import json
import os
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

# Optional private reference project (hand-gated chart used as the golden fixture).
# It is never committed; golden tests skip when it is absent.
REF = Path(os.environ.get("ASTRO_REF_DIR", APP.parent))
GOLDEN = REF / "03_canonical" / "chart_data.json"
if (REF / "02_audit" / "de421.bsp").is_file():
    os.environ.setdefault("ASTRO_EPHE_DIR", str(REF / "02_audit"))  # reuse kernel, avoid download


@pytest.fixture(scope="session")
def golden():
    if not GOLDEN.is_file():
        pytest.skip("reference dataset not available (set ASTRO_REF_DIR)")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))
