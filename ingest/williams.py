"""Williams Commercials Index: процент position в N-летнем диапазоне.

  W = (current - min_N) / (max_N - min_N) × 100

Делается на NET position Asset Managers через три окна: 3y, 1y, 6m.

Также здесь же tag generation (extreme / stretched / momentum / neutral) -
вынесено сюда же потому что использует те же multi-window данные.
"""

import logging
import statistics
from dataclasses import dataclass

from . import config

log = logging.getLogger(__name__)

WEEKS_3Y = 156
WEEKS_1Y = 52
WEEKS_6M = 26
WEEKS_6M_SIGMA = 26  # окно для расчёта σ нормы


@dataclass
class PairMetrics:
    """Все производные метрики по паре. Готов для подачи в JSON snapshot."""
    pair: str
    am_net: int
    am_wow: int
    am_mom: int
    am_3m: int
    lf_net: int
    lf_wow: int
    dealer_net: int
    dealer_wow: int
    oi: int
    oi_wow: int
    williams_3y: int  # 0..100
    williams_1y: int
    williams_6m: int
    tag: str  # extreme / stretched / momentum / neutral
    sigma_6m: float  # σ от 6-мес normы для AM WoW дельт


def _williams_percentile(current: int, history: list[int]) -> int:
    """Возвращает percentile current в historical окне (0..100).

    Если history пустая или одно значение -- возвращаем 50 (mid).
    """
    if not history:
        return 50
    lo = min(history)
    hi = max(history)
    if hi == lo:
        return 50
    pct = (current - lo) / (hi - lo) * 100
    return max(0, min(100, int(round(pct))))


def _wow_sigma(history_am_net: list[int]) -> float:
    """σ недельных дельт AM Net за последние 6 месяцев.

    history_am_net - список am_net в обратном хронологическом порядке (свежие первые).
    Возвращает σ дельт. Если данных мало -- возвращает 0.
    """
    if len(history_am_net) < 4:
        return 0.0
    # Берём окно для расчёта (новые -> старые), считаем разницы между соседями.
    window = history_am_net[: WEEKS_6M_SIGMA + 1]
    deltas = [window[i] - window[i + 1] for i in range(len(window) - 1)]
    if len(deltas) < 2:
        return 0.0
    try:
        return float(statistics.stdev(deltas))
    except statistics.StatisticsError:
        return 0.0


def _classify_tag(williams_3y: int, am_wow: int, sigma_6m: float) -> str:
    """Тег для пары на основе Williams 3y + WoW σ-magnitude."""
    t = config.TAG_THRESHOLDS
    if williams_3y >= t["extreme_high"] or williams_3y <= t["extreme_low"]:
        return "extreme"
    if williams_3y >= t["stretched_high"] or williams_3y <= t["stretched_low"]:
        return "stretched"
    if sigma_6m > 0 and abs(am_wow) >= t["momentum_sigma"] * sigma_6m:
        return "momentum"
    return "neutral"


def compute_pair_metrics(pair: str, history_rows: list) -> PairMetrics:
    """Главная функция. Принимает history_rows (sqlite3.Row или dict),
    в обратном хронологическом порядке (свежие первые).

    Возвращает PairMetrics для текущего snapshot.

    Минимум: нужен хотя бы 1 row. Идеально -- 156+ для полного 3y окна.
    """
    if not history_rows:
        raise ValueError(f"No history for {pair}")

    cur = history_rows[0]
    prev = history_rows[1] if len(history_rows) > 1 else None
    prev_4w = history_rows[4] if len(history_rows) > 4 else None  # 4 недели назад
    prev_13w = history_rows[13] if len(history_rows) > 13 else None  # ~3 месяца назад

    # Деление на длинные/короткие окна для Williams.
    am_history_3y = [r["am_net"] for r in history_rows[:WEEKS_3Y]]
    am_history_1y = am_history_3y[:WEEKS_1Y]
    am_history_6m = am_history_3y[:WEEKS_6M]

    am_net = cur["am_net"]
    am_wow = (am_net - prev["am_net"]) if prev else 0
    am_mom = (am_net - prev_4w["am_net"]) if prev_4w else 0
    am_3m = (am_net - prev_13w["am_net"]) if prev_13w else 0

    lf_net = cur["lf_net"]
    lf_wow = (lf_net - prev["lf_net"]) if prev else 0

    dealer_net = cur["dealer_net"]
    dealer_wow = (dealer_net - prev["dealer_net"]) if prev else 0

    oi = cur["open_interest"]
    oi_wow = (oi - prev["open_interest"]) if prev else 0

    w3y = _williams_percentile(am_net, am_history_3y)
    w1y = _williams_percentile(am_net, am_history_1y)
    w6m = _williams_percentile(am_net, am_history_6m)

    sigma = _wow_sigma(am_history_3y)
    tag = _classify_tag(w3y, am_wow, sigma)

    return PairMetrics(
        pair=pair,
        am_net=am_net, am_wow=am_wow, am_mom=am_mom, am_3m=am_3m,
        lf_net=lf_net, lf_wow=lf_wow,
        dealer_net=dealer_net, dealer_wow=dealer_wow,
        oi=oi, oi_wow=oi_wow,
        williams_3y=w3y, williams_1y=w1y, williams_6m=w6m,
        tag=tag, sigma_6m=sigma,
    )
