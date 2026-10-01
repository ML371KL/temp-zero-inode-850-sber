"""Волна W2 (аудит после W1, LEAD-DECISIONS-3): аналитические тесты М§18 на фикстуре — «прочее» года якоря,
FVC режима, ставка избытка на терминале, D_pend в цене, pos по «концу дня», окно и замороженные μ A-P2u,
год шока сдвигом, обучение на четырёх режимах, `sigma0_split` и гейт `lt_spread_floor`, границы гайденса,
сдвиг положения осей и гейт `off_band_shift`, источник суждения, целые ключи книги тестом чувствительности."""

from __future__ import annotations

import copy
import math
from datetime import timedelta

import pytest

from model import uncertainty as U
from model.book import anchor_facts, book_from_dict, book_roles
from model.book_schema import BookError, FactsError
from model.checks import check_gates, check_invariants, guidance_band, inside, text_tolerance
from model.credit import bridge_from_facts, kappa_addon, regime_cor_engine
from model.grid import (DividendRecord, LiveInputs, cap_shift, cell_probabilities, derived_values, layer_weights,
                        make_context, price_from_v0, run_cell, run_grid)
from model.nextreport import model_expectation, open_period, with_observation
from model.nii import current_share_target, nss_value, solve_transmission
from model.paths import Trajectory
from model.timeline import make_clock, make_timeline, period_words
from model.valuation import excess_income, terminal
from tests.support_core import book_with, fixture_book, fixture_dict, fixture_facts, fixture_run

pytestmark = pytest.mark.tact


def _prices(book):
    return {str(t): float(p) for t, p in book.get("meta.market_price").items()}


def _live(book, v, register=()):
    prices = _prices(book)
    return LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=tuple(register))


def _record(year, dps, ex, status="declared"):
    return DividendRecord(year=year, dps=dps, status=status, record_date=ex, last_buy_date=ex - timedelta(days=1),
                          ex_date=ex, pay_date=ex + timedelta(days=15), sources=("тест", "тест 2"))


def _fails(data, *fragments):
    with pytest.raises(BookError) as exc:
        book_from_dict(data, facts=fixture_facts())
    for fr in fragments:
        assert fr in str(exc.value), str(exc.value)


def _agm(book, tl):
    return tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")


# ------------------------------------------------------------------ № 26: pos по «концу дня»


def test_position_on_the_ruler_is_end_of_day():
    book = fixture_book()
    tl = make_timeline(book)
    end = tl.end(2)
    assert tl.pos(end) == 2.0 == tl.pos(end + timedelta(days=1))          # pos(E_q) = q, как у S_(q+1)
    assert tl.pos(end - timedelta(days=1)) == pytest.approx(1 + (tl.d(2) - 2) / tl.d(2), abs=1e-15)   # день v не прошёл
    assert tl.pos(tl.end(0)) == 0.0 and tl.pos(tl.start(0)) == -1.0       # линейка продолжается в прошлое
    a, b = make_clock(book, tl, end), make_clock(book, tl, end + timedelta(days=1))
    assert (a.q0, a.elapsed, a.tau, a.roll) == (b.q0, b.elapsed, b.tau, b.roll)
    curve = tl.end(0) + timedelta(days=40)
    rolled = make_clock(book.with_overrides({"meta.curve_as_of": curve.isoformat()}), tl, end)
    assert rolled.roll == pytest.approx(0.25 * (2.0 - tl.pos(curve)), abs=1e-15)


