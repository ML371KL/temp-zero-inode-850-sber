"""Новостные ленты: тексты релизов РСБУ эмитента, числа из текста и ссылки на статические PDF.

Источники и строки поиска — `data/indicators/sources.yaml` (`news`): архив дня
Интерфакса, лента новостей smart-lab по тикеру, публичные превью Telegram-каналов
(`t.me/s/<канал>`, без логина), страницы изданий со ссылками на PDF. HTML-страницы
сайта эмитента закрыты антиботом — их не трогаем; ссылки на статические PDF берём
из открытых источников (REPORT-ras §3.3).

Разбор текста — перенос регулярок этапа 1 (`ifx.py`, `narrative.py`): Интерфакс и
текст релиза дают P&L нарастающим итогом и за месяц, ROE, CoR, CIR, капитал и
Н1.x. Ловушки: «2 трлн 345,6 млрд», грубое «1,234 трлн» (не берётся), блоки
«Есть обновление» (текст режется от «INTERFAX.RU»), месяц прошлого года. Знак
резервов и расходов — как в таблице релиза (расход со знаком минус). Проценты — доли.

Месяц релиза определяется по фразам периода первого предложения, где они есть
(`period_months`): «за N месяцев», «январь–<месяц>», «за/в <месяце>» — целыми словами.
Число берётся только из фразы, которая называет месяц релиза (нарастающее — период в
столько же месяцев): текст следующего релиза не даёт чисел прошлому месяцу.

Всё скачанное ложится в защищённый сырой архив `raw/news/` с моментом получения:
момент выхода новости невосполним.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from indicators import http, periods
from indicators.sources import FAILED, Context, Result

SOURCE = "news"
# Москва без перехода на летнее время с 2014 года: фиксированный сдвиг (tzdata не нужен).
MSK = timezone(timedelta(hours=3), "MSK")
# Названия месяцев целым словом, во всех падежах: «мае» не ловит «марте», основа не ловит прилагательное.
_SOFT = r"(?:ь|я|ю|е|[её]м)"          # январь, января, январю, январе, январём
_HARD = r"(?:а|у|е|ом)?"              # март, марта, марту, марте, мартом
MONTH_WORDS = ("январ" + _SOFT, "феврал" + _SOFT, "март" + _HARD, "апрел" + _SOFT, "ма(?:й|я|ю|е|ем)",
               "июн" + _SOFT, "июл" + _SOFT, "август" + _HARD, "сентябр" + _SOFT, "октябр" + _SOFT,
               "ноябр" + _SOFT, "декабр" + _SOFT)
MON = r"\b(?:" + "|".join(MONTH_WORDS) + r")\b"
# Число месяцев словами («за восемь месяцев», «по итогам восьми месяцев»).
COUNT_WORDS = (("одиннадцат[ьи]", 11), ("двенадцат[ьи]", 12), ("дв(?:а|ух)", 2), ("тр(?:и|[её]х)", 3),
               ("четыр(?:е|[её]х)", 4), ("пят[ьи]", 5), ("шест[ьи]", 6), ("сем[ьи]", 7), ("вос(?:емь|ьми)", 8),
               ("девят[ьи]", 9), ("десят[ьи]", 10))
_COUNT = r"\d{1,2}|" + "|".join(w for w, _ in COUNT_WORDS)
MONTHS_IN_YEAR, HALF_YEAR_MONTHS, QUARTER_MONTHS = 12, 6, 3
# Фразы нарастающего периода релиза: «за N месяцев», «январь–<месяц>», полугодие, I квартал, год.
_CUMULATIVE = re.compile(
    rf"\b(?:за|итог\w*|результат\w*)\s+(?:перв\w+\s+)?(?P<n>{_COUNT})\s+месяц\w*"
    rf"|\bянвар\w*\s*[-–—]\s*(?P<to>{MON})"
    r"|(?P<half>\b(?:(?:перв\w+|I|1)(?:-?[ео][ем])?|за)\s+полугоди\w+)"
    r"|(?P<quarter>\b(?:перв\w+|I|1)(?:-?[йм])?\s*кв(?:артал\w*|(?![а-яё])))"
    r"|(?P<year>\bза\s+(?:\d{4}\s+)?год\b|\bв\s+\d{4}\s+году\b|\bитог\w*\s+(?:\d{4}\s+)?года\b)", re.I)
# «за август», «в августе» — месяц сам по себе, не начало «январь–<месяц>».
_IN_MONTH = re.compile(rf"\b(?:за|в)\s+(?P<m>{MON})(?!\s*[-–—]\s*{MON})", re.I)
N = r"\d{1,4}(?:[.,]\d{1,3})?"
PERCENT_METRICS = frozenset({"roe_ytd", "roe_m", "cor_ytd", "cor_m", "cir_ytd", "n1_0", "n1_1", "n1_2"})


# ------------------------------------------------------------------ HTML

def decode(body: bytes) -> str:
    """UTF-8, иначе cp1251 (старые страницы лент)."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1251", errors="replace")


