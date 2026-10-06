"""Формы ЦБ по регномеру: разбор 101/102/123/135, винтажи, первое появление, недельная перекачка."""

from __future__ import annotations

import hashlib
import re
from datetime import date

import pytest

from indicators import cbr_forms, config, http
from indicators.sources import DEGRADED, FAILED, OK, Context, verdict
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
    asked = [c["data"].decode() for c in g2.calls if c["headers"].get("SOAPAction", "").endswith("Data102FXML")]
    assert sum("2026-10-01T00:00:00" in a for a in asked) == 1
    # Скачанные вчера даты сегодня не перекачиваются; даты, на которые подставной ЦБ формы не отдал,
    # остаются в очереди и запрашиваются снова — «обработана» только форма, дошедшая до рядов.
    assert not any("2026-09-01T00:00:00" in a or "2026-08-01T00:00:00" in a for a in asked)
    waiting = [on for on, e in cbr_forms.load_index(store)["forms"]["102"].items() if cbr_forms.pending(e)]
    assert waiting and len(asked) == 1 + len(waiting)

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


# ------------------------------------------------------------------ очередь: форма «обработана», когда дошла до рядов

OLD, NEW = "2026-09-01", "2026-10-01"
T1, T2 = "2026-10-06T05:00:00+00:00", "2026-10-06T05:03:00+00:00"


def _dates(*days):
    return ("<r>" + "".join(f"<dateTime>{d}T00:00:00</dateTime>" for d in days) + "</r>").encode()


class _Cbr:
    """Подставной сервис ЦБ для сценариев очереди: список дат формы 102 и ответ на её форму — из сценария;
    у прочих форм — одна известная дата, запрашивать нечего. `asked` — что спросили, по порядку."""

    def __init__(self, dates, form, moment=T1):
        self.dates, self.form, self.moment, self.asked = dates, form, moment, []

    def __call__(self, url, *, data=None, headers=None, sink=None, name=None, **_kw):
        op = (headers or {}).get("SOAPAction", "").rsplit("/", 1)[-1]
        if op.startswith("GetDatesForF"):
            self.asked.append(op)
            body = self.dates if op.endswith("102") else _dates(OLD)
        else:
            form = re.search(r"Data(\d{3})", op).group(1)
            day = re.search(r"(\d{4}-\d{2}-\d{2})T", data.decode()).group(1)
            self.asked.append(f"{op}:{day}")
            body = self.form(day) if form == "102" else _b(f"f{form}_{OLD}.xml")
        if isinstance(body, Exception):
            raise body
        return http.Response(url=url, status=200, body=body, fetched_at=self.moment)


def _known_store(monkeypatch, tmp_path, days=(OLD,), checked=T1):
    """Состояние после обычных тактов: у каждой формы известные даты уже в рядах и сверены `checked`
    (sha256 версий — фикстур, поэтому перекачка видит тот же ответ)."""
    use_fixtures(monkeypatch, tmp_path)
    store = Store(tmp_path / "state")
    forms = {}
    for form in cbr_forms.FORMS:
        forms[form] = {}
        for on in days if form == "102" else (OLD,):
            sha = hashlib.sha256(_b(f"f{form}_{on}.xml")).hexdigest()
            forms[form][on] = {"first_seen_utc": None, "checked": checked,
                               "versions": [{"sha256": sha, "fetched_at": checked}]}
    store.write_state(cbr_forms.INDEX_FILE, {"forms": forms, "last_recheck": checked[:10]})
    return store


def _forms_102(asked):
    return [a for a in asked if a.startswith("Data102FXML")]


