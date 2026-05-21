"""SQLite слой для tos-stats.

Schema:
  reports - сырые COT-репорты с CFTC, одна строка = одна пара × одна неделя.

Идемпотентность: INSERT OR REPLACE на (pair, report_date).
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterable

from . import config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    pair TEXT NOT NULL,
    report_date TEXT NOT NULL,  -- YYYY-MM-DD (Tuesday-as-of-date)
    am_long INTEGER NOT NULL,
    am_short INTEGER NOT NULL,
    am_net INTEGER NOT NULL,
    lf_long INTEGER NOT NULL,
    lf_short INTEGER NOT NULL,
    lf_net INTEGER NOT NULL,
    dealer_long INTEGER NOT NULL,
    dealer_short INTEGER NOT NULL,
    dealer_net INTEGER NOT NULL,
    other_long INTEGER NOT NULL,
    other_short INTEGER NOT NULL,
    other_net INTEGER NOT NULL,
    open_interest INTEGER NOT NULL,
    PRIMARY KEY (pair, report_date)
);
CREATE INDEX IF NOT EXISTS idx_reports_pair_date ON reports(pair, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(report_date DESC);
"""


@contextmanager
def connect(db_path: str | None = None):
    """Контекст SQLite-соединения. Создаёт parent dir если надо."""
    path = db_path or config.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Создать таблицы и индексы. Идемпотентно."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    log.info("DB initialized at %s", db_path or config.DB_PATH)


def save_reports(rows: Iterable[dict], db_path: str | None = None) -> int:
    """INSERT OR REPLACE сырых COT-репортов.

    Возвращает количество новых (или обновлённых) строк.
    """
    cols = (
        "pair", "report_date",
        "am_long", "am_short", "am_net",
        "lf_long", "lf_short", "lf_net",
        "dealer_long", "dealer_short", "dealer_net",
        "other_long", "other_short", "other_net",
        "open_interest",
    )
    placeholders = ", ".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO reports ({', '.join(cols)}) VALUES ({placeholders})"

    count = 0
    with connect(db_path) as conn:
        for r in rows:
            conn.execute(sql, tuple(r[c] for c in cols))
            count += 1
    return count


def get_history(
    pair: str,
    weeks: int = config.HISTORY_WEEKS_KEEP,
    db_path: str | None = None,
) -> list[sqlite3.Row]:
    """Последние N недель отчётов по паре, новые первыми."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reports
             WHERE pair = ?
             ORDER BY report_date DESC
             LIMIT ?
            """,
            (pair, weeks),
        ).fetchall()
    return list(rows)


def get_latest_date(db_path: str | None = None) -> str | None:
    """Самая свежая report_date в БД (любая пара). YYYY-MM-DD или None если БД пустая."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(report_date) AS d FROM reports").fetchone()
    return row["d"] if row and row["d"] else None


def trim_old(weeks_keep: int = config.HISTORY_WEEKS_KEEP, db_path: str | None = None) -> int:
    """Удалить отчёты старше N недель от самой свежей даты (для каждой пары независимо)."""
    deleted = 0
    with connect(db_path) as conn:
        for pair in config.PAIRS.keys():
            cutoff_row = conn.execute(
                """
                SELECT report_date FROM reports
                 WHERE pair = ?
                 ORDER BY report_date DESC
                 LIMIT 1 OFFSET ?
                """,
                (pair, weeks_keep),
            ).fetchone()
            if cutoff_row:
                cur = conn.execute(
                    "DELETE FROM reports WHERE pair = ? AND report_date < ?",
                    (pair, cutoff_row["report_date"]),
                )
                deleted += cur.rowcount
    if deleted:
        log.info("Trimmed %d old reports", deleted)
    return deleted
