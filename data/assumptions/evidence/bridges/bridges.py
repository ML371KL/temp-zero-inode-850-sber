# -*- coding: utf-8 -*-
"""Мосты базисов и строки ОПУ для книги — перенос сверок критика этапа 1 (cor_bridge, ras_ifrs_bridge,
misc_hist) плюс числа строк ОПУ, на которые опираются ключи fees, opex, noncore, other, tax, pnl, nii.dia_rate.

Базисы не смешиваются: МСФО группы (ifrs_quarterly / ifrs_history), упр. метрики (mgmt_kpi), РСБУ банка (релизы).
  * мост CoR: CoR движка = −llp_debt_fa / средние валовые кредиты АС × 365/d (MODEL §4.6) против упр. «стоимости
    риска» (окно 1К24–2К26) → b_cor аддитивный;
  * мост ЧПМ: из листа ЧПД (evidence/nii/out/history_fit.csv) — упр. минус движок, то же окно;
  * сезонный мост прибыли РСБУ банка → ЧП акционерам МСФО по кварталам (нау-каст);
  * «прочее» ОПУ = PBT − (ЧПД + резервы по долговым ФА + ЧКД + страхование + непрофильный + расходы), страхование =
    выручка + расходы на услуги (финансовые расходы страхования — в «прочем»; MODEL §4.7);
  * квартальный профиль «прочего» (книга 1.1, решение ведущего A9): доли кварталов в годовой сумме по полным годам
    2024–2025 — сумма квартала за два года / сумма двух лет; доля с обратным знаком (квартал-прибыль при годовом убытке)
    — около нуля → 0, остальные нормируются к 1; округление — 3 знака, Σ = 1 точно (MODEL §4.7);
  * ЧКД и расходы 1П26 г/г, непрофильный результат, эффективная ставка налога, доля неконтролирующих, взносы АСВ.
Входы — малые входы inputs/ (evidence/extract_inputs.py). Запуск из корня репозитория:
  python -B data/assumptions/evidence/bridges/bridges.py      → out/bridges_out.json
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S1 = os.path.join(HERE, "inputs", "stage1")
OUT = os.path.join(HERE, "out")
NII_FIT = os.path.join(os.path.dirname(HERE), "nii", "out", "history_fit.csv")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

QEND = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _rows(*parts):
    return list(csv.DictReader(open(os.path.join(S1, *parts), encoding="utf-8")))


def load():
    q: dict = {}
    for r in _rows("ifrs", "ifrs_quarterly.csv"):
        q.setdefault(r["metric"], {})[r["period"]] = float(r["value"])
    h: dict = {}
    for r in _rows("ifrs", "ifrs_history.csv"):
        try:
            h.setdefault(r["metric"], {})[r["period"]] = float(r["value"])
        except ValueError:
            pass
    m: dict = {}
    for r in _rows("mgmt", "mgmt_kpi.csv"):
        if r["metric"] in ("cor_total", "provisions_credit_mgmt", "nim"):
            m[(r["metric"], r["period"])] = float(r["value"])
    ras = {r["month"]: r["np_ytd"] for r in _rows("ras", "ras_release_monthly_wide.csv")}
    return q, h, m, ras


def cor_bridge(q, h, m) -> dict:
    llp, gross = q["llp_debt_fa"], h["loans_ac_total_gross"]
    rows = []
    for y in range(2023, 2027):
        for k in range(1, 5):
            p = f"{k}Q{y}"
            if p not in llp:
                continue
            e = dt.date(y, *QEND[k])
            s = dt.date(y - (k == 1), *QEND[4 if k == 1 else k - 1])
            ge, gs = gross.get(e.isoformat()), gross.get(s.isoformat())
            if ge is None or gs is None:
                continue
            avg = (ge + gs) / 2
            cor_eng = -llp[p] / avg * 365 / (e - s).days * 100
            cm = m.get(("cor_total", f"{y}Q{k}"))
            rows.append({"period": f"{y}Q{k}", "days": (e - s).days, "avg_loans_ac": round(avg, 1),
                         "llp_debt_fa": llp[p], "cor_engine_pct": round(cor_eng, 2), "cor_mgmt_pct": cm,
                         "gap_pp": None if cm is None else round(cor_eng - cm, 2)})
    w = [r for r in rows if r["cor_mgmt_pct"] is not None and r["period"] >= "2024Q1"]
    g = [r["gap_pp"] for r in w]
    return {"rows": rows, "window": [w[0]["period"], w[-1]["period"]], "n": len(g),
            "additive_mean_pp": round(st.mean(g), 3), "sd_pp": round(st.stdev(g), 3),
            "ratio_mean": round(st.mean(r["cor_engine_pct"] / r["cor_mgmt_pct"] for r in w), 3)}


def nim_bridge() -> dict:
    rows = [r for r in csv.DictReader(open(NII_FIT, encoding="utf-8"))
            if "2024Q1" <= r["quarter"] <= "2026Q2" and r["nim_fact_eng"] and r["nim_fact_mgmt"]]
    d = [float(r["nim_fact_mgmt"]) - float(r["nim_fact_eng"]) for r in rows]
    return {"window": [rows[0]["quarter"], rows[-1]["quarter"]], "n": len(d),
            "mgmt_minus_engine_pp": round(st.mean(d), 3), "sd_pp": round(st.stdev(d), 3)}


def ras_ifrs_bridge(q, ras) -> dict:
    ifrs = q["np_shareholders"]
    out = {}
    for y in (2023, 2024, 2025, 2026):
        prev = 0.0
        for k, mm in ((1, "03"), (2, "06"), (3, "09"), (4, "12")):
            v = ras.get(f"{y}-{mm}")
            if not v:
                prev = None
                continue
            v = float(v)
            rq = None if prev is None else v - prev
            prev = v
            iq = ifrs.get(f"{k}Q{y}")
            if rq and iq:
                out[(y, k)] = (rq, iq, iq / rq)
    by_q = {}
    for k in (1, 2, 3, 4):
        xs = [v[2] for (y, kk), v in sorted(out.items()) if kk == k]
        by_q[str(k)] = {"mean": round(st.mean(xs), 3), "n": len(xs), "values": [round(x, 3) for x in xs]}
    years = {}
    for y in (2023, 2024, 2025):
        ks = [out.get((y, k)) for k in (1, 2, 3, 4)]
        if all(ks):
            years[str(y)] = round(sum(v[1] for v in ks) / sum(v[0] for v in ks), 3)
    rows = [{"period": f"{y}Q{k}", "ras": round(r, 1), "ifrs": i, "ratio": round(b, 3)} for (y, k), (r, i, b) in sorted(out.items())]
    return {"rows": rows, "by_quarter": by_q, "by_year": years}


def _year(q, metric, y, quarters=(1, 2, 3, 4)):
    vals = [q[metric].get(f"{k}Q{y}") for k in quarters]
    return None if any(v is None for v in vals) else sum(vals)


def pnl_lines(q, h) -> dict:
    out: dict = {"misc": {}, "insurance_net": {}, "noncore": {}}
    for y in (2024, 2025, 2026):
        ks = [k for k in (1, 2, 3, 4) if f"{k}Q{y}" in q["pbt"]]
        misc = ins = nc = 0.0
        for k in ks:
            p = f"{k}Q{y}"
            g = lambda mm: q[mm][p]  # noqa: E731
            i_ = g("ins_revenue") + g("ins_service_expense")
            n_ = g("noncore_revenue") + g("noncore_cost")
            fees = g("fee_income") + g("fee_expense")
            misc += g("pbt") - (g("nii") + g("llp_debt_fa") + fees + i_ + n_ + g("opex"))
            ins += i_
            nc += n_
        lab = str(y) if len(ks) == 4 else f"{len(ks) * 3}M{y}"
        out["misc"][lab], out["insurance_net"][lab], out["noncore"][lab] = round(misc, 1), round(ins, 1), round(nc, 1)
    fees = {y: [q["fee_income"][f"{k}Q{y}"] + q["fee_expense"][f"{k}Q{y}"] for k in (1, 2, 3, 4) if f"{k}Q{y}" in q["fee_income"]]
            for y in (2025, 2026)}
    fy25, h1_26, h1_25 = sum(fees[2025]), sum(fees[2026][:2]), sum(fees[2025][:2])
    h2_25 = sum(fees[2025][2:])
    opex_h1 = {y: -sum(q["opex"][f"{k}Q{y}"] for k in (1, 2)) for y in (2025, 2026)}
    tax_eff = {"2025": round(_year(q, "tax", 2025) / -_year(q, "pbt", 2025), 4),
               "6M2026": round(sum(q["tax"][f"{k}Q2026"] for k in (1, 2)) / -sum(q["pbt"][f"{k}Q2026"] for k in (1, 2)), 4)}
    np25, nci25 = _year(q, "np", 2025), _year(q, "np_nci", 2025)
    dia_6m = -sum(q["ie_deposit_insurance"][f"{k}Q2026"] for k in (1, 2))
    fl = h["deposits_individuals"]
    fl_avg = (fl["2025-12-31"] + fl["2026-06-30"]) / 2
    days_6m = (dt.date(2026, 6, 30) - dt.date(2025, 12, 31)).days
    return {
        **out,
        "fees": {"H1_2026": round(h1_26, 1), "H1_2025": round(h1_25, 1), "yoy_H1": round(h1_26 / h1_25 - 1, 4),
                 "FY2025": round(fy25, 1), "H2_2025": round(h2_25, 1),
                 "H2_2026_if_year_flat": round(fy25 - h1_26, 1), "yoy_H2_if_year_flat": round((fy25 - h1_26) / h2_25 - 1, 4)},
        "opex": {"H1_2026": round(opex_h1[2026], 1), "H1_2025": round(opex_h1[2025], 1),
                 "yoy_H1": round(opex_h1[2026] / opex_h1[2025] - 1, 4)},
        "tax_effective": tax_eff,
        "tax_effective_gap_vs_25pct": round(st.mean(tax_eff.values()) - 0.25, 4),
        "nci_share_2025": round(nci25 / np25, 5),
        "dia_rate_6M2026": round(dia_6m * 365 / days_6m / fl_avg, 5),
        "dia_inputs": {"dia_6m": round(dia_6m, 1), "retail_funds_avg": round(fl_avg, 1), "days": days_6m},
    }


PROFILE_YEARS = (2024, 2025)                  # полные годы с раскрытыми строками «прочего» (2023 — свёрнутая форма)


def misc_profile(q) -> dict:
    """Доли кварталов в годовой сумме «прочего» по полным годам PROFILE_YEARS (MODEL §4.7, other.misc_quarter_shares)."""
    by_q = {}
    for y in PROFILE_YEARS:
        for k in (1, 2, 3, 4):
            p = f"{k}Q{y}"
            g = lambda mm: q[mm][p]  # noqa: E731
            misc = g("pbt") - (g("nii") + g("llp_debt_fa") + g("fee_income") + g("fee_expense") + g("ins_revenue")
                               + g("ins_service_expense") + g("noncore_revenue") + g("noncore_cost") + g("opex"))
            by_q.setdefault(str(k), {})[str(y)] = round(misc, 1)
    sums = {k: round(sum(v.values()), 1) for k, v in by_q.items()}
    total = sum(sums.values())
    raw = {k: v / total for k, v in sums.items()}
    kept = {k: (v if v > 0 else 0.0) for k, v in raw.items()}        # квартал с обратным знаком — около нуля
    norm = sum(kept.values())
    shares = {k: round(v / norm, 3) for k, v in kept.items()}
    top = max(shares, key=shares.get)                                 # Σ = 1 точно: остаток округления — в крупнейшую долю
    shares[top] = round(1.0 - sum(v for k, v in shares.items() if k != top), 3)
    by_year = {str(y): {k: round(by_q[k][str(y)] / sum(by_q[kk][str(y)] for kk in by_q), 3) for k in by_q}
               for y in PROFILE_YEARS}
    return {"years": list(PROFILE_YEARS), "by_quarter": by_q, "sum_by_quarter": sums, "total": round(total, 1),
            "shares_raw": {k: round(v, 4) for k, v in raw.items()}, "shares_by_year": by_year, "shares": shares}


def compute() -> dict:
    q, h, m, ras = load()
    return {"cor_bridge": cor_bridge(q, h, m), "nim_bridge": nim_bridge(), "ras_to_ifrs": ras_ifrs_bridge(q, ras),
            "pnl": pnl_lines(q, h), "misc_profile": misc_profile(q)}


def compare(res: dict, book: dict) -> list[str]:
    """Расхождения с книгой (пусто — книга воспроизводится): округлённые ключи шаблона против листа. Квартальный
    профиль «прочего» (A-O3) с книги 1.5 сверяет лист evidence/misc (средние кварталы 2024К1–2026К3); здесь — профиль
    2024–2025 годов книг 1.1–1.4, справочно."""
    p = res["pnl"]
    pairs = [
        ("nii.dia_rate", book["nii"]["dia_rate"], round(p["dia_rate_6M2026"], 4), 0.00005),
        ("tax.effective_gap", book["tax"]["effective_gap"], round(p["tax_effective_gap_vs_25pct"], 3), 0.0005),
        ("pnl.nci_share (≈0)", book["pnl"]["nci_share"], p["nci_share_2025"], 0.001),
        ("checks.bridge_drift_pp.cor ≈ 2,5σ моста CoR", book["checks"]["bridge_drift_pp"]["cor"],
         round(2.5 * res["cor_bridge"]["sd_pp"] / 100, 3), 0.0005),
    ]
    return [f"{k}: книга {b} против листа {v}" for k, b, v, tol in pairs if abs(b - v) > tol]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    res["book_mismatches"] = compare(res, evlib.template())
    evlib.write_json(evlib.Path(OUT) / "bridges_out.json", res)
    c, n = res["cor_bridge"], res["nim_bridge"]
    print(f"мост CoR движок − упр.: {c['additive_mean_pp']:+.3f} п.п. ± {c['sd_pp']:.3f} (n {c['n']}, {c['window']}), отношение {c['ratio_mean']}")
    print(f"мост ЧПМ упр. − движок: {n['mgmt_minus_engine_pp']:+.3f} п.п. (n {n['n']}, {n['window']})")
    print("РСБУ → МСФО по кварталам:", {k: v["mean"] for k, v in res["ras_to_ifrs"]["by_quarter"].items()}, "годы:", res["ras_to_ifrs"]["by_year"])
    print("«прочее»:", res["pnl"]["misc"], "страхование:", res["pnl"]["insurance_net"], "непрофильный:", res["pnl"]["noncore"])
    mp = res["misc_profile"]
    print("«прочее» по кварталам", mp["years"], ":", mp["sum_by_quarter"], "→ доли", mp["shares"], "(сырые", mp["shares_raw"], ")")
    print("ЧКД:", res["pnl"]["fees"], "расходы:", res["pnl"]["opex"])
    print("налог:", res["pnl"]["tax_effective"], "НКД:", res["pnl"]["nci_share_2025"], "АСВ:", res["pnl"]["dia_rate_6M2026"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
