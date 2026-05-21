"""Постинг weekly snapshot в Discord через webhook.

Никакого бота не нужно. Discord webhook = просто POST на URL.
Один webhook URL -- DISCORD_WEBHOOK_WEEKLY -- сюда идёт всё:
  1. Главный embed с TLDR + таблица по 6 парам + DXY
  2. Если есть extreme-теги -- follow-up post с extreme-парами,
     с пингом DISCORD_SWING_ROLE_ID если он задан в env.

Между постами sleep 2 сек чтобы не упереться в rate limit Discord webhook.
"""

import asyncio
import logging
import os

import aiohttp

log = logging.getLogger(__name__)


# Discord embed color: deep-black монохромный (берём rich-black из дизайн-системы).
EMBED_COLOR_DEFAULT = 0x0A0A0A
EMBED_COLOR_EXTREME = 0xB8392C  # red акцент для extreme alerts


TAG_LABEL_RU = {
    "extreme": "Крайнее",
    "stretched": "Растянуто",
    "momentum": "Импульс",
    "neutral": "Нейтрально",
}


def _format_number(n: int, signed: bool = False) -> str:
    if signed:
        return f"{n:+,}".replace(",", " ")
    return f"{n:,}".replace(",", " ")


def _build_weekly_embed(snapshot: dict, site_url: str) -> dict:
    """Главный embed - сводка по 6 парам + DXY aggregate."""
    lines = []
    lines.append("```")
    lines.append(f"{'PAIR':<8}{'TAG':<12}{'AM NET':>14}{'WoW':>10}{'W3y':>6}")
    for p in snapshot["pairs"]:
        tag = TAG_LABEL_RU.get(p["tag"], p["tag"])
        lines.append(
            f"{p['id']:<8}{tag:<12}{_format_number(p['am_net'], True):>14}"
            f"{_format_number(p['am_wow'], True):>10}{p['williams']['w3y']:>6}"
        )
    agg = snapshot["dxy_aggregate"]
    lines.append(
        f"{'DXY':<8}{TAG_LABEL_RU.get(agg['tag'], agg['tag']):<12}"
        f"{_format_number(agg['weighted_net'], True):>14}"
        f"{_format_number(agg['wow'], True):>10}{agg['williams']['w3y']:>6}"
    )
    lines.append("```")

    table = "\n".join(lines)

    # TLDR убираем <em> теги для Discord (он их не рендерит).
    tldr_plain = snapshot["tldr"].replace("<em>", "**").replace("</em>", "**")

    embed = {
        "title": f"TOS COT Snapshot · Неделя {snapshot['week']} · {snapshot['year']}",
        "description": f"{tldr_plain}\n\n{table}",
        "color": EMBED_COLOR_DEFAULT,
        "url": site_url,
        "footer": {
            "text": f"Данные CFTC TFF · Обновлено {snapshot['updated_at'][:10]} · Не финансовая рекомендация",
        },
    }
    return embed


def _build_alerts_embeds(snapshot: dict, site_url: str) -> list[dict]:
    """Отдельные embeds для пар с extreme tag (1-3 в неделю обычно)."""
    embeds = []
    for p in snapshot["pairs"]:
        if p["tag"] != "extreme":
            continue
        narrative = p.get("narrative", {})
        snapshot_text = narrative.get("snapshot", "")
        # Убираем <em> теги (Discord не рендерит)
        snapshot_text = snapshot_text.replace("<em>", "**").replace("</em>", "**")

        direction = "long" if p["am_net"] > 0 else "short"
        color = 0x15803D if direction == "long" else 0xB8392C

        embeds.append({
            "title": f"⚠ {p['id']} - Crowded {direction.upper()}",
            "description": snapshot_text,
            "color": color,
            "url": f"{site_url}#/{p['id']}",
            "fields": [
                {"name": "Williams 3y", "value": str(p["williams"]["w3y"]), "inline": True},
                {"name": "AM Net", "value": _format_number(p["am_net"], True), "inline": True},
                {"name": "Неделя", "value": _format_number(p["am_wow"], True), "inline": True},
            ],
            "footer": {"text": "Открыть детальный разбор -> tos-stats"},
        })

    # DXY aggregate отдельным алертом если extreme.
    agg = snapshot["dxy_aggregate"]
    if agg["tag"] == "extreme":
        narrative = agg.get("narrative", {})
        text = narrative.get("snapshot", "").replace("<em>", "**").replace("</em>", "**")
        embeds.append({
            "title": "⚠ DXY POSITIONING - Crowded",
            "description": text,
            "color": EMBED_COLOR_EXTREME,
            "url": f"{site_url}#/DXY",
            "fields": [
                {"name": "Williams 3y", "value": str(agg["williams"]["w3y"]), "inline": True},
                {"name": "Weighted Net", "value": _format_number(agg["weighted_net"], True), "inline": True},
                {"name": "Неделя", "value": _format_number(agg["wow"], True), "inline": True},
            ],
            "footer": {"text": "Главная USD-позиция упёрлась в перцентиль"},
        })

    return embeds


async def _post_webhook(url: str, payload: dict) -> None:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                raise RuntimeError(f"Discord webhook {resp.status}: {text[:200]}")


async def post_weekly(snapshot: dict, site_url: str = "https://crypt0punky.github.io/tos-stats/") -> None:
    """Запостить weekly digest + (если есть extreme) follow-up post с алертами.

    Всё идёт в один webhook DISCORD_WEBHOOK_WEEKLY -- последовательно.
    Между постами 2 сек паузы (Discord webhook rate: 30 req / 60s per channel).
    """
    weekly_url = os.environ.get("DISCORD_WEBHOOK_WEEKLY")
    if not weekly_url:
        log.warning("DISCORD_WEBHOOK_WEEKLY not set, skipping Discord post")
        return

    # 1. Основной embed.
    weekly_embed = _build_weekly_embed(snapshot, site_url)
    await _post_webhook(weekly_url, {
        "username": "TOS Stats",
        "embeds": [weekly_embed],
    })
    log.info("Posted weekly embed")

    # 2. Extreme follow-up если есть.
    alert_embeds = _build_alerts_embeds(snapshot, site_url)
    if not alert_embeds:
        log.info("No extreme tags this week, no follow-up")
        return

    await asyncio.sleep(2)  # rate-limit friendly

    swing_role = os.environ.get("DISCORD_SWING_ROLE_ID")
    if swing_role:
        content = f"<@&{swing_role}> экстремальный positioning на неделе"
    else:
        content = "⚠ Экстремальный positioning на неделе"

    # Discord limit: 10 embeds per webhook call. У нас максимум 7 + DXY = 8 - влезаем.
    await _post_webhook(weekly_url, {
        "username": "TOS Stats",
        "content": content,
        "embeds": alert_embeds,
        "allowed_mentions": {"roles": [swing_role] if swing_role else []},
    })
    log.info("Posted %d extreme alert embed(s) as follow-up", len(alert_embeds))
