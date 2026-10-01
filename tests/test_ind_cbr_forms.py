"""Формы ЦБ по регномеру: разбор 101/102/123/135, винтажи, первое появление, недельная перекачка."""

from __future__ import annotations

from datetime import date

import pytest

from indicators import cbr_forms, config
from indicators.sources import DEGRADED, FAILED, OK, Context
from indicators.store import Store
from tests.support_ind import FIX, FakeGetter, use_fixtures

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)


def _b(name):
    return (FIX / "cbr_forms" / name).read_bytes()


def test_parse_dates_and_form_102():
    dates = cbr_forms.parse_dates(_b("dates_f102.xml"))
    assert dates == sorted(dates) and dates[-1] == "2026-09-01"
    s = cbr_forms.f102_summary(cbr_forms.parse_f102(_b("f102_2026-09-01.xml")))
    assert s["ni_ytd"] == pytest.approx(1332.707281)
    assert s["pretax_ytd"] == pytest.approx(1799.340898)
    assert s["tax_ytd"] == pytest.approx(466.633617)


def test_month_value_is_the_difference_of_year_to_date():
    aug = cbr_forms.f102_summary(cbr_forms.parse_f102(_b("f102_2026-09-01.xml")))["ni_ytd"]
    jul = cbr_forms.f102_summary(cbr_forms.parse_f102(_b("f102_2026-08-01.xml")))["ni_ytd"]
    m = cbr_forms.month_from_ytd({"2026M07": jul, "2026M08": aug})
    assert m["2026M08"] == pytest.approx(169.07, abs=0.01)     # релиз: 169,1 млрд за август
    assert "2026M07" not in m                                   # июнь неизвестен
    assert cbr_forms.month_from_ytd({"2026M01": 160.0}) == {"2026M01": 160.0}


def test_forms_135_123_101():
    n = cbr_forms.parse_f135(_b("f135_2026-09-01.xml"))
    assert n["Н1.0"] == 13.095 and n["Н1.1"] == 10.115 and n["Н1.3"] is None
    c = cbr_forms.parse_f123(_b("f123_2026-09-01.xml"))
    assert c["000"] == 8092364887
    new = cbr_forms.f101_derived(cbr_forms.parse_f101(_b("f101_2026-09-01.xml")))
    old = cbr_forms.f101_derived(cbr_forms.parse_f101(_b("f101_2022-02-01.xml")))
    for d in (new, old):
        assert d["loans_fl_gross"] and d["loans_fl_gross"] > 1000 and d["deposits_fl"] > 1000
    assert new["loans_fl_gross"] > old["loans_fl_gross"]
    assert new["iea"] is None and old["iea"] is None                 # без определения фактов активы не считаются


def test_interest_earning_assets_follow_the_facts_definition():
    definition = config.facts_file("bridge_ras_ifrs", FIX / "facts")["iea"]
    rows = cbr_forms.parse_f101(_b("f101_2026-09-01.xml"))
    got = cbr_forms.f101_derived(rows, iea=definition)["iea"]
    assert 30_000 < got < 100_000                                   # млрд ₽
    loans_only = {"codes": {"loans": ["45.0|45.1", "45.2"]}, "codes_required": ["45.0|45.1", "45.2"]}
    d = cbr_forms.f101_derived(rows, iea=loans_only)
    assert d["iea"] == pytest.approx(d["loans_ul_core_gross"] + d["loans_fl_gross"])
    missing = {"codes": {"x": ["99.9"]}, "codes_required": ["99.9"]}
    assert cbr_forms.f101_derived(rows, iea=missing)["iea"] is None
    old = cbr_forms.f101_derived(cbr_forms.parse_f101(_b("f101_2022-02-01.xml")), iea=definition)
    assert old["iea"] is None                                        # эпоха 5-значных счетов — не считается


def _ctx(store, getter, today=date(2026, 10, 1)):
    return Context(store=store, today=today, company=config.company(), cfg=config.sources(), getter=getter)