def test_failed_download_stays_in_the_queue_until_the_form_arrives(monkeypatch, tmp_path):
    """ЦБ назвал новую дату, а форму не отдал: такт деградирует и просит повтор; повтор запрашивает форму
    снова (а не считает дату обработанной), и только тогда статус чист. Момент первого появления — первый."""
    store = _known_store(monkeypatch, tmp_path)
    down = _Cbr(_dates(OLD, NEW), lambda day: http.FetchError(cbr_forms.URL, 503, "HTTP 503"))
    r1 = cbr_forms.collect(_ctx(store, down, date(2026, 10, 6)))
    v1 = verdict([r1])
    assert r1.status == DEGRADED and (v1.code, v1.retry) == (3, True)
    entry = cbr_forms.load_index(store)["forms"]["102"][NEW]
    assert entry == {"first_seen_utc": T1, "versions": []} and cbr_forms.pending(entry)
    assert store.load("cbr.f102.ni_ytd") is None

    up = _Cbr(_dates(OLD, NEW), lambda day: _b("f102_2026-09-01.xml"), moment=T2)
    r2 = cbr_forms.collect(_ctx(store, up, date(2026, 10, 6)))
    assert _forms_102(up.asked) == [f"Data102FXML:{NEW}"]             # повтор спросил ту же форму
    assert r2.status == OK and verdict([r2]).code == 0
    entry = cbr_forms.load_index(store)["forms"]["102"][NEW]
    assert entry["first_seen_utc"] == T1 and len(entry["versions"]) == 1 and not cbr_forms.pending(entry)
    assert store.load("cbr.f102.ni_ytd").value_as_of("2026M09", "2026-10-06") == pytest.approx(1332.707281)

    quiet = _Cbr(_dates(OLD, NEW), lambda day: _b("f102_2026-09-01.xml"), moment=T2)
    cbr_forms.collect(_ctx(store, quiet, date(2026, 10, 7)))
    assert _forms_102(quiet.asked) == []                              # дошла до рядов — больше не качается


def test_unparsed_response_is_not_marked_as_known(monkeypatch, tmp_path):
    """Форма скачана, разбор не прошёл: ответ не считается известным. Каждый следующий такт запрашивает и
    разбирает его снова и снова сообщает о нём; после починки разбора тот же ответ доходит до рядов."""
    store = _known_store(monkeypatch, tmp_path)
    body, parse = _b("f102_2026-09-01.xml"), cbr_forms.parse_f102

    def broken(_):
        raise cbr_forms.FormError("форма 102: разбор сломан")

    for day in (6, 7):                                                # два такта подряд: ошибка не исчезает
        monkeypatch.setattr(cbr_forms, "parse_f102", broken)
        g = _Cbr(_dates(OLD, NEW), lambda d: body)
        r = cbr_forms.collect(_ctx(store, g, date(2026, 10, day)))
        assert _forms_102(g.asked) == [f"Data102FXML:{NEW}"]
        assert r.status == DEGRADED and any("разбор" in x for x in r.reasons) and not verdict([r]).retry
        entry = cbr_forms.load_index(store)["forms"]["102"][NEW]
        assert len(entry["versions"]) == 1 and entry["versions"][-1]["parsed"] is False
        assert cbr_forms.pending(entry) and "checked" not in entry
        assert store.load("cbr.f102.ni_ytd") is None
    monkeypatch.setattr(cbr_forms, "parse_f102", parse)               # разбор починен: байты ответа те же
    g = _Cbr(_dates(OLD, NEW), lambda d: body)
    r = cbr_forms.collect(_ctx(store, g, date(2026, 10, 8)))
    entry = cbr_forms.load_index(store)["forms"]["102"][NEW]
    assert r.status == OK and len(entry["versions"]) == 1 and "parsed" not in entry["versions"][-1]
    assert not r.data.get("revised")                                  # первый разобранный ответ — не пересмотр
    assert store.load("cbr.f102.ni_ytd").value_as_of("2026M09") == pytest.approx(1332.707281)


STUB = b"<html><body>Service temporarily unavailable</body></html>"


def _versions(store, on):
    return [(v["sha256"], v.get("parsed")) for v in cbr_forms.load_index(store)["forms"]["102"][on]["versions"]]


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def test_stub_instead_of_a_form_is_a_failed_download(monkeypatch, tmp_path):
    """Заглушка с кодом 200 вместо формы — отказ с повтором, а не версия формы: в индекс она не попадает,
    сколько бы раз и с какими байтами ни пришла; форма, пришедшая следом, — первый винтаж, не пересмотр."""
    assert cbr_forms.is_service_document(_b("f102_2026-09-01.xml")) and not cbr_forms.is_service_document(STUB)
    store = _known_store(monkeypatch, tmp_path)
    for n in (1, 2):                                                  # байты заглушки от такта к такту разные
        g = _Cbr(_dates(OLD, NEW), lambda d, n=n: STUB + str(n).encode())
        r = cbr_forms.collect(_ctx(store, g, date(2026, 10, 6)))
        v = verdict([r])
        assert r.status == DEGRADED and any("не документ сервиса" in x for x in r.reasons)
        assert (v.code, v.retry) == (3, True)
        assert cbr_forms.load_index(store)["forms"]["102"][NEW] == {"first_seen_utc": T1, "versions": []}
    up = _Cbr(_dates(OLD, NEW), lambda d: _b("f102_2026-09-01.xml"), moment=T2)
    r = cbr_forms.collect(_ctx(store, up, date(2026, 10, 6)))
    assert r.status == OK and not r.data.get("revised")
    assert _versions(store, NEW) == [(_sha(_b("f102_2026-09-01.xml")), None)]