def strip_html(doc: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", doc, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t ]+", " ", _html.unescape(t)).strip()


def meta(doc: str, prop: str) -> str | None:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', doc) or \
        re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"', doc)
    return _html.unescape(m.group(1)) if m else None


def links(doc: str, base: str = "") -> list[tuple[str, str]]:
    """[(абсолютный адрес, текст ссылки)] в порядке появления."""
    out = []
    for href, text in re.findall(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', doc, re.S | re.I):
        out.append((urllib.parse.urljoin(base, _html.unescape(href)), strip_html(text)))
    return out


def pdf_links(doc: str, patterns: list[str]) -> list[str]:
    """Ссылки на статические PDF эмитента по шаблонам sources.yaml (в href и в тексте)."""
    found: list[str] = []
    text = _html.unescape(doc)
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            url = m.group(0).rstrip('.,;)"\'')
            if url not in found:
                found.append(url)
    return found


def article_text(doc: str) -> str:
    """Текст статьи: от «INTERFAX.RU» (если есть), без блоков «Есть обновление»."""
    body = doc
    m = re.search(r"<article[^>]*>(.*?)</article>", doc, re.S | re.I)
    if m:
        body = m.group(1)
    text = strip_html(body)
    i = text.find("INTERFAX.RU")
    if i >= 0:
        text = text[i:]
    j = text.find("Есть обновление")
    if j > 0:
        text = text[:j]
    return text


def interfax_listing(doc: str, base: str) -> list[dict[str, str]]:
    """Архив дня Интерфакса → [{url, title}] статей раздела."""
    out, seen = [], set()
    for url, title in links(doc, base):
        if re.search(r"interfax\.ru/business/\d+", url) and title and url not in seen:
            seen.add(url)
            out.append({"url": url, "title": title})
    return out


def telegram_posts(doc: str) -> list[dict[str, Any]]:
    """Публичное превью `t.me/s/<канал>` → [{post, time, text, links}]."""
    out = []
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', doc)[1:]:
        post = re.search(r'data-post="([^"]+)"', block)
        stamp = re.search(r'<time[^>]+datetime="([^"]+)"', block)
        text = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not post:
            continue
        body = text.group(1) if text else ""
        out.append({"post": post.group(1), "time": stamp.group(1) if stamp else None,
                    "text": strip_html(body), "links": [u for u, _ in links(body)]})
    return out


def relevant(text: str, keywords: list[str], require: list[str] | None = None) -> bool:
    """Есть ли в тексте имя эмитента (любое из `keywords`) и все слова `require` (без регистра)."""
    low = (text or "").lower()
    if not any(k.lower() in low for k in keywords):
        return False
    return all(r.lower() in low for r in (require or []))


# ------------------------------------------------------------------ числа из текста

def _norm(t: str) -> str:
    t = t.replace(" ", " ").replace("п.п.", "пп")
    t = re.sub(r"(\d+)\s+трлн\s+(\d{1,3})(?:[,.](\d))?\s+млрд",
               lambda m: f"{int(m.group(1)) * 1000 + int(m.group(2))},{m.group(3) or '0'} млрд", t)
    t = re.sub(r"(\d)\s(\d{3})(?=[,.]\d|\s*(?:млрд|трлн))", r"\1\2", t)
    return re.sub(r"\s+", " ", t)


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def _bn(v: str, unit: str, min_dec: int = 4) -> float | None:
    """В млрд; «трлн» с меньшим числом знаков после запятой, чем `min_dec`, — грубое округление, не берётся."""
    if unit.startswith("трлн"):
        dec = len(re.split(r"[.,]", v)[1]) if re.search(r"[.,]", v) else 0
        return _num(v) * 1000 if dec >= min_dec else None
    return _num(v)


_COMPARE = re.compile(r",\s*что\s+на|по\s+сравнению|годом\s+ранее|больше\s+результата|меньше\s+результата", re.I)


def _sents(t: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z«\"])", t) if x.strip()]


def _month_no(word: str) -> int:
    """Номер месяца по слову в любом падеже («мая» → 5, «марте» → 3)."""
    for i, form in enumerate(MONTH_WORDS):
        if re.fullmatch(form, word, re.I):
            return i + 1
    raise ValueError(word)


def _count(word: str) -> int:
    if word.isdigit():
        return int(word)
    for form, n in COUNT_WORDS:
        if re.fullmatch(form, word, re.I):
            return n
    raise ValueError(word)


