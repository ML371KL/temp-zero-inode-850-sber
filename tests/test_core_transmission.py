"""Передача ставки (М§4.5): реализованная передача движка = цели A-N3, Nss(H) = цели A-N2."""

from __future__ import annotations

import pytest

from model.book_schema import BookError
from model.credit import bridge_from_facts
from model.nii import nss_value, solve_transmission
from model.worlds import BASE_WORLD, MARKET_WORLD
from tests.support_core import draft_book, fixture_book, fixture_facts, fixture_run

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def _solve(book):
    facts = fixture_facts()
    return solve_transmission(book, facts, bridge_from_facts(facts))


def test_realized_transmission_equals_target():
    book = fixture_book()
    tr = _solve(book)
    tol = float(book.get("checks.transmission_tol"))
    assert abs(tr.t_real - float(book.get("nii.transmission.target"))) <= tol
    ref = str(book.get("nii.transmission.reference_world"))
    assert abs(nss_value(book, fixture_facts(), ref, 0.0, tr.sigma0, tr.phi, tr.sigma0_liab) - tr.target_eng) <= tol
    bridge = bridge_from_facts(fixture_facts())
    assert tr.target_eng == pytest.approx(bridge.to_engine_nim(float(book.get("nii.nim_lt_target_mgmt"))))


@pytest.mark.parametrize("target", [0.0, 0.03, 0.10, 0.14])
def test_transmission_solved_along_the_axis(target):
    book = fixture_book().with_overrides({"nii.transmission.target": target})
    tr = _solve(book)
    assert tr.t_real == pytest.approx(target, abs=1e-12)
    assert tr.nss[tr.reference_world] == pytest.approx(tr.target_eng, abs=1e-12)


def test_sigma0_cancels_in_realized_transmission():
    """σ0_A в разности миров сокращается; σ0_L — тоже, если `lt_shift` у обеих книг средств ФЛ одинаков
    (М§4.5): тогда φ не зависит от A-N2."""
    base = fixture_book().with_overrides({"nii.sigma0_split": 1.0})
    a = _solve(base)
    b = _solve(base.with_overrides({"nii.nim_lt_target_mgmt": 0.066}))
    assert b.sigma0 != pytest.approx(a.sigma0)
    assert b.phi == pytest.approx(a.phi, abs=1e-12)
    books = {k: dict(v) for k, v in fixture_book().get("nii.books").items()}
    for k in ("retail_current", "retail_term", "corp_funds"):
        books[k]["lt_shift"] = True
    same = fixture_book().with_overrides({"nii.books": books, "nii.sigma0_split": 0.3})
    c, d = _solve(same), _solve(same.with_overrides({"nii.nim_lt_target_mgmt": 0.066}))
    assert d.sigma0_liab != pytest.approx(c.sigma0_liab)
    assert d.phi == pytest.approx(c.phi, abs=1e-12)


def test_phi_is_zero_in_reference_world_and_local_is_diagnostic():
    tr = _solve(fixture_book())
    assert set(tr.t_local) == set(fixture_book().get("worlds.ids"))
    assert tr.d != 0
    assert tr.roe_equiv > 0


def test_draft_book_transmission_solved():
    tr = _solve(draft_book())
    assert tr.t_real == pytest.approx(float(draft_book().get("nii.transmission.target")), abs=1e-9)


def test_transmission_undefined_refuses():
    book = fixture_book()
    key_n = dict(book.get(f"worlds.{BASE_WORLD}.key_rate"))
    with pytest.raises(BookError):
        _solve(book.with_overrides({f"worlds.{MARKET_WORLD}.key_rate": key_n}))


def test_cells_transmission_is_a_diagnostic():
    """Передача по клеткам (M, norm, schedule) − (N, norm, schedule) последнего года — число, не цель."""
    from model.grid import derived_values, realized_cells
    run = fixture_run()
    rc = realized_cells(run)
    assert -0.5 < rc < 0.5
    d = derived_values(run.ctx)
    assert d["nii"]["T_real"] == run.ctx.transmission.t_real
    assert set(d["capital"]["floor20"]) == set(run.ctx.book.get("capital.reg_scenarios.ids"))
    assert d["anchor"] == dict(run.ctx.book.anchors)
