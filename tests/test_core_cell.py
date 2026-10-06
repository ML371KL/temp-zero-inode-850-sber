"""Клетка: DDM = RI в каждой клетке, тождества, якорь, резервы, капитал (М§4, §6, §18)."""

from __future__ import annotations

import math

import pytest

from model.capital import CapitalConst, n11_audited, ratios
from model.cell import run_cell
from model.checks import check_invariants
from model.credit import kappa_addon
from model.grid import make_context, sensitivity_overrides
from model.valuation import terminal, value_cell
from model.worlds import BASE_WORLD, Discount, ZeroCurve
from tests.support_core import fixture_book, fixture_facts, fixture_run

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def test_ddm_equals_ri_in_every_cell():
    run = fixture_run()
    tol = float(run.ctx.book.get("checks.ddm_ri_tol"))
    assert len(run.cells) == 36
    for c in run.cells:
        assert abs(c.v_ddm - c.v_ri) <= tol * abs(c.v_ri), c.label
        # ожидаемая разность — машинная точность, допуск книги ошибку не «чинит»
        assert abs(c.v_ddm - c.v_ri) <= 1e-9 * abs(c.v_ri), c.label


def test_identities_hold_in_every_quarter():
    found = {f.name: f for f in check_invariants(fixture_run())}
    for name in ("bv_identity", "balance_identity", "pnl_identity", "ddm_equals_ri", "probabilities",
                 "transmission_solved", "dividend_bounds", "governance_sum", "book_schema", "dps_history"):
        assert not found[name].fired, (name, found[name].message)


def test_bv_identity_explicitly():
    c = fixture_run().cell("H", "norm", "schedule")
    q_ = c.quarters
    for q in range(1, len(q_["bv"])):
        rhs = (q_["bv"][q - 1] + q_["ni_sh"][q] - q_["coupon"][q] * (1 - q_["tau_stat"][q]) + q_["oci"][q]
               + q_["om"][q] - q_["div"][q])
        assert q_["bv"][q] == pytest.approx(rhs, rel=1e-12)
        assert q_["ci"][q] == pytest.approx(q_["ni_sh"][q] - q_["coupon"][q] * (1 - q_["tau_stat"][q])
                                            + q_["oci"][q] + q_["om"][q], rel=1e-12, abs=1e-9)


def test_anchor_reproduces_reported_ratios():
    """На якоре Н20.0 (до вычета дивиденда) и Н1.1 банка воспроизводят факты (условие калибровки)."""
    run = fixture_run()
    facts = fixture_facts()
    c = run.cells[0]
    assert c.quarters["n20"][0] == pytest.approx(facts.need("capital", "n20_0.value"), abs=5e-4)
    assert c.quarters["n11"][0] == pytest.approx(facts.need("capital", "n1_1_bank.value"), abs=5e-4)
    assert c.quarters["bv"][0] == facts.need("balance", "equity.bv_common")


def test_kappa_addon_is_zero_in_world_n():
    ctx = make_context(fixture_book(), fixture_facts())
    p = ctx.prep
    Q = ctx.timeline.Q
    base = ctx.worlds[BASE_WORLD].real_key
    for w, wp in ctx.worlds.items():
        add = kappa_addon(p.kappa, p.lag, wp.real_key, base, Q)
        if w == BASE_WORLD:
            assert all(x == 0.0 for x in add)
        assert all(x >= 0.0 for x in add)
        assert all(x == 0.0 for x in add[:p.lag + 1])             # история до сетки — добавка ноль


def test_cor_shift_moves_llp_exactly():
    """Сдвиг CoR на x ⇒ LLP += x × Ē^АС × d/365 точно (М§18)."""
    book, facts = fixture_book(), fixture_facts()
    x = 0.001
    shifted = book.with_overrides(sensitivity_overrides(book, "cor", x))
    a = run_cell(make_context(book, facts), "M", "downturn", "strict")
    b = run_cell(make_context(shifted, facts), "M", "downturn", "strict")
    tl = make_context(book, facts).timeline
    for q in range(1, tl.Q + 1):
        assert b.quarters["loans_ac_avg"][q] == pytest.approx(a.quarters["loans_ac_avg"][q], rel=1e-12)
        expected = x * a.quarters["loans_ac_avg"][q] * tl.d(q) / 365
        assert b.quarters["llp"][q] - a.quarters["llp"][q] == pytest.approx(expected, rel=1e-9)
    assert b.v_ri < a.v_ri


def test_dividend_lowers_k20_and_k11_one_for_one():
    """Дивиденд снижает K20 и K11 ровно на Div (аддитивные вычеты, М§4.11)."""
    c = CapitalConst(ded20_0=900.0, ded11_0=1100.0, rwa0=60000.0, fvoci_recognition=0.8, gap20=-0.001,
                     gap11=0.0, at1=150.0, audit_cutoffs=(3, 4), buffer20=0.01, buffer11=0.0)
    kw = dict(dpreg=0.0, reserve=-300.0, unaudited=500.0, t2=400.0, rwa=65000.0, ded_pp20=0.004, ded_pp11=0.004)
    a = ratios(c, bv=9000.0, **kw)
    b = ratios(c, bv=9000.0 - 777.0, **kw)
    assert a.k20 - b.k20 == pytest.approx(777.0, rel=1e-12)
    assert a.k11 - b.k11 == pytest.approx(777.0, rel=1e-12)
    assert a.ded20 == pytest.approx(900.0 * 65000 / 60000)        # вычеты растут с RWA


