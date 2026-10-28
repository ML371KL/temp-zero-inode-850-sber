"""Факты: тождества и сверки сумм на самих фактах (без папки передачи).

Баланс якоря по книгам сходится; книги ЧПД дают ЧПД отчёта; строки ОПУ дают PBT; DPS
2023–2025 воспроизводится формулой политики с округлением вверх до копейки (Decimal);
мосты — средние своей истории (упр. ↔ движок: CoR, ЧПМ, CIR; РСБУ → МСФО: ЧП, ЧПД, процентные
активы); акции в обращении = размещённые − собственные; календарь и аналоги — в формате SCHEMA.md.

От «сегодня» тесты такта не зависят: срок дивидендной политики — флаг выпуска `policy_expired`, а не
красный такт (MODEL §14.3); «календарь опережает сегодня» — тест срока (метка `deadline`: в CI и в
ручном прогоне, не на сервере).
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact          # быстрые (≈1 с): тождества фактов идут в такте сервера

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
TOL = 0.05          # млрд ₽: числа отчёта — с одним знаком после запятой


def load(name: str) -> dict:
    return json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))


def v(node) -> float:
    assert isinstance(node, dict) and node.get("v") is not None, f"нет числа: {node!r}"
    return float(node["v"])


# ------------------------------------------------------------------ баланс якоря

def test_balance_identity_by_books():
    b = load("balance")
    L = b["loans_ac_gross"]
    loans_ac = sum(v(L[k]) for k in ("commercial", "project", "mortgage_market", "mortgage_subsidized",
                                     "consumer", "cards", "auto"))
    assert abs(loans_ac - v(L["total"])) < TOL, "продукты ≠ итог кредитов АС"
    assert abs(v(L["mortgage_market"]) + v(L["mortgage_subsidized"]) - v(L["mortgage"])) < TOL
    sec = sum(v(b["securities"][k]) for k in ("fvoci_debt", "fvoci_repo", "ac", "ac_repo", "fvtpl_bonds"))
    assert abs(sec - v(b["securities"]["debt_total"])) < TOL
    liq = sum(v(b["liquidity"][k]) for k in ("cash", "cbr", "due_from_banks"))
    assert b["liquidity"]["reverse_repo"]["v"] is None     # внутри средств в банках
    assets = loans_ac + v(b["loans_fvtpl"]) - v(b["allowance_ac"]) + sec + liq + v(b["other_assets"])
    assert abs(assets - v(b["total_assets"])) < TOL, f"активы по книгам {assets} ≠ {v(b['total_assets'])}"
    funds = sum(v(b["funds"][k]) for k in ("retail_current", "retail_term", "corp_current", "corp_term"))
    wh = sum(v(b["wholesale"][k]) for k in ("banks", "issued_debt", "subordinated"))
    assert b["wholesale"]["repo_cbr"]["v"] is None           # внутри средств банков
    liab = funds + wh + v(b["other_liabilities"]) + v(b["dividends_payable"])
    assert abs(liab - v(b["total_liabilities"])) < TOL
    assert abs(liab + v(b["equity"]["total"]) - v(b["total_assets"])) < TOL, "пассивы + капитал ≠ активы"
    e = b["equity"]
    assert abs(v(e["total"]) - v(e["at1"]) - v(e["nci"]) - v(e["bv_common"])) < TOL
    assert abs(v(e["shareholders"]) - v(e["at1"]) - v(e["bv_common"])) < TOL
    assert v(b["allowance_ac"]) > 0, "резерв — положительной величиной (в активах вычитается)"
    iea = loans_ac + v(b["loans_fvtpl"]) + sec + liq
    assert abs(iea - v(b["iea"])) < TOL


def test_dividends_payable_matches_declared_pool():
    """Дивиденды к выплате на якоре — объявленный пул за вычетом части на собственные акции, пока срок выплаты позже
    якоря (якорь 2К26: 841,0); выплаченный до якоря дивиденд обязательства не оставляет (якорь 3К26: 0)."""
    b, d = load("balance"), load("dividends")
    y = b["dividends_payable_year"]
    rec = next(r for r in d["history"] if r["year"] == y)
    pool = v(rec["pool_declared"])
    if rec["pay_date"] <= b["as_of"]:
        assert v(b["dividends_payable"]) == 0.0, "дивиденд выплачен до якоря — к выплате 0"
        return
    assert 0 < pool - v(b["dividends_payable"]) < 0.02 * pool, "DP = пул − часть на собственные акции"


def test_balance_history_bv():
    h = load("balance")["history"]
    b = load("balance")
    last = h[load("anchor")["period"]]
    assert abs(v(last["bv_common"]) - v(b["equity"]["bv_common"])) < TOL
    assert abs(v(last["iea"]) - v(b["iea"])) < TOL


# ------------------------------------------------------------------ книги ЧПД

BOOKS = ("corp_loans", "mortgage", "mortgage_sub", "retail_loans", "securities", "liquidity",
         "retail_current", "retail_term", "corp_funds", "wholesale")
ASSETS = BOOKS[:6]


def test_nii_books_close_equals_balance():
    n, b = load("nii_books"), load("balance")
    L = b["loans_ac_gross"]
    expect = {
        "corp_loans": v(L["commercial"]) + v(L["project"]) + v(b["loans_fvtpl"]),
        "mortgage": v(L["mortgage_market"]), "mortgage_sub": v(L["mortgage_subsidized"]),
        "retail_loans": v(L["consumer"]) + v(L["cards"]) + v(L["auto"]),
        "securities": v(b["securities"]["debt_total"]), "liquidity": v(b["liquidity"]["total"]),
        "retail_current": v(b["funds"]["retail_current"]), "retail_term": v(b["funds"]["retail_term"]),
        "corp_funds": v(b["funds"]["corp_current"]) + v(b["funds"]["corp_term"]),
        "wholesale": sum(v(b["wholesale"][k]) for k in ("banks", "issued_debt", "subordinated")),
    }
    assert set(n["books"]) == set(BOOKS), "10 книг схемы (MODEL §4.1)"
    for bk, val in expect.items():
        assert abs(v(n["books"][bk]["balance_close"]) - val) < TOL, bk


def test_nii_books_rates_and_total():
    n = load("nii_books")
    days = v(n["days"])
    books = n["books"]
    # Якорь, полученный перезаякориванием (MODEL §15.2 п. 4): ставки книг активов несут процентный доход
    # вне книг — остаток ЧПД разнесён по ним пропорционально их процентам; флаг — `rates_carry_residual`.
    carry = n["rates_carry_residual"]
    assert isinstance(carry, bool)
    scale = 1.0
    if carry:
        income = sum(v(books[b]["interest_q"]) for b in ASSETS)
        expense = sum(v(books[b]["interest_q"]) for b in BOOKS[6:])
        scale = 1 + (v(n["nii_q"]) - (income - expense - v(n["dia_q"]))) / income
    for bk in BOOKS:
        r = books[bk]
        avg = (v(r["balance_open"]) + v(r["balance_close"])) / 2
        assert abs(v(r["balance_avg"]) - avg) < 1e-6
        implied = v(r["interest_q"]) * (scale if bk in ASSETS else 1.0) / avg * 365 / days
        assert abs(implied - v(r["rate_anchor"])) < 2e-5, f"{bk}: ставка ≠ проценты / средний остаток (act/365)"
        assert 0 < v(r["rate_anchor"]) < 0.5
    assert v(books["mortgage"]["rate_anchor"]) == v(books["retail_loans"]["rate_anchor"]), \
        "ипотека и прочая розница — одна ставка (решения ведущего §2)"
    loans_int = sum(v(books[b]["interest_q"]) for b in ("corp_loans", "mortgage", "mortgage_sub", "retail_loans"))
    assert abs(loans_int - v(n["interest_loans_q"])) < 0.05
    ii = sum(v(books[b]["interest_q"]) for b in ASSETS)
    ie = sum(v(books[b]["interest_q"]) for b in BOOKS[6:])
    nii = ii - ie - v(n["dia_q"]) + v(n["other_interest_net_q"])
    assert abs(nii - v(n["nii_q"])) < 0.1, f"Σ книг {nii} ≠ ЧПД отчёта {v(n['nii_q'])}"
    iea = sum(v(books[b]["balance_avg"]) for b in ASSETS)
    assert abs(iea - v(n["iea_avg"])) < 1e-3
    assert abs(v(n["nii_q"]) * 365 / days / iea - v(n["nim_eng_q"])) < 1e-6
    b = load("balance")
    rc, rt = v(b["funds"]["retail_current"]), v(b["funds"]["retail_term"])
    assert abs(rc / (rc + rt) - v(n["current_share_anchor"])) < 1e-6


def test_key_average_anchor_quarter():
    k = v(load("nii_books")["key_avg_anchor_q"])
    assert 0.05 < k < 0.30


# ------------------------------------------------------------------ ОПУ

def test_pnl_identities():
    q = load("pnl_quarterly")["quarters"]
    checked = 0
    for p, r in q.items():
        assert abs(v(r["fee_income"]) + v(r["fee_expense"]) - v(r["fees_net"])) < TOL, p
        assert abs(v(r["pbt"]) + v(r["tax"]) - v(r["ni"])) < 0.15, f"{p}: PBT + налог ≠ ЧП"
        assert abs(v(r["ni_shareholders"]) + v(r["ni_nci"]) - v(r["ni"])) < 0.15, f"{p}: ЧП акционерам + NCI ≠ ЧП"
        if r["misc_net"]["v"] is None:
            assert r["insurance_net"]["v"] is None or r["noncore_net"]["v"] is None
            continue
        s = sum(v(r[k]) for k in ("nii", "llp_debt_fa", "fees_net", "insurance_net", "noncore_net", "opex", "misc_net"))
        assert abs(s - v(r["pbt"])) < TOL, f"{p}: сумма строк {s} ≠ PBT {v(r['pbt'])}"
        checked += 1
    assert checked >= 8
    # знаки как в отчётности
    last = q[load("anchor")["period"]]
    assert v(last["opex"]) < 0 and v(last["llp_debt_fa"]) < 0 and v(last["tax"]) < 0 and v(last["dia"]) < 0


def test_pnl_annual_sums_match_dividend_base():
    q = load("pnl_quarterly")["quarters"]
    for rec in load("dividends")["history"]:
        y = rec["year"]
        s = sum(v(q[f"{y}Q{k}"]["ni_shareholders"]) for k in range(1, 5))
        assert abs(s - v(rec["ni_shareholders"])) < 0.15, f"{y}: Σ кварталов ≠ ЧП акционерам года"


def test_at1_coupon_only_in_q4():
    """Купон AT1 — величиной и только в квартале выплаты: в нём он раскрыт (год − 9 месяцев), а не null;
    Σ кварталов года = купону года в dividends.history и capital.at1.coupon_annual."""
    q = load("pnl_quarterly")["quarters"]
    cap = load("capital")
    cq = int(v(cap["at1"]["coupon_quarter"]))
    for p, r in q.items():
        if int(p[-1]) != cq:
            assert r["at1_coupon"]["v"] == 0, p
        else:
            assert v(r["at1_coupon"]) > 0 and r["at1_coupon"].get("src"), f"{p}: купон квартала выплаты — числом с документом"
    for rec in load("dividends")["history"]:
        y = rec["year"]
        s = sum(v(q[f"{y}Q{k}"]["at1_coupon"]) for k in range(1, 5))
        assert abs(s - v(rec["at1_coupon"])) < TOL, f"{y}: Σ кварталов купона ≠ купону года"
    last = max(load("dividends")["history"], key=lambda r: r["year"])
    assert abs(v(last["at1_coupon"]) - v(cap["at1"]["coupon_annual"])) < TOL


# ------------------------------------------------------------------ DPS 2023–2025 (MODEL §5.2, прил. C)

def ceil_kopeck(x: Decimal) -> Decimal:
    return (x * 100).to_integral_value(rounding=ROUND_CEILING) / 100


def half_up_kopeck(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")


def dps_exact(rec: dict, payout: Decimal, n_iss: Decimal) -> Decimal:
    ni = Decimal(str(rec["ni_shareholders"]["v"]))
    cpn = Decimal(str(rec["at1_coupon"]["v"]))
    tau = Decimal(str(rec["tax_statutory"]["v"]))
    return payout * (ni - cpn * (1 - tau)) * 1000 / n_iss


def test_dps_formula_ceil_kopeck_2023_2025():
    d, s = load("dividends"), load("shares")
    n_iss = Decimal(str(s["issued_total"]["v"]))
    payout = Decimal(str(d["policy"]["payout"]["v"]))
    years = sorted(r["year"] for r in d["history"])
    # годы подряд: от первого года действующей политики до последнего объявленного дивиденда
    assert len(years) >= 3 and years == list(range(years[0], years[-1] + 1))
    assert years[0] == dt.date.fromisoformat(d["policy"]["approved"]).year
    assert years[-1] == load("balance")["dividends_payable_year"]
    for rec in d["history"]:
        exact = dps_exact(rec, payout, n_iss)
        declared = Decimal(str(rec["dps_ordinary"]["v"])).quantize(Decimal("0.01"))
        assert ceil_kopeck(exact) == declared, f"{rec['year']}: {exact} → {ceil_kopeck(exact)} ≠ {declared}"
        assert rec["dps_preferred"]["v"] == rec["dps_ordinary"]["v"]


def test_dps_half_up_breaks_2023_and_tau25_breaks_2024():
    """Почему «вверх до копейки» и законная ставка года (MODEL прил. C)."""
    d, s = load("dividends"), load("shares")
    n_iss = Decimal(str(s["issued_total"]["v"]))
    payout = Decimal(str(d["policy"]["payout"]["v"]))
    by = {r["year"]: r for r in d["history"]}
    assert half_up_kopeck(dps_exact(by[2023], payout, n_iss)) != Decimal(str(by[2023]["dps_ordinary"]["v"]))
    alt = dict(by[2024], tax_statutory={"v": 0.25})
    assert ceil_kopeck(dps_exact(alt, payout, n_iss)) != Decimal(str(by[2024]["dps_ordinary"]["v"])).quantize(Decimal("0.01"))


def test_pool_declared_close_to_formula():
    d, s = load("dividends"), load("shares")
    n_iss = s["issued_total"]["v"]
    for rec in d["history"]:
        pool = rec["dps_ordinary"]["v"] * n_iss / 1000
        assert abs(pool - v(rec["pool_declared"])) < 0.15, rec["year"]


def test_register_seed_records():
    d = load("dividends")
    for rec in d["register_seed"]:
        assert rec["status"] in ("declared", "recommended", "paid")
        assert rec["year"] and rec["dps"]


def _add_years(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def test_policy_term_is_computed_from_the_document():
    """Срок политики — дата утверждения + срок по документу (п. 6.4: 3 года), а не null и не литерал;
    источник срока — страница Положения в реестре документов."""
    pol = load("dividends")["policy"]
    approved = dt.date.fromisoformat(pol["approved"])
    until = dt.date.fromisoformat(pol["valid_until"])
    assert until == _add_years(approved, 3), "valid_until ≠ утверждение + 3 года (п. 6.4)"
    assert "6.4" in pol["valid_until_note"] and "3 года" in pol["valid_until_note"]
    assert pol["sha256"][:16] in pol["valid_until_src"] and "с. 7" in pol["valid_until_src"]


def test_policy_term_note_says_what_happens_after_the_term():
    """Истёкший срок политики выпуск не блокирует (AUDIT-2 № 93, решение В17): в фактах — дата срока и
    записка словами, что будет после неё; сравнения срока с «сегодня» в тестах нет — об истёкшем сроке
    сообщает флаг выпуска. Записка печатается на витрине: дата срока — в ней же, с годом."""
    pol = load("dividends")["policy"]
    until = dt.date.fromisoformat(pol["valid_until"])
    note = pol["valid_until_note"]
    assert until.strftime("%d.%m.%Y") in note, "в записке о сроке нет самой даты срока"
    assert "по прежней" in note and "истёкшем сроке" in note, "записка не говорит, что будет после срока"
    assert note[0].isupper() and not note.endswith("."), "записка — фраза с прописной, точку ставит витрина"


# ------------------------------------------------------------------ акции

def test_shares_arithmetic():
    s = load("shares")
    assert abs(v(s["issued_ordinary"]) + v(s["issued_preferred"]) - v(s["issued_total"])) < 1e-6
    assert abs(v(s["issued_ordinary"]) - v(s["treasury_ordinary"]) - v(s["outstanding_ordinary"])) < 1e-6
    assert abs(v(s["issued_preferred"]) - v(s["treasury_preferred"]) - v(s["outstanding_preferred"])) < 1e-6
    assert abs(v(s["outstanding_ordinary"]) + v(s["outstanding_preferred"]) - v(s["outstanding_total"])) < 1e-6
    assert v(s["outstanding_total"]) < v(s["issued_total"])


# ------------------------------------------------------------------ капитал

def test_capital_anchor_consistency():
    c, b = load("capital"), load("balance")
    assert c["fvoci_reserve"]["v"] == b["equity"]["fvoci_reserve"]["v"]
    assert abs(v(c["at1"]["amount"]) - v(b["equity"]["at1"])) < TOL
    ratio = v(c["basel"]["cet1"]) / v(c["basel"]["rwa"])
    assert abs(ratio - v(c["basel"]["cet1_ratio"])) < 0.0006, "CET1 / RWA ≠ коэффициент (округление 0,1 п.п.)"
    # Н20.0 «до вычета» объявленного дивиденда, пока отсечка позже якоря (вычет — в месяце отсечки)
    rec = next(r for r in load("dividends")["history"] if r["year"] == b["dividends_payable_year"])
    assert c["n20_0"]["pre_dividend"] is (rec["record_date"] > b["as_of"])
    for k in ("1", "3", "5", "10"):
        assert 0.0 < v(c["ofz_curve_anchor"][k]) < 0.4
    # вычеты якоря (книга: Ded20_0 = BV − CET1 по Базелю)
    ded20 = v(b["equity"]["bv_common"]) - v(c["basel"]["cet1"])
    assert 0 < ded20 < v(b["equity"]["bv_common"])


# ------------------------------------------------------------------ мосты

def test_bridge_mgmt_values_are_window_means():
    br = load("bridge_mgmt_ifrs")
    lo, hi = br["window"]
    for x in ("cor", "nim", "cir"):
        rows = [h for h in br[x]["history"] if lo <= h["period"] <= hi]
        gaps = [v(h["gap"]) for h in rows]
        assert len(gaps) == int(v(br[x]["n"]))
        assert abs(statistics.fmean(gaps) - v(br[x]["value"])) < 1e-6
        assert abs(statistics.stdev(gaps) - v(br[x]["sd"])) < 1e-6
        for h in br[x]["history"]:
            if h["gap"]["v"] is not None:
                assert abs(v(h["engine"]) - v(h["mgmt"]) - v(h["gap"])) < 1e-6
    assert 0.0 < v(br["cor"]["value"]) < 0.004, "CoR движка выше упр. (≈ +0,17 п.п.)"
    assert -0.006 < v(br["nim"]["value"]) < 0.0, "ЧПМ движка ниже упр. (≈ −0,4 п.п.)"
    assert -0.04 < v(br["cir"]["value"]) < 0.0, "CIR движка ниже упр. (≈ −1,9 п.п., sanity-val S7)"


def test_bridge_cir_engine_definition():
    """CIR движка = −opex / (nii + fees_net + insurance_net + misc_net) квартала (MODEL §4.6); год — суммами."""
    br, q = load("bridge_mgmt_ifrs")["cir"], load("pnl_quarterly")["quarters"]
    income = ("nii", "fees_net", "insurance_net", "misc_net")
    for row in br["history"]:
        r = q[row["period"]]
        if any(r[k]["v"] is None for k in income):
            assert row["engine"]["v"] is None and row["engine"]["calc"], row["period"]
            continue
        assert abs(-v(r["opex"]) / sum(v(r[k]) for k in income) - v(row["engine"])) < 1e-6, row["period"]
    assert br["annual"], "нет годовой сверки CIR"
    for y, a in br["annual"].items():
        rows = [q[f"{y}Q{k}"] for k in range(1, 5)]
        eng = -sum(v(r["opex"]) for r in rows) / sum(v(r[k]) for r in rows for k in income)
        assert abs(eng - v(a["engine"])) < 1e-6 and abs(v(a["engine"]) - v(a["mgmt"]) - v(a["gap"])) < 1e-6, y


def test_bridge_cor_engine_definition():
    """CoR движка = −llp_debt_fa / средние валовые кредиты АС × 365/d (MODEL §4.6)."""
    br, q, h = load("bridge_mgmt_ifrs"), load("pnl_quarterly")["quarters"], load("balance")["history"]
    for row in br["cor"]["history"]:
        p = row["period"]
        y, k = int(p[:4]), int(p[-1])
        prev = f"{y - 1}Q4" if k == 1 else f"{y}Q{k - 1}"
        if prev not in h:
            continue
        end = dt.date.fromisoformat(h[p]["date"])
        start = dt.date.fromisoformat(h[prev]["date"])
        avg = (v(h[p]["loans_ac_gross"]) + v(h[prev]["loans_ac_gross"])) / 2
        eng = -v(q[p]["llp_debt_fa"]) / avg * 365 / (end - start).days
        assert abs(eng - v(row["engine"])) < 1e-6, p


def test_bridge_ras_ifrs():
    br = load("bridge_ras_ifrs")
    assert br["basis"] == "RAS→IFRS"
    by = {k: [] for k in ("1", "2", "3", "4")}
    for r in br["quarters"]:
        ratio = v(r["ifrs_ni_sh"]) / v(r["ras_ni"])
        assert abs(ratio - v(r["ratio"])) < 1e-6
        by[r["period"][-1]].append(ratio)
    for k, xs in by.items():
        assert xs, k
        assert abs(statistics.fmean(xs) - v(br["by_quarter"][k])) < 1e-6
    assert v(br["by_quarter"]["1"]) > v(br["by_quarter"]["4"]), "сезонность: К1 выше К4"


def _ratio_block(b: dict) -> None:
    """Общее для мостов ЧПД и процентных активов: отношение, окно, среднее, by_quarter = value, бэктест."""
    lo, hi = b["window"]
    ps = [r["period"] for r in b["quarters"]]
    assert ps == sorted(ps) and len(set(ps)) == len(ps) and hi == ps[-1] and lo in ps
    for r in b["quarters"]:
        assert abs(v(r["ifrs"]) / v(r["ras"]) - v(r["ratio"])) < 1e-6, r["period"]
        assert r["src"] and "sha256" in r["src"], r["period"]
    xs = [v(r["ratio"]) for r in b["quarters"] if lo <= r["period"] <= hi]
    assert len(xs) == int(v(b["n"])) and abs(statistics.fmean(xs) - v(b["value"])) < 1e-6
    assert all(v(b["by_quarter"][k]) == v(b["value"]) for k in "1234"), "by_quarter = value (сезонности нет)"
    bt = b["backtest"]
    assert v(bt["prev_quarter"]) <= v(bt["same_quarter"]), "бэктест: сезонный мост не лучше прошлого квартала"


def test_bridge_ras_nii():
    """ЧПД РСБУ → МСФО (T3): МСФО — ЧПД pnl_quarterly того же квартала; 2025–2026 — 1,12–1,18 (этап 1: 1,135–1,170)."""
    b, q = load("bridge_ras_ifrs")["nii"], load("pnl_quarterly")["quarters"]
    _ratio_block(b)
    for r in b["quarters"]:
        assert v(r["ifrs"]) == v(q[r["period"]]["nii"]), r["period"]
        if r["period"] >= "2025Q1":
            assert 1.12 <= v(r["ratio"]) <= 1.18, r["period"]
    assert [r["period"] for r in b["quarters"]] == sorted(q), "ЧПД РСБУ — за каждый квартал МСФО"


def test_bridge_ras_iea():
    """Процентные активы: МСФО — среднее balance.history.iea концов квартала; коды формы 0409101 названы."""
    b, h = load("bridge_ras_ifrs")["iea"], load("balance")["history"]
    _ratio_block(b)
    for r in b["quarters"]:
        p = r["period"]
        y, k = int(p[:4]), int(p[-1])
        prev = f"{y - 1}Q4" if k == 1 else f"{y}Q{k - 1}"
        assert abs((v(h[prev]["iea"]) + v(h[p]["iea"])) / 2 - v(r["ifrs"])) < TOL, p
        assert 1.0 < v(r["ratio"]) < 1.2, p
    codes = [c for cs in b["codes"].values() for c in cs]
    assert set(b["codes_required"]) <= set(codes) and all(isinstance(c, str) for c in codes)


# ------------------------------------------------------------------ ключевая, «прочее»

def test_key_avg_anchor_quarter_matches_nii_books():
    q, nb, anchor = load("pnl_quarterly")["quarters"], load("nii_books"), load("anchor")["period"]
    assert abs(v(q[anchor]["key_avg"]) - v(nb["key_avg_anchor_q"])) < 1e-9


def test_misc_net_mass_in_q4():
    """«Прочее» ОПУ сосредоточено в 4К (факт для ключа книги other.misc_quarter_shares, A9): в каждом
    полном году с раскрытым «прочим» доля 4К больше половины годовой суммы."""
    q = load("pnl_quarterly")["quarters"]
    years = sorted({p[:4] for p in q if all(f"{p[:4]}Q{k}" in q for k in range(1, 5))})
    full = [y for y in years if all(q[f"{y}Q{k}"]["misc_net"]["v"] is not None for k in range(1, 5))]
    assert full, "нет полного года «прочего»"
    for y in full:
        xs = [v(q[f"{y}Q{k}"]["misc_net"]) for k in range(1, 5)]
        assert xs[3] / sum(xs) > 0.5, y


def test_mgmt_derived_metrics_carry_calc():
    """Метрика, которой на странице нет (выведена из значения сравнимого периода и изменения к нему), несёт
    вывод в `calc`; где число напечатано в другом документе — источник указывает на него (AUDIT-1 № 36).
    Узлы окна мостов упр. ↔ движок — в их числе."""
    q = load("mgmt_quarterly")["quarters"]
    # ЧПМ 1К25 с якоря 3К26 напечатана (презентация 3К26, с. 8), CIR 3К25 — выведена из подписей той же презентации
    derived = [("2024Q1", "cir"), ("2024Q2", "cir"), ("2024Q3", "cir"), ("2025Q1", "cir"), ("2025Q2", "cir"),
               ("2025Q3", "cir"), ("2025Q1", "cor"), ("2023Q4", "roe"), ("2023Q4", "n20_0"), ("2024Q4", "n20_0")]
    for p, k in derived:
        n = q[p][k]
        assert n.get("src") and "выведено:" in (n.get("calc") or ""), f"{p}.{k}: нет вывода в calc"
        left, right = n["calc"].split("выведено:")[1].split("=")[:2]
        shown = float(right.split()[0].replace(",", ".").replace("−", "-"))
        assert abs(shown / 100 - n["v"]) < 1e-9, f"{p}.{k}: вывод даёт {shown}, узел {n['v']}"
    direct = {("2023Q3", "nim"): ("ifrs_9M2023_press_release.pdf", "с. 7"),
              ("2023Q2", "nim"): ("2023Q2_ifrs_press_release.pdf", "с. 3"),
              ("2023Q2", "roe"): ("2023Q2_ifrs_press_release.pdf", "с. 3"),
              ("2023Q2", "cir"): ("2023Q2_ifrs_press_release.pdf", "с. 3")}
    for (p, k), (doc, page) in direct.items():
        n = q[p][k]
        assert doc in n["src"] and page in n["src"], f"{p}.{k}: источник — не документ, где число напечатано"
        assert "напечатано" in n["calc"], f"{p}.{k}"
    # мост читает те же узлы: вывод виден и в истории моста
    hist = {h["period"]: h for h in load("bridge_mgmt_ifrs")["cir"]["history"]}
    assert "выведено:" in hist["2024Q1"]["mgmt"]["calc"]


def test_mgmt_loans_sum():
    for p, r in load("mgmt_quarterly")["quarters"].items():
        if r["loans_mgmt"]["v"] is not None:
            assert abs(v(r["loans_corporate"]) + v(r["loans_retail"]) - v(r["loans_mgmt"])) < TOL, p
        for k in ("nim", "cor", "cir", "roe"):
            x = r[k]["v"]
            assert x is None or -0.1 < x < 1.0, f"{p}.{k}: не доля"


# ------------------------------------------------------------------ гайденс

def test_guidance_items():
    g = load("guidance")
    items = g["items"]
    assert g["year"] == int(load("anchor")["period"][:4])
    assert items["cor_max"]["kind"] == "max"
    assert items["n20_0"]["kind"] == "min", "Н20.0 — пол политики, не точка (B2)"
    for key in ("roe", "nim", "cor_max", "cir", "fee_growth", "n20_0"):
        n = items[key]
        val = n["v"]
        assert n["kind"] in ("point", "min", "max", "range")
        if n["kind"] == "range":
            assert isinstance(val, list) and val[0] <= val[1]
        else:
            assert isinstance(val, (int, float))
        assert n["scope"] == "group" and "relation" not in n, f"{key}: цель банка, не прогноз сектора"
    for seg in ("corporate", "retail"):
        n = items["loan_growth"][seg]
        assert n["scope"] == "sector" and isinstance(n["v"], list)
        assert n["relation"] in ("in_line", "above"), f"{seg}: отношение банка к сектору — структурным полем"
        words = {"in_line": "в соответствии с сектором", "above": "лучше сектора"}[n["relation"]]
        assert words in n["scope_note"], f"{seg}: relation расходится со словами презентации"
    assert items["loan_growth"]["corporate"]["relation"] == "in_line"
    assert items["loan_growth"]["retail"]["relation"] == "above", "ФЛ: Сбер — лучше сектора (AUDIT-1 № 17)"
    dates = [h["date"] for h in g["history"]]
    assert dates == sorted(dates) and max(dates) <= g["as_of"]


def _nodes(obj):
    if isinstance(obj, dict):
        if "v" in obj:
            yield obj
        for x in obj.values():
            yield from _nodes(x)
    elif isinstance(obj, list):
        for x in obj:
            yield from _nodes(x)


def test_guidance_point_tolerance():
    """Допуск точечного гайденса — половина единицы последнего разряда числа, как оно записано в
    презентации (а не repr доли): «22%» → 0,005; «~6.2%» → 0,0005; «~0%» → 0,005. Только у point."""
    import re
    items = load("guidance")["items"]
    for n in _nodes(items):
        if n["kind"] != "point":
            assert "tol" not in n, f"«{n['text']}»: tol — только у точечного гайденса"
            continue
        m = re.search(r"\d+(?:[.,](\d+))?", n["text"])
        digits = len(m.group(1) or "")
        assert abs(n["tol"] - 0.5 * 10 ** -digits / 100) < 1e-12, f"«{n['text']}»: tol {n['tol']}"
    assert items["roe"]["tol"] == 0.005 and items["fee_growth"]["tol"] == 0.005
    # ЧПМ — точка («~6.2%», допуск 0,0005 — проверен циклом выше) или нижняя граница («>6.2%», без допуска)
    assert items["nim"]["kind"] in ("point", "min"), items["nim"]


# ------------------------------------------------------------------ календарь

KINDS = {"ifrs", "ras", "cbr_rate", "cbr_forecast", "agm", "record", "investor_day"}


def test_calendar_events():
    cal = load("calendar")
    as_of = dt.date.fromisoformat(cal["as_of"])
    ev = cal["events"]
    ids = [e["id"] for e in ev]
    assert len(ids) == len(set(ids)), "id событий не уникальны"
    dates = [e["date"] for e in ev]
    assert dates == sorted(dates), "события не по порядку дат"
    for e in ev:
        assert e["kind"] in KINDS, e["id"]
        assert isinstance(e["confirmed"], bool)
        assert e["precision"] in ("day", "window")
        d = dt.date.fromisoformat(e["date"])
        if e["precision"] == "window":
            assert dt.date.fromisoformat(e["earliest"]) <= d <= dt.date.fromisoformat(e["latest"]), e["id"]
        if not e["confirmed"]:
            assert e["precision"] == "window" and e.get("note"), f"{e['id']}: оценка без окна или прецедента"
        if e["kind"] == "ifrs":
            assert e["covers"] and e["covers"][4] == "Q", e["id"]
        elif e["kind"] == "ras":
            assert e["covers"] and e["covers"][4] == "M", e["id"]
        else:
            assert e["covers"] is None
        assert e["src"]
    assert (dt.date.fromisoformat(dates[-1]) - as_of).days >= 365, "горизонт меньше 12 месяцев"
    kinds = {e["kind"] for e in ev}
    assert {"ifrs", "ras", "cbr_rate", "cbr_forecast", "agm", "record", "investor_day"} <= kinds
    # открытый квартал книги закрывает МСФО следующего квартала
    anchor = load("anchor")["period"]
    y, k = int(anchor[:4]), int(anchor[-1])
    nxt = f"{y + 1}Q1" if k == 4 else f"{y}Q{k + 1}"
    assert any(e["kind"] == "ifrs" and e["covers"] == nxt for e in ev)
    # РСБУ — каждый месяц без пропусков
    months = sorted(e["covers"] for e in ev if e["kind"] == "ras")
    for a, b in zip(months, months[1:]):
        ya, ma, yb, mb = int(a[:4]), int(a[5:]), int(b[:4]), int(b[5:])
        assert (yb * 12 + mb) - (ya * 12 + ma) == 1, f"пропуск РСБУ между {a} и {b}"


def test_calendar_precedents_are_in_the_past():
    """Прецедент — дата, которая уже была: в записке события даты после слова «прецедент» (до конца
    части записки) не позже `as_of` календаря. Объявленная, но не наступившая дата прецедентом не
    называется (AUDIT-1 № 37)."""
    import re
    cal = load("calendar")
    as_of = dt.date.fromisoformat(cal["as_of"])
    bad = []
    for e in cal["events"]:
        note = e.get("note") or ""
        for m in re.finditer(r"прецедент[а-я]*:?([^;]*)", note):
            for dd, mm, yy in re.findall(r"(\d{2})\.(\d{2})\.(\d{4})", m.group(1)):
                if dt.date(int(yy), int(mm), int(dd)) > as_of:
                    bad.append(f"{e['id']}: «прецедент» {dd}.{mm}.{yy} позже as_of {as_of}")
    assert not bad, "\n".join(bad)


# ------------------------------------------------------------------ аналоги

def test_peers_basis_is_plain_words():
    """База сравнения печатается на витрине как есть: словами, без имён полей и служебных дат (AUDIT-1 № 68)."""
    import re
    basis = load("peers")["basis"]
    assert not re.search(r"[A-Za-z]+_[A-Za-z_]+|\bbv\b|\bas_of\b|\d{4}-\d{2}-\d{2}", basis), basis
    for word in ("P/B", "P/E", "ROE", "Дивидендная доходность", "Норматив"):
        assert word in basis, f"в базе сравнения нет столбца «{word}»"


def test_peers():
    p = load("peers")
    tickers = [b["ticker"] for b in p["banks"]]
    assert sorted(tickers) == sorted(["VTBR", "T", "SVCB", "BSPB", "MBNK"])
    assert p["subject_ticker"] not in tickers
    for b in p["banks"]:
        for k in ("bv", "ni_ltm", "roe_ltm", "shares_outstanding", "dps_ltm"):
            assert isinstance(b[k], dict) and "v" in b[k], f"{b['ticker']}.{k}"
        cr = b["capital_ratio"]
        assert cr["name"] and cr["as_of"] and isinstance(cr["value"], dict)
        assert 0.05 < v(cr["value"]) < 0.4
        roe = v(b["ni_ltm"]) / ((v(b["bv"]) + v(b["bv_prev"])) / 2)
        assert abs(roe - v(b["roe_ltm"])) < 1e-3, b["ticker"]
        assert v(b["bv"]) > 0 and v(b["shares_outstanding"]) > 0
        assert "price" not in b, "цены — живой вход, не факт"
        dt.date.fromisoformat(b["bv_date"])


@pytest.mark.deadline
def test_calendar_is_ahead_of_today():
    """Тест срока (метка `deadline`: CI и ручной прогон; в набор сервера и в прогон «в будущем» не входит):
    календарь фактов опережает «сегодня» — впереди есть отчёт МСФО, а последнее событие не ближе 90 дней.
    Календарь продлевают на каждом перезаякоривании (SCHEMA.md §3)."""
    today = dt.date.today()
    ev = load("calendar")["events"]
    assert any(e["kind"] == "ifrs" and dt.date.fromisoformat(e["date"]) >= today for e in ev),         f"calendar.json: нет отчёта МСФО после {today} — продлить календарь"
    last = max(dt.date.fromisoformat(e["date"]) for e in ev)
    assert (last - today).days >= 90, f"calendar.json кончается {last} — продлить календарь (не меньше 12 месяцев)"
