"""Волна W1 (LEAD-DECISIONS-2): аналитические тесты М§18 на фикстуре — σ0 с года `nii.sigma0_from`,
ближний сдвиг ЧПМ без обрыва, доли «прочего», ввод ε, конец квартала — конец дня, нейтральность
«факт = ожидание» A-P2u, парные передачи, гейт стыка ЧПМ, флаг прыжка ЧП, классы акций, реестр."""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from model import uncertainty as U
from model.book import book_from_dict
from model.book_schema import BookError
from model.checks import check_gates, check_invariants
from model.dividends import Checkpoint, decide
from model.grid import DividendRecord, LiveInputs, make_context, market_cap, run_cell, run_grid
from model.live import register_gaps
from model.nextreport import model_expectation, open_period, with_observation
from model.paths import Trajectory
from model.timeline import make_timeline
from tests.support_core import fixture_book, fixture_dict, fixture_facts, fixture_run

pytestmark = pytest.mark.tact


def _prices(book):
    return {str(t): float(p) for t, p in book.get("meta.market_price").items()}


def _live(book, v, register=()):
    prices = _prices(book)
    return LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=tuple(register))


def _record(year, dps, ex):
    return DividendRecord(year=year, dps=dps, status="declared", record_date=ex, last_buy_date=ex - timedelta(days=1),
                          ex_date=ex, pay_date=ex + timedelta(days=15), sources=("тест", "тест 2"))


# ------------------------------------------------------------------ A1: σ0, ближний сдвиг


def test_sigma0_enters_spreads_from_the_sigma0_from_year_not_earlier():
    run = fixture_run()
    book, tl, prep = run.ctx.book, run.ctx.timeline, run.ctx.prep
    tr = run.ctx.transmission
    s0 = tr.sigma0
    y0 = int(book.get("nii.sigma0_from"))
    assert s0 != 0 and tr.sigma0_liab != 0 and tl.anchor_year < y0 <= tl.last_year
    for b, spec in book.get("nii.books").items():
        t = Trajectory(spec["spread"])
        sigma = s0 if spec["side"] == "asset" else -tr.sigma0_liab     # у пассивов знак обратный (М§4.4)
        for q in range(tl.Q + 1):
            base = t.value(tl.year(q), tl.h(q))
            shift = sigma if spec.get("lt_shift") and tl.year(q) >= y0 else 0.0
            assert prep.spreads[b][q] == pytest.approx(base + shift, abs=1e-15), (b, q)
    lt_books = [b for b, s in book.get("nii.books").items() if s.get("lt_shift") and s["side"] == "asset"]
    q_before = max(q for q in range(tl.Q + 1) if tl.year(q) < y0)
    b = lt_books[0]
    assert prep.spreads[b][q_before] == pytest.approx(Trajectory(book.get(f"nii.books.{b}.spread")).value(
        tl.year(q_before), tl.h(q_before)), abs=1e-15)


def test_stationary_nim_does_not_depend_on_sigma0_from():
    a = fixture_run().ctx.transmission
    b = make_context(fixture_book().with_overrides({"nii.sigma0_from": fixture_book().get("meta.last_period")[:4]
                                                    and int(fixture_book().get("meta.last_period")[:4])}),
                     fixture_facts()).transmission
    assert (a.sigma0, a.sigma0_liab, a.phi, a.t_real) == (b.sigma0, b.sigma0_liab, b.phi, b.t_real)


def test_sigma0_from_outside_the_grid_refuses():
    last = int(fixture_book().get("meta.last_period")[:4])
    with pytest.raises(BookError, match="sigma0_from"):
        fixture_book().with_overrides({"nii.sigma0_from": last + 1})


