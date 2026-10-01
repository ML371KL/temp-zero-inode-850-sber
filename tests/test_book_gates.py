# -*- coding: utf-8 -*-
"""Эксплуатационные данные книги: объяснения гейтов и записки о скачках заголовка (поток book; MODEL §14.2, §14.4).

Сборку выпуска роняет только сработавший гейт без действующего объяснения; объяснение, которому до срока меньше
30 дней, и истёкший документ эмитента — плашки выпуска (решение ведущего В17, MODEL §14.3). Поэтому тест «объяснение
действует на сегодня» — будильник с меткой `deadline`: в набор сервера и в прогоны «в будущем» он не входит (срок
проверяет сама сборка), идёт в CI и в ручном прогоне. «Сегодня» — `date.today()`, его подменяет `FAKE_TODAY`.
Остальные тесты от «сегодня» не зависят — ни прямо, ни через горизонт календаря фактов: объяснение `guidance_gap`
живёт не дольше отчёта МСФО, закрывающего первый прогнозный квартал книги (`meta.first_period`; событие календаря
`data/facts/calendar.json` с `covers` этого квартала) — новый отчёт меняет и факт года, и гайденс, а после
перезаякоривания квартал книги и срок записи сдвигаются вместе; каждый гейт,
сработавший в результатах книги той же версии, объяснён, и масса лежит в коридоре; тексты объяснений и записок
написаны словами для владельца — их печатает витрина (PAYLOAD §0.2). Формат записей — `gate_explanations.yaml` и
`release_notes.yaml` (массы после прогона сверяет интеграция).
"""
from __future__ import annotations

import json
import re
from datetime import date

import pytest
import yaml

try:
    from tests import support_book as S
except ImportError:                      # прогон без pytest.ini (pythonpath = .) и без tests/__init__.py
    import support_book as S

EXPLANATIONS = S.BOOK_DIR / "gate_explanations.yaml"
NOTES = S.BOOK_DIR / "release_notes.yaml"
RESULTS = S.BOOK_DIR / "results.json"
CALENDAR = S.FACTS_DIR / "calendar.json"
GUIDANCE_GATE = "guidance_gap"
MASS_LOW, MASS_HIGH = (0.4, 0.02), (1.5, 0.02)      # коридор массы 0,4 × lo − 0,02 … 1,5 × hi + 0,02 (MODEL §14.2)


def _day(value) -> date:
    """Дата из YAML/JSON: объект даты или строка ISO."""
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    return date.fromisoformat(str(value))


def _explanations() -> dict:
    data = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), "gate_explanations.yaml — словарь «гейт → объяснение»"
    return data


def closing_ifrs(period: str, events: list[dict] | None = None):
    """Отчёт МСФО календаря фактов, закрывающий квартал `period` (`covers`): (дата, id) или None. От «сегодня» и от
    того, сколько событий осталось в календаре впереди, не зависит."""
    if events is None:
        events = json.loads(CALENDAR.read_text(encoding="utf-8"))["events"]
    found = sorted((_day(e["date"]), e["id"]) for e in events if e.get("kind") == "ifrs" and e.get("covers") == period)
    return found[0] if found else None


def fired_gates() -> dict[str, float] | None:
    """Гейты, сработавшие в результатах книги (`results.json`): {имя: масса}; None — результаты другой версии книги
    (новая книга ещё не посчитана ядром)."""
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    if res["book_version"] != S.template()["meta"]["version"]:
        return None
    return {c["name"]: float(c.get("mass") or 0.0) for c in res["checks"] if c.get("kind") == "gate" and c.get("fired")}


@pytest.mark.tact
def test_gate_explanations_have_the_format():
    """Запись — {explanation, expected_mass | expected_mass_range: [lo, hi], valid_until} (MODEL §14.2)."""
    for name, e in _explanations().items():
        assert isinstance(e, dict), name
        assert isinstance(e.get("explanation"), str) and len(e["explanation"].strip()) > 40, name
        has_mass, has_range = "expected_mass" in e, "expected_mass_range" in e
        assert has_mass != has_range, f"{name}: ровно одно из expected_mass | expected_mass_range"
        if has_mass:
            assert 0.0 <= float(e["expected_mass"]) <= 1.0, name
        else:
            lo, hi = e["expected_mass_range"]
            assert 0.0 <= float(lo) <= float(hi) <= 1.0, name
        _day(e["valid_until"])
        assert set(e) <= {"explanation", "expected_mass", "expected_mass_range", "valid_until"}, name


