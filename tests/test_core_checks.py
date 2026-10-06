"""Гейты и объяснения (М§14.2): статусы quiet / explained / unexplained / expired / mass_off."""

from __future__ import annotations

from dataclasses import replace
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


def _over_paid(run):
    """Прогон с одним решением, у которого выплата года на 1 млрд ₽ больше запаса капитала ступени."""
    cell = next(c for c in run.cells if any(d.source == "model" and d.excess > 0 for d in c.decisions))
    d = next(d for d in cell.decisions if d.source == "model" and d.excess > 0)
    h = d.headroom[d.step]
    over = replace(d, excess=d.excess + (h - d.div) + 1.0, div=h + 1.0)
    changed = replace(cell, decisions=tuple(over if x is d else x for x in cell.decisions))
    return replace(run, cells=tuple(changed if c is cell else c for c in run.cells)), f"{cell.label} {d.year}"


def test_dividend_bounds_compares_the_whole_payout_with_headroom():
    """Инвариант сравнивает с запасом капитала ВСЮ выплату года — базовую, догоняющую и долю избытка: они
    части Div_Y, а не добавка к границе (М§5.3 п. 4, §14.1)."""
    run = fixture_run()
    assert not next(f for f in check_invariants(run) if f.name == "dividend_bounds").fired
    for c in run.cells:                                    # на значениях книги выплата не больше запаса
        for d in c.decisions:
            if d.source == "model":
                assert d.div <= max(0.0, d.headroom[d.step]) + 1e-9 * max(1.0, d.div), (c.label, d.year)
    bad, where = _over_paid(run)
    found = next(f for f in check_invariants(bad) if f.name == "dividend_bounds")
    assert found.fired and where in found.message


def test_probabilities_invariant_names_a_negative_weight():
    """Таблица с суммой 1 и отрицательным весом — не распределение: клетка вошла бы в оценку с обратным
    знаком (М§3.4, §14.1)."""
    run = fixture_run()
    assert not next(f for f in check_invariants(run) if f.name == "probabilities").fired
    layer = run.layers["analytical"]
    (k1, p1), (k2, p2) = list(layer.prob.items())[:2]
    prob = {**layer.prob, k1: -p1, k2: p2 + 2 * p1}
    assert sum(prob.values()) == pytest.approx(1.0, abs=1e-12)
    bad = replace(run, layers={**run.layers, "analytical": replace(layer, prob=prob)})
    found = next(f for f in check_invariants(bad) if f.name == "probabilities")
    assert found.fired and "отрицательные вероятности" in found.message and "≠ 1" not in found.message


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
