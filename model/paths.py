"""Траектории книги (М§0.4) и точечные пути.

Траектория — словарь `{ключ: значение}` или скаляр. Значение в квартале:
точный ключ квартала → ключ полугодия → ключ года → линейная интерполяция по
годам → линейный сход к `LT` к году `LT_from` → плоско назад до первого ключа.
Словари-переопределения по годам — не траектории (`override_value`).
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, MutableMapping

from model.book_schema import BookError, is_number, parse_time_key


class Trajectory:
    """Разобранная траектория: быстрый ответ «значение в квартале»."""

    __slots__ = ("scalar", "quarters", "halves", "years", "lt", "lt_from", "first_key")

    def __init__(self, traj: Any):
        self.scalar: float | None = None
        self.quarters: dict[tuple[int, int], float] = {}
        self.halves: dict[tuple[int, int], float] = {}
        self.years: dict[int, float] = {}
        self.lt: float | None = None
        self.lt_from: int | None = None
        if is_number(traj):
            self.scalar = float(traj)
            return
        if not isinstance(traj, Mapping) or not traj:
            raise BookError(f"траектория — число или непустой словарь, получено {traj!r}")
        for key, value in traj.items():
            key = str(key)
            if key == "LT_from":
                self.lt_from = int(value)
                continue
            if value is None or not is_number(value):
                raise BookError(f"траектория: значение ключа {key} — не число ({value!r})")
            if key == "LT":
                self.lt = float(value)
                continue
            kind, year, n = parse_time_key(key)
            if kind == "Q":
                self.quarters[(year, n)] = float(value)
            elif kind == "H":
                self.halves[(year, n)] = float(value)
            else:
                self.years[year] = float(value)
        # Годы, заданные только полугодиями или кварталами, — среднее года
        # (его читают интерполяция соседних лет и печать по годам).
        for year in {y for y, _ in self.halves} | {y for y, _ in self.quarters}:
            if year in self.years:
                continue
            qs = [self.quarters.get((year, q)) for q in (1, 2, 3, 4)]
            if all(v is not None for v in qs):
                self.years[year] = sum(qs) / 4
                continue
            hs = [self.halves.get((year, h)) for h in (1, 2)]
            if all(v is not None for v in hs):
                self.years[year] = sum(hs) / 2
        keyed = ([y * 4 + q - 1 for y, q in self.quarters] + [y * 4 + (h - 1) * 2 for y, h in self.halves]
                 + [y * 4 for y in self.years])
        self.first_key = min(keyed) if keyed else None

    def year_value(self, year: int) -> float:
        """Значение года по правилам 3–6 (без ключей квартала и полугодия)."""
        if self.scalar is not None:
            return self.scalar
        if year in self.years:
            return self.years[year]
        if not self.years:
            if self.lt is not None:
                return self.lt
            raise BookError("траектория без значений")
        ys = sorted(self.years)
        if year < ys[0]:
            return self.years[ys[0]]
        if year > ys[-1]:
            last_y, last_v = ys[-1], self.years[ys[-1]]
            if self.lt is None:
                return last_v
            if self.lt_from is None or self.lt_from <= last_y + 1 or year >= self.lt_from:
                return self.lt
            return last_v + (self.lt - last_v) * (year - last_y) / (self.lt_from - last_y)
        lo = max(y for y in ys if y < year)
        hi = min(y for y in ys if y > year)
        return self.years[lo] + (self.years[hi] - self.years[lo]) * (year - lo) / (hi - lo)

    def value(self, year: int, quarter: int) -> float:
        if self.scalar is not None:
            return self.scalar
        v = self.quarters.get((year, quarter))
        if v is not None:
            return v
        v = self.halves.get((year, 1 if quarter <= 2 else 2))
        if v is not None:
            return v
        if year in self.years:
            return self.years[year]
        # Правило 6: до первого заданного ключа — значение первого ключа.
        if self.first_key is not None and year * 4 + quarter - 1 < self.first_key:
            idx = self.first_key
            fy, fq = idx // 4, idx % 4 + 1
            return self.value(fy, fq)
        return self.year_value(year)


def _parse_period(period: str) -> tuple[int, int]:
    kind, year, n = parse_time_key(period)
    if kind != "Q":
        raise BookError(f"период «{period}» — не квартал ГГГГQn")
    return year, n


def path_value(traj: Any, period: str, *, last_period: str | None = None) -> float:
    """Значение траектории в квартале `period` по правилу М§0.4."""
    year, q = _parse_period(period)
    return Trajectory(traj).value(year, q)


def year_mean(traj: Any, year: int) -> float:
    """Среднее четырёх кварталов года (годовые суммы и доли книги по году)."""
    t = Trajectory(traj)
    return sum(t.value(year, q) for q in (1, 2, 3, 4)) / 4


def override_value(table: Mapping | None, year: int) -> float | None:
    """Словарь-переопределение по годам: значение года или None (правила 4–6 не действуют)."""
    if not table:
        return None
    value = table.get(str(year))
    if value is None:
        value = table.get(year)  # type: ignore[call-overload]
    return None if value is None else float(value)


def shift_trajectory(traj: Any, delta: float) -> Any:
    """Ось вида shift: сдвиг всех значений траектории, кроме LT_from."""
    if is_number(traj):
        return float(traj) + delta
    if not isinstance(traj, Mapping):
        raise BookError(f"сдвиг: траектория — число или словарь, получено {traj!r}")
    return {k: (v if str(k) == "LT_from" else float(v) + delta) for k, v in traj.items()}


def _key(container: Any, part: str) -> Any:
    if isinstance(container, Mapping):
        if part in container:
            return part
        if part.lstrip("-").isdigit() and int(part) in container:
            return int(part)
        return None
    if isinstance(container, list) and part.isdigit() and int(part) < len(container):
        return int(part)
    return None


def get_path(data: Mapping, dotted: str) -> Any:
    """Значение по точечному пути; нет ключа — BookError."""
    cur: Any = data
    for part in dotted.split("."):
        key = _key(cur, part)
        if key is None:
            raise BookError(f"нет ключа книги {dotted} (оборвался на «{part}»)")
        cur = cur[key]
    return cur


def has_path(data: Mapping, dotted: str) -> bool:
    try:
        get_path(data, dotted)
        return True
    except BookError:
        return False


def set_path(data: MutableMapping, dotted: str, value: Any) -> None:
    """Замена значения по точечному пути (путь обязан существовать до последнего звена)."""
    parts = dotted.split(".")
    cur: Any = data
    for part in parts[:-1]:
        key = _key(cur, part)
        if key is None:
            raise BookError(f"нет ключа книги {dotted} (оборвался на «{part}»)")
        cur = cur[key]
    last = parts[-1]
    key = _key(cur, last)
    if isinstance(cur, MutableMapping):
        cur[last if key is None else key] = value
    elif isinstance(cur, list) and key is not None:
        cur[key] = value
    else:
        raise BookError(f"нельзя записать {dotted}")


def deep_copy(data: Any) -> Any:
    return copy.deepcopy(data)
