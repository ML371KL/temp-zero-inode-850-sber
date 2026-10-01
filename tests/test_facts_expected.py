"""Факты из отчёта квартала для сухого прогона перезаякоривания (SCHEMA.md §4, MODEL §15.2).

`build_facts.py --expected` строит факты следующего квартала из закоммиченных фактов якоря и файла
отчёта квартала с ожиданием модели (`quarter-report/1` — тот же файл, что пишет и читает
`ops/tools/reanchor.py`) — без папки передачи. Проверяется на отчёте, собранном из самих фактов
(«квартал повторил якорь»): схема узлов и происхождение, тождества баланса, книг ЧПД и ОПУ, поля,
которые читает ядро, правило ставок якоря, неизменность мостов, отказы на негодном отчёте, запрет
записи в data/facts, совпадение перечня полей отчёта с reanchor.py.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_facts_anchor import B, BUILDER, FACTS, ROOT
from tests.test_facts_schema import CORE_NUMBERS, SHA_RE, get, has_text, is_num, nodes, walk

pytestmark = pytest.mark.tact

TOL = 1e-4          # млрд ₽: суммы строк против книг и итогов (округление узлов — 6 знаков)
OUTSIDE_BOOKS = -20.0   # млрд ₽: процентный доход вне книг в отчёте теста (не разнесён по книгам)


def prev_facts() -> dict[str, dict]:
    return {k: json.loads((FACTS / f"{k}.json").read_text(encoding="utf-8")) for k in B.BUILT}


def v(node) -> float:
    assert isinstance(node, dict) and node.get("v") is not None, node
    return float(node["v"])


def sample_report(prev: dict[str, dict]) -> dict:
    """Отчёт следующего квартала, собранный из фактов якоря: остатки книг прежние (дивиденды выплачены
    за счёт ликвидности), проценты книг — по ставкам якоря, ОПУ — строки квартала якоря, ЧПД — проценты
    книг минус взносы АСВ плюс доход вне книг. Тождества сходятся по построению."""
    bal, nb, cap = prev["balance"], prev["nii_books"], prev["capital"]
    period = B.q_next(prev["anchor"]["period"])
    days = B.q_days(period)
    books = {b: sum(v(B.dig(bal, p)) for p in paths) for b, paths in B.BOOK_LINES.items()}
    opening = dict(books)
    books["liquidity"] -= v(bal["dividends_payable"])
    interest = {b: v(nb["books"][b]["rate_anchor"]) * (opening[b] + books[b]) / 2 * days / 365 for b in B.BOOKS}
    dia = v(nb["dia_q"])
    nii = (sum(interest[b] for b in B.ASSET_BOOKS) - sum(interest[b] for b in B.LIABILITY_BOOKS) - dia + OUTSIDE_BOOKS)
    last = prev["pnl_quarterly"]["quarters"][prev["anchor"]["period"]]
    pnl = {k: v(last[k]) for k in B.PNL_LINES}
    shift = nii - pnl["nii"]                # ЧПД пересчитан — тот же сдвиг в прибыли до налога и чистой прибыли
    pnl.update(nii=nii, pbt=pnl["pbt"] + shift, ni=pnl["ni"] + shift, ni_shareholders=pnl["ni_shareholders"] + shift,
               misc_net=v(last["misc_net"]))
    sec = bal["securities"]
    return {
        "schema": B.REPORT_SCHEMA, "period": period, "note": "тест: квартал повторил якорь",
        "balance": {"books": books, "loans_fvtpl": v(bal["loans_fvtpl"]),
                    "fvoci": v(sec["fvoci_debt"]) + v(sec["fvoci_repo"]), "fvtpl_bonds": v(sec["fvtpl_bonds"]),
                    "allowance": v(bal["allowance_ac"]), "other_assets": v(bal["other_assets"]),
                    "other_liabilities": v(bal["other_liabilities"]), "dividends_payable": 0.0,
                    "bv_common": v(bal["equity"]["bv_common"]), "fvoci_reserve": v(cap["fvoci_reserve"])},
        "interest": interest, "dia": dia, "pnl": pnl,
        "capital": {"n20_0": v(cap["n20_0"]["value"]), "n20_pre_dividend": False, "n1_1_bank": v(cap["n1_1_bank"]["value"]),
                    "basel_cet1": v(cap["basel"]["cet1"]), "basel_rwa": v(cap["basel"]["rwa"]),
                    "bank_base_capital": v(cap["bank_base_capital"])},
        "market": {"ofz_curve": {t: v(cap["ofz_curve_anchor"][t]) for t in B.CURVE_NODES},
                   "key_avg": v(nb["key_avg_anchor_q"]), "price_index": 1.015},
        "mgmt": {"nim": 0.066, "cor": 0.011},
    }


SHA = hashlib.sha256(b"sample").hexdigest()


@pytest.fixture(scope="module")
def rolled():
    prev = prev_facts()
    before = copy.deepcopy(prev)
    report = sample_report(prev)
    files, checks = B.expected_facts(prev, report, name="report.json", sha=SHA)
    assert prev == before, "expected_facts изменила входные факты"
    return prev, report, files, checks


def test_checks_pass_and_anchor_moves(rolled):
    prev, report, files, checks = rolled
    assert checks and all(ok for _, ok, _ in checks), [c for c in checks if not c[1]]
    old, new = B.Anchor(prev["anchor"]["period"]), B.Anchor(report["period"])
    assert tuple(files) == B.BUILT
    assert files["anchor"]["period"] == new.period and files["anchor"]["as_of"] == new.date
    for name in B.BUILT:
        if name != "guidance":
            assert files[name]["as_of"] == new.date, name
    assert files["nii_books"]["period"] == new.period
    assert files["bridge_mgmt_ifrs"]["window"] == [B.BRIDGE_FIRST, new.period]
    assert max(files["pnl_quarterly"]["quarters"]) == new.period == max(files["mgmt_quarterly"]["quarters"])
    assert new.period in files["balance"]["history"] and files["capital"]["history"][-1]["period"] == new.period
    assert files["capital"]["n1_1_bank"]["as_of"] == new.form_date
    exp = files["anchor"]["expected"]
    assert exp["period"] == new.period and exp["from_anchor"] == old.period and exp["sha256"] == SHA
    assert "ОЖИДАЕМЫЙ" in files["anchor"]["note"], "ожидаемые факты помечены в anchor.note"


def test_every_number_is_a_node_and_points_to_the_report(rolled):
    _, report, files, _ = rolled
    registry = {d["sha256"][:16] for d in files["anchor"]["documents"]}
    assert SHA[:16] in registry, "файл отчёта — документ реестра"
    bad, from_report = [], 0
    for name, data in files.items():
        for p, n in nodes(data):
            if n["v"] is None:
                if not has_text(n.get("calc")):
                    bad.append(f"{name}.{p}: null без причины")
            elif not (has_text(n.get("src")) or has_text(n.get("calc"))):
                bad.append(f"{name}.{p}: число без src и calc")
            if SHA[:16] in (n.get("src") or ""):
                from_report += 1
                if "ожидание модели" not in (n.get("calc") or ""):
                    bad.append(f"{name}.{p}: число отчёта не помечено как ожидание модели")
        for p, x, key in walk(data):
            if key == "src" and isinstance(x, str):
                shas = SHA_RE.findall(x)
                if not shas or not set(shas) <= registry:
                    bad.append(f"{name}.{p}: src вне реестра")
    assert not bad, "\n".join(bad[:20])
    assert from_report > 60, "числа нового квартала ссылаются на файл отчёта"
    # новый квартал не несёт документов отчётности: числа якоря — либо из отчёта, либо расчёт
    for p, n in nodes(files["pnl_quarterly"]["quarters"][report["period"]]):
        assert n["v"] is None or SHA[:16] in (n.get("src") or "") or "src" not in n, p


def test_core_fields_are_numbers(rolled):
    _, _, files, _ = rolled
    bad = []
    for name, paths in CORE_NUMBERS.items():
        for path in paths:
            n = get(files[name], path)
            if not (isinstance(n, dict) and (is_num(n.get("v")) or isinstance(n.get("v"), list))):
                bad.append(f"{name}.{path}")
    assert not bad, f"поля, которые читает ядро, не числа: {bad}"
    row = files["pnl_quarterly"]["quarters"][files["anchor"]["period"]]
    assert all(is_num(row[k]["v"]) for k in B.PNL_LINES) and is_num(row["key_avg"]["v"]) and is_num(row["misc_net"]["v"])


def test_balance_identities_and_line_split(rolled):
    """Строки баланса в сумме дают книгу; строки, заданные отчётом (кредиты по СС, FVOCI, облигации по СС),
    стоят как в отчёте; остальное делится в долях прежнего якоря."""
    prev, report, files, _ = rolled
    bal, old, R = files["balance"], prev["balance"], report["balance"]
    for b, paths in B.BOOK_LINES.items():
        total = sum(v(B.dig(bal, p)) for p in paths)
        assert abs(total - R["books"][b]) < TOL, f"{b}: строки баланса ≠ остатку книги"
    assert abs(v(bal["loans_fvtpl"]) - R["loans_fvtpl"]) < TOL
    sec = bal["securities"]
    assert abs(v(sec["fvoci_debt"]) + v(sec["fvoci_repo"]) - R["fvoci"]) < TOL
    assert abs(v(sec["fvtpl_bonds"]) - R["fvtpl_bonds"]) < TOL
    for pair in (("loans_ac_gross.commercial", "loans_ac_gross.project"), ("securities.ac", "securities.ac_repo"),
                 ("securities.fvoci_debt", "securities.fvoci_repo"), ("funds.corp_current", "funds.corp_term"),
                 ("liquidity.cash", "liquidity.due_from_banks"), ("wholesale.banks", "wholesale.issued_debt")):
        a0, b0 = (v(B.dig(old, p)) for p in pair)
        a1, b1 = (v(B.dig(bal, p)) for p in pair)
        assert abs(a0 / b0 - a1 / b1) < 1e-6, f"доли строк {pair} изменились"
    L = bal["loans_ac_gross"]
    loans = sum(v(L[k]) for k in B.AC_LOAN_LINES)
    assert abs(loans - v(L["total"])) < TOL and abs(v(L["mortgage_market"]) + v(L["mortgage_subsidized"]) - v(L["mortgage"])) < TOL
    debt, liq = v(sec["debt_total"]), v(bal["liquidity"]["total"])
    assets = loans + v(bal["loans_fvtpl"]) - v(bal["allowance_ac"]) + debt + liq + v(bal["other_assets"])
    assert abs(assets - v(bal["total_assets"])) < TOL
    e = bal["equity"]
    assert abs(v(bal["total_liabilities"]) + v(e["total"]) - v(bal["total_assets"])) < 0.05, "пассивы + капитал ≠ активы"
    assert abs(v(e["total"]) - v(e["at1"]) - v(e["nci"]) - v(e["bv_common"])) < TOL
    assert abs(loans + v(bal["loans_fvtpl"]) + debt + liq - v(bal["iea"])) < TOL
    h = bal["history"][report["period"]]
    assert abs(v(h["iea"]) - v(bal["iea"])) < TOL and abs(v(h["bv_common"]) - v(e["bv_common"])) < TOL
    assert sec["fvtpl_equity"]["v"] is None and "нет данных" in sec["fvtpl_equity"]["calc"]


def test_anchor_rates_carry_the_income_outside_books(rolled):
    """Ставки якоря несут весь ЧПД (MODEL §15.2 п. 4): остаток ЧПД вне книг разнесён по книгам активов
    пропорционально их процентам; проценты книг и остаток — как в отчёте."""
    prev, report, files, _ = rolled
    nb = files["nii_books"]
    assert nb["rates_carry_residual"] is True
    days = v(nb["days"])
    income = sum(report["interest"][b] for b in B.ASSET_BOOKS)
    expense = sum(report["interest"][b] for b in B.LIABILITY_BOOKS)
    residual = report["pnl"]["nii"] - (income - expense - report["dia"])
    assert abs(residual - OUTSIDE_BOOKS) < 1e-6 and abs(v(nb["other_interest_net_q"]) - residual) < TOL
    carried = 0.0
    for b in B.BOOKS:
        r = nb["books"][b]
        assert abs(v(r["balance_open"]) - v(prev["nii_books"]["books"][b]["balance_close"])) < TOL
        assert abs(v(r["balance_close"]) - report["balance"]["books"][b]) < TOL
        assert abs(v(r["interest_q"]) - report["interest"][b]) < TOL
        scale = 1 + residual / income if b in B.ASSET_BOOKS else 1.0
        assert abs(v(r["interest_q"]) * scale / v(r["balance_avg"]) * 365 / days - v(r["rate_anchor"])) < 2e-6, b
        if b in B.ASSET_BOOKS:
            assert v(r["rate_anchor"]) < v(prev["nii_books"]["books"][b]["rate_anchor"]), f"{b}: отрицательный остаток снижает ставку"
        sign = 1 if b in B.ASSET_BOOKS else -1
        carried += sign * v(r["rate_anchor"]) * v(r["balance_avg"]) * days / 365
    assert abs(carried - v(nb["dia_q"]) - v(nb["nii_q"])) < 1e-2, "ставки якоря × средние остатки − АСВ = ЧПД отчёта"
    iea_avg = sum(v(nb["books"][b]["balance_avg"]) for b in B.ASSET_BOOKS)
    assert abs(v(nb["nii_q"]) * 365 / days / iea_avg - v(nb["nim_eng_q"])) < 1e-6
    # закоммиченный якорь правила не несёт — его ставки = проценты книги / средний остаток
    assert prev["nii_books"]["rates_carry_residual"] is (prev["anchor"]["period"] >= B.RATES_CARRY_FROM)


def test_pnl_and_capital(rolled):
    prev, report, files, _ = rolled
    row = files["pnl_quarterly"]["quarters"][report["period"]]
    s = sum(v(row[k]) for k in B.PNL_SUM)
    assert abs(s - v(row["pbt"])) < 0.05 and abs(v(row["pbt"]) + v(row["tax"]) - v(row["ni"])) < 0.15
    assert row["fee_income"]["v"] is None and "нет данных" in row["fee_income"]["calc"], "чего модель не ведёт — null с причиной, не 0"
    assert abs(v(row["dia"]) + report["dia"]) < TOL
    cq = int(v(files["capital"]["at1"]["coupon_quarter"]))
    quarter = int(report["period"][-1])
    assert (v(row["at1_coupon"]) == 0) == (quarter != cq)
    cap = files["capital"]
    assert abs(v(cap["basel"]["cet1"]) / v(cap["basel"]["rwa"]) - v(cap["basel"]["cet1_ratio"])) < 1e-6
    assert cap["n20_0"]["pre_dividend"] is False
    assert v(cap["fvoci_reserve"]) == v(files["balance"]["equity"]["fvoci_reserve"])
    for t in B.CURVE_NODES:
        assert v(cap["ofz_curve_anchor"][t]) == report["market"]["ofz_curve"][t]
    for k, n in cap.items():               # смешанный файл: у каждого блока и узла — basis
        if isinstance(n, dict):
            assert has_text(n.get("basis")), f"capital.{k}: нет basis"
    assert all(has_text(n.get("basis")) for n in cap["history"][-1].values() if isinstance(n, dict))
    old_q = prev["pnl_quarterly"]["quarters"]            # прежние кварталы не тронуты
    assert all(files["pnl_quarterly"]["quarters"][p] == old_q[p] for p in old_q)
    without = copy.deepcopy(report)         # misc_net в отчёте необязателен — остаток до прибыли до налога
    del without["pnl"]["misc_net"]
    files2, _ = B.expected_facts(prev, without, name="r.json", sha=SHA)
    assert abs(v(files2["pnl_quarterly"]["quarters"][report["period"]]["misc_net"]) - report["pnl"]["misc_net"]) < TOL


def test_bridges_and_mgmt_quarter(rolled):
    """Упр. ЧПМ и CoR — из отчёта; CIR без поля в отчёте — движок минус мост прежнего якоря (мост не двигается)."""
    prev, report, files, _ = rolled
    row = files["mgmt_quarterly"]["quarters"][report["period"]]
    assert v(row["nim"]) == report["mgmt"]["nim"] and v(row["cor"]) == report["mgmt"]["cor"] and SHA[:16] in row["nim"]["src"]
    for x in ("cor", "nim", "cir"):
        old, new = prev["bridge_mgmt_ifrs"][x], files["bridge_mgmt_ifrs"][x]
        assert int(v(new["n"])) == int(v(old["n"])) + 1
        last = new["history"][-1]
        assert last["period"] == report["period"] and abs(v(last["engine"]) - v(last["mgmt"]) - v(last["gap"])) < 1e-6
        assert abs(v(row[x]) - v(last["mgmt"])) < 1e-12
        gaps = [v(h["gap"]) for h in new["history"] if new is not None and h["gap"]["v"] is not None
                and files["bridge_mgmt_ifrs"]["window"][0] <= h["period"]]
        assert abs(sum(gaps) / len(gaps) - v(new["value"])) < 1e-6
    cir_old, cir_new = prev["bridge_mgmt_ifrs"]["cir"], files["bridge_mgmt_ifrs"]["cir"]
    assert abs(v(cir_new["value"]) - v(cir_old["value"])) < 2e-6, "упр. CIR через мост — значение моста прежнее"


def test_bad_reports_are_refused(rolled):
    prev, report, _, _ = rolled
    with pytest.raises(B.BuildError, match="следующего квартала"):
        B.expected_facts(prev, dict(report, period=prev["anchor"]["period"]), name="r.json", sha=SHA)
    with pytest.raises(B.BuildError, match="schema"):
        B.expected_facts(prev, dict(report, schema="v0"), name="r.json", sha=SHA)
    with pytest.raises(B.BuildError, match="as_of"):
        B.expected_facts(prev, dict(report, as_of="2020-01-01"), name="r.json", sha=SHA)
    holed = copy.deepcopy(report)
    del holed["interest"]["wholesale"]
    del holed["capital"]["n20_0"]
    holed["balance"]["allowance_ac"] = 1.0
    with pytest.raises(B.BuildError) as e:
        B.expected_facts(prev, holed, name="r.json", sha=SHA)
    for text in ("нет capital.n20_0", "нет interest.wholesale", "незнакомый ключ balance.allowance_ac"):
        assert text in str(e.value)
    typed = copy.deepcopy(report)
    typed["pnl"]["nii"] = "1066,9"
    with pytest.raises(B.BuildError, match="pnl.nii"):
        B.expected_facts(prev, typed, name="r.json", sha=SHA)
    flag = copy.deepcopy(report)
    flag["capital"]["n20_pre_dividend"] = 0
    with pytest.raises(B.BuildError, match="n20_pre_dividend"):
        B.expected_facts(prev, flag, name="r.json", sha=SHA)
    broken = copy.deepcopy(report)          # баланс не сходится — сверка красная, код выхода 1
    broken["balance"]["bv_common"] += 100.0
    _, checks = B.expected_facts(prev, broken, name="r.json", sha=SHA)
    assert any(not ok and "активы" in name for name, ok, _ in checks)


def _flat(obj, path=""):
    if isinstance(obj, dict):
        for k, x in obj.items():
            yield from _flat(x, f"{path}.{k}" if path else k)
    else:
        yield path, obj


def test_report_export_round_trip(tmp_path, rolled):
    """Отчёт квартала из фактов (`--report`) — вход reanchor.py на настоящем отчёте: из закоммиченных фактов
    он годен по составу и сходится тождествами, а путь «отчёт → факты → отчёт» возвращает исходные числа."""
    prev, report, files, _ = rolled
    committed = B.report_from_facts(prev, source="тест")
    B.check_report_shape(committed)
    bal = committed["balance"]
    assets = sum(bal["books"][b] for b in B.ASSET_BOOKS) - bal["allowance"] + bal["other_assets"]
    claims = (sum(bal["books"][b] for b in B.LIABILITY_BOOKS) + bal["other_liabilities"] + bal["dividends_payable"]
              + bal["bv_common"] + bal["at1"] + bal["nci"])
    assert abs(assets - claims) < 0.05 and committed["period"] == prev["anchor"]["period"]
    assert abs(sum(committed["pnl"][k] for k in B.PNL_SUM) - committed["pnl"]["pbt"]) < 0.05
    back = dict(_flat(B.report_from_facts(files, source="тест")))
    for path, value in _flat(report):
        if path in ("note", "source", "market.price_index"):
            continue
        assert path in back, f"в отчёте из фактов нет {path}"
        same = abs(back[path] - value) < 2e-6 * max(1.0, abs(value)) if is_num(value) else back[path] == value
        assert same, f"{path}: {value} → {back[path]}"
    out = tmp_path / "report.json"
    res = cli("--report", str(out), "--facts", str(FACTS))
    assert res.returncode == 0, res.stderr[-800:]
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == B.REPORT_SCHEMA
    assert cli("--report", str(out), "--facts", str(FACTS), "--anchor", "2020Q1").returncode == 1


def test_report_fields_match_the_reanchor_tool():
    """Один файл отчёта — два инструмента: перечень разделов и полей у сборщика фактов тот же, что у
    ops/tools/reanchor.py (читается из его исходника — без импорта ядра)."""
    path = ROOT / "ops" / "tools" / "reanchor.py"
    if not path.exists():
        pytest.skip("нет ops/tools/reanchor.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        if target in ("SCHEMA", "TOP_REQUIRED", "TOP_OPTIONAL", "SECTIONS", "CURVE_NODES"):
            consts[target] = ast.literal_eval(value)
    assert set(consts) == {"SCHEMA", "TOP_REQUIRED", "TOP_OPTIONAL", "SECTIONS", "CURVE_NODES"}, sorted(consts)
    assert consts["SCHEMA"] == B.REPORT_SCHEMA
    assert (set(consts["TOP_REQUIRED"]), set(consts["TOP_OPTIONAL"])) == (set(B.REPORT_TOP[0]), set(B.REPORT_TOP[1]))
    assert set(consts["CURVE_NODES"]) == set(B.CURVE_NODES)
    theirs = {k: (set(req), set(opt)) for k, (req, opt) in consts["SECTIONS"].items()}
    ours = {k: (set(req), set(opt)) for k, (req, opt) in B.REPORT_SECTIONS.items()}
    assert theirs == ours, "перечень полей отчёта квартала разошёлся с reanchor.py"


def _same(x, y, tol=2e-6) -> bool:
    if isinstance(x, dict) or isinstance(y, dict):
        return isinstance(x, dict) and isinstance(y, dict) and set(x) == set(y) and all(_same(x[k], y[k], tol) for k in x)
    if is_num(x) and is_num(y):
        return abs(x - y) <= tol * max(1.0, abs(x))
    return x == y


def test_core_sees_the_same_anchor_as_the_reanchor_candidate(tmp_path, rolled):
    """Канон сборщика и рабочие факты кандидата reanchor.py — из одного файла отчёта: всё, что ядро берёт
    из фактов якоря (стартовое состояние, ставки, ОПУ, капитал, кривая), и значения мостов совпадают."""
    import dataclasses
    import importlib.util
    path = ROOT / "ops" / "tools" / "reanchor.py"
    if not path.exists():
        pytest.skip("нет ops/tools/reanchor.py")
    book_mod = pytest.importorskip("model.book")
    spec = importlib.util.spec_from_file_location("reanchor_under_facts_test", path)
    tool = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tool
    spec.loader.exec_module(tool)
    prev, report, files, _ = rolled
    facts = book_mod.load_facts(FACTS)
    book = book_mod.load_book(facts=facts)
    if str(book.get("meta.first_period")) != report["period"]:
        pytest.skip("книга и факты не пара: первый прогнозный квартал книги — не следующий за якорем фактов")
    theirs = tool.facts_after(book, facts, report)
    for name, data in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for name in B.MANUAL:
        (tmp_path / f"{name}.json").write_bytes((FACTS / f"{name}.json").read_bytes())
    mine = book_mod.load_facts(tmp_path)
    books = book.get("nii.books")
    a, b = book_mod.anchor_facts(theirs, books), book_mod.anchor_facts(mine, books)
    bad = [f.name for f in dataclasses.fields(a)
           if not _same(dataclasses.asdict(a)[f.name], dataclasses.asdict(b)[f.name])]
    assert not bad, f"ядро прочтёт разное из кандидата reanchor и из канона сборщика: {bad}"
    for x in ("cor", "nim", "cir"):
        assert abs(theirs.files["bridge_mgmt_ifrs"][x]["value"]["v"] - v(files["bridge_mgmt_ifrs"][x]["value"])) < 2e-6, x


def cli(*args: str) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("BANK_HANDOFF_DIR", None)
    return subprocess.run([sys.executable, "-B", str(BUILDER), *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8")


def test_cli_writes_only_to_out_and_never_to_facts(tmp_path, rolled):
    _, report, files, _ = rolled
    rep = tmp_path / "report.json"
    rep.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in FACTS.glob("*.json")}
    assert cli("--expected", str(rep)).returncode == 1, "без --out — отказ"
    refused = cli("--expected", str(rep), "--out", str(FACTS))
    assert refused.returncode == 1 and "data/facts" in refused.stderr
    out = tmp_path / "facts"
    res = cli("--expected", str(rep), "--out", str(out))
    assert res.returncode == 0, res.stdout[-1500:] + res.stderr[-1500:]
    assert "СБОЙ" not in res.stdout
    assert {p.name: p.read_bytes() for p in FACTS.glob("*.json")} == before, "data/facts изменён"
    assert sorted(p.stem for p in out.glob("*.json")) == sorted(B.BUILT + B.MANUAL)
    for name in B.MANUAL:                   # ручные файлы — как есть: каталог читается ядром целиком
        assert (out / f"{name}.json").read_bytes() == (FACTS / f"{name}.json").read_bytes()
    written = json.loads((out / "balance.json").read_text(encoding="utf-8"))
    assert written["as_of"] == files["balance"]["as_of"]
    assert b"\r\n" not in (out / "balance.json").read_bytes()
    assert cli("--expected", str(rep), "--out", str(out), "--check").returncode == 0
    assert cli("--expected", str(rep), "--out", str(out), "--anchor", "2020Q1").returncode == 1


def test_core_reads_the_expected_anchor(tmp_path, rolled):
    """Ядро читает каталог ожидаемых фактов как обычные факты (стартовое состояние М§4.1)."""
    book_mod = pytest.importorskip("model.book")
    _, report, files, _ = rolled
    for name, data in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for name in B.MANUAL:
        (tmp_path / f"{name}.json").write_bytes((FACTS / f"{name}.json").read_bytes())
    facts = book_mod.load_facts(tmp_path)
    af = book_mod.anchor_facts(facts, {b: {} for b in B.BOOKS})
    assert af.period == report["period"]
    for b in B.BOOKS:
        assert abs(af.balances[b] - report["balance"]["books"][b]) < TOL
        assert abs(af.rates[b] - v(files["nii_books"]["books"][b]["rate_anchor"])) < 1e-9
    assert abs(af.fv_loans - report["balance"]["loans_fvtpl"]) < TOL
    assert abs(af.fvoci - report["balance"]["fvoci"]) < TOL and abs(af.fvtpl_bonds - report["balance"]["fvtpl_bonds"]) < TOL
    assert abs(af.bv - report["balance"]["bv_common"]) < TOL and af.dividends_payable == 0.0
    assert af.n20_pre_dividend is False and af.pnl[report["period"]]["ni_shareholders"] is not None
