"""Ядро, волна перед первым push (решения ведущего P4): точность диагностик.

Гейт пола спреда на концах осей долей (М§4.5, §14.2); уточнение корня обратного расчёта на полной
полосе и «в диапазоне книги» — о корне (М§11.2); нейтральное значение точки по двум допускам (М§13);
срединные прогоны полосы и книга отдельного прогона (М§10).
"""

from __future__ import annotations

import pytest

from model import uncertainty as U
from model.checks import SHARE_KEYS, check_gates, share_axis_ends
from model.grid import run_grid
from model.nextreport import _point_neutral
from model.nii import solve_transmission
from model.paths import Trajectory
from model.reverse import _refine_full, bisect_point, root_in_range
from tests.support_core import fixture_book, fixture_facts, real_book_or_skip

tact = pytest.mark.tact


def _gate(run):
    return next(f for f in check_gates(run) if f.name == "lt_spread_floor")


def _share_axis(path: str, low: float, high: float) -> dict:
    return {"name": f"ось {path}", "kind": "value", "paths": [path], "low": low, "high": high, "dist": "triangular"}


# ------------------------------------------------------------------ гейт пола спреда на осях долей


@tact
def test_share_axis_ends_are_the_corners_of_the_share_axes_without_the_book_point():
    """Углы области осей долей: сочетания концов осей по `nii.sigma0_split` и `nii.phi_split`; сама точка
    книги углом не считается; без осей долей углов нет."""
    book = fixture_book()
    plain = [a for a in book.get("valuation.uncertainty.axes") if not set(a["paths"]) & set(SHARE_KEYS)]
    assert share_axis_ends(book.with_overrides({"valuation.uncertainty.axes": plain})) == []
    split, phi = float(book.get("nii.sigma0_split")), float(book.get("nii.phi_split"))
    one = book.with_overrides({"valuation.uncertainty.axes": plain + [_share_axis("nii.phi_split", 0.0, phi)]})
    assert share_axis_ends(one) == [{"nii.phi_split": 0.0}]                  # верхний конец — значение книги
    both = book.with_overrides({"valuation.uncertainty.axes": plain + [
        _share_axis("nii.sigma0_split", 0.0, split), _share_axis("nii.phi_split", phi, 1.0)]})
    assert share_axis_ends(both) == [{"nii.sigma0_split": 0.0, "nii.phi_split": phi},
                                     {"nii.sigma0_split": 0.0, "nii.phi_split": 1.0},
                                     {"nii.sigma0_split": split, "nii.phi_split": 1.0}]


@tact
def test_lt_floor_gate_is_checked_at_the_ends_of_the_share_axes():
    """М§4.5, §14.2: оси долей обязаны лежать там, где гейт проходит. Полы, которые выдерживают значения книги,
    но не выдерживает конец оси доли сжатия (всё сжатие — в кредитных спредах), — гейт срабатывает, называет
    конец оси и мир; спред в углу — закрытой формулой с φ_A = φ; масса — вес этих миров."""
    book, facts = fixture_book(), fixture_facts()
    base = run_grid(book, facts)
    tr = base.ctx.transmission
    phi = float(book.get("nii.phi_split"))
    assert tr.phi > 0 and 0 < phi < 1
    floors = {b: min(row.values()) - 1e-4 for b, row in tr.lt_spread.items()}   # значения книги проходят с запасом
    plain = [a for a in book.get("valuation.uncertainty.axes") if not set(a["paths"]) & set(SHARE_KEYS)]
    common = {"checks.lt_spread_floor": floors}
    quiet = _gate(run_grid(book.with_overrides({**common, "valuation.uncertainty.axes": plain}), facts))
    assert not quiet.fired and quiet.mass == 0 and quiet.detail["axis_ends"] == []
    assert "на концах осей долей" not in quiet.message                        # осей долей нет — и слов о них нет
    inside = book.with_overrides({**common, "valuation.uncertainty.axes": plain + [
        _share_axis("nii.phi_split", 0.0, phi)]})
    gate = _gate(run_grid(inside, facts))
    assert not gate.fired and "при значениях книги и на концах осей долей" in gate.message
    wide = book.with_overrides({**common, "valuation.uncertainty.axes": plain + [
        _share_axis("nii.phi_split", 0.0, 1.0)]})
    run = run_grid(wide, facts)
    gate = _gate(run)
    assert gate.fired and "на концах осей долей" in gate.message and "100,0 %" in gate.message
    # закрытая формула угла: φ_A = φ — всё сжатие в кредитных спредах
    books = book.get("nii.books")
    last = str(book.get("meta.last_period"))
    y, h = int(last[:4]), int(last[-1])
    ref_w = str(book.get("nii.transmission.reference_world"))
    bad = set()
    for b, row in tr.lt_spread.items():
        spec = books[b]
        path = lambda w: f"worlds.{w}.key_rate" if spec["ref"] == "key" else f"worlds.{w}.{spec['ref']}"  # noqa: E731
        for w in row:
            s = Trajectory(spec["spread"]).value(y, h) + (tr.sigma0 if spec["lt_shift"] else 0.0)
            if spec["phi"]:
                s -= tr.phi * (Trajectory(book.get(path(w))).value(y, h)
                               - Trajectory(book.get(path(ref_w))).value(y, h))
            if s < floors[b]:
                bad.add(w)
    assert bad and [e["shares"] for e in gate.detail["axis_ends"]] == [{"nii.phi_split": 1.0}]
    assert sorted(gate.detail["axis_ends"][0]["worlds"]) == sorted(bad) == sorted(gate.detail["worlds"])
    weights = run.layers["analytical"].world_weights
    assert gate.mass == pytest.approx(sum(weights[w] for w in bad), abs=1e-12)
    corner = solve_transmission(wide.with_overrides({"nii.phi_split": 1.0}), facts, run.ctx.bridge)
    assert corner.phi == pytest.approx(tr.phi, abs=1e-15) and corner.phi_assets == pytest.approx(tr.phi, abs=1e-15)


