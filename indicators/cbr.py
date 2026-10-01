"""Банк России: ключевая ставка, RUONIA, максимальная ставка по вкладам топ-10, ликвидность.

Основной путь — SOAP `DailyInfo.asmx` (XML, редизайн сайта парсер не ломает):
`KeyRate`, `RuoniaXML`, `BliquidityXML`; ставки вкладов — HTML-таблица
`statistics/avgprocstav` (по декадам). К cbr.ru — только родной `Python-urllib`
(таблица UA в `indicators/http.py`). Ключевая и кривая в оценку не входят: это
наблюдения для плиток и флага `book_update` (М§15).
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any

from indicators import http
from indicators.sources import Context, Result
from indicators.store import point

DAILY_INFO = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
KEY_RATE_PAGE = "https://www.cbr.ru/hd_base/KeyRate/"
DEPOSITS_PAGE = "https://www.cbr.ru/statistics/avgprocstav/"
SOURCE = "cbr"
HISTORY_DAYS = 400
RATE_MIN, RATE_MAX = 0.03, 0.40
_DECADE_DAY = {"I": 1, "II": 11, "III": 21}


class CbrError(RuntimeError):
    """Ответ ЦБ пришёл, но нужного в нём нет или оно не похоже на правду."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _records(body: bytes, tag: str) -> list[dict[str, str]]:
    try:
        tree = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CbrError(f"не XML ({exc})") from exc
    out = []
    for node in tree.iter():
        if _local(node.tag) == tag:
            out.append({_local(c.tag): (c.text or "").strip() for c in node})
    return out


def _iso(stamp: str) -> str:
    return datetime.fromisoformat(stamp).date().isoformat()


def _rate(text: str) -> float:
    return round(float(text.replace(",", ".")) / 100.0, 10)


def _checked(rows: list[tuple[str, float]], where: str) -> list[tuple[str, float]]:
    if not rows:
        raise CbrError(f"{where}: пусто")
    by_day = {}
    for day, rate in rows:
        if not RATE_MIN <= rate <= RATE_MAX:
            raise CbrError(f"{where}: {rate:.4f} на {day} вне {RATE_MIN:.0%}–{RATE_MAX:.0%} (единицы?)")
        by_day[day] = rate
    return sorted(by_day.items())


def dates_inner(start: date, end: date) -> str:
    return f"<fromDate>{start.isoformat()}T00:00:00</fromDate><ToDate>{end.isoformat()}T00:00:00</ToDate>"


def parse_key_rate(body: bytes) -> list[tuple[str, float]]:
    """`KeyRate` → [(дата, доля)] по возрастанию."""
    rows = [(_iso(r["DT"]), _rate(r["Rate"])) for r in _records(body, "KR") if r.get("DT") and r.get("Rate")]
    return _checked(rows, "KeyRate")


_PAGE_ROW = re.compile(r"<td>\s*(\d{2})\.(\d{2})\.(\d{4})\s*</td>\s*<td>\s*(\d+(?:[.,]\d+)?)\s*</td>")


def parse_key_rate_page(html: str) -> list[tuple[str, float]]:
    rows = [(f"{y}-{m}-{d}", _rate(r)) for d, m, y, r in _PAGE_ROW.findall(html)]
    return _checked(rows, "hd_base/KeyRate")


def parse_ruonia(body: bytes) -> list[tuple[str, float, float | None]]:
    """`RuoniaXML` → [(дата, ставка долей, объём млрд)]."""
    out = []
    for r in _records(body, "ro"):
        if r.get("D0") and r.get("ruo"):
            vol = float(r["vol"]) if r.get("vol") else None
            out.append((_iso(r["D0"]), _rate(r["ruo"]), vol))
    _checked([(d, v) for d, v, _ in out], "RuoniaXML")
    return sorted(out)


def parse_bliquidity(body: bytes) -> list[tuple[str, float]]:
    """`BliquidityXML` → [(дата, структурный дефицит, млрд ₽; > 0 — дефицит)]."""
    out = []
    for r in _records(body, "BL"):
        value = r.get("StrLiDefNew") or r.get("StrLiDef")
        if r.get("DT") and value:
            out.append((_iso(r["DT"]), float(value)))
    if not out:
        raise CbrError("BliquidityXML: пусто")
    return sorted(out)


def decade_date(label: str) -> str:
    """«II.09.2026» → первый день декады ISO."""
    m = re.fullmatch(r"(I{1,3})\.(\d{2})\.(\d{4})", label)
    if not m:
        raise CbrError(f"не декада: {label!r}")
    return date(int(m.group(3)), int(m.group(2)), _DECADE_DAY[m.group(1)]).isoformat()


