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


def compare(res: dict, book: dict) -> list[str]:
    """Расхождения чисел листа с шаблоном книги (пусто — книга воспроизводится)."""
    c = book["capital"]
    pairs = [
        ("capital.n20.deductions_anchor", c["n20"]["deductions_anchor"], res["ded20_anchor"], 0.05),
        ("capital.n20.gap_pp", c["n20"]["gap_pp"], res["n20_gap_pp"], 0.00005),
        ("capital.n11.deductions_anchor", c["n11"]["deductions_anchor"], res["ded11_anchor"], 0.05),
        ("capital.n11.gap_pp", c["n11"]["gap_pp"], res["n11_gap_pp"], 0.00005),
        ("capital.n20.fvoci_recognition", c["n20"]["fvoci_recognition"], res["fvoci_ofz_share_2025"], 0.005),
        ("oci.fvoci_duration", book["oci"]["fvoci_duration"], res["fvoci_duration"]["D"], 0.05),
    ] + [(f"capital.rwa.density.{k}", c["rwa"]["density"][k], v, 0.0005) for k, v in res["density"].items()]
    return [f"{p}: книга {b} против листа {v}" for p, b, v, tol in pairs if abs(b - v) > tol]


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