def cumulative_months(text: str, *, bare_year: bool = False) -> int | None:
    """Число месяцев первой фразы нарастающего периода: «за 8 месяцев», «январь–август» → 8; нет фразы — None.

    «За год» и «по итогам года» без номера года — период только при `bare_year` (релиз за декабрь):
    в месячном релизе «за год» значит «год к году».
    """
    for m in _CUMULATIVE.finditer(text):
        if m.group("n"):
            n = _count(m.group("n"))
        elif m.group("to"):
            n = _month_no(m.group("to"))
        elif m.group("half"):
            n = HALF_YEAR_MONTHS
        elif m.group("quarter"):
            n = QUARTER_MONTHS
        elif bare_year or re.search(r"\d{4}", m.group("year")):
            n = MONTHS_IN_YEAR
        else:
            continue
        if 1 <= n <= MONTHS_IN_YEAR:
            return n
    return None


def period_months(text: str) -> tuple[int, ...]:
    """Месяцы релиза РСБУ, которые называет текст: решает первое предложение с фразой периода.

    Заголовок и предложения без периода пропускаются. В найденном предложении берутся число
    месяцев фразы нарастающего периода («за N месяцев», «январь–<месяц>», полугодие, I квартал,
    год с номером) и месяц первой фразы «за/в <месяце>» — в этом порядке, без повторов. Фразы
    дальше по тексту месяц не меняют: в январском релизе ниже стоит «прибыль за 4 месяца» — о
    нормативах, не о периоде релиза. Подстроки («7м» в «332,7 млрд») и основы слов месяцем не
    считаются. Нет фразы периода — пусто.
    """
    for sentence in _sents(_norm(text or "")):
        found: list[int] = []
        n = cumulative_months(sentence)
        if n is not None:
            found.append(n)
        m = _IN_MONTH.search(sentence)
        if m and _month_no(m.group("m")) not in found:
            found.append(_month_no(m.group("m")))
        if found:
            return tuple(found)
    return ()


def release_month(text: str) -> int | None:
    """Главный месяц текста — первый из `period_months` (нарастающий период важнее слова месяца);
    None — по тексту не определить."""
    months = period_months(text)
    return months[0] if months else None