def test_unaudited_loss_stays_in_base_capital():
    """Из K11 исключается только неаудированная прибыль; убыток периода уменьшает базовый капитал сразу,
    вместе с BV, а N11* возвращает в капитал ровно исключённое (М§4.11, §7)."""
    c = CapitalConst(ded20_0=0.0, ded11_0=0.0, rwa0=1000.0, fvoci_recognition=1.0, gap20=0.0, gap11=0.0,
                     at1=0.0, audit_cutoffs=(3, 4), buffer20=0.0, buffer11=0.0)
    kw = dict(dpreg=0.0, reserve=0.0, t2=0.0, rwa=1000.0, ded_pp20=0.0, ded_pp11=0.0)
    flat = ratios(c, bv=200.0, unaudited=0.0, **kw)
    loss = ratios(c, bv=100.0, unaudited=-100.0, **kw)          # убыток 100: BV 200 → 100
    gain = ratios(c, bv=300.0, unaudited=100.0, **kw)           # прибыль 100: BV 200 → 300
    assert flat.k11 - loss.k11 == pytest.approx(100.0, rel=1e-12)
    assert gain.k11 == pytest.approx(flat.k11, rel=1e-12)
    assert flat.k20 - loss.k20 == pytest.approx(100.0, rel=1e-12)
    assert gain.k20 - flat.k20 == pytest.approx(100.0, rel=1e-12)
    assert n11_audited(loss.k11, -100.0, 1000.0, 0.0, 0.0) == pytest.approx(0.1, rel=1e-12)
    assert n11_audited(gain.k11, 100.0, 1000.0, 0.0, 0.0) == pytest.approx(0.3, rel=1e-12)


def test_crisis_loss_quarter_lowers_k11_in_the_grid():
    """В клетках кризиса есть квартал с убытком: K11 = BVreg − Ded11 (E < 0 не возвращается), в прочих
    кварталах K11 = BVreg − E − Ded11 (М§4.11)."""
    book, facts = fixture_book(), fixture_facts()
    f = float(book.get("capital.n20.fvoci_recognition"))
    ctx = make_context(book, facts)
    c = run_cell(ctx, "M", "crisis", "strict")
    q_ = c.quarters
    losses = 0
    for q in range(1, ctx.timeline.Q + 1):
        e = q_["e_unaudited"][q]
        bvreg = q_["bv"][q] + q_["dpreg"][q] - (1 - f) * q_["reserve"][q]
        assert q_["k11"][q] == pytest.approx(bvreg - max(e, 0.0) - q_["ded11"][q], rel=1e-12), q
        losses += e < 0
    assert losses > 0


def test_terminal_n11_star_is_the_audited_ratio_of_the_last_quarter(monkeypatch):
    """N11* терминала считает `n11_audited` на величинах последнего квартала — K11, неаудированный результат,
    RWA; знак результата разбирает функция (убыток в капитал не возвращается — тест выше), М§7."""
    from model import cell as cell_module
    book, facts = fixture_book(), fixture_facts()
    ctx = make_context(book, facts)
    seen = []

    def spy(k11, unaudited, rwa, gap11, ded_pp11):
        seen.append((k11, unaudited, rwa))
        return n11_audited(k11, unaudited, rwa, gap11, ded_pp11)

    monkeypatch.setattr(cell_module, "n11_audited", spy)
    c = run_cell(ctx, "M", "crisis", "strict")
    Q, q_ = ctx.timeline.Q, c.quarters
    assert seen == [(q_["k11"][Q], q_["e_unaudited"][Q], q_["rwa"][Q])]
    assert c.n11_star == pytest.approx(q_["n11"][Q] + max(q_["e_unaudited"][Q], 0.0) / q_["rwa"][Q], rel=1e-12)


