"""Волна W3, выпуск: поля П§2 (раскладка сжатия, пол вероятности, строки года, история, окно формы ЦБ), три
исхода сборки — отказ, тревога, плашка (флаги `explanation_expiring`, `policy_expired`), `live.degraded_flag`
по политике тревог, сторож заголовка без записи реестра, нау-каст года на одном слое, чувствительности
оценщиком срединных прогонов, validate единиц и базиса плиток.

Тесты такта: выпуск на фикстуре собирается один раз (`_base`), варианты — заменой дешёвой части (выходы
индикаторов, отчёт о живых входах, объяснения гейтов, факты, которых клетки не читают) и пересборкой
`build_payload`; новая сетка считается только там, где меняется дата оценки."""

from __future__ import annotations

import copy
import dataclasses
import functools
import math
from datetime import timedelta

import pytest

from model import payload as P
from model import uncertainty as U
from model.book import Facts
from model.checks import check_gates, gate_statuses, guidance_values
from model.grid import LiveInputs, bank_language, live_from_book, run_grid, year_mgmt
from model.live import apply_live
from model.nextreport import open_period
from model.timeline import make_timeline
from tests.support_core import fixture_book, fixture_facts
from tests.support_core2 import (CONTROL_FIXTURE, book_prices, explained, fast_release_payload, outputs,
                                 sensitivity_rows)

pytestmark = pytest.mark.tact

DRAWS = 2                      # прогонов полосы в выпусках такта: полоса здесь не проверяется


@pytest.fixture(scope="module")
def built():
    return fast_release_payload()


def _reseal(d: dict) -> dict:
    d["meta"]["payload_sha256"] = P.payload_hash(d)
    d["meta"]["bytes"] = P.compact_bytes(d)
    return d


def _release(day, *, previous=None):
    """Быстрый выпуск на фикстуре на дату `day` с выходами сборщиков (без реестра); гейты объяснены."""
    book, facts = fixture_book(), fixture_facts()
    px = book_prices(book)
    probe = run_grid(book, facts, LiveInputs(valuation_date=day, prices=px, price_dates={t: day for t in px},
                                             register=()))
    out = outputs({t: [(day, px[t])] for t in px}, alarm=[])
    rel = P.make_release(live=True, fast=True, today=day, book=book, facts=facts, outputs=out,
                         explanations=explained(check_gates(probe, today=day), today=day), notes=[], draws=DRAWS,
                         control_model=CONTROL_FIXTURE)
    rel.previous = previous
    return rel, P.build_payload(rel)


@functools.lru_cache(maxsize=None)
def _base():
    """Выпуск на дату оценки книги — основа вариантов: (выпуск, дата)."""
    day = P.to_date(fixture_book().get("meta.valuation_date"))
    return _release(day)[0], day


def _variant(**changes):
    """Вариант основного выпуска: те же сетка и полоса, заменены поля `Release`; → (выпуск, payload)."""
    rel = copy.copy(_base()[0])
    for key, value in changes.items():
        setattr(rel, key, value)
    return rel, P.build_payload(rel)


def _with_indicators(**fields):
    return _variant(indicators=dataclasses.replace(_base()[0].indicators, **fields))


def _with_facts(file: str, content: dict):
    """Факты с подменённым файлом, которого клетки не читают (история упр. метрик, срок политики)."""
    facts = fixture_facts()
    return _variant(facts=Facts(root=facts.root, files={**facts.files, file: content}, digest=facts.digest))


def _flag(d, name):
    return next(f for f in d["checks"]["flags"] if f["name"] == name)


def _ru(day) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


# ------------------------------------------------------------------ поля выпуска


