"""Полоса неопределённости печатаемого заголовка (A-V9, М§10) — перенос механики 850oa.

* Оси — суждения книги `valuation.uncertainty.axes` (`value`, `shift`, `dict`); положение
  оси s ∈ [−1; 1]: s = 0 — значение книги, ±1 — концы диапазона, между ними линейно по
  половинам; s треугольное с модой 0 (`tri_s`), поэтому медиана суждения — книга.
* Выборка — латинский гиперкуб; ПОРЯДОК СЛУЧАЙНЫХ ЧИСЕЛ — часть определения:
  `random.Random(seed)`; для каждой оси по порядку книги `perm = list(range(n));
  rng.shuffle(perm)`; затем для i = 0…n−1 и каждой оси j `u = (perm_j[i] + rng.random()) / n`.
* Прогон — вся сетка 36 клеток на изменённой книге (`model.grid.run_grid`), три слоя и
  то же отображение V0 → цена, что у печатаемого числа. Низ и верх прогона округляются
  до 0,1 ₽ половиной вверх; центр(λ) = низ + λ (верх − низ); статистики заголовка — по
  округлённым прогонам (так же их пересчитывает витрина).
* Вклад оси — доля квадрата ранговой корреляции (Спирмен; ранги при равенстве — средние)
  положения оси с центром при λ книги; у осей-словарей скаляра нет — вклад не печатается.
* Все оси книги — в полосе (М§10): отбор «вклад < 1 %» снят — доля в дисперсии меряет разброс, а
  не положение. Асимметричная ось сдвигает среднее центра на сдвиг положения
  (цена_high + цена_low − 2 × точка) / 6 (`mean_shift`). Механизм
  `valuation.uncertainty.off_band_axes` сохранён (ось не разыгрывается, цена ошибки суждения
  печатается, `in_band = false`, вклад `null`); у книги он пуст. Σ сдвигов осей вне полосы —
  диагностика `off_band_shift` и гейт (|Σ| > print_step / 2, М§14.2).
* Источник суждения — комментарий машинной книги к первому пути оси (`book_notes`), без меток,
  не длиннее 80 знаков по границе фразы; фразы со служебным текстом (имена полей, пути к листам и
  файлам, рабочие пометки — П§0.2) опускаются; нет комментария — «book-<версия>, ось „<имя>“».
* Единица оси — поле `unit` оси книги (код П§0.2); без него — по путям оси (сдвиг и ключи с `_pp` —
  п.п., сроки — годы, коэффициенты — число, суммы — млрд ₽), не по величине значения и не по словам имени.
* Оценщик срединных прогонов (`median_shift`) — для малых сдвигов медианы (чувствительности, местный
  наклон): среднее попарных разностей прогонов на одних точках гиперкуба по прогонам, чей ранг в
  базовой выборке лежит в окне `valuation.sensitivities.rank_window`.
* DDM = RI — в каждой клетке каждого прогона: максимум относительной разности по прогонам —
  `Band.ddm_ri_max` (сводка, М§6.4).
* Печать — ROUND_HALF_UP к `valuation.headline.print_step` в десятичной арифметике
  (`round_half_up` — одна функция на всё ядро).
* Пересчёты медианы для диагностик — `MedianAnchor`: `median_draws` прогонов той же
  выборки (общие случайные числа) плюс сдвиг к печатаемой медиане.

Пул процессов (`BANK_WORKERS`: `auto` — ядра − 1, не больше 7; `1` — без пула) считает
прогоны кусками по порядку точек; куски склеиваются в том же порядке — числа те же бит в
бит. Рабочий — чистый интерпретатор (`spawn`) с кодом с диска.
"""

from __future__ import annotations

import atexit
import copy
import math
import os
import pickle
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from multiprocessing import get_context, parent_process
from typing import Any, Iterable, Mapping, Sequence

from model.book import Book, Facts
from model.book_schema import UNIT_CODES, UNIT_WORDS, BookError
from model.grid import GridRun, LiveInputs, run_grid
from model.paths import shift_trajectory

__all__ = ["FAST_DRAWS", "round_half_up", "Band", "band", "judgements", "off_band_axes", "MedianAnchor", "workers",
           "axis_unit", "mean_shift", "median_shift", "middle_draws", "draw_book", "rank_window", "off_band_shift",
           "book_notes",
           "judgement_source", "source_text", "service_text",
           "axis_key", "axis_overrides", "trial_book", "draw_points", "tri_s", "quantile", "ranks",
           "pearson", "axis_contributions", "run_summary", "close_pool", "evaluate_many"]

FAST_DRAWS = 48                  # прогонов быстрой сборки (техническая константа, не книга)
DRAW_STEP = 0.1                  # прогоны полосы хранятся с точностью 0,1 ₽ (М§10)
WORKERS_ENV = "BANK_WORKERS"
MAX_WORKERS = 7                  # потолок `auto`: пул не берёт все ядра и память
PARALLEL_MIN_DRAWS = 32          # меньше прогонов — последовательно (запуск пула дороже)
CHUNKS_PER_WORKER = 4            # мелкие куски выравнивают нагрузку неравных ядер
WINDOWS_POOL_LIMIT = 61          # предел процессов пула на Windows
GOVERNANCE = "valuation.governance"
QUANTILE_NAMES = ("p10", "p25", "median", "p75", "p90")   # поля выпуска по уровням книги (П§2 headline)
# Поля сводки прогона для хвоста полосы (`run_summary`, `Band.tail`, М§10).
TAIL_FIELDS = ("gap_mass", "need_capital_mass", "negative_mass", "low_floor", "high_floor")

