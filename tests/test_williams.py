"""Smoke test для williams.compute_pair_metrics + dxy_agg.

Запуск:
  python -m pytest tests/ -v
или
  python -m tests.test_williams
"""

import unittest

from ingest import config
from ingest.williams import _williams_percentile, _classify_tag, compute_pair_metrics
from ingest.dxy_agg import _weighted_usd_strength
from ingest.run import normalize_history


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
        # Ряды УЖЕ развёрнуты normalize_history, то есть в смысле ярлыка пары:
        # у USDJPY/USDCAD минус = лонг иены/канадца = шорт доллара. Сырое
        # соглашение CFTC (там лонг иены был бы плюсом) сюда не доходит.
        pair_history = {
            "EURUSD": [_row(am_net=100000), _row(am_net=90000)],
            "GBPUSD": [_row(am_net=50000), _row(am_net=45000)],
            "USDJPY": [_row(am_net=-200000), _row(am_net=-190000)],  # шорт USD/JPY = лонг иены
            "AUDUSD": [_row(am_net=30000), _row(am_net=28000)],
            "USDCAD": [_row(am_net=-20000), _row(am_net=-18000)],   # шорт USD/CAD = лонг канадца
            "NZDUSD": [_row(am_net=10000), _row(am_net=8000)],
        }
        series = _weighted_usd_strength(pair_history)
        self.assertEqual(len(series), 2)
        # Управляющие во всех шести в длинной иностранной валюте -> шорт доллара.
        self.assertLess(series[0], 0)