def test_w3_fields_of_the_release(built):
    """Поля волны W3 (PAYLOAD §2): раскладка сжатия φ и кредитная маржа, флаг `phi` у пассивов, пол вероятности,
    U сторожа заголовка, подпись строки моста, флаги сроков, единицы осей — коды."""
    rel, d = built
    tr, t = d["nii"]["transmission"], rel.run.ctx.transmission
    assert tr["phi_split"] == rel.book.get("nii.phi_split")
    assert tr["phi_assets"] == pytest.approx(t.phi_assets, abs=1e-6) and tr["phi_liab"] == pytest.approx(t.phi_liab, abs=1e-6)
    assert tr["phi"] == pytest.approx(t.phi, abs=1e-6) and tr["phi_assets"] == pytest.approx(tr["phi_split"] * tr["phi"], abs=2e-6)
    assert set(tr["loan_margin"]) == set(d["worlds"]["order"])
    assert tr["loan_margin"] == pytest.approx({w: v for w, v in t.loan_margin.items()}, abs=1e-6)
    books = rel.book.get("nii.books")
    Q = rel.run.ctx.timeline.Q
    for b in d["nii"]["books"]:
        spec = books[b["key"]]
        assert b["phi"] == bool(spec.get("phi"))
        if spec["side"] == "liability":                    # у пассива с phi — добавка φ_L × X_W к стоимости (М§4.4)
            add = t.phi_liab if spec.get("phi") else 0.0
            for w in d["worlds"]["order"]:
                want = rel.run.ctx.prep.spreads[b["key"]][Q] + add * t.x_lt[w]
                assert b["spread_lt_world"][w] == pytest.approx(want, abs=1e-6), (b["key"], w)
    assert any(b["phi"] and b["side"] == "liability" for b in d["nii"]["books"])
    assert d["regimes"]["update"]["floor_share"] == rel.book.get("joint.regime_update.floor_share")
    jg = d["fair_value"]["jump_guard"]
    assert jg["unregistered_dividend"] == 0.0 and jg["unregistered_dps"] == 0.0
    v0 = next(r for r in d["layers"]["headline_mix"]["waterfall"] if r["key"] == "v0")
    assert v0["title"] == "Оценка капитала V0"                       # «стоимость капитала» — только ставка (М§2)
    assert not any("Стоимость капитала" in r["title"] for r in d["layers"]["headline_mix"]["waterfall"])
    names = [f["name"] for f in d["checks"]["flags"]]
    assert {"explanation_expiring", "policy_expired"} <= set(names) and names == list(P.FLAG_TITLES)
    assert {r["unit"] for r in d["judgements"]["rows"]} <= set(P.UNIT_CODES)
    for r in d["judgements"]["rows"]:                                # единица — полем оси или путём, не величиной
        parts = r["paths"][0].split(".")
        if r["paths"][0] in ("oci.fvoci_maturity", "oci.fvoci_duration"):
            assert r["unit"] == "years"
        if r["paths"][0] == "credit.kappa":
            assert r["unit"] == "number"
        if r["kind"] == "shift":
            assert r["unit"] == "pp"
        if "unit" not in next(a for a in rel.book.get("valuation.uncertainty.axes") if a["paths"] == r["paths"]) \
                and (any(p.endswith("_pp") for p in parts) or "nim_shift" in parts):
            assert r["unit"] == "pp"
    assert "sha256" not in str(d["guidance"]["src"])                 # хвост хэша — только в карточке «Выпуск и книга»
    assert all("%" not in s["title"].replace(" %", "") for s in d["dividends"]["ladder"])    # «50 %», не «50%»


def test_annual_rows_add_up_to_the_pretax_profit(built):
    """№ 83 (П§2 paths, П§7 п. 11): pbt = nii − llp − fvc + fees + insurance + other + noncore − opex + one_off;
    FVC и разовые статьи — своими строками, переоценка облигаций по СС — внутри «прочего»."""
    rel, d = built
    run = rel.run
    tl = run.ctx.timeline
    lam = run.lam
    p_mix = {c.key: lam * run.layers["analytical"].prob[c.key] + (1 - lam) * run.layers["macro_neutral"].prob[c.key]
             for c in run.cells}
    seen_fvc = seen_one_off = 0
    for row in d["paths"]["annual"]:
        total = sum(sign * row[k] for k, sign in P.PBT_ROWS)
        assert row["pbt"] == pytest.approx(total, abs=0.06), row["year"]
        qs = [q for q in tl.quarters_of_year(row["year"]) if 1 <= q <= tl.Q]
        for key in ("fvc", "one_off"):
            want = sum(p_mix[c.key] * c.quarters[key][q] for c in run.cells for q in qs)
            assert row[key] == pytest.approx(want, abs=0.006), (row["year"], key)
        seen_fvc += abs(row["fvc"]) > 0.01
        seen_one_off += abs(row["one_off"]) > 0.01
    assert seen_fvc > 0 and seen_one_off == 1                        # разовый убыток кризиса — в год шока
    shock = run.ctx.prep.regimes["crisis"].shock_year
    assert next(r for r in d["paths"]["annual"] if r["year"] == shock)["one_off"] < 0
    broken = copy.deepcopy(d)
    broken["paths"]["annual"][1]["fvc"] += 10.0
    assert any("сумме строк года" in p for p in P.validate(_reseal(broken)))


def test_anchor_year_mgmt_metrics_use_the_reported_quarters(built):
    """№ 79 (П§2 paths.annual, guidance.items): упр. ЧПМ, CoR и CIR года якоря — отчётные кварталы раскрытым упр.
    фактом, прогнозные — мостом; `model_year` строки гайденса и `mass_outside` — тем же правилом."""
    rel, d = built
    run = rel.run
    ctx, tl, br = run.ctx, run.ctx.timeline, run.ctx.bridge
    lam = run.lam
    p_mix = {c.key: lam * run.layers["analytical"].prob[c.key] + (1 - lam) * run.layers["macro_neutral"].prob[c.key]
             for c in run.cells}
    mix = lambda name, q: sum(p_mix[c.key] * c.quarters[name][q] for c in run.cells)        # noqa: E731
    ay = tl.anchor_year
    row = next(r for r in d["paths"]["annual"] if r["year"] == ay)
    assert row["fact_quarters"] > 0
    for key in ("nim", "cor", "cir"):
        want = year_mgmt(ctx, key, ay, row[key], mix)
        assert row[f"{key}_mgmt"] == pytest.approx(want, abs=2e-6), key
    to_mgmt = {"nim": br.to_mgmt_nim, "cor": br.to_mgmt_cor, "cir": br.to_mgmt_cir}
    assert row["cor_mgmt"] != pytest.approx(to_mgmt["cor"](row["cor"]), abs=1e-5)           # не средний мост
    later = next(r for r in d["paths"]["annual"] if r["fact_quarters"] == 0)
    for key in ("nim", "cor", "cir"):
        assert later[f"{key}_mgmt"] == pytest.approx(to_mgmt[key](later[key]), abs=2e-6)
    gy = d["guidance"]["year"]
    grow = next(r for r in d["paths"]["annual"] if r["year"] == gy)
    items = {i["key"]: i for i in d["guidance"]["items"]}
    assert items["cor_max"]["model_year"] == grow["cor_mgmt"] and items["nim"]["model_year"] == grow["nim_mgmt"]
    assert items["cir"]["model_year"] == grow["cir_mgmt"]
    gate = next(f for f in rel.findings if f.name == "guidance_gap")
    bands = gate.detail["bands"]
    prob = run.layers["analytical"].prob
    values = {c.key: guidance_values(run, c, gy) for c in run.cells}
    for key in ("roe", "nim", "cor_max", "cir"):
        lo, hi = bands[key]
        mass = sum(prob[k] for k, v in values.items()
                   if not ((lo is None or v[key] >= lo) and (hi is None or v[key] <= hi)))
        assert items[key]["mass_outside"] == pytest.approx(mass, abs=1e-6), key


