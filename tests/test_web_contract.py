"""Витрина и контракт выпуска sber-v1 (docs/PAYLOAD.md, docs/DASHBOARD.md §2).

* витрина читает только объявленные блоки (`d.<блок>` — из REQUIRED_TOP_LEVEL П§1) и поля,
  которые есть в выпуске; снятые поля (П§6) не читает;
* фронт ничего не считает, кроме λ: суммы — только в сглаживании плотности, прогоны —
  только в правиле λ, потенциал, P/B, доходности и запасы — поля выпуска;
* шаг ползунка — сетка `by_lambda`; правило λ на прогонах выпуска даёт строки таблицы;
* синтетический выпуск tests/fixtures/payload-sample.json держит контракт: все блоки,
  хэш и размер (П§0.4), тождества П§7, поля и единицы W1, W2 и W3 (в форме выпуска ядра: сверка
  с контрольной моделью — в единицах строки, эквивалент ЧП — уровень, строки года сходятся к прибыли,
  год нау-каста — на одном слое, единицы плиток — коды П§0.2); совпадает со своим генератором;
* печатаемые строки выпуска — словами для владельца (П§0.2): без кортежей, e-нотации, меток класса,
  путей к листам, рабочих пометок, дат без года и версий без имени, без имён методов и полей API
  (источник дивидендной истории — решение собрания и раскрытие, решение P1, P7);
* фикстура после проверки W3 (решения P4, P7): дисконт за управление 0,004 и в сумме каналов, сдвиг
  доли в сверке — единицей `pp`, шум счёта в разности, дробный конец оси в годах.

Метка `tact` — у быстрых тестов без документов и без ядра (INTERFACES §9); сверка с
`validate` ядра и тесты по тексту PAYLOAD (`docs`) идут в CI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
PAYLOAD_DOC = ROOT / "docs" / "PAYLOAD.md"
FIXTURE = ROOT / "tests" / "fixtures" / "payload-sample.json"
GENERATOR = ROOT / "tests" / "web" / "make_sample.py"
MAX_BYTES = 500_000


def code_only(text: str) -> str:
    out = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", " ", out)


APP_CODE = code_only(APP)


def function_body(name: str) -> str:
    begin = APP_CODE.index(f"function {name}(")
    tail = re.search(r"(?m)^(?:function |async function |const |let )", APP_CODE[begin + 1:])
    return APP_CODE[begin:begin + 1 + tail.start()] if tail else APP_CODE[begin:]


def required_top_level() -> list[str]:
    """Блоки П§1 — первая колонка таблицы раздела «Верхние блоки»."""
    text = PAYLOAD_DOC.read_text(encoding="utf-8")
    part = text[text.index("## 1. Верхние блоки"):text.index("## 2. Поля блоков")]
    blocks = re.findall(r"^\| `([a-z_]+)` \|", part, re.M)
    assert len(blocks) == 28, f"в П§1 ожидалось 28 блоков, найдено {len(blocks)}"
    return blocks


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── витрина читает только объявленное ──

@pytest.mark.docs
def test_frontend_reads_only_declared_blocks():
    used = set(re.findall(r"(?<![\w$.])d\.([A-Za-z_]\w*)", APP_CODE))
    assert used, "витрина не читает ни одного блока через d.<блок>"
    assert used <= set(required_top_level()), f"необъявленные блоки: {sorted(used - set(required_top_level()))}"
    try:
        from model.payload import REQUIRED_TOP_LEVEL
    except ImportError:
        return
    assert set(REQUIRED_TOP_LEVEL) == set(required_top_level()), "список блоков ядра и П§1 разошёлся"


@pytest.mark.tact
def test_frontend_reads_fields_that_the_release_has():
    """Каждое `d.<блок>.<поле>` — поле синтетического выпуска (он собран по П§2)."""
    d = fixture()
    used = set(re.findall(r"(?<![\w$.])d\.([a-z_]+)\.([a-z_0-9]+)\b", APP_CODE))
    assert used, "регулярка ничего не нашла — поменялось соглашение `d` = выпуск"
    for block, field in sorted(used):
        node = d.get(block)
        assert isinstance(node, dict) and field in node, f"d.{block}.{field}: такого поля в выпуске нет"


@pytest.mark.docs
def test_frontend_does_not_read_removed_fields():
    """Поля П§6 (EV, колл Мертона, долг, цели по брокерам) витрина не читает."""
    text = PAYLOAD_DOC.read_text(encoding="utf-8")
    part = text[text.index("## 6. Снятые поля"):text.index("## 7. `validate`")]
    top = re.search(r"\| верхний уровень \| (.*?) \|", part).group(1)
    for name in re.findall(r"`(\w+)`", top):
        assert not re.search(rf"(?<![\w$.])d\.{name}\b", APP_CODE), f"витрина читает снятый блок {name}"
    specific = set()
    for row in re.findall(r"^\| `(?:fair_value|layers\.\*|grid\.cells\[\]|market|dividends|nowcast|book)` \| (.*?) \|", part, re.M):
        specific |= set(re.findall(r"`(\w+)`", row))
    generic = {"d", "ev", "interest", "margin", "strike", "claims"}
    for name in sorted(specific - generic):
        assert not re.search(rf"\.{name}\b", APP_CODE), f"витрина читает снятое поле {name}"
    assert not re.search(r"company\)?\.ticker\b", APP_CODE), "у банка две категории: tickers и main_ticker"
    assert "brokers.rows" not in APP_CODE and not re.search(r"\bb\.rows\b", function_body("brokersCard")), "цели по брокерам — только агрегаты"
    assert "sections" in specific and "sections" not in fixture()["book"], "строки книги по разделам сняты (В7): ни витрина, ни фикстура"


@pytest.mark.docs
def test_basis_codes_of_the_contract_have_words():
    """Каждый код базиса П§0.2 — в словаре витрины: код без подписи на экран не выходит (№ 62)."""
    text = PAYLOAD_DOC.read_text(encoding="utf-8")
    line = text[text.index("* **Базис.**"):text.index("* **Нераскрытое")]
    codes = set(re.findall(r"`([a-z]+)`\s+\(", line))
    assert {"ifrs", "ras", "mgmt", "engine", "regulatory", "sector"} <= codes, codes
    basis = re.search(r"const BASIS = \{(.*?)\};", APP_CODE).group(1)
    assert codes <= set(re.findall(r"(\w+): \"", basis)), codes - set(re.findall(r"(\w+): \"", basis))
    d = fixture()
    used = {i["basis"] for i in d["guidance"]["items"]} | {t["basis"] for t in d["indicators"]["tiles"]} | {d["meta"]["basis"], d["grid"]["basis"]}
    assert used <= set(re.findall(r"(\w+): \"", basis)), used


# ── фронт ничего не считает, кроме λ ──

@pytest.mark.tact
def test_the_front_computes_nothing_but_the_books_lambda_rule():
    assert APP_CODE.count("low_draws") == APP_CODE.count("head.low_draws"), "прогоны читаются только из заголовка"
    readers = {m.group(1) for m in re.finditer(r"function (\w+)\(", APP_CODE)
               if "low_draws" in function_body(m.group(1))}
    assert readers <= {"centresAt", "hero"}, f"прогоны читают {sorted(readers)}"
    dist = function_body("distributionChart")
    reducers = [m.start() for m in re.finditer(r"\.reduce\(", APP_CODE)]
    start = APP_CODE.index("function distributionChart(")
    assert all(start <= pos < start + len(dist) for pos in reducers), "суммы — только в сглаживании плотности"
    for forbidden in ("/ market", "/ price", "/ mk.price", "/ d.market.price", "cap /", ".cap /", "- bv_v", "- row.bv_v", "median / ",
                      "printed_median /", "/ head.market", ".dps /", "n20_post_dividend -", "- a.req20_now", "* N_out", "shares.outstanding_mln /"):
        assert forbidden not in APP_CODE, f"витрина считает сама: «{forbidden}»"
    hero = function_body("hero")
    assert "row.p_below_market" in hero and "row.market_percentile" in hero and "row.upside" in hero
    assert "g.sum_signed" in function_body("governanceCard"), "итог дисконта — число выпуска"
    for field in ("n20_headroom", "market_pb", "fair_pb_median", "excess_market", "dividend_yield_fwd", "yield"):
        assert field in APP_CODE, field


@pytest.mark.tact
def test_the_slider_rebuilds_the_median_from_the_release_draws():
    q = function_body("quantile7")
    assert "(n - 1) * q" in q and "Math.floor(h)" in q and "Math.min(lo + 1, n - 1)" in q
    assert "lo + lam * (head.high_draws[i] - lo)" in function_body("centresAt")
    at = function_body("headlineAt")
    assert "roundHalfUp(" in at and "head.print_step" in at and all(x in at for x in ("q(0.5)", "q(0.1)", "q(0.9)", "q(0.25)", "q(0.75)"))
    assert "roundHalfEven" not in APP_CODE, "печать — половиной вверх, как ядро"
    assert re.search(r"function roundHalfUp\(x\) \{\s*return Math\.floor\(x \+ 0\.5", APP_CODE)
    assert "view.low + lam * (view.high - view.low)" in function_body("pointAt")


@pytest.mark.tact
def test_the_sliders_steps_are_the_lambda_grid():
    assert "step: String(lambdaStep(d))" in function_body("hero"), "шаг ползунка — lambda_step выпуска"
    assert "lambda_step" in function_body("lambdaStep")
    d = fixture()
    head = d["fair_value"]["headline"]
    step = head["lambda_step"]
    for key in ("headline", "bank_first_line"):
        rows = (head if key == "headline" else d["fair_value"]["bank_first_line"])["by_lambda"]
        grid = [round(r["lambda"], 9) for r in rows]
        want = [round(i * step, 9) for i in range(round(1 / step) + 1)]
        assert all(g in grid for g in want), f"{key}.by_lambda не покрывает сетку ползунка"
    assert abs(step * 20 - 1) < 1e-12


# ── правило λ на прогонах фикстуры ──

def _q7(values, q):
    n = len(values)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return values[lo] + (h - lo) * (values[hi] - values[lo])


def _half_up(x, step):
    return float((Decimal(repr(x)) / Decimal(repr(step))).quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(repr(step)))


def _at(head, lam):
    c = sorted(a + lam * (b - a) for a, b in zip(head["low_draws"], head["high_draws"]))
    return c


@pytest.mark.tact
def test_the_lambda_rule_reproduces_the_headline_and_the_table():
    d = fixture()
    head = d["fair_value"]["headline"]
    lam = head["own_macro_confidence"]
    c = _at(head, lam)
    assert abs(_q7(c, 0.5) - head["median"]) <= 0.005
    assert abs(_q7(c, 0.1) - head["band80"][0]) <= 0.005 and abs(_q7(c, 0.9) - head["band80"][1]) <= 0.005
    assert head["printed_median"] == _half_up(head["median"], head["print_step"])
    assert head["printed_band80"] == [_half_up(v, head["print_step"]) for v in head["band80"]]
    main = d["meta"]["company"]["main_ticker"]
    for row in head["by_lambda"]:
        c = _at(head, row["lambda"])
        for q, key in ((0.5, "median"), (0.1, "p10"), (0.25, "p25"), (0.75, "p75"), (0.9, "p90")):
            assert abs(_q7(c, q) - row[key]) <= 0.005, (row["lambda"], key)
        assert abs(sum(1 for v in c if v < d["market"]["prices"][main]["price"]) / len(c) - row["p_below_market"]) <= 1e-6
    book = next(r for r in head["by_lambda"] if abs(r["lambda"] - lam) < 1e-9)
    assert book["median"] == head["median"] and book["p_below_market"] == head["p_below_market"]


# ── синтетический выпуск держит контракт ──

HASH_EXCLUDED_META = ("generated_at", "published_at", "payload_sha256", "previous_sha256", "bytes")


def _content(payload):
    p = json.loads(json.dumps(payload, ensure_ascii=False))
    for k in HASH_EXCLUDED_META:
        p["meta"].pop(k, None)
    p["live"].pop("fetched_at", None)
    p["nowcast"]["journal"].pop("releases", None)
    for e in p["nowcast"]["journal"].get("entries", []):
        e.pop("release_sha", None)
    p.pop("changes", None)
    p.pop("valuation_history", None)
    return p


def _finite(node, where="выпуск"):
    if isinstance(node, float):
        assert math.isfinite(node), where
    elif isinstance(node, dict):
        for k, v in node.items():
            _finite(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _finite(v, f"{where}[{i}]")


@pytest.mark.docs
def test_the_sample_release_follows_the_contract():
    d = fixture()
    assert d["schema"] == "sber-v1"
    assert [b for b in required_top_level() if b not in d] == []
    _finite(d)
    meta = d["meta"]
    compact = dict(d, meta={k: v for k, v in meta.items() if k not in ("published_at", "bytes")})
    size = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert meta["bytes"] == size <= MAX_BYTES, (meta["bytes"], size)
    canon = json.dumps(_content(d), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert meta["payload_sha256"] == hashlib.sha256(canon).hexdigest(), "хэш содержания (П§0.4)"
    company = meta["company"]
    assert company["main_ticker"] in company["tickers"] and "ticker" not in company
    for key in ("market_by_ticker", "p_below_by_ticker", "upside"):
        assert set(d["fair_value"]["headline"][key]) == set(company["tickers"]), key
    assert d["market"]["price"] == d["market"]["prices"][company["main_ticker"]]["price"]
    assert len(d["fair_value"]["headline"]["low_draws"]) == len(d["fair_value"]["headline"]["high_draws"]) == d["fair_value"]["headline"]["draws"]
    assert "rows" not in d["market"]["brokers"], "цели по брокерам — только агрегаты"


@pytest.mark.tact
def test_the_sample_release_keeps_the_identities():
    """Тождества П§7, которые витрина печатает рядом друг с другом."""
    d = fixture()
    fv = d["fair_value"]
    lam = fv["own_macro_confidence"]
    assert abs(fv["central"] - (fv["low"] + lam * (fv["high"] - fv["low"]))) <= 0.01
    mix = d["layers"]["headline_mix"]
    assert abs(mix["price"] - fv["central"]) <= 0.01
    wf = {r["key"]: r["amount"] for r in mix["waterfall"]}
    assert abs(wf["bv_v"] + wf["pv_ri_explicit"] + wf["pv_terminal"] - wf["v0"]) <= 0.05
    assert abs(wf["v0"] + wf["governance"] + wf["bridge"] - wf["equity"]) <= 0.05
    assert abs(fv["bridge"]["amount"] - sum(r["amount"] * r["sign"] for r in fv["bridge"]["rows"])) <= 0.05
    for key in ("analytical", "market_implied", "macro_neutral"):
        L = d["layers"][key]
        assert abs(sum(L["world_weights"].values()) - 1) <= 1e-9
        assert abs(L["v0"] - (L["bv_v"] + L["pv_ri_explicit"] + L["pv_terminal"])) <= 0.05
    for p in ("p_analytical", "p_market_implied", "p_neutral"):
        assert abs(sum(c[p] for c in d["grid"]["cells"]) - 1) <= 1e-5, p
    assert len({(c["world"], c["regime"], c["scenario"]) for c in d["grid"]["cells"]}) == 36
    b = d["capital"]["bridge"]
    assert abs(b["start"] + sum(r["pp"] for r in b["rows"]) - b["end"]) <= 1e-6
    for row in d["paths"]["roe_tree"]:
        assert abs(row["roa"] * row["leverage"] - row["roe"]) <= 1e-5
    assert abs(sum(c["share"] for c in fv["headline"]["contributions"]) - 1) <= 1e-6
    bfl = fv["bank_first_line"]
    assert abs(bfl["market_pb"] - d["market"]["cap"] / bfl["bv_v"]) <= 1e-4 and bfl["market_pb"] == d["market"]["multiples"]["pb"]
    assert abs(bfl["excess_market"] - (d["market"]["cap"] - bfl["bv_v"])) <= 0.05
    assert abs(fv["rates_view"]["rub"] - (fv["rates_view"]["high"] - fv["rates_view"]["low"])) <= 0.01
    assert d["governance"]["sum_signed"] == d["governance"]["discount"]
    for r in d["dividends"]["register"]:
        assert 0 < r["dps"] < 0.5 * d["market"]["price"] and r["ex_date"] == r["record_date"]
    assert all(r["ok"] for r in d["dividends"]["formula_check"]) and {2024, 2025} <= {r["year"] for r in d["dividends"]["formula_check"]}
    assert d["checks"]["invariants_broken"] == 0
    for g in d["checks"]["gates"]:
        if g["fired"]:
            assert g["explanation"] and g["valid_until"] >= d["meta"]["valuation_date"], g["name"]
    assert abs(sum(r["prior"] for r in d["regimes"]["rows"].values()) - 1) <= 1e-9
    assert abs(sum(r["posterior"] for r in d["regimes"]["rows"].values()) - 1) <= 1e-5


@pytest.mark.tact
def test_the_sample_release_speaks_the_w1_contract():
    """Фикстура — в полях и единицах W1 (LEAD-DECISIONS-2, PAYLOAD после spec-w1)."""
    d = fixture()
    t = d["nii"]["transmission"]
    assert 0.05 < t["roe_equiv"] < 5, "roe_equiv — п.п. ROE на 1 п.п. ключевой (доля на долю)"
    lo, hi = t["pairs_range"]
    keys = [p["key_from"] for p in t["pairs"]] + [t["pairs"][-1]["key_to"]]
    assert keys == sorted(keys) and all(p["inside"] == (lo <= p["value"] <= hi) for p in t["pairs"])
    retro = d["nowcast"]["retro"]
    for block in [retro, *retro["by_horizon"].values()]:
        assert set(block["rmse"]) == set(block["bias"]) == set(block["n"]) <= set(block["titles"])
        for k, bias in block["bias"].items():
            errs = [p["benchmarks"][k] - p["actual"] for p in block["periods"] if k in p["benchmarks"]]
            assert len(errs) == block["n"][k] and abs(sum(errs) / len(errs) - bias) <= 0.01, f"смещение {k} — млрд ₽ (эталон − факт)"
    nx = d["dividends"]["next_expected"]
    assert nx["status"] != "model" or nx["dps"] == nx["dps_policy"], "главная цифра без объявления — DPS политики"
    assert nx["dps_mean"] <= nx["dps_policy"] and 0 <= nx["p_cancel"] <= 1
    assert d["nowcast"]["year"]["dps_model"] == nx["dps_policy"] and d["market"]["multiples"]["dividend_yield_fwd"] == nx["yield"]
    assert {"n1_0", "n1_2"} <= set(d["capital"]["titles"])
    kinds = {i["key"]: i["kind"] for i in d["guidance"]["items"]}
    assert kinds["n20_0"] == "min" and set(kinds.values()) <= {"point", "min", "max", "range"}
    rows = d["judgements"]["rows"]
    assert all(isinstance(j["in_band"], bool) for j in rows)
    assert all(j["share"] is None and j["rank_corr"] is None for j in rows if not j["in_band"])
    assert all(j["in_band"] for j in rows), "книга 1.2: все оси в полосе (В1)"
    assert {c["judgement_key"] for c in d["fair_value"]["headline"]["contributions"]} <= {j["id"] for j in rows if j["in_band"]}
    assert all(isinstance(a["cir_mgmt"], float) for a in d["paths"]["annual"])
    near = d["regimes"]["near_nim_shift"]
    assert near[0]["period"] == d["meta"]["first_period"] and near[-1]["value"] == 0
    gates = {g["name"]: g for g in d["checks"]["gates"]}
    assert {"nim_path_joint", "transmission_pairs"} <= set(gates)
    assert all(len(g["cell_list"]) <= 10 and set(g["corridor"]) == {"text", "value"} for g in gates.values())
    assert "ni_jump" in {f["name"] for f in d["checks"]["flags"]}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["nowcast"]["admission"]["earliest_decision"])
    assert {"tau_eff", "days"} <= set(d["next_report"]["expectation"]) and "snapshot" in d["changes"]


UNIT_CODES = {"pct", "share", "pp", "bp", "bn", "rub", "price", "number", "years", "level", "mix", "dict", "count", "version", "date", "period"}
SERVICE_TEXT = re.compile(r"(?<![\w.])[a-z]+(?:_[a-z0-9]+)+(?![\w])|\b(?:true|false|inf)\b|\b\d{4}[MQ]\d{1,2}\b|\(-?inf|valuation\.uncertainty")


def _printed_strings(node, key=None):
    """Строки выпуска, которые витрина печатает как есть (П§0.2): title, name, note, message, detail, source, equation."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _printed_strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _printed_strings(v, key)
    elif isinstance(node, str) and key in ("title", "name", "note", "message", "detail", "source", "equation", "what", "explanation", "scope_note", "valid_until_note"):
        if not (key == "name" and re.fullmatch(r"[a-z0-9_]+", node)):       # имя проверки или гейта — ключ, печатается его `title`
            yield key, node


