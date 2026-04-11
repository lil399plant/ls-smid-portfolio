"""
Factor calculation module for L/S SMID-Cap portfolio.

Each factor function takes a DataFrame with raw fundamental/price data
and returns a Series of raw factor scores (higher = more attractive for longs).

Factors:
  - value_score()         : EV/EBITDA, P/FCF, P/B (inverted — lower is better)
  - quality_score()       : ROIC, Gross Margin Stability, Accruals Ratio
  - momentum_score()      : 12-1M price momentum, earnings revisions
  - growth_score()        : Revenue & EPS 3Y CAGR
  - health_score()        : Altman Z-Score, Net Debt/EBITDA, ICR
"""

import pandas as pd
import numpy as np


# ── VALUE ─────────────────────────────────────────────────────────────────────

def value_score(df: pd.DataFrame) -> pd.Series:
    """
    Lower multiples = higher score.
    Inputs (columns): ev_ebitda, p_fcf, p_b
    """
    ev_ebitda = -df['ev_ebitda'].rank(pct=True)   # invert: lower multiple = better
    p_fcf     = -df['p_fcf'].rank(pct=True)
    p_b       = -df['p_b'].rank(pct=True)
    return (ev_ebitda + p_fcf + p_b) / 3


# ── QUALITY ───────────────────────────────────────────────────────────────────

def quality_score(df: pd.DataFrame) -> pd.Series:
    """
    Higher ROIC, stable margins, low accruals = higher score.
    Inputs: roic, gross_margin, accruals_ratio
    Accruals ratio = (Net Income - OCF) / Avg Total Assets (lower = better)
    """
    roic           = df['roic'].rank(pct=True)
    gross_margin   = df['gross_margin'].rank(pct=True)
    accruals       = -df['accruals_ratio'].rank(pct=True)   # lower accruals = better
    return (roic + gross_margin + accruals) / 3


# ── MOMENTUM ──────────────────────────────────────────────────────────────────

def momentum_score(df: pd.DataFrame) -> pd.Series:
    """
    12-month minus 1-month price return + earnings revision momentum.
    Inputs: ret_12m, ret_1m, eps_revision_3m
    """
    price_mom   = (df['ret_12m'] - df['ret_1m']).rank(pct=True)
    eps_rev     = df['eps_revision_3m'].rank(pct=True)
    return 0.6 * price_mom + 0.4 * eps_rev


# ── GROWTH ────────────────────────────────────────────────────────────────────

def growth_score(df: pd.DataFrame) -> pd.Series:
    """
    Revenue + EPS 3-year CAGR.
    Inputs: rev_cagr_3y, eps_cagr_3y
    """
    rev_growth = df['rev_cagr_3y'].rank(pct=True)
    eps_growth = df['eps_cagr_3y'].rank(pct=True)
    return 0.5 * rev_growth + 0.5 * eps_growth


# ── FINANCIAL HEALTH ──────────────────────────────────────────────────────────

def health_score(df: pd.DataFrame) -> pd.Series:
    """
    Altman Z-Score (higher = safer), low leverage, high interest coverage.
    Inputs: altman_z, net_debt_ebitda, interest_coverage
    """
    z_score   = df['altman_z'].rank(pct=True)
    leverage  = -df['net_debt_ebitda'].rank(pct=True)   # lower leverage = better
    icr       = df['interest_coverage'].rank(pct=True)
    return (z_score + leverage + icr) / 3


def altman_z(df: pd.DataFrame) -> pd.Series:
    """
    Altman Z-Score for public manufacturers.
    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
    X1 = Working Capital / Total Assets
    X2 = Retained Earnings / Total Assets
    X3 = EBIT / Total Assets
    X4 = Market Cap / Total Liabilities
    X5 = Revenue / Total Assets

    Z > 2.99 = Safe zone
    1.81 < Z < 2.99 = Grey zone
    Z < 1.81 = Distress zone
    """
    X1 = df['working_capital'] / df['total_assets']
    X2 = df['retained_earnings'] / df['total_assets']
    X3 = df['ebit'] / df['total_assets']
    X4 = df['market_cap'] / df['total_liabilities']
    X5 = df['revenue'] / df['total_assets']
    return 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5


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
    Compute all factor scores, z-score within sector, combine into composite.
    Returns df with individual factor scores + final composite.
    """
    df = df.copy()
    df['f_value']    = value_score(df)
    df['f_quality']  = quality_score(df)
    df['f_momentum'] = momentum_score(df)
    df['f_growth']   = growth_score(df)
    df['f_health']   = health_score(df)

    factor_cols = ['f_value', 'f_quality', 'f_momentum', 'f_growth', 'f_health']

    # Z-score within GICS sector
    for col in factor_cols:
        df[col + '_z'] = df.groupby('gics_sector')[col].transform(
            lambda x: (x - x.mean()) / x.std()
        )

    z_cols = [c + '_z' for c in factor_cols]
    factor_keys = ['value', 'quality', 'momentum', 'growth', 'health']
    df['composite'] = sum(
        WEIGHTS[k] * df[f'f_{k}_z'] for k in factor_keys
    )

    return df
