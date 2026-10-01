"""Кварталы сетки, дни, дата оценки на линейке (М§0.2).

q = 0 — якорь (`meta.anchor_period`), q = 1…Q — кварталы от `meta.first_period`
до `meta.last_period`. Проценты — по дням (act/365), дисконт — по линейке
0,25 года на квартал: τ_q = 0,25 × [(1 − e) + (q − q0)].

Конец квартала — конец дня (М§0.2): дата оценки v = E_q (q = 0…Q − 1) закрывает
квартал q — q0 = q + 1, e = 0 (капитал на дату оценки — после дивиденда квартала
ГОСА, как отчётность на эту дату; мост §8.1 включается в тот же день). Внутри
квартала день v не считается прошедшим. v ≥ E_Q — вне сетки.

Положение даты на линейке — той же конвенцией, одна функция для даты оценки и даты
кривой: pos(x) = (q0(x) − 1) + e(x); pos(E_q) = q, как у S_(q+1). Перекат
Δ = 0,25 × (pos(v) − pos(curve_as_of)) и τ считаются одной линейкой, поэтому на E_q и
S_(q+1) точка одна и та же.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

from model.book_schema import BookError, QUARTER_KEY

if TYPE_CHECKING:
    from model.book import Book

QUARTER_YEARS = 0.25          # доля года на квартал на линейке дисконта
DAYS_IN_YEAR = 365            # act/365: база начисления процентов


@lru_cache(maxsize=None)
def parse_period(p: str) -> tuple[int, int]:
    """"2026Q3" → (2026, 3). Чистая функция строки — с памятью: клетка зовёт её на каждом шаге."""
    m = QUARTER_KEY.match(str(p))
    if not m:
        raise BookError(f"период «{p}» — не квартал ГГГГQn")
    return int(m.group(1)), int(m.group(2))


def period_str(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def quarter_start(year: int, quarter: int) -> date:
    return date(year, 3 * (quarter - 1) + 1, 1)


def quarter_end(year: int, quarter: int) -> date:
    y, q = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return quarter_start(y, q) - timedelta(days=1)


def quarter_days(year: int, quarter: int) -> int:
    return (quarter_end(year, quarter) - quarter_start(year, quarter)).days + 1


def quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь")


def period_words(period: object) -> str:
    """Период словами для текстов выпуска (П§0.2): «3 кв. 2026», «сентябрь 2026»; прочее — как есть."""
    text = str(period)
    m = QUARTER_KEY.match(text)
    if m:
        return f"{int(m.group(2))} кв. {m.group(1)}"
    head, sep, tail = text.partition("M")
    if sep and head.isdigit() and tail.isdigit() and 1 <= int(tail) <= len(MONTHS):
        return f"{MONTHS[int(tail) - 1]} {head}"
    return text


def shift_quarter(year: int, quarter: int, n: int) -> tuple[int, int]:
    idx = year * 4 + (quarter - 1) + n
    return idx // 4, idx % 4 + 1


def to_date(x) -> date:
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x))


@dataclass(frozen=True)
class Timeline:
    anchor: str                            # meta.anchor_period
    periods: tuple[str, ...]               # q = 1..Q
    days: tuple[int, ...]                  # d_q, q = 1..Q

    @property
    def Q(self) -> int:
        return len(self.periods)

    def period(self, q: int) -> str:
        """Период квартала q (q ≤ 0 — история до сетки календарными кварталами)."""
        return _period_at(self.anchor, q)

    def index(self, period: str) -> int:
        """q периода (якорь — 0, до якоря — отрицательные)."""
        ay, aq = parse_period(self.anchor)
        y, qq = parse_period(period)
        return (y * 4 + qq) - (ay * 4 + aq)

    def year(self, q: int) -> int:
        return parse_period(self.period(q))[0]

    def h(self, q: int) -> int:
        return parse_period(self.period(q))[1]

    def d(self, q: int) -> int:
        if 1 <= q <= self.Q:
            return self.days[q - 1]
        return quarter_days(*parse_period(self.period(q)))

    def start(self, q: int) -> date:
        return quarter_start(*parse_period(self.period(q)))

    def end(self, q: int) -> date:
        return quarter_end(*parse_period(self.period(q)))

    def q_of_date(self, d: date) -> int:
        """Квартал сетки, содержащий дату (до сетки — q ≤ 0)."""
        return self.index(period_str(*quarter_of(d)))

    def split(self, d: date) -> tuple[int, float]:
        """(q0, e) даты по «концу дня» (М§0.2): в день E_q квартал q закрыт — (q + 1, 0);
        внутри квартала q — (q, (d − S_q)/d_q). Линейка продолжается в прошлое: q ≤ 0."""
        q = self.q_of_date(d)
        if d == self.end(q):
            return q + 1, 0.0
        return q, (d - self.start(q)).days / self.d(q)

    def pos(self, d: date) -> float:
        """Положение даты на линейке: (q0 − 1) + e, pos(E_q) = q (М§0.2)."""
        q0, e = self.split(d)
        return (q0 - 1) + e

    @property
    def anchor_year(self) -> int:
        return parse_period(self.anchor)[0]

    @property
    def last_year(self) -> int:
        return parse_period(self.periods[-1])[0]

    def quarters_of_year(self, year: int) -> list[int]:
        """Индексы q (возможно ≤ 0) четырёх кварталов года."""
        ay, aq = parse_period(self.anchor)
        first = (year * 4 + 1) - (ay * 4 + aq)
        return [first, first + 1, first + 2, first + 3]


@lru_cache(maxsize=None)
def _period_at(anchor: str, q: int) -> str:
    ay, aq = parse_period(anchor)
    return period_str(*shift_quarter(ay, aq, q))


def make_timeline(book: "Book") -> Timeline:
    anchor = str(book.get("meta.anchor_period"))
    first = str(book.get("meta.first_period"))
    last = str(book.get("meta.last_period"))
    ay, aq = parse_period(anchor)
    fy, fq = parse_period(first)
    ly, lq = parse_period(last)
    if (fy * 4 + fq) != (ay * 4 + aq) + 1:
        raise BookError("meta.first_period — не следующий квартал после meta.anchor_period")
    n = (ly * 4 + lq) - (fy * 4 + fq) + 1
    if n <= 0:
        raise BookError("meta.last_period раньше meta.first_period")
    periods, days = [], []
    for i in range(n):
        y, q = shift_quarter(fy, fq, i)
        periods.append(period_str(y, q))
        days.append(quarter_days(y, q))
    return Timeline(anchor=anchor, periods=tuple(periods), days=tuple(days))


@dataclass(frozen=True)
class Clock:                               # дата оценки на линейке (М§0.2)
    valuation_date: date
    q0: int
    elapsed: float
    tau: tuple[float, ...]                 # τ_q, q = 0..Q (до q0 — 0)
    roll: float                            # Δ переката по форвардам

    def tau_q(self, q: int) -> float:
        return self.tau[q] if q >= 0 else 0.0


def make_clock(book: "Book", timeline: Timeline, valuation_date: date) -> Clock:
    v = to_date(valuation_date)
    facts_date = to_date(book.get("meta.facts_date"))
    if v < facts_date:
        raise BookError(f"дата оценки {v} раньше даты фактов {facts_date}")
    q0, e = timeline.split(v)               # конец квартала — конец дня: квартал закрыт
    if q0 < 1 or q0 > timeline.Q:
        raise BookError(f"дата оценки {v} вне сетки кварталов (конец дня E_Q и позже — вне сетки)")
    tau = [0.0] * (timeline.Q + 1)
    for q in range(q0, timeline.Q + 1):
        tau[q] = QUARTER_YEARS * ((1 - e) + (q - q0))
    roll = 0.0
    if book.get("valuation.roll_along_forwards"):
        curve = to_date(book.get("meta.curve_as_of"))
        if curve <= v:                      # одна линейка с τ: pos(v) = (q0 − 1) + e
            roll = QUARTER_YEARS * (((q0 - 1) + e) - timeline.pos(curve))
    return Clock(valuation_date=v, q0=q0, elapsed=e, tau=tuple(tau), roll=roll)
