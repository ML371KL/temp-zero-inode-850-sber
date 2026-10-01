"""Хранилище: неизменяемый сырой архив с sha256 и gzip, ряды с винтажами, защищённые источники."""

from __future__ import annotations

import pytest

import gzip
import hashlib
from datetime import date

from indicators import sources
from indicators.store import COMPRESS_ABOVE_BYTES, PROTECTED_SOURCES, Point, Store

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)


def test_raw_is_immutable_and_keeps_a_second_answer_of_the_day(tmp_path):
    s = Store(tmp_path)
    a = s.save_raw("cbr", "x.xml", b"one", url="u", fetched_at="2026-10-01T06:00:00+00:00", status=200)
    assert s.save_raw("cbr", "x.xml", b"one", url="u", fetched_at="2026-10-01T07:00:00+00:00") == a
    b = s.save_raw("cbr", "x.xml", b"two", url="u", fetched_at="2026-10-01T08:00:00+00:00")
    assert b != a and a.read_bytes() == b"one" and s.read_raw(b) == b"two"
    assert s.save_raw("cbr", "x.xml", b"two", url="u", fetched_at="2026-10-01T09:00:00+00:00") == b
    meta = s.raw_meta(a)
    assert meta["sha256"] == hashlib.sha256(b"one").hexdigest() and meta["status"] == 200
    assert a.parent.name == "2026-10-01"


def test_large_raw_is_gzipped_but_hashed_as_received(tmp_path):
    s = Store(tmp_path)
    body = b"x" * (COMPRESS_ABOVE_BYTES + 10)
    p = s.save_raw("iss", "big.json", body, url="u")
    assert p.name.endswith(".gz") and gzip.decompress(p.read_bytes()) == body
    assert s.raw_meta(p)["sha256"] == hashlib.sha256(body).hexdigest()
    assert s.read_raw(p) == body


def test_vintages_neighbour_rule(tmp_path):
    s = Store(tmp_path)
    t = ["2026-10-0%dT00:00:00+00:00" % d for d in range(1, 8)]
    s.upsert("r", [Point("2026M08", 1.0, t[0])])
    s.upsert("r", [Point("2026M08", 1.0, t[1])])
    s.upsert("r", [Point("2026M08", 1.0, t[2])])
    assert len(s.load("r").points) == 1                    # A→A→A — одна точка
    s.upsert("r", [Point("2026M08", 2.0, t[3]), Point("2026M08", 1.0, t[4])])
    assert len(s.load("r").points) == 3                    # A→B→A — три
    s.upsert("r", [Point("2026M07", 5.0, t[5]), Point("2026M07", 5.0, t[0])])
    assert len([p for p in s.load("r").points if p.period == "2026M07"]) == 2   # ранняя датирует знание


def test_value_as_of_answers_what_was_known(tmp_path):
    s = Store(tmp_path)
    s.upsert("r", [Point("2026M08", 100.0, "2026-09-09T07:00:00+00:00"),
                   Point("2026M08", 101.0, "2026-09-20T07:00:00+00:00")])
    assert s.value_as_of("r", "2026M08", "2026-09-08") is None
    assert s.value_as_of("r", "2026M08", "2026-09-10") == 100.0
    assert s.value_as_of("r", "2026M08", "2026-10-01") == 101.0
    assert s.load("r").first_seen("2026M08") == "2026-09-09T07:00:00+00:00"


def test_null_is_kept_as_null_and_nan_is_rejected(tmp_path):
    s = Store(tmp_path)
    s.upsert("r", [Point("2022M06", None, "2026-10-01T00:00:00+00:00", "missing"),
                   Point("2026M08", float("nan"), "2026-10-01T00:00:00+00:00")])
    series = s.load("r")
    assert [p.period for p in series.points] == ["2022M06"] and series.points[0].value is None
    assert series.history() == {} and s.rejected


def test_prune_never_touches_protected_sources(tmp_path):
    s = Store(tmp_path)
    for src in ("iss", *sorted(PROTECTED_SOURCES)):
        s.save_raw(src, "a", b"1", url="u", fetched_at="2024-01-01T00:00:00+00:00")
    removed = s.prune_raw(keep_days=30, today=date(2026, 10, 1))
    assert removed == ["iss/2024-01-01"]
    for src in PROTECTED_SOURCES:
        assert s.raw_days(src) == ["2024-01-01"]


def test_irrecoverable_collectors_write_to_protected_raw_dirs():
    for name in sources.IRRECOVERABLE:
        assert sources.COLLECTORS[name].raw_source in PROTECTED_SOURCES


def test_state_files_are_written_atomically(tmp_path):
    s = Store(tmp_path)
    s.write_state("a/b.json", {"x": [1, "б"]})
    assert s.read_state("a/b.json") == {"x": [1, "б"]} and s.read_state("nope.json") is None
    assert not list(tmp_path.rglob("*.tmp"))


def test_root_defaults_to_the_state_dir_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_STATE_DIR", str(tmp_path / "st"))
    assert Store().root == tmp_path / "st"
