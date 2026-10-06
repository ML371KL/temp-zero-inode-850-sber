"""Проверки прогона: инварианты (блокируют), гейты правдоподобия (требуют объяснения) — М§14.

Инварианты уровня клетки и книги — здесь; инварианты уровня выпуска
(`release_numbers`, `jump_guard`, `payload_contract`, `exdate_jump`) — model/payload.py.
Коридоры гейтов — в книге (`checks.*`), не в коде. Масса гейта — суммарная
вероятность клеток слоя «свой взгляд», где условие нарушено; у гейтов уровня
выпуска — 1. Сработавший гейт требует действующего объяснения — записи в
`data/assumptions/gate_explanations.yaml` {explanation, expected_mass |
expected_mass_range, valid_until}: сборку роняют нет записи, истёкший срок
(действует включительно), масса вне коридора 0,4 × lo − 0,02 … 1,5 × hi + 0,02.
Объяснение, срок которого истекает в ближайшие 30 дней, сборку не роняет и тревогой не
является: выпуск несёт флаг `explanation_expiring` (М§14.2–§14.3).
Флаги ядра (М§14.3, `ni_jump`) — тем же типом `Finding` (kind «flag»); флаги живых входов —
model/live.py. DDM = RI — в печатаемой сетке и во всех прогонах полосы (`band.ddm_ri_max`).

Сообщения гейтов и инвариантов печатает витрина: словами, доли — в процентах, разности долей
(спреды к опоре, сдвиги, запасы) — в п.п., число клеток и кварталов — согласованным падежом,
малые отклонения — как допуск («1·10⁻⁹»), без кодов полей, `inf`, кортежей и e-нотации (П§0.2).
Годовые ЧПМ, CoR и CIR гейта `guidance_gap` — в упр. базисе по правилу «год с отчётными
кварталами» (`model.grid.year_mgmt`, М§4.6). Границы узла гайденса — одна функция `guidance_band` (гейт и выпуск):
у `point` — ± `tol` узла фактов, без него — половина единицы последнего разряда числа из `text`.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from model.book import BOOK_TITLES, ROOT
from model.book_schema import BookError, FactsError, validate
from model.dividends import dps_history
from model.grid import REFERENCE_REGIME, REFERENCE_SCENARIO, GridRun, year_mgmt
from model.nii import nss_value, solve_transmission
from model.timeline import period_words

EXPLANATIONS_PATH = ROOT / "data" / "assumptions" / "gate_explanations.yaml"
WARN_DAYS = 30                               # плашка «объяснение истекает» — за 30 дней до срока (М§14.2)
MASS_LOW = (0.4, 0.02)                       # коридор массы: 0,4 × lo − 0,02 …
MASS_HIGH = (1.5, 0.02)                      # … 1,5 × hi + 0,02
REL_TOL = 1e-9                               # тождества BV, баланса, ОПУ (относительно)
PROB_TOL = 1e-12                             # суммы вероятностей

INVARIANTS = ("probabilities", "bv_identity", "balance_identity", "pnl_identity", "ddm_equals_ri",
              "dps_history", "transmission_solved", "dividend_bounds", "governance_sum", "book_schema")
GATES = ("pb_by_world", "roe_range", "cor_range", "nim_range", "nim_path_joint", "cir_range",
         "roe_k_homogeneity", "guidance_gap", "capital_gap", "k_gt_g", "roe_gt_g", "terminal_share",
         "real_rate", "bridge_drift", "transmission_pairs", "lt_spread_floor", "off_band_shift",
         "m_crisis_vs_cbr", "manual_input_overdue")
CORE_FLAGS = ("ni_jump",)
# Доли, которые делят сдвиг σ0 и сжатие φ между кредитными книгами и средствами клиентов (М§4.5): гейт
# `lt_spread_floor` проверяется и на концах осей по этим ключам.
SHARE_KEYS = ("nii.sigma0_split", "nii.phi_split")
SHARE_WORDS = {"nii.sigma0_split": "доле сдвига σ0 на кредитных книгах",
               "nii.phi_split": "доле сжатия φ на кредитных книгах"}
PERCENT = 100                                # доля → проценты в текстах сообщений


def pct(x: float, digits: int = 2) -> str:
    """Доля словами: 0.0125 → «1,25 %» (тексты сообщений, П§0.2)."""
    return f"{PERCENT * float(x):.{digits}f}".replace(".", ",").replace("-", "−") + " %"


def pp(x: float, digits: int = 2) -> str:
    """Разность долей словами: 0.003 → «0,30 п.п.»."""
    return pct(x, digits)[:-2] + " п.п."


def num(x: float, digits: int = 3) -> str:
    return f"{float(x):.{digits}f}".replace(".", ",").replace("-", "−")


NBSP = "\u00a0"                              # неразрывный пробел: разрядка чисел в текстах
SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def grouped(n: int) -> str:
    """Целое с разрядкой: 2000 → «2 000» (неразрывный пробел)."""
    return f"{int(n):,}".replace(",", NBSP)


def sci(x: float, digits: int = 1) -> str:
    """Малое число как допуск: 6.9e-18 → «6,9·10⁻¹⁸», 1e-9 → «1·10⁻⁹», 0 → «0» (П§0.2: без e-нотации)."""
    x = float(x)
    if x == 0:
        return "0"
    mantissa, _, exponent = f"{x:.{digits}e}".partition("e")
    mantissa = mantissa.rstrip("0").rstrip(".").replace(".", ",").replace("-", "−")
    return f"{mantissa}·10{str(int(exponent)).translate(SUPERSCRIPT)}"


def _one(n: int) -> bool:
    return n % 10 == 1 and n % 100 != 11


def in_cells(n: int) -> str:
    """«в 1 клетке», «в 5 клетках», «в 21 клетке» (предложный падеж)."""
    return f"в {n} {'клетке' if _one(n) else 'клетках'}"


def in_quarters(n: int) -> str:
    """«в 1 квартале», «в 5 кварталах» (предложный падеж)."""
    return f"в {n} {'квартале' if _one(n) else 'кварталах'}"


def band_words(band: Sequence[float | None], digits: int = 2) -> str:
    """Коридор словами: «1,00…2,00 %», «не выше 1,40 %», «не ниже 13,30 %»; вырожденный — одно число."""
    lo, hi = band
    if lo is not None and lo == hi:
        return pct(lo, digits)
    if lo is None and hi is None:
        return "без границ"
    if lo is None:
        return f"не выше {pct(hi, digits)}"
    if hi is None:
        return f"не ниже {pct(lo, digits)}"
    return f"{pct(lo, digits)[:-2]}…{pct(hi, digits)}"


@dataclass(frozen=True)
class Finding:
    name: str                              # имя из М§14.1–§14.3
    kind: str                              # "invariant" | "gate" | "flag"
    fired: bool
    mass: float | None
    cells: tuple[str, ...]
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)


def _inv(name: str, bad: Sequence[str], message_ok: str, message_bad: str, **detail) -> Finding:
    return Finding(name=name, kind="invariant", fired=bool(bad), mass=None, cells=tuple(bad[:36]),
                   message=message_bad if bad else message_ok, detail=detail)


# ------------------------------------------------------------------ инварианты


def check_invariants(run: GridRun, band: Any = None) -> list[Finding]:
    """Инварианты М§14.1 уровня клетки и книги; `band` — полоса: DDM = RI и в её прогонах (М§6.4)."""
    ctx, book = run.ctx, run.ctx.book
    out: list[Finding] = []
    # probabilities: каждая таблица — неотрицательные числа в сумме 1 (сумма 1 при отрицательном весе —
    # не распределение: клетка вошла бы в оценку с обратным знаком)
    bad, negative = [], []

    def table(name: str, values) -> None:
        values = [float(v) for v in values]
        if abs(sum(values) - 1) > PROB_TOL:
            bad.append(name)
        if min(values) < 0:
            negative.append(name)

    table("вероятности режимов книги", book.get("joint.regime_prob").values())
    for r, row in book.get("joint.reg_prob_given_regime").items():
        table(f"вероятности сценариев капитала режима {r}", row.values())
    for name, layer in run.layers.items():
        table(f"веса миров слоя {name}", layer.world_weights.values())
        table(f"вероятности клеток слоя {name}", layer.prob.values())
    table("вероятности режимов после наблюдений", ctx.posterior.values())
    broken = (["суммы вероятностей ≠ 1: " + ", ".join(bad)] if bad else []) + (
        ["отрицательные вероятности: " + ", ".join(negative)] if negative else [])
    out.append(_inv("probabilities", bad + negative, "все таблицы вероятностей неотрицательны и в сумме 1",
                    "; ".join(broken)))
    # тождества по кварталам
    bv_bad, bal_bad, pnl_bad = [], [], []
    for c in run.cells:
        q_ = c.quarters
        for q in range(1, len(q_["bv"])):
            lhs = q_["bv"][q]
            rhs = (q_["bv"][q - 1] + q_["ni_sh"][q] - q_["coupon"][q] * (1 - q_["tau_stat"][q])
                   + q_["oci"][q] + q_["om"][q] - q_["div"][q])
            if abs(lhs - rhs) > REL_TOL * max(1.0, abs(lhs)):
                bv_bad.append(f"{c.label} {ctx.timeline.period(q)}")
            a, le = q_["assets"][q], q_["liabilities_equity"][q]
            if abs(a - le) > REL_TOL * max(1.0, abs(a)):
                bal_bad.append(f"{c.label} {ctx.timeline.period(q)}")
            pbt = (q_["nii"][q] - q_["llp"][q] - q_["fvc"][q] + q_["fees"][q] + q_["ins"][q]
                   + q_["misc"][q] + q_["fvr"][q] + q_["noncore"][q] - q_["opex"][q] + q_["one_off"][q])
            ni = q_["pbt"][q] - q_["tax"][q] - q_["ot"][q]
            if (abs(pbt - q_["pbt"][q]) > REL_TOL * max(1.0, abs(pbt))
                    or abs(ni - q_["ni"][q]) > REL_TOL * max(1.0, abs(ni))):
                pnl_bad.append(f"{c.label} {ctx.timeline.period(q)}")
    out.append(_inv("bv_identity", bv_bad, "капитал на конец квартала равен капиталу на начало плюс совокупный "
                                           "доход минус дивиденд — во всех кварталах клеток",
                    f"тождество капитала нарушено {in_quarters(len(bv_bad))}"))
    out.append(_inv("balance_identity", bal_bad, "активы равны пассивам с капиталом во всех кварталах",
                    f"тождество баланса нарушено {in_quarters(len(bal_bad))}"))
    out.append(_inv("pnl_identity", pnl_bad, "прибыль до налога равна сумме строк, чистая прибыль — прибыли до "
                                             "налога за вычетом налогов",
                    f"тождество отчёта о прибыли нарушено {in_quarters(len(pnl_bad))}"))
    tol = float(book.get("checks.ddm_ri_tol"))
    ddm_bad = [c.label for c in run.cells if abs(c.v_ddm - c.v_ri) > tol * abs(c.v_ri)]
    worst = ddm_ri_max(run)
    band_worst = getattr(band, "ddm_ri_max", None)
    where = "во всех клетках"
    if band_worst is not None:
        where = f"во всех клетках сетки и во всех {grouped(getattr(band, 'draws', 0))} прогонах полосы"
        if band_worst > tol:
            ddm_bad = ddm_bad + [f"полоса: наибольшая разность {sci(band_worst)}"]
    both = max(worst, band_worst or 0.0)
    out.append(_inv("ddm_equals_ri", ddm_bad,
                    f"дивидендная модель равна остаточному доходу {where} (наибольшая относительная разность "
                    f"{sci(both)})",
                    f"дивидендная модель расходится с остаточным доходом: {', '.join(ddm_bad[:3])} (наибольшая "
                    f"относительная разность {sci(both)} при допуске {sci(tol)})", worst=worst,
                    band_worst=band_worst))
    # dps_history
    try:
        rows = dps_history(book, ctx.facts)
        bad = [str(r["year"]) for r in rows if not r["ok"]]
        out.append(_inv("dps_history", bad, "формула политики воспроизводит объявленный дивиденд на акцию до копейки",
                        "дивиденд на акцию истории не воспроизводится: " + ", ".join(bad), rows=rows))
    except (FactsError, BookError) as exc:
        out.append(_inv("dps_history", ["история"], "", f"проверка дивиденда на акцию не выполнена: {exc}"))
    # transmission_solved
    tr = ctx.transmission
    ttol = float(book.get("checks.transmission_tol"))
    nss_ref = nss_value(book, ctx.facts, tr.reference_world, 0.0, tr.sigma0, tr.phi, tr.sigma0_liab)
    e1, e2 = abs(nss_ref - tr.target_eng), abs(tr.t_real - tr.t_target)
    bad = [x for x, e in ((f"ЧПМ сквозь цикл мира {tr.reference_world}", e1), ("реализованная передача", e2))
           if e > ttol]
    out.append(_inv("transmission_solved", bad,
                    f"ЧПМ сквозь цикл мира {tr.reference_world} равен цели (отклонение {sci(e1)}), реализованная "
                    f"передача равна цели книги (отклонение {sci(e2)})",
                    "передача ставки не решена: " + ", ".join(bad), nss_error=e1, t_error=e2))
    # dividend_bounds: вся выплата года — базовая, догоняющая и доля избытка — не больше запаса капитала
    # выбранной ступени (догоняющая и избыток — части Div_Y, а не добавка к границе; М§5.3 п. 4)
    bad = []
    for c in run.cells:
        for d in c.decisions:
            if d.dps < 0 or d.deferred_after < -1e-9:
                bad.append(f"{c.label} {d.year}")
            elif d.source == "model":
                h = d.headroom[d.step]
                if d.div > max(0.0, h) + REL_TOL * max(1.0, abs(d.div)):
                    bad.append(f"{c.label} {d.year}")
    out.append(_inv("dividend_bounds", bad, "дивиденд неотрицателен и не больше запаса капитала, отложенный пул "
                                            "неотрицателен",
                    f"границы дивиденда нарушены: {', '.join(bad[:5])}"))
    # governance_sum
    comps = book.get("valuation.governance.components")
    total = sum(float(x["sign"]) * float(x["value"]) for x in comps)
    g = float(book.get("valuation.governance.discount"))
    bad = ["governance"] if abs(total - g) > PROB_TOL else []
    out.append(_inv("governance_sum", bad, "дисконт за управление равен сумме каналов с их знаками",
                    f"дисконт за управление {pct(g, 3)} не равен сумме каналов {pct(total, 3)}"))
    # book_schema
    try:
        validate(book.data)
        out.append(_inv("book_schema", [], "книга в закрытой схеме", ""))
    except BookError as exc:
        out.append(_inv("book_schema", ["книга"], "", str(exc)))
    return out


def ddm_ri_max(run: GridRun) -> float:
    """max |V_DDM − V_RI| / |V_RI| по клеткам прогона (М§6.4)."""
    return max(abs(c.v_ddm - c.v_ri) / abs(c.v_ri) for c in run.cells)


# ------------------------------------------------------------------ гейты


def _gate(name: str, run: GridRun, bad_cells: Sequence[str], message: str, *, mass: float | None = None,
          **detail) -> Finding:
    prob = run.layers["analytical"].prob
    if mass is None:
        keys = {c.label: c.key for c in run.cells}
        mass = sum(prob[keys[x]] for x in set(bad_cells))
    return Finding(name=name, kind="gate", fired=bool(bad_cells) or bool(mass and mass > 0),
                   mass=mass, cells=tuple(bad_cells), message=message, detail=detail)


def _outside(x: float | None, rng: Sequence[float]) -> bool:
    return x is not None and not (rng[0] <= x <= rng[1])


NUMBER_IN_TEXT = re.compile(r"(\d+)(?:[.,](\d+))?\s*(%)?")


def text_tolerance(text: Any) -> float | None:
    """Половина единицы последнего разряда числа, как оно записано в гайденсе (М§14.2):
    «22%» → 0,005; «~6.2%» → 0,0005; «~0%» → 0,005; числа в тексте нет — None."""
    m = NUMBER_IN_TEXT.search(str(text or ""))
    if not m:
        return None
    unit = Decimal(1) / Decimal(10) ** len(m.group(2) or "")
    if m.group(3):
        unit /= PERCENT
    return float(unit / 2)


def guidance_band(node: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """Границы узла гайденса по `kind` (М§14.2, прил. B): `range` — [мин, макс]; `min` — (v, None);
    `max` — (None, v); `point` — v ± `tol` узла (нет `tol` — половина единицы последнего разряда числа из
    `text`). Открытая граница — None. Одна функция для гейта и выпуска."""
    v, kind = node.get("v"), node.get("kind")
    if v is None:
        return (None, None)
    if isinstance(v, (list, tuple)):
        if kind not in (None, "range"):
            raise FactsError(f"узел гайденса: список при kind = {kind!r}")
        return (float(v[0]), float(v[1]))
    v = float(v)
    if kind == "max":
        return (None, v)
    if kind == "min":
        return (v, None)
    if kind != "point":
        raise FactsError(f"узел гайденса: незнакомый kind {kind!r} (известны point, min, max, range)")
    tol = node.get("tol")
    if tol is None:
        tol = text_tolerance(node.get("text"))
    if tol is None:
        raise FactsError("узел гайденса point: нет ни tol, ни числа в text — допуск не определён (М§14.2)")
    return (v - float(tol), v + float(tol))


def inside(band: Sequence[float | None], x: float) -> bool:
    lo, hi = band
    return (lo is None or x >= lo) and (hi is None or x <= hi)


RANGE_WORDS = {"roe": "ROE года", "cor": "CoR года (базис движка)", "cir": "CIR года (базис движка)"}


def _band_share(part: Mapping[str, Any], n: int) -> str:
    """«в 807 из 2 000 прогонов (масса таких клеток в среднем по всем прогонам — 0,77 %)» — строка хвоста
    полосы (М§10). Масса усреднена по всем прогонам полосы, а не по тем, где такие клетки есть."""
    return (f"в {grouped(part['draws'])} из {grouped(n)} прогонов (масса таких клеток в среднем по всем "
            f"прогонам — {pct(part['mass'])})")


def _band_gap_words(tail: Mapping[str, Any] | None) -> str:
    """Добавка к сообщению гейта `capital_gap`: то же условие в прогонах полосы."""
    return "" if tail is None else f"; в полосе — {_band_share(tail['capital_gap'], tail['n'])}"


def _band_capital_words(tail: Mapping[str, Any] | None) -> str:
    """Добавка к сообщению гейта `roe_gt_g`: в прогонах полосы — клетки, где нужна докапитализация, и клетки
    с отрицательной стоимостью; сколько такие клетки весят в печатаемой полосе (М§7, §10)."""
    if tail is None:
        return ""
    text = f"; в полосе — {_band_share(tail['need_capital'], tail['n'])}"
    neg, shift = tail["negative"], tail["floor_shift"]
    if not neg["draws"]:
        return text + "; стоимость клетки ниже нуля — ни в одном прогоне"
    return (text + f"; стоимость клетки ниже нуля — {_band_share(neg, tail['n'])}: с ограничением нулём снизу "
            f"низ полосы был бы выше на {num(shift['low'], 1)} ₽, медиана — на {num(shift['median'], 1)} ₽")


def check_gates(run: GridRun, *, today: _dt.date | None = None,
                off_band: Mapping[str, Any] | None = None, band: Any = None) -> list[Finding]:
    """Гейты М§14.2 и флаги ядра М§14.3. `off_band` — диагностика сдвига положения осей вне полосы
    (`model.uncertainty.off_band_shift`: {rub, limit, axes}); нет — считается здесь, если такие оси есть.
    `band` — полоса: сообщения гейтов `capital_gap` и `roe_gt_g` называют и хвост её прогонов (условие и
    масса гейта — по-прежнему печатаемой сетки: прогоны полосы объяснения не требуют)."""
    ctx, book = run.ctx, run.ctx.book
    bridge = ctx.bridge
    today = today or ctx.clock.valuation_date
    tail = band.tail(run.lam) if band is not None else None
    out: list[Finding] = []
    ch = lambda k: book.get(f"checks.{k}")
    # pb_by_world
    bad = [c.label for c in run.cells if _outside(c.v_ri / c.bv_v, ch(f"pb_by_world.{c.world}"))]
    out.append(_gate("pb_by_world", run, bad, f"P/B клетки вне коридора мира {in_cells(len(bad))}"))
    # годовые коридоры ROE, CoR, CIR; квартальный ЧПМ (упр. базис)
    for name, key, field_ in (("roe_range", "roe_range", "roe"), ("cor_range", "cor_range", "cor"),
                              ("cir_range", "cir_range", "cir")):
        rng = ch(key)
        bad, worst = [], []
        for c in run.cells:
            vals = [v for v in c.annual[field_] if v is not None]
            if any(_outside(v, rng) for v in vals):
                bad.append(c.label)
                worst.append((min(vals), max(vals)))
        span = (min(w[0] for w in worst), max(w[1] for w in worst)) if worst else None
        out.append(_gate(name, run, bad, f"{RANGE_WORDS[field_]} вне коридора {band_words(rng)} {in_cells(len(bad))}"
                         + (f" (размах {band_words(span)})" if span else ""), span=span))
    rng = ch("nim_range")
    bad = []
    for c in run.cells:
        nims = [bridge.to_mgmt_nim(v) for v in c.quarters["nim"][1:] if v is not None]
        if any(_outside(v, rng) for v in nims):
            bad.append(c.label)
    out.append(_gate("nim_range", run, bad, f"ЧПМ квартала (упр.) вне коридора {in_cells(len(bad))}"))
    out.append(_nim_joint_gate(run))
    # однородность ROE'_T − k_T
    lo_hi = ch("roe_k_spread.range")
    bad = [c.label for c in run.cells if _outside(c.roe_t - c.k_t, lo_hi)]
    prob = run.layers["analytical"].prob
    by_world = {}
    for w in book.get("worlds.ids"):
        cells = [c for c in run.cells if c.world == w]
        mass = sum(prob[c.key] for c in cells)
        by_world[w] = (sum(prob[c.key] * (c.roe_t - c.k_t) for c in cells) / mass if mass
                       else sum(c.roe_t - c.k_t for c in cells) / len(cells))
    gap = max(by_world.values()) - min(by_world.values())
    world_fired = gap > float(ch("roe_k_spread.max_world_gap"))
    f = _gate("roe_k_homogeneity", run, bad,
              "ROE терминала − стоимость капитала по мирам: "
              + ", ".join(f"{w} {pp(v)}" for w, v in by_world.items()) + f"; разрыв {pp(gap)}",
              by_world=by_world, gap=gap)
    if world_fired:
        f = Finding(f.name, "gate", True, 1.0, f.cells, f.message + " — больше предела книги", f.detail)
    out.append(f)
    # guidance_gap
    out.append(_guidance_gate(run))
    # capital_gap
    bad = [c.label for c in run.cells if "capital_gap" in c.flags]
    first = min((c.gap_period for c in run.cells if c.gap_period), default=None)
    out.append(_gate("capital_gap", run, bad, f"норматив ниже пола {in_cells(len(bad))}"
                     + (f", первый квартал — {period_words(first)}" if first else "") + _band_gap_words(tail),
                     first_period=first, band=None if tail is None else tail["capital_gap"]))
    bad = [c.label for c in run.cells if c.g_capped]
    out.append(_gate("k_gt_g", run, bad, f"защита роста терминала (на 1 б.п. ниже стоимости капитала) "
                                         f"сработала {in_cells(len(bad))}"))
    bad = [c.label for c in run.cells if c.roe_t <= c.g_t]
    out.append(_gate("roe_gt_g", run, bad, f"ROE терминала не выше роста {in_cells(len(bad))}"
                     + _band_capital_words(tail), band=tail))
    bad = [c.label for c in run.cells if _outside(c.terminal_share, ch("terminal_share"))]
    out.append(_gate("terminal_share", run, bad, f"доля терминала вне коридора {in_cells(len(bad))}"))
    bad = [c.label for c in run.cells if _outside(c.real_rate, ch("real_rate"))]
    out.append(_gate("real_rate", run, bad, f"реальная ставка мира вне коридора {in_cells(len(bad))}"))
    out.append(_bridge_gate(run))
    out.append(_pairs_gate(run))
    out.append(_lt_floor_gate(run))
    out.append(_off_band_gate(run, off_band))
    out.append(_crisis_gate(run))
    out.append(_overdue_gate(run, today))
    gates = sorted(out, key=lambda f: GATES.index(f.name))
    return gates + [_ni_jump_flag(run)]


def _nim_joint_gate(run: GridRun) -> Finding:
    """Стык ближнего пути ЧПМ и сквозь цикл (М§14.2): первые N кварталов не выше
    max(ЧПМ якоря, Nss мира) + допуск, всё в упр. базисе через мост."""
    ctx, book = run.ctx, run.ctx.book
    br, tr = ctx.bridge, ctx.transmission
    n = int(book.get("checks.nim_path_joint.quarters"))
    tol = float(book.get("checks.nim_path_joint.tolerance"))
    nim0 = ctx.facts.need("nii_books", "nim_eng_q")
    bad, worst = [], []
    for c in run.cells:
        cap = max(br.to_mgmt_nim(nim0), br.to_mgmt_nim(tr.nss[c.world])) + tol
        over = [(q, br.to_mgmt_nim(c.quarters["nim"][q])) for q in range(1, min(n, ctx.timeline.Q) + 1)
                if c.quarters["nim"][q] is not None and br.to_mgmt_nim(c.quarters["nim"][q]) > cap]
        if over:
            bad.append(c.label)
            q, v = max(over, key=lambda t: t[1] - cap)
            worst.append((v - cap, c.label, ctx.timeline.period(q), v, cap))
    msg = (f"ЧПМ (упр.) первых {n} кварталов выше большего из ЧПМ якоря ({pct(br.to_mgmt_nim(nim0))}) и ЧПМ "
           f"сквозь цикл мира более чем на {pp(tol)} {in_cells(len(bad))}")
    if worst:
        _, lab, per, v, cap = max(worst)
        msg += f"; наибольшее превышение — {lab}, {period_words(per)}: {pct(v)} при пороге {pct(cap)}"
    return _gate("nim_path_joint", run, bad, msg,
                 nss_mgmt={w: br.to_mgmt_nim(v) for w, v in tr.nss.items()}, nim0_mgmt=br.to_mgmt_nim(nim0))


def _pairs_gate(run: GridRun) -> Finding:
    """Парные передачи соседних по key^LT миров в коридоре книги (М§4.5, §14.2); масса 1."""
    tr, book = run.ctx.transmission, run.ctx.book
    lo, hi = (float(x) for x in book.get("checks.transmission_pairs"))
    parts, fired = [], False
    for key, v in tr.pairs.items():
        if v is None:
            parts.append(f"{key.replace('_', '→')}: не определена (долгосрочные ключевые миров равны)")
            continue
        out = not lo <= v <= hi
        fired |= out
        parts.append(f"{key.replace('_', '→')} {num(v)} — {'вне коридора' if out else 'в коридоре'} "
                     f"{num(lo, 2)}…{num(hi, 2)}")
    return Finding("transmission_pairs", "gate", fired, 1.0 if fired else 0.0, (),
                   "; ".join(parts) or "пар миров нет", {"pairs": dict(tr.pairs)})


def share_axis_ends(book: Any) -> list[dict[str, float]]:
    """Углы области осей долей `nii.sigma0_split` и `nii.phi_split` (М§4.5): сочетания концов осей полосы
    по этим ключам, кроме самих значений книги. Эффективный LT-спред линеен по обеим долям, поэтому
    наименьший спред на осях — в одном из углов."""
    ends: list[list[tuple[str, float]]] = []
    for ax in book.get("valuation.uncertainty.axes") or []:
        paths = [p for p in ax.get("paths") or [] if p in SHARE_KEYS]
        if ax.get("kind") != "value" or not paths:
            continue
        ends.append([(p, float(end)) for p in paths for end in (ax["low"], ax["high"])])
    corners = [dict(combo) for combo in itertools.product(*ends)] if ends else []
    centre = {p: float(book.get(p)) for p in SHARE_KEYS}
    out: list[dict[str, float]] = []
    for corner in corners:
        if any(corner[p] != centre[p] for p in corner) and corner not in out:
            out.append(corner)
    return out


def _lt_floor_gate(run: GridRun) -> Finding:
    """Эффективный LT-спред кредитной книги к опоре в стационаре мира ниже пола книги (М§4.5, §14.2).

    Проверяется при значениях книги и в углах области осей долей `nii.sigma0_split`, `nii.phi_split`:
    оси долей обязаны лежать там, где гейт проходит, — иначе половина прогонов полосы считалась бы на
    кредитных спредах ниже пола. Масса — вес миров слоя «свой взгляд», где условие нарушено хотя бы у одной
    кредитной книги (при значениях книги или в углу осей)."""
    ctx, book = run.ctx, run.ctx.book
    floors = book.get("checks.lt_spread_floor")

    def below(lt_spread: Mapping[str, Mapping[str, float]]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for b, row in lt_spread.items():
            if b not in floors:
                raise BookError(f"checks.lt_spread_floor.{b}: нет пола кредитной книги (М§14.2)")
            floor = float(floors[b])
            for w, s in row.items():
                if s < floor:
                    found.setdefault(w, []).append(f"{BOOK_TITLES.get(b, b).lower()} {pp(s)} при поле {pp(floor)}")
        return found

    def worlds_text(found: Mapping[str, Sequence[str]]) -> str:
        return "; ".join(f"мир {w} — {', '.join(v)}" for w, v in found.items())

    bad = below(ctx.transmission.lt_spread)
    corners = share_axis_ends(book)
    at_ends = []
    for corner in corners:
        found = below(solve_transmission(book.with_overrides(corner), ctx.facts, ctx.bridge).lt_spread)
        if found:
            at_ends.append({"shares": corner, "worlds": sorted(found), "text": worlds_text(found)})
    worlds = sorted(set(bad) | {w for e in at_ends for w in e["worlds"]})
    cells = [c.label for c in run.cells if c.world in worlds]
    if bad:
        msg = "долгосрочный спред кредитной книги к опорной ставке ниже пола: " + worlds_text(bad)
    elif at_ends:
        msg = ("долгосрочный спред кредитной книги к опорной ставке ниже пола на концах осей долей: "
               + "; ".join("при " + ", ".join(f"{SHARE_WORDS[p]} {pct(v, 1)}" for p, v in e["shares"].items())
                           + f" — {e['text']}" for e in at_ends))
    else:
        msg = "долгосрочные спреды кредитных книг к опорным ставкам не ниже полов во всех мирах" \
            + (" — при значениях книги и на концах осей долей" if corners else "")
    return _gate("lt_spread_floor", run, cells, msg, worlds=worlds,
                 lt_spread={b: dict(row) for b, row in ctx.transmission.lt_spread.items()},
                 axis_ends=[{"shares": e["shares"], "worlds": e["worlds"]} for e in at_ends])


def _off_band_gate(run: GridRun, off_band: Mapping[str, Any] | None) -> Finding:
    """|Σ сдвигов положения осей вне полосы| > print_step / 2 (М§10, §14.2); масса 1; пусто — молчит."""
    book = run.ctx.book
    axes = book.get("valuation.uncertainty.off_band_axes") or []
    if not axes:
        return Finding("off_band_shift", "gate", False, 0.0, (), "все оси книги — в полосе", {"rub": 0.0, "axes": 0})
    if off_band is None:
        from model.uncertainty import off_band_shift          # полоса импортирует сетку, не наоборот
        off_band = off_band_shift(book, run.ctx.facts, run.ctx.live, run.point)
    rub, limit = float(off_band["rub"]), float(off_band["limit"])
    fired = abs(rub) > limit
    msg = (f"оси вне полосы ({len(axes)}) сдвигают заголовок на {num(rub, 2)} ₽ при пороге {num(limit, 2)} ₽ "
           f"(половина шага печати)")
    return Finding("off_band_shift", "gate", fired, 1.0 if fired else 0.0, (), msg,
                   {"rub": rub, "limit": limit, "axes": len(axes)})


def _key_before(run: GridRun) -> dict[int, float | None]:
    """Средняя ключевая кварталов до сетки (q ≤ 0): факты `pnl_quarterly → key_avg`, якорь — nii_books."""
    tl, facts = run.ctx.timeline, run.ctx.facts
    out: dict[int, float | None] = {}
    for per, row in facts.periods("pnl_quarterly").items():
        q = tl.index(per)
        if q <= 0:
            node = row.get("key_avg")
            out[q] = float(node["v"]) if isinstance(node, Mapping) and node.get("v") is not None else None
    anchor = facts.v("nii_books", "key_avg_anchor_q") if facts.has("nii_books", "key_avg_anchor_q") else None
    if anchor is not None:
        out[0] = anchor
    return out


def _ni_jump_flag(run: GridRun) -> Finding:
    """Флаг «прыжок ЧП» (М§14.3): клетки (W, norm, schedule), первые N кварталов, г/г > порога при
    неизменной ключевой; кварталы до сетки — факты (ЧП акционерам и средняя ключевая)."""
    ctx, book = run.ctx, run.ctx.book
    tl = ctx.timeline
    n = int(book.get("checks.ni_jump.quarters"))
    yoy_max = float(book.get("checks.ni_jump.yoy"))
    key_tol = float(book.get("checks.ni_jump.key_tol"))
    keys_before = _key_before(run)
    hist = ctx.prep.hist["ni_sh"]
    jumps, cells = [], []
    for w in book.get("worlds.ids"):
        try:
            c = run.cell(w, REFERENCE_REGIME, REFERENCE_SCENARIO)
        except KeyError:
            continue
        key_w = ctx.worlds[w].key
        for q in range(1, min(n, tl.Q) + 1):
            j = q - 4
            ni_prev = c.quarters["ni_sh"][j] if j >= 1 else hist.get(j)
            key_prev = key_w[j] if j >= 1 else keys_before.get(j)
            ni_now = c.quarters["ni_sh"][q]
            if ni_prev is None or key_prev is None or ni_now is None or ni_prev <= 0:
                continue
            growth = ni_now / ni_prev - 1
            if growth > yoy_max and abs(key_w[q] - key_prev) <= key_tol:
                jumps.append(f"{c.label}, {period_words(tl.period(q))}: ЧП г/г {pct(growth, 0)} при ключевой "
                             f"{pct(key_prev)} → {pct(key_w[q])}")
                if c.label not in cells:
                    cells.append(c.label)
    detail = ("; ".join(jumps[:6]) + (f" … и ещё {len(jumps) - 6}" if len(jumps) > 6 else "")) if jumps else None
    return Finding("ni_jump", "flag", bool(jumps), None, tuple(cells),
                   detail or f"ЧП первых {n} кварталов г/г не выше {pct(yoy_max, 0)} при неизменной ключевой",
                   {"jumps": jumps, "quarters": n, "yoy": yoy_max, "key_tol": key_tol})


GUIDANCE_WORDS = {"roe": "ROE", "nim": "ЧПМ (упр.)", "cor_max": "стоимость риска (упр.)", "cir": "CIR (упр.)"}
BRIDGE_WORDS = {"cor": "стоимость риска", "nim": "ЧПМ"}


GUIDANCE_METRICS = {"nim": "nim", "cor_max": "cor", "cir": "cir"}     # ключ гайденса → метрика моста


def guidance_values(run: GridRun, cell: Any, year: int) -> dict[str, float | None]:
    """Путь года гайденса клетки: ROE — одного определения; ЧПМ, CoR и CIR — в упр. базисе по правилу «год с
    отчётными кварталами» (М§4.6). Одна функция у гейта и у выпуска (`guidance.items[].mass_outside`)."""
    i = cell.years.index(year)
    row = lambda name, q: cell.quarters[name][q]  # noqa: E731
    out: dict[str, float | None] = {"roe": cell.annual["roe"][i]}
    for key, metric in GUIDANCE_METRICS.items():
        out[key] = year_mgmt(run.ctx, metric, year, cell.annual[metric][i], row)
    return out


def _guidance_gate(run: GridRun) -> Finding:
    ctx = run.ctx
    facts = ctx.facts
    try:
        year = int(facts.plain("guidance", "year"))
        nodes = {k: facts.node("guidance", f"items.{k}") for k in ("roe", "nim", "cor_max", "cir")}
    except FactsError as exc:
        return Finding("guidance_gap", "gate", False, None, (), f"гайденс не прочитан: {exc}")
    # ЧПМ, CoR и CIR — в упр. базисе: отчётные кварталы — раскрытым упр. фактом, прогнозные — мостом
    # (М§4.6, §14.2); ROE — одного определения; узлы сектора (scope: sector) гейт не берёт
    years = run.cells[0].years
    if year not in years:
        return Finding("guidance_gap", "gate", False, 0.0, (), f"год гайденса {year} вне сетки")
    bands = {}
    for k, node in nodes.items():
        if node.get("v") is None or node.get("scope") == "sector":
            continue
        bands[k] = guidance_band(node)
    bad, why = [], {}
    for c in run.cells:
        vals = guidance_values(run, c, year)
        miss = [k for k, b in bands.items() if vals.get(k) is not None and not inside(b, vals[k])]
        if miss:
            bad.append(c.label)
            for k in miss:
                why.setdefault(k, []).append(vals[k])
    msg = "; ".join(f"{GUIDANCE_WORDS[k]}: {band_words((min(v), max(v)))} против гайденса {band_words(bands[k])}"
                    for k, v in why.items())
    return _gate("guidance_gap", run, bad, f"путь {year} года вне гайденса ({msg})" if bad
                 else f"путь {year} года внутри гайденса", bands={k: list(b) for k, b in bands.items()},
                 outside=sorted(why))


def _bridge_gate(run: GridRun) -> Finding:
    ctx, book = run.ctx, run.ctx.book
    facts = ctx.facts
    fired, parts = False, []
    for x, value in (("cor", ctx.bridge.cor_value), ("nim", ctx.bridge.nim_value)):
        hist = facts.file("bridge_mgmt_ifrs").get(x, {}).get("history") or []
        word = BRIDGE_WORDS[x]
        if not hist:
            parts.append(f"{word}: истории моста нет — не проверено")
            continue
        last = hist[-1]
        gap = last.get("gap")
        gap = gap.get("v") if isinstance(gap, Mapping) else gap
        when = period_words(last.get("period"))
        if gap is None:
            parts.append(f"{word}: разность за {when} не раскрыта")
            continue
        limit = float(book.get(f"checks.bridge_drift_pp.{x}"))
        drift = abs(float(gap) - value)
        parts.append(f"{word}, {when}: разность упр. ↔ МСФО {pp(float(gap))} при мосте {pp(value)} — дрейф "
                     f"{pp(drift)} {'больше' if drift > limit else 'не больше'} предела {pp(limit)}")
        fired |= drift > limit
    return Finding("bridge_drift", "gate", fired, 1.0 if fired else 0.0, (), "; ".join(parts))


def _crisis_gate(run: GridRun) -> Finding:
    ctx, book = run.ctx, run.ctx.book
    tl = ctx.timeline
    from model.worlds import MARKET_WORLD
    crisis = [r for r, rp in ctx.prep.regimes.items() if rp.shock_year is not None]
    if not crisis:
        return Finding("m_crisis_vs_cbr", "gate", False, 0.0, (), "кризисного режима нет")
    r = crisis[0]
    shock = int(ctx.prep.regimes[r].shock_year)
    cfg = book.get("checks.m_crisis_vs_cbr")
    bad, why = [], []
    for c in run.cells:
        if c.world != MARKET_WORLD or c.regime != r:
            continue
        loans = c.quarters["loans"]
        reasons = []
        for y, rng in cfg["loan_growth"].items():
            y = int(y)
            q_end, q_prev = tl.index(f"{y}Q4"), tl.index(f"{y - 1}Q4")
            if 0 <= q_prev and q_end <= tl.Q:
                g = loans[q_end] / loans[q_prev] - 1
                if _outside(g, rng):
                    reasons.append(f"рост кредита {y} года {pct(g, 1)} вне {band_words(rng, 1)}")
        if shock in c.years:
            cor = c.annual["cor"][c.years.index(shock)]
            if cor is not None and ctx.bridge.to_mgmt_cor(cor) < float(cfg["cor_shock_year"]):
                reasons.append(f"CoR {shock} года (упр.) {pct(ctx.bridge.to_mgmt_cor(cor))} ниже "
                               f"{pct(float(cfg['cor_shock_year']))}")
        if c.n20_min > float(cfg["n20_min"]):
            reasons.append(f"минимум Н20.0 {pct(c.n20_min)} выше {pct(float(cfg['n20_min']))}")
        if reasons:
            bad.append(c.label)
            why.append(f"{c.label}: " + ", ".join(reasons))
    return _gate("m_crisis_vs_cbr", run, bad, "; ".join(why) if why else "клетки M × кризис не мягче сценария ЦБ")


def _overdue_gate(run: GridRun, today: _dt.date) -> Finding:
    ctx, book = run.ctx, run.ctx.book
    try:
        events = ctx.facts.file("calendar").get("events") or []
    except FactsError:
        return Finding("manual_input_overdue", "gate", False, None, (), "календаря нет")
    days = book.get("checks.manual_overdue_days")
    anchor_q = ctx.timeline.index(ctx.timeline.anchor)
    curve = _dt.date.fromisoformat(str(book.get("worlds.source.curve_date")))
    declared = {r.year for r in ctx.live.register if r.status in ("declared", "paid")}
    late = []
    for ev in events:
        try:
            d = _dt.date.fromisoformat(str(ev.get("date")))
        except ValueError:
            continue
        kind, covers = ev.get("kind"), ev.get("covers")
        if kind == "ifrs" and covers and ctx.timeline.index(str(covers)) > anchor_q:
            if (today - d).days > int(days["ifrs_fact"]):
                late.append(f"МСФО за {period_words(covers)} ({d})")
        elif kind == "cbr_forecast" and curve < d and (today - d).days > int(days["worlds_after_cbr"]):
            late.append(f"прогноз ЦБ {d}")
        elif kind == "agm" and (today - d).days > int(days["dividend_register"]) and d.year - 1 not in declared:
            late.append(f"ГОСА {d}: дивиденд за {d.year - 1} не в реестре")
    fired = bool(late)
    return Finding("manual_input_overdue", "gate", fired, 1.0 if fired else 0.0, (),
                   "просрочен ручной вход: " + "; ".join(late) if late else "ручные входы не просрочены")


# ------------------------------------------------------------------ объяснения гейтов


def _parse_until(key: str, raw: Any) -> _dt.date | None:
    if raw is None:
        return None
    if isinstance(raw, _dt.datetime):
        return raw.date()
    if isinstance(raw, _dt.date):
        return raw
    try:
        return _dt.date.fromisoformat(str(raw))
    except ValueError:
        raise ValueError(f"gate_explanations.yaml, {key}: valid_until = {raw!r} — не дата ГГГГ-ММ-ДД") from None


def load_gate_explanations(path: Path | None = None, *, today: _dt.date | None = None
                           ) -> Mapping[str, Mapping[str, Any]]:
    """Объяснения гейтов; действуют включительно по `valid_until`."""
    path = Path(path) if path else EXPLANATIONS_PATH
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    today = today or _dt.date.today()
    out = {}
    for key, spec in raw.items():
        spec = spec or {}
        until = _parse_until(key, spec.get("valid_until"))
        rng = spec.get("expected_mass_range")
        if rng is not None:
            if not isinstance(rng, (list, tuple)) or len(rng) != 2 or not 0 <= float(rng[0]) <= float(rng[1]) <= 1:
                raise ValueError(f"gate_explanations.yaml, {key}: expected_mass_range = {rng!r}")
            rng = (float(rng[0]), float(rng[1]))
        stale = until is not None and until < today
        out[str(key)] = {"explanation": str(spec.get("explanation", "")).strip(),
                         "expected_mass": spec.get("expected_mass"), "expected_mass_range": rng,
                         "valid_until": until, "stale": stale,
                         "expiring": (not stale and until is not None and 0 <= (until - today).days <= WARN_DAYS)}
    return out


@dataclass(frozen=True)
class GateStatus:
    name: str
    fired: bool
    mass: float | None
    status: str                            # quiet | explained | unexplained | expired | mass_off
    explanation: str | None
    expected_mass: float | tuple[float, float] | None
    valid_until: _dt.date | None
    expiring: bool


def mass_corridor(expected: float | tuple[float, float] | None) -> tuple[float, float] | None:
    if expected is None:
        return None
    lo, hi = expected if isinstance(expected, tuple) else (float(expected), float(expected))
    return (lo * MASS_LOW[0] - MASS_LOW[1], hi * MASS_HIGH[0] + MASS_HIGH[1])


def gate_statuses(findings: Sequence[Finding], explanations: Mapping, *, today: _dt.date) -> list[GateStatus]:
    out = []
    for f in findings:
        if f.kind != "gate":
            continue
        spec = explanations.get(f.name)
        expected = None
        if spec:
            expected = spec.get("expected_mass_range") or spec.get("expected_mass")
            if isinstance(expected, list):
                expected = (float(expected[0]), float(expected[1]))
        until = spec.get("valid_until") if spec else None
        if isinstance(until, str):
            until = _parse_until(f.name, until)
        stale = until is not None and until < today
        expiring = bool(spec) and not stale and until is not None and 0 <= (until - today).days <= WARN_DAYS
        if not f.fired:
            status = "quiet"
        elif not spec:
            status = "unexplained"
        elif stale:
            status = "expired"
        else:
            corridor = mass_corridor(expected)
            if corridor is not None and f.mass is not None and not corridor[0] <= f.mass <= corridor[1]:
                status = "mass_off"
            else:
                status = "explained"
        out.append(GateStatus(name=f.name, fired=f.fired, mass=f.mass, status=status,
                              explanation=spec.get("explanation") if spec else None,
                              expected_mass=expected, valid_until=until, expiring=expiring))
    return out