def test_revision_is_reported_when_the_new_vintage_is_in_the_series(monkeypatch, tmp_path):
    """Перекачка принесла другой ответ, а разбор его не прошёл: это не пересмотр (в рядах — прежний
    винтаж); неудачная попытка в записи одна и стоит последней. Вернулся прежний ответ — попытка снята,
    запись чиста. Пришёл новый разбираемый — он второй винтаж, и только теперь такт называет пересмотр."""
    week_ago = "2026-09-29T05:00:00+00:00"
    good = _b("f102_2026-09-01.xml")
    changed = good.replace(b"<tp3>1332707281</tp3>", b"<tp3>1333000000</tp3>")
    broken = good.replace(b"<F102>", b"<F102x>").replace(b"</F102>", b"</F102x>")     # конверт есть, символов нет
    assert cbr_forms.is_service_document(broken) and changed != good
    store = _known_store(monkeypatch, tmp_path, checked=week_ago)

    r = cbr_forms.collect(_ctx(store, _Cbr(_dates(OLD), lambda d: broken), date(2026, 10, 6)))
    assert r.status == DEGRADED and any("разбор" in x for x in r.reasons) and not r.data.get("revised")
    assert _versions(store, OLD) == [(_sha(good), None), (_sha(broken), False)]
    assert cbr_forms.load_index(store)["last_recheck"] == "2026-09-29"                # сверка не состоялась

    other = broken + b"<!-- -->"                                      # другой неразбираемый ответ — на месте прежнего
    r = cbr_forms.collect(_ctx(store, _Cbr(_dates(OLD), lambda d: other), date(2026, 10, 7)))
    assert not r.data.get("revised") and _versions(store, OLD) == [(_sha(good), None), (_sha(other), False)]

    r = cbr_forms.collect(_ctx(store, _Cbr(_dates(OLD), lambda d: good, moment=T2), date(2026, 10, 7)))
    entry = cbr_forms.load_index(store)["forms"]["102"][OLD]
    assert r.status == OK and not r.data.get("revised") and _versions(store, OLD) == [(_sha(good), None)]
    assert entry["checked"] == T2 and not cbr_forms.pending(entry)
    assert store.load("cbr.f102.ni_ytd") is None                      # в ряды за эти такты ничего не легло

    later = "2026-10-13T05:00:00+00:00"
    r = cbr_forms.collect(_ctx(store, _Cbr(_dates(OLD), lambda d: changed, moment=later), date(2026, 10, 13)))
    assert r.status == OK and r.data["revised"] == [f"102:{OLD}"]
    assert _versions(store, OLD) == [(_sha(good), None), (_sha(changed), None)]
    assert store.load("cbr.f102.ni_ytd").value_as_of("2026M08", "2026-10-13") == pytest.approx(1333.0)


def test_pending_date_is_asked_for_even_when_the_list_stops_naming_it(monkeypatch, tmp_path):
    """Дата в очереди, а следующий список дат её не называет: форма всё равно запрашивается и такт о ней
    сообщает — дата не остаётся молча «обнаруженной»."""
    store = _known_store(monkeypatch, tmp_path)
    down = _Cbr(_dates(OLD, NEW), lambda day: http.FetchError(cbr_forms.URL, 503, "HTTP 503"))
    cbr_forms.collect(_ctx(store, down, date(2026, 10, 6)))
    gone = _Cbr(_dates(OLD), lambda day: http.FetchError(cbr_forms.URL, 503, "HTTP 503"), moment=T2)
    r = cbr_forms.collect(_ctx(store, gone, date(2026, 10, 6)))
    assert _forms_102(gone.asked) == [f"Data102FXML:{NEW}"] and r.status == DEGRADED and verdict([r]).retry
    back = _Cbr(_dates(OLD), lambda day: _b("f102_2026-09-01.xml"), moment=T2)
    r = cbr_forms.collect(_ctx(store, back, date(2026, 10, 6)))
    assert r.status == OK and not cbr_forms.pending(cbr_forms.load_index(store)["forms"]["102"][NEW])


