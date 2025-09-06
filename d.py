# d.py — тихая/production-дружелюбная версия (Flask dev стартует только если RUN_FLASK_DEV=1)
import os
import ssl
import socks
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import warnings
from logging.handlers import RotatingFileHandler

# ========== Конфиг ==========
PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"
CONNECT_TIMEOUT = 20.0
SEND_CODE_TIMEOUT = 20.0
IS_AUTH_TIMEOUT = 6.0
MAX_SEND_PER_REQUEST = 25
SEND_CONCURRENCY = 3
DELAY_BETWEEN_TASKS = 0.25
LOGFILE = "d.log"
# ===========================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH")
if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите переменные окружения BOT_TOKEN, API_ID, API_HASH")

# Logging: подробности в лог файл, консоль — только CRITICAL
def setup_logging():
    warnings.filterwarnings("ignore")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(LOGFILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.CRITICAL)
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(ch)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("telethon").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("asyncio").setLevel(logging.ERROR)

# Убираем флажок баннера Flask (если dev запуск включён)
try:
    from flask import cli as flask_cli
    flask_cli.show_server_banner = lambda *args, **kwargs: None
except Exception:
    pass

setup_logging()
logger = logging.getLogger("d_service")

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

# Telegram handlers (без изменений, только логирование в файл)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришлите номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    await update.message.reply_text("Принял. Сразу начинаю попытки через указанные прокси (подробности в d.log)...")

    proxies = load_proxies(PROXIES_FILE)
    if not proxies:
        await update.message.reply_text("Нет прокси в файле proxies.txt")
        logger.warning("proxies.txt пуст или не найден")
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
                    logger.info("client.connect() timeout via %s:%s", ip, port)
                    try:
                        await client.disconnect()
                    except:
                        pass
                    return
                except Exception as e:
                    logger.info("connect failed %s:%s -> %s", ip, port, repr(e))
                    try:
                        await client.disconnect()
                    except:
                        pass
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
                            logger.info("send_code_request OK via %s:%s", ip, port)
                        except asyncio.TimeoutError:
                            logger.info("send_code_request timeout via %s:%s", ip, port)
                        except Exception as e_inner:
                            logger.info("send_code_request failed via %s:%s -> %s", ip, port, repr(e_inner))
                finally:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

            except Exception as e_outer:
                logger.info("Telethon exception via %s:%s -> %s", ip, port, repr(e_outer))

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

    await update.message.reply_text(f"Готово. Попыток отправки кода: {sent}. Успешные прокси: {len(ok_list)}.")
    logger.info("Finished attempt for %s; sent=%d; ok=%d", phone, sent, len(ok_list))

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

# Flask app (gunicorn будет использовать переменную flask_app как WSGI прилож.!)
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "OK", 200

# если хотите запустить dev-flask локально: export RUN_FLASK_DEV=1
def run_flask_dev_if_requested():
    if os.environ.get("RUN_FLASK_DEV") == "1":
        port = int(os.environ.get("PORT", 5000))
        # запускаем только в отдельном потоке для dev целей
        t = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=port), daemon=True, name="flask_bg")
        t.start()

def ensure_event_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

def main():
    logger.info("Starting service (production friendly). Logs -> %s", LOGFILE)
    # dev flask запустится только если явно включён через env
    run_flask_dev_if_requested()
    app = build_app()
    ensure_event_loop()
    try:
        logger.info("Bot starting (polling)...")
        app.run_polling()
    except Exception as e:
        logger.exception("Ошибка при запуске бота: %s", e)

if __name__ == "__main__":
    main()
