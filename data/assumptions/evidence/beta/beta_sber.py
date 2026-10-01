# -*- coding: utf-8 -*-
"""β_E Сбербанка по правилу семейства (A-V1 книги 850oa) — лист этапа 1 (market/beta), перенесённый в книгу 1.0.

Правило (A-V1 850oa, evidence/book-1.4/beta/beta.py): полная доходность (цена + валовой дивиденд в экс-дату) против
индекса полной доходности MCFTR (брутто), НЕДЕЛЬНЫЕ доходности по последнему общему торговому дню ISO-недели,
окна 2 / 3 / 5 лет до даты рынка, МНК; se — Ньюи — Уэст (4 лага); Димсон (лаг + лид) — сверка; доходность через
разрыв торгов > 10 дней не считается (закрытие биржи 25.02–24.03.2022). У банка бета КАПИТАЛА (β_E) — без
разрычаживания и без поправки на операционный рычаг (DESIGN-850-sber §3.2). Центр — окно 3 года; диапазон — окна 2–5 лет.

Экс-дата: последний день с дивидендом = дата отсечки минус k торговых дней (k = 2 при расчётах T+2 до 28.07.2023,
k = 1 при T+1 с 31.07.2023) по торговому календарю бумаги; экс-дата — следующий торговый день. При T+1 экс-дата =
дата отсечки. Каждая экс-дата сверяется гэпом цены (блок [1]).

Входы: inputs/prices_{SBER,SBERP,MCFTR,IMOEX,MOEXFN}.csv (fetch_iss.py, MOEX ISS), дивиденды — лист решений годовых
собраний акционеров inputs/stage1/market/shares_dividends/dividends_public.csv: год прибыли, дивиденд на обыкновенную и
привилегированную акцию, дата отсечки — с публичным источником каждого года (страница «Дивиденды» на сайте банка и
новость о решении собрания). Экс-даты лист считает сам — от отсечки по торговому календарю бумаги.
Только стандартная библиотека. Детерминированно. Запуск из корня репозитория:
  python -B data/assumptions/evidence/beta/beta_sber.py   (вывод: out/beta_out.txt, out/beta_results.csv, out/beta_summary.json)
Перенос меняет только корни путей: входы — малые входы inputs/ (evidence/extract_inputs.py), пути повторяют папку передачи.
"""
import csv, datetime as dt, json, math, os, sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
INP = os.path.join(HERE, "inputs", "stage1", "market", "beta", "inputs")
DIVS = os.path.join(HERE, "inputs", "stage1", "market", "shares_dividends", "dividends_public.csv")
DPS_COLUMN = {"SBER": "dps_ordinary", "SBERP": "dps_preferred"}   # дивиденд на акцию категории — столбец листа решений
OUTD = os.path.join(HERE, "out")

END = "2026-09-29"                      # последний полный торговый день до даты работы 30.09.2026
WINDOWS = [("2 года", "2024-09-29", END), ("3 года", "2023-09-29", END), ("5 лет", "2021-09-29", END)]
REF_WINDOWS = [("с 25.03.2022", "2022-03-25", END), ("2019–2021", "2019-01-01", "2021-12-31")]
GAP = {"W": 10, "M": 45}
NW_LAGS = {"W": 4, "M": 2}
T1_SWITCH = "2023-07-31"                # переход MOEX на T+1 (последний день T+2 — 28.07.2023)
ERP = 0.0557                            # конвенция семейства (A-V2 850oa; решение владельца 30.09.2026)
W_SBER_INDEX = 0.14                     # вес Сбера в индексе для справочного «рынка без Сбера» [НП, critic.md §2.1]
MAIN = "TR→MCFTR, недели (ПРАВИЛО)"
CRASH = ("2022-02-24", "2022-03-31")    # справочно: как в research/valuation-method.md §4.3 (эти недели исключены)

LOG = []


