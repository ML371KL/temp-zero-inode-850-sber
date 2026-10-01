"""Волна W3 (второй аудит, LEAD-DECISIONS-4): аналитические тесты М§18 на фикстуре — раскладка сжатия φ
(`nii.phi_split`), кредитная маржа миров и тест знака, одностороннее угасание, порог ликвидности терминала,
опора FVC до розыгрыша, пол вероятности режима, год с отчётными кварталами, дивиденд без записи реестра,
единица оси, оценщик срединных прогонов, нейтральное значение по двум допускам, выплата по дате записи."""

from __future__ import annotations

import copy
import math
from datetime import timedelta

import pytest

from model import uncertainty as U
from model.book import anchor_facts, book_from_dict, book_roles, load_facts
from model.book_schema import UNIT_CODES, BookError, FactsError
from model.checks import check_gates, check_invariants, guidance_values, in_cells, in_quarters, grouped, sci
from model.credit import Bridge, bridge_from_facts, mgmt_history, year_in_mgmt
from model.grid import (DividendRecord, LiveInputs, cap_shift, derived_values, floor_shift, make_context, modal_cell,
                        realized_cells, run_cell, run_grid, sensitivity_overrides, unregistered_dividend,
                        volume_sign_test, year_mgmt)
from model.nii import solve_transmission
from model.paths import Trajectory
from model.timeline import make_timeline
from model.valuation import terminal
from tests.support_core import (FIXTURE_FACTS, book_with, fixture_book, fixture_dict, fixture_facts, fixture_run,
                                real_book_or_skip)

pytestmark = pytest.mark.tact


def _fails(data, *fragments):
    with pytest.raises(BookError) as exc:
        book_from_dict(data, facts=fixture_facts())
    for fr in fragments:
        assert fr in str(exc.value), str(exc.value)


def _weights(book, facts):
    books = book.get("nii.books")
    af = anchor_facts(facts, books)
    roles = book_roles(books)
    iea = sum(af.balances[b] for b in roles.assets)
    return books, roles, {b: af.balances[b] / iea for b in roles.names}


def _lt(book, world):
    """Опорные ставки мира в last_period — из траекторий книги, независимо от ядра."""
    last = str(book.get("meta.last_period"))
    y, h = int(last[:4]), int(last[-1])
    return {ref: Trajectory(book.get(f"worlds.{world}.key_rate" if ref == "key" else f"worlds.{world}.{ref}")).value(y, h)
            for ref in ("key", "ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y")}


def _live(book, v, register=()):
    prices = {str(t): float(p) for t, p in book.get("meta.market_price").items()}
    return LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=tuple(register))


# ------------------------------------------------------------------ В12: раскладка сжатия φ


def test_phi_split_divides_the_compression_between_loans_and_client_funds():
    """М§4.5: φ_A = p × φ, φ_L = (1 − p) × φ × A / L; добавка пассивов в стационаре равна остатку сжатия; Nss,
    T_real, T(W) и парные передачи от доли не зависят."""
    book, facts = fixture_book(), fixture_facts()
    bridge = bridge_from_facts(facts)
    books, roles, w = _weights(book, facts)
    phi_a = [b for b in roles.assets if books[b].get("phi")]
    phi_l = [b for b in roles.liabilities if books[b].get("phi")]
    assert set(phi_l) == {"retail_current", "retail_term", "corp_funds"} and "phi" not in books["wholesale"]
    a_sum, l_sum = sum(w[b] for b in phi_a), sum(w[b] for b in phi_l)
    ref_w = str(book.get("nii.transmission.reference_world"))
    rh = _lt(book, ref_w)
    base = solve_transmission(book, facts, bridge)
    for p in (1.0, 0.5, 0.09, 0.0):
        tr = solve_transmission(book.with_overrides({"nii.phi_split": p}), facts, bridge)
        assert tr.phi_split == p
        assert tr.phi_assets == pytest.approx(p * tr.phi, abs=1e-15)
        assert tr.phi_liab == pytest.approx((1 - p) * tr.phi * a_sum / l_sum, abs=1e-15)
        # объём сжатия мира один при любой раскладке: всё, что определено по φ целиком, совпадает точно
        assert (tr.phi, tr.sigma0, tr.sigma0_liab, tr.t_real) == (base.phi, base.sigma0, base.sigma0_liab, base.t_real)
        assert dict(tr.nss) == dict(base.nss) and dict(tr.pairs) == dict(base.pairs)
        assert dict(tr.t_local) == dict(base.t_local)
        assert tr.t_real == pytest.approx(float(book.get("nii.transmission.target")), abs=1e-12)
        for world in book.get("worlds.ids"):
            rw = _lt(book, world)
            c_w = sum(w[b] * (rw[books[b]["ref"]] - rh[books[b]["ref"]]) for b in phi_a)
            assert tr.x_lt[world] == pytest.approx(c_w / a_sum, abs=1e-15)
            # добавка пассивов L × φ_L × X_W = остаток сжатия (1 − p) × φ × C_W
            assert l_sum * tr.phi_liab * tr.x_lt[world] == pytest.approx((1 - p) * tr.phi * c_w, abs=1e-15)
        assert sum(tr.phi_weights.values()) == pytest.approx(1.0, abs=1e-15)
        assert tr.phi_weights == pytest.approx({b: w[b] / a_sum for b in phi_a}, abs=1e-15)
    d = derived_values(make_context(book, facts))["nii"]
    assert {"phi", "phi_assets", "phi_liab", "phi_split", "loan_margin"} <= set(d)


def test_phi_split_one_is_bitwise_the_calculation_without_the_split():
    """М§18: при `nii.phi_split` = 1 расчёт совпадает бит в бит с расчётом без раскладки — флаг `phi` у пассивов
    при доле 1 ничего не меняет ни в одном ряду ни одной клетки."""
    facts = fixture_facts()
    one = fixture_book().with_overrides({"nii.phi_split": 1.0})
    data = fixture_dict()
    data["nii"]["phi_split"] = 1.0
    for spec in data["nii"]["books"].values():
        if spec["side"] == "liability":
            spec.pop("phi", None)                         # расчёт «без раскладки»: сжатие несут только активы
    plain = book_from_dict(data, facts=facts)
    a, b = run_grid(one, facts), run_grid(plain, facts)
    assert a.ctx.transmission.phi_assets == a.ctx.transmission.phi and a.ctx.transmission.phi_liab == 0.0
    assert (a.point, a.low, a.high) == (b.point, b.low, b.high)
    for ca, cb in zip(a.cells, b.cells):
        assert ca.v_ri == cb.v_ri and ca.v_ddm == cb.v_ddm
        assert ca.quarters == cb.quarters


