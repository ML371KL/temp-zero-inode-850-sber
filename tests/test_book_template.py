# -*- coding: utf-8 -*-
"""Книга: шаблон разбирается, сборка воспроизводит машинную книгу, все пути прил. A MODEL есть и не null,
вероятности, оси полосы и обратного расчёта согласованы со значениями книги, решения ведущего и правила книг 1.1–1.3
(ближние спреды от ставок якоря, сход ближнего сдвига ЧПМ, доли «прочего», конец реального роста расходов; все оси в
полосе и условия оси вне полосы, год шока от якоря, опора FVC, распределение σ0 и полы спреда; раскладка сжатия φ,
пол вероятности режима, единицы осей) — числами книги (поток book). Быстрые — метка `tact` (такт сервера); тесты
`docs` такт не гоняет. Суждения, которые проверяются прогоном сетки ядра, — `tests/test_book_core.py`."""
from __future__ import annotations

import json
import re

import pytest
import yaml

try:
    from tests import support_book as S
except ImportError:                      # прогон без pytest.ini (pythonpath = .) и без tests/__init__.py
    import support_book as S

pytestmark = pytest.mark.tact

MARKS = ("  #{{WORLDS}}", "#{{WORLDS_BANK}}", "#{{OVERLAY_SHA256}}")
VERSION = "1.4"
# Книга 1.4 перенесена на якорь 2026Q3 (ops/tools/reanchor.py, режим keep, MODEL §15.2): решения, которые перенос
# записывает на структуре нового якоря и в его ценах, — числами журнала V1.4-CHANGES.md (суждения те же): цели A-N2
# и A-N3 и доля σ0 — при прежних скалярах пути (цель A-N2 — на мосте ЧПМ канона фактов), вычеты — формулой книги на
# фактах 30.09.2026, реальные суммы — × индекс цен 3К26. В книгах 1.0–1.3 (якорь 2К26): 0,060; 0,06; 0,09; 936,7;
# 1 173,8; −200.
MOVED = {"nii.nim_lt_target_mgmt": 0.057238, "nii.transmission.target": 0.047867, "nii.sigma0_split": 0.089584,
         "capital.n20.deductions_anchor": 963.7, "capital.n11.deductions_anchor": 1192.927,
         "other.misc_net_real.LT": -203.195111}
RESULTS = S.BOOK_DIR / "results.json"
# Оси, которые из полосы не выводятся никогда (MODEL §10): односторонние рисковые — по путям ключей
NEVER_OFF_BAND = ("tax.statutory", "tax.one_off.prob", "regimes.crisis.one_off_loss.amount")


@pytest.fixture(scope="module")
def book() -> dict:
    return S.built_book()


