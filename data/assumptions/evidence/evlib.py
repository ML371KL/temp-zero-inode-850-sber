# -*- coding: utf-8 -*-
"""Общие помощники доказательных листов книги: книга-шаблон, запись JSON, sha256. Только stdlib и PyYAML."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

EVIDENCE = Path(__file__).resolve().parent
BOOK_DIR = EVIDENCE.parent                                   # data/assumptions
TEMPLATE = BOOK_DIR / "assumptions_template.yaml"
RECORD = BOOK_DIR / "worlds_source.json"


def template() -> dict:
    """Шаблон книги (значения книги 1.0 без миров)."""
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def record() -> dict:
    """Запись миров семейства (байтовая копия)."""
    return json.loads(RECORD.read_bytes().decode("utf-8"))


def get(d: dict, dotted: str):
    """Значение по точечному пути."""
    for part in dotted.split("."):
        d = d[part]
    return d


def dumps(obj) -> str:
    """Детерминированный JSON выхода листа (UTF-8, отступ 1, LF в конце)."""
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj), encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