def test_liability_rate_carries_the_remainder_of_the_compression():
    """М§4.4: c_q = c_(q−1) + ρ (β ref_W,q + s(q) + φ_L·[b ∈ Φ_L]·X_W,q − c_(q−1)); X_W,q — разность опорных
    ставок кредитных книг Φ_A мира и мира H с весами якоря; актив — со сжатием φ_A; в мире H добавки нет."""
    run = fixture_run()
    book, facts, tl = run.ctx.book, run.ctx.facts, run.ctx.timeline
    books, roles, w = _weights(book, facts)
    tr = run.ctx.transmission
    assert tr.phi_liab > 0 and tr.phi_assets > 0
    ref_w = str(book.get("nii.transmission.reference_world"))
    phi_a = [b for b in roles.assets if books[b].get("phi")]
    a_sum = sum(w[b] for b in phi_a)
    spreads = run.ctx.prep.spreads
    wh = run.ctx.worlds[ref_w]
    for world in book.get("worlds.ids"):
        ww = run.ctx.worlds[world]
        c = run.cell(world, "downturn", "upper")
        for q in range(1, tl.Q + 1):
            x_q = sum(w[b] / a_sum * (ww.ref(books[b]["ref"])[q] - wh.ref(books[b]["ref"])[q]) for b in phi_a)
            if world == ref_w:
                assert x_q == 0.0
            for b in roles.names:
                spec = books[b]
                ref = ww.ref(spec["ref"])[q]
                prev = c.books[b]["rate"][q - 1]
                if spec["side"] == "asset":
                    target = ref + spreads[b][q] - (tr.phi_assets * (ref - wh.ref(spec["ref"])[q]) if spec.get("phi") else 0.0)
                else:
                    target = float(spec["beta"]) * ref + spreads[b][q] + (tr.phi_liab * x_q if spec.get("phi") else 0.0)
                assert c.books[b]["rate"][q] == pytest.approx(prev + float(spec["rho"]) * (target - prev), abs=1e-13), (b, q)


def test_realized_transmission_holds_for_any_split():
    """A-N3: реализованная передача = цель при любой доле; передача по клеткам (M − N последнего года) от
    раскладки почти не зависит — меняется, кто несёт сжатие, а не его объём."""
    book, facts = fixture_book(), fixture_facts()
    cells = {}
    for p in (1.0, 0.0):
        run = run_grid(book.with_overrides({"nii.phi_split": p}), facts)
        inv = {f.name: f for f in check_invariants(run)}
        assert not inv["transmission_solved"].fired
        cells[p] = realized_cells(run)
    assert cells[0.0] == pytest.approx(cells[1.0], abs=0.01)


def test_phi_split_schema_rules():
    book = fixture_book()
    assert book.get("nii.phi_split") == 0.5
    _fails(book_with({"nii.phi_split": 1.2}), "phi_split", "от 0 до 1")
    _fails(book_with({"nii.phi_split": -0.1}), "phi_split")
    _fails(book_with({}, drop=("nii.phi_split",)), "phi_split", "нет ключа")
    data = fixture_dict()
    for spec in data["nii"]["books"].values():
        if spec["side"] == "liability":
            spec.pop("phi", None)
    _fails(data, "пассивов с phi нет")
    data["nii"]["phi_split"] = 1.0
    book_from_dict(data, facts=fixture_facts())                   # всё сжатие на активах — допустимо
    _fails(book_with({"nii.books.retail_term.phi": False}), "retail_current", "retail_term", "флаг одинаков")
    data = fixture_dict()
    for name in ("corp_loans", "mortgage", "retail_loans"):
        data["nii"]["books"][name]["phi"] = False
    _fails(data, "активов с phi нет")
    book_from_dict(book_with({"nii.books.wholesale.phi": False}), facts=fixture_facts())   # явный флаг у опта


def test_loan_margin_and_the_spread_floor_follow_the_asset_share():
    """М§4.5: кредитная маржа стационара m_W = доходность кредитов − CoR режима `norm` с κ-добавкой мира −
    смесь балансирующих активов; эффективный LT-спред кредитной книги сжимается только долей φ_A — при p = 0
    спреды к опоре одинаковы во всех мирах."""
    book, facts = fixture_book(), fixture_facts()
    bridge = bridge_from_facts(facts)
    books, roles, w = _weights(book, facts)
    last = str(book.get("meta.last_period"))
    y, h = int(last[:4]), int(last[-1])
    ref_w = str(book.get("nii.transmission.reference_world"))
    kappa = float(book.get("credit.kappa"))
    sec = float(book.get("volumes.securities_share_of_liquid"))
    cor_norm = bridge.to_engine_cor(Trajectory(book.get("regimes.norm.cor")).value(y, h))
    real = {x: Trajectory(book.get(f"worlds.{x}.real_key")).value(y, h) for x in book.get("worlds.ids")}
    margins = {}
    for p in (1.0, 0.5, 0.0):
        tr = solve_transmission(book.with_overrides({"nii.phi_split": p}), facts, bridge)
        rh = _lt(book, ref_w)
        for world in book.get("worlds.ids"):
            rw = _lt(book, world)

            def rate(b):
                spec = books[b]
                s = Trajectory(spec["spread"]).value(y, h) + (tr.sigma0 if spec["lt_shift"] else 0.0)
                if spec["phi"]:
                    s -= tr.phi_assets * (rw[spec["ref"]] - rh[spec["ref"]])
                return rw[spec["ref"]] + s

            total = sum(w[b] for b in roles.loans)
            want = (sum(w[b] * rate(b) for b in roles.loans) / total
                    - (cor_norm + kappa * max(0.0, real[world] - real["N"]))
                    - (sec * rate("securities") + (1 - sec) * rate("liquidity")))
            assert tr.loan_margin[world] == pytest.approx(want, abs=1e-15), (p, world)
        margins[p] = dict(tr.loan_margin)
        if p == 0.0:
            for b, row in tr.lt_spread.items():
                assert len({round(v, 15) for v in row.values()}) == 1, b
    assert margins[0.0][ref_w] == margins[1.0][ref_w]                 # в мире H сжатия нет
    assert margins[0.0]["M"] > margins[0.5]["M"] > margins[1.0]["M"]  # маржа мира M растёт, когда сжатие уходит с кредитов
    assert margins[1.0]["M"] < 0 < margins[0.0]["M"]                  # на фикстуре: при p = 1 кредит в M убыточен


