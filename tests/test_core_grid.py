"""Сетка: 36 клеток, одна конвенция вероятностей, слои и λ, цена, A-P2u, чистота (М§3, §8, §9, §12)."""

from __future__ import annotations

import copy

import pytest

from model.book import book_from_dict
from model.grid import (bank_language, cap_shift, make_context, price_from_v0, run_grid, world_layer)
from model.worlds import BASE_WORLD
from tests.support_core import fixture_book, fixture_dict, fixture_facts, fixture_run

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def test_36_cells_in_book_order():
    run = fixture_run()
    b = run.ctx.book
    expected = [(w, r, s) for w in b.get("worlds.ids") for r in b.get("regimes.ids")
                for s in b.get("capital.reg_scenarios.ids")]
    assert [c.key for c in run.cells] == expected
    assert run.cell("H", "norm", "schedule").key == ("H", "norm", "schedule")


def test_one_convention_of_probabilities():
    run = fixture_run()
    b = run.ctx.book
    for name, layer in run.layers.items():
        assert sum(layer.prob.values()) == pytest.approx(1.0, abs=1e-12)
        for (w, r, s), p in layer.prob.items():
            assert p == pytest.approx(layer.world_weights[w] * run.ctx.posterior[r]
                                      * float(b.get(f"joint.reg_prob_given_regime.{r}.{s}")), abs=1e-15)
    assert run.layers["macro_neutral"].world_weights[b.get("joint.macro_neutral_world")] == 1.0


def test_layers_price_point_and_identity():
    run = fixture_run()
    for layer in run.layers.values():
        assert layer.v0 == pytest.approx(layer.bv_v + layer.pv_ri_explicit + layer.pv_terminal, rel=1e-12)
        assert layer.price == pytest.approx(price_from_v0(layer.v0, run.bridge_amount, run.governance,
                                                          run.shares_out))
        assert layer.pb == pytest.approx(layer.v0 / layer.bv_v)
    assert run.low == run.layers["macro_neutral"].price
    assert run.high == run.layers["analytical"].price
    assert run.point == pytest.approx(run.low + run.lam * (run.high - run.low))
    assert run.price_at(0.0) == run.low and run.price_at(1.0) == pytest.approx(run.high)


def test_bank_language_and_world_layers():
    run = fixture_run()
    bl = bank_language(run)
    assert bl["fair_pb_point"] == pytest.approx(bl["v_point"] / bl["bv_v"])
    assert bl["market_cap"] > 0 and bl["market_pb"] == pytest.approx(bl["market_cap"] / bl["bv_v"])
    assert bl["excess_point"] == pytest.approx(bl["v_point"] - bl["bv_v"])
    for w in run.ctx.book.get("worlds.ids"):
        wl = world_layer(run, w)
        assert wl["pb"] == pytest.approx(wl["v0"] / wl["bv_v"])


def test_grid_is_a_pure_function_of_inputs():
    a = run_grid(fixture_book(), fixture_facts())
    b = run_grid(fixture_book(), fixture_facts())
    assert [c.v_ri for c in a.cells] == [c.v_ri for c in b.cells]
    assert (a.low, a.high, a.point) == (b.low, b.high, b.point)


def test_cap_shift_limits_each_observation():
    prior = {"a": 0.15, "b": 0.40, "c": 0.30, "d": 0.15}
    post = {"a": 0.60, "b": 0.20, "c": 0.15, "d": 0.05}
    out = cap_shift(prior, post, 0.05)
    assert sum(out.values()) == pytest.approx(1.0)
    assert max(abs(out[r] - prior[r]) for r in prior) <= 0.05 + 1e-12
    assert out["a"] > prior["a"]


def _with_observations(obs):
    data = copy.deepcopy(fixture_dict())
    data["joint"]["regime_update"]["observations"] = obs
    return book_from_dict(data, facts=fixture_facts())