class TestNormalizeHistory(unittest.TestCase):
    """Инверсия знака происходит ровно один раз, в run.normalize_history."""

    def test_raw_long_yen_becomes_short_usdjpy(self):
        # Сырой CFTC: лонг иены +100 (am_long 100 / am_short 0).
        raw = [_row(am_net=100, lf_net=40, dealer_net=-70, oi=1000)]
        out = normalize_history("USDJPY", raw)
        # Под ярлыком USD/JPY это шорт -100, стороны меняются местами.
        self.assertEqual(out[0]["am_net"], -100)
        self.assertEqual(out[0]["am_long"], 0)
        self.assertEqual(out[0]["am_short"], 100)
        # Остальные нетто тоже разворачиваются, open_interest и дата - нет.
        self.assertEqual(out[0]["lf_net"], -40)
        self.assertEqual(out[0]["dealer_net"], 70)
        self.assertEqual(out[0]["open_interest"], 1000)
        self.assertEqual(out[0]["report_date"], raw[0]["report_date"])
        # Исходная строка не тронута.
        self.assertEqual(raw[0]["am_net"], 100)

    def test_non_inverted_pair_untouched(self):
        raw = [_row(am_net=100)]
        self.assertIs(normalize_history("EURUSD", raw), raw)

    def test_backfill_normalizes_the_same_way(self):
        """backfill - второй вход в те же расчёты, знак там обязан разворачиваться.

        Проверяется ПОВЕДЕНИЕ, а не наличие импорта: backfill прогоняется целиком
        на подставных данных (сеть и запись на диск замоканы), и в готовом
        снапшоте знак у USDJPY/USDCAD должен быть уже развёрнут. Убери
        normalize_history из места загрузки - тест краснеет.
        """
        import asyncio
        from unittest import mock

        from ingest import backfill

        # Сырой CFTC по всем парам: лонг иностранной валюты, то есть плюс.
        raw = {p: [_row(am_net=100000 + i * 10, lf_net=1000, oi=500000,
                        report_date=f"2026-01-{(i % 28) + 1:02d}")
                   for i in range(30)]
               for p in config.PAIRS}
        captured = {}

        with mock.patch.object(backfill.db, "init_db"), \
             mock.patch.object(backfill.db, "save_reports", return_value=0), \
             mock.patch.object(backfill.fetch_cot, "fetch_all_pairs",
                               new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(backfill.db, "get_history",
                               side_effect=lambda p, weeks=200: raw[p]), \
             mock.patch.object(backfill.publish, "write_json",
                               side_effect=captured.update):
            rc = asyncio.run(backfill.backfill(skip_narrate=True))

        self.assertEqual(rc, 0)
        by_id = {p["id"]: p for p in captured["pairs"]}
        # Плюс на входе -> под ярлыком USD/JPY и USD/CAD это шорт.
        self.assertLess(by_id["USDJPY"]["am_net"], 0)
        self.assertLess(by_id["USDCAD"]["am_net"], 0)
        # Пары, которые разворачивать не надо, остались как есть.
        self.assertGreater(by_id["EURUSD"]["am_net"], 0)


class TestRunPipelineNormalizes(unittest.TestCase):
    """Основной путь (пятничный таймер) тоже обязан разворачивать знак.

    Дырка, найденная прогоном мутаций 21.09.2026: тест на backfill есть, а
    снятие вызова из run_pipeline не краснело ничем.
    """

    def test_run_pipeline_snapshot_is_normalized(self):
        import asyncio
        from unittest import mock

        from ingest import run

        raw = {p: [_row(am_net=100000 + i * 10, lf_net=1000, oi=500000,
                        report_date=f"2026-01-{(i % 28) + 1:02d}")
                   for i in range(30)]
               for p in config.PAIRS}
        captured = {}

        with mock.patch.object(run.db, "init_db"), \
             mock.patch.object(run.db, "get_latest_date", return_value="2026-01-01"), \
             mock.patch.object(run.fetch_cot, "fetch_with_retry",
                               new=mock.AsyncMock(return_value=[{"stub": 1}])), \
             mock.patch.object(run.db, "save_reports", return_value=1), \
             mock.patch.object(run.db, "trim_old", return_value=0), \
             mock.patch.object(run.db, "get_history",
                               side_effect=lambda p, weeks=200: raw[p]), \
             mock.patch.object(run.narrate, "generate_all",
                               new=mock.AsyncMock(return_value={})), \
             mock.patch.object(run.narrate, "generate_tldr",
                               new=mock.AsyncMock(return_value="stub")), \
             mock.patch.object(run.publish, "write_json",
                               side_effect=captured.update), \
             mock.patch.object(run, "git_push"), \
             mock.patch.object(run.post_discord, "post_weekly",
                               new=mock.AsyncMock()):
            stats = asyncio.run(run.run_pipeline())

        self.assertEqual(stats["pairs"], len(config.PAIRS))
        by_id = {p["id"]: p for p in captured["pairs"]}
        self.assertLess(by_id["USDJPY"]["am_net"], 0)
        self.assertLess(by_id["USDCAD"]["am_net"], 0)
        self.assertGreater(by_id["EURUSD"]["am_net"], 0)
        # Композит: все шесть в лонге иностранной валюты -> шорт доллара.
        self.assertLess(captured["dxy_aggregate"]["weighted_net"], 0)


class TestSignEndToEnd(unittest.TestCase):
    def test_long_eur_and_long_jpy_both_short_dollar(self):
        """Лонг евро и лонг иены у управляющих - это шорт доллара в обоих случаях.

        Данные подаются СЫРЫМИ, как их отдаёт CFTC (лонг иены = плюс), и
        проходят через normalize_history - то есть проверяется вся цепочка
        знака, а не только композит.
        """
        def basket(**raw_am):
            per_pair = {}
            for pair, am in raw_am.items():
                per_pair[pair] = normalize_history(pair, [_row(am_net=am)])
            return _weighted_usd_strength(per_pair)[0]

        # Управляющие в лонге евро (остальные по нулю) -> шорт доллара.
        long_eur = basket(EURUSD=100000, GBPUSD=0, USDJPY=0,
                          AUDUSD=0, USDCAD=0, NZDUSD=0)
        self.assertLess(long_eur, 0)

        # Управляющие в лонге иены - сырой CFTC плюс - тоже шорт доллара.
        long_jpy = basket(EURUSD=0, GBPUSD=0, USDJPY=100000,
                          AUDUSD=0, USDCAD=0, NZDUSD=0)
        self.assertLess(long_jpy, 0)

        # То же правило обязано держать КАЖДУЮ пару по отдельности: лонг
        # иностранной валюты в любой ноге корзины = шорт доллара. Без этого
        # подмена знака у одной пары (например USDCAD) проходит незамеченной.
        for pair in config.PAIRS:
            legs = {p: 0 for p in config.PAIRS}
            legs[pair] = 100000
            with self.subTest(pair=pair):
                self.assertLess(basket(**legs), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
