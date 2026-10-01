# -*- coding: utf-8 -*-
"""Ближние ключи спредов книг ЧПД — от ставок якоря (MODEL §4.4; решение ведущего A1 F3, книга 1.1).

Правило для каждой книги с траекторией спреда (словарь с ключом года якоря):
  ключ года якоря Y0      s(Y0)     = y_b,0 − ref_b,H,1        (активы; пассивы: c_b,0 − β_b × ref_b,H,1)
  ключ года Y0 + 1        s(Y0 + 1) = (s(Y0) + s(Y0 + 2)) / 2  (переход к калибровке истории — середина)
  ключи с Y0 + 2 и LT     — калибровка истории (evidence/nii/calib_nii.py; сверяет nii_book.py)
y_b,0 / c_b,0 — ставка якоря книги (data/facts/nii_books.json → books.<b>.rate_anchor, act/365); ref_b,H,1 — опора
книги в мире H в первом прогнозном квартале (полугодие записи миров семейства, поле / 100 — как в build_assumptions.py).
При неизменной опоре ставка книги в первом прогнозном квартале стоит на ставке якоря — без скачка ЧПМ в 3К26 и без
«тяги» механики к калибровке истории в году якоря (sanity-val S1). Y0 — год первого прогнозного квартала.
Округление — 5 знаков, половина от нуля (Decimal по десятичной записи фактов и записи миров).

Запуск из корня репозитория: python -B data/assumptions/evidence/nii/near_spreads.py → out/near_spreads_out.json;
код выхода 1 — ключи шаблона книги расходятся с правилом.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
import evlib  # noqa: E402

FACTS = evlib.BOOK_DIR.parent / "facts" / "nii_books.json"
REF_FIELD = {"key": "key_avg", "ofz_1y": "ofz_1y", "ofz_3y": "ofz_3y", "ofz_5y": "ofz_5y", "ofz_10y": "ofz_10y"}
Q5 = Decimal("0.00001")


def _d(x) -> Decimal:
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


def _r5(x: Decimal) -> float:
    return float(x.quantize(Q5, rounding=ROUND_HALF_UP))


def _half(period: str) -> str:
    return f"{period[:4]}H{1 if int(period[5]) <= 2 else 2}"


def year_value(spread: dict, year: int) -> Decimal:
    """Значение траектории спреда в году (ключ года; после последнего ключа — LT с LT_from; MODEL §0.4)."""
    if str(year) in spread:
        return _d(spread[str(year)])
    years = sorted(int(k) for k in spread if k not in ("LT", "LT_from") and str(k).isdigit())
    lt_from = spread.get("LT_from")
    if "LT" in spread and years and year > years[-1]:
        if lt_from is None or year >= int(lt_from):
            return _d(spread["LT"])
        y0, v0 = years[-1], _d(spread[str(years[-1])])          # линейный сход к LT к году LT_from
        return v0 + (_d(spread["LT"]) - v0) * (year - y0) / (int(lt_from) - y0)
    raise ValueError(f"нет значения спреда в {year}: {spread}")


def compute() -> dict:
    book = evlib.template()
    rec = evlib.record()
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    first = book["meta"]["first_period"]
    y0 = int(first[:4])
    row_h = next(r for r in rec["rows"] if r["world"] == book["nii"]["transmission"]["reference_world"]
                 and r["period"] == _half(first))
    books, bad = {}, []
    for b, spec in book["nii"]["books"].items():
        sp = spec["spread"]
        if not isinstance(sp, dict) or str(y0) not in sp:
            continue                                              # скаляр — постоянный спред истории, правило не действует
        ref = _d(round(row_h[REF_FIELD[spec["ref"]]] / 100.0, 5))
        rate0 = _d(facts["books"][b]["rate_anchor"]["v"])
        beta = _d(spec["beta"]) if spec["side"] == "liability" else Decimal(1)
        s0 = rate0 - beta * ref
        s2 = year_value(sp, y0 + 2)
        rule = {str(y0): _r5(s0), str(y0 + 1): _r5((_d(_r5(s0)) + s2) / 2)}
        book_near = {k: sp.get(k) for k in rule}
        books[b] = {"side": spec["side"], "ref": spec["ref"], "rho": spec["rho"], "rate_anchor": float(rate0),
                    "ref_H_first_quarter": float(ref), "beta": float(beta), "calibration_Y0_plus_2": float(s2),
                    "rule": rule, "book": book_near,
                    "rate_first_quarter_H": _r5(ref * beta + _d(rule[str(y0)])) if spec["side"] == "liability"
                    else _r5(ref + _d(rule[str(y0)]))}
        for k, v in rule.items():
            if book_near[k] is None or abs(float(book_near[k]) - v) > 5e-6 + 1e-12:
                bad.append(f"nii.books.{b}.spread.{k}: книга {book_near[k]} против правила {v}")
    return {"anchor_year": y0, "first_period": first, "reference_half": _half(first),
            "facts": "data/facts/nii_books.json (rate_anchor)", "books": books, "book_mismatches": bad}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    evlib.write_json(evlib.Path(OUT) / "near_spreads_out.json", res)
    for b, v in res["books"].items():
        print(f"{b:13s} ставка якоря {100 * v['rate_anchor']:.4f} % − опора H {100 * v['ref_H_first_quarter']:.2f} % "
              f"(β {v['beta']:g}) → {res['anchor_year']}: {100 * v['rule'][str(res['anchor_year'])]:+.3f} п.п., "
              f"{res['anchor_year'] + 1}: {100 * v['rule'][str(res['anchor_year'] + 1)]:+.3f} п.п. "
              f"(калибровка {res['anchor_year'] + 2}: {100 * v['calibration_Y0_plus_2']:+.3f})")
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
