"""Волна W2, выпуск на фикстуре ядра: сторож заголовка на цепочке «конец квартала ГОСА → экс-дата» (М§14.4),
ближайший дивиденд без рекомендации с прошедшей отсечкой (М§15), строки гайденса сектора (П§2)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from model import payload as P
from model.checks import check_gates
from model.grid import DividendRecord, LiveInputs, run_grid
from model.timeline import make_timeline
from tests.support_core import fixture_book, fixture_facts
from tests.support_core2 import CONTROL_FIXTURE, book_prices, explained, outputs

pytestmark = pytest.mark.tact


def _register_row(year, dps, ex, status="declared"):
    return {"year": year, "dps": dps, "status": status, "record_date": ex.isoformat(), "ex_date": ex.isoformat(),
            "last_buy_date": (ex - timedelta(days=1)).isoformat(), "pay_date": (ex + timedelta(days=15)).isoformat(),
            "decided_date": (ex - timedelta(days=15)).isoformat(), "sources": ["T-Invest", "Интерфакс"]}


def _payload(day, register=(), previous=None):
    """Быстрый выпуск на фикстуре на дату `day` с реестром сборщиков и объяснёнными гейтами."""
    book, facts = fixture_book(), fixture_facts()
    px = book_prices(book)
    recs = tuple(DividendRecord(year=r["year"], dps=r["dps"], status=r["status"], record_date=P.to_date(r["record_date"]),
                                last_buy_date=P.to_date(r["last_buy_date"]), ex_date=P.to_date(r["ex_date"]),
                                pay_date=P.to_date(r["pay_date"]), sources=tuple(r["sources"]))
                 for r in register if r["status"] != "recommended")
    probe = run_grid(book, facts, LiveInputs(valuation_date=day, prices=px, price_dates={t: day for t in px},
                                             register=recs))
    expl = explained(check_gates(probe, today=day), today=day)
    out = outputs({t: [(day, px[t])] for t in px}, register=register)
    rel = P.make_release(live=True, fast=True, today=day, book=book, facts=facts, outputs=out, explanations=expl,
                         notes=[], draws=4, control_model=CONTROL_FIXTURE)
    rel.previous = previous
    return rel, P.build_payload(rel)


def test_jump_guard_is_silent_across_the_agm_quarter_end_and_the_ex_date():
    """№ 12 (М§14.4): на E_qA дивиденд переходит из V0 в мост, на экс-дате уходит из моста — сторож смотрит
    V0 + B с поправкой на экс-даты и молчит на всей цепочке E_qA − 1 → E_qA → экс-дата − 1 → экс-дата."""
    book, facts = fixture_book(), fixture_facts()
    tl = make_timeline(book)
    year = tl.anchor_year
    end = tl.end(tl.index(f"{year + 1}Q{book.get('dividends.calendar.agm_quarter')}"))
    ex = end + timedelta(days=18)
    base = run_grid(book, facts)
    policy = sum(c.dps_policy[year] for c in base.cells) / len(base.cells)
    dps = round(1.02 * policy, 2)                                   # объявлено на 2 % выше политики
    reg = [_register_row(year, dps, ex)]
    days = [end - timedelta(days=1), end, ex - timedelta(days=1), ex]
    prev, chain = None, []
    for day in days:
        rel, d = _payload(day, reg, prev)
        chain.append((rel, d))
        prev = d
    amount = dps * chain[0][0].run.shares_out / 1000
    limit = float(book.get("valuation.headline.jump_guard.v0_pct"))
    for i in range(1, len(chain)):
        jg = chain[i][1]["fair_value"]["jump_guard"]
        assert jg["reason"] == "within_limit", (days[i], jg)
        assert abs(jg["v0_change"]) < 0.01 and abs(jg["median_change"]) < 0.01
        inv = next(x for x in chain[i][1]["checks"]["invariants"] if x["name"] == "jump_guard")
        assert inv["ok"] and "в пределах порога" in inv["detail"]
    v0 = [d["layers"]["analytical"]["v0"] for _, d in chain]
    bridge = [d["fair_value"]["bridge"]["amount"] for _, d in chain]
    pend = [d["fair_value"]["bridge"]["pending_dividend"] for _, d in chain]
    assert bridge == pytest.approx([0.0, amount, amount, 0.0], abs=0.01)
    assert pend == pytest.approx([amount, 0.0, 0.0, 0.0], abs=0.01)
    drop = v0[1] / v0[0] - 1                                         # один V0 падает на вычтенный дивиденд
    assert drop == pytest.approx(-amount / v0[0], abs=2e-3) and abs(drop) > 0.6 * limit
    jg_end = chain[1][1]["fair_value"]["jump_guard"]
    assert jg_end["v0_agm_adjustment"] == 0.0 and jg_end["exdate_adjustment"] == 0.0
    jg_ex = chain[3][1]["fair_value"]["jump_guard"]
    assert jg_ex["exdate_adjustment"] == pytest.approx(dps, abs=0.01)
    assert jg_ex["v0_agm_adjustment"] == pytest.approx(0.0, abs=0.02)        # мост прошлого выпуска − дивиденд экс-даты
    med = [d["fair_value"]["headline"]["median"] for _, d in chain]
    assert med[3] - med[2] == pytest.approx(-dps, abs=0.5)                   # скачок на экс-дату = −DPS
    assert abs(chain[1][0].run.point - chain[0][0].run.point) < 1e-3 * chain[1][0].run.point   # E_qA: перекат


def test_next_expected_skips_a_recommendation_whose_record_date_has_passed():
    """№ 43 (М§15): рекомендация совета — ближайшая выплата только до отсечки; после неё без решения ГОСА
    ближайшей считается выплата модели следующего года."""
    book = fixture_book()
    tl = make_timeline(book)
    year = tl.anchor_year
    end = tl.end(tl.index(f"{year + 1}Q{book.get('dividends.calendar.agm_quarter')}"))
    ex = end + timedelta(days=18)
    rec = [_register_row(year, 44.0, ex, status="recommended")]
    _, before = _payload(end - timedelta(days=40), rec)
    ne = before["dividends"]["next_expected"]
    assert (ne["status"], ne["year"], ne["dps"]) == ("recommended", year, 44.0)
    assert any(f["name"] == "dividend_recommended" and f["raised"] for f in before["checks"]["flags"])
    _, after = _payload(ex + timedelta(days=5), rec)
    ne = after["dividends"]["next_expected"]
    assert ne["status"] == "model" and ne["year"] == year + 1 and ne["dps"] == ne["dps_policy"]
    assert "last_buy_date" not in ne


def test_sector_guidance_rows_on_the_fixture():
    """№ 17: прогноз сектора — базис `sector`; «в соответствии с сектором» сравнивается с диапазоном,
    «лучше сектора» — нет (статус «неприменимо», масса null); пересмотры — ключами строк."""
    _, d = _payload(P.to_date(fixture_book().get("meta.valuation_date")))
    items = {i["key"]: i for i in d["guidance"]["items"]}
    corp, retail = items["loan_growth_corporate"], items["loan_growth_retail"]
    assert (corp["scope"], corp["relation"], corp["basis"]) == ("sector", "in_line", "sector")
    assert corp["status"] in ("inside", "outside") and corp["mass_outside"] is not None
    assert (retail["scope"], retail["relation"], retail["basis"]) == ("sector", "above", "sector")
    assert retail["status"] == "n/a" and retail["mass_outside"] is None and retail["model_year"] is not None
    assert "сектор" in corp["scope_note"] and "scope_note" not in items["roe"]
    assert items["fee_growth"]["status"] == "inside"                 # «~0 %» ± 0,5 п.п., а не ± 5 п.п.
    keys = [r["key"] for r in d["guidance"]["revisions"]]
    assert keys == ["fee_growth", "loan_growth_corporate", "loan_growth_retail"]
    assert d["dividends"]["policy"]["valid_until"] == "2026-12-05" and d["dividends"]["policy"]["valid_until_note"]
    assert P.validate(d) == [], P.validate(d)[:5]
