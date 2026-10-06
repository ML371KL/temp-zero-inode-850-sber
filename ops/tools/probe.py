"""Проба источников с сервера: доступны ли они той дорогой, какой ходят сборщики.

    python ops/tools/probe.py                   все пробы, таблица в stdout
    python ops/tools/probe.py --only iss cbr_forms
    python ops/tools/probe.py --pdf URL         статический PDF эмитента по адресу
    python ops/tools/probe.py --only sber_ocr   дымовая проверка распознавания PDF релиза
    python ops/tools/probe.py --json ФАЙЛ       ещё и строки таблицы в JSON

Первый шаг выкладки на новую машину (DESIGN §6.2: проверено с ноутбука, с
сервера — пробой) и первый шаг разбора «источник молчит». Ходит через
`indicators.http` — ту же таблицу User-Agent по хостам, без прокси окружения,
с закреплённым корнем только для хостов T-Invest и эмитента, — поэтому проба проверяет
именно боевую дорогу, а не соседнюю. Одна попытка на пробу, без повторов и
без записи в сырой архив; эмитент (тикеры, регномер) — из `meta.company` книги.

Пробы:
  iss          MOEX ISS: котировки категорий акций (TQBR) и кривая zcyc
  cbr_soap     ЦБ DailyInfo (SOAP): ключевая ставка за 30 дней
  cbr_forms    ЦБ CreditOrgInfo (SOAP) по регномеру: GetDatesForF101/102/123/135 — 4 метода
  avgprocstav  ЦБ, HTML: максимальная ставка по вкладам топ-10 по декадам
  dataservice  ЦБ, JSON /dataservice: список публикаций
  sber_pdf     статический PDF эмитента. Адрес: `--pdf`; иначе последний адрес дозора из
               состояния; иначе — на свежем сервере — адрес PDF последнего релиза РСБУ из
               затравки `data/indicators/seed/ras_release.json` (хост — из `sources.yaml`).
               Откуда адрес, сказано в строке таблицы. HTML-страницы эмитента за антиботом
               не трогаются никогда
  tinvest      T-Invest: TLS с закреплённым корнем (и отказ без него); с токеном из
               окружения — ShareBy главного тикера. Токен не печатается никогда


Только по имени (`--only sber_ocr`; в общий прогон не входит — минуты работы):
  sber_ocr     дымовая проверка распознавания: PDF уже опубликованного релиза РСБУ (тот же
               адрес, что у `sber_pdf`) проходит дорогой дозора — рендер и tesseract
               (`indicators.sber_pdf.analyse`), — и распознанные ЧПД и чистая прибыль
               сверяются с числами этого месяца в затравке (допуск — `sources.yaml`,
               `release_watch.tolerance`). В строке — время и пик памяти: они идут в замер
               бюджета такта дозора. Без неё tesseract впервые исполнился бы в день релиза

Коды: 0 — все пробы прошли (на пустом состоянии свежего сервера — тоже); 1 — хоть одна
не прошла (строка «НЕТ» с причиной); 64 — неверные аргументы.
"""
# Без `from __future__ import annotations`: dataclass с отложенными аннотациями
# не грузится через importlib.util.spec_from_file_location (так модуль грузят тесты).
import argparse
import json
import re
import socket
import ssl
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import config, http  # noqa: E402

TIMEOUT = 30.0
CREDIT_ORG_INFO = "https://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx"
DAILY_INFO = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
DATASERVICE = "https://www.cbr.ru/dataservice/publications"
TINVEST_HOST = "invest-public-api.tinkoff.ru"
FORMS = ("101", "102", "123", "135")


@dataclass
class Row:
    probe: str
    what: str
    target: str               # хост и путь, без строки запроса
    ok: bool
    status: str               # HTTP-код или «TLS»/«—»
    seconds: float
    size: int | None
    note: str


def _target(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.hostname}{parts.path}"