def test_sign_test_of_the_volume_effects():
    """М§4.5: снятие остановки кредита в кризисе и поправок роста режимов. На фикстуре (не откалибрована)
    проверяется механизм: чем меньше доля сжатия на кредитах, тем лучше мир M против мира H; при p = 0 мир M
    не хуже мира H. На настоящей книге тест знака обязан выполняться целиком."""
    book, facts = fixture_book(), fixture_facts()
    gap = {}
    for p in (1.0, 0.5, 0.0):
        b2 = book.with_overrides({"nii.phi_split": p})
        st = volume_sign_test(b2, facts)
        assert set(st) >= {"point", "point_free", "d_point", "d_world", "ok"}
        assert st["d_point"] == pytest.approx(st["point_free"] - st["point"], abs=1e-12)
        gap[p] = st["d_world"]["M"] - st["d_world"]["H"]
        free = b2.with_overrides({**{f"regimes.{r}.loan_growth_adj": 0.0 for r in b2.get("regimes.ids")},
                                  "regimes.crisis.loan_growth_override": {}})
        assert run_grid(free, facts).point == pytest.approx(st["point_free"], rel=1e-12)
    assert gap[0.0] > gap[0.5] > gap[1.0] and gap[0.0] >= 0 > gap[1.0]


def test_sign_test_holds_on_the_real_book():
    book, facts = real_book_or_skip()
    run = run_grid(book, facts)
    st = volume_sign_test(book, facts, run=run)
    assert st["ok"], st
    assert st["d_point"] >= 0 and st["d_world"]["M"] >= st["d_world"]["H"]
    assert min(run.ctx.transmission.loan_margin.values()) >= 0, run.ctx.transmission.loan_margin
    gate = next(x for x in check_gates(run) if x.name == "lt_spread_floor")
    assert not gate.fired, gate.message                              # гейт проходит во всех мирах, включая M


# ------------------------------------------------------------------ В13: одностороннее угасание


def test_fade_is_one_sided():
    """М§7: ROE'_T = k_T + fade × max(ROE_T − k_T, 0) + min(ROE_T − k_T, 0): при ROE_T ≥ k_T — прежняя формула,
    при ROE_T < k_T множитель равен 1; fade < 1 не повышает оценку ни в одной клетке; DDM = RI держится."""
    kw = dict(n20=0.16, req20=0.133, n11_star=0.2, req11=0.08, rwa=100000.0, bv_q=15000.0, y_balancing=0.07,
              c_wholesale=0.12, headroom=1e9, tau_eff=0.24, nci_share=0.01, coupon_annual=9.7, tau_stat=0.25,
              lt_inflation=0.05, real_growth=0.015, multiple=1.0)
    rich = terminal(pbt_last_year=3500.0, k_t=0.16, fade=0.5, **kw)
    assert rich.roe_raw > rich.k_t and rich.roe_t == rich.k_t + 0.5 * (rich.roe_raw - rich.k_t)
    poor = terminal(pbt_last_year=1500.0, k_t=0.16, fade=0.5, **kw)
    assert poor.roe_raw < poor.k_t and poor.roe_t == poor.roe_raw            # сходимость снизу не предполагается
    same = terminal(pbt_last_year=1500.0, k_t=0.16, fade=1.0, **kw)
    assert (poor.tv_ri, poor.tv_ddm) == (same.tv_ri, same.tv_ddm)
    for t in (rich, poor):
        assert t.tv_ddm == pytest.approx(t.x_t + t.bv_star + t.tv_ri, rel=1e-12)    # DDM = RI на терминале
    book, facts = fixture_book(), fixture_facts()
    base = fixture_run()
    faded = run_grid(book.with_overrides({"valuation.terminal.fade": 0.5}), facts)
    below = above = 0
    for a, b in zip(base.cells, faded.cells):
        assert b.v_ri <= a.v_ri + 1e-9 * abs(a.v_ri), a.label                # ни одна клетка не растёт
        if a.roe_t_raw < a.k_t:
            assert b.roe_t == b.roe_t_raw and b.v_ri == pytest.approx(a.v_ri, rel=1e-12)
            below += 1
        else:
            assert b.roe_t == pytest.approx(b.k_t + 0.5 * (b.roe_t_raw - b.k_t), abs=1e-15)
            above += b.v_ri < a.v_ri
    assert below > 0 and above > 0                                           # на фикстуре есть клетки обоих видов
    assert faded.point < base.point
    assert not [f.name for f in check_invariants(faded) if f.fired]


# ------------------------------------------------------------------ В14: опора FVC — путь книги до розыгрыша


def test_fvc_reference_is_the_book_path_before_the_draw():
    """М§4.6: сдвиг путей CoR всех режимов на x меняет FVC каждой клетки на factor × x × Ē^СС × d/365 — опора
    стоит на значениях книги, и сдвиг уровня доходит до кредитов по СС."""
    book, facts = fixture_book(), fixture_facts()
    base = fixture_run()
    tl = base.ctx.timeline
    factor = float(book.get("credit.fv_loans_factor"))
    assert book.base is None
    for x in (0.001, -0.002):
        b2 = book.with_overrides(sensitivity_overrides(book, "cor", x))
        assert b2.base is book
        shifted = run_grid(b2, facts)
        assert shifted.ctx.prep.fv_ref_cor == base.ctx.prep.fv_ref_cor       # опора не сдвинулась
        for a, b in zip(base.cells, shifted.cells):
            for q in range(1, tl.Q + 1):
                want = factor * x * a.quarters["loans_fv_avg"][q] * tl.d(q) / 365
                assert b.quarters["fvc"][q] - a.quarters["fvc"][q] == pytest.approx(want, abs=1e-9), (a.label, q)
                assert b.quarters["llp"][q] - a.quarters["llp"][q] == pytest.approx(
                    x * a.quarters["loans_ac_avg"][q] * tl.d(q) / 365, abs=1e-9)
    # копия копии помнит ту же исходную книгу; ось уровня и полоса идут через тот же путь
    twice = book.with_overrides({"valuation.erp": 0.06}).with_overrides({"regimes.norm.cor.LT": 0.02})
    assert twice.base is book
    axis = next(a for a in book.get("valuation.uncertainty.axes") if "regimes.norm.cor.LT" in a["paths"])
    drawn = U.trial_book(book, U.axis_overrides(book, axis, 1.0))
    assert drawn.base is book
    hi = run_grid(drawn, facts)
    assert hi.ctx.prep.fv_ref_cor == base.ctx.prep.fv_ref_cor
    ref = str(book.get("credit.fv_loans_ref"))
    assert sum(hi.cell("N", ref, "schedule").quarters["fvc"][1:]) > 0        # клетка опоры при сдвиге оси несёт расход
    assert all(v == 0.0 for v in base.cell("N", ref, "schedule").quarters["fvc"][1:])   # при значениях книги — ноль
    # книга, собранная заново, — сама себе база
    again = book_from_dict(dict(drawn.source), facts=facts)
    assert again.base is None
    assert all(v == 0.0 for v in run_grid(again, facts).cell("N", ref, "schedule").quarters["fvc"][1:])


