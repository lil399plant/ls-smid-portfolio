"""
Master data collection pipeline.

Pulls from both OpenBB and Refinitiv, merges into unified DataFrames,
and saves to data/processed/ as parquet files.

Refinitiv is used when Eikon/Workspace is open (or LSEG_APP_KEY is set).
OpenBB fills in everything else. If Refinitiv is unavailable, the pipeline
degrades gracefully to OpenBB-only mode.

Usage:
    python -m data.collect                        # full run, saves output
    python -m data.collect --tickers AAPL MSFT   # specific tickers
    python -m data.collect --source openbb        # OpenBB only
    python -m data.collect --source refinitiv     # Refinitiv only
"""

import argparse
import pandas as pd
from pathlib import Path

from data.openbb_client import obb
from data.refinitiv_client import REFINITIV_AVAILABLE, close as rd_close

PROCESSED = Path(__file__).resolve().parent / "processed"
PROCESSED.mkdir(exist_ok=True)


def _to_df(result) -> pd.DataFrame:
    """Handle both OBBject (.to_df()) and plain DataFrame outputs."""
    if result is None:
        return pd.DataFrame()
    if hasattr(result, "to_df"):
        return result.to_df()
    if isinstance(result, pd.DataFrame):
        return result
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# OPENBB PULLS
# ══════════════════════════════════════════════════════════════════════════════

def obb_pull_profiles(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for t in tickers:
        for provider in ("yfinance", "fmp"):  # yfinance is free, no tier limit
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
    rows = []
    for t in tickers:
        for provider in ("yfinance", "fmp"):
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


def obb_pull_news(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for t in tickers:
        try:
            n = _to_df(obb.news.company(t, provider="benzinga", limit=20))
            if not n.empty:
                n["ticker"] = t
                rows.append(n)
        except Exception as e:
            print(f"    [OBB WARN] news {t}: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


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


# ══════════════════════════════════════════════════════════════════════════════
# REFINITIV PULLS (only runs if Refinitiv is connected)
# ══════════════════════════════════════════════════════════════════════════════

def refinitiv_pull_all(tickers: list[str]) -> dict[str, pd.DataFrame]:
    if not REFINITIV_AVAILABLE:
        print("  [Refinitiv] Not connected — skipping. Open Eikon/Workspace to enable.")
        return {}

    from data.refinitiv_pulls import pull_all_factors, merge_all_factors
    factor_dict = pull_all_factors(tickers)
    merged = merge_all_factors(factor_dict)
    return {"factors": merged, **factor_dict}


# ══════════════════════════════════════════════════════════════════════════════
# MERGE — combine OpenBB + Refinitiv into unified output
# ══════════════════════════════════════════════════════════════════════════════

def merge_sources(obb_data: dict, rd_data: dict) -> pd.DataFrame:
    """
    Merge OpenBB and Refinitiv data on ticker.
    Refinitiv columns take precedence where both exist (higher quality data).
    """
    # Start with OpenBB profiles as base universe
    base = obb_data.get("profiles", pd.DataFrame())

    # Merge OpenBB fundamentals
    for key in ["fundamentals", "metrics"]:
        df = obb_data.get(key, pd.DataFrame())
        if not df.empty and "ticker" in df.columns:
            overlap = [c for c in df.columns if c in base.columns and c != "ticker"]
            base = base.merge(df.drop(columns=overlap), on="ticker", how="left")

    # Merge Refinitiv factors (overrides where available)
    rd_factors = rd_data.get("factors", pd.DataFrame())
    if not rd_factors.empty and "ticker" in rd_factors.columns:
        # Refinitiv takes precedence — drop OpenBB version of shared columns
        shared = [c for c in rd_factors.columns if c in base.columns and c != "ticker"]
        base = base.drop(columns=shared)
        base = base.merge(rd_factors, on="ticker", how="left")

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
    print(f"Data Collection Pipeline")
    print(f"Tickers: {len(tickers)} | Source: {source}")
    print(f"Refinitiv available: {REFINITIV_AVAILABLE}")
    print(f"{'='*60}\n")

    obb_data = {}
    rd_data  = {}

    # ── OpenBB ────────────────────────────────────────────────────────────────
    if source in ("both", "openbb"):
        print("[ OpenBB ]")
        print("  → Profiles...")
        obb_data["profiles"]    = obb_pull_profiles(tickers)
        print("  → Fundamentals...")
        obb_data["fundamentals"]= obb_pull_fundamentals(tickers)
        print("  → Metrics...")
        obb_data["metrics"]     = obb_pull_metrics(tickers)
        print("  → Prices...")
        obb_data["prices"]      = obb_pull_prices(tickers)
        print("  → Macro...")
        obb_data["macro"]       = obb_pull_macro()
        print("  → Insider trades...")
        obb_data["insider"]     = obb_pull_insider_trades(tickers)

    # ── Refinitiv ─────────────────────────────────────────────────────────────
    if source in ("both", "refinitiv"):
        print("\n[ Refinitiv ]")
        rd_data = refinitiv_pull_all(tickers)

    # ── Merge ─────────────────────────────────────────────────────────────────
    print("\n[ Merging sources... ]")
    merged = merge_sources(obb_data, rd_data)
    print(f"  → Merged: {len(merged)} rows × {len(merged.columns)} columns")

    # ── Save ──────────────────────────────────────────────────────────────────
    if save:
        merged.to_parquet(PROCESSED / "master.parquet")
        if not obb_data.get("prices", pd.DataFrame()).empty:
            obb_data["prices"].to_parquet(PROCESSED / "prices.parquet")
        if not obb_data.get("macro", pd.DataFrame()).empty:
            obb_data["macro"].to_parquet(PROCESSED / "macro.parquet")
        if rd_data.get("short_interest") is not None and not rd_data["short_interest"].empty:
            rd_data["short_interest"].to_parquet(PROCESSED / "short_interest.parquet")
        print(f"\n  ✓ Saved to {PROCESSED}")

    rd_close()
    return {"obb": obb_data, "refinitiv": rd_data, "master": merged}


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
