"""Факты: схема узлов и происхождение каждого числа (data/facts/SCHEMA.md).

Каждое число — узел {v, src|calc}; null — с причиной; src ссылается на документ реестра
(anchor.json → documents или sources ручного файла) по префиксу sha256; базисы помечены;
поля, которые читает ядро (MODEL прил. B, жирным), на месте и не null; нет следов
частной машины в публичных файлах.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact          # быстрые (≈1 с): схема и провенанс идут в такте сервера

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
BUILT = ("anchor", "balance", "nii_books", "pnl_quarterly", "capital", "shares", "dividends",
         "bridge_mgmt_ifrs", "bridge_ras_ifrs", "mgmt_quarterly", "guidance")
MANUAL = ("calendar", "peers")
BASES = {"IFRS", "RAS", "mgmt", "mgmt↔IFRS", "RAS→IFRS", "mixed"}
BARE_NUMBER_KEYS = {"year", "dividends_payable_year"}
NODE_NUMBER_KEYS = {"tol"}             # числовые поля внутри узла: допуск точечного гайденса (SCHEMA.md §1)
SHA_RE = re.compile(r"sha256 ([0-9a-f]{16})")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load(name: str) -> dict:
    return json.loads((FACTS / f"{name}.json").read_text(encoding="utf-8"))


def is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def has_text(x) -> bool:
    return isinstance(x, str) and any(ch.isalnum() for ch in x)


def walk(obj, path=""):
    """(путь, значение, ключ) для всех значений дерева."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, v, k
            yield from walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{path}.{i}"
            yield p, v, None
            yield from walk(v, p)


def nodes(obj):
    for p, v, _ in walk(obj):
        if isinstance(v, dict) and "v" in v:
            yield p, v