def test_collect_bootstrap_then_new_date_then_weekly_recheck(monkeypatch, tmp_path):
    use_fixtures(monkeypatch, tmp_path)
    store = Store(tmp_path / "state")
    g = FakeGetter(moment="2026-10-01T05:00:00+00:00")
    r = cbr_forms.collect(_ctx(store, g))
    assert r.status in (OK, DEGRADED)                  # у подставного ЦБ есть формы не на все даты
    index = cbr_forms.load_index(store)
    f102 = index["forms"]["102"]
    assert f102["2026-09-01"]["first_seen_utc"] is None                   # до запуска сборщика
    assert f102["2003-07-01"]["note"] == "до запуска сборщика" and not f102["2003-07-01"]["versions"]
    fetched = [c for c in g.calls if c["headers"].get("SOAPAction", "").endswith("Data102FXML")]
    assert len(fetched) == cbr_forms.RECHECK_DATES
    assert store.load("cbr.f102.ni_ytd").value_as_of("2026M08", "2026-10-01") == pytest.approx(1332.707281)
    assert store.load("cbr.f135.n1_0").value_as_of("2026M08") == pytest.approx(0.13095)
    assert "1481" in "".join(c["data"].decode() for c in g.calls if c["data"])     # регномер — из книги

    # Новый день: появилась дата 2026-10-01 — полная форма и момент первого появления.
    dates_new = _b("dates_f102.xml").replace(b"<dateTime>2026-09-01T00:00:00</dateTime>",
                                             b"<dateTime>2026-10-01T00:00:00</dateTime>"
                                             b"<dateTime>2026-09-01T00:00:00</dateTime>")
    g2 = FakeGetter(overrides={"GetDatesForF102": dates_new, "2026-10-01T00:00:00</dt>": _b("f102_2026-09-01.xml")},
                    moment="2026-10-02T05:00:00+00:00")
    cbr_forms.collect(_ctx(store, g2, date(2026, 10, 2)))
    seen = cbr_forms.load_index(store)["forms"]["102"]["2026-10-01"]
    assert seen["first_seen_utc"] == "2026-10-02T05:00:00+00:00" and len(seen["versions"]) == 1
    assert len([c for c in g2.calls if c["headers"].get("SOAPAction", "").endswith("Data102FXML")]) == 1

    # Через неделю — перекачка последних дат; изменённый ответ — новый винтаж, прежний не стёрт.
    changed = _b("f102_2026-09-01.xml").replace(b"<tp3>1332707281</tp3>", b"<tp3>1333000000</tp3>")
    g3 = FakeGetter(overrides={"GetDatesForF102": dates_new, "2026-09-01T00:00:00</dt>": changed},
                    moment="2026-10-08T05:00:00+00:00")
    r3 = cbr_forms.collect(_ctx(store, g3, date(2026, 10, 8)))
    assert "102:2026-09-01" in r3.data.get("revised", [])
    s = store.load("cbr.f102.ni_ytd")
    assert s.value_as_of("2026M08", "2026-10-07") == pytest.approx(1332.707281)
    assert s.value_as_of("2026M08", "2026-10-08") == pytest.approx(1333.0)
    assert len(cbr_forms.load_index(store)["forms"]["102"]["2026-09-01"]["versions"]) == 2
    assert store.raw_days("cbr_forms")


def test_expected_form102_date_defaults_and_history(monkeypatch, tmp_path):
    store = Store(tmp_path)
    assert cbr_forms.expected_form102_date(store, "2026M09") == ("2026-10-24", "2026-10-26")
    store.write_state(cbr_forms.INDEX_FILE, {"forms": {"102": {
        "2026-08-01": {"first_seen_utc": "2026-08-25T06:00:00+00:00"},
        "2026-09-01": {"first_seen_utc": "2026-09-23T06:00:00+00:00"}}}})
    assert cbr_forms.expected_form102_date(store, "2026M09") == ("2026-10-23", "2026-10-25")


def test_collect_fails_only_without_any_dates_list(monkeypatch, tmp_path):
    from indicators import http

    use_fixtures(monkeypatch, tmp_path)
    g = FakeGetter(overrides={"GetDatesForF": http.FetchError("x", 503, "HTTP 503")})
    r = cbr_forms.collect(_ctx(Store(tmp_path / "state"), g))
    assert r.status == FAILED and "списка дат" in r.detail or "список дат" in r.detail