def test_the_template_parses_and_carries_one_of_each_build_mark():
    text = (S.BOOK_DIR / "assumptions_template.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["meta"]["version"] == VERSION
    lines = text.splitlines()
    assert sum(1 for ln in lines if ln == "  #{{WORLDS}}") == 1
    assert sum(1 for ln in lines if ln == "#{{WORLDS_BANK}}") == 1
    assert sum(1 for ln in lines if "#{{OVERLAY_SHA256}}" in ln) == 1
    assert set(data["worlds"]) == {"source", "build", "quarter_rule", "ids", "overlay"}   # миры вписывает сборка
    assert "worlds_bank" not in data


def test_the_build_reproduces_the_committed_machine_book():
    y, j, _ = S.build_module().render()
    assert (S.BOOK_DIR / "assumptions.yaml").read_text(encoding="utf-8") == y
    assert (S.BOOK_DIR / "assumptions.json").read_text(encoding="utf-8") == j


def test_the_build_is_deterministic_and_json_equals_yaml():
    m = S.build_module()
    y1, j1, b1 = m.render()
    y2, j2, b2 = m.render()
    assert (y1, j1) == (y2, j2)
    assert json.loads(j1) == yaml.safe_load(y1) == b1
    assert not any(ln.rstrip() in MARKS for ln in y1.splitlines())


def test_the_build_check_mode_exits_zero():
    done = S.run_script(S.BOOK_DIR / "build_assumptions.py", "--check")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.docs
def test_every_app_a_path_is_in_the_book_and_not_null(book):
    paths = S.app_a_paths(book)
    assert len(paths) > 300
    off = S.switched_off(book)
    missing = [p for p in paths if "*" not in p and not S.has(book, p)]
    nulls = [p for p in paths if "*" not in p and S.has(book, p) and S.get(book, p) is None and p not in off]
    assert missing == [] and nulls == []
    for p in (p for p in paths if "*" in p):                      # словарь по годам — не пуст
        assert S.get(book, p.split(".*")[0])


@pytest.mark.docs
def test_every_leaf_of_the_book_is_named_by_app_a(book):
    paths = S.app_a_paths(book)
    exact = [p for p in paths if "*" not in p]
    star = [p.split("*")[0] for p in paths if "*" in p]
    unnamed = [path for path, _ in S.leaves(book)
               if not any(path.split("[")[0] == p or path.split("[")[0].startswith(p + ".") for p in exact)
               and not any(path.startswith(s) for s in star)]
    assert unnamed == []


def test_nulls_only_under_switched_off_keys(book):
    off = S.switched_off(book)
    nulls = {p for p, v in S.leaves(book) if v is None}
    assert nulls <= off, sorted(nulls - off)
    assert off == nulls                                              # у книги 1.0 все три выключателя выключены
    for sw in S.SWITCHES:
        assert S.get(book, sw) is False
    assert book["capital"]["recapitalization"]["enabled"] is False
    assert book["dividends"]["buyback"]["enabled"] is False


def test_probability_tables_sum_to_one(book):
    j = book["joint"]
    tables = [j["world_prob"], j["world_prob_market_implied"], j["regime_prob"], *j["reg_prob_given_regime"].values()]
    for axis in book["valuation"]["uncertainty"]["axes"]:
        if axis["kind"] == "dict":
            tables += [axis["low"], axis["high"]]
    for t in tables:
        assert abs(sum(t.values()) - 1) < 1e-12, t
    assert set(j["regime_prob"]) == set(book["regimes"]["ids"])
    assert set(j["world_prob"]) == set(book["worlds"]["ids"])


def test_governance_discount_is_the_sum_of_channels(book):
    g = book["valuation"]["governance"]
    assert abs(g["discount"] - sum(c["sign"] * c["value"] for c in g["components"])) < 1e-12
    assert {c["id"] for c in g["components"]} == {"quasi_fiscal", "withdrawals", "liquidity_listing"}


def test_uncertainty_axes_hold_the_book_value_inside_their_range(book):
    unc = book["valuation"]["uncertainty"]
    for a in unc["axes"] + unc["off_band_axes"]:
        assert a["dist"] == "triangular" and a["kind"] in ("value", "shift", "dict")
        for p in a["paths"]:
            assert S.has(book, p) and S.get(book, p) is not None, (a["name"], p)
        if a["kind"] == "value":
            for p in a["paths"]:
                assert a["low"] <= S.get(book, p) <= a["high"], (a["name"], p)
        elif a["kind"] == "shift":
            assert a["low"] <= 0 <= a["high"], a["name"]


def test_reverse_dcf_axes_search_covers_range(book):
    for a in book["valuation"]["reverse_dcf"]["axes"]:
        assert a["kind"] in ("value", "shift", "mix")
        for p in a["paths"]:
            assert S.has(book, p), (a["name"], p)
        assert a["search"][0] <= a["range"][0] <= a["range"][1] <= a["search"][1], a["name"]
    names = {a["name"] for a in book["valuation"]["reverse_dcf"]["axes"]}
    assert any("Выплата" in n for n in names)                         # ось «выплата» (решение ведущего, MODEL §11.1)


def test_issuer_trajectories_end_on_lt_levels_by_the_last_year(book):
    last_year = int(book["meta"]["last_period"][:4])
    for path, v in S.leaves({k: book[k] for k in book if k not in ("worlds", "worlds_bank")}):
        if path.endswith(".LT_from"):
            assert int(v) <= last_year, path
        m = re.search(r"\.(\d{4})(Q\d|H\d)$", path)
        if m:
            assert int(m.group(1)) != last_year, path                 # ключ квартала/полугодия в году last_period запрещён


def test_meta_company_and_prices(book):
    c = book["meta"]["company"]
    assert c["main_ticker"] in c["tickers"]
    assert set(book["meta"]["market_price"]) == set(c["tickers"])
    for k in ("date", "facts_date", "valuation_date", "curve_as_of"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", book["meta"][k])
    assert book["meta"]["valuation_date"] >= book["meta"]["date"]


def test_lead_decisions_are_in_the_book(book):
    """Решения ведущего к книге 1.0 (LEAD-DECISIONS §1–§2), не изменённые книгой 1.1, — числами книги."""
    assert book["regimes"]["cor_basis"] == "mgmt"
    assert book["nii"]["nim_lt_target_mgmt"] == MOVED["nii.nim_lt_target_mgmt"]           # 0,060 на якоре 2К26
    assert book["nii"]["transmission"]["target"] == MOVED["nii.transmission.target"]       # 0,06 на якоре 2К26
    assert book["valuation"]["beta_e"] == 0.83
    assert book["joint"]["regime_prob"] == {"soft": 0.15, "norm": 0.40, "downturn": 0.30, "crisis": 0.15}
    assert book["credit"]["kappa"] == 0.05 and book["credit"]["real_rate_lag_q"] == 4
    assert book["regimes"]["crisis"]["one_off_loss"] == {"period": "2027Q1", "amount": -400.0}
    assert {k: book["dividends"]["excess"][k] for k in ("from_profit_year", "epsilon")} == {"from_profit_year": 2030, "epsilon": 0.5}
    assert book["capital"]["n20"]["deductions_anchor"] == MOVED["capital.n20.deductions_anchor"]
    assert book["capital"]["n11"]["deductions_anchor"] == MOVED["capital.n11.deductions_anchor"]
    assert book["capital"]["n11"]["audit_cutoffs"] == [3, 4]
    assert book["capital"]["mgmt_buffer"]["n20_0"] == 0.013
    assert book["fees"]["growth_override"] == {"2026": 0.0}
    assert book["volumes"]["guidance_growth"] == {"corporate": 0.09, "retail": 0.10}
    assert book["opex"]["real_growth"]["2026"] == 0.05
    assert book["other"]["misc_net_real"]["LT"] == MOVED["other.misc_net_real.LT"]         # −200 в ценах 2К26
    assert book["valuation"]["headline"]["print_step"] == 5
    assert book["valuation"]["headline"]["jump_guard"] == {"median_pct": 0.08, "v0_pct": 0.08}
    assert book["valuation"]["headline"]["ceiling_x_market"] == 3.0
    assert len(book["nii"]["books"]) == 10
    mort, retail = book["nii"]["books"]["mortgage"], book["nii"]["books"]["retail_loans"]
    assert {k: mort[k] for k in ("ref", "rho", "spread")} == {k: retail[k] for k in ("ref", "rho", "spread")}


def test_lead_decisions_2_are_in_the_book(book):
    """Решения ведущего по итогам этапа 2 (LEAD-DECISIONS-2, книга 1.1), не изменённые книгой 1.2, — числами книги
    (множитель FVC из A3, значение дисконта из A10 и отбор осей A11 пересмотрены решениями В4, В5, В1 — тест ниже)."""
    y0 = int(book["meta"]["first_period"][:4])
    assert book["nii"]["sigma0_from"] == y0 + 1                                    # A1 F1: σ0 со второго года
    near = book["regimes"]["near_nim_shift"]                                        # A1 F2: ближний сдвиг — общий
    assert near["LT"] == 0.0 and "LT_from" not in near
    # книги 1.0–1.3: −0,005 в году якоря; с 1.4 — ближняя калибровка переноса с первого прогнозного квартала (М§15.2)
    assert near[str(y0)] == -0.005 if str(y0) in near else book["meta"]["first_period"] in near
    assert all(book["regimes"][r]["nim_shift"][str(y0)] == 0.0 for r in book["regimes"]["ids"])
    assert book["dividends"]["excess"]["ramp_years"] == 3                           # A4: ввод ε за 3 года
    og = book["opex"]["real_growth"]                                                # A8: +0,5 п.п. до 2030, с 2031 — 0
    assert S.year_value(og, 2029) == S.year_value(og, 2030) == 0.005
    assert og["LT"] == 0.0 and og["LT_from"] == 2031 and S.year_value(og, 2031) == 0.0
    obs = book["joint"]["regime_update"]["observables"]                             # A10: σ A-P2u подтверждены
    assert (obs["cor"]["sigma_pp"], obs["nim"]["sigma_pp"]) == (0.0035, 0.002)
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["valuation.governance.discount"])
    assert (ax["low"], ax["high"]) == (0.0, 0.10)                                   # A10: ось дисконта — как в книге
    ch = book["checks"]                                                             # A1, A6: новые гейты и флаг
    assert ch["nim_path_joint"] == {"quarters": 8, "tolerance": 0.003}
    assert ch["transmission_pairs"] == [-0.05, 0.25]
    assert ch["ni_jump"] == {"quarters": 8, "yoy": 0.25, "key_tol": 0.01}
    assert book["meta"]["company"]["share_classes"] == {"SBER": "ordinary", "SBERP": "preferred"}


def test_lead_decisions_3_are_in_the_book(book):
    """Решения ведущего по аудиту после волны W1 (LEAD-DECISIONS-3, книга 1.2), не изменённые книгой 1.3, — числами
    книги (значение дисконта за управление из В5 пересмотрено решением В16 — тест ниже)."""
    unc = book["valuation"]["uncertainty"]                                          # В1: все оси — в полосе
    assert unc["off_band_axes"] == [] and len(unc["axes"]) >= 33
    assert sum(1 for a in unc["axes"] if a["kind"] == "dict") == 2
    ru = book["joint"]["regime_update"]                                             # В2: окно, стационарные режимы
    assert ru["window_obs"] == 4 and ru["max_shift_pp"] == 0.05
    # окно A-P2u: факты закрытых кварталов, перенесённые с замороженными ожиданиями режимов (с 1.4 — 3К26 с подменой
    # CoR решением владельца 28.10.2026, DECISIONS 13.1: 1,3 % ± 0,12 п.п. вместо опубликованных 1,1 %)
    assert [o["period"] for o in ru["observations"]] == ["2026Q3"]
    assert all("mu_cor" in o and "mu_nim" in o for o in ru["observations"])
    obs = ru["observations"][0]
    assert (obs["cor"], obs["se_cor"], obs["nim"], obs["se_nim"]) == (0.013, 0.0012, 0.0665, 0.0)
    crisis = book["regimes"]["crisis"]
    assert crisis["shock_year_offset"] == 1 and "shock_year" not in crisis
    assert book["other"]["misc_net_real"] == {"LT": MOVED["other.misc_net_real.LT"]}  # В3: правило, а не ключ года якоря
    assert book["credit"]["fv_loans_factor"] == 1.0                                 # В4: FVC режима, опора — норма
    assert book["credit"]["fv_loans_ref"] == "norm" and "norm" in book["regimes"]["ids"]
    nii = book["nii"]                                                               # В8: распределение σ0 и полы
    assert nii["sigma0_split"] == MOVED["nii.sigma0_split"]
    shifted = [b for b, v in nii["books"].items() if v["side"] == "liability" and v["lt_shift"]]
    assert shifted == ["retail_current", "retail_term", "corp_funds"]
    assert book["checks"]["lt_spread_floor"] == {"corp_loans": 0.005, "mortgage": 0.015, "mortgage_sub": 0.015,
                                                 "retail_loans": 0.015}
    assert book["volumes"]["liquid_min_share"] == 0.20                              # запись аудита № 23
    # № 20; с 1.4 — сетка вокруг ожидания модели на 4К26 (ручной шаг перезаякоривания, docs/REANCHOR.md §6)
    assert book["valuation"]["next_report"]["nim_values"] == [0.060, 0.063, 0.066, 0.069, 0.072]


def test_lead_decisions_4_are_in_the_book(book):
    """Решения ведущего по аудиту после волны W2 (LEAD-DECISIONS-4, книга 1.3) — числами книги."""
    nii = book["nii"]                                                               # В12: раскладка сжатия φ
    assert nii["phi_split"] == 0.0 and nii["sigma0_split"] == MOVED["nii.sigma0_split"]
    carriers = [b for b, v in nii["books"].items() if v["side"] == "liability" and v["phi"]]
    assert carriers == ["retail_current", "retail_term", "corp_funds"]
    assert book["valuation"]["terminal"]["fade"] == 1.0                              # В13: ось угасания — только вниз
    fade = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["valuation.terminal.fade"])
    assert (fade["low"], fade["high"]) == (0.5, 1.0)
    assert book["credit"]["fv_loans_ref"] == "norm" and book["credit"]["fv_loans_factor"] == 1.0   # В14: опора — норма
    ru = book["joint"]["regime_update"]                                             # В15: пол вероятности режима
    assert ru["floor_share"] == 0.33 and ru["window_obs"] == 4
    g = book["valuation"]["governance"]                                             # В16: дисконт 0,4 % V0
    assert g["discount"] == 0.004 and g["components"][0]["value"] == 0.004
    assert book["checks"]["lt_spread_floor"]["corp_loans"] == 0.005                 # В20: пол подтверждён листом
    assert book["valuation"]["sensitivities"]["rank_window"] == [0.40, 0.60]        # записи аудита № 81, № 80
    assert book["valuation"]["next_report"]["value_tol"] == 0.0001
    assert len(book["valuation"]["uncertainty"]["axes"]) == 34


