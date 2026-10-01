"""Скорость сетки: полоса — 2 000 прогонов 36 клеток, поэтому прогон сетки обязан быть быстрым."""

from __future__ import annotations

import time

from model.grid import run_grid
from tests.support_core import fixture_book, fixture_facts

# Потолок одного прогона сетки (36 клеток с пробными проходами дивиденда и контекстом),
# секунды: на ноутбуке разработчика ≈0,1 с; запас на медленный раннер CI.
GRID_SECONDS = 2.0


def test_grid_is_fast_enough_for_the_band():
    book, facts = fixture_book(), fixture_facts()
    run_grid(book, facts)                                   # прогрев импорта и кэшей
    t = time.perf_counter()
    n = 3
    for i in range(n):
        run_grid(book.with_overrides({"credit.kappa": 0.05 + 0.01 * i}), facts)
    per_run = (time.perf_counter() - t) / n
    assert per_run < GRID_SECONDS, f"прогон сетки {per_run:.2f} с"
