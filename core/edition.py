"""Which build this is. The Friends Edition is the same app with its own name, data folder and port, so it never
shares keys, KOLs or history with the main copy (even on the same computer). Set at build time in version.json
("edition"), or with TRENCH_EDITION when running from source."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EDITIONS = {
    "main": {"edition": "main", "name": "Trench Scope", "folder": "TrenchScope", "port": 8000, "file": "TrenchScope"},
    "friends": {"edition": "friends", "name": "Trench Scope Friends", "folder": "TrenchScopeFriends", "port": 8100,
                "file": "TrenchScope-Friends"},
}


def current() -> dict:
    ed = os.getenv("TRENCH_EDITION")
    if not ed:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        try:
            ed = json.loads((base / "version.json").read_text()).get("edition")
        except Exception:
            ed = None
    return EDITIONS.get(ed or "main", EDITIONS["main"])