def test_pre_push_decisions_are_in_the_book(book):
    """Решения ведущего перед первой выкладкой (P4) — числами книги: два шага уточнения корня обратного расчёта на
    полной полосе (невязка строки — в пределах стопа поиска); оси долей σ0 и φ лежат в области, где гейт
    `lt_spread_floor` проходит во всех мирах: ось σ0 — от нуля до значения книги (выше пол нарушен), ось φ — от
    значения книги до предела гейта при доле σ0 книги (листы `evidence/nii` сверяют концы с правилом и проверяют
    гейт на углах прямоугольника осей). Доли по истории и по нулевому полу осями не разыгрываются."""
    unc = book["valuation"]["uncertainty"]
    assert unc["median_refine"] == 2
    assert book["valuation"]["headline"]["search_tol_rub"] == 0.5
    axes = {a["paths"][0]: a for a in unc["axes"] if len(a["paths"]) == 1}
    s0, phi = axes["nii.sigma0_split"], axes["nii.phi_split"]
    assert (s0["low"], s0["high"]) == (0.0, book["nii"]["sigma0_split"]) == (0.0, MOVED["nii.sigma0_split"])
    assert (phi["low"], phi["high"]) == (book["nii"]["phi_split"], 0.001) and phi["high"] < 0.0012
    assert s0["unit"] == phi["unit"] == "pct" and unc["off_band_axes"] == []
    assert len(unc["axes"]) == 34


