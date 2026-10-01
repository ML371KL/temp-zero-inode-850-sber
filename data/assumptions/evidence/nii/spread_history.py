# -*- coding: utf-8 -*-
"""История спреда кредитных книг к опоре, 2019–2026: подтверждение полов гейта `lt_spread_floor`
(`checks.lt_spread_floor`, A-X8; MODEL §4.5, §14.2; решение ведущего В20).

Пол — нижняя граница эффективного LT-спреда кредитной книги к её опоре в СТАЦИОНАРЕ мира: кредит не дешевле
госбумаги того же срока плюс минимальная премия. История стационара не знает, поэтому лист меряет спред тремя
способами и говорит, какой из них отвечает на вопрос о поле:

  1. ровная опора — кварталы, где размах опоры книги за 4 квартала не больше 1 п.п.: портфель успел подстроиться,
     спред «доходность − опора» ближе всего к стационарному. Пол подтверждён, если он не выше МИНИМУМА таких
     кварталов; рядом — медиана (сколько запаса у пола против обычного уровня);
  2. с поправкой на лаг книги — доходность минус опора, усреднённая весами ρ(1 − ρ)^k (ρ книги): все кварталы, отдельно
     при опоре ниже и не ниже 12 % — показывает, как спред сжимается при высоких ставках;
  3. сырой спред «доходность − опора того же квартала» — справочно: при движении опоры его двигает лаг портфеля.

Доходности — управленческие, из презентаций банка (`out/rates_panel.csv`: кредиты ЮЛ; кредиты ФЛ — одна доходность на
рыночную ипотеку и прочую розницу, поэтому у книг `mortgage` и `retail_loans` история одна); опоры — ОФЗ 1 г. и 3 г.
(КБД Мосбиржи, средние за квартал).

Запуск из корня репозитория (после calib_nii.py): python -B data/assumptions/evidence/nii/spread_history.py
→ out/spread_history_out.json, out/spread_history.csv; код выхода 1 — пол книги выше минимума истории при ровной опоре.
"""
from __future__ import annotations

import csv
import io
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import evlib  # noqa: E402
import nii_lib as L  # noqa: E402

STABLE_RANGE_PP = 1.0          # «ровная опора»: размах опоры книги за 4 квартала, п.п.
HIGH_RATE_PP = 12.0            # граница «высоких ставок», % (опора книги)
HISTORY_FROM = "2019Q1"
YIELD_COL = {"corporate": "corp_loan_yield_mgmt", "mortgage": "retail_loan_yield_mgmt",
             "retail_other": "retail_loan_yield_mgmt"}      # упр. доходности презентаций: ЮЛ; ФЛ — одна на всю розницу
CSV_HEAD = ("book", "quarter", "yield_mgmt_pct", "ref", "ref_pct", "spread_pp", "ref_range_4q_pp", "flat_ref",
            "spread_lag_adjusted_pp")


def _lt(spread) -> float:
    return float(spread["LT"]) if isinstance(spread, dict) else float(spread)


def _stats(values: list[float]) -> dict:
    return {"n": len(values), "min_pp": min(values), "median_pp": round(st.median(values), 2), "max_pp": max(values)}


def book_history(spec: dict, panel: list[dict], ofz: dict) -> dict:
    """Спред упр. доходности портфеля к опоре книги по кварталам с HISTORY_FROM: сырой, при ровной опоре, с поправкой
    на лаг книги."""
    quarters = sorted(ofz)
    ref, rho, col = spec["ref"], float(spec["rho"]), YIELD_COL[spec["sector"]]
    rows = []
    for r in panel:
        q = r["quarter"]
        if q < HISTORY_FROM or not r[col]:
            continue
        i = quarters.index(q)
        window = [ofz[quarters[j]][ref] for j in range(i - 3, i + 1)]
        num = den = 0.0
        for k in range(i + 1):
            wgt = rho * (1 - rho) ** k
            num, den = num + wgt * ofz[quarters[i - k]][ref], den + wgt
        y, x = float(r[col]), ofz[q][ref]
        rng = round(max(window) - min(window), 2)
        rows.append({"quarter": q, "yield_mgmt_pct": y, "ref_pct": round(x, 2), "spread_pp": round(y - x, 2),
                     "ref_range_4q_pp": rng, "flat_ref": rng <= STABLE_RANGE_PP,
                     "spread_lag_adjusted_pp": round(y - num / den, 2)})
    flat = [r for r in rows if r["flat_ref"]]
    worst = min(flat, key=lambda r: r["spread_pp"])
    low = [r["spread_lag_adjusted_pp"] for r in rows if r["ref_pct"] < HIGH_RATE_PP]
    high = [r["spread_lag_adjusted_pp"] for r in rows if r["ref_pct"] >= HIGH_RATE_PP]
    return {"ref": ref, "rho": rho, "yield": col, "quarters": len(rows), "from": rows[0]["quarter"],
            "to": rows[-1]["quarter"],
            "flat_ref": dict(_stats([r["spread_pp"] for r in flat]), min_at=worst["quarter"],
                             quarters=[{k: r[k] for k in ("quarter", "spread_pp", "ref_pct")} for r in flat]),
            "lag_adjusted": {"all": _stats([r["spread_lag_adjusted_pp"] for r in rows]),
                             "ref_below_12": _stats(low), "ref_12_plus": _stats(high)},
            "raw": _stats([r["spread_pp"] for r in rows]),
            "rows": rows}


