"""Сетка 36 клеток, вероятности, A-P2u, слои и λ, мост и цена, язык банка (М§3, §8–§9, §12).

Вероятность клетки P_L(W, r, s) = P_L(W) × P(r | наблюдения) × P(s | r): веса миров —
свои у каждого слоя, вероятности режимов после A-P2u и сценариев капитала —
одни во всех мирах и слоях (одна конвенция). Цена слоя
[(V0 − D_pend)(1 − g_gov) + D_pend + B(v)] × 1000 / N_out: объявленный дивиденд —
обязательство и дисконта за управление не несёт нигде — ни в мосте B(v) (после вычета из BV
модели), ни до вычета (D_pend: от решения ГОСА до конца квартала ГОСА он ещё внутри V0,
М§8.2). Низ — слой «рыночные ставки как есть», верх — «свой взгляд», точка —
низ + λ (верх − низ).

A-P2u (перенос 850oa grid.py, квартальный): по каждому наблюдаемому (CoR, ЧПМ) и
режиму — инновация AR(1) к ожиданию режима в базисе движка, правдоподобие,
предел сдвига вероятностей после КАЖДОГО наблюдения и вслед за ним пол вероятности
режима (`joint.regime_update.floor_share` × базовая: режим остаётся возможным всегда);
затухающие отклонения δ = ρ^k m несут клетки режима (М§4.4, §4.6). Априорные стационарны,
память — окно: фильтр стартует с базовых вероятностей книги и пустого состояния и читает
только последние `joint.regime_update.window_obs` наблюдений (М§12). Ожидание режима μ_x,r(q)
прогнозного квартала — среднее слоя «свой взгляд» по клеткам режима без отклонений
(предварительный проход); у наблюдения квартала не позже якоря — замороженные
`mu_cor`, `mu_nim` записи (их пишет перезаякоривание, М§15.2; ядро их не пересчитывает — и при
смене года якоря). Ожидание квартала —
прогноз того же фильтра Σ_r P(r) (μ_r + ρ^k m_r) (`regime_forecast`, М§13): наблюдение,
равное ему, точно нейтрально только при одном режиме; при смеси режимов оно их
перевзвешивает — это обучение (A5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from model.book import Book, Facts
from model.book_schema import BookError, FactsError
from model.cell import CellResult, Prepared, preliminary_paths, prepare, run_cell
from model.credit import Bridge, bridge_from_facts, year_in_mgmt
from model.nii import REFERENCE_REGIME, Transmission, solve_transmission
from model.timeline import DAYS_IN_YEAR, Clock, Timeline, make_clock, make_timeline, period_str, to_date
from model.worlds import BASE_WORLD, MARKET_WORLD, Discount, WorldPath, check_family_worlds, discount, world_path

__all__ = ["DividendRecord", "LiveInputs", "live_from_book", "RunContext", "make_context",
           "CellResult", "run_cell", "Layer", "GridRun", "run_grid", "price_from_v0", "evaluate",
           "LAYER_TITLES", "bank_language", "sensitivity_overrides", "world_layer", "market_cap",
           "realized_cells", "derived_values", "bridge_rows", "cap_shift", "floor_shift", "regime_update",
           "regime_expectations", "regime_forecast", "share_class_counts", "window_observations",
           "unregistered_dividend", "volume_sign_test", "modal_cell", "year_mgmt", "REFERENCE_REGIME",
           "REFERENCE_SCENARIO"]

# Режим и сценарий «передачи по клеткам» (М§4.5) и флага ni_jump (М§14.3): (W, norm, schedule);
# режим — `model.nii.REFERENCE_REGIME` (по нему же считается кредитная маржа миров).
REFERENCE_SCENARIO = "schedule"
SIGN_TOL = 1e-9                             # ₽: «не снижает точку» — с точностью счёта
LAYER_TITLES = {
    "analytical": "Свой макро-взгляд",
    "market_implied": "Вменённые рынком",
    "macro_neutral": "Рыночные ставки как есть",
}
THOUSAND = 1000


# ------------------------------------------------------------------ живые входы


@dataclass(frozen=True)
class DividendRecord:                      # запись реестра (живой вход, М§15)
    year: int
    dps: float
    status: str                            # "declared" | "recommended" | "paid"
    record_date: date | None
    last_buy_date: date | None
    ex_date: date | None
    pay_date: date | None
    sources: tuple[str, ...]

    @property
    def ex(self) -> date | None:
        """Экс-дата = отсечка (T+1), если отдельно не задана."""
        return self.ex_date or self.record_date


@dataclass(frozen=True)
class LiveInputs:                          # что живого идёт в прогон (строит model/live.py)
    valuation_date: date
    prices: Mapping[str, float]            # тикер → ₽ (главный обязателен)
    price_dates: Mapping[str, date]
    register: tuple[DividendRecord, ...]


def _opt_date(x: Any) -> date | None:
    if x is None or x == "":
        return None
    if isinstance(x, Mapping):
        x = x.get("v")
        if x is None:
            return None
    return to_date(x)


def _plain(x: Any) -> Any:
    return x.get("v") if isinstance(x, Mapping) and "v" in x else x


def live_from_book(book: Book, facts: Facts) -> LiveInputs:
    """Цена и дата книги, реестр — `dividends.json → register_seed`."""
    v = to_date(book.get("meta.valuation_date"))
    prices = {str(t): float(p) for t, p in book.get("meta.market_price").items()}
    register = []
    for rec in facts.file("dividends").get("register_seed") or []:
        register.append(DividendRecord(
            year=int(_plain(rec["year"])), dps=float(_plain(rec["dps"])), status=str(_plain(rec["status"])),
            record_date=_opt_date(rec.get("record_date")), last_buy_date=_opt_date(rec.get("last_buy_date")),
            ex_date=_opt_date(rec.get("ex_date")), pay_date=_opt_date(rec.get("pay_date")),
            sources=tuple(str(s) for s in rec.get("sources") or ())))
    return LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices},
                      register=tuple(register))


# ------------------------------------------------------------------ контекст прогона


@dataclass(frozen=True)
class RunContext:                          # общее для 36 клеток одного прогона
    book: Book
    facts: Facts
    live: LiveInputs
    timeline: Timeline
    clock: Clock
    bridge: Bridge
    transmission: Transmission
    posterior: Mapping[str, float]         # P(r | наблюдения) после A-P2u
    deviations: Mapping[str, Any]          # режим → {"cor": ряд δ_cor,q, "nim": ряд δ_nim,q} (q = 0…Q)
    expectations: Mapping[str, Any] = field(default_factory=dict)
    # μ_x,r(q) без отклонений (М§12): {"until": q, "cor": {r: ряд q = 0…until}, "nim": {…}, "frozen":
    # {период q ≤ 0: {"cor": {r: μ}, "nim": {r: μ}}} — замороженные ожидания записи, базис движка}; пусто — наблюдений нет
    # сверх договора (только добавление)
    prep: Prepared | None = None
    worlds: Mapping[str, WorldPath] = field(default_factory=dict)
    discounts: Mapping[str, Discount] = field(default_factory=dict)
    nim_ref: tuple[float, ...] | None = None     # снято в W1 (A5): ожидание режима — μ слоя; всегда None
    updates: tuple[Mapping[str, Any], ...] = ()   # наблюдения окна A-P2u с апостериорными после каждого
    #                                               (после предела сдвига и пола вероятности)


def _check_ids(book: Book) -> None:
    check_family_worlds(book)
    if REFERENCE_REGIME not in book.get("regimes.ids"):
        raise BookError(f"regimes.ids: нет режима {REFERENCE_REGIME} (передача по клеткам, М§4.5)")
    if REFERENCE_SCENARIO not in book.get("capital.reg_scenarios.ids"):
        raise BookError(f"capital.reg_scenarios.ids: нет сценария {REFERENCE_SCENARIO} (М§4.5)")


def make_context(book: Book, facts: Facts, live: LiveInputs | None = None) -> RunContext:
    _check_ids(book)
    live = live if live is not None else live_from_book(book, facts)
    timeline = make_timeline(book)
    clock = make_clock(book, timeline, live.valuation_date)
    bridge = bridge_from_facts(facts)
    transmission = solve_transmission(book, facts, bridge)
    prep = prepare(book, facts, timeline, bridge, transmission, live.register)
    ids = tuple(book.get("worlds.ids"))
    worlds = {w: world_path(book, w, timeline) for w in ids}
    discounts = {w: discount(book, w, clock) for w in ids}
    prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
    base = RunContext(book=book, facts=facts, live=live, timeline=timeline, clock=clock, bridge=bridge,
                      transmission=transmission, posterior=prior, deviations={}, prep=prep,
                      worlds=worlds, discounts=discounts)
    observations = window_observations(book, bridge, timeline)
    if not observations:
        return base
    until = max(o["q"] for o in observations)
    mu: dict[str, Any] = regime_expectations(base, until) if until >= 1 else {"until": 0, "cor": {}, "nim": {}}
    mu["frozen"] = {o["period"]: o["mu"] for o in observations if o["q"] <= 0}
    posterior, deviations, updates = regime_update(book, prep, observations, mu, prior, timeline.Q)
    return RunContext(book=book, facts=facts, live=live, timeline=timeline, clock=clock, bridge=bridge,
                      transmission=transmission, posterior=posterior, deviations=deviations, expectations=mu,
                      prep=prep, worlds=worlds, discounts=discounts, updates=tuple(updates))


# ------------------------------------------------------------------ A-P2u


def _observations(book: Book, bridge: Bridge, timeline: Timeline) -> list[dict[str, Any]]:
    """Наблюдения по порядку периодов; mgmt → движок одним мостом (М§4.6).

    Квартал не позже якоря (q ≤ 0) клетки не считают: его ожидания режимов — замороженные
    `mu_cor`, `mu_nim` записи, в базисе записи (тот же мост, М§12); без них — BookError."""
    to_engine = {"cor": bridge.to_engine_cor, "nim": bridge.to_engine_nim}
    out = []
    for obs in book.get("joint.regime_update.observations") or []:
        q = timeline.index(str(obs["period"]))
        if q > timeline.Q:
            raise BookError(f"наблюдение A-P2u {obs['period']}: позже last_period")
        mgmt = obs["basis"] == "mgmt"
        row: dict[str, Any] = {"period": str(obs["period"]), "q": q, "basis": obs["basis"],
                               "cor_raw": obs.get("cor"), "nim_raw": obs.get("nim"), "mu": {}}
        for x in ("cor", "nim"):
            value = obs.get(x)
            conv = to_engine[x] if mgmt else float
            row[x] = None if value is None else conv(float(value))
            row[f"se_{x}"] = float(obs.get(f"se_{x}") or 0.0)
            if q <= 0 and value is not None:
                frozen = obs.get(f"mu_{x}")
                if not isinstance(frozen, Mapping):
                    raise BookError(f"наблюдение A-P2u {obs['period']} (квартал не позже якоря): нет "
                                    f"замороженных ожиданий режимов mu_{x} (М§12)")
                row["mu"][x] = {str(r): conv(float(v)) for r, v in frozen.items()}
        out.append(row)
    return sorted(out, key=lambda o: o["q"])


def window_observations(book: Book, bridge: Bridge, timeline: Timeline) -> list[dict[str, Any]]:
    """Окно фильтра: последние `joint.regime_update.window_obs` наблюдений по порядку периодов (М§12)."""
    n = int(book.get("joint.regime_update.window_obs"))
    if n < 1:
        raise BookError(f"joint.regime_update.window_obs = {n}: нужно целое ≥ 1")
    return _observations(book, bridge, timeline)[-n:]


def cap_shift(priors: Mapping[str, float], posterior: Mapping[str, float], limit: float) -> dict[str, float]:
    """Предел сдвига вероятностей за ОДНО наблюдение (перенос 850oa grid.cap_shift).

    Сдвиг каждого режима обрезается по пределу, распределение перенормируется,
    и если перенормировка вывела сдвиг за предел — весь вектор сдвигов сжимается
    пропорционально.
    """
    if not limit:
        return dict(posterior)
    out = {r: priors[r] + max(-limit, min(limit, posterior[r] - priors[r])) for r in priors}
    total = sum(out.values()) or 1.0
    out = {r: v / total for r, v in out.items()}
    worst = max(abs(out[r] - priors[r]) for r in priors)
    if worst > limit + 1e-12:
        out = {r: priors[r] + limit / worst * (out[r] - priors[r]) for r in priors}
    return out


def floor_shift(prior: Mapping[str, float], current: Mapping[str, float], share: float) -> dict[str, float]:
    """Пол вероятности режима (М§12): P(r) не ниже `share` × базовой `prior[r]`.

    Режимы ниже пола ставятся на пол, остальные умножаются на общий множитель так, чтобы сумма была 1;
    если после умножения ещё какой-то режим ушёл ниже своего пола — он тоже ставится на пол, шаг повторяется.
    `share` = 0 — пола нет; 1 — вероятности стоят на базовых."""
    if not share:
        return dict(current)
    floors = {r: share * prior[r] for r in prior}
    fixed: set[str] = set()
    out = dict(current)
    while True:
        low = [r for r in out if r not in fixed and out[r] < floors[r]]
        if not low:
            return out
        fixed.update(low)
        rest = [r for r in out if r not in fixed]
        total = sum(current[r] for r in rest)
        scale = (1.0 - sum(floors[r] for r in fixed)) / total if total > 0 else 0.0
        out = {r: (floors[r] if r in fixed else current[r] * scale) for r in out}


def regime_expectations(ctx: RunContext, until: int) -> dict[str, Any]:
    """μ_x,r(q) = Σ_W P_an(W) Σ_s P(s | r) x⁰_(W,r,s),q, q = 0…until (М§12): CoR и ЧПМ движка
    клеток без отклонений A-P2u, веса миров слоя «свой взгляд» (в полосе — разыгранные)."""
    book = ctx.book
    until = max(1, min(int(until), ctx.timeline.Q))
    world_w = {w: float(book.get(f"joint.world_prob.{w}")) for w in book.get("worlds.ids")}
    table = book.get("joint.reg_prob_given_regime")
    out: dict[str, Any] = {"until": until, "cor": {}, "nim": {}}
    for r in book.get("regimes.ids"):
        acc = {"cor": [0.0] * (until + 1), "nim": [0.0] * (until + 1)}
        for w, pw in world_w.items():
            for sc, ps in table[r].items():
                wt = pw * float(ps)
                if not wt:
                    continue
                x0 = preliminary_paths(ctx, w, r, sc, until)
                for x in ("cor", "nim"):
                    for q in range(1, until + 1):
                        acc[x][q] += wt * x0[x][q]
        out["cor"][r], out["nim"][r] = tuple(acc["cor"]), tuple(acc["nim"])
    return out


def regime_forecast(ctx: RunContext, q: int) -> dict[str, dict[str, float]]:
    """Прогноз фильтра A-P2u на квартал q по режимам: μ_x,r(q) + ρ_x^k m_x,r (базис движка, М§13).

    Ожидание квартала — Σ_r P(r | наблюдения) × прогноз режима."""
    mu = ctx.expectations
    if not mu or int(mu.get("until", 0)) < q:
        mu = regime_expectations(ctx, q)
    out: dict[str, dict[str, float]] = {"cor": {}, "nim": {}}
    for r in ctx.book.get("regimes.ids"):
        dev = ctx.deviations.get(r, {})
        for x in ("cor", "nim"):
            d = dev.get(x)
            out[x][r] = mu[x][r][q] + (d[q] if d is not None else 0.0)
    return out


def regime_update(book: Book, prep: Prepared, observations: Sequence[Mapping[str, Any]],
                  mu: Mapping[str, Any], prior: Mapping[str, float], Q: int
                  ) -> tuple[dict[str, float], dict[str, dict[str, tuple[float, ...]]], list[dict]]:
    """Апостериорные вероятности режимов и затухающие отклонения δ по режимам (М§12).

    `observations` — окно фильтра (`window_observations`): состояние стартует пустым, априорные —
    `prior` (базовые книги, в полосе — разыгранные). `mu` — ожидания режимов без отклонений
    (`regime_expectations`) до квартала последнего наблюдения; у наблюдения q ≤ 0 — замороженные
    ожидания записи (`obs["mu"]`)."""
    ru = "joint.regime_update"
    sigma = {x: float(book.get(f"{ru}.observables.{x}.sigma_pp")) for x in ("cor", "nim")}
    rho = {x: float(book.get(f"{ru}.observables.{x}.rho_q")) for x in ("cor", "nim")}
    limit = float(book.get(f"{ru}.max_shift_pp"))
    floor = float(book.get(f"{ru}.floor_share"))
    if not 0.0 <= floor <= 1.0:
        raise BookError(f"{ru}.floor_share = {floor}: доля от 0 до 1 (М§12)")
    shrink = bool(book.get(f"{ru}.shrink_by_se"))
    regimes = list(prior)
    mean = {x: {r: 0.0 for r in regimes} for x in ("cor", "nim")}
    var = {x: {r: 0.0 for r in regimes} for x in ("cor", "nim")}
    last_q: dict[str, int | None] = {"cor": None, "nim": None}
    marks: dict[str, list[tuple[int, dict[str, float]]]] = {"cor": [], "nim": []}
    current = dict(prior)
    updates = []
    for obs in observations:
        q = obs["q"]
        like = {r: 1.0 for r in regimes}
        for x in ("cor", "nim"):
            value = obs[x]
            if value is None:
                continue
            se = obs[f"se_{x}"]
            s2 = sigma[x] ** 2
            frozen = (obs.get("mu") or {}).get(x) if q <= 0 else None
            for r in regimes:
                dev = value - (frozen[r] if frozen is not None else mu[x][r][q])
                if last_q[x] is None:
                    prior_mean, prior_var = 0.0, s2
                else:
                    k = q - last_q[x]
                    rk = rho[x] ** k
                    prior_mean = rk * mean[x][r]
                    prior_var = s2 * (1 - rho[x] ** (2 * k)) + rho[x] ** (2 * k) * var[x][r]
                error = dev - prior_mean
                like[r] *= math.exp(-0.5 * error * error / (prior_var + se ** 2))
                if not se or not shrink:
                    mean[x][r], var[x][r] = dev, 0.0
                else:
                    gain = prior_var / (prior_var + se ** 2)
                    mean[x][r] = prior_mean + gain * error
                    var[x][r] = (1 - gain) * prior_var
            last_q[x] = q
            marks[x].append((q, dict(mean[x])))
        norm = sum(current[r] * like[r] for r in regimes)
        if norm <= 0:
            raise BookError(f"A-P2u {obs['period']}: правдоподобие всех режимов — ноль")
        posterior = {r: current[r] * like[r] / norm for r in regimes}
        current = cap_shift(current, posterior, limit)
        current = floor_shift(prior, current, floor)
        updates.append({**obs, "posterior_after": dict(current)})
    deviations: dict[str, dict[str, tuple[float, ...]]] = {}
    for r in regimes:
        dev_r = {}
        for x in ("cor", "nim"):
            series = [0.0] * (Q + 1)
            for q in range(1, Q + 1):
                last = None
                for mq, m in marks[x]:
                    if mq <= q:
                        last = (mq, m[r])
                if last is not None:
                    series[q] = rho[x] ** (q - last[0]) * last[1]
            dev_r[x] = tuple(series)
        deviations[r] = dev_r
    return current, deviations, updates


# ------------------------------------------------------------------ слои, цена


def price_from_v0(v0: float, bridge_amount: float, governance: float, shares_out: float,
                  pending: float = 0.0, *, governance_on_bridge: bool = False) -> float:
    """Цена М§8.2: [(V0 − D_pend)(1 − g_gov) + D_pend + B(v)] × 1000 / N_out; при V0 − D_pend ≤ 0
    дисконт не применяется. D_pend — объявленный дивиденд, ещё не вычтенный из BV модели: внутри V0,
    но без дисконта за управление. Одна формула у слоя, клетки, мира, прогонов полосы и P(цена ниже рынка)."""
    core = v0 - pending
    if core > 0:
        if governance_on_bridge:
            return (v0 + bridge_amount) * (1 - governance) * THOUSAND / shares_out
        return (core * (1 - governance) + pending + bridge_amount) * THOUSAND / shares_out
    return (v0 + bridge_amount) * THOUSAND / shares_out


@dataclass(frozen=True)
class Layer:
    name: str                              # analytical | market_implied | macro_neutral
    title: str
    world_weights: Mapping[str, float]
    prob: Mapping[tuple[str, str, str], float]         # вероятность клетки (W, r, s) в слое
    v0: float
    bv_v: float
    price: float
    pb: float
    excess: float
    pv_ri_explicit: float
    pv_terminal: float
    terminal_share: float
    roe_tc: float
    k_tc: float
    p_price_below_market: float
    p_roe_below_k: float
    capital_gap_mass: float


@dataclass(frozen=True)
class GridRun:
    ctx: RunContext
    cells: tuple[CellResult, ...]          # 36, порядок worlds.ids × regimes.ids × reg_scenarios.ids
    layers: Mapping[str, Layer]
    lam: float                             # λ книги
    low: float                             # ₽: слой «рыночные ставки как есть»
    high: float                            # ₽: слой «свой взгляд»
    point: float                           # ₽: точка при λ
    bridge_amount: float                   # B(v), млрд ₽ (М§8.1)
    governance: float                      # g_gov
    # сверх договора (только добавление)
    shares_out: float = 0.0
    market_price: float = 0.0
    bridge_rows: tuple[Mapping[str, Any], ...] = ()
    governance_on_bridge: bool = False
    pending_dividend: float = 0.0          # D_pend, млрд ₽: объявленный, ещё не вычтенный из BV модели (М§8.2)
    unregistered_dividend: float = 0.0     # U, млрд ₽: дивиденд, вычтенный клетками на концах кварталов ГОСА без
    #                                        записи реестра, ожидание слоя «свой взгляд» (М§14.4)

    def price_at(self, lam: float) -> float:
        return self.low + lam * (self.high - self.low)

    def cell(self, world: str, regime: str, scenario: str) -> CellResult:
        for c in self.cells:
            if c.world == world and c.regime == regime and c.scenario == scenario:
                return c
        raise KeyError((world, regime, scenario))

    def price_of(self, v0: float) -> float:
        """Цена М§8.2 для капитала v0 при мосте, D_pend и дисконте этого прогона."""
        return price_from_v0(v0, self.bridge_amount, self.governance, self.shares_out, self.pending_dividend,
                             governance_on_bridge=self.governance_on_bridge)

    def cell_price(self, c: CellResult) -> float:
        return self.price_of(c.v_ri)


def cell_probabilities(book: Book, world_weights: Mapping[str, float], posterior: Mapping[str, float]
                       ) -> dict[tuple[str, str, str], float]:
    table = book.get("joint.reg_prob_given_regime")
    out = {}
    for w in book.get("worlds.ids"):
        for r in book.get("regimes.ids"):
            for s in book.get("capital.reg_scenarios.ids"):
                out[(w, r, s)] = float(world_weights.get(w, 0.0)) * posterior[r] * float(table[r][s])
    return out


def layer_weights(book: Book) -> dict[str, dict[str, float]]:
    ids = tuple(book.get("worlds.ids"))
    neutral = str(book.get("joint.macro_neutral_world"))
    return {
        "analytical": {w: float(book.get(f"joint.world_prob.{w}")) for w in ids},
        "market_implied": {w: float(book.get(f"joint.world_prob_market_implied.{w}")) for w in ids},
        "macro_neutral": {w: (1.0 if w == neutral else 0.0) for w in ids},
    }


def make_layer(name: str, title: str, weights: Mapping[str, float], prob: Mapping[tuple, float],
               cells: Sequence[CellResult], price_of, market: float) -> Layer:
    v0 = sum(prob[c.key] * c.v_ri for c in cells)
    bv_v = sum(prob[c.key] * c.bv_v for c in cells)
    pv_ri = sum(prob[c.key] * c.pv_ri_explicit for c in cells)
    pv_t = sum(prob[c.key] * c.pv_terminal for c in cells)
    tv_pv = sum(prob[c.key] * c.terminal_share * c.v_ddm for c in cells)
    return Layer(
        name=name, title=title, world_weights=dict(weights), prob=dict(prob), v0=v0, bv_v=bv_v,
        price=price_of(v0), pb=v0 / bv_v if bv_v else float("nan"), excess=v0 - bv_v,
        pv_ri_explicit=pv_ri, pv_terminal=pv_t, terminal_share=tv_pv / v0 if v0 else float("nan"),
        roe_tc=sum(prob[c.key] * c.roe_t for c in cells), k_tc=sum(prob[c.key] * c.k_t for c in cells),
        p_price_below_market=sum(prob[c.key] for c in cells if price_of(c.v_ri) < market),
        p_roe_below_k=sum(prob[c.key] for c in cells if c.roe_t < c.k_t),
        capital_gap_mass=sum(prob[c.key] for c in cells if "capital_gap" in c.flags),
    )


def bridge_rows(ctx: RunContext) -> list[dict[str, Any]]:
    """Строки моста B(v) (М§8.1): объявленные записи реестра до экс-даты; `pending` — дивиденд ещё не
    вычтен из BV модели (v раньше даты вычета): он внутри V0 и входит в D_pend (М§8.2)."""
    book, tl, v = ctx.book, ctx.timeline, ctx.clock.valuation_date
    af = ctx.prep.af
    facts_date = to_date(book.get("meta.facts_date"))
    agm = int(book.get("dividends.calendar.agm_quarter"))
    rows = []
    for rec in ctx.live.register:
        if rec.status not in ("declared", "paid"):
            continue
        agm_q = tl.index(period_str(rec.year + 1, agm))
        before_anchor = agm_q <= 0 or rec.year == af.dividends_payable_year
        deducted_on = facts_date if before_anchor else tl.end(agm_q)
        ex = rec.ex
        sign = int(deducted_on <= v) - int(ex is not None and ex <= v)
        amount = rec.dps * af.n_out / THOUSAND
        rows.append({"year": rec.year, "dps": rec.dps, "amount": amount, "deducted_on": deducted_on,
                     "last_buy_date": rec.last_buy_date, "ex_date": ex, "sign": sign,
                     "contribution": sign * amount, "pending": v < deducted_on})
    return rows


def unregistered_dividend(ctx: RunContext, cells: Sequence[CellResult], prob: Mapping[tuple, float]) -> float:
    """U(v) = Σ E_L[Div_qA] по кварталам ГОСА, закрытым к дате оценки, за год которых в реестре нет записи
    declared | paid (М§14.4): дивиденд модели, который клетки уже вычли из капитала, а мост не закрыл."""
    tl, pol = ctx.timeline, ctx.prep.policy
    total = 0.0
    for q in range(1, ctx.clock.q0):
        if tl.h(q) != pol.agm_quarter or (tl.year(q) - 1) in ctx.prep.register:
            continue
        total += sum(prob[c.key] * float(c.quarters["div"][q] or 0.0) for c in cells)
    return total


def run_grid(book: Book, facts: Facts, live: LiveInputs | None = None) -> GridRun:
    """36 клеток, три слоя, мост, цена низа, верха и точки (чистая функция входов)."""
    ctx = make_context(book, facts, live)
    cells = tuple(run_cell(ctx, w, r, s)
                  for w in book.get("worlds.ids")
                  for r in book.get("regimes.ids")
                  for s in book.get("capital.reg_scenarios.ids"))
    return grid_from_cells(ctx, cells)


def grid_from_cells(ctx: RunContext, cells: Sequence[CellResult]) -> GridRun:
    book = ctx.book
    af = ctx.prep.af
    brows = bridge_rows(ctx)
    b_amount = sum(r["contribution"] for r in brows)
    pending = sum(r["amount"] for r in brows if r["pending"])
    g = float(book.get("valuation.governance.discount"))
    on_bridge = bool(book.get("bridge.governance_applies"))
    main = str(book.get("meta.company.main_ticker"))
    if main not in ctx.live.prices:
        raise BookError(f"нет цены главного тикера {main} в живых входах")
    market = float(ctx.live.prices[main])
    price_of = lambda v0: price_from_v0(v0, b_amount, g, af.n_out, pending,  # noqa: E731
                                        governance_on_bridge=on_bridge)
    layers = {}
    for name, weights in layer_weights(book).items():
        prob = cell_probabilities(book, weights, ctx.posterior)
        layers[name] = make_layer(name, LAYER_TITLES[name], weights, prob, cells, price_of, market)
    lam = float(book.get("joint.own_macro_confidence"))
    low, high = layers["macro_neutral"].price, layers["analytical"].price
    return GridRun(ctx=ctx, cells=tuple(cells), layers=layers, lam=lam, low=low, high=high,
                   point=low + lam * (high - low), bridge_amount=b_amount, governance=g,
                   shares_out=af.n_out, market_price=market, bridge_rows=tuple(brows),
                   governance_on_bridge=on_bridge, pending_dividend=pending,
                   unregistered_dividend=unregistered_dividend(ctx, cells, layers["analytical"].prob))


def evaluate(book: Book, facts: Facts, live: LiveInputs | None = None) -> tuple[float, float]:
    """(низ, верх), ₽ — для полосы и поисков."""
    run = run_grid(book, facts, live)
    return run.low, run.high


def world_layer(run: GridRun, world: str) -> Mapping[str, float]:
    """Слой «только этот мир» (П§2 fair_value.by_world)."""
    weights = {w: (1.0 if w == world else 0.0) for w in run.ctx.book.get("worlds.ids")}
    prob = cell_probabilities(run.ctx.book, weights, run.ctx.posterior)
    v0 = sum(prob[c.key] * c.v_ri for c in run.cells)
    bv = sum(prob[c.key] * c.bv_v for c in run.cells)
    price = run.price_of(v0)
    return {"price": price, "v0": v0, "bv_v": bv, "pb": v0 / bv if bv else float("nan"),
            "roe_t": sum(prob[c.key] * c.roe_t for c in run.cells),
            "k_t": sum(prob[c.key] * c.k_t for c in run.cells)}


# ------------------------------------------------------------------ язык банка (М§8.3)


def share_class_counts(book: Book, af: Any) -> dict[str, float | None]:
    """Тикер → акции в обращении его класса (`meta.company.share_classes.<t>`, М§8.3)."""
    classes = book.get("meta.company.share_classes")
    by_class = {"ordinary": af.n_out_ordinary, "preferred": af.n_out_preferred}
    return {str(t): by_class.get(str(classes[t])) for t in book.get("meta.company.tickers")}


def market_cap(run: GridRun) -> float | None:
    """Cap = Σ_t цена тикера × акции в обращении его класса / 1000 (класс — ключ книги, М§8.3)."""
    total = 0.0
    for t, n in share_class_counts(run.ctx.book, run.ctx.prep.af).items():
        price = run.ctx.live.prices.get(t)
        if price is None or n is None:
            return None
        total += float(price) * n / THOUSAND
    return total


def bank_language(run: GridRun, lam: float | None = None) -> dict[str, float | None]:
    """Справедливый P/B против рыночного, PV избыточной доходности, ROE_TC и k_TC точки."""
    lam = run.lam if lam is None else lam
    lo, hi = run.layers["macro_neutral"], run.layers["analytical"]
    mix = lambda a, b: a + lam * (b - a)
    bv = mix(lo.bv_v, hi.bv_v)
    v = mix(lo.v0, hi.v0)
    cap = market_cap(run)
    return {
        "lambda": lam, "bv_v": bv, "v_point": v, "fair_pb_point": v / bv,
        "market_cap": cap, "market_pb": None if cap is None else cap / bv,
        "excess_point": v - bv, "excess_market": None if cap is None else cap - bv,
        "roe_tc": mix(lo.roe_tc, hi.roe_tc), "k_tc": mix(lo.k_tc, hi.k_tc),
        "price_point": run.price_at(lam),
    }


def year_mgmt(ctx: RunContext, metric: str, year: int, annual: float | None, row) -> float | None:
    """Годовая упр. метрика `metric` ∈ {cor, nim, cir} клетки или смеси (М§4.6).

    `annual` — годовое значение движка; `row(name, q)` — значение ряда в прогнозном квартале q (ряд клетки
    или ожидание смеси). Год без отчётных кварталов — `to_mgmt(annual)`; год с отчётными кварталами —
    `credit.year_in_mgmt`: отчётные входят раскрытым упр. фактом, прогнозные — мостом; веса Z_q — средние
    кредиты АС × дни (CoR), средние процентные активы × дни (ЧПМ), операционный доход (CIR)."""
    tl, prep, br = ctx.timeline, ctx.prep, ctx.bridge
    qs = tl.quarters_of_year(year)
    if all(q >= 1 for q in qs):
        to_mgmt = {"cor": br.to_mgmt_cor, "nim": br.to_mgmt_nim, "cir": br.to_mgmt_cir}[metric]
        return None if annual is None else to_mgmt(annual)
    if any(q > tl.Q for q in qs):
        return None
    hist, ends = prep.hist, prep.hist_bal
    anchor_end = {"loans_ac": prep.anchor_state["loans"] - prep.af.fv_loans,
                  "iea": sum(prep.af.balances[b] for b in prep.roles.assets)}

    def end(name: str, q: int) -> float:
        value = anchor_end[name] if q == 0 else ends.get(q, {}).get(name)
        if value is None:
            raise FactsError(f"balance.history: нет {name} на конец {tl.period(q)} — вес отчётного квартала "
                             f"в упр. метрике года {year} не определён (М§4.6)")
        return float(value)

    def income(q: int) -> float:
        parts = [hist.get(k, {}).get(q) for k in ("nii", "fees", "ins", "misc")]
        if any(v is None for v in parts):
            raise FactsError(f"pnl_quarterly: нет строк дохода за {tl.period(q)} — вес отчётного квартала в CIR "
                             f"года {year} не определён (М§4.6)")
        return float(sum(parts))

    quarters = []
    for q in qs:
        d = tl.d(q)
        if q <= 0:                                       # отчётный квартал: вес — из фактов, значение — упр. факт
            if metric == "cir":
                z = income(q)
            else:
                name = "loans_ac" if metric == "cor" else "iea"
                z = (end(name, q - 1) + end(name, q)) / 2 * d
            quarters.append((tl.period(q), None, z))
        elif metric == "cor":
            avg = float(row("loans_ac_avg", q))
            quarters.append((tl.period(q), float(row("llp", q)) * DAYS_IN_YEAR / d / avg, avg * d))
        elif metric == "nim":
            avg = float(row("iea_avg", q))
            quarters.append((tl.period(q), float(row("nii", q)) * DAYS_IN_YEAR / d / avg, avg * d))
        else:
            z = sum(float(row(k, q)) for k in ("nii", "fees", "ins", "misc", "fvr"))
            quarters.append((tl.period(q), float(row("opex", q)) / z, z))
    return year_in_mgmt(br, ctx.facts, metric, quarters)


def modal_cell(book: Book, posterior: Mapping[str, float]) -> tuple[str, str, str]:
    """Модальная клетка книги (INTERFACES §4.6): мир с наибольшим весом слоя «свой взгляд», режим с наибольшей
    вероятностью после A-P2u, сценарий капитала с наибольшей P(s | r) этого режима; при равенстве — первый
    по порядку книги."""
    worlds = list(book.get("worlds.ids"))
    world = max(worlds, key=lambda w: (float(book.get(f"joint.world_prob.{w}")), -worlds.index(w)))
    regimes = list(book.get("regimes.ids"))
    regime = max(regimes, key=lambda r: (float(posterior[r]), -regimes.index(r)))
    scenarios = list(book.get("capital.reg_scenarios.ids"))
    table = book.get(f"joint.reg_prob_given_regime.{regime}")
    scenario = max(scenarios, key=lambda s: (float(table[s]), -scenarios.index(s)))
    return world, regime, scenario


def volume_sign_test(book: Book, facts: Facts, live: LiveInputs | None = None, *,
                     run: GridRun | None = None) -> dict[str, Any]:
    """Тест знака объёмных эффектов (М§4.5): снятие остановки кредита в кризисе (`loan_growth_override` пуст)
    и поправок роста режимов (`loan_growth_adj` = 0) не снижает точку, и цена мира M меняется при этом не хуже
    цены мира H (в мире H сжатия нет — там это цена капитала, а не раскладка сжатия)."""
    run = run or run_grid(book, facts, live)
    ov: dict[str, Any] = {}
    for r in book.get("regimes.ids"):
        ov[f"regimes.{r}.loan_growth_adj"] = 0.0
        if book.get(f"regimes.{r}").get("loan_growth_override"):
            ov[f"regimes.{r}.loan_growth_override"] = {}
    free = run_grid(book.with_overrides(ov), facts, run.ctx.live)
    ref = str(book.get("nii.transmission.reference_world"))
    d_world = {w: world_layer(free, w)["price"] - world_layer(run, w)["price"] for w in book.get("worlds.ids")}
    d_point = free.point - run.point
    return {"point": run.point, "point_free": free.point, "d_point": d_point, "d_world": d_world,
            "market_world": MARKET_WORLD, "reference_world": ref,
            "ok": bool(d_point >= -SIGN_TOL and d_world[MARKET_WORLD] >= d_world[ref] - SIGN_TOL)}


def realized_cells(run: GridRun) -> float:
    """Передача по клеткам: [ЧПМ последнего года (M, norm, schedule) − то же (N, …)] / (key_M^LT − key_N^LT)
    (диагностика выпуска `nii.transmission.realized_cells`, М§4.5)."""
    tl = run.ctx.timeline
    last = [q for q in range(1, tl.Q + 1) if tl.year(q) == tl.last_year]

    def nim(c: CellResult) -> float:
        nii = sum(c.quarters["nii"][q] for q in last)
        iea = sum(c.quarters["iea_avg"][q] * tl.d(q) for q in last) / sum(tl.d(q) for q in last)
        return nii / iea
    m = run.cell(MARKET_WORLD, REFERENCE_REGIME, REFERENCE_SCENARIO)
    n = run.cell(BASE_WORLD, REFERENCE_REGIME, REFERENCE_SCENARIO)
    dkey = run.ctx.worlds[MARKET_WORLD].key[tl.Q] - run.ctx.worlds[BASE_WORLD].key[tl.Q]
    return (nim(m) - nim(n)) / dkey


def derived_values(ctx: RunContext) -> dict[str, Any]:
    """Выводимое ядром (М прил. A → results.json `derived`): передача, anchor-ключи, RWA_0, полы, CoR движка."""
    tl, tr, prep = ctx.timeline, ctx.transmission, ctx.prep
    years = range(tl.anchor_year, tl.last_year + 1)

    def by_year(series: Sequence[float]) -> dict[str, float]:
        out = {}
        for y in years:
            qs = [q for q in tl.quarters_of_year(y) if 1 <= q <= tl.Q]
            if qs:
                out[str(y)] = sum(series[q] for q in qs) / len(qs)
        return out

    shock = next((rp.shock_year for rp in prep.regimes.values() if rp.shock_year is not None), None)
    return {
        "nii": {"sigma0": tr.sigma0, "sigma0_liab": tr.sigma0_liab, "sigma0_split": tr.split, "delta0": tr.delta0,
                "lt_spread": {b: dict(row) for b, row in tr.lt_spread.items()},
                "phi": tr.phi, "phi_assets": tr.phi_assets, "phi_liab": tr.phi_liab, "phi_split": tr.phi_split,
                "loan_margin": dict(tr.loan_margin), "target_eng": tr.target_eng, "T_real": tr.t_real,
                "T": dict(tr.t_local), "T_pairs": dict(tr.pairs), "roe_equiv": tr.roe_equiv,
                "roe_equiv_pairs": dict(tr.pairs_roe), "D": tr.d, "nss": dict(tr.nss)},
        "anchor": dict(ctx.book.anchors),
        "capital": {"rwa0": prep.rwa0,
                    "floor20": {s: by_year(sp.floor20) for s, sp in prep.scenarios.items()},
                    "floor11": {s: by_year(sp.floor11) for s, sp in prep.scenarios.items()}},
        "regimes": {**{r: {"cor_engine": by_year(rp.cor_engine)} for r, rp in prep.regimes.items()},
                    "shock_year": shock},
    }


def sensitivity_overrides(book: Book, kind: str, step: float | None = None) -> dict[str, Any]:
    """Сдвиги книги для строк «₽ за 0,1 п.п. CoR / ЧПМ» (М§8.3): все пути CoR режимов — на cor_pp;
    ЧПМ сквозь цикл — на nim_pp. Пересчёт медианы с привязкой — model/uncertainty.py."""
    from model.paths import shift_trajectory
    if kind == "cor":
        d = float(book.get("valuation.sensitivities.cor_pp")) if step is None else step
        return {f"regimes.{r}.cor": shift_trajectory(book.get(f"regimes.{r}.cor"), d)
                for r in book.get("regimes.ids")}
    if kind == "nim":
        d = float(book.get("valuation.sensitivities.nim_pp")) if step is None else step
        return {"nii.nim_lt_target_mgmt": float(book.get("nii.nim_lt_target_mgmt")) + d}
    raise BookError(f"чувствительность {kind!r}: известны cor, nim")
