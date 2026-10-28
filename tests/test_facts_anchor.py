"""Сборщик фактов: якорь — из аргумента, литералов квартала в коде нет (AUDIT-1 № 9).

Квартал якоря приходит аргументом `--anchor` (по умолчанию — `data/facts/anchor.json`); дата, лист
якоря, нарастающий итог, начало квартала, окно мостов, годы теста DPS выводятся из него и из
данных. В коде сборщика периоды и даты допустимы только в именованных параметрах рядов и
документов — и каждый такой параметр назван в перечне мест, привязанных к якорю (SCHEMA.md §3).
Папка передачи тестам не нужна.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
BUILDER = ROOT / "ops" / "tools" / "build_facts.py"

# Периоды, даты и годы в строках кода: 2026Q2, 2Q2026, 6M2026, FY2025, 2026-06-30, 30.06.2026, «2К26», «6М26».
PERIOD_RE = re.compile(r"(?<![\w.])(?:20\d\dQ[1-4]|[1-4]Q20\d\d|\d{1,2}M20\d\d|FY20\d\d|20\d\d-\d\d-\d\d|"
                       r"\d\d\.\d\d\.20\d\d|[1-4]К\d\d|\d{1,2}М\d\d)(?![\w])")
YEAR_RE = re.compile(r"(?<![\w.,/-])20[1-4]\d(?![\w,/-])")


def builder():
    spec = importlib.util.spec_from_file_location("build_facts_under_test", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


B = builder()


# ------------------------------------------------------------------ якорь и выводимое из него

def test_anchor_derivations():
    a = B.Anchor("2026Q2")
    assert (a.date, a.open_date, a.form_date) == ("2026-06-30", "2026-03-31", "2026-07-01")
    assert (a.ytd, a.ytd_ru, a.k_ru) == ("6M2026", "6М26", "2К")
    assert (a.fy_last, a.fy_last_date, a.prev_year_end) == ("FY2025", "2025-12-31", "2025-12-31")
    assert a.bridge_window == (B.BRIDGE_FIRST, "2026Q2")
    q3 = B.Anchor("2026Q3")
    assert (q3.date, q3.open_date, q3.ytd, q3.fy_last) == ("2026-09-30", "2026-06-30", "9M2026", "FY2025")
    q4 = B.Anchor("2026Q4")                # якорь на годовом отчёте: последний год — год якоря
    assert (q4.date, q4.ytd, q4.fy_last, q4.fy_last_date, q4.prev_year_end) == \
        ("2026-12-31", "FY2026", "FY2026", "2026-12-31", "2025-12-31")
    q1 = B.Anchor("2027Q1")
    assert (q1.open_date, q1.ytd, q1.fy_last, q1.prev_year_end) == ("2026-12-31", "3M2027", "FY2026", "2026-12-31")
    assert B.q_next("2026Q4") == "2027Q1" and B.q_prev("2027Q1") == "2026Q4"
    for bad in ("2026", "2026Q5", "2Q2026", ""):
        with pytest.raises(B.BuildError):
            B.Anchor(bad)


def test_default_anchor_is_the_committed_one():
    assert B.default_anchor() == json.loads((FACTS / "anchor.json").read_text(encoding="utf-8"))["period"]


def test_committed_facts_follow_the_anchor():
    """Все файлы сборщика стоят на одном якоре, а выводимые из него поля — те, что даёт Anchor."""
    load = lambda n: json.loads((FACTS / f"{n}.json").read_text(encoding="utf-8"))   # noqa: E731
    a = B.Anchor(load("anchor")["period"])
    for name in B.BUILT:
        if name != "guidance":             # у гайденса as_of — дата последнего пересмотра
            assert load(name)["as_of"] == a.date, name
    assert load("nii_books")["period"] == a.period
    assert load("bridge_mgmt_ifrs")["window"] == list(a.bridge_window)
    assert max(load("pnl_quarterly")["quarters"]) == a.period == max(load("mgmt_quarterly")["quarters"])
    assert load("balance")["prev_year_end"]["as_of"] == a.prev_year_end
    assert load("capital")["n1_1_bank"]["as_of"] == a.form_date
    assert load("guidance")["year"] == a.year
    hist = load("dividends")["history"]
    assert [r["year"] for r in hist][0] == B.DIV_FIRST_YEAR
    # дивиденды к выплате и флаг «до вычета» — из дат последнего объявленного дивиденда, не литералом
    last = max(hist, key=lambda r: r["year"])
    payable = load("balance")["dividends_payable"]["v"]
    assert (payable > 0) == (a.date < last["pay_date"]), "дивиденды к выплате: выплата после якоря ⇔ обязательство есть"
    record_month = last["record_date"][:7] + "-01"
    assert load("capital")["n20_0"]["pre_dividend"] == (a.date < record_month)


# ------------------------------------------------------------------ литералов квартала в коде нет

def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _module_parameters(tree: ast.Module) -> dict[str, ast.AST]:
    """Именованные параметры модуля: ИМЯ = <литерал> на верхнем уровне."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper():
            out[node.targets[0].id] = node.value
    return out