# ------------------------------------------------------------------ история


def test_history_takes_annual_mgmt_metrics_from_the_annual_node():
    """№ 84 (П§2 history): годовые упр. метрики — раскрытые годовые значения; без годового узла — среднее
    четырёх раскрытых кварталов, при неполном годе — null; провалы раскрытия — по каждому базису отдельно."""
    facts = fixture_facts()
    periods = sorted(facts.periods("pnl_quarterly"))
    anchor = str(facts.plain("anchor", "period"))
    first_year = int(periods[0][:4])
    node = lambda v: {"v": v, "src": "тест"}                                                # noqa: E731
    quarters = {}
    y0, y1, y2 = first_year - 2, first_year - 1, first_year          # два года до МСФО фикстуры и первый год с ней
    for y, vals in ((y0, [0.05, 0.051, 0.052, None]), (y1, [None, None, None, None]), (y2, [0.058, 0.059, 0.06, 0.061])):
        for q, v in enumerate(vals, start=1):
            quarters[f"{y}Q{q}"] = {"nim": node(v), "cor": node(None if v is None else v / 5), "cir": node(None),
                                    "roe": node(None)}
    mgmt = {"basis": "mgmt", "quarters": quarters,
            "annual": {str(y0): {"nim": node(0.0538), "cor": node(0.006), "cir": node(0.34)},
                       str(y1): {"nim": node(0.0532), "cor": node(0.019), "cir": node(0.382)}}}
    _, d = _with_facts("mgmt_quarterly", mgmt)
    annual = {r["year"]: r for r in d["history"]["annual"]}
    assert annual[y0]["mgmt"] == {"nim": 0.0538, "cor": 0.006, "cir": 0.34}         # годовой узел, не среднее трёх
    assert annual[y1]["mgmt"] == {"nim": 0.0532, "cor": 0.019, "cir": 0.382}        # год без кварталов — из узла
    assert annual[y2]["mgmt"]["nim"] == pytest.approx((0.058 + 0.059 + 0.06 + 0.061) / 4, abs=1e-6)   # четыре квартала
    assert annual[y2]["mgmt"]["cir"] is None                                         # нет ни узла, ни кварталов
    assert int(anchor[:4]) not in annual                                             # неполный год годом не называется
    gaps = d["history"]["gaps"]
    assert {g["basis"] for g in gaps} == {"ifrs", "mgmt"}
    ifrs = [g for g in gaps if g["basis"] == "ifrs"]
    assert [g["period"] for g in ifrs] == [f"{y0}Q1–{y1}Q4"]                         # потоков МСФО нет два года подряд
    assert all("МСФО" in g["reason"] for g in ifrs)
    mg = [g["period"] for g in gaps if g["basis"] == "mgmt"]                         # и там, где МСФО есть
    assert mg == [f"{y0}Q4–{y1}Q4", f"{y2 + 1}Q1–{anchor}"]
    assert P.validate(d) == [], P.validate(d)[:5]
    # validate: диапазоны одного базиса не пересекаются, базис — ifrs или mgmt
    for change, text in ((lambda g: g.append({"period": f"{y0}Q3–{y1}Q1", "basis": "ifrs", "reason": "x"}), "пересекаются"),
                         (lambda g: g[0].__setitem__("basis", "ras"), "basis"),
                         (lambda g: g[0].__setitem__("period", str(y0)), "не квартал")):
        bad = copy.deepcopy(d)
        change(bad["history"]["gaps"])
        assert any(text in p for p in P.validate(_reseal(bad))), text
    lost = copy.deepcopy(d)
    del lost["history"]["gaps"][0]["basis"]
    assert P.validate(_reseal(lost)) != []


