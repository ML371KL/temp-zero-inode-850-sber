"""Ядро на настоящей книге и фактах (`assumptions.yaml`, `data/facts/`); пока их нет — пропуск."""

from __future__ import annotations

import functools

import pytest

from model.checks import check_gates, check_invariants
from model.grid import run_grid
from tests.support_core import real_book_or_skip

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


@functools.lru_cache(maxsize=None)
def _run():
    book, facts = real_book_or_skip()
    return run_grid(book, facts)


def test_real_book_invariants_hold():
    run = _run()
    fired = [(f.name, f.message) for f in check_invariants(run) if f.fired]
    assert not fired, fired


def test_real_book_ddm_equals_ri_in_every_cell():
    run = _run()
    tol = float(run.ctx.book.get("checks.ddm_ri_tol"))
    for c in run.cells:
        assert abs(c.v_ddm - c.v_ri) <= tol * abs(c.v_ri), c.label


def test_real_book_anchor_reproduces_facts():
    run = _run()
    facts = run.ctx.facts
    c = run.cells[0]
    assert c.quarters["n20"][0] == pytest.approx(facts.need("capital", "n20_0.value"), abs=5e-4)
    assert c.quarters["n11"][0] == pytest.approx(facts.need("capital", "n1_1_bank.value"), abs=5e-4)


def test_real_book_transmission_and_gates_run():
    run = _run()
    tr = run.ctx.transmission
    assert tr.t_real == pytest.approx(float(run.ctx.book.get("nii.transmission.target")),
                                      abs=float(run.ctx.book.get("checks.transmission_tol")))
    names = [f.name for f in check_gates(run)]
    assert "roe_k_homogeneity" in names and "capital_gap" in names
