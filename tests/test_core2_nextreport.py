"""Что даст отчёт (М§13): открытый квартал, ожидание, таблицы, нейтральные CoR и ЧПМ не двигают медиану."""

from __future__ import annotations

import pytest

from model import uncertainty as U
from model.grid import live_from_book, run_grid
from model.nextreport import edge_slope, model_expectation, next_report, open_period, with_observation
from model.timeline import DAYS_IN_YEAR
from tests.support_core2 import book_and_facts, small_book


@pytest.mark.tact
def test_open_period_is_first_quarter_without_observation():
    book, facts = book_and_facts()
    first = str(book.get("meta.first_period"))
    assert open_period(book, facts) == first
    b2 = with_observation(book, first, cor=0.014, nim=0.062)
    assert open_period(b2, facts) != first and open_period(b2, facts) > first


@pytest.mark.tact
def test_model_expectation_is_the_analytical_layer_mean():
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    run = run_grid(book, facts, live)
    period = open_period(book, facts)
    e = model_expectation(book, facts, period, live=live, run=run)
    q = run.ctx.timeline.index(period)
    prob = run.layers["analytical"].prob
    assert e["cor_q_engine"] == pytest.approx(sum(prob[c.key] * c.quarters["cor"][q] for c in run.cells))
    assert e["cor_q_mgmt"] == pytest.approx(run.ctx.bridge.to_mgmt_cor(e["cor_q_engine"]))
    assert e["nim_q_mgmt"] == pytest.approx(run.ctx.bridge.to_mgmt_nim(e["nim_q_engine"]))
    assert e["ni_q"] == pytest.approx(sum(prob[c.key] * c.quarters["ni_sh"][q] for c in run.cells))
    assert {r["regime"] for r in e["by_regime"]} == set(book.get("regimes.ids"))


@pytest.fixture(scope="module")
def report():
    """Малая книга; таблицы CoR и ЧПМ — по три значения вокруг ожидания модели."""
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    e = model_expectation(book, facts, open_period(book, facts), live=live)
    cor = [round(e["cor_q_mgmt"] + d, 4) for d in (-0.003, 0.0, 0.003)]
    nim = [round(e["nim_q_mgmt"] + d, 4) for d in (-0.004, 0.0, 0.004)]
    book = small_book(book, **{"valuation.next_report.cor_values": cor, "valuation.next_report.nim_values": nim})
    band = U.band(book, facts, live, draws=8, workers=1)
    anchor = U.MedianAnchor(book, facts, live, band)
    return book, facts, live, band, anchor, next_report(book, facts, live, band, anchor=anchor)


def test_tables_follow_the_book_values(report):
    book, _, _, band, _, nr = report
    assert [r["cor"] for r in nr["cor_table"]] == book.get("valuation.next_report.cor_values")
    assert [r["nim"] for r in nr["nim_table"]] == book.get("valuation.next_report.nim_values")
    base = band.medians(band.lam)["central"]
    for r in nr["cor_table"] + nr["nim_table"]:
        assert r["d_median"] == pytest.approx(r["median"] - base)
        assert sum(r["posterior"].values()) == pytest.approx(1.0)
    # хуже CoR — ниже медиана и точка; выше ЧПМ — выше
    assert nr["cor_table"][0]["d_point"] > nr["cor_table"][-1]["d_point"]
    assert nr["nim_table"][-1]["d_point"] > nr["nim_table"][0]["d_point"]
    assert nr["slope"]["rub_per_01pp_cor"] < 0 < nr["slope"]["rub_per_01pp_nim"]


def test_table_rows_carry_one_observable_only(report):
    """М§13: второе наблюдаемое строки — не наблюдалось; строка таблицы — книга с наблюдением {x: v, другое: null}."""
    book, facts, live, band, anchor, nr = report
    row = nr["cor_table"][0]
    b2 = with_observation(book, nr["period"], cor=row["cor"], nim=None)
    assert run_grid(b2, facts, live).point == pytest.approx(row["point"], abs=1e-9)
    assert run_grid(b2, facts, live).ctx.posterior == pytest.approx(row["posterior"], abs=1e-12)
    row = nr["nim_table"][-1]
    b3 = with_observation(book, nr["period"], cor=None, nim=row["nim"])
    assert run_grid(b3, facts, live).point == pytest.approx(row["point"], abs=1e-9)
    other = with_observation(book, nr["period"], cor=nr["expectation"]["cor_mgmt"], nim=row["nim"])
    assert run_grid(other, facts, live).point != pytest.approx(row["point"], abs=1e-6)   # CoR на ожидании учит режимы


