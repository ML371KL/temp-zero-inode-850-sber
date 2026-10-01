# -*- coding: utf-8 -*-
"""Такт сервера проверяет каждый поток (решение ведущего 01.10.2026, C1).

`ops/run.sh` перед каждой сборкой гоняет белый список — тесты с меткой `tact`
(выражение `TACT_TESTS`). Пересборка публикует новый код `main` через 15–25
минут после push, раньше, чем CI успеет покраснеть, поэтому на сервере обязаны
идти быстрые тесты КАЖДОГО потока: ядро, книга, факты, индикаторы, витрина,
контрольная модель, перезаякоривание, эксплуатация (INTERFACES §9). Поток без
тестов такта — дыра: его поломка уходит на витрину непроверенной.

Сроки данных в набор сервера не входят (решение ведущего В17, INTERFACES §9):
тест срока несёт метку `deadline`, выражение такта её исключает. Истёкшее
объяснение сработавшего гейта останавливает сама сборка (код 1), об истекающем
сообщает плашка выпуска, истёкший документ эмитента — тоже плашка. Поэтому набор
сервера обязан быть зелёным на любой будущей дате: прогон «в будущем» (сервер по
понедельникам) краснеет только от теста, привязанного к дате, — это дефект теста.

Метки читаются статически (ast): метка модуля (`pytestmark`, одна или
списком), класса (декоратор и `pytestmark` в теле) и функции — в том числе через
имя-псевдоним модуля (`tact = pytest.mark.tact`, затем `@tact`). Выражение такта
вычисляется так же, как его вычислит pytest на сервере. Время всего набора: цель
сервера — 3 минуты (сверяется замером шага в журнале такта), шаг CI «Тесты такта —
замер времени» краснеет после 300 с (`ops/budgets.json`).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
RUN_SH = ROOT / "ops" / "run.sh"

# Поток → префиксы его тестовых файлов (INTERFACES §2 и раздел W2).
STREAMS = {
    "core": ("test_core_", "test_core2_", "test_integrate_"),
    "book": ("test_book_",),
    "facts": ("test_facts_",),
    "indicators": ("test_ind_",),
    "web": ("test_web_",),
    "control": ("test_control_",),
    "reanchor": ("test_reanchor",),
    "ops": ("test_ops_", "test_public_hygiene"),
}
MARK = re.compile(r"\bmark\.(\w+)")


def tact_expression() -> str:
    """Выражение такта из ops/run.sh — то, что сервер отдаёт `pytest -m`."""
    found = re.findall(r'^TACT_TESTS="([^"]+)"$', RUN_SH.read_text(encoding="utf-8"), re.M)
    assert len(found) == 1, found
    return found[0]


def selected(expression: str, marks: set[str]) -> bool:
    """Выберет ли `pytest -m <expression>` тест с этими метками (имена, and/or/not, скобки)."""
    tokens = re.findall(r"\(|\)|[A-Za-z_]\w*", expression)
    assert "".join(tokens) == re.sub(r"\s+", "", expression), f"непонятное выражение: {expression}"
    words = [t if t in ("(", ")", "and", "or", "not") else str(t in marks) for t in tokens]
    return bool(eval(" ".join(words), {"__builtins__": {}}, {}))  # noqa: S307 — только True/False и логика


def mark_aliases(tree: ast.Module) -> dict[str, str]:
    """Имена модуля, за которыми стоит метка: `tact = pytest.mark.tact` → {"tact": "tact"}."""
    aliases = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found = re.fullmatch(r"pytest\.mark\.(\w+)", ast.unparse(node.value))
            if found and node.targets[0].id != "pytestmark":
                aliases[node.targets[0].id] = found.group(1)
    return aliases


def _marks_of(nodes, aliases: dict[str, str]) -> set[str]:
    marks = set()
    for node in nodes:
        marks |= set(MARK.findall(ast.unparse(node)))
        marks |= {aliases[n.id] for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in aliases}
    return marks


def _pytestmark(body, aliases: dict[str, str]) -> set[str]:
    return _marks_of((node.value for node in body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)), aliases)


def collect_tests(path: Path):
    """(имя теста, его метки, узел функции) для всех тестов файла — функции модуля
    и методы классов `Test*`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = mark_aliases(tree)
    module = _pytestmark(tree.body, aliases)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node.name, module | _marks_of(node.decorator_list, aliases), node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            cls = module | _marks_of(node.decorator_list, aliases) | _pytestmark(node.body, aliases)
            for fn in node.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith("test"):
                    yield f"{node.name}::{fn.name}", cls | _marks_of(fn.decorator_list, aliases), fn