def test_point_is_equal_on_the_quarter_end_and_the_next_day():
    """М§0.2, §18: между E_qA и E_qA + 1 точка не меняется вовсе (rel 1e-12) — перекат и τ на одной линейке."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    for q in (1, _agm(book, tl)):
        end = tl.end(q)
        rec = (_record(tl.anchor_year, 45.0, tl.end(_agm(book, tl)) + timedelta(days=19)),)
        on = run_grid(book, facts, _live(book, end, rec))
        nxt = run_grid(book, facts, _live(book, end + timedelta(days=1), rec))
        assert nxt.point == pytest.approx(on.point, rel=1e-12)
        assert (nxt.low, nxt.high) == pytest.approx((on.low, on.high), rel=1e-12)


# ------------------------------------------------------------------ № 25: D_pend — объявленный дивиденд без дисконта


def test_declared_dividend_carries_no_governance_discount_before_the_model_deduction():
    """М§8.2: от решения ГОСА до E_qA объявленный дивиденд внутри V0, но вне дисконта: на E_qA цена меняется
    на перекат, не на g_gov × DPS."""
    facts = fixture_facts()
    g = 0.10
    book = fixture_book().with_overrides({
        "valuation.governance.discount": g,
        "valuation.governance.components": [{"id": "x", "name": "x", "value": g, "sign": 1, "basis": "тест"}]})
    tl = make_timeline(book)
    end = tl.end(_agm(book, tl))
    dps = 45.0
    rec = (_record(tl.anchor_year, dps, end + timedelta(days=19)),)
    before, on = (run_grid(book, facts, _live(book, end + timedelta(days=d), rec)) for d in (-1, 0))
    amount = dps * before.shares_out / 1000
    assert before.pending_dividend == pytest.approx(amount) and before.bridge_amount == 0.0
    assert on.pending_dividend == 0.0 and on.bridge_amount == pytest.approx(amount)
    lay = before.layers["analytical"]
    assert lay.price == pytest.approx(((lay.v0 - amount) * (1 - g) + amount) * 1000 / before.shares_out, rel=1e-12)
    assert abs(on.point - before.point) < 1e-3 * on.point                 # перекат двух дней (< 0,1 % цены, М§18)
    assert abs(on.point - before.point) < 0.2 * g * dps                   # и не g_gov × DPS
    none = run_grid(book, facts, _live(book, end - timedelta(days=1)))    # решения ещё нет — и D_pend нет
    assert none.pending_dividend == 0.0
    assert before.cell_price(before.cells[0]) == pytest.approx(
        price_from_v0(before.cells[0].v_ri, 0.0, g, before.shares_out, amount), rel=1e-15)


def test_price_formula_with_the_pending_dividend():
    assert price_from_v0(1000.0, 100.0, 0.1, 10.0) == pytest.approx((900.0 + 100.0) * 100)
    assert price_from_v0(1000.0, 0.0, 0.1, 10.0, 200.0) == pytest.approx((800.0 * 0.9 + 200.0) * 100)
    assert price_from_v0(150.0, 0.0, 0.1, 10.0, 200.0) == pytest.approx(150.0 * 100)      # V0 − D_pend ≤ 0 — без дисконта
    assert price_from_v0(1000.0, 50.0, 0.0, 10.0, 200.0) == price_from_v0(1000.0, 50.0, 0.0, 10.0)


# ------------------------------------------------------------------ № 2: «прочее» года якоря — уровень × доля


def test_misc_of_the_anchor_year_is_the_level_times_the_quarter_share():
    """М§0.3: оставшиеся кварталы года якоря = уровень книги × индекс цен × доля квартала; сумма года — выход."""
    run = fixture_run()
    book, tl, prep = run.ctx.book, run.ctx.timeline, run.ctx.prep
    level = Trajectory(book.get("other.misc_net_real"))
    shares = {h: float(book.get(f"other.misc_quarter_shares.{h}")) for h in (1, 2, 3, 4)}
    ay = tl.anchor_year
    rest = [q for q in tl.quarters_of_year(ay) if q >= 1]
    assert rest
    annual = sum(level.value(ay, h) for h in (1, 2, 3, 4)) / 4
    fact = sum(prep.hist["misc"][q] for q in tl.quarters_of_year(ay) if q <= 0)
    for c in (run.cell("H", "norm", "schedule"), run.cell("M", "crisis", "strict")):
        idx = run.ctx.worlds[c.world].price_index
        for q in rest:
            assert c.quarters["misc"][q] == pytest.approx(annual * shares[tl.h(q)] * idx[q], rel=1e-12)
        year_total = fact + sum(c.quarters["misc"][q] for q in rest)
        assert year_total != pytest.approx(annual, rel=1e-3)                 # остатком год не закрывается
    # ось уровня «прочего» двигает оставшиеся кварталы года якоря
    moved = run_grid(book.with_overrides({"other.misc_net_real": {"LT": annual - 100.0}}), run.ctx.facts)
    c0, c1 = run.cell("H", "norm", "schedule"), moved.cell("H", "norm", "schedule")
    assert c1.quarters["misc"][rest[0]] < c0.quarters["misc"][rest[0]]
    # непрофильный результат закрывает год остатком, как раньше
    nc = Trajectory(book.get("noncore.result_real"))
    nc_year = sum(nc.value(ay, h) for h in (1, 2, 3, 4)) / 4
    nc_fact = sum(prep.hist["noncore"][q] for q in tl.quarters_of_year(ay) if q <= 0)
    assert prep.noncore_q[ay] * len(rest) + nc_fact == pytest.approx(nc_year, rel=1e-12)


# ------------------------------------------------------------------ № 3: FVC режима


def test_fvc_is_the_cor_deviation_from_the_reference_regime_on_fv_loans():
    """М§4.6: FVC = factor × (CoR_corporate − CoR_ref) × Ē^СС × d/365; опора — путь режима `fv_loans_ref`
    через мост, без κ и δ; в клетке режима-опоры мира N — ноль, в кризисе — расход, в мягкой посадке — доход."""
    run = fixture_run()
    book, tl = run.ctx.book, run.ctx.timeline
    ref = str(book.get("credit.fv_loans_ref"))
    factor = float(book.get("credit.fv_loans_factor"))
    assert factor == 1.0 and ref == "norm"
    cor_ref = regime_cor_engine(book, run.ctx.bridge, ref, tl)
    assert all(v == 0.0 for v in run.cell("N", ref, "schedule").quarters["fvc"][1:])
    shock = run.ctx.prep.regimes["crisis"].shock_year
    crisis = run.cell("N", "crisis", "schedule")
    assert all(crisis.quarters["fvc"][q] > 0 for q in tl.quarters_of_year(shock))
    soft = run.cell("N", "soft", "schedule")
    assert sum(soft.quarters["fvc"][1:]) < 0
    for c in (run.cell("M", "downturn", "strict"), run.cell("H", "norm", "upper"), crisis):
        for q in range(1, tl.Q + 1):
            want = factor * (c.quarters["cor"][q] - cor_ref[q]) * c.quarters["loans_fv_avg"][q] * tl.d(q) / 365
            assert c.quarters["fvc"][q] == pytest.approx(want, rel=1e-9, abs=1e-12), (c.label, q)
            assert c.quarters["loans_fv_avg"][q] > 0
    # κ-добавка мира действует и на кредиты по СС: клетка режима-опоры мира H несёт только её
    h = run.cell("H", ref, "schedule")
    p = run.ctx.prep
    kap = kappa_addon(p.kappa, p.lag, run.ctx.worlds["H"].real_key, run.ctx.worlds["N"].real_key, tl.Q)
    assert max(kap) > 0
    for q in range(1, tl.Q + 1):
        assert h.quarters["fvc"][q] == pytest.approx(kap[q] * h.quarters["loans_fv_avg"][q] * tl.d(q) / 365, abs=1e-10)


def test_fvc_is_inside_the_step_and_can_be_switched_off():
    book, facts = fixture_book(), fixture_facts()
    run = fixture_run()
    off = run_grid(book.with_overrides({"credit.fv_loans_factor": 0.0}), facts)
    assert all(v == 0.0 for c in off.cells for v in c.quarters["fvc"][1:])
    a, b = run.cell("M", "crisis", "strict"), off.cell("M", "crisis", "strict")
    assert a.quarters["fvc"][1] > 0                      # первый квартал: баланс до него общий — разница ровно FVC
    assert a.quarters["pbt"][1] == pytest.approx(b.quarters["pbt"][1] - a.quarters["fvc"][1], rel=1e-12)
    q = run.ctx.timeline.quarters_of_year(run.ctx.prep.regimes["crisis"].shock_year)[1]
    assert a.quarters["bv"][q] < b.quarters["bv"][q] and a.quarters["n20"][q] < b.quarters["n20"][q]
    assert a.quarters["rwa"][q] != b.quarters["rwa"][q]   # баланс, RWA и нормативы пересобраны с этой строкой
    assert not [f.name for f in check_invariants(run) if f.fired]            # тождества ОПУ и BV — с FVC
    other = run_grid(book.with_overrides({"credit.fv_loans_ref": "soft"}), facts)
    assert all(v == 0.0 for v in other.cell("N", "soft", "schedule").quarters["fvc"][1:])
    assert sum(other.cell("N", "norm", "schedule").quarters["fvc"][1:]) > 0
    _fails(book_with({"credit.fv_loans_ref": "boom"}), "fv_loans_ref")


# ------------------------------------------------------------------ № 4: ставка избытка на терминале


BOUND_MIN_SHARE = 0.30        # минимум ликвидности, при котором он связывает на конце сетки (фикстура)


def test_excess_income_uses_the_marginal_rate_of_the_balancing_items():
    assert excess_income(100.0, 0.08, 0.15, 250.0) == pytest.approx(8.0)              # минимум не связывает — смесь
    assert excess_income(100.0, 0.08, 0.15, 0.0) == pytest.approx(15.0)               # связывает — опт
    assert excess_income(100.0, 0.08, 0.15, 40.0) == pytest.approx(0.08 * 40 + 0.15 * 60)
    assert excess_income(-100.0, 0.08, 0.15, 0.0) == pytest.approx(-8.0)              # недостаток без добора опта — смесь
    # порог связывания — HLA / (1 − m): изъятый капитал уменьшает и LA, и активы, от которых считается минимум
    assert excess_income(100.0, 0.08, 0.15, 40.0, 0.2) == pytest.approx(0.08 * 50 + 0.15 * 50)
    assert excess_income(45.0, 0.08, 0.15, 40.0, 0.2) == pytest.approx(0.08 * 45)     # между HLA и H* — ещё смесь
    # недостаток капитала при связанном минимуме сначала гасит добор опта WT, остальное — в балансирующие активы
    assert excess_income(-100.0, 0.08, 0.15, 0.0, 0.2, 30.0) == pytest.approx(-(0.15 * 30 + 0.08 * 70))
    assert excess_income(-20.0, 0.08, 0.15, 0.0, 0.2, 30.0) == pytest.approx(-0.15 * 20)
    kw = dict(n20=0.16, req20=0.133, n11_star=0.2, req11=0.08, rwa=100000.0, bv_q=15000.0, pbt_last_year=3000.0,
              tau_eff=0.24, nci_share=0.01, coupon_annual=9.7, tau_stat=0.25, lt_inflation=0.05, real_growth=0.015,
              fade=1.0, multiple=1.0, k_t=0.16)
    t = terminal(y_balancing=0.07, c_wholesale=0.12, headroom=1000.0, **kw)
    x = (0.16 - 0.133) * 100000.0
    g = 1.05 * 1.015 - 1
    y_x = 0.07 * 1000.0 + 0.12 * (x - 1000.0)
    assert t.y_x == pytest.approx(y_x)
    assert t.ni_t1 == pytest.approx((3000.0 * (1 + g) - y_x) * (1 - 0.24) * (1 - 0.01), rel=1e-12)
    assert t.tv_ddm == pytest.approx(15000.0 + t.tv_ri, rel=1e-12)                    # DDM = RI не затронут


def test_terminal_net_income_closed_form_in_every_cell():
    """М§7: NI_T+1 = [PBT_L (1 + g_T) − Y_X] (1 − τ_eff)(1 − nci); Y_X — смесь бумаг и ликвидности, пока
    минимум ликвидности не связывает (порог H* = HLA / (1 − m)), дальше — ставка опта; недостаток капитала
    сначала гасит добор опта WT (закрытая формула из рядов клетки и книги)."""
    run = fixture_run()
    book, tl, p = run.ctx.book, run.ctx.timeline, run.ctx.prep
    Q, L = tl.Q, tl.last_year
    nci = float(book.get("pnl.nci_share"))

    def closed_form(r):
        """Y_X и NI_T+1 каждой клетки прогона из её рядов и книги; → (свободных, связанных, с добором опта)."""
        sec = float(r.ctx.book.get("volumes.securities_share_of_liquid"))
        lm = float(r.ctx.book.get("volumes.liquid_min_share"))
        wf = float(r.ctx.book.get("volumes.wholesale_to_funds"))
        free = bound = repaid = 0
        for c in r.cells:
            la = c.quarters["securities"][Q] + c.quarters["liquidity"][Q]
            h_star = max(0.0, la - lm * c.quarters["assets"][Q]) / (1 - lm)
            wt = c.quarters["wholesale"][Q] - wf * c.quarters["funds"][Q]            # добор опта на конце Q
            assert wt == pytest.approx(c.quarters["wholesale_extra"][Q], abs=1e-6) and wt >= -1e-9
            y_bal = sec * c.books["securities"]["rate"][Q] + (1 - sec) * c.books["liquidity"]["rate"][Q]
            c_wh = c.books["wholesale"]["rate"][Q]
            x_t = c.x_t
            if x_t > 0:
                y_x = y_bal * min(x_t, h_star) + c_wh * max(0.0, x_t - h_star)
                bound += x_t > h_star
                free += x_t <= h_star
            else:
                y_x = -(c_wh * min(-x_t, wt) + y_bal * max(0.0, -x_t - wt))
                repaid += wt > 0
            assert c.y_x == pytest.approx(y_x, rel=1e-10, abs=1e-9), c.label
            pbt_l = sum(c.quarters["pbt"][q] for q in range(1, Q + 1) if tl.year(q) == L)
            ni = (pbt_l * (1 + c.g_t) - y_x) * (1 - c.quarters["tau_eff"][Q]) * (1 - nci)
            ci = ni - p.coupon_annual * (1 - c.quarters["tau_stat"][Q])
            assert c.roe_t_raw * c.bv_star == pytest.approx(ci, rel=1e-10), c.label
        return free, bound, repaid

    free, _, _ = closed_form(run)
    assert free > 0                                    # на фикстуре есть клетки с несвязанным минимумом
    # остаток № 38: ветка «сверх запаса — опт» на связанной клетке — минимум ликвидности поднят так, что связывает
    tight = run_grid(book.with_overrides({"volumes.liquid_min_share": BOUND_MIN_SHARE}), run.ctx.facts)
    _, bound, repaid = closed_form(tight)
    assert bound > 0 and repaid > 0, (bound, repaid)   # есть и избыток сверх порога, и недостаток с добором опта
    # при securities_share_of_liquid = 0 ставка избытка несвязанной клетки — ставка ликвидности (прежнее правило)
    zero = run_grid(book.with_overrides({"volumes.securities_share_of_liquid": 0.0}), run.ctx.facts)
    seen = 0
    for c in zero.cells:
        if 0 < c.x_t <= c.la_headroom:
            assert c.y_x == c.books["liquidity"]["rate"][Q] * c.x_t
            seen += 1
    assert seen > 0


def test_la_minimum_binds_where_the_balance_needs_it():
    """М§4.10: LA не ниже `liquid_min_share` × активы; недостающее добирается оптом сверх базового."""
    book, facts = fixture_book(), fixture_facts()
    lm = 0.30
    run = run_grid(book.with_overrides({"volumes.liquid_min_share": lm}), facts)
    c = run.cell("M", "soft", "schedule")
    tl = run.ctx.timeline
    extra = [q for q in range(1, tl.Q + 1) if c.quarters["wholesale_extra"][q] > 0]
    assert extra
    for q in extra:
        la = c.quarters["securities"][q] + c.quarters["liquidity"][q]
        assert la == pytest.approx(lm * c.quarters["assets"][q], rel=1e-12)
    assert c.la_headroom == pytest.approx(0.0, abs=1e-6) or tl.Q not in extra


# ------------------------------------------------------------------ В2: год шока — сдвигом


def test_shock_year_is_the_anchor_year_plus_the_offset():
    run = fixture_run()
    tl = run.ctx.timeline
    off = int(run.ctx.book.get("regimes.crisis.shock_year_offset"))
    assert run.ctx.prep.regimes["crisis"].shock_year == tl.anchor_year + off
    assert all(rp.shock_year is None for r, rp in run.ctx.prep.regimes.items() if r != "crisis")
    assert derived_values(run.ctx)["regimes"]["shock_year"] == tl.anchor_year + off
    data = fixture_dict()
    assert "shock_year" not in data["regimes"]["crisis"]
    _fails(book_with({"regimes.crisis.shock_year": tl.anchor_year + 1}), "shock_year", "незнакомый ключ")
    _fails(book_with({"regimes.crisis.shock_year_offset": 0}), "shock_year_offset", "≥ 1")
    _fails(book_with({"regimes.crisis.shock_year_offset": 2}), "вне года шока")
    _fails(book_with({"regimes.crisis.one_off_loss": {"period": f"{tl.anchor_year + 2}Q1", "amount": -400.0}}),
           "one_off_loss.period", "вне года шока")
    ov = {str(tl.anchor_year + 2): 0.045}
    _fails(book_with({"regimes.crisis.loan_growth_override": ov}), "loan_growth_override", "не год шока")
    _fails(book_with({}, drop=("regimes.crisis.shock_year_offset",)), "без regimes.crisis.shock_year_offset")
    _fails(book_with({"regimes.norm.one_off_loss": {"period": f"{tl.anchor_year + 1}Q1", "amount": -1.0}}),
           "кризисный ключ без")


def test_crisis_keys_move_together_with_the_shock_year():
    """Сдвиг 2: кризисные ключи на год позже — отмена выплаты и разовый убыток приходятся на новый год шока."""
    data = fixture_dict()
    cr = data["regimes"]["crisis"]
    y = int(data["meta"]["anchor_period"][:4])
    cr["shock_year_offset"] = 2
    cor = {}
    for k, v in cr["cor"].items():
        cor[k.replace(str(y + 1) + "Q", str(y + 2) + "Q")] = v
    cor[str(y + 1)], cor[str(y + 2)] = cr["cor"][str(y)], cr["cor"][str(y + 1)]
    cor.pop(str(y + 3), None)
    cr["cor"] = cor
    cr["one_off_loss"]["period"] = f"{y + 2}Q1"
    cr["loan_growth_override"] = {str(y + 2): 0.025, str(y + 3): 0.045}
    data["checks"]["m_crisis_vs_cbr"]["loan_growth"] = {str(y + 2): [0.0, 0.05], str(y + 3): [0.02, 0.07]}
    book = book_from_dict(data, facts=fixture_facts())
    run = run_grid(book, fixture_facts())
    c = run.cell("N", "crisis", "strict")
    decs = {d.year: d for d in c.decisions}
    assert decs[y + 1].source == "crisis_skip" and decs[y].source == "model"
    tl = run.ctx.timeline
    assert c.quarters["one_off"][tl.index(f"{y + 2}Q1")] == -400.0
    assert sum(1 for v in c.quarters["one_off"][1:] if v) == 1


# ------------------------------------------------------------------ В2: окно и замороженные μ A-P2u


def _obs(period, cor, nim=None, **extra):
    return {"period": period, "cor": cor, "nim": nim, "se_cor": None if cor is None else 0.0,
            "se_nim": None if nim is None else 0.0, "basis": "mgmt", **extra}


def test_filter_reads_only_the_window_of_last_observations():
    """М§12: память правила — окно `window_obs` последних наблюдений; априорные — базовые книги."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    n = int(book.get("joint.regime_update.window_obs"))
    assert n == 4
    values = [0.010, 0.019, 0.013, 0.022, 0.016]
    obs = [_obs(tl.period(q), v) for q, v in zip(range(1, 6), values)]
    full = make_context(book.with_overrides({"joint.regime_update.observations": obs}), facts)
    last = make_context(book.with_overrides({"joint.regime_update.observations": obs[1:]}), facts)
    assert full.posterior == pytest.approx(last.posterior, abs=1e-15)
    assert [u["period"] for u in full.updates] == [o["period"] for o in obs[1:]]
    for r in full.deviations:
        assert full.deviations[r]["cor"] == pytest.approx(last.deviations[r]["cor"], abs=1e-18)
    wide = make_context(book.with_overrides({"joint.regime_update.observations": obs,
                                             "joint.regime_update.window_obs": 5}), facts)
    assert wide.posterior != pytest.approx(full.posterior, abs=1e-6)        # старое наблюдение влияло бы
    one = make_context(book.with_overrides({"joint.regime_update.observations": obs,
                                            "joint.regime_update.window_obs": 1}), facts)
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
    limit = float(book.get("joint.regime_update.max_shift_pp"))
    assert max(abs(one.posterior[r] - prior[r]) for r in prior) <= limit + 1e-12   # одно наблюдение от базовых априорных
    _fails(book_with({"joint.regime_update.window_obs": 0}), "window_obs")
    _fails(book_with({}, drop=("joint.regime_update.window_obs",)), "window_obs", "нет ключа")


