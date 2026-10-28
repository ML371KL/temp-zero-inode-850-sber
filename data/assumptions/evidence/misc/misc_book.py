# -*- coding: utf-8 -*-
"""«Прочее» ОПУ, непрофильный результат и налог по фактам 2024К1–2026К3 — лист книги 1.5 (A-O2, A-O3, A-T1).

«Прочее» (MODEL §4.7) — остаток прибыли до налога без ЧПД, резервов по долговым ФА, ЧКД, страхования (выручка +
расходы на услуги), непрофильного результата и расходов; лист раскладывает его по строкам отчёта о прибылях и убытках
МСФО (малый вход inputs/stage1/ifrs/ifrs_quarterly.csv — лист этапа 1, `evidence/extract_inputs.py`) и делит строки:
  * устойчивые — финансовые расходы страхования, первоначальное признание и модификация, прочие операционные, ФИ по СС
    через ПСД, доля ассоциированных: идут каждый квартал, растут с бизнесом;
  * волатильные и разовые — ФИ по СС через ОПУ (переоценки; в т.ч. кредитная переоценка кредитов по СС — примечание о
    кредитах, константы ниже), производные и валюта, обесценение активов, прочие резервы.
Цены — якоря 3К26 (I = 1 на 30.09.2026, MODEL §4.7): квартал делится на индекс цен конца квартала к сентябрю 2026 года,
индекс — цепочкой годовых темпов ИПЦ по месяцам (таблица Банка России, константа CPI_YOY ниже; помесячный шаг —
(1 + темп г/г)^(1/12), сентябрь 2026 года — темп августа).
Правила книги 1.5 (сверка с шаблоном — `compare`, код выхода 1 при расхождении):
  * `other.misc_net_real.LT` — год из средних кварталов 2024К1–2026К3 в ценах якоря (1К–3К — по три года, 4К — по
    два): сумма четырёх средних, к шагу 10 млрд ₽; ось — центр ± 100. Простое среднее 11 кварталов × 4 занижало бы
    уровень: 4К, где сосредоточено «прочее», в окне два из одиннадцати;
  * `other.misc_quarter_shares` — доли тех же средних кварталов в их сумме: доля квартала с обратным знаком → 0,
    остальные нормируются, три знака, Σ = 1 (остаток округления — в крупнейшую);
  * `noncore.result_real.2026` — факт 9М26 + оценка 4К26: среднее 1К–3К26 × среднее отношение 4К к среднему 1К–3К
    того же года (2024–2025), к 0,1 млрд ₽ (год якоря закрывается остатком: 4К26 = ключ − факт 9М26, MODEL §0.3).
Налог — справка (ключ сверяет лист bridges): эффективная ставка по кварталам и с начала года; сверка 2025 года
(МСФО 2025, прим. 29); у промежуточной отчётности налог — по средневзвешенной ставке, ожидаемой за год (МСФО 9М26,
прим. 2): ставка 9М26 — оценка банка на 2026 год.
Запуск из корня репозитория: python -B data/assumptions/evidence/misc/misc_book.py → out/misc_book_out.json,
out/misc_quarterly.csv
"""
from __future__ import annotations

import csv
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S1 = os.path.join(HERE, "inputs", "stage1")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

QUARTERS = [f"{y}Q{k}" for y in (2024, 2025, 2026) for k in (1, 2, 3, 4) if (y, k) <= (2026, 3)]
ANCHOR_Q = "2026Q3"
# Строки «прочего» в отчёте о прибылях и убытках МСФО (метрики листа этапа 1) → группа и подпись
STABLE = {
    "ins_finance": "финансовые расходы по страхованию",
    "gl_initial_recognition": "первоначальное признание и модификация (АС)",
    "other_op_net": "прочие операционные",
    "gl_fvoci": "ФИ по СС через ПСД",
    "assoc_share": "доля ассоциированных",
}
VOLATILE = {
    "gl_fvtpl_nonderiv": "непроизводные ФИ по СС через ОПУ",
    "gl_deriv_fx": "производные, валюта, драгметаллы",
    "impairment": "обесценение активов",
    "other_provisions": "прочие резервы",
    "office_revaluation": "переоценка офисной недвижимости (годовая строка)",
}
PNL_CORE = ("pbt", "nii", "llp_debt_fa", "fee_income", "fee_expense", "ins_revenue", "ins_service_expense",
            "noncore_revenue", "noncore_cost", "opex", "tax")
