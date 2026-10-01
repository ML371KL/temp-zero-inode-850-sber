"""Помощник `ops/run.sh`: решения такта, которые шеллу не прочесть самому.

    python ops/tact.py window              дозору есть что делать? 0 — да, 1 — тихий выход
    python ops/tact.py news                что принял дозор: 0 — есть новое (собрать выпуск), 1 — нет
    python ops/tact.py done                новое опубликовано: снять отложенное
    python ops/tact.py retry               повторять ли сбор: 0 — отказал невосполнимый источник
                                           (отчёт сборщиков: retry), 1 — повтор ничего не даст
    python ops/tact.py simulate <источник> <режим>
                                           проба тревоги: 0 — источник невосполнимый и из режима,
                                           64 — нет (проба с чужим источником — не тихий ноль)

ОТЛОЖЕННОЕ (`$BANK_STATE_DIR/release_watch.pending`) — принятые дозором месяцы РСБУ
и новые кандидаты МСФО, выпуск на которых ещё не опубликован. Отчёт сборщиков
(`collector_report.json`) называет месяц «новым» только в том такте, где его
приняли: упади сборка — следующий такт дозора его уже не увидел бы. Поэтому
новое копится здесь и снимается только после публикации (`done`); кандидаты МСФО,
уже виденные дозором, помнит `release_watch.seen` — «новым» кандидат бывает один раз.

ПОВТОР СБОРА (`retry`, docs/INTERFACES.md §6): сборщики ставят в отчёте `retry: true`,
когда отказал или деградировал невосполнимый источник и повторный сбор может добрать
его день. Утренний такт повторяет сбор только в этом случае; источник без токена,
деградация восполнимого источника и тревога реестра дивидендов повтора не дают. Нет
отчёта или он не читается — повтора нет: решать не по чему.

`window` — тихая проверка таймера дозора (каждые 10 минут в окне релизов по
будням): в окне релиза по календарю индикаторов или при отложенном — работа,
иначе выход без единой строки. Сбой проверки — «работа» (решит сам сборщик):
лишняя строка в журнале дешевле пропущенного релиза.

Только stdlib; слой индикаторов импортируется лениво и только в `window` и `simulate`.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT = "collector_report.json"
PENDING = "release_watch.pending"
SEEN = "release_watch.seen"
USAGE = 64


def state_dir() -> Path:
    value = os.environ.get("BANK_STATE_DIR")
    return Path(value) if value else ROOT / "var" / "state"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def pending(state: Path) -> dict:
    raw = _read(state / PENDING, {})
    raw = raw if isinstance(raw, dict) else {}
    return {"ras": sorted(set(raw.get("ras") or [])), "ifrs": sorted(set(raw.get("ifrs") or []))}


def in_release_window(today: date) -> bool:
    """Окно релиза РСБУ или МСФО по календарю индикаторов (тот же расчёт, что у сборщика)."""
    from indicators import calendar as cal  # noqa: PLC0415
    from indicators import config  # noqa: PLC0415
    from indicators.store import Store  # noqa: PLC0415

    schedule = config.sources().get("schedule") or {}
    return bool(cal.is_release_day(today, store=Store(state_dir()), schedule=schedule,
                                   facts_events=config.calendar_events()))


def cmd_window(today: date | None = None) -> int:
    state = state_dir()
    got = pending(state)
    if got["ras"] or got["ifrs"]:
        return 0
    try:
        return 0 if in_release_window(today or date.today()) else 1
    except Exception:  # noqa: BLE001 — решит сборщик
        return 0


def cmd_news() -> int:
    """Новое из отчёта дозора — в отложенное; 0 и строка, если отложенное не пусто."""
    state = state_dir()
    report = _read(state / REPORT, None)
    if not isinstance(report, dict):
        print("ДОЗОР: нет отчёта сборщиков collector_report.json — нечего разбирать", file=sys.stderr)
        return 2
    watch = report.get("release_watch") or {}
    accepted = [str(m) for m in (watch.get("accepted") or [])]
    candidates = [str(c) for c in (report.get("ifrs_candidates") or [])]
    seen = _read(state / SEEN, {})
    seen_ifrs = set(seen.get("ifrs") or []) if isinstance(seen, dict) else set()
    fresh_ifrs = [c for c in candidates if c not in seen_ifrs]
    got = pending(state)
    got["ras"] = sorted(set(got["ras"]) | set(accepted))
    got["ifrs"] = sorted(set(got["ifrs"]) | set(fresh_ifrs))
    _write(state / PENDING, got)
    _write(state / SEEN, {"ifrs": sorted(seen_ifrs | set(candidates))})
    if not (got["ras"] or got["ifrs"]):
        print("  дозор нового не принял — выпуск не нужен")
        return 1
    parts = []
    if got["ras"]:
        parts.append("РСБУ " + ", ".join(got["ras"]))
    if got["ifrs"]:
        parts.append("МСФО " + ", ".join(got["ifrs"]))
    print("  принято и ещё не опубликовано: " + "; ".join(parts))
    return 0


def cmd_done() -> int:
    state = state_dir()
    got = pending(state)
    (state / PENDING).unlink(missing_ok=True)
    if got["ras"] or got["ifrs"]:
        print("  отложенное дозора опубликовано: " + ", ".join(got["ras"] + got["ifrs"]))
    return 0


def cmd_retry() -> int:
    """0 — отчёт сборщиков просит повтор (невосполнимый источник не собран); иначе 1."""
    report = _read(state_dir() / REPORT, None)
    if not isinstance(report, dict) or report.get("retry") is not True:
        return 1
    names = sorted(d.get("name", "?") for d in report.get("details") or []
                   if isinstance(d, dict) and d.get("retry"))
    print("  повтор сбора может добрать: " + (", ".join(names) or "невосполнимый источник"))
    return 0


def cmd_simulate(name: str, mode: str) -> int:
    from indicators import sources  # noqa: PLC0415

    names = sources.MODES.get(mode, ())
    if name not in names or name not in sources.IRRECOVERABLE:
        allowed = sorted(n for n in names if n in sources.IRRECOVERABLE)
        print(f"ПРОБА ТРЕВОГИ: источник «{name}» не невосполнимый источник режима {mode} "
              f"(подходят: {', '.join(allowed) or 'нет'})", file=sys.stderr)
        return USAGE
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["window"]:
        return cmd_window()
    if args == ["news"]:
        return cmd_news()
    if args == ["done"]:
        return cmd_done()
    if args == ["retry"]:
        return cmd_retry()
    if len(args) == 3 and args[0] == "simulate":
        return cmd_simulate(args[1], args[2])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return USAGE


if __name__ == "__main__":
    sys.exit(main())
