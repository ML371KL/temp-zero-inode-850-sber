# -*- coding: utf-8 -*-
"""Перезаякоривание (`ops/tools/reanchor.py`, М§15.2): инвариант «факт = ожидание ⇒ механика ≈ 0».

Быстрые тесты (метка `tact`) — на синтетической книге и фактах `tests/fixtures/core/`: клетка,
перезаякоренная на своём пути, сохраняет стоимость; на ожидаемом отчёте ожидаемый путь ЧПМ и
точка не меняются; наблюдение закрытого квартала несёт замороженные μ, и апостериорные новой
книги равны апостериорным прежней с тем же наблюдением; якорь воспроизводит отчёт; режим `keep`
сохраняет четыре скаляра пути передачи ставки и пересчитывает обе доли, `resolve` — диагностика
без кандидата; μ записей окна не пересчитываются; отчёт «без шока» — ожидание прочих режимов;
подмена наблюдения закрытого квартала (решение книги с причиной) идёт только в наблюдение кандидата
на отчёте — факты остаются числами отчёта; внутри репозитория запись — только под `var/`; отказы
называют причину. Полный набор (все клетки, медиана полосы, цепочка через смену года
якоря, настоящая книга, запуск из командной строки) — `ci_only`.

Даты тестов — от периодов книги фикстуры (конец закрываемого квартала + 2 дня), не от «сегодня».
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from model.book import anchor_facts, book_from_dict, book_roles, load_book, load_facts
from model.credit import bridge_from_facts, mgmt_history
from model.grid import run_grid
from model.nii import solve_transmission
from model.paths import Trajectory
from model.timeline import parse_period
from tests.support_core import FIXTURE_BOOK, FIXTURE_FACTS, fixture_book, fixture_facts, real_book_or_skip

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("tool_reanchor", ROOT / "ops" / "tools" / "reanchor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


R = _load()
tact = pytest.mark.tact
ci_only = pytest.mark.ci_only

CELLS = (("N", "soft", "schedule"), ("H", "downturn", "upper"), ("M", "crisis", "strict"))
POINT_SHARE = 0.2            # точка на ожидаемом отчёте — в пределах этой доли шага печати
CHAIN_SHARE = 0.4            # то же в цепочке отчётов: в квартал ГОСА один дивиденд на все клетки
MEDIAN_SHARE = 0.5           # медиана: закрытый квартал перестаёт разыгрываться, несимметричные оси её сдвигают
BAND_DRAWS = 48              # прогонов полосы в тесте медианы (парное сравнение на одних точках)


def scene_of(book, facts) -> SimpleNamespace:
    """Сухой прогон: прежняя книга на дате оценки, она же с ожидаемым наблюдением, кандидат."""
    first = str(book.get("meta.first_period"))
    v = R.period_end(first) + timedelta(days=2)
    live = R.live_at(book, facts, v)
    run_a = run_grid(book, facts, live)
    plus, run_plus, obs = R.expected_run(book, facts, live)
    expected = R.expected_report(book, facts, run=run_plus)
    cand = R.reanchor(book, facts, expected, valuation_date=v, base_run=run_plus)
    live_e = R.live_at(cand.book, cand.facts, v)
    return SimpleNamespace(book=book, facts=facts, v=v, live=live, run_a=run_a, plus=plus, run_plus=run_plus,
                           obs=obs, expected=expected, cand=cand, live_e=live_e,
                           run_e=run_grid(cand.book, cand.facts, live_e), period=first)


@lru_cache(maxsize=None)
def scene() -> SimpleNamespace:
    return scene_of(fixture_book(), fixture_facts())


def _report(**changes) -> dict:
    """Ожидаемый отчёт фикстуры с правками по точечным путям."""
    report = copy.deepcopy(scene().expected)
    for path, value in changes.items():
        node = report
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        if value is None:
            node.pop(parts[-1])
        else:
            node[parts[-1]] = value
    return report


# ------------------------------------------------------------------ инвариант: механика ≈ 0


@tact
def test_a_cell_reanchored_on_its_own_path_keeps_its_value():
    """М§15.2: отчёт клетки — её состояние на конец закрытого квартала; стоимость клетки на дате
    оценки та же (допуск 0,1 %; мера — на порядки меньше)."""
    s = scene()
    mech = R.cell_mechanics(s.book, s.facts, s.run_plus, s.obs, s.v, cells=CELLS)
    assert [r["key"] for r in mech["rows"]] == list(CELLS)
    assert mech["ok"] and abs(mech["worst"]["rel"]) <= R.MECHANICS_TOL / 10, mech["worst"]


@tact
def test_the_expected_path_of_nim_does_not_move_on_the_expected_report():
    """Уровень не считается дважды: ожидаемый путь ЧПМ кандидата (слой «свой взгляд») — путь
    прежней книги. Кварталы ближней калибровки — точно, хвост ключами лет — в среднем за год."""
    s = scene()
    path = s.cand.derived["nim_path"]
    near = s.cand.near
    by_year: dict[int, list[float]] = {}
    for period, old, new in zip(path["periods"], path["old"], path["new"]):
        assert abs(new - old) <= R.NEAR_RANGE, period
        if period in near:
            assert abs(new - old) <= 2 * R.NEAR_TOL, period
        by_year.setdefault(parse_period(period)[0], []).append(new - old)
    assert all(abs(sum(d) / len(d)) <= 2 * R.NEAR_TOL for d in by_year.values())
    first_year = parse_period(path["periods"][0])[0]
    assert all(f"{first_year + 1}Q{q}" in near for q in (1, 2, 3, 4)) and near["LT"] == 0.0


@tact
def test_the_point_on_the_expected_report_is_the_rolled_point():
    """Перезаякоривание на ожидаемом пути = прокатка: точка, низ и верх — в доле шага печати."""
    s = scene()
    step = float(s.book.get("valuation.headline.print_step"))
    for name in ("low", "point", "high"):
        assert abs(getattr(s.run_e, name) - getattr(s.run_plus, name)) <= POINT_SHARE * step, name
    for name, layer in s.run_plus.layers.items():
        assert s.run_e.layers[name].v0 == pytest.approx(layer.v0, rel=3 * R.MECHANICS_TOL), name


@tact
def test_the_closed_quarter_becomes_an_observation_with_frozen_expectations():
    """Факт считается один раз: наблюдение q = 0 с μ прежней книги; апостериорные новой книги —
    апостериорные прежней с тем же наблюдением; в книгу они не вписываются."""
    s = scene()
    obs = s.cand.data["joint"]["regime_update"]["observations"]
    assert [o["period"] for o in obs] == [s.period] and obs[0]["basis"] == "mgmt"
    regimes = list(s.book.get("regimes.ids"))
    assert list(obs[0]["mu_cor"]) == regimes and list(obs[0]["mu_nim"]) == regimes
    assert (obs[0]["cor"], obs[0]["nim"]) == pytest.approx((s.obs["cor"], s.obs["nim"]), abs=1e-9)
    for r in regimes:
        assert s.run_e.ctx.posterior[r] == pytest.approx(s.run_plus.ctx.posterior[r], abs=1e-9)
    assert s.cand.data["joint"]["regime_prob"] == s.book.source["joint"]["regime_prob"]
    moved = max(abs(s.run_plus.ctx.posterior[r] - s.run_a.ctx.posterior[r]) for r in regimes)
    assert moved > 1e-3, "на смеси режимов наблюдение, равное ожиданию, учит (М§12)"


@tact
def test_observations_older_than_the_window_are_dropped():
    s = scene()
    X = copy.deepcopy(dict(s.book.source))
    ru = X["joint"]["regime_update"]
    window = int(ru["window_obs"])
    y, q = parse_period(str(s.book.get("meta.anchor_period")))
    old = [{"period": f"{y - 2 + (q + i - 1) // 4}Q{(q + i - 1) % 4 + 1}", "cor": 0.01, "nim": None,
            "se_cor": 0.0, "se_nim": None, "basis": "mgmt", "mu_cor": {r: 0.01 for r in X["regimes"]["ids"]}}
           for i in range(window + 1)]
    ru["observations"] = copy.deepcopy(old)
    record = R.carry_observation(X, s.book, s.facts, s.obs)
    kept = ru["observations"]
    assert len(kept) == window and kept[-1] is record and kept[0]["period"] == old[2]["period"]


def _old_records(book, count: int) -> list[dict]:
    """Записи окна прежних кварталов (не позже якоря) с замороженными μ — у каждой свои числа."""
    y, q = parse_period(str(book.get("meta.anchor_period")))
    regimes = list(book.get("regimes.ids"))
    index = 4 * y + q - 1
    out = []
    for i in range(count):
        yy, qq = divmod(index - (count - 1 - i), 4)
        out.append({"period": f"{yy}Q{qq + 1}", "cor": 0.011 + 0.001 * i, "nim": 0.061 + 0.001 * i,
                    "se_cor": 0.0, "se_nim": 0.0, "basis": "mgmt",
                    "mu_cor": {r: 0.008 + 0.004 * j + 0.0001 * i for j, r in enumerate(regimes)},
                    "mu_nim": {r: 0.064 - 0.002 * j + 0.0001 * i for j, r in enumerate(regimes)}})
    return out


@tact
def test_frozen_expectations_are_never_recomputed_not_even_when_the_anchor_year_changes():
    """В15, М§12: запись окна хранит ожидания режимов, действовавшие, когда квартал был открыт. Перенос
    якоря их не трогает — ни сам по себе, ни при смене года якоря, когда кризисные траектории сдвигаются
    на год; μ новой записи — ожидания прежней книги (до сдвига года шока)."""
    s = scene()
    window = int(s.book.get("joint.regime_update.window_obs"))
    records = _old_records(s.book, window - 1)
    old = s.book.with_overrides({R.OBS: records})
    # перенос якоря: прежние записи — те же, новая — в конце окна, с μ прежней книги
    cand = R.reanchor(old, s.facts, s.expected, valuation_date=s.v)
    kept = cand.data["joint"]["regime_update"]["observations"]
    assert kept[:-1] == records and len(kept) == window
    frozen = R.frozen_expectations(old, s.facts)
    assert kept[-1]["period"] == s.period
    assert (kept[-1]["mu_cor"], kept[-1]["mu_nim"]) == (frozen["mu_cor"], frozen["mu_nim"])
    # смена года якоря: ключи кризиса — на год, записи окна и их μ — бит в бит
    X = copy.deepcopy(cand.data)
    crisis = next(r for r in X["regimes"]["ids"] if "shock_year_offset" in X["regimes"][r])
    before = copy.deepcopy(X["joint"]["regime_update"]["observations"])
    R.shift_crisis(X, 1)
    assert X["regimes"][crisis]["cor"] != cand.data["regimes"][crisis]["cor"], "кризис перезаряжен"
    assert X["joint"]["regime_update"]["observations"] == before
    # следующий перенос выталкивает самую старую запись, остальные — снова без пересчёта
    record = R.carry_observation(X, cand.book, cand.facts, {**s.obs, "period": R.next_period(s.period)})
    after = X["joint"]["regime_update"]["observations"]
    assert after[:-1] == before[1:] and after[-1] is record and len(after) == window


@tact
def test_the_floor_of_regime_probability_is_part_of_the_learning_row():
    """В15: после наблюдения вероятность режима не ниже `floor_share` × базовой; пол действует в
    прежней книге с наблюдением (A⁺) и у кандидата одинаково — он в строке «обучение», не в механике."""
    s = scene()
    prior = s.book.get("joint.regime_prob")
    share = float(s.book.get("joint.regime_update.floor_share"))
    floor = s.cand.derived["regime_floor"]
    assert floor["share"] == share and floor["floors"] == {r: share * p for r, p in prior.items()}
    assert floor["on_floor"] == [] and all(s.run_e.ctx.posterior[r] >= floor["floors"][r] for r in prior)
    high = s.book.with_overrides({"joint.regime_update.floor_share": 0.95})
    _, run_plus, obs = R.expected_run(high, s.facts)
    lifted = R.regime_floor(high, run_plus.ctx.posterior)
    assert lifted["on_floor"], "наблюдение, равное ожиданию, опускает дальний режим до пола"
    for r in lifted["on_floor"]:
        assert run_plus.ctx.posterior[r] == pytest.approx(0.95 * prior[r], abs=1e-12)
    cand = R.reanchor(high, s.facts, R.expected_report(high, s.facts, run=run_plus), valuation_date=s.v,
                      base_run=run_plus)
    run_e = run_grid(cand.book, cand.facts, R.live_at(cand.book, cand.facts, s.v))
    assert cand.derived["regime_floor"]["on_floor"] == lifted["on_floor"]
    for r in prior:
        assert run_e.ctx.posterior[r] == pytest.approx(run_plus.ctx.posterior[r], abs=1e-9)
    assert sum(run_e.ctx.posterior.values()) == pytest.approx(1.0)


@tact
def test_the_report_without_the_shock_is_the_expectation_of_the_other_regimes():
    """М§12, §15.2: в квартале шока ожидаемый отчёт — среднее двугорбого прогноза; «отчёт без шока» —
    ожидание прочих режимов с их вероятностями (в сумме 1). Режимы с шоком — те, у кого первый прогнозный
    квартал книги лежит в году шока; у книги фикстуры (якорь не в четвёртом квартале) таких нет."""
    s = scene()
    assert R.shock_regimes(s.run_a) == []
    rows = R._regime_rows(s.book, s.facts, s.live)
    crisis = next(r for r in s.book.get("regimes.ids") if "shock_year_offset" in s.book.source["regimes"][r])
    rest = [r for r in rows if r["regime"] != crisis]
    total = sum(r["posterior"] for r in rest)
    calm = R.expected_observation(s.book, s.facts, s.live, without=[crisis])
    for x in ("cor", "nim"):
        assert calm[x] == pytest.approx(sum(r["posterior"] * r[f"{x}_q_mgmt"] for r in rest) / total, abs=1e-12)
        assert R.expected_observation(s.book, s.facts, s.live)[x] == pytest.approx(s.obs[x], abs=1e-12)
    assert (calm["period"], calm["basis"], calm["se_cor"]) == (s.period, "mgmt", 0.0)
    with pytest.raises(R.ReportError, match="прочих режимов"):
        R.expected_observation(s.book, s.facts, s.live, without=list(s.book.get("regimes.ids")))
    year_of_shock = s.run_a.ctx.prep.regimes[crisis].shock_year
    assert year_of_shock is not None and year_of_shock != s.run_a.ctx.timeline.year(1)


# ------------------------------------------------------------------ ожидаемый отчёт и факты якоря


@tact
def test_the_expected_report_is_a_valid_quarter_report():
    s = scene()
    rep = s.expected
    R.check_report(s.book, s.facts, rep)
    assert rep["period"] == s.period and "синтетический" in rep["note"]
    roles = book_roles(s.book.get("nii.books"))
    booked = (sum(rep["interest"][b] for b in roles.assets) - sum(rep["interest"][b] for b in roles.liabilities)
              - rep["dia"])
    assert booked == pytest.approx(rep["pnl"]["nii"], abs=1e-6), "ближний сдвиг разнесён по книгам без остатка"
    assert rep["mgmt"]["nim"] == pytest.approx(s.obs["nim"], abs=1e-9)
    assert rep["mgmt"]["cor"] == pytest.approx(s.obs["cor"], abs=1e-9)
    assert json.loads(json.dumps(rep)) == rep, "отчёт переживает JSON туда-обратно"


@tact
def test_the_new_anchor_reproduces_the_report():
    """Факты кандидата — состояние отчёта; якорь модели воспроизводит нормативы и RWA отчёта."""
    s = scene()
    rep, cand = s.expected, s.cand
    books = s.book.get("nii.books")
    af = anchor_facts(cand.facts, books)
    assert af.period == s.period and af.as_of == R.period_end(s.period)
    assert cand.data["meta"]["anchor_period"] == s.period
    assert cand.data["meta"]["first_period"] == R.next_period(s.period)
    assert cand.data["valuation"]["next_report"]["period"] == R.next_period(s.period)
    for b in books:
        assert af.balances[b] == pytest.approx(rep["balance"]["books"][b], abs=1e-5), b
    assert af.bv == rep["balance"]["bv_common"] and af.fv_loans == rep["balance"]["loans_fvtpl"]
    assert af.fvoci == pytest.approx(rep["balance"]["fvoci"], abs=1e-5)
    row = af.pnl[s.period]
    assert row["nii"] == pytest.approx(rep["pnl"]["nii"]) and row["opex"] == pytest.approx(rep["pnl"]["opex"])
    roles = book_roles(books)
    old = anchor_facts(s.facts, books)
    days = R.engine_ratios(s.book, s.facts, rep)["days"]
    carried = sum((1 if b in roles.assets else -1) * af.rates[b] * (old.balances[b] + af.balances[b]) / 2
                  for b in books) * days / 365 - rep["dia"]
    assert carried == pytest.approx(rep["pnl"]["nii"], abs=1e-3), "ставки якоря несут весь ЧПД"
    anchor = s.run_e.cells[0].quarters
    assert anchor["rwa"][0] == pytest.approx(rep["capital"]["basel_rwa"], rel=1e-6)
    assert anchor["bv"][0] == rep["balance"]["bv_common"]
    assert cand.derived["anchor_row"]["n20"] == pytest.approx(rep["capital"]["n20_0"], abs=1e-8)
    assert cand.derived["anchor_row"]["n11"] == pytest.approx(rep["capital"]["n1_1_bank"], abs=1e-8)


@tact
def test_the_closed_quarter_enters_the_history_the_core_reads_for_the_anchor_year():
    """М§4.6: год якоря в упр. базисе собирается из отчётных кварталов — упр. факт закрытого квартала
    обязан быть в истории моста, конец прежнего якоря — в истории баланса. Иначе гейт гайденса и
    выпуск на фактах кандидата не считаются."""
    s = scene()
    old_anchor = str(s.book.get("meta.anchor_period"))
    for facts in (s.cand.facts, R.facts_after(s.book, s.facts, s.expected, update_bridge=False)):
        for metric in ("cor", "nim", "cir"):
            assert mgmt_history(facts, metric)[s.period] == pytest.approx(s.expected["mgmt"][metric], abs=1e-9)
        history = facts.periods("balance", "history")
        assert {old_anchor, s.period} <= set(history)
        assert history[old_anchor]["iea"]["v"] == pytest.approx(
            sum(anchor_facts(s.facts, s.book.get("nii.books")).balances[b]
                for b in book_roles(s.book.get("nii.books")).assets), abs=1e-5)
    frozen = R.facts_after(s.book, s.facts, s.expected, update_bridge=False)
    assert bridge_from_facts(frozen) == bridge_from_facts(s.facts), "среднее моста без продления окна — прежнее"
    assert bridge_from_facts(s.cand.facts) != bridge_from_facts(s.facts)
    checks = R.core_checks(s.run_e, s.v)
    assert checks["error"] is None and checks["invariants"] == []
    assert all(mass is None or 0.0 <= mass <= 1.0 for mass, _ in checks["gates"].values())
    # отчёт без CIR презентации: история моста CIR без квартала — проверки ядра называют причину
    rep = _report(**{"mgmt.cir": None})
    cand = R.reanchor(s.book, s.facts, rep, valuation_date=s.v, base_run=s.run_plus, near=s.cand.near)
    assert any("mgmt.cir" in w for w in cand.warnings)
    lame = R.core_checks(run_grid(cand.book, cand.facts, s.live_e), s.v)
    assert lame["error"] and s.period in lame["error"]


@tact
def test_calibrations_to_the_anchor_keep_the_rules_of_the_old_book():
    """Замороженные anchor-ключи, скаляры пути передачи ставки, плотности RWA, цены якоря — то, что
    помнит прошлое."""
    s = scene()
    X, old = s.cand.data, s.book
    for path in R.FROZEN_ANCHORS:
        assert R._get(old.source, path) == "anchor"
        assert R._get(X, path) == pytest.approx(old.anchors[path], abs=1e-9), path
    was, now = s.run_plus.ctx.transmission, s.run_e.ctx.transmission
    assert len(R.SCALARS) == len(R._sigmas(now)) == 4
    assert R._sigmas(now) == pytest.approx(R._sigmas(was), abs=2e-6)
    kept = s.cand.derived["transmission"]
    assert list(kept) == list(R.TRANSMISSION_KEYS)
    for path, (before, after, _) in kept.items():
        assert after != before, f"{path} пересчитан на структуру нового якоря"
        assert R._get(X, path) == after, path
    assert (now.split, now.phi_split, now.t_target) == (kept[R.SPLIT][1], kept[R.PHI_SPLIT][1], kept[R.T_TARGET][1])
    assert s.cand.derived["sigmas"] == {"old": R._sigmas(was), "new": R._sigmas(now)}
    omega = s.cand.derived["phi_weights"]
    assert set(omega["new"]) == set(omega["old"]) and sum(omega["new"].values()) == pytest.approx(1.0)
    factor = s.cand.derived["price_index"]
    assert factor == pytest.approx(s.expected["market"]["price_index"])
    for key, value in old.source["other"]["misc_net_real"].items():
        assert X["other"]["misc_net_real"][key] == pytest.approx(value * factor, abs=1e-5)
    k = s.cand.derived["rwa_factor"]
    for b, value in old.source["capital"]["rwa"]["density"].items():
        assert X["capital"]["rwa"]["density"][b] == pytest.approx(value * k, abs=1e-6)


def _keep_case(split: float, phi_split: float):
    """Прежняя книга с долями (split, phi_split), её прогон и заготовка кандидата на фактах нового якоря."""
    s = scene()
    old = s.book.with_overrides({R.SPLIT: split, R.PHI_SPLIT: phi_split})
    run_old = run_grid(old, s.facts)
    X = copy.deepcopy(s.cand.data)
    for path in R.TRANSMISSION_KEYS:
        R._set(X, path, R._get(old.source, path))
    X["valuation"]["uncertainty"]["axes"] = copy.deepcopy(old.source["valuation"]["uncertainty"]["axes"]) + [
        {"name": "доля сдвига", "kind": "value", "paths": [R.SPLIT], "low": 0.0, "high": 1.0, "dist": "triangular"},
        {"name": "доля сжатия", "kind": "value", "paths": [R.PHI_SPLIT], "low": 0.0, "high": 0.6, "dist": "triangular"}]
    return s, old, run_old, X


@tact
@pytest.mark.parametrize("split,phi_split", [(0.5, 0.5), (0.09, 0.0), (1.0, 1.0), (0.0, 0.3)])
def test_keep_holds_the_four_path_scalars_at_any_shares(split, phi_split):
    """М§15.2 п. 8: σ0_A, σ0_L, φ_A, φ_L прежней книги — на структуре нового якоря; цели и доли —
    пересчитаны; доли на краях (0 и 1) остаются на краях; концы осей целей несут прежние скаляры."""
    s, old, run_old, X = _keep_case(split, phi_split)
    before = copy.deepcopy(X)
    kept = R.keep_transmission(X, s.cand.facts, run_old)
    bridge = bridge_from_facts(s.cand.facts)
    now = solve_transmission(book_from_dict(X, facts=s.cand.facts), s.cand.facts, bridge)
    was = run_old.ctx.transmission
    assert R._sigmas(now) == pytest.approx(R._sigmas(was), abs=2e-6)
    assert was.phi_liab != 0.0 or phi_split == 1.0, "случай с добавкой к стоимости пассивов не пуст"
    for path, edge in ((R.SPLIT, split), (R.PHI_SPLIT, phi_split)):
        value = R._get(X, path)
        assert 0.0 <= value <= 1.0
        if edge in (0.0, 1.0):
            assert value == edge, path
        else:
            assert value != edge and abs(value - edge) < 0.01, path
        assert kept[path][:2] == (edge, value)
    # без пересчёта те же цели на новой структуре дают другие скаляры — есть что сохранять
    raw = solve_transmission(book_from_dict(before, facts=s.cand.facts), s.cand.facts, bridge)
    assert R._sigmas(raw) != pytest.approx(R._sigmas(was), abs=2e-6)
    # концы осей целей: те же скаляры, что давал конец оси в прежней книге
    for path in (R.NIM_TARGET, R.T_TARGET):
        (axis_old,) = [a for a in before["valuation"]["uncertainty"]["axes"] if a["paths"] == [path]]
        (axis_new,) = [a for a in X["valuation"]["uncertainty"]["axes"] if a["paths"] == [path]]
        for key in ("low", "high"):
            end_old = solve_transmission(old.with_overrides({path: axis_old[key]}), s.facts, run_old.ctx.bridge)
            Y = copy.deepcopy(X)
            R._set(Y, path, axis_new[key])
            end_new = solve_transmission(book_from_dict(Y, facts=s.cand.facts), s.cand.facts, bridge)
            assert R._sigmas(end_new) == pytest.approx(R._sigmas(end_old), abs=5e-6), (path, key)
    # оси долей сдвигаются вместе с центром; конец на границе 0 или 1 остаётся на ней
    for path in (R.SPLIT, R.PHI_SPLIT):
        (axis_old,) = [a for a in before["valuation"]["uncertainty"]["axes"] if a["paths"] == [path]]
        (axis_new,) = [a for a in X["valuation"]["uncertainty"]["axes"] if a["paths"] == [path]]
        shift = kept[path][1] - kept[path][0]
        for key in ("low", "high"):
            want = axis_old[key] if axis_old[key] in (0.0, 1.0) else min(1.0, max(0.0, axis_old[key] + shift))
            assert axis_new[key] == pytest.approx(want, abs=1e-9), (path, key)


@tact
def test_resolve_is_a_diagnosis_and_writes_no_candidate(tmp_path):
    """В18: режим процедуры — `keep` (он же по умолчанию); `resolve` — диагностика: цели, доли и их
    оси — числа прежней книги, скаляры решены заново, а кандидат книги и фактов не пишется."""
    s = scene()
    assert R.TRANSMISSION_MODES == (R.KEEP, R.RESOLVE) == ("keep", "resolve")
    for function in (R.reanchor, R.attribution, R.cell_mechanics):
        assert function.__kwdefaults__["transmission"] == R.KEEP, function.__name__
    result = R.attribution(s.book, s.facts, s.expected, valuation_date=s.v, cells=False, transmission=R.RESOLVE)
    cand = result["candidate"]
    for path in R.TRANSMISSION_KEYS:
        assert R._get(cand.data, path) == R._get(s.book.source, path), path
    for where in ("uncertainty", "reverse_dcf"):
        for a, b in zip(cand.data["valuation"][where]["axes"], s.book.source["valuation"][where]["axes"]):
            if a["paths"][0] in R.TRANSMISSION_KEYS:
                assert a == b, a["name"]
    sig = cand.derived["sigmas"]
    assert sig["new"] != pytest.approx(sig["old"], abs=1e-5), "скаляры решены заново на новой структуре"
    assert cand.near == s.cand.near, "ближняя калибровка ЧПМ — та же: она не прячет сдвиг скаляров"
    out = tmp_path / "diagnosis"
    R.write_candidate(out, s.book, result)
    assert sorted(p.name for p in out.iterdir()) == ["REANCHOR.md", "expected.json"]
    text = (out / "REANCHOR.md").read_text(encoding="utf-8")
    assert "ДИАГНОСТИКА: режим `resolve`" in text and "не записан" in text


@tact
def test_prices_of_the_anchor_move_with_their_axes_and_the_anchor_year_keeps_its_fact():
    s = scene()
    X = copy.deepcopy(dict(s.book.source))
    factor = 1.02
    R.reindex_prices(X, s.facts, s.period, factor)
    year = parse_period(s.period)[0]
    reported = sum(float(row["noncore_net"]["v"]) for per, row in s.facts.periods("pnl_quarterly").items()
                   if parse_period(per)[0] == year)
    old_t = Trajectory(s.book.source["noncore"]["result_real"])
    new = X["noncore"]["result_real"]
    assert new[str(year)] == pytest.approx(reported + (old_t.year_value(year) - reported) * factor, abs=1e-5)
    new_t = Trajectory(new)
    for y in range(year + 1, year + 8):
        assert new_t.year_value(y) == pytest.approx(old_t.year_value(y) * factor, abs=1e-5), y
    axes = {a["name"]: a for a in X["valuation"]["uncertainty"]["axes"]}
    for a in s.book.source["valuation"]["uncertainty"]["axes"]:
        scaled = any(p.startswith(R.PRICE_INDEXED) for p in a["paths"])
        for key in ("low", "high"):
            if scaled:
                assert axes[a["name"]][key] == pytest.approx(a[key] * factor, abs=1e-5), a["name"]
            else:
                assert axes[a["name"]][key] == a[key], a["name"]
    X["valuation"]["uncertainty"]["axes"][0]["paths"] = ["other.misc_net_real.LT", "valuation.erp"]
    with pytest.raises(R.ReportError, match="разделить ось"):
        R.reindex_prices(X, s.facts, s.period, factor)


@tact
def test_spread_keys_follow_the_new_anchor_rates():
    """Ключи спредов «от ставки якоря» (М§4.4) выводятся заново тем же правилом; ключ следующего
    года остаётся серединой; книга, которая правилу не следовала, не трогается."""
    s = scene()
    books = s.book.get("nii.books")
    old_rates = dict(anchor_facts(s.facts, books).rates)
    year = parse_period(s.period)[0]
    name, other = [b for b, spec in s.book.source["nii"]["books"].items()
                   if isinstance(spec["spread"], dict) and str(year) in spec["spread"]][:2]
    spreads = {}
    for b, off_rule in ((name, 0.0), (other, 0.01)):         # первая книга следует правилу, вторая — нет
        spread = dict(s.book.source["nii"]["books"][b]["spread"])
        spread[str(year)] = round(R.spread_rule(s.book.source, old_rates, b)[1] + off_rule, 5)
        spread[str(year + 1)] = round((spread[str(year)] + Trajectory(spread).year_value(year + 2)) / 2, 5)
        spreads[f"nii.books.{b}.spread"] = spread
    old = s.book.with_overrides(spreads)
    X = copy.deepcopy(dict(old.source))
    X["meta"]["first_period"] = R.next_period(s.period)
    rates = {b: r - 0.004 for b, r in old_rates.items()}
    moved = R.rederive_spreads(X, old, old_rates, rates)
    assert name in moved and other not in moved
    for b in moved:
        y, rule = R.spread_rule(X, rates, b)
        spread = X["nii"]["books"][b]["spread"]
        assert spread[str(y)] == pytest.approx(rule, abs=1e-6), b
    spread = X["nii"]["books"][name]["spread"]
    assert spread[str(year + 1)] == pytest.approx((spread[str(year)] + Trajectory(spread).year_value(year + 2)) / 2,
                                                  abs=1e-6)
    assert all(X["nii"]["books"][b]["spread"] == old.source["nii"]["books"][b]["spread"]
               for b in books if b not in moved)
    same = copy.deepcopy(dict(old.source))
    assert R.rederive_spreads(same, old, old_rates, old_rates) == [], "ставки те же — ключи те же"
    assert same == old.source


@tact
def test_the_near_shift_trajectory_is_quarters_then_years_then_lt():
    periods = [f"{y}Q{q}" for y in range(2031, 2037) for q in (1, 2, 3, 4)][3:]
    values = [0.004 * 0.6 ** i for i in range(len(periods))]
    old = {"2031": -0.005, "2032Q1": -0.003, "2032Q2": -0.002, "2032Q3": -0.001, "2032Q4": 0.0,
           "2032": -0.0015, "LT": 0.0}
    out = R.near_trajectory(old, periods, values, 2036)
    assert out["2031Q4"] == pytest.approx(values[0]) and "2031" not in out
    assert out["2032"] == pytest.approx(sum(out[f"2032Q{q}"] for q in (1, 2, 3, 4)) / 4)
    assert "2033Q1" in out, "разброс внутри года заметен — год пишется кварталами"
    assert not any(k.startswith("2036Q") for k in out) and out["LT"] == 0.0
    years = [k for k in out if k.isdigit() and int(k) > 2033]
    assert years and years == sorted(years) and all(abs(out[k]) >= R.NEAR_TOL for k in years)
    flat = R.near_trajectory(old, periods, [0.0] * len(periods), 2036)
    assert set(flat) == {"2031Q4", "2032Q1", "2032Q2", "2032Q3", "2032Q4", "2032", "LT"}
    assert all(v == 0.0 for v in flat.values()), "нечего гасить — траектория нулевая, без хвоста"


@tact
def test_crisis_keys_move_with_the_anchor_year():
    """Смена года якоря: ключи кризисных траекторий, период разового убытка, годы проверок и пути
    осей — на год; сдвиг года шока и прочие режимы не трогаются (М§15.2 п. 6)."""
    s = scene()
    X = copy.deepcopy(dict(s.book.source))
    old = s.book.source
    crisis = next(r for r in old["regimes"]["ids"] if "shock_year_offset" in old["regimes"][r])
    R.shift_crisis(X, 1)
    spec, was = X["regimes"][crisis], old["regimes"][crisis]
    assert spec["shock_year_offset"] == was["shock_year_offset"]
    for name in R.CRISIS_TRAJECTORIES:
        expect = {R._shift_key(k, 1): (v + 1 if k == "LT_from" else v) for k, v in was[name].items()}
        assert spec[name] == expect, name
    y, q = parse_period(was["one_off_loss"]["period"])
    assert spec["one_off_loss"] == {"period": f"{y + 1}Q{q}", "amount": was["one_off_loss"]["amount"]}
    assert list(spec["loan_growth_override"]) == [str(int(k) + 1) for k in was["loan_growth_override"]]
    assert list(X["checks"]["m_crisis_vs_cbr"]["loan_growth"]) == [
        str(int(k) + 1) for k in old["checks"]["m_crisis_vs_cbr"]["loan_growth"]]
    for r in old["regimes"]["ids"]:
        if r != crisis:
            assert X["regimes"][r] == old["regimes"][r]
    paths = [p for a in X["valuation"]["uncertainty"]["axes"] for p in a["paths"]]
    for a in old["valuation"]["uncertainty"]["axes"]:
        for p in a["paths"]:
            head, _, key = p.rpartition(".")
            if head.startswith(f"regimes.{crisis}.") and key[:4].isdigit():
                assert f"{head}.{R._shift_key(key, 1)}" in paths and p not in paths, p
    same = copy.deepcopy(dict(old))
    R.shift_crisis(same, 0)
    assert same == old


# ------------------------------------------------------------------ отказы


@tact
@pytest.mark.parametrize("changes,fragment", [
    ({"period": "1999Q4"}, "первый прогнозный квартал книги"),
    ({"extra": 1}, "незнакомые ключи extra"),
    ({"pnl.bonus": 1.0}, "незнакомые ключи bonus"),
    ({"balance.bv_common": None}, "нет ключей bv_common"),
    ({"balance.other_assets": 1.0}, "баланс не сходится"),
    ({"mgmt.nim": 6.2}, "доля единицы"),
    ({"pnl.opex": 300.0}, "со знаком минус"),
    ({"capital.n20_pre_dividend": 1}, "true или false"),
    ({"market.ofz_curve": {"1": 0.1}}, "узлы"),
    ({"balance.dividends_payable_year": None, "balance.dividends_payable": 0.0, "as_of": "2000-01-01"},
     "не конец квартала"),
    ({"schema": "чужая/1"}, "schema"),
])
def test_a_bad_report_is_refused_with_the_reason(changes, fragment):
    s = scene()
    with pytest.raises(R.ReportError) as exc:
        R.check_report(s.book, s.facts, _report(**changes))
    assert fragment in str(exc.value), str(exc.value)


@tact
def test_a_cumulative_statement_is_refused_by_the_basis_check():
    """ОПУ нарастающим итогом вместо квартала ловится сверкой с упр. метриками через мост."""
    s = scene()
    rep = _report()
    rep["pnl"] = {k: v * 3 for k, v in rep["pnl"].items()}
    rep["interest"] = {k: v * 3 for k, v in rep["interest"].items()}
    rep["dia"] *= 3
    with pytest.raises(R.ReportError, match="нарастающим итогом"):
        R.check_report(s.book, s.facts, rep)
    rep = _report()
    rep["interest"] = {k: v * 2 for k, v in rep["interest"].items()}
    with pytest.raises(R.ReportError, match="остаток"):
        R.check_report(s.book, s.facts, rep)


@tact
def test_inside_the_repository_the_tool_writes_only_under_var(tmp_path, capsys):
    """Кандидат — не канон: внутри рабочей копии запись только под `var/` (в git — один .gitkeep).
    `tests/`, `ops/`, `web/` (каталог витрины целиком уходит на Pages), корень — отказ, как и канон."""
    s = scene()
    for inside in (ROOT / "data" / "assumptions" / "x", ROOT / "data" / "facts", ROOT / "docs", ROOT / "model",
                   ROOT / "tests" / "reanchor-out", ROOT / "web", ROOT / "web" / "data", ROOT / "ops" / "tools",
                   ROOT / "functions", ROOT, ROOT / "новый-каталог", ROOT / "var" / ".." / "web"):
        with pytest.raises(R.ReportError, match="инструмент туда не пишет"):
            R.check_out(inside)
    for allowed in (ROOT / "var" / "reanchor" / "x", R.DEFAULT_OUT, ROOT.parent / "вне-репозитория", tmp_path / "c"):
        R.check_out(allowed)
    assert R.DEFAULT_OUT == ROOT / R.WRITABLE / "reanchor"
    # из командной строки: отказ до расчёта, ничего не создано
    common = ["--book", str(FIXTURE_BOOK), "--facts-dir", str(FIXTURE_FACTS)]
    report = tmp_path / "expected.json"
    report.write_text(json.dumps(s.expected), encoding="utf-8")
    for args, target in ((["--expected", str(ROOT / "web" / "expected-reanchor.json")],
                          ROOT / "web" / "expected-reanchor.json"),
                         (["--facts", str(report), "--out", str(ROOT / "tests" / "reanchor-out")],
                          ROOT / "tests" / "reanchor-out")):
        assert R.main(common + args) == 1
        assert "внутри репозитория вне var/" in capsys.readouterr().err
        assert not target.exists()


@tact
def test_bad_arguments_of_the_carry_are_refused():
    s = scene()
    with pytest.raises(R.ReportError, match="раньше конца квартала"):
        R.reanchor(s.book, s.facts, s.expected, valuation_date=R.period_end(s.period) - timedelta(days=1))
    with pytest.raises(R.ReportError, match="transmission"):
        R.reanchor(s.book, s.facts, s.expected, transmission="иначе")


# ------------------------------------------------------------------ разложение и запись


@tact
def test_the_decomposition_has_its_rows_and_the_dry_run_has_no_surprise(tmp_path):
    """Сухой прогон: обучение, кандидат на ожидаемом отчёте, сюрприз факта — ноль; кандидат и
    REANCHOR.md пишутся в каталог, книга читается ядром обратно."""
    s = scene()
    result = R.attribution(s.book, s.facts, s.expected, valuation_date=s.v, cells=False)
    steps = result["steps"]
    assert R.is_dry_run(result) and list(steps) == ["rolled", "learning", "expected", "candidate"]
    # без подмены наблюдения: наблюдение F — ожидаемое, апостериорные прежней книги с отчётом не удваиваются
    assert result["observation_override"] is None and "book_report" not in result["posterior"]
    assert steps["candidate"]["point"] == steps["expected"]["point"]
    assert steps["rolled"]["point"] == pytest.approx(s.run_a.point)
    assert steps["learning"]["point"] == pytest.approx(s.run_plus.point)
    assert steps["expected"]["point"] == pytest.approx(s.run_e.point, abs=1e-9)
    out = tmp_path / "cand"
    R.write_candidate(out, s.book, result)
    text = (out / "REANCHOR.md").read_text(encoding="utf-8")
    for title in ("(обучение)", "(механика и выпуклость, ≈ 0)", "(сюрприз факта)", "## Что сдвинулось в книге",
                  "сухой прогон", "--no-cells", "Передача ставки: `keep`", "скаляры пути σ0_A, σ0_L, φ_A, φ_L",
                  "пол вероятности режима", "## Проверки ядра на кандидате", "Инварианты: выполнены все."):
        assert title in text, title
    assert "ДИАГНОСТИКА" not in text
    checks = result["checks"]
    assert checks["candidate"]["error"] is None and checks["book"]["error"] is None
    for name in checks["candidate"]["gates"]:
        assert f"| `{name}` |" in text, name
    data = yaml.safe_load((out / "assumptions.yaml").read_text(encoding="utf-8"))
    assert data == json.loads((out / "assumptions.json").read_text(encoding="utf-8"))
    again = load_book(out / "assumptions.json", facts=load_facts(out / "facts"))
    assert run_grid(again, load_facts(out / "facts"), s.live_e).point == pytest.approx(s.run_e.point, abs=1e-9)
    assert json.loads((out / "expected.json").read_text(encoding="utf-8")) == s.expected
    changed = {R.dotted(p) for p, _, _ in result["candidate"].changes}
    assert {"meta.anchor_period", "meta.first_period", "meta.facts_date", R.OBS} <= changed
    assert all(R.reason(p) for p in changed), [p for p in changed if not R.reason(p)]


@tact
def test_a_surprise_goes_to_the_surprise_row_and_teaches_the_regimes():
    """Фактический отчёт хуже ожидания (резервы выше): ближняя калибровка ЧПМ — та, что выведена на
    ожидаемом отчёте; оценка ниже кандидата на ожидании, вероятности сдвигаются к тяжёлым режимам."""
    s = scene()
    extra = 40.0                                         # млрд ₽ резервов сверх ожидания
    rep = _report()
    tax = extra * rep["pnl"]["tax"] / rep["pnl"]["pbt"]
    for key, delta in (("llp_debt_fa", -extra), ("misc_net", 0.0), ("pbt", -extra), ("tax", -tax),
                       ("ni", -extra - tax), ("ni_shareholders", -extra - tax)):
        rep["pnl"][key] += delta
    rep["balance"]["bv_common"] += -extra - tax          # прибыль ниже — капитал ниже, резерв выше
    rep["balance"]["allowance"] += extra
    rep["balance"]["other_liabilities"] += tax
    rep["capital"]["basel_cet1"] += -extra - tax
    rep["capital"]["bank_base_capital"] += -extra - tax
    rep["capital"]["n20_0"] += (-extra - tax) / rep["capital"]["basel_rwa"]
    eng = R.engine_ratios(s.book, s.facts, rep)
    rep["mgmt"]["cor"] = s.run_plus.ctx.bridge.to_mgmt_cor(eng["cor"])
    cand = R.reanchor(s.book, s.facts, rep, valuation_date=s.v, base_run=s.run_plus, near=s.cand.near)
    run = run_grid(cand.book, cand.facts, R.live_at(cand.book, cand.facts, s.v))
    assert cand.near == s.cand.near and "nim_path" not in cand.derived
    assert run.point < s.run_e.point - 1.0, (run.point, s.run_e.point)
    assert run.cells[0].quarters["bv"][0] == pytest.approx(s.run_e.cells[0].quarters["bv"][0] - extra - tax)
    heavy = [r for r in s.book.get("regimes.ids")][-2:]
    assert sum(run.ctx.posterior[r] for r in heavy) > sum(s.run_e.ctx.posterior[r] for r in heavy)
    assert cand.derived["anchor_row"]["n20"] == pytest.approx(rep["capital"]["n20_0"], abs=1e-8)
    assert cand.data["joint"]["regime_update"]["observations"][-1]["cor"] == pytest.approx(rep["mgmt"]["cor"])


OVERRIDE_REASON = "разовый роспуск части резерва занизил стоимость риска квартала (тест)"
OVERRIDE_NOTE = "разовый роспуск резерва занизил CoR квартала (тест)"


def _override(**changes) -> dict:
    """Подмена наблюдения фикстуры: CoR отчёта + 0,2 п.п. с se, ЧПМ отчёта с se 0, подпись для витрины;
    правки — поверх."""
    mg = scene().expected["mgmt"]
    out = {"cor": mg["cor"] + 0.002, "se_cor": 0.0012, "nim": mg["nim"], "se_nim": 0.0, "reason": OVERRIDE_REASON,
           "note": OVERRIDE_NOTE}
    for key, value in changes.items():
        if value == "нет":
            out.pop(key)
        else:
            out[key] = value
    return out


@tact
@pytest.mark.parametrize("changes,fragment", [
    ({"reason": "нет"}, "нет причины"),
    ({"reason": "   "}, "нет причины"),
    ({"reason": None}, "нет причины"),
    ({"se_cor": "нет"}, "нет se_cor"),
    ({"nim": "нет", "se_nim": "нет"}, "нет nim, se_nim"),
    ({"se_cor": None}, "se_cor"),
    ({"se_nim": -0.0001}, "se_nim"),
    ({"se_cor": 0.12}, "0,12 п.п. — это 0,0012"),
    ({"cor": 1.3}, "а не проценты"),
    ({"nim": 0.0}, "а не проценты"),
    ({"nim": "6.65%"}, "а не проценты"),
    ({"cor": None, "se_cor": 0.001}, "null или 0"),
    ({"cor": None, "se_cor": None, "nim": None, "se_nim": None}, "обе величины null"),
    ({"mu_cor": {}}, "незнакомые ключи mu_cor"),
    ({"period": "1999Q1"}, "а отчёт — за"),
    ({"note": "   "}, "подпись для витрины (note) пуста"),
    ({"note": "длинная подпись " * 6}, "не длиннее 80"),
])
def test_an_override_of_the_observation_names_each_quantity_and_its_reason(changes, fragment):
    """Подмена наблюдения — решение книги: значение и se каждой величины названы явно, причина записана,
    доли — доли; иначе отказ с причиной (до расчёта)."""
    period = scene().period
    with pytest.raises(R.ReportError, match=re.escape(fragment)):
        R.check_observation(_override(**changes), period)
    good = R.check_observation(_override(period=period, cor=None, se_cor=None), period)
    assert good["cor"] is None and good["se_cor"] is None and good["nim"] == _override()["nim"]
    assert good["basis"] == "mgmt" and good["reason"] == OVERRIDE_REASON and good["note"] == OVERRIDE_NOTE
    assert "published" not in good                                       # чисел отчёта не дали — пометки нет
    with pytest.raises(R.ReportError, match="без наблюдения"):
        R.attribution(scene().book, scene().facts, scene().expected, observe=False, observation=_override())


@tact
def test_an_override_that_changes_a_published_number_carries_the_mark_for_the_dashboard():
    """Подмена, которая меняет опубликованное число отчёта или не берёт его (null), несёт пометку: опубликованные
    числа подменённых величин и подпись для витрины `note` (обязательна, не длиннее 80 знаков — та же мера, что у
    причины в книге); величина, взятая как в отчёте, в пометку не идёт; подмена одного se — не подмена числа."""
    period, mg = scene().period, scene().expected["mgmt"]
    with pytest.raises(R.ReportError, match="нужна подпись для витрины note"):
        R.check_observation(_override(note="нет"), period, mg)
    with pytest.raises(R.ReportError, match=r"\(CoR\)"):
        R.check_observation(_override(note="нет", cor=None, se_cor=None), period, mg)
    rec = R.check_observation(_override(), period, mg)
    assert rec["published"] == {"cor": mg["cor"]} and rec["note"] == OVERRIDE_NOTE
    both = R.check_observation(_override(nim=mg["nim"] - 0.001), period, mg)
    assert both["published"] == {"cor": mg["cor"], "nim": mg["nim"]}
    gone = R.check_observation(_override(cor=None, se_cor=None), period, mg)
    assert gone["published"] == {"cor": mg["cor"]} and gone["cor"] is None
    se_only = R.check_observation(_override(cor=mg["cor"], note="нет"), period, mg)   # только se — не подмена числа
    assert "published" not in se_only and "note" not in se_only
    assert R.check_observation(_override(note="  " + OVERRIDE_NOTE + "  "), period, mg)["note"] == OVERRIDE_NOTE
    assert len(OVERRIDE_NOTE) <= R.REASON_MAX == 80


@tact
def test_an_override_goes_only_to_the_observation_of_the_candidate_on_the_report(tmp_path):
    """Подмена идёт только в наблюдение кандидата на отчёте: запись окна несёт значения и se подмены с
    замороженными μ, факты — опубликованные числа отчёта (история моста), апостериорные кандидата равны
    апостериорным прежней книги с тем же наблюдением; разложение — не сухой прогон, причина — в
    REANCHOR.md, в «что сдвинулось» и в копии подмены каталога."""
    s = scene()
    ov = _override()
    result = R.attribution(s.book, s.facts, s.expected, valuation_date=s.v, cells=False, observation=ov)
    cand, steps, post = result["candidate"], result["steps"], result["posterior"]
    rec = cand.data["joint"]["regime_update"]["observations"][-1]
    assert rec["period"] == s.period and rec["basis"] == "mgmt"
    assert (rec["cor"], rec["se_cor"], rec["nim"], rec["se_nim"]) == (ov["cor"], ov["se_cor"], ov["nim"], 0.0)
    # пометка подмены (М§12): опубликованный CoR отчёта и подпись для витрины; ЧПМ взят как в отчёте — его в ней нет
    assert rec["published"] == {"cor": s.expected["mgmt"]["cor"]} and rec["reason"] == OVERRIDE_NOTE
    plain = {k: v for k, v in rec.items() if k not in R.MARK_KEYS}
    assert R.book_from_dict(cand.data, facts=cand.facts).get(R.OBS)[-1] == rec          # схема её принимает
    assert set(rec) - set(plain) == {"published", "reason"}
    assert list(rec["mu_cor"]) == list(s.book.get("regimes.ids")) and list(rec["mu_nim"]) == list(rec["mu_cor"])
    # факты не знают подмены: кандидат на отчёте и на ожидании — одни и те же факты, CoR моста — отчёта
    assert cand.facts.digest == result["candidate_expected"].facts.digest == s.cand.facts.digest
    hist = cand.facts.file("bridge_mgmt_ifrs")["cor"]["history"]
    assert hist[-1]["period"] == s.period and hist[-1]["mgmt"]["v"] == pytest.approx(s.expected["mgmt"]["cor"])
    # критерий 5: апостериорные кандидата — прежней книги с тем же наблюдением; CoR выше — тяжелее режимы
    for r in s.book.get("regimes.ids"):
        assert post["candidate"][r] == pytest.approx(post["book_report"][r], abs=1e-9), r
    heavy = list(s.book.get("regimes.ids"))[-2:]
    assert sum(post["candidate"][r] for r in heavy) > sum(post["expected"][r] for r in heavy)
    assert steps["candidate"]["point"] < steps["expected"]["point"], "CoR выше ожидания — оценка ниже"
    assert not R.is_dry_run(result)
    assert cand.derived["observation_override"] == {
        "reason": OVERRIDE_REASON, "report": {"cor": s.expected["mgmt"]["cor"], "nim": s.expected["mgmt"]["nim"]},
        "used": {k: ov[k] for k in R.OBS_FIELDS}, "note": OVERRIDE_NOTE}
    out = tmp_path / "cand"
    R.write_candidate(out, s.book, result)
    text = (out / "REANCHOR.md").read_text(encoding="utf-8")
    assert "сухой прогон" not in text and "Наблюдение закрытого квартала подменено решением книги" in text
    assert f"Пометка подмены в записи наблюдения (её печатает витрина): «{OVERRIDE_NOTE}»" in text
    assert "прежняя книга с тем же наблюдением, что у кандидата (с подменой)" in text
    row = next(line for line in text.splitlines() if line.startswith(f"| `{R.OBS}` |"))
    assert OVERRIDE_REASON in row and "вместо" in row
    assert json.loads((out / "observation.json").read_text(encoding="utf-8")) == {
        "period": s.period, **{k: ov[k] for k in R.OBS_FIELDS}, "basis": "mgmt", "reason": OVERRIDE_REASON,
        "note": OVERRIDE_NOTE, "published": {"cor": s.expected["mgmt"]["cor"]}}


@tact
def test_the_command_line_takes_the_override_from_a_file(tmp_path, capsys):
    """`--observation ФАЙЛ` (YAML или JSON): подмена — в записи наблюдения кандидата и копией в каталоге;
    негодная подмена — код 1 с причиной до расчёта, каталог не создан."""
    s = scene()
    common = ["--book", str(FIXTURE_BOOK), "--facts-dir", str(FIXTURE_FACTS), "--template", str(tmp_path / "нет")]
    report = tmp_path / "report.json"
    report.write_text(json.dumps(s.expected), encoding="utf-8")
    ov = tmp_path / "observation.yaml"
    ov.write_text(yaml.safe_dump(_override(period=s.period), allow_unicode=True), encoding="utf-8")
    out = tmp_path / "out"
    assert R.main(common + ["--facts", str(report), "--out", str(out), "--valuation-date", s.v.isoformat(),
                            "--no-cells", "--observation", str(ov)]) == 0
    printed = capsys.readouterr().out
    assert "Наблюдение закрытого квартала подменено решением книги" in printed and OVERRIDE_REASON in printed
    assert json.loads((out / "observation.json").read_text(encoding="utf-8"))["reason"] == OVERRIDE_REASON
    rec = json.loads((out / "assumptions.json").read_text(encoding="utf-8"))["joint"]["regime_update"]["observations"]
    assert (rec[-1]["cor"], rec[-1]["se_cor"]) == (_override()["cor"], _override()["se_cor"])
    assert rec[-1]["published"] == {"cor": s.expected["mgmt"]["cor"]} and rec[-1]["reason"] == OVERRIDE_NOTE
    for name, body, why in (("no-reason.json", json.dumps(_override(reason="нет")), "нет причины"),
                            ("no-note.json", json.dumps(_override(note="нет")), "нужна подпись для витрины"),
                            ("list.yaml", "- 1\n- 2\n", "ожидается словарь"),
                            ("missing.yaml", None, "файл подмены наблюдения")):
        path = tmp_path / name
        if body is not None:
            path.write_text(body, encoding="utf-8")
        bad = tmp_path / f"bad-{name}"
        assert R.main(common + ["--facts", str(report), "--out", str(bad), "--observation", str(path)]) == 1
        assert why in capsys.readouterr().err, name
        assert not bad.exists(), name


@tact
def test_the_template_patch_keeps_comments_and_equals_the_candidate():
    text = ('meta:\n  version: "1"          # версия\n  list: [1, 2]\nworlds:\n  ids: [N]\n  overlay:\n'
            '    sha256: null  # ставит сборка\nnear: {"2026": -0.005, LT: 0.0}   # ближний сдвиг\n'
            'axes:\n  - {name: "Ось, с.точкой", low: 1.0, high: 2.0}   # ось\nblock:\n  a: 1   # а\n  b: 2\n')
    data = yaml.safe_load(text)
    data["worlds"]["N"] = {"name": "мир"}
    new = copy.deepcopy(data)
    new["meta"]["version"] = "1+q"
    new["near"] = {"2026Q4": 0.001, "LT": 0.0}
    new["axes"][0]["high"] = 2.5
    new["block"]["c"] = {"x": "anchor"}
    cand = SimpleNamespace(data=new, changes=R.diff(data, new))
    out = R.patch_template(text, cand)
    got = yaml.safe_load(out)
    assert got["meta"]["version"] == "1+q" and got["near"] == new["near"] and got["axes"] == new["axes"]
    assert got["block"] == new["block"] and "N" not in got["worlds"]
    for comment in ("# версия", "# ставит сборка", "# ближний сдвиг", "# ось"):
        assert comment in out, comment
    broken = SimpleNamespace(data=new, changes=[(("нет", "такого"), None, 1)])
    with pytest.raises((R.ReportError, KeyError)):
        R.patch_template(text, broken)


@tact
def test_facts_of_two_candidates_are_compared_as_the_core_reads_them():
    """`--compare-facts`: сверяется состояние якоря, которое ядро читает из обоих наборов фактов."""
    s = scene()
    again = R.facts_after(s.book, s.facts, s.expected)
    assert R.compare_facts(s.book, s.cand.facts, again) == []
    bv, other = s.expected["balance"]["bv_common"], s.expected["balance"]["other_liabilities"]
    shifted = R.facts_after(s.book, s.facts, _report(**{"balance.bv_common": bv + 1,
                                                       "balance.other_liabilities": other - 1}))
    assert [k for k, _, _ in R.compare_facts(s.book, s.cand.facts, shifted)] == ["bv", "other_liabilities"]


# ------------------------------------------------------------------ документ процедуры


@pytest.mark.docs
def test_the_procedure_document_names_every_field_and_every_switch():
    """`docs/REANCHOR.md` — процедура дня отчёта: в ней названо каждое поле файла отчёта квартала (и в
    описании формата, и в таблице «откуда берётся») и каждый ключ командной строки инструмента."""
    text = (ROOT / "docs" / "REANCHOR.md").read_text(encoding="utf-8")
    fmt = text[text.index("## 2. Файл отчёта квартала"):text.index("## 3. Что переносится")]
    day = text[text.index("## 1. День отчёта"):text.index("## 2. Файл отчёта квартала")]

    def named(field: str, where: str) -> bool:
        return re.search(rf"(?<![A-Za-z_]){re.escape(field)}(?![A-Za-z_])", where) is not None

    for field in R.TOP_REQUIRED + R.TOP_OPTIONAL:
        assert named(field, fmt), field
    for section, (required, optional) in R.SECTIONS.items():
        for field in required + optional:
            assert named(field, fmt), f"{section}.{field}"
        for field in required:
            assert named(field, day), f"день отчёта: {section}.{field}"
    for switch in ("--expected", "--facts", "--out", "--valuation-date", "--band", "--compare-facts",
                   "--transmission", "--no-cells", "--observation"):
        assert switch in text, switch
    for field in R.OBS_KEYS:                     # формат файла подмены наблюдения — в §3.1
        assert named(field, text[text.index("### 3.1."):text.index("### 3.2.")]), f"подмена наблюдения: {field}"
    for phrase in ("Критерии приёмки", "Что скачать", "Какие числа внести", "Что записать", "data/facts/sheets/",
                   "docs/DECISIONS.md", "CHANGES.md", f"только под `{R.WRITABLE}/`"):
        assert phrase in text, phrase
    assert f"{R.MECHANICS_TOL * 100:g}".replace(".", ",") + " %" in day, "допуск механики — числом инструмента"


# ------------------------------------------------------------------ полный набор (CI)


@ci_only
def test_every_cell_keeps_its_value_and_the_layers_do_not_move():
    s = scene()
    mech = R.cell_mechanics(s.book, s.facts, s.run_plus, s.obs, s.v)
    assert len(mech["rows"]) == len(s.run_plus.cells) and mech["ok"]
    assert abs(mech["worst"]["rel"]) <= R.MECHANICS_TOL / 10, mech["worst"]
    step = float(s.book.get("valuation.headline.print_step"))
    assert abs(mech["headline"]["point"] - s.run_plus.point) <= 0.01 * step


@ci_only
def test_the_median_on_the_expected_report_is_the_rolled_median():
    """Заголовок: медиана кандидата на ожидаемом отчёте — в доле шага печати от медианы прежней
    книги с тем же наблюдением (одни точки гиперкуба), края полосы 80 % — в шаге печати.

    Ноля здесь нет по построению: закрытый квартал перестаёт разыгрываться, и оси с несимметричным
    диапазоном, часть эффекта которых приходилась на него (реальный рост расходов года якоря),
    сдвигают медиану в сторону значения книги."""
    s = scene()
    step = float(s.book.get("valuation.headline.print_step"))
    old = R.band_stats(s.plus, s.facts, s.live, BAND_DRAWS)
    new = R.band_stats(s.cand.book, s.cand.facts, s.live_e, BAND_DRAWS)
    assert abs(new["median"] - old["median"]) <= MEDIAN_SHARE * step, (old, new)
    for key in ("p10", "p90"):
        assert abs(new[key] - old[key]) <= step, (key, old[key], new[key])


@ci_only
def test_resolving_the_targets_again_moves_the_value_without_news():
    """`resolve` (диагностика): цели A-N2, A-N3 и доли не тронуты, скаляры пути решены на структуре
    нового якоря — оценка сдвигается без единой новости; `keep` этот сдвиг снимает."""
    s = scene()
    cand = R.reanchor(s.book, s.facts, s.expected, valuation_date=s.v, base_run=s.run_plus,
                      transmission=R.RESOLVE)
    for path in R.TRANSMISSION_KEYS:
        assert R._get(cand.data, path) == R._get(s.book.source, path), path
    assert cand.data["valuation"]["uncertainty"]["axes"][0] == s.book.source["valuation"]["uncertainty"]["axes"][0]
    run = run_grid(cand.book, cand.facts, s.live_e)
    assert R._sigmas(run.ctx.transmission) != pytest.approx(R._sigmas(s.run_plus.ctx.transmission), abs=1e-5)
    assert abs(run.point - s.run_plus.point) > 10 * abs(s.run_e.point - s.run_plus.point)
    assert cand.near == s.cand.near, "ближняя калибровка ЧПМ — та же: она не прячет сдвиг скаляров"
    mech = R.cell_mechanics(s.book, s.facts, s.run_plus, s.obs, s.v, cells=CELLS[2:], transmission=R.RESOLVE)
    assert not mech["ok"], "сдвиг целиком виден в строке «механика»"


@ci_only
def test_the_fx_share_of_rwa_is_carried_to_the_new_anchor():
    """Валютная доля RWA: индекс курса стартует с единицы, доля пересчитывается — путь RWA тот же."""
    book = fixture_book().with_overrides({"capital.rwa.fx.enabled": True, "capital.rwa.fx.share": 0.15,
                                          "capital.rwa.fx.foreign_inflation": 0.02})
    s = scene_of(book, fixture_facts())
    assert s.cand.data["capital"]["rwa"]["fx"]["share"] != 0.15
    mech = R.cell_mechanics(book, s.facts, s.run_plus, s.obs, s.v, cells=CELLS[1:])
    assert mech["ok"] and abs(mech["worst"]["rel"]) <= R.MECHANICS_TOL / 5, mech["worst"]


@ci_only
def test_a_chain_of_reports_walks_through_the_change_of_the_anchor_year():
    """Квартал за кварталом на ожидаемых отчётах, до квартала ГОСА следующего года: в году якоря —
    прокатка; на смене года кризис сдвигается на год, его клетки «перезаряжены» и в допуск механики
    не входят, прочие — в допуске; квартал ГОСА переносит дивиденды к выплате без механики."""
    book, facts = fixture_book(), fixture_facts()
    step = float(book.get("valuation.headline.print_step"))
    crisis = next(r for r in book.get("regimes.ids") if "shock_year_offset" in book.source["regimes"][r])
    offset = int(book.source["regimes"][crisis]["shock_year_offset"])
    years_changed = 0
    share = float(book.get("joint.regime_update.floor_share"))
    records: list[dict] = []
    for _ in range(4):
        s = scene_of(book, facts)
        anchor_year = parse_period(s.period)[0]
        obs = s.cand.data["joint"]["regime_update"]["observations"]
        assert obs[:-1] == records[-(len(obs) - 1):] if len(obs) > 1 else not records, "μ записей окна не пересчитаны"
        frozen = R.frozen_expectations(book, facts)
        assert (obs[-1]["mu_cor"], obs[-1]["mu_nim"]) == (frozen["mu_cor"], frozen["mu_nim"])
        records = copy.deepcopy(obs)
        for r, p in book.get("joint.regime_prob").items():
            assert s.run_e.ctx.posterior[r] >= share * p - 1e-12, (s.period, r)
        loss_year = parse_period(s.cand.data["regimes"][crisis]["one_off_loss"]["period"])[0]
        assert loss_year == anchor_year + offset == anchor_year + int(
            s.cand.data["regimes"][crisis]["shock_year_offset"])
        assert len(s.cand.data["joint"]["regime_update"]["observations"]) <= int(
            book.get("joint.regime_update.window_obs"))
        result = R.attribution(book, facts, s.expected, valuation_date=s.v, cells=False)
        text = R.report_text(book, result)
        if s.cand.derived["year_shift"]:
            years_changed += 1
            # квартал шока: сухой прогон печатает и обучение на отчёте без шока (М§12, §15.2)
            calm = result["no_shock"]
            assert calm["regimes"] == [crisis] == R.shock_regimes(s.run_a)
            assert calm["observation"]["cor"] < s.obs["cor"] < calm["shock_cor"][crisis]
            assert calm["observation"] == R.expected_observation(book, facts, s.live, without=[crisis])
            assert 0 < calm["shock_probability"] < 1 and sum(calm["posterior"].values()) == pytest.approx(1.0)
            assert calm["posterior"][crisis] < s.run_a.ctx.posterior[crisis], "без шока режим с шоком теряет вероятность"
            assert f"**Квартал шока режима {crisis}.**" in text and "В журнал версии книги — оба числа." in text
            assert int(s.cand.data["noncore"]["price_base_year"]) == anchor_year
            assert any("год якоря сменился" in w and "не пересчитываются" in w for w in s.cand.warnings)
            shocked = max(obs[-1]["mu_cor"], key=obs[-1]["mu_cor"].get)
            assert shocked == crisis, "закрытый квартал судится против ожидания прежней книги — с шоком"
            mech = R.cell_mechanics(book, facts, s.run_plus, s.obs, s.v, cells=[CELLS[1], ("N", crisis, "upper")])
            calm, rearmed = mech["rows"]
            assert mech["ok"] and mech["rearmed"] == [crisis] and rearmed["rearmed"] and not calm["rearmed"]
            assert abs(calm["rel"]) <= R.MECHANICS_TOL / 10 < abs(rearmed["rel"]), mech["rows"]
        else:
            assert abs(s.run_e.point - s.run_plus.point) <= CHAIN_SHARE * step, s.period
            assert "no_shock" not in result and "Квартал шока" not in text and R.shock_regimes(s.run_a) == []
        last, book, facts = s, s.cand.book, s.cand.facts
    assert years_changed == 1, "цепочка прошла смену года якоря"
    assert last.expected["balance"]["dividends_payable"] > 0 and last.expected["capital"]["n20_pre_dividend"]
    mech = R.cell_mechanics(last.book, last.facts, last.run_plus, last.obs, last.v, cells=CELLS[:2])
    assert mech["ok"] and abs(mech["worst"]["rel"]) <= R.MECHANICS_TOL / 10, mech["worst"]


@ci_only
def test_the_command_line_dry_run_prints_the_four_rows(tmp_path, capsys):
    """`--expected`, затем `--facts` на том же файле: кандидат, четыре строки разложения точки и
    разложение медианы; чужой квартал — код 1 с причиной."""
    common = ["--book", str(FIXTURE_BOOK), "--facts-dir", str(FIXTURE_FACTS), "--template", str(tmp_path / "нет")]
    expected = tmp_path / "expected.json"
    assert R.main(common + ["--expected", str(expected)]) == 0
    assert "ожидаемый отчёт" in capsys.readouterr().out
    rep = json.loads(expected.read_text(encoding="utf-8"))
    v = (R.period_end(rep["period"]) + timedelta(days=2)).isoformat()
    out = tmp_path / "out"
    assert R.main(common + ["--facts", str(expected), "--out", str(out), "--valuation-date", v,
                            "--band", "8"]) == 0
    printed = capsys.readouterr().out
    for title in ("(обучение)", "(механика, ≈ 0)", "(выпуклость)", "(сюрприз факта)", "медиана полосы",
                  "допуск 0,1 % — выполнен", "Передача ставки: `keep`", "## Проверки ядра на кандидате"):
        assert title in printed, title
    assert (out / "REANCHOR.md").read_text(encoding="utf-8").strip() == printed.strip()
    assert sorted(p.name for p in out.iterdir()) == ["REANCHOR.md", "assumptions.json", "assumptions.yaml",
                                                     "expected.json", "facts"]
    rep["period"] = R.next_period(rep["period"])
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(rep), encoding="utf-8")
    assert R.main(common + ["--facts", str(wrong), "--out", str(out)]) == 1
    assert "первый прогнозный квартал книги" in capsys.readouterr().err
    assert R.main(common + ["--facts", str(expected), "--out", str(ROOT / "data" / "facts")]) == 1
    assert "внутри репозитория вне var/" in capsys.readouterr().err
    diagnosis = tmp_path / "resolve"
    assert R.main(common + ["--facts", str(expected), "--out", str(diagnosis), "--valuation-date", v,
                            "--transmission", "resolve", "--no-cells"]) == 0
    assert "ДИАГНОСТИКА: режим `resolve`" in capsys.readouterr().out
    assert sorted(p.name for p in diagnosis.iterdir()) == ["REANCHOR.md", "expected.json"]
    again = tmp_path / "again"
    assert R.main(common + ["--facts", str(expected), "--out", str(again), "--valuation-date", v, "--no-cells",
                            "--compare-facts", str(out / "facts")]) == 0
    assert "Расхождений нет" in capsys.readouterr().out


@ci_only
def test_the_real_book_reanchors_on_its_expected_report(tmp_path):
    """Сухой прогон на настоящей книге: клетки по мирам и режимам — в допуске, точка — в доле шага
    печати, шаблон книги с правками равен кандидату и сохраняет комментарии."""
    book, facts = real_book_or_skip()
    s = scene_of(book, facts)
    step = float(book.get("valuation.headline.print_step"))
    mech = R.cell_mechanics(book, facts, s.run_plus, s.obs, s.v, cells=CELLS)
    assert mech["ok"], mech["worst"]                                 # критерий приёмки: 0,1 % стоимости клетки
    # Сторож — 1/5 допуска к большему из стоимости и капитала клетки: у клетки глубокого кризиса стоимость — малая
    # доля капитала (M/crisis/strict книги 1.5 — 1 322 при капитале ≈ 9 000, P/B 0,14), и та же механика в рублях
    # (+0,29 млрд ₽ и у книги 1.4, и у 1.5: дивиденд при связывающем капитале — малая разность запаса и пула) к
    # стоимости — 0,014 % у 1.4 и 0,022 % у 1.5 (DECISIONS 14.6)
    for r in mech["rows"]:
        scale = max(abs(r["old"]), abs(s.run_plus.cell(*r["key"]).bv_v))
        assert abs(r["new"] - r["old"]) <= R.MECHANICS_TOL / 5 * scale, r
    assert abs(s.run_e.point - s.run_plus.point) <= POINT_SHARE * step
    template = R.TEMPLATE
    if template.exists():
        patched = R.patch_template(template.read_text(encoding="utf-8"), s.cand)
        assert patched.count("#") >= template.read_text(encoding="utf-8").count("#") - 5
        assert "#{{WORLDS}}" in patched and "#{{WORLDS_BANK}}" in patched