# Кредитная переоценка кредитов по СС («изменение кредитного качества»; внутри строки ФИ по СС через ОПУ): значения
# примечаний «Кредиты и авансы клиентам» нарастающим итогом и за три месяца (знак — доход «+»), млрд ₽.
# (период, значение, документ, PDF-страница, sha256 — первые 16 знаков)
FV_LOANS_CREDIT = [
    ("3M2024", -6.5, "research/primary/2025Q1_ifrs_interim_statements.pdf", 29, "1f25a2344d65ea90"),
    ("6M2024", 23.8, "primary/ifrs_6M2025_statements.pdf", 33, "8cf65023cc411f57"),
    ("9M2024", 10.8, "primary/ifrs_9M2025_statements.pdf", 34, "059ab2d344263b84"),
    ("12M2024", 35.5, "primary/ifrs_FY2024_statements.pdf", 41, "fbd4d739ce58c597"),
    ("3M2025", 13.7, "research/primary/2025Q1_ifrs_interim_statements.pdf", 29, "1f25a2344d65ea90"),
    ("6M2025", 28.7, "primary/ifrs_6M2025_statements.pdf", 33, "8cf65023cc411f57"),
    ("9M2025", 30.4, "primary/ifrs_9M2025_statements.pdf", 34, "059ab2d344263b84"),
    ("12M2025", 34.1, "primary/ifrs_FY2025_statements.pdf", 43, "eb4e9c7919500f3d"),
    ("3M2026", -1.5, "primary/ifrs_3M2026_statements.pdf", 27, "4b1449f839cf0826"),
    ("6M2026", 34.7, "research/primary/2026H1_ifrs_interim_statements.pdf", 32, "5b3a3de88ae6c96b"),
    ("9M2026", 22.0, "research/primary/2026Q3_ifrs_interim_statements.pdf", 37, "bfb59dab3fe2cea9"),
]
# За три месяца — раскрыто отдельно (сверка с разностью нарастающих итогов)
FV_LOANS_CREDIT_3M = {"2024Q2": 30.3, "2025Q2": 15.0, "2025Q3": 1.7, "2026Q2": 36.2, "2026Q3": -12.7}
# Строки «прочего», раскрытые только за год (в поквартальном листе их нет): относятся к 4К как разность «год − 9М».
# (квартал, строка, значение, документ, PDF-страница, sha256 — первые 16 знаков)
ANNUAL_ONLY = [
    ("2024Q4", "assoc_share", 7.5, "primary/ifrs_FY2025_statements_sber.pdf", 6, "06ac4bd4164d576e"),   # доля ассоциированных за 2024 год
    ("2025Q4", "office_revaluation", 2.5, "primary/ifrs_FY2025_statements_sber.pdf", 6, "06ac4bd4164d576e"),   # переоценка офисной недвижимости 2025
]
# Годовой темп ИПЦ, % (месяц: г/г) — «Инфляция и ключевая ставка Банка России», primary/regimes_cbr_hd_base_infl_2013_2026.html
# (sha256 f897f642ea657f71); сентября 2026 года в таблице нет — берётся август.
CPI_YOY = {
    "2024-01": 7.44, "2024-02": 7.69, "2024-03": 7.72, "2024-04": 7.84, "2024-05": 8.30, "2024-06": 8.59,
    "2024-07": 9.13, "2024-08": 9.05, "2024-09": 8.63, "2024-10": 8.54, "2024-11": 8.88, "2024-12": 9.52,
    "2025-01": 9.92, "2025-02": 10.06, "2025-03": 10.34, "2025-04": 10.23, "2025-05": 9.88, "2025-06": 9.40,
    "2025-07": 8.79, "2025-08": 8.14, "2025-09": 7.98, "2025-10": 7.71, "2025-11": 6.64, "2025-12": 5.59,
    "2026-01": 6.00, "2026-02": 5.91, "2026-03": 5.86, "2026-04": 5.58, "2026-05": 5.31, "2026-06": 6.02,
    "2026-07": 5.98, "2026-08": 6.33,
}
# Налог на прибыль 2025 года: сверка теоретического налога с фактическим (МСФО 2025, прим. 29; PDF-с. 86, sha256
# eb4e9c7919500f3d), млрд ₽
TAX_2025 = {"pbt": 2250.0, "theoretical_25pct": 562.5, "securities_other_rates": -55.9, "unrecognised_tax_assets": 35.1,
            "other_permanent": 2.4, "tax": 544.1}
