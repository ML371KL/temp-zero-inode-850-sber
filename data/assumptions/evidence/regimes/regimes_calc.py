# -*- coding: utf-8 -*-
"""Режимы доходности и кредитного цикла книги 1.0: референс-класс 2008–2026 и проверки путей книги (MODEL §3.2, §4.6, §12).

Перенос расчётов листа этапа 1 (regimes: episodes.csv, REGIMES-PROPOSAL.md) в воспроизводимый вид:
  1. классы лет 2008–2025 (кризис — ROE < 12 %; спад/хвост — CoR ≥ 1,6 %; нормализация — 1,0–1,6 %; мягкая — < 1,0 %),
     частоты, трёхлетние окна с кризисом, P(шок в окне 1,25 года) = 1 − e^(−1,25/6);
  2. CoR сквозь цикл в истории (среднее, без 2009, 2013–2025, медиана) и ожидания книги: взвешенный LT-CoR (упр. и
     движок через мост), CoR 2027, превышение над 1,2 % за 2027–2029, взвешенный LT-сдвиг ЧПМ;
  3. κ: CoR_q = a + κ·max(0, r_real(t−L) − 4) на кварталах 2014–2026 (лаги 0/2/4/6; выборки: все, без 4К14–2015,
     2020, 2022, с 2016) и с контролем падения ВВП;
  4. σ и ρ для A-P2u: шум квартального CoR вокруг уровня года и изменение ЧПМ квартал к кварталу (2016–2026);
  5. разовый убыток кризиса в долях капитала (2008, 2014, 2022).
Базис CoR — управленческий (как пути режимов книги, regimes.cor_basis: mgmt). Входы — inputs/stage1/regimes/episodes.csv
(малый вход, evidence/extract_inputs.py; источник каждого числа — столбец source листа).
Запуск из корня репозитория: python -B data/assumptions/evidence/regimes/regimes_calc.py → out/regimes_out.json
"""
from __future__ import annotations

import csv
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EPISODES = os.path.join(HERE, "inputs", "stage1", "regimes", "episodes.csv")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

RN = 4.0                 # нейтральная реальная ставка, % (ЦБ: 3,5–4,5; центр — лист этапа 1)
COR_BRIDGE = 0.0017      # мост CoR движок − упр. (evidence/bridges: +0,17 п.п., окно 1К24–2К26)
USED = ("cor", "roe", "nim", "key_rate_avg", "cpi_yoy_eoq", "real_key_rate_expost", "gdp_real_growth",
        "oneoff_loss_to_equity")


def load() -> dict:
    d: dict = {}
    for r in csv.DictReader(open(EPISODES, encoding="utf-8")):
        if r["entity"] in ("Сбер", "макро") and r["metric"] in USED and r["value"] != "":
            d.setdefault(r["metric"], {})[r["period"]] = float(r["value"])
    return d


def _q(y: int, k: int) -> str:
    return f"{k}Q{y}"


def classes(d: dict) -> dict:
    cls = {}
    for y in range(2008, 2026):
        c, roe = d["cor"][str(y)], d["roe"].get(str(y))
        if roe is not None and roe < 12:
            reg = "crisis"
        elif c >= 1.6:
            reg = "downturn"
        elif c >= 1.0:
            reg = "norm"
        else:
            reg = "soft"
        cls[y] = {"cor": c, "roe": roe, "regime": reg}
    counts = {r: sum(1 for v in cls.values() if v["regime"] == r) for r in ("soft", "norm", "downturn", "crisis")}
    win = [y for y in range(2008, 2024) if any(cls[t]["regime"] == "crisis" for t in range(y, y + 3))]
    cors = [v["cor"] for v in cls.values()]
    return {"years": {str(k): v for k, v in cls.items()}, "counts": counts, "n_years": len(cls),
            "windows3_with_crisis": len(win), "windows3_total": 16,
            "p_shock_in_1_25y": round(1 - math.exp(-1.25 / 6), 3),
            "cor_mean_2008_2025": round(st.mean(cors), 2),
            "cor_mean_ex2009": round(st.mean(v["cor"] for k, v in cls.items() if k != 2009), 2),
            "cor_mean_2013_2025": round(st.mean(v["cor"] for k, v in cls.items() if k >= 2013), 2),
            "cor_median": round(st.median(cors), 2)}