def test_share_axes_of_the_real_book_lie_where_the_gate_holds():
    """Книга: оси долей σ0 и φ лежат в области гейта — в углах осей пол выдержан во всех мирах; вдоль осей
    (прочие — на значениях книги) эффективный спред не ниже пола."""
    book, facts = real_book_or_skip()
    run = run_grid(book, facts)
    gate = _gate(run)
    assert not gate.fired and gate.detail["axis_ends"] == [], gate.message
    corners = share_axis_ends(book)
    assert corners, "у книги есть оси долей"
    floors = book.get("checks.lt_spread_floor")
    for corner in corners:
        tr = solve_transmission(book.with_overrides(corner), facts, run.ctx.bridge)
        for b, row in tr.lt_spread.items():
            assert min(row.values()) >= float(floors[b]), (corner, b, row)


# ------------------------------------------------------------------ обратный расчёт: уточнение и «в диапазоне»


@tact
def test_refinement_takes_the_secant_step_until_the_gap_is_inside_the_stop():
    """М§11.2: один пересчёт полной полосы только измеряет невязку решения подвыборки; второй стоит на шаге
    секущей от точки книги — на линейной невязке он попадает в корень. Невязка в стопе — второго пересчёта нет."""
    calls: list[float] = []

    def g(x: float) -> float:
        calls.append(x)
        return 1000.0 * (x - 0.0052)

    value, gap, estimate = _refine_full(g, 0.0, -5.2, 0.0048, -0.01, 0.01, 2, 0.05)
    assert calls == [0.0048, pytest.approx(0.0052, abs=1e-12)]
    assert value == pytest.approx(0.0052, abs=1e-12) and abs(gap) <= 1e-9 and estimate == value
    calls.clear()
    value, gap, estimate = _refine_full(g, 0.0, -5.2, 0.0048, -0.01, 0.01, 1, 0.05)   # один шаг: приближение
    assert calls == [0.0048] and value == 0.0048 and gap == pytest.approx(-0.4)
    assert estimate == pytest.approx(0.0052, abs=1e-12)                              # оценка корня — секущей
    calls.clear()
    value, gap, estimate = _refine_full(g, 0.0, -5.2, 0.0048, -0.01, 0.01, 2, 0.5)    # невязка уже в стопе
    assert calls == [0.0048] and (value, estimate) == (0.0048, 0.0048)
    value, gap, estimate = _refine_full(g, 0.0, -5.2, 0.0048, -0.01, 0.0050, 2, 0.05)  # шаг не выходит из поиска
    assert value == 0.0050 and estimate <= 0.0050


