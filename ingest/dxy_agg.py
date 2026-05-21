"""Кастомный композит "DXY positioning aggregate".

Берём NET позиции AM по всем 6 парам, инвертируем знак для тех где
long-валюта = short-USD (EUR/GBP/AUD/NZD -> sign -1), оставляем как есть
для USD-base пар (USDJPY/USDCAD -- там long AM = long USD, sign +1).

В config.PAIRS у нас всё `dxy_sign = -1` потому что AM-данные с CFTC
всегда котируются в "длинной валюте" (например JAPANESE YEN long = long JPY
= short USD/JPY = инверс USD-strength). Чтобы получить "сколько AM в
длинном USD" - инвертируем всё, потом взвешиваем по долям DXY.

Williams считается тем же методом что и для пары, только над агрегированной
серией.
"""

import logging
import statistics
from dataclasses import dataclass

from . import config
from .williams import _williams_percentile, WEEKS_3Y, WEEKS_1Y, WEEKS_6M

log = logging.getLogger(__name__)


@dataclass
class DXYAggregate:
    weighted_net: int
    wow: int
    mom: int
    m3: int
    williams_3y: int
    williams_1y: int
    williams_6m: int
    tag: str


def _weighted_usd_strength(pair_history: dict[str, list]) -> list[float]:
    """Собрать временную серию агрегата USD-strength по точкам.

    pair_history: pair -> list of rows (sqlite3.Row), новые первые.
    Все пары должны иметь одинаковые report_date в каждой позиции.

    Возвращает серию из len(rows) точек где [0] = свежая.
    """
    pairs = list(config.DXY_WEIGHTS.keys())
    total_w = sum(config.DXY_WEIGHTS.values())

    n_points = min(len(pair_history[p]) for p in pairs)
    series: list[float] = []
    for i in range(n_points):
        s = 0.0
        for p in pairs:
            sign = config.PAIRS[p]["dxy_sign"]
            w = config.DXY_WEIGHTS[p] / total_w
            am_net = pair_history[p][i]["am_net"]
            s += w * sign * am_net
        # Инвертируем общий знак: положительное значение agg = долгий USD.
        # Так как все pairs имеют sign=-1 (long currency = short USD),
        # после применения sign получим "минус long currency = +short USD".
        # Чтобы юзер читал положительное число как "long USD" - умножаем на -1.
        series.append(-s)
    return series


def compute_aggregate(pair_history: dict[str, list]) -> DXYAggregate:
    """Главная функция.

    pair_history: dict pair_id -> list of sqlite3.Row, отсортированных
                  в обратном хронологическом порядке.
    """
    pairs = list(config.DXY_WEIGHTS.keys())
    missing = [p for p in pairs if p not in pair_history or not pair_history[p]]
    if missing:
        raise ValueError(f"Missing history for DXY aggregate: {missing}")

    series = _weighted_usd_strength(pair_history)
    if len(series) < 2:
        raise ValueError(f"Not enough history for DXY aggregate: {len(series)} points")

    current = int(series[0])
    prev = int(series[1])
    prev_4w = int(series[4]) if len(series) > 4 else current
    prev_13w = int(series[13]) if len(series) > 13 else current

    wow = current - prev
    mom = current - prev_4w
    m3 = current - prev_13w

    history_3y = [int(v) for v in series[:WEEKS_3Y]]
    history_1y = history_3y[:WEEKS_1Y]
    history_6m = history_3y[:WEEKS_6M]

    w3y = _williams_percentile(current, history_3y)
    w1y = _williams_percentile(current, history_1y)
    w6m = _williams_percentile(current, history_6m)

    # Tag для агрегата по тем же thresholds.
    t = config.TAG_THRESHOLDS
    if w3y >= t["extreme_high"] or w3y <= t["extreme_low"]:
        tag = "extreme"
    elif w3y >= t["stretched_high"] or w3y <= t["stretched_low"]:
        tag = "stretched"
    else:
        # Momentum для агрегата считаем через σ WoW в полугодовом окне.
        try:
            deltas = [history_3y[i] - history_3y[i + 1] for i in range(min(26, len(history_3y) - 1))]
            sigma = statistics.stdev(deltas) if len(deltas) >= 2 else 0.0
        except statistics.StatisticsError:
            sigma = 0.0
        if sigma > 0 and abs(wow) >= t["momentum_sigma"] * sigma:
            tag = "momentum"
        else:
            tag = "neutral"

    return DXYAggregate(
        weighted_net=current, wow=wow, mom=mom, m3=m3,
        williams_3y=w3y, williams_1y=w1y, williams_6m=w6m, tag=tag,
    )