# ------------------------------------------------------------------ В15: пол вероятности режима


def _obs(period, cor, **extra):
    return {"period": period, "cor": cor, "nim": None, "se_cor": 0.0, "se_nim": None, "basis": "mgmt", **extra}


def test_floor_shift_closed_form():
    prior = {"soft": 0.15, "norm": 0.40, "downturn": 0.30, "crisis": 0.15}
    cur = {"soft": 0.01, "norm": 0.45, "downturn": 0.50, "crisis": 0.04}
    out = floor_shift(prior, cur, 0.33)
    assert out["soft"] == pytest.approx(0.0495) and out["crisis"] == pytest.approx(0.0495)   # ниже пола — на пол
    rest = 1 - 2 * 0.0495
    assert out["norm"] == pytest.approx(0.45 * rest / 0.95) and out["downturn"] == pytest.approx(0.50 * rest / 0.95)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-15)
    assert floor_shift(prior, cur, 0.0) == cur                              # 0 — пола нет
    assert floor_shift(prior, cur, 1.0) == pytest.approx(prior, abs=1e-15)  # 1 — обучение выключено
    assert floor_shift(prior, prior, 0.33) == prior                         # выше пола — ничего не меняется
    # режим, ушедший ниже пола после умножения остальных, тоже ставится на пол (шаг повторяется)
    cur2 = {"soft": 0.0, "norm": 0.134, "downturn": 0.766, "crisis": 0.1}
    out2 = floor_shift(prior, cur2, 0.33)
    assert all(out2[r] >= 0.33 * prior[r] - 1e-15 for r in prior) and sum(out2.values()) == pytest.approx(1.0)
    assert out2["soft"] == pytest.approx(0.0495) and out2["norm"] == pytest.approx(0.132)


def test_regime_probability_never_falls_below_the_floor():
    """М§12: после любого набора наблюдений P(r) ≥ floor_share × базовой, Σ = 1; при `floor_share` = 0 —
    прежний результат (фильтр с окном и пределом сдвига)."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    share = float(book.get("joint.regime_update.floor_share"))
    assert share == 0.33
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
    limit = float(book.get("joint.regime_update.max_shift_pp"))
    for values in ((0.045, 0.045, 0.045, 0.045), (0.004, 0.004, 0.004, 0.004), (0.012, 0.03, 0.005, 0.04)):
        obs = [_obs(tl.period(q), v) for q, v in zip(range(1, 5), values)]
        ctx = make_context(book.with_overrides({"joint.regime_update.observations": obs}), facts)
        for u in ctx.updates:
            post = u["posterior_after"]
            assert sum(post.values()) == pytest.approx(1.0, abs=1e-12)
            assert all(post[r] >= share * prior[r] - 1e-15 for r in prior), (values, u["period"])
        assert ctx.posterior == ctx.updates[-1]["posterior_after"]
    crisis = [_obs(tl.period(q), 0.045) for q in range(1, 5)]
    ctx = make_context(book.with_overrides({"joint.regime_update.observations": crisis}), facts)
    assert ctx.posterior["soft"] == pytest.approx(share * prior["soft"], abs=1e-12)        # пол связал
    free = make_context(book.with_overrides({"joint.regime_update.observations": crisis,
                                             "joint.regime_update.floor_share": 0.0}), facts)
    assert free.posterior["soft"] < share * prior["soft"]
    # при floor_share = 0 — прежний фильтр: шаг за шагом только правдоподобие и предел сдвига
    one = make_context(book.with_overrides({"joint.regime_update.observations": crisis[:1],
                                            "joint.regime_update.floor_share": 0.0}), facts)
    assert max(abs(one.posterior[r] - prior[r]) for r in prior) <= limit + 1e-12
    capped = cap_shift(prior, {r: (1.0 if r == "crisis" else 0.0) for r in prior}, limit)
    assert one.posterior["crisis"] == pytest.approx(capped["crisis"], abs=1e-9)
    _fails(book_with({"joint.regime_update.floor_share": 1.5}), "floor_share", "от 0 до 1")
    _fails(book_with({}, drop=("joint.regime_update.floor_share",)), "floor_share", "нет ключа")


def test_frozen_expectations_are_never_recomputed():
    """М§12: замороженные μ записи ядро не пересчитывает — ни при смене путей режимов книги, ни при смене года
    шока (перезаякоривание со сменой года якоря): наблюдение квартала не позже якоря судится против записи."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    mu = {"soft": 0.011, "norm": 0.013, "downturn": 0.016, "crisis": 0.0165}
    ob = [_obs(tl.anchor, 0.0125, mu_cor=mu)]
    base = make_context(book.with_overrides({"joint.regime_update.observations": ob}), facts)
    moved = sensitivity_overrides(book, "cor", 0.004)                        # пути CoR всех режимов — другие
    other = make_context(book.with_overrides({**moved, "joint.regime_update.observations": ob}), facts)
    assert other.posterior == base.posterior
    assert other.expectations["frozen"] == base.expectations["frozen"]
    assert other.updates[0]["mu"] == base.updates[0]["mu"]
    sigma = float(book.get("joint.regime_update.observables.cor.sigma_pp"))
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in mu}
    like = {r: math.exp(-0.5 * (0.0125 - mu[r]) ** 2 / sigma ** 2) for r in mu}
    norm = sum(prior[r] * like[r] for r in mu)
    want = cap_shift(prior, {r: prior[r] * like[r] / norm for r in mu}, float(book.get("joint.regime_update.max_shift_pp")))
    assert base.posterior == pytest.approx(floor_shift(prior, want, 0.33), abs=1e-12)


