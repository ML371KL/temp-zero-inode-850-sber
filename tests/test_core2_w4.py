"""Выпуск, волна перед первым push (решения ведущего P4): нейтральные значения оценщиком срединных
прогонов и ожидание квартала срединных прогонов (М§13); строка обратного расчёта — «в диапазоне» о корне
(М§11.2); печать строк обратного расчёта в сводке книги.
"""

from __future__ import annotations

import pytest

from model import uncertainty as U
from model.book_results import render_run_output
from model.grid import LiveInputs, live_from_book, make_context, regime_forecast
from model.nextreport import middle_expectation, model_expectation, next_report, open_period, with_observation
from model.payload import REQUIRED_FIELDS
from model.reverse import reverse_dcf
from tests.support_core2 import book_and_facts, small_book

DRAWS = 16


@pytest.fixture(scope="module")
def report():
    """Малая книга со ВСЕМИ осями книги; таблицы — по три значения вокруг ожидания модели."""
    book, facts = book_and_facts()
    live = live_from_book(book, facts)
    e = model_expectation(book, facts, open_period(book, facts), live=live)
    cor = [round(e["cor_q_mgmt"] + d, 4) for d in (-0.003, 0.0, 0.003)]
    near = e["nim_q_mgmt"] + 2.5 * float(book.get("valuation.next_report.value_tol"))   # строка в рублёвом допуске
    nim = [e["nim_q_mgmt"] - 0.004, near, e["nim_q_mgmt"] + 0.004]
    book = small_book(book, median_draws=DRAWS, **{"valuation.next_report.cor_values": cor,
                                                   "valuation.next_report.nim_values": nim})
    band = U.band(book, facts, live, draws=DRAWS, workers=1)
    anchor = U.MedianAnchor(book, facts, live, band, base=band)
    return book, facts, live, band, anchor, e, next_report(book, facts, live, band, anchor=anchor)


def test_middle_expectation_is_the_mean_forecast_of_the_middle_draws(report):
    """М§13: ожидание квартала срединных прогонов — среднее прогноза фильтра A-P2u по книгам прогонов, чей ранг
    центра лежит в окне рангов; считается независимо — по тем же точкам гиперкуба."""
    book, facts, live, band, anchor, e, nr = report
    lam, window, period = band.lam, U.rank_window(book), nr["period"]
    chosen = U.middle_draws(band.exact_centres(lam), window)
    assert 1 <= len(chosen) < DRAWS
    want = {"cor": 0.0, "nim": 0.0}
    for i in chosen:
        ctx = make_context(U.draw_book(book, band.axes, band.s[i]), facts, live)
        fc = regime_forecast(ctx, ctx.timeline.index(period))
        want["cor"] += ctx.bridge.to_mgmt_cor(sum(ctx.posterior[r] * fc["cor"][r] for r in fc["cor"])) / len(chosen)
        want["nim"] += ctx.bridge.to_mgmt_nim(sum(ctx.posterior[r] * fc["nim"][r] for r in fc["nim"])) / len(chosen)
    got = middle_expectation(book, facts, live, band, lam, window, period)
    assert got["draws"] == len(chosen)
    for x in ("cor", "nim"):
        assert got[x] == pytest.approx(want[x], abs=1e-12)
        assert nr["neutral"][x]["middle_expectation"] == pytest.approx(want[x], abs=1e-12)
    assert "middle_expectation?" in " ".join(REQUIRED_FIELDS["next_report.neutral.nim"])


def test_neutral_nim_of_the_median_is_the_expectation_of_the_middle_draws(report):
    """М§13: пока ожидания ЧПМ у режимов равны, нейтральная ЧПМ ТОЧКИ совпадает с ожиданием книги, а нейтральная
    ЧПМ МЕДИАНЫ — с ожиданием квартала срединных прогонов (оси сдвигают ожидание квартала в каждом прогоне) —
    в пределах `value_tol`; невязка — сдвиг медианы оценщиком срединных прогонов."""
    book, facts, live, band, anchor, e, nr = report
    by_regime = [r["nim_q_engine"] for r in e["by_regime"]]
    assert max(by_regime) - min(by_regime) < 1e-6, "ожидания ЧПМ у режимов равны"
    value_tol = float(book.get("valuation.next_report.value_tol"))
    tol = float(book.get("valuation.headline.search_tol_rub"))
    n = nr["neutral"]
    assert abs(n["point_nim"] - e["nim_q_mgmt"]) <= value_tol, (n["point_nim"], e["nim_q_mgmt"])
    assert abs(n["nim"]["value"] - n["nim"]["middle_expectation"]) <= value_tol, n["nim"]
    lam = band.lam
    moved = U.median_shift(anchor.base.exact_centres(lam),
                           anchor.recompute(with_observation(book, nr["period"], cor=None, nim=n["nim"]["value"])
                                            ).exact_centres(lam), U.rank_window(book))
    assert moved == pytest.approx(n["nim"]["gap_rub"], abs=1e-9) and abs(moved) <= tol
    # строка таблицы рядом с ожиданием — в рублёвом допуске, но корнем ни у точки, ни у медианы не стала
    row = nr["nim_table"][1]
    assert abs(row["d_point"]) <= tol and n["point_nim"] != row["nim"] and n["nim"]["value"] != row["nim"]


