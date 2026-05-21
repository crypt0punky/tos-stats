"""AI narrative через Anthropic Sonnet.

Для каждой пары + DXY aggregate отдельный запрос.
Промпт жёсткий: 4 раздела + watch list, без price targets, без воды,
без буквы ё, без длинных тире, конкретные числа.

Output структурированный JSON через response_format (Anthropic SDK поддерживает
через tool_use). Используем prompt + parse JSON из response text - надёжнее
в нашей версии SDK.
"""

import asyncio
import json
import logging
import os
import re

from anthropic import AsyncAnthropic, APIError

from . import config

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты COT-аналитик для русскоязычной трейдинг-аудитории TOS Community.

СТИЛЬ:
- Трейдерский, сухой, по делу. Активный залог. Конкретные числа из данных.
- Без буквы "ё" (никогда). Без длинного тире "—" (только обычный дефис "-").
- Без шаблонных оборотов: "стоит отметить", "является", "следует учитывать", "представляет собой".
- Без хеджирования: "вероятно", "возможно", "скорее всего", "кажется".
- Без рекламного языка: "ключевой", "значимый", "уникальный", "поистине".
- Не Title Case в русском (тип "Главный Драйвер"), не ALL CAPS.
- Один абзац на раздел.

ЗАПРЕЩЕНО:
- Давать price targets, stop loss, take profit ("цена пойдет к X")
- Прогнозировать направление цены ("ожидаем рост / падение")
- Упоминать макро-события, имена политиков, новости (этих данных у тебя нет)
- Использовать слова: "ё", "—", "стоит отметить", "является", "вероятно", "ландшафт", "ключевой"
- Раздувать значимость: "критическое расхождение", "знаковый уровень"

ВЫХОД: строго JSON одним объектом без markdown:
{
  "snapshot": "...",
  "dynamics": "...",
  "historical": "...",
  "cross_pair": "...",
  "watch": ["...", "...", "...", "..."]
}

Каждое поле narrative - один абзац 60-90 слов. Watch - массив 3-5 строк по 10-20 слов.
Числа в тексте оборачивай в <em>число</em> когда хочешь выделить. Знак "+" или "-"
перед числом автоматически окрасит блок (положительные green, отрицательные red),
числа без знака останутся серыми."""


def _format_history(history_rows: list, n: int = 12) -> str:
    """Последние N недель в компактном виде для подачи в промпт."""
    if not history_rows:
        return "(нет данных)"
    lines = []
    for r in history_rows[:n]:
        lines.append(
            f"  {r['report_date']}: AM Net {r['am_net']:>9}  LF Net {r['lf_net']:>9}  OI {r['open_interest']:>9}"
        )
    return "\n".join(lines)


def _build_pair_user_prompt(pair: str, metrics, history_rows: list, cross_pair_summary: str) -> str:
    """Сборка user-промпта для конкретной пары."""
    return f"""ПАРА: {pair}
TAG: {metrics.tag}

ТЕКУЩИЕ ЗНАЧЕНИЯ (TFF report):
  AM Net: {metrics.am_net:+}  (WoW {metrics.am_wow:+}, MoM {metrics.am_mom:+}, 3M {metrics.am_3m:+})
  LF Net: {metrics.lf_net:+}  (WoW {metrics.lf_wow:+})
  Dealers Net: {metrics.dealer_net:+}  (WoW {metrics.dealer_wow:+})
  Open Interest: {metrics.oi:,}  (WoW {metrics.oi_wow:+})

WILLIAMS PERCENTILE (мульти-окно):
  3y: {metrics.williams_3y}
  1y: {metrics.williams_1y}
  6m: {metrics.williams_6m}

σ WoW дельты AM за 6 мес: {metrics.sigma_6m:.0f}
(WoW магнитуда vs σ: {abs(metrics.am_wow) / metrics.sigma_6m if metrics.sigma_6m > 0 else 0:.2f}σ)

ПОСЛЕДНИЕ 12 НЕДЕЛЬ:
{_format_history(history_rows, 12)}

CROSS-PAIR КОНТЕКСТ (все 6 пар + DXY aggregate сейчас):
{cross_pair_summary}

Напиши 4 раздела (Срез / Динамика / История / В связке) и watch-list (3-5 пунктов).
В разделе "В связке" обязательно упомяни как эта пара ложится в общую USD-positioning картину
относительно других пар и DXY aggregate.

