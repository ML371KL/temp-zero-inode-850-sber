"""Закрытая схема книги (М прил. A, инвариант book_schema): громкий отказ BookError."""

from __future__ import annotations

import copy

import pytest

from model.book import book_from_dict
from model.book_schema import KEYS, SWITCHES, BookError, read_paths, validate
from tests.support_core import book_with, draft_book, fixture_book, fixture_dict, fixture_facts

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def _fails(data, *fragments):
    with pytest.raises(BookError) as exc:
        book_from_dict(data, facts=fixture_facts())
    for fr in fragments:
        assert fr in str(exc.value), str(exc.value)


def test_fixture_book_is_in_schema():
    book = fixture_book()
    validate(book.data)
    assert book.anchors["credit.allowance_ratio"] > 0


def test_draft_book_with_worlds_loads():
    """Черновик книги 1.0 + миры и надстройка — машинная книга, которую ядро читает."""
    book = draft_book()
    assert book.get("meta.last_period")
    assert all(v != "anchor" for v in book.anchors.values())


def test_unknown_key_fails_loudly():
    _fails(book_with({"credit.kappa_typo": 0.1}), "credit.kappa_typo", "незнакомый ключ")
    _fails(book_with({"nii.books.corp_loans.spred": 0.01}), "spred")
    _fails(book_with({"totally_new_block": {"x": 1}}), "totally_new_block")


def test_missing_key_fails_loudly():
    _fails(book_with({}, drop=("credit.kappa",)), "credit.kappa", "нет ключа")
    _fails(book_with({}, drop=("worlds.M.real_key",)), "worlds.M.real_key")
    _fails(book_with({}, drop=("joint.regime_prob.crisis",)), "joint.regime_prob.crisis")


def test_null_in_read_path_is_not_zero():
    _fails(book_with({"credit.kappa": None}), "credit.kappa", "null")
    _fails(book_with({"regimes.norm.cor.LT": None}), "regimes.norm.cor")


def test_switched_off_keys_may_be_null_switched_on_may_not():
    data = fixture_dict()
    assert data["credit"]["segment_relative"]["enabled"] is False
    assert data["credit"]["segment_relative"]["corporate"] is None
    book_from_dict(data, facts=fixture_facts())                       # выключено — null можно
    _fails(book_with({"credit.segment_relative.enabled": True}), "credit.segment_relative.corporate")
    _fails(book_with({"capital.rwa.fx.enabled": True}), "capital.rwa.fx.share")
    _fails(book_with({"other.fvtpl_bond_reval": True}), "oci.fvtpl_bond_duration")
    assert "credit.segment_relative.corporate" not in read_paths(data)
    assert "credit.kappa" in read_paths(data)


def test_unimplemented_modules_and_values_refuse():
    _fails(book_with({"capital.recapitalization.enabled": True}), "не реализован")
    _fails(book_with({"dividends.buyback.enabled": True}), "не реализован")
    _fails(book_with({"dividends.policy.base": "ifrs_ni_adjusted"}), "зарезервировано")


@pytest.mark.parametrize("path,value", [
    ("valuation.uncertainty.axes.0.kind", "percent"),
    ("valuation.uncertainty.axes.0.dist", "normal"),
    ("dividends.policy.base", "net_income"),
    ("dividends.policy.shortfall_rule", "cut_to_zero"),
    ("regimes.cor_basis", "ifrs"),
    ("nii.books.corp_loans.ref", "ruonia"),
])
def test_unknown_enumerations_refuse(path, value):
    _fails(book_with({path: value}), value)


def test_trajectory_rules_of_section_0_4():
    last_year = int(fixture_dict()["meta"]["last_period"][:4])
    traj = dict(fixture_dict()["regimes"]["norm"]["cor"])
    traj["LT_from"] = last_year + 1
    _fails(book_with({"regimes.norm.cor": traj}), "LT_from")
    traj = dict(fixture_dict()["regimes"]["norm"]["cor"])
    traj[f"{last_year}Q2"] = 0.02
    _fails(book_with({"regimes.norm.cor": traj}), "last_period")
    traj = dict(fixture_dict()["regimes"]["norm"]["cor"])
    traj["2027-01"] = 0.02
    _fails(book_with({"regimes.norm.cor": traj}), "не разбирается")
    # ключ года при квартальных ключах — среднее года
    traj = dict(fixture_dict()["regimes"]["crisis"]["cor"])
    traj["2027"] = traj["2027"] + 0.001
    _fails(book_with({"regimes.crisis.cor": traj}), "среднему кварталов")


def test_world_trajectory_must_reach_last_period():
    key = dict(fixture_dict()["worlds"]["N"]["key_rate"])
    last = max(key)
    del key[last]
    _fails(book_with({"worlds.N.key_rate": key}), "не доходит")


def test_axis_paths_must_exist_and_governance_must_sum():
    data = fixture_dict()
    axes = copy.deepcopy(data["valuation"]["uncertainty"]["axes"])
    axes[0]["paths"] = ["nii.nim_lt_target_typo"]
    _fails(book_with({"valuation.uncertainty.axes": axes}), "не найден")
    _fails(book_with({"valuation.governance.discount": 0.05}), "Σ sign × value")


