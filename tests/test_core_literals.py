"""Литералы в `model/`: нет годов, дат, чисел книги и фактов, имени, тикеров и регномера эмитента (М§0.6, §18).

Годы — сторож по образцу Ленты (`test_no_year_literals`): ни одного целого 2000…2100 вне
комментариев и строк (разбор AST). Числа книги и фактов — по образцу Ленты
(`test_no_literals`): дробные и крупные литералы кода сверяются со всеми числами книги
и фактов; совпадение разрешено только названным литералом ПО ФАЙЛУ И ЗНАЧЕНИЮ с
причиной (технические константы: четверть года, допуски, коридор массы гейта).
Модуль другого потока может назвать свои технические литералы словарём
`NAMED_LITERALS = {значение: "причина"}` в самом модуле — список виден в диффе.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from tests.support_core import FIXTURE_BOOK, FIXTURE_FACTS, REAL_BOOK, REAL_FACTS, real_facts_ready

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "model"
YEARS = range(2000, 2101)

# (файл, значение) → почему это не второй экземпляр допущения.
NAMED_LITERALS: dict[tuple[str, float], str] = {
    ("timeline.py", 0.25): "QUARTER_YEARS: четверть года на линейке дисконта (М§0.2)",
    ("checks.py", 1e-09): "REL_TOL: относительный допуск тождеств BV, баланса и ОПУ (М§14.1)",
    ("checks.py", 0.4): "MASS_LOW: нижний множитель коридора ожидаемой массы гейта (М§14.2)",
    ("checks.py", 0.02): "MASS_LOW/MASS_HIGH: прибавка ±0,02 коридора массы гейта (М§14.2)",
    ("checks.py", 1.5): "MASS_HIGH: верхний множитель коридора массы гейта (М§14.2)",
    ("dividends.py", 1e-09): "допуск сравнения base_div с пулом (флаг dividend_cut)",
    ("book_schema.py", 1e-09): "допуск правила «ключ года = среднее кварталов» (М§0.4)",
    ("valuation.py", 0.0001): "G_BELOW_K: защита роста терминала g_T = k_T − 1 б.п. (М§7)",
    ("grid.py", 0.5): "показатель гауссова правдоподобия A-P2u: exp(−½ × error² / дисперсия) (М§12)",
    ("grid.py", 1e-09): "SIGN_TOL: «не снижает точку» теста знака — с точностью счёта, ₽ (М§4.5)",
}

# Мелочь, которая встречается и в книге, и в любой арифметике. Половины (0,5) здесь нет: у книги десяток
# ключей, равных 0,5 (λ, ε, доли), — она разрешается только по файлу и значению с причиной.
TRIVIAL = {0.0, 1.0, 2.0}


def _numbers(value, out: set) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 9))
    elif isinstance(value, dict):
        for k, v in value.items():
            _numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers(v, out)


def _sources():
    book_path = REAL_BOOK if REAL_BOOK.exists() else FIXTURE_BOOK
    facts_dir = REAL_FACTS if real_facts_ready() else FIXTURE_FACTS
    text = book_path.read_text(encoding="utf-8")
    book = json.loads(text) if book_path.suffix == ".json" else yaml.safe_load(text)
    facts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(facts_dir.glob("*.json"))]
    return book, facts


@pytest.fixture(scope="module")
def source_numbers() -> set:
    book, facts = _sources()
    out: set = set()
    _numbers(book, out)
    for f in facts:
        _numbers(f, out)
    return {v for v in out if v not in TRIVIAL and v != 0}


def _files():
    return sorted(p for p in MODEL.glob("*.py"))


def _module_named(tree: ast.AST) -> dict[float, str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAMED_LITERALS"
                                                for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return {}
            return {round(float(k), 9): str(v) for k, v in value.items()}
    return {}


def year_literals(source: str) -> list[tuple[int, int]]:
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value in YEARS:
            out.append((node.lineno, node.value))
    return out


def literals_of(path: Path) -> list[tuple[int, float, str]]:
    """Дробные и крупные (≥ 10 000) числовые литералы файла."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and not isinstance(node.value, bool) \
                and isinstance(node.value, (int, float)):
            value = round(float(node.value), 9)
            if value != int(value) or abs(value) >= 10_000:
                out.append((node.lineno, value, lines[node.lineno - 1].strip()))
    return out


def test_model_has_no_year_literals():
    found = {p.name: year_literals(p.read_text(encoding="utf-8")) for p in _files()}
    found = {k: v for k, v in found.items() if v}
    assert not found, f"литералы года в model/*.py: {found} — год правила обязан быть ключом книги"


