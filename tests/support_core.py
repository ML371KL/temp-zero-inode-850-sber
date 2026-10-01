"""Помощники тестов ядра (core1): книга и факты фикстуры, черновик, настоящая книга.

Фикстура `tests/fixtures/core/` — машинная книга в схеме книги 1.3 (черновик 1.0 + ключи волн W1–W3
+ миры записи 850oa 1.6 + банковская надстройка) и факты по М прил. B, собранные из листов этапа 1 (числа с
происхождением в узлах). Настоящие `data/assumptions/assumptions.yaml` и
`data/facts/*.json` используются, когда они появятся (тесты с ними пропускаются
до того сами).
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

import pytest
import yaml

from model.book import Book, Facts, book_from_dict, load_book, load_facts

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "core"
FIXTURE_BOOK = FIXTURE / "book.json"
FIXTURE_FACTS = FIXTURE / "facts"
REAL_BOOK = ROOT / "data" / "assumptions" / "assumptions.yaml"
REAL_FACTS = ROOT / "data" / "facts"
DRAFT = ROOT / "data" / "assumptions" / "assumptions.draft.yaml"


@functools.lru_cache(maxsize=None)
def fixture_facts() -> Facts:
    return load_facts(FIXTURE_FACTS)


def fixture_dict() -> dict:
    return json.loads(FIXTURE_BOOK.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def fixture_book() -> Book:
    return load_book(FIXTURE_BOOK, facts=fixture_facts())


def book_with(changes: dict, *, drop: tuple[str, ...] = ()) -> dict:
    """Словарь книги фикстуры с заменами по точечным путям (для тестов схемы)."""
    data = copy.deepcopy(fixture_dict())
    for dotted, value in changes.items():
        parts = dotted.split(".")
        cur = data
        for p in parts[:-1]:
            cur = cur[int(p)] if isinstance(cur, list) else cur[p]
        cur[parts[-1]] = value
    for dotted in drop:
        parts = dotted.split(".")
        cur = data
        for p in parts[:-1]:
            cur = cur[p]
        del cur[parts[-1]]
    return data


# Ключи прил. A, добавленные волнами W1–W3 (LEAD-DECISIONS-2, -3, -4): черновик 1.0 их не знает — берутся из фикстуры.
W1_KEYS = ("meta.company.share_classes", "regimes.near_nim_shift", "nii.sigma0_from", "other.misc_quarter_shares",
           "dividends.excess.ramp_years", "valuation.uncertainty.off_band_axes", "checks.nim_path_joint",
           "checks.transmission_pairs", "checks.ni_jump",
           "joint.regime_update.window_obs", "regimes.crisis.shock_year_offset", "credit.fv_loans_ref",
           "nii.sigma0_split", "checks.lt_spread_floor",
           "nii.phi_split", "joint.regime_update.floor_share", "valuation.sensitivities.rank_window",
           "valuation.next_report.value_tol")


def draft_book(facts: Facts | None = None) -> Book:
    """Текущий черновик книги + миры и надстройка фикстуры (машинная книга на черновике);
    ключей W1, которых в черновике нет, — значения фикстуры."""
    if not DRAFT.exists():
        pytest.skip("нет data/assumptions/assumptions.draft.yaml")
    draft = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    fx = fixture_dict()
    for w in fx["worlds"]["ids"]:
        draft["worlds"][w] = fx["worlds"][w]
    draft["worlds_bank"] = fx["worlds_bank"]
    if draft["worlds"]["overlay"].get("sha256") is None:
        draft["worlds"]["overlay"]["sha256"] = fx["worlds"]["overlay"]["sha256"]
    for dotted in W1_KEYS:
        head, _, last = dotted.rpartition(".")
        src, dst = fx, draft
        for part in head.split("."):
            src, dst = src[part], dst.setdefault(part, {})
        dst.setdefault(last, [] if last == "off_band_axes" else copy.deepcopy(src[last]))   # оси черновика — свои
    draft["regimes"]["crisis"].pop("shock_year", None)                  # ключ-год заменён сдвигом (М§3.2)
    for name, spec in draft["nii"]["books"].items():                    # lt_shift и phi — у книг обеих сторон (М§4.5)
        spec.setdefault("lt_shift", fx["nii"]["books"][name]["lt_shift"])
        if "phi" in fx["nii"]["books"][name]:
            spec.setdefault("phi", fx["nii"]["books"][name]["phi"])
    return book_from_dict(draft, facts=facts or fixture_facts())


# Файлы фактов, которые читает ядро (М прил. B): пока их нет всех — факты «ещё собираются».
CORE_FACTS = ("anchor", "balance", "nii_books", "pnl_quarterly", "capital", "shares", "dividends",
              "bridge_mgmt_ifrs", "guidance", "calendar")


def real_facts_ready() -> bool:
    return REAL_FACTS.is_dir() and all((REAL_FACTS / f"{n}.json").exists() for n in CORE_FACTS)


def real_facts_or_skip() -> Facts:
    if not real_facts_ready():
        pytest.skip("нет полного набора data/facts/*.json (поток facts) — тест на фикстуре уже прошёл")
    return load_facts(REAL_FACTS)


def real_book_or_skip() -> tuple[Book, Facts]:
    facts = real_facts_or_skip()
    if not REAL_BOOK.exists():
        pytest.skip("нет data/assumptions/assumptions.yaml (поток book)")
    return load_book(REAL_BOOK, facts=facts), facts


@functools.lru_cache(maxsize=None)
def fixture_run():
    from model.grid import run_grid
    return run_grid(fixture_book(), fixture_facts())
