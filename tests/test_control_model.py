# -*- coding: utf-8 -*-
"""Годовая контрольная модель (docs/MODEL.md §17): её собственные свойства и сверка с ядром.

* Свойства контрольной модели (ядро не нужно; метка tact — быстрые): V_DDM = V_RI в каждой клетке
  (≤ 0,1 %, фактически машинная точность), тождества BV, баланса и PBT, квартальные профили прибыли и
  CI, вероятности слоёв, DPS истории до копейки, передача решена (T_real = T*, Nss(H) = цель) и
  парные передачи телескопичны, плоский мир ⇒ V = BV_v, кризисный перенос и догоняющая выплата
  (N, crisis, strict), связывающий капитал (M, downturn, strict), нормативы якоря воспроизводят факты.
* Решения W1 (LEAD-DECISIONS-2): σ0 с года sigma0_from, ближний сдвиг ЧПМ вне Nss и общий для клеток,
  «конец дня» на конце квартала ГОСА, ввод ε, доли «прочего», A-P2u при одном режиме.
* Решения W2 (LEAD-DECISIONS-3, аудит после W1): «прочее» года якоря — уровень × доля (№ 2), FVC
  режима на кредитах по СС (№ 3), доход на избыток по маржинальной ставке балансирующих статей
  (№ 4), цена с D_pend (№ 25), окно наблюдений, стационарные априорные и замороженные μ (В2),
  распределение сдвига σ0 между активами и пассивами (В8), год шока кризиса от года якоря.
* Решения W3 (LEAD-DECISIONS-4, второй аудит): раскладка сжатия φ между кредитными книгами и средствами
  клиентов, кредитная маржа миров и тест знака объёмных эффектов (В12), одностороннее угасание (В13),
  опора FVC — путь нормы книги до розыгрыша (В14), пол вероятности режима и замороженные μ (В15),
  порог связывания минимума ликвидности и добор опта на терминале, кварталы выплаты и вычета
  объявленного дивиденда по записи реестра (М§5.4), центральная клетка по правилу (№ 86).
* Сверка с ядром (маркер ci_only): ядро импортируется только внутри сверки; пока ядра нет —
  пропуск с причиной. Допуски — М§17 (docs/CONTROL-MODEL.md, «Допуски»); точное совпадение V0
  слоёв (относительная разность ≤ 1e-9) — провал: это означало бы общий код.

Сводка сверки для выпуска (П§2 checks.control_model; строка несёт единицу и вид допуска):
    python -B -m tests.test_control_model --write   → data/checks/control_model.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    from tests import independent_model as cm
except ImportError:                                     # pytest без пакета tests
    import independent_model as cm                      # type: ignore[no-redef]

CHECKS_JSON = cm.ROOT / "data" / "checks" / "control_model.json"

# Допуски М§17 (доли; «ratio» — абсолютная разность долей, 0,003 = 0,3 п.п.).
TOL_LINE = 0.10          # строки ОПУ и баланса
TOL_SMALL_BV = 0.02      # PBT, NI, CI при малой величине: |Δ| ≤ 2 % BV
TOL_RATIO = 0.003        # нормативы, ROE_T, k_T, g_T
TOL_VALUE = 0.03         # V_c, V0_L, низ, верх, точка; TV — в форме TV_DDM
TOL_BRIDGE = 0.01        # мост и объявленные дивиденды
TOL_DERIVED = 1e-6       # σ0_A, σ0_L, φ, φ_A, φ_L, T_real, T(W), пары, ROE-эквивалент, LT-спреды, маржа миров
TOL_DDM = 0.001          # V_DDM = V_RI внутри контрольной модели
EXACT = 1e-9             # совпадение V0 слоёв с ядром до этой точности — провал (общий код)
DIV_FLOOR_BV = 0.005     # дивиденд года и DPS: |Δ| ≤ 0,5 % BV конца года выплаты (связывающий капитал)
TOL_ZERO_BN = 1e-9       # машинный ноль в единицах строки: мост при нуле у ядра; величина относительной строки

FLOW_LINES = ("nii", "llp", "fvc", "fees", "opex", "misc", "noncore", "pbt", "ni_sh", "ci")
STOCK_LINES = ("bv", "rwa")
RATIO_LINES = ("n20", "n11")
SMALL_BV_LINES = ("pbt", "ni_sh", "ci")
ABS_LINES = ("llp", "opex")      # расходные строки сверяются по модулю (знак хранения у ядра свой)

# Единицы строк сводки — коды П§0.2; слова строк — то, что печатает витрина (метку клетки W/r/s она
# переводит в слова сама).
U_BN, U_RUB, U_PCT, U_PP, U_NUM = "bn", "rub", "pct", "pp", "number"
REL, ABS = "rel", "abs"
LINE_TITLES = {
    "nii": "ЧПД", "llp": "резервы", "fvc": "переоценка кредитов по справедливой стоимости сверх опоры",
    "fees": "ЧКД", "opex": "расходы", "misc": "прочее", "noncore": "непрофильный результат",
    "pbt": "прибыль до налога", "ni_sh": "ЧП акционерам", "ci": "совокупный доход", "div": "дивиденд года",
    "bv": "капитал", "rwa": "RWA", "n20": "Н20.0", "n11": "Н20.1",
}
LAYER_TITLES = {"analytical": "свой макро-взгляд", "market_implied": "вменённые рынком",
                "macro_neutral": "рыночные ставки как есть"}
BOOK_TITLES = {"corp_loans": "корпоративные кредиты", "mortgage": "рыночная ипотека",
               "mortgage_sub": "льготная ипотека", "retail_loans": "прочие розничные кредиты"}
SUMMARY_WHOLE = ("derived", "value", "bridge", "cell")     # группы строк, которые идут в сводку целиком
ROW_FIELDS = ("what", "unit", "core", "control", "diff", "diff_rel", "tol", "tol_kind", "ok")


def _inputs():
    try:
        return cm.c_book_machine(), cm.c_facts_bundle()
    except cm.ControlInputError as exc:
        pytest.skip(f"нет машинной книги или фактов ({exc}): контрольной модели не на чем считать")


@pytest.fixture(scope="module")
def inputs():
    return _inputs()


@pytest.fixture(scope="module")
def control(inputs):
    book, facts = inputs
    try:
        return cm.c_control_run(book, facts)
    except cm.ControlUnsupported as exc:
        pytest.skip(f"ветка книги вне годовой контрольной модели: {exc}")


# ============================================================================ свойства контрольной модели


@pytest.mark.tact
def test_ddm_equals_ri_in_every_cell(control):
    tol = cm.c_bnum(control["ctx"]["B"], "checks.ddm_ri_tol")
    assert tol <= TOL_DDM
    for c in control["cells"]:
        gap = abs(c["v_ddm"] - c["v_ri"]) / abs(c["v_ri"])
        assert gap <= 1e-9, (c["world"], c["regime"], c["scenario"], gap)


@pytest.mark.tact
def test_bv_balance_and_pbt_identities(control):
    A = control["ctx"]["A"]
    for c in control["cells"]:
        bv_prev = A["BV"]
        for r in c["rows"]:
            bv = bv_prev + r["ni_sh"] - r["cpn_net"] + r["oci"] + r["om"] - r["div"]
            assert math.isclose(bv, r["bv"], rel_tol=1e-9), (c["world"], c["regime"], c["scenario"])
            assert abs(r["assets"] - r["liabilities"]) <= 1e-9 * r["assets"]
            pbt = (r["nii"] - r["llp"] - r["fvc"] + r["fees"] + r["ins"] + r["misc"] + r["fvr"]
                   + r["noncore"] - r["opex"] + r["one_off"])
            assert math.isclose(pbt, r["pbt"], rel_tol=1e-12, abs_tol=1e-9)
            assert math.isclose(r["ni"], r["pbt"] - r["tax"] - r["ot"], rel_tol=1e-12, abs_tol=1e-9)
            assert math.isclose(sum(r["ni_q"].values()), r["ni_sh"], rel_tol=1e-9, abs_tol=1e-9)
            assert math.isclose(sum(r["ci_q"]), r["ci"], rel_tol=1e-9, abs_tol=1e-9)
            assert math.isclose(sum(r["fvc_q"]), r["fvc"], rel_tol=1e-12, abs_tol=1e-12)
            bv_prev = r["bv"]


@pytest.mark.tact
def test_layer_probabilities_sum_to_one(control):
    for name, ly in control["layers"].items():
        assert abs(ly["prob_sum"] - 1.0) <= 1e-12, name
    assert abs(sum(control["posterior"].values()) - 1.0) <= 1e-12


@pytest.mark.tact
def test_point_is_low_plus_lambda(control):
    pt = control["low"] + control["lam"] * (control["high"] - control["low"])
    assert math.isclose(pt, control["point"], rel_tol=1e-12)
    B = control["ctx"]["B"]
    assert math.isclose(control["ctx"]["gov_sum"], cm.c_bnum(B, "valuation.governance.discount"), abs_tol=1e-12)


@pytest.mark.tact
def test_dps_history_to_the_kopeck(control):
    """Формула пула на истории facts/dividends.json даёт объявленный DPS до копейки (М§5.2): все
    годы истории с полными данными, не меньше двух последних."""
    rows = [h for h in control["dps_history"] if h["declared"] is not None]
    assert len(rows) >= 2, "в facts/dividends.json меньше двух лет истории DPS"
    for h in rows:
        assert h["ok"], h


@pytest.mark.tact
def test_transmission_solved(control):
    tr = control["transmission"]
    tol = cm.c_bnum(control["ctx"]["B"], "checks.transmission_tol")
    assert abs(tr["nss_ref"] - tr["target_eng"]) <= max(tol, 1e-12)
    assert abs(tr["t_real"] - cm.c_bnum(control["ctx"]["B"], "nii.transmission.target")) <= max(tol, 1e-12)


@pytest.mark.tact
def test_anchor_reproduces_capital_facts(control):
    """На якоре Н20.0 (до вычета дивиденда) и Н1.1 банка воспроизводятся (условие калибровки М§4.11)."""
    A = control["ctx"]["A"]
    for c in control["cells"]:
        cap = c["anchor_capital"]
        assert abs(cap["n20"] - A["n20_fact"]) <= TOL_RATIO
        assert abs(cap["n11"] - A["n11_fact"]) <= TOL_RATIO


@pytest.mark.tact
def test_crisis_cell_skips_and_catches_up(control):
    """(N, crisis, strict): выплата, решаемая в год шока (год якоря + shock_year_offset, М§3.2),
    отменена, пул уходит в отложенный и возвращается догоняющей выплатой (М§5.3 пп. 4–5)."""
    B = control["ctx"]["B"]
    shock = control["shock_year"]
    assert shock == control["ctx"]["anchor"][0] + int(cm.c_bget(B, "regimes.crisis.shock_year_offset"))
    c = cm.c_cell_of(control, "N", "crisis", "strict")
    skipped = c["decisions"][shock - 1]
    assert skipped["kind"] == "crisis_skip" and skipped["div"] == 0.0 and skipped["want"] > 0.0
    caught = sum(d.get("catch", 0.0) for d in c["decisions"].values())
    assert math.isclose(caught, skipped["want"], rel_tol=1e-9)
    assert c["rows"][-1]["D"] == pytest.approx(0.0, abs=1e-9)
    for other in ("soft", "norm", "downturn"):                      # отмена — только в клетке кризиса
        assert cm.c_cell_of(control, "N", other, "strict")["decisions"][shock - 1]["kind"] != "crisis_skip"


@pytest.mark.tact
def test_binding_capital_cell(control):
    """(M, downturn, strict): хотя бы в одном году запас H меньше пула политики — выплата по
    остатку сверх требований (М§5.3 п. 3), дивиденд не превышает запас."""
    c = cm.c_cell_of(control, "M", "downturn", "strict")
    bound = [d for d in c["decisions"].values() if d["kind"] == "policy" and d["H"] < d["want"]]
    assert bound, "в клетке (M, downturn, strict) капитал ни разу не связывает"
    for d in bound:
        assert d["div"] <= max(0.0, d["H"]) + d.get("catch", 0.0) + d.get("excess", 0.0) + 1e-9


@pytest.mark.tact
def test_flat_world_gives_book_value(control):
    """Плоский мир: плоская кривая, доход ровно k на капитал (с учётом даты выплаты) и ROE_T = k_T
    ⇒ V = BV_v (М§17) — проверка формы RI/DDM и терминала контрольной модели. Терминальный год
    подобран так, что ROE_T = k_T при множителе угасания книги: одностороннее угасание (М§7) само
    доходность ниже k_T к k_T не подтягивает."""
    ctx = dict(control["ctx"])
    B = cm.copy.deepcopy(ctx["B"])
    z = 0.12
    for w in ctx["worlds"]:
        B["worlds"][w]["zero_curve"] = {"1": z, "3": z, "5": z, "10": z, "LT": z}
    add = cm.c_bnum(B, "valuation.beta_e") * cm.c_bnum(B, "valuation.erp")
    ctx["B"] = B
    ctx["disc"] = {w: cm.CDisc(B["worlds"][w]["zero_curve"], add, 0.3) for w in ctx["worlds"]}
    base = cm.c_cell_of(control, "H", "norm", "upper")
    df = ctx["disc"]["H"]
    ck, periods = ctx["clock"], ctx["periods"]
    agm = int(cm.c_bnum(B, "dividends.calendar.agm_quarter"))
    rows = [dict(r) for r in base["rows"]]
    bv = ctx["A"]["BV"]
    for i, r in enumerate(rows):
        te = ck["tau_end"][i]
        k = (df(ck["tau_end"][i - 1]) / df(te) - 1.0) if i > ck["p0"] else (1.0 / df(te) - 1.0)
        corr = 0.0
        if r["div"]:
            ta = ck["t_start"][i] + 0.25 * (periods[i]["quarters"].index(agm) + 1)
            corr = r["div"] * (df(ta) / df(te) - 1.0)
        if i == ck["p0"]:
            # (1 − e)·CI − k·BV_v + corr = 0 при BV_v = BV + e·CI
            e = ck["e"]
            ci = (k * bv - corr) / ((1.0 - e) - k * e)
        else:
            ci = k * bv - corr
        r["ci"] = ci
        r["ci_q"] = [ci / periods[i]["n"]] * periods[i]["n"]
        bv = bv + ci - r["div"]
        r["bv"] = bv
    cell = {"world": "H", "regime": "norm", "scenario": "upper"}
    probe = cm._c_value_cell(ctx, cell, rows, {})        # X_T, BV*, Y_X, k_T, g_T от PBT терминала не зависят
    A = ctx["A"]
    yl = periods[-1]["year"]
    cpn = A["cpn_annual"] * (1.0 - cm.c_traj_at(cm.c_bget(B, "tax.statutory"), yl, A["cpn_quarter"]))
    keep = (1.0 - rows[-1]["tau_eff"]) * (1.0 - cm.c_bnum(B, "pnl.nci_share"))
    rows[-1]["pbt"] = ((probe["k_t"] * probe["bv_star"] + cpn) / keep + probe["y_x"]) / (1.0 + probe["g_t"])
    out = cm._c_value_cell(ctx, cell, rows, {})
    assert out["roe_t_raw"] == pytest.approx(out["k_t"], abs=1e-14)
    assert out["roe_t"] == pytest.approx(out["k_t"], abs=1e-14)
    assert out["v_ri"] == pytest.approx(out["bv_v"], rel=1e-10)
    assert out["v_ddm"] == pytest.approx(out["bv_v"], rel=1e-10)


@pytest.mark.tact
def test_kappa_addon_zero_in_world_n(control):
    """κ-добавка к CoR — относительно мира N: в мире N она ноль (М§4.6)."""
    for per in control["ctx"]["wv"][cm.W_LOW]:
        assert per["kappa_gaps"] and all(g == 0.0 for g in per["kappa_gaps"])


@pytest.mark.tact
def test_draft_worlds_match_machine_book(inputs):
    """Миры и надстройка, собранные контрольной моделью из копии записи по таблице М§3.1, совпадают
    с блоком worlds машинной книги (независимая проверка сборки книги)."""
    book, _ = inputs
    if not (cm.WORLDS_SOURCE.exists() and cm.WORLDS_BANK.exists() and cm.DRAFT_YAML.exists()):
        pytest.skip("нет черновика, копии записи миров или надстройки")
    lt = {w: book["worlds"][w]["zero_curve"]["LT"] for w in book["worlds"]["ids"] if w != cm.W_MKT}
    draft = cm.c_book_from_draft(curve_lt=lt)
    for w in book["worlds"]["ids"]:
        for f, v in book["worlds"][w].items():
            if f == "name":
                continue
            got = draft["worlds"][w][f]
            if isinstance(v, dict):
                assert {str(k): float(x) for k, x in v.items()} == pytest.approx(
                    {str(k): float(x) for k, x in got.items()}, abs=1e-12), (w, f)
            else:
                assert float(v) == pytest.approx(float(got), abs=1e-12), (w, f)
    assert draft["worlds_bank"] == book["worlds_bank"]


@pytest.mark.tact
def test_template_plus_worlds_is_the_machine_book(inputs):
    """Шаблон книги + миры и надстройка, собранные контрольной моделью, — это машинная книга целиком
    (кроме названий миров — подписи сборки семейства) и хэш надстройки = sha256 worlds_bank.json:
    независимая проверка build_assumptions.py (М§3.1, прил. A)."""
    import hashlib
    book, _ = inputs
    if not (cm.TEMPLATE_YAML.exists() and cm.WORLDS_SOURCE.exists() and cm.WORLDS_BANK.exists()):
        pytest.skip("нет шаблона, копии записи миров или надстройки")
    lt = {w: book["worlds"][w]["zero_curve"]["LT"] for w in book["worlds"]["ids"] if w != cm.W_MKT}
    built = cm.c_book_from_draft(cm.TEMPLATE_YAML, curve_lt=lt)
    assert book["worlds"]["overlay"]["sha256"] == hashlib.sha256(cm.WORLDS_BANK.read_bytes()).hexdigest()
    mine = cm.copy.deepcopy(book)
    for tree in (mine, built):
        tree["worlds"]["overlay"]["sha256"] = None
        for w in book["worlds"]["ids"]:
            tree["worlds"][w].pop("name", None)
    assert _same_tree(built, mine)


def _same_tree(a, b, path="") -> bool:
    """Равенство деревьев книги: числа — до 1e-12, остальное — точно (путь расхождения в ошибке)."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert set(map(str, a)) == set(map(str, b)), f"{path}: ключи {sorted(set(map(str, a)) ^ set(map(str, b)))}"
        bk = {str(k): v for k, v in b.items()}
        return all(_same_tree(v, bk[str(k)], f"{path}.{k}") for k, v in a.items())
    if isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), path
        return all(_same_tree(x, y, f"{path}[{i}]") for i, (x, y) in enumerate(zip(a, b)))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        assert abs(float(a) - float(b)) <= 1e-12, (path, a, b)
        return True
    assert a == b, (path, a, b)
    return True