@pytest.mark.tact
def test_the_sample_release_speaks_the_w2_contract():
    """Фикстура — в полях и формах W2 (AUDIT-1, LEAD-DECISIONS-3, PAYLOAD после spec-w2)."""
    d = fixture()
    # № 58, В7: строк книги по разделам нет; источник суждения — словами
    assert "sections" not in d["book"]
    rows = d["judgements"]["rows"]
    for j in rows:
        assert j["source"] and len(j["source"]) <= 80 and not j["source"].startswith("valuation."), j["id"]
        want = None if j["kind"] == "dict" else (j["price_high"] + j["price_low"] - 2 * d["fair_value"]["central"]) / 6
        assert (j["mean_shift"] is None) == (want is None) and (want is None or abs(j["mean_shift"] - want) <= 0.01), j["id"]
    off = d["judgements"]["off_band_shift"]
    assert set(off) == {"rub", "limit", "axes"} and off["axes"] == sum(1 for j in rows if not j["in_band"])
    assert abs(off["rub"] - sum(j["mean_shift"] or 0 for j in rows if not j["in_band"])) <= 1e-9
    assert off["limit"] == d["fair_value"]["headline"]["print_step"] / 2
    # № 25, № 12: D_pend и слагаемое опорного капитала
    assert d["fair_value"]["bridge"]["pending_dividend"] == 0.0 and "v0_agm_adjustment" in d["fair_value"]["jump_guard"]
    # № 59: число клеток и их список
    gap = d["capital"]["capital_gap"]
    assert isinstance(gap["cells"], int) and isinstance(gap["cell_list"], list) and len(gap["cell_list"]) == min(gap["cells"], 10)
    # № 14: отмена кризисом — не урезание капиталом
    assert not any({"dividend_cut", "crisis_skip"} <= set(c["flags"]) for c in d["grid"]["cells"])
    # № 17, № 62: сектор и нормативы — своими базисами; «лучше сектора» не сравнивается
    items = {i["key"]: i for i in d["guidance"]["items"]}
    assert items["n20_0"]["basis"] == "regulatory" and all(i["scope"] in ("group", "sector") for i in items.values())
    for i in items.values():
        assert (i["scope"] == "sector") == (i["basis"] == "sector") == (i["relation"] in ("in_line", "above")), i["key"]
        if i["relation"] == "above":
            assert i["status"] == "n/a" and i["mass_outside"] is None
    assert {r["key"] for r in d["guidance"]["revisions"]} <= set(items)
    # № 16, № 19, № 22: эквивалент — уровень; ЧПМ режимы не учит при равных ожиданиях; местный наклон и размах
    nr = d["next_report"]
    ni = nr["expectation"]["ni"]
    for kind in ("cor", "nim"):
        eq = nr["neutral"][kind]["ni_equivalent"]
        assert abs(eq / ni - 1) < 0.1, f"{kind}: ni_equivalent — ЧП квартала при нейтральном значении, рядом с expectation.ni"
        table = nr[f"{kind}_table"]
        assert nr["reaction"][kind] == {"d_median_min": min(r["d_median"] for r in table), "d_median_max": max(r["d_median"] for r in table)}
        assert nr["slope"][f"at_{kind}"] in ("neutral", "expectation")
    assert len({r["nim_mgmt"] for r in nr["expectation"]["by_regime"]}) == 1 and nr["neutral"]["nim"]["ni_equivalent"] == ni
    assert len({json.dumps(r["posterior"], sort_keys=True) for r in nr["nim_table"]}) == 1, "строки ЧПМ вероятностей режимов не меняют"
    # № 15: сверка — в единицах строки, с видом допуска
    cm = d["checks"]["control_model"]["rows"]
    for r in cm:
        assert r["unit"] in UNIT_CODES and r["tol_kind"] in ("rel", "abs") and abs(r["diff"] - (r["control"] - r["core"])) <= 1e-6, r["what"]
        assert r["diff_rel"] is None if r["core"] == 0 else abs(r["diff_rel"] - (r["control"] / r["core"] - 1)) <= 1e-6, r["what"]
    assert any(abs(r["diff"]) > 1 and r["ok"] for r in cm), "есть строка с |diff| > 1 в допуске: абсолютная разность — не проценты"
    assert {r["tol_kind"] for r in cm} == {"rel", "abs"} and any("tol_abs" in r for r in cm)
    # № 33: срок политики — дата утверждения + 3 года; коды книги — как у ядра
    pol = d["dividends"]["policy"]
    assert pol["valid_until"] == f"{int(pol['approved'][:4]) + 3}{pol['approved'][4:]}" and pol["valid_until_note"]
    assert pol["base"] == "ifrs_ni_shareholders" and pol["divisor"] == "issued" and pol["crisis"]["catch_up"] is True
    # № 44, № 46: консенсус — только T-30; запись журнала несёт версию книги
    retro = d["nowcast"]["retro"]["by_horizon"]
    assert "consensus" in retro["T-30"]["rmse"] and "consensus" not in retro["T-90"]["rmse"] and "consensus" not in retro["T-90"]["titles"]
    assert all("consensus" not in p["benchmarks"] for p in retro["T-90"]["periods"])
    assert all("book_version" in e for e in d["nowcast"]["journal"]["entries"])
    # № 60: у месячных плиток дата — период; мин и макс — за окно истории
    monthly = [t for t in d["indicators"]["tiles"] if t["date"] and re.fullmatch(r"\d{4}M\d{2}", t["date"])]
    assert len(monthly) >= 4
    for t in d["indicators"]["tiles"]:
        if t["status"] != "missing":
            assert t["min"]["value"] == min(t["history"]["value"]) and t["max"]["value"] == max(t["history"]["value"]), t["id"]
            assert t["min"]["date"] in t["history"]["date"] and t["date"] == t["history"]["date"][-1]
    # В2, В8: окно наблюдений, год шока сдвигом, сдвиг спредов по сторонам баланса, пол LT-спреда
    crisis = d["regimes"]["rows"]["crisis"]["crisis"]
    assert d["regimes"]["update"]["window_obs"] == 4 and crisis["shock_year"] == int(d["meta"]["anchor_period"][:4]) + crisis["shock_year_offset"]
    t = d["nii"]["transmission"]
    assert 0 <= t["sigma0_split"] <= 1 and t["sigma0"] * t["sigma0_liab"] <= 0
    for b in d["nii"]["books"]:
        assert set(b["spread_lt_world"]) == set(d["worlds"]["order"]), b["key"]
        assert b["spread_floor"] is None or min(b["spread_lt_world"].values()) >= b["spread_floor"], f"{b['key']}: гейт lt_spread_floor в фикстуре тих"
    assert sum(1 for b in d["nii"]["books"] if b["spread_floor"] is not None) == 3
    gates = {g["name"] for g in d["checks"]["gates"]}
    assert {"lt_spread_floor", "off_band_shift"} <= gates and len(gates) == 19
    # № 28, № 68: пояснения строк разложения; строка фактов — период
    assert all("note" in r for r in d["changes"]["vs_previous"]["rows"]) and d["changes"]["vs_previous"]["walk_book"] == []
    assert any("входит в строку" in r["note"] for r in d["changes"]["vs_previous"]["rows"])
    facts = next(r for r in d["inputs"]["rows"] if r["key"] == "facts")
    assert facts["unit"] == "period" and re.fullmatch(r"\d{4}Q[1-4]", facts["value"])
    assert d["governance"]["discount"] == d["meta"]["governance_discount"] == 0.004      # В16


