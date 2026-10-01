"""Калибровка книг ЧПД и передачи ставки Сбера — лист этапа 1 (calib-nii), перенесённый в книгу 1.0.

Запуск из корня репозитория: python -B data/assumptions/evidence/nii/calib_nii.py   (≈8 с, без сети)
Входы — inputs/ (малые входы, evidence/extract_inputs.py; КБД ОФЗ — inputs/stage1/calib/nii/data/ofz_quarterly.csv,
сырьё MOEX ISS — ссылкой и sha256 в zcyc_index.csv). Выход: out/*.csv, out/results.json, out/RESULTS.md.
Перенос меняет только корни путей; числа — те же, что у листа этапа 1 (README области).

Метод (MODEL.md §4.4–4.5): книга b — частичная подстройка
    y_t = y_{t-1} + ρ_b·(a_b·ref_t + s_b − y_{t-1})      (актив; движок: a_b = 1 + сжатие φ между мирами)
    c_t = c_{t-1} + ρ_b·(β_b·key_t + s_b − c_{t-1})      (пассив)
оценка — взвешенный МНК по СВОБОДНОЙ траектории 2018К4–2026К2 (как её считает движок, без
подстановки факта прошлого квартала); при данном ρ путь линеен по (y0, a|β, s) — решение
нормальными уравнениями, ρ — сетка 0,02…1,00. Годовые наблюдения (МСФО 2019–2022, упр. 2021–2022) —
средневзвешенное квартальных значений модели с весами «средний остаток × дни».
Ставки в процентах годовых; в книгу — доли (/100).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os

import nii_lib as L

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "out")
os.makedirs(DATA, exist_ok=True)
QF = L.q_range("2018Q4", "2026Q2")          # t = 0 … 30; t = 0 — начальное состояние
TIX = {q: i for i, q in enumerate(QF)}
Q_IFRS = L.q_range("2023Q1", "2026Q2")
ANCHOR_Q = "2026Q2"
OOS_LAST = "2024Q4"                        # проверка вне выборки: оценка по 2024К4, прогноз 2025К1–2026К2
SUB_H = {2019: 0.05, 2020: 0.30, 2021: 0.28, 2022: 0.48, 2023: 0.62, 2024: 0.73, 2025: 0.82, 2026: 0.68}
SUB_H_2026Q2_SBER = 0.67                   # Сбер 2К26: доля рыночных программ в выдачах 33 % [Ф: IR-страница 2К26, research/facts]
SUB_RUNOFF_Q = 0.032                       # погашения ИЖК сектора ≈3,1–3,4 % портфеля за квартал [Р: ЦБ «Банковский сектор II кв. 2026», с. 9]
SUB_M_GRID = [1.0 + 0.25 * i for i in range(17)]   # компенсация льготной ипотеки над ключевой, п.п. (профиль)
SUB_M = 2.5          # компенсация, п.п. над ключевой: с 2026 — 2,0 (МКД) / 2,5 (ИЖС), в 2025 — 2,5–3,5
                     # [НП, вторичный: rg.ru 31.12.2025]; история её не различает (профиль RMSE плоский)
TAX_EFF = 0.243                            # эффективная ставка налога (DESIGN §3.5 п.4; книга tax.* — 25 % − 0,7 п.п.)
R = lambda x, n=2: None if x is None else round(x, n)  # noqa: E731


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ====================================================================== данные
def load():
    key = L.key_quarterly()
    ofz = L.ofz_quarterly()
    mg = L.mgmt_series()
    fq = L.ifrs_flows_quarterly()
    fa = L.ifrs_flows_annual()
    qb = L.quarter_balances("2018Q4", "2026Q2")
    refs = {"key": [key[q]["avg"] for q in QF]}
    for t in (1, 3, 5):
        refs[f"ofz_{t}y"] = [ofz[q][f"ofz_{t}y"] for q in QF]
    return dict(key=key, ofz=ofz, mg=mg, fq=fq, fa=fa, qb=qb, refs=refs)


def avg_bal(D, q, cols):
    p = L.q_prev(q)
    return sum((D["qb"][p][c] + D["qb"][q][c]) / 2 for c in cols)


BOOK_COLS = {
    "loans": ["corp_loans", "retail_loans_all"], "corp_loans": ["corp_loans"], "retail_loans_all": ["retail_loans_all"],
    "securities": ["securities"], "liquidity": ["liquidity"], "retail_current": ["retail_current"],
    "retail_term": ["retail_term"], "corp_funds": ["corp_funds"], "wholesale": ["wholesale"],
    "dia": ["retail_funds"], "iea": ["iea"], "ibl": ["ibl"], "other_ii": ["iea"], "other_ie": ["ibl"],
}


def ifrs_rate_q(D, book, q):
    return D["fq"][q][book] / avg_bal(D, q, BOOK_COLS[book]) * 365 / L.q_days(q) * 100


def ifrs_obs(D, book, *, include_2022=True, last=None):
    """Наблюдения ставки книги в базисе МСФО: годы 2019–2022 (вес 4) и кварталы 2023К1–2026К2 (вес 1)."""
    obs = []
    for y in range(2019, 2023):
        if y == 2022 and not include_2022:
            continue
        qs = L.q_range(f"{y}Q1", f"{y}Q4")
        wmap = {TIX[q]: avg_bal(D, q, BOOK_COLS[book]) * L.q_days(q) for q in qs}
        den = sum(wmap.values()) / 365
        obs.append((wmap, D["fa"][f"FY{y}"][book] / den * 100, 4.0, f"FY{y}"))
    for q in Q_IFRS:
        if last and TIX[q] > TIX[last]:
            continue
        obs.append(({TIX[q]: 1.0}, ifrs_rate_q(D, book, q), 1.0, q))
    return obs


def mgmt_obs(D, metric, book_cols, *, last=None):
    """Упр. доходность кредитов: кварталы, где раскрыта, + годовые 2021 (вес 1: нет 4К) и 2022 (вес 3: нет 1–3К)."""
    s = D["mg"][metric]
    obs = []
    for q in QF:
        if q in s and not (last and TIX[q] > TIX[last]):
            obs.append(({TIX[q]: 1.0}, s[q], 1.0, q))
    for y, w in ((2021, 1.0), (2022, 3.0)):
        if f"{y}FY" in s:
            qs = L.q_range(f"{y}Q1", f"{y}Q4")
            obs.append(({TIX[q]: avg_bal(D, q, book_cols) * L.q_days(q) for q in qs}, s[f"{y}FY"], w, f"{y}FY"))
    return obs


A_BOUNDS = {"asset": (0.0, 1.2), "liab": (0.0, 1.05)}   # экономические границы наклона a / β


def fit(D, ref, obs, kind="asset", **kw):
    if kw.get("a_fixed") is None:
        kw.setdefault("a_bounds", A_BOUNDS[kind])
    f = L.fit_partial_adjustment(D["refs"][ref], [(o[0], o[1], o[2]) for o in obs], **kw)
    f["ref"] = ref
    f["path"] = L.simulate(D["refs"][ref], f["rho"], f["a"], f["s"], f["y0"], f.get("rho_down"))
    f["resid"] = [(o[3], o[1], sum(f["path"][t] * w for t, w in o[0].items()) / sum(o[0].values())) for o in obs]
    # профиль по ρ: 95 % интервал по ΔSSE (F-приближение)
    prof = []
    for i in range(2, 101) if not kw.get("asym") else ():
        try:
            g = L.fit_partial_adjustment(D["refs"][ref], [(o[0], o[1], o[2]) for o in obs], rho_fixed=i / 100,
                                         **{k: v for k, v in kw.items() if k != "rho_fixed"})
            prof.append((i / 100, g["rmse_w"] ** 2))
        except RuntimeError:
            pass
    if "rho_fixed" not in kw and prof:
        m = min(p[1] for p in prof)
        lim = m * (1 + 3.84 / max(1, f["dof"]))
        ok = [p[0] for p in prof if p[1] <= lim]
        f["rho_ci"] = (min(ok), max(ok))
    else:
        f["rho_ci"] = None
    return f


# ====================================================================== льготная ипотека (доля в рознице)
def subsidized_share(D, runoff_sub=SUB_RUNOFF_Q, runoff_mkt=SUB_RUNOFF_Q):
    """Накопление поколений: выдачи ≈ Δпортфель + погашения; доля льготных выдач — ЦБ (сектор, по годам),
    2К26 — Сбер. Возвращает {квартал: (остаток льготной, доля в ипотеке, доля в рознице)}."""
    S, out = 0.0, {"2018Q4": (0.0, 0.0, 0.0)}
    for q in QF[1:]:
        y, k = L.q_parse(q)
        M, Mp = D["qb"][q]["mortgage_all"], D["qb"][L.q_prev(q)]["mortgage_all"]
        h = SUB_H_2026Q2_SBER if q == "2026Q2" else SUB_H[y]
        iss = M - Mp + runoff_sub * S + runoff_mkt * (Mp - S)
        S = S * (1 - runoff_sub) + h * iss
        out[q] = (S, S / M, S / D["qb"][q]["retail_loans_all"])
    return out


def retail_obs_market(D, obs, wsub, m):
    """Пересчёт наблюдений доходности всей розницы в доходность рыночной части при компенсации m."""
    key = D["refs"]["key"]
    new = []
    for wmap, yv, w, lab in obs:
        tot = sum(wmap.values())
        num = yv * tot - sum(wt * wsub[t] * (key[t] + m) for t, wt in wmap.items())
        wm = {t: wt * (1 - wsub[t]) for t, wt in wmap.items()}
        den = sum(wm.values())
        new.append((wm, num / den, w * (den / tot) ** 2, lab))
    return new


# ====================================================================== книги
ASSET_REFS = ["key", "ofz_1y", "ofz_3y", "ofz_5y"]


def fit_books(D, *, last=None, include_2022=True, asym=False):
    res = {}
    # --- корпоративные кредиты (упр. доходность ЮЛ, базис mgmt ≈ МСФО: см. сверку loans_basis)
    ob = mgmt_obs(D, "corp_loan_yield", ["corp_loans"], last=last)
    cand = {r: fit(D, r, ob) for r in ASSET_REFS}
    best = min(cand, key=lambda r: cand[r]["rmse_w"])
    res["corp_loans"] = {"free": cand[best], "a1": fit(D, best, ob, a_fixed=1.0), "cand": cand, "obs": ob}
    # --- розница: льготная ипотека (ключевая + m) + рыночная часть с лагом
    wsub_q = subsidized_share(D)
    wsub = [wsub_q[q][2] for q in QF]
    ob_all = mgmt_obs(D, "retail_loan_yield", ["retail_loans_all"], last=last)
    grid = []
    for m in SUB_M_GRID:
        obm = retail_obs_market(D, ob_all, wsub, m)
        for r in ASSET_REFS:
            f = fit(D, r, obm)
            grid.append((f["rmse_w"], m, r, f))
    grid.sort(key=lambda g: g[0])
    m_best = grid[0][1]
    obm = retail_obs_market(D, ob_all, wsub, SUB_M)
    cand_m = {r: fit(D, r, obm) for r in ASSET_REFS}
    r_best = min(cand_m, key=lambda r: cand_m[r]["rmse_w"])
    f_best = cand_m[r_best]
    res["retail_market"] = {"free": f_best, "a1": fit(D, r_best, obm, a_fixed=1.0), "m": SUB_M, "m_rmse_min": m_best,
                            "m_profile": sorted({(g[1], g[2]): g[0] for g in grid}.items()),
                            "cand": cand_m, "obs": obm, "obs_all": ob_all, "wsub": wsub}
    # фиксированные m (чувствительность)
    res["retail_market"]["m_fixed"] = {}
    for m in (2.0, 2.5, 3.0, 3.5):
        obx = retail_obs_market(D, ob_all, wsub, m)
        res["retail_market"]["m_fixed"][m] = fit(D, r_best, obx)
    # --- бумаги
    ob = ifrs_obs(D, "securities", include_2022=include_2022, last=last)
    cand = {r: fit(D, r, ob) for r in ASSET_REFS}
    best = min(cand, key=lambda r: cand[r]["rmse_w"])
    res["securities"] = {"free": cand[best], "a1": fit(D, best, ob, a_fixed=1.0), "cand": cand, "obs": ob}
    # --- ликвидность (балансирующая: ρ = 1)
    ob = ifrs_obs(D, "liquidity", include_2022=include_2022, last=last)
    res["liquidity"] = {"free": fit(D, "key", ob, rho_fixed=1.0), "a1": fit(D, "key", ob, rho_fixed=1.0, a_fixed=1.0),
                        "rho_free": fit(D, "key", ob), "obs": ob}
    # --- пассивы
    for b in ("retail_current", "retail_term", "corp_funds"):
        ob = ifrs_obs(D, b, include_2022=include_2022, last=last)
        res[b] = {"free": fit(D, "key", ob, kind="liab"), "obs": ob,
                  "cand": {r: fit(D, r, ob, kind="liab") for r in ("key", "ofz_1y")}}
    ob = ifrs_obs(D, "wholesale", include_2022=include_2022, last=last)
    res["wholesale"] = {"free": fit(D, "key", ob, kind="liab", rho_fixed=1.0, a_fixed=1.0),
                        "diag": fit(D, "key", ob, kind="liab"), "obs": ob}
    if asym:   # асимметричная подстройка (ρ при росте опоры / при снижении) — диагностика
        for b in ("corp_loans", "retail_market", "securities"):
            res[b]["asym"] = fit(D, res[b]["free"]["ref"], res[b]["obs"], asym=True)
        for b in ("retail_current", "retail_term", "corp_funds"):
            res[b]["asym"] = fit(D, "key", res[b]["obs"], kind="liab", asym=True)
    return res


# ====================================================================== доля текущих счетов ФЛ
def fit_current_share(D, q0="2022Q4"):
    qs = L.q_range(q0, ANCHOR_Q)
    keyv = [D["key"][q]["avg"] for q in qs]
    obs = [({i: 1.0}, D["qb"][q]["retail_current"] / D["qb"][q]["retail_funds"] * 100, 1.0) for i, q in enumerate(qs)]
    f = L.fit_partial_adjustment(keyv, obs)
    f["path"] = L.simulate(keyv, f["rho"], f["a"], f["s"], f["y0"])
    f["obs"] = [(q, o[1]) for q, o in zip(qs, obs)]
    f["psi"] = -f["a"]                       # c* = s + a·key  ⇒  ψ = −a (доля на 1 п.п. ключевой)
    # до 2022К4 — рост доли без связи со ставкой (структурный сдвиг: накопительные счета, цифровые каналы)
    f["history"] = [(p, D["qb"][p]["retail_current"] / D["qb"][p]["retail_funds"] * 100, D["key"][p]["eop"])
                    for p in ("2018Q4", "2019Q4", "2020Q4", "2021Q4", "2022Q4")]
    return f


# ====================================================================== история: ЧПМ, доходность активов, стоимость фондирования
def const_rates(D):
    """Постоянные доли, которыми в модели идут АСВ и «прочие» процентные строки (среднее 2023К1–2026К2)."""
    dia = [D["fq"][q]["dia"] / avg_bal(D, q, ["retail_funds"]) * 365 / L.q_days(q) * 100 for q in Q_IFRS]
    oii = [D["fq"][q]["other_ii"] / avg_bal(D, q, ["iea"]) * 365 / L.q_days(q) * 100 for q in Q_IFRS]
    oie = [D["fq"][q]["other_ie"] / avg_bal(D, q, ["ibl"]) * 365 / L.q_days(q) * 100 for q in Q_IFRS]
    return {"dia": sum(dia) / len(dia), "other_ii": sum(oii) / len(oii), "other_ie": sum(oie) / len(oie)}


def model_paths(D, fb, variant="free"):
    """Пути ставок книг (% годовых) на 2018К4–2026К2 по оценкам fb (variant: free | a1 | asym)."""
    key = D["refs"]["key"]
    pick = lambda b: fb[b].get(variant) or fb[b]["free"]  # noqa: E731
    sim = lambda f: L.simulate(D["refs"][f["ref"]], f["rho"], f["a"], f["s"], f["y0"], f.get("rho_down"))  # noqa: E731
    P = {b: sim(pick(b)) for b in ("corp_loans", "securities", "liquidity")}
    rm = fb["retail_market"]
    ymkt = sim(pick("retail_market"))
    P["retail_loans_all"] = [w * (key[t] + rm["m"]) + (1 - w) * ymkt[t] for t, w in enumerate(rm["wsub"])]
    P["retail_market"] = ymkt
    P["mortgage_sub"] = [k + rm["m"] for k in key]
    for b in ("retail_current", "retail_term", "corp_funds", "wholesale"):
        P[b] = sim(pick(b) if variant == "asym" else fb[b]["free"])
    return P


def history(D, fb, cr, variant="free"):
    P = model_paths(D, fb, variant)
    rows = []
    for q in QF[1:]:
        t, d = TIX[q], L.q_days(q)
        ab = {b: avg_bal(D, q, BOOK_COLS[b]) for b in BOOK_COLS}
        ii = sum(P[b][t] * ab[b] for b in ("corp_loans", "retail_loans_all", "securities", "liquidity")) / 100 \
            + cr["other_ii"] * ab["iea"] / 100
        ie = sum(P[b][t] * ab[b] for b in ("retail_current", "retail_term", "corp_funds", "wholesale")) / 100 \
            + cr["other_ie"] * ab["ibl"] / 100 + cr["dia"] * ab["dia"] / 100
        mod = {"nim": (ii - ie) / ab["iea"] * 100, "ay": ii / ab["iea"] * 100, "cof": ie / ab["ibl"] * 100}
        fact = {}
        if q in D["fq"]:
            f = D["fq"][q]
            ii_f = f["loans"] + f["securities"] + f["liquidity"] + f["other_ii"]
            ie_f = f["retail_current"] + f["retail_term"] + f["corp_funds"] + f["wholesale"] + f["other_ie"] + f["dia"]
            k = 365 / d
            fact = {"nim": f["nii_reported"] * k / ab["iea"] * 100, "ay": ii_f * k / ab["iea"] * 100,
                    "cof": ie_f * k / ab["ibl"] * 100}
        mg = {m: D["mg"][src].get(q) for m, src in (("nim", "nim"), ("ay", "asset_yield"), ("cof", "cost_of_funding"))}
        rows.append({"q": q, "key": D["refs"]["key"][t], "model": mod, "fact_eng": fact, "fact_mgmt": mg,
                     "interp": D["qb"][q]["_interp"], "ii": ii, "ie": ie, "iea": ab["iea"], "ibl": ab["ibl"], "d": d})
    # годовые факты движка 2019–2022 (МСФО) против годовой модели
    ann = []
    for y in range(2019, 2023):
        rs = [r for r in rows if r["q"].startswith(str(y))]
        f = D["fa"][f"FY{y}"]
        iea = sum(r["iea"] * r["d"] for r in rs) / 365
        ibl = sum(r["ibl"] * r["d"] for r in rs) / 365
        ii_m = sum(r["ii"] * r["d"] for r in rs) / 365
        ie_m = sum(r["ie"] * r["d"] for r in rs) / 365
        ii_f = f["loans"] + f["securities"] + f["liquidity"] + f["other_ii"]
        ie_f = f["retail_current"] + f["retail_term"] + f["corp_funds"] + f["wholesale"] + f["other_ie"] + f["dia"]
        mgk = D["mg"]
        ann.append({"y": y, "model": {"nim": (ii_m - ie_m) / iea * 100, "ay": ii_m / iea * 100, "cof": ie_m / ibl * 100},
                    "fact_eng": {"nim": f["nii_reported"] / iea * 100, "ay": ii_f / iea * 100, "cof": ie_f / ibl * 100},
                    "fact_mgmt": {"nim": mgk["nim"].get(f"{y}FY"), "ay": mgk["asset_yield"].get(f"{y}FY"),
                                  "cof": mgk["cost_of_funding"].get(f"{y}FY")}})
    # мост упр. − МСФО (аддитивный) по окну 1К24–2К26 (MODEL §4.6)
    win = [r for r in rows if "2024Q1" <= r["q"] <= "2026Q2"]
    bridge = {m: sum(r["fact_mgmt"][m] - r["fact_eng"][m] for r in win) / len(win) for m in ("nim", "ay", "cof")}
    return rows, ann, bridge, P


def err_stats(pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return None
    e = [m - f for f, m in pairs]
    return {"n": len(e), "me": sum(e) / len(e), "rmse": math.sqrt(sum(x * x for x in e) / len(e)),
            "maxabs": max(abs(x) for x in e)}


# ====================================================================== передача ставки
def annual_key(D):
    """Средняя ключевая за год (календарные дни). 2013: 5,5 % весь год — ключевая с 13.09.2013 = ставка
    недельного репо, 5,50 % с 14.09.2012 [Ф: cbr.ru/hd_base/repo_proc/, 30.09.2026]."""
    out = {2013: 5.5}
    for y in range(2014, 2026):
        qs = L.q_range(f"{y}Q1", f"{y}Q4")
        out[y] = sum(D["key"][q]["avg"] * L.q_days(q) for q in qs) / sum(L.q_days(q) for q in qs)
    return out


def eng_nim_annual(D):
    """ЧПМ в базисе движка по годам: ЧПД МСФО / средние процентные активы (остатки — квартальные,
    2019–2022 интерполированы между концами лет)."""
    out = {}
    for y in range(2019, 2026):
        qs = L.q_range(f"{y}Q1", f"{y}Q4")
        den = sum(avg_bal(D, q, ["iea"]) * L.q_days(q) for q in qs) / 365
        nii = D["fa"][f"FY{y}"]["nii_reported"] if y <= 2022 else sum(D["fq"][q]["nii_reported"] for q in qs)
        out[y] = nii / den * 100
    return out


def empirical(D):
    ky = annual_key(D)
    mg = D["mg"]
    nim = {y: mg["nim"].get(f"{y}FY") for y in range(2013, 2026)}
    roe = {y: mg["roe"].get(f"{y}FY") for y in range(2013, 2026)}
    base = [y for y in range(2013, 2026) if y not in (2014, 2015, 2022)]
    out = {"key": ky, "nim": nim, "roe": roe}
    for name, ser in (("nim", nim), ("roe", roe)):
        ys = [y for y in base if ser[y] is not None]
        r = L.ols([ky[y] for y in ys], [ser[y] for y in ys])
        r["years"] = ys
        r["shock_points"] = {y: {"key": ky[y], "fact": ser[y], "fit": r["a"] + r["b"] * ky[y],
                                 "resid": ser[y] - (r["a"] + r["b"] * ky[y])} for y in (2014, 2015) if ser[y] is not None}
        out[f"{name}_main"] = r
        ys2 = [y for y in range(2013, 2026) if y != 2022 and ser[y] is not None]
        r2 = L.ols([ky[y] for y in ys2], [ser[y] for y in ys2]); r2["years"] = ys2
        out[f"{name}_with_2014_15"] = r2
        ys3 = [y for y in base if ser[y] is not None and y >= 2016]
        r3 = L.ols([ky[y] for y in ys3], [ser[y] for y in ys3]); r3["years"] = ys3
        out[f"{name}_2016plus"] = r3
    # поправка методики ЧПМ 2019 (+0,26 п.п. к годам до 2018: 5,66 → 5,92 за 2018) — чувствительность
    adj = {y: (nim[y] + 0.26 if nim[y] is not None and y <= 2016 else nim[y]) for y in nim}
    ys = [y for y in base if adj[y] is not None]
    r = L.ols([ky[y] for y in ys], [adj[y] for y in ys]); r["years"] = ys
    out["nim_main_method_adj"] = r
    # базис движка (МСФО: ЧПД / средние процентные активы), годы 2019–2025 без 2022
    eng = eng_nim_annual(D)
    ys = [y for y in eng if y != 2022]
    r = L.ols([ky[y] for y in ys], [eng[y] for y in ys]); r["years"] = ys
    out["nim_engine_basis"] = r
    out["nim_engine_annual"] = eng
    # длинная разность: средние 2023–2025 против 2019–2021
    for nm, ser in (("nim_mgmt", nim), ("nim_engine", eng)):
        a_ = [2019, 2020, 2021]; b_ = [2023, 2024, 2025]
        dn = sum(ser[y] for y in b_) / 3 - sum(ser[y] for y in a_) / 3
        dk = sum(ky[y] for y in b_) / 3 - sum(ky[y] for y in a_) / 3
        out[f"long_diff_{nm}"] = {"d_nim": dn, "d_key": dk, "b": dn / dk}
    # квартальная 2019К1–2026К2 (упр. ЧПМ на среднюю ключевую квартала)
    qs = [q for q in QF[1:] if q in mg["nim"]]
    rq = L.ols([D["key"][q]["avg"] for q in qs], [mg["nim"][q] for q in qs]); rq["n_q"] = len(qs)
    out["nim_quarterly_2019_2026"] = rq
    return out


def anchor_structure(D, fb, cr, wsub_anchor):
    """Веса книг на якоре 30.06.2026 (к процентным активам, МСФО)."""
    b = D["qb"][ANCHOR_Q]
    iea = b["iea"]
    sub = wsub_anchor * b["retail_loans_all"]
    E = {"corp_loans": b["corp_loans"], "mortgage_sub": sub, "retail_loans": b["retail_loans_all"] - sub,
         "securities": b["securities"], "liquidity": b["liquidity"],
         "retail_current": b["retail_current"], "retail_term": b["retail_term"],
         "corp_funds": b["corp_funds"], "wholesale": b["wholesale"]}
    return E, iea


def transmission(D, fb, cr, cs, E, iea, key_H=10.5, ofz_H=None, h=1.0, T_star=None, phi=0.0,
                 asset_slopes=None, psi=None, bounds=(30.0, 55.0)):
    """T = ∂Nss/∂δ по MODEL §4.5 (центральная разность, h — п.п.). Ставки в %; T — доля на долю."""
    psi = cs["psi"] if psi is None else psi
    c_ref, key_ref = cs["c_ref"], cs["key_ref"]
    beta = {b: fb[b]["free"]["a"] for b in ("retail_current", "retail_term", "corp_funds")}
    beta["wholesale"] = 1.0
    s = {b: fb[b]["free"]["s"] for b in ("retail_current", "retail_term", "corp_funds", "wholesale")}
    slopes = asset_slopes or {}
    phi_books = ("corp_loans", "retail_loans")

    def nss(dl):
        k = key_H + dl
        a = 0.0
        for bk in ("corp_loans", "retail_loans", "mortgage_sub", "securities", "liquidity"):
            sl = slopes.get(bk, 1.0)
            comp = phi if bk in phi_books else 0.0
            a += E[bk] / iea * (sl * dl - comp * dl)       # уровни и спреды не влияют на производную
        lr = (E["retail_current"] + E["retail_term"]) / iea
        cstar = min(max(c_ref - psi * (k - key_ref), bounds[0]), bounds[1]) / 100
        fl = cstar * (beta["retail_current"] * k + s["retail_current"]) + (1 - cstar) * (beta["retail_term"] * k + s["retail_term"])
        liab = sum(E[b] / iea * (beta[b] * k + s[b]) for b in ("corp_funds", "wholesale")) + lr * fl
        return a - liab
    return (nss(h) - nss(-h)) / (2 * h)


def world_lt_refs():
    out = {}
    with open(L.P("stage1", "worlds", "world_paths_quarterly.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["period"] == "LT":
                out[r["world"]] = {"key": float(r["key_avg"]), "ofz_1y": float(r["ofz_1y"]),
                                   "ofz_3y": float(r["ofz_3y"]), "ofz_5y": float(r["ofz_5y"])}
    return out


def nss_levels(fb, cs, E, iea, phi, bounds=(35.0, 52.0), dia=0.0):
    """Стационарный ЧПМ (базис движка, %, MODEL §4.5, σ0 = 0) на LT-ставках миров N/H/M.
    LT-спред актива — форма движка в мире H: s − (1 − a)·ref_H,LT; сжатие φ — у книг Φ (кредиты, кроме льготной)."""
    W = world_lt_refs()
    H = W["H"]
    out = {}
    for w, rf in W.items():
        a = 0.0
        for bk, fbk in (("corp_loans", "corp_loans"), ("retail_loans", "retail_market"), ("securities", "securities"),
                        ("liquidity", "liquidity")):
            f = fb[fbk]["free"]
            ref = f["ref"]
            s_lt = f["s"] - (1 - f["a"]) * H[ref]
            comp = phi * (rf[ref] - H[ref]) if bk in ("corp_loans", "retail_loans") else 0.0
            a += E[bk] / iea * (rf[ref] + s_lt - comp)
        a += E["mortgage_sub"] / iea * (rf["key"] + fb["retail_market"]["m"])
        k = rf["key"]
        cst = min(max(cs["c_ref"] - cs["psi"] * (k - cs["key_ref"]), bounds[0]), bounds[1]) / 100
        fr = {b: fb[b]["free"] for b in ("retail_current", "retail_term", "corp_funds", "wholesale")}
        fl = cst * (fr["retail_current"]["a"] * k + fr["retail_current"]["s"]) +             (1 - cst) * (fr["retail_term"]["a"] * k + fr["retail_term"]["s"])
        lr = (E["retail_current"] + E["retail_term"]) / iea
        liab = lr * fl + sum(E[b] / iea * (fr[b]["a"] * k + fr[b]["s"]) for b in ("corp_funds", "wholesale"))
        out[w] = {"nss": a - liab - dia * lr, "c_star": cst, "key": k}
    return out


def world_paths():
    out = {}
    with open(L.P("stage1", "worlds", "world_paths_quarterly.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["period"] != "LT":
                out.setdefault(r["world"], {})[r["period"]] = {k: float(r[k2]) for k, k2 in (
                    ("key", "key_avg"), ("ofz_1y", "ofz_1y"), ("ofz_3y", "ofz_3y"), ("ofz_5y", "ofz_5y"))}
    return out


def near_to_lt(y, near, lt, y0=2026, y1=2028):
    """Спред балансирующей книги (ρ = 1): от значения якоря (год y0) линейно к истории (с года y1)."""
    w = min(1.0, max(0.0, (y - y0) / (y1 - y0)))
    return near + (lt - near) * w


def forward(fb, cs, E, iea, anchor_rates, cr, world, phi, bounds=(35.0, 52.0), key_anchor=None):
    """Упрощённый прогон книг по пути мира 2026К3–2036К4: структура баланса якоря неизменна, кроме доли
    текущих счетов (c* и ρ_c); спред актива в мире H — s − (1 − a)·ref_H(q), сжатие φ·(ref_W − ref_H) у книг Φ.
    Это проверка калибровки (путь ЧПМ против гайденса и LT), не ядро."""
    WP = world_paths()
    P, H = WP[world], WP["H"]
    qs = sorted(P)
    y = dict(anchor_rates)
    c = cs["c_ref"] / 100
    out = []
    for q in qs:
        rf, rh = P[q], H[q]
        for bk, fbk in (("corp_loans", "corp_loans"), ("retail_loans", "retail_market"), ("securities", "securities"),
                        ("liquidity", "liquidity")):
            f = fb[fbk]["free"]
            ref = f["ref"]
            s_q = f["s"] - (1 - f["a"]) * rh[ref]
            if bk == "liquidity" and key_anchor is not None:      # ближний спред — по ставке якоря
                s_near = anchor_rates["liquidity"] - f["a"] * key_anchor + (f["s"] - (1 - f["a"]) * rh[ref]) - f["s"]
                s_q = near_to_lt(int(q[:4]), s_near, s_q)
            comp = phi * (rf[ref] - rh[ref]) if bk in ("corp_loans", "retail_loans") else 0.0
            y[bk] = y[bk] + f["rho"] * (rf[ref] + s_q - comp - y[bk])
        y["mortgage_sub"] = rf["key"] + fb["retail_market"]["m"]
        for bk in ("retail_current", "retail_term", "corp_funds", "wholesale"):
            f = fb[bk]["free"]
            s_b = f["s"]
            if bk == "wholesale" and key_anchor is not None:
                s_b = near_to_lt(int(q[:4]), anchor_rates["wholesale"] - key_anchor, f["s"])
            y[bk] = y[bk] + f["rho"] * (f["a"] * rf["key"] + s_b - y[bk])
        cst = min(max(cs["c_ref"] - cs["psi"] * (rf["key"] - cs["key_ref"]), bounds[0]), bounds[1]) / 100
        c = c + cs["rho"] * (cst - c)
        fl_tot = E["retail_current"] + E["retail_term"]
        ii = sum(E[b] * y[b] for b in ("corp_loans", "retail_loans", "mortgage_sub", "securities", "liquidity")) / 100
        ie = (fl_tot * (c * y["retail_current"] + (1 - c) * y["retail_term"]) + E["corp_funds"] * y["corp_funds"]
              + E["wholesale"] * y["wholesale"]) / 100 + cr["dia"] * fl_tot / 100
        oth = cr["other_ii"] * iea / 100 - cr["other_ie"] * (fl_tot + E["corp_funds"] + E["wholesale"]) / 100
        out.append({"q": q, "key": rf["key"], "nim_eng": (ii - ie + oth) / iea * 100, "c": c})
    return out


# ====================================================================== шок 2014 и смягчение 2025–26
def shock_2014(D, fb, cr):
    """Книги с оценками 2019–2026 на ставках 2013К4–2016К4, структура баланса 31.12.2018 (самая ранняя
    в МСФО этапа 1), стартовые ставки — стационарные при ставках 2013К4; льготной ипотеки нет."""
    qs = L.q_range("2013Q4", "2016Q4")
    keyv = [D["key"][q]["avg"] for q in qs]
    ofz = {t: [D["ofz"].get(q, D["ofz"]["2014Q1"])[f"ofz_{t}y"] for q in qs] for t in (1, 3, 5)}
    refs = {"key": keyv, "ofz_1y": ofz[1], "ofz_3y": ofz[3], "ofz_5y": ofz[5]}
    b = D["qb"]["2018Q4"]
    P = {}
    for bk in ("corp_loans", "securities", "liquidity"):
        f = fb[bk]["free"]
        r = refs[f["ref"]]
        P[bk] = L.simulate(r, f["rho"], f["a"], f["s"], f["a"] * r[0] + f["s"])
    f = fb["retail_market"]["free"]
    r = refs[f["ref"]]
    P["retail_loans_all"] = L.simulate(r, f["rho"], f["a"], f["s"], f["a"] * r[0] + f["s"])
    for bk in ("retail_current", "retail_term", "corp_funds", "wholesale"):
        f = fb[bk]["free"]
        P[bk] = L.simulate(keyv, f["rho"], f["a"], f["s"], f["a"] * keyv[0] + f["s"])
    out = []
    for i, q in enumerate(qs):
        ii = sum(P[bk][i] * b[bk] for bk in ("corp_loans", "retail_loans_all", "securities", "liquidity")) / 100 \
            + cr["other_ii"] * b["iea"] / 100
        ie = sum(P[bk][i] * b[bk] for bk in ("retail_current", "retail_term", "corp_funds", "wholesale")) / 100 \
            + cr["other_ie"] * b["ibl"] / 100 + cr["dia"] * b["retail_funds"] / 100
        out.append({"q": q, "key": keyv[i], "nim": (ii - ie) / b["iea"] * 100, "ay": ii / b["iea"] * 100,
                    "cof": ie / b["ibl"] * 100, "nim_mgmt": D["mg"]["nim"].get(q),
                    "ay_mgmt": D["mg"]["asset_yield"].get(q), "cof_mgmt": D["mg"]["cost_of_funding"].get(q)})
    return out


# ====================================================================== отчёт
def md_table(head, rows):
    s = "| " + " | ".join(head) + " |\n|" + "---|" * len(head) + "\n"
    for r in rows:
        s += "| " + " | ".join("" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v)) for v in r) + " |\n"
    return s


def main():
    D = load()
    cr = const_rates(D)
    fb = fit_books(D, asym=True)
    fb_no22 = fit_books(D, include_2022=False)
    fb_oos = fit_books(D, last=OOS_LAST)
    cs = fit_current_share(D)
    cs["c_ref"] = D["qb"][ANCHOR_Q]["retail_current"] / D["qb"][ANCHOR_Q]["retail_funds"] * 100
    cs["key_ref"] = D["key"][ANCHOR_Q]["avg"]
    rows, ann, bridge, P = history(D, fb, cr)
    rows_a1, ann_a1, _, _ = history(D, fb, cr, variant="a1")
    rows_oos, _, _, _ = history(D, fb_oos, cr)
    rows_asym, ann_asym, _, _ = history(D, fb, cr, variant="asym")
    emp = empirical(D)
    wsub_q = subsidized_share(D)
    wsub_anchor = wsub_q[ANCHOR_Q][2]
    E, iea = anchor_structure(D, fb, cr, wsub_anchor)
    hh = L.ifrs_history()
    bv_common = hh["eq_shareholders"]["2026-06-30"] - hh["eq_at1"]["2026-06-30"]   # капитал обыкновенных (8 387,2) [Ф: МСФО 6М26]
    roe_k = iea / bv_common * (1 - TAX_EFF)

    # --- три границы передачи
    rc = E["retail_current"] / iea
    cc = (L.ifrs_history()["dep_corp_other_current"]["2026-06-30"] + L.ifrs_history()["dep_gov_current"]["2026-06-30"]) / iea
    ibl = sum(E[b] for b in ("retail_current", "retail_term", "corp_funds", "wholesale")) / iea
    mech = {
        "capital_and_fl_current_free": 1 - (ibl - rc),
        "capital_and_all_current_free": 1 - (ibl - rc - cc),
        "equity_only_free": bv_common / iea,
        "equity_and_noninterest_liab_free": 1 - ibl,
        "calibrated_betas_phi0": transmission(D, fb, cr, cs, E, iea),
        "calibrated_betas_phi0_psi0": transmission(D, fb, cr, cs, E, iea, psi=0.0),
    }
    slopes_hist = {"corp_loans": fb["corp_loans"]["free"]["a"], "retail_loans": fb["retail_market"]["free"]["a"],
                   "securities": fb["securities"]["free"]["a"], "liquidity": fb["liquidity"]["free"]["a"]}
    mech["history_slopes"] = transmission(D, fb, cr, cs, E, iea, asset_slopes=slopes_hist)
    h = L.ifrs_history()
    disc = []
    for dte, y in (("2018-12-31", 2018), ("2019-12-31", 2019), ("2020-12-31", 2020), ("2021-12-31", 2021),
                   ("2023-12-31", 2023), ("2024-12-31", 2024), ("2025-12-31", 2025)):
        up = h["nii_sens_rub_up100bp"].get(dte)
        up4 = h["nii_sens_rub_up400bp"].get(dte)
        v = up if up is not None else (up4 / 4 if up4 is not None else None)
        qs = [q for q in L.q_range(f"{y}Q1", f"{y}Q4") if q in TIX and q != "2018Q4"]
        ieay = sum(avg_bal(D, q, ["iea"]) * L.q_days(q) for q in qs) / sum(L.q_days(q) for q in qs) if qs             else D["qb"]["2018Q4"]["iea"]          # 2018: остаток на 31.12.2018 (средний за год не собран)
        disc.append({"date": dte, "dnii_100bp": v, "basis": "±100 б.п." if up is not None else "±400 б.п. / 4",
                     "iea_avg": ieay, "T": (v / ieay * 100) if (v is not None and ieay) else None})
    # --- φ по MODEL §4.5 при разных T*
    a_phi = (E["corp_loans"] + E["retail_loans"]) / iea
    T_H0 = mech["calibrated_betas_phi0"]
    phi_tab = []
    for Ts in (0.0, 0.02, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.14):
        phi = (T_H0 - Ts) / a_phi
        tn = transmission(D, fb, cr, cs, E, iea, key_H=8.0, phi=phi)
        tm = transmission(D, fb, cr, cs, E, iea, key_H=17.0, phi=phi)
        lv = nss_levels(fb, cs, E, iea, phi, dia=cr["dia"])
        real = (lv["M"]["nss"] - lv["N"]["nss"]) / (lv["M"]["key"] - lv["N"]["key"])
        phi_tab.append({"T_star": Ts, "phi": phi, "T_N": tn, "T_M": tm, "roe_pp_per_pp": Ts * roe_k,
                        "nss_N": lv["N"]["nss"], "nss_H": lv["H"]["nss"], "nss_M": lv["M"]["nss"], "realized_M_N": real})
    lv0 = nss_levels(fb, cs, E, iea, 0.0, dia=cr["dia"])
    # σ0 для цели A-N2 (ЧПМ сквозь цикл 5,7 % упр. при ставках мира H): движок = упр. − мост (1К24–2К26)
    a_lt = (E["corp_loans"] + E["retail_loans"]) / iea          # книги с lt_shift (кредиты, кроме льготной)
    target_eng = 5.7 - bridge["nim"]
    sigma0 = (target_eng - lv0["H"]["nss"]) / a_lt

    # --- 2014 и смягчение
    sh = shock_2014(D, fb, cr)

    # ================================================================== запись
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "rates_panel.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["quarter", "key_avg", "key_eop", "ofz_1y", "ofz_3y", "ofz_5y", "sub_share_retail",
                    "corp_loan_yield_mgmt", "retail_loan_yield_mgmt"] +
                   [f"ifrs_{b}" for b in ("loans", "securities", "liquidity", "retail_current", "retail_term", "corp_funds", "wholesale", "dia")] +
                   ["fl_current_share", "balances_interpolated"])
        for q in QF:
            t = TIX[q]
            row = [q, R(D["key"][q]["avg"], 3), D["key"][q]["eop"]] + [R(D["refs"][f"ofz_{k}y"][t], 3) for k in (1, 3, 5)] + \
                  [R(wsub_q[q][2], 4), D["mg"]["corp_loan_yield"].get(q), D["mg"]["retail_loan_yield"].get(q)]
            row += [R(ifrs_rate_q(D, b, q), 3) if q in D["fq"] else None
                    for b in ("loans", "securities", "liquidity", "retail_current", "retail_term", "corp_funds", "wholesale", "dia")]
            row += [R(D["qb"][q]["retail_current"] / D["qb"][q]["retail_funds"] * 100, 2), D["qb"][q]["_interp"]]
            w.writerow(row)
    with open(os.path.join(DATA, "history_fit.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["quarter", "key_avg", "balances_interpolated"] +
                   [f"{m}_{k}" for m in ("nim", "ay", "cof") for k in ("fact_mgmt", "model_mgmt", "err_mgmt", "fact_eng", "model_eng", "err_eng")] +
                   ["nim_model_eng_a1", "nim_model_eng_oos", "nim_model_eng_asym"])
        for r, ra, ro, rs_ in zip(rows, rows_a1, rows_oos, rows_asym):
            line = [r["q"], R(r["key"], 3), r["interp"]]
            for m in ("nim", "ay", "cof"):
                fm, me = r["fact_mgmt"][m], r["model"][m]
                mm = me + bridge[m]
                fe = r["fact_eng"].get(m)
                line += [fm, R(mm, 3), R(mm - fm, 3) if fm is not None else None, R(fe, 3), R(me, 3), R(me - fe, 3) if fe is not None else None]
            line += [R(ra["model"]["nim"], 3), R(ro["model"]["nim"], 3), R(rs_["model"]["nim"], 3)]
            w.writerow(line)
    with open(os.path.join(DATA, "shock_2014.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["quarter", "key_avg", "nim_model_eng", "ay_model_eng", "cof_model_eng", "nim_mgmt", "ay_mgmt", "cof_mgmt"])
        for r in sh:
            w.writerow([r["q"], R(r["key"], 3), R(r["nim"], 3), R(r["ay"], 3), R(r["cof"], 3), r["nim_mgmt"], r["ay_mgmt"], r["cof_mgmt"]])

    def pack(f):
        return {"ref": f["ref"], "rho": f["rho"], "rho_ci95": f.get("rho_ci"), "a_or_beta": R(f["a"], 4), "s_pp": R(f["s"], 3),
                "y0_2018Q4": R(f["y0"], 3), "rmse_w_pp": R(f["rmse_w"], 3), "n_obs": f["n_obs"],
                "se": {k: R(v, 4) for k, v in f["se"].items()}}
    books = {b: {"free": pack(fb[b]["free"])} for b in fb}
    for b in ("corp_loans", "retail_market", "securities", "liquidity"):
        books[b]["a1"] = pack(fb[b]["a1"])
        if "cand" in fb[b]:
            books[b]["by_ref"] = {r: {"rho": c["rho"], "a": R(c["a"], 3), "s": R(c["s"], 3), "rmse": R(c["rmse_w"], 3)} for r, c in fb[b]["cand"].items()}
    for b in ("retail_current", "retail_term", "corp_funds"):
        books[b]["by_ref"] = {r: {"rho": c["rho"], "beta": R(c["a"], 3), "s": R(c["s"], 3), "rmse": R(c["rmse_w"], 3)} for r, c in fb[b]["cand"].items()}
    books["liquidity"]["rho_free"] = pack(fb["liquidity"]["rho_free"])
    for b in fb:
        if "asym" in fb[b]:
            books[b]["asym"] = dict(pack(fb[b]["asym"]), rho_down=fb[b]["asym"]["rho_down"])
    books["retail_market"]["m_rmse_min"] = fb["retail_market"]["m_rmse_min"]
    # спред в мире H по годам: s_H(q) = s − (1 − a)·ref_H(q) — наклон истории внутри мира H (MODEL §4.4)
    wp = {}
    with open(L.P("stage1", "worlds", "world_paths_quarterly.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["world"] == "H":
                wp[r["period"]] = {"key": float(r["key_avg"]), "ofz_1y": float(r["ofz_1y"]), "ofz_3y": float(r["ofz_3y"]),
                                   "ofz_5y": float(r["ofz_5y"])}
    spread_H = {}
    for b in ("corp_loans", "retail_market", "securities", "liquidity"):
        f = fb[b]["free"]
        tr = {"anchor_2026Q2": R(f["s"] - (1 - f["a"]) * D["refs"][f["ref"]][TIX[ANCHOR_Q]], 3)}
        for y in range(2026, 2037):
            qs = [q for q in wp if q.startswith(str(y))]
            tr[str(y)] = R(f["s"] - (1 - f["a"]) * sum(wp[q][f["ref"]] for q in qs) / len(qs), 3)
        tr["LT"] = R(f["s"] - (1 - f["a"]) * wp["LT"][f["ref"]], 3)
        spread_H[b] = tr
    books["wholesale"]["diag_free"] = pack(fb["wholesale"]["diag"])
    books["retail_market"]["m_pp"] = fb["retail_market"]["m"]
    books["retail_market"]["m_fixed"] = {str(m): pack(v) for m, v in fb["retail_market"]["m_fixed"].items()}
    no22 = {b: pack(fb_no22[b]["free"]) for b in fb_no22}
    oos = {b: pack(fb_oos[b]["free"]) for b in fb_oos}

    # ставки якоря (2К26, МСФО, act/365)
    rate_anchor = {b: ifrs_rate_q(D, b, ANCHOR_Q) for b in ("loans", "securities", "liquidity", "retail_current", "retail_term", "corp_funds", "wholesale", "dia")}
    cly, rly = D["mg"]["corp_loan_yield"][ANCHOR_Q], D["mg"]["retail_loan_yield"][ANCHOR_Q]
    bq = D["qb"][ANCHOR_Q]; bp = D["qb"][L.q_prev(ANCHOR_Q)]
    avc = (bq["corp_loans"] + bp["corp_loans"]) / 2; avr = (bq["retail_loans_all"] + bp["retail_loans_all"]) / 2
    scale = rate_anchor["loans"] / ((cly * avc + rly * avr) / (avc + avr))
    y_corp0, y_ret0 = cly * scale, rly * scale
    y_sub0 = D["key"][ANCHOR_Q]["avg"] + fb["retail_market"]["m"]
    w_sub_avg = (wsub_q[ANCHOR_Q][2] + wsub_q["2026Q1"][2]) / 2
    y_mkt0 = (y_ret0 - w_sub_avg * y_sub0) / (1 - w_sub_avg)
    anchor_rates = {"corp_loans": y_corp0, "retail_loans": y_mkt0, "mortgage_sub": y_sub0,
                    "securities": rate_anchor["securities"], "liquidity": rate_anchor["liquidity"],
                    "retail_current": rate_anchor["retail_current"], "retail_term": rate_anchor["retail_term"],
                    "corp_funds": rate_anchor["corp_funds"], "wholesale": rate_anchor["wholesale"]}
    phi06 = next(p_["phi"] for p_ in phi_tab if abs(p_["T_star"] - 0.06) < 1e-9)
    # φ, при котором РЕАЛИЗОВАННАЯ передача (стационарный ЧПМ M − N на LT-ставках миров) = цели (линейна по φ)
    r0 = nss_levels(fb, cs, E, iea, 0.0, dia=cr["dia"]); r1 = nss_levels(fb, cs, E, iea, 1.0, dia=cr["dia"])
    dk = r0["M"]["key"] - r0["N"]["key"]
    re0 = (r0["M"]["nss"] - r0["N"]["nss"]) / dk; re1 = (r1["M"]["nss"] - r1["N"]["nss"]) / dk
    phi_real = {Ts: (re0 - Ts) / (re0 - re1) for Ts in (0.0, 0.02, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.14)}
    for p_ in phi_tab:
        ph = phi_real[p_["T_star"]]
        p_["phi_for_realized"] = ph
        p_["T_H_at_phi_realized"] = T_H0 - ph * a_phi
    phi_r06 = phi_real[0.06]
    fwd = {}
    for wname in ("N", "H", "M"):
        for lab, ph in (("phi0", 0.0), ("phi_T006", phi06), ("phi_real006", phi_r06)):
            path = forward(fb, cs, E, iea, anchor_rates, cr, wname, ph, key_anchor=D["key"][ANCHOR_Q]["avg"])
            by = {}
            for r_ in path:
                by.setdefault(r_["q"][:4], []).append(r_["nim_eng"])
            fwd[f"{wname}_{lab}"] = {"2026Q3": path[0]["nim_eng"], "2026Q4": path[1]["nim_eng"],
                                     **{y_: sum(v) / len(v) for y_, v in by.items() if y_ != "2026"},
                                     "FY2026_mgmt": (D["mg"]["nim"]["2026Q1"] + D["mg"]["nim"]["2026Q2"]
                                                     + path[0]["nim_eng"] + path[1]["nim_eng"] + 2 * bridge["nim"]) / 4}

    results = {
        "generated": "2026-09-30", "script": "data/assumptions/evidence/nii/calib_nii.py",
        "inputs_sha256": {p: sha(L.P(*p.split("/"))) for p in (
            "research/facts/cbr_key_rate_changes.csv", "research/facts/sber_facts.csv", "stage1/mgmt/mgmt_kpi.csv",
            "stage1/ifrs/ifrs_quarterly.csv", "stage1/ifrs/ifrs_history.csv", "stage1/ifrs/ifrs_all.csv",
            "stage1/calib/nii/data/ofz_quarterly.csv", "stage1/worlds/world_paths_quarterly.csv")},
        "const_rates_pct": {k: R(v, 4) for k, v in cr.items()},
        "books": books, "books_without_FY2022": no22, "books_fit_to_2024Q4": oos, "spread_H_engine_form_pp": spread_H,
        "current_share": {"psi_pp_per_pp": R(cs["psi"], 4), "rho": cs["rho"], "rho_ci95": None, "c_star_intercept_pp": R(cs["s"], 3),
                          "rmse_pp": R(cs["rmse_w"], 3), "c_ref_pct": R(cs["c_ref"], 3), "key_ref_pct": R(cs["key_ref"], 3),
                          "se": {k: R(v, 4) for k, v in cs["se"].items()}, "history_pre_2022Q4": cs["history"]},
        "subsidized": {"share_of_retail_anchor": R(wsub_anchor, 4), "share_of_mortgage_anchor": R(wsub_q[ANCHOR_Q][1], 4),
                       "balance_anchor": R(wsub_q[ANCHOR_Q][0], 1),
                       "alt_runoff": {f"{a}/{b}": R(subsidized_share(D, a, b)[ANCHOR_Q][1], 4) for a, b in ((0.02, 0.045), (0.025, 0.04), (0.04, 0.03))}},
        "anchor": {"balances": {k: R(v, 1) for k, v in E.items()}, "iea": R(iea, 1),
                   "rates_ifrs_2Q26_pct": {k: R(v, 3) for k, v in rate_anchor.items()},
                   "corp_loans_rate": R(y_corp0, 3), "retail_all_rate": R(y_ret0, 3), "mortgage_sub_rate": R(y_sub0, 3),
                   "retail_market_rate": R(y_mkt0, 3), "loans_mgmt_to_ifrs_scale": R(scale, 4),
                   "key_avg_2Q26": R(D["key"][ANCHOR_Q]["avg"], 3), "fl_current_share": R(cs["c_ref"], 3)},
        "bridge_mgmt_minus_eng_2024Q1_2026Q2": {k: R(v, 3) for k, v in bridge.items()},
        "forward_nim_eng": {k: {kk: R(vv, 3) for kk, vv in v.items()} for k, v in fwd.items()},
        "history_errors": {}, "transmission": {}, "shock_2014": sh,
    }
    for m in ("nim", "ay", "cof"):
        for lab, rs, sel in (("mgmt_2019_2021", rows, lambda r: r["q"] < "2022Q1"),
                             ("mgmt_2023_2026", rows, lambda r: r["q"] >= "2023Q1"),
                             ("eng_2023_2026", rows, lambda r: r["q"] >= "2023Q1"),
                             ("eng_2023_2026_a1", rows_a1, lambda r: r["q"] >= "2023Q1"),
                             ("mgmt_2025_2026_oos", rows_oos, lambda r: r["q"] >= "2025Q1"),
                             ("mgmt_2019_2021_asym", rows_asym, lambda r: r["q"] < "2022Q1"),
                             ("mgmt_2023_2026_asym", rows_asym, lambda r: r["q"] >= "2023Q1"),
                             ("eng_2023_2026_asym", rows_asym, lambda r: r["q"] >= "2023Q1")):
            if lab.startswith("mgmt"):
                pr = [(r["fact_mgmt"][m], r["model"][m] + bridge[m]) for r in rs if sel(r)]
            else:
                pr = [(r["fact_eng"].get(m), r["model"][m]) for r in rs if sel(r)]
            results["history_errors"][f"{m}_{lab}"] = {k: R(v, 3) for k, v in (err_stats(pr) or {}).items()}
        results["history_errors"][f"{m}_eng_annual_2019_2022"] = {k: R(v, 3) for k, v in err_stats([(a["fact_eng"][m], a["model"][m]) for a in ann]).items()}
    results["annual_eng"] = [{"y": a["y"], **{f"{k}_{m}": R(a[k][m], 3) for k in ("fact_eng", "model", "fact_mgmt") for m in ("nim", "ay", "cof")}} for a in ann]
    results["transmission"] = {
        "empirical": {k: ({kk: (R(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items() if kk != "res"} if isinstance(v, dict) and "b" in v else v)
                      for k, v in emp.items()},
        "mechanical": {k: R(v, 4) for k, v in mech.items()},
        "disclosed": [{k: (R(v, 4) if isinstance(v, float) else v) for k, v in d.items()} for d in disc],
        "roe_factor_iea_bv_1mtax": R(roe_k, 3), "phi_table": [{k: R(v, 4) for k, v in p.items()} for p in phi_tab],
        "phi_books_weight": R(a_phi, 4), "T_H_phi0": R(T_H0, 4),
        "nss_phi0": {w: {k: R(v, 4) for k, v in x.items()} for w, x in lv0.items()},
        "A_N2_check": {"target_mgmt": 5.7, "bridge_nim": R(bridge["nim"], 3), "target_eng": R(target_eng, 3),
                       "nss_H_sigma0_0": R(lv0["H"]["nss"], 3), "sigma0_pp": R(sigma0, 3),
                       "other_net_interest_pp_of_iea": R(cr["other_ii"] - cr["other_ie"] * 55291 / 67627, 3)}, "asset_slopes_hist": {k: R(v, 3) for k, v in slopes_hist.items()},
    }
    near = {"wholesale": rate_anchor["wholesale"] - D["key"][ANCHOR_Q]["avg"],
            "liquidity": rate_anchor["liquidity"] - D["key"][ANCHOR_Q]["avg"],
            "liquidity_hist_form": fb["liquidity"]["free"]["s"] - (1 - fb["liquidity"]["free"]["a"]) * D["key"][ANCHOR_Q]["avg"],
            "wholesale_incl_other_ie": (D["fq"][ANCHOR_Q]["wholesale"] + D["fq"][ANCHOR_Q]["other_ie"]) / avg_bal(D, ANCHOR_Q, ["wholesale"])
            * 365 / L.q_days(ANCHOR_Q) * 100 - D["key"][ANCHOR_Q]["avg"]}
    results["near_spreads_anchor_pp"] = {k: R(v, 3) for k, v in near.items()}
    # минимум доли ликвидных (долговые бумаги + ликвидность) в активах: отчётные даты 2018–2026. Определение ядра и
    # фактов (MODEL §4.10): книга бумаг — только долговые, акции и паи фондов — в прочих активах (книга 1.2)
    eqf = L.equities_and_funds()
    lm = {q: (D["qb"][q]["securities"] - eqf[L.q_end(q).isoformat()] + D["qb"][q]["liquidity"]) / D["qb"][q]["total_assets"]
          for q in QF if not D["qb"][q]["_interp"]}
    lm_eq = {q: (D["qb"][q]["securities"] + D["qb"][q]["liquidity"]) / D["qb"][q]["total_assets"]
             for q in QF if not D["qb"][q]["_interp"]}
    results["liquid_share"] = {"definition": "(долговые бумаги + ликвидность) / активы; акции и паи фондов — в прочих активах",
                               "min": R(min(lm.values()), 4), "min_at": min(lm, key=lm.get), "anchor": R(lm[ANCHOR_Q], 4),
                               "with_equities": {"min": R(min(lm_eq.values()), 4), "min_at": min(lm_eq, key=lm_eq.get),
                                                 "anchor": R(lm_eq[ANCHOR_Q], 4)}}
    write_proposal(D, fb, cs, results, spread_H, near, cr, E, iea, anchor_rates, rate_anchor, wsub_q, scale)
    with open(os.path.join(DATA, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    write_readme(D, fb, cs, rows, ann, bridge, results, sh, rows_oos)
    print("готово:", os.path.join(DATA, "results.json"))
    return D, fb, cs, rows, ann, bridge, results, sh


# ====================================================================== предложение для книги и фактов
def write_proposal(D, fb, cs, res, spread_H, near, cr, E, iea, anchor_rates, rate_anchor, wsub_q, scale):
    f4 = lambda x: round(x / 100, 5)  # noqa: E731  (п.п. → доли)

    def traj(tr, near_=None, lt_from=2031):
        keys = {str(y): tr[str(y)] for y in range(2026, lt_from)}
        if near_ is not None:                       # балансирующая: 2026 — по якорю, к 2028 — к истории
            for y in range(2026, lt_from):
                keys[str(y)] = near_to_lt(y, near_, tr[str(y)])
        d = {k: f4(v) for k, v in keys.items()}
        d["LT"] = f4(tr["LT"])
        d["LT_from"] = lt_from
        return d

    B = {b: fb[b]["free"] for b in fb}
    liq_near = rate_anchor["liquidity"] - D["key"][ANCHOR_Q]["avg"]
    pt = res["transmission"]["phi_table"]
    phi_T = next(p["phi"] for p in pt if p["T_star"] == 0.06)
    phi_R = next(p["phi_for_realized"] for p in pt if p["T_star"] == 0.06)
    ci = lambda b: f"{B[b]['rho_ci'][0]}–{B[b]['rho_ci'][1]}"  # noqa: E731
    wh = {"2026": f4(near["wholesale"]), "2027": f4(near_to_lt(2027, near["wholesale"], B["wholesale"]["s"])),
          "LT": f4(B["wholesale"]["s"]), "LT_from": 2028}
    lines = [
        "# Предложение calib-nii для блока nii книги 1.0 (доли). Сгенерировано stage1/calib/nii/calib_nii.py, 30.09.2026.",
        "# Спред активов — траектория в мире H в форме движка: s − (1 − a)·ref_H(q) (наклон истории a < 1 внутри мира H);",
        "# сжатие между мирами — φ (выводит ядро из A-N3), LT-сдвиг σ0 — из A-N2. Обоснование — REPORT-nii.md.",
        "nii:",
        "  books:",
        f"    corp_loans:     {{side: asset, ref: {B['corp_loans']['ref']}, rho: {B['corp_loans']['rho']}, "
        f"spread: {json.dumps(traj(spread_H['corp_loans']))}, phi: true, lt_shift: true, sector: corporate, "
        f"cor_segment: corporate, fv_share: anchor}}   # история: a = {B['corp_loans']['a']:.3f}, "
        f"s = {B['corp_loans']['s']:.2f} п.п., ρ 95 % {ci('corp_loans')}",
        f"    retail_loans:   {{side: asset, ref: {B['retail_market']['ref']}, rho: {B['retail_market']['rho']}, "
        f"spread: {json.dumps(traj(spread_H['retail_market']))}, phi: true, lt_shift: true, sector: retail_other, "
        f"cor_segment: retail}}   # рыночная ипотека + потреб + карты + авто (история не различает); "
        f"a = {B['retail_market']['a']:.3f}, s = {B['retail_market']['s']:.2f}, ρ 95 % {ci('retail_market')}; sector — решение ведущего",
        f"    mortgage_sub:   {{side: asset, ref: key, rho: 1.0, spread: {f4(fb['retail_market']['m'])}, phi: false, "
        "lt_shift: false, sector: mortgage, cor_segment: retail}   # ключевая + компенсация [НП: 2,0 МКД / 2,5 ИЖС с 2026, "
        "rg.ru 31.12.2025]; ось 0,020–0,035",
        f"    securities:     {{side: asset, ref: {B['securities']['ref']}, rho: {B['securities']['rho']}, "
        f"spread: {json.dumps(traj(spread_H['securities']))}, phi: false, lt_shift: false, balancing: true}}"
        f"   # a = {B['securities']['a']:.3f}, s = {B['securities']['s']:.2f}, ρ 95 % {ci('securities')}",
        f"    liquidity:      {{side: asset, ref: key, rho: 1.0, spread: {json.dumps(traj(spread_H['liquidity'], near_=liq_near))}, "
        f"phi: false, lt_shift: false, balancing: true}}   # a = {B['liquidity']['a']:.3f} (касса без дохода); "
        f"2026 — по ставке якоря {rate_anchor['liquidity']:.2f} %",
        f"    retail_current: {{side: liability, ref: key, rho: {B['retail_current']['rho']}, beta: {round(B['retail_current']['a'], 3)}, "
        f"spread: {f4(B['retail_current']['s'])}}}   # ρ 95 % {ci('retail_current')}",
        f"    retail_term:    {{side: liability, ref: key, rho: {B['retail_term']['rho']}, beta: {round(B['retail_term']['a'], 3)}, "
        f"spread: {f4(B['retail_term']['s'])}}}   # ρ 95 % {ci('retail_term')}",
        f"    corp_funds:     {{side: liability, ref: key, rho: {B['corp_funds']['rho']}, beta: {round(B['corp_funds']['a'], 3)}, "
        f"spread: {f4(B['corp_funds']['s'])}}}   # ρ 95 % {ci('corp_funds')}",
        f"    wholesale:      {{side: liability, ref: key, rho: 1.0, beta: 1.0, spread: {json.dumps(wh)}, balancing: true}}"
        f"   # 2К26: ключевая {near['wholesale']:+.2f} п.п. (с арендой/ПФИ/короткими {near['wholesale_incl_other_ie']:+.2f}); "
        f"история {B['wholesale']['s']:+.2f}",
        "  transmission:",
        f"    target: 0.06                # A-N3: центр 0,06, ось 0,00–0,14 (REPORT-nii §4); φ при T(H) = 0,06: {phi_T:.3f}; "
        f"φ при реализованной (M − N) = 0,06: {phi_R:.3f}",
        "  retail_current_share:",
        "    c_ref: anchor               # 14 309,0 / 34 478,5 = 0,415 [Ф: МСФО 6М26]",
        f"    key_ref: anchor             # средняя ключевая 2К26 по календарным дням = {D['key'][ANCHOR_Q]['avg'] / 100:.5f}",
        f"    psi: {cs['psi']:.3f}                  # доля на долю ключевой; оценка 2022К4–2026К2, ст. ош. {cs['se'].get('a', 0):.2f}",
        f"    rho: {cs['rho']}",
        "    bounds: [0.35, 0.52]        # [В] вокруг наблюдённых 40–50 % (2022К4–2026К2)",
        f"  dia_rate: {cr['dia'] / 100:.4f}             # взносы АСВ / средние средства ФЛ, среднее 1К23–2К26 "
        f"(2К26: {rate_anchor['dia']:.3f} %)",
        "volumes:",
        f"  liquid_min_share: {math.floor(res['liquid_share']['min'] * 100) / 100:.2f}      # минимум (долговые бумаги + ликвидность)/активы "
        f"на отчётных датах 2018–2026: {res['liquid_share']['min'] * 100:.2f} % ({res['liquid_share']['min_at']}); "
        f"якорь {res['liquid_share']['anchor'] * 100:.2f} %",
    ]
    with open(os.path.join(DATA, "nii_book_proposal.yaml"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # факты якоря по книгам (форма прил. B MODEL: {v, src} / {v, calc})
    bq, bp = D["qb"][ANCHOR_Q], D["qb"][L.q_prev(ANCHOR_Q)]
    src = ("МСФО 6М26 (research/primary/2026H1_ifrs_interim_statements.pdf) и 3М26 (primary/ifrs_3M2026_statements.pdf); "
           "stage1/ifrs/ifrs_history.csv, ifrs_quarterly.csv")
    ws0, ws1 = wsub_q[L.q_prev(ANCHOR_Q)][2], wsub_q[ANCHOR_Q][2]
    fq = D["fq"][ANCHOR_Q]

    def node(v, how):
        return {"v": None if v is None else round(v, 6), how[0]: how[1]}

    books = {}
    for b, cols in (("corp_loans", ["corp_loans"]), ("securities", ["securities"]), ("liquidity", ["liquidity"]),
                    ("retail_current", ["retail_current"]), ("retail_term", ["retail_term"]),
                    ("corp_funds", ["corp_funds"]), ("wholesale", ["wholesale"])):
        books[b] = {"balance_open": node(sum(bp[c] for c in cols), ("src", src)),
                    "balance_close": node(sum(bq[c] for c in cols), ("src", src))}
    books["retail_loans"] = {
        "balance_open": node(bp["retail_loans_all"] * (1 - ws0), ("calc", "розница АС × (1 − доля льготной); доля — накопление поколений (REPORT-nii §2.3)")),
        "balance_close": node(bq["retail_loans_all"] * (1 - ws1), ("calc", "то же на 30.06.2026"))}
    books["mortgage_sub"] = {
        "balance_open": node(bp["retail_loans_all"] * ws0, ("calc", "розница АС × доля льготной")),
        "balance_close": node(bq["retail_loans_all"] * ws1, ("calc", "то же на 30.06.2026"))}
    d = L.q_days(ANCHOR_Q)
    for b in ("securities", "liquidity", "retail_current", "retail_term", "corp_funds", "wholesale"):
        books[b]["interest_q"] = node(fq[b], ("src", src + "; процентные строки по инструментам (nii_lib._flows_from)"))
        books[b]["rate_anchor"] = node(rate_anchor[b] / 100, ("calc", "interest_q / средний остаток × 365/91"))
    books["corp_loans"]["rate_anchor"] = node(anchor_rates["corp_loans"] / 100, ("calc", f"упр. доходность ЮЛ 2К26 14,2 % × {scale:.4f} (масштаб к доходности кредитов МСФО 2К26)"))
    books["retail_loans"]["rate_anchor"] = node(anchor_rates["retail_loans"] / 100, ("calc", "упр. доходность ФЛ 2К26 16,7 % × масштаб, за вычетом льготной (ключевая 2К26 + 2,5 п.п.) по средней доле"))
    books["mortgage_sub"]["rate_anchor"] = node(anchor_rates["mortgage_sub"] / 100, ("calc", "средняя ключевая 2К26 + компенсация 2,5 п.п."))
    for b in ("corp_loans", "retail_loans", "mortgage_sub"):
        avg = (books[b]["balance_open"]["v"] + books[b]["balance_close"]["v"]) / 2
        books[b]["interest_q"] = node(anchor_rates[b] / 100 * avg * d / 365,
                                      ("calc", "rate_anchor × средний остаток × 91/365; сумма трёх книг ≈ процентный доход по кредитам МСФО 2К26 (1 970,0)"))
    out = {"basis": "IFRS", "as_of": "2026-06-30", "period": ANCHOR_Q, "books": books,
           "dia_q": node(fq["dia"], ("src", src)),
           "other_interest_net_q": node(fq["other_ii"] - fq["other_ie"], ("src", src + "; аренда, ПФИ, короткие позиции, срочные по СС — вне книг")),
           "nim_eng_q": node(fq["nii_reported"] * 365 / d / avg_bal(D, ANCHOR_Q, ["iea"]), ("calc", "ЧПД 2К26 × 365/91 / средние процентные активы")),
           "current_share_anchor": node(bq["retail_current"] / bq["retail_funds"], ("src", src + "; текущие ФЛ / средства ФЛ")),
           "key_avg_anchor_q": node(D["key"][ANCHOR_Q]["avg"] / 100, ("calc", "средняя ключевая 2К26 по календарным дням (research/facts/cbr_key_rate_changes.csv)"))}
    with open(os.path.join(DATA, "nii_books_anchor_proposal.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


# ====================================================================== README с результатами
def write_readme(D, fb, cs, rows, ann, bridge, res, sh, rows_oos):
    B = res["books"]
    s = ["# stage1/calib/nii — калибровка книг ЧПД и передачи ставки (результаты)\n",
         "Генерируется `python calib_nii.py` (30.09.2026). Разбор и рекомендации — `REPORT-nii.md`. "
         "Ставки — % годовых, базис МСФО (движок), если не сказано иное; упр. — базис mgmt. "
         "Машинный вид — `data/results.json`; ряды — `data/rates_panel.csv`, `data/history_fit.csv`, `data/shock_2014.csv`; "
         "КБД ОФЗ — `data/ofz_quarterly.csv` (из `fetch_zcyc.py`).\n"]
    s.append("## Книги: оценки (свободная траектория 2018К4–2026К2)\n")
    rows_t = []
    for b, lab in (("corp_loans", "корп. кредиты (упр. доходность ЮЛ)"), ("retail_market", "розница рыночная (упр. доходность ФЛ за вычетом льготной)"),
                   ("securities", "бумаги (МСФО)"), ("liquidity", "ликвидность (МСФО, ρ = 1)"),
                   ("retail_current", "текущие ФЛ (МСФО)"), ("retail_term", "срочные ФЛ (МСФО)"),
                   ("corp_funds", "средства ЮЛ (МСФО)"), ("wholesale", "опт (МСФО, ρ = 1, β = 1)")):
        f = B[b]["free"]
        rows_t.append([lab, f["ref"], f["rho"], f"{f['rho_ci95'][0]:.2f}–{f['rho_ci95'][1]:.2f}" if f["rho_ci95"] else "фикс.",
                       f["a_or_beta"], f["s_pp"], f["y0_2018Q4"], f["rmse_w_pp"], f["n_obs"]])
    s.append(md_table(["книга", "ref", "ρ", "ρ 95 %", "a / β", "s, п.п.", "y 2018К4", "RMSE, п.п.", "набл."], rows_t))
    s.append(f"\nЛьготная ипотека: ставка = ключевая + m, m = {B['retail_market']['m_pp']:.2f} п.п. (внешняя оценка; история розницы m не различает: "
             f"RMSE 0,332–0,340 при m = 1–5 п.п., минимум при {B['retail_market']['m_rmse_min']:.2f}); "
             f"доля в рознице на 30.06.2026 — {res['subsidized']['share_of_retail_anchor']*100:.1f} % "
             f"({res['subsidized']['share_of_mortgage_anchor']*100:.1f} % ипотеки, ≈{res['subsidized']['balance_anchor']:.0f} млрд).\n")
    s.append("\ny 2018К4 у книг с ρ ≈ 1 не идентифицирован (не влияет на путь).\n")
    s.append("\nАсимметричная подстройка (ρ при росте опоры / при снижении) — диагностика, в книгу не предлагается:\n")
    s.append(md_table(["книга", "ρ↑", "ρ↓", "a / β", "s", "RMSE", "RMSE симм."],
                      [[b, B[b]["asym"]["rho"], B[b]["asym"]["rho_down"], B[b]["asym"]["a_or_beta"], B[b]["asym"]["s_pp"],
                        B[b]["asym"]["rmse_w_pp"], B[b]["free"]["rmse_w_pp"]] for b in B if "asym" in B[b]]))
    s.append("\nВарианты с наклоном a = 1 (форма движка для активов):\n")
    s.append(md_table(["книга", "ref", "ρ", "s, п.п.", "RMSE, п.п."],
                      [[b, B[b]["a1"]["ref"], B[b]["a1"]["rho"], B[b]["a1"]["s_pp"], B[b]["a1"]["rmse_w_pp"]]
                       for b in ("corp_loans", "retail_market", "securities", "liquidity")]))
    s.append("\nВыбор опорной ставки (RMSE, п.п., при свободном наклоне):\n")
    rr = []
    for b in ("corp_loans", "retail_market", "securities", "retail_current", "retail_term", "corp_funds"):
        rr.append([b] + [B[b]["by_ref"].get(r, {}).get("rmse") for r in ("key", "ofz_1y", "ofz_3y", "ofz_5y")])
    s.append(md_table(["книга", "key", "ofz_1y", "ofz_3y", "ofz_5y"], rr))
    s.append("\nУстойчивость (ρ; a|β; s): без FY2022 / оценка только по 2024К4:\n")
    s.append(md_table(["книга", "полная", "без FY2022", "по 2024К4"],
                      [[b, f"{B[b]['free']['rho']}; {B[b]['free']['a_or_beta']}; {B[b]['free']['s_pp']}",
                        f"{res['books_without_FY2022'][b]['rho']}; {res['books_without_FY2022'][b]['a_or_beta']}; {res['books_without_FY2022'][b]['s_pp']}",
                        f"{res['books_fit_to_2024Q4'][b]['rho']}; {res['books_fit_to_2024Q4'][b]['a_or_beta']}; {res['books_fit_to_2024Q4'][b]['s_pp']}"]
                       for b in B]))
    c = res["current_share"]
    s.append(f"\n## Доля текущих счетов ФЛ\n\nc*(key) = c_ref − ψ·(key − key_ref), подстройка ρ_c: ψ = {c['psi_pp_per_pp']:.3f} (доля в п.п. на 1 п.п. ключевой), "
             f"ρ_c = {c['rho']:.2f}, RMSE {c['rmse_pp']:.2f} п.п. (оценка 2022К4–2026К2; до 2022 доля росла 25 → 48 % без связи со ставкой). "
             f"Якорь: c_ref = {c['c_ref_pct']:.1f} %, key_ref = {c['key_ref_pct']:.2f} %.\n")
    s.append("\n## История: факт и модель\n\nМост упр. − МСФО (аддитивный, окно 1К24–2К26): " +
             ", ".join(f"{k} {v:+.2f}" for k, v in res["bridge_mgmt_minus_eng_2024Q1_2026Q2"].items()) + " п.п.\n\n")
    hr = []
    for r in rows:
        fm = r["fact_mgmt"]; fe = r["fact_eng"]; mo = r["model"]
        hr.append([r["q"], r["key"], fm["nim"], mo["nim"] + bridge["nim"], fe.get("nim"), mo["nim"],
                   fm["ay"], mo["ay"] + bridge["ay"], fm["cof"], mo["cof"] + bridge["cof"]])
    s.append(md_table(["квартал", "ключевая", "ЧПМ упр.", "модель упр.", "ЧПМ МСФО", "модель МСФО", "дох. активов упр.", "модель", "ст. фонд. упр.", "модель"], hr))
    s.append("\nГоды 2019–2022 (МСФО, базис движка):\n")
    s.append(md_table(["год", "ЧПМ факт", "модель", "дох. активов факт", "модель", "ст. фонд. факт", "модель", "ЧПМ упр."],
                      [[a["y"], a["fact_eng"]["nim"], a["model"]["nim"], a["fact_eng"]["ay"], a["model"]["ay"],
                        a["fact_eng"]["cof"], a["model"]["cof"], a["fact_mgmt"]["nim"]] for a in ann]))
    s.append("\nОшибки (модель − факт), п.п.:\n")
    s.append(md_table(["ряд", "n", "средняя", "RMSE", "макс. |e|"],
                      [[k, v.get("n"), v.get("me"), v.get("rmse"), v.get("maxabs")] for k, v in res["history_errors"].items()]))
    T = res["transmission"]
    e = T["empirical"]
    s.append("\n## Передача ставки A-N3\n\n**Эмпирическая** (годовая МНК, упр. ЧПМ и ROE на среднюю ключевую):\n")
    er = []
    for k in ("nim_main", "nim_main_method_adj", "nim_2016plus", "nim_with_2014_15", "roe_main", "roe_2016plus", "roe_with_2014_15", "nim_quarterly_2019_2026"):
        v = e[k]
        er.append([k, v.get("n"), v["b"], v["se_b"], v["r2"], ",".join(str(y) for y in v.get("years", [])) if v.get("years") else f"{v.get('n_q')} кв."])
    s.append(md_table(["регрессия", "n", "наклон", "ст. ош.", "R²", "годы"], er))
    for k in ("nim_main", "roe_main"):
        for y, p in e[k]["shock_points"].items():
            s.append(f"- {k}: {y} — ключевая {p['key']:.2f}, факт {p['fact']}, линия {p['fit']:.2f}, отклонение {p['resid']:+.2f} п.п.\n")
    s.append(f"\nПересчёт ROE → ЧПМ: IEA/BV × (1 − τ) = {T['roe_factor_iea_bv_1mtax']:.2f}.\n\n**Механическая** (постоянные спреды, наклон активов 1):\n")
    s.append(md_table(["вариант", "T"], [[k, v] for k, v in T["mechanical"].items()]))
    s.append("\n**Раскрытая** (прим. о процентном риске, годовой ЧПД при +100 б.п.):\n")
    s.append(md_table(["дата", "ΔЧПД, млрд", "база", "IEA средн.", "T"], [[d["date"], d["dnii_100bp"], d["basis"], d["iea_avg"], d["T"]] for d in T["disclosed"]]))
    s.append(f"\n**φ по MODEL §4.5** (веса якоря; T(H | φ = 0) = {T['T_H_phi0']:.4f}; вес книг Φ = {T['phi_books_weight']:.3f}):\n")
    s.append(md_table(["T*", "φ", "T(N)", "T(M)", "п.п. ROE на 1 п.п."], [[p["T_star"], p["phi"], p["T_N"], p["T_M"], p["roe_pp_per_pp"]] for p in T["phi_table"]]))
    s.append("\n## Шок 2014 (книги 2019–2026, структура 31.12.2018)\n\n")
    s.append(md_table(["квартал", "ключевая", "ЧПМ модель МСФО", "ЧПМ упр. факт", "дох. акт. модель", "факт упр.", "ст. фонд. модель", "факт упр."],
                      [[r["q"], r["key"], r["nim"], r["nim_mgmt"], r["ay"], r["ay_mgmt"], r["cof"], r["cof_mgmt"]] for r in sh]))
    s.append("\n## Смягчение 2025–26: вне выборки (оценка по 2024К4)\n\n")
    s.append(md_table(["квартал", "ключевая", "ЧПМ упр. факт", "модель (вне выборки) упр.", "ЧПМ МСФО факт", "модель МСФО"],
                      [[r["q"], r["key"], r["fact_mgmt"]["nim"], r["model"]["nim"] + bridge["nim"], r["fact_eng"].get("nim"), r["model"]["nim"]]
                       for r in rows_oos if r["q"] >= "2024Q3"]))
    s.append("\n## Якорь 30.06.2026 (МСФО)\n\n")
    A = res["anchor"]
    s.append(md_table(["книга", "остаток, млрд"], [[k, v] for k, v in A["balances"].items()]))
    s.append("\nСтавки 2К26 (act/365): " + ", ".join(f"{k} {v:.2f}" for k, v in A["rates_ifrs_2Q26_pct"].items()) +
             f"; корп. кредиты {A['corp_loans_rate']:.2f}, розница всего {A['retail_all_rate']:.2f} (льготная {A['mortgage_sub_rate']:.2f}, рыночная {A['retail_market_rate']:.2f}).\n")
    T = res["transmission"]
    s.append("\n## Передача: локальная T (MODEL §4.5) и реализованная (стационарный ЧПМ M − N)\n\n")
    s.append(md_table(["T* (цель)", "φ для T(H) = T*", "реализованная при нём", "φ для реализованной = T*", "T(H) при нём"],
                      [[p_["T_star"], p_["phi"], p_["realized_M_N"], p_["phi_for_realized"], p_["T_H_at_phi_realized"]] for p_ in T["phi_table"]]))
    n0 = T["nss_phi0"]; a2 = T["A_N2_check"]
    s.append(f"\nСтационарный ЧПМ движка при φ = 0, σ0 = 0 (веса якоря, LT-ставки миров): N {n0['N']['nss']:.2f}, H {n0['H']['nss']:.2f}, "
             f"M {n0['M']['nss']:.2f} % (c* {n0['N']['c_star']:.3f} / {n0['H']['c_star']:.3f} / {n0['M']['c_star']:.3f}). "
             f"Цель A-N2 5,7 % упр. = {a2['target_eng']:.2f} % движка ⇒ σ0 = {a2['sigma0_pp']:+.2f} п.п. на кредитных книгах; "
             f"прочие процентные строки (аренда, ПФИ, короткие позиции) ≈ {a2['other_net_interest_pp_of_iea']:+.2f} п.п. IEA — в Nss не входят.\n")
    s.append("\n## Упрощённый прогон по мирам (структура якоря, ЧПМ движка, %)\n\n")
    fw = res["forward_nim_eng"]
    cols = ["2026Q3", "2026Q4", "2027", "2028", "2030", "2036", "FY2026_mgmt"]
    s.append(md_table(["вариант"] + cols, [[k] + [v.get(c) for c in cols] for k, v in fw.items()]))
    s.append("\nБлижние спреды по ставкам якоря 2К26, п.п.: " + ", ".join(f"{k} {v:+.2f}" for k, v in res["near_spreads_anchor_pp"].items()) + "\n")
    s.append("\nСпред активов в форме движка (мир H, s − (1 − a)·ref_H), п.п.:\n\n")
    sp = res["spread_H_engine_form_pp"]
    cols = ["anchor_2026Q2", "2026", "2027", "2028", "2029", "2030", "2031", "LT"]
    s.append(md_table(["книга"] + cols, [[b] + [v.get(c) for c in cols] for b, v in sp.items()]))
    with open(os.path.join(DATA, "RESULTS.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(s))


if __name__ == "__main__":
    main()
