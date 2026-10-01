"""Помощники тестов индикаторов: подставной `getter` на фикстурах и окружение без сети.

Фикстуры — `tests/fixtures/ind/`: реальные ответы открытых машинных источников (ISS, ЦБ,
CreditOrgInfo) 30.09–01.10.2026; **T-Invest — синтетика в формате ответа** (Quotation
`units`/`nano`, только поля, которые читает `indicators/tinvest.py`; брокеры «Брокер A…M») —
условия T запрещают публиковать ответы API; в записях о дивидендах размер и дата отсечки —
публичные решения собраний акционеров, последний день покупки — день торгов перед отсечкой,
прочие поля сочинены. **Новости — сочинённые тексты:** от источника взяты только разметка
(архив дня, статья, RSS, превью канала) и схема чисел, которую читает разбор; числа — релиз
РСБУ эмитента и решения о дивидендах. Это не выдержки, не сокращения и не пересказы статей и
постов: тест с меткой `archive` сверяет фикстуры с архивом настоящих текстов из папки передачи
(`real_news_corpus`, `longest_common_runs`). Токен в тестах — заведомо поддельная строка.
"""

from __future__ import annotations

import ast
import html as _html
import io
import json
import re
import tokenize
from datetime import datetime, timezone
from pathlib import Path

from indicators import config, http
from indicators.store import Store

FIX = Path(__file__).resolve().parent / "fixtures" / "ind"
FAKE_TOKEN = "fake-token-for-tests-only"
# Сверка фикстур новостей с настоящими текстами: общий кусок такой длины и больше — уже выдержка.
# Короче — термины и схема чисел, без которых разбору нечего читать («в августе - на 27,0% - до
# 325,2 млрд» — 36 знаков). Термин длиннее порога (полное название норматива Н1.0 — 41 знак) в
# фикстуре стоит в другой форме слова.
NEWS_COMMON_RUN_LIMIT = 40
# Архив настоящих текстов в папке передачи (этап 1: статьи лент и изданий, посты каналов, smart-lab).
REAL_NEWS_DIRS = ("stage1/_scratch/ras", "research/facts")
_RUN_SEED, _RUN_STEP = 16, 4


