"""Smoke test для williams.compute_pair_metrics + dxy_agg.

Запуск:
  python -m pytest tests/ -v
или
  python -m tests.test_williams
"""

import unittest

from ingest.williams import _williams_percentile, _classify_tag, compute_pair_metrics
from ingest.dxy_agg import _weighted_usd_strength


def _row(am_net=0, lf_net=0, dealer_net=0, oi=0, report_date="2026-05-19"):
    """Утилита для создания fake report row."""
    return {
        "pair": "TEST",
        "report_date": report_date,
        "am_long": max(am_net, 0), "am_short": max(-am_net, 0), "am_net": am_net,
        "lf_long": max(lf_net, 0), "lf_short": max(-lf_net, 0), "lf_net": lf_net,
        "dealer_long": max(dealer_net, 0), "dealer_short": max(-dealer_net, 0), "dealer_net": dealer_net,
        "other_long": 0, "other_short": 0, "other_net": 0,
        "open_interest": oi,
    }


class TestWilliamsPercentile(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(_williams_percentile(100, []), 50)

    def test_single_value(self):
        # Если все значения одинаковые - 50.
        self.assertEqual(_williams_percentile(100, [100, 100, 100]), 50)

    def test_at_max(self):
        self.assertEqual(_williams_percentile(100, [0, 50, 100]), 100)

    def test_at_min(self):
        self.assertEqual(_williams_percentile(0, [0, 50, 100]), 0)

    def test_middle(self):
        self.assertEqual(_williams_percentile(50, [0, 50, 100]), 50)


class TestClassifyTag(unittest.TestCase):
    def test_extreme_high(self):
        self.assertEqual(_classify_tag(95, 0, 0), "extreme")

    def test_extreme_low(self):
        self.assertEqual(_classify_tag(5, 0, 0), "extreme")

    def test_stretched_high(self):
        self.assertEqual(_classify_tag(85, 0, 0), "stretched")

    def test_stretched_low(self):
        self.assertEqual(_classify_tag(15, 0, 0), "stretched")

    def test_momentum(self):
        # WoW > 1.5σ от 6-мес нормы.
        self.assertEqual(_classify_tag(50, 20000, 10000.0), "momentum")

    def test_neutral(self):
        self.assertEqual(_classify_tag(50, 500, 10000.0), "neutral")


class TestComputePairMetrics(unittest.TestCase):
    def test_basic_metrics(self):
        # 30 недель данных, последняя - +200,000, предыдущая - +180,000.
        history = []
        for i in range(30):
            am = 150000 + (30 - i) * 1500  # растущий
            history.append(_row(am_net=am, lf_net=am // 10, oi=500000, report_date=f"2026-{((i + 1) % 12) + 1:02d}-01"))

        metrics = compute_pair_metrics("EURUSD", history)
        self.assertEqual(metrics.pair, "EURUSD")
        # Свежее значение должно быть максимальным -> Williams близко к 100.
        self.assertGreaterEqual(metrics.williams_3y, 95)
        self.assertEqual(metrics.am_net, history[0]["am_net"])
        self.assertGreater(metrics.am_wow, 0)


class TestDXYWeightedStrength(unittest.TestCase):
    def test_basic_aggregate(self):
        # Все пары "long currency" -> aggregate должен быть отрицательным (short USD).
        pair_history = {
            "EURUSD": [_row(am_net=100000), _row(am_net=90000)],
            "GBPUSD": [_row(am_net=50000), _row(am_net=45000)],
            "USDJPY": [_row(am_net=-200000), _row(am_net=-190000)],  # long JPY = short USD/JPY
            "AUDUSD": [_row(am_net=30000), _row(am_net=28000)],
            "USDCAD": [_row(am_net=-20000), _row(am_net=-18000)],   # long CAD = short USD/CAD
            "NZDUSD": [_row(am_net=10000), _row(am_net=8000)],
        }
        series = _weighted_usd_strength(pair_history)
        self.assertEqual(len(series), 2)
        # Все pairs идут в long-валюту -> AM в коротком USD -> series должен быть отрицательный.
        self.assertLess(series[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
