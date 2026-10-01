# -*- coding: utf-8 -*-
"""Формула пула и DPS дивполитики 2023 на истории 2023–2025 (MODEL §5.2, прил. C) и акции якоря.

Политика 2023 (dividend_policy_2023.pdf, sha256 f486aa17…; п. 1.6, 2.3):
  база_Y = ЧП акционерам МСФО_Y − купон AT1_Y × (1 − τ_Y);  пул_Y = 0,5 × база_Y;  DPS_Y = пул_Y × 1000 / N_iss,
N_iss — размещённые акции обеих категорий (ISS ISSUESIZE: 21 586 948 000 + 1 000 000 000). Точная десятичная
арифметика; округление «вверх до копейки» (ceil_kopeck) против «половины вверх».
Акции в обращении на якоре: собственные = дивиденды на собственные акции за 2025 (7,9 обыкн. + 1,3 прив., МСФО 6М26
прим. 11, с. 43) / DPS 37,64; дивиденды к выплате = объявлено 850,2 − 7,9 − 1,3 (там же).
Входы — inputs/ (evidence/extract_inputs.py): pool_check.csv и iss_description_*.json листа этапа 1 (market).
Запуск из корня репозитория: python -B data/assumptions/evidence/dividends/dps_history.py → out/dps_history_out.json
"""
from __future__ import annotations

import csv
import json
import os
import sys
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
SD = os.path.join(HERE, "inputs", "stage1", "market", "shares_dividends")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

KOP = Decimal("0.01")
# МСФО 6М26 прим. 11, с. 43 (sha256 5b3a3de8…): объявлено за 2025, из него — на собственные акции
DECLARED_2025 = Decimal("850.2")
ON_TREASURY_2025 = {"ordinary": Decimal("7.9"), "preferred": Decimal("1.3")}


def issued() -> dict:
    out = {}
    for sec in ("SBER", "SBERP"):
        d = json.load(open(os.path.join(SD, f"iss_description_{sec}.json"), encoding="utf-8"))["description"]
        cols = d["columns"]
        vals = {r[cols.index("name")]: r[cols.index("value")] for r in d["data"]}
        out[sec] = Decimal(vals["ISSUESIZE"]) / Decimal(1_000_000)          # млн шт.
    return out


def dps(ni: Decimal, cpn: Decimal, tau: Decimal, n_iss: Decimal, payout=Decimal("0.5")) -> dict:
    base = ni - cpn * (1 - tau)
    pool = payout * base
    exact = pool * 1000 / n_iss
    return {"base": base, "pool": pool, "exact": exact,
            "ceil_kopeck": exact.quantize(KOP, rounding=ROUND_CEILING),
            "half_up_kopeck": exact.quantize(KOP, rounding=ROUND_HALF_UP)}


def compute() -> dict:
    iss = issued()
    n_iss = iss["SBER"] + iss["SBERP"]
    rows = []
    for r in csv.DictReader(open(os.path.join(SD, "pool_check.csv"), encoding="utf-8")):
        ni, cpn, tau = Decimal(r["ni_to_shareholders_bn"]), Decimal(r["at1_coupon_bn"]), Decimal(r["tax_rate"])
        d = dps(ni, cpn, tau, n_iss)
        alt = dps(ni, cpn, Decimal("0.25"), n_iss)
        declared = Decimal(r["dps_declared"]).quantize(KOP)
        rows.append({"year": int(r["fiscal_year"]), "ni_shareholders": str(ni), "at1_coupon": str(cpn), "tau": str(tau),
                     "base": str(d["base"]), "pool": str(d["pool"]), "dps_exact": str(d["exact"].quantize(Decimal("1e-5"))),
                     "dps_ceil": str(d["ceil_kopeck"]), "dps_half_up": str(d["half_up_kopeck"]),
                     "dps_declared": str(declared), "ceil_matches": d["ceil_kopeck"] == declared,
                     "half_up_matches": d["half_up_kopeck"] == declared,
                     "dps_ceil_if_tau_25": str(alt["ceil_kopeck"]), "src": r["source"]})
    dps25 = Decimal("37.64")
    treasury = {k: (v * 1000 / dps25).quantize(Decimal("0.1")) for k, v in ON_TREASURY_2025.items()}
    outstanding = (n_iss - treasury["ordinary"] - treasury["preferred"]).quantize(Decimal("0.1"))
    return {"n_issued": {"ordinary": str(iss["SBER"]), "preferred": str(iss["SBERP"]), "total": str(n_iss)},
            "history": rows,
            "anchor": {"treasury_on_record_2026": {k: str(v) for k, v in treasury.items()},
                       "outstanding_total": str(outstanding),
                       "dividends_payable": str(DECLARED_2025 - sum(ON_TREASURY_2025.values()))}}


def compare(res: dict, book: dict) -> list[str]:
    p = book["dividends"]["policy"]
    bad = []
    if p["dps_rounding"] != "ceil_kopeck" or p["divisor"] != "issued" or p["payout"]["LT"] != 0.5:
        bad.append("политика книги не та, что проверена на истории (ceil_kopeck, issued, 0,5)")
    for r in res["history"]:
        if r["year"] in (2024, 2025) and not r["ceil_matches"]:
            bad.append(f"DPS {r['year']}: формула {r['dps_ceil']} ≠ объявленному {r['dps_declared']}")
    return bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    res["book_mismatches"] = compare(res, evlib.template())
    evlib.write_json(evlib.Path(OUT) / "dps_history_out.json", res)
    print("N_iss:", res["n_issued"])
    for r in res["history"]:
        print(r["year"], "база", r["base"], "пул", r["pool"], "DPS", r["dps_exact"], "вверх", r["dps_ceil"],
              "половина вверх", r["dps_half_up"], "объявлен", r["dps_declared"], "при τ 25 %", r["dps_ceil_if_tau_25"])
    print("якорь:", res["anchor"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