def test_crisis_keys_lie_in_the_shock_year(book):
    """Год шока = год якоря + `shock_year_offset`; квартальный профиль CoR кризиса, период разового убытка и первый
    год `loan_growth_override` лежат в году шока (MODEL §3.2: иначе ядро отказывает `BookError`) — перезаякоривание
    сдвигает их вместе с якорем."""
    crisis = book["regimes"]["crisis"]
    off = crisis["shock_year_offset"]
    assert isinstance(off, int) and not isinstance(off, bool) and off >= 1
    shock = int(book["meta"]["anchor_period"][:4]) + off
    assert crisis["one_off_loss"]["period"][:4] == str(shock)
    quarters = sorted(k for k in crisis["cor"] if "Q" in str(k))
    assert quarters == [f"{shock}Q{k}" for k in (1, 2, 3, 4)]
    assert min(int(y) for y in crisis["loan_growth_override"]) == shock


def test_the_sigma0_split_axis_and_the_liability_shift_are_consistent(book):
    """`nii.sigma0_split` ∈ [0; 1] и лежит внутри своей оси; при split < 1 есть пассив с `lt_shift`, при split > 0 —
    актив; у обеих книг средств ФЛ ключ одинаков (сдвиг σ0_L тогда не входит в передачу, MODEL §4.5); балансирующие
    книги и льготная ипотека сдвига не несут; полы спреда — у кредитных книг (с `sector`) и только у них."""
    nii = book["nii"]
    split, books = nii["sigma0_split"], nii["books"]
    assert 0.0 <= split <= 1.0
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.sigma0_split"])
    assert ax["kind"] == "value" and 0.0 <= ax["low"] <= split <= ax["high"] <= 1.0
    shifted = {side: [b for b, v in books.items() if v["side"] == side and v["lt_shift"]] for side in ("asset", "liability")}
    assert (split == 0.0 or shifted["asset"]) and (split == 1.0 or shifted["liability"])
    assert books["retail_current"]["lt_shift"] is books["retail_term"]["lt_shift"]
    assert not any(v["lt_shift"] for v in books.values() if v.get("balancing"))
    assert books["mortgage_sub"]["lt_shift"] is False
    floors = book["checks"]["lt_spread_floor"]
    assert set(floors) == {b for b, v in books.items() if "sector" in v}
    assert all(isinstance(v, float) and 0.0 < v < 0.05 for v in floors.values())
    corp = [floors[b] for b, v in books.items() if v.get("sector") == "corporate"]
    assert max(corp) <= min(v for b, v in floors.items() if books[b]["sector"] != "corporate")


