"""CLI сборки (`model.build_release`) и таблицы книги (`model.book_results`): коды выхода, файлы, сводка."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from model import book_results as BR
from model import build_release as BRL
from model import payload as P
from tests.support_core2 import book_and_facts, fast_release_payload

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.tact
def test_check_file_accepts_a_valid_release_and_rejects_a_broken_one(tmp_path, capsys):
    _, d = fast_release_payload()
    good = tmp_path / "latest.json"
    BRL.write_release(d, good)
    assert BRL.main(["--check", str(good)]) == 0                    # контракт; публикацию быстрой отвергает publish
    assert "к публикации не годна" in capsys.readouterr().out
    assert P.validate(d, publish=True) != []
    full = copy.deepcopy(d)
    full["meta"]["fast"] = False
    full["meta"]["payload_sha256"] = P.payload_hash(full)
    full["meta"]["bytes"] = P.compact_bytes(full)
    BRL.write_release(full, good)
    assert BRL.main(["--check", str(good)]) == 0
    bad = copy.deepcopy(full)
    bad.pop("calendar")
    BRL.write_release(bad, tmp_path / "bad.json")
    assert BRL.main(["--check", str(tmp_path / "bad.json")]) == 1
    assert "calendar" in capsys.readouterr().out
    (tmp_path / "junk.json").write_text("{", encoding="utf-8")
    assert BRL.main(["--check", str(tmp_path / "junk.json")]) == 1


@pytest.mark.tact
def test_written_release_is_compact_json(tmp_path):
    _, d = fast_release_payload()
    path = tmp_path / "x.json"
    BRL.write_release(d, path)
    text = path.read_text(encoding="utf-8")
    assert "\n" not in text and ": " not in text[:200]
    assert json.loads(text) == d


@pytest.mark.tact
def test_book_results_fast_tables_and_rendering(monkeypatch):
    book, facts = book_and_facts()
    monkeypatch.setattr(BR.U, "FAST_DRAWS", 6)
    res = BR.book_results(book, facts, slow=False)
    assert {"book_version", "facts_date", "engine_commit", "derived", "layers", "headline", "cells", "central_cell",
            "checks", "reverse_dcf", "next_report"} <= set(res)
    assert len(res["cells"]) == 36 and res["draws"] == 6
    assert set(res["derived"]) >= {"nii", "anchor", "capital", "regimes"}
    h = res["headline"]
    assert h["point"] == pytest.approx(h["low"] + h["lambda"] * (h["high"] - h["low"]))
    text = BR.render_run_output(json.loads(json.dumps(res, default=BR._default)))
    assert "ЗАГОЛОВОК" in text and "СЛОИ" in text and "ПРОВЕРКИ" in text
    again = BR.book_results(book, facts, slow=False)
    assert again["headline"] == res["headline"] and again["cells"] == res["cells"]     # бит в бит


@pytest.mark.ci_only
def test_bank_language_sensitivities_are_smooth_in_lambda(parallel_band):
    """М§8.3 (проверка гладкости): на числе прогонов пересчёта медианы книги соседние строки `by_lambda`
    чувствительностей различаются не больше чем на 1 ₽ — оценщик срединных прогонов, а не разность двух медиан.
    Три полосы `median_draws` — тест только для CI."""
    from model.grid import live_from_book
    from tests.support_core2 import sensitivity_rows
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    with parallel_band():
        _, rows = sensitivity_rows(book, facts, live, int(book.get("valuation.uncertainty.median_draws")))
    for a, b in zip(rows, rows[1:]):
        for key in ("rub_per_01pp_cor", "rub_per_01pp_nim"):
            assert abs(a[key] - b[key]) <= 1.0, (key, a[key], b[key])


@pytest.mark.ci_only
def test_cli_check_in_memory_fast_book_only():
    """`python -m model.build_release --check --fast --book-only`: собирает в памяти, ничего не пишет."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", BANK_WORKERS="1")
    done = subprocess.run([sys.executable, "-B", "-m", "model.build_release", "--check", "--fast", "--book-only"],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert done.returncode in (0, 1), done.stdout + done.stderr
    assert "медиана" in done.stdout
    assert not (Path(env["BANK_STATE_DIR"]) / "release" / "latest.json").exists()