def test_local_slope_at_the_neutral_value_and_the_reaction_span(report):
    """М§13: наклон — местный, у нейтрального значения: того же знака и по модулю не меньше наклона по крайним
    строкам (реакция — ступень: на хвостах апостериорные упираются в предел сдвига); рядом — размах реакции.
    Сдвиг медианы между наблюдениями ± шаг/2 — оценщиком срединных прогонов (окно рангов — по прогонам книги
    без нового наблюдения), а не разностью двух медиан."""
    book, _, _, band, anchor, nr = report
    lam = band.lam
    window = U.rank_window(book)
    base = anchor.base.exact_centres(lam)
    for kind in ("cor", "nim"):
        local, edge = nr["slope"][f"rub_per_01pp_{kind}"], edge_slope(nr[f"{kind}_table"], kind)
        assert nr["slope"][f"at_{kind}"] == "neutral" and nr["neutral"][kind]["value"] is not None
        # прогоны полосы хранятся с точностью 0,1 ₽ — на столько разность двух медиан и может «дрожать»
        assert local * edge > 0, (kind, local, edge)
        same = {round(r[f"{kind}_mgmt"], 12) for r in nr["expectation"]["by_regime"]}
        if len(same) > 1:        # наблюдение различает режимы: реакция — ступень, на хвостах упирается в предел
            assert abs(local) >= abs(edge) - U.DRAW_STEP, (kind, local, edge)
        else:                    # ожидания режимов на квартал одни (ЧПМ 4К26 книги 1.4): режимы наблюдение не учит,
            assert abs(local - edge) <= 0.1 * abs(edge) + U.DRAW_STEP, (kind, local, edge)   # реакция почти линейна
        d = [r["d_median"] for r in nr[f"{kind}_table"]]
        assert nr["reaction"][kind] == {"d_median_min": min(d), "d_median_max": max(d)}
        step = float(book.get(f"valuation.sensitivities.{kind}_pp"))
        at = nr["neutral"][kind]["value"]
        obs = {"cor": at + step / 2, "nim": None} if kind == "cor" else {"cor": None, "nim": at + step / 2}
        low = {"cor": at - step / 2, "nim": None} if kind == "cor" else {"cor": None, "nim": at - step / 2}
        up = anchor.recompute(with_observation(book, nr["period"], **obs)).exact_centres(lam)
        dn = anchor.recompute(with_observation(book, nr["period"], **low)).exact_centres(lam)
        want = U.median_shift(dn, up, window, by=base)             # среднее попарных разностей срединных прогонов
        assert local == pytest.approx(want / (step / 0.001), abs=1e-9)


@pytest.mark.parametrize("kind", ["cor", "nim"])
def test_neutral_values_do_not_move_the_median(report, kind):
    """М§13: невязка нейтрального значения — сдвиг медианы оценщиком срединных прогонов (среднее попарных
    разностей прогонов окна рангов на общих точках гиперкуба), а не разность двух медиан: та на `median_draws`
    прогонах — ступенчатая функция наблюдения, и корень по ней вставал бы на ступень."""
    book, facts, live, band, anchor, nr = report
    tol = float(book.get("valuation.headline.search_tol_rub"))
    n = nr["neutral"][kind]
    assert n["value"] is not None, nr["neutral"]
    obs = {"cor": n["value"], "nim": None} if kind == "cor" else {"cor": None, "nim": n["value"]}
    b2 = with_observation(book, nr["period"], **obs)
    lam = band.lam
    moved = U.median_shift(anchor.base.exact_centres(lam), anchor.recompute(b2).exact_centres(lam),
                           U.rank_window(book))
    assert moved == pytest.approx(n["gap_rub"], abs=1e-9)
    assert abs(moved) <= tol + 1e-9
    # нейтральная точки: точка на ней не двигается
    p_obs = {"cor": nr["neutral"][f"point_{kind}"], "nim": None} if kind == "cor" else \
        {"cor": None, "nim": nr["neutral"][f"point_{kind}"]}
    base_point = run_grid(book, facts, live).point
    assert abs(run_grid(with_observation(book, nr["period"], **p_obs), facts, live).point - base_point) <= tol + 1e-9


