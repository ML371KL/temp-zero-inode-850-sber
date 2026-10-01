"""Такт индикаторов целиком на фикстурах: CLI, нау-каст с записью журнала, выходы для выпуска."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone

import pytest

from indicators import collect, http, outputs, record, sources, tinvest
from indicators import register as reg
from indicators.journal import Journal
from indicators.store import Store
from tests.support_ind import FAKE_TOKEN, FIX, FakeGetter, use_fixtures

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)

TODAY = date(2026, 10, 1)
EXPECTATION = {"period": "2026Q3", "ni_q": 495.0, "nim_q_mgmt": 0.0640, "cor_q_mgmt": 0.0135}


def test_empty_state_never_raises_and_says_why(monkeypatch, tmp_path):
    state = use_fixtures(monkeypatch, tmp_path)
    out = outputs.release_inputs(tickers=["SBER", "SBERP"], peer_tickers=["VTBR"], state_dir=state, today=TODAY)
    assert {f.name for f in fields(outputs.IndicatorOutputs)} >= {"prices", "curve", "journal", "collector"}
    assert out.prices == {"SBER": (), "SBERP": ()} and out.curve is None and out.key_rate is None
    assert out.nowcast is None and out.register == () and out.brokers is None
    assert out.journal["entries"] == [] and out.admission["status"] == "collecting"
    assert out.admission["first_event"] and out.admission["earliest_decision"]
    assert any("SBER" in d for d in out.collector["degraded"])
    assert all(t["status"] == "missing" and t["reason"] for t in out.tiles)
    # П§0.2: причину плитки печатает витрина — словами, без идентификаторов рядов
    assert {t["reason"] for t in out.tiles if t["id"] == "ras_ni_m"} == {"данных ещё нет: релиз РСБУ банка"}
    assert not [t["reason"] for t in out.tiles if re.search(r"[a-z]\.[a-z]|_", t["reason"])]
    assert not (state / "journal.sqlite").exists()                    # только чтение


def _run_all(monkeypatch, tmp_path):
    state = use_fixtures(monkeypatch, tmp_path)
    g = FakeGetter(moment="2026-10-01T06:00:00+00:00")
    code = collect.main(["collect"], getter=g, today=TODAY)
    assert code in (0, 3)
    monkeypatch.setenv("TINVEST_TOKEN", FAKE_TOKEN)                  # вечерний такт — новый процесс со своим токеном
    assert collect.main(["daily-indicators"], getter=g, today=TODAY) in (0, 3)
    store = Store(state)
    code = collect.run_nowcast(store, TODAY, expectation=EXPECTATION, book_version="1.0",
                               to_mgmt_nim=lambda v: v + 0.00384)
    return state, store, code


def test_full_tact_and_release_inputs(monkeypatch, tmp_path):
    state, store, code = _run_all(monkeypatch, tmp_path)
    report = store.read_state("collector_report.json")
    assert report["mode"] == "daily-indicators" and report["sources"]["iss"] == "ok"
    assert report["sources"]["cbr_forms"] in ("ok", "degraded")        # формы не на все даты в фикстурах
    assert report["sources"]["tinvest"] == "degraded"                  # аналог VTBR в фикстурах не найден
    assert any("VTBR" in d for d in report["degraded"])
    # П§0.2: в выпуск причины идут с названием сборщика словами; отчёт такта хранит имена сборщиков
    assert any(d.startswith("tinvest: ") for d in report["degraded"])
    shown = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=TODAY).collector["degraded"]
    assert any(d.startswith("T-Invest: ") and "VTBR" in d for d in shown)
    assert not [d for d in shown if re.match(r"[a-z_]+: ", d)]
    assert outputs.degraded_words(["cbr_forms: форма 102: HTTP 500", "release_watch: РСБУ: x", "прочее"]) == [
        "формы ЦБ: форма 102: HTTP 500", "дозор релиза: РСБУ: x", "прочее"]
    assert set(outputs.COLLECTOR_WORDS) == set(sources.COLLECTORS) | {"release_watch", "register"}

    now = store.read_state("nowcast.json")
    assert now["period"] == "2026Q3" and now["horizon"] == "T-30" and now["book_version"] == "1.0"
    by = now["quarter"]["by_target"]
    ni = by["ni_q"]
    assert ni["forecast"] == pytest.approx(495.0 + ni["w"] * (ni["ras_bridged"] - 495.0))
    assert now["quarter"]["months_known"] == 2
    j = Journal(state / "journal.sqlite", readonly=True)
    rows = j.forecasts()
    assert {(r["target"], r["horizon"]) for r in rows} == {("ni_q", "T-30"), ("nim_q", "T-30"), ("cor_q", "T-30")}
    assert all(r["benchmarks"]["model"] for r in rows)
    assert j.frozen("ni_q", "2026Q3", "bridge") == 1.003
    # T3: ЧПМ через мосты ЧПД и активов фактов; мост, базис и σ заморожены с первой записью
    nim = by["nim_q"]
    assert nim["ras_bridged"] is not None and 0.0 < nim["w"] < 1.0 and "мост ЧПД" in nim["equation"]
    assert j.frozen("nim_q", "2026Q3", "bridge") == pytest.approx(nim["bridge"])
    assert j.frozen("nim_q", "2026Q3", "basis") == "engine" and j.frozen("nim_q", "2026Q3", "sigmas")["ras"]
    # T4: моста CoR нет — прогноз равен ожиданию, и это сказано в статусе
    assert by["cor_q"]["forecast"] == EXPECTATION["cor_q_mgmt"] and by["cor_q"]["ras_estimate"] is None
    assert now["status"]["cor_q"] == by["cor_q"]["equation"] and "T4" in now["status"]["cor_q"]
    rec = {r["target"]: r for r in rows}
    assert rec["cor_q"]["equation"] == by["cor_q"]["equation"] and rec["nim_q"]["equation"] == nim["equation"]
    # № 47: гайденс ЧПМ — точка фактов; у CoR в фактах граница («меньше 1,4 %») — эталоном не служит
    assert rec["nim_q"]["benchmarks"]["guidance"] == pytest.approx(0.062) and "guidance" not in rec["cor_q"]["benchmarks"]
    # № 46: книга ожидания — в записи; sha — последний собранный выпуск (на свежем состоянии — нули)
    assert {r["book_version"] for r in rows} == {"1.0"} and {r["release_sha"] for r in rows} == {"0" * 12}
    # повторный нау-каст того же дня не пишет новых строк
    collect.run_nowcast(store, TODAY, expectation={**EXPECTATION, "ni_q": 520.0})
    assert len(j.forecasts()) == 3

    out = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=TODAY)
    last = out.prices["SBER"][-1]
    assert last.source == "tinvest" and last.price == pytest.approx(272.7) and last.time == "13:53"
    assert out.curve["nodes"]["10"] == pytest.approx(0.168282)
    assert out.key_rate == {"value": 0.14, "date": "2026-09-30", "since": "2026-07-27"}
    assert out.register[-1]["dps"] == pytest.approx(37.64) and out.register[-1]["ex_date"] == "2026-07-20"
    assert out.register[-1]["status"] == "paid" and out.register[-1]["sources"][0] == "T-Invest"
    assert out.flags["dividend_register"]["raised"] is False
    assert out.brokers["n"] == 13 and "Брокер" not in json.dumps(out.brokers, ensure_ascii=False)
    assert out.price_history["SBER"][0] == ("2026-08-20", 270.66)
    tiles = {t["id"]: t for t in out.tiles}
    assert tiles["key_rate"]["value"] == 0.14 and tiles["key_rate"]["since"] == "2026-07-27"
    assert tiles["price_SBER"]["status"] == "ok" and tiles["ras_ni_m"]["value"] == pytest.approx(169.1)
    assert len(tiles["ofz_10y"]["history"]["date"]) <= outputs.TILE_POINTS
    assert out.ras_months[-1]["month"] == "2026M08" and out.ras_months[-1]["ni"] == pytest.approx(169.1)
    assert out.form102 and out.form102[-1]["ok"] is True
    assert out.ras_schedule["month"] == "2026M09" and out.ras_schedule["release_date"] == "2026-10-09"
    assert out.nowcast["period"] == "2026Q3" and out.journal["total_entries"] == 3
    assert {e["book_version"] for e in out.journal["entries"]} == {"1.0"}
    # аналог без инструмента — ответ 404: деградация невосполнимого источника, но повтор её не снимет
    assert out.collector["retry"] is False and out.collector["streak"] == {"cbr_forms": 1, "news": 1, "tinvest": 2}
    assert out.retro["main"] == "ras_bridge" and out.flags["ras_mismatch"]["raised"] is False
    assert out.admission["status"] == "collecting"
    for f in state.rglob("*"):
        if f.is_file():
            assert FAKE_TOKEN.encode() not in f.read_bytes()


def test_tiles_window_is_a_year_to_the_last_point_and_money_is_rounded(monkeypatch, tmp_path):
    """№ 60: окно плитки — год до последней точки (месяц — по его концу); мин/макс — за окно; деньги — до 0,01."""
    state, store, _ = _run_all(monkeypatch, tmp_path)
    out = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=TODAY)
    tiles = {t["id"]: t for t in out.tiles if t["status"] != "missing"}
    assert {"ras_ni_m", "f102_ni_ytd", "key_rate", "price_SBER"} <= set(tiles)
    assert {tiles[k]["unit"] for k in ("ras_ni_m", "f102_ni_ytd", "price_SBER")} == {"bn", "price"}     # округляются
    for t in tiles.values():
        last = outputs._period_day(t["date"])
        days = [outputs._period_day(d) for d in t["history"]["date"]]
        assert all((last - d).days <= outputs.HISTORY_DAYS for d in days), t["id"]
        for edge in (t["min"], t["max"]):
            assert (last - outputs._period_day(edge["date"])).days <= outputs.HISTORY_DAYS, t["id"]
        assert t["min"]["value"] <= min(t["history"]["value"]) and t["max"]["value"] >= max(t["history"]["value"])
        if t["unit"] in outputs.MONEY_UNITS:
            numbers = [t["value"], t["min"]["value"], t["max"]["value"], t["change"], *t["history"]["value"]]
            assert all(v == round(v, 2) for v in numbers if v is not None), t["id"]
    month = tiles["ras_ni_m"]
    assert month["date"] == "2026M08" and month["history"]["date"][0] == "2025M08"
    assert len(month["history"]["date"]) == 13                          # год по концам месяцев, не 20 месяцев
    assert month["min"]["date"] >= "2025M08"                            # минимум — за окно, не за весь ряд
    full = store.load("ras.release.np_m").history()
    assert min(full.values()) < month["min"]["value"]                   # за весь ряд минимум другой
    rate = tiles["key_rate"]
    assert rate["max"]["date"] >= "2025-09-29" and rate["max"]["value"] < 0.18     # 18 % — раньше окна


def test_the_journal_names_the_book_even_against_a_foreign_release(monkeypatch, tmp_path):
    """№ 46: sha записи — чужой (прошлый) выпуск со своей книгой; книга ожидания пишется отдельно."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    (state / "release").mkdir(parents=True)
    (state / "release" / "latest.json").write_text(json.dumps({"meta": {
        "payload_sha256": "ab" * 32, "book_version": "1.1", "generated_at": "2026-09-30T18:00:00+00:00"}}),
        encoding="utf-8")
    collect.run_nowcast(store, TODAY, expectation=EXPECTATION, book_version="1.2", to_mgmt_nim=lambda v: v)
    rows = Journal(state / "journal.sqlite", readonly=True).forecasts()
    assert {r["release_sha"] for r in rows} == {"ab" * 6} and {r["book_version"] for r in rows} == {"1.2"}
    out = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=TODAY)
    assert out.journal["releases"] == {"ab" * 6: {"book_version": "1.1", "generated_at": "2026-09-30T18:00:00+00:00"}}
    assert {e["book_version"] for e in out.journal["entries"]} == {"1.2"}


