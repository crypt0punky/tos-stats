"""tos-stats main entry.

Используй `python -m ingest.run` для запуска pipeline.
Этот файл оставлен для совместимости со старыми инструкциями.
"""
import asyncio
import sys

from ingest.run import main


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