def test_the_phi_split_axis_and_the_phi_flags_are_consistent(book):
    """`nii.phi_split` ∈ [0; 1] и лежит внутри своей оси; при доле < 1 есть пассив с `phi`, при доле > 0 — актив; у
    обеих книг средств ФЛ флаг одинаков (объём сжатия тогда не зависит от раскладки, MODEL §4.5); флаг есть у каждой
    книги; балансирующие книги и льготная ипотека сжатия не несут; сжатие и сдвиг σ0 несут одни и те же книги."""
    nii = book["nii"]
    p, books = nii["phi_split"], nii["books"]
    assert 0.0 <= p <= 1.0
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.phi_split"])
    assert ax["kind"] == "value" and 0.0 <= ax["low"] <= p <= ax["high"] <= 1.0
    assert all(isinstance(v.get("phi"), bool) for v in books.values())
    carriers = {side: [b for b, v in books.items() if v["side"] == side and v["phi"]] for side in ("asset", "liability")}
    assert (p == 0.0 or carriers["asset"]) and (p == 1.0 or carriers["liability"])
    assert books["retail_current"]["phi"] is books["retail_term"]["phi"]
    assert not any(v["phi"] for v in books.values() if v.get("balancing"))
    assert books["mortgage_sub"]["phi"] is False
    assert {b for b, v in books.items() if v["phi"]} == {b for b, v in books.items() if v["lt_shift"]}


# Единица оси для печати (MODEL §10, PAYLOAD §0.2): код и правило по ПУТЯМ оси, не по словам имени
AXIS_UNITS = {"pct", "pp", "number", "years", "bn"}
YEARS_PATHS = ("oci.fvoci_duration", "oci.fvoci_maturity")
PP_MARKS = ("_pp", "nim_shift", "mgmt_buffer")


def test_scalar_axes_carry_the_unit_of_their_paths(book):
    """У каждой скалярной оси — поле `unit` из кодов выпуска; у оси-словаря его нет. Сдвиг доли (вид `shift`, пути с
    `_pp`, сдвиг ЧПМ режима, управленческий буфер) — `pp`; срок — `years`; суммы в млрд ₽ — `bn`; коэффициент больше
    единицы процентом не печатается. Без поля единицу пришлось бы угадывать по величине значения (запись аудита № 99)."""
    for a in book["valuation"]["uncertainty"]["axes"]:
        if a["kind"] == "dict":
            assert "unit" not in a, a["name"]
            continue
        unit = a.get("unit")
        assert unit in AXIS_UNITS, (a["name"], unit)
        paths = a["paths"]
        if a["kind"] == "shift" or any(m in p for p in paths for m in PP_MARKS):
            assert unit == "pp", a["name"]
        if any(p in YEARS_PATHS for p in paths):
            assert unit == "years", a["name"]
        else:
            assert unit != "years", a["name"]
        big = max(abs(a["low"]), abs(a["high"]))
        if big >= 10:
            assert unit == "bn", a["name"]                                           # суммы — млрд ₽
        elif big > 1:
            assert unit in ("number", "years"), a["name"]                            # коэффициент или срок, не доля
        if unit == "bn":
            assert big >= 10, a["name"]