NAMED_LITERALS = {
    0.1: "DRAW_STEP: прогоны полосы с точностью 0,1 ₽ (М§10, П§0.3)",
}


# ------------------------------------------------------------------ печать


def round_half_up(x: float, step: float) -> float:
    """ROUND_HALF_UP(x / step) × step в десятичной арифметике (М§10; фронт — `roundHalfUp`)."""
    q = (Decimal(repr(float(x))) / Decimal(repr(float(step)))).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return float(q * Decimal(repr(float(step))))


# ------------------------------------------------------------------ статистика


def tri_s(u: float) -> float:
    """Квантиль треугольного распределения на [−1; 1] с модой 0: √(2u) − 1 при u < ½, иначе 1 − √(2(1 − u))."""
    return math.sqrt(2.0 * u) - 1.0 if 2.0 * u < 1.0 else 1.0 - math.sqrt(2.0 * (1.0 - u))


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Квантиль тип 7 (линейная интерполяция): h = (n − 1)·q."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    h = (n - 1) * q
    lo = int(math.floor(h))
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def ranks(values: Sequence[float]) -> list[float]:
    """Ранги 1..n; при равенстве — средние ранги группы (М§10: центры округлены до 0,1 ₽)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    pos = 0
    while pos < len(order):
        end = pos
        while end + 1 < len(order) and values[order[end + 1]] == values[order[pos]]:
            end += 1
        mean_rank = (pos + end) / 2 + 1
        for k in range(pos, end + 1):
            out[order[k]] = mean_rank
        pos = end + 1
    return out


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def axis_contributions(keys: Sequence[str], points: Sequence[Sequence[float]], central: Sequence[float],
                       skip: Iterable[int] = ()) -> list[dict[str, Any]]:
    """Ранговая корреляция положения оси с центром и доля её квадрата в сумме (по убыванию доли).

    Оси с номерами `skip` (словари) не участвуют: скаляра у них нет (М§10)."""
    skip = set(skip)
    rc = ranks(central)
    rho = {j: pearson(ranks([s[j] for s in points]), rc) for j in range(len(keys)) if j not in skip}
    tot = sum(r * r for r in rho.values()) or 1.0
    rows = [{"j": j, "axis": keys[j], "rank_corr": r, "share": r * r / tot} for j, r in rho.items()]
    return sorted(rows, key=lambda x: (-x["share"], x["j"]))


# ------------------------------------------------------------------ оси


def _differs(paths: Sequence[str]) -> str:
    parts = [p.split(".") for p in paths]
    if len({len(p) for p in parts}) != 1:
        return paths[0] + "+"
    return ".".join(col[0] if len(set(col)) == 1 else "*" for col in zip(*parts))


def axis_key(axis: Mapping[str, Any]) -> str:
    """Ключ оси: путь книги; у нескольких путей — общий шаблон со `*` на различающихся местах."""
    paths = [str(p) for p in axis.get("paths") or []]
    if not paths:
        raise BookError(f"ось {axis.get('name')!r}: нет путей")
    return paths[0] if len(paths) == 1 else _differs(paths)


def axis_keys(axes: Sequence[Mapping[str, Any]]) -> list[str]:
    """Ключи осей по порядку; совпавшие — с суффиксом «#номер»."""
    out, seen = [], {}
    for ax in axes:
        k = axis_key(ax)
        seen[k] = seen.get(k, 0) + 1
        out.append(k if seen[k] == 1 else f"{k}#{seen[k]}")
    return out


def _check_axis(ax: Mapping[str, Any]) -> None:
    if ax.get("kind") not in ("value", "shift", "dict"):
        raise BookError(f"ось {ax.get('name')!r}: незнакомый kind {ax.get('kind')!r} (М§10)")
    if ax.get("dist") != "triangular":
        raise BookError(f"ось {ax.get('name')!r}: незнакомое dist {ax.get('dist')!r} (М§10)")


def axis_overrides(book: Book, ax: Mapping[str, Any], s: float) -> dict[str, Any]:
    """Подмены книги для положения оси s ∈ [−1; 1] (М§10)."""
    _check_axis(ax)
    out: dict[str, Any] = {}
    w = abs(s)
    for path in ax["paths"]:
        if ax["kind"] == "shift":
            end = ax["high"] if s >= 0 else ax["low"]
            out[path] = shift_trajectory(book.get(path), w * float(end))
        elif ax["kind"] == "dict":
            base = book.get(path)
            end = ax["high"] if s >= 0 else ax["low"]
            if not isinstance(base, Mapping) or set(base) != set(end):
                raise BookError(f"ось {ax['name']!r}: концы словаря не совпадают с {path}")
            mix = {k: float(base[k]) + min(w, 1.0) * (float(end[k]) - float(base[k])) for k in base}
            tot = sum(mix.values())
            out[path] = {k: v / tot for k, v in mix.items()}
        else:
            base = book.get(path)
            if base is None:
                raise BookError(f"ось {ax['name']!r}: значение книги {path} — null")
            base = float(base)
            end = float(ax["high"] if s >= 0 else ax["low"])
            out[path] = base + w * (end - base)
    return out


def axis_book_value(book: Book, ax: Mapping[str, Any]) -> Any:
    """Значение книги на оси: число (value), 0 (shift), словарь (dict)."""
    if ax["kind"] == "shift":
        return 0.0
    v = book.get(ax["paths"][0])
    return copy.deepcopy(v) if isinstance(v, Mapping) else float(v)


