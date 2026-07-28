# BondPF Roadmap

## What v1 does (shipped)

- Pulls live prices for all holdings via the yfinance API
- Calculates gain/loss per position and for the whole portfolio ($ and %)
- Flags each stock OVERVALUED / UNDERVALUED / FAIR vs analyst price targets
  (with optional manual Morningstar fair value overrides)
- Emails a dip alert when any holding drops 5%+ in a day, urgent alert at 10%+
- Prints a clean aligned summary table on every run
- Saves a gain/loss bar chart as a PNG
- Runs automatically every weekday at 9:00 AM via cron

## Where to change things

- **Holdings** (add/remove stocks, share counts, cost basis, Morningstar
  fair values): edit `my_portfolio.py` (private, never on GitHub)
- **Alert sensitivity**: `DIP_ALERT_PCT` and `BIG_DIP_ALERT_PCT` at the top
  of `bondpf.py`
- **Secrets** (email address, Gmail app password): `.env` (private)

## Roadmap

### v1.5 — publish (in progress)
- [x] Split real numbers into gitignored `my_portfolio.py`
- [ ] Wipe old git history, republish repo as public
- [ ] Add project + link to LinkedIn

### v2 — AI commentary
- [ ] Each morning, send position moves to the Claude API and get back a
      short plain-English "what happened and why" note in the email
- [ ] (later, optional) pull SEC filing / risk-factor data from a provider
      like Massive to give the AI real source material

### v3 — always-on
- [ ] Deploy to a cloud server so no laptop needs to be awake
- [ ] Web dashboard (Vercel) instead of terminal + email

## Ideas parked for later
- Hard stop-loss price per stock (currently uses % day-drop alerts instead)
- Historical performance tracking over time
- Dividend income tracking
