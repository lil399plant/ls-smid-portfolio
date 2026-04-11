"""
Refinitiv / LSEG Data client initializer.

Mirrors the pattern of openbb_client.py — every script that needs Refinitiv
imports `rd` and `REFINITIV_AVAILABLE` from here.

Connection logic (tries in order):
  1. Desktop session — Eikon / Workspace app open on this machine (no key needed)
  2. Platform session — cloud API via LSEG_APP_KEY in .env

Usage:
    from data.refinitiv_client import rd, REFINITIV_AVAILABLE

    if REFINITIV_AVAILABLE:
        df = rd.get_data("AAPL.O", ["TR.ROIC", "TR.EVtoEBITDA"])
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────────────────────
_repo_root = Path(__file__).resolve().parent.parent
load_dotenv(_repo_root / ".env")

# ── Suppress noisy LSEG startup logs ─────────────────────────────────────────
logging.getLogger("refinitiv").setLevel(logging.ERROR)
logging.getLogger("lseg").setLevel(logging.ERROR)

# ── Import library (supports both old and new package names) ──────────────────
rd = None
REFINITIV_AVAILABLE = False
_session_type = None

try:
    import lseg.data as _rd_lib
    rd = _rd_lib
except ImportError:
    try:
        import refinitiv.data as _rd_lib
        rd = _rd_lib
    except ImportError:
        rd = None

if rd is None:
    print("[refinitiv_client] ⚠️  Neither lseg.data nor refinitiv.data is installed.")
    print("                   Run: pip install lseg-data")
else:
    # ── Try desktop session first (Eikon / Workspace) ─────────────────────────
    try:
        rd.open_session()
        REFINITIV_AVAILABLE = True
        _session_type = "desktop"
        print("[refinitiv_client] ✅ Connected via Eikon/Workspace desktop session")

    except Exception as desktop_err:
        # ── Fall back to platform session (cloud API key) ─────────────────────
        app_key = os.getenv("LSEG_APP_KEY") or os.getenv("REFINITIV_APP_KEY")
        if app_key:
            try:
                rd.open_session(app_key=app_key)
                REFINITIV_AVAILABLE = True
                _session_type = "platform"
                print("[refinitiv_client] ✅ Connected via LSEG platform session (cloud)")
            except Exception as platform_err:
                print(f"[refinitiv_client] ❌ Desktop session failed: {desktop_err}")
                print(f"[refinitiv_client] ❌ Platform session failed: {platform_err}")
                print("[refinitiv_client]    → Open Eikon/Workspace, or add LSEG_APP_KEY to .env")
        else:
            print(f"[refinitiv_client] ⚠️  Desktop session unavailable: {desktop_err}")
            print("[refinitiv_client]    → No LSEG_APP_KEY in .env either.")
            print("[refinitiv_client]    → Open Eikon/Workspace to enable Refinitiv pulls.")


def close():
    """Call at end of script to cleanly close the Refinitiv session."""
    if REFINITIV_AVAILABLE and rd is not None:
        try:
            rd.close_session()
        except Exception:
            pass


def session_info() -> dict:
    return {
        "available": REFINITIV_AVAILABLE,
        "session_type": _session_type,
        "library": "lseg.data" if "lseg" in str(type(rd)) else "refinitiv.data",
    }


__all__ = ["rd", "REFINITIV_AVAILABLE", "close", "session_info"]