def book_expectations(book: dict) -> dict:
    """Ожидания путей книги (упр. базис; движок — через мост)."""
    reg, p = book["regimes"], book["joint"]["regime_prob"]
    lt = sum(p[r] * reg[r]["cor"]["LT"] for r in reg["ids"])
    nim_lt = sum(p[r] * reg[r]["nim_shift"]["LT"] for r in reg["ids"])
    cor27 = sum(p[r] * reg[r]["cor"]["2027"] for r in reg["ids"])
    exc = sum(p[r] * sum(reg[r]["cor"][str(y)] - 0.012 for y in (2027, 2028, 2029)) for r in reg["ids"])
    avg2729 = sum(p[r] * st.mean(reg[r]["cor"][str(y)] for y in (2027, 2028, 2029)) for r in reg["ids"])
    return {"cor_lt_mgmt": round(lt, 5), "cor_lt_engine": round(lt + COR_BRIDGE, 5), "nim_shift_lt": round(nim_lt, 5),
            "cor_2027_mgmt": round(cor27, 5), "cor_2027_2029_avg_mgmt": round(avg2729, 5),
            "excess_over_1_2pct_2027_2029_pp_years": round(100 * exc, 2)}


def ols(X: list[list[float]], y: list[float]) -> tuple[list[float], list[float], float, int]:
    n, k = len(y), len(X[0])
    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    inv = _inv(xtx)
    beta = [sum(inv[a][b] * xty[b] for b in range(k)) for a in range(k)]
    res = [y[i] - sum(X[i][j] * beta[j] for j in range(k)) for i in range(n)]
    s2 = sum(e * e for e in res) / max(1, n - k)
    ybar = st.mean(y)
    sst = sum((v - ybar) ** 2 for v in y)
    return beta, [math.sqrt(s2 * inv[i][i]) for i in range(k)], 1 - sum(e * e for e in res) / sst, n


def _inv(a: list[list[float]]) -> list[list[float]]:
    k = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(k)] for i, row in enumerate(a)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        m[c] = [x / piv for x in m[c]]
        for r in range(k):
            if r != c:
                f = m[r][c]
                m[r] = [m[r][j] - f * m[c][j] for j in range(2 * k)]
    return [row[k:] for row in m]


