# -*- coding: utf-8 -*-
"""Раскладка сжатия φ между кредитными книгами и средствами клиентов: ключ `nii.phi_split` — второй шаг правила
центров (MODEL §4.4–§4.5, §14.2; решение ведущего В12, книга 1.3). Первый шаг — `sigma0_split.py`.

Сжатие φ — ответ маржи на ставку мира: чем выше опорные ставки мира против мира H, тем уже маржа. Его объём задан
реализованной передачей A-N3 и от раскладки не зависит; доля p = `nii.phi_split` решает, кто его несёт:
  φ_A = p × φ               — спреды кредитных книг с `phi` сжимаются на φ_A × (опора мира − опора мира H);
  φ_L = (1 − p) × φ × A / L — средства клиентов с `phi` дорожают на φ_L × X_W (X_W — разность опорных ставок
                              кредитных книг мира W и мира H, взвешенная их долями на якоре).
При p = 1 (книга 1.2) всё сжатие лежит на кредитных ставках: в мире M корпоративный кредит стоит на 2,5 п.п. дешевле
ОФЗ 1 г., кредитный портфель приносит меньше балансирующих активов плюс стоимость риска — рост кредита разрушает
стоимость, остановка кредита в кризисе работает как плюс.

Правило центра (шаг 2): наибольшая доля p, при которой при `nii.sigma0_split` книги гейт `lt_spread_floor` проходит
во всех мирах и кредитная маржа стационара m_W неотрицательна в каждом мире; вниз до 0,01. Тест знака объёмных
эффектов на сетке клеток (снятие остановки кредита в кризисе и поправок роста режимов не снижает точку) считает ядро —
тест книги `tests/test_book_template.py`; лист держит его стационарный признак m_W ≥ 0.

Лист также показывает:
  • совместную область долей (σ0; φ), где гейт проходит во всех мирах, и ближайшую к (1; 1) точку сетки 0,01 —
    проверка, что последовательное правило даёт наименьшее отклонение от «всё на кредитных ставках»;
  • долю сжатия, которую несли кредитные ставки в истории: наклон a_b доходности книги к опоре в калибровке
    2018–2026 меньше единицы, 1 − a_b — сжатие спреда на единицу опоры; доля = Σ ω_b (1 − a_b) / φ — сомнение в самом
    правиле: она лежит вне области гейта и осью полосы не разыгрывается (строка `history_share` таблицы);
  • ось полосы — в области гейта: от нуля до наибольшей доли, при которой при `nii.sigma0_split` книги гейт
    проходит во всех мирах (вниз до 0,001); на всём прямоугольнике осей σ0 и φ гейт проходит (лист проверяет углы);
  • стоимость средств клиентов по мирам при долях книги — сторона, которая несёт остаток сжатия.

Запуск из корня репозитория (после calib_nii.py): python -B data/assumptions/evidence/nii/phi_split.py
→ out/phi_split_out.json; код выхода 1 — книга расходится с правилом.
"""
from __future__ import annotations

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import evlib  # noqa: E402
from sigma0_split import STEP, Stationary, floor_step  # noqa: E402

AXIS_STEP = 0.001              # шаг округления верха оси (область гейта у́же шага центров 0,01)
CALIB_BOOK = {"corp_loans": "corp_loans", "mortgage": "retail_market", "retail_loans": "retail_market"}
# книга ядра → книга листа калибровки (история не различает рыночную ипотеку и прочую розницу)
TOL = 1e-12


def ok(stn: Stationary, floors: dict | None, world: str, split: float, p: float) -> bool:
    """Гейт проходит в мире (floors — полы; None — гейт не проверяется) и кредитная маржа стационара неотрицательна."""
    sol = stn.solve(split, p)
    gate = floors is None or all(stn.s_eff(b, world, sol) >= floors[b] - TOL for b in stn.loans())
    return gate and stn.loan_margin(world, sol) >= -TOL


