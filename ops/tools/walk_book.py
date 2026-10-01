"""Прогулка версий книги: правки по одной, накопительно, ядром (М§16; перенос 850oa под книгу Сбера).

От стартовой книги шаги применяются по очереди, и после каждого печатается точка
при значениях книги (низ / точка при λ / верх, ₽), её сдвиг, V0 и P/B слоя «свой
взгляд», V0 слоя «рыночные ставки как есть», ROE и k сквозь цикл; с `--median` —
ещё печатаемая медиана полосы A-V9 и её сдвиг, полосы 80 % и 50 % и P(центр <
рынка). В конце — две проверки: последний шаг = прямой расчёт конечной книги и
книга после шагов = конечная книга целиком. Прогулка, где хоть одна «НЕТ», не
объясняет переход между версиями — код 1.

Как запускать (из корня репозитория):
  python -B ops/tools/walk_book.py --start СТАРАЯ.yaml --end НОВАЯ.yaml --steps ШАГИ.yaml
      [--facts КАТАЛОГ] [--median [--draws N]] [--json ФАЙЛ]

СТАРАЯ и НОВАЯ — машинные книги (`assumptions.yaml` или `.json` версии, например
`git show book-1.0:data/assumptions/assumptions.yaml > var/book-1.0.yaml`); факты —
одни на обе (`data/facts` или `--facts`): прогулка отделяет суждения книги от фактов.
Живые входы — цена, дата и реестр книги (`model.grid.live_from_book`).

Файл шагов (YAML или JSON) — список шагов:
  - name: "Режимы: вероятности сводки §5.1"
    from_end: [regimes.prior]                    # значения — из конечной книги
  - name: "β_E = 0,83 (проба)"
    set: {valuation.beta_e: 0.83}                # значения — явно
Путь, которого нет в конечной книге (`from_end`), или значение `null` (`set`)
убирают ключ; недостающие блоки создаются. Книга после каждого шага проверяется
закрытой схемой и выводом anchor-ключей (`model.book.book_from_dict`); не
читается — код 2 с номером шага.

Коды возврата: 0 — прогулка сходится; 1 — хоть одна проверка «НЕТ»;
2 — негодные входы (файл, шаг, книга после шага, ядро).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.book import BookError, FactsError, book_from_dict, load_facts, normalize  # noqa: E402
from model.grid import live_from_book, run_grid  # noqa: E402

OK, NOT_CONSISTENT, BAD_INPUT = 0, 1, 2
# Прямой расчёт и последний шаг — один код на одной книге: допуск только на
# порядок сложения, если шаги собрали словарь в ином порядке ключей.
REL = 1e-12
_MISSING = object()


class Refused(Exception):
    """Негодные входы — код 2."""


def _get(A: dict, dotted: str):
    node = A
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _put(A: dict, dotted: str, value) -> None:
    node, keys = A, dotted.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    if value is _MISSING:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = copy.deepcopy(value)


def read_book(path: Path) -> dict:
    """Машинная книга как словарь (до вывода anchor-ключей)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text) if Path(path).suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise Refused(f"книга не читается ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise Refused(f"книга ({path}): ожидается словарь")
    return normalize(data)


def read_steps(path: Path) -> list[dict]:
    try:
        steps = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f"шаги не читаются ({path}): {exc}") from exc
    if not isinstance(steps, list) or not steps:
        raise Refused(f"шаги ({path}): ожидается непустой список")
    for i, step in enumerate(steps, 1):
        if (not isinstance(step, dict) or not isinstance(step.get("name"), str)
                or set(step) - {"name", "set", "from_end"}
                or not (step.get("set") or step.get("from_end"))):
            raise Refused(f"шаг {i}: нужны name и хотя бы одно из set {{путь: значение}} / from_end [пути]")
        if not isinstance(step.get("set", {}), dict) or not isinstance(step.get("from_end", []), list):
            raise Refused(f"шаг {i} «{step['name']}»: set — словарь, from_end — список путей")
    return steps


