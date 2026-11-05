# -*- coding: utf-8 -*-
"""Числа объяснений гейтов книги Сбера, которых нет полем в таблицах: составы клеток гейтов и их величины на сетке книги
(тот же счёт, что у гейтов ядра: `model.checks`, `model.grid.run_grid`) и числа полосы из сообщений гейтов таблиц.

    python -B ops/tools/gate_numbers.py [--out data/assumptions/gate_numbers.json]

Слова объяснений — шаблон `data/assumptions/gate_explanations.tpl` (пишет ведущий); файл объяснений из шаблона пишет
команда приёма миров (`ops/tools/adopt_worlds.py`, шаг `gate-texts`), числа — метками из этого выхода, таблиц книги
(`results.json`), записи миров и машинной книги. Ядро (`model/`) не трогается: функции ядра на книге дерева (ЧПМ, CoR и
CIR года гайденса, норматив клетки в квартале разрыва, выплата клетки — с этим выходом считаются, а не пишутся руками).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "assumptions" / "gate_numbers.json"
RESULTS = ROOT / "data" / "assumptions" / "results.json"


def span(xs) -> list[float]:
    xs = [x for x in xs if x is not None]
    return [min(xs), max(xs)] if xs else []


def num(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", ".").replace("−", "-"))


def numbers() -> dict:
    os.environ.setdefault("BANK_WORKERS", "2")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from model import checks as K
    from model.book import load_book, load_facts
    from model.grid import run_grid
    from model.live import live_from_book

    F = load_facts()
    B = load_book(None, facts=F)
    g = run_grid(B, F, live_from_book(B, F))
    tl, prob = g.ctx.timeline, g.layers["analytical"].prob
    cells = {c.label: c for c in g.cells}
    fired = {f.name: f for f in K.check_gates(g) if f.kind == "gate" and f.fired}
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    msgs = {c["name"]: c["message"] for c in res["checks"] if c.get("kind") == "gate" and c.get("fired")}
    out: dict = {"book_version": str(B.get("meta.version")), "gates": {k: sorted(f.cells) for k, f in fired.items()}}

    # guidance_gap: путь года гайденса (функции гейта); ROE — одного определения, ЧПМ, CoR, CIR — упр. базис
    year = int(F.plain("guidance", "year"))
    vals = [K.guidance_values(g, c, year) for c in g.cells]
    roe_band = list(K.guidance_band(F.node("guidance", "items.roe")))
    roe = span(v["roe"] for v in vals)
    out["guidance_gap"] = {"year": year, "roe": roe, "roe_band": roe_band, "nim": span(v["nim"] for v in vals),
                           "cor": span(v["cor_max"] for v in vals), "cir": span(v["cir"] for v in vals),
                           "roe_over": [roe[0] - roe_band[1], roe[1] - roe_band[1]], "cells": len(g.cells)}
    # roe_k_homogeneity: избыточная доходность терминала по мирам и разрыв (деталь гейта)
    if "roe_k_homogeneity" in fired:
        d = fired["roe_k_homogeneity"].detail
        out["roe_k_homogeneity"] = {"by_world": dict(d["by_world"]), "gap": d["gap"]}
    # m_crisis_vs_cbr: минимум Н20.0 клеток гейта
    if "m_crisis_vs_cbr" in fired:
        mc = fired["m_crisis_vs_cbr"].cells
        out["m_crisis_vs_cbr"] = {"n20_min": min(cells[x].n20_min for x in mc), "scenarios": sorted(cells[x].scenario for x in mc)}
    # capital_gap: клетка ниже пола — кварталы, норматив и пол, выплата, числа полосы из сообщения таблиц
    if "capital_gap" in fired:
        lab = sorted(fired["capital_gap"].cells)[0]
        c = cells[lab]
        n20, fl = c.quarters["n20"], c.quarters["floor20"]
        below = [q for q in range(1, tl.Q + 1) if n20[q] is not None and n20[q] < fl[q]]
        q0 = below[0]
        back = next(q for q in range(q0 + 1, tl.Q + 1) if n20[q] is not None and n20[q] >= fl[q])
        y0, qq0 = tl.period(q0)[:4], tl.period(q0)[-1]
        div = [(c.quarters["div"][q] or 0.0, tl.period(q)) for q in range(1, tl.Q + 1)]
        m = re.search(r"в полосе — в ([\d ]+) из ([\d ]+) прогонов \(масса таких клеток в среднем по всем прогонам — ([\d,]+) %\)",
                      msgs.get("capital_gap", "").replace(" ", " "))   # разряды в сообщениях ядра — неразрывным пробелом
        out["capital_gap"] = {
            "cell": lab, "below": [tl.period(q) for q in below], "first": tl.period(q0), "first_year": int(y0), "first_q": int(qq0),
            "n20_first": n20[q0], "floor_first": fl[q0], "floor_before": fl[q0 - 1], "gap_first": fl[q0] - n20[q0],
            "back": tl.period(back), "back_year": int(tl.period(back)[:4]), "back_q": int(tl.period(back)[-1]),
            "count": len(below), "last": tl.period(below[-1]), "last_year": int(tl.period(below[-1])[:4]),
            "last_q": int(tl.period(below[-1])[-1]), "n20_last": n20[below[-1]], "floor_last": fl[below[-1]],
            "gap_last": fl[below[-1]] - n20[below[-1]],
            "payout_max": max(div)[0], "payout_max_period": max(div)[1],
            "band": {"runs": num(m.group(1)), "draws": num(m.group(2)), "share": num(m.group(3))} if m else None}
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    n = numbers()
    a.out.write_text(json.dumps(n, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"ИТОГ: числа объяснений гейтов книги {n['book_version']} — {a.out.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
