# -*- coding: utf-8 -*-
"""Миры книги: запись семейства — бит в бит, рецепт (обезличенные копии, как у «Ленты») воспроизводит запись, блоки
worlds.<W> — поле записи / 100, надстройка worlds_bank.json воспроизводится своим сборщиком и вписана в книгу со своим
хэшем (поток book). Быстрые — метка `tact`; `archive` такт не гоняет."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

try:
    from tests import support_book as S
except ImportError:                      # прогон без pytest.ini (pythonpath = .) и без tests/__init__.py
    import support_book as S

pytestmark = pytest.mark.tact

OVERLAY_SCRIPT = S.EVIDENCE / "worlds_bank" / "build_overlay.py"


@pytest.fixture(scope="module")
def book() -> dict:
    return S.built_book()


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads((S.BOOK_DIR / "worlds_source.json").read_bytes().decode("utf-8"))


def test_the_world_record_is_stored_byte_for_byte(book):
    raw = (S.BOOK_DIR / "worlds_source.json").read_bytes()
    assert S.sha256_bytes(raw) == S.RECORD_SHA256 == book["worlds"]["source"]["sha256"]
    assert b"\r\n" in raw                                      # CRLF записи сохранены (.gitattributes: -text)
    assert S.sha256_bytes((S.BOOK_DIR / "worlds_source.csv").read_bytes()) == S.RECORD_CSV_SHA256


def test_the_recipe_is_the_anonymised_family_copy():
    """Рецепт — обезличенные копии, как у «Ленты» (решение ведущего B4): хэши LF-нормализованных байтов, без тикера,
    адреса выпуска и сумм книги-источника (сама запись — бит в бит, тест выше; воспроизводимость — тест ниже)."""
    for name, sha in S.RECIPE_LF_SHA256.items():
        raw = (S.BOOK_DIR / name).read_bytes()
        assert S.sha256_bytes(raw.replace(b"\r\n", b"\n")) == sha, name
        text = raw.decode("utf-8")
        assert not [w for w in S.RECIPE_FORBIDDEN if w in text], name
        if not name.endswith(".py"):                       # у кода — только формат печати «₽», без чисел
            assert "₽" not in text, name


def test_git_keeps_the_record_bytes():
    if shutil.which("git") is None or not (S.ROOT / ".git").exists():
        pytest.skip("нет git")
    done = subprocess.run(["git", "check-attr", "text", "--", "data/assumptions/worlds_source.json",
                           "data/assumptions/worlds_source.csv"], cwd=str(S.ROOT), capture_output=True, text=True)
    assert done.returncode == 0
    assert all(line.endswith(": text: unset") for line in done.stdout.strip().splitlines()), done.stdout


def test_the_worlds_recipe_reproduces_the_record():
    done = S.run_script(S.BOOK_DIR / "worlds_recipe.py", "--check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "воспроизведено: 63" in done.stdout


def test_world_blocks_are_the_record_over_100(book, record):
    meta = book["meta"]
    first_half = f"{meta['first_period'][:4]}H{1 if int(meta['first_period'][5]) <= 2 else 2}"
    for w in book["worlds"]["ids"]:
        blk = book["worlds"][w]
        rows = {r["period"]: r for r in record["rows"] if r["world"] == w}
        assert next(iter(blk["key_rate"])) == first_half
        assert list(blk["key_rate"])[-1] == f"{meta['last_period'][:4]}H2"
        for key, src in (("key_rate", "key_avg"), ("cpi", "cpi_yoy_avg"), ("wage_growth", "nominal_wage_yoy"),
                         ("ofz_1y", "ofz_1y"), ("ofz_3y", "ofz_3y"), ("ofz_5y", "ofz_5y"), ("ofz_10y", "ofz_10y"),
                         ("real_key", "real_key_fwd12m")):
            for p, v in blk[key].items():
                assert v == round(rows[p][src] / 100, 5), (w, key, p)
        fair = record["fair_zero_curve_today"][w]
        assert {k: v for k, v in blk["zero_curve"].items() if k != "LT"} == {k: round(x / 100, 5) for k, x in fair.items()}
        assert blk["lt_inflation"] == round(record["lt_inflation"][w] / 100, 5)
    recipe = S.load_module(S.BOOK_DIR / "worlds_recipe.py", "book_worlds_recipe")
    assert book["worlds"]["M"]["zero_curve"]["LT"] == round(recipe.curve_lt_rule(record)["M"] / 100, 5)
    assert book["worlds"]["N"]["zero_curve"]["LT"] == 0.09 and book["worlds"]["H"]["zero_curve"]["LT"] == 0.127


def test_world_weights_and_their_axis_stay_inside_the_record_ranges(book, record):
    assert book["joint"]["world_prob"] == record["probabilities"]
    axis = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["joint.world_prob"])
    for end in (axis["low"], axis["high"]):
        for w, v in end.items():
            lo, hi = record["probability_ranges"][w]
            assert lo <= v <= hi, (w, v)


def test_the_overlay_is_reproduced_by_its_builder():
    mod = S.load_module(OVERLAY_SCRIPT, "book_build_overlay")
    assert (S.BOOK_DIR / "worlds_bank.json").read_text(encoding="utf-8") == mod.build_text()


def test_the_overlay_hash_is_in_the_book(book):
    sha = S.sha256_bytes((S.BOOK_DIR / "worlds_bank.json").read_bytes())
    assert book["worlds"]["overlay"]["sha256"] == sha
    assert S.template()["worlds"]["overlay"]["sha256"] is None       # в шаблоне хэш ставит сборка


def test_the_overlay_format_and_rules(book):
    ov = json.loads((S.BOOK_DIR / "worlds_bank.json").read_text(encoding="utf-8"))
    assert ov["schema"] == "worlds_bank-v1"
    assert set(ov["source"]) == {"doc", "url", "sha256", "as_of"} and len(ov["source"]["sha256"]) == 64
    anchor_year = int(book["meta"]["anchor_period"][:4])
    for w in book["worlds"]["ids"]:
        blk = ov["worlds"][w]
        fields = [blk["credit_growth"][k] for k in ("corporate", "mortgage", "retail_other")] + \
                 [blk["funds_growth"][k] for k in ("retail", "corporate")] + [blk["nominal_gdp_growth"]]
        for f in fields:
            assert [int(y) for y in f] == list(range(anchor_year, anchor_year + 4))     # 2026–2029: годы прогноза ОНДКП
            assert all(isinstance(v, float) and -0.2 < v < 0.3 for v in f.values())
        assert blk["funds_growth"]["retail"] == blk["funds_growth"]["corporate"]     # оба = М2
        assert book["worlds_bank"][w] == blk
    first = str(anchor_year)
    for grp, k in (("credit_growth", "corporate"), ("funds_growth", "retail")):     # год якоря — один во всех мирах
        assert len({ov["worlds"][w][grp][k][first] for w in book["worlds"]["ids"]}) == 1


def test_the_overlay_draft_copy_has_no_absolute_paths():
    text = (S.EVIDENCE / "worlds_bank" / "inputs" / "bank_overlay_draft.yaml").read_text(encoding="utf-8")
    assert ":/" not in text.replace("https://", "").replace("http://", "")
    draft = yaml.safe_load(text)
    assert draft["meta"]["worlds_record"]["sha256"] == S.RECORD_SHA256


@pytest.mark.archive
def test_the_overlay_draft_copy_is_the_stage1_draft():
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root:
        pytest.skip("нет BANK_HANDOFF_DIR")
    mod = S.load_module(OVERLAY_SCRIPT, "book_build_overlay_archive")
    src = Path(root, *mod.HANDOFF_DRAFT).read_text(encoding="utf-8")
    assert mod.sanitize(src) == (S.EVIDENCE / "worlds_bank" / "inputs" / "bank_overlay_draft.yaml").read_text(encoding="utf-8")