@pytest.mark.deadline
def test_every_explanation_of_a_fired_gate_is_valid_today():
    """`valid_until` действует включительно (MODEL §14.2): на следующий день сборка с этим сработавшим гейтом выходит
    кодом 1. Проверяются записи гейтов, сработавших в результатах книги; пока результаты другой версии — все записи."""
    today = date.today()
    fired = fired_gates()
    watched = {n: e for n, e in _explanations().items() if fired is None or n in fired}
    stale = {n: _day(e["valid_until"]).isoformat() for n, e in watched.items() if _day(e["valid_until"]) < today}
    assert not stale, (f"на {today.isoformat()} истекли объяснения гейтов {stale}: перезаякорить книгу или продлить "
                       "объяснение в data/assumptions/gate_explanations.yaml, если гейт по-прежнему правильно объяснён")


@pytest.mark.tact
def test_every_gate_fired_in_the_book_results_is_explained():
    """Каждый гейт, сработавший в результатах книги той же версии, имеет запись, и его масса лежит в коридоре записи
    (иначе сборка выпуска на цене книги вышла бы кодом 1: «нет объяснения» или «масса вне коридора»)."""
    fired = fired_gates()
    if fired is None:
        pytest.skip("results.json — другой версии книги: массы сверит прогон ядра на новой книге")
    notes = _explanations()
    assert sorted(set(fired) - set(notes)) == [], "сработавшие гейты без объяснения"
    for name, mass in fired.items():
        e = notes[name]
        lo, hi = e["expected_mass_range"] if "expected_mass_range" in e else (e["expected_mass"], e["expected_mass"])
        low, high = float(lo) * MASS_LOW[0] - MASS_LOW[1], float(hi) * MASS_HIGH[0] + MASS_HIGH[1]
        assert low <= mass <= high, f"{name}: масса {mass} вне коридора {low:.4f}…{high:.4f}"


@pytest.mark.tact
def test_the_guidance_gap_explanation_ends_by_the_report_that_closes_the_book_quarter():
    """Объяснение `guidance_gap` — не дольше отчёта МСФО, который закрывает первый прогнозный квартал книги
    (MODEL §14.2): отчёт приносит новый факт года и новый гайденс, объяснение пишется заново. Квартал — из книги
    (`meta.first_period`), дата отчёта — событие календаря фактов с `covers` этого квартала; такое событие из
    календаря не удаляют, пока книга квартал не закроет. Ни «сегодня», ни горизонт календаря в проверку не входят:
    тест зелёный на любой дате прогона, пока книга и календарь согласованы."""
    entry = _explanations().get(GUIDANCE_GATE)
    if entry is None:
        pytest.skip(f"объяснения {GUIDANCE_GATE} нет — гейт не срабатывает")
    period = str(S.template()["meta"]["first_period"])
    closing = closing_ifrs(period)
    assert closing is not None, (f"в календаре фактов нет отчёта МСФО за {period} — событие открытого квартала книги "
                                 "не удаляют, пока книга его не закроет")
    until, (day, event) = _day(entry["valid_until"]), closing
    assert until <= day, f"{GUIDANCE_GATE}: valid_until {until} позже отчёта МСФО за {period} — {day} ({event})"


