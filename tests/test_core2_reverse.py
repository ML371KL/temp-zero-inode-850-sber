"""Обратный расчёт медианы (М§11): решение строки возвращает рынок; банковские строки; поиск корня."""

from __future__ import annotations

import math

import pytest

from model import uncertainty as U
from model.grid import LiveInputs, bank_language, live_from_book, market_cap, run_grid
from model.reverse import bisect_point, bracketed, reverse_dcf, reverse_overrides, reverse_trial, solve_outward
from tests.support_core2 import book_and_facts, small_book


@pytest.mark.tact
def test_bisection_and_secant_find_roots():
    root, gap = bisect_point(lambda x: x * x - 2, 0.0, 2.0, tol=1e-9)
    assert root == pytest.approx(math.sqrt(2), abs=1e-8) and abs(gap) <= 1e-9
    assert bisect_point(lambda x: x * x + 1, 0.0, 2.0, tol=1e-9) == (None, None)   # нет смены знака
    g = lambda x: 3 * x - 1                                                          # noqa: E731
    root, used = solve_outward(g, 0.0, g(0.0), 0.1, 5.0, tol=1e-9)
    assert root == pytest.approx(1 / 3, abs=1e-9) and used <= 12
    assert solve_outward(lambda x: x + 10, 0.0, 10.0, 1.0, 5.0, tol=1e-9)[0] is None  # недостижимо до конца
    root, _, ok = bracketed(lambda x: math.exp(x) - 3, 0.0, -2.0, 2.0, math.exp(2) - 3, None, None, 20, 1e-10)
    assert ok and root == pytest.approx(math.log(3), abs=1e-9)


@pytest.mark.tact
def test_mix_axis_blends_regulatory_rows_toward_strict():
    book, _ = book_and_facts()
    ax = next(a for a in book.get("valuation.reverse_dcf.axes") if a["kind"] == "mix")
    table = reverse_overrides(book, ax, 1.0)[ax["paths"][0]]
    for row in table.values():
        assert row == pytest.approx({s: float(v) for s, v in ax["toward"].items()})
    half = reverse_overrides(book, ax, 0.5)[ax["paths"][0]]
    for r, row in half.items():
        assert sum(row.values()) == pytest.approx(1.0)


def test_follow_center_moves_the_band_axis_end():
    """Строка обратного расчёта на оси полосы (вида value) двигает конец этой оси за решением (follow_center)."""
    book, _ = book_and_facts()
    band = {tuple(a["paths"]): a for a in book.get("valuation.uncertainty.axes") if a["kind"] == "value"}
    ax = next(a for a in book.get("valuation.reverse_dcf.axes") if a["kind"] == "value" and tuple(a["paths"]) in band)
    x = float(band[tuple(ax["paths"])]["high"]) * 1.5
    b2 = reverse_trial(book, ax, x, follow=True)
    band_ax = next(a for a in b2.get("valuation.uncertainty.axes") if a["paths"] == ax["paths"])
    assert band_ax["high"] == x and float(b2.get(ax["paths"][0])) == x


@pytest.fixture(scope="module")
def solved():
    """Малая книга, две строки (ЧПМ, ERP); рынок — медиана полосы − 15 ₽ (достижимо)."""
    book, facts = book_and_facts()
    axes = [a for a in book.get("valuation.reverse_dcf.axes")
            if a["paths"] in (["nii.nim_lt_target_mgmt"], ["valuation.erp"])]
    book = small_book(book, **{"valuation.reverse_dcf.axes": axes})
    live0 = live_from_book(book, facts)
    band = U.band(book, facts, live0, draws=8, workers=1)
    main = str(book.get("meta.company.main_ticker"))
    target = band.medians(band.lam)["central"] - 15.0
    prices = dict(live0.prices)
    prices[main] = target
    live = LiveInputs(valuation_date=live0.valuation_date, prices=prices, price_dates=dict(live0.price_dates),
                      register=live0.register)
    band = U.band(book, facts, live, draws=8, workers=1)
    anchor = U.MedianAnchor(book, facts, live, band)
    res = reverse_dcf(book, facts, live, band, anchor=anchor)
    return book, facts, live, band, anchor, res, target


def test_reverse_median_returns_the_market(solved):
    book, facts, live, band, anchor, res, target = solved
    tol = float(book.get("valuation.headline.search_tol_rub"))
    assert res["target"] == target and res["number"] == "median"
    for row in res["rows"]:
        assert row["status"] == "solved", row
        ax = next(a for a in book.get("valuation.reverse_dcf.axes") if U.axis_key(a) == row["key"])
        trial = reverse_trial(book, ax, row["solved"], follow=True)
        if row["gap_basis"] == "full":
            median = U.band(trial, facts, live, draws=band.draws, workers=1).medians(band.lam)["central"]
        else:
            median = anchor.at(trial)["central"]
        assert median - target == pytest.approx(row["gap"], abs=1e-9)
        assert abs(row["gap"]) <= tol + 1e-9
        # точечное решение (для справки): точка при нём = рынок
        trial_p = reverse_trial(book, ax, row["point_solved"], follow=True)
        assert abs(run_grid(trial_p, facts, live).point - target) <= tol + 1e-9
        assert row["delta"] == pytest.approx(row["solved"] - row["book"])


def test_market_below_median_needs_dearer_capital_and_lower_margin(solved):
    book, _, _, _, _, res, _ = solved
    rows = {r["key"]: r for r in res["rows"]}
    assert rows["valuation.erp"]["solved"] > float(book.get("valuation.erp"))
    assert rows["nii.nim_lt_target_mgmt"]["solved"] < float(book.get("nii.nim_lt_target_mgmt"))
    bank = {r["key"]: r for r in res["bank_rows"]}
    assert bank["implied_cost_of_equity"]["implied"] > bank["implied_cost_of_equity"]["book"]
    assert set(bank["implied_cost_of_equity"]["by_world"]) == set(book.get("worlds.ids"))
    assert bank["implied_roe_through_cycle"]["implied"] < bank["implied_roe_through_cycle"]["book"]


def test_market_cap_minus_bv_row(solved):
    book, facts, live, _, _, res, _ = solved
    run = run_grid(book, facts, live)
    bl = bank_language(run)
    row = next(r for r in res["bank_rows"] if r["key"] == "market_cap_minus_bv")
    assert row["implied"] == pytest.approx(market_cap(run) - bl["bv_v"])
    assert row["book"] == pytest.approx(bl["v_point"] - bl["bv_v"])
