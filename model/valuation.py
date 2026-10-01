"""Оценка клетки: остаточный доход (печать) и дивидендная модель (инвариант), терминал (М§6–§7).

V_RI  = BV_v + Σ RI_q DF_v(τ_q) + DF_v(τ_Q) TV_RI,   RI_q = CI_q − k_q BV_(q−1) (q0: доля 1 − e);
V_DDM = Σ Div_q DF_v(τ_q) + DF_v(τ_Q) TV_DDM.
Равенство алгебраическое (телескопическая сумма) при совокупном ROE в RI,
k_q = DF_(q−1)/DF_q − 1, дивиденде в квартал ГОСА, делителе без собственных акций
и одном множителе времени терминала у обеих форм (М§6.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from model.worlds import Discount

# Защита роста терминала: g_T не выше k_T − 1 б.п. (гейт k_gt_g, М§7).
G_BELOW_K = 0.0001


@dataclass(frozen=True)
class Terminal:
    x_t: float                 # избыток капитала на T (млрд ₽; знак любой)
    bv_star: float             # BV* = BV_Q − X_T
    ni_t1: float               # ЧП акционерам терминального года
    ci_t1: float               # совокупный доход терминального года
    roe_raw: float             # ROE_T = CI_T+1 / BV*
    roe_t: float               # ROE'_T после одностороннего угасания (fade — только при ROE_T > k_T)
    k_t: float
    g_t: float                 # после защиты
    g_capped: bool             # сработала защита g_T = k_T − 0,0001
    tv_ri: float
    tv_ddm: float
    y_x: float = 0.0           # доход на избыток, вычтенный из терминального года (млрд ₽)


def excess_income(x_t: float, y_balancing: float, c_wholesale: float, headroom: float,
                  min_share: float = 0.0, wholesale_extra: float = 0.0) -> float:
    """Y_X — доход на избыток капитала по маржинальной ставке балансирующих статей (М§7).

    Избыток (X_T > 0): пока минимум ликвидности не связывает — смесь бумаг и ликвидности, дальше — ставка
    опта. Порог — H* = HLA / (1 − m), а не сам запас HLA: изъятый капитал уменьшает и LA, и активы, от которых
    считается минимум (`min_share` — m). Недостаток (X_T ≤ 0) — зеркально: добавленный капитал сначала гасит
    добор опта WT (`wholesale_extra`, если минимум связывает), остальное ложится в балансирующие активы по смеси."""
    if x_t > 0:
        limit = headroom / (1 - min_share)
        within = min(x_t, limit)
        return y_balancing * within + c_wholesale * (x_t - within)
    repaid = min(-x_t, wholesale_extra)
    return -(c_wholesale * repaid + y_balancing * (-x_t - repaid))


def terminal(*, n20: float, req20: float, n11_star: float, req11: float, rwa: float, bv_q: float,
             pbt_last_year: float, y_balancing: float, c_wholesale: float, headroom: float,
             tau_eff: float, nci_share: float,
             coupon_annual: float, tau_stat: float, lt_inflation: float, real_growth: float,
             fade: float, multiple: float, k_t: float, min_share: float = 0.0,
             wholesale_extra: float = 0.0) -> Terminal:
    """Терминал М§7: капиталонейтральная выплата, избыток капитала 1:1, одностороннее угасание через ROE_T.

    `y_balancing` — смесь доходностей бумаг и ликвидности на конце Q в их долях в LA, `c_wholesale` —
    ставка опта, `headroom` — запас HLA_Q балансирующих активов над минимумом ликвидности, `min_share` —
    доля минимума m (порог связывания — HLA / (1 − m)), `wholesale_extra` — добор опта WT_Q на конце Q.
    Угасание действует только на положительный избыток ROE_T − k_T: сходимость снизу не предполагается."""
    x_t = multiple * min((n20 - req20) * rwa, (n11_star - req11) * rwa)
    bv_star = bv_q - x_t
    g = (1 + lt_inflation) * (1 + real_growth) - 1
    capped = g > k_t - G_BELOW_K
    if capped:
        g = k_t - G_BELOW_K
    y_x = excess_income(x_t, y_balancing, c_wholesale, headroom, min_share, wholesale_extra)
    ni_t1 = (pbt_last_year * (1 + g) - y_x) * (1 - tau_eff) * (1 - nci_share)
    ci_t1 = ni_t1 - coupon_annual * (1 - tau_stat)
    roe_raw = ci_t1 / bv_star
    excess = roe_raw - k_t
    roe_t = k_t + fade * max(excess, 0.0) + min(excess, 0.0)
    tv_ri = (roe_t - k_t) * bv_star / (k_t - g)
    tv_ddm = x_t + (roe_t - g) * bv_star / (k_t - g)
    return Terminal(x_t=x_t, bv_star=bv_star, ni_t1=ni_t1, ci_t1=ci_t1, roe_raw=roe_raw, roe_t=roe_t,
                    k_t=k_t, g_t=g, g_capped=capped, tv_ri=tv_ri, tv_ddm=tv_ddm, y_x=y_x)


@dataclass(frozen=True)
class CellValue:
    bv_v: float
    v_ri: float
    v_ddm: float
    pv_ri_explicit: float      # Σ RI_q DF_v(τ_q)
    pv_terminal: float         # DF_v(τ_Q) TV_RI
    pv_div_explicit: float     # Σ Div_q DF_v(τ_q)
    terminal_share: float      # DF_v(τ_Q) TV_DDM / V_DDM
    ri: tuple[float, ...]      # RI_q, q = 0…Q (до q0 — 0)


def value_cell(*, q0: int, elapsed: float, disc: Discount, bv: Sequence[float], ci: Sequence[float],
               div: Sequence[float], term: Terminal) -> CellValue:
    """RI и DDM по рядам клетки (индекс q = 0…Q; bv — на конец квартала после дивиденда)."""
    Q = len(bv) - 1
    e = elapsed
    bv_v = bv[q0 - 1] + e * ci[q0]
    dfq = disc.dfq
    ri = [0.0] * (Q + 1)
    k0 = 1 / dfq[q0] - 1
    ri[q0] = (1 - e) * ci[q0] - k0 * bv_v
    for q in range(q0 + 1, Q + 1):
        ri[q] = ci[q] - disc.k[q] * bv[q - 1]
    pv_ri = sum(ri[q] * dfq[q] for q in range(q0, Q + 1))
    pv_div = sum(div[q] * dfq[q] for q in range(q0, Q + 1))
    pv_term = dfq[Q] * term.tv_ri
    v_ri = bv_v + pv_ri + pv_term
    v_ddm = pv_div + dfq[Q] * term.tv_ddm
    share = dfq[Q] * term.tv_ddm / v_ddm if v_ddm else float("nan")
    return CellValue(bv_v=bv_v, v_ri=v_ri, v_ddm=v_ddm, pv_ri_explicit=pv_ri, pv_terminal=pv_term,
                     pv_div_explicit=pv_div, terminal_share=share, ri=tuple(ri))
