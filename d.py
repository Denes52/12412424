import os
import time
import urllib.request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import Conflict
import socks
from telethon import TelegramClient

# --- Настройки бота (из Render env) ---
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']

# --- Прокси ---
PROXIES = [
    ('8.210.148.229', 1111),
    ('192.252.214.17', 4145),
    ('5.183.131.72', 1080),
    ('47.236.166.47', 1100),
    ('68.191.23.134', 9200),
    ('103.127.223.126', 1080),
    ('103.12.161.222', 1080),
    ('103.118.175.165', 8199),
    ('107.219.228.250', 7777),
    ('184.170.251.30', 11288),
    ('47.238.67.238', 1024),
    ('8.219.119.119', 1024),
    ('185.93.89.183', 15918),
    ('165.22.110.253', 1080),
    ('156.244.45.138', 33333),
    ('198.177.253.13', 4145),
    ('176.117.237.132', 1080),
    ('8.218.104.176', 1100),
    ('193.122.123.43', 28080)
]

# --- handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Пришли номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    if not phone_number.startswith("+") or not phone_number[1:].isdigit():
        await update.message.reply_text("Неверный формат. Начни с + и цифр.")
        return

    await update.message.reply_text(f"Запуск проверки {phone_number} через {len(PROXIES)} прокси...")
    for idx, (ip, port) in enumerate(PROXIES, start=1):
        await update.message.reply_text(f"[{idx}] Прокси {ip}:{port}")
        proxy = (socks.SOCKS5, ip, port)
        client = TelegramClient(f'session_{idx}', API_ID, API_HASH, proxy=proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(phone_number)
                await update.message.reply_text(f"[{idx}] Код подтверждения отправлен")
        except Exception as e:
            await update.message.reply_text(f"[{idx}] Ошибка: {e}")
        finally:
            await client.disconnect()

# --- webhook utilities ---
def get_webhook_info():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode()
    except Exception as e:
        return f"getWebhookInfo failed: {e}"

def delete_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read().decode()
    except Exception as e:
        return f"deleteWebhook failed: {e}"

# --- main ---
def main():
    # debug logs to help в логах Render
    print("getWebhookInfo:", get_webhook_info())
    print("deleteWebhook:", delete_webhook())

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    max_retries = 6
    retry_delay = 5

    for attempt in range(1, max_retries + 1):
        try:
            print(f"Запуск polling (attempt {attempt}/{max_retries})...")
            app.run_polling()
            # Если app.run_polling() завершился без исключения — выходим
            print("Polling остановлен нормально.")
            break
        except Conflict as c:
            # конкретная обработка Conflict
            print("Conflict exception:", c)
            print("Попытка удалить webhook и перезапустить...")
            print(delete_webhook())
            time.sleep(retry_delay)
            continue
        except Exception as e:
            # общая ошибка — логируем и повторяем при возможности
            print("Polling error:", repr(e))
            if "Conflict" in str(e):
                print("Обнаружен конфликт в тексте ошибки, пробуем удалить webhook и перезапустить...")
                print(delete_webhook())
                time.sleep(retry_delay)
                continue
            # для других ошибок — повторяем пару раз, потом падаем
            time.sleep(retry_delay)
            continue
    else:
        print("Не удалось запустить polling после нескольких попыток. Проверьте, что нигде не запущен другой экземпляр бота или смените токен.")

if __name__ == "__main__":
    main()