STEP_LEVEL = 10.0                 # шаг уровня «прочего», млрд ₽
AXIS_HALF = 100.0                 # половина оси уровня, млрд ₽ (ширина книг 1.0–1.4 — ±100 в ценах 2026)
R = lambda x, n=1: None if x is None else round(x, n)  # noqa: E731


def label(q: str) -> str:
    """2026Q3 → 3Q2026 (метка листа этапа 1)."""
    return f"{q[5]}Q{q[:4]}"


def load() -> dict:
    out: dict = {}
    with open(os.path.join(S1, "ifrs", "ifrs_quarterly.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["value"] != "":
                out.setdefault(r["metric"], {})[r["period"]] = float(r["value"])
    return out


def deflators() -> dict[str, float]:
    """Индекс цен конца квартала → цены якоря (конец сентября 2026 года): P(2026-09) / P(конец квартала)."""
    months = sorted(CPI_YOY) + ["2026-09"]
    yoy = dict(CPI_YOY, **{"2026-09": CPI_YOY["2026-08"]})
    level, p = {}, 1.0
    for m in reversed(months):                       # P(m) относительно сентября 2026 года
        level[m] = p
        p /= (1 + yoy[m] / 100) ** (1 / 12)
    out = {}
    for q in QUARTERS:
        end = f"{q[:4]}-{3 * int(q[5]):02d}"
        out[q] = 1.0 / level[end]
    return out


def fv_loans_quarterly() -> dict:
    """Кредитная переоценка кредитов по СС по кварталам из нарастающих итогов; сверка с раскрытием за три месяца."""
    ytd = {p: v for p, v, *_ in FV_LOANS_CREDIT}
    q: dict[str, float] = {}
    for y in (2024, 2025, 2026):
        prev = 0.0
        for k in (1, 2, 3, 4):
            v = ytd.get(f"{3 * k}M{y}")
            if v is None:
                continue
            q[f"{y}Q{k}"] = round(v - prev, 1)
            prev = v
    bad = [p for p, v in FV_LOANS_CREDIT_3M.items() if abs(q[p] - v) > 0.05]
    return {"quarterly": q, "check_3m_disclosed": "ок" if not bad else f"расходятся: {bad}",
            "by_year": {"2024": ytd["12M2024"], "2025": ytd["12M2025"], "9M2026": ytd["9M2026"]},
            "sources": [{"period": p, "value": v, "doc": d, "pdf_page": pg, "sha256_16": h}
                        for p, v, d, pg, h in FV_LOANS_CREDIT]}


def quarterly(m: dict, fvl: dict, dfl: dict) -> list[dict]:
    rows = []
    for q in QUARTERS:
        p = label(q)
        g = lambda k: m.get(k, {}).get(p)  # noqa: E731
        core = {k: g(k) for k in PNL_CORE}
        fees = core["fee_income"] + core["fee_expense"]
        ins = core["ins_revenue"] + core["ins_service_expense"]
        nc = core["noncore_revenue"] + core["noncore_cost"]
        misc = core["pbt"] - (core["nii"] + core["llp_debt_fa"] + fees + ins + nc + core["opex"])
        comp = {k: g(k) for k in (*STABLE, *VOLATILE)}
        for qq, k, v, *_ in ANNUAL_ONLY:
            if qq == q:
                comp[k] = (comp[k] or 0.0) + v
        known = sum(v for v in comp.values() if v is not None)
        rows.append({
            "quarter": q, "pbt": core["pbt"], "tax": core["tax"], "tax_rate": round(-core["tax"] / core["pbt"], 4),
            "noncore": R(nc), "insurance_net": R(ins), "misc": R(misc), **{k: comp[k] for k in comp},
            "fv_loans_credit": fvl["quarterly"].get(q), "unexplained": R(misc - known),
            "stable": R(sum(comp[k] or 0.0 for k in STABLE)), "volatile": R(sum(comp[k] or 0.0 for k in VOLATILE)),
            "deflator": round(dfl[q], 5), "misc_real": R(misc * dfl[q], 2),
        })
    return rows


def _sum(rows: list[dict], key: str, qs: list[str], real: bool = False) -> float:
    by = {r["quarter"]: r for r in rows}
    return sum((by[q][key] or 0.0) * (by[q]["deflator"] if real else 1.0) for q in qs)


def years(rows: list[dict]) -> dict:
    groups = {"2024": [q for q in QUARTERS if q.startswith("2024")], "2025": [q for q in QUARTERS if q.startswith("2025")],
              "9M2026": [q for q in QUARTERS if q.startswith("2026")]}
    keys = ("misc", *STABLE, *VOLATILE, "stable", "volatile", "fv_loans_credit", "noncore", "insurance_net", "pbt", "tax")
    out = {}
    for lab, qs in groups.items():
        out[lab] = {k: R(_sum(rows, k, qs)) for k in keys}
        out[lab]["misc_real"] = R(_sum(rows, "misc", qs, real=True))
        out[lab]["tax_rate"] = round(out[lab]["tax"] / -out[lab]["pbt"], 4)
    out["9M2025"] = {k: R(_sum(rows, k, ["2025Q1", "2025Q2", "2025Q3"])) for k in keys}
    return out


def balanced(rows: list[dict], key: str) -> float:
    """Год из средних кварталов: Σ по кварталам года (1К…4К) среднего этого квартала в ценах якоря за 2024–2026."""
    by = {r["quarter"]: r for r in rows}
    return sum(st.mean((by[q][key] or 0.0) * by[q]["deflator"] for q in QUARTERS if q.endswith(f"Q{k}"))
               for k in (1, 2, 3, 4))


def level(rows: list[dict]) -> dict:
    """Уровень «прочего» в ценах якоря, млрд ₽ в год: правило книги и сверочные оценки."""
    n = len(QUARTERS)
    year = balanced(rows, "misc")
    mean_all = _sum(rows, "misc", QUARTERS, real=True) / n * 4
    stable_last4 = _sum(rows, "stable", QUARTERS[-4:], real=True)
    full_years = [q for q in QUARTERS if q < "2026Q1"]
    mean_2024_2025 = _sum(rows, "misc", full_years, real=True) / 2
    rule = round(year / STEP_LEVEL) * STEP_LEVEL
    return {"rule": "сумма средних кварталов 2024К1–2026К3 в ценах якоря (1К–3К — три года, 4К — два), к шагу 10 млрд ₽",
            "quarters": n, "balanced_year": R(year), "center": rule,
            "axis": {"low": rule - AXIS_HALF, "high": rule + AXIS_HALF},
            "by_group": {"stable": R(balanced(rows, "stable")), "volatile": R(balanced(rows, "volatile")),
                         "fv_loans_credit": R(balanced(rows, "fv_loans_credit")),
                         **{k: R(balanced(rows, k)) for k in (*STABLE, *VOLATILE)}},
            "check": {"mean_quarter_x4": R(mean_all), "stable_last_4q": R(stable_last4),
                      "stable_last_4q_plus_volatile": R(stable_last4 + balanced(rows, "volatile"))},
            "book_1_4_basis_2024_2025_mean": R(mean_2024_2025),
            "annual_real": {"2024": R(_sum(rows, "misc", QUARTERS[:4], real=True)),
                            "2025": R(_sum(rows, "misc", QUARTERS[4:8], real=True)),
                            "9M2026": R(_sum(rows, "misc", QUARTERS[8:], real=True))}}


def profile(rows: list[dict]) -> dict:
    """Доли кварталов в годовой сумме: средние кварталы 2024К1–2026К3 в ценах якоря (правило A-O3 книги 1.1 — на
    всей истории с раскрытыми строками)."""
    by = {r["quarter"]: r for r in rows}
    means = {}
    for k in (1, 2, 3, 4):
        vals = [by[q]["misc_real"] for q in QUARTERS if q.endswith(f"Q{k}")]
        means[str(k)] = {"mean": round(st.mean(vals), 2), "n": len(vals), "values": vals}
    total = sum(v["mean"] for v in means.values())
    raw = {k: v["mean"] / total for k, v in means.items()}
    kept = {k: (v if v > 0 else 0.0) for k, v in raw.items()}
    norm = sum(kept.values())
    shares = {k: round(v / norm, 3) for k, v in kept.items()}
    top = max(shares, key=shares.get)
    shares[top] = round(1.0 - sum(v for k, v in shares.items() if k != top), 3)
    return {"rule": "средние кварталы 2024К1–2026К3 в ценах якоря; квартал с обратным знаком → 0; Σ = 1",
            "by_quarter": means, "total_mean_year": round(total, 1), "shares_raw": {k: round(v, 4) for k, v in raw.items()},
            "shares": shares}


def q4_check(rows: list[dict], lvl: dict, shares: dict) -> dict:
    """4К26: правило книги (уровень × доля 4К; индекс цен 4К26 — у ядра) против оценки по строкам — средний 4К 2024–2025
    каждой строки в ценах якоря."""
    by = {r["quarter"]: r for r in rows}
    per = {}
    for k in (*STABLE, *VOLATILE):
        vals = [(by[q][k] or 0.0) * by[q]["deflator"] for q in ("2024Q4", "2025Q4")]
        per[k] = round(st.mean(vals), 1)
    q4 = [by[q]["misc_real"] for q in ("2024Q4", "2025Q4")]
    return {"rule_level_x_share": R(lvl["center"] * shares["4"]), "by_lines_mean_q4": R(sum(per.values())),
            "by_lines": per, "q4_history_real": q4}


def noncore(rows: list[dict]) -> dict:
    by = {r["quarter"]: r["noncore"] for r in rows}
    ratios = {}
    for y in (2024, 2025):
        q13 = st.mean(by[f"{y}Q{k}"] for k in (1, 2, 3))
        ratios[str(y)] = round(by[f"{y}Q4"] / q13, 3)
    q13_26 = st.mean(by[f"2026Q{k}"] for k in (1, 2, 3))
    q4_est = q13_26 * st.mean(ratios.values())
    fact_9m = sum(by[f"2026Q{k}"] for k in (1, 2, 3))
    return {"quarterly": by, "q4_to_q1q3_mean": ratios, "q1q3_2026_mean": round(q13_26, 2),
            "q4_2026_estimate": round(q4_est, 1), "fact_9m2026": round(fact_9m, 1),
            "rule_2026": round(fact_9m + q4_est, 1),
            "rule": "факт 9М26 + оценка 4К26 (среднее 1К–3К26 × среднее отношение 4К к среднему 1К–3К 2024–2025)"}


def tax(rows: list[dict], yrs: dict) -> dict:
    t = TAX_2025
    return {"quarterly": {r["quarter"]: r["tax_rate"] for r in rows},
            "2025": yrs["2025"]["tax_rate"], "9M2026": yrs["9M2026"]["tax_rate"], "9M2025": round(yrs["9M2025"]["tax"] / -yrs["9M2025"]["pbt"], 4),
            "reconciliation_2025_pp": {k: round(100 * v / t["pbt"], 2) for k, v in t.items() if k not in ("pbt", "theoretical_25pct", "tax")},
            "reconciliation_2025": t,
            "interim_rule": "МСФО 9М26, прим. 2 (PDF-с. 13): налог промежуточной отчётности — по средневзвешенной годовой "
                            "ставке, ожидаемой за полный год, — ставка 9М26 есть оценка банка на 2026 год"}


def compute() -> dict:
    m = load()
    dfl = deflators()
    fvl = fv_loans_quarterly()
    rows = quarterly(m, fvl, dfl)
    yrs = years(rows)
    lvl = level(rows)
    prof = profile(rows)
    return {"anchor": ANCHOR_Q, "quarters": QUARTERS, "groups": {"stable": STABLE, "volatile": VOLATILE},
            "years": yrs, "level": lvl, "profile": prof, "q4_2026": q4_check(rows, lvl, prof["shares"]),
            "fv_loans_credit": fvl, "noncore": noncore(rows), "tax": tax(rows, yrs),
            "unexplained_max_abs": max(abs(r["unexplained"]) for r in rows), "rows": rows}


def compare(res: dict, book: dict) -> list[str]:
    """Ключи книги против правил листа (пусто — книга воспроизводится)."""
    bad = []
    o, nc = book["other"], book["noncore"]
    lvl = res["level"]
    if abs(o["misc_net_real"].get("LT", 0.0) - lvl["center"]) > 1e-9 or set(o["misc_net_real"]) != {"LT"}:
        bad.append(f"other.misc_net_real: книга {o['misc_net_real']} против правила {{LT: {lvl['center']}}}")
    ax = next((a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["other.misc_net_real.LT"]), None)
    if ax is None or [ax["low"], ax["high"]] != [lvl["axis"]["low"], lvl["axis"]["high"]]:
        bad.append(f"ось other.misc_net_real.LT: книга {ax and [ax['low'], ax['high']]} против "
                   f"{[lvl['axis']['low'], lvl['axis']['high']]}")
    shares = {str(k): v for k, v in o["misc_quarter_shares"].items()}
    if shares != res["profile"]["shares"]:
        bad.append(f"other.misc_quarter_shares: книга {shares} против правила {res['profile']['shares']}")
    if abs(nc["result_real"]["2026"] - res["noncore"]["rule_2026"]) > 1e-9:
        bad.append(f"noncore.result_real.2026: книга {nc['result_real']['2026']} против правила {res['noncore']['rule_2026']}")
    if res["unexplained_max_abs"] > 0.15:
        bad.append(f"строки «прочего» не сходятся с остатком: {res['unexplained_max_abs']}")
    return bad


def write_table(rows: list[dict]) -> None:
    cols = list(rows[0])
    with open(os.path.join(OUT, "misc_quarterly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    res["book_mismatches"] = compare(res, evlib.template())
    os.makedirs(OUT, exist_ok=True)
    write_table(res["rows"])
    evlib.write_json(evlib.Path(OUT) / "misc_book_out.json", {k: v for k, v in res.items() if k != "rows"})
    y, lvl, prof, q4 = res["years"], res["level"], res["profile"], res["q4_2026"]
    print("«прочее», номинал:", {k: y[k]["misc"] for k in ("2024", "2025", "9M2026")},
          "в ценах якоря:", lvl["annual_real"])
    print("устойчивые / волатильные:", {k: (y[k]["stable"], y[k]["volatile"]) for k in ("2024", "2025", "9M2026")})
    print("уровень:", lvl["balanced_year"], "→", lvl["center"], "ось", lvl["axis"], "по группам", lvl["by_group"],
          "сверка", lvl["check"])
    print("доли кварталов:", prof["shares"], "(средние", {k: v["mean"] for k, v in prof["by_quarter"].items()}, ")")
    print("4К26: правило", q4["rule_level_x_share"], "по строкам", q4["by_lines_mean_q4"])
    print("кредитная переоценка кредитов по СС:", res["fv_loans_credit"]["quarterly"], res["fv_loans_credit"]["check_3m_disclosed"])
    print("непрофильный:", res["noncore"]["rule_2026"], "(4К26", res["noncore"]["q4_2026_estimate"], ")")
    print("налог:", {k: res["tax"][k] for k in ("2025", "9M2025", "9M2026")}, res["tax"]["reconciliation_2025_pp"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
