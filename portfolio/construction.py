"""
Portfolio construction: select longs/shorts from composite factor scores,
enforce beta-neutrality and concentration limits.
"""

import pandas as pd
import numpy as np


def select_portfolio(
    df: pd.DataFrame,
    n_longs: int = 40,
    n_shorts: int = 40,
    max_sector_pct: float = 0.25,
) -> pd.DataFrame:
    """
    Select top n_longs (highest composite) and bottom n_shorts (lowest composite).
    df must have: ticker, composite, gics_sector, beta
    Returns DataFrame with 'side' column ('long' or 'short').
    """
    df = df.sort_values('composite', ascending=False).copy()

    longs  = df.head(n_longs).copy()
    longs['side'] = 'long'

    shorts = df.tail(n_shorts).copy()
    shorts['side'] = 'short'

    portfolio = pd.concat([longs, shorts], ignore_index=True)
    return portfolio


def compute_beta_neutral_weights(
    portfolio: pd.DataFrame,
    gross_exposure: float = 2.0,
) -> pd.DataFrame:
    """
    Equal-weight within each side, then scale short leg so portfolio beta ≈ 0.
    Returns portfolio with 'weight' column (positive = long, negative = short).
    """
    portfolio = portfolio.copy()

    longs  = portfolio[portfolio['side'] == 'long']
    shorts = portfolio[portfolio['side'] == 'short']

    n_long  = len(longs)
    n_short = len(shorts)

    # Start with equal weight
    w_long  = (gross_exposure / 2) / n_long
    w_short = (gross_exposure / 2) / n_short

    portfolio.loc[portfolio['side'] == 'long',  'weight'] =  w_long
    portfolio.loc[portfolio['side'] == 'short', 'weight'] = -w_short

    # Beta-neutrality adjustment
    beta_long  = (longs['beta'] * w_long).sum()
    beta_short = (shorts['beta'] * w_short).sum()
    portfolio_beta = beta_long - beta_short

    # Scale short leg to zero net beta
    if beta_short != 0 and portfolio_beta != 0:
        scale = beta_long / beta_short
        portfolio.loc[portfolio['side'] == 'short', 'weight'] *= scale

    return portfolio


def check_concentration(portfolio: pd.DataFrame, max_sector_pct: float = 0.25) -> dict:
    """
    Returns dict of risk check results.
    """
    issues = {}

    # Sector concentration (gross)
    sector_gross = portfolio.groupby('gics_sector')['weight'].apply(
        lambda w: w.abs().sum()
    )
    total_gross = portfolio['weight'].abs().sum()
    sector_pct = sector_gross / total_gross

    over_limit = sector_pct[sector_pct > max_sector_pct]
    if not over_limit.empty:
        issues['sector_concentration'] = over_limit.to_dict()

    # Single name limit (>5% gross)
    single_name = portfolio[portfolio['weight'].abs() > 0.05]
    if not single_name.empty:
        issues['single_name_over_5pct'] = single_name['ticker'].tolist()

    # Net exposure
    net_exp = portfolio['weight'].sum()
    if abs(net_exp) > 0.05:
        issues['net_exposure_breach'] = net_exp

    return issues