def _timed(probe: str, what: str, url: str, call: Callable[[], tuple[bool, str, int | None, str]]) -> Row:
    """Одна проба: (годно, статус, байт, заметка) из `call`; отказ — строка «нет» с причиной."""
    start = time.monotonic()
    try:
        ok, status, size, note = call()
    except http.FetchError as exc:
        ok, status, size, note = False, str(exc.status or "—"), None, str(exc).split(": ", 1)[-1]
    except Exception as exc:  # noqa: BLE001 — проба называет любой отказ, а не падает
        ok, status, size, note = False, "—", None, f"{type(exc).__name__}: {exc}"[:200]
    return Row(probe, what, _target(url), ok, status, round(time.monotonic() - start, 2), size, note)


def _get(getter, url: str, **kwargs) -> http.Response:
    return (getter or http.fetch)(url, attempts=1, timeout=TIMEOUT, **kwargs)


# ------------------------------------------------------------------ пробы

def probe_iss(getter=None, *, company: dict, board: str) -> list[Row]:
    from indicators import iss  # noqa: PLC0415

    tickers = list(company["tickers"])
    url = iss.quotes_url(tickers, board)

    def quotes():
        resp = _get(getter, url)
        got = iss.parse_quotes(resp.json(), board)
        missing = [t for t in tickers if t not in got]
        prices = ", ".join(f"{t} {got[t].get('price')}" for t in tickers if t in got)
        return not missing, str(resp.status), len(resp.body), prices + (f"; нет {', '.join(missing)}" if missing else "")

    def zcyc():
        resp = _get(getter, iss.ZCYC_URL)
        curve = iss.parse_zcyc(resp.json())
        return True, str(resp.status), len(resp.body), f"кривая {curve['as_of']}: узлы {', '.join(curve['nodes'])}"

    return [_timed("iss", "котировки " + ", ".join(tickers), url, quotes),
            _timed("iss", "кривая zcyc", iss.ZCYC_URL, zcyc)]


def probe_cbr_soap(getter=None, *, today: date) -> list[Row]:
    from indicators import cbr  # noqa: PLC0415

    def key_rate():
        resp = http.soap(DAILY_INFO, "KeyRate", cbr.dates_inner(today - timedelta(days=30), today),
                         getter=lambda url, **kw: _get(getter, url, **kw))
        rows = cbr.parse_key_rate(resp.body)
        return True, str(resp.status), len(resp.body), f"ключевая {rows[-1][1]:.2%} на {rows[-1][0]}"

    return [_timed("cbr_soap", "DailyInfo KeyRate", DAILY_INFO, key_rate)]


def probe_cbr_forms(getter=None, *, company: dict) -> list[Row]:
    from indicators import cbr_forms  # noqa: PLC0415

    regnum = company["cbr_regnum"]
    rows = []
    for form in FORMS:
        op, inner = cbr_forms.dates_request(form, regnum)

        def dates(op=op, inner=inner):
            resp = http.soap(CREDIT_ORG_INFO, op, inner, getter=lambda url, **kw: _get(getter, url, **kw))
            got = cbr_forms.parse_dates(resp.body)        # ответ без дат — отказ пробы (FormError)
            return True, str(resp.status), len(resp.body), f"дат {len(got)}, последняя {max(got)}"

        rows.append(_timed("cbr_forms", f"{op} (регномер из книги)", CREDIT_ORG_INFO, dates))
    return rows


def probe_avgprocstav(getter=None, *, today: date) -> list[Row]:
    from indicators import cbr  # noqa: PLC0415

    url = cbr.deposits_url(today - timedelta(days=90), today)

    def page():
        resp = _get(getter, url)
        rows = cbr.parse_deposits(resp.text)
        return True, str(resp.status), len(resp.body), f"декад {len(rows)}, последняя {rows[-1][0]}: {rows[-1][1]:.3%}"

    return [_timed("avgprocstav", "ставки по вкладам топ-10", url, page)]


def probe_dataservice(getter=None) -> list[Row]:
    def publications():
        resp = _get(getter, DATASERVICE, headers={"Accept": "application/json"})
        data = resp.json()
        return isinstance(data, list) and bool(data), str(resp.status), len(resp.body), (
            f"публикаций {len(data)}" if isinstance(data, list) else f"не список: {type(data).__name__}")

    return [_timed("dataservice", "список публикаций", DATASERVICE, publications)]


