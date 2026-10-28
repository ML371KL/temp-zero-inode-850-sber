# -*- coding: utf-8 -*-
"""Книга против ядра: суждения книги 1.3, которые проверяются только прогоном сетки (поток book; MODEL §4.5, §12).

* Тест знака объёмных эффектов — условие правила центра `nii.phi_split`: при долях книги рост кредита нигде не
  разрушает стоимость (метка `tact`, ≈ 1 с — четыре прогона сетки).
* Цепочка отчётов «без новостей» — ограничение книги, названное числами: пол вероятности режима останавливает
  вымывание хвостов (вне такта: шестнадцать прогонов сетки, ≈ 8 с).
Книга и факты — те, что читает ядро (`data/assumptions/assumptions.yaml`, `data/facts/`); дата — дата книги.
"""
from __future__ import annotations

import pytest

try:
    from tests import support_book as S
except ImportError:                      # прогон без pytest.ini (pythonpath = .) и без tests/__init__.py
    import support_book as S


@pytest.fixture(scope="module")
def book() -> dict:
    return S.built_book()


def test_the_no_news_chain_stops_at_the_floor(book):
    """Ограничение книги, названное числами (решение ведущего В15; раздел 15 п. 16): цепочка восьми отчётов «факт =
    ожидание» опускает вероятности мягкой посадки и кризиса до пола 0,33 × базовой (4,95 %) — в книге 1.4 (окно
    начинается фактом 3К26, CoR которого ниже ожиданий тяжёлых режимов) обе к шестому отчёту, — и дальше они не
    падают; без пола хвосты уходят ниже пола (в книге 1.3 — к четвёртому и пятому отчёту, без пола — ниже 2,5 %)."""
    from model.book import load_book, load_facts
    from model.grid import run_grid
    from model.live import live_from_book
    from model.nextreport import model_expectation, open_period, with_observation
    from model.uncertainty import trial_book

    facts = load_facts()
    core_book = load_book(facts=facts)
    live = live_from_book(core_book, facts)
    share, prior = book["joint"]["regime_update"]["floor_share"], book["joint"]["regime_prob"]

    def chain(floor: float) -> list[dict]:
        b, out = trial_book(core_book, {"joint.regime_update.floor_share": floor}), []
        for _ in range(8):
            period = open_period(b, facts)
            e = model_expectation(b, facts, period, live=live, run=run_grid(b, facts, live))
            b = with_observation(b, period, cor=e["cor_q_mgmt"], nim=e["nim_q_mgmt"])
            out.append(dict(run_grid(b, facts, live).ctx.posterior))
        return out

    with_floor, without = chain(share), chain(0.0)
    for post in with_floor:
        assert abs(sum(post.values()) - 1) < 1e-9
        assert all(post[r] >= share * prior[r] - 1e-12 for r in prior), post
    soft = [round(p["soft"], 4) for p in with_floor]
    crisis = [round(p["crisis"], 4) for p in with_floor]
    assert soft[:5] == [0.1591, 0.1364, 0.1086, 0.0751, 0.0538] and soft[5:] == [0.0495] * 3   # на полу с шестого
    assert crisis[:5] == [0.1259, 0.101, 0.0719, 0.0615, 0.0607] and crisis[5:] == [0.0495] * 3  # тоже с шестого
    for r in ("soft", "crisis"):                                                     # без пола хвосты — ниже пола
        assert without[-1][r] < share * prior[r], (r, without[-1][r])


@pytest.mark.tact
def test_volume_effects_have_the_right_sign(book):
    """Тест знака объёмных эффектов (MODEL §4.5) — на ядре: снятие остановки кредита в кризисе и поправок роста
    режимов не снижает точку, и цена мира «рыночные ставки как есть» меняется при этом не хуже цены мира отсчёта
    (в нём сжатия φ нет: что кредит под надбавками капитала может стоить дороже, чем приносит, — цена капитала, а не
    раскладка сжатия). При всём сжатии на кредитных ставках (книга 1.2) тест не проходит: рост кредита в мире
    высоких ставок разрушал стоимость (запись аудита № 74)."""
    from model.book import load_book, load_facts
    from model.grid import run_grid, world_layer
    from model.uncertainty import trial_book

    facts = load_facts()
    core_book = load_book(facts=facts)
    free = {"regimes.crisis.loan_growth_override": {}}
    free.update({f"regimes.{r}.loan_growth_adj": {"LT": 0.0} for r in book["regimes"]["ids"]})
    market, ref = book["joint"]["macro_neutral_world"], book["nii"]["transmission"]["reference_world"]

    def effect(b):
        base, alt = run_grid(b, facts), run_grid(trial_book(b, free), facts)
        return alt.point - base.point, {w: world_layer(alt, w)["price"] - world_layer(base, w)["price"]
                                        for w in (market, ref)}

    point, worlds = effect(core_book)
    assert point >= -1e-9, f"снятие остановки и поправок роста кредита снижает точку на {-point:.2f} ₽"
    assert worlds[market] >= worlds[ref] - 1e-9, worlds
    point_old, worlds_old = effect(trial_book(core_book, {"nii.phi_split": 1.0}))   # раскладка книги 1.2
    assert point_old < 0 and worlds_old[market] < worlds_old[ref]