def test_near_nim_shift_converges_by_quarters_without_a_cliff():
    run = fixture_run()
    book, tl, prep = run.ctx.book, run.ctx.timeline, run.ctx.prep
    t = Trajectory(book.get("regimes.near_nim_shift"))
    assert prep.near == tuple(t.value(tl.year(q), tl.h(q)) for q in range(tl.Q + 1))
    y1 = tl.anchor_year + 1
    q4 = tl.index(f"{tl.anchor_year}Q4")
    nxt = [prep.near[tl.index(f"{y1}Q{h}")] for h in (1, 2, 3, 4)]
    assert prep.near[q4] < nxt[0] < nxt[1] < nxt[2] < nxt[3] == 0.0         # сход по кварталам, не обрыв
    assert all(v == 0.0 for v in prep.near[tl.index(f"{y1}Q4"):])
    # ближний сдвиг одинаков во всех клетках и входит в ЧПД вместе со сдвигом режима (М§4.4)
    for c in run.cells[::7]:
        rg = prep.regimes[c.regime].nim_shift
        for q in (1, q4, tl.index(f"{y1}Q2")):
            dev = run.ctx.deviations.get(c.regime, {}).get("nim")
            d = dev[q] if dev else 0.0
            ov = (prep.near[q] + rg[q] + d) * c.quarters["iea_avg"][q] * tl.d(q) / 365
            assert c.quarters["overlay"][q] == pytest.approx(ov, rel=1e-12)


def test_value_axis_on_a_year_key_with_quarter_keys_refuses():
    data = fixture_dict()
    axis = {"name": "ближний сдвиг 2027", "kind": "value", "paths": ["regimes.near_nim_shift.2027"],
            "low": -0.004, "high": 0.0, "dist": "triangular"}
    data["valuation"]["uncertainty"]["axes"].append(axis)
    with pytest.raises(BookError, match="квартальными ключами"):
        book_from_dict(data, facts=fixture_facts())


# ------------------------------------------------------------------ A9, A4: «прочее» по кварталам, ввод ε


def test_misc_quarter_shares_rebuild_the_annual_amount():
    run = fixture_run()
    book, tl, prep = run.ctx.book, run.ctx.timeline, run.ctx.prep
    t = Trajectory(book.get("other.misc_net_real"))
    shares = {h: float(book.get(f"other.misc_quarter_shares.{h}")) for h in (1, 2, 3, 4)}
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-12)
    for y in range(tl.anchor_year + 1, tl.last_year + 1):
        annual = sum(t.value(y, h) for h in (1, 2, 3, 4)) / 4
        assert sum(prep.misc_q[(y, h)] for h in (1, 2, 3, 4)) == pytest.approx(annual, rel=1e-12)
    ay = tl.anchor_year                      # год якоря — то же правило «уровень × доля» (М§0.3, W2)
    annual = sum(t.value(ay, h) for h in (1, 2, 3, 4)) / 4
    assert sum(prep.misc_q[(ay, h)] for h in (1, 2, 3, 4)) == pytest.approx(annual, rel=1e-12)


def test_misc_quarter_shares_must_sum_to_one():
    with pytest.raises(BookError, match="misc_quarter_shares"):
        fixture_book().with_overrides({"other.misc_quarter_shares.4": 0.5})


def test_excess_epsilon_is_phased_in_over_ramp_years():
    run = fixture_run()
    pol = run.ctx.prep.policy
    start, ramp, eps = pol.excess_from, pol.ramp_years, pol.epsilon
    assert ramp == 3 and eps > 0
    rwa = 10000.0
    pts = [Checkpoint(n20=0.133 + 3000.0 / rwa, n11=0.9, rwa=rwa, floor20=0.08, req11=0.07)]
    got = []
    for y in (start - 1, start, start + 1, start + 2, start + 3):
        d = decide(pol, year=y, base=1000.0, points=pts, buffer20=0.0, deferred=0.0, shock=False, declared_dps=None)
        free = d.headroom[d.step] - d.base_div - d.catch
        got.append(d.excess / free)
    assert got == pytest.approx([0.0, eps / 3, 2 * eps / 3, eps, eps], abs=1e-12)
    with pytest.raises(BookError, match="ramp_years"):
        fixture_book().with_overrides({"dividends.excess.ramp_years": 0})


# ------------------------------------------------------------------ A2: конец квартала — конец дня


