"""Регрессия книги (М§18): свежий расчёт воспроизводит закоммиченный `data/assumptions/results.json`.

Не проверяет правильность (ошибка ядра вошла бы в таблицы) — ловит незамеченную смену чисел:
правка кода, книги или фактов без нового `python -B -m model.book_results`. Допуск М§18 —
относительный 1e-12, пол 1e-10. Коммит ядра и гейт, зависящий от «сегодня»
(`manual_input_overdue`), в сравнение не входят.
"""
from __future__ import annotations

import json
import math

import pytest

from model import book_results as BR
from model.book import ROOT

RESULTS = ROOT / "data" / "assumptions" / "results.json"
REL, FLOOR = 1e-12, 1e-10
GRID_KEYS = ("book_version", "facts_date", "derived", "layers", "cells", "central_cell", "sign_test")
GRID_HEADLINE = ("point", "printed_point", "low", "high", "rates_view", "lambda", "market")
BAND_HEADLINE = ("median", "printed_median", "band80", "printed_band80", "band50", "printed_band50",
                 "p10", "p25", "p75", "p90", "mean", "p_below_market", "p_below_by_ticker", "upside",
                 "draws", "seed", "contributions")
TODAY_DEPENDENT = {"manual_input_overdue"}
# Текст инварианта DDM = RI называет число прогонов полосы и максимум разности по ним (C4): быстрая полоса
# сетки его меняет — сверяются имя, вид, срабатывание и масса, без текста.
BAND_DEPENDENT_TEXT = {"ddm_equals_ri"}


def _diff(saved, fresh, path: str, bad: list[str]) -> None:
    if isinstance(saved, dict) and isinstance(fresh, dict):
        for k in sorted(set(saved) | set(fresh)):
            if k not in saved or k not in fresh:
                bad.append(f"{path}.{k}: ключ есть только в {'свежем' if k in fresh else 'сохранённом'}")
            else:
                _diff(saved[k], fresh[k], f"{path}.{k}", bad)
    elif isinstance(saved, list) and isinstance(fresh, list):
        if len(saved) != len(fresh):
            bad.append(f"{path}: длина {len(saved)} против {len(fresh)}")
        for i, (a, b) in enumerate(zip(saved, fresh)):
            _diff(a, b, f"{path}[{i}]", bad)
    elif isinstance(saved, (int, float)) and isinstance(fresh, (int, float)) \
            and not isinstance(saved, bool) and not isinstance(fresh, bool):
        if not math.isclose(saved, fresh, rel_tol=REL, abs_tol=FLOOR):
            bad.append(f"{path}: {saved!r} против {fresh!r}")
    elif saved != fresh:
        bad.append(f"{path}: {saved!r} против {fresh!r}")


def _saved() -> dict:
    if not RESULTS.exists():
        pytest.skip("нет data/assumptions/results.json (python -B -m model.book_results)")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def _plain(res: dict) -> dict:
    return json.loads(json.dumps(res, default=BR._default))


def _checks(res: dict) -> list[dict]:
    return [{k: v for k, v in c.items() if not (k == "message" and c["name"] in BAND_DEPENDENT_TEXT)}
            for c in res["checks"] if c["name"] not in TODAY_DEPENDENT]


def test_grid_part_of_the_book_results_reproduces():
    """Сетка без полосы (≈1 с): выводимое, слои, 36 клеток, центральная клетка, тест знака, проверки, точка."""
    saved = _saved()
    fresh = _plain(BR.book_results(slow=False))
    bad: list[str] = []
    for k in GRID_KEYS:
        if k not in saved:
            bad.append(f"{k}: ключ есть только в свежем")
            continue
        _diff(saved[k], fresh[k], k, bad)
    for k in GRID_HEADLINE:
        _diff(saved["headline"][k], fresh["headline"][k], f"headline.{k}", bad)
    _diff(_checks(saved), _checks(fresh), "checks", bad)
    assert not bad, "results.json устарел — пересобрать python -B -m model.book_results:\n" + "\n".join(bad[:20])


@pytest.mark.ci_only
def test_band_part_of_the_book_results_reproduces(parallel_band):
    """Полоса на полном числе прогонов книги: заголовок и вклады осей (поиски — не пересчитываются)."""
    from model.book import load_book, load_facts
    from model.grid import live_from_book, run_grid
    from model import uncertainty as U

    saved = _saved()
    facts = load_facts()
    book = load_book(facts=facts)
    live = live_from_book(book, facts)
    run = run_grid(book, facts, live)
    with parallel_band():
        band = U.band(book, facts, live, draws=int(book.get("valuation.uncertainty.draws")))
    h = band.headline(run.lam, live.prices, str(book.get("meta.company.main_ticker")))
    fresh = {k: h[k] for k in BAND_HEADLINE if k in h}
    fresh.update(draws=band.draws, seed=band.seed,
                 contributions=[{k: c[k] for k in ("axis", "name", "share", "rank_corr")}
                                for c in band.contributions(run.lam)])
    bad: list[str] = []
    for k in BAND_HEADLINE:
        _diff(saved["headline"][k], _plain(fresh)[k], f"headline.{k}", bad)
    assert not bad, "results.json устарел — пересобрать python -B -m model.book_results:\n" + "\n".join(bad[:20])