def test_net_income_equivalents(report):
    book, facts, live, _, _, nr = report
    e = nr["expectation"]
    br = run_grid(book, facts, live).ctx.bridge
    k = e["days"] / DAYS_IN_YEAR * (1 - e["tau_eff"])
    cor_n, nim_n = nr["neutral"]["cor"]["value"], nr["neutral"]["nim"]["value"]
    run = run_grid(book, facts, live)                     # отклонение CoR действует и на кредиты по СС (М§13)
    q = run.ctx.timeline.index(nr["period"])
    fv = sum(run.layers["analytical"].prob[c.key] * c.quarters["loans_fv_avg"][q] for c in run.cells)
    base = e["loans_ac"] + float(book.get("credit.fv_loans_factor")) * fv
    assert fv > 0 and nr["neutral"]["cor"]["ni_equivalent"] == pytest.approx(
        e["ni"] - (br.to_engine_cor(cor_n) - e["cor_engine"]) * base * k)
    assert nr["neutral"]["nim"]["ni_equivalent"] == pytest.approx(
        e["ni"] + (br.to_engine_nim(nim_n) - e["nim_engine"]) * e["iea"] * k)


def test_closing_event_and_benchmarks(report):
    _, facts, live, _, _, nr = report
    if "calendar" in facts.files:
        assert nr["closing"] is None or nr["closing"]["published"] == (nr["closing"]["date"] <= live.valuation_date.isoformat())
    keys = [b["key"] for b in nr["benchmarks"]]
    assert keys == ["same_quarter_last_year", "last_quarter", "guidance"]


def test_neutral_nim_is_the_expectation_while_regime_expectations_coincide():
    """М§13, §18: пока ожидания ЧПМ у режимов равны, наблюдение на ожидании режимы не перевзвешивает — нейтральная
    ЧПМ совпадает с ожиданием в пределах `value_tol`, а не «прилипает» к строке таблицы с невязкой в рублёвом
    допуске. Оси полосы — только оценочные (ставка, терминал, дисконт): ожидание квартала в каждом прогоне то же,
    что у книги (ось, сдвигающая ЧПМ открытого квартала, делает то же наблюдение сюрпризом своего прогона)."""
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    e = model_expectation(book, facts, open_period(book, facts), live=live)
    by_regime = [r["nim_q_engine"] for r in e["by_regime"]]
    assert max(by_regime) - min(by_regime) < 1e-6
    axes = [a for a in book.get("valuation.uncertainty.axes") if all(str(x).startswith("valuation.") for x in a["paths"])]
    assert len(axes) >= 3
    value_tol = float(book.get("valuation.next_report.value_tol"))
    tol = float(book.get("valuation.headline.search_tol_rub"))
    near = e["nim_q_mgmt"] + 2.5 * value_tol                # строка рядом с ожиданием: невязка в рублёвом допуске
    nim = [e["nim_q_mgmt"] - 0.004, near, e["nim_q_mgmt"] + 0.004]
    cor = [round(e["cor_q_mgmt"] + d, 4) for d in (-0.003, 0.003)]
    book = small_book(book, **{"valuation.next_report.cor_values": cor, "valuation.next_report.nim_values": nim,
                               "valuation.uncertainty.axes": axes})
    band = U.band(book, facts, live, draws=8, workers=1)
    nr = next_report(book, facts, live, band)
    row = nr["nim_table"][1]
    assert 0 < row["d_median"] <= tol                       # прежнее правило приняло бы эту строку за корень
    n = nr["neutral"]["nim"]
    assert abs(n["value"] - e["nim_q_mgmt"]) <= value_tol, (n, e["nim_q_mgmt"])
    assert n["value"] != near and abs(n["gap_rub"]) <= tol
    assert n["ni_equivalent"] == pytest.approx(e["ni_q"], rel=1e-3)                  # ЧП при нейтральном значении
