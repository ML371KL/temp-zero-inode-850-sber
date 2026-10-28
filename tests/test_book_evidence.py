# -*- coding: utf-8 -*-
"""Доказательные листы книги воспроизводимы: скрипт области в копии листа (tmp_path) даёт те же выходы, что лежат в
evidence/<область>/out/, и те же числа, что в книге (коды выхода 0 — сверка с шаблоном без расхождений). Архивные
тесты (маркер archive, нужна папка передачи) сверяют малые входы с листами этапа 1 и выходы — с выходами этапа 1.
Метка `tact` — у сверок без запуска листов (прогон листов ≈12 с идёт в CI и полном наборе): числа книги в выходах,
правило центров `nii.sigma0_split` и `nii.phi_split`, история спредов и полы, неподвижная точка дисконта за управление
против результатов книги, отсутствие ответов брокерского API (файлом, таблицей или выпиской его полей) во входах
и выходах листов: дивиденды для β — лист решений годовых собраний с публичным источником каждого года."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

try:
    from tests import support_book as S
except ImportError:                      # прогон без pytest.ini (pythonpath = .) и без tests/__init__.py
    import support_book as S

AREAS = {                                 # область → скрипты по порядку (быстрые, без сети)
    "dividends": ("dps_history.py",),
    "regulation": ("floors.py",),
    "bridges": ("bridges.py",),
    "governance": ("governance_channels.py",),
    "regimes": ("regimes_calc.py",),
    "capital": ("capital_calib.py", "gap_additive.py", "capital_book.py"),
    "beta": ("beta_sber.py",),
    "nii": ("calib_nii.py", "nii_book.py", "near_spreads.py", "sigma0_split.py", "phi_split.py", "spread_history.py"),
}
RESULTS = S.BOOK_DIR / "results.json"
# Признаки сырого ответа T-Invest (конверт запроса, заголовки, поля Quotation): во входах листов их быть не должно.
# Строки собраны из частей: сам тест — не «сырой ответ» для общего теста гигиены репозитория
T_RAW_MARKERS = ("x-rate" + "limit", "headers_" + "kept", "instrument" + "Id", "dividend" + "Net",
                 "recommendation" + "Date", "target" + "Price", '"na' + 'no"')
# Табличная выписка ответа (запись аудита № 70): CSV, у которого источник строк — метод API брокера либо есть
# колонки-поля ответа с префиксом `t_`. Выписок ответа нет и в JSON (решение ведущего P1): ни имён полей ответа, ни
# имён методов API — нигде в области листа (входы, выходы, тексты, код листа)
T_ANSWER_WORDS = tuple(a + b for a, b in (("dividend", "Net"), ("record", "Date"), ("lastBuy", "Date"), ("payment", "Date"),
                                          ("declared", "Date"), ("Get", "Dividends"), ("Instruments", "Service"),
                                          ("tinvest_", "samples")))
DIVIDENDS_SHEET = "stage1/market/shares_dividends/dividends_public.csv"
T_TABLE_SOURCES = ("Get" + "Dividends", "T-" + "Invest", "tin" + "vest")
T_TABLE_COLUMN = re.compile(r"^t_[a-z_]+$")
NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _close_json(a, b, tol=1e-9) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close_json(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close_json(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def _close_text(a: str, b: str) -> bool:
    """Тексты равны; иначе — равны вне чисел, а числа расходятся не больше единицы последнего напечатанного знака
    (последний бит libm на другой платформе может сдвинуть округление печати)."""
    if a == b:
        return True
    if NUM.sub("#", a) != NUM.sub("#", b):
        return False
    for x, y in zip(NUM.findall(a), NUM.findall(b)):
        if x != y:
            dec = len(x.split(".")[1]) if "." in x and "e" not in x.lower() else 0
            if abs(float(x) - float(y)) > 10 ** -dec + 1e-12:
                return False
    return True


def same_output(new: Path, old: Path) -> bool:
    a, b = new.read_bytes().replace(b"\r\n", b"\n"), old.read_bytes().replace(b"\r\n", b"\n")
    if a == b:
        return True
    if new.suffix == ".json":
        return _close_json(json.loads(a), json.loads(b))
    return _close_text(a.decode("utf-8"), b.decode("utf-8"))


@pytest.mark.parametrize("area", list(AREAS))
def test_evidence_area_reproduces_its_outputs_and_the_book(area, tmp_path):
    work = S.copy_area(tmp_path, area)
    for script in AREAS[area]:
        done = S.run_script(work / script)
        assert done.returncode == 0, f"{area}/{script}:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    committed = S.EVIDENCE / area / "out"
    names = sorted(p.name for p in committed.iterdir() if p.is_file())
    assert names, f"нет выходов листа {area}"
    assert sorted(p.name for p in (work / "out").iterdir() if p.is_file()) == names
    bad = [n for n in names if not same_output(work / "out" / n, committed / n)]
    assert bad == [], f"{area}: выходы не воспроизводятся: {bad}"


@pytest.mark.tact
def test_evidence_outputs_carry_the_book_numbers():
    """Выходы листов, лежащие в репозитории, согласованы с книгой (без запуска)."""
    book = S.template()
    div = json.loads((S.EVIDENCE / "dividends" / "out" / "dps_history_out.json").read_text(encoding="utf-8"))
    assert [(r["year"], r["dps_ceil"], r["dps_declared"]) for r in div["history"]] == \
        [(2023, "33.30", "33.30"), (2024, "34.84", "34.84"), (2025, "37.64", "37.64")]
    assert div["history"][1]["dps_ceil_if_tau_25"] != "34.84" and div["history"][0]["dps_half_up"] == "33.29"
    assert div["anchor"]["outstanding_total"] == "22342.5" and div["anchor"]["dividends_payable"] == "841.0"
    cap = json.loads((S.EVIDENCE / "capital" / "out" / "capital_book_out.json").read_text(encoding="utf-8"))
    assert cap["book_mismatches"] == []
    # книга на якоре позже листа (перезаякоривание, evlib.moved): Ded20_0 = BV − CET1 по фактам якоря книги,
    # плотности — плотности листа × общий множитель к RWA якоря; иначе — числа листа
    ded20 = cap["book_anchor"]["ded20_anchor"] if "book_anchor" in cap else cap["ded20_anchor"]
    assert ded20 == book["capital"]["n20"]["deductions_anchor"]
    br = json.loads((S.EVIDENCE / "bridges" / "out" / "bridges_out.json").read_text(encoding="utf-8"))
    assert br["cor_bridge"]["additive_mean_pp"] == 0.17 and br["nim_bridge"]["mgmt_minus_engine_pp"] == 0.412
    assert br["pnl"]["misc"]["2025"] == -246.5 and br["book_mismatches"] == []
    assert br["misc_profile"]["shares"] == book["other"]["misc_quarter_shares"]          # A9: профиль «прочего»
    assert br["misc_profile"]["sum_by_quarter"] == {"1": -82.1, "2": 8.2, "3": -62.8, "4": -271.2}
    beta = json.loads((S.EVIDENCE / "beta" / "out" / "beta_summary.json").read_text(encoding="utf-8"))
    assert beta["beta_E_center_book"] == book["valuation"]["beta_e"]
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["valuation.beta_e"])
    assert ax["low"] <= beta["ci95"][0] and beta["windows"]["2 года"]["beta"] <= ax["high"] and beta["beta_5y_ex_crash"] <= ax["high"]
    reg = json.loads((S.EVIDENCE / "regimes" / "out" / "regimes_out.json").read_text(encoding="utf-8"))
    assert reg["classes"]["counts"] == {"soft": 5, "norm": 5, "downturn": 5, "crisis": 3}
    assert reg["book"]["cor_lt_mgmt"] == 0.01535 and abs(reg["book"]["nim_shift_lt"]) <= 0.0005
    gov = json.loads((S.EVIDENCE / "governance" / "out" / "governance_out.json").read_text(encoding="utf-8"))
    assert gov["total"] == book["valuation"]["governance"]["discount"] == 0.004 and gov["book_mismatches"] == []
    qf = gov["channels"]["quasi_fiscal"]                                                  # В5: доля V0, не капитала якоря;
    fp = qf["fixed_point"]                                                                # В16: ключ — точка к шагу
    assert qf["share_of_bv0"] == 0.00377 and qf["pv_bn"] == 31.6 and fp["half_step"] == fp["step"] / 2
    assert qf["value"] == pytest.approx(round(fp["share_of_v0"] / fp["step"]) * fp["step"], abs=1e-12)
    assert abs(qf["value"] - fp["share_of_v0"]) <= fp["half_step"] + 1e-12
    nii = json.loads((S.EVIDENCE / "nii" / "out" / "nii_book_out.json").read_text(encoding="utf-8"))
    assert nii["book_mismatches"] == [] and abs(nii["a_n3"]["phi_for_realized"] - 0.484) < 0.001
    assert nii["a_n2"]["line_at_key_H_mgmt_pct"] == 5.784 and nii["a_n2"]["nss_H_sigma0_0_mgmt_pct"] == 7.25
    assert nii["liquid_share"]["min"] == 0.207 and nii["liquid_share"]["anchor"] == 0.2141   # № 23: без акций и фондов
    assert nii["liquid_share"]["with_equities"] == {"min": 0.2116, "min_at": "2024Q2", "anchor": 0.2176}
    assert book["volumes"]["liquid_min_share"] == int(nii["liquid_share"]["min"] * 100) / 100 == 0.20
    near = json.loads((S.EVIDENCE / "nii" / "out" / "near_spreads_out.json").read_text(encoding="utf-8"))
    assert near["book_mismatches"] == [] and near["anchor_year"] == int(book["meta"]["first_period"][:4])
    for b, v in near["books"].items():                                                    # A1 F3: ближние ключи
        # перенос якоря пишет ключи с шестью знаками от неокруглённой опоры квартала: до единицы пятого знака
        assert {k: book["nii"]["books"][b]["spread"][k] for k in v["rule"]} == pytest.approx(v["rule"], abs=1e-5), b
    assert [near["books"][b]["rule"][str(near["anchor_year"])] for b in ("mortgage", "retail_loans", "securities")] \
        == [0.00672, 0.00672, -0.02803]                     # якорь 3К26 (книга 1.4; на 2К26 — 0,00541 и −0,02795)
    fl = json.loads((S.EVIDENCE / "regulation" / "out" / "floors_out.json").read_text(encoding="utf-8"))
    assert fl["mismatches"] == [] and fl["book"]["strict"]["2031"]["floor20"] == 0.14


def _out(area: str, name: str) -> dict:
    return json.loads((S.EVIDENCE / area / "out" / name).read_text(encoding="utf-8"))


@pytest.mark.tact
def test_the_sigma0_split_is_the_rule_of_the_sheet():
    """`nii.sigma0_split` — шаг 1 правила центров (MODEL §4.5; лист `evidence/nii/sigma0_split.py`, без запуска):
    наибольшая доля, при которой гейт `lt_spread_floor` проходит в мире отсчёта (сжатия φ там нет), вниз до 0,01; при
    долях книги гейт не нарушен ни в одном мире; ось полосы — в области гейта: от нуля до центра (выше центра пол
    корпоративной книги нарушен — такую долю полоса не разыгрывает); доля при нулевом спреде — справка листа."""
    book = S.template()
    out = _out("nii", "sigma0_split_out.json")
    rule, floors = out["rule"], out["floors"]
    assert out["book_mismatches"] == [] and floors == book["checks"]["lt_spread_floor"]
    assert rule["reference_world"] == book["nii"]["transmission"]["reference_world"]
    assert rule["center"] == rule["book"] == book["nii"]["sigma0_split"]
    assert 0 <= rule["max_split_exact"] - rule["center"] < 0.01
    assert rule["phi_split_book"] == book["nii"]["phi_split"]
    assert rule["worlds_violating_at_book"] == [] and rule["mass_at_book"] == 0
    at_book = out["table"]["book"]
    assert all(v == [] for v in at_book["violations"].values())
    for b, row in at_book["lt_spread"].items():                                          # ни одна книга не ниже пола
        assert all(v >= floors[b] for v in row.values()), b
    binding = at_book["lt_spread"][rule["binding_book"]][rule["reference_world"]]        # связывающая книга — у пола
    assert 0 <= binding - floors[rule["binding_book"]] < 0.0002
    step_up = out["table"]["split_1"]                                                    # всё на кредитах — гейт нарушен
    assert all(step_up["violations"][w] for w in book["worlds"]["ids"]) and step_up["sigma0_liab"] == 0.0
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.sigma0_split"])
    assert [ax["low"], ax["high"]] == [out["axis"]["low"], out["axis"]["high"]] == [0.0, rule["center"]]
    assert ax["high"] <= rule["max_split_exact"], "верх оси — не выше наибольшей доли, при которой гейт проходит"
    assert out["zero_spread_split"] == out["center_by_corporate_floor"]["0.0000"] > ax["high"]
    # справка листа на якоре 3К26 (книга 1.4; на 2К26 при поле 0,0025 — 0,23)
    assert out["center_by_corporate_floor"] == {"0.0000": 0.36, "0.0025": 0.22, "0.0050": 0.09, "0.0075": None}
    liab = out["stationary"]["liabilities_lt_shift"]
    assert liab == [b for b, v in book["nii"]["books"].items() if v["side"] == "liability" and v["lt_shift"]]


@pytest.mark.tact
def test_the_phi_split_is_the_rule_of_the_sheet():
    """`nii.phi_split` — шаг 2 правила центров (MODEL §4.5; лист `evidence/nii/phi_split.py`, без запуска): наибольшая
    доля, при которой при `sigma0_split` книги гейт проходит во всех мирах и кредитная маржа стационара неотрицательна,
    вниз до 0,01. Раскладка не меняет объём сжатия (стационарный ЧПМ миров тот же); при всём сжатии на кредитных
    ставках гейт нарушен и маржа мира высоких ставок отрицательна; совместный выбор долей даёт ту же точку. Ось
    полосы — в области гейта: от нуля до наибольшей доли, при которой при `sigma0_split` книги гейт проходит во всех
    мирах (вниз до 0,001); на углах прямоугольника осей σ0 и φ гейт проходит. Доля по наклонам доходностей кредитных
    книг в истории — вне области гейта: осью не разыгрывается, лист называет её отдельно."""
    book = S.template()
    out = _out("nii", "phi_split_out.json")
    rule, st, worlds = out["rule"], out["stationary"], book["worlds"]["ids"]
    assert out["book_mismatches"] == [] and out["floors"] == book["checks"]["lt_spread_floor"]
    assert rule["sigma0_split_book"] == book["nii"]["sigma0_split"]
    assert rule["center"] == rule["book"] == book["nii"]["phi_split"]
    assert 0 <= rule["max_phi_split_exact"] - rule["center"] < 0.01
    assert rule["max_phi_split_exact"] == min(rule["max_phi_split_by_world"].values())
    assert st["volume_residual_max"] < 1e-12 and st["t_real"] == pytest.approx(st["t_star"], abs=1e-9)
    books = book["nii"]["books"]
    assert st["assets_phi"] == [b for b, v in books.items() if v["side"] == "asset" and v["phi"]]
    assert st["liabilities_phi"] == [b for b, v in books.items() if v["side"] == "liability" and v["phi"]]
    assert st["phi_assets"] == pytest.approx(rule["book"] * st["phi"], abs=1e-12)
    assert st["phi_liab"] == pytest.approx((1 - rule["book"]) * st["phi"] * st["weight_assets_phi"]
                                           / st["weight_liabilities_phi"], abs=1e-12)
    at_book, on_loans = out["table"]["book"], out["table"]["all_on_loans"]
    assert all(v == [] for v in at_book["violations"].values())
    assert min(at_book["loan_margin_pp"].values()) > 0                                   # кредит окупается в каждом мире
    market = book["joint"]["macro_neutral_world"]
    assert on_loans["violations"][market] and on_loans["loan_margin_pp"][market] < 0      # раскладка книги 1.2
    assert on_loans["phi_liab"] == 0.0 and on_loans["phi_assets"] == pytest.approx(st["phi"], abs=1e-12)
    for w in worlds:                                                                     # добавка пассивов = остаток сжатия
        assert at_book["liability_addon_pp"][w] == pytest.approx(100 * st["phi_liab"] * st["x_pp"][w] / 100, abs=2e-3)
    joint = out["joint_region"]
    # ближайшая к (1; 1) точка сетки 0,01 — центр правила на шаге; у книги, перенесённой на новый якорь (keep), доля σ0
    # стоит не на шаге, а в пределах шага ниже наибольшей доли (лист sigma0_split.py)
    step = _out("nii", "sigma0_split_out.json")["rule"]["center_by_step"]
    assert joint["nearest_l1"] == joint["nearest_l2"] == [step, book["nii"]["phi_split"]]
    assert 0 <= step - book["nii"]["sigma0_split"] < 0.01
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.phi_split"])
    assert [ax["low"], ax["high"]] == [out["axis"]["low"], out["axis"]["high"]]
    assert ax["low"] == 0.0 and 0 <= rule["max_phi_split_exact"] - ax["high"] < 0.001     # верх оси — у предела гейта
    ax_s = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.sigma0_split"])
    corners = [[a, b] for a in (ax_s["low"], ax_s["high"]) for b in (ax["low"], ax["high"])]
    assert out["axis"]["corners_in_gate_region"] == corners                               # гейт проверен на всех углах
    on_high = out["table"]["axis_high"]
    assert all(v == [] for v in on_high["violations"].values()) and min(on_high["loan_margin_pp"].values()) > 0
    hist, share = out["history"], out["history_share"]
    assert 0 <= hist["share"] - share["value"] < 0.01 and hist["share_2se"][0] < hist["share"] < hist["share_2se"][1]
    assert share["value"] > ax["high"] and share["gate_violations"][market], "доля по истории — вне области гейта"
    assert out["table"]["history_share"]["phi_split"] == share["value"]
    funds, past = out["funds_cost_at_book"], out["funds_cost_history"]
    assert funds[market]["to_key"] > past["max_to_key"] > 0.5        # названное ограничение: пассивы дороже истории


@pytest.mark.tact
def test_the_spread_history_confirms_the_floors():
    """Полы гейта `lt_spread_floor` подтверждены листом истории (решение ведущего В20; `evidence/nii/spread_history.py`,
    без запуска): пол книги не выше минимума спреда при ровной опоре и ниже медианы; у льготной ипотеки истории нет
    (спред — компенсация государства). Оговорка листа названа числом: при высоких ставках спред корпоративных кредитов
    с поправкой на лаг ниже пола."""
    book = S.template()
    out = _out("nii", "spread_history_out.json")
    floors, books = book["checks"]["lt_spread_floor"], book["nii"]["books"]
    assert out["book_mismatches"] == [] and out["floors"] == floors
    with_history = [b for b, v in books.items() if "sector" in v and v["phi"]]
    assert sorted(out["verdict"]) == sorted(with_history) and set(floors) - set(with_history) == {"mortgage_sub"}
    for b, v in out["verdict"].items():
        flat = out["history"][b]["flat_ref"]
        assert v["confirmed"] and v["floor_pp"] == pytest.approx(100 * floors[b])
        assert v["floor_pp"] <= flat["min_pp"] < flat["median_pp"] <= flat["max_pp"] and flat["n"] >= 4, b
        assert out["history"][b]["quarters"] >= 24 and out["history"][b]["from"].startswith("2019"), b
    corp = out["verdict"]["corp_loans"]
    assert (corp["flat_ref_min_pp"], corp["flat_ref_median_pp"]) == (0.76, 1.21)
    assert corp["calibration_below_flat_min"] and corp["floor_above_lag_adjusted_median_at_high_rates"]
    rows = (S.EVIDENCE / "nii" / "out" / "spread_history.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + sum(h["quarters"] for h in out["history"].values())


@pytest.mark.tact
def test_the_sheets_and_the_core_solve_the_same_transmission():
    """Листы считают σ0_A, σ0_L, φ, его раскладку, эффективные LT-спреды и кредитную маржу независимо от ядра (факты и
    запись миров); результаты книги той же версии (`results.json → derived.nii`) обязаны совпасть. Результаты прежней
    версии книги (всё сжатие на кредитных ставках) сверяются с таблицей листа для этой раскладки."""
    book = S.template()
    sigma, phi = _out("nii", "sigma0_split_out.json"), _out("nii", "phi_split_out.json")
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    d = res["derived"]["nii"]
    same = res["book_version"] == book["meta"]["version"]
    t = phi["table"]["book" if same else "all_on_loans"]
    assert d["sigma0"] == pytest.approx(sigma["table"]["book"]["sigma0_assets"], abs=1e-9)
    assert d["sigma0_liab"] == pytest.approx(sigma["table"]["book"]["sigma0_liab"], abs=1e-9)
    assert d["phi"] == pytest.approx(t["phi"], abs=1e-9)
    for b, row in t["lt_spread"].items():
        for w, v in row.items():
            assert d["lt_spread"][b][w] == pytest.approx(v, abs=1e-6), (b, w)
    if same:
        assert d["phi_assets"] == pytest.approx(t["phi_assets"], abs=1e-9)
        assert d["phi_liab"] == pytest.approx(t["phi_liab"], abs=1e-9)
        for w, v in t["loan_margin_pp"].items():
            assert 100 * d["loan_margin"][w] == pytest.approx(v, abs=1e-3), w
    assert d["target_eng"] == pytest.approx(sigma["stationary"]["target_eng"], abs=1e-12)


@pytest.mark.tact
def test_the_governance_discount_is_the_fixed_point_of_the_printed_median():
    """Дисконт за управление — доля V0 печатаемой медианы (MODEL §8.2; решения В5 и В16): ключ — неподвижная точка
    g = PV / (PV + медиана × N_out / 1000), округлённая к шагу листа; от точки на медиане результатов книги он отстоит
    не больше чем на половину шага (0,025 п.п. ≈ 0,1 ₽ цены). Красный тест после нового прогона ядра — пересчитать лист
    `evidence/governance` (вход `fixed_point.median_rub`) и ключ книги."""
    book = S.template()
    gov = _out("governance", "governance_out.json")
    fp = gov["channels"]["quasi_fiscal"]["fixed_point"]
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    shares = json.loads((S.FACTS_DIR / "shares.json").read_text(encoding="utf-8"))["outstanding_total"]["v"]
    pv = fp["share_of_v0"] / (1 - fp["share_of_v0"]) * fp["median_rub"] * shares / 1000   # PV канала, млрд ₽
    assert pv == pytest.approx(gov["channels"]["quasi_fiscal"]["pv_bn"], abs=0.1)
    cap = res["headline"]["median"] * shares / 1000
    key = book["valuation"]["governance"]["discount"]
    assert abs(key - pv / (pv + cap)) <= fp["half_step"] + 1e-12
    assert key == pytest.approx(round(key / fp["step"]) * fp["step"], abs=1e-12)          # ключ стоит на шаге


@pytest.mark.tact
def test_inputs_hold_no_raw_api_responses():
    """Ответы брокерского API в репозиторий не кладутся (условия сервиса; записи аудита № 7 и № 70, решение P1) —
    ни файлом ответа, ни таблицей, ни выпиской его полей: признаков сырого ответа нет ни в одном входе, CSV-входы не
    несут колонок-полей ответа и не называют метод API источником строк, а область листа β не называет ни полей
    ответа, ни методов API нигде — ни во входах, ни в выходах, ни в тексте, ни в коде листа."""
    for p in S.EVIDENCE.rglob("inputs/**/*"):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        assert not [m for m in T_RAW_MARKERS if m in text], p
        if p.suffix.lower() == ".csv":
            head = text.splitlines()[0].lstrip("\ufeff").split(",") if text else []
            assert not [c for c in head if T_TABLE_COLUMN.match(c.strip())], p
            assert not [m for m in T_TABLE_SOURCES if m.lower() in text.lower()], p
    assert not (S.EVIDENCE / "beta" / "inputs" / "stage1" / "market" / ("tinvest_" + "samples")).exists()
    assert not (S.EVIDENCE / "beta" / "inputs" / "dividends_extract.json").exists(), "выписка ответа убрана (P1)"
    listed = json.loads((S.EVIDENCE / "dividends" / "inputs" / "SOURCES.json").read_text(encoding="utf-8"))["files"]
    assert sorted(Path(f["path"]).name for f in listed) == ["iss_description_SBER.json", "iss_description_SBERP.json",
                                                            "pool_check.csv"]
    for p in sorted((S.EVIDENCE / "beta").rglob("*")) + [S.EVIDENCE / "extract_inputs.py", S.EVIDENCE / "README.md"]:
        if p.is_file() and p.suffix != ".pyc":
            text = p.read_text(encoding="utf-8", errors="replace")
            assert not [w for w in T_ANSWER_WORDS if w in text], f"{p}: имя поля или метода ответа брокерского API"


@pytest.mark.tact
def test_the_beta_sheet_reads_the_public_decisions_of_the_shareholders_meetings():
    """Лист β берёт дивиденды из нейтрального листа решений: год прибыли, дивиденд на акцию каждой категории, дата
    отсечки — с публичным источником каждой строки (страница эмитента и новость о решении с датой и адресом). Лист
    — тот же, из которого сборщик строит факты: годы, размер и отсечка совпадают с `data/facts/dividends.json`."""
    path = S.EVIDENCE / "beta" / "inputs" / DIVIDENDS_SHEET
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert {"fiscal_year", "dps_ordinary", "dps_preferred", "record_date", "decided_date", "issuer_page",
            "news_outlet", "news_published", "news_title", "news_url"} <= set(rows[0])
    for r in rows:
        assert r["issuer_page"].startswith("https://") and r["news_url"].startswith("https://"), r["fiscal_year"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["decided_date"]) and r["news_outlet"] and r["news_title"], r["fiscal_year"]
        assert bool(r["record_date"]) == (float(r["dps_ordinary"]) > 0), r["fiscal_year"]
    listed = json.loads((S.EVIDENCE / "beta" / "inputs" / "SOURCES.json").read_text(encoding="utf-8"))["files"]
    entry = next(f for f in listed if f["path"] == DIVIDENDS_SHEET)
    assert entry["sha256_full"] == entry["sha256_input"] == S.sha256_bytes(path.read_bytes())
    facts = json.loads((S.ROOT / "data" / "facts" / "dividends.json").read_text(encoding="utf-8"))
    by_year = {r["year"]: r for r in facts["history_before"] + facts["history"]}
    assert sorted(by_year) == sorted(int(r["fiscal_year"]) for r in rows)
    for r in rows:
        f = by_year[int(r["fiscal_year"])]
        assert f["dps_ordinary"]["v"] == float(r["dps_ordinary"]) and f["dps_preferred"]["v"] == float(r["dps_preferred"])
        assert (f["record_date"] or "") == r["record_date"] and f["decided_date"] == r["decided_date"], r["fiscal_year"]
    anchor = json.loads((S.ROOT / "data" / "facts" / "anchor.json").read_text(encoding="utf-8"))
    doc = next(d for d in anchor["documents"] if d["key"] == "dividends_public")
    assert doc["sha256"] == entry["sha256_full"], "лист β и факты читают один и тот же лист решений"
    summary = json.loads((S.EVIDENCE / "beta" / "out" / "beta_summary.json").read_text(encoding="utf-8"))
    assert "решения годовых собраний" in summary["inputs"]["dividends"]


def test_a_table_of_api_fields_is_caught(tmp_path):
    """Правило № 70 на заглушке: CSV с колонками `t_*` или с методом API в источнике строк — табличная выписка ответа."""
    good = "fiscal_year,dps_declared,source\n2025,37.64,\"отчётность МСФО за 2025 год, с. 6\"\n"
    with_columns = "ticker,record_date," + "t_" + "last_buy_date," + "t_" + "close_price\nX,2030-01-10,2030-01-09,100\n"
    with_source = "ticker,record_date,source\nX,2030-01-10,\"" + T_TABLE_SOURCES[0] + ", снимок\"\n"   # синтетика

    def flagged(text: str) -> bool:
        head = text.splitlines()[0].split(",")
        return bool([c for c in head if T_TABLE_COLUMN.match(c.strip())]
                    or [m for m in T_TABLE_SOURCES if m.lower() in text.lower()])

    assert not flagged(good) and flagged(with_columns) and flagged(with_source)


@pytest.mark.tact
def test_inputs_carry_no_absolute_paths_and_sources_are_listed():
    user = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/home/[a-z_][a-z0-9_-]*/", re.I)
    for area in AREAS:
        inp = S.EVIDENCE / area / "inputs"
        for p in inp.rglob("*"):
            if p.is_file():
                assert not user.search(p.read_text(encoding="utf-8", errors="replace")), p
        if area != "governance":
            src = json.loads((inp / "SOURCES.json").read_text(encoding="utf-8"))
            for f in src["files"]:
                assert S.sha256_bytes((inp / f["path"]).read_bytes()) == f["sha256_input"], f["path"]


def _handoff_moved(root: str) -> str | None:
    """Якорь калибровки книг ЧПД в папке передачи, если он позже якоря листов обоснований книги (перезаякоривание:
    листы этапа 1 продлены новым кварталом, листы `evidence/` — снимок книги 1.0 на `evlib.SHEETS_ANCHOR`); иначе None."""
    spec = importlib.util.spec_from_file_location("evlib_for_test", S.EVIDENCE / "evlib.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    prop = Path(root, "stage1", "calib", "nii", "data", "nii_books_anchor_proposal.json")
    period = json.loads(prop.read_text(encoding="utf-8")).get("period") if prop.exists() else None
    return period if period and period > ev.SHEETS_ANCHOR else None


@pytest.mark.archive
def test_small_inputs_are_the_stage1_sheets_filtered():
    if not os.environ.get("BANK_HANDOFF_DIR"):
        pytest.skip("нет BANK_HANDOFF_DIR")
    moved = _handoff_moved(os.environ["BANK_HANDOFF_DIR"])
    if moved:
        pytest.skip(f"листы этапа 1 продлены якорем {moved}, листы обоснований книги — снимок на якоре прежней книги: "
                    "малые входы сверяются после пересчёта листов на новом якоре")
    done = S.run_script(S.EVIDENCE / "extract_inputs.py", "--check")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.archive
@pytest.mark.parametrize("area,pairs", [
    ("nii", [("out/results.json", "stage1/calib/nii/data/results.json"),
             ("out/history_fit.csv", "stage1/calib/nii/data/history_fit.csv"),
             ("out/nii_book_proposal.yaml", "stage1/calib/nii/data/nii_book_proposal.yaml")]),
    ("capital", [("out/gap_n20_quarterly.csv", "stage1/calib/capital/data/gap_n20_quarterly.csv"),
                 ("out/n11_bridge_quarterly.csv", "stage1/calib/capital/data/n11_bridge_quarterly.csv"),
                 ("out/out.txt", "stage1/calib/capital/out.txt")]),
    ("beta", [("out/beta_summary.json", "stage1/market/beta/beta_summary.json"),
              ("out/beta_out.txt", "stage1/market/beta/beta_out.txt")]),
])
def test_moved_sheets_give_the_stage1_numbers(area, pairs):
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root:
        pytest.skip("нет BANK_HANDOFF_DIR")
    moved = _handoff_moved(root) if area == "nii" else None
    if moved:
        pytest.skip(f"калибровка книг ЧПД этапа 1 пересчитана на якорь {moved}, лист книги — на якоре прежней книги: "
                    "сверка — после пересчёта листа на новом якоре")
    for mine, theirs in pairs:
        a, b = S.EVIDENCE / area / mine, Path(root, theirs)
        if mine.endswith("results.json"):                       # переезд меняет только происхождение входов и
            ja, jb = (json.loads(p.read_text(encoding="utf-8")) for p in (a, b))   # определение ликвидной доли (№ 23)
            for k in ("inputs_sha256", "script", "liquid_share"):
                ja.pop(k), jb.pop(k)
            assert ja == jb
        elif mine.endswith("nii_book_proposal.yaml"):           # то же: строка `liquid_min_share` — без акций и фондов
            ta, tb = (["" if "liquid_min_share" in ln else ln for ln in p.read_text(encoding="utf-8").splitlines()]
                      for p in (a, b))
            assert ta == tb
        elif mine.endswith("beta_summary.json"):                # дивиденды — лист решений вместо ответа брокера (P1)
            ja, jb = (json.loads(p.read_text(encoding="utf-8")) for p in (a, b))
            ja.pop("inputs"), jb.pop("inputs")
            assert _close_json(ja, jb)
        elif mine.endswith("beta_out.txt"):                     # то же: без подписи источника и сверки с брокером
            ta = a.read_text(encoding="utf-8").splitlines()
            tb = [re.sub(r" \(T [A-Za-z]+ [0-9-]+\)", "", ln) for ln in b.read_text(encoding="utf-8").splitlines()]
            caption = [i for i, ln in enumerate(ta) if ln.startswith("Цены: MOEX ISS")]
            assert len(caption) == 1 and tb[caption[0]].startswith("Цены: MOEX ISS")
            ta[caption[0]] = tb[caption[0]] = ""
            assert _close_text("\n".join(ta), "\n".join(tb))
        else:
            assert same_output(a, b), mine
