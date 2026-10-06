"""Ретро-проверка эталонов прибыли квартала на истории и измерение дисперсий для w.

Для каждого квартала истории с фактом МСФО (`actual.ni_q`, момент — выход отчёта)
эталоны считаются на том, что было известно на дату горизонта (моменты точек хранилища —
дата релиза РСБУ, выход МСФО, первая выкладка формы): мост — только прошлые годы (вне выборки).

**Дата горизонта** — отчёт − 30 или − 90 дней, но не раньше выхода МСФО прошлого квартала:
журнал открывает квартал только после него (`collect.open_period`), поэтому первая запись
T−90 первого квартала ложится ≈ за 60 дней до отчёта (МСФО за год выходит в конце февраля),
когда РСБУ января уже известен. Ретро и σ меряются на том же наборе знаний, что и журнал.

Эталоны (DESIGN §5): `ras_bridge` — РСБУ квартала × сезонный мост: известные месяцы
квартала плюс оценка остальных профилем прошлого года (на T−30 обычно известны два
месяца, на T−90 — ни одного у второго и третьего кварталов, один у первого и четвёртого;
без месяцев квартала это «последний месяц × профиль × мост»);
`yoy_growth` — год назад × рост г/г последнего известного квартала; `prev_quarter` —
последний известный квартал; `same_quarter_last_year`; `consensus` — консенсус у отчёта
(снят за день до выхода): он есть только на горизонте T−30, на T−90 его нет — иначе
эталон заглядывал бы на три месяца вперёд.

Дисперсии для w: σ_РСБУ(k) — относительная RMSE оценки «РСБУ × мост» при k первых
известных месяцах квартала (k = 0 — последний месяц прошлого квартала × профиль);
σ_база — RMSE эталона `yoy_growth` на горизонте T−90 (приор без РСБУ квартала).

**День знания σ_РСБУ(k)** (`known_date`) — день, когда стал известен k-й месяц квартала
(k = 0 — дата горизонта T−90), но не раньше выхода МСФО прошлого квартала — как и дата
горизонта: раньше журнал квартал не открывает, и мост фактов (отношения кварталов до
оцениваемого) в этот день ещё не содержал бы прошлого квартала. Всё прочее, что входит в
оценку, берётся таким, каким оно было известно в этот день, а не в день отчёта. Для
прибыли разницы нет (месяцы и мост прошлых лет к этому дню уже вышли), для ЧПМ она есть:
процентные активы конца квартала (форма 0409101) в день релиза месяца ещё не вышли и
продлеваются ростом, как в живом прогнозе, — на знании дня отчёта σ была бы ниже ошибки
живого прогноза.

ЧПМ и CoR (T3/T4, `ratio_sigma_table`): абсолютные ошибки оценки «РСБУ × мост» против факта
упр. базиса; мост — среднее отношений `quarters[].ratio` фактов за окно их метода (`n`;
нет — четыре квартала) раньше оцениваемого (вне выборки); σ_база — эталон «прошлый
квартал» на T−90. Перевод ЧПМ в упр. базис — нынешним мостом ядра (одно число на всю
историю): в σ он входит как постоянный сдвиг, а не как знание дня прогноза.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Callable, Mapping

from indicators import periods
from indicators.journal import BENCHMARK_TITLES
from indicators.nowcast import History, cor_ras, facts_ratio_before, history_bridge, nim_ras, ras_estimate
from indicators.store import Store

HORIZON_DAYS = {"T-30": 30, "T-90": 90}
MAIN = {"T-30": "ras_bridge", "T-90": "ras_bridge"}
CONSENSUS_HORIZON = "T-30"                       # консенсус снят у отчёта: эталон только ближнего горизонта
CONSENSUS_TITLE = "консенсус у отчёта (за день до выхода)"
FACTS_WINDOW = 4          # кварталов отношений фактов в мосте ретро, если у блока нет своего окна `n`
MONTHLY = {"nim": "nii_m", "cor": "cor_m"}       # цель → месячная метрика релиза, по которой считается k


def _not_before_opening(h: History, q: str, day: str) -> str:
    """День не раньше выхода МСФО прошлого квартала: до него журнал квартал не открывает (`collect.open_period`)."""
    opened = h.report_date(periods.shift_quarter(q, -1))
    return max(day, opened) if opened else day


def horizon_date(h: History, q: str, report: str, days: int) -> str:
    """Дата горизонта: отчёт − дни, но не раньше выхода МСФО прошлого квартала (как `collect.open_period`)."""
    return _not_before_opening(h, q, (date.fromisoformat(report) - timedelta(days=days)).isoformat())


def known_date(h: History, q: str, k: int, report: str,
               month: Callable[[str, str | None], tuple[float, str, str] | None]) -> str | None:
    """День, на знании которого меряется σ_РСБУ(k): k ≥ 1 — день, когда стал известен k-й месяц квартала
    (`month` — чтение месяца: ЧП, ЧПД или CoR релиза; не позже отчёта и не раньше открытия квартала —
    январь первого квартала выходит до МСФО за год); k = 0 — дата дальнего горизонта T−90, как у σ_база.
    Месяц неизвестен — None (оценки с таким k у квартала нет)."""
    if k == 0:
        return horizon_date(h, q, report, HORIZON_DAYS["T-90"])
    got = month(periods.quarter_months(q)[k - 1], None)
    return None if got is None else _not_before_opening(h, q, min(got[2][:10], report))


def benchmarks_at(h: History, store: Store, q: str, as_of: str) -> dict[str, float]:
    """Эталоны прибыли квартала q на дату as_of (что было известно)."""
    out: dict[str, float] = {}
    bridge, _ = history_bridge(h, q, as_of)
    est = ras_estimate(h, q, as_of)
    if est is not None and bridge is not None:
        out["ras_bridge"] = est["estimate"] * bridge
    last = h.latest_ifrs_quarter(q, as_of)
    if last is not None:
        out["prev_quarter"] = h.quarter_ifrs(last, as_of)
        base = h.quarter_ifrs(periods.shift_quarter(q, -4), as_of)
        grow_from = h.quarter_ifrs(periods.shift_quarter(last, -4), as_of)
        if base is not None and grow_from:
            out["yoy_growth"] = base * h.quarter_ifrs(last, as_of) / grow_from
    ly = h.quarter_ifrs(periods.shift_quarter(q, -4), as_of)
    if ly is not None:
        out["same_quarter_last_year"] = ly
    return out


def _consensus(store: Store, q: str) -> float | None:
    s = store.load("consensus.ni_q")
    return None if s is None else s.value_as_of(q)


def retro(store: Store, *, today: date | None = None) -> dict[str, Any]:
    """П§2 `nowcast.retro` (горизонт T−30) и `by_horizon` для обоих горизонтов."""
    today = today or date.today()
    h = History.load(store)
    by_h: dict[str, Any] = {}
    for hz, days in HORIZON_DAYS.items():
        rows = []
        for q in sorted(h.ifrs):
            report = h.report_date(q)
            if report is None or report > today.isoformat():
                continue
            marks = benchmarks_at(h, store, q, horizon_date(h, q, report, days))
            cons = _consensus(store, q) if hz == CONSENSUS_HORIZON else None
            if cons is not None:
                marks["consensus"] = cons
            if marks:
                rows.append({"period": q, "actual": h.ifrs[q][1], "report_date": report,
                             "benchmarks": {k: round(v, 4) for k, v in marks.items()}})
        rmse, bias = {}, {}
        keys = sorted({k for r in rows for k in r["benchmarks"]})
        for k in keys:
            errs = [(r["benchmarks"][k] - r["actual"], r["actual"]) for r in rows if k in r["benchmarks"]]
            if errs:
                rmse[k] = math.sqrt(sum((e / a) ** 2 for e, a in errs) / len(errs))
                bias[k] = sum(e for e, _ in errs) / len(errs)
        by_h[hz] = {"periods": rows, "rmse": rmse, "bias": bias, "n": {k: sum(1 for r in rows if k in r["benchmarks"])
                                                                      for k in keys},
                    "main": MAIN[hz], "horizon": hz,
                    "titles": {k: CONSENSUS_TITLE if k == "consensus" else BENCHMARK_TITLES.get(k, k) for k in keys}}
    main = dict(by_h["T-30"])
    main["by_horizon"] = by_h
    return main


def sigma_table(store: Store, *, today: date | None = None) -> dict[str, Any]:
    """σ_РСБУ(k), k = 0…3, и σ_база (доли) по истории — для веса w нау-каста."""
    today = today or date.today()
    h = History.load(store)
    errs: dict[int, list[float]] = {k: [] for k in range(4)}
    base: list[float] = []
    used: list[str] = []
    for q in sorted(h.ifrs):
        report = h.report_date(q)
        if report is None or report > today.isoformat():
            continue
        actual = h.ifrs[q][1]
        for k in range(4):
            day = known_date(h, q, k, report, h.month)
            if day is None:
                continue
            bridge, _ = history_bridge(h, q, day)
            est = ras_estimate(h, q, day, known=k)
            if bridge is not None and est is not None and est["months_known"] == k:
                errs[k].append((est["estimate"] * bridge - actual) / actual)
        marks = benchmarks_at(h, store, q, horizon_date(h, q, report, HORIZON_DAYS["T-90"]))
        if "yoy_growth" in marks:
            base.append((marks["yoy_growth"] - actual) / actual)
            used.append(q)

    def rmse(xs: list[float]) -> float | None:
        return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else None

    return {"ras": {str(k): rmse(v) for k, v in errs.items()}, "base": rmse(base),
            "n": {"ras": {str(k): len(v) for k, v in errs.items()}, "base": len(base)},
            "base_benchmark": "yoy_growth", "quarters": used}


def _window(block: Any) -> int:
    """Окно среднего отношений метода фактов (`n.v`), иначе `FACTS_WINDOW`."""
    n = block.get("n") if isinstance(block, Mapping) else None
    v = n.get("v") if isinstance(n, Mapping) else n
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1 else FACTS_WINDOW


def _rmse(xs: list[float]) -> float | None:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else None


def ratio_sigma_table(store: Store, *, bridge: Mapping[str, Any] | None,
                      to_mgmt_nim: Callable[[float], float] | None = None,
                      today: date | None = None) -> dict[str, Any]:
    """σ_РСБУ(k) и σ_база ЧПМ и CoR (абсолютные, доли) — для веса w целей T3/T4.

    Нет отношений моста в фактах (`nii`, `iea`, `cor` → `quarters`) — σ_РСБУ пусты, w = 0.
    ЧПМ базиса движка переводится в упр. функцией моста ядра `to_mgmt_nim`.
    """
    today = today or date.today()
    bridge = dict(bridge or {})
    h = History.load(store)
    iea_block = bridge.get("iea") if isinstance(bridge.get("iea"), Mapping) else {}
    basis = str(iea_block.get("basis") or "engine")
    out: dict[str, Any] = {}
    for key in ("nim", "cor"):
        errs: dict[int, list[float]] = {k: [] for k in range(4)}
        base: list[float] = []
        used: list[str] = []
        for q, (moment, actual) in sorted(h.mgmt.get(key, {}).items()):
            report = moment[:10]
            if report > today.isoformat():
                continue
            prev = h.mgmt_quarter(key, periods.shift_quarter(q, -1),
                                  horizon_date(h, q, report, HORIZON_DAYS["T-90"]))
            if prev is not None:
                base.append(prev - actual)
            if key == "nim":
                b_nii = facts_ratio_before(bridge.get("nii"), q, _window(bridge.get("nii")))
                b_iea = facts_ratio_before(bridge.get("iea"), q, _window(bridge.get("iea")))
                b = None if b_nii is None or not b_iea else b_nii / b_iea
            else:
                b = facts_ratio_before(bridge.get("cor"), q, _window(bridge.get("cor")))
            if b is None or (key == "nim" and basis != "mgmt" and to_mgmt_nim is None):
                continue
            for k in range(4):
                # знание дня, когда вышел k-й месяц: у ЧПМ невышедший конец квартала активов продлевается ростом
                day = known_date(h, q, k, report, lambda m, a: h.release_month(MONTHLY[key], m, a))
                if day is None:
                    continue
                est = nim_ras(h, q, day, known=k) if key == "nim" else cor_ras(h, q, day, known=k)
                got = None if est is None else (est["nii"]["months_known"] if key == "nim" else est["months_known"])
                if got != k:
                    continue
                v = est["value"] * b
                if key == "nim" and basis != "mgmt":
                    v = to_mgmt_nim(v)
                errs[k].append(v - actual)
            used.append(q)
        out[key] = {"ras": {str(k): _rmse(v) for k, v in errs.items()}, "base": _rmse(base),
                    "n": {"ras": {str(k): len(v) for k, v in errs.items()}, "base": len(base)},
                    "unit": "share (абсолютная ошибка)", "base_benchmark": "prev_quarter",
                    "bridge_window": {b: _window(bridge.get(b)) for b in (("nii", "iea") if key == "nim" else ("cor",))},
                    "quarters": used}
    return out


def mgmt_retro(store: Store, key: str) -> dict[str, Any]:
    """Наивные эталоны ЧПМ или CoR квартала (упр.): прошлый квартал и тот же квартал год назад."""
    s = store.load(f"actual.{key}")
    if s is None:
        return {"periods": [], "rmse": {}}
    hist = s.history()
    rows = []
    for q, a in sorted(hist.items()):
        marks = {}
        if periods.shift_quarter(q, -1) in hist:
            marks["prev_quarter"] = hist[periods.shift_quarter(q, -1)]
        if periods.shift_quarter(q, -4) in hist:
            marks["same_quarter_last_year"] = hist[periods.shift_quarter(q, -4)]
        if marks:
            rows.append({"period": q, "actual": a, "benchmarks": marks})
    rmse = {}
    for k in ("prev_quarter", "same_quarter_last_year"):
        e = [r["benchmarks"][k] - r["actual"] for r in rows if k in r["benchmarks"]]
        if e:
            rmse[k] = math.sqrt(sum(x * x for x in e) / len(e))
    return {"periods": rows, "rmse": rmse, "unit": "share (абсолютная ошибка)"}
