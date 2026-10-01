# -*- coding: utf-8 -*-
"""Полы и требования по капиталу из траекторий книги против регуляторного графика этапа 1 (MODEL §3.3, §4.12).

floor20_s,Y = capital.minimum.n20_0 + conservation + sifi + ccyb;  floor11_s,Y = capital.minimum.n1_1 + те же надбавки
(Н20.1 группы; прокси — Н1.1 банка);  req20 = max(dividends.policy.threshold, floor20 + capital.mgmt_buffer.n20_0).
Траектории книги читаются по годам правилом MODEL §0.4 (ключ года, интерполяция, сход к LT к LT_from, плоско назад).
График этапа 1 (inputs/stage1/regulation/schedule.yaml, сценарии A/B/C, фазировка «постепенно») — 220-И пп. 2.2,
3.2, 3.4, 3.5; ОФС ЦБ 05.2026 с. 63; доклад ЦБ о СЗКО 22.05.2025; отчёт 20.02.2026 (источники — в самом графике).
Запуск из корня репозитория: python -B data/assumptions/evidence/regulation/floors.py → out/floors_out.json
"""
from __future__ import annotations

import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = os.path.join(HERE, "inputs", "stage1", "regulation", "schedule.yaml")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

YEARS = list(range(2026, 2032))
STAGE1 = {"schedule": "A_po_grafiku", "upper": "B_verkhnyaya_gruppa", "strict": "C_zhestkiy"}


def year_value(traj, year: int) -> float:
    """Значение траектории книги в году (MODEL §0.4 на уровне лет)."""
    if not isinstance(traj, dict):
        return float(traj)
    pts = sorted((int(k), float(v)) for k, v in traj.items() if k not in ("LT", "LT_from"))
    if str(year) in traj:
        return float(traj[str(year)])
    if not pts:
        return float(traj["LT"])
    if year < pts[0][0]:
        return pts[0][1]
    for (y0, v0), (y1, v1) in zip(pts, pts[1:]):
        if y0 < year < y1:
            return v0 + (v1 - v0) * (year - y0) / (y1 - y0)
    last_y, last_v = pts[-1]
    if "LT" not in traj:
        return last_v
    lt, lt_from = float(traj["LT"]), traj.get("LT_from")
    if lt_from is None or year >= int(lt_from):
        return lt
    return last_v + (lt - last_v) * (year - last_y) / (int(lt_from) - last_y)


def compute() -> dict:
    book = evlib.template()
    cap = book["capital"]
    thr, buf = book["dividends"]["policy"]["threshold"], cap["mgmt_buffer"]["n20_0"]
    sched = yaml.safe_load(open(SCHEDULE, encoding="utf-8"))["schedule"]
    out: dict = {"book": {}, "stage1": {}, "mismatches": []}
    for s in cap["reg_scenarios"]["ids"]:
        sc = cap["reg_scenarios"][s]
        rows = {}
        for y in YEARS:
            add = sum(year_value(sc[k], y) for k in ("conservation", "sifi", "ccyb"))
            f20 = round(cap["minimum"]["n20_0"] + add, 5)
            f11 = round(cap["minimum"]["n1_1"] + add, 5)
            rows[str(y)] = {"floor20": f20, "floor11": f11, "req20": round(max(thr, f20 + buf), 5),
                            "deduction_n20_0": year_value(sc["deduction_pp"]["n20_0"], y)}
            st1 = sched[STAGE1[s]]["gradual"][y]["group_floor"]
            out["stage1"].setdefault(s, {})[str(y)] = {"floor20": st1["N20_0"] / 100, "floor11": st1["N20_1"] / 100}
            for k, key in (("floor20", "N20_0"), ("floor11", "N20_1")):
                if abs(rows[str(y)][k] - st1[key] / 100) > 1e-9:
                    out["mismatches"].append(f"{s} {y} {k}: книга {rows[str(y)][k]} против графика {st1[key] / 100}")
        out["book"][s] = rows
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    evlib.write_json(evlib.Path(OUT) / "floors_out.json", res)
    for s, rows in res["book"].items():
        print(f"{s:9s} пол Н20.0: " + "  ".join(f"{y}:{100 * r['floor20']:.2f}" for y, r in rows.items())
              + " | требование: " + " ".join(f"{100 * r['req20']:.2f}" for r in rows.values()))
        print(f"{'':9s} пол Н20.1: " + "  ".join(f"{y}:{100 * r['floor11']:.2f}" for y, r in rows.items()))
    print("расхождения с графиком этапа 1:", res["mismatches"] or "нет")
    return 1 if res["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
