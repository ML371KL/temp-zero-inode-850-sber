# -*- coding: utf-8 -*-
"""Малые входы доказательных листов книги из листов этапа 1 ($BANK_HANDOFF_DIR) → evidence/<область>/inputs/.

Входы кладутся под теми же относительными путями, что в папке передачи (inputs/stage1/ifrs/ifrs_quarterly.csv …),
поэтому перенесённые скрипты этапа 1 меняют только корень путей. Большие листы сокращаются до строк, которые читают
скрипты области: строка остаётся, если значения её столбцов-ключей (metric, period, code, norm) встречаются как
строковые литералы в коде области (разбор ast), — столбцы с происхождением (doc, page, sha256) не трогаются.
Правильность отбора проверяется воспроизведением: скрипт области на малых входах даёт те же числа, что на полных
листах (README области). Первичка (PDF) в репозиторий не кладётся — только ссылкой и sha256 внутри листов. Ответы
брокерского API в репозиторий не кладутся вовсе — ни файлом, ни выпиской полей: дивиденды для листа β — лист
решений годовых собраний с публичным источником каждого года (страница эмитента, новость о решении).

Запуск из корня репозитория (нужна папка передачи):
  BANK_HANDOFF_DIR=<папка> python data/assumptions/evidence/extract_inputs.py [область ...] [--check]
--check — сравнить с лежащими входами, ничего не писать (выход 1 при расхождении).
Рядом с входами пишется inputs/SOURCES.json: путь, sha256 полного листа, sha256 малого входа, строк оставлено / всего.
Файл входов, которого опись больше не называет, удаляется (--check — расхождение).
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

ALL = None                                        # взять лист целиком


def by(*cols: str) -> tuple[str, ...]:
    """Отбор строк: значения этих столбцов — литералы кода области."""
    return cols


# область → (скрипты области, {относительный путь в папке передачи: отбор})
AREAS: dict[str, tuple[tuple[str, ...], dict[str, tuple[str, ...] | None]]] = {
    "nii": (("calib_nii.py", "nii_lib.py"), {
        "research/facts/cbr_key_rate_changes.csv": ALL,
        "research/facts/sber_facts.csv": by("metric"),
        "stage1/mgmt/mgmt_kpi.csv": by("metric"),
        "stage1/ifrs/ifrs_quarterly.csv": by("metric"),
        "stage1/ifrs/ifrs_history.csv": by("metric"),
        "stage1/ifrs/ifrs_all.csv": by("metric", "period"),
        "stage1/calib/nii/data/ofz_quarterly.csv": ALL,
        "stage1/calib/nii/data/zcyc_index.csv": ALL,
        "stage1/worlds/world_paths_quarterly.csv": ALL,
    }),
    "capital": (("capital_calib.py", "gap_additive.py"), {
        "stage1/mgmt/mgmt_kpi.csv": by("metric"),
        "stage1/ifrs/ifrs_history.csv": by("metric"),
        "stage1/ifrs/ifrs_quarterly.csv": by("metric"),
        "stage1/ras/cbr_f135.csv": by("norm"),
        "stage1/ras/cbr_f123.csv": by("code"),
        "stage1/ras/ras_release_monthly_wide.csv": ALL,
        "stage1/calib/capital/data/zcyc_quarter_ends.csv": ALL,
    }),
    "bridges": (("bridges.py",), {
        "stage1/ifrs/ifrs_quarterly.csv": by("metric"),
        "stage1/ifrs/ifrs_history.csv": by("metric"),
        "stage1/mgmt/mgmt_kpi.csv": by("metric"),
        "stage1/ras/ras_release_monthly_wide.csv": ALL,
    }),
    "misc": (("misc_book.py",), {
        "stage1/ifrs/ifrs_quarterly.csv": by("metric"),
    }),
    "regimes": (("regimes_calc.py",), {
        "stage1/regimes/episodes.csv": by("metric"),
    }),
    "beta": (("beta_sber.py",), {
        "stage1/market/beta/inputs/prices_SBER.csv": ALL,
        "stage1/market/beta/inputs/prices_SBERP.csv": ALL,
        "stage1/market/beta/inputs/prices_MCFTR.csv": ALL,
        "stage1/market/beta/inputs/prices_IMOEX.csv": ALL,
        "stage1/market/beta/inputs/prices_MOEXFN.csv": ALL,
        "stage1/market/shares_dividends/dividends_public.csv": ALL,
    }),
    "regulation": (("floors.py",), {
        "stage1/regulation/schedule.yaml": ALL,
    }),
    "dividends": (("dps_history.py",), {
        "stage1/market/shares_dividends/pool_check.csv": ALL,
        "stage1/market/shares_dividends/iss_description_SBER.json": ALL,
        "stage1/market/shares_dividends/iss_description_SBERP.json": ALL,
    }),
}


def literals(paths: list[Path]) -> set[str]:
    """Все строковые литералы кода (включая части f-строк)."""
    out: set[str] = set()
    for p in paths:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    return out


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# Абсолютные пути машины автора листа → относительные от корней, которые знают листы (публичный репозиторий)
_SEP = r"[\\/]"
_ABS = re.compile(r"[A-Za-z]:" + _SEP + r"[^\s'\",;|)]*?" + _SEP + r"(?=(?:primary|research|stage1|magnit-850oa)" + _SEP + ")")


def sanitize(body: bytes) -> bytes:
    """Убрать префиксы абсолютных путей (остаётся путь от корня листов этапа 1 или записи миров)."""
    text = body.decode("utf-8")
    clean = _ABS.sub("", text)
    return body if clean == text else clean.encode("utf-8")


def extract_file(src: Path, cols: tuple[str, ...] | None, lits: set[str]) -> tuple[bytes, int, int]:
    """(байты малого входа, строк оставлено, строк всего). Лист без отбора — байт в байт."""
    raw = src.read_bytes()
    if cols is None:                              # целиком; переводы строк — LF (репозиторий хранит текст в LF)
        body = raw.replace(b"\r\n", b"\n")
        n = body.count(b"\n")
        return body, n, n
    text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    rd = csv.reader(io.StringIO(text, newline=""))
    head = next(rd)
    idx = [head.index(c) for c in cols]
    rows = list(rd)
    keep = [r for r in rows if all(r[i] in lits for i in idx)]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head)
    w.writerows(keep)
    body = buf.getvalue().encode("utf-8")
    if raw.startswith(b"\xef\xbb\xbf"):
        body = b"\xef\xbb\xbf" + body
    return body, len(keep), len(rows)


def extract_area(area: str, handoff: Path) -> dict[str, bytes]:
    """{путь внутри evidence/<область>/inputs: байты} плюс SOURCES.json."""
    scripts, files = AREAS[area]
    lits = literals([HERE / area / s for s in scripts])
    out: dict[str, bytes] = {}
    sources = []
    for rel, cols in files.items():
        src = handoff / rel
        body, kept, total = extract_file(src, cols, lits)
        body = sanitize(body)
        out[rel] = body
        sources.append({"path": rel, "sha256_full": _sha(src.read_bytes()), "sha256_input": _sha(body),
                        "filter": "весь лист" if cols is None else f"строки, где {' и '.join(cols)} — литерал кода области",
                        "rows_kept": kept, "rows_total": total})
    out["SOURCES.json"] = (json.dumps({"area": area, "root": "$BANK_HANDOFF_DIR", "files": sources},
                                      ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="малые входы доказательных листов")
    ap.add_argument("areas", nargs="*", help=f"области (по умолчанию все: {', '.join(AREAS)})")
    ap.add_argument("--check", action="store_true", help="сравнить с лежащими входами")
    a = ap.parse_args(argv)
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root:
        print("нет BANK_HANDOFF_DIR — входы извлекать не из чего")
        return 1
    bad = 0
    for area in a.areas or list(AREAS):
        files = extract_area(area, Path(root))
        base = HERE / area / "inputs"
        stale = [q for q in sorted(base.rglob("*")) if q.is_file() and q.relative_to(base).as_posix() not in files]
        for q in stale:                           # вход, которого опись больше не называет (убранная выписка)
            if a.check:
                print("ЛИШНИЙ ФАЙЛ:", area, q.relative_to(base).as_posix())
                bad += 1
            else:
                q.unlink()
        for rel, body in files.items():
            dst = HERE / area / "inputs" / rel
            if a.check:
                if not dst.exists() or dst.read_bytes() != body:
                    print("РАСХОЖДЕНИЕ:", area, rel)
                    bad += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
        print(area, "—", "сверено" if a.check else "записано", len(files), "файлов")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