def test_the_regime_floor_is_a_share_of_the_base_probability(book):
    """Пол вероятности режима (MODEL §12): доля от 0 до 1; пол каждого режима ниже его базовой вероятности, сумма
    полов равна доле — правилу всегда есть что перераспределить. У книги 1.3 — 0,33: хвостовые режимы (мягкая
    посадка, кризис) не ниже 4,95 %."""
    ru = book["joint"]["regime_update"]
    share, prior = ru["floor_share"], book["joint"]["regime_prob"]
    assert isinstance(share, float) and 0.0 < share < 1.0
    floors = {r: share * p for r, p in prior.items()}
    assert abs(sum(floors.values()) - share) < 1e-12
    assert floors["soft"] == pytest.approx(0.0495) and floors["crisis"] == pytest.approx(0.0495)
    assert all(floors[r] < prior[r] for r in prior)


def off_band_report(book: dict, rows: list, point: float) -> dict:
    """Три условия оси вне полосы (MODEL §10) по строкам цены ошибки суждения: (1) ожидаемая доля размах² / Σ размах²
    меньше 1 %; (2) |сдвиг положения оси| < print_step / 10; (3) |Σ сдвигов по осям вне полосы| ≤ print_step / 10.
    Сдвиг положения — (цена_high + цена_low − 2 × точка) / 6. Возвращает {имя оси или «Σ»: [что нарушено]}."""
    limit = book["valuation"]["headline"]["print_step"] / 10
    by_name = {r["name"]: r for r in rows}
    total = sum(r["swing"] ** 2 for r in rows if r.get("swing") is not None)
    bad, shifts = {}, 0.0
    for a in book["valuation"]["uncertainty"]["off_band_axes"]:
        r = by_name.get(a["name"])
        if r is None:
            bad[a["name"]] = ["нет строки цены ошибки суждения — нужен прогон ядра на этой книге"]
            continue
        shift = (r["price_high"] + r["price_low"] - 2 * point) / 6
        shifts += shift
        why = []
        if not r["swing"] ** 2 / total < 0.01:
            why.append("доля размаха не меньше 1 %")
        if not abs(shift) < limit:
            why.append(f"сдвиг положения {shift:+.2f} ₽")
        if a["kind"] == "dict" or any(p.startswith(NEVER_OFF_BAND) for p in a["paths"]):
            why.append("ось-словарь или односторонняя рисковая ось")
        if why:
            bad[a["name"]] = why
    if abs(shifts) > limit:
        bad["Σ"] = [f"сумма сдвигов положения {shifts:+.2f} ₽ больше {limit} ₽"]
    return bad


def test_off_band_axes_meet_the_three_conditions(book):
    """Ось вне полосы допустима только при трёх условиях MODEL §10 (у книги 1.2 список пуст — все оси в полосе).
    Проверка — по `results.json` (строки `judgements` и точка); с непустым списком результаты обязаны быть этой книги."""
    off = book["valuation"]["uncertainty"]["off_band_axes"]
    names = {a["name"] for a in book["valuation"]["uncertainty"]["axes"]}
    assert not [a["name"] for a in off if a["name"] in names]
    if not off:
        return
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert res["book_version"] == book["meta"]["version"], "оси вне полосы проверяются по результатам этой книги"
    assert off_band_report(book, res["judgements"], res["headline"]["point"]) == {}


def test_the_off_band_rule_catches_an_asymmetric_and_a_wide_axis(book):
    """Правило на заглушке: узкая симметричная ось проходит; асимметричная (сдвиг положения не меньше print_step / 10),
    широкая (доля не меньше 1 %), налоговая и сумма сдвигов — нет. Отбор книги 1.1 (19 осей «вклад < 1 %») правило
    не прошёл бы: одна налоговая ось сдвигала положение на несколько рублей."""
    step = book["valuation"]["headline"]["print_step"]
    point = 400.0

    def row(name, lo, hi):
        return {"name": name, "price_low": lo, "price_high": hi, "swing": abs(hi - lo)}

    def variant(axes):
        b = {"valuation": {"headline": {"print_step": step}, "uncertainty": {"off_band_axes": axes}}}
        return off_band_report(b, rows, point)

    rows = [row("главная", 300.0, 520.0), row("узкая", 399.0, 401.0), row("асимметричная", 400.0, 400.0 - step),
            row("широкая", 370.0, 430.0), row("налог", 400.0, 399.5), row("ещё узкая", 399.6, 400.2),
            row("вниз 1", 400.0, 398.2), row("вниз 2", 400.0, 398.4)]
    ax = {n: {"name": n, "kind": "value", "paths": ["x.y"]}
          for n in ("узкая", "асимметричная", "широкая", "ещё узкая", "вниз 1", "вниз 2")}
    ax["налог"] = {"name": "налог", "kind": "shift", "paths": ["tax.statutory"]}
    assert variant([ax["узкая"], ax["ещё узкая"]]) == {}
    assert list(variant([ax["асимметричная"]])) == ["асимметричная", "Σ"]
    assert list(variant([ax["широкая"]])) == ["широкая"]
    assert list(variant([ax["налог"]])) == ["налог"]
    assert list(variant([ax["вниз 1"], ax["вниз 2"]])) == ["Σ"]                     # каждая мала, сумма — нет
    assert "нет строки" in variant([{"name": "новая", "kind": "value", "paths": ["x.y"]}])["новая"][0]