def extract_release_numbers(text: str, *, month: str | None = None) -> dict[str, tuple[float, str]]:
    """Числа релиза РСБУ из новостного текста → {метрика: (значение, фраза)}.

    Метрики: `np_ytd`, `np_m`, `nii_ytd`, `nii_m`, `fee_ytd`, `fee_m`, `prov_ytd`, `prov_m`,
    `opex_ytd`, `opex_m`, `roe_ytd`, `roe_m`, `cor_ytd`, `cor_m`, `cir_ytd`, `n1_1`, `n1_2`,
    `n1_0`, `cap_base`, `cap_main`, `cap_total`. Деньги — млрд ₽ (резервы и расходы со
    знаком минус), проценты — доли.

    `month` — месяц релиза («2026M08»), с ним число берётся, только если фраза называет этот
    месяц: нарастающее — при периоде в столько же месяцев, месячное — при слове этого месяца,
    остаток на дату — при первом числе следующего месяца; год фразы — год релиза. Без `month`
    берётся любой месяц (разбор текста самого по себе).
    """
    t = _norm(text).replace("₽", "руб.")
    out: dict[str, tuple[float, str]] = {}
    year, n = periods.parse_month(month) if month else (None, None)
    M = rf"\b{MONTH_WORDS[n - 1]}\b" if n else MON

    def put(key: str, value: float | None, s: str) -> None:
        # Год — только из главной части фразы: хвост сравнения («что на …», «по сравнению
        # с …», «годом ранее») называет прошлый год и не должен отбрасывать число.
        head = _COMPARE.split(s)[0]
        years = set(re.findall(r"(\d{4})\s+(?:год|году|года)", head))
        if year and years and str(year) not in years:
            return
        if value is None or key in out:
            return
        if key in PERCENT_METRICS:
            value = round(value / 100.0, 10)
        out[key] = (round(value, 4), s[:220])

    def stock_date_fits(s: str) -> bool:
        """Остаток «на 1 <месяца>» — первое число месяца после месяца релиза."""
        got = re.search(rf"на\s+1\s+({MON})", s, re.I)
        return n is None or got is None or _month_no(got.group(1)) == n % MONTHS_IN_YEAR + 1

    for s in _sents(t):
        sl = s.lower()
        period = cumulative_months(s, bare_year=n in (None, MONTHS_IN_YEAR))
        is_ytd = period is not None and n in (None, period)       # нарастающее этого релиза
        not_other = n is None or period in (None, n)               # фраза без периода — период релиза
        if "прибыл" in sl and ("чист" in sl or "заработал" in sl):
            # в релизе за декабрь между «по РСБУ» и числом стоит оговорка про СПОД (события после отчётной даты)
            m = re.search(rf"прибыль\s+по\s+р[сб][бс]у\s+(?:без\s+уч[её]та\s+[^.%\d]{{0,80}}?\s+)?(?:на\s+{N}%\s*[-–—]?\s*)?"
                          rf"[-–—]?\s*до\s+(?:рекордн\w+\s+)?({N})\s*(млрд|трлн)", s, re.I)
            if m and is_ytd:
                put("np_ytd", _bn(m.group(1), m.group(2)), s)
            m = re.search(rf"(?:в|за)\s+{M}(?:\s+\d{{4}}\s+года|\s+текущего\s+года)?\s+[^.]*?(?:заработал\w*|составила|"
                          rf"выросла[^.]*?до|увеличил\w*[^.]*?до|снизил\w*[^.]*?до)\s+({N})\s*млрд\s*руб\w*", s, re.I)
            if m and not re.search(rf"январ\w*\s*[-–—]\s*{MON}", m.group(0), re.I):
                put("np_m", _num(m.group(1)), s)
        if "рентабельность капитала" in sl:
            m = re.search(rf"рентабельность капитала\s+(?:за|в)\s+(?:\d+\s+месяц\w*|январ\w*\s*[-–—]\s*{MON}|первое полугодие|"
                          rf"I квартал\w*)[^.%]*?(?:составила|выросла до|снизилась до|до)\s+({N})%", s, re.I)
            if m and not_other:
                put("roe_ytd", _num(m.group(1)), s)
            m = re.search(rf"(?:в|за)\s+{M}\s*[-–—]\s*({N})%", s, re.I)
            if m:
                put("roe_m", _num(m.group(1)), s)
        if re.search(r"чист\w+ процентн\w+ доход", sl):
            m = re.search(rf"процентн\w+ доход\w*[^.]*?[-–—]?\s*до\s+({N})\s*(млрд|трлн)", s, re.I)
            if m and is_ytd:
                put("nii_ytd", _bn(m.group(1), m.group(2)), s)
            m = re.search(rf"в\s+{M}\s*[-–—]\s*на\s+{N}%\s*[-–—]\s*до\s+({N})\s*млрд", s, re.I)
            if m:
                put("nii_m", _num(m.group(1)), s)
        if re.search(r"комиссионн\w+ доход", sl):
            m = re.search(rf"комиссионн\w+ доход\w*[^.]*?(?:[-–—,]\s*до|составил\w*|до)\s+({N})\s*(млрд|трлн)", s, re.I)
            if m and is_ytd:
                put("fee_ytd", _bn(m.group(1), m.group(2)), s)
        if re.search(rf"^в\s+{M}\s+(?:этот\s+)?показатель\s+(?:сократился|снизился|вырос|увеличился)", sl) \
                and "fee_ytd" in out and "fee_m" not in out and "opex_ytd" not in out:
            m = re.search(rf"до\s+({N})\s*млрд", s)
            if m:
                put("fee_m", _num(m.group(1)), s)
        if "расходы на резервы" in sl:
            m = re.search(rf"(?:до|составили)\s+({N})\s*(млрд|трлн)", s, re.I)
            if m and is_ytd:
                v = _bn(m.group(1), m.group(2))
                put("prov_ytd", None if v is None else -v, s)
            m = re.search(rf"(?:в|за)\s+{M}(?!\s*[-–—]\s*[а-я])(?:\s+текущего года)?\s*[-–—]?[^.]*?до\s+({N})\s*млрд", s, re.I)
            if m and not re.search(r"январ\w*\s*[-–—]", m.group(0), re.I):
                put("prov_m", -_num(m.group(1)), s)
        if "стоимость" in sl and "риска" in sl:
            m = re.search(rf"за\s+(?:\d+\s+месяц\w*|январ\w*\s*[-–—]\s*{MON})[^.%]*?составила\s+({N})%", s, re.I)
            if m and not_other:
                put("cor_ytd", _num(m.group(1)), s)
            m = re.search(rf"(?:и|,)\s+({N})%\s+в\s+{M}", s, re.I) or re.search(rf"в\s+{M}\s*[-–—]\s*({N})%", s, re.I)
            if m:
                put("cor_m", _num(m.group(1)), s)
        if "операционные расходы" in sl:
            m = re.search(rf"операционные расходы[^.]*?(?:[-–—]\s*до|до|составили)\s+({N})\s*(млрд|трлн)", s, re.I)
            if m and is_ytd:
                v = _bn(m.group(1), m.group(2))
                put("opex_ytd", None if v is None else -v, s)
            m = re.search(rf"в\s+{M}\s*[-–—]\s*на\s+{N}%\s*[-–—]\s*до\s+({N})\s*млрд", s, re.I)
            if m:
                put("opex_m", -_num(m.group(1)), s)
        if re.search(r"отношение\s+(?:операционных\s+)?расходов\s+к\s+доходам", sl):
            m = re.search(rf"составил\w*\s+({N})%", s, re.I)
            if m and not_other:
                put("cir_ytd", _num(m.group(1)), s)
        if not stock_date_fits(s):
            continue
        if re.search(r"базов\w+\s+и\s+основн\w+\s+капитал", sl) and "трлн" in sl and "коэффициент" not in sl:
            # «составили X трлн рублей и Y трлн рублей» и короткая запись «составили X и Y трлн рублей»
            m = re.search(rf"составили\s+({N})(?:\s*трлн\s*руб\w*\.?)?\s+и\s+({N})\s*трлн", s, re.I)
            if m:
                put("cap_base", _bn(m.group(1), "трлн", 3), s)
                put("cap_main", _bn(m.group(2), "трлн", 3), s)
        if "общий капитал" in sl:
            m = re.search(rf"(?:до|составил)\s+({N})\s*трлн", s, re.I)
            if m:
                put("cap_total", _bn(m.group(1), "трлн", 3), s)
        if re.search(r"коэффициент\w*\s+достаточности\s+базового", sl):
            m = re.search(rf"({N})%\s*и\s*({N})%", s)
            if m:
                put("n1_1", _num(m.group(1)), s)
                put("n1_2", _num(m.group(2)), s)
        if re.search(r"коэффициент\w*\s+достаточности\s+общего", sl):
            m = re.search(rf"коэффициент\w*\s+достаточности\s+общего\s+капитала[^.%]*?(?:до|составил\w*|на уровне)\s+({N})%", s, re.I)
            if m:
                put("n1_0", _num(m.group(1)), s)
    return out