# ============================================================================ решения W1 (LEAD-DECISIONS-2)


def _history_dps(facts: dict) -> float:
    """DPS последнего года истории — тестовое значение записи реестра (не допущение)."""
    return float(cm._c_nodeval(facts["dividends"]["history"][-1]["dps_ordinary"]))


def _declared(year: int, dps: float, agm_end: dt.date, status: str = "declared") -> dict:
    """Запись реестра сценария: отсечка = экс-дата через 20 дней после конца квартала ГОСА."""
    ex = agm_end + dt.timedelta(days=20)
    return {"year": year, "dps": dps, "status": status, "record_date": ex.isoformat(),
            "last_buy_date": (ex - dt.timedelta(days=1)).isoformat(), "ex_date": ex.isoformat(),
            "pay_date": (ex + dt.timedelta(days=14)).isoformat(), "sources": ["сверка М§17"]}


def _agm_end(book: dict) -> tuple[int, dt.date]:
    """Год прибыли якоря и конец квартала ГОСА, на котором решается его дивиденд (М§5.4)."""
    y0, _ = cm.c_per_parse(book["meta"]["anchor_period"])
    return y0, cm.c_q_last_day(y0 + 1, int(book["dividends"]["calendar"]["agm_quarter"]))


@pytest.mark.tact
def test_sigma0_enters_spreads_from_sigma0_from(inputs, control):
    """A1 F1, В8 (М§4.4): сдвиг σ0 прибавляется ко всем значениям спредов книг с lt_shift с года
    sigma0_from, не раньше: +σ0_A у активов, −σ0_L у пассивов. Сдвиг sigma0_from на год позже не меняет
    ЧПД раньше sigma0_from и поднимает ЧПД года sigma0_from (сдвиг стационарного ЧПМ Δ0 < 0); стационар
    (σ0_A, σ0_L, φ, T_real, пары) от sigma0_from не зависит."""
    book, facts = inputs
    ctx = control["ctx"]
    tr = ctx["tr"]
    y1 = ctx["sigma0_from"]
    for b, sp in ctx["B"]["nii"]["books"].items():
        sig = tr["sigma0"] if sp["side"] == cm.LOANS_SIDE else -tr["sigma0_liab"]
        for y in (y1 - 1, y1):
            got = cm.c_spread_at(ctx, b, y, cm.QY)
            want = cm.c_traj_at(sp["spread"], y, cm.QY) + (sig if sp.get("lt_shift") and y >= y1 else 0.0)
            assert got == want, (b, y)
    if y1 + 1 > control["years"][-1]:
        pytest.skip("sigma0_from — последний год явного участка: сдвинуть некуда")
    b2 = cm.copy.deepcopy(book)
    b2["nii"]["sigma0_from"] = y1 + 1
    later = cm.c_control_run(b2, facts)
    for k in ("sigma0", "sigma0_liab", "phi", "pairs"):
        assert later["transmission"][k] == tr[k], k
    base_rows = cm.c_cell_of(control, "H", "norm", "schedule")["rows"]
    late_rows = cm.c_cell_of(later, "H", "norm", "schedule")["rows"]
    for y, r0, r1 in zip(control["years"], base_rows, late_rows):
        if y < y1:
            assert r1["nii"] == r0["nii"], y
        elif y == y1:
            assert (r1["nii"] - r0["nii"]) * tr["delta0"] < 0.0, (y, r0["nii"], r1["nii"])


@pytest.mark.tact
def test_near_spread_keys_follow_anchor_rates(control):
    """A1 F3 (М§4.4, правило книги — независимая проверка): ключ года якоря траектории спреда =
    ставка якоря книги − опора мира H первого прогнозного квартала (у пассивов — β × опора), до
    половины пятого знака записи книги: при неизменной опоре ставка не прыгает в первом квартале."""
    ctx = control["ctx"]
    B, A = ctx["B"], ctx["A"]
    y0, _ = ctx["anchor"]
    fy, fq = cm.c_per_parse(B["meta"]["first_period"])
    ref_w = B["nii"]["transmission"]["reference_world"]
    checked = 0
    for b, sp in B["nii"]["books"].items():
        path = sp["spread"]
        if not isinstance(path, dict) or str(y0) not in {str(k) for k in path}:
            continue                                        # скалярный спред — калибровка без ближних ключей
        ref = cm.c_world_quarter(B, ref_w, cm.c_ref_field(sp["ref"]), fy, fq)
        beta = 1.0 if sp["side"] == cm.LOANS_SIDE else float(sp["beta"])
        key = float({str(k): v for k, v in path.items()}[str(y0)])
        assert abs(key - (A["rate0"][b] - beta * ref)) <= 5e-6 + 1e-12, (b, key, A["rate0"][b] - beta * ref)
        checked += 1
    assert checked, "в книге нет ни одной траектории спреда с ключом года якоря"


@pytest.mark.tact
def test_near_nim_shift_is_common_and_outside_nss(inputs, control):
    """A1 F2 (М§3.2, §4.4–§4.5): ближний сдвиг ЧПМ одинаков во всех клетках, не входит в Nss (σ0, φ,
    T_real и пары от него не зависят) и прибавляется к ЧПД как near × ĪEA × дни/365."""
    book, facts = inputs
    first = control["cells"][0]["rows"]
    for c in control["cells"]:
        assert [r["near"] for r in c["rows"]] == [r["near"] for r in first]
    b2 = cm.copy.deepcopy(book)
    bump = cm.c_bnum(b2, "nii.transmission.fd_step")          # «шаг» из книги, не число теста
    near = b2["regimes"]["near_nim_shift"]
    b2["regimes"]["near_nim_shift"] = ({k: (v if k == "LT_from" else v + bump) for k, v in near.items()}
                                       if isinstance(near, dict) else near + bump)
    ctx2 = cm.c_control_context(b2, facts)
    for k in ("sigma0", "sigma0_liab", "phi", "t_real", "pairs"):
        assert ctx2["tr"][k] == control["transmission"][k], k
    p0 = ctx2["periods"][0]
    r_base = cm.c_cell_of(control, "H", "norm", "schedule")["rows"][0]
    r_bump = cm.c_cell_annual(ctx2, "H", "norm", "schedule")["rows"][0]
    expect = bump * r_base["iea"] * p0["days"] / cm.DAYS_BASE
    assert (r_bump["nii"] - r_base["nii"]) == pytest.approx(expect, rel=0.02)


def _with_governance(book: dict, g: float) -> dict:
    """Копия книги с дисконтом за управление g: вся величина — в первом канале (сумма каналов = дисконту)."""
    b = cm.copy.deepcopy(book)
    b["valuation"]["governance"]["discount"] = g
    for i, comp in enumerate(b["valuation"]["governance"]["components"]):
        comp["value"], comp["sign"] = (g if i == 0 else 0.0), 1
    return b


@pytest.mark.tact
def test_end_of_day_at_agm_quarter_end(inputs):
    """A2, № 25 (М§0.2, §8.1–§8.2, §18): «конец квартала — конец дня» и D_pend. С записью реестра за год
    прибыли точка на E_qA и E_qA + 1 — одна позиция линейки (равны); между E_qA − 1 и E_qA она меняется
    только на перекат двух дней (< 0,1 % цены) — не на DPS и не на g_gov × DPS: до вычета из BV
    объявленный дивиденд выведен из-под дисконта суммой D_pend, после — стоит в мосте. Поэтому скачок
    при другом дисконте g отличается ровно множителем (1 − g). Без записи точка на E_qA падает на
    ожидаемый дивиденд модели (клетки вычитают его, моста нет)."""
    book, facts = inputs
    y0, e_qa = _agm_end(book)
    dps = _history_dps(facts)
    f2 = cm.copy.deepcopy(facts)
    f2["dividends"]["register_seed"] = [_declared(y0, dps, e_qa)]
    run = {d: cm.c_control_run(book, f2, valuation_date=e_qa + dt.timedelta(days=d)) for d in (-1, 0, 1)}
    amount = dps * run[0]["ctx"]["A"]["N_out"] / 1000.0
    assert run[-1]["bridge_amount"] == 0.0 and run[-1]["pending_amount"] == pytest.approx(amount, rel=1e-12)
    assert run[0]["bridge_amount"] == pytest.approx(amount, rel=1e-12) and run[0]["pending_amount"] == 0.0
    assert run[0]["point"] == pytest.approx(run[1]["point"], rel=1e-12)
    jump = run[0]["point"] - run[-1]["point"]
    assert 0.0 < jump < 0.001 * run[0]["point"], (jump, run[0]["point"])      # только перекат двух дней
    g1 = run[0]["governance"]
    g2 = 10.0 * g1 if g1 > 0.0 else cm.c_bnum(book, "nii.transmission.fd_step")
    b2 = _with_governance(book, g2)
    alt = {d: cm.c_control_run(b2, f2, valuation_date=e_qa + dt.timedelta(days=d)) for d in (-1, 0)}
    jump2 = alt[0]["point"] - alt[-1]["point"]
    assert jump2 / jump == pytest.approx((1.0 - g2) / (1.0 - g1), rel=1e-9)   # g × DPS в скачке нет
    assert abs(jump2 - jump) < 0.1 * (g2 - g1) * dps
    # без записи реестра — падение на дивиденд модели в конце квартала ГОСА
    free = {d: cm.c_control_run(book, facts, valuation_date=e_qa + dt.timedelta(days=d)) for d in (-1, 0)}
    B = free[0]["ctx"]["B"]
    lam, gov = free[0]["lam"], free[0]["governance"]
    weights = cm.c_layer_weights(B)
    prs = cm.c_bget(B, "joint.reg_prob_given_regime")

    def c_mean_dps(layer):
        return sum(weights[layer].get(c["world"], 0.0) * free[0]["posterior"][c["regime"]]
                   * float(prs[c["regime"]][c["scenario"]]) * c["decisions"][y0]["dps"] for c in free[0]["cells"])

    drop = (1.0 - gov) * (c_mean_dps("macro_neutral")
                          + lam * (c_mean_dps("analytical") - c_mean_dps("macro_neutral")))
    assert free[-1]["point"] - free[0]["point"] == pytest.approx(drop, rel=0.05)


@pytest.mark.tact
def test_excess_payout_ramps_in(control):
    """A4 (М§5.3 п. 4): ε_Y = ε × min(1, (Y − from + 1)/ramp_years) — накопленный избыток выпускается
    за ramp_years лет, а не разом; до from_profit_year — ноль."""
    B = control["ctx"]["B"]
    fy = int(cm.c_bnum(B, "dividends.excess.from_profit_year"))
    eps, ramp = cm.c_bnum(B, "dividends.excess.epsilon"), int(cm.c_bnum(B, "dividends.excess.ramp_years"))
    seen = set()
    for c in control["cells"]:
        for yp, d in c["decisions"].items():
            if d["kind"] != "policy":
                continue
            want = eps * min(1.0, (yp - fy + 1) / ramp) if yp >= fy else 0.0
            assert d["eps"] == pytest.approx(want, abs=1e-15), (c["world"], c["regime"], c["scenario"], yp)
            seen.add(yp)
    years = set(range(fy, fy + ramp)) & set(range(control["years"][0], control["years"][-1]))
    assert years <= seen, "годы ввода ε без решения по политике"


@pytest.mark.tact
def test_regime_update_fact_equal_to_expectation_moves_nothing(inputs):
    """A5 (М§12, §18): при одном режиме наблюдение, равное ожиданию μ (среднее слоя «свой взгляд» по
    клеткам режима), не меняет ни вероятностей, ни отклонений, ни V0 слоёв."""
    book, facts = inputs
    b1 = cm.copy.deepcopy(book)
    b1["joint"]["regime_update"]["observations"] = []        # окно — только наблюдение теста
    regimes = list(b1["regimes"]["ids"])
    only = regimes[1] if len(regimes) > 1 else regimes[0]
    b1["joint"]["regime_prob"] = {r: (1.0 if r == only else 0.0) for r in regimes}
    base = cm.c_control_run(b1, facts)
    per = b1["meta"]["first_period"]
    y, q = cm.c_per_parse(per)
    mu = cm.c_expectations_ctl(base["ctx"], [(y, q)])
    A = base["ctx"]["A"]
    b2 = cm.copy.deepcopy(b1)
    b2["joint"]["regime_update"]["observations"] = [{
        "period": per, "cor": cm.c_to_mgmt(A, "cor", mu[("cor", only, y, q)]),
        "nim": cm.c_to_mgmt(A, "nim", mu[("nim", only, y, q)]), "se_cor": 0.0, "se_nim": 0.0, "basis": "mgmt"}]
    obs = cm.c_control_run(b2, facts)
    assert obs["posterior"] == pytest.approx(base["posterior"], abs=1e-12)
    for x in ("cor", "nim"):
        assert abs(obs["ctx"]["dev"][only][x]["m"]) <= 1e-12
    for name in cm.LAYERS:
        assert obs["layers"][name]["v0"] == pytest.approx(base["layers"][name]["v0"], rel=1e-9)


@pytest.mark.tact
def test_transmission_pairs_telescope(control):
    """A6 (М§4.5): парные передачи соседних по key^LT миров телескопичны — Σ T_pair × Δkey^LT =
    Nss(крайний верхний) − Nss(крайний нижний); при N и M по краям это T_real × (key_M − key_N).
    ROE-эквивалент — тот же множитель IEA_0/BV_0 × (1 − τ_eff,L) для T* и пар."""
    tr = control["transmission"]
    key, nss = tr["key_lt"], tr["nss"]
    order = sorted(key, key=key.get)
    total = 0.0
    for w1, w2 in zip(order, order[1:]):
        t = tr["pairs"][f"{w1}_{w2}"]
        assert t is not None
        total += t * (key[w2] - key[w1])
    assert total == pytest.approx(nss[order[-1]] - nss[order[0]], abs=1e-12)
    if (order[0], order[-1]) == (cm.W_LOW, cm.W_MKT):
        assert total == pytest.approx(tr["t_real"] * (key[cm.W_MKT] - key[cm.W_LOW]), abs=1e-12)
    scale = tr["roe_equiv"] / cm.c_bnum(control["ctx"]["B"], "nii.transmission.target")
    for k, v in tr["pairs"].items():
        assert tr["roe_equiv_pairs"][k] == pytest.approx(v * scale, rel=1e-12)


@pytest.mark.tact
def test_bad_keys_fail_loudly(inputs):
    """Ключи W1–W3 вне правил приложения A — громкий отказ, а не молчаливый расчёт."""
    book, facts = inputs

    def c_broken(change) -> dict:
        b = cm.copy.deepcopy(book)
        change(b)
        return b

    last_year = cm.c_per_parse(book["meta"]["last_period"])[0]
    shock_next = cm.c_per_parse(book["meta"]["anchor_period"])[0] + book["regimes"]["crisis"]["shock_year_offset"] + 1
    cases = {
        "доли «прочего» не в сумме 1": lambda b: b["other"].update(
            misc_quarter_shares={h: v * 2.0 for h, v in b["other"]["misc_quarter_shares"].items()}),
        "sigma0_from вне явного участка": lambda b: b["nii"].update(sigma0_from=last_year + 1),
        "sigma0_split больше единицы": lambda b: b["nii"].update(sigma0_split=1.5),
        "window_obs меньше единицы": lambda b: b["joint"]["regime_update"].update(window_obs=0),
        "phi_split больше единицы": lambda b: b["nii"].update(phi_split=1.5),
        "floor_share больше единицы": lambda b: b["joint"]["regime_update"].update(floor_share=1.5),
        "флаг phi у книг средств ФЛ разный": lambda b: b["nii"]["books"][cm.RETAIL_TERM].update(
            phi=not bool(b["nii"]["books"][cm.RETAIL_CURRENT].get("phi"))),
        "опора FVC — не режим книги": lambda b: b["credit"].update(fv_loans_ref="нет такого режима"),
        "год шока сдвинут, кризисные ключи — нет": lambda b: b["regimes"]["crisis"].update(
            shock_year_offset=b["regimes"]["crisis"]["shock_year_offset"] + 1),
        "разовый убыток вне года шока": lambda b: b["regimes"]["crisis"]["one_off_loss"].update(
            period=f"{shock_next}Q1"),
    }
    for name, change in cases.items():
        with pytest.raises(cm.ControlInputError):
            cm.c_control_context(c_broken(change), facts)
            pytest.fail(f"нет отказа: {name}")
    b = c_broken(lambda b: b["dividends"]["excess"].update(ramp_years=0))
    with pytest.raises(cm.ControlInputError):
        cm.c_control_run(b, facts)


# ============================================================================ решения W2 (LEAD-DECISIONS-3)


