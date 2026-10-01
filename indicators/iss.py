"""MOEX ISS: запасные цены категорий и аналогов, дневные закрытия, кривая zcyc, индексы.

Бесплатный ISS отдаёт рынок с задержкой 15 минут («только для ознакомления»: в
выпуск — только точки, которые печатает витрина). Разбор — чистые `parse_*`
(тесты на сохранённых ответах), сбор — с подменяемым `getter`. Единицы: цены — ₽,
ставки кривой — доли, даты — ISO, время сделки — ЧЧ:ММ МСК.
"""

from __future__ import annotations

import urllib.parse
from datetime import date, timedelta
from typing import Any

from indicators import http
from indicators.sources import FAILED, Context, Result
from indicators.store import point

ISS = "https://iss.moex.com/iss"
SOURCE = "iss"
CURVE_NODES = ("1", "3", "5", "10")
# Узел кривой вне 3–40 % годовых — чужие единицы или мусор (коридор семейства).
RATE_MIN, RATE_MAX = 0.03, 0.40
MAX_HISTORY_PAGES = 20
HISTORY_DAYS = 370


class IssError(RuntimeError):
    """Ответ ISS пришёл, но не годится."""


def _rows(payload: dict, block: str) -> list[dict]:
    try:
        part = payload[block]
        return [dict(zip(part["columns"], row)) for row in part["data"]]
    except (KeyError, TypeError) as exc:
        raise IssError(f"в ответе нет блока {block!r}") from exc


