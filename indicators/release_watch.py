"""Дозор релиза РСБУ: кандидаты чисел из разных видов источников и приём при совпадении двух.

Виды источников (REPORT-ras §3.6): таблица PDF (`pdf`, распознавание), текст
Интерфакса (`interfax`), текст smart-lab (`smartlab`), посты каналов (`telegram:<канал>` —
каждый канал свой вид: лента-агрегатор и канал эмитента независимы), издания,
форма 0409102 ЦБ (`form102` — только ЧП и прибыль до налога, ≈2 недели спустя), ручной
ввод (`manual`, `record-ras` — принимается сам).

Правила (INDICATORS §5):

* **Текст — кандидат только своего месяца.** Релиз месяца M выходит в месяце M + 1: берутся
  тексты с днём публикации после конца M и не позже конца M + 1, чьё первое предложение с фразой
  периода называет M (`news.period_months`); числа — только из фраз, называющих этот месяц.
* **Число принимается, если его подтверждают два разных вида** в пределах допуска
  (`sources.yaml → release_watch.tolerance`). Две группы по два вида и больше — расхождение.
* **Поток одного вида принимается тождеством** (`accept_by_identity`): если нарастающее и месячное
  потока (ЧП, ЧПД, ЧКД, резервы, расходы, прибыль до налога) называет один вид источника (или пара
  уже принята двумя видами), а «НМ месяца − НМ прошлого месяца в ряду = месяц» сходится в допуске
  `identity`, оба числа принимаются с видами `[<вид>, identity]`. Январь так не принимается: там НМ
  и месяц — одно число, и тождество ничего не подтверждает.
* **Тождество «НМ = НМ прошлого месяца + месяц»**: поток, не прошедший его, в ряд не пишется.
* **Принятое автоматически другое автоматическое не переписывает**: иное число — расхождение,
  меняет его только ручной ввод.

Разошлись — плашка `ras_mismatch` и разбор (`record-ras` вручную).

Принятое пишется в ряды `ras.release.<метрика>` (период — месяц `2026M08`, момент —
приём): деньги — млрд ₽ (резервы и расходы со знаком минус, как в таблице релиза),
проценты — доли. Состояние дня — `release_watch/<месяц>.json`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from indicators import cbr_forms, news, periods
from indicators.sources import Context, Result
from indicators.store import Store, point

METRICS = ("np_ytd", "np_m", "nii_ytd", "nii_m", "fee_ytd", "fee_m", "prov_ytd", "prov_m", "opex_ytd",
           "opex_m", "pretax_ytd", "pretax_m", "roe_ytd", "roe_m", "cor_ytd", "cor_m", "cir_ytd",
           "loans_ul", "loans_fl", "funds_ul", "funds_fl", "cap_base", "cap_main", "cap_total",
           "n1_0", "n1_1", "n1_2")
SHARE_METRICS = frozenset({"roe_ytd", "roe_m", "cor_ytd", "cor_m", "cir_ytd", "n1_0", "n1_1", "n1_2"})
FLOWS = ("np", "nii", "fee", "prov", "opex", "pretax")
STATE_DIR = "release_watch"
MANUAL, CALC, HELD = "manual", "calc", "accepted"
IDENTITY = "identity"     # метка приёма: число одного вида подтверждено тождеством нарастающего итога
# Ведущее значение группы — вид, повторяющий релиз (таблица, текст); форма 0409102 — подтверждение.
LEAD_ORDER = ("pdf", "interfax", "smartlab", "outlet")
# Метрики словами — для строк, которые печатает витрина (причины деградации, плашка `ras_mismatch`; П§0.2).
FLOW_WORDS = {"np": "чистая прибыль", "nii": "чистый процентный доход", "fee": "чистый комиссионный доход",
              "prov": "резервы", "opex": "операционные расходы", "pretax": "прибыль до налога"}
RATIO_WORDS = {"roe": "ROE", "cor": "стоимость риска", "cir": "CIR"}
STOCK_WORDS = {"loans_ul": "кредиты юрлицам", "loans_fl": "кредиты физлицам", "funds_ul": "средства юрлиц",
               "funds_fl": "средства физлиц", "cap_base": "базовый капитал", "cap_main": "основной капитал",
               "cap_total": "общий капитал", "n1_0": "Н1.0", "n1_1": "Н1.1", "n1_2": "Н1.2"}
SPAN_WORDS = {"ytd": "с начала года", "m": "за месяц"}


def metric_words(metric: str) -> str:
    """Метрика релиза словами: «np_ytd» → «чистая прибыль с начала года»."""
    head, _, span = metric.rpartition("_")
    words = FLOW_WORDS.get(head) or RATIO_WORDS.get(head)
    if words and span in SPAN_WORDS:
        return f"{words} {SPAN_WORDS[span]}"
    return STOCK_WORDS.get(metric, metric)


def series_id(metric: str) -> str:
    return f"ras.release.{metric}"


def tolerance(metric: str, cfg: dict[str, Any]) -> float:
    tol = cfg.get("tolerance") or {}
    return float(tol.get("share" if metric in SHARE_METRICS else "money", 0.0005 if metric in SHARE_METRICS else 0.15))


def release_days(month: str) -> tuple[date, date]:
    """Когда выходит релиз месяца: после его конца и не позже конца следующего — (после, по)."""
    return periods.month_end(month), periods.month_end(periods.shift_month(month, 1))


def gather(ctx: Context, month: str) -> dict[str, list[dict[str, Any]]]:
    """Кандидаты по метрикам: [{kind, value, ref}] из новостей, PDF, формы 102 (ручной ввод добавляет `watch`)."""
    cands: dict[str, list[dict[str, Any]]] = {}
    _, number = periods.parse_month(month)
    after, until = release_days(month)
    items = (ctx.store.read_state("news/items.json") or {}).get("items") or {}
    for url, item in items.items():
        day = news.published_day(item.get("published") or item.get("first_seen"))
        if day is None or not after < day <= until:
            continue                    # релиз месяца выходит в следующем месяце: текст другого дня — не о нём
        if item.get("topic") == "dividend":
            continue                    # новость о дивидендах — вход реестра, не релиза РСБУ
        kind = str(item.get("kind") or "outlet")
        text = item.get("text") or ""
        if not text or number not in news.period_months(text):
            continue                    # текст о другом месяце кандидатов не даёт
        found = news.extract_release_numbers(text, month=month)
        if kind.startswith("telegram"):
            for metric, got in news.extract_headline(text, month=month).items():
                found.setdefault(metric, got)
        for metric, (value, phrase) in found.items():
            cands.setdefault(metric, []).append({"kind": kind, "value": value, "ref": url, "phrase": phrase})
    for key, cand in ((ctx.store.read_state("sber_pdf/candidates.json") or {}).items()):
        if cand.get("kind") != "ras_release" or cand.get("month") != month or cand.get("status") != "ok":
            continue
        for metric, value in (cand.get("numbers") or {}).items():
            if metric in SHARE_METRICS and value is not None and value > 1:
                value = round(value / 100.0, 10)
            cands.setdefault(metric, []).append({"kind": "pdf", "value": value, "ref": cand.get("url"),
                                                 "sha256": cand.get("sha256")})
    as_of = ctx.today.isoformat()
    for metric, sid in (("np_ytd", "cbr.f102.ni_ytd"), ("pretax_ytd", "cbr.f102.pretax_ytd")):
        f = ctx.store.load(sid)
        v = None if f is None else f.value_as_of(month, as_of)
        if v is not None:
            cands.setdefault(metric, []).append({"kind": "form102", "value": round(v, 4), "ref": "cbr:0409102"})
    return cands


def _kinds(rows: list[dict]) -> set[str]:
    return {r["kind"] for r in rows}


def decide(cands: dict[str, list[dict[str, Any]]], cfg: dict[str, Any]) -> tuple[dict, dict]:
    """(принятые {метрика: {value, kinds}}, расхождения {метрика: [кандидаты]})."""
    accepted, mismatches = {}, {}
    for metric, rows in cands.items():
        manual = [r for r in rows if r["kind"] == MANUAL]
        if manual:
            accepted[metric] = {"value": manual[-1]["value"], "kinds": [MANUAL]}
            continue
        tol = tolerance(metric, cfg)
        groups: list[list[dict]] = []
        for r in sorted(rows, key=lambda r: r["value"]):
            if groups and abs(r["value"] - groups[-1][0]["value"]) <= tol:
                groups[-1].append(r)
            else:
                groups.append([r])
        agreed = [g for g in groups if len(_kinds(g)) >= 2]
        if len(agreed) == 1:
            lead = min(agreed[0], key=lambda r: (r["kind"] == "form102",
                                                 LEAD_ORDER.index(r["kind"]) if r["kind"] in LEAD_ORDER
                                                 else len(LEAD_ORDER)))
            accepted[metric] = {"value": lead["value"], "kinds": sorted(_kinds(agreed[0]))}
        elif agreed or len(_kinds(rows)) >= 2:
            # ни одна группа не набрала двух видов — или набрали две разные: выбирать нечем
            mismatches[metric] = rows
    return accepted, mismatches


def single_kind(rows: list[dict[str, Any]], tol: float) -> tuple[float, str] | None:
    """(значение, вид), если метрику называет ровно один вид источника и все его числа — в допуске; иначе None."""
    kinds = _kinds(rows)
    if len(kinds) != 1:
        return None
    vals = sorted(r["value"] for r in rows)
    if vals[-1] - vals[0] > tol:
        return None
    return vals[len(vals) // 2], next(iter(kinds))


def accept_by_identity(store: Store, month: str, cands: dict[str, list[dict[str, Any]]], accepted: dict,
                       mismatches: dict, cfg: dict[str, Any]) -> list[str]:
    """Приём потока, который называет один вид источника, по тождеству нарастающего итога (INDICATORS §5).

    Для потока P&L пара «нарастающее — месяц» берётся из принятого двумя видами или, если его нет, из
    единственного вида (`single_kind`); оба числа есть, НМ прошлого месяца лежит в ряду и
    |НМ прошлого месяца + месяц − НМ| ≤ `tolerance.identity` — непринятые числа пары принимаются с видами
    `[<вид>, identity]`. Январь не принимается (НМ и месяц там одно число), расхождение — тоже.
    Возвращает принятые так метрики; `accepted` дополняется на месте."""
    _, number = periods.parse_month(month)
    if number == 1:
        return []
    tol = float((cfg.get("tolerance") or {}).get("identity", 0.35))
    prev_month = periods.shift_month(month, -1)
    done: list[str] = []
    for flow in FLOWS:
        pair = (f"{flow}_ytd", f"{flow}_m")
        if all(k in accepted for k in pair) or any(k in mismatches for k in pair):
            continue
        got: dict[str, tuple[float, str | None]] = {}
        for key in pair:
            if key in accepted:
                got[key] = (accepted[key]["value"], None)
            else:
                one = single_kind(cands.get(key) or [], tolerance(key, cfg))
                if one is not None:
                    got[key] = one
        if len(got) < 2:
            continue
        prev = _held(store, pair[0], prev_month)
        ytd, mon = got[pair[0]][0], got[pair[1]][0]
        if prev is None or abs(prev + mon - ytd) > tol:
            continue
        for key, (value, kind) in got.items():
            if kind is not None:
                accepted[key] = {"value": value, "kinds": [kind, IDENTITY]}
                done.append(key)
    return done


def _held(store: Store, metric: str, month: str) -> float | None:
    s = store.load(series_id(metric))
    return None if s is None else s.value_as_of(month)


def _by_hand(acc: dict[str, Any]) -> bool:
    """Число внесено руками или выведено из внесённого руками."""
    return acc["kinds"] == [MANUAL] or bool(acc.get("manual"))


def derive_month(store: Store, month: str, accepted: dict[str, dict]) -> None:
    """Недостающая половина пары потока: в январе нарастающее и месяц — одно число; дальше месяц —
    разность нарастающих, если месяц не принят, а оба нарастающих известны."""
    _, m = periods.parse_month(month)
    for flow in FLOWS:
        ytd, mon = accepted.get(f"{flow}_ytd"), accepted.get(f"{flow}_m")
        if m == 1:
            src = ytd or mon
            if src is not None and (ytd is None or mon is None):
                accepted[f"{flow}_m" if mon is None else f"{flow}_ytd"] = {
                    "value": src["value"], "kinds": [CALC], "manual": _by_hand(src)}
            continue
        if mon is not None or ytd is None:
            continue
        prev = _held(store, f"{flow}_ytd", periods.shift_month(month, -1))
        if prev is not None:
            accepted[f"{flow}_m"] = {"value": round(ytd["value"] - prev, 4), "kinds": [CALC],
                                     "manual": _by_hand(ytd)}


def identity_failures(store: Store, month: str, accepted: dict[str, dict], cfg: dict[str, Any]) -> dict[str, str]:
    """Тождество «НМ = НМ прошлого месяца + месяц» для потоков P&L (январь: НМ = месяц) → {поток: что не сошлось}."""
    tol = float((cfg.get("tolerance") or {}).get("identity", 0.35))
    out: dict[str, str] = {}
    _, m = periods.parse_month(month)
    for flow in FLOWS:
        ytd = accepted.get(f"{flow}_ytd", {}).get("value")
        mon = accepted.get(f"{flow}_m", {}).get("value")
        if ytd is None or mon is None:
            continue
        prev = 0.0 if m == 1 else _held(store, f"{flow}_ytd", periods.shift_month(month, -1))
        if prev is not None and abs(prev + mon - ytd) > tol:
            out[flow] = f"{FLOW_WORDS[flow]}: с начала года {ytd} ≠ {prev} + {mon}"
    return out


def watch(ctx: Context, month: str) -> dict[str, Any]:
    """Один проход по месяцу: кандидаты → приём → ряды → состояние дня."""
    cfg = ctx.cfg.get("release_watch") or {}
    name = f"{STATE_DIR}/{month}.json"
    state = ctx.store.read_state(name) or {"month": month, "accepted": {}, "first_candidate": None}
    manual = ctx.store.read_state(f"{STATE_DIR}/manual_{month}.json") or {}
    cands = gather(ctx, month)
    for metric, value in manual.items():
        cands.setdefault(metric, []).append({"kind": MANUAL, "value": value["value"], "ref": value.get("source")})
    accepted, mismatches = decide(cands, cfg)
    by_identity = accept_by_identity(ctx.store, month, cands, accepted, mismatches, cfg)
    derive_month(ctx.store, month, accepted)
    failures = identity_failures(ctx.store, month, accepted, cfg)
    now = ctx.moment()
    known_before = _held(ctx.store, "np_ytd", month) is not None
    new = []
    for metric, acc in accepted.items():
        by_hand = _by_hand(acc)
        held = _held(ctx.store, metric, month)
        prev = state["accepted"].get(metric)
        if held is not None and not by_hand:
            # В ряду уже есть число месяца: автомат его подтверждает, но не переписывает.
            if abs(held - acc["value"]) <= tolerance(metric, cfg):
                if prev is None:
                    state["accepted"][metric] = {"value": held, "kinds": acc["kinds"], "at": now}
            elif acc["kinds"] != [CALC]:
                mismatches[metric] = [{"kind": HELD, "value": held, "ref": series_id(metric)}] + cands.get(metric, [])
            continue
        flow, _, part = metric.rpartition("_")
        if not by_hand and part in ("ytd", "m") and flow in failures:
            continue                    # поток не прошёл тождество — в ряд не пишется
        if prev is not None and prev["kinds"] == acc["kinds"] and abs(prev["value"] - acc["value"]) <= 1e-9:
            continue
        ctx.store.upsert(series_id(metric), [point(month, acc["value"], fetched_at=now,
                                                  status="manual" if by_hand else "ok",
                                                  source="release:" + "+".join(acc["kinds"]))],
                         unit="share" if metric in SHARE_METRICS else "RUB bn", basis="ras",
                         label=f"релиз РСБУ банка: {metric}")
        state["accepted"][metric] = {"value": acc["value"], "kinds": acc["kinds"], "at": now}
        new.append(metric)
    if cands and not state.get("first_candidate"):
        state["first_candidate"] = now
    state.update({"checked_at": now, "candidates": cands, "mismatches": mismatches,
                  "identity_failures": list(failures.values()), "accepted_by_identity": sorted(by_identity)})
    ctx.store.write_state(name, state)
    return {"month": month, "new": new, "mismatches": sorted(mismatches),
            "identity_failures": list(failures.values()), "accepted_by_identity": sorted(by_identity),
            "complete": "np_ytd" in state["accepted"],
            "month_accepted": "np_ytd" in new and not known_before}


def pending_months(store: Store, today: date, lookback: int = 2) -> list[str]:
    """Месяцы окна дозора: последние `lookback` месяцев (проход дешёвый и идемпотентный —
    поздние источники дня релиза подтверждают метрики и после приёма ЧП)."""
    return [periods.shift_month(periods.month_of(today), -back) for back in range(lookback, 0, -1)]


def run(ctx: Context) -> Result:
    """Такт дозора: месяцы окна по очереди, старший первым (тождеству нужен прошлый месяц)."""
    res = Result(name="release_watch")
    for month in pending_months(ctx.store, ctx.today):
        out = watch(ctx, month)
        res.data.setdefault("months", []).append(out)
        if out["new"]:
            res.series += [series_id(m) for m in out["new"]]
        if out["mismatches"]:
            res.degrade(f"РСБУ {month}: источники разошлись — {', '.join(map(metric_words, out['mismatches']))}")
        if out["identity_failures"]:
            res.degrade(f"РСБУ {month}: нарастающий итог не равен прошлому месяцу плюс месяц — "
                        f"{'; '.join(out['identity_failures'])}")
    return res


def mismatch_flag(store: Store, months: list[str], *, tolerance_bn: float = 0.15) -> dict[str, Any]:
    """Флаг `ras_mismatch`: расхождение двух источников дня релиза или релиза с формой 0409102."""
    details = []
    rel = store.load(series_id("np_ytd"))
    f102 = store.load("cbr.f102.ni_ytd")
    for m in months:
        st = store.read_state(f"{STATE_DIR}/{m}.json") or {}
        if st.get("mismatches"):
            details.append(f"{m}: источники дня релиза разошлись "
                           f"({', '.join(metric_words(k) for k in sorted(st['mismatches']))})")
        r = None if rel is None else rel.value_as_of(m)
        f = None if f102 is None else f102.value_as_of(m)
        if r is not None and f is not None and abs(r - f) > tolerance_bn:
            details.append(f"{m}: ЧП релиза {r:.1f} против формы 0409102 {f:.1f} млрд ₽")
    return {"raised": bool(details), "detail": "; ".join(details)}


def form102_rows(store: Store, months: list[str], *, tolerance_bn: float = 0.15) -> list[dict[str, Any]]:
    """П§2 `nowcast.form102`: ЧП месяца по форме 0409102 против релиза (млрд ₽)."""
    f = store.load("cbr.f102.ni_ytd")
    r = store.load(series_id("np_m"))
    if f is None:
        return []
    monthly = cbr_forms.month_from_ytd(f.history())
    out = []
    for m in months:
        ni = monthly.get(m)
        if ni is None:
            continue
        rel = None if r is None else r.value_as_of(m)
        seen = f.first_seen(m)
        diff = None if rel is None else round(ni - rel, 4)
        out.append({"month": m, "ni": round(ni, 4), "first_seen": seen, "release_ni": rel, "diff": diff,
                    "ok": None if diff is None else abs(diff) <= tolerance_bn})
    return out
