# L/S SMID-Cap Factor Portfolio

A systematic long/short portfolio targeting SMID-cap equities using a 5-factor composite model.

## Strategy Overview

- **Universe**: US SMID-cap stocks (market cap $500M–$10B), S&P 400 + Russell 2000 overlap
- **Structure**: ~40 longs / ~40 shorts, dollar-neutral + beta-neutral
- **Rebalance**: Monthly (3rd Friday), quarterly full reconstruction
- **Target gross exposure**: ~200%, net ~0%
- **Target return**: 12–18% net annualised | Sharpe > 1.0 | Max drawdown < 15%

## Factor Model (5-Factor Composite)

| Factor | Weight | Key Metrics |
|--------|--------|-------------|
| Value | 25% | EV/EBITDA, P/FCF, P/B |
| Quality | 25% | ROIC, Gross Margin, Accruals Ratio |
| Momentum | 20% | 12-1M price return, earnings revision |
| Growth | 15% | Revenue & EPS 3Y CAGR |
| Financial Health | 15% | Altman Z-Score, Net Debt/EBITDA, ICR |

Scores are z-scored **within GICS sector** to remove sector bias.

## Repo Structure

```
ls-smid-portfolio/
├── docs/
│   └── LS_Portfolio_Workflow.docx   # Full 9-phase workflow
├── data/
│   ├── raw/                         # Downloaded fundamentals, prices
│   └── processed/                   # Cleaned, merged datasets
├── factor_model/
│   ├── universe.py                  # Universe construction & filters
│   ├── factors.py                   # Factor calculation (Value/Quality/Mom/Growth/Health)
│   ├── composite.py                 # Z-score, weight, combine → final score
│   └── backtest.py                  # Rolling backtest & IC analysis
├── portfolio/
│   ├── construction.py              # Long/short selection, beta-neutralisation
│   ├── risk.py                      # Risk checks, VaR, concentration limits
│   └── rebalance.py                 # Monthly rebalance logic
└── research/
    └── notes/                       # Analysis notebooks & memos
```

## Full Workflow

See [`docs/LS_Portfolio_Workflow.docx`](docs/LS_Portfolio_Workflow.docx) for the complete 9-phase build plan:

1. Investment thesis & edge definition
2. Universe construction & data sourcing
3. Factor research & signal design
4. Composite score construction
5. Portfolio construction & optimisation
6. Risk management framework
7. Backtesting & performance attribution
8. Live implementation & monitoring
9. Iteration & continuous improvement

## Data Sources

- **Primary**: OpenBB (equity fundamentals, prices, macro)
- **Secondary**: Refinitiv Eikon (point-in-time data, short interest)
- **Alternative**: FRED (macro regime signals)

## Getting Started

```bash
git clone https://github.com/<your-username>/ls-smid-portfolio.git
cd ls-smid-portfolio
pip install -r requirements.txt
```

## Contributors

- Lead: lileggplant
- Cofounder: TBD
