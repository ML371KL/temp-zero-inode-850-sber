"""Витрина и функции Pages под Node: синтаксис и поведение (docs/DASHBOARD.md §6, §7).

`node --check` — для web/app.js и функций (модули ES подаются через stdin с
--input-type=module). Поведение двери и фильтра — tests/web/web_functions_check.mjs
(подменённые fetch, Cache API, часы); поведение витрины (адреса, шесть экранов на
синтетическом выпуске, λ = 0 и 1, выпуск без необязательных блоков, плашки, отказы
двери, печать по записям аудита W2) — tests/web/web_app_check.mjs с заглушкой DOM
(tests/web/dom_stub.mjs). Текст экранов без служебных строк (П§0.2) —
tests/web/web_release_text.mjs на фикстуре; на собранном выпуске тот же скрипт
запускает оператор (docs/DASHBOARD.md §7): тесты состояние машины не читают.
Нужен Node.js 18+; без Node тест падает с причиной (маркер ci_only: на сервере
Node может не быть).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.ci_only


def _run(args, stdin=None):
    assert NODE, "нужен Node.js 18+ в PATH: им проверяются web/app.js и функции Pages"
    return subprocess.run([NODE, *args], input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=300)


def test_node_is_recent_enough():
    major = int(_run(["-p", "process.versions.node.split('.')[0]"]).stdout.strip() or 0)
    assert major >= 18, f"нужен Node 18+ (fetch, Request, Response), а стоит {major}"


def test_app_js_parses():
    result = _run(["--check", str(ROOT / "web" / "app.js")])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("rel", ["functions/api/model.js", "functions/_middleware.js"])
def test_functions_parse_as_modules(rel):
    result = _run(["--input-type=module", "--check"], stdin=(ROOT / rel).read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stderr


def _report(result):
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout[-2000:] + result.stderr[-2000:]}


def test_functions_behave():
    result = _run([str(ROOT / "tests" / "web" / "web_functions_check.mjs")])
    report = _report(result)
    assert result.returncode == 0 and report.get("total", 0) > 40, json.dumps(report, ensure_ascii=False)[:4000]


def test_app_behaves():
    result = _run([str(ROOT / "tests" / "web" / "web_app_check.mjs")])
    report = _report(result)
    assert result.returncode == 0 and report.get("total", 0) > 100, json.dumps(report, ensure_ascii=False)[:4000]


def _release_text(*args):
    result = _run([str(ROOT / "tests" / "web" / "web_release_text.mjs"), *args])
    report = _report(result)
    lines = [f"{f['screen']}: {f['rule']} — «{f.get('hit', '')}» в «…{f['sample']}…»" for f in report.get("failed", [])]
    return result, report, "\n".join(lines) or json.dumps(report, ensure_ascii=False)[:2000]


def test_fixture_screens_carry_no_service_text():
    """Шесть экранов фикстуры, с раскрытыми таблицами: ни имён полей и ключей, ни true/false, inf, кодов периодов,
    дат ISO, десятичной точки в процентах, «п.п..» (П§0.2, AUDIT-1 № 62). На собранном выпуске — тот же скрипт
    с путём к файлу: приёмка текстов ядра, фактов и индикаторов на экране."""
    result, report, problems = _release_text()
    assert result.returncode == 0 and report.get("screens") == 6 and report.get("failed") == [], problems

