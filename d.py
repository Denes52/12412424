# d.py — финальная версия с быстрой проверкой SOCKS5
import os
import ssl
import socks
import asyncio
from concurrent.futures import ThreadPoolExecutor

from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ========== Параметры (можно менять) ==========
TCP_TIMEOUT = 1.2    # быстрый TCP connect (сек)
SSL_TIMEOUT = 2.0    # быстрый TLS handshake (сек)
WORKERS = 40         # число потоков для параллельной проверки
# ==============================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите переменные окружения BOT_TOKEN, API_ID, API_HASH")

# читаем proxies.txt (поддерживает строки с или без "socks5://")
def load_proxies(filename="proxies.txt"):
    lst = []
    if not os.path.exists(filename):
        return lst
    with open(filename, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("socks5://"):
                s = s[len("socks5://"):]
            if s.startswith("http://"):
                s = s[len("http://"):]
            if ":" not in s:
                continue
            ip, port = s.split(":", 1)
            try:
                p = int(port)
            except Exception:
                continue
            lst.append((ip.strip(), p))
    return lst

PROXIES = load_proxies("proxies.txt")

# синхронная двухэтапная проверка через SOCKS5: TCP connect + TLS handshake
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

# асинхронно фильтруем рабочие прокси через ThreadPoolExecutor
async def filter_working_proxies(proxies, workers=WORKERS):
    if not proxies:
        return []
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        tasks = [loop.run_in_executor(ex, check_proxy_sync, ip, port) for ip, port in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    good = []
    for (ip, port), res in zip(proxies, results):
        if res is True:
            good.append((ip, port))
    return good

# Telegram bot handlers (минимум выводов)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    await update.message.reply_text("Запрос принят — проверяю прокси...")

    good = await filter_working_proxies(PROXIES, workers=WORKERS)

    # записать рабочие прокси в файл
    with open("ok_proxies.txt", "w", encoding="utf-8") as f:
        for ip, port in good:
            f.write(f"{ip}:{port}\n")

    sent = 0
    # попробуем отправлять коды через рабочие прокси (последовательно)
    for idx, (ip, port) in enumerate(good, start=1):
        proxy = (socks.SOCKS5, ip, port)
        session = f"session_{idx}"
        client = TelegramClient(session, API_ID, API_HASH, proxy=proxy)
        try:
            await client.connect()
            if await client.is_user_authorized():
                sent += 1
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

    await update.message.reply_text(f"Готово ✅ Рабочих прокси: {len(good)}. Запросов кода отправлено: {sent}.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен (polling)...")
    app.run_polling()

if __name__ == "__main__":
    main()