def test_observation_at_the_anchor_uses_the_frozen_regime_expectations():
    """М§12: квартал не позже якоря клетки не считают — ожидания режимов берутся из записи (`mu_cor`, `mu_nim`),
    в базисе записи, через тот же мост; хвост факта затухает в прогнозных кварталах ρ^k."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    regimes = list(book.get("regimes.ids"))
    mu = {"soft": 0.011, "norm": 0.013, "downturn": 0.016, "crisis": 0.0165}
    value = 0.0125
    ob = _obs(tl.anchor, value, mu_cor=mu)
    ctx = make_context(book.with_overrides({"joint.regime_update.observations": [ob]}), facts)
    sigma = float(book.get("joint.regime_update.observables.cor.sigma_pp"))
    rho = float(book.get("joint.regime_update.observables.cor.rho_q"))
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in regimes}
    like = {r: math.exp(-0.5 * (value - mu[r]) ** 2 / sigma ** 2) for r in regimes}
    norm = sum(prior[r] * like[r] for r in regimes)
    want = cap_shift(prior, {r: prior[r] * like[r] / norm for r in regimes},
                     float(book.get("joint.regime_update.max_shift_pp")))
    assert ctx.posterior == pytest.approx(want, abs=1e-12)
    assert ctx.expectations["frozen"][tl.anchor]["cor"]["norm"] == pytest.approx(ctx.bridge.to_engine_cor(mu["norm"]))
    for r in regimes:                                           # δ_q = ρ^q × (obs − μ_r), q ≥ 1; в q = 0 клетки нет
        d = ctx.deviations[r]["cor"]
        assert d[0] == 0.0
        for q in (1, 2, 5):
            assert d[q] == pytest.approx(rho ** q * (value - mu[r]), abs=1e-15)
    cell = run_cell(ctx, "N", "norm", "schedule")
    base = run_cell(make_context(book, facts), "N", "norm", "schedule")
    assert cell.quarters["cor"][1] - base.quarters["cor"][1] == pytest.approx(rho * (value - mu["norm"]), abs=1e-12)
    earlier = _obs(tl.period(-1), 0.012, mu_cor=mu)             # два квартала не позже якоря и прогнозный — одно окно
    later = _obs(tl.period(1), 0.014)
    mixed = make_context(book.with_overrides({"joint.regime_update.observations": [later, ob, earlier]}), facts)
    assert [u["period"] for u in mixed.updates] == [tl.period(-1), tl.anchor, tl.period(1)]
    assert mixed.expectations["until"] == 1
    _fails(book_with({"joint.regime_update.observations": [_obs(tl.anchor, value)]}), "mu_cor", "не позже якоря")
    _fails(book_with({"joint.regime_update.observations": [_obs(tl.period(1), value, mu_cor=mu)]}),
           "mu_cor", "прогнозного квартала")
    _fails(book_with({"joint.regime_update.observations": [_obs(tl.anchor, value, mu_cor={"norm": 0.013})]}),
           "mu_cor")
    _fails(book_with({"joint.regime_update.observations": [ob, dict(ob)]}), "второе наблюдение")
    assert open_period(book.with_overrides({"joint.regime_update.observations": [ob]}), facts) == tl.period(1)


# ------------------------------------------------------------------ № 21, № 19: обучение при смеси режимов


def _point_by_regime(run):
    """Точка при P(r) = 1 на клетках прогона: цена слоя аффинна по V0, поэтому точка = Σ_r P(r) × точка_r."""
    book = run.ctx.book
    out = {}
    for r in book.get("regimes.ids"):
        one = {x: (1.0 if x == r else 0.0) for x in book.get("regimes.ids")}
        prices = {}
        for name, weights in layer_weights(book).items():
            prob = cell_probabilities(book, weights, one)
            prices[name] = run.price_of(sum(prob[c.key] * c.v_ri for c in run.cells))
        out[r] = prices["macro_neutral"] + run.lam * (prices["analytical"] - prices["macro_neutral"])
    return out


def test_fact_equal_to_the_expectation_is_learning_with_four_regimes():
    """М§12 (A5 — обучение): при смеси режимов наблюдение на ожидании перевзвешивает режимы; сдвиг точки равен
    Σ_r ΔP(r) × V_r, вклад отклонений клеток ≈ 0."""
    book, facts = fixture_book(), fixture_facts()
    live = _live(book, make_timeline(book).end(0) + timedelta(days=30))
    base = run_grid(book, facts, live)
    period = open_period(book, facts)
    exp = model_expectation(book, facts, period, live=live, run=base)
    obs = run_grid(with_observation(book, period, cor=exp["cor_q_mgmt"], nim=None), facts, live)
    d_prob = {r: obs.ctx.posterior[r] - base.ctx.posterior[r] for r in base.ctx.posterior}
    assert max(abs(v) for v in d_prob.values()) > 1e-3                         # режимы перевзвешены
    assert sum(d_prob.values()) == pytest.approx(0.0, abs=1e-12)
    v_r = _point_by_regime(base)
    assert base.point == pytest.approx(sum(base.ctx.posterior[r] * v_r[r] for r in v_r), rel=1e-12)
    learning = sum(d_prob[r] * v_r[r] for r in v_r)
    shift = obs.point - base.point
    assert abs(learning) > 0.5                                                 # ₽: это не ноль
    assert shift == pytest.approx(learning, abs=0.02 * abs(learning) + 0.02)   # вклад отклонений клеток ≈ 0
    q = base.ctx.timeline.index(period)                                        # Σ_r P(r) × error_r = 0
    err = {r: obs.ctx.deviations[r]["cor"][q] for r in d_prob}
    assert sum(base.ctx.posterior[r] * err[r] for r in err) == pytest.approx(0.0, abs=1e-12)


def test_nim_row_equal_to_the_expectation_leaves_the_regimes_alone():
    """М§13: второе наблюдаемое строки таблицы — не наблюдалось; пока ожидания ЧПМ режимов равны, строка ЧПМ на
    ожидании оставляет апостериорные равными априорным, а точку — в допуске поиска."""
    book, facts = fixture_book(), fixture_facts()
    live = _live(book, make_timeline(book).end(0) + timedelta(days=30))
    base = run_grid(book, facts, live)
    period = open_period(book, facts)
    exp = model_expectation(book, facts, period, live=live, run=base)
    nims = [r["nim_q_engine"] for r in exp["by_regime"]]
    assert max(nims) - min(nims) < 1e-6
    row = run_grid(with_observation(book, period, cor=None, nim=exp["nim_q_mgmt"]), facts, live)
    assert row.ctx.posterior == pytest.approx(base.ctx.posterior, abs=1e-6)
    assert abs(row.point - base.point) <= float(book.get("valuation.headline.search_tol_rub"))
    both = run_grid(with_observation(book, period, cor=exp["cor_q_mgmt"], nim=exp["nim_q_mgmt"]), facts, live)
    assert abs(both.point - base.point) > abs(row.point - base.point)         # CoR на ожидании учит режимы


# ------------------------------------------------------------------ № 18, № 22: нейтральное значение и наклон


def test_neutral_search_accepts_an_edge_within_the_tolerance():
    """М§13: допуск один для строк, края и шагов — край отрезка с |невязкой| ≤ допуска — корень."""
    from model.nextreport import _Gap, _neutral
    tol, pad = 0.5, 0.002
    values = [0.058, 0.061, 0.064]
    for side in ("right", "left"):
        table = [-9.0, -6.0, -3.0] if side == "right" else [3.0, 6.0, 9.0]       # знак не меняется
        edge = values[-1] + pad if side == "right" else values[0] - pad
        for fe in (0.0, -0.0, 0.25, -0.25):
            g = _Gap(lambda x, fe=fe: fe)
            root, gap, err = _neutral(values, table, g, pad, tol)
            assert (root, err) == (pytest.approx(edge), None) and gap == fe, (side, fe)
        sign = -1.0 if side == "right" else 1.0
        root, gap, err = _neutral(values, table, _Gap(lambda x: sign * 0.6), pad, tol)    # тот же знак, вне допуска
        assert root is None and gap is None and "вне отрезка" in err
        cross = _Gap(lambda x, e=edge, s=sign: -s * 2.0 if x == e else s * 3.0 + (x - values[0]) * 0)
        root, gap, err = _neutral(values, table, cross, pad, tol)                         # смена знака на краю — поиск
        assert err is None and root is not None


def test_local_slope_is_the_difference_around_the_point():
    from model.nextreport import edge_slope, local_slope
    g = lambda x: -4000.0 * (x - 0.013)                    # noqa: E731 — ₽ на долю: −4 ₽ на 0,1 п.п.
    assert local_slope(g, 0.013, 0.001) == pytest.approx(-4.0)
    assert local_slope(g, 0.013, 0.0005) == pytest.approx(-4.0)
    assert local_slope(g, 0.013, 0.0) is None
    rows = [{"cor": 0.009, "d_median": 10.0}, {"cor": 0.015, "d_median": 0.0}, {"cor": 0.021, "d_median": -11.0}]
    assert edge_slope(rows, "cor") == pytest.approx(-21.0 / 12)
    assert edge_slope(rows[:1], "cor") is None


# ------------------------------------------------------------------ № 35: границы узла гайденса


def test_guidance_band_uses_the_node_tolerance():
    assert guidance_band({"v": 0.2, "kind": "point", "text": "20%"}) == pytest.approx((0.195, 0.205))
    assert guidance_band({"v": 0.0, "kind": "point", "text": "~0%"}) == pytest.approx((-0.005, 0.005))
    assert guidance_band({"v": 0.062, "kind": "point", "text": "~6.2%"}) == pytest.approx((0.0615, 0.0625))
    assert guidance_band({"v": 0.22, "kind": "point", "tol": 0.01, "text": "22%"}) == pytest.approx((0.21, 0.23))
    assert guidance_band({"v": 0.014, "kind": "max"}) == (None, 0.014)
    assert guidance_band({"v": 0.133, "kind": "min"}) == (0.133, None)
    assert guidance_band({"v": [0.3, 0.32], "kind": "range"}) == (0.3, 0.32)
    assert guidance_band({"v": None, "kind": "point"}) == (None, None)
    assert text_tolerance("6,25 %") == pytest.approx(0.00005) and text_tolerance("0.22") == pytest.approx(0.005)
    assert text_tolerance("около нуля") is None
    with pytest.raises(FactsError):
        guidance_band({"v": 0.2, "kind": "point"})                   # ни tol, ни числа в text
    with pytest.raises(FactsError):
        guidance_band({"v": 0.2, "kind": "about"})
    assert inside((None, 0.014), 0.0139) and not inside((None, 0.014), 0.0141)
    assert inside((0.133, None), 0.2) and not inside((0.195, 0.205), 0.21)


def test_guidance_gate_ignores_sector_nodes_and_speaks_words():
    run = fixture_run()
    f = next(x for x in check_gates(run) if x.name == "guidance_gap")
    assert set(f.detail["bands"]) <= {"roe", "nim", "cor_max", "cir"}
    facts = run.ctx.facts
    assert facts.node("guidance", "items.loan_growth.retail")["scope"] == "sector"
    for x in check_gates(run):                                       # сообщения — словами (П§0.2)
        assert "inf" not in x.message and "(None" not in x.message and "_" not in x.message.replace("RWA_", ""), x.name
    assert period_words("2027Q1") == "1 кв. 2027" and period_words("2026M09") == "сентябрь 2026"


# ------------------------------------------------------------------ В8: sigma0_split и гейт lt_spread_floor


def _weights(book, facts):
    books = book.get("nii.books")
    af = anchor_facts(facts, books)
    roles = book_roles(books)
    iea = sum(af.balances[b] for b in roles.assets)
    return {b: af.balances[b] / iea for b in roles.names}, roles


def test_sigma0_split_divides_the_shift_between_assets_and_liabilities():
    """М§4.5: σ0_A = split × Δ0 / Σ a_b, σ0_L = (1 − split) × Δ0 / Σ l_b,H; при split = 1 — прежнее решение."""
    book, facts = fixture_book(), fixture_facts()
    bridge = bridge_from_facts(facts)
    w, roles = _weights(book, facts)
    books = book.get("nii.books")
    ref = str(book.get("nii.transmission.reference_world"))
    target = bridge.to_engine_nim(float(book.get("nii.nim_lt_target_mgmt")))
    delta0 = target - nss_value(book, facts, ref, 0.0, 0.0, 0.0)
    a_sum = sum(w[b] for b in roles.assets if books[b].get("lt_shift"))
    last = str(book.get("meta.last_period"))
    key_h = Trajectory(book.get(f"worlds.{ref}.key_rate")).value(int(last[:4]), int(last[-1]))
    rcs = book.get("nii.retail_current_share")
    c_star = current_share_target(key_h, float(rcs["c_ref"]), float(rcs["psi"]), float(rcs["key_ref"]),
                                  tuple(rcs["bounds"]))
    l_r = w["retail_current"] + w["retail_term"]
    l_sum = sum((l_r * c_star if b == "retail_current" else l_r * (1 - c_star) if b == "retail_term" else w[b])
                for b in roles.liabilities if books[b].get("lt_shift"))
    assert l_sum > 0 and a_sum > 0
    for split in (1.0, 0.5, 0.09, 0.0):
        b2 = book.with_overrides({"nii.sigma0_split": split})
        tr = solve_transmission(b2, facts, bridge)
        assert tr.split == split and tr.delta0 == pytest.approx(delta0, abs=1e-15)
        assert tr.sigma0 == pytest.approx(split * delta0 / a_sum, abs=1e-15)
        assert tr.sigma0_liab == pytest.approx((1 - split) * delta0 / l_sum, abs=1e-15)
        assert tr.nss[ref] == pytest.approx(target, abs=1e-12)                 # Nss(H, 0) = цель точно
        assert tr.t_real == pytest.approx(float(book.get("nii.transmission.target")), abs=1e-12)
    one = solve_transmission(book.with_overrides({"nii.sigma0_split": 1.0}), facts, bridge)
    assert one.sigma0 == delta0 / a_sum and one.sigma0_liab == 0.0             # бит в бит прежняя формула
    d = derived_values(make_context(book, facts))["nii"]
    assert {"sigma0", "sigma0_liab", "sigma0_split", "lt_spread"} <= set(d)
    _fails(book_with({"nii.sigma0_split": 1.2}), "sigma0_split")
    _fails(book_with({}, drop=("nii.sigma0_split",)), "sigma0_split", "нет ключа")
    data = fixture_dict()
    for spec in data["nii"]["books"].values():
        if spec["side"] == "liability":
            spec["lt_shift"] = False
    _fails(data, "пассивов с lt_shift нет")
    data["nii"]["sigma0_split"] = 1.0
    book_from_dict(data, facts=facts)                                          # всё на активах — допустимо
    _fails(book_with({}, drop=("nii.books.corp_funds.lt_shift",)), "corp_funds.lt_shift", "нет ключа")


def test_liability_shift_keeps_the_near_nim_path():
    """Сдвиг на пассивах действует с года `sigma0_from`, как и на активах: ближний путь якоря не трогает."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    a = run_grid(book.with_overrides({"nii.sigma0_split": 1.0}), facts).cell("H", "norm", "schedule")
    b = run_grid(book.with_overrides({"nii.sigma0_split": 0.2}), facts).cell("H", "norm", "schedule")
    y0 = int(book.get("nii.sigma0_from"))
    for q in range(1, tl.Q + 1):
        if tl.year(q) < y0:
            assert b.quarters["nii"][q] == pytest.approx(a.quarters["nii"][q], rel=1e-9), q
    last = [q for q in range(1, tl.Q + 1) if tl.year(q) == tl.last_year]
    assert sum(b.quarters["nim"][q] for q in last) == pytest.approx(sum(a.quarters["nim"][q] for q in last), rel=0.02)


