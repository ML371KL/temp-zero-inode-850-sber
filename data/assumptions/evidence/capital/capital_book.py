# -*- coding: utf-8 -*-
"""Числа книги 1.0 по капиталу из выходов листа (capital_calib.py, gap_additive.py) — сверка с шаблоном книги.

Считает: Ded20_0 и поправку Н20.0 (аддитивная форма), Ded11_0 и поправку Н20.1 (прокси — Н1.1 банка), плотности RWA
книг (розница без ипотеки — средневзвешенно по экспозициям), долю ОФЗ в корректировке FVOCI (признание в нормативе),
дюрацию FVOCI. Факты якоря — МСФО 6М26 (sha256 5b3a3de8…: BV с. 5; CET1, RWA, T2 с. 68; фонд FVOCI с. 44),
форма 0409123 на 01.07.2026 (базовый капитал банка), форма 0409135 (Н1.1 банка) — в листах out/ с источниками.
Запуск из корня репозитория (после capital_calib.py и gap_additive.py):
  python -B data/assumptions/evidence/capital/capital_book.py
"""
from __future__ import annotations

import ast
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

ANCHOR_Q = "2026Q2"


def _row(name: str, q: str) -> dict:
    for r in csv.DictReader(open(os.path.join(OUT, name), encoding="utf-8")):
        if r["q"] == q:
            return r
    raise KeyError(q)


def _dict_after(text: str, marker: str, *, inside: bool = False) -> dict:
    """Словарь Python, напечатанный в out.txt после метки (inside — метка внутри словаря; строка — первая с 2025-12-31)."""
    lines = [ln for ln in text.splitlines() if marker in ln]
    line = next((ln for ln in lines if "2025-12-31" in ln), lines[0]) if inside else lines[0]
    start = line.index("{") if inside else line.index("{", line.index(marker))
    return ast.literal_eval(line[start:line.rindex("}") + 1])


def compute() -> dict:
    book = evlib.template()
    f = book["capital"]["n20"]["fvoci_recognition"]
    gap = json.load(open(os.path.join(OUT, "gap_additive_out.json"), encoding="utf-8"))
    n11 = _row("n11_bridge_quarterly.csv", ANCHOR_Q)
    g20 = _row("gap_n20_quarterly.csv", ANCHOR_Q)
    bv, dp, e = float(n11["bv_common"]), float(n11["div_pending"]), float(n11["unaudited_np_ifrs"])
    res_fvoci = float(g20["fvoci_reserve"])
    base_bank = round(float(n11["cap_base_bank"]), 1)                 # форма 0409123 на 01.07.2026, млрд ₽
    bvreg_minus_e = round(bv + dp - (1 - f) * res_fvoci - e, 1)       # BV + DPreg − (1 − f)R − E
    rwa0 = gap["rwa0"]
    n11_fact = round(float(n11["n1_1_bank"]) / 100, 5)
    ded11 = round(bvreg_minus_e - base_bank, 1)
    n11_model = base_bank / rwa0
    text = open(os.path.join(OUT, "out.txt"), encoding="utf-8").read()
    dens = _dict_after(text, "плотность книг после масштаба")
    expo = _dict_after(text, "экспозиции")
    scale = float(re.search(r"масштаб ([0-9.]+);", text).group(1))
    ro = ("consumer", "cards", "auto")
    retail_other = sum(dens[k] * expo[k] for k in ro) / sum(expo[k] for k in ro)
    fv = _dict_after(text, "'ofz_share'", inside=True)
    dur = _dict_after(text, "тенор  5.0 лет, кварталы 2024–2026")
    return {
        "ded20_anchor": gap["ded20_anchor"],
        "n20_gap_pp": round(gap["anchor"]["gap_pp"] / 100, 4),
        "n20_gap_history": {"2024_2026": gap["window_2024_2026"], "2025_2026": gap["window_2025_2026"]},
        "bvreg_minus_e": bvreg_minus_e, "base_capital_bank": base_bank,
        "ded11_anchor": ded11,
        "n11_model": round(n11_model, 5), "n11_fact": n11_fact, "n11_gap_pp": round(n11_fact - n11_model, 4),
        "rwa_bank_to_group": float(n11["rwa_bank_to_B"]),
        "density_scale": scale,
        "density": {"corp_loans": dens["corp_loans"], "mortgage": dens["mortgage"], "mortgage_sub": dens["mortgage"],
                    "retail_loans": round(retail_other, 3), "securities": dens["securities"],
                    "liquidity": dens["liquidity"], "other_assets": dens["other_assets"]},
        "fvoci_ofz_share_2025": fv["ofz_share"],
        "fvoci_duration": {"D": dur["D"], "se": dur["se"], "r2": dur["r2"], "n": dur["n"]},
    }


