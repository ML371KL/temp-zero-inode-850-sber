"""Хранилище: неизменяемый сырой архив и ряды с винтажами «что было известно на дату».

Перенос `indicators/store.py` 850oa (INTERFACES §5, §7.1):

```
raw/<источник>/<YYYY-MM-DD>/<имя>[.gz]   сырой ответ как есть + <имя>.meta.json
                                          (url, fetched_at, sha256 исходных байт, status)
series/<series_id>.json                   точки {period, value, fetched_at, status, source}
```

Сырой слой неизменяем: существующий файл не перезаписывается, другой ответ того же
дня ложится рядом с номером. Крупные ответы сжимаются gzip (mtime = 0 — повтор даёт
те же байты). Защищённые источники (`PROTECTED_SOURCES`: винтажи форм ЦБ, снимки
T-Invest, новости и PDF дня релиза, ручной ввод) ротация не трогает никогда —
восстановить их неоткуда.

Каждая точка ряда несёт дату периода И момент получения, поэтому `value_as_of`
отвечает, что было известно о периоде на дату. `null ≠ 0`: «данных нет» и «не
смогли взять» различает `status`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from indicators import config

COMPRESS_ABOVE_BYTES = 32 * 1024
GZIP_LEVEL = 6
GZIP_MAGIC = bytes((0x1F, 0x8B))
# Потолок состояния сервера; превышение — тревога `collect health`, не ротация.
STATE_CEILING_BYTES = 2_000_000_000
RAW_KEEP_DAYS = 400

# Источники без истории у первоисточника: пропущенный день не добирается никогда.
PROTECTED_SOURCES = frozenset({"cbr_forms", "tinvest", "news", "sber_pdf", "manual"})

# Статусы точки, значение которых годно для расчётов.
USABLE = frozenset({"ok", "manual"})
SAME_VALUE_RELATIVE = 1e-12
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    return _SAFE.sub("_", str(name)).strip("_") or "response"


@dataclass(frozen=True)
class Point:
    """Наблюдение ряда: период, значение, момент получения, статус, источник."""

    period: str
    value: float | None
    fetched_at: str
    status: str = "ok"
    source: str = ""
    note: str = ""


def _same(first: Point, second: Point) -> bool:
    if first.status != second.status:
        return False
    if first.value is None or second.value is None:
        return first.value is second.value
    scale = max(abs(first.value), abs(second.value), 1.0)
    return abs(first.value - second.value) <= SAME_VALUE_RELATIVE * scale


def _finite(p: Point) -> bool:
    return p.value is None or (isinstance(p.value, (int, float)) and math.isfinite(p.value))


@dataclass
class Series:
    id: str
    meta: dict[str, Any] = field(default_factory=dict)
    points: list[Point] = field(default_factory=list)

    def _best(self, as_of: str | None) -> dict[str, tuple[tuple, Point]]:
        best: dict[str, tuple[tuple, Point]] = {}
        for order, p in enumerate(self.points):
            if p.status not in USABLE or (as_of is not None and p.fetched_at[:10] > as_of):
                continue
            key = (p.fetched_at, order)
            held = best.get(p.period)
            if held is None or key > held[0]:
                best[p.period] = (key, p)
        return best

    def point_as_of(self, period: str, as_of: str | None = None) -> Point | None:
        """Последняя годная версия точки периода, полученная не позже `as_of` (равные моменты — записанная позже)."""
        held = self._best(as_of).get(period)
        return held[1] if held else None

    def value_as_of(self, period: str, as_of: str | None = None) -> float | None:
        p = self.point_as_of(period, as_of)
        return None if p is None else p.value

    def history(self, as_of: str | None = None) -> dict[str, float]:
        """Ряд, каким он был виден на дату (None — сейчас); null-точки не входят."""
        best = self._best(as_of)
        return {k: best[k][1].value for k in sorted(best) if best[k][1].value is not None}

    def points_as_of(self, as_of: str | None = None) -> dict[str, Point]:
        best = self._best(as_of)
        return {k: best[k][1] for k in sorted(best)}

    def latest(self) -> Point | None:
        best = self._best(None)
        if not best:
            return None
        return best[max(best)][1]

    def first_seen(self, period: str) -> str | None:
        """Момент первого появления годного значения периода."""
        seen = [p.fetched_at for p in self.points if p.period == period and p.status in USABLE
                and p.value is not None]
        return min(seen) if seen else None


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else config.state_dir()
        self.raw = self.root / "raw"
        self.series_dir = self.root / "series"
        self.rejected: list[str] = []

    # ------------------------------------------------------------ сырой слой

    def save_raw(self, source: str, name: str, body: bytes, *, url: str,
                 fetched_at: str | None = None, status: int | str | None = None,
                 extra: dict | None = None) -> Path:
        """Кладёт сырой ответ; существующий файл не перезаписывается. sha256 — от исходных байт."""
        fetched_at = fetched_at or _now()
        day = fetched_at[:10]
        directory = self.raw / safe_name(source) / day
        directory.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256(body).hexdigest()
        meta = {"url": url, "fetched_at": fetched_at, "sha256": sha, "status": status,
                "bytes": len(body)}
        meta.update(extra or {})
        name = safe_name(name)
        stored = body
        if len(body) >= COMPRESS_ABOVE_BYTES and not name.endswith(".gz"):
            stored = gzip.compress(body, GZIP_LEVEL, mtime=0)
            name += ".gz"
            meta["stored_encoding"] = "gzip"
        path = directory / name
        if path.exists():
            stem, suffix = path.stem, path.suffix
            index = 1
            while path.exists():
                if path.read_bytes() == stored:
                    return path
                path = directory / f"{stem}.{index}{suffix}"
                index += 1
        path.write_bytes(stored)
        path.with_name(path.name + ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return path

    @staticmethod
    def read_raw(path: Path) -> bytes:
        body = Path(path).read_bytes()
        return gzip.decompress(body) if body.startswith(GZIP_MAGIC) else body

    @staticmethod
    def raw_meta(path: Path) -> dict:
        p = Path(path)
        return json.loads(p.with_name(p.name + ".meta.json").read_text(encoding="utf-8"))

    def raw_days(self, source: str) -> list[str]:
        d = self.raw / safe_name(source)
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []

    def raw_files(self, source: str, day: str | None = None) -> list[Path]:
        d = self.raw / safe_name(source)
        if day:
            d = d / day
        if not d.exists():
            return []
        return sorted(p for p in d.rglob("*") if p.is_file() and not p.name.endswith(".meta.json"))

    def raw_size_bytes(self) -> int:
        if not self.raw.exists():
            return 0
        return sum(p.stat().st_size for p in self.raw.rglob("*") if p.is_file())

    def state_size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def prune_raw(self, keep_days: int = RAW_KEEP_DAYS, *, today: date | None = None,
                  protected: Iterable[str] = PROTECTED_SOURCES) -> list[str]:
        """Ротация: целые дни незащищённых источников старше `keep_days`."""
        removed: list[str] = []
        if not self.raw.exists():
            return removed
        today = today or date.today()
        keep = set(protected)
        for source_dir in sorted(self.raw.iterdir()):
            if not source_dir.is_dir() or source_dir.name in keep:
                continue
            for day_dir in sorted(source_dir.iterdir()):
                try:
                    age = (today - date.fromisoformat(day_dir.name)).days
                except ValueError:
                    continue
                if age <= keep_days:
                    continue
                for f in sorted(day_dir.rglob("*"), reverse=True):
                    f.unlink() if f.is_file() else f.rmdir()
                day_dir.rmdir()
                removed.append(f"{source_dir.name}/{day_dir.name}")
        return removed

    # ------------------------------------------------------------ ряды

    def path_for(self, series_id: str) -> Path:
        return self.series_dir / f"{safe_name(series_id)}.json"

    def load(self, series_id: str) -> Series | None:
        path = self.path_for(series_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Series(id=raw["id"], meta=dict(raw.get("meta") or {}),
                      points=[Point(**p) for p in raw.get("points") or []])

    def save(self, series: Series) -> Path:
        self.series_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(series.id)
        payload = {"id": series.id, "meta": series.meta,
                   "points": [asdict(p) for p in series.points]}
        body = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return path

    def upsert(self, series_id: str, points: Iterable[Point], **meta: Any) -> Series:
        """Добавляет точки, не затирая прежние версии (правило соседа 850oa).

        Точка отбрасывается, только если ближайшая по времени получения ранее
        записанная точка того же периода говорит то же самое: A→A→A — одна точка,
        A→B→A — три, поздняя A + ранняя A — две (ранняя датирует знание).
        Неконечное значение не принимается и попадает в `rejected`.
        """
        series = self.load(series_id) or Series(id=series_id)
        series.meta.update({k: v for k, v in meta.items() if v is not None})
        by_period: dict[str, list[Point]] = {}
        for p in series.points:
            by_period.setdefault(p.period, []).append(p)
        for point in points:
            if not _finite(point):
                self.rejected.append(f"{series_id} {point.period}: {point.value!r} — не конечное число")
                print(f"ОТБРОШЕНО: {self.rejected[-1]}", file=sys.stderr)
                continue
            same_period = by_period.setdefault(point.period, [])
            earlier = [p for p in same_period if p.fetched_at <= point.fetched_at]
            # сосед — последняя по времени получения; при равных моментах — записанная позже
            if earlier and _same(max(enumerate(earlier), key=lambda t: (t[1].fetched_at, t[0]))[1], point):
                continue
            same_period.append(point)
            series.points.append(point)
        series.points.sort(key=lambda p: (p.period, p.fetched_at))
        self.save(series)
        return series

    def value_as_of(self, series_id: str, period: str, as_of: str) -> float | None:
        series = self.load(series_id)
        return None if series is None else series.value_as_of(period, as_of)

    def all_series(self) -> list[Series]:
        if not self.series_dir.exists():
            return []
        out = []
        for path in sorted(self.series_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            out.append(Series(id=raw["id"], meta=dict(raw.get("meta") or {}),
                              points=[Point(**p) for p in raw.get("points") or []]))
        return out

    # ------------------------------------------------------------ файлы состояния

    def write_state(self, name: str, obj: Any) -> Path:
        """Атомарная запись JSON-файла состояния (`collector_report.json`, `nowcast.json` …)."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False),
                       encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return path

    def read_state(self, name: str) -> Any:
        path = self.root / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def point(period: str, value: float | None, *, fetched_at: str | None = None, status: str = "ok",
          source: str = "", note: str = "") -> Point:
    """Точка с моментом получения «сейчас» по умолчанию."""
    return Point(period=period, value=None if value is None else float(value),
                 fetched_at=fetched_at or _now(), status=status, source=source, note=note)