def _dated(value: ast.AST, skip: set[int]) -> list[str]:
    hits = []
    for node in ast.walk(value):
        if id(node) in skip or not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, str):
            hits += PERIOD_RE.findall(node.value) + YEAR_RE.findall(node.value)
        elif isinstance(node.value, int) and not isinstance(node.value, bool) and 2010 <= node.value <= 2049:
            hits.append(str(node.value))
    return hits


def test_builder_has_no_quarter_literals_outside_named_parameters():
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    params = _module_parameters(tree)
    in_params = {id(n) for v in params.values() for n in ast.walk(v)}
    bad = []
    for node in ast.walk(tree):
        if id(node) in skip or id(node) in in_params or not isinstance(node, ast.Constant):
            continue
        for hit in _dated(node, skip):
            bad.append(f"строка {node.lineno}: {hit!r}")
    assert not bad, "период, дата или год литералом вне именованного параметра сборщика:\n" + "\n".join(bad[:20])


@pytest.mark.docs
def test_schema_lists_every_dated_parameter():
    """Каждый параметр сборщика, несущий период, дату или год, назван в перечне SCHEMA.md §3 — иначе
    при перезаякоривании о нём некому вспомнить."""
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    schema = (FACTS / "SCHEMA.md").read_text(encoding="utf-8")
    section = schema[schema.index("## 3."):schema.index("## 4.")]
    dated = [name for name, value in _module_parameters(tree).items() if _dated(value, set())]
    assert dated, "разбор параметров сборщика ничего не нашёл"
    missing = [n for n in dated if f"`{n}`" not in section]
    assert not missing, f"SCHEMA.md §3 не называет параметры сборщика: {missing}"


# ------------------------------------------------------------------ правила, вынесенные в функции

def test_derived_calc_picks_the_expression_of_the_value():
    note = "[зеркало] выведено: 29,5 − 3,4 и 29,0 − 3,5"
    assert B.derived_calc(note, 26.1).startswith("выведено: 29,5 − 3,4 = 26,1")
    assert B.derived_calc(note, 25.5).startswith("выведено: 29,0 − 3,5 = 25,5")
    with_reason = B.derived_calc("выведено: 6,34 − 0,23 (подпись «+0,23 пп г/г») | ранее: 6.1", 6.11)
    assert with_reason.startswith("выведено: 6,34 − 0,23 = 6,11 (подпись «+0,23 пп г/г»)")
    assert B.derived_calc("выведено из подписей: 29,1 − 0,7 и 27,9 − 0,2", 28.4).startswith("выведено: 29,1 − 0,7 = 28,4")
    assert B.derived_calc("чистая процентная маржа", 6.2) is None
    assert "выведено" in B.derived_calc("2К21 выведено", 41.0)      # вывод без выражения — словами


