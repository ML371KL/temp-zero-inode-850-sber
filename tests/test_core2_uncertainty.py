"""Полоса A-V9 (М§10): печать, треугольные оси, LHS, быстрая выборка, вклады осей, привязка медианы."""

from __future__ import annotations

import math
import random

import pytest

from model import uncertainty as U
from model.grid import live_from_book, run_grid
from tests.support_core2 import book_and_facts, small_book


# ------------------------------------------------------------------ печать и статистика


@pytest.mark.parametrize("x, step, want", [
    (437.5, 5, 440.0), (437.4999, 5, 435.0), (432.5, 5, 435.0), (1.25, 0.1, 1.3), (1.35, 0.1, 1.4),
    (272.87, 0.1, 272.9), (-2.5, 5, -5.0), (0.0, 5, 0.0), (1002.5, 5, 1005.0),
])
@pytest.mark.tact
def test_round_half_up_in_decimal(x, step, want):
    assert U.round_half_up(x, step) == want


@pytest.mark.tact
def test_triangular_axis_has_book_value_as_median_and_ends():
    assert U.tri_s(0.5) == 0.0
    assert U.tri_s(0.0) == -1.0 and U.tri_s(1.0) == 1.0
    for u in (0.1, 0.25, 0.4):
        assert U.tri_s(u) == pytest.approx(-U.tri_s(1 - u), abs=1e-15)
    # плотность треугольника: P(|s| < 0.5) = 0.75
    rng = random.Random(1)
    s = [U.tri_s(rng.random()) for _ in range(20000)]
    assert sum(1 for v in s if abs(v) < 0.5) / len(s) == pytest.approx(0.75, abs=0.02)


@pytest.mark.tact
def test_quantile_is_type_7():
    v = [1.0, 2.0, 4.0, 8.0]
    assert U.quantile(v, 0.5) == 3.0
    assert U.quantile(v, 0.1) == pytest.approx(1.3)
    assert U.quantile(v, 0.9) == pytest.approx(6.8)


@pytest.mark.tact
def test_latin_hypercube_order_is_part_of_the_definition():
    n, k, seed = 50, 3, 20260930
    pts = U.draw_points(n, k, seed)
    # каждая ось стратифицирована: ровно одна точка в каждой страте
    for j in range(k):
        strata = sorted(int(_u_of_s(pts[i][j]) * n) for i in range(n))
        assert strata == list(range(n))
    # порядок случайных чисел: перестановки по осям, затем u по прогонам и осям
    rng = random.Random(seed)
    perms = []
    for _ in range(k):
        p = list(range(n))
        rng.shuffle(p)
        perms.append(p)
    first = [U.tri_s((perms[j][0] + rng.random()) / n) for j in range(k)]
    assert pts[0] == first
    assert U.draw_points(n, k, seed) == pts


def _u_of_s(s: float) -> float:
    """Обратное к tri_s: u(s)."""
    return (s + 1) ** 2 / 2 if s < 0 else 1 - (1 - s) ** 2 / 2


# ------------------------------------------------------------------ вклады осей


@pytest.mark.tact
def test_axis_contributions_are_squared_rank_correlation_shares():
    rng = random.Random(7)
    pts = [[rng.uniform(-1, 1) for _ in range(3)] for _ in range(400)]
    centre = [math.exp(3 * s[0]) + 0.3 * s[1] for s in pts]          # монотонно по оси 0, слабо по оси 1
    rows = U.axis_contributions(["a", "b", "c"], pts, centre)
    assert sum(r["share"] for r in rows) == pytest.approx(1.0, abs=1e-12)
    assert rows[0]["axis"] == "a" and rows[0]["share"] > 0.8
    shares = {r["axis"]: r["share"] for r in rows}
    assert shares["b"] > shares["c"]
    # Спирмен не зависит от монотонного преобразования центра
    rows2 = U.axis_contributions(["a", "b", "c"], pts, [math.log(c + 10) for c in centre])
    assert [r["share"] for r in rows2] == pytest.approx([r["share"] for r in rows], abs=1e-12)
    # оси-словари не участвуют: доли остальных — в сумме 1
    rows3 = U.axis_contributions(["a", "b", "c"], pts, centre, skip=[0])
    assert {r["axis"] for r in rows3} == {"b", "c"} and sum(r["share"] for r in rows3) == pytest.approx(1.0)


