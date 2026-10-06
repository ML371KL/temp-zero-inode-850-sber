"""Нау-каст открытого квартала по РСБУ банка (DESIGN §5, М§12, INTERFACES §5).

Одно уравнение без подогнанных коэффициентов на три цели:

```
прогноз = ожидание модели + w · (оценка по РСБУ × мост − ожидание)
w = σ²_база / (σ²_база + σ²_РСБУ(k)),   k — известных месяцев квартала (0…3)
```

* **T1 `ni_q`** — ЧП акционерам МСФО, млрд ₽. Оценка по РСБУ = Σ известных месяцев ЧП +
  Σ оценённых профилем (последний известный месяц × отношение тех же месяцев год назад).
  Месяц известен по релизу (`ras.release.np_m`) или форме 0409102 (`cbr.f102.ni_ytd`,
  разностью нарастающих). Мост — сезонное отношение МСФО/РСБУ того же квартала
  (`facts/bridge_ras_ifrs.json → by_quarter`; без файла — среднее прошлых лет по истории).
  σ — относительные ошибки ретро (`indicators/retro.py → sigma_table`).
* **T3 `nim_q`** — ЧПМ, упр. базис. ЧПМ РСБУ = ЧПД квартала (релиз `ras.release.nii_m`,
  неизвестные месяцы — профилем) × 365/d ÷ средние процентные активы банка (форма 0409101,
  `cbr.f101.iea` по кодам фактов `bridge_ras_ifrs.iea.codes`: (конец прошлого квартала + конец
  квартала) / 2, как у стороны РСБУ моста; невышедший конец квартала — последний известный
  конец месяца × средний месячный рост за три месяца). Мост ЧПМ = мост ЧПД ÷ мост активов (`bridge_ras_ifrs.json → nii`, `iea`: по кварталу
  года, иначе среднее окна `value`) — это ЧПМ в базисе МСФО группы (`iea.basis`, по
  умолчанию `engine`); в упр. базис его переводит мост ядра (`model.credit`, функция
  `to_mgmt_nim` вызывающего; перевод базисов — только там, INTERFACES §0 п. 6). σ — абсолютные
  ошибки ретро (`retro.ratio_sigma_table`: мост — среднее четырёх прошлых кварталов фактов,
  вне выборки), σ_база — «прошлый квартал». Моста в фактах нет — прогноз = ожидание.
* **T4 `cor_q`** — CoR, упр. базис: CoR РСБУ квартала = среднее помесячной CoR релиза
  (`ras.release.cor_m`, известные месяцы; до первого — последний известный месяц) × мост
  `bridge_ras_ifrs.json → cor` (РСБУ → упр.). Моста нет (`cor.v = null`, «нет данных») —
  **нау-каст CoR равен ожиданию модели**, `ras_estimate` — `null`, и `equation` это говорит.

Мост, профиль и σ квартала замораживаются в журнале при первой записи (`collect nowcast`,
цели `ni_q` и `nim_q`, `cor_q`) и дальше важнее пересчитанных.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping

from indicators import cbr_forms, periods
from indicators.store import USABLE, Store

EQUATION = "прогноз = ожидание модели + w·(оценка по РСБУ × сезонный мост − ожидание)"
EQUATIONS = {
    "ni_q": EQUATION,
    "nim_q": ("прогноз = ожидание модели + w·(ЧПД РСБУ × мост ЧПД × 365/d ÷ (процентные активы РСБУ × "
              "мост активов), в упр. базисе мостом ядра − ожидание)"),
    "cor_q": "прогноз = ожидание модели + w·(CoR РСБУ × мост CoR РСБУ → упр. − ожидание)",
}
NO_BRIDGE = {
    "ni_q": "моста ЧП РСБУ → МСФО нет: прогноз = ожидание модели",
    "nim_q": "моста ЧПД и процентных активов РСБУ → МСФО нет в фактах: прогноз = ожидание модели",
    "cor_q": "моста CoR РСБУ → упр. нет (нет данных): нау-каст CoR (T4) = ожидание модели",
}
NO_MGMT = "ЧПМ РСБУ через мосты посчитан, но мост ЧПМ ядра (упр. ↔ движок) недоступен: прогноз = ожидание модели"
NO_SIGMA = "σ ретро ещё не измерена (нет истории моста): w = 0, прогноз = ожидание модели"
VERSION = "nowcast-2"
TARGET_KEYS = ("ni_q", "nim_q", "cor_q")
EXPECTATION_KEYS = {"ni_q": ("ni_q", "ni"), "nim_q": ("nim_q_mgmt", "nim_q", "nim_mgmt"),
                    "cor_q": ("cor_q_mgmt", "cor_q", "cor_mgmt")}
BASIS = {"ni_q": "ifrs", "nim_q": "mgmt", "cor_q": "mgmt"}
INTERVAL_Z = 1.0          # интервал — прогноз ± одна стандартная ошибка
DAYS_IN_YEAR = 365
IEA_GROWTH_MONTHS = 3     # рост процентных активов для невышедших концов месяцев — за столько месяцев
IEA_STALE_MONTHS = 3      # последний известный конец месяца старше начала квартала больше чем на столько — нет оценки
COR_LOOKBACK_MONTHS = 3   # CoR РСБУ до первого месяца квартала — последний известный месяц не старше этого

Row = tuple[str, float, str]      # (момент известности, значение, источник)


def _known(rows: list[Row], as_of: str | None) -> tuple[float, str, str] | None:
    """(значение последнего винтажа, источник, момент первого появления), известные на дату."""
    rows = [r for r in rows if as_of is None or r[0][:10] <= as_of]
    if not rows:
        return None
    moment, value, src = max(rows)
    return value, src, min(r[0] for r in rows)


def _rows(store: Store, sid: str, source: str) -> dict[str, list[Row]]:
    s = store.load(sid)
    out: dict[str, list[Row]] = {}
    if s is None:
        return out
    for p in s.points:
        if p.value is not None and p.status in ("ok", "manual"):
            out.setdefault(p.period, []).append((p.fetched_at, p.value, source))
    return out


Vintage = tuple[str, int, float | None]      # (момент известности, порядок записи, значение)


def _vintages(series: Any) -> dict[str, list[Vintage]]:
    """Период → годные версии точки в порядке записи (правило хранилища: при равных моментах — записанная позже)."""
    out: dict[str, list[Vintage]] = {}
    for order, p in enumerate(series.points):
        if p.status in USABLE:
            out.setdefault(p.period, []).append((p.fetched_at, order, p.value))
    return out


def _vintage_as_of(rows: list[Vintage], as_of: str) -> float | None:
    """Значение последней версии, полученной не позже даты, — то же, что `Series.value_as_of`."""
    rows = [r for r in rows if r[0][:10] <= as_of]
    return max(rows, key=lambda r: r[:2])[2] if rows else None


@dataclass
class History:
    """Ряды хранилища, нужные нау-касту и ретро, с моментами известности."""

    store: Store
    months: dict[str, list[Row]] = field(default_factory=dict)                      # мес → ЧП (релиз, форма 102)
    ifrs: dict[str, tuple[str, float]] = field(default_factory=dict)                # кв → (момент, ЧП МСФО)
    release: dict[str, dict[str, list[Row]]] = field(default_factory=dict)          # метрика релиза → мес → строки
    iea: dict[str, list[Row]] = field(default_factory=dict)                         # мес → процентные активы, ф. 101
    mgmt: dict[str, dict[str, tuple[str, float]]] = field(default_factory=dict)     # nim|cor → кв → (момент, факт)
    # Версии фактов кварталов: `ifrs` и `mgmt` несут момент ПЕРВОГО появления и ПОСЛЕДНЕЕ значение — на дату
    # между версиями значение читается отсюда (иначе ретро знало бы пересмотр до его выхода).
    ifrs_rows: dict[str, list[Vintage]] = field(default_factory=dict)               # кв → версии ЧП МСФО
    mgmt_rows: dict[str, dict[str, list[Vintage]]] = field(default_factory=dict)    # nim|cor → кв → версии факта

    @classmethod
    def load(cls, store: Store) -> "History":
        h = cls(store=store)
        for m, rows in _rows(store, "ras.release.np_m", "release").items():
            h.months.setdefault(m, []).extend(rows)
        f102 = store.load("cbr.f102.ni_ytd")
        if f102 is not None:
            # Порядок — момент получения, при равных моментах — порядок записи (правило хранилища).
            ytd_by_moment = sorted((p.fetched_at, order, p.period, p.value) for order, p in enumerate(f102.points)
                                   if p.value is not None and p.status in ("ok", "manual"))
            known: dict[str, float] = {}
            held: dict[str, float] = {}                # мес → последнее записанное значение месяца по форме
            for moment, group in itertools.groupby(ytd_by_moment, key=lambda row: row[0]):
                # Итоги одного момента входят вместе: месяц — разность двух уже обновлённых итогов, а не
                # нового и прежнего (иначе у месяца на один момент оказались бы два значения).
                touched = []
                for _, _, m, v in group:
                    known[m] = v
                    touched.append(m)
                monthly = cbr_forms.month_from_ytd(known)
                # Нарастающий итог входит в разность двух месяцев — своего и следующего: пересмотр итога (или
                # его запоздавший приход) с этого момента меняет и следующий месяц, если тот уже известен.
                for mm in sorted({x for m in touched for x in (m, periods.shift_month(m, 1))}):
                    mv = monthly.get(mm)
                    if mm not in known or mv is None or held.get(mm) == mv:
                        continue
                    held[mm] = mv
                    h.months.setdefault(mm, []).append((moment, mv, "form102"))
        act = store.load("actual.ni_q")
        if act is not None:
            h.ifrs_rows = _vintages(act)
            for q, p in act.points_as_of(None).items():
                if p.value is not None:
                    h.ifrs[q] = (act.first_seen(q) or p.fetched_at, p.value)
        for metric in ("nii_m", "cor_m"):
            h.release[metric] = _rows(store, f"ras.release.{metric}", "release")
        h.iea = _rows(store, "cbr.f101.iea", "form101")
        for key in ("nim", "cor"):
            s = store.load(f"actual.{key}_q")
            h.mgmt_rows[key] = {} if s is None else _vintages(s)
            h.mgmt[key] = {} if s is None else {
                q: (s.first_seen(q) or p.fetched_at, p.value) for q, p in s.points_as_of(None).items()
                if p.value is not None}
        return h

    @staticmethod
    def _fact(latest: float, rows: list[Vintage] | None, as_of: str | None) -> float | None:
        """Факт квартала на дату: версия, известная не позже `as_of`; без даты (или без версий — история
        собрана не из хранилища) — последнее значение."""
        return latest if as_of is None or not rows else _vintage_as_of(rows, as_of)

    def month(self, m: str, as_of: str | None) -> tuple[float, str, str] | None:
        """(значение, источник, момент) ЧП месяца, известное на дату; релиз важнее формы."""
        rows = [r for r in self.months.get(m, []) if as_of is None or r[0][:10] <= as_of]
        if not rows:
            return None
        for kind in ("release", "form102"):
            kind_rows = [r for r in rows if r[2] == kind]
            if kind_rows:
                moment, value, src = max(kind_rows)
                first = min(r[0] for r in kind_rows)
                return value, src, first
        return None

    def release_month(self, metric: str, m: str, as_of: str | None) -> tuple[float, str, str] | None:
        return _known(self.release.get(metric, {}).get(m, []), as_of)

    def iea_month(self, m: str, as_of: str | None) -> tuple[float, str, str] | None:
        return _known(self.iea.get(m, []), as_of)

    def mgmt_quarter(self, key: str, q: str, as_of: str | None) -> float | None:
        row = self.mgmt.get(key, {}).get(q)
        if row is None or (as_of is not None and row[0][:10] > as_of):
            return None
        return self._fact(row[1], self.mgmt_rows.get(key, {}).get(q), as_of)

    def quarter_ifrs(self, q: str, as_of: str | None) -> float | None:
        row = self.ifrs.get(q)
        if row is None or (as_of is not None and row[0][:10] > as_of):
            return None
        return self._fact(row[1], self.ifrs_rows.get(q), as_of)

    def report_date(self, q: str) -> str | None:
        row = self.ifrs.get(q)
        return None if row is None else row[0][:10]

    def quarter_ras(self, q: str, as_of: str | None) -> float | None:
        vals = [self.month(m, as_of) for m in periods.quarter_months(q)]
        if any(v is None for v in vals):
            return None
        return sum(v[0] for v in vals)

    def latest_ifrs_quarter(self, before: str, as_of: str | None) -> str | None:
        qs = [q for q in self.ifrs if q < before and self.quarter_ifrs(q, as_of) is not None]
        return max(qs) if qs else None


def history_bridge(h: History, q: str, as_of: str | None) -> tuple[float | None, list[str]]:
    """Среднее отношение МСФО/РСБУ ЧП того же квартала прошлых лет, известное на дату."""
    _, n = periods.parse_quarter(q)
    ratios, used = [], []
    for past in sorted(h.ifrs):
        if past >= q or periods.parse_quarter(past)[1] != n:
            continue
        ifrs = h.quarter_ifrs(past, as_of)
        ras = h.quarter_ras(past, as_of)
        if ifrs is not None and ras:
            ratios.append(ifrs / ras)
            used.append(past)
    return (sum(ratios) / len(ratios) if ratios else None), used


# ------------------------------------------------------------------ мосты фактов

def node_value(node: Any) -> float | None:
    """Число узла факта `{v, src|calc}` (или голое число); `null` и не-число — None."""
    if isinstance(node, Mapping):
        node = node.get("v")
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def facts_bridge(bridge: Mapping[str, Any] | None, q: str) -> float | None:
    """Сезонный мост ЧП (`by_quarter.<n>`) — T1."""
    if not bridge:
        return None
    return node_value((bridge.get("by_quarter") or {}).get(str(periods.parse_quarter(q)[1])))


def facts_ratio(block: Any, q: str) -> tuple[float | None, str | None]:
    """Мост блока фактов (`nii`, `iea`, `cor`): по кварталу года, иначе среднее окна `value`.

    Блок без числа (`{"v": null, "calc": "нет данных…"}`) — (None, None).
    """
    if not isinstance(block, Mapping):
        return None, None
    n = str(periods.parse_quarter(q)[1])
    v = node_value((block.get("by_quarter") or {}).get(n))
    if v is not None:
        return v, f"by_quarter.{n}"
    v = node_value(block.get("value"))
    if v is not None:
        return v, "value"
    v = node_value(block)
    return (v, "v") if v is not None else (None, None)


def facts_ratio_before(block: Any, q: str, window: int) -> float | None:
    """Среднее отношений `quarters[].ratio` последних `window` кварталов раньше q (ретро вне выборки)."""
    if not isinstance(block, Mapping):
        return None
    rows = sorted((str(r.get("period")), node_value(r.get("ratio"))) for r in block.get("quarters") or []
                  if isinstance(r, Mapping) and str(r.get("period")) < q)
    vals = [v for _, v in rows[-window:] if v is not None]
    return sum(vals) / len(vals) if vals else None


# ------------------------------------------------------------------ оценки РСБУ

def ras_estimate(h: History, q: str, as_of: str | None, *, known: int | None = None,
                 profile: Mapping[str, float] | None = None,
                 getter: Callable[[str, str | None], tuple[float, str, str] | None] | None = None,
                 key: str = "ni") -> dict[str, Any] | None:
    """Оценка РСБУ квартала: известные месяцы + профиль. `known` — принудительно k первых месяцев (ретро).

    Профиль — значения тех же месяцев год назад (`profile` — замороженные при первой записи
    {месяц год назад: значение}); оценка месяца = последний известный × (месяц год назад / база
    год назад). `getter` — чтение месяца (по умолчанию ЧП: релиз, затем форма 102), `key` — имя
    значения в строках месяцев.
    """
    get = getter or h.month
    months = periods.quarter_months(q)
    rows = []
    last_known: str | None = None
    for i, m in enumerate(months):
        got = get(m, as_of)
        if known is not None and i >= known:
            got = None
        if got is not None:
            rows.append({"month": m, "status": "known", "source": got[1], "date_known": got[2][:10], key: got[0]})
            last_known = m
        else:
            rows.append({"month": m, "status": "estimated", "source": "profile", "date_known": None, key: None})
    if last_known is None:
        before = periods.shift_month(months[0], -1)
        for _ in range(3):
            if get(before, as_of) is not None:
                last_known = before
                break
            before = periods.shift_month(before, -1)
    if last_known is None:
        return None
    frozen = dict(profile or {})

    def year_ago_value(m: str) -> float | None:
        ly = periods.year_ago(m)
        if ly in frozen:
            return frozen[ly]
        got = get(ly, as_of)
        return None if got is None else got[0]

    base = get(last_known, as_of)[0]
    used: dict[str, float] = {}
    if any(r["status"] == "estimated" for r in rows):
        base_ly = year_ago_value(last_known)
        if not base_ly:
            return None
        used[periods.year_ago(last_known)] = base_ly
        for r in rows:
            if r["status"] != "estimated":
                continue
            ly = year_ago_value(r["month"])
            if ly is None:
                return None
            used[periods.year_ago(r["month"])] = ly
            r[key] = base * ly / base_ly
    k = sum(1 for r in rows if r["status"] == "known")
    return {"months": rows, "months_known": k, "estimate": sum(r[key] for r in rows),
            "profile": used, "profile_base": last_known}


def iea_months(q: str) -> list[str]:
    """Концы месяцев средних процентных активов квартала: конец прошлого квартала и конец квартала
    (формы 0409101 на 1-е число первого месяца квартала и месяца после него — как у моста фактов)."""
    ms = periods.quarter_months(q)
    return [periods.shift_month(ms[0], -1), ms[-1]]


def iea_quarter_mean(values: Mapping[str, float], q: str) -> float | None:
    """Среднее концов `iea_months` (история: все известны); одно правило для фактов и нау-каста."""
    got = [values.get(m) for m in iea_months(q)]
    return None if any(v is None for v in got) else sum(got) / len(got)


def iea_average(h: History, q: str, as_of: str | None) -> dict[str, Any] | None:
    """Средние процентные активы РСБУ квартала на дату: известные концы `iea_months` формы 0409101,
    невышедшие — последний известный конец месяца × средний месячный рост за `IEA_GROWTH_MONTHS`."""
    need = iea_months(q)
    lo = periods.shift_month(need[0], -(IEA_STALE_MONTHS + IEA_GROWTH_MONTHS))
    known: dict[str, float] = {}
    m = lo
    while m <= need[-1]:
        got = h.iea_month(m, as_of)
        if got is not None and got[0]:
            known[m] = got[0]
        m = periods.shift_month(m, 1)
    if not known:
        return None
    last = max(known)
    if last < periods.shift_month(need[0], -IEA_STALE_MONTHS):
        return None
    growth = 0.0
    for span in range(IEA_GROWTH_MONTHS, 0, -1):
        prev = periods.shift_month(last, -span)
        if prev in known:
            growth = (known[last] / known[prev]) ** (1.0 / span) - 1.0
            break
    rows = []
    for mm in need:
        if mm in known:
            rows.append({"month": mm, "status": "known", "value": known[mm]})
            continue
        if mm > last:
            steps = sum(1 for _ in _months_between(last, mm))
            rows.append({"month": mm, "status": "estimated", "value": known[last] * (1.0 + growth) ** steps})
            continue
        before = [x for x in known if x < mm]
        after = [x for x in known if x > mm]
        near = max(before) if before else min(after)
        rows.append({"month": mm, "status": "estimated", "value": known[near]})
    return {"months": rows, "average": sum(r["value"] for r in rows) / len(rows), "growth": growth,
            "last_known": last}


def _months_between(a: str, b: str):
    """Месяцы после a до b включительно."""
    m = periods.shift_month(a, 1)
    while m <= b:
        yield m
        m = periods.shift_month(m, 1)


def nim_ras(h: History, q: str, as_of: str | None, *, known: int | None = None,
            profile: Mapping[str, float] | None = None) -> dict[str, Any] | None:
    """ЧПМ РСБУ квартала: ЧПД (известные месяцы + профиль) × 365/d ÷ средние процентные активы."""
    nii = ras_estimate(h, q, as_of, known=known, profile=profile, key="value",
                       getter=lambda m, a: h.release_month("nii_m", m, a))
    iea = iea_average(h, q, as_of)
    if nii is None or iea is None or not iea["average"]:
        return None
    days = periods.quarter_days(q)
    return {"nii": nii, "iea": iea, "days": days,
            "value": nii["estimate"] * DAYS_IN_YEAR / days / iea["average"]}


def cor_ras(h: History, q: str, as_of: str | None, *, known: int | None = None) -> dict[str, Any] | None:
    """CoR РСБУ квартала: среднее помесячной CoR релиза за известные месяцы квартала; до первого —
    последний известный месяц (не старше `COR_LOOKBACK_MONTHS`)."""
    rows = []
    for i, m in enumerate(periods.quarter_months(q)):
        got = h.release_month("cor_m", m, as_of)
        if got is not None and (known is None or i < known):
            rows.append({"month": m, "value": got[0]})
    k = len(rows)
    if not rows:
        m = periods.quarter_months(q)[0]
        for _ in range(COR_LOOKBACK_MONTHS):
            m = periods.shift_month(m, -1)
            got = h.release_month("cor_m", m, as_of)
            if got is not None:
                rows = [{"month": m, "value": got[0], "status": "carried"}]
                break
    if not rows:
        return None
    return {"months": rows, "months_known": k, "value": sum(r["value"] for r in rows) / len(rows)}


def weight(sigma_base: float | None, sigma_ras: float | None) -> float:
    if sigma_ras is None or sigma_base is None:
        return 0.0
    if sigma_ras <= 0:
        return 1.0
    return sigma_base ** 2 / (sigma_base ** 2 + sigma_ras ** 2)


def _release_months(store: Store, q: str, as_of: str | None) -> dict[str, dict[str, float | None]]:
    """ЧПД и резервы месяцев квартала из релиза (для строк `months`)."""
    out: dict[str, dict[str, float | None]] = {}
    for metric, key in (("nii_m", "nii"), ("prov_m", "llp")):
        s = store.load(f"ras.release.{metric}")
        for m in periods.quarter_months(q):
            out.setdefault(m, {})[key] = None if s is None else s.value_as_of(m, as_of)
    return out


# ------------------------------------------------------------------ цели

def _nim_target(h: History, q: str, as_of: str, bridge: Mapping[str, Any], sig: Mapping[str, Any],
                frozen: Mapping[str, Any], to_mgmt_nim: Callable[[float], float] | None) -> dict[str, Any]:
    est = nim_ras(h, q, as_of, profile=frozen.get("profile"))
    b_nii, s_nii = facts_ratio(bridge.get("nii"), q)
    b_iea, s_iea = facts_ratio(bridge.get("iea"), q)
    b = frozen.get("bridge")
    src = "frozen" if b is not None else None
    if b is None and b_nii is not None and b_iea:
        b, src = b_nii / b_iea, f"facts/bridge_ras_ifrs.json → nii.{s_nii}, iea.{s_iea}"
    iea_block = bridge.get("iea") if isinstance(bridge.get("iea"), Mapping) else {}
    basis = str(frozen.get("basis") or iea_block.get("basis") or "engine")
    ras_value = est["value"] if est else None
    ifrs_value = None if ras_value is None or b is None else ras_value * b
    bridged = ifrs_value if basis == "mgmt" or ifrs_value is None else (
        to_mgmt_nim(ifrs_value) if to_mgmt_nim is not None else None)
    k = est["nii"]["months_known"] if est else 0
    s_ras = (sig.get("ras") or {}).get(str(k))
    s_base = sig.get("base")
    w = weight(s_base, s_ras) if bridged is not None else 0.0
    se = math.sqrt(w) * s_ras if bridged is not None and s_ras is not None and s_base is not None else None
    if b is None:
        equation = NO_BRIDGE["nim_q"]
    elif ifrs_value is not None and bridged is None:
        equation = NO_MGMT
    elif bridged is not None and w == 0.0:
        equation = EQUATIONS["nim_q"] + "; " + NO_SIGMA
    else:
        equation = EQUATIONS["nim_q"]
    return {"ras_estimate": ras_value, "bridge": b, "bridge_source": src, "bridge_basis": basis,
            "bridge_nii": b_nii, "bridge_iea": b_iea, "ras_bridged_ifrs": ifrs_value, "ras_bridged": bridged,
            "w": w, "sigma_ras": s_ras, "sigma_base": s_base, "std_error": se, "rel_std_error": None,
            "months_known": k, "nii_ras": est["nii"]["estimate"] if est else None,
            "iea_ras": est["iea"]["average"] if est else None, "iea_months": est["iea"]["months"] if est else [],
            "days": periods.quarter_days(q), "profile": est["nii"]["profile"] if est else {},
            "basis": "mgmt", "equation": equation}


def _cor_target(h: History, q: str, as_of: str, bridge: Mapping[str, Any], sig: Mapping[str, Any],
                frozen: Mapping[str, Any]) -> dict[str, Any]:
    b, s = facts_ratio(bridge.get("cor"), q)
    src = f"facts/bridge_ras_ifrs.json → cor.{s}" if b is not None else None
    if frozen.get("bridge") is not None:
        b, src = frozen["bridge"], "frozen"
    if b is None:
        return {"ras_estimate": None, "bridge": None, "bridge_source": None, "ras_bridged": None, "w": 0.0,
                "sigma_ras": None, "sigma_base": None, "std_error": None, "rel_std_error": None,
                "months_known": 0, "basis": "mgmt", "equation": NO_BRIDGE["cor_q"]}
    est = cor_ras(h, q, as_of)
    ras_value = est["value"] if est else None
    bridged = None if ras_value is None else ras_value * b
    k = est["months_known"] if est else 0
    s_ras = (sig.get("ras") or {}).get(str(k))
    s_base = sig.get("base")
    w = weight(s_base, s_ras) if bridged is not None else 0.0
    se = math.sqrt(w) * s_ras if bridged is not None and s_ras is not None and s_base is not None else None
    equation = EQUATIONS["cor_q"] + ("; " + NO_SIGMA if bridged is not None and w == 0.0 else "")
    return {"ras_estimate": ras_value, "bridge": b, "bridge_source": src, "ras_bridged": bridged, "w": w,
            "sigma_ras": s_ras, "sigma_base": s_base, "std_error": se, "rel_std_error": None,
            "months_known": k, "cor_months": est["months"] if est else [], "basis": "mgmt", "equation": equation}


def ras_inputs(store: Store, *, period: str, today: date, bridge: Mapping[str, Any],
               sigmas: Mapping[str, Any] | None = None, frozen: Mapping[str, Any] | None = None,
               to_mgmt_nim: Callable[[float], float] | None = None) -> dict[str, Any]:
    """Месяцы квартала, оценки РСБУ, мосты, w и std_error трёх целей — без ожидания модели (INTERFACES §5).

    `sigmas` — {"ras": {"0": σ, …, "3": σ}, "base": σ} (T1, относительные) и необязательные
    {"nim": {…}, "cor": {…}} (T3/T4, абсолютные); `None` — измеряются ретро по истории хранилища.
    `frozen` — замороженные при первой записи {"bridge", "profile", "sigmas"} (T1) и {"nim": {…},
    "cor": {…}} — они важнее пересчитанных. `to_mgmt_nim` — перевод ЧПМ базиса МСФО группы в упр.
    (мост ядра; нет — нау-каст ЧПМ равен ожиданию).
    """
    frozen = dict(frozen or {})
    bridge = dict(bridge or {})
    as_of = today.isoformat()
    if sigmas is None and not frozen.get("sigmas"):
        from indicators import retro  # noqa: PLC0415 — retro импортирует этот модуль

        sigmas = {**retro.sigma_table(store, today=today),
                  **retro.ratio_sigma_table(store, bridge=bridge, to_mgmt_nim=to_mgmt_nim, today=today)}
    sigmas = dict(sigmas or {})
    h = History.load(store)
    est = ras_estimate(h, period, as_of, profile=frozen.get("profile"))
    sig = frozen.get("sigmas") or sigmas
    b_facts = facts_bridge(bridge, period)
    b_hist, used = history_bridge(h, period, as_of)
    b = frozen.get("bridge")
    b_src = "frozen" if b is not None else ("facts/bridge_ras_ifrs.json" if b_facts is not None else "history")
    if b is None:
        b = b_facts if b_facts is not None else b_hist
    extra = _release_months(store, period, as_of)
    months = []
    for r in (est["months"] if est else [{"month": m, "status": "estimated", "source": "profile",
                                           "date_known": None, "ni": None}
                                          for m in periods.quarter_months(period)]):
        row = dict(r)
        row.update(extra.get(r["month"], {"nii": None, "llp": None}))
        months.append(row)
    k = est["months_known"] if est else 0
    s_ras = (sig.get("ras") or {}).get(str(k))
    s_base = sig.get("base")
    targets: dict[str, Any] = {}
    ras_value = est["estimate"] if est else None
    bridged = None if ras_value is None or b is None else ras_value * b
    w = weight(s_base, s_ras) if bridged is not None else 0.0
    rel_se = None
    if bridged is not None and s_ras is not None and s_base is not None:
        rel_se = math.sqrt(w) * s_ras
    targets["ni_q"] = {"ras_estimate": ras_value, "bridge": b, "bridge_source": b_src, "bridge_quarters": used,
                       "ras_bridged": bridged, "w": w, "sigma_ras": s_ras, "sigma_base": s_base,
                       "rel_std_error": rel_se, "basis": "ifrs",
                       "equation": EQUATION if bridged is not None else NO_BRIDGE["ni_q"]}
    fz_nim = dict(frozen.get("nim") or {})
    targets["nim_q"] = _nim_target(h, period, as_of, bridge, fz_nim.get("sigmas") or sigmas.get("nim") or {},
                                   fz_nim, to_mgmt_nim)
    fz_cor = dict(frozen.get("cor") or {})
    targets["cor_q"] = _cor_target(h, period, as_of, bridge, fz_cor.get("sigmas") or sigmas.get("cor") or {},
                                   fz_cor)
    return {"period": period, "as_of": as_of, "months": months, "months_known": k, "targets": targets,
            "profile": est["profile"] if est else {}, "profile_base": est["profile_base"] if est else None,
            "sigmas": sig, "equation": EQUATION, "version": VERSION}


def _expectation(expectation: Mapping[str, Any], key: str) -> float | None:
    for name in EXPECTATION_KEYS[key]:
        v = expectation.get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def combine(ras: Mapping[str, Any], expectation: Mapping[str, float],
            benchmarks: Mapping[str, Mapping[str, float | None]] | None = None) -> dict[str, Any]:
    """П§2 `nowcast.quarter.by_target`: прогноз = ожидание + w·(оценка × мост − ожидание). Чистая функция.

    Нет оценки через мост — прогноз равен ожиданию (w = 0), и `equation` говорит почему (T3/T4 без
    моста). std_error — абсолютная у ЧПМ и CoR (`std_error` цели), относительная у ЧП (`rel_std_error`).
    """
    out: dict[str, Any] = {}
    for key in TARGET_KEYS:
        t = (ras.get("targets") or {}).get(key) or {}
        exp = _expectation(expectation or {}, key)
        bridged = t.get("ras_bridged")
        w = float(t.get("w") or 0.0)
        if exp is not None and bridged is not None:
            forecast = exp + w * (bridged - exp)
        elif exp is not None:
            forecast, w = exp, 0.0
        elif bridged is not None:
            forecast, w = bridged, 1.0
        else:
            forecast = None
        se = None
        if forecast is not None and bridged is not None:
            if t.get("std_error") is not None:
                se = float(t["std_error"])
            elif t.get("rel_std_error") is not None:
                se = abs(forecast) * float(t["rel_std_error"])
        marks = [{"key": k, "name": v.get("name", k) if isinstance(v, Mapping) else k,
                  "value": v.get("value") if isinstance(v, Mapping) else v}
                 for k, v in ((benchmarks or {}).get(key) or {}).items()]
        if exp is not None and not any(m["key"] == "model" for m in marks):
            marks.append({"key": "model", "name": "ожидание модели без индикаторов", "value": exp})
        out[key] = {
            "ras_estimate": t.get("ras_estimate"), "bridge": t.get("bridge"), "ras_bridged": bridged,
            "expectation": exp, "w": w, "forecast": forecast, "std_error": se,
            "interval": None if forecast is None or se is None else [forecast - INTERVAL_Z * se,
                                                                     forecast + INTERVAL_Z * se],
            "deviation": None if forecast is None or exp is None else forecast - exp,
            "equation": t.get("equation") or (EQUATIONS[key] if bridged is not None else NO_BRIDGE[key]),
            "version": ras.get("version", VERSION), "benchmarks": marks,
            "basis": BASIS[key],
        }
    return out


HORIZON_START = {"T-90": 90, "T-30": 30}      # горизонт открывается за столько дней до отчёта


def horizon_for(today: date, report_date: date) -> str | None:
    """Горизонт записи журнала: T-90 — от 90 до 31 дня до отчёта, T-30 — от 30 дней до дня отчёта."""
    days = (report_date - today).days
    if HORIZON_START["T-30"] < days <= HORIZON_START["T-90"]:
        return "T-90"
    if 0 < days <= HORIZON_START["T-30"]:
        return "T-30"
    return None


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