MOVED = ("capital.n20.deductions_anchor", "capital.n20.gap_pp", "capital.n11.deductions_anchor", "capital.n11.gap_pp")
DENSITY_COMMON_TOL = 1e-5            # плотности книги на новом якоре — плотности листа × один множитель (6 знаков книги)


def moved_check(res: dict, book: dict) -> list[str]:
    """Книга на якоре позже листа (перезаякоривание, MODEL §15.2): вычеты и поправки на новом якоре выводит перенос
    (вычеты — формулой книги на фактах якоря, поправки — чтобы якорь воспроизводил нормативы отчёта), плотности —
    общим множителем к RWA якоря. Лист сверяет Ded20_0 = BV − CET1 по фактам якоря книги и общий множитель плотностей;
    поправки и Ded11_0 сверяет сам перенос (критерий 4 docs/REANCHOR.md §1.5)."""
    facts = evlib.BOOK_DIR.parent / "facts"
    bal = json.loads((facts / "balance.json").read_text(encoding="utf-8"))
    cap = json.loads((facts / "capital.json").read_text(encoding="utf-8"))
    bv, cet1 = bal["equity"]["bv_common"]["v"], cap["basel"]["cet1"]["v"]
    c = book["capital"]
    ded20 = round(bv - cet1, 1)
    res["book_anchor"] = {"anchor_period": book["meta"]["anchor_period"], "bv_common": bv, "basel_cet1": cet1,
                          "ded20_anchor": ded20, "moved_keys": {p: evlib.get(book, p) for p in MOVED}}
    ratios = {k: c["rwa"]["density"][k] / v for k, v in res["density"].items()}
    k = sum(ratios.values()) / len(ratios)
    res["book_anchor"]["density_factor"] = round(k, 6)
    bad = []
    if abs(c["n20"]["deductions_anchor"] - ded20) > 0.05:
        bad.append(f"capital.n20.deductions_anchor: книга {c['n20']['deductions_anchor']} против BV − CET1 якоря {ded20}")
    bad += [f"capital.rwa.density.{b}: множитель {r:.6f} против общего {k:.6f}" for b, r in ratios.items()
            if abs(r - k) > DENSITY_COMMON_TOL]
    return bad


def compare(res: dict, book: dict) -> list[str]:
    """Расхождения чисел листа с шаблоном книги (пусто — книга воспроизводится). Книга на якоре позже листа —
    `moved_check`: ключи переноса сверяет правило переноса, остальные — лист."""
    c = book["capital"]
    moved = evlib.moved(book)
    pairs = [
        ("capital.n20.deductions_anchor", c["n20"]["deductions_anchor"], res["ded20_anchor"], 0.05),
        ("capital.n20.gap_pp", c["n20"]["gap_pp"], res["n20_gap_pp"], 0.00005),
        ("capital.n11.deductions_anchor", c["n11"]["deductions_anchor"], res["ded11_anchor"], 0.05),
        ("capital.n11.gap_pp", c["n11"]["gap_pp"], res["n11_gap_pp"], 0.00005),
        ("capital.n20.fvoci_recognition", c["n20"]["fvoci_recognition"], res["fvoci_ofz_share_2025"], 0.005),
        ("oci.fvoci_duration", book["oci"]["fvoci_duration"], res["fvoci_duration"]["D"], 0.05),
    ] + [(f"capital.rwa.density.{k}", c["rwa"]["density"][k], v, 0.0005) for k, v in res["density"].items()]
    if moved:
        pairs = [p for p in pairs if p[0] not in MOVED and not p[0].startswith("capital.rwa.density.")]
    bad = [f"{p}: книга {b} против листа {v}" for p, b, v, tol in pairs if abs(b - v) > tol]
    return bad + (moved_check(res, book) if moved else [])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    bad = compare(res, evlib.template())
    res["book_mismatches"] = bad
    evlib.write_json(evlib.Path(OUT) / "capital_book_out.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