# ------------------------------------------------------------------ оси книги


@pytest.mark.tact
def test_axis_overrides_value_shift_dict():
    book, _ = book_and_facts()
    axes = book.get("valuation.uncertainty.axes")
    for ax in axes:
        for s in (-1.0, -0.5, 0.0, 0.5, 1.0):
            ov = U.axis_overrides(book, ax, s)
            assert set(ov) == set(ax["paths"])
            if ax["kind"] == "value":
                base = float(book.get(ax["paths"][0]))
                end = ax["high"] if s >= 0 else ax["low"]
                assert ov[ax["paths"][0]] == pytest.approx(base + abs(s) * (end - base))
            elif ax["kind"] == "dict":
                assert sum(ov[ax["paths"][0]].values()) == pytest.approx(1.0)
            if s == 0.0 and ax["kind"] != "shift":
                assert ov[ax["paths"][0]] == pytest.approx(book.get(ax["paths"][0]))
    with pytest.raises(Exception):
        U.axis_overrides(book, {**axes[0], "kind": "wobble"}, 0.3)


@pytest.mark.tact
def test_governance_axis_keeps_channels_summing_to_the_discount():
    book, _ = book_and_facts()
    for new in (0.0, 0.03, 0.1):
        b2 = U.trial_book(book, {"valuation.governance.discount": new})
        comps = b2.get("valuation.governance.components")
        assert sum(float(c["sign"]) * float(c["value"]) for c in comps) == pytest.approx(new, abs=1e-12)
        assert float(b2.get("valuation.governance.discount")) == pytest.approx(new, abs=1e-15)


@pytest.mark.tact
def test_axis_keys_collapse_parallel_paths():
    assert U.axis_key({"paths": ["regimes.soft.cor.LT", "regimes.norm.cor.LT"]}) == "regimes.*.cor.LT"
    assert U.axis_key({"paths": ["valuation.erp"]}) == "valuation.erp"
    assert U.axis_key({"paths": ["worlds.M.cpi", "worlds.M.lt_inflation"]}) == "worlds.M.*"


# ------------------------------------------------------------------ быстрая выборка полосы


@pytest.fixture(scope="module")
def small_band():
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    return book, facts, live, U.band(book, facts, live, draws=10, workers=1)


@pytest.mark.tact
def test_fast_band_sample_is_deterministic_and_rounded(small_band):
    book, facts, live, band = small_band
    assert band.draws == 10 and len(band.low_draws) == len(band.high_draws) == 10 == len(band.s)
    for v in band.low_draws + band.high_draws:
        assert v == U.round_half_up(v, 0.1)
    again = U.band(book, facts, live, draws=10, workers=1)
    assert again.low_draws == band.low_draws and again.high_draws == band.high_draws   # бит в бит
    assert band.axes == tuple(book.get("valuation.uncertainty.axes"))


@pytest.mark.tact
def test_band_draw_is_the_grid_on_the_changed_book(small_band):
    """Прогон i — та же сетка и то же отображение, что у печатаемого числа (М§10)."""
    book, facts, live, band = small_band
    s = band.s[3]
    ov = {}
    for ax, sj in zip(band.axes, s):
        ov.update(U.axis_overrides(book, ax, sj))
    run = run_grid(U.trial_book(book, ov), facts, live)
    assert band.low_draws[3] == U.round_half_up(run.low, 0.1)
    assert band.high_draws[3] == U.round_half_up(run.high, 0.1)