def extract_headline(text: str, *, month: str | None = None) -> dict[str, tuple[float, str]]:
    """Заголовки лент и постов каналов (прописными или обычным текстом) → ЧП нарастающим и за месяц.

    Форматы: «ЗА N МЕСЯЦЕВ … ЧИСТАЯ ПРИБЫЛЬ … ДО X МЛРД РУБ.», «В <МЕСЯЦЕ> <ЭМИТЕНТ>
    УВЕЛИЧИЛ ЧИСТУЮ ПРИБЫЛЬ ПО РСБУ НА P%, ДО Y МЛРД РУБ», «… В 1КВ … ПОЛУЧИЛ X МЛРД РУБ.
    ЧИСТОЙ ПРИБЫЛИ», «В <МЕСЯЦЕ> ПОЛУЧИЛ Y МЛРД РУБ. ЧИСТОЙ ПРИБЫЛИ», «Чистая прибыль за N
    месяцев … до X млрд ₽» (канал эмитента). `month` — месяц релиза: берётся только нарастающее
    за столько же месяцев и число этого месяца, год фразы — год релиза (как у `extract_release_numbers`).
    """
    out: dict[str, tuple[float, str]] = {}
    t = _norm(text).lower().replace("ё", "е").replace("₽", "руб")
    year, n = periods.parse_month(month) if month else (None, None)
    M = rf"\b{MONTH_WORDS[n - 1]}\b" if n else MON

    def fits(phrase: str, *, cumulative: bool) -> bool:
        years = set(re.findall(r"(\d{4})\s+год", phrase))
        if year and years and str(year) not in years:
            return False
        return not cumulative or n is None or cumulative_months(phrase) == n

    ytd = rf"(?:за\s+(?:{_COUNT})\s+месяц\w*|(?:в|за)\s+январ\w*\s*[-–—]\s*{MON}|в\s+1\s*кв\w*)"
    for pat in (rf"{ytd}[^.\n]*?чист\w+\s+прибыл\w*[^.\n]*?(?:до|составила)\s+({N})\s*(млрд|трлн)",
                rf"чист\w+\s+прибыл\w*\s+{ytd}[^.\n]*?(?:до|составила)\s+({N})\s*(млрд|трлн)",
                rf"{ytd}[^.\n]*?(?:получил|заработал)\w*\s+({N})\s*(млрд|трлн)\s+руб\w*\.?\s+чистой\s+прибыли"):
        for m in re.finditer(pat, t):
            v = _bn(m.group(1), m.group(2))
            if v is not None and "np_ytd" not in out and fits(m.group(0), cumulative=True):
                out["np_ytd"] = (v, m.group(0)[:200])
    for pat in (rf"в\s+{M}\s+[^.\n]*?(?:получил|заработал)\w*\s+({N})\s*млрд\s+руб\w*\.?\s+чистой\s+прибыли",
                rf"в\s+{M}\s+[^.\n]*?(?:увеличил|сократил|снизил|нарастил)\w*\s+чистую\s+прибыль[^.\n]*?до\s+({N})\s*млрд"):
        for m in re.finditer(pat, t):
            if "np_m" not in out and fits(m.group(0), cumulative=False):
                out["np_m"] = (_num(m.group(1)), m.group(0)[:200])
    return out