def test_lt_spread_floor_gate():
    """М§14.2: s_eff,b,W = s_b^LT + σ0_A·[lt_shift] − φ_A·[b ∈ Φ_A]·(ref_W^LT − ref_H^LT) ниже пола книги;
    масса — вес миров слоя «свой взгляд», где условие нарушено."""
    run = fixture_run()
    book, tr = run.ctx.book, run.ctx.transmission
    books = book.get("nii.books")
    last = str(book.get("meta.last_period"))
    y, h = int(last[:4]), int(last[-1])
    ref_w = str(book.get("nii.transmission.reference_world"))
    loans = [b for b, s in books.items() if "sector" in s]
    assert set(tr.lt_spread) == set(loans)
    floors = book.get("checks.lt_spread_floor")
    bad = set()
    for b in loans:
        spec = books[b]
        path = lambda w: f"worlds.{w}.key_rate" if spec["ref"] == "key" else f"worlds.{w}.{spec['ref']}"  # noqa: E731
        for w in book.get("worlds.ids"):
            want = Trajectory(spec["spread"]).value(y, h) + (tr.sigma0 if spec["lt_shift"] else 0.0)
            if spec["phi"]:
                want -= tr.phi_assets * (Trajectory(book.get(path(w))).value(y, h)
                                         - Trajectory(book.get(path(ref_w))).value(y, h))
            assert tr.lt_spread[b][w] == pytest.approx(want, abs=1e-15), (b, w)
            if want < float(floors[b]):
                bad.add(w)
    f = next(x for x in check_gates(run) if x.name == "lt_spread_floor")
    weights = run.layers["analytical"].world_weights
    assert f.fired == bool(bad) and f.mass == pytest.approx(sum(weights[w] for w in bad), abs=1e-12)
    assert sorted(f.detail["worlds"]) == sorted(bad)
    loose = run_grid(book.with_overrides({"checks.lt_spread_floor": {b: -1.0 for b in loans}}), run.ctx.facts)
    g = next(x for x in check_gates(loose) if x.name == "lt_spread_floor")
    assert not g.fired and g.mass == 0
    _fails(book_with({}, drop=("checks.lt_spread_floor.mortgage",)), "lt_spread_floor.mortgage")
    _fails(book_with({"checks.lt_spread_floor.securities": 0.0}), "lt_spread_floor.securities", "незнакомый ключ")


