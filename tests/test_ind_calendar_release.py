"""Календарь релизов и дозор РСБУ: приём при совпадении двух видов источников, тождество, расхождения, ручной ввод."""

from __future__ import annotations

import json
from datetime import date

import pytest

from indicators import calendar as cal
from indicators import config, news, record, release_watch, sber_pdf
from indicators.sources import Context
from indicators.store import Point, Store
from tests.support_ind import FIX, FakeGetter, seeded_store, use_fixtures

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)


def _sched(monkeypatch, tmp_path):
    use_fixtures(monkeypatch, tmp_path)
    return config.sources()["schedule"]


def test_windows_from_schedule(monkeypatch, tmp_path):
    s = _sched(monkeypatch, tmp_path)
    assert cal.ras_release_window("2026M09", s) == (date(2026, 10, 8), date(2026, 10, 15))
    assert cal.ras_release_window("2026M12", s) == (date(2027, 1, 17), date(2027, 1, 20))
    assert cal.ifrs_window("2026Q3", s) == (date(2026, 10, 20), date(2026, 10, 31))
    assert cal.ifrs_window("2026Q4", s) == (date(2027, 2, 20), date(2027, 2, 28))


def test_dates_from_tinvest_and_facts(monkeypatch, tmp_path):
    s = _sched(monkeypatch, tmp_path)
    store = Store(tmp_path / "state")
    reports = json.loads((FIX / "tinvest" / "reports_SBER.json").read_text(encoding="utf-8"))
    from indicators import tinvest

    store.write_state("tinvest/reports.json", {"events": tinvest.parse_reports(reports)})
    rel = cal.ras_release("2026M09", store=store, schedule=s, facts_events=[])
    assert rel["date"] == "2026-10-09" and rel["confirmed"] and rel["src"].startswith("T-Invest")
    rel_aug = cal.ras_release("2026M08", store=store, schedule=s, facts_events=[])
    assert rel_aug["precision"] == "window" and rel_aug["earliest"] == "2026-09-08"
    assert cal.ifrs_report("2026Q3", store=store, schedule=s, facts_events=[])["date"] == "2026-10-28"
    facts = config.calendar_events()
    assert cal.ifrs_report("2026Q3", store=Store(tmp_path / "x"), schedule=s, facts_events=facts)["src"] \
        == "facts/calendar.json"


def test_next_ras_and_events(monkeypatch, tmp_path):
    s = _sched(monkeypatch, tmp_path)
    store = seeded_store(tmp_path / "state")
    nxt = cal.next_ras(date(2026, 10, 1), store=store, schedule=s, facts_events=[])
    assert nxt["month"] == "2026M09" and nxt["enters_via"] == "release"
    assert nxt["form102_date_est"] == "2026-10-24" and "days" not in nxt
    # № 100: дата формы — оценка окном, верхний край отдаётся рядом с нижним
    assert nxt["form102_latest_est"] == "2026-10-26" and nxt["form102_date_est"] <= nxt["form102_latest_est"]
    form = next(e for e in cal.events(date(2026, 10, 1), store=store, schedule=s, facts_events=[])
                if e["kind"] == "form102" and e["covers"] == "2026M09")
    assert (form["precision"], form["confirmed"]) == ("window", False)
    assert (form["earliest"], form["latest"]) == (nxt["form102_date_est"], nxt["form102_latest_est"])
    ev = cal.events(date(2026, 10, 1), store=store, schedule=s, facts_events=config.calendar_events(),
                    register=[{"year": 2026, "record_date": "2027-07-19", "pay_date": "2027-08-02"}])
    kinds = {e["kind"] for e in ev}
    assert {"ras", "form102", "ifrs", "cbr_rate", "record", "pay"} <= kinds
    assert [e["date"] for e in ev] == sorted(e["date"] for e in ev)
    assert len([e for e in ev if e["kind"] == "ifrs" and e["covers"] == "2026Q3"]) == 1
    assert cal.is_release_day(date(2026, 10, 9), store=store, schedule=s, facts_events=[])
    assert not cal.is_release_day(date(2026, 10, 3), store=store, schedule=s, facts_events=[])


def _watch_ctx(monkeypatch, tmp_path, today=date(2026, 9, 9)):
    state = use_fixtures(monkeypatch, tmp_path)
    store = seeded_store(state)
    # сырьё дня релиза 8М26 без итога: снимаем принятое из истории, чтобы дозор принимал заново
    ctx = Context(store=store, today=today, company=config.company(), cfg=config.sources(), getter=FakeGetter())
    return ctx


