"""T-Invest REST (токен «только чтение» из `TINVEST_TOKEN`): цены, дивиденды, календарь отчётов, цели брокеров.

Токен читается из окружения на каждый такт и никуда не пишется: ни в сырой архив
(там только тела ответов и адрес), ни в журналы, ни в выпуск. Нет токена — источник
`missing`, цены берёт запасной ISS. Хосты T-Invest подписаны УЦ Минцифры — корень
закреплён только для них (`indicators/http.py::EXTRA_CA`). Токен нужен одному этому
сборщику: такт запускает его отдельной командой (`collect --only tinvest`), а в общем
запуске он идёт первым и после него токен из окружения процесса убирается
(`forget_token`) — новости и PDF разбираются без секретов (INTERFACES §8).

Сырые снимки (цели брокеров, дивиденды, календарь) — приватный защищённый архив:
истории у источника нет. В выпуск идут только агрегаты (П§2 `market.brokers`: число
целей, медиана, минимум, максимум, счёт рекомендаций) — условия T запрещают
публиковать ответы API (DESIGN §6.5).
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

from indicators import http
from indicators.sources import FAILED, MISSING, Context, Result
from indicators.store import point

BASE = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1."
TOKEN_ENV = "TINVEST_TOKEN"
SOURCE = "tinvest"
INSTRUMENTS_FILE = "tinvest/instruments.json"
DIVIDENDS_FILE = "tinvest/dividends.json"
# Москва без перехода на летнее время с 2014 года: фиксированный сдвиг (tzdata не нужен).
MSK = timezone(timedelta(hours=3), "MSK")
RECOMMENDATIONS = {"RECOMMENDATION_BUY": "buy", "RECOMMENDATION_HOLD": "hold", "RECOMMENDATION_SELL": "sell"}


class TinvestError(RuntimeError):
    """Ответ T-Invest пришёл, но не годится."""


def token() -> str | None:
    value = (os.environ.get(TOKEN_ENV) or "").strip()
    return value or None


def forget_token() -> None:
    """Убирает токен из окружения процесса: сборщик своё снял, разбору чужого ввода секрет не нужен."""
    os.environ.pop(TOKEN_ENV, None)


def quotation(x: dict | None) -> float | None:
    """Quotation/MoneyValue {units, nano} → число."""
    if not x:
        return None
    try:
        return int(x.get("units", "0")) + int(x.get("nano", 0)) / 1e9
    except (TypeError, ValueError):
        return None


def call(method: str, body: dict, *, getter=None, sink=None, name: str | None = None,
         base: str = BASE) -> dict:
    tok = token()
    if tok is None:
        raise TinvestError(f"нет {TOKEN_ENV}")
    resp = (getter or http.fetch)(base + method, data=json.dumps(body).encode("utf-8"),
                                  headers={"Authorization": "Bearer " + tok,
                                           "Content-Type": "application/json", "Accept": "application/json"},
                                  sink=sink, name=name)
    return resp.json()


def why(exc: Exception) -> str:
    """Причина отказа для строк деградации (их печатает витрина, П§0.2): у сетевого отказа — без адреса
    API, в котором стоит имя метода; что именно не снято, строка называет словами сама."""
    return str(exc).split(": ", 1)[-1] if isinstance(exc, http.FetchError) else str(exc)


# ------------------------------------------------------------------ разбор

def parse_share(payload: dict) -> dict[str, Any]:
    ins = payload.get("instrument") or {}
    if not ins.get("uid"):
        raise TinvestError("в ответе нет идентификатора инструмента")
    return {"uid": ins["uid"], "figi": ins.get("figi"), "ticker": ins.get("ticker"),
            "class_code": ins.get("classCode"), "issue_size": ins.get("issueSize")}


def _msk(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(MSK)


def parse_last_prices(payload: dict) -> dict[str, dict[str, Any]]:
    """uid → {price, date (МСК), time (ЧЧ:ММ МСК), ticker}."""
    out = {}
    for p in payload.get("lastPrices") or []:
        price = quotation(p.get("price"))
        if not price or not p.get("time"):
            continue
        moment = _msk(p["time"])
        out[p.get("instrumentUid")] = {"price": price, "date": moment.date().isoformat(),
                                       "time": moment.strftime("%H:%M"), "ticker": p.get("ticker")}
    return out


def parse_dividends(payload: dict, *, profit_year_lag: int = 1) -> list[dict[str, Any]]:
    """Записи дивидендов: `dividendNet` = валовой DPS; экс-дата = отсечка (М§15).

    Поле `declaredDate` ненадёжно (М§15) — дата решения из него не берётся. Год
    прибыли — год отсечки минус `profit_year_lag` (sources.yaml).
    """
    out = []
    for d in payload.get("dividends") or []:
        dps = quotation(d.get("dividendNet"))
        record = (d.get("recordDate") or "")[:10] or None
        if dps is None or record is None:
            continue
        out.append({"year": int(record[:4]) - profit_year_lag, "dps": dps, "status": "declared",
                    "record_date": record, "ex_date": record,
                    "last_buy_date": (d.get("lastBuyDate") or "")[:10] or None,
                    "pay_date": (d.get("paymentDate") or "")[:10] or None,
                    "decided_date": None, "created_at": d.get("createdAt"),
                    "regularity": d.get("regularity"), "sources": ["T-Invest"]})
    return sorted(out, key=lambda r: r["record_date"])


def parse_reports(payload: dict) -> list[dict[str, Any]]:
    """Календарь отчётов: {date, year, num, type, created_at}; дубли снимаются."""
    seen, out = set(), []
    for e in payload.get("events") or []:
        day = (e.get("reportDate") or "")[:10]
        key = (day, e.get("periodYear"), e.get("periodNum"), e.get("periodType"))
        if not day or key in seen:
            continue
        seen.add(key)
        out.append({"date": day, "year": e.get("periodYear"), "num": e.get("periodNum"),
                    "type": e.get("periodType"), "created_at": e.get("createdAt")})
    return sorted(out, key=lambda r: r["date"])


def brokers_aggregate(payload: dict, *, as_of: str) -> dict[str, Any]:
    """Только агрегаты целей брокеров (П§2 `market.brokers`); цели по брокерам не выходят."""
    targets = [quotation(t.get("targetPrice")) for t in payload.get("targets") or []]
    targets = [t for t in targets if t]
    recs = {"buy": 0, "hold": 0, "sell": 0}
    for t in payload.get("targets") or []:
        key = RECOMMENDATIONS.get(t.get("recommendation"))
        if key:
            recs[key] += 1
    if not targets:
        return {"as_of": as_of, "source": "T-Invest", "n": 0, "median": None, "min": None, "max": None,
                "recommendations": recs}
    return {"as_of": as_of, "source": "T-Invest", "n": len(targets), "median": statistics.median(targets),
            "min": min(targets), "max": max(targets), "recommendations": recs,
            "consensus": quotation((payload.get("consensus") or {}).get("consensus"))}


# ------------------------------------------------------------------ сборщик

def instruments(ctx: Context, tickers: list[str], *, getter, sink, class_code: str,
                errors: list[tuple[str, bool]] | None = None) -> dict[str, dict]:
    """uid инструментов по тикерам (кэш в состоянии; тикеры — из книги и фактов аналогов).

    Не найденный тикер пишется в `errors` (причина, снимет ли её повтор) и пропускается: отказ по
    аналогу не должен останавливать сбор по эмитенту (его отсутствие проверяет вызывающий).
    """
    cache = ctx.store.read_state(INSTRUMENTS_FILE) or {}
    changed = False
    for t in tickers:
        if t in cache:
            continue
        try:
            payload = call("InstrumentsService/ShareBy",
                           {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": class_code, "id": t},
                           getter=getter, sink=sink, name=f"shareby_{t}.json")
            cache[t] = parse_share(payload)
            changed = True
        except (http.FetchError, TinvestError, ValueError) as exc:
            if errors is None:
                raise
            errors.append((f"{t}: {why(exc)}", http.retryable(exc)))
    if changed:
        ctx.store.write_state(INSTRUMENTS_FILE, cache)
    return {t: cache[t] for t in tickers if t in cache}


def collect(ctx: Context) -> Result:
    res = Result(name="tinvest")
    if token() is None:
        res.status, res.detail = MISSING, f"нет {TOKEN_ENV}: цены — запасной ISS, дивиденды и цели брокеров не сняты"
        return res
    cfg = ctx.cfg.get("tinvest") or {}
    getter = ctx.getter or http.fetch
    sink = http.Sink(ctx.store, SOURCE)
    now = ctx.moment()
    tickers = list(ctx.company["tickers"]) + [t for t in ctx.peer_tickers if t not in ctx.company["tickers"]]
    errors: list[tuple[str, bool]] = []
    ids = instruments(ctx, tickers, getter=getter, sink=sink, class_code=cfg.get("class_code", "TQBR"),
                      errors=errors)
    if any(t not in ids for t in ctx.company["tickers"]):
        res.status, res.detail = FAILED, "инструменты: " + "; ".join(e for e, _ in errors)
        return res
    for e, again in errors:
        res.degrade(f"инструмент аналога не найден — {e}", retry=again)
    by_uid = {v["uid"]: t for t, v in ids.items()}
    try:
        prices = parse_last_prices(call("MarketDataService/GetLastPrices", {"instrumentId": list(by_uid)},
                                        getter=getter, sink=sink, name="last_prices.json"))
        for uid, p in prices.items():
            t = by_uid.get(uid)
            if t is None:
                continue
            sid = f"tinvest.price.{t}"
            ctx.store.upsert(sid, [point(p["date"], p["price"], fetched_at=now, source="tinvest",
                                         note=p["time"])], unit="RUB", label=f"цена {t}, T-Invest")
            res.series.append(sid)
        for t in ctx.company["tickers"]:
            if ids.get(t, {}).get("uid") not in prices:
                res.degrade(f"T-Invest: нет цены {t}", retry=False)          # ответ пришёл — цены в нём нет
    except (http.FetchError, TinvestError, ValueError) as exc:
        res.degrade(f"цены: {why(exc)}", retry=http.retryable(exc))
    lag = int((ctx.cfg.get("dividends") or {}).get("profit_year_lag", 1))
    # Записи копятся по тикерам: отказ или пустой ответ по одному тикеру не стирает то, что уже снято.
    held = ctx.store.read_state(DIVIDENDS_FILE) or {}
    register: dict[str, list] = {t: list(rows or []) for t, rows in (held.get("by_ticker") or {}).items()}
    stale: dict[str, str] = dict(held.get("stale_since") or {})
    for t in ctx.company["tickers"]:
        uid = ids.get(t, {}).get("uid")
        if not uid:
            continue
        span = {"from": f"{(ctx.today - timedelta(days=int(cfg.get('dividends_days', 800)))).isoformat()}T00:00:00Z",
                "to": f"{(ctx.today + timedelta(days=400)).isoformat()}T00:00:00Z"}
        try:
            rows = parse_dividends(call("InstrumentsService/GetDividends", {"instrumentId": uid, **span},
                                        getter=getter, sink=sink, name=f"dividends_{t}.json"),
                                   profit_year_lag=lag)
            if not rows and register.get(t):
                raise TinvestError("пустой ответ при непустых прежних записях")
            register[t] = rows
            stale.pop(t, None)
        except (http.FetchError, TinvestError, ValueError) as exc:
            if register.get(t):
                stale.setdefault(t, str(held.get("fetched_at") or now))
                res.degrade(f"дивиденды {t}: {why(exc)} — оставлены записи от {stale[t]}", retry=http.retryable(exc))
            else:
                res.degrade(f"дивиденды {t}: {why(exc)}", retry=http.retryable(exc))
        try:
            span_r = {"from": f"{(ctx.today - timedelta(days=400)).isoformat()}T00:00:00Z",
                      "to": f"{(ctx.today + timedelta(days=400)).isoformat()}T00:00:00Z"}
            reports = parse_reports(call("InstrumentsService/GetAssetReports", {"instrumentId": uid, **span_r},
                                         getter=getter, sink=sink, name=f"reports_{t}.json"))
            if t == ctx.company["main_ticker"]:
                ctx.store.write_state("tinvest/reports.json", {"fetched_at": now, "events": reports})
        except (http.FetchError, TinvestError, ValueError) as exc:
            res.degrade(f"календарь отчётов {t}: {why(exc)}", retry=http.retryable(exc))
        try:
            agg = brokers_aggregate(call("InstrumentsService/GetForecastBy", {"instrumentId": uid},
                                         getter=getter, sink=sink, name=f"forecast_{t}.json"),
                                    as_of=ctx.today.isoformat())
            for key in ("n", "median", "min", "max"):
                sid = f"tinvest.brokers.{key}.{t}"
                ctx.store.upsert(sid, [point(agg["as_of"], agg[key], fetched_at=now, source="tinvest")],
                                 unit="RUB" if key != "n" else "count", label=f"цели брокеров {t}: {key}")
                res.series.append(sid)
            ctx.store.write_state(f"tinvest/brokers_{t}.json", agg)
        except (http.FetchError, TinvestError, ValueError) as exc:
            res.degrade(f"цели брокеров {t}: {why(exc)}", retry=http.retryable(exc))
    if register:
        # `stale_since` — тикер → момент последнего удачного снимка (пока его записи не обновляются).
        ctx.store.write_state(DIVIDENDS_FILE, {"fetched_at": now, "by_ticker": register, "stale_since": stale})
    return res


def msk_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(MSK)
