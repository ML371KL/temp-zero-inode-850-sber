"""CLI такта индикаторов: `python -m indicators.collect <режим>` (INTERFACES §6).

Режимы: `collect` (утро), `daily-indicators` (вечер), `release-watch` (дни релизов),
`nowcast` (нау-каст и запись журнала), `status`, `health`, `record-ras`,
`record-actual`, `record-consensus`, `record-guidance`, `record-dividend`.

У режимов сбора — `--only <сборщик>[,…]` и `--skip <сборщик>[,…]`: такт запускает сборщик
T-Invest отдельной командой (только её процесс получает токен, INTERFACES §8), остальное — второй.
`--only` запускает одни названные сборщики; дозор релиза, проверка реестра дивидендов и ротация
архива идут в запуске без `--only`. Запуски одного такта пишут один отчёт.

Утро и вечер проверяют реестр дивидендов (`indicators/register.py`): годной записи за прошлый
год к концу квартала ГОСА нет — строка «ТРЕВОГА реестра» и код 3.

Коды выхода (политика тревог — `indicators/sources.py`): 0 — ок, в том числе деградация
восполнимого источника моложе трёх тактов; 1 — провал критического источника или команды;
3 — тревога: отказал невосполнимый источник, тревога реестра или деградация восполнимого
источника третий такт подряд. Итог такта — `$BANK_STATE_DIR/collector_report.json` (статусы,
причины, счётчик `streak`, признак повтора `retry`); строки журнала — по-русски, по одной на событие.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from datetime import date, datetime, timezone
from typing import Any

from indicators import calendar as cal
from indicators import config, nowcast, periods, record, release_watch, retro, seed, sources, tinvest
from indicators import register as reg
from indicators.http import TRUSTED_CA
from indicators.journal import BENCHMARK_TITLES, TARGETS, Journal, JournalError
from indicators.store import STATE_CEILING_BYTES, Store

REPORT_FILE = "collector_report.json"
TOKEN_COLLECTOR = "tinvest"      # единственный сборщик, которому нужен секрет окружения
EXTRA_KEYS = ("release_watch", "ifrs_candidates")       # части отчёта, которые пишет запуск с дозором
SEED_MARK = "seed_imported.json"
# Цель журнала → пункт гайденса года (ключи фактов `guidance.json` и `record-guidance`).
GUIDANCE_OF_TARGET = {"nim_q": "nim", "cor_q": "cor"}
GUIDANCE_WARN_DAYS = 14          # за столько дней до записи T-90 напомнить, что гайденса года нет


def _say(line: str) -> None:
    print(line, flush=True)


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def context(store: Store, today: date, *, getter=None) -> sources.Context:
    return sources.Context(store=store, today=today, company=config.company(), cfg=config.sources(),
                           getter=getter, peer_tickers=tuple(config.peer_tickers()))


def ensure_seed(store: Store) -> bool:
    """История из `data/indicators/seed/` в ряды — при первом запуске и при смене файлов истории."""
    digest = hashlib.sha256()
    for name in seed.SEED_FILES:
        p = config.SEED_DIR / f"{name}.json"
        if p.exists():
            digest.update(p.read_bytes())
    mark = store.read_state(SEED_MARK) or {}
    if mark.get("sha256") == digest.hexdigest():
        return False
    seed.import_to_store(store)
    store.write_state(SEED_MARK, {"sha256": digest.hexdigest(),
                                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return True


# Режимы, у которых такт один на день: повтор сбора в тот же день счётчик деградации не двигает.
ONE_TACT_A_DAY = frozenset({"collect", "daily-indicators"})


def write_report(store: Store, mode: str, today: date, results: list[sources.Result], verdict: sources.Verdict,
                 extra: dict | None = None) -> dict:
    """Отчёт такта. Запуски одного такта (тот же режим и день: `--only`, `--skip`, повтор сбора)
    сливаются по источникам: у каждого источника — итог его последнего запуска."""
    prev = store.read_state(REPORT_FILE) or {}
    same = prev.get("mode") == mode and prev.get("today") == today.isoformat()
    statuses = dict(prev.get("sources") or {})
    details = {d["name"]: d for d in (prev.get("details") or [])} if same else {}
    for r in results:
        statuses[r.name] = r.status
        details[r.name] = {**r.as_dict(), "code": verdict.codes.get(r.name, 0),
                           "retry": bool(r.name in sources.IRRECOVERABLE and (
                               r.status == sources.FAILED or (r.status == sources.DEGRADED and r.retry)))}
    degraded = []
    for d in details.values():
        if d["status"] != sources.OK:
            degraded.append(f"{d['name']}: {d['detail'] or '; '.join(d['reasons']) or d['status']}")
        else:
            degraded.extend(f"{d['name']}: {x}" for x in d["reasons"])
    codes = [d.get("code", 0) for d in details.values()]
    kept = {k: prev[k] for k in EXTRA_KEYS if same and k in prev}
    counted = set(prev.get("streak_counted") or []) if same else set()
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode,
              "today": today.isoformat(), "code": 1 if 1 in codes else (3 if 3 in codes else 0),
              "retry": any(d.get("retry") for d in details.values()),
              "sources": statuses, "streak": dict(verdict.streak),
              "streak_counted": sorted(counted | {r.name for r in results if r.status != sources.OK}),
              "degraded": degraded, "details": list(details.values()), **kept, **(extra or {})}
    store.write_state(REPORT_FILE, report)
    return report


def _print_results(results: list[sources.Result]) -> None:
    for r in results:
        tail = r.detail or "; ".join(r.reasons)
        _say(f"{r.name}: {r.status}" + (f" — {tail}" if tail else "") + (f" (рядов: {len(set(r.series))})" if r.series else ""))


# ------------------------------------------------------------------ такты

def selected(mode: str, only: str | None, skip: str | None) -> tuple[str, ...]:
    """Сборщики режима с учётом `--only` и `--skip` (имена `sources.COLLECTORS` через запятую)."""
    def names(text: str | None, flag: str) -> set[str]:
        got = {x.strip() for x in (text or "").split(",") if x.strip()}
        unknown = sorted(got - set(sources.COLLECTORS))
        if unknown:
            raise config.ConfigError(f"{flag}: нет сборщика {', '.join(unknown)}; есть: "
                                     f"{', '.join(sorted(sources.COLLECTORS))}")
        return got

    keep, drop = names(only, "--only"), names(skip, "--skip")
    return tuple(n for n in sources.MODES[mode] if (not keep or n in keep) and n not in drop)


def run_mode(mode: str, store: Store, today: date, *, getter=None, force: bool = False,
             only: str | None = None, skip: str | None = None) -> int:
    names = selected(mode, only, skip)
    ctx = context(store, today, getter=getter)
    ensure_seed(store)
    extra: dict[str, Any] = {}
    if mode == "release-watch":
        sched = ctx.cfg.get("schedule") or {}
        if not force and not cal.is_release_day(today, store=store, schedule=sched,
                                                facts_events=config.calendar_events()):
            _say("release-watch: сегодня не окно релиза — пропуск")
            return 0
    # Сборщик T-Invest — первым, и токен после него из окружения процесса убирается: ленты,
    # страницы и PDF (чужой ввод) разбираются без секрета (INTERFACES §8, В10).
    first = tuple(n for n in names if n == TOKEN_COLLECTOR)
    results = sources.run(first, ctx)
    tinvest.forget_token()
    results += sources.run(tuple(n for n in names if n != TOKEN_COLLECTOR), ctx)
    whole = not only            # `--only` — одни названные сборщики; шаги такта идут в запуске без него
    if whole and mode in ("collect", "release-watch"):
        rw = release_watch.run(ctx)
        results.append(rw)
        accepted = [m["month"] for m in rw.data.get("months", []) if m.get("month_accepted")]
        extra["release_watch"] = {"accepted": accepted, "months": rw.data.get("months", [])}
        for m in accepted:
            _say(f"принят месяц РСБУ {m}")
        cands = store.read_state("sber_pdf/candidates.json") or {}
        extra["ifrs_candidates"] = sorted(k for k, v in cands.items()
                                          if v.get("kind") in ("ifrs_statements", "ifrs_press"))
    if whole and mode in ("collect", "daily-indicators"):
        results.append(register_check(ctx))
    if whole and mode == "collect":
        removed = store.prune_raw(today=today)
        if removed:
            _say(f"ротация сырого архива: {len(removed)} дн.")
    prev = store.read_state(REPORT_FILE) or {}
    same = prev.get("mode") == mode and prev.get("today") == today.isoformat()
    counted = set(prev.get("streak_counted") or []) if same and mode in ONE_TACT_A_DAY else set()
    verdict = sources.verdict(results, prev.get("streak") or {}, counted=counted)
    report = write_report(store, mode, today, results, verdict, extra)
    _print_results(results)
    for line in verdict.lines:
        _say(line)
    if report["retry"]:
        _say("невосполнимый источник отказал: повтор сбора может добрать день")
    _say(f"готово: {mode}, код {verdict.code}")
    return verdict.code


def register_check(ctx: sources.Context) -> sources.Result:
    """Реестр дивидендов: годная запись за прошлый год к концу квартала ГОСА — иначе тревога (М§14.3)."""
    res = sources.Result(name="register")
    rows = reg.build(ctx.store, ticker=ctx.company["main_ticker"], today=ctx.today, cfg=ctx.cfg)
    quarter = config.book_value("dividends.calendar.agm_quarter")
    text = reg.alarm(rows, today=ctx.today, agm_quarter=quarter if isinstance(quarter, int) else None)
    res.data["records"] = [{k: r.get(k) for k in reg.KEEP} for r in rows]
    for r in rows:
        for note in r.get("notes") or []:
            res.degrade(f"реестр {r['year']}: {note}")
    if text:
        res.degrade(text)
        _say("ТРЕВОГА реестра: " + text)
    return res


# ------------------------------------------------------------------ нау-каст

def open_period(store: Store, today: date) -> str:
    """Квартал нау-каста: следующий за последним кварталом с фактом МСФО (не позже текущего)."""
    s = store.load("actual.ni_q")
    hist = {} if s is None else s.history(today.isoformat())
    last = max(hist) if hist else periods.shift_quarter(periods.quarter_of(today), -2)
    return min(periods.shift_quarter(last, 1), periods.quarter_of(today))


def load_expectation(period: str) -> tuple[dict | None, dict[str, Any], str]:
    """Ожидание модели через ядро (единственный ленивый импорт ядра, INTERFACES §5) и его происхождение:
    {book_version, book_digest, facts_digest} — книга и факты, на которых ожидание посчитано."""
    try:
        from model.book import load_book, load_facts  # noqa: PLC0415
        from model.nextreport import model_expectation  # noqa: PLC0415

        facts = load_facts()
        book = load_book(facts=facts)
        exp = dict(model_expectation(book, facts, period))
        origin: dict[str, Any] = {"book_version": None, "book_digest": getattr(book, "digest", None),
                                  "facts_digest": getattr(facts, "digest", None)}
        try:
            origin["book_version"] = str(book.get("meta.version"))
        except Exception:  # noqa: BLE001
            pass
        return exp, origin, ""
    except Exception as exc:  # noqa: BLE001 — ядро ещё не готово или книга не собрана
        return None, {}, f"ожидание модели недоступно: {type(exc).__name__}: {exc}"


def release_identity(store: Store) -> tuple[str, dict]:
    """sha12 последнего собранного выпуска на момент записи и его книга. Нау-каст идёт до сборки:
    это прошлый выпуск, на свежем состоянии — нули; книга самого ожидания — в `inputs` записи."""
    path = store.root / "release" / "latest.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        sha = str(meta.get("payload_sha256") or "")[:12]
        if len(sha) == 12:
            return sha, {"book_version": meta.get("book_version"), "generated_at": meta.get("generated_at")}
    except (OSError, ValueError):
        pass
    return "0" * 12, {"book_version": None, "generated_at": None}


def guidance_point(store: Store, item: str, year: int, as_of: str) -> float | None:
    """Гайденс года точкой, известный на дату. Ручная запись (`record-guidance`) важнее фактов.

    Факты — `guidance.json`: узел `items.<пункт>` вида `point` (момент — `date`) и его пересмотры
    `history`; последнее известное на дату. Диапазон и граница (`cor_max` — «меньше 1,4 %») —
    не точка: эталоном не служат.
    """
    s = store.load(f"guidance.{item}")
    held = None if s is None else s.value_as_of(str(int(year)), as_of)
    if held is not None:
        return held
    facts = config.facts_file("guidance") or {}
    node = (facts.get("items") or {}).get(item)
    if facts.get("year") != int(year) or not isinstance(node, dict) or node.get("kind") != "point":
        return None
    known = [(str(node.get("date") or ""), node.get("v"))]
    known += [(str(h.get("date") or ""), (h.get("value") or {}).get("v")) for h in facts.get("history") or []
              if h.get("key") == item]
    known = sorted((k for k in known if k[0] and k[0] <= as_of), key=lambda k: k[0])
    value = known[-1][1] if known else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def target_benchmarks(store: Store, period: str, today: date, ras: dict, expectation: dict | None) -> dict:
    """Эталоны целей на сегодня: прибыль — ретро-набор + мост РСБУ + консенсус; ЧПМ и CoR — наивные и гайденс."""
    as_of = today.isoformat()
    h = nowcast.History.load(store)
    ni = retro.benchmarks_at(h, store, period, as_of)
    bridged = ras["targets"]["ni_q"].get("ras_bridged")
    if bridged is not None:
        ni["ras_bridge"] = bridged
    cons = store.load("consensus.ni_q")
    if cons is not None and cons.value_as_of(period, as_of) is not None:
        ni["consensus"] = cons.value_as_of(period, as_of)
    out = {"ni_q": ni}
    year = periods.parse_quarter(period)[0]
    for key, item in GUIDANCE_OF_TARGET.items():
        marks: dict[str, float] = {}
        s = store.load(f"actual.{key}")
        hist = {} if s is None else s.history(as_of)
        before = [q for q in hist if q < period]
        if before:
            marks["prev_quarter"] = hist[max(before)]
        ly = hist.get(periods.shift_quarter(period, -4))
        if ly is not None:
            marks["same_quarter_last_year"] = ly
        g = guidance_point(store, item, year, as_of)
        if g is not None:
            marks["guidance"] = g
        out[key] = marks
    if expectation:
        for key in TARGETS:
            v = nowcast._expectation(expectation, key)
            if v is not None:
                out[key]["model"] = v
    return out


def load_nim_bridge() -> tuple[Any, str]:
    """Перевод ЧПМ базиса МСФО группы в упр. — функция моста ядра (INTERFACES §0 п. 6, §5; ленивый импорт)."""
    try:
        from model.book import load_facts  # noqa: PLC0415
        from model.credit import bridge_from_facts  # noqa: PLC0415

        return bridge_from_facts(load_facts()).to_mgmt_nim, ""
    except Exception as exc:  # noqa: BLE001 — ядро или факты моста ещё не готовы
        return None, f"мост ЧПМ ядра недоступен: {type(exc).__name__}: {exc}"


def _frozen(journal: Journal, target: str, period: str, keys: tuple[str, ...]) -> dict[str, Any]:
    got = {k: journal.frozen(target, period, k) for k in keys}
    return {k: v for k, v in got.items() if v is not None}


def run_nowcast(store: Store, today: date, *, journal: Journal | None = None, expectation: dict | None = None,
                book_version: str | None = None, to_mgmt_nim: Any = None) -> int:
    """Нау-каст открытого квартала (T1, T3, T4), запись журнала на горизонтах, `nowcast.json`.

    `expectation` и `to_mgmt_nim` (мост ЧПМ упр. ↔ движок) по умолчанию — из ядра, лениво.
    """
    ensure_seed(store)
    cfg = config.sources()
    facts_events = config.calendar_events()
    journal = journal or Journal(store.root / "journal.sqlite")
    period = open_period(store, today)
    notes: list[str] = []
    origin: dict[str, Any] = {"book_version": book_version}
    if expectation is None:
        expectation, origin, note = load_expectation(period)
        book_version = origin.get("book_version")
        if note:
            notes.append(note)
    bridge = config.bridge_ras_ifrs() or {}
    bridge_note = ""
    if to_mgmt_nim is None:
        to_mgmt_nim, bridge_note = load_nim_bridge()
    frozen: dict[str, Any] = _frozen(journal, "ni_q", period, ("bridge", "profile", "sigmas"))
    frozen["nim"] = _frozen(journal, "nim_q", period, ("bridge", "profile", "sigmas", "basis"))
    frozen["cor"] = _frozen(journal, "cor_q", period, ("bridge", "sigmas"))
    sigmas = {**retro.sigma_table(store, today=today),
              **retro.ratio_sigma_table(store, bridge=bridge, to_mgmt_nim=to_mgmt_nim, today=today)}
    ras = nowcast.ras_inputs(store, period=period, today=today, bridge=bridge, sigmas=sigmas, frozen=frozen,
                             to_mgmt_nim=to_mgmt_nim)
    if ras["targets"]["nim_q"]["equation"] == nowcast.NO_MGMT and bridge_note:
        notes.append(bridge_note)
    marks = target_benchmarks(store, period, today, ras, expectation)
    by_target = nowcast.combine(ras, expectation or {},
                                {k: {n: {"name": BENCHMARK_TITLES.get(n, n), "value": v} for n, v in m.items()}
                                 for k, m in marks.items()})
    report = cal.ifrs_report(period, store=store, schedule=cfg.get("schedule") or {}, facts_events=facts_events)
    horizon = nowcast.horizon_for(today, date.fromisoformat(report["date"]))
    sha, rel = release_identity(store)
    # Эталон «гайденс» замораживается с записью T-90: без гайденса года запись ляжет без него навсегда.
    days = (date.fromisoformat(report["date"]) - today).days
    soon = nowcast.HORIZON_START["T-90"] < days <= nowcast.HORIZON_START["T-90"] + GUIDANCE_WARN_DAYS
    writes = horizon == "T-90" and journal.forecast("nim_q", period, horizon) is None
    if (soon or writes) and "guidance" not in marks["nim_q"]:
        notes.append(f"нет гайденса года по ЧПМ для {period}: запись T-90 "
                     + ("ложится" if writes else f"через {days - nowcast.HORIZON_START['T-90']} дн. ляжет")
                     + " без эталона «гайденс» — обновить факты `guidance.json` или внести `record-guidance --item nim`")
    recorded = []
    if horizon and expectation:
        # Мосты, профили (значения месяцев год назад) и σ квартала замораживаются при первой записи.
        t = ras["targets"]["ni_q"]
        if t.get("bridge") is not None:
            journal.freeze("ni_q", period, "bridge", t["bridge"])
        journal.freeze("ni_q", period, "profile", {**(frozen.get("profile") or {}), **(ras.get("profile") or {})})
        journal.freeze("ni_q", period, "sigmas", ras.get("sigmas") or {})
        tn = ras["targets"]["nim_q"]
        if tn.get("bridge") is not None:
            journal.freeze("nim_q", period, "bridge", tn["bridge"])
            journal.freeze("nim_q", period, "basis", tn["bridge_basis"])
            journal.freeze("nim_q", period, "profile", {**(frozen["nim"].get("profile") or {}),
                                                         **(tn.get("profile") or {})})
            journal.freeze("nim_q", period, "sigmas", sigmas.get("nim") or {})
        tc = ras["targets"]["cor_q"]
        if tc.get("bridge") is not None:
            journal.freeze("cor_q", period, "bridge", tc["bridge"])
            journal.freeze("cor_q", period, "sigmas", sigmas.get("cor") or {})
    if horizon:
        for key in TARGETS:
            row = by_target[key]
            if row["forecast"] is None or "model" not in marks[key]:
                notes.append(f"{key}: запись {horizon} пропущена — нет прогноза или ожидания модели")
                continue
            t = ras["targets"][key]
            try:
                jid = journal.record_forecast(target=key, period=period, horizon=horizon, forecast=row["forecast"],
                                              benchmarks=marks[key], release_sha=sha, std_error=row["std_error"],
                                              equation=row["equation"], version=nowcast.VERSION,
                                              inputs={**{k: v for k, v in origin.items() if v is not None},
                                                      "months_known": t.get("months_known", ras["months_known"]),
                                                      "w": row["w"], "bridge": row["bridge"],
                                                      "bridge_source": t.get("bridge_source"),
                                                      "ras_estimate": t.get("ras_estimate"), "report": report})
                recorded.append({"target": key, "horizon": horizon, "id": jid})
            except JournalError as exc:
                notes.append(f"{key}: {exc}")
        releases = store.read_state("journal_releases.json") or {}
        releases.setdefault(sha, rel)
        store.write_state("journal_releases.json", releases)
    status = {k: by_target[k]["equation"] for k in TARGETS}
    payload = {"period": period, "computed_at": nowcast.now_utc(), "book_version": book_version,
               "targets": [{"key": k, **{f: v for f, v in TARGETS[k].items()}} for k in TARGETS],
               "report": report, "horizon": horizon, "recorded": recorded, "notes": notes, "status": status,
               "ras": ras, "expectation": expectation,
               "quarter": {"period": period, "months": ras["months"], "months_known": ras["months_known"],
                           "by_target": by_target}}
    store.write_state("nowcast.json", payload)
    ni, nim = by_target["ni_q"], by_target["nim_q"]
    _say(f"нау-каст {period}: известно месяцев {ras['months_known']}, w {ni['w']:.2f}, "
         f"прогноз ЧП {ni['forecast'] if ni['forecast'] is None else round(ni['forecast'], 1)}"
         + (f"; записан горизонт {horizon}" if recorded else ""))
    nim_text = "—" if nim["forecast"] is None else f"{nim['forecast'] * 100:.2f} %"
    _say(f"нау-каст ЧПМ (T3): w {nim['w']:.2f}, прогноз {nim_text} — {nim['equation']}")
    _say(f"нау-каст CoR (T4): {by_target['cor_q']['equation']}")
    for n in notes:
        _say("ВНИМАНИЕ: " + n)
    return 3 if notes else 0


# ------------------------------------------------------------------ status и health

def status(store: Store, today: date) -> int:
    report = store.read_state(REPORT_FILE) or {}
    _say(f"последний такт: {report.get('mode')} {report.get('generated_at')} код {report.get('code')}")
    streak = report.get("streak") or {}
    for name, st in sorted((report.get("sources") or {}).items()):
        _say(f"  {name}: {st}" + (f" (тактов деградации подряд: {streak[name]})" if name in streak else ""))
    for s in store.all_series():
        p = s.latest()
        _say(f"  ряд {s.id}: {p.period if p else '—'} = {p.value if p else '—'}")
    path = store.root / "journal.sqlite"
    if path.exists():
        j = Journal(path, readonly=True)
        _say(f"журнал: записей {len(j.forecasts())}, допуск {j.admission_summary()['status']}")
    return 0


def health(store: Store, today: date) -> int:
    size = store.state_size_bytes()
    raw = store.raw_size_bytes()
    log = store.root / "state_size.log"
    store.root.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{today.isoformat()}\t{size}\t{raw}\n")
    code = 0
    _say(f"состояние: {size / 1e6:.1f} МБ (сырой архив {raw / 1e6:.1f} МБ), потолок {STATE_CEILING_BYTES / 1e9:.0f} ГБ")
    if size > STATE_CEILING_BYTES:
        _say("ТРЕВОГА: состояние выше потолка — решение владельца (ротация защищённых источников запрещена)")
        code = 3
    if not TRUSTED_CA.exists():
        _say("ТРЕВОГА: нет закреплённого корня для T-Invest")
        code = 3
    last = (store.read_state(REPORT_FILE) or {}).get("sources", {}).get(TOKEN_COLLECTOR)
    _say("токен T-Invest: " + ("задан" if tinvest.token() else "в этом процессе не задан (такт даёт его только "
                                                                "сборщику T-Invest)")
         + f"; последний сбор T-Invest — {last or 'не было'}")
    return code


# ------------------------------------------------------------------ CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m indicators.collect", description="такт индикаторов")
    sub = ap.add_subparsers(dest="mode", required=True)
    for m in ("collect", "daily-indicators", "release-watch"):
        p = sub.add_parser(m)
        p.add_argument("--only", metavar="СБОРЩИК[,…]", help="только эти сборщики (без шагов такта)")
        p.add_argument("--skip", metavar="СБОРЩИК[,…]", help="без этих сборщиков")
        if m == "release-watch":
            p.add_argument("--force", action="store_true", help="не проверять окно релиза")
    sub.add_parser("nowcast")
    sub.add_parser("status")
    sub.add_parser("health")
    p = sub.add_parser("record-ras", help="числа релиза РСБУ за месяц: метрика=значение …")
    p.add_argument("--month", required=True, help="2026M09")
    p.add_argument("--source", required=True)
    p.add_argument("values", nargs="+")
    p = sub.add_parser("record-actual", help="факт квартала в журнал (неснимаем)")
    p.add_argument("--target", required=True, choices=sorted(TARGETS))
    p.add_argument("--period", required=True, help="2026Q3")
    p.add_argument("--value", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--reported-on", help="день публикации YYYY-MM-DD")
    p = sub.add_parser("record-consensus")
    p.add_argument("--period", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--source", required=True)
    p = sub.add_parser("record-dividend", help="решение ГОСА или рекомендация совета (запасной источник реестра)")
    p.add_argument("--year", required=True, type=int, help="год прибыли")
    p.add_argument("--dps", required=True, help="валовой DPS, ₽ (обыкновенная акция)")
    p.add_argument("--status", required=True, choices=reg.STATUSES)
    p.add_argument("--record-date", help="отсечка YYYY-MM-DD (у declared обязательна)")
    p.add_argument("--decided", help="дата решения YYYY-MM-DD")
    p.add_argument("--pay-date", help="дата выплаты YYYY-MM-DD")
    p.add_argument("--dps-preferred", help="DPS привилегированной, если отличается")
    p.add_argument("--source", required=True, help="ссылка на документ эмитента")
    p = sub.add_parser("record-guidance")
    p.add_argument("--year", required=True, type=int)
    p.add_argument("--item", required=True, choices=record.GUIDANCE_ITEMS)
    p.add_argument("--value", required=True)
    p.add_argument("--source", required=True)
    return ap


def main(argv: list[str] | None = None, *, getter=None, today: date | None = None) -> int:
    _utf8()
    args = build_parser().parse_args(argv)
    today = today or date.today()
    store = Store()
    try:
        if args.mode in sources.MODES:
            return run_mode(args.mode, store, today, getter=getter, force=getattr(args, "force", False),
                            only=args.only, skip=args.skip)
        if args.mode == "nowcast":
            return run_nowcast(store, today)
        if args.mode == "status":
            return status(store, today)
        if args.mode == "health":
            return health(store, today)
        if args.mode == "record-ras":
            values = record.parse_pairs(args.values)
            record.record_ras(store, args.month, values, args.source)
            ctx = context(store, today, getter=getter)
            out = release_watch.watch(ctx, args.month)
            _say(f"record-ras {args.month}: принято {', '.join(out['new']) or 'ничего нового'}")
            return 0
        if args.mode == "record-actual":
            reported = date.fromisoformat(args.reported_on) if args.reported_on else None
            v = record.record_actual(Journal(store.root / "journal.sqlite"), store, target=args.target,
                                     period=args.period, value=args.value, source=args.source, reported_on=reported)
            _say(f"record-actual {args.target} {args.period} = {v}")
            return 0
        if args.mode == "record-consensus":
            v = record.record_consensus(store, period=args.period, value=args.value, source=args.source)
            _say(f"record-consensus {args.period} = {v}")
            return 0
        if args.mode == "record-dividend":
            rec = reg.record_manual(
                store, year=args.year, dps=record.parse_value(args.dps, share=False), status=args.status,
                source=args.source, record_date=args.record_date, decided_date=args.decided, pay_date=args.pay_date,
                dps_preferred=None if args.dps_preferred is None else record.parse_value(args.dps_preferred, share=False))
            _say(f"record-dividend {rec['year']}: {rec['status']} {rec['dps']} ₽, отсечка {rec['record_date'] or '—'}")
            return 0
        if args.mode == "record-guidance":
            v = record.record_guidance(store, year=args.year, item=args.item, value=args.value, source=args.source)
            _say(f"record-guidance {args.year} {args.item} = {v}")
            return 0
    except (record.RecordError, reg.RegisterError, JournalError, periods.PeriodError, config.ConfigError) as exc:
        _say(f"ПРОВАЛ: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _say(f"ПРОВАЛ: {type(exc).__name__}: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
