# BondPF 📈

A personal stock portfolio tracker that runs automatically every weekday morning. Built in Python as my first production project.

## What it does

- **Live market data** — pulls current prices for every holding via the [yfinance](https://github.com/ranaroussi/yfinance) API
- **P/L tracking** — calculates gain/loss per position and for the whole portfolio, in dollars and percent
- **Valuation flags** — compares each stock's price to analyst mean price targets (with optional manual Morningstar fair value overrides) and flags positions as `OVERVALUED`, `UNDERVALUED`, or `FAIR`
- **Dip alerts** — emails me automatically when any holding drops 5%+ in a day, with an urgent alert at 10%+
- **Daily summary table** — clean aligned terminal report on every run
- **Gain/loss chart** — matplotlib bar chart saved as PNG, green for winners, red for losers
- **Scheduled** — runs on its own every weekday at 9:00 AM

## Sample output

```
TICKER  SHARES    BUY $    PRICE     VALUE    GAIN $   GAIN %   DAY %   FAIR $  FLAG
--------------------------------------------------------------------------------------------
AAPL        10   150.00   205.50   2055.00   +555.00    +37.0   -0.43   228.14  UNDERVALUED
NVDA         5   410.00   118.20    591.00  -1459.00    -71.2   -2.77   135.00  UNDERVALUED
SPY          2   480.00   560.10   1120.20   +160.20    +16.7   +0.32       --  N/A
--------------------------------------------------------------------------------------------
TOTAL                               3766.20   -743.80    -16.5
```

*(Example data — the real config holds my actual positions locally.)*

## How it works

Everything lives in one file, `bondpf.py`, organized in sections:

1. **Config** — holdings, manual fair value overrides, and alert thresholds at the top; logic below never needs touching for portfolio changes
2. **Price fetching** — one yfinance call per ticker, defensively coded (`.get()`) because market data is messy (ETFs have no analyst targets, for example)
3. **Portfolio math** — cost basis, market value, gain, day change, and valuation banding (±10% dead zone around fair value)
4. **Display** — f-string formatted summary table and a matplotlib chart using the `Agg` backend so it renders with no display attached (needed for scheduled runs)
5. **Alerts** — SMTP email via Gmail, credentials loaded from an untracked `.env` file so no secrets ever touch this repo

## Setup

```bash
pip3 install yfinance matplotlib
python3 bondpf.py
```

For email alerts, create a Gmail app password and put it in a `.env` file (gitignored):

```
GMAIL_APP_PASSWORD="your app password"
```

## Roadmap

- [ ] AI-generated morning commentary on why positions moved (Claude API)
- [ ] Web dashboard deployed on Vercel
- [ ] Historical performance tracking

## Disclaimer

This is a personal learning project, not financial advice. Valuation flags are based on analyst estimates, which are frequently wrong in both directions.
