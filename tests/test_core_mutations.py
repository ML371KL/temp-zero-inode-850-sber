"""Мутации ядра (М§18): каждую ошибку роняет аналитическая или инвариантная проверка.

Подлинное ядро проходит все проверки набора; каждый мутант (`tests/mutations.py`)
роняет хотя бы одну. Мутанты гоняются подпроцессами параллельно.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.mutations import MUTATIONS, build_mutant, run_checks_in


def test_original_core_passes_all_mutation_checks():
    result = run_checks_in(None)
    bad = {k: v for k, v in result.items() if v != "ok"}
    assert not bad, bad


def test_every_mutation_is_caught(tmp_path):
    def one(m):
        target = build_mutant(m, tmp_path / m.id)
        return m, run_checks_in(target)

    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, MUTATIONS))
    missed = []
    for m, res in results:
        caught = [k for k, v in res.items() if v != "ok" and not k.startswith("_")]
        if not caught:                    # падение подготовки мутанта — не «поймана проверкой»
            missed.append(f"{m.id} «{m.title}»: {res}")
    assert not missed, "не пойманы: " + "; ".join(missed)


@pytest.mark.parametrize("m", MUTATIONS, ids=lambda m: m.id)
def test_mutation_targets_exist(m):
    """Строка-цель каждой мутации есть в коде: иначе мутацию перенацеливают, а не удаляют."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert m.find in (root / m.path).read_text(encoding="utf-8"), m.id