@pytest.mark.tact
def test_the_sample_release_speaks_the_w3_contract():
    """Фикстура — в полях и формах W3 (AUDIT-2, LEAD-DECISIONS-4, PAYLOAD после spec-w3)."""
    d = fixture()
    money = 0.05
    # № 83: строка FVC и разовые статьи; строки года сходятся к прибыли до налога (П§7 п. 11)
    for a in d["paths"]["annual"]:
        rows = a["nii"] - a["llp"] - a["fvc"] + a["fees"] + a["insurance"] + a["other"] + a["noncore"] - a["opex"] + a["one_off"]
        assert abs(a["pbt"] - rows) <= money, a["year"]
    assert any(a["one_off"] for a in d["paths"]["annual"]) and all(a["fvc"] > 0 for a in d["paths"]["annual"])
    # № 82: год нау-каста — на одном слое: без новости dps = dps_model; ошибка года шире квартальной; интервал — dps ± выплата с ошибки
    y, q = d["nowcast"]["year"], d["nowcast"]["quarter"]["by_target"]["ni_q"]
    per_share = lambda bn: bn * y["payout"] * 1000 / d["meta"]["shares"]["issued_mln"]  # noqa: E731
    assert abs(y["ni_year"] - (y["ni_fact"] + y["ni_quarter"] + y["ni_rest"])) <= money and abs(y["base"] - (y["ni_year"] - y["at1_coupon_after_tax"])) <= money
    assert q["deviation"] != 0 and abs((y["dps"] - y["dps_model"]) - per_share(q["deviation"])) <= 2e-4, "нау-каст двигает год только своим отклонением"
    assert y["ni_year_se"] > q["std_error"] and abs((y["dps_interval"][1] - y["dps_interval"][0]) / 2 - per_share(y["ni_year_se"])) <= 2e-4
    cor = d["nowcast"]["quarter"]["by_target"]["cor_q"]
    assert cor["interval"] is None and cor["std_error"] is None and cor["forecast"] == cor["expectation"], "нау-каст без моста — ожидание модели, без интервала"
    # № 84: провалы истории — по базисам, диапазоны одного базиса не пересекаются; годовые упр. метрики — годовой узел
    gaps = d["history"]["gaps"]
    assert {g["basis"] for g in gaps} == {"ifrs", "mgmt"} and len(gaps) == len({g["basis"] for g in gaps})
    quarters = {x["period"]: x for x in d["history"]["quarters"]}
    span = lambda g: g["period"].split("–")  # noqa: E731
    for g in gaps:
        lo, hi = span(g)
        for period, row in quarters.items():
            inside = lo <= period <= hi
            value = row["ifrs"]["ni_sh"] if g["basis"] == "ifrs" else row["mgmt"]["nim"]
            assert (value is None) == inside, (g["basis"], period)
    mgmt = [x["mgmt"]["nim"] for x in d["history"]["quarters"]]
    first_gap = mgmt.index(None)
    assert first_gap > 0 and any(v is not None for v in mgmt[first_gap:]), "провал упр. метрик — в середине ряда: линия рвётся"
    assert all(a["mgmt"]["nim"] is not None for a in d["history"]["annual"]), "год с неполными кварталами несёт годовой узел фактов, а не среднее трёх кварталов"
    # № 98: единицы плиток — коды П§0.2, базис — коды (рынок — market, нормативы банка — regulatory)
    tiles = {t["id"]: t for t in d["indicators"]["tiles"]}
    assert {t["unit"] for t in tiles.values()} == {"bn", "share", "price", "level"} and all(t["unit"] in UNIT_CODES for t in tiles.values())
    assert tiles["n1_0"]["basis"] == tiles["n1_1"]["basis"] == "regulatory" and tiles["f123_capital"]["basis"] == "ras"
    assert {t["basis"] for t in tiles.values()} == {"ras", "regulatory", "market"}
    # № 100: дата формы ЦБ — окно оценки, не точная дата
    form = next(e for e in d["calendar"]["events"] if e["kind"] == "form102")
    ras = d["calendar"]["next_ras"]
    assert form["precision"] == "window" and form["confirmed"] is False and form["earliest"] == form["date"] == ras["form102_date_est"] < form["latest"] == ras["form102_latest_est"]
    # В12: сжатие φ — по сторонам баланса; флаг phi — у книг обеих сторон; кредитная маржа миров
    t = d["nii"]["transmission"]
    assert abs(t["phi_assets"] - t["phi_split"] * t["phi"]) <= 1e-6 and t["phi_liab"] > 0 and set(t["loan_margin"]) == set(d["worlds"]["order"])
    assert {b["side"] for b in d["nii"]["books"] if b["phi"]} == {"asset", "liability"}
    # В15, № 87: пол вероятности режима; слагаемое сторожа без записи реестра
    assert 0 < d["regimes"]["update"]["floor_share"] < 1
    assert {"unregistered_dividend", "unregistered_dps"} <= set(d["fair_value"]["jump_guard"])
    # В17: истекающее объяснение и истёкшая политика — флаги; первый поднят вместе с признаком гейта
    flags = {f["name"]: f for f in d["checks"]["flags"]}
    assert {"explanation_expiring", "policy_expired"} <= set(flags)
    assert flags["explanation_expiring"]["raised"] == any(g["fired"] and g["expiring"] for g in d["checks"]["gates"])
    assert flags["policy_expired"]["raised"] == (d["dividends"]["policy"]["valid_until"] < d["meta"]["valuation_date"])
    # № 102: «оценка капитала» в мосте; снимки гайденса по датам — смен значения меньше, чем строк
    titles = {r["key"]: r["title"] for r in d["layers"]["headline_mix"]["waterfall"]}
    assert titles["v0"] == "Оценка капитала V0" and not any("тоимость капитала V0" in v for v in titles.values())
    rev = d["guidance"]["revisions"]
    dates = sorted({r["date"] for r in rev})
    assert len(rev) == len(dates) * len(d["guidance"]["items"]) and len(dates) >= 3
    changes = sum(1 for a in rev for b in rev if a["key"] == b["key"] and dates.index(b["date"]) == dates.index(a["date"]) + 1 and a["value"] != b["value"])
    assert 0 < changes < len(d["guidance"]["items"])
    # № 99, № 101: единица оси — код (срок — years, коэффициент — number); оси-словари несут оба конца по ключам; строка без оси в обратном расчёте
    rows = {j["id"]: j for j in d["judgements"]["rows"]}
    assert rows["fvoci_maturity"]["unit"] == "years" and rows["kappa"]["unit"] == "number"
    dicts = [j for j in rows.values() if j["kind"] == "dict"]
    assert len(dicts) == 2 and all(set(j["book"]) == set(j["low"]) == set(j["high"]) for j in dicts)
    reg = next(r for r in d["reverse_dcf"]["rows"] if r["key"] == "reg_mix")
    assert reg["range"][0] == reg["range"][1] and reg["solved"] is None and reg["status"] == "unreachable"
    # № 105: в истории дивидендов есть нулевой год и год с выплатой меньше рубля
    dps = [h["dps"] for h in d["dividends"]["history"]]
    assert 0.0 in dps and any(0 < v < 1 for v in dps)
    assert d["nii"]["disclosed"]["nii_per_100bp"] is None and d["nii"]["disclosed"]["src"], "нераскрытое — null с причиной (№ 103)"
    assert d["meta"]["book_version"] == "1.3"