def _day(stamp: Any) -> str | None:
    try:
        return date.fromisoformat(str(stamp)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _q(params: dict) -> str:
    return urllib.parse.urlencode(params, safe=",.")


# ------------------------------------------------------------------ котировки

def quotes_url(tickers, board: str) -> str:
    return (f"{ISS}/engines/stock/markets/shares/boards/{board}/securities.json?" + _q({
        "iss.meta": "off", "iss.only": "securities,marketdata", "securities": ",".join(tickers),
        "securities.columns": "SECID,SHORTNAME,PREVPRICE,PREVLEGALCLOSEPRICE,PREVDATE,ISSUESIZE",
        "marketdata.columns": "SECID,LAST,TIME,UPDATETIME,SYSTIME,TRADINGSTATUS"}))


def parse_quotes(payload: dict, board: str) -> dict[str, dict]:
    """Идут торги — последняя сделка (`LAST`, дата `SYSTIME`, время `TIME`); нет — прошлое закрытие."""
    securities = {r["SECID"]: r for r in _rows(payload, "securities")}
    market = {r["SECID"]: r for r in _rows(payload, "marketdata")}
    out = {}
    for secid, sec in securities.items():
        md = market.get(secid) or {}
        last, day = _positive(md.get("LAST")), _day(md.get("SYSTIME"))
        if last is not None and day is not None:
            t = md.get("TIME") or md.get("UPDATETIME")
            quote = {"price": last, "date": day, "time": str(t)[:5] if t else None, "kind": "last"}
        else:
            prev = _positive(sec.get("PREVLEGALCLOSEPRICE")) or _positive(sec.get("PREVPRICE"))
            quote = {"price": prev, "date": _day(sec.get("PREVDATE")), "time": None,
                     "kind": "prev" if prev else "none"}
        quote.update(source=f"iss:{board}", issue_size=sec.get("ISSUESIZE"))
        out[str(secid)] = quote
    return out


# ------------------------------------------------------------ история закрытий

def history_url(secid: str, board: str, start: date, till: date, offset: int = 0) -> str:
    return (f"{ISS}/history/engines/stock/markets/shares/boards/{board}/securities/{secid}.json?"
            + _q({"iss.meta": "off", "from": start.isoformat(), "till": till.isoformat(),
                  "start": offset, "history.columns": "TRADEDATE,LEGALCLOSEPRICE,CLOSE,VOLUME"}))


def parse_history_page(payload: dict) -> tuple[list[dict], tuple[int, int, int] | None]:
    """Страница истории → ([{date, close}], курсор). Закрытие — `LEGALCLOSEPRICE`, иначе `CLOSE`."""
    rows = []
    for row in _rows(payload, "history"):
        day = _day(row.get("TRADEDATE"))
        close = _positive(row.get("LEGALCLOSEPRICE")) or _positive(row.get("CLOSE"))
        if day and close:
            rows.append({"date": day, "close": close})
    cursor = None
    if "history.cursor" in payload:
        c = _rows(payload, "history.cursor")
        if c:
            cursor = (int(c[0]["INDEX"]), int(c[0]["TOTAL"]), int(c[0]["PAGESIZE"]))
    return rows, cursor


def _pages(url_of, name_of, getter, sink) -> list[dict]:
    by_day: dict[str, float] = {}
    offset = 0
    for _ in range(MAX_HISTORY_PAGES):
        resp = getter(url_of(offset), sink=sink, name=name_of(offset))
        rows, cursor = parse_history_page(resp.json())
        for r in rows:
            by_day[r["date"]] = r["close"]
        if cursor is None:
            if len(rows) < 100:
                break
            offset += len(rows)
            continue
        index, total, size = cursor
        if size <= 0 or index + size >= total:
            break
        offset = index + size
    else:
        raise IssError(f"больше {MAX_HISTORY_PAGES} страниц — обход прерван")
    return [{"date": d, "close": by_day[d]} for d in sorted(by_day)]


def fetch_history(secid: str, board: str, *, start: date, till: date, getter=None, sink=None) -> list[dict]:
    g = getter or http.fetch
    return _pages(lambda o: history_url(secid, board, start, till, o),
                  lambda o: f"history_{secid}_{o}.json", g, sink)


# ------------------------------------------------------------ кривая zcyc

ZCYC_URL = f"{ISS}/engines/stock/zcyc.json?iss.meta=off&iss.only=params,yearyields"


def parse_zcyc(payload: dict) -> dict:
    """Узлы 1/3/5/10 лет бескупонной кривой на последнюю дату ответа (доли)."""
    rows = _rows(payload, "yearyields")
    days = [d for d in (_day(r.get("tradedate")) for r in rows) if d]
    if not days:
        raise IssError("zcyc: нет ни одной даты")
    as_of = max(days)
    nodes, time_ = {}, None
    for r in rows:
        if _day(r.get("tradedate")) != as_of:
            continue
        try:
            key, value = f"{float(r['period']):g}", round(float(r["value"]) / 100.0, 10)
        except (KeyError, TypeError, ValueError):
            continue
        if key in CURVE_NODES:
            if not RATE_MIN <= value <= RATE_MAX:
                raise IssError(f"zcyc: узел {key} лет = {r['value']} вне {RATE_MIN:.0%}–{RATE_MAX:.0%}")
            nodes[key] = value
            time_ = r.get("tradetime") or time_
    missing = [k for k in CURVE_NODES if k not in nodes]
    if missing:
        raise IssError(f"zcyc {as_of}: нет узлов {', '.join(missing)}")
    return {"as_of": as_of, "time": time_, "nodes": {k: nodes[k] for k in CURVE_NODES}, "source": "iss:zcyc"}


# ------------------------------------------------------------ индексы

def index_url(secid: str, start: date, till: date, offset: int = 0) -> str:
    return (f"{ISS}/history/engines/stock/markets/index/securities/{secid}.json?"
            + _q({"iss.meta": "off", "from": start.isoformat(), "till": till.isoformat(), "start": offset,
                  "history.columns": "TRADEDATE,CLOSE"}))


def fetch_index(secid: str, *, start: date, till: date, getter=None, sink=None) -> list[dict]:
    g = getter or http.fetch
    return _pages(lambda o: index_url(secid, start, till, o), lambda o: f"index_{secid}_{o}.json", g, sink)


# ------------------------------------------------------------ сборщик

def collect(ctx: Context) -> Result:
    """Цены категорий и аналогов, закрытия за год, кривая, индексы → ряды `iss.*`."""
    cfg = ctx.cfg.get("iss") or {}
    board = cfg.get("board", "TQBR")
    getter = ctx.getter or http.fetch
    sink = http.Sink(ctx.store, SOURCE)
    res = Result(name="iss")
    now = ctx.moment()
    tickers = list(ctx.company["tickers"]) + [t for t in ctx.peer_tickers if t not in ctx.company["tickers"]]
    try:
        quotes = parse_quotes(getter(quotes_url(tickers, board), sink=sink, name="quotes.json").json(), board)
        for t, q in quotes.items():
            if q["price"] is None or q["date"] is None:
                res.degrade(f"ISS: нет цены {t}")
                continue
            sid = f"iss.price.{t}"
            ctx.store.upsert(sid, [point(q["date"], q["price"], fetched_at=now, source=q["source"],
                                         note=q["time"] or "")], unit="RUB", label=f"цена {t}, ISS")
            res.series.append(sid)
        for t in ctx.company["tickers"]:
            if t not in quotes:
                res.degrade(f"ISS: в ответе нет {t}")
    except (http.FetchError, IssError, ValueError) as exc:
        res.status, res.detail = FAILED, f"котировки: {exc}"
        return res
    start = ctx.today - timedelta(days=HISTORY_DAYS)
    for t in ctx.company["tickers"]:
        try:
            rows = fetch_history(t, board, start=start, till=ctx.today, getter=getter, sink=sink)
            sid = f"iss.close.{t}"
            ctx.store.upsert(sid, [point(r["date"], r["close"], fetched_at=now, source=f"iss:{board}")
                                   for r in rows], unit="RUB", label=f"закрытие {t}")
            res.series.append(sid)
        except (http.FetchError, IssError, ValueError) as exc:
            res.degrade(f"ISS: история {t} — {exc}")
    try:
        curve = parse_zcyc(getter(ZCYC_URL, sink=sink, name="zcyc.json").json())
        for node, value in curve["nodes"].items():
            sid = f"iss.zcyc.{node}"
            ctx.store.upsert(sid, [point(curve["as_of"], value, fetched_at=now, source="iss:zcyc")],
                             unit="share", label=f"КБД ОФЗ {node} лет")
            res.series.append(sid)
        res.data["curve"] = curve
    except (http.FetchError, IssError, ValueError) as exc:
        res.degrade(f"ISS: кривая — {exc}")
    for secid in cfg.get("indices") or ():
        try:
            rows = fetch_index(secid, start=start, till=ctx.today, getter=getter, sink=sink)
            sid = f"iss.index.{secid}"
            ctx.store.upsert(sid, [point(r["date"], r["close"], fetched_at=now, source="iss:index")
                                   for r in rows], unit="points", label=f"индекс {secid}")
            res.series.append(sid)
        except (http.FetchError, IssError, ValueError) as exc:
            res.degrade(f"ISS: индекс {secid} — {exc}")
    return res