def consistent_overrides(book: Book, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Подмены, которые держат правила книги: дисконт за управление = Σ каналов (М§8.2).

    Ось `valuation.governance.discount` меняет итог; каналы масштабируются пропорционально
    (итог книги 0 — разница кладётся в первый канал), иначе закрытая схема отвергла бы книгу.
    """
    out = dict(overrides)
    key = f"{GOVERNANCE}.discount"
    if key in out and f"{GOVERNANCE}.components" not in out:
        new = float(out[key])
        comps = copy.deepcopy(list(book.get(f"{GOVERNANCE}.components")))
        old = sum(float(c["sign"]) * float(c["value"]) for c in comps)
        if comps:
            if old:
                for c in comps:
                    c["value"] = float(c["value"]) * new / old
            else:
                c0 = comps[0]
                c0["value"] = float(c0["value"]) + (new - old) * float(c0["sign"])
            total = sum(float(c["sign"]) * float(c["value"]) for c in comps)
            out[key] = total              # итог — ровно сумма каналов (без ошибки округления)
        out[f"{GOVERNANCE}.components"] = comps
    return out


def trial_book(book: Book, overrides: Mapping[str, Any]) -> Book:
    """Книга с подменами по путям (схема проверяется), каналы дисконта согласованы."""
    if not overrides:
        return book
    return book.with_overrides(consistent_overrides(book, overrides))


def draw_points(n: int, n_axes: int, seed: int) -> list[list[float]]:
    """Точки осей s[i][j] латинского гиперкуба в порядке случайных чисел книги (М§10)."""
    rng = random.Random(int(seed))
    perms = []
    for _ in range(n_axes):
        p = list(range(n))
        rng.shuffle(p)
        perms.append(p)
    return [[tri_s((perms[j][i] + rng.random()) / n) for j in range(n_axes)] for i in range(n)]


# ------------------------------------------------------------------ прогон


def run_summary(run: GridRun) -> dict[str, float]:
    """Что полосе и диагностикам нужно от прогона сетки (малое, пересылается из пула);
    `ddm_ri` — max |V_DDM − V_RI| / |V_RI| по клеткам прогона (инвариант в каждом прогоне, М§6.4).

    Поля хвоста (`TAIL_FIELDS`, М§10) — массы клеток прогона под весами «свой взгляд»: с капитальным
    разрывом, с ROE терминала не выше роста и с отрицательной стоимостью; `low_floor` и `high_floor` — низ и
    верх прогона, если стоимость клетки ограничить нулём снизу (отображение V0 → цена то же)."""
    lo, hi = run.layers["macro_neutral"], run.layers["analytical"]
    floor_lo, floor_hi = (run.price_of(sum(layer.prob[c.key] * max(c.v_ri, 0.0) for c in run.cells))
                          for layer in (lo, hi))
    return {"low": run.low, "high": run.high, "v0_low": lo.v0, "v0_high": hi.v0, "bv_low": lo.bv_v,
            "bv_high": hi.bv_v, "roe_low": lo.roe_tc, "roe_high": hi.roe_tc, "k_low": lo.k_tc,
            "k_high": hi.k_tc, "bridge": run.bridge_amount, "governance": run.governance,
            "ddm_ri": max(abs(c.v_ddm - c.v_ri) / abs(c.v_ri) for c in run.cells),
            "gap_mass": hi.capital_gap_mass,
            "need_capital_mass": sum(hi.prob[c.key] for c in run.cells if c.roe_t <= c.g_t),
            "negative_mass": sum(hi.prob[c.key] for c in run.cells if c.v_ri < 0),
            "low_floor": floor_lo, "high_floor": floor_hi}


def draw_book(book: Book, axes: Sequence[Mapping[str, Any]], s: Sequence[float]) -> Book:
    """Книга прогона полосы: оси `axes` в положениях `s` (М§10) — одна функция у полосы и у диагностик,
    которым нужна книга отдельного прогона (ожидание квартала срединных прогонов, М§13)."""
    ov: dict[str, Any] = {}
    for ax, sj in zip(axes, s):
        ov.update(axis_overrides(book, ax, sj))
    return trial_book(book, ov)


def band_chunk(book: Book, facts: Facts, live: LiveInputs, axes: Sequence[Mapping[str, Any]],
               points: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    """Прогоны полосы в точках по порядку — одна функция для последовательного расчёта и пула."""
    return [run_summary(run_grid(draw_book(book, axes, s), facts, live)) for s in points]


def _task_chunk(book: Book, facts: Facts, tasks: Sequence[tuple[Mapping[str, Any], LiveInputs | None]]
                ) -> list[dict[str, Any]]:
    return [run_summary(run_grid(trial_book(book, ov), facts, lv)) for ov, lv in tasks]


# ------------------------------------------------------------------ пул процессов


_POOL: dict[str, Any] = {"executor": None, "workers": 0, "failed": False}


def workers() -> int:
    """Число процессов полосы из BANK_WORKERS: целое ≥ 1 или `auto` (ядра − 1, не больше 7)."""
    raw = os.environ.get(WORKERS_ENV, "").strip().lower()
    if raw in ("", "auto"):
        return max(1, min((os.cpu_count() or 1) - 1, MAX_WORKERS))
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{WORKERS_ENV}={raw!r}: нужно целое ≥ 1 или auto")
    return min(int(raw), WINDOWS_POOL_LIMIT)


def _executor(k: int) -> ProcessPoolExecutor:
    if _POOL["executor"] is None or _POOL["workers"] != k:
        close_pool()
        _POOL.update(executor=ProcessPoolExecutor(max_workers=k, mp_context=get_context("spawn")), workers=k)
    return _POOL["executor"]


def close_pool() -> None:
    """Закрывает пул (и при выходе из процесса — atexit)."""
    executor, _POOL["executor"], _POOL["workers"] = _POOL["executor"], None, 0
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


atexit.register(close_pool)


def _band_worker(blob: bytes, points: list[list[float]]) -> list[dict[str, Any]]:
    book, facts, live, axes = pickle.loads(blob)
    return band_chunk(book, facts, live, axes, points)


def _task_worker(blob: bytes, tasks: list) -> list[dict[str, Any]]:
    book, facts = pickle.loads(blob)
    return _task_chunk(book, facts, tasks)


def _unavailable(exc: BaseException) -> None:
    _POOL["failed"] = True
    close_pool()
    print(f"параллельный расчёт недоступен: {type(exc).__name__}: {exc}, считаю последовательно",
          file=sys.stderr)
    return None


def _pool_map(worker, blob: bytes, items: list, k: int) -> list | None:
    """Куски по порядку в пуле из k процессов; None — пул недоступен (дальше последовательно)."""
    if _POOL["failed"]:
        return None
    size = -(-len(items) // (k * CHUNKS_PER_WORKER))
    chunks = [items[i:i + size] for i in range(0, len(items), size)]
    try:
        parts = list(_executor(k).map(worker, [blob] * len(chunks), chunks))
    except (BrokenProcessPool, MemoryError, OSError, NotImplementedError, pickle.PicklingError) as exc:
        return _unavailable(exc)
    return [row for part in parts for row in part]


def _pool_size(n: int, k: int | None) -> int:
    if parent_process() is not None:       # в рабочем процессе пула — без вложенного пула
        return 1
    k = workers() if k is None else max(1, int(k))
    return k if n >= PARALLEL_MIN_DRAWS else 1


def evaluate_many(book: Book, facts: Facts, tasks: Sequence[tuple[Mapping[str, Any], LiveInputs | None]],
                  *, k: int | None = None) -> list[dict[str, Any]]:
    """Сводки прогонов сетки для списка (подмены книги, живые входы) — пулом, если задач много."""
    tasks = list(tasks)
    size = _pool_size(len(tasks), k)
    if size > 1:
        try:
            blob = pickle.dumps((book, facts), protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:  # noqa: BLE001 — не пересылается
            blob = None
            _unavailable(exc)
        if blob is not None:
            rows = _pool_map(_task_worker, blob, tasks, size)
            if rows is not None:
                return rows
    return _task_chunk(book, facts, tasks)


# ------------------------------------------------------------------ полоса


@dataclass(frozen=True)
class Band:
    axes: tuple[Mapping[str, Any], ...]    # оси книги по порядку
    s: tuple[tuple[float, ...], ...]       # положение оси s ∈ [−1, 1] по прогонам
    low_draws: tuple[float, ...]           # ₽, 0,1 (ROUND_HALF_UP)
    high_draws: tuple[float, ...]
    # сверх договора (только добавление)
    draws: int = 0
    seed: int = 0
    lam: float = 0.0                       # λ книги
    print_step: float = 1.0
    quantiles: tuple[float, ...] = ()
    ceiling_x_market: float = 0.0
    keys: tuple[str, ...] = ()             # ключи осей (axis_key)
    rows: tuple[Mapping[str, float], ...] = field(default=(), repr=False)   # точные сводки прогонов
    ddm_ri_max: float = 0.0                # max |V_DDM − V_RI| / V_RI по всем клеткам всех прогонов (М§6.4)

    def centres(self, lam: float) -> list[float]:
        return [lo + lam * (hi - lo) for lo, hi in zip(self.low_draws, self.high_draws)]

    def exact_centres(self, lam: float) -> list[float]:
        """Центры прогонов без округления до 0,1 ₽ (оценщик срединных прогонов); нет точных сводок — округлённые."""
        if not self.rows:
            return self.centres(lam)
        return [r["low"] + lam * (r["high"] - r["low"]) for r in self.rows]

    def levels(self) -> tuple[float, float, float, float, float]:
        """Уровни квантилей книги по порядку: низ 80 %, низ 50 %, медиана, верх 50 %, верх 80 %."""
        lv = tuple(sorted(float(q) for q in self.quantiles))
        if len(lv) != len(QUANTILE_NAMES):
            raise BookError(f"valuation.uncertainty.quantiles: нужно {len(QUANTILE_NAMES)} уровней "
                            "(P10, P25, медиана, P75, P90 выпуска), в книге {len(lv)}")
        return lv

    def stats(self, lam: float) -> dict[str, float]:
        """Квантили тип 7 уровней книги (P10, P25, медиана, P75, P90) и среднее центра при λ."""
        c = sorted(self.centres(lam))
        out = {name: quantile(c, q) for name, q in zip(QUANTILE_NAMES, self.levels())}
        out["mean"] = sum(c) / len(c)
        return out

    def medians(self, lam: float) -> dict[str, float]:
        """Медианы центра, низа и верха прогонов."""
        m = self.levels()[2]
        return {"central": quantile(sorted(self.centres(lam)), m),
                "low": quantile(sorted(self.low_draws), m), "high": quantile(sorted(self.high_draws), m)}

    def p_below(self, lam: float, price: float) -> float:
        c = self.centres(lam)
        return sum(1 for v in c if v < price) / len(c)

    def headline(self, lam: float, prices: Mapping[str, float], main: str) -> dict[str, Any]:
        """Квантили тип 7, среднее, P(центр < цены) по тикерам, печать (М§10)."""
        st = self.stats(lam)
        step = self.print_step
        rnd = lambda x: round_half_up(x, step)  # noqa: E731
        market = float(prices[main])
        return {
            "median": st["median"], "printed_median": rnd(st["median"]),
            "band80": [st["p10"], st["p90"]], "printed_band80": [rnd(st["p10"]), rnd(st["p90"])],
            "band50": [st["p25"], st["p75"]], "printed_band50": [rnd(st["p25"]), rnd(st["p75"])],
            "p10": st["p10"], "p25": st["p25"], "p75": st["p75"], "p90": st["p90"], "mean": st["mean"],
            "market": market, "market_by_ticker": {t: float(p) for t, p in prices.items()},
            "p_below_market": self.p_below(lam, market),
            "p_below_by_ticker": {t: self.p_below(lam, float(p)) for t, p in prices.items()},
            "market_percentile": self.p_below(lam, market),
            "upside": {t: st["median"] / float(p) - 1 for t, p in prices.items()},
        }

    def contributions(self, lam: float) -> list[dict[str, Any]]:
        """Вклады осей: доля квадрата ранговой корреляции s оси с центром при λ (словари — без вклада)."""
        skip = [j for j, ax in enumerate(self.axes) if ax["kind"] == "dict"]
        keys = self.keys or tuple(axis_keys(self.axes))
        rows = axis_contributions(keys, self.s, self.centres(lam), skip)
        for r in rows:
            ax = self.axes[r["j"]]
            r["name"] = ax["name"]
            r["paths"] = list(ax["paths"])
            r["judgement_key"] = keys[r["j"]]
        return rows

    def mean_of(self, field_: str) -> float:
        return sum(r[field_] for r in self.rows) / len(self.rows) if self.rows else float("nan")

    def tail(self, lam: float) -> dict[str, Any] | None:
        """Хвост полосы (М§10): в скольких прогонах есть клетки с капитальным разрывом (`capital_gap`), с ROE
        терминала не выше роста (`need_capital`) и с отрицательной стоимостью (`negative`) — {`draws`, `mass`:
        масса таких клеток под весами «свой взгляд» в среднем по ВСЕМ прогонам полосы (у прогона без таких
        клеток она ноль)}; `floor_shift` — на сколько выше были
        бы низ полосы и медиана центра при λ, если стоимость клетки ограничить нулём снизу (₽; не поправка
        печатаемого числа, а мера того, сколько весят в нём такие клетки). Нет точных сводок — None."""
        rows = self.rows
        if not rows or any(k not in rows[0] for k in TAIL_FIELDS):
            return None
        n = len(rows)

        def part(key: str) -> dict[str, float]:
            return {"draws": sum(1 for r in rows if r[key] > 0), "mass": sum(r[key] for r in rows) / n}

        base = sorted(r["low"] + lam * (r["high"] - r["low"]) for r in rows)
        floor = sorted(r["low_floor"] + lam * (r["high_floor"] - r["low_floor"]) for r in rows)
        levels = self.levels()
        return {"n": n, "capital_gap": part("gap_mass"), "need_capital": part("need_capital_mass"),
                "negative": part("negative_mass"),
                "floor_shift": {"low": quantile(floor, levels[0]) - quantile(base, levels[0]),
                                "median": quantile(floor, levels[2]) - quantile(base, levels[2])}}


def band(book: Book, facts: Facts, live: LiveInputs, *, draws: int | None = None,
         workers: int | None = None) -> Band:
    """Полоса A-V9: n прогонов сетки по суждениям книги (LHS, треугольные оси)."""
    U = "valuation.uncertainty"
    axes = tuple(copy.deepcopy(list(book.get(f"{U}.axes"))))
    for ax in axes:
        _check_axis(ax)
    n = int(draws if draws is not None else book.get(f"{U}.draws"))
    if n < 2:
        raise BookError(f"полоса: прогонов {n} — нужно не меньше двух")
    seed = int(book.get(f"{U}.seed"))
    points = draw_points(n, len(axes), seed)
    k = _pool_size(n, workers)
    rows = None
    if k > 1:
        try:
            blob = pickle.dumps((book, facts, live, list(axes)), protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:  # noqa: BLE001
            blob = None
            _unavailable(exc)
        if blob is not None:
            rows = _pool_map(_band_worker, blob, points, k)
    if rows is None:
        rows = band_chunk(book, facts, live, axes, points)
    return Band(
        axes=axes, s=tuple(tuple(p) for p in points),
        low_draws=tuple(round_half_up(r["low"], DRAW_STEP) for r in rows),
        high_draws=tuple(round_half_up(r["high"], DRAW_STEP) for r in rows),
        draws=n, seed=seed, lam=float(book.get("joint.own_macro_confidence")),
        print_step=float(book.get("valuation.headline.print_step")),
        quantiles=tuple(float(q) for q in book.get(f"{U}.quantiles")),
        ceiling_x_market=float(book.get("valuation.headline.ceiling_x_market")),
        keys=tuple(axis_keys(axes)), rows=tuple(rows),
        ddm_ri_max=max((float(r.get("ddm_ri", 0.0)) for r in rows), default=0.0))


# ------------------------------------------------------------------ пересчёт медианы


class MedianAnchor:
    """Медиана при подмене = печатаемая медиана + её сдвиг на общих случайных числах (850oa).

    База — полоса на `median_draws` прогонах той же книги и тех же живых входов; пересчёт —
    та же выборка на изменённой книге (или других живых входах). `evaluations` — сколько
    полос пересчитано (каждая — n сеток).
    """

    def __init__(self, book: Book, facts: Facts, live: LiveInputs, full: Band, *, n: int | None = None,
                 base: Band | None = None):
        self.book, self.facts, self.live, self.full = book, facts, live, full
        self.n = int(n if n is not None else book.get("valuation.uncertainty.median_draws"))
        self.base = base if base is not None else band(book, facts, live, draws=self.n)
        self.evaluations = 1
        self.full_grids = 0

    def recompute(self, book: Book | None = None, live: LiveInputs | None = None) -> Band:
        self.evaluations += 1
        return band(book or self.book, self.facts, live or self.live, draws=self.n)

    def anchored(self, other: Band, lam: float | None = None) -> dict[str, float]:
        """Медианы центра, низа и верха `other`, привязанные к печатаемой полосе."""
        lam = self.full.lam if lam is None else lam
        f, b, o = self.full.medians(lam), self.base.medians(lam), other.medians(lam)
        return {k: f[k] + (o[k] - b[k]) for k in f}

    def at(self, book: Book | None = None, live: LiveInputs | None = None, lam: float | None = None
           ) -> dict[str, float]:
        return self.anchored(self.recompute(book, live), lam)

    def delta(self, other: Band, lam: float | None = None) -> float:
        """Сдвиг медианы центра `other` к базе при λ (₽)."""
        lam = self.full.lam if lam is None else lam
        return other.medians(lam)["central"] - self.base.medians(lam)["central"]


def rank_window(book: Book) -> tuple[float, float]:
    """Окно рангов оценщика срединных прогонов — `valuation.sensitivities.rank_window` (М§10)."""
    lo, hi = (float(x) for x in book.get("valuation.sensitivities.rank_window"))
    if not 0.0 <= lo < hi <= 1.0:
        raise BookError(f"valuation.sensitivities.rank_window = [{lo}, {hi}]: нужно 0 ≤ нижний < верхний ≤ 1 (М§10)")
    return lo, hi


def median_shift(base: Sequence[float], shifted: Sequence[float], window: tuple[float, float], *,
                 by: Sequence[float] | None = None) -> float:
    """Оценщик срединных прогонов (М§10): среднее попарных разностей `shifted[i] − base[i]` по прогонам, чей
    ранг в базовой выборке лежит в окне квантилей `window`.

    Прогоны книги и сдвинутой книги — на одних и тех же точках гиперкуба, поэтому сдвиг виден в каждом
    прогоне отдельно. Разность двух медиан — тот же сдвиг, измеренный по одному-двум прогонам; среднее по
    всей выборке — другая величина (сдвиг среднего). `by` — выборка, по которой берутся ранги (у местного
    наклона — прогоны книги без нового наблюдения); нет — ранги по `base`."""
    n = len(base)
    if n == 0 or n != len(shifted) or (by is not None and len(by) != n):
        raise ValueError("оценщик срединных прогонов: выборки разной длины или пусты")
    chosen = middle_draws(base if by is None else by, window)
    return sum(shifted[i] - base[i] for i in chosen) / len(chosen)


def middle_draws(values: Sequence[float], window: tuple[float, float]) -> list[int]:
    """Срединные прогоны (М§10): номера прогонов, чей ранг в выборке `values` лежит в окне квантилей `window`;
    окно уже шага рангов — один прогон, ближайший к середине окна."""
    n = len(values)
    if n == 0:
        raise ValueError("срединные прогоны: выборка пуста")
    lo, hi = window
    rk = ranks(values)
    if n == 1:
        chosen = [0]
    else:
        chosen = [i for i in range(n) if lo <= (rk[i] - 1) / (n - 1) <= hi]
        if not chosen:                                   # окно уже шага рангов — ближайший к середине окна прогон
            mid = (lo + hi) / 2
            chosen = [min(range(n), key=lambda i: abs((rk[i] - 1) / (n - 1) - mid))]
    return chosen


# ------------------------------------------------------------------ цена ошибки суждения

# Единица оси по пути (М§10) — знание схемы книги, не величина значения: ключ пути → код П§0.2.
PP_KEYS = ("nim_shift",)                                  # и любой ключ с суффиксом «_pp»
MONEY_KEYS = ("amount", "result_real", "misc_net_real")   # суммы, млрд ₽
YEARS_KEYS = ("fvoci_duration", "fvoci_maturity", "fvtpl_bond_duration")
NUMBER_KEYS = ("kappa", "psi", "beta_e", "beta", "rho", "target", "excess_capital_multiple")


def axis_unit(book: Book, ax: Mapping[str, Any]) -> str:
    """Код единицы оси (П§0.2): поле `unit` оси; нет — как у строки обратного расчёта с теми же путями;
    нет — по путям оси."""
    reverse_units = {tuple(a["paths"]): str(a.get("unit", ""))
                     for a in book.get("valuation.reverse_dcf.axes") or []}
    return _unit(ax, reverse_units)


def _unit(ax: Mapping[str, Any], reverse_units: Mapping[tuple, str]) -> str:
    unit = ax.get("unit")
    if unit is not None:
        if unit not in UNIT_CODES:
            raise BookError(f"ось {ax.get('name')!r}: единица {unit!r} — не код П§0.2")
        return str(unit)
    if ax["kind"] == "shift":
        return "pp"
    if ax["kind"] == "dict":
        return "dict"
    key = tuple(ax["paths"])
    word = reverse_units.get(key)
    if word in UNIT_WORDS:
        return UNIT_WORDS[word]
    if word in UNIT_CODES:
        return str(word)
    parts = set(str(ax["paths"][0]).split("."))
    if any(p.endswith("_pp") for p in parts) or parts & set(PP_KEYS):
        return "pp"
    if parts & set(MONEY_KEYS):
        return "bn"
    if parts & set(YEARS_KEYS):
        return "years"
    if parts & set(NUMBER_KEYS):
        return "number"
    return "pct"


MEAN_SHIFT_DIVISOR = 6                # сдвиг положения оси: (цена_high + цена_low − 2 × точка) / 6 (М§10)
SOURCE_MAX = 80                       # источник суждения — не длиннее 80 знаков (П§2 judgements)
CONTINUATION_INDENT = 4               # строка-продолжение комментария стоит правее ключа больше чем на 4 знака
# Метка источника в комментарии книги: [Ф: …], [Р/В: …], [В], [НП], [C: …]; группа — текст после двоеточия.
MARK = re.compile(r"\[(?:Ф|Р|В|НП|C|С)(?:/(?:Ф|Р|В|НП|C|С))*(?:\s+[^:\]]*)?(?::\s*([^\]]*))?\]")
# Служебные имена: имя поля с подчёркиванием или точечный путь ключа книги.
SERVICE = re.compile(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|(?<![\w.])[a-z][a-z_]*(?:\.[a-z][a-z_0-9]*)+(?![\w])")
# Служебный текст, которого на экране быть не должно (П§0.2): правило → шаблон. Правится источник строки
# (книга, факты, ядро); здесь — сторож: фраза источника суждения с таким текстом опускается.
SERVICE_TEXT: tuple[tuple[str, re.Pattern], ...] = (
    ("кортеж", re.compile(r"\(\d{4}, \d+, [A-Z]{3,}")),
    ("e-нотация", re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?e[-−+]?\d+(?!\w)")),
    ("метка класса допущения", re.compile(r"\[(?:В|Ф|Р|НП|C|С)(?:/(?:В|Ф|Р|НП|C|С))*\]")),
    ("хвост sha256", re.compile(r"sha256 [0-9a-f]{8,}")),
    ("рабочая пометка", re.compile(r"ведущ|первичк|антибот|без токена", re.IGNORECASE)),
    # источник называется словами владельца («брокерский календарь дивидендов»), а не именем метода API
    ("имя метода API", re.compile(r"(?<![A-Za-z])(?:Get|List|Find)[A-Z][A-Za-z]+|(?<![A-Za-z])[A-Z][A-Za-z]+Service(?![a-z])")),
    # путь — строчные латинские сегменты через «/» (метка клетки «M/crisis/upper» и «P/B» — не путь) или имя файла
    ("путь к листу или файлу", re.compile(r"(?<![\w/])[a-z][a-z0-9_-]+/[a-z0-9_.-]|[\w-]+\.(?:ya?ml|json|csv|py|xlsx)\b")),
    ("код периода", re.compile(r"(?<!\w)\d{4}M(?:0[1-9]|1[0-2])(?!\w)")),
    ("дата без года", re.compile(r"(?<![\d.,–−-])(?:0[1-9]|[12]\d|3[01])\.(?:0[1-9]|1[0-2])(?![\d.])")),
)


def service_text(text: Any) -> list[str]:
    """Нарушения правила П§0.2 «служебного текста на экране нет» в строке: имена сработавших шаблонов."""
    s = str(text or "")
    return [name for name, pattern in SERVICE_TEXT if pattern.search(s)]


KEY_LINE = re.compile(r'^(\s*)(?:"([^"]+)"|([\w.\-]+))\s*:(?:\s|$)')


def mean_shift(point: float, price_low: float | None, price_high: float | None) -> float | None:
    """Сдвиг положения оси (М§10): первый порядок её вклада в «среднее прогонов − точка», ₽."""
    if price_low is None or price_high is None:
        return None
    return (price_high + price_low - 2 * point) / MEAN_SHIFT_DIVISOR


def _split_comment(raw: str) -> tuple[str, str]:
    """Строка YAML → (код, комментарий): «#» вне кавычек, в начале строки или после пробела."""
    quoted = False
    for i, ch in enumerate(raw):
        if ch == '"':
            quoted = not quoted
        elif ch == "#" and not quoted and (i == 0 or raw[i - 1].isspace()):
            return raw[:i], raw[i + 1:].strip()
    return raw, ""


def book_notes(book: Book) -> dict[str, str]:
    """Комментарии машинной книги по точечным путям ключей (как `note_of` 850oa): комментарий строки
    ключа и строки-продолжения под ней (стоят заметно правее ключа). Книга не из YAML — пусто."""
    path = book.path
    if path is None or path.suffix not in (".yaml", ".yml") or not path.exists():
        return {}
    notes: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    last: tuple[str, int] | None = None                # (путь ключа, отступ) последней строки с ключом
    for raw in path.read_text(encoding="utf-8").splitlines():
        code, comment = _split_comment(raw)
        if not code.strip():
            col = len(raw) - len(raw.lstrip())
            if comment and last is not None and col > last[1] + CONTINUATION_INDENT:
                notes[last[0]] = (notes.get(last[0], "") + " " + comment).strip()
            else:
                last = None
            continue
        m = KEY_LINE.match(code)
        if not m:                                       # элемент списка, продолжение потока — не ключ
            last = None
            continue
        indent, key = len(m.group(1)), m.group(2) or m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        dotted = ".".join(k for _, k in stack)
        if comment:
            notes[dotted] = comment
        last = (dotted, indent)
    return notes


def source_text(note: str, limit: int = SOURCE_MAX) -> str:
    """Источник из комментария книги: содержимое меток `[Р: …]` (без самих меток), иначе текст без меток;
    фразы со служебными именами (ключи книги, имена полей) опускаются; не длиннее `limit` знаков по
    границе фразы."""
    note = " ".join(str(note).split())
    inner = [m.group(1).strip() for m in MARK.finditer(note) if (m.group(1) or "").strip()]
    text = "; ".join(inner) if inner else MARK.sub("", note)
    text = text.replace("[", "").replace("]", "")          # скобка метки, разорванной переносом комментария
    phrases = [ph.strip(" ;,—:") for ph in text.split(";")]
    phrases = [ph for ph in phrases if ph and not SERVICE.search(ph) and not service_text(ph)]
    out = ""
    for ph in phrases:
        nxt = ph if not out else f"{out}; {ph}"
        if len(nxt) > limit:
            break
        out = nxt
    if out or not phrases:
        return out
    first = phrases[0]                                     # первая фраза длиннее предела — режется по границе
    room = limit - 1                                       # место под «…»
    cut = max(first.rfind(sep, 0, room + len(sep)) for sep in (", ", " — "))
    if cut < room // 2:                                    # граница части фразы слишком рано — по границе слова
        cut = first.rfind(" ", 0, room + 1)
    return (first[:cut] if cut > 0 else first[:room]).rstrip(" ,—:(") + "…"


def judgement_source(book: Book, ax: Mapping[str, Any], notes: Mapping[str, str]) -> str:
    """Источник суждения словами (П§2): комментарий книги к первому пути оси — поиск по префиксу пути
    (не выше второго уровня); нет — «book-<версия>, ось „<имя>“» (версия — с именем, П§0.2)."""
    parts = str(ax["paths"][0]).split(".")
    for cut in range(len(parts), 1, -1):
        note = notes.get(".".join(parts[:cut]))
        if note:
            text = source_text(note)
            if text:
                return text
    return f"book-{book.get('meta.version')}, ось „{ax['name']}“"


def off_band_axes(book: Book) -> tuple[Mapping[str, Any], ...]:
    """Оси вне полосы (`valuation.uncertainty.off_band_axes`, М§10): не разыгрываются; у книги со всеми
    осями в полосе — пусто."""
    axes = tuple(copy.deepcopy(list(book.get("valuation.uncertainty.off_band_axes") or [])))
    for ax in axes:
        _check_axis(ax)
        if ax["kind"] not in ("value", "shift"):
            raise BookError(f"ось вне полосы {ax.get('name')!r}: только value и shift (М§10)")
    return axes


def _axis_prices(book: Book, facts: Facts, live: LiveInputs, axes: Sequence[Mapping[str, Any]], lam: float,
                 point: float | None) -> tuple[float, list[tuple[float, float]]]:
    """Точка книги и точки на краях каждой оси (прочие оси — книга)."""
    tasks: list[tuple[Mapping[str, Any], LiveInputs | None]] = [] if point is not None else [({}, live)]
    for ax in axes:
        tasks.append((axis_overrides(book, ax, -1.0), live))
        tasks.append((axis_overrides(book, ax, 1.0), live))
    runs = evaluate_many(book, facts, tasks)
    at = lambda r: r["low"] + lam * (r["high"] - r["low"])  # noqa: E731
    if point is None:
        point, runs = at(runs[0]), runs[1:]
    return point, [(at(runs[2 * j]), at(runs[2 * j + 1])) for j in range(len(axes))]


def off_band_shift(book: Book, facts: Facts, live: LiveInputs, point: float | None = None,
                   rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Диагностика П§2 judgements.off_band_shift: Σ сдвигов положения осей вне полосы (₽), порог гейта
    `print_step` / 2 и число таких осей. `rows` — готовые строки суждений (иначе края осей считаются здесь)."""
    off = off_band_axes(book)
    limit = float(book.get("valuation.headline.print_step")) / 2
    if not off:
        return {"rub": 0.0, "limit": limit, "axes": 0}
    shifts = [r.get("mean_shift") for r in rows or [] if not r.get("in_band")]
    if len(shifts) != len(off) or any(s is None for s in shifts):
        lam = float(book.get("joint.own_macro_confidence"))
        point, prices = _axis_prices(book, facts, live, off, lam, point)
        shifts = [mean_shift(point, lo, hi) for lo, hi in prices]
    return {"rub": sum(shifts), "limit": limit, "axes": len(off)}


def judgements(book: Book, facts: Facts, live: LiveInputs, band: Band, *, point: float | None = None
               ) -> list[dict[str, Any]]:
    """П§2 judgements.rows: точка при значении оси low / high (прочие — книга), размах, сдвиг положения,
    вклад, источник; оси полосы и оси вне полосы (`in_band = false`, вклад `null`). `point` — точка книги
    (нет — считается здесь)."""
    off = off_band_axes(book)
    axes = tuple(band.axes) + off
    keys = tuple(band.keys or axis_keys(band.axes)) + tuple(axis_keys(off))
    in_band = [True] * len(band.axes) + [False] * len(off)
    contrib = {r["axis"]: r for r in band.contributions(band.lam)}
    point, prices = _axis_prices(book, facts, live, axes, band.lam, point)
    reverse_units = {tuple(a["paths"]): str(a.get("unit", ""))
                     for a in book.get("valuation.reverse_dcf.axes") or []}
    notes = book_notes(book)
    rows = []
    for j, ax in enumerate(axes):
        p_lo, p_hi = prices[j]
        c = contrib.get(keys[j]) if in_band[j] else None
        rows.append({
            "id": keys[j], "name": ax["name"], "unit": _unit(ax, reverse_units), "kind": ax["kind"],
            "paths": list(ax["paths"]), "book": axis_book_value(book, ax),
            "low": copy.deepcopy(ax["low"]), "high": copy.deepcopy(ax["high"]), "dist": ax["dist"],
            "price_low": p_lo, "price_high": p_hi, "swing": abs(p_hi - p_lo),
            "mean_shift": None if ax["kind"] == "dict" else mean_shift(point, p_lo, p_hi),
            "share": None if c is None else c["share"], "rank_corr": None if c is None else c["rank_corr"],
            "in_band": in_band[j], "source": judgement_source(book, ax, notes),
        })
    return sorted(rows, key=lambda r: -r["swing"])