def test_history_annual_metrics_equal_the_annual_nodes_of_the_facts(built):
    """№ 84 на данных фактов (без литералов годов): у каждого года с раскрытым годовым узлом упр. метрика выпуска
    равна узлу; диапазоны провалов одного базиса не пересекаются, и в провале значений базиса действительно нет."""
    rel, d = built
    if "mgmt_quarterly" not in rel.facts.files:
        pytest.skip("в фактах нет mgmt_quarterly.json (фикстура ядра)")
    nodes = rel.facts.file("mgmt_quarterly").get("annual") or {}
    seen = 0
    for row in d["history"]["annual"]:
        for key in ("nim", "cor", "cir"):
            node = (nodes.get(str(row["year"])) or {}).get(key)
            if isinstance(node, dict) and node.get("v") is not None:
                assert row["mgmt"][key] == pytest.approx(float(node["v"]), abs=1e-6), (row["year"], key)
                seen += 1
    assert seen > 0
    periods = [q["period"] for q in d["history"]["quarters"]]
    quarters = {q["period"]: q for q in d["history"]["quarters"]}
    assert P._gap_problems(d["history"]["gaps"]) == []
    for g in d["history"]["gaps"]:
        lo, _, hi = g["period"].partition("–")
        assert lo in periods and (hi or lo) in periods and g["basis"] in ("ifrs", "mgmt")
        for p in periods[periods.index(lo):periods.index(hi or lo) + 1]:
            block = quarters[p][g["basis"]]
            keys = P.IFRS_FLOWS if g["basis"] == "ifrs" else tuple(block)
            assert all(block[k] is None for k in keys), (p, g["basis"])


# ------------------------------------------------------------------ В17: отказ, тревога, плашка


def test_expiring_explanation_is_a_flag_not_an_alarm_and_expired_is_a_refusal():
    """М§14.2–§14.3: объяснение сработавшего гейта истекает в ближайшие 30 дней — плашка `explanation_expiring`,
    тревог нет (код сборки 0); истёкшее объяснение или его отсутствие — отказ сборки (код 1)."""
    rel, day = _base()
    fired = [f for f in rel.findings if f.kind == "gate" and f.fired]
    assert len(fired) >= 2

    def with_explanations(expl):
        return _variant(explanations=expl, gates=gate_statuses(rel.findings, expl, today=day))

    quiet = None
    for days, expiring in ((30, True), (31, False), (0, True)):                       # срок действует включительно
        expl = explained(fired, today=day)
        for spec in expl.values():
            spec["valid_until"] = day + timedelta(days=days)
        r2, d = with_explanations(expl)
        flag = _flag(d, "explanation_expiring")
        assert flag["raised"] is expiring, days
        assert all(g["expiring"] is expiring for g in d["checks"]["gates"] if g["fired"])
        assert P.validate(d) == [] and P.alerts(r2, d) == []                          # выпуск годен, тревог нет
        if expiring:
            assert _ru(day + timedelta(days=days)) in flag["detail"]                  # дата — с годом
            assert all(P.GATE_TITLES[f.name] in flag["detail"] for f in fired)
            assert not U.service_text(flag["detail"])
        else:
            quiet = d
    expl = explained(fired, today=day)
    first = next(iter(expl))
    expl[first]["valid_until"] = day - timedelta(days=1)                              # срок истёк вчера
    del expl[next(k for k in expl if k != first)]                                     # а у другого гейта записи нет
    _, d = with_explanations(expl)
    status = {g["name"]: g["status"] for g in d["checks"]["gates"] if g["fired"]}
    assert status[first] == "expired" and "unexplained" in status.values()
    problems = P.validate(d)                                                          # отказ сборки
    assert any("expired" in p for p in problems) and any("unexplained" in p for p in problems)
    assert not _flag(d, "explanation_expiring")["raised"]                             # истёкшее — уже не «истекает»
    bad = copy.deepcopy(quiet)                                                        # флаг согласован с гейтами
    _flag(bad, "explanation_expiring")["raised"] = True
    assert any("explanation_expiring" in p for p in P.validate(_reseal(bad)))


def test_expired_issuer_document_is_a_flag_and_the_release_goes_out():
    """М§5.1, §14.3: срок дивидендной политики по документу раньше даты оценки — плашка `policy_expired`; клетки
    платят по политике книги, выпуск годен, тревог нет; срока нет (`null`) — флаг не поднимается."""
    rel, day = _base()
    file = fixture_facts().file("dividends")

    def with_until(until):
        policy = {**file["policy"], "valid_until": None if until is None else until.isoformat()}
        return _with_facts("dividends", {**file, "policy": policy})

    _, d0 = _variant()
    assert not _flag(d0, "policy_expired")["raised"]                                  # срок фикстуры — впереди
    _, on = with_until(day)                                                           # в последний день срока — действует
    assert not _flag(on, "policy_expired")["raised"]
    r2, after = with_until(day - timedelta(days=1))
    flag = _flag(after, "policy_expired")
    assert flag["raised"] and _ru(day - timedelta(days=1)) in flag["detail"] and not U.service_text(flag["detail"])
    assert P.validate(after) == [] and P.alerts(r2, after) == []                      # не отказ и не тревога
    assert after["dividends"]["policy"]["valid_until"] == (day - timedelta(days=1)).isoformat()
    assert after["dividends"]["model"] == on["dividends"]["model"]                    # клетки платят по прежней политике
    assert after["fair_value"]["central"] == on["fair_value"]["central"]
    _, none = with_until(None)
    assert not _flag(none, "policy_expired")["raised"]


