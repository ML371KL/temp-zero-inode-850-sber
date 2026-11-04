# -*- coding: utf-8 -*-
"""Правила книги для чисел, зависящих от миров (ops/tools/worlds_rules.py): на записи миров книги каждое правило даёт
шаблон — числа и числа правил в тексте — бит в бит; правила знают всё, что опись книги называет зависящим от миров.

ЗАЧЕМ. Команда приёма миров (ops/tools/adopt_worlds.py) пересчитывает только то, что названо в манифесте, и пишет
выход правил в шаблон. Правило, которое на своей записи не воспроизводит шаблон, при следующей версии миров запишет
не то число: красный тест здесь — до приёма, а не после."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "tools"))
import adopt_worlds as A  # noqa: E402
import worlds_rules as R  # noqa: E402

pytestmark = pytest.mark.tact

MANIFEST = A.load_manifest(ROOT)
RULES = [s for s in A.walk_steps(MANIFEST["steps"]) if s["kind"] == "rule"]


@pytest.fixture(scope="module")
def ctx():
    state = A.read_state(ROOT)
    return A.RuleContext(tree=ROOT, entry=None, version=state["version"], manifest=MANIFEST)


@pytest.mark.parametrize("step", RULES, ids=[s["id"] for s in RULES])
def test_the_rule_gives_the_template_on_the_record_of_the_book(step, ctx):
    bad, n = [], 0
    for target in A.call_rule(ROOT, step, ctx):
        path = ROOT / target["file"]
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) if target.get("values") else None
        for k, want in (target.get("values") or {}).items():
            n += 1
            have = A.get(doc, A._path(k))
            if not (A.same(have, want) and type(have) is type(want)):
                bad.append((A._dotted(A._path(k)), have, want))
        bad += [("текст", p[0][:80], p[1][:80]) for p in target.get("replace") or [] if p[0] != p[1]]
    assert not bad, bad[:10]
    assert n, step["id"]


def test_the_rules_cover_what_the_inventory_names(ctx):
    """Ключи, зависящие от миров (опись книги, раздел 2): спреды книг с траекторией (2026, 2027, годы до LT_from),
    веса миров, рост кредита кризиса и коридор гейта против рискового сценария ЦБ, доли σ0 и φ и концы их осей, дисконт за
    управление (ключ, канал, строка), версия книги — все пишут правила."""
    written = set()
    for step in RULES:
        for target in A.call_rule(ROOT, step, ctx):
            written |= {A._dotted(A._path(k)) for k in (target.get("values") or {})}
    book = yaml.safe_load((ROOT / MANIFEST["template"]).read_text(encoding="utf-8"))
    for b, spec in book["nii"]["books"].items():
        if isinstance(spec["spread"], dict):
            assert {f"nii.books.{b}.spread.{k}" for k in spec["spread"] if k not in ("LT", "LT_from")} <= written, b
    assert {f"regimes.crisis.loan_growth_override.{y}" for y in book["regimes"]["crisis"]["loan_growth_override"]} <= written
    assert {f"checks.m_crisis_vs_cbr.loan_growth.{y}.{i}" for y in book["checks"]["m_crisis_vs_cbr"]["loan_growth"]
            for i in (0, 1)} <= written
    assert {f"joint.world_prob.{w}" for w in book["joint"]["world_prob"]} <= written
    axes = book["valuation"]["uncertainty"]["axes"]
    for key in ("nii.sigma0_split", "nii.phi_split"):
        i = next(j for j, a in enumerate(axes) if a["paths"] == [key])
        assert {key, f"valuation.uncertainty.axes.{i}.low", f"valuation.uncertainty.axes.{i}.high"} <= written
    for key in ("valuation.governance.discount", "valuation.governance.components.0.value",
                "valuation.governance.components.0.basis", "quasi_fiscal.fixed_point.median_rub", "meta.version"):
        assert key in written, key


def test_the_version_rule_bumps_only_a_released_version(ctx, monkeypatch):
    """Номер версии: младший разряд + 1, только если книга с этим номером выпущена на другой записи миров; строки
    версии шаблона и текста книги идут за номером."""
    book = yaml.safe_load((ROOT / MANIFEST["template"]).read_text(encoding="utf-8"))
    cur = str(book["meta"]["version"])
    head, _, minor = cur.rpartition(".")
    monkeypatch.setattr(R, "released_record", lambda root, v: "0" * 64)
    out = R.book_version(ctx)
    new = f"{head}.{int(minor) + 1}"
    assert out[0]["values"][("meta", "version")] == new
    today = dt.date.today().strftime("%d.%m.%Y")
    assert f"ШАБЛОН машинной книги, версия {new} (запись версии в журнале — {today};" == out[0]["replace"][0][1]
    assert out[1]["replace"][0][1].endswith(f"версия {new}\n") and f"`book-{new}`" in out[1]["replace"][1][1]
    assert out[1]["replace"][2][1] == f"**версия {new} записана:** {today}"
    monkeypatch.setattr(R, "released_record", lambda root, v: book["worlds"]["source"]["sha256"])
    assert R.book_version(ctx)[0]["values"][("meta", "version")] == cur
    monkeypatch.setattr(R, "released_record", lambda root, v: None)
    assert R.book_version(ctx)[0]["values"][("meta", "version")] == cur


def test_the_governance_key_follows_the_median_of_the_tables(ctx, monkeypatch):
    """Дисконт за управление — неподвижная точка на медиане таблиц: медиана выше порога — ключ на шаг ниже (точка
    падает с ростом медианы), медиана книги — ключ книги; вход листа получает медиану и версию таблиц."""
    real = R.json_file
    book = yaml.safe_load((ROOT / MANIFEST["template"]).read_text(encoding="utf-8"))
    key = book["valuation"]["governance"]["discount"]
    step = 0.0005

    def at(median):
        def fake(c, rel):
            data = real(c, rel)
            if rel == R.RESULTS:
                data = dict(data, headline=dict(data["headline"], median=median))
            return data
        monkeypatch.setattr(R, "json_file", fake)
        gov, tpl = R.governance(ctx)
        return tpl["values"][("valuation", "governance", "discount")], gov["values"]
    res = real(ctx, R.RESULTS)
    k0, inp = at(res["headline"]["median"])
    assert k0 == key and inp[("quasi_fiscal", "fixed_point", "median_of")] == f"книга {res['book_version']}"
    hi, _ = at(res["headline"]["median"] * 1.15)
    lo, _ = at(res["headline"]["median"] * 0.85)
    assert hi == pytest.approx(key - step, abs=1e-12) or hi == pytest.approx(key - 2 * step, abs=1e-12)
    assert lo >= key + step - 1e-12


def test_the_point_writer_keeps_the_text_and_writes_inside_lists(tmp_path):
    """Точечная запись команды: число внутри списка (ось) — без правки остального текста; замена текста правила не
    трогает данные."""
    p = tmp_path / "t.yaml"
    p.write_text('axes:\n  - {name: "a", low: 0.0, high: 0.089584}   # c 0,1\nk: 3  # к\n', encoding="utf-8")
    ch = A.set_values(p, {("axes", 0, "high"): 0.09, ("k",): 4})
    assert set(ch) == {"axes.0.high", "k"}
    assert p.read_text(encoding="utf-8") == 'axes:\n  - {name: "a", low: 0.0, high: 0.09}   # c 0,1\nk: 4  # к\n'
    assert A.replace_text(p, [["# c 0,1", "# c 0,25"]]) == 1
    with pytest.raises(A.Stop):
        A.replace_text(p, [["k: 4", "k: 5"]])                   # данные — только значениями


def test_numbers_inside_folded_texts_are_written_by_replacement(tmp_path):
    """Строки блочного стиля (`>-`, объяснения гейтов): число правила пишет замена текста — перенос строк блока
    остаётся; сверка — данные до замен плюс значения правила, иначе отказ."""
    p = tmp_path / "g.yaml"
    p.write_text("g:\n  explanation: >-\n    масса 2,5 % и\n    ROE 22,5 %\n  expected_mass: 0.0254\n", encoding="utf-8")
    want = {("g", "explanation"): "масса 2,6 % и ROE 22,5 %"}
    assert A.replace_text(p, [["масса 2,5 %", "масса 2,6 %"]], want) == 1
    assert p.read_text(encoding="utf-8") == "g:\n  explanation: >-\n    масса 2,6 % и\n    ROE 22,5 %\n  expected_mass: 0.0254\n"
    with pytest.raises(A.Stop):
        A.replace_text(p, [["ROE 22,5 %", "ROE 22,7 %"]], want)          # замена сверх значения правила
