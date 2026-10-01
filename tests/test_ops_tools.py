# -*- coding: utf-8 -*-
"""Инструменты оператора `ops/tools/`: числа в документах, прогулка книги,
миры семейства, проба источников (адрес PDF эмитента — из затравки, дымовая проверка
распознавания). Сети нет: проба ходит в фикстуры индикаторов."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "ops" / "tools"
FIXTURE_BOOK = ROOT / "tests" / "fixtures" / "core" / "book.json"
FIXTURE_FACTS = ROOT / "tests" / "fixtures" / "core" / "facts"
BOOK = ROOT / "data" / "assumptions"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"tool_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------ render_numbers

render_numbers = _load("render_numbers")
RESULTS = {"headline": {"printed_median": 350.0, "band80": [300.25, 410.0], "p_below": 0.123},
           "reverse_dcf": {"rows": [{"axis": "ERP", "value": -0.0186}, {"axis": "ROE", "value": None}]},
           "cells": [{"cell": {"world": "M", "regime": "norm"}, "v": 12345.678}]}


@pytest.mark.parametrize("path,fmt,text", [
    ("headline.printed_median", "r0", "350"),
    ("headline.band80[0]", "r1", "300,2"),
    ("headline.p_below", "p1", "12,3"),
    ("reverse_dcf.rows[axis=ERP].value", "sp2", "−1,86"),
    ("reverse_dcf.rows[axis=ROE].value", "sp2n", "недостижимо"),
    ("cells[cell.world=M&cell.regime=norm].v", "r0", "12 346"),
])
def test_marks_resolve_and_format_like_the_documents(path, fmt, text):
    assert render_numbers.formatted(render_numbers.resolve(RESULTS, path), fmt) == text


def test_render_rewrites_values_and_reports_broken_marks():
    doc = "Медиана ≈<!--=headline.printed_median r0-->1<!--/--> ₽, `<!--=x r0-->пример<!--/-->`.\n"
    new, errors = render_numbers.render(doc, RESULTS)
    assert errors == [] and "<!--=headline.printed_median r0-->350<!--/-->" in new
    assert "`<!--=x r0-->пример<!--/-->`" in new, "метка в коде — пример синтаксиса"
    _, errors = render_numbers.render("x <!--=нет.пути r0-->1<!--/-->\n", RESULTS)
    assert errors and "нет ключа" in errors[0]
    _, errors = render_numbers.render("<!--=headline.printed_median r0-->1<!--/--> в начале\n", RESULTS)
    assert errors and "в начале строки" in errors[0]


def test_render_check_on_a_tree(tmp_path, capsys):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    assert render_numbers.main(["--check", "--root", str(root)]) == 0, "меток нет — сверять нечего"
    doc = root / "docs" / "X.md"
    doc.write_text("Медиана ≈<!--=headline.printed_median r0-->1<!--/--> ₽\n", encoding="utf-8")
    assert render_numbers.main(["--check", "--root", str(root)]) == 1, "метки есть, результатов нет"
    (root / "data" / "assumptions").mkdir(parents=True)
    (root / "data" / "assumptions" / "results.json").write_text(json.dumps(RESULTS), encoding="utf-8")
    assert render_numbers.main(["--check", "--root", str(root)]) == 1
    assert "устарел: docs/X.md" in capsys.readouterr().err
    assert render_numbers.main(["--root", str(root)]) == 0
    assert "≈<!--=headline.printed_median r0-->350<!--/--> ₽" in doc.read_text(encoding="utf-8")
    assert render_numbers.main(["--check", "--root", str(root)]) == 0


# ------------------------------------------------------------ walk_book

@pytest.fixture(scope="module")
def walk_book():
    return _load("walk_book")


def _steps(tmp_path: Path, steps) -> Path:
    path = tmp_path / "steps.yaml"
    path.write_text(yaml.safe_dump(steps, allow_unicode=True), encoding="utf-8")
    return path


def test_the_walk_between_two_books_converges(tmp_path, walk_book, capsys):
    end = json.loads(FIXTURE_BOOK.read_text(encoding="utf-8"))
    end["valuation"]["beta_e"] = round(end["valuation"]["beta_e"] + 0.05, 4)
    end["valuation"]["erp"] = round(end["valuation"]["erp"] + 0.003, 5)
    (tmp_path / "end.json").write_text(json.dumps(end, ensure_ascii=False), encoding="utf-8")
    steps = _steps(tmp_path, [{"name": "β", "from_end": ["valuation.beta_e"]},
                              {"name": "ERP", "from_end": ["valuation.erp"]}])
    code = walk_book.main(["--start", str(FIXTURE_BOOK), "--end", str(tmp_path / "end.json"),
                           "--steps", str(steps), "--facts", str(FIXTURE_FACTS),
                           "--json", str(tmp_path / "walk.json")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "последний шаг = прямой расчёт конечной книги: ДА" in out
    assert "книга после шагов = конечная книга целиком: ДА" in out
    rows = json.loads((tmp_path / "walk.json").read_text(encoding="utf-8"))["rows"]
    assert [r["step"] for r in rows] == ["старт", "β", "ERP"]
    assert rows[1]["point"] < rows[0]["point"], "выше β — выше стоимость капитала, ниже точка"

    partial = _steps(tmp_path, [{"name": "только β", "from_end": ["valuation.beta_e"]}])
    code = walk_book.main(["--start", str(FIXTURE_BOOK), "--end", str(tmp_path / "end.json"),
                           "--steps", str(partial), "--facts", str(FIXTURE_FACTS)])
    assert code == 1 and "конечная книга целиком: НЕТ" in capsys.readouterr().out


def test_a_step_that_breaks_the_book_is_refused(tmp_path, walk_book, capsys):
    steps = _steps(tmp_path, [{"name": "незнакомый ключ", "set": {"valuation.no_such_key": 1}}])
    code = walk_book.main(["--start", str(FIXTURE_BOOK), "--end", str(FIXTURE_BOOK),
                           "--steps", str(steps), "--facts", str(FIXTURE_FACTS)])
    assert code == 2
    assert "шаг 1 «незнакомый ключ»" in capsys.readouterr().err
    bad = _steps(tmp_path, [{"name": "без правок"}])
    assert walk_book.main(["--start", str(FIXTURE_BOOK), "--end", str(FIXTURE_BOOK),
                           "--steps", str(bad), "--facts", str(FIXTURE_FACTS)]) == 2


# ------------------------------------------------------------ refresh_worlds

@pytest.fixture(scope="module")
def refresh_worlds():
    return _load("refresh_worlds")


def _needs_book():
    if not (BOOK / "assumptions.yaml").exists() or not (BOOK / "worlds_source.json").exists():
        pytest.skip("книги 1.0 ещё нет в data/assumptions/ (поток book)")


def test_the_world_canon_holds(refresh_worlds, capsys):
    _needs_book()
    assert refresh_worlds.main(["--check"]) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "рецепт воспроизводит worlds_source.json" in out and "воспроизводится сборкой" in out


def test_a_broken_record_breaks_the_canon(refresh_worlds, tmp_path):
    _needs_book()
    book = tmp_path / "assumptions"
    shutil.copytree(BOOK, book, ignore=shutil.ignore_patterns("evidence", "__pycache__"))
    record = json.loads((book / "worlds_source.json").read_text(encoding="utf-8"))
    record["rows"][0]["key_avg"] = record["rows"][0]["key_avg"] + 1.0
    (book / "worlds_source.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    problems, _ = refresh_worlds.check_canon(book)
    assert any("рецепт не воспроизводит" in p for p in problems), problems
    assert any("sha256" in p for p in problems), problems


def test_a_candidate_of_the_same_record_changes_nothing(refresh_worlds, tmp_path):
    _needs_book()
    record = tmp_path / "record"
    record.mkdir()
    shutil.copyfile(BOOK / "worlds_source.json", record / "worlds_source.json")
    before = {p.name: p.read_bytes() for p in BOOK.iterdir() if p.is_file()}
    out = tmp_path / "candidate"
    assert refresh_worlds.main(["--record", str(record), "--out", str(out)]) == 0
    assert (out / "assumptions.candidate.yaml").read_text(encoding="utf-8") == \
        (BOOK / "assumptions.yaml").read_text(encoding="utf-8")
    report = (out / "CANDIDATE.md").read_text(encoding="utf-8")
    assert "Что меняется в записи (0)" in report and "| точка при λ книги, ₽ |" in report
    assert {p.name: p.read_bytes() for p in BOOK.iterdir() if p.is_file()} == before, "книга не тронута"


def test_the_candidate_never_writes_into_the_book(refresh_worlds, tmp_path, capsys):
    _needs_book()
    record = tmp_path / "record"
    record.mkdir()
    shutil.copyfile(BOOK / "worlds_source.json", record / "worlds_source.json")
    assert refresh_worlds.main(["--record", str(record), "--out", str(BOOK / "candidate")]) == 1
    assert "не пишет в книгу" in capsys.readouterr().err
    assert not (BOOK / "candidate").exists()


def test_the_candidate_template_changes_only_the_record_lines(refresh_worlds):
    _needs_book()
    text = (BOOK / "assumptions_template.yaml").read_text(encoding="utf-8")
    new = refresh_worlds.candidate_template(text, sha="f" * 64, asof="2026-10-24", curve_date="2026-10-23")
    changed = [(a, b) for a, b in zip(text.splitlines(), new.splitlines()) if a != b]
    assert len(text.splitlines()) == len(new.splitlines())
    assert {a.split(":")[0].strip() for a, _ in changed} <= {"sha256", "record_asof", "curve_date", "curve_as_of"}
    assert any('"' + "f" * 64 + '"' in b for _, b in changed)


# ------------------------------------------------------------ probe

@pytest.fixture
def probe(monkeypatch, tmp_path):
    from tests.support_ind import use_fixtures

    use_fixtures(monkeypatch, tmp_path, token=False)
    return _load("probe")


def _fake_tls(host, context):
    if context is None:
        import ssl

        raise ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
    return "Russian Trusted Root CA"


def test_the_probe_walks_every_source_through_the_collector_road(probe, monkeypatch):
    from tests.support_ind import FakeGetter

    getter = FakeGetter(overrides={"dataservice": b'[{"id": 1, "category_name": "x"}]',
                                   "static.test": b"%PDF-1.7 fixture"})
    rows = probe.run(pdf="https://static.test/files/report.pdf", getter=getter, tls=_fake_tls)
    by = {(r.probe, r.what): r for r in rows}
    assert {r.probe for r in rows} == set(probe.PROBES)
    forms = [r for r in rows if r.probe == "cbr_forms"]
    assert len(forms) == 4 and all(r.ok for r in forms), [r.note for r in forms]
    assert {r.what.split()[0] for r in forms} == {f"GetDatesForF{f}" for f in probe.FORMS}
    for r in rows:
        if r.probe != "tinvest":
            assert r.ok, (r.probe, r.what, r.note)
    assert by[("tinvest", "TLS с закреплённым корнем")].ok
    assert "как ожидается" in by[("tinvest", "TLS без закреплённого корня (справочно)")].note
    share = by[("tinvest", "ShareBy главного тикера (токен из окружения)")]
    assert not share.ok and "TINVEST_TOKEN" in share.note
    table = probe.table(rows)
    assert table.count("\n") == len(rows) + 1 and "| НЕТ " in table
    assert all("?" not in r.target for r in rows), "адреса без строки запроса"


def test_the_probe_names_a_failure_instead_of_falling(probe):
    from tests.support_ind import FakeGetter
    from indicators import http

    getter = FakeGetter(overrides={"iss.moex.com": http.FetchError("https://iss.moex.com/x", 503, "HTTP 503")})
    rows = probe.run(["iss", "sber_pdf"], getter=getter, tls=_fake_tls)
    iss_rows = [r for r in rows if r.probe == "iss"]
    assert iss_rows and all(not r.ok and r.status == "503" for r in iss_rows)
    [pdf] = [r for r in rows if r.probe == "sber_pdf"]
    assert not pdf.ok and pdf.status == "404", "PDF затравки не отдаётся — строка «НЕТ» с кодом, а не падение"
    [blind] = probe.probe_sber_pdf(getter, url=None)
    assert not blind.ok and "--pdf" in blind.note, "адреса нет нигде — подсказка, а не падение"


# Затравка релизов РСБУ в формате `data/indicators/seed/ras_release.json`: источник числа
# называет адрес PDF релиза. Хосты и числа выдуманы.
PDF_CFG = {"sber_pdf": {"hosts": ["static.test"],
                        "ras_month_patterns": [r"(?P<months>\d{1,2})months(?P<year>\d{4})"]},
           "release_watch": {"tolerance": {"money": 0.15, "share": 0.0005}}}


def _release_url(months: int, year: int = 2001, host: str = "static.test", scheme: str = "https") -> str:
    return f"{scheme}://{host}/files/info/ras_for_{months}months{year}_ru.pdf"


def _seed(*months: tuple[str, str | None]) -> dict:
    """Месяцы затравки: (месяц, адрес PDF в источнике чисел или None — число из новости)."""
    out = {}
    for month, url in months:
        src = f"первичный: PDF релиза (OCR): {url} | sha256 abc | стр. 3" if url else "Интерфакс, заметка дня"
        out[month] = {"release_date": None, "values": {"nii_ytd": {"v": 200.5, "src": src},
                                                       "np_ytd": {"v": 90.25, "src": src},
                                                       "fee_ytd": {"v": 40.0, "src": "Интерфакс, заметка дня"},
                                                       "n1_0": {"v": 0.1310, "src": src}}}
    return {"basis": "ras", "months": out}


def test_the_pdf_address_comes_from_the_seed_on_a_fresh_server(probe, tmp_path):
    """Свежий сервер: дозор PDF ещё не находил, `--pdf` не задан — адрес берётся из затравки:
    самый поздний месяц с адресом на хосте эмитента по https. Чужой хост и http не берутся."""
    seed = _seed(("2001M07", _release_url(7)), ("2001M08", _release_url(8)), ("2001M09", None),
                 ("2001M10", _release_url(10, host="mirror.example")),
                 ("2001M11", _release_url(11, scheme="http")))
    assert probe.seed_pdf_url(PDF_CFG, seed) == (_release_url(8), "2001M08")
    empty = tmp_path / "empty-state"
    assert probe.pdf_address(PDF_CFG, state=empty, seed=seed) == (
        _release_url(8), "адрес из затравки (релиз за 2001M08)", "2001M08")
    assert probe.seed_pdf_url(PDF_CFG, _seed(("2001M09", None))) is None
    assert probe.pdf_address(PDF_CFG, state=empty, seed={})[:2] == (None, "адреса нет")
    # Адрес дозора новее затравки, `--pdf` важнее обоих.
    watched = tmp_path / "state"
    (watched / "sber_pdf").mkdir(parents=True)
    (watched / "sber_pdf" / "index.json").write_text(json.dumps({_release_url(9): {}}), encoding="utf-8")
    assert probe.pdf_address(PDF_CFG, state=watched, seed=seed)[:2] == (_release_url(9), "адрес дозора (состояние)")
    assert probe.pdf_address(PDF_CFG, pdf="https://static.test/x.pdf", state=watched, seed=seed)[:2] == (
        "https://static.test/x.pdf", "адрес из --pdf")


@pytest.mark.tact
def test_the_probe_passes_on_the_empty_state_of_a_fresh_server(monkeypatch, tmp_path, capsys):
    """Приёмка установки: проба без аргументов на пустом состоянии кончается кодом 0, когда
    источники исправны, — адрес PDF эмитента берётся из затравки репозитория (настоящей), и
    строка таблицы говорит, откуда он. Раньше строка «НЕТ» давала код 1 на любом свежем сервере."""
    from tests.support_ind import FakeGetter, use_fixtures
    from indicators import config

    state = use_fixtures(monkeypatch, tmp_path, token=True)
    probe = _load("probe")
    assert not state.exists(), "состояние пусто: дозор ничего не находил"
    seeded = probe.seed_pdf_url(config.sources())
    assert seeded is not None, "в затравке data/indicators/seed/ras_release.json нет адреса PDF релиза"
    url, month = seeded
    getter = FakeGetter(overrides={"dataservice": b'[{"id": 1, "category_name": "x"}]', url: b"%PDF-1.7 fixture"})
    rows = probe.run(getter=getter, tls=_fake_tls)
    assert all(r.ok for r in rows), [(r.probe, r.what, r.note) for r in rows if not r.ok]
    [pdf] = [r for r in rows if r.probe == "sber_pdf"]
    assert f"адрес из затравки (релиз за {month})" in pdf.note and pdf.target in url
    assert "sber_ocr" not in {r.probe for r in rows}, "распознавание — только по имени"
    monkeypatch.setattr(probe, "run", lambda only, pdf=None: rows)
    assert probe.main([]) == 0
    assert f"проб {len(rows)}, прошли {len(rows)}" in capsys.readouterr().out


def _ocr(probe, analyse, *, seed=None, url=None, month="2001M08"):
    from tests.support_ind import FakeGetter

    getter = FakeGetter(overrides={"static.test": b"%PDF-1.7 fixture"})
    [row] = probe.probe_sber_ocr(getter, url=url or _release_url(8), origin="адрес из затравки (релиз за 2001M08)",
                                 month=month, cfg=PDF_CFG, seed=seed or _seed(("2001M08", _release_url(8))),
                                 analyse=analyse)
    return row


def test_the_recognition_smoke_check_compares_with_the_accepted_month(probe):
    """Дымовая проверка распознавания до таймеров: PDF уже опубликованного релиза проходит
    дорогой дозора, распознанные ЧПД и ЧП сверяются с принятыми числами месяца (допуск —
    из конфигурации сборщиков). Сама распознавалка здесь подставная: проверяется сверка."""
    good = {"status": "ok", "method": "ocr", "ocr_seconds": 41.5,
            "numbers": {"nii_ytd": 200.6, "np_ytd": 90.25, "n1_0": 0.1312}}
    row = _ocr(probe, lambda body, kind: good if (body[:5], kind) == (b"%PDF-", "ras_release") else {})
    assert row.ok and row.probe == "sber_ocr", row.note
    for part in ("месяц 2001M08", "способ ocr (41.5 с)", "nii_ytd 200.6 (принято 200.5)", "np_ytd 90.25 (принято 90.25)",
                 "сверено строк 3", "адрес из затравки"):
        assert part in row.note, (part, row.note)
    assert "расходятся" not in row.note

    # Месяц не передан — берётся из имени файла по шаблонам конфигурации.
    row = _ocr(probe, lambda body, kind: good, month=None)
    assert row.ok and "месяц 2001M08" in row.note

    wrong = {"status": "ok", "method": "ocr", "numbers": {"nii_ytd": 200.5, "np_ytd": 9.25}}
    row = _ocr(probe, lambda body, kind: wrong)
    assert not row.ok and "расходятся: np_ytd" in row.note, row.note

    # Расхождение второстепенной строки названо, но проверку не валит: приём числа — по двум источникам.
    side = {"status": "ok", "method": "ocr", "numbers": {"nii_ytd": 200.5, "np_ytd": 90.25, "n1_0": 0.2}}
    row = _ocr(probe, lambda body, kind: side)
    assert row.ok and "расходятся: n1_0" in row.note, row.note

    partial = {"status": "ok", "method": "ocr", "numbers": {"nii_ytd": 200.5}}
    row = _ocr(probe, lambda body, kind: partial)
    assert not row.ok and "не распознаны: np_ytd" in row.note, row.note


def test_the_recognition_smoke_check_names_what_is_missing(probe):
    manual = {"status": "needs_manual", "detail": "PDF без текстового слоя, tesseract не установлен", "numbers": {}}
    row = _ocr(probe, lambda body, kind: manual)
    assert not row.ok and "needs_manual" in row.note and "tesseract не установлен" in row.note

    def boom(body, kind):
        raise RuntimeError("pdfium")

    row = _ocr(probe, boom)
    assert not row.ok and "RuntimeError" in row.note, "отказ распознавалки — строка «НЕТ», а не падение пробы"

    row = _ocr(probe, lambda body, kind: manual, seed=_seed(("2001M07", _release_url(7))))
    assert not row.ok and "нет принятых чисел месяца 2001M08" in row.note

    [row] = probe.probe_sber_ocr(None, url=None, origin="адреса нет", month=None, cfg=PDF_CFG, seed={})
    assert not row.ok and "--pdf" in row.note


def test_the_recognition_smoke_check_is_run_by_name_only(probe, monkeypatch):
    assert "sber_ocr" not in probe.PROBES and probe.EXTRA_PROBES == ("sber_ocr",)
    seen = []
    monkeypatch.setattr(probe, "run", lambda only, pdf=None: seen.append(only) or [
        probe.Row("sber_ocr", probe.OCR_WHAT, "static.test/x.pdf", True, "200", 41.0, 10, "ок")])
    assert probe.main(["--only", "sber_ocr"]) == 0 and seen == [["sber_ocr"]]


def test_the_probe_never_prints_the_token(probe, monkeypatch, capsys):
    from tests.support_ind import FAKE_TOKEN, FakeGetter

    monkeypatch.setenv("TINVEST_TOKEN", FAKE_TOKEN)
    rows = probe.run(["tinvest"], getter=FakeGetter(), tls=_fake_tls)
    assert all(r.ok for r in rows), [r.note for r in rows]
    text = probe.table(rows) + json.dumps([r.__dict__ for r in rows], ensure_ascii=False)
    assert FAKE_TOKEN not in text


def test_the_probe_command_line(probe, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(probe, "run", lambda only, pdf=None: [
        probe.Row("iss", "котировки", "iss.moex.com/x", True, "200", 0.1, 10, "ок")])
    assert probe.main(["--only", "iss", "--json", str(tmp_path / "p.json")]) == 0
    assert "проб 1, прошли 1" in capsys.readouterr().out
    assert json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))[0]["probe"] == "iss"
    assert probe.main(["--only", "нет_такой"]) == 64
