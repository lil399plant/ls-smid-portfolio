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
# Key = OpenBB credential attribute name
# Value = environment variable name in .env

_credentials = {
    # Equity & fundamentals
    "fmp_api_key":              os.getenv("OPENBB_FMP_API_KEY"),
    "intrinio_api_key":         os.getenv("OPENBB_INTRINIO_API_KEY"),
    "benzinga_api_key":         os.getenv("OPENBB_BENZINGA_API_KEY"),

    # Prices & market data
    "polygon_api_key":          os.getenv("OPENBB_POLYGON_API_KEY"),
    "alpha_vantage_api_key":    os.getenv("OPENBB_ALPHA_VANTAGE_API_KEY"),

    # Crypto
    "coingecko_api_key":        os.getenv("OPENBB_COINGECKO_API_KEY"),
    "coindesk_api_key":         os.getenv("OPENBB_COINDESK_API_KEY"),
    "tao_api_key":              os.getenv("OPENBB_TAO_API_KEY"),

    # Macro & government
    "fred_api_key":             os.getenv("OPENBB_FRED_API_KEY"),
    "bls_api_key":              os.getenv("OPENBB_BLS_API_KEY"),
    "eia_api_key":              os.getenv("OPENBB_EIA_API_KEY"),
    "econdb_api_key":           os.getenv("OPENBB_ECONDB_API_KEY"),

    # Regulatory & government
    "cftc_api_key":             os.getenv("OPENBB_CFTC_API_KEY"),
    "congress_api_key":         os.getenv("OPENBB_CONGRESS_API_KEY"),
}

for key, value in _credentials.items():
    if value:
        try:
            setattr(obb.user.credentials, key, value)
        except Exception:
            pass  # key not supported in this OpenBB version — safe to ignore

# ── Silence OpenBB's startup banner ──────────────────────────────────────────
obb.user.preferences.output_type = "dataframe"

# ── Validate at least one provider is configured ─────────────────────────────
_configured = [k for k, v in _credentials.items() if v]
if not _configured:
    raise EnvironmentError(
        "No OpenBB API keys found. "
        "Copy .env.example to .env and fill in your keys."
    )

print(f"[openbb_client] Loaded {len(_configured)} providers: "
      f"{', '.join(k.replace('_api_key', '') for k in _configured)}")

__all__ = ["obb"]
