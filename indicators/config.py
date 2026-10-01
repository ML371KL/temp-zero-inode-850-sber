"""Конфигурация индикаторов: эмитент из книги, строки сборщиков, факты, каталог состояния.

Индикаторы не импортируют ядро (INTERFACES §5): `meta.company` книги читается
здесь напрямую через PyYAML. Всё, что называет эмитента (имя, тикеры, регномер,
строки поиска новостей, шаблоны адресов документов), приходит из книги и
`data/indicators/sources.yaml`; в коде пакета литералов эмитента нет.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
BOOK_PATH = ROOT / "data" / "assumptions" / "assumptions.yaml"
# Пока машинной книги нет, `meta.company` берётся из черновика книги 1.0:
# блок эмитента в них один и тот же, а сборщики нужны раньше книги.
BOOK_DRAFT_PATH = ROOT / "data" / "assumptions" / "assumptions.draft.yaml"
FACTS_DIR = ROOT / "data" / "facts"
SOURCES_PATH = ROOT / "data" / "indicators" / "sources.yaml"
SEED_DIR = ROOT / "data" / "indicators" / "seed"

COMPANY_FIELDS = ("name", "tickers", "main_ticker", "cbr_regnum", "fiscal_year_end")
# Переменные окружения пакета — без префикса банка (INTERFACES §8, W1/C5): копия под
# другой банк меняет только книгу, факты и sources.yaml. Токен T-Invest — `tinvest.TOKEN_ENV`.
STATE_ENV = "BANK_STATE_DIR"
HANDOFF_ENV = "BANK_HANDOFF_DIR"


class ConfigError(ValueError):
    """Нет книги, блока эмитента или файла конфигурации."""


def state_dir() -> Path:
    """Каталог состояния: `$BANK_STATE_DIR` или `var/state` репозитория (INTERFACES §7.1)."""
    value = os.environ.get(STATE_ENV)
    return Path(value) if value else ROOT / "var" / "state"


def _read_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def book_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return BOOK_PATH if BOOK_PATH.exists() else BOOK_DRAFT_PATH


def company(path: Path | None = None) -> dict[str, Any]:
    """`meta.company` книги: {name, tickers, main_ticker, cbr_regnum, fiscal_year_end}."""
    p = book_path(path)
    if not p.exists():
        raise ConfigError(f"нет книги {p.name}: эмитент для сборщиков неизвестен")
    data = _read_yaml(p) or {}
    block = (data.get("meta") or {}).get("company")
    if not isinstance(block, dict):
        raise ConfigError(f"{p.name}: нет блока meta.company")
    missing = [k for k in COMPANY_FIELDS if block.get(k) in (None, "", [])]
    if missing:
        raise ConfigError(f"{p.name}: в meta.company нет {', '.join(missing)}")
    out = dict(block)
    out["tickers"] = [str(t) for t in block["tickers"]]
    out["main_ticker"] = str(block["main_ticker"])
    out["cbr_regnum"] = str(block["cbr_regnum"])
    if out["main_ticker"] not in out["tickers"]:
        raise ConfigError("meta.company.main_ticker не входит в tickers")
    return out


def book_value(dotted: str, path: Path | None = None) -> Any:
    """Значение книги по точечному пути (YAML без импорта ядра); нет книги или ключа — None."""
    p = book_path(path)
    if not p.exists():
        return None
    node: Any = _read_yaml(p) or {}
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@lru_cache(maxsize=4)
def _sources_cached(path: str) -> dict[str, Any]:
    data = _read_yaml(Path(path))
    if not isinstance(data, dict):
        raise ConfigError(f"{Path(path).name}: ожидался словарь")
    return data


def sources(path: Path | None = None) -> dict[str, Any]:
    """`data/indicators/sources.yaml` — строки эмитента для сборщиков."""
    p = Path(path or SOURCES_PATH)
    if not p.exists():
        raise ConfigError(f"нет файла {p.name}")
    return _sources_cached(str(p))


def fill(template: str, **values: Any) -> str:
    """Подстановка `{ticker}`, `{regnum}`, `{year}` … в строку из sources.yaml."""
    return str(template).format(**values)


def facts_file(name: str, facts_dir: Path | None = None) -> dict | None:
    """Файл фактов `data/facts/<name>.json` или None, если его ещё нет."""
    p = Path(facts_dir or FACTS_DIR) / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def peer_tickers(facts_dir: Path | None = None) -> list[str]:
    """Тикеры банков-аналогов из `facts/peers.json` (нет файла — пусто)."""
    data = facts_file("peers", facts_dir) or {}
    return [str(b["ticker"]) for b in data.get("banks") or [] if b.get("ticker")]


def bridge_ras_ifrs(facts_dir: Path | None = None) -> dict | None:
    """Сезонный мост прибыли РСБУ → МСФО (`facts/bridge_ras_ifrs.json`) или None."""
    return facts_file("bridge_ras_ifrs", facts_dir)


def calendar_events(facts_dir: Path | None = None) -> list[dict]:
    """События `facts/calendar.json` (нет файла — пусто)."""
    data = facts_file("calendar", facts_dir) or {}
    return list(data.get("events") or [])


def load_seed(name: str, seed_dir: Path | None = None) -> dict | None:
    """Файл истории `data/indicators/seed/<name>.json` или None."""
    p = Path(seed_dir or SEED_DIR) / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