def test_quarter_end_is_end_of_day_and_the_point_is_continuous_at_the_agm_quarter_end():
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    end = tl.end(agm_q)
    rec = _record(tl.anchor_year, 45.0, end + timedelta(days=19))
    runs = [run_grid(book, facts, _live(book, end + timedelta(days=d), (rec,))) for d in (-1, 0, 1)]
    before, on, after = runs
    assert (on.ctx.clock.q0, on.ctx.clock.elapsed) == (agm_q + 1, 0.0)
    assert (after.ctx.clock.q0, after.ctx.clock.elapsed) == (agm_q + 1, 0.0)
    assert on.bridge_amount > 0 and before.bridge_amount == 0.0          # мост включается в конец дня E_qA
    for a, b in ((before, on), (on, after)):                              # перекат, не DPS (М§18)
        assert abs(b.point - a.point) < 1e-3 * on.point, (a.point, b.point)
    with pytest.raises(BookError, match="вне сетки"):
        run_grid(book, facts, _live(book, tl.end(tl.Q)))


# ------------------------------------------------------------------ A5: «факт = ожидание»


def _one_regime_book():
    data = copy.deepcopy(fixture_dict())
    ids = data["regimes"]["ids"]
    data["joint"]["regime_prob"] = {r: (1.0 if r == "norm" else 0.0) for r in ids}
    unc = data["valuation"]["uncertainty"]
    unc["axes"] = [a for a in unc["axes"] if a["paths"] != ["joint.regime_prob"]]
    return book_from_dict(data, facts=fixture_facts())


def test_fact_equal_to_the_expectation_moves_nothing_with_one_regime():
    """М§12, §18: при одном режиме наблюдение, равное прогнозу фильтра, не меняет вероятностей, отклонений,
    клеток и точки (при смеси режимов — обучение: `tests/test_core_w2.py`).

    Медиана полосы не меняется точно, если оси полосы не двигают само ожидание квартала (CoR ближнего
    квартала: путь режима, κ-добавка с лагом равна нулю); ЧПМ квартала оси двигают (ψ, веса миров) — в
    прогоне полосы наблюдение отличается от его ожидания, и медиана учится (сдвиг мал)."""
    book, facts = _one_regime_book(), fixture_facts()
    live = _live(book, make_timeline(book).end(0) + timedelta(days=30))
    base = run_grid(book, facts, live)
    period = open_period(book, facts)
    exp = model_expectation(book, facts, period, live=live, run=base)
    b2 = with_observation(book, period, cor=exp["cor_q_mgmt"], nim=exp["nim_q_mgmt"])
    obs = run_grid(b2, facts, live)
    assert obs.ctx.posterior == pytest.approx(base.ctx.posterior, abs=1e-15)
    for r, dev in obs.ctx.deviations.items():
        if base.ctx.posterior[r] > 0:
            assert max(abs(x) for x in dev["cor"] + dev["nim"]) < 1e-12, r
    assert obs.point == pytest.approx(base.point, abs=1e-6)
    assert [c.v_ri for c in obs.cells if c.regime == "norm"] == pytest.approx(
        [c.v_ri for c in base.cells if c.regime == "norm"], rel=1e-12)
    m0 = U.band(book, facts, live, draws=6, workers=1).medians(base.lam)["central"]
    b3 = with_observation(book, period, cor=exp["cor_q_mgmt"], nim=None)
    m_cor = U.band(b3, facts, live, draws=6, workers=1).medians(base.lam)["central"]
    assert m_cor == pytest.approx(m0, abs=1e-6)
    m_both = U.band(b2, facts, live, draws=6, workers=1).medians(base.lam)["central"]
    assert abs(m_both - m0) < 5e-3 * m0


def test_expectation_is_the_filter_forecast_and_the_observation_is_the_layer_mean():
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    obs_book = with_observation(book, tl.period(1), cor=0.017, nim=0.062)
    run = run_grid(obs_book, facts)
    e = model_expectation(obs_book, facts, tl.period(2), run=run)
    rho = {x: float(book.get(f"joint.regime_update.observables.{x}.rho_q")) for x in ("cor", "nim")}
    mu = make_context(obs_book, facts).expectations
    assert mu["until"] == 1
    post = run.ctx.posterior
    for r in book.get("regimes.ids"):                                      # δ затухает ρ^k
        d = run.ctx.deviations[r]
        assert d["cor"][2] == pytest.approx(rho["cor"] * d["cor"][1], rel=1e-12)
        assert d["nim"][2] == pytest.approx(rho["nim"] * d["nim"][1], rel=1e-12)
    by = {row["regime"]: row for row in e["by_regime"]}
    assert e["cor_q_engine"] == pytest.approx(sum(post[r] * by[r]["cor_q_engine"] for r in by), rel=1e-12)
    eng = run.ctx.bridge.to_engine_cor(0.017)
    pw = {w: float(book.get(f"joint.world_prob.{w}")) for w in book.get("worlds.ids")}
    table = book.get("joint.reg_prob_given_regime")
    for r in book.get("regimes.ids"):
        avg = sum(pw[c.world] * float(table[r][c.scenario]) * c.quarters["cor"][1] for c in run.cells if c.regime == r)
        assert avg == pytest.approx(eng, abs=1e-12), r


