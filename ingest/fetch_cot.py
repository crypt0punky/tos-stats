"""Загрузка свежих COT-репортов с CFTC Socrata API.

Schema TFF: https://publicreporting.cftc.gov/resource/gpe5-46if.json
Поля которые нам нужны:
  - report_date_as_yyyy_mm_dd: "2026-05-19T00:00:00.000"
  - cftc_contract_market_code: "099741"
  - dealer_positions_long_all / dealer_positions_short_all
  - asset_mgr_positions_long / asset_mgr_positions_short
  - lev_money_positions_long / lev_money_positions_short
  - other_rept_positions_long / other_rept_positions_short
  - open_interest_all

CFTC возвращает все значения как строки -- конвертируем в int.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from . import config

log = logging.getLogger(__name__)


class CFTCFetchError(Exception):
    """Не удалось получить данные с CFTC."""


def _to_int(s: str | None) -> int:
    """CFTC отдаёт числа строками. Пустая строка / None -> 0."""
    if s is None or s == "":
        return 0
    return int(float(s))


def _parse_date(raw: str) -> str:
    """CFTC формат: '2026-05-19T00:00:00.000'. Возвращаем 'YYYY-MM-DD'."""
    return raw.split("T", 1)[0]


def _normalize_row(raw: dict, pair: str) -> dict:
    """Сырой CFTC ряд -> наш формат для db.save_reports."""
    am_long = _to_int(raw.get("asset_mgr_positions_long"))
    am_short = _to_int(raw.get("asset_mgr_positions_short"))
    lf_long = _to_int(raw.get("lev_money_positions_long"))
    lf_short = _to_int(raw.get("lev_money_positions_short"))
    de_long = _to_int(raw.get("dealer_positions_long_all"))
    de_short = _to_int(raw.get("dealer_positions_short_all"))
    ot_long = _to_int(raw.get("other_rept_positions_long"))
    ot_short = _to_int(raw.get("other_rept_positions_short"))

    return {
        "pair": pair,
        "report_date": _parse_date(raw["report_date_as_yyyy_mm_dd"]),
        "am_long": am_long,
        "am_short": am_short,
        "am_net": am_long - am_short,
        "lf_long": lf_long,
        "lf_short": lf_short,
        "lf_net": lf_long - lf_short,
        "dealer_long": de_long,
        "dealer_short": de_short,
        "dealer_net": de_long - de_short,
        "other_long": ot_long,
        "other_short": ot_short,
        "other_net": ot_long - ot_short,
        "open_interest": _to_int(raw.get("open_interest_all")),
    }


async def _fetch_pair(
    session: aiohttp.ClientSession,
    pair: str,
    code: str,
    since_date: str | None,
    limit: int = 200,
) -> list[dict]:
    """Получить отчёты для одной пары после since_date (или все если None).

    Возвращает уже normalized список dicts готовых к db.save_reports.
    """
    params = {
        "cftc_contract_market_code": code,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(limit),
    }
    if since_date:
        # SoQL where clause: даты на CFTC в формате 'YYYY-MM-DDT00:00:00.000'
        params["$where"] = f"report_date_as_yyyy_mm_dd > '{since_date}T00:00:00.000'"

    async with session.get(config.CFTC_TFF_URL, params=params, timeout=30) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise CFTCFetchError(
                f"CFTC TFF returned {resp.status} for {pair} ({code}): {text[:200]}"
            )
        raw_rows = await resp.json()

    log.info("CFTC TFF %s (%s): %d rows since %s", pair, code, len(raw_rows), since_date or "BEGINNING")
    return [_normalize_row(r, pair) for r in raw_rows]


async def fetch_all_pairs(since_date: str | None = None, limit: int = 200) -> list[dict]:
    """Скачать свежие отчёты по всем парам из config.PAIRS.

    Если since_date указана - возвращает только новые отчёты после неё.
    Если None - возвращает limit самых свежих (для бэкфилла).

    Возвращает один общий список dicts готовых к db.save_reports.
    """
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            _fetch_pair(session, pair, meta["code"], since_date, limit)
            for pair, meta in config.PAIRS.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[dict] = []
    errors: list[str] = []
    for pair, res in zip(config.PAIRS.keys(), results):
        if isinstance(res, Exception):
            errors.append(f"{pair}: {res}")
            log.error("fetch failed for %s: %s", pair, res)
            continue
        out.extend(res)

    if errors:
        raise CFTCFetchError(f"Partial fetch failure: {', '.join(errors)}")
    return out


async def fetch_with_retry(since_date: str | None = None) -> list[dict]:
    """Загрузка с retry если CFTC ещё не выложили свежие данные.

    Возвращает rows. Если за все попытки пусто - возвращает пустой список
    (caller решает что делать -- скорее всего holiday week, ждать следующего раза).
    """
    for attempt in range(1, config.RETRY_MAX_ATTEMPTS + 1):
        try:
            rows = await fetch_all_pairs(since_date=since_date)
        except CFTCFetchError as e:
            log.warning("Attempt %d/%d: %s", attempt, config.RETRY_MAX_ATTEMPTS, e)
            if attempt == config.RETRY_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(config.RETRY_DELAY_SEC)
            continue

        if rows:
            log.info("Fetched %d new rows on attempt %d", len(rows), attempt)
            return rows

        # Пустой ответ значит CFTC ещё не выложили обновление.
        log.info("Attempt %d: no new data, sleep %ds", attempt, config.RETRY_DELAY_SEC)
        if attempt == config.RETRY_MAX_ATTEMPTS:
            return []
        await asyncio.sleep(config.RETRY_DELAY_SEC)

    return []


# ----- Standalone smoke test -----

async def _smoke_main():
    """Запуск напрямую: python -m ingest.fetch_cot
    Тянет последние 3 отчёта по EUR/USD и печатает.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        rows = await _fetch_pair(session, "EURUSD", config.PAIRS["EURUSD"]["code"], None, 3)

    print(f"\n=== EURUSD last 3 reports ===")
    for r in rows:
        print(
            f"  {r['report_date']}: AM Net {r['am_net']:>8}  "
            f"LF Net {r['lf_net']:>8}  OI {r['open_interest']:>9}"
        )


if __name__ == "__main__":
    asyncio.run(_smoke_main())