# ------------------------------------------------------------------ решения о дивидендах

_DATE_WORDS = re.compile(rf"(\d{{1,2}})\s+({MON})(?:\s+(\d{{4}}))?", re.I)
_DATE_DOTS = re.compile(r"(\d{1,2})\.(\d{2})\.(\d{4})")
_DECLARED = re.compile(r"(?:акционер\w*|собрани\w*|ГОСА)[^.]{0,160}?(?:утвердил\w*|одобрил\w*|"
                       r"принял\w*\s+решение\s+(?:о\s+выплат\w*|выплатить|направить))", re.I)
_RECOMMENDED = re.compile(r"(?:набсовет\w*|наблюдательн\w+\s+совет\w*|совет\w*\s+директоров)[^.]{0,160}?"
                          r"рекомендова\w*", re.I)
_DIV_YEAR = re.compile(r"дивиденд\w*[^.]{0,80}?(?:по\s+итогам|за)\s+(\d{4})\s*(?:год\w*|г\.?)", re.I)
_DPS = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:руб(?:л\w*|\.)?|₽)\s*(?:на\s+(?:одну\s+|каждую\s+)?"
                  r"(?P<cls>обыкновенн\w+\s+и\s+привилегированн\w+|обыкновенн\w+|привилегированн\w+)?\s*акци\w*)",
                  re.I)
_RECORD = re.compile(r"(?:закрыти\w+\s+реестр\w*|реестр\w*\s+акционер\w*|отсечк\w*|список\s+лиц)", re.I)


def _find_date(text: str, default_year: int | None) -> str | None:
    """Первая дата в тексте: «20 июля 2026 года», «20 июля» (год — по умолчанию) или «20.07.2026»."""
    m = _DATE_DOTS.search(text)
    w = _DATE_WORDS.search(text)
    if w and (not m or w.start() < m.start()):
        year = int(w.group(3)) if w.group(3) else default_year
        if year is None:
            return None
        try:
            return date(year, _month_no(w.group(2)), int(w.group(1))).isoformat()
        except ValueError:
            return None
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def extract_dividend_decision(text: str, *, published: str | None = None) -> dict[str, Any] | None:
    """Решение ГОСА (`declared`) или рекомендация совета директоров (`recommended`) о дивидендах.

    Возвращает {status, year (год прибыли), dps, dps_preferred, record_date, decided_date, phrase}
    или None, если в тексте нет решения с годом и DPS. DPS — из предложения с решением (или
    следующего): прошлогодняя выплата дальше по тексту («по итогам … года выплатил …») не
    берётся. Дата решения — день публикации (МСК), дата отсечки — из фразы о закрытии реестра.
    """
    t = _norm(text or "").replace("₽", " ₽")
    sents = _sents(t)
    pub = parse_published(published)
    pub_day = None if pub is None else (pub.astimezone(MSK).date() if pub.tzinfo else pub.date())
    for i, s in enumerate(sents):
        if "дивиденд" not in s.lower():
            continue
        status = "declared" if _DECLARED.search(s) else ("recommended" if _RECOMMENDED.search(s) else None)
        if status is None:
            continue
        year = _DIV_YEAR.search(s)
        if not year:
            continue
        scope = s + " " + (sents[i + 1] if i + 1 < len(sents) else "")
        dps, dps_pref = None, None
        for m in _DPS.finditer(scope):
            v = _num(m.group(1))
            cls = (m.group("cls") or "").lower()
            if cls.startswith("привилегированн"):
                dps_pref = v if dps_pref is None else dps_pref
                continue
            dps = v if dps is None else dps
            if "привилегированн" in cls or not cls:
                dps_pref = v if dps_pref is None else dps_pref
        if dps is None:
            continue
        record = None
        for r in sents[i:]:
            hit = _RECORD.search(r)
            if hit:
                record = _find_date(r[hit.start():], pub_day.year if pub_day else None)
                if record:
                    break
        return {"status": status, "year": int(year.group(1)), "dps": dps, "dps_preferred": dps_pref,
                "record_date": record, "decided_date": pub_day.isoformat() if pub_day else None,
                "phrase": s[:220]}
    return None


# ------------------------------------------------------------------ RSS и страницы изданий

def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", block, re.S | re.I)
    if not m:
        return ""
    v = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", m.group(1), flags=re.S)
    return strip_html(_html.unescape(v))


