"""Журнал прогнозов: SQLite, записи неизменяемы, эталоны замораживаются при первой записи.

Перенос `indicators/journal.py` 850oa под квартальный банк (DESIGN §5, М§12):

* **Цели:** `ni_q` (T1 — ЧП акционерам квартала МСФО, млрд ₽; витрина и зачёт),
  `nim_q` и `cor_q` (T3/T4 — ЧПМ и CoR квартала, упр. базис, доли; наблюдаемые A-P2u).
* **Горизонты:** `T-90` (зачёт: от 90 до 31 дня до МСФО; у второго и третьего кварталов
  РСБУ целевого квартала ещё нет, у четвёртого вышел октябрь; первый квартал открывается
  только с МСФО за год — запись ложится ≈ за 60 дней до отчёта, РСБУ января уже известен)
  и `T-30` (печать: качество моста).
  Одна запись на (цель, квартал, горизонт): первая запись горизонта и есть прогноз
  горизонта, вместе с ней замораживаются эталоны. Повтор возвращает прежнюю запись.
* **Происхождение записи:** `release_sha` — последний собранный выпуск на момент записи
  (нау-каст идёт до сборки; на свежем состоянии — нули); книга и факты самого ожидания —
  `inputs.book_version`, `inputs.book_digest`, `inputs.facts_digest`.
* **Неизменяемость держит база:** правку, удаление и замену (`INSERT OR REPLACE`,
  UPSERT) запрещают триггеры SQLite; старому файлу они добавляются при открытии.
  Прогноз после внесённого факта — ошибка дисциплины. Факт неснимаем: другое
  значение того же квартала — отказ.
* **Правило допуска** (решение владельца, как у 850oa): не раньше 4 событий вне
  выборки и при отношении MSE уравнения к лучшему эталону (среди них обязательно
  «ожидание модели без индикаторов») не больше 0,8; понижение — если на последних 4
  событиях отношение больше 1,0. Считается по T3/T4 (`nim_q`, `cor_q`) на горизонте
  зачёта; пройденный допуск — право владельца подключить нау-каст ЧПМ и CoR к A-P2u,
  автоматического подключения нет.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from indicators import config

TARGETS: dict[str, dict[str, str]] = {
    "ni_q": {"title": "Чистая прибыль акционерам за квартал (МСФО)", "unit": "RUB bn", "basis": "ifrs"},
    "nim_q": {"title": "ЧПМ квартала", "unit": "share", "basis": "mgmt"},
    "cor_q": {"title": "Стоимость риска квартала", "unit": "share", "basis": "mgmt"},
}
ADMISSION_TARGETS = ("nim_q", "cor_q")
HORIZONS = ("T-90", "T-30")
HORIZON_DAYS = {"T-90": 90, "T-30": 30}
SCORING_HORIZON = "T-90"
PRINT_HORIZON = "T-30"
MODEL_BENCHMARK = "model"
ADMISSION_MIN_EVENTS = 4
ADMISSION_MAX_MSE_RATIO = 0.8
DEMOTION_MSE_RATIO = 1.0
RECENT_EVENTS = 4
SCHEMA_VERSION = 2              # 2 — триггеры против INSERT OR REPLACE (W1/C3)

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT NOT NULL,
    period      TEXT NOT NULL,
    horizon     TEXT NOT NULL,
    forecast    REAL NOT NULL,
    std_error   REAL,
    benchmarks  TEXT NOT NULL,
    release_sha TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    equation    TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '',
    inputs      TEXT NOT NULL DEFAULT '{}',
    UNIQUE (target, period, horizon)
);
CREATE TABLE IF NOT EXISTS actuals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT NOT NULL,
    period      TEXT NOT NULL,
    value       REAL NOT NULL,
    source      TEXT NOT NULL,
    reported_on TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (target, period)
);
CREATE TABLE IF NOT EXISTS frozen (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT NOT NULL,
    period      TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    frozen_at   TEXT NOT NULL,
    UNIQUE (target, period, key)
);
"""

# Ключ записи каждой таблицы: одна строка на ключ (UNIQUE) — и повод запретить замену.
ROW_KEYS: dict[str, tuple[str, ...]] = {
    "forecasts": ("target", "period", "horizon"),
    "actuals": ("target", "period"),
    "frozen": ("target", "period", "key"),
}

