"""Дивидендная политика данными книги (М§5): база, пул, DPS, решение года.

Пул года прибыли Y: p × max(0, ЧП акционерам − купон AT1 × (1 − τ_stat)); DPS =
пул / размещённые акции обеих категорий; BV уменьшается на DPS × акции в обращении
(часть на собственные акции остаётся в группе). Решение — в квартал ГОСА года
Y+1 по пробному проходу без дивиденда: запас капитала H в точках проверки,
ступени политики, остаток сверх требований, догоняющая выплата, доля ε избытка с
линейным вводом за `excess.ramp_years` лет (М§5.3 п. 4), кризисный перенос; объявленный
в реестре DPS — обязательство без проверок. Отмена выплаты в год шока кризиса — флаг
`crisis_skip`, а не `dividend_cut`: капитал выплату не урезал (М§5.3 п. 5).
Тест истории — формула на фактах в точной десятичной арифметике (ceil_kopeck).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from model.book_schema import BookError, FactsError
from model.paths import Trajectory

if TYPE_CHECKING:
    from model.book import Book, Facts

KOPECK = Decimal("0.01")
THOUSAND = 1000                              # млрд ₽ × 1000 / млн шт. = ₽ на акцию


def round_dps(value: Decimal, rule: str) -> Decimal:
    if rule == "ceil_kopeck":
        return value.quantize(KOPECK, rounding=ROUND_CEILING)
    if rule == "half_up_kopeck":
        return value.quantize(KOPECK, rounding=ROUND_HALF_UP)
    raise BookError(f"dividends.policy.dps_rounding = {rule!r}: известны ceil_kopeck, half_up_kopeck")


def _dec(x: Any) -> Decimal:
    return Decimal(str(x))


def dps_formula(*, ni_shareholders: Any, at1_coupon: Any, tax_statutory: Any, payout: Any,
                n_issued: Any, deduct_at1_after_tax: bool = True,
                rounding: str = "ceil_kopeck") -> dict[str, Decimal]:
    """Формула М§5.2 в точной десятичной арифметике (тест истории, прил. C)."""
    ni, cpn, tau = _dec(ni_shareholders), _dec(at1_coupon), _dec(tax_statutory)
    base = ni - (cpn * (1 - tau) if deduct_at1_after_tax else Decimal(0))
    pool = _dec(payout) * max(Decimal(0), base)
    exact = pool * THOUSAND / _dec(n_issued)
    return {"base": base, "pool": pool, "dps_exact": exact, "dps_rounded": round_dps(exact, rounding)}


def dps_history(book: "Book", facts: "Facts") -> list[dict[str, Any]]:
    """Строки теста `dps_history`: формула на `facts/dividends.json → history`."""
    rows = facts.file("dividends").get("history")
    if not isinstance(rows, list) or not rows:
        raise FactsError("dividends.history: нет истории выплат")
    n_iss = facts.need("shares", "issued_total")
    payout = Trajectory(book.get("dividends.policy.payout"))
    rule = str(book.get("dividends.policy.dps_rounding"))
    after_tax = bool(book.get("dividends.policy.deduct_at1_after_tax"))
    out = []
    for i, row in enumerate(rows):
        year = int(row["year"])
        get = lambda k: facts.need("dividends", f"history.{i}.{k}")
        r = dps_formula(ni_shareholders=get("ni_shareholders"), at1_coupon=get("at1_coupon"),
                        tax_statutory=get("tax_statutory"), payout=payout.year_value(year),
                        n_issued=n_iss, deduct_at1_after_tax=after_tax, rounding=rule)
        declared = _dec(get("dps_ordinary"))
        out.append({"year": year, "ni_shareholders": get("ni_shareholders"), "at1_coupon": get("at1_coupon"),
                    "tax_statutory": get("tax_statutory"), "base": float(r["base"]), "pool": float(r["pool"]),
                    "dps_exact": float(r["dps_exact"]), "dps_rounded": float(r["dps_rounded"]),
                    "dps_declared": float(declared), "rounding": rule,
                    "ok": r["dps_rounded"] == declared})
    return out


@dataclass(frozen=True)
class Step:
    payout: Trajectory
    threshold: float


@dataclass(frozen=True)
class Policy:
    """Политика книги, разобранная один раз на прогон."""
    steps: tuple[Step, ...]
    deviation: Trajectory
    deduct_at1_after_tax: bool
    epsilon: float
    excess_from: int
    ramp_years: int
    skip_in_shock: bool
    catch_up: bool
    agm_quarter: int
    reg_quarter: int
    pay_quarter: int
    checkpoints: tuple[int, ...]
    n_iss: float
    n_out: float

    def epsilon_at(self, year: int) -> float:
        """ε_Y = ε × min(1, (Y − from + 1) / ramp_years) при Y ≥ from, иначе 0 (М§5.3 п. 4)."""
        if year < self.excess_from:
            return 0.0
        return self.epsilon * min(1.0, (year - self.excess_from + 1) / self.ramp_years)

    def payouts(self, year: int) -> list[float]:
        dev = self.deviation.year_value(year)
        return [min(1.0, max(0.0, s.payout.year_value(year) + dev)) for s in self.steps]


def make_policy(book: "Book", n_iss: float, n_out: float) -> Policy:
    pol = book.get("dividends.policy")
    raw_steps = pol.get("steps") or []
    if raw_steps:
        steps = tuple(Step(Trajectory(s["payout"]), float(s["threshold"])) for s in raw_steps)
    else:
        steps = (Step(Trajectory(pol["payout"]), float(pol["threshold"])),)
    cal = book.get("dividends.calendar")
    return Policy(steps=steps, deviation=Trajectory(book.get("dividends.payout_deviation")),
                  deduct_at1_after_tax=bool(pol["deduct_at1_after_tax"]),
                  epsilon=float(book.get("dividends.excess.epsilon")),
                  excess_from=int(book.get("dividends.excess.from_profit_year")),
                  ramp_years=int(book.get("dividends.excess.ramp_years")),
                  skip_in_shock=bool(book.get("dividends.crisis.skip_in_shock_year")),
                  catch_up=bool(book.get("dividends.crisis.catch_up")),
                  agm_quarter=int(cal["agm_quarter"]), reg_quarter=int(cal["reg_deduction_quarter"]),
                  pay_quarter=int(cal["payment_quarter"]),
                  checkpoints=tuple(int(x) for x in cal["checkpoints"]),
                  n_iss=n_iss, n_out=n_out)


@dataclass(frozen=True)
class Checkpoint:
    """Норматив пробного прохода (без дивиденда года) в точке проверки."""
    n20: float
    n11: float
    rwa: float
    floor20: float
    req11: float


@dataclass(frozen=True)
class Decision:
    year: int                  # год прибыли
    base: float                # база выплаты (млрд ₽)
    want: tuple[float, ...]    # уменьшение BV по ступеням при полной выплате пула
    headroom: tuple[float, ...]  # H_j (млрд ₽; inf — точек проверки нет)
    step: int                  # индекс выбранной ступени
    base_div: float
    catch: float
    excess: float
    div: float                 # Div_Y: уменьшение BV
    dps: float                 # ₽ на акцию, не округлён
    deferred_before: float
    deferred_after: float
    source: str                # model | register | crisis_skip
    cut: bool                  # капитал урезал выплату: base_div < пула политики (М§5.3 п. 3); отмена кризисом — нет
    flags: tuple[str, ...] = field(default_factory=tuple)


def base_of_year(ni_sh_year: float, coupon_year: float, tau_stat: float, after_tax: bool) -> float:
    """База выплаты: ЧП акционерам − купоны AT1 за вычетом налогового эффекта."""
    return ni_sh_year - (coupon_year * (1 - tau_stat) if after_tax else 0.0)


def headroom(points: Sequence[Checkpoint], threshold: float, buffer20: float) -> float:
    """H_j = min_c min((N20 − req20_j) RWA, (N11 − req11) RWA) — дивиденд снижает K20 и K11 1:1."""
    if not points:
        return float("inf")
    return min(min((p.n20 - max(threshold, p.floor20 + buffer20)) * p.rwa,
                   (p.n11 - p.req11) * p.rwa) for p in points)


def decide(policy: Policy, *, year: int, base: float, points: Sequence[Checkpoint], buffer20: float,
           deferred: float, shock: bool, declared_dps: float | None) -> Decision:
    """Решение о дивиденде за год Y (М§5.3 пп. 2–6)."""
    pays = policy.payouts(year)
    ratio = policy.n_out / policy.n_iss
    want = tuple(p * max(0.0, base) * ratio for p in pays)
    hs = tuple(headroom(points, s.threshold, buffer20) for s in policy.steps)
    if declared_dps is not None:
        div = declared_dps * policy.n_out / THOUSAND
        return Decision(year=year, base=base, want=want, headroom=hs, step=0, base_div=div, catch=0.0,
                        excess=0.0, div=div, dps=declared_dps, deferred_before=deferred,
                        deferred_after=deferred, source="register", cut=False)
    if shock and policy.skip_in_shock:
        return Decision(year=year, base=base, want=want, headroom=hs, step=0, base_div=0.0, catch=0.0,
                        excess=0.0, div=0.0, dps=0.0, deferred_before=deferred,
                        deferred_after=deferred + want[0], source="crisis_skip", cut=False,
                        flags=("crisis_skip",))
    chosen = None
    for j, (w, h) in enumerate(zip(want, hs)):
        if h >= w:
            chosen = j
            break
    if chosen is not None:
        base_div, h_sel = want[chosen], hs[chosen]
    else:
        chosen = len(want) - 1
        h_sel = hs[chosen]
        base_div = max(0.0, min(want[chosen], h_sel))
    catch = min(deferred, max(0.0, h_sel - base_div)) if policy.catch_up else 0.0
    eps = policy.epsilon_at(year)
    exc = eps * max(0.0, h_sel - base_div - catch) if eps else 0.0
    if exc == float("inf") or catch == float("inf"):
        raise BookError("запас капитала бесконечен: нет точек проверки дивиденда в сетке")
    div = base_div + catch + exc
    cut = base_div < want[0] - 1e-9
    return Decision(year=year, base=base, want=want, headroom=hs, step=chosen, base_div=base_div,
                    catch=catch, excess=exc, div=div, dps=div * THOUSAND / policy.n_out,
                    deferred_before=deferred, deferred_after=deferred - catch, source="model", cut=cut,
                    flags=("dividend_cut",) if cut else ())


def pool_policy(policy: Policy, year: int, base: float) -> float:
    """Пул политики первой ступени без капитального ограничения (млрд ₽, на все размещённые)."""
    return policy.payouts(year)[0] * max(0.0, base)


def register_dps(register: Sequence[Any]) -> Mapping[int, float]:
    """Год прибыли → DPS объявленных записей реестра (declared | paid)."""
    out = {}
    for rec in register:
        status = getattr(rec, "status", None)
        if status in ("declared", "paid"):
            out[int(rec.year)] = float(rec.dps)
    return out
