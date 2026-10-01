"""docs/INDICATORS.md не расходится с кодом: команды, горизонты, пороги правила допуска, режимы;
сборщик T-Invest описан словами — имена методов и полей ответа живут только в коде (W3b, P1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from indicators import collect, journal, sources

pytestmark = pytest.mark.docs

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "INDICATORS.md"


def _ru(x: float) -> str:
    return str(x).replace(".", ",")


def test_every_command_and_mode_is_documented():
    text = DOC.read_text(encoding="utf-8")
    parser = collect.build_parser()
    commands = next(a for a in parser._actions if a.dest == "mode").choices
    for name in commands:
        assert f"`{name}" in text, name
    for name in sources.COLLECTORS:
        assert f"`{name}`" in text, name


def test_admission_rule_numbers_match_the_journal():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert f"не раньше {journal.ADMISSION_MIN_EVENTS} событий" in text
    assert f"не больше {_ru(journal.ADMISSION_MAX_MSE_RATIO)}" in text
    assert f"больше {_ru(journal.DEMOTION_MSE_RATIO)}" in text
    for h in journal.HORIZONS:
        assert f"`{h}`" in text
    for t in journal.TARGETS:
        assert f"`{t}`" in text


def test_ocr_limits_and_tile_codes_match_the_code():
    """№ 90, № 98: потолки распознавания и коды плиток в документе — те же, что в коде."""
    from indicators import outputs, sber_pdf

    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert f"за {sber_pdf.OCR_PAGE_TIMEOUT_S:.0f} с на страницу (`OCR_PAGE_TIMEOUT_S`)" in text
    assert f"в {sber_pdf.OCR_BUDGET_S:.0f} с (`OCR_BUDGET_S`)" in text
    assert "Качество `tesseract` на этих PDF не проверено" not in text          # правило фактическое, не намерение
    for code in (*outputs.TILE_UNITS, *outputs.TILE_BASES):
        assert f"`{code}`" in text, code
    assert "`RUB bn`" not in text and "`collector.alarm`" in text and "`form102_latest_est`" in text


def _tinvest_api_names() -> set[str]:
    """Имена методов и полей ответа T-Invest — те, что называет код сборщика (и поле даты объявления,
    которое он намеренно не читает)."""
    code = (ROOT / "indicators" / "tinvest.py").read_text(encoding="utf-8")
    methods = set(re.findall(r'"\w+Service/(\w+)"', code))
    fields = set(re.findall(r'\.get\("([a-z]+[A-Z]\w*)"', code)) | set(re.findall(r'\["([a-z]+[A-Z]\w*)"\]', code))
    return methods | fields | {"declaredDate"}


def test_the_tinvest_collector_is_described_in_words():
    """W3b, P1: в документе и данных индикаторов нет имён методов и полей ответа T-Invest — источник
    назван словами («календарь дивидендов», «дивиденд на акцию до налога»); имена остаются в коде."""
    names = _tinvest_api_names()
    assert {"GetDividends", "GetLastPrices", "ShareBy", "dividendNet", "recordDate", "lastBuyDate",
            "paymentDate"} <= names, sorted(names)                       # список взят из кода и не пуст
    files = [DOC, *sorted((ROOT / "data" / "indicators").rglob("*.yaml")),
             *sorted((ROOT / "data" / "indicators").rglob("*.json"))]
    assert len(files) >= 8
    bad = [f"{path.relative_to(ROOT).as_posix()}: {name}" for path in files
           for name in sorted(names) if re.search(rf"\b{name}\b", path.read_text(encoding="utf-8"))]
    assert not bad, "имена методов и полей ответа T-Invest вне кода:\n" + "\n".join(bad)