@pytest.mark.tact
def test_the_closing_report_does_not_depend_on_today_or_the_calendar_horizon():
    """Правило срока на заглушке календаря: отчёт квартала книги находится по `covers`, сколько бы событий ни стояло
    до и после него; события другого вида и других кварталов не подходят; нет события — None (тест срока назовёт
    календарь, а не дату прогона)."""
    events = [{"id": "ifrs-2001Q4", "kind": "ifrs", "date": "2002-02-25", "covers": "2001Q4"},
              {"id": "ras-2002M03", "kind": "ras", "date": "2002-04-09", "covers": "2002Q1"},
              {"id": "ifrs-2002Q1", "kind": "ifrs", "date": "2002-04-28", "covers": "2002Q1"},
              {"id": "ifrs-2002Q2", "kind": "ifrs", "date": "2002-07-29", "covers": "2002Q2"}]
    assert closing_ifrs("2002Q1", events) == (date(2002, 4, 28), "ifrs-2002Q1")
    assert closing_ifrs("2002Q1", events[2:3]) == (date(2002, 4, 28), "ifrs-2002Q1")      # календарь кончается отчётом
    assert closing_ifrs("2002Q3", events) is None and closing_ifrs("2002Q1", events[:2]) is None
    period = str(S.template()["meta"]["first_period"])
    assert closing_ifrs(period) is not None, f"календарь фактов держит отчёт МСФО открытого квартала книги {period}"


@pytest.mark.tact
def test_explanations_and_notes_are_written_for_the_owner():
    """Текст объяснения гейта и записки о скачке печатает витрина: без рабочих пометок, имён ключей и листов, меток
    класса допущения, дат без года и версий без имени (PAYLOAD §0.2; записи аудита № 73, № 96)."""
    bad = {name: S.printed_issues(e["explanation"]) for name, e in _explanations().items()}
    assert {k: v for k, v in bad.items() if v} == {}
    notes = yaml.safe_load(NOTES.read_text(encoding="utf-8"))
    assert [S.printed_issues(n["note"]) for n in notes if S.printed_issues(n["note"])] == []


def test_the_owner_text_rule_catches_working_notes():
    """Правило на заглушках: строки первого аудита витрины ловятся, обычный текст проходит."""
    for text in ("делить сжатие с пассивами — вопрос ведущему (М§4.4–§4.5)", "решение владельца 30.09; ось 0,049–0,062",
                 "850oa 1.6; DESIGN §3.4", "порог минимума — суждение [В]", "лист evidence/governance",
                 "regimes; ось 2027 0…−0,012", "первичку собрать", "отклонение 6,9e-18",
                 "(2026, 3, QUARTER; ≈10-е число — РСБУ)", "ключ nii.sigma0_split 0,09"):
        assert S.printed_issues(text), text
    for text in ("решение владельца 30.09.2026; премия за риск по Дамодарану на январь 2026 года",
                 "общая запись сценариев ставок семейства моделей — magnit-850oa book-1.6",
                 "маржа 6,60–6,63 % против гайденса около 6,2 %; сдвиг −0,5 п.п.; до 30.11.2026",
                 "цель банка по нормативу 13,3 % против минимума с надбавками 12,0 %"):
        assert S.printed_issues(text) == [], (text, S.printed_issues(text))


@pytest.mark.tact
def test_release_notes_have_the_format():
    """Записки о задуманных скачках (MODEL §14.4): список {valid_until, expected_central, tolerance_pct ≤ 25, note}."""
    notes = yaml.safe_load(NOTES.read_text(encoding="utf-8"))
    assert isinstance(notes, list)
    for n in notes:
        assert set(n) == {"valid_until", "expected_central", "tolerance_pct", "note"}, n
        _day(n["valid_until"])
        assert float(n["expected_central"]) > 0 and 0 < float(n["tolerance_pct"]) <= 25 and n["note"].strip()


@pytest.mark.docs
def test_explained_gates_are_gates_of_the_model():
    """Имена объяснений — гейты таблицы MODEL §14.2 (опечатка в имени — объяснение, которое ничего не объясняет)."""
    text = S.MODEL_MD.read_text(encoding="utf-8")
    a = text.index("### 14.2.")
    section = text[a:text.index("### 14.3.", a)]
    gates = set(re.findall(r"(?m)^\| `([a-z_0-9]+)` \|", section))
    assert {"guidance_gap", "nim_path_joint", "transmission_pairs"} <= gates
    assert set(_explanations()) <= gates, sorted(set(_explanations()) - gates)