@pytest.mark.tact
def test_the_sample_release_speaks_the_w4_contract():
    """Фикстура после проверки W3 (решения ведущего P1, P4, P7; отчёты verify-num, verify-deploy, verify-push)."""
    d = fixture()
    # P4, verify-num № 7: дисконт за управление 0,004 — и ключ, и сумма каналов, и строки суждений и обратного расчёта
    g = d["governance"]
    assert g["discount"] == g["sum_signed"] == d["meta"]["governance_discount"] == 0.004
    assert abs(sum(c["value"] * c["sign"] for c in g["components"]) - g["discount"]) <= 1e-12, "сумма каналов равна дисконту"
    assert next(j for j in d["judgements"]["rows"] if j["id"] == "governance")["book"] == 0.004
    assert next(x for x in d["reverse_dcf"]["rows"] if x["key"] == "governance")["book"] == 0.004
    # P7, verify-deploy № 6: сдвиг доли в сверке — единицей `pp` (как у писателя сводки); шум счёта в разности — не ноль в данных
    cm = d["checks"]["control_model"]["rows"]
    shifts = [x for x in cm if x["unit"] == "pp"]
    assert len(shifts) >= 2 and all(x["tol_kind"] == "abs" for x in shifts) and any("σ0" in x["what"] for x in shifts)
    assert any(0 < abs(x["diff"]) < x["tol"] * 1e-6 and x["ok"] for x in shifts), "есть строка, где разность — шум счёта: на экране она ноль"
    # P7, verify-deploy № 4: ось в годах с дробным концом — «2,5 года»
    axis = next(j for j in d["judgements"]["rows"] if j["id"] == "fvoci_maturity")
    assert axis["unit"] == "years" and axis["low"] != int(axis["low"]) and axis["book"] == int(axis["book"])
    # P1, P7: источник истории дивидендов — решение собрания и раскрытие, сверка с брокерским календарём — словами
    sources = {h["src"] for h in d["dividends"]["history"]}
    assert len(sources) == 2 and all("собрани" in x and "раскрытие эмитента" in x for x in sources) and any("брокерским календарём" in x for x in sources)
    # P7, verify-deploy № 5: счётчики проверок — 2 сработавших гейта, 1 поднятый флаг; сработавшие гейты фикстуры считают клетки
    fired = [x for x in d["checks"]["gates"] if x["fired"]]
    assert len(fired) == 2 and all(x["cells"] > 0 for x in fired) and sum(1 for f in d["checks"]["flags"] if f["raised"]) == 1