def test_release_accepted_when_two_kinds_agree(monkeypatch, tmp_path):
    ctx = _watch_ctx(monkeypatch, tmp_path)
    news.collect(ctx)
    out = release_watch.watch(ctx, "2026M08")
    st = ctx.store.read_state("release_watch/2026M08.json")
    acc = st["accepted"]
    assert acc["np_ytd"]["value"] == pytest.approx(1332.7)
    assert {"interfax", "telegram:markettwits"} <= set(acc["np_ytd"]["kinds"])
    assert acc["np_m"]["value"] == pytest.approx(169.1)
    # поток одного вида — принят тождеством нарастающего итога с июлем ряда; доли одного вида — нет
    assert acc["nii_ytd"]["kinds"] == acc["prov_m"]["kinds"] == ["interfax", "identity"]
    assert acc["nii_m"]["value"] == pytest.approx(325.2) and acc["prov_m"]["value"] == pytest.approx(-107.8)
    assert "cor_m" not in acc and "roe_m" not in acc and "cir_ytd" not in acc
    assert out["complete"] and not out["mismatches"] and not out["identity_failures"]


def test_pdf_table_confirms_the_text(monkeypatch, tmp_path):
    ctx = _watch_ctx(monkeypatch, tmp_path)
    news.collect(ctx)
    lines = sber_pdf.words_tsv_lines((FIX / "sber_pdf" / "ras_2026M08_p3.words.tsv").read_text(encoding="utf-8"))
    ctx.store.write_state("sber_pdf/candidates.json", {"x": {
        "kind": "ras_release", "month": "2026M08", "status": "ok", "url": "u", "sha256": "0" * 64,
        "numbers": sber_pdf.parse_ras_table(lines)}})
    out = release_watch.watch(ctx, "2026M08")
    acc = ctx.store.read_state("release_watch/2026M08.json")["accepted"]
    for k, v in {"nii_ytd": 2441.3, "nii_m": 325.2, "prov_m": -107.8, "opex_ytd": -806.7, "fee_m": 59.8}.items():
        assert acc[k]["value"] == pytest.approx(v) and "pdf" in acc[k]["kinds"], k
    assert not out["identity_failures"]
    assert ctx.store.load("ras.release.nii_m").value_as_of("2026M08") == pytest.approx(325.2)


def test_disagreement_raises_the_flag(monkeypatch, tmp_path):
    ctx = _watch_ctx(monkeypatch, tmp_path)
    news.collect(ctx)
    ctx.store.write_state("sber_pdf/candidates.json", {"x": {
        "kind": "ras_release", "month": "2026M08", "status": "ok", "url": "u",
        "numbers": {"nii_ytd": 2414.3}}})                                  # переставленные цифры распознавания
    out = release_watch.watch(ctx, "2026M08")
    assert "nii_ytd" in out["mismatches"]
    flag = release_watch.mismatch_flag(ctx.store, ["2026M08"])
    assert flag["raised"] and "2026M08" in flag["detail"]
    # П§0.2: плашку печатает витрина — метрика названа словами, а не ключом
    assert "чистый процентный доход с начала года" in flag["detail"] and "nii_ytd" not in flag["detail"]
    reasons = release_watch.run(ctx).reasons
    assert any("источники разошлись — чистый процентный доход с начала года" in r for r in reasons)


def test_form102_is_a_second_source_and_a_check(monkeypatch, tmp_path):
    ctx = _watch_ctx(monkeypatch, tmp_path)
    rows = release_watch.form102_rows(ctx.store, ["2026M07", "2026M08"])
    by = {r["month"]: r for r in rows}
    assert by["2026M08"]["ni"] == pytest.approx(169.07, abs=0.01) and by["2026M08"]["ok"] is True
    ctx.store.upsert("cbr.f102.ni_ytd", [Point("2026M08", 1340.0, "2026-10-01T06:00:00+00:00")])
    assert release_watch.mismatch_flag(ctx.store, ["2026M08"])["raised"]


def _release(store, month):
    return {k: store.load(f"ras.release.{k}").value_as_of(month) for k in ("np_ytd", "np_m")}


