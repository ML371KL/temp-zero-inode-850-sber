"""Реестр объявленных дивидендов для выпуска: три источника и тревога (М§15, §14.3; B3).

Источники записи года прибыли Y (главный тикер книги):

1. **T-Invest `GetDividends`** (`tinvest/dividends.json`): `dividendNet` = валовой DPS, отсечка,
   последний день покупки, выплата. Запись появляется когда угодно: сразу после рекомендации
   совета, в день отсечки или после выплаты — стадию решения по ней судить нельзя.
2. **Новость о решении ГОСА или рекомендации совета директоров** (`news/items.json`, тема
   `dividend`, виды из `sources.yaml → dividends.news_kinds`: Интерфакс, ТАСС): DPS, год, отсечка,
   дата решения (день публикации, МСК) — `news.extract_dividend_decision`. Засчитывается одна
   новость: две ленты об одном решении — не два независимых источника.
3. **Ручная запись оператора** (`record-dividend`, `manual/dividends.json`) со ссылкой на документ
   эмитента — запасной второй источник.

Источник засчитывается, если его DPS совпадает с опорным (T-Invest, иначе ручная, иначе новость)
до `dividends.dps_tolerance` и отсечки не противоречат; несовпадения — в `notes`. Статус (М§15.1):

* `declared` — есть решение ГОСА (новость или ручная запись) или только T-Invest;
* «T-Invest + только рекомендация совета» — `recommended` до дня отсечки и `declared` с него:
  прошедшая отсечка решение подтверждает, но рекомендация вторым источником решения не
  считается (в `sources` остаётся T-Invest и ручная запись);
* `recommended` — только рекомендация совета;
* `paid` — есть запись T-Invest (или объявленная запись) и наступила дата выплаты, что бы ни
  писали новости.

Дата решения (`decided_date`) — только из решений ГОСА; день рекомендации — `recommended_date`.
Годность (два источника у `declared`, 0 < DPS < 0,5 цены, отсечка через 10–20 дней после решения)
проверяет ядро (`model/live.py`).

**Тревога** (`alarm`): прошёл конец квартала ГОСА года Y + 1 (`dividends.calendar.agm_quarter`
книги), а годной записи за Y (`paid` или `declared` с двумя источниками) нет — строка тревоги
такта (код 3 сборщиков → «ТРЕВОГА (код 8)» в `ops/run.sh`) и флаг `dividend_register` выходов.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from indicators import news, periods
from indicators.tinvest import DIVIDENDS_FILE as TINVEST_FILE
from indicators.store import Store

TINVEST = "T-Invest"
MANUAL = "ручная запись"
MANUAL_FILE = "manual/dividends.json"
STATUSES = ("declared", "recommended")
DEFAULT_DPS_TOLERANCE = 0.005
KEEP = ("year", "dps", "status", "record_date", "last_buy_date", "ex_date", "pay_date", "decided_date", "sources")


class RegisterError(ValueError):
    """Ручная запись реестра не принята."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tinvest_records(store: Store, ticker: str) -> list[dict[str, Any]]:
    data = store.read_state(TINVEST_FILE) or {}
    return [dict(r) for r in (data.get("by_ticker") or {}).get(ticker) or []]


