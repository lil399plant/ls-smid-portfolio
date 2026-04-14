"""
Factor calculation module for L/S SMID-Cap portfolio.

Each factor function takes a DataFrame with raw fundamental/price data
and returns a Series of raw factor scores (higher = more attractive for longs).

Column mapping (collect.py → factors.py):
  p_b              → price_to_book      (OpenBB; Refinitiv TR.PriceToBookValue unavailable)
  roic             → return_on_equity   (best available proxy; ROIC not on this Refinitiv license)
  accruals_ratio   → profit_margin - operating_margin  (OCF unavailable; accrual quality proxy)
  eps_revision_3m  → eps_revision
  rev_cagr_3y      → rev_growth_fwd
  eps_cagr_3y      → eps_growth_fwd
  altman_z         → computed inline via altman_z()

Factors:
  - value_score()    : EV/EBITDA, P/FCF, P/B (inverted — lower is better)
  - quality_score()  : ROE proxy, Gross Margin, Accrual Quality proxy
  - momentum_score() : 12-1M price momentum, earnings revisions
  - growth_score()   : Forward revenue & EPS growth
  - health_score()   : Altman Z-Score, Net Debt/EBITDA, ICR
"""

import pandas as pd
import numpy as np


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    """Safely coerce a column to numeric, returning NaN Series if missing."""
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(df[col], errors="coerce")


# ── VALUE ─────────────────────────────────────────────────────────────────────

def _mean_components(*series) -> pd.Series:
    """Average components ignoring NaN, so one missing input doesn't null the score."""
    return pd.concat(list(series), axis=1).mean(axis=1, skipna=True)


def value_score(df: pd.DataFrame) -> pd.Series:
    """
    Lower multiples = higher score.
    Inputs: ev_ebitda, p_fcf, price_to_book
    """
    ev_ebitda     = -_num(df, 'ev_ebitda').rank(pct=True)
    p_fcf         = -_num(df, 'p_fcf').rank(pct=True)
    price_to_book = -_num(df, 'price_to_book').rank(pct=True)
    return _mean_components(ev_ebitda, p_fcf, price_to_book)


# ── QUALITY ───────────────────────────────────────────────────────────────────

def quality_score(df: pd.DataFrame) -> pd.Series:
    """
    Higher ROE, stable gross margins, low earnings accruals = higher score.

    Inputs:
      return_on_equity  — proxy for ROIC (ROIC unavailable on this Refinitiv license)
      gross_margin      — margin quality
      accrual_quality   — (profit_margin - operating_margin): tighter gap = cleaner earnings.
                          Negative = operating leverage; very positive = accrual risk.
                          Lower gap = better, so we invert.
    """
    roe          = _num(df, 'return_on_equity').rank(pct=True)
    gross_margin = _num(df, 'gross_margin').rank(pct=True)

    # Accrual quality proxy: profit_margin - operating_margin
    # (smaller gap = earnings are closer to cash; invert so lower = better score)
    accrual_proxy = _num(df, 'profit_margin') - _num(df, 'operating_margin')
    accrual_score = -accrual_proxy.rank(pct=True)

    return _mean_components(roe, gross_margin, accrual_score)


# ── MOMENTUM ──────────────────────────────────────────────────────────────────

def momentum_score(df: pd.DataFrame) -> pd.Series:
    """
    12-month minus 1-month price return + earnings revision momentum.
    Inputs: ret_12m, ret_1m, eps_revision
    """
    price_mom = (_num(df, 'ret_12m') - _num(df, 'ret_1m')).rank(pct=True)
    eps_rev   = _num(df, 'eps_revision').rank(pct=True)
    return 0.6 * price_mom + 0.4 * eps_rev


# ── GROWTH ────────────────────────────────────────────────────────────────────

