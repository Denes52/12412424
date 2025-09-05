# d.py — финальный: проверка прокси через SOCKS (соединение к api.telegram.org:443),
# только потом передаём proxy в Telethon.
import os
import socket
import socks
import ssl
import traceback
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from telethon import TelegramClient

BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']

# Прокси: (ip, port)
PROXIES = [
    ('8.210.148.229', 1111),
    ('192.252.214.17', 4145),
    ('5.183.131.72', 1080),
    ('47.236.166.47', 1100),
    ('68.191.23.134', 9200),
    # ... остальные
]

# Управление: если false — не используем прокси
USE_PROXIES = os.environ.get('USE_PROXIES', 'true').lower() not in ('0', 'false', 'no')
PROXY_CONNECT_TIMEOUT = float(os.environ.get('PROXY_CHECK_TIMEOUT', '4.0'))


def test_proxy_to_host_socks5(ip: str, port: int, timeout: float = 4.0) -> (bool, str):
    """
    Попытка через SOCKS5 прокси подключиться к api.telegram.org:443.
    Возвращает (True, '') если OK, иначе (False, 'причина').
    Это реальный тест — если прокси не умеет проксировать TLS на внешний хост,
    Telethon тоже не сможет работать через него.
    """
    try:
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, ip, port, rdns=True)  # rdns True
        s.settimeout(timeout)
        # Попытка установить TLS-соединение к api.telegram.org:443 через прокси
        s.connect(("api.telegram.org", 443))
        # обернём в TLS-контекст и сделаем handshake (быстрая проверка)
        ctx = ssl.create_default_context()
        ssl_sock = ctx.wrap_socket(s, server_hostname="api.telegram.org")
        # небольшой recv чтобы убедиться, что handshake прошёл
        ssl_sock.settimeout(2.0)
        try:
            ssl_sock.recv(1)
        except socket.timeout:
            # timeout recv — OK, значит handshake скорее всего успешен
            pass
        ssl_sock.close()
        return True, ""
    except Exception as e:
        return False, repr(e)


async def send_code_via_proxy(phone: str, ip: str, port: int, idx: int, update: Update) -> bool:
    """
    Попытка отправить код через Telethon, используя конкретный proxy.
    Возвращает True если отправлено/успешно, False иначе.
    Все исключения логируем.
    """
    proxy_tuple = (socks.SOCKS5, ip, port, True, None, None)
    session = f"session_{idx}"
    client = TelegramClient(session, API_ID, API_HASH, proxy=proxy_tuple)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await update.message.reply_text(f"[{idx}] session {session} уже авторизована — пропускаю.")
            return True
        await client.send_code_request(phone)
        await update.message.reply_text(f"[{idx}] Код подтверждения отправлен через {ip}:{port}")
        return True
    except Exception as e:
        await update.message.reply_text(f"[{idx}] Ошибка Telethon через {ip}:{port}: {e}")
        print(f"[{idx}] Telethon traceback:", traceback.format_exc())
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def send_code_direct(phone: str, idx: int, update: Update) -> bool:
    """Попытка отправить код напрямую (без прокси)."""
    session = f"session_direct_{idx}"
    client = TelegramClient(session, API_ID, API_HASH)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await update.message.reply_text(f"[direct {idx}] session авторизована — пропускаю.")
            return True
        await client.send_code_request(phone)
        await update.message.reply_text(f"[direct {idx}] Код подтверждения отправлен (без прокси).")
        return True
    except Exception as e:
        await update.message.reply_text(f"[direct {idx}] Ошибка (без прокси): {e}")
        print("[direct] traceback:", traceback.format_exc())
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
    if not phone.startswith("+") or not phone[1:].isdigit():
        await update.message.reply_text("Неверный формат. Начни с + и цифр.")
        return

    await update.message.reply_text(f"Запуск проверки {phone}. USE_PROXIES={USE_PROXIES}")

    if not USE_PROXIES:
        ok = await send_code_direct(phone, 1, update)
        if not ok:
            await update.message.reply_text("Не удалось отправить код напрямую — проверь API_ID/API_HASH.")
        return

    # 1) Быстрая TCP-проверка (create_connection)
    alive_tcp = []
    for ip, port in PROXIES:
        await update.message.reply_text(f"Проверяю TCP {ip}:{port} ...")
        try:
            with socket.create_connection((ip, port), timeout=2.0):
                alive_tcp.append((ip, port))
                await update.message.reply_text(f"TCP OK: {ip}:{port}")
        except Exception:
            await update.message.reply_text(f"TCP недоступен: {ip}:{port} — пропускаю")

    if not alive_tcp:
        await update.message.reply_text("Нет прокси с доступным TCP. Установи рабочие прокси или USE_PROXIES=false.")
        return

    # 2) Тестируем реальную проксировку к Telegram через SOCKS5
    alive_real = []
    for ip, port in alive_tcp:
        await update.message.reply_text(f"Тестирую SOCKS5-проксирование {ip}:{port} -> api.telegram.org:443 ...")
        ok, reason = test_proxy_to_host_socks5(ip, port, timeout=PROXY_CONNECT_TIMEOUT)
        if ok:
            alive_real.append((ip, port))
            await update.message.reply_text(f"Прокси реально проксирует: {ip}:{port}")
        else:
            await update.message.reply_text(f"Прокси НЕ проксирует к Telegram: {ip}:{port}  причина: {reason}")

    if not alive_real:
        await update.message.reply_text("Не найдено прокси, которые проксируют к Telegram. Попробуй установить USE_PROXIES=false или добавить другие прокси.")
        return

    # 3) Пытаемся отправить код через реальные прокси по очереди
    for idx, (ip, port) in enumerate(alive_real, start=1):
        await update.message.reply_text(f"[Попытка {idx}] Использую прокси {ip}:{port}")
        ok = await send_code_via_proxy(phone, ip, port, idx, update)
        if ok:
            return

    # 4) Если все не удалось, пробуем зайти напрямую в конце
    await update.message.reply_text("Все прокси не сработали — пробую напрямую.")
    ok = await send_code_direct(phone, 1, update)
    if not ok:
        await update.message.reply_text("Не удалось отправить код ни через прокси, ни напрямую. Проверь API_ID/API_HASH и прокси.")

# --- Запуск ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен. USE_PROXIES =", USE_PROXIES)
    app.run_polling()
