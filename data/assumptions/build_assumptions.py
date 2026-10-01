# -*- coding: utf-8 -*-
"""Сборка машинной книги: шаблон + миры семейства + банковская надстройка → assumptions.yaml и assumptions.json.

Шаблон `assumptions_template.yaml` — все ключи MODEL прил. A, кроме `worlds.<W>.*` и `worlds_bank.*`. Сборка:
  * вписывает блоки `worlds.N|H|M` из байтовой копии записи миров `worlds_source.json` (MODEL §3.1: поле / 100;
    узел `zero_curve.LT` — N и H по таблице семейства, M — правилом `curve_lt_rule` рецепта; `lt_inflation` —
    блок записи); sha256 записи обязан совпасть с `worlds.source.sha256` шаблона;
  * вписывает блок `worlds_bank` из надстройки `worlds_bank.json` и её sha256 в `worlds.overlay.sha256`;
  * пишет `assumptions.yaml` (комментарии шаблона сохраняются) и `assumptions.json` (тот же объект).
Запуск из корня репозитория:
  python data/assumptions/build_assumptions.py            # пишет файлы
  python data/assumptions/build_assumptions.py --check    # сборка воспроизводит файлы (выход 1 — расхождение)
Только stdlib и PyYAML.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "assumptions_template.yaml"
RECORD = HERE / "worlds_source.json"
OVERLAY = HERE / "worlds_bank.json"
OUT_YAML = HERE / "assumptions.yaml"
OUT_JSON = HERE / "assumptions.json"

MARK_WORLDS = "  #{{WORLDS}}"
MARK_WORLDS_BANK = "#{{WORLDS_BANK}}"
MARK_OVERLAY_SHA = "null  #{{OVERLAY_SHA256}}"

# Поле книги ← поле записи (MODEL §3.1; всё — % годовых в записи, доли в книге)
SERIES = (
    ("key_rate", "key_avg"),
    ("cpi", "cpi_yoy_avg"),
    ("wage_growth", "nominal_wage_yoy"),
    ("ofz_1y", "ofz_1y"),
    ("ofz_3y", "ofz_3y"),
    ("ofz_5y", "ofz_5y"),
    ("ofz_10y", "ofz_10y"),
    ("real_key", "real_key_fwd12m"),
)
# Имена миров и уровень кривой в терминале — таблица сборки семейства (850oa, Лента): N и H — суждение записи;
# у M при рыночной инфляции (m_inflation.source = forward_bei) уровень — правило рецепта curve_lt_rule:
# ОФЗ 10 лет последнего полугодия мира M, округление до 0,1 п.п.
WORLD_TABLE = {
    "N": {"name": "Нормализация", "curve_lt": 0.090},
    "H": {"name": "Высокие ставки надолго", "curve_lt": 0.127},
    "M": {"name": "Рыночный как есть", "curve_lt": 0.167},
}
OVERLAY_GROUPS = (("credit_growth", ("corporate", "mortgage", "retail_other")),
                  ("funds_growth", ("retail", "corporate")))


class BuildError(ValueError):
    """Входы сборки не согласованы (хэш, периоды, формат надстройки)."""


def _pct(v: float) -> float:
    return round(float(v) / 100.0, 5)


def _flow(d: dict) -> str:
    """Словарь в строку потока YAML с кавычками у ключей-периодов и голыми LT/LT_from."""
    parts = []
    for k, v in d.items():
        key = k if k in ("LT", "LT_from") else json.dumps(str(k))
        parts.append(f"{key}: {v!r}")
    return "{" + ", ".join(parts) + "}"


def _halves(first_period: str, last_period: str) -> list[str]:
    """Полугодия сетки от полугодия первого квартала до полугодия последнего."""
    y, q = int(first_period[:4]), int(first_period[5])
    y1, q1 = int(last_period[:4]), int(last_period[5])
    h, h1 = (1 if q <= 2 else 2), (1 if q1 <= 2 else 2)
    out = []
    while (y, h) <= (y1, h1):
        out.append(f"{y}H{h}")
        y, h = (y, 2) if h == 1 else (y + 1, 1)
    return out


def curve_lt(record: dict, world: str) -> float:
    """Узел LT бескупонной кривой мира (MODEL §3.1)."""
    if world == "M" and (record.get("m_inflation") or {}).get("source") == "forward_bei":
        last = [r for r in record["rows"] if r["world"] == "M"][-1]
        return round(round(last["ofz_10y"], 1) / 100.0, 5)
    return WORLD_TABLE[world]["curve_lt"]


def world_blocks(record: dict, meta: dict, ids: list[str]) -> dict:
    """worlds.<W> машинной книги из записи (доли)."""
    need = _halves(meta["first_period"], meta["last_period"])
    out = {}
    for w in ids:
        rows = {r["period"]: r for r in record["rows"] if r["world"] == w}
        missing = [p for p in need if p not in rows]
        if missing:
            raise BuildError(f"в записи миров у мира {w} нет полугодий {missing}")
        blk: dict = {"name": WORLD_TABLE[w]["name"]}
        for book_key, rec_key in SERIES:
            blk[book_key] = {p: _pct(rows[p][rec_key]) for p in need}
        fair = record["fair_zero_curve_today"][w]
        zc = {str(k): _pct(v) for k, v in sorted(fair.items(), key=lambda kv: float(kv[0]))}
        zc["LT"] = curve_lt(record, w)
        blk["zero_curve"] = zc
        blk["lt_inflation"] = _pct(record["lt_inflation"][w])
        out[w] = blk
    return out


def overlay_blocks(overlay: dict, ids: list[str], meta: dict) -> dict:
    """worlds_bank.<W> из надстройки: проверка формата (MODEL прил. B)."""
    if overlay.get("schema") != "worlds_bank-v1":
        raise BuildError("worlds_bank.json: schema ≠ worlds_bank-v1")
    anchor_year = str(meta["anchor_period"][:4])
    out = {}
    for w in ids:
        src = overlay["worlds"].get(w)
        if src is None:
            raise BuildError(f"worlds_bank.json: нет мира {w}")
        blk: dict = {}
        for grp, keys in OVERLAY_GROUPS:
            blk[grp] = {}
            for k in keys:
                path = src[grp][k]
                if anchor_year not in path or any(v is None for v in path.values()):
                    raise BuildError(f"worlds_bank.json: {w}.{grp}.{k} без года якоря или с null")
                blk[grp][k] = {str(y): float(v) for y, v in path.items()}
        blk["nominal_gdp_growth"] = {str(y): float(v) for y, v in src["nominal_gdp_growth"].items()}
        out[w] = blk
    return out


def _render_worlds(blocks: dict, record_sha: str, origin: str) -> str:
    """Блоки миров текстом. Комментарий у ключа мира — источник суждения для осей на путях мира (выпуск печатает его
    как есть): словами, с именем и версией книги-источника записи."""
    lines = [f"  # ↓ миры семейства: сборка build_assumptions.py из worlds_source.json (sha256 {record_sha[:8]}…); "
             "поле записи / 100, полугодия; руками не править"]
    for w, b in blocks.items():
        lines.append(f"  {w}:                                 # [Ф: общая запись сценариев ставок семейства моделей — {origin}]")
        lines.append(f"    name: {json.dumps(b['name'], ensure_ascii=False)}")
        for key, _ in SERIES:
            lines.append(f"    {key}: {_flow(b[key])}")
        lines.append(f"    zero_curve: {_flow(b['zero_curve'])}")
        lines.append(f"    lt_inflation: {b['lt_inflation']!r}")
    return "\n".join(lines)


def _render_overlay(blocks: dict, overlay_sha: str) -> str:
    lines = [f"worlds_bank:                         # банковская надстройка миров: сборка из worlds_bank.json (sha256 "
             f"{overlay_sha[:8]}…; рецепт — evidence/worlds_bank/); доли, годы; руками не править"]
    for w, b in blocks.items():
        lines.append(f"  {w}:")
        for grp, keys in OVERLAY_GROUPS:
            inner = ", ".join(f"{k}: {_flow(b[grp][k])}" for k in keys)
            lines.append(f"    {grp}: {{{inner}}}")
        lines.append(f"    nominal_gdp_growth: {_flow(b['nominal_gdp_growth'])}")
    return "\n".join(lines)


def render(template: Path | None = None) -> tuple[str, str, dict]:
    """(текст assumptions.yaml, текст assumptions.json, объект книги)."""
    tpath = template or TEMPLATE
    text = tpath.read_text(encoding="utf-8")
    base = yaml.safe_load(text)
    for mark in (MARK_WORLDS, MARK_WORLDS_BANK, MARK_OVERLAY_SHA):
        if text.count(mark) != 1:
            raise BuildError(f"в шаблоне метка {mark!r} встречается {text.count(mark)} раз (нужна одна)")
    rec_bytes = RECORD.read_bytes()
    rec_sha = hashlib.sha256(rec_bytes).hexdigest()
    if rec_sha != base["worlds"]["source"]["sha256"]:
        raise BuildError(f"sha256 worlds_source.json {rec_sha} ≠ worlds.source.sha256 шаблона")
    record = json.loads(rec_bytes.decode("utf-8"))
    if record.get("asof") != base["worlds"]["source"]["record_asof"]:
        raise BuildError("asof записи миров ≠ worlds.source.record_asof")
    ov_bytes = OVERLAY.read_bytes()
    ov_sha = hashlib.sha256(ov_bytes).hexdigest()
    overlay = json.loads(ov_bytes.decode("utf-8"))
    ids = list(base["worlds"]["ids"])
    worlds = world_blocks(record, base["meta"], ids)
    bank = overlay_blocks(overlay, ids, base["meta"])
    out = (text.replace(MARK_WORLDS, _render_worlds(worlds, rec_sha, str(base["worlds"]["source"]["origin"])))
               .replace(MARK_WORLDS_BANK, _render_overlay(bank, ov_sha))
               .replace(MARK_OVERLAY_SHA, f'"{ov_sha}"  #'))
    out = ("# МАШИННАЯ КНИГА — собрана build_assumptions.py из assumptions_template.yaml, worlds_source.json и "
           "worlds_bank.json; руками не править.\n" + out)
    book = yaml.safe_load(out)
    if book["worlds"]["overlay"]["sha256"] != ov_sha:
        raise BuildError("хэш надстройки не вписался в worlds.overlay.sha256")
    js = json.dumps(book, ensure_ascii=False, indent=1) + "\n"
    if json.loads(js) != book:
        raise BuildError("машинная книга не переводится в JSON без потерь (ключи не строки или даты без кавычек)")
    return out, js, book


def build(template: Path | None = None) -> dict:
    """Машинная книга (объект) из шаблона, записи миров и надстройки; файлов не пишет."""
    return render(template)[2]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="сборка машинной книги")
    ap.add_argument("--check", action="store_true", help="сверить assumptions.yaml и .json со сборкой")
    a = ap.parse_args(argv)
    try:
        y, j, book = render()
    except BuildError as e:
        print("ОТКАЗ сборки книги:", e)
        return 1
    if a.check:
        bad = [p.name for p, t in ((OUT_YAML, y), (OUT_JSON, j))
               if not p.exists() or p.read_text(encoding="utf-8") != t]
        if bad:
            print("НЕ воспроизводятся сборкой:", ", ".join(bad))
            return 1
        print("assumptions.yaml и assumptions.json воспроизводятся; версия", book["meta"]["version"])
        return 0
    OUT_YAML.write_text(y, encoding="utf-8", newline="\n")
    OUT_JSON.write_text(j, encoding="utf-8", newline="\n")
    print("записано: assumptions.yaml", len(y), "знаков; assumptions.json", len(j), "знаков; версия", book["meta"]["version"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