def test_alerts_are_the_live_degradation_the_register_flag_and_the_size(built):
    """INTERFACES §4.6: тревоги выпуска — деградация живых входов, флаг `dividend_register`, размер; истекающие
    объяснения в тревоги не входят."""
    rel, d = built
    assert P.alerts(rel, d) == []
    d2 = copy.deepcopy(d)
    for g in d2["checks"]["gates"]:
        if g["fired"]:
            g["expiring"] = True
    assert P.alerts(rel, d2) == []                                                      # плашка, а не тревога
    d3 = copy.deepcopy(d)
    d3["meta"]["bytes"] = P.WARN_BYTES
    assert any("байт" in a for a in P.alerts(rel, d3))
    d4 = copy.deepcopy(d)
    d4["live"].update(degraded_flag=True, degraded=["цена: не принята"])
    assert any("деградация" in a for a in P.alerts(rel, d4))


# ------------------------------------------------------------------ № 89: live.degraded_flag по политике тревог


def test_degraded_flag_follows_the_alarm_policy():
    """INTERFACES §6 (В9, В20): причина деградации восполнимого источника плиток моложе трёх тактов печатается, но
    тревоги не поднимает; тревога — деградация живого входа оценки или источник из `collector.alarm`."""
    book, facts = fixture_book(), fixture_facts()
    day = P.to_date(book.get("meta.valuation_date"))
    px = book_prices(book)
    prices = {t: [(day, px[t])] for t in px}
    why = "ставки вкладов ЦБ: источник не ответил (503)"
    _, young = apply_live(book, facts, outputs(prices, degraded=[why], alarm=[]), today=day)
    assert young.degraded == [why] and young.alarms == [] and young.degraded_flag is False       # первый такт
    _, third = apply_live(book, facts, outputs(prices, degraded=[why], alarm=["cbr"]), today=day)
    assert third.degraded == [why] and third.degraded_flag and len(third.alarms) == 1                   # третий такт
    assert "Банк России" in third.alarms[0] or "cbr" in third.alarms[0]       # сборщик — словами (П§0.2)
    _, old = apply_live(book, facts, outputs(prices, degraded=[why]), today=day)
    assert old.degraded_flag and old.alarms == [why]                   # отчёт без списка alarm: тревожна каждая причина
    _, clean = apply_live(book, facts, outputs(prices, alarm=[]), today=day)
    assert not clean.degraded and not clean.degraded_flag
    # живой вход оценки — тревога сразу, каким бы ни был счётчик сборщиков
    main = str(book.get("meta.company.main_ticker"))
    previous = {"prices": {t: {"value": px[t], "date": (day - timedelta(days=1)).isoformat(), "time": "18:40",
                               "source": "tinvest"} for t in px}}
    jump = {t: [(day, px[t] * (2.0 if t == main else 1.0))] for t in px}
    _, fb = apply_live(book, facts, outputs(jump, alarm=[]), today=day, previous=previous)
    assert fb.prices[main]["status"] == "fallback" and fb.degraded_flag and any(main in a for a in fb.alarms)
    assert "100 %" in fb.prices[main]["reason"] and "100%" not in fb.prices[main]["reason"]      # «30 %», не «30%»
    w = str(book.get("joint.macro_neutral_world"))
    nodes = {k: 100 * float(book.get(f"worlds.{w}.zero_curve.{k}")) for k in ("1", "3", "5", "10")}
    _, curve = apply_live(book, facts, outputs(prices, alarm=[], curve={"as_of": day.isoformat(), "nodes": nodes}),
                          today=day)
    assert curve.curve is None and curve.degraded_flag and any("кривая" in a for a in curve.alarms)
    _, key = apply_live(book, facts, outputs(prices, alarm=[], key_rate={"value": 17.0, "date": day.isoformat()}),
                        today=day)
    assert key.degraded_flag and any("ключевая" in a for a in key.alarms)
    bad_rec = {"year": 2026, "dps": 40.0, "status": "declared", "record_date": "2027-07-19", "ex_date": "2027-07-19",
               "last_buy_date": "2027-07-16", "pay_date": "2027-08-02", "sources": ["tinvest"]}       # один источник
    _, reg = apply_live(book, facts, outputs(prices, alarm=[], register=[bad_rec]), today=day)
    assert reg.degraded_flag and any("реестр" in a for a in reg.alarms)


def test_release_code_follows_the_alarm_policy():
    """Деградация восполнимого источника на первом такте — причина в `live.degraded`, тревоги нет (код сборки 0);
    на третьем — источник в `collector.alarm`, тревога (код 3)."""
    rel, day = _base()
    book, facts = rel.book, rel.facts
    px = book_prices(book)
    prices = {t: [(day, px[t])] for t in px}
    why = "ставки вкладов ЦБ: источник не ответил (503)"
    for alarm, code3 in (([], False), (["cbr_deposit_rates"], True)):
        out = outputs(prices, degraded=[why], alarm=alarm)
        live, report = apply_live(book, facts, out, today=day)
        assert live == rel.live                                       # живые входы оценки те же — сетка не меняется
        r2, d = _variant(indicators=out, live_report=report)
        assert d["live"]["degraded"] == [why] and d["live"]["degraded_flag"] is code3
        alerts = P.alerts(r2, d)
        assert P.validate(d) == [] and bool(alerts) is code3
        if code3:
            assert len(alerts) == 1 and "cbr_deposit_rates" in alerts[0]


# ------------------------------------------------------------------ № 87: сторож заголовка без записи реестра


