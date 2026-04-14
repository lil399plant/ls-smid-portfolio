"""
Universe construction for L/S SMID-Cap portfolio.

Filters applied (in order):
  1. Market cap $500M – $10B
  2. Average daily dollar volume >= $5M  [skipped if column unavailable]
  3. Price >= $5 (avoid penny stocks)
  4. Exclude: financials (GICS 40), utilities (GICS 55), REITs (GICS 60)
  5. Exclude: recent IPOs (< 2 years listed)  [skipped if column unavailable]
  6. Exclude: stocks in M&A announced deals   [skipped if column unavailable]

Column mapping (collect.py → universe.py):
  market_cap_m  → derived from market_cap / 1e6  (OpenBB raw dollars)
  adv_90d_m     → adv_10d if present, else filter skipped
  listing_date  → skipped if not present
  in_ma_deal    → skipped if not present
  gics_sector_code → Refinitiv 2-digit sector code (int or str)
"""

import pandas as pd
import numpy as np


# GICS sector-level codes to exclude (2-digit).
# Financials=40, Utilities=55, Real Estate=60 (covers all REITs).
EXCLUDED_GICS = {40, 55, 60}


def _prep_market_cap_m(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure market_cap_m column exists in $M.
    Prefers Refinitiv market_cap_m if present and non-null;
    falls back to OpenBB market_cap (raw dollars) / 1e6.
    """
    df = df.copy()
    if 'market_cap_m' in df.columns and df['market_cap_m'].notna().any():
        df['market_cap_m'] = pd.to_numeric(df['market_cap_m'], errors='coerce')
    elif 'market_cap' in df.columns:
        df['market_cap_m'] = pd.to_numeric(df['market_cap'], errors='coerce') / 1e6
    else:
        df['market_cap_m'] = np.nan
    return df


def _parse_gics_sector(val) -> int | None:
    """Extract 2-digit GICS sector code from any GICS code format."""
    try:
        return int(str(int(float(str(val))))[:2])
    except (ValueError, TypeError):
        return None


def apply_universe_filters(
    df: pd.DataFrame,
    min_mktcap_m: float = 500,
    max_mktcap_m: float = 10_000,
    min_adv_m: float = 5,
    min_price: float = 5.0,
    min_listing_years: float = 2.0,
) -> pd.DataFrame:
    """
    Apply universe filters. Filters that depend on unavailable columns are
    skipped with a warning rather than raising, so the pipeline degrades
    gracefully when data is partial.
    """
    df = _prep_market_cap_m(df)
    initial = len(df)

    # 1. Market cap band
    if df['market_cap_m'].notna().any():
        df = df[df['market_cap_m'].between(min_mktcap_m, max_mktcap_m)]
        print(f"  [universe] Mkt cap ${min_mktcap_m}M–${max_mktcap_m}M: {initial} → {len(df)}")
    else:
        print("  [universe] SKIP mkt cap filter — market_cap unavailable")

    # 2. Liquidity (use best available ADV column)
    adv_col = next(
        (c for c in ['adv_90d_m', 'adv_10d'] if c in df.columns and df[c].notna().any()),
        None
    )
    if adv_col:
        before = len(df)
        df = df[pd.to_numeric(df[adv_col], errors='coerce') >= min_adv_m]
        print(f"  [universe] ADV >= ${min_adv_m}M ({adv_col}): {before} → {len(df)}")
    else:
        print("  [universe] SKIP liquidity filter — no ADV column available")

    # 3. Price floor
    if 'price' in df.columns:
        before = len(df)
        df = df[pd.to_numeric(df['price'], errors='coerce') >= min_price]
        print(f"  [universe] Price >= ${min_price}: {before} → {len(df)}")

    # 4. Sector exclusions
    if 'gics_sector_code' in df.columns and df['gics_sector_code'].notna().any():
        before = len(df)
        sector_2d = df['gics_sector_code'].apply(_parse_gics_sector)
        df = df[~sector_2d.isin(EXCLUDED_GICS)]
        print(f"  [universe] Exclude Fins/Utils/REITs: {before} → {len(df)}")
    else:
        print("  [universe] SKIP sector filter — gics_sector_code unavailable")

    # 5. Listing age
    if 'listing_date' in df.columns and df['listing_date'].notna().any():
        before = len(df)
        cutoff = pd.Timestamp.today() - pd.DateOffset(years=min_listing_years)
        df = df[pd.to_datetime(df['listing_date'], errors='coerce') <= cutoff]
        print(f"  [universe] IPO age >= {min_listing_years}y: {before} → {len(df)}")
    else:
        print("  [universe] SKIP IPO age filter — listing_date unavailable")

    # 6. M&A exclusion
    if 'in_ma_deal' in df.columns:
        before = len(df)
        df = df[~df['in_ma_deal'].fillna(False).astype(bool)]
        print(f"  [universe] M&A exclusion: {before} → {len(df)}")
    else:
        print("  [universe] SKIP M&A filter — in_ma_deal unavailable")

    print(f"  [universe] Final universe: {len(df)} stocks")
    return df.reset_index(drop=True)
