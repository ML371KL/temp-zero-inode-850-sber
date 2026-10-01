"""CLI сборки выпуска `sber-v1` в `$BANK_STATE_DIR/release/` (INTERFACES §6).

    python -m model.build_release                  полный выпуск на живых входах → release/latest.json
    python -m model.build_release --book-only      на цене, дате и реестре книги (без индикаторов)
    python -m model.build_release --fast           быстрая сборка (meta.fast: true) → release/latest-fast.json
    python -m model.build_release --check [--fast] [--book-only]   собрать в памяти и проверить, ничего не писать
    python -m model.build_release --check ФАЙЛ     проверить готовый файл выпуска

Коды выхода: 0 — собран, в том числе с плашками (истекающее объяснение гейта, истёкший срок
документа эмитента — флаги `explanation_expiring`, `policy_expired`); 1 — не собран (контракт,
инвариант, сработавший гейт без действующего объяснения: нет записи, срок истёк или масса вне
коридора ожидания; защита заголовка; нет цены главного тикера) — прежний выпуск остаётся; 3 —
собран, но тревога (деградация живых входов — `live.degraded_flag`; флаг `dividend_register` —
нет записи реестра за год к концу квартала ГОСА; размер ≥ 400 000 байт). С `--check` тревоги
печатаются, код 0. Годный полный выпуск на живых входах обновляет последнюю принятую цену
(`$BANK_STATE_DIR/live.json`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from model.live import LiveError, state_dir, write_last_accepted
from model import payload as P

__all__ = ["main", "write_release"]


def _compact(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_release(payload, path: Path) -> None:
    """Компактный JSON атомарно (через временный файл)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_compact(payload), encoding="utf-8", newline="\n")
    tmp.replace(path)


def _check_file(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ПРОВАЛ: файл {path.name} не прочитан: {exc}")
        return 1
    problems = P.validate(payload)
    for p in problems:
        print(f"  контракт: {p}")
    fast = " (быстрая сборка — к публикации не годна)" if (payload.get("meta") or {}).get("fast") else ""
    print("ПРОВАЛ: выпуск не годен" if problems else f"годен: {path.name}, {payload['meta']['bytes']} байт{fast}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m model.build_release", description="сборка выпуска sber-v1")
    ap.add_argument("--book-only", action="store_true", help="цена, дата и реестр книги, без индикаторов")
    ap.add_argument("--fast", action="store_true", help="быстрая сборка: мало прогонов, без поисков; не публикуется")
    ap.add_argument("--check", nargs="?", const="", default=None, metavar="ФАЙЛ",
                    help="проверить: без файла — собрать в памяти; с файлом — проверить готовый выпуск")
    args = ap.parse_args(argv)
    if args.check:
        return _check_file(Path(args.check))
    started = time.monotonic()
    root = state_dir()
    try:
        release = P.make_release(live=not args.book_only, fast=args.fast)
        payload = P.build_payload(release)
    except LiveError as exc:
        print(f"ПРОВАЛ: {exc}")
        if args.check is None:
            (root / "release").mkdir(parents=True, exist_ok=True)
            (root / "release" / "build_report.json").write_text(json.dumps(
                {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": 1,
                 "fast": args.fast, "book_only": args.book_only, "problems": [str(exc)], "alerts": [],
                 "target": None}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return 1
    problems = P.validate(payload)
    alerts = P.alerts(release, payload)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fast": args.fast, "book_only": args.book_only, "bytes": payload["meta"]["bytes"],
              "payload_sha256": payload["meta"]["payload_sha256"], "problems": problems, "alerts": alerts,
              "valuation_date": payload["meta"]["valuation_date"],
              "printed_median": payload["fair_value"]["headline"]["printed_median"],
              "duration_s": round(time.monotonic() - started, 1)}
    for p in problems:
        print(f"  проблема: {p}")
    for a in alerts:
        print(f"  тревога: {a}")
    for flag in payload["checks"]["flags"]:
        if flag.get("raised") and flag.get("name") in ("explanation_expiring", "policy_expired"):
            print(f"  плашка: {flag.get('title')} — {flag.get('detail')}")
    h = payload["fair_value"]["headline"]
    print(f"медиана {h['median']} ₽ (печать {h['printed_median']}), полоса 80 % {h['band80']}, "
          f"точка {payload['fair_value']['central']}, {payload['meta']['bytes']} байт")
    if args.check is not None:
        report["code"] = 1 if problems else 0
        print("ПРОВАЛ: выпуск не годен" if problems else "годен (проверка в памяти, ничего не записано)")
        return report["code"]
    rel_dir = root / "release"
    target = rel_dir / ("latest-fast.json" if args.fast else "latest.json")
    code = 1 if problems else (3 if alerts else 0)
    if not problems:
        write_release(payload, target)
        again = P.validate(json.loads(target.read_text(encoding="utf-8")))
        if again:
            code = 1
            report["problems"] = again
            print("ПРОВАЛ: записанный файл не прошёл повторный validate")
        elif not args.book_only and not args.fast:
            write_last_accepted(release.live_report, root)
    report["code"] = code
    report["target"] = target.name if not problems else None
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8",
                                               newline="\n")
    print({0: "готово: выпуск собран", 3: "готово с тревогой (код 3)", 1: "ПРОВАЛ: выпуск не собран, прежний остаётся"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
