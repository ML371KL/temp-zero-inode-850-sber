"""Факты: строки, которые уходят в выпуск и печатаются на витрине, — словами для владельца.

PAYLOAD §0.2 «служебного текста на экране нет»: правится источник строки, а не маска на витрине.
Из фактов на экран идут записки и названия событий календаря, записка о прогнозе сектора и события
пересмотров гайденса, текст и записка о сроке дивидендной политики, база сравнения и подписи
аналогов. В них нет кортежей и полей ответов источников, меток класса допущения, хвостов sha256,
путей к листам, рабочих пометок, кодов периодов и дат ISO; дата — с годом (короткая дата читается
на экране как дробь; AUDIT-2 № 73, № 96).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"

RULES = {
    "кортеж ответа источника": re.compile(r"\(\d{4}, \d+, [A-Z]{3,}"),
    "метка класса допущения": re.compile(r"\[(?:В|Ф|Р|НП)(?:\]|,)"),
    "хвост sha256": re.compile(r"sha256 [0-9a-f]{8,}"),
    "рабочая пометка": re.compile(r"ведущ|первичк|антибот", re.I),
    "короткая дата без года": re.compile(r"(?<![\d.,])(?:0[1-9]|[12]\d|3[01])\.(?:0[1-9]|1[0-2])(?!\d|\.\d|\s?%)"),
    "код периода": re.compile(r"(?<![\w])\d{4}(?:M\d{2}|Q[1-4])(?![\w])"),
    "дата ISO": re.compile(r"(?<![\d-])\d{4}-\d{2}-\d{2}(?![\d-])"),
    "имя поля": re.compile(r"(?<![\w])[a-z]+(?:_[a-z0-9]+)+(?![\w])"),
    "true/false": re.compile(r"(?<![\w])(?:true|false|None|null)(?![\w])"),
    "путь к листу или файлу": re.compile(r"stage\d/|\.(?:csv|json|py|ya?ml)(?![\w])"),
    "e-нотация": re.compile(r"\d(?:[.,]\d+)?e[+-]?\d+", re.I),
    "десятичная точка в проценте": re.compile(r"\d\.\d+\s?%"),
    "версия без имени": re.compile(r"850oa \d"),
}


def load(name: str) -> dict:
    return json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))


def printed() -> list[tuple[str, str]]:
    """(место, строка) — всё, что из фактов уходит в выпуск текстом."""
    out = []
    for e in load("calendar")["events"]:
        out.append((f"calendar.{e['id']}.title", e["title"]))
        if e.get("note"):
            out.append((f"calendar.{e['id']}.note", e["note"]))
    g = load("guidance")
    for seg, n in g["items"]["loan_growth"].items():
        out.append((f"guidance.loan_growth.{seg}.scope_note", n["scope_note"]))
    for h in g["history"]:
        out.append((f"guidance.history.{h['date']}.{h['key']}.event", h["event"]))
        out.append((f"guidance.history.{h['date']}.{h['key']}.note", h["note"]))
    pol = load("dividends")["policy"]
    for k in ("name", "text", "valid_until_note"):
        out.append((f"dividends.policy.{k}", pol[k]))
    p = load("peers")
    out.append(("peers.basis", p["basis"]))
    for b in p["banks"]:
        out += [(f"peers.{b['ticker']}.name", b["name"]), (f"peers.{b['ticker']}.basis", b["basis"]),
                (f"peers.{b['ticker']}.capital_ratio.name", b["capital_ratio"]["name"])]
    return out


def breaches(text: str) -> list[str]:
    return [f"{rule}: «{m.group(0)}»" for rule, rx in RULES.items() for m in [rx.search(text)] if m]


def test_printed_strings_are_plain_words():
    rows = printed()
    assert len(rows) > 60, "перечень печатаемых строк пуст — поля фактов переименованы?"
    bad = [f"{where}: {hit}" for where, text in rows for hit in breaches(text)]
    assert not bad, "служебный текст в строках фактов, идущих на витрину:\n" + "\n".join(bad)


@pytest.mark.parametrize("text, rule", [
    ("дата календаря эмитента в T-Invest (2026, 3, QUARTER; ≈10-е число — РСБУ)", "кортеж ответа источника"),
    ("прецеденты Дня инвестора: 06.12.2023, 10.12.2025; дата — оценка [В]", "метка класса допущения"),
    ("вторичный источник [НП]", "метка класса допущения"),
    ("оценка [Р, не раскрыто]", "метка класса допущения"),
    ("прецедент 28.10.2025; IR-страница закрыта антиботом", "рабочая пометка"),
    ("сектор без изменений к 29.04; Сбер — лучше сектора", "короткая дата без года"),
    ("2026 — 09.10, календарь эмитента", "короткая дата без года"),
    ("презентация, с. 24, sha256 d17d36167137507e", "хвост sha256"),
    ("РСБУ за 2026M09", "код периода"),
    ("день не объявлен на 2026-10-01", "дата ISO"),
    ("по столбцу bv_date", "имя поля"),
    ("лист stage1/ras/ras_release_monthly_wide.csv", "путь к листу или файлу"),
    ("допуск 6.9e-18", "e-нотация"),
    ("порог 13.3 %", "десятичная точка в проценте"),
    ("миры 850oa 1.6", "версия без имени"),
])
def test_rules_catch_service_text(text, rule):
    """Правила ловят прежние строки фактов и образцы записей аудита — тест не зелёный «по пустоте»."""
    assert any(hit.startswith(rule) for hit in breaches(text)), f"«{text}»: правило «{rule}» не сработало"


@pytest.mark.parametrize("text", [
    "оценка по прецеденту: 09.10.2024, 09.10.2025; 09.10.2026 — дата из календаря эмитента (T-Invest)",
    "Положение действует 3 года с даты утверждения Наблюдательным советом (п. 6.4) — по 05.12.2026",
    "условие — Н20.0 не ниже 13,3 % на среднесрочном горизонте",
    "МСФО группы за 3К и 9М 2026 г.",
    "отсечки 11.07.2024, 18.07.2025, 20.07.2026 (T+1: экс-дата = отсечка); выплата — через ≈2 недели",
])
def test_rules_pass_plain_words(text):
    assert not breaches(text), breaches(text)