def max_p(stn: Stationary, floors: dict | None, world: str, split: float) -> float | None:
    """Наибольшая доля p ∈ [0; 1], при которой мир проходит; None — не проходит и при p = 0."""
    if ok(stn, floors, world, split, 1.0):
        return 1.0
    if not ok(stn, floors, world, split, 0.0):
        return None
    lo, hi = 0.0, 1.0                         # спред и маржа мира монотонны по p (φ_A линейна по p)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(stn, floors, world, split, mid) else (lo, mid)
    return lo


def nearest_to_all_assets(stn: Stationary, floors: dict) -> dict:
    """Точки сетки 0,01 × 0,01 долей (σ0; φ), где гейт проходит во всех мирах; ближайшая к (1; 1) по сумме и по
    евклидову расстоянию."""
    n = int(round(1 / STEP))
    feasible = []
    for i in range(n + 1):
        split = round(i * STEP, 6)
        for j in range(n + 1):
            p = round(j * STEP, 6)
            if all(ok(stn, floors, w, split, p) for w in stn.worlds):
                feasible.append((split, p))
            else:
                break                          # при данном split гейт мира с высокой опорой монотонен по p
        if not any(s == split for s, _ in feasible):
            break                              # и по split: дальше область пуста
    l1 = min(feasible, key=lambda x: ((1 - x[0]) + (1 - x[1]), -x[0]))
    l2 = min(feasible, key=lambda x: (1 - x[0]) ** 2 + (1 - x[1]) ** 2)
    return {"points": len(feasible), "max_sigma0_split": max(s for s, _ in feasible),
            "max_phi_split": max(p for _, p in feasible),
            "max_phi_split_at_sigma0_split_0": max(p for s, p in feasible if s == 0.0),
            "nearest_l1": list(l1), "nearest_l2": list(l2)}


def history_share(stn: Stationary, phi: float) -> dict:
    """Доля сжатия на кредитных ставках по наклонам истории: Σ ω_b (1 − a_b) / φ."""
    res = json.load(open(os.path.join(OUT, "results.json"), encoding="utf-8"))["books"]
    slopes = {b: {"a": res[c]["free"]["a_or_beta"], "se": res[c]["free"]["se"]["a"]} for b, c in CALIB_BOOK.items()}
    phi_a = sum(stn.omega[b] * (1 - slopes[b]["a"]) for b in stn.phi_assets)
    lo = sum(stn.omega[b] * (1 - slopes[b]["a"] - 2 * slopes[b]["se"]) for b in stn.phi_assets)
    hi = sum(stn.omega[b] * (1 - slopes[b]["a"] + 2 * slopes[b]["se"]) for b in stn.phi_assets)
    return {"slopes": slopes, "omega": {b: round(v, 4) for b, v in stn.omega.items()},
            "phi_assets_history": round(phi_a, 4), "share": round(phi_a / phi, 4),
            "share_2se": [round(lo / phi, 3), round(hi / phi, 3)],
            "by_book": {b: round((1 - slopes[b]["a"]) / phi, 3) for b in stn.phi_assets}}


FUNDS_COL = {"retail_current": "ifrs_retail_current", "retail_term": "ifrs_retail_term", "corp_funds": "ifrs_corp_funds"}


def _blend(stn: Stationary, cost: dict, current_share: float) -> float:
    """Средняя стоимость средств клиентов: средства ФЛ делятся на текущие и срочные долей `current_share`,
    веса ФЛ и ЮЛ — структура якоря."""
    weight = {"retail_current": stn.l_r * current_share, "retail_term": stn.l_r * (1 - current_share),
              "corp_funds": stn.w["corp_funds"]}
    return sum(weight[b] * cost[b] for b in weight) / sum(weight.values())