def test_fast_release_carries_no_middle_expectation():
    from model.nextreport import not_computed
    book, facts = book_and_facts()
    block = not_computed(book, facts, live_from_book(book, facts), "быстрая сборка")
    assert block["neutral"]["cor"]["middle_expectation"] is None and block["neutral"]["nim"]["middle_expectation"] is None


@pytest.fixture(scope="module")
def solved():
    """Малая книга, строка ЧПМ; рынок — медиана полосы − 15 ₽: решение внутри диапазона книги."""
    book, facts = book_and_facts()
    axes = [a for a in book.get("valuation.reverse_dcf.axes") if a["paths"] == ["nii.nim_lt_target_mgmt"]]
    book = small_book(book, **{"valuation.reverse_dcf.axes": axes, "valuation.uncertainty.median_refine": 2})
    live0 = live_from_book(book, facts)
    band = U.band(book, facts, live0, draws=8, workers=1)
    main = str(book.get("meta.company.main_ticker"))
    prices = dict(live0.prices)
    prices[main] = band.medians(band.lam)["central"] - 15.0
    live = LiveInputs(valuation_date=live0.valuation_date, prices=prices, price_dates=dict(live0.price_dates),
                      register=live0.register)
    band = U.band(book, facts, live, draws=8, workers=1)
    anchor = U.MedianAnchor(book, facts, live, band)
    return book, reverse_dcf(book, facts, live, band, anchor=anchor)


def test_reverse_row_in_range_means_the_root_is_in_range(solved):
    """М§11.2: строка, уточнённая на полной полосе, сошлась (невязка в стопе), и «в диапазоне» — о её решении;
    число пересчётов полной полосы — не больше `median_refine`."""
    book, res = solved
    tol = float(book.get("valuation.headline.search_tol_rub"))
    row = res["rows"][0]
    lo, hi = row["range"]
    assert row["status"] == "solved" and row["gap_basis"] == "full" and row["converged"] and abs(row["gap"]) <= tol
    assert row["in_range"] == (lo <= row["solved"] <= hi) and row["in_range"]
    assert res["full_grids"] <= 2 * 8 and "относится к корню" in res["method"]


def test_run_output_prints_in_range_only_for_a_root():
    """Сводка книги: «в диапазоне» — только у строки с корнем в диапазоне; приближение — «вне диапазона» с
    пометкой «уточнение не сошлось»; недостижимая строка — без слов о диапазоне."""
    from model.book_results import book_results
    book, facts = book_and_facts()
    results = dict(book_results(book, facts, slow=False))
    base = {"key": "k", "kind": "shift", "unit": "п.п.", "paths": ["p"], "book": 0.0, "range": [-0.003, 0.005]}
    results["reverse_dcf"] = {"target": 270.0, "bank_rows": [], "rows": [
        {**base, "name": "корень внутри", "solved": 0.0040, "status": "solved", "in_range": True, "converged": True,
         "gap": 0.1},
        {**base, "name": "приближение внутри, корень вне", "solved": 0.0048, "status": "solved", "in_range": False,
         "converged": False, "gap": 5.6},
        {**base, "name": "недостижимо", "solved": None, "status": "unreachable", "in_range": False,
         "converged": False, "gap": 28.9}]}
    text = render_run_output(results)
    lines = {name: next(ln for ln in text.splitlines() if name in ln)
             for name in ("корень внутри", "приближение внутри, корень вне", "недостижимо")}
    assert "(solved, в диапазоне;" in lines["корень внутри"]
    assert "(solved, вне диапазона, уточнение не сошлось;" in lines["приближение внутри, корень вне"]
    assert "(unreachable;" in lines["недостижимо"]


def test_dividend_history_source_is_written_for_the_owner():
    """П§0.2, решение P7: источник истории дивидендов в выпуске — словами для владельца: без имён методов
    брокерского API, рабочих пометок, путей к листам и хвостов sha256; правила ловят то, что печаталось прежде."""
    from tests.support_core2 import fast_release_payload
    _, d = fast_release_payload()
    rows = d["dividends"]["history"]
    assert rows and all(r["src"] for r in rows)
    bad = [(r["year"], U.service_text(r["src"])) for r in rows if U.service_text(r["src"])]
    assert not bad, bad
    old = "T-Invest API: GetDividends SBER (снимок 30.09.2026, без токена)"
    assert {"имя метода API", "рабочая пометка"} <= set(U.service_text(old))
    assert U.service_text("InstrumentsService, метод расписания") == ["имя метода API"]
    for fine in ("брокерский календарь дивидендов (T-Invest, снимок 30.09.2026)", "Getting started", "Сервис котировок",
                 "решения годовых собраний акционеров"):
        assert U.service_text(fine) == [], fine
