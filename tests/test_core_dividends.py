"""Дивиденды: DPS истории до копейки, решение года, кризис, ε (М§5, прил. C)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from model.cell import run_cell
from model.dividends import Checkpoint, Policy, Step, decide, dps_formula, dps_history
from model.grid import make_context
from model.paths import Trajectory
from tests.support_core import fixture_book, fixture_facts, fixture_run, real_book_or_skip

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def test_dps_history_to_the_kopeck_on_fixture_facts():
    rows = dps_history(fixture_book(), fixture_facts())
    years = {r["year"] for r in rows}
    assert {2023, 2024, 2025} <= years
    for r in rows:
        assert r["ok"], r
        assert Decimal(str(r["dps_rounded"])) == Decimal(str(r["dps_declared"]))


def test_dps_history_on_real_facts():
    book, facts = real_book_or_skip()
    for r in dps_history(book, facts):
        assert r["ok"], r


def test_ceil_kopeck_versus_half_up_and_statutory_rate():
    """Прил. C: «вверх до копейки» даёт 34,84; с τ = 25 % за 2024 вышло бы ≠ 34,84."""
    facts = fixture_facts()
    hist = {int(r["year"]): i for i, r in enumerate(facts.file("dividends")["history"])}
    i = hist[2024]
    ni = facts.need("dividends", f"history.{i}.ni_shareholders")
    cpn = facts.need("dividends", f"history.{i}.at1_coupon")
    n_iss = facts.need("shares", "issued_total")
    declared = Decimal(str(facts.need("dividends", f"history.{i}.dps_ordinary")))
    ok = dps_formula(ni_shareholders=ni, at1_coupon=cpn, tax_statutory=facts.need("dividends", f"history.{i}.tax_statutory"),
                     payout=0.5, n_issued=n_iss, rounding="ceil_kopeck")
    assert ok["dps_rounded"] == declared
    wrong = dps_formula(ni_shareholders=ni, at1_coupon=cpn, tax_statutory=0.25, payout=0.5,
                        n_issued=n_iss, rounding="ceil_kopeck")
    assert wrong["dps_rounded"] != declared


def _policy(**kw) -> Policy:
    base = dict(steps=(Step(Trajectory(0.5), 0.133),), deviation=Trajectory(0.0), deduct_at1_after_tax=True,
                epsilon=0.5, excess_from=2030, ramp_years=1, skip_in_shock=True, catch_up=True, agm_quarter=2,
                reg_quarter=3, pay_quarter=3, checkpoints=(3, 4), n_iss=1000.0, n_out=900.0)
    base.update(kw)
    return Policy(**base)


def _points(h_rub: float, rwa: float = 10000.0) -> list[Checkpoint]:
    # запас Н20.0 = h_rub при требовании 0,133 и Н20.1 с большим запасом
    return [Checkpoint(n20=0.133 + h_rub / rwa, n11=0.5, rwa=rwa, floor20=0.1, req11=0.07)]


def test_full_pool_when_capital_allows():
    pol = _policy()
    d = decide(pol, year=2027, base=2000.0, points=_points(5000.0), buffer20=0.013, deferred=0.0,
               shock=False, declared_dps=None)
    assert d.want[0] == pytest.approx(0.5 * 2000.0 * 900 / 1000)
    assert d.div == pytest.approx(d.want[0]) and not d.cut
    assert d.dps == pytest.approx(d.div * 1000 / 900)


def test_shortfall_pays_residual_above_requirement():
    pol = _policy()
    d = decide(pol, year=2027, base=2000.0, points=_points(300.0), buffer20=0.013, deferred=0.0,
               shock=False, declared_dps=None)
    assert d.div == pytest.approx(300.0) and d.cut and "dividend_cut" in d.flags
    d = decide(pol, year=2027, base=2000.0, points=_points(-50.0), buffer20=0.013, deferred=0.0,
               shock=False, declared_dps=None)
    assert d.div == 0.0                                        # отрицательного дивиденда нет


def test_steps_choose_first_affordable():
    pol = _policy(steps=(Step(Trajectory(0.5), 0.133), Step(Trajectory(0.3), 0.12)))
    # запас на пороге 0,133 — 500; на пороге 0,12 — 630
    d = decide(pol, year=2027, base=2000.0, points=_points(500.0), buffer20=0.0, deferred=0.0,
               shock=False, declared_dps=None)
    assert d.step == 1 and d.div == pytest.approx(0.3 * 2000.0 * 0.9)


def test_crisis_skip_then_catch_up_and_excess():
    pol = _policy(excess_from=2028)
    skip = decide(pol, year=2026, base=2000.0, points=[], buffer20=0.013, deferred=0.0, shock=True,
                  declared_dps=None)
    assert skip.div == 0.0 and "crisis_skip" in skip.flags
    assert skip.cut is False and "dividend_cut" not in skip.flags      # отмена кризисом — не урезание капиталом
    assert skip.deferred_after == pytest.approx(skip.want[0])
    nxt = decide(pol, year=2027, base=1000.0, points=_points(2000.0), buffer20=0.013,
                 deferred=skip.deferred_after, shock=False, declared_dps=None)
    assert nxt.catch == pytest.approx(skip.want[0]) and nxt.deferred_after == pytest.approx(0.0)
    assert nxt.excess == 0.0                                   # год прибыли до from_profit_year
    later = decide(pol, year=2028, base=1000.0, points=_points(2000.0), buffer20=0.013, deferred=0.0,
                   shock=False, declared_dps=None)
    assert later.excess == pytest.approx(0.5 * (2000.0 - later.base_div))


def test_register_overrides_without_checks():
    pol = _policy()
    d = decide(pol, year=2027, base=2000.0, points=_points(-500.0), buffer20=0.013, deferred=10.0,
               shock=True, declared_dps=40.0)
    assert d.source == "register" and d.div == pytest.approx(40.0 * 900 / 1000)
    assert d.deferred_after == 10.0


def test_crisis_cell_skips_shock_year_and_catches_up():
    run = fixture_run()
    shock = run.ctx.timeline.anchor_year + int(run.ctx.book.get("regimes.crisis.shock_year_offset"))
    assert run.ctx.prep.regimes["crisis"].shock_year == shock
    c = run.cell("N", "crisis", "strict")
    decs = {d.year: d for d in c.decisions}
    assert decs[shock - 1].source == "crisis_skip" and "crisis_skip" in c.flags
    assert decs[shock].catch > 0
    normal = run.cell("N", "norm", "strict")
    assert all(d.source == "model" for d in normal.decisions)


def test_epsilon_one_holds_ratio_at_requirement():
    """ε = 1 — капиталонейтральная выплата: в точке проверки норматив ≈ требованию.

    Второй порядок пробного прохода (М§5.3 п. 7) — проценты на удержанные деньги и
    RWA ликвидных активов, ушедших на выплату: ≈0,05 п.п., допуск 0,1 п.п.
    """
    book, facts = fixture_book(), fixture_facts()
    ay = make_context(book, facts).timeline.anchor_year
    b = book.with_overrides({"dividends.excess.epsilon": 1.0, "dividends.excess.from_profit_year": ay + 1,
                             "dividends.excess.ramp_years": 1})
    ctx = make_context(b, facts)
    c = run_cell(ctx, "H", "norm", "schedule")
    tl = ctx.timeline
    for d in c.decisions:
        if d.source != "model" or d.year < ay + 1 or d.year + 1 > tl.last_year:
            continue
        gaps = []
        for h in b.get("dividends.calendar.checkpoints"):
            q = tl.index(f"{d.year + 1}Q{h}")
            gaps.append(min(c.quarters["n20"][q] - c.quarters["req20"][q],
                            c.quarters["n11"][q] - c.quarters["req11"][q]))
        assert min(gaps) == pytest.approx(0.0, abs=1e-3), (d.year, gaps)