def test_the_near_nim_shift_fades_linearly_over_the_next_year(book):
    """Ближний сдвиг ЧПМ (MODEL §3.2): год якоря — одно значение, следующий год — линейный сход к нулю по кварталам
    (без обрыва на границе года), ключ года = среднее кварталов (MODEL §0.4), дальше — LT 0. Книга, перенесённая на
    новый якорь, несёт ближнюю калибровку переноса (MODEL §15.2 п. 6; docs/REANCHOR.md §3.6): кварталы с первого
    прогнозного, затем ключи лет; у года с кварталами ключ года — среднее кварталов; кварталы — только в годах,
    идущих подряд от первого; LT 0."""
    near = book["regimes"]["near_nim_shift"]
    first = book["meta"]["first_period"]
    y0 = int(first[:4])
    if str(y0) not in near:                                   # ближняя калибровка переноса якоря
        quarters = sorted(k for k in near if "Q" in k)
        years = sorted({int(k[:4]) for k in quarters})
        assert quarters[0] == first and years == list(range(y0, years[-1] + 1))
        for y in years[1:]:
            qs = [near[f"{y}Q{k}"] for k in (1, 2, 3, 4)]
            assert near[str(y)] == pytest.approx(sum(qs) / 4, abs=1e-12), y
        assert near["LT"] == 0.0 and "LT_from" not in near
        return
    v0 = near[str(y0)]
    quarters = [near[f"{y0 + 1}Q{k}"] for k in (1, 2, 3, 4)]
    assert quarters == pytest.approx([v0 * (1 - k / 4) for k in (1, 2, 3, 4)], abs=1e-12)
    assert near[str(y0 + 1)] == pytest.approx(sum(quarters) / 4, abs=1e-12)
    assert S.year_value({k: v for k, v in near.items() if "Q" not in k}, y0 + 2) == 0.0
    assert not any(k.startswith((f"{y}Q", f"{y}H")) for k in near for y in range(y0 + 2, y0 + 20))


def test_value_axes_on_year_keys_do_not_meet_quarter_keys(book):
    """Ось `value` на ключе года траектории с квартальными ключами этого года — отказ ядра (MODEL §0.4)."""
    unc = book["valuation"]["uncertainty"]
    for a in unc["axes"] + unc["off_band_axes"]:
        for p in a["paths"]:
            head, _, key = p.rpartition(".")
            if a["kind"] == "value" and key.isdigit() and S.has(book, head) and isinstance(S.get(book, head), dict):
                assert not any(str(k).startswith(key + "Q") for k in S.get(book, head)), (a["name"], p)


def test_near_spread_keys_follow_the_anchor_rates(book):
    """Ближние ключи спредов (MODEL §4.4, решение A1 F3): ключ года якоря = ставка якоря − опора мира H первого
    прогнозного квартала (у пассивов β × опора); следующий год — середина до калибровки года якоря + 2. Ставки якоря —
    факты `data/facts/nii_books.json`, опора — машинная книга (запись миров / 100)."""
    facts = json.loads((S.FACTS_DIR / "nii_books.json").read_text(encoding="utf-8"))["books"]
    first = book["meta"]["first_period"]
    y0 = int(first[:4])
    half = f"{y0}H{1 if int(first[5]) <= 2 else 2}"
    ref_world = book["worlds"][book["nii"]["transmission"]["reference_world"]]
    field = {"key": "key_rate"}
    checked = []
    for b, spec in book["nii"]["books"].items():
        sp = spec["spread"]
        if not isinstance(sp, dict) or str(y0) not in sp:
            continue
        ref = ref_world[field.get(spec["ref"], spec["ref"])][half]
        beta = spec["beta"] if spec["side"] == "liability" else 1.0
        s0 = facts[b]["rate_anchor"]["v"] - beta * ref
        assert sp[str(y0)] == pytest.approx(s0, abs=5e-6 + 1e-12), b
        mid = (sp[str(y0)] + S.year_value(sp, y0 + 2)) / 2
        assert sp[str(y0 + 1)] == pytest.approx(mid, abs=5e-6 + 1e-12), b
        checked.append(b)
    assert {"mortgage", "retail_loans", "securities"} <= set(checked)                # книги решения A1 F3
    got = [round(book["nii"]["books"][b]["spread"][str(y0)], 4) for b in ("mortgage", "retail_loans", "securities")]
    assert got == [0.0067, 0.0067, -0.028]          # якорь 3К26 (книга 1.4); на 2К26 — 0,0054 / 0,0054 / −0,0279 (sanity-val S1 п. 3)


