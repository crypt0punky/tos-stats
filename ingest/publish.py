"""Сборка итогового JSON snapshot для frontend и запись в web/data/.

Также копирует в архив (web/data/archive/YYYY-WNN.json).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def _iso_week(report_date_str: str) -> tuple[int, int]:
    """YYYY-MM-DD -> (year, iso_week_number)."""
    d = datetime.strptime(report_date_str, "%Y-%m-%d")
    iso = d.isocalendar()
    return iso.year, iso.week


def build_snapshot(
    pair_metrics_list: list,
    aggregate,
    narratives: dict,
    tldr: str,
    report_date: str,
) -> dict:
    """Финальный JSON в формате который потребляет frontend.

    Контракт зафиксирован в SESSIONS_LOG (2026-05-20 запись).
    """
    year, week = _iso_week(report_date)

    pairs_out = []
    for m in pair_metrics_list:
        n = narratives.get(m.pair, {})
        pairs_out.append({
            "id": m.pair,
            "tag": m.tag,
            "williams": {
                "w3y": m.williams_3y,
                "w1y": m.williams_1y,
                "w6m": m.williams_6m,
            },
            "am_net": m.am_net,
            "am_wow": m.am_wow,
            "am_mom": m.am_mom,
            "am_3m": m.am_3m,
            "lf_net": m.lf_net,
            "lf_wow": m.lf_wow,
            "dealers_net": m.dealer_net,
            "dealers_wow": m.dealer_wow,
            "oi": m.oi,
            "oi_wow": m.oi_wow,
            "narrative": {
                "snapshot": n.get("snapshot", ""),
                "dynamics": n.get("dynamics", ""),
                "historical": n.get("historical", ""),
                "cross_pair": n.get("cross_pair", ""),
            },
            "watch": n.get("watch", []),
        })

    n_agg = narratives.get("DXY", {})
    snapshot = {
        "week": week,
        "year": year,
        "report_date": report_date,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tldr": tldr,
        "pairs": pairs_out,
        "dxy_aggregate": {
            "tag": aggregate.tag,
            "williams": {
                "w3y": aggregate.williams_3y,
                "w1y": aggregate.williams_1y,
                "w6m": aggregate.williams_6m,
            },
            "weighted_net": aggregate.weighted_net,
            "wow": aggregate.wow,
            "mom": aggregate.mom,
            "m3": aggregate.m3,
            "narrative": {
                "snapshot": n_agg.get("snapshot", ""),
                "dynamics": n_agg.get("dynamics", ""),
                "historical": n_agg.get("historical", ""),
                "cross_pair": n_agg.get("cross_pair", ""),
            },
        },
        "history": {},  # заполняется ниже
    }

    return snapshot


def attach_history(snapshot: dict, history_by_pair: dict[str, list]) -> None:
    """Добавить в snapshot последние N репортов для таблицы на detail page.

    Изменяет snapshot in-place. Формат: history[pair_id] = list rows (новые первые).
    """
    n = config.TABLE_WEEKS_SHOW
    for pair, rows in history_by_pair.items():
        snapshot["history"][pair] = [
            {
                "date": r["report_date"],
                "am": r["am_net"],
                "lf": r["lf_net"],
                "oi": r["open_interest"],
                # WoW delta считаем поверх (предыдущая строка - текущая):
                # frontend ожидает уже посчитанные дельты.
            }
            for r in rows[: n + 1]  # +1 чтобы посчитать delta последнего
        ]

        # Вычислить WoW дельты в history.
        h = snapshot["history"][pair]
        for i in range(len(h)):
            if i + 1 < len(h):
                h[i]["am_d"] = h[i]["am"] - h[i + 1]["am"]
                h[i]["lf_d"] = h[i]["lf"] - h[i + 1]["lf"]
            else:
                h[i]["am_d"] = 0
                h[i]["lf_d"] = 0
        # Обрезаем до N строк (последний row нужен был только для delta).
        snapshot["history"][pair] = h[:n]


def write_json(snapshot: dict) -> None:
    """Записать current.json + archive copy."""
    config.WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    current_path = config.WEB_DATA_DIR / "current.json"
    archive_name = f"{snapshot['year']}-W{snapshot['week']:02d}.json"
    archive_path = config.ARCHIVE_DIR / archive_name

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)

    current_path.write_text(payload, encoding="utf-8")
    archive_path.write_text(payload, encoding="utf-8")

    log.info("Wrote %s (%d bytes) + archive %s", current_path, len(payload), archive_name)
