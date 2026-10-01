"""Живые входы (М§15): коридоры годности цены, последняя принятая, дата оценки, реестр, флаги."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from model.live import LiveError, apply_live, check_price, check_register, read_last_accepted, write_last_accepted
from tests.support_core2 import TODAY, book_and_facts, book_prices, outputs, point

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def _tickers():
    book, _ = book_and_facts()
    return [str(t) for t in book.get("meta.company.tickers")], str(book.get("meta.company.main_ticker"))


def test_fresh_price_is_accepted_and_sets_the_valuation_date():
    book, facts = book_and_facts()
    tickers, main = _tickers()
    px = book_prices(book)
    day = TODAY - timedelta(days=1)
    out = outputs({t: [(day - timedelta(days=1), px[t] * 0.99), (day, px[t] * 1.01)] for t in tickers})
    live, rep = apply_live(book, facts, out, today=TODAY)
    assert live.prices[main] == pytest.approx(px[main] * 1.01)
    assert live.valuation_date == max(day, date.fromisoformat(str(book.get("meta.date"))))
    assert rep.applied and all(rep.prices[t]["status"] == "live" for t in tickers)
    assert not rep.flags["price_fallback"]["raised"]


@pytest.mark.parametrize("case, factor, age, accepted", [
    ("в пределах 30 %", 1.25, 1, True),
    ("скачок больше 30 % к свежему эталону", 1.4, 1, False),
    ("устарела на 8 дней", 1.0, 8, False),
    ("чужие единицы (×100)", 100.0, 1, False),
])
def test_price_corridors(case, factor, age, accepted):
    ref_day = TODAY - timedelta(days=3)
    got, why, _ = check_price((point("T", 300.0 * factor, TODAY - timedelta(days=age)),),
                              reference=300.0, reference_date=ref_day - timedelta(days=age), today=TODAY)
    assert (got is not None) == accepted, (case, why)


def test_jump_after_idle_needs_a_confirming_day():
    ref_day = TODAY - timedelta(days=20)
    lone = (point("T", 400.0, TODAY),)
    got, why, _ = check_price(lone, reference=300.0, reference_date=ref_day, today=TODAY)
    assert got is None and "подтвержден" in why
    confirmed = (point("T", 396.0, TODAY - timedelta(days=1)), point("T", 400.0, TODAY))
    got, why, note = check_price(confirmed, reference=300.0, reference_date=ref_day, today=TODAY)
    assert got is not None and "снята" in note


def test_units_are_never_accepted_even_after_idle():
    got, why, _ = check_price((point("T", 29.0, TODAY - timedelta(days=1)), point("T", 30.0, TODAY)),
                              reference=300.0, reference_date=TODAY - timedelta(days=40), today=TODAY)
    assert got is None and "единицы" in why


def test_price_never_rolls_back_before_the_last_accepted():
    got, why, _ = check_price((point("T", 301.0, TODAY - timedelta(days=2)),), reference=300.0,
                              reference_date=TODAY - timedelta(days=1), today=TODAY)
    assert got is None and "назад" in why


def test_exdate_lowers_the_reference_by_dps():
    reg = [{"year": 2025, "dps": 40.0, "status": "declared", "ex_date": (TODAY - timedelta(days=1)).isoformat(),
            "record_date": (TODAY - timedelta(days=1)).isoformat()}]
    # 300 → 205: −32 % к эталону, но −21 % к эталону минус DPS (260)
    got, why, _ = check_price((point("T", 205.0, TODAY),), reference=300.0, reference_date=TODAY - timedelta(days=2),
                              today=TODAY, register=reg)
    assert got is not None, why


def test_rejected_price_falls_back_to_the_last_accepted():
    book, facts = book_and_facts()
    tickers, main = _tickers()
    px = book_prices(book)
    last_day = TODAY - timedelta(days=2)
    previous = {"prices": {t: {"value": px[t], "date": last_day.isoformat(), "time": "18:40", "source": "tinvest"}
                           for t in tickers}}
    out = outputs({t: [(TODAY, px[t] * 2.0)] for t in tickers})       # скачок ×2 к свежему эталону
    live, rep = apply_live(book, facts, out, today=TODAY, previous=previous)
    assert live.prices[main] == px[main] and rep.prices[main]["status"] == "fallback"
    assert rep.flags["price_fallback"]["raised"] and rep.degraded
    assert live.valuation_date == max(last_day, date.fromisoformat(str(book.get("meta.date"))))


def test_main_ticker_without_any_fallback_stops_the_release():
    book, facts = book_and_facts()
    tickers, main = _tickers()
    out = outputs({t: [] for t in tickers})
    with pytest.raises(LiveError):
        apply_live(book, facts, out, today=TODAY, previous={})


def test_second_category_falls_back_to_the_book_price():
    book, facts = book_and_facts()
    tickers, main = _tickers()
    if len(tickers) < 2:
        pytest.skip("одна категория акций")
    px = book_prices(book)
    other = [t for t in tickers if t != main][0]
    out = outputs({main: [(TODAY, px[main])], other: []})
    live, rep = apply_live(book, facts, out, today=TODAY, previous={})
    assert rep.prices[other]["status"] == "book" and live.prices[other] == px[other]
    assert rep.flags["price_fallback"]["raised"]


def test_register_rules():
    rec = {"year": 2026, "dps": 40.0, "status": "declared", "record_date": "2027-07-19", "ex_date": "2027-07-19",
           "last_buy_date": "2027-07-16", "pay_date": "2027-08-02", "decided_date": "2027-06-30",
           "sources": ["tinvest", "iss"]}
    ok, bad = check_register([rec], price=300.0)
    assert ok and not bad
    for change, why in (({"sources": ["tinvest"]}, "одним источником"), ({"dps": 200.0}, "вне"),
                        ({"ex_date": "2027-07-18"}, "экс-дата"), ({"decided_date": "2027-07-15"}, "10–20")):
        ok, bad = check_register([{**rec, **change}], price=300.0)
        assert not ok and why in bad[0]["reason"], change
    ok, _ = check_register([{"year": 2026, "dps": 40.0, "status": "recommended", "sources": ["news"]}], price=300.0)
    assert ok and ok[0]["status"] == "recommended"


def test_declared_record_enters_the_run_and_recommended_raises_a_flag():
    book, facts = book_and_facts()
    tickers, main = _tickers()
    px = book_prices(book)
    ex = TODAY + timedelta(days=30)
    reg = [{"year": 2026, "dps": 40.0, "status": "declared", "record_date": ex.isoformat(), "ex_date": ex.isoformat(),
            "last_buy_date": (ex - timedelta(days=1)).isoformat(), "pay_date": (ex + timedelta(days=14)).isoformat(),
            "sources": ["tinvest", "iss"]},
           {"year": 2027, "dps": 50.0, "status": "recommended", "sources": ["news"]}]
    out = outputs({t: [(TODAY, px[t])] for t in tickers}, register=reg)
    live, rep = apply_live(book, facts, out, today=TODAY)
    assert [(r.year, r.status) for r in live.register] == [(2026, "declared")]
    assert rep.flags["dividend_recommended"]["raised"]


def test_book_update_flag_from_the_curve_shift():
    book, facts = book_and_facts()
    tickers, _ = _tickers()
    px = book_prices(book)
    w = str(book.get("joint.macro_neutral_world"))
    nodes = {k: float(book.get(f"worlds.{w}.zero_curve.{k}")) for k in ("1", "3", "5", "10")}
    shift = float(book.get("checks.book_update.shift_bp")) / 10_000
    moved = {**nodes, "10": nodes["10"] + 2 * shift}
    out = outputs({t: [(TODAY, px[t])] for t in tickers}, curve={"as_of": TODAY.isoformat(), "nodes": moved})
    _, rep = apply_live(book, facts, out, today=TODAY)
    assert rep.curve["shift_bp"]["10"] == round(2 * shift * 10_000)
    assert rep.flags["book_update"]["raised"]
    bad = outputs({t: [(TODAY, px[t])] for t in tickers}, curve={"as_of": TODAY.isoformat(),
                                                                  "nodes": {k: v * 100 for k, v in nodes.items()}})
    _, rep = apply_live(book, facts, bad, today=TODAY)
    assert rep.curve is None and any("кривая" in d for d in rep.degraded)


def test_last_accepted_is_written_only_for_live_prices(tmp_path):
    book, facts = book_and_facts()
    tickers, main = _tickers()
    px = book_prices(book)
    out = outputs({t: [(TODAY, px[t] * 1.02)] for t in tickers})
    _, rep = apply_live(book, facts, out, today=TODAY)
    write_last_accepted(rep, tmp_path)
    got = read_last_accepted(tmp_path)
    assert got["prices"][main]["value"] == pytest.approx(px[main] * 1.02)
    assert set(got) == {"prices", "register", "valuation_date", "written_at"}      # только цены, реестр и даты
    assert read_last_accepted(tmp_path / "нет") == {}


def test_book_only_uses_the_books_price_date_and_register():
    book, facts = book_and_facts()
    live, rep = apply_live(book, facts, None, today=TODAY)
    assert not rep.applied
    assert live.valuation_date == date.fromisoformat(str(book.get("meta.valuation_date")))
    assert all(r["status"] == "book" for r in rep.prices.values())
