# d.py — финальный код: проверка прокси, аккуратная работа с Telethon и сообщения в чат
import os
import socket
import socks
import asyncio
import traceback
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from telethon import TelegramClient

# --- Конфиг через переменные окружения ---
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']

# Прокси по умолчанию (ip, port). Заменяй на живые.
PROXIES = [
    ('8.210.148.229', 1111),
    ('192.252.214.17', 4145),
    ('5.183.131.72', 1080),
    ('47.236.166.47', 1100),
    ('68.191.23.134', 9200),
    # ...
]

# Управление: если установить USE_PROXIES="false", работа будет без прокси
USE_PROXIES = os.environ.get('USE_PROXIES', 'true').lower() not in ('0', 'false', 'no')
# Таймаут TCP-проверки прокси (сек)
PROXY_CHECK_TIMEOUT = float(os.environ.get('PROXY_CHECK_TIMEOUT', '3.0'))

# --- Утилиты ---
def is_tcp_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """Быстрая проверка доступности TCP порта (фильтрует Connection refused)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

async def try_send_code_with_proxy(phone: str, ip: str, port: int, idx: int, update: Update):
    """Создать Telethon клиент через прокси и отправить код (одна попытка)."""
    # Telethon принимает proxy как (socks.SOCKS5, host, port, True/False, username, password)
    proxy = (socks.SOCKS5, ip, port, True, None, None)
    session_name = f"session_{idx}"
    client = TelegramClient(session_name, API_ID, API_HASH, proxy=proxy)
    try:
        await client.connect()
        # если уже авторизован — нет смысла слать код
        if await client.is_user_authorized():
            await update.message.reply_text(f"[{idx}] Сессия уже авторизована (session: {session_name}). Пропускаю.")
            return True
        await client.send_code_request(phone)
        await update.message.reply_text(f"[{idx}] Код подтверждения отправлен на {phone} через {ip}:{port}")
        return True
    except Exception as e:
        await update.message.reply_text(f"[{idx}] Ошибка при работе через {ip}:{port} — {e}")
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

async def try_send_code_without_proxy(phone: str, idx: int, update: Update):
    """Попытка отправить код без прокси (прямое подключение)."""
    session_name = f"session_direct_{idx}"
    client = TelegramClient(session_name, API_ID, API_HASH)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await update.message.reply_text(f"[direct {idx}] Сессия уже авторизована. Пропускаю.")
            return True
        await client.send_code_request(phone)
        await update.message.reply_text(f"[direct {idx}] Код подтверждения отправлен на {phone} (без прокси)")
        return True
    except Exception as e:
        await update.message.reply_text(f"[direct {idx}] Ошибка (без прокси): {e}")
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Пришли номер в формате +79998887766")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith('+') or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат номера. Начни с + и используй только цифры.")
        return

    await update.message.reply_text(f"Запуск проверки номера {phone}. USE_PROXIES={USE_PROXIES}")

    # если не используем прокси — одна попытка напрямую
    if not USE_PROXIES:
        ok = await try_send_code_without_proxy(phone, 1, update)
        if not ok:
            await update.message.reply_text("Попытка без прокси не удалась.")
        return

    # Идем по списку прокси, сначала фильтруем недоступные по TCP
    alive = []
    for ip, port in PROXIES:
        await update.message.reply_text(f"Проверяю прокси {ip}:{port} ...")
        if is_tcp_open(ip, port, timeout=PROXY_CHECK_TIMEOUT):
            alive.append((ip, port))
            await update.message.reply_text(f"Прокси {ip}:{port} — доступен (TCP).")
        else:
            await update.message.reply_text(f"Прокси {ip}:{port} — недоступен (TCP). Пропускаю.")

    if not alive:
        await update.message.reply_text("Нет доступных прокси. Установи рабочие прокси или выставь USE_PROXIES=false.")
        return

    # Пробуем по очереди через доступные прокси, пока не отправим код
    for idx, (ip, port) in enumerate(alive, start=1):
        await update.message.reply_text(f"[Попытка {idx}] Использую прокси {ip}:{port}")
        try:
            ok = await try_send_code_with_proxy(phone, ip, port, idx, update)
            if ok:
                return
        except Exception as e:
            # на всякий — логируем полную трассировку в чат (коротко)
            await update.message.reply_text(f"[{idx}] Неожиданная ошибка: {e}")
            await update.message.reply_text("Подробности в логах сервера.")
            print("Traceback:", traceback.format_exc())

    # Если все прокси не сработали — пробуем один раз без прокси
    await update.message.reply_text("Все прокси не сработали — пробую отправить без прокси.")
    ok = await try_send_code_without_proxy(phone, 1, update)
    if not ok:
        await update.message.reply_text("Не удалось отправить код ни через прокси, ни напрямую. Проверь прокси или API_ID/API_HASH.")

# --- Запуск приложения ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен. USE_PROXIES =", USE_PROXIES)
    app.run_polling()
