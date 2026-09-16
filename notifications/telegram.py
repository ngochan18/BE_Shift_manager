import os
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


class TelegramService:
    @staticmethod
    def send_message(chat_id, text):
        """Gửi tin nhắn Telegram tới một chat_id."""
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN chưa được cấu hình.")
            return False

        if not chat_id:
            return False

        try:
            response = requests.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
        except requests.RequestException as e:
            logger.error(f"Telegram request failed: {str(e)}")
            return False

    @staticmethod
    def send_to_many(chat_ids, text):
        """Gửi tin nhắn Telegram tới nhiều chat_id."""
        results = []
        for chat_id in chat_ids:
            if chat_id:
                results.append(TelegramService.send_message(chat_id, text))
        return results