def growth_score(df: pd.DataFrame) -> pd.Series:
    """
    Forward revenue & EPS growth (consensus estimate vs actual).
    Inputs: rev_growth_fwd, eps_growth_fwd
    """
    rev_growth = _num(df, 'rev_growth_fwd').rank(pct=True)
    eps_growth = _num(df, 'eps_growth_fwd').rank(pct=True)
    return 0.5 * rev_growth + 0.5 * eps_growth


# ── ALTMAN Z-SCORE ────────────────────────────────────────────────────────────

def altman_z(df: pd.DataFrame) -> pd.Series:
    """
    Altman Z-Score for public companies.
    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

    X1 = Working Capital / Total Assets
    X2 = Retained Earnings / Total Assets
    X3 = EBIT / Total Assets
    X4 = Market Cap / Total Liabilities
    X5 = Revenue / Total Assets

    Zones:  Z > 2.99 = Safe  |  1.81–2.99 = Grey  |  Z < 1.81 = Distress

    Uses market_cap (raw dollars, from OpenBB) and total_liabilities (raw).
    """
    ta = _num(df, 'total_assets').replace(0, np.nan)
    tl = _num(df, 'total_liabilities').replace(0, np.nan)

    X1 = _num(df, 'working_capital')  / ta
    X2 = _num(df, 'retained_earnings') / ta
    X3 = _num(df, 'ebit')             / ta
    X4 = _num(df, 'market_cap')       / tl   # market_cap in raw dollars from OpenBB
    X5 = _num(df, 'revenue')          / ta

    return 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5


# ── FINANCIAL HEALTH ──────────────────────────────────────────────────────────

def health_score(df: pd.DataFrame) -> pd.Series:
    """
    Altman Z-Score (higher = safer), low leverage, high interest coverage.
    Altman Z is computed here so it always uses available data even if
    the pre-computed column in the parquet is null.
    Inputs: working_capital, total_assets, retained_earnings, ebit,
            market_cap, total_liabilities, revenue, net_debt_ebitda,
            interest_coverage
    """
    z   = altman_z(df).rank(pct=True)
    lev = -_num(df, 'net_debt_ebitda').rank(pct=True)
    icr = _num(df, 'interest_coverage').rank(pct=True)
    return _mean_components(z, lev, icr)


# ── COMPOSITE ─────────────────────────────────────────────────────────────────

WEIGHTS = {
    'value':    0.25,
    'quality':  0.25,
    'momentum': 0.20,
    'growth':   0.15,
    'health':   0.15,
}

def composite_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all factor scores, z-score within GICS sector, combine into composite.
    Returns df with individual factor scores + final composite.

    Requires columns: see individual factor functions above.
    Sector column: 'sector' (OpenBB string) or 'gics_sector_code' (Refinitiv int).
    Falls back to treating all stocks as one sector if neither is present.
    """
    df = df.copy()

    df['f_value']    = value_score(df)
    df['f_quality']  = quality_score(df)
    df['f_momentum'] = momentum_score(df)
    df['f_growth']   = growth_score(df)
    df['f_health']   = health_score(df)

    factor_cols = ['f_value', 'f_quality', 'f_momentum', 'f_growth', 'f_health']

    # Prefer 'sector' (OpenBB readable name); fall back to gics_sector_code or 'ALL'
    if 'sector' in df.columns and df['sector'].notna().any():
        sector_col = 'sector'
    elif 'gics_sector_code' in df.columns and df['gics_sector_code'].notna().any():
        sector_col = 'gics_sector_code'
    else:
        df['_sector_fallback'] = 'ALL'
        sector_col = '_sector_fallback'

    # Z-score within sector (skip if fewer than 2 stocks in a sector)
    for col in factor_cols:
        df[col + '_z'] = df.groupby(sector_col)[col].transform(
            lambda x: (x - x.mean()) / x.std() if len(x) > 1 else pd.Series(0.0, index=x.index)
        )

    df['composite'] = sum(
        WEIGHTS[k] * df[f'f_{k}_z'] for k in WEIGHTS
    )

    # Clean up fallback column if added
    if '_sector_fallback' in df.columns:
        df = df.drop(columns=['_sector_fallback'])

    return df