def test_tact_of_the_release_day_leaves_the_previous_month_alone(monkeypatch, tmp_path):
    """№ 5: такт 09.09 проходит июль и август; тексты 8М июлю чисел не дают («332,7 млрд» — не «7 м»)."""
    ctx = _watch_ctx(monkeypatch, tmp_path)
    july = _release(ctx.store, "2026M07")
    news.collect(ctx)
    res = release_watch.run(ctx)
    by = {m["month"]: m for m in res.data["months"]}
    assert list(by) == ["2026M07", "2026M08"]
    assert _release(ctx.store, "2026M07") == july == {"np_ytd": pytest.approx(1163.6), "np_m": pytest.approx(168.4)}
    assert by["2026M07"]["new"] == [] and not by["2026M07"]["identity_failures"] and not by["2026M07"]["mismatches"]
    texts = [c for rows in ctx.store.read_state("release_watch/2026M07.json")["candidates"].values() for c in rows]
    assert {c["kind"] for c in texts} <= {"form102"}                       # у июля — только форма 0409102
    assert not by["2026M08"]["identity_failures"] and not by["2026M08"]["mismatches"] and res.status == "ok"
    assert not release_watch.mismatch_flag(ctx.store, ["2026M07", "2026M08"])["raised"]


def _as_next_month(item: dict, *, ytd: str, month: str, published: str) -> dict:
    """Текст релиза 8М → синтетический текст релиза 9М: слова периода, число нарастающим и за месяц."""
    text = item["text"]
    for old, new in (("8 МЕСЯЦЕВ", "9 МЕСЯЦЕВ"), ("8 месяцев", "9 месяцев"), ("восьми месяцев", "девяти месяцев"),
                     ("восемь месяцев", "девять месяцев"),
                     ("январь-август", "январь-сентябрь"), ("АВГУСТЕ", "СЕНТЯБРЕ"), ("августе", "сентябре"),
                     ("за август", "за сентябрь"), ("За август", "За сентябрь"),
                     ("августа", "сентября"), ("1 сентября", "1 октября"),
                     ("1 трлн 332,7", "1 трлн " + ytd), ("1 332,7", "1 " + ytd), ("169,1", month)):
        text = text.replace(old, new)
    # Список замен привязан к словам фикстур: слово августа, пережившее замену, — тест проверял бы не то.
    left = [w for w in ("август", "8 месяц", "восьм", "восемь") if w in text.lower()]
    assert not left, (left, text[:80])
    return dict(item, text=text, published=published)


def _next_release_ctx(monkeypatch, tmp_path, *, ytd="501,8", month="169,1", keep=False):
    """09.10: тексты релиза 9М (из фикстур 8М), своих текстов августа на свежем состоянии нет."""
    ctx = _watch_ctx(monkeypatch, tmp_path)
    news.collect(ctx)
    held = ctx.store.read_state("news/items.json")
    fresh = {url + "#9m": _as_next_month(item, ytd=ytd, month=month, published="2026-10-09T09:50:00+03:00")
             for url, item in held["items"].items() if item["topic"] == "ras"}
    held["items"] = {**(held["items"] if keep else {}), **fresh}
    ctx.store.write_state("news/items.json", held)
    return Context(store=ctx.store, today=date(2026, 10, 9), company=ctx.company, cfg=ctx.cfg, getter=FakeGetter())


@pytest.mark.parametrize("keep", [False, True])
def test_next_release_does_not_rewrite_the_previous_month(monkeypatch, tmp_path, keep):
    """№ 5: 09.10 — релиз 9М. Август (ряд из истории) не меняется, `ras_mismatch` не поднят, сентябрь принят."""
    ctx = _next_release_ctx(monkeypatch, tmp_path, keep=keep)
    august = _release(ctx.store, "2026M08")
    res = release_watch.run(ctx)
    by = {m["month"]: m for m in res.data["months"]}
    assert list(by) == ["2026M08", "2026M09"]
    assert _release(ctx.store, "2026M08") == august == {"np_ytd": pytest.approx(1332.7), "np_m": pytest.approx(169.1)}
    assert by["2026M08"]["new"] == [] and not by["2026M08"]["identity_failures"]
    assert by["2026M09"]["month_accepted"] and not by["2026M09"]["identity_failures"]
    assert _release(ctx.store, "2026M09") == {"np_ytd": pytest.approx(1501.8), "np_m": pytest.approx(169.1)}
    acc = ctx.store.read_state("release_watch/2026M09.json")["accepted"]
    assert {"interfax", "telegram:markettwits"} <= set(acc["np_ytd"]["kinds"])
    assert res.status == "ok", res.reasons
    assert not release_watch.mismatch_flag(ctx.store, ["2026M07", "2026M08", "2026M09"])["raised"]


