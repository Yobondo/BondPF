"""
BondPF - personal stock portfolio tracker by Bondalius Bond.
Pulls live prices, calculates gains/losses, flags over/undervalued
stocks, and emails alerts on big daily dips.
"""

import os
import json                             # reads/writes the portfolio file
import smtplib                          # talks to mail servers
from datetime import date               # today's date for the history log
from email.message import EmailMessage  # builds the email itself

import requests                           # sends the Telegram message
import yfinance as yf
import matplotlib
matplotlib.use("Agg")   # draw charts to a file, no window needed
import matplotlib.pyplot as plt

# Folder this script lives in. Used so the script finds .env and
# saves the chart correctly no matter where it's run from (a
# scheduled 9AM run starts in a different folder than your terminal).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# The "memory" folder is a plain folder of markdown files, one per
# stock. Open it in Obsidian ("Open folder as vault") to browse it.
# The script writes a dated line here each run and reads it back so
# the brief remembers what it concluded on previous days.
MEMORY_DIR = os.path.join(SCRIPT_DIR, "memory")

# ---------------------------------------------------------------
# CONFIG - the only part of the file you edit day to day
# ---------------------------------------------------------------

# The real portfolio lives in my_portfolio.py, which is gitignored
# so it never appears on GitHub. If that file doesn't exist (e.g.
# you just cloned this repo), the example portfolio below is used
# so the script runs out of the box.
#
# Each ticker maps to a dict holding how many shares you own and
# your average cost per share (what you paid on average).
# MORNINGSTAR_FV holds manual fair value estimates that override
# the auto-pulled analyst targets.
PORTFOLIO_JSON = os.path.join(SCRIPT_DIR, "my_portfolio.json")


def load_portfolio():
    """Read holdings + fair values. Prefers my_portfolio.json (the bot
    can edit it safely), falls back to the old my_portfolio.py, then to
    a built-in example so the repo runs for anyone who clones it."""
    if os.path.exists(PORTFOLIO_JSON):
        with open(PORTFOLIO_JSON) as f:
            data = json.load(f)
        return data.get("holdings", {}), data.get("fair_values", {})
    try:
        from my_portfolio import HOLDINGS as H, MORNINGSTAR_FV as F
        return dict(H), dict(F)
    except ImportError:
        example = {
            "AAPL": {"shares": 10, "avg_cost": 150.00},
            "NVDA": {"shares": 5,  "avg_cost": 410.00},
            "SPY":  {"shares": 2,  "avg_cost": 480.00},
        }
        return example, {}


def save_portfolio():
    """Write the current holdings + fair values back to my_portfolio.json."""
    with open(PORTFOLIO_JSON, "w") as f:
        json.dump({"holdings": HOLDINGS, "fair_values": MORNINGSTAR_FV},
                  f, indent=2)


def reload_holdings():
    """Re-read the portfolio file into the module globals (used after the
    bot records a trade, so the next analysis sees the change)."""
    global HOLDINGS, MORNINGSTAR_FV
    HOLDINGS, MORNINGSTAR_FV = load_portfolio()


def apply_trade(action, symbol, shares, price):
    """Apply a confirmed buy or sell to HOLDINGS, save, and return a
    short summary line. Averages in on a buy, removes on a full sell."""
    symbol = symbol.upper()
    if action == "buy":
        if symbol in HOLDINGS:
            old = HOLDINGS[symbol]
            total = old["shares"] + shares
            cost = old["shares"] * old["avg_cost"] + shares * price
            HOLDINGS[symbol] = {"shares": total, "avg_cost": cost / total}
        else:
            HOLDINGS[symbol] = {"shares": shares, "avg_cost": price}
        summary = f"BOUGHT {shares} {symbol} at ${price:.2f}"
    else:  # sell
        if symbol not in HOLDINGS:
            return f"You don't hold {symbol}, nothing to sell."
        held = HOLDINGS[symbol]["shares"]
        if shares >= held - 1e-9:
            del HOLDINGS[symbol]            # sold the whole position
            summary = f"SOLD entire {symbol} position ({held} shares) at ${price:.2f}"
        else:
            HOLDINGS[symbol]["shares"] = held - shares
            summary = f"SOLD {shares} {symbol} at ${price:.2f}"
    save_portfolio()
    log_event(symbol, summary)              # pin it to the memory vault
    return summary


