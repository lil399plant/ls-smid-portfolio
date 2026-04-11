"""
Quick connectivity test for all API keys in .env.
Prints PASS / FAIL for each provider.
"""

import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

results = {}

def test(name, fn):
    try:
        fn()
        results[name] = "✅ PASS"
    except Exception as e:
        results[name] = f"❌ FAIL — {e}"

# ── FMP ───────────────────────────────────────────────────────────────────────
def check_fmp():
    key = os.getenv("OPENBB_FMP_API_KEY")
    r = requests.get(f"https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={key}", timeout=8)
    assert r.status_code == 200 and r.json(), f"status {r.status_code}"

# ── Benzinga ──────────────────────────────────────────────────────────────────
def check_benzinga():
    key = os.getenv("OPENBB_BENZINGA_API_KEY")
    r = requests.get(
        "https://api.benzinga.com/api/v2/news",
        params={"token": key, "pageSize": 1},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"

# ── Nasdaq Data Link ──────────────────────────────────────────────────────────
def check_nasdaq():
    # Nasdaq Data Link blocks direct HTTP via Cloudflare — key validity
    # is confirmed by the non-401 response. Use via OpenBB SDK in practice.
    key = os.getenv("OPENBB_NASDAQ_API_KEY")
    r = requests.get(
        f"https://data.nasdaq.com/api/v3/datasets?database_code=FRED&per_page=1&api_key={key}",
        headers={"User-Agent": "python-requests/2.28"},
        timeout=8
    )
    # 403 = Cloudflare bot block (key is fine), 401 = bad key
    assert r.status_code != 401, f"Invalid key (401)"
    # Treat 403 as a soft pass — Cloudflare blocks scripts but key works via SDK
    if r.status_code == 403:
        raise Exception("Cloudflare blocked direct request — key likely valid, use via OpenBB SDK")

# ── Alpha Vantage ─────────────────────────────────────────────────────────────
def check_alpha_vantage():
    key = os.getenv("OPENBB_ALPHA_VANTAGE_API_KEY")
    r = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": key},
        timeout=8
    )
    data = r.json()
    assert "Global Quote" in data, f"unexpected response: {list(data.keys())}"

# ── CoinGecko ─────────────────────────────────────────────────────────────────
def check_coingecko():
    key = os.getenv("OPENBB_COINGECKO_API_KEY")
    r = requests.get(
        "https://api.coingecko.com/api/v3/ping",
        headers={"x-cg-demo-api-key": key},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"

# ── CoinDesk ──────────────────────────────────────────────────────────────────
def check_coindesk():
    key = os.getenv("OPENBB_COINDESK_API_KEY")
    r = requests.get(
        "https://data-api.coindesk.com/index/cc/v1/latest/tick",
        params={"market": "cadli", "instruments": "BTC-USD", "limit": 1},
        headers={"Authorization": f"Bearer {key}"},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"

# ── FRED ──────────────────────────────────────────────────────────────────────
def check_fred():
    key = os.getenv("OPENBB_FRED_API_KEY")
    r = requests.get(
        "https://api.stlouisfed.org/fred/series",
        params={"series_id": "GNPCA", "api_key": key, "file_type": "json"},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"

# ── BLS ───────────────────────────────────────────────────────────────────────
def check_bls():
    key = os.getenv("OPENBB_BLS_API_KEY")
    r = requests.post(
        "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        json={"seriesid": ["CUUR0000SA0"], "startyear": "2023", "endyear": "2023", "registrationkey": key},
        timeout=20
    )
    data = r.json()
    assert data.get("status") == "REQUEST_SUCCEEDED", f"status: {data.get('status')} | {data.get('message')}"

# ── EIA ───────────────────────────────────────────────────────────────────────
def check_eia():
    key = os.getenv("OPENBB_EIA_API_KEY")
    r = requests.get(
        "https://api.eia.gov/v2/petroleum/pri/spt/data/",
        params={"api_key": key, "length": 1},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"

# ── Congress.gov ──────────────────────────────────────────────────────────────
def check_congress():
    key = os.getenv("OPENBB_CONGRESS_API_KEY")
    r = requests.get(
        "https://api.congress.gov/v3/bill",
        params={"api_key": key, "limit": 1},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"

# ── CFTC ──────────────────────────────────────────────────────────────────────
def check_cftc():
    key = os.getenv("OPENBB_CFTC_API_KEY")
    r = requests.get(
        "https://publicreporting.cftc.gov/resource/6dca-aqww.json",
        params={"$limit": 1, "$$app_token": key},
        timeout=8
    )
    assert r.status_code == 200, f"status {r.status_code}"


# ── Run all tests ─────────────────────────────────────────────────────────────
checks = {
    "FMP":           check_fmp,
    "Benzinga":      check_benzinga,
    "Nasdaq":        check_nasdaq,
    "Alpha Vantage": check_alpha_vantage,
    "CoinGecko":     check_coingecko,
    "CoinDesk":      check_coindesk,
    "FRED":          check_fred,
    "BLS":           check_bls,
    "EIA":           check_eia,
    "Congress":      check_congress,
    "CFTC":          check_cftc,
}

print("\nTesting API keys...\n")
for name, fn in checks.items():
    key_var = f"OPENBB_{name.upper().replace(' ', '_')}_API_KEY"
    val = os.getenv(key_var, "")
    if not val or "your_" in val:
        results[name] = "⏭  SKIPPED — no key"
        continue
    test(name, fn)

max_len = max(len(k) for k in results)
for name, result in results.items():
    print(f"  {name:<{max_len}}  {result}")
print()
