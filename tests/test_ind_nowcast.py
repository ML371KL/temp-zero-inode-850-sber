"""Нау-каст квартала: уравнение DESIGN §5, месяцы РСБУ и профиль, вес w, заморозка, горизонты; ретро на истории."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from indicators import config, nowcast, retro
from indicators.periods import quarter_months, shift_month, shift_quarter, year_ago
from indicators.store import Point, Store
from tests.support_ind import FIX, seeded_store

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)

SIGMAS = {"ras": {"0": 0.07, "1": 0.04, "2": 0.04, "3": 0.035}, "base": 0.065}


def test_combine_is_the_design_equation():
    ras = {"version": nowcast.VERSION, "targets": {
        "ni_q": {"ras_estimate": 500.0, "bridge": 1.003, "ras_bridged": 501.5, "w": 0.7, "rel_std_error": 0.03},
        "nim_q": {"ras_bridged": None, "w": 0.0}, "cor_q": {"ras_bridged": None, "w": 0.0}}}
    out = nowcast.combine(ras, {"ni_q": 490.0, "nim_q_mgmt": 0.061, "cor_q_mgmt": 0.012})
    ni = out["ni_q"]
    assert ni["forecast"] == pytest.approx(490.0 + 0.7 * (501.5 - 490.0))
    assert ni["deviation"] == pytest.approx(ni["forecast"] - 490.0)
    assert ni["std_error"] == pytest.approx(ni["forecast"] * 0.03)
    assert ni["interval"] == pytest.approx([ni["forecast"] - ni["std_error"], ni["forecast"] + ni["std_error"]])
    assert out["nim_q"]["forecast"] == 0.061 and out["nim_q"]["w"] == 0.0 and out["nim_q"]["basis"] == "mgmt"
    assert out["cor_q"]["forecast"] == 0.012
    assert any(b["key"] == "model" for b in ni["benchmarks"])
    no_model = nowcast.combine(ras, {})
    assert no_model["ni_q"]["forecast"] == 501.5 and no_model["nim_q"]["forecast"] is None


def test_weight_from_measured_variances():
    assert nowcast.weight(0.065, 0.035) == pytest.approx(0.065 ** 2 / (0.065 ** 2 + 0.035 ** 2))
    assert nowcast.weight(None, 0.03) == 0.0 and nowcast.weight(0.05, 0.0) == 1.0


def test_ras_inputs_on_history(tmp_path):
    store = seeded_store(tmp_path)
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    out = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=bridge, sigmas=SIGMAS)
    months = {m["month"]: m for m in out["months"]}
    assert months["2026M07"]["status"] == "known" and months["2026M07"]["ni"] == pytest.approx(168.4)
    assert months["2026M08"]["ni"] == pytest.approx(169.1) and months["2026M08"]["nii"] == pytest.approx(325.2)
    sep = months["2026M09"]
    assert sep["status"] == "estimated" and sep["source"] == "profile"
    # профиль: август этого года × (сентябрь / август прошлого года)
    h = nowcast.History.load(store)
    ratio = h.month("2025M09", None)[0] / h.month("2025M08", None)[0]
    assert sep["ni"] == pytest.approx(169.1 * ratio)
    ni = out["targets"]["ni_q"]
    assert out["months_known"] == 2 and ni["bridge"] == 1.003 and ni["bridge_source"].startswith("facts")
    assert ni["ras_bridged"] == pytest.approx((168.4 + 169.1 + sep["ni"]) * 1.003)
    assert ni["w"] == pytest.approx(nowcast.weight(0.065, 0.04))
    assert ni["rel_std_error"] == pytest.approx(math.sqrt(ni["w"]) * 0.04)
    assert out["targets"]["cor_q"]["ras_estimate"] is None                # моста РСБУ → упр. нет


def test_frozen_values_win_over_recomputed(tmp_path):
    store = seeded_store(tmp_path)
    frozen = {"bridge": 1.1, "profile": {"2025M08": 100.0, "2025M09": 110.0},
              "sigmas": {"ras": {"2": 0.05}, "base": 0.05}}
    out = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge={}, frozen=frozen)
    sep = [m for m in out["months"] if m["month"] == "2026M09"][0]
    assert sep["ni"] == pytest.approx(169.1 * 1.1)
    assert out["profile"] == {"2025M08": 100.0, "2025M09": 110.0}
    assert out["targets"]["ni_q"]["bridge"] == 1.1 and out["targets"]["ni_q"]["w"] == pytest.approx(0.5)


def test_form102_month_counts_as_known(tmp_path):
    store = seeded_store(tmp_path)
    # сентябрь по форме 0409102 (нарастающим) появился раньше релиза
    ytd_aug = store.load("cbr.f102.ni_ytd").value_as_of("2026M08")
    store.upsert("cbr.f102.ni_ytd", [Point("2026M09", ytd_aug + 170.0, "2026-10-01T06:00:00+00:00")])
    out = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge={}, sigmas=SIGMAS)
    sep = [m for m in out["months"] if m["month"] == "2026M09"][0]
    assert sep["status"] == "known" and sep["source"] == "form102" and sep["ni"] == pytest.approx(170.0)
    assert out["months_known"] == 3


def _ytd(store, *points):
    for month, value, day in points:
        store.upsert("cbr.f102.ni_ytd", [Point(month, value, day + "T07:00:00+00:00")], basis="ras", unit="RUB bn")
    return nowcast.History.load(store)


def test_revised_year_to_date_moves_the_next_month(tmp_path):
    """Месяц по форме 0409102 — разность двух нарастающих итогов: пересмотр прошлого итога (и его запоздавший
    приход) меняет и следующий месяц — с момента пересмотра, не раньше."""
    h = _ytd(Store(tmp_path / "revised"), ("2026M01", 100.0, "2026-02-25"), ("2026M02", 220.0, "2026-03-25"),
             ("2026M01", 110.0, "2026-04-02"))
    assert h.month("2026M01", "2026-04-01")[0] == pytest.approx(100.0)
    assert h.month("2026M02", "2026-04-01")[0] == pytest.approx(120.0)     # до пересмотра января
    assert h.month("2026M01", None)[0] == pytest.approx(110.0)
    value, source, first = h.month("2026M02", None)
    assert value == pytest.approx(110.0) and source == "form102" and first.startswith("2026-03-25")
    assert h.quarter_ras("2026Q1", None) is None                            # марта нет — квартал не собран
    late = _ytd(Store(tmp_path / "late"), ("2026M02", 220.0, "2026-03-25"), ("2026M01", 100.0, "2026-04-02"))
    assert late.month("2026M02", "2026-04-01") is None                      # январь ещё неизвестен
    value, _, first = late.month("2026M02", None)
    assert value == pytest.approx(120.0) and first.startswith("2026-04-02")  # известен с прихода января
    same = _ytd(Store(tmp_path / "same"), ("2026M01", 100.0, "2026-02-25"), ("2026M02", 220.0, "2026-03-25"),
                ("2026M03", 330.0, "2026-04-25"), ("2026M02", 230.0, "2026-05-02"))
    assert [r[1] for r in same.months["2026M01"]] == [100.0]                # январь пересмотр февраля не трогает
    assert [r[1] for r in same.months["2026M02"]] == [120.0, 130.0]
    assert [r[1] for r in same.months["2026M03"]] == [110.0, 100.0]
    december = _ytd(Store(tmp_path / "year"), ("2025M12", 1500.0, "2026-01-25"), ("2026M01", 100.0, "2026-02-25"),
                    ("2025M12", 1510.0, "2026-03-02"))
    assert [r[1] for r in december.months["2026M01"]] == [100.0]            # январь — сам итог, декабрь ни при чём
    # Два соседних итога пересмотрены в один момент (формы одной перекачки): у февраля на этот момент одно
    # значение — разность новых итогов, а не нового января и прежнего февраля (200 − 90 = 110 было бы больше
    # верных 105 и победило бы при выборе последней версии).
    together = _ytd(Store(tmp_path / "together"), ("2026M01", 100.0, "2026-02-25"), ("2026M02", 200.0, "2026-03-25"),
                    ("2026M01", 90.0, "2026-04-02"), ("2026M02", 195.0, "2026-04-02"))
    assert [r[1] for r in together.months["2026M01"]] == [100.0, 90.0]
    assert [r[1] for r in together.months["2026M02"]] == [100.0, 105.0]
    assert together.month("2026M02", None)[0] == pytest.approx(105.0)
    assert together.month("2026M02", "2026-04-01")[0] == pytest.approx(100.0)


def test_fact_of_a_quarter_is_read_as_of_the_date(tmp_path):
    """У факта квартала две версии: на дату между ними история отвечает первой — как хранилище, — а дата
    отчёта остаётся днём первого появления; без даты — последняя версия."""
    store = Store(tmp_path)
    for series, first, second in (("actual.ni_q", 100.0, 120.0), ("actual.nim_q", 0.060, 0.062)):
        store.upsert(series, [Point("2026Q1", first, "2026-04-30T07:00:00+00:00")])
        store.upsert(series, [Point("2026Q1", second, "2026-06-30T07:00:00+00:00", "manual")])
    h = nowcast.History.load(store)
    assert h.quarter_ifrs("2026Q1", "2026-04-29") is None
    assert h.quarter_ifrs("2026Q1", "2026-05-01") == store.value_as_of("actual.ni_q", "2026Q1", "2026-05-01") == 100.0
    assert h.quarter_ifrs("2026Q1", "2026-06-30") == h.quarter_ifrs("2026Q1", None) == 120.0
    assert h.report_date("2026Q1") == "2026-04-30" and h.ifrs["2026Q1"][1] == 120.0
    assert h.mgmt_quarter("nim", "2026Q1", "2026-05-01") == 0.060
    assert h.mgmt_quarter("nim", "2026Q1", None) == 0.062 and h.mgmt_quarter("nim", "2026Q1", "2026-04-01") is None
    manual = nowcast.History(store=store)                                   # история без версий — последнее значение
    manual.ifrs = {"2026Q1": ("2026-04-30T07:00:00+00:00", 120.0)}
    assert manual.quarter_ifrs("2026Q1", "2026-05-01") == 120.0


def test_horizons():
    rep = date(2026, 10, 28)
    assert nowcast.horizon_for(date(2026, 7, 30), rep) == "T-90"
    assert nowcast.horizon_for(date(2026, 9, 28), rep) == "T-30"
    assert nowcast.horizon_for(date(2026, 9, 27), rep) == "T-90"
    assert nowcast.horizon_for(date(2026, 10, 28), rep) is None
    assert nowcast.horizon_for(date(2026, 7, 29), rep) is None


def test_retro_and_sigmas_on_history(tmp_path):
    store = seeded_store(tmp_path)
    r = retro.retro(store, today=date(2026, 10, 1))
    assert r["horizon"] == "T-30" and r["main"] == "ras_bridge"
    for hz in ("T-30", "T-90"):
        block = r["by_horizon"][hz]
        assert block["periods"] and all(math.isfinite(v) for v in block["rmse"].values())
        assert 0.01 < block["rmse"]["ras_bridge"] < 0.10
    assert r["rmse"]["consensus"] < r["rmse"]["prev_quarter"]
    s = retro.sigma_table(store, today=date(2026, 10, 1))
    assert s["ras"]["3"] < s["ras"]["0"] and 0.0 < s["base"] < 0.2
    assert s["n"]["ras"]["3"] >= 8


def test_consensus_is_a_benchmark_of_the_near_horizon_only(tmp_path):
    """№ 44: консенсус снят у отчёта — на горизонте T−90 его нет, на T−30 он назван «у отчёта»."""
    r = retro.retro(seeded_store(tmp_path), today=date(2026, 10, 1))
    far, near = r["by_horizon"]["T-90"], r["by_horizon"]["T-30"]
    assert "consensus" not in far["rmse"] and "consensus" not in far["titles"]
    assert not any("consensus" in p["benchmarks"] for p in far["periods"])
    assert near["n"]["consensus"] >= 8 and "у отчёта" in near["titles"]["consensus"]
    assert r["titles"] == near["titles"]


def test_far_horizon_is_measured_on_what_the_journal_can_know(tmp_path):
    """№ 45: дата горизонта — не раньше МСФО прошлого квартала: T−90 первого квартала ≈ T−60, январь РСБУ известен."""
    store = seeded_store(tmp_path)
    h = nowcast.History.load(store)
    firsts = [q for q in sorted(h.ifrs) if q.endswith("Q1") and h.report_date(shift_quarter(q, -1))]
    assert firsts
    for q in firsts:
        report, opened = h.report_date(q), h.report_date(shift_quarter(q, -1))
        day = retro.horizon_date(h, q, report, retro.HORIZON_DAYS["T-90"])
        assert day == opened > (date.fromisoformat(report) - timedelta(days=90)).isoformat(), q
        assert nowcast.ras_estimate(h, q, day)["months_known"] == 1, q          # январь вышел до МСФО за год
        assert retro.benchmarks_at(h, store, q, day)["prev_quarter"] == h.ifrs[shift_quarter(q, -1)][1]
    q2 = "2026Q2"                                                       # у 2–4 кварталов — обычные 90 дней
    assert retro.horizon_date(h, q2, h.report_date(q2), 90) == \
        (date.fromisoformat(h.report_date(q2)) - timedelta(days=90)).isoformat()
    assert retro.horizon_date(h, min(h.ifrs), "2020-05-01", 30) == "2020-04-01"  # прошлого отчёта в истории нет
    r = retro.retro(store, today=date(2026, 10, 1))
    far = r["by_horizon"]["T-90"]
    assert far["rmse"]["ras_bridge"] > r["rmse"]["ras_bridge"]         # дальше от отчёта — мост хуже
    assert far["rmse"]["prev_quarter"] == pytest.approx(r["rmse"]["prev_quarter"])   # прошлый квартал известен на обоих
    base = retro.sigma_table(store, today=date(2026, 10, 1))
    assert base["base"] == pytest.approx(far["rmse"]["yoy_growth"])    # σ_база — тот же набор знаний


def test_retro_is_honest_about_what_was_known(tmp_path):
    store = seeded_store(tmp_path)
    h = nowcast.History.load(store)
    marks = retro.benchmarks_at(h, store, "2026Q2", "2026-04-30")      # T−90 ≈ конец апреля
    assert marks["prev_quarter"] == pytest.approx(508.5)                 # 1К26 вышла 29.04
    early = retro.benchmarks_at(h, store, "2026Q2", "2026-04-28")
    assert early["prev_quarter"] == pytest.approx(399.0)                 # до выхода 1К26 — 4К25


def test_mgmt_retro(tmp_path):
    store = seeded_store(tmp_path)
    out = retro.mgmt_retro(store, "nim_q")
    assert out["periods"] and out["rmse"]["prev_quarter"] < 0.01
    assert retro.mgmt_retro(Store(tmp_path / "empty"), "cor_q") == {"periods": [], "rmse": {}}


# ------------------------------------------------------------------ T3 ЧПМ и T4 CoR (B1)

NIM_SIGMAS = {**SIGMAS, "nim": {"ras": {"0": 0.002, "1": 0.0025, "2": 0.0013, "3": 0.001}, "base": 0.002}}


def _to_mgmt(v: float) -> float:
    return v + 0.00384                     # аддитивный мост ядра: движок − упр. = −0,384 п.п.


def test_nim_nowcast_through_the_nii_and_asset_bridges(tmp_path):
    store = seeded_store(tmp_path)
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    out = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=bridge, sigmas=NIM_SIGMAS,
                             to_mgmt_nim=_to_mgmt)
    t = out["targets"]["nim_q"]
    h = nowcast.History.load(store)

    def nii(m):
        return h.release_month("nii_m", m, None)[0]

    # профиль ЧПД (DECISIONS 14.7): сентябрь год назад × рост г/г июня–августа
    sep = nii("2025M09") * sum(map(nii, ("2026M06", "2026M07", "2026M08"))) / sum(
        map(nii, ("2025M06", "2025M07", "2025M08")))
    assert t["nii_ras"] == pytest.approx(nii("2026M07") + nii("2026M08") + sep) and t["months_known"] == 2
    assert t["profile_rule"] == nowcast.PROFILE_YOY and set(t["profile"]) == {"2025M06", "2025M07", "2025M08",
                                                                              "2025M09"}
    # средние активы: конец июня известен, конец сентября — конец августа × рост трёх месяцев
    months = {m["month"]: m for m in t["iea_months"]}
    jun, may, aug_a = (h.iea_month(m, None)[0] for m in ("2026M06", "2026M05", "2026M08"))
    assert months["2026M06"] == {"month": "2026M06", "status": "known", "value": jun}
    g = (aug_a / may) ** (1 / 3) - 1
    assert months["2026M09"]["status"] == "estimated" and months["2026M09"]["value"] == pytest.approx(aug_a * (1 + g))
    assert t["iea_ras"] == pytest.approx((jun + aug_a * (1 + g)) / 2)
    assert t["ras_estimate"] == pytest.approx(t["nii_ras"] * 365 / 92 / t["iea_ras"])
    b = bridge["nii"]["by_quarter"]["3"]["v"] / bridge["iea"]["by_quarter"]["3"]["v"]
    assert t["bridge"] == pytest.approx(b) and t["bridge_source"].startswith("facts/bridge_ras_ifrs.json → nii")
    assert t["ras_bridged"] == pytest.approx(_to_mgmt(t["ras_estimate"] * b))
    assert t["w"] == pytest.approx(nowcast.weight(0.002, 0.0013))
    assert t["std_error"] == pytest.approx(math.sqrt(t["w"]) * 0.0013)
    c = nowcast.combine(out, {"ni_q": 495.0, "nim_q_mgmt": 0.064, "cor_q_mgmt": 0.0135})["nim_q"]
    assert c["forecast"] == pytest.approx(0.064 + t["w"] * (t["ras_bridged"] - 0.064))
    assert c["std_error"] == pytest.approx(t["std_error"])            # абсолютная, не доля прогноза
    assert "мост ЧПД" in c["equation"] and c["basis"] == "mgmt"


def test_nim_without_a_bridge_or_the_core_bridge_equals_the_expectation(tmp_path):
    store = seeded_store(tmp_path)
    exp = {"ni_q": 495.0, "nim_q_mgmt": 0.064, "cor_q_mgmt": 0.0135}
    none = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge={}, sigmas=NIM_SIGMAS)
    assert none["targets"]["nim_q"]["ras_bridged"] is None
    c = nowcast.combine(none, exp)["nim_q"]
    assert c["forecast"] == 0.064 and c["w"] == 0.0 and c["equation"] == nowcast.NO_BRIDGE["nim_q"]
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    no_core = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=bridge, sigmas=NIM_SIGMAS)
    t = no_core["targets"]["nim_q"]
    assert t["ras_bridged_ifrs"] is not None and t["ras_bridged"] is None and t["equation"] == nowcast.NO_MGMT
    no_sigma = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=bridge, sigmas=SIGMAS,
                                  to_mgmt_nim=_to_mgmt)
    assert no_sigma["targets"]["nim_q"]["w"] == 0.0 and nowcast.NO_SIGMA in no_sigma["targets"]["nim_q"]["equation"]


def test_frozen_nim_bridge_and_basis_win(tmp_path):
    store = seeded_store(tmp_path)
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    frozen = {"nim": {"bridge": 1.05, "basis": "mgmt", "sigmas": {"ras": {"2": 0.001}, "base": 0.001}}}
    t = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=bridge, sigmas=NIM_SIGMAS,
                           frozen=frozen)["targets"]["nim_q"]
    assert t["bridge"] == 1.05 and t["bridge_source"] == "frozen" and t["bridge_basis"] == "mgmt"
    assert t["ras_bridged"] == pytest.approx(t["ras_estimate"] * 1.05) and t["w"] == pytest.approx(0.5)


def test_cor_t4_is_the_expectation_without_a_bridge_and_uses_one_when_given(tmp_path):
    store = seeded_store(tmp_path)
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    out = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=bridge, sigmas=NIM_SIGMAS)
    t = out["targets"]["cor_q"]
    assert bridge["cor"]["v"] is None and t["ras_estimate"] is None and t["equation"] == nowcast.NO_BRIDGE["cor_q"]
    c = nowcast.combine(out, {"cor_q_mgmt": 0.0135})["cor_q"]
    assert c["forecast"] == 0.0135 and c["w"] == 0.0 and "T4" in c["equation"]
    with_bridge = {"cor": {"value": {"v": 0.8, "calc": "тест"}}}
    sig = {**SIGMAS, "cor": {"ras": {"1": 0.003}, "base": 0.004}}
    t = nowcast.ras_inputs(store, period="2026Q3", today=date(2026, 10, 1), bridge=with_bridge,
                           sigmas=sig)["targets"]["cor_q"]
    h = nowcast.History.load(store)
    aug = h.release_month("cor_m", "2026M08", None)[0]
    assert t["months_known"] == 1 and t["ras_estimate"] == pytest.approx(aug)     # в релизах истории — только август
    assert t["ras_bridged"] == pytest.approx(aug * 0.8) and t["w"] == pytest.approx(nowcast.weight(0.004, 0.003))


def test_ratio_sigmas_are_measured_out_of_sample(tmp_path):
    store = seeded_store(tmp_path)
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    s = retro.ratio_sigma_table(store, bridge=bridge, to_mgmt_nim=_to_mgmt, today=date(2026, 10, 1))
    nim = s["nim"]
    first = min(r["period"] for r in bridge["iea"]["quarters"])
    assert nim["quarters"] and min(nim["quarters"]) > first               # у первого квартала моста ещё нет
    assert nim["bridge_window"] == {"nii": 4, "iea": 1}                   # окна метода фактов
    assert all(0.0 < v < 0.01 for v in nim["ras"].values()) and 0.0 < nim["base"] < 0.01
    assert s["cor"]["ras"] == {"0": None, "1": None, "2": None, "3": None} and s["cor"]["base"] > 0
    assert retro.ratio_sigma_table(store, bridge=bridge, today=date(2026, 10, 1))["nim"]["quarters"] == []


def test_nim_sigma_is_measured_on_the_day_the_month_came_out(tmp_path):
    """σ_РСБУ(k) меряется на знании дня, когда вышел k-й месяц квартала: процентные активы конца квартала в
    этот день ещё не вышли и продлеваются ростом, как в живом прогнозе; оценка дня отчёта (с известным концом
    квартала) в σ не идёт. k = 0 — дата дальнего горизонта. День знания — не раньше открытия квартала (выхода
    МСФО прошлого): январь выходит до МСФО за год, а журнал квартал ещё не открыл."""
    store = seeded_store(tmp_path)
    bridge = config.facts_file("bridge_ras_ifrs", FIX / "facts")
    h = nowcast.History.load(store)

    def nii_month(m, as_of):
        return h.release_month("nii_m", m, as_of)

    q = "2026Q2"
    report = h.mgmt["nim"][q][0][:10]
    day = retro.known_date(h, q, 1, report, nii_month)
    assert h.report_date("2026Q1") < day == nii_month("2026M04", None)[2][:10] < report
    assert retro.known_date(h, q, 0, report, nii_month) == retro.horizon_date(h, q, report, retro.HORIZON_DAYS["T-90"])
    assert retro.known_date(h, "2031Q1", 1, "2031-04-28", nii_month) is None       # месяца нет — оценки нет
    first, opened = "2026Q1", h.report_date("2025Q4")
    first_report = h.mgmt["nim"][first][0][:10]
    assert nii_month("2026M01", None)[2][:10] < opened < first_report              # январь вышел до МСФО за год
    assert retro.known_date(h, first, 1, first_report, nii_month) == opened
    assert retro.known_date(h, first, 1, first_report, h.month) == opened          # то же — у прибыли
    assert retro.known_date(h, first, 2, first_report, nii_month) == nii_month("2026M02", None)[2][:10] > opened
    live, late = nowcast.nim_ras(h, q, day, known=1), nowcast.nim_ras(h, q, report, known=1)
    assert [m["status"] for m in live["iea"]["months"]] == ["known", "estimated"]
    assert [m["status"] for m in late["iea"]["months"]] == ["known", "known"]
    assert abs(live["value"] - late["value"]) > 1e-4                               # расхождение — не округление
    table = retro.ratio_sigma_table(store, bridge=bridge, to_mgmt_nim=_to_mgmt, today=date(2026, 10, 1))["nim"]
    errors, yoy = [], []
    for period in table["quarters"]:
        moment, actual = h.mgmt["nim"][period]
        when = retro.known_date(h, period, 1, moment[:10], nii_month)
        est = nowcast.nim_ras(h, period, when, known=1) if when else None
        if est is None or est["nii"]["months_known"] != 1:
            continue
        b = (nowcast.facts_ratio_before(bridge["nii"], period, table["bridge_window"]["nii"])
             / nowcast.facts_ratio_before(bridge["iea"], period, table["bridge_window"]["iea"]))
        errors.append(_to_mgmt(est["value"] * b) - actual)
        # профиль ретро — правило живого нау-каста: месяц год назад × рост г/г трёх последних известных месяцев
        if est["nii"]["profile_rule"] == nowcast.PROFILE_YOY:
            months, window = quarter_months(period), [shift_month(quarter_months(period)[0], -i) for i in range(3)]
            growth = sum(nii_month(m, when)[0] for m in window) / sum(nii_month(year_ago(m), when)[0] for m in window)
            got = {r["month"]: r["value"] for r in est["nii"]["months"]}
            assert [got[m] for m in months[1:]] == pytest.approx(
                [nii_month(year_ago(m), when)[0] * growth for m in months[1:]], rel=1e-12), period
            yoy.append(period)
    # у 2024К1 трёх месяцев год назад (ноябрь 2022 — январь 2023) в истории нет — правило последнего месяца
    assert len(errors) == table["n"]["ras"]["1"] >= 8 and len(yoy) >= len(errors) - 1
    assert table["ras"]["1"] == pytest.approx(math.sqrt(sum(e * e for e in errors) / len(errors)), rel=1e-12)
    profit = retro.sigma_table(store, today=date(2026, 10, 1))                    # у прибыли день знания тот же ответ
    assert profit["n"]["ras"]["1"] >= 8 and 0.0 < profit["ras"]["1"] < profit["ras"]["0"]


def _nii_history(values: dict[str, float]) -> nowcast.History:
    """История ЧПД релиза без хранилища: месяц выходит 9-го числа следующего месяца."""
    h = nowcast.History(store=Store.__new__(Store))
    h.release = {"nii_m": {m: [(f"{shift_month(m, 1)[:4]}-{shift_month(m, 1)[5:]}-09T07:00:00+00:00", v, "release")]
                           for m, v in values.items()}}
    h.iea = {m: [(f"{shift_month(m, 1)[:4]}-{shift_month(m, 1)[5:]}-26T07:00:00+00:00", 60000.0 + 100 * i, "form101")]
             for i, m in enumerate(("2026M05", "2026M06", "2026M07", "2026M08"))}
    return h


NII = {"2025M04": 240.0, "2025M05": 245.0, "2025M06": 250.0, "2025M07": 260.0, "2025M08": 255.0, "2025M09": 275.0,
       "2026M04": 300.0, "2026M05": 310.0, "2026M06": 320.0, "2026M07": 315.0, "2026M08": 325.0}


def test_nii_profile_is_the_year_ago_month_times_the_three_month_growth():
    """DECISIONS 14.7: недостающий месяц ЧПД (T3) — тот же месяц год назад × рост г/г трёх последних известных
    месяцев; нет какого-то из шести чисел — правило прибыли (последний месяц × отношение год назад). Прибыль (T1) —
    прежнее правило; замороженный профиль важнее истории."""
    h, day, q = _nii_history(NII), "2026-09-20", "2026Q3"

    def nii(m, as_of):
        return h.release_month("nii_m", m, as_of)

    est = nowcast.nim_ras(h, q, day)["nii"]                                       # известны июль и август
    sep = 275.0 * (320.0 + 315.0 + 325.0) / (250.0 + 260.0 + 255.0)
    assert est["months"][2]["value"] == pytest.approx(sep) and est["estimate"] == pytest.approx(315.0 + 325.0 + sep)
    assert (est["profile_rule"], est["profile_base"]) == (nowcast.PROFILE_YOY, "2026M08")
    assert est["profile"] == {"2025M06": 250.0, "2025M07": 260.0, "2025M08": 255.0, "2025M09": 275.0}
    assert nowcast.PROFILE_RULES == {"ni_q": nowcast.PROFILE_LAST, "nim_q": nowcast.PROFILE_YOY}
    profit = nowcast.ras_estimate(h, q, day, getter=nii, key="value")              # правило T1 — прежнее
    assert profit["months"][2]["value"] == pytest.approx(325.0 * 275.0 / 255.0) and 325.0 * 275.0 / 255.0 - sep > 5
    assert profit["profile_rule"] == nowcast.PROFILE_LAST and set(profit["profile"]) == {"2025M08", "2025M09"}
    none = nowcast.nim_ras(h, q, day, known=0)["nii"]                             # k = 0: база — июнь
    growth = (320.0 + 310.0 + 300.0) / (250.0 + 245.0 + 240.0)
    assert [r["value"] for r in none["months"]] == pytest.approx([260.0 * growth, 255.0 * growth, 275.0 * growth])
    assert none["months_known"] == 0 and none["profile_base"] == "2026M06"
    frozen = nowcast.nim_ras(h, q, day, profile={"2025M09": 280.0, "2025M06": 251.0})["nii"]
    assert frozen["months"][2]["value"] == pytest.approx(280.0 * 960.0 / (251.0 + 260.0 + 255.0))
    gap = _nii_history({m: v for m, v in NII.items() if m != "2025M06"})          # июня год назад нет
    fallback = nowcast.nim_ras(gap, q, day)["nii"]
    assert fallback["profile_rule"] == nowcast.PROFILE_LAST
    assert fallback["months"][2]["value"] == pytest.approx(325.0 * 275.0 / 255.0)
    full = nowcast.nim_ras(_nii_history({**NII, "2026M09": 330.0}), q, "2026-10-10")["nii"]
    assert full["profile_rule"] is None and full["profile"] == {} and full["months_known"] == 3   # профиль не нужен


def test_iea_average_from_an_older_form():
    store = Store.__new__(Store)
    h = nowcast.History(store=store)
    h.iea = {"2026M04": [("2026-05-26T07:00:00+00:00", 100.0, "form101")],
             "2026M05": [("2026-06-26T07:00:00+00:00", 101.0, "form101")]}
    got = nowcast.iea_average(h, "2026Q3", "2026-07-01")
    assert [m["status"] for m in got["months"]] == ["estimated", "estimated"]
    g = (101.0 / 100.0) - 1
    assert got["months"][0]["value"] == pytest.approx(101.0 * (1 + g))
    assert got["months"][1]["value"] == pytest.approx(101.0 * (1 + g) ** 4)
    assert nowcast.iea_average(h, "2027Q1", "2026-07-01") is None       # последний конец месяца слишком стар
