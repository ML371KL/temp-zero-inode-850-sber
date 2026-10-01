"""Реестр дивидендов (М§15, B3): T-Invest + новость о решении ГОСА/совета + ручная запись; тревога
«нет записи реестра к концу квартала ГОСА»; команда `record-dividend`."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from indicators import collect, config, http, news, outputs, sources, tinvest
from indicators import register as reg
from indicators.store import Store
from tests.support_ind import FIX, FakeGetter, use_fixtures

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)

CFG = {"dividends": {"news_kinds": {"interfax": "Интерфакс", "tass": "ТАСС"}, "dps_tolerance": 0.005}}


def _tinvest_rows() -> list[dict]:
    return tinvest.parse_dividends(json.loads((FIX / "tinvest" / "dividends_SBER.json").read_text(encoding="utf-8")))


def _item(name: str, kind: str) -> tuple[str, dict]:
    doc = (FIX / "news" / name).read_text(encoding="utf-8")
    return f"https://example.org/{name}", {"kind": kind, "topic": "dividend", "title": news.meta(doc, "og:title"),
                                           "text": news.article_text(doc),
                                           "published": news.meta(doc, "article:published_time")}


def _store(tmp_path, *, tinvest_rows=True, items=(), manual=()) -> Store:
    store = Store(tmp_path / "state")
    if tinvest_rows:
        store.write_state(reg.TINVEST_FILE, {"by_ticker": {"SBER": _tinvest_rows()}})
    store.write_state("news/items.json", {"items": dict(items), "pdf": {}})
    for m in manual:
        reg.record_manual(store, **m)
    return store


def _year(rows, y):
    return next(r for r in rows if r["year"] == y)


def test_tinvest_and_the_agm_news_make_two_sources(tmp_path):
    store = _store(tmp_path, items=[_item("interfax_9000201.html", "interfax")])
    rows = reg.build(store, ticker="SBER", today=date(2026, 7, 10), cfg=CFG)
    r = _year(rows, 2025)
    assert r["sources"] == ["T-Invest", "Интерфакс"] and r["status"] == "declared" and reg.usable(r)
    assert r["dps"] == pytest.approx(37.64) and r["record_date"] == r["ex_date"] == "2026-07-20"
    assert r["decided_date"] == "2026-06-30" and r["last_buy_date"] == "2026-07-17"
    assert _year(reg.build(store, ticker="SBER", today=date(2026, 8, 2), cfg=CFG), 2025)["status"] == "declared"
    assert _year(reg.build(store, ticker="SBER", today=date(2026, 8, 3), cfg=CFG), 2025)["status"] == "paid"


def test_one_news_counts_once_and_the_manual_record_is_the_fallback(tmp_path):
    two_feeds = [_item("interfax_9000201.html", "interfax"), _item("interfax_9000201.html", "tass")]
    two_feeds[1] = ("https://example.org/tass", two_feeds[1][1])
    store = _store(tmp_path, tinvest_rows=False, items=two_feeds)
    r = _year(reg.build(store, ticker="SBER", today=date(2026, 7, 1), cfg=CFG), 2025)
    assert len(r["sources"]) == 1 and not reg.usable(r)             # две ленты об одном решении — один источник
    store = _store(tmp_path / "m", tinvest_rows=False, items=[two_feeds[0]], manual=[{
        "year": 2025, "dps": 37.64, "status": "declared", "record_date": "2026-07-20", "decided_date": "2026-06-30",
        "source": "https://example.org/disclosure/agm-decision"}])
    r = _year(reg.build(store, ticker="SBER", today=date(2026, 7, 1), cfg=CFG), 2025)
    assert r["sources"] == ["Интерфакс", "ручная запись"] and reg.usable(r)


def test_recommendation_and_disagreement(tmp_path):
    store = _store(tmp_path, tinvest_rows=False, items=[_item("interfax_9000202.html", "interfax")])
    r = _year(reg.build(store, ticker="SBER", today=date(2026, 5, 5), cfg=CFG), 2025)
    assert r["status"] == "recommended" and not reg.usable(r)
    assert r["decided_date"] is None and r["recommended_date"] == "2026-04-29"      # дата решения — только ГОСА
    wrong = _store(tmp_path / "w", items=[_item("interfax_9000201.html", "interfax")], manual=[{
        "year": 2025, "dps": 36.0, "status": "declared", "record_date": "2026-07-20", "source": "doc"}])
    r = _year(reg.build(wrong, ticker="SBER", today=date(2026, 7, 10), cfg=CFG), 2025)
    assert r["sources"] == ["T-Invest", "Интерфакс"] and any("ручная запись: 36.0" in n for n in r["notes"])
    other = _store(tmp_path / "o", items=[_item("interfax_9000201.html", "kommersant")])
    assert _year(reg.build(other, ticker="SBER", today=date(2026, 7, 10), cfg=CFG), 2025)["sources"] == ["T-Invest"]


def test_tinvest_with_a_recommendation_only_follows_the_cut_off_and_the_payment(tmp_path):
    """№ 43: «T-Invest + рекомендация совета» — `recommended` до отсечки, `declared` с неё, `paid` с выплаты."""
    store = _store(tmp_path, items=[_item("interfax_9000202.html", "interfax")])

    def at(day: date) -> dict:
        return _year(reg.build(store, ticker="SBER", today=day, cfg=CFG), 2025)

    before = at(date(2026, 5, 5))
    assert before["status"] == "recommended" and before["sources"] == ["T-Invest", "Интерфакс"]
    assert before["decided_date"] is None and not reg.usable(before)
    assert at(date(2026, 7, 19))["status"] == "recommended"
    after = at(date(2026, 7, 20))                                    # день отсечки: решение состоялось
    assert after["status"] == "declared" and after["sources"] == ["T-Invest"] and not reg.usable(after)
    assert after["decided_date"] is None and after["recommended_date"] == "2026-04-29"
    assert at(date(2026, 8, 2))["status"] == "declared"              # за день до выплаты
    paid = at(date(2026, 8, 3))                                      # выплата — что бы ни писали новости
    assert paid["status"] == "paid" and reg.usable(paid)
    assert reg.alarm([paid], today=date(2026, 8, 3), agm_quarter=2) is None
    # решение ГОСА в лентах — второй источник: рекомендация его не заслоняет
    both = _store(tmp_path / "b", items=[_item("interfax_9000202.html", "interfax"),
                                         _item("interfax_9000201.html", "interfax")])
    r = _year(reg.build(both, ticker="SBER", today=date(2026, 7, 10), cfg=CFG), 2025)
    assert r["status"] == "declared" and r["sources"] == ["T-Invest", "Интерфакс"] and reg.usable(r)
    assert r["decided_date"] == "2026-06-30"
    # ручная рекомендация вторым источником объявленной записи тоже не считается
    hand = _store(tmp_path / "h", manual=[{"year": 2025, "dps": 37.64, "status": "recommended", "source": "doc"}])
    assert _year(reg.build(hand, ticker="SBER", today=date(2026, 7, 21), cfg=CFG), 2025)["sources"] == ["T-Invest"]


def test_a_failed_or_empty_answer_for_one_ticker_keeps_its_records(monkeypatch, tmp_path):
    """№ 42: 503 или пустой ответ GetDividends по одному тикеру не стирает его записи; такт деградирует."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)

    def tact(day: int, target: Store = store, **overrides) -> sources.Result:
        moment = datetime(2026, 10, day, 6, tzinfo=timezone.utc)
        return tinvest.collect(sources.Context(store=target, today=moment.date(), company=config.company(),
                                               cfg=config.sources(), getter=FakeGetter(overrides=overrides.get("o")),
                                               now=moment))

    assert tact(1).status == "ok"
    good = store.read_state(reg.TINVEST_FILE)
    rows = reg.build(store, ticker="SBER", today=date(2026, 10, 1), cfg=config.sources())
    assert good["stale_since"] == {} and len(good["by_ticker"]["SBER"]) == 3
    main = json.loads((FIX / "tinvest" / "shareby_SBER.json").read_text(encoding="utf-8"))["instrument"]["uid"]
    for answer in (http.FetchError("https://invest-public-api.example/GetDividends", 503, "HTTP 503"),
                   b'{"dividends": []}'):
        res = tact(2, o={("GetDividends", main): answer})
        assert res.status == "degraded" and any("дивиденды SBER" in r and "оставлены" in r for r in res.reasons)
        # причину печатает витрина: что не снято — словами, без адреса API с именем метода (W3b, P7)
        assert not any("GetDividends" in r or "invest-public-api" in r for r in res.reasons), res.reasons
        held = store.read_state(reg.TINVEST_FILE)
        assert held["by_ticker"] == good["by_ticker"]                    # SBER — прежние, SBERP — свежие
        assert held["stale_since"] == {"SBER": "2026-10-01T06:00:00+00:00"}
        assert held["fetched_at"] == "2026-10-02T06:00:00+00:00"
        assert reg.build(store, ticker="SBER", today=date(2026, 10, 1), cfg=config.sources()) == rows
    assert tact(3).status == "ok"
    assert store.read_state(reg.TINVEST_FILE)["stale_since"] == {}       # удачный сбор снимает пометку
    # пустой ответ при пустом прошлом — не отказ: записей просто нет
    empty = Store(tmp_path / "empty")
    assert tact(1, empty, o={"GetDividends": b'{"dividends": []}'}).status == "ok"
    assert empty.read_state(reg.TINVEST_FILE)["by_ticker"] == {"SBER": [], "SBERP": []}


