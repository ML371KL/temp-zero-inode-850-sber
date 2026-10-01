"""Что изменилось: разложение изменения точки и медианы между выпусками (М§16).

Шаги в фиксированном порядке, каждый — пересчёт с заменой одного входа прошлого выпуска A
на вход текущего B при уже заменённых предыдущих:

1. `valuation_date` — перекат даты оценки v_A → v_B (реестр A);
2. `register` — реестр дивидендов (новые объявления, экс-даты, мост);
3. `facts` — факты и перезаякоривание (дата фактов, наблюдения A-P2u);
4. `book` — суждения книги (прогулка по ключам — `ops/tools/walk_book.py`, вне выпуска);
5. `worlds` — блок миров целиком;
6. `engine` — код ядра (остаток при тех же книге и фактах);
7. `rounding` — округление печати.

Книга и факты прошлого выпуска в сборке не восстанавливаются: шаги 1–2 считаются на книге и
фактах B, а разность «A на книге B» − «опубликованное A» уходит на первый сменившийся вход
из facts → book → worlds → engine (остальные сменившиеся названы в пояснении). Смена
рыночной цены стоимость не меняет — печатается отдельной строкой. Снимок входов
(`inputs_snapshot`) кладётся в выпуск вне хэша (`changes.snapshot`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from model.book import Book, Facts, canonical_json
from model.grid import DividendRecord, LiveInputs, run_grid
from model.timeline import to_date

__all__ = ["Step", "inputs_snapshot", "attribute", "TITLES", "ORDER", "register_to_dicts",
           "register_from_dicts"]

ORDER = ("valuation_date", "register", "facts", "book", "worlds", "engine", "rounding")
TITLES = {
    "valuation_date": "Перекат даты оценки",
    "register": "Реестр дивидендов",
    "facts": "Факты и перезаякоривание",
    "book": "Суждения книги",
    "worlds": "Миры ставок",
    "engine": "Код ядра",
    "rounding": "Округление печати",
}


@dataclass(frozen=True)
class Step:
    component: str
    title: str
    point_rub: float
    median_rub: float | None
    note: str = ""


def _sha(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def register_to_dicts(register: Sequence[DividendRecord]) -> list[dict[str, Any]]:
    out = []
    for r in register:
        out.append({"year": r.year, "dps": r.dps, "status": r.status,
                    "record_date": None if r.record_date is None else r.record_date.isoformat(),
                    "last_buy_date": None if r.last_buy_date is None else r.last_buy_date.isoformat(),
                    "ex_date": None if r.ex_date is None else r.ex_date.isoformat(),
                    "pay_date": None if r.pay_date is None else r.pay_date.isoformat(),
                    "sources": list(r.sources)})
    return out


def _d(x: Any) -> date | None:
    return None if x in (None, "") else to_date(x)


def register_from_dicts(rows: Sequence[Mapping[str, Any]]) -> tuple[DividendRecord, ...]:
    return tuple(DividendRecord(year=int(r["year"]), dps=float(r["dps"]), status=str(r["status"]),
                                record_date=_d(r.get("record_date")), last_buy_date=_d(r.get("last_buy_date")),
                                ex_date=_d(r.get("ex_date")), pay_date=_d(r.get("pay_date")),
                                sources=tuple(r.get("sources") or ())) for r in rows)


def inputs_snapshot(book: Book, facts: Facts, live: LiveInputs) -> dict[str, Any]:
    """Входы выпуска, по которым следующий выпуск разложит изменение (вне хэша)."""
    data = dict(book.source or book.data)
    worlds = {"worlds": data.get("worlds"), "worlds_bank": data.get("worlds_bank")}
    rest = {k: v for k, v in data.items() if k not in ("worlds", "worlds_bank")}
    obs = (data.get("joint") or {}).get("regime_update", {}).get("observations") or []
    return {
        "valuation_date": live.valuation_date.isoformat(),
        "prices": {t: float(p) for t, p in live.prices.items()},
        "price_dates": {t: d.isoformat() for t, d in live.price_dates.items()},
        "register": register_to_dicts(live.register),
        "book_version": str(book.get("meta.version")), "book_digest": book.digest,
        "book_rest_digest": _sha(rest), "worlds_digest": _sha(worlds), "observations_digest": _sha(obs),
        "facts_digest": facts.digest, "facts_date": str(book.get("meta.facts_date")),
    }


def attribute(previous: Mapping[str, Any], book: Book, facts: Facts, live: LiveInputs, *, engine_commit: str,
              current: Mapping[str, float] | None = None, anchor: Any = None) -> list[Step]:
    """Шаги М§16 между прошлым выпуском `previous` (JSON выпуска) и текущими входами.

    `current` — {"point", "median", "printed_point", "printed_median"} текущего выпуска
    (нет — точка считается здесь, медиана не раскладывается); `anchor` — `MedianAnchor`
    текущей полосы (нет — медиана не раскладывается)."""
    snap = ((previous.get("changes") or {}).get("snapshot") or previous.get("inputs_snapshot") or {})
    fv = previous.get("fair_value") or {}
    prev_point = fv.get("central")
    prev_median = (fv.get("headline") or {}).get("median")
    if not snap or prev_point is None:
        return []
    lam = float(book.get("joint.own_macro_confidence"))
    a_live = LiveInputs(valuation_date=to_date(snap["valuation_date"]),
                        prices={t: float(p) for t, p in (snap.get("prices") or live.prices).items()},
                        price_dates={t: to_date(d) for t, d in (snap.get("price_dates") or {}).items()}
                        or dict(live.price_dates),
                        register=register_from_dicts(snap.get("register") or []))
    main = str(book.get("meta.company.main_ticker"))
    if main not in a_live.prices:
        a_live = LiveInputs(a_live.valuation_date, dict(live.prices), dict(live.price_dates), a_live.register)
    rolled = LiveInputs(valuation_date=live.valuation_date, prices=dict(live.prices),
                        price_dates=dict(live.price_dates), register=a_live.register)
    p0 = run_grid(book, facts, a_live).point
    p1 = run_grid(book, facts, rolled).point
    cur = dict(current or {})
    p2 = cur.get("point")
    if p2 is None:
        p2 = run_grid(book, facts, live).point
    m0 = m1 = m2 = None
    if anchor is not None and prev_median is not None and cur.get("median") is not None:
        m2 = float(cur["median"])
        m0 = anchor.at(live=a_live, lam=lam)["central"]
        m1 = anchor.at(live=rolled, lam=lam)["central"]
    now = inputs_snapshot(book, facts, live)
    changed = {
        "facts": snap.get("facts_digest") != now["facts_digest"] or snap.get("observations_digest") != now[
            "observations_digest"],
        "book": snap.get("book_rest_digest") != now["book_rest_digest"],
        "worlds": snap.get("worlds_digest") != now["worlds_digest"],
    }
    was_commit = str((previous.get("meta") or {}).get("engine_commit") or "")
    changed["engine"] = bool(was_commit and engine_commit and was_commit != engine_commit)
    rest_point = p0 - float(prev_point)
    rest_median = None if m0 is None else m0 - float(prev_median)
    first = next((k for k in ("facts", "book", "worlds", "engine") if changed[k]), "engine")
    others = [k for k in ("facts", "book", "worlds", "engine") if changed[k] and k != first]
    note_rest = ("вместе с: " + ", ".join(TITLES[k].lower() for k in others)) if others else (
        "" if any(changed.values()) else "книга, факты и код те же — остаток расчёта")
    if changed["book"] and first == "book":
        note_rest = (note_rest + "; " if note_rest else "") + (
            f"книга {snap.get('book_version')} → {book.get('meta.version')}; вклады отдельных ключей — "
            "прогулка книги вне выпуска")
    steps = [
        Step("valuation_date", TITLES["valuation_date"], p1 - p0, None if m1 is None else m1 - m0,
             f"{snap['valuation_date']} → {live.valuation_date.isoformat()}"
             + (" (на книге и фактах текущего выпуска)" if any(changed[k] for k in ("facts", "book", "worlds")) else "")),
        Step("register", TITLES["register"], p2 - p1, None if m1 is None else m2 - m1,
             f"записей: {len(a_live.register)} → {len(live.register)}"),
    ]
    for comp in ("facts", "book", "worlds", "engine"):
        if comp == first:
            steps.append(Step(comp, TITLES[comp], rest_point, rest_median, note_rest))
        else:
            steps.append(Step(comp, TITLES[comp], 0.0, None if m0 is None else 0.0,
                              f"входит в строку «{TITLES[first].lower()}»" if changed[comp] else ""))
    pp_prev = fv.get("printed_central")
    pp_now = cur.get("printed_point")
    rounding_p = 0.0
    if pp_prev is not None and pp_now is not None:
        rounding_p = (float(pp_now) - float(pp_prev)) - (p2 - float(prev_point))
    pm_prev = (fv.get("headline") or {}).get("printed_median")
    pm_now = cur.get("printed_median")
    rounding_m = None
    if m2 is not None and pm_prev is not None and pm_now is not None:
        rounding_m = (float(pm_now) - float(pm_prev)) - (m2 - float(prev_median))
    steps.append(Step("rounding", TITLES["rounding"], rounding_p, rounding_m, "печать к шагу книги"))
    return steps