def last_pdf_url(state: Path) -> str | None:
    """Последний адрес статического PDF эмитента, известный дозору (состояние сборщиков)."""
    for name, pick in (("sber_pdf/candidates.json", lambda k: k.split("#", 1)[0]),
                       ("sber_pdf/index.json", lambda k: k)):
        try:
            data = json.loads((state / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        urls = sorted({pick(k) for k in data if isinstance(k, str) and k.startswith("http")})
        if urls:
            return urls[-1]
    return None


PDF_LINK = re.compile(r"https://[^\s|<>]+?\.pdf", re.I)


def _pdf_hosts(cfg: dict) -> list[str]:
    return list((cfg.get("sber_pdf") or {}).get("hosts") or [])


def _seed_months(seed: dict | None) -> dict:
    months = (seed or {}).get("months")
    return months if isinstance(months, dict) else {}


def seed_pdf_url(cfg: dict, seed: dict | None = None) -> tuple[str, str] | None:
    """Адрес PDF последнего релиза РСБУ из затравки: (адрес, месяц) или None.

    Затравка `data/indicators/seed/ras_release.json` называет источник каждого числа; у
    чисел из релиза это адрес его PDF. Берётся самый поздний месяц, у которого такой адрес
    есть, и только https-адрес на хосте эмитента (`sources.yaml → sber_pdf.hosts`): в
    инструкцию адрес не вписывается — он устарел бы с первым релизом."""
    seed = config.load_seed("ras_release") if seed is None else seed
    hosts = _pdf_hosts(cfg)
    for month in sorted(_seed_months(seed), reverse=True):
        values = (_seed_months(seed)[month] or {}).get("values") or {}
        found: dict[str, int] = {}
        for node in values.values():
            for url in PDF_LINK.findall(str((node or {}).get("src") or "")):
                if http.on_hosts(url, hosts):
                    found[url] = found.get(url, 0) + 1
        if found:
            return max(sorted(found), key=found.get), month
    return None


def pdf_address(cfg: dict, *, pdf: str | None = None, state: Path | None = None,
                seed: dict | None = None) -> tuple[str | None, str, str | None]:
    """Адрес PDF для пробы: (адрес, откуда он, месяц затравки). Порядок: `--pdf` → последний
    адрес дозора в состоянии → адрес последнего релиза из затравки (свежий сервер)."""
    if pdf:
        return pdf, "адрес из --pdf", None
    known = last_pdf_url(state if state is not None else config.state_dir())
    if known:
        return known, "адрес дозора (состояние)", None
    seeded = seed_pdf_url(cfg, seed)
    if seeded:
        return seeded[0], f"адрес из затравки (релиз за {seeded[1]})", seeded[1]
    return None, "адреса нет", None


def probe_sber_pdf(getter=None, *, url: str | None, origin: str = "адрес из --pdf",
                   hosts: list[str] | None = None) -> list[Row]:
    if not url:
        return [Row("sber_pdf", "статический PDF эмитента", "—", False, "—", 0.0, None,
                    "адреса нет: ни дозор, ни затравка его не знают — задайте --pdf URL (из новости о релизе)")]

    def pdf():
        resp = _get(getter, url, **({"hosts": hosts} if hosts else {}))
        is_pdf = resp.body[:5] == b"%PDF-"
        return is_pdf, str(resp.status), len(resp.body), (
            f"PDF, sha256 {resp.sha256[:12]}…; {origin}" if is_pdf
            else f"ответ не PDF (страница вместо файла?); {origin}")

    return [_timed("sber_pdf", "статический PDF эмитента", url, pdf)]


# Строки релиза, без которых дымовая проверка не засчитывается: ЧПД и чистая прибыль
# нарастающим итогом (ключи разбора `indicators.sber_pdf` и затравки — одни).
OCR_REQUIRED = ("nii_ytd", "np_ytd")
OCR_WHAT = "распознавание PDF релиза (дымовая проверка)"


def _peak_memory() -> str:
    """Пик памяти пробы и её дочерних процессов (tesseract), МБ — в замер бюджета; вне Linux — пусто."""
    try:
        import resource  # noqa: PLC0415 — модуля нет на Windows
    except ImportError:
        return ""
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    return f"; пик памяти: проба {own:.0f} МБ, tesseract {children:.0f} МБ"


def probe_sber_ocr(getter=None, *, url: str | None, origin: str, month: str | None, cfg: dict,
                   seed: dict | None = None, analyse=None) -> list[Row]:
    """Дымовая проверка распознавания до таймеров: уже опубликованный релиз РСБУ проходит
    дорогой дозора, распознанные ЧПД и ЧП сверяются с принятыми числами месяца."""
    from indicators import sber_pdf  # noqa: PLC0415

    if not url:
        return [Row("sber_ocr", OCR_WHAT, "—", False, "—", 0.0, None,
                    "адреса нет: ни дозор, ни затравка его не знают — задайте --pdf URL релиза РСБУ")]
    pdf_cfg = cfg.get("sber_pdf") or {}
    month = month or sber_pdf.ras_month_from_url(url, list(pdf_cfg.get("ras_month_patterns") or []))
    seed = config.load_seed("ras_release") if seed is None else seed
    values = (_seed_months(seed).get(month) or {}).get("values") or {}
    accepted = {key: node["v"] for key, node in values.items()
                if isinstance(node, dict) and node.get("v") is not None}
    tolerance = (cfg.get("release_watch") or {}).get("tolerance") or {}
    hosts = _pdf_hosts(cfg)

    def recognise():
        if not accepted:
            return False, "—", None, f"в затравке нет принятых чисел месяца {month or '?'} — сверять не с чем; {origin}"
        resp = _get(getter, url, **({"hosts": hosts} if hosts else {}))
        if resp.body[:5] != b"%PDF-":
            return False, str(resp.status), len(resp.body), f"ответ не PDF (страница вместо файла?); {origin}"
        result = (analyse or sber_pdf.analyse)(resp.body, "ras_release")
        numbers = result.get("numbers") or {}
        if result.get("status") != "ok":
            return False, str(resp.status), len(resp.body), (
                f"распознавание не прошло: {result.get('status')} — {result.get('detail') or 'чисел нет'}; "
                f"{origin}" + _peak_memory())
        common = sorted(set(numbers) & set(accepted))
        off = [key for key in common
               if abs(float(numbers[key]) - float(accepted[key]))
               > float(tolerance.get("share" if key in sber_pdf.PERCENT_KEYS else "money") or 0.0)]
        missing = [key for key in OCR_REQUIRED if key not in common]
        shown = ", ".join(f"{key} {numbers[key]:g} (принято {accepted[key]:g})" for key in OCR_REQUIRED
                          if key in common)
        took = result.get("ocr_seconds")
        note = (f"месяц {month}, способ {result.get('method')}"
                + (f" ({took:g} с)" if isinstance(took, (int, float)) else "")
                + f": {shown or 'ЧПД и ЧП не распознаны'}; сверено строк {len(common)}"
                + (f", расходятся: {', '.join(off)}" if off else "")
                + (f", не распознаны: {', '.join(missing)}" if missing else "")
                + f"; {origin}" + _peak_memory())
        return not missing and not set(OCR_REQUIRED) & set(off), str(resp.status), len(resp.body), note

    return [_timed("sber_ocr", OCR_WHAT, url, recognise)]


def _tls(host: str, context: ssl.SSLContext | None) -> str:
    """Рукопожатие TLS; возвращает издателя сертификата, отказ — исключение."""
    ctx = context or ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=TIMEOUT) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as conn:
            issuer = dict(x[0] for x in conn.getpeercert().get("issuer", ()))
            return issuer.get("organizationName") or issuer.get("commonName") or "?"


def probe_tinvest(getter=None, *, company: dict, class_code: str, tls=_tls) -> list[Row]:
    from indicators import tinvest  # noqa: PLC0415

    url = tinvest.BASE + "InstrumentsService/ShareBy"
    pinned = http.tls_context(url)

    def with_pin():
        if pinned is None:
            return False, "TLS", None, f"для {TINVEST_HOST} нет закреплённого корня (indicators/http.py EXTRA_CA)"
        issuer = tls(TINVEST_HOST, pinned)
        return True, "TLS", None, f"рукопожатие с закреплённым корнем, издатель «{issuer}»"

    def without_pin():
        try:
            issuer = tls(TINVEST_HOST, None)
        except ssl.SSLError as exc:
            why = getattr(exc, "verify_message", None) or getattr(exc, "reason", None) or exc
            return True, "TLS", None, f"без закреплённого корня — отказ проверки, как ожидается ({why})"
        return True, "TLS", None, f"проходит и без закреплённого корня (издатель «{issuer}») — корень уже в системе"

    def share_by():
        if not tinvest.token():
            return False, "—", None, f"нет {tinvest.TOKEN_ENV} в окружении — источник будет «отсутствует», цены с ISS"
        main = company["main_ticker"]
        payload = tinvest.call("InstrumentsService/ShareBy",
                               {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": class_code, "id": main},
                               getter=lambda u, **kw: _get(getter, u, **kw))
        share = tinvest.parse_share(payload)
        return bool(share.get("uid")), "200", None, f"{main}: инструмент найден"

    return [_timed("tinvest", "TLS с закреплённым корнем", url, with_pin),
            _timed("tinvest", "TLS без закреплённого корня (справочно)", url, without_pin),
            _timed("tinvest", "ShareBy главного тикера (токен из окружения)", url, share_by)]


PROBES = ("iss", "cbr_soap", "cbr_forms", "avgprocstav", "dataservice", "sber_pdf", "tinvest")
# Только по имени: минуты работы и сверка качества, а не доступности источника.
EXTRA_PROBES = ("sber_ocr",)


def run(only: list[str] | None = None, *, pdf: str | None = None, getter=None, today: date | None = None,
        tls=_tls) -> list[Row]:
    today = today or date.today()
    company = config.company()
    cfg = config.sources()
    # Та же настройка, что у сборщика документов эмитента: корень издателя — его хостам.
    http.pin_trusted_root((cfg.get("sber_pdf") or {}).get("trusted_root_hosts") or [])
    board = (cfg.get("iss") or {}).get("board", "TQBR")
    class_code = (cfg.get("tinvest") or {}).get("class_code", board)
    chosen = only or list(PROBES)
    rows: list[Row] = []
    for name in chosen:
        if name == "iss":
            rows += probe_iss(getter, company=company, board=board)
        elif name == "cbr_soap":
            rows += probe_cbr_soap(getter, today=today)
        elif name == "cbr_forms":
            rows += probe_cbr_forms(getter, company=company)
        elif name == "avgprocstav":
            rows += probe_avgprocstav(getter, today=today)
        elif name == "dataservice":
            rows += probe_dataservice(getter)
        elif name == "sber_pdf":
            url, origin, _ = pdf_address(cfg, pdf=pdf)
            rows += probe_sber_pdf(getter, url=url, origin=origin, hosts=_pdf_hosts(cfg))
        elif name == "sber_ocr":
            url, origin, month = pdf_address(cfg, pdf=pdf)
            rows += probe_sber_ocr(getter, url=url, origin=origin, month=month, cfg=cfg)
        elif name == "tinvest":
            rows += probe_tinvest(getter, company=company, class_code=class_code, tls=tls)
    return rows


def table(rows: list[Row]) -> str:
    head = ("проба", "что", "адрес", "итог", "статус", "с", "байт", "заметка")
    body = [(r.probe, r.what, r.target, "да" if r.ok else "НЕТ", r.status, f"{r.seconds:.2f}",
             "—" if r.size is None else str(r.size), r.note) for r in rows]
    width = [max(len(str(x)) for x in col) for col in zip(head, *body)]
    fmt = lambda cells: "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, width)) + " |"  # noqa: E731
    return "\n".join([fmt(head), "|" + "|".join("-" * (w + 2) for w in width) + "|", *map(fmt, body)])


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=PROBES + EXTRA_PROBES,
                    help="только эти пробы (sber_ocr — только по имени)")
    ap.add_argument("--pdf", help="адрес статического PDF эмитента (иначе — дозор, затем затравка)")
    ap.add_argument("--json", help="записать строки таблицы в JSON")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 64 if exc.code else 0
    rows = run(args.only, pdf=args.pdf)
    print(table(rows))
    failed = [r for r in rows if not r.ok]
    print(f"\nпроб {len(rows)}, прошли {len(rows) - len(failed)}"
          + (f"; не прошли: {', '.join(sorted({r.probe for r in failed}))}" if failed else ""))
    if args.json:
        Path(args.json).write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
