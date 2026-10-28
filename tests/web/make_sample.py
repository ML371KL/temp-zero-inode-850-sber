"""Синтетический выпуск sber-v1 для витрины: tests/fixtures/payload-sample.json.

Все блоки и поля docs/PAYLOAD.md §2 (контракт после волны W3) с реалистичными
порядками (цена ≈273 ₽, медиана ≈370 ₽), но ЧИСЛА ВЫДУМАНЫ — это не оценка.
Тождества, которые сверяет validate (П§7), выполнены: заголовок и строки
by_lambda — правилом λ по прогонам, печать половиной вверх, мост и водопад
сходятся, вероятности в сумме 1, дерево ROE, мост норматива, хэш и размер
выпуска (П§0.4), строки года сходятся к прибыли до налога, год нау-каста — на
одном слое. Формы узлов — как у выпуска ядра: коды политики, периоды месячных
плиток, единицы плиток кодами П§0.2, сверка с контрольной моделью в единицах
строки, снимки гайденса по датам, провалы истории по базисам; печатаемые
строки — словами (П§0.2).

    python tests/web/make_sample.py            # перезаписать фикстуру
    python tests/web/make_sample.py --check    # сверить файл с генератором
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "payload-sample.json"

RNG = random.Random(850)

# ── константы синтетического выпуска ──
TICKERS = ["SBER", "SBERP"]
MAIN = "SBER"
PRICE = {"SBER": 272.87, "SBERP": 273.77}
N_ISS = 22586.948
BY_TICKER = {"SBER": {"issued_mln": 21586.948, "treasury_mln": 209.9},
             "SBERP": {"issued_mln": 1000.0, "treasury_mln": 34.5}}
for _t in BY_TICKER.values():
    _t["outstanding_mln"] = round(_t["issued_mln"] - _t["treasury_mln"], 3)
N_OUT = round(sum(v["outstanding_mln"] for v in BY_TICKER.values()), 3)
VDATE = "2026-09-30"
FACTS_DATE = "2026-06-30"
LAM = 0.5
STEP = 5.0
DRAWS = 2000
YEARS = list(range(2026, 2037))
WORLDS = ["N", "H", "M"]
REGIMES = ["soft", "norm", "downturn", "crisis"]
SCEN = ["schedule", "upper", "strict"]
WORLD_NAMES = {"N": "Нормализация", "H": "Высокие ставки надолго", "M": "Рыночный как есть"}
REGIME_NAMES = {"soft": "Мягкая посадка", "norm": "Нормализация", "downturn": "Корпоративный спад", "crisis": "Кризис"}
SCEN_NAMES = {"schedule": "По графику", "upper": "Верхняя группа СЗКО", "strict": "Жёсткий"}
W_A = {"N": 0.35, "H": 0.45, "M": 0.20}
W_MI = {"N": 0.10, "H": 0.25, "M": 0.65}
W_N = {"N": 0.0, "H": 0.0, "M": 1.0}
P_REG = {"soft": 0.15, "norm": 0.40, "downturn": 0.30, "crisis": 0.15}
P_SC = {r: {"schedule": 0.25, "upper": 0.45, "strict": 0.30} for r in REGIMES}
P_SC["crisis"] = {"schedule": 0.70, "upper": 0.25, "strict": 0.05}
BV_V = 8120.4
GOV = 0.004             # дисконт за управление — доля V0 (В16)
BOOK = "1.3"
BOOK_PREV = "1.2"
DPS_POLICY = 38.95      # DPS политики за год якоря на прибыли смеси заголовка
P_CANCEL = 0.15         # масса клеток «свой взгляд», где выплата за год отменена кризисом


def hup(x: float, step: float) -> float:
    """Печать половиной вверх к шагу (как ядро: Decimal ROUND_HALF_UP)."""
    q = (Decimal(repr(x)) / Decimal(repr(step))).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return float(q * Decimal(repr(step)))


def r(x, nd=2):
    return None if x is None else round(float(x), nd)


def q7(sorted_vals, q):
    n = len(sorted_vals)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def yearly(vals):
    return [{"year": y, "value": r(v, 6)} for y, v in zip(YEARS, vals)]


def ramp(a, b, n=len(YEARS), k=4):
    """Сход от a к b за k лет, дальше — b."""
    return [a + (b - a) * min(1.0, i / k) for i in range(n)]


def price_of(v0, bridge=0.0):
    return (v0 * (1 - GOV) + bridge) * 1000 / N_OUT


# ── сетка ──
PB_W = {"N": 1.38, "H": 1.027, "M": 0.999}
F_R = {"soft": 1.14, "norm": 1.05, "downturn": 0.93, "crisis": 0.74}
F_S = {"schedule": 1.03, "upper": 1.0, "strict": 0.965}
BASE_N20 = [0.1402, 0.1388, 0.1379, 0.1372, 0.1368, 0.1366, 0.1365, 0.1364, 0.1364, 0.1363, 0.1363]   # Н20.0 на конец года, общий уровень
ROE_T_W = {"N": 0.188, "H": 0.192, "M": 0.221}
K_T_W = {"N": 0.136, "H": 0.173, "M": 0.213}
G_T_W = {"N": 0.055, "H": 0.070, "M": 0.113}


def make_cells():
    cells = []
    for w in WORLDS:
        for rg in REGIMES:
            for s in SCEN:
                pb = PB_W[w] * F_R[rg] * F_S[s]
                v = BV_V * pb
                excess = v - BV_V
                pv_ri = excess * (0.46 if rg != "crisis" else 0.30) + (60 if rg == "soft" else 0)
                crisis = rg == "crisis"
                cut = rg == "downturn" and s == "strict" and w != "N"
                gap = crisis and s != "schedule" and w != "N"
                roe_t = ROE_T_W[w] + {"soft": 0.012, "norm": 0.0, "downturn": -0.012, "crisis": -0.03}[rg]
                n20 = [BASE_N20[i] + {"soft": 0.004, "norm": 0.0, "downturn": -0.006, "crisis": -0.02}[rg] * (0.2 if i == 0 else 1 if i <= 3 else 0.4)
                       + {"schedule": 0.002, "upper": 0.0, "strict": -0.0025}[s] * min(1, i / 2) - (0.015 if gap and i == 1 else 0)
                       for i in range(len(YEARS))]
                n20_min = min(n20)
                flags = []
                if gap:
                    flags.append("capital_gap")
                if roe_t < K_T_W[w]:
                    flags.append("roe_below_k")
                if cut:
                    flags.append("dividend_cut")
                if crisis:
                    flags.append("crisis_skip")
                ni = [1756 * (1 + 0.06 * i) * F_R[rg] ** (0.7 if i > 0 else 0.1) * (0.55 if crisis and i == 1 else 1)
                      * (1 + (0.02 if w == "M" else -0.01 if w == "N" else 0) * i) for i in range(len(YEARS))]
                bv = [8950 * (1 + 0.08 * i) for i in range(len(YEARS))]
                cor = [0.0138 + {"soft": -0.002, "norm": 0.0, "downturn": 0.004, "crisis": 0.009}[rg] * (1 if i < 3 else 0.35)
                       + (0.022 if crisis and i == 1 else 0) for i in range(len(YEARS))]
                nim = [0.0587 - (0.004 if crisis and i == 1 else 0) + {"N": -0.002, "H": 0.0, "M": 0.003}[w] * min(1, i / 3)
                       for i in range(len(YEARS))]
                n11 = [x - 0.018 for x in n20]
                dps = [38.6 * (1 + 0.06 * i) * F_R[rg] ** 0.8 * (0 if crisis and i == 0 else 1) for i in range(len(YEARS))]
                dps[-1] = None                                   # ГОСА после last_period
                payout = [0.5 if d else (None if d is None else 0.0) for d in dps]
                cells.append({
                    "world": w, "regime": rg, "scenario": s,
                    "p_analytical": r(W_A[w] * P_REG[rg] * P_SC[rg][s], 6),
                    "p_market_implied": r(W_MI[w] * P_REG[rg] * P_SC[rg][s], 6),
                    "p_neutral": r(W_N[w] * P_REG[rg] * P_SC[rg][s], 6),
                    "v": r(v), "bv_v": BV_V, "price": r(price_of(v)), "pb": r(pb, 4),
                    "pv_ri_explicit": r(pv_ri), "terminal_share": r(0.52 + 0.04 * (PB_W[w] - 1), 4),
                    "roe_t": r(roe_t, 4), "k_t": K_T_W[w], "g_t": G_T_W[w], "x_t": r(120 + 40 * (PB_W[w] - 1)),
                    "n20_min": r(n20_min, 4), "n11_min": r(n20_min - 0.018, 4),
                    "gap_period": "2027Q2" if gap else None,
                    "dps_first": r(dps[0]), "flags": flags,
                    "annual": {"ni_sh": [r(x, 1) for x in ni], "roe": [r(x / b, 4) for x, b in zip(ni, bv)],
                               "cor": [r(x, 4) for x in cor], "nim": [r(x, 4) for x in nim],
                               "cir": [r(0.305 + 0.004 * min(i, 5), 4) for i in range(len(YEARS))],
                               "n20": [r(x, 4) for x in n20], "n11": [r(x, 4) for x in n11],
                               "dps": [r(x) for x in dps], "payout": payout},
                })
    # нормировка вероятностей до точной суммы 1 (последняя клетка слоя — остаток)
    for key in ("p_analytical", "p_market_implied", "p_neutral"):
        total = sum(c[key] for c in cells)
        last = max(range(len(cells)), key=lambda i: cells[i][key])
        cells[last][key] = r(cells[last][key] + 1 - total, 6)
    return cells


def layer(cells, key, title, weights):
    v0 = sum(c[key] * c["v"] for c in cells)
    pvri = sum(c[key] * c["pv_ri_explicit"] for c in cells)
    pvt = v0 - BV_V - pvri
    roe = sum(c[key] * c["roe_t"] for c in cells)
    k = sum(c[key] * c["k_t"] for c in cells)
    return {
        "title": title, "world_weights": weights, "v0": r(v0), "bv_v": BV_V, "price": r(price_of(v0)),
        "pb": r(v0 / BV_V, 4), "excess": r(v0 - BV_V), "pv_ri_explicit": r(pvri), "pv_terminal": r(v0 - BV_V - r(pvri)),
        "terminal_share": r(sum(c[key] * c["terminal_share"] for c in cells), 4),
        "roe_tc": r(roe, 4), "k_tc": r(k, 4),
        "p_price_below_market": r(sum(c[key] for c in cells if c["price"] < PRICE[MAIN]), 6),
        "p_roe_below_k": r(sum(c[key] for c in cells if c["roe_t"] < c["k_t"]), 6),
        "capital_gap_mass": r(sum(c[key] for c in cells if "capital_gap" in c["flags"]), 6),
        "_pvt": pvt,
    }


# ── прогоны полосы ──
def make_draws(low_c, high_c):
    """Низ и верх прогонов: медиана центра при λ книги ≈370 ₽, точка ≈ книги выше медианы."""
    lows, highs = [], []
    for _ in range(DRAWS):
        z = RNG.gauss(0, 1)
        c = 372 + 58 * z - 6 * z * z
        d = (high_c - low_c) * (1 + 0.18 * RNG.gauss(0, 1))
        lo = max(40.0, c - d / 2)
        hi = max(40.0, c + d / 2)
        lows.append(hup(lo, 0.1))
        highs.append(hup(hi, 0.1))
    return lows, highs


def stats_at(lows, highs, lam):
    c = sorted(lo + lam * (hi - lo) for lo, hi in zip(lows, highs))
    q = lambda p: q7(c, p)  # noqa: E731
    return {"median": q(0.5), "p10": q(0.1), "p25": q(0.25), "p75": q(0.75), "p90": q(0.9),
            "mean": sum(c) / len(c),
            "p_below": {t: sum(1 for x in c if x < PRICE[t]) / len(c) for t in TICKERS},
            "pct": sum(1 for x in c if x <= PRICE[MAIN]) / len(c)}


def build():
    cells = make_cells()
    la = layer(cells, "p_analytical", "свой макро-взгляд", W_A)
    lm = layer(cells, "p_market_implied", "вменённые рынком", W_MI)
    ln = layer(cells, "p_neutral", "рыночные ставки как есть", W_N)
    low_p, high_p = ln["price"], la["price"]
    central = low_p + LAM * (high_p - low_p)
    lows, highs = make_draws(low_p, high_p)
    head_s = stats_at(lows, highs, LAM)
    median = head_s["median"]
    cap_by = {t: r(PRICE[t] * BY_TICKER[t]["outstanding_mln"] / 1000) for t in TICKERS}
    cap = r(sum(cap_by.values()))

    # ── мост и смесь заголовка ──
    div_rows = [{"year": 2025, "dps": 37.64, "amount": r(37.64 * N_OUT / 1000), "deducted_on": FACTS_DATE,
                 "last_buy_date": "2026-07-17", "ex_date": "2026-07-20", "sign": 0}]
    bridge_amount = r(sum(x["amount"] * x["sign"] for x in div_rows))
    v0_mix = ln["v0"] + LAM * (la["v0"] - ln["v0"])
    pvri_mix = ln["pv_ri_explicit"] + LAM * (la["pv_ri_explicit"] - ln["pv_ri_explicit"])
    pvt_mix = v0_mix - BV_V - pvri_mix
    gov_amount = -GOV * v0_mix
    equity = v0_mix + gov_amount + bridge_amount
    per = lambda x: r(x * 1000 / N_OUT)  # noqa: E731
    waterfall = [
        {"key": "bv_v", "title": "Капитал на дату оценки", "amount": BV_V, "per_share": per(BV_V), "total": False},
        {"key": "pv_ri_explicit", "title": "PV доходности сверх стоимости капитала, явный участок", "amount": r(pvri_mix), "per_share": per(pvri_mix), "total": False},
        {"key": "pv_terminal", "title": "PV терминала (остаточный доход)", "amount": r(pvt_mix), "per_share": per(pvt_mix), "total": False},
        {"key": "v0", "title": "Оценка капитала V0", "amount": r(v0_mix), "per_share": per(v0_mix), "total": True},
        {"key": "governance", "title": "Дисконт за управление", "amount": r(gov_amount), "per_share": per(gov_amount), "total": False},
        {"key": "bridge", "title": "Объявленный дивиденд до отсечки", "amount": bridge_amount, "per_share": per(bridge_amount), "total": False},
        {"key": "equity", "title": "Капитал акционеров по модели", "amount": r(equity), "per_share": per(equity), "total": True},
    ]
    headline_mix = {"title": "смесь заголовка: λ × «свой взгляд» + (1 − λ) × «рыночные ставки»", "lambda": LAM,
                    "v0": r(v0_mix), "bv_v": BV_V, "pv_ri_explicit": r(pvri_mix), "pv_terminal": r(pvt_mix),
                    "governance": r(gov_amount), "bridge": bridge_amount, "price": r(central), "waterfall": waterfall}
    for L in (la, lm, ln):
        L.pop("_pvt")

    # ── заголовок и таблица λ ──
    def headline_row(lam):
        s = stats_at(lows, highs, lam)
        point = low_p + lam * (high_p - low_p)
        return {"lambda": round(lam, 2), "median": r(s["median"]), "p10": r(s["p10"]), "p25": r(s["p25"]),
                "p75": r(s["p75"]), "p90": r(s["p90"]), "mean": r(s["mean"]), "point": r(point),
                "p_below_market": r(s["p_below"][MAIN], 6), "p_below_by_ticker": {t: r(s["p_below"][t], 6) for t in TICKERS},
                "market_percentile": r(s["pct"], 6), "upside": {t: r(s["median"] / PRICE[t] - 1, 6) for t in TICKERS}}

    by_lambda = [headline_row(i / 20) for i in range(21)]
    book_row = by_lambda[10]
    contributions = []
    # все оси книги — в полосе (В1): 21 скалярная ось с вкладом (шесть последних — меньше 1 %) и две оси-словаря без вклада
    shares = [0.262, 0.168, 0.116, 0.092, 0.07, 0.054, 0.042, 0.035, 0.028, 0.024, 0.02, 0.017, 0.014, 0.012, 0.011,
              0.009, 0.008, 0.007, 0.006, 0.005, 0.004]
    judg = judgement_axes()
    scalar = [j for j in judg if j["kind"] != "dict"]
    for share, j in zip(shares, scalar):
        contributions.append({"axis": j["id"], "name": j["name"], "share": r(share / sum(shares), 6),
                              "rank_corr": r(math.copysign(math.sqrt(share * 0.93), j["sign"]), 3),
                              "paths": j["paths"], "judgement_key": j["id"]})
    contributions[0]["share"] = r(1 - sum(c["share"] for c in contributions[1:]), 6)
    share_of = {c["axis"]: c for c in contributions}

    head = {
        "median": book_row["median"], "printed_median": hup(book_row["median"], STEP),
        "band80": [book_row["p10"], book_row["p90"]], "printed_band80": [hup(book_row["p10"], STEP), hup(book_row["p90"], STEP)],
        "band50": [book_row["p25"], book_row["p75"]], "printed_band50": [hup(book_row["p25"], STEP), hup(book_row["p75"], STEP)],
        "mean": book_row["mean"], "point": r(central), "printed_point": hup(r(central), STEP),
        "market": PRICE[MAIN], "market_by_ticker": dict(PRICE),
        "p_below_market": book_row["p_below_market"], "p_below_by_ticker": book_row["p_below_by_ticker"],
        "market_percentile": book_row["market_percentile"], "upside": book_row["upside"],
        "draws": DRAWS, "seed": 20260930, "quantiles": [0.1, 0.25, 0.5, 0.75, 0.9],
        "own_macro_confidence": LAM, "lambda_step": 0.05, "print_step": STEP, "ceiling_x_market": 3.0,
        "low_draws": lows, "high_draws": highs, "contributions": contributions, "by_lambda": by_lambda,
    }

    # ── язык банка ──
    def first_line(lam):
        s = stats_at(lows, highs, lam)
        point = low_p + lam * (high_p - low_p)
        v_point = point * N_OUT / 1000
        v_med = (s["median"] * N_OUT / 1000 - bridge_amount) / (1 - GOV)
        return {"lambda": round(lam, 2), "bv_v": BV_V, "v_point": r(v_point), "v_median": r(v_med),
                "fair_pb_point": r(v_point / BV_V, 4), "fair_pb_median": r(v_med / BV_V, 4),
                "market_pb": r(cap / BV_V, 4), "excess_point": r(v_point - BV_V), "excess_median": r(v_med - BV_V),
                "excess_market": r(cap - BV_V), "roe_tc": r(ln["roe_tc"] + lam * (la["roe_tc"] - ln["roe_tc"]), 4),
                "k_tc": r(ln["k_tc"] + lam * (la["k_tc"] - ln["k_tc"]), 4),
                "rub_per_1pp_roe": r(23.8 + 4 * lam, 2), "rub_per_01pp_cor": r(-4.1 - 0.6 * lam, 2),
                "rub_per_01pp_nim": r(5.2 + 0.8 * lam, 2)}

    bfl_rows = [first_line(i / 20) for i in range(21)]
    bfl = dict(bfl_rows[10])
    bfl.pop("lambda")
    bfl.update({"target": "median", "roe_ltm": 0.2254,
                "sensitivity_basis": "сдвиг путей CoR всех режимов на 0,1 п.п. (упр. базис, мостом в движок); ЧПМ сквозь цикл (упр.) на 0,1 п.п.; ROE — через строку ЧПМ",
                "layers": {k: {"bv_v": BV_V, "v0": L["v0"], "fair_pb": L["pb"], "excess": L["excess"], "roe_tc": L["roe_tc"], "k_tc": L["k_tc"]}
                           for k, L in (("analytical", la), ("macro_neutral", ln))},
                "by_lambda": bfl_rows})

    by_world = {}
    for w in WORLDS:
        ws = [c for c in cells if c["world"] == w]
        v0w = sum(P_REG[c["regime"]] * P_SC[c["regime"]][c["scenario"]] * c["v"] for c in ws)
        by_world[w] = {"price": r(price_of(v0w)), "v0": r(v0w), "bv_v": BV_V, "pb": r(v0w / BV_V, 4)}

    fair_value = {
        "method": "judgement_median", "target": "median", "headline": head,
        "low": r(low_p), "central": r(central), "high": r(high_p),
        "printed_low": hup(r(low_p), STEP), "printed_central": hup(r(central), STEP), "printed_high": hup(r(high_p), STEP),
        "own_macro_confidence": LAM,
        "rates_view": {"low": r(low_p), "high": r(high_p), "rub": r(r(high_p) - r(low_p))},
        "bank_first_line": bfl,
        "bridge": {"amount": bridge_amount, "per_share": per(bridge_amount), "governance_applies": False, "pending_dividend": 0.0, "rows": div_rows},
        "by_world": by_world,
        "jump_guard": {"previous_median": r(median - 3.4), "exdate_adjustment": 0.0, "median_change": r(3.4 / (median - 3.4), 6),
                       "median_limit": 0.08, "v0_agm_adjustment": 0.0, "v0_change": 0.0071, "unregistered_dividend": 0.0, "unregistered_dps": 0.0,
                       "v0_limit": 0.08, "reason": "within_limit"},
    }

    market = make_market(cap, cap_by, median)
    layers = {"analytical": la, "market_implied": lm, "macro_neutral": ln, "headline_mix": headline_mix}
    grid = {"world_order": WORLDS, "regime_order": REGIMES, "scenario_order": SCEN, "years": YEARS, "basis": "engine", "cells": cells}
    variance = {"price": {"world": 0.412, "regime": 0.447, "scenario": 0.061, "interaction": 0.08},
                "method": "доли дисперсии цены клеток под вероятностями слоя «свой взгляд»: главные эффекты осей сетки и остаток"}
    worlds = make_worlds(by_world, cells)
    regimes = make_regimes()
    capital = make_capital(cells)
    n20_check = capital.pop("_n20_analytical")       # ожидание Н20.0 года якоря под весами «свой взгляд»: проверка капитала дивиденда
    dividends = make_dividends(n20_check)
    paths = make_paths(capital["mix"])
    nii = make_nii()
    guidance = make_guidance(capital["mix"]["n20"][0])
    governance = {"discount": GOV, "components": [
        {"id": "quasi_fiscal", "name": "Квазифискальные сделки и сделки со связанными сторонами", "value": GOV, "sign": 1,
         "basis": "сделки со связанными сторонами: поток потерь, делённый на разность стоимости капитала и роста, — 0,4 % оценки капитала (неподвижная точка на медиане)"},
        {"id": "withdrawals", "name": "Изъятия сверх налоговой оси", "value": 0.0, "sign": 1,
         "basis": "налоги, капитал и кризис заданы явно и в дисконт не входят; без опоры — 0"},
        {"id": "liquidity_listing", "name": "Ликвидность и листинг", "value": 0.0, "sign": 1, "basis": "≈0: ликвидная бумага первого уровня"}],
        "sum_signed": GOV, "price_at_low": r(central / (1 - GOV)), "price_at_high": r(central / (1 - GOV) * 0.9)}
    reverse_dcf = make_reverse()
    judgements = {"rows": make_judgement_rows(judg, share_of, central), "off_band_shift": {"rub": 0.0, "limit": STEP / 2, "axes": 0}}
    next_report = make_next_report(median, central)
    nowcast = make_nowcast(n20_check)
    indicators = make_indicators()
    calendar = make_calendar()
    history = make_history()
    checks = make_checks(capital["capital_gap"])
    inputs = make_inputs()
    live = make_live()
    changes = make_changes(median, central)
    valuation_history = make_valuation_history(median)
    book = make_book()

    meta = {
        "generated_at": "2026-09-30T16:05:12+00:00", "published_at": "2026-09-30T16:07:40+00:00",
        "valuation_date": VDATE, "facts_date": FACTS_DATE, "book_version": BOOK, "book_date": "2026-09-30",
        "book_tag": f"book-{BOOK}", "engine_commit": "5f1c0d9b2a7e4c3f8e6d1a0b9c8d7e6f5a4b3c2d", "fast": False,
        "payload_sha256": None, "previous_sha256": "9b0e4f6a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f", "bytes": None,
        "company": {"name": "Сбербанк", "tickers": TICKERS, "main_ticker": MAIN,
                    "share_classes": [{"ticker": "SBER", "class": "ordinary"}, {"ticker": "SBERP", "class": "preferred"}],
                    "cbr_regnum": 1481, "fiscal_year_end": "12-31"},
        "shares": {"issued_mln": N_ISS, "outstanding_mln": N_OUT, "by_ticker": BY_TICKER, "as_of": FACTS_DATE,
                   "src": "размещённые — МСФО 2025 прим. 22; собственные — расчёт по дивидендам 6М26 с. 43"},
        "period_unit": "quarter", "input_unit": "month",
        "anchor_period": "2026Q2", "first_period": "2026Q3", "last_period": "2036Q4", "horizon": ["2026Q3", "2036Q4"],
        "open_period": "2026Q3", "periods_closed": 0, "elapsed": 0.989, "book_first_period_closed": False,
        "curve_as_of": "2026-09-18", "basis": "ifrs", "step": "quarter", "day_count": "act365", "governance_discount": GOV,
    }
    payload = {
        "schema": "sber-v1", "meta": meta, "market": market, "fair_value": fair_value, "layers": layers, "grid": grid,
        "variance": variance, "worlds": worlds, "regimes": regimes, "capital": capital, "dividends": dividends,
        "paths": paths, "nii": nii, "guidance": guidance, "governance": governance, "reverse_dcf": reverse_dcf,
        "judgements": judgements, "next_report": next_report, "nowcast": nowcast, "indicators": indicators,
        "calendar": calendar, "history": history, "checks": checks, "inputs": inputs, "live": live,
        "changes": changes, "valuation_history": valuation_history, "book": book,
    }
    seal(payload)
    return payload


# ── рынок ──
def trading_days(start, end):
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_market(cap, cap_by, median):
    days = trading_days("2025-10-01", VDATE)
    path = {}
    x = 296.0
    closes = []
    for i, day in enumerate(days):
        x *= math.exp(RNG.gauss(-0.0002, 0.0115))
        if day == "2026-07-20":
            x -= 37.64
        closes.append(x)
    scale = PRICE["SBER"] / closes[-1]
    closes = [c * scale for c in closes]
    for t in TICKERS:
        k = PRICE[t] / PRICE["SBER"]
        full = [(dd, r(c * k * (1 + (0.004 * math.sin(i / 17) if t == "SBERP" else 0)))) for i, (dd, c) in enumerate(zip(days, closes))]
        full[-1] = (VDATE, PRICE[t])
        thin = full[::2]
        if thin[-1][0] != VDATE:
            thin.append(full[-1])
        path[t] = [{"date": dd, "close": c} for dd, c in thin]
        lo = min(full, key=lambda p: p[1])
        hi = max(full, key=lambda p: p[1])
        path.setdefault("_min", {})[t] = {"date": lo[0], "close": lo[1]}
        path.setdefault("_max", {})[t] = {"date": hi[0], "close": hi[1]}
    pmin, pmax = path.pop("_min"), path.pop("_max")
    peers_rows = [
        {"ticker": "SBER", "name": "Сбербанк", "price": PRICE["SBER"], "price_date": VDATE, "cap": cap, "bv": 8387.2, "bv_date": FACTS_DATE,
         "ni_ltm": 1745.1, "pb": r(cap / 8387.2, 4), "pe": r(cap / 1745.1, 4), "roe_ltm": 0.2254, "dividend_yield": 0.1379,
         "capital_ratio": {"name": "Н20.0", "value": 0.151, "as_of": FACTS_DATE}, "basis": "МСФО группы",
         "src": "выпуск и факты капитала", "status": "live", "reason": None},
        {"ticker": "VTBR", "name": "ВТБ", "price": 71.35, "price_date": VDATE, "cap": 383.6, "bv": 2170.4, "bv_date": FACTS_DATE,
         "ni_ltm": 571.2, "pb": 0.1767, "pe": 0.6716, "roe_ltm": 0.188, "dividend_yield": 0.0,
         "capital_ratio": {"name": "Н20.0", "value": 0.098, "as_of": FACTS_DATE}, "basis": "МСФО группы",
         "src": "МСФО 6М26", "status": "live", "reason": None},
        {"ticker": "T", "name": "Т-Технологии", "price": 3021.0, "price_date": VDATE, "cap": 811.9, "bv": 402.3, "bv_date": FACTS_DATE,
         "ni_ltm": 139.6, "pb": 2.018, "pe": 5.816, "roe_ltm": 0.301, "dividend_yield": 0.031,
         "capital_ratio": {"name": "достаточность общего капитала (группа, МСФО-расчёт)", "value": 0.129, "as_of": FACTS_DATE}, "basis": "МСФО группы",
         "src": "МСФО 6М26", "status": "live", "reason": None},
        {"ticker": "BSPB", "name": "Банк Санкт-Петербург", "price": 318.2, "price_date": VDATE, "cap": 141.7, "bv": 212.9, "bv_date": "2026-03-31",
         "ni_ltm": 49.1, "pb": 0.6656, "pe": 2.886, "roe_ltm": 0.231, "dividend_yield": 0.118,
         "capital_ratio": {"name": "Н1.0", "value": 0.187, "as_of": "2026-09-01"}, "basis": "МСФО группы",
         "src": "МСФО 3М26", "status": "live", "reason": None},
        {"ticker": "CBOM", "name": "МКБ", "price": None, "price_date": None, "cap": None, "bv": 351.0, "bv_date": FACTS_DATE,
         "ni_ltm": 31.4, "pb": None, "pe": None, "roe_ltm": 0.089, "dividend_yield": None,
         "capital_ratio": {"name": "Н1.0", "value": 0.121, "as_of": "2026-09-01"}, "basis": "МСФО группы",
         "src": "МСФО 6М26", "status": "missing", "reason": "живой цены нет: торги по бумаге не велись в день оценки"},
    ]
    history = []
    for i in range(24):
        month = 10 + i
        y, m = 2024 + (month - 1) // 12, (month - 1) % 12 + 1
        history.append({"date": f"{y}-{m:02d}-28", "median": r(372 + 8 * math.sin(i / 3) - 0.9 * i, 1), "n": 11 + (i % 4)})
    return {
        "price": PRICE[MAIN], "price_date": VDATE,
        "prices": {t: {"price": PRICE[t], "date": VDATE, "time": "18:49", "source": "T-Invest", "status": "live", "book_price": PRICE[t]} for t in TICKERS},
        "cap": cap, "cap_by_ticker": cap_by,
        "multiples": {"pb": r(cap / BV_V, 4), "pb_reported": r(cap / 8387.2, 4), "pe_ltm": r(cap / 1745.1, 4), "pe_fwd": r(cap / 1761.3, 4),
                      "dividend_yield_ltm": {t: r(37.64 / PRICE[t], 6) for t in TICKERS},
                      "dividend_yield_fwd": {t: r(DPS_POLICY / PRICE[t], 6) for t in TICKERS},
                      "bv_per_share": r(BV_V * 1000 / N_OUT), "basis": "ifrs"},
        "price_history": path, "price_min": pmin, "price_max": pmax,
        "ex_dividend": [{"date": "2026-07-20", "dps": 37.64, "year": 2025}],
        "peers": {"as_of": "2026-10-01", "basis": "МСФО групп на отчётную дату аналога; капитал — обыкновенных акционеров без бессрочных инструментов; "
                  "прибыль — акционерам за четыре квартала; ROE — к среднему капиталу; дивиденд — с отсечкой за 12 месяцев; норматив — как раскрыт банком; "
                  "цены — закрытия дня оценки",
                  "subject_ticker": MAIN, "rows": peers_rows},
        "brokers": {"as_of": "2026-09-29", "source": "T-Invest: консенсус-прогнозы, агрегаты", "n": 14, "median": 385.0,
                    "min": 320.0, "max": 452.0, "recommendations": {"buy": 12, "hold": 2, "sell": 0}, "history": history},
    }


def make_worlds(by_world, cells):
    key = {"N": [0.142, 0.1225, 0.0975, 0.085, 0.08], "H": [0.145, 0.14, 0.1275, 0.1175, 0.1125, 0.105],
           "M": [0.142, 0.1515, 0.169, 0.1745, 0.175, 0.17]}
    cpi = {"N": [0.065, 0.053, 0.042, 0.04], "H": [0.066, 0.0665, 0.0625, 0.057, 0.055], "M": [0.065, 0.056, 0.074, 0.097, 0.105, 0.108]}
    rows = {}
    for w in WORLDS:
        k = key[w] + [key[w][-1]] * (len(YEARS) - len(key[w]))
        c = cpi[w] + [cpi[w][-1]] * (len(YEARS) - len(cpi[w]))
        ws = [x for x in cells if x["world"] == w]
        pw = lambda x: P_REG[x["regime"]] * P_SC[x["regime"]][x["scenario"]]  # noqa: E731
        rows[w] = {
            "name": WORLD_NAMES[w], "weights": {"analytical": W_A[w], "market_implied": W_MI[w], "macro_neutral": W_N[w]},
            "key_rate": yearly(k), "cpi": yearly(c), "real_key": yearly([(1 + a) / (1 + b) - 1 for a, b in zip(k, c)]),
            "ofz": {"1": {"N": 0.1316, "H": 0.1388, "M": 0.136}[w], "3": {"N": 0.1155, "H": 0.1443, "M": 0.1548}[w],
                    "5": {"N": 0.1061, "H": 0.1405, "M": 0.1614}[w], "10": {"N": 0.0985, "H": 0.1344, "M": 0.1646}[w]},
            "zero_curve": {"1": {"N": 0.1316, "H": 0.1388, "M": 0.136}[w], "3": {"N": 0.1155, "H": 0.1443, "M": 0.1548}[w],
                           "5": {"N": 0.1061, "H": 0.1405, "M": 0.1614}[w], "10": {"N": 0.0985, "H": 0.1344, "M": 0.1646}[w],
                           "LT": {"N": 0.09, "H": 0.127, "M": 0.167}[w]},
            "lt_inflation": {"N": 0.04, "H": 0.055, "M": 0.098}[w],
            "k_t": r(sum(pw(x) * x["k_t"] for x in ws), 4), "g_t": G_T_W[w],
            "price": by_world[w]["price"], "v0": by_world[w]["v0"],
            "credit_growth": {"corporate": yearly(ramp(0.09, {"N": 0.075, "H": 0.08, "M": 0.12}[w], k=3)),
                              "mortgage": yearly(ramp(0.07, {"N": 0.08, "H": 0.075, "M": 0.115}[w], k=3)),
                              "retail_other": yearly(ramp(0.12, {"N": 0.08, "H": 0.08, "M": 0.12}[w], k=3))},
            "funds_growth": {"retail": yearly(ramp(0.11, {"N": 0.075, "H": 0.085, "M": 0.12}[w], k=3)),
                             "corporate": yearly(ramp(0.11, {"N": 0.075, "H": 0.085, "M": 0.12}[w], k=3))},
            "transmission": {"N": 0.071, "H": 0.058, "M": 0.049}[w],
        }
    return {"order": WORLDS, "rows": rows,
            "source": {"record": "worlds_source.json", "origin": "worlds-850 book-1.6", "record_asof": "2026-09-21",
                       "curve_date": "2026-09-18", "sha256": "7296567d5200b5599efd0989de6ed8c8461d56c5c5dda244ab7d622303969fec"},
            "overlay": {"file": "worlds_bank.json", "sha256": "3c3270ef8a1b9d2e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e"}}


def make_regimes():
    cor = {"soft": [0.012, 0.010, 0.009, 0.010, 0.011], "norm": [0.014, 0.015, 0.014, 0.014, 0.014],
           "downturn": [0.016, 0.023, 0.020, 0.016, 0.017], "crisis": [0.016, 0.038, 0.025, 0.016, 0.020]}
    nim = {"soft": [0.0, 0.001, 0.002], "norm": [0.0, 0.0, 0.0], "downturn": [0.0, -0.002, -0.002, -0.001],
           "crisis": [0.0, -0.004, -0.002]}
    near = [("2026Q3", -0.005), ("2026Q4", -0.005), ("2027Q1", -0.00375), ("2027Q2", -0.0025), ("2027Q3", -0.00125), ("2027Q4", 0.0)]
    lga = {"soft": [0.0, 0.01, 0.01, 0.0], "norm": [0.0], "downturn": [0.0, -0.03, -0.03, 0.0], "crisis": [0.0, 0.0, 0.0, 0.01, 0.0]}
    post = {"soft": 0.158, "norm": 0.409, "downturn": 0.291, "crisis": 0.142}
    rows = {}
    for rg in REGIMES:
        c = cor[rg]
        rows[rg] = {
            "title": REGIME_NAMES[rg], "prior": P_REG[rg], "posterior": post[rg],
            "cor": [{"year": 2026 + i, "value": v} for i, v in enumerate(c[:4])],
            "cor_engine": [{"year": 2026 + i, "value": r(v + 0.0017, 4)} for i, v in enumerate(c[:4])],
            "cor_lt": c[4], "cor_lt_engine": r(c[4] + 0.0017, 4),
            "nim_shift": [{"year": 2026 + i, "value": v} for i, v in enumerate(nim[rg])],
            "nim_shift_lt": nim[rg][-1],
            "loan_growth_adj": [{"year": 2026 + i, "value": v} for i, v in enumerate(lga[rg])],
        }
    rows["crisis"]["crisis"] = {"shock_year": 2027, "shock_year_offset": 1, "one_off_loss": {"period": "2027Q1", "amount": -400.0},
                                "loan_growth_override": [{"year": 2027, "value": 0.025}, {"year": 2028, "value": 0.045}],
                                "cor_quarters": [{"period": "2027Q1", "value": 0.05}, {"period": "2027Q2", "value": 0.04},
                                                 {"period": "2027Q3", "value": 0.034}, {"period": "2027Q4", "value": 0.028}]}
    rows["downturn"]["crisis"] = None
    del rows["downturn"]["crisis"]
    exp_lt = sum(post[g] * rows[g]["cor_lt"] for g in REGIMES)
    hist = []
    mg = {"2022": None}
    cor_m = [None] * 4 + [0.013, 0.011, 0.008, 0.009, 0.012, 0.011, 0.012, 0.013, 0.013, 0.011, 0.013, 0.012, 0.014, 0.012]
    nim_m = [None] * 4 + [0.056, 0.058, 0.059, 0.059, 0.058, 0.057, 0.058, 0.058, 0.057, 0.057, 0.059, 0.060, 0.061, 0.062]
    for i, (a, b) in enumerate(zip(cor_m, nim_m)):
        y, q = 2022 + i // 4, i % 4 + 1
        hist.append({"period": f"{y}Q{q}", "cor_mgmt": a, "cor_engine": None if a is None else r(a + 0.0017, 4),
                     "nim_mgmt": b, "nim_engine": None if b is None else r(b - 0.0041, 4)})
    del mg
    return {
        "order": REGIMES, "basis": "engine", "cor_basis": "mgmt",
        "cor_bridge": {"method": "движок = упр. + сдвиг моста (окно 1К24–2К26, резервы по долговым ФА к средним валовым кредитам АС)", "value": 0.0017},
        "rows": rows, "near_nim_shift": [{"period": q, "value": v} for q, v in near],
        "expected": {"cor_lt": r(exp_lt, 5), "cor_lt_engine": r(exp_lt + 0.0017, 5),
                     "nim_shift_lt": r(sum(post[g] * rows[g]["nim_shift_lt"] for g in REGIMES), 5)},
        "update": {"cor": {"sigma_pp": 0.0035, "rho_q": 0.5}, "nim": {"sigma_pp": 0.002, "rho_q": 0.7}, "max_shift_pp": 0.05, "window_obs": 4, "floor_share": 0.33,
                   "observations": [{"period": "2026Q2", "basis": "mgmt", "cor": 0.012, "nim": 0.062, "se_cor": 0.0, "se_nim": 0.0,
                                     "cor_engine": 0.0137, "nim_engine": 0.0579, "posterior_after": post,
                                     # пометка подмены книги (М§12): опубликованное число и причина — выдуманы
                                     "published": {"cor": 0.0105, "nim": None,
                                                   "reason": "разовый роспуск резерва занизил CoR квартала (пример)"}}]},
        "history": hist,
        "reference_class": {"episodes": [
            {"id": "2008", "name": "Кризис 2008–2009", "period": "2008Q4–2010Q1", "cor_peak": 0.0725, "quarters": 6, "nim_drop": -0.004, "src": "МСФО 2009"},
            {"id": "2014", "name": "Санкции и девальвация", "period": "2014Q4–2015Q4", "cor_peak": 0.025, "quarters": 5, "nim_drop": -0.009, "src": "МСФО 2015"},
            {"id": "2020", "name": "Пандемия", "period": "2020Q1–2020Q4", "cor_peak": 0.021, "quarters": 4, "nim_drop": -0.002, "src": "МСФО 2020"},
            {"id": "2022", "name": "Шок 2022", "period": "2022Q1–2022Q4", "cor_peak": 0.025, "quarters": 4, "nim_drop": None, "src": "МСФО 2022 (годовая)"}],
            "shares": [{"regime": g, "book": P_REG[g], "class": s, "lo": lo, "hi": hi} for g, s, lo, hi in (
                ("soft", 0.17, 0.06, 0.39), ("norm", 0.44, 0.25, 0.65), ("downturn", 0.22, 0.09, 0.45), ("crisis", 0.17, 0.06, 0.39))]},
    }


def wq(pairs, q):
    """Квантиль значений по весам (ступенчатый): P10–P90 норматива по клеткам сценария."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total - 1e-12:
            return v
    return pairs[-1][0]


