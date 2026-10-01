"""Журнал прогнозов: неизменяемость, одна запись на горизонт, заморозка эталонов, правило допуска по T3/T4."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from indicators import journal as jm
from indicators.journal import Journal, JournalError

pytestmark = pytest.mark.tact


def test_rows_cannot_be_changed_or_deleted(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.record_forecast(target="ni_q", period="2026Q3", horizon="T-30", forecast=500.0,
                      benchmarks={"model": 490.0}, release_sha="a" * 40)
    j.record_actual(target="ni_q", period="2025Q4", value=399.0, source="МСФО 2025, с. 6")
    j.freeze("ni_q", "2026Q3", "bridge", 1.003)
    db = sqlite3.connect(j.path)
    for sql in ("UPDATE forecasts SET forecast = 1", "DELETE FROM forecasts", "UPDATE actuals SET value = 1",
                "DELETE FROM actuals", "UPDATE frozen SET value = '2'", "DELETE FROM frozen"):
        # № 48: согласование — «правка запрещена», «удаление запрещено»
        ban = "правка запрещена" if sql.startswith("UPDATE") else "удаление запрещено"
        with pytest.raises(sqlite3.DatabaseError, match=f"журнал неизменяем: {ban}"):
            db.execute(sql)
    db.close()


def test_entries_carry_the_book_of_the_expectation(tmp_path):
    """№ 46: книга ожидания — в `inputs` записи и в `entries[].book_version`; у прежних записей — null."""
    j = Journal(tmp_path / "j.sqlite")
    j.record_forecast(target="nim_q", period="2026Q3", horizon="T-30", forecast=0.06, benchmarks={"model": 0.06},
                      release_sha="0" * 12, inputs={"book_version": "1.2", "book_digest": "ab" * 32})
    j.record_forecast(target="cor_q", period="2026Q3", horizon="T-30", forecast=0.01, benchmarks={"model": 0.01},
                      release_sha="0" * 12)
    by = {e["target"]: e for e in j.entries()}
    assert by["nim_q"]["book_version"] == "1.2" and by["cor_q"]["book_version"] is None
    assert by["nim_q"]["release_sha"] == "0" * 12                       # свежий сервер: выпуска ещё нет
    row = j.forecast("nim_q", "2026Q3", "T-30")
    assert row["inputs"]["book_digest"] == "ab" * 32 and row["book_version"] == "1.2"
    assert {f["book_version"] for f in j.export()["forecasts"]} == {"1.2", None}


def test_one_record_per_horizon_and_frozen_benchmarks(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    a = j.record_forecast(target="ni_q", period="2026Q3", horizon="T-30", forecast=500.0,
                          benchmarks={"model": 490.0, "ras_bridge": 505.0}, release_sha="b" * 12)
    b = j.record_forecast(target="ni_q", period="2026Q3", horizon="T-30", forecast=510.0,
                          benchmarks={"model": 480.0, "ras_bridge": 515.0}, release_sha="c" * 12)
    assert a == b
    row = j.forecast("ni_q", "2026Q3", "T-30")
    assert row["forecast"] == 500.0 and row["benchmarks"] == {"model": 490.0, "ras_bridge": 505.0}
    assert row["release_sha"] == "b" * 12
    assert j.record_forecast(target="ni_q", period="2026Q3", horizon="T-90", forecast=1.0,
                             benchmarks={}, release_sha="d" * 12) != a
    assert j.freeze("ni_q", "2026Q3", "bridge", 1.003) == 1.003
    assert j.freeze("ni_q", "2026Q3", "bridge", 1.5) == 1.003


def test_discipline_errors(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    with pytest.raises(JournalError):
        j.record_forecast(target="eps", period="2026Q3", horizon="T-30", forecast=1, benchmarks={}, release_sha="x")
    with pytest.raises(JournalError):
        j.record_forecast(target="ni_q", period="2026Q3", horizon="T-7", forecast=1, benchmarks={}, release_sha="x")
    j.record_actual(target="ni_q", period="2026Q2", value=511.6, source="МСФО 6М26, с. 6",
                    reported_on=date(2026, 7, 29))
    j.record_actual(target="ni_q", period="2026Q2", value=511.6, source="повтор")      # то же значение — без строки
    with pytest.raises(JournalError, match="неснимаем"):
        j.record_actual(target="ni_q", period="2026Q2", value=511.7, source="исправление")
    with pytest.raises(JournalError, match="задним числом"):
        j.record_forecast(target="ni_q", period="2026Q2", horizon="T-30", forecast=500, benchmarks={},
                          release_sha="x")
    with pytest.raises(JournalError):
        j.record_actual(target="ni_q", period="2026Q1", value=500.0, source="")
    assert j.actual("ni_q", "2026Q2")["reported_on"] == "2026-07-29"


def _event(j, target, period, forecast, actual, **marks):
    j.record_forecast(target=target, period=period, horizon="T-90", forecast=forecast,
                      benchmarks={"model": marks.pop("model"), **marks}, release_sha="e" * 12)
    j.record_actual(target=target, period=period, value=actual, source="тест")


def test_admission_collects_then_passes_or_fails(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    qs = ["2026Q4", "2027Q1", "2027Q2", "2027Q3"]
    for q in qs[:3]:
        _event(j, "nim_q", q, 0.0620, 0.0621, model=0.0600, prev_quarter=0.0650)
        _event(j, "cor_q", q, 0.0120, 0.0121, model=0.0100, prev_quarter=0.0150)
    s = j.admission_summary()
    assert s["status"] == "collecting" and s["events_scored"] == 3 and s["events_needed"] == 4
    _event(j, "nim_q", qs[3], 0.0620, 0.0619, model=0.0600, prev_quarter=0.0650)
    _event(j, "cor_q", qs[3], 0.0120, 0.0119, model=0.0100, prev_quarter=0.0150)
    s = j.admission_summary()
    assert s["status"] == "passed" and s["mse_ratio"]["nim_q"] < 0.8 and s["mse_ratio"]["cor_q"] < 0.8
    assert "4 событий" in s["rule"]


def test_equation_equal_to_the_model_never_passes(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    for q in ("2026Q4", "2027Q1", "2027Q2", "2027Q3"):
        _event(j, "nim_q", q, 0.0600, 0.0620, model=0.0600)
        _event(j, "cor_q", q, 0.0100, 0.0110, model=0.0100)
    a = j.admission("nim_q")
    assert a["status"] == "failed" and a["mse_ratio"] == pytest.approx(1.0)
    assert j.admission_summary()["status"] == "failed"


def test_rows_without_the_model_benchmark_are_not_scored(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.record_forecast(target="nim_q", period="2026Q4", horizon="T-90", forecast=0.06,
                      benchmarks={"prev_quarter": 0.061}, release_sha="f" * 12)
    j.record_actual(target="nim_q", period="2026Q4", value=0.062, source="тест")
    assert j.admission("nim_q")["events_scored"] == 0


def test_entries_of_the_last_four_events_with_errors(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    for q in ("2026Q1", "2026Q2", "2026Q3", "2026Q4", "2027Q1"):
        j.record_forecast(target="ni_q", period=q, horizon="T-30", forecast=500.0, benchmarks={"model": 490.0},
                          release_sha="0" * 12)
    j.record_actual(target="ni_q", period="2026Q4", value=510.0, source="тест")
    rows = j.entries()
    assert sorted({r["period"] for r in rows}) == ["2026Q2", "2026Q3", "2026Q4", "2027Q1"]
    q4 = [r for r in rows if r["period"] == "2026Q4"][0]
    assert q4["actual"] == 510.0 and q4["errors"] == {"forecast": -10.0, "model": -20.0}
    assert jm.rule_text() in j.export()["rule"]


def test_readonly_open_requires_an_existing_file(tmp_path):
    with pytest.raises(JournalError):
        Journal(tmp_path / "none.sqlite", readonly=True)
    assert not (tmp_path / "none.sqlite").exists()


def test_insert_or_replace_and_upsert_are_refused(tmp_path):
    """INSERT OR REPLACE стирает строку без триггеров DELETE — его держат триггеры BEFORE INSERT (W1/C3)."""
    j = Journal(tmp_path / "j.sqlite")
    j.record_forecast(target="ni_q", period="2026Q3", horizon="T-30", forecast=500.0,
                      benchmarks={"model": 490.0}, release_sha="a" * 12)
    j.record_actual(target="ni_q", period="2025Q4", value=399.0, source="МСФО 2025, с. 6")
    j.freeze("ni_q", "2026Q3", "bridge", 1.003)
    cols = "(target, period, horizon, forecast, benchmarks, release_sha, recorded_at)"
    db = sqlite3.connect(j.path)
    for sql in (f"INSERT OR REPLACE INTO forecasts {cols} VALUES ('ni_q','2026Q3','T-30',1,'{{}}','x','t')",
                f"REPLACE INTO forecasts (id, {cols[1:]} VALUES (1,'nim_q','2027Q1','T-90',1,'{{}}','x','t')",
                f"INSERT INTO forecasts {cols} VALUES ('ni_q','2026Q3','T-30',1,'{{}}','x','t') "
                "ON CONFLICT(target, period, horizon) DO UPDATE SET forecast = 2",
                "INSERT OR REPLACE INTO actuals (target, period, value, source, reported_on, recorded_at) "
                "VALUES ('ni_q','2025Q4',1,'y','d','t')",
                "INSERT OR REPLACE INTO frozen (target, period, key, value, frozen_at) "
                "VALUES ('ni_q','2026Q3','bridge','2','t')"):
        with pytest.raises(sqlite3.DatabaseError, match="журнал неизменяем"):
            db.execute(sql)
    db.execute(f"INSERT INTO forecasts {cols} VALUES ('ni_q','2026Q4','T-90',1,'{{}}','x','t')")   # новая строка — можно
    db.rollback()
    db.close()
    assert j.forecast("ni_q", "2026Q3", "T-30")["forecast"] == 500.0
    assert j.actual("ni_q", "2025Q4")["value"] == 399.0 and j.frozen("ni_q", "2026Q3", "bridge") == 1.003


def test_an_old_journal_gets_the_replace_triggers_on_open(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(jm.SCHEMA)                       # файл до W1: без триггеров против REPLACE
    db.execute("INSERT INTO actuals (target, period, value, source, reported_on, recorded_at) "
               "VALUES ('ni_q','2025Q4',399,'x','d','t')")
    db.commit()
    db.close()
    Journal(path)
    db = sqlite3.connect(path)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {f"{t}_no_replace" for t in jm.ROW_KEYS} <= names
    with pytest.raises(sqlite3.DatabaseError, match="журнал неизменяем"):
        db.execute("INSERT OR REPLACE INTO actuals (target, period, value, source, reported_on, recorded_at) "
                   "VALUES ('ni_q','2025Q4',1,'y','d','t')")
    db.close()