def test_point_tolerance_from_the_text():
    assert B.point_tol("22%") == 0.005
    assert B.point_tol("~6.2%") == 0.0005
    assert B.point_tol("~0%") == 0.005
    assert B.point_tol("20%") == 0.005, "«20 %» — допуск ±0,5 п.п., а не ±5 п.п. по repr доли 0.2"
    assert B.point_tol("13,35%") == 0.00005
    with pytest.raises(B.BuildError):
        B.point_tol("около нуля")


class _GuidanceSheet:
    """Лист гайденса в памяти: всё, что `build_guidance` читает из контекста сборки."""

    def __init__(self, rows: list[dict], year: int):
        self.rows, self.a = rows, type("A", (), {"year": year})()
        ref = staticmethod(lambda doc, page, sha: f"{doc}, с. {page}, sha256 {sha[:16]}")
        self.docs = type("D", (), {"ref": ref})()

    def csv_rows(self, rel: str) -> list[dict]:
        assert rel == "mgmt/guidance.csv", rel
        return self.rows


def test_a_bound_in_the_text_sets_the_kind_of_the_guidance_node():
    """Вид узла — по тексту строки листа, если в нём граница: «>6.2%» — нижняя граница (`min`, без допуска),
    хотя у ЧПМ по умолчанию точка; пересмотр «~6.2%» → «>6.2%» остаётся в истории тем же числом с прежним
    текстом в calc; у ключа-границы «<1.4%» и у точек — как раньше."""
    def row(metric, as_of, text, low, high, note=""):
        return {"metric": metric, "target_year": "2030", "as_of": as_of, "event": f"отчёт {as_of}", "text": text,
                "low": low, "high": high, "unit": "%", "doc": "p.pdf", "page": "24", "sha256": "ab" * 32, "note": note}
    rows = [row("roe", "2030-04-29", "22%", "22", "22"),
            row("nim", "2030-04-29", "~6.2%", "6.2", "6.2"), row("nim", "2030-07-29", ">6.2%", "6.2", ""),
            row("cor", "2030-04-29", "<1.4%", "", "1.4"), row("cir", "2030-04-29", "~30-32%", "30", "32"),
            row("fee_growth", "2030-04-29", "~0%", "0", "0"), row("n20_0", "2030-04-29", "13.3%", "13.3", "13.3"),
            row("sector_corp_loans", "2030-04-29", "+8-10%", "8", "10", "Сбер — в соответствии с сектором"),
            row("sector_retail_loans", "2030-04-29", "+5-8%", "5", "8", "Сбер — лучше сектора")]
    g = B.build_guidance(_GuidanceSheet(rows, 2030))
    items = g["items"]
    assert items["nim"]["kind"] == "min" and items["nim"]["v"] == 0.062 and "tol" not in items["nim"]
    assert items["nim"]["text"] == ">6.2%" and items["nim"]["date"] == "2030-07-29"
    assert (items["roe"]["kind"], items["roe"]["tol"]) == ("point", 0.005)
    assert (items["cor_max"]["kind"], items["cor_max"]["v"]) == ("max", 0.014) and "tol" not in items["cor_max"]
    assert (items["n20_0"]["kind"], items["fee_growth"]["kind"], items["cir"]["kind"]) == ("min", "point", "range")
    nim = [h["value"] for h in g["history"] if h["key"] == "nim"]
    assert [(n["v"], n["calc"]) for n in nim] == [(0.062, "«~6.2%» → доли"), (0.062, "«>6.2%» → доли")]
    assert B.guidance_kind("≤49%", "point") == "max" and B.guidance_kind("≥ 13.3%", "point") == "min"
    assert B.guidance_kind("~6.2%", "point") == "point" and B.guidance_kind("+8-10%", "range") == "range"