def test_a_flow_failing_the_identity_is_not_written(monkeypatch, tmp_path):
    """№ 5: провал тождества «НМ = НМ прошлого месяца + месяц» не пишет поток в ряд."""
    ctx = _next_release_ctx(monkeypatch, tmp_path, ytd="501,8", month="150,0")       # 1332,7 + 150,0 ≠ 1501,8
    res = release_watch.run(ctx)
    out = next(m for m in res.data["months"] if m["month"] == "2026M09")
    assert out["identity_failures"] and "np_ytd" not in out["new"] and "np_m" not in out["new"]
    assert not out["complete"] and not out["month_accepted"]
    assert _release(ctx.store, "2026M09") == {"np_ytd": None, "np_m": None}
    assert res.status == "degraded"
    assert any("нарастающий итог не равен" in r and "чистая прибыль: с начала года" in r for r in res.reasons)
    # правит только человек: ручной ввод принимается сам
    record.record_ras(ctx.store, "2026M09", {"np_ytd": 1501.8, "np_m": 169.1}, "пресс-релиз РСБУ 9М26, с. 3")
    assert release_watch.watch(ctx, "2026M09")["complete"]
    assert _release(ctx.store, "2026M09") == {"np_ytd": pytest.approx(1501.8), "np_m": pytest.approx(169.1)}


def test_an_accepted_number_is_not_rewritten_by_the_automat(monkeypatch, tmp_path):
    """№ 5: число месяца, уже лежащее в ряду, автомат подтверждает, но не меняет: иное — расхождение."""
    ctx = _watch_ctx(monkeypatch, tmp_path)
    news.collect(ctx)
    held = ctx.store.read_state("news/items.json")
    for item in held["items"].values():                                # два вида согласно называют другое число
        item["text"] = item["text"].replace("1 трлн 332,7", "1 трлн 340,0").replace("1 332,7", "1 340,0")
    ctx.store.write_state("news/items.json", held)
    ctx.store.upsert("cbr.f102.ni_ytd", [Point("2026M08", 1340.0, "2026-09-09T06:00:00+00:00")])
    out = release_watch.watch(ctx, "2026M08")
    assert "np_ytd" in out["mismatches"] and "np_ytd" not in out["new"]
    assert ctx.store.load("ras.release.np_ytd").value_as_of("2026M08") == pytest.approx(1332.7)
    rows = ctx.store.read_state("release_watch/2026M08.json")["mismatches"]["np_ytd"]
    assert rows[0] == {"kind": "accepted", "value": pytest.approx(1332.7), "ref": "ras.release.np_ytd"}
    assert release_watch.mismatch_flag(ctx.store, ["2026M08"])["raised"]
    record.record_ras(ctx.store, "2026M08", {"np_ytd": 1340.0}, "исправленный релиз")
    assert "np_ytd" in release_watch.watch(ctx, "2026M08")["new"]
    assert ctx.store.load("ras.release.np_ytd").value_as_of("2026M08") == pytest.approx(1340.0)


def test_every_release_metric_has_words():
    """П§0.2: у каждой метрики релиза есть название словами — ключи в строки для витрины не попадают."""
    words = {m: release_watch.metric_words(m) for m in release_watch.METRICS}
    assert all(w != m and "_" not in w for m, w in words.items()), words
    assert len(set(words.values())) == len(words)
    assert words["np_ytd"] == "чистая прибыль с начала года" and words["prov_m"] == "резервы за месяц"
    assert words["n1_0"] == "Н1.0" and words["cir_ytd"] == "CIR с начала года"
    assert set(release_watch.FLOWS) == set(release_watch.FLOW_WORDS)


def test_two_agreeing_pairs_are_a_mismatch_and_january_is_one_number(tmp_path):
    """№ 5: две группы по два вида — расхождение (выбирать нечем); в январе нарастающее и месяц — одно число."""
    cfg = {"tolerance": {"money": 0.15, "identity": 0.35}}
    rows = [{"kind": k, "value": v} for k, v in (("interfax", 1501.8), ("telegram:a", 1501.8),
                                                 ("kommersant", 1332.7), ("smartlab", 1332.7))]
    accepted, mismatches = release_watch.decide({"np_ytd": rows}, cfg)
    assert accepted == {} and mismatches["np_ytd"] == rows
    accepted, mismatches = release_watch.decide({"np_ytd": rows[:3]}, cfg)        # пара и одиночка — пара
    assert accepted["np_ytd"]["value"] == 1501.8 and not mismatches
    jan = {"np_m": {"value": 150.2, "kinds": ["interfax", "smartlab"]}}
    release_watch.derive_month(Store(tmp_path), "2027M01", jan)
    assert jan["np_ytd"] == {"value": 150.2, "kinds": ["calc"], "manual": False}
    assert release_watch.release_days("2026M12") == (date(2026, 12, 31), date(2027, 1, 31))