class FakeGetter:
    """Отдаёт фикстуры по адресу и телу запроса, как `indicators.http.fetch` (с записью в сырой архив)."""

    def __init__(self, overrides: dict[str | tuple[str, str], bytes | Exception] | None = None,
                 moment: str | None = None):
        """`overrides` — подмена ответа: ключ-строка ищется в адресе или теле запроса, ключ-пара
        («часть адреса», «часть тела») — в обоих сразу (метод T-Invest по одному тикеру)."""
        self.calls: list[dict] = []
        self.overrides = dict(overrides or {})
        self.moment = moment

    def body_for(self, url: str, data: bytes | None, headers: dict) -> bytes:
        for key, value in self.overrides.items():
            if isinstance(key, tuple):
                hit = key[0] in url and key[1].encode() in (data or b"")
            else:
                hit = key in url or bool(data and key.encode() in data)
            if hit:
                if isinstance(value, Exception):
                    raise value
                return value
        action = (headers or {}).get("SOAPAction", "")
        text = (data or b"").decode("utf-8", errors="replace")
        if "CreditOrgInfo" in url:
            m = re.search(r"GetDatesForF(\d+)", action)
            if m:
                return (FIX / "cbr_forms" / f"dates_f{m.group(1)}.xml").read_bytes()
            form = re.search(r"Data(\d{3})", action).group(1)
            day = re.search(r"(\d{4}-\d{2}-\d{2})T", text).group(1)
            exact = FIX / "cbr_forms" / f"f{form}_{day}.xml"
            if exact.exists():
                return exact.read_bytes()
            raise http.FetchError(url, 500, "HTTP 500 (нет фикстуры на дату)")
        if "DailyInfo" in url:
            name = {"KeyRate": "key_rate.xml", "RuoniaXML": "ruonia.xml", "BliquidityXML": "bliquidity.xml"}
            for op, f in name.items():
                if action.endswith("/" + op):
                    return (FIX / "cbr" / f).read_bytes()
        if "avgprocstav" in url:
            return (FIX / "cbr" / "deposits_top10.html").read_bytes()
        if "iss.moex.com" in url:
            if "zcyc" in url:
                return (FIX / "iss" / "zcyc.json").read_bytes()
            if "/markets/index/" in url:
                return (FIX / "iss" / "index_IMOEX_0.json").read_bytes()
            if "/history/" in url:
                return (FIX / "iss" / "history_SBER_0.json").read_bytes()
            return (FIX / "iss" / "quotes.json").read_bytes()
        if "invest-public-api" in url:
            body = json.loads(text or "{}")
            method = url.rsplit(".", 1)[-1]
            by_uid = {json.loads((FIX / "tinvest" / f"shareby_{t}.json").read_text(encoding="utf-8"))
                      ["instrument"]["uid"]: t for t in ("SBER", "SBERP")}
            if method.endswith("ShareBy"):
                p = FIX / "tinvest" / f"shareby_{body.get('id')}.json"
                if not p.exists():
                    raise http.FetchError(url, 404, "HTTP 404")
                return p.read_bytes()
            if method.endswith("GetLastPrices"):
                return (FIX / "tinvest" / "last_prices.json").read_bytes()
            ticker = by_uid.get(body.get("instrumentId"), "SBER")
            if method.endswith("GetDividends"):
                return (FIX / "tinvest" / f"dividends_{ticker}.json").read_bytes()
            if method.endswith("GetAssetReports"):
                return (FIX / "tinvest" / "reports_SBER.json").read_bytes()
            if method.endswith("GetForecastBy"):
                return (FIX / "tinvest" / f"forecast_{ticker}.json").read_bytes()
        if "interfax.ru/business/news/" in url:
            return (FIX / "news" / "interfax_day_2026-09-09.html").read_bytes()
        for art in ("9000101", "9000201", "9000202"):
            if f"interfax.ru/business/{art}" in url:
                return (FIX / "news" / f"interfax_{art}.html").read_bytes()
        if "kommersant.ru/RSS/news.xml" in url:
            return (FIX / "news" / "kommersant_rss.xml").read_bytes()
        if "kommersant.ru/doc/9000401" in url:
            return (FIX / "news" / "kommersant_doc.html").read_bytes()
        if "tass.ru/rss/v2.xml" in url:
            return (FIX / "news" / "tass_rss.xml").read_bytes()
        if "t.me/s/markettwits" in url:
            return (FIX / "news" / "tg_markettwits.html").read_bytes()
        if "t.me/s/SberForInvestors" in url:
            return (FIX / "news" / "tg_SberForInvestors.html").read_bytes()
        raise http.FetchError(url, 404, "HTTP 404")

    def __call__(self, url: str, *, data: bytes | None = None, headers: dict | None = None,
                 sink: http.Sink | None = None, name: str | None = None, **_kw) -> http.Response:
        self.calls.append({"url": url, "data": data, "headers": dict(headers or {})})
        body = self.body_for(url, data, headers or {})
        moment = self.moment or datetime.now(timezone.utc).isoformat(timespec="seconds")
        resp = http.Response(url=url, status=200, body=body, fetched_at=moment,
                             headers={"Content-Type": "application/octet-stream"})
        if sink is not None and name:
            path = sink.keep(name, resp)
            resp = http.Response(resp.url, resp.status, resp.body, resp.fetched_at, resp.headers, str(path))
        return resp


def use_fixtures(monkeypatch, tmp_path: Path, *, token: bool = True) -> Path:
    """Состояние — во временном каталоге; книга и факты — фикстуры; поддельный токен."""
    state = tmp_path / "state"
    monkeypatch.setenv("BANK_STATE_DIR", str(state))
    monkeypatch.setattr(config, "BOOK_PATH", FIX / "book_meta.yaml")
    monkeypatch.setattr(config, "BOOK_DRAFT_PATH", FIX / "book_meta.yaml")
    monkeypatch.setattr(config, "FACTS_DIR", FIX / "facts")
    if token:
        monkeypatch.setenv("TINVEST_TOKEN", FAKE_TOKEN)
    else:
        monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    monkeypatch.setattr(http.time, "sleep", lambda *_: None)
    return state


def seeded_store(root: Path) -> Store:
    from indicators import seed

    store = Store(root)
    seed.import_to_store(store)
    return store