# Неизменяемость держит сама база (INTERFACES §5): UPDATE и DELETE запрещены. `INSERT OR
# REPLACE` (и `REPLACE INTO`) стирает конфликтующую строку без триггеров DELETE (они
# срабатывают лишь при `PRAGMA recursive_triggers` — настройке соединения, а не файла),
# поэтому ещё BEFORE INSERT: строка с тем же ключом или тем же id уже есть — отказ до
# разрешения конфликта. Так же останавливается UPSERT (`INSERT … ON CONFLICT DO …`).
TRIGGERS = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: {ban}'); END;\n"
    for t in ROW_KEYS for op, ban in (("UPDATE", "правка запрещена"), ("DELETE", "удаление запрещено"))) + "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_replace BEFORE INSERT ON {t} "
    f"WHEN EXISTS (SELECT 1 FROM {t} WHERE id = NEW.id OR ("
    + " AND ".join(f"{c} = NEW.{c}" for c in cols)
    + ")) BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: замена записи запрещена'); END;\n"
    for t, cols in ROW_KEYS.items())

BENCHMARK_TITLES = {
    "ras_bridge": "РСБУ квартала (известные месяцы и профиль прошлого года) × сезонный мост",
    "yoy_growth": "год назад × рост г/г прошлого квартала",
    "prev_quarter": "прошлый квартал",
    "same_quarter_last_year": "тот же квартал год назад",
    "consensus": "консенсус аналитиков",
    "guidance": "гайденс",
    MODEL_BENCHMARK: "ожидание модели без индикаторов",
}


