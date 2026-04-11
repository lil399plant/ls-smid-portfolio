"""
OpenBB client initializer.

Every script in this project imports `obb` from here — never directly from openbb.
This ensures credentials are always loaded from .env before any API call is made.

Usage:
    from data.openbb_client import obb

    df = obb.equity.price.historical("AAPL", provider="polygon").to_df()
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from openbb import obb

# Load .env from repo root (works regardless of where the script is run from)
_repo_root = Path(__file__).resolve().parent.parent
load_dotenv(_repo_root / ".env")

# ── Inject credentials from environment into OpenBB ───────────────────────────

_credentials = {
    "fmp_api_key":              os.getenv("OPENBB_FMP_API_KEY"),
    "polygon_api_key":          os.getenv("OPENBB_POLYGON_API_KEY"),
    "fred_api_key":             os.getenv("OPENBB_FRED_API_KEY"),
    "intrinio_api_key":         os.getenv("OPENBB_INTRINIO_API_KEY"),
    "alpha_vantage_api_key":    os.getenv("OPENBB_ALPHA_VANTAGE_API_KEY"),
}

for key, value in _credentials.items():
    if value:
        try:
            setattr(obb.user.credentials, key, value)
        except Exception:
            pass  # key not supported in this OpenBB version — safe to ignore

# ── Silence OpenBB's startup banner ──────────────────────────────────────────
obb.user.preferences.output_type = "dataframe"

# ── Validate at least one provider is configured ──────────────────────────────
_configured = [k for k, v in _credentials.items() if v]
if not _configured:
    raise EnvironmentError(
        "No OpenBB API keys found. "
        "Copy .env.example to .env and fill in your keys."
    )

print(f"[openbb_client] Loaded credentials for: {', '.join(k.replace('_api_key','') for k in _configured)}")

__all__ = ["obb"]