def test_guidance_point_comes_from_the_facts_with_its_moment(monkeypatch, tmp_path):
    """№ 47: гайденс года — точка фактов, известная на дату; ручная запись важнее; граница — не точка."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    assert collect.guidance_point(store, "nim", 2026, "2026-10-01") == pytest.approx(0.062)
    assert collect.guidance_point(store, "nim", 2026, "2026-05-01") == pytest.approx(0.059)     # до пересмотра 29.07
    assert collect.guidance_point(store, "nim", 2026, "2025-12-01") is None                     # ещё не дан
    assert collect.guidance_point(store, "nim", 2027, "2027-03-01") is None                     # факты другого года
    assert collect.guidance_point(store, "cor", 2026, "2026-10-01") is None                     # точки нет
    assert collect.guidance_point(store, "cor_max", 2026, "2026-10-01") is None                 # граница — не точка
    assert collect.main(["record-guidance", "--year", "2027", "--item", "nim", "--value", "5,9%",
                         "--source", "День инвестора, с. 12"], today=TODAY) == 0
    # № 69: ручная запись несёт момент настенных часов (UTC) — читаем её на дату этого же момента,
    # а не на замороженный TODAY; днём раньше она ещё не известна
    day = date.fromisoformat(store.load("guidance.nim").point_as_of("2027").fetched_at[:10])
    assert collect.guidance_point(store, "nim", 2027, day.isoformat()) == pytest.approx(0.059)
    assert collect.guidance_point(store, "nim", 2027, (day - timedelta(days=1)).isoformat()) is None
    with pytest.raises(SystemExit):                                                              # ключ фактов — fee_growth
        collect.main(["record-guidance", "--year", "2026", "--item", "fees_growth", "--value", "5%", "--source", "x"],
                     today=TODAY)
    assert collect.main(["record-guidance", "--year", "2026", "--item", "loan_growth.retail", "--value", "6%",
                         "--source", "презентация, с. 23"], today=TODAY) == 0


@pytest.mark.parametrize("days_ahead", (0, 1, 7, 30, 60, 400))
def test_a_manual_record_is_known_from_the_day_of_its_own_moment(monkeypatch, tmp_path, days_ahead):
    """№ 69: момент ручной записи — настенные часы, а не `today` команды: при любом положении часов запись
    читается на дату своего момента и не читается днём раньше (тест не привязан к дате прогона)."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    wall = datetime(TODAY.year, TODAY.month, TODAY.day, 21, 30, tzinfo=timezone.utc) + timedelta(days=days_ahead)
    monkeypatch.setattr(record, "_now", lambda: wall.isoformat(timespec="seconds"))
    year = str(wall.year + 1)
    assert collect.main(["record-guidance", "--year", year, "--item", "nim", "--value", "5,9%",
                         "--source", "День инвестора, с. 12"], today=TODAY) == 0
    assert collect.main(["record-consensus", "--period", "2026Q3", "--value", "505", "--source", "Интерфакс"],
                        today=TODAY) == 0
    day = wall.date()
    assert store.load("guidance.nim").point_as_of(year).fetched_at[:10] == day.isoformat()
    assert collect.guidance_point(store, "nim", int(year), day.isoformat()) == pytest.approx(0.059)
    assert collect.guidance_point(store, "nim", int(year), (day - timedelta(days=1)).isoformat()) is None
    consensus = store.load("consensus.ni_q")
    assert consensus.value_as_of("2026Q3", day.isoformat()) == pytest.approx(505.0)
    assert consensus.value_as_of("2026Q3", (day - timedelta(days=1)).isoformat()) is None