# ------------------------------------------------------------------ A6: парные передачи


def test_pairwise_transmission_of_neighbouring_worlds():
    tr = fixture_run().ctx.transmission
    order = tr.order
    assert [tr.key_lt[w] for w in order] == sorted(tr.key_lt.values())
    assert list(tr.pairs) == [f"{a}_{b}" for a, b in zip(order, order[1:])]
    for a, b in zip(order, order[1:]):
        want = (tr.nss[b] - tr.nss[a]) / (tr.key_lt[b] - tr.key_lt[a])
        assert tr.pairs[f"{a}_{b}"] == pytest.approx(want, rel=1e-12)
        assert tr.pairs_roe[f"{a}_{b}"] == pytest.approx(want * tr.roe_factor, rel=1e-12)
    assert tr.roe_equiv == pytest.approx(tr.t_target * tr.roe_factor, rel=1e-12)
    first, last = order[0], order[-1]                                       # пары — разбиение отрезка M − N
    chord = (tr.nss[last] - tr.nss[first]) / (tr.key_lt[last] - tr.key_lt[first])
    weights = [(tr.key_lt[b] - tr.key_lt[a]) / (tr.key_lt[last] - tr.key_lt[first]) for a, b in zip(order, order[1:])]
    assert sum(w * tr.pairs[f"{a}_{b}"] for w, (a, b) in zip(weights, zip(order, order[1:]))) == pytest.approx(chord)


def test_transmission_pairs_gate_uses_the_book_corridor():
    run = fixture_run()
    f = next(x for x in check_gates(run) if x.name == "transmission_pairs")
    lo, hi = run.ctx.book.get("checks.transmission_pairs")
    assert f.fired == any(not lo <= v <= hi for v in run.ctx.transmission.pairs.values() if v is not None)
    tight = run_grid(fixture_book().with_overrides({"checks.transmission_pairs": [0.0, 0.1]}), fixture_facts())
    g = next(x for x in check_gates(tight) if x.name == "transmission_pairs")
    assert g.fired and g.mass == 1.0


# ------------------------------------------------------------------ гейт стыка ЧПМ и флаг прыжка ЧП


def test_nim_path_joint_gate_is_the_mgmt_cap_on_the_first_quarters():
    run = fixture_run()
    br, tr, book = run.ctx.bridge, run.ctx.transmission, run.ctx.book
    n = int(book.get("checks.nim_path_joint.quarters"))
    tol = float(book.get("checks.nim_path_joint.tolerance"))
    nim0 = run.ctx.facts.need("nii_books", "nim_eng_q")
    want = []
    for c in run.cells:
        cap = max(br.to_mgmt_nim(nim0), br.to_mgmt_nim(tr.nss[c.world])) + tol
        if any(br.to_mgmt_nim(c.quarters["nim"][q]) > cap for q in range(1, n + 1)):
            want.append(c.label)
    f = next(x for x in check_gates(run) if x.name == "nim_path_joint")
    assert sorted(f.cells) == sorted(want)
    assert f.mass == pytest.approx(sum(run.layers["analytical"].prob[c.key] for c in run.cells if c.label in want))
    loose = run_grid(book.with_overrides({"checks.nim_path_joint.tolerance": 1.0}), run.ctx.facts)
    assert not next(x for x in check_gates(loose) if x.name == "nim_path_joint").fired


