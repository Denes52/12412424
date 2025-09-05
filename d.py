import os
import asyncio
import socks
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- Настройки бота ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# --- Список прокси (ip, port) ---
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

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли мне номер телефона в формате +79998887766"
    )

# --- Обработка номера телефона ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    if not phone_number.startswith("+") or not phone_number[1:].isdigit():
        await update.message.reply_text("Неверный формат номера. Начни с + и используй только цифры")
        return

    await update.message.reply_text(f"Запущена проверка номера {phone_number} через {len(PROXIES)} прокси...")

    for idx, (ip, port) in enumerate(PROXIES, start=1):
        await update.message.reply_text(f"[{idx}] Используется прокси {ip}:{port}")

        # Создаем Telethon клиент с SOCKS5 прокси
        proxy = (socks.SOCKS5, ip, port)
        client = TelegramClient(f'session_{idx}', API_ID, API_HASH, proxy=proxy)

        try:
            await client.start()
            if not await client.is_user_authorized():
                await client.send_code_request(phone_number)
                await update.message.reply_text(f"[{idx}] Код подтверждения отправлен на {phone_number}")
        except Exception as e:
            await update.message.reply_text(f"[{idx}] Ошибка: {e}")
        finally:
            await client.disconnect()

# --- Запуск бота ---
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