def test_nowcast_warns_when_the_far_record_has_no_guidance(monkeypatch, tmp_path, capsys):
    """№ 47: запись T-90 без гайденса года — «ВНИМАНИЕ» и код 3: эталон замораживается с записью."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    kw = {"expectation": EXPECTATION, "book_version": "1.2", "to_mgmt_nim": lambda v: v}
    assert collect.run_nowcast(store, date(2026, 8, 5), **kw) == 0                # гайденс 2026 года есть в фактах
    assert Journal(state / "journal.sqlite", readonly=True).forecast("nim_q", "2026Q3", "T-90")["benchmarks"]["guidance"]
    capsys.readouterr()
    monkeypatch.setattr(collect, "guidance_point", lambda *a, **k: None)
    other = Store(tmp_path / "other")
    assert collect.run_nowcast(other, date(2026, 7, 29), **kw) == 3               # за день до окна T-90
    assert "нет гайденса года по ЧПМ" in capsys.readouterr().out
    assert "через 1 дн." in other.read_state("nowcast.json")["notes"][0]
    assert collect.run_nowcast(other, date(2026, 8, 5), **kw) == 3                # запись ложится без эталона
    row = Journal(other.root / "journal.sqlite", readonly=True).forecast("nim_q", "2026Q3", "T-90")
    assert "guidance" not in row["benchmarks"] and "ложится" in other.read_state("nowcast.json")["notes"][0]
    assert collect.run_nowcast(other, date(2026, 8, 6), **kw) == 0                # запись сделана — напоминать нечего


def test_the_token_is_gone_before_foreign_input_is_parsed(monkeypatch, tmp_path):
    """В10: в общем запуске сборщик T-Invest идёт первым, после него токена в окружении процесса нет."""
    import os

    use_fixtures(monkeypatch, tmp_path)
    inner = FakeGetter(moment="2026-10-01T06:00:00+00:00")
    seen: list[tuple[bool, bool]] = []                               # (запрос к T-Invest, токен в окружении)

    def getter(url, **kw):
        seen.append(("invest-public-api" in url, bool(os.environ.get("TINVEST_TOKEN"))))
        return inner(url, **kw)

    collect.main(["collect"], getter=getter, today=TODAY)
    assert {tinvest_call for tinvest_call, _ in seen} == {True, False}
    assert all(has_token == to_tinvest for to_tinvest, has_token in seen)
    last_tinvest = max(i for i, (to_tinvest, _) in enumerate(seen) if to_tinvest)
    assert not any(to_tinvest for to_tinvest, _ in seen[last_tinvest + 1:]) and last_tinvest < len(seen) - 1
    assert "TINVEST_TOKEN" not in os.environ


def test_only_and_skip_split_the_tact_and_share_one_report(monkeypatch, tmp_path, capsys):
    """В9, В10: сборщик T-Invest — отдельным запуском (`--only`), остальное — вторым (`--skip`); отчёт один."""
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    g = FakeGetter(moment="2026-10-01T06:00:00+00:00")
    first = collect.main(["collect", "--only", "tinvest"], getter=g, today=TODAY)
    report = store.read_state("collector_report.json")
    assert first == 3 and [d["name"] for d in report["details"]] == ["tinvest"]      # аналог не найден — деградация
    assert all("invest-public-api" in c["url"] for c in g.calls)                       # токен нужен только здесь
    assert "release_watch" not in report                                               # шаги такта — во втором запуске
    g.calls.clear()
    second = collect.main(["collect", "--skip", "tinvest"], getter=g, today=TODAY)
    report = store.read_state("collector_report.json")
    assert not any("invest-public-api" in c["url"] for c in g.calls)
    assert {d["name"] for d in report["details"]} == {"tinvest", "cbr_forms", "iss", "cbr", "news", "release_watch",
                                                      "register"}
    assert report["sources"]["tinvest"] == "degraded" and any("VTBR" in d for d in report["degraded"])
    assert report["code"] == max(first, second) == 3
    assert report["retry"] is True                                # формы ЦБ не на все даты: сетевой отказ (5xx)
    assert {d["name"]: d["retry"] for d in report["details"]}["tinvest"] is False      # 404 повтор не снимет
    assert report["release_watch"]["months"] and "streak" in report
    assert collect.main(["collect", "--only", "нет_такого"], getter=g, today=TODAY) == 1
    assert "нет сборщика" in capsys.readouterr().out
    assert collect.selected("daily-indicators", None, "tinvest") == ("iss", "cbr")
    assert collect.selected("release-watch", "tinvest", None) == ()


def test_a_recoverable_source_alarms_only_on_the_third_tact_in_a_row(monkeypatch, tmp_path, capsys):
    """В9: ставки вкладов (плитка) не ответили — два такта код 0 и строка «такт k из 3», с третьего — тревога."""
    state = use_fixtures(monkeypatch, tmp_path, token=False)
    store = Store(state)
    paid = tinvest.parse_dividends(json.loads((FIX / "tinvest" / "dividends_SBER.json").read_text(encoding="utf-8")))
    store.write_state(reg.TINVEST_FILE, {"by_ticker": {"SBER": paid}})                # реестр в порядке — тревоги нет
    down = {"avgprocstav": http.FetchError("https://example.org/avgprocstav", 503, "HTTP 503")}
    codes = []
    for day in (1, 2, 2, 3, 4):                      # 02.10 — два запуска: повтор внутри такта счётчик не двигает
        code = collect.main(["daily-indicators", "--skip", "tinvest"], getter=FakeGetter(overrides=down),
                            today=date(2026, 10, day))
        report = store.read_state("collector_report.json")
        codes.append((code, report["streak"].get("cbr"), report["retry"]))
    assert codes == [(0, 1, False), (0, 2, False), (0, 2, False), (3, 3, False), (3, 4, False)]
    text = capsys.readouterr().out
    assert "cbr: деградация (такт 1 из 3)" in text and "cbr: деградация 3-й такт подряд — тревога" in text
    out = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=date(2026, 10, 4))
    assert out.collector["streak"]["cbr"] == 4 and any("ставки вкладов" in d for d in out.collector["degraded"])
    assert out.collector["alarm"] == ["cbr"]
    assert collect.main(["daily-indicators", "--skip", "tinvest"], getter=FakeGetter(), today=date(2026, 10, 5)) == 0
    assert "cbr" not in store.read_state("collector_report.json")["streak"]


def test_the_build_is_told_which_sources_already_alarm(monkeypatch, tmp_path):
    """№ 89: сборка получает `collector.alarm` — источники, уже получившие код 3 у сборщиков. Восполнимый
    источник плиток моложе трёх тактов туда не входит (причина — только в `degraded`), с третьего — входит."""
    state = use_fixtures(monkeypatch, tmp_path, token=False)
    store = Store(state)
    paid = tinvest.parse_dividends(json.loads((FIX / "tinvest" / "dividends_SBER.json").read_text(encoding="utf-8")))
    store.write_state(reg.TINVEST_FILE, {"by_ticker": {"SBER": paid}})
    assert outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=TODAY).collector["alarm"] == []
    down = {"avgprocstav": http.FetchError("https://example.org/avgprocstav", 503, "HTTP 503")}
    seen = []
    for day in (1, 2, 5):
        code = collect.main(["daily-indicators", "--skip", "tinvest"], getter=FakeGetter(overrides=down),
                            today=date(2026, 10, day))
        got = outputs.release_inputs(tickers=["SBER", "SBERP"], state_dir=state, today=date(2026, 10, day)).collector
        seen.append((code, got["alarm"], any("ставки вкладов" in d for d in got["degraded"])))
    assert seen == [(0, [], True), (0, [], True), (3, ["cbr"], True)]
    # критический источник деградировал — тревога сразу, без счётчика; невосполнимый — тоже
    quotes = {"/securities.json": b'{"marketdata": {"columns": [], "data": []}, "securities": {"columns": [], "data": []}}'}
    code = collect.main(["daily-indicators", "--skip", "tinvest"], getter=FakeGetter(overrides=quotes),
                        today=date(2026, 10, 6))
    report = store.read_state("collector_report.json")
    alarm = outputs.collector_alarm(report)
    assert report["sources"]["iss"] != "ok" and "iss" in alarm and code in (1, 3)
    assert outputs.collector_alarm({}) == [] and outputs.collector_alarm({"details": [{"name": "x", "code": 0}]}) == []
    assert outputs.collector_alarm({"details": [{"name": "news", "code": 3}, {"name": "iss", "code": 1}]}) == ["news"]


def test_release_watch_mode_skips_outside_the_window(monkeypatch, tmp_path, capsys):
    use_fixtures(monkeypatch, tmp_path)
    g = FakeGetter()
    assert collect.main(["release-watch"], getter=g, today=date(2026, 10, 3)) == 0
    assert "пропуск" in capsys.readouterr().out and not g.calls
    code = collect.main(["release-watch", "--force"], getter=g, today=date(2026, 10, 3))
    assert code in (0, 3) and g.calls


def test_status_health_and_record_commands(monkeypatch, tmp_path, capsys):
    state, store, _ = _run_all(monkeypatch, tmp_path)
    assert collect.main(["status"], today=TODAY) == 0
    assert collect.main(["health"], today=TODAY) == 0
    assert (state / "state_size.log").exists()
    assert "задан" in capsys.readouterr().out
    assert collect.main(["record-consensus", "--period", "2026Q3", "--value", "505", "--source", "Интерфакс"],
                        today=TODAY) == 0
    assert collect.main(["record-guidance", "--year", "2026", "--item", "roe", "--value", "22%",
                         "--source", "презентация 2К26, с. 23"], today=TODAY) == 0
    assert store.load("guidance.roe").value_as_of("2026") == pytest.approx(0.22)
    assert collect.main(["record-actual", "--target", "ni_q", "--period", "2026Q3", "--value", "512,3",
                         "--source", "МСФО 9М26, с. 6", "--reported-on", "2026-10-28"], today=TODAY) == 0
    assert collect.main(["record-actual", "--target", "ni_q", "--period", "2026Q3", "--value", "513",
                         "--source", "исправление"], today=TODAY) == 1          # факт неснимаем
    assert store.load("actual.ni_q").value_as_of("2026Q3") == pytest.approx(512.3)
    assert collect.main(["record-ras", "--month", "2026M09", "--source", "релиз 9М26",
                         "np_ytd=1500", "np_m=167,3"], today=date(2026, 10, 9)) == 0
    assert collect.main(["record-ras", "--month", "2026M09", "--source", "x", "eps=1"], today=TODAY) == 1


def test_open_period_follows_the_last_ifrs_fact(monkeypatch, tmp_path):
    state, store, _ = _run_all(monkeypatch, tmp_path)
    assert collect.open_period(store, TODAY) == "2026Q3"
    assert collect.open_period(Store(tmp_path / "empty"), TODAY) == "2026Q3"      # без фактов — прошлый квартал
    assert collect.open_period(Store(tmp_path / "empty"), date(2026, 12, 1)) == "2026Q3"


def test_combine_is_re_exported_for_the_core():
    """W1/C6: ядро берёт `combine` из `indicators/outputs.py` — одна точка входа индикаторов."""
    from indicators import nowcast

    assert outputs.combine is nowcast.combine and "combine" in outputs.__all__