# ------------------------------------------------------------------ № 1, № 58: сдвиг положения, оси вне полосы, источник


def test_mean_shift_and_the_off_band_diagnostic():
    """М§10: сдвиг положения оси = (цена_high + цена_low − 2 × точка) / 6; Σ по осям вне полосы — диагностика
    и гейт `off_band_shift` (|Σ| > print_step / 2); у книги со всеми осями в полосе гейт молчит."""
    book, facts = fixture_book(), fixture_facts()
    live = _live(book, make_timeline(book).end(0) + timedelta(days=30))
    run = run_grid(book, facts, live)
    assert book.get("valuation.uncertainty.off_band_axes") == []
    quiet = next(x for x in check_gates(run) if x.name == "off_band_shift")
    assert not quiet.fired and quiet.mass == 0.0
    assert U.off_band_shift(book, facts, live, run.point) == {
        "rub": 0.0, "limit": float(book.get("valuation.headline.print_step")) / 2, "axes": 0}
    axes = copy.deepcopy(book.get("valuation.uncertainty.axes"))
    names = ("Налог: сдвиг ставки", "ERP")
    off = [a for a in axes if a["name"] in names]
    assert len(off) == 2
    few = [a for a in axes if a["name"] not in names][:3] + [a for a in axes if a["kind"] == "dict"][:1]
    b2 = book.with_overrides({"valuation.uncertainty.axes": few, "valuation.uncertainty.off_band_axes": off})
    run2 = run_grid(b2, facts, live)
    assert run2.point == run.point
    shifts = []
    for ax in off:
        lo = run_grid(U.trial_book(b2, U.axis_overrides(b2, ax, -1.0)), facts, live).point
        hi = run_grid(U.trial_book(b2, U.axis_overrides(b2, ax, 1.0)), facts, live).point
        shifts.append((hi + lo - 2 * run.point) / 6)
        assert U.mean_shift(run.point, lo, hi) == pytest.approx(shifts[-1], rel=1e-12)
    diag = U.off_band_shift(b2, facts, live, run2.point)
    assert diag["axes"] == 2 and diag["rub"] == pytest.approx(sum(shifts), rel=1e-12)
    assert shifts[0] < 0                                               # налоговая ось односторонняя — тянет вниз
    f = next(x for x in check_gates(run2) if x.name == "off_band_shift")          # без диагностики — считает сам
    assert f.fired == (abs(sum(shifts)) > diag["limit"]) and f.detail["rub"] == pytest.approx(sum(shifts))
    for rub, fired in ((diag["limit"] * 1.01, True), (-diag["limit"] * 1.01, True), (diag["limit"] * 0.99, False)):
        g = next(x for x in check_gates(run2, off_band={**diag, "rub": rub}) if x.name == "off_band_shift")
        assert g.fired == fired and g.mass == (1.0 if fired else 0.0) and "вне полосы" in g.message
    band = U.band(b2, facts, live, draws=4, workers=1)
    rows = U.judgements(b2, facts, live, band, point=run2.point)
    out = [r for r in rows if not r["in_band"]]
    assert len(out) == 2 and all(r["share"] is None and r["rank_corr"] is None for r in out)
    assert sum(r["mean_shift"] for r in out) == pytest.approx(diag["rub"], rel=1e-12)
    assert U.off_band_shift(b2, facts, live, run2.point, rows)["rub"] == pytest.approx(diag["rub"], rel=1e-12)
    for r in rows:
        if r["kind"] == "dict":
            assert r["mean_shift"] is None
        else:
            assert r["mean_shift"] == pytest.approx((r["price_high"] + r["price_low"] - 2 * run2.point) / 6)
        assert r["source"] == f"book-{b2.get('meta.version')}, ось „{r['name']}“"       # у JSON-книги комментариев нет