class JournalError(RuntimeError):
    """Нарушение дисциплины журнала."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rule_text() -> str:
    return (f"не раньше {ADMISSION_MIN_EVENTS} событий МСФО вне выборки и при отношении MSE нау-каста к лучшему "
            f"эталону (включая ожидание модели без индикаторов) не больше "
            f"{str(ADMISSION_MAX_MSE_RATIO).replace('.', ',')} по ЧПМ и CoR квартала на горизонте "
            f"{SCORING_HORIZON}; понижение — если на последних {ADMISSION_MIN_EVENTS} событиях отношение "
            f"больше {str(DEMOTION_MSE_RATIO).replace('.', ',')}; к цене автоматически не подключается")


class Journal:
    def __init__(self, path: Path | None = None, *, readonly: bool = False):
        self.path = Path(path) if path is not None else config.state_dir() / "journal.sqlite"
        if readonly:
            if not self.path.exists():
                raise JournalError("журнала ещё нет")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(SCHEMA)
            db.executescript(TRIGGERS)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            db.commit()

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # ------------------------------------------------------------ запись

    def record_forecast(self, *, target: str, period: str, horizon: str, forecast: float,
                        benchmarks: Mapping[str, float], release_sha: str, recorded_at: str | None = None,
                        std_error: float | None = None, equation: str = "", version: str = "",
                        inputs: Mapping[str, Any] | None = None) -> int:
        """Прогноз горизонта; первая запись (цель, квартал, горизонт) — окончательная. Возвращает id."""
        if target not in TARGETS:
            raise JournalError(f"неизвестная цель {target!r}")
        if horizon not in HORIZONS:
            raise JournalError(f"неизвестный горизонт {horizon!r}")
        if forecast is None:
            raise JournalError(f"{target} {period}: прогноз null не записывается")
        if self.actual(target, period) is not None:
            raise JournalError(f"{target} {period}: факт уже внесён — прогноз задним числом не записывается")
        existing = self.forecast(target, period, horizon)
        if existing is not None:
            return existing["id"]
        clean = {k: float(v) for k, v in (benchmarks or {}).items() if v is not None}
        with closing(self._db()) as db:
            cur = db.execute(
                "INSERT INTO forecasts (target, period, horizon, forecast, std_error, benchmarks, release_sha,"
                " recorded_at, equation, version, inputs) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (target, period, horizon, float(forecast), std_error,
                 json.dumps(clean, sort_keys=True, ensure_ascii=False), str(release_sha)[:12],
                 recorded_at or _now(), equation, version,
                 json.dumps(inputs or {}, sort_keys=True, ensure_ascii=False, default=str)))
            db.commit()
            return int(cur.lastrowid)

    def record_actual(self, *, target: str, period: str, value: float, source: str,
                      reported_on: date | None = None) -> None:
        """Факт квартала; неснимаем: то же значение — без новой строки, другое — отказ."""
        if target not in TARGETS:
            raise JournalError(f"неизвестная цель {target!r}")
        if value is None:
            raise JournalError(f"{target} {period}: факт null не вносится")
        if not source:
            raise JournalError(f"{target} {period}: факт без источника не вносится")
        prev = self.actual(target, period)
        if prev is not None:
            if abs(prev["value"] - float(value)) <= 1e-12:
                return
            raise JournalError(f"{target} {period}: факт {prev['value']} уже внесён и неснимаем")
        with closing(self._db()) as db:
            db.execute("INSERT INTO actuals (target, period, value, source, reported_on, recorded_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (target, period, float(value), source, (reported_on or date.today()).isoformat(), _now()))
            db.commit()

    def freeze(self, target: str, period: str, key: str, value: Any) -> Any:
        """Замораживает значение (мост, профиль, дисперсии) при первой записи; возвращает замороженное."""
        held = self.frozen(target, period, key)
        if held is not None:
            return held
        with closing(self._db()) as db:
            db.execute("INSERT INTO frozen (target, period, key, value, frozen_at) VALUES (?,?,?,?,?)",
                       (target, period, key, json.dumps(value, sort_keys=True, ensure_ascii=False), _now()))
            db.commit()
        return value

    # ------------------------------------------------------------ чтение

    def frozen(self, target: str, period: str, key: str) -> Any:
        with closing(self._db()) as db:
            row = db.execute("SELECT value FROM frozen WHERE target=? AND period=? AND key=?",
                             (target, period, key)).fetchone()
        return None if row is None else json.loads(row[0])

    @staticmethod
    def _row(r: tuple) -> dict[str, Any]:
        keys = ("id", "target", "period", "horizon", "forecast", "std_error", "benchmarks", "release_sha",
                "recorded_at", "equation", "version", "inputs")
        out = dict(zip(keys, r))
        out["benchmarks"] = json.loads(out["benchmarks"])
        out["inputs"] = json.loads(out["inputs"] or "{}")
        # Книга, на которой посчитано ожидание записи; у записей до W2 её нет — null.
        out["book_version"] = out["inputs"].get("book_version")
        return out

    def forecasts(self, target: str | None = None) -> list[dict[str, Any]]:
        q = ("SELECT id, target, period, horizon, forecast, std_error, benchmarks, release_sha, recorded_at,"
             " equation, version, inputs FROM forecasts")
        args: tuple = ()
        if target:
            q += " WHERE target = ?"
            args = (target,)
        with closing(self._db()) as db:
            return [self._row(r) for r in db.execute(q + " ORDER BY period, target, horizon, id", args)]

    def forecast(self, target: str, period: str, horizon: str) -> dict[str, Any] | None:
        for r in self.forecasts(target):
            if r["period"] == period and r["horizon"] == horizon:
                return r
        return None

    def actual(self, target: str, period: str) -> dict[str, Any] | None:
        with closing(self._db()) as db:
            row = db.execute("SELECT value, source, reported_on, recorded_at FROM actuals WHERE target=? AND period=?",
                             (target, period)).fetchone()
        if row is None:
            return None
        return {"value": row[0], "source": row[1], "reported_on": row[2], "recorded_at": row[3]}

    def actuals(self, target: str) -> dict[str, dict[str, Any]]:
        with closing(self._db()) as db:
            rows = db.execute("SELECT period, value, source, reported_on FROM actuals WHERE target=? ORDER BY period",
                              (target,)).fetchall()
        return {p: {"value": v, "source": s, "reported_on": d} for p, v, s, d in rows}

    # ------------------------------------------------------------ зачёт

    def scored(self, target: str, horizon: str = SCORING_HORIZON) -> list[dict[str, Any]]:
        """События цели с фактом и прогнозом горизонта: прогноз, эталоны той же записи, факт."""
        acts = self.actuals(target)
        out = []
        for r in self.forecasts(target):
            if r["horizon"] != horizon or r["period"] not in acts:
                continue
            out.append({"period": r["period"], "forecast": r["forecast"], "benchmarks": r["benchmarks"],
                        "actual": acts[r["period"]]["value"], "version": r["version"]})
        return sorted(out, key=lambda x: x["period"])

    def admission(self, target: str) -> Mapping[str, Any]:
        """Допуск цели: {status, events_scored, events_needed, mse_ratio, best_benchmark, recent_mse_ratio, reason}."""
        rows = [r for r in self.scored(target) if MODEL_BENCHMARK in r["benchmarks"]]
        return admission_from_rows(rows, target=target)

    def admission_summary(self) -> dict[str, Any]:
        """П§2 `nowcast.admission` (без календарных полей — их добавляет выход выпуска)."""
        per = {t: self.admission(t) for t in ADMISSION_TARGETS}
        scored = min(p["events_scored"] for p in per.values())
        if scored < ADMISSION_MIN_EVENTS:
            status = "collecting"
        elif all(p["status"] == "passed" for p in per.values()):
            status = "passed"
        else:
            status = "failed"
        return {"rule": rule_text(), "status": status, "events_needed": ADMISSION_MIN_EVENTS,
                "events_scored": scored, "mse_ratio": {t: per[t]["mse_ratio"] for t in ADMISSION_TARGETS},
                "by_target": {t: dict(per[t]) for t in ADMISSION_TARGETS}}

    def entries(self, *, events: int = RECENT_EVENTS) -> list[dict[str, Any]]:
        """Записи последних `events` кварталов (все цели и горизонты) с фактами и ошибками."""
        rows = self.forecasts()
        periods_ = sorted({r["period"] for r in rows})[-events:]
        out = []
        for r in rows:
            if r["period"] not in periods_:
                continue
            act = self.actual(r["target"], r["period"])
            actual = None if act is None else act["value"]
            errors = None
            if actual is not None:
                errors = {"forecast": r["forecast"] - actual}
                errors.update({k: v - actual for k, v in r["benchmarks"].items()})
            out.append({"id": r["id"], "target": r["target"], "period": r["period"], "horizon": r["horizon"],
                        "recorded_at": r["recorded_at"], "release_sha": r["release_sha"],
                        "book_version": r["book_version"],
                        "forecast": r["forecast"], "benchmarks": r["benchmarks"], "actual": actual,
                        "errors": errors})
        return out

    def export(self) -> dict[str, Any]:
        """Полный журнал для `journal.json` ветки данных."""
        return {"schema": SCHEMA_VERSION, "targets": TARGETS, "rule": rule_text(),
                "forecasts": self.forecasts(),
                "actuals": {t: self.actuals(t) for t in TARGETS}}


def mse_against_best(rows: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    """(MSE прогноза / MSE лучшего эталона, эталон) на одних и тех же событиях; эталон — общий для всех строк."""
    if not rows:
        return None, None
    common = set.intersection(*(set(r["benchmarks"]) for r in rows))
    if not common:
        return None, None
    mse = sum((r["forecast"] - r["actual"]) ** 2 for r in rows) / len(rows)
    by = {k: sum((r["benchmarks"][k] - r["actual"]) ** 2 for r in rows) / len(rows) for k in common}
    best = min(sorted(by), key=by.get)
    if by[best] == 0.0:
        return (1.0 if mse == 0.0 else None), best
    return mse / by[best], best


def admission_from_rows(rows: list[dict[str, Any]], *, target: str = "") -> dict[str, Any]:
    n = len(rows)
    ratio, best = mse_against_best(rows)
    recent, recent_best = mse_against_best(rows[-ADMISSION_MIN_EVENTS:])
    base = {"target": target, "events_scored": n, "events_needed": max(0, ADMISSION_MIN_EVENTS - n),
            "mse_ratio": ratio, "best_benchmark": best,
            "recent_mse_ratio": recent if n >= ADMISSION_MIN_EVENTS else None}
    if n < ADMISSION_MIN_EVENTS:
        return {**base, "status": "collecting", "reason": f"зачтено {n} из {ADMISSION_MIN_EVENTS} событий"}
    if recent is None or recent > DEMOTION_MSE_RATIO:
        return {**base, "status": "failed", "demoted": True,
                "reason": f"на последних {ADMISSION_MIN_EVENTS} событиях отношение MSE к «{recent_best}» больше "
                          f"{DEMOTION_MSE_RATIO}"}
    if ratio is not None and ratio <= ADMISSION_MAX_MSE_RATIO:
        return {**base, "status": "passed", "reason": f"отношение MSE к «{best}» — {ratio:.2f}"}
    return {**base, "status": "failed", "reason": f"отношение MSE к «{best}» — {ratio}"}
