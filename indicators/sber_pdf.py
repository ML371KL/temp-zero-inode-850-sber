"""Статические PDF эмитента: скачивание по найденной ссылке (sha256), разбор, распознавание.

* Ссылки приходят из открытых источников (`indicators/news.py`), HTML-страницы
  эмитента не трогаем (антибот не обходим). Файл ложится в защищённый архив
  `raw/sber_pdf/` с sha256 и `Last-Modified`; через неделю — перекачка: перезалив
  (другой sha256) — новый винтаж.
* PDF с текстовым слоем (отчётность МСФО) — разбор `pdfplumber` (импорт ленивый).
* PDF без текста (релизы РСБУ: текст в кривых) — рендер `pypdfium2` и `tesseract`,
  если он установлен (`tesseract-ocr`, `tesseract-ocr-rus`). Разбор таблицы — по
  координатам строк (перенос `tables.py` этапа 1): подписи строк слева, колонки
  «нарастающим» и «месяц» по заголовкам, ячейки — по ближайшей строке и колонке.
* Любой отказ распознавания — статус `needs_manual` с причиной, число вносит
  `record-ras`: нет `tesseract` или `pypdfium2`, PDF не рендерится, `tesseract` не
  ответил за `OCR_PAGE_TIMEOUT_S` (страница) или документ не уложился в `OCR_BUDGET_S`,
  ошибка или неразбираемый вывод. Такт от этого не падает. Распознаётся только релиз
  РСБУ: у прочих документов без текстового слоя таблиц для автомата нет.
* Запись кандидата ложится в состояние ДО разбора: индекс к этому времени уже отметил
  файл проверенным, и оборванный разбор (потолок юнита, память) не оставит файл без
  записи — следующий такт называет его в причинах деградации.
* Дымовая проверка распознавания на уже опубликованном релизе — проба
  `ops/tools/probe.py --only sber_ocr`: она зовёт `analyse` — тот же путь, что у сборщика.

Кандидаты чисел — `sber_pdf/candidates.json` в состоянии; фактом их делает приём
при двух источниках (`release_watch.py`) или просмотр и `record-*`.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

from indicators import http, periods
from indicators.sources import Context, Result

SOURCE = "sber_pdf"
INDEX_FILE = "sber_pdf/index.json"
CANDIDATES_FILE = "sber_pdf/candidates.json"
RECHECK_DAYS = 7
TEXT_LAYER_MIN_CHARS = 200
RENDER_SCALE = 3.0
# Распознавание: страница дольше OCR_PAGE_TIMEOUT_S — зависание, а не работа; весь документ —
# не дольше OCR_BUDGET_S (шаг сбора обязан уложиться в потолок юнита, `ops/budgets.json`).
OCR_PAGE_TIMEOUT_S = 60.0
OCR_BUDGET_S = 180.0
DETAIL_CHARS = 200
# Кандидат, чей разбор ещё идёт, и он же — после такта, оборванного на разборе.
PENDING = "разбор не завершён"
INTERRUPTED = "разбор был оборван — число внести вручную"
# Статус разбора и вид документа словами — для причин деградации, которые печатает витрина (П§0.2).
STATUS_WORDS = {"needs_manual": "не распознан", "failed": "не прочитан"}
KIND_WORDS = {"ras_release": "релиза РСБУ", "ifrs_statements": "отчётности МСФО", "ifrs_press": "пресс-релиза МСФО"}

MONTHS = "январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр"
MONTH_NAMES_EN = ("january", "february", "march", "april", "may", "june", "july", "august",
                  "september", "october", "november", "december")
ROWS_PNL = (
    ("nii", r"^чист\w*\s+процентн"),
    ("fee", r"^чист\w*\s+комисс"),
    ("prov", r"расходы\s+на\s+резерв"),
    ("opex", r"^операционн\w*\s+расход"),
    ("pretax", r"прибыль\s+до\s+налог"),
    ("np", r"^чистая\s+прибыль\s*$"),
)
ROWS_CAP = (
    ("cap_base", r"^базовый\s+капитал"), ("cap_main", r"^основной\s+капитал"), ("cap_total", r"^общий\s+капитал"),
    ("n1_1", r"достаточн\w*\s+базов"), ("n1_2", r"достаточн\w*\s+основн"), ("n1_0", r"достаточн\w*\s+общ"),
)
PERCENT_KEYS = frozenset({"n1_0", "n1_1", "n1_2"})


class NeedsManual(RuntimeError):
    """PDF без текста, а распознавания на машине нет: число вносится руками."""


# ------------------------------------------------------------------ текстовый слой

def text_pages(body: bytes) -> list[str]:
    """Текст страниц (pdfplumber, импорт ленивый); пустая страница — ''."""
    import pdfplumber  # noqa: PLC0415 — зависимость только инструмента разбора PDF

    with pdfplumber.open(io.BytesIO(body)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def has_text_layer(pages: list[str]) -> bool:
    return sum(len(p.strip()) for p in pages) >= TEXT_LAYER_MIN_CHARS


_AMOUNT = re.compile(r"\(?-?\d{1,3}(?:[  ]\d{3})*,\d+\)?")


def amounts(line: str) -> list[float]:
    """Суммы строки отчётности: «1 234,5», «(123,4)» → −123,4; номера примечаний без запятой пропускаются."""
    out = []
    for tok in _AMOUNT.findall(line):
        neg = tok.startswith("(") or tok.startswith("-")
        v = float(tok.strip("()-").replace(" ", "").replace(" ", "").replace(",", "."))
        out.append(-v if neg else v)
    return out


IFRS_ROWS = (
    ("nii", r"^Чистые процентные доходы\b(?!\s+после)"),
    ("llp_debt_fa", r"^Чистый расход от создания резерва под кредитные убытки по долговым"),
    ("pbt", r"^Прибыль до налогообложения"),
    ("tax", r"^Расход по налогу на прибыль"),
    ("ni", r"^Прибыль за период"),
    ("ni_sh", r"(?:^|:\s*)-\s*акционеров Банка"),
)


def _logical_lines(page: str) -> list[str]:
    """Строки с подписью, перенесённой на следующую строку, склеиваются (сумм в первой нет)."""
    out: list[str] = []
    carry = ""
    for raw in page.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not amounts(line) and re.match(r"^[А-ЯA-Z(\-]", line) and len(line) > 20:
            carry = (carry + " " + line).strip() if carry else line
            continue
        out.append((carry + " " + line).strip() if carry else line)
        carry = ""
    if carry:
        out.append(carry)
    return out


def parse_ifrs_statements(pages: list[str]) -> dict[str, Any]:
    """Отчёт о прибылях и убытках МСФО → {строка: [суммы колонок]} (млрд ₽, расход со знаком минус).

    Колонки — как в документе: у промежуточного за 6 и 9 месяцев — [период, период
    прошлого года, квартал, квартал прошлого года]; у 3 месяцев и года — [период,
    прошлый год]. Квартал для года — год минус 9 месяцев (считает вызывающий).
    """
    found: dict[str, list[float]] = {}
    page_no = None
    for i, page in enumerate(pages):
        if "отчет о прибылях и убытках" not in page.lower():
            continue
        for line in _logical_lines(page):
            for key, rx in IFRS_ROWS:
                if key not in found and re.search(rx, line):
                    vals = amounts(line)
                    if vals:
                        found[key] = vals
                        page_no = page_no or i + 1
        if "ni_sh" in found:
            break
    if not found:
        raise ValueError("в PDF не найден отчёт о прибылях и убытках")
    out: dict[str, Any] = {"page": page_no, "columns": found}
    for key, vals in found.items():
        out[f"{key}_period"] = vals[0]
        out[f"{key}_quarter"] = vals[2] if len(vals) >= 4 else None
    return out


# ------------------------------------------------------------------ распознавание

def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def tsv_lines(tsv: str) -> list[dict[str, Any]]:
    """TSV tesseract (уровень слов) → строки {x, y, x2, h, text} по (block, par, line)."""
    groups: dict[tuple, list[dict]] = {}
    header = None
    for raw in tsv.splitlines():
        cols = raw.split("\t")
        if header is None:
            header = cols
            continue
        if len(cols) != len(header):
            continue
        row = dict(zip(header, cols))
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        groups.setdefault(key, []).append({"x": int(row["left"]), "y": int(row["top"]),
                                           "w": int(row["width"]), "h": int(row["height"]), "t": row["text"]})
    return [_line(ws) for _, ws in sorted(groups.items())]


def _line(ws: list[dict]) -> dict[str, Any]:
    ws = sorted(ws, key=lambda w: w["x"])
    return {"x": ws[0]["x"], "y": min(w["y"] for w in ws), "x2": max(w["x"] + w["w"] for w in ws),
            "h": max(w["h"] for w in ws), "text": " ".join(w["t"] for w in ws)}


def words_tsv_lines(tsv: str) -> list[dict[str, Any]]:
    """Слова «line x y w h text» (формат снимков распознавания этапа 1) → строки."""
    by: dict[int, list[dict]] = {}
    for raw in tsv.splitlines():
        if not raw.strip():
            continue
        li, x, y, w, h, t = raw.split("\t", 5)
        by.setdefault(int(li), []).append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "t": t})
    return [_line(ws) for _, ws in sorted(by.items())]


def _brief(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:DETAIL_CHARS]


def ocr_pages(body: bytes, *, lang: str = "rus", page_timeout: float = OCR_PAGE_TIMEOUT_S,
              budget: float = OCR_BUDGET_S) -> list[list[dict[str, Any]]]:
    """Рендер pypdfium2 + tesseract TSV → строки по страницам.

    Любой отказ — `NeedsManual` с причиной (шапка модуля): зависший или упавший tesseract,
    нерендерящийся PDF и мусор в выводе не роняют сборщик. `page_timeout` — секунд на страницу,
    `budget` — на весь документ.
    """
    if not tesseract_available():
        raise NeedsManual("PDF без текстового слоя, tesseract не установлен")
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415 — зависимость только инструмента разбора PDF
    except ImportError as exc:
        raise NeedsManual("PDF без текстового слоя, pypdfium2 не установлен") from exc

    started = time.monotonic()
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            pdf = pdfium.PdfDocument(body)
            count = len(pdf)
        except Exception as exc:  # noqa: BLE001 — ошибки pdfium: битый или защищённый PDF
            raise NeedsManual(f"PDF не открывается для рендера — {_brief(exc)}") from exc
        for i in range(count):
            where = f"стр. {i + 1} из {count}"
            left = budget - (time.monotonic() - started)
            if left <= 0:
                raise NeedsManual(f"распознавание не уложилось в {budget:.0f} с ({where})")
            png = Path(tmp) / f"p{i + 1}.png"
            try:
                pdf[i].render(scale=RENDER_SCALE).to_pil().convert("L").save(png)
            except Exception as exc:  # noqa: BLE001 — pdfium, растровая библиотека, диск
                raise NeedsManual(f"{where} не рендерится — {_brief(exc)}") from exc
            wait = min(page_timeout, left)
            try:
                done = subprocess.run(["tesseract", str(png), "-", "-l", lang, "--psm", "6", "tsv"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=wait)
            except subprocess.TimeoutExpired as exc:
                raise NeedsManual(f"tesseract не ответил за {wait:.0f} с ({where})") from exc
            except OSError as exc:
                raise NeedsManual(f"tesseract не запустился — {_brief(exc)}") from exc
            if done.returncode != 0:
                said = (done.stderr or "").strip().splitlines()
                raise NeedsManual(f"tesseract: код {done.returncode} ({where})"
                                  + (f" — {said[-1][:DETAIL_CHARS]}" if said else ""))
            try:
                pages.append(tsv_lines(done.stdout))
            except (ValueError, KeyError) as exc:
                raise NeedsManual(f"вывод tesseract не разобран ({where}) — {_brief(exc)}") from exc
    return pages


def _clean(t: str) -> str:
    t = t.strip().replace("¯", "-").replace("–", "-").replace("−", "-")
    t = t.replace("З", "3").replace("з", "3").replace("О", "0").replace("о", "0").replace("б", "6")
    return re.sub(r"\s*,\s*", ",", t)


def to_amount(tok: str) -> float | None:
    """Сумма из ячейки распознавания: «2 345,6», «-123,4», «1 0644» (потерянная запятая)."""
    t = _clean(tok)
    if re.search(r"(%|0/0|96|пп|х|x)$", t) and not re.fullmatch(r"-?\d[\d ]*", t):
        return None
    neg = t.startswith("-")
    t = t.lstrip("-").strip()
    if not re.fullmatch(r"\d[\d ]*(,\s?\d+)?", t):
        return None
    if "," in t:
        a, b = t.split(",", 1)
        v = float(a.replace(" ", "") + "." + b.strip())
    else:
        digits = t.replace(" ", "")
        parts = t.split(" ")
        if len(parts) >= 2 and len(parts[-1]) in (1, 4) or (len(parts) == 1 and len(digits) >= 4):
            v = float(digits[:-1] + "." + digits[-1])
        else:
            v = float(digits)
    return -v if neg else v


def to_percent(tok: str) -> float | None:
    """«12,396» / «12,3%» / «12,30/0» (так распознаётся «%») → доля 0,123."""
    t = _clean(tok).replace(" ", "")
    t = re.sub(r"(0/0|%|o/o)$", "", t)
    m = re.fullmatch(r"(-?\d{1,2}),(\d)(96)?", t) or re.fullmatch(r"(-?\d{1,2}),(\d{2})", t)
    if not m:
        return None
    return round(float(m.group(1) + "." + m.group(2)) / 100.0, 10)


def _columns(lines: list[dict]) -> dict[str, dict]:
    """Заголовки колонок: «8М 2026» (нарастающим) и «август 2026» (месяц); колонки изменений (со «/») — мимо."""
    cols: dict[str, dict] = {}
    for ln in lines:
        t = ln["text"].lower().strip()
        if "/" in t:
            continue
        if re.fullmatch(r"[\dзвдтm-]{1,3}\s?[мm]\s?\d{4}", t) and "ytd" not in cols:
            cols["ytd"] = ln
        elif re.fullmatch(rf"({MONTHS})\w*\s?\d{{4}}", t) and "month" not in cols:
            cols["month"] = ln
        elif re.fullmatch(r"1\s+\w+\s+\d{4}\d?", t) and "date" not in cols:
            cols["date"] = ln
    return cols


def _cells(lines: list[dict], label: dict, cols: dict[str, dict]) -> dict[str, str]:
    """Ячейки строки: числовые строки распознавания на высоте подписи — к ближайшей колонке по x."""
    cy = label["y"] + label["h"] / 2
    tol = max(label["h"], 20) * 0.9
    out: dict[str, tuple[float, str]] = {}
    for ln in lines:
        if ln is label or re.search(r"[А-Яа-яA-Za-z]{3,}", ln["text"]):
            continue
        if abs(ln["y"] + ln["h"] / 2 - cy) > tol or ln["x"] <= label["x2"]:
            continue
        centre = (ln["x"] + ln["x2"]) / 2
        name, dist = None, None
        for key, col in cols.items():
            d = abs(centre - (col["x"] + col["x2"]) / 2)
            if dist is None or d < dist:
                name, dist = key, d
        if name is not None and (name not in out or dist < out[name][0]):
            out[name] = (dist, ln["text"])
    return {k: v[1] for k, v in out.items()}


def parse_ras_table(lines: list[dict]) -> dict[str, float]:
    """Таблица P&L релиза РСБУ (строки распознавания страницы) → {`nii_ytd`, `nii_m`, …} млрд ₽."""
    cols = _columns(lines)
    if not {"ytd", "month"} <= set(cols):
        return {}
    out: dict[str, float] = {}
    for key, rx in ROWS_PNL:
        labels = [ln for ln in lines if re.search(rx, ln["text"].lower().replace("ё", "е"))
                  and ln["x"] < cols["ytd"]["x"]]
        for label in labels:
            cells = _cells(lines, label, {"ytd": cols["ytd"], "month": cols["month"]})
            ytd, month = to_amount(cells.get("ytd", "")), to_amount(cells.get("month", ""))
            if ytd is not None or month is not None:
                if ytd is not None:
                    out[f"{key}_ytd"] = ytd
                if month is not None:
                    out[f"{key}_m"] = month
                break
    return out


def parse_ras_capital(lines: list[dict]) -> dict[str, float]:
    """Таблица капитала релиза: базовый/основной/общий (млрд ₽) и Н1.x (доли) на 1-е число."""
    cols = _columns(lines)
    col = cols.get("date")
    if col is None:
        return {}
    out: dict[str, float] = {}
    for key, rx in ROWS_CAP:
        labels = [ln for ln in lines if re.search(rx, ln["text"].lower()) and ln["x"] < col["x"]]
        for label in labels:
            cell = _cells(lines, label, {"date": col}).get("date")
            if not cell:
                continue
            if key in PERCENT_KEYS:
                v = to_percent(cell)
            elif re.fullmatch(r"\d{1,3}(?: \d{3})+", cell.strip()):
                v = float(cell.replace(" ", ""))          # «6 247» — целые млрд ₽ в таблице капитала
            else:
                v = to_amount(cell)
            if v is not None:
                out[key] = v
                break
    return out


def parse_ras_pdf_lines(pages: list[list[dict]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for lines in pages:
        for k, v in parse_ras_table(lines).items():
            out.setdefault(k, v)
        for k, v in parse_ras_capital(lines).items():
            out.setdefault(k, v)
    return out


# ------------------------------------------------------------------ классификация и сбор

def classify(url: str, kinds: dict[str, str]) -> str | None:
    for kind, pattern in kinds.items():
        if re.search(pattern, url, re.I):
            return kind
    return None


def ras_month_from_url(url: str, patterns: list[str]) -> str | None:
    """Месяц релиза РСБУ из имени файла по шаблонам с группами `months`/`month_name` и `year`."""
    for pat in patterns:
        m = re.search(pat, url, re.I)
        if not m:
            continue
        g = m.groupdict()
        year = int(g["year"])
        if g.get("months"):
            return periods.month(year, int(g["months"]))
        if g.get("month_name"):
            name = g["month_name"].lower()
            for i, n in enumerate(MONTH_NAMES_EN):
                if name.startswith(n[:3]):
                    return periods.month(year, i + 1)
    return None


def download(ctx: Context, url: str, *, getter=None) -> tuple[http.Response, bool]:
    """Скачивание в защищённый архив; (ответ, новый ли винтаж).

    Документ берётся только по https с хостов эмитента (`sources.yaml → sber_pdf.hosts`): ни адрес,
    ни редирект на другой хост не исполняются, конечный адрес ответа сверяется ещё раз.
    """
    hosts = list((ctx.cfg.get("sber_pdf") or {}).get("hosts") or [])
    if not http.on_hosts(url, hosts):
        raise http.FetchError(url, None, "адрес не на хосте эмитента по https", final=True)
    index = ctx.store.read_state(INDEX_FILE) or {}
    resp = (getter or ctx.getter or http.fetch)(url, sink=http.Sink(ctx.store, SOURCE),
                                                name=url.rsplit("/", 1)[-1] or "document.pdf", hosts=hosts)
    if not http.on_hosts(resp.landed, hosts):
        raise http.FetchError(url, None, f"ответ пришёл с чужого адреса ({http.host_of(resp.landed)})", final=True)
    entry = index.setdefault(url, {"first_seen": resp.fetched_at, "versions": []})
    new = not entry["versions"] or entry["versions"][-1]["sha256"] != resp.sha256
    if new:
        entry["versions"].append({"sha256": resp.sha256, "fetched_at": resp.fetched_at,
                                  "last_modified": resp.headers.get("Last-Modified"),
                                  "etag": resp.headers.get("ETag")})
    entry["checked"] = resp.fetched_at
    ctx.store.write_state(INDEX_FILE, index)
    return resp, new


def analyse(body: bytes, kind: str) -> dict[str, Any]:
    """Разбор по виду документа → {status, numbers, method}."""
    try:
        pages = text_pages(body)
    except Exception as exc:  # noqa: BLE001 — битый PDF — не падение такта
        return {"status": "failed", "detail": f"pdfplumber: {type(exc).__name__}: {exc}", "numbers": {}}
    if has_text_layer(pages):
        if kind == "ifrs_statements":
            try:
                return {"status": "ok", "method": "text", "numbers": parse_ifrs_statements(pages)}
            except ValueError as exc:
                return {"status": "failed", "detail": str(exc), "numbers": {}}
        return {"status": "ok", "method": "text", "numbers": {}, "text": "\n".join(pages)[:50000]}
    if kind != "ras_release":
        return {"status": "needs_manual", "numbers": {},
                "detail": "PDF без текстового слоя: распознаётся только релиз РСБУ"}
    started = time.monotonic()
    try:
        numbers = parse_ras_pdf_lines(ocr_pages(body))
    except NeedsManual as exc:
        return {"status": "needs_manual", "detail": str(exc), "numbers": {}}
    except Exception as exc:  # noqa: BLE001 — мусор распознавания — не падение такта
        return {"status": "needs_manual", "detail": f"строки распознавания не разобраны — {_brief(exc)}",
                "numbers": {}}
    out = {"status": "ok" if numbers else "needs_manual", "method": "ocr", "numbers": numbers,
           "ocr_seconds": round(time.monotonic() - started, 1)}
    if not numbers:
        out["detail"] = "в распознанном тексте не найдена таблица релиза"
    return out


def _called(cand: dict[str, Any]) -> str:
    """Документ словами: «PDF релиза РСБУ за 2026M08» (имя файла — в записи кандидата, не в причине)."""
    words = KIND_WORDS.get(str(cand.get("kind")), "эмитента")
    return f"PDF {words}" + (f" за {cand['month']}" if cand.get("month") else "")


def collect(ctx: Context) -> Result:
    """PDF по ссылкам из новостей: новые — скачать и разобрать; старше недели — перекачать."""
    cfg = ctx.cfg.get("sber_pdf") or {}
    kinds = dict(cfg.get("kinds") or {})
    res = Result(name="sber_pdf")
    news = ctx.store.read_state("news/items.json") or {"pdf": {}}
    index = ctx.store.read_state(INDEX_FILE) or {}
    candidates = ctx.store.read_state(CANDIDATES_FILE) or {}
    for key, cand in candidates.items():
        if cand.get("status") == "needs_manual" and cand.get("detail") == PENDING:
            # прошлый такт оборвался на разборе этого файла: сказать один раз, заново не разбирать
            cand["detail"] = INTERRUPTED
            res.degrade(f"{_called(cand)}: {INTERRUPTED}", retry=False)
    for url in sorted(news.get("pdf") or {}):
        entry = index.get(url)
        if entry and entry.get("checked"):
            age = (ctx.today - date.fromisoformat(entry["checked"][:10])).days
            if age < RECHECK_DAYS:
                continue
        try:
            resp, new = download(ctx, url)
        except http.FetchError as exc:
            res.degrade(f"PDF эмитента: {exc}", retry=http.retryable(exc))
            continue
        res.raw += 1
        if not new:
            continue
        kind = classify(url, kinds) or "other"
        key = f"{url}#{resp.sha256[:12]}"
        about = {"url": url, "kind": kind, "sha256": resp.sha256, "fetched_at": resp.fetched_at}
        if kind == "ras_release":
            about["month"] = ras_month_from_url(url, list(cfg.get("ras_month_patterns") or []))
        # Запись — до разбора: индекс уже отметил файл проверенным (`download`).
        candidates[key] = {"status": "needs_manual", "detail": PENDING, "numbers": {}, **about}
        ctx.store.write_state(CANDIDATES_FILE, candidates)
        try:
            result = analyse(resp.body, kind)
        except Exception as exc:  # noqa: BLE001 — разбор одного файла не роняет сборщик
            result = {"status": "needs_manual", "detail": f"разбор упал — {_brief(exc)}", "numbers": {}}
        result.update(about)
        candidates[key] = result
        if result["status"] != "ok":
            # файл скачан, не разобран: повтор сбора не поможет — число вносит человек
            res.degrade(f"{_called(result)} {STATUS_WORDS.get(result['status'], result['status'])} — "
                        f"{result.get('detail') or 'причина не названа'}; число внести вручную", retry=False)
    ctx.store.write_state(CANDIDATES_FILE, candidates)
    return res
