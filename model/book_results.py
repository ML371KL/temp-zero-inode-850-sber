"""Результаты книги: `results.json` и `run_output.txt` (регрессия книги, М§18).

`python -B -m model.book_results [--fast] [--out КАТАЛОГ]` — расчёт на цене, дате и реестре
книги (`live_from_book`): выводимые величины, слои, заголовок (медиана, полосы, точка, низ,
верх), 36 клеток, центральная (модальная) клетка по годам, тест знака объёмных эффектов, проверки,
обратный расчёт и «что даст отчёт». Центральная клетка выбирается правилом (`model.grid.modal_cell`:
мода по каждой оси в слое «свой взгляд»), а не литералом; опорная клетка диагностик (`norm`,
`schedule`) — другое понятие.
Числа — полной точности; `ops/tools/render_numbers.py` печатает их в документах по меткам.
Свежий расчёт воспроизводит файлы (rel 1e-12, пол 1e-10); коммит ядра в сравнение не входит.
По умолчанию файлы пишутся в `data/assumptions/` (их коммитит интеграция).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Mapping

from model.book import ROOT, Book, Facts, load_book, load_facts
from model.checks import check_gates, check_invariants, ddm_ri_max
from model.grid import derived_values, live_from_book, modal_cell, realized_cells, run_grid, volume_sign_test
from model import uncertainty as U

__all__ = ["book_results", "render_run_output", "main", "CENTRAL_RULE", "OUT_DIR"]

OUT_DIR = ROOT / "data" / "assumptions"
CENTRAL_RULE = ("мода по каждой оси в слое «свой взгляд»: мир с наибольшим весом, режим с наибольшей вероятностью "
                "после наблюдений, сценарий капитала с наибольшей вероятностью при этом режиме; при равенстве — "
                "первый по порядку книги")
MESSAGE_WIDTH = 140       # строка сообщения проверки в `run_output.txt`; длинное переносится, а не обрезается
MESSAGE_INDENT = 35       # продолжение сообщения — под началом его первой строки (вид и имя проверки — слева)


def _layer(L) -> dict[str, Any]:
    return {"title": L.title, "world_weights": dict(L.world_weights), "v0": L.v0, "bv_v": L.bv_v, "price": L.price,
            "pb": L.pb, "excess": L.excess, "pv_ri_explicit": L.pv_ri_explicit, "pv_terminal": L.pv_terminal,
            "terminal_share": L.terminal_share, "roe_tc": L.roe_tc, "k_tc": L.k_tc,
            "p_price_below_market": L.p_price_below_market, "p_roe_below_k": L.p_roe_below_k,
            "capital_gap_mass": L.capital_gap_mass}


def book_results(book: Book | None = None, facts: Facts | None = None, *, slow: bool = True) -> dict:
    """Таблицы книги; `slow=False` — быстрая полоса без поисков (проверка, не регрессия)."""
    facts = facts or load_facts()
    book = book or load_book(facts=facts)
    live = live_from_book(book, facts)
    run = run_grid(book, facts, live)
    draws = int(book.get("valuation.uncertainty.draws")) if slow else U.FAST_DRAWS
    band = U.band(book, facts, live, draws=draws)
    main = str(book.get("meta.company.main_ticker"))
    lam = run.lam
    h = band.headline(lam, live.prices, main)
    step = band.print_step
    headline = {"median": h["median"], "printed_median": h["printed_median"], "band80": h["band80"],
                "printed_band80": h["printed_band80"], "band50": h["band50"], "printed_band50": h["printed_band50"],
                "p10": h["p10"], "p25": h["p25"], "p75": h["p75"], "p90": h["p90"],
                "mean": h["mean"], "p_below_market": h["p_below_market"], "market": h["market"],
                "market_by_ticker": h["market_by_ticker"], "p_below_by_ticker": h["p_below_by_ticker"],
                "upside": h["upside"],
                "point": run.point, "printed_point": U.round_half_up(run.point, step), "low": run.low, "high": run.high,
                "rates_view": run.high - run.low, "lambda": lam, "draws": band.draws, "seed": band.seed,
                "ddm_ri_max": max(band.ddm_ri_max, ddm_ri_max(run)),
                "contributions": [{k: c[k] for k in ("axis", "name", "share", "rank_corr")}
                                  for c in band.contributions(lam)]}
    ay = run.ctx.timeline.anchor_year
    cells = [{"world": c.world, "regime": c.regime, "scenario": c.scenario, "v": c.v_ri, "v_ddm": c.v_ddm,
              "bv_v": c.bv_v, "pb": c.v_ri / c.bv_v, "roe_t": c.roe_t, "k_t": c.k_t, "g_t": c.g_t,
              "n20_min": c.n20_min, "n11_min": c.n11_min, "dps_first": c.dps.get(ay), "flags": sorted(c.flags)}
             for c in run.cells]
    key = modal_cell(book, run.ctx.posterior)
    cc = run.cell(*key)
    central = {"cell": "/".join(key), "probability": run.layers["analytical"].prob[key],
               "scenario_weight": float(book.get(f"joint.reg_prob_given_regime.{key[1]}.{key[2]}")),
               "rule": CENTRAL_RULE, "years": list(cc.years),
               **{k: list(v) for k, v in cc.annual.items()},
               "v": cc.v_ri, "bv_v": cc.bv_v, "roe_t": cc.roe_t, "k_t": cc.k_t, "g_t": cc.g_t, "x_t": cc.x_t,
               "terminal_share": cc.terminal_share}
    judg = U.judgements(book, facts, live, band, point=run.point) if slow else None
    off_band = U.off_band_shift(book, facts, live, run.point, judg)
    findings = check_invariants(run, band) + check_gates(run, off_band=off_band, band=band)
    checks = [{"name": f.name, "kind": f.kind, "fired": f.fired, "mass": f.mass, "message": f.message}
              for f in findings]
    derived = derived_values(run.ctx)
    derived["nii"]["realized_cells"] = realized_cells(run)
    out = {"book_version": str(book.get("meta.version")), "facts_date": str(book.get("meta.facts_date")),
           "engine_commit": _commit(), "draws": band.draws, "slow": slow, "derived": derived,
           "layers": {k: _layer(L) for k, L in run.layers.items()}, "headline": headline, "cells": cells,
           "central_cell": central, "sign_test": volume_sign_test(book, facts, run=run), "checks": checks}
    if slow:
        from model.nextreport import next_report
        from model.reverse import reverse_dcf
        anchor = U.MedianAnchor(book, facts, live, band)
        out["reverse_dcf"] = reverse_dcf(book, facts, live, band, anchor=anchor, run=run)
        out["next_report"] = next_report(book, facts, live, band, anchor=anchor, run=run)
        out["judgements"] = judg
        out["off_band_shift"] = off_band
    else:
        out["reverse_dcf"] = out["next_report"] = out["judgements"] = None
    return out


def _commit() -> str:
    from model.payload import engine_commit
    return engine_commit()


def _f(x: Any, nd: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:,.{nd}f}".replace(",", " ")
    return str(x)


def _p(x: Any, nd: int = 2) -> str:
    return "—" if x is None else f"{100 * x:.{nd}f} %"


def render_run_output(results: Mapping[str, Any]) -> str:
    """Текстовая сводка результатов книги (для людей и диффа)."""
    h = results["headline"]
    L = results["layers"]
    lines = [f"Книга {results['book_version']} · факты на {results['facts_date']} · прогонов полосы {results['draws']}"
             + ("" if results.get("slow") else " (быстрая проверка, не регрессия)"), "",
             "ЗАГОЛОВОК (₽ на акцию)",
             f"  медиана {_f(h['median'])} → печать {_f(h['printed_median'], 0)}; полоса 80 % {_f(h['band80'][0])}–"
             f"{_f(h['band80'][1])} (печать {_f(h['printed_band80'][0], 0)}–{_f(h['printed_band80'][1], 0)}); "
             f"50 % {_f(h['band50'][0])}–{_f(h['band50'][1])}",
             f"  среднее {_f(h['mean'])}; P(центр < рынка {_f(h['market'])}) = {_p(h['p_below_market'], 1)}",
             f"  точка при λ = {h['lambda']:g}: {_f(h['point'])} (печать {_f(h['printed_point'], 0)}); низ {_f(h['low'])}, "
             f"верх {_f(h['high'])}, взгляд на ставки {_f(h['rates_view'])}", "", "СЛОИ"]
    for k, v in L.items():
        lines.append(f"  {v['title']:<28} V0 {_f(v['v0'], 1):>10}  BV_v {_f(v['bv_v'], 1):>9}  цена {_f(v['price']):>8}  "
                     f"P/B {v['pb']:.3f}  ROE'_T {_p(v['roe_tc'])}  k_T {_p(v['k_tc'])}")
    lines += ["", "ВКЛАДЫ ОСЕЙ В ПОЛОСУ (доля квадрата ранговой корреляции)"]
    for c in h["contributions"][:12]:
        lines.append(f"  {c['share']:6.3f}  ρ {c['rank_corr']:+.3f}  {c['name']}")
    d = results["derived"]["nii"]
    lines += ["", "ПЕРЕДАЧА СТАВКИ",
              f"  σ0 активов {d['sigma0']:+.6f}, σ0 пассивов {d.get('sigma0_liab', 0.0):+.6f} (доля на активах "
              f"{d.get('sigma0_split', 1.0):g}), φ {d['phi']:.6f}: активы {d.get('phi_assets', d['phi']):.6f}, "
              f"пассивы {d.get('phi_liab', 0.0):.6f} (доля на активах {d.get('phi_split', 1.0):g}), "
              f"T_real {d['T_real']:.6f}, цель движка "
              f"{d['target_eng']:.6f}, ROE-экв. {d['roe_equiv']:.4f}, по клеткам {d['realized_cells']:.6f}",
              "  кредитная маржа стационара: " + ", ".join(f"{w} {_p(v)}" for w, v in (d.get("loan_margin") or {}).items()),
              "  LT-спред кредитных книг к опоре: " + "; ".join(
                  f"{b} " + ", ".join(f"{w} {_p(v)}" for w, v in row.items())
                  for b, row in (d.get("lt_spread") or {}).items()),
              "  T(W): " + ", ".join(f"{w} {v:.4f}" for w, v in d["T"].items()),
              "  пары: " + ", ".join(f"{k.replace('_', '→')} " + ("—" if v is None else f"{v:.4f}")
                                    + ("" if (d.get("roe_equiv_pairs") or {}).get(k) is None
                                       else f" (ROE-экв. {d['roe_equiv_pairs'][k]:.3f})")
                                    for k, v in (d.get("T_pairs") or {}).items()),
              f"  DDM = RI: макс. отн. разность по сетке и полосе {h.get('ddm_ri_max', 0.0):.1e}"]
    st = results.get("sign_test")
    if st:
        lines.append("  тест знака (снятие остановки кредита в кризисе и поправок роста режимов): точка "
                     f"{st['d_point']:+.2f} ₽; миры " + ", ".join(f"{w} {v:+.2f}" for w, v in st["d_world"].items())
                     + (" — выполнен" if st["ok"] else " — НЕ выполнен"))
    cc = results["central_cell"]
    lines += ["", f"ЦЕНТРАЛЬНАЯ (МОДАЛЬНАЯ) КЛЕТКА {cc['cell']} (вероятность {_p(cc.get('probability'), 1)}, вес сценария "
              f"{_p(cc.get('scenario_weight'), 0)}): V {_f(cc['v'], 1)}, BV_v {_f(cc['bv_v'], 1)}, ROE'_T {_p(cc['roe_t'])}, "
              f"k_T {_p(cc['k_t'])}, g_T {_p(cc['g_t'])}, доля терминала {_p(cc['terminal_share'], 1)}",
              "  год      " + "".join(f"{y:>9}" for y in cc["years"])]
    for key, title, pct in (("ni_sh", "ЧП акц.", False), ("roe", "ROE", True), ("nim", "ЧПМ дв.", True),
                            ("cor", "CoR дв.", True), ("cir", "CIR", True), ("n20", "Н20.0", True), ("dps", "DPS", False)):
        vals = cc.get(key) or []
        lines.append(f"  {title:<8} " + "".join(f"{(_p(v, 1) if pct else _f(v, 1)):>9}" for v in vals))
    lines += ["", "ПРОВЕРКИ"]
    for c in results["checks"]:
        mark = ("НАРУШЕН" if c["kind"] == "invariant" else "сработал") if c["fired"] else "ок"
        mass = "" if c["mass"] is None else f" (масса {c['mass']:.3f})"
        # Сообщение — целиком: длинное переносится под своё начало (в хвосте сообщения гейта — прогоны полосы).
        first, *rest = textwrap.wrap(c["message"], MESSAGE_WIDTH, break_on_hyphens=False) or [""]
        lines.append(f"  {c['kind']:<9} {c['name']:<22} {mark}{mass}: {first}")
        lines += [" " * MESSAGE_INDENT + part for part in rest]
    rev = results.get("reverse_dcf")
    if rev:
        lines += ["", f"ЧТО В ЦЕНЕ (медиана = {_f(rev['target'])} ₽)"]
        for r in rev["rows"]:                              # «в диапазоне» — только о корне (М§11.2)
            where = "" if r["status"] != "solved" else ", в диапазоне" if r["in_range"] else ", вне диапазона"
            loose = ", уточнение не сошлось" if r["status"] == "solved" and r.get("converged") is False else ""
            lines.append(f"  {r['name']:<44} книга {_f(r['book'], 4):>9} → {_f(r['solved'], 4):>9} "
                         f"({r['status']}{where}{loose}; невязка {_f(r['gap'])} ₽)")
        for r in rev["bank_rows"]:
            lines.append(f"  {r['title']:<44} книга {_f(r['book'], 4):>9} → {_f(r['implied'], 4):>9} ({r['status']})")
    nr = results.get("next_report")
    if nr:
        n = nr["neutral"]
        e = nr["expectation"]
        lines += ["", f"ЧТО ДАСТ ОТЧЁТ {nr['period']}: ожидание CoR упр. {_p(e['cor_mgmt'])}, ЧПМ упр. {_p(e['nim_mgmt'])}, "
                      f"ЧП {_f(e['ni'], 1)} млрд",
                  f"  нейтральная CoR {_p(n['cor']['value'])} (невязка {_f(n['cor']['gap_rub'])} ₽, экв. ЧП "
                  f"{_f(n['cor']['ni_equivalent'], 1)}); нейтральная ЧПМ {_p(n['nim']['value'])} "
                  f"(невязка {_f(n['nim']['gap_rub'])} ₽, экв. ЧП {_f(n['nim']['ni_equivalent'], 1)})",
                  f"  местный наклон: {_f(nr['slope']['rub_per_01pp_cor'])} ₽ на 0,1 п.п. CoR; "
                  f"{_f(nr['slope']['rub_per_01pp_nim'])} ₽ на 0,1 п.п. ЧПМ" + (f"; {n['error']}" if n.get("error") else "")]
        rc = nr.get("reaction") or {}
        if rc:
            lines.append("  размах реакции медианы: CoR "
                         f"{_f(rc['cor']['d_median_min'])}…{_f(rc['cor']['d_median_max'])} ₽; ЧПМ "
                         f"{_f(rc['nim']['d_median_min'])}…{_f(rc['nim']['d_median_max'])} ₽")
    judg = results.get("judgements")
    if judg:
        ob = results.get("off_band_shift") or {}
        total = sum(r["mean_shift"] for r in judg if r.get("in_band") and r.get("mean_shift") is not None)
        lines += ["", "ЦЕНА ОШИБКИ СУЖДЕНИЙ (точка на краях оси; сдвиг положения — первый порядок «среднее − точка»)",
                  f"  Σ сдвигов положения осей полосы {_f(total)} ₽; осей вне полосы {ob.get('axes', 0)}: "
                  f"{_f(ob.get('rub'))} ₽ при пороге {_f(ob.get('limit'))} ₽"]
        for r in judg[:12]:
            lines.append(f"  {r['name'][:52]:<52} {_f(r['price_low']):>8} … {_f(r['price_high']):>8}  размах "
                         f"{_f(r['swing']):>7}  сдвиг {_f(r['mean_shift']):>7}")
    return "\n".join(lines) + "\n"


def _default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -B -m model.book_results", description="results.json и run_output.txt книги")
    ap.add_argument("--fast", action="store_true", help="быстрая полоса без поисков (не регрессия)")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="каталог для results.json и run_output.txt")
    args = ap.parse_args(argv)
    try:
        res = book_results(slow=not args.fast)
    except Exception as exc:  # noqa: BLE001 — любой отказ — код 1 с причиной
        print(f"ПРОВАЛ: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=_default) + "\n",
                                           encoding="utf-8", newline="\n")
    (args.out / "run_output.txt").write_text(render_run_output(json.loads(json.dumps(res, default=_default))),
                                             encoding="utf-8", newline="\n")
    print(f"готово: {args.out / 'results.json'}, {args.out / 'run_output.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
