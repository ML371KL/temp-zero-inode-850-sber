# -*- coding: utf-8 -*-
"""Разрыв Н20.0 «норматив − расчёт» в аддитивной форме вычетов (решение ведущего к книге 1.0, п. 5; MODEL §4.11).

K20 = BV − (1 − f)·R − Ded20_0·RWA_q/RWA_0 + DP + AT1 + T2;  gap = Н20.0 − K20/RWA.
Перенос `_scratch/specfix/gap_additive.py` этапа 2: входы — выходы capital_calib.py (out/gap_n20_quarterly.csv,
out/gap_n20_model_form.csv), якорь — факты МСФО 6М26 (BV обыкн. 8 387,2 с. 5; CET1 Базель 7 450,5 с. 68;
RWA Базель 59 318,1 с. 68). Запуск из корня репозитория: python -B data/assumptions/evidence/capital/gap_additive.py
"""
from __future__ import annotations

import csv
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
BV0, CET1_0, RWA0 = 8387.2, 7450.5, 59318.1          # МСФО 6М26 с. 5 и 68 (sha256 5b3a3de8…)
DED0 = BV0 - CET1_0                                  # Ded20_0 = 936,7


def compute() -> dict:
    g = {r["q"]: r for r in csv.DictReader(open(os.path.join(OUT, "gap_n20_quarterly.csv"), encoding="utf-8"))}
    m = {r["q"]: r for r in csv.DictReader(open(os.path.join(OUT, "gap_n20_model_form.csv"), encoding="utf-8"))}
    rows = []
    for q in sorted(m):
        r, k = g[q], m[q]
        if not r["fvoci_reserve"]:
            continue
        f, t2 = float(k["f"]), float(k["t2"])
        bv, res, dp, at1, rwa = (float(r[x]) for x in ("bv_common", "fvoci_reserve", "div_pending", "at1", "rwa_B"))
        k20 = bv - (1 - f) * res - DED0 * rwa / RWA0 + dp + at1 + t2
        gap = float(r["n20_reg"]) / 100 - k20 / rwa
        rows.append({"q": q, "k20": round(k20, 1), "n20_model_pct": round(100 * k20 / rwa, 3),
                     "n20_fact_pct": float(r["n20_reg"]), "gap_pp": round(100 * gap, 3)})

    def stats(first):
        w = [x["gap_pp"] for x in rows if x["q"] >= first]
        return {"n": len(w), "mean_pp": round(st.mean(w), 3), "sd_pp": round(st.stdev(w), 3)}

    return {"ded20_anchor": round(DED0, 1), "rwa0": RWA0, "rows": rows, "anchor": rows[-1],
            "window_2024_2026": stats("2024Q1"), "window_2025_2026": stats("2025Q1")}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    for x in res["rows"]:
        print(x["q"], x["k20"], x["n20_model_pct"], x["n20_fact_pct"], x["gap_pp"])
    print("2024–2026:", res["window_2024_2026"], "2025–2026:", res["window_2025_2026"], "якорь:", res["anchor"])
    with open(os.path.join(OUT, "gap_additive_out.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
