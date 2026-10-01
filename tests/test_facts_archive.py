"""Факты против папки передачи (маркер archive; в CI пропускается).

Сборщик воспроизводит data/facts байт в байт; sha256 каждого документа реестров
(anchor.json → documents, sources календаря и аналогов) совпадает с файлом в папке передачи.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
BUILT = ("anchor", "balance", "nii_books", "pnl_quarterly", "capital", "shares", "dividends",
         "bridge_mgmt_ifrs", "bridge_ras_ifrs", "mgmt_quarterly", "guidance")

pytestmark = pytest.mark.archive


def handoff() -> Path:
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root or not Path(root).is_dir():
        pytest.skip("нет папки передачи (BANK_HANDOFF_DIR)")
    return Path(root)


def test_builder_reproduces_facts(tmp_path):
    root = handoff()
    env = dict(os.environ, BANK_HANDOFF_DIR=str(root), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-B", str(ROOT / "ops" / "tools" / "build_facts.py"), "--out", str(tmp_path)],
                         cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    assert "СБОЙ" not in res.stdout
    diff = [n for n in BUILT if (tmp_path / f"{n}.json").read_bytes() != (FACTS / f"{n}.json").read_bytes()]
    assert not diff, f"пересборка расходится с data/facts: {diff}"


def test_builder_check_mode():
    root = handoff()
    env = dict(os.environ, BANK_HANDOFF_DIR=str(root), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-B", str(ROOT / "ops" / "tools" / "build_facts.py"), "--check"],
                         cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr[-2000:]


@pytest.mark.parametrize("name", ("anchor", "calendar", "peers"))
def test_registry_sha256_matches_files(name):
    root = handoff()
    data = json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))
    docs = data["documents"] if name == "anchor" else data["sources"]
    bad, checked = [], 0
    for d in docs:
        f = d.get("file")
        if not f:
            continue
        path = root / f
        if not path.exists():
            bad.append(f"{d['key']}: нет файла {f}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != d["sha256"]:
            bad.append(f"{d['key']}: sha256 не совпал")
        checked += 1
    assert checked, "ни одного файла не проверено"
    assert not bad, "\n".join(bad)


def _run(*args: str) -> subprocess.CompletedProcess:
    root = handoff()
    env = dict(os.environ, BANK_HANDOFF_DIR=str(root), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-B", str(ROOT / "ops" / "tools" / "build_facts.py"), *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")


def test_anchor_argument_reproduces_and_refuses_a_quarter_without_sheets(tmp_path):
    """Якорь из аргумента: квартал закоммиченных фактов — те же байты; квартал, которого нет в листах
    этапа 1, — отказ с названием места, а не молчаливая сборка прежнего якоря (AUDIT-1 № 9)."""
    period = json.loads((FACTS / "anchor.json").read_text(encoding="utf-8"))["period"]
    res = _run("--anchor", period, "--out", str(tmp_path))
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    assert all((tmp_path / f"{n}.json").read_bytes() == (FACTS / f"{n}.json").read_bytes() for n in BUILT)
    y, k = int(period[:4]), int(period[-1])
    nxt = f"{y + 1}Q1" if k == 4 else f"{y}Q{k + 1}"
    out = tmp_path / "next"
    res = _run("--anchor", nxt, "--out", str(out))
    if res.returncode == 0:                 # листы уже продлены на следующий квартал — кандидат собран
        assert json.loads((out / "anchor.json").read_text(encoding="utf-8"))["period"] == nxt
    else:
        assert "ПРОВАЛ сборки" in res.stderr and nxt in res.stderr and not out.exists()


def test_derived_mgmt_rows_carry_calc():
    """Каждая строка листа управленческих метрик с записью «выведено», попавшая в факты, несёт вывод
    в calc (или прямой документ в src и вывод как сверку) — AUDIT-1 № 36."""
    import csv
    root = handoff()
    fields = {"nim": "nim", "cor_total": "cor", "cor_corporate": "cor_corporate", "cor_retail": "cor_retail",
              "cir": "cir", "roe": "roe", "cost_of_funding": "cost_of_funding", "asset_yield": "asset_yield",
              "retail_deposit_cost": "cost_retail_funds", "corp_deposit_cost": "cost_corp_funds", "n20_0": "n20_0"}
    mq = json.loads((FACTS / "mgmt_quarterly.json").read_text(encoding="utf-8"))
    checked, bad = 0, []
    with open(root / "stage1" / "mgmt" / "mgmt_kpi.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if "выведено" not in (r["note"] or "") or r["metric"] not in fields:
                continue
            p, key = r["period"], fields[r["metric"]]
            if p in mq["quarters"]:
                node = mq["quarters"][p][key]
            elif p.endswith("FY") and p[:4] in mq["annual"] and key in mq["annual"][p[:4]]:
                node = mq["annual"][p[:4]][key]
            else:
                continue
            checked += 1
            calc = node.get("calc") or ""
            if "выведено" not in calc and "напечатано" not in calc:
                bad.append(f"{p}.{key}")
    assert checked >= 14, f"проверено узлов: {checked}"
    assert not bad, f"выведенные метрики без calc: {bad}"
