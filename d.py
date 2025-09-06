import os
import ssl
import socks
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ====== Конфиг ======
PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"

CONNECT_TIMEOUT = 20.0
SEND_CODE_TIMEOUT = 20.0
IS_AUTH_TIMEOUT = 6.0
MAX_SEND_PER_REQUEST = 25
SEND_CONCURRENCY = 3
DELAY_BETWEEN_TASKS = 0.25

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите переменные окружения BOT_TOKEN, API_ID, API_HASH")

# ======================
def parse_proxy_line(line: str):
    parts = line.strip().split(":")
    if len(parts) < 2:
        return None
    ip = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except:
        return None
    return (ip, port)

def load_proxies(filename=PROXIES_FILE):
    proxies = []
    if not os.path.exists(filename):
        return proxies
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            p = parse_proxy_line(s)
            if p:
                proxies.append(p)
    return proxies

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришлите номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    msg = await update.message.reply_text("Принял. Сразу начинаю попытки через указанные прокси...")
    proxies = load_proxies(PROXIES_FILE)
    if not proxies:
        await msg.edit_text("Нет прокси в файле proxies.txt")
        return

    to_try = proxies[:MAX_SEND_PER_REQUEST]
    sent = 0
    sem = asyncio.Semaphore(SEND_CONCURRENCY)
    ok_list = []

    async def try_send_via_proxy(ip, port):
        nonlocal sent
        proxy = (socks.SOCKS5, ip, port)
        session = f"session_{ip.replace('.', '_')}_{port}"
        async with sem:
            try:
                client = TelegramClient(session, API_ID, API_HASH, proxy=proxy)
                try:
                    await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                except asyncio.TimeoutError:
                    print(f"[timeout] client.connect() через {ip}:{port}")
                    try: await client.disconnect()
                    except: pass
                    return
                except Exception as e:
                    print(f"[warn] connect failed {ip}:{port}: {repr(e)}")
                    try: await client.disconnect()
                    except: pass
                    return

                try:
                    try:
                        is_auth = await asyncio.wait_for(client.is_user_authorized(), timeout=IS_AUTH_TIMEOUT)
                    except Exception:
                        is_auth = False
                    if not is_auth:
                        try:
                            await asyncio.wait_for(client.send_code_request(phone), timeout=SEND_CODE_TIMEOUT)
                            sent += 1
                            ok_list.append(f"{ip}:{port}")
                            print(f"[ok] send_code_request via {ip}:{port}")
                        except asyncio.TimeoutError:
                            print(f"[timeout] send_code_request via {ip}:{port}")
                        except Exception as e_inner:
                            print(f"[warn] send_code_request failed via {ip}:{port}: {repr(e_inner)}")
                finally:
                    try: await client.disconnect()
                    except: pass
            except Exception as e_outer:
                print(f"[warn] Ошибка Telethon через {ip}:{port}: {repr(e_outer)}")

    tasks = []
    for ip, port in to_try:
        tasks.append(asyncio.create_task(try_send_via_proxy(ip, port)))
        await asyncio.sleep(DELAY_BETWEEN_TASKS)

    if tasks:
        await asyncio.gather(*tasks)

    if ok_list:
        with open(OK_PROXIES_FILE, "w", encoding="utf-8") as f:
            for line in ok_list:
                f.write(line + "\n")

    await msg.edit_text(f"Готово. Попыток отправки кода: {sent}. Успешные прокси: {len(ok_list)}.")

def build_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

# Flask app
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "OK", 200

# запускаем бота в фоне, но в том же event loop, без закрытия лупа
def start_bot_background():
    async def runner():
        bot = build_bot()
        print("Бот запускается (polling)...")
        await bot.run_polling(stop_signals=None, close_loop=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(runner())
    loop.run_forever()

threading.Thread(target=start_bot_background, daemon=True).start()