def test_jump_guard_is_silent_across_the_agm_quarter_end_without_a_register_record():
    """М§14.4: без записи реестра клетки на E_qA вычитают дивиденд модели сами; сторож сравнивает V0 + B + U
    (U — этот дивиденд) и медиану с u на акцию — и молчит на цепочке E_qA − 1 → E_qA → E_qA + 30 дней; об
    отсутствующей записи сообщает флаг `dividend_register`, а не сторож."""
    book = fixture_book()
    tl = make_timeline(book)
    agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
    end = tl.end(agm_q)
    days = [end - timedelta(days=1), end, end + timedelta(days=30)]
    prev, chain = None, []
    for day in days:
        rel, d = _release(day, previous=prev)
        chain.append((rel, d))
        prev = d
    limit = float(book.get("valuation.headline.jump_guard.v0_pct"))
    u = [d["fair_value"]["jump_guard"]["unregistered_dividend"] for _, d in chain]
    u_ps = [d["fair_value"]["jump_guard"]["unregistered_dps"] for _, d in chain]
    assert u[0] == 0.0 and u[1] > 0 and u[2] == pytest.approx(u[1], rel=0.02) and u_ps[1] > 0
    v0 = [d["layers"]["analytical"]["v0"] for _, d in chain]
    drop = v0[1] / v0[0] - 1
    assert drop == pytest.approx(-u[1] / v0[0], abs=2e-3) and abs(drop) > 0.6 * limit       # один V0 упал бы за порог
    for i, roll in ((1, 0.002), (2, 0.02)):                # за день — перекат дня, за 30 дней — перекат месяца
        jg = chain[i][1]["fair_value"]["jump_guard"]
        assert jg["reason"] == "within_limit", (days[i], jg)
        assert abs(jg["v0_change"]) < roll and abs(jg["median_change"]) < roll + 0.01
        inv = next(x for x in chain[i][1]["checks"]["invariants"] if x["name"] == "jump_guard")
        assert inv["ok"]
    med = [d["fair_value"]["headline"]["median"] for _, d in chain]
    assert med[1] + u_ps[1] == pytest.approx(med[0], rel=0.02)
    # об отсутствующей записи сообщает флаг реестра (тревога) — после конца квартала ГОСА
    assert _flag(chain[2][1], "dividend_register")["raised"] and not _flag(chain[0][1], "dividend_register")["raised"]
    assert any("реестра" in a for a in P.alerts(*chain[2]))
    # проверка, что тест видит именно поправку U: «прошлый U», завышенный на дивиденд, даёт скачок за порогом
    bare = copy.copy(chain[1][0])
    stale = copy.deepcopy(chain[0][1])
    stale["fair_value"]["jump_guard"]["unregistered_dividend"] = u[1]
    stale["fair_value"]["jump_guard"]["unregistered_dps"] = u_ps[1]
    bare.previous = stale
    jg = P.build_payload(bare)["fair_value"]["jump_guard"]
    assert abs(jg["v0_change"]) > 0.6 * limit


# ------------------------------------------------------------------ № 82: нау-каст года


def _nowcast(period, expectation, *, forecast_shift, rel_se, sigma_base, with_sigma=True):
    """Запись нау-каста открытого квартала: оценка через мост = ожидание × (1 + 2 × сдвиг), вес 0,5."""
    target = {"ras_estimate": 1.0, "bridge": 1.0, "ras_bridged": expectation * (1 + 2 * forecast_shift), "w": 0.5,
              "rel_std_error": rel_se, "sigma_ras": rel_se, "basis": "ifrs", "equation": "тест"}
    if with_sigma:
        target["sigma_base"] = sigma_base
    return {"period": period, "ras": {"period": period, "targets": {"ni_q": target}, "months": [], "months_known": 2,
                                      "sigmas": {"base": sigma_base} if with_sigma else {}, "version": "тест"}}