def test_judgement_source_is_the_book_comment_without_marks(tmp_path):
    text = (
        "valuation:                       # оценка (MODEL §8)\n"
        "  erp: 0.0557                    # A-V2: премия за риск [Ф: решение владельца 30.09.2026; ось 0,049–0,062]\n"
        "  beta_e: 0.83                   # A-V1: бета, недели [Р: market — 0,826, se 0,053, n 157;\n"
        "                                 #   окна 2 года 0,835, 5 лет 0,996 без 24.02–31.03.2022; ось 0,70–1,05]\n"
        "  terminal:\n"
        "    fade: 1.0                    # угасание избыточной доходности\n"
        "tax:\n"
        "  one_off: {year: 2027, amount: 200.0, prob: 0.0}   # разовый налог [В: DESIGN §4 — P 0–30 % × 100–300; центр 0]\n"
        "  statutory: {\"2026\": 0.25, LT: 0.25}   # явные оси tax.statutory и tax.one_off [В]\n"
        "# ---------------- раздел\n"
        "other:\n"
        "  misc_net_real: {LT: -200.0}    # «прочее», млрд ₽ в год\n"
        "    # отдельная строка, не продолжение\n"
    )
    path = tmp_path / "book.yaml"
    path.write_text(text, encoding="utf-8")
    book = copy.copy(fixture_book())
    object.__setattr__(book, "path", path)
    notes = U.book_notes(book)
    assert notes["valuation.erp"].startswith("A-V2") and "окна 2 года" in notes["valuation.beta_e"]
    assert "раздел" not in " ".join(notes.values()) and "отдельная строка" not in notes["other.misc_net_real"]
    src = lambda paths, name="ось": U.judgement_source(book, {"paths": paths, "name": name}, notes)  # noqa: E731
    assert src(["valuation.erp"]) == "решение владельца 30.09.2026; ось 0,049–0,062"
    # по границе фразы, ≤ 80; фраза с датой без года («24.02») — служебный текст (П§0.2), опускается
    assert src(["valuation.beta_e"]) == "market — 0,826, se 0,053, n 157; ось 0,70–1,05"
    assert src(["valuation.terminal.fade"]) == "угасание избыточной доходности"       # нет метки — текст комментария
    assert src(["tax.one_off.prob"]) == "DESIGN §4 — P 0–30 % × 100–300; центр 0"    # поиск по префиксу пути
    assert src(["other.misc_net_real.LT"]) == "«прочее», млрд ₽ в год"
    version = book.get("meta.version")
    assert src(["tax.statutory"], "Налог") == f"book-{version}, ось „Налог“"          # служебные имена — не источник
    assert src(["valuation.headline.print_step"], "Шаг") == f"book-{version}, ось „Шаг“"    # выше 2-го уровня не ищет
    long = U.source_text("[Р: " + "слово " * 30 + "]")
    assert len(long) <= 80 and long.endswith("…")
    assert U.source_text("A-N2: ЧПМ сквозь цикл [В]") == "A-N2: ЧПМ сквозь цикл"
    assert U.book_notes(fixture_book()) == {}