HOLDINGS, MORNINGSTAR_FV = load_portfolio()

# Email alert thresholds: percent drop vs yesterday's close.
DIP_ALERT_PCT = 5.0       # "heads up" alert
BIG_DIP_ALERT_PCT = 10.0  # "wake up" alert


# ---------------------------------------------------------------
# PRICE FETCHING
# ---------------------------------------------------------------

def fetch_market_data(tickers):
    """Pull current price, previous close, and analyst target for
    each ticker. Returns a dict of dicts, one entry per ticker."""
    data = {}
    for symbol in tickers:
        ticker = yf.Ticker(symbol)      # object representing one stock
        info = ticker.info               # big dict of everything Yahoo knows
        data[symbol] = {
            "price": info.get("regularMarketPrice"),
            "prev_close": info.get("regularMarketPreviousClose"),
            "analyst_target": info.get("targetMeanPrice"),  # avg analyst 12-mo target
            "headline": get_top_headline(ticker),
        }
    return data


def get_top_headline(ticker):
    """Return the title of the most recent news story for a stock,
    or an empty string if there's no news (or the fetch fails)."""
    try:
        news = ticker.news        # a list of stories, each a dict
        if not news:              # empty list = no news
            return ""
        first_story = news[0]     # [0] is the first/most recent item
        # yfinance nests the title under "content" in newer versions,
        # but older versions put it at the top level. Handle both.
        content = first_story.get("content", first_story)
        return content.get("title", "")
    except Exception:
        # News is a "nice to have" - never let it crash the whole run.
        return ""


# ---------------------------------------------------------------
# PORTFOLIO MATH
# ---------------------------------------------------------------

def analyze_position(symbol, holding, market):
    """Combine one holding with its market data and compute
    everything we want to know about it. Returns a dict."""
    shares = holding["shares"]
    avg_cost = holding["avg_cost"]
    price = market["price"]
    prev_close = market["prev_close"]

    cost_basis = shares * avg_cost          # what you paid in total
    market_value = shares * price           # what it's worth right now
    gain = market_value - cost_basis        # dollars up or down
    gain_pct = (gain / cost_basis) * 100    # same thing as a percent

    # Day change: how far the price moved vs yesterday's close.
    # This drives the 5% / 10% dip alerts.
    day_change_pct = ((price - prev_close) / prev_close) * 100

    # Fair value: manual Morningstar number wins if you entered one,
    # otherwise fall back to the analyst target (which can be None).
    fair_value = MORNINGSTAR_FV.get(symbol, market["analyst_target"])

    if fair_value:  # None and 0 are both "falsy", so this skips them
        premium_pct = ((price - fair_value) / fair_value) * 100
        if premium_pct > 10:
            valuation = "OVERVALUED"
        elif premium_pct < -10:
            valuation = "UNDERVALUED"
        else:
            valuation = "FAIR"
    else:
        premium_pct = None
        valuation = "N/A"

    return {
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "market_value": market_value,
        "gain": gain,
        "gain_pct": gain_pct,
        "day_change_pct": day_change_pct,
        "fair_value": fair_value,
        "valuation": valuation,
        "headline": market.get("headline", ""),
    }


def analyze_portfolio():
    """Fetch market data and analyze every holding.
    Returns a list of position dicts."""
    market = fetch_market_data(HOLDINGS.keys())
    positions = []
    for symbol, holding in HOLDINGS.items():
        positions.append(analyze_position(symbol, holding, market[symbol]))
    return positions


# ---------------------------------------------------------------
# SUMMARY TABLE
# ---------------------------------------------------------------