# Служебный текст в печатаемых строках (П§0.2, AUDIT-2 № 73, № 96): те же запреты, что у общего теста текста экранов.
WORKING_TEXT = {
    "кортеж": re.compile(r"\(\d{4}, \d+, [A-Z]{3,}"),
    "e-нотация": re.compile(r"(?<![\w.,])\d(?:[.,]\d+)?e[-−+]?\d{1,3}(?!\w)"),
    "метка класса допущения": re.compile(r"\[(?:В|Ф|Р|НП)\]"),
    "хвост sha256": re.compile(r"sha256 [0-9a-f]{8,}"),
    "путь к листу или файл": re.compile(r"(?<![\w/.])[a-z][a-z\d_]*(?:/[a-z\d_.-]+)+|(?<!\w)[\w.-]+\.(?:json|ya?ml|pdf|md|xlsx?|csv|txt|py)(?!\w)"),
    "рабочая пометка": re.compile(r"ведущ|первичк|антибот|без токена", re.I),
    "имя метода или поля API": re.compile(r"(?<!\w)(?:Get|List|Post|Find)[A-Z][A-Za-z]+|(?<!\w)[a-z]+(?:[A-Z][a-z\d]+)+(?!\w)|[A-Z][a-z]+Service(?![^\W\d_])|(?<!\w)API(?!\w)"),
    "версия без имени": re.compile(r"850oa \d+[.,]\d"),
    "дата без года": re.compile(r"(?<![\d.,§\w-])(?:0[1-9]|[12]\d|3[01])\.(?:0[1-9]|1[0-2])(?![\d.]|\.\d|\s?%)"),
    "процент без пробела или с точкой": re.compile(r"\d\.\d+\s?%|\d%"),
    "падеж после числа на 1": re.compile(r"в (?:\d*[02-9])?1 (?:клетках|кварталах)"),
    "число прогонов без разрядки": re.compile(r"\d{4,} прогон"),
    "падеж после дробного числа": re.compile(r"\d,\d+ (?:год|лет|день|дней)(?![а-яё])"),
}
PRINTED_KEYS = ("title", "name", "note", "message", "detail", "source", "src", "equation", "what", "explanation", "scope_note", "valid_until_note",
                "basis", "event", "reason", "text", "condition", "rule", "method", "definition", "summary")