def test_nowcast_year_sits_on_one_layer_and_carries_the_error_of_the_rest():
    """П§2 nowcast.year: без нау-каста `dps` = `dps_model`; квартал в сумме года = квартал смеси + отклонение
    нау-каста от своего ожидания; ошибка года — открытый квартал и оставшиеся кварталы (σ_база из входов
    нау-каста); оставшиеся есть, а σ_база нет — `ni_year_se` и `dps_interval` — null."""
    rel, _ = _base()
    book, facts = rel.book, rel.facts
    _, plain = _variant()
    y = plain["nowcast"]["year"]
    run = rel.run
    tl = run.ctx.timeline
    lam = run.lam
    p_mix = {c.key: lam * run.layers["analytical"].prob[c.key] + (1 - lam) * run.layers["macro_neutral"].prob[c.key]
             for c in run.cells}
    period = plain["nowcast"]["quarter"]["period"]
    assert period == open_period(book, facts)
    q = tl.index(period)
    mix_q = sum(p_mix[c.key] * c.quarters["ni_sh"][q] for c in run.cells)
    rest = [x for x in tl.quarters_of_year(y["year"]) if q < x <= tl.Q]
    assert rest                                                        # в году остаются кварталы после открытого
    rest_ni = [sum(p_mix[c.key] * c.quarters["ni_sh"][x] for c in run.cells) for x in rest]
    assert y["ni_quarter"] == pytest.approx(mix_q, abs=0.006) and y["dps"] == y["dps_model"]     # без нау-каста
    assert y["ni_year"] == pytest.approx(y["ni_fact"] + mix_q + sum(rest_ni), abs=0.02)
    assert y["ni_year_se"] is None and y["dps_interval"] is None
    expectation = plain["next_report"]["expectation"]["ni"]            # якорь нау-каста — слой «свой взгляд»
    assert abs(expectation - mix_q) > 0.01                             # два слоя различаются — поэтому и правка
    shift, rel_se, sigma = 0.04, 0.03, 0.06
    r2, d2 = _with_indicators(nowcast=_nowcast(period, expectation, forecast_shift=shift, rel_se=rel_se,
                                               sigma_base=sigma))
    t1 = d2["nowcast"]["quarter"]["by_target"]["ni_q"]
    assert t1["forecast"] == pytest.approx(expectation * (1 + shift), rel=1e-6)
    assert t1["deviation"] == pytest.approx(expectation * shift, rel=1e-6)
    y2 = d2["nowcast"]["year"]
    assert y2["ni_quarter"] == pytest.approx(mix_q + expectation * shift, abs=0.006)             # смесь + отклонение
    assert y2["dps"] > y["dps"] and y2["dps_model"] == y["dps_model"]
    se_q = t1["std_error"]
    want = math.sqrt(se_q ** 2 + sum((sigma * abs(v)) ** 2 for v in rest_ni))
    assert y2["ni_year_se"] == pytest.approx(want, abs=0.006) and y2["ni_year_se"] > se_q        # шире квартального
    n_iss = r2.run.ctx.prep.af.n_iss
    width = y2["payout"] * want * 1000 / n_iss
    assert y2["dps_interval"] == pytest.approx([y2["dps"] - width, y2["dps"] + width], abs=2e-4)
    assert y2["dps_interval"][1] - y2["dps_interval"][0] > 2 * y2["payout"] * se_q * 1000 / n_iss
    _, d3 = _with_indicators(nowcast=_nowcast(period, expectation, forecast_shift=shift, rel_se=rel_se,
                                              sigma_base=sigma, with_sigma=False))
    y3 = d3["nowcast"]["year"]
    assert y3["ni_year_se"] is None and y3["dps_interval"] is None and y3["dps"] == y2["dps"]   # нет σ_база — null
    for d in (plain, d2, d3):
        assert P.validate(d) == [], P.validate(d)[:3]


# ------------------------------------------------------------------ № 100, № 98: окно формы ЦБ, плитки


def test_form102_event_is_a_window_and_tiles_carry_unit_and_basis_codes():
    """П§2 calendar: дата формы 0409102 — оценка, печатается окном (`precision: window`, `earliest`, `latest`,
    `confirmed: false`), `next_ras.form102_latest_est` — верхний край; П§0.2: единицы и базис плиток — коды."""
    _, day = _base()
    early, late = day + timedelta(days=23), day + timedelta(days=25)
    month = f"{day.year}M{day.month:02d}"
    sched = {"month": month, "release_date": (day + timedelta(days=8)).isoformat(), "release_confirmed": True,
             "form102_date_est": early.isoformat(), "form102_latest_est": late.isoformat(), "enters_via": "release",
             "date": (day + timedelta(days=8)).isoformat()}
    blank = {"change": None, "change_from": None, "min": None, "max": None, "history": {"date": [], "value": []},
             "status": "ok"}
    tiles = [{"id": "ras_ni_m", "group": "ras", "title": "Чистая прибыль банка за месяц", "unit": "bn", "basis": "ras",
              "value": 169.0, "date": month, "source": "релиз банка", **blank},
             {"id": "f135_n1_0", "group": "capital", "title": "Н1.0 банка", "unit": "share", "basis": "regulatory",
              "value": 0.131, "date": month, "source": "форма 0409135", **blank},
             {"id": "price_main", "group": "market", "title": "Цена акции", "unit": "price", "basis": "market",
              "value": 273.0, "date": day.isoformat(), "source": "Мосбиржа", **blank}]
    _, d = _with_indicators(ras_schedule=sched, tiles=tuple(tiles))
    assert P.validate(d) == [], P.validate(d)[:5]
    ev = next(e for e in d["calendar"]["events"] if e["kind"] == "form102")
    assert (ev["precision"], ev["earliest"], ev["latest"], ev["confirmed"]) == ("window", early.isoformat(),
                                                                                late.isoformat(), False)
    assert ev["date"] == early.isoformat() and ev["covers"] == month
    assert d["calendar"]["next_ras"]["form102_date_est"] == early.isoformat()
    assert d["calendar"]["next_ras"]["form102_latest_est"] == late.isoformat()
    _, single = _with_indicators(ras_schedule={**sched, "form102_latest_est": None})                # верхнего края нет
    ev1 = next(e for e in single["calendar"]["events"] if e["kind"] == "form102")
    assert ev1["precision"] == "window" and ev1["latest"] == ev1["earliest"] == early.isoformat()
    breaks = {
        "единица плитки словами": lambda x: x["indicators"]["tiles"][0].__setitem__("unit", "RUB bn"),
        "базис плитки словами": lambda x: x["indicators"]["tiles"][1].__setitem__("basis", "РСБУ банка"),
        "базис плитки вне кодов": lambda x: x["indicators"]["tiles"][2].__setitem__("basis", "exchange"),
        "дата формы — точной": lambda x: next(e for e in x["calendar"]["events"] if e["kind"] == "form102").update(
            precision="day"),
        "форма подтверждена": lambda x: next(e for e in x["calendar"]["events"] if e["kind"] == "form102").update(
            confirmed=True),
        "единица сверки вне кодов": lambda x: x["checks"]["control_model"]["rows"][0].__setitem__("unit", "млрд"),
    }
    for name, fn in breaks.items():
        bad = copy.deepcopy(d)
        fn(bad)
        assert P.validate(_reseal(bad)) != [], name
    assert "market" in P.BASIS_CODES and {"bn", "price", "level", "share"} <= set(P.UNIT_CODES)