def test_manual_record_is_accepted_and_identity_checked(monkeypatch, tmp_path):
    ctx = _watch_ctx(monkeypatch, tmp_path, today=date(2026, 10, 9))
    record.record_ras(ctx.store, "2026M09", record.parse_pairs(["np_ytd=1500,0", "np_m=167,3", "roe_ytd=22,8%"]),
                      "пресс-релиз РСБУ 9М26, с. 3")
    out = release_watch.watch(ctx, "2026M09")
    acc = ctx.store.read_state("release_watch/2026M09.json")["accepted"]
    assert acc["np_ytd"]["kinds"] == ["manual"] and acc["roe_ytd"]["value"] == pytest.approx(0.228)
    assert ctx.store.load("ras.release.np_ytd").point_as_of("2026M09").status == "manual"
    assert out["identity_failures"] == []                  # 1332,7 + 167,3 = 1500,0
    record.record_ras(ctx.store, "2026M09", {"np_m": 150.0}, "опечатка")
    assert release_watch.watch(ctx, "2026M09")["identity_failures"]


def test_record_value_rules():
    assert record.parse_value("22,9%", share=True) == pytest.approx(0.229)
    assert record.parse_value("0.013", share=True) == 0.013
    with pytest.raises(record.RecordError):
        record.parse_value("1.4", share=True)                  # проценты без «%»
    with pytest.raises(record.RecordError):
        record.parse_value("5%", share=False)
    with pytest.raises(record.RecordError):
        record.parse_pairs(["eps=1"])


# Текст релиза 9М одного вида (сочинён, как NINE_COMMON в tests/test_ind_news.py): потоки P&L нарастающим и за месяц.
NINE_ONE_KIND = ("INTERFAX.RU - За девять месяцев 2026 года банк по РСБУ увеличил чистую прибыль на 18,2% (в годовом "
                 "выражении) — до 1 трлн 501,8 млрд рублей, в сентябре — на 7,3% — до 169,1 млрд. "
                 "Чистые процентные доходы по итогам девяти месяцев прибавили 24,1% и дошли до 2 трлн 768,5 млрд "
                 "рублей; в сентябре — плюс 19,4%, до 327,3 млрд. "
                 "Чистый комиссионный доход за девять месяцев составил 534,6 млрд рублей (+1,2% г/г), в сентябре — "
                 "58,7 млрд. Расходы на резервы с начала года, за девять месяцев, составили 580,7 млрд рублей и "
                 "48,4 млрд рублей за сентябрь. Операционные расходы банка за девять месяцев выросли до 915,1 млрд "
                 "рублей, в сентябре — до 108,4 млрд (+4,2% г/г).")


def _one_kind_ctx(monkeypatch, tmp_path, texts: dict[str, str], today=date(2026, 10, 9)):
    """Состояние с историей по август 2026 года и текстами релиза одного вида (Интерфакс)."""
    ctx = _watch_ctx(monkeypatch, tmp_path, today=today)
    ctx.store.write_state("news/items.json", {"pdf": {}, "items": {
        url: {"kind": "interfax", "topic": "ras", "published": f"{today.isoformat()}T10:05:00+03:00", "text": text}
        for url, text in texts.items()}})
    return ctx


def test_a_flow_of_one_kind_is_accepted_when_the_running_total_adds_up(monkeypatch, tmp_path):
    """Релиз 9М26 — только Интерфакс (как 09.10.2026): нарастающее и месяц каждого потока сходятся с августом ряда
    (2 441,3 + 327,3 = 2 768,6 ≈ 2 768,5 в допуске приёма 0,15) — поток принят видами [interfax, identity] и записан в
    ряд."""
    ctx = _one_kind_ctx(monkeypatch, tmp_path, {"u9": NINE_ONE_KIND})
    out = release_watch.watch(ctx, "2026M09")
    acc = ctx.store.read_state("release_watch/2026M09.json")["accepted"]
    want = {"np_ytd": 1501.8, "np_m": 169.1, "nii_ytd": 2768.5, "nii_m": 327.3, "fee_ytd": 534.6, "fee_m": 58.7,
            "prov_ytd": -580.7, "prov_m": -48.4, "opex_ytd": -915.1, "opex_m": -108.4}
    for k, v in want.items():
        assert acc[k]["value"] == pytest.approx(v) and acc[k]["kinds"] == ["interfax", "identity"], k
        assert ctx.store.load(f"ras.release.{k}").value_as_of("2026M09") == pytest.approx(v), k
    assert sorted(out["accepted_by_identity"]) == sorted(want)
    assert out["month_accepted"] and not out["mismatches"] and not out["identity_failures"]
    assert ctx.store.load("ras.release.nii_m").point_as_of("2026M09").source == "release:interfax+identity"


