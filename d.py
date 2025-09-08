import os
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient
from telethon.errors import FloodWaitError, PhoneNumberInvalidError, PhoneCodeInvalidError

# ================== Flask (для Render) ==================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is running!"

# ================== Настройки из переменных окружения ==================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
USE_PROXIES = os.getenv("USE_PROXIES", "true").lower() == "true"

PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"

# ================== Функция проверки прокси ==================
async def try_proxy(phone_number: str, proxy: tuple) -> bool:
    print(f"🔌 Пробую прокси {proxy[0]}:{proxy[1]}")
    client = TelegramClient("check_session", API_ID, API_HASH, proxy=("socks5", proxy[0], proxy[1]))

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone_number)
            print(f"📲 Отправляю код на {phone_number}...")
        await client.disconnect()

        with open(OK_PROXIES_FILE, "a") as f:
            f.write(f"{proxy[0]}:{proxy[1]}\n")

        print("✅ Прокси успешно!")
        return True
    except FloodWaitError as e:
        print(f"⏳ FloodWait {e.seconds} сек")
    except PhoneNumberInvalidError:
        print("❌ Неверный номер")
    except PhoneCodeInvalidError:
        print("❌ Неверный код")
    except Exception as e:
        print(f"❌ Ошибка прокси {proxy}: {e}")
    finally:
        await client.disconnect()
    return False

# ================== Хэндлеры бота ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь номер телефона в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    await update.message.reply_text(f"🔍 Проверяю прокси для {phone_number}...")

    if not os.path.exists(PROXIES_FILE):
        await update.message.reply_text("❌ Файл proxies.txt не найден.")
        return

    with open(PROXIES_FILE, "r") as f:
        proxies = [line.strip().split(":") for line in f if line.strip()]

    for host, port in proxies:
        ok = await try_proxy(phone_number, (host, int(port)))
        if ok:
            await update.message.reply_text(f"✅ Код отправлен на {phone_number} через {host}:{port}")
            return

    await update.message.reply_text("❌ Не удалось отправить код через все прокси.")

# ================== Запуск ==================
def main():
    # Telegram Bot
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск Flask + Bot
    import threading
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()
    app_bot.run_polling()

if __name__ == "__main__":
    main()
