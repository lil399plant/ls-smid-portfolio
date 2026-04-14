"""
Master data collection pipeline.

Three-tier data retrieval with automatic fallback:
  Tier 1 — Direct refinitiv.data SDK, single-batch call (mr114trader style)
  Tier 2 — refinitiv_pulls.py thematic functions (position-based column renaming)
  Tier 3 — OpenBB (yfinance / fmp / alpha_vantage providers)

Each tier falls through to the next if data is missing or the call fails.
Refinitiv tiers are only attempted when REFINITIV_AVAILABLE is True.

Usage:
    python -m data.collect                        # full run, saves output
    python -m data.collect --tickers AAPL MSFT   # specific tickers
    python -m data.collect --source openbb        # OpenBB only
    python -m data.collect --source refinitiv     # Refinitiv only (both tiers)
"""

import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

from data.openbb_client import obb
from data.refinitiv_client import rd, REFINITIV_AVAILABLE, close as rd_close

PROCESSED = Path(__file__).resolve().parent / "processed"
PROCESSED.mkdir(exist_ok=True)

# ── Key columns that must be present and non-null for a pull to be "sufficient"
# Only use fields confirmed to return data from this Refinitiv license.
# pe_ratio / roic / gross_margin require field codes unavailable on this terminal.
_REQUIRED_FACTOR_COLS = ["ev_ebitda", "price"]
_REQUIRED_PRICE_COLS  = ["ticker"]

# ── Tier 1 field map: TR. code → clean column name (same schema as refinitiv_pulls.py)
_T1_FIELDS = {
    "TR.EVtoEBITDA":              "ev_ebitda",
    "TR.PricePERatio":            "pe_ratio",
    "TR.ROIC":                    "roic",
    "TR.GrossProfitMarginPercent":"gross_margin",
    "TR.NetDebtToEBITDA":         "net_debt_ebitda",
    "TR.PriceClose":              "price",
    "TR.PriceToCFPerShare":       "p_fcf",
    "TR.PriceToBookValue":        "p_b",
    "TR.ReturnOnAssets":          "roa",
    "TR.ReturnOnEquity":          "roe",
    "TR.NetProfitMargin":         "net_profit_margin",
    "TR.NetIncome":               "net_income",
    "TR.CashFromOperations":      "operating_cf",
    "TR.TotalAssets":             "total_assets",
    "TR.TotalReturn1Yr":          "ret_12m",
    "TR.TotalReturn1Mo":          "ret_1m",
    "TR.EPSSmartEst":             "eps_smart_est",
    "TR.EPSMeanEstimate":         "eps_mean_est",
    "TR.EPSActValue":             "eps_actual",
    "TR.RevenueActValue":         "revenue_actual",
    "TR.RevenueMean":             "revenue_mean_est",
    "TR.WorkingCapital":          "working_capital",
    "TR.RetainedEarnings":        "retained_earnings",
    "TR.EBIT":                    "ebit",
    "TR.MarketCap":               "market_cap_m",
    "TR.TotalLiabilities":        "total_liabilities",
    "TR.Revenue":                 "revenue",
    "TR.InterestCoverage":        "interest_coverage",
    "TR.ShortInterest":           "short_interest_shares",
    "TR.ShortInterestRatio":      "short_ratio",
    "TR.ShortPercentOfFloat":     "short_pct_float",
    "TR.SharesFloat":             "shares_float",
    "TR.InstitutionalOwnership":  "institutional_pct",
    "TR.InsiderOwnership":        "insider_pct",
    "TR.SharesOutstanding":       "shares_outstanding",
    "TR.GICSSectorCode":          "gics_sector_code",
    "TR.GICSSectorName":          "gics_sector",
    "TR.Beta":                    "beta",
    "TR.Volume10DayAvg":          "adv_10d",
}


def _to_df(result) -> pd.DataFrame:
    """Handle both OBBject (.to_df()) and plain DataFrame outputs."""
    if result is None:
        return pd.DataFrame()
    if hasattr(result, "to_df"):
        return result.to_df()
    if isinstance(result, pd.DataFrame):
        return result
    return pd.DataFrame()


