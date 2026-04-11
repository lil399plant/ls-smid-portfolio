"""
Data collection pipeline.

Pulls all data needed for the factor model and saves to data/processed/.
Run this monthly before rebalance.

Usage:
    python -m data.collect --universe sp400 --save
"""

import argparse
import pandas as pd
from pathlib import Path
from data.openbb_client import obb  # credentials loaded here

PROCESSED = Path(__file__).resolve().parent / "processed"
PROCESSED.mkdir(exist_ok=True)


# ── Equity fundamentals (FMP) ─────────────────────────────────────────────────

def pull_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """Pull key ratios and income statement metrics for all tickers."""
    rows = []
    for ticker in tickers:
        try:
            r = obb.equity.fundamental.ratios(ticker, provider="fmp", limit=1).to_df()
            r['ticker'] = ticker
            rows.append(r)
        except Exception as e:
            print(f"  [WARN] {ticker} fundamentals failed: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def pull_metrics(tickers: list[str]) -> pd.DataFrame:
    """Pull ROIC, EV/EBITDA, etc."""
    rows = []
    for ticker in tickers:
        try:
            m = obb.equity.fundamental.metrics(ticker, provider="fmp", limit=1).to_df()
            m['ticker'] = ticker
            rows.append(m)
        except Exception as e:
            print(f"  [WARN] {ticker} metrics failed: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ── Price data (Polygon) ──────────────────────────────────────────────────────

def pull_prices(tickers: list[str], start: str = "2022-01-01") -> pd.DataFrame:
    """Pull daily OHLCV for all tickers."""
    rows = []
    for ticker in tickers:
        try:
            p = obb.equity.price.historical(
                ticker, start_date=start, provider="polygon"
            ).to_df()
            p['ticker'] = ticker
            rows.append(p)
        except Exception as e:
            print(f"  [WARN] {ticker} prices failed: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ── Profiles (sector, market cap) ─────────────────────────────────────────────

def pull_profiles(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        try:
            p = obb.equity.profile(ticker, provider="fmp").to_df()
            p['ticker'] = ticker
            rows.append(p)
        except Exception as e:
            print(f"  [WARN] {ticker} profile failed: {e}")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ── Macro (FRED) ──────────────────────────────────────────────────────────────

def pull_macro() -> pd.DataFrame:
    """Pull key macro signals: VIX, yield curve, credit spreads."""
    series = {
        "vix":          "VIXCLS",
        "yield_10y":    "DGS10",
        "yield_2y":     "DGS2",
        "hy_spread":    "BAMLH0A0HYM2",
        "ig_spread":    "BAMLC0A0CM",
    }
    frames = {}
    for name, fred_id in series.items():
        try:
            df = obb.economy.fred_series(fred_id, provider="fred").to_df()
            frames[name] = df['value']
        except Exception as e:
            print(f"  [WARN] FRED {fred_id} failed: {e}")
    return pd.DataFrame(frames) if frames else pd.DataFrame()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(tickers: list[str], save: bool = True):
    print(f"Collecting data for {len(tickers)} tickers...")

    print("  → Profiles...")
    profiles = pull_profiles(tickers)

    print("  → Fundamentals...")
    fundamentals = pull_fundamentals(tickers)

    print("  → Metrics...")
    metrics = pull_metrics(tickers)

    print("  → Prices...")
    prices = pull_prices(tickers)

    print("  → Macro...")
    macro = pull_macro()

    if save:
        profiles.to_parquet(PROCESSED / "profiles.parquet")
        fundamentals.to_parquet(PROCESSED / "fundamentals.parquet")
        metrics.to_parquet(PROCESSED / "metrics.parquet")
        prices.to_parquet(PROCESSED / "prices.parquet")
        macro.to_parquet(PROCESSED / "macro.parquet")
        print(f"  ✓ Saved to {PROCESSED}")

    return dict(profiles=profiles, fundamentals=fundamentals,
                metrics=metrics, prices=prices, macro=macro)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", help="List of tickers (overrides default)")
    parser.add_argument("--save", action="store_true", default=True)
    args = parser.parse_args()

    # Default: small test universe — replace with full S&P 400 + R2000 list
    DEFAULT_TICKERS = ["MEDP", "CALM", "IRDM", "ITRI", "MGEE",
                       "PLXS", "SWX", "LBRT", "CRVL", "KTOS"]

    tickers = args.tickers or DEFAULT_TICKERS
    main(tickers, save=args.save)