def _working_text(payload) -> list[str]:
    found = []

    def walk(node, key=None, where="выпуск"):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, key, f"{where}[{i}]")
        elif isinstance(node, str) and key in PRINTED_KEYS:
            for rule, pattern in WORKING_TEXT.items():
                hit = pattern.search(node)
                if hit:
                    found.append(f"{where}: {rule} — «{hit.group(0)}» в «{node[:100]}»")

    walk(payload)
    return found


@pytest.mark.tact
def test_the_printed_strings_of_the_sample_are_words_for_the_owner():
    """№ 73, № 96, № 97: в печатаемых строках фикстуры нет кортежей, e-нотации, меток класса допущения, хвостов sha256,
    путей к листам и файлам, рабочих пометок, дат без года, версий без имени, процентов без пробела;
    P1, P7: ни имён методов и полей API, ни пометок о токене, после дробного числа — «года»."""
    assert _working_text(fixture()) == []


@pytest.mark.tact
@pytest.mark.parametrize("field, text, rule", [
    ("note", "дата календаря эмитента в T-Invest (2026, 3, QUARTER; ≈10-е число — РСБУ)", "кортеж"),
    ("detail", "Nss(H) = цель (6.9e-18), T_real = T* (3.7e-16)", "e-нотация"),
    ("note", "дата — оценка [В]; новая политика входит новой версией книги", "метка класса допущения"),
    ("src", "презентация 2К26, с. 24, sha256 d17d36167137507e", "хвост sha256"),
    ("basis", "других изъятий нет; evidence/governance", "путь к листу или файл"),
    ("src", "факты capital.json (Н20.0 группы, Н1.1 банка)", "путь к листу или файл"),
    ("explanation", "порог — вопрос ведущему (М§4.4–§4.5)", "рабочая пометка"),
    ("source", "частота эпизодов; первичку собрать", "рабочая пометка"),
    ("note", "прецедент 28.10.2025; IR-страница закрыта антиботом", "рабочая пометка"),
    ("source", "850oa 1.6; DESIGN §3.4", "версия без имени"),
    ("source", "решение владельца 30.09; ось 0,049–0,062", "дата без года"),
    ("scope_note", "прогноз сектора обновлён к 29.04", "дата без года"),
    ("title", "Доля 50% прибыли", "процент без пробела или с точкой"),
    ("message", "P/B клетки вне коридора мира в 1 клетках", "падеж после числа на 1"),
    ("detail", "во всех клетках сетки и 2000 прогонов полосы", "число прогонов без разрядки"),
    ("src", "календарь дивидендов брокера (снимок 30.09.2026, без токена)", "рабочая пометка"),
    ("src", "брокер: GetPayouts, снимок 30.09.2026", "имя метода или поля API"),
    ("src", "брокер, CalendarService, снимок 30.09.2026", "имя метода или поля API"),
    ("src", "payoutNet 33,30 ₽, cutoffDate 11.07.2024", "имя метода или поля API"),
    ("src", "API брокера, снимок 30.09.2026", "имя метода или поля API"),
    ("source", "дюрация портфеля: 1,6 год … 2,4 года", "падеж после дробного числа"),
    ("note", "срок — около 2,5 лет", "падеж после дробного числа"),
])
def test_the_text_rules_catch_the_working_notes_of_the_second_audit(field, text, rule):
    """Правила ловят строки, которые второй аудит нашёл на экранах выпуска (сначала красное — потом правка источника)."""
    found = _working_text({"x": {field: text}})
    assert found and rule in found[0], found


