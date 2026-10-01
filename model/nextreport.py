"""Что даст отчёт: открытый квартал, ожидание модели, таблицы CoR и ЧПМ, нейтральные значения (М§13).

* Открытый квартал q* — самый ранний прогнозный квартал без внесённого наблюдения A-P2u.
* Ожидание модели CoR и ЧПМ для q* — прогноз фильтра A-P2u Σ_r P(r | наблюдения) ×
  (μ_x,r(q*) + ρ^k m_x,r) в базисе движка (μ — среднее слоя «свой взгляд» по клеткам режима
  без отклонений, М§12), переведённое мостом в упр. базис. Наблюдение, равное ожиданию, точно
  нейтрально только при одном режиме; при смеси режимов оно их перевзвешивает (обучение, М§12)
  — мера «отчёт без новостей» — нейтральное значение. ЧП акционерам квартала, средние кредиты
  АС и по СС и процентные активы — средние по клеткам слоя «свой взгляд».
* Таблицы: для каждого значения `next_report.cor_values` (упр. базис) книга копируется с
  ещё одним наблюдением {q*, cor: v, nim: null, se 0, basis: mgmt} и пересчитывается
  (A-P2u с тем же окном, путь клеток, капитал); печатаются точка, медиана (пересчёт
  `median_draws` с привязкой к печатаемой), низ и верх и их изменение к заголовку. Так же для
  ЧПМ с {cor: null, nim: v}. Второе наблюдаемое в строке — не наблюдалось: строка отвечает на
  вопрос «что даст это число отчёта», а не «это число при втором, равном ожиданию».
* Нейтральная CoR (ЧПМ) — наблюдение, при котором медиана не меняется: отрезок — соседние
  строки со сменой знака (нет — крайняя строка и край, отодвинутый на `search_pad`),
  квадратичный шаг по трём строкам, затем секущая; невязка печатается. Невязка поиска — сдвиг
  медианы оценщиком срединных прогонов (`uncertainty.median_shift`, окно рангов — по прогонам
  книги без нового наблюдения), как у наклона: разность двух медиан на `median_draws` прогонах —
  ступенчатая функция, и корень по ней вставал бы на ступень. Стоп — по двум допускам
  сразу: |невязка| ≤ `search_tol_rub` и следующее приближение ближе `next_report.value_tol` к
  принятому; не больше 12 пересчётов медианы на корень, не сошлось — лучшее приближение.
  Строка таблицы с невязкой в рублёвом допуске сама корнем не считается, если между соседними
  строками невязка меняет знак: при пологой реакции рублёвый допуск — сотые доли п.п., и
  нейтральное значение «прилипало» бы к сетке. Без смены знака строка или край отрезка с
  |невязкой| в допуске — корень (ноль принимается с любой стороны). Нейтральная точки — тот же
  поиск по точке: между строками со сменой знака — по двум допускам, без неё — бисекция на
  отрезке таблицы, расширенном на `search_pad`.
* Эквивалент в прибыли квартала — ЧП при нейтральном значении: NI_экв,cor = E[NI] −
  (to_engine(CoR_n) − E[CoR_eng]) × E[Ē^АС + f × Ē^СС] × d/365 × (1 − τ_eff)
  (f — `credit.fv_loans_factor`: отклонение CoR действует и на кредиты по СС, М§4.6);
  NI_экв,nim = E[NI] + (to_engine(ЧПМ_n) − E[ЧПМ_eng]) × E[ĪEA] × d/365 × (1 − τ_eff).
* Наклон — местный, у нейтрального значения (не найдено — у ожидания): сдвиг медианы между
  наблюдениями − шаг/2 и + шаг/2, шаг — `valuation.sensitivities.cor_pp` / `nim_pp`, на 0,1 п.п.;
  сдвиг — оценщиком срединных прогонов (`uncertainty.median_shift`; окно рангов — по прогонам
  книги без нового наблюдения), а не разностью двух медиан; рядом — размах реакции: наименьшее
  и наибольшее изменение медианы по строкам таблицы.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Mapping, Sequence

from model.book import Book, Facts
from model.book_schema import BookError
from model.grid import GridRun, LiveInputs, live_from_book, make_context, regime_forecast, run_grid
from model.timeline import DAYS_IN_YEAR, parse_period, period_str, shift_quarter, to_date
from model import uncertainty as U
from model.reverse import bisect_point

__all__ = ["open_period", "model_expectation", "middle_expectation", "with_observation", "next_report",
           "not_computed", "benchmarks", "closing_event", "local_slope", "edge_slope"]

PP01 = 0.001                 # 0,1 п.п. — единица наклона (М§13)
OBS = "joint.regime_update.observations"

NAMED_LITERALS = {
    0.001: "PP01: 0,1 п.п. — единица наклона «₽ на 0,1 п.п.» (М§13)",
}


def open_period(book: Book, facts: Facts) -> str:
    """q* — самый ранний прогнозный квартал без внесённого наблюдения (М§13)."""
    seen = {str(o["period"]) for o in book.get(OBS) or []}
    y, q = parse_period(str(book.get("meta.first_period")))
    last = str(book.get("meta.last_period"))
    while True:
        p = period_str(y, q)
        if p not in seen:
            return p
        if p == last:
            raise BookError("все прогнозные кварталы уже закрыты наблюдениями — открытого нет")
        y, q = shift_quarter(y, q, 1)


def with_observation(book: Book, period: str, *, cor: float | None, nim: float | None,
                     basis: str = "mgmt") -> Book:
    """Книга с ещё одним наблюдением A-P2u (факт: se 0)."""
    obs = copy.deepcopy(list(book.get(OBS) or []))
    obs.append({"period": period, "cor": cor, "nim": nim, "se_cor": 0.0, "se_nim": 0.0, "basis": basis})
    return U.trial_book(book, {OBS: obs})


def _weighted(run: GridRun, q: int, name: str) -> float:
    prob = run.layers["analytical"].prob
    return sum(prob[c.key] * float(c.quarters[name][q]) for c in run.cells)


def model_expectation(book: Book, facts: Facts, period: str, *, live: LiveInputs | None = None,
                      run: GridRun | None = None) -> dict[str, Any]:
    """Ожидание модели на квартал (слой «свой взгляд»): ЧПМ и CoR — прогноз фильтра A-P2u (М§13),
    ЧП акционерам, средние кредиты АС и процентные активы — средние по клеткам."""
    live = live if live is not None else live_from_book(book, facts)
    run = run or run_grid(book, facts, live)
    tl = run.ctx.timeline
    q = tl.index(period)
    if not 1 <= q <= tl.Q:
        raise BookError(f"ожидание модели: {period} вне прогнозных кварталов сетки")
    br = run.ctx.bridge
    post = run.ctx.posterior
    fc = regime_forecast(run.ctx, q)
    cor_e = sum(float(post[r]) * fc["cor"][r] for r in fc["cor"])
    nim_e = sum(float(post[r]) * fc["nim"][r] for r in fc["nim"])
    by_regime = []
    for r in book.get("regimes.ids"):
        ce, ne = fc["cor"][r], fc["nim"][r]
        by_regime.append({"regime": r, "posterior": float(post[r]),
                          "cor_q_mgmt": br.to_mgmt_cor(ce), "nim_q_mgmt": br.to_mgmt_nim(ne),
                          "cor_q_engine": ce, "nim_q_engine": ne})
    return {"period": period, "ni_q": _weighted(run, q, "ni_sh"),
            "nim_q_mgmt": br.to_mgmt_nim(nim_e), "cor_q_mgmt": br.to_mgmt_cor(cor_e),
            "nim_q_engine": nim_e, "cor_q_engine": cor_e,
            "loans_ac": _weighted(run, q, "loans_ac_avg"), "loans_fv": _weighted(run, q, "loans_fv_avg"),
            "iea": _weighted(run, q, "iea_avg"),
            "tau_eff": _weighted(run, q, "tau_eff"), "days": tl.d(q), "by_regime": by_regime}


def middle_expectation(book: Book, facts: Facts, live: LiveInputs, base: U.Band, lam: float,
                       window: tuple[float, float], period: str) -> dict[str, float]:
    """Ожидание квартала `period` срединных прогонов полосы `base`, упр. базис (М§13): среднее прогноза фильтра
    A-P2u по книгам прогонов, чей ранг центра лежит в окне `window`.

    Оси, которые сдвигают ЧПМ или CoR открытого квартала (чувствительность доли текущих счетов, компенсация
    льготной ипотеки, веса миров, доли σ0 и φ), меняют ожидание квартала в каждом прогоне, и наблюдение, равное
    ожиданию книги, для такого прогона — сюрприз. Нейтральное значение медианы поэтому сравнивается с этим
    средним, а не с ожиданием книги: пока ожидания ЧПМ у режимов равны, нейтральная ЧПМ совпадает с ним."""
    chosen = U.middle_draws(base.exact_centres(lam), window)
    total = {"cor": 0.0, "nim": 0.0}
    for i in chosen:
        ctx = make_context(U.draw_book(book, base.axes, base.s[i]), facts, live)
        fc = regime_forecast(ctx, ctx.timeline.index(period))
        to_mgmt = {"cor": ctx.bridge.to_mgmt_cor, "nim": ctx.bridge.to_mgmt_nim}
        for x in total:
            total[x] += to_mgmt[x](sum(float(ctx.posterior[r]) * fc[x][r] for r in fc[x]))
    return {"cor": total["cor"] / len(chosen), "nim": total["nim"] / len(chosen), "draws": len(chosen)}


def closing_event(facts: Facts, period: str, valuation_date: date) -> dict[str, Any] | None:
    """МСФО, закрывающее квартал (`calendar.json`: kind ifrs, covers = квартал)."""
    if "calendar" not in facts.files:
        return None
    for ev in facts.file("calendar").get("events") or []:
        if ev.get("kind") == "ifrs" and ev.get("covers") == period:
            d = to_date(ev["date"])
            return {"id": ev.get("id"), "date": d.isoformat(), "title": ev.get("title"),
                    "confirmed": bool(ev.get("confirmed")), "precision": ev.get("precision"),
                    "earliest": ev.get("earliest"), "latest": ev.get("latest"),
                    "published": d <= valuation_date}
    return None


def _node(facts: Facts, file: str, path: str) -> float | None:
    try:
        return facts.v(file, path)
    except Exception:  # noqa: BLE001 — нет узла — эталона нет
        return None


def _mid(facts: Facts, path: str) -> float | None:
    try:
        raw = facts.raw("guidance", path)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return (float(raw[0]) + float(raw[1])) / 2
    return None if raw is None else float(raw)


def benchmarks(book: Book, facts: Facts, period: str) -> list[dict[str, Any]]:
    """Наивные эталоны квартала: тот же квартал год назад, прошлый отчётный квартал, гайденс."""
    y, q = parse_period(period)
    last_year = period_str(*shift_quarter(y, q, -4))
    anchor = str(book.get("meta.anchor_period"))
    out = []
    for key, name, p in (("same_quarter_last_year", "тот же квартал год назад", last_year),
                         ("last_quarter", "прошлый отчётный квартал", anchor)):
        out.append({"key": key, "name": name, "period": p,
                    "cor": _node(facts, "mgmt_quarterly", f"quarters.{p}.cor"),
                    "nim": _node(facts, "mgmt_quarterly", f"quarters.{p}.nim"),
                    "ni": _node(facts, "pnl_quarterly", f"quarters.{p}.ni_shareholders"),
                    "note": "факт (ЧПМ и CoR — упр. базис, ЧП — МСФО)"})
    out.append({"key": "guidance", "name": "гайденс года", "period": None,
                "cor": _mid(facts, "items.cor_max"), "nim": _mid(facts, "items.nim"), "ni": None,
                "note": "гайденс — упр. метрики года; CoR — верхняя граница; ЧП квартала гайденс не даёт"})
    return out


def not_computed(book: Book, facts: Facts, live: LiveInputs, reason: str,
                 run: GridRun | None = None) -> dict[str, Any]:
    """Блок П§2 next_report быстрой сборки: ожидание есть, таблицы и поиски — «не считалось»."""
    period = open_period(book, facts)
    exp = model_expectation(book, facts, period, live=live, run=run)
    return {"period": period, "book_period": str(book.get("valuation.next_report.period")), "target": "median",
            "closing": closing_event(facts, period, live.valuation_date),
            "expectation": _expectation_block(exp), "cor_table": [], "nim_table": [],
            "neutral": {"cor": {"value": None, "gap_rub": None, "ni_equivalent": None, "middle_expectation": None},
                        "nim": {"value": None, "gap_rub": None, "ni_equivalent": None, "middle_expectation": None},
                        "point_cor": None, "point_nim": None, "error": reason},
            "slope": {"rub_per_01pp_cor": None, "rub_per_01pp_nim": None, "at_cor": None, "at_nim": None},
            "reaction": {k: {"d_median_min": None, "d_median_max": None} for k in ("cor", "nim")},
            "benchmarks": benchmarks(book, facts, period), "status": "not_computed", "reason": reason}


def _expectation_block(exp: Mapping[str, Any]) -> dict[str, Any]:
    return {"cor_mgmt": exp["cor_q_mgmt"], "nim_mgmt": exp["nim_q_mgmt"], "cor_engine": exp["cor_q_engine"],
            "nim_engine": exp["nim_q_engine"], "ni": exp["ni_q"], "loans_ac": exp["loans_ac"], "iea": exp["iea"],
            "tau_eff": exp["tau_eff"], "days": exp["days"],
            "by_regime": [{"regime": r["regime"], "cor_mgmt": r["cor_q_mgmt"], "nim_mgmt": r["nim_q_mgmt"],
                           "posterior": r["posterior"]} for r in exp["by_regime"]]}


class _Gap:
    """Невязка поиска при наблюдении x (с журналом): сдвиг медианы оценщиком срединных прогонов, у точки —
    сдвиг точки."""

    def __init__(self, fn):
        self.fn, self.seen = fn, {}

    def __call__(self, x: float) -> float:
        if x not in self.seen:
            self.seen[x] = self.fn(x)
        return self.seen[x]


def _next_point(a: float, fa: float, b: float, fb: float, c: float | None, fc: float | None) -> float:
    """Следующее приближение корня на [a; b]: обратная квадратичная по трём точкам, иначе секущая."""
    if c is not None and fc is not None and len({fa, fb, fc}) == 3:
        x = (a * fb * fc / ((fa - fb) * (fa - fc)) + b * fa * fc / ((fb - fa) * (fb - fc))
             + c * fa * fb / ((fc - fa) * (fc - fb)))
        if min(a, b) < x < max(a, b):
            return x
    return (a * fb - b * fa) / (fb - fa)


def _refine(g, a: float, fa: float, b: float, fb: float, c: float | None, fc: float | None, steps: int,
            tol: float, value_tol: float | None) -> tuple[float, float]:
    """Корень g на [a; b] (fa, fb разных знаков) по двум допускам (М§13): |невязка| ≤ `tol` и следующее
    приближение ближе `value_tol` к принятому (`value_tol` = None — стоп по одной невязке).

    → (приближение, его невязка); не сошлось за `steps` пересчётов — лучшее из вычисленных."""
    for x, fx in ((a, fa), (b, fb)):
        if fx == 0:
            return x, fx
    best: tuple[float, float] | None = None
    for _ in range(steps):
        x = _next_point(a, fa, b, fb, c, fc)
        fx = g(x)
        if best is None or abs(fx) < abs(best[1]):
            best = (x, fx)
        if fx == 0:
            return x, fx
        if (fx > 0) == (fa > 0):
            c, fc, a, fa = a, fa, x, fx
        else:
            c, fc, b, fb = b, fb, x, fx
        if abs(fx) <= tol and (value_tol is None or abs(_next_point(a, fa, b, fb, c, fc) - x) <= value_tol):
            return x, fx
    return best if best is not None else ((a, fa) if abs(fa) <= abs(fb) else (b, fb))


def _neutral(values: Sequence[float], gaps: Sequence[float], g: _Gap, pad: float, tol: float,
             value_tol: float | None = None) -> tuple[float | None, float | None, str | None]:
    """Нейтральное значение по таблице (М§13): смена знака соседних строк → поиск внутри отрезка по двум
    допускам; без смены знака — строка или край отрезка с невязкой в допуске."""
    known = sorted(zip(values, gaps))
    for v, d in known:
        g.seen.setdefault(v, d)
    cross = next((i for i in range(len(known) - 1) if (known[i][1] > 0) != (known[i + 1][1] > 0)), None)
    if cross is None:
        near = [(v, d) for v, d in known if abs(d) <= tol]
        if near:                                 # без смены знака строка с невязкой в допуске — корень
            v, d = min(near, key=lambda t: abs(t[1]))
            return v, d, None
        # нет смены знака: крайняя строка со стороны корня, расширенная на search_pad
        rising = known[-1][1] > known[0][1]
        below = (known[0][1] > 0) == rising      # корень левее таблицы
        a, fa = known[0] if below else known[-1]
        end = a - pad if below else a + pad
        fe = g(end)
        if abs(fe) <= tol:                       # край с невязкой в допуске — корень (ноль — с любой стороны)
            return end, fe, None
        if (fe > 0) == (fa > 0):
            return None, None, (f"нейтральное значение вне отрезка поиска (таблица и ±{pad:g}): "
                                f"сдвиг медианы на краю {fe:+.2f} ₽")
        lo, flo, hi, fhi = (end, fe, a, fa) if below else (a, fa, end, fe)
        root, gap = _refine(g, lo, flo, hi, fhi, None, None, U_MAX, tol, value_tol)
    else:                                        # строка сама корнем не считается — поиск внутри отрезка
        (a, fa), (b, fb) = known[cross], known[cross + 1]
        c, fc = (known[cross + 2] if cross + 2 < len(known) else known[cross - 1] if cross else (None, None))
        root, gap = _refine(g, a, fa, b, fb, c, fc, U_MAX, tol, value_tol)
    return root, gap, None


U_MAX = 12                   # не больше 12 пересчётов медианы на корень (М§13)


POINT_STEPS = 40             # не больше 40 пересчётов точки на корень (сетка без полосы)


def _point_neutral(values: Sequence[float], dpoints: Sequence[float], pg, pad: float, tol: float,
                   value_tol: float | None = None) -> float | None:
    """Нейтральное значение точки (М§13). Между соседними строками со сменой знака — поиск по двум допускам:
    строка с невязкой в рублёвом допуске корнем сама не считается (иначе значение «прилипает» к сетке таблицы);
    без смены знака — бисекция на отрезке таблицы, расширенном на `pad`, край с невязкой в допуске — корень."""
    known = sorted(zip(values, dpoints))
    cross = next((i for i in range(len(known) - 1) if (known[i][1] > 0) != (known[i + 1][1] > 0)), None)
    if cross is not None:
        (a, fa), (b, fb) = known[cross], known[cross + 1]
        c, fc = (known[cross + 2] if cross + 2 < len(known) else known[cross - 1] if cross else (None, None))
        root, _ = _refine(_Gap(pg), a, fa, b, fb, c, fc, POINT_STEPS, tol, value_tol)
        return root
    root, _ = bisect_point(pg, known[0][0] - pad, known[-1][0] + pad, tol=tol)
    return root


def next_report(book: Book, facts: Facts, live: LiveInputs, band: U.Band, *,
                anchor: U.MedianAnchor | None = None, run: GridRun | None = None) -> dict[str, Any]:
    """П§2 next_report: таблицы CoR и ЧПМ, нейтральные значения, эквивалент прибыли, наклон."""
    lam = band.lam
    run = run or run_grid(book, facts, live)
    anchor = anchor or U.MedianAnchor(book, facts, live, band)
    tol = float(book.get("valuation.headline.search_tol_rub"))
    pad = float(book.get("valuation.next_report.search_pad"))
    value_tol = float(book.get("valuation.next_report.value_tol"))
    window = U.rank_window(book)
    period = open_period(book, facts)
    exp = model_expectation(book, facts, period, live=live, run=run)
    base_median = band.medians(lam)
    base_point = run.point
    br = run.ctx.bridge

    def book_at(kind: str, v: float) -> Book:      # второе наблюдаемое строки — не наблюдалось (М§13)
        if kind == "cor":
            return with_observation(book, period, cor=v, nim=None)
        return with_observation(book, period, cor=None, nim=v)

    bands: dict[tuple[str, float], U.Band] = {}       # пересчёты медианы по наблюдениям — каждый один раз

    def band_at(kind: str, v: float) -> U.Band:
        if (kind, v) not in bands:
            bands[(kind, v)] = anchor.recompute(book_at(kind, v))
        return bands[(kind, v)]

    base_centres = anchor.base.exact_centres(lam)   # прогоны книги без нового наблюдения — по ним окно рангов

    def shift(kind: str, v: float) -> float:        # сдвиг медианы оценщиком срединных прогонов (М§10)
        return U.median_shift(base_centres, band_at(kind, v).exact_centres(lam), window)

    tables: dict[str, list[dict[str, Any]]] = {}
    gaps: dict[str, _Gap] = {}
    for kind in ("cor", "nim"):
        values = [float(v) for v in book.get(f"valuation.next_report.{kind}_values")]
        g = _Gap(lambda v, kind=kind: shift(kind, v))  # невязка поиска нейтрального значения (М§13)
        rows = []
        for v in values:
            b2 = book_at(kind, v)
            r2 = run_grid(b2, facts, live)
            m = anchor.anchored(band_at(kind, v), lam)
            rows.append({kind: v, "point": r2.point, "median": m["central"], "median_low": m["low"],
                         "median_high": m["high"], "d_point": r2.point - base_point,
                         "d_median": m["central"] - base_median["central"],
                         "posterior": {r: float(p) for r, p in r2.ctx.posterior.items()}})
        tables[kind], gaps[kind] = rows, g
    neutral: dict[str, Any] = {}
    errors = []
    middle = middle_expectation(book, facts, live, anchor.base, lam, window, period)
    tau = exp["tau_eff"]
    days = exp["days"]
    cor_base = exp["loans_ac"] + float(book.get("credit.fv_loans_factor")) * exp["loans_fv"]
    for kind in ("cor", "nim"):
        rows = tables[kind]
        values = [r[kind] for r in rows]
        root, gap, err = _neutral(values, [gaps[kind](v) for v in values], gaps[kind], pad, tol, value_tol)
        if err:
            errors.append(f"{kind}: {err}")
        if root is None:
            neq = None
        elif kind == "cor":
            neq = exp["ni_q"] - (br.to_engine_cor(root) - exp["cor_q_engine"]) * cor_base \
                * days / DAYS_IN_YEAR * (1 - tau)
        else:
            neq = exp["ni_q"] + (br.to_engine_nim(root) - exp["nim_q_engine"]) * exp["iea"] \
                * days / DAYS_IN_YEAR * (1 - tau)
        neutral[kind] = {"value": root, "gap_rub": gap, "ni_equivalent": neq, "middle_expectation": middle[kind]}
        pg = lambda v, kind=kind: run_grid(book_at(kind, v), facts, live).point - base_point  # noqa: E731
        neutral[f"point_{kind}"] = _point_neutral(values, [r["d_point"] for r in rows], pg, pad, tol, value_tol)
    if errors:
        neutral["error"] = "; ".join(errors)

    slope: dict[str, Any] = {}
    for kind in ("cor", "nim"):                    # местный наклон у нейтрального значения (М§13)
        at, where = neutral[kind]["value"], "neutral"
        if at is None:
            at, where = exp[f"{kind}_q_mgmt"], "expectation"
        step = float(book.get(f"valuation.sensitivities.{kind}_pp"))
        slope[f"rub_per_01pp_{kind}"] = local_slope(lambda v, kind=kind: shift(kind, v), at, step)
        slope[f"at_{kind}"] = where
    reaction = {kind: {"d_median_min": min(r["d_median"] for r in tables[kind]),
                       "d_median_max": max(r["d_median"] for r in tables[kind])} for kind in ("cor", "nim")}
    return {"period": period, "book_period": str(book.get("valuation.next_report.period")), "target": "median",
            "closing": closing_event(facts, period, live.valuation_date),
            "expectation": _expectation_block(exp), "cor_table": tables["cor"], "nim_table": tables["nim"],
            "neutral": neutral, "slope": slope, "reaction": reaction,
            "benchmarks": benchmarks(book, facts, period), "status": "computed",
            "evaluations": anchor.evaluations}


def local_slope(g, at: float, step: float) -> float | None:
    """Местный наклон: (g(at + шаг/2) − g(at − шаг/2)) на 0,1 п.п. (М§13); шаг 0 — None. `g` — сдвиг медианы
    при наблюдении (в выпуске — оценщик срединных прогонов на общих точках гиперкуба)."""
    if not step:
        return None
    return (g(at + step / 2) - g(at - step / 2)) / (step / PP01)


def edge_slope(rows: Sequence[Mapping[str, Any]], kind: str) -> float | None:
    """Наклон по крайним строкам таблицы на 0,1 п.п. — нижняя оценка малых отклонений (сравнение, М§13)."""
    if len(rows) < 2 or rows[-1][kind] == rows[0][kind]:
        return None
    return (rows[-1]["d_median"] - rows[0]["d_median"]) / ((rows[-1][kind] - rows[0][kind]) / PP01)
