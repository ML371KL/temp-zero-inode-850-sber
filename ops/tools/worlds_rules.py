# -*- coding: utf-8 -*-
"""Правила книги Сбербанка для чисел, зависящих от миров ставок, — шаги `rule` манифеста приёма миров.

Каждая функция `f(ctx) -> [{"file", "values", "decimals", "replace"}]` (`ctx` — RuleContext команды
`ops/tools/adopt_worlds.py`: `ctx.root`, `ctx.record`, `ctx.overlay`, `ctx.yaml(rel)`, `ctx.module(rel)`, `ctx.version`,
`ctx.from_version`) считает числа книги по правилу из записи миров, надстройки, фактов, выходов листов и таблиц и не
пишет сама: пишет команда, точечно (`values` — адрес → число; адрес — кортеж: элементы списков номером), а числа
правил в тексте вне данных — заменой (`replace`: [было, стало] или [было, стало, сколько]).
Сторож (`adopt_worlds.py --check`, `tests/test_worlds_rules.py`): на записи книги каждое правило даёт шаблон.

  book_version   номер версии книги: младший разряд + 1, если версия с этим номером уже выпущена на другой записи миров
                 (тег `book-<версия>`); строка 1 шаблона и текста книги, тег версии и строка «миры» текста книги;
  world_weights  веса миров A-P1 — вероятности записи; ось «Веса миров» вне диапазонов записи — стоп (суждение);
  spreads        ключи спредов книг ЧПД: 2028–2030 (годы Y0 + 2 … LT_from − 1) — лист `evidence/nii/h_path_spreads.py`
                 (калибровка истории, снимок 2К26, на пути мира H записи; решение ведущего 29.10.2026); "2026" — A-N1n:
                 ставка якоря − β × опора мира H первого прогнозного полугодия (поле записи / 100, 5 знаков, как в
                 машинной книге), 6 знаков — как пишет перенос якоря (`ops/tools/reanchor.py`); "2027" — середина между
                 "2026" и НОВЫМ значением 2028 года (у оптового фондирования с LT_from 2028 — до LT), 6 знаков; LT —
                 замороженная калибровка (решение владельца 14.7), правило её не трогает;
  crisis_cbr     рост кредита кризисного режима (`regimes.crisis.loan_growth_override`) — середины «требований»
                 рискового сценария ЦБ, коридор гейта `checks.m_crisis_vs_cbr.loan_growth` — их диапазоны (блок
                 `crisis_regime` черновика надстройки);
  sigma0_split   доля σ0 (A-N7) и концы её оси — центр и область гейта листа `evidence/nii/sigma0_split.py`;
  phi_split      доля φ (A-N8) и концы её оси — лист `evidence/nii/phi_split.py` (после записи σ0);
  governance     дисконт за управление (A-V7): медиана таблиц (`results.json`) → вход листа `evidence/governance`
                 (`fixed_point.median_rub`, `median_of`) → неподвижная точка листа → ключ, канал (а) и числа его строки
                 `basis` (её печатает витрина); цикл «ключ → таблицы → медиана → ключ» — шаг `loop` манифеста;
  gate_texts     числа объяснений сработавших гейтов, которые есть в таблицах (сообщения гейтов, клетки, режимы), записи
                 и книге (строки блочного стиля — заменой текста, `text_values`); остальные числа текстов — человеку;
  gate_masses    ожидаемые массы объяснений сработавших гейтов — массы таблиц книги (4 знака).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

TEMPLATE = "data/assumptions/assumptions_template.yaml"
BOOK_TEXT = "data/assumptions/ASSUMPTIONS-BOOK.md"
EVIDENCE = "data/assumptions/evidence"
GATES = "data/assumptions/gate_explanations.yaml"
RESULTS = "data/assumptions/results.json"
DRAFT = f"{EVIDENCE}/worlds_bank/inputs/bank_overlay_draft.yaml"
GOV_INPUTS = f"{EVIDENCE}/governance/inputs/governance_inputs.yaml"
REF_FIELD = {"key": "key_avg", "ofz_1y": "ofz_1y", "ofz_3y": "ofz_3y", "ofz_5y": "ofz_5y", "ofz_10y": "ofz_10y"}
NEAR_DECIMALS = 6
MASS_DECIMALS = 4


class RuleError(Exception):
    """Шаблон не в том виде, который правило знает (строка переписана руками и т. п.)."""


# ----------------------------------------------------------------------------------------------- общее
def ru(x: float, nd: int, *, sign: bool = False) -> str:
    """Число для текста: запятая, минус «−», группы тысяч пробелом."""
    s = f"{abs(x):,.{nd}f}".replace(",", " ").replace(".", ",")
    if round(x, nd) < 0:
        return "−" + s
    return ("+" + s) if sign and round(x, nd) > 0 else s


def ru_date(iso: str) -> str:
    return dt.date.fromisoformat(str(iso)).strftime("%d.%m.%Y")


def template(ctx) -> dict:
    return ctx.yaml(TEMPLATE)


def text_of(ctx, rel: str) -> str:
    return (Path(ctx.root) / rel).read_text(encoding="utf-8")


def json_file(ctx, rel: str) -> Any:
    return json.loads((Path(ctx.root) / rel).read_text(encoding="utf-8"))


def one(text: str, pattern: str, flags: int = 0) -> re.Match:
    ms = list(re.finditer(pattern, text, flags))
    if len(ms) != 1:
        raise RuleError(f"в тексте {len(ms)} мест под правило (нужно одно): {pattern[:90]}")
    return ms[0]


def sub_groups(m: re.Match, values: list[str]) -> list[str]:
    """[было, стало]: совпадение `m` с группами, заменёнными на `values`."""
    s, base = m.group(0), m.start(0)
    out, at = [], 0
    for i, v in enumerate(values, 1):
        out.append(s[at:m.start(i) - base])
        out.append(v)
        at = m.end(i) - base
    out.append(s[at:])
    return [s, "".join(out)]


def axis_index(book: dict, path: str) -> int:
    axes = book["valuation"]["uncertainty"]["axes"]
    hits = [i for i, a in enumerate(axes) if a["paths"] == [path]]
    if len(hits) != 1:
        raise RuleError(f"осей {path}: {len(hits)}, нужна одна")
    return hits[0]


# ----------------------------------------------------------------------------------------------- версия книги
def released_record(root: Path, version: str) -> str | None:
    """sha256 записи миров книги выпуска `book-<версия>` (тег); тега нет — None."""
    p = subprocess.run(["git", "-C", str(root), "show", f"book-{version}:{TEMPLATE}"], capture_output=True)
    if p.returncode != 0:
        return None
    return str(((yaml.safe_load(p.stdout.decode("utf-8")) or {}).get("worlds") or {}).get("source", {}).get("sha256"))


def book_version(ctx) -> list[dict]:
    """Приём миров — новая версия книги: младший разряд + 1, если версия с номером шаблона уже выпущена на другой записи
    миров (тег `book-<версия>`). Иначе — номер шаблона (приём той же записи, повтор команды, книга до выпуска). Строка 1
    шаблона и текста книги, тег версии текста и строка «миры» текста (происхождение и дата снимка записи) — за ним."""
    book = template(ctx)
    cur = str(book["meta"]["version"])
    src = book["worlds"]["source"]
    rel = released_record(Path(ctx.root), cur)
    new = cur
    if rel is not None and rel != src["sha256"]:
        head, _, minor = cur.rpartition(".")
        new = f"{head}.{int(minor) + 1}"
    tpl = text_of(ctx, TEMPLATE)
    m = one(tpl.split("\n", 1)[0], r"ШАБЛОН машинной книги, версия (\S+) \(запись версии в журнале — (\d\d\.\d\d\.\d{4});")
    day = dt.date.today().strftime("%d.%m.%Y") if new != cur else None   # день записи новой версии — день приёма
    out = [{"file": TEMPLATE, "values": {("meta", "version"): new},
            "replace": [sub_groups(m, [new, day])] if day else []}]
    text = text_of(ctx, BOOK_TEXT)
    first = text.splitlines()[0]
    m = re.fullmatch(r"(.*версия )(\S+)", first)
    if not m:
        raise RuleError("строка 1 текста книги не кончается «версия <номер>»")
    pairs = [[first + "\n", f"{m.group(1)}{new}\n"]]
    pairs.append(sub_groups(one(text, r"\*\*тег версии:\*\* `book-([\d.]+)`"), [new]))
    rec = one(text, r"\*\*версия ([\d.]+) записана:\*\* (\d\d\.\d\d\.\d{4})")
    pairs.append(sub_groups(rec, [new, day]) if day else [rec.group(0)] * 2)
    pairs.append(sub_groups(one(text, r"\*\*миры:\*\* запись семейства (\S+ \S+) \(снимок (\d\d\.\d\d\.\d{4})[;)]"),
                            [src["origin"], ru_date(src["curve_date"])]))
    days = ((ctx.entry or {}).get("record") or {}).get("window_days")
    if days:                                     # шапка: снимок записи версии миров и его окно дней
        a, b = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
        window = (f"{a:%d}–{b:%d.%m.%Y}" if (a.month, a.year) == (b.month, b.year) else f"{a:%d.%m.%Y}–{b:%d.%m.%Y}")
        pairs.append(sub_groups(one(text, r"кривые миров — снимок записи семейства (\S+) \(окно ([\d.–]+);"),
                                [ctx.version, window]))
    out.append({"file": BOOK_TEXT, "replace": pairs})
    return out


# ----------------------------------------------------------------------------------------------- веса миров
def world_weights(ctx) -> list[dict]:
    """Веса миров слоя «свой макро-взгляд» (A-P1) — вероятности записи миров; концы оси «Веса миров» — суждение книги:
    правило их не двигает, но вне диапазонов записи (`probability_ranges`) — стоп (решение ведущему)."""
    book = template(ctx)
    probs = ctx.record["probabilities"]
    ranges = ctx.record.get("probability_ranges") or {}
    axes = book["valuation"]["uncertainty"]["axes"]
    ax = axes[axis_index(book, "joint.world_prob")]
    out = [f"{end} {w} {v}" for end in ("low", "high") for w, v in ax[end].items()
           if w in ranges and not ranges[w][0] <= v <= ranges[w][1]]
    if out:
        raise RuleError(f"ось «Веса миров» вне диапазонов записи миров {ranges}: {out} — суждение ведущему")
    values = {("joint", "world_prob", w): float(probs[w]) for w in book["joint"]["world_prob"]}
    return [{"file": TEMPLATE, "values": values}]


# ----------------------------------------------------------------------------------------------- спреды ЧПД
def _half(period: str) -> str:
    return f"{period[:4]}H{1 if int(period[5]) <= 2 else 2}"


def spreads(ctx) -> list[dict]:
    book = template(ctx)
    nii = book["nii"]
    first = str(book["meta"]["first_period"])
    y0 = int(first[:4])
    ref_world = str(nii["transmission"]["reference_world"])
    row = next(r for r in ctx.record["rows"] if r["world"] == ref_world and r["period"] == _half(first))
    rates = {b: float(v["rate_anchor"]["v"]) for b, v in
             json_file(ctx, "data/facts/nii_books.json")["books"].items() if "rate_anchor" in v}
    hps = ctx.module(f"{EVIDENCE}/nii/h_path_spreads.py", "worlds_rules_h_path_spreads")
    near = ctx.module(f"{EVIDENCE}/nii/near_spreads.py", "worlds_rules_near_spreads")
    values: dict[tuple, float] = {}
    decimals: dict[tuple, int] = {}
    new = {b: dict(s["spread"]) for b, s in nii["books"].items() if isinstance(s["spread"], dict)}
    hp = hps.compute(book, ctx.record)                       # 1) 2028–2030 — калибровка на пути мира H записи
    if hp["book_mismatches"] and any("опора книги" in x for x in hp["book_mismatches"]):
        raise RuleError(f"лист h_path_spreads: {hp['book_mismatches']}")
    for b, v in hp["books"].items():
        for y, x in v["rule"].items():
            values[("nii", "books", b, "spread", y)] = x
            decimals[("nii", "books", b, "spread", y)] = 5
            new[b][y] = x
    for b, spec in nii["books"].items():                     # 2) "2026" — A-N1n; 3) "2027" — середина к новому 2028
        sp = spec["spread"]
        if not isinstance(sp, dict) or str(y0) not in sp:
            continue
        ref = round(float(row[REF_FIELD[spec["ref"]]]) / 100.0, 5)
        beta = float(spec["beta"]) if spec["side"] == "liability" else 1.0
        s0 = round(rates[b] - beta * ref, NEAR_DECIMALS)
        new[b][str(y0)] = s0
        values[("nii", "books", b, "spread", str(y0))] = s0
        if str(y0 + 1) in sp:
            s1 = round((s0 + float(near.year_value(new[b], y0 + 2))) / 2, NEAR_DECIMALS)
            values[("nii", "books", b, "spread", str(y0 + 1))] = s1
        for y in (str(y0), str(y0 + 1)):
            decimals[("nii", "books", b, "spread", y)] = NEAR_DECIMALS
    return [{"file": TEMPLATE, "values": values, "decimals": decimals}]


# ----------------------------------------------------------------------------------------------- кризис против ЦБ
def _pct_text(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else ru(float(x), 1)


def crisis_cbr(ctx) -> list[dict]:
    """Кризисный режим против рискового сценария ЦБ (ОНДКП, табл. 3.5): «требования банковской системы к экономике»
    года — диапазон коридора гейта и середина абсолютного роста кредита режима (годы, которые несёт шаблон)."""
    book = template(ctx)
    years = ctx.yaml(DRAFT)["crisis_regime"]["years"]
    claims = {str(y): [float(v) for v in r["claims"]] for y, r in years.items()}
    values: dict[tuple, Any] = {}
    for y in book["regimes"]["crisis"]["loan_growth_override"]:
        lo, hi = claims[str(y)]
        values[("regimes", "crisis", "loan_growth_override", str(y))] = round((lo + hi) / 2 / 100, 6)
    for y in book["checks"]["m_crisis_vs_cbr"]["loan_growth"]:
        for i, v in enumerate(claims[str(y)]):
            values[("checks", "m_crisis_vs_cbr", "loan_growth", str(y), i)] = round(v / 100, 6)
    ys = list(book["regimes"]["crisis"]["loan_growth_override"])
    pat = "середины рискового сценария ЦБ " + " / ".join(r"(\d+(?:,\d)?)–(\d+(?:,\d)?) %" for _ in ys)
    m = one(text_of(ctx, TEMPLATE), pat)
    repl = sub_groups(m, [_pct_text(v) for y in ys for v in claims[str(y)]])
    return [{"file": TEMPLATE, "values": values, "replace": [repl]}]


# ----------------------------------------------------------------------------------------------- доли σ0 и φ
def _split(ctx, key: str, out_name: str) -> list[dict]:
    book = template(ctx)
    out = json_file(ctx, f"{EVIDENCE}/nii/out/{out_name}")
    center = out["rule"]["center"]
    if center is None:
        raise RuleError(f"лист {out_name}: центра нет (гейт не проходит ни при какой доле) — суждение, стоп")
    i = axis_index(book, f"nii.{key}")
    values = {("nii", key): float(center),
              ("valuation", "uncertainty", "axes", i, "low"): float(out["axis"]["low"]),
              ("valuation", "uncertainty", "axes", i, "high"): float(out["axis"]["high"])}
    return [{"file": TEMPLATE, "values": values}]


def sigma0_split(ctx) -> list[dict]:
    return _split(ctx, "sigma0_split", "sigma0_split_out.json")


def phi_split(ctx) -> list[dict]:
    return _split(ctx, "phi_split", "phi_split_out.json")


# ----------------------------------------------------------------------------------------------- дисконт за управление
def governance(ctx) -> list[dict]:
    """A-V7: медиана таблиц книги → вход листа → точка g = PV / (PV + медиана × N / 1000) → ключ к шагу листа; канал (а)
    и итог дисконта — ключ; числа строки `basis` канала (PV, точка, ключ) — из того же расчёта."""
    res = json_file(ctx, RESULTS)
    book = template(ctx)
    inp = ctx.yaml(GOV_INPUTS)
    fp = inp["quasi_fiscal"]["fixed_point"]
    fp["median_rub"] = float(res["headline"]["median"])
    fp["median_of"] = f"книга {res['book_version']}"
    gc = ctx.module(f"{EVIDENCE}/governance/governance_channels.py", "worlds_rules_governance")
    out = gc.compute(inp=inp, book=book, rec=ctx.record)
    qf = out["channels"]["quasi_fiscal"]
    comps = book["valuation"]["governance"]["components"]
    i = next(j for j, c in enumerate(comps) if c["id"] == "quasi_fiscal")
    m = re.fullmatch(r"(.* стоит около )(\d+)( млрд ₽ — )(\d+,\d+)( % оценки капитала на медиане выпуска, с округлением к "
                     r"шагу \d+,\d+ п\.п\. — )(\d+,\d+)( %.*)", comps[i]["basis"])
    if not m:
        raise RuleError("строка basis канала quasi_fiscal не в известном виде")
    basis = (m.group(1) + ru(qf["pv_bn"], 0) + m.group(3) + ru(100 * qf["fixed_point"]["share_of_v0"], 2) + m.group(5)
             + ru(100 * qf["value"], 2) + m.group(7))
    tpl = {("valuation", "governance", "discount"): float(out["total"]),
           ("valuation", "governance", "components", i, "value"): float(qf["value"]),
           ("valuation", "governance", "components", i, "basis"): basis}
    gov = {("quasi_fiscal", "fixed_point", "median_rub"): fp["median_rub"],
           ("quasi_fiscal", "fixed_point", "median_of"): fp["median_of"]}
    return [{"file": GOV_INPUTS, "values": gov}, {"file": TEMPLATE, "values": tpl}]


# ----------------------------------------------------------------------------------------------- гейты
NUM = r"([+−-]?\d+(?: \d{3})*(?:,\d+)?)"


def _rx(template: str) -> str:
    """Шаблон фразы объяснения → регулярное выражение: `{}` — число, пробел — любой пробел (перенос строки блока)."""
    parts = template.split("{}")
    return NUM.join(re.escape(p).replace(r"\ ", r"\s+") for p in parts)


def _n(x: float) -> str:
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else ru(x, 1)


def gate_texts(ctx) -> list[dict]:
    """Числа объяснений сработавших гейтов, которые есть в таблицах книги (`results.json`: сообщения гейтов, клетки,
    апостериорные режимы), в записи миров и в книге, — правилом; фразы и суждения — прежние. Чисел нет в таблицах (их
    правило не трогает — сверить вручную): ЧПМ, CoR и CIR года гайденса (guidance_gap), норматив клетки в квартале
    разрыва и его отставание от пола, выплата клетки (capital_gap), сравнения с прошлыми книгами. Правило пишет только
    гейты, состав клеток которых тот же, что в тексте (иначе фраза неверна — текст человеку)."""
    res = json_file(ctx, RESULTS)
    book = template(ctx)
    text = text_of(ctx, GATES)
    notes = ctx.yaml(GATES) or {}
    gates = {c["name"]: c for c in res["checks"] if c.get("kind") == "gate" and c.get("fired")}
    cells = res["cells"]
    rows = {w: [r for r in ctx.record["rows"] if r["world"] == w] for w in book["worlds"]["ids"]}
    post = {r["regime"]: r["posterior"] for r in res["next_report"]["expectation"]["by_regime"]}
    p_m = book["joint"]["world_prob"]["M"]
    pairs = []

    def sub(pattern: str, vals: list[str]) -> None:
        pairs.append(sub_groups(one(text, _rx(pattern)), vals))

    def nums(msg: str, pattern: str) -> list[float]:
        m = re.search(pattern, msg.replace(" ", " "))          # разряды в сообщениях ядра — неразрывным пробелом
        if not m:
            raise RuleError(f"сообщение гейта не в известном виде: {msg[:90]}")
        return [float(g.replace(" ", "").replace(",", ".").replace("−", "-")) for g in m.groups()]

    if "guidance_gap" in gates and "guidance_gap" in notes:
        lo, hi, g_lo, g_hi = nums(gates["guidance_gap"]["message"], r"ROE: ([\d,]+)…([\d,]+) % против гайденса ([\d,]+)…([\d,]+) %")
        sub("ROE года — {}–{} % против «22 %» (полоса {}–{} %)", [ru(lo, 1), ru(hi, 1), ru(g_lo, 1), ru(g_hi, 1)])
        if gates["guidance_gap"]["mass"] == 1.0:
            sub("во всех {} клетках", [str(len(cells))])
        sub("не опускает год ниже {} %", [ru(lo, 1)])
        if lo > g_hi:
            sub("расходится с гайденсом по ROE на {}–{} п.п. вверх", [ru(lo - g_hi, 2), ru(hi - g_hi, 2)])
    if "roe_k_homogeneity" in gates and "roe_k_homogeneity" in notes:
        n, h, m, gap = nums(gates["roe_k_homogeneity"]["message"],
                            r"N ([−-]?[\d,]+) п\.п\., H ([−-]?[\d,]+) п\.п\., M ([−-]?[\d,]+) п\.п\.; разрыв ([\d,]+) п\.п\.")
        sub("при нормализации ставок {} п.п., при высоких ставках надолго {} п.п., при рыночных ставках как есть {} "
            "п.п.; разрыв — {} п.п.", [ru(n, 2, sign=True), ru(h, 2, sign=True), ru(m, 2, sign=True), ru(gap, 2)])
        k = {c["world"]: c["k_t"] for c in cells}
        sub("ставок (ключевая {} %) маржа почти не выше, чем при нормализации (ключевая {} %), а стоимость капитала "
            "выше на {} п.п.", [_n(rows["M"][-1]["key_avg"]), _n(rows["N"][-1]["key_avg"]), ru(100 * (k["M"] - k["N"]), 1)])
    mc = [c for c in cells if c["world"] == "M" and c["regime"] == "crisis"]
    if "pb_by_world" in gates and "pb_by_world" in notes:
        bad = [c for c in cells if not book["checks"]["pb_by_world"][c["world"]][0] <= c["pb"]
               <= book["checks"]["pb_by_world"][c["world"]][1]]
        if {(c["world"], c["regime"], c["scenario"]) for c in bad} == {(c["world"], c["regime"], c["scenario"]) for c in mc}:
            sub("(вес {} % × {} % ≈ {} %):", [_n(100 * p_m), ru(100 * post["crisis"], 1), ru(100 * gates["pb_by_world"]["mass"], 1)])
            sub("оценка — {}–{} капитала при нижней границе коридора {} (у", [
                ru(min(c["pb"] for c in bad), 2), ru(max(c["pb"] for c in bad), 2), _n(book["checks"]["pb_by_world"]["M"][0])])
            sub("доходность терминала {}–{} % при стоимости капитала {} %.", [
                ru(100 * min(c["roe_t"] for c in bad), 1), ru(100 * max(c["roe_t"] for c in bad), 1), ru(100 * bad[0]["k_t"], 1)])
    if "m_crisis_vs_cbr" in gates and "m_crisis_vs_cbr" in notes:
        msg = gates["m_crisis_vs_cbr"]["message"].replace(" ", " ")
        got = re.findall(r"M/crisis/(\w+): минимум Н20\.0 ([\d,]+) %", msg)
        if {s for s, _ in got} == {"upper", "strict"}:
            share = sum(book["joint"]["reg_prob_given_regime"]["crisis"][s] for s in ("upper", "strict"))
            sub("(вес {} % × {} % × {} % ≈ {} %)", [_n(100 * p_m), ru(100 * post["crisis"], 1), _n(100 * share),
                                                    ru(100 * gates["m_crisis_vs_cbr"]["mass"], 1)])
            sub("минимум норматива Н20.0 — {} %, выше порога книги {} %", [
                ru(min(float(v.replace(",", ".")) for _, v in got), 2), _n(100 * book["checks"]["m_crisis_vs_cbr"]["n20_min"])])
    if "capital_gap" in gates and "capital_gap" in notes:
        msg = gates["capital_gap"]["message"]
        q, y = nums(msg, r"первый квартал — (\d) кв\. (\d{4})")
        runs, total, share = nums(msg, r"в полосе — в ([\d ]+) из ([\d ]+) прогонов \(масса таких клеток в среднем по всем "
                                       r"прогонам — ([\d,]+) %\)")
        sub("(вес {} %): в {} квартале {} года", [ru(100 * gates["capital_gap"]["mass"], 2), str(int(q)), str(int(y))])
        sub("в полосе пол задевают {} из {} прогонов, масса таких клеток в среднем по всем прогонам — {} %", [
            ru(runs, 0), ru(total, 0), ru(share, 1)])
    new_text = text
    for a, b in pairs:
        new_text = new_text.replace(a, b)
    new = yaml.safe_load(new_text)
    values = {(g, "explanation"): new[g]["explanation"] for g in notes if "explanation" in notes[g]}
    return [{"file": GATES, "replace": pairs, "values": values, "text_values": True}]


def gate_masses(ctx) -> list[dict]:
    """Ожидаемая масса объяснения гейта = масса сработавшего гейта в таблицах книги, 4 знака (запись с коридором массы —
    суждение о дрейфе, её правило не трогает). Тексты объяснений — суждение (числа — сверить с таблицами)."""
    res = json_file(ctx, RESULTS)
    fired = {c["name"]: c["mass"] for c in res["checks"] if c.get("fired") and c.get("kind") == "gate"}
    notes = ctx.yaml(GATES) or {}
    values = {(name, "expected_mass"): round(float(fired[name]), MASS_DECIMALS) for name, e in notes.items()
              if "expected_mass" in e and name in fired}
    return [{"file": GATES, "values": values, "decimals": {k: MASS_DECIMALS for k in values}}]