def compute() -> dict:
    book = evlib.template()
    books = book["nii"]["books"]
    floors = {b: float(v) for b, v in book["checks"]["lt_spread_floor"].items()}
    ofz = L.ofz_quarterly()
    panel = list(csv.DictReader(open(os.path.join(OUT, "rates_panel.csv"), encoding="utf-8")))
    hist, verdict, bad = {}, {}, []
    for b, spec in books.items():
        if "sector" not in spec or not spec.get("phi"):
            continue                              # льготная ипотека: спред — компенсация государства, истории нет
        h = book_history(spec, panel, ofz)
        hist[b] = h
        floor_pp, flat = round(100 * floors[b], 4), h["flat_ref"]
        verdict[b] = {
            "floor_pp": floor_pp, "lt_spread_calibration_pp": round(100 * _lt(spec["spread"]), 3),
            "flat_ref_min_pp": flat["min_pp"], "flat_ref_median_pp": flat["median_pp"], "flat_ref_quarters": flat["n"],
            "confirmed": floor_pp <= flat["min_pp"] + 1e-9,
            "headroom_to_min_pp": round(flat["min_pp"] - floor_pp, 2),
            "floor_to_median": round(floor_pp / flat["median_pp"], 2),
            "calibration_below_flat_min": 100 * _lt(spec["spread"]) < flat["min_pp"],
            "lag_adjusted_median_ref_12_plus_pp": h["lag_adjusted"]["ref_12_plus"]["median_pp"],
            "floor_above_lag_adjusted_median_at_high_rates": floor_pp > h["lag_adjusted"]["ref_12_plus"]["median_pp"],
        }
        if not verdict[b]["confirmed"]:
            bad.append(f"checks.lt_spread_floor.{b}: пол {floor_pp:.2f} п.п. выше минимума истории при ровной опоре "
                       f"{flat['min_pp']:.2f} п.п. ({flat['min_at']})")
    unlisted = sorted(set(floors) - set(hist) - {b for b, s in books.items() if "sector" in s and not s.get("phi")})
    if unlisted:
        bad.append(f"checks.lt_spread_floor: полы без истории и не льготная ипотека — {unlisted}")
    return {"definition": {"flat_ref": f"размах опоры книги за 4 квартала не больше {STABLE_RANGE_PP:g} п.п.",
                           "lag_adjusted": "доходность − опора, усреднённая весами ρ(1 − ρ)^k (ρ книги)",
                           "high_rate": f"опора книги не ниже {HIGH_RATE_PP:g} %",
                           "rule": "пол подтверждён, если он не выше минимума кварталов с ровной опорой"},
            "floors": floors, "verdict": verdict, "history": hist, "book_mismatches": bad}


def write_csv(res: dict) -> None:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEAD)
    for b, h in res["history"].items():
        for r in h["rows"]:
            w.writerow([b, r["quarter"], r["yield_mgmt_pct"], h["ref"], r["ref_pct"], r["spread_pp"],
                        r["ref_range_4q_pp"], int(r["flat_ref"]), r["spread_lag_adjusted_pp"]])
    with open(os.path.join(OUT, "spread_history.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    evlib.write_json(evlib.Path(OUT) / "spread_history_out.json", res)
    write_csv(res)
    for b, h in res["history"].items():
        v, la, f = res["verdict"][b], h["lag_adjusted"], h["flat_ref"]
        print(f"{b} (к {h['ref']}, {h['from']}–{h['to']}, кварталов {h['quarters']}): пол {v['floor_pp']:.2f} п.п.; "
              f"ровная опора ({f['n']} кв.): минимум {f['min_pp']:+.2f} ({f['min_at']}), медиана {f['median_pp']:+.2f}, "
              f"максимум {f['max_pp']:+.2f} → пол {'подтверждён' if v['confirmed'] else 'НЕ подтверждён'}, запас "
              f"{v['headroom_to_min_pp']:+.2f} п.п., {v['floor_to_median']:.2f} медианы")
        print(f"   с поправкой на лаг: все {la['all']['n']} кв. — минимум {la['all']['min_pp']:+.2f}, медиана "
              f"{la['all']['median_pp']:+.2f}; опора ниже {HIGH_RATE_PP:g} % ({la['ref_below_12']['n']} кв.) — "
              f"{la['ref_below_12']['min_pp']:+.2f} / {la['ref_below_12']['median_pp']:+.2f}; не ниже "
              f"({la['ref_12_plus']['n']} кв.) — {la['ref_12_plus']['min_pp']:+.2f} / {la['ref_12_plus']['median_pp']:+.2f}")
        print(f"   сырой спред: минимум {h['raw']['min_pp']:+.2f}, медиана {h['raw']['median_pp']:+.2f}; LT-спред "
              f"калибровки {v['lt_spread_calibration_pp']:+.3f} п.п."
              + (" — ниже минимума при ровной опоре" if v["calibration_below_flat_min"] else ""))
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