@pytest.mark.tact
@pytest.mark.parametrize("text", [
    "решение владельца 30.09.2026; ось 0,049–0,062", "запись семейства book-1.6 от 21.09.2026", "ЧПМ 6,58–6,63 % против ≈6,2 %",
    "наибольшая относительная разность 1,8·10⁻¹²", "п. 6.4 Положения: срок — 3 года", "Н20.0 после выплаты не ниже 13,3 %",
    "в 21 клетке и 4 кварталах", "ЦБ, форма 0409135", "рост г/г +25 % при ключевой 14,62 % → 14,00 %", "оценка по прошлым годам: 06.12.2023, 10.12.2025",
    "решение годового общего собрания акционеров от 30.06.2026 (раскрытие эмитента); сверено с брокерским календарём дивидендов",
    "дюрация 1,6 года; сроки портфеля 2,5–6 лет; от 2,5 до 6 лет", "T-Invest, 18:49 МСК", "МСФО 6М26 с. 5, 68; презентация 2К26 с. 21", "β_E к индексу ММВБ; ROE'_T выше g_T",
])
def test_the_text_rules_leave_ordinary_words_alone(text):
    assert _working_text({"x": {"note": text}}) == []


@pytest.mark.tact
def test_the_sample_release_carries_no_service_text():
    """П§0.2: единицы — из кодов; в строках, которые витрина печатает как есть, нет имён полей и ключей, true/false, inf и кодов периодов."""
    d = fixture()

    def units(node, where="выпуск"):
        if isinstance(node, dict):
            if isinstance(node.get("unit"), str) and where.split(".")[0] not in ("meta",):
                assert node["unit"] in UNIT_CODES, f"{where}: единица «{node['unit']}» не из кодов П§0.2"
            for k, v in node.items():
                units(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                units(v, f"{where}[{i}]")

    units(d)
    for key, text in _printed_strings(d):
        hit = SERVICE_TEXT.search(re.sub(r"\S+\.(?:json|pdf|ya?ml|md)\b", " ", text))
        assert not hit, f"{key}: служебный текст «{hit.group(0)}» в «{text[:120]}»"


@pytest.mark.tact
def test_the_sample_release_matches_its_generator():
    done = subprocess.run([sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_sample_release_passes_the_core_validate():
    """Когда появится `model/payload.py::validate`, синтетический выпуск обязан быть годным."""
    try:
        from model.payload import validate
    except ImportError:
        pytest.skip("model/payload.py ещё нет (поток core2): сверка с validate — после интеграции")
    problems = validate(fixture())
    assert problems == [], problems[:10]
