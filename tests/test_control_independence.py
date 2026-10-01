# -*- coding: utf-8 -*-
"""Независимость контрольной модели от ядра (docs/MODEL.md §17, INTERFACES §9).

* tests/independent_model.py импортирует только стандартную библиотеку и PyYAML, не называет
  каталог ядра ни в одной строке кода и не открывает его файлов во время прогона;
* у контрольной модели и ядра нет общих имён функций и классов и нет одинаковых (с точностью до
  имён переменных) тел функций — «другой шаг ловит общий код» только если кода общего нет;
* ядро не импортирует контрольную модель;
* в коде контрольной модели нет чисел книги и фактов — только структурные константы.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Тесты такта (INTERFACES §9, W1/C1): независимость — быстрая проверка потока control.
pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "tests" / "independent_model.py"
CORE_DIR = ROOT / "model"

ALLOWED_IMPORTS = {"__future__", "argparse", "copy", "datetime", "decimal", "json", "math", "sys",
                   "pathlib", "yaml"}
FORBIDDEN_CALLS = {"__import__", "exec", "eval", "compile", "import_module"}

# Числа-литералы контрольной модели и почему они не допущения книги.
STRUCTURAL = {
    0: "ноль", 1: "единица; первый квартал", 2: "половина (средний остаток); полугодий в году",
    3: "месяцев в квартале", 4: "кварталов в году; длина ключа года «2026»",
    0.25: "доля года в квартале (линейка дисконта, подтягивание FVOCI, М§0.2, §4.9)",
    0.5: "½ в правдоподобии A-P2u (М§12); середина квартала выплаты в среднем остатке (М§4.10)",
    60: "итераций фиксированной точки баланса",
    100: "проценты записи миров → доли (М§3.1)", 365: "act/365 (М§0.3)",
    1000: "млрд → млн (цена и DPS на акцию)", 0.0001: "защита g_T = k_T − 0,0001 (М§7)",
    1e-11: "сходимость фиксированной точки", 1e-12: "допуск предела сдвига A-P2u",
    1e-9: "допуск суммы долей кварталов «прочего» = 1 (М прил. A)",
}


def _tree() -> ast.Module:
    return ast.parse(CONTROL.read_text(encoding="utf-8"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _core_files() -> list[Path]:
    return sorted(CORE_DIR.glob("*.py")) if CORE_DIR.exists() else []


def test_control_imports_only_stdlib_and_yaml():
    names, calls = set(), set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.add("." * node.level + (node.module or ""))
            else:
                names.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call):
            fn = node.func
            calls.add(fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else "")
    assert names <= ALLOWED_IMPORTS, f"контрольная модель импортирует {sorted(names - ALLOWED_IMPORTS)}"
    assert not (calls & FORBIDDEN_CALLS), f"динамический импорт или исполнение: {sorted(calls & FORBIDDEN_CALLS)}"


def test_control_code_never_names_the_core_directory():
    """Ни одна строка кода (кроме докстрингов) не указывает на каталог ядра."""
    tree = _tree()
    docs = _docstring_nodes(tree)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            s = node.value.strip().lower().replace("\\", "/")
            if s == "model" or s.startswith(("model/", "model.")) or "/model/" in s:
                bad.append((node.lineno, node.value))
        if isinstance(node, ast.Name) and node.id == "model":
            bad.append((node.lineno, "имя model"))
    assert not bad, f"контрольная модель ссылается на ядро: {bad}"


def _defs(tree: ast.AST) -> dict[str, ast.AST]:
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def test_no_shared_function_or_class_names_with_core():
    files = _core_files()
    if not files:
        pytest.skip("каталога ядра ещё нет — сравнивать имена не с чем")
    mine = {k for k in _defs(_tree()) if not (k.startswith("__") and k.endswith("__"))}
    shared = {}
    for path in files:
        for name in _defs(ast.parse(path.read_text(encoding="utf-8"))):
            if name in mine:
                shared.setdefault(name, []).append(path.name)
    assert not shared, f"общие имена функций/классов с ядром: {shared}"


class _Normalize(ast.NodeTransformer):
    """Имена переменных и аргументов → номера по порядку появления (копия с переименованием
    переменных остаётся копией)."""

    def __init__(self):
        self.map: dict[str, str] = {}

    def _n(self, name: str) -> str:
        return self.map.setdefault(name, f"v{len(self.map)}")

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id=self._n(node.id), ctx=node.ctx), node)

    def visit_arg(self, node):
        return ast.copy_location(ast.arg(arg=self._n(node.arg), annotation=None), node)


def _bodies(tree: ast.AST, min_stmts: int = 5) -> dict[str, str]:
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stmts = [n for n in ast.walk(node) if isinstance(n, ast.stmt)]
            if len(stmts) < min_stmts:
                continue
            body = [s for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            mod = ast.parse(ast.unparse(ast.Module(body=body, type_ignores=[])))
            out[ast.dump(_Normalize().visit(mod), annotate_fields=False)] = node.name
    return out


def test_no_copied_function_bodies_from_core():
    files = _core_files()
    if not files:
        pytest.skip("каталога ядра ещё нет — сравнивать тела функций не с чем")
    mine = _bodies(_tree())
    copies = []
    for path in files:
        for dump, name in _bodies(ast.parse(path.read_text(encoding="utf-8"))).items():
            if dump in mine:
                copies.append(f"{path.name}:{name} = independent_model:{mine[dump]}")
    assert not copies, f"одинаковые тела функций у ядра и контрольной модели: {copies}"


def test_core_does_not_import_control_model():
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(m.split(".")[0] == "tests" or "independent_model" in m for m in mods), \
                f"{path.name} импортирует контрольную модель"


def test_control_has_no_book_or_fact_numbers():
    tree = _tree()
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            found.setdefault(abs(node.value), []).append(node.lineno)
    extra = {v: lines for v, lines in found.items() if v not in STRUCTURAL}
    assert not extra, f"числа в контрольной модели вне структурных констант: {extra}"


_PROBE = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
core = (root / "model").resolve()
opened = []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        p = Path(args[0] if isinstance(args[0], str) else args[0].decode()).resolve()
        if core == p or core in p.parents:
            opened.append(str(p))
sys.addaudithook(hook)
sys.path.insert(0, str(root / "tests"))
import independent_model as cm
try:
    book, facts = cm.c_book_machine(), cm.c_facts_bundle()
except cm.ControlInputError as exc:
    print(json.dumps({"skip": str(exc)})); sys.exit(0)
cm.c_control_run(book, facts)
mods = sorted(m for m in sys.modules if m == "model" or m.startswith("model."))
print(json.dumps({"opened": opened, "modules": mods}))
"""


def test_control_run_touches_no_core_file():
    """Прогон контрольной модели в отдельном процессе: ни одного открытого файла каталога ядра и ни
    одного загруженного модуля ядра (аудит-хук интерпретатора)."""
    out = subprocess.run([sys.executable, "-B", "-c", _PROBE, str(ROOT)], capture_output=True,
                         text=True, encoding="utf-8", timeout=600, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    if "skip" in res:
        pytest.skip(f"нет книги или фактов: {res['skip']}")
    assert res["opened"] == [], f"контрольная модель открыла файлы ядра: {res['opened']}"
    assert res["modules"] == [], f"контрольная модель загрузила модули ядра: {res['modules']}"
