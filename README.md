# L/S SMID-Cap Factor Portfolio

A systematic long/short equity portfolio targeting US SMID-cap stocks ($500M–$10B market cap), built on a 5-factor composite model using Refinitiv and OpenBB as data sources.

---

## Strategy at a Glance

| Parameter | Value |
|---|---|
| Universe | US SMID-cap (S&P 400 + Russell 2000 overlap) |
| Structure | ~40 longs / ~40 shorts |
| Neutrality | Dollar-neutral + beta-neutral |
| Gross exposure | ~200% |
| Rebalance | Monthly (3rd Friday) |
| Target net return | 12–18% annualised |
| Target Sharpe | > 1.0 |
| Max drawdown limit | < 15% |

---

## Factor Model

5 factors, z-scored **within GICS sector** to remove sector bias, then combined:

| Factor | Weight | Key Metrics | Primary Source |
|---|---|---|---|
| Value | 25% | EV/EBITDA, P/FCF, P/B | Refinitiv |
| Quality | 25% | ROIC, Gross Margin, Accruals Ratio | Refinitiv |
| Momentum | 20% | 12-1M price return, EPS revision | Refinitiv |
| Growth | 15% | Forward revenue & EPS growth | Refinitiv |
| Financial Health | 15% | Altman Z-Score, Net Debt/EBITDA, ICR | Refinitiv |

---

## Repo Structure

```
ls-smid-portfolio/
│
├── .env.example              ← API key template (copy to .env, never commit .env)
├── requirements.txt          ← Python dependencies
│
├── data/
│   ├── openbb_client.py      ← OpenBB initialiser (loads .env, injects all API keys)
│   ├── refinitiv_client.py   ← Refinitiv/LSEG initialiser (auto-detects Workspace or cloud)
│   ├── refinitiv_pulls.py    ← All Refinitiv factor pulls (value, quality, momentum, etc.)
│   ├── collect.py            ← Master pipeline: runs both sources, merges, saves parquet
│   ├── test_keys.py          ← Validates all API keys in .env are working
│   ├── raw/                  ← Downloaded raw files (gitignored)
│   └── processed/            ← Cleaned merged output parquet files (gitignored)
│
├── factor_model/
│   ├── factors.py            ← Factor score calculations (all 5 factors + composite)
│   ├── universe.py           ← Universe construction & filters (mktcap, liquidity, sector)
│   └── backtest.py           ← (coming) Rolling backtest & IC analysis
│
├── portfolio/
│   ├── construction.py       ← Long/short selection, beta-neutral weighting
│   ├── risk.py               ← (coming) Risk checks, VaR, concentration limits
│   └── rebalance.py          ← (coming) Monthly rebalance logic
│
├── research/
│   └── notes/                ← Analysis notebooks & memos
│
└── docs/
    └── LS_Portfolio_Workflow.docx   ← Full 9-phase build plan (start here)
```

---

## Quickstart

### 1. Clone and install
```bash
git clone https://github.com/lil399plant/ls-smid-portfolio.git
cd ls-smid-portfolio
pip install -r requirements.txt
```

### 2. Set up API keys
```bash
cp .env.example .env
open .env   # fill in your own keys
```
See `.env.example` for the full list of providers and where to get each key.

### 3. Open Refinitiv Workspace
Refinitiv connects automatically when **LSEG Workspace** is open and logged in on your machine. No extra config needed — just have it running in the background.

### 4. Run the data pipeline
```bash
python3 -m data.collect
```
Pulls from both Refinitiv and OpenBB, merges them, saves to `data/processed/master.parquet`.

### 5. Test your API keys
```bash
python3 data/test_keys.py
```

---

## Data Sources

### Refinitiv / LSEG Workspace
The primary source for all factor data. Connects via the desktop app (no key needed if Workspace is open) or via `LSEG_APP_KEY` in `.env` for cloud access.

Covers: fundamentals, estimates, earnings revisions, short interest, ownership, ESG, price history (point-in-time).

### OpenBB
Fills the gaps Refinitiv doesn't cover well:

| Provider | What it adds |
|---|---|
| FRED | Yield curve, credit spreads, macro regime signals |
| BLS | CPI, PPI, employment data |
| EIA | Oil & energy prices |
| CFTC | Commitments of Traders positioning |
| Congress.gov | Congressional trading signals |
| Benzinga | News sentiment, earnings calendars |
| CoinGecko / CoinDesk | Crypto market data |
| Polygon / Alpha Vantage | Price history fallback |

---

## Full Workflow

See [`docs/LS_Portfolio_Workflow.docx`](docs/LS_Portfolio_Workflow.docx) for the complete 9-phase plan covering everything from universe construction through live implementation.
