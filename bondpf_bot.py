"""
BondPF two-way chat bot.

Run this and LEAVE IT RUNNING (laptop awake):  python3 bondpf_bot.py

Then text your Telegram bot any question ("why is SMR down?", "is AVGO
overvalued?") and Jeffrey answers using your live portfolio, your memory
vault, and your strategy. This is separate from bondpf.py, which just
sends the once-a-day morning brief.
"""

import time
import requests

import bondpf   # reuse everything: secrets, portfolio, memory, strategy

# Model for chat replies. Sonnet is fast and cheap for back-and-forth;
# bump to "claude-opus-5" if you want the deepest answers (costs more).
CHAT_MODEL = "claude-sonnet-5"


def load_strategy():
    """Read strategy.md so the bot answers in Jeffrey's framework/voice."""
    import os
    path = os.path.join(bondpf.SCRIPT_DIR, "strategy.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def answer(question, positions, strategy):
    """Send the question plus full context to Claude and return the reply."""
    api_key = bondpf.load_secret("ANTHROPIC_API_KEY")
    if not api_key:
        return "No ANTHROPIC_API_KEY in .env."
    import anthropic

    facts = bondpf.build_facts(positions)
    history = bondpf.memory_digest([p["symbol"] for p in positions], days=10)

    user_msg = (
        "Here is my current portfolio snapshot:\n\n"
        f"{facts}\n\n"
        "Recent history and pinned key events from my notes:\n\n"
        f"{history}\n\n"
        f"My question: {question}\n\n"
        "Answer as my advisor using the strategy you were given. Be concise "
        "and direct. Flag when an answer would need a Morningstar PDF you "
        "don't have."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=700,
        system=strategy if strategy else anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": user_msg}],
    )
    parts = [b.text for b in message.content
             if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip() or "(no reply)"


def get_updates(token, offset):
    """Long-poll Telegram for new messages. Returns a list of updates."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 30},
                         timeout=40)
        return r.json().get("result", [])
    except Exception as e:
        print(f"poll error: {e}")
        time.sleep(3)
        return []


def main():
    token = bondpf.load_secret("TELEGRAM_BOT_TOKEN")
    chat_id = str(bondpf.load_secret("TELEGRAM_CHAT_ID"))
    if not token or not chat_id:
        print("Missing Telegram secrets in .env.")
        return
    strategy = load_strategy()

    # Pull the portfolio once at startup. Text "/refresh" to re-pull prices.
    print("Fetching portfolio...")
    positions = bondpf.analyze_portfolio()
    print("BondPF bot is listening. Text your bot on Telegram. Ctrl+C to stop.")

    offset = None
    while True:
        for update in get_updates(token, offset):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            # Only respond to YOUR chat, ignore everyone else.
            if str(msg.get("chat", {}).get("id")) != chat_id:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue

            if text.lower() in ("/refresh", "refresh"):
                bondpf.send_telegram("Refreshing prices...")
                positions = bondpf.analyze_portfolio()
                bondpf.send_telegram("Done, prices updated.")
                continue

            print(f"Q: {text}")
            reply = answer(text, positions, strategy)
            bondpf.send_telegram(reply)
        time.sleep(1)


if __name__ == "__main__":
    main()
