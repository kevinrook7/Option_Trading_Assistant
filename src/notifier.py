import requests
from src.config_loader import get_env_var

def send_telegram_message(message):
    """
    Sends a text message via Telegram Bot API.
    Returns True if successful, False otherwise.
    """
    bot_token = get_env_var("TELEGRAM_BOT_TOKEN")
    chat_id = get_env_var("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        print("❌ Telegram credentials missing. Check your .env file.")
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"  # Allows bold/italic formatting
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ Telegram notification sent successfully.")
            return True
        else:
            print(f"❌ Failed to send Telegram message: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending Telegram message: {e}")
        return False