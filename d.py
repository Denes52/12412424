# d.py — финальная версия для Render (web service) или worker
import os
import ssl
import socks
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========== Настройки (при необходимости поменяй) ==========
TCP_TIMEOUT = 1.2
SSL_TIMEOUT = 2.0
WORKERS = 40
PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"
# ===========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите переменные окружения BOT_TOKEN, API_ID, API_HASH")

# --- helpers: чтение proxies.txt ---
def load_proxies(filename=PROXIES_FILE):
    out = []
    if not os.path.exists(filename):
        return out
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
                p = int(port)
            except Exception:
                continue
            out.append((ip.strip(), p))
    return out

PROXIES = load_proxies(PROXIES_FILE)

# --- синхронная двухэтапная проверка (TCP + TLS) ---
def check_proxy_sync(ip: str, port: int, tcp_timeout=TCP_TIMEOUT, ssl_timeout=SSL_TIMEOUT) -> bool:
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, ip, port, rdns=True)
    s.settimeout(tcp_timeout)
    try:
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

async def filter_working_proxies(proxies, workers=WORKERS):
    if not proxies:
        return []
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        tasks = [loop.run_in_executor(ex, check_proxy_sync, ip, port) for ip, port in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    good = []
    for (ip, port), r in zip(proxies, results):
        if r is True:
            good.append((ip, port))
    return good

# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли номер: +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    await update.message.reply_text("Принял. Проверяю прокси (быстро)...")

    good = await filter_working_proxies(PROXIES, workers=WORKERS)

    # записать рабочие прокси
    with open(OK_PROXIES_FILE, "w", encoding="utf-8") as f:
        for ip, port in good:
            f.write(f"{ip}:{port}\n")

    sent = 0
    # пробуем отправлять код по каждому рабочему прокси
    for idx, (ip, port) in enumerate(good, start=1):
        proxy = (socks.SOCKS5, ip, port)
        session = f"session_{idx}"
        client = TelegramClient(session, API_ID, API_HASH, proxy=proxy)
        try:
            await client.connect()
            if await client.is_user_authorized():
                # уже авторизован — считаем как успешную проверку, но не шлём код
                sent += 0
            else:
                await client.send_code_request(phone)
                sent += 1
        except Exception:
            pass
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    await update.message.reply_text(f"Готово. Рабочих прокси: {len(good)}. Кодов отправлено: {sent}.")

# --- создаём приложение бота ---
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

# --- запуск бота в отдельном потоке (чтобы основной процесс слушал порт) ---
def run_bot_bg(app):
    # запускаем polling (blocking) в отдельном потоке
    app.run_polling()

# --- создаём Flask чтобы Render увидел открытый порт ---
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "OK", 200

def main():
    app = build_app()
    # стартуем бота в фоне
    t = threading.Thread(target=run_bot_bg, args=(app,), daemon=True)
    t.start()

    # слушаем порт, Render увидит сервис
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