def network_selected(config_) -> bool:
    """Живые тесты — только когда их выбрали явно (`-m network`)."""
    expr = config_.getoption("-m") or ""
    return "network" in expr and "not network" not in expr


# ------------------------------------------------------------------ фикстуры новостей против настоящих текстов

NBSP = chr(0xA0)


def plain_text(raw: bytes | str) -> str:
    """Текст для сверки: без разметки, комментариев и адресов; «ё» → «е», тире любого вида — дефис,
    пробелы схлопнуты. Типографика не должна прятать совпадение."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", text, flags=re.S | re.I)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace(NBSP, " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", re.sub(r"[–—−]", "-", text)).strip()


def longest_common_runs(texts: dict[str, str], docs: dict[str, str]) -> dict[str, tuple[int, str, str]]:
    """Самый длинный общий кусок каждого текста с документами: {имя текста: (длина, кусок, имя документа)}.

    Один проход по документам: затравки текстов (`_RUN_SEED` знаков с шагом `_RUN_STEP`) лежат в словаре,
    совпавшая затравка расширяется в обе стороны. Находит любой кусок от `_RUN_SEED + _RUN_STEP - 1`
    знаков; короче — не ищет (длина 0)."""
    names = list(texts)
    index: dict[str, list[tuple[int, int]]] = {}
    for k, name in enumerate(names):
        text = texts[name]
        for i in range(0, len(text) - _RUN_SEED + 1, _RUN_STEP):
            index.setdefault(text[i:i + _RUN_SEED], []).append((k, i))
    best = {name: (0, "", "") for name in names}
    for where, doc in docs.items():
        for at in range(len(doc) - _RUN_SEED + 1):
            hits = index.get(doc[at:at + _RUN_SEED])
            if not hits:
                continue
            for k, i in hits:
                text = texts[names[k]]
                a, b = i, at
                while a > 0 and b > 0 and text[a - 1] == doc[b - 1]:
                    a, b = a - 1, b - 1
                e, f = i + _RUN_SEED, at + _RUN_SEED
                while e < len(text) and f < len(doc) and text[e] == doc[f]:
                    e, f = e + 1, f + 1
                if e - a > best[names[k]][0]:
                    best[names[k]] = (e - a, text[a:e], where)
    return best


def longest_common_run(text: str, docs: dict[str, str]) -> tuple[int, str, str]:
    """(длина, кусок, имя документа) самого длинного общего куска одного текста с документами."""
    return longest_common_runs({"": text}, docs)[""]


def python_texts(path: Path, *, shortest: int) -> dict[str, str]:
    """Строки и комментарии файла кода длиной от `shortest` знаков: {«файл:строка»: текст для сверки}.
    Сочинённые тексты живут и внутри тестов — к ним тот же вопрос, что к фикстурам."""
    source = path.read_text(encoding="utf-8")
    found = [(node.lineno, node.value) for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    found += [(tok.start[0], tok.string) for tok in tokenize.generate_tokens(io.StringIO(source).readline)
              if tok.type == tokenize.COMMENT]
    out: dict[str, str] = {}
    for line, value in found:
        text = plain_text(value)
        if len(text) >= shortest:
            key = f"{path.name}:{line}"
            while key in out:                # строка и комментарий на одной строке файла
                key += "+"
            out[key] = text
    return out


def _strings(node) -> list[str]:
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return [node] if isinstance(node, str) and len(node) >= _RUN_SEED else []


def real_news_corpus(handoff: Path) -> dict[str, str]:
    """Настоящие тексты новостей из папки передачи (`REAL_NEWS_DIRS`): {путь: текст для сверки}.
    HTML и XML — без разметки, JSON — все строки файла. В репозиторий эти тексты не входят."""
    docs: dict[str, str] = {}
    for part in REAL_NEWS_DIRS:
        for path in sorted((handoff / part).rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".html", ".htm", ".xml", ".json", ".txt"):
                continue
            raw = path.read_bytes()
            if path.suffix.lower() == ".json":
                try:
                    raw = " \n ".join(_strings(json.loads(raw.decode("utf-8", "replace")))).encode("utf-8")
                except ValueError:
                    pass
            text = plain_text(raw)
            if text:
                docs[path.relative_to(handoff).as_posix()] = text
    return docs
