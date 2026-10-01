"""Миры: полугодия, надстройка со сходом к g_T, дисконт, однородность миров (М§0.5, §3.1, §14.2)."""

from __future__ import annotations

import pytest

from model.checks import check_gates
from model.grid import make_context
from model.timeline import make_clock, make_timeline
from model.worlds import ZeroCurve, discount, g_terminal, world_path
from tests.support_core import fixture_book, fixture_facts, fixture_run

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def test_quarter_reads_its_half_year():
    book = fixture_book()
    tl = make_timeline(book)
    wp = world_path(book, "N", tl)
    key = book.get("worlds.N.key_rate")
    for q in range(1, tl.Q + 1):
        y, n = tl.year(q), tl.h(q)
        assert wp.key[q] == key[f"{y}H{1 if n <= 2 else 2}"]
    assert wp.price_index[0] == 1.0
    assert wp.price_index[4] == pytest.approx(
        (1 + wp.cpi[1]) ** 0.25 * (1 + wp.cpi[2]) ** 0.25 * (1 + wp.cpi[3]) ** 0.25 * (1 + wp.cpi[4]) ** 0.25)


def test_overlay_converges_to_terminal_growth():
    book = fixture_book()
    tl = make_timeline(book)
    lt_from = int(book.get("volumes.lt_from"))
    for w in book.get("worlds.ids"):
        wp = world_path(book, w, tl)
        g = g_terminal(book, w)
        for sector, table in wp.credit_growth.items():
            assert table[lt_from] == pytest.approx(g)
            assert table[tl.last_year] == pytest.approx(g)
            last = max(int(y) for y in book.get(f"worlds_bank.{w}.credit_growth.{sector}"))
            assert table[last] == pytest.approx(float(book.get(f"worlds_bank.{w}.credit_growth.{sector}.{last}")))


def test_zero_curve_interpolation_and_extrapolation():
    c = ZeroCurve({"1": 0.10, "3": 0.12, "5": 0.13, "10": 0.14, "LT": 0.09}, 0.0)
    assert c.z(0.5) == 0.10 and c.z(2.0) == pytest.approx(0.11)
    assert (1 + c.z(15)) ** 15 == pytest.approx(1.14 ** 10 * 1.09 ** 5)
    assert c.df(0) == 1.0


def test_capital_charge_is_consistent_with_discount_factors():
    book = fixture_book()
    tl = make_timeline(book)
    clock = make_clock(book, tl, make_timeline(book).end(1))
    d = discount(book, "H", clock)
    for q in range(clock.q0 + 1, tl.Q + 1):
        assert d.k[q] == pytest.approx(d.dfq[q - 1] / d.dfq[q] - 1, rel=1e-12)
    assert d.k[clock.q0] == pytest.approx(1 / d.dfq[clock.q0] - 1)
    assert d.dfq[clock.q0] == pytest.approx(d.df(clock.tau[clock.q0]))
    assert d.k_t == pytest.approx(d.df(clock.tau[tl.Q]) / d.df(clock.tau[tl.Q] + 1) - 1)


def test_homogeneity_of_worlds_within_book_corridor():
    """ROE'_T − k_T по мирам: разрыв средних не больше `checks.roe_k_spread.max_world_gap`."""
    run = fixture_run()
    gate = {f.name: f for f in check_gates(run)}["roe_k_homogeneity"]
    gap = gate.detail["gap"]
    assert gap <= float(run.ctx.book.get("checks.roe_k_spread.max_world_gap")), gate.message
    assert set(gate.detail["by_world"]) == set(run.ctx.book.get("worlds.ids"))


def test_worlds_share_regime_probabilities():
    ctx = make_context(fixture_book(), fixture_facts())
    assert set(ctx.posterior) == set(fixture_book().get("regimes.ids"))
