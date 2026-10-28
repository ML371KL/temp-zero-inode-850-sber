"""Выпуск sber-v1 (PAYLOAD): годный быстрый выпуск, validate ловит поломку контракта, хэш и размер."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from model import payload as P
from model import uncertainty as U
from tests.support_core2 import fast_release_payload

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "payload-sample.json"


@pytest.fixture(scope="module")
def built():
    rel, payload = fast_release_payload()
    return rel, payload


def _reseal(d: dict) -> dict:
    d["meta"]["payload_sha256"] = P.payload_hash(d)
    d["meta"]["bytes"] = P.compact_bytes(d)
    return d


def test_fast_release_is_valid_and_complete(built):
    _, d = built
    assert P.validate(d) == [], P.validate(d)[:5]
    assert list(d) == list(P.REQUIRED_TOP_LEVEL) and len(P.REQUIRED_TOP_LEVEL) == 28
    assert d["schema"] == P.SCHEMA and d["meta"]["fast"] is True
    assert P.validate(d, publish=True) != []                        # быструю сборку не публикуют
    assert d["checks"]["invariants_broken"] == 0
    assert [i["name"] for i in d["checks"]["invariants"]] == list(P.INVARIANT_TITLES)
    assert {g["name"] for g in d["checks"]["gates"]} == set(P.GATE_TITLES)
    assert [f["name"] for f in d["checks"]["flags"]] == list(P.FLAG_TITLES)


def test_w1_fields_of_the_release(built):
    """Новые поля W1 (PAYLOAD §2): пары передачи, ближний сдвиг, ввод ε, подписи нормативов, дивиденд
    ближайшего года по политике с риском отмены, CIR в упр. базисе, оси вне полосы."""
    rel, d = built
    tr = d["nii"]["transmission"]
    assert [(p["from"], p["to"]) for p in tr["pairs"]] == [tuple(k.split("_")) for k in rel.run.ctx.transmission.pairs]
    assert tr["pairs_range"] == list(rel.book.get("checks.transmission_pairs"))
    near = d["regimes"]["near_nim_shift"]
    # ряд — до первого квартала с нулём; у ближней калибровки перенесённой книги нуля до конца явного участка нет
    assert near[0]["period"] == rel.book.get("meta.first_period")
    assert near[-1]["value"] == 0.0 or near[-1]["period"] == rel.book.get("meta.last_period")
    assert all(r["value"] != 0.0 for r in near[:-1])
    assert d["dividends"]["policy"]["excess"]["ramp_years"] == rel.book.get("dividends.excess.ramp_years")
    assert {"n1_0", "n1_2"} <= set(d["capital"]["titles"])
    ne = d["dividends"]["next_expected"]
    if ne["status"] == "model":
        assert ne["dps"] == ne["dps_policy"] and 0.0 <= ne["p_cancel"] <= 1.0
    assert d["nowcast"]["year"]["dps_model"] == next(
        (r["dps_policy"] for r in d["dividends"]["model"] if r["year"] == d["nowcast"]["year"]["year"]), None)
    br = rel.run.ctx.bridge
    for row in d["paths"]["annual"]:                         # год без отчётных кварталов — средний мост (М§4.6)
        if row["cir"] is not None and row["fact_quarters"] == 0:
            assert row["cir_mgmt"] == pytest.approx(br.to_mgmt_cir(row["cir"]), abs=2e-6)
    kinds = {i["key"]: i["kind"] for i in d["guidance"]["items"]}
    assert kinds.get("cor_max") == "max"
    off = {tuple(a["paths"]) for a in rel.book.get("valuation.uncertainty.off_band_axes")}
    for r in d["judgements"]["rows"]:
        assert r["in_band"] == (tuple(r["paths"]) not in off)
    names = {g["name"] for g in d["checks"]["gates"]}
    assert {"nim_path_joint", "transmission_pairs", "lt_spread_floor", "off_band_shift"} <= names
    assert "ni_jump" in {f["name"] for f in d["checks"]["flags"]}
    assert P.CODE_DIRS == ("model", "indicators", "ops", "data/assumptions", "data/facts", "data/indicators",
                           "data/checks", "requirements.txt")


def test_dividend_register_flag_is_an_alarm(built):
    rel, d = built
    d2 = copy.deepcopy(d)
    flag = next(f for f in d2["checks"]["flags"] if f["name"] == "dividend_register")
    flag.update(raised=True, detail="дивиденд за год: записи declared нет")
    assert any("реестра" in a for a in P.alerts(rel, d2))
    assert not any("реестра" in a for a in P.alerts(rel, d))


@pytest.mark.docs
def test_required_top_level_matches_payload_doc():
    import re
    text = (ROOT / "docs" / "PAYLOAD.md").read_text(encoding="utf-8")
    part = text[text.index("## 1. Верхние блоки"):text.index("## 2. Поля блоков")]
    assert re.findall(r"^\| `([a-z_]+)` \|", part, re.M) == list(P.REQUIRED_TOP_LEVEL)


def test_web_sample_release_passes_validate():
    """Образец витрины (поток web, `make_sample`) — годный выпуск по тому же контракту."""
    assert P.validate(json.loads(SAMPLE.read_text(encoding="utf-8"))) == []


def test_w2_fields_of_the_release(built):
    """Поля волны W2 (PAYLOAD §2): D_pend в мосте, сторож V0 + B, окно A-P2u и сдвиг года шока, число клеток
    разрыва, источник и сдвиг положения суждений, диагностика осей вне полосы, местный наклон и размах,
    σ0 пассивов и эффективные LT-спреды, строки гайденса сектора, вид допуска сверки, без строк книги."""
    rel, d = built
    assert d["fair_value"]["bridge"]["pending_dividend"] == 0.0
    assert "v0_agm_adjustment" in d["fair_value"]["jump_guard"]
    crisis = next(r["crisis"] for r in d["regimes"]["rows"].values() if "crisis" in r)
    assert crisis["shock_year"] == int(d["meta"]["anchor_period"][:4]) + crisis["shock_year_offset"]
    assert d["regimes"]["update"]["window_obs"] == rel.book.get("joint.regime_update.window_obs")
    gap = d["capital"]["capital_gap"]
    assert isinstance(gap["cells"], int) and isinstance(gap["cell_list"], list) and gap["cells"] >= len(gap["cell_list"])
    assert "sections" not in d["book"] and P.FORBIDDEN_FIELDS["book"] == ("sections",)
    for r in d["judgements"]["rows"]:
        assert r["source"].strip() and "valuation.uncertainty" not in r["source"] and len(r["source"]) <= 120
        assert "mean_shift" in r
    ob = d["judgements"]["off_band_shift"]
    assert ob["limit"] == float(rel.book.get("valuation.headline.print_step")) / 2
    assert ob["axes"] == len(rel.book.get("valuation.uncertainty.off_band_axes"))
    nr = d["next_report"]
    assert set(nr["slope"]) == {"rub_per_01pp_cor", "rub_per_01pp_nim", "at_cor", "at_nim"}
    assert set(nr["reaction"]) == {"cor", "nim"} and set(nr["reaction"]["cor"]) == {"d_median_min", "d_median_max"}
    tr = d["nii"]["transmission"]
    assert tr["sigma0_split"] == rel.book.get("nii.sigma0_split")
    assert tr["sigma0_liab"] == pytest.approx(rel.run.ctx.transmission.sigma0_liab, abs=1e-6)
    floors = rel.book.get("checks.lt_spread_floor")
    for b in d["nii"]["books"]:
        assert set(b["spread_lt_world"]) == set(d["worlds"]["order"])
        assert b["spread_floor"] == floors.get(b["key"])
    items = {i["key"]: i for i in d["guidance"]["items"]}
    assert items["n20_0"]["basis"] == "regulatory" and items["roe"]["scope"] == "group" and items["roe"]["relation"] is None
    for key, i in items.items():
        if i["scope"] == "sector":
            assert i["basis"] == "sector" and i["relation"] in ("in_line", "above")
            if i["relation"] == "above":            # «лучше сектора» с диапазоном сектора не сравнивается
                assert i["status"] == "n/a" and i["mass_outside"] is None
    assert {r["key"] for r in d["guidance"]["revisions"]} <= set(items)
    row = d["checks"]["control_model"]["rows"][0]
    assert {"unit", "diff_rel", "tol_kind"} <= set(row)
    facts_row = next(r for r in d["inputs"]["rows"] if r["key"] == "facts")
    assert facts_row["unit"] == "period" and facts_row["value"] == d["meta"]["anchor_period"]
    assert {"date", "period"} <= set(P.UNIT_CODES) and {"regulatory", "sector"} <= set(P.BASIS_CODES)


def test_mix_ratios_carry_the_gap_and_scenario_deductions(built):
    """№ 13 (П§2 capital): норматив смеси = E[K]/E[RWA] + ожидание аддитивного слагаемого клеток — в mix,
    мосте, годовом и квартальном пути, строке гайденса и условии дивиденда; старт моста — факт якоря."""
    rel, d = built
    lam = d["fair_value"]["own_macro_confidence"]
    cells = d["grid"]["cells"]
    w = [lam * c["p_analytical"] + (1 - lam) * c["p_neutral"] for c in cells]
    cap = d["capital"]
    for i, y in enumerate(cap["years"][:-1]):
        for key in ("n20", "n11"):
            want = sum(wi * c["annual"][key][i] for wi, c in zip(w, cells))
            assert cap["mix"][key][i] == pytest.approx(want, abs=5e-4), (y, key)
            assert next(r for r in d["paths"]["annual"] if r["year"] == y)[f"{key}_end"] == cap["mix"][key][i]
    assert cap["bridge"]["start"] == pytest.approx(cap["anchor"]["n20"], abs=5e-4)
    assert cap["bridge"]["start"] + sum(r["pp"] for r in cap["bridge"]["rows"]) == pytest.approx(cap["bridge"]["end"], abs=1e-9)
    gy = d["guidance"]["year"]
    n20 = next(i for i in d["guidance"]["items"] if i["key"] == "n20_0")
    assert n20["model_year"] == cap["mix"]["n20"][cap["years"].index(gy)]
    x = rel.run
    tl = x.ctx.timeline
    q = tl.index(d["paths"]["quarters"][3]["period"])
    p_mix = {c.key: lam * x.layers["analytical"].prob[c.key] + (1 - lam) * x.layers["macro_neutral"].prob[c.key]
             for c in x.cells}
    k = sum(p_mix[c.key] * c.quarters["k20"][q] for c in x.cells)
    rwa = sum(p_mix[c.key] * c.quarters["rwa"][q] for c in x.cells)
    add = sum(p_mix[c.key] * (c.quarters["n20"][q] - c.quarters["k20"][q] / c.quarters["rwa"][q]) for c in x.cells)
    assert d["paths"]["quarters"][3]["n20"] == pytest.approx(k / rwa + add, abs=1e-6)
    assert add != 0.0                                                 # поправка и вычеты сценария не потеряны
    cond = d["dividends"]["next_expected"]["condition"]
    assert d["nowcast"]["year"]["capital_check"]["n20_expected"] == cond["n20_expected"]


def test_crisis_skip_is_not_a_capital_cut_in_the_release(built):
    """№ 14: год шока — отмена в `p_zero` и `p_cancel`, а `p_cut` и `p_limited` её массы не несут."""
    rel, d = built
    shock = rel.run.ctx.prep.regimes["crisis"].shock_year
    row = next(r for r in d["dividends"]["model"] if r["pay_year"] == shock)
    skipped = sum(rel.run.layers["analytical"].prob[c.key] for c in rel.run.cells
                  if any(x.year == row["year"] and x.source == "crisis_skip" for x in c.decisions))
    cut = sum(rel.run.layers["analytical"].prob[c.key] for c in rel.run.cells
              if any(x.year == row["year"] and x.source == "model" and x.cut for x in c.decisions))
    assert skipped > 0
    assert row["p_cut"] == pytest.approx(cut, abs=1e-6) and row["p_zero"] >= skipped - 1e-6
    ne = d["dividends"]["next_expected"]
    if ne["year"] == row["year"]:
        assert ne["condition"]["p_limited"] == pytest.approx(cut, abs=1e-6) and ne["p_cancel"] >= skipped - 1e-6


PRINTED = ("title", "name", "note", "message", "detail", "source", "equation")
SERVICE_TEXT = (r"\b[a-z]+_[a-z_]+\b", r"\btrue\b|\bfalse\b", r"(?<![A-Za-zА-Яа-я])inf(?![A-Za-zА-Яа-я])",
                r"\d{4}M\d{2}", r"\(-?[\d.]+, -?[\d.]+\)")


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


def test_printed_strings_carry_no_service_text(built):
    """№ 62 (П§0.2): в строках, которые печатает витрина, нет имён полей, true/false, inf, кодов месяца и кортежей."""
    import re
    _, d = built
    bad = [f"{path}: {text[:80]}" for path, text in _printed(d)
           if not re.fullmatch(r"[a-z0-9_]+", text)          # `name` проверки или гейта — ключ, не подпись
           for rx in SERVICE_TEXT if re.search(rx, text)]
    assert not bad, bad[:10]
    from model.timeline import period_words
    assert period_words("2026M09") == "сентябрь 2026" and period_words("2026Q3") == "3 кв. 2026"


def test_headline_is_the_lambda_rule_on_draws(built):
    rel, d = built
    h = d["fair_value"]["headline"]
    assert h["draws"] == len(h["low_draws"]) == rel.band.draws
    grid = [r["lambda"] for r in h["by_lambda"]]
    assert grid == [round(i / P.LAMBDA_STEPS, 10) for i in range(P.LAMBDA_STEPS + 1)]
    book_row = next(r for r in h["by_lambda"] if r["lambda"] == h["own_macro_confidence"])
    assert book_row["median"] == h["median"] and book_row["p_below_market"] == h["p_below_market"]
    assert h["printed_median"] == U.round_half_up(U.quantile(sorted(rel.band.centres(rel.band.lam)), 0.5), h["print_step"])
    fv = d["fair_value"]
    assert fv["central"] == pytest.approx(fv["low"] + fv["own_macro_confidence"] * (fv["high"] - fv["low"]), abs=0.011)


def _break(d, fn):
    d2 = copy.deepcopy(d)
    fn(d2)
    return _reseal(d2)


BREAKS = {
    "нет блока": lambda d: d.pop("nowcast"),
    "нет поля": lambda d: d["fair_value"]["headline"].pop("band80"),
    "нет поля строки": lambda d: d["grid"]["cells"][5].pop("annual"),
    "снятое поле": lambda d: d["fair_value"].__setitem__("ev_gap", 0.0),
    "снятый блок": lambda d: d.__setitem__("headline", {}),
    "цели брокеров": lambda d: d["market"]["brokers"].__setitem__("rows", []),
    "тикер вместо тикеров": lambda d: d["meta"]["company"].__setitem__("ticker", "X"),
    "прогон сдвинут": lambda d: d["fair_value"]["headline"]["low_draws"].__setitem__(0, 1.0),
    "печать не по правилу": lambda d: d["fair_value"]["headline"].__setitem__(
        "printed_median", d["fair_value"]["headline"]["printed_median"] + d["fair_value"]["headline"]["print_step"]),
    "точка не на оси": lambda d: d["fair_value"].__setitem__("central", d["fair_value"]["central"] + 1.0),
    "главный тикер": lambda d: d["meta"]["company"].__setitem__("main_ticker", "NONE"),
    "инвариант": lambda d: d["checks"]["invariants"][0].__setitem__("ok", False),
    "гейт без объяснения": lambda d: d["checks"]["gates"][0].update(fired=True, status="unexplained"),
    # образцы собираются во время теста: в исходнике репозитория нет ни почты, ни адреса, ни пути
    "почта в строке": lambda d: d["book"].__setitem__("worlds_source", "пишите на " + "someone" + "@" + "mail.ru"),
    "путь ФС": lambda d: d["live"]["degraded"].append("файл " + "Z" + ":/data/a.json не прочитан"),
    "IPv4": lambda d: d["live"]["degraded"].append("сервер " + ".".join(["10", "1", "2", "3"]) + " не ответил"),
    "вклады не в сумме 1": lambda d: d["fair_value"]["headline"]["contributions"][0].__setitem__("share", 0.0),
    "водопад": lambda d: d["layers"]["headline_mix"]["waterfall"][0].__setitem__("amount", 0.0),
    "мост норматива": lambda d: d["capital"]["bridge"].__setitem__("end", 0.5),
    # W2 (П§7 пп. 6, 10, 12, 14)
    "мост не с факта якоря": lambda d: [d["capital"]["bridge"].__setitem__(k, d["capital"]["bridge"][k] + 0.01)
                                         for k in ("start", "end")],
    "норматив смеси без поправки": lambda d: d["capital"]["mix"]["n20"].__setitem__(
        2, d["capital"]["mix"]["n20"][2] + 0.005),
    "число клеток разрыва списком": lambda d: d["capital"]["capital_gap"].__setitem__("cells", []),
    "источник суждения — служебный путь": lambda d: d["judgements"]["rows"][0].__setitem__(
        "source", "книга: valuation.uncertainty.axes[0]"),
    "источник суждения пуст": lambda d: d["judgements"]["rows"][1].__setitem__("source", " "),
    "оси вне полосы без строк": lambda d: d["judgements"]["off_band_shift"].__setitem__("axes", 3),
    "строки книги по разделам": lambda d: d["book"].__setitem__("sections", []),
    "единица вне кодов": lambda d: d["inputs"]["rows"][0].__setitem__("unit", "RUB"),
    "базис вне кодов": lambda d: d["guidance"]["items"][0].__setitem__("basis", "banking"),
    "пересмотр без строки": lambda d: d["guidance"]["revisions"].append(
        {"date": "2026-07-29", "key": "loan_growth.corporate", "value": 0.1, "event": "тест"}),
    "inf в сообщении гейта": lambda d: d["checks"]["gates"][0].__setitem__("message", "вне (-inf, 0.014)"),
    "кортеж в сообщении гейта": lambda d: d["checks"]["gates"][0].__setitem__("message", "вне (0.215, 0.225)"),
    "дисконт на ждущем дивиденде": lambda d: d["fair_value"]["bridge"].__setitem__("pending_dividend", 900.0),
    "формула DPS": lambda d: d["dividends"]["formula_check"][0].__setitem__("ok", False),
    "клеток не 36": lambda d: d["grid"]["cells"].pop(),
    "период": lambda d: d["meta"].__setitem__("open_period", "3 кв."),
    # вложенные узлы П§2 (W1/C2): пропажа поля роняет сборку, а не карточку витрины
    "поле вложенного объекта": lambda d: d["dividends"]["next_expected"]["condition"].pop("p_limited"),
    "поле элемента списка": lambda d: d["nii"]["transmission"]["pairs"][0].pop("inside"),
    "поле значения словаря": lambda d: next(iter(d["market"]["prices"].values())).pop("status"),
    "поле узла ключей": lambda d: d["fair_value"]["bank_first_line"]["layers"]["analytical"].pop("k_tc"),
    "главная цифра дивиденда": lambda d: d["dividends"]["next_expected"].__setitem__(
        "dps", (d["dividends"]["next_expected"]["dps_policy"] or 0.0) + 1.0),
}


@pytest.mark.parametrize("name", list(BREAKS))
def test_validate_catches_a_broken_contract(built, name):
    _, d = built
    assert P.validate(_break(d, BREAKS[name])) != [], name


def test_validate_catches_hash_bytes_and_nan(built):
    _, d = built
    d2 = copy.deepcopy(d)
    d2["grid"]["cells"][0]["v"] += 1.0                       # содержание изменилось, хэш — прежний
    assert any("payload_sha256" in p for p in P.validate(d2))
    d3 = copy.deepcopy(d)
    d3["meta"]["bytes"] += 1
    assert any("meta.bytes" in p for p in P.validate(d3))
    d4 = _break(d, lambda x: None)
    d4["layers"]["analytical"]["pb"] = float("nan")
    assert P.validate(d4) != []


def test_hash_excludes_times_history_and_changes(built):
    _, d = built
    h = P.payload_hash(d)
    d2 = copy.deepcopy(d)
    d2["meta"]["generated_at"] = "2030-01-01T00:00:00+00:00"
    d2["meta"]["published_at"] = "2030-01-01T00:00:00+00:00"
    d2["live"]["fetched_at"] = "2030-01-01T00:00:00+00:00"
    d2["changes"] = {"vs_previous": None}
    d2["valuation_history"]["rule"] = "другое"
    assert P.payload_hash(d2) == h
    d2["fair_value"]["headline"]["median"] += 0.01
    assert P.payload_hash(d2) != h


def test_release_size_with_full_draws_and_history_fits(built):
    """Размер (П§0.4–0.5): 2 × draws книги прогонов и 400 строк истории — ниже 500 000 байт."""
    rel, d = built
    n = int(rel.book.get("valuation.uncertainty.draws"))
    d2 = copy.deepcopy(d)
    h = d2["fair_value"]["headline"]
    h["low_draws"] = [round(250.0 + (i % 997) * 0.3, 1) for i in range(n)]
    h["high_draws"] = [round(350.0 + (i % 991) * 0.3, 1) for i in range(n)]
    h["draws"] = n
    vh = d2["valuation_history"]
    rows = P.HISTORY_ROWS
    for k in ("median", "p10", "p25", "p75", "p90", "point"):
        vh[k] = [412.35] * rows
    vh["date"] = ["2026-10-01"] * rows
    vh["sha"] = ["0123456789ab"] * rows
    vh["price"] = {t: [272.87] * rows for t in d2["meta"]["company"]["tickers"]}
    size = P.compact_bytes(d2)
    assert size < P.MAX_BYTES, size
    d2["book"]["worlds_source"] += "x" * (P.MAX_BYTES - size + 10)
    _reseal(d2)
    assert any("потолка" in p for p in P.validate(d2))


def test_meta_bytes_is_measured_without_itself_and_published_at(built):
    _, d = built
    assert d["meta"]["bytes"] == P.compact_bytes(d)
    d2 = copy.deepcopy(d)
    d2["meta"]["published_at"] = "2026-10-01T16:00:00+00:00"
    assert P.compact_bytes(d2) == d["meta"]["bytes"]


def test_bank_language_and_market_identities(built):
    _, d = built
    b = d["fair_value"]["bank_first_line"]
    assert b["market_pb"] == d["market"]["multiples"]["pb"]
    assert b["excess_market"] == pytest.approx(d["market"]["cap"] - b["bv_v"], abs=0.05)
    assert d["market"]["price"] == d["market"]["prices"][d["meta"]["company"]["main_ticker"]]["price"]
    for row in d["paths"]["roe_tree"]:
        assert row["roa"] * row["leverage"] == pytest.approx(row["roe"], abs=1e-9)
    assert d["governance"]["sum_signed"] == d["governance"]["discount"]


def test_fast_release_marks_searches_as_not_computed(built):
    _, d = built
    assert all(r["status"] == "not_computed" for r in d["reverse_dcf"]["rows"])
    assert d["next_report"]["cor_table"] == [] and "error" in d["next_report"]["neutral"]
    assert d["fair_value"]["bank_first_line"]["rub_per_01pp_cor"] is None


def test_exdate_jump_invariant_holds(built):
    rel, _ = built
    f = rel.extra_findings[0]
    assert f.name == "exdate_jump" and not f.fired, f.message


def test_jump_guard_blocks_an_unexplained_jump(built):
    rel, d = built
    same = copy.copy(rel)
    same.previous = copy.deepcopy(d)                       # тот же выпуск прошлым — в пределах порога
    jg0 = P.build_payload(same)["fair_value"]["jump_guard"]
    assert jg0["reason"] == "within_limit" and jg0["v0_change"] == pytest.approx(0.0, abs=1e-6)
    assert jg0["v0_agm_adjustment"] == pytest.approx(d["fair_value"]["bridge"]["amount"], abs=0.01)
    prev = copy.deepcopy(d)
    prev["fair_value"]["headline"]["median"] = d["fair_value"]["headline"]["median"] * 1.5
    rel2 = copy.copy(rel)
    rel2.previous = prev
    d2 = P.build_payload(rel2)
    jg = d2["fair_value"]["jump_guard"]
    assert jg["reason"] == "unexplained" and d2["checks"]["invariants_broken"] >= 1
    assert any("jump_guard" in p for p in P.validate(d2))
    note = [{"valid_until": P.to_date("2026-12-31"), "expected_central": d["fair_value"]["headline"]["median"],
             "tolerance_pct": 5.0, "note": "тест"}]
    rel3 = copy.copy(rel2)
    rel3.notes = note
    d3 = P.build_payload(rel3)
    assert d3["fair_value"]["jump_guard"]["reason"] == "release_note" and P.validate(d3) == []
    assert d3["changes"]["vs_previous"] is not None


def test_rows_carry_the_dashboards_unit_codes(built):
    _, d = built
    for block in (d["judgements"]["rows"], d["reverse_dcf"]["rows"], d["reverse_dcf"]["bank_rows"], d["inputs"]["rows"]):
        assert {r["unit"] for r in block} <= set(P.UNIT_CODES), block[0]
    assert {r["unit"] for r in d["book"]["key_judgements"]} <= set(P.UNIT_CODES)
    by_kind = {(r["kind"], r["unit"]) for r in d["judgements"]["rows"]}
    assert all(u == "pp" for k, u in by_kind if k == "shift") and all(u == "dict" for k, u in by_kind if k == "dict")
    assert P.unit_code("%") == "pct" and P.unit_code("", values=[0.83, 0.7]) == "number"
    assert P.unit_code(None, values=[-400.0]) == "bn" and P.unit_code("доля", kind="mix") == "mix"
