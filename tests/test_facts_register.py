"""Факты: дивиденды — публичные факты с публичным источником; формат записи с двумя источниками (MODEL §15, B3).

Размер на акцию, дата отсечки и дата решения — из решений годовых собраний акционеров: лист решений папки
передачи (документ реестра `dividends_public`) с источником каждого года — страница «Дивиденды» на сайте банка
и новость о решении. Остальные даты — по правилу от отсечки. Брокерский календарь в факты не идёт: сборщик
сверяет с ним размер, отсечку и последний день покупки и пишет итог словами (`broker_check`); выписок ответов
брокерского API и имён их полей в фактах и документах о фактах нет.

Источник записи — строка «<вид>: <что и откуда>, sha256 <префикс>», вид ∈ {tinvest, news, manual} (виды живого
реестра выпуска): брокерский календарь, новость о решении ГОСА (рекомендации СД), ручная запись по документу
эмитента. Запись `declared` — два источника РАЗНЫХ видов. В фактах формат несут `dividends.history[]`
(документ эмитента + новость о решении, дата решения ГОСА) и `register_seed[]` (объявленные на дату книги).
Проверка на самих фактах и сверка с приёмкой ядра.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
KINDS = ("tinvest", "news", "manual")
SOURCE_RE = re.compile(r"^(tinvest|news|manual): \S")
SHA_RE = re.compile(r"sha256 ([0-9a-f]{16})")
RECORD_AFTER_DECISION = (10, 20)     # дней от решения ГОСА до отсечки (политика п. 4.1, MODEL §15)
DIVIDENDS_DOC = "dividends_public"   # лист решений годовых собраний — документ реестра
# Имена полей и методов ответов брокерского API и пути к образцам ответов: в данных и документах о фактах их нет
# (решение ведущего P1). Слова собраны из частей: сам тест — не «имя поля» для общего теста гигиены.
API_WORDS = tuple(a + b for a, b in (
    ("dividend", "Net"), ("record", "Date"), ("lastBuy", "Date"), ("payment", "Date"), ("declared", "Date"),
    ("close", "Price"), ("yield", "Value"), ("Get", "Dividends"), ("Get", "AssetReports"), ("Get", "LastPrices"),
    ("Get", "ForecastBy"), ("Instruments", "Service"), ("tinvest_", "samples"), ("invest-public", "-api")))
PUBLIC_TEXTS = [FACTS / "SCHEMA.md", ROOT / "docs" / "FACTS.md"]


def load(name: str) -> dict:
    return json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))


def builder():
    spec = importlib.util.spec_from_file_location("build_facts_for_register", ROOT / "ops" / "tools" / "build_facts.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def plain(x):
    return x.get("v") if isinstance(x, dict) and "v" in x else x


def kinds(sources: list[str]) -> set[str]:
    return {s.split(":", 1)[0] for s in sources}


def records() -> list[tuple[str, dict]]:
    d = load("dividends")
    return [(f"history.{r['year']}", r) for r in d["history"]] + [(f"register_seed.{i}", r)
                                                                  for i, r in enumerate(d["register_seed"])]


def all_years() -> list[tuple[str, dict]]:
    d = load("dividends")
    return [(f"history_before.{r['year']}", r) for r in d["history_before"]] + [(f"history.{r['year']}", r)
                                                                               for r in d["history"]]


def test_sources_have_kind_and_document():
    registry = {x["sha256"][:16] for x in load("anchor")["documents"]}
    for where, r in records():
        src = r.get("sources")
        assert isinstance(src, list) and src and all(isinstance(s, str) for s in src), f"{where}: нет sources"
        for s in src:
            assert SOURCE_RE.match(s), f"{where}: источник без вида {KINDS}: {s[:60]}"
            shas = SHA_RE.findall(s)
            assert shas and set(shas) <= registry, f"{where}: источник без документа реестра: {s[:60]}"
        assert len(kinds(src)) == len(src), f"{where}: два источника одного вида — это один источник"


def test_decided_date_and_record_date_rule():
    """Дата решения есть у каждого года истории; отсечка — через 10–20 дней после решения собрания."""
    for where, r in all_years() + records():
        assert r.get("decided_date") or where.startswith("register_seed"), f"{where}: нет даты решения собрания"
        if not r.get("decided_date") or not r.get("record_date"):
            continue
        gap = (dt.date.fromisoformat(r["record_date"]) - dt.date.fromisoformat(r["decided_date"])).days
        lo, hi = RECORD_AFTER_DECISION
        assert lo <= gap <= hi, f"{where}: отсечка через {gap} дн. после решения"
        if "sources" in r:
            assert "news" in kinds(r["sources"]) or "manual" in kinds(r["sources"]), \
                f"{where}: дата решения без новости или ручной записи"


def test_declared_register_records_have_two_kinds():
    for i, r in enumerate(load("dividends")["register_seed"]):
        assert r["status"] in ("declared", "recommended", "paid"), i
        if r["status"] == "declared":
            assert len(kinds(r["sources"])) >= 2, f"register_seed.{i}: declared без второго вида источника"
            assert r["ex_date"] == r["record_date"], f"register_seed.{i}: экс-дата ≠ отсечке (T+1)"


def test_every_history_year_carries_the_issuer_document_and_the_news():
    """Каждый год истории теста DPS — образец записи с двумя источниками: документ эмитента + новость о решении."""
    for r in load("dividends")["history"]:
        assert kinds(r["sources"]) == {"manual", "news"}, r["year"]
        manual = next(s for s in r["sources"] if s.startswith("manual:"))
        assert "https://www.sberbank.com/" in manual and "страница «Дивиденды»" in manual, r["year"]
        news = next(s for s in r["sources"] if s.startswith("news:"))
        assert re.search(r"\d{2}\.\d{2}\.\d{4}", news) and "https://" in news and "решение ГОСА" in news, r["year"]


def test_dividend_numbers_come_from_the_public_decisions_sheet():
    """DPS каждого года (включая годы до политики и год без выплаты) ссылается на лист решений; лист в реестре
    назван словами для владельца (его название печатает витрина как источник истории дивидендов) и несёт адрес
    страницы эмитента."""
    doc = next(d for d in load("anchor")["documents"] if d["key"] == DIVIDENDS_DOC)
    assert doc["url"].startswith("https://www.sberbank.com/") and doc["file"].endswith("dividends_public.csv")
    title = doc["title"]
    assert "Решения годовых собраний акционеров" in title and "страница «Дивиденды»" in title
    for bad in ("API", "снимок", "токен", ".csv", ".json", "stage", "sha256", "лист"):
        assert bad not in title, f"название документа — словами для владельца, без «{bad}»: {title}"
    assert not re.search(r"\d{4}-\d{2}-\d{2}", title), "дата в названии — с точками, не ISO"
    for where, r in all_years():
        for k in ("dps_ordinary", "dps_preferred"):
            src = r[k].get("src") or ""
            assert SHA_RE.findall(src) == [doc["sha256"][:16]], f"{where}.{k}: источник — не лист решений: {src[:80]}"
            assert "решение годового собрания акционеров" in src, f"{where}.{k}: в источнике нет решения собрания"
    news = next(d for d in load("anchor")["documents"] if d["key"].startswith("ifx_agm_"))
    assert re.match(r"^Интерфакс \d{2}\.\d{2}\.\d{4}: «", news["title"]), news["title"]


def test_no_broker_api_fields_or_sample_paths_in_facts_and_their_documents():
    """Решение ведущего P1: в данных и документах — ни имён полей и методов ответа брокерского API, ни путей к
    образцам ответов; брокерский календарь назван словами (сверка — `broker_check`)."""
    bad = []
    for path in sorted(FACTS.glob("*.json")) + PUBLIC_TEXTS:
        text = path.read_text(encoding="utf-8")
        bad += [f"{path.name}: {w}" for w in API_WORDS if w in text]
    assert not bad, "имена полей и методов брокерского API в фактах: " + ", ".join(bad)
    d = load("dividends")
    assert "сверены с брокерским календарём" in d["broker_check"] and "расхождений нет" in d["broker_check"]
    assert "10-й рабочий день после" in d["dates_rule"] and "T+1" in d["dates_rule"]
    for s in load("calendar")["sources"]:
        assert s["key"] not in ("tinvest_reports", "tinvest_divs"), "источник календаря назван методом API"


def test_dividend_dates_follow_the_rule_from_the_record_date():
    """Экс-дата, последний день покупки и срок выплаты считаются от отсечки по правилу сборщика (режим расчётов
    биржи, рабочие дни): в фактах — ровно эти даты, а не даты из брокерского календаря."""
    b = builder()
    switch = b.T1_SWITCH
    for where, r in all_years():
        if not r["record_date"]:
            assert r["ex_date"] is None and r["pay_date"] is None and plain(r["dps_ordinary"]) == 0, where
            continue
        record = dt.date.fromisoformat(r["record_date"])
        t1 = r["record_date"] >= switch
        assert r["settlement"] == ("T+1" if t1 else "T+2"), where
        ex = record if t1 and b.is_workday(record) else b.workday_before(record)
        assert r["ex_date"] == ex.isoformat(), f"{where}: экс-дата"
        assert r["pay_date"] == b.workdays_after(record, b.PAY_WORKDAYS).isoformat(), f"{where}: срок выплаты"
        assert b.is_workday(dt.date.fromisoformat(r["pay_date"])), f"{where}: срок выплаты — рабочий день"
        if "last_buy_date" in r:
            assert r["last_buy_date"] == b.workday_before(ex).isoformat(), f"{where}: последний день покупки"


def test_the_workday_rule_knows_weekends_and_holidays():
    """Правило рабочих дней сборщика: выходные, праздники и понедельник после праздника, выпавшего на выходной
    (12.06.2016 — воскресенье: 13.06.2016 нерабочий; 12.06.2015 — пятница)."""
    b = builder()
    d = dt.date
    assert not b.is_workday(d(2016, 6, 13)) and not b.is_workday(d(2015, 6, 12)) and not b.is_workday(d(2019, 6, 12))
    assert b.is_workday(d(2017, 6, 13)) and b.is_workday(d(2021, 5, 11)) and not b.is_workday(d(2021, 5, 10))
    assert b.workday_before(d(2016, 6, 14)) == d(2016, 6, 10)            # экс-дата дивиденда за 2015 г. (T+2)
    assert b.workday_before(d(2015, 6, 15)) == d(2015, 6, 11)            # за 2014 г.
    assert b.workdays_after(d(2026, 7, 20), 10) == d(2026, 8, 3)         # срок выплаты за 2025 г.
    assert b.workdays_after(d(2025, 7, 18), 10) == d(2025, 8, 1)
    assert b.workdays_after(d(2026, 10, 30), 3) == d(2026, 11, 5)        # 4 ноября — праздник


def _as_declared(r: dict) -> dict:
    return {"year": r["year"], "dps": plain(r["dps_ordinary"]), "status": "declared", "record_date": r["record_date"],
            "last_buy_date": r["last_buy_date"], "ex_date": r["ex_date"], "pay_date": r["pay_date"],
            "decided_date": r.get("decided_date"), "sources": list(r["sources"])}


def test_core_accepts_the_two_source_record_and_waits_for_the_second():
    """Сверка с приёмкой ядра (model.live): запись 2025 г. как объявленная — принята; с одним
    источником — ждёт второго. Цена — заведомо в коридоре (DPS < 0,5 × цены), дело не в ней."""
    live = pytest.importorskip("model.live")
    check = getattr(live, "check_register", None)
    if check is None:
        pytest.skip("в model.live нет check_register")
    last = max(load("dividends")["history"], key=lambda r: r["year"])
    rec = _as_declared(last)
    price = 4 * rec["dps"]
    ok, bad = check([rec], price=price)
    assert len(ok) == 1 and not bad, bad
    one = dict(rec, sources=rec["sources"][:1])
    ok, bad = check([one], price=price)
    assert not ok and len(bad) == 1, "запись с одним источником принята"