# ------------------------------------------------------------------ № 81: чувствительности — оценщиком


def test_bank_language_sensitivities_are_the_estimator_of_the_middle_draws():
    """М§8.3, §10: строки «₽ за 0,1 п.п. CoR / ЧПМ» и «₽ за 1 п.п. ROE» — оценщик срединных прогонов (среднее
    попарных сдвигов прогонов с рангом в окне), а не разность двух медиан; сдвиг уровня CoR доходит до кредитов
    по СС."""
    book, facts = fixture_book(), fixture_facts()
    live = live_from_book(book, facts)
    x, rows = sensitivity_rows(book, facts, live, 6, workers=1)
    rel, run, band = x.rel, x.run, x.band
    window = U.rank_window(rel.book)
    s = rel.sensitivities
    assert len(rows) == P.LAMBDA_STEPS + 1
    for lam, at in zip(P._lambda_grid(run.lam), rows):
        base = band.exact_centres(lam)
        for kind in ("cor", "nim"):
            shift = U.median_shift(base, s[kind]["band"].exact_centres(lam), window)
            assert at[f"rub_per_01pp_{kind}"] == pytest.approx(shift * 0.001 / s[kind]["step"], abs=0.006)
        assert at["rub_per_01pp_cor"] < 0 < at["rub_per_01pp_nim"]
        d_roe = bank_language(s["nim"]["run"], lam)["roe_tc"] - bank_language(run, lam)["roe_tc"]
        shift = U.median_shift(base, s["nim"]["band"].exact_centres(lam), window)
        assert at["rub_per_1pp_roe"] == pytest.approx(shift / d_roe * s["roe_pp"], abs=0.006)
    # опора FVC стоит на значениях книги (М§4.6): на сдвинутой книге FVC клеток больше
    assert s["cor"]["run"].ctx.book.base is rel.book.base and s["cor"]["band"].s == band.s     # те же точки гиперкуба
    tl = run.ctx.timeline
    a, b = run.cells[0], s["cor"]["run"].cells[0]
    assert b.quarters["fvc"][tl.Q] > a.quarters["fvc"][tl.Q]


# ------------------------------------------------------------------ тексты выпуска — словами (П§0.2)


PRINTED = ("title", "name", "note", "message", "detail", "source", "src", "equation", "explanation")


def _printed(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in PRINTED and isinstance(v, str):
                yield f"{path}.{k}", v
            else:
                yield from _printed(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _printed(v, f"{path}[{i}]")


# Строки, которые ядро передаёт из фактов и объяснений гейтов как есть: их слова — у потоков facts и book.
FOREIGN_TEXT = ("$.market.peers", "$.dividends.history", "$.checks.control_model", "$.checks.gates",
                "$.calendar", "$.guidance.items", "$.guidance.revisions", "$.governance.components",
                "$.next_report.closing", "$.indicators", "$.nowcast")


def test_core_texts_of_the_release_carry_no_service_text():
    """№ 73, № 97 (П§0.2): строки, которые пишет само ядро (инварианты, флаги, подписи, источники блоков), — без
    e-нотации, меток класса, хвостов sha256, путей к файлам, рабочих пометок и кодов периода."""
    _, d = _variant()
    bad = [(path, text[:90], U.service_text(text)) for path, text in _printed(d)
           if U.service_text(text) and not path.startswith(FOREIGN_TEXT)]
    assert not bad, bad[:8]
    for g in d["checks"]["gates"]:                                   # сообщения гейтов пишет ядро
        assert not U.service_text(g["message"]), g["name"]
    inv = {i["name"]: i for i in d["checks"]["invariants"]}
    assert "·10" in inv["ddm_equals_ri"]["detail"] and "e-" not in inv["ddm_equals_ri"]["detail"]
    assert "Σ" not in inv["governance_sum"]["detail"] and "null" not in inv["book_schema"]["title"]
    assert "DPS" not in inv["exdate_jump"]["detail"] and "." not in inv["exdate_jump"]["detail"].split(":")[1]
    src = d["capital"]["anchor"]["src"]
    assert ".json" not in src and "М§" not in src
    assert _ru(P.to_date(d["capital"]["anchor"]["as_of"])) in src                    # дата — из фактов, с годом
    assert ".json" not in d["meta"]["shares"]["src"] and ".json" not in d["valuation_history"]["source"]
    for row in d["dividends"]["history"] + d["market"]["peers"]["rows"]:             # хвост хэша источника снят
        assert "sha256" not in str(row.get("src"))