def make_capital(cells):
    n = len(YEARS)
    cons = [0.010, 0.015] + [0.025] * (n - 2)
    sifi = {"schedule": [0.005, 0.0075, 0.010] + [0.010] * (n - 3),
            "upper": [0.005, 0.0075, 0.010, 0.015] + [0.020] * (n - 4),
            "strict": [0.005, 0.0075, 0.010, 0.015, 0.020] + [0.025] * (n - 5)}
    ccyb = {"schedule": [0.005] * n, "upper": [0.005, 0.005] + [0.010] * (n - 2), "strict": [0.005, 0.005] + [0.010] * (n - 2)}
    ded = [0.0, 0.0] + [0.0045] * (n - 2)
    scen = {}
    for s in SCEN:
        floor20 = [0.08 + a + b + c + d for a, b, c, d in zip(cons, sifi[s], ccyb[s], ded)]
        floor11 = [0.045 + a + b + c + d for a, b, c, d in zip(cons, sifi[s], ccyb[s], ded)]
        mass = sum(P_REG[g] * P_SC[g][s] for g in REGIMES)
        scen[s] = {"title": SCEN_NAMES[s], "p_given_regime": {g: P_SC[g][s] for g in REGIMES}, "mass": r(mass, 6),
                   "conservation": cons, "sifi": sifi[s], "ccyb": ccyb[s], "deduction_n20": ded, "deduction_n11": ded,
                   "floor20": [r(x, 4) for x in floor20], "floor11": [r(x, 4) for x in floor11],
                   "req20": [r(max(0.133, x + 0.013), 4) for x in floor20], "req11": [r(x, 4) for x in floor11]}   # требование — порог политики или пол + буфер
    # норматив сценария и смеси — ожидание нормативов клеток (определение движка, П§2 capital): сценарий — под весами
    # «свой взгляд», смесь заголовка — под λ × «свой» + (1 − λ) × «рыночные ставки»
    by = {}
    for s in SCEN:
        own = [c for c in cells if c["scenario"] == s]
        tot = sum(c["p_analytical"] for c in own)
        by[s] = {}
        for k in ("n20", "n11"):
            by[s][k] = [r(sum(c["p_analytical"] * c["annual"][k][i] for c in own) / tot, 4) for i in range(n)]
            for q, name in ((0.1, "p10"), (0.9, "p90")):
                by[s][f"{k}_{name}"] = [wq([(c["annual"][k][i], c["p_analytical"]) for c in own], q) for i in range(n)]
    mass = {s: scen[s]["mass"] for s in SCEN}
    p_mix = [LAM * c["p_analytical"] + (1 - LAM) * c["p_neutral"] for c in cells]
    mix = {k: [r(sum(w * c["annual"][k][i] for w, c in zip(p_mix, cells)), 4) for i in range(n)] for k in ("n20", "n11")}
    for k in ("req20", "req11", "floor20", "floor11"):
        mix[k] = [r(sum(mass[s] * scen[s][k][i] for s in SCEN), 4) for i in range(n)]
    gap_cells = [c for c in cells if "capital_gap" in c["flags"]]
    rows = [("dividend_accrual", "Вычет дивиденда (выплата за 2025 и начисление за 2026)", -0.0212, -1280.4),
            ("profit", "Прибыль второго полугодия", 0.0147, 880.6),
            ("oci_other", "OCI и прочие движения капитала", 0.0012, 72.1),
            ("rwa_growth", "Рост RWA", -0.0048, 1925.0),
            ("deductions", "Вычеты: рост с RWA, поправка и вычеты сценария", -0.0009, -54.9)]
    start = 0.151
    end = mix["n20"][0]                               # мост приходит в норматив смеси на конец года
    rows.append(("other", "Прочее", r(end - start - sum(x[2] for x in rows), 6), 11.8))
    return {
        "titles": {"n20": "Н20.0 группы", "n11": "Н20.1 группы (прокси — Н1.1 банка)", "n11_observed": "Н1.1 банка, форма 0409135",
                   "n1_0": "Н1.0 банка", "n1_2": "Н1.2 банка"},
        "anchor": {"as_of": FACTS_DATE, "n20": 0.151, "n20_pre_dividend": True, "n20_post_dividend": 0.1368,
                   "n11_bank": {"value": 0.11961, "as_of": "2026-07-01"}, "n10_bank": {"value": 0.1352, "as_of": "2026-07-01"},
                   "rwa": 59318.1, "bv_common": 8387.2, "at1": 150.0, "t2": 500.3, "fvoci_reserve": -374.9,
                   "ded20": 936.7, "ded11": 1173.8, "req20_now": 0.133, "req11_now": 0.065, "n20_headroom": 0.0038,
                   "basis": "ifrs", "src": "МСФО 6М26 с. 5, 68; презентация 2К26 с. 21; форма 0409135"},
        "observed": {"n1_0": 0.1339, "n1_1": 0.1187, "n1_2": 0.1193, "as_of": "2026-09-01", "source": "форма 0409135 ЦБ"},
        "policy_threshold": 0.133, "minimum": {"n20_0": 0.08, "n1_1": 0.045}, "mgmt_buffer": {"n20_0": 0.013, "n1_1": 0.0},
        "years": YEARS, "scenarios": scen, "by_scenario": by, "mix": mix,
        "capital_gap": {"mass": r(sum(c["p_analytical"] for c in gap_cells), 6), "cells": len(gap_cells),
                        "cell_list": [f"{c['world']}/{c['regime']}/{c['scenario']}" for c in gap_cells][:10], "first_period": "2027Q2",
                        "by_scenario": {s: r(sum(c["p_analytical"] for c in gap_cells if c["scenario"] == s), 6) for s in SCEN}},
        "_n20_analytical": r(sum(c["p_analytical"] * c["annual"]["n20"][0] for c in cells), 4),
        "bridge": {"from": "2026Q2", "to": "2026Q4", "start": start, "end": end,
                   "rows": [{"key": k, "title": t, "pp": pp, "amount": a} for k, t, pp, a in rows]},
    }


