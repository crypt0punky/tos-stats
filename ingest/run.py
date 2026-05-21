"""Entry point pipeline.

Запускается cron каждую пятницу 21:32 CET.

Шаги:
  1. fetch_cot.fetch_with_retry() - тянет свежие данные из CFTC
  2. db.save_reports() - пишем в SQLite
  3. williams.compute_pair_metrics() + dxy_agg.compute_aggregate()
  4. narrate.generate_all() + narrate.generate_tldr() - AI слой
  5. publish.build_snapshot() + write_json() - пишем web/data/current.json
  6. post_discord.post_weekly() - Discord embed
  7. git commit + push (через subprocess)

При любой uncaught exception - alerter.notify_exception() в Telegram.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import (
    alerter,
    config,
    db,
    dxy_agg,
    fetch_cot,
    narrate,
    post_discord,
    publish,
    williams,
)


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / f"weekly-{datetime.now().strftime('%Y%m%d')}.log"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def git_push(repo_root: Path, message: str) -> None:
    """git add -> commit -> push. Falls если что-то не так -- alerter получит."""
    subprocess.check_call(["git", "-C", str(repo_root), "add", "web/data/"], stderr=subprocess.STDOUT)
    # commit может вернуть exit 1 если нет diff -- это OK
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", message],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        if "nothing to commit" in (proc.stdout + proc.stderr).lower():
            logging.warning("Nothing to commit (data identical?)")
            return
        raise RuntimeError(f"git commit failed: {proc.stdout}\n{proc.stderr}")

    subprocess.check_call(["git", "-C", str(repo_root), "push"], stderr=subprocess.STDOUT)


async def run_pipeline() -> dict:
    """Главная функция. Возвращает stats dict для финального алерта."""
    log = logging.getLogger("run")

    # 1. Init DB.
    db.init_db()

    # 2. Fetch CFTC.
    since_date = db.get_latest_date()
    log.info("Last seen date in DB: %s", since_date or "(empty)")
    rows = await fetch_cot.fetch_with_retry(since_date=since_date)
    if not rows:
        log.warning("No new rows after retry. CFTC delayed or holiday week.")
        # Не падаем -- ждём следующей пятницы. Алерт WARNING.
        await alerter.notify(
            "CFTC не выложили данные за окно retry. Проверь cftc.gov на holiday-сдвиг.",
            level="WARNING",
        )
        return {"new_rows": 0}

    new_rows = db.save_reports(rows)
    db.trim_old()
    log.info("Saved %d rows to DB", new_rows)

    # 3. Считаем метрики по каждой паре + DXY.
    history_by_pair = {}
    for pair in config.PAIRS.keys():
        history_by_pair[pair] = db.get_history(pair, weeks=200)

    pair_metrics = []
    for pair in config.PAIRS.keys():
        h = history_by_pair[pair]
        if not h:
            log.error("No history for %s after fetch", pair)
            continue
        metrics = williams.compute_pair_metrics(pair, h)
        pair_metrics.append(metrics)

    aggregate = dxy_agg.compute_aggregate(history_by_pair)

    log.info(
        "Computed metrics: pairs=%d, DXY W3y=%d (%s)",
        len(pair_metrics), aggregate.williams_3y, aggregate.tag,
    )

    # 4. AI narrative.
    narratives = await narrate.generate_all(pair_metrics, history_by_pair, aggregate)
    tldr = await narrate.generate_tldr(pair_metrics, aggregate, narratives)

    # 5. Build snapshot + write JSON.
    # Берём report_date из реальных данных (все пары на одну дату):
    report_date = history_by_pair[next(iter(config.PAIRS))][0]["report_date"]
    snapshot = publish.build_snapshot(pair_metrics, aggregate, narratives, tldr, report_date)
    publish.attach_history(snapshot, history_by_pair)
    publish.write_json(snapshot)

    # 6. Git push (только если в репо).
    repo_root = config.REPO_ROOT
    if (repo_root / ".git").exists():
        git_push(repo_root, f"data: week {snapshot['week']} {snapshot['year']} snapshot")
        log.info("git push completed")
    else:
        log.warning("No .git in %s, skipping push (dev mode?)", repo_root)

    # 7. Discord.
    site_url = os.environ.get("SITE_URL", "https://crypt0punky.github.io/tos-stats/")
    await post_discord.post_weekly(snapshot, site_url=site_url)

    extreme_count = sum(1 for m in pair_metrics if m.tag == "extreme")
    if aggregate.tag == "extreme":
        extreme_count += 1

    return {
        "week": snapshot["week"],
        "new_rows": new_rows,
        "pairs": len(pair_metrics),
        "extreme_count": extreme_count,
    }


async def main() -> int:
    setup_logging()
    log = logging.getLogger("run")
    log.info("=== tos-stats pipeline start ===")

    try:
        stats = await run_pipeline()
        log.info("=== pipeline OK: %s ===", stats)
        await alerter.notify_success(stats)
        return 0
    except Exception as exc:
        log.exception("Pipeline failed")
        await alerter.notify_exception("Pipeline failed", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