# ------------------------------------------------------------------ № 79: год с отчётными кварталами


def test_year_in_mgmt_is_the_weighted_average_of_quarters():
    """М§4.6: x_упр,Y = Σ w_q × x_упр,q; отчётный квартал — упр. факт истории моста, прогнозный — мост; отчётный
    квартал без записи истории — отказ, не ноль."""
    facts = fixture_facts()
    bridge = Bridge(cor_method="additive", cor_value=0.002, nim_method="additive", nim_value=-0.004,
                    cir_method="ratio", cir_value=0.9)
    anchor = str(facts.plain("anchor", "period"))
    year = int(anchor[:4])
    hist = mgmt_history(facts, "cor")
    quarters = [(f"{year}Q1", None, 100.0), (f"{year}Q2", 0.5, 200.0), (f"{year}Q3", 0.016, 300.0),
                (f"{year}Q4", 0.018, 400.0)]
    want = (100 * hist[f"{year}Q1"] + 200 * hist[f"{year}Q2"] + 300 * (0.016 - 0.002) + 400 * (0.018 - 0.002)) / 1000
    assert year_in_mgmt(bridge, facts, "cor", quarters) == pytest.approx(want, abs=1e-15)
    # при аддитивном мосте: to_mgmt(год движка) − Σ_отчётные w_q × (gap_q − b)
    gaps = {str(r["period"]): float(r["gap"]["v"]) for r in facts.file("bridge_mgmt_ifrs")["cor"]["history"]}
    eng = {f"{year}Q1": hist[f"{year}Q1"] + gaps[f"{year}Q1"], f"{year}Q2": hist[f"{year}Q2"] + gaps[f"{year}Q2"]}
    full = [(p, eng.get(p, v), z) for p, v, z in quarters]
    year_eng = sum(z * v for _, v, z in full) / 1000
    alt = (year_eng - 0.002) - sum(z / 1000 * (gaps[p] - 0.002) for p, _, z in full if p in gaps)
    assert year_in_mgmt(bridge, facts, "cor", full) == pytest.approx(alt, abs=1e-15)
    # мост-отношение у прогнозного квартала
    cir = year_in_mgmt(bridge, facts, "cir", [(f"{year + 1}Q1", 0.27, 1.0), (f"{year + 1}Q2", 0.36, 3.0)])
    assert cir == pytest.approx((0.27 / 0.9 + 3 * 0.36 / 0.9) / 4)
    with pytest.raises(FactsError, match="нет упр. факта отчётного квартала"):
        year_in_mgmt(bridge, facts, "cor", [(f"{year - 3}Q1", 0.01, 1.0)])
    with pytest.raises(FactsError):
        year_in_mgmt(bridge, facts, "roe", quarters)
    with pytest.raises(FactsError):
        year_in_mgmt(bridge, facts, "cor", [(f"{year}Q4", None, 1.0)])     # прогнозный квартал без значения движка


def test_guidance_gate_takes_the_reported_quarters_from_the_disclosed_fact():
    """М§14.2: путь года гайденса — отчётные кварталы раскрытым упр. фактом, прогнозные — мостом; та же
    функция — у гейта и у строки гайденса выпуска; год без отчётных кварталов — средний мост."""
    run = fixture_run()
    ctx, tl = run.ctx, run.ctx.timeline
    facts, br = ctx.facts, ctx.bridge
    year = tl.anchor_year
    hist = ctx.prep.hist_bal
    c = run.cell("M", "downturn", "strict")
    got = guidance_values(run, c, year)
    ac0 = ctx.prep.anchor_state["loans"] - ctx.prep.af.fv_loans
    iea0 = sum(ctx.prep.af.balances[b] for b in ctx.prep.roles.assets)
    mg = {m: mgmt_history(facts, m) for m in ("cor", "nim", "cir")}
    acc = {m: [0.0, 0.0] for m in mg}
    for q in tl.quarters_of_year(year):
        d = tl.d(q)
        if q <= 0:
            ac = [ac0 if j == 0 else hist[j]["loans_ac"] for j in (q - 1, q)]
            iea = [iea0 if j == 0 else hist[j]["iea"] for j in (q - 1, q)]
            z = {"cor": sum(ac) / 2 * d, "nim": sum(iea) / 2 * d,
                 "cir": sum(ctx.prep.hist[k][q] for k in ("nii", "fees", "ins", "misc"))}
            x = {m: mg[m][tl.period(q)] for m in mg}
        else:
            inc = sum(c.quarters[k][q] for k in ("nii", "fees", "ins", "misc", "fvr"))
            z = {"cor": c.quarters["loans_ac_avg"][q] * d, "nim": c.quarters["iea_avg"][q] * d, "cir": inc}
            x = {"cor": br.to_mgmt_cor(c.quarters["llp"][q] * 365 / d / c.quarters["loans_ac_avg"][q]),
                 "nim": br.to_mgmt_nim(c.quarters["nii"][q] * 365 / d / c.quarters["iea_avg"][q]),
                 "cir": br.to_mgmt_cir(c.quarters["opex"][q] / inc)}
        for m in mg:
            acc[m][0] += z[m] * x[m]
            acc[m][1] += z[m]
    assert got["cor_max"] == pytest.approx(acc["cor"][0] / acc["cor"][1], abs=1e-12)
    assert got["nim"] == pytest.approx(acc["nim"][0] / acc["nim"][1], abs=1e-12)
    assert got["cir"] == pytest.approx(acc["cir"][0] / acc["cir"][1], abs=1e-12)
    assert got["roe"] == c.annual["roe"][0]
    # разрыв отчётных кварталов — свой, не средний: год не равен «год движка минус средний мост»
    assert got["cor_max"] != pytest.approx(br.to_mgmt_cor(c.annual["cor"][0]), abs=1e-5)
    nxt = guidance_values(run, c, year + 1)                                   # год без отчётных кварталов
    assert nxt["cor_max"] == br.to_mgmt_cor(c.annual["cor"][1]) and nxt["nim"] == br.to_mgmt_nim(c.annual["nim"][1])
    gate = next(x for x in check_gates(run) if x.name == "guidance_gap")
    bands = gate.detail["bands"]
    bad = [x.label for x in run.cells
           if any(v is not None and k in bands and not (
               (bands[k][0] is None or v >= bands[k][0]) and (bands[k][1] is None or v <= bands[k][1]))
                  for k, v in guidance_values(run, x, year).items())]
    assert sorted(gate.cells) == sorted(bad)


