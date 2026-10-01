"""Миры ставок по кварталам и дисконт мира (М§0.5, §3.1).

Мир — набор: ключевая, ИПЦ, зарплаты, ОФЗ 1/3/5/10 лет, реальная ключевая
(полугодовые траектории; квартал берёт значение своего полугодия), бескупонная
кривая и долгосрочная инфляция; банковская надстройка `worlds_bank` — рост
кредитов по секторам и средств клиентов по годам со сходом к g_T мира к
`volumes.lt_from`. Ряды — кортежи с индексом q = 0…Q ([0] — значение правила
М§0.4 в квартале якоря, справочно).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from model.book_schema import BookError
from model.paths import Trajectory
from model.timeline import QUARTER_YEARS, Clock, Timeline, parse_period

if TYPE_CHECKING:
    from model.book import Book

# Миры семейства (М§3.1): N — мир калибровки путей режимов (κ-добавка в нём
# ноль, NIM_ref A-P2u — клетка этого мира); M — «рыночный как есть». Реализованная
# передача ставки — разность стационарных ЧПМ M и N (М§4.5).
BASE_WORLD = "N"
MARKET_WORLD = "M"
OFZ_KEYS = ("ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y")
CURVE_NODES = ((1.0, "1"), (3.0, "3"), (5.0, "5"), (10.0, "10"))
SECTORS = ("corporate", "mortgage", "retail_other")
FUND_SECTORS = ("retail", "corporate")


def check_family_worlds(book: "Book") -> tuple[str, ...]:
    ids = tuple(book.get("worlds.ids"))
    for w in (BASE_WORLD, MARKET_WORLD):
        if w not in ids:
            raise BookError(f"worlds.ids: нет мира {w} (миры семейства N, H, M — М§3.1)")
    return ids


def g_terminal(book: "Book", world: str) -> float:
    """g_T мира без защиты k_T: (1 + LT-инфляция)(1 + реальный рост) − 1 (М§7)."""
    return ((1 + float(book.get(f"worlds.{world}.lt_inflation")))
            * (1 + float(book.get("valuation.terminal.real_growth"))) - 1)


def _series(traj, timeline: Timeline) -> tuple[float, ...]:
    t = Trajectory(traj)
    return tuple(t.value(*parse_period(timeline.period(q))) for q in range(timeline.Q + 1))


@dataclass(frozen=True)
class WorldPath:                           # ряды по кварталам (индекс q = 0…Q)
    world: str
    key: tuple[float, ...]
    cpi: tuple[float, ...]
    wage: tuple[float, ...]
    ofz: Mapping[str, tuple[float, ...]]   # "ofz_1y" … "ofz_10y"
    real_key: tuple[float, ...]
    price_index: tuple[float, ...]         # I_W,q, I_W,0 = 1
    credit_growth: Mapping[str, Mapping[int, float]]   # сектор → год → доля (надстройка + сход к g_T)
    funds_growth: Mapping[str, Mapping[int, float]]
    lt_inflation: float
    g_t: float

    def ref(self, name: str) -> tuple[float, ...]:
        """Опорная ставка книги: key или ofz_<срок>."""
        return self.key if name == "key" else self.ofz[name]


def world_path(book: "Book", world: str, timeline: Timeline) -> WorldPath:
    base = f"worlds.{world}"
    key = _series(book.get(f"{base}.key_rate"), timeline)
    cpi = _series(book.get(f"{base}.cpi"), timeline)
    wage = _series(book.get(f"{base}.wage_growth"), timeline)
    ofz = {k: _series(book.get(f"{base}.{k}"), timeline) for k in OFZ_KEYS}
    real_key = _series(book.get(f"{base}.real_key"), timeline)
    index = [1.0]
    for q in range(1, timeline.Q + 1):
        index.append(index[-1] * (1 + cpi[q]) ** QUARTER_YEARS)
    g_t = g_terminal(book, world)
    lt_from = int(book.get("volumes.lt_from"))
    years = range(timeline.anchor_year, timeline.last_year + 1)

    def converge(table: Mapping) -> dict[int, float]:
        traj = {str(k): float(v) for k, v in table.items()}
        traj["LT"] = g_t
        traj["LT_from"] = lt_from
        t = Trajectory(traj)
        return {y: t.year_value(y) for y in years}

    wb = f"worlds_bank.{world}"
    credit = {s: converge(book.get(f"{wb}.credit_growth.{s}")) for s in SECTORS}
    funds = {s: converge(book.get(f"{wb}.funds_growth.{s}")) for s in FUND_SECTORS}
    return WorldPath(world=world, key=key, cpi=cpi, wage=wage, ofz=ofz, real_key=real_key,
                     price_index=tuple(index), credit_growth=credit, funds_growth=funds,
                     lt_inflation=float(book.get(f"{base}.lt_inflation")), g_t=g_t)


# ------------------------------------------------------------------ дисконт


class ZeroCurve:
    """Бескупонная кривая мира (М§0.5): узлы 1, 3, 5, 10 лет и LT, годовое начисление."""

    __slots__ = ("nodes", "lt", "premium")

    def __init__(self, curve: Mapping[str, float], premium: float):
        self.nodes = [(t, float(curve[k])) for t, k in CURVE_NODES]
        self.lt = float(curve["LT"])
        self.premium = premium

    def z(self, t: float) -> float:
        nodes = self.nodes
        if t <= nodes[0][0]:
            return nodes[0][1]
        for (t0, z0), (t1, z1) in zip(nodes, nodes[1:]):
            if t <= t1:
                return z0 + (z1 - z0) * (t - t0) / (t1 - t0)
        t10, z10 = nodes[-1]
        return ((1 + z10) ** t10 * (1 + self.lt) ** (t - t10)) ** (1 / t) - 1

    def df(self, t: float) -> float:
        if t <= 0:
            return 1.0
        return (1 + self.z(t) + self.premium) ** (-t)


@dataclass(frozen=True)
class Discount:
    k: tuple[float, ...]                   # k_q, q = 0…Q (до q0 — 0)
    k_t: float                             # годовой форвард за T
    dfq: tuple[float, ...]                 # DF_v(τ_q), q = 0…Q (до q0 — 1)
    curve: ZeroCurve
    roll: float

    def df(self, tau: float) -> float:     # DF_v(τ) с перекатом
        return self.curve.df(self.roll + tau) / self.curve.df(self.roll)


def discount(book: "Book", world: str, clock: Clock) -> Discount:
    premium = float(book.get("valuation.beta_e")) * float(book.get("valuation.erp"))
    curve = ZeroCurve(book.get(f"worlds.{world}.zero_curve"), premium)
    base = curve.df(clock.roll)
    Q = len(clock.tau) - 1

    def dfv(tau: float) -> float:
        return curve.df(clock.roll + tau) / base

    dfq = [1.0] * (Q + 1)
    k = [0.0] * (Q + 1)
    for q in range(clock.q0, Q + 1):
        dfq[q] = dfv(clock.tau[q])
        prev = dfv(clock.tau[q - 1]) if q > clock.q0 else 1.0
        k[q] = prev / dfq[q] - 1
    tq = clock.tau[Q]
    k_t = dfv(tq) / dfv(tq + 1) - 1
    return Discount(k=tuple(k), k_t=k_t, dfq=tuple(dfq), curve=curve, roll=clock.roll)
