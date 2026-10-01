# -*- coding: utf-8 -*-
"""Банковская надстройка миров: черновик этапа 1 → data/assumptions/worlds_bank.json (формат — MODEL прил. B).

Правило (MODEL §3.1, решение ведущего к книге 1.0):
  * середины диапазонов черновика → доли (0,01 = 1 %);
  * credit_growth.corporate = «организации», .mortgage = ИЖК, .retail_other = «прочая розница»;
  * funds_growth.retail = funds_growth.corporate = М2 (раздельных рядов средств ФЛ и ЮЛ у ЦБ нет);
  * nominal_gdp_growth — справочно (прокси (1 + реальный ВВП)·(1 + средний ИПЦ) − 1);
  * годы — от года якоря до последнего года прогноза ОНДКП (2026–2029); после него ядро сводит рост объёмов
    к g_T мира к volumes.lt_from (MODEL §4.3) — второго долгосрочного роста в файле нет (правило черновика
    «к номинальному ВВП при 2 % реально» в книгу не входит).

Вход — копия черновика этапа 1 `inputs/bank_overlay_draft.yaml` (абсолютные пути заменены относительными,
остальное — как в черновике); первичка — ссылкой и sha256 в самом черновике (meta.sources).
Запуск из корня репозитория:
  python data/assumptions/evidence/worlds_bank/build_overlay.py            # пишет worlds_bank.json
  python data/assumptions/evidence/worlds_bank/build_overlay.py --check    # сверка с файлом (выход 1 — расхождение)
  python data/assumptions/evidence/worlds_bank/build_overlay.py --sync     # обновить копию черновика из $BANK_HANDOFF_DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BOOK_DIR = HERE.parents[1]                       # data/assumptions
DRAFT = HERE / "inputs" / "bank_overlay_draft.yaml"
RECORD = BOOK_DIR / "worlds_source.json"
OUT = BOOK_DIR / "worlds_bank.json"
HANDOFF_DRAFT = ("stage1", "worlds", "bank_overlay_draft.yaml")   # относительно $BANK_HANDOFF_DIR

WORLDS = ("N", "H", "M")
FIELDS = {                                       # поле надстройки → поле черновика
    ("credit_growth", "corporate"): "orgs",
    ("credit_growth", "mortgage"): "mortgage",
    ("credit_growth", "retail_other"): "other_retail",
    ("funds_growth", "retail"): "m2",
    ("funds_growth", "corporate"): "m2",
    ("nominal_gdp_growth", None): "nominal_gdp_proxy",
}
AS_OF = "2026-09-30"                             # титул ОНДКП: «Проект от 30 сентября 2026 года»

RECIPE = (
    "N — середины диапазонов базового сценария ОНДКП 2027–2029 (табл. 3.2, PDF-с. 73 / печ. 71), кроме «прочей "
    "розницы» 2026–2027 — прогноз ЦБ по балансам («Банковский сектор. II кв. 2026», PDF-с. 20 / печ. 19, sha256 "
    "ed4826f8…) и 2028–2029 — [Р] (население − w·ИЖК)/(1 − w), w = 0,5806 (доля ИЖК в задолженности ФЛ на "
    "01.08.2026, dataservice ЦБ). H и M — реальный рост базы при инфляции своего мира: g_W = (1 + g_N)·(1 + π_W)/"
    "(1 + π_N) − 1, π — средний ИПЦ мира за год по записи миров семейства (worlds_source.json, sha256 7296567d…); "
    "2026 — одинаково во всех мирах. credit_growth.corporate — «организации», mortgage — ИЖК, retail_other — "
    "«прочая розница»; funds_growth.retail = funds_growth.corporate = М2 (раздельных рядов нет); "
    "nominal_gdp_growth — справочно. Середины диапазонов → доли. Годы 2026–2029; после 2029 ядро сводит рост "
    "объёмов к g_T мира к volumes.lt_from (MODEL §4.3). Сборщик — "
    "data/assumptions/evidence/worlds_bank/build_overlay.py; вход — черновик надстройки этапа 1 "
    "(evidence/worlds_bank/inputs/bank_overlay_draft.yaml)."
)

# Абсолютные пути черновика этапа 1 → относительные (публичный репозиторий: путей пользователя нет)
_RECORD_PATH = re.compile(r"'[^'\n]*?/magnit-850oa/data/assumptions/(worlds_source\.json)'")
_PRIMARY_PATH = re.compile(r"'[^'\n]*?/(primary/[^'/\n]+)'")


def sanitize(text: str) -> str:
    """Черновик этапа 1 → копия для репозитория: только замена абсолютных путей относительными."""
    text = _RECORD_PATH.sub(r"'\1'", text)
    return _PRIMARY_PATH.sub(r"'\1'", text)


def _mid_share(rng) -> float:
    """[низ, верх] в % → середина в долях, 5 знаков (0,001 п.п.)."""
    lo, hi = float(rng[0]), float(rng[1])
    return round((lo + hi) / 2 / 100.0, 5)


def world_cpi(record: dict) -> dict:
    """Средний ИПЦ мира за год, % — среднее двух полугодий cpi_yoy_avg записи (как в черновике)."""
    out = {}
    for w in WORLDS:
        rows = {r["period"]: r["cpi_yoy_avg"] for r in record["rows"] if r["world"] == w}
        years = sorted({int(p[:4]) for p in rows})
        out[w] = {y: round((rows[f"{y}H1"] + rows[f"{y}H2"]) / 2, 3) for y in years if f"{y}H1" in rows}
    return out


def verify_draft(draft: dict, record_bytes: bytes) -> list[str]:
    """Самопроверка черновика: запись миров та же; H и M — пересчёт N по инфляции мира (±0,011 п.п. — округление)."""
    bad = []
    sha = hashlib.sha256(record_bytes).hexdigest()
    if draft["meta"]["worlds_record"]["sha256"] != sha:
        bad.append(f"черновик собран на другой записи миров: {draft['meta']['worlds_record']['sha256']} ≠ {sha}")
    cpi = world_cpi(json.loads(record_bytes.decode("utf-8")))
    years_n = draft["worlds"]["N"]["years"]
    for w in ("H", "M"):
        for y, row in draft["worlds"][w]["years"].items():
            f = 1.0 if int(y) == min(int(k) for k in years_n) else (1 + cpi[w][int(y)] / 100) / (1 + cpi["N"][int(y)] / 100)
            for fld in ("orgs", "mortgage", "other_retail", "m2", "nominal_gdp_proxy"):
                for i in (0, 1):
                    exp = ((1 + years_n[y][fld][i] / 100) * f - 1) * 100
                    if abs(exp - row[fld][i]) > 0.011:
                        bad.append(f"{w} {y} {fld}[{i}]: {row[fld][i]} против пересчёта {exp:.3f}")
    return bad


def build_overlay(draft: dict, record_bytes: bytes) -> dict:
    """Объект worlds_bank.json из черновика надстройки."""
    bad = verify_draft(draft, record_bytes)
    if bad:
        raise ValueError("черновик надстройки не проходит самопроверку:\n  " + "\n  ".join(bad))
    src = draft["meta"]["sources"]["ondkp"]
    worlds = {}
    for w in WORLDS:
        years = draft["worlds"][w]["years"]
        block: dict = {"credit_growth": {}, "funds_growth": {}, "nominal_gdp_growth": {}}
        for (grp, key), fld in FIELDS.items():
            path = {str(y): _mid_share(years[y][fld]) for y in sorted(years, key=int)}
            if key is None:
                block[grp] = path
            else:
                block[grp][key] = path
        worlds[w] = block
    return {
        "schema": "worlds_bank-v1",
        "source": {"doc": src["file"], "url": src["url"], "sha256": src["sha256"], "as_of": AS_OF},
        "recipe": RECIPE,
        "worlds": worlds,
    }


def render(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def load_draft(path: Path = DRAFT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_text() -> str:
    return render(build_overlay(load_draft(), RECORD.read_bytes()))


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="сверить worlds_bank.json со сборкой")
    ap.add_argument("--sync", action="store_true", help="обновить копию черновика из $BANK_HANDOFF_DIR")
    a = ap.parse_args(argv)
    if a.sync:
        root = os.environ.get("BANK_HANDOFF_DIR")
        if not root:
            print("нет BANK_HANDOFF_DIR — копию черновика обновить не из чего")
            return 1
        text = Path(root, *HANDOFF_DRAFT).read_text(encoding="utf-8")
        DRAFT.parent.mkdir(parents=True, exist_ok=True)
        DRAFT.write_text(sanitize(text), encoding="utf-8", newline="\n")
        print("копия черновика обновлена:", DRAFT.relative_to(BOOK_DIR))
    text = build_text()
    if a.check:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("worlds_bank.json", "воспроизводится" if ok else "НЕ воспроизводится сборкой")
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("записано:", OUT.relative_to(BOOK_DIR), "sha256", hashlib.sha256(OUT.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