def test_alarm_after_the_agm_quarter_without_a_usable_record(tmp_path):
    only_tinvest = reg.build(_store(tmp_path), ticker="SBER", today=date(2026, 7, 1), cfg=CFG)
    text = reg.alarm(only_tinvest, today=date(2026, 7, 1), agm_quarter=2)
    assert text and "за 2025 год" in text and "2026-06-30" in text and "T-Invest" in text
    assert reg.alarm(only_tinvest, today=date(2026, 6, 30), agm_quarter=2) is None      # квартал ГОСА ещё идёт
    assert reg.alarm([], today=date(2026, 7, 1), agm_quarter=None) is None
    assert "записей нет" in reg.alarm([], today=date(2026, 7, 1), agm_quarter=2)
    paid = reg.build(_store(tmp_path / "p"), ticker="SBER", today=date(2026, 10, 1), cfg=CFG)
    assert reg.alarm(paid, today=date(2026, 10, 1), agm_quarter=2) is None


def test_manual_record_rules(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(reg.RegisterError, match="отсечки"):
        reg.record_manual(store, year=2026, dps=43.8, status="declared", source="doc")
    with pytest.raises(reg.RegisterError, match="документ"):
        reg.record_manual(store, year=2026, dps=43.8, status="recommended", source=" ")
    with pytest.raises(reg.RegisterError, match="DPS"):
        reg.record_manual(store, year=2026, dps=0.0, status="recommended", source="doc")
    with pytest.raises(reg.RegisterError, match="не дата"):
        reg.record_manual(store, year=2026, dps=43.8, status="declared", record_date="19.07.2027", source="doc")


def test_record_dividend_command_and_the_tact_alarm(monkeypatch, tmp_path, capsys):
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    store.write_state(reg.TINVEST_FILE, {"by_ticker": {"SBER": _tinvest_rows()}})
    cfg = config.sources()
    ctx = collect.context(store, date(2026, 7, 1), getter=FakeGetter())
    res = collect.register_check(ctx)
    assert res.status == "degraded" and any("нет записи реестра за 2025 год" in x for x in res.reasons)
    assert "ТРЕВОГА реестра" in capsys.readouterr().out
    assert collect.main(["record-dividend", "--year", "2025", "--dps", "37,64", "--status", "declared",
                         "--record-date", "2026-07-20", "--decided", "2026-06-30",
                         "--source", "https://example.org/disclosure/agm-decision"], today=date(2026, 7, 1)) == 0
    assert collect.main(["record-dividend", "--year", "2025", "--dps", "37,64", "--status", "declared",
                         "--source", "doc"], today=date(2026, 7, 1)) == 1            # у решения ГОСА нужна отсечка
    assert collect.register_check(ctx).status == "ok"
    out = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=date(2026, 7, 1))
    r = _year(out.register, 2025)
    assert r["sources"] == ["T-Invest", "ручная запись"] and r["decided_date"] == "2026-06-30"
    assert out.flags["dividend_register"] == {"raised": False, "detail": ""}
    assert set(reg.KEEP) <= set(r) and cfg["dividends"]["news_kinds"]["interfax"] == "Интерфакс"


def test_agm_news_from_the_feeds_reaches_the_register(monkeypatch, tmp_path):
    """Лента дня Интерфакса и RSS ТАСС → новости темы «дивиденды» → запись реестра с одним источником-новостью."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    g = FakeGetter(overrides={"business/news/": (FIX / "news" / "interfax_day_2026-06-30.html").read_bytes()})
    news.collect(collect.context(store, date(2026, 6, 30), getter=g))
    items = store.read_state("news/items.json")["items"]
    div = {u: v for u, v in items.items() if v["topic"] == "dividend"}
    assert {v["kind"] for v in div.values()} == {"interfax", "tass"}
    assert not any("9000203" in u for u in items)                  # дивиденды другого банка — не эмитент
    rows = reg.build(store, ticker="SBER", today=date(2026, 7, 1), cfg=config.sources())
    r = _year(rows, 2025)
    assert r["status"] == "declared" and len(r["sources"]) == 1 and r["sources"][0] in ("Интерфакс", "ТАСС")
    assert r["record_date"] == "2026-07-20" and r["decided_date"] == "2026-06-30"
    assert "нет записи реестра за 2025 год" in reg.alarm(rows, today=date(2026, 7, 1), agm_quarter=2)
