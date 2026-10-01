"""Ручной ввод — запасной путь автомата: record-ras, record-actual, record-consensus, record-guidance.

Каждое число вносится с источником (документ, страница или адрес) и ложится в ряды
со статусом `manual`; момент ввода — момент известности. Доли вводятся либо долей
(`0.229`), либо процентами со знаком (`22.9%`); доля больше 0,5 без знака «%» —
отказ (почти наверняка проценты). Факт журнала неснимаем (`journal.record_actual`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from indicators import periods
from indicators.journal import TARGETS, Journal
from indicators.release_watch import METRICS, SHARE_METRICS, STATE_DIR
from indicators.store import Point, Store

# Пункты гайденса — ключи фактов `guidance.json → items` (один словарь на факты и ручной ввод);
# `cor` — точка, если эмитент её назовёт (в фактах сейчас граница `cor_max`), `payout` — доля выплаты.
GUIDANCE_ITEMS = ("roe", "nim", "cor", "cor_max", "cir", "fee_growth", "n20_0", "loan_growth.corporate",
                  "loan_growth.retail", "payout")
SHARE_GUIDANCE = frozenset(GUIDANCE_ITEMS)
MAX_SHARE = 0.5


class RecordError(ValueError):
    """Ввод не принят."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_value(text: str, *, share: bool) -> float:
    """«1234,5» → 1234.5; «12,3%» → 0.123; доля без «%» больше 0,5 — отказ."""
    t = str(text).strip().replace(" ", "").replace(",", ".")
    pct = t.endswith("%")
    try:
        v = float(t.rstrip("%"))
    except ValueError as exc:
        raise RecordError(f"не число: {text!r}") from exc
    if pct:
        if not share:
            raise RecordError(f"«%» у денежной величины: {text!r}")
        return round(v / 100.0, 10)
    if share and abs(v) > MAX_SHARE:
        raise RecordError(f"{text!r}: доля больше {MAX_SHARE} — укажите проценты со знаком «%»")
    return v


def parse_pairs(items: Iterable[str]) -> dict[str, float]:
    """[«np_ytd=1234,5», «roe_ytd=12,3%»] → {метрика: значение} с проверкой имён."""
    out = {}
    for item in items:
        if "=" not in item:
            raise RecordError(f"ожидалось метрика=значение: {item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if key not in METRICS:
            raise RecordError(f"неизвестная метрика релиза {key!r}; допустимы: {', '.join(METRICS)}")
        out[key] = parse_value(raw, share=key in SHARE_METRICS)
    return out


def record_ras(store: Store, month: str, values: dict[str, float], source: str) -> dict[str, Any]:
    """Числа релиза РСБУ за месяц руками: в очередь дозора (вид `manual`, принимается сам)."""
    periods.parse_month(month)
    if not source:
        raise RecordError("нужен источник (документ, страница или адрес)")
    name = f"{STATE_DIR}/manual_{month}.json"
    held = store.read_state(name) or {}
    now = _now()
    for k, v in values.items():
        held[k] = {"value": v, "source": source, "at": now}
    store.write_state(name, held)
    return held


def record_actual(journal: Journal, store: Store, *, target: str, period: str, value: str | float,
                  source: str, reported_on: date | None = None) -> float:
    """Факт квартала: в журнал (неснимаем) и в ряд `actual.<цель>` (момент — день публикации)."""
    if target not in TARGETS:
        raise RecordError(f"неизвестная цель {target!r}; допустимы: {', '.join(TARGETS)}")
    periods.parse_quarter(period)
    v = parse_value(str(value), share=TARGETS[target]["unit"] == "share")
    journal.record_actual(target=target, period=period, value=v, source=source, reported_on=reported_on)
    at = (reported_on.isoformat() + "T07:00:00+00:00") if reported_on else _now()
    store.upsert(f"actual.{target}", [Point(period, v, at, "manual", source)], basis=TARGETS[target]["basis"],
                 unit=TARGETS[target]["unit"], label=TARGETS[target]["title"])
    return v


def record_consensus(store: Store, *, period: str, value: str | float, source: str) -> float:
    """Консенсус ЧП квартала перед МСФО (эталон `consensus`)."""
    periods.parse_quarter(period)
    if not source:
        raise RecordError("нужен источник консенсуса")
    v = parse_value(str(value), share=False)
    store.upsert("consensus.ni_q", [Point(period, v, _now(), "manual", source)], basis="ifrs", unit="RUB bn",
                 label="консенсус ЧП квартала")
    return v


def record_guidance(store: Store, *, year: int, item: str, value: str | float, source: str) -> float:
    """Гайденс года (упр. базис) точкой: ROE, ЧПМ, CoR, CIR, рост ЧКД, Н20.0, рост кредитов, выплата.

    Эталоном журнала служат `nim` и `cor` (точки); запись важнее фактов `guidance.json` и нужна,
    когда факты года ещё не обновлены, — до первой записи T-90 квартала."""
    if item not in GUIDANCE_ITEMS:
        raise RecordError(f"неизвестный пункт гайденса {item!r}; допустимы: {', '.join(GUIDANCE_ITEMS)}")
    if not source:
        raise RecordError("нужен источник гайденса")
    v = parse_value(str(value), share=item in SHARE_GUIDANCE)
    store.upsert(f"guidance.{item}", [Point(str(int(year)), v, _now(), "manual", source)], basis="mgmt",
                 unit="share", label=f"гайденс: {item}")
    return v
