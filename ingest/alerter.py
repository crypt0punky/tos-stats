"""Telegram-алерты для ошибок pipeline.

Отдельный bot (создан через @BotFather), отдельный TOKEN + CHAT_ID Daniil'а.
В случае любой necovered exception в run.py - sendMessage с traceback.
"""

import logging
import os
import traceback

import aiohttp

from . import config

log = logging.getLogger(__name__)


async def notify(message: str, level: str = "ERROR") -> None:
    """Послать сообщение в Telegram алерт-чат.

    Не raise если что-то не так с TG -- pipeline уже падает,
    алерт-фейл это вторичная проблема.
    """
    token = os.environ.get(config.TG_BOT_TOKEN_ENV)
    chat_id = os.environ.get(config.TG_CHAT_ID_ENV)

    if not token or not chat_id:
        log.warning("TG alert credentials not set, message lost: %s", message[:100])
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"🚨 *tos-stats {level}*\n\n```\n{message[:3500]}\n```"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("TG sendMessage %d: %s", resp.status, body[:200])
    except Exception as e:
        log.error("TG alert delivery failed: %s", e)


async def notify_exception(prefix: str, exc: BaseException) -> None:
    """Удобная обёртка для алерта с трейсбэком."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    await notify(f"{prefix}\n\n{tb}", level="ERROR")


async def notify_success(stats: dict) -> None:
    """Опциональный позитивный алерт что pipeline прошёл (для дебага первой пятницы).

    После того как pipeline стабилен -- этот алерт можно убрать через env флаг.
    """
    if os.environ.get("TG_NOTIFY_SUCCESS") != "1":
        return
    msg = (
        f"✅ tos-stats pipeline OK\n"
        f"Week: {stats.get('week')}\n"
        f"New rows: {stats.get('new_rows', 0)}\n"
        f"Extreme tags: {stats.get('extreme_count', 0)}"
    )
    await notify(msg, level="OK")
