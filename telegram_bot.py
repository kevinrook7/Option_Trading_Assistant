"""
telegram_bot.py — minimal Telegram interface for the Stockester agent.

Polls for incoming messages, passes each one to the LangGraph agent,
and replies with the analyst's final response.

Run:
    python telegram_bot.py
"""

import sys
import os
import time
import requests

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from stockester_agent.agent.graph import graph

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = str(os.getenv("TELEGRAM_CHAT_ID"))
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"


def get_updates(offset=None):
    params = {"timeout": 30, "offset": offset}
    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        print(f"⚠️  getUpdates error: {e}")
        return []


def send_message(text):
    # Telegram messages are capped at 4096 chars
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        try:
            requests.post(
                f"{BASE_URL}/sendMessage",
                json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as e:
            print(f"⚠️  sendMessage error: {e}")


def handle_message(text: str):
    print(f"📨 Received: {text!r}")
    send_message("⏳ Thinking...")

    try:
        result = graph.invoke({"messages": [HumanMessage(content=text)]})
        reply  = result["messages"][-1].content
    except Exception as e:
        reply = f"❌ Agent error: {e}"

    send_message(reply)
    print("✅ Replied.")


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from .env")
        sys.exit(1)

    print(f"🤖 Bot started. Listening for messages from chat {CHAT_ID}...")
    offset = None

    while True:
        updates = get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1

            msg = update.get("message", {})
            chat = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()

            # Only respond to your own chat
            if chat != CHAT_ID:
                print(f"⛔ Ignoring message from unknown chat {chat}")
                continue

            if not text:
                continue

            handle_message(text)

        time.sleep(1)


if __name__ == "__main__":
    main()
