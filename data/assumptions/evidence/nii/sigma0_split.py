# -*- coding: utf-8 -*-
"""Распределение сдвига σ0 между активами и пассивами: ключ `nii.sigma0_split` — первый шаг правила центров
(MODEL §4.5, §14.2; решения ведущего В8 и В12, книга 1.3). Второй шаг — `phi_split.py`.

Книги ЧПД на структуре якоря в мире H дают стационарный ЧПМ выше цели A-N2; разность Δ0 = цель − Nss(H | без сдвига)
несут книги с `lt_shift`:
  активы   σ0_A = split × Δ0 / Σ a_b            — сдвиг спредов кредитных книг;
  пассивы  σ0_L = (1 − split) × Δ0 / Σ l_b,H    — в стоимость входит со знаком минус (Δ0 < 0 — пассивы дорожают).
Сжатие φ решается после сдвигов по реализованной передаче M − N (A-N3) и делится долей p = `nii.phi_split`:
  φ_A = p × φ                      — сжатие спредов кредитных книг с `phi`;
  φ_L = (1 − p) × φ × A / L        — добавка φ_L × X_W к стоимости пассивов с `phi`,
  X_W = Σ ω_b × (ref_b,W − ref_b,H), ω_b = a_b / A, A и L — веса активов и пассивов с `phi` на якоре.
Объём сжатия мира φ × C_W при любой доле один, поэтому стационарный ЧПМ миров от p не зависит (лист проверяет).
Эффективный LT-спред кредитной книги к её опоре в стационаре мира W:
  s_eff,b,W = s_b^LT + σ0_A·[lt_shift_b] − φ_A·[phi_b]·(ref_b,W^LT − ref_b,H^LT),
гейт `lt_spread_floor`: s_eff,b,W ≥ checks.lt_spread_floor.<b> у каждой кредитной книги в каждом мире.

Правило центров (отправная точка — (1; 1): сдвиг и сжатие целиком в кредитных ставках):
  1. `nii.sigma0_split` — наибольшая доля, при которой гейт проходит в мире H (сжатия там нет); вниз до 0,01;
  2. `nii.phi_split` — наибольшая доля, при которой гейт проходит в остальных мирах (лист `phi_split.py`).
Этот лист считает шаг 1 и показывает исход гейта по мирам при долях книги.

Ось полосы — в области гейта: от нуля (весь сдвиг на средствах клиентов) до центра. Выше центра спред корпоративной
книги в мире H ниже пола, такую долю полоса не разыгрывает: пол обязан держаться на всей оси доли (при прочих осях
на значениях книги), а не только в центре. Доля, при которой спред корпоративных кредитов к опоре равен нулю (прежний верх оси), остаётся справкой
`zero_spread_split` — чувствительность центра к самому полу.

Запуск из корня репозитория (после calib_nii.py): python -B data/assumptions/evidence/nii/sigma0_split.py
→ out/sigma0_split_out.json; код выхода 1 — книга расходится с правилом.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import evlib  # noqa: E402

FACTS = evlib.BOOK_DIR.parent / "facts"
REF_FIELD = {"key": "key_avg", "ofz_1y": "ofz_1y", "ofz_3y": "ofz_3y", "ofz_5y": "ofz_5y", "ofz_10y": "ofz_10y"}
REAL_FIELD = "real_key_fwd12m"
STEP = 0.01                    # шаг округления центров и концов осей
BASE_WORLD, MARKET_WORLD = "N", "M"        # миры реализованной передачи A-N3 и отсчёта κ-добавки (MODEL §4.5, §4.6)


def floor_step(x: float, step: float = STEP) -> float:
    return round(math.floor(x / step + 1e-9) * step, 6)


def _lt(spread) -> float:
    return float(spread["LT"]) if isinstance(spread, dict) else float(spread)


class Stationary:
    """Стационарный ЧПМ на структуре якоря (MODEL §4.5) — независимый от ядра повтор по фактам и записи миров."""

    def __init__(self, book: dict, rec: dict):
        nb = json.loads((FACTS / "nii_books.json").read_text(encoding="utf-8"))
        bridge = json.loads((FACTS / "bridge_mgmt_ifrs.json").read_text(encoding="utf-8"))
        br = bridge["nim"]
        self.book = book
        self.books = book["nii"]["books"]
        self.assets = [b for b, s in self.books.items() if s["side"] == "asset"]
        self.liabs = [b for b, s in self.books.items() if s["side"] == "liability"]
        bal = {b: nb["books"][b]["balance_close"]["v"] for b in self.books}
        iea = sum(bal[b] for b in self.assets)
        self.w = {b: bal[b] / iea for b in self.books}
        self.retail = ("retail_current", "retail_term")
        self.l_r = sum(self.w[b] for b in self.retail)
        last = book["meta"]["last_period"]
        half = f"{last[:4]}H{1 if int(last[5]) <= 2 else 2}"
        self.worlds = list(book["worlds"]["ids"])
        rows = {w: next(r for r in rec["rows"] if r["world"] == w and r["period"] == half) for w in self.worlds}
        self.ref = {w: {k: round(rows[w][f] / 100.0, 5) for k, f in REF_FIELD.items()} for w in self.worlds}
        self.real = {w: round(rows[w][REAL_FIELD] / 100.0, 5) for w in self.worlds}
        self.s_lt = {b: _lt(s["spread"]) for b, s in self.books.items()}
        rcs = book["nii"]["retail_current_share"]
        self.c_ref, self.key_ref = nb["current_share_anchor"]["v"], nb["key_avg_anchor_q"]["v"]
        self.psi, self.bounds = float(rcs["psi"]), tuple(rcs["bounds"])
        self.dia = float(book["nii"]["dia_rate"])
        self.h = book["nii"]["transmission"]["reference_world"]
        target = float(book["nii"]["nim_lt_target_mgmt"])
        self.target_eng = target + br["value"]["v"] if br["method"] == "additive" else target * br["value"]["v"]
        self.t_star = float(book["nii"]["transmission"]["target"])
        self.a_lt = sum(self.w[b] for b in self.assets if self.books[b].get("lt_shift"))
        self.l_lt = sum(self.weight_h(b) for b in self.liabs if self.books[b].get("lt_shift"))
        # сжатие φ: кредитные книги Φ_A и пассивы Φ_L (флаг phi), веса A и L, доли ω_b (MODEL §4.4–§4.5)
        self.phi_assets = [b for b in self.assets if self.books[b].get("phi")]
        self.phi_liabs = [b for b in self.liabs if self.books[b].get("phi")]
        self.a_phi = sum(self.w[b] for b in self.phi_assets)
        self.l_phi = sum(self.w[b] for b in self.phi_liabs)          # у обеих книг ФЛ флаг одинаков — сумма не зависит от мира
        self.omega = {b: self.w[b] / self.a_phi for b in self.phi_assets}
        self.p_book = float(book["nii"]["phi_split"])
        # кредитная маржа стационара: CoR режима-опоры в базисе движка, κ, доля бумаг в балансирующих активах
        cr = bridge["cor"]
        ref_regime = book["credit"]["fv_loans_ref"]
        cor_lt = _lt(book["regimes"][ref_regime]["cor"])
        if book["regimes"]["cor_basis"] == "mgmt":
            cor_lt = cor_lt + cr["value"]["v"] if cr["method"] == "additive" else cor_lt * cr["value"]["v"]
        self.cor_lt = cor_lt
        self.kappa = float(book["credit"]["kappa"])
        self.sec = bal["securities"] / (bal["securities"] + bal["liquidity"])

    def c_star(self, world: str) -> float:
        lo, hi = self.bounds
        return min(hi, max(lo, self.c_ref - self.psi * (self.ref[world]["key"] - self.key_ref)))

    def weight_h(self, b: str) -> float:
        """Вес пассива в стационаре мира H: у средств ФЛ — доля текущих c*(key_H^LT) (MODEL §4.5)."""
        if b == "retail_current":
            return self.l_r * self.c_star(self.h)
        if b == "retail_term":
            return self.l_r * (1 - self.c_star(self.h))
        return self.w[b]

    def x(self, world: str) -> float:
        """X_W — разность опорных ставок кредитных книг Φ_A мира W и мира H, взвешенная долями ω_b на якоре."""
        return sum(om * (self.ref[world][self.books[b]["ref"]] - self.ref[self.h][self.books[b]["ref"]])
                   for b, om in self.omega.items())

    def liab_cost(self, b: str, world: str, s_l: float, phi_l: float) -> float:
        spec = self.books[b]
        cost = float(spec["beta"]) * self.ref[world][spec["ref"]] + self.s_lt[b]
        cost -= s_l if spec.get("lt_shift") else 0.0
        cost += phi_l * self.x(world) if spec.get("phi") else 0.0
        return cost

    def nss(self, world: str, s_a: float, s_l: float, phi_a: float, phi_l: float = 0.0) -> float:
        rw, rh, total = self.ref[world], self.ref[self.h], 0.0
        for b in self.assets:
            spec = self.books[b]
            y = rw[spec["ref"]] + self.s_lt[b] + (s_a if spec.get("lt_shift") else 0.0)
            if spec.get("phi"):
                y -= phi_a * (rw[spec["ref"]] - rh[spec["ref"]])
            total += self.w[b] * y
        cost = {b: self.liab_cost(b, world, s_l, phi_l) for b in self.liabs}
        for b in self.liabs:
            if b not in self.retail:
                total -= self.w[b] * cost[b]
        c = self.c_star(world)
        total -= self.l_r * (c * cost["retail_current"] + (1 - c) * cost["retail_term"]) + self.dia * self.l_r
        return total

    def solve(self, split: float, p: float | None = None) -> dict:
        """σ0_A, σ0_L, φ и его раскладка φ_A, φ_L при долях split и p (замкнуто, MODEL §4.5)."""
        p = self.p_book if p is None else p
        delta0 = self.target_eng - self.nss(self.h, 0.0, 0.0, 0.0)
        s_a = split * delta0 / self.a_lt if split > 0 else 0.0
        s_l = (1 - split) * delta0 / self.l_lt if split < 1 else 0.0
        m, n = MARKET_WORLD, BASE_WORLD
        dkey = self.ref[m]["key"] - self.ref[n]["key"]
        d = sum(self.w[b] * (self.ref[m][self.books[b]["ref"]] - self.ref[n][self.books[b]["ref"]])
                for b in self.phi_assets) / dkey
        t0 = (self.nss(m, s_a, s_l, 0.0) - self.nss(n, s_a, s_l, 0.0)) / dkey
        phi = (t0 - self.t_star) / d
        phi_a = p * phi
        phi_l = (1 - p) * phi * self.a_phi / self.l_phi if p < 1 else 0.0
        nss_all_assets = {w: self.nss(w, s_a, s_l, phi) for w in self.worlds}
        nss = {w: self.nss(w, s_a, s_l, phi_a, phi_l) for w in self.worlds}
        return {"split": split, "phi_split": p, "delta0": delta0, "sigma0_assets": s_a, "sigma0_liab": s_l,
                "phi": phi, "phi_assets": phi_a, "phi_liab": phi_l, "nss": nss,
                "volume_residual": max(abs(nss[w] - nss_all_assets[w]) for w in self.worlds),
                "t_real": (nss[m] - nss[n]) / dkey}

    def loans(self) -> list[str]:
        return [b for b in self.assets if "sector" in self.books[b]]

    def s_eff(self, b: str, world: str, sol: dict) -> float:
        spec = self.books[b]
        s = self.s_lt[b] + (sol["sigma0_assets"] if spec.get("lt_shift") else 0.0)
        if spec.get("phi"):
            s -= sol["phi_assets"] * (self.ref[world][spec["ref"]] - self.ref[self.h][spec["ref"]])
        return s

    def loan_margin(self, world: str, sol: dict) -> float:
        """m_W — кредитная маржа стационара мира (MODEL §4.5): доходность кредитного портфеля − CoR режима-опоры с
        κ-добавкой мира − смесь балансирующих активов."""
        loans = self.loans()
        y = sum(self.w[b] * (self.ref[world][self.books[b]["ref"]] + self.s_eff(b, world, sol)) for b in loans) \
            / sum(self.w[b] for b in loans)
        cor = self.cor_lt + self.kappa * max(0.0, self.real[world] - self.real[BASE_WORLD])
        bal = self.sec * (self.ref[world][self.books["securities"]["ref"]] + self.s_lt["securities"]) \
            + (1 - self.sec) * (self.ref[world][self.books["liquidity"]["ref"]] + self.s_lt["liquidity"])
        return y - cor - bal

    def table(self, sol: dict, floors: dict) -> dict:
        """Спреды, ставки и стоимости стационара по мирам при решении sol."""
        ws = self.worlds
        return {
            "split": sol["split"], "phi_split": sol["phi_split"],
            "sigma0_assets": sol["sigma0_assets"], "sigma0_liab": sol["sigma0_liab"], "phi": sol["phi"],
            "phi_assets": sol["phi_assets"], "phi_liab": sol["phi_liab"], "nss": sol["nss"],
            "lt_spread": {b: {w: round(self.s_eff(b, w, sol), 6) for w in ws} for b in self.loans()},
            "loan_rate_lt_pct": {b: {w: round(100 * (self.ref[w][self.books[b]["ref"]] + self.s_eff(b, w, sol)), 2)
                                     for w in ws} for b in self.loans()},
            "liability_cost_lt_pct": {b: {w: round(100 * self.liab_cost(b, w, sol["sigma0_liab"], sol["phi_liab"]), 2)
                                          for w in ws} for b in self.liabs},
            "liability_addon_pp": {w: round(100 * sol["phi_liab"] * self.x(w), 3) for w in ws},
            "loan_margin_pp": {w: round(100 * self.loan_margin(w, sol), 3) for w in ws},
            "violations": {w: [b for b in self.loans() if self.s_eff(b, w, sol) < floors[b] - 1e-12] for w in ws},
        }


def max_split(stn: Stationary, floors: dict, world: str, p: float | None = None) -> tuple[float | None, str | None]:
    """Наибольший split ∈ [0; 1], при котором все кредитные книги мира не ниже пола (при доле сжатия p);
    (None, книга) — такого split нет."""
    def worst(split: float) -> tuple[float, str]:
        sol = stn.solve(split, p)
        return min((stn.s_eff(b, world, sol) - floors[b], b) for b in stn.loans())
    if worst(1.0)[0] >= 0:
        return 1.0, None
    if worst(0.0)[0] < 0:
        return None, worst(0.0)[1]
    lo, hi = 0.0, 1.0                         # запас монотонен по split (σ0_A линейна, φ от split не зависит)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if worst(mid)[0] >= 0 else (lo, mid)
    return lo, worst(hi)[1]


def compute() -> dict:
    book, rec = evlib.template(), evlib.record()
    stn = Stationary(book, rec)
    floors = {b: float(v) for b, v in book["checks"]["lt_spread_floor"].items()}
    split_book, p_book = float(book["nii"]["sigma0_split"]), stn.p_book
    worlds, weights = stn.worlds, book["joint"]["world_prob"]
    exact, binding = max_split(stn, floors, stn.h)
    center = None if exact is None else floor_step(exact)
    per_world = {}
    for w in worlds:
        m, b = max_split(stn, floors, w)
        sol0 = stn.solve(0.0)
        per_world[w] = {"max_split_at_book_phi_split": None if m is None else round(m, 5), "binding_book": b,
                        "margin_at_split_0_pp": {x: round(100 * (stn.s_eff(x, w, sol0) - floors[x]), 3)
                                                 for x in stn.loans()}}
    zero = dict(floors, **{b: 0.0 for b in floors if book["nii"]["books"][b]["sector"] == "corporate"})
    zero_spread = floor_step(max_split(stn, zero, stn.h)[0])     # справка: доля при нулевом спреде корпоративной книги
    axis_high = center                                           # ось — в области гейта: выше центра пол нарушен
    table = {tag: stn.table(stn.solve(split), floors)
             for tag, split in (("split_1", 1.0), ("book", split_book), ("split_0", 0.0))}
    at_book = table["book"]["violations"]
    corp_floor = {}
    for f in (0.0, 0.0025, 0.005, 0.0075):       # чувствительность центра к полу корпоративной книги; None — пол не
        alt = dict(floors, **{b: f for b in floors if book["nii"]["books"][b]["sector"] == "corporate"})   # достижим
        m = max_split(stn, alt, stn.h)[0]
        corp_floor[f"{f:.4f}"] = None if m is None else floor_step(m)
    axis = next((a for a in book["valuation"]["uncertainty"]["axes"] if a["paths"] == ["nii.sigma0_split"]), None)
    bad = []
    if center is None or abs(split_book - center) > 1e-12:
        bad.append(f"nii.sigma0_split: книга {split_book} против правила {center}")
    if set(floors) != set(stn.loans()):
        bad.append(f"checks.lt_spread_floor: книги {sorted(floors)} против кредитных книг {sorted(stn.loans())}")
    if axis is None or [axis["low"], axis["high"]] != [0.0, axis_high]:
        bad.append(f"ось nii.sigma0_split: книга {axis and [axis['low'], axis['high']]} против [0.0, {axis_high}]")
    for end in (0.0, axis_high or 0.0):          # на обоих концах оси гейт проходит в мире отсчёта (сжатия там нет)
        sol = stn.solve(end)
        low = [b for b in stn.loans() if stn.s_eff(b, stn.h, sol) < floors[b] - 1e-12]
        if low:
            bad.append(f"ось nii.sigma0_split: на конце {end} гейт нарушен в мире {stn.h}: {low}")
    for flag in ("lt_shift", "phi"):
        if bool(stn.books["retail_current"].get(flag)) != bool(stn.books["retail_term"].get(flag)):
            bad.append(f"nii.books.retail_current / retail_term: {flag} обязан совпадать (MODEL §4.5)")
    return {
        "rule": {"definition": "шаг 1 правила центров: наибольший split, при котором гейт lt_spread_floor проходит в мире "
                               "отсчёта (сжатия φ там нет); округление вниз до 0,01",
                 "reference_world": stn.h, "max_split_exact": None if exact is None else round(exact, 5),
                 "binding_book": binding, "center": center, "book": split_book, "phi_split_book": p_book,
                 "worlds_violating_at_book": [w for w in worlds if at_book[w]],
                 "mass_at_book": round(sum(weights[w] for w in worlds if at_book[w]), 6)},
        "stationary": {"reference_world": stn.h, "target_eng": stn.target_eng,
                       "nss_H_without_shift": stn.nss(stn.h, 0.0, 0.0, 0.0), "delta0": stn.solve(1.0)["delta0"],
                       "weight_assets_lt_shift": stn.a_lt, "weight_liabilities_lt_shift_H": stn.l_lt,
                       "assets_lt_shift": [b for b in stn.assets if stn.books[b].get("lt_shift")],
                       "liabilities_lt_shift": [b for b in stn.liabs if stn.books[b].get("lt_shift")],
                       "c_star": {w: round(stn.c_star(w), 5) for w in worlds}, "ref_lt": stn.ref},
        "floors": floors, "worlds": per_world, "table": table,
        "axis": {"low": 0.0, "high": axis_high,
                 "high_rule": "центр правила: наибольшая доля, при которой гейт lt_spread_floor проходит в мире отсчёта; "
                              "ось лежит в области гейта (выше центра пол корпоративной книги нарушен)",
                 "book": axis and [axis["low"], axis["high"]]},
        "zero_spread_split": zero_spread,
        "center_by_corporate_floor": corp_floor,
        "book_mismatches": bad,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    res = compute()
    evlib.write_json(evlib.Path(OUT) / "sigma0_split_out.json", res)
    r, s = res["rule"], res["stationary"]
    print(f"Δ0 = {100 * s['delta0']:+.3f} п.п. (цель {100 * s['target_eng']:.3f} % − Nss(H) без сдвига "
          f"{100 * s['nss_H_without_shift']:.3f} %); вес активов с lt_shift {s['weight_assets_lt_shift']:.3f}, "
          f"пассивов в мире H {s['weight_liabilities_lt_shift_H']:.3f}")
    for tag, t in res["table"].items():
        print(f"{tag}: split {t['split']:.2f}  σ0_A {100 * t['sigma0_assets']:+.3f}  σ0_L {100 * t['sigma0_liab']:+.3f} п.п.  "
              f"φ {t['phi']:.4f} (φ_A {t['phi_assets']:.4f}, φ_L {t['phi_liab']:.4f} при phi_split {t['phi_split']:g})")
        for b, row in t["lt_spread"].items():
            print(f"   {b:13s} пол {100 * res['floors'][b]:.2f}  s_eff " +
                  "  ".join(f"{w} {100 * v:+.3f}" for w, v in row.items()))
        print("   нарушения:", {w: v for w, v in t["violations"].items() if v} or "нет")
    for w, v in res["worlds"].items():
        print(f"мир {w}: наибольший split при phi_split книги {v['max_split_at_book_phi_split']}; связывает {v['binding_book']}")
    print(f"правило (мир {r['reference_world']}): {r['max_split_exact']} → центр {r['center']}; книга {r['book']}; "
          f"нарушения при книге: {r['worlds_violating_at_book'] or 'нет'} (масса {r['mass_at_book']})")
    print("центр при поле корпоративной книги:", res["center_by_corporate_floor"], "; ось (область гейта):",
          res["axis"]["low"], "…", res["axis"]["high"], "; доля при нулевом спреде корпоративной книги (справка):",
          res["zero_spread_split"])
    print("расхождения с книгой:", res["book_mismatches"] or "нет")
    return 1 if res["book_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