def test_ni_jump_flag_compares_the_same_quarter_a_year_ago_at_an_unchanged_key():
    run = fixture_run()
    f = next(x for x in check_gates(run) if x.name == "ni_jump")
    assert f.kind == "flag"
    always = run_grid(fixture_book().with_overrides({"checks.ni_jump.yoy": -1.0, "checks.ni_jump.key_tol": 1.0}),
                      fixture_facts())
    g = next(x for x in check_gates(always) if x.name == "ni_jump")
    assert g.fired and len(g.detail["jumps"]) == 3 * int(always.ctx.book.get("checks.ni_jump.quarters"))
    never = run_grid(fixture_book().with_overrides({"checks.ni_jump.key_tol": -1.0}), fixture_facts())
    assert not next(x for x in check_gates(never) if x.name == "ni_jump").fired


# ------------------------------------------------------------------ C9, B3, C4


def test_share_classes_key_not_the_ticker_order_drives_market_cap():
    run = fixture_run()
    af = run.ctx.prep.af
    prices = run.ctx.live.prices
    classes = run.ctx.book.get("meta.company.share_classes")
    want = sum(float(prices[t]) * (af.n_out_ordinary if classes[t] == "ordinary" else af.n_out_preferred) / 1000
               for t in classes)
    assert market_cap(run) == pytest.approx(want)
    data = fixture_dict()
    data["meta"]["company"]["tickers"] = list(reversed(data["meta"]["company"]["tickers"]))
    swapped = run_grid(book_from_dict(data, facts=fixture_facts()), fixture_facts())
    assert market_cap(swapped) == pytest.approx(want)                       # порядок тикеров класс не задаёт
    data["meta"]["company"]["share_classes"] = {t: ("preferred" if c == "ordinary" else "ordinary")
                                                for t, c in classes.items()}
    other = run_grid(book_from_dict(data, facts=fixture_facts()), fixture_facts())
    assert market_cap(other) != pytest.approx(want)


def test_dividend_register_gap_after_the_agm_quarter_end():
    book = fixture_book()
    tl = make_timeline(book)
    agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    end = tl.end(agm_q)
    assert register_gaps(book, [], end) == []                              # в сам день конца — ещё нет
    gaps = register_gaps(book, [], end + timedelta(days=1))
    assert len(gaps) == 1 and str(tl.anchor_year) in gaps[0]
    declared = [{"year": tl.anchor_year, "status": "declared", "dps": 40.0}]
    assert register_gaps(book, declared, end + timedelta(days=1)) == []
    recommended = [{"year": tl.anchor_year, "status": "recommended", "dps": 40.0}]
    assert register_gaps(book, recommended, end + timedelta(days=1)) != []


def test_band_checks_ddm_equals_ri_in_every_draw():
    book, facts = fixture_book(), fixture_facts()
    run = fixture_run()
    band = U.band(book, facts, run.ctx.live, draws=4, workers=1)
    tol = float(book.get("checks.ddm_ri_tol"))
    assert 0.0 <= band.ddm_ri_max < tol
    assert band.ddm_ri_max == max(r["ddm_ri"] for r in band.rows)
    f = next(x for x in check_invariants(run, band) if x.name == "ddm_equals_ri")
    assert not f.fired and f.detail["band_worst"] == band.ddm_ri_max
    broken = copy.copy(band)
    object.__setattr__(broken, "ddm_ri_max", 10 * tol)
    assert next(x for x in check_invariants(run, broken) if x.name == "ddm_equals_ri").fired


def test_average_ranks_on_ties():
    assert U.ranks([3.0, 1.0, 2.0, 2.0]) == [4.0, 1.0, 2.5, 2.5]
    assert U.ranks([5.0, 5.0, 5.0]) == [2.0, 2.0, 2.0]


def test_cir_is_compared_with_guidance_in_the_mgmt_basis():
    run = fixture_run()
    br = run.ctx.bridge
    assert br.to_mgmt_cir(br.to_engine_cir(0.3)) == pytest.approx(0.3)
    f = next(x for x in check_gates(run) if x.name == "guidance_gap")
    year = int(run.ctx.facts.plain("guidance", "year"))
    i = run.cells[0].years.index(year)
    lo, hi = f.detail["bands"]["cir"]
    mgmt = [br.to_mgmt_cir(c.annual["cir"][i]) for c in run.cells]
    cir_out = [v for v in mgmt if not lo <= v <= hi]
    assert ("cir" in f.detail["outside"]) == bool(cir_out)