def stream_of(name: str) -> str | None:
    for stream, prefixes in STREAMS.items():
        if name.startswith(prefixes):
            return stream
    return None


def tact_tests_by_stream() -> dict[str, list[str]]:
    expression = tact_expression()
    found: dict[str, list[str]] = {stream: [] for stream in STREAMS}
    for path in sorted(TESTS.glob("test_*.py")):
        stream = stream_of(path.name)
        if stream is None:
            continue
        found[stream] += [f"{path.name}::{name}" for name, marks, _ in collect_tests(path)
                          if selected(expression, marks)]
    return found


def test_the_selection_reads_marks_like_pytest():
    """Проверка самого счётчика: выражение и метки модуля, класса, функции."""
    expression = "tact and not network and not archive and not docs and not ci_only and not deadline"
    assert selected(expression, {"tact"}) and selected(expression, {"tact", "parametrize"})
    assert not selected(expression, {"tact", "ci_only"}) and not selected(expression, {"docs"})
    assert not selected(expression, {"tact", "deadline"})
    assert selected("(docs or tact) and not network", {"docs"})
    assert not selected("tact and __import__", {"tact"}), "имя — всегда метка, не код"
    with pytest.raises(AssertionError):
        selected("tact; x", {"tact"})


def test_marks_are_read_through_module_aliases(tmp_path):
    """Метка, поставленная именем модуля (`tact = pytest.mark.tact`; `@tact`), — та же метка:
    иначе поток с такой записью выглядел бы пустым в такте, а пара `tact` + `ci_only` — законной."""
    source = (
        "import pytest\n"
        "tact = pytest.mark.tact\nslow = pytest.mark.ci_only\n"
        "@tact\ndef test_a(): pass\n"
        "@pytest.mark.parametrize('x', [1])\n@slow\ndef test_b(x): pass\n"
        "def test_c(): pass\n"
        "class TestD:\n    pytestmark = [tact]\n    def test_e(self): pass\n")
    path = tmp_path / "test_sample.py"
    path.write_text(source, encoding="utf-8")
    assert {name: marks for name, marks, _ in collect_tests(path)} == {
        "test_a": {"tact"}, "test_b": {"ci_only", "parametrize"}, "test_c": set(), "TestD::test_e": {"tact"}}


def test_every_test_file_belongs_to_a_stream():
    """Файл тестов без потока выпал бы из подсчёта такта (и из договора владения)."""
    orphans = sorted(p.name for p in TESTS.glob("test_*.py") if stream_of(p.name) is None)
    assert not orphans, f"файлы тестов вне потоков INTERFACES §2: {orphans}"


def test_the_tact_has_tests_of_every_stream():
    """В белом списке такта есть тесты каждого потока (C1). Пустой поток —
    пометить `@pytest.mark.tact` его быстрые тесты (INTERFACES §9: что именно)."""
    found = tact_tests_by_stream()
    empty = sorted(stream for stream, tests in found.items() if not tests)
    assert not empty, ("в такте сервера нет тестов потоков: " + ", ".join(empty)
                       + " — пометить быстрые тесты меткой tact (весь такт ≤ 3 мин)")


def test_deadline_tests_stay_out_of_the_server_tact():
    """Сроки данных в набор сервера не входят (В17, INTERFACES §9): выражение такта
    исключает метку `deadline` — тест с ней сервер не выберет, даже если он несёт и `tact`
    (тогда его берёт только `pytest -m tact` руками). Иначе тест срока останавливал бы
    суточный такт в день срока, хотя сборка сама отвечает на срок отказом или плашкой."""
    expression = tact_expression()
    assert re.search(r"\bnot deadline\b", expression), expression
    assert not selected(expression, {"tact", "deadline"})
    chosen = [f"{path.name}::{name}" for path in sorted(TESTS.glob("test_*.py"))
              for name, marks, _ in collect_tests(path) if "deadline" in marks and selected(expression, marks)]
    assert not chosen, chosen


def test_the_deadline_mark_is_only_for_tests_of_data_terms():
    """Метка `deadline` — у тестов данных со сроком (объяснения гейтов книги, записи
    фактов), а не способ вывести из такта любой тест, привязанный к дате: такой тест
    чинится, а не метится."""
    marked = {path.name for path in sorted(TESTS.glob("test_*.py"))
              for _, marks, _ in collect_tests(path) if "deadline" in marks}
    assert all(stream_of(name) in ("book", "facts") for name in marked), sorted(marked)