def test_policy_term_is_added_not_typed():
    assert B.add_years("2023-12-05", 3) == "2026-12-05"
    assert B.add_years("2024-02-29", 3) == "2027-02-28"
    pol = json.loads((FACTS / "dividends.json").read_text(encoding="utf-8"))["policy"]
    assert pol["valid_until"] == B.policy_valid_until(B.POLICY_APPROVED, B.POLICY_TERM_YEARS, B.POLICY_EXTENSION)
    assert f"{B.POLICY_TERM_YEARS} года" in B.POLICY_TERM_NOTE and "{until}" in B.POLICY_TERM_NOTE, \
        "записка о сроке: срок — числом параметра, дата — подстановкой, не литералом"
    assert pol["valid_until_note"].startswith(B.POLICY_TERM_NOTE.format(until=B.d_ru(B.add_years(B.POLICY_APPROVED, B.POLICY_TERM_YEARS))))


def test_policy_extension_moves_the_term_forward_only():
    """Продление срока без нового Положения — параметром сборщика с источником (SCHEMA.md §3): срок
    становится датой продления; продление не позже срока по документу — ошибка записи."""
    assert B.policy_valid_until("2023-12-05", 3, None) == "2026-12-05"
    assert B.policy_valid_until("2023-12-05", 3, {"until": "2027-06-30"}) == "2027-06-30"
    for bad in ({"until": "2026-12-05"}, {"until": "2026-01-01"}, {}):
        with pytest.raises(B.BuildError):
            B.policy_valid_until("2023-12-05", 3, bad)


def test_short_dates_in_guidance_notes_get_their_year():
    """Записка гайденса печатается на витрине; короткая дата читается там как дробь (AUDIT-2 № 96).
    Год — из дат гайденса: последняя с тем же днём и месяцем не позже даты строки."""
    known = ["2025-12-10", "2026-02-26", "2026-04-29", "2026-07-29"]
    assert B.dates_with_year("сектор без изменений к 29.04; Сбер — лучше сектора", known, "2026-07-29") == \
        "сектор без изменений к 29.04.2026; Сбер — лучше сектора"
    assert B.dates_with_year("без изменений к 10.12.2025", known, "2026-02-26") == "без изменений к 10.12.2025"
    assert B.dates_with_year("к 29.04", ["2025-04-29", "2026-04-29"], "2026-03-01") == "к 29.04.2025", "дата не позже строки"
    assert B.dates_with_year("ROE 22.05%, рост 13.35 п.п., «~6.25%»", known, "2026-07-29") == \
        "ROE 22.05%, рост 13.35 п.п., «~6.25%»", "дробь датой не считается: процент и номер месяца больше 12"
    with pytest.raises(B.BuildError):
        B.dates_with_year("без изменений к 01.03", known, "2026-07-29")
    g = json.loads((FACTS / "guidance.json").read_text(encoding="utf-8"))
    notes = [h["note"] for h in g["history"]] + [n["scope_note"] for n in g["items"]["loan_growth"].values()]
    for note in notes:                     # в фактах коротких дат не осталось: без известных дат записка не меняется
        assert B.dates_with_year(note, [], "") == note, note


def test_relation_table_covers_the_sector_items():
    sector = {k: rel for k, (_, _, rel) in B.GUIDANCE_KEYS.items() if rel is not None}
    assert sector == {"loan_growth.corporate": "in_line", "loan_growth.retail": "above"}
    assert set(sector.values()) <= set(B.RELATION_WORDS)


# ------------------------------------------------------------------ командная строка

def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("BANK_HANDOFF_DIR", None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-B", str(BUILDER), *args], cwd=ROOT, env=e, capture_output=True,
                          text=True, encoding="utf-8")


def test_cli_without_handoff_dir_refuses(tmp_path):
    res = run("--anchor", "2026Q3", "--out", str(tmp_path))
    assert res.returncode == 1 and "BANK_HANDOFF_DIR" in res.stderr
    assert not list(tmp_path.glob("*.json"))


def test_cli_help_names_the_anchor_argument():
    res = run("--help")
    assert res.returncode == 0 and "--anchor" in res.stdout and "--expected" in res.stdout