ВЫХОД: только JSON, без markdown ``` блока."""


def _build_aggregate_user_prompt(agg, pair_metrics_list: list) -> str:
    """Промпт для DXY aggregate."""
    pairs_summary = "\n".join(
        f"  {m.pair}: tag={m.tag}, W3y={m.williams_3y}, AM Net {m.am_net:+}, WoW {m.am_wow:+}"
        for m in pair_metrics_list
    )
    return f"""ИНСТРУМЕНТ: DXY POSITIONING AGGREGATE (наш кастомный композит)

Что это: взвешенная сумма AM Net через 6 пар G10 с весами по индексу DXY:
  EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, AUD 3.9%, NZD 3.9%
Положительные значения = AM в длинном USD, отрицательные = в коротком.

ТЕКУЩИЕ ЗНАЧЕНИЯ:
  Weighted Net: {agg.weighted_net:+}  (WoW {agg.wow:+}, MoM {agg.mom:+}, 3M {agg.m3:+})

WILLIAMS PERCENTILE (мульти-окно):
  3y: {agg.williams_3y}
  1y: {agg.williams_1y}
  6m: {agg.williams_6m}

TAG: {agg.tag}

СОСТОЯНИЕ ПАР В КОРЗИНЕ:
{pairs_summary}

Напиши 4 раздела (Срез / Динамика / История / В связке). Watch не нужен.
В разделе "В связке" -- какие пары несут основной flow в текущий aggregate уровень,
какие идут против. Что произойдёт с aggregate если ключевая пара начнёт разворачиваться.

ВЫХОД: только JSON, без markdown.
{{"snapshot": "...", "dynamics": "...", "historical": "...", "cross_pair": "...", "watch": []}}"""


def _strip_markdown_fences(text: str) -> str:
    """Убрать ```json и ``` если LLM их добавила вопреки инструкции."""
    text = text.strip()
    if text.startswith("```"):
        # ```json\n{...}\n```
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


