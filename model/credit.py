"""Резервы в базисе движка и мост упр. ↔ МСФО (М§4.6) — единственное место
перевода базисов.

CoR движка — резервы по долговым ФА (`llp_debt_fa`) к средним валовым кредитам
АС. Пути CoR режимов книги (`regimes.cor_basis: mgmt`) переводятся в движок
мостом `to_engine_cor`; тем же мостом идут наблюдения A-P2u, строки «что даст
отчёт», цель ЧПМ сквозь цикл и гейты по гайденсу (ЧПМ, CoR и CIR — в упр. базисе;
мост CIR — только гайденс и печать, М§4.6). κ-добавка — относительно
мира N (в нём откалиброваны пути, добавка там ноль) с лагом L кварталов.

Кредитная переоценка кредитов по СС: уровень — в «прочем», отклонение CoR клетки (с
κ-добавкой и отклонением A-P2u) от пути режима-опоры `credit.fv_loans_ref` (через тот же
мост, без κ и δ; путь — книги до розыгрыша осей) — строкой FVC на средних кредитах по СС
(`fvc_amount`, М§4.6).

Год с отчётными кварталами (`year_in_mgmt`, М§4.6): средний мост переводит только то, чего
отчётность ещё не раскрыла; годовая упр. метрика — взвешенное среднее кварталов, отчётный
квартал входит раскрытым упр. фактом истории моста, прогнозный — мостом. Одна функция у
гейта `guidance_gap` и у выпуска.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from model.book_schema import BookError, FactsError
from model.paths import Trajectory
from model.timeline import Timeline, parse_period

if TYPE_CHECKING:
    from model.book import Book, Facts

METHODS = ("additive", "ratio")
MGMT_METRICS = ("cor", "nim", "cir")       # метрики, которые мост переводит между упр. базисом и движком


@dataclass(frozen=True)
class Bridge:
    cor_method: str
    cor_value: float
    nim_method: str
    nim_value: float
    cir_method: str = "additive"           # мост CIR (W1, A7): гайденс и cir_mgmt выпуска
    cir_value: float = 0.0

    @staticmethod
    def _to(method: str, b: float, v: float) -> float:
        return v + b if method == "additive" else v * b

    @staticmethod
    def _back(method: str, b: float, v: float) -> float:
        return v - b if method == "additive" else v / b

    def to_engine_cor(self, v: float) -> float:
        return self._to(self.cor_method, self.cor_value, v)

    def to_mgmt_cor(self, v: float) -> float:
        return self._back(self.cor_method, self.cor_value, v)

    def to_engine_nim(self, v: float) -> float:
        return self._to(self.nim_method, self.nim_value, v)

    def to_mgmt_nim(self, v: float) -> float:
        return self._back(self.nim_method, self.nim_value, v)

    def to_engine_cir(self, v: float) -> float:
        return self._to(self.cir_method, self.cir_value, v)

    def to_mgmt_cir(self, v: float) -> float:
        return self._back(self.cir_method, self.cir_value, v)


def bridge_from_facts(facts: "Facts") -> Bridge:
    """Мост из `facts/bridge_mgmt_ifrs.json` (method, value для cor, nim и cir); нет узла — FactsError."""
    out = {}
    for x in ("cor", "nim", "cir"):
        method = str(facts.plain("bridge_mgmt_ifrs", f"{x}.method"))
        if method not in METHODS:
            raise FactsError(f"bridge_mgmt_ifrs.{x}.method = {method!r}: известны {', '.join(METHODS)}")
        value = facts.need("bridge_mgmt_ifrs", f"{x}.value")
        if method == "ratio" and value == 0:
            raise FactsError(f"bridge_mgmt_ifrs.{x}.value: мост-отношение не может быть нулём")
        out[x] = (method, value)
    return Bridge(cor_method=out["cor"][0], cor_value=out["cor"][1],
                  nim_method=out["nim"][0], nim_value=out["nim"][1],
                  cir_method=out["cir"][0], cir_value=out["cir"][1])


def mgmt_history(facts: "Facts", metric: str) -> dict[str, float]:
    """Раскрытые упр. значения метрики по кварталам — `bridge_mgmt_ifrs.json → <metric>.history[].mgmt`."""
    node = facts.file("bridge_mgmt_ifrs").get(metric) or {}
    out = {}
    for row in node.get("history") or []:
        value = row.get("mgmt")
        value = value.get("v") if isinstance(value, dict) else value
        if value is not None:
            out[str(row.get("period"))] = float(value)
    return out


def year_in_mgmt(bridge: Bridge, facts: "Facts", metric: str,
                 quarters: Sequence[tuple[str, float | None, float]]) -> float:
    """Годовая упр. метрика года с отчётными кварталами (М§4.6): x_упр,Y = Σ_q w_q × x_упр,q, w_q = Z_q / Σ Z.

    `quarters` — (период, значение движка, вес Z_q) всех кварталов года; у отчётного квартала (не позже
    якоря фактов) берётся упр. факт истории моста (значение движка не читается), у прогнозного —
    `to_mgmt` значения движка. Отчётный квартал без записи истории — `FactsError`, не ноль."""
    if metric not in MGMT_METRICS:
        raise FactsError(f"упр. метрика {metric!r}: известны {', '.join(MGMT_METRICS)}")
    to_mgmt = {"cor": bridge.to_mgmt_cor, "nim": bridge.to_mgmt_nim, "cir": bridge.to_mgmt_cir}[metric]
    anchor = parse_period(str(facts.plain("anchor", "period")))
    history = mgmt_history(facts, metric)
    total = sum(float(z) for _, _, z in quarters)
    if not quarters or total == 0:
        raise FactsError(f"упр. метрика {metric} года: нет кварталов или нулевые веса (М§4.6)")
    acc = 0.0
    for period, value, z in quarters:
        if parse_period(str(period)) <= anchor:
            if str(period) not in history:
                raise FactsError(f"bridge_mgmt_ifrs.{metric}.history: нет упр. факта отчётного квартала {period} — "
                                 "история моста обязана нести все отчётные кварталы года якоря (М§4.6)")
            x = history[str(period)]
        else:
            if value is None:
                raise FactsError(f"упр. метрика {metric}: нет значения движка прогнозного квартала {period}")
            x = to_mgmt(float(value))
        acc += float(z) * x
    return acc / total


def cor_to_engine(book: "Book", bridge: Bridge, value: float) -> float:
    """Значение пути CoR книги → базис движка по `regimes.cor_basis`."""
    basis = book.get("regimes.cor_basis")
    if basis == "mgmt":
        return bridge.to_engine_cor(value)
    if basis == "engine":
        return value
    raise BookError(f"regimes.cor_basis = {basis!r}: известны mgmt, engine")


def regime_cor_engine(book: "Book", bridge: Bridge, regime: str, timeline: Timeline) -> tuple[float, ...]:
    """Путь CoR режима в базисе движка по кварталам q = 0…Q (без κ и отклонений A-P2u)."""
    t = Trajectory(book.get(f"regimes.{regime}.cor"))
    return tuple(cor_to_engine(book, bridge, t.value(*parse_period(timeline.period(q))))
                 for q in range(timeline.Q + 1))


def fvc_amount(factor: float, cor_corporate: float, cor_ref: float, fv_avg: float, dt: float) -> float:
    """FVC_q = factor × (CoR_corporate,q − CoR_ref,q) × Ē^СС × d/365 (М§4.6): расход при CoR выше опоры."""
    return factor * (cor_corporate - cor_ref) * fv_avg * dt


def kappa_addon(kappa: float, lag: int, real_w: Sequence[float], real_n: Sequence[float],
                Q: int) -> tuple[float, ...]:
    """κ × max(0, rr_W,(q−L) − rr_N,(q−L)); для q − L < 1 (история до сетки) — 0."""
    out = [0.0] * (Q + 1)
    for q in range(1, Q + 1):
        j = q - lag
        if j >= 1:
            out[q] = kappa * max(0.0, real_w[j] - real_n[j])
    return tuple(out)