# RICs that are not NASDAQ-listed and need a different suffix.
# Add entries here when a ticker fails with .O resolution errors.
_RIC_OVERRIDES = {
    "SWX":  "SWX.N",   # NYSE: Southwest Gas
    "LBRT": "LBRT.N",  # NYSE: Liberty Energy
    "RBC":  "RBC.N",   # NYSE: RBC Bearings
    "GS":   "GS.N",    # NYSE: Goldman Sachs
    "WCC":  "WCC.N",   # NYSE: WESCO International
    "PINS": "PINS.N",  # NYSE: Pinterest
    "CHGG": "CHGG.N",  # NYSE: Chegg
    "MGEE": "MGEE.O",  # NASDAQ
    "CALM": "CALM.O",  # NASDAQ
}

def _ticker_to_ric(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if "." in ticker:
        return ticker
    return _RIC_OVERRIDES.get(ticker, f"{ticker}.O")


def _to_rics(tickers: list[str]) -> list[str]:
    return [_ticker_to_ric(t) for t in tickers]


def _coerce_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """
    Coerce object-dtype columns to float where possible so pyarrow can
    serialize them. Empty strings are normalized to NaN first.
    Columns with genuine string content (e.g. names, sectors) are left as-is.
    """
    df = df.copy()
    for col in df.columns:
        if col == "ticker":
            continue
        if df[col].dtype == object:
            # Normalize empty strings to NaN before attempting numeric coercion
            series = df[col].replace("", np.nan)
            coerced = pd.to_numeric(series, errors="coerce")
            # Use coerced result if at least one value converted successfully,
            # or if the original column was all-null/empty anyway
            if coerced.notna().any() or series.isna().all():
                df[col] = coerced
            else:
                df[col] = series  # keep as string but with NaN instead of ''
    return df


def _is_sufficient(df: pd.DataFrame, required_cols: list[str] = _REQUIRED_FACTOR_COLS) -> bool:
    """
    Returns True if the DataFrame is non-empty and every required column
    has at least one non-null value.
    """
    if df is None or df.empty:
        return False
    for col in required_cols:
        if col not in df.columns or df[col].isna().all():
            return False
    return True


def _fill_missing(base: pd.DataFrame, supplement: pd.DataFrame) -> pd.DataFrame:
    """
    Fill NaN values in `base` using `supplement`, joined on ticker.
    Columns only in `supplement` are added to `base`.
    """
    if supplement is None or supplement.empty or "ticker" not in supplement.columns:
        return base
    if base.empty:
        return supplement

    merged = base.merge(supplement, on="ticker", how="left", suffixes=("", "_sup"))

    for col in supplement.columns:
        if col == "ticker":
            continue
        sup_col = f"{col}_sup"
        if col in merged.columns and sup_col in merged.columns:
            # Fill NaNs in base column from supplement
            merged[col] = merged[col].where(merged[col].notna(), merged[sup_col])
            merged = merged.drop(columns=[sup_col])
        elif sup_col in merged.columns:
            merged = merged.rename(columns={sup_col: col})

    return merged


# ══════════════════════════════════════════════════════════════════════════════
# TIER 1 — Direct Refinitiv SDK (mr114trader style)
# ══════════════════════════════════════════════════════════════════════════════

# Display-name substring → clean column name.
# Refinitiv returns human-readable display names that vary slightly across
# Eikon versions/locales but share consistent substrings. We match on these
# rather than relying on column position (which breaks when Refinitiv silently
# drops fields from mixed-data-set batch calls).
_DISPLAY_NAME_MAP = [
    ("Enterprise Value To EBITDA",       "ev_ebitda"),
    ("P/E",                              "pe_ratio"),
    ("Price To Cash Flow Per Share",      "p_fcf"),
    ("Price To Book",                    "p_b"),
    ("Price Close",                      "price"),
    ("Return On Invested Capital",       "roic"),
    ("Gross Profit Margin",              "gross_margin"),
    ("Return On Assets",                 "roa"),
    ("Return On Equity",                 "roe"),
    ("Net Profit Margin",                "net_profit_margin"),
    ("Net Income",                       "net_income"),
    ("Cash From Operations",             "operating_cf"),
    ("Total Assets",                     "total_assets"),
    ("Net Debt To EBITDA",               "net_debt_ebitda"),
    ("1 Year Total Return",              "ret_12m"),
    ("1 Month Total Return",             "ret_1m"),
    ("SmartEstimate",                    "eps_smart_est"),
    ("Mean Estimate",                    "eps_mean_est"),
    ("Earnings Per Share - Actual",      "eps_actual"),
    ("Revenue - Actual",                 "revenue_actual"),
    ("Revenue - Mean",                   "revenue_mean_est"),
    ("Working Capital",                  "working_capital"),
    ("Retained Earnings",                "retained_earnings"),
    ("EBIT",                             "ebit"),
    ("Total Liabilities",                "total_liabilities"),
    ("Int Exp",                          "interest_coverage"),
    ("Short Interest",                   "short_interest_shares"),
    ("Short Interest Ratio",             "short_ratio"),
    ("Short Percent Of Float",           "short_pct_float"),
    ("Shares Float",                     "shares_float"),
    ("Institutional Ownership",          "institutional_pct"),
    ("Insider Ownership",                "insider_pct"),
    ("Outstanding Shares",               "shares_outstanding"),
    ("GICS Sector Code",                 "gics_sector_code"),
    ("GICS Sector Name",                 "gics_sector"),
    ("Market Capitalisation",            "market_cap_m"),
    ("Beta",                             "beta"),
    ("Average Daily Volume",             "adv_10d"),
    # Revenue (bare) — matched last so Revenue-Actual/Mean match first
    ("Revenue",                          "revenue"),
]

# All TR. field codes to request in one batch.
# Only includes codes confirmed to return data on this Refinitiv license.
# Fields like TR.ROIC, TR.PricePERatio, TR.GrossProfitMarginPercent error on
# this terminal — those are expected to come from Tier 2 (refinitiv_pulls.py).
_T1_FIELDS_LIST = [
    "TR.EVtoEBITDA", "TR.PriceToCFPerShare", "TR.PriceClose",
    "TR.NetProfitMargin", "TR.NetIncome", "TR.TotalAssets",
    "TR.NetDebtToEBITDA", "TR.Revenue", "TR.TotalReturn1Yr", "TR.TotalReturn1Mo",
    "TR.EPSSmartEst", "TR.EPSMeanEstimate", "TR.EPSActValue",
    "TR.RevenueActValue", "TR.RevenueMean", "TR.WorkingCapital",
    "TR.RetainedEarnings", "TR.EBIT", "TR.TotalLiabilities",
    "TR.InterestCoverage", "TR.ShortInterest",
    "TR.SharesOutstanding", "TR.GICSSectorCode",
]


def _match_display_name(col: str) -> str | None:
    """Match a Refinitiv display name to a clean column name via substring."""
    for substring, clean in _DISPLAY_NAME_MAP:
        if substring.lower() in col.lower():
            return clean
    return None


def tier1_rd_pull_factors(tickers: list[str]) -> pd.DataFrame:
    """
    Single rd.get_data batch with all fields. Columns are identified by
    display-name substring matching (not position) so dropped fields don't
    corrupt the renaming of fields that were returned.
    """
    if not REFINITIV_AVAILABLE or rd is None:
        return pd.DataFrame()

    rics = _to_rics(tickers)
    try:
        df = rd.get_data(universe=rics, fields=_T1_FIELDS_LIST)
        if df is None or df.empty:
            return pd.DataFrame()
    except Exception as e:
        print(f"  [Tier 1] rd.get_data failed: {e}")
        return pd.DataFrame()

    # Map Instrument → ticker
    if "Instrument" in df.columns:
        df["ticker"] = df["Instrument"].apply(
            lambda x: x.split(".")[0] if isinstance(x, str) else str(x)
        )
        df = df.drop(columns=["Instrument"])

    # Rename returned columns by display-name substring matching.
    # Unmatched columns are dropped; clean names already used are skipped
    # (first match wins, so ordering in _DISPLAY_NAME_MAP matters).
    rename_map = {}
    used_clean = set()
    for col in df.columns:
        if col == "ticker":
            continue
        clean = _match_display_name(col)
        if clean and clean not in used_clean:
            rename_map[col] = clean
            used_clean.add(clean)
    df = df.rename(columns=rename_map)
    # Drop any columns that weren't matched
    drop = [c for c in df.columns if c not in used_clean and c != "ticker"]
    df = df.drop(columns=drop)

    # Convert market cap to $M
    if "market_cap_m" in df.columns:
        df["market_cap_m"] = pd.to_numeric(df["market_cap_m"], errors="coerce") / 1e6

    # Derived metrics
    def _num(col):
        return pd.to_numeric(df.get(col, pd.Series(np.nan, index=df.index)), errors="coerce")

    ta = _num("total_assets").where(lambda x: x != 0, np.nan)
    df["accruals_ratio"] = (_num("net_income") - _num("operating_cf")) / ta

    rev_act = _num("revenue_actual")
    df["rev_growth_fwd"] = (_num("revenue_mean_est") - rev_act) / rev_act.abs().where(rev_act != 0, np.nan)

    eps_act = _num("eps_actual")
    df["eps_growth_fwd"] = (_num("eps_smart_est") - eps_act) / eps_act.abs().where(eps_act != 0, np.nan)

    df["price_momentum"] = _num("ret_12m") - _num("ret_1m")

    smart = _num("eps_smart_est")
    mean  = _num("eps_mean_est")
    df["eps_revision"] = (smart - mean) / mean.abs().where(mean != 0, np.nan)

    tl = _num("total_liabilities").where(lambda x: x != 0, np.nan)
    mc = _num("market_cap_m") * 1e6
    df["altman_z"] = (
        1.2 * (_num("working_capital") / ta) +
        1.4 * (_num("retained_earnings") / ta) +
        3.3 * (_num("ebit") / ta) +
        0.6 * (mc / tl) +
        1.0 * (_num("revenue") / ta)
    )

    return df.reset_index(drop=True)


def tier1_rd_pull_prices(tickers: list[str], days_back: int = 756) -> pd.DataFrame:
    """Price history via rd.get_history — same approach as mr114trader."""
    if not REFINITIV_AVAILABLE or rd is None:
        return pd.DataFrame()

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
                start=start, end=end, interval="daily",
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)
                df.columns = [c.lower().replace(" ", "_") for c in df.columns]
                df["ticker"] = ticker
                frames.append(df)
        except Exception as e:
            print(f"    [Tier 1 WARN] Price history {ticker}: {e}")

    return pd.concat(frames) if frames else pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# TIER 2 — refinitiv_pulls.py thematic functions
