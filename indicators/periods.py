"""Периоды индикаторов: месяцы `2026M09`, кварталы `2026Q3`, календарные даты.

Свой модуль, чтобы индикаторы не импортировали ядро (INTERFACES §5). Литералов
годов нет: всё выводится из переданных дат и строк периодов.
"""

from __future__ import annotations

import calendar as _cal
import re
from datetime import date, timedelta

_MONTH = re.compile(r"^(\d{4})M(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")


class PeriodError(ValueError):
    """Строка не разбирается как период."""


def parse_month(p: str) -> tuple[int, int]:
    """«2026M09» → (год, месяц)."""
    m = _MONTH.match(str(p))
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise PeriodError(f"не месяц: {p!r}")
    return int(m.group(1)), int(m.group(2))


def parse_quarter(p: str) -> tuple[int, int]:
    """«2026Q3» → (год, номер квартала)."""
    m = _QUARTER.match(str(p))
    if not m:
        raise PeriodError(f"не квартал: {p!r}")
    return int(m.group(1)), int(m.group(2))


def month(year: int, m: int) -> str:
    return f"{year:04d}M{m:02d}"


def quarter(year: int, q: int) -> str:
    return f"{year:04d}Q{q}"


def month_of(d: date) -> str:
    return month(d.year, d.month)


def quarter_of(d: date) -> str:
    return quarter(d.year, (d.month - 1) // 3 + 1)


def quarter_of_month(p: str) -> str:
    y, m = parse_month(p)
    return quarter(y, (m - 1) // 3 + 1)


def quarter_months(p: str) -> list[str]:
    """Месяцы квартала по порядку."""
    y, q = parse_quarter(p)
    return [month(y, 3 * (q - 1) + k) for k in (1, 2, 3)]


def shift_month(p: str, n: int) -> str:
    y, m = parse_month(p)
    k = y * 12 + (m - 1) + n
    return month(k // 12, k % 12 + 1)


def shift_quarter(p: str, n: int) -> str:
    y, q = parse_quarter(p)
    k = y * 4 + (q - 1) + n
    return quarter(k // 4, k % 4 + 1)


def year_ago(p: str) -> str:
    """Тот же месяц или квартал годом раньше."""
    return shift_month(p, -12) if _MONTH.match(p) else shift_quarter(p, -4)


def month_start(p: str) -> date:
    y, m = parse_month(p)
    return date(y, m, 1)


def month_end(p: str) -> date:
    y, m = parse_month(p)
    return date(y, m, _cal.monthrange(y, m)[1])


def quarter_start(p: str) -> date:
    return month_start(quarter_months(p)[0])


def quarter_end(p: str) -> date:
    return month_end(quarter_months(p)[-1])


def quarter_days(p: str) -> int:
    return (quarter_end(p) - quarter_start(p)).days + 1


def months_in_year_to(p: str) -> list[str]:
    """Месяцы года от января до `p` включительно (нарастающий итог)."""
    y, m = parse_month(p)
    return [month(y, k) for k in range(1, m + 1)]


def form_month(form_date: str | date) -> str:
    """Дата формы ЦБ (на 1-е число) → месяц данных: 2026-09-01 → «2026M08»."""
    d = form_date if isinstance(form_date, date) else date.fromisoformat(str(form_date)[:10])
    return month_of(d - timedelta(days=1))


def form_date_of(p: str) -> str:
    """Месяц данных → дата формы ЦБ на 1-е число следующего месяца."""
    return month_start(shift_month(p, 1)).isoformat()


def is_month(p: str) -> bool:
    return bool(_MONTH.match(str(p)))


def is_quarter(p: str) -> bool:
    return bool(_QUARTER.match(str(p)))


def from_stage1_month(s: str) -> str:
    """«2026-08» (листы этапа 1) → «2026M08»."""
    y, m = str(s).split("-")[:2]
    return month(int(y), int(m))


def from_stage1_quarter(s: str) -> str:
    """«1Q2023» или «2023Q1» → «2023Q1»."""
    s = str(s)
    m = re.match(r"^([1-4])Q(\d{4})$", s)
    if m:
        return quarter(int(m.group(2)), int(m.group(1)))
    parse_quarter(s)
    return s


def iso_utc(moment) -> str:
    """Момент ISO UTC с секундами."""
    return moment.isoformat(timespec="seconds")
