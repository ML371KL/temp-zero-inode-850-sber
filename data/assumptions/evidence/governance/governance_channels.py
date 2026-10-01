# -*- coding: utf-8 -*-
"""Дисконт за управление по каналам снизу вверх (A-V7; решения ведущего к книге 1.0, п. 9, В5 и В16; схема книги «Ленты»).

Каналы взаимоисключающие, все — доли капитала модели V0 печатаемой медианы: к V0 слоя дисконт и применяется
(MODEL §8.2); знак «+» увеличивает дисконт. Налоги, капитал и кризис заданы в книге явно (tax.statutory, tax.one_off,
capital.*, режим crisis) и в дисконт не входят; канал без опоры — 0.
  (а) квазифискальные сделки и сделки со связанными сторонами: поток покупок не по профилю × доля потери /
      (k_T − g_T) мира H → PV в рублях; доля — неподвижной точкой g = PV / V0(g), где V0 × (1 − g) × 1000 / N_out —
      медиана выпуска (inputs: fixed_point); ключ книги — точка, округлённая к шагу ключа, и лист отказывает, если
      ключ отстоит от точки больше чем на половину шага (MODEL §8.2); доля капитала якоря BV_0 — справочно;
  (б) изъятия сверх налоговой оси: измеренная история («налог на сверхприбыль» 2023) — внутри налоговых осей книги → 0;
  (в) ликвидность и листинг: первый уровень, оборот дня к капитализации → 0.
k_T мира H = уровень кривой в терминале (таблица сборки миров, 12,7 %) + β_E × ERP книги; g_T = (1 + π_LT(H))(1 + g_real) − 1.
Запуск из корня репозитория: python -B data/assumptions/evidence/governance/governance_channels.py → out/governance_out.json
"""
from __future__ import annotations

import csv
import os
import statistics as st
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "inputs", "governance_inputs.yaml")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

CURVE_LT_H = 0.127        # уровень кривой мира H в терминале — таблица сборки миров семейства (build_assumptions.py)
BV0 = 8387.2              # капитал обыкновенных акционеров 30.06.2026, МСФО 6М26 с. 5 (sha256 5b3a3de8…)
SHARES_ORD_ISSUED = 21586.948   # размещённые обыкновенные, млн (ISS ISSUESIZE; evidence/dividends)


def compute() -> dict:
    book = evlib.template()
    rec = evlib.record()
    inp = yaml.safe_load(open(INPUTS, encoding="utf-8"))
    v = book["valuation"]
    k_t = CURVE_LT_H + v["beta_e"] * v["erp"]
    g_t = (1 + rec["lt_inflation"]["H"] / 100) * (1 + v["terminal"]["real_growth"]) - 1
    qf = inp["quasi_fiscal"]
    paid = sum(d["amount"] for d in qf["deals"])
    flow = paid / qf["window"]["years"]
    ls = qf["loss_share"]
    pv = {k: (flow if k != "high" else paid) * ls[k] / (k_t - g_t) for k in ("center", "low", "high")}
    fp = qf["fixed_point"]
    cap = fp["median_rub"] * fp["shares_outstanding_mln"] / 1000        # V0 × (1 − g) медианы, млрд ₽
    ch_a = {k: round(x / (x + cap), 5) for k, x in pv.items()}           # g = PV / V0(g) ⇔ g = PV / (PV + V0 × (1 − g))
    step = fp["step"]
    value = round(round(ch_a["center"] / step) * step, 6)
    w = inp["withdrawals"]
    liq = inp["liquidity"]
    rows = list(csv.DictReader(open(os.path.join(HERE, liq["prices_file"]), encoding="utf-8")))
    win = [r for r in rows if liq["window"]["from"] <= r["TRADEDATE"] <= liq["window"]["to"]]
    val = [float(r["VALUE"]) / 1e9 for r in win]
    last = win[-1]
    cap_ord = (SHARES_ORD_ISSUED - liq["treasury_ordinary_mln"]) * float(last["LEGALCLOSEPRICE"] or last["CLOSE"]) / 1000
    channels = {
        "quasi_fiscal": {"value": value, "pv_bn": round(pv["center"], 1), "range": [ch_a["low"], ch_a["high"]],
                         "paid_bn": paid, "flow_bn_per_year": round(flow, 2), "loss_share": ls["center"],
                         "k_T_H": round(k_t, 5), "g_T_H": round(g_t, 5),
                         "fixed_point": {"median_rub": fp["median_rub"], "median_of": fp["median_of"],
                                         "v0_median_bn": round(pv["center"] + cap, 1),
                                         "share_of_v0": ch_a["center"], "step": step, "half_step": step / 2},
                         "share_of_bv0": round(pv["center"] / BV0, 5)},
        "withdrawals": {"value": 0.0, "windfall_tax_2023_bn": w["windfall_tax_2023"]["amount"],
                        "windfall_share_of_bv_2023": round(w["windfall_tax_2023"]["amount"] / w["bv_common_2023_end"]["amount"], 5),
                        "note": "история изъятий мала и уже внутри налоговых осей книги (tax.statutory сдвиг 0–0,03; tax.one_off P 0–0,30)"},
        "liquidity_listing": {"value": 0.0, "listing_level": liq["listing_level"]["value"], "imoex_weight": liq["imoex_weight"]["value"],
                              "median_daily_value_bn": round(st.median(val), 2), "days": len(val),
                              "market_cap_ordinary_bn": round(cap_ord, 0),
                              "turnover_to_cap_pct_per_day": round(100 * st.median(val) / cap_ord, 3)},
    }
    total = sum(c["value"] for c in channels.values())
    return {"channels": channels, "total": round(total, 6)}


def compare(res: dict, book: dict) -> list[str]:
    g = book["valuation"]["governance"]
    comp = {c["id"]: c["value"] for c in g["components"]}
    bad = [f"канал {k}: книга {comp.get(k)} против листа {c['value']}" for k, c in res["channels"].items()
           if abs(comp.get(k, -1) - c["value"]) > 1e-12]
    if abs(g["discount"] - sum(c["sign"] * c["value"] for c in g["components"])) > 1e-12:
        bad.append("valuation.governance.discount ≠ Σ sign × value (инвариант governance_sum)")
    if abs(g["discount"] - res["total"]) > 1e-12:
        bad.append(f"итог книги {g['discount']} против листа {res['total']}")
    fp = res["channels"]["quasi_fiscal"]["fixed_point"]
    if abs(comp.get("quasi_fiscal", -1) - fp["share_of_v0"]) > fp["half_step"] + 1e-12:
        bad.append(f"канал quasi_fiscal: ключ {comp.get('quasi_fiscal')} дальше половины шага от неподвижной точки "
                   f"{fp['share_of_v0']} (медиана {fp['median_rub']} ₽)")
    return bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    res["book_mismatches"] = compare(res, evlib.template())
    evlib.write_json(evlib.Path(OUT) / "governance_out.json", res)
    for k, c in res["channels"].items():
        print(k, c)
    fp = res["channels"]["quasi_fiscal"]["fixed_point"]
    print(f"неподвижная точка: {100 * fp['share_of_v0']:.3f} % V0 на медиане {fp['median_rub']} ₽ ({fp['median_of']}); "
          f"шаг {100 * fp['step']:.2f} п.п. → ключ {100 * res['channels']['quasi_fiscal']['value']:.2f} %")
    print("итог:", res["total"], "; расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