@pytest.mark.tact
def test_misc_of_the_anchor_year_is_level_times_share(control):
    """№ 2, В3 (М§0.3, §4.7): «прочее» каждого прогнозного квартала — уровень книги × доля квартала ×
    индекс цен мира, и в году якоря тоже: остатком года оно не закрывается, сумма года — выход.
    Непрофильный результат закрывает год якоря остатком (сумма книги − факт отчётных кварталов), поровну
    на оставшиеся кварталы."""
    ctx = control["ctx"]
    B, A = ctx["B"], ctx["A"]
    shares = ctx["misc_shares"]
    y0, qa = ctx["anchor"]
    assert ctx["periods"][0]["stub"], "якорь на конце года: правила года якоря не проверить"
    for c in control["cells"]:
        for p, r in zip(ctx["periods"], c["rows"]):
            level = cm.c_traj_mean(cm.c_bget(B, "other.misc_net_real"), p["year"], range(1, cm.QY + 1))
            assert math.isclose(sum(r["misc_q"]), r["misc"], rel_tol=1e-12, abs_tol=1e-9)
            for j, q in enumerate(p["quarters"]):
                assert r["misc_q"][j] == pytest.approx(level * shares[q] * r["idx_q"][j], rel=1e-12, abs=1e-12), \
                    (c["world"], p["year"], q)
        p0, r0 = ctx["periods"][0], c["rows"][0]
        book_nc = cm.c_traj_mean(cm.c_bget(B, "noncore.result_real"), y0, range(1, cm.QY + 1))
        fact_nc = sum(cm._c_fact_line(A, "noncore_net", y0, q) for q in range(1, qa + 1))
        per_quarter = [x / i for x, i in zip(r0["noncore_q"], r0["idx_q"])]
        assert per_quarter == pytest.approx([(book_nc - fact_nc) / p0["n"]] * p0["n"], rel=1e-12)
    # сумма года якоря — выход: факт отчётных кварталов + модель, а не сумма книги
    c = cm.c_cell_of(control, "H", "norm", "schedule")
    fact_misc = sum(cm._c_fact_line(A, "misc_net", y0, q) for q in range(1, qa + 1))
    level0 = cm.c_traj_mean(cm.c_bget(B, "other.misc_net_real"), y0, range(1, cm.QY + 1))
    rest_share = sum(shares[q] for q in ctx["periods"][0]["quarters"])
    if abs(fact_misc - level0 * (1.0 - rest_share)) > 1e-6:
        assert abs(fact_misc + c["rows"][0]["misc"] - level0) > 1e-6


@pytest.mark.tact
def test_fvc_is_the_regime_deviation_on_fair_value_loans(inputs, control):
    """№ 3, В4 (М§4.6): FVC = f × (CoR_corporate − CoR_ref) × кредиты по СС × d/365; опора — путь режима
    credit.fv_loans_ref через мост, без κ-добавки и отклонения A-P2u. В клетке режима-опоры мира N без
    наблюдений FVC = 0; в спаде и кризисе — расход, в мягкой посадке — доход; κ-добавка мира действует
    и на кредиты по СС; строка линейна по fv_loans_factor (0 выключает её)."""
    book, facts = inputs
    ctx = control["ctx"]
    ref = ctx["fv_ref"]
    assert ref == cm.c_bget(ctx["B"], "credit.fv_loans_ref")
    scen = list(cm.c_bget(ctx["B"], "capital.reg_scenarios.ids"))
    quiet = control
    if book["joint"]["regime_update"].get("observations"):   # окно книги не пусто — опора без отклонений A-P2u
        b0 = cm.copy.deepcopy(book)
        b0["joint"]["regime_update"]["observations"] = []
        quiet = cm.c_control_run(b0, facts)
    for s in scen:
        assert all(r["fvc"] == 0.0 for r in cm.c_cell_of(quiet, cm.W_LOW, ref, s)["rows"]), s
    if cm.c_bnum(ctx["B"], "credit.fv_loans_factor") <= 0.0:
        pytest.skip("книга держит строку FVC выключенной")
    total = {r: sum(x["fvc"] for x in cm.c_cell_of(control, cm.W_LOW, r, scen[0])["rows"])
             for r in cm.c_bget(ctx["B"], "regimes.ids")}
    assert total["crisis"] > 0.0 and total["downturn"] > 0.0 and total["soft"] < 0.0, total
    shock = cm.c_cell_of(control, cm.W_LOW, "crisis", scen[0])["rows"][control["years"].index(control["shock_year"])]
    assert shock["fvc"] > 0.0 and min(shock["fvc_q"]) > 0.0
    # знак квартала — знак отклонения CoR клетки от опоры; в мире с κ-добавкой режим-опора несёт её
    for c in control["cells"]:
        for r in c["rows"]:
            for f, cor, cref in zip(r["fvc_q"], r["cor_q"], r["cor_ref_q"]):
                assert f * (cor - cref) >= 0.0 and (f == 0.0) == (cor == cref)
    gaps = [g for per in ctx["wv"]["H"] for g in per["kappa_gaps"]]
    h_ref = cm.c_cell_of(control, "H", ref, scen[0])["rows"]
    if any(g > 0.0 for g in gaps):
        assert sum(r["fvc"] for r in h_ref) > 0.0
    # линейность по множителю: кредиты и CoR от него не зависят
    b2 = cm.copy.deepcopy(book)
    b2["credit"]["fv_loans_factor"] = 0.5 * cm.c_bnum(book, "credit.fv_loans_factor")
    half = cm.c_cell_annual(cm.c_control_context(b2, facts), cm.W_LOW, "crisis", scen[0])["rows"]
    full = cm.c_cell_of(control, cm.W_LOW, "crisis", scen[0])["rows"]
    assert [r["fvc"] for r in half] == pytest.approx([0.5 * r["fvc"] for r in full], rel=1e-12, abs=1e-12)
    b0 = cm.copy.deepcopy(book)
    b0["credit"]["fv_loans_factor"] = 0.0
    off = cm.c_cell_annual(cm.c_control_context(b0, facts), cm.W_LOW, "crisis", scen[0])["rows"]
    assert all(r["fvc"] == 0.0 for r in off)
    assert off[0]["pbt"] > full[0]["pbt"]                 # расход FVC уменьшил прибыль клетки кризиса


@pytest.mark.tact
def test_terminal_excess_income_at_the_marginal_rate(inputs, control):
    """№ 4, остаток № 38 (М§7): доход на избыток капитала вычитается по маржинальной ставке балансирующих
    статей. Избыток — по смеси доходностей бумаг и ликвидности на конце Q в их долях в LA, пока минимум
    ликвидности не свяжет, дальше — по ставке опта; порог связывания — H* = HLA / (1 − m), не сам запас
    HLA (изъятый капитал уменьшает и активы, от которых считается минимум). Недостаток капитала —
    зеркально: сначала гасится добор опта WT, остальное — по смеси. При доле бумаг 0 смесь — ставка
    ликвидности."""
    book, facts = inputs
    ctx = control["ctx"]
    B = ctx["B"]
    sec = cm.c_bnum(B, "volumes.securities_share_of_liquid")
    m = cm.c_bnum(B, "volumes.liquid_min_share")
    nci = cm.c_bnum(B, "pnl.nci_share")
    free = 0
    for c in control["cells"]:
        last = c["rows"][-1]
        mix = sec * last["y_sec_end"] + (1.0 - sec) * last["y_liq_end"]
        wh = last["c_wh_end"]
        assert c["y_bal"] == pytest.approx(mix, rel=1e-12)
        assert c["hla"] == pytest.approx(max(0.0, last["securities"] + last["liquidity"] - m * last["assets"]), abs=1e-6)
        assert c["h_star"] == pytest.approx(c["hla"] / (1.0 - m), rel=1e-12)
        assert c["wt"] == pytest.approx(last["wholesale"] - cm.c_bnum(B, "volumes.wholesale_to_funds") * last["funds"],
                                        abs=1e-6)
        assert c["ni_t1"] == pytest.approx((last["pbt"] * (1.0 + c["g_t"]) - c["y_x"]) * (1.0 - last["tau_eff"])
                                           * (1.0 - nci), rel=1e-12)
        if 0.0 < c["x_t"] <= c["h_star"]:
            assert c["y_x"] == pytest.approx(mix * c["x_t"], rel=1e-12)
            free += 1
        elif c["x_t"] > 0.0:
            assert c["y_x"] == pytest.approx(mix * c["h_star"] + wh * (c["x_t"] - c["h_star"]), rel=1e-12)
        else:
            repaid = min(-c["x_t"], c["wt"])
            assert c["y_x"] == pytest.approx(-(wh * repaid + mix * (-c["x_t"] - repaid)), rel=1e-12)
    assert free, "нет клетки с избытком в пределах порога связывания минимума ликвидности"
    # связанный минимум ликвидности: запаса нет — весь избыток по ставке опта
    cell = {"world": "H", "regime": "norm", "scenario": "upper"}
    base = cm.c_cell_of(control, *cell.values())
    assert base["x_t"] > 0.0
    rows = [dict(r) for r in base["rows"]]
    la_min = m * rows[-1]["assets"]
    rows[-1]["securities"], rows[-1]["liquidity"] = sec * la_min, (1.0 - sec) * la_min
    bound = cm._c_value_cell(ctx, cell, rows, base["decisions"])
    assert bound["hla"] == pytest.approx(0.0, abs=1e-6)
    assert bound["y_x"] == pytest.approx(rows[-1]["c_wh_end"] * bound["x_t"], rel=1e-9)
    # частично связанный: порог H* = запас / (1 − m) меньше избытка — смесь до порога, опт на остаток;
    # избыток между запасом и порогом идёт ещё по смеси (порог — не сам запас)
    part = [dict(r) for r in base["rows"]]
    room = 0.5 * base["x_t"]
    part[-1]["securities"], part[-1]["liquidity"] = sec * (la_min + room), (1.0 - sec) * (la_min + room)
    mixed = cm._c_value_cell(ctx, cell, part, base["decisions"])
    edge = room / (1.0 - m)
    assert mixed["hla"] == pytest.approx(room, rel=1e-9) and room < edge < mixed["x_t"]
    assert mixed["y_x"] == pytest.approx(base["y_bal"] * edge + part[-1]["c_wh_end"] * (mixed["x_t"] - edge), rel=1e-9)
    # недостаток капитала: добавленный капитал сначала гасит добор опта, остальное — по смеси
    short = [dict(r) for r in base["rows"]]
    gap = 2.0 * base["x_t"] / cm.c_bnum(B, "valuation.terminal.excess_capital_multiple") / short[-1]["rwa"]
    short[-1]["n20"], short[-1]["n11_star"] = short[-1]["n20"] - gap, short[-1]["n11_star"] - gap
    for extra in (0.0, 0.25 * base["x_t"], 3.0 * base["x_t"]):
        short[-1]["wholesale_extra"] = extra
        lack = cm._c_value_cell(ctx, cell, short, base["decisions"])
        assert lack["x_t"] == pytest.approx(-base["x_t"], rel=1e-9) and lack["wt"] == extra
        repaid = min(base["x_t"], extra)
        want = -(short[-1]["c_wh_end"] * repaid + base["y_bal"] * (base["x_t"] - repaid))
        assert lack["y_x"] == pytest.approx(want, rel=1e-9)
    # доля бумаг 0: ставка смеси — ставка ликвидности
    b0 = cm.copy.deepcopy(book)
    b0["volumes"]["securities_share_of_liquid"] = 0.0
    liq = cm.c_cell_annual(cm.c_control_context(b0, facts), "H", "norm", "upper")
    assert liq["y_bal"] == liq["rows"][-1]["y_liq_end"]
    if 0.0 < liq["x_t"] <= liq["h_star"]:
        assert liq["y_x"] == pytest.approx(liq["rows"][-1]["y_liq_end"] * liq["x_t"], rel=1e-12)
    # вычет по смеси больше вычета по одной ставке ликвидности, пока бумаги доходнее ликвидности
    last = base["rows"][-1]
    if last["y_sec_end"] > last["y_liq_end"] and base["x_t"] <= base["h_star"]:
        assert base["y_x"] > last["y_liq_end"] * base["x_t"]


@pytest.mark.tact
def test_price_keeps_declared_dividend_out_of_the_discount(inputs, control):
    """№ 25 (М§8.2): цена = [(V0 − D_pend)(1 − g) + D_pend + B] × 1000/N_out; при V0 − D_pend ≤ 0 дисконта
    нет. D_pend — объявленные (declared | paid) дивиденды реестра, которые клетки ещё не вычли из BV;
    запись `recommended` — только плашка: ни в цену, ни в клетки она не входит."""
    n_out = control["ctx"]["A"]["N_out"]
    g = 0.25                                                       # входы формулы, не допущения книги
    assert cm.c_price_of(100.0, 7.0, g, n_out, 20.0) == pytest.approx((80.0 * (1.0 - g) + 20.0 + 7.0) * 1000.0 / n_out)
    assert cm.c_price_of(100.0, 7.0, g, n_out) == pytest.approx((100.0 * (1.0 - g) + 7.0) * 1000.0 / n_out)
    assert cm.c_price_of(10.0, 7.0, g, n_out, 20.0) == pytest.approx((10.0 + 7.0) * 1000.0 / n_out)
    assert cm.c_price_of(-5.0, 0.0, g, n_out) == pytest.approx(-5.0 * 1000.0 / n_out)
    book, facts = inputs
    y0, e_qa = _agm_end(book)
    dps = _history_dps(facts)
    v = e_qa - dt.timedelta(days=10)
    amount = dps * n_out / 1000.0
    seen = {}
    for status in ("declared", "paid", "recommended"):
        f2 = cm.copy.deepcopy(facts)
        f2["dividends"]["register_seed"] = [_declared(y0, dps, e_qa, status)]
        ctx = cm.c_control_context(book, f2, valuation_date=v)
        seen[status] = (ctx["pending_amount"], ctx["bridge_amount"], cm._c_register_dps(ctx, y0))
    assert seen["declared"] == pytest.approx((amount, 0.0, dps), rel=1e-12)
    assert seen["paid"] == pytest.approx((amount, 0.0, dps), rel=1e-12)
    assert seen["recommended"] == (0.0, 0.0, None)
    assert control["pending_amount"] == sum(
        a for a, deduct, _ in cm._c_register_amounts(control["ctx"]) if control["ctx"]["clock"]["v"] < deduct)


def _obs(period: str, cor, nim) -> dict:
    return {"period": period, "cor": cor, "nim": nim, "se_cor": 0.0, "se_nim": 0.0, "basis": "mgmt"}


def _next_period(per: str) -> str:
    y, q = cm.c_per_parse(per)
    return f"{y + 1}Q1" if q == cm.QY else f"{y}Q{q + 1}"


@pytest.mark.tact
def test_regime_filter_reads_only_the_window(inputs):
    """В2 (М§12): фильтр стартует с базовых вероятностей книги и пустого состояния и проходит только
    последние window_obs наблюдений — более ранние на результат не влияют; без наблюдений
    апостериорные — базовые."""
    book, facts = inputs
    q1 = book["meta"]["first_period"]
    q2 = _next_period(q1)
    first, second = _obs(q1, 0.022, 0.058), _obs(q2, 0.011, 0.061)       # наблюдения — входы теста

    def c_ctx(observations, window):
        b = cm.copy.deepcopy(book)
        b["joint"]["regime_update"].update(observations=observations, window_obs=window)
        return cm.c_control_context(b, facts)

    prior = {r: float(v) for r, v in book["joint"]["regime_prob"].items()}
    assert c_ctx([], 1)["posterior"] == prior
    both_w1, only_second = c_ctx([first, second], 1), c_ctx([second], 1)
    assert both_w1["posterior"] == only_second["posterior"]
    assert both_w1["dev"] == only_second["dev"]
    both_w2 = c_ctx([second, first], 2)                                  # порядок записи не важен — по периодам
    assert both_w2["posterior"] != only_second["posterior"]
    assert both_w2["posterior"] == c_ctx([first, second], 4)["posterior"]
    assert both_w2["dev"]["norm"]["cor"]["q"] == cm.c_per_parse(q2)[1]   # отклонение — от последнего наблюдения
    cap = cm.c_bnum(book, "joint.regime_update.max_shift_pp")
    one = c_ctx([first], 4)["posterior"]
    assert max(abs(one[r] - prior[r]) for r in prior) <= cap + 1e-12     # предел сдвига — на квартал


@pytest.mark.tact
def test_observation_of_a_closed_quarter_uses_frozen_expectations(inputs):
    """В2 (М§12): наблюдение квартала не позже якоря несёт замороженные ожидания режимов mu_cor, mu_nim в
    базисе записи; фильтр берёт μ из записи (клетки этот квартал не считают), отклонение в клетке
    затухает от квартала наблюдения: δ_q = ρ^(q − q_obs) × (obs − μ) только в прогнозных кварталах.
    Запись без μ наблюдённой величины — отказ."""
    book, facts = inputs
    anchor = book["meta"]["anchor_period"]
    regimes = list(book["regimes"]["ids"])
    mu_cor = {r: 0.010 + 0.004 * i for i, r in enumerate(regimes)}       # входы теста
    obs_cor = 0.0135
    rec = {**_obs(anchor, obs_cor, None), "mu_cor": mu_cor}
    b = cm.copy.deepcopy(book)
    b["joint"]["regime_update"].update(observations=[rec], floor_share=0.0)   # пол — отдельным тестом
    ctx = cm.c_control_context(b, facts)
    ru = b["joint"]["regime_update"]
    sig, rho, cap = ru["observables"]["cor"]["sigma_pp"], ru["observables"]["cor"]["rho_q"], ru["max_shift_pp"]
    prior = {r: float(v) for r, v in book["joint"]["regime_prob"].items()}
    like = {r: math.exp(-0.5 * (obs_cor - mu_cor[r]) ** 2 / sig ** 2) for r in regimes}
    tot = sum(prior[r] * like[r] for r in regimes)
    raw = {r: prior[r] * like[r] / tot for r in regimes}
    clip = {r: prior[r] + max(-cap, min(cap, raw[r] - prior[r])) for r in regimes}
    z = sum(clip.values())
    clip = {r: v / z for r, v in clip.items()}
    worst = max(abs(clip[r] - prior[r]) for r in regimes)
    if worst > cap + 1e-12:
        clip = {r: prior[r] + cap / worst * (clip[r] - prior[r]) for r in regimes}
    assert ctx["posterior"] == pytest.approx(clip, abs=1e-12)
    ya, qa = cm.c_per_parse(anchor)
    fy, fq = cm.c_per_parse(book["meta"]["first_period"])
    A = ctx["A"]
    for r in regimes:
        dv = ctx["dev"][r]["cor"]
        assert (dv["y"], dv["q"]) == (ya, qa) and "nim" not in ctx["dev"][r]
        if A["bridge"]["cor"][0] == "additive":                       # отклонение не зависит от b_cor
            assert dv["m"] == pytest.approx(obs_cor - mu_cor[r], abs=1e-15)
        assert cm._c_dev_at(dv, fy, fq) == pytest.approx(rho * dv["m"], rel=1e-12)
        assert cm._c_dev_at(dv, fy + 1, fq) == pytest.approx(rho ** (cm.QY + 1) * dv["m"], rel=1e-12)
    # отклонение входит в CoR первого прогнозного квартала клетки
    r0 = regimes[0]
    with_obs = cm.c_cell_annual(ctx, cm.W_LOW, r0, "schedule")["rows"][0]["cor_q"][0]
    b0 = cm.copy.deepcopy(book)
    b0["joint"]["regime_update"].update(observations=[], floor_share=0.0)
    without = cm.c_cell_annual(cm.c_control_context(b0, facts), cm.W_LOW, r0, "schedule")["rows"][0]["cor_q"][0]
    assert with_obs - without == pytest.approx(rho * ctx["dev"][r0]["cor"]["m"], rel=1e-9)
    bad = cm.copy.deepcopy(book)
    bad["joint"]["regime_update"]["observations"] = [_obs(anchor, obs_cor, None)]
    with pytest.raises(cm.ControlInputError):
        cm.c_control_context(bad, facts)
    partial = cm.copy.deepcopy(book)
    partial["joint"]["regime_update"]["observations"] = [{**_obs(anchor, obs_cor, 0.06), "mu_cor": mu_cor}]
    with pytest.raises(cm.ControlInputError):                           # ЧПМ наблюдена, mu_nim нет
        cm.c_control_context(partial, facts)