def out(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.append(s)


def load_px(sec, is_index=False):
    px = {}
    for r in csv.DictReader(open(os.path.join(INP, f"prices_{sec}.csv"), encoding="utf-8")):
        if not is_index and float(r.get("NUMTRADES") or 0) <= 0:
            continue
        v = "" if is_index else (r.get("LEGALCLOSEPRICE") or "")
        v = v or r.get("CLOSE")
        if v in (None, ""):
            continue
        px[r["TRADEDATE"]] = float(v)
    return px


def load_divs(tk):
    """Решения о дивидендах: дата отсечки и дивиденд на акцию категории (год без выплаты — без отсечки, пропускается)."""
    rows = csv.DictReader(open(DIVS, encoding="utf-8"))
    return sorted(({"record": r["record_date"], "amount": float(r[DPS_COLUMN[tk]])} for r in rows if r["record_date"]),
                  key=lambda z: z["record"])


def ex_dates(divs, dates, shift=0):
    """{экс-дата: дивиденд}. last_cum = отсечка − k торговых дней; экс = следующий торговый день (+shift — ловушка)."""
    res, info = {}, []
    for dv in divs:
        rec = dv["record"]
        if rec <= dates[0] or rec > dates[-1]:
            continue
        k = 2 if rec < T1_SWITCH else 1
        on_or_before = [d for d in dates if d <= rec]
        if on_or_before[-1] == rec:
            i = dates.index(rec) - k
        else:  # отсечка в неторговый день: расчёты должны пройти не позже неё
            i = dates.index(on_or_before[-1]) - k + 1
        last_cum = dates[i]
        ex = dates[i + 1 + shift]
        res[ex] = res.get(ex, 0.0) + dv["amount"]
        info.append(dict(record=rec, last_cum=last_cum, ex=ex, amount=dv["amount"], k=k))
    return res, info


def tr_index(px, ex):
    dates = sorted(px)
    tr = {dates[0]: px[dates[0]]}
    for a, b in zip(dates, dates[1:]):
        tr[b] = tr[a] * (px[b] + ex.get(b, 0.0)) / px[a]
    return tr


def inv(A):
    n = len(A)
    M = [list(map(float, A[i])) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        M[c] = [x / pv for x in M[c]]
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [row[n:] for row in M]


def ols(y, X, lags):
    n, k = len(y), len(X[0])
    XtX = [[sum(X[t][i] * X[t][j] for t in range(n)) for j in range(k)] for i in range(k)]
    Xty = [sum(X[t][i] * y[t] for t in range(n)) for i in range(k)]
    Q = inv(XtX)
    b = [sum(Q[i][j] * Xty[j] for j in range(k)) for i in range(k)]
    u = [y[t] - sum(b[i] * X[t][i] for i in range(k)) for t in range(n)]
    ssr = sum(e * e for e in u)
    ym = sum(y) / n
    sst = sum((v - ym) ** 2 for v in y)
    s2 = ssr / (n - k)
    Vo = [[Q[i][j] * s2 for j in range(k)] for i in range(k)]
    g = [[X[t][i] * u[t] for i in range(k)] for t in range(n)]
    S = [[sum(g[t][i] * g[t][j] for t in range(n)) for j in range(k)] for i in range(k)]
    for l in range(1, lags + 1):
        w = 1.0 - l / (lags + 1.0)
        G = [[sum(g[t][i] * g[t - l][j] for t in range(l, n)) for j in range(k)] for i in range(k)]
        for i in range(k):
            for j in range(k):
                S[i][j] += w * (G[i][j] + G[j][i])
    QS = [[sum(Q[i][m] * S[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
    Vh = [[sum(QS[i][m] * Q[m][j] for m in range(k)) * n / (n - k) for j in range(k)] for i in range(k)]
    return b, Vo, Vh, (1 - ssr / sst) if sst > 0 else float("nan")


def period_rows(tr, mkt, freq, frm, to, anchor_weekday=None):
    """Доходности по последнему общему торговому дню ISO-недели (или недели, кончающейся в anchor_weekday) / месяца."""
    common = sorted(d for d in tr if d in mkt and frm <= d <= to)
    ends = []
    for d in common:
        dd = dt.date.fromisoformat(d)
        if freq == "W" and anchor_weekday is None:
            key = tuple(dd.isocalendar()[:2])
        elif freq == "W":
            key = (dd + dt.timedelta(days=(anchor_weekday - dd.weekday()) % 7)).isoformat()
        else:
            key = (dd.year, dd.month)
        if ends and ends[-1][0] == key:
            ends[-1] = (key, d)
        else:
            ends.append((key, d))
    rows = []
    for (_, a), (_, b) in zip(ends, ends[1:]):
        rows.append(dict(start=a, end=b, gap=(dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days,
                         rs=tr[b] / tr[a] - 1, rm=mkt[b] / mkt[a] - 1))
    return rows


def slope(yy, xx):
    a, c = sum(xx) / len(xx), sum(yy) / len(yy)
    return sum((u - a) * (v - c) for u, v in zip(xx, yy)) / sum((u - a) ** 2 for u in xx)


def stats(rows, freq, d0, d1, market_ex_w=None):
    ok = lambda r: d0 < r["end"] <= d1 and r["gap"] <= GAP[freq]
    s = [r for r in rows if ok(r)]
    n = len(s)
    if n < 12:
        return None
    y = [r["rs"] for r in s]
    if market_ex_w is not None:            # справочно: рынок без Сбера r_x = (r_m − w·r_s)/(1 − w)
        xm = [(r["rm"] - market_ex_w * r["rs"]) / (1 - market_ex_w) for r in s]
    else:
        xm = [r["rm"] for r in s]
    b, Vo, Vh, r2 = ols(y, [[1.0, x] for x in xm], NW_LAGS[freq])
    yd, Xd = [], []
    idx = {r["end"]: i for i, r in enumerate(rows)}
    for r in s:
        i = idx[r["end"]]
        if 0 < i < len(rows) - 1:
            p, nx = rows[i - 1], rows[i + 1]
            if p["gap"] <= GAP[freq] and nx["gap"] <= GAP[freq] and nx["end"] <= d1:
                yd.append(r["rs"])
                Xd.append([1.0, p["rm"], r["rm"], nx["rm"]])
    bd, _, Vdh, _ = ols(yd, Xd, NW_LAGS[freq])
    dim = bd[1] + bd[2] + bd[3]
    se_dim = math.sqrt(max(sum(Vdh[i][j] for i in (1, 2, 3) for j in (1, 2, 3)), 0.0))
    mx, my = sum(xm) / n, sum(y) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in xm) / (n - 1))
    sy = math.sqrt(sum((v - my) ** 2 for v in y) / (n - 1))
    rho = sum((a - mx) * (c - my) for a, c in zip(xm, y)) / ((n - 1) * sx * sy)
    ann = 52 if freq == "W" else 12
    b0 = slope(y, xm)
    infl = sorted(((slope(y[:i] + y[i + 1:], xm[:i] + xm[i + 1:]) - b0, s[i]["end"]) for i in range(n)), key=lambda t: -abs(t[0]))[:3]
    return dict(n=n, beta=b[1], se_ols=math.sqrt(Vo[1][1]), se_hac=math.sqrt(Vh[1][1]), r2=r2, rho=rho,
                dimson=dim, se_dim=se_dim, n_dim=len(yd), vol_s=sy * math.sqrt(ann), vol_m=sx * math.sqrt(ann),
                first=s[0]["end"], last=s[-1]["end"], infl=infl)


def main():
    mc = load_px("MCFTR", True)
    im = load_px("IMOEX", True)
    fn = load_px("MOEXFN", True)
    out("=" * 112)
    out(f"β_E СБЕРБАНКА ПО ПРАВИЛУ A-V1 850oa. Рынок: MCFTR (полная доходность, брутто). Дата окон: {END}")
    out("Цены: MOEX ISS TQBR LEGALCLOSEPRICE (дни без сделок исключены); индексы — CLOSE. Дивиденды: решения годовых собраний акционеров (дивиденд на акцию до налога).")
    out("=" * 112)
    res, csv_rows = {}, []
    for tk in ("SBER", "SBERP"):
        px = load_px(tk)
        dates = sorted(px)
        divs = load_divs(tk)
        ex, info = ex_dates(divs, dates)
        out(f"\n[1] {tk}: сверка экс-дат (экс = отсечка при T+1; = отсечка − 1 торг. день при T+2). Гэп цены ≈ −дивиденд, полная доходность дня ≈ рынку")
        for z in info:
            e = z["ex"]
            i = dates.index(e)
            prev = px[dates[i - 1]]
            mprev = [d for d in mc if d < e]
            mret = (mc[e] / mc[max(mprev)] - 1) if (e in mc and mprev) else float("nan")
            win = [dates[j] for j in range(max(1, i - 2), min(len(dates), i + 3))]
            worst = min(win, key=lambda d: px[d] / px[dates[dates.index(d) - 1]])
            out(f"  отсечка {z['record']} (T+{z['k']})  посл. день с див. {z['last_cum']}  экс {e}  "
                f"DPS {z['amount']:6.2f} = {z['amount'] / prev * 100:5.2f} % | цена {px[e] / prev - 1:+7.2%}  полная {(px[e] + z['amount']) / prev - 1:+7.2%}  "
                f"MCFTR {mret:+7.2%}  {'OK' if worst == e else 'ВНИМАНИЕ: наиб. падение ' + worst}")
        tr = tr_index(px, ex)
        ex_shift, _ = ex_dates(divs, dates, shift=1)
        tr_shift = tr_index(px, ex_shift)
        ex_early, _ = ex_dates(divs, dates, shift=-1)
        tr_early = tr_index(px, ex_early)
        variants_rows = {
            MAIN: (period_rows(tr, mc, "W", "2016-01-01", END), "W", None),
            "TR→MCFTR, месяцы": (period_rows(tr, mc, "M", "2016-01-01", END), "M", None),
            "TR→MCFTR, недели по средам": (period_rows(tr, mc, "W", "2016-01-01", END, anchor_weekday=2), "W", None),
            "цена→IMOEX (старый метод 1.3.1)": (period_rows(px, im, "W", "2016-01-01", END), "W", None),
            "TR→MOEXFN (ценовой отраслевой)": (period_rows(tr, fn, "W", "2016-01-01", END), "W", None),
            f"TR→MCFTR без Сбера (w={W_SBER_INDEX})": (period_rows(tr, mc, "W", "2016-01-01", END), "W", W_SBER_INDEX),
            "ЛОВУШКА: экс-дата +1 день": (period_rows(tr_shift, mc, "W", "2016-01-01", END), "W", None),
            "ЛОВУШКА: экс-дата −1 день": (period_rows(tr_early, mc, "W", "2016-01-01", END), "W", None),
            "TR→MCFTR, недели без 24.02–31.03.2022": (
                [r for r in period_rows(tr, mc, "W", "2016-01-01", END) if not (r["end"] >= CRASH[0] and r["start"] <= CRASH[1])], "W", None),
        }
        for lab, d0, d1 in WINDOWS + REF_WINDOWS:
            for vname, (rows, f, wx) in variants_rows.items():
                st = stats(rows, f, d0, d1, wx)
                if st:
                    res[(tk, lab, vname)] = st
                    csv_rows.append(dict(ticker=tk, window=lab, variant=vname,
                                         **{k: (round(v, 5) if isinstance(v, float) else v) for k, v in st.items() if k != "infl"}))
    out("\n[2] Беты. se — Ньюи — Уэст (недели 4 лага, месяцы 2); Димсон = β(t−1)+β(t)+β(t+1); ρ — корреляция; σ — годовая волатильность")
    out(f"  {'бумага':6s} {'окно':13s} {'вариант':38s} {'n':>4s} {'β':>6s} {'se':>5s} {'R²':>5s} {'ρ':>5s} {'Димсон':>7s} {'se':>5s} {'σ_s':>5s} {'σ_m':>5s}  период")
    for (tk, lab, vname), r in res.items():
        out(f"  {tk:6s} {lab:13s} {vname:38s} {r['n']:4d} {r['beta']:6.3f} {r['se_hac']:5.3f} {r['r2']:5.2f} {r['rho']:5.2f} "
            f"{r['dimson']:7.3f} {r['se_dim']:5.3f} {r['vol_s']:5.2f} {r['vol_m']:5.2f}  {r['first']}…{r['last']}")
    out("\n[3] Устойчивость (правило, SBER): наибольшее влияние одного наблюдения (исключение по одному)")
    for lab, _, _ in WINDOWS:
        r = res[("SBER", lab, MAIN)]
        out(f"  {lab:8s}: " + ", ".join(f"{e} {d:+.3f}" for d, e in r["infl"]))
    rule = {lab: res[("SBER", lab, MAIN)] for lab, _, _ in WINDOWS}
    rule_p = {lab: res[("SBERP", lab, MAIN)] for lab, _, _ in WINDOWS}
    c = rule["3 года"]
    lo, hi = min(r["beta"] for r in rule.values()), max(r["beta"] for r in rule.values())
    ci = (c["beta"] - 1.96 * c["se_hac"], c["beta"] + 1.96 * c["se_hac"])
    out("\n[4] ИТОГ ПО ПРАВИЛУ A-V1 (SBER, недели, полная доходность к MCFTR)")
    for lab, r in rule.items():
        out(f"  {lab:8s}: β_E {r['beta']:.3f} (se {r['se_hac']:.3f}, n {r['n']}, R² {r['r2']:.2f}; Димсон {r['dimson']:.3f});  SBERP {rule_p[lab]['beta']:.3f}")
    out(f"  ЦЕНТР (окно 3 года): β_E = {c['beta']:.3f} → в книгу {round(c['beta'], 2):.2f}; 95 % интервал выборочной ошибки {ci[0]:.2f}–{ci[1]:.2f}")
    rng = (math.floor(ci[0] * 20) / 20, math.ceil(ci[1] * 20) / 20)
    out(f"  ДИАПАЗОН ПО A-V1 (95 % интервал центра, округлённый наружу до 0,05): {rng[0]:.2f}–{rng[1]:.2f}")
    out(f"  Справочно — разброс окон 2–5 лет: {lo:.3f}–{hi:.3f}; 5 лет без недель 24.02–31.03.2022: "
        f"{res[('SBER', '5 лет', 'TR→MCFTR, недели без 24.02–31.03.2022')]['beta']:.3f}")
    out(f"  Премия β_E × ERP {ERP * 100:.2f} % = {c['beta'] * ERP * 100:.2f} п.п. (по диапазону A-V1 {rng[0] * ERP * 100:.2f}–{rng[1] * ERP * 100:.2f};"
        f" по разбросу окон {lo * ERP * 100:.2f}–{hi * ERP * 100:.2f})")
    trap = res[("SBER", "3 года", "ЛОВУШКА: экс-дата +1 день")]["beta"]
    trap_m = res[("SBER", "3 года", "ЛОВУШКА: экс-дата −1 день")]["beta"]
    out(f"  Ловушка экс-даты (3 года): +1 торговый день → β {trap:.3f}; −1 день → β {trap_m:.3f}; правило → {c['beta']:.3f}")
    summary = dict(rule="A-V1 850oa: недели, полная доходность (валовой DPS в экс-дату) к MCFTR, окна 2/3/5 лет, центр — 3 года",
                   end=END, center_window="3 года", beta_E_center=round(c["beta"], 4), beta_E_center_book=round(c["beta"], 2),
                   se_hac_center=round(c["se_hac"], 4), ci95=[round(ci[0], 3), round(ci[1], 3)],
                   windows={lab: dict(beta=round(r["beta"], 4), se=round(r["se_hac"], 4), n=r["n"], r2=round(r["r2"], 3),
                                      dimson=round(r["dimson"], 4), first=r["first"], last=r["last"]) for lab, r in rule.items()},
                   windows_SBERP={lab: round(r["beta"], 4) for lab, r in rule_p.items()},
                   range_windows=[round(lo, 4), round(hi, 4)], range_A_V1=list(rng),
                   beta_5y_ex_crash=round(res[("SBER", "5 лет", "TR→MCFTR, недели без 24.02–31.03.2022")]["beta"], 4), erp=ERP, premium_pp=round(c["beta"] * ERP * 100, 3),
                   trap_exdate_plus1_3y=round(trap, 4), trap_exdate_minus1_3y=round(res[("SBER", "3 года", "ЛОВУШКА: экс-дата −1 день")]["beta"], 4),
                   inputs={"prices": "MOEX ISS history TQBR/index (fetch_iss.py)",
                           "dividends": "решения годовых собраний акционеров: дивиденд на акцию и дата отсечки (страница «Дивиденды» на сайте банка, новости о решениях)"})
    with open(os.path.join(OUTD, "beta_summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUTD, "beta_results.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(csv_rows)
    with open(os.path.join(OUTD, "beta_out.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(LOG) + "\n")


if __name__ == "__main__":
    os.makedirs(OUTD, exist_ok=True)
    main()