def test_dates_list_without_dates_is_a_failure(monkeypatch, tmp_path):
    """Заглушка с кодом 200 вместо списка дат — отказ, а не «новых дат нет»: такт деградирует и просит
    повтор, недельная сверка не считается состоявшейся."""
    with pytest.raises(cbr_forms.FormError):
        cbr_forms.parse_dates(STUB)
    store = _known_store(monkeypatch, tmp_path, checked="2026-09-29T05:00:00+00:00")
    stub = _Cbr(STUB, lambda day: b"")
    r = cbr_forms.collect(_ctx(store, stub, date(2026, 10, 6)))
    v = verdict([r])
    assert r.status == DEGRADED and any("список дат" in x for x in r.reasons) and (v.code, v.retry) == (3, True)
    assert cbr_forms.load_index(store)["last_recheck"] == "2026-09-29"


def test_weekly_recheck_repeats_only_the_dates_that_failed(monkeypatch, tmp_path):
    """Недельная перекачка: дата, чью форму ЦБ не отдал, остаётся несверенной и запрашивается в следующем
    проходе — одна, без повторной перекачки сверенных; отметка недели ставится, когда сверены все."""
    week_ago = "2026-09-29T05:00:00+00:00"
    store = _known_store(monkeypatch, tmp_path, days=("2026-08-01", OLD), checked=week_ago)
    flaky = _Cbr(_dates("2026-08-01", OLD),
                 lambda day: http.FetchError(cbr_forms.URL, 503, "HTTP 503") if day == "2026-08-01" else _b(f"f102_{day}.xml"))
    r1 = cbr_forms.collect(_ctx(store, flaky, date(2026, 10, 6)))
    index = cbr_forms.load_index(store)
    assert r1.status == DEGRADED and verdict([r1]).retry
    assert _forms_102(flaky.asked) == ["Data102FXML:2026-08-01", f"Data102FXML:{OLD}"]
    assert index["forms"]["102"]["2026-08-01"]["checked"] == week_ago and index["forms"]["102"][OLD]["checked"] == T1
    assert index["last_recheck"] == "2026-09-29"

    fine = _Cbr(_dates("2026-08-01", OLD), lambda day: _b(f"f102_{day}.xml"), moment=T2)
    r2 = cbr_forms.collect(_ctx(store, fine, date(2026, 10, 6)))
    index = cbr_forms.load_index(store)
    assert _forms_102(fine.asked) == ["Data102FXML:2026-08-01"]
    assert r2.status == OK and not r2.data.get("revised")             # тот же sha256 — не новый винтаж
    assert index["forms"]["102"]["2026-08-01"]["checked"] == T2 and index["last_recheck"] == "2026-10-06"


def test_index_of_the_previous_format_is_rechecked_by_the_common_mark(monkeypatch, tmp_path):
    """Запись прежнего формата (без `checked`) перекачивается по общей отметке `last_recheck`: раньше
    недели — нет, через неделю — да, и получает свою отметку."""
    store = _known_store(monkeypatch, tmp_path)
    index = cbr_forms.load_index(store)
    for form in cbr_forms.FORMS:
        del index["forms"][form][OLD]["checked"]
    index["last_recheck"] = "2026-10-01"
    store.write_state(cbr_forms.INDEX_FILE, index)
    early = _Cbr(_dates(OLD), lambda day: _b("f102_2026-09-01.xml"))
    cbr_forms.collect(_ctx(store, early, date(2026, 10, 7)))
    assert _forms_102(early.asked) == []
    later = _Cbr(_dates(OLD), lambda day: _b("f102_2026-09-01.xml"), moment=T2)
    cbr_forms.collect(_ctx(store, later, date(2026, 10, 8)))
    assert _forms_102(later.asked) == [f"Data102FXML:{OLD}"]
    assert cbr_forms.load_index(store)["forms"]["102"][OLD]["checked"] == T2


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
