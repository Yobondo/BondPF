"""
BondPF two-way chat bot.

Run this and LEAVE IT RUNNING (laptop awake):  python3 bondpf_bot.py

Then text your Telegram bot any question ("why is SMR down?", "is AVGO
overvalued?") and Jeffrey answers using your live portfolio, your memory
vault, and your strategy. This is separate from bondpf.py, which just
sends the once-a-day morning brief.
"""

import time
import json
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


def answer(question, positions, strategy, conversation):
    """Answer using portfolio context PLUS the recent conversation, so
    follow-up questions ('what about that one?') make sense."""
    api_key = bondpf.load_secret("ANTHROPIC_API_KEY")
    if not api_key:
        return "No ANTHROPIC_API_KEY in .env."
    import anthropic

    facts = bondpf.build_facts(positions)
    history = bondpf.memory_digest([p["symbol"] for p in positions], days=10)

    # Strategy + always-fresh portfolio context go in the SYSTEM prompt.
    system = (
        f"{strategy}\n\n"
        "---\nCURRENT PORTFOLIO SNAPSHOT:\n"
        f"{facts}\n\n"
        "RECENT HISTORY AND PINNED KEY EVENTS:\n"
        f"{history}\n\n"
        "Answer as my advisor using the strategy. Be concise and direct. "
        "Flag when an answer would need a Morningstar PDF you don't have."
    )

    # The conversation list carries the recent back-and-forth. We append
    # the new question, so Claude sees the whole recent thread.
    messages = conversation + [{"role": "user", "content": question}]

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=700,
        system=system,
        messages=messages,
    )
    cost = bondpf.log_cost(CHAT_MODEL, message.usage)
    print(f"reply cost: ~${cost:.4f}")
    parts = [b.text for b in message.content
             if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip() or "(no reply)"


def parse_trade(text):
    """Use Claude to pull a structured trade out of plain English.
    Returns a dict {action, symbol, shares, price} or None if unclear."""
    api_key = bondpf.load_secret("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    import anthropic
    prompt = (
        "Extract a stock trade from this message. Reply with ONLY a JSON "
        'object: {"action":"buy" or "sell","symbol":"TICKER","shares":number,'
        '"price":number}. If any field is missing or it is not a trade, reply '
        'with exactly {"error":"unclear"}.\n\n'
        f"Message: {text}"
    )
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",   # cheap + fast for extraction
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    bondpf.log_cost("claude-haiku-4-5-20251001", msg.usage)
    raw = "".join(b.text for b in msg.content
                  if getattr(b, "type", None) == "text").strip()
    # The model may wrap the JSON in ```code fences``` or extra text, so
    # just grab the {...} object itself before parsing.
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    if data.get("error") or not all(k in data for k in
                                    ("action", "symbol", "shares", "price")):
        return None
    return data


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

    # Rolling short-term memory of the chat: a list of {role, content}.
    # We keep only the last MAX_TURNS messages so it doesn't grow forever.
    conversation = []
    MAX_TURNS = 10

    # A trade waiting for you to confirm before it's written. None = none.
    pending_trade = None

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

            if text.lower() in ("/cost", "cost"):
                bondpf.send_telegram(bondpf.cost_summary())
                continue

            if text.lower() in ("/reset", "reset"):
                conversation.clear()
                bondpf.send_telegram("Conversation cleared. Fresh start.")
                continue

            # --- Trade confirmation flow ---
            # Step 2: if a trade is pending, this message is the yes/no.
            if pending_trade is not None:
                if text.lower() in ("yes", "y", "confirm"):
                    t = pending_trade
                    result = bondpf.apply_trade(t["action"], t["symbol"],
                                                float(t["shares"]), float(t["price"]))
                    bondpf.reload_holdings()
                    positions = bondpf.analyze_portfolio()   # refresh with new book
                    bondpf.send_telegram(f"Done. {result}. Portfolio updated.")
                    pending_trade = None
                else:
                    bondpf.send_telegram("Cancelled, nothing changed.")
                    pending_trade = None
                continue

            # Step 1: a trade request. Parse it and ask you to confirm.
            if text.lower().startswith("/trade") or \
               text.lower().split(" ")[0] in ("bought", "sold"):
                trade_text = text[len("/trade"):].strip() if \
                    text.lower().startswith("/trade") else text
                parsed = parse_trade(trade_text)
                if not parsed:
                    bondpf.send_telegram(
                        "Couldn't read that trade. Try: /trade bought 5 AAPL at 250")
                    continue
                pending_trade = parsed
                bondpf.send_telegram(
                    f"Confirm: {parsed['action'].upper()} {parsed['shares']} "
                    f"{parsed['symbol'].upper()} at ${float(parsed['price']):.2f}?\n"
                    "Reply 'yes' to record it, anything else to cancel.")
                continue

            print(f"Q: {text}")
            reply = answer(text, positions, strategy, conversation)
            # Save this exchange, then trim to the last MAX_TURNS messages.
            conversation.append({"role": "user", "content": text})
            conversation.append({"role": "assistant", "content": reply})
            del conversation[:-MAX_TURNS]
            bondpf.send_telegram(reply)
        time.sleep(1)


if __name__ == "__main__":
    main()