def test_regime_update_moves_posterior_and_cells_carry_deviation():
    """Наблюдение CoR (упр.) переводится мостом; в квартале наблюдения CoR клетки мира N = наблюдению."""
    book = fixture_book()
    period = book.get("meta.first_period")
    obs_mgmt = 0.030
    b = _with_observations([{"period": period, "cor": obs_mgmt, "nim": None, "se_cor": 0.0,
                             "se_nim": None, "basis": "mgmt"}])
    ctx = make_context(b, fixture_facts())
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
    limit = float(book.get("joint.regime_update.max_shift_pp"))
    assert ctx.posterior != prior
    assert max(abs(ctx.posterior[r] - prior[r]) for r in prior) <= limit + 1e-12
    assert ctx.posterior["crisis"] > prior["crisis"]                 # высокая CoR — к кризису
    run = run_grid(b, fixture_facts())
    engine_obs = run.ctx.bridge.to_engine_cor(obs_mgmt)
    for r in book.get("regimes.ids"):
        c = run.cell(BASE_WORLD, r, "schedule")
        assert c.quarters["cor"][1] == pytest.approx(engine_obs, rel=1e-9)
        # затухание δ = ρ^k m в следующих кварталах
        rho = float(book.get("joint.regime_update.observables.cor.rho_q"))
        d1 = ctx.deviations[r]["cor"][1]
        assert ctx.deviations[r]["cor"][3] == pytest.approx(rho ** 2 * d1)


def test_nim_observation_is_the_layer_average_of_the_regime():
    """Наблюдение ЧПМ (факт, se 0): в его квартале среднее клеток режима по мирам и сценариям слоя
    «свой взгляд» равно наблюдению; структурная разница миров сохраняется (М§12)."""
    b = _with_observations([{"period": fixture_book().get("meta.first_period"), "cor": None, "nim": 0.058,
                             "se_cor": None, "se_nim": 0.0, "basis": "mgmt"}])
    run = run_grid(b, fixture_facts())
    eng = run.ctx.bridge.to_engine_nim(0.058)
    pw = {w: float(b.get(f"joint.world_prob.{w}")) for w in b.get("worlds.ids")}
    table = b.get("joint.reg_prob_given_regime")
    for r in b.get("regimes.ids"):
        cells = [c for c in run.cells if c.regime == r]
        avg = sum(pw[c.world] * float(table[r][c.scenario]) * c.quarters["nim"][1] for c in cells)
        assert avg == pytest.approx(eng, abs=1e-12), r
        by_world = {c.world: c.quarters["nim"][1] for c in cells if c.scenario == "schedule"}
        assert len({round(v, 12) for v in by_world.values()}) == len(by_world)      # миры различаются
    mu = run.ctx.expectations
    assert mu["until"] == 1 and set(mu["nim"]) == set(b.get("regimes.ids"))
    assert run.ctx.nim_ref is None


def test_two_similar_quarters_are_not_two_independent_signals():
    """Инновация, а не отклонение: второй такой же квартал двигает меньше первого."""
    book = fixture_book()
    p1 = book.get("meta.first_period")
    tl = make_context(book, fixture_facts()).timeline
    p2 = tl.period(2)
    one = _with_observations([{"period": p1, "cor": 0.022, "nim": None, "se_cor": 0.0, "se_nim": None,
                               "basis": "mgmt"}])
    two = _with_observations([{"period": p1, "cor": 0.022, "nim": None, "se_cor": 0.0, "se_nim": None,
                               "basis": "mgmt"},
                              {"period": p2, "cor": 0.022, "nim": None, "se_cor": 0.0, "se_nim": None,
                               "basis": "mgmt"}])
    prior = float(book.get("joint.regime_prob.crisis"))
    c1 = make_context(one, fixture_facts()).posterior["crisis"]
    c2 = make_context(two, fixture_facts()).posterior["crisis"]
    assert c1 - prior > 0
    assert (c2 - c1) < (c1 - prior)
