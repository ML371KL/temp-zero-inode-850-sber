"""Траектории книги (М§0.4), кварталы и дата оценки (М§0.2)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from model.book_schema import BookError
from model.paths import get_path, override_value, path_value, set_path, shift_trajectory, year_mean
from model.timeline import make_clock, make_timeline, parse_period
from tests.support_core import fixture_book

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def test_quarter_half_year_year_rules():
    traj = {"2027Q1": 0.05, "2027": 0.038, "2028H2": 0.02, "2029": 0.01, "LT": 0.03, "LT_from": 2032}
    assert path_value(traj, "2027Q1") == 0.05                    # правило 1: ключ квартала
    assert path_value(traj, "2027Q3") == 0.038                   # правило 3: ключ года
    assert path_value(traj, "2028Q4") == 0.02                    # правило 2: ключ полугодия
    assert path_value(traj, "2029Q1") == 0.01


def test_interpolation_convergence_and_flat_back():
    traj = {"2026": 0.01, "2028": 0.03, "LT": 0.05, "LT_from": 2031}
    assert path_value(traj, "2027Q2") == pytest.approx(0.02)     # правило 4: линейно по годам
    assert path_value(traj, "2029Q1") == pytest.approx(0.03 + 0.02 / 3)   # правило 5: сход к LT
    assert path_value(traj, "2030Q4") == pytest.approx(0.03 + 0.04 / 3)
    assert path_value(traj, "2031Q1") == 0.05                    # с LT_from — LT
    assert path_value(traj, "2025Q1") == 0.01                    # правило 6: плоско назад
    assert path_value({"2026": 0.01, "LT": 0.02}, "2027Q1") == 0.02   # LT без LT_from — сразу
    assert path_value({"2026": 0.01, "2027": 0.02}, "2035Q1") == 0.02  # без LT — последнее
    assert path_value(0.7, "2030Q2") == 0.7                      # скаляр
    assert path_value({"LT": 0.004}, "2026Q3") == 0.004


def test_world_half_years_and_first_key_flat_back():
    traj = {"2026H2": 0.139, "2027H1": 0.13}
    assert path_value(traj, "2026Q3") == path_value(traj, "2026Q4") == 0.139
    assert path_value(traj, "2027Q2") == 0.13
    assert path_value(traj, "2026Q2") == 0.139                   # до первого ключа — значение первого


def test_year_mean_and_overrides_and_shift():
    traj = {"2027Q1": 0.05, "2027Q2": 0.04, "2027Q3": 0.034, "2027Q4": 0.028, "2027": 0.038}
    assert year_mean(traj, 2027) == pytest.approx(0.038)
    assert override_value({"2026": 0.0}, 2026) == 0.0
    assert override_value({"2026": 0.0}, 2027) is None           # переопределение — не траектория
    shifted = shift_trajectory({"2026": 0.01, "LT": 0.02, "LT_from": 2030}, 0.001)
    assert shifted == {"2026": 0.011, "LT": 0.021, "LT_from": 2030}
    assert shift_trajectory(0.5, -0.1) == pytest.approx(0.4)


def test_dotted_paths():
    data = {"a": {"b": {"2027": 1.0}}, "l": [{"x": 1}]}
    assert get_path(data, "a.b.2027") == 1.0
    set_path(data, "a.b.2027", 2.0)
    assert data["a"]["b"]["2027"] == 2.0
    assert get_path(data, "l.0.x") == 1
    with pytest.raises(BookError):
        get_path(data, "a.c")


def test_bad_trajectory_key_refuses():
    with pytest.raises(BookError):
        path_value({"2027-06": 0.1}, "2027Q1")


def test_timeline_of_the_book():
    book = fixture_book()
    tl = make_timeline(book)
    fy, fq = parse_period(book.get("meta.first_period"))
    ly, lq = parse_period(book.get("meta.last_period"))
    assert tl.Q == (ly * 4 + lq) - (fy * 4 + fq) + 1
    assert tl.index(book.get("meta.anchor_period")) == 0
    assert tl.period(1) == book.get("meta.first_period")
    assert all(90 <= d <= 92 for d in tl.days)
    assert sum(tl.days[:4]) in (365, 366)


def test_clock_elapsed_tau_and_roll():
    book = fixture_book()
    tl = make_timeline(book)
    end0 = tl.end(0)
    c = make_clock(book, tl, end0)                     # дата фактов: q0 = 1, e = 0
    assert (c.q0, c.elapsed) == (1, 0.0)
    assert c.tau[1] == pytest.approx(0.25) and c.tau[tl.Q] == pytest.approx(0.25 * tl.Q)
    v = tl.end(1)                                      # конец квартала — конец дня: q = 1 закрыт
    c = make_clock(book, tl, v)
    assert (c.q0, c.elapsed) == (2, 0.0)
    assert c.tau[2] == pytest.approx(0.25) and c.tau[1] == 0.0
    before = make_clock(book, tl, v - timedelta(days=1))  # внутри квартала день v не прошёл
    assert before.q0 == 1 and before.elapsed == pytest.approx((tl.d(1) - 2) / tl.d(1))
    assert before.tau[1] == pytest.approx(0.25 * 2 / tl.d(1))   # E − 1 → E: два дня начисления
    after = make_clock(book, tl, v + timedelta(days=1))
    assert (after.q0, after.elapsed) == (2, 0.0)                # E → S_(q+1): ни одного
    assert c.roll >= 0
    with pytest.raises(BookError):
        make_clock(book, tl, date(end0.year - 1, 1, 1))
    with pytest.raises(BookError):                    # конец последнего квартала — вне сетки
        make_clock(book, tl, tl.end(tl.Q))
