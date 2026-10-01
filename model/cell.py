"""Квартальный проход клетки (мир × режим × регуляторный сценарий) в порядке М§4.2.

В квартале q: (1) в квартал ГОСА — решение о дивиденде (пробный проход с состояния
на конец q − 1); (2) ставки мира и пути режима и сценария; (3) объёмы небалансирующих
статей; (4) ставки книг; (5) предварительный баланс при BV_(q−1) − Div_q и DP_q;
(6) строки ОПУ; (7) капитал — OCI, купон AT1, прочие движения, BV_q; (8) окончательный
баланс при BV_q; (9) RWA и нормативы. Затем терминал (М§7) и оценка RI/DDM (М§6).

Сдвиг ЧПМ в ЧПД — ближний (`regimes.near_nim_shift`, общий для клеток) + сдвиг режима +
отклонение A-P2u (М§4.4). «Прочее» ОПУ — годовая сумма × индекс цен × доля квартала
(`other.misc_quarter_shares`, М§4.7) — и в году якоря: остатком год не закрывается, сумма
года — выход (М§0.3); остатком закрывается только непрофильный результат. Кредитная
переоценка кредитов по СС (FVC) — отклонение CoR клетки от пути режима-опоры
`credit.fv_loans_ref` на средних кредитах по СС, внутри шага; опора — путь книги до
розыгрыша осей (`Book.base`, М§4.6). Сжатие φ: доля `nii.phi_split` — в спредах кредитных
книг (φ_A), остаток — добавкой φ_L × X_W,q к стоимости пассивов с `phi` (М§4.4). Доход на
избыток капитала терминала — по маржинальной ставке балансирующих статей (М§7). Предварительный
проход (`preliminary_paths`) — клетка без отклонений A-P2u до заданного квартала: ожидания
режимов μ (М§12).

Книжные величины, общие для всех клеток прогона, готовит `prepare` (один раз на
прогон); клетка читает их из контекста `RunContext` (model/grid.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from operator import methodcaller
from typing import TYPE_CHECKING, Any, Mapping

from model.book import AnchorFacts, BookRoles, SECURITIES, LIQUIDITY, WHOLESALE, anchor_facts, book_roles
from model.book_schema import BookError, FactsError
from model.capital import (CapitalConst, RwaWeights, ScenarioPath, after_audit_start, capital_const,
                           density_index, fx_index, ratios, rwa_anchor, rwa_weights, scenario_path)
from model.credit import Bridge, fvc_amount, kappa_addon, regime_cor_engine
from model.dividends import (Checkpoint, Decision, Policy, base_of_year, decide, make_policy, pool_policy,
                             register_dps)
from model.nii import current_share_target, spread_paths
from model.paths import Trajectory
from model.timeline import DAYS_IN_YEAR, QUARTER_YEARS, Timeline, parse_period, period_str
from model.valuation import terminal, value_cell
from model.worlds import BASE_WORLD, Discount, WorldPath

if TYPE_CHECKING:
    from model.book import Book, Facts
    from model.grid import RunContext
    from model.nii import Transmission

# Строки кварталов CellResult.quarters (индекс q = 0…Q; q = 0 — якорь по фактам).
QUARTER_FIELDS = (
    "bv", "ni_sh", "ni", "pbt", "nii", "llp", "fvc", "fees", "opex", "ins", "misc", "noncore", "fvr",
    "one_off", "tax", "ot", "oci", "coupon", "om", "ci", "div", "dp", "dpreg", "pay", "reserve",
    "rwa", "k20", "k11", "n20", "n11", "ded20", "ded11", "e_unaudited", "req20", "req11", "floor20",
    "floor11", "loans", "loans_ac", "loans_fv", "funds", "funds_retail", "iea", "iea_avg",
    "loans_ac_avg", "loans_fv_avg", "assets", "liabilities_equity", "securities", "liquidity", "wholesale",
    "wholesale_extra", "other_assets", "other_liabilities", "allowance", "current_share",
    "nim", "cor", "cir", "roe_q", "k_q", "key", "tau_stat", "tau_eff", "int_income", "int_expense",
    "dia", "overlay",
)


# ------------------------------------------------------------------ подготовка прогона


@dataclass(frozen=True)
class RegimePath:
    regime: str
    cor_engine: tuple[float, ...]          # путь CoR режима в базисе движка (без κ и δ)
    nim_shift: tuple[float, ...]           # сдвиг ЧПМ режима (без ближнего сдвига)
    loan_adj: Mapping[int, float]          # год → поправка к росту кредита
    override: Mapping[int, float]          # год → абсолютный рост (кризис)
    one_off_q: int | None
    one_off_amount: float
    shock_year: int | None                 # год шока = год якоря + shock_year_offset (М§3.2); нет ключа — None


@dataclass(frozen=True)
class Prepared:
    """Книжные величины прогона, общие для 36 клеток."""
    timeline: Timeline
    roles: BookRoles
    books: Mapping[str, Mapping[str, Any]]
    af: AnchorFacts
    spreads: Mapping[str, tuple[float, ...]]
    phi: float                             # φ — сжатие целиком
    reference_world: str
    regimes: Mapping[str, RegimePath]
    scenarios: Mapping[str, ScenarioPath]
    dn: tuple[float, ...]
    rwa_w: RwaWeights
    cap: CapitalConst
    policy: Policy
    anchor_year: int
    guidance_year: int
    guidance_growth: Mapping[str, float]
    loan_share_drift: float
    funds_share_drift: float
    drift_until: int
    liquid_min: float
    sec_share: float
    wh_to_funds: float
    oa_ratio: float
    ol_ratio: float
    allowance_ratio: float
    fv_share: float
    fv_book: str | None
    c_ref: float
    psi: float
    key_ref: float
    c_bounds: tuple[float, float]
    c_rho: float
    dia: float
    fees_gvw: tuple[float, ...]
    fees_override: Mapping[int, float]     # год → рост (год якоря — рост остатка года gF_rest)
    opex_real: tuple[float, ...]
    ins_gvw: tuple[float, ...]
    noncore_q: Mapping[int, float]         # год → сумма в цены якоря на квартал (год якоря — остаток)
    misc_q: Mapping[tuple[int, int], float]   # (год, квартал) → «прочее» квартала в ценах якоря (М§4.7)
    tau_stat: Mapping[int, float]
    tax_gap: float
    ot_q: int | None
    ot_amount: float
    other_movements: tuple[float, ...]
    t2: tuple[float, ...]
    nci_share: float
    fv_factor: float
    kappa: float
    lag: int
    segment_rel: Mapping[str, float] | None
    coupon_annual: float
    coupon_quarter: int
    hist: Mapping[str, Mapping[int, float]]   # fees, opex (C > 0), ins, ni_sh по q ≤ 0
    hist_bal: Mapping[int, Mapping[str, float]]   # концы кварталов до якоря: bv, loans_ac, iea (balance.history)
    fvoci_duration: float
    fvoci_maturity: float
    fvoci_share: float
    fvoci_tenor: str
    y_tenor0: float
    fvr_on: bool
    fvtpl_share: float
    fvtpl_duration: float
    real_growth: float
    fade: float
    multiple: float
    register: Mapping[int, float]          # год прибыли → объявленный DPS
    reg_q_by_year: Mapping[int, int]       # год прибыли → квартал вычета из рег. капитала (реестр)
    pay_q_by_year: Mapping[int, int]
    rwa0: float
    anchor_state: Mapping[str, float]
    near: tuple[float, ...] = ()           # ближний сдвиг ЧПМ по q = 0…Q (М§3.2)
    misc_shares: Mapping[int, float] = field(default_factory=dict)   # доля квартала h в годовой сумме
    fv_ref: str = ""                       # режим-опора FVC (credit.fv_loans_ref, М§4.6)
    fv_ref_cor: tuple[float, ...] = ()     # путь опоры FVC в базисе движка — по книге до розыгрыша (Book.base)
    phi_assets: float = 0.0                # φ_A — сжатие спредов активов с phi
    phi_liab: float = 0.0                  # φ_L — добавка к стоимости пассивов с phi на единицу X_W,q
    phi_weights: Mapping[str, float] = field(default_factory=dict)   # ω_b книг Φ_A в X_W,q (М§4.4)


def _series(traj: Any, tl: Timeline) -> tuple[float, ...]:
    t = Trajectory(traj)
    return tuple(t.value(*parse_period(tl.period(q))) for q in range(tl.Q + 1))


def _year_sum_table(traj: Any, years: range) -> dict[int, float]:
    t = Trajectory(traj)
    return {y: sum(t.value(y, n) for n in (1, 2, 3, 4)) / 4 for y in years}


def prepare(book: "Book", facts: "Facts", timeline: Timeline, bridge: Bridge,
            transmission: "Transmission", register: tuple = ()) -> Prepared:
    books = book.get("nii.books")
    roles = book_roles(books)
    af = anchor_facts(facts, books)
    tl = timeline
    ay = tl.anchor_year
    years = range(ay - 1, tl.last_year + 1)
    if int(book.get("noncore.price_base_year")) != ay:
        raise BookError("noncore.price_base_year: цены книги — цены года якоря (М§4.7)")
    spreads = spread_paths(book, tl, transmission.sigma0, transmission.sigma0_liab)
    regimes = {}
    for r in book.get("regimes.ids"):
        spec = book.get(f"regimes.{r}")
        adj = Trajectory(spec["loan_growth_adj"])
        one = spec.get("one_off_loss")
        one_q = tl.index(one["period"]) if one else None
        offset = spec.get("shock_year_offset")
        regimes[r] = RegimePath(
            regime=r, cor_engine=regime_cor_engine(book, bridge, r, tl),
            nim_shift=_series(spec["nim_shift"], tl),
            loan_adj={y: adj.year_value(y) for y in years},
            override={int(k): float(v) for k, v in (spec.get("loan_growth_override") or {}).items()},
            one_off_q=one_q if one_q is not None and 1 <= one_q <= tl.Q else None,
            one_off_amount=float(one["amount"]) if one else 0.0,
            shock_year=None if offset is None else ay + int(offset))
    scenarios = {s: scenario_path(book, s, tl) for s in book.get("capital.reg_scenarios.ids")}
    rw = rwa_weights(book, roles)
    rwa0 = rwa_anchor(rw, af)
    cap = capital_const(book, af, rwa0)
    policy = make_policy(book, af.n_iss, af.n_out)
    # История по кварталам до якоря (q ≤ 0): база «к тому же кварталу» и прибыль.
    hist: dict[str, dict[int, float]] = {"fees": {}, "opex": {}, "ins": {}, "ni_sh": {}, "noncore": {},
                                         "misc": {}, "llp": {}, "nii": {}, "pbt": {}, "tax": {}, "ni": {}}
    for per, row in af.pnl.items():
        q = tl.index(per)
        if q > 0:
            continue
        for name, key, sign in (("fees", "fees_net", 1), ("opex", "opex", -1), ("ins", "insurance_net", 1),
                                ("ni_sh", "ni_shareholders", 1), ("noncore", "noncore_net", 1),
                                ("misc", "misc_net", 1), ("llp", "llp_debt_fa", -1), ("nii", "nii", 1),
                                ("pbt", "pbt", 1), ("tax", "tax", -1), ("ni", "ni", 1)):
            if row.get(key) is not None:
                hist[name][q] = sign * row[key]
    # Концы кварталов до якоря (годовые отношения года якоря — средние пяти концов, М§0.3).
    hist_bal: dict[int, dict[str, float]] = {}
    bal_hist = facts.file("balance").get("history")
    if isinstance(bal_hist, list):          # запись INTERFACES §3.2: [{period, …}]; словарь по периодам — тоже
        bal_hist = {str(r["period"]): r for r in bal_hist if isinstance(r, Mapping) and "period" in r}
    if isinstance(bal_hist, Mapping):
        for per, row in bal_hist.items():
            q = tl.index(str(per))
            if q >= 0 or not isinstance(row, Mapping):
                continue
            vals = {}
            for key, name in (("bv_common", "bv"), ("loans_ac_gross", "loans_ac"), ("iea", "iea")):
                node = row.get(key)
                if isinstance(node, Mapping) and node.get("v") is not None:
                    vals[name] = float(node["v"])
            hist_bal[q] = vals
    # Год якоря: факт отчётных кварталов и остаток года (М§0.3).
    fact_qs = [q for q in tl.quarters_of_year(ay) if q <= 0]
    rest_qs = [q for q in tl.quarters_of_year(ay) if q >= 1]

    def need_hist(name: str, qs: list[int]) -> float:
        missing = [tl.period(q) for q in qs if q not in hist[name]]
        if missing:
            raise FactsError(f"факты pnl_quarterly: нет {name} за {', '.join(missing)} (М§0.3)")
        return sum(hist[name][q] for q in qs)

    for q in range(1, 5):
        for name in ("fees", "opex", "ins"):
            if q - 4 not in hist[name]:
                raise FactsError(f"факты pnl_quarterly: нет {name} за {tl.period(q - 4)} "
                                "(база «к тому же кварталу», М§0.3)")
    fees_override = {int(k): float(v) for k, v in book.get("fees.growth_override").items()}
    if ay in fees_override and rest_qs:
        prev = need_hist("fees", tl.quarters_of_year(ay - 1))
        fact = need_hist("fees", fact_qs)
        base = sum(hist["fees"][q - 4] for q in rest_qs)
        fees_override[ay] = (prev * (1 + fees_override[ay]) - fact) / base - 1
    noncore_tab = _year_sum_table(book.get("noncore.result_real"), years)
    misc_tab = _year_sum_table(book.get("other.misc_net_real"), years)
    noncore_q = {y: v / 4 for y, v in noncore_tab.items()}
    shares = {h: float(book.get(f"other.misc_quarter_shares.{h}")) for h in (1, 2, 3, 4)}
    misc_q = {(y, h): v * shares[h] for y, v in misc_tab.items() for h in (1, 2, 3, 4)}
    if rest_qs:             # остатком года закрывается только непрофильный результат (М§0.3)
        noncore_q[ay] = (noncore_tab[ay] - need_hist("noncore", fact_qs)) / len(rest_qs)
    stat = Trajectory(book.get("tax.statutory"))
    tau_stat = {y: sum(stat.value(y, n) for n in (1, 2, 3, 4)) / 4 for y in years}
    ot = book.get("tax.one_off")
    ot_q = tl.index(period_str(int(ot["year"]), 1))
    seg = None
    if book.get("credit.segment_relative.enabled"):
        seg = {"corporate": float(book.get("credit.segment_relative.corporate")),
               "retail": float(book.get("credit.segment_relative.retail"))}
    tenor = str(book.get("oci.fvoci_tenor"))
    node = tenor.split("_")[1].rstrip("y")
    fv_book = af.fv_book
    fv_share = float(books[fv_book]["fv_share"]) if fv_book and "fv_share" in books[fv_book] else 0.0
    reg_q, pay_q = {}, {}
    for rec in register:
        if getattr(rec, "status", None) not in ("declared", "paid"):
            continue
        if rec.record_date is not None:
            reg_q[int(rec.year)] = tl.q_of_date(rec.record_date)
        if rec.pay_date is not None:
            pay_q[int(rec.year)] = tl.q_of_date(rec.pay_date)
    anchor_state = {"loans": sum(af.balances[b] for b in roles.loans), "rwa0": rwa0}
    fvr_on = bool(book.get("other.fvtpl_bond_reval"))
    fv_ref = str(book.get("credit.fv_loans_ref"))
    if fv_ref not in regimes:
        raise BookError(f"credit.fv_loans_ref = {fv_ref!r}: нет такого режима (М§4.6)")
    ref_book = book if book.base is None else book.base      # опора FVC — путь нормы книги до розыгрыша (М§4.6)
    fv_ref_cor = regime_cor_engine(ref_book, bridge, fv_ref, tl)
    return Prepared(
        timeline=tl, roles=roles, books=books, af=af, spreads=spreads, phi=transmission.phi,
        reference_world=transmission.reference_world or str(book.get("nii.transmission.reference_world")),
        regimes=regimes, scenarios=scenarios, dn=density_index(book, tl), rwa_w=rw, cap=cap,
        policy=policy, anchor_year=ay, guidance_year=int(book.get("volumes.guidance_year")),
        guidance_growth={k: float(v) for k, v in book.get("volumes.guidance_growth").items()},
        loan_share_drift=float(book.get("volumes.loan_share_drift")),
        funds_share_drift=float(book.get("volumes.funds_share_drift")),
        drift_until=int(book.get("volumes.share_drift_until")),
        liquid_min=float(book.get("volumes.liquid_min_share")),
        sec_share=float(book.get("volumes.securities_share_of_liquid")),
        wh_to_funds=float(book.get("volumes.wholesale_to_funds")),
        oa_ratio=float(book.get("volumes.other_assets_to_loans")),
        ol_ratio=float(book.get("volumes.other_liabilities_to_loans")),
        allowance_ratio=float(book.get("credit.allowance_ratio")),
        fv_share=fv_share, fv_book=fv_book,
        c_ref=float(book.get("nii.retail_current_share.c_ref")),
        psi=float(book.get("nii.retail_current_share.psi")),
        key_ref=float(book.get("nii.retail_current_share.key_ref")),
        c_bounds=tuple(float(x) for x in book.get("nii.retail_current_share.bounds")),
        c_rho=float(book.get("nii.retail_current_share.rho")),
        dia=float(book.get("nii.dia_rate")),
        fees_gvw=_series(book.get("fees.growth_vs_wages"), tl),
        fees_override=fees_override,
        opex_real=_series(book.get("opex.real_growth"), tl),
        ins_gvw=_series(book.get("other.insurance_growth_vs_wages"), tl),
        noncore_q=noncore_q, misc_q=misc_q, tau_stat=tau_stat,
        tax_gap=float(book.get("tax.effective_gap")),
        ot_q=ot_q if 1 <= ot_q <= tl.Q else None,
        ot_amount=float(ot["prob"]) * float(ot["amount"]),
        other_movements=_series(book.get("equity.other_movements"), tl),
        t2=_series(book.get("capital.n20.t2"), tl),
        nci_share=float(book.get("pnl.nci_share")),
        fv_factor=float(book.get("credit.fv_loans_factor")),
        kappa=float(book.get("credit.kappa")), lag=int(book.get("credit.real_rate_lag_q")),
        segment_rel=seg, coupon_annual=af.coupon_annual, coupon_quarter=af.coupon_quarter,
        hist={k: dict(v) for k, v in hist.items()}, hist_bal=hist_bal,
        fvoci_duration=float(book.get("oci.fvoci_duration")),
        fvoci_maturity=float(book.get("oci.fvoci_maturity")),
        fvoci_share=float(book.get("oci.fvoci_share")), fvoci_tenor=tenor,
        y_tenor0=af.ofz_anchor[node], fvr_on=fvr_on,
        fvtpl_share=float(book.get("oci.fvtpl_bond_share")),
        fvtpl_duration=float(book.get("oci.fvtpl_bond_duration")) if fvr_on else 0.0,
        real_growth=float(book.get("valuation.terminal.real_growth")),
        fade=float(book.get("valuation.terminal.fade")),
        multiple=float(book.get("valuation.terminal.excess_capital_multiple")),
        register=register_dps(register), reg_q_by_year=reg_q, pay_q_by_year=pay_q,
        rwa0=rwa0, anchor_state=anchor_state,
        near=_series(book.get("regimes.near_nim_shift"), tl), misc_shares=shares,
        fv_ref=fv_ref, fv_ref_cor=fv_ref_cor, phi_assets=transmission.phi_assets,
        phi_liab=transmission.phi_liab, phi_weights=dict(transmission.phi_weights),
    )


# ------------------------------------------------------------------ состояние


class State:
    """Состояние клетки на конец квартала (копируется для пробного прохода)."""

    __slots__ = ("E", "rate", "FR", "c", "AL", "OA", "OL", "DP", "BV", "R", "D", "pay", "reg",
                 "guid", "wh_extra", "q")

    def clone(self) -> "State":
        s = State.__new__(State)
        s.E, s.rate = dict(self.E), dict(self.rate)
        s.FR, s.c, s.AL, s.OA, s.OL = self.FR, self.c, self.AL, self.OA, self.OL
        s.DP, s.BV, s.R, s.D = self.DP, self.BV, self.R, self.D
        s.pay, s.reg = dict(self.pay), list(self.reg)
        s.guid, s.wh_extra, s.q = dict(self.guid), self.wh_extra, self.q
        return s


def initial_state(p: Prepared) -> State:
    af, roles = p.af, p.roles
    s = State.__new__(State)
    s.E = {b: af.balances[b] for b in roles.names}
    s.rate = {b: af.rates[b] for b in roles.names}
    s.FR = sum(af.balances[b] for b in roles.retail)
    s.c = af.balances[roles.retail[0]] / s.FR
    s.AL, s.OA, s.OL = af.allowance, af.other_assets, af.other_liabilities
    s.DP, s.BV, s.R, s.D = af.dividends_payable, af.bv, af.fvoci_reserve, 0.0
    s.pay, s.reg, s.guid, s.wh_extra, s.q = {}, [], {}, 0.0, 0
    tl, pol = p.timeline, p.policy
    if af.dividends_payable and af.dividends_payable_year is not None:
        y = af.dividends_payable_year
        pq = p.pay_q_by_year.get(y, tl.index(period_str(y + 1, pol.pay_quarter)))
        rq = p.reg_q_by_year.get(y, tl.index(period_str(y + 1, pol.reg_quarter)))
        s.pay[max(1, pq)] = s.pay.get(max(1, pq), 0.0) + af.dividends_payable
        if rq >= 1:
            s.reg.append((0, rq, af.dividends_payable))
    return s


def dpreg_at(reg: list, q: int) -> float:
    """DPreg на конец q: объявлено к кварталу ГОСА включительно, не вычтено до квартала отсечки."""
    return sum(a for (dq, rq, a) in reg if dq <= q < rq)


# ------------------------------------------------------------------ результат клетки


@dataclass(frozen=True)
class CellResult:
    world: str
    regime: str
    scenario: str
    quarters: Mapping[str, tuple[float | None, ...]]   # ряды по кварталам 0..Q
    annual: Mapping[str, tuple[float | None, ...]]     # по годам якоря … last_period
    dps: Mapping[int, float]               # год прибыли → DPS (₽, не округлён)
    v_ri: float
    v_ddm: float
    bv_v: float
    x_t: float
    roe_t: float
    k_t: float
    g_t: float
    tv_ri: float
    terminal_share: float
    n20_min: float
    n11_min: float
    gap_period: str | None
    flags: frozenset[str]
    # сверх договора INTERFACES §4.3 (только добавление)
    years: tuple[int, ...] = ()
    tv_ddm: float = 0.0
    pv_ri_explicit: float = 0.0
    pv_terminal: float = 0.0
    roe_t_raw: float = 0.0
    g_capped: bool = False
    bv_star: float = 0.0
    decisions: tuple[Decision, ...] = ()
    dps_policy: Mapping[int, float] = field(default_factory=dict)
    ci_q0: float = 0.0
    n11_star: float = 0.0
    real_rate: float = 0.0
    y_x: float = 0.0                       # доход на избыток, вычтенный из терминального года (М§7)
    la_headroom: float = 0.0               # HLA_Q — запас балансирующих активов над минимумом на конце Q
    books: Mapping[str, Mapping[str, tuple[float | None, ...]]] = field(default_factory=dict)
    # книга → {"rate": ставка квартала, "avg": средний остаток, "balance": остаток на конец} по q = 0…Q

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.world, self.regime, self.scenario)

    @property
    def label(self) -> str:
        return f"{self.world}/{self.regime}/{self.scenario}"


# ------------------------------------------------------------------ квартальный проход


class _Cell:
    """Одна клетка: ряды мира, режима и сценария + шаг квартала."""

    def __init__(self, ctx: "RunContext", world: str, regime: str, scenario: str, *,
                 preliminary: bool = False):
        p: Prepared = ctx.prep
        self.p, self.tl = p, p.timeline
        self.w: WorldPath = ctx.worlds[world]
        self.wh: WorldPath = ctx.worlds[p.reference_world]
        self.rg: RegimePath = p.regimes[regime]
        self.sc: ScenarioPath = p.scenarios[scenario]
        self.world, self.regime, self.scenario = world, regime, scenario
        Q = self.tl.Q
        dev = ctx.deviations.get(regime, {}) if not preliminary else {}
        zero = tuple([0.0] * (Q + 1))
        self.d_cor = dev.get("cor", zero)
        self.d_nim = dev.get("nim", zero)
        near = p.near or zero
        self.nim_shift = tuple(near[q] + self.rg.nim_shift[q] for q in range(Q + 1))
        kap = kappa_addon(p.kappa, p.lag, self.w.real_key, ctx.worlds[BASE_WORLD].real_key, Q)
        self.cor = tuple(self.rg.cor_engine[q] + kap[q] + self.d_cor[q] for q in range(Q + 1))
        self.kappa = kap
        self.cor_ref = p.fv_ref_cor                         # путь режима-опоры книги до розыгрыша, без κ и δ
        # X_W,q — разность опорных ставок кредитных книг Φ_A мира и мира H, взвешенная долями якоря (М§4.4)
        refs = [(wt, self.w.ref(p.books[b]["ref"]), self.wh.ref(p.books[b]["ref"]))
                for b, wt in p.phi_weights.items()]
        self.x_w = tuple(sum(wt * (rw[q] - rh[q]) for wt, rw, rh in refs) for q in range(Q + 1))
        self.x = fx_index(ctx.book, self.w.cpi, Q)
        # CoR сегментов: при выключенной сегментной схеме — общая CoR (М§4.6).
        if p.segment_rel is not None:
            af = p.af
            w_ac = {"corporate": 0.0, "retail": 0.0}
            for b in p.roles.loans:
                seg = p.books[b]["cor_segment"]
                w_ac[seg] += af.balances[b] - (af.fv_loans if b == p.fv_book else 0.0)
            tot = sum(w_ac.values())
            norm = sum(w_ac[s] / tot * p.segment_rel[s] for s in w_ac)
            self.seg_mult = {s: p.segment_rel[s] / norm for s in w_ac}
        else:
            self.seg_mult = None
        self.years = {q: self.tl.year(q) for q in range(-8, Q + 1)}
        self.hs = {q: self.tl.h(q) for q in range(-8, Q + 1)}
        self.flows: dict[str, dict[int, float]] = {k: dict(v) for k, v in p.hist.items()}

    # ------------------------------------------------------------ рост объёмов
    def loan_growth(self, b: str, year: int) -> float:
        p, rg = self.p, self.rg
        if year in rg.override:
            return rg.override[year]
        sector = p.books[b]["sector"]
        g = self.w.credit_growth[sector][year]
        d = p.loan_share_drift if year <= p.drift_until else 0.0
        return (1 + g) * (1 + d) - 1 + rg.loan_adj.get(year, 0.0)

    def funds_growth(self, sector: str, year: int) -> float:
        p = self.p
        g = self.w.funds_growth[sector][year]
        d = p.funds_share_drift if year <= p.drift_until else 0.0
        return (1 + g) * (1 + d) - 1

    def guidance_multipliers(self, st: State, q: int) -> dict[str, float]:
        """Год гайденса: сегменты растут одним квартальным множителем к цели года (М§4.3)."""
        p, tl = self.p, self.tl
        year = self.years[q]
        qs = [x for x in tl.quarters_of_year(year) if x >= 1]
        n = len(qs)
        out = {}
        for seg in ("corporate", "retail"):
            books = [b for b in p.roles.loans if p.books[b]["cor_segment"] == seg]
            start = sum(st.E[b] for b in books)
            base_end = p.af.prev_year_end[seg] if year == p.anchor_year else start
            target = base_end * (1 + p.guidance_growth[seg])
            out[seg] = (target / start) ** (1 / n)
        return out

    # ------------------------------------------------------------ шаг квартала
    def step(self, st: State, q: int, div_decl: float, pay_q: int | None = None,
             reg_q: int | None = None) -> dict[str, float]:
        p, tl, w = self.p, self.tl, self.w
        books, roles = p.books, p.roles
        year, h = self.years[q], self.hs[q]
        d = tl.days[q - 1]
        dt = d / DAYS_IN_YEAR
        key = w.key[q]
        wage = w.wage[q]
        # 2. объёмы небалансирующих статей
        E0 = st.E
        E = dict(E0)
        if year == p.guidance_year:
            if not st.guid or st.guid.get("_year") != year:
                st.guid = dict(self.guidance_multipliers(st, q))
                st.guid["_year"] = year
            for b in roles.loans:
                E[b] = E0[b] * st.guid[books[b]["cor_segment"]]
        else:
            for b in roles.loans:
                E[b] = E0[b] * (1 + self.loan_growth(b, year)) ** QUARTER_YEARS
        FR = st.FR * (1 + self.funds_growth("retail", year)) ** QUARTER_YEARS
        gc = (1 + self.funds_growth("corporate", year)) ** QUARTER_YEARS
        for b in roles.corp_funds:
            E[b] = E0[b] * gc
        c_star = current_share_target(key, p.c_ref, p.psi, p.key_ref, p.c_bounds)
        c = st.c + p.c_rho * (c_star - st.c)
        rc, rt = roles.retail
        E[rc], E[rt] = c * FR, (1 - c) * FR
        loans = sum(E[b] for b in roles.loans)
        fv = E[p.fv_book] * p.fv_share if p.fv_book else 0.0
        loans_ac = loans - fv
        funds = FR + sum(E[b] for b in roles.corp_funds)
        wh_base = p.wh_to_funds * funds
        OA, OL, AL = p.oa_ratio * loans, p.ol_ratio * loans, p.allowance_ratio * loans_ac
        # 3. ставки книг
        rate = dict(st.rate)
        phi, phi_l = p.phi_assets, p.phi_liab
        for b in roles.names:
            spec = books[b]
            ref = w.ref(spec["ref"])[q]
            s = p.spreads[b][q]
            rho = float(spec["rho"])
            if spec["side"] == "asset":
                if spec.get("phi"):
                    s -= phi * (ref - self.wh.ref(spec["ref"])[q])
                rate[b] = st.rate[b] + rho * (ref + s - st.rate[b])
            else:
                if phi_l and spec.get("phi"):
                    s += phi_l * self.x_w[q]
                rate[b] = st.rate[b] + rho * (float(spec["beta"]) * ref + s - st.rate[b])
        # 4. предварительный баланс при BV_(q−1); выплата объявленного в том же квартале — сразу
        pay = st.pay.get(q, 0.0)
        pq = (pay_q if pay_q is not None else q) if div_decl else None
        if div_decl and pq == q:
            pay += div_decl
        DP = st.DP + div_decl - pay
        nonbal_assets = loans - AL + OA
        fixed_liab = funds + OL + DP + p.af.at1 + p.af.nci
        la_min = p.liquid_min * nonbal_assets / (1 - p.liquid_min)
        la_pre = fixed_liab + wh_base + (st.BV - div_decl) - nonbal_assets
        extra_pre = max(0.0, la_min - la_pre)
        la_pre += extra_pre
        sec_pre = p.sec_share * la_pre
        liq_pre = la_pre - sec_pre
        wh_pre = wh_base + extra_pre
        # 5. ОПУ
        avg = {}
        for b in roles.names:
            avg[b] = (E0[b] + E[b]) / 2
        avg[SECURITIES] = (E0[SECURITIES] + sec_pre) / 2
        avg[LIQUIDITY] = (E0[LIQUIDITY] + liq_pre) / 2
        avg[WHOLESALE] = (E0[WHOLESALE] + wh_pre) / 2
        income = sum(rate[b] * avg[b] for b in roles.assets) * dt
        expense = sum(rate[b] * avg[b] for b in roles.liabilities) * dt
        fr_avg = (st.FR + FR) / 2
        dia = p.dia * fr_avg * dt
        iea_avg = sum(avg[b] for b in roles.assets)
        ov = (self.nim_shift[q] + self.d_nim[q]) * iea_avg * dt
        nii = income - expense - dia + ov
        fv0 = E0[p.fv_book] * p.fv_share if p.fv_book else 0.0
        fv_avg = (fv0 + fv) / 2
        ac_avg = sum(avg[b] for b in roles.loans) - fv_avg
        cor = self.cor[q]
        if self.seg_mult is None:
            llp = cor * ac_avg * dt
            cor_corp, ref_corp = cor, self.cor_ref[q]
        else:
            llp = 0.0
            for b in roles.loans:
                seg = books[b]["cor_segment"]
                part = avg[b] - (fv_avg if b == p.fv_book else 0.0)
                llp += cor * self.seg_mult[seg] * part * dt
            cor_corp = cor * self.seg_mult["corporate"]
            ref_corp = self.cor_ref[q] * self.seg_mult["corporate"]
        fvc = fvc_amount(p.fv_factor, cor_corp, ref_corp, fv_avg, dt)
        flows = self.flows
        gvw = p.fees_override.get(year)
        g_fees = gvw if gvw is not None else wage + p.fees_gvw[q]
        fees = flows["fees"][q - 4] * (1 + g_fees)
        opex = flows["opex"][q - 4] * (1 + wage) * (1 + p.opex_real[q])
        ins = flows["ins"][q - 4] * (1 + wage + p.ins_gvw[q])
        idx = w.price_index[q]
        noncore = p.noncore_q[year] * idx
        misc = p.misc_q[(year, h)] * idx
        tenor = w.ofz[p.fvoci_tenor]
        dy = tenor[q] - (tenor[q - 1] if q > 1 else p.y_tenor0)
        fvr = -p.fvtpl_duration * dy * p.fvtpl_share * avg[SECURITIES] if p.fvr_on else 0.0
        one_off = self.rg.one_off_amount if q == self.rg.one_off_q else 0.0
        pbt = nii - llp - fvc + fees + ins + misc + fvr + noncore - opex + one_off
        tau_s = p.tau_stat[year]
        tau_e = tau_s + p.tax_gap
        tax = tau_e * pbt
        ot = p.ot_amount if q == p.ot_q else 0.0
        ni = pbt - tax - ot
        ni_sh = ni * (1 - p.nci_share)
        # 6. капитал
        fvoci_avg = p.fvoci_share * avg[SECURITIES]
        R = st.R * (1 - QUARTER_YEARS / p.fvoci_maturity) - p.fvoci_duration * dy * fvoci_avg * (1 - tau_s)
        oci = R - st.R
        cpn = p.coupon_annual if h == p.coupon_quarter else 0.0
        om = p.other_movements[q]
        ci = ni_sh - cpn * (1 - tau_s) + oci + om
        BV = st.BV + ci - div_decl
        # 7. окончательный баланс при BV_q
        la = fixed_liab + wh_base + BV - nonbal_assets
        extra = max(0.0, la_min - la)
        la += extra
        E[SECURITIES] = p.sec_share * la
        E[LIQUIDITY] = la - E[SECURITIES]
        E[WHOLESALE] = wh_base + extra
        assets = loans - AL + E[SECURITIES] + E[LIQUIDITY] + OA
        liab_eq = funds + E[WHOLESALE] + OL + DP + BV + p.af.at1 + p.af.nci
        # 8. RWA и нормативы
        reg = st.reg
        if div_decl:
            reg = reg + [(q, reg_q if reg_q is not None else q, div_decl)]
        dpreg = dpreg_at(reg, q)
        back = after_audit_start(h, p.cap.audit_cutoffs)
        flows["ni_sh"][q] = ni_sh
        e_unaud = sum(flows["ni_sh"][q - j] for j in range(back + 1))
        rwa = p.rwa_w.rwa({b: E[b] for b in roles.loans}, E[SECURITIES], E[LIQUIDITY], OA,
                          p.dn[q], self.x[q])
        rt_ = ratios(p.cap, bv=BV, dpreg=dpreg, reserve=R, unaudited=e_unaud, t2=p.t2[q], rwa=rwa,
                     ded_pp20=self.sc.ded_pp20[q], ded_pp11=self.sc.ded_pp11[q])
        # запись и переход
        flows["fees"][q], flows["opex"][q], flows["ins"][q] = fees, opex, ins
        st.E, st.rate, st.FR, st.c = E, rate, FR, c
        st.AL, st.OA, st.OL, st.DP, st.BV, st.R = AL, OA, OL, DP, BV, R
        st.reg, st.wh_extra, st.q = reg, extra, q
        if div_decl and pq != q:
            st.pay[pq] = st.pay.get(pq, 0.0) + div_decl
        income_base = nii + fees + ins + misc + fvr
        return {
            "bv": BV, "ni_sh": ni_sh, "ni": ni, "pbt": pbt, "nii": nii, "llp": llp, "fvc": fvc,
            "fees": fees, "opex": opex, "ins": ins, "misc": misc, "noncore": noncore, "fvr": fvr,
            "one_off": one_off, "tax": tax, "ot": ot, "oci": oci, "coupon": cpn, "om": om, "ci": ci,
            "div": div_decl, "dp": DP, "dpreg": dpreg, "pay": pay, "reserve": R,
            "rwa": rwa, "k20": rt_.k20, "k11": rt_.k11, "n20": rt_.n20, "n11": rt_.n11,
            "ded20": rt_.ded20, "ded11": rt_.ded11, "e_unaudited": e_unaud,
            "req20": self.sc.req20[q], "req11": self.sc.req11[q], "floor20": self.sc.floor20[q],
            "floor11": self.sc.floor11[q], "loans": loans, "loans_ac": loans_ac, "loans_fv": fv,
            "funds": funds, "funds_retail": FR, "iea": sum(E[b] for b in roles.assets),
            "iea_avg": iea_avg, "loans_ac_avg": ac_avg, "loans_fv_avg": fv_avg, "assets": assets,
            "liabilities_equity": liab_eq, "securities": E[SECURITIES], "liquidity": E[LIQUIDITY],
            "wholesale": E[WHOLESALE], "wholesale_extra": extra, "other_assets": OA,
            "other_liabilities": OL, "allowance": AL, "current_share": c,
            "nim": nii / iea_avg / dt, "cor": llp / ac_avg / dt,
            "cir": opex / income_base if income_base else float("nan"),
            "roe_q": ci / st_bv_prev if (st_bv_prev := BV - ci + div_decl) else float("nan"),
            "k_q": 0.0, "key": key, "tau_stat": tau_s, "tau_eff": tau_e,
            "int_income": income, "int_expense": expense, "dia": dia, "overlay": ov,
            "la_headroom": max(0.0, la - p.liquid_min * assets),
            "_rate": rate, "_avg": avg, "_bal": E,
        }

    # ------------------------------------------------------------ год Y: решение о дивиденде
    def year_base(self, year: int) -> float:
        p, tl = self.p, self.tl
        qs = tl.quarters_of_year(year)
        ni = 0.0
        for q in qs:
            if q not in self.flows["ni_sh"]:
                raise FactsError(f"нет ЧП акционерам за {tl.period(q)} для базы выплаты {year}")
            ni += self.flows["ni_sh"][q]
        cpn = sum(p.coupon_annual for q in qs if self.hs.get(q, tl.h(q)) == p.coupon_quarter)
        return base_of_year(ni, cpn, p.tau_stat[year], p.policy.deduct_at1_after_tax)

    def trial(self, st: State, q_a: int, year: int) -> list[Checkpoint]:
        """Пробный проход q_A … последняя точка проверки с Div_Y = 0 (М§5.3 п. 1)."""
        p, tl = self.p, self.tl
        pay_year = year + 1
        cps = [tl.index(period_str(pay_year, h)) for h in p.policy.checkpoints]
        cps = sorted(c for c in cps if c >= q_a)
        last = min(max(cps) if cps else tl.Q, tl.Q)
        cps = [c for c in cps if c <= tl.Q] or [last]
        trial_state = st.clone()
        saved = {k: dict(v) for k, v in self.flows.items() if k in ("fees", "opex", "ins", "ni_sh")}
        out = []
        for qq in range(q_a, last + 1):
            row = self.step(trial_state, qq, 0.0)
            if qq in cps:
                out.append(Checkpoint(n20=row["n20"], n11=row["n11"], rwa=row["rwa"],
                                      floor20=row["floor20"], req11=row["req11"]))
        for k, v in saved.items():
            self.flows[k] = v
        return out

    def run(self, until: int | None = None) -> tuple[list[dict], list[Decision], State]:
        p, tl = self.p, self.tl
        st = initial_state(p)
        rows: list[dict] = []
        decisions: list[Decision] = []
        pol = p.policy
        paid_year = p.af.dividends_payable_year
        last = tl.Q if until is None else min(tl.Q, until)
        for q in range(1, last + 1):
            div_decl, pay_q, reg_q = 0.0, None, None
            if self.hs[q] == pol.agm_quarter:
                year = self.years[q] - 1
                if year >= p.anchor_year - 1 and year != paid_year:
                    base = self.year_base(year)
                    declared = p.register.get(year)
                    shock = self.rg.shock_year is not None and year + 1 == self.rg.shock_year
                    points: list[Checkpoint] = []
                    if declared is None and not (shock and pol.skip_in_shock):
                        points = self.trial(st, q, year)
                    dec = decide(pol, year=year, base=base, points=points, buffer20=p.cap.buffer20,
                                 deferred=st.D, shock=shock, declared_dps=declared)
                    st.D = dec.deferred_after
                    decisions.append(dec)
                    div_decl = dec.div
                    pay_q = max(q, p.pay_q_by_year.get(year, tl.index(period_str(year + 1, pol.pay_quarter))))
                    reg_q = max(q, p.reg_q_by_year.get(year, tl.index(period_str(year + 1, pol.reg_quarter))))
            rows.append(self.step(st, q, div_decl, pay_q, reg_q))
        return rows, decisions, st


def anchor_row(p: Prepared, ctx: "RunContext", scenario: str) -> dict[str, float | None]:
    """Строка q = 0 (якорь): балансы и нормативы из фактов, потоки квартала якоря — факт ОПУ."""
    af, roles, tl = p.af, p.roles, p.timeline
    sc = p.scenarios[scenario]
    st = initial_state(p)
    loans = sum(af.balances[b] for b in roles.loans)
    loans_ac = loans - af.fv_loans
    dpreg = dpreg_at(st.reg, 0) if st.reg else 0.0
    ay, aq = parse_period(tl.anchor)
    back = after_audit_start(aq, p.cap.audit_cutoffs)
    try:
        e0 = sum(p.hist["ni_sh"][-j] for j in range(back + 1))
    except KeyError:
        e0 = None
    x0 = 1.0
    rwa0 = p.rwa_w.rwa({b: af.balances[b] for b in roles.loans}, af.balances[SECURITIES],
                       af.balances[LIQUIDITY], af.other_assets, p.dn[0], x0)
    rt_ = ratios(p.cap, bv=af.bv, dpreg=dpreg, reserve=af.fvoci_reserve, unaudited=e0 or 0.0,
                 t2=p.t2[0], rwa=rwa0, ded_pp20=sc.ded_pp20[0], ded_pp11=sc.ded_pp11[0])
    h = p.hist
    row: dict[str, float | None] = {k: None for k in QUARTER_FIELDS}
    row.update({
        "bv": af.bv, "reserve": af.fvoci_reserve, "dp": af.dividends_payable, "dpreg": dpreg,
        "rwa": rwa0, "k20": rt_.k20, "n20": rt_.n20, "ded20": rt_.ded20, "ded11": rt_.ded11,
        "k11": rt_.k11 if e0 is not None else None, "n11": rt_.n11 if e0 is not None else None,
        "e_unaudited": e0, "req20": sc.req20[0], "req11": sc.req11[0], "floor20": sc.floor20[0],
        "floor11": sc.floor11[0], "loans": loans, "loans_ac": loans_ac, "loans_fv": af.fv_loans,
        "funds": sum(af.balances[b] for b in roles.funds),
        "funds_retail": sum(af.balances[b] for b in roles.retail),
        "iea": sum(af.balances[b] for b in roles.assets), "securities": af.balances[SECURITIES],
        "liquidity": af.balances[LIQUIDITY], "wholesale": af.balances[WHOLESALE],
        "other_assets": af.other_assets, "other_liabilities": af.other_liabilities,
        "allowance": af.allowance, "current_share": st.c,
        "nii": h["nii"].get(0), "llp": h["llp"].get(0), "fees": h["fees"].get(0),
        "opex": h["opex"].get(0), "ins": h["ins"].get(0), "misc": h["misc"].get(0),
        "noncore": h["noncore"].get(0), "pbt": h["pbt"].get(0), "tax": h["tax"].get(0),
        "ni": h["ni"].get(0), "ni_sh": h["ni_sh"].get(0),
    })
    return row


def preliminary_paths(ctx: "RunContext", world: str, regime: str, scenario: str, until: int
                      ) -> dict[str, tuple[float, ...]]:
    """CoR и ЧПМ движка клетки без отклонений A-P2u (δ ≡ 0) по q = 1…until (М§12, x⁰).

    Кварталы до `until` считаются так же, как в полном проходе (проход последовательный,
    пробный проход дивиденда смотрит вперёд сам), поэтому x⁰ не зависит от `until`."""
    cell = _Cell(ctx, world, regime, scenario, preliminary=True)
    rows, _, _ = cell.run(until=until)
    return {"cor": (0.0,) + tuple(r["cor"] for r in rows), "nim": (0.0,) + tuple(r["nim"] for r in rows)}


def run_cell(ctx: "RunContext", world: str, regime: str, scenario: str) -> CellResult:
    """Клетка (W, r, s): квартальный проход, терминал, RI и DDM (М§4–§7)."""
    p = ctx.prep
    tl = p.timeline
    cell = _Cell(ctx, world, regime, scenario)
    rows, decisions, st = cell.run()
    Q = tl.Q
    disc: Discount = ctx.discounts[world]
    row0 = anchor_row(p, ctx, scenario)
    full = [row0] + rows
    quarters = {k: tuple(map(methodcaller("get", k), full)) for k in QUARTER_FIELDS}
    quarters["k_q"] = tuple(disc.k)
    books = {b: {"rate": (p.af.rates[b],) + tuple(r["_rate"][b] for r in rows),
                 "avg": (None,) + tuple(r["_avg"][b] for r in rows),
                 "balance": (p.af.balances[b],) + tuple(r["_bal"][b] for r in rows)}
             for b in p.roles.names}
    # терминал (М§7)
    last = rows[-1]
    L = tl.year(Q)
    pbt_l = sum(r["pbt"] for q, r in zip(range(1, Q + 1), rows) if tl.year(q) == L)
    n11_star = (last["k11"] + last["e_unaudited"]) / last["rwa"] + p.cap.gap11 - cell.sc.ded_pp11[Q]
    y_bal = p.sec_share * last["_rate"][SECURITIES] + (1 - p.sec_share) * last["_rate"][LIQUIDITY]
    term = terminal(n20=last["n20"], req20=last["req20"], n11_star=n11_star, req11=last["req11"],
                    rwa=last["rwa"], bv_q=last["bv"], pbt_last_year=pbt_l, y_balancing=y_bal,
                    c_wholesale=last["_rate"][WHOLESALE], headroom=last["la_headroom"], tau_eff=last["tau_eff"],
                    nci_share=p.nci_share, coupon_annual=p.coupon_annual, tau_stat=last["tau_stat"],
                    lt_inflation=cell.w.lt_inflation, real_growth=p.real_growth, fade=p.fade,
                    multiple=p.multiple, k_t=disc.k_t, min_share=p.liquid_min,
                    wholesale_extra=last["wholesale_extra"])
    bv = quarters["bv"]
    ci = (0.0,) + tuple(r["ci"] for r in rows)
    div = (0.0,) + tuple(r["div"] for r in rows)
    val = value_cell(q0=ctx.clock.q0, elapsed=ctx.clock.elapsed, disc=disc, bv=bv, ci=ci, div=div,
                     term=term)
    # нормативы после дивидендов, разрыв к полу
    n20s = [r["n20"] for r in rows]
    n11s = [r["n11"] for r in rows]
    gap = None
    for q, r in zip(range(1, Q + 1), rows):
        if r["n20"] < r["floor20"] or r["n11"] < r["floor11"]:
            gap = tl.period(q)
            break
    flags = set()
    if gap is not None:
        flags.add("capital_gap")
    if term.roe_t < term.k_t:
        flags.add("roe_below_k")
    for dec in decisions:
        flags.update(dec.flags)
    dps = {dec.year: dec.dps for dec in decisions}
    dps_pol = {dec.year: pool_policy(p.policy, dec.year, dec.base) * 1000 / p.af.n_iss for dec in decisions}
    annual, years = annual_rows(p, quarters, dps)
    return CellResult(
        world=world, regime=regime, scenario=scenario, quarters=quarters, annual=annual, dps=dps,
        v_ri=val.v_ri, v_ddm=val.v_ddm, bv_v=val.bv_v, x_t=term.x_t, roe_t=term.roe_t, k_t=term.k_t,
        g_t=term.g_t, tv_ri=term.tv_ri, terminal_share=val.terminal_share,
        n20_min=min(n20s), n11_min=min(n11s), gap_period=gap, flags=frozenset(flags),
        years=years, tv_ddm=term.tv_ddm, pv_ri_explicit=val.pv_ri_explicit, pv_terminal=val.pv_terminal,
        roe_t_raw=term.roe_raw, g_capped=term.g_capped, bv_star=term.bv_star,
        decisions=tuple(decisions), dps_policy=dps_pol, ci_q0=ci[ctx.clock.q0], n11_star=n11_star,
        y_x=term.y_x, la_headroom=last["la_headroom"],
        real_rate=float(ctx.book.get(f"worlds.{world}.zero_curve.LT")) - cell.w.lt_inflation,
        books=books,
    )


def annual_rows(p: Prepared, quarters: Mapping[str, tuple], dps: Mapping[int, float]
                ) -> tuple[dict[str, tuple[float | None, ...]], tuple[int, ...]]:
    """Годовые ряды клетки (П§2 grid.cells[].annual): год якоря — факт отчётных кварталов + клетка.

    Годовые отношения — сумма кварталов к среднему пяти концов кварталов года (М§0.3);
    концы до якоря — `balance.history` фактов (если конца нет — среднее доступных).
    """
    tl = p.timeline
    years = tuple(range(p.anchor_year, tl.last_year + 1))
    out: dict[str, list[float | None]] = {k: [] for k in ("ni_sh", "roe", "cor", "nim", "cir", "n20",
                                                          "n11", "dps", "payout")}
    hist = p.hist
    Q = tl.Q

    def flow(name: str, q: int) -> float | None:
        if q >= 1:
            return quarters[name][q]
        return hist.get(name, {}).get(q)

    for y in years:
        qs = tl.quarters_of_year(y)
        ends = [q for q in [qs[0] - 1] + qs if q <= Q]

        def total(name: str) -> float | None:
            vals = [flow(name, q) for q in qs]
            return None if any(v is None for v in vals) else sum(vals)

        def mean_end(name: str) -> float | None:
            vals = []
            for q in ends:
                v = quarters[name][q] if q >= 0 else p.hist_bal.get(q, {}).get(name)
                if v is not None:
                    vals.append(v)
            return sum(vals) / len(vals) if vals else None

        ni = total("ni_sh")
        bv_avg = mean_end("bv")
        out["ni_sh"].append(ni)
        out["roe"].append(None if ni is None or not bv_avg else ni / bv_avg)
        llp, ac = total("llp"), mean_end("loans_ac")
        out["cor"].append(None if llp is None or not ac else llp / ac)
        nii, iea = total("nii"), mean_end("iea")
        out["nim"].append(None if nii is None or not iea else nii / iea)
        parts = [total(k) for k in ("nii", "fees", "ins", "misc")]
        opex = total("opex")
        if opex is None or any(v is None for v in parts):
            out["cir"].append(None)
        else:
            fvr = sum(quarters["fvr"][q] or 0.0 for q in qs if q >= 1)
            out["cir"].append(opex / (sum(parts) + fvr))
        last_q = qs[-1]
        out["n20"].append(quarters["n20"][last_q] if 0 <= last_q <= Q else None)
        out["n11"].append(quarters["n11"][last_q] if 0 <= last_q <= Q else None)
        d = dps.get(y)
        out["dps"].append(d)
        out["payout"].append(None if d is None or not ni else d * p.af.n_iss / 1000 / ni)
    return {k: tuple(v) for k, v in out.items()}, years
