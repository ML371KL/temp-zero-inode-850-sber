"""Документы фактов: SCHEMA.md описывает каждый файл, FACTS.md называет каждый документ реестров и повторяет
печатаемые записки календаря и срока политики."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"

pytestmark = pytest.mark.docs


def test_schema_describes_every_file():
    text = (FACTS / "SCHEMA.md").read_text(encoding="utf-8")
    missing = [p.name for p in sorted(FACTS.glob("*.json")) if f"`{p.name}`" not in text]
    assert not missing, f"SCHEMA.md не описывает: {missing}"


def test_facts_md_names_every_document():
    text = (ROOT / "docs" / "FACTS.md").read_text(encoding="utf-8")
    missing = []
    for name, key in (("anchor", "documents"), ("calendar", "sources"), ("peers", "sources")):
        for d in json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))[key]:
            if f"`{d['key']}`" not in text or d["sha256"][:16] not in text:
                missing.append(f"{name}:{d['key']}")
    assert not missing, f"FACTS.md не называет документы: {missing}"


def test_facts_md_repeats_the_calendar_notes():
    """Таблица календаря в FACTS.md повторяет названия и записки событий дословно: записки печатает
    витрина, и документ не должен отставать от правки факта (AUDIT-2 № 73)."""
    text = (ROOT / "docs" / "FACTS.md").read_text(encoding="utf-8")
    missing = []
    for e in json.loads((FACTS / "calendar.json").read_text(encoding="utf-8"))["events"]:
        row = f"| {e['title']} | {e.get('note') or ''} |"
        if row not in text:
            missing.append(e["id"])
    assert not missing, f"FACTS.md расходится с calendar.json в событиях: {missing}"


def test_facts_md_quotes_the_policy_term_note():
    """Записка о сроке политики печатается на витрине — FACTS.md приводит её дословно."""
    text = " ".join((ROOT / "docs" / "FACTS.md").read_text(encoding="utf-8").split())
    note = json.loads((FACTS / "dividends.json").read_text(encoding="utf-8"))["policy"]["valid_until_note"]
    assert note in text, "FACTS.md: записка о сроке политики расходится с dividends.json"