# ══════════════════════════════════════════════════════════════════════════════

def tier2_rd_pull(tickers: list[str]) -> dict[str, pd.DataFrame]:
    if not REFINITIV_AVAILABLE:
        print("  [Tier 2] Refinitiv not connected — skipping.")
        return {}

    from data.refinitiv_pulls import pull_all_factors, merge_all_factors
    factor_dict = pull_all_factors(tickers)
    merged = merge_all_factors(factor_dict)
    return {"factors": merged, **factor_dict}


# ══════════════════════════════════════════════════════════════════════════════
# TIER 3 — OpenBB
# ══════════════════════════════════════════════════════════════════════════════

def obb_pull_profiles(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for t in tickers:
        for provider in ("yfinance", "fmp"):
            try:
                r = _to_df(obb.equity.profile(t, provider=provider))
                if not r.empty:
                    r["ticker"] = t
                    rows.append(r)
                    break
            except Exception:
                continue
        else:
            print(f"    [OBB WARN] profile {t}: all providers failed")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def obb_pull_fundamentals(tickers: list[str]) -> pd.DataFrame:
    # equity.fundamental.ratios only supports fmp/intrinio (not yfinance)
    rows = []
    for t in tickers:
        for provider in ("fmp", "intrinio"):
            try:
                r = _to_df(obb.equity.fundamental.ratios(t, provider=provider, limit=1))
                if not r.empty:
                    r["ticker"] = t
                    rows.append(r)
                    break
            except Exception:
                continue
        else:
            print(f"    [OBB WARN] fundamentals {t}: all providers failed")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def obb_pull_metrics(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for t in tickers:
        for provider in ("yfinance", "fmp"):
            try:
                m = _to_df(obb.equity.fundamental.metrics(t, provider=provider, limit=1))
                if not m.empty:
                    m["ticker"] = t
                    rows.append(m)
                    break
            except Exception:
                continue
        else:
            print(f"    [OBB WARN] metrics {t}: all providers failed")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def obb_pull_prices(tickers: list[str], start: str = "2022-01-01") -> pd.DataFrame:
    rows = []
    for t in tickers:
        for provider in ("yfinance", "alpha_vantage", "fmp"):
            try:
                p = _to_df(obb.equity.price.historical(t, start_date=start, provider=provider))
                if not p.empty:
                    p["ticker"] = t
                    rows.append(p)
                    break
            except Exception:
                continue
        else:
            print(f"    [OBB WARN] prices {t}: all providers failed")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def obb_pull_macro() -> pd.DataFrame:
    series = {
        "vix":       "VIXCLS",
        "yield_10y": "DGS10",
        "yield_2y":  "DGS2",
        "hy_spread": "BAMLH0A0HYM2",
        "ig_spread": "BAMLC0A0CM",
    }
    frames = {}
    for name, fred_id in series.items():
        try:
            df = _to_df(obb.economy.fred_series(fred_id, provider="fred"))
            if not df.empty and "value" in df.columns:
                frames[name] = df["value"]
        except Exception as e:
            print(f"    [OBB WARN] FRED {fred_id}: {e}")
    return pd.DataFrame(frames) if frames else pd.DataFrame()


def obb_pull_insider_trades(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for t in tickers:
        try:
            ins = _to_df(obb.equity.ownership.insider_trading(t, provider="fmp", limit=20))
            if not ins.empty:
                ins["ticker"] = t
                rows.append(ins)
        except Exception as e:
            print(f"    [OBB WARN] insider trades {t}: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def obb_pull_congressional_trades(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for t in tickers:
        try:
            c = _to_df(obb.equity.ownership.government_trades(t, provider="quiverquant"))
            if not c.empty:
                c["ticker"] = t
                rows.append(c)
        except Exception as e:
            print(f"    [OBB WARN] congressional trades {t}: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def obb_pull_all(tickers: list[str]) -> dict[str, pd.DataFrame]:
    print("  → Profiles...")
    obb_data = {"profiles": obb_pull_profiles(tickers)}
    print("  → Fundamentals...")
    obb_data["fundamentals"] = obb_pull_fundamentals(tickers)
    print("  → Metrics...")
    obb_data["metrics"]      = obb_pull_metrics(tickers)
    print("  → Prices...")
    obb_data["prices"]       = obb_pull_prices(tickers)
    print("  → Macro...")
    obb_data["macro"]        = obb_pull_macro()
    print("  → Insider trades...")
    obb_data["insider"]      = obb_pull_insider_trades(tickers)
    return obb_data


def obb_build_factors(obb_data: dict) -> pd.DataFrame:
    """Merge OpenBB fundamentals + metrics + profiles into a single factor DataFrame."""
    base = obb_data.get("profiles", pd.DataFrame())
    for key in ("fundamentals", "metrics"):
        df = obb_data.get(key, pd.DataFrame())
        if not df.empty and "ticker" in df.columns:
            overlap = [c for c in df.columns if c in base.columns and c != "ticker"]
            base = base.merge(df.drop(columns=overlap), on="ticker", how="left")
    return base


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main(
    tickers: list[str],
    source: str = "both",   # "both" | "openbb" | "refinitiv"
    save: bool = True,
):
    print(f"\n{'='*60}")
    print(f"Data Collection Pipeline  —  3-Tier Fallback")
    print(f"Tickers: {len(tickers)} | Source: {source}")
    print(f"Refinitiv available: {REFINITIV_AVAILABLE}")
    print(f"{'='*60}\n")

    factors_df = pd.DataFrame()
    prices_df  = pd.DataFrame()
    macro_df   = pd.DataFrame()
    obb_data   = {}

    use_refinitiv = source in ("both", "refinitiv")
    use_openbb    = source in ("both", "openbb")

    # ── Tier 1: Direct Refinitiv SDK (mr114trader style) ──────────────────────
    if use_refinitiv and REFINITIV_AVAILABLE:
        print("[ Tier 1 — Direct Refinitiv SDK ]")
        factors_df = tier1_rd_pull_factors(tickers)
        prices_df  = tier1_rd_pull_prices(tickers)

        if _is_sufficient(factors_df):
            print("  ✓ Tier 1 factors: sufficient\n")
        else:
            print("  ✗ Tier 1 factors: missing data — falling back to Tier 2\n")

            # ── Tier 2: refinitiv_pulls.py thematic functions ──────────────────
            print("[ Tier 2 — Refinitiv thematic pulls ]")
            rd_data = tier2_rd_pull(tickers)
            t2_factors = rd_data.get("factors", pd.DataFrame())

            if _is_sufficient(t2_factors):
                print("  ✓ Tier 2 factors: sufficient")
                # Fill any remaining gaps in Tier 1 output with Tier 2 data
                if not factors_df.empty:
                    factors_df = _fill_missing(factors_df, t2_factors)
                else:
                    factors_df = t2_factors
                # Use Tier 2 prices if Tier 1 prices are empty
                if prices_df.empty:
                    from data.refinitiv_pulls import pull_price_history
                    prices_df = pull_price_history(tickers)
                print()
            else:
                print("  ✗ Tier 2 factors: missing data — falling back to Tier 3 (OpenBB)\n")
                # Carry forward whatever partial data we have from Tier 1/2
                if not t2_factors.empty:
                    factors_df = _fill_missing(factors_df, t2_factors)

    # ── Tier 3: OpenBB ────────────────────────────────────────────────────────
    # Always pull OpenBB if source includes it.
    # When Refinitiv data is sufficient, OpenBB still fills any remaining gaps.
    if use_openbb:
        print("[ Tier 3 — OpenBB ]")
        obb_data = obb_pull_all(tickers)
        macro_df = obb_data.get("macro", pd.DataFrame())
        obb_factors = obb_build_factors(obb_data)

        if factors_df.empty:
            print("  → Using OpenBB as primary factor source")
            factors_df = obb_factors
        else:
            print("  → Using OpenBB to fill remaining gaps")
            factors_df = _fill_missing(factors_df, obb_factors)

        if prices_df.empty:
            prices_df = obb_data.get("prices", pd.DataFrame())

        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"[ Final dataset: {len(factors_df)} rows × {len(factors_df.columns)} columns ]")
    fill_pct = factors_df.notna().values.mean() * 100 if not factors_df.empty else 0
    print(f"  Fill rate: {fill_pct:.1f}%\n")

    # ── Save ──────────────────────────────────────────────────────────────────
    if save:
        if not factors_df.empty:
            factors_df = _coerce_for_parquet(factors_df)
            factors_df.to_parquet(PROCESSED / "master.parquet")
        if not prices_df.empty:
            prices_df.to_parquet(PROCESSED / "prices.parquet")
        if not macro_df.empty:
            macro_df.to_parquet(PROCESSED / "macro.parquet")
        insider = obb_data.get("insider", pd.DataFrame())
        if not insider.empty:
            insider.to_parquet(PROCESSED / "insider.parquet")
        print(f"  ✓ Saved to {PROCESSED}")

    rd_close()

    return {
        "master":   factors_df,
        "prices":   prices_df,
        "macro":    macro_df,
        "obb":      obb_data,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+")
    parser.add_argument("--source", choices=["both", "openbb", "refinitiv"], default="both")
    parser.add_argument("--save", action="store_true", default=True)
    args = parser.parse_args()

    DEFAULT_TICKERS = [
        "MEDP", "CALM", "IRDM", "ITRI", "MGEE",
        "PLXS", "SWX", "LBRT", "CRVL", "KTOS"
    ]

    main(args.tickers or DEFAULT_TICKERS, source=args.source, save=args.save)