def _median(book, facts, draws: int | None) -> dict:
    """Печатаемая медиана и полосы полосы A-V9 на цене книги (ядро части 2)."""
    from model.uncertainty import band  # noqa: PLC0415 — нужна только с --median

    live = live_from_book(book, facts)
    lam = float(book.get("joint.own_macro_confidence"))
    main = str(book.get("meta.company.main_ticker"))
    head = band(book, facts, live, draws=draws).headline(lam, dict(live.prices), main)
    try:
        return dict(median=float(head["median"]), p10=float(head["band80"][0]), p90=float(head["band80"][1]),
                    p25=float(head["band50"][0]), p75=float(head["band50"][1]),
                    p_below=float(head["p_below_market"]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise Refused(f"заголовок полосы без поля ({exc}): сверить с PAYLOAD §2 fair_value.headline") from exc


def point(data: dict, facts, *, median: bool = False, draws: int | None = None) -> dict:
    """Точка и слои ядром на цене книги; с `median` — медиана и полосы A-V9."""
    book = book_from_dict(data, facts=facts)
    run = run_grid(book, facts)
    own, market = run.layers["analytical"], run.layers["macro_neutral"]
    out = dict(low=run.low, point=run.point, high=run.high, v0_own=own.v0, v0_market=market.v0,
               pb_own=own.pb, roe_tc=own.roe_tc, k_tc=own.k_tc)
    if median:
        out.update(_median(book, facts, draws))
    return out


def walk(start: dict, end: dict, steps: list[dict], facts, *, median: bool = False,
         draws: int | None = None) -> dict:
    """Строки прогулки, прямой расчёт конечной книги и обе проверки."""
    A = copy.deepcopy(start)
    try:
        rows = [dict(step="старт", **point(A, facts, median=median, draws=draws))]
    except (BookError, FactsError) as exc:
        raise Refused(f"стартовая книга не читается ядром: {exc}") from exc
    for i, step in enumerate(steps, 1):
        for dotted in step.get("from_end", []):
            _put(A, dotted, _get(end, dotted))
        for dotted, value in (step.get("set") or {}).items():
            _put(A, dotted, _MISSING if value is None else value)
        try:
            rows.append(dict(step=step["name"], **point(A, facts, median=median, draws=draws)))
        except (BookError, FactsError) as exc:
            raise Refused(f"шаг {i} «{step['name']}»: книга не читается — {exc}") from exc
    try:
        direct = point(copy.deepcopy(end), facts, median=median, draws=draws)
    except (BookError, FactsError) as exc:
        raise Refused(f"конечная книга не читается ядром: {exc}") from exc
    consistent = all(abs(rows[-1][k] - v) <= REL * max(1.0, abs(v)) for k, v in direct.items())
    return dict(rows=rows, direct=direct, consistent=consistent, same_book=normalize(A) == normalize(end))


def render(res: dict) -> str:
    median = "median" in res["direct"]
    head = f"{'шаг':<56}{'низ':>9}{'точка':>9}{'верх':>9}{'Δточка':>9}"
    head += f"{'медиана':>9}{'Δмед.':>8}{'P10–P90':>16}{'P25–P75':>16}{'P<рын.':>8}" if median else ""
    lines = [head + f"{'V0 свой':>10}{'V0 рын.':>10}{'P/B':>7}{'ROE_T':>8}{'k_T':>8}"]
    prev = None
    for r in res["rows"]:
        line = (f"{r['step'][:55]:<56}{r['low']:>9.1f}{r['point']:>9.1f}{r['high']:>9.1f}"
                + ("" if prev is None else f"{r['point'] - prev['point']:+9.1f}").rjust(9))
        if median:
            line += f"{r['median']:>9.1f}" + ("" if prev is None else f"{r['median'] - prev['median']:+8.1f}").rjust(8)
            line += (f"{r['p10']:>7.1f}–{r['p90']:<7.1f}".rjust(16) + f"{r['p25']:>7.1f}–{r['p75']:<7.1f}".rjust(16)
                     + f"{100 * r['p_below']:>7.1f}%")
        lines.append(line + f"{r['v0_own']:>10.1f}{r['v0_market']:>10.1f}{r['pb_own']:>7.2f}"
                     f"{100 * r['roe_tc']:>7.2f}%{100 * r['k_tc']:>7.2f}%")
        prev = r
    d = res["direct"]
    lines += ["", f"последний шаг = прямой расчёт конечной книги: {'ДА' if res['consistent'] else 'НЕТ'} "
                  f"(прямой: {d['low']:.1f} / {d['point']:.1f} / {d['high']:.1f}"
                  + (f", медиана {d['median']:.1f}" if median else "") + ")",
              f"книга после шагов = конечная книга целиком: {'ДА' if res['same_book'] else 'НЕТ'}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="стартовая машинная книга (assumptions.yaml версии)")
    ap.add_argument("--end", required=True, help="конечная машинная книга")
    ap.add_argument("--steps", required=True, help="файл шагов (YAML или JSON)")
    ap.add_argument("--facts", help="каталог фактов (по умолчанию data/facts)")
    ap.add_argument("--median", action="store_true", help="ещё печатаемая медиана полосы A-V9 на каждом шаге")
    ap.add_argument("--draws", type=int, default=None, help="прогонов полосы для --median (по умолчанию — книги)")
    ap.add_argument("--json", help="записать строки и проверки в JSON")
    args = ap.parse_args(argv)
    try:
        facts = load_facts(Path(args.facts) if args.facts else None)
        start, end = read_book(Path(args.start)), read_book(Path(args.end))
        res = walk(start, end, read_steps(Path(args.steps)), facts, median=args.median, draws=args.draws)
    except (OSError, BookError, FactsError, Refused) as exc:
        print(f"НЕГОДНЫЕ ВХОДЫ: {exc}", file=sys.stderr)
        return BAD_INPUT
    print(render(res))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return OK if res["consistent"] and res["same_book"] else NOT_CONSISTENT


if __name__ == "__main__":
    sys.exit(main())