def test_one_kind_is_not_enough_without_the_identity(monkeypatch, tmp_path):
    """Тождество не сошлось (месяц ЧПД 317,3 вместо 327,3), один вид назвал два числа, январь (нарастающее и месяц —
    одно число) — поток одного вида не принимается; расхождения и провала тождества нет: одиночный вид спорить не с кем."""
    wrong = NINE_ONE_KIND.replace("до 327,3 млрд", "до 317,3 млрд")
    ctx = _one_kind_ctx(monkeypatch, tmp_path, {"u9": wrong})
    out = release_watch.watch(ctx, "2026M09")
    acc = ctx.store.read_state("release_watch/2026M09.json")["accepted"]
    assert "nii_ytd" not in acc and "nii_m" not in acc and acc["fee_m"]["kinds"] == ["interfax", "identity"]
    assert not out["mismatches"] and not out["identity_failures"]
    ctx = _one_kind_ctx(monkeypatch, tmp_path / "two", {"a": NINE_ONE_KIND, "b": wrong})
    release_watch.watch(ctx, "2026M09")
    acc = ctx.store.read_state("release_watch/2026M09.json")["accepted"]
    assert "nii_m" not in acc and acc["np_m"]["kinds"] == ["interfax", "identity"]
    jan = {"np_ytd": [{"kind": "interfax", "value": 150.2}], "np_m": [{"kind": "interfax", "value": 150.2}]}
    accepted: dict = {}
    assert release_watch.accept_by_identity(Store(tmp_path / "jan"), "2027M01", jan, accepted, {}, {}) == []
    assert accepted == {}


def test_one_kind_identity_tolerance_is_the_rounding_of_three_numbers(tmp_path):
    """DECISIONS 14.7: приём одного вида тождеством — в допуске `identity_accept` 0,15 млрд ₽ (три числа с точностью
    0,1 — ошибка округления не больше 0,15): невязка 0,1 — приём, 0,3 (число без десятых: «542» вместо 542,3) — нет.
    Общая проверка тождества принятых чисел — прежняя, `identity` 0,35."""
    cfg = config.sources()["release_watch"]
    assert cfg["tolerance"]["identity_accept"] == 0.15 and cfg["tolerance"]["identity"] == 0.35
    store = Store(tmp_path)
    store.upsert("ras.release.nii_ytd", [Point("2026M08", 2441.3, "2026-09-09T07:00:00+00:00")], basis="ras",
                 unit="RUB bn")

    def one_kind(month_value: float, conf: dict) -> list[str]:
        cands = {"nii_ytd": [{"kind": "interfax", "value": 2768.5}],
                 "nii_m": [{"kind": "interfax", "value": month_value}]}
        return release_watch.accept_by_identity(store, "2026M09", cands, {}, {}, conf)

    assert one_kind(327.3, cfg) == ["nii_ytd", "nii_m"]                       # 2 441,3 + 327,3 − 2 768,5 = 0,1
    assert one_kind(327.5, cfg) == [] and one_kind(327.5, {}) == []           # 0,3: в прежнем допуске 0,35 прошло бы
    assert one_kind(327.5, {"tolerance": {"identity_accept": 0.35}}) == ["nii_ytd", "nii_m"]
    assert one_kind(327.5, {"tolerance": {"identity": 0.15, "identity_accept": 0.35}}) == ["nii_ytd", "nii_m"]
    both = {"nii_ytd": {"value": 2768.5, "kinds": ["interfax", "pdf"]},
            "nii_m": {"value": 327.5, "kinds": ["interfax", "pdf"]}}
    assert release_watch.identity_failures(store, "2026M09", both, cfg) == {}  # два вида: общая проверка — 0,35
    both["nii_m"]["value"] = 327.7
    assert list(release_watch.identity_failures(store, "2026M09", both, cfg)) == ["nii"]