def test_agm_quarter_is_neutral_for_regulatory_capital():
    """В квартал ГОСА дивиденд вычтен из BV, но ещё не из регуляторного капитала (DPreg): K20 и K11 не зависят от суммы."""
    from model.grid import DividendRecord, LiveInputs
    book, facts = fixture_book(), fixture_facts()
    ctx0 = make_context(book, facts)
    tl = ctx0.timeline
    year = tl.anchor_year
    agm_q = tl.index(f"{year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    out = []
    for dps in (40.0, 55.0):
        rec = DividendRecord(year=year, dps=dps, status="declared", record_date=None, last_buy_date=None,
                             ex_date=None, pay_date=None, sources=("тест",))
        live = LiveInputs(valuation_date=ctx0.live.valuation_date, prices=ctx0.live.prices,
                          price_dates=ctx0.live.price_dates, register=(rec,))
        out.append(run_cell(make_context(book, facts, live), "H", "norm", "schedule"))
    a, b = out
    assert a.quarters["bv"][agm_q] - b.quarters["bv"][agm_q] == pytest.approx(
        15.0 * fixture_facts().need("shares", "outstanding_total") / 1000, rel=1e-9)
    assert a.quarters["k20"][agm_q] == pytest.approx(b.quarters["k20"][agm_q], rel=1e-12)
    assert a.quarters["k11"][agm_q] == pytest.approx(b.quarters["k11"][agm_q], rel=1e-12)
    assert a.dps[year] == 40.0 and b.dps[year] == 55.0


def test_flat_world_values_equity_at_book():
    """Плоский мир: ROE = k каждый квартал и в терминале ⇒ V = BV_v (М§17, §18)."""
    curve = ZeroCurve({"1": 0.1, "3": 0.1, "5": 0.1, "10": 0.1, "LT": 0.1}, 0.0)
    Q, q0, e = 12, 1, 0.3
    tau = [0.0] + [0.25 * ((1 - e) + (q - q0)) for q in range(1, Q + 1)]
    dfq = [1.0] + [curve.df(t) for t in tau[1:]]
    k = [0.0] + [((dfq[q - 1] if q > q0 else 1.0) / dfq[q]) - 1 for q in range(1, Q + 1)]
    k_t = curve.df(tau[Q]) / curve.df(tau[Q] + 1) - 1
    disc = Discount(k=tuple(k), k_t=k_t, dfq=tuple(dfq), curve=curve, roll=0.0)
    bv, ci, div = [1000.0], [0.0], [0.0]
    for q in range(1, Q + 1):
        # RI_q0 = (1 − e) CI − k0 (BV_0 + e CI) = 0;  RI_q = CI − k_q BV_(q−1) = 0
        inc = k[q] * bv[0] / (1 - e - e * k[q]) if q == q0 else k[q] * bv[-1]
        d = 0.4 * inc if q % 4 == 2 else 0.0
        ci.append(inc)
        div.append(d)
        bv.append(bv[-1] + inc - d)
    kw = dict(n20=0.15, req20=0.15, n11_star=0.12, req11=0.12, rwa=10000.0, bv_q=bv[-1],
              y_balancing=0.05, c_wholesale=0.09, headroom=500.0, tau_eff=0.2, nci_share=0.0, coupon_annual=0.0,
              tau_stat=0.2,
              lt_inflation=0.04, real_growth=0.01, fade=1.0, multiple=1.0, k_t=k_t)
    g = (1 + 0.04) * (1 + 0.01) - 1
    term = terminal(pbt_last_year=k_t * bv[-1] / (1 - 0.2) / (1 + g), **kw)
    assert term.roe_t == pytest.approx(k_t, rel=1e-12) and term.x_t == 0
    val = value_cell(q0=q0, elapsed=e, disc=disc, bv=bv, ci=ci, div=div, term=term)
    assert val.v_ri == pytest.approx(val.bv_v, rel=1e-12)
    assert val.v_ddm == pytest.approx(val.bv_v, rel=1e-12)


def test_terminal_payout_is_capital_neutral_identity():
    """TV_DDM = BV_Q + TV_RI (терминал обеих форм с одним множителем времени, М§7)."""
    t = terminal(n20=0.16, req20=0.133, n11_star=0.13, req11=0.08, rwa=100000.0, bv_q=15000.0,
                 pbt_last_year=3000.0, y_balancing=0.07, c_wholesale=0.1, headroom=5000.0, tau_eff=0.24,
                 nci_share=0.0, coupon_annual=9.7,
                 tau_stat=0.25, lt_inflation=0.05, real_growth=0.015, fade=0.8, multiple=1.0, k_t=0.16)
    assert t.tv_ddm == pytest.approx(15000.0 + t.tv_ri, rel=1e-12)
    assert t.x_t == pytest.approx((0.16 - 0.133) * 100000.0)
    t2 = terminal(n20=0.16, req20=0.133, n11_star=0.13, req11=0.08, rwa=100000.0, bv_q=15000.0,
                  pbt_last_year=3000.0, y_balancing=0.07, c_wholesale=0.1, headroom=5000.0, tau_eff=0.24,
                  nci_share=0.0, coupon_annual=9.7,
                  tau_stat=0.25, lt_inflation=0.2, real_growth=0.015, fade=0.8, multiple=1.0, k_t=0.16)
    assert t2.g_capped and t2.g_t == pytest.approx(0.16 - 0.0001)


def test_cell_series_are_finite_and_complete():
    c = fixture_run().cell("M", "crisis", "strict")
    for name, series in c.quarters.items():
        for q, v in enumerate(series[1:], start=1):
            assert v is not None and math.isfinite(v), (name, q)
    assert c.years[0] == fixture_run().ctx.timeline.anchor_year
    assert all(len(v) == len(c.years) for v in c.annual.values())
    assert c.annual["dps"][-1] is None                    # ГОСА за последний год — после last_period
