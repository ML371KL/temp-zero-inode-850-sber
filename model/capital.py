"""RWA, нормативы из BV с аддитивными вычетами, требования и полы (М§3.3, §4.11–§4.12).

K20 = BV + DPreg − (1 − f)·R − Ded20 + AT1 + T2;  K11 = BV + DPreg − (1 − f)·R − E − Ded11;
Ded_q = Ded_0 × RWA_q / RWA_0 (вычеты растут с бизнесом; дивиденд и прибыль входят
один к одному). Н20.1 группы — через прокси Н1.1 банка. Пол = минимум + надбавки
сценария года; требование Н20.0 = max(порог политики; пол + буфер).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

from model.book import SECURITIES, LIQUIDITY, AnchorFacts, BookRoles
from model.paths import Trajectory
from model.timeline import QUARTER_YEARS, Timeline, parse_period

if TYPE_CHECKING:
    from model.book import Book

OTHER_ASSETS = "other_assets"


@dataclass(frozen=True)
class ScenarioPath:
    """Полы, требования и вычеты сценария s по кварталам q = 0…Q."""
    scenario: str
    floor20: tuple[float, ...]
    floor11: tuple[float, ...]
    req20: tuple[float, ...]
    req11: tuple[float, ...]
    ded_pp20: tuple[float, ...]
    ded_pp11: tuple[float, ...]
    conservation: tuple[float, ...]
    sifi: tuple[float, ...]
    ccyb: tuple[float, ...]

    def req20_at(self, q: int, threshold: float, buffer20: float) -> float:
        """Требование Н20.0 при пороге ступени политики (М§5.3 п. 2)."""
        return max(threshold, self.floor20[q] + buffer20)


def _series(traj, timeline: Timeline) -> tuple[float, ...]:
    t = Trajectory(traj)
    return tuple(t.value(*parse_period(timeline.period(q))) for q in range(timeline.Q + 1))


def scenario_path(book: "Book", scenario: str, timeline: Timeline) -> ScenarioPath:
    base = f"capital.reg_scenarios.{scenario}"
    cons = _series(book.get(f"{base}.conservation"), timeline)
    sifi = _series(book.get(f"{base}.sifi"), timeline)
    ccyb = _series(book.get(f"{base}.ccyb"), timeline)
    d20 = _series(book.get(f"{base}.deduction_pp.n20_0"), timeline)
    d11 = _series(book.get(f"{base}.deduction_pp.n1_1"), timeline)
    m20 = float(book.get("capital.minimum.n20_0"))
    m11 = float(book.get("capital.minimum.n1_1"))
    b20 = float(book.get("capital.mgmt_buffer.n20_0"))
    b11 = float(book.get("capital.mgmt_buffer.n1_1"))
    thr = float(book.get("dividends.policy.threshold"))
    add = [cons[q] + sifi[q] + ccyb[q] for q in range(timeline.Q + 1)]
    floor20 = tuple(m20 + a for a in add)
    floor11 = tuple(m11 + a for a in add)
    return ScenarioPath(scenario=scenario, floor20=floor20, floor11=floor11,
                        req20=tuple(max(thr, f + b20) for f in floor20),
                        req11=tuple(f + b11 for f in floor11),
                        ded_pp20=d20, ded_pp11=d11, conservation=cons, sifi=sifi, ccyb=ccyb)


def density_index(book: "Book", timeline: Timeline) -> tuple[float, ...]:
    """Dn_q: рост плотности RWA до `density_drift_until` включительно (М§4.11)."""
    rate = float(book.get("capital.rwa.density_drift_rate"))
    until = int(book.get("capital.rwa.density_drift_until"))
    out = [1.0]
    for q in range(1, timeline.Q + 1):
        out.append(out[-1] * ((1 + rate) ** QUARTER_YEARS if timeline.year(q) <= until else 1.0))
    return tuple(out)


def fx_index(book: "Book", cpi: Sequence[float], Q: int) -> tuple[float, ...]:
    """X_q валютной части RWA по относительному ППС; выключено — X ≡ 1."""
    if not book.get("capital.rwa.fx.enabled"):
        return tuple([1.0] * (Q + 1))
    share = float(book.get("capital.rwa.fx.share"))
    pi = float(book.get("capital.rwa.fx.foreign_inflation"))
    out, cum = [1.0], 1.0
    for q in range(1, Q + 1):
        cum *= ((1 + cpi[q]) / (1 + pi)) ** QUARTER_YEARS
        out.append(1 - share + share * cum)
    return tuple(out)


@dataclass(frozen=True)
class RwaWeights:
    loans: Mapping[str, float]             # кредитная книга → плотность
    securities: float
    liquidity: float
    other_assets: float

    def rwa(self, loans: Mapping[str, float], securities: float, liquidity: float,
            other_assets: float, dn: float, x: float) -> float:
        base = sum(self.loans[b] * loans[b] for b in self.loans)
        base += self.securities * securities + self.liquidity * liquidity
        base += self.other_assets * other_assets
        return base * dn * x


def rwa_weights(book: "Book", roles: BookRoles) -> RwaWeights:
    dens = book.get("capital.rwa.density")
    return RwaWeights(loans={b: float(dens[b]) for b in roles.loans},
                      securities=float(dens[SECURITIES]), liquidity=float(dens[LIQUIDITY]),
                      other_assets=float(dens[OTHER_ASSETS]))


def rwa_anchor(weights: RwaWeights, af: AnchorFacts) -> float:
    """RWA_0 модели: плотности × книги якоря (калиброваны к RWA по Базелю)."""
    return weights.rwa({b: af.balances[b] for b in weights.loans}, af.balances[SECURITIES],
                       af.balances[LIQUIDITY], af.other_assets, 1.0, 1.0)


@dataclass(frozen=True)
class CapitalConst:
    """Постоянные книги для нормативов (М§4.11)."""
    ded20_0: float
    ded11_0: float
    rwa0: float
    fvoci_recognition: float
    gap20: float
    gap11: float
    at1: float
    audit_cutoffs: tuple[int, ...]
    buffer20: float
    buffer11: float


def capital_const(book: "Book", af: AnchorFacts, rwa0: float) -> CapitalConst:
    return CapitalConst(
        ded20_0=float(book.get("capital.n20.deductions_anchor")),
        ded11_0=float(book.get("capital.n11.deductions_anchor")),
        rwa0=rwa0,
        fvoci_recognition=float(book.get("capital.n20.fvoci_recognition")),
        gap20=float(book.get("capital.n20.gap_pp")),
        gap11=float(book.get("capital.n11.gap_pp")),
        at1=af.at1,
        audit_cutoffs=tuple(int(x) for x in book.get("capital.n11.audit_cutoffs")),
        buffer20=float(book.get("capital.mgmt_buffer.n20_0")),
        buffer11=float(book.get("capital.mgmt_buffer.n1_1")),
    )


@dataclass(frozen=True)
class Ratios:
    rwa: float
    k20: float
    k11: float
    n20: float
    n11: float
    ded20: float
    ded11: float


def ratios(c: CapitalConst, *, bv: float, dpreg: float, reserve: float, unaudited: float,
           t2: float, rwa: float, ded_pp20: float, ded_pp11: float) -> Ratios:
    """K20, K11, Н20.0 и Н20.1 (прокси Н1.1) на конец квартала (М§4.11)."""
    ded20 = c.ded20_0 * rwa / c.rwa0
    ded11 = c.ded11_0 * rwa / c.rwa0
    bvreg = bv + dpreg - (1 - c.fvoci_recognition) * reserve
    k20 = bvreg - ded20 + c.at1 + t2
    k11 = bvreg - unaudited - ded11
    return Ratios(rwa=rwa, k20=k20, k11=k11, n20=k20 / rwa + c.gap20 - ded_pp20,
                  n11=k11 / rwa + c.gap11 - ded_pp11, ded20=ded20, ded11=ded11)


def after_audit_start(quarter: int, cutoffs: Sequence[int]) -> int:
    """Номер первого квартала года, прибыль с которого ещё не аудирована на конец `quarter`.

    E_q = Σ NI по кварталам после последнего квартала-отсечки, строго
    предшествующего q, по q включительно; возвращается сдвиг назад (0 — только q).
    """
    back = 0
    h = quarter
    while True:
        h = 4 if h == 1 else h - 1
        if h in cutoffs:
            return back
        back += 1
        if back > 3:
            return back