def get(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


ALL = BUILT + MANUAL


@pytest.mark.parametrize("name", ALL)
def test_file_present_and_parses(name):
    path = FACTS / f"{name}.json"
    assert path.exists(), f"нет {path.name}"
    raw = path.read_bytes()
    assert b"\r\n" not in raw, f"{name}.json: переводы строк CRLF"
    data = json.loads(raw.decode("utf-8"))
    assert DATE_RE.match(str(data.get("as_of", ""))), f"{name}.json: нет as_of YYYY-MM-DD"


@pytest.mark.parametrize("name", BUILT + ("peers",))
def test_basis_declared(name):
    data = load(name)
    assert data.get("basis"), f"{name}.json: нет basis"
    if name == "peers":
        for b in data["banks"]:
            assert has_text(b.get("basis")), f"peers {b.get('ticker')}: нет basis"
        return
    assert data["basis"] in BASES, f"{name}.json: basis {data['basis']!r} вне {sorted(BASES)}"
    if data["basis"] == "mixed":
        # у каждого узла и блока верхнего уровня смешанного файла — свой basis; в истории — у каждого узла
        for k, v in data.items():
            if isinstance(v, dict):
                assert has_text(v.get("basis")), f"{name}.{k}: у блока/узла смешанного файла нет basis"
            elif isinstance(v, list):
                for i, row in enumerate(v):
                    for kk, n in row.items():
                        if isinstance(n, dict) and "v" in n:
                            assert has_text(n.get("basis")), f"{name}.{k}.{i}.{kk}: нет basis"


@pytest.mark.parametrize("name", ALL)
def test_every_number_is_a_node_with_provenance(name):
    data = load(name)
    bad = []
    for p, v in nodes(data):
        val = v["v"]
        if val is None:
            if not has_text(v.get("calc")):
                bad.append(f"{p}: null без причины в calc")
            continue
        if isinstance(val, list):
            if not (name == "guidance" and len(val) == 2 and all(is_num(x) for x in val) and val[0] <= val[1]):
                bad.append(f"{p}: список в v допустим только как диапазон гайденса")
        elif not is_num(val):
            bad.append(f"{p}: v не число ({val!r})")
        if not (has_text(v.get("src")) or has_text(v.get("calc"))):
            bad.append(f"{p}: число без src и calc")
    # голые числа вне узлов; внутри узла — только поля NODE_NUMBER_KEYS
    for p, v, key in walk(data):
        if is_num(v):
            parent_is_node = p.rsplit(".", 1)[-1] == "v" or re.search(r"\.v\.\d+$", p)
            if key in NODE_NUMBER_KEYS:
                owner = get(data, p.rsplit(".", 1)[0])
                if not (isinstance(owner, dict) and "v" in owner):
                    bad.append(f"{p}: поле {key} вне узла")
            elif not parent_is_node and key not in BARE_NUMBER_KEYS:
                bad.append(f"{p}: голое число {v} вне узла")
    assert not bad, "\n".join(bad[:30])


def registry(name: str) -> dict[str, dict]:
    if name in MANUAL:
        docs = load(name)["sources"]
    else:
        docs = load("anchor")["documents"]
    return {d["sha256"][:16]: d for d in docs}


@pytest.mark.parametrize("name", ALL)
def test_src_resolves_to_registry(name):
    data = load(name)
    reg = registry(name)
    bad = []
    for p, v, key in walk(data):
        if key == "src" and isinstance(v, str):
            shas = SHA_RE.findall(v)
            if not shas:
                bad.append(f"{p}: в src нет sha256 документа")
            for s in shas:
                if s not in reg:
                    bad.append(f"{p}: sha256 {s} нет в реестре")
    assert not bad, "\n".join(bad[:30])


@pytest.mark.parametrize("name", ("anchor", "calendar", "peers"))
def test_registry_entries(name):
    data = load(name)
    docs = data["documents"] if name == "anchor" else data["sources"]
    assert docs
    keys, shas = set(), set()
    for d in docs:
        assert re.fullmatch(r"[0-9a-f]{64}", d["sha256"]), d
        assert d["key"] not in keys, f"повтор ключа {d['key']}"
        keys.add(d["key"])
        assert d["sha256"][:16] not in shas, f"коллизия префикса sha256 {d['sha256'][:16]}"
        shas.add(d["sha256"][:16])
        f = d.get("file")
        if f is not None:
            assert not re.match(r"^([A-Za-z]:|/|\\)", f), f"{d['key']}: абсолютный путь"
            assert ".." not in f.split("/"), f"{d['key']}: путь с .."


@pytest.mark.docs                       # читает и data/facts/SCHEMA.md (коммит из одних *.md — docs.yml)
def test_no_private_traces():
    pats = {
        "абсолютный путь": re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|/home/|\\Users\\"),
        "почта": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
        "IPv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "токен": re.compile(r"(?i)(bearer\s+[a-z0-9._-]{10,}|token\s*[:=]\s*\S{10,}|t\.[A-Za-z0-9_-]{40,})"),
    }
    bad = []
    for path in sorted(FACTS.glob("*")):
        if path.suffix not in (".json", ".md"):
            continue
        text = path.read_text(encoding="utf-8")
        for what, rx in pats.items():
            m = rx.search(text)
            if m:
                bad.append(f"{path.name}: {what}: {m.group(0)[:40]}")
    assert not bad, "\n".join(bad)


# Поля, которые читает ядро (MODEL прил. B, жирным): узел с числом.
CORE_NUMBERS = {
    "balance": ["loans_ac_gross.commercial", "loans_ac_gross.project", "loans_ac_gross.mortgage_market",
                "loans_ac_gross.mortgage_subsidized", "loans_ac_gross.consumer", "loans_ac_gross.cards",
                "loans_ac_gross.auto", "loans_fvtpl", "allowance_ac", "securities.fvoci_debt", "securities.fvoci_repo",
                "securities.ac", "securities.ac_repo", "securities.fvtpl_bonds", "liquidity.cash", "liquidity.cbr",
                "liquidity.due_from_banks", "other_assets", "funds.retail_current", "funds.retail_term",
                "funds.corp_current", "funds.corp_term", "wholesale.banks", "wholesale.issued_debt",
                "wholesale.subordinated", "other_liabilities", "dividends_payable", "equity.total", "equity.at1",
                "equity.nci", "equity.bv_common", "prev_year_end.loans_corporate", "prev_year_end.loans_retail"],
    "nii_books": ["dia_q", "current_share_anchor", "key_avg_anchor_q"]
    + [f"books.{b}.rate_anchor" for b in ("corp_loans", "mortgage", "mortgage_sub", "retail_loans", "securities",
                                           "liquidity", "retail_current", "retail_term", "corp_funds", "wholesale")],
    "capital": ["at1.amount", "at1.coupon_annual", "at1.coupon_quarter", "t2_recognized", "basel.cet1_ratio",
                "basel.cet1", "basel.total_ratio", "basel.rwa", "n20_0.value", "n1_1_bank.value", "fvoci_reserve",
                "ofz_curve_anchor.1", "ofz_curve_anchor.3", "ofz_curve_anchor.5", "ofz_curve_anchor.10",
                "bank_base_capital"],
    "shares": ["issued_ordinary", "issued_preferred", "issued_total", "outstanding_ordinary",
               "outstanding_preferred", "outstanding_total"],
    "bridge_mgmt_ifrs": ["cor.value", "nim.value", "cir.value"],
    "bridge_ras_ifrs": ["nii.value", "iea.value"] + [f"{x}.by_quarter.{k}" for x in ("nii", "iea") for k in "1234"],
    "guidance": ["items.roe", "items.nim", "items.cor_max", "items.cir", "items.fee_growth", "items.n20_0",
                 "items.loan_growth.corporate", "items.loan_growth.retail"],
}
CORE_PLAIN = {
    "anchor": ["as_of", "period"],
    "capital": ["n20_0.as_of", "n20_0.pre_dividend", "n1_1_bank.as_of"],
    "bridge_mgmt_ifrs": ["window", "cor.method", "nim.method", "cir.method"],
    "bridge_ras_ifrs": ["nii.window", "iea.window", "iea.codes", "cor"],
    "guidance": ["year", "as_of"],
}


@pytest.mark.parametrize("name", sorted(CORE_NUMBERS))
def test_core_fields_present(name):
    data = load(name)
    bad = []
    for path in CORE_NUMBERS[name]:
        try:
            n = get(data, path)
        except (KeyError, IndexError, TypeError):
            bad.append(f"{name}.{path}: нет")
            continue
        if not (isinstance(n, dict) and "v" in n) or n["v"] is None:
            bad.append(f"{name}.{path}: не узел с числом")
    for path in CORE_PLAIN.get(name, []):
        try:
            get(data, path)
        except (KeyError, IndexError, TypeError):
            bad.append(f"{name}.{path}: нет поля")
    assert not bad, "\n".join(bad)


def test_pnl_quarters_cover_anchor_year_and_four_before():
    q = load("pnl_quarterly")["quarters"]
    anchor = load("anchor")["period"]
    y, k = int(anchor[:4]), int(anchor[-1])
    need = []
    for _ in range(max(4, k) + 4):
        need.append(f"{y}Q{k}")
        y, k = (y - 1, 4) if k == 1 else (y, k - 1)
    fields = ("nii", "fees_net", "llp_debt_fa", "insurance_net", "noncore_net", "opex", "pbt", "tax", "ni",
              "ni_shareholders", "misc_net")
    bad = [f"{p}.{f}" for p in need[:8] for f in fields if p not in q or q[p][f]["v"] is None]
    assert not bad, "нет чисел ОПУ: " + ", ".join(bad)


def test_bridge_window_matches_history():
    b = load("bridge_mgmt_ifrs")
    lo, hi = b["window"]
    for x in ("cor", "nim", "cir"):
        periods = [h["period"] for h in b[x]["history"]]
        assert lo in periods and hi in periods, f"{x}: окно вне истории"
        assert b[x]["method"] in ("additive", "ratio")


def test_bridge_history_carries_every_reported_quarter_of_the_anchor_year():
    """Год гайденса в упр. базисе собирается из упр. факта отчётных кварталов года якоря (MODEL §4.6,
    прил. B): история моста обязана нести каждый такой квартал числами — иначе ядро отказывает."""
    b = load("bridge_mgmt_ifrs")
    anchor = load("anchor")["period"]
    year, last = anchor[:4], int(anchor[-1])
    need = [f"{year}Q{k}" for k in range(1, last + 1)]
    for x in ("cor", "nim", "cir"):
        rows = {h["period"]: h for h in b[x]["history"]}
        bad = [p for p in need if p not in rows or any(rows[p][f]["v"] is None for f in ("mgmt", "engine", "gap"))]
        assert not bad, f"{x}: в истории моста нет отчётных кварталов года якоря {bad}"


def test_mgmt_annual_nodes_have_a_source_or_a_reason():
    """Годовые упр. метрики читает сборка выпуска (PAYLOAD §2 `history.annual`): узел — число документа
    с источником или null с причиной; год, раскрытый только годовым числом, null не бывает."""
    m = load("mgmt_quarterly")
    annual = m["annual"]
    first = min(m["quarters"])[:4]
    anchor = load("anchor")["period"]
    last_full = int(anchor[:4]) - (0 if anchor.endswith("Q4") else 1)     # последний год с годовым отчётом
    assert sorted(annual) == [str(y) for y in range(int(first), last_full + 1)], "годы annual — от начала ряда до последнего года"
    for y, rec in annual.items():
        assert set(rec) == {"nim", "cor", "cir", "roe"}, y
        for f, n in rec.items():
            assert has_text(n.get("src")) if n["v"] is not None else has_text(n.get("calc")), f"annual.{y}.{f}"
    # год без квартальных презентаций раскрыт годовыми числами — они и стоят в annual
    gap_years = {p[:4] for p, row in m["quarters"].items() if row["nim"]["v"] is None and row["cor"]["v"] is None}
    for y in sorted(gap_years):
        if all(m["quarters"][f"{y}Q{k}"]["nim"]["v"] is None for k in range(1, 5)):
            assert annual[y]["nim"]["v"] is not None and annual[y]["cor"]["v"] is not None, f"annual.{y}: годовые числа не собраны"


def test_mgmt_quarterly_gap_is_null_not_zero():
    q = load("mgmt_quarterly")["quarters"]
    for p in ("2022Q1", "2022Q2", "2022Q3"):
        assert q[p]["nim"]["v"] is None and "не" in q[p]["nim"]["calc"]


def test_pnl_key_avg_in_every_quarter():
    """Средняя ключевая квартала (флаг ni_jump, MODEL прил. B): узел в каждом квартале, доля или null с причиной."""
    q = load("pnl_quarterly")["quarters"]
    for p, row in q.items():
        n = row.get("key_avg")
        assert isinstance(n, dict) and "v" in n, f"{p}: нет key_avg"
        assert n["v"] is None or 0.0 < n["v"] < 0.5, f"{p}: key_avg не доля ({n['v']})"


def test_ras_cor_bridge_is_a_block_or_a_null_with_reason():
    """Мост CoR РСБУ → упр.: блок формата nii или null с причиной — не ноль (INTERFACES §3.2)."""
    cor = load("bridge_ras_ifrs")["cor"]
    if "v" in cor:
        assert cor["v"] is None and has_text(cor.get("calc")) and "T4" in cor["calc"]
    else:
        assert {"window", "quarters", "value", "by_quarter"} <= set(cor)