def print_summary(positions):
    """Print the portfolio as a clean aligned table plus totals."""
    # Header row. Each {...:>8} means "right-align in 8 characters"
    # so every column lines up no matter how long the numbers are.
    print()
    print(f"{'TICKER':<7}{'SHARES':>8}{'BUY $':>9}{'PRICE':>9}"
          f"{'VALUE':>10}{'GAIN $':>10}{'GAIN %':>9}{'DAY %':>8}"
          f"{'FAIR $':>9}  {'FLAG'}")
    print("-" * 92)

    for p in positions:
        # Fair value can be None (ETFs like QQQ), so format it separately.
        fair = f"{p['fair_value']:.2f}" if p['fair_value'] else "--"
        buy = HOLDINGS[p['symbol']]['avg_cost']
        print(f"{p['symbol']:<7}{p['shares']:>8g}{buy:>9.2f}{p['price']:>9.2f}"
              f"{p['market_value']:>10.2f}{p['gain']:>+10.2f}{p['gain_pct']:>+9.1f}"
              f"{p['day_change_pct']:>+8.2f}{fair:>9}  {p['valuation']}")
        # Show the headline on its own indented line if we have one.
        if p["headline"]:
            print(f"        📰 {p['headline']}")

    # Portfolio totals: sum() adds up one field across all positions.
    total_value = sum(p["market_value"] for p in positions)
    total_gain = sum(p["gain"] for p in positions)
    total_cost = total_value - total_gain
    total_pct = (total_gain / total_cost) * 100
    print("-" * 92)
    print(f"{'TOTAL':<7}{'':>8}{'':>9}{'':>9}{total_value:>10.2f}"
          f"{total_gain:>+10.2f}{total_pct:>+9.1f}")
    print()


# ---------------------------------------------------------------
# BAR CHART
# ---------------------------------------------------------------

def make_chart(positions, filename="bondpf_chart.png"):
    """Save a bar chart of gain/loss per position as a PNG."""
    symbols = [p["symbol"] for p in positions]
    gains = [p["gain"] for p in positions]
    # Green bar if up, red if down. One color per bar.
    colors = ["#2e7d32" if g >= 0 else "#c62828" for g in gains]

    plt.figure(figsize=(10, 5))
    plt.bar(symbols, gains, color=colors)
    plt.axhline(0, color="black", linewidth=0.8)  # zero line
    plt.title("BondPF - Gain/Loss per Position ($)")
    plt.ylabel("Gain / Loss ($)")
    plt.tight_layout()
    plt.savefig(filename, dpi=150)
    plt.close()
    print(f"Chart saved to {filename}")


# ---------------------------------------------------------------
# HISTORY TRACKING
# ---------------------------------------------------------------

def record_history(positions):
    """Append today's portfolio total to history.csv.
    Creates the file with a header if it doesn't exist yet.
    Skips writing if today's date is already the last row
    (so re-running the script the same day doesn't double-log)."""
    path = os.path.join(SCRIPT_DIR, "history.csv")
    today = date.today().isoformat()  # YYYY-MM-DD
    total_value = sum(p["market_value"] for p in positions)

    # If the file exists, peek at the last row to see if we
    # already logged today. No point writing twice.
    if os.path.exists(path):
        with open(path) as f:
            lines = f.readlines()
        if len(lines) > 1:              # header + at least one data row
            last_date = lines[-1].strip().split(",")[0]
            if last_date == today:
                print(f"History already has an entry for {today} - skipping.")
                return
    else:
        # Brand new file: write the header first.
        with open(path, "w") as f:
            f.write("date,total_value\n")

    with open(path, "a") as f:
        f.write(f"{today},{total_value:.2f}\n")
    print(f"History updated: {today} → ${total_value:,.2f}")


def make_history_chart():
    """Read history.csv and save a line chart of total value
    over time as history_chart.png."""
    path = os.path.join(SCRIPT_DIR, "history.csv")
    chart_path = os.path.join(SCRIPT_DIR, "history_chart.png")

    if not os.path.exists(path):
        print("No history.csv yet - skipping history chart.")
        return

    dates = []
    values = []
    with open(path) as f:
        next(f)  # skip the header row
        for line in f:
            line = line.strip()
            if not line:
                continue
            d, v = line.split(",")
            dates.append(d)
            values.append(float(v))

    if not dates:
        print("history.csv has no data rows yet - skipping history chart.")
        return

    plt.figure(figsize=(10, 5))
    plt.plot(dates, values, marker="o", color="#1565c0")
    plt.title("BondPF - Portfolio Value Over Time")
    plt.ylabel("Total Value ($)")
    plt.xlabel("Date")
    plt.xticks(rotation=45, ha="right")  # tilt dates so they don't overlap
    plt.tight_layout()
    plt.savefig(chart_path, dpi=150)
    plt.close()
    print(f"History chart saved to {chart_path}")


# ---------------------------------------------------------------
# EMAIL ALERTS
# ---------------------------------------------------------------