@pytest.mark.tact
def test_fact_equal_to_expectation_is_learning_on_four_regimes(inputs, control):
    """В2, № 21 (М§12, §18): при смеси режимов наблюдение, равное ожиданию квартала (среднему смеси),
    перевзвешивает режимы — это обучение, а не ноль: сдвиг V0 слоя равен Σ_r ΔP(r) × V_r (V_r — оценка
    при P(r) = 1), вклад отклонений клеток мал против него. У книги с наблюдениями в окне (с 1.4) база — та же
    книга без наблюдений: ожидание квартала — априорное."""
    book, facts = inputs
    base = control
    if control["ctx"]["mu"]:                      # окно книги не пусто — база без наблюдений
        book = cm.copy.deepcopy(book)
        book["joint"]["regime_update"]["observations"] = []
        base = cm.c_control_run(book, facts)
    per = book["meta"]["first_period"]
    y, q = cm.c_per_parse(per)
    ctx = base["ctx"]
    A, B = ctx["A"], ctx["B"]
    regimes = list(B["regimes"]["ids"])
    prior = base["posterior"]
    mu = cm.c_expectations_ctl(ctx, [(y, q)])
    e_cor = sum(prior[r] * mu[("cor", r, y, q)] for r in regimes)
    if max(mu[("cor", r, y, q)] for r in regimes) == min(mu[("cor", r, y, q)] for r in regimes):
        pytest.skip("ожидания CoR режимов равны — обучения нет")
    b2 = cm.copy.deepcopy(book)
    b2["joint"]["regime_update"]["observations"] = [_obs(per, cm.c_to_mgmt(A, "cor", e_cor), None)]
    obs = cm.c_control_run(b2, facts)
    post = obs["posterior"]
    assert max(abs(post[r] - prior[r]) for r in regimes) > 1e-4          # вероятности перевзвешены
    assert sum(prior[r] * obs["ctx"]["dev"][r]["cor"]["m"] for r in regimes) == pytest.approx(0.0, abs=1e-12)
    weights = cm.c_layer_weights(B)
    prs = cm.c_bget(B, "joint.reg_prob_given_regime")
    for name in cm.LAYERS:
        v_r = {r: sum(weights[name].get(c["world"], 0.0) * float(prs[r][c["scenario"]]) * c["v_ri"]
                      for c in base["cells"] if c["regime"] == r) for r in regimes}
        reweight = sum((post[r] - prior[r]) * v_r[r] for r in regimes)
        shift = obs["layers"][name]["v0"] - base["layers"][name]["v0"]
        assert abs(reweight) > 0.0
        assert abs(shift - reweight) <= 0.05 * abs(reweight), (name, shift, reweight)


@pytest.mark.tact
def test_sigma0_split_divides_the_shift(inputs, control):
    """В8 (М§4.5): сдвиг стационарного ЧПМ Δ0 делят активы и пассивы с lt_shift — σ0_A × Σa = split × Δ0,
    σ0_L × Σl_H = (1 − split) × Δ0, Nss(H) = цель при любом split; split = 1 — весь сдвиг на активах
    (σ0_L = 0, прежнее решение); φ от split не зависит, пока lt_shift у обеих книг средств ФЛ одинаков;
    эффективный LT-спред кредитной книги — s^LT + σ0_A × [lt_shift] − φ_A × [Φ_A] × (ref_W − ref_H)."""
    book, facts = inputs
    ctx = control["ctx"]
    tr, B, A = ctx["tr"], ctx["B"], ctx["A"]
    assert tr["split"] == cm.c_bnum(B, "nii.sigma0_split")
    assert tr["sigma0"] * tr["w_assets"] == pytest.approx(tr["split"] * tr["delta0"], abs=1e-15)
    assert tr["sigma0_liab"] * tr["w_liab"] == pytest.approx((1.0 - tr["split"]) * tr["delta0"], abs=1e-15)
    books = B["nii"]["books"]
    same_retail = bool(books[cm.RETAIL_CURRENT].get("lt_shift")) == bool(books[cm.RETAIL_TERM].get("lt_shift"))
    for split in (0.0, 0.5, 1.0):
        b2 = cm.copy.deepcopy(B)
        b2["nii"]["sigma0_split"] = split
        t2 = cm.c_transmission_ctl(b2, A)
        assert t2["nss_ref"] == pytest.approx(tr["target_eng"], abs=1e-12), split
        assert t2["t_real"] == pytest.approx(cm.c_bnum(B, "nii.transmission.target"), abs=1e-12), split
        assert t2["delta0"] == tr["delta0"]
        if same_retail:
            assert t2["phi"] == pytest.approx(tr["phi"], abs=1e-12), split
        if split == 1.0:
            assert t2["sigma0_liab"] == 0.0 and t2["sigma0"] == pytest.approx(tr["delta0"] / tr["w_assets"], rel=1e-12)
        if split == 0.0:
            assert t2["sigma0"] == 0.0 and t2["sigma0_liab"] == pytest.approx(tr["delta0"] / tr["w_liab"], rel=1e-12)
    yl, ql = cm.c_per_parse(B["meta"]["last_period"])
    ref_w = B["nii"]["transmission"]["reference_world"]
    credit = [b for b, sp in books.items() if sp.get("sector")]
    assert sorted(tr["lt_spread"]) == sorted(credit)
    for b in credit:
        sp = books[b]
        s_lt = cm.c_traj_at(sp["spread"], yl, ql) + (tr["sigma0"] if sp.get("lt_shift") else 0.0)
        assert tr["lt_spread"][b][ref_w] == pytest.approx(s_lt, abs=1e-15)          # в мире H сжатия нет
        for w in ctx["worlds"]:
            d_ref = (cm.c_world_quarter(B, w, cm.c_ref_field(sp["ref"]), yl, ql)
                     - cm.c_world_quarter(B, ref_w, cm.c_ref_field(sp["ref"]), yl, ql))
            want = s_lt - (tr["phi_assets"] * d_ref if sp.get("phi") else 0.0)
            assert tr["lt_spread"][b][w] == pytest.approx(want, abs=1e-15), (b, w)
    # отказ: остаток сдвига некому нести
    b3 = cm.copy.deepcopy(B)
    for sp in b3["nii"]["books"].values():
        if sp["side"] != cm.LOANS_SIDE:
            sp["lt_shift"] = False
    if tr["split"] < 1.0:
        with pytest.raises(cm.ControlInputError):
            cm.c_transmission_ctl(b3, A)
    b3["nii"]["sigma0_split"] = 1.0
    assert cm.c_transmission_ctl(b3, A)["sigma0_liab"] == 0.0


# ============================================================================ решения W3 (LEAD-DECISIONS-4)


def _shifted(traj, x: float):
    """Траектория, сдвинутая на x во всех ключах времени и в LT (LT_from — год, не сдвигается)."""
    if isinstance(traj, dict):
        return {k: (v if str(k) == "LT_from" else v + x) for k, v in traj.items()}
    return traj + x


def _with_split(book: dict, split: float) -> dict:
    b = cm.copy.deepcopy(book)
    b["nii"]["phi_split"] = split
    return b


@pytest.mark.tact
def test_phi_split_divides_the_compression(inputs, control):
    """В12, № 74 (М§4.4–§4.5): сжатие φ решается как раньше, доля nii.phi_split = p делит его — φ_A = p × φ
    снимается со спредов кредитных книг Φ_A, остаток — добавка φ_L × X_W к стоимости пассивов Φ_L, φ_L =
    (1 − p) × φ × A / L. Объём сжатия мира один при любой доле: φ, σ0, Nss, T_real, T(W) и пары от p не
    зависят, добавка пассивов в стационаре равна остатку сжатия (1 − p) × φ × C_W. В пути — только
    скаляры и доли ω_b якоря: в мире H сжатия нет, ставка пассива сдвинута на φ_L × X_W,q, от остатка
    кредитов добавка не зависит. При p = 1 расчёт — бит в бит без раскладки."""
    book, facts = inputs
    ctx = control["ctx"]
    tr, B, A = ctx["tr"], ctx["B"], ctx["A"]
    books = B["nii"]["books"]
    yl, ql = cm.c_per_parse(B["meta"]["last_period"])
    ref_w = B["nii"]["transmission"]["reference_world"]
    phi_assets = [b for b, sp in books.items() if sp["side"] == cm.LOANS_SIDE and sp.get("phi")]
    phi_liabs = [b for b, sp in books.items() if sp["side"] != cm.LOANS_SIDE and sp.get("phi")]
    p = cm.c_bnum(B, "nii.phi_split")
    assert tr["phi_split"] == p and set(tr["phi_weights"]) == set(phi_assets)
    assert sum(tr["phi_weights"].values()) == pytest.approx(1.0, abs=1e-15)
    assert tr["phi_a_sum"] == pytest.approx(sum(A["E0"][b] for b in phi_assets) / tr["iea0"], rel=1e-15)
    assert tr["phi_l_sum"] == pytest.approx(sum(A["E0"][b] for b in phi_liabs) / tr["iea0"], rel=1e-15)

    def c_ref_gap(w, b):
        f = cm.c_ref_field(books[b]["ref"])
        return cm.c_world_quarter(B, w, f, yl, ql) - cm.c_world_quarter(B, ref_w, f, yl, ql)

    volume = {w: sum(A["E0"][b] / tr["iea0"] * c_ref_gap(w, b) for b in phi_assets) for w in ctx["worlds"]}   # C_W
    for split in (0.0, 0.3, 1.0):
        t2 = cm.c_transmission_ctl(_with_split(B, split), A)
        for k in ("phi", "sigma0", "sigma0_liab", "t_real", "t_local", "pairs", "nss"):
            assert t2[k] == tr[k], (split, k)                       # от доли не зависят — точно
        assert t2["phi_assets"] == split * tr["phi"]
        for w in ctx["worlds"]:
            assert t2["nss_books"][w] == pytest.approx(tr["nss"][w], abs=1e-14), (split, w)
            assert t2["phi_l_sum"] * t2["phi_liab"] * t2["x_lt"][w] == pytest.approx(
                (1.0 - split) * tr["phi"] * volume[w], abs=1e-15), (split, w)
        if split == 1.0:
            assert t2["phi_liab"] == 0.0 and t2["phi_assets"] == tr["phi"]
        if split == 0.0:                                            # спреды кредитных книг одинаковы во всех мирах
            for b, per_world in t2["lt_spread"].items():
                assert len(set(per_world.values())) == 1, b
    # путь клетки: ставки книг на конец первого периода при p = 0 против p = 1
    high = max(ctx["worlds"], key=lambda w: abs(tr["x_lt"][w]))
    ctx0, ctx1 = (cm.c_control_context(_with_split(book, s), facts) for s in (0.0, 1.0))
    cell = (high, "norm", "schedule")
    c0, c1 = cm.c_cell_annual(ctx0, *cell), cm.c_cell_annual(ctx1, *cell)
    p0 = ctx0["periods"][0]
    wd, wh = ctx0["wv"][high][0], ctx0["wv"][ref_w][0]
    r0, r1 = c0["rows"][0], c1["rows"][0]
    gaps = {b: [wd[cm.c_ref_field(books[b]["ref"])][j] - wh[cm.c_ref_field(books[b]["ref"])][j]
                for j in range(p0["n"])] for b in phi_assets}
    for j in range(p0["n"]):
        assert r0["x_q"][j] == pytest.approx(sum(ctx0["tr"]["phi_weights"][b] * gaps[b][j] for b in phi_assets),
                                             abs=1e-15)
    x_mean = sum(r0["x_q"]) / p0["n"]
    assert x_mean != 0.0
    for b, sp in books.items():
        k_end = cm._c_partial(float(sp["rho"]), p0["n"])[0]
        d = r0["rates_end"][b] - r1["rates_end"][b]
        if b in phi_liabs:                                          # вклады дорожают на φ_L × X_W,q
            assert d == pytest.approx(k_end * ctx0["tr"]["phi_liab"] * x_mean, abs=1e-14), b
        elif b in phi_assets:                                       # спред кредита больше не сжат
            assert d == pytest.approx(k_end * tr["phi"] * sum(gaps[b]) / p0["n"], abs=1e-14), b
        else:
            assert d == 0.0, b
    # в мире H сжатия нет при любой доле
    assert (cm.c_cell_annual(ctx0, ref_w, "norm", "schedule")["v_ri"]
            == cm.c_cell_annual(ctx1, ref_w, "norm", "schedule")["v_ri"])
    # предельный кредит добавку не несёт: остановка кредита в кризисе не меняет X_W,q и ставки пассивов
    crisis = cm.c_cell_annual(ctx0, high, cm.R_CRISIS, "schedule")
    for ra, rb in zip(c0["rows"], crisis["rows"]):
        assert ra["x_q"] == rb["x_q"] and all(ra["rates_end"][b] == rb["rates_end"][b] for b in phi_liabs)
    assert any(ra["loans"] != rb["loans"] for ra, rb in zip(c0["rows"], crisis["rows"]))
    # p = 1 — бит в бит с расчётом без раскладки (флаги phi у пассивов сняты)
    plain = _with_split(book, 1.0)
    for b in phi_liabs:
        plain["nii"]["books"][b]["phi"] = False
    assert cm.c_cell_annual(cm.c_control_context(plain, facts), *cell)["v_ri"] == c1["v_ri"]
    # отказы: доля вне [0; 1]; остаток сжатия некому нести; флаг phi у книг средств ФЛ разный
    with pytest.raises(cm.ControlInputError):
        cm.c_transmission_ctl(_with_split(B, 1.5), A)
    if p < 1.0:
        orphan = cm.copy.deepcopy(B)
        for b in phi_liabs:
            orphan["nii"]["books"][b]["phi"] = False
        with pytest.raises(cm.ControlInputError):
            cm.c_transmission_ctl(orphan, A)
    uneven = cm.copy.deepcopy(B)
    uneven["nii"]["books"][cm.RETAIL_TERM]["phi"] = not bool(books[cm.RETAIL_CURRENT].get("phi"))
    with pytest.raises(cm.ControlInputError):
        cm.c_transmission_ctl(uneven, A)


@pytest.mark.tact
def test_loan_margin_of_the_worlds(control):
    """В12, № 74 (М§4.5): кредитная маржа стационара мира — доходность кредитного портфеля (опора + эффективный
    LT-спред, веса якоря) минус CoR режима norm с κ-добавкой мира минус смесь балансирующих активов. При
    центрах книги она неотрицательна в каждом мире; в мире H от доли сжатия не зависит; перенос сжатия с
    кредитных книг на пассивы поднимает её на φ × C_W / Σ a_кредиты."""
    ctx = control["ctx"]
    tr, B, A = ctx["tr"], ctx["B"], ctx["A"]
    books = B["nii"]["books"]
    yl, ql = cm.c_per_parse(B["meta"]["last_period"])
    ref_w = B["nii"]["transmission"]["reference_world"]
    credit = [b for b, sp in books.items() if sp.get("sector")]
    loans0 = sum(A["E0"][b] for b in credit)
    sec = cm.c_bnum(B, "volumes.securities_share_of_liquid")
    kap = cm.c_bnum(B, "credit.kappa")

    def c_lt(w, b):
        return cm.c_world_quarter(B, w, cm.c_ref_field(books[b]["ref"]), yl, ql)

    def c_rr(w):
        return cm.c_world_quarter(B, w, "real_key", yl, ql)

    cor_norm = cm.c_traj_at(B["regimes"][cm.R_NORM]["cor"], yl, ql)
    if B["regimes"]["cor_basis"] == "mgmt":
        cor_norm = cm.c_to_engine(A, "cor", cor_norm)
    for w in ctx["worlds"]:
        m = tr["loan_margin"][w]
        assert m >= 0.0, (w, m)
        yld = sum(A["E0"][b] * (c_lt(w, b) + tr["lt_spread"][b][w]) for b in credit) / loans0
        bal = (sec * (c_lt(w, cm.SECURITIES) + cm.c_traj_at(books[cm.SECURITIES]["spread"], yl, ql))
               + (1.0 - sec) * (c_lt(w, cm.LIQUIDITY) + cm.c_traj_at(books[cm.LIQUIDITY]["spread"], yl, ql)))
        risk = cor_norm + kap * max(0.0, c_rr(w) - c_rr(cm.W_LOW))
        assert m == pytest.approx(yld - risk - bal, abs=1e-15), w
    on_loans = cm.c_transmission_ctl(_with_split(B, 1.0), A)["loan_margin"]
    on_funds = cm.c_transmission_ctl(_with_split(B, 0.0), A)["loan_margin"]
    assert on_loans[ref_w] == on_funds[ref_w] == tr["loan_margin"][ref_w]
    for w in ctx["worlds"]:
        volume = sum(A["E0"][b] * (c_lt(w, b) - c_lt(ref_w, b)) for b in credit if books[b].get("phi")) / loans0
        assert on_funds[w] - on_loans[w] == pytest.approx(tr["phi"] * volume, abs=1e-15), w
    if tr["phi"] > 0.0:                    # сжатие на кредитных книгах съедает маржу там, где ставки выше мира H
        for w in ctx["worlds"]:
            if tr["x_lt"][w] > 0.0:
                assert on_loans[w] < on_funds[w]


