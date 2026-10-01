"""Мост объявленных дивидендов и скачок на экс-дату = −DPS (М§8.1, инвариант exdate_jump)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from model.grid import DividendRecord, LiveInputs, make_context, price_from_v0, run_grid
from model.timeline import make_timeline
from tests.support_core import fixture_book, fixture_facts

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def _live(book, v, register):
    prices = {str(t): float(p) for t, p in book.get("meta.market_price").items()}
    return LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=register)


def _record(year, dps, ex, record=None):
    return DividendRecord(year=year, dps=dps, status="declared", record_date=record or ex,
                          last_buy_date=ex - timedelta(days=1), ex_date=ex, pay_date=ex + timedelta(days=15),
                          sources=("тест",))


def test_exdate_jump_is_minus_dps():
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    year = tl.anchor_year
    agm_q = tl.index(f"{year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    v = tl.end(agm_q) + timedelta(days=20)                    # после ГОСА модели, в квартале отсечки
    dps = 44.5
    on_ex = run_grid(book, facts, _live(book, v, (_record(year, dps, v),)))
    before = run_grid(book, facts, _live(book, v, (_record(year, dps, v + timedelta(days=1)),)))
    tol = float(book.get("checks.exdate_jump_tol"))
    for a, b in ((on_ex.point, before.point), (on_ex.low, before.low), (on_ex.high, before.high)):
        assert abs((a - b) - (-dps)) <= tol * dps
        assert a - b == pytest.approx(-dps, abs=1e-9)          # мост без дисконта за управление — ровно
    assert on_ex.bridge_amount == 0.0
    assert before.bridge_amount == pytest.approx(dps * on_ex.shares_out / 1000)
    # V клеток не зависит от экс-даты: объявленный DPS закрыл год во всех клетках
    assert [c.v_ri for c in on_ex.cells] == [c.v_ri for c in before.cells]


def test_bridge_sign_when_exdate_precedes_model_deduction():
    """Экс-дата раньше модельного вычета (ГОСА и отсечка в одном квартале) — строка −DPS × N_out."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    year = tl.anchor_year
    agm_q = tl.index(f"{year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    v = tl.end(agm_q) - timedelta(days=5)
    ctx = make_context(book, facts, _live(book, v, (_record(year, 40.0, v - timedelta(days=3)),)))
    from model.grid import bridge_rows
    rows = bridge_rows(ctx)
    assert rows[0]["sign"] == -1


def test_governance_not_applied_to_bridge():
    assert price_from_v0(1000.0, 100.0, 0.1, 10.0) == pytest.approx((900.0 + 100.0) * 100)
    assert price_from_v0(-50.0, 100.0, 0.1, 10.0) == pytest.approx(50.0 * 100)   # V0 ≤ 0 — без дисконта
    with_discount = run_grid(fixture_book().with_overrides({
        "valuation.governance.discount": 0.05,
        "valuation.governance.components": [{"id": "x", "name": "x", "value": 0.05, "sign": 1, "basis": "тест"}],
    }), fixture_facts())
    assert with_discount.governance == 0.05
    lay = with_discount.layers["analytical"]
    assert lay.price == pytest.approx(lay.v0 * 0.95 * 1000 / with_discount.shares_out)


def test_recommended_record_is_not_in_bridge():
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    v = tl.end(4) + timedelta(days=20)
    rec = DividendRecord(year=tl.anchor_year, dps=40.0, status="recommended", record_date=None,
                         last_buy_date=None, ex_date=None, pay_date=None, sources=("тест",))
    run = run_grid(book, facts, _live(book, v, (rec,)))
    assert run.bridge_amount == 0.0 and not run.bridge_rows
