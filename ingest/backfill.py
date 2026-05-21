"""One-shot backfill: тянет всю историю CFTC (200 недель), считает метрики,
генерирует current.json с реальными числами + AI narrative.

НЕ постит в Discord и НЕ делает git push -- это делается отдельно через
git add web/data/ && git commit && git push после успешного backfill.

Использование:
  cd ~/Desktop/tos-tech/tos-stats
  source .venv/bin/activate  # или python3 -m venv .venv && pip install -r requirements.txt
  cp .env.example .env       # заполнить ANTHROPIC_API_KEY минимум
  python -m ingest.backfill

Result: web/data/current.json содержит свежий снапшот.
"""

import asyncio
import logging
import sys
from pathlib import Path

from . import (
    config,
    db,
    dxy_agg,
    fetch_cot,
    narrate,
    publish,
    williams,
)


async def backfill(skip_narrate: bool = False) -> int:
    log = logging.getLogger("backfill")
    log.info("=== backfill start ===")

    # 1. Init DB.
    db.init_db()

    # 2. Полный пулл по всем парам (limit 200 = ~3.8 года).
    log.info("Fetching full history from CFTC...")
    rows = await fetch_cot.fetch_all_pairs(since_date=None, limit=200)
    log.info("Got %d total rows across %d pairs", len(rows), len(config.PAIRS))

    new_rows = db.save_reports(rows)
    log.info("Saved %d rows to DB", new_rows)

    # 3. Считаем метрики.
    history_by_pair = {p: db.get_history(p, weeks=200) for p in config.PAIRS}
    missing = [p for p, h in history_by_pair.items() if not h]
    if missing:
        log.error("Empty history for: %s. CFTC returned no rows -- check contract codes.", missing)
        return 1

    pair_metrics = [
        williams.compute_pair_metrics(p, history_by_pair[p])
        for p in config.PAIRS
    ]
    aggregate = dxy_agg.compute_aggregate(history_by_pair)
    log.info(
        "Metrics: pairs=%d, DXY W3y=%d (%s)",
        len(pair_metrics), aggregate.williams_3y, aggregate.tag,
    )

    # 4. AI narrative.
    if skip_narrate:
        log.info("Skipping AI narrative (--skip-narrate)")
        narratives = {m.pair: narrate._fallback_narrative(m) for m in pair_metrics}
        narratives["DXY"] = {
            "snapshot": f"DXY aggregate Williams 3y {aggregate.williams_3y}, tag {aggregate.tag}.",
            "dynamics": "AI слой пропущен в backfill.",
            "historical": "AI слой пропущен в backfill.",
            "cross_pair": "AI слой пропущен в backfill.",
            "watch": [],
            "_source": "fallback",
        }
        tldr = f"Backfill завершён. DXY agg Williams 3y {aggregate.williams_3y}. AI narrative прилетит при следующем еженедельном запуске."
    else:
        log.info("Generating AI narrative (Sonnet, ~30-60 sec)...")
        narratives = await narrate.generate_all(pair_metrics, history_by_pair, aggregate)
        tldr = await narrate.generate_tldr(pair_metrics, aggregate, narratives)

    # 5. Snapshot + JSON write.
    report_date = history_by_pair[next(iter(config.PAIRS))][0]["report_date"]
    snapshot = publish.build_snapshot(pair_metrics, aggregate, narratives, tldr, report_date)
    publish.attach_history(snapshot, history_by_pair)
    publish.write_json(snapshot)

    log.info("=== backfill OK: week %d, report_date %s ===", snapshot["week"], report_date)
    log.info("Next: cd to repo, git add web/data/ && git commit && git push")
    return 0


async def _main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    skip_narrate = "--skip-narrate" in sys.argv
    return await backfill(skip_narrate=skip_narrate)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
