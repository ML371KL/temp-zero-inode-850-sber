"""Живые входы выпуска: цены категорий, дата оценки, реестр дивидендов; коридоры годности (М§15).

Живые только цены категорий акций, дата оценки и реестр объявленных дивидендов; всё
остальное — книга. Кривая ОФЗ и ключевая ставка — наблюдение (флаг `book_update`,
плитки), в оценку не идут.

**Цена** — наблюдения сборщиков (`indicators.outputs.release_inputs`: T-Invest, запасной
ISS), новейшее последним. Годность (М§15): дата сделки не позже дня прогона и не старше
7 дней; не раньше последней принятой цены (повтор не откатывает цену назад); отклонение от
последней принятой ≤ 30 % (при переходе через экс-дату эталон уменьшается на DPS);
отличие в 10 раз и больше — чужие единицы, не принимается никогда; если последняя принятая
старше недели, скачок больше 30 % принимается при подтверждении соседним торговым днём
(в пределах 5 %). Не прошедшая цена заменяется последней принятой (`fallback`, флаг
`price_fallback`); у главного тикера нет запасной — выпуск не собирается (`LiveError`);
вторая категория берёт книжную цену с пометкой.

**Дата оценки** — дата принятой цены главного тикера, не раньше `meta.date` книги.

**Реестр** — записи `declared` принимаются при двух источниках (T-Invest и новость
Интерфакса/ТАСС о решении ГОСА; запасной второй — ручная запись оператора); 0 < DPS < 0,5 × цены;
экс-дата = отсечка; последний день покупки — раньше отсечки не больше чем на 5 дней;
отсечка — через 10–20 дней после решения (если дата решения известна). `recommended` — только
плашка. В прогон идут объявленные и выплаченные записи с экс-датой позже даты фактов
(раньше — они уже в капитале якоря). Флаг `dividend_register` (М§14.3): прошёл конец квартала
ГОСА года Y + 1 (дата оценки позже E_qA), а записи `declared` за год Y нет — плашка и тревога
сборки (без записи точка падает на дивиденд модели в конце квартала ГОСА, М§8.1).

**Деградация и тревога** (INTERFACES §6, В9/В20). Все причины деградации печатаются
(`LiveReport.degraded`), но тревогу сборки (`degraded_flag`, код 3) поднимают только две: деградировал
живой вход оценки (цена не принята или принята со снятой проверкой скачка, запись реестра не принята,
кривая или ключевая не приняты) либо источник уже получил код 3 у сборщиков (`collector["alarm"]`).
Восполнимый источник плиток, деградировавший меньше трёх тактов подряд, флага не поднимает. Сборщик
без списка `alarm` (прежний формат) — тревожна каждая его причина.

Последняя принятая цена живёт в `$BANK_STATE_DIR/live.json` и обновляется только после
годного выпуска (`write_last_accepted`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from model.book import ROOT, Book, Facts
from model.checks import pct
from model.grid import DividendRecord, LiveInputs, live_from_book
from model.timeline import make_timeline, period_str, to_date

__all__ = ["LiveReport", "LiveError", "apply_live", "read_last_accepted", "write_last_accepted",
           "state_dir", "check_price", "check_register", "register_gaps", "MAX_PRICE_AGE_DAYS"]

STATE_ENV = "BANK_STATE_DIR"
LIVE_FILE = "live.json"
MAX_PRICE_AGE_DAYS = 7           # цена не старше недели (М§15)
MAX_PRICE_JUMP = 0.30            # ±30 % к последней принятой (М§15)
PRICE_UNITS_FACTOR = 10.0        # в 10 раз и больше — чужие единицы (М§15)
REFERENCE_MAX_AGE_DAYS = 7       # «скачок за такт» определён, пока эталон не старше недели
PRICE_CONFIRM_BAND = 0.05        # подтверждение соседним днём — в пределах 5 % (М§15)
DPS_PRICE_CAP = 0.5              # DPS < 0,5 × цены (М§15, П§7 п. 9)
RECORD_AFTER_DECISION = (10, 20)  # отсечка через 10–20 дней после решения (политика п. 4.1)
LAST_BUY_MAX_GAP_DAYS = 5        # последний день покупки — предыдущий торговый день
RATE_MIN, RATE_MAX = 0.03, 0.40  # узел кривой вне 3–40 % — единицы, а не рынок
MAX_CURVE_AGE_DAYS = 7
BP = 10_000
CURVE_NODES = ("1", "3", "5", "10")
SHIFT_NODES = ("5", "10")        # флаг book_update — по узлам 5 и 10 лет (М§14.3)
SOURCE_WORDS = {"tinvest": "T-Invest", "iss": "Мосбиржа, TQBR", "book": "книга"}

NAMED_LITERALS = {
    0.5: "DPS_PRICE_CAP: DPS < 0,5 × цены — правило годности записи реестра (М§15, П§7 п. 9)",
    0.3: "MAX_PRICE_JUMP: предел скачка цены к последней принятой (М§15)",
    0.05: "PRICE_CONFIRM_BAND: подтверждение скачка соседним днём (М§15)",
    0.03: "RATE_MIN: нижняя граница узла кривой (единицы, а не рынок)",
    0.4: "RATE_MAX: верхняя граница узла кривой (единицы, а не рынок)",
    10000: "BP: базисных пунктов в единице",
}


class LiveError(RuntimeError):
    """Живой вход, без которого выпуск не собирается (цена главного тикера)."""


@dataclass
class LiveReport:
    applied: bool
    degraded: list[str]
    prices: dict[str, dict]
    curve: dict | None
    key_rate: dict | None
    register: dict
    flags: dict[str, dict]
    # сверх договора (только добавление)
    valuation_date: date | None = None
    book_date: date | None = None
    book_age_days: int = 0
    fetched_at: str | None = None
    records: list[dict] = field(default_factory=list)     # принятые записи реестра (все статусы)
    rejected: list[dict] = field(default_factory=list)    # отвергнутые записи с причиной
    alarms: list[str] = field(default_factory=list)       # тревожные причины деградации (подмножество degraded
    #                                                       и источники из collector["alarm"])

    @property
    def degraded_flag(self) -> bool:
        """Тревога сборки (§6): деградировал живой вход оценки или источник из `collector["alarm"]`."""
        return bool(self.alarms)


# ------------------------------------------------------------------ состояние


def state_dir(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(STATE_ENV)
    return Path(env) if env else ROOT / "var" / "state"


def read_last_accepted(state_dir_: Path | None = None) -> dict:
    """`$BANK_STATE_DIR/live.json`; нет файла или он битый — пустой словарь."""
    path = state_dir(state_dir_) / LIVE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_last_accepted(report: LiveReport, state_dir_: Path | None = None) -> None:
    """Последние принятые цены и реестр — только после годного выпуска."""
    root = state_dir(state_dir_)
    root.mkdir(parents=True, exist_ok=True)
    old = read_last_accepted(root)
    prices = dict(old.get("prices") or {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for t, row in report.prices.items():
        if row.get("status") == "live" and row.get("accepted"):
            prices[t] = {"value": row["value"], "date": row["date"], "time": row.get("time"),
                         "source": row.get("source"), "accepted_at": now}
    data = {"prices": prices,
            "register": {"as_of": report.register.get("as_of"), "rows": list(report.records)},
            "valuation_date": None if report.valuation_date is None else report.valuation_date.isoformat(),
            "written_at": now}
    tmp = root / (LIVE_FILE + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(root / LIVE_FILE)


# ------------------------------------------------------------------ проверки


def _day(x: Any) -> date | None:
    if x is None or x == "":
        return None
    try:
        return to_date(x) if not isinstance(x, str) else date.fromisoformat(x[:10])
    except (TypeError, ValueError):
        return None


def _exdate_drop(register: Sequence[Mapping[str, Any]], after: date | None, upto: date) -> float:
    """Σ DPS записей с экс-датой в (after; upto] — на столько эталон цены уменьшается."""
    total = 0.0
    for rec in register:
        ex = _day(rec.get("ex_date")) or _day(rec.get("record_date"))
        if rec.get("status") in ("declared", "paid") and ex is not None and (after is None or ex > after) \
                and ex <= upto:
            total += float(rec.get("dps") or 0.0)
    return total


def check_price(points: Sequence[Any], *, reference: float | None, reference_date: date | None,
                today: date, register: Sequence[Mapping[str, Any]] = ()) -> tuple[dict | None, str | None, str | None]:
    """Годность новейшего наблюдения цены (М§15) → (принятое, причина отказа, пометка деградации)."""
    if not points:
        return None, "наблюдений цены нет", None
    p = points[-1]
    price, d = float(p.price), _day(p.date)
    if d is None:
        return None, f"дата сделки {p.date!r} не разбирается", None
    got = {"value": price, "date": d.isoformat(), "time": p.time, "source": p.source}
    if price <= 0:
        return None, f"цена {price} не положительна", None
    if d > today:
        return None, f"дата сделки {d} позже дня прогона {today}", None
    if (today - d).days > MAX_PRICE_AGE_DAYS:
        return None, f"цена от {d} старше {MAX_PRICE_AGE_DAYS} дней", None
    if reference is None:
        return got, None, None
    if reference_date is not None and d < reference_date:
        return None, f"цена от {d} раньше последней принятой ({reference_date}) — назад не откатывается", None
    ref = reference - _exdate_drop(register, reference_date, d)
    if ref <= 0:
        return got, None, None
    ratio = price / ref
    if ratio >= PRICE_UNITS_FACTOR or ratio <= 1 / PRICE_UNITS_FACTOR:
        return None, (f"цена {price:.2f} ₽ отличается от эталона {ref:.2f} ₽ в {max(ratio, 1 / ratio):.0f} раз — "
                      "чужие единицы, нужна правка сборщика"), None
    if abs(ratio - 1) <= MAX_PRICE_JUMP:
        return got, None, None
    gap = None if reference_date is None else (d - reference_date).days
    if gap is not None and gap <= REFERENCE_MAX_AGE_DAYS:
        return None, (f"цена {price:.2f} ₽ отличается от последней принятой {ref:.2f} ₽ на {pct(abs(ratio - 1), 0)} "
                      f"при пределе {pct(MAX_PRICE_JUMP, 0)}"), None
    for q in reversed(points[:-1]):
        qd = _day(q.date)
        if qd is None or qd >= d or (d - qd).days > REFERENCE_MAX_AGE_DAYS:
            continue
        if reference_date is not None and qd <= reference_date:
            continue
        if abs(float(q.price) / price - 1) <= PRICE_CONFIRM_BAND:
            return got, None, (f"проверка скачка цены снята: эталон {ref:.2f} ₽ старше {REFERENCE_MAX_AGE_DAYS} дней; "
                               f"скачок {'+' if ratio > 1 else ''}{pct(ratio - 1, 0)} подтверждён днём {qd} "
                               f"({float(q.price):.2f} ₽)")
        break
    return None, (f"цена {price:.2f} ₽ отличается от эталона {ref:.2f} ₽ на {pct(abs(ratio - 1), 0)}; эталон старше "
                  f"{REFERENCE_MAX_AGE_DAYS} дней — скачок ждёт подтверждения соседним торговым днём"), None


def check_register(rows: Sequence[Mapping[str, Any]], *, price: float) -> tuple[list[dict], list[dict]]:
    """Записи реестра по правилам М§15 → (принятые, отвергнутые с причиной)."""
    ok, bad = [], []
    for raw in rows:
        rec = {k: raw.get(k) for k in ("year", "dps", "status", "record_date", "last_buy_date", "ex_date",
                                       "pay_date", "decided_date")}
        rec["sources"] = [str(s) for s in raw.get("sources") or []]
        status = str(rec.get("status") or "")
        why = None
        try:
            dps = float(rec["dps"])
            rec["year"] = int(rec["year"])
        except (KeyError, TypeError, ValueError):
            dps, why = 0.0, "нет года или DPS"
        record, ex = _day(rec.get("record_date")), _day(rec.get("ex_date"))
        last_buy, decided = _day(rec.get("last_buy_date")), _day(rec.get("decided_date"))
        if why is None and status not in ("declared", "recommended", "paid"):
            why = f"незнакомый статус {status!r}"
        if why is None and not 0 < dps < DPS_PRICE_CAP * price:
            why = f"DPS {dps} вне (0; {DPS_PRICE_CAP} × цены)"
        if why is None and status in ("declared", "paid"):
            if record is None:
                why = "нет даты отсечки"
            elif ex is not None and ex != record:
                why = f"экс-дата {ex} ≠ отсечке {record} (режим T+1)"
            elif last_buy is not None and not (0 < (record - last_buy).days <= LAST_BUY_MAX_GAP_DAYS):
                why = f"последний день покупки {last_buy} — не предыдущий торговый день перед {record}"
            elif decided is not None and not (RECORD_AFTER_DECISION[0] <= (record - decided).days
                                              <= RECORD_AFTER_DECISION[1]):
                why = f"отсечка {record} не через 10–20 дней после решения {decided}"
            elif status == "declared" and len(set(rec["sources"])) < 2:
                why = "объявление подтверждено одним источником — ждёт второго"
        if why is None:
            rec["dps"] = dps
            if status in ("declared", "paid") and ex is None:
                rec["ex_date"] = rec.get("record_date")
            ok.append(rec)
        else:
            bad.append({**rec, "reason": why})
    return ok, bad


def _records(rows: Sequence[Mapping[str, Any]], facts_date: date) -> tuple[DividendRecord, ...]:
    """Записи для прогона: объявленные и выплаченные с экс-датой позже даты фактов."""
    out = []
    for r in rows:
        if r.get("status") not in ("declared", "paid"):
            continue
        ex = _day(r.get("ex_date")) or _day(r.get("record_date"))
        if ex is None or ex <= facts_date:
            continue
        out.append(DividendRecord(year=int(r["year"]), dps=float(r["dps"]), status=str(r["status"]),
                                  record_date=_day(r.get("record_date")), last_buy_date=_day(r.get("last_buy_date")),
                                  ex_date=ex, pay_date=_day(r.get("pay_date")),
                                  sources=tuple(r.get("sources") or ())))
    return tuple(sorted(out, key=lambda x: x.year))


def _seed_rows(facts: Facts) -> list[dict]:
    rows = []
    for rec in facts.file("dividends").get("register_seed") or []:
        plain = {k: (v.get("v") if isinstance(v, Mapping) and "v" in v else v) for k, v in rec.items()}
        plain.setdefault("sources", ["факты книги: стартовый реестр"])
        rows.append(plain)
    return rows


def _curve(book: Book, curve: Mapping[str, Any] | None, today: date, degraded: list[str]) -> dict | None:
    if not curve:
        return None
    nodes = {k: curve.get("nodes", {}).get(k) for k in CURVE_NODES}
    if any(v is None for v in nodes.values()):
        degraded.append("кривая ОФЗ: не все узлы 1, 3, 5, 10 лет — не принята как наблюдение")
        return None
    bad = [f"{k} лет = {v}" for k, v in nodes.items() if not RATE_MIN <= float(v) <= RATE_MAX]
    if bad:
        degraded.append(f"кривая ОФЗ: узлы вне {pct(RATE_MIN, 0)[:-2]}–{pct(RATE_MAX, 0)} ({', '.join(bad)}) — не принята")
        return None
    as_of = _day(curve.get("as_of"))
    if as_of is None or (today - as_of).days > MAX_CURVE_AGE_DAYS or as_of > today:
        degraded.append(f"кривая ОФЗ от {curve.get('as_of')} старше {MAX_CURVE_AGE_DAYS} дней — не принята")
        return None
    world = str(book.get("joint.macro_neutral_world"))
    book_nodes = {k: float(book.get(f"worlds.{world}.zero_curve.{k}")) for k in CURVE_NODES}
    return {"as_of": as_of.isoformat(), "nodes": {k: float(v) for k, v in nodes.items()},
            "book_nodes": book_nodes, "book_world": world,
            "shift_bp": {k: round((float(nodes[k]) - book_nodes[k]) * BP) for k in SHIFT_NODES},
            "source": curve.get("source")}


def _flags(book: Book, facts: Facts, report: LiveReport, outputs: Any, today: date) -> dict[str, dict]:
    flags: dict[str, dict] = {}
    fb = [f"{t}: {r['reason']}" for t, r in report.prices.items() if r["status"] in ("fallback",)
          or (report.applied and r["status"] == "book")]
    flags["price_fallback"] = {"raised": bool(fb), "detail": "; ".join(fb) or None}
    shift = float(book.get("checks.book_update.shift_bp"))
    max_age = int(book.get("checks.book_update.max_age_days"))
    why = []
    if report.curve:
        moved = {k: v for k, v in report.curve["shift_bp"].items() if abs(v) >= shift}
        if moved:
            why.append("кривая ОФЗ ушла от книги: " + ", ".join(f"{k} лет {v:+d} б.п." for k, v in moved.items()))
    if report.book_age_days > max_age:
        why.append(f"книге {report.book_age_days} дней при пределе {max_age}")
    flags["book_update"] = {"raised": bool(why), "detail": "; ".join(why) or None}
    rec = [r for r in report.records if r.get("status") == "recommended"]
    flags["dividend_recommended"] = {
        "raised": bool(rec),
        "detail": "; ".join(f"за {r['year']} год: {r['dps']} ₽ (рекомендация совета)" for r in rec) or None}
    missing = register_gaps(book, report.records, report.valuation_date or today)
    flags["dividend_register"] = {"raised": bool(missing),
                                  "detail": ("квартал ГОСА прошёл, записи declared в реестре нет: "
                                             + "; ".join(missing)) if missing else None}
    ras = (getattr(outputs, "flags", None) or {}).get("ras_mismatch") if outputs is not None else None
    flags["ras_mismatch"] = {"raised": bool(ras and ras.get("raised")),
                             "detail": (ras or {}).get("detail") or None}
    return flags


def register_gaps(book: Book, records: Sequence[Mapping[str, Any]], valuation_date: date) -> list[str]:
    """Годы прибыли Y, у которых конец квартала ГОСА Y + 1 прошёл (E_qA после даты фактов и раньше
    даты оценки), а записи `declared` (или `paid`) за Y в реестре нет (флаг `dividend_register`, М§14.3)."""
    tl = make_timeline(book)
    agm = int(book.get("dividends.calendar.agm_quarter"))
    have = {int(r["year"]) for r in records if r.get("status") in ("declared", "paid") and r.get("year") is not None}
    out = []
    for year in range(tl.anchor_year - 1, tl.last_year):
        q_a = tl.index(period_str(year + 1, agm))
        if not 1 <= q_a <= tl.Q:
            continue
        end = tl.end(q_a)
        if end < valuation_date and year not in have:
            out.append(f"дивиденд за {year} год (ГОСА — квартал {tl.period(q_a)}, конец {end.isoformat()})")
    return out


# ------------------------------------------------------------------ сборка входов


def _collector_words() -> Mapping[str, str]:
    """Названия сборщиков словами (`indicators.outputs.COLLECTOR_WORDS`, П§0.2); нет модуля — ключи как есть."""
    try:
        from indicators.outputs import COLLECTOR_WORDS       # ядро читает индикаторы через outputs (INTERFACES §5)
    except ImportError:
        return {}
    return COLLECTOR_WORDS


def apply_live(book: Book, facts: Facts, outputs: Any | None, *, today: date | None = None,
               previous: Mapping[str, Any] | None = None) -> tuple[LiveInputs, LiveReport]:
    """Живые входы прогона и отчёт о них; `outputs=None` — цена, дата и реестр книги."""
    today = today or date.today()
    book_date = to_date(book.get("meta.date"))
    facts_date = to_date(book.get("meta.facts_date"))
    main = str(book.get("meta.company.main_ticker"))
    tickers = [str(t) for t in book.get("meta.company.tickers")]
    book_prices = {str(t): float(p) for t, p in book.get("meta.market_price").items()}
    previous = dict(previous or {})
    prev_prices = previous.get("prices") or {}
    degraded: list[str] = []
    if outputs is None:
        base = live_from_book(book, facts)
        prices = {t: {"value": base.prices[t], "date": base.price_dates[t].isoformat(), "time": None,
                      "accepted": False, "status": "book", "reason": "сборка на цене книги",
                      "source": "book", "book_price": book_prices.get(t),
                      "last_accepted": None} for t in tickers}
        records = _seed_rows(facts)
        report = LiveReport(applied=False, degraded=[], prices=prices, curve=None, key_rate=None,
                            register={"as_of": book_date.isoformat(), "entries": len(records),
                                      "note": "реестр книги (стартовые записи фактов)"},
                            flags={}, valuation_date=base.valuation_date, book_date=book_date,
                            book_age_days=(today - book_date).days, fetched_at=None, records=records)
        report.flags = _flags(book, facts, report, None, today)
        return base, report

    alarms: list[str] = []
    collector = outputs.collector or {}
    reasons = [str(x) for x in collector.get("degraded") or []]
    degraded.extend(reasons)
    if collector.get("alarm") is None:
        alarms.extend(reasons)                       # прежний формат сборщиков: тревожна каждая причина
    else:
        words = _collector_words()
        alarms.extend(f"источник «{words.get(str(name), name)}»: деградация не снята повтором — тревога сборщиков"
                      for name in collector.get("alarm") or [])

    def live_alarm(reason: str) -> None:             # деградация живого входа оценки — тревога сразу
        degraded.append(reason)
        alarms.append(reason)

    raw_register = list(outputs.register or ())
    # эталон цены: последняя принятая; нет — цена книги на дату книги
    prices: dict[str, dict] = {}
    accepted_price: dict[str, float] = {}
    accepted_date: dict[str, date] = {}
    for t in tickers:
        last = prev_prices.get(t) or {}
        ref = float(last["value"]) if last.get("value") else book_prices.get(t)
        ref_date = _day(last.get("date")) if last.get("value") else book_date
        got, why, note = check_price(tuple(outputs.prices.get(t) or ()), reference=ref, reference_date=ref_date,
                                     today=today, register=raw_register)
        row = {"book_price": book_prices.get(t),
               "last_accepted": ({"value": float(last["value"]), "date": last.get("date")}
                                 if last.get("value") else None)}
        if got is not None:
            row.update(value=got["value"], date=got["date"], time=got["time"], source=got["source"],
                       accepted=True, status="live", reason=None)
            if note:
                live_alarm(f"{t}: {note}")
        elif last.get("value"):
            row.update(value=float(last["value"]), date=last.get("date"), time=last.get("time"),
                       source=last.get("source"), accepted=False, status="fallback",
                       reason=f"{why}; взята последняя принятая")
            live_alarm(f"цена {t}: {why} — взята последняя принятая {float(last['value']):.2f} ₽ от {last.get('date')}")
        elif t != main:
            row.update(value=book_prices[t], date=book_date.isoformat(), time=None, source="book",
                       accepted=False, status="book", reason=f"{why}; последней принятой нет — цена книги")
            live_alarm(f"цена {t}: {why} — последней принятой нет, взята цена книги")
        else:
            raise LiveError(f"цена главного тикера {t}: {why}; последней принятой нет — выпуск не собирается (М§15)")
        prices[t] = row
        accepted_price[t] = float(row["value"])
        accepted_date[t] = _day(row["date"]) or book_date
    valuation = max(accepted_date[main], book_date)
    ok, bad = check_register(raw_register, price=accepted_price[main])
    by_year: dict[tuple[int, bool], dict] = {}
    for r in _seed_rows(facts) + ok:
        by_year[(int(r["year"]), str(r["status"]) == "recommended")] = r
    decided = {y for y, rec in by_year if not rec}
    # рекомендация года, по которому уже есть решение ГОСА, — не нужна
    records = sorted((r for (y, rec), r in by_year.items() if not (rec and y in decided)),
                     key=lambda r: (int(r["year"]), str(r["status"])))
    for r in bad:
        live_alarm(f"реестр: запись за {r.get('year')} год не принята — {r['reason']}")
    seen = len(degraded)
    curve = _curve(book, outputs.curve, today, degraded)
    alarms.extend(degraded[seen:])                   # кривая не принята — живой вход
    key_rate = None
    kr = outputs.key_rate
    if kr and kr.get("value") is not None and 0 < float(kr["value"]) < 1:
        key_rate = {"value": float(kr["value"]), "date": kr.get("date"), "since": kr.get("since")}
    elif kr:
        live_alarm(f"ключевая ставка {kr.get('value')!r} вне (0; 1) — не принята")
    live = LiveInputs(valuation_date=valuation, prices=dict(accepted_price),
                      price_dates=dict(accepted_date), register=_records(records, facts_date))
    report = LiveReport(
        applied=True, degraded=degraded, prices=prices, curve=curve, key_rate=key_rate,
        register={"as_of": today.isoformat(), "entries": len(records),
                  "note": "реестр сборщиков (T-Invest, второй источник) и факты книги; "
                          "в оценку — объявленные с экс-датой позже даты фактов"},
        flags={}, valuation_date=valuation, book_date=book_date, book_age_days=(today - book_date).days,
        fetched_at=getattr(outputs, "as_of", None), records=records, rejected=bad, alarms=alarms)
    report.flags = _flags(book, facts, report, outputs, today)
    return live, report