def rss_items(doc: str) -> list[dict[str, Any]]:
    """RSS 2.0 → [{url, title, description, published}] (CDATA и сущности раскрыты)."""
    out = []
    for block in re.findall(r"<item\b.*?</item>", doc, re.S | re.I):
        url = _tag(block, "link") or _tag(block, "guid")
        if not url:
            continue
        stamp = None
        pub = _tag(block, "pubDate")
        if pub:
            try:
                stamp = parsedate_to_datetime(pub).isoformat()
            except (TypeError, ValueError):
                stamp = None
        out.append({"url": url.strip(), "title": _tag(block, "title"), "description": _tag(block, "description"),
                    "published": stamp})
    return out


def page_text(doc: str) -> str:
    """Текст статьи издания: `articleBody` JSON-LD, иначе `<article>`, иначе абзацы текста, иначе вся страница."""
    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", doc, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and isinstance(node.get("articleBody"), str):
                return strip_html(node["articleBody"])
    if re.search(r"<article\b", doc, re.I):
        return article_text(doc)
    paras = re.findall(r'<p\s[^>]*class="[^"]*text[^"]*"[^>]*>(.*?)</p>', doc, re.S | re.I)
    if paras:
        return "\n".join(strip_html(p) for p in paras)
    return strip_html(doc)


def page_published(doc: str) -> str | None:
    """Момент публикации страницы: meta `article:published_time`, JSON-LD `datePublished`, дата smart-lab."""
    got = meta(doc, "article:published_time")
    if got:
        return got
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', doc)
    if m:
        return m.group(1)
    m = re.search(r'<li class="date">\s*(\d{1,2})\s+(' + MON + r')\s+(\d{4}),\s*(\d{1,2}):(\d{2})', doc, re.I)
    if m:
        try:
            return datetime(int(m.group(3)), _month_no(m.group(2)), int(m.group(1)), int(m.group(4)),
                            int(m.group(5)), tzinfo=MSK).isoformat()
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ сборщик

def _day_url(template: str, d: date, **kw: Any) -> str:
    return template.format(yyyy=f"{d.year:04d}", mm=f"{d.month:02d}", dd=f"{d.day:02d}", **kw)


def days_to_scan(today: date, cfg: dict[str, Any], done: list[str]) -> list[date]:
    """Дни архива Интерфакса: сегодня и `lookback_days` назад — всегда; до `catchup_days` назад — если
    день ещё не пройден после своего окончания (пропущенные такты: выходные, простой сервера)."""
    back = int(cfg.get("lookback_days", 1))
    catch = max(back, int(cfg.get("catchup_days", back)))
    seen = set(done or [])
    days = [today - timedelta(days=k) for k in range(back + 1)]
    days += [today - timedelta(days=k) for k in range(back + 1, catch + 1)
             if (today - timedelta(days=k)).isoformat() not in seen]
    return days


