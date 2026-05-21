# tos-stats

COT-аналитика TOS для FX-пар G10 + кастомный композит по доллару.

Еженедельный pipeline: CFTC TFF -> SQLite -> Williams 3y/1y/6m + DXY aggregate -> Anthropic Sonnet narrative -> JSON -> GitHub Pages + Discord embed.

## Структура

```
tos-stats/
├── ingest/                  # Python pipeline (на Callisto)
│   ├── config.py            # PAIRS, DXY_WEIGHTS, thresholds, paths
│   ├── db.py                # SQLite schema, save_reports, get_history
│   ├── fetch_cot.py         # CFTC Socrata API (TFF report)
│   ├── williams.py          # multi-window percentile, tag-классификация
│   ├── dxy_agg.py           # взвешенный композит по индексу DXY
│   ├── narrate.py           # Anthropic Sonnet, 4 раздела + watch list
│   ├── publish.py           # сборка current.json + архив
│   ├── post_discord.py      # webhook post + extreme follow-up
│   ├── alerter.py           # Telegram алерты при ошибках
│   └── run.py               # entry point (вызывается systemd timer)
├── web/                     # static site (GitHub Pages)
│   ├── index.html
│   ├── styles.css
│   ├── app.js               # fetch /data/current.json + render
│   └── data/
│       ├── current.json     # перезаписывается каждую пятницу
│       └── archive/         # snapshot copies (YYYY-WNN.json)
├── deploy/
│   ├── tos-stats.service    # systemd unit (oneshot)
│   ├── tos-stats.timer      # systemd timer (Fri 21:32 CET)
│   ├── setup.sh             # инициальная установка на Callisto
│   └── post-merge.hook      # auto-reload requirements + systemd при git pull
├── tests/
│   └── test_williams.py     # smoke unit tests
├── requirements.txt
├── .env.example             # шаблон конфига (.env в git НЕ идёт)
└── README.md
```

## Покрытие

6 FX-пар G10 + наш кастомный композит по доллару:

| Пара | CFTC код | Доля в DXY (нормализованная) |
|------|----------|------------------------------|
| EURUSD | 099741 | 57.6% |
| USDJPY | 097741 | 13.6% |
| GBPUSD | 096742 | 11.9% |
| USDCAD | 090741 | 9.1% |
| AUDUSD | 232741 | 3.9% |
| NZDUSD | 112741 | 3.9% |

Equity-индексы, commodities, bonds НЕ покрываем -- для FX-аудитории edge сильнее, остальные пары только захламляли бы UI.

## Tag-классификация

- `extreme` -- Williams 3y >= 90 или <= 10. Crowded позиционирование на 3-летнем экстремуме.
- `stretched` -- Williams 3y >= 80 или <= 20. Растянуто, не экстремум.
- `momentum` -- |WoW дельта| >= 1.5σ от 6-мес нормы. Резкое движение.
- `neutral` -- ничего из вышеперечисленного.

В Discord embed: тег + цветной dot + цветная подсветка card. В narrative: extreme-пары публикуются отдельным follow-up postом с пингом @swing role.

## Pipeline schedule

Cron: `Fri 21:32 Europe/Berlin` (= CFTC publish 15:30 ET + 2 мин запас).

Этапы (один проход):
1. `fetch_cot.py` -- запрос на Socrata API CFTC, retry до 02:00 если задержка
2. `db.save_reports` -- INSERT OR REPLACE свежих rows
3. `williams.compute_pair_metrics` × 6 + `dxy_agg.compute_aggregate`
4. `narrate.generate_all` -- 7 Sonnet запросов (~$0.30-0.50 / неделя)
5. `publish.build_snapshot` + `write_json` -- web/data/current.json
6. `git push` -- GitHub Pages auto-deploy
7. `post_discord.post_weekly` -- embed в #cot-weekly

При failure на любом шаге -- `alerter.notify_exception` в Telegram ЛС Daniil'у.

## Установка (один раз на Callisto)

```bash
# 0. Клон в стандартное место.
cd /root/tos_bots
git clone git@github.com-tos-stats:crypt0punky/tos-stats.git
cd tos-stats

# 1. .env с секретами.
cp .env.example .env
nano .env  # вписать ANTHROPIC_API_KEY, DISCORD_WEBHOOK_WEEKLY, TG_*, DISCORD_SWING_ROLE_ID

# 2. Setup-скрипт.
bash deploy/setup.sh

# 3. Smoke test.
./.venv/bin/python -m ingest.fetch_cot  # 3 последних EURUSD report должны напечататься

# 4. Первый ручной запуск (необязательно, dryrun timer'а):
systemctl start tos-stats.service
tail -f logs/weekly.log

# 5. Git post-merge hook (опционально, чтобы git pull сам ребилдил deps):
ln -s ../../deploy/post-merge.hook .git/hooks/post-merge
chmod +x .git/hooks/post-merge
```

## Полезные команды

```bash
# Когда следующий запуск.
systemctl list-timers tos-stats.timer

# Ручной запуск pipeline.
systemctl start tos-stats.service && tail -f logs/weekly.log

# Логи последнего запуска.
journalctl -u tos-stats.service -n 50

# Проверка БД.
sqlite3 /root/tos_bots/data/stats.db ".tables"
sqlite3 /root/tos_bots/data/stats.db "SELECT pair, MAX(report_date), am_net FROM reports GROUP BY pair"

# Тесты.
./.venv/bin/python -m pytest tests/ -v

# Smoke fetch (без записи в БД).
./.venv/bin/python -m ingest.fetch_cot
```

## Связанные сервисы на Callisto

- `tos-access-bot.service` -- Telegram access bot
- `tos-cult-bot.service` -- Telegram cult bot
- `tos-stats.service` (oneshot, by timer) -- этот pipeline

Общая БД `/root/tos_bots/data/bots.db` -- НЕ трогаем. У tos-stats своя `stats.db`.

## Что НЕ коммитить

См. `.gitignore`. Главное: `.env`, `*.db`, `logs/`, `.venv/`.

Если случайно закоммитил секрет -- сразу регенерируй (webhook URL, TG token, Anthropic key) и force-push после `git filter-branch` или `git filter-repo`.