@pytest.mark.tact
def test_headline_reproduces_from_draws(small_band):
    book, _, live, band = small_band
    main = str(book.get("meta.company.main_ticker"))
    h = band.headline(band.lam, live.prices, main)
    c = sorted(lo + band.lam * (hi - lo) for lo, hi in zip(band.low_draws, band.high_draws))
    assert h["median"] == U.quantile(c, 0.5) and h["band80"] == [U.quantile(c, 0.1), U.quantile(c, 0.9)]
    assert h["printed_median"] == U.round_half_up(h["median"], band.print_step)
    assert h["p_below_market"] == sum(1 for v in c if v < live.prices[main]) / len(c)
    assert set(h["upside"]) == set(live.prices)


@pytest.mark.tact
def test_band_contributions_skip_dict_axes(small_band):
    _, _, _, band = small_band
    rows = band.contributions(band.lam)
    kinds = {k: ax["kind"] for k, ax in zip(band.keys, band.axes)}
    assert all(kinds[r["axis"]] != "dict" for r in rows)
    assert sum(r["share"] for r in rows) == pytest.approx(1.0)
    assert all(r["judgement_key"] == r["axis"] for r in rows)


def test_median_anchor_sees_exactly_the_printed_median_at_book_values(small_band):
    book, facts, live, band = small_band
    anchor = U.MedianAnchor(book, facts, live, band, n=6)
    m = anchor.at(book)
    assert m["central"] == pytest.approx(band.medians(band.lam)["central"], abs=1e-12)
    shifted = U.trial_book(book, {"valuation.erp": float(book.get("valuation.erp")) + 0.01})
    assert anchor.at(shifted)["central"] < m["central"]                   # дороже капитал — ниже медиана


@pytest.mark.tact
def test_workers_env(monkeypatch):
    """Переменная без префикса банка (М§0.6, INTERFACES §8): копия ядра под другой банк её не меняет."""
    assert U.WORKERS_ENV == "BANK_WORKERS"
    monkeypatch.setenv("BANK_WORKERS", "3")
    assert U.workers() == 3
    monkeypatch.setenv("BANK_WORKERS", "auto")
    assert 1 <= U.workers() <= U.MAX_WORKERS
    monkeypatch.setenv("BANK_WORKERS", "zero")
    with pytest.raises(ValueError):
        U.workers()


@pytest.mark.ci_only
def test_pool_gives_bit_identical_band(parallel_band):
    """Пул процессов склеивает куски по порядку точек: числа те же бит в бит."""
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    serial = U.band(book, facts, live, draws=40, workers=1)
    with parallel_band():
        pooled = U.band(book, facts, live, draws=40, workers=3)
    assert pooled.low_draws == serial.low_draws and pooled.high_draws == serial.high_draws


def test_judgements_price_at_axis_ends():
    book, facts = book_and_facts()
    book = small_book(book)
    live = live_from_book(book, facts)
    band = U.band(book, facts, live, draws=6, workers=1)
    rows = U.judgements(book, facts, live, band)
    off = U.off_band_axes(book)
    assert len(rows) == len(band.axes) + len(off)
    assert [r["swing"] for r in rows] == sorted((r["swing"] for r in rows), reverse=True)
    for ax in off:                        # ось вне полосы: цена ошибки есть, вклада нет (М§10)
        row = next(r for r in rows if r["paths"] == list(ax["paths"]))
        assert row["in_band"] is False and row["share"] is None and row["rank_corr"] is None
        assert row["price_low"] is not None and row["price_high"] is not None
    assert all(r["in_band"] for r in rows if r["paths"] not in [list(a["paths"]) for a in off])
    erp = next(r for r in rows if r["paths"] == ["valuation.erp"])
    b2 = U.trial_book(book, {"valuation.erp": erp["high"]})
    assert erp["price_high"] == pytest.approx(run_grid(b2, facts, live).point, abs=1e-9)
    assert erp["price_high"] < erp["price_low"]