def collect(ctx: Context) -> Result:
    """Архив дня Интерфакса, лента smart-lab, превью каналов, ленты изданий → тексты, ссылки на PDF.

    Берутся новости эмитента двух тем: релиз РСБУ (`ras_require`) и дивиденды (`dividend_require`:
    решение ГОСА и рекомендация совета — второй источник реестра, М§15).
    """
    cfg = ctx.cfg.get("news") or {}
    getter = ctx.getter or http.fetch
    sink = http.Sink(ctx.store, SOURCE)
    res = Result(name="news")
    keywords = list(cfg.get("issuer_keywords") or [])
    require = list(cfg.get("ras_require") or [])
    div_require = list(cfg.get("dividend_require") or [])
    patterns = list(cfg.get("pdf_patterns") or [])
    ticker = ctx.company["main_ticker"]
    state = ctx.store.read_state("news/items.json") or {}
    state.setdefault("items", {})
    state.setdefault("pdf", {})
    state.setdefault("days", {})
    now = ctx.moment()
    fetched = 0

    def topic_of(text: str) -> str | None:
        if relevant(text, keywords, require):
            return "ras"
        if div_require and relevant(text, keywords, div_require):
            return "dividend"
        return None

    def remember(url: str, kind: str, title: str, text: str, published: str | None, found: list[str],
                 topic: str = "ras") -> None:
        item = state["items"].setdefault(url, {"kind": kind, "first_seen": now})
        item.update({"title": title, "published": published, "text": text[:20000], "topic": topic})
        for p in found:
            state["pdf"].setdefault(p, {"found_in": url, "first_seen": now})

    done = list(state["days"].get("interfax") or [])
    for d in days_to_scan(ctx.today, cfg, done) if cfg.get("interfax_day") else []:
        url = _day_url(cfg["interfax_day"], d)
        try:
            doc = decode(getter(url, sink=sink, name=f"interfax_{d.isoformat()}.html").body)
            fetched += 1
        except http.FetchError as exc:
            res.degrade(f"Интерфакс {d.isoformat()}: {exc}", retry=http.retryable(exc))
            continue
        complete = True
        for art in interfax_listing(doc, url):
            topic = topic_of(art["title"])
            if not topic or art["url"] in state["items"]:
                continue
            try:
                page = decode(getter(art["url"], sink=sink,
                                     name=f"interfax_{art['url'].rsplit('/', 1)[-1]}.html").body)
            except http.FetchError as exc:
                res.degrade(f"Интерфакс {art['url']}: {exc}", retry=http.retryable(exc))
                complete = False
                continue
            remember(art["url"], "interfax", art["title"], article_text(page),
                     meta(page, "article:published_time"), pdf_links(page, patterns), topic)
        if complete and d < ctx.today and d.isoformat() not in done:
            done.append(d.isoformat())
    state["days"]["interfax"] = sorted(done)[-int(cfg.get("days_kept", 31)):]
    for entry in cfg.get("telegram_channels") or []:
        spec = entry if isinstance(entry, dict) else {"name": entry}
        channel = spec["name"]
        kind = str(spec.get("kind") or f"telegram:{channel}")
        words = None if spec.get("keywords") == "any" else keywords + [f"#{ticker}"]
        need = list(spec.get("require", require))
        url = f"https://t.me/s/{channel}"
        try:
            doc = decode(getter(url, sink=sink, name=f"tg_{channel}.html").body)
            fetched += 1
        except http.FetchError as exc:
            res.degrade(f"канал {channel}: {exc}", retry=http.retryable(exc))
            continue
        for post in telegram_posts(doc):
            low = post["text"].lower()
            if (words is None or relevant(post["text"], words)) and all(w.lower() in low for w in need):
                remember(f"https://t.me/{post['post']}", kind, post["text"][:120], post["text"],
                         post["time"], pdf_links(" ".join(post["links"]), patterns))
    if cfg.get("smartlab_news"):
        url = cfg["smartlab_news"].format(ticker=ticker)
        try:
            doc = decode(getter(url, sink=sink, name=f"smartlab_{ticker}.html").body)
            fetched += 1
            for link, title in links(doc, url):
                if re.search(r"smart-lab\.ru/blog/(?:news/)?\d+", link) and relevant(title, keywords, require) \
                        and link not in state["items"]:
                    page = decode(getter(link, sink=sink, name=f"smartlab_{link.rstrip('/').rsplit('/', 1)[-1]}").body)
                    remember(link, "smartlab", title, strip_html(page), page_published(page),
                             pdf_links(page, patterns))
        except http.FetchError as exc:
            res.degrade(f"smart-lab: {exc}", retry=http.retryable(exc))
    for outlet in cfg.get("outlets") or []:
        name = outlet["name"]
        rss = outlet.get("format") == "rss"
        try:
            doc = decode(getter(outlet["listing"], sink=sink,
                                name=f"outlet_{name}.{'xml' if rss else 'html'}").body)
            fetched += 1
        except http.FetchError as exc:
            res.degrade(f"{name}: {exc}", retry=http.retryable(exc))
            continue
        entries = rss_items(doc) if rss else [{"url": u, "title": t, "description": "", "published": None}
                                              for u, t in links(doc, outlet["listing"])]
        for e in entries:
            if not re.search(outlet.get("article", r"."), e["url"]) or e["url"] in state["items"]:
                continue
            topic = topic_of(f"{e['title']} {e['description']}")
            if topic is None:
                continue
            if not outlet.get("fetch_article", True):
                text = f"{e['title']}. {e['description']}".strip()
                remember(e["url"], name, e["title"], text, e["published"], pdf_links(text, patterns), topic)
                continue
            try:
                page = decode(getter(e["url"], sink=sink,
                                     name=f"outlet_{name}_{hashlib.sha1(e['url'].encode()).hexdigest()[:10]}.html").body)
            except http.FetchError as exc:
                res.degrade(f"{name} {e['url']}: {exc}", retry=http.retryable(exc))
                continue
            remember(e["url"], name, e["title"], page_text(page)[:20000],
                     e["published"] or page_published(page), pdf_links(page, patterns), topic)
    ctx.store.write_state("news/items.json", state)
    res.data["items"] = len(state["items"])
    res.data["pdf"] = sorted(state["pdf"])
    if fetched == 0 and (cfg.get("interfax_day") or cfg.get("telegram_channels")):
        res.status, res.detail = FAILED, "ни одна лента не ответила"
    return res


def parse_published(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def published_day(stamp: str | None) -> date | None:
    """День публикации по Москве (момент без пояса — как есть)."""
    moment = parse_published(stamp)
    if moment is None:
        return None
    return moment.astimezone(MSK).date() if moment.tzinfo else moment.date()