def load_secret(key):
    """Read one KEY="value" line from the .env file next to this
    script. Keeps secrets out of the code (and off GitHub)."""
    env_path = os.path.join(SCRIPT_DIR, ".env")
    try:
        with open(env_path) as f:
            for line in f:
                if line.startswith(key + "="):
                    # split into KEY and value, strip quotes/newline
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        return None
    return None


def send_email(subject, body):
    """Send an email to yourself through Gmail's mail server."""
    address = load_secret("BONDPF_EMAIL")
    password = load_secret("GMAIL_APP_PASSWORD")
    if not address or not password:
        print("BONDPF_EMAIL or GMAIL_APP_PASSWORD missing from .env - skipping email.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content(body)

    # Connect to Gmail's server over an encrypted connection (SSL),
    # log in with the app password, send, and disconnect.
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(address, password)
        server.send_message(msg)
    print(f"Alert email sent: {subject}")


def check_alerts(positions):
    """Email if any holding dropped past the alert thresholds today."""
    big_dips = [p for p in positions if p["day_change_pct"] <= -BIG_DIP_ALERT_PCT]
    dips = [p for p in positions
            if -BIG_DIP_ALERT_PCT < p["day_change_pct"] <= -DIP_ALERT_PCT]

    if not dips and not big_dips:
        return  # calm day, no email

    lines = []
    for p in big_dips:
        lines.append(f"🚨 {p['symbol']} is DOWN {abs(p['day_change_pct']):.1f}% today "
                     f"(${p['price']:.2f})")
    for p in dips:
        lines.append(f"⚠️ {p['symbol']} is down {abs(p['day_change_pct']):.1f}% today "
                     f"(${p['price']:.2f})")

    header = ("🚨 BondPF: big dip alert" if big_dips
              else "⚠️ BondPF: dip alert")
    send_telegram(header + "\n" + "\n".join(lines))


# ---------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------

def send_telegram(text):
    """Send a message to yourself on Telegram. Needs a bot token and
    your chat id in .env. Skips quietly if either is missing."""
    token = load_secret("TELEGRAM_BOT_TOKEN")
    chat_id = load_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from .env - skipping Telegram.")
        return

    # Telegram's whole API is just web requests. We POST the message
    # to the bot's sendMessage endpoint.
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code == 200:
            print("Telegram message sent.")
        else:
            print(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Telegram send failed: {e}")


# ---------------------------------------------------------------
# MORNING BRIEF (structured facts + AI synthesis)
# ---------------------------------------------------------------

def weekly_trend():
    """Look at history.csv and say whether the portfolio is trending
    up, down, or flat over roughly the last week. Returns text or None."""
    path = os.path.join(SCRIPT_DIR, "history.csv")
    if not os.path.exists(path):
        return None
    values = []
    with open(path) as f:
        next(f, None)                 # skip the header row
        for line in f:
            parts = line.strip().split(",")
            try:
                values.append(float(parts[1]))
            except (IndexError, ValueError):
                continue
    if len(values) < 2:
        return None
    now = values[-1]
    # Compare to ~5 entries ago (a trading week), or the oldest we have.
    past = values[-6] if len(values) >= 6 else values[0]
    if now > past * 1.001:
        return "trending up ↗"
    if now < past * 0.999:
        return "trending down ↘"
    return "roughly flat →"


def build_facts(positions):
    """Assemble the structured, non-AI part of the brief straight from
    our own numbers. Returns a formatted string."""
    total_value = sum(p["market_value"] for p in positions)
    total_gain = sum(p["gain"] for p in positions)
    total_cost = total_value - total_gain
    total_pct = (total_gain / total_cost * 100) if total_cost else 0

    # Portfolio-level day move: rebuild yesterday's value from each
    # position's day change, then compare.
    prev_value = sum(p["market_value"] / (1 + p["day_change_pct"] / 100)
                     for p in positions)
    day_pct = ((total_value - prev_value) / prev_value * 100) if prev_value else 0

    today = date.today().strftime("%a %b %d")
    out = [f"📊 BondPF Morning Brief — {today}", ""]

    # --- SNAPSHOT ---
    arrow = "▲" if day_pct >= 0 else "▼"
    out.append("💰 SNAPSHOT")
    out.append(f"Total value: ${total_value:,.0f} ({arrow}{abs(day_pct):.1f}% today)")
    out.append(f"Overall P/L: {total_gain:+,.0f} ({total_pct:+.1f}%)")
    trend = weekly_trend()
    if trend:
        out.append(f"This week: {trend}")
    out.append("")

    # --- TODAY'S MOVERS --- (biggest gainers and losers by day move)
    ranked = sorted(positions, key=lambda p: p["day_change_pct"], reverse=True)
    gainers = [p for p in ranked if p["day_change_pct"] > 0][:3]
    losers = [p for p in reversed(ranked) if p["day_change_pct"] < 0][:3]
    out.append("🔺 TODAY'S MOVERS")
    for p in gainers + losers:
        a = "▲" if p["day_change_pct"] >= 0 else "▼"
        row = f"• {p['symbol']} {a}{abs(p['day_change_pct']):.1f}%"
        if p["headline"]:
            row += f" — {p['headline']}"
        out.append(row)
    out.append("")

    # --- VALUATION WATCH ---
    under = [p["symbol"] for p in positions if p["valuation"] == "UNDERVALUED"]
    over = [p["symbol"] for p in positions if p["valuation"] == "OVERVALUED"]
    out.append("🎯 VALUATION WATCH")
    out.append(f"Undervalued: {', '.join(under) if under else 'none'}")
    out.append(f"Overvalued: {', '.join(over) if over else 'none'}")
    # Biggest gap below fair value (most undervalued name).
    gaps = [((p["price"] - p["fair_value"]) / p["fair_value"] * 100, p["symbol"])
            for p in positions if p["fair_value"]]
    if gaps:
        gap, sym = min(gaps)               # most negative = most undervalued
        if gap < 0:
            out.append(f"Biggest gap: {sym} ~{abs(gap):.0f}% below target")
    out.append("")

    # --- DATA GAPS --- holdings with no trusted Morningstar fair value,
    # so the flags above lean on weak Tier-4 data. Prompt to upgrade.
    starved = [p["symbol"] for p in positions
               if p["symbol"] not in MORNINGSTAR_FV]
    if starved:
        out.append("📋 DATA GAPS (Tier-4 data only, no Morningstar FV)")
        out.append(", ".join(starved))
        out.append("Text  /research TICKER  to upgrade one.")
        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------
# MEMORY (markdown vault - one file per stock, Obsidian-friendly)
# ---------------------------------------------------------------

# Each stock's markdown file has two sections: KEY EVENTS (pinned facts
# like buys/sells/thesis changes, ALWAYS read) and DAILY LOG (one line
# per day, only the recent slice is read). You can hand-edit Key Events
# right in Obsidian and the brief will pick it up.
FUNDAMENTALS_HEADER = "## Fundamentals"
KEY_EVENTS_HEADER = "## Key Events"
DAILY_LOG_HEADER = "## Daily Log"


def _ensure_memory_file(path, symbol):
    """Create the file, or migrate an old flat file to the two-section
    format, so both Key Events and Daily Log always exist."""
    template = (
        f"# {symbol}\n\n"
        f"{KEY_EVENTS_HEADER}\n"
        "<!-- Pinned facts: buys, sells, thesis changes. Always remembered. "
        "Edit freely in Obsidian. -->\n\n"
        f"{DAILY_LOG_HEADER}\n"
    )
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(template)
        return
    with open(path) as f:
        content = f.read()
    if DAILY_LOG_HEADER not in content:
        # Old flat format: keep its dated bullets, move them under Daily Log.
        bullets = [ln for ln in content.splitlines() if ln.startswith("- ")]
        with open(path, "w") as f:
            f.write(template)
            f.write("\n".join(bullets) + ("\n" if bullets else ""))


def record_memory(positions):
    """Append today's one-line snapshot to each stock's Daily Log."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    today = date.today().isoformat()
    for p in positions:
        path = os.path.join(MEMORY_DIR, f"{p['symbol']}.md")
        _ensure_memory_file(path, p["symbol"])
        with open(path) as f:
            if f"- {today} |" in f.read():
                continue   # already logged today, don't duplicate
        line = (f"- {today} | ${p['price']:.2f} ({p['day_change_pct']:+.1f}%) "
                f"| overall {p['gain_pct']:+.1f}% | {p['valuation']}")
        if p["headline"]:
            line += f" | {p['headline']}"
        with open(path, "a") as f:   # Daily Log is the last section, so append
            f.write(line + "\n")


def log_event(symbol, text):
    """Pin a permanent event (a sale, a thesis change) to a stock's
    Key Events section so it is remembered forever, not just 10 days."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = os.path.join(MEMORY_DIR, f"{symbol}.md")
    _ensure_memory_file(path, symbol)
    event = f"- {date.today().isoformat()} | {text}\n"
    with open(path) as f:
        lines = f.readlines()
    out, inserted = [], False
    for ln in lines:
        out.append(ln)
        if ln.strip() == KEY_EVENTS_HEADER and not inserted:
            out.append(event)          # insert right under the header
            inserted = True
    with open(path, "w") as f:
        f.writelines(out if inserted else lines + [event])
    print(f"Logged key event for {symbol}: {text}")


def write_fundamentals(symbol, line):
    """Create or REPLACE the single-line Fundamentals section in a stock's
    note (FV, moat, uncertainty). One updatable line, never a growing log."""
    import re
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = os.path.join(MEMORY_DIR, f"{symbol.upper()}.md")
    _ensure_memory_file(path, symbol.upper())
    with open(path) as f:
        content = f.read()
    section = f"{FUNDAMENTALS_HEADER}\n{line}\n"
    if FUNDAMENTALS_HEADER in content:
        # replace the old fundamentals section, up to the next "## " header
        content = re.sub(rf"{re.escape(FUNDAMENTALS_HEADER)}.*?(?=\n## |\Z)",
                         section.rstrip(), content, count=1, flags=re.S)
    else:
        # insert right after the "# TITLE" line
        lines = content.split("\n")
        at = next((i + 1 for i, ln in enumerate(lines)
                   if ln.startswith("# ")), 1)
        lines.insert(at, "\n" + section.rstrip())
        content = "\n".join(lines)
    with open(path, "w") as f:
        f.write(content)


def memory_digest(symbols, days=10):
    """Return each stock's Fundamentals, pinned Key Events, and the last
    `days` Daily Log entries, so the brief has full context."""
    blocks = []
    for symbol in symbols:
        path = os.path.join(MEMORY_DIR, f"{symbol}.md")
        if not os.path.exists(path):
            continue
        fund, events, daily, section = [], [], [], None
        with open(path) as f:
            for ln in f:
                s = ln.strip()
                if s == FUNDAMENTALS_HEADER:
                    section = "fund"; continue
                if s == KEY_EVENTS_HEADER:
                    section = "events"; continue
                if s == DAILY_LOG_HEADER:
                    section = "daily"; continue
                if not s or s.startswith("<!--"):
                    continue
                if section == "fund":
                    fund.append(s)
                elif s.startswith("- ") and section == "events":
                    events.append(s)
                elif s.startswith("- ") and section == "daily":
                    daily.append(s)
        parts = []
        if fund:
            parts.append("Fundamentals: " + " ".join(fund))
        if events:
            parts.append("Key events: " + "; ".join(e[2:] for e in events))
        if daily:
            parts.append("Recent:\n" + "\n".join(daily[-days:]))
        if parts:
            blocks.append(f"{symbol}:\n" + "\n".join(parts))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------
# COST TRACKING
# ---------------------------------------------------------------

# Rough per-MILLION-token prices in USD (input, output). These power
# the at-a-glance estimate only - VERIFY them on the Anthropic pricing
# page, and treat the console Usage page as the real spend number.
PRICES = {
    "claude-opus-5":              (15.00, 75.00),
    "claude-sonnet-5":            (3.00, 15.00),
    "claude-haiku-4-5-20251001":  (1.00, 5.00),
}


def log_cost(model, usage):
    """Log the token count and estimated cost of one Claude call to
    cost_log.csv, and return the estimated dollars."""
    in_tok = getattr(usage, "input_tokens", 0)
    out_tok = getattr(usage, "output_tokens", 0)
    in_price, out_price = PRICES.get(model, (0, 0))
    cost = in_tok / 1_000_000 * in_price + out_tok / 1_000_000 * out_price
    path = os.path.join(SCRIPT_DIR, "cost_log.csv")
    new_file = not os.path.exists(path)
    with open(path, "a") as f:
        if new_file:
            f.write("date,model,input_tokens,output_tokens,est_cost_usd\n")
        f.write(f"{date.today().isoformat()},{model},{in_tok},{out_tok},"
                f"{cost:.5f}\n")
    return cost


def cost_summary():
    """Return a short text summary of total estimated spend so far."""
    path = os.path.join(SCRIPT_DIR, "cost_log.csv")
    if not os.path.exists(path):
        return "No cost logged yet."
    total, calls, today_total = 0.0, 0, 0.0
    today = date.today().isoformat()
    with open(path) as f:
        next(f, None)   # skip header
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 5:
                continue
            try:
                c = float(parts[4])
            except ValueError:
                continue
            total += c
            calls += 1
            if parts[0] == today:
                today_total += c
    return (f"Estimated spend: ${total:.3f} total across {calls} calls "
            f"(${today_total:.3f} today). Console has the exact number.")


def ai_synthesis(facts, history=""):
    """Give Claude the day's facts (and recent memory) and ask for the
    closing 'take' and a thing to watch. Returns text, or "" if no key."""
    api_key = load_secret("ANTHROPIC_API_KEY")
    if not api_key:
        print("No ANTHROPIC_API_KEY in .env - skipping AI synthesis.")
        return ""
    try:
        import anthropic
    except ImportError:
        print("anthropic library not installed - run: pip3 install anthropic")
        return ""

    # Load your investment strategy (if present) to use as the AI's
    # system prompt, so the brief reasons in your framework and voice.
    strategy = ""
    strategy_path = os.path.join(SCRIPT_DIR, "strategy.md")
    if os.path.exists(strategy_path):
        with open(strategy_path) as f:
            strategy = f.read()

    history_block = ""
    if history:
        history_block = (
            "Here is the recent history of these positions (your own notes "
            "from previous days, oldest to newest). Use it to judge whether "
            "today is a change or a continuation, and call out anything that "
            "has been drifting:\n\n"
            f"{history}\n\n"
        )

    prompt = (
        "Here are today's portfolio facts:\n\n"
        f"{facts}\n\n"
        f"{history_block}"
        "Write exactly two short sections in plain text, applying the strategy "
        "you were given (framework, flags, tone). Flag when a call would need a "
        "Morningstar PDF you don't have.\n\n"
        "🧠 THE TAKE\n"
        "2-4 sentences: what actually mattered today and why, in your framework. "
        "Company-specific vs sector noise. Raise any risk flags that apply "
        "(concentration, portfolio beta, binary events, unprotected gains).\n\n"
        "👀 ONE THING TO WATCH\n"
        "A single forward-looking sentence.\n\n"
        "Use those exact emoji headers. Keep it tight."
    )

    client = anthropic.Anthropic(api_key=api_key)
    model = "claude-opus-5"
    message = client.messages.create(
        model=model,
        max_tokens=600,
        system=strategy if strategy else anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": prompt}],
    )
    cost = log_cost(model, message.usage)
    print(f"Brief AI cost: ~${cost:.4f}")
    # The reply is a list of blocks; newer models can include a
    # "thinking" block first, so grab only the ones that carry text.
    text_parts = [block.text for block in message.content
                  if getattr(block, "type", None) == "text"]
    return "\n".join(text_parts).strip()


def morning_brief(positions):
    """Build the full brief (facts + memory-aware AI synthesis)."""
    facts = build_facts(positions)
    # Read back recent history so Jeffrey has continuity, then reason.
    history = memory_digest([p["symbol"] for p in positions], days=10)
    take = ai_synthesis(facts, history)
    return f"{facts}\n{take}" if take else facts


# ---------------------------------------------------------------
# MAIN - runs only when you execute this file directly
# ---------------------------------------------------------------

if __name__ == "__main__":
    positions = analyze_portfolio()
    print_summary(positions)
    record_history(positions)
    make_chart(positions, os.path.join(SCRIPT_DIR, "bondpf_chart.png"))
    make_history_chart()

    # Build the structured morning brief and send it to Telegram.
    # (The old separate dip alert is retired - the brief's TODAY'S
    # MOVERS section already covers big drops. check_alerts() is kept
    # in the file as a backup but no longer called.)
    brief = morning_brief(positions)
    print("\n" + brief)
    send_telegram(brief)

    # Record today's snapshot to the memory vault AFTER the brief, so
    # today's entry doesn't get read back into today's own analysis.
    record_memory(positions)