# ------------------------------------------------------------------ № 40: целые ключи книги — тестом чувствительности


def test_integer_book_keys_change_the_result():
    """Сканер литералов видит дробные числа; целые ключи — шаг печати, лаг κ, λ — проверяются тем, что смена
    ключа меняет результат (М§18)."""
    book, facts = fixture_book(), fixture_facts()
    run = fixture_run()
    live = run.ctx.live
    # шаг печати
    for step in (10, 1):
        band = U.band(book.with_overrides({"valuation.headline.print_step": step}), facts, live, draws=4, workers=1)
        h = band.headline(band.lam, live.prices, str(book.get("meta.company.main_ticker")))
        assert h["printed_median"] == U.round_half_up(h["median"], step) and h["printed_median"] % step == 0
        assert all(v % step == 0 for v in h["printed_band80"])
    # лаг κ-добавки: сдвиг на квартал
    lag = int(book.get("credit.real_rate_lag_q"))
    tl = run.ctx.timeline
    shorter = run_grid(book.with_overrides({"credit.real_rate_lag_q": lag - 1}), facts)
    reg = run.ctx.prep.regimes["norm"].cor_engine
    a, b = run.cell("H", "norm", "schedule"), shorter.cell("H", "norm", "schedule")
    add_a = [a.quarters["cor"][q] - reg[q] for q in range(tl.Q + 1) if q]
    add_b = [b.quarters["cor"][q] - reg[q] for q in range(tl.Q + 1) if q]
    assert max(add_a) > 0 and add_a[:lag] == pytest.approx([0.0] * lag, abs=1e-15)
    assert add_b[:-1] == pytest.approx(add_a[1:], abs=1e-15)
    # λ
    for lam in (0.3, 0.8):
        r2 = run_grid(book.with_overrides({"joint.own_macro_confidence": lam}), facts)
        assert r2.point == pytest.approx(r2.low + lam * (r2.high - r2.low), rel=1e-15)
        assert (r2.low, r2.high) == (run.low, run.high) and r2.point != run.point