@tact
def test_in_range_is_a_statement_about_the_root():
    """М§11.2: «в диапазоне книги» печатается только при корне в диапазоне. Приближение внутри диапазона, у
    которого оценка корня — за краем, — «вне»; решение с невязкой в стопе судится само; нет решения — «вне»."""
    tol = 0.5
    assert root_in_range(0.0048, 5.6, 0.0052, -0.003, 0.005, tol) is False     # строка CoR книги 1.3 до уточнения
    assert root_in_range(0.0048, 5.6, 0.0049, -0.003, 0.005, tol) is True      # и приближение, и корень внутри
    assert root_in_range(0.0048, 0.2, 0.0052, -0.003, 0.005, tol) is True      # сошлось — судится решение
    assert root_in_range(0.0052, -0.1, 0.0052, -0.003, 0.005, tol) is False    # корень за краем
    assert root_in_range(0.004, 0.3, None, -0.003, 0.005, tol) is True         # без уточнения — решение подвыборки
    assert root_in_range(None, None, None, 0.0, 1.0, tol) is False


# ------------------------------------------------------------------ нейтральное значение точки


@tact
def test_point_neutral_does_not_stick_to_a_table_row():
    """М§13: между соседними строками со сменой знака нейтральное значение точки ищется по двум допускам —
    строка с невязкой в рублёвом допуске корнем не считается (прежде бисекция принимала край отрезка)."""
    slope, root_true = 1600.0, 0.06672
    pg = lambda x: slope * (x - root_true)                                    # noqa: E731
    values = [0.064, 0.067, 0.070]
    assert abs(pg(0.067)) <= 0.5 and bisect_point(pg, 0.064, 0.067, tol=0.5)[0] == 0.067   # так «прилипало»
    root = _point_neutral(values, [pg(v) for v in values], pg, 0.002, 0.5, 0.0001)
    assert abs(root - root_true) <= 0.0001 and root != 0.067
    # без смены знака — бисекция на отрезке таблицы, расширенном на search_pad; край с невязкой в допуске — корень
    low = lambda x: slope * (x - 0.0715)                                      # noqa: E731
    assert _point_neutral(values, [low(v) for v in values], low, 0.002, 0.5, 0.0001) == pytest.approx(0.0715, abs=4e-4)
    far = lambda x: slope * (x - 0.09)                                        # noqa: E731
    assert _point_neutral(values, [far(v) for v in values], far, 0.002, 0.5, 0.0001) is None


# ------------------------------------------------------------------ срединные прогоны, книга прогона


@tact
def test_middle_draws_are_the_draws_ranked_inside_the_window():
    """М§10: срединные прогоны — те, чей ранг в выборке лежит в окне квантилей; окно уже шага рангов — один
    прогон, ближайший к середине окна; оценщик сдвига медианы усредняет попарные разности по ним же."""
    values = [50.0, 10.0, 40.0, 20.0, 30.0, 60.0, 0.0, 90.0, 70.0, 80.0]      # ранги: 6 2 5 3 4 7 1 10 8 9
    assert U.middle_draws(values, (0.4, 0.6)) == [0, 2]                       # ранги 5 и 6: квантили 4/9 и 5/9
    assert U.middle_draws(values, (0.0, 1.0)) == list(range(10))
    assert U.middle_draws(values, (0.49, 0.51)) in ([0], [2])                 # окно уже шага рангов — один прогон
    assert U.middle_draws([7.0], (0.4, 0.6)) == [0]
    shifted = [v + i for i, v in enumerate(values)]
    assert U.median_shift(values, shifted, (0.4, 0.6)) == pytest.approx((0 + 2) / 2)
    with pytest.raises(ValueError):
        U.middle_draws([], (0.4, 0.6))


@tact
def test_draw_book_is_the_book_of_one_band_draw():
    """Книга прогона полосы — оси в положениях точки гиперкуба: её сетка даёт ровно прогон полосы."""
    book, facts = fixture_book(), fixture_facts()
    axes = list(book.get("valuation.uncertainty.axes"))
    points = U.draw_points(3, len(axes), int(book.get("valuation.uncertainty.seed")))
    from model.grid import live_from_book
    live = live_from_book(book, facts)
    rows = U.band_chunk(book, facts, live, axes, points)
    for s, row in zip(points, rows):
        run = run_grid(U.draw_book(book, axes, s), facts, live)
        assert (run.low, run.high) == (row["low"], row["high"])
    centre = U.draw_book(book, axes, [0.0] * len(axes))
    assert run_grid(centre, facts, live).point == pytest.approx(run_grid(book, facts, live).point, rel=1e-12)
