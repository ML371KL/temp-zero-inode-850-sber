"""Новостные ленты: архив дня, статьи, превью каналов, RSS изданий, ссылки на PDF, числа релиза РСБУ,
решения о дивидендах. Фикстуры — сочинённые тексты (B5, W3b): от источника — разметка и схема чисел,
слова свои; с архивом настоящих текстов их сверяет тест с меткой `archive`."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from indicators import config, news
from indicators.sources import Context
from indicators.store import Store
from tests.support_ind import (FIX, NEWS_COMMON_RUN_LIMIT, FakeGetter, longest_common_run, longest_common_runs,
                               plain_text, python_texts, real_news_corpus, use_fixtures)

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)


def _t(name):
    return (FIX / "news" / name).read_text(encoding="utf-8")


def test_interfax_listing_and_relevance(monkeypatch, tmp_path):
    use_fixtures(monkeypatch, tmp_path)
    cfg = config.sources()["news"]
    base = "https://www.interfax.ru/business/news/2026/09/09/"
    items = news.interfax_listing(_t("interfax_day_2026-09-09.html"), base)
    assert sorted(i["url"].rsplit("/", 1)[-1] for i in items) == ["9000101", "9000102", "9000103"]
    rel = [i for i in items if news.relevant(i["title"], cfg["issuer_keywords"], cfg["ras_require"])]
    assert sorted(i["url"].rsplit("/", 1)[-1] for i in rel) == ["9000101", "9000103"]
    assert not news.relevant("Сбережения граждан растут по РСБУ", cfg["issuer_keywords"], cfg["ras_require"])
    assert news.relevant("«Сбер» нарастил прибыль по РСБУ", cfg["issuer_keywords"], cfg["ras_require"])


EXPECTED = {
    "np_ytd": 1332.7, "np_m": 169.1, "roe_ytd": 0.229, "roe_m": 0.231, "prov_ytd": -532.3, "prov_m": -107.8,
    "cor_ytd": 0.014, "cor_m": 0.017, "nii_ytd": 2441.3, "nii_m": 325.2, "fee_ytd": 475.9, "fee_m": 59.8,
    "opex_ytd": -806.7, "opex_m": -104.0, "cir_ytd": 0.257, "cap_base": 6247.0, "cap_main": 6397.0,
    "cap_total": 8105.0, "n1_1": 0.101, "n1_2": 0.104, "n1_0": 0.131,
}


def test_interfax_article_numbers():
    doc = _t("interfax_9000101.html")
    assert news.meta(doc, "article:published_time") == "2026-09-09T09:54+03:00"
    text = news.article_text(doc)
    assert text.startswith("INTERFAX.RU")
    got = news.extract_release_numbers(text, month="2026M08")
    for k, v in EXPECTED.items():
        assert got[k][0] == pytest.approx(v), k


def test_telegram_previews():
    posts = news.telegram_posts(_t("tg_markettwits.html"))
    assert [p["post"] for p in posts] == ["markettwits/900001", "markettwits/900002"]
    assert posts[0]["time"] == "2026-09-09T06:50:41+00:00"
    h = news.extract_headline(posts[0]["text"])
    assert h["np_ytd"][0] == pytest.approx(1332.7) and h["np_m"][0] == pytest.approx(169.1)
    sfi = news.telegram_posts(_t("tg_SberForInvestors.html"))[0]["text"]
    both = {**news.extract_headline(sfi, month="2026M08"), **news.extract_release_numbers(sfi, month="2026M08")}
    assert both["np_ytd"][0] == pytest.approx(1332.7) and both["np_m"][0] == pytest.approx(169.1)


def test_release_month_reads_period_phrases_not_substrings():
    """№ 5: месяц текста — по фразам периода целыми словами; «332,7 млрд» — не «7 м», «марте» — не май."""
    interfax = news.article_text(_t("interfax_9000101.html"))
    posts = [p["text"] for name in ("tg_markettwits.html", "tg_SberForInvestors.html")
             for p in news.telegram_posts(_t(name))[:1]]
    for text in [interfax, news.page_text(_t("kommersant_doc.html")), *posts]:
        assert news.release_month(text) == 8, text[:60]
    assert news.release_month("Прибыль выросла до 1 332,7 млрд рублей.") is None          # нет фразы периода
    assert news.release_month("В мае банк заработал 150,2 млрд рублей по РСБУ.") == 5
    assert news.release_month("В марте банк заработал 150,2 млрд рублей по РСБУ.") == 3
    assert news.release_month("Мартовский релиз: за два месяца прибыль составила 300 млрд.") == 2
    assert news.release_month("За январь-сентябрь 2026 года прибыль выросла, в августе была ниже.") == 9
    assert news.release_month("По итогам первого полугодия банк заработал 859,3 млрд.") == 6
    assert news.release_month("Банк в 1кв получил 404,5 млрд руб. чистой прибыли по РСБУ.") == 3
    assert news.release_month("Чистая прибыль по РСБУ за 2025 год составила 1 трлн 690,1 млрд рублей.") == 12
    assert news.release_month("Активы за год выросли на 12%.") is None                    # «за год» — год к году
    assert [news.cumulative_months(s) for s in ("за 12 месяцев", "по итогам восьми месяцев", "в январе-мае")] \
        == [12, 8, 5]


def test_numbers_of_another_month_are_not_taken():
    """№ 5: текст релиза 8М не даёт чисел июлю и сентябрю; нарастающее — только за столько же месяцев."""
    text = news.article_text(_t("interfax_9000101.html"))
    assert set(EXPECTED) <= set(news.extract_release_numbers(text, month="2026M08"))
    for other in ("2026M07", "2026M09", "2026M05"):
        # без даты во фразе остаются только остатки («общий капитал … до …») — их отсекает месяц текста
        got = news.extract_release_numbers(text, month=other)
        assert not [k for k in got if k.endswith(("_ytd", "_m"))], (other, sorted(got))
        assert not {"cap_base", "cap_main"} & set(got)                 # «на 1 сентября» — не их дата
    post = news.telegram_posts(_t("tg_markettwits.html"))[0]["text"]
    assert set(news.extract_headline(post, month="2026M08")) == {"np_ytd", "np_m"}
    assert news.extract_headline(post, month="2026M07") == {} and news.extract_headline(post, month="2026M09") == {}
    # релиз 9М с числом прошлого месяца: сентябрю — своё, августу — ничего
    nine = ("За январь-сентябрь 2026 года банк нарастил чистую прибыль по РСБУ на 18% - до 1 трлн 501,8 млрд рублей. "
            "Чистая прибыль банка за сентябрь составила 169,1 млрд рублей против 160,0 млрд рублей в августе.")
    got = news.extract_release_numbers(nine, month="2026M09")
    assert got["np_ytd"][0] == pytest.approx(1501.8) and got["np_m"][0] == pytest.approx(169.1)
    assert news.extract_release_numbers(nine, month="2026M08") == {}


def test_pdf_links_text_and_moment_of_an_outlet_page(monkeypatch, tmp_path):
    use_fixtures(monkeypatch, tmp_path)
    pats = config.sources()["news"]["pdf_patterns"]
    doc = _t("kommersant_doc.html")
    assert news.pdf_links(doc, pats) == ["https://www.sberbank.com/common/img/uploaded/files/info/2026_rsbu/year/"
                                         "ras_for_8months2026_x0x0x0x0_ru.pdf"]
    assert news.pdf_links("<a href='https://example.com/x.pdf'>x</a>", pats) == []
    assert news.page_text(doc).startswith("Сбербанк по итогам восьми месяцев")          # абзацы текста, без боковины
    assert news.page_published(doc) == "2026-09-09T10:14:57+03:00"
    ld = ('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-07-09T10:18:13+03:00",'
          '"articleBody":"«Сбер» нарастил чистую прибыль по РСБУ."}</script><div>меню</div>')
    assert news.page_text(ld) == "«Сбер» нарастил чистую прибыль по РСБУ."
    assert news.page_published(ld) == "2026-07-09T10:18:13+03:00"
    sl = '<ul><li class="date">30 сентября 2026, 22:36</li></ul>'
    assert news.page_published(sl) == "2026-09-30T22:36:00+03:00"


def test_rss_items_unwrap_cdata_and_dates():
    items = news.rss_items(_t("tass_rss.xml"))
    assert [i["url"] for i in items] == ["https://tass.ru/ekonomika/9000301", "https://tass.ru/obschestvo/9000302"]
    assert items[0]["published"] == "2026-06-30T12:15:00+03:00"
    assert "20 июля 2026" in items[0]["description"]


def test_coarse_trillions_are_not_taken():
    got = news.extract_release_numbers("Чистая прибыль по РСБУ за 8 месяцев выросла до 1,332 трлн рублей.")
    assert "np_ytd" not in got


def test_dividend_decisions_of_the_agm_and_the_board():
    agm = _t("interfax_9000201.html")
    text = news.meta(agm, "og:title") + ". " + news.article_text(agm)
    got = news.extract_dividend_decision(text, published=news.meta(agm, "article:published_time"))
    assert got["status"] == "declared" and got["year"] == 2025
    assert got["dps"] == pytest.approx(37.64) and got["dps_preferred"] == pytest.approx(37.64)
    assert got["record_date"] == "2026-07-20" and got["decided_date"] == "2026-06-30"
    board = _t("interfax_9000202.html")
    rec = news.extract_dividend_decision(news.meta(board, "og:title") + ". " + news.article_text(board),
                                         published=news.meta(board, "article:published_time"))
    assert rec["status"] == "recommended" and rec["dps"] == pytest.approx(37.64)
    assert rec["record_date"] == "2026-07-20" and rec["decided_date"] == "2026-04-29"
    tass = news.rss_items(_t("tass_rss.xml"))[0]
    t = news.extract_dividend_decision(f"{tass['title']}. {tass['description']}", published=tass["published"])
    assert (t["status"], t["year"], t["dps"], t["record_date"]) == ("declared", 2025, pytest.approx(37.64), "2026-07-20")
    # прошлогодняя выплата дальше по тексту и решение в процентах прибыли DPS не дают
    assert news.extract_dividend_decision("По итогам 2024 года банк выплатил 34,84 рубля на акцию.") is None
    assert news.extract_dividend_decision("Акционеры утвердили дивиденды в размере 25% прибыли за 2025 год.") is None
    split = news.extract_dividend_decision(
        "Акционеры утвердили дивиденды за 2025 год: 10,5 руб. на обыкновенную акцию и 12 руб. на "
        "привилегированную акцию. Отсечка — 20.07.2026.", published="2026-06-30T12:00:00+03:00")
    assert split["dps"] == pytest.approx(10.5) and split["dps_preferred"] == pytest.approx(12.0)
    assert split["record_date"] == "2026-07-20"


def test_days_to_scan_catch_up_missed_days():
    cfg = {"lookback_days": 1, "catchup_days": 4}
    today = date(2026, 7, 6)
    got = news.days_to_scan(today, cfg, done=["2026-07-03"])
    assert got == [date(2026, 7, 6), date(2026, 7, 5), date(2026, 7, 4), date(2026, 7, 2)]
    assert news.days_to_scan(today, {"lookback_days": 1}, done=[]) == [date(2026, 7, 6), date(2026, 7, 5)]


def test_collect_keeps_items_and_raw(monkeypatch, tmp_path):
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    ctx = Context(store=store, today=date(2026, 9, 9), company=config.company(), cfg=config.sources(),
                  getter=FakeGetter())
    r = news.collect(ctx)
    got = store.read_state("news/items.json")
    items = got["items"]
    kinds = sorted(v["kind"] for v in items.values())
    assert kinds == ["interfax", "kommersant", "tass", "telegram:SberForInvestors", "telegram:markettwits"], r.reasons
    # постороннее сообщение ленты (не о РСБУ эмитента) не взято
    assert not any("900002" in u for u in items)
    assert any("9000103" in x for x in r.reasons)                  # вторая статья не скачалась — деградация
    assert any("cbrstocks" in x for x in r.reasons) and any("vedomosti" in x for x in r.reasons)
    k = items["https://www.kommersant.ru/doc/9000401"]
    assert k["topic"] == "ras" and k["published"] == "2026-09-09T10:14:57+03:00"
    assert any("x0x0x0x0" in u for u in got["pdf"])
    tass = items["https://tass.ru/ekonomika/9000301"]
    assert tass["topic"] == "dividend" and tass["text"].startswith("Акционеры Сбербанка")
    assert store.raw_days("news")
    # статья 9000103 не скачалась — дни архива не пройдены и догоняются в следующий такт
    assert got["days"]["interfax"] == []
    g = FakeGetter(overrides={"business/9000103": (FIX / "news" / "interfax_9000101.html").read_bytes()})
    news.collect(Context(store=store, today=date(2026, 9, 9), company=config.company(), cfg=config.sources(),
                         getter=g))
    days = store.read_state("news/items.json")["days"]["interfax"]
    assert "2026-09-08" in days and "2026-09-02" in days and "2026-09-09" not in days     # сегодня не закончился


def test_channel_kind_override_joins_aggregators(monkeypatch, tmp_path):
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    cfg = dict(config.sources())
    cfg["news"] = {**cfg["news"], "interfax_day": None, "outlets": [], "smartlab_news": None,
                   "telegram_channels": [{"name": "cbrstocks", "require": ["РСБУ"], "kind": "telegram:markettwits"}]}
    g = FakeGetter(overrides={"t.me/s/cbrstocks": (FIX / "news" / "tg_markettwits.html").read_bytes()})
    ctx = Context(store=store, today=date(2026, 9, 9), company=config.company(), cfg=cfg, getter=g)
    news.collect(ctx)
    items = store.read_state("news/items.json")["items"]
    assert {v["kind"] for v in items.values()} == {"telegram:markettwits"}


# Синтетический текст в строении январского релиза: период — в первом предложении, ниже — «прибыль за
# 4 месяца» о нормативах (так устроены январские статьи; сам текст сочинён, чисел эмитента в нём нет).
JANUARY = ("INTERFAX.RU - В январе 2027 года чистая прибыль банка по РСБУ выросла на 12,0% - до 150,2 млрд рублей, "
           "год назад было 134,1 млрд рублей, сообщил банк. "
           "За январь чистый процентный доход прибавил 9,1%, до 310,4 млрд рублей. "
           "На 1 февраля достаточность базового капитала - 10,3%; нормативы посчитаны без прибыли "
           "за 4 месяца, которую аудитор ещё не подтвердил.")


def test_the_month_of_a_text_is_read_from_its_first_period_sentence():
    """№ 94: месяц текста решает первое предложение с фразой периода — «прибыль за 4 месяца» ниже по
    тексту январский релиз апрельским не делает; заголовок без периода пропускается."""
    assert news.period_months(JANUARY) == (1,) and news.release_month(JANUARY) == 1
    assert news.cumulative_months(news._norm(JANUARY)) == 4                 # по всему тексту вышел бы апрель
    got = news.extract_release_numbers(JANUARY, month="2027M01")
    assert got["np_m"][0] == pytest.approx(150.2)
    assert news.extract_release_numbers(JANUARY, month="2027M04") == {}
    titled = "Банк раскрыл результаты по РСБУ.\nЗа 8 месяцев он заработал 1 332,7 млрд рублей. В июле — 168,4 млрд."
    assert news.period_months(titled) == (8,)
    # в одном предложении и слово месяца, и нарастающий период: названы оба, главный — нарастающий
    both = "В январе банк заработал 150,2 млрд рублей по РСБУ; нормативы — без учёта прибыли за 4 месяца."
    assert news.period_months(both) == (4, 1) and news.release_month(both) == 4
    assert news.period_months("За январь-сентябрь прибыль выросла, в августе была ниже.") == (9, 8)
    assert news.period_months("Банк в марте нарастил портфель по РСБУ (после снижения в феврале).") == (3,)
    assert news.period_months("Банк в августе заработал 169,1 млрд рублей. За 8 месяцев — 1 332,7 млрд.") == (8,)
    assert news.period_months("Прибыль выросла. Капитал тоже.") == () and news.period_months("") == ()


def test_the_january_release_text_is_a_candidate_of_january(monkeypatch, tmp_path):
    """№ 94: статья о релизе за январь — кандидат января у дозора (и только января)."""
    from indicators import release_watch

    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    both = "В январе банк заработал 150,2 млрд рублей по РСБУ; нормативы — без учёта прибыли за 4 месяца."
    store.write_state("news/items.json", {"pdf": {}, "items": {
        "u1": {"kind": "interfax", "topic": "ras", "published": "2027-02-09T10:05:00+03:00", "text": JANUARY},
        "u2": {"kind": "smartlab", "topic": "ras", "published": "2027-02-09T10:20:00+03:00", "text": both}}})
    ctx = Context(store=store, today=date(2027, 2, 9), company=config.company(), cfg=config.sources(),
                  getter=FakeGetter())
    cands = release_watch.gather(ctx, "2027M01")
    assert {(c["kind"], c["value"]) for c in cands["np_m"]} == {("interfax", 150.2), ("smartlab", 150.2)}
    assert release_watch.gather(ctx, "2026M12") == {}                       # день публикации — не окно декабря
    out = release_watch.watch(ctx, "2027M01")
    assert {"np_m", "np_ytd"} <= set(out["new"]) and not out["mismatches"]   # в январе НМ и месяц — одно число
    assert store.load("ras.release.np_ytd").value_as_of("2027M01") == pytest.approx(150.2)
    # тот же текст, опубликованный в мае, апрелю чисел не даёт: фраз апреля в нём нет
    store.write_state("news/items.json", {"pdf": {}, "items": {
        "u2": {"kind": "smartlab", "topic": "ras", "published": "2027-05-12T10:20:00+03:00", "text": both}}})
    assert release_watch.gather(Context(store=store, today=date(2027, 5, 12), company=config.company(),
                                        cfg=config.sources(), getter=FakeGetter()), "2027M04") == {}


def test_the_annual_release_gives_the_year_to_date_profit():
    """Релиз за декабрь: между «по РСБУ» и числом стоит оговорка про СПОД (события после отчётной даты) —
    прибыль года берётся; год фразы — год релиза, год из хвоста сравнения его не отбрасывает (текст сочинён)."""
    text = ("INTERFAX.RU - В 2026 году банк поднял чистую прибыль по РСБУ без учета поправок на события после "
            "отчетной даты (СПОД) на 6,0% - до рекордных 1795,9 млрд рублей, годом ранее было 1700,0 млрд рублей "
            "прибыли за 2025 год, сообщил банк. В декабре чистая прибыль выросла на 5,0% - до 132,2 млрд рублей.")
    assert news.period_months(text) == (12,)
    got = news.extract_release_numbers(text, month="2026M12")
    assert got["np_ytd"][0] == pytest.approx(1795.9) and got["np_m"][0] == pytest.approx(132.2)
    assert "np_ytd" not in news.extract_release_numbers(text, month="2025M12")       # год фразы — другой
    plain = "За 8 месяцев банк поднял чистую прибыль по РСБУ на 10,1% - до 1 332,7 млрд рублей."
    assert news.extract_release_numbers(plain, month="2026M08")["np_ytd"][0] == pytest.approx(1332.7)


def test_base_and_main_capital_in_the_long_and_the_short_phrase():
    """Базовый и основной капитал читаются из трёх записей пары: «X трлн рублей и Y трлн рублей»,
    «X трлн руб. и Y трлн руб.» и «X и Y трлн рублей» (синтетические фразы; вторая и третья до W3b не
    читались, вторая стоит в двух статьях архива этапа 1 из 160). Грубое число (меньше трёх знаков
    после запятой) не берётся."""
    for phrase in ("составили 6,111 трлн рублей и 6,222 трлн рублей соответственно",
                   "составили 6,111 трлн руб. и 6,222 трлн руб. соответственно",
                   "составили 6,111 и 6,222 трлн рублей"):
        got = news.extract_release_numbers(f"Базовый и основной капитал на 1 мая {phrase}.", month="2026M04")
        assert (got["cap_base"][0], got["cap_main"][0]) == (pytest.approx(6111.0), pytest.approx(6222.0)), phrase
        assert news.extract_release_numbers(f"Базовый и основной капитал на 1 мая {phrase}.", month="2026M05") == {}
    coarse = news.extract_release_numbers("Базовый и основной капитал составили 6,1 и 6,2 трлн рублей.")
    assert "cap_base" not in coarse and "cap_main" not in coarse
    ratios = news.extract_release_numbers("Нормативы по базовому и основному капиталу составили 10,1 и 10,4% "
                                          "при общем капитале 8,105 трлн рублей.")
    assert "cap_base" not in ratios and "cap_main" not in ratios        # доли рядом с «трлн» — не капитал


# Релиз 9М в общих оборотах месяца потока (текст сочинён; числа — схема релиза за сентябрь 2026 года): нарастающее и
# месяц в одной фразе, «Z млрд рублей за <месяц>», процент без «на», скобка после процента.
NINE_COMMON = ("INTERFAX.RU - За девять месяцев 2026 года банк по РСБУ увеличил чистую прибыль на 18,2% (в годовом "
               "выражении) — до 1 трлн 501,8 млрд рублей, в сентябре — на 7,3% — до 169,1 млрд. "
               "Чистые процентные доходы по итогам девяти месяцев прибавили 24,1% и дошли до 2 трлн 768,5 млрд "
               "рублей; в сентябре — плюс 19,4%, до 327,3 млрд. "
               "Чистый комиссионный доход за девять месяцев составил 534,6 млрд рублей (+1,2% г/г), в сентябре — "
               "58,7 млрд. Расходы на резервы с начала года, за девять месяцев, составили 580,7 млрд рублей и "
               "48,4 млрд рублей за сентябрь. Операционные расходы банка за девять месяцев выросли до 915,1 млрд "
               "рублей, в сентябре — до 108,4 млрд (+4,2% г/г).")
NINE_EXPECTED = {"np_ytd": 1501.8, "np_m": 169.1, "nii_ytd": 2768.5, "nii_m": 327.3, "fee_ytd": 534.6, "fee_m": 58.7,
                 "prov_ytd": -580.7, "prov_m": -48.4, "opex_ytd": -915.1, "opex_m": -108.4}


def test_monthly_values_of_the_flows_in_the_common_phrasings():
    """Месяц потока — и из общих оборотов «за N месяцев … — до X, в <месяце> — на Y% — до Z», «Z млрд рублей за
    <месяц>», «в <месяце> — Z млрд»; «2 трлн 768,5 млрд» — одно число. Только для своего месяца релиза."""
    got = news.extract_release_numbers(NINE_COMMON, month="2026M09")
    assert {k: got[k][0] for k in NINE_EXPECTED} == pytest.approx(NINE_EXPECTED)
    assert news.extract_release_numbers(NINE_COMMON, month="2026M08") == {}
    last_year = news.extract_release_numbers(NINE_COMMON.replace("2026 года", "2025 года"), month="2026M09")
    assert "np_ytd" not in last_year and "np_m" not in last_year            # год фразы — не год релиза


def test_a_number_of_another_flow_or_another_year_is_not_the_month():
    """Число месяца принадлежит потоку, названному перед ним; сравнение («против …», месяц прошлого года) — не
    месяц релиза; «этот показатель» — поток прошлого предложения, а не всегда ЧКД (тексты сочинены)."""
    two = "В сентябре банк заработал 169,1 млрд рублей прибыли, а чистый процентный доход банка вырос на 19,4%."
    got = news.extract_release_numbers(two, month="2026M09")
    assert got["np_m"][0] == pytest.approx(169.1) and "nii_m" not in got
    years = ("В сентябре 2025 года чистые процентные доходы банка — 274,1 млрд рублей, в сентябре 2026 года — "
             "327,3 млрд рублей.")
    assert news.extract_release_numbers(years, month="2026M09")["nii_m"][0] == pytest.approx(327.3)
    nine = "Чистая прибыль банка за сентябрь составила 169,1 млрд рублей против 160,0 млрд рублей в августе."
    assert news.extract_release_numbers(nine, month="2026M08") == {}
    this = ("Расходы на резервы за восемь месяцев дошли до 532,3 млрд руб. (годом ранее — 329,1). "
            "В августе показатель вырос в 1,7 раза — до 107,8 млрд руб. против 63,0 млрд руб. годом ранее.")
    got = news.extract_release_numbers(this, month="2026M08")
    assert got["prov_m"][0] == pytest.approx(-107.8) and "fee_m" not in got


# Посты канала эмитента о консенсусе (тексты сочинены): ожидание раньше прибыли; прогноз — подлежащее.
CONSENSUS_FUTURE = ("По прогнозу аналитиков, чистая прибыль Группы за III квартал 2026 г. достигнет 511 млрд ₽ "
                    "(ROE около 22%).")
CONSENSUS_STATED = "Консенсус-прогноз аналитиков по чистой прибыли за 3 квартал 2026 года — 511 млрд ₽ (МСФО)."


def test_the_consensus_of_the_quarter_from_a_post_of_the_issuer():
    """Консенсус ЧП квартала из поста канала эмитента: ожидание раньше прибыли, квартал с годом, число после глагола
    будущего или связки; «консенсус-прогноз … по чистой прибыли … составил X» — тоже консенсус. Пост о результате
    отчёта («составила», «как и ожидали») консенсусом не считается."""
    for text in (CONSENSUS_FUTURE, CONSENSUS_STATED,
                 CONSENSUS_STATED.replace("— 511 млрд ₽ (МСФО)", "по МСФО составил 511 млрд ₽")):
        got = news.extract_consensus(text)
        assert got is not None and (got["period"], got["value"]) == ("2026Q3", 511.0), text
    assert news.extract_consensus("Чистая прибыль Группы по МСФО за III квартал 2026 г. составила 511,2 млрд ₽ — "
                                  "немного выше консенсуса.") is None
    assert news.extract_consensus("Как и ожидали аналитики, чистая прибыль Группы за III квартал 2026 г. составила "
                                  "511,2 млрд ₽.") is None
    assert news.extract_consensus("Аналитики ожидают роста кредитования в 2026 году.") is None


def _channel_page(text: str, post: str, moment: str) -> bytes:
    """Превью канала из одного сочинённого поста (разметка `t.me/s/<канал>`)."""
    return ('<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" '
            f'data-post="{post}"><div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>'
            f'<time datetime="{moment}" class="time">10:15</time></div></div>').encode("utf-8")


def test_the_consensus_post_goes_to_the_consensus_series_and_the_manual_record_wins(monkeypatch, tmp_path):
    """Сборщик новостей пишет консенсус поста канала с `consensus: true` в ряд `consensus.ni_q` (момент — публикация
    поста): эталон «консенсус» журнала и ретро. Повтор такта то же число второй раз не пишет; квартал с ручной
    записью (`record-consensus`) автомат не трогает."""
    from indicators import record

    cfg = dict(config.sources())
    channel = next(c for c in cfg["news"]["telegram_channels"] if isinstance(c, dict) and c.get("consensus"))
    cfg["news"] = {**cfg["news"], "interfax_day": None, "outlets": [], "smartlab_news": None,
                   "telegram_channels": [channel]}
    page = _channel_page(CONSENSUS_FUTURE, f"{channel['name']}/900021", "2026-10-26T07:15:00+00:00")
    for manual in (False, True):
        state = use_fixtures(monkeypatch, tmp_path / str(manual))
        store = Store(state)
        if manual:
            record.record_consensus(store, period="2026Q3", value="509", source="консенсус банка, ввод вручную")
        ctx = Context(store=store, today=date(2026, 10, 26), company=config.company(), cfg=cfg,
                      getter=FakeGetter(overrides={f"t.me/s/{channel['name']}": page}))
        for _ in range(2):
            res = news.collect(ctx)
        series = store.load("consensus.ni_q")
        points = [p for p in series.points if p.period == "2026Q3"]
        if manual:
            assert series.value_as_of("2026Q3") == 509.0 and len(points) == 1 and points[0].status == "manual"
            assert "consensus.ni_q" not in res.series
        else:
            assert series.value_as_of("2026Q3") == 511.0 and len(points) == 1
            assert points[0].fetched_at == "2026-10-26T07:15:00+00:00" and points[0].source.endswith("/900021")
            assert series.value_as_of("2026Q3", "2026-10-25") is None              # до поста консенсуса не знали


NEWS_FIXTURES = sorted(p.name for p in (FIX / "news").iterdir())


def test_the_excerpt_check_catches_a_copied_sentence_and_passes_a_reworded_one():
    """Сверка фикстур с настоящими текстами (W3b, P2) сама проверена: выписанное предложение ловится,
    в том числе под другой разметкой и с другим тире; пересказ своими словами с теми же числами — нет."""
    article = plain_text("<p>Банк за девять месяцев получил 1 234,5 млрд рублей прибыли — на 7% больше, "
                         "чем годом ранее.</p><p>Капитал вырос до 5,678 трлн рублей.</p>")
    corpus = {"статья": article}
    copied = plain_text("<div>Лента.<br/>Банк за девять месяцев получил 1 234,5 млрд рублей прибыли - на 7% "
                        "больше, чем годом ранее.</div>")
    size, piece, where = longest_common_run(copied, corpus)
    assert size >= 80 and where == "статья" and piece.startswith("Банк за девять месяцев")
    reworded = plain_text("<p>Прибыль банка с января по сентябрь — 1 234,5 млрд рублей, плюс 7% за год.</p>")
    assert longest_common_run(reworded, corpus)[0] < NEWS_COMMON_RUN_LIMIT
    both = longest_common_runs({"выписка": copied, "пересказ": reworded, "пусто": ""}, corpus)
    assert both["выписка"][0] == size and both["пересказ"][0] < NEWS_COMMON_RUN_LIMIT and both["пусто"] == (0, "", "")
    assert plain_text("<!-- пометка --><a href='https://example.org/x'>слово</a> https://example.org/y ёж") \
        == "слово еж"
    # тексты внутри тестов — тот же вопрос: строка и комментарий файла кода читаются с номером строки
    inline = python_texts(Path(__file__), shortest=NEWS_COMMON_RUN_LIMIT)
    assert any(text.startswith("INTERFAX.RU - В январе 2027 года") for text in inline.values())
    assert any(text.startswith("# Синтетический текст в строении январского релиза") for text in inline.values())


@pytest.mark.parametrize("name", NEWS_FIXTURES)
def test_every_news_fixture_says_it_is_synthetic(name):
    """Каждая фикстура новостей помечена в самом файле: сочинённый текст, а не сохранённая страница."""
    assert len(NEWS_FIXTURES) >= 10
    assert "<!-- синтетика:" in _t(name), name


@pytest.mark.archive
def test_news_fixtures_share_no_long_piece_with_the_real_texts():
    """W3b, P2: фикстуры новостей — сочинённые тексты, не сокращение и не пересказ статей и постов.
    Архив настоящих текстов этапа 1 (папка передачи) не содержит ни одного куска фикстуры длиной
    от `NEWS_COMMON_RUN_LIMIT` знаков; общее короче — термины и схема чисел, которую читает разбор.
    Тот же вопрос — к текстам внутри тестов и кода индикаторов (строки и комментарии); кусок без
    пробела — адрес или имя файла, а не текст."""
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root:
        pytest.skip("нужна папка передачи (BANK_HANDOFF_DIR)")
    corpus = real_news_corpus(Path(root))
    assert len(corpus) >= 500, f"архив настоящих текстов неполон: {len(corpus)} файлов"
    texts = {name: plain_text(_t(name)) for name in NEWS_FIXTURES}
    here = Path(__file__).resolve().parent
    code = [*sorted(here.glob("test_ind_*.py")), here / "support_ind.py",
            *sorted((here.parent / "indicators").glob("*.py"))]
    assert len(code) >= 30
    for path in code:
        texts.update(python_texts(path, shortest=NEWS_COMMON_RUN_LIMIT))
    assert len(texts) > len(NEWS_FIXTURES) + 100
    bad = [f"{name}: {size} знаков «{piece}» — {where}"
           for name, (size, piece, where) in longest_common_runs(texts, corpus).items()
           if size >= NEWS_COMMON_RUN_LIMIT and " " in piece.strip()]
    assert not bad, "тексты репозитория повторяют настоящие:\n" + "\n".join(bad)
