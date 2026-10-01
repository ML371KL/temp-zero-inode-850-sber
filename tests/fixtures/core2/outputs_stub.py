"""Заглушка выходов индикаторов с интерфейсом INTERFACES §5 (`indicators/outputs.py`).

Нужна тестам ядра части 2, только если модуля индикаторов нет: поля и типы — как в договоре.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PricePoint:
    ticker: str
    price: float
    date: str
    time: str | None
    source: str
    fetched_at: str


@dataclass(frozen=True)
class IndicatorOutputs:
    as_of: str
    prices: Mapping[str, tuple[PricePoint, ...]]
    price_history: Mapping[str, tuple[tuple[str, float], ...]]
    peer_prices: Mapping[str, PricePoint]
    curve: Mapping[str, Any] | None
    key_rate: Mapping[str, Any] | None
    register: tuple[Mapping[str, Any], ...]
    brokers: Mapping[str, Any] | None
    groups: tuple[Mapping[str, Any], ...]
    tiles: tuple[Mapping[str, Any], ...]
    ras_months: tuple[Mapping[str, Any], ...]
    form102: tuple[Mapping[str, Any], ...]
    ras_schedule: Mapping[str, Any] | None
    nowcast: Mapping[str, Any] | None
    journal: Mapping[str, Any]
    admission: Mapping[str, Any]
    retro: Mapping[str, Any]
    collector: Mapping[str, Any]
    flags: Mapping[str, Mapping[str, Any]]