def test_reported_quarter_without_the_bridge_history_is_a_refusal(tmp_path):
    """Нет записи истории моста за отчётный квартал года якоря — `FactsError` у гейта, а не ноль и не средний мост."""
    import json
    import shutil
    root = tmp_path / "facts"
    shutil.copytree(FIXTURE_FACTS, root)
    path = root / "bridge_mgmt_ifrs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cor"]["history"] = data["cor"]["history"][1:]                       # первый отчётный квартал года — без записи
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    facts = load_facts(root)
    run = run_grid(book_from_dict(fixture_dict(), facts=facts), facts)        # сетке история не нужна
    with pytest.raises(FactsError, match="история моста обязана нести"):
        check_gates(run)
    row = lambda name, q: run.cells[0].quarters[name][q]                      # noqa: E731
    with pytest.raises(FactsError):
        year_mgmt(run.ctx, "cor", run.ctx.timeline.anchor_year, 0.014, row)
    assert year_mgmt(run.ctx, "nim", run.ctx.timeline.anchor_year, 0.06, row) is not None    # у ЧПМ история цела


# ------------------------------------------------------------------ № 87: дивиденд без записи реестра


def test_unregistered_dividend_is_what_the_cells_deducted_without_a_record():
    """М§14.4: U — ожидание дивиденда, вычтенного клетками на закрытых концах кварталов ГОСА, за год которых в
    реестре нет записи declared | paid; с записью U = 0 (дивиденд — в мосте)."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    end = tl.end(agm_q)
    before, on = (run_grid(book, facts, _live(book, end + timedelta(days=d))) for d in (-1, 0))
    assert before.unregistered_dividend == 0.0
    prob = on.layers["analytical"].prob
    want = sum(prob[c.key] * c.quarters["div"][agm_q] for c in on.cells)
    assert want > 0 and on.unregistered_dividend == pytest.approx(want, rel=1e-12)
    # сумма V0 + B + U непрерывна на конце квартала ГОСА и без записи реестра
    total = lambda r: r.layers["analytical"].v0 + r.bridge_amount + r.unregistered_dividend    # noqa: E731
    assert total(on) == pytest.approx(total(before), rel=2e-3)
    assert on.layers["analytical"].v0 < before.layers["analytical"].v0 - 0.8 * want             # один V0 падает
    mix = unregistered_dividend(on.ctx, on.cells, on.layers["macro_neutral"].prob)
    assert mix > 0 and mix != pytest.approx(on.unregistered_dividend, rel=1e-6)                 # слой — свой
    ex = end + timedelta(days=18)
    for status, expect_zero in (("declared", True), ("paid", True), ("recommended", False)):
        rec = DividendRecord(year=tl.anchor_year, dps=40.0, status=status, record_date=ex,
                             last_buy_date=ex - timedelta(days=1), ex_date=ex, pay_date=ex + timedelta(days=14),
                             sources=("тест", "тест 2"))
        u = run_grid(book, facts, _live(book, end, (rec,))).unregistered_dividend
        assert (u == 0.0) == expect_zero, status
    later = run_grid(book, facts, _live(book, tl.end(agm_q + 4)))            # два года ГОСА без записей
    assert later.unregistered_dividend > on.unregistered_dividend


# ------------------------------------------------------------------ М§5.4: выплата — по дате записи реестра


def test_declared_dividend_is_paid_in_the_quarter_of_its_pay_date():
    """М§5.4: у объявленного дивиденда даты записи сильнее календаря книги — вычет из регуляторного капитала в
    квартале отсечки, выплата (дивиденды к выплате) — в квартале `pay_date`; `recommended` календарь не меняет."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    assert int(book.get("dividends.calendar.payment_quarter")) == tl.h(agm_q + 1)   # календарь книги: квартал после ГОСА
    record = tl.end(agm_q + 1) + timedelta(days=20)                                 # отсечка — на квартал позже календаря
    pay = tl.end(agm_q + 2) + timedelta(days=10)                                    # выплата — ещё через квартал
    v = tl.end(0) + timedelta(days=30)

    def cell(status, pay_date):
        rec = DividendRecord(year=tl.anchor_year, dps=40.0, status=status, record_date=record,
                             last_buy_date=record - timedelta(days=1), ex_date=record, pay_date=pay_date,
                             sources=("тест", "тест 2"))
        return run_cell(make_context(book, facts, _live(book, v, (rec,))), "H", "norm", "schedule")

    c = cell("declared", pay)
    amount = 40.0 * facts.need("shares", "outstanding_total") / 1000
    q_rec, q_pay = tl.q_of_date(record), tl.q_of_date(pay)
    assert (q_rec, q_pay) == (agm_q + 2, agm_q + 3)
    assert c.quarters["div"][agm_q] == pytest.approx(amount, rel=1e-12)             # решение — в квартал ГОСА
    assert c.quarters["pay"][q_pay] == pytest.approx(amount, rel=1e-12)             # выплата — в квартале pay_date
    assert all(c.quarters["pay"][q] == 0.0 for q in range(agm_q, q_pay))
    assert all(c.quarters["dp"][q] >= amount - 1e-9 for q in range(agm_q, q_pay))   # до выплаты — к выплате
    assert all(c.quarters["dpreg"][q] == pytest.approx(amount) for q in range(agm_q, q_rec))
    assert c.quarters["dpreg"][q_rec] == 0.0                                        # вычет — в квартале отсечки
    no_date = cell("declared", None)                                               # нет даты выплаты — календарь книги
    assert no_date.quarters["pay"][agm_q + 1] == pytest.approx(amount, rel=1e-12)
    rec_only = cell("recommended", pay)                                            # рекомендация календарь не меняет
    assert rec_only.quarters["div"][agm_q] != pytest.approx(amount, rel=1e-6)
    assert rec_only.quarters["pay"][q_pay] != pytest.approx(rec_only.quarters["div"][agm_q], rel=1e-6) \
        or rec_only.quarters["pay"][agm_q + 1] > 0


# ------------------------------------------------------------------ № 99: единица оси


