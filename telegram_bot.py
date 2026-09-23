"""
telegram_bot.py — two-way Telegram interface for the Stockester agent.

Polls for messages, handles slash commands, and passes free-text queries
to the LangGraph agent. The scheduled 9:25 AM alert from main.py shares
the same Telegram chat but is independent of this process.

Supported commands
------------------
  /scan [SYMBOL]   — run a full agent analysis (default: NIFTY)
  /history [SYMBOL] — show last 5 predictions from SQLite
  /performance     — prediction accuracy by horizon
  /help            — list commands

Any other text is treated as a free-form query to the agent.

Run
---
  python telegram_bot.py
"""

import json
import os
import sys
import time

import requests
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

sys.path.insert(0, os.getcwd())
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

from stockester_agent.agent.graph import graph
from stockester_agent.agent.tools import clear_cache
from stockester_agent.memory import (
    get_recent_predictions,
    get_performance_summary,
    get_historical_context_data,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID"))
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

HELP_TEXT = (
    "<b>Stockester Commands</b>\n\n"
    "/scan [SYMBOL]    — run agent analysis (default: NIFTY)\n"
    "/history [SYMBOL] — last 5 predictions from memory\n"
    "/performance      — prediction accuracy by horizon\n"
    "/help             — this message\n\n"
    "<i>Any other message is sent to the agent as a free-form query.</i>"
)


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def get_updates(offset=None) -> list:
    params = {"timeout": 30, "offset": offset}
    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        print(f"[getUpdates error] {e}")
        return []


def send_message(text: str) -> None:
    """Send HTML message to the configured chat, chunked at 4096 chars."""
    for chunk in [text[i : i + 4096] for i in range(0, len(text), 4096)]:
        try:
            requests.post(
                f"{BASE_URL}/sendMessage",
                json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as e:
            print(f"[sendMessage error] {e}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_scan(symbol: str = "NIFTY") -> None:
    """Run a full agent analysis and send the formatted result."""
    symbol = symbol.upper().strip() or "NIFTY"
    send_message(f"Running analysis for <b>{symbol}</b>...")
    clear_cache()

    try:
        result = graph.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"Run a complete market analysis for {symbol}. "
                            "Start with get_market_snapshot, then decide whether "
                            "Monte Carlo or historical context would be useful."
                        )
                    )
                ],
                "symbol": symbol,
                "notify_telegram": False,   # we send manually below
                "analysis": None,
                "snapshot_id": None,
            }
        )

        analysis = result.get("analysis") or {}
        from stockester_agent.agent.schema import AnalystOutput
        output = AnalystOutput.model_validate(analysis)
        send_message(output.to_telegram_html())

    except Exception as e:
        send_message(f"Agent error: {e}")


def handle_history(symbol: str = "NIFTY") -> None:
    """Show the last 5 predictions for a symbol."""
    symbol = symbol.upper().strip() or "NIFTY"
    data = get_historical_context_data(symbol, lookback_days=90)

    if data["total_predictions"] == 0:
        send_message(f"No predictions found for {symbol} in the last 90 days.")
        return

    lines = [f"<b>Recent predictions for {symbol}</b>\n"]
    for p in data["recent"]:
        ts = (p["timestamp"] or "")[:16].replace("T", " ")
        stance = (p["stance"] or "?").capitalize()
        conf = f"{p['confidence']:.0%}" if p["confidence"] is not None else "?"
        correct = {1: "correct", 0: "wrong", None: "pending"}.get(p["correct"], "?")
        ret = f"{p['return_pct']:+.2f}%" if p["return_pct"] is not None else ""
        lines.append(f"{ts}  {stance} ({conf})  [{correct}] {ret}")

    acc = data["accuracy"]
    if acc is not None:
        lines.append(f"\nAccuracy (evaluated): {acc:.1%}")

    send_message("\n".join(lines))


def handle_performance() -> None:
    """Show accuracy statistics across all evaluation horizons."""
    perf = get_performance_summary("NIFTY")

    if not perf:
        send_message("No evaluated predictions yet. Run /scan a few times and wait for evaluation.")
        return

    lines = ["<b>Prediction Performance (NIFTY)</b>\n"]
    for hours, stats in sorted(perf.items()):
        label = f"{hours}h"
        lines.append(
            f"  {label:4s}  {stats['correct']}/{stats['total']}  "
            f"accuracy={stats['accuracy']:.1%}  avg_return={stats['avg_return_pct']:+.2f}%"
        )

    send_message("\n".join(lines))


def handle_agent_query(text: str) -> None:
    """Pass free-form text to the agent and send back the formatted result."""
    send_message("Thinking...")
    try:
        result = graph.invoke(
            {
                "messages": [HumanMessage(content=text)],
                "symbol": "NIFTY",
                "notify_telegram": False,
                "analysis": None,
                "snapshot_id": None,
            }
        )

        analysis = result.get("analysis") or {}
        if analysis.get("stance"):
            from stockester_agent.agent.schema import AnalystOutput
            output = AnalystOutput.model_validate(analysis)
            send_message(output.to_telegram_html())
        else:
            # Fallback: show last message content
            last = result["messages"][-1]
            send_message(getattr(last, "content", "No response."))

    except Exception as e:
        send_message(f"Agent error: {e}")


# ---------------------------------------------------------------------------
# Message dispatcher
# ---------------------------------------------------------------------------

def dispatch(text: str) -> None:
    """Route a message to the appropriate handler based on command prefix."""
    text = text.strip()
    parts = text.split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    print(f"[dispatch] cmd={cmd!r}  arg={arg!r}")

    if cmd == "/help":
        send_message(HELP_TEXT)
    elif cmd == "/scan":
        handle_scan(arg or "NIFTY")
    elif cmd == "/history":
        handle_history(arg or "NIFTY")
    elif cmd == "/performance":
        handle_performance()
    else:
        handle_agent_query(text)


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from .env")
        sys.exit(1)

    print(f"Bot started. Listening for messages from chat {CHAT_ID}...", flush=True)
    offset = None

    while True:
        updates = get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1

            msg = update.get("message", {})
            chat = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()

            if chat != CHAT_ID:
                print(f"Ignoring message from unknown chat {chat}")
                continue

            if not text:
                continue

            print(f"Received: {text!r}")
            dispatch(text)

        time.sleep(1)


if __name__ == "__main__":
    main()