def make_dividends(n20_check):
    model = []
    for i, y in enumerate(range(2026, 2036)):
        policy = DPS_POLICY * (1.064 ** i)
        dps = policy * (1 - P_CANCEL) if i == 0 else policy * 0.985
        model.append({"year": y, "pay_year": y + 1, "dps": r(dps, 4), "dps_p10": 0.0 if i == 0 else r(dps * 0.78, 4),
                      "dps_p90": r(policy * 1.031 if i == 0 else dps * 1.17, 4), "dps_policy": r(policy, 4), "payout": 0.5,
                      "amount": r(dps * N_ISS / 1000), "p_cut": r(0.084 if i < 3 else 0.041, 6),
                      "p_zero": P_CANCEL if i == 0 else 0.0, "declared": False})
    hist = []
    for y, dps, ni, rec, pay in ((2013, 3.20, None, "2014-06-17", "2014-06-26"), (2014, 0.45, None, "2015-06-15", "2015-06-26"),
                                 (2015, 1.97, None, "2016-06-14", "2016-06-26"), (2016, 6.00, None, "2017-06-14", "2017-06-28"),
                                 (2017, 12.00, None, "2018-06-26", "2018-07-10"), (2018, 16.00, None, "2019-06-13", "2019-06-27"),
                                 (2019, 18.70, 845.0, "2020-10-05", "2020-10-06"), (2020, 18.70, 760.3, "2021-05-12", "2021-05-13"),
                                 (2021, 0.0, 1245.9, None, None), (2022, 25.00, 270.5, "2023-05-11", "2023-05-12"),
                                 (2023, 33.30, 1511.8, "2024-07-11", "2024-07-12"), (2024, 34.84, 1581.6, "2025-07-18", "2025-07-21"),
                                 (2025, 37.64, 1707.4, "2026-07-20", "2026-08-04")):
        pool = r(dps * N_ISS / 1000) if ni else None                      # пул и доля выплаты ранних лет в фактах не раскрыты
        hist.append({"year": y, "dps": dps, "dps_preferred": dps, "pool": pool, "payout_ratio": r(pool / ni, 4) if ni else None,
                     "record_date": rec, "ex_date": rec, "pay_date": pay, "ni_shareholders": ni,
                     # источник — слова для владельца (решение собрания и раскрытие), без имён методов и полей API
                     "src": "годовое общее собрание акционеров решило дивиденды не выплачивать (раскрытие эмитента)" if dps == 0
                     else "решение годового общего собрания акционеров (раскрытие эмитента); сверено с брокерским календарём дивидендов"})
    checks = []
    for y, ni, tax, declared in ((2023, 1511.8, 0.20, 33.30), (2024, 1581.6, 0.20, 34.84), (2025, 1707.4, 0.25, 37.64)):
        base = ni - 9.7 * (1 - tax)
        pool = base * 0.5
        exact = pool * 1000 / N_ISS
        rounded = math.ceil(exact * 100 - 1e-9) / 100
        checks.append({"year": y, "ni_shareholders": ni, "at1_coupon": 9.7, "tax_statutory": tax, "base": r(base), "pool": r(pool),
                       "dps_exact": r(exact, 4), "dps_rounded": rounded, "dps_declared": declared, "rounding": "ceil_kopeck",
                       "ok": abs(rounded - declared) < 1e-9})
    return {
        "policy": {"name": "Положение о дивидендной политике (НС 05.12.2023, протокол № 34)",
                   "text": "Не менее 50 % чистой прибыли группы по МСФО, приходящейся на акционеров, при достаточности капитала: Н20.0 после выплаты — не ниже 13,3 %",
                   "doc": "dividend_policy_2023.pdf", "sha256": "f486aa17c0de4b1f8a9e2d3c4b5a6f7e8d9c0b1a2f3e4d5c6b7a8f9e0d1c2b3a",
                   "approved": "2023-12-05", "valid_until": "2026-12-05",
                   "valid_until_note": "п. 6.4 Положения: срок — 3 года с даты утверждения; новая политика ожидается со стратегией и войдёт новой версией книги",
                   "base": "ifrs_ni_shareholders",
                   "payout": [{"year": y, "value": 0.5} for y in YEARS], "metric": "n20_0", "threshold": 0.133, "steps": [],
                   "shortfall_rule": "residual_above_requirement", "deduct_at1_after_tax": True,
                   "divisor": "issued", "excess": {"from_profit_year": 2030, "epsilon": 0.5, "ramp_years": 3},
                   "crisis": {"skip_in_shock_year": True, "catch_up": True}},
        "ladder": [{"key": "policy", "title": "50 % прибыли МСФО", "condition": "Н20.0 после выплаты не ниже 13,3 %", "payout": 0.5, "current": True},
                   {"key": "shortfall", "title": "Остаток сверх требований", "condition": "Н20.0 ниже 13,3 % — выплата урезается до запаса над порогом",
                    "payout": None, "current": False}],
        "register": [{"year": 2025, "dps": 37.64, "amount": r(37.64 * N_ISS / 1000), "record_date": "2026-07-20", "last_buy_date": "2026-07-17",
                      "ex_date": "2026-07-20", "pay_date": "2026-08-04", "status": "paid", "in_bridge": False,
                      "sources": ["T-Invest", "Интерфакс"]}],
        "history": hist, "formula_check": checks, "model": model,
        "next_expected": {"year": 2026, "status": "model", "dps": DPS_POLICY, "dps_policy": DPS_POLICY, "dps_mean": model[0]["dps"],
                          "dps_p10": model[0]["dps_p10"], "dps_p90": model[0]["dps_p90"], "p_cancel": P_CANCEL,
                          "record_date": None, "record_date_est": "2027-07-19", "record_date_note": "как в прошлые годы: отсечка во второй половине июля",
                          "pay_date_est": "2027-08-03", "yield": {t: r(DPS_POLICY / PRICE[t], 6) for t in TICKERS},
                          "condition": {"metric": "n20_0", "threshold": 0.133, "n20_expected": n20_check, "p_limited": 0.084}},
        "yield_ltm": {t: r(37.64 / PRICE[t], 6) for t in TICKERS}, "basis": "ifrs",
    }