def test_axis_unit_comes_from_the_axis_field_then_from_its_paths():
    """М§10: единица оси — поле `unit` (код П§0.2); без него — по путям оси (сдвиг и ключи с `_pp` — п.п., сроки
    — годы, коэффициенты — число, суммы — млрд ₽), не по величине значения и не по словам имени."""
    book = fixture_book()
    axes = {a["name"]: a for a in book.get("valuation.uncertainty.axes")}
    unit = lambda name: U.axis_unit(book, axes[name])                             # noqa: E731
    assert axes["Срок подтягивания FVOCI"]["unit"] == "years" and unit("Срок подтягивания FVOCI") == "years"
    assert unit("Дюрация FVOCI") == "years" and unit("κ: CoR к реальной ставке сверх мира N (A-C2b)") == "number"
    by_path = {"regimes.crisis.nim_shift.2027": "pp", "capital.n20.gap_pp": "pp", "credit.kappa": "number",
               "capital.reg_scenarios.schedule.deduction_pp.n20_0.LT": "pp", "noncore.result_real.LT": "bn",
               "regimes.crisis.one_off_loss.amount": "bn", "oci.fvoci_maturity": "years", "valuation.beta_e": "number",
               "nii.retail_current_share.psi": "number", "tax.one_off.prob": "pct", "valuation.erp": "pct",
               "nii.nim_lt_target_mgmt": "pct", "nii.transmission.target": "number"}
    for path, want in by_path.items():
        assert U._unit({"name": "x", "kind": "value", "paths": [path], "low": 0.0, "high": 1.0}, {}) == want, path
    # величина значения единицу не задаёт: тот же путь при любых концах оси — та же единица
    assert U._unit({"name": "x", "kind": "value", "paths": ["credit.kappa"], "low": 0.0, "high": 500.0}, {}) == "number"
    assert U._unit({"name": "x", "kind": "value", "paths": ["tax.one_off.prob"], "low": 0.0, "high": 30.0}, {}) == "pct"
    assert U._unit({"name": "п.п. в имени", "kind": "value", "paths": ["valuation.erp"], "low": 0, "high": 1}, {}) == "pct"
    assert U._unit({"name": "x", "kind": "shift", "paths": ["tax.statutory"], "low": 0, "high": 1}, {}) == "pp"
    assert U._unit({"name": "x", "kind": "dict", "paths": ["joint.world_prob"], "low": {}, "high": {}}, {}) == "dict"
    assert U._unit({"name": "x", "kind": "value", "paths": ["valuation.erp"], "unit": "bp", "low": 0, "high": 1}, {}) == "bp"
    assert U._unit({"name": "x", "kind": "value", "paths": ["a.b"], "low": 0, "high": 1}, {("a.b",): "млрд ₽"}) == "bn"
    _fails(book_with({"valuation.uncertainty.axes.0.unit": "проценты"}), "unit", "незнакомое значение")
    assert {"years", "number", "pp", "pct", "bn"} <= set(UNIT_CODES)
    units = {a["name"]: U.axis_unit(book, a) for a in axes.values()}       # каждая ось книги получает код
    assert set(units.values()) <= set(UNIT_CODES)
    assert all(units[a["name"]] == "pp" for a in axes.values() if a["kind"] == "shift")


# ------------------------------------------------------------------ № 81: оценщик срединных прогонов


def test_median_shift_is_the_mean_pairwise_shift_of_the_middle_draws():
    """М§10: Δ_медианы = среднее попарных разностей по прогонам, чей ранг в базовой выборке лежит в окне;
    разность двух медиан — сдвиг одного-двух прогонов, среднее по всей выборке — сдвиг среднего."""
    base = [float(i) for i in range(10)]
    moved = [v + (5.0 if i < 2 or i > 7 else 1.0 + 0.1 * i) for i, v in enumerate(base)]
    assert U.median_shift(base, moved, (0.4, 0.6)) == pytest.approx((1.4 + 1.5) / 2)
    assert U.median_shift(base, moved, (0.0, 1.0)) == pytest.approx(sum(m - b for b, m in zip(base, moved)) / 10)
    order = [3, 0, 9, 5, 1, 7, 2, 8, 6, 4]                                    # порядок прогонов не важен — важен ранг
    assert U.median_shift([base[i] for i in order], [moved[i] for i in order], (0.4, 0.6)) == pytest.approx(1.45)
    assert U.median_shift(base, moved, (0.0, 0.25), by=list(reversed(base))) == pytest.approx((1.7 + 5.0 + 5.0) / 3)
    assert U.median_shift([1.0, 2.0, 3.0], [1.5, 2.25, 3.5], (0.49, 0.51)) == pytest.approx(0.25)   # окно уже шага рангов
    assert U.median_shift([7.0], [8.0], (0.4, 0.6)) == 1.0
    with pytest.raises(ValueError):
        U.median_shift([1.0, 2.0], [1.0], (0.4, 0.6))
    book = fixture_book()
    assert U.rank_window(book) == (0.4, 0.6)
    _fails(book_with({"valuation.sensitivities.rank_window": [0.6, 0.6]}), "rank_window")
    _fails(book_with({"valuation.sensitivities.rank_window": [0.2, 1.2]}), "rank_window")
    _fails(book_with({}, drop=("valuation.sensitivities.rank_window",)), "rank_window", "нет ключа")
    # разность двух медиан — сдвиг одного срединного прогона; оценщик усредняет срединные прогоны
    base11 = [float(i) for i in range(11)]
    moved11 = [v + (1.6 if i == 5 else 1.0) for i, v in enumerate(base11)]
    by_medians = U.quantile(sorted(moved11), 0.5) - U.quantile(sorted(base11), 0.5)
    assert by_medians == pytest.approx(1.6)
    assert U.median_shift(base11, moved11, (0.3, 0.7)) == pytest.approx((4 * 1.0 + 1.6) / 5)


# ------------------------------------------------------------------ № 80: нейтральное значение по двум допускам


