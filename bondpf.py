"""
BondPF - personal stock portfolio tracker by Bondalius Bond.
Pulls live prices, calculates gains/losses, flags over/undervalued
stocks, and emails alerts on big daily dips.
"""

import os
import smtplib                          # talks to mail servers
from datetime import date               # today's date for the history log
from email.message import EmailMessage  # builds the email itself

import yfinance as yf
import matplotlib
matplotlib.use("Agg")   # draw charts to a file, no window needed
import matplotlib.pyplot as plt

# Folder this script lives in. Used so the script finds .env and
# saves the chart correctly no matter where it's run from (a
# scheduled 9AM run starts in a different folder than your terminal).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

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
try:
    from my_portfolio import HOLDINGS, MORNINGSTAR_FV
except ImportError:
    HOLDINGS = {
        "AAPL": {"shares": 10, "avg_cost": 150.00},
        "NVDA": {"shares": 5,  "avg_cost": 410.00},
        "SPY":  {"shares": 2,  "avg_cost": 480.00},
    }
    MORNINGSTAR_FV = {
        # "AAPL": 210.00,   <- example manual override
    }

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

    subject = ("🚨 BondPF: big dip alert" if big_dips
               else "⚠️ BondPF: dip alert")
    send_email(subject, "\n".join(lines))


# ---------------------------------------------------------------
# AI COMMENTARY (Claude API)
# ---------------------------------------------------------------

def ai_commentary(positions):
    """Ask Claude to write a short plain-English note about how the
    portfolio moved today. Returns the text, or "" if no API key."""
    api_key = load_secret("ANTHROPIC_API_KEY")
    if not api_key:
        print("No ANTHROPIC_API_KEY in .env - skipping AI commentary.")
        return ""

    # Import here (not at the top) so the whole script still runs for
    # people who haven't installed the anthropic library yet.
    try:
        import anthropic
    except ImportError:
        print("anthropic library not installed - run: pip3 install anthropic")
        return ""

    # Build a compact text summary of the portfolio to hand to Claude.
    # We only send what's useful: ticker, day move, total gain, flag,
    # and the news headline we already fetched.
    lines = []
    for p in positions:
        line = (f"{p['symbol']}: {p['day_change_pct']:+.1f}% today, "
                f"{p['gain_pct']:+.1f}% overall, {p['valuation']}")
        if p["headline"]:
            line += f" | news: {p['headline']}"
        lines.append(line)
    portfolio_text = "\n".join(lines)

    # The prompt tells Claude who it's writing for and what we want.
    prompt = (
        "You are a concise portfolio assistant. Below is today's snapshot "
        "of my stock holdings (daily move, overall gain, valuation flag, and "
        "a recent news headline where available). Write a short morning note "
        "(4-6 sentences) explaining what stands out today and why, connecting "
        "moves to the news where it fits. Be factual and calm. Do NOT give "
        "buy/sell advice. End with one thing worth watching.\n\n"
        f"{portfolio_text}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    # The reply comes back as a list of content blocks; we want the text.
    return message.content[0].text


# ---------------------------------------------------------------
# MAIN - runs only when you execute this file directly
# ---------------------------------------------------------------

if __name__ == "__main__":
    positions = analyze_portfolio()
    print_summary(positions)
    record_history(positions)
    make_chart(positions, os.path.join(SCRIPT_DIR, "bondpf_chart.png"))
    check_alerts(positions)
    make_history_chart()

    # Ask Claude for a morning note, print it, and email it to yourself.
    note = ai_commentary(positions)
    if note:
        print("\n=== AI Morning Note ===")
        print(note)
        send_email("📊 BondPF: your morning note", note)