async def _generate_one(
    client: AsyncAnthropic,
    user_prompt: str,
    pair_label: str,
) -> dict:
    """Один запрос к Sonnet, возвращает распарсенный JSON dict с narrative."""
    try:
        resp = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=config.ANTHROPIC_MAX_TOKENS,
            temperature=config.ANTHROPIC_TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except APIError as e:
        log.error("Anthropic API error for %s: %s", pair_label, e)
        raise

    text = resp.content[0].text if resp.content else ""
    text = _strip_markdown_fences(text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        log.error("Bad JSON from Sonnet for %s: %s\nRaw: %s", pair_label, e, text[:500])
        raise ValueError(f"narrate: bad JSON for {pair_label}")

    required = ("snapshot", "dynamics", "historical", "cross_pair")
    missing = [k for k in required if k not in parsed]
    if missing:
        raise ValueError(f"narrate: missing fields for {pair_label}: {missing}")
    if "watch" not in parsed:
        parsed["watch"] = []

    return parsed


def _fallback_narrative(metrics) -> dict:
    """Шаблон-narrative если AI недоступен. Чтобы сайт работал без AI слоя."""
    direction = "длинный" if metrics.am_net > 0 else "короткий"
    return {
        "snapshot": (
            f"AM {direction}: <em>{metrics.am_net:+}</em>, Williams 3y на {metrics.williams_3y}. "
            f"AI-аналитика временно недоступна, цифры актуальные."
        ),
        "dynamics": (
            f"Недельная дельта <em>{metrics.am_wow:+}</em>, месячная <em>{metrics.am_mom:+}</em>, "
            f"трёхмесячная <em>{metrics.am_3m:+}</em>."
        ),
        "historical": "AI-разбор истории появится при следующем обновлении.",
        "cross_pair": "AI-разбор контекста появится при следующем обновлении.",
        "watch": [],
        "_source": "fallback",
    }


def _cross_pair_summary(pair_metrics_list: list, agg) -> str:
    """Краткий cross-pair context для подачи в промпт каждой пары."""
    lines = [
        f"  {m.pair}: tag={m.tag}, W3y={m.williams_3y}, AM Net {m.am_net:+}, WoW {m.am_wow:+}"
        for m in pair_metrics_list
    ]
    lines.append(
        f"  DXY aggregate: tag={agg.tag}, W3y={agg.williams_3y}, "
        f"Weighted Net {agg.weighted_net:+}, WoW {agg.wow:+}"
    )
    return "\n".join(lines)


async def generate_all(
    pair_metrics_list: list,
    history_by_pair: dict[str, list],
    aggregate,
) -> dict[str, dict]:
    """Сгенерировать narrative для всех пар + aggregate.

    Возвращает dict pair_id -> narrative_dict (snapshot/dynamics/historical/cross_pair/watch).
    Aggregate под ключом "DXY".

    Если Anthropic упал - вставляет fallback narrative и continue.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set, using fallback narratives")
        out = {m.pair: _fallback_narrative(m) for m in pair_metrics_list}
        out["DXY"] = {
            "snapshot": f"DXY aggregate Williams 3y {aggregate.williams_3y}. AI-аналитика недоступна.",
            "dynamics": "AI слой выключен.",
            "historical": "AI слой выключен.",
            "cross_pair": "AI слой выключен.",
            "watch": [],
            "_source": "fallback",
        }
        return out

    client = AsyncAnthropic(api_key=api_key)
    cross_summary = _cross_pair_summary(pair_metrics_list, aggregate)

    out: dict[str, dict] = {}
    # Делаем последовательно а не parallel: rate limit Anthropic Tier 1
    # на messages 50 RPM, нам и 7 запросов хватает без забот, но parallel
    # риск burst-thresholds. Sequential 7 запросов ~30-50 сек.
    for m in pair_metrics_list:
        log.info("Generating narrative for %s", m.pair)
        prompt = _build_pair_user_prompt(m.pair, m, history_by_pair[m.pair], cross_summary)
        try:
            out[m.pair] = await _generate_one(client, prompt, m.pair)
        except Exception as e:
            log.error("Narrative for %s failed: %s", m.pair, e)
            out[m.pair] = _fallback_narrative(m)

    log.info("Generating narrative for DXY aggregate")
    prompt = _build_aggregate_user_prompt(aggregate, pair_metrics_list)
    try:
        out["DXY"] = await _generate_one(client, prompt, "DXY")
    except Exception as e:
        log.error("Narrative for DXY failed: %s", e)
        out["DXY"] = {
            "snapshot": f"DXY aggregate Williams 3y {aggregate.williams_3y}. AI-разбор не сгенерирован.",
            "dynamics": "AI временно недоступен.",
            "historical": "AI временно недоступен.",
            "cross_pair": "AI временно недоступен.",
            "watch": [],
            "_source": "fallback",
        }

    return out


async def generate_tldr(pair_metrics_list: list, aggregate, narratives: dict) -> str:
    """Cross-pair синтез для главной страницы (TLDR блок).

    Отдельный запрос к Sonnet -- видит все narratives и метрики, выдаёт 3-4 предложения.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            f"AM positioning week summary. DXY aggregate Williams 3y {aggregate.williams_3y}. "
            f"AI-аналитика недоступна."
        )

    client = AsyncAnthropic(api_key=api_key)

    pairs_summary = "\n".join(
        f"  {m.pair}: tag={m.tag}, W3y={m.williams_3y}, AM Net {m.am_net:+}, WoW {m.am_wow:+}"
        for m in pair_metrics_list
    )
    user_prompt = f"""СОСТОЯНИЕ ПАР НА НЕДЕЛЕ:
{pairs_summary}

DXY AGGREGATE: tag={aggregate.tag}, W3y={aggregate.williams_3y}, Weighted Net {aggregate.weighted_net:+}, WoW {aggregate.wow:+}

Напиши главный нарратив недели одним абзацем (3-4 предложения, 60-100 слов).
Что главное в позиционировании сейчас. Где толпа, где дивергенция, где аномалии.
Числа и тикеры оборачивай в <em>...</em>.

ВЫХОД: только текст абзаца, без JSON, без заголовка."""

    try:
        resp = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=400,
            temperature=config.ANTHROPIC_TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        return _strip_markdown_fences(text)
    except Exception as e:
        log.error("TLDR generation failed: %s", e)
        return f"AM positioning. DXY aggregate W3y {aggregate.williams_3y}, tag {aggregate.tag}."
