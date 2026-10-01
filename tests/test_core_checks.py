"""Гейты и объяснения (М§14.2): статусы quiet / explained / unexplained / expired / mass_off."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from model.checks import (CORE_FLAGS, GATES, INVARIANTS, Finding, check_gates, check_invariants, gate_statuses,
                          load_gate_explanations, mass_corridor)
from tests.support_core import fixture_run

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def test_all_names_of_section_14_are_reported():
    run = fixture_run()
    assert [f.name for f in check_invariants(run)] == list(INVARIANTS)
    found = check_gates(run)
    gates = [f for f in found if f.kind == "gate"]
    assert [f.name for f in gates] == list(GATES)
    for f in gates:
        assert f.mass is None or 0.0 <= f.mass <= 1.0 + 1e-12
    assert [f.name for f in found if f.kind == "flag"] == list(CORE_FLAGS)
    assert len(found) == len(GATES) + len(CORE_FLAGS)


def _f(name, fired, mass):
    return Finding(name=name, kind="gate", fired=fired, mass=mass, cells=(), message="")


def test_gate_statuses():
    today = date(2030, 1, 10)
    expl = {
        "a": {"explanation": "текст", "expected_mass": 0.2, "valid_until": today},
        "b": {"explanation": "текст", "expected_mass_range": (0.1, 0.2), "valid_until": today - timedelta(days=1)},
        "c": {"explanation": "текст", "expected_mass": 0.2, "valid_until": today + timedelta(days=10)},
    }
    got = {s.name: s for s in gate_statuses(
        [_f("a", True, 0.25), _f("b", True, 0.15), _f("c", True, 0.9), _f("d", True, 0.1), _f("e", False, 0.0)],
        expl, today=today)}
    assert got["a"].status == "explained"                  # действует включительно
    assert got["b"].status == "expired"
    assert got["c"].status == "mass_off" and got["c"].expiring
    assert got["d"].status == "unexplained"
    assert got["e"].status == "quiet"
    assert mass_corridor(0.2) == pytest.approx((0.4 * 0.2 - 0.02, 1.5 * 0.2 + 0.02))


def test_load_gate_explanations(tmp_path):
    p = tmp_path / "gate_explanations.yaml"
    p.write_text("guidance_gap:\n  explanation: до МСФО 3К\n  expected_mass_range: [0.5, 1.0]\n"
                 "  valid_until: 2030-10-28\n", encoding="utf-8")
    got = load_gate_explanations(p, today=date(2030, 10, 1))
    assert got["guidance_gap"]["expected_mass_range"] == (0.5, 1.0)
    assert got["guidance_gap"]["expiring"] and not got["guidance_gap"]["stale"]
    assert load_gate_explanations(tmp_path / "нет.yaml") == {}