def test_year_scanner_sees_a_year_but_not_comments_or_strings():
    source = ('"""Докстрока про 2026 год."""\n'
              "x = 1 if year <= 2027 else 2   # комментарий 2028\n"
              "label = '2029'\n"
              "flag, share = True, 2030.5\n")
    assert year_literals(source) == [(2, 2027)]


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_book_or_fact_number_is_retyped(path, source_numbers):
    named = _module_named(ast.parse(path.read_text(encoding="utf-8")))
    offenders = []
    for lineno, value, line in literals_of(path):
        if value in source_numbers and (path.name, value) not in NAMED_LITERALS and value not in named:
            offenders.append(f"{path.name}:{lineno}: {value} — {line[:90]}")
    assert not offenders, ("эти числа есть в книге или фактах и должны читаться оттуда; если это "
                           "параметр проверки или арифметики — назовите его в NAMED_LITERALS:\n"
                           + "\n".join(offenders))


def test_the_scanner_sees_one_half():
    """0,5 — число книги (λ, ε): захардкоженная половина без названной причины — находка, а не «мелочь»."""
    assert 0.5 not in TRIVIAL
    from model import live, payload
    assert 0.5 in live.NAMED_LITERALS and 0.5 in payload.NAMED_LITERALS and ("grid.py", 0.5) in NAMED_LITERALS


def test_every_named_literal_still_exists():
    actual = {(p.name, v) for p in _files() for _, v, _ in literals_of(p)}
    stale = [k for k in NAMED_LITERALS if k not in actual]
    assert not stale, f"разрешения ни к чему не относятся: {stale}"


def test_no_issuer_name_tickers_or_regnum_in_model():
    book, _ = _sources()
    company = book["meta"]["company"]
    words = [str(company["name"])] + [str(t) for t in company["tickers"]] + [str(company["cbr_regnum"])]
    stem = str(company["name"])[:4]
    offenders = []
    for p in _files():
        text = p.read_text(encoding="utf-8")
        for w in words:
            if re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", text):
                offenders.append(f"{p.name}: «{w}»")
        if re.search(re.escape(stem), text, flags=re.IGNORECASE):
            offenders.append(f"{p.name}: «{stem}…»")
    assert not offenders, "литералы эмитента в model/: " + ", ".join(offenders)


def test_dates_are_not_hardcoded():
    offenders = []
    for p in _files():
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "date" \
                    and node.args and isinstance(node.args[0], ast.Constant):
                offenders.append(f"{p.name}:{node.lineno}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{4}Q[1-4]|\d{4}H[12]", node.value):
                offenders.append(f"{p.name}:{node.lineno}: {node.value}")
    assert not offenders, "даты и периоды в коде ядра: " + ", ".join(offenders)


ENV_ALLOWED = {"BANK_STATE_DIR", "BANK_DATA_REPO_DIR", "BANK_WORKERS", "GITHUB_SHA"}


def test_environment_names_have_no_bank_prefix():
    """Переменные окружения ядра — без префикса банка (М§0.6, INTERFACES §8; W1/C5): копия ядра
    под другой банк меняет только книгу и факты. Имя тикера — и как префикс (`<тикер>_…`) — запрещено."""
    book, _ = _sources()
    tickers = [str(t) for t in book["meta"]["company"]["tickers"]]
    names, prefixed = set(), []
    for p in _files():
        text = p.read_text(encoding="utf-8")
        for t in tickers:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}_", text):
                prefixed.append(f"{p.name}: «{t}_…»")
        for node in ast.walk(ast.parse(text)):
            # имя переменной: константа *_ENV = "…" или литерал в os.environ.get / os.getenv / os.environ[…]
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) \
                    and any(isinstance(t, ast.Name) and t.id.endswith("_ENV") for t in node.targets):
                names.add(node.value.value)
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                    and ast.unparse(node.func) in ("os.environ.get", "os.getenv"):
                names.add(str(node.args[0].value))
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ" \
                    and isinstance(node.slice, ast.Constant):
                names.add(str(node.slice.value))
    assert not prefixed, "префикс банка в model/: " + ", ".join(prefixed)
    assert names <= ENV_ALLOWED, sorted(names - ENV_ALLOWED)
    from model import live, payload, uncertainty
    assert (live.STATE_ENV, payload.DATA_REPO_ENV, uncertainty.WORKERS_ENV) == (
        "BANK_STATE_DIR", "BANK_DATA_REPO_DIR", "BANK_WORKERS")
