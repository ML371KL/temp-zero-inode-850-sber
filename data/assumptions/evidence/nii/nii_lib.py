"""Данные и механика для калибровки книг ЧПД Сбера — перенос листа этапа 1 (calib-nii) в книгу 1.0.

Только стандартная библиотека. Все ставки внутри модуля — в процентах годовых (не доли);
в книгу они переводятся делением на 100 в calib_nii.py.

Источники (пути внутри inputs/ повторяют пути папки передачи; малые входы — evidence/extract_inputs.py):
  ключевая   — research/facts/cbr_key_rate_changes.csv (cbr.ru, 30.09.2026)
  ОФЗ        — stage1/calib/nii/data/ofz_quarterly.csv (MOEX ISS zcyc, fetch_zcyc.py)
  упр.       — stage1/mgmt/mgmt_kpi.csv (базис mgmt); 2014–2016 — research/facts/sber_facts.csv
  МСФО       — stage1/ifrs/ifrs_quarterly.csv (кварталы 2023–2026), ifrs_all.csv (годы 2018–2022,
               один документ на год), ifrs_history.csv (остатки)
Базисы не смешиваются: ставки книг — МСФО (базис движка); упр. ряды — отдельно и только
через явный мост (calib_nii.py §история).
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HANDOFF = os.path.join(HERE, "inputs")          # книга 1.0: корень путей — малые входы листа
P = lambda *a: os.path.join(HANDOFF, *a)  # noqa: E731


# ------------------------------------------------------------------ время
def q_label(y: int, k: int) -> str:
    return f"{y}Q{k}"


def q_parse(q: str) -> tuple[int, int]:
    return int(q[:4]), int(q[5])


def q_range(q0: str, q1: str) -> list[str]:
    y, k = q_parse(q0)
    out = []
    while True:
        lab = q_label(y, k)
        out.append(lab)
        if lab == q1:
            return out
        k += 1
        if k == 5:
            y, k = y + 1, 1


def q_start(q: str) -> dt.date:
    y, k = q_parse(q)
    return dt.date(y, 3 * (k - 1) + 1, 1)


def q_end(q: str) -> dt.date:
    y, k = q_parse(q)
    return (dt.date(y + 1, 1, 1) if k == 4 else dt.date(y, 3 * k + 1, 1)) - dt.timedelta(days=1)


def q_days(q: str) -> int:
    return (q_end(q) - q_start(q)).days + 1


def q_prev(q: str) -> str:
    y, k = q_parse(q)
    return q_label(y - 1, 4) if k == 1 else q_label(y, k - 1)


def ifrs_q(q: str) -> str:            # 2023Q1 -> 1Q2023 (метка ifrs_quarterly.csv)
    y, k = q_parse(q)
    return f"{k}Q{y}"


# ------------------------------------------------------------------ ключевая
def key_changes() -> list[tuple[dt.date, float]]:
    out = []
    with open(P("research", "facts", "cbr_key_rate_changes.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append((dt.date.fromisoformat(r["effective_date"]), float(r["key_rate_pct"])))
    return sorted(out)


def key_on(day: dt.date, ch=None) -> float:
    ch = ch or key_changes()
    v = ch[0][1]                     # 5,5 % действует с 13.09.2013 (введение ключевой)
    for d, r in ch:
        if d <= day:
            v = r
        else:
            break
    return v


def key_quarterly() -> dict[str, dict]:
    """Средняя по календарным дням (act/365) и значение на конец квартала."""
    ch = key_changes()
    out = {}
    for q in q_range("2013Q4", "2026Q3"):
        d, s, n = q_start(q), 0.0, 0
        while d <= q_end(q):
            s += key_on(d, ch)
            n += 1
            d += dt.timedelta(days=1)
        out[q] = {"avg": s / n, "eop": key_on(q_end(q), ch)}
    return out


# ------------------------------------------------------------------ ОФЗ
def ofz_quarterly() -> dict[str, dict]:
    out = {}
    path = P("stage1", "calib", "nii", "data", "ofz_quarterly.csv")
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["quarter"]] = {k: float(v) for k, v in r.items() if k.startswith("ofz_") and v}
    return out


# ------------------------------------------------------------------ упр. ряды
def mgmt_series() -> dict[str, dict[str, float]]:
    d: dict[str, dict[str, float]] = defaultdict(dict)
    with open(P("stage1", "mgmt", "mgmt_kpi.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d[r["metric"]][r["period"]] = float(r["value"])
    # 2014–2016 поквартально (базис mgmt, презентации 9М2015, 9М2016) — из фактов исследования
    with open(P("research", "facts", "sber_facts.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            per = r["period"]
            if r["metric"] in ("nim", "asset_yield", "cost_of_funding") and len(per) == 6 and per[1] == "Q" \
                    and per[2:4] == "20" and 2014 <= int(per[2:]) <= 2016:
                q = f"{per[2:]}Q{per[0]}"
                d[r["metric"]].setdefault(q, float(r["value"]))
    return d


# ------------------------------------------------------------------ МСФО: остатки
def ifrs_history() -> dict[str, dict[str, float]]:
    d: dict[str, dict[str, float]] = defaultdict(dict)
    with open(P("stage1", "ifrs", "ifrs_history.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                d[r["metric"]][r["period"]] = float(r["value"])
            except ValueError:
                pass
    return d


BAL_DATES = ["2018-12-31", "2019-12-31", "2020-12-31", "2021-12-31", "2022-12-31"] + \
    [q_end(q).isoformat() for q in q_range("2023Q1", "2026Q2")]


def _interp_deriv(h, date):
    """Требования по производным на дату (для обобщённых форм, где бумаги и ПФИ — одной строкой)."""
    pts = sorted((dt.date.fromisoformat(p), v) for p, v in h["deriv_assets"].items() if p[4] == "-")
    x = dt.date.fromisoformat(date)
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= x <= d1:
            return v0 + (v1 - v0) * (x - d0).days / (d1 - d0).days
    return pts[-1][1] if x > pts[-1][0] else pts[0][1]


def book_balances() -> dict[str, dict[str, float]]:
    """Остатки книг на отчётные даты (млрд ₽, МСФО). Ключ — дата конца периода."""
    h = ifrs_history()
    g = lambda m, p: h[m].get(p)  # noqa: E731
    out = {}
    for p in BAL_DATES:
        b = {}
        b["corp_loans"] = g("loans_ac_commercial_gross", p) + g("loans_ac_project_gross", p) + (g("loans_fvtpl", p) or 0.0)
        b["mortgage_all"] = g("loans_ac_mortgage_gross", p)
        b["retail_other"] = g("loans_ac_consumer_gross", p) + g("loans_ac_cards_gross", p) + g("loans_ac_auto_gross", p)
        b["retail_loans_all"] = b["mortgage_all"] + b["retail_other"]
        if g("securities", p) is not None:
            b["securities"] = g("securities", p) + (g("securities_repo", p) or 0.0)
            b["_sec_note"] = "бумаги + заложенные по репо"
        else:
            b["securities"] = g("securities_and_deriv", p) - _interp_deriv(h, p)
            b["_sec_note"] = "обобщённая форма: бумаги и ПФИ одной строкой минус ПФИ (интерполяция)"
        b["liquidity"] = g("cash", p) + g("mandatory_reserves", p) + g("due_from_banks", p)
        b["retail_current"] = g("dep_ind_current", p)
        b["retail_term"] = g("deposits_individuals", p) - g("dep_ind_current", p)
        b["retail_funds"] = g("deposits_individuals", p)
        b["corp_funds"] = g("deposits_corporates", p)
        b["wholesale"] = g("due_to_banks", p) + g("debt_securities_issued", p) + g("sub_debt", p) + (g("other_borrowed", p) or 0.0)
        b["equity"] = g("eq_total", p)
        b["total_assets"] = g("total_assets", p)
        b["loans_ac_ecl"] = g("loans_ac_total_ecl", p)
        b["iea"] = b["corp_loans"] + b["retail_loans_all"] + b["securities"] + b["liquidity"]
        b["ibl"] = b["retail_current"] + b["retail_term"] + b["corp_funds"] + b["wholesale"]
        out[p] = b
    return out


EQUITY_METRICS = ("sec_fvtplm_equities", "sec_fvtplm_funds", "sec_fvoci_equity")


def equities_and_funds() -> dict[str, float]:
    """Акции и паи фондов внутри «бумаг» на отчётные даты (млрд ₽, МСФО): ядро и факты считают книгу бумаг только из
    долговых, акции и фонды — в прочих активах. На датах с обобщённой формой — линейная интерполяция по дням между
    датами с разбивкой (как ПФИ)."""
    h = ifrs_history()
    pts = sorted((dt.date.fromisoformat(p), sum(h[m][p] for m in EQUITY_METRICS))
                 for p in h[EQUITY_METRICS[0]] if all(p in h[m] for m in EQUITY_METRICS))
    out = {}
    for p in BAL_DATES:
        x = dt.date.fromisoformat(p)
        hit = [v for d, v in pts if d == x]
        if hit:
            out[p] = hit[0]
            continue
        (d0, v0), (d1, v1) = next((a, b) for a, b in zip(pts, pts[1:]) if a[0] < x < b[0])
        out[p] = v0 + (v1 - v0) * (x - d0).days / (d1 - d0).days
    return out


def quarter_balances(q0="2018Q4", q1="2026Q2") -> dict[str, dict[str, float]]:
    """Остатки на конец каждого квартала: факт с 2023К1; 2019–2022 — геометрическая
    интерполяция между концами лет (помечено `_interp`)."""
    yb = book_balances()
    keys = [k for k in yb["2018-12-31"] if not k.startswith("_")]
    out = {}
    for q in q_range(q0, q1):
        e = q_end(q).isoformat()
        if e in yb:
            out[q] = dict(yb[e]); out[q]["_interp"] = False
            continue
        y, k = q_parse(q)
        a, b = yb[f"{y - 1}-12-31"], yb[f"{y}-12-31"]
        w = k / 4
        out[q] = {kk: a[kk] * (b[kk] / a[kk]) ** w if a[kk] > 0 and b[kk] > 0 else a[kk] + (b[kk] - a[kk]) * w
                  for kk in keys}
        out[q]["_interp"] = True
        out[q]["_sec_note"] = "интерполяция"
    return out


# ------------------------------------------------------------------ МСФО: процентные строки по книгам
# Доли, которыми делятся «свёрнутые» строки обобщённых форм, — по полной разбивке FY2023
# (сравнительные данные отчётности FY2024): бумаги СС 65,5 / банки СС 65,3 / ПФИ 4,7 из 135,5;
# расходы: банки СС 44,4 / ПФИ 6,1 / срочные СС 5,7 / короткие позиции 7,7 из 63,9.
LUMP_II = {"securities": 65.5 / 135.5, "liquidity": 65.3 / 135.5, "other": 4.7 / 135.5}
LUMP_IE = {"wholesale": 44.4 / 63.9, "other": (6.1 + 5.7 + 7.7) / 63.9}

YEAR_DOC = {  # один документ на год (согласованные строки): FY2018–19 — FY2019; FY2020 — FY2021;
    "FY2018": "ifrs_FY2019_statements.pdf",       # FY2021 — FY2022 (без прекращённой деятельности);
    "FY2019": "ifrs_FY2019_statements.pdf",       # FY2022 — FY2023 (после МСФО 17, как 2023+)
    "FY2020": "ifrs_FY2021_statements.pdf",
    "FY2021": "FY2022_ifrs_summary_statements.pdf",
    "FY2022": "FY2023_ifrs_summary_statements.pdf",
}


def _flows_from(get) -> dict[str, float]:
    """get(metric) -> значение или None. Процентные доходы (+) и расходы (+ как затраты) по книгам."""
    v = lambda m: get(m) or 0.0  # noqa: E731
    f = defaultdict(float)
    f["loans"] = v("ii_ac_loans") + v("ii_oth_loans_fvtpl")
    f["securities"] = v("ii_ac_debt_securities") + v("ii_fvoci_debt_securities") + v("ii_oth_securities_fvtpl")
    f["liquidity"] = v("ii_ac_banks") + v("ii_ac_cash") + v("ii_ac_other") + v("ii_oth_banks_fvtpl")
    f["other_ii"] = v("ii_oth_derivatives")
    lump = get("ii_oth_other_fa_fvtpl")
    if lump is not None and get("ii_oth_securities_fvtpl") is None:
        for k, w in LUMP_II.items():
            f[k if k != "other" else "other_ii"] += lump * w
    f["retail_current"] = -v("ie_ac_current_individuals")
    f["retail_term"] = -v("ie_ac_term_individuals")
    f["corp_funds"] = -(v("ie_ac_term_corporates") + v("ie_ac_current_corporates"))
    # 1К–3К2024 есть и свёрнутые строки (отчётность 2024), и полные (сравнительные 2025): свёрнутые
    # «выпущенные + субординированные» и «прочие» (= аренда + прочие) берём, только если полных нет.
    lump_debt = v("ie_ac_debt_issued_and_sub") if get("ie_ac_debt_issued") is None else 0.0
    lump_oth = v("ie_ac_other") if get("ie_ac_lease") is None or get("ie_ac_debt_issued_and_sub") is None else 0.0
    f["wholesale"] = -(v("ie_ac_banks") + v("ie_ac_debt_issued") + v("ie_ac_sub_debt") + lump_debt
                       + v("ie_ac_other_borrowed") + v("ie_oth_banks_fvtpl"))
    f["other_ie"] = -(v("ie_ac_lease") + lump_oth + v("ie_oth_derivatives") + v("ie_oth_term_fvtpl")
                      + v("ie_oth_short_positions") + v("ie_oth_other"))
    lump = get("ie_oth_fl_fvtpl")
    if lump is not None and get("ie_oth_banks_fvtpl") is None:
        f["wholesale"] += -lump * LUMP_IE["wholesale"]
        f["other_ie"] += -lump * LUMP_IE["other"]
    f["dia"] = -v("ie_deposit_insurance")
    f["nii_reported"] = v("nii")
    f["nii_books"] = (f["loans"] + f["securities"] + f["liquidity"] + f["other_ii"]
                      - f["retail_current"] - f["retail_term"] - f["corp_funds"] - f["wholesale"] - f["other_ie"] - f["dia"])
    return dict(f)


def ifrs_flows_quarterly() -> dict[str, dict[str, float]]:
    d: dict[str, dict[str, float]] = defaultdict(dict)
    with open(P("stage1", "ifrs", "ifrs_quarterly.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d[r["period"]][r["metric"]] = float(r["value"])
    return {q: _flows_from(lambda m, q=q: d[ifrs_q(q)].get(m)) for q in q_range("2023Q1", "2026Q2")}


def ifrs_flows_annual() -> dict[str, dict[str, float]]:
    d: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    with open(P("stage1", "ifrs", "ifrs_all.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["period"] in YEAR_DOC and r["doc"].endswith(YEAR_DOC[r["period"]]):
                d[r["period"]][r["metric"]] = float(r["value"])
    out = {y: _flows_from(lambda m, y=y: d[y].get(m)) for y in YEAR_DOC}
    # 2021–2022: свёрнутые строки обобщённых форм делятся по полной разбивке FY2023 (LUMP_II / LUMP_IE)
    return out


# ------------------------------------------------------------------ частичная подстройка: оценка
def rho_seq(ref: list[float], rho, rho_down=None) -> list[float]:
    """Скорость подстройки по кварталам: одна ρ или (асимметрия) ρ при росте опоры / rho_down при снижении."""
    if rho_down is None:
        return [rho] * len(ref)
    return [rho] + [rho if ref[t] > ref[t - 1] else rho_down for t in range(1, len(ref))]


def simulate(ref: list[float], rho, a: float, s: float, y0: float, rho_down=None) -> list[float]:
    """y_t = y_{t-1} + rho*(a*ref_t + s - y_{t-1}); y[0] = y0 (состояние первого квартала выборки)."""
    rs = rho_seq(ref, rho, rho_down)
    y = [y0]
    for t in range(1, len(ref)):
        y.append(y[-1] + rs[t] * (a * ref[t] + s - y[-1]))
    return y


def _basis(ref, rho, rho_down=None):
    """Столбцы линейной формы пути при данном rho: y_t = y0*B0_t + a*B1_t + s*B2_t."""
    n = len(ref)
    rs = rho_seq(ref, rho, rho_down)
    b0, b1, b2 = [1.0], [0.0], [0.0]
    for t in range(1, n):
        r = rs[t]
        b0.append((1 - r) * b0[-1])
        b1.append((1 - r) * b1[-1] + r * ref[t])
        b2.append((1 - r) * b2[-1] + r)
    return b0, b1, b2


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_partial_adjustment(ref, obs, *, a_fixed=None, y0_fixed=None, s_fixed=None,
                           rho_grid=None, rho_fixed=None, asym=False, a_bounds=None):
    """Взвешенный МНК по свободной траектории.

    obs — список (веса по кварталам, значение) наблюдений: каждое наблюдение —
          ({t: вес_t}, y_obs, w) — среднее модели с весами вес_t сравнивается с y_obs, вес w
          (квартальное: {t: 1}; годовое: {t1..t4: остатки×дни}).
    Параметры: rho (сетка), a (наклон к опоре; для пассивов — β), s (спред), y0 (начальное состояние).
    Возвращает dict с оценками, SSE, RMSE по наблюдениям и стандартными ошибками (линейная часть).
    """
    if a_bounds is not None and a_fixed is None:
        # наклон вне экономических границ — фиксируем на границе и переоцениваем остальное
        f = fit_partial_adjustment(ref, obs, y0_fixed=y0_fixed, s_fixed=s_fixed, rho_grid=rho_grid,
                                   rho_fixed=rho_fixed, asym=asym)
        if a_bounds[0] <= f["a"] <= a_bounds[1]:
            f["a_bound"] = None
            return f
        ab = min(max(f["a"], a_bounds[0]), a_bounds[1])
        g = fit_partial_adjustment(ref, obs, a_fixed=ab, y0_fixed=y0_fixed, s_fixed=s_fixed, rho_grid=rho_grid,
                                   rho_fixed=rho_fixed, asym=asym)
        g["a_bound"] = ab
        return g
    base = [rho_fixed] if rho_fixed is not None else (rho_grid or [i / 100 for i in range(2, 101)])
    grid = [(r, None) for r in base] if not asym else [(ru / 50, rd / 50) for ru in range(1, 51) for rd in range(1, 51)]
    best = None
    for rho, rho_dn in grid:
        b0, b1, b2 = _basis(ref, rho, rho_dn)
        cols, names = [], []
        # начальное состояние не влияет на наблюдения (ρ = 1 или наблюдения далеко) — не оцениваем
        y0_free = y0_fixed is None and max(abs(sum(b0[t] * w for t, w in o[0].items())) for o in obs) > 1e-9
        if y0_free:
            cols.append(b0); names.append("y0")
        if a_fixed is None:
            cols.append(b1); names.append("a")
        if s_fixed is None:
            cols.append(b2); names.append("s")
        rows, ys, ws = [], [], []
        for wmap, yv, w in obs:
            tot = sum(wmap.values())
            agg = lambda col: sum(col[t] * wt for t, wt in wmap.items()) / tot  # noqa: E731
            off = 0.0
            if y0_fixed is not None:
                off += y0_fixed * agg(b0)
            if a_fixed is not None:
                off += a_fixed * agg(b1)
            if s_fixed is not None:
                off += s_fixed * agg(b2)
            rows.append([agg(c) for c in cols]); ys.append(yv - off); ws.append(w)
        k = len(cols)
        A = [[sum(ws[i] * rows[i][p] * rows[i][q] for i in range(len(rows))) for q in range(k)] for p in range(k)]
        bb = [sum(ws[i] * rows[i][p] * ys[i] for i in range(len(rows))) for p in range(k)]
        sol = _solve(A, bb) if k else []
        if sol is None:
            continue
        res = [ys[i] - sum(rows[i][p] * sol[p] for p in range(k)) for i in range(len(rows))]
        sse = sum(ws[i] * res[i] ** 2 for i in range(len(rows)))
        if best is None or sse < best["sse"] - 1e-12:
            best = {"rho": rho, "rho_down": rho_dn, "sse": sse, "sol": dict(zip(names, sol)), "A": A, "res": res,
                    "ws": ws, "k": k}
    if best is None:
        raise RuntimeError("оценка не сошлась")
    sol = best["sol"]
    out = {"rho": best["rho"], "rho_down": best["rho_down"],
           "a": sol.get("a", a_fixed), "s": sol.get("s", s_fixed), "y0": sol.get("y0", y0_fixed)}
    if out["y0"] is None:                      # не идентифицировано — стационарное значение при ref[0]
        out["y0"] = out["a"] * ref[0] + out["s"]
    n = len(obs)
    wsum = sum(best["ws"])
    dof = max(1, n - best["k"] - (0 if rho_fixed is not None else (2 if asym else 1)))
    sigma2 = best["sse"] / dof
    out["rmse_w"] = math.sqrt(best["sse"] / wsum)
    out["n_obs"] = n
    out["dof"] = dof
    # стандартные ошибки линейной части (условно по rho): sigma2 * (X'WX)^-1
    k = best["k"]
    se = {}
    if k:
        inv = []
        for j in range(k):
            e = [1.0 if i == j else 0.0 for i in range(k)]
            inv.append(_solve(best["A"], e))
        for j, nm in enumerate(sol):
            se[nm] = math.sqrt(max(0.0, sigma2 * inv[j][j]))
    out["se"] = se
    # профиль SSE по rho для доверительного интервала (ΔSSE ≤ sigma2 * 3,84 ≈ 95 %)
    out["sigma2"] = sigma2
    return out


def rho_profile(ref, obs, **kw):
    prof = []
    for i in range(2, 101):
        r = i / 100
        try:
            f = fit_partial_adjustment(ref, obs, rho_fixed=r, **kw)
            prof.append((r, f))
        except RuntimeError:
            pass
    return prof


def ols(x: list[float], y: list[float]) -> dict:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    b = sxy / sxx
    a = my - b * mx
    res = [yy - a - b * xx for xx, yy in zip(x, y)]
    sse = sum(r * r for r in res)
    s2 = sse / (n - 2) if n > 2 else float("nan")
    se_b = math.sqrt(s2 / sxx) if n > 2 else float("nan")
    syy = sum((b_ - my) ** 2 for b_ in y)
    return {"a": a, "b": b, "se_b": se_b, "r2": 1 - sse / syy if syy else float("nan"), "n": n, "res": res}