def test_kappa_addon_closed_form_on_synthetic_paths():
    """κ × max(0, rr_W,(q−L) − rr_N,(q−L)) с лагом L; история до сетки — ноль (М§4.6)."""
    rr_w = [0.0, 0.09, 0.08, 0.07, 0.06, 0.05, 0.02]
    rr_n = [0.0, 0.04, 0.05, 0.06, 0.07, 0.03, 0.03]
    got = kappa_addon(0.5, 2, rr_w, rr_n, 6)
    assert got == pytest.approx((0.0, 0.0, 0.0, 0.5 * 0.05, 0.5 * 0.03, 0.5 * 0.01, 0.0))
    assert kappa_addon(0.5, 0, rr_w, rr_n, 6)[1] == pytest.approx(0.025)        # без лага добавка — с первого квартала


def test_dia_leg_is_the_book_rate_on_average_retail_funds():
    """Взносы АСВ = `nii.dia_rate` × средние средства ФЛ × d/365 — из книги, а не из ряда клетки (М§4.4)."""
    run = fixture_run()
    tl, c = run.ctx.timeline, run.cell("H", "norm", "schedule")
    rate = float(run.ctx.book.get("nii.dia_rate"))
    assert rate > 0
    for q in range(1, tl.Q + 1):
        avg = (c.quarters["funds_retail"][q - 1] + c.quarters["funds_retail"][q]) / 2
        assert c.quarters["dia"][q] == pytest.approx(rate * avg * tl.d(q) / 365, rel=1e-12)
    tenor = run.ctx.prep.fvoci_tenor
    assert run.ctx.prep.y_tenor0 == run.ctx.facts.need("capital", f"ofz_curve_anchor.{tenor.split('_')[1].rstrip('y')}")
