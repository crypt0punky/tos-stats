"""Конфигурация tos-stats. Все константы в одном месте.

Меняешь pair scope - правишь PAIRS. Меняешь веса DXY - правишь DXY_WEIGHTS.
"""

import os
from pathlib import Path

# ----- Paths -----

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DATA_DIR = REPO_ROOT / "web" / "data"
ARCHIVE_DIR = WEB_DATA_DIR / "archive"
LOG_DIR = REPO_ROOT / "logs"

DB_PATH = os.environ.get("TOS_STATS_DB", "/root/tos_bots/data/stats.db")

# ----- Instruments -----

# CFTC contract codes для TFF report.
# Получены с https://publicreporting.cftc.gov/resource/gpe5-46if.json
# по полю cftc_contract_market_code.
PAIRS = {
    "EURUSD": {
        "code": "099741",
        "name": "EURO FX",
        "exchange": "CHICAGO MERCANTILE EXCHANGE",
        # Знак для DXY aggregate: EUR в индексе DXY обратный,
        # поэтому long EUR = short USD (sign = -1).
        "dxy_sign": -1,
        "dxy_weight": 0.576,
    },
    "GBPUSD": {
        "code": "096742",
        "name": "BRITISH POUND",
        "exchange": "CHICAGO MERCANTILE EXCHANGE",
        "dxy_sign": -1,
        "dxy_weight": 0.119,
    },
    "USDJPY": {
        "code": "097741",
        "name": "JAPANESE YEN",
        "exchange": "CHICAGO MERCANTILE EXCHANGE",
        # Long JPY = short USD/JPY, инверс для DXY long USD.
        "dxy_sign": -1,
        "dxy_weight": 0.136,
    },
    "AUDUSD": {
        "code": "232741",
        "name": "AUSTRALIAN DOLLAR",
        "exchange": "CHICAGO MERCANTILE EXCHANGE",
        "dxy_sign": -1,
        "dxy_weight": 0.039,
    },
    "USDCAD": {
        "code": "090741",
        "name": "CANADIAN DOLLAR",
        "exchange": "CHICAGO MERCANTILE EXCHANGE",
        "dxy_sign": -1,
        "dxy_weight": 0.091,
    },
    "NZDUSD": {
        "code": "112741",
        "name": "NEW ZEALAND DOLLAR",
        "exchange": "CHICAGO MERCANTILE EXCHANGE",
        "dxy_sign": -1,
        "dxy_weight": 0.039,
    },
}

# Минус CFTC: они котируют JPY и CAD как long JPY / long CAD, а торговая
# пара USD/JPY и USD/CAD имеет обратный смысл. Это исправляется на этапе
# отображения (USDJPY long AM = short JPY = -value в CFTC данных).
INVERT_FOR_DISPLAY = {"USDJPY", "USDCAD"}

# ----- DXY aggregate -----

# Веса по индексу DXY ICE (стандарт).
# Sum = 1.0 (нормализовано на 100%).
# Источник: https://www.ice.com/products/194/US-Dollar-Index-Futures
DXY_WEIGHTS = {
    "EURUSD": 0.576,
    "USDJPY": 0.136,
    "GBPUSD": 0.119,
    "USDCAD": 0.091,
    # NB: SEK 4.2% и CHF 3.6% мы НЕ покрываем (нет в наших pairs).
    # Дополняем долями AUD/NZD как proxy на risk-on FX-bloc.
    # Веса в результате нормализуются до 1.0 при compute_aggregate.
    "AUDUSD": 0.039,
    "NZDUSD": 0.039,
}

# ----- Tags thresholds -----

# Williams 3y percentile thresholds для генерации tag-а.
# Также используется momentum-detection через σ от 6-мес нормы.
TAG_THRESHOLDS = {
    "extreme_high": 90,    # Williams 3y >= 90 -> extreme
    "extreme_low": 10,     # Williams 3y <= 10 -> extreme
    "stretched_high": 80,  # Williams 3y >= 80 -> stretched
    "stretched_low": 20,   # Williams 3y <= 20 -> stretched
    "momentum_sigma": 1.5, # |WoW delta| >= 1.5σ от 6m нормы -> momentum
}

# ----- API endpoints -----

CFTC_TFF_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
CFTC_LEGACY_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# ----- Anthropic AI -----

ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 1500
ANTHROPIC_TEMPERATURE = 0.3

# ----- Discord webhooks -----

WEBHOOK_WEEKLY_ENV = "DISCORD_WEBHOOK_WEEKLY"
WEBHOOK_ALERTS_ENV = "DISCORD_WEBHOOK_ALERTS"

# ----- Telegram alerts -----

TG_BOT_TOKEN_ENV = "TG_ALERT_BOT_TOKEN"
TG_CHAT_ID_ENV = "TG_ALERT_CHAT_ID"

# ----- Misc -----

# Сколько недель истории храним в SQLite на пару.
# 3 года = 156 недель, держим 200 для запаса.
HISTORY_WEEKS_KEEP = 200

# Сколько недель показываем в таблице на странице detail.
TABLE_WEEKS_SHOW = 12

# Retry-окно если CFTC ещё не выложили данные.
RETRY_MAX_ATTEMPTS = 12
RETRY_DELAY_SEC = 600  # 10 минут между попытками
