"""Обратный расчёт медианы: что в цене (М§11) и банковские строки.

Для каждой оси `valuation.reverse_dcf.axes` — значение ОДНОГО суждения, при котором
печатаемая медиана равна цене главного тикера; остальное разыгрывается как в полосе.

* Вид оси: `value` — значение ключей; `shift` — сдвиг траекторий (кроме `LT_from`); `mix` —
  каждая строка `joint.reg_prob_given_regime` → (1 − x) × книга + x × `toward`.
* Суждение подменяется в книге ДО полосы: у оси, которая есть в полосе, проверяемое значение —
  центр розыгрыша, а граница, которую он пересёк, следует за ним (`reverse_bounds: follow_center`);
  ось, которой в полосе нет, стоит на проверяемом значении. Строки осей полосы и осей вне её
  решаются поэтому по-разному, и решения разных строк не перемножаются (оговорка — в `method`).
* Поиск (перенос 850oa): точечное решение — бисекция на `search` (≤ 40 шагов, стоп
  `search_tol_rub`); медиана — на подвыборке `subsample` с привязкой к печатаемой
  (`MedianAnchor`): старт — точечное решение, отодвинутое в 9/4 раза, секущая наружу до
  смены знака, обратная квадратичная интерполяция внутри; нет смены знака — «недостижимо».
  Решение внутри диапазона книги уточняется на полной полосе (секущая, не больше
  min(`median_refine`, 2) полос; невязка в стопе — уточнение кончено, М§11.2);
  решение — точка с наименьшей невязкой, невязка печатается.
* «В диапазоне книги» (`in_range`) — только о корне: решение с невязкой в стопе лежит в диапазоне;
  у несошедшегося уточнения — и лучшее приближение, и оценка корня секущей лежат в диапазоне.
  Приближение внутри диапазона при корне за его краем печатается «вне диапазона».
* Банковские строки: вменённый ROE сквозь цикл (строка ЧПМ), вменённая стоимость капитала
  (строка ERP; k_T по мирам и λ-смесь), рынок: Cap − BV против V − BV точки.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Callable, Mapping, Sequence

from model.book import Book, Facts
from model.book_schema import BookError
from model.checks import grouped, num
from model.grid import GridRun, LiveInputs, bank_language, run_grid, sensitivity_overrides
from model.paths import shift_trajectory
from model import uncertainty as U

__all__ = ["reverse_dcf", "reverse_overrides", "reverse_trial", "solve_outward", "bracketed",
           "bisect_point", "root_in_range", "SEED_FACTOR", "MAX_STEPS", "BISECT_STEPS", "MAX_REFINE",
           "not_computed"]

SEED_FACTOR = 9 / 4          # старт поиска медианы дальше от книги, чем точечное решение (850oa)
MAX_STEPS = 12               # предел пересчётов медианы на один корень
BISECT_STEPS = 40            # бисекция точки (М§11.2)
MAX_REFINE = 2               # уточнение на полной полосе — не больше двух шагов (М§11.2)
ERP_PATH = "valuation.erp"

NAMED_LITERALS = {
    2.25: "SEED_FACTOR: старт поиска медианы — точечное решение × 9/4 (перенос 850oa)",
}


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


# ------------------------------------------------------------------ подмены


def reverse_overrides(book: Book, ax: Mapping[str, Any], v: float) -> dict[str, Any]:
    """Подмены книги для значения v оси обратного расчёта."""
    kind = ax.get("kind", "value")
    out: dict[str, Any] = {}
    for path in ax["paths"]:
        if kind == "value":
            out[path] = float(v)
        elif kind == "shift":
            out[path] = shift_trajectory(book.get(path), float(v))
        elif kind == "mix":
            table = book.get(path)
            toward = ax.get("toward") or {}
            out[path] = {r: {s: (1 - v) * float(row[s]) + v * float(toward[s]) for s in row}
                         for r, row in table.items()}
        else:
            raise BookError(f"ось обратного расчёта {ax.get('name')!r}: незнакомый kind {kind!r}")
    return out


def _book_value(book: Book, ax: Mapping[str, Any]) -> float:
    return float(book.get(ax["paths"][0])) if ax.get("kind", "value") == "value" else 0.0


def _band_axis(book: Book, path: str) -> int | None:
    for j, ax in enumerate(book.get("valuation.uncertainty.axes") or []):
        if ax.get("kind") == "value" and path in ax.get("paths", []):
            return j
    return None


def reverse_trial(book: Book, ax: Mapping[str, Any], v: float, *, follow: bool) -> Book:
    """Книга с суждением оси на значении v; граница оси полосы следует за центром."""
    ov = reverse_overrides(book, ax, v)
    if follow and ax.get("kind", "value") == "value":
        axes = copy.deepcopy(list(book.get("valuation.uncertainty.axes")))
        moved = False
        for path in ax["paths"]:
            j = _band_axis(book, path)
            if j is not None:
                axes[j]["low"], axes[j]["high"] = min(float(axes[j]["low"]), v), max(float(axes[j]["high"]), v)
                moved = True
        if moved:
            ov["valuation.uncertainty.axes"] = axes
    return U.trial_book(book, ov)


# ------------------------------------------------------------------ поиск корня


def bracketed(g: Callable[[float], float], a: float, fa: float, b: float, fb: float, c: float | None,
              fc: float | None, steps: int, tol: float) -> tuple[float, int, bool]:
    """Корень g на [a; b] (fa, fb разных знаков): обратная квадратичная по трём точкам, иначе секущая.

    → (корень, пересчётов, найден ли в допуске); не сошлось — оценка секущей (не проверена)."""
    used = 0
    for _ in range(steps):
        x = None
        if c is not None and fc is not None and len({fa, fb, fc}) == 3:
            x = (a * fb * fc / ((fa - fb) * (fa - fc)) + b * fa * fc / ((fb - fa) * (fb - fc))
                 + c * fa * fb / ((fc - fa) * (fc - fb)))
            if not min(a, b) < x < max(a, b):
                x = None
        if x is None:
            x = (a * fb - b * fa) / (fb - fa)
        fx = g(x)
        used += 1
        if abs(fx) <= tol:
            return x, used, True
        if (fx > 0) == (fa > 0):
            c, fc, a, fa = a, fa, x, fx
        else:
            c, fc, b, fb = b, fb, x, fx
    return (a * fb - b * fa) / (fb - fa), used, False


def solve_outward(g: Callable[[float], float], b: float, gb: float, x1: float, end: float, *, tol: float,
                  steps: int = MAX_STEPS) -> tuple[float | None, int]:
    """Корень g между b и `end`: старт x1, секущая наружу до смены знака, затем `bracketed`.

    g не меняет знак до `end` — None («недостижимо в поиске»)."""
    if abs(gb) <= tol:
        return b, 0
    prev, xa, fa, x, used = None, b, gb, x1, 0
    direction = _sign(end - b)
    while used < steps:
        fx = g(x)
        used += 1
        if abs(fx) <= tol:
            return x, used
        if (fx > 0) != (fa > 0):
            c, fc = prev if prev else (None, None)
            root, more, _ = bracketed(g, xa, fa, x, fx, c, fc, steps - used, tol)
            return root, used + more
        if x == end:
            return None, used
        nxt = x - fx * (x - xa) / (fx - fa) if fx != fa else math.inf
        if not math.isfinite(nxt) or (nxt - x) * direction <= 0:
            nxt = x + (x - xa)
        nxt = min(nxt, end) if direction > 0 else max(nxt, end)
        prev, xa, fa, x = (xa, fa), x, fx, nxt
    return None, used


def bisect_point(g: Callable[[float], float], lo: float, hi: float, *, tol: float,
                 steps: int = BISECT_STEPS) -> tuple[float | None, float | None]:
    """Бисекция на [lo; hi] (≤ steps шагов, стоп |g| ≤ tol) → (корень, невязка); нет смены знака — (None, None)."""
    flo, fhi = g(lo), g(hi)
    if abs(flo) <= tol:
        return lo, flo
    if abs(fhi) <= tol:
        return hi, fhi
    if flo * fhi > 0:
        return None, None
    best = (lo, flo) if abs(flo) < abs(fhi) else (hi, fhi)
    for _ in range(steps):
        mid = (lo + hi) / 2
        fm = g(mid)
        if abs(fm) < abs(best[1]):
            best = (mid, fm)
        if abs(fm) <= tol:
            return mid, fm
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return best


class _Recorder:
    """Функция невязки с журналом вычисленных точек (x → невязка)."""

    def __init__(self, fn: Callable[[float], float]):
        self.fn, self.seen = fn, {}

    def __call__(self, x: float) -> float:
        if x not in self.seen:
            self.seen[x] = self.fn(x)
        return self.seen[x]

    def best(self) -> tuple[float, float] | None:
        return min(self.seen.items(), key=lambda kv: abs(kv[1])) if self.seen else None


# ------------------------------------------------------------------ строки


def not_computed(book: Book, reason: str) -> dict[str, Any]:
    """Блок П§2 reverse_dcf быстрой сборки: строки «не считалось» с причиной."""
    rows = []
    for ax in book.get("valuation.reverse_dcf.axes") or []:
        rows.append({"key": U.axis_key(ax), "name": ax["name"], "kind": ax.get("kind", "value"),
                     "unit": ax.get("unit", ""), "paths": list(ax["paths"]), "book": _book_value(book, ax),
                     "range": [float(x) for x in ax["range"]], "solved": None, "delta": None, "in_range": False,
                     "status": "not_computed", "gap": None, "point_solved": None, "point_status": "not_computed",
                     "reason": reason})
    bank = [{"key": k, "title": BANK_TITLES.get(k, k), "unit": BANK_UNITS.get(k, ""), "book": None,
             "implied": None, "delta": None, "status": "not_computed", "reason": reason}
            for k in book.get("valuation.reverse_dcf.bank_rows") or []]
    return {"target": None, "number": "median", "method": reason, "rows": rows, "bank_rows": bank}


BANK_TITLES = {
    "implied_roe_through_cycle": "Вменённый ROE сквозь цикл",
    "implied_cost_of_equity": "Вменённая стоимость капитала",
    "market_cap_minus_bv": "Рынок: капитализация − капитал",
}
BANK_UNITS = {"implied_roe_through_cycle": "доля", "implied_cost_of_equity": "доля",
              "market_cap_minus_bv": "млрд ₽"}


def _full_gap(trial: Book, facts: Facts, live: LiveInputs, band: U.Band, anchor: U.MedianAnchor, lam: float,
              px: float) -> float:
    """Невязка медианы полной полосы книги `trial` к цене (одна полоса `band.draws` прогонов)."""
    full = U.band(trial, facts, live, draws=band.draws)
    anchor.full_grids += full.draws
    return full.medians(lam)["central"] - px


def _refine_full(g: Callable[[float], float], b: float, gb: float, x: float, lo: float, hi: float, steps: int,
                 tol: float) -> tuple[float, float, float]:
    """Уточнение корня на полной полосе (М§11.2): секущая от точки книги (b, gb) через решение подвыборки x,
    не больше `steps` пересчётов `g`, шаги не выходят из [lo; hi]; невязка в стопе `tol` — уточнение кончено.

    → (решение — вычисленная точка с наименьшей невязкой, её невязка, оценка корня секущей по двум последним
    точкам). Оценка нужна, когда уточнение не сошлось: по ней видно, по какую сторону края диапазона корень."""
    pts = [(b, gb)]
    estimate = x
    for _ in range(steps):
        gx = g(x)
        pts.append((x, gx))
        (xb, fb), (xa, fa) = pts[-2], pts[-1]
        estimate = min(max(xa - fa * (xa - xb) / (fa - fb) if fa != fb else xa, lo), hi)
        if abs(gx) <= tol:
            break
        x = estimate
    value, gap = min(pts[1:], key=lambda t: abs(t[1]))
    return value, gap, (value if abs(gap) <= tol else estimate)


def root_in_range(value: float | None, gap: float | None, estimate: float | None, low: float, high: float,
                  tol: float) -> bool:
    """«В диапазоне книги» — о корне, а не о приближении (М§11.2): решение с невязкой в стопе лежит в
    [low; high]; у несошедшегося уточнения в диапазоне лежат и приближение, и оценка корня секущей."""
    if value is None or not low <= value <= high:
        return False
    if gap is None or abs(gap) <= tol or estimate is None:
        return True
    return low <= estimate <= high


def reverse_dcf(book: Book, facts: Facts, live: LiveInputs, band: U.Band, *,
                anchor: U.MedianAnchor | None = None, run: GridRun | None = None) -> dict[str, Any]:
    """П§2 reverse_dcf: строки по осям книги и банковские строки (М§11)."""
    main = str(book.get("meta.company.main_ticker"))
    px = float(live.prices[main])
    lam = band.lam
    tol = float(book.get("valuation.headline.search_tol_rub"))
    follow = book.get("valuation.uncertainty.reverse_bounds") == "follow_center"
    refine = min(int(book.get("valuation.uncertainty.median_refine")), MAX_REFINE)
    sub = int(book.get("valuation.reverse_dcf.subsample"))
    run = run or run_grid(book, facts, live)
    if anchor is None or anchor.n != sub:
        anchor = U.MedianAnchor(book, facts, live, band, n=sub)
    median_full = band.medians(lam)["central"]
    median_gap = median_full - px
    point_gap = run.point - px
    rows, solutions = [], {}
    for ax in book.get("valuation.reverse_dcf.axes") or []:
        kind = ax.get("kind", "value")
        b = _book_value(book, ax)
        lo, hi = (float(x) for x in ax["search"])
        rlo, rhi = (float(x) for x in ax["range"])

        def trial(v: float, ax=ax) -> Book:
            return reverse_trial(book, ax, v, follow=follow)

        pg = _Recorder(lambda v, trial=trial: run_grid(trial(v), facts, live).point - px)
        p, p_gap = bisect_point(pg, lo, hi, tol=tol)
        mg = _Recorder(lambda v, trial=trial: anchor.at(trial(v), lam=lam)["central"] - px)
        mg.seen[b] = median_gap
        if p is not None:
            sense = _sign(p - b) * _sign(-point_gap)
        else:
            sense = _sign(pg(hi) - pg(lo))
        direction = sense * _sign(-median_gap)
        value, used = None, 0
        if abs(median_gap) <= tol:
            value = b
        elif direction:
            end = hi if direction > 0 else lo
            x1 = end if p is None else b + direction * SEED_FACTOR * abs(p - b)
            x1 = min(x1, hi) if direction > 0 else max(x1, lo)
            value, used = solve_outward(mg, b, median_gap, x1, end, tol=tol)
        gap, basis, estimate = None, "subsample", None
        if value is not None:
            gap = mg(value)
            if refine > 0 and value != b and rlo <= value <= rhi:
                value, gap, estimate = _refine_full(
                    lambda x, trial=trial: _full_gap(trial(x), facts, live, band, anchor, lam, px),
                    b, median_gap, value, lo, hi, refine, tol)
                basis = "full"
        else:
            near = mg.best()
            gap = None if near is None else near[1]
        status = "solved" if value is not None else "unreachable"
        solutions[U.axis_key(ax)] = (ax, value)
        rows.append({
            "key": U.axis_key(ax), "name": ax["name"], "kind": kind, "unit": ax.get("unit", ""),
            "paths": list(ax["paths"]), "book": b, "range": [rlo, rhi], "search": [lo, hi],
            "solved": value, "delta": None if value is None else value - b,
            "in_range": root_in_range(value, gap, estimate, rlo, rhi, tol), "status": status, "gap": gap,
            "gap_basis": basis, "converged": gap is not None and abs(gap) <= tol, "point_solved": p,
            "point_gap": p_gap, "point_status": "solved" if p is not None else "unreachable", "evaluations": used,
        })
    bank = bank_rows(book, facts, live, run, solutions, follow=follow)
    method = (f"медиана — подвыборка {grouped(sub)} прогонов с общими случайными числами и поправкой к полной "
              f"медиане; поиск — секущая наружу и обратная квадратичная, стоп {num(tol, 2)} ₽; решение внутри "
              f"диапазона книги уточняется на полной полосе ({grouped(band.draws)} прогонов, шагов — не больше "
              f"{refine}, до невязки в стопе), пометка «в диапазоне книги» относится к корню, а не к приближению; "
              f"точка — бисекция ≤ {BISECT_STEPS} шагов. Ось полосы разыгрывается вокруг проверяемого значения, "
              "ось вне полосы стоит на нём: строки решаются по-разному, и решения разных строк не перемножаются")
    return {"target": px, "number": "median", "method": method, "rows": rows, "bank_rows": bank,
            "subsample": sub, "evaluations": anchor.evaluations, "full_grids": anchor.full_grids}


def _row_for(book: Book, solutions: Mapping[str, tuple], paths: Sequence[str]):
    for key, (ax, value) in solutions.items():
        if any(p in ax["paths"] for p in paths):
            return ax, value
    return None, None


def bank_rows(book: Book, facts: Facts, live: LiveInputs, run: GridRun,
              solutions: Mapping[str, tuple], *, follow: bool) -> list[dict[str, Any]]:
    """Банковские строки М§11.3 по списку `valuation.reverse_dcf.bank_rows`."""
    lam = run.lam
    base = bank_language(run, lam)
    out = []
    worlds = list(book.get("worlds.ids"))

    def k_by_world(r: GridRun) -> dict[str, float]:
        return {w: sum(c.k_t for c in r.cells if c.world == w) / sum(1 for c in r.cells if c.world == w)
                for w in worlds}

    for key in book.get("valuation.reverse_dcf.bank_rows") or []:
        row = {"key": key, "title": BANK_TITLES.get(key, key), "unit": BANK_UNITS.get(key, "")}
        if key == "implied_roe_through_cycle":
            ax, value = _row_for(book, solutions, list(sensitivity_overrides(book, "nim")))
            implied = None
            if ax is not None and value is not None:
                implied = bank_language(run_grid(reverse_trial(book, ax, value, follow=follow), facts, live),
                                        lam)["roe_tc"]
            row.update(book=base["roe_tc"], implied=implied,
                       delta=None if implied is None else implied - base["roe_tc"],
                       status="solved" if implied is not None else "unreachable",
                       row=None if ax is None else U.axis_key(ax))
        elif key == "implied_cost_of_equity":
            ax, value = _row_for(book, solutions, [ERP_PATH])
            implied, by_world = None, None
            if ax is not None and value is not None:
                r2 = run_grid(reverse_trial(book, ax, value, follow=follow), facts, live)
                implied, by_world = bank_language(r2, lam)["k_tc"], k_by_world(r2)
            row.update(book=base["k_tc"], implied=implied,
                       delta=None if implied is None else implied - base["k_tc"],
                       status="solved" if implied is not None else "unreachable",
                       by_world=by_world, book_by_world=k_by_world(run),
                       row=None if ax is None else U.axis_key(ax))
        elif key == "market_cap_minus_bv":
            cap = base["market_cap"]
            implied = None if cap is None else cap - base["bv_v"]
            row.update(book=base["excess_point"], implied=implied,
                       delta=None if implied is None else implied - base["excess_point"],
                       status="computed" if implied is not None else "unreachable")
        else:
            raise BookError(f"valuation.reverse_dcf.bank_rows: незнакомая строка {key!r}")
        out.append(row)
    return out
