"""
Refinitiv data pulls for the L/S SMID factor model.

Each function returns a clean DataFrame with a 'ticker' column.
All field names use the TR. namespace (Refinitiv DataStream / Eikon API).

Factor coverage:
  - Value:           EV/EBITDA, P/FCF, P/B
  - Quality:         ROIC, Gross Margin, Accruals Ratio components
  - Momentum:        Earnings revision (1W, 4W), price momentum
  - Growth:          Revenue CAGR 3Y, EPS CAGR 3Y
  - Health:          Altman Z components, Net Debt/EBITDA, Interest Coverage
  - Short Interest:  Short ratio, days-to-cover (only available via Refinitiv)
  - Ownership:       Institutional %, insider %
  - Price History:   Adjusted OHLCV with point-in-time accuracy
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from data.refinitiv_client import rd, REFINITIV_AVAILABLE


def _require_refinitiv():
    if not REFINITIV_AVAILABLE:
        raise RuntimeError(
            "Refinitiv is not connected. Open Eikon/Workspace and restart, "
            "or add LSEG_APP_KEY to your .env file."
        )


# ── VALUE FACTORS ─────────────────────────────────────────────────────────────

def pull_value(tickers: list[str]) -> pd.DataFrame:
    """
    EV/EBITDA, Price/FCF, Price/Book.
    Lower = cheaper = higher value score.
    """
    _require_refinitiv()
    fields = {
        "TR.EVtoEBITDA":        "ev_ebitda",
        "TR.PriceToCFPerShare":  "p_fcf",
        "TR.PriceToBookValue":   "p_b",
        "TR.PEMean":             "pe_ratio",
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)
    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── QUALITY FACTORS ───────────────────────────────────────────────────────────

def pull_quality(tickers: list[str]) -> pd.DataFrame:
    """
    ROIC, Gross Margin, and raw components for Accruals Ratio.
    Accruals = (Net Income - OCF) / Avg Total Assets (lower = higher quality)
    """
    _require_refinitiv()
    fields = {
        "TR.ROIC":                   "roic",
        "TR.GrossProfitMarginPercent": "gross_margin",
        "TR.NetIncome":              "net_income",
        "TR.CashFromOperations":     "operating_cf",
        "TR.TotalAssets":            "total_assets",
        "TR.ReturnOnAssets":         "roa",
        "TR.ReturnOnEquity":         "roe",
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)

    # Compute accruals ratio directly
    df["accruals_ratio"] = (
        (df["net_income"] - df["operating_cf"]) / df["total_assets"]
    )

    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── MOMENTUM FACTORS ──────────────────────────────────────────────────────────

def pull_momentum(tickers: list[str]) -> pd.DataFrame:
    """
    Price momentum (12-1M) and earnings revision momentum.
    Earnings revisions are a key edge only available via Refinitiv.
    """
    _require_refinitiv()
    fields = {
        "TR.PriceReturn1Yr":          "ret_12m",
        "TR.PriceReturn1M":           "ret_1m",
        "TR.EPSSmartEstRevision1W":   "eps_rev_1w",
        "TR.EPSSmartEstRevision4W":   "eps_rev_4w",
        "TR.RevSmartEstRevision4W":   "rev_rev_4w",
        "TR.EPSMean":                 "eps_consensus",
        "TR.RecommendationMean":      "analyst_rec",   # 1=Strong Buy, 5=Strong Sell
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)

    # 12-1M momentum (skip most recent month to avoid reversal)
    df["price_momentum"] = df["ret_12m"] - df["ret_1m"]

    # Composite earnings revision signal
    df["eps_revision"] = (
        0.5 * df["eps_rev_1w"].fillna(0) +
        0.5 * df["eps_rev_4w"].fillna(0)
    )

    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── GROWTH FACTORS ────────────────────────────────────────────────────────────

def pull_growth(tickers: list[str]) -> pd.DataFrame:
    """
    Revenue and EPS 3-year CAGRs, plus forward growth estimates.
    """
    _require_refinitiv()
    fields = {
        "TR.RevenueCAGR3Yr":          "rev_cagr_3y",
        "TR.EPSGrowthMean3Yr":        "eps_cagr_3y",
        "TR.RevenueGrowthMean":       "rev_growth_fwd",
        "TR.EPSGrowthMeanF1":         "eps_growth_fwd",
        "TR.SGAExpenses":             "sga",          # for quality-growth combo
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)
    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── FINANCIAL HEALTH FACTORS ──────────────────────────────────────────────────

def pull_health(tickers: list[str]) -> pd.DataFrame:
    """
    Altman Z-Score components, leverage, and interest coverage.
    Also computes Altman Z directly.
    """
    _require_refinitiv()
    fields = {
        "TR.WorkingCapital":          "working_capital",
        "TR.RetainedEarnings":        "retained_earnings",
        "TR.EBIT":                    "ebit",
        "TR.MarketCap":               "market_cap",
        "TR.TotalLiabilities":        "total_liabilities",
        "TR.Revenue":                 "revenue",
        "TR.TotalAssets":             "total_assets",
        "TR.NetDebtToEBITDA":         "net_debt_ebitda",
        "TR.InterestCoverage":        "interest_coverage",
        "TR.TotalDebt":               "total_debt",
        "TR.FreeCashFlow":            "fcf",
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)

    # Altman Z-Score (public manufacturer formula)
    # Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
    ta = df["total_assets"].replace(0, np.nan)
    df["altman_z"] = (
        1.2 * (df["working_capital"] / ta) +
        1.4 * (df["retained_earnings"] / ta) +
        3.3 * (df["ebit"] / ta) +
        0.6 * (df["market_cap"] / df["total_liabilities"].replace(0, np.nan)) +
        1.0 * (df["revenue"] / ta)
    )

    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── SHORT INTEREST (Refinitiv exclusive) ──────────────────────────────────────

def pull_short_interest(tickers: list[str]) -> pd.DataFrame:
    """
    Short interest data — one of the key advantages of Refinitiv over free APIs.
    High short interest + improving fundamentals = powerful long signal (short squeeze).
    High short interest + deteriorating = confirms short thesis.
    """
    _require_refinitiv()
    fields = {
        "TR.ShortInterest":           "short_interest_shares",
        "TR.ShortInterestRatio":      "short_ratio",        # days to cover
        "TR.ShortPercentOfFloat":     "short_pct_float",
        "TR.SharesFloat":             "shares_float",
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)
    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── OWNERSHIP DATA ────────────────────────────────────────────────────────────

def pull_ownership(tickers: list[str]) -> pd.DataFrame:
    """
    Institutional and insider ownership — useful for conviction signals.
    """
    _require_refinitiv()
    fields = {
        "TR.InstitutionalOwnership":  "institutional_pct",
        "TR.InsiderOwnership":        "insider_pct",
        "TR.SharesOutstanding":       "shares_outstanding",
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)
    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── PRICE HISTORY (point-in-time) ─────────────────────────────────────────────

def pull_price_history(
    tickers: list[str],
    days_back: int = 756,   # ~3 years
    interval: str = "daily"
) -> pd.DataFrame:
    """
    Adjusted OHLCV history. Refinitiv provides point-in-time adjusted data,
    which avoids look-ahead bias vs. some free sources.
    """
    _require_refinitiv()
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")

    frames = []
    for ticker in tickers:
        ric = _ticker_to_ric(ticker)
        try:
            df = rd.get_history(
                universe=ric,
                fields=["TR.OpenPrice", "TR.HighPrice", "TR.LowPrice",
                        "TR.ClosePrice", "TR.Volume"],
                start=start,
                end=end,
                interval=interval,
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)
                df.columns = [c.lower().replace(" ", "_") for c in df.columns]
                df["ticker"] = ticker
                frames.append(df)
        except Exception as e:
            print(f"  [WARN] Price history failed for {ticker}: {e}")

    return pd.concat(frames) if frames else pd.DataFrame()


# ── UNIVERSE META (sector, market cap, beta) ──────────────────────────────────

def pull_universe_meta(tickers: list[str]) -> pd.DataFrame:
    """
    Sector, market cap, beta — needed for universe filtering and beta-neutrality.
    """
    _require_refinitiv()
    fields = {
        "TR.GICSSectorCode":          "gics_sector_code",
        "TR.GICSSectorName":          "gics_sector",
        "TR.GICSIndustryGroupCode":   "gics_industry_group_code",
        "TR.MarketCap":               "market_cap_m",
        "TR.Beta":                    "beta",
        "TR.IssueDate":               "listing_date",
        "TR.PriceClose":              "price",
        "TR.Volume10DayAvg":          "adv_10d",
    }
    df = rd.get_data(
        universe=_to_rics(tickers),
        fields=list(fields.keys())
    )
    df = df.rename(columns=fields)
    df["market_cap_m"] = df["market_cap_m"] / 1e6   # convert to $M
    df["ticker"] = _rics_to_tickers(df.index.tolist(), tickers)
    return df.reset_index(drop=True)


# ── FULL FACTOR SNAPSHOT ──────────────────────────────────────────────────────

def pull_all_factors(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """
    Run all factor pulls in sequence and return a dict of DataFrames.
    Use this as the single entry point for a full monthly data refresh.

    Returns:
        {
            "value":         DataFrame,
            "quality":       DataFrame,
            "momentum":      DataFrame,
            "growth":        DataFrame,
            "health":        DataFrame,
            "short_interest": DataFrame,
            "ownership":     DataFrame,
            "universe_meta": DataFrame,
        }
    """
    _require_refinitiv()

    pulls = {
        "value":          pull_value,
        "quality":        pull_quality,
        "momentum":       pull_momentum,
        "growth":         pull_growth,
        "health":         pull_health,
        "short_interest": pull_short_interest,
        "ownership":      pull_ownership,
        "universe_meta":  pull_universe_meta,
    }

    results = {}
    for name, fn in pulls.items():
        print(f"  → Refinitiv: {name}...")
        try:
            results[name] = fn(tickers)
        except Exception as e:
            print(f"    [WARN] {name} failed: {e}")
            results[name] = pd.DataFrame()

    return results


def merge_all_factors(factor_dict: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Merge all factor DataFrames on 'ticker' into a single wide DataFrame.
    """
    base = factor_dict.get("universe_meta", pd.DataFrame())
    if base.empty:
        base = pd.DataFrame({"ticker": []})

    for name, df in factor_dict.items():
        if name == "universe_meta" or df.empty:
            continue
        # Drop columns that already exist in base (except ticker)
        overlap = [c for c in df.columns if c in base.columns and c != "ticker"]
        df = df.drop(columns=overlap)
        base = base.merge(df, on="ticker", how="left")

    return base


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _ticker_to_ric(ticker: str) -> str:
    """Convert a US ticker to a Refinitiv RIC. Handles basic cases."""
    ticker = ticker.upper().strip()
    # Already a RIC (has exchange suffix)
    if "." in ticker:
        return ticker
    # Default to NASDAQ (.O) — override per ticker if needed
    return f"{ticker}.O"


def _to_rics(tickers: list[str]) -> list[str]:
    return [_ticker_to_ric(t) for t in tickers]


def _rics_to_tickers(rics: list[str], original: list[str]) -> list[str]:
    """Map RICs back to original tickers."""
    ric_map = {_ticker_to_ric(t): t for t in original}
    return [ric_map.get(r, r.split(".")[0]) for r in rics]