def _world_price(res: dict, world: str) -> float:
    """Цена мира (М§8.2): оценка при P(W) = 1 с вероятностями режимов и сценариев прогона."""
    B, ctx = res["ctx"]["B"], res["ctx"]
    prs = cm.c_bget(B, "joint.reg_prob_given_regime")
    v0 = sum(res["posterior"][c["regime"]] * float(prs[c["regime"]][c["scenario"]]) * c["v_ri"]
             for c in res["cells"] if c["world"] == world)
    return cm.c_price_of(v0, ctx["bridge_amount"], ctx["gov"], ctx["A"]["N_out"], ctx["pending_amount"])


@pytest.mark.tact
def test_sign_of_volume_effects(inputs, control):
    """В12, № 74 (М§4.5, тест знака): снятие остановки кредита в кризисе (loan_growth_override пуст) и
    поправок роста режимов (loan_growth_adj = 0) не снижает точку, и цена мира M меняется при этом не хуже
    цены мира H — рост кредита в мире с высокими ставками не разрушает стоимость."""
    book, facts = inputs
    b2 = cm.copy.deepcopy(book)
    b2["regimes"][cm.R_CRISIS]["loan_growth_override"] = {}
    for r in b2["regimes"]["ids"]:
        b2["regimes"][r]["loan_growth_adj"] = 0.0
    free = cm.c_control_run(b2, facts)
    assert free["point"] >= control["point"], (free["point"], control["point"])
    ref_w = control["ctx"]["B"]["nii"]["transmission"]["reference_world"]
    d_mkt = _world_price(free, cm.W_MKT) - _world_price(control, cm.W_MKT)
    d_ref = _world_price(free, ref_w) - _world_price(control, ref_w)
    assert d_mkt >= d_ref, (d_mkt, d_ref)


@pytest.mark.tact
def test_fade_is_one_sided(inputs, control):
    """В13, № 75 (М§7): ROE'_T = k_T + fade × max(ROE_T − k_T, 0) + min(ROE_T − k_T, 0). Множитель действует
    только на положительный избыток: fade < 1 не повышает оценку ни в одной клетке; при ROE_T ≥ k_T —
    прежняя формула; при ROE_T < k_T оценка от fade не зависит; DDM = RI держится."""
    book, facts = inputs
    f = 0.5 * cm.c_bnum(book, "valuation.terminal.fade")          # вход теста: половина множителя книги
    b2 = cm.copy.deepcopy(book)
    b2["valuation"]["terminal"]["fade"] = f
    faded = cm.c_control_run(b2, facts)
    above = below = 0
    for c0, c1 in zip(control["cells"], faded["cells"]):
        tag = (c0["world"], c0["regime"], c0["scenario"])
        assert c1["roe_t_raw"] == c0["roe_t_raw"] and c1["k_t"] == c0["k_t"], tag
        gap = c1["roe_t_raw"] - c1["k_t"]
        assert c1["v_ri"] <= c0["v_ri"], tag
        assert abs(c1["v_ddm"] - c1["v_ri"]) <= 1e-9 * abs(c1["v_ri"]), tag
        if gap >= 0.0:
            assert c1["roe_t"] == pytest.approx(c1["k_t"] + f * gap, abs=1e-15), tag
            above += 1
        else:
            assert c1["roe_t"] == c1["roe_t_raw"] and c1["v_ri"] == c0["v_ri"], tag
            below += 1
    assert above, "нет клетки с ROE_T не ниже k_T"
    assert faded["point"] <= control["point"]
    if not below:                                # книга без клеток ниже k_T: та же проверка на подмене терминала
        ctx = dict(faded["ctx"])
        base = cm.c_cell_of(control, cm.W_MKT, "downturn", "strict")
        rows = [dict(r) for r in base["rows"]]
        rows[-1]["pbt"] = 0.5 * rows[-1]["pbt"]
        cell = {"world": cm.W_MKT, "regime": "downturn", "scenario": "strict"}
        low_f = cm._c_value_cell(ctx, cell, rows, base["decisions"])
        low_1 = cm._c_value_cell(control["ctx"], cell, [dict(r) for r in rows], base["decisions"])
        assert low_f["roe_t_raw"] < low_f["k_t"] and low_f["v_ri"] == low_1["v_ri"]


@pytest.mark.tact
def test_fvc_reference_stays_with_the_book(inputs, control):
    """В14, № 76 (М§4.6): опора FVC — путь режима-опоры книги до розыгрыша. Сдвиг путей CoR всех режимов на x
    (ось уровня, строка «₽ за 0,1 п.п. CoR») доходит до кредитов по СС: ΔFVC = f × x × Ē^СС × d/365 в каждом
    квартале каждой клетки, резервы растут на x × Ē^АС × d/365. Книга, собранная заново, — сама себе
    база: опора сдвигается вместе с путями, строка не меняется."""
    book, facts = inputs
    ctx = control["ctx"]
    B, A = ctx["B"], ctx["A"]
    if cm.c_bget(B, "credit.segment_relative.enabled"):
        pytest.skip("сегментная поправка CoR включена: сдвиг сегмента — не x")
    f = cm.c_bnum(B, "credit.fv_loans_factor")
    x = cm.c_bnum(book, "valuation.sensitivities.cor_pp")          # шаг строки чувствительности — из книги
    b2 = cm.copy.deepcopy(book)
    for r in b2["regimes"]["ids"]:
        b2["regimes"][r]["cor"] = _shifted(b2["regimes"][r]["cor"], x)
    moved = cm.c_control_context(b2, facts, base_book=book)
    rebuilt = cm.c_control_context(b2, facts)
    x_eng = x
    if B["regimes"]["cor_basis"] == "mgmt":
        x_eng = cm.c_to_engine(A, "cor", x) - cm.c_to_engine(A, "cor", 0.0)
    scen = list(cm.c_bget(B, "capital.reg_scenarios.ids"))
    for key in ((cm.W_LOW, ctx["fv_ref"], scen[0]), (cm.W_MKT, "soft", scen[-1]), ("H", cm.R_CRISIS, scen[0])):
        base = cm.c_cell_of(control, *key)["rows"]
        new = cm.c_cell_annual(moved, *key)["rows"]
        own = cm.c_cell_annual(rebuilt, *key)["rows"]
        for p, r0, r1, r2 in zip(ctx["periods"], base, new, own):
            for j in range(p["n"]):
                want = f * x_eng * r0["fv_avg_q"][j] * r0["days_q"][j] / cm.DAYS_BASE
                assert r1["fvc_q"][j] - r0["fvc_q"][j] == pytest.approx(want, rel=1e-9, abs=1e-12), (key, p["year"], j)
                assert r1["cor_ref_q"][j] == r0["cor_ref_q"][j]
                assert r2["fvc_q"][j] == pytest.approx(r0["fvc_q"][j], rel=1e-9, abs=1e-12)
            assert r1["llp"] - r0["llp"] == pytest.approx(
                x_eng * r0["loans_ac_avg"] * p["days"] / cm.DAYS_BASE, rel=1e-9)
    if f > 0.0 and x_eng > 0.0:                  # в клетке режима-опоры мира N сдвиг уровня — расход FVC
        ref_rows = cm.c_cell_annual(moved, cm.W_LOW, ctx["fv_ref"], scen[0])["rows"]
        assert all(r["fvc"] > 0.0 for r in ref_rows)


def _closed_periods(book: dict, count: int) -> list[str]:
    """Последние count кварталов не позже якоря (наблюдения таких кварталов несут замороженные μ)."""
    y, q = cm.c_per_parse(book["meta"]["anchor_period"])
    out = []
    for _ in range(count):
        out.append(f"{y}Q{q}")
        y, q = (y - 1, cm.QY) if q == 1 else (y, q - 1)
    return out[::-1]


@pytest.mark.tact
def test_regime_probability_floor(inputs):
    """В15, № 77 (М§12): после каждого наблюдения, вслед за пределом сдвига, вероятность режима не ниже
    floor_share × базовой; режим ниже пола ставится на пол, остальные умножаются на общий множитель до
    суммы 1 (с повтором, если ещё кто-то ушёл ниже). floor_share = 0 пол выключает, 1 — выключает
    обучение; значение вне [0; 1] — отказ."""
    floors = {"a": 0.05, "b": 0.10, "c": 0.10, "d": 0.05}                 # входы теста
    same = {"a": 0.06, "b": 0.50, "c": 0.30, "d": 0.14}
    assert cm.c_floor_probabilities(same, floors) == same
    one = cm.c_floor_probabilities({"a": 0.01, "b": 0.50, "c": 0.30, "d": 0.19}, floors)
    assert one["a"] == floors["a"] and sum(one.values()) == pytest.approx(1.0, abs=1e-15)
    assert one["b"] / one["c"] == pytest.approx(0.50 / 0.30, rel=1e-12)   # остальные — общим множителем
    assert one["d"] / one["c"] == pytest.approx(0.19 / 0.30, rel=1e-12)
    chain = cm.c_floor_probabilities({"a": 0.01, "b": 0.88, "c": 0.10, "d": 0.01}, floors)
    assert chain == pytest.approx({"a": 0.05, "b": 0.80, "c": 0.10, "d": 0.05}, abs=1e-15)   # c ушёл ниже пола на втором шаге
    full = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
    assert cm.c_floor_probabilities({"a": 0.10, "b": 0.60, "c": 0.20, "d": 0.10}, full) == pytest.approx(full, abs=1e-15)
    # цепочка наблюдений закрытых кварталов против одного режима
    book, facts = inputs
    regimes = list(book["regimes"]["ids"])
    prior = {r: float(v) for r, v in book["joint"]["regime_prob"].items()}
    share = cm.c_bnum(book, "joint.regime_update.floor_share")
    window = int(book["joint"]["regime_update"]["window_obs"])
    mu = {r: 0.010 + 0.004 * i for i, r in enumerate(regimes)}            # входы теста
    obs = [{**_obs(per, mu[regimes[0]], None), "mu_cor": mu} for per in _closed_periods(book, window)]

    def c_ctx(floor_share):
        b = cm.copy.deepcopy(book)
        b["joint"]["regime_update"].update(observations=obs, floor_share=floor_share)
        return cm.c_control_context(b, facts)

    ctx = c_ctx(share)
    assert len(ctx["updates"]) == len(obs) and ctx["updates"][-1]["posterior"] == ctx["posterior"]
    for step in ctx["updates"]:
        post = step["posterior"]
        assert sum(post.values()) == pytest.approx(1.0, abs=1e-12)
        for r in regimes:
            assert post[r] >= share * prior[r] - 1e-15, (step["period"], r)
    loose = c_ctx(0.0)
    assert sum(loose["posterior"].values()) == pytest.approx(1.0, abs=1e-12)
    washed = [r for r in regimes if loose["posterior"][r] < share * prior[r]]
    if share > 0.0:
        assert washed, "цепочка наблюдений не довела ни один режим до пола — тест ничего не проверяет"
        for r in washed:                                                  # без пола режим вымыт, с полом — стоит на нём
            assert ctx["posterior"][r] == pytest.approx(share * prior[r], abs=1e-15)
    frozen = c_ctx(1.0)
    assert all(step["posterior"] == pytest.approx(prior, abs=1e-15) for step in frozen["updates"])
    assert frozen["dev"][regimes[0]]["cor"]["m"] == ctx["dev"][regimes[0]]["cor"]["m"]   # отклонения клеток пол не трогает
    for bad in (-0.1, 1.2):
        with pytest.raises(cm.ControlInputError):
            c_ctx(bad)


@pytest.mark.tact
def test_each_observed_quarter_keeps_its_own_deviation(inputs):
    """М§12 (толкование — docs/CONTROL-MODEL.md §7): клетка несёт отклонение от последнего наблюдения окна
    не позже квартала. Наблюдённый прогнозный квартал держит своё отклонение и тогда, когда за ним в окне
    есть более позднее наблюдение: у факта (se = 0) ожидание режима плюс отклонение в каждом наблюдённом
    квартале равно наблюдению; после последнего наблюдения отклонение затухает как ρ^k."""
    book, facts = inputs
    q1 = book["meta"]["first_period"]
    q2 = _next_period(q1)
    seen = {q1: 0.020, q2: 0.011}                                         # входы теста
    b = cm.copy.deepcopy(book)
    b["joint"]["regime_update"]["observations"] = [_obs(per, val, None) for per, val in seen.items()]
    if int(b["joint"]["regime_update"]["window_obs"]) < len(seen):
        pytest.skip("окно книги короче двух наблюдений")
    ctx = cm.c_control_context(b, facts)
    plain = cm.c_control_context(book, facts)
    A = ctx["A"]
    rho = cm.c_bnum(book, "joint.regime_update.observables.cor.rho_q")
    y3, q3 = cm.c_per_parse(_next_period(q2))
    for r in book["regimes"]["ids"]:
        dv = ctx["dev"][r]["cor"]
        assert [(y, q) for y, q, _ in dv["trail"]] == [cm.c_per_parse(q1), cm.c_per_parse(q2)]
        assert dv["trail"][-1][2] == dv["m"] and dv["trail"][0][2] != dv["m"]
        for per, val in seen.items():
            y, q = cm.c_per_parse(per)
            assert ctx["mu"][("cor", r, y, q)] + cm._c_dev_at(dv, y, q) == pytest.approx(
                cm.c_to_engine(A, "cor", val), abs=1e-15), (r, per)
        assert cm._c_dev_at(dv, y3, q3) == pytest.approx(rho * dv["m"], rel=1e-12)
    # то же в клетке: CoR наблюдённых кварталов сдвинут каждый на своё отклонение
    p0 = ctx["periods"][0]
    marks = [f"{p0['year']}Q{q}" for q in p0["quarters"]]
    if q1 in marks and q2 in marks:
        r0 = book["regimes"]["ids"][0]
        with_obs = cm.c_cell_annual(ctx, cm.W_LOW, r0, "schedule")["rows"][0]["cor_q"]
        without = cm.c_cell_annual(plain, cm.W_LOW, r0, "schedule")["rows"][0]["cor_q"]
        trail = ctx["dev"][r0]["cor"]["trail"]
        for per, (_, _, m) in zip((q1, q2), trail):
            j = marks.index(per)
            assert with_obs[j] - without[j] == pytest.approx(m, abs=1e-15), per


@pytest.mark.tact
def test_frozen_expectations_do_not_follow_the_book(inputs):
    """В15 (М§12): замороженные μ наблюдения квартала не позже якоря не пересчитываются — запись хранит
    ожидания, действовавшие на момент наблюдения. Смена путей режимов книги (её делает и смена года якоря:
    кризисные ключи сдвигаются) не меняет ни отклонений такого наблюдения, ни апостериорных."""
    book, facts = inputs
    regimes = list(book["regimes"]["ids"])
    mu = {r: 0.010 + 0.004 * i for i, r in enumerate(regimes)}            # входы теста
    rec = {**_obs(book["meta"]["anchor_period"], 0.0135, None), "mu_cor": mu}
    b1 = cm.copy.deepcopy(book)
    b1["joint"]["regime_update"]["observations"] = [rec]
    b2 = cm.copy.deepcopy(b1)
    step = cm.c_bnum(book, "valuation.sensitivities.cor_pp")
    for i, r in enumerate(regimes):
        b2["regimes"][r]["cor"] = _shifted(b2["regimes"][r]["cor"], (i + 1) * step)
    c1, c2 = cm.c_control_context(b1, facts), cm.c_control_context(b2, facts)
    assert c2["posterior"] == c1["posterior"] and c2["dev"] == c1["dev"] and c2["mu"] == c1["mu"]
    assert b2["joint"]["regime_update"]["observations"][0]["mu_cor"] == mu


@pytest.mark.tact
def test_central_cell_follows_the_rule(inputs, control):
    """№ 86 (INTERFACES §4.6): центральная клетка — мода по каждой оси в слое «свой взгляд»: мир с наибольшим
    весом, режим с наибольшей вероятностью после A-P2u, сценарий капитала с наибольшей P(s | r) у этого
    режима; при равенстве — первый по порядку книги. Вероятность клетки и вес сценария — рядом."""
    book, facts = inputs
    B = control["ctx"]["B"]
    pw = {w: float(v) for w, v in B["joint"]["world_prob"].items()}
    prs = B["joint"]["reg_prob_given_regime"]
    cen = control["central"]
    w, r, s = cen["cell"]
    assert pw[w] == max(pw.values()) and control["posterior"][r] == max(control["posterior"].values())
    assert float(prs[r][s]) == max(float(v) for v in prs[r].values())
    assert cen["label"] == f"{w}/{r}/{s}" and cen["scenario_weight"] == float(prs[r][s])
    assert cen["probability"] == pytest.approx(pw[w] * control["posterior"][r] * float(prs[r][s]), abs=1e-15)
    assert any(cen["label"] in line for line in cm.c_summary_lines(control))
    # правило, а не литерал: другие вероятности — другая клетка; равенство — первый по порядку книги
    regimes, worlds = list(B["regimes"]["ids"]), list(B["worlds"]["ids"])
    b2 = cm.copy.deepcopy(book)
    rest = 0.4 / (len(regimes) - 1)
    b2["joint"]["regime_prob"] = {x: (0.6 if x == regimes[-1] else rest) for x in regimes}
    b2["joint"]["world_prob"] = {x: 1.0 / len(worlds) for x in worlds}
    ctx2 = cm.c_control_context(b2, facts)
    alt = cm.c_central_cell({"ctx": ctx2, "posterior": ctx2["posterior"]})
    last = prs[regimes[-1]]
    scen = list(B["capital"]["reg_scenarios"]["ids"])
    best = [x for x in scen if float(last[x]) == max(float(v) for v in last.values())][0]
    assert alt["cell"] == (worlds[0], regimes[-1], best)