def test_neutral_value_is_not_a_table_row_when_the_gap_changes_sign():
    """М§13: строка таблицы с невязкой в рублёвом допуске корнем сама по себе не считается, если между соседними
    строками невязка меняет знак, — поиск идёт внутрь отрезка; стоп — по двум допускам."""
    from model.nextreport import U_MAX, _Gap, _neutral
    slope, true = 1600.0, 0.06672                                     # 1,6 ₽ на 0,1 п.п. — пологая реакция ЧПМ
    calls = []

    def fn(x):
        calls.append(x)
        return slope * (x - true)

    values = [0.064, 0.067, 0.070]
    table = [slope * (v - true) for v in values]
    assert abs(table[1]) <= 0.5                                       # строка 6,70 % — в рублёвом допуске
    g = _Gap(fn)
    root, gap, err = _neutral(values, table, g, 0.002, 0.5, 0.0001)
    assert err is None and root != 0.067 and abs(root - true) <= 0.0001 and abs(gap) <= 0.5
    assert 1 <= len(calls) <= U_MAX
    # без допуска по значению — стоп по одной невязке (прежнее правило шага), но тоже внутри отрезка
    root2, gap2, _ = _neutral(values, table, _Gap(lambda x: slope * (x - true)), 0.002, 0.5)
    assert root2 not in values and abs(gap2) <= 0.5
    # без смены знака строка с невязкой в допуске — корень
    root3, gap3, err3 = _neutral(values, [0.9, 0.4, 0.8], _Gap(lambda x: 9.9), 0.002, 0.5, 0.0001)
    assert (root3, gap3, err3) == (0.067, 0.4, None)
    # строка с невязкой ровно ноль — корень и при смене знака
    root4, gap4, _ = _neutral(values, [-3.0, 0.0, 4.0], _Gap(lambda x: 99.0), 0.002, 0.5, 0.0001)
    assert (root4, gap4) == (0.067, 0.0)
    # ступенчатая функция: не сошлось по значению — лучшее приближение, не больше U_MAX пересчётов
    steps = []

    def stair(x):
        steps.append(x)
        return -2.0 if x < 0.0655 else 3.0

    root5, gap5, err5 = _neutral(values, [-2.0, 3.0, 3.0], _Gap(stair), 0.002, 0.5, 0.0001)
    assert err5 is None and root5 is not None and len(steps) <= U_MAX and abs(gap5) in (2.0, 3.0)
    assert float(fixture_book().get("valuation.next_report.value_tol")) == 0.0001
    _fails(book_with({"valuation.next_report.value_tol": 0.0}), "value_tol")
    _fails(book_with({}, drop=("valuation.next_report.value_tol",)), "value_tol", "нет ключа")


# ------------------------------------------------------------------ № 86: центральная клетка — правилом


def test_central_cell_is_the_modal_cell_by_rule():
    """INTERFACES §4.6: мир с наибольшим весом слоя «свой взгляд», режим с наибольшей вероятностью после A-P2u,
    сценарий капитала с наибольшей P(s | r); при равенстве — первый по порядку книги."""
    book = fixture_book()
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
    w, r, s = modal_cell(book, prior)
    assert float(book.get(f"joint.world_prob.{w}")) == max(float(v) for v in book.get("joint.world_prob").values())
    assert prior[r] == max(prior.values())
    table = book.get(f"joint.reg_prob_given_regime.{r}")
    assert float(table[s]) == max(float(v) for v in table.values())
    flat = {x: 0.25 for x in prior}
    assert modal_cell(book, flat)[1] == book.get("regimes.ids")[0]            # равенство — первый по порядку книги
    tilted = {**{x: 0.1 for x in prior}, "crisis": 0.7}
    assert modal_cell(book, tilted)[1] == "crisis"                           # вероятности после наблюдений
    tie = book.with_overrides({"joint.world_prob": {x: 1 / 3 for x in book.get("worlds.ids")}})
    assert modal_cell(tie, prior)[0] == book.get("worlds.ids")[0]


# ------------------------------------------------------------------ № 97: падежи, разрядка, малые числа


def test_message_helpers_agree_in_case_and_group_digits():
    assert [in_cells(n) for n in (0, 1, 2, 5, 11, 21, 36, 101, 111)] == [
        "в 0 клетках", "в 1 клетке", "в 2 клетках", "в 5 клетках", "в 11 клетках", "в 21 клетке", "в 36 клетках",
        "в 101 клетке", "в 111 клетках"]
    assert in_quarters(1) == "в 1 квартале" and in_quarters(4) == "в 4 кварталах" and in_quarters(41) == "в 41 квартале"
    assert grouped(2000) == "2 000" and grouped(200) == "200" and grouped(1234567) == "1 234 567"
    assert sci(6.9e-18) == "6,9·10⁻¹⁸" and sci(1e-9) == "1·10⁻⁹" and sci(0.0) == "0" and sci(1.84e-12) == "1,8·10⁻¹²"
    assert sci(-2.5e-7) == "−2,5·10⁻⁷" and sci(3e5) == "3·10⁵"
    run = fixture_run()
    for f in check_invariants(run) + check_gates(run):
        assert not U.service_text(f.message), (f.name, f.message, U.service_text(f.message))
        assert "e-" not in f.message and "Σ" not in f.message and "null" not in f.message, f.name
    gate = next(x for x in check_gates(run) if x.name == "lt_spread_floor")
    assert gate.fired and "п.п." in gate.message and " %" not in gate.message     # спреды к опоре — в п.п.
    one = copy.copy(next(x for x in check_gates(run) if x.name == "pb_by_world"))
    assert "клетк" in one.message


def test_service_text_rules():
    """П§0.2: служебного текста на экране нет — правила, которыми сторожится источник суждения."""
    bad = {"(2026, 3, QUARTER; ≈10-е число — РСБУ)": "кортеж", "отклонение 6,9e-18": "e-нотация",
           "оценка [В]": "метка класса допущения", "с. 24, sha256 d17d36167137507e": "хвост sha256",
           "вопрос ведущему": "рабочая пометка", "первичку собрать": "рабочая пометка",
           "страница закрыта антиботом": "рабочая пометка", "evidence/governance/ — канал": "путь к листу или файлу",
           "лист calib.yaml": "путь к листу или файлу", "за 2026M09": "код периода", "решение владельца 30.09": "дата без года"}
    for text, rule in bad.items():
        assert rule in U.service_text(text), text
    for text in ("решение владельца 30.09.2026", "ось 0,049–0,062", "magnit-850oa book-1.6", "МСФО/РСБУ и 1/3",
                 "2024 −161, 2025 −247 (МСФО)", "допуск 1·10⁻⁹", "п. 6.4 политики", "15,10 − 15,22 п.п."):
        assert U.service_text(text) == [], (text, U.service_text(text))
    assert U.source_text("A-K1 [Р: evidence/capital/ — расчёт; ответы ЦБ 2022; первичку собрать]") == "ответы ЦБ 2022"
    assert U.source_text("ось [В: вопрос ведущему]") == ""