def test_misc_quarter_shares_are_the_2024_2025_profile_of_the_facts(book):
    """Доли кварталов «прочего» (решение A9, MODEL §4.7): Σ = 1, неотрицательны, 4К — крупнейшая; повтор по фактам
    `pnl_quarterly.misc_net` полных лет 2024–2025 (квартал с обратным знаком — 0) — те же доли до округления."""
    shares = book["other"]["misc_quarter_shares"]
    assert list(shares) == ["1", "2", "3", "4"]
    assert abs(sum(shares.values()) - 1.0) < 1e-9 and min(shares.values()) >= 0.0
    assert max(shares, key=shares.get) == "4"
    pnl = json.loads((S.FACTS_DIR / "pnl_quarterly.json").read_text(encoding="utf-8"))
    sums = {str(k): sum(pnl["quarters"][f"{y}Q{k}"]["misc_net"]["v"] for y in (2024, 2025)) for k in (1, 2, 3, 4)}
    total = sum(sums.values())
    kept = {k: max(0.0, v / total) for k, v in sums.items()}
    for k, v in kept.items():
        assert shares[k] == pytest.approx(v / sum(kept.values()), abs=1e-3), k


def test_opex_real_growth_ends_and_its_axis_leaves_the_end_alone(book):
    """Реальный рост расходов (A8): до 2030 включительно, с 2031 — 0; ось полосы сдвигает только явные годы до конца
    роста (иначе в полосе вернулся бы бессрочный рост, против которого A8)."""
    og = book["opex"]["real_growth"]
    end, last = int(og["LT_from"]), int(book["meta"]["last_period"][:4])
    assert og["LT"] == 0.0 and end <= last
    assert all(S.year_value(og, y) == 0.0 for y in range(end, last + 1))
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"][0].startswith("opex.real_growth"))
    assert ax["kind"] == "shift"
    years = sorted(int(p.rpartition(".")[2]) for p in ax["paths"])
    assert all(p.startswith("opex.real_growth.") for p in ax["paths"]) and max(years) < end
    assert years == sorted(int(k) for k in og if str(k).isdigit())


def test_new_keys_of_books_1_1_and_1_2_have_valid_forms(book):
    """Формы новых ключей (MODEL прил. A): ramp_years — целое ≥ 1; sigma0_from — год сетки; классы акций — по каждому
    тикеру, главный — обыкновенные; оси вне полосы — формат осей (`value` | `shift`), не повторяют оси полосы; окно
    A-P2u — целое ≥ 1; опора FVC — режим книги; сетка ЧПМ «что даст отчёт» — по возрастанию; коридоры гейтов —
    [мин < макс]."""
    ry = book["dividends"]["excess"]["ramp_years"]
    assert isinstance(ry, int) and not isinstance(ry, bool) and ry >= 1
    y_first, y_last = int(book["meta"]["first_period"][:4]), int(book["meta"]["last_period"][:4])
    assert y_first <= book["nii"]["sigma0_from"] <= y_last
    c = book["meta"]["company"]
    assert set(c["share_classes"]) == set(c["tickers"]) and set(c["share_classes"].values()) <= {"ordinary", "preferred"}
    assert c["share_classes"][c["main_ticker"]] == "ordinary"
    unc = book["valuation"]["uncertainty"]
    names = [a["name"] for a in unc["axes"]]
    assert len(set(names)) == len(names)
    for a in unc["off_band_axes"]:
        assert a["kind"] in ("value", "shift") and a["dist"] == "triangular" and a["name"] not in names, a
        for p in a["paths"]:
            assert S.has(book, p), (a["name"], p)
    w = book["joint"]["regime_update"]["window_obs"]                              # книга 1.2: окно A-P2u — целое ≥ 1
    assert isinstance(w, int) and not isinstance(w, bool) and w >= 1
    assert book["credit"]["fv_loans_ref"] in book["regimes"]["ids"] and 0.0 <= book["credit"]["fv_loans_factor"] <= 1.0
    nims = book["valuation"]["next_report"]["nim_values"]
    assert nims == sorted(nims) and len(set(nims)) == len(nims)
    lo, hi = book["valuation"]["sensitivities"]["rank_window"]                    # книга 1.3: окно вокруг медианы
    assert 0.0 < lo < 0.5 < hi < 1.0 and abs((lo + hi) / 2 - 0.5) < 1e-12
    assert 0.0 < book["valuation"]["next_report"]["value_tol"] <= 0.001
    lo, hi = book["checks"]["transmission_pairs"]
    assert lo < hi
    for k in ("nim_path_joint", "ni_jump"):
        q = book["checks"][k]["quarters"]
        assert isinstance(q, int) and 1 <= q <= 42
    assert 0 < book["checks"]["nim_path_joint"]["tolerance"] < 0.01
    assert 0 < book["checks"]["ni_jump"]["key_tol"] < 0.05 and 0 < book["checks"]["ni_jump"]["yoy"] < 1
    lo, hi = book["checks"]["cir_range"]
    assert 0 < lo < hi < 1
