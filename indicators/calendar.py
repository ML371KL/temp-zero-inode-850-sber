"""Календарь индикаторов и выпуска: релизы РСБУ, формы ЦБ, МСФО, события фактов.

Источники дат по убыванию силы: `facts/calendar.json` (поток facts; подтверждённые
даты), календарь отчётов T-Invest (`GetAssetReports`: РСБУ и МСФО не различает — из
двух дат квартала ранняя — релиз РСБУ последнего месяца, поздняя — МСФО), расписание
по умолчанию `sources.yaml` (`schedule`: окна дней). Форма 0409102 — по истории
первого появления (`cbr_forms.expected_form102_date`).

Литералов дат нет: окна — числа дней конфигурации, годы — из переданных периодов.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from indicators import cbr_forms, periods
from indicators.store import Store

EVENT_HORIZON_DAYS = 366
RECENT_DAYS = 7


def _clip(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, periods.month_end(periods.month(y, m)).day))


def ras_release_window(month: str, schedule: dict[str, Any]) -> tuple[date, date]:
    """Окно выхода релиза РСБУ за месяц: дни следующего месяца (декабрь — своё окно)."""
    nxt = periods.shift_month(month, 1)
    y, m = periods.parse_month(nxt)
    lo, hi = schedule.get("ras_december_day" if periods.parse_month(month)[1] == 12 else "ras_release_day", (8, 15))
    return _clip(y, m, int(lo)), _clip(y, m, int(hi))


def ifrs_window(period: str, schedule: dict[str, Any]) -> tuple[date, date]:
    """Окно выхода МСФО квартала: дни месяца после квартала (4-й квартал — свой сдвиг и дни)."""
    y, q = periods.parse_quarter(period)
    last = periods.quarter_months(period)[-1]
    if q == 4:
        spec = schedule.get("ifrs_q4", {"month_offset": 2, "day": (20, 28)})
        target = periods.shift_month(last, int(spec.get("month_offset", 2)))
        lo, hi = spec.get("day", (20, 28))
    else:
        target = periods.shift_month(last, 1)
        lo, hi = schedule.get("ifrs_quarter_day", (20, 31))
    ty, tm = periods.parse_month(target)
    return _clip(ty, tm, int(lo)), _clip(ty, tm, int(hi))


def tinvest_reports(store: Store) -> list[dict[str, Any]]:
    data = store.read_state("tinvest/reports.json") or {}
    return list(data.get("events") or [])


def _tinvest_pair(store: Store, period: str) -> list[date]:
    y, q = periods.parse_quarter(period)
    days = sorted({date.fromisoformat(e["date"]) for e in tinvest_reports(store)
                   if e.get("year") == y and e.get("num") == q and "QUARTER" in str(e.get("type", ""))})
    return days


def _facts_event(events: list[dict], kind: str, covers: str) -> dict | None:
    for e in events:
        if e.get("kind") == kind and e.get("covers") == covers:
            return e
    return None


def ras_release(month: str, *, store: Store, schedule: dict, facts_events: list[dict]) -> dict[str, Any]:
    """Ожидаемый релиз РСБУ за месяц: {date, confirmed, precision, earliest, latest, src}."""
    ev = _facts_event(facts_events, "ras", month)
    lo, hi = ras_release_window(month, schedule)
    if ev and ev.get("date"):
        return {"date": ev["date"], "confirmed": bool(ev.get("confirmed")), "precision": ev.get("precision", "day"),
                "earliest": ev.get("earliest"), "latest": ev.get("latest"), "src": "facts/calendar.json"}
    y, m = periods.parse_month(month)
    if m % 3 == 0:
        pair = _tinvest_pair(store, periods.quarter_of_month(month))
        if len(pair) >= 2:
            return {"date": pair[0].isoformat(), "confirmed": True, "precision": "day",
                    "earliest": None, "latest": None, "src": "T-Invest, календарь отчётов"}
    return {"date": lo.isoformat(), "confirmed": False, "precision": "window",
            "earliest": lo.isoformat(), "latest": hi.isoformat(), "src": "sources.yaml schedule"}


def ifrs_report(period: str, *, store: Store, schedule: dict, facts_events: list[dict]) -> dict[str, Any]:
    """Ожидаемая МСФО квартала: {date, confirmed, precision, earliest, latest, src}."""
    ev = _facts_event(facts_events, "ifrs", period)
    if ev and ev.get("date"):
        return {"date": ev["date"], "confirmed": bool(ev.get("confirmed")), "precision": ev.get("precision", "day"),
                "earliest": ev.get("earliest"), "latest": ev.get("latest"), "src": "facts/calendar.json",
                "id": ev.get("id"), "title": ev.get("title")}
    pair = _tinvest_pair(store, period)
    if pair:
        return {"date": pair[-1].isoformat(), "confirmed": True, "precision": "day", "earliest": None,
                "latest": None, "src": "T-Invest, календарь отчётов"}
    lo, hi = ifrs_window(period, schedule)
    return {"date": hi.isoformat(), "confirmed": False, "precision": "window", "earliest": lo.isoformat(),
            "latest": hi.isoformat(), "src": "sources.yaml schedule"}


def ras_known(store: Store, month: str, as_of: str | None = None) -> str | None:
    """Как месяц РСБУ уже известен: `release` | `form102` | None."""
    s = store.load("ras.release.np_ytd")
    if s is not None and s.value_as_of(month, as_of) is not None:
        return "release"
    f = store.load("cbr.f102.ni_ytd")
    if f is not None and f.value_as_of(month, as_of) is not None:
        return "form102"
    return None


def next_ras(today: date, *, store: Store, schedule: dict, facts_events: list[dict],
             release_auto: bool = True) -> dict[str, Any]:
    """П§2 `calendar.next_ras` без `days`: первый месяц, которого ещё нет ни в релизе, ни в форме 102.

    Дата формы 0409102 — оценка по прошлым публикациям, не объявление: отдаётся окном
    (`form102_date_est` — нижний край, `form102_latest_est` — верхний)."""
    current = periods.month_of(today)
    month = current
    for back in (3, 2, 1):
        candidate = periods.shift_month(current, -back)
        if ras_known(store, candidate) is None:
            month = candidate
            break
    rel =ras_release(month, store=store, schedule=schedule, facts_events=facts_events)
    f_lo, f_hi = cbr_forms.expected_form102_date(store, month, default_days=tuple(schedule.get("form102_day", (24, 26))))
    enters = "release" if release_auto else "form102"
    return {"month": month, "release_date": rel["date"], "release_confirmed": rel["confirmed"],
            "form102_date_est": f_lo, "form102_latest_est": f_hi, "enters_via": enters,
            "date": rel["date"] if enters == "release" else f_lo}


def open_ifrs_period(today: date, store: Store) -> str:
    """Самый ранний квартал без внесённого факта журнала (прибыль) не раньше прошлого квартала."""
    return periods.shift_quarter(periods.quarter_of(today), -1)


def is_release_day(today: date, *, store: Store, schedule: dict, facts_events: list[dict]) -> bool:
    """В окне ли сегодня релиз РСБУ ещё не принятого месяца или МСФО прошлого квартала."""
    for back in (1, 2):
        month = periods.shift_month(periods.month_of(today), -back)
        if ras_known(store, month) == "release":
            continue
        rel = ras_release(month, store=store, schedule=schedule, facts_events=facts_events)
        lo = date.fromisoformat(rel["earliest"] or rel["date"])
        hi = date.fromisoformat(rel["latest"] or rel["date"]) + timedelta(days=int(schedule.get("late_days", 3)))
        if lo <= today <= hi:
            return True
    q = periods.shift_quarter(periods.quarter_of(today), -1)
    rep = ifrs_report(q, store=store, schedule=schedule, facts_events=facts_events)
    lo = date.fromisoformat(rep["earliest"] or rep["date"])
    hi = date.fromisoformat(rep["latest"] or rep["date"])
    return lo <= today <= hi


def events(today: date, *, store: Store, schedule: dict, facts_events: list[dict],
           register: list[dict] | tuple = ()) -> list[dict[str, Any]]:
    """События на 12 месяцев вперёд и за `RECENT_DAYS` назад (П§2 `calendar.events` без `days`)."""
    start, end = today - timedelta(days=RECENT_DAYS), today + timedelta(days=EVENT_HORIZON_DAYS)
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def add(ev: dict[str, Any]) -> None:
        d = date.fromisoformat(ev["date"])
        key = (ev["kind"], ev.get("covers"), ev["date"])
        if start <= d <= end and key not in seen:
            seen.add(key)
            out.append(ev)

    for e in facts_events:
        if e.get("date"):
            add({k: e.get(k) for k in ("id", "date", "kind", "title", "covers", "confirmed", "precision",
                                       "earliest", "latest", "note") if e.get(k) is not None or k in ("covers",)})
    month = periods.shift_month(periods.month_of(today), -1)
    for k in range(13):
        m = periods.shift_month(month, k)
        rel = ras_release(m, store=store, schedule=schedule, facts_events=facts_events)
        add({"id": f"ras-{m}", "date": rel["date"], "kind": "ras", "title": f"РСБУ банка за {m}", "covers": m,
             "confirmed": rel["confirmed"], "precision": rel["precision"],
             **({"earliest": rel["earliest"], "latest": rel["latest"]} if rel["precision"] == "window" else {})})
        f_lo, f_hi = cbr_forms.expected_form102_date(store, m, default_days=tuple(schedule.get("form102_day", (24, 26))))
        add({"id": f"form102-{m}", "date": f_lo, "kind": "form102", "title": f"форма 0409102 за {m}",
             "covers": m, "confirmed": False, "precision": "window", "earliest": f_lo, "latest": f_hi})
    q = periods.shift_quarter(periods.quarter_of(today), -1)
    for k in range(5):
        p = periods.shift_quarter(q, k)
        rep = ifrs_report(p, store=store, schedule=schedule, facts_events=facts_events)
        add({"id": rep.get("id") or f"ifrs-{p}", "date": rep["date"], "kind": "ifrs",
             "title": rep.get("title") or f"МСФО за {p}", "covers": p, "confirmed": rep["confirmed"],
             "precision": rep["precision"],
             **({"earliest": rep["earliest"], "latest": rep["latest"]} if rep["precision"] == "window" else {})})
    for r in register or ():
        if r.get("pay_date"):
            add({"id": f"pay-{r.get('year')}", "date": r["pay_date"], "kind": "pay",
                 "title": f"выплата дивиденда за {r.get('year')}", "covers": None, "confirmed": True,
                 "precision": "day"})
        if r.get("record_date"):
            add({"id": f"record-{r.get('year')}", "date": r["record_date"], "kind": "record",
                 "title": f"отсечка дивиденда за {r.get('year')}", "covers": None, "confirmed": True,
                 "precision": "day"})
    return sorted(out, key=lambda e: (e["date"], e["kind"]))
