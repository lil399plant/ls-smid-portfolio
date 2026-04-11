"""
Universe construction for L/S SMID-Cap portfolio.

Filters applied (in order):
  1. Market cap $500M – $10B
  2. Average daily dollar volume >= $5M (90-day)
  3. Price >= $5 (avoid penny stocks)
  4. Exclude: financials (GICS 40), utilities (GICS 55), REITs (GICS 6010)
  5. Exclude: recent IPOs (< 2 years listed)
  6. Exclude: stocks in M&A announced deals
"""

import pandas as pd


EXCLUDED_GICS = {
    40,       # Financials
    55,       # Utilities
    601010,   # Equity REITs
}


def apply_universe_filters(
    df: pd.DataFrame,
    min_mktcap_m: float = 500,
    max_mktcap_m: float = 10_000,
    min_adv_m: float = 5,
    min_price: float = 5.0,
    min_listing_years: float = 2.0,
) -> pd.DataFrame:
    """
    df must have columns:
      market_cap_m, adv_90d_m, price, gics_code, listing_date, in_ma_deal
    Returns filtered DataFrame.
    """
    df = df.copy()

    # Market cap band
    df = df[(df['market_cap_m'] >= min_mktcap_m) & (df['market_cap_m'] <= max_mktcap_m)]

    # Liquidity
    df = df[df['adv_90d_m'] >= min_adv_m]

    # Price
    df = df[df['price'] >= min_price]

    # Exclude sectors
    df = df[~df['gics_sector_code'].isin(EXCLUDED_GICS)]

    # Listing age
    df['listing_date'] = pd.to_datetime(df['listing_date'])
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=min_listing_years)
    df = df[df['listing_date'] <= cutoff]

    # M&A filter
    if 'in_ma_deal' in df.columns:
        df = df[~df['in_ma_deal']]

    return df.reset_index(drop=True)
