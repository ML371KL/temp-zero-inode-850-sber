"""Лист квартала: шаблон следующего отчёта и чтение заполненного листа сборщиком фактов.

Лист квартала (`data/facts/SCHEMA.md` §5) — строки отчёта и презентации нового квартала в формате
листов этапа 1. Шаблон следующего квартала лежит в `data/facts/sheets/<период>.csv`: его строит
сборщик из того, что прочёл на якоре (`--sheet-template`), колонки `prev_*` называют место той же
строки в прошлом отчёте. Заполненный лист дополняет листы этапа 1; строка, расходящаяся с листом
этапа 1, — отказ. Быстрые тесты (`tact`) идут без папки передачи; с ней (`archive`) — шаблон
воспроизводится байт в байт и сборка следующего квартала из заполненного листа не просит ни одной
строки вне шаблона.
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
SHEETS = FACTS / "sheets"
BUILDER = ROOT / "ops" / "tools" / "build_facts.py"


def builder():
    spec = importlib.util.spec_from_file_location("build_facts_sheet_test", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


B = builder()
HEAD = "metric,period,value,unit,basis,doc,page,sha256,note"


def load(name: str) -> dict:
    return json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def sheet(tmp_path: Path, name: str, lines: list[str]):
    return B.Sheet(write(tmp_path / f"{name}.csv", "\n".join([HEAD] + lines) + "\n"), B.Docs(tmp_path), name=name)


def committed_template() -> tuple[object, list[dict]]:
    new = B.Anchor(B.q_next(B.default_anchor()))
    path = SHEETS / f"{new.period}.csv"
    assert path.exists(), (f"нет шаблона листа следующего квартала {path.relative_to(ROOT).as_posix()}: после "
                           f"перезаякоривания — build_facts.py --check --sheet-template <файл> (SCHEMA.md §5)")
    with path.open(encoding="utf-8", newline="") as f:
        return new, list(csv.DictReader(f))


# ------------------------------------------------------------------ метки следующего квартала

def test_quarter_labels_follow_the_anchor():
    old, new = B.Anchor("2026Q2"), B.Anchor("2026Q3")
    assert B.quarter_labels(old, new) == {"2026-06-30": "2026-09-30", "2Q2026": "3Q2026", "6M2026": "9M2026",
                                          "2026Q2": "2026Q3"}
    year_end = B.quarter_labels(B.Anchor("2026Q3"), B.Anchor("2026Q4"))
    assert year_end["9M2026"] == "FY2026" and year_end["2026-09-30"] == "2026-12-31"
    new_year = B.quarter_labels(B.Anchor("2026Q4"), B.Anchor("2027Q1"))
    assert new_year == {"2026-12-31": "2027-03-31", "4Q2026": "1Q2027", "FY2026": "3M2027", "2026Q4": "2027Q1"}


def test_quarter_inputs_name_the_dates_of_the_quarter():
    """Входы вне листа квартала названы с датами нового якоря: формы ЦБ — на 1-е число после квартала,
    релизы РСБУ — три месяца квартала, кривая и ключевая — на конец квартала."""
    text = "\n".join(f"{where}: {what}" for where, what in B.quarter_inputs(B.Anchor("2026Q3")))
    for piece in ("01.10.2026", "2026-07, 2026-08, 2026-09", "30.09.2026", "2026Q3", "9М26", "промежуточная"):
        assert piece in text, piece
    assert "годовая отчётность" in "\n".join(what for _, what in B.quarter_inputs(B.Anchor("2026Q4")))


# ------------------------------------------------------------------ шаблон из журнала чтений

def test_template_rows_come_from_what_the_builder_read(tmp_path):
    h = sheet(tmp_path, "ifrs_history", [
        "total_assets,2026-06-30,100.0,RUB bn,IFRS,primary/a.pdf,5,,отчётный период документа",
        "cash,2026-06-30,7.0,RUB bn,IFRS,primary/a.pdf,5,,",
        "cash,2026-09-30,8.0,RUB bn,IFRS,primary/b.pdf,5,ff,уже внесено",
        "total_assets,2025-12-31,90.0,RUB bn,IFRS,primary/a.pdf,5,,сравнительные данные"])
    a = sheet(tmp_path, "ifrs_anchor", ["orf_fvoci,2026-06-30,-3.0,RUB bn,IFRS,primary/a.pdf,44,,"])
    q = sheet(tmp_path, "ifrs_quarterly", ["nii,2Q2026,10.0,RUB bn,IFRS,primary/a.pdf,6,,прямо: колонка «за три месяца»"])
    m = sheet(tmp_path, "mgmt_kpi", ["nim,2026Q2,6.7,%,mgmt,pres.pdf,6,,ЧПМ"])
    h.get("total_assets", "2026-06-30"), h.get("cash", "2026-06-30"), h.get("total_assets", "2025-12-31")
    h.get("orf_fvoci", "2026-06-30"), a.get("orf_fvoci", "2026-06-30")      # остаток ищут в ряду, затем в листе якоря
    q.get("nii", "2Q2026"), q.get("ii_ac_cash", "2Q2026"), q.get("nii", "1Q2026")
    m.get("nim", "2026Q2")
    labels = B.quarter_labels(B.Anchor("2026Q2"), B.Anchor("2026Q3"))
    rows = B.template_rows([h, a, q, m], labels, {"nii": "Чистые процентные доходы"})
    got = {(r["sheet"], r["metric"]): r for r in rows}
    assert set(got) == {("ifrs_history", "total_assets"), ("ifrs_history", "cash"), ("ifrs_anchor", "orf_fvoci"),
                        ("ifrs_quarterly", "nii"), ("ifrs_quarterly", "ii_ac_cash"), ("mgmt_kpi", "nim")}, \
        "в шаблоне — только строки квартала якоря: прошлый год и прошлый квартал уже в листах"
    assert [r["sheet"] for r in rows] == sorted((r["sheet"] for r in rows), key=B.SHEET_NAMES.index)
    assert all(tuple(r) == B.SHEET_COLUMNS for r in rows)
    ta = got[("ifrs_history", "total_assets")]
    assert (ta["period"], ta["value"], ta["prev_period"], ta["prev_value"], ta["prev_doc"], ta["prev_page"]) == \
        ("2026-09-30", "", "2026-06-30", "100.0", "primary/a.pdf", "5"), "что внести — пусто, откуда — место в прошлом отчёте"
    cash = got[("ifrs_history", "cash")]
    assert (cash["value"], cash["doc"], cash["sha256"], cash["note"]) == ("8.0", "primary/b.pdf", "ff", "уже внесено"), \
        "строка нового квартала, уже стоящая в листах, в шаблоне заполнена"
    assert got[("ifrs_anchor", "orf_fvoci")]["prev_value"] == "-3.0", "строка идёт в тот лист, где она стояла"
    nii = got[("ifrs_quarterly", "nii")]
    assert (nii["period"], nii["what"], nii["unit"]) == ("3Q2026", "Чистые процентные доходы", "RUB bn")
    opt = got[("ifrs_quarterly", "ii_ac_cash")]
    assert opt["prev_value"] == "" and "необязательная" in opt["prev_note"], "строки не было на прежнем якоре — так и сказано"
    assert got[("mgmt_kpi", "nim")]["period"] == "2026Q3"


# ------------------------------------------------------------------ заполненный лист дополняет листы этапа 1

SHEET_HEAD = ",".join(B.SHEET_COLUMNS)


def quarter_sheet(tmp_path: Path, lines: list[str], head: str = SHEET_HEAD) -> Path:
    return write(tmp_path / "quarter" / "2026Q3.csv", "\n".join([head] + lines) + "\n")


def test_filled_rows_are_read_and_empty_ones_wait(tmp_path):
    path = quarter_sheet(tmp_path, [
        "ifrs_history,total_assets,Итого активы,2026-09-30,110.5,RUB bn,primary/b.pdf,5,,отчётный период,2026-06-30,100.0,primary/a.pdf,5,",
        "ifrs_history,cash,,2026-09-30,,RUB bn,,,,,2026-06-30,7.0,primary/a.pdf,5,",
        "ifrs_quarterly,eqf_ltip__shareholders,,3Q2026,1.5,RUB bn,primary/b.pdf | primary/a.pdf,8 | 8,,расчёт: 9M2026 − 6M2026,2Q2026,4.9,,,",
        "mgmt_kpi,nim,,2026Q3,6.6,%,pres3.pdf,6,,ЧПМ,2026Q2,6.7,pres.pdf,6,"])
    rows = B.quarter_sheet_rows(path)
    assert [r["metric"] for r in rows["ifrs_history"]] == ["total_assets"], "строка без значения ещё не внесена"
    assert rows["ifrs_anchor"] == []
    two = rows["ifrs_quarterly"][0]
    assert (two["doc"], two["page"], two["sha256"]) == ("primary/b.pdf | primary/a.pdf", "8 | 8", " | "), \
        "квартал разностью — два документа; хэш не вписан — посчитает сборщик"
    assert rows["mgmt_kpi"][0]["value"] == "6.6"


@pytest.mark.parametrize("line, why", [
    ("ifrs_all,total_assets,,2026-09-30,1.0,RUB bn,primary/b.pdf,5,,,,,,,", "вне"),
    ("ifrs_history,total_assets,,2026-09-30,110 500,RUB bn,primary/b.pdf,5,,,,,,,", "не число"),
    ("ifrs_history,total_assets,,2026-09-30,\"110,5\",RUB bn,primary/b.pdf,5,,,,,,,", "не число"),
    ("ifrs_history,total_assets,,2026-09-30,110.5,RUB bn,,5,,,,,,,", "нет метрики, периода или документа"),
    ("ifrs_history,,,2026-09-30,110.5,RUB bn,primary/b.pdf,5,,,,,,,", "нет метрики, периода или документа"),
    ("ifrs_quarterly,x,,3Q2026,1.5,RUB bn,primary/b.pdf | primary/a.pdf,8,,,,,,,", "поровну"),
])
def test_bad_rows_are_refused_with_the_place(tmp_path, line, why):
    with pytest.raises(B.BuildError) as e:
        B.quarter_sheet_rows(quarter_sheet(tmp_path, [line]))
    assert why in str(e.value) and "строка 2" in str(e.value), str(e.value)


def test_sheet_without_columns_or_file_is_refused(tmp_path):
    with pytest.raises(B.BuildError, match="нет колонок"):
        B.quarter_sheet_rows(quarter_sheet(tmp_path, ["a,b,1"], head="sheet,metric,value"))
    with pytest.raises(B.BuildError, match="нет листа квартала"):
        B.quarter_sheet_rows(tmp_path / "нет.csv")


def test_quarter_rows_extend_the_stage_sheet_and_never_rewrite_it(tmp_path):
    h = sheet(tmp_path, "ifrs_history", ["total_assets,2026-06-30,100.0,RUB bn,IFRS,primary/a.pdf,5,,",
                                         "cash,2026-09-30,,RUB bn,IFRS,,,,"])
    new = {"metric": "total_assets", "period": "2026-09-30", "value": "110.5", "unit": "RUB bn", "doc": "primary/b.pdf",
           "page": "5", "sha256": "", "note": ""}
    cash = dict(new, metric="cash", value="8.0")
    assert h.add([new, cash], "лист квартала") == 2, "новая строка и строка без значения в листе этапа 1"
    assert h.get("total_assets", "2026-09-30") == 110.5 and h.get("cash", "2026-09-30") == 8.0
    assert h.add([dict(new)], "лист квартала") == 0, "та же строка второй раз — без изменений"
    assert h.add([dict(new, period="2026-06-30", value="100.0")], "лист квартала") == 0, "совпавшее значение — не спор"
    with pytest.raises(B.BuildError, match="изменённую редакцию"):
        h.add([dict(new, period="2026-06-30", value="101.0")], "лист квартала")
    assert h.get("total_assets", "2026-06-30") == 100.0, "лист этапа 1 листом квартала не переписывается"


def test_sheets_of_the_handoff_folder_are_read_up_to_the_anchor(tmp_path):
    """Канон: листы кварталов лежат в папке передачи; читаются листы не позже якоря, затем черновики
    из аргумента. Остатки якоря в листе квартала заменяют отдельный лист якоря."""
    folder = tmp_path / "stage1" / "quarter"
    row = "ifrs_history,total_assets,,{d},{v},RUB bn,primary/b.pdf,5,,,,,,,"
    write(folder / "2026Q3.csv", "\n".join([SHEET_HEAD, row.format(d="2026-09-30", v="110.5")]) + "\n")
    write(folder / "2026Q4.csv", "\n".join([SHEET_HEAD, row.format(d="2026-12-31", v="120.0")]) + "\n")
    draft = write(tmp_path / "draft.csv", "\n".join([SHEET_HEAD, "mgmt_kpi,nim,,2026Q3,6.6,%,pres3.pdf,6,,,,,,,"]) + "\n")
    c = B.Ctx.__new__(B.Ctx)               # без листов этапа 1: проверяется только выбор листов кварталов
    c.root, c.a = tmp_path, B.Anchor("2026Q3")
    rows, files = c._quarter_rows((draft,))
    assert files == ["2026Q3.csv", "draft.csv"], "лист следующего квартала на этом якоре не читается"
    assert [r["period"] for r in rows["ifrs_history"]] == ["2026-09-30"] and rows["mgmt_kpi"][0]["metric"] == "nim"
    c.a = B.Anchor("2026Q2")
    assert c._quarter_rows(()) == ({name: [] for name in B.SHEET_NAMES}, [])
    (tmp_path / "stage1" / "ifrs").mkdir()
    assert c._anchor_sheet(tmp_path / "stage1" / "ifrs", optional=True) is None
    with pytest.raises(B.BuildError, match="нет листа якоря"):
        c._anchor_sheet(tmp_path / "stage1" / "ifrs")
    write(folder / "черновик.csv", SHEET_HEAD + "\n")
    with pytest.raises(B.BuildError, match="имя листа квартала"):
        c._quarter_rows(())


# ------------------------------------------------------------------ шаблон в дереве

def test_committed_template_is_for_the_next_quarter_and_points_to_the_anchor_report():
    """Шаблон в дереве — на квартал, следующий за якорем фактов; его колонки `prev_*` — числа и места
    отчёта якоря: те же, что в фактах (итог активов, ЧПД квартала, упр. ЧПМ)."""
    new, rows = committed_template()
    old = B.Anchor(B.default_anchor())
    labels = B.quarter_labels(old, new)
    assert rows and tuple(rows[0]) == B.SHEET_COLUMNS
    keys = [(r["metric"], r["period"]) for r in rows]
    assert len(keys) == len(set(keys)), "строка шаблона повторяется"
    bad = []
    for r in rows:
        where = f"{r['sheet']}/{r['metric']}/{r['period']}"
        if r["sheet"] not in B.SHEET_NAMES or r["period"] not in labels.values() or labels.get(r["prev_period"]) != r["period"]:
            bad.append(f"{where}: лист или период вне квартала {new.period}")
        if r["prev_value"] != "" and not (r["prev_doc"] and r["prev_page"]):
            bad.append(f"{where}: нет места в прошлом отчёте")
        if r["prev_value"] == "" and "необязательная" not in r["prev_note"]:
            bad.append(f"{where}: нет ни прошлого значения, ни пометки")
        if r["value"] != "":
            float(r["value"])
            if not r["doc"]:
                bad.append(f"{where}: внесено без документа")
    assert not bad, "\n".join(bad)
    assert {r["sheet"] for r in rows} >= {"ifrs_history", "ifrs_quarterly", "mgmt_kpi"}
    prev = {(r["sheet"], r["metric"]): float(r["prev_value"]) for r in rows if r["prev_value"] != ""}
    assert prev[("ifrs_history", "total_assets")] == load("balance")["total_assets"]["v"]
    assert prev[("ifrs_quarterly", "nii")] == load("pnl_quarterly")["quarters"][old.period]["nii"]["v"]
    assert abs(prev[("mgmt_kpi", "nim")] / 100 - load("mgmt_quarterly")["quarters"][old.period]["nim"]["v"]) < 1e-9
    # что внести и откуда: у строк отчётности есть описание метрики, у каждой — единица
    assert all(r["unit"] for r in rows if r["prev_value"] != "")
    assert sum(1 for r in rows if r["sheet"] != "mgmt_kpi" and r["what"]) > 100


@pytest.mark.docs
def test_schema_describes_the_quarter_sheet():
    text = (FACTS / "SCHEMA.md").read_text(encoding="utf-8")
    section = text[text.index("## 5."):]
    for word in list(B.SHEET_NAMES) + ["--sheet-template", "--sheet", B.QUARTER_SHEETS, "prev_doc", "sheets/"]:
        assert word in section, f"SCHEMA.md §5 не называет «{word}»"


# ------------------------------------------------------------------ командная строка

def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("BANK_HANDOFF_DIR", None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-B", str(BUILDER), *args], cwd=ROOT, env=e, capture_output=True,
                          text=True, encoding="utf-8")


def test_cli_draft_sheet_never_goes_to_the_canon(tmp_path):
    """Черновик листа квартала (`--sheet`) в data/facts не пишется: канон собирается из папки передачи."""
    draft = quarter_sheet(tmp_path, [])
    for args in (("--sheet", str(draft)), ("--sheet", str(draft), "--out", str(FACTS))):
        res = run(*args)
        assert res.returncode == 1 and "--sheet" in res.stderr and "--out" in res.stderr, res.stderr
    res = run("--help")
    assert "--sheet-template" in res.stdout and "--sheet " in res.stdout


# ------------------------------------------------------------------ с папкой передачи

HANDOFF = os.environ.get("BANK_HANDOFF_DIR")
archive = pytest.mark.archive


@archive
def test_template_is_reproduced_from_the_handoff_folder(tmp_path):
    """Шаблон в дереве — то, что сборщик пишет на якоре фактов (байт в байт)."""
    if not HANDOFF:
        pytest.skip("нет папки передачи (BANK_HANDOFF_DIR)")
    _, ctx = B.build_ctx(Path(HANDOFF), B.Anchor(B.default_anchor()))
    new, rows = B.sheet_template(ctx)
    out = tmp_path / "sheet.csv"
    B.write_sheet(rows, out)
    assert out.read_bytes() == (SHEETS / f"{new.period}.csv").read_bytes(), \
        "шаблон листа квартала разошёлся со сборщиком: build_facts.py --check --sheet-template data/facts/sheets/<период>.csv"


def _synthetic_inputs(monkeypatch, old, new):
    """Входы следующего квартала вне листа квартала — в памяти, копиями квартала якоря (формы ЦБ,
    релизы РСБУ, калибровка): папка передачи не тронута. Числа бессмысленны — проверяется состав строк."""
    form = {old.form_date: new.form_date}

    class Next(B.Ctx):
        def calib(self):
            if self._calib is None:
                saved, self.a = self.a, old
                try:
                    data, _ = super().calib()
                finally:
                    self.a = saved
                self._calib = dict(data, period=new.period)
            return super().calib()

        def calib_sub_range(self):
            return ""

        def csv_rows(self, rel):
            rows = super().csv_rows(rel)
            extra = []
            for r in rows:
                if r.get("form_date") in form:                  # формы ЦБ на 1-е число после квартала
                    row = dict(r, form_date=form[r["form_date"]])
                    if r.get("value_ytd_thousand_rub"):          # форма 0409102 — нарастающим итогом
                        before = B.Anchor(B.q_prev(old.period)).form_date
                        prev = next((x for x in rows if x["form_date"] == before and x["symbol"] == r["symbol"]
                                     and x["form_variant"] == r["form_variant"] and x["value_ytd_thousand_rub"]), None)
                        if prev is not None and old.quarter > 1:
                            row["value_ytd_thousand_rub"] = str(2 * float(r["value_ytd_thousand_rub"])
                                                                - float(prev["value_ytd_thousand_rub"]))
                    extra.append(row)
                if rel.endswith("INDEX.csv") and r.get("name") in form:
                    extra.append(dict(r, name=form[r["name"]]))
                if rel.endswith("ras_release_monthly.csv"):      # релизы: месяцы квартала — нарастающим итогом
                    y, m = r["month"].split("-")
                    if int(y) == old.year and 3 * old.quarter - 2 <= int(m) <= 3 * old.quarter and r["value"]:
                        start = next((x for x in rows if x["metric"] == r["metric"]
                                      and x["month"] == f"{y}-{3 * old.quarter - 3:02d}"), None)
                        end = next(x for x in rows if x["metric"] == r["metric"] and x["month"] == f"{y}-{3 * old.quarter:02d}")
                        val = float(r["value"])
                        if r["metric"].endswith("_ytd") and r["metric"] != "cor_ytd" and start is not None and start["value"]:
                            val = float(end["value"]) + (float(r["value"]) - float(start["value"]))
                        extra.append(dict(r, month=f"{y}-{int(m) + 3:02d}", value=str(val)))
            return rows + extra

        def file_ref(self, rel, what, **kw):
            for d_old, d_new in form.items():
                if d_new in rel and not (self.root / rel).exists():   # ответа формы на новую дату нет — прежний файл
                    kw = dict(kw, key=(kw.get("key") or "") + "_synthetic")
                    return super().file_ref(rel.replace(d_new, d_old), what.replace(d_new, d_old), **kw).replace(d_old, d_new)
            return super().file_ref(rel, what, **kw)

    real_f101 = B.f101_iea
    monkeypatch.setattr(B, "Ctx", Next)
    monkeypatch.setattr(B, "f101_iea", lambda c, f101, d: real_f101(c, f101, old.form_date if d == new.form_date else d))


@archive
def test_next_quarter_builds_from_a_filled_sheet_and_asks_for_nothing_else(tmp_path, monkeypatch):
    """Шаблон полон: лист квартала, заполненный по шаблону (здесь — копиями квартала якоря), даёт
    сборщику все строки отчёта и презентации следующего якоря. Не сходятся только две сверки процентов
    кредитных книг — калибровка книг ЧПД не пересчитана (её и ловит сверка)."""
    if not HANDOFF:
        pytest.skip("нет папки передачи (BANK_HANDOFF_DIR)")
    old = B.Anchor(B.default_anchor())
    new, rows = committed_template()
    if new.year != old.year:
        pytest.skip("копии квартала якоря годятся только внутри года: на смене года состав отчёта другой")
    if any(r["value"] != "" for r in rows):
        pytest.skip("строки следующего квартала уже в листах этапа 1 — шаблон отслужил, якорь пора переносить")
    with pytest.raises(B.BuildError, match="нет квартала якоря"):
        B.build(Path(HANDOFF), new)
    filled = [dict(r, value=r["prev_value"], doc=r["prev_doc"], page=r["prev_page"], note=r["prev_note"])
              for r in rows if r["prev_value"] != ""]
    draft = tmp_path / f"{new.period}.csv"
    B.write_sheet(filled, draft)
    _synthetic_inputs(monkeypatch, old, new)
    files, ctx = B.build_ctx(Path(HANDOFF), new, (draft,))
    assert ctx.sheet_files == [draft.name]
    assert files["anchor"]["period"] == new.period and files["balance"]["as_of"] == new.date
    assert max(files["pnl_quarterly"]["quarters"]) == new.period == files["bridge_mgmt_ifrs"]["window"][1]
    failed = [name for name, ok, _ in ctx.checks if not ok]
    assert len(failed) == 2 and all("книг" in name for name in failed), failed
    # ни одной строки квартала сборщик не прочёл мимо шаблона
    labels = set(B.quarter_labels(old, new).values())
    listed = {(r["metric"], r["period"]) for r in rows}
    stray = [(s.name, key) for s in (ctx.H, ctx.A, ctx.Q, ctx.M) for key in s.seen if key[1] in labels and key not in listed]
    assert not stray, f"сборщик читает строки квартала вне шаблона: {stray[:10]}"
    # даты нового якоря — из него самого, следов прежнего в текстах нет
    assert dt.date.fromisoformat(files["nii_books"]["as_of"]) == dt.date.fromisoformat(new.date)
    assert B.d_ru(old.open_date) not in json.dumps(files["nii_books"], ensure_ascii=False)