def make_paths(mix):
    rows, quarters, tree = [], [], []
    bv_prev = 8387.2
    assets_prev = 69141.5
    for i, y in enumerate(YEARS):
        g = 1.0 + 0.075 * i
        nii = 3610 * g * (1 - 0.004 * i)
        fees = 868 * (1 + 0.07 * i)
        ins = 190 * (1 + 0.08 * i)
        other = -205 * (1 + 0.05 * i)
        noncore = -170 + 30 * min(i, 4)
        opex = 1300 * (1 + 0.08 * i)
        llp = 610 * (1 + 0.1 * i) * (1.25 if i == 1 else 1)
        fvc = 6.0 + 1.5 * i + (14.0 if i == 1 else 0.0)                    # кредитная переоценка кредитов по СС сверх опоры — расход
        one_off = -60.0 if i == 1 else 0.0                                 # разовый убыток кризиса в ожидании смеси
        pbt = nii - llp - fvc + fees + ins + other + noncore - opex + one_off
        tax = pbt * 0.25 - 12
        ni = pbt - tax
        bv_end = bv_prev + ni * 0.5 + 60
        assets_end = assets_prev * (1.085 if i else 1.034)
        avg_bv = (bv_prev + bv_end) / 2
        avg_a = (assets_prev + assets_end) / 2
        roe = (ni - 4) / avg_bv
        rows.append({"year": y, "fact_quarters": 2 if i == 0 else 0, "nii": r(nii), "nim": r(0.0587 - 0.0006 * min(i, 3), 4),
                     "nim_mgmt": r(0.0628 - 0.0006 * min(i, 3), 4), "fees": r(fees), "fees_growth": r(0.0 if i == 0 else 0.07, 4),
                     "insurance": r(ins), "other": r(other), "noncore": r(noncore), "opex": r(opex),
                     "cir": r(opex / (nii + fees + ins + other + noncore), 4),
                     "cir_mgmt": r(opex / (nii + fees + ins + other + noncore) + 0.014, 4), "llp": r(llp),
                     "cor": r(0.0138 + (0.0035 if i == 1 else 0), 4), "cor_mgmt": r(0.0121 + (0.0035 if i == 1 else 0), 4),
                     "fvc": r(fvc), "one_off": r(one_off), "pbt": r(pbt), "tax": r(tax), "ni": r(ni), "ni_sh": r(ni - 4), "oci": r(60.0), "ci": r(ni + 60),
                     "roe": r(roe, 4), "roe_ci": r((ni + 56) / avg_bv, 4), "bv_end": r(bv_end), "rwa_end": r(61600 * (1.08 ** i)),
                     "n20_end": mix["n20"][i], "n11_end": mix["n11"][i], "req20_end": r([0.133, 0.133, 0.133, 0.138, 0.143, 0.145][min(i, 5)], 4),
                     "floor20_end": r([0.10, 0.1075, 0.12, 0.125, 0.13, 0.132][min(i, 5)], 4),
                     "loans_end": r(52600 * 1.09 ** i), "funds_end": r(50900 * 1.085 ** i), "assets_end": r(assets_end),
                     "dps": (r(DPS_POLICY * (1 - P_CANCEL), 4) if i == 0 else r(DPS_POLICY * 1.064 ** i * 0.985, 4)) if i < len(YEARS) - 1 else None,
                     "payout": 0.5 if i < len(YEARS) - 1 else None,
                     "div_paid": r(841.0 if i == 0 else 38.62 * 1.064 ** (i - 1) * N_ISS / 1000)})
        lev = avg_a / avg_bv
        parts = {"nii_to_assets": nii / avg_a, "fees_to_assets": fees / avg_a, "other_to_assets": (ins + other - fvc + one_off) / avg_a,
                 "noncore_to_assets": noncore / avg_a, "opex_to_assets": -opex / avg_a, "llp_to_assets": -llp / avg_a,
                 "tax_to_assets": -tax / avg_a}
        parts = {k: r(v, 6) for k, v in parts.items()}
        roa = r(sum(parts.values()), 6)
        tree.append({"year": y, **parts, "roa": roa, "leverage": r(lev, 4), "roe": r(roa * r(lev, 4), 6)})
        bv_prev, assets_prev = bv_end, assets_end
    for i in range(14):
        y, qn = 2026 + (2 + i) // 4, (2 + i) % 4 + 1
        quarters.append({"period": f"{y}Q{qn}", "fact": False, "nii": r(905 + 12 * i), "nim": r(0.0584 - 0.0003 * min(i, 4), 4),
                         "nim_mgmt": r(0.0625 - 0.0003 * min(i, 4), 4), "llp": r(160 + 3 * i + (60 if 2 <= i <= 5 else 0)),
                         "cor": r(0.0135 + (0.004 if 2 <= i <= 5 else 0), 4), "cor_mgmt": r(0.0118 + (0.004 if 2 <= i <= 5 else 0), 4),
                         "fees": r(220 + 3 * i), "opex": r(330 + 6 * i), "ni_sh": r(445 + 5 * i - (40 if 2 <= i <= 5 else 0)),
                         "bv": r(8120 + 180 * i), "n20": r(mix["n20"][min(1, (2 + i) // 4)] + 0.0006 * (3 - (2 + i) % 4), 4),
                         "n11": r(mix["n11"][min(1, (2 + i) // 4)] + 0.0006 * (3 - (2 + i) % 4), 4),
                         "req20": 0.133, "floor20": r(0.10 + 0.0019 * (i // 4), 4)})
    return {"mix": {"title": "смесь заголовка (λ книги)", "lambda": LAM}, "annual": rows, "quarters": quarters, "roe_tree": tree,
            "terminal": {"roe_t": 0.1968, "k_t": 0.1612, "g_t": 0.0694, "x_t": 131.4, "payout_t": r(1 - 0.0694 / 0.1968, 4), "fade": 1.0}}


def make_nii():
    books = [("corp_loans", "Корпоративные кредиты", "asset", "ofz_1y", 0.33, None, True, False, 16266.3, 0.1421, 0.00666),
             ("mortgage", "Ипотека рыночная", "asset", "ofz_3y", 0.12, None, True, False, 6147.0, 0.165, 0.03408),
             ("mortgage_sub", "Ипотека льготная", "asset", "key", 1.0, None, False, False, 7137.9, 0.1712, 0.025),
             ("retail_loans", "Розница без ипотеки", "asset", "ofz_3y", 0.12, None, True, False, 6990.2, 0.2215, 0.03408),
             ("securities", "Ценные бумаги", "asset", "ofz_3y", 0.33, None, True, False, 9787.6, 0.1283, -0.01216),
             ("liquidity", "Ликвидность", "asset", "key", 1.0, None, False, True, 3558.2, 0.074, -0.0659),
             ("retail_current", "Текущие счета физлиц", "liability", "key", 0.31, 0.272, True, False, 14309.0, 0.0343, -0.00929),
             ("retail_term", "Срочные вклады физлиц", "liability", "key", 0.37, 0.855, True, False, 20160.6, 0.1171, -0.01983),
             ("corp_funds", "Средства юрлиц", "liability", "key", 0.97, 0.709, True, False, 14438.0, 0.0879, -0.01496),
             ("wholesale", "Оптовое фондирование", "liability", "key", 1.0, 1.0, False, True, 6157.9, 0.1562, -0.01555)]
    out = []
    floors = {"corp_loans": 0.005, "mortgage": 0.015, "retail_loans": 0.015}      # checks.lt_spread_floor — у кредитных книг
    sigma = {"asset": -0.00112, "liability": 0.00028}                              # σ0_A и σ0_L (доля сдвига на активах 0,8)
    # сжатие φ к миру H: у кредитной книги сжимается спред (−φ_A × разность опор), у средств клиентов дорожает фондирование (+φ_L × X_W)
    squeeze = {"asset": {"N": 0.0008, "H": 0.0, "M": -0.0004}, "liability": {"N": -0.0006, "H": 0.0, "M": 0.0012}}
    for key, title, side, ref, rho, beta, phi, bal, bal0, rate0, lt in books:
        world = {w: r(lt + (sigma[side] + squeeze[side][w] if phi else 0.0), 5) for w in WORLDS}
        row = {"key": key, "title": title, "side": side, "ref": ref, "rho": rho, "phi": phi, "balancing": bal,
               "balance_anchor": bal0, "rate_anchor": rate0, "spread_lt": lt, "spread_lt_world": world, "spread_floor": floors.get(key),
               "rate_lt": {"N": r(0.09 + lt + (0.0 if side == "asset" else -0.01), 4), "H": r(0.127 + lt, 4), "M": r(0.167 + lt, 4)}}
        if beta is not None:
            row["beta"] = beta
        out.append(row)
    years = YEARS
    nim_w = {"N": [0.0628, 0.062, 0.0612, 0.0605, 0.06] + [0.06] * 6, "H": [0.0628, 0.0621, 0.0615, 0.0608, 0.0604] + [0.0602] * 6,
             "M": [0.0628, 0.0624, 0.0631, 0.0635, 0.0637] + [0.0638] * 6}
    return {"books": out,
            "transmission": {"target": 0.06, "definition": "разность стационарных ЧПМ миров M и N на разность их долгосрочных ключевых",
                             "realized": 0.06, "nim_lt_target_mgmt": 0.06, "target_eng": 0.0559, "sigma0": -0.00112, "sigma0_liab": 0.00028,
                             "sigma0_split": 0.8, "phi": 0.484, "phi_assets": 0.2904, "phi_liab": 0.179, "phi_split": 0.6,
                             "loan_margin": {"N": 0.0312, "H": 0.0241, "M": 0.0058},
                             "tol": 1e-9, "solved": True, "roe_equiv": 0.3649,
                             "pairs": [{"from": "N", "to": "H", "key_from": 0.08, "key_to": 0.105, "value": 0.212, "roe_equiv": 1.281, "inside": True},
                                       {"from": "H", "to": "M", "key_from": 0.105, "key_to": 0.17, "value": 0.001, "roe_equiv": 0.006, "inside": True}],
                             "pairs_range": [-0.05, 0.25], "by_world": {"N": 0.119, "H": 0.102, "M": 0.059},
                             "realized_cells": 0.0597},
            "nim_by_world": {"years": years, **nim_w},
            "current_share": {"c_ref": 0.415, "psi": 1.12, "bounds": [0.35, 0.52],
                              "by_world": {"N": [r(0.415 + 0.01 * min(i, 4), 4) for i in range(len(years))],
                                           "H": [r(0.415 + 0.003 * min(i, 4), 4) for i in range(len(years))],
                                           "M": [r(0.415 - 0.012 * min(i, 4), 4) for i in range(len(years))]}},
            "disclosed": {"nii_per_100bp": None, "src": "не раскрыто в фактах книги"}}


def make_revisions(items):
    """Снимки гайденса по датам событий: строка есть в каждом снимке, значение меняется редко."""
    events = [("2025-12-10", "День инвестора 10.12.2025"), ("2026-02-26", "МСФО 2025 (26.02.2026)"),
              ("2026-04-29", "МСФО 1К26 (29.04.2026)"), ("2026-07-29", "МСФО 2К26 (29.07.2026)")]
    was = {"nim": [(0, 0.059), (2, 0.062)], "fee_growth": [(0, [0.05, 0.07]), (3, 0.0)],
           "loan_growth_retail": [(0, [0.09, 0.11]), (1, [0.05, 0.08])]}
    out = []
    for i, (day, event) in enumerate(events):
        for key, _title, _basis, value, *_ in items:
            for since, v in was.get(key, []):
                if i >= since:
                    value = v
            out.append({"date": day, "key": key, "value": value, "event": event})
    return out


def make_guidance(n20_year):
    items = [("roe", "ROE", "mgmt", 0.22, 0.2254, 0.217, 0.2086, "inside", 0.18),
             ("nim", "ЧПМ", "mgmt", 0.062, 0.0616, 0.0612, 0.0608, "inside", 0.22),
             ("cor_max", "CoR, не выше", "mgmt", 0.014, 0.0121, 0.0124, 0.0127, "inside", 0.12),
             ("cir", "CIR", "mgmt", [0.30, 0.32], 0.2975, 0.3052, 0.3129, "inside", 0.09),
             ("fee_growth", "Рост комиссий", "mgmt", 0.0, 0.054, 0.012, -0.03, "outside", 0.61),
             ("n20_0", "Н20.0 на конец года", "regulatory", 0.133, 0.151, n20_year, None, "inside", 0.03),
             ("loan_growth_corporate", "Рост кредитов юрлицам", "sector", [0.08, 0.10], 0.037, 0.089, None, "inside", 0.0),
             ("loan_growth_retail", "Рост кредитов физлицам", "sector", [0.05, 0.08], 0.056, 0.098, None, "n/a", None)]
    # строки сектора — прогноз сектора, а не гайденс банка (М§14.2): «лучше сектора» с диапазоном не сравнивается
    sector = {"loan_growth_corporate": ("in_line", "прогноз по сектору; банк — в соответствии с сектором"),
              "loan_growth_retail": ("above", "прогноз по сектору; банк — лучше сектора")}
    kinds = {"roe": "point", "nim": "point", "cor_max": "max", "cir": "range", "fee_growth": "point", "n20_0": "min",
             "loan_growth_corporate": "range", "loan_growth_retail": "range"}
    return {"year": 2026, "as_of": "2026-07-29", "src": "презентация 2К26 с. 23–24", "basis": "mgmt",
            "items": [{"key": k, "title": t, "basis": b, "kind": kinds[k], "guidance": g,
                       "scope": "sector" if k in sector else "group", "relation": sector[k][0] if k in sector else None,
                       **({"scope_note": sector[k][1]} if k in sector else {}),
                       "fact_ytd": f, "fact_periods": ["2026Q1", "2026Q2"],
                       "model_year": m, "required_rest": rr, "status": st, "mass_outside": mo} for k, t, b, g, f, m, rr, st, mo in items],
            "gate": {"name": "guidance_gap", "fired": True, "mass": 0.61,
                     "explanation": "Рост комиссий 2026 выше гайденса ≈0 %: модель продолжает тренд 1П26 (+5,4 % г/г) на 3К; гайденс менеджмента учитывает отмену части комиссий во 2П. Расхождение — в пределах 1,5 % ЧКД года, на цену влияет меньше шага печати.",
                     "valid_until": "2026-10-28"},
            "revisions": make_revisions(items),
            "strategy": {"name": "Стратегия 2024–2026", "targets": [
                {"key": "roe", "title": "ROE не ниже 22 %", "target": 0.22, "fact_last": 0.2254, "fact_period": "2026Q2",
                 "model": [{"year": 2026, "value": 0.217}], "met": False},
                {"key": "payout", "title": "Выплата 50 % прибыли", "target": 0.5, "fact_last": 0.5, "fact_period": "2025",
                 "model": [{"year": 2026, "value": 0.5}], "met": True}],
                "next_event": {"date": "2026-12-09", "title": "День инвестора: новая стратегия"}}}


def make_reverse():
    rows = [("nim_lt", "ЧПМ сквозь цикл (A-N2)", "value", "pct", ["nii.nim_lt_target_mgmt"], 0.060, [0.054, 0.067], 0.0527, "solved"),
            ("cor_lt", "Сдвиг CoR сквозь цикл", "shift", "pp", ["regimes.*.cor.LT"], 0.0, [-0.003, 0.005], 0.0047, "solved"),
            ("transmission", "Передача ключевой в ЧПМ (A-N3)", "value", "number", ["nii.transmission.target"], 0.06, [0.0, 0.14], None, "unreachable"),
            ("beta", "β_E", "value", "number", ["valuation.beta_e"], 0.83, [0.70, 1.05], 1.274, "solved"),
            ("erp", "ERP", "value", "pct", ["valuation.erp"], 0.0557, [0.049, 0.062], 0.0869, "solved"),
            ("reg_mix", "Регуляторный сценарий (смесь к «Жёсткому»)", "mix", "mix", ["joint.reg_prob_given_regime"], 0.0, [0.0, 0.0], None, "unreachable"),
            ("real_growth", "Реальный рост в терминале", "value", "pct", ["valuation.terminal.real_growth"], 0.015, [0.005, 0.025], -0.0214, "solved"),
            ("governance", "Дисконт за управление", "value", "pct", ["valuation.governance.discount"], GOV, [0.0, 0.10], 0.262, "solved"),
            ("payout", "Отклонение выплаты от политики", "shift", "pp", ["dividends.payout_deviation"], 0.0, [-0.10, 0.0], None, "unreachable")]
    out = []
    for key, name, kind, unit, paths, book, rng, solved, status in rows:
        inr = solved is not None and rng[0] <= solved <= rng[1]
        out.append({"key": key, "name": name, "kind": kind, "unit": unit, "paths": paths, "book": book, "range": rng,
                    "solved": solved, "delta": None if solved is None else r(solved - book, 6), "in_range": inr, "status": status,
                    "gap": 0.0 if solved is not None else r({"transmission": 58.4, "reg_mix": 82.3}.get(key, 31.2), 2),
                    "point_solved": None if solved is None else r(solved * 1.02, 6), "point_status": status})
    return {"target": PRICE[MAIN], "number": "median",
            "method": "подвыборка 400 прогонов с общими случайными числами, поправка к полной медиане, секущая; невязка — медиана полной полосы в решении; "
                      "у оси полосы прогоны распределены вокруг проверяемого значения, поэтому решения разных строк не перемножаются",
            "rows": out,
            "bank_rows": [{"key": "implied_roe_through_cycle", "title": "Вменённый ROE сквозь цикл", "unit": "pct", "book": 0.1968, "implied": 0.1612,
                           "delta": -0.0356, "status": "solved"},
                          {"key": "implied_cost_of_equity", "title": "Вменённая стоимость капитала", "unit": "pct", "book": 0.1612, "implied": 0.2051,
                           "delta": 0.0439, "status": "solved", "by_world": {"N": 0.1712, "H": 0.2066, "M": 0.2474}},
                          {"key": "market_cap_minus_bv", "title": "Рынок: капитализация минус капитал", "unit": "bn", "book": 146.3, "implied": -2022.9,
                           "delta": -2169.2, "status": "solved"}]}


def judgement_axes():
    # источник — слова книги для владельца (П§0.2): без меток класса, путей и рабочих пометок, даты — с годом, не длиннее 80 знаков;
    # единица оси — поле `unit` оси книги или её пути (М§10): срок — `years`, коэффициент — `number`, сдвиг доли — `pp`
    axes = [("nim_lt", "ЧПМ сквозь цикл (A-N2)", "value", "pct", ["nii.nim_lt_target_mgmt"], 0.060, 0.054, 0.067, 1, "линия 5,4 % + 0,037 × ключевая сквозь цикл; диапазон — история 2013–2026"),
            ("cor_lt", "CoR сквозь цикл (сдвиг LT всех режимов)", "shift", "pp", ["regimes.*.cor.LT"], 0.0, -0.003, 0.005, -1, "референс-класс эпизодов 2008–2026, М§3.2"),
            ("erp", "ERP", "value", "pct", ["valuation.erp"], 0.0557, 0.049, 0.062, -1, "решение владельца 30.09.2026"),
            ("beta", "β_E", "value", "number", ["valuation.beta_e"], 0.83, 0.70, 1.05, -1, "β к индексу за 3 года — 0,826"),
            ("transmission", "Передача ключевой в ЧПМ (A-N3)", "value", "number", ["nii.transmission.target"], 0.06, 0.0, 0.14, 1, "калибровка книг ЧПД: эмпирика 0,056–0,085"),
            ("kappa", "κ: CoR к реальной ставке сверх мира N", "value", "number", ["credit.kappa"], 0.05, 0.0, 0.15, -1, "регрессии 2014–2026: доля CoR на долю реальной ставки"),
            ("crisis_nim", "Кризис: сдвиг ЧПМ 2027", "value", "pp", ["regimes.crisis.nim_shift.2027"], -0.004, -0.012, 0.0, 1, f"книга {BOOK}, ось „Кризис: сдвиг ЧПМ 2027“"),
            ("one_off", "Кризис: разовый убыток", "value", "bn", ["regimes.crisis.one_off_loss.amount"], -400.0, -1000.0, 0.0, 1, "0–12 % капитала: эпизоды 2008 и 2014"),
            ("opex", "Расходы: реальный рост", "shift", "pp", ["opex.real_growth"], 0.0, -0.005, 0.025, -1, "реальный рост расходов сверх зарплат до 2030 года"),
            ("fees", "ЧКД: рост к зарплатам мира", "shift", "pp", ["fees.growth_vs_wages"], 0.0, -0.02, 0.03, 1, "история 2019–2025: рост комиссий к росту зарплат"),
            ("mortgage_sub", "Компенсация льготной ипотеки", "value", "pct", ["nii.books.mortgage_sub.spread"], 0.025, 0.020, 0.035, 1, "постановление о компенсации с 31.12.2025"),
            ("psi", "Доля текущих счетов: чувствительность к ключевой (ψ)", "value", "number", ["nii.retail_current_share.psi"], 1.12, 0.94, 1.30, -1, "калибровка ± стандартная ошибка"),
            ("misc", "Прочее ОПУ (цены 2026)", "value", "bn", ["other.misc_net_real.LT"], -200.0, -300.0, -100.0, 1, "история 2024–1П26"),
            ("noncore", "Непрофильный результат (LT, цены 2026)", "value", "bn", ["noncore.result_real.LT"], -50.0, -150.0, 0.0, 1, "история 2023–1П26; сходится к нулю"),
            ("tax_shift", "Налог: сдвиг ставки", "shift", "pp", ["tax.statutory"], 0.0, 0.0, 0.03, -1, "единственный канал изъятий государства; односторонний риск"),
            ("growth", "Реальный рост в терминале", "value", "pct", ["valuation.terminal.real_growth"], 0.015, 0.005, 0.025, 1, "рост реального ВВП сквозь цикл"),
            ("buffer", "Управленческий буфер к полу Н20.0", "value", "pct", ["capital.mgmt_buffer.n20_0"], 0.013, 0.008, 0.020, -1, "13,3 − 12,0 п.п."),
            ("deductions", "Вычеты капитала (групповая консолидация)", "value", "pct", ["capital.reg_scenarios.*.deduction_pp.LT"], 0.0045, 0.0, 0.009, -1, "доклад ЦБ 05.02.2026"),
            ("epsilon", "Выплата избытка ε с 2030", "value", "pct", ["dividends.excess.epsilon"], 0.5, 0.0, 1.0, 1, "суждение книги: половина избытка капитала"),
            ("governance", "Дисконт за управление", "value", "pct", ["valuation.governance.discount"], GOV, 0.0, 0.10, -1, "0,4 % оценки капитала — неподвижной точкой на медиане (М§8.2)"),
            ("fvoci_maturity", "Срок подтягивания FVOCI", "value", "years", ["oci.fvoci_maturity"], 4.0, 2.5, 6.0, -1, "сроки портфеля 2,5–6 лет по отчётности 2024–2026"),
            ("world_prob", "Веса миров", "dict", "dict", ["joint.world_prob"],
             {"N": 0.35, "H": 0.45, "M": 0.20}, {"N": 0.25, "H": 0.50, "M": 0.25}, {"N": 0.40, "H": 0.45, "M": 0.15}, 1, "запись миров семейства 850, версия 1.6"),
            ("regime_prob", "Вероятности режимов (P(кризис) 10–25 %)", "dict", "dict", ["joint.regime_prob"],
             {"soft": 0.15, "norm": 0.40, "downturn": 0.30, "crisis": 0.15},
             {"soft": 0.1588, "norm": 0.4235, "downturn": 0.3177, "crisis": 0.10},
             {"soft": 0.1324, "norm": 0.3529, "downturn": 0.2647, "crisis": 0.25}, -1, "частота эпизодов 2008–2025")]
    return [{"id": a, "name": n, "kind": k, "unit": u, "paths": p, "book": b, "low": lo, "high": hi, "sign": s, "source": src}
            for a, n, k, u, p, b, lo, hi, s, src in axes]


def make_judgement_rows(axes, share_of, central):
    out = []
    for i, a in enumerate(axes):
        swing = [96.0, 61.0, 49.0, 44.0, 37.0, 31.0, 25.0, 22.0, 20.0, 18.0, 15.0, 13.0, 12.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 2.0, 12.0, 28.0][i]
        # широкая сторона диапазона — снижающая цену (точка выше среднего прогонов); у двух осей — наоборот;
        # односторонняя ось (книга на краю) на этом краю даёт саму точку
        down = 0.4 if a["id"] in ("growth", "epsilon") else 0.6
        if a["kind"] != "dict" and a["book"] in (a["low"], a["high"]):
            down = 1.0
        if a["sign"] > 0:
            plo, phi = central - swing * down, central + swing * (1 - down)
        else:
            plo, phi = central + swing * (1 - down), central - swing * down
        c = share_of.get(a["id"])
        plo, phi = r(plo), r(phi)
        # сдвиг положения оси (М§10): (цена_high + цена_low − 2 × точка) / 6; у оси-словаря — null
        shift = None if a["kind"] == "dict" else r((phi + plo - 2 * r(central)) / 6, 4)
        out.append({"id": a["id"], "name": a["name"], "unit": a["unit"], "kind": a["kind"], "paths": a["paths"], "book": a["book"],
                    "low": a["low"], "high": a["high"], "dist": "triangular", "price_low": plo, "price_high": phi,
                    "swing": r(abs(phi - plo)), "mean_shift": shift, "share": c["share"] if c else None,
                    "rank_corr": c["rank_corr"] if c else None, "in_band": True, "source": a["source"]})
    out.sort(key=lambda x: -x["swing"])
    return out


def make_next_report(median, central):
    post = lambda c: {"soft": r(max(0.02, 0.35 - 18 * (c - 0.009)), 4), "crisis": r(min(0.4, 0.05 + 22 * max(0, c - 0.012)), 4)}  # noqa: E731
    cor_rows = []
    for i in range(13):
        c = round(0.009 + 0.001 * i, 3)
        d = -20.0 * math.tanh((c - 0.0118) / 0.003)
        p = post(c)
        rest = 1 - p["soft"] - p["crisis"]
        cor_rows.append({"cor": c, "point": r(central + d * 1.05), "median": r(median + d), "median_low": r(median + d - 26),
                         "median_high": r(median + d + 26), "d_point": r(d * 1.05), "d_median": r(d),
                         "posterior": {"soft": p["soft"], "norm": r(rest * 0.58, 4), "downturn": r(rest * 0.42, 4), "crisis": p["crisis"]}})
    nim_rows = []
    for i in range(9):
        v = round(0.058 + 0.001 * i, 3)
        d = (v - 0.0597) / 0.001 * 3.1
        # ожидания ЧПМ у режимов равны — строки ЧПМ вероятностей режимов не меняют (М§13)
        nim_rows.append({"nim": v, "point": r(central + d * 1.05), "median": r(median + d), "median_low": r(median + d - 26),
                         "median_high": r(median + d + 26), "d_point": r(d * 1.05), "d_median": r(d),
                         "posterior": {"soft": 0.158, "norm": 0.409, "downturn": 0.291, "crisis": 0.142}})
    return {"period": "2026Q3", "book_period": "2026Q3", "target": "median",
            "closing": {"date": "2026-10-28", "title": "МСФО за 9 месяцев 2026 г.", "confirmed": False, "published": False},
            "expectation": {"cor_mgmt": 0.0125, "nim_mgmt": 0.0597, "cor_engine": 0.0142, "nim_engine": 0.0556, "ni": 452.0,
                            "loans_ac": 50812.0, "iea": 66930.0, "tau_eff": 0.243, "days": 92,
                            "by_regime": [{"regime": g, "cor_mgmt": c, "nim_mgmt": n, "posterior": p} for g, c, n, p in (
                                ("soft", 0.011, 0.0597, 0.158), ("norm", 0.0124, 0.0597, 0.409), ("downturn", 0.0133, 0.0597, 0.291),
                                ("crisis", 0.0131, 0.0597, 0.142))]},
            "cor_table": cor_rows, "nim_table": nim_rows,
            # ni_equivalent — ЧП квартала ПРИ нейтральном значении (уровень рядом с expectation.ni), не чувствительность
            "neutral": {"cor": {"value": 0.0118, "gap_rub": 0.02, "ni_equivalent": 458.8}, "nim": {"value": 0.0597, "gap_rub": -0.03, "ni_equivalent": 452.0},
                        "point_cor": 0.0121, "point_nim": 0.0594},
            "slope": {"rub_per_01pp_cor": -6.67, "rub_per_01pp_nim": 3.1, "at_cor": "neutral", "at_nim": "neutral"},
            "reaction": {"cor": {"d_median_min": min(x["d_median"] for x in cor_rows), "d_median_max": max(x["d_median"] for x in cor_rows)},
                         "nim": {"d_median_min": min(x["d_median"] for x in nim_rows), "d_median_max": max(x["d_median"] for x in nim_rows)}},
            "benchmarks": [{"key": "same_quarter_last_year", "name": "тот же квартал год назад", "cor": 0.013, "nim": 0.057, "ni": 413.0, "note": "3К25, упр."},
                           {"key": "last_quarter", "name": "прошлый отчётный квартал", "cor": 0.012, "nim": 0.062, "ni": 433.3, "note": "2К26, упр."},
                           {"key": "guidance", "name": "гайденс года", "cor": 0.014, "nim": 0.062, "ni": None, "note": "годовые ориентиры 2026"}]}


def make_year(ni_q, n20_check):
    """Прибыль года → DPS на одном слое (П§2 `nowcast.year`): квартал смеси заголовка плюс отклонение нау-каста от своего
    ожидания; без нау-каста `dps` = `dps_model`; ошибка года — открытый квартал и оставшиеся (σ_база × ожидание)."""
    at1, fact, mix_q, sigma = 7.28, 873.4, 449.4, 0.05
    year_model = r(DPS_POLICY * N_ISS / 500 + at1)                       # прибыль года, на которой политика даёт dps_model
    rest = r(year_model - fact - mix_q)
    quarter = r(mix_q + ni_q["deviation"])
    ni_year = r(fact + quarter + rest)
    se = r(math.sqrt(ni_q["std_error"] ** 2 + (sigma * abs(rest)) ** 2))
    per = lambda x: x * 500 / N_ISS  # noqa: E731
    dps = r(per(ni_year - at1), 4)
    return {"year": 2026, "ni_fact": fact, "ni_quarter": quarter, "ni_rest": rest, "ni_year": ni_year, "ni_year_se": se,
            "at1_coupon_after_tax": at1, "base": r(ni_year - at1), "payout": 0.5, "dps": dps, "dps_interval": [r(dps - per(se), 4), r(dps + per(se), 4)],
            "dps_model": DPS_POLICY, "capital_check": {"n20_expected": n20_check, "requirement": 0.133, "ok": n20_check >= 0.133},
            "yield": {t: r(dps / PRICE[t], 6) for t in TICKERS},
            "note": "факт отчётных кварталов + смесь заголовка на открытый квартал с отклонением нау-каста и на остаток года; к цене не подключено до допуска"}


def make_nowcast(n20_check):
    months = [("2026M07", "known", "release", "2026-08-11", 152.3, 311.8, 47.2), ("2026M08", "known", "release", "2026-09-10", 148.9, 309.4, 51.8),
              ("2026M09", "estimated", "profile", None, 146.0, 305.0, 49.0)]
    rows = []
    ytd = 0.0
    for i in range(14):
        m = 8 + i
        y, mm = 2025 + (m - 1) // 12, (m - 1) % 12 + 1
        ni = 131 + 9 * math.sin(i) + i * 1.2
        ytd = ni if mm == 1 else ytd + ni
        rows.append({"month": f"{y}M{mm:02d}", "ni": r(ni, 1), "ni_ytd": r(ytd, 1), "nii": r(270 + 3 * i, 1), "fees": r(64 + 0.8 * i, 1),
                     "llp": r(38 + 2 * math.cos(i), 1), "opex": r(92 + 1.1 * i, 1), "cor": r(0.0105 + 0.001 * math.sin(i / 2), 4),
                     "roe": r(0.232 + 0.01 * math.sin(i / 3), 4), "loans_corporate": r(27900 + 180 * i, 1), "loans_retail": r(17400 + 120 * i, 1),
                     "funds_retail": r(33100 + 260 * i, 1), "funds_corporate": r(15900 + 90 * i, 1),
                     "n1_0": r(0.133 + 0.001 * math.sin(i), 4), "n1_1": r(0.118 + 0.001 * math.sin(i), 4), "source": "релиз РСБУ",
                     "published_at": f"{y + (mm == 12)}-{mm % 12 + 1:02d}-10"})
    rows[-1]["month"] = "2026M08"
    by_target = {
        "ni_q": {"ras_estimate": 438.6, "bridge": 1.011, "ras_bridged": 443.42, "expectation": 452.0, "w": 0.35, "forecast": 449.0,
                 "std_error": 19.5, "interval": [429.5, 468.5], "deviation": -3.0,
                 "equation": "прогноз = ожидание модели + w × (РСБУ квартала × сезонный мост − ожидание модели)", "version": "nowcast-2",
                 "benchmarks": [{"key": "model", "name": "ожидание модели без индикаторов", "value": 452.0},
                                {"key": "prev_quarter", "name": "прошлый квартал", "value": 433.3},
                                {"key": "same_quarter_last_year", "name": "тот же квартал год назад", "value": 413.0}],
                 "basis": "ifrs"},
        "nim_q": {"ras_estimate": 0.0571, "bridge": 1.044, "ras_bridged": 0.0596, "expectation": 0.0597, "w": 0.3, "forecast": 0.0597,
                  "std_error": 0.0012, "interval": [0.0585, 0.0609], "deviation": 0.0, "equation": "то же правило для ЧПМ квартала, упр. базис через мост",
                  "version": "nowcast-2", "benchmarks": [{"key": "model", "name": "ожидание модели без индикаторов", "value": 0.0597},
                                                       {"key": "prev_quarter", "name": "прошлый квартал", "value": 0.062}], "basis": "mgmt"},
        "cor_q": {"ras_estimate": None, "bridge": None, "ras_bridged": None, "expectation": 0.0125, "w": 0.0, "forecast": 0.0125,
                  "std_error": None, "interval": None, "deviation": 0.0,
                  "equation": "моста РСБУ → МСФО для CoR нет: прогноз — ожидание модели", "version": "nowcast-2",
                  "benchmarks": [{"key": "model", "name": "ожидание модели без индикаторов", "value": 0.0125},
                                 {"key": "prev_quarter", "name": "прошлый квартал", "value": 0.012}], "basis": "mgmt"},
    }
    return {"period_unit": "quarter", "input_unit": "month", "connected_to_price": False,
            "targets": [{"key": "ni_q", "title": "прибыль квартала", "unit": "bn", "basis": "ifrs"},
                        {"key": "nim_q", "title": "ЧПМ квартала", "unit": "share", "basis": "mgmt"},
                        {"key": "cor_q", "title": "CoR квартала", "unit": "share", "basis": "mgmt"}],
            "quarter": {"period": "2026Q3", "months": [{"month": m, "status": s, "source": src, "date_known": dk, "ni": ni, "nii": nii, "llp": llp}
                                                       for m, s, src, dk, ni, nii, llp in months],
                        "months_known": 2, "by_target": by_target},
            "year": make_year(by_target["ni_q"], n20_check),
            "months": {"basis": "ras", "rows": rows[:-1]},
            "form102": [{"month": "2026M07", "ni": 152.3, "first_seen": "2026-08-26", "release_ni": 152.3, "diff": 0.0, "ok": True},
                        {"month": "2026M08", "ni": None, "first_seen": None, "release_ni": 148.9, "diff": None, "ok": None}],
            "admission": {"rule": "4 события вне выборки; MSE прогноза не больше 0,8 MSE лучшего эталона — по ЧПМ и CoR квартала",
                          "status": "collecting", "events_needed": 4, "events_scored": 0, "mse_ratio": {"nim_q": None, "cor_q": None},
                          "first_event": "2026Q3", "earliest_decision": "2027-10-28"},
            "retro": make_retro(),
            "journal": {"entries": [
                {"id": f"{t}-2026Q3-T-30", "target": t, "period": "2026Q3", "horizon": "T-30", "recorded_at": "2026-09-28T16:05:00+00:00",
                 "release_sha": "9b0e4f6a1c2d", "book_version": BOOK_PREV, "forecast": f, "benchmarks": bm, "actual": None,
                 "errors": {"forecast": None, **{k: None for k in bm}}}
                for t, f, bm in (("ni_q", 450.8, {"model": 452.0, "prev_quarter": 433.3, "ras_bridge": 443.42, "same_quarter_last_year": 413.0}),
                                 ("nim_q", 0.0597, {"model": 0.0597, "prev_quarter": 0.062}), ("cor_q", 0.0125, {"model": 0.0125, "prev_quarter": 0.012}))],
                "total_entries": 3, "rule": "записи неизменяемы; эталоны замораживаются при первой записи; факт вносится после МСФО",
                "releases": {"9b0e4f6a1c2d": {"book_version": BOOK_PREV, "generated_at": "2026-09-28T16:04:10+00:00"}}}}


# ретро эталонов прибыли квартала (T1): факт МСФО 2023–2026 и наивные эталоны на T-30 и T-90
HIST_NI = [360.1, 362.0, 374.2, 413.4, 390.8, 404.9, 412.6, 385.3, 385.8, 405.0, 413.0, 399.6, 440.1, 433.3]
RETRO_TITLES = {"consensus": "консенсус у отчёта (за день до выхода)", "prev_quarter": "прошлый квартал",
                "ras_bridge": "РСБУ квартала (известные месяцы и профиль прошлого года) × сезонный мост",
                "same_quarter_last_year": "тот же квартал год назад", "yoy_growth": "год назад × рост г/г прошлого квартала"}


def make_retro():
    a = HIST_NI
    out = {}
    for h, lag, noise in (("T-30", 1, 0.025), ("T-90", 2, 0.05)):
        periods = []
        for i, act in enumerate(a):
            y, q = 2023 + i // 4, i % 4 + 1
            b = {}
            if i >= lag:
                b["prev_quarter"] = a[i - lag]
            if i >= 4:
                b["same_quarter_last_year"] = a[i - 4]
                b["ras_bridge"] = r(act * (1 + noise * math.sin(1.3 * i)), 1)
            if i >= 4 + lag:
                b["yoy_growth"] = r(a[i - 4] * a[i - lag] / a[i - 4 - lag], 1)
            if i % 3 != 1 and h == "T-30":
                b["consensus"] = r(act * (1 + 0.02 * math.cos(0.9 * i)), 1)
            report = f"{y}-04-28" if q == 1 else f"{y}-07-29" if q == 2 else f"{y}-10-28" if q == 3 else f"{y + 1}-02-26"
            periods.append({"period": f"{y}Q{q}", "actual": act, "report_date": report, "benchmarks": b})
        keys = sorted({k for p in periods for k in p["benchmarks"]})
        err = {k: [(p["benchmarks"][k], p["actual"]) for p in periods if k in p["benchmarks"]] for k in keys}
        out[h] = {"periods": periods, "rmse": {k: r(math.sqrt(sum(((b - x) / x) ** 2 for b, x in v) / len(v)), 6) for k, v in err.items()},
                  "bias": {k: r(sum(b - x for b, x in v) / len(v), 2) for k, v in err.items()}, "n": {k: len(v) for k, v in err.items()},
                  "main": "ras_bridge", "horizon": h, "titles": {k: RETRO_TITLES[k] for k in keys}}
    return {**out["T-30"], "by_horizon": out}


def make_indicators():
    groups = [("ras", "РСБУ банка за месяц"), ("cbr", "Формы ЦБ"), ("rates", "Ставки и кривая"), ("market", "Рынок"), ("sector", "Сектор — справочно")]
    tiles = []

    def series(n, a, b, noise, digits=4, monthly=False):
        from datetime import date, timedelta
        start = date(2025, 9, 30)
        if monthly:                                   # 12 месяцев до последнего раскрытого: 2025M09 … 2026M08
            dates = [f"{2025 + (8 + i) // 12}M{(8 + i) % 12 + 1:02d}" for i in range(n)]
        else:
            dates = [(start + timedelta(days=int(i * 365 / (n - 1)))).isoformat() for i in range(n)]
        vals = [round(a + (b - a) * i / (n - 1) + noise * math.sin(i * 1.7), digits) for i in range(n)]
        return dates, vals

    spec = [("ras_ni", "ras", "Чистая прибыль, месяц", "bn", "ras", 12, 131, 149, 6, 1, "релиз РСБУ"),
            ("ras_roe", "ras", "ROE, с начала года", "share", "ras", 12, 0.228, 0.236, 0.004, 4, "релиз РСБУ"),
            ("ras_cor", "ras", "CoR, месяц", "share", "ras", 12, 0.011, 0.0104, 0.001, 4, "релиз РСБУ"),
            ("ras_loans_c", "ras", "Корпоративные кредиты", "bn", "ras", 12, 27400, 29600, 60, 0, "релиз РСБУ"),
            ("n1_0", "cbr", "Н1.0 банка", "share", "regulatory", 12, 0.133, 0.1339, 0.001, 4, "ЦБ, форма 0409135"),
            ("n1_1", "cbr", "Н1.1 банка", "share", "regulatory", 12, 0.119, 0.1187, 0.001, 4, "ЦБ, форма 0409135"),
            ("f123_capital", "cbr", "Собственные средства банка, форма 0409123", "bn", "ras", 12, 7710, 8092.4, 30, 1, "ЦБ, форма 0409123"),
            ("key", "rates", "Ключевая ставка", "share", "market", 12, 0.165, 0.14, 0.0, 4, "ЦБ"),
            ("ofz5", "rates", "ОФЗ 5 лет (кривая)", "share", "market", 60, 0.158, 0.1614, 0.003, 4, "Мосбиржа, zcyc"),
            ("ruonia", "rates", "RUONIA", "share", "market", 60, 0.163, 0.1382, 0.001, 4, "ЦБ"),
            ("sber_close", "market", "Цена главного тикера", "price", "market", 60, 296, 272.87, 9, 2, "Мосбиржа, TQBR"),
            ("imoex", "market", "Индекс Мосбиржи", "level", "market", 60, 2890, 2710, 60, 0, "Мосбиржа"),
            ("brokers_median", "market", "Медиана целей брокеров", "price", "market", 24, 372, 385, 4, 1, "T-Invest"),
            ("liquidity", "rates", "Структурный дефицит ликвидности", "bn", "market", 30, 2400, 3223.4, 180, 1, "Банк России"),
            ("deposits", "sector", "Средняя ставка по вкладам (топ-10)", "share", "market", 30, 0.165, 0.141, 0.002, 4, "ЦБ"),
            ("sector_loans", "sector", "Корпоративный кредит, сектор, г/г", "share", "market", 12, 0.16, 0.09, 0.005, 4, "ЦБ, обзор банковского сектора"),
            ("ipoteka", "sector", "Выдачи ипотеки, месяц", "bn", "market", 12, 390, 420, 25, 0, "ЦБ")]
    for sid, grp, title, unit, basis, n, a, b, noise, dg, src in spec:
        dates, vals = series(n, a, b, noise, dg, monthly=grp in ("ras", "cbr"))
        i_lo = min(range(n), key=lambda i: vals[i])
        i_hi = max(range(n), key=lambda i: vals[i])
        tiles.append({"id": sid, "group": grp, "title": title, "unit": unit, "basis": basis, "value": vals[-1], "date": dates[-1],
                      "change": r(vals[-1] - vals[-2], 6), "change_from": dates[-2], "min": {"date": dates[i_lo], "value": vals[i_lo]},
                      "max": {"date": dates[i_hi], "value": vals[i_hi]}, "history": {"date": dates, "value": vals},
                      "source": src, "status": "ok"})
    next(x for x in tiles if x["id"] == "key")["since"] = "2026-09-12"
    tiles.append({"id": "form102_ni", "group": "cbr", "title": "Прибыль по форме 0409102, месяц", "unit": "bn", "basis": "ras", "value": None,
                  "date": None, "change": None, "change_from": None, "min": None, "max": None, "history": {"date": [], "value": []},
                  "source": "ЦБ, форма 0409102", "status": "missing", "reason": "форма за август ещё не раскрыта ЦБ"})
    return {"groups": [{"id": g, "title": t} for g, t in groups], "tiles": tiles}


NOTES = {"ras-2026M09": "дата из календаря эмитента в T-Invest; отчёт за сентябрь и 9 месяцев; в прошлые годы — 09.10.2024, 09.10.2025",
         "investor-2026": "объявлен месяц, день — оценка по прошлым годам: 06.12.2023, 10.12.2025; новая политика войдёт новой версией книги"}


def make_calendar():
    from datetime import date
    today = date.fromisoformat(VDATE)
    ev = [("ras-2026M09", "2026-10-09", "ras", "РСБУ банка за 9 месяцев 2026 г.", None, True, "day"),
          ("cbr-2026-10", "2026-10-23", "cbr_rate", "Заседание Банка России по ключевой ставке", None, True, "day"),
          ("f102-2026M09", "2026-10-24", "form102", "Форма 0409102 за сентябрь 2026 г.", None, False, "window"),
          ("ifrs-2026Q3", "2026-10-28", "ifrs", "МСФО за 9 месяцев 2026 г.", "2026Q3", False, "window"),
          ("ras-2026M10", "2026-11-11", "ras", "РСБУ банка за 10 месяцев 2026 г.", None, False, "window"),
          ("investor-2026", "2026-12-09", "investor_day", "День инвестора: новая стратегия", None, False, "window"),
          ("cbr-2026-12", "2026-12-18", "cbr_rate", "Заседание Банка России по ключевой ставке", None, True, "day"),
          ("cbr-forecast-2027", "2027-02-13", "cbr_forecast", "Опорный прогноз Банка России", None, False, "window"),
          ("ifrs-2026Q4", "2027-02-26", "ifrs", "МСФО за 2026 г.", "2026Q4", False, "window"),
          ("agm-2027", "2027-06-25", "agm", "Годовое собрание акционеров: дивиденд за 2026 г.", None, False, "window"),
          ("record-2027", "2027-07-19", "record", "Отсечка дивиденда за 2026 г.", None, False, "window"),
          ("pay-2027", "2027-08-03", "pay", "Выплата дивиденда за 2026 г.", None, False, "window")]
    events = []
    for eid, dt, kind, title, covers, conf, prec in ev:
        e = {"id": eid, "date": dt, "kind": kind, "title": title, "confirmed": conf, "precision": prec,
             "days": (date.fromisoformat(dt) - today).days}
        if covers:
            e["covers"] = covers
        if prec == "window":
            from datetime import timedelta
            d0 = date.fromisoformat(dt)
            e["earliest"] = (d0 - timedelta(days=0 if kind == "form102" else 4)).isoformat()   # у формы ЦБ дата — нижний край окна
            e["latest"] = (d0 + timedelta(days=2 if kind == "form102" else 4)).isoformat()
        if eid in NOTES:
            e["note"] = NOTES[eid]
        events.append(e)
    nf = dict(next(e for e in events if e["id"] == "ifrs-2026Q3"))
    recent = [{"id": "cbr-2026-09", "date": "2026-09-12", "kind": "cbr_rate", "title": "Заседание Банка России по ключевой ставке",
               "confirmed": True, "precision": "day", "days": -18, "note": "ставка снижена до 14 %"},
              {"id": "ras-2026M08", "date": "2026-09-10", "kind": "ras", "title": "РСБУ банка за 8 месяцев 2026 г.", "confirmed": True,
               "precision": "day", "days": -20}]
    recent = [x for x in recent if x["days"] >= -7]
    return {"today": VDATE, "next_fact": nf,
            "next_ras": {"month": "2026M09", "release_date": "2026-10-09", "release_confirmed": True, "form102_date_est": "2026-10-24",
                         "form102_latest_est": "2026-10-26", "enters_via": "release", "date": "2026-10-09", "days": 9},
            "events": events, "recent": recent}


def make_history():
    qs = []
    # 2021Q1–2022Q4: потоков МСФО квартала в фактах нет; 2021Q4–2022Q4: нет и упр. метрик (три квартала 2021 раскрыты)
    ni = [None] * 8 + [360.1, 362.0, 374.2, 413.4, 390.8, 404.9, 412.6, 385.3, 385.8, 405.0, 413.0, 399.6, 440.1, 433.3]
    cor_m = [0.007, 0.005, 0.004] + [None] * 5 + [0.013, 0.011, 0.008, 0.009, 0.012, 0.011, 0.012, 0.013, 0.013, 0.011, 0.013, 0.012, 0.014, 0.012]
    nim_m = [0.052, 0.053, 0.054] + [None] * 5 + [0.056, 0.058, 0.059, 0.059, 0.058, 0.057, 0.058, 0.058, 0.057, 0.057, 0.059, 0.060, 0.061, 0.062]
    for i in range(22):
        y, q = 2021 + i // 4, i % 4 + 1
        k = ni[i]
        qs.append({"period": f"{y}Q{q}",
                   "ifrs": {"ni": None if k is None else r(k + 4), "ni_sh": k, "nii": None if k is None else r(620 + 22 * i),
                            "fees": None if k is None else r(170 + 4 * i), "llp": None if k is None else r(95 + 4 * i),
                            "cor": None if k is None else r(cor_m[i] + 0.0017, 4), "opex": None if k is None else r(230 + 7 * i),
                            "pbt": None if k is None else r(k * 1.27), "bv": None if k is None else r(5900 + 145 * (i - 4)),
                            "roe": None if k is None else r(k * 4 / (5900 + 145 * (i - 4)), 4)},
                   "mgmt": {"nim": nim_m[i], "cor": cor_m[i], "cir": None if k is None else r(0.30 - 0.001 * (i - 4), 4),
                            "roe": None if k is None else r(k * 4 / (5900 + 145 * (i - 4)) + 0.002, 4)},
                   "n20": None if k is None else r(0.132 + 0.001 * math.sin(i), 4)})
    annual = [{"year": y, "ifrs": {"ni_sh": n, "roe": ro, "bv_end": b}, "mgmt": {"nim": nm, "cor": c, "cir": ci}, "dps": d, "payout": p, "n20_end": nn}
              for y, n, ro, b, nm, c, ci, d, p, nn in (
                  (2019, 845.0, 0.20, 4450.0, 0.054, 0.011, 0.344, 18.70, 0.50, None), (2020, 760.3, 0.161, 4969.0, 0.051, 0.013, 0.324, 18.70, 0.50, None),
                  (2021, 1245.9, 0.242, 5680.0, 0.0538, 0.006, 0.335, 0.0, 0.0, None), (2022, 270.5, 0.052, 5880.0, 0.0532, 0.019, 0.382, 25.00, 2.09, 0.117),
                  (2023, 1511.8, 0.244, 6630.0, 0.058, 0.009, 0.302, 33.30, 0.50, 0.131), (2024, 1581.6, 0.222, 7610.0, 0.058, 0.012, 0.291, 34.84, 0.50, 0.132),
                  (2025, 1707.4, 0.215, 8290.0, 0.059, 0.012, 0.296, 37.64, 0.50, 0.136))]
    return {"quarters": qs, "annual": annual, "ltm": {"as_of": FACTS_DATE, "ni_sh": 1686.0, "roe": 0.2254, "basis": "ifrs"},
            "gaps": [{"period": "2021Q1–2022Q4", "basis": "ifrs", "reason": "потоков МСФО квартала в фактах нет"},
                     {"period": "2021Q4–2022Q4", "basis": "mgmt", "reason": "управленческие метрики квартала не раскрывались"}]}


def make_checks(gap):
    inv = [("probabilities", "Вероятности клеток, режимов, сценариев и веса миров в сумме 1"),
           ("bv_identity", "Капитал: тождество чистого излишка в каждом квартале каждой клетки"),
           ("balance_identity", "Баланс: активы = пассивы + капитал"), ("pnl_identity", "ОПУ: прибыль = сумма строк"),
           ("ddm_equals_ri", "Дивидендная модель = остаточный доход в каждой клетке", "во всех клетках сетки и 2 000 прогонах полосы; наибольшая относительная разность 1,8·10⁻¹²"),
           ("dps_history", "Формула политики воспроизводит объявленный DPS до копейки"),
           ("exdate_jump", "Скачок оценки на экс-дату = −DPS"),
           ("transmission_solved", "Передача ставки решена: ЧПМ в мире H и реализованная передача M − N равны книге"),
           ("dividend_bounds", "Дивиденды неотрицательны и не больше запаса капитала"),
           ("governance_sum", "Дисконт за управление = сумма каналов"),
           ("release_numbers", "Числа выпуска конечны; печать = округлению половиной вверх; P10 ≤ медиана ≤ P90; точка на оси ставок"),
           ("book_schema", "Книга: закрытая схема, все читаемые значения заданы"),
           ("jump_guard", "Защита заголовка: скачок медианы объяснён (М§14.4)"), ("payload_contract", "Контракт выпуска и потолок 500 КБ")]
    gates = [("pb_by_world", "P/B клетки вне коридора мира", "P/B клетки мира 0,4–2,0×", [0.4, 2.0]),
             ("roe_range", "ROE года вне коридора", "ROE года 2–30 %", [0.02, 0.30]),
             ("cor_range", "CoR года вне коридора", "CoR года 0,4–5 % (движок)", [0.004, 0.05]),
             ("nim_range", "ЧПМ квартала вне коридора", "ЧПМ квартала 3,5–8 % (упр.)", [0.035, 0.08]),
             ("nim_path_joint", "Ближний путь ЧПМ выше якоря и уровня сквозь цикл",
              "ЧПМ упр. первых 8 кварталов не выше max(якорь, сквозь цикл мира) + 0,3 п.п.", {"quarters": 8, "tolerance": 0.003}),
             ("cir_range", "CIR года вне коридора", "CIR года 25–45 %", [0.25, 0.45]),
             ("roe_k_homogeneity", "Разрыв «ROE − стоимость капитала» неоднороден по мирам", "−10…+10 п.п.; разброс миров ≤ 5 п.п.", [-0.10, 0.10]),
             ("guidance_gap", "Путь года вне гайденса", "масса клеток вне гайденса", None),
             ("capital_gap", "Капитальный разрыв: норматив ниже регуляторного пола", "норматив после дивидендов не ниже пола сценария", None),
             ("k_gt_g", "Сработала защита «стоимость капитала больше роста»", "k_T > g_T во всех клетках", None),
             ("roe_gt_g", "ROE терминала не выше роста", "ROE'_T > g_T", None),
             ("terminal_share", "Доля терминала вне коридора", "15–65 %", [0.15, 0.65]),
             ("real_rate", "Реальная ставка терминала вне коридора", "2–13 %", [0.02, 0.13]),
             ("bridge_drift", "Мост упр. ↔ МСФО дрейфует", "CoR и ЧПМ ±0,4 п.п.", [-0.004, 0.004]),
             ("transmission_pairs", "Передача ставки между соседними мирами вне коридора", "каждая пара соседних миров −0,05…0,25", [-0.05, 0.25]),
             ("lt_spread_floor", "Долгосрочный спред кредитной книги к опорной ставке ниже пола",
              "LT-спред к опоре: корпоративные кредиты не ниже 0,5 п.п., ипотека и розница — 1,5 п.п.", {"corp_loans": 0.005, "mortgage": 0.015, "retail_loans": 0.015}),
             ("off_band_shift", "Оси вне полосы сдвигают заголовок", "сумма сдвигов положения осей вне полосы не больше половины шага печати", 2.5),
             ("m_crisis_vs_cbr", "Клетка «M × кризис» мягче рискового сценария ЦБ", "рост кредита 2027 0–5 %, 2028 2–7 %; CoR шока ≥ 2,4 % (упр.)", None),
             ("manual_input_overdue", "Ручной вход просрочен", "МСФО 7 дн., миры 21 дн., реестр 5 дн.", None)]
    out = []
    for name, title, text, val in gates:
        g = {"name": name, "title": title, "fired": False, "mass": 0.0, "cells": 0, "cell_list": [], "status": "quiet", "explanation": None,
             "expected_mass": None, "valid_until": None, "expiring": False, "message": None, "corridor": {"text": text, "value": val}}
        if name == "guidance_gap":
            g.update({"fired": True, "mass": 0.61, "cells": 22, "status": "explained", "expected_mass": [0.4, 0.8], "valid_until": "2026-10-28", "expiring": True,
                      "message": "путь 2026 года вне гайденса (рост комиссий: 1,2 % против гайденса 0,0 %)",
                      "cell_list": [f"{w}/{rg}/{sc}" for w in WORLDS for rg in ("soft", "norm") for sc in SCEN][:10],
                      "explanation": "Рост комиссий 2026 выше гайденса ≈0 %: модель продолжает тренд 1П26 (+5,4 % г/г) на 3К; гайденс учитывает отмену части комиссий во 2П. Влияние на цену меньше шага печати; пересмотр — по МСФО 3К26 (28.10.2026)."})
        if name == "capital_gap":
            g.update({"fired": True, "mass": gap["mass"], "cells": gap["cells"], "status": "explained", "expected_mass": [0.0, 0.05], "valid_until": "2027-03-31",
                      "message": f"норматив ниже пола в {gap['cells']} клетках",
                      "cell_list": gap["cell_list"],
                      "explanation": "Клетки «кризис × верхняя группа СЗКО / жёсткий» в мирах H и M опускают Н20.0 ниже пола в год шока (разовый убыток 400 млрд ₽ и CoR 5 % в квартале шока). Выплата кризиса отменяется, разрыв закрывается через год — так и задумано книгой."})
        out.append(g)
    flags = [("book_update", "Книгу пора обновить", False, None), ("report_fact", "Вышла МСФО, факт не внесён", False, None),
             ("explanation_expiring", "Срок объяснения проверки истекает", True, "«Путь года вне гайденса» — объяснение действует по 2026-10-28"),
             ("policy_expired", "Срок дивидендной политики истёк: действует прежняя до новой", False, None),
             ("dividend_register", "Квартал ГОСА прошёл, объявления нет в реестре", False, None),
             ("ni_jump", "Прибыль квартала г/г прыгает при неизменной ключевой", False, None),
             ("dividend_recommended", "Совет директоров рекомендовал дивиденд", False, None),
             ("price_fallback", "Живая цена не принята", False, None),
             ("ras_mismatch", "РСБУ месяца: источники расходятся", False, None)]
    return {"invariants": [{"name": n, "title": t, "ok": True, "detail": (rest or ["в допуске"])[0]} for n, t, *rest in inv], "invariants_broken": 0,
            "gates": out, "flags": [{"name": n, "title": t, "raised": rz, "detail": dt} for n, t, rz, dt in flags],
            "control_model": {"as_of": "2026-09-30", "commit": "5f1c0d9", "rows": control_rows()}}


def control_rows():
    """Сводка сверки в форме писателя: diff — в единицах строки, diff_rel — относительная, вид допуска — tol_kind."""
    spec = [("передача: сдвиг спредов активов σ0", "pp", -0.00112, -0.00112, 1e-6, "abs", None),
            ("кредитная маржа стационара, мир M", "pp", 0.0058, 0.0058, 1e-6, "abs", None),
            ("передача: реализованная M − N", "number", 0.06, 0.06, 1e-6, "abs", None),
            ("V0 слоя «свой макро-взгляд»", "bn", 9411.52, 9386.43, 0.03, "rel", None),
            ("цена: точка при λ книги", "rub", 382.0, 380.5, 0.03, "rel", None),
            ("ЧП акционерам 2027, клетка мира H, нормализация, по графику", "bn", 1861.2, 1855.9, 0.10, "rel", 44.3),
            ("резервы 2027, клетка мира N, кризис, по графику", "bn", 2034.74, 2037.56, 0.10, "rel", None),
            ("Н20.0 на конец 2027, клетка мира H, спад, по графику", "pct", 0.14894, 0.14862, 0.003, "abs", None),
            ("избыток капитала на терминале, клетка мира M, мягкая посадка, по графику", "bn", 131.4, 101.43, 584.48, "abs", None),
            ("DPS 2029, клетка мира M, спад, жёсткий", "rub", 46.12, 47.26, 0.10, "rel", 2.05),
            ("вероятность режима «кризис» после A-P2u", "pct", 0.142, 0.142, 0.02, "abs", None),
            ("мост объявленных дивидендов", "bn", 0.0, 0.0, 0.01, "rel", None)]
    rows = []
    for what, unit, core, control, tol, kind, tol_abs in spec:
        diff = r(control - core, 6)
        rel = None if core == 0 else r(control / core - 1, 6)
        ok = (abs(rel) <= tol if rel is not None else diff == 0) if kind == "rel" else abs(diff) <= tol
        if tol_abs is not None:
            ok = ok or abs(diff) <= tol_abs
        row = {"what": what, "unit": unit, "core": core, "control": control, "diff": diff, "diff_rel": rel, "tol": tol, "tol_kind": kind, "ok": ok}
        if tol_abs is not None:
            row["tol_abs"] = tol_abs
        rows.append(row)
    # шум счёта у строки с совпавшими числами: писатель сводки хранит разность как есть, на экране она — ноль
    rows[1].update({"diff": 5.55e-17, "diff_rel": 9.57e-15})
    return rows


def make_inputs():
    return {"rows": [
        {"key": "price.SBER", "name": "Цена обыкновенной акции", "value": 272.87, "unit": "rub", "as_of": VDATE, "source": "T-Invest, 18:49 МСК", "status": "ok"},
        {"key": "price.SBERP", "name": "Цена привилегированной акции", "value": 273.77, "unit": "rub", "as_of": VDATE, "source": "T-Invest, 18:49 МСК", "status": "ok"},
        {"key": "curve", "name": "Кривая ОФЗ (5 лет)", "value": 0.1597, "unit": "share", "as_of": "2026-09-30", "source": "Мосбиржа, zcyc", "status": "ok"},
        {"key": "key_rate", "name": "Ключевая ставка", "value": 0.14, "unit": "share", "as_of": "2026-09-12", "source": "ЦБ", "status": "ok"},
        {"key": "register", "name": "Реестр дивидендов", "value": 1, "unit": "count", "as_of": VDATE, "source": "T-Invest; решения ГОСА", "status": "ok"},
        {"key": "book", "name": "Книга допущений", "value": BOOK, "unit": "version", "as_of": "2026-09-30", "source": "машинная книга допущений", "status": "ok"},
        {"key": "facts", "name": "Факты якоря", "value": "2026Q2", "unit": "period", "as_of": FACTS_DATE, "source": "факты отчётности: МСФО 6М26", "status": "ok"}]}


def make_live():
    return {"applied": True, "degraded": [], "degraded_flag": False, "valuation_date": VDATE, "fetched_at": "2026-09-30T16:04:31+00:00",
            "prices": {t: {"value": PRICE[t], "date": VDATE, "time": "18:49", "accepted": True, "status": "live", "reason": None,
                           "last_accepted": {"value": PRICE[t], "date": VDATE}} for t in TICKERS},
            "curve": {"as_of": "2026-09-30", "nodes": {"1": 0.1352, "3": 0.1512, "5": 0.1597, "10": 0.1629},
                      "book_nodes": {"1": 0.136, "3": 0.1548, "5": 0.1614, "10": 0.1646}, "shift_bp": {"5": -17, "10": -17}},
            "key_rate": {"value": 0.14, "date": VDATE, "since": "2026-09-12"},
            "register": {"as_of": VDATE, "entries": 1, "note": "за 2025 выплачено 04.08.2026; новых объявлений нет"},
            "book_date": "2026-09-30", "book_age_days": 0}


def make_changes(median, central):
    rows = [("valuation_date", "Перекат даты оценки", 1.1, 1.0, "29.09.2026 → 30.09.2026"), ("register", "Реестр дивидендов", 0.0, 0.0, ""),
            ("facts", "Факты и перезаякоривание", 0.0, 0.0, ""), ("book", "Суждения книги", 2.3, 2.4, f"книга {BOOK_PREV} → {BOOK}; сюда входит и смена кода ядра"),
            ("worlds", "Миры", 0.0, 0.0, ""), ("engine", "Код ядра", 0.0, 0.0, "входит в строку «Суждения книги»"), ("rounding", "Округление", 0.0, 0.0, "")]
    return {"vs_previous": {"previous_sha": "9b0e4f6a1c2d", "previous_generated_at": "2026-09-29T16:04:55+00:00",
                            "previous_published_at": "2026-09-29T16:06:10+00:00",
                            "rows": [{"component": c, "title": t, "point_rub": p, "median_rub": m, "note": n} for c, t, p, m, n in rows],
                            "walk_book": [],
                            "total_point_rub": 3.4, "total_median_rub": 3.4,
                            "market_price": {"SBER": {"from": 270.15, "to": PRICE["SBER"]}, "SBERP": {"from": 271.02, "to": PRICE["SBERP"]}},
                            "reference": {"median": r(median - 3.4), "point": r(central - 3.4), "exdate_adjustment": 0.0},
                            "note": f"книга {BOOK_PREV} → {BOOK}; факты те же"},
            "snapshot": {"valuation_date": VDATE, "prices": dict(PRICE), "price_dates": {t: VDATE for t in TICKERS}, "register": [],
                         "book_version": BOOK, "book_digest": hashlib.sha256(b"book").hexdigest(), "facts_digest": hashlib.sha256(b"facts").hexdigest(),
                         "worlds_digest": hashlib.sha256(b"worlds").hexdigest(), "facts_date": FACTS_DATE}}


def make_valuation_history(median):
    from datetime import date, timedelta
    d0 = date(2026, 7, 1)
    rows = {"date": [], "sha": [], "median": [], "p10": [], "p25": [], "p75": [], "p90": [], "point": [], "price": {t: [] for t in TICKERS}}
    px = 300.0
    for i in range(0, 91, 3):
        day = d0 + timedelta(days=i)
        if day.isoformat() >= VDATE:
            break
        m = median - 14 + 0.18 * i - (37.64 if day.isoformat() >= "2026-07-20" else 0) + (37.64 if day.isoformat() >= "2026-07-20" else 0) * 0.0
        if day.isoformat() < "2026-07-20":
            m += 37.64
        px *= math.exp(RNG.gauss(-0.001, 0.012))
        if day.isoformat() >= "2026-07-20" and "2026-07-20" not in rows["date"] and len([x for x in rows["date"] if x >= "2026-07-20"]) == 0:
            px -= 37.64
        rows["date"].append(day.isoformat())
        rows["sha"].append(hashlib.sha256(day.isoformat().encode()).hexdigest()[:12])
        rows["median"].append(r(m))
        rows["p10"].append(r(m - 72))
        rows["p25"].append(r(m - 36))
        rows["p75"].append(r(m + 34))
        rows["p90"].append(r(m + 66))
        rows["point"].append(r(m + 12))
        for t in TICKERS:
            rows["price"][t].append(r(px * (PRICE[t] / PRICE["SBER"])))
    rows["rollbacks"] = [5]
    rows["book_changes"] = [{"date": "2026-09-15", "from": "1.1", "to": BOOK_PREV}]
    rows["rule"] = "день — последняя строка дня (МСК); старше 180 дней — недели; старше двух лет — месяцы; не больше 400 строк"
    rows["source"] = "история публикаций в репозитории данных"
    return rows


def make_book():
    """Строк книги по разделам в выпуске нет (поле снято, П§6): суждения с источником — блок `judgements`."""
    return {"version": BOOK, "date": "2026-09-30", "tag": f"book-{BOOK}", "facts_date": FACTS_DATE, "curve_as_of": "2026-09-18",
            "worlds_source": "запись миров семейства 21.09.2026, кривые 18.09.2026",
            "key_judgements": [{"id": "A-N2", "name": "ЧПМ сквозь цикл", "value": 0.06, "unit": "pct"},
                               {"id": "A-N3", "name": "Передача ставки", "value": 0.06, "unit": "number"},
                               {"id": "A-P2b", "name": "P(кризис)", "value": 0.15, "unit": "pct"}],
            "changes_last": {"from_version": BOOK_PREV, "summary": "сжатие спреда делится между кредитными книгами и средствами клиентов; угасание доходности — одностороннее; дисконт за управление 0,4 % оценки капитала"}}


# ── хэш и размер (П§0.4) ──
EXCLUDED_META = ("generated_at", "published_at", "payload_sha256", "previous_sha256", "bytes")


def content_for_hash(payload):
    p = json.loads(json.dumps(payload, ensure_ascii=False))
    for k in EXCLUDED_META:
        p["meta"].pop(k, None)
    p["live"].pop("fetched_at", None)
    p["nowcast"]["journal"].pop("releases", None)
    for e in p["nowcast"]["journal"].get("entries", []):
        e.pop("release_sha", None)
    p.pop("changes", None)
    p.pop("valuation_history", None)
    return p


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def seal(payload):
    payload["meta"]["payload_sha256"] = hashlib.sha256(canon(content_for_hash(payload))).hexdigest()
    payload["meta"]["bytes"] = 0
    for _ in range(3):
        p = json.loads(json.dumps(payload, ensure_ascii=False))
        p["meta"].pop("published_at", None)
        p["meta"].pop("bytes", None)
        payload["meta"]["bytes"] = len(json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def render(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def main(argv) -> int:
    for stream in (sys.stdout, sys.stderr):      # сообщения — по-русски и в консоли с чужой кодировкой
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    text = render(build())
    if "--check" in argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("фикстура совпадает с генератором" if ok else "фикстура разошлась с генератором")
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)}: {len(text.encode('utf-8'))} байт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
