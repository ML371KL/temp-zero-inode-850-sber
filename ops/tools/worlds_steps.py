# -*- coding: utf-8 -*-
"""Шаги приёма миров книги Сбербанка, которые не сводятся к числу правила: таблицы, контрольная модель, сторожа
суждений, эталон цепочки «без новостей», прогулка, журнал версии, быстрые числа (их зовёт манифест
data/assumptions/worlds_dependents.yaml; команда — ops/tools/adopt_worlds.py).

  python -B ops/tools/worlds_steps.py tables [--force]
      таблицы книги (model.book_results), если они посчитаны не на этой книге: у Сбера в `results.json` нет отпечатка
      книги, поэтому «та же книга» — машинная книга `assumptions.json`, данные `data/facts` и ядро `model/` на коммите
      таблиц (`engine_commit`) те же, что в дереве, либо отпечаток прошлого прогона этой команды (`var/adopt/tables.json`:
      sha256 машинной книги и таблиц); иначе — пересчёт и отпечаток;
  python -B ops/tools/worlds_steps.py control
      контрольная модель (`tests.test_control_model --write`); вне допуска — код 1; строки те же — файл не трогается
      (метка дня и коммита счёта остаются прежними);
  python -B ops/tools/worlds_steps.py guards --phase pre|post
      сторожа суждений на новой записи: pre — ключевая мира H в LT записи — опора суждения A-N2 (выход листа nii на
      коммите дерева; сдвинулась — код 1, суждение ведущему); post (после таблиц) — ожидание ЧПМ и CoR «что даст
      отчёт» внутри сеток `valuation.next_report`, арифметика A-O1 (рост расходов 2026 сверх зарплат мира H) к шагу
      ключа — иначе ФЛАГ;
  python -B ops/tools/worlds_steps.py pins
      эталон цепочки «без новостей» (`tests/fixtures/book/no_news_chain.json`): вероятности режимов после восьми
      отчётов, равных ожиданию модели, с полом и без (тест `test_book_core.py` сверяет с ним ядро);
  python -B ops/tools/worlds_steps.py walk
      прогулка версий от прошлой выпущенной книги (тег `book-<версия>`) к книге дерева: файл шагов — из блока `walk`
      манифеста (`var/adopt/`), печать — `walk.out` манифеста; обе проверки прогулки обязаны сказать «ДА»; при смене
      версии миров — с медианой (500 прогонов);
  python -B ops/tools/worlds_steps.py changes
      журнал версии книги `data/assumptions/V<версия>-CHANGES.md`: ключи шаблона «было → стало» против прошлой
      выпущенной книги, прогулка, заголовок таблиц (проза разделов — человеку);
  python -B ops/tools/worlds_steps.py quick --out ФАЙЛ [--draws 500]
      быстрые числа книги дерева (формат model-kit: point, median, low, high, print, worlds, runs, seed).

Строки вывода «ИТОГ: …» команда приёма кладёт в отчёт шага, «ФЛАГ: …» — в красные флаги.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "data" / "assumptions"
EV = BOOK / "evidence"
MANIFEST = BOOK / "worlds_dependents.yaml"
RESULTS = BOOK / "results.json"
SIDE = ROOT / "var" / "adopt" / "tables.json"
PINS = ROOT / "tests" / "fixtures" / "book" / "no_news_chain.json"
NII_OUT = "data/assumptions/evidence/nii/out/nii_book_out.json"
OPEX_1H26_YOY = 639.6 / 555.0      # расходы МСФО 1П26 к 1П25 (639,6 / 555,0; опора A-O1 — комментарий ключа шаблона)
PY = sys.executable


def say(line: str) -> None:
    print(line, flush=True)


def env() -> dict:
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def record() -> dict:
    return json.loads((BOOK / "worlds_source.json").read_bytes().decode("utf-8"))


def template() -> dict:
    return yaml.safe_load((BOOK / "assumptions_template.yaml").read_text(encoding="utf-8"))


def same_version() -> bool:
    v, f = os.environ.get("WORLDS_VERSION"), os.environ.get("WORLDS_FROM_VERSION")
    return bool(v) and v == f


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=check)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def R_date(iso) -> str:
    return dt.date.fromisoformat(str(iso)).strftime("%d.%m.%Y")


def _half_up(x: float, nd: int):
    """Половина вверх по десятичной записи числа (327,025 → 327,03), а не по двоичной (формат Python дал бы 327,02)."""
    from decimal import ROUND_HALF_UP, Decimal
    return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


def ru(x: float, nd: int = 2) -> str:
    return f"{_half_up(x, nd):.{nd}f}".replace(".", ",").replace("-", "−")


# ----------------------------------------------------------------------------------------------- таблицы и контроль
def tables_current() -> str | None:
    """Почему таблицы `results.json` посчитаны на книге дерева (None — не на ней)."""
    if not RESULTS.exists():
        return None
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    if str(res.get("book_version")) != str(template()["meta"]["version"]):
        return None
    book = BOOK / "assumptions.json"
    if SIDE.exists():
        side = json.loads(SIDE.read_text(encoding="utf-8"))
        if side.get("book_sha256") == sha(book) and side.get("results_sha256") == sha(RESULTS):
            return "отпечаток прошлого прогона"
    ec = res.get("engine_commit")
    if not ec:
        return None
    old = git("show", f"{ec}:data/assumptions/assumptions.json", check=False)
    if old.returncode != 0 or json.loads(old.stdout.decode("utf-8")) != json.loads(book.read_text(encoding="utf-8")):
        return None
    if git("diff", "--quiet", ec, "--", "model", "data/facts", check=False).returncode != 0:
        return None
    if git("status", "--porcelain", "--untracked-files=all", "--", "model", "data/facts").stdout.strip():
        return None
    return f"коммит таблиц {ec[:8]}"


def cmd_tables(a) -> int:
    why = None if a.force else tables_current()
    ver = str(template()["meta"]["version"])
    if why:
        say(f"ИТОГ: таблицы посчитаны на этой книге (версия {ver}; {why}) — пересчёт не нужен")
        return 0
    p = subprocess.run([PY, "-B", "-m", "model.book_results"], cwd=ROOT, env=env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        say((p.stdout + p.stderr).strip()[-2000:])
        return 1
    SIDE.parent.mkdir(parents=True, exist_ok=True)
    SIDE.write_text(dump({"book_sha256": sha(BOOK / "assumptions.json"), "results_sha256": sha(RESULTS)}),
                    encoding="utf-8")
    h = json.loads(RESULTS.read_text(encoding="utf-8"))["headline"]
    say(f"ИТОГ: таблицы книги {ver}: медиана {h['median']:.3f} (печать {h.get('printed_median')}), точка {h['point']:.2f}, "
        f"низ {h['low']:.2f}, верх {h['high']:.2f}")
    return 0


def cmd_control(a) -> int:
    path = ROOT / "data" / "checks" / "control_model.json"
    old = path.read_bytes() if path.exists() else None
    p = subprocess.run([PY, "-B", "-m", "tests.test_control_model", "--write"], cwd=ROOT, env=env(), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if not path.exists():
        say((p.stdout + p.stderr).strip()[-2000:])
        return 1
    new = json.loads(path.read_text(encoding="utf-8"))
    if p.returncode != 0 or not new.get("all_ok") or new.get("n_bad"):
        say(f"ФЛАГ: контрольная модель — вне допуска {new.get('n_bad')} строк из {new.get('n_rows')} (решение ведущему)")
        return 1
    stamp = ("as_of", "commit")
    if old is not None:
        prev = json.loads(old.decode("utf-8"))
        if {k: v for k, v in prev.items() if k not in stamp} == {k: v for k, v in new.items() if k not in stamp}:
            path.write_bytes(old)
            say(f"ИТОГ: контрольная модель — те же {new['n_rows']} строк, вне допуска 0 (файл не тронут)")
            return 0
    say(f"ИТОГ: контрольная модель — {new['n_rows']} строк, вне допуска 0 (записано)")
    return 0


# ----------------------------------------------------------------------------------------------- сторожа суждений
def cmd_guards(a) -> int:
    book = template()
    rec = record()
    ref = book["nii"]["transmission"]["reference_world"]
    rows = [r for r in rec["rows"] if r["world"] == ref]
    if a.phase == "pre":
        head = git("show", f"HEAD:{NII_OUT}", check=False)
        if head.returncode != 0:
            say(f"ФЛАГ: нет {NII_OUT} на коммите дерева — опору A-N2 сверить нечем")
            return 0
        was = json.loads(head.stdout.decode("utf-8"))["a_n2"]["key_H_LT_pct"]
        now = rows[-1]["key_avg"]
        if abs(now - was) > 1e-9:
            say(f"ОТКАЗ: ключевая мира {ref} в LT записи {now} % против опоры суждения A-N2 {was} % — цель ЧПМ сквозь "
                f"цикл (nii.nim_lt_target_mgmt) опирается на неё: суждение ведущему")
            return 1
        say(f"ИТОГ: опора A-N2 — ключевая мира {ref} в LT записи {now} % — прежняя (суждения A-N2, A-N3 — keep)")
        return 0
    flags, notes = [], []
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    e = res["next_report"]["expectation"]
    nr = book["valuation"]["next_report"]
    for k, grid in (("nim_mgmt", nr["nim_values"]), ("cor_mgmt", nr["cor_values"])):
        if not min(grid) <= e[k] <= max(grid):
            flags.append(f"ожидание модели «что даст отчёт» {k} {100 * e[k]:.2f} % вне сетки {grid} (A-R1: суждение)")
        else:
            notes.append(f"{k} {100 * e[k]:.2f} % в сетке")
    first = str(book["meta"]["first_period"])
    half = f"{first[:4]}H{1 if int(first[5]) <= 2 else 2}"
    w = next(r for r in rows if r["period"] == half)["nominal_wage_yoy"]
    x = OPEX_1H26_YOY / (1 + w / 100) - 1
    key = book["opex"]["real_growth"][first[:4]]
    if abs(round(x, 2) - key) > 1e-12:
        flags.append(f"A-O1: рост расходов 2026 сверх зарплат мира {ref} — {100 * x:.1f} % (зарплаты {w} %), к шагу 0,01 — "
                     f"{round(x, 2)}, ключ книги {key}: суждение ведущему")
    else:
        notes.append(f"A-O1 {100 * x:.1f} % → {key}")
    for f in flags:
        say(f"ФЛАГ: {f}")
    say("ИТОГ: сторожа суждений после таблиц: " + "; ".join(notes) if notes else "ИТОГ: сторожа — см. флаги")
    return 0


# ----------------------------------------------------------------------------------------------- эталон цепочки
def no_news_chain() -> dict:
    sys.path.insert(0, str(ROOT))
    from model.book import load_book, load_facts  # noqa: PLC0415
    from model.grid import run_grid  # noqa: PLC0415
    from model.live import live_from_book  # noqa: PLC0415
    from model.nextreport import model_expectation, open_period, with_observation  # noqa: PLC0415
    from model.uncertainty import trial_book  # noqa: PLC0415
    facts = load_facts()
    core = load_book(facts=facts)
    live = live_from_book(core, facts)
    share = float(core.get("joint.regime_update.floor_share"))

    def chain(floor: float) -> list[dict]:
        b, out = trial_book(core, {"joint.regime_update.floor_share": floor}), []
        for _ in range(8):
            period = open_period(b, facts)
            e = model_expectation(b, facts, period, live=live, run=run_grid(b, facts, live))
            b = with_observation(b, period, cor=e["cor_q_mgmt"], nim=e["nim_q_mgmt"])
            out.append({r: round(float(p), 4) for r, p in run_grid(b, facts, live).ctx.posterior.items()})
        return out
    return {"note": "вероятности режимов (4 знака) после 1…8 отчётов, равных ожиданию модели (тест "
                    "tests/test_book_core.py::test_the_no_news_chain_stops_at_the_floor); пишет ops/tools/worlds_steps.py pins",
            "book_version": str(core.get("meta.version")), "with_floor": chain(share), "without_floor": chain(0.0)}


def cmd_pins(a) -> int:
    new = no_news_chain()
    old = json.loads(PINS.read_text(encoding="utf-8")) if PINS.exists() else None
    if old == new:
        say("ИТОГ: цепочка «без новостей» — та же (эталон не тронут)")
        return 0
    if a.check:
        say("РАСХОЖДЕНИЕ: цепочка «без новостей» ядра ≠ эталону tests/fixtures/book/no_news_chain.json")
        return 1
    PINS.parent.mkdir(parents=True, exist_ok=True)
    PINS.write_text(dump(new), encoding="utf-8", newline="\n")
    say("ИТОГ: цепочка «без новостей» — эталон записан: кризис " + " / ".join(str(p["crisis"]) for p in new["with_floor"]))
    return 0


# ----------------------------------------------------------------------------------------------- прогулка и журнал
def released() -> list[str]:
    p = subprocess.run(["git", "-C", str(ROOT), "tag", "--list", "book-*"], capture_output=True, text=True)
    vs = [t[5:] for t in p.stdout.split() if re.fullmatch(r"book-\d+\.\d+", t)]
    return sorted(vs, key=lambda s: tuple(int(x) for x in s.split(".")))


def previous_book(ver: str) -> str:
    key = tuple(int(x) for x in ver.split("."))
    older = [v for v in released() if tuple(int(x) for x in v.split(".")) < key]
    if not older:
        raise SystemExit(f"нет выпущенной книги раньше {ver} (теги book-*)")
    return older[-1]


def walk_ctx(book: dict, ver: str, prev: str) -> dict:
    idx = json.loads((Path(os.environ["WORLDS_SOURCE_DIR"]) / "versions.json").read_text(encoding="utf-8")) \
        if os.environ.get("WORLDS_SOURCE_DIR") else None
    state = json.loads((BOOK / "worlds_state.json").read_text(encoding="utf-8"))
    entry = (idx or {}).get("versions", {}).get(state["version"]) or {}
    label = (entry.get("labels") or {}).get("walk_step") or f"миры ставок {state['version']}"
    return {"label": label[:1].upper() + label[1:], "short": (entry.get("labels") or {}).get("short", state["version"]),
            "version": state["version"], "book": ver, "prev": prev,
            "cbr_decision_ru": ".".join(reversed(entry["cbr_decision"].split("-"))) if entry.get("cbr_decision") else "—"}


def fill(text: str, ctx: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(ctx[m.group(1)]), text)


def cmd_walk(a) -> int:
    w = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["walk"]
    book = template()
    ver = str(book["meta"]["version"])
    prev = previous_book(ver)
    ctx = walk_ctx(book, ver, prev)
    tmp = ROOT / "var" / "adopt"
    tmp.mkdir(parents=True, exist_ok=True)
    steps = tmp / f"walk_steps_{prev}_to_{ver}.json"
    steps.write_text(dump([{"name": fill(s["name"], ctx), "from_end": s["from_end"]} for s in w["steps"]]),
                     encoding="utf-8", newline="\n")
    start = tmp / f"book-{prev}.yaml"
    start.write_bytes(git("show", f"book-{prev}:data/assumptions/assumptions.yaml").stdout)
    extra = [] if same_version() else ["--median", "--draws", "500"]
    out = ROOT / fill(w["out"], ctx)
    p = subprocess.run([PY, "-B", "ops/tools/walk_book.py", "--start", str(start), "--end", "data/assumptions/assumptions.yaml",
                        "--steps", str(steps), "--json", str(out.with_suffix(".json")), *extra], cwd=ROOT, env=env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out.write_text(p.stdout, encoding="utf-8", newline="\n")
    if p.returncode != 0:
        say((p.stdout + p.stderr).strip()[-1500:])
        return 1
    say(f"ИТОГ: прогулка {prev} → {ver}: {len(w['steps'])} шагов{' с медианой (500)' if extra else ''}, обе проверки «ДА» "
        f"({out.relative_to(ROOT).as_posix()})")
    return 0


def flat(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = d
    return out


def cmd_changes(a) -> int:
    book = template()
    ver = str(book["meta"]["version"])
    prev = previous_book(ver)
    old = yaml.safe_load(git("show", f"book-{prev}:data/assumptions/assumptions_template.yaml").stdout.decode("utf-8"))
    fo, fn = flat(old), flat(book)
    keys = sorted(k for k in set(fo) | set(fn) if fo.get(k) != fn.get(k))
    ctx = walk_ctx(book, ver, prev)
    walk_txt = ROOT / fill(yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["walk"]["out"], ctx)
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    oldres = json.loads(git("show", f"book-{prev}:data/assumptions/results.json").stdout.decode("utf-8"))

    def head(r: dict) -> str:
        h = r["headline"]
        return (f"медиана {ru(h['median'])} (печать {h['printed_median']:.0f}), P10–P90 {ru(h['p10'])}–{ru(h['p90'])}, "
                f"точка {ru(h['point'])}, низ / верх {ru(h['low'])} / {ru(h['high'])}")

    def cell(x):
        s = json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x
        return (s if len(s) <= 80 else s[:77] + "…").replace("|", "/")
    gates = lambda r: ", ".join(sorted(c["name"] for c in r["checks"] if c.get("fired") and c.get("kind") == "gate"))  # noqa: E731
    lines = [f"# Книга допущений Сбербанка: журнал версии {ver}", "",
             f"Книга {prev} → {ver}: {ctx['label'].lower()} — приём миров {ctx['version']} командой "
             f"`ops/tools/adopt_worlds.py` (манифест `worlds_dependents.yaml`, отчёт — `var/adopt/{ctx['version']}/report.json`). "
             f"Дата книги, цена, факты, оценки и зерно полосы — прежние; меняются миры и числа, зависящие от них, по правилам "
             f"книги (`ops/tools/worlds_rules.py`).", "",
             f"Версия записана: {dt.date.today():%d.%m.%Y}; дата книги (`meta.date`) — {R_date(book['meta']['date'])}, "
             f"кривые миров (`meta.curve_as_of`) — {R_date(book['meta']['curve_as_of'])}.", "",
             "## Заголовок (таблицы книги, 2 000 прогонов; ₽)", "",
             f"- книга {prev}: {head(oldres)}; гейты: {gates(oldres)}",
             f"- книга {ver}: {head(res)}; гейты: {gates(res)}", "",
             "## Прогулка версий", "", "```", walk_txt.read_text(encoding="utf-8").rstrip("\n") if walk_txt.exists() else "—",
             "```", "", f"## Ключи шаблона «было → стало» ({len(keys)})", "", "| ключ | было | стало |", "|---|---|---|"]
    lines += [f"| `{k}` | {cell(fo.get(k, '—'))} | {cell(fn.get(k, '—'))} |" for k in keys]
    path = BOOK / f"V{ver}-CHANGES.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    say(f"ИТОГ: журнал версии {path.name}: {len(keys)} ключей шаблона; проза разделов и объяснения гейтов — просмотреть ведущему")
    say("ФЛАГ: тексты объяснений гейтов (gate_explanations.yaml), проза текста книги и решений (DECISIONS) — числа прежней "
        "книги; массы гейтов и числа меток — правилом; сверить с таблицами")
    return 0


# ----------------------------------------------------------------------------------------------- быстрые числа
def cmd_quick(a) -> int:
    sys.path.insert(0, str(ROOT))
    from model.book import load_book, load_facts  # noqa: PLC0415
    from model.grid import live_from_book, run_grid, world_layer  # noqa: PLC0415
    from model.uncertainty import band, close_pool  # noqa: PLC0415
    facts = load_facts()
    book = load_book(facts=facts)
    run = run_grid(book, facts)
    live = live_from_book(book, facts)
    try:
        b = band(book, facts, live, draws=a.draws)
        head = b.headline(float(book.get("joint.own_macro_confidence")), dict(live.prices),
                          str(book.get("meta.company.main_ticker")))
    finally:
        close_pool()
    out = {"point": round(run.point, 3), "median": round(float(head["median"]), 3), "low": round(run.low, 3),
           "high": round(run.high, 3), "print": head.get("printed_median"),
           "worlds": {w: round(world_layer(run, w)["price"], 3) for w in book.get("worlds.ids")},
           "runs": a.draws, "seed": int(book.get("valuation.uncertainty.seed")), "book_version": str(book.get("meta.version"))}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(dump(out), encoding="utf-8")
    say(f"ИТОГ: быстрые числа ({a.draws} прогонов): медиана {out['median']}, точка {out['point']}")
    return 0


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tables")
    p.add_argument("--force", action="store_true")
    sub.add_parser("control")
    p = sub.add_parser("guards")
    p.add_argument("--phase", choices=("pre", "post"), required=True)
    p = sub.add_parser("pins")
    p.add_argument("--check", action="store_true")
    sub.add_parser("walk")
    sub.add_parser("changes")
    p = sub.add_parser("quick")
    p.add_argument("--out", required=True)
    p.add_argument("--draws", type=int, default=500)
    a = ap.parse_args(argv)
    return globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    sys.exit(main())
