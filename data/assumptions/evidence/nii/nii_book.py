# -*- coding: utf-8 -*-
"""Блок nii книги против калибровки листа ЧПД (out/nii_book_proposal.yaml, out/results.json) и опоры A-N2/A-N3.

Сверяет: параметры 10 книг (mortgage — параметры ставки retail_loans калибровки, сектор ипотеки — решение ведущего;
остальные — как в калибровке; ближние ключи спредов — год якоря и следующий — не калибровка, а правило ставок якоря,
их сверяет near_spreads.py; `lt_shift` и `phi` у пассивов — суждения A-N7 и A-N8, их сверяют sigma0_split.py и
phi_split.py), долю текущих счетов
(ψ, ρ, границы), минимум ликвидной доли (долговые бумаги + ликвидность, без акций и фондов); печатает опоры:
  A-N2 — линия ЧПМ = a + b × ключевая (годовая регрессия 2013–2025 без 2014–15 и 2022, упр.) при ключевой мира H в LT;
         стационарный ЧПМ книг в мире H при σ0 = 0 (движок и упр. через мост ЧПМ);
  A-N3 — φ, при котором реализованная передача M − N равна 0,06; эмпирика и механика передачи.
Запуск из корня репозитория (после calib_nii.py): python -B data/assumptions/evidence/nii/nii_book.py → out/nii_book_out.json
"""
from __future__ import annotations

import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

RATE_KEYS = ("side", "ref", "rho", "beta", "spread", "phi", "lt_shift", "balancing")
DECIDED = {"nii.dia_rate"}           # решение книги: 0,0052 по 6М26 вместо среднего 1К23–2К26 0,0053 (сводка §1 п. 23)


def compute() -> dict:
    book = evlib.template()
    prop = yaml.safe_load(open(os.path.join(OUT, "nii_book_proposal.yaml"), encoding="utf-8"))
    res = json.load(open(os.path.join(OUT, "results.json"), encoding="utf-8"))
    rec = evlib.record()
    t = res["transmission"]
    line = t["empirical"]["nim_main"]
    key_h = [r for r in rec["rows"] if r["world"] == "H"][-1]["key_avg"]
    bridge = res["bridge_mgmt_minus_eng_2024Q1_2026Q2"]["nim"]
    real = next(r for r in t["phi_table"] if abs(r["T_star"] - book["nii"]["transmission"]["target"]) < 1e-12)
    mism = []
    pb, bb = prop["nii"]["books"], book["nii"]["books"]
    y0 = int(book["meta"]["first_period"][:4])
    near = {str(y0), str(y0 + 1)}                    # ближние ключи — правило ставок якоря (near_spreads.py, MODEL §4.4)
    for b in bb:
        src = pb["retail_loans"] if b == "mortgage" else pb[b]
        for k in RATE_KEYS:
            if k in ("lt_shift", "phi") and bb[b]["side"] == "liability":   # распределение σ0 и сжатия φ по пассивам —
                continue                                             # не калибровка (A-N7, A-N8)
            if k in src or k in bb[b]:
                sv = src.get(k)
                bv = bb[b].get(k)
                if isinstance(sv, dict):
                    sv = {str(x): y for x, y in sv.items() if not (k == "spread" and str(x) in near)}
                    bv = {str(x): y for x, y in (bv or {}).items() if not (k == "spread" and str(x) in near)}
                if sv != bv:
                    mism.append(f"nii.books.{b}.{k}: книга {bv} против калибровки {sv}")
    for k in ("psi", "rho", "bounds"):
        if prop["nii"]["retail_current_share"][k] != book["nii"]["retail_current_share"][k]:
            mism.append(f"nii.retail_current_share.{k}")
    if prop["volumes"]["liquid_min_share"] != book["volumes"]["liquid_min_share"]:
        mism.append("volumes.liquid_min_share")
    if prop["nii"]["transmission"]["target"] != book["nii"]["transmission"]["target"]:
        mism.append("nii.transmission.target")
    return {
        "a_n2": {"line_a": line["a"], "line_b": line["b"], "line_years": line["years"], "key_H_LT_pct": key_h,
                 "line_at_key_H_mgmt_pct": round(line["a"] + line["b"] * key_h, 3),
                 "nss_H_sigma0_0_engine_pct": t["nss_phi0"]["H"]["nss"],
                 "nss_H_sigma0_0_mgmt_pct": round(t["nss_phi0"]["H"]["nss"] + bridge, 2),
                 "bridge_nim_mgmt_minus_engine_pp": bridge,
                 "book_center_mgmt": book["nii"]["nim_lt_target_mgmt"]},
        "a_n3": {"target": book["nii"]["transmission"]["target"], "phi_for_realized": real["phi_for_realized"],
                 "local_T_H_at_that_phi": real["T_H_at_phi_realized"], "phi_books_weight": t["phi_books_weight"],
                 "mechanical_T_phi0": t["T_H_phi0"], "roe_factor_iea_bv_1mtax": t["roe_factor_iea_bv_1mtax"],
                 "empirical_nim_main_b": line["b"], "empirical_engine_b": t["empirical"]["nim_engine_basis"]["b"],
                 "empirical_quarterly_b": t["empirical"]["nim_quarterly_2019_2026"]["b"],
                 "disclosed_T": [d["T"] for d in t["disclosed"]]},
        "current_share": res["current_share"]["psi_pp_per_pp"], "current_share_se": res["current_share"]["se"]["a"],
        "liquid_share": res["liquid_share"], "subsidized": res["subsidized"],
        "near_spread_keys_by_anchor_rule": sorted(near),
        "book_mismatches": mism, "decided_differently": sorted(DECIDED),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    evlib.write_json(evlib.Path(OUT) / "nii_book_out.json", res)
    print("A-N2:", res["a_n2"])
    print("A-N3:", res["a_n3"])
    print("расхождения с калибровкой:", res["book_mismatches"] or "нет", "; решения книги иначе:", res["decided_differently"])
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