def kappa(d: dict) -> dict:
    qs = [_q(y, k) for y in range(2014, 2027) for k in range(1, 5)]
    rr = d["real_key_rate_expost"]

    def lag(q: str, L: int) -> str:
        idx = int(q[2:]) * 4 + int(q[0]) - 1 - L
        return _q(idx // 4, idx % 4 + 1)

    out = []
    for L in (0, 2, 4, 6):
        for sample in ("all", "ex_shocks", "2016+"):
            X, Y = [], []
            for q in qs:
                c, y = d["cor"].get(q), int(q[2:])
                if c is None or lag(q, L) not in rr:
                    continue
                if sample == "ex_shocks" and (q == "4Q2014" or y in (2015, 2020, 2022)):
                    continue
                if sample == "2016+" and y < 2016:
                    continue
                X.append([1.0, max(0.0, rr[lag(q, L)] - RN)])
                Y.append(c)
            b, se, r2, n = ols(X, Y)
            out.append({"lag_q": L, "sample": sample, "n": n, "a": round(b[0], 3), "kappa": round(b[1], 3),
                        "se": round(se[1], 3), "r2": round(r2, 3)})
    gdp = d["gdp_real_growth"]
    with_gdp = []
    for L in (0, 2, 4):
        X, Y = [], []
        for q in qs:
            c, g = d["cor"].get(q), gdp.get(q[2:])
            if c is None or g is None or lag(q, L) not in rr:
                continue
            X.append([1.0, max(0.0, rr[lag(q, L)] - RN), max(0.0, -g)])
            Y.append(c)
        b, se, r2, n = ols(X, Y)
        with_gdp.append({"lag_q": L, "n": n, "kappa": round(b[1], 3), "se_kappa": round(se[1], 3),
                         "gdp_sens": round(b[2], 3), "se_gdp": round(se[2], 3)})
    cur = [q for q in (_q(2024, k) for k in range(1, 5))] + [_q(2025, k) for k in range(1, 5)] + [_q(2026, k) for k in (1, 2)]
    excess = [round(rr[q] - RN, 2) for q in cur if q in rr]
    return {"quarterly": out, "with_gdp": with_gdp,
            "current_cycle_excess_real_rate_pp": {"min": min(excess), "max": max(excess), "n": len(excess)}}


def noise(d: dict) -> dict:
    """σ квартального CoR вокруг уровня года (тихие годы) и изменения ЧПМ квартал к кварталу, 2016–2026."""
    dev, dev_x, dq = [], [], []
    quiet = []
    for y in range(2016, 2026):
        qs = [d["cor"].get(_q(y, k)) for k in range(1, 5)]
        if any(v is None for v in qs):
            continue
        cls = "crisis" if (d["roe"].get(str(y), 99) < 12) else ("downturn" if d["cor"][str(y)] >= 1.6 else "quiet")
        if cls != "quiet":
            continue
        quiet.append(y)
        m = st.mean(qs)
        dev += [v - m for v in qs]
        qx = [v for k, v in zip(range(1, 5), qs) if _q(y, k) != "2Q2019"]   # 2К19 — разовый роспуск (Агрокор)
        dev_x += [v - st.mean(qx) for v in qx]
    for y in range(2016, 2027):
        for k in range(1, 5):
            a, b = d["cor"].get(_q(y, k)), d["cor"].get(_q(y - (k == 1), 4 if k == 1 else k - 1))
            if a is not None and b is not None:
                dq.append(a - b)
    nim_q = [(y, k, d["nim"][_q(y, k)]) for y in range(2016, 2027) for k in range(1, 5) if _q(y, k) in d["nim"]]
    dn = [b[2] - a[2] for a, b in zip(nim_q, nim_q[1:])
          if (b[0] * 4 + b[1]) - (a[0] * 4 + a[1]) == 1]
    return {"quiet_years": quiet, "cor_dev_from_year_sd_pp": round(math.sqrt(sum(x * x for x in dev) / (len(dev) - len(quiet))), 3),
            "cor_dev_n": len(dev),
            "cor_dev_from_year_sd_pp_ex_2Q2019": round(math.sqrt(sum(x * x for x in dev_x) / (len(dev_x) - len(quiet))), 3), "cor_qoq_sd_pp": round(st.stdev(dq), 3), "cor_qoq_n": len(dq),
            "nim_qoq_sd_pp": round(st.stdev(dn), 3), "nim_qoq_n": len(dn)}


def compute() -> dict:
    d = load()
    book = evlib.template()
    return {"classes": classes(d), "book": book_expectations(book), "kappa": kappa(d), "noise": noise(d),
            "oneoff_loss_to_equity_pct": {k: v for k, v in sorted(d["oneoff_loss_to_equity"].items())},
            "oneoff_book_to_bv0": round(book["regimes"]["crisis"]["one_off_loss"]["amount"] / 8387.2, 4)}


def compare(res: dict, book: dict) -> list[str]:
    """Проверки путей книги (REGIMES-PROPOSAL §8): частота, несмещённость ЧПМ, согласие с историей."""
    bad = []
    b = res["book"]
    if not 0.014 <= b["cor_lt_mgmt"] <= 0.017:
        bad.append(f"взвешенный LT-CoR книги {b['cor_lt_mgmt']} вне 1,4–1,7 % (история 2013–2025 / 2008–2025)")
    if abs(b["nim_shift_lt"]) > 0.0005:
        bad.append(f"взвешенный LT-сдвиг ЧПМ {b['nim_shift_lt']} не несмещён (|x| > 0,05 п.п.)")
    if abs(sum(book["joint"]["regime_prob"].values()) - 1) > 1e-12:
        bad.append("вероятности режимов не в сумме 1")
    return bad


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    res["book_mismatches"] = compare(res, evlib.template())
    evlib.write_json(evlib.Path(OUT) / "regimes_out.json", res)
    c = res["classes"]
    print("классы 2008–2025:", c["counts"], "окна с кризисом:", c["windows3_with_crisis"], "/ 16; P(шок за 1,25 г.):", c["p_shock_in_1_25y"])
    print("CoR сквозь цикл:", {k: v for k, v in c.items() if k.startswith("cor_")})
    print("книга:", res["book"])
    for r in res["kappa"]["quarterly"]:
        print("κ", r)
    for r in res["kappa"]["with_gdp"]:
        print("κ+ВВП", r)
    print("текущий цикл:", res["kappa"]["current_cycle_excess_real_rate_pp"])
    print("шум:", res["noise"])
    print("разовый убыток:", res["oneoff_loss_to_equity_pct"], "книга / BV0:", res["oneoff_book_to_bv0"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