def news_decisions(store: Store, cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Решения ГОСА и рекомендации совета из новостей видов `dividends.news_kinds`."""
    kinds = dict((cfg.get("dividends") or {}).get("news_kinds") or {})
    items = (store.read_state("news/items.json") or {}).get("items") or {}
    out = []
    for url, item in sorted(items.items()):
        kind = str(item.get("kind") or "")
        if kind not in kinds or item.get("topic") != "dividend":
            continue
        text = f"{item.get('title') or ''}. {item.get('text') or ''}"
        got = news.extract_dividend_decision(text, published=item.get("published") or item.get("first_seen"))
        if got is not None:
            out.append({**got, "source": kinds[kind], "url": url, "published": item.get("published")})
    return out


def manual_records(store: Store) -> list[dict[str, Any]]:
    return [dict(r) for r in (store.read_state(MANUAL_FILE) or {}).get("records") or []]


def record_manual(store: Store, *, year: int, dps: float, status: str, source: str,
                  record_date: str | None = None, decided_date: str | None = None, pay_date: str | None = None,
                  last_buy_date: str | None = None, dps_preferred: float | None = None) -> dict[str, Any]:
    """Ручная запись решения ГОСА или рекомендации совета (`record-dividend`): запасной источник реестра."""
    if status not in STATUSES:
        raise RegisterError(f"статус {status!r}: допустимы {', '.join(STATUSES)}")
    if not source or not str(source).strip():
        raise RegisterError("нужна ссылка на документ эмитента (сообщение о существенном факте, протокол, пресс-релиз)")
    if not dps or dps <= 0:
        raise RegisterError(f"DPS {dps}: нужен положительный валовой дивиденд на акцию, ₽")
    if status == "declared" and not record_date:
        raise RegisterError("у решения ГОСА нужна дата отсечки (--record-date)")
    for name, day in (("record_date", record_date), ("decided_date", decided_date), ("pay_date", pay_date),
                      ("last_buy_date", last_buy_date)):
        if day:
            try:
                date.fromisoformat(day)
            except ValueError as exc:
                raise RegisterError(f"{name}: {day!r} — не дата YYYY-MM-DD") from exc
    rec = {"year": int(year), "dps": float(dps), "dps_preferred": dps_preferred, "status": status,
           "record_date": record_date, "ex_date": record_date, "last_buy_date": last_buy_date,
           "pay_date": pay_date, "decided_date": decided_date, "source": str(source).strip(),
           "recorded_at": _now()}
    held = store.read_state(MANUAL_FILE) or {"records": []}
    held.setdefault("records", []).append(rec)
    store.write_state(MANUAL_FILE, held)
    return rec


def _latest(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return sorted(rows, key=lambda r: str(r.get(key) or ""))[-1] if rows else None


def merge(year: int, tinvest: list[dict], found: list[dict], manual: list[dict], *, today: date,
          tolerance: float = DEFAULT_DPS_TOLERANCE) -> dict[str, Any]:
    """Одна запись года прибыли из трёх источников (правила — в шапке модуля)."""
    ti = _latest(tinvest, "record_date")
    man = _latest(manual, "recorded_at")
    ordered = sorted(found, key=lambda r: (r["status"] == "declared", str(r.get("published") or "")))
    ref = ti or man or (ordered[-1] if ordered else None)
    if ref is None:
        raise ValueError(f"год {year}: нет ни одного источника")
    dps = float(ref["dps"])
    record = (ti or {}).get("record_date") or (man or {}).get("record_date") or next(
        (r["record_date"] for r in reversed(ordered) if r.get("record_date")), None)

    def agrees(r: Mapping[str, Any]) -> bool:
        return abs(float(r["dps"]) - dps) <= tolerance and (
            not r.get("record_date") or not record or r["record_date"] == record)

    notes: list[str] = []
    news_ok = [r for r in ordered if agrees(r)]
    for r in ordered:
        if not agrees(r):
            notes.append(f"{r['source']}: {r['dps']} ₽, отсечка {r.get('record_date') or '—'} — не совпадает "
                         f"с {dps} ₽, {record or '—'} ({r['url']})")
    man_ok = man is not None and agrees(man)
    if man is not None and not man_ok:
        notes.append(f"ручная запись: {man['dps']} ₽, отсечка {man.get('record_date') or '—'} — не совпадает "
                     f"с {dps} ₽, {record or '—'}")
    evidence = ([man] if man_ok else []) + list(reversed(news_ok))
    declared = [r for r in evidence if r.get("status") == "declared"]
    recommended = [r for r in evidence if r.get("status") == "recommended"]
    day = today.isoformat()
    cut_off = bool(ti and record and record <= day)          # отсечка T-Invest наступила: решение состоялось
    status = "recommended" if recommended and not declared and not cut_off else "declared"
    # Одна новость (решение ГОСА важнее рекомендации) и ручная запись; у объявленной записи
    # рекомендация совета вторым источником решения не считается.
    counted = [r for r in news_ok[-1:] + ([man] if man_ok else [])
               if status == "recommended" or r.get("status") == "declared"]
    sources = ([TINVEST] if ti else []) + [MANUAL if r is man else r["source"] for r in counted]
    decided = next((r.get("decided_date") for r in declared if r.get("decided_date")), None)
    advised = next((r.get("decided_date") for r in recommended if r.get("decided_date")), None)
    pay = (ti or {}).get("pay_date") or (man or {}).get("pay_date")
    if (ti or status == "declared") and pay and pay <= day:
        status = "paid"
    return {"year": int(year), "dps": dps, "status": status, "record_date": record,
            "last_buy_date": (ti or {}).get("last_buy_date") or (man or {}).get("last_buy_date"),
            "ex_date": record, "pay_date": pay, "decided_date": decided, "recommended_date": advised,
            "sources": sources, "notes": notes,
            "evidence": ([{"source": TINVEST, "dps": ti["dps"], "record_date": ti.get("record_date")}] if ti else [])
            + [{"source": r.get("source") if r is not man else MANUAL, "status": r.get("status"), "dps": r["dps"],
                "record_date": r.get("record_date"), "decided_date": r.get("decided_date"),
                "ref": r.get("url") or r.get("source")} for r in evidence]}


def build(store: Store, *, ticker: str, today: date, cfg: Mapping[str, Any],
          since: str | None = None) -> list[dict[str, Any]]:
    """Записи реестра по годам прибыли; `since` — отсечь записи с отсечкой (или решением) раньше даты."""
    tol = float((cfg.get("dividends") or {}).get("dps_tolerance", DEFAULT_DPS_TOLERANCE))
    by_year: dict[int, dict[str, list]] = {}
    for kind, rows in (("tinvest", tinvest_records(store, ticker)), ("news", news_decisions(store, cfg)),
                       ("manual", manual_records(store))):
        for r in rows:
            try:
                y = int(r["year"])
                float(r["dps"])
            except (KeyError, TypeError, ValueError):
                continue
            by_year.setdefault(y, {}).setdefault(kind, []).append(r)
    out = []
    for y, src in sorted(by_year.items()):
        rec = merge(y, src.get("tinvest", []), src.get("news", []), src.get("manual", []), today=today,
                    tolerance=tol)
        anchor = rec.get("record_date") or rec.get("decided_date")
        if since and anchor and anchor < since:
            continue
        out.append(rec)
    return out


def usable(rec: Mapping[str, Any]) -> bool:
    """Запись закрывает год: выплачена или объявлена и подтверждена двумя источниками (М§15)."""
    return rec.get("status") == "paid" or (rec.get("status") == "declared" and len(set(rec.get("sources") or [])) >= 2)


def alarm(records: list[Mapping[str, Any]], *, today: date, agm_quarter: int | None) -> str | None:
    """Тревога: прошёл конец квартала ГОСА года Y + 1, а годной записи за год Y нет (М§14.3)."""
    if not agm_quarter:
        return None
    year = today.year - 1
    end = periods.quarter_end(periods.quarter(today.year, int(agm_quarter)))
    if today <= end:
        return None
    if any(r.get("year") == year and usable(r) for r in records):
        return None
    have = [r for r in records if r.get("year") == year]
    detail = "; ".join(f"{r['status']} {r['dps']} ₽ ({', '.join(r['sources']) or 'без источника'})" for r in have)
    return (f"нет записи реестра за {year} год к концу квартала ГОСА ({end.isoformat()}): "
            f"{detail or 'записей нет'} — проверить ленты или внести `record-dividend` со ссылкой на документ эмитента")