@pytest.mark.tact
def test_central_cell_agrees_with_the_book_results(control):
    """№ 86: клетка «центральная» в results.json (её выпускает ядро) — та же, что по правилу у контрольной
    модели, с той же вероятностью и весом сценария."""
    path = cm.BOOK_DIR / "results.json"
    if not path.exists():
        pytest.skip("results.json ещё не выпущен")
    doc = json.loads(path.read_text(encoding="utf-8"))
    cen = doc.get("central_cell")
    version = str(cm.c_bget(control["ctx"]["B"], "meta.version"))
    if str(doc.get("book_version")) != version or not isinstance(cen, dict) or "cell" not in cen:
        pytest.skip("results.json — прежней версии книги или без клетки по правилу (перевыпуск — после правок ядра)")
    mine = control["central"]
    assert cen["cell"] == mine["label"]
    assert cen["probability"] == pytest.approx(mine["probability"], abs=1e-12)
    assert cen["scenario_weight"] == pytest.approx(mine["scenario_weight"], abs=1e-12)


@pytest.mark.tact
def test_average_balances_of_the_year(inputs, control):
    """Годовая агрегация средних остатков (docs/CONTROL-MODEL.md §3). Небалансирующая статья растёт внутри
    периода одним квартальным множителем: средний остаток — сумма квартальных трапеций геометрического
    пути, у растущей статьи он ниже полусуммы начала и конца. Балансирующие активы: к полусумме начала и
    конца прибавляется время капитальных потоков по квартальному профилю дохода — доход квартала j входит
    в активы со следующего квартала, вес (n − j)/n − ½ — и выплаты, вес (n − j_выплаты + ½)/n − ½, и
    кривизна небалансирующих статей. При связанном на обоих концах минимуме ликвидности LA стоит на
    минимуме, а время потоков несёт добор опта."""
    assert cm._c_geo_path(100.0, 100.0, cm.QY) == [100.0] * (cm.QY + 1)                # входы теста
    path = cm._c_geo_path(100.0, 121.0, 2)
    assert path == pytest.approx([100.0, 110.0, 121.0], rel=1e-12)
    assert cm._c_path_mean(path) == pytest.approx((105.0 + 115.5) / 2.0, rel=1e-12)
    assert cm._c_path_mean(path) < (100.0 + 121.0) / 2.0
    assert cm._c_geo_path(0.0, 8.0, 2) == [0.0, 4.0, 8.0]                              # с нуля — линейно
    ctx = control["ctx"]
    A = ctx["A"]
    loans = [b for b, sp in ctx["B"]["nii"]["books"].items() if sp.get("sector")]
    fvs = cm.c_bnum(ctx["B"], f"nii.books.{cm.CORP_LOANS}.fv_share")
    ssl = cm.c_bnum(ctx["B"], "volumes.securities_share_of_liquid")
    wtf = cm.c_bnum(ctx["B"], "volumes.wholesale_to_funds")
    funds0 = sum(A["E0"][b] for b in ("retail_current", "retail_term", cm.CORP_FUNDS))
    extra0 = max(0.0, A["E0"][cm.WHOLESALE] - wtf * funds0)   # добор опта якоря (книга 1.4: отношение заморожено)
    for c in control["cells"][::7]:
        ac_prev = sum(A["E0"][b] for b in loans) - A["fv_loans"]
        la_prev = A["E0"][cm.SECURITIES] + A["E0"][cm.LIQUIDITY]
        extra_prev = extra0
        for p, r in zip(ctx["periods"], c["rows"]):
            n = p["n"]
            assert r["loans_ac_avg"] < (ac_prev + r["loans_ac"]) / 2.0 or r["loans_ac"] <= ac_prev
            timing = sum(((n - (j + 1)) / n - 0.5) * x for j, x in enumerate(r["ci_q"]))
            assert r["shift_la"] == pytest.approx(timing - r["paid_shift"] + r["curve_la"], rel=1e-6, abs=1e-6)
            la_end = r["securities"] + r["liquidity"]
            if r["wholesale_extra"] == 0.0 and extra_prev == 0.0:                   # минимум не связывает
                assert r["la_avg"] == pytest.approx((la_prev + la_end) / 2.0 + r["shift_la"], rel=1e-12)
                assert r["wholesale_avg"] == pytest.approx(
                    cm.c_bnum(ctx["B"], "volumes.wholesale_to_funds") * r["funds_avg"], rel=1e-12)
            ac_prev, la_prev, extra_prev = r["loans_ac"], la_end, r["wholesale_extra"]
    # связанный минимум ликвидности: доля минимума — вход теста, выше доли LA в активах якоря
    book, facts = inputs
    b2 = cm.copy.deepcopy(book)
    la0 = A["E0"][cm.SECURITIES] + A["E0"][cm.LIQUIDITY]
    assets0 = sum(A["E0"][b] for b in loans) - A["AL"] + la0 + A["OA"]
    m = min(0.9, la0 / assets0 + 0.15)
    b2["volumes"]["liquid_min_share"] = m
    rows = cm.c_cell_annual(cm.c_control_context(b2, facts), cm.W_MKT, "norm", "schedule")["rows"]
    bound = [(r0, r1) for r0, r1 in zip(rows, rows[1:]) if r0["wholesale_extra"] > 0.0 and r1["wholesale_extra"] > 0.0]
    assert bound, "минимум ликвидности не связал ни одного года подряд"
    wtf = cm.c_bnum(ctx["B"], "volumes.wholesale_to_funds")
    for r0, r1 in bound:
        assert r1["securities"] + r1["liquidity"] == pytest.approx(m * r1["assets"], rel=1e-9)
        assert r1["la_avg"] == pytest.approx(m / (1.0 - m) * r1["assets_nb_avg"], rel=1e-12)
        free = ((r0["securities"] + r0["liquidity"] - r0["wholesale_extra"]
                 + r1["securities"] + r1["liquidity"] - r1["wholesale_extra"]) / 2.0 + r1["shift_la"])
        base = r1["wholesale_avg"] - (r1["la_avg"] - free)                          # опт без добора
        assert base == pytest.approx(wtf * r1["funds_avg"], rel=1e-9)
        assert r1["wholesale_avg"] > base


def _dated_record(year: int, dps: float, *, pay=None, reg=None, status: str = "declared") -> dict:
    """Запись реестра за год прибыли с датами в заданных кварталах (год, квартал): отсечка — на 20-й день
    квартала reg, выплата — на 34-й день квартала pay; None — даты в записи нет."""
    rec = {"year": year, "dps": dps, "status": status, "sources": ["сверка М§17"]}
    if reg is not None:
        day = cm.c_q_first_day(*reg) + dt.timedelta(days=19)
        rec.update(record_date=day.isoformat(), ex_date=day.isoformat(),
                   last_buy_date=(day - dt.timedelta(days=1)).isoformat())
    if pay is not None:
        rec["pay_date"] = (cm.c_q_first_day(*pay) + dt.timedelta(days=33)).isoformat()
    return rec


@pytest.mark.tact
def test_declared_dividend_follows_the_register_dates(inputs):
    """М§5.4: у объявленного дивиденда (запись declared | paid) даты записи сильнее календаря книги — выплата
    (дивиденды к выплате и ликвидность) в квартале pay_date, вычет из регуляторного капитала в квартале
    отсечки; нет даты — квартал календаря. Запись recommended календарь не меняет."""
    book, facts = inputs
    y0, _ = _agm_end(book)
    dps = _history_dps(facts)
    cal = book["dividends"]["calendar"]
    agm, pay_q, reg_q = int(cal["agm_quarter"]), int(cal["payment_quarter"]), int(cal["reg_deduction_quarter"])
    ya = y0 + 1
    cell = ("H", "norm", "schedule")

    def c_rows(register):
        f2 = cm.copy.deepcopy(facts)
        f2["dividends"]["register_seed"] = register
        ctx = cm.c_control_context(book, f2)
        return cm.c_cell_annual(ctx, *cell), ctx

    plain, ctx = c_rows([_dated_record(y0, dps)])                          # записи без дат — календарь книги
    i = [p["year"] for p in ctx["periods"]].index(ya)
    n = ctx["periods"][i]["n"]
    amount = dps * ctx["A"]["N_out"] / 1000.0
    assert plain["rows"][i]["div"] == pytest.approx(amount, rel=1e-12) and plain["decisions"][y0]["kind"] == "register"
    same, _ = c_rows([_dated_record(y0, dps, pay=(ya, pay_q), reg=(ya, reg_q))])
    assert same["v_ri"] == plain["v_ri"]                                   # даты в кварталах календаря — тот же расчёт
    nii = {}
    for q in range(agm, cm.QY + 1):
        c, _ = c_rows([_dated_record(y0, dps, pay=(ya, q), reg=(ya, min(q, reg_q)))])
        row = c["rows"][i]
        assert row["dp"] == 0.0 and row["div"] == pytest.approx(amount, rel=1e-12)
        # вес выплаты в среднем остатке ликвидных активов года: (n − j + ½)/n — чем позже, тем дольше деньги в балансе
        assert row["paid_shift"] == pytest.approx(((n - q + 0.5) / n - 0.5) * amount, rel=1e-9, abs=1e-9), q
        nii[q] = row["nii"]
    assert all(nii[q + 1] > nii[q] for q in range(agm, cm.QY))
    # выплата и отсечка в следующем году: на конец года ГОСА сумма — в дивидендах к выплате и ещё в
    # регуляторном капитале; через год — выплачена
    late, _ = c_rows([_dated_record(y0, dps, pay=(ya + 1, 1), reg=(ya + 1, 1))])
    assert late["rows"][i]["dp"] == pytest.approx(amount, rel=1e-12) and late["rows"][i + 1]["dp"] == 0.0
    assert late["rows"][i]["dpreg"] == pytest.approx(amount, rel=1e-12) and late["rows"][i + 1]["dpreg"] == 0.0
    assert plain["rows"][i]["dpreg"] == 0.0 and late["rows"][i]["n20"] > plain["rows"][i]["n20"]
    # дивиденд, вычтенный в якоре: сумма — факт баланса, квартал выплаты — по записи за этот год прибыли
    if ctx["A"]["DP"] and ctx["periods"][0]["stub"]:
        qs = ctx["periods"][0]["quarters"]
        n0 = len(qs)
        for q in qs:
            c, _ = c_rows([_dated_record(y0 - 1, dps, pay=(y0, q), reg=(y0, qs[0]), status="paid")])
            row = c["rows"][0]
            want = ((n0 - (qs.index(q) + 1) + 0.5) / n0 - 0.5) * ctx["A"]["DP"]
            assert row["dp"] == 0.0 and row["paid_shift"] == pytest.approx(want, rel=1e-9, abs=1e-9), q
    # recommended — только плашка: ни дивиденда года, ни календаря она не задаёт
    advised, _ = c_rows([_dated_record(y0, dps, pay=(ya, cm.QY), reg=(ya, cm.QY), status="recommended")])
    nothing, _ = c_rows([])
    assert advised["v_ri"] == nothing["v_ri"] and advised["decisions"][y0]["kind"] != "register"


# ============================================================================ сверка с ядром


def _core_grid(book_path=None):
    """Прогон ядра (импорт только здесь). Нет ядра или его функций — None и причина."""
    try:
        book_mod = importlib.import_module("model.book")
        grid_mod = importlib.import_module("model.grid")
    except Exception as exc:                                   # ядро ещё пишется
        return None, f"ядро не импортируется ({type(exc).__name__}: {exc})"
    if not (hasattr(book_mod, "load_book") and hasattr(book_mod, "load_facts")
            and hasattr(grid_mod, "run_grid")):
        return None, "в ядре нет load_book / load_facts / run_grid (INTERFACES §4.1, §4.3)"
    facts = book_mod.load_facts()
    book = book_mod.load_book(facts=facts)
    return grid_mod.run_grid(book, facts), ""


def _quarter_index(ctl: dict) -> dict[int, list[int]]:
    """Год → номера кварталов сетки ядра (1…Q), которые покрывает период контрольной модели."""
    y0, qa = ctl["ctx"]["anchor"]
    out = {}
    for p in ctl["ctx"]["periods"]:
        out[p["year"]] = [(p["year"] - y0) * cm.QY + (q - qa) for q in p["quarters"]]
    return out


def verdict(diff, diff_rel, tol, tol_kind, tol_abs=None) -> bool:
    """Вердикт строки по тому, что в ней записано (то же видит читатель витрины): abs — |diff| ≤ tol;
    rel — |diff_rel| ≤ tol, или |diff| ≤ tol_abs у строк с правилом «или» (М§17), или обе величины —
    ноль (diff_rel не определена при core = 0)."""
    if diff is None or tol is None:
        return False
    if tol_kind == ABS:
        return abs(diff) <= tol
    if diff_rel is not None and abs(diff_rel) <= tol:
        return True
    if tol_abs is not None and abs(diff) <= tol_abs:
        return True
    return diff_rel is None and diff == 0.0


def _row(what, unit, core, control, tol, tol_kind, *, group, tol_abs=None, ok=None):
    """Строка сверки: diff = control − core в единицах строки, diff_rel = control/core − 1 (None при
    core = 0), вид допуска tol_kind — с чем сравнивается tol; tol_abs — абсолютный пол правила «или».
    group — группа для выбора худшей строки сводки (в файл не пишется)."""
    diff = diff_rel = None
    if tol_kind == REL:                      # машинный ноль относительной строки — ноль (иначе шум 1e-15 дал бы −100 %)
        core, control = (0.0 if (v is not None and abs(v) <= TOL_ZERO_BN) else v for v in (core, control))
    if core is not None and control is not None:
        diff = control - core
        diff_rel = control / core - 1.0 if core != 0.0 else None
    row = {"what": what, "unit": unit, "core": core, "control": control, "diff": diff, "diff_rel": diff_rel,
           "tol": tol, "tol_kind": tol_kind,
           "ok": bool(verdict(diff, diff_rel, tol, tol_kind, tol_abs) if ok is None else ok), "group": group}
    if tol_abs is not None:
        row["tol_abs"] = tol_abs
    return row


def _missing(what, unit, control=None):
    return _row(f"{what}: нет у ядра", unit, None, control, None, ABS, group="missing", ok=False)


def _bridge_row(what, core, control):
    """Мост и объявленные дивиденды: ≤ 1 % (М§17); при нуле у ядра обе модели обязаны дать ноль."""
    if core is None:
        return _missing(what, U_BN, control)
    if core == 0.0:
        return _row(what, U_BN, core, control, TOL_ZERO_BN, ABS, group="bridge")
    return _row(what, U_BN, core, control, TOL_BRIDGE, REL, group="bridge")


def dps_floor(ctl: dict, cell: dict, profit_year: int) -> float:
    """Пол допуска DPS (М§17): 0,5 % BV конца года выплаты на акцию — тот же год, что у строки Div."""
    row = cell["rows"][ctl["years"].index(profit_year + 1)]
    return DIV_FLOOR_BV * abs(row["bv"]) * 1000.0 / ctl["ctx"]["A"]["N_out"]


