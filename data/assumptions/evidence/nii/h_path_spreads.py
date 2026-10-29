# -*- coding: utf-8 -*-
"""Ключи спредов лет после ближних — калибровка истории на пути мира H записи миров (MODEL §4.4; решение ведущего
29.10.2026, книга 1.6).

Калибровка истории книг ЧПД (`calib_nii.py`, снимок 2К26 — решение владельца 14.7) задаёт ставку книги формой
y = a × опора + s; спред к опоре в мире отсчёта H в году y — s − (1 − a) × ref_H(y) (наклон истории внутри мира H).
Калибровка (s, a, ρ, β, LT-спреды) заморожена; опора мира H — вход записи миров, а не калибровка. Поэтому ключи лет
Y0 + 2 … LT_from − 1 (книга 1.6: 2028–2030) книг с траекторией спреда — эта форма на пути мира H записи:
  ключ года y = round(s − (1 − a) × ref_H(y), 3 знака в п.п.) / 100,
s и a — выходы снимка калибровки (`out/results.json` → books.<книга калибровки>.free: s_pp, a_or_beta, ref), без
перекалибровки; ref_H(y) — среднее полугодий года мира H записи (как среднее кварталов путей миров этапа 1, которыми
считал снимок). Ключ Y0 + 1 — середина до ключа Y0 + 2 (лист near_spreads.py); ключ LT — замороженная калибровка: лист
печатает форму на LT записи справочно и его не сверяет. Книга ипотеки и розницы — одна калибровка `retail_market`.

Самопроверка формы: на путях миров этапа 1 (вход снимка, `inputs/stage1/worlds/world_paths_quarterly.csv`) лист
воспроизводит ключи предложения калибровки (`out/nii_book_proposal.yaml`) до 1e-5 — остаток от округления s и a в
выходах снимка (s — до 0,001 п.п., a — до 0,0001; снимок считал на неокруглённых).

Запуск из корня репозитория: python -B data/assumptions/evidence/nii/h_path_spreads.py → out/h_path_spreads_out.json;
код выхода 1 — ключи шаблона книги расходятся с правилом. `compute(book, rec, calib)` — то же правило на чужих шаблоне,
записи миров и калибровке: им инструмент миров (ops/tools/refresh_worlds.py) сверяет ключи книги с новой записью.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

CALIB = os.path.join(OUT, "results.json")
PROPOSAL = os.path.join(OUT, "nii_book_proposal.yaml")
STAGE1_PATHS = os.path.join(HERE, "inputs", "stage1", "worlds", "world_paths_quarterly.csv")
CALIB_BOOK = {"corp_loans": "corp_loans", "mortgage": "retail_market", "retail_loans": "retail_market",
              "securities": "securities", "liquidity": "liquidity"}
REF_FIELD = {"key": "key_avg", "ofz_1y": "ofz_1y", "ofz_3y": "ofz_3y", "ofz_5y": "ofz_5y"}
Q3 = Decimal("0.001")
TOL = 5e-6 + 1e-12            # ключ шаблона — пять знаков правила


def _d(x) -> Decimal:
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


def _key(s_pp: float, a: float, ref_pct: float) -> float:
    pp = (_d(s_pp) - (1 - _d(a)) * _d(ref_pct)).quantize(Q3, rounding=ROUND_HALF_UP)
    return float(pp / 100)


def h_year_refs(rec: dict, world: str = "H") -> dict:
    """{год: {опора: среднее полугодий года, %}} мира записи миров."""
    out: dict = {}
    for r in rec["rows"]:
        if r["world"] != world:
            continue
        y = int(str(r["period"])[:4])
        for ref, field in REF_FIELD.items():
            out.setdefault(y, {}).setdefault(ref, []).append(float(r[field]))
    return {y: {k: sum(v) / len(v) for k, v in refs.items()} for y, refs in out.items()}


def stage1_year_refs(path: str = STAGE1_PATHS, world: str = "H") -> dict:
    """То же на путях миров этапа 1 (кварталы; LT — отдельной строкой) — вход снимка калибровки."""
    out: dict = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["world"] != world or r["period"] == "LT":
                continue
            y = int(r["period"][:4])
            for ref, field in REF_FIELD.items():
                out.setdefault(y, {}).setdefault(ref, []).append(float(r[field]))
    return {y: {k: sum(v) / len(v) for k, v in refs.items()} for y, refs in out.items()}


def rule_years(spread: dict, y0: int) -> list[int]:
    """Годы правила: явные ключи года от Y0 + 2 до LT_from − 1."""
    lt_from = int(spread.get("LT_from", 10 ** 6))
    return sorted(int(k) for k in spread if str(k).isdigit() and y0 + 2 <= int(k) < lt_from)


def compute(book: dict | None = None, rec: dict | None = None, calib: dict | None = None) -> dict:
    book = evlib.template() if book is None else book
    rec = evlib.record() if rec is None else rec
    calib = json.load(open(CALIB, encoding="utf-8"))["books"] if calib is None else calib
    ref_world = book["nii"]["transmission"]["reference_world"]
    y0 = int(str(book["meta"]["first_period"])[:4])
    refs = h_year_refs(rec, ref_world)
    last = [r for r in rec["rows"] if r["world"] == ref_world][-1]
    books, bad = {}, []
    for b, cb in CALIB_BOOK.items():
        spec = book["nii"]["books"][b]
        sp = spec["spread"]
        f = calib[cb]["free"]
        if f["ref"] != spec["ref"]:
            bad.append(f"nii.books.{b}: опора книги {spec['ref']} ≠ опора калибровки {cb} {f['ref']}")
            continue
        rule = {str(y): _key(f["s_pp"], f["a_or_beta"], refs[y][spec["ref"]]) for y in rule_years(sp, y0)}
        lt_form = _key(f["s_pp"], f["a_or_beta"], float(last[REF_FIELD[spec["ref"]]]))
        books[b] = {"calibration": cb, "ref": spec["ref"], "s_pp": f["s_pp"], "a": f["a_or_beta"],
                    "ref_H_year_pct": {y: round(refs[int(y)][spec["ref"]], 4) for y in rule},
                    "rule": rule, "book": {k: sp.get(k) for k in rule},
                    "lt_form_on_record": lt_form, "lt_book": sp.get("LT")}
        for k, v in rule.items():
            if sp.get(k) is None or abs(float(sp[k]) - v) > TOL:
                bad.append(f"nii.books.{b}.spread.{k}: книга {sp.get(k)} против правила {v}")
    return {"reference_world": ref_world, "anchor_year": y0, "years": sorted({k for v in books.values() for k in v["rule"]}),
            "calibration": "out/results.json (снимок calib_nii, решение владельца 14.7)", "books": books,
            "book_mismatches": bad}


def self_check(calib: dict | None = None) -> dict:
    """Форма на путях миров этапа 1 против ключей предложения калибровки (наибольший остаток)."""
    calib = json.load(open(CALIB, encoding="utf-8"))["books"] if calib is None else calib
    prop = yaml.safe_load(open(PROPOSAL, encoding="utf-8"))["nii"]["books"]
    refs = stage1_year_refs()
    worst, rows = 0.0, {}
    for b, cb in CALIB_BOOK.items():
        pb = prop["retail_loans" if b == "mortgage" else b]["spread"]
        f = calib[cb]["free"]
        for y in (int(k) for k in pb if str(k).isdigit() and int(k) >= 2028):
            v = _key(f["s_pp"], f["a_or_beta"], refs[y][f["ref"]])
            rows[f"{b}.{y}"] = [pb[str(y)], v]
            worst = max(worst, abs(float(pb[str(y)]) - v))
    return {"max_abs_vs_proposal": round(worst, 7), "rows": rows}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    res["self_check_stage1"] = self_check()
    evlib.write_json(evlib.Path(OUT) / "h_path_spreads_out.json", res)
    for b, v in res["books"].items():
        print(f"{b:13s} s {v['s_pp']:+.3f} п.п., a {v['a']:.4f}, опора {v['ref']}: " + "; ".join(
            f"{y}: {100 * float(v['book'][y]):+.3f} → правило {100 * x:+.3f}" if v["book"][y] != x
            else f"{y}: {100 * x:+.3f}" for y, x in v["rule"].items())
            + f"; LT книги {100 * float(v['lt_book']):+.3f} (форма на LT записи {100 * v['lt_form_on_record']:+.3f})")
    print("самопроверка на путях этапа 1: наибольший остаток к предложению калибровки",
          res["self_check_stage1"]["max_abs_vs_proposal"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    ok = not res["book_mismatches"] and res["self_check_stage1"]["max_abs_vs_proposal"] <= 1e-5 + 1e-12
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