def funds_cost(stn: Stationary, sol: dict) -> dict:
    """Средняя стоимость средств клиентов в стационаре мира и её отношение к ключевой."""
    out = {}
    for w in stn.worlds:
        cost = {b: stn.liab_cost(b, w, sol["sigma0_liab"], sol["phi_liab"]) for b in stn.liabs}
        avg = _blend(stn, cost, stn.c_star(w))
        out[w] = {"key_pct": round(100 * stn.ref[w]["key"], 2), "funds_cost_pct": round(100 * avg, 2),
                  "to_key": round(avg / stn.ref[w]["key"], 3),
                  "by_book_pct": {b: round(100 * cost[b], 2) for b in FUNDS_COL},
                  "wholesale_pct": round(100 * cost["wholesale"], 2)}
    return out


def funds_history(stn: Stationary) -> dict:
    """Стоимость средств клиентов к ключевой в истории (ставки книг МСФО из `out/rates_panel.csv`, кварталы с раскрытыми
    ставками всех трёх книг; веса ФЛ и ЮЛ — структура якоря, доля текущих счетов — квартала)."""
    rows = []
    for r in csv.DictReader(open(os.path.join(OUT, "rates_panel.csv"), encoding="utf-8")):
        if not all(r[c] for c in FUNDS_COL.values()) or not r["fl_current_share"]:
            continue
        cost = {b: float(r[c]) for b, c in FUNDS_COL.items()}
        avg = _blend(stn, cost, float(r["fl_current_share"]) / 100.0)
        rows.append({"quarter": r["quarter"], "key_pct": float(r["key_avg"]), "funds_cost_pct": round(avg, 2),
                     "to_key": round(avg / float(r["key_avg"]), 3)})
    top = max(rows, key=lambda x: x["to_key"])
    high = [x for x in rows if x["key_pct"] >= 16.0]
    return {"quarters": len(rows), "from": rows[0]["quarter"], "to": rows[-1]["quarter"],
            "max_to_key": top["to_key"], "max_at": top["quarter"], "anchor": rows[-1],
            "key_16_plus": {"n": len(high), "to_key_min": min(x["to_key"] for x in high),
                            "to_key_max": max(x["to_key"] for x in high)},
            "rows": rows}