def compare_with_core(run, ctl) -> list[dict]:
    """Все строки сверки М§17 (формат строки — _row)."""
    rows = []
    tr_c = run.ctx.transmission
    tr = ctl["transmission"]
    derived = [("передача: сдвиг спредов активов σ0", U_PP, getattr(tr_c, "sigma0", None), tr["sigma0"]),
               ("передача: сдвиг стоимости пассивов σ0", U_PP, getattr(tr_c, "sigma0_liab", None), tr["sigma0_liab"]),
               ("передача: сжатие спреда φ", U_NUM, getattr(tr_c, "phi", None), tr["phi"]),
               ("передача: сжатие φ на спредах кредитных книг", U_NUM, getattr(tr_c, "phi_assets", None),
                tr["phi_assets"]),
               ("передача: добавка сжатия φ к стоимости средств клиентов", U_NUM, getattr(tr_c, "phi_liab", None),
                tr["phi_liab"]),
               ("передача: реализованная M − N", U_NUM, getattr(tr_c, "t_real", None), tr["t_real"])]
    for w, v in tr["t_local"].items():
        derived.append((f"передача: локальная в мире {w}", U_NUM, tr_c.t_local.get(w), v))
    for what, unit, core_v, ctl_v in derived:
        rows.append(_missing(what, unit, ctl_v) if core_v is None
                    else _row(what, unit, core_v, ctl_v, TOL_DERIVED, ABS, group="derived"))
    # парные передачи и ROE-эквивалент (М§4.5, §17): аналитические, одно определение — до 1e-6
    pairs_c = getattr(tr_c, "pairs", None)
    for k, v in tr["pairs"].items():
        what = "передача: пара миров " + " → ".join(k.split("_"))
        if pairs_c is None or k not in pairs_c:
            rows.append(_missing(what, U_NUM, v))
        elif pairs_c[k] is None or v is None:                       # равные key^LT — пара не определена
            rows.append(_row(what, U_NUM, pairs_c[k], v, TOL_DERIVED, ABS, group="derived",
                             ok=pairs_c[k] is None and v is None))
        else:
            rows.append(_row(what, U_NUM, pairs_c[k], v, TOL_DERIVED, ABS, group="derived"))
    roe_c = getattr(tr_c, "roe_equiv", None)
    what = "передача: эквивалент в ROE"
    rows.append(_missing(what, U_NUM, tr["roe_equiv"]) if roe_c is None
                else _row(what, U_NUM, roe_c, tr["roe_equiv"], TOL_DERIVED, ABS, group="derived"))
    spreads_c = getattr(tr_c, "lt_spread", None)
    for b, per_world in tr["lt_spread"].items():
        for w, v in per_world.items():
            what = f"эффективный долгосрочный спред к опоре: {BOOK_TITLES.get(b, b)}, мир {w}"
            core_v = None if spreads_c is None else spreads_c.get(b, {}).get(w)
            rows.append(_missing(what, U_PP, v) if core_v is None
                        else _row(what, U_PP, core_v, v, TOL_DERIVED, ABS, group="lt_spread"))
    margin_c = getattr(tr_c, "loan_margin", None)
    for w, v in tr["loan_margin"].items():
        what = f"кредитная маржа стационара, мир {w}"
        core_v = None if margin_c is None else margin_c.get(w)
        rows.append(_missing(what, U_PP, v) if core_v is None
                    else _row(what, U_PP, core_v, v, TOL_DERIVED, ABS, group="derived"))
    for name in cm.LAYERS:
        rows.append(_row(f"V0 слоя «{LAYER_TITLES[name]}»", U_BN, run.layers[name].v0, ctl["layers"][name]["v0"],
                         TOL_VALUE, REL, group="value"))
    for name, core_v, ctl_v in (("низ", run.low, ctl["low"]), ("верх", run.high, ctl["high"]),
                                ("точка при λ книги", run.point, ctl["point"])):
        rows.append(_row(f"цена: {name}", U_RUB, core_v, ctl_v, TOL_VALUE, REL, group="value"))
    rows.append(_bridge_row("мост объявленных дивидендов", run.bridge_amount, ctl["bridge_amount"]))
    rows.append(_bridge_row("объявленный дивиденд до вычета из капитала модели",
                            getattr(run, "pending_dividend", None), ctl["pending_amount"]))
    qidx = _quarter_index(ctl)
    for c in ctl["cells"]:
        key = (c["world"], c["regime"], c["scenario"])
        cc = run.cell(*key)
        tag = "/".join(key)
        rows.append(_row(f"стоимость клетки {tag}", U_BN, cc.v_ri, c["v_ri"], TOL_VALUE, REL, group="cell"))
        for name, grp, core_v, ctl_v in (("ROE терминала", "roe_t", cc.roe_t, c["roe_t"]),
                                         ("плата за капитал на терминале", "k_t", cc.k_t, c["k_t"]),
                                         ("рост терминала", "g_t", cc.g_t, c["g_t"])):
            rows.append(_row(f"{name}, клетка {tag}", U_PCT, core_v, ctl_v, TOL_RATIO, ABS, group=grp))
        rwa_q = c["rows"][-1]["rwa"]
        rows.append(_row(f"избыток капитала на терминале, клетка {tag}", U_BN, cc.x_t, c["x_t"],
                         TOL_RATIO * rwa_q, ABS, group="x_t"))
        # доход на избыток (М§7, §17) — строка ОПУ терминального года; поле ядра — договор INTERFACES §4.3
        yx_core = getattr(cc, "y_x", None)
        what = f"доход на избыток капитала на терминале, клетка {tag}"
        rows.append(_missing(what, U_BN, c["y_x"]) if yx_core is None
                    else _row(what, U_BN, yx_core, c["y_x"], TOL_LINE, REL, group="y_x",
                              tol_abs=TOL_RATIO * rwa_q * abs(c["y_bal"])))
        q = cc.quarters
        # TV сверяется в форме DDM (капитал на T): TV_DDM = BV_Q + TV_RI (М§7). Сам TV_RI — малая
        # разность, усиленная 1/(k_T − g_T): 0,1 п.п. ROE_T даёт у мира M ≈ 5 % TV_RI.
        if "bv" in q and q["bv"][-1] is not None:
            rows.append(_row(f"терминальная стоимость с капиталом, клетка {tag}", U_BN, q["bv"][-1] + cc.tv_ri,
                             c["rows"][-1]["bv"] + c["tv_ri"], TOL_VALUE, REL, group="tv"))
        for line in FLOW_LINES + STOCK_LINES + RATIO_LINES + ("div",):
            if line not in q:
                rows.append(_missing(f"{LINE_TITLES[line]}, клетка {tag}", U_PCT if line in RATIO_LINES else U_BN))
        for i, p in enumerate(ctl["ctx"]["periods"]):
            r = c["rows"][i]
            ids = qidx[p["year"]]
            bv_scale = abs(r["bv"])
            for line in FLOW_LINES + ("div",):
                if line not in q:
                    continue
                what = f"{LINE_TITLES[line]} {p['year']}, клетка {tag}"
                vals = [q[line][j] for j in ids]
                if any(v is None for v in vals):
                    rows.append(_missing(what, U_BN, r[line]))
                    continue
                core_v, ctl_v = sum(vals), r[line]
                if line in ABS_LINES:
                    core_v, ctl_v = abs(core_v), abs(ctl_v)
                floor = (TOL_SMALL_BV * bv_scale if line in SMALL_BV_LINES
                         else DIV_FLOOR_BV * bv_scale if line == "div" else None)
                rows.append(_row(what, U_BN, core_v, ctl_v, TOL_LINE, REL, group=line, tol_abs=floor))
            j_end = ids[-1]
            for line in STOCK_LINES:
                if line in q and q[line][j_end] is not None:
                    rows.append(_row(f"{LINE_TITLES[line]} на конец {p['year']}, клетка {tag}", U_BN,
                                     q[line][j_end], r[line], TOL_LINE, REL, group=line))
            for line in RATIO_LINES:
                if line in q and q[line][j_end] is not None:
                    rows.append(_row(f"{LINE_TITLES[line]} на конец {p['year']}, клетка {tag}", U_PCT,
                                     q[line][j_end], r[line], TOL_RATIO, ABS, group=line))
        for yp, d in c["decisions"].items():
            what = f"DPS за {yp}, клетка {tag}"
            core_dps = cc.dps.get(yp) if hasattr(cc.dps, "get") else None
            rows.append(_missing(what, U_RUB, d["dps"]) if core_dps is None
                        else _row(what, U_RUB, core_dps, d["dps"], TOL_LINE, REL, group="dps",
                                  tol_abs=dps_floor(ctl, c, yp)))
    return rows


def exact_match(run, ctl) -> bool:
    """V0 всех слоёв совпали с ядром до 1e-9 — признак общего кода (М§17)."""
    return all(abs(ctl["layers"][n]["v0"] / run.layers[n].v0 - 1.0) <= EXACT for n in cm.LAYERS)


def _fail_lines(bad: list[dict]) -> str:
    return "\n".join(f"{r['what']}: ядро {r['core']}, контроль {r['control']}, допуск {r['tol']} ({r['tol_kind']}"
                     + (f", или {r['tol_abs']}" if "tol_abs" in r else "") + ")" for r in bad[:60])


@pytest.mark.ci_only
def test_control_matches_core_within_tolerances(control):
    run, why = _core_grid()
    if run is None:
        pytest.skip(f"сверять не с чем: {why}. Сверка включится сама, когда ядро (model.grid.run_grid) "
                    f"будет собрано")
    rows = compare_with_core(run, control)
    bad = [r for r in rows if not r["ok"]]
    if bad:
        pytest.fail(f"вне допусков М§17: {len(bad)} из {len(rows)} строк сверки\n" + _fail_lines(bad))


def _closed_quarter_obs(book: dict, cor: float) -> dict:
    """Наблюдение квартала якоря с замороженными μ (их пишет перезаякоривание, М§12): μ CoR — значения
    путей режимов книги в первом прогнозном квартале (вход сверки, не допущение)."""
    y, q = cm.c_per_parse(book["meta"]["first_period"])
    mu = {r: cm.c_traj_at(book["regimes"][r]["cor"], y, q) for r in book["regimes"]["ids"]}
    return {"period": book["meta"]["anchor_period"], "cor": cor, "nim": None, "se_cor": 0.0, "se_nim": None,
            "basis": "mgmt", "mu_cor": mu}


OBS_SCENARIOS = {   # наблюдения A-P2u (М§12) первого прогнозного квартала — входы сверки, не допущения
    "факт-ожидание": {"first": {"cor": 0.012, "nim": 0.060, "se_cor": 0.0, "se_nim": 0.0, "basis": "mgmt"}},
    "плохой-квартал": {"first": {"cor": 0.018, "nim": 0.058, "se_cor": 0.0, "se_nim": 0.0, "basis": "mgmt"}},
    "нау-каст-cor": {"first": {"cor": 0.014, "nim": None, "se_cor": 0.001, "se_nim": None, "basis": "mgmt"}},
    # квартал якоря с замороженными μ и первый прогнозный квартал: оба в окне
    "закрытый-квартал": {"closed": 0.019, "first": {"cor": 0.013, "nim": None, "se_cor": 0.0, "se_nim": None,
                                                    "basis": "mgmt"}},
    # то же при окне в одно наблюдение: квартал якоря из окна выпал
    "окно-1": {"closed": 0.019, "window": 1, "first": {"cor": 0.013, "nim": None, "se_cor": 0.0, "se_nim": None,
                                                       "basis": "mgmt"}},
    # три высоких квартала подряд (квартал якоря и два прогнозных): режим с низкой CoR доходит до пола (В15)
    "пол": {"closed": 0.024, "first": {"cor": 0.024, "nim": None, "se_cor": 0.0, "se_nim": None, "basis": "mgmt"},
            "more": [{"cor": 0.024, "nim": None, "se_cor": 0.0, "se_nim": None, "basis": "mgmt"}]},
}
TOL_POSTERIOR = 0.02     # вероятность режима после A-P2u (М§17)


def _book_with_observations(book: dict, spec: dict) -> dict:
    b2 = cm.copy.deepcopy(book)
    obs = [{"period": book["meta"]["first_period"], **spec["first"]}]
    for extra in spec.get("more", ()):                          # следующие прогнозные кварталы подряд
        obs.append({"period": _next_period(obs[-1]["period"]), **extra})
    if "closed" in spec:
        obs.insert(0, _closed_quarter_obs(book, spec["closed"]))
    b2["joint"]["regime_update"]["observations"] = obs
    if "window" in spec:
        b2["joint"]["regime_update"]["window_obs"] = spec["window"]
    return b2


@pytest.mark.ci_only
@pytest.mark.parametrize("name", list(OBS_SCENARIOS))
def test_control_matches_core_with_observations(inputs, name):
    """Ветка A-P2u, которую книга держит пустой: апостериорные вероятности режимов (окно, пол, замороженные
    μ наблюдения квартала якоря) и точка."""
    book, facts = inputs
    try:
        book_mod = importlib.import_module("model.book")
        grid_mod = importlib.import_module("model.grid")
        run_fn, from_dict = grid_mod.run_grid, book_mod.book_from_dict
    except Exception as exc:
        pytest.skip(f"ядро не готово ({type(exc).__name__}: {exc})")
    b2 = _book_with_observations(book, OBS_SCENARIOS[name])
    ctl = cm.c_control_run(b2, facts)
    core_facts = book_mod.load_facts()
    run = run_fn(from_dict(b2, facts=core_facts), core_facts)
    for r, p in ctl["posterior"].items():
        assert abs(run.ctx.posterior[r] - p) <= TOL_POSTERIOR, (r, run.ctx.posterior[r], p)
    for layer in cm.LAYERS:
        assert abs(ctl["layers"][layer]["v0"] / run.layers[layer].v0 - 1.0) <= TOL_VALUE, layer
    assert abs(ctl["point"] / run.point - 1.0) <= TOL_VALUE
    bad = [r for r in compare_with_core(run, ctl) if not r["ok"]]         # отклонения клеток — в строках ОПУ
    if bad:
        pytest.fail(f"вне допусков М§17: {len(bad)} строк\n" + _fail_lines(bad))


@pytest.mark.tact
def test_window_scenarios_of_the_reconciliation_differ(inputs):
    """Сценарии сверки «закрытый квартал» и «окно 1» действительно различаются окном: при окне в одно
    наблюдение квартал якоря на апостериорные не влияет."""
    book, facts = inputs
    both = cm.c_control_context(_book_with_observations(book, OBS_SCENARIOS["закрытый-квартал"]), facts)
    last = cm.c_control_context(_book_with_observations(book, OBS_SCENARIOS["окно-1"]), facts)
    spec = {"first": OBS_SCENARIOS["окно-1"]["first"]}
    alone = cm.c_control_context(_book_with_observations(book, spec), facts)
    assert last["posterior"] == alone["posterior"]
    if cm.c_bget(book, "joint.regime_update.window_obs") > 1:
        assert both["posterior"] != last["posterior"]


@pytest.mark.tact
def test_floor_scenario_of_the_reconciliation_binds(inputs):
    """Сценарий сверки «пол» действительно доводит режим до пола вероятности: без пола тот же набор
    наблюдений оставил бы его ниже floor_share × базовой."""
    book, facts = inputs
    share = cm.c_bnum(book, "joint.regime_update.floor_share")
    if share <= 0.0:
        pytest.skip("книга держит пол вероятности выключенным")
    b2 = _book_with_observations(book, OBS_SCENARIOS["пол"])
    if len(b2["joint"]["regime_update"]["observations"]) > int(b2["joint"]["regime_update"]["window_obs"]):
        pytest.skip("окно книги короче сценария")
    prior = {r: float(v) for r, v in book["joint"]["regime_prob"].items()}
    post = cm.c_control_context(b2, facts)["posterior"]
    b2["joint"]["regime_update"]["floor_share"] = 0.0
    loose = cm.c_control_context(b2, facts)["posterior"]
    pinned = [r for r in prior if loose[r] < share * prior[r]]
    assert pinned and all(post[r] == pytest.approx(share * prior[r], abs=1e-15) for r in pinned), (post, loose)


def _core_with_register(book: dict, facts: dict, register: list, v: str):
    """Пара прогонов «контроль, ядро» на книге с датой оценки v и реестром объявленных дивидендов."""
    import dataclasses
    book_mod = importlib.import_module("model.book")
    grid_mod = importlib.import_module("model.grid")
    f2 = cm.copy.deepcopy(facts)
    f2["dividends"]["register_seed"] = register
    b2 = cm.copy.deepcopy(book)
    b2["meta"]["valuation_date"] = b2["meta"]["date"] = v
    ctl = cm.c_control_run(b2, f2)
    core_facts = book_mod.load_facts()
    core_facts = dataclasses.replace(core_facts, files={**core_facts.files, "dividends": f2["dividends"]})
    return ctl, grid_mod.run_grid(book_mod.book_from_dict(b2, facts=core_facts), core_facts)


@pytest.mark.ci_only
def test_control_matches_core_bridge_before_ex_date(inputs):
    """Мост М§8.1: объявленный дивиденд последнего года истории в реестре, дата оценки — за пять дней
    до экс-даты. B(v) ≤ 1 %, цена ≤ 3 % (М§17). Если этот дивиденд выплачен до якоря (якорь после квартала
    выплаты), запись — дивиденд года прибыли якоря с отсечкой после квартала ГОСА (сценарий теста)."""
    book, facts = inputs
    hist = facts["dividends"]["history"][-1]
    rec = {"year": hist["year"], "dps": cm._c_nodeval(hist["dps_ordinary"]), "status": "declared",
           "record_date": hist["record_date"], "last_buy_date": hist["last_buy_date"],
           "ex_date": hist["ex_date"], "pay_date": hist["pay_date"], "sources": ["сверка М§17"]}
    if hist["ex_date"] <= str(book["meta"]["facts_date"]):
        y0, e_qa = _agm_end(book)
        rec = _declared(y0, _history_dps(facts), e_qa)
    v = (dt.date.fromisoformat(rec["ex_date"]) - dt.timedelta(days=5)).isoformat()
    try:
        ctl, run = _core_with_register(book, facts, [rec], v)
    except ImportError as exc:
        pytest.skip(f"ядро не готово ({exc})")
    assert ctl["bridge_amount"] > 0.0
    assert abs(ctl["bridge_amount"] / run.bridge_amount - 1.0) <= TOL_BRIDGE
    assert abs(ctl["point"] / run.point - 1.0) <= TOL_VALUE


@pytest.mark.ci_only
@pytest.mark.parametrize("offset", [-10, -1, 0, 1], ids=["E_qA-10", "E_qA-1", "E_qA", "E_qA+1"])
def test_control_matches_core_at_agm_quarter_end(inputs, offset):
    """A2, № 25 (М§0.2, §8.1–§8.2): «конец квартала — конец дня» и D_pend у обеих моделей. Запись реестра за
    год прибыли якоря, дата оценки — до, в день и после конца квартала ГОСА: B(v) и D_pend ≤ 1 %,
    точка ≤ 3 %."""
    book, facts = inputs
    y0, e_qa = _agm_end(book)
    v = (e_qa + dt.timedelta(days=offset)).isoformat()
    try:
        ctl, run = _core_with_register(book, facts, [_declared(y0, _history_dps(facts), e_qa)], v)
    except ImportError as exc:
        pytest.skip(f"ядро не готово ({exc})")
    for what, core_v, ctl_v in (("мост", run.bridge_amount, ctl["bridge_amount"]),
                                ("D_pend", run.pending_dividend, ctl["pending_amount"])):
        scale = max(abs(ctl_v), abs(core_v))
        assert abs(ctl_v - core_v) <= TOL_BRIDGE * scale + TOL_ZERO_BN, what
    assert (ctl["pending_amount"] > 0.0) == (offset < 0) and (ctl["bridge_amount"] > 0.0) == (offset >= 0)
    assert abs(ctl["point"] / run.point - 1.0) <= TOL_VALUE


REGISTER_DATES = {   # кварталы выплаты и отсечки записи реестра: (лет после года прибыли, квартал) — входы сверки
    "выплата-во-2К": {"pay": (1, 2), "reg": (1, 2)},
    "выплата-в-4К": {"pay": (1, 4), "reg": (1, 3)},
    "выплата-и-отсечка-через-год": {"pay": (2, 1), "reg": (2, 1)},
}