def parse_deposits(html: str) -> list[tuple[str, float]]:
    """Таблица `avgprocstav` («Декада» | «Ставка, %») → [(дата начала декады, доля)]."""
    cells = [re.sub(r"<[^>]+>|\s+", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)]
    rows = [(decade_date(a), _rate(b)) for a, b in zip(cells, cells[1:])
            if re.fullmatch(r"I{1,3}\.\d{2}\.\d{4}", a) and re.fullmatch(r"[\d,]+", b)]
    return _checked(rows, "avgprocstav")


def deposits_url(start: date, end: date) -> str:
    return DEPOSITS_PAGE + "?" + urllib.parse.urlencode({
        "UniDbQuery.Posted": "True", "UniDbQuery.From": f"{start.day}.{start.month:02d}.{start.year}",
        "UniDbQuery.To": end.strftime("%d.%m.%Y")})


def key_rate_page_url(start: date, end: date) -> str:
    return KEY_RATE_PAGE + "?" + urllib.parse.urlencode({
        "UniDbQuery.Posted": "True", "UniDbQuery.From": start.strftime("%d.%m.%Y"),
        "UniDbQuery.To": end.strftime("%d.%m.%Y")})


def changes(rows: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Дни смены значения (первая строка — начало)."""
    out, last = [], None
    for day, rate in rows:
        if rate != last:
            out.append({"date": day, "value": rate})
            last = rate
    return out


def key_rate_summary(history: dict[str, float]) -> dict[str, Any] | None:
    """Ключевая для выхода выпуска: {value, date, since} — since — день начала действия."""
    if not history:
        return None
    rows = sorted(history.items())
    ch = changes(rows)
    return {"value": rows[-1][1], "date": rows[-1][0], "since": ch[-1]["date"]}


def collect(ctx: Context) -> Result:
    getter = ctx.getter or http.fetch
    sink = http.Sink(ctx.store, SOURCE)
    res = Result(name="cbr")
    now = ctx.moment()
    start = ctx.today - timedelta(days=HISTORY_DAYS)
    inner = dates_inner(start, ctx.today)

    def put(sid: str, rows, unit: str, label: str, source: str) -> None:
        ctx.store.upsert(sid, [point(d, v, fetched_at=now, source=source) for d, v in rows],
                         unit=unit, label=label, basis="market")
        res.series.append(sid)

    try:
        body = http.soap(DAILY_INFO, "KeyRate", inner, getter=getter, sink=sink, name="key_rate.xml").body
        put("cbr.key_rate", parse_key_rate(body), "share", "ключевая ставка", "cbr:KeyRate")
    except (http.FetchError, CbrError) as soap_error:
        try:
            html = getter(key_rate_page_url(start, ctx.today), sink=sink, name="key_rate.html").text
            put("cbr.key_rate", parse_key_rate_page(html), "share", "ключевая ставка", "cbr:hd_base")
            res.degrade(f"ключевая: SOAP не ответил годно ({soap_error}), взята страница")
        except (http.FetchError, CbrError) as page_error:
            res.degrade(f"ключевая: SOAP — {soap_error}; страница — {page_error}")
    try:
        body = http.soap(DAILY_INFO, "RuoniaXML", inner, getter=getter, sink=sink, name="ruonia.xml").body
        put("cbr.ruonia", [(d, v) for d, v, _ in parse_ruonia(body)], "share", "RUONIA", "cbr:RuoniaXML")
    except (http.FetchError, CbrError) as exc:
        res.degrade(f"RUONIA: {exc}")
    try:
        body = http.soap(DAILY_INFO, "BliquidityXML", inner, getter=getter, sink=sink,
                         name="bliquidity.xml").body
        put("cbr.bliquidity", parse_bliquidity(body), "RUB bn", "структурный дефицит ликвидности",
            "cbr:BliquidityXML")
    except (http.FetchError, CbrError) as exc:
        res.degrade(f"ликвидность: {exc}")
    try:
        html = getter(deposits_url(start, ctx.today), sink=sink, name="deposits_top10.html").text
        put("cbr.deposit_top10", parse_deposits(html), "share",
            "максимальная ставка по вкладам 10 крупнейших банков", "cbr:avgprocstav")
    except (http.FetchError, CbrError) as exc:
        res.degrade(f"ставки вкладов: {exc}")
    return res
