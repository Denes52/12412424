# d.py — финальная версия
import os
import socket
import ssl
import socks
import asyncio
from concurrent.futures import ThreadPoolExecutor

from telethon import TelegramClient
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- Переменные окружения (на Render настроить BOT_TOKEN, API_ID, API_HASH) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH")

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("Установите BOT_TOKEN, API_ID, API_HASH в переменных окружения")

# --- Чтение прокси (парсит строки с или без префикса socks5://) ---
def load_proxies(filename="proxies.txt"):
    proxies = []
    if not os.path.exists(filename):
        return proxies
    with open(filename, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            # убрать возможный префикс
            if s.startswith("socks5://"):
                s = s[len("socks5://"):]
            if s.startswith("http://"):
                s = s[len("http://"):]
            if ":" not in s:
                continue
            ip, port = s.split(":", 1)
            try:
                port = int(port)
            except ValueError:
                continue
            proxies.append((ip.strip(), port))
    return proxies

PROXIES = load_proxies("proxies.txt")

# --- Синхронная проверка одного SOCKS5-прокси: подключиться через proxy к api.telegram.org:443 и выполнить TLS handshake ---
def check_proxy_sync(ip: str, port: int, timeout: float = 6.0) -> bool:
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, ip, port, rdns=True)
    s.settimeout(timeout)
    try:
        # подключаемся к telegram api через прокси
        s.connect(("api.telegram.org", 443))
        # оборачиваем SSL и делаем рукопожатие (проверка сертификата)
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

# --- Асинхронно проверяем все прокси через ThreadPoolExecutor ---
async def filter_working_proxies(proxies, workers=20):
    loop = asyncio.get_running_loop()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        tasks = [loop.run_in_executor(ex, check_proxy_sync, ip, port) for ip,port in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    good = []
    for (ip,port), res in zip(proxies, results):
        if isinstance(res, Exception):
            # исключения считаем нерабочими
            continue
        if res:
            good.append((ip, port))
    return good

# --- Телеграм-бот: minimal output. Пришли номер, бот проверит прокси, запишет ok_proxies.txt и отправит итог ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Пример: +79998887766")
        return

    # короткое уведомление пользователю, потом финал
    await update.message.reply_text("Запрос принят — проверяю прокси и отправляю коды...")

    # проверяем прокси (параллельно)
    good = await filter_working_proxies(PROXIES, workers=20)

    # сохраняем рабочие прокси
    with open("ok_proxies.txt", "w", encoding="utf-8") as f:
        for ip, port in good:
            f.write(f"{ip}:{port}\n")

    sent_count = 0
    # последовательно пробуем отправлять код через каждый рабочий прокси
    for idx, (ip, port) in enumerate(good, start=1):
        proxy = (socks.SOCKS5, ip, port)
        session_name = f"session_{idx}"
        client = TelegramClient(session_name, API_ID, API_HASH, proxy=proxy)
        try:
            await client.connect()
            # если сессия уже авторизована, считаем как успешный (но не нужно отправлять код)
            if await client.is_user_authorized():
                sent_count += 1
            else:
                # отправляем код подтверждения
                await client.send_code_request(phone)
                sent_count += 1
        except Exception:
            # пропускаем проблемный прокси
            pass
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    # финальный ответ — только цифры и файл
    await update.message.reply_text(f"Готово ✅ Рабочих прокси: {len(good)}. Запросов кода отправлено: {sent_count}.")

# --- Запуск бота ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен (polling)...")
    app.run_polling()

if __name__ == "__main__":
    main()