@pytest.mark.ci_only
@pytest.mark.parametrize("name", list(REGISTER_DATES))
def test_control_matches_core_on_register_dates(inputs, name):
    """М§5.4, §17: объявленный дивиденд года якоря с датами записи вне календаря книги — выплата и вычет из
    регуляторного капитала стоят у обеих моделей в кварталах записи: вся сверка М§17 в допусках, дивиденды
    к выплате и DPreg на концах годов совпадают (≤ 1 %)."""
    book, facts = inputs
    y0, _ = _agm_end(book)
    spec = REGISTER_DATES[name]
    rec = _dated_record(y0, _history_dps(facts), pay=(y0 + spec["pay"][0], spec["pay"][1]),
                        reg=(y0 + spec["reg"][0], spec["reg"][1]))
    try:
        ctl, run = _core_with_register(book, facts, [rec], str(book["meta"]["valuation_date"]))
    except ImportError as exc:
        pytest.skip(f"ядро не готово ({exc})")
    bad = [r for r in compare_with_core(run, ctl) if not r["ok"]]
    if bad:
        pytest.fail(f"вне допусков М§17: {len(bad)} строк\n" + _fail_lines(bad))
    qidx = _quarter_index(ctl)
    seen = 0.0
    for c in ctl["cells"]:
        q = run.cell(c["world"], c["regime"], c["scenario"]).quarters
        for p, r in zip(ctl["ctx"]["periods"], c["rows"]):
            for line in ("dp", "dpreg"):
                core_v = q[line][qidx[p["year"]][-1]]
                assert abs(r[line] - core_v) <= TOL_BRIDGE * max(abs(core_v), abs(r[line])) + TOL_ZERO_BN, (
                    line, p["year"], c["world"], c["regime"], c["scenario"], core_v, r[line])
                seen = max(seen, abs(r[line]))
    if spec["pay"][0] > 1:
        assert seen > 0.0, "выплата через год: на конце года ГОСА дивиденды к выплате обязаны быть ненулевыми"


@pytest.mark.ci_only
def test_control_matches_core_on_shifted_cor_level(inputs):
    """В14 (М§4.6, §17): пути CoR всех режимов сдвинуты на шаг строки чувствительности, опора FVC — путь
    нормы книги до подмены (у ядра — копия книги `with_overrides`, она помнит исходную; у контрольной
    модели — книга-опора). Вся сверка М§17 в допусках, и в клетке режима-опоры мира N строка FVC у обеих
    моделей — расход: сдвиг уровня дошёл до кредитов по СС."""
    book, facts = inputs
    try:
        book_mod = importlib.import_module("model.book")
        grid_mod = importlib.import_module("model.grid")
    except ImportError as exc:
        pytest.skip(f"ядро не готово ({exc})")
    step = cm.c_bnum(book, "valuation.sensitivities.cor_pp")
    b2 = cm.copy.deepcopy(book)
    for r in b2["regimes"]["ids"]:
        b2["regimes"][r]["cor"] = _shifted(b2["regimes"][r]["cor"], step)
    ctl = cm.c_control_run(b2, facts, base_book=book)
    core_facts = book_mod.load_facts()
    over = {f"regimes.{r}.cor": b2["regimes"][r]["cor"] for r in b2["regimes"]["ids"]}
    run = grid_mod.run_grid(book_mod.book_from_dict(book, facts=core_facts).with_overrides(over), core_facts)
    bad = [r for r in compare_with_core(run, ctl) if not r["ok"]]
    if bad:
        pytest.fail(f"вне допусков М§17: {len(bad)} строк" + chr(10) + _fail_lines(bad))
    if cm.c_bnum(book, "credit.fv_loans_factor") > 0.0 and step > 0.0:
        key = (cm.W_LOW, ctl["ctx"]["fv_ref"], list(cm.c_bget(ctl["ctx"]["B"], "capital.reg_scenarios.ids"))[0])
        mine = sum(r["fvc"] for r in cm.c_cell_of(ctl, *key)["rows"])
        theirs = sum(v for v in run.cell(*key).quarters["fvc"] if v is not None)
        assert mine > 0.0 and theirs > 0.0, (mine, theirs)


@pytest.mark.ci_only
def test_control_is_not_bit_identical_to_core(control):
    run, why = _core_grid()
    if run is None:
        pytest.skip(f"сверять не с чем: {why}")
    assert not exact_match(run, control), (
        "V0 всех слоёв совпали с ядром до 1e-9: контрольная модель и ядро, видимо, делят код (М§17)")


# ============================================================================ сводка для выпуска


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cm.ROOT, capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _strain(r: dict) -> float:
    """Доля допуска, выбранная строкой (для выбора худшей строки группы): по тому же правилу, что вердикт."""
    if r["diff"] is None or not r["tol"]:
        return math.inf
    if r["tol_kind"] == ABS:
        return abs(r["diff"]) / r["tol"]
    parts = []
    if r["diff_rel"] is not None:
        parts.append(abs(r["diff_rel"]) / r["tol"])
    if r.get("tol_abs"):
        parts.append(abs(r["diff"]) / r["tol_abs"])
    if not parts:
        return 0.0 if r["diff"] == 0.0 else math.inf
    return min(parts)


def _public(r: dict, what: str | None = None) -> dict:
    """Строка сводки для выпуска (П§2 checks.control_model.rows[]): единица и вид допуска сохранены."""
    out = {}
    for k in ROW_FIELDS:
        if k == "ok" and "tol_abs" in r:
            out["tol_abs"] = r["tol_abs"]
        out[k] = what if (k == "what" and what is not None) else r[k]
    return out


def summary_rows(rows: list[dict]) -> list[dict]:
    """Сжатие строк сверки для выпуска: передача, слои, цена и мост, стоимость клеток, худшая строка
    каждой группы строк клетки, строки, которых нет у ядра (не больше 10)."""
    keep = [_public(r) for r in rows if r["group"] in SUMMARY_WHOLE]
    worst: dict[str, dict] = {}
    for r in rows:
        if r["group"] in SUMMARY_WHOLE + ("missing",):
            continue
        if r["group"] not in worst or _strain(r) > _strain(worst[r["group"]]):
            worst[r["group"]] = r
    keep += [_public(r, f"худшая строка: {r['what']}") for _, r in sorted(worst.items())]
    keep += [_public(r) for r in rows if r["group"] == "missing"][:10]
    return keep


def checks_document(run, ctl, *, as_of: str, commit: str | None) -> dict:
    """Содержимое data/checks/control_model.json (INTERFACES §3.3)."""
    rows = compare_with_core(run, ctl)
    return {"as_of": as_of, "commit": commit,
            "book_version": str(cm.c_bget(ctl["ctx"]["B"], "meta.version")),
            "all_ok": all(r["ok"] for r in rows) and not exact_match(run, ctl),
            "n_rows": len(rows), "n_bad": sum(not r["ok"] for r in rows),
            "rows": summary_rows(rows)}


def write_checks(path: Path = CHECKS_JSON) -> dict:
    book, facts = cm.c_book_machine(), cm.c_facts_bundle()
    ctl = cm.c_control_run(book, facts)
    run, why = _core_grid()
    if run is None:
        raise SystemExit(f"сводку не записать: {why}")
    doc = checks_document(run, ctl, as_of=dt.date.today().isoformat(), commit=_git_commit())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(doc, ensure_ascii=False, indent=1) + chr(10)).encode("utf-8"))   # LF, как в репозитории
    return doc


def _stub_core(ctl: dict, k: float, d_ratio: float = 0.0):
    """Заглушка прогона ядра из результата контрольной модели: денежные величины × k, отношения + d_ratio.
    Ядро не нужно — так писатель сводки проверяется в такте на строках реальной формы."""
    tr = ctl["transmission"]
    trn = SimpleNamespace(sigma0=tr["sigma0"], sigma0_liab=tr["sigma0_liab"], phi=tr["phi"], t_real=tr["t_real"],
                          phi_assets=tr["phi_assets"], phi_liab=tr["phi_liab"], loan_margin=dict(tr["loan_margin"]),
                          t_local=dict(tr["t_local"]), pairs=dict(tr["pairs"]), roe_equiv=tr["roe_equiv"],
                          lt_spread={b: dict(v) for b, v in tr["lt_spread"].items()})
    qidx = _quarter_index(ctl)
    n_q = max(ids[-1] for ids in qidx.values())
    cells = {}
    for c in ctl["cells"]:
        quarters = {line: [None] + [0.0] * n_q for line in FLOW_LINES + ("div",)}
        quarters.update({line: [None] * (n_q + 1) for line in STOCK_LINES + RATIO_LINES})
        for p, r in zip(ctl["ctx"]["periods"], c["rows"]):
            j = qidx[p["year"]][-1]
            for line in FLOW_LINES + ("div",):
                quarters[line][j] = r[line] * k
            for line in STOCK_LINES:
                quarters[line][j] = r[line] * k
            for line in RATIO_LINES:
                quarters[line][j] = r[line] + d_ratio
        bv_q = c["rows"][-1]["bv"]
        cells[(c["world"], c["regime"], c["scenario"])] = SimpleNamespace(
            v_ri=c["v_ri"] * k, roe_t=c["roe_t"] + d_ratio, k_t=c["k_t"], g_t=c["g_t"], x_t=c["x_t"] * k,
            y_x=c["y_x"] * k, tv_ri=(bv_q + c["tv_ri"]) * k - bv_q * k, quarters=quarters,
            dps={yp: d["dps"] * k for yp, d in c["decisions"].items()})
    return SimpleNamespace(
        ctx=SimpleNamespace(transmission=trn),
        layers={n: SimpleNamespace(v0=ly["v0"] * k) for n, ly in ctl["layers"].items()},
        low=ctl["low"] * k, high=ctl["high"] * k, point=ctl["point"] * k,
        bridge_amount=ctl["bridge_amount"] * k, pending_dividend=ctl["pending_amount"] * k,
        cell=lambda w, r, s: cells[(w, r, s)])


def _assert_row_form(r: dict) -> None:
    """Строка сводки — формы П§2: поля, единица из словаря, вид допуска, вердикт выводится из записанного."""
    assert set(ROW_FIELDS) <= set(r) and set(r) <= set(ROW_FIELDS) | {"tol_abs"}, sorted(r)
    assert r["unit"] in (U_BN, U_RUB, U_PCT, U_PP, U_NUM) and r["tol_kind"] in (REL, ABS), r
    if r["core"] is not None and r["control"] is not None:
        assert r["diff"] == pytest.approx(r["control"] - r["core"], abs=1e-12)
        if r["core"] != 0.0:
            assert r["diff_rel"] == pytest.approx(r["control"] / r["core"] - 1.0, abs=1e-12)
        else:
            assert r["diff_rel"] is None
        both_undefined = False
    else:
        assert r["diff"] is None and r["diff_rel"] is None
        both_undefined = r["core"] is None and r["control"] is None and r["ok"]     # пара миров не определена
    if not both_undefined:
        assert r["ok"] == verdict(r["diff"], r["diff_rel"], r["tol"], r["tol_kind"], r.get("tol_abs")), r


@pytest.mark.tact
def test_row_keeps_the_kind_of_tolerance():
    """№ 15 (П§2 checks.control_model): строка несёт единицу, вид допуска и относительную разность;
    вердикт следует из записанного — относительная строка не судится по абсолютной разности."""
    r = _row("ЧПД", U_BN, 1000.0, 975.0, TOL_LINE, REL, group="nii")
    assert (r["diff"], r["diff_rel"], r["tol_kind"], r["ok"]) == (-25.0, pytest.approx(-0.025), REL, True)
    assert not _row("ЧПД", U_BN, 1000.0, 880.0, TOL_LINE, REL, group="nii")["ok"]
    floor = _row("ЧП акционерам", U_BN, 10.0, 14.0, TOL_LINE, REL, group="ni_sh", tol_abs=5.0)
    assert floor["ok"] and floor["tol_abs"] == 5.0 and floor["diff_rel"] == pytest.approx(0.4)
    assert not _row("ЧП акционерам", U_BN, 10.0, 16.0, TOL_LINE, REL, group="ni_sh", tol_abs=5.0)["ok"]
    x_t = _row("избыток капитала", U_BN, 131.4, 101.4, 584.5, ABS, group="x_t")       # |diff| > 1 при допуске в млрд ₽
    assert x_t["ok"] and x_t["diff"] == pytest.approx(-30.0) and "tol_abs" not in x_t
    ratio = _row("Н20.0", U_PCT, 0.1489, 0.1525, TOL_RATIO, ABS, group="n20")
    assert not ratio["ok"] and ratio["diff"] == pytest.approx(0.0036)
    zero = _row("переоценка", U_BN, 0.0, 0.0, TOL_LINE, REL, group="fvc")
    assert zero["ok"] and zero["diff_rel"] is None
    noise = _row("переоценка", U_BN, -3.6e-15, 0.0, TOL_LINE, REL, group="fvc")     # шум ядра — ноль
    assert noise["ok"] and noise["core"] == 0.0 and noise["diff_rel"] is None
    assert not _row("переоценка", U_BN, 0.0, 0.2, TOL_LINE, REL, group="fvc")["ok"]
    assert _bridge_row("мост", 0.0, 0.0)["tol_kind"] == ABS and _bridge_row("мост", 0.0, 0.0)["ok"]
    assert not _bridge_row("мост", 0.0, 1.0)["ok"]
    assert _bridge_row("мост", 850.0, 851.0)["tol_kind"] == REL and _bridge_row("мост", 850.0, 851.0)["ok"]
    gone = _missing("капитал", U_BN, 5.0)
    assert not gone["ok"] and gone["diff"] is None and gone["tol"] is None
    for row in (r, floor, x_t, ratio, zero, gone):
        _assert_row_form(_public(row))


@pytest.mark.tact
def test_summary_writer_on_rows_of_real_shape(control):
    """№ 15, № 41: сводка сверки на заглушке ядра (величины контрольной модели × 1,01) — все строки в
    допуске, формы П§2; относительные и абсолютные строки различены, есть абсолютная строка с |diff| > 1
    (избыток капитала, млрд ₽); пол допуска DPS — от BV конца года выплаты, свой у каждого года. При
    расхождении в 20 % строки выходят из допуска."""
    near = _stub_core(control, 1.0 / 1.01, d_ratio=0.001)
    rows = compare_with_core(near, control)
    assert rows and all(r["ok"] for r in rows), [r["what"] for r in rows if not r["ok"]][:10]
    doc = checks_document(near, control, as_of="2026-10-02", commit=None)
    assert doc["all_ok"] and doc["n_bad"] == 0 and doc["n_rows"] == len(rows)
    json.dumps(doc, ensure_ascii=False)
    for r in doc["rows"]:
        _assert_row_form(r)
    kinds = {r["tol_kind"] for r in doc["rows"]}
    assert kinds == {REL, ABS}
    assert any(r["tol_kind"] == ABS and r["unit"] == U_BN and abs(r["diff"]) > 1.0 for r in doc["rows"])
    assert any("tol_abs" in r for r in doc["rows"])
    rel = [r for r in rows if r["tol_kind"] == REL and r["diff_rel"] is not None and r["group"] in ("value", "cell")]
    assert rel and all(r["diff_rel"] == pytest.approx(0.01, rel=1e-9) for r in rel)
    groups = {r["group"] for r in rows}
    assert {"fvc", "misc", "y_x", "dps", "div", "x_t", "bridge", "derived", "lt_spread"} <= groups
    assert len([r for r in doc["rows"] if r["what"].startswith("худшая строка: ")]) == len(
        groups - set(SUMMARY_WHOLE) - {"missing"})
    # пол допуска DPS — BV конца года выплаты (тот же год, что у строки Div), а не последнего года
    c = cm.c_cell_of(control, "M", "norm", "upper")
    n_out = control["ctx"]["A"]["N_out"]
    floors = {}
    for yp in c["decisions"]:
        row = c["rows"][control["years"].index(yp + 1)]
        floors[yp] = dps_floor(control, c, yp)
        assert floors[yp] == pytest.approx(DIV_FLOOR_BV * abs(row["bv"]) * 1000.0 / n_out, rel=1e-12)
    assert len(set(floors.values())) == len(floors)
    by_what = {r["what"]: r for r in rows}
    for yp, floor in floors.items():
        assert by_what[f"DPS за {yp}, клетка M/norm/upper"]["tol_abs"] == floor
        div = by_what[f"{LINE_TITLES['div']} {yp + 1}, клетка M/norm/upper"]
        assert div["tol_abs"] * 1000.0 / n_out == pytest.approx(floor, rel=1e-12)
    far = compare_with_core(_stub_core(control, 1.0 / 1.2), control)
    bad = [r for r in far if not r["ok"]]
    assert bad and not checks_document(_stub_core(control, 1.0 / 1.2), control, as_of="2026-10-02", commit=None)["all_ok"]
    assert not exact_match(near, control) and exact_match(_stub_core(control, 1.0), control)


@pytest.mark.tact
def test_checks_file_has_the_payload_form():
    """Файл data/checks/control_model.json (ядро передаёт его в выпуск как есть): строки формы П§2,
    вердикты следуют из записанных чисел, счётчики сходятся."""
    if not CHECKS_JSON.exists():
        pytest.skip("сводки сверки ещё нет (её пишет интеграция: python -B -m tests.test_control_model --write)")
    doc = json.loads(CHECKS_JSON.read_text(encoding="utf-8"))
    assert {"as_of", "commit", "rows"} <= set(doc)
    assert doc["rows"], "в сводке нет строк"
    for r in doc["rows"]:
        _assert_row_form(r)
    assert doc["n_rows"] >= len(doc["rows"]) and doc["n_bad"] >= sum(not r["ok"] for r in doc["rows"])
    if doc["all_ok"]:
        assert doc["n_bad"] == 0


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сверка контрольной модели с ядром (М§17)")
    ap.add_argument("--write", action="store_true", help="записать data/checks/control_model.json")
    a = ap.parse_args(argv)
    if a.write:
        doc = write_checks()
        print(f"записано {CHECKS_JSON.relative_to(cm.ROOT)}: строк {doc['n_rows']}, вне допуска {doc['n_bad']}")
        return 0 if doc["all_ok"] else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
