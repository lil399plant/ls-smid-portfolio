"""
Refinitiv data pulls for the L/S SMID factor model.

Each function returns a clean DataFrame with a 'ticker' column.
Uses position-based column renaming since Refinitiv returns display names
(e.g. "Enterprise Value To EBITDA (Daily Time Series Ratio)") not TR. codes.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from data.refinitiv_client import rd, REFINITIV_AVAILABLE


def _require_refinitiv():
    if not REFINITIV_AVAILABLE:
        raise RuntimeError(
            "Refinitiv is not connected. Open Eikon/Workspace and restart."
        )


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Safely get a numeric column as a Series — returns NaN Series if missing."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _get_data(rics: list[str], fields: dict) -> pd.DataFrame:
    """
    Wrapper around rd.get_data that:
    - Renames columns by position (not display name) to avoid Refinitiv naming inconsistencies
    - Maps Instrument column back to clean tickers
    - Returns a clean DataFrame
    """
    df = rd.get_data(universe=rics, fields=list(fields.keys()))

    # Separate Instrument column from data columns
    data_cols = [c for c in df.columns if c != "Instrument"]
    target_names = list(fields.values())

    # Rename by position
    rename_map = dict(zip(data_cols, target_names[:len(data_cols)]))
    df = df.rename(columns=rename_map)

    # Map RIC → clean ticker
    if "Instrument" in df.columns:
        df["ticker"] = df["Instrument"].apply(
            lambda x: x.split(".")[0] if isinstance(x, str) else str(x)
        )
        df = df.drop(columns=["Instrument"])

    return df.reset_index(drop=True)


def _ticker_to_ric(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if "." in ticker:
        return ticker
    return f"{ticker}.O"  # default to NASDAQ


def _to_rics(tickers: list[str]) -> list[str]:
    return [_ticker_to_ric(t) for t in tickers]


# ── VALUE ─────────────────────────────────────────────────────────────────────

def pull_value(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    return _get_data(_to_rics(tickers), {
        "TR.EVtoEBITDA":        "ev_ebitda",
        "TR.PriceToCFPerShare": "p_fcf",
        "TR.PriceToBookValue":  "p_b",
        "TR.PEMean":            "pe_ratio",
    })


# ── QUALITY ───────────────────────────────────────────────────────────────────

def pull_quality(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    df = _get_data(_to_rics(tickers), {
        "TR.ROIC":                     "roic",
        "TR.GrossProfitMarginPercent":  "gross_margin",
        "TR.NetIncome":                "net_income",
        "TR.CashFromOperations":       "operating_cf",
        "TR.TotalAssets":              "total_assets",
        "TR.ReturnOnAssets":           "roa",
        "TR.ReturnOnEquity":           "roe",
        "TR.NetProfitMargin":          "net_profit_margin",
    })
    # Accruals ratio = (Net Income - OCF) / Total Assets
    ta = _col(df, "total_assets").where(lambda x: x != 0, np.nan)
    ni = _col(df, "net_income")
    cf = _col(df, "operating_cf")
    df["accruals_ratio"] = (ni - cf) / ta
    return df


# ── MOMENTUM ──────────────────────────────────────────────────────────────────

def pull_momentum(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    df = _get_data(_to_rics(tickers), {
        "TR.TotalReturn1Yr":   "ret_12m",
        "TR.TotalReturn1Mo":   "ret_1m",
        "TR.PricePctChg1Y":    "price_chg_1y",
        "TR.PricePctChg1M":    "price_chg_1m",
        "TR.EPSSmartEst":      "eps_smart_est",
        "TR.EPSMeanEstimate":  "eps_mean_est",
        "TR.EPSActValue":      "eps_actual",
    })
    ret12 = _col(df, "ret_12m")
    ret1  = _col(df, "ret_1m")
    # EPS revision proxy: smart est vs mean est divergence
    smart = _col(df, "eps_smart_est")
    mean  = _col(df, "eps_mean_est")
    df["price_momentum"] = ret12 - ret1
    df["eps_revision"]   = (smart - mean) / mean.abs().where(mean != 0, np.nan)
    return df


# ── GROWTH ────────────────────────────────────────────────────────────────────

def pull_growth(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    # Use confirmed-working fields only
    df = _get_data(_to_rics(tickers), {
        "TR.RevenueActValue":   "revenue_actual",
        "TR.RevenueMean":       "revenue_mean_est",
        "TR.NetProfitMargin":   "net_profit_margin",
        "TR.EPSActValue":       "eps_actual",
        "TR.EPSSmartEst":       "eps_smart_est",
        "TR.ROIC":              "roic",
    })
    # Forward revenue growth proxy: (consensus est - actual) / actual
    rev_act  = _col(df, "revenue_actual")
    rev_est  = _col(df, "revenue_mean_est")
    df["rev_growth_fwd"] = (rev_est - rev_act) / rev_act.abs().where(rev_act != 0, np.nan)
    # Forward EPS growth proxy
    eps_act  = _col(df, "eps_actual")
    eps_est  = _col(df, "eps_smart_est")
    df["eps_growth_fwd"] = (eps_est - eps_act) / eps_act.abs().where(eps_act != 0, np.nan)
    return df


# ── FINANCIAL HEALTH ──────────────────────────────────────────────────────────

def pull_health(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    df = _get_data(_to_rics(tickers), {
        "TR.WorkingCapital":    "working_capital",
        "TR.RetainedEarnings":  "retained_earnings",
        "TR.EBIT":              "ebit",
        "TR.MarketCap":         "market_cap",
        "TR.TotalLiabilities":  "total_liabilities",
        "TR.Revenue":           "revenue",
        "TR.TotalAssets":       "total_assets",
        "TR.NetDebtToEBITDA":   "net_debt_ebitda",
        "TR.InterestCoverage":  "interest_coverage",
    })
    # Altman Z-Score
    ta   = _col(df, "total_assets").where(lambda x: x != 0, np.nan)
    tl   = _col(df, "total_liabilities").where(lambda x: x != 0, np.nan)
    wc   = _col(df, "working_capital")
    re   = _col(df, "retained_earnings")
    ebit = _col(df, "ebit")
    mc   = _col(df, "market_cap")
    rev  = _col(df, "revenue")
    df["altman_z"] = (
        1.2 * (wc / ta) +
        1.4 * (re / ta) +
        3.3 * (ebit / ta) +
        0.6 * (mc / tl) +
        1.0 * (rev / ta)
    )
    return df


# ── SHORT INTEREST ────────────────────────────────────────────────────────────

def pull_short_interest(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    return _get_data(_to_rics(tickers), {
        "TR.ShortInterest":       "short_interest_shares",
        "TR.ShortInterestRatio":  "short_ratio",
        "TR.ShortPercentOfFloat": "short_pct_float",
        "TR.SharesFloat":         "shares_float",
    })


# ── OWNERSHIP ─────────────────────────────────────────────────────────────────

def pull_ownership(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    return _get_data(_to_rics(tickers), {
        "TR.InstitutionalOwnership": "institutional_pct",
        "TR.InsiderOwnership":       "insider_pct",
        "TR.SharesOutstanding":      "shares_outstanding",
    })


# ── UNIVERSE META ─────────────────────────────────────────────────────────────

def pull_universe_meta(tickers: list[str]) -> pd.DataFrame:
    _require_refinitiv()
    df = _get_data(_to_rics(tickers), {
        "TR.GICSSectorCode":     "gics_sector_code",
        "TR.GICSSectorName":     "gics_sector",
        "TR.MarketCap":          "market_cap_m",
        "TR.Beta":               "beta",
        "TR.PriceClose":         "price",
        "TR.Volume10DayAvg":     "adv_10d",
    })
    # Convert market cap to $M
    df["market_cap_m"] = pd.to_numeric(df.get("market_cap_m"), errors="coerce") / 1e6
    return df


# ── PRICE HISTORY ─────────────────────────────────────────────────────────────

def pull_price_history(
    tickers: list[str],
    days_back: int = 756,
    interval: str = "daily"
) -> pd.DataFrame:
    _require_refinitiv()
    start  = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end    = datetime.now().strftime("%Y-%m-%d")
    frames = []

    for ticker in tickers:
        ric = _ticker_to_ric(ticker)
        try:
            df = rd.get_history(
                universe=ric,
                fields=["TR.OpenPrice", "TR.HighPrice", "TR.LowPrice",
                        "TR.ClosePrice", "TR.Volume"],
                start=start, end=end, interval=interval,
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)
                df.columns = [c.lower().replace(" ", "_") for c in df.columns]
                df["ticker"] = ticker
                frames.append(df)
        except Exception as e:
            print(f"    [WARN] Price history {ticker}: {e}")

    return pd.concat(frames) if frames else pd.DataFrame()


# ── FULL PULL ─────────────────────────────────────────────────────────────────

def pull_all_factors(tickers: list[str]) -> dict[str, pd.DataFrame]:
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
    base = factor_dict.get("universe_meta", pd.DataFrame())
    if base.empty:
        base = pd.DataFrame({"ticker": []})
    for name, df in factor_dict.items():
        if name == "universe_meta" or df.empty or "ticker" not in df.columns:
            continue
        overlap = [c for c in df.columns if c in base.columns and c != "ticker"]
        base = base.merge(df.drop(columns=overlap), on="ticker", how="left")
    return base
