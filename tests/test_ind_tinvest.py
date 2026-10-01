"""T-Invest: токен только из окружения, разбор ответов, в выпуск — только агрегаты целей брокеров.

Фикстуры — синтетика в формате ответа T-Invest (№ 7): условия T запрещают публиковать ответы API.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from indicators import config, tinvest
from indicators.sources import MISSING, OK, Context
from indicators.store import Store
from tests.support_ind import FAKE_TOKEN, FIX, FakeGetter, use_fixtures

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)


def _json(name):
    return json.loads((FIX / "tinvest" / name).read_text(encoding="utf-8"))


def _ctx(store, getter=None):
    return Context(store=store, today=date(2026, 10, 1), company=config.company(), cfg=config.sources(),
                   getter=getter)


def test_quotation_and_last_prices():
    assert tinvest.quotation({"units": "272", "nano": 700000000}) == pytest.approx(272.7)
    assert tinvest.quotation(None) is None
    p = tinvest.parse_last_prices(_json("last_prices.json"))
    sber = p[_json("shareby_SBER.json")["instrument"]["uid"]]
    assert sber == {"price": pytest.approx(272.7), "date": "2026-09-30", "time": "13:53", "ticker": "SBER"}


def test_dividends_register_records():
    rows = tinvest.parse_dividends(_json("dividends_SBER.json"))
    last = rows[-1]
    assert last["dps"] == pytest.approx(37.64) and last["record_date"] == "2026-07-20"
    assert last["ex_date"] == last["record_date"] and last["year"] == 2025 and last["status"] == "declared"
    assert last["last_buy_date"] == "2026-07-17" and last["pay_date"] == "2026-08-03"   # выплата в фикстуре сочинена
    assert last["decided_date"] is None                   # declaredDate ненадёжно (М§15)
    assert last["sources"] == ["T-Invest"]                # слово источника реестра (П§2 dividends.register)


def test_reports_deduplicated_and_brokers_aggregated():
    reports = tinvest.parse_reports(_json("reports_SBER.json"))
    q3 = [r["date"] for r in reports if r["year"] == 2026 and r["num"] == 3]
    assert q3 == ["2026-10-09", "2026-10-28"]
    agg = tinvest.brokers_aggregate(_json("forecast_SBER.json"), as_of="2026-09-30")
    assert agg["n"] == 13 and agg["min"] == 320 and agg["max"] == 430 and agg["median"] == 375
    assert agg["recommendations"] == {"buy": 11, "hold": 2, "sell": 0}
    assert "Брокер" not in json.dumps(agg, ensure_ascii=False)           # имена и цели по брокерам не выходят


def test_no_token_means_missing_source(monkeypatch, tmp_path):
    use_fixtures(monkeypatch, tmp_path, token=False)
    g = FakeGetter()
    r = tinvest.collect(_ctx(Store(tmp_path / "state"), g))
    assert r.status == MISSING and not g.calls


def test_collect_writes_series_and_keeps_the_token_out_of_files(monkeypatch, tmp_path):
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    g = FakeGetter()
    r = tinvest.collect(_ctx(store, g))
    assert r.status == OK, r.reasons
    assert store.load("tinvest.price.SBER").latest().value == pytest.approx(272.7)
    assert store.load("tinvest.price.SBER").latest().note == "13:53"
    assert store.read_state("tinvest/dividends.json")["by_ticker"]["SBER"][-1]["dps"] == pytest.approx(37.64)
    assert store.read_state("tinvest/brokers_SBER.json")["n"] >= 10
    assert all(c["headers"]["Authorization"] == "Bearer " + FAKE_TOKEN for c in g.calls)
    for f in state.rglob("*"):
        if f.is_file():
            assert FAKE_TOKEN.encode() not in f.read_bytes(), f
    assert store.raw_days("tinvest")