def compute() -> dict:
    book, rec = evlib.template(), evlib.record()
    stn = Stationary(book, rec)
    floors = {b: float(v) for b, v in book["checks"]["lt_spread_floor"].items()}
    split_book, p_book = float(book["nii"]["sigma0_split"]), stn.p_book
    worlds = stn.worlds
    per_world = {w: max_p(stn, floors, w, split_book) for w in worlds}
    exact = None if None in per_world.values() else min(per_world.values())
    center = None if exact is None else floor_step(exact)
    sol_book = stn.solve(split_book, p_book)
    hist = history_share(stn, sol_book["phi"])
    hist_share = floor_step(hist["share"])                               # доля по наклонам истории — вне области гейта
    axis_high = None if exact is None else floor_step(exact, AXIS_STEP)     # ось — в области гейта при доле σ0 книги
    margin_zero = {w: max_p(stn, None, w, split_book) for w in worlds}      # где кредитная маржа мира уходит в ноль
    table = {tag: stn.table(stn.solve(split_book, p), floors)
             for tag, p in (("all_on_loans", 1.0), ("history_share", hist_share), ("axis_high", axis_high or 0.0),
                            ("book", p_book))}
    residual = max(stn.solve(split_book, j / 20)["volume_residual"] for j in range(21))
    by_floor = {}
    for f in (0.0, 0.0025, 0.005):               # p при sigma0_split = 0 и другом поле корпоративной книги
        alt = dict(floors, **{b: f for b in floors if book["nii"]["books"][b]["sector"] == "corporate"})
        ms = [max_p(stn, alt, w, 0.0) for w in worlds]
        by_floor[f"{f:.4f}"] = None if None in ms else floor_step(min(ms))
    axis = next((a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.phi_split"]), None)
    bad = []
    if center is None or abs(p_book - center) > 1e-12:
        bad.append(f"nii.phi_split: книга {p_book} против правила {center}")
    if axis is None or [axis["low"], axis["high"]] != [0.0, axis_high]:
        bad.append(f"ось nii.phi_split: книга {axis and [axis['low'], axis['high']]} против [0.0, {axis_high}]")
    axis_s = next((a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.sigma0_split"]), None)
    corners = [(s_, p_) for s_ in ((axis_s["low"], axis_s["high"]) if axis_s else ()) for p_ in (0.0, axis_high or 0.0)]
    outside = [c for c in corners if not all(ok(stn, floors, w, *c) for w in worlds)]
    if not corners or outside:                   # запас спреда монотонен по обеим долям — достаточно углов
        bad.append(f"оси долей вне области гейта lt_spread_floor: углы (σ0; φ) {outside or 'нет оси sigma0_split'}")
    if p_book < 1 and not stn.phi_liabs:
        bad.append("nii.phi_split < 1, а пассивов с phi нет (MODEL §4.5)")
    if any(stn.books[b].get("balancing") for b in stn.phi_assets + stn.phi_liabs):
        bad.append("phi у балансирующей книги (MODEL прил. A)")
    if bool(stn.books["retail_current"].get("phi")) != bool(stn.books["retail_term"].get("phi")):
        bad.append("nii.books.retail_current / retail_term: phi обязан совпадать (MODEL §4.5)")
    if residual > 1e-12 or abs(sol_book["t_real"] - stn.t_star) > 1e-9:
        bad.append(f"раскладка меняет стационарный ЧПМ: невязка {residual:.2e}, T_real {sol_book['t_real']:.6f}")
    for tag in ("book", "axis_high"):
        low = {w: v for w, v in table[tag]["loan_margin_pp"].items() if v < 0}
        if low:
            bad.append(f"кредитная маржа стационара отрицательна ({tag}): {low}")
        if table[tag]["violations"] != {w: [] for w in worlds}:
            bad.append(f"гейт lt_spread_floor нарушен ({tag}): {table[tag]['violations']}")
    return {
        "rule": {"definition": "шаг 2 правила центров: наибольший phi_split, при котором при sigma0_split книги гейт "
                               "lt_spread_floor проходит во всех мирах и кредитная маржа стационара неотрицательна; "
                               "округление вниз до 0,01",
                 "sigma0_split_book": split_book, "max_phi_split_by_world": {w: None if v is None else round(v, 5)
                                                                           for w, v in per_world.items()},
                 "max_phi_split_exact": None if exact is None else round(exact, 5), "center": center, "book": p_book,
                 "max_phi_split_with_margin_only": {w: None if v is None else round(v, 4) for w, v in margin_zero.items()}},
        "joint_region": nearest_to_all_assets(stn, floors),
        "stationary": {"reference_world": stn.h, "phi": sol_book["phi"], "phi_assets": sol_book["phi_assets"],
                       "phi_liab": sol_book["phi_liab"], "weight_assets_phi": stn.a_phi,
                       "weight_liabilities_phi": stn.l_phi, "assets_phi": stn.phi_assets,
                       "liabilities_phi": stn.phi_liabs, "x_pp": {w: round(100 * stn.x(w), 3) for w in worlds},
                       "volume_residual_max": residual, "t_real": sol_book["t_real"], "t_star": stn.t_star,
                       "cor_lt_engine": stn.cor_lt, "kappa": stn.kappa, "real_rate_lt": stn.real,
                       "securities_share_of_liquid": round(stn.sec, 4)},
        "floors": floors, "table": table,
        "funds_cost_at_book": funds_cost(stn, sol_book),
        "funds_cost_all_on_loans": funds_cost(stn, stn.solve(split_book, 1.0)),
        "funds_cost_history": funds_history(stn),
        "history": hist,
        "axis": {"low": 0.0, "high": axis_high,
                 "high_rule": "область гейта: наибольший phi_split, при котором при sigma0_split книги гейт "
                              "lt_spread_floor проходит во всех мирах; вниз до 0,001",
                 "corners_in_gate_region": [list(c) for c in corners],
                 "book": axis and [axis["low"], axis["high"]]},
        "history_share": {"value": hist_share,
                          "rule": "доля сжатия на кредитных ставках по наклонам доходностей кредитных книг к опоре в "
                                  "калибровке истории: Σ ω_b (1 − a_b) / φ; вниз до 0,01 — вне области гейта, осью "
                                  "полосы не разыгрывается",
                          "gate_violations": table["history_share"]["violations"]},
        "center_at_sigma0_split_0_by_corporate_floor": by_floor,
        "book_mismatches": bad,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    evlib.write_json(evlib.Path(OUT) / "phi_split_out.json", res)
    r, s, j = res["rule"], res["stationary"], res["joint_region"]
    print(f"φ = {s['phi']:.4f}; вес кредитных книг со сжатием A = {s['weight_assets_phi']:.3f}, средств клиентов "
          f"L = {s['weight_liabilities_phi']:.3f}; X_W, п.п.: {s['x_pp']}; невязка объёма сжатия {s['volume_residual_max']:.1e}")
    for tag, t in res["table"].items():
        print(f"{tag}: phi_split {t['phi_split']:.2f}  φ_A {t['phi_assets']:.4f}  φ_L {t['phi_liab']:.4f}  добавка к "
              f"стоимости средств клиентов, п.п.: {t['liability_addon_pp']}")
        for b, row in t["lt_spread"].items():
            print(f"   {b:13s} пол {100 * res['floors'][b]:.2f}  s_eff " +
                  "  ".join(f"{w} {100 * v:+.3f}" for w, v in row.items()))
        print("   кредитная маржа m_W, п.п.:", t["loan_margin_pp"], " нарушения:",
              {w: v for w, v in t["violations"].items() if v} or "нет")
    print(f"правило (sigma0_split {r['sigma0_split_book']}): по мирам {r['max_phi_split_by_world']} → "
          f"{r['max_phi_split_exact']} → центр {r['center']}; книга {r['book']}; только по марже: "
          f"{r['max_phi_split_with_margin_only']}")
    print(f"совместная область: точек сетки {j['points']}; σ0 ≤ {j['max_sigma0_split']}, φ ≤ {j['max_phi_split']}; "
          f"ближайшая к (1; 1): по сумме {j['nearest_l1']}, евклидова {j['nearest_l2']}")
    h = res["history"]
    print(f"история: наклоны {h['slopes']}; сжатие кредитных ставок {h['phi_assets_history']:.3f} на единицу опоры → "
          f"доля {h['share']:.3f} (±2 ст. ош.: {h['share_2se']}); по книгам {h['by_book']} — вне области гейта: "
          f"{ {w: v for w, v in res['history_share']['gate_violations'].items() if v} }")
    print(f"ось полосы (область гейта при доле σ0 книги): {res['axis']['low']} … {res['axis']['high']}; углы прямоугольника "
          f"осей (σ0; φ), где гейт проверен: {res['axis']['corners_in_gate_region']}")
    for tag, name in (("funds_cost_at_book", "книга"), ("funds_cost_all_on_loans", "всё сжатие на кредитах")):
        print(f"средства клиентов в стационаре ({name}): " + "; ".join(
            f"{w} {v['funds_cost_pct']:.2f} % при ключевой {v['key_pct']:g} % ({v['to_key']:.2f} ключевой; по книгам "
            f"{v['by_book_pct']})" for w, v in res[tag].items()))
    fh = res["funds_cost_history"]
    print(f"средства клиентов в истории {fh['from']}–{fh['to']} ({fh['quarters']} кварталов): максимум {fh['max_to_key']:.2f} "
          f"ключевой ({fh['max_at']}); при ключевой от 16 % — {fh['key_16_plus']['to_key_min']:.2f}–"
          f"{fh['key_16_plus']['to_key_max']:.2f} ({fh['key_16_plus']['n']} кварталов); якорь {fh['anchor']['to_key']:.2f}")
    print("phi_split при sigma0_split = 0 и поле корпоративной книги:", res["center_at_sigma0_split_0_by_corporate_floor"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