def test_keys_cover_appendix_a_templates():
    keys = set(KEYS)
    for k in ("meta.company.main_ticker", "worlds.<W>.zero_curve.LT", "joint.reg_prob_given_regime.<r>.<s>",
              "regimes.<r>.cor", "nii.books.<b>.spread", "capital.reg_scenarios.<s>.deduction_pp.n1_1",
              "capital.n11.audit_cutoffs[]", "dividends.calendar.reg_deduction_quarter",
              "valuation.uncertainty.axes[].dist", "checks.m_crisis_vs_cbr.loan_growth.<y>",
              "worlds_bank.<W>.credit_growth.retail_other.<y>", "capital.rwa.density.<rwa>"):
        assert k in keys, k
    for switch, off in SWITCHES.items():
        for k in off:
            assert k in keys


def test_with_overrides_keeps_schema_and_anchors():
    book = fixture_book()
    b2 = book.with_overrides({"credit.kappa": 0.1})
    assert b2.get("credit.kappa") == 0.1 and book.get("credit.kappa") != 0.1
    assert b2.anchors == book.anchors
    assert b2.digest != book.digest
    with pytest.raises(BookError):
        book.with_overrides({"credit.kappa_typo": 0.1})
    with pytest.raises(BookError):
        book.with_overrides({"credit.kappa": None})


def test_w1_keys_are_in_the_closed_schema():
    """Ключи прил. A волны W1 (LEAD-DECISIONS-2) — в закрытой схеме и в KEYS."""
    keys = set(KEYS)
    for k in ("meta.company.share_classes.<t>", "regimes.near_nim_shift", "nii.sigma0_from",
              "other.misc_quarter_shares.1", "other.misc_quarter_shares.4", "dividends.excess.ramp_years",
              "valuation.uncertainty.off_band_axes[].kind", "checks.nim_path_joint.quarters",
              "checks.nim_path_joint.tolerance", "checks.transmission_pairs", "checks.ni_jump.quarters",
              "checks.ni_jump.yoy", "checks.ni_jump.key_tol"):
        assert k in keys, k
    for dotted in ("regimes.near_nim_shift", "nii.sigma0_from", "other.misc_quarter_shares",
                   "dividends.excess.ramp_years", "checks.ni_jump"):
        _fails(book_with({}, drop=(dotted,)), dotted, "нет ключа")
    _fails(book_with({"meta.company.share_classes": {"SBER": "ordinary", "SBERP": "common"}}), "common")
    _fails(book_with({"meta.company.share_classes": {"SBER": "ordinary"}}), "share_classes.SBERP")


def test_w1_semantic_rules():
    data = fixture_dict()
    first_year = int(data["meta"]["first_period"][:4])
    _fails(book_with({"nii.sigma0_from": first_year - 1}), "sigma0_from")
    _fails(book_with({"other.misc_quarter_shares.1": 0.5}), "misc_quarter_shares", "≠ 1")
    _fails(book_with({"dividends.excess.ramp_years": 0}), "ramp_years")
    _fails(book_with({"checks.ni_jump.quarters": 0}), "ni_jump.quarters")
    band_axis = copy.deepcopy(data["valuation"]["uncertainty"]["axes"][0])
    _fails(book_with({"valuation.uncertainty.off_band_axes": [band_axis]}), "и в полосе, и вне полосы")
    dict_axis = next(a for a in data["valuation"]["uncertainty"]["axes"] if a["kind"] == "dict")
    rest = [a for a in data["valuation"]["uncertainty"]["axes"] if a is not dict_axis]
    _fails(book_with({"valuation.uncertainty.axes": rest, "valuation.uncertainty.off_band_axes": [dict_axis]}),
           "только value и shift")


def test_w2_keys_are_in_the_closed_schema():
    """Ключи прил. A волны W2 (LEAD-DECISIONS-3) — в закрытой схеме и в KEYS; `shock_year` снят."""
    keys = set(KEYS)
    for k in ("joint.regime_update.window_obs", "joint.regime_update.observations[].mu_cor.<r>",
              "joint.regime_update.observations[].mu_nim.<r>", "regimes.<r>.shock_year_offset",
              "credit.fv_loans_ref", "nii.sigma0_split", "nii.books.<b>.lt_shift", "checks.lt_spread_floor.<b>"):
        assert k in keys, k
    assert "regimes.<r>.shock_year" not in keys
    for dotted in ("joint.regime_update.window_obs", "credit.fv_loans_ref", "nii.sigma0_split",
                   "checks.lt_spread_floor", "nii.books.retail_term.lt_shift"):
        _fails(book_with({}, drop=(dotted,)), dotted, "нет ключа")
    _fails(book_with({"credit.fv_loans_ref": None}), "fv_loans_ref", "null")
    _fails(book_with({"nii.sigma0_split": -0.1}), "sigma0_split")
    _fails(book_with({"checks.lt_spread_floor.liquidity": 0.0}), "lt_spread_floor.liquidity")
