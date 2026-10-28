"""Факты Сбербанка (data/facts/*.json) из листов этапа 1 папки передачи.

Запуск из корня репозитория (нужна папка передачи; в CI не запускается — JSON закоммичены):

    BANK_HANDOFF_DIR=<папка передачи> python -B ops/tools/build_facts.py            # переписать data/facts
    BANK_HANDOFF_DIR=<папка передачи> python -B ops/tools/build_facts.py --check    # сверить с data/facts, не писать
    BANK_HANDOFF_DIR=<папка передачи> python -B ops/tools/build_facts.py --out DIR  # записать в DIR
    … build_facts.py --anchor 2026Q3 --out DIR [--report ОТЧЁТ.json]                # якорь — другой квартал
    python -B ops/tools/build_facts.py --expected ОТЧЁТ.json --out DIR              # отчёт квартала → факты (без папки)
    … build_facts.py --check --sheet-template ЛИСТ.csv                              # шаблон листа следующего квартала

Якорь — аргумент `--anchor` (по умолчанию — `data/facts/anchor.json → period`): квартал, дата,
лист якоря, нарастающий итог, начало квартала, конец прошлого года, годы теста DPS, окно мостов,
дивиденды к выплате и флаг «норматив до вычета дивиденда» выводятся из него и из данных листов;
литералов квартала в коде нет (перечень мест, привязанных к якорю, — data/facts/SCHEMA.md §3).

Читает листы этапа 1 (stage1/ifrs, mgmt, ras, market, calib) и манифесты первички
(primary/MANIFEST-*.md, research/primary/MANIFEST.md); sha256 каждого упомянутого документа
пересчитывается по файлу и сверяется с листом. Каждое число — узел {"v", "src"} (документ,
страница, sha256) или {"v", "calc"} (формула из узлов); нераскрытое — {"v": null, "calc":
причина}. Схема — data/facts/SCHEMA.md, происхождение — docs/FACTS.md.

calendar.json и peers.json ведутся вручную (новости, календари ЦБ и банков-аналогов): сборщик их
не пишет. Печатает сверки: тождество баланса якоря, сумма книг ЧПД = ЧПД отчёта, PBT = сумма
строк ОПУ, DPS по формуле политики, мосты упр. ↔ МСФО и РСБУ → МСФО.

`--report` пишет файл отчёта квартала якоря (`quarter-report/1`) из собранных фактов — вход
`ops/tools/reanchor.py --facts` на настоящем отчёте; с `--facts КАТАЛОГ` — из готового каталога, без
папки передачи.

Лист квартала (SCHEMA.md §5) — строки отчёта и презентации нового квартала в формате листов этапа 1:
`--sheet-template` пишет шаблон листа квартала, следующего за якорем (что внести и откуда — место той же
строки в прошлом отчёте); заполненный лист лежит в папке передачи (`stage1/quarter/<период>.csv`) и
дополняет листы этапа 1; `--sheet ЛИСТ.csv` — черновик листа вне папки передачи (только с `--out`).

`--expected` — сухой прогон перезаякоривания (MODEL §15.2, docs/REANCHOR.md): факты следующего
квартала из закоммиченных фактов якоря и файла отчёта квартала с ожиданием модели — того же файла
`quarter-report/1`, что пишет `ops/tools/reanchor.py --expected` (правила — SCHEMA.md §4). Папка
передачи не нужна; пишет только в `--out` (в data/facts — никогда).

Код выхода: 0 — собрано (или --check: файлы совпали); 1 — расхождение, ошибка сверки или нет папки.
"""

from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FACTS = REPO / "data" / "facts"

# Начала рядов — параметры сборщика (не модели) и к якорю не привязаны: это границы собранной
# истории. Всё, что зависит от якоря, выводится из него (class Anchor) и из данных листов.
PNL_FIRST = "2023Q1"          # первый квартал квартальной МСФО (1К23 — начало ряда этапа 1)
MGMT_FIRST = "2019Q1"         # первый квартал управленческих метрик в ряду
BRIDGE_FIRST = "2024Q1"       # начало окна моста упр. ↔ МСФО: первый квартал полной формы ОПУ; конец окна — якорь (MODEL §4.6)
DIV_FIRST_YEAR = 2023         # первый год прибыли теста DPS: первый год действующей политики (п. 6.4; MODEL прил. C)
CAPITAL_HISTORY_FIRST = "2022Q4"
# Первый якорь, на котором ставки якоря книг активов несут процентный доход вне книг (MODEL §15.2 п. 4;
# правило перезаякоривания — то же в ops/tools/reanchor.py). Прежние якоря — ставка = проценты книги /
# средний остаток, остаток ЧПД — отдельной строкой other_interest_net_q.
RATES_CARRY_FROM = "2026Q3"
POLICY_DOC = "research/primary/dividend_policy_2023.pdf"
POLICY_APPROVED = "2023-12-05"             # с. 1: «Утверждено… Протокол от 05.12.2023 № 34»
POLICY_APPROVED_BY = "Наблюдательный совет, протокол от 05.12.2023 № 34"
POLICY_TERM_YEARS = 3                      # п. 6.4, с. 7: срок действия — 3 года с даты утверждения
POLICY_TERM_PAGE = 7
# Записка о сроке печатается на витрине: словами, дата — с годом ({until} — срок, он считается). После срока
# выпуск несёт пометку «срок политики истёк», сборку истёкший документ не останавливает (MODEL §5.1, §14.3).
POLICY_TERM_NOTE = ("Положение действует 3 года с даты утверждения Наблюдательным советом (п. 6.4) — по {until}. Новая "
                    "политика ожидается вместе со стратегией в ноябре 2026 г. (День инвестора — в календаре событий) и "
                    "войдёт новым фактом и новой версией книги. Если к этой дате новой политики нет, расчёт идёт по "
                    "прежней, а выпуск несёт пометку об истёкшем сроке")
# Продление срока без нового Положения (решение Наблюдательного совета, сообщение эмитента): словарь
# {"until": дата «по», "file": копия сообщения в папке передачи, "url": адрес, "key": ключ реестра документов,
# "title": заголовок, "words": что сказано о сроке}; None — продления нет (SCHEMA.md §3).
POLICY_EXTENSION = None
SNAPSHOT_RU = "30.09.2026"    # дата снимков рынка в папке передачи (ISS, брокерский календарь, ключевая ставка ЦБ)
# Мосты РСБУ → МСФО для нау-каста ЧПМ (T3): окно — последние N кварталов до якоря. Отношение ЧПД
# колеблется без тренда (прошлый квартал и среднее четырёх — равные ошибки), отношение процентных
# активов дрейфует вверх (прошлый квартал вдвое точнее среднего четырёх); бэктест пишется в факт.
RAS_NII_WINDOW_Q = 4
RAS_IEA_WINDOW_Q = 1
RAS_SHEET_TITLE = "Этап 1: месячные релизы РСБУ банка 2023–2026 (лист с источником каждой ячейки)"
RAS_NII_STABLE_FROM = "2025Q1"   # с этого квартала отношение ЧПД МСФО / РСБУ держится в 1,12–1,18 (сверка сборщика и теста)
# Процентные активы банка РСБУ по форме 0409101 (агрегированная форма с 06.2023; актив, исходящие
# остатки). Имена счетов в справочнике ЦБ — старого плана счетов: по действующему 501/502/504 —
# долговые бумаги по СС через ПУ / через ПСД / по амортизированной стоимости. «45.0|45.1» — 45.0,
# а до 01.2024 (его нет) — 45.1. Обязательные коды — без них квартал не считается; прочие
# отсутствуют в форме при нулевом остатке.
F101_IEA = {
    "loans": ("45.0|45.1", "45.2", "441", "442", "443", "444", "445", "446", "447", "448", "449", "450",
              "451", "453", "454"),
    "interbank": ("32.1", "32.2", "319"),
    "securities": ("501", "502", "504"),
}
F101_REQUIRED = ("45.0|45.1", "45.2", "32.1", "32.2", "501", "502", "504")
REGISTER_KINDS = ("tinvest", "news", "manual")   # виды источников записи реестра (MODEL §15)
# Дивиденды — публичные факты: лист решений годовых собраний (год прибыли, дивиденд на акцию каждой категории, дата
# отсечки, дата решения) с источником каждой строки — страница «Дивиденды» на сайте банка и новость о решении.
# Брокерский календарь (лист этапа 1 в папке передачи) в факты не идёт: сборщик только сверяет с ним размер,
# отсечку и последний день покупки и пишет итог сверки словами.
DIVIDENDS_SHEET = "stage1/market/shares_dividends/dividends_public.csv"
DIVIDENDS_KEY = "dividends_public"
BROKER_SHEET = "market/shares_dividends/dividends_2014_2026.csv"
T1_SWITCH = "2023-07-31"      # с этой даты расчёты на Московской бирже — T+1 (последний день T+2 — 28.07.2023)
PAY_WORKDAYS = 10             # срок выплаты номинальным держателям — 10 рабочих дней после отсечки (п. 6 ст. 42 закона
                              # об акционерных обществах; тот же срок называет страница «Дивиденды» банка)
HOLIDAYS = ((1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8), (2, 23), (3, 8), (5, 1), (5, 9), (6, 12),
            (11, 4))          # нерабочие праздничные дни (ст. 112 Трудового кодекса); праздник в выходной — выходной в понедельник
F135_GAP_NOTE = "провал раскрытия 2022–2023"     # формы 0409135 этих лет ЦБ не публиковал
SUB_COMP_SOURCE = "вторичный источник rg.ru 31.12.2025, по первичному документу не проверено"   # компенсация по льготной ипотеке сверх ключевой
RAS_Q1_NOTE = "1К23 — релиз"                     # квартал, за который формы 0409102 нет: ЧП РСБУ — из релиза
# Новость о решении ГОСА с копией страницы в папке передачи (по году прибыли): сборщик читает копию и сверяет с её
# текстом дату решения, дивиденд и дату реестра. У прочих лет новость названа в листе решений (издание, дата, адрес).
AGM_NEWS = {2025: ("stage2/reports/facts-sources/interfax_1099207_20260630.html", "https://www.interfax.ru/business/1099207",
                   "ifx_agm_2026", "Интерфакс")}
# Законная ставка налога на прибыль года прибыли (сверка налога в примечании МСФО): документ,
# страница, ставка, что сказано. Новый год прибыли в тесте DPS — новая строка.
TAX_STATUTORY = {
    2023: ("primary/ifrs_FY2024_statements.pdf", 84, 0.20, "законная ставка налога на прибыль 2023 г. — 20 % (примечание о налоге)"),
    2024: ("primary/ifrs_FY2025_statements_sber.pdf", 86, 0.20, "законная ставка 2024 г. — 20 % (примечание о налоге)"),
    2025: ("primary/ifrs_FY2025_statements_sber.pdf", 86, 0.25, "законная ставка 2025 г. — 25 % (примечание о налоге; 176-ФЗ от 12.07.2024)"),
}
# Прямые источники управленческих метрик, которые лист этапа 1 выводит из соседней страницы
# («выведено: …»): документ и страница, где число напечатано. Вывод листа остаётся сверкой в calc.
MGMT_DIRECT = {
    ("nim", "2023Q3"): ("ifrs_9M2023_press_release.pdf", 7),
    ("nim", "2023Q2"): ("2023Q2_ifrs_press_release.pdf", 3),
    ("roe", "2023Q2"): ("2023Q2_ifrs_press_release.pdf", 3),
    ("cir", "2023Q2"): ("2023Q2_ifrs_press_release.pdf", 3),
}
# Гайденс сектора: отношение банка к прогнозу сектора — структурное поле relation и слова
# презентации, по которым оно сверяется с листом (MODEL прил. B).
RELATION_WORDS = {"in_line": "в соответствии с сектором", "above": "лучше сектора"}
MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря")
MONTHS_NOM = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
              "ноябрь", "декабрь")

# Лист квартала (SCHEMA.md §5): строки отчёта и презентации нового квартала в формате листов этапа 1.
# Заполненные листы лежат в папке передачи — <QUARTER_SHEETS>/<период>.csv — и дополняют листы этапа 1.
QUARTER_SHEETS = "stage1/quarter"
SHEET_NAMES = ("ifrs_history", "ifrs_anchor", "ifrs_quarterly", "mgmt_kpi")      # колонка sheet листа квартала
SHEET_COLUMNS = ("sheet", "metric", "what", "period", "value", "unit", "doc", "page", "sha256", "note",
                 "prev_period", "prev_value", "prev_doc", "prev_page", "prev_note")

BUILT = ("anchor", "balance", "nii_books", "pnl_quarterly", "capital", "shares", "dividends",
         "bridge_mgmt_ifrs", "bridge_ras_ifrs", "mgmt_quarterly", "guidance")
MANUAL = ("calendar", "peers")


class BuildError(RuntimeError):
    """Нет листа, строки или документа; не сошлась сверка."""


# ------------------------------------------------------------------ периоды

def q_parse(p: str) -> tuple[int, int]:
    return int(p[:4]), int(p[5])


def q_range(a: str, b: str) -> list[str]:
    y, k = q_parse(a)
    out = []
    while f"{y}Q{k}" <= b:
        out.append(f"{y}Q{k}")
        y, k = (y + 1, 1) if k == 4 else (y, k + 1)
    return out


def q_end(p: str) -> dt.date:
    y, k = q_parse(p)
    return dt.date(y, 3 * k, 31 if k in (1, 4) else 30)


def q_prev(p: str) -> str:
    y, k = q_parse(p)
    return f"{y - 1}Q4" if k == 1 else f"{y}Q{k - 1}"


def q_days(p: str) -> int:
    return (q_end(p) - q_end(q_prev(p))).days


def ifrs_q(p: str) -> str:
    """2026Q2 → 2Q2026 (обозначение листов МСФО этапа 1)."""
    y, k = q_parse(p)
    return f"{k}Q{y}"


def q_next(p: str) -> str:
    y, k = q_parse(p)
    return f"{y + 1}Q1" if k == 4 else f"{y}Q{k + 1}"


def d_ru(iso: str) -> str:
    """2026-03-31 → 31.03.2026 (даты в текстах calc)."""
    return dt.date.fromisoformat(iso).strftime("%d.%m.%Y")


def q_ru(p: str) -> str:
    """2026Q2 → 2К26."""
    y, k = q_parse(p)
    return f"{k}К{y % 100:02d}"


def is_workday(d: dt.date) -> bool:
    """Рабочий день пятидневной недели: не выходной, не праздник (HOLIDAYS) и не понедельник после праздника,
    выпавшего на выходной. Январские каникулы и особые переносы правило не знает — даты дивидендов туда не
    попадают, а экс-даты сборщик сверяет с торговым календарём листа этапа 1."""
    if d.weekday() >= 5 or (d.month, d.day) in HOLIDAYS:
        return False
    if d.weekday() == 0 and d.month != 1:
        return not any(((d - dt.timedelta(days=k)).month, (d - dt.timedelta(days=k)).day) in HOLIDAYS for k in (1, 2))
    return True


def workdays_after(d: dt.date, n: int) -> dt.date:
    """n-й рабочий день после даты d (сама дата не считается)."""
    while n > 0:
        d += dt.timedelta(days=1)
        n -= is_workday(d)
    return d


def workday_before(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while not is_workday(d):
        d -= dt.timedelta(days=1)
    return d


@dataclass(frozen=True)
class Anchor:
    """Якорь сборки и всё, что из него выводится (перечень — data/facts/SCHEMA.md §3)."""

    period: str                            # 2026Q2

    def __post_init__(self):
        if not re.fullmatch(r"\d{4}Q[1-4]", self.period):
            raise BuildError(f"якорь {self.period!r}: нужен квартал вида ГГГГQк (год, Q, номер квартала)")

    @property
    def year(self) -> int:
        return q_parse(self.period)[0]

    @property
    def quarter(self) -> int:
        return q_parse(self.period)[1]

    @property
    def date(self) -> str:                 # конец квартала якоря
        return q_end(self.period).isoformat()

    @property
    def open_date(self) -> str:            # конец прошлого квартала — начало квартала якоря
        return q_end(q_prev(self.period)).isoformat()

    @property
    def form_date(self) -> str:            # дата форм ЦБ на конец квартала (1-е число после него)
        return (q_end(self.period) + dt.timedelta(days=1)).isoformat()

    @property
    def prev_year_end(self) -> str:
        return f"{self.year - 1}-12-31"

    @property
    def fy_last_year(self) -> int:         # год последнего годового отчёта не позже якоря
        return self.year if self.quarter == 4 else self.year - 1

    @property
    def fy_last(self) -> str:              # он же в обозначениях листов
        return f"FY{self.fy_last_year}"

    @property
    def fy_last_date(self) -> str:
        return f"{self.fy_last_year}-12-31"

    @property
    def ytd(self) -> str:                  # нарастающий итог якоря в листах МСФО: 6M2026; год — FY2026
        return f"FY{self.year}" if self.quarter == 4 else f"{3 * self.quarter}M{self.year}"

    @property
    def ytd_ru(self) -> str:               # то же в текстах: 6М26; год — «2026 г.»
        return f"{self.year} г." if self.quarter == 4 else f"{3 * self.quarter}М{self.year % 100:02d}"

    @property
    def k_ru(self) -> str:                 # квартал в текстах без года: 2К
        return f"{self.quarter}К"

    @property
    def bridge_window(self) -> tuple[str, str]:
        return BRIDGE_FIRST, self.period


def default_anchor() -> str:
    """Якорь по умолчанию — квартал закоммиченных фактов (data/facts/anchor.json)."""
    path = FACTS / "anchor.json"
    if not path.exists():
        raise BuildError("нет data/facts/anchor.json — задайте якорь аргументом --anchor")
    return str(json.loads(path.read_text(encoding="utf-8"))["period"])


def ru(x: float, n: int = 1) -> str:
    """Число для текстов calc: запятая, минус — знаком «−»."""
    s = f"{x:.{n}f}".replace(".", ",")
    return s.replace("-", "−")


def r6(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def r1(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def rmse(errors: list[float]) -> float:
    return (sum(e * e for e in errors) / len(errors)) ** 0.5


def full_years(first: str, last: str) -> list[int]:
    """Годы, все четыре квартала которых лежат в [first, last]."""
    qs = set(q_range(first, last))
    return [y for y in range(q_parse(first)[0], q_parse(last)[0] + 1) if all(f"{y}Q{k}" in qs for k in range(1, 5))]


# ------------------------------------------------------------------ документы и узлы

SHORT = 16   # длина префикса sha256 в src; полный — в anchor.json → documents


class Docs:
    """Реестр упомянутых документов: sha256 → запись; проверка хэша по файлу."""

    def __init__(self, root: Path):
        self.root = root
        self.by_sha: dict[str, dict] = {}
        self.manifest = self._manifests()
        self._file_sha: dict[Path, str] = {}

    def _manifests(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        paths = sorted((self.root / "primary").glob("MANIFEST*.md")) + [self.root / "research" / "primary" / "MANIFEST.md"]
        for p in paths:
            if not p.exists():
                continue
            header = None
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.startswith("|"):
                    header = None
                    continue
                cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
                if header is None:
                    header = [c.lower() for c in cells]
                    continue
                if set(cells[0]) <= set("-: "):
                    continue
                row = dict(zip(header, cells))
                name = row.get("файл")
                if not name:
                    continue
                url = row.get("url", "")
                title = row.get("что это", "")
                out.setdefault(name, {"url": url.split(" ")[0] if url.startswith("http") else url,
                                      "title": title})
        return out

    def locate(self, name: str) -> Path | None:
        cand = [self.root / name] if "/" in name else []
        cand += [self.root / "primary" / Path(name).name, self.root / "research" / "primary" / Path(name).name]
        for c in cand:
            if c.exists():
                return c
        return None

    def sha_of(self, path: Path) -> str:
        if path not in self._file_sha:
            self._file_sha[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return self._file_sha[path]

    def add(self, name: str, sha: str | None = None, *, key: str | None = None, url: str | None = None,
            title: str | None = None, file: str | None = None) -> str:
        """Регистрирует документ; возвращает полный sha256. Файл найден — хэш сверяется."""
        path = self.locate(file or name)
        if path is not None:
            real = self.sha_of(path)
            if sha and not real.startswith(sha) and not sha.startswith(real[:len(sha)]):
                raise BuildError(f"sha256 документа {name}: лист {sha[:16]}…, файл {real[:16]}…")
            sha = real
            rel = path.relative_to(self.root).as_posix()
        else:
            rel = file
        if not sha or len(sha) != 64:
            raise BuildError(f"нет полного sha256 документа {name}")
        base = Path(name).name
        m = self.manifest.get(base, {})
        rec = self.by_sha.setdefault(sha, {
            "key": key or re.sub(r"\.[a-z]+$", "", base),
            "file": rel,
            "sha256": sha,
            "url": url or m.get("url") or None,
            "title": title or m.get("title") or base,
        })
        return rec["sha256"]

    def ref(self, name: str, page, sha: str | None = None, **kw) -> str:
        full = self.add(name, sha, **kw)
        where = f", с. {page}" if page not in (None, "") else ""
        return f"{Path(name).name}{where}, sha256 {full[:SHORT]}"

    def documents(self) -> list[dict]:
        return sorted(self.by_sha.values(), key=lambda r: (r["key"], r["sha256"]))


def dedup(src: str | None) -> str | None:
    """Убирает повторы ссылок «док, с. N, sha256 …» в одном src (порядок сохраняется)."""
    if not src:
        return src
    seen: list[str] = []
    for part in src.split("; "):
        if part not in seen:
            seen.append(part)
    return "; ".join(seen)


def node(v, src: str | None = None, calc: str | None = None, **extra) -> dict:
    """Узел факта. Число без src и calc — ошибка сборщика (то же правило, что у тестов)."""
    src = dedup(src)
    if v is None and not (src or calc):
        raise BuildError("null без причины (calc)")
    if v is not None and not any(isinstance(x, str) and any(ch.isalnum() for ch in x) for x in (src, calc)):
        raise BuildError(f"узел без источника: {v}")
    out = {"v": v}
    if src:
        out["src"] = src
    if calc:
        out["calc"] = calc
    out.update(extra)
    return out


def na(reason: str) -> dict:
    return node(None, calc=f"не раскрыто: {reason}")


# ------------------------------------------------------------------ листы этапа 1

DERIVED_RE = re.compile(r"выведено(?: из подписей)?:?\s*([^|]*)")
EXPR_RE = re.compile(r"(\d+(?:,\d+)?)\s*([+−])\s*(\d+(?:,\d+)?)")


def derived_expr(note: str, value: float) -> tuple[str | None, str]:
    """Записка листа «выведено: a ± b [и c ± d] (пояснение)» → (выражение, дающее значение строки; пояснение).

    В одной записке бывает вывод двух соседних периодов — берётся выражение, равное значению."""
    m = DERIVED_RE.search(note or "")
    if not m:
        return None, ""
    tail = m.group(1).strip()
    why = re.search(r"\(([^()]*)\)", tail)
    extra = f" ({why.group(1)})" if why else ""
    for a, op, b in EXPR_RE.findall(tail):
        x, y = float(a.replace(",", ".")), float(b.replace(",", "."))
        if abs((x + y if op == "+" else x - y) - value) < 5e-7:
            nd = max(len(t.split(",")[1]) if "," in t else 0 for t in (a, b))
            return f"{a} {op} {b} = {ru(value, nd)}", extra
    return "", extra


def derived_calc(note: str, value: float) -> str | None:
    """calc узла, выведенного листом: на странице напечатаны значение сравнимого периода и изменение
    к нему, самого числа нет. Без записки «выведено» — None."""
    expr, extra = derived_expr(note, value)
    if expr is None:
        return None
    if not expr:
        return "выведено из соседних значений страницы (лист этапа 1): самого числа на странице нет"
    return (f"выведено: {expr}{extra} — на странице напечатаны значение сравнимого периода и изменение к нему, "
            f"самого числа нет")


class Sheet:
    """Лист «metric, period, value, …, doc, page, sha256, note» → строки по (метрика, период)."""

    def __init__(self, path: Path | None, docs: Docs, *, direct: dict | None = None, name: str = ""):
        self.docs = docs
        self.name = name                   # имя листа в колонке sheet листа квартала
        self.direct = direct or {}         # (метрика, период) → (документ, страница), где число напечатано
        self.rows: dict[tuple[str, str], dict] = {}
        self.seen: dict[tuple[str, str], None] = {}   # прочитанные (метрика, период) по порядку — шаблон листа квартала
        if path is None:                   # пустой лист (якорь на годовом отчёте: всё — в каноническом ряду)
            return
        if not path.exists():
            raise BuildError(f"нет листа {path.name}")
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.rows.setdefault((r["metric"], r["period"]), r)

    def add(self, rows: list[dict], origin: str) -> int:
        """Дополняет лист строками листа квартала; возвращает число новых строк. Строка, которая уже
        есть в листе этапа 1 с другим значением, — отказ: изменённую редакцию вносят в сам лист."""
        added = 0
        for r in rows:
            key = (r["metric"], r["period"])
            old = self.rows.get(key)
            if old is None or old["value"] in ("", None):
                self.rows[key] = r
                added += 1
            elif abs(float(old["value"]) - float(r["value"])) > 1e-9:
                raise BuildError(f"{origin}: {self.name} / {key[0]} / {key[1]} = {r['value']}, а в листе этапа 1 — "
                                 f"{old['value']}: изменённую редакцию вносят в лист этапа 1, а не в лист квартала")
        return added

    def get(self, metric: str, period: str) -> float | None:
        self.seen.setdefault((metric, period))
        r = self.rows.get((metric, period))
        if r is None or r["value"] in ("", None):
            return None
        return float(r["value"])

    def need(self, metric: str, period: str) -> float:
        v = self.get(metric, period)
        if v is None:
            raise BuildError(f"нет строки {metric} / {period}")
        return v

    def sheet_src(self, metric: str, period: str) -> str:
        r = self.rows[(metric, period)]
        docs = [d.strip() for d in r["doc"].split("|")]
        pages = [p.strip() for p in str(r["page"]).split("|")]
        shas = [s.strip() for s in r["sha256"].split("|")]
        return "; ".join(self.docs.ref(d, p, s) for d, p, s in zip(docs, pages, shas))

    def src(self, metric: str, period: str) -> str:
        if (metric, period) in self.direct:
            doc, page = self.direct[(metric, period)]
            return self.docs.ref(doc, page)
        return self.sheet_src(metric, period)

    def calc_note(self, metric: str, period: str) -> str | None:
        r = self.rows[(metric, period)]
        note = r.get("note") or ""
        m = re.match(r"расчёт: (\S+ − \S+)", note)
        if m:
            return f"{m.group(1)} (разность нарастающих итогов)"
        if r["value"] in ("", None):
            return None
        if (metric, period) in self.direct:
            # число напечатано в другом документе (src); вывод листа остаётся сверкой
            expr, _ = derived_expr(note, float(r["value"]))
            where = self.sheet_src(metric, period)
            return (f"напечатано на странице; сверка — вывод из сравнимого периода: {expr} ({where})" if expr
                    else f"напечатано на странице; лист этапа 1 ссылался на {where}")
        return derived_calc(note, float(r["value"]))

    def n(self, metric: str, period: str, *, scale: float = 1.0, sign: float = 1.0, calc: str | None = None) -> dict:
        v = self.get(metric, period)
        if v is None:
            raise BuildError(f"нет строки {metric} / {period}")
        c = calc or self.calc_note(metric, period)
        return node(r6(v * scale * sign), self.src(metric, period), c)

    def has(self, metric: str, period: str) -> bool:
        return self.get(metric, period) is not None


PERIOD_NAME_RE = re.compile(r"\d{4}Q[1-4]")
SHEET_REQUIRED = ("sheet", "metric", "period", "value", "doc", "page", "sha256", "note")


def quarter_sheet_rows(path: Path) -> dict[str, list[dict]]:
    """Лист квартала → строки по листам этапа 1 (SCHEMA.md §5). Строка без значения ещё не внесена и
    пропускается; у внесённой обязательны лист, метрика, период, число и документ."""
    if not path.exists():
        raise BuildError(f"нет листа квартала {path}")
    out: dict[str, list[dict]] = {name: [] for name in SHEET_NAMES}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        lack = [k for k in SHEET_REQUIRED if k not in (reader.fieldnames or [])]
        if lack:
            raise BuildError(f"{path.name}: в листе квартала нет колонок {lack}")
        for i, r in enumerate(reader, start=2):
            value = (r["value"] or "").strip()
            if value == "":
                continue
            where = f"{path.name}, строка {i}"
            if r["sheet"] not in out:
                raise BuildError(f"{where}: лист «{r['sheet']}» вне {SHEET_NAMES}")
            if not (r["metric"] or "").strip() or not (r["period"] or "").strip() or not (r["doc"] or "").strip():
                raise BuildError(f"{where}: у внесённой строки нет метрики, периода или документа")
            try:
                float(value)
            except ValueError:
                raise BuildError(f"{where}: значение {value!r} — не число (десятичная точка, без пробелов)") from None
            # квартал разностью нарастающих итогов — два документа через « | »: страниц и хэшей столько же
            docs = [d.strip() for d in r["doc"].split("|")]
            pages = [x.strip() for x in (r["page"] or "").split("|")]
            shas = [x.strip() for x in (r["sha256"] or "").split("|")]
            if shas == [""]:               # хэш не вписан — сборщик посчитает его по файлу документа
                shas = [""] * len(docs)
            if len(pages) != len(docs) or len(shas) != len(docs):
                raise BuildError(f"{where}: документов {len(docs)}, страниц {len(pages)}, sha256 {len(shas)} — должно быть поровну")
            out[r["sheet"]].append({"metric": r["metric"].strip(), "period": r["period"].strip(), "value": value,
                                    "unit": (r.get("unit") or "").strip(), "doc": " | ".join(docs),
                                    "page": " | ".join(pages), "sha256": " | ".join(shas),
                                    "note": (r["note"] or "").strip()})
    return out


class Ctx:
    """Всё, что читается из папки передачи, и якорь сборки."""

    def __init__(self, root: Path, anchor: Anchor, sheets: tuple[Path, ...] = ()):
        self.root = root
        self.a = anchor
        s1 = root / "stage1"
        self.docs = Docs(root)
        extra, self.sheet_files = self._quarter_rows(sheets)     # строки листов кварталов по листам этапа 1
        self.H = Sheet(s1 / "ifrs" / "ifrs_history.csv", self.docs, name="ifrs_history")       # канонический ряд МСФО
        self.Q = Sheet(s1 / "ifrs" / "ifrs_quarterly.csv", self.docs, name="ifrs_quarterly")   # кварталы МСФО
        self.Q.add(extra["ifrs_quarterly"], "лист квартала")
        if not self.Q.has("nii", ifrs_q(anchor.period)):
            raise BuildError(f"в листах этапа 1 нет квартала якоря {anchor.period} (ifrs_quarterly.csv: nii / "
                             f"{ifrs_q(anchor.period)}): сначала листы отчёта или лист квартала (SCHEMA.md §3, §5), "
                             f"для сухого прогона — --expected")
        on_anchor = any(r["period"] == anchor.date for name in ("ifrs_history", "ifrs_anchor") for r in extra[name])
        self.A = Sheet(self._anchor_sheet(s1 / "ifrs", optional=on_anchor), self.docs,
                       name="ifrs_anchor")                                                     # якорь (+ годовые раскрытия)
        self.M = Sheet(s1 / "mgmt" / "mgmt_kpi.csv", self.docs, direct=MGMT_DIRECT, name="mgmt_kpi")   # управленческие метрики
        for sheet in (self.H, self.A, self.M):
            sheet.add(extra[sheet.name], "лист квартала")
        self.s1 = s1
        self.checks: list[tuple[str, bool, str]] = []
        self._calib: dict | None = None
        self._dividends: dict[int, dict] | None = None

    def _quarter_rows(self, sheets: tuple[Path, ...]) -> tuple[dict[str, list[dict]], list[str]]:
        """Строки листов кварталов: листы папки передачи не позже якоря (по порядку периодов), затем
        черновики из аргумента. Возвращает строки по листам этапа 1 и имена прочитанных файлов."""
        files = []
        folder = self.root / QUARTER_SHEETS
        for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
            if not PERIOD_NAME_RE.fullmatch(path.stem):
                raise BuildError(f"{QUARTER_SHEETS}/{path.name}: имя листа квартала — <период>.csv")
            if path.stem <= self.a.period:
                files.append(path)
        files += list(sheets)
        out: dict[str, list[dict]] = {name: [] for name in SHEET_NAMES}
        for path in files:
            for name, rows in quarter_sheet_rows(path).items():
                out[name] += rows
        return out, [f.name for f in files]

    def _anchor_sheet(self, folder: Path, *, optional: bool = False) -> Path | None:
        """Лист якоря — тот ifrs_anchor_*.csv, чей отчётный период кончается датой якоря. Якорь на
        годовом отчёте листа не требует (год — в каноническом ряду); не требует его и якорь, остатки
        которого несёт лист квартала (`optional`)."""
        hits = []
        for path in sorted(folder.glob("ifrs_anchor_*.csv")):
            with path.open(encoding="utf-8") as f:
                if any(r["period"] == self.a.date for r in csv.DictReader(f)):
                    hits.append(path)
        if len(hits) > 1:
            raise BuildError(f"листов якоря на {self.a.date} больше одного: {[h.name for h in hits]}")
        if not hits and self.a.quarter != 4 and not optional:
            raise BuildError(f"нет листа якоря ifrs_anchor_*.csv с остатками на {self.a.date}")
        return hits[0] if hits else None

    def calib(self) -> tuple[dict, str]:
        """Калибровка книг ЧПД на якоре (этап 1, calib-nii): (содержимое, ссылка). Файл обязан быть
        посчитан на тот же квартал — иначе разбиение процентов по кредитным книгам чужое."""
        rel = "stage1/calib/nii/data/nii_books_anchor_proposal.json"
        if self._calib is None:
            path = self.root / rel
            if not path.exists():
                raise BuildError(f"нет калибровки книг ЧПД {rel}")
            self._calib = json.loads(path.read_text(encoding="utf-8"))
            if self._calib.get("period") != self.a.period:
                raise BuildError(f"калибровка книг ЧПД посчитана на {self._calib.get('period')}, якорь — {self.a.period}: "
                                 f"пересчитать stage1/calib/nii/calib_nii.py на квартал якоря")
        ref = self.file_ref(rel, "calib-nii", key="calib_nii_anchor_proposal",
                            title="calib-nii: книги ЧПД на якоре (предложение этапа 1)")
        return self._calib, ref

    def calib_sub_range(self) -> str:
        """Разброс оценки льготной ипотеки при других темпах погашения (calib-nii, results.json) — для calc."""
        path = self.root / "stage1/calib/nii/data/results.json"
        if not path.exists():
            return ""
        sub = json.loads(path.read_text(encoding="utf-8")).get("subsidized") or {}
        alt = [float(x) for x in (sub.get("alt_runoff") or {}).values()]
        close = self.calib()[0]["books"]["mortgage_sub"]["balance_close"]["v"]
        if not alt or abs(float(sub.get("balance_anchor", 0.0)) - close) > 0.1:   # результаты — другого якоря
            return ""
        return f"; при других темпах погашения — {ru(min(alt) * 100)}–{ru(max(alt) * 100)} %"

    def dividends(self) -> dict[int, dict]:
        """Лист решений о дивидендах: год прибыли → строка (публичные источники — в самой строке)."""
        if self._dividends is None:
            path = self.root / DIVIDENDS_SHEET
            if not path.exists():
                raise BuildError(f"нет листа {DIVIDENDS_SHEET}")
            with path.open(encoding="utf-8") as f:
                rows = {int(r["fiscal_year"]): r for r in csv.DictReader(f)}
            for y, r in rows.items():
                paid = Decimal(r["dps_ordinary"]) > 0
                if paid != bool(r["record_date"]) or not r["decided_date"] or not r["issuer_page"].startswith("https://"):
                    raise BuildError(f"{DIVIDENDS_SHEET}: строка {y} — нет даты решения, страницы эмитента или отсечки")
                if not all(r[k] for k in ("news_outlet", "news_published", "news_title", "news_url")):
                    raise BuildError(f"{DIVIDENDS_SHEET}: строка {y} — нет новости о решении (издание, дата, заголовок, адрес)")
            self._dividends = rows
        return self._dividends

    def dividends_ref(self, what: str = "") -> str:
        rows = self.dividends()
        return self.file_ref(DIVIDENDS_SHEET, what, key=DIVIDENDS_KEY, url=next(iter(rows.values()))["issuer_page"],
                             title=(f"Решения годовых собраний акционеров Сбербанка о дивидендах за {min(rows)}–{max(rows)} гг.: "
                                    "страница «Дивиденды» на сайте банка и новости о решениях (сверено с брокерским "
                                    "календарём дивидендов)"))

    def broker_rows(self) -> dict[tuple[str, str], dict]:
        """Брокерский календарь дивидендов (лист этапа 1) — только для сверки и торгового календаря экс-дат."""
        return {(r["ticker"], r["fiscal_year"]): r for r in self.csv_rows(BROKER_SHEET)}

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append((name, ok, detail))

    def bal(self, metric: str, date: str) -> float | None:
        v = self.H.get(metric, date)
        return v if v is not None else self.A.get(metric, date)

    def bal_n(self, metric: str, date: str, **kw) -> dict:
        sheet = self.H if self.H.has(metric, date) else self.A
        return sheet.n(metric, date, **kw)

    def bal_src(self, metric: str, date: str) -> str:
        sheet = self.H if self.H.has(metric, date) else self.A
        return sheet.src(metric, date)

    def csv_rows(self, rel: str) -> list[dict]:
        p = self.s1 / rel
        if not p.exists():
            raise BuildError(f"нет листа {rel}")
        with p.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def file_ref(self, rel: str, what: str, *, key: str, url: str | None = None, title: str | None = None) -> str:
        """Ссылка на файл папки передачи (лист, ответ API) с его sha256."""
        p = self.root / rel
        if not p.exists():
            raise BuildError(f"нет файла {rel}")
        sha = self.docs.add(rel, None, key=key, url=url, title=title, file=rel)
        return f"{rel}{(', ' + what) if what else ''}, sha256 {sha[:SHORT]}"


# ------------------------------------------------------------------ балансовые конструкции

def securities_debt(c: Ctx, date: str) -> tuple[float, str, bool]:
    """Долговые бумаги (с заложенными по репо) на дату: (значение, формула, оценка?)."""
    eq_names = ("sec_fvtplm_equities", "sec_fvtplm_funds", "sec_fvoci_equity")
    sec, repo = c.bal("securities", date), c.bal("securities_repo", date)
    if sec is not None and repo is not None and all(c.bal(m, date) is not None for m in eq_names):
        eq = sum(c.bal(m, date) for m in eq_names)
        return sec + repo - eq, (f"бумаги {ru(sec)} + заложенные по репо {ru(repo)} − акции и фонды {ru(eq)} "
                                 f"(«{eq_names[0]}», «{eq_names[1]}», «{eq_names[2]}»)"), False
    lump = c.bal("securities_and_deriv", date)
    if lump is None:
        raise BuildError(f"нет бумаг на {date}")
    deriv = interp(c, "deriv_assets", date)
    eqi = sum(interp(c, m, date) for m in eq_names)
    return lump - deriv - eqi, (f"обобщённая форма: «бумаги и ПФИ» {ru(lump)} − ПФИ {ru(deriv)} − акции и фонды "
                                f"{ru(eqi)} (ПФИ и акции — линейная интерполяция между соседними датами раскрытия; оценка)"), True


def interp(c: Ctx, metric: str, date: str) -> float:
    pts = sorted((dt.date.fromisoformat(p), float(r["value"])) for (m, p), r in c.H.rows.items()
                 if m == metric and len(p) == 10 and p[4] == "-" and r["value"] != "")
    x = dt.date.fromisoformat(date)
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= x <= d1:
            return v0 + (v1 - v0) * (x - d0).days / (d1 - d0).days
    raise BuildError(f"{metric}: {date} вне дат раскрытия (интерполяция без экстраполяции)")


def liquidity(c: Ctx, date: str) -> float:
    return c.bal("cash", date) + c.bal("mandatory_reserves", date) + c.bal("due_from_banks", date)


def iea(c: Ctx, date: str) -> tuple[float, bool, str]:
    sec, how, est = securities_debt(c, date)
    loans = c.bal("loans_ac_total_gross", date) + (c.bal("loans_fvtpl", date) or 0.0)
    liq = liquidity(c, date)
    return loans + sec + liq, est, (f"кредиты АС валовые + по СС {ru(loans)} + долговые бумаги {ru(sec)} + "
                                    f"ликвидность {ru(liq)}; бумаги: {how}")


# ------------------------------------------------------------------ последний объявленный дивиденд

@dataclass(frozen=True)
class Declared:
    """Последний дивиденд, объявленный не позже якоря: суммы МСФО и даты реестра."""
    year: int
    total: float                 # объявлено всего, млрд ₽
    treasury: float              # из них на собственные акции
    treasury_ordinary: float
    treasury_preferred: float
    dps: float
    record_date: str
    pay_date: str                # срок выплаты номинальным держателям: 10-й рабочий день после отсечки
    decided: str | None          # дата решения ГОСА (лист решений)
    payable: bool                # на якоре обязательство ещё не погашено (выплата — после якоря)
    pre_dividend: bool           # норматив якоря — до вычета (вычет — в месяце отсечки, а он после якоря)

    @property
    def net(self) -> float:
        return self.total - self.treasury


def div_when(div: Declared) -> str:
    return d_ru(div.decided) if div.decided else f"(отсечка {d_ru(div.record_date)})"


def declared_dividend(c: Ctx) -> Declared:
    """Год прибыли y — последний, за который отчётность МСФО не позже якоря раскрывает объявленные
    дивиденды (строка div_declared_total FY y) и решение принято не позже даты якоря."""
    rows = c.dividends()
    anchor = dt.date.fromisoformat(c.a.date)
    for y in range(c.a.year, DIV_FIRST_YEAR - 1, -1):
        fy = f"FY{y}"
        r = rows.get(y)
        if r is None or not r["record_date"] or c.bal("div_declared_total", fy) is None:
            continue
        record = dt.date.fromisoformat(r["record_date"])
        decided = r["decided_date"]
        if dt.date.fromisoformat(decided) > anchor:
            continue
        note = c.H.rows.get(("div_declared_on_treasury", fy), c.A.rows.get(("div_declared_on_treasury", fy)))
        if note is None:
            raise BuildError(f"нет дивидендов на собственные акции за {y} г. (div_declared_on_treasury {fy})")
        m = re.search(r"обыкн\. ([\d,]+) \+ прив\. ([\d,]+)", note["note"])
        if not m:
            raise BuildError(f"нет разбивки дивидендов на собственные акции за {y} г.")
        d_o, d_p = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        treasury = c.bal("div_declared_on_treasury", fy)
        if abs(d_o + d_p - treasury) > 0.05:
            raise BuildError(f"разбивка дивидендов на собственные акции за {y} г. не сходится")
        pay = workdays_after(record, PAY_WORKDAYS)
        month_of_record = dt.date(record.year, record.month, 1)
        return Declared(year=y, total=c.bal("div_declared_total", fy), treasury=treasury, treasury_ordinary=d_o,
                        treasury_preferred=d_p, dps=float(r["dps_ordinary"]), record_date=r["record_date"],
                        pay_date=pay.isoformat(), decided=decided, payable=anchor < pay,
                        pre_dividend=anchor < month_of_record)
    raise BuildError("в листах нет объявленного дивиденда не позже якоря")


# ------------------------------------------------------------------ anchor.json

def build_anchor(c: Ctx) -> dict:
    report = "годовая отчётность" if c.a.quarter == 4 else "промежуточная отчётность"
    return {
        "basis": "IFRS",
        "as_of": c.a.date,
        "period": c.a.period,
        "note": (f"якорь — последний отчётный квартал МСФО группы ({report} {c.a.ytd_ru}); "
                 "documents — реестр всех документов, на которые ссылаются узлы фактов (sha256 полностью; "
                 "в src — первые 16 знаков); file — путь в папке передачи (BANK_HANDOFF_DIR)"),
        "documents": c.docs.documents(),
    }


# ------------------------------------------------------------------ balance.json

def build_balance(c: Ctx, div: Declared) -> dict:
    d = c.a.date
    H = c
    mort = c.bal("loans_ac_mortgage_gross", d)
    calib, calib_ref = c.calib()
    sub = r1(calib["books"]["mortgage_sub"]["balance_close"]["v"])
    loans = {
        "commercial": H.bal_n("loans_ac_commercial_gross", d),
        "project": H.bal_n("loans_ac_project_gross", d),
        "mortgage_market": node(r1(mort - sub), calc=(f"loans_ac_gross.mortgage − mortgage_subsidized = {ru(mort)} − {ru(sub)}; "
                                                     "разбиение ипотеки в отчётности не раскрыто")),
        "mortgage_subsidized": node(sub, src=calib_ref,
                                    calc=(f"оценка calib-nii на {d_ru(d)} (накопление поколений: выдачи ≈ прирост портфеля + "
                                          f"погашения; доля льготной в выдачах — ЦБ, «Обзор финансовой стабильности», за квартал "
                                          f"якоря — раскрытие банка; параметры — REPORT-nii этапа 1): {ru(sub / mort * 100)} % ипотеки"
                                          f"{c.calib_sub_range()}; расчётная оценка — в отчётности не раскрыто")),
        "consumer": H.bal_n("loans_ac_consumer_gross", d),
        "cards": H.bal_n("loans_ac_cards_gross", d),
        "auto": H.bal_n("loans_ac_auto_gross", d),
        "mortgage": H.bal_n("loans_ac_mortgage_gross", d),
        "total": H.bal_n("loans_ac_total_gross", d),
    }
    ecl = c.bal("loans_ac_total_ecl", d)
    fvtpl_eq = c.bal("sec_fvtplm_equities", d) + c.bal("sec_fvtplm_funds", d)
    fvtpl_bonds = c.bal("sec_fvtpl_designated", d) + c.bal("sec_fvtpl_mandatory", d) - fvtpl_eq + c.bal("repo_fvtpl_mandatory", d)
    sec = {
        "fvoci_debt": H.bal_n("sec_fvoci_debt", d),
        "fvoci_repo": H.bal_n("repo_fvoci_debt", d),
        "ac": H.bal_n("sec_ac", d),
        "ac_repo": H.bal_n("repo_ac", d),
        "fvtpl_bonds": node(r1(fvtpl_bonds), src="; ".join(H.bal_src(m, d) for m in ("sec_fvtpl_designated", "sec_fvtpl_mandatory", "repo_fvtpl_mandatory")),
                            calc=(f"отнесённые к СС {ru(c.bal('sec_fvtpl_designated', d))} + обязательно по СС "
                                  f"{ru(c.bal('sec_fvtpl_mandatory', d))} − акции {ru(c.bal('sec_fvtplm_equities', d))} − фонды "
                                  f"{ru(c.bal('sec_fvtplm_funds', d))} + заложенные по репо {ru(c.bal('repo_fvtpl_mandatory', d))}")),
        "fvtpl_equity": node(r1(fvtpl_eq), src="; ".join(H.bal_src(m, d) for m in ("sec_fvtplm_equities", "sec_fvtplm_funds")),
                             calc="акции + паи фондов по СС (в книгу securities не входят — в other_assets)"),
        "fvoci_equity": H.bal_n("sec_fvoci_equity", d),
    }
    sec_debt = sum(sec[k]["v"] for k in ("fvoci_debt", "fvoci_repo", "ac", "ac_repo", "fvtpl_bonds"))
    sec["debt_total"] = node(r1(sec_debt), calc="fvoci_debt + fvoci_repo + ac + ac_repo + fvtpl_bonds")
    liq = {
        "cash": H.bal_n("cash", d),
        "cbr": H.bal_n("mandatory_reserves", d, calc="обязательные резервы на счетах в ЦБ (строка баланса)"),
        "due_from_banks": H.bal_n("due_from_banks", d),
        "reverse_repo": na("обратное репо отдельно не раскрыто — внутри средств в банках (due_from_banks)"),
    }
    liq_sum = sum(liq[k]["v"] for k in ("cash", "cbr", "due_from_banks"))
    liq["total"] = node(r1(liq_sum), calc="cash + cbr + due_from_banks")
    total_assets = c.bal("total_assets", d)
    loans_fvtpl = c.bal("loans_fvtpl", d)
    other_assets = total_assets - (loans["total"]["v"] + loans_fvtpl + ecl) - sec_debt - liq_sum
    oa_parts = ["ppe_rou", "intangibles", "other_assets", "deriv_assets", "dta", "assoc_investments"]
    oa_sum = sum(c.bal(m, d) for m in oa_parts) + fvtpl_eq + c.bal("sec_fvoci_equity", d)
    c.check("balance.other_assets = сумма раскрытых строк", abs(other_assets - oa_sum) < 0.05,
            f"остаток {ru(other_assets)} против Σ строк {ru(oa_sum)}")
    funds = {
        "retail_current": H.bal_n("dep_ind_current", d),
        "retail_term": node(r1(c.bal("dep_ind_term", d) + c.bal("dep_ind_repo", d)),
                            src="; ".join(H.bal_src(m, d) for m in ("dep_ind_term", "dep_ind_repo")),
                            calc=f"срочные ФЛ {ru(c.bal('dep_ind_term', d))} + репо ФЛ {ru(c.bal('dep_ind_repo', d))}"),
        "corp_current": node(r1(c.bal("dep_corp_other_current", d) + c.bal("dep_gov_current", d)),
                             src="; ".join(H.bal_src(m, d) for m in ("dep_corp_other_current", "dep_gov_current")),
                             calc=(f"расчётные счета прочих ЮЛ {ru(c.bal('dep_corp_other_current', d))} + госорганизаций "
                                   f"{ru(c.bal('dep_gov_current', d))}")),
        "corp_term": node(r1(c.bal("dep_corp_other_term", d) + c.bal("dep_gov_term", d) + c.bal("dep_corp_other_repo", d) + c.bal("dep_gov_repo", d)),
                          src="; ".join(H.bal_src(m, d) for m in ("dep_corp_other_term", "dep_gov_term", "dep_corp_other_repo", "dep_gov_repo")),
                          calc=(f"срочные прочих ЮЛ {ru(c.bal('dep_corp_other_term', d))} + госорганизаций {ru(c.bal('dep_gov_term', d))} + "
                                f"репо {ru(c.bal('dep_corp_other_repo', d))} + {ru(c.bal('dep_gov_repo', d))}")),
    }
    funds_sum = sum(x["v"] for x in funds.values())
    c.check("funds = средства клиентов отчёта", abs(funds_sum - c.bal("dep_customers_total", d)) < 0.05,
            f"Σ книг {ru(funds_sum)} против {ru(c.bal('dep_customers_total', d))}")
    wh = {
        "banks": H.bal_n("due_to_banks", d),
        "issued_debt": H.bal_n("debt_securities_issued", d),
        "subordinated": H.bal_n("sub_debt", d),
        "repo_cbr": na("репо с ЦБ отдельно не раскрыто — внутри средств банков (banks)"),
    }
    wh_sum = sum(wh[k]["v"] for k in ("banks", "issued_debt", "subordinated"))
    fy = f"FY{div.year}"
    div_src = "; ".join([c.bal_src("div_declared_total", fy), c.bal_src("div_declared_on_treasury", fy)])
    div_how = (f"дивиденды за {div.year} г., объявленные ГОСА {div_when(div)}, {ru(div.total)} − на собственные акции "
               f"{ru(div.treasury)} (обыкн. {ru(div.treasury_ordinary)} + прив. {ru(div.treasury_preferred)})")
    written = c.bal("eqf_dividends_declared__shareholders", c.a.ytd)   # списание из капитала с начала года
    same_year = dt.date.fromisoformat(div.record_date).year == c.a.year
    if div.payable:
        dp = div.net
        dp_node = node(r1(dp), src=div_src,
                       calc=(f"{div_how}; в составе прочих обязательств суммой не раскрыты"
                             + (f"; = списание из капитала {c.a.ytd_ru} ({ru(written)})" if written is not None and same_year else "")
                             + f"; срок выплаты — {d_ru(div.pay_date)}"))
    else:
        dp = 0.0
        dp_node = node(0.0, src=div_src,
                       calc=(f"{div_how} = {ru(div.net)}: срок выплаты {d_ru(div.pay_date)} — до даты якоря; обязательства "
                             f"на {d_ru(d)} нет"))
    if written is not None and same_year:
        c.check(f"объявленные дивиденды за {div.year} г. = списание из капитала {c.a.ytd_ru}", abs(div.net + written) < 0.05,
                f"{ru(div.net)} против {ru(-written)}; к выплате на якоре — {ru(dp)}")
    total_liab = c.bal("total_liab", d)
    other_liab = total_liab - funds_sum - wh_sum - dp
    ol_sum = sum(c.bal(m, d) for m in ("deriv_liab", "insurance_liab", "dtl", "other_liab")) - dp
    c.check("balance.other_liabilities = сумма раскрытых строк", abs(other_liab - ol_sum) < 0.05,
            f"остаток {ru(other_liab)} против Σ строк − дивиденды {ru(ol_sum)}")
    eq_total = c.bal("eq_total", d)
    at1, nci = c.bal("eq_at1", d), c.bal("eq_nci", d)
    equity = {
        "total": H.bal_n("eq_total", d),
        "shareholders": H.bal_n("eq_shareholders", d),
        "at1": H.bal_n("eq_at1", d),
        "nci": H.bal_n("eq_nci", d),
        "bv_common": node(r1(eq_total - at1 - nci), calc=f"equity.total − at1 − nci = {ru(eq_total)} − {ru(at1)} − ({ru(nci)})"),
        "share_capital": H.bal_n("eq_share_capital", d),
        "share_premium": H.bal_n("eq_share_premium", d),
        "treasury_cost": H.bal_n("eq_treasury", d),
        "fvoci_reserve": c.bal_n("orf_fvoci", d, calc="фонд переоценки FVOCI после налога (компонент прочих резервов)"),
        "other_reserves": H.bal_n("eq_other_reserves", d),
        "retained_earnings": H.bal_n("eq_retained", d),
    }
    ye = c.a.prev_year_end
    corp_ye = c.bal("loans_ac_commercial_gross", ye) + c.bal("loans_ac_project_gross", ye) + c.bal("loans_fvtpl", ye)
    ret_ye = sum(c.bal(f"loans_ac_{p}_gross", ye) for p in ("mortgage", "consumer", "cards", "auto"))
    prev = {
        "as_of": ye,
        "loans_corporate": node(r1(corp_ye), src="; ".join(c.bal_src(m, ye) for m in ("loans_ac_commercial_gross", "loans_ac_project_gross", "loans_fvtpl")),
                                calc=(f"коммерческие {ru(c.bal('loans_ac_commercial_gross', ye))} + проектные "
                                      f"{ru(c.bal('loans_ac_project_gross', ye))} + кредиты по СС {ru(c.bal('loans_fvtpl', ye))} — "
                                      "тот же состав, что книга corp_loans")),
        "loans_retail": node(r1(ret_ye), src="; ".join(c.bal_src(f"loans_ac_{p}_gross", ye) for p in ("mortgage", "consumer", "cards", "auto")),
                             calc="ипотека + потребительские + карты + авто (валовые, АС) — книги mortgage + mortgage_sub + retail_loans"),
    }
    out = {
        "basis": "IFRS",
        "as_of": d,
        "unit": "млрд ₽",
        "sign": "остатки — величины (резерв — положительной величиной, в активах вычитается); капитал и фонды — со знаком отчёта",
        "loans_ac_gross": loans,
        "loans_fvtpl": H.bal_n("loans_fvtpl", d),
        "allowance_ac": node(r1(-ecl), src=c.bal_src("loans_ac_total_ecl", d),
                             calc=f"величина резерва под ОКУ по кредитам АС: −({ru(ecl)}); в отчёте — со знаком минус"),
        "securities": sec,
        "liquidity": liq,
        "other_assets": node(r1(other_assets), calc=(
            f"total_assets − (кредиты АС валовые − резерв + кредиты по СС) − долговые бумаги − ликвидность = "
            f"{ru(total_assets)} − {ru(loans['total']['v'] + loans_fvtpl + ecl)} − {ru(sec_debt)} − {ru(liq_sum)}; "
            f"= ОС {ru(c.bal('ppe_rou', d))} + НМА {ru(c.bal('intangibles', d))} + прочие {ru(c.bal('other_assets', d))} + "
            f"ПФИ {ru(c.bal('deriv_assets', d))} + ОНА {ru(c.bal('dta', d))} + ассоциированные {ru(c.bal('assoc_investments', d))} + "
            f"акции и фонды {ru(fvtpl_eq + c.bal('sec_fvoci_equity', d))}")),
        "total_assets": H.bal_n("total_assets", d),
        "funds": funds,
        "wholesale": wh,
        "other_liabilities": node(r1(other_liab), calc=(
            f"total_liabilities − средства клиентов − опт − дивиденды к выплате = {ru(total_liab)} − {ru(funds_sum)} − {ru(wh_sum)} − {ru(dp)}; "
            f"= ПФИ {ru(c.bal('deriv_liab', d))} + страховые {ru(c.bal('insurance_liab', d))} + ОНО {ru(c.bal('dtl', d))} + "
            f"прочие {ru(c.bal('other_liab', d))} − дивиденды {ru(dp)}")),
        "dividends_payable": dp_node,
        "dividends_payable_year": div.year,
        "total_liabilities": H.bal_n("total_liab", d),
        "equity": equity,
        "prev_year_end": prev,
        "history": balance_history(c),
    }
    # тождество якоря: активы по книгам = пассивы + капитал
    assets = loans["total"]["v"] + out["loans_fvtpl"]["v"] - out["allowance_ac"]["v"] + sec_debt + liq_sum + other_assets
    liab = funds_sum + wh_sum + other_liab + dp + eq_total
    c.check("тождество баланса якоря (по книгам)", abs(assets - total_assets) < 0.05 and abs(liab - total_assets) < 0.05,
            f"активы {ru(assets)}, пассивы + капитал {ru(liab)}, отчёт {ru(total_assets)}")
    iea0 = loans["total"]["v"] + out["loans_fvtpl"]["v"] + sec_debt + liq_sum
    out["iea"] = node(r1(iea0), calc="loans_ac_gross.total + loans_fvtpl + securities.debt_total + liquidity.total (процентные активы движка)")
    return out


def balance_history(c: Ctx) -> dict:
    """Концы кварталов: кредиты, капитал, процентные активы — для истории выпуска и мостов."""
    out = {}
    for p in q_range(CAPITAL_HISTORY_FIRST, c.a.period):
        d = q_end(p).isoformat()
        row = {"date": d}
        row["loans_ac_gross"] = c.bal_n("loans_ac_total_gross", d)
        row["allowance_ac"] = node(r1(-c.bal("loans_ac_total_ecl", d)), src=c.bal_src("loans_ac_total_ecl", d), calc="величина резерва (в отчёте со знаком минус)") \
            if c.bal("loans_ac_total_ecl", d) is not None else na("резерв на дату не собран")
        row["loans_fvtpl"] = c.bal_n("loans_fvtpl", d) if c.bal("loans_fvtpl", d) is not None else na("кредиты по СС на дату не собраны")
        sh, at1 = c.bal("eq_shareholders", d), c.bal("eq_at1", d)
        row["equity_shareholders"] = c.bal_n("eq_shareholders", d) if sh is not None else na("капитал на дату не собран")
        if sh is not None and at1 is not None:
            row["bv_common"] = node(r1(sh - at1), calc=f"капитал акционеров {ru(sh)} − AT1 {ru(at1)}")
        else:
            row["bv_common"] = na("капитал или AT1 на дату не собраны")
        row["total_assets"] = c.bal_n("total_assets", d) if c.bal("total_assets", d) is not None else na("итог активов на дату не собран")
        try:
            v, est, how = iea(c, d)
            row["iea"] = node(r1(v), calc=how + ("; оценка" if est else ""))
        except BuildError as e:
            row["iea"] = na(f"состав бумаг на дату не раскрыт ({e})")
        out[p] = row
    return out


# ------------------------------------------------------------------ nii_books.json

def build_nii_books(c: Ctx, bal: dict) -> dict:
    p, o, d = c.a.period, c.a.open_date, c.a.date
    days = q_days(p)
    Q, qq = c.Q, ifrs_q(p)
    calib, cref = c.calib()
    keyref = key_ref(c)
    on, kq = d_ru(o), c.a.k_ru          # «31.03.2026», «2К» — тексты calc

    def g(m: str, date: str) -> float:
        v = c.bal(m, date)
        if v is None:
            raise BuildError(f"нет {m} на {date}")
        return v

    def iq(m: str) -> float:
        return Q.get(m, qq) or 0.0

    def isrc(ms) -> str:
        return "; ".join(sorted({Q.src(m, qq) for m in ms if Q.has(m, qq)}))

    sub_o = r1(calib["books"]["mortgage_sub"]["balance_open"]["v"])
    bal_o = {
        "corp_loans": g("loans_ac_commercial_gross", o) + g("loans_ac_project_gross", o) + g("loans_fvtpl", o),
        "mortgage": g("loans_ac_mortgage_gross", o) - sub_o,
        "mortgage_sub": sub_o,
        "retail_loans": g("loans_ac_consumer_gross", o) + g("loans_ac_cards_gross", o) + g("loans_ac_auto_gross", o),
        "securities": securities_debt(c, o)[0],
        "liquidity": liquidity(c, o),
        "retail_current": g("dep_ind_current", o),
        "retail_term": g("deposits_individuals", o) - g("dep_ind_current", o),
        "corp_funds": g("deposits_corporates", o),
        "wholesale": g("due_to_banks", o) + g("debt_securities_issued", o) + g("sub_debt", o),
    }
    L = bal["loans_ac_gross"]
    bal_c = {
        "corp_loans": L["commercial"]["v"] + L["project"]["v"] + bal["loans_fvtpl"]["v"],
        "mortgage": L["mortgage_market"]["v"],
        "mortgage_sub": L["mortgage_subsidized"]["v"],
        "retail_loans": L["consumer"]["v"] + L["cards"]["v"] + L["auto"]["v"],
        "securities": bal["securities"]["debt_total"]["v"],
        "liquidity": bal["liquidity"]["total"]["v"],
        "retail_current": bal["funds"]["retail_current"]["v"],
        "retail_term": bal["funds"]["retail_term"]["v"],
        "corp_funds": bal["funds"]["corp_current"]["v"] + bal["funds"]["corp_term"]["v"],
        "wholesale": sum(bal["wholesale"][k]["v"] for k in ("banks", "issued_debt", "subordinated")),
    }
    open_how = {
        "corp_loans": f"коммерческие + проектные (АС, валовые) + кредиты по СС на {on}",
        "mortgage": f"ипотека АС на {on} {ru(g('loans_ac_mortgage_gross', o))} − льготная (оценка calib-nii) {ru(sub_o)}",
        "mortgage_sub": f"оценка calib-nii на {on} (накопление поколений); расчётная оценка — в отчётности не раскрыто",
        "retail_loans": f"потребительские + карты + авто (АС, валовые) на {on}",
        "securities": f"долговые бумаги с заложенными по репо на {on}: " + securities_debt(c, o)[1],
        "liquidity": f"денежные средства + обязательные резервы + средства в банках на {on}",
        "retail_current": f"текущие счета ФЛ на {on}",
        "retail_term": f"средства ФЛ − текущие (срочные + репо) на {on}",
        "corp_funds": f"средства ЮЛ на {on}",
        "wholesale": f"средства банков + выпущенные облигации + субординированные на {on}",
    }
    open_src = {
        "corp_loans": ("loans_ac_commercial_gross", "loans_ac_project_gross", "loans_fvtpl"),
        "mortgage": ("loans_ac_mortgage_gross",),
        "retail_loans": ("loans_ac_consumer_gross", "loans_ac_cards_gross", "loans_ac_auto_gross"),
        "securities": ("securities", "securities_repo", "sec_fvtplm_equities", "sec_fvtplm_funds", "sec_fvoci_equity"),
        "liquidity": ("cash", "mandatory_reserves", "due_from_banks"),
        "retail_current": ("dep_ind_current",),
        "retail_term": ("deposits_individuals", "dep_ind_current"),
        "corp_funds": ("deposits_corporates",),
        "wholesale": ("due_to_banks", "debt_securities_issued", "sub_debt"),
    }
    # процентные строки квартала якоря по книгам (как calib-nii nii_lib._flows_from)
    ii_loans = iq("ii_ac_loans") + iq("ii_oth_loans_fvtpl")
    flows = {
        "securities": (iq("ii_ac_debt_securities") + iq("ii_fvoci_debt_securities") + iq("ii_oth_securities_fvtpl"),
                       ("ii_ac_debt_securities", "ii_fvoci_debt_securities", "ii_oth_securities_fvtpl"),
                       "бумаги АС + FVOCI + по СС"),
        "liquidity": (iq("ii_ac_banks") + iq("ii_ac_cash") + iq("ii_ac_other") + iq("ii_oth_banks_fvtpl"),
                      ("ii_ac_banks", "ii_ac_cash", "ii_ac_other", "ii_oth_banks_fvtpl"),
                      "средства в банках (АС и по СС) + прочие процентные доходы АС"),
        "retail_current": (-iq("ie_ac_current_individuals"), ("ie_ac_current_individuals",), "текущие счета ФЛ"),
        "retail_term": (-iq("ie_ac_term_individuals"), ("ie_ac_term_individuals",), "срочные ФЛ"),
        "corp_funds": (-(iq("ie_ac_term_corporates") + iq("ie_ac_current_corporates")),
                       ("ie_ac_term_corporates", "ie_ac_current_corporates"), "срочные + расчётные ЮЛ"),
        "wholesale": (-(iq("ie_ac_banks") + iq("ie_ac_debt_issued") + iq("ie_ac_sub_debt") + iq("ie_oth_banks_fvtpl")),
                      ("ie_ac_banks", "ie_ac_debt_issued", "ie_ac_sub_debt", "ie_oth_banks_fvtpl"),
                      "средства банков (АС и по СС) + выпущенные облигации + субординированные"),
    }
    other_ii = iq("ii_oth_derivatives")
    other_ie = -(iq("ie_ac_lease") + iq("ie_oth_derivatives") + iq("ie_oth_term_fvtpl") + iq("ie_oth_short_positions") + iq("ie_oth_other"))
    dia = -iq("ie_deposit_insurance")
    nii = Q.need("nii", qq)
    key_avg = key_average(c, p)
    sub_rate = calib["books"]["mortgage_sub"]["rate_anchor"]["v"]      # ключевая + компенсация — параметр калибровки
    c.check("ставка льготной ипотеки calib-nii = средняя ключевая квартала + компенсация 1–4 п.п.",
            0.01 <= sub_rate - key_avg <= 0.04,
            f"{ru(sub_rate * 100, 3)} % = ключевая {ru(key_avg * 100, 3)} % + {ru((sub_rate - key_avg) * 100, 2)} п.п.")
    rates_calib = {
        "corp_loans": (calib["books"]["corp_loans"]["rate_anchor"]["v"], calib["books"]["corp_loans"]["rate_anchor"]["calc"]),
        "mortgage": (calib["books"]["retail_loans"]["rate_anchor"]["v"],
                     "одна ставка с retail_loans (история не различает рыночную ипотеку и прочую розницу): " + calib["books"]["retail_loans"]["rate_anchor"]["calc"]),
        "retail_loans": (calib["books"]["retail_loans"]["rate_anchor"]["v"], calib["books"]["retail_loans"]["rate_anchor"]["calc"]),
        "mortgage_sub": (r6(sub_rate), (f"calib-nii: key_avg_anchor_q {ru(key_avg * 100, 3)} % + компенсация "
                                        f"{ru((sub_rate - key_avg) * 100, 1)} п.п. ({SUB_COMP_SOURCE})")),
    }
    books = {}
    ii_books = 0.0
    for b in ("corp_loans", "mortgage", "mortgage_sub", "retail_loans", "securities", "liquidity",
              "retail_current", "retail_term", "corp_funds", "wholesale"):
        bo, bc = r1(bal_o[b]), r1(bal_c[b])
        avg = (bo + bc) / 2
        if b == "mortgage_sub":
            n_open = node(bo, src=cref, calc=open_how[b])
        elif b == "mortgage":
            n_open = node(bo, src="; ".join(c.bal_src(m, o) for m in open_src[b]) + "; " + cref, calc=open_how[b])
        else:
            n_open = node(bo, src="; ".join(c.bal_src(m, o) for m in open_src[b]), calc=open_how[b])
        rec = {"balance_open": n_open,
               "balance_close": node(bc, calc=f"balance.json на {d} (состав книги — MODEL §4.1)"),
               "balance_avg": node(r6(avg), calc="(balance_open + balance_close) / 2")}
        if b in rates_calib:
            rate, how = rates_calib[b]
            interest = rate * avg * days / 365
            ii_books += interest
            rec["interest_q"] = node(r6(interest), calc=(f"rate_anchor × balance_avg × {days}/365; процентный доход по кредитам {kq} "
                                                         f"(АС + по СС = {ru(ii_loans)}) по книгам не раскрыт — разбиение calib-nii"))
            rec["rate_anchor"] = node(r6(rate), src=cref if b != "mortgage_sub" else f"{cref}; {keyref}", calc=how)
        else:
            val, ms, what = flows[b]
            rec["interest_q"] = node(r1(val), src=isrc(ms), calc=f"{what}, {kq} (процентные доходы/расходы по инструментам; расходы — величиной)")
            rec["rate_anchor"] = node(r6(val / avg * 365 / days), calc=f"interest_q / balance_avg × 365/{days} (act/365)")
        books[b] = rec
    c.check(f"книги кредитов: Σ interest_q = процентный доход по кредитам {kq}", abs(ii_books - ii_loans) < 0.5,
            f"{ru(ii_books, 2)} против {ru(ii_loans)} (АС {ru(iq('ii_ac_loans'))} + по СС {ru(iq('ii_oth_loans_fvtpl'))})")
    nii_books = (ii_books + flows["securities"][0] + flows["liquidity"][0] + other_ii
                 - flows["retail_current"][0] - flows["retail_term"][0] - flows["corp_funds"][0] - flows["wholesale"][0]
                 - other_ie - dia)
    c.check(f"Σ книг ЧПД {kq} + прочие + АСВ = ЧПД отчёта", abs(nii_books - nii) < 0.5, f"{ru(nii_books, 2)} против {ru(nii)}")
    carry = p >= RATES_CARRY_FROM
    if carry:
        interest = {b: books[b]["interest_q"]["v"] for b in BOOKS}
        residual, scale = carry_scale(interest, nii, dia)
        income = sum(interest[b] for b in ASSET_BOOKS)
        for b in ASSET_BOOKS:
            r = books[b]
            was = r["rate_anchor"]
            r["rate_anchor"] = node(r6(r["interest_q"]["v"] * scale / r["balance_avg"]["v"] * 365 / days), src=was.get("src"),
                                    calc=(f"{was['calc']}; × (1 + остаток ЧПД {ru(residual)} / Σ процентов активных книг "
                                          f"{ru(income)}) — ставка книги активов несёт долю процентного дохода вне книг "
                                          f"(MODEL §15.2 п. 4)"))
    iea_avg = sum(books[b]["balance_avg"]["v"] for b in ("corp_loans", "mortgage", "mortgage_sub", "retail_loans", "securities", "liquidity"))
    retail_avg = books["retail_current"]["balance_avg"]["v"] + books["retail_term"]["balance_avg"]["v"]
    return {
        "basis": "IFRS",
        "as_of": d,
        "period": p,
        "days": node(days, calc=f"дней в квартале {p} (act/365)"),
        "unit": "остатки и проценты — млрд ₽; ставки — доли, годовые, act/365",
        "rates_carry_residual": carry,
        "books": books,
        "interest_loans_q": node(r1(ii_loans), src=isrc(("ii_ac_loans", "ii_oth_loans_fvtpl")), calc=f"процентный доход по кредитам АС + по СС, {kq}"),
        "other_interest_net_q": node(r1(other_ii - other_ie), src=isrc(("ii_oth_derivatives", "ie_ac_lease", "ie_oth_derivatives", "ie_oth_term_fvtpl", "ie_oth_short_positions")),
                                     calc="ПФИ (доход) − аренда − ПФИ (расход) − срочные по СС − короткие позиции: вне книг"),
        "dia_q": node(r1(dia), src=Q.src("ie_deposit_insurance", qq), calc=f"взносы в АСВ, {kq} (величиной; в отчёте со знаком минус)"),
        "dia_rate": node(r6(dia / retail_avg * 365 / days), calc=f"dia_q / средние средства ФЛ × 365/{days}"),
        "nii_q": Q.n("nii", qq),
        "iea_avg": node(r6(iea_avg), calc="Σ balance_avg активных книг (кредиты, бумаги, ликвидность)"),
        "nim_eng_q": node(r6(nii * 365 / days / iea_avg), calc=f"nii_q × 365/{days} / iea_avg (ЧПМ базиса движка)"),
        "current_share_anchor": node(r6(bal["funds"]["retail_current"]["v"] / (bal["funds"]["retail_current"]["v"] + bal["funds"]["retail_term"]["v"])),
                                     calc="balance.funds.retail_current / (retail_current + retail_term) на якоре"),
        "key_avg_anchor_q": node(r6(key_avg), src=keyref, calc=f"средняя ключевая ставка {p} по календарным дням"),
    }


def key_average(c: Ctx, p: str) -> float:
    rows = c.csv_rows("../research/facts/cbr_key_rate_changes.csv")
    ch = sorted((dt.date.fromisoformat(r["effective_date"]), float(r["key_rate_pct"])) for r in rows)
    s, e = q_end(q_prev(p)) + dt.timedelta(days=1), q_end(p)
    if not ch or ch[0][0] > s:
        raise BuildError(f"ключевая ставка: ряд начинается позже начала {p}")
    tot, n = 0.0, 0
    day = s
    while day <= e:
        rate = [v for d0, v in ch if d0 <= day][-1]
        tot += rate
        n += 1
        day += dt.timedelta(days=1)
    return tot / n / 100


# ------------------------------------------------------------------ pnl_quarterly.json

MISC_PARTS = ("ins_finance", "gl_fvtpl_nonderiv", "gl_deriv_fx", "gl_fvoci", "gl_initial_recognition",
              "impairment", "other_provisions", "assoc_share", "office_revaluation", "other_op_net")

def key_ref(c: Ctx) -> str:
    return c.file_ref("research/facts/cbr_key_rate_changes.csv", "ключевая ставка ЦБ по датам изменений",
                      key="cbr_key_rate_changes", url="https://www.cbr.ru/hd_base/KeyRate/",
                      title=f"Банк России: ключевая ставка по датам изменений (выгрузка {SNAPSHOT_RU})")


def misc_shares(quarters: dict, last: str) -> dict[str, list[float]]:
    """Доли кварталов в годовой сумме «прочего» по полным годам — только для сверки сборщика: ключ
    other.misc_quarter_shares считает и выбирает книга (evidence/bridges, MODEL §4.7)."""
    out = {}
    for y in full_years(PNL_FIRST, last):
        xs = [quarters[f"{y}Q{k}"]["misc_net"]["v"] for k in range(1, 5)]
        if None not in xs:
            out[str(y)] = [x / sum(xs) for x in xs]
    return out


def at1_coupon_quarter(c: Ctx, p: str) -> dict:
    """Купон AT1 квартала величиной (как в dividends и capital; в отчёте о капитале — со знаком минус).

    Купон платится раз в год, в декабре: в отчётах о капитале за 3, 6 и 9 месяцев строки купона
    нет, в годовом — есть. Четвёртый квартал = год − 9 месяцев = годовой купон; прочие — 0."""
    Q, qq = c.Q, ifrs_q(p)
    y, k = q_parse(p)
    if Q.has("eqf_at1_coupon__retained", qq):
        return node(abs(r6(Q.need("eqf_at1_coupon__retained", qq))), Q.src("eqf_at1_coupon__retained", qq),
                    calc="купон AT1 квартала величиной (в отчёте о капитале — со знаком минус)")
    if k != 4:
        return node(0.0, calc="купон AT1 выплачивается в декабре (К4); в квартале — 0 (MODEL §4.9)")
    fy = f"FY{y}"
    return node(abs(r1(c.H.need("eqf_at1_coupon__retained", fy))), c.H.src("eqf_at1_coupon__retained", fy),
                calc=(f"{fy} − 9M{y}: процентные платежи по бессрочному субординированному займу за год "
                      f"({ru(c.H.need('eqf_at1_coupon__retained', fy))}); в отчёте о капитале за 9 месяцев строки нет (0); величиной"))


def build_pnl(c: Ctx) -> dict:
    Q = c.Q
    keyref = key_ref(c)
    quarters = {}
    for p in q_range(PNL_FIRST, c.a.period):
        qq = ifrs_q(p)

        def n(m, calc=None):
            return Q.n(m, qq, calc=calc) if Q.has(m, qq) else na(f"строка «{m}» за {p} не раскрыта")

        def sm(ms, what) -> dict:
            if not all(Q.has(m, qq) for m in ms):
                return na(f"{what}: в обобщённой форме {p} строки свёрнуты в «прочие операционные» (other_op_net)")
            v = sum(Q.need(m, qq) for m in ms)
            notes = {Q.calc_note(m, qq) for m in ms} - {None}
            calc = " + ".join(ms) + (f"; кварталы — {'; '.join(sorted(notes))}" if notes else "")
            return node(r1(v), "; ".join(sorted({Q.src(m, qq) for m in ms})), calc)

        row = {
            "nii": n("nii"),
            "fees_net": sm(("fee_income", "fee_expense"), "ЧКД"),
            "fee_income": n("fee_income"),
            "fee_expense": n("fee_expense"),
            "llp_debt_fa": n("llp_debt_fa", calc=Q.calc_note("llp_debt_fa", qq) if Q.has("llp_debt_fa", qq) else None),
            "fv_loans_credit": na("кредитная переоценка кредитов по СС отдельной строкой ОПУ не раскрывается (внутри результата по СС)"),
            "insurance_net": sm(("ins_revenue", "ins_service_expense"), "страхование (выручка + расходы на услуги)"),
            "insurance_finance": n("ins_finance"),
            "noncore_net": sm(("noncore_revenue", "noncore_cost"), "непрофильная деятельность"),
            "opex": n("opex"),
            "pbt": n("pbt"),
            "tax": n("tax"),
            "ni": n("np"),
            "ni_shareholders": n("np_shareholders"),
            "ni_nci": n("np_nci"),
            "dia": n("ie_deposit_insurance"),
        }
        parts = ("nii", "llp_debt_fa", "fees_net", "insurance_net", "noncore_net", "opex")
        if all(row[k]["v"] is not None for k in parts):
            misc = row["pbt"]["v"] - sum(row[k]["v"] for k in parts)
            row["misc_net"] = node(r1(misc), calc=("pbt − (nii + llp_debt_fa + fees_net + insurance_net + noncore_net + opex): "
                                                   "финансовые расходы страхования, торговые и по СС, валютные, прочие резервы, "
                                                   "обесценение, переоценка офисной недвижимости, ассоциированные (MODEL §4.7)"))
        else:
            row["misc_net"] = na(f"в обобщённой форме {p} страхование и непрофильная деятельность свёрнуты в прочие — остаток не определяется")
        row["fvtpl_bond_reval"] = na("переоценка облигаций по СС отдельно не раскрыта: внутри доходов по СС (gl_fvtpl_nonderiv), входит в misc_net")
        row["oci_fvoci"] = sm(("oci_fvoci_fv_change_net", "oci_fvoci_recycled_net"), "OCI по FVOCI (переоценка + перенос в ОПУ)")
        row["oci_total"] = n("oci_total")
        row["ci_shareholders"] = n("ci_shareholders")
        row["other_equity_movements"] = sm(("eqf_other_movements__shareholders", "eqf_ltip__shareholders", "eqf_treasury_ops__shareholders"),
                                           "прочие движения капитала (прочие + LTIP + операции с собственными акциями)")
        row["at1_coupon"] = at1_coupon_quarter(c, p)
        row["key_avg"] = node(r6(key_average(c, p)), src=keyref,
                              calc=f"средняя ключевая ставка {p} по календарным дням (доля; флаг ni_jump, MODEL §14.3)")
        # тождество PBT (MODEL §4.7): остаток misc_net = сумме раскрытых прочих строк ОПУ
        # (К4 — разность «год − 9М», а доля ассоциированных за 9М не раскрыта: К4 сверяется годом ниже)
        if row["misc_net"]["v"] is not None and q_parse(p)[1] != 4:
            s = sum(Q.get(m, qq) or 0.0 for m in MISC_PARTS)
            c.check(f"PBT {p}: misc_net = Σ прочих строк ОПУ", abs(s - row["misc_net"]["v"]) < 0.35,
                    f"остаток {ru(row['misc_net']['v'])} против Σ строк {ru(s)}")
        quarters[p] = row
    # годом: остаток по строкам годового отчёта = Σ прочих строк года = Σ misc_net кварталов
    years = full_years(PNL_FIRST, c.a.period)
    for y in years:
        if any(quarters[f"{y}Q{k}"]["misc_net"]["v"] is None for k in range(1, 5)):
            continue
        fy = f"FY{y}"
        H = c.H
        core = (H.need("nii", fy) + H.need("llp_debt_fa", fy) + H.need("fee_income", fy) + H.need("fee_expense", fy)
                + H.need("ins_revenue", fy) + H.need("ins_service_expense", fy) + H.need("noncore_revenue", fy)
                + H.need("noncore_cost", fy) + H.need("opex", fy))
        resid = H.need("pbt", fy) - core
        parts = sum(H.get(m, fy) or 0.0 for m in MISC_PARTS)
        sq = sum(quarters[f"{y}Q{k}"]["misc_net"]["v"] for k in range(1, 5))
        c.check(f"PBT {fy}: остаток = Σ прочих строк = Σ misc_net кварталов", abs(resid - parts) < 0.35 and abs(resid - sq) < 0.5,
                f"остаток {ru(resid)}, Σ строк {ru(parts)}, Σ кварталов {ru(sq)}")
    # сверка кварталы → год по ЧП акционерам
    for y in years:
        s = sum(quarters[f"{y}Q{k}"]["ni_shareholders"]["v"] for k in range(1, 5))
        fy = c.H.get("np_shareholders", f"FY{y}")
        c.check(f"Σ ЧП акционерам кварталов {y} = год", fy is not None and abs(s - fy) < 0.15,
                f"{ru(s)} против {'нет года' if fy is None else ru(fy)}")
    shares = misc_shares(quarters, c.a.period)
    c.check("«прочее»: основная масса — в 4К (sanity-val S11; доли — ключ книги)", bool(shares) and all(v[3] > 0.5 for v in shares.values()),
            "; ".join(f"{y}: " + ", ".join(ru(x, 3) for x in v) for y, v in shares.items()))
    return {
        "basis": "IFRS",
        "as_of": c.a.date,
        "unit": "млрд ₽ за квартал; key_avg — доля единицы",
        "sign": "как в отчётности: расходы, резервы, налог, взносы АСВ — со знаком минус; тождество PBT = nii + llp_debt_fa + fees_net + insurance_net + noncore_net + opex + misc_net",
        "quarters": quarters,
    }


# ------------------------------------------------------------------ capital.json

def build_capital(c: Ctx, bal: dict, div: Declared) -> dict:
    d = c.a.date
    H = c
    f135 = [r for r in c.csv_rows("ras/cbr_f135.csv")]
    f123 = [r for r in c.csv_rows("ras/cbr_f123.csv")]
    idx = {(r["form"], r["name"]): r for r in c.csv_rows("ras/raw/INDEX.csv")}
    form_date = c.a.form_date

    def form_ref(form: str, date: str) -> str:
        r = idx.get((form, date))
        if r is None:
            raise BuildError(f"нет ответа формы {form} на {date}")
        rel = f"stage1/ras/raw/{form}/{date}.xml"
        method = r["method"]
        return c.file_ref(rel, f"форма 0409{form} на {date}", key=f"cbr_f{form}_{date}",
                          url=f"https://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx ({method})",
                          title=f"Банк России, CreditOrgInfo: форма 0409{form} банка на {date} (ответ SOAP)")

    def f135v(norm: str, date: str) -> float | None:
        for r in f135:
            if r["form_date"] == date and r["norm"] == norm and r["value_pct"]:
                return r6(float(r["value_pct"]) / 100)
        return None

    def f123v(code: str, date: str) -> float:
        for r in f123:
            if r["form_date"] == date and r["code"] == code and r["value_thousand_rub"]:
                return float(r["value_thousand_rub"]) / 1e6
        raise BuildError(f"форма 123: нет кода {code} на {date}")

    coupon = c.bal("at1_coupon_paid", c.a.fy_last)
    if coupon is None:
        raise BuildError(f"нет купона AT1 за {c.a.fy_last} (at1_coupon_paid)")
    zc = [r for r in c.csv_rows("calib/capital/data/zcyc_quarter_ends.csv") if r["quarter_end"] == d]
    if not zc:
        raise BuildError(f"нет кривой zcyc на {d} (stage1/calib/capital/data/zcyc_quarter_ends.csv)")
    zsha = zc[0]["sha256"]
    c.docs.add("zcyc", zsha, key=f"moex_zcyc_{zc[0]['tradedate']}", url=zc[0]["url"],
               title=f"MOEX ISS zcyc: бескупонная кривая ОФЗ на {zc[0]['tradedate']} {zc[0]['tradetime']} (ответ JSON не сохранён; значения — лист stage1/calib/capital/data/zcyc_quarter_ends.csv)",
               file=None)
    zsrc = f"MOEX ISS zcyc {zc[0]['tradedate']} {zc[0]['tradetime']}, yearyields, sha256 {zsha[:SHORT]}"
    curve = {"basis": "market", "as_of": zc[0]["tradedate"],
             "convention": "бескупонная доходность ОФЗ (КБД MOEX), годовое начисление, доли"}
    for r in zc:
        t = float(r["period_y"])
        if t in (1.0, 3.0, 5.0, 10.0):
            curve[str(int(t))] = node(r6(float(r["yield_pct"]) / 100), zsrc)
    mg = c.M
    rec = dt.date.fromisoformat(div.record_date)
    cut = f"вычет в месяце отсечки, {MONTHS_NOM[rec.month - 1]} {rec.year}"
    out = {
        "basis": "mixed",
        "as_of": d,
        "note": "у каждого блока свой basis: МСФО группы, Базель группы (примечание МСФО), регуляторный норматив группы, РСБУ банка (формы ЦБ), рынок",
        "at1": {
            "basis": "IFRS",
            "amount": H.bal_n("eq_at1", d),
            "coupon_annual": node(r1(-coupon), src=c.bal_src("at1_coupon_paid", c.a.fy_last),
                                  calc="годовой купон AT1 (150 млрд, Минфин) величиной; в отчёте — выплата со знаком минус"),
            "coupon_quarter": node(4, src=c.bal_src("at1_coupon_paid", c.a.fy_last), calc="купон выплачивается в декабре — квартал 4"),
        },
        "t2_recognized": node(c.bal("basel_t2", d), src=c.bal_src("basel_t2", d),
                              calc="прокси: капитал 2-го уровня группы по Базелю; регуляторный T2 для Н20.0 не раскрыт",
                              basis="Basel (группа)"),
        "basel": {
            "basis": "Basel (группа; примечание о капитале МСФО)",
            "as_of": d,
            "cet1_ratio": c.bal_n("basel_cet1_ratio_pct", d, scale=0.01),
            "cet1": c.bal_n("basel_cet1", d),
            "t1_ratio": c.bal_n("basel_t1_ratio_pct", d, scale=0.01),
            "t1": c.bal_n("basel_t1", d),
            "t2": c.bal_n("basel_t2", d),
            "total_ratio": c.bal_n("basel_total_ratio_pct", d, scale=0.01),
            "total_capital": c.bal_n("basel_total_capital", d),
            "rwa": c.bal_n("basel_rwa", d),
        },
        "n20_0": {
            "basis": "регуляторный норматив группы (Н20.0; оперативные данные презентации — источник mgmt)",
            "value": mg.n("n20_0", c.a.period, scale=0.01),
            "as_of": d,
            "pre_dividend": div.pre_dividend,
            "pre_dividend_note": (f"Н20.0 на {d_ru(d)} — {'до вычета' if div.pre_dividend else 'после вычета'} дивиденда "
                                  f"за {div.year} г. ({cut})"),
            "min_with_buffers": c.bal_n("n20_0_min_with_buffers_pct", d, scale=0.01),
        },
        "n1_1_bank": {
            "basis": "RAS",
            "value": node(f135v("Н1.1", form_date), src=form_ref("135", form_date), calc="Н1.1 банка, % / 100; прокси Н20.1 группы (MODEL §3.3)"),
            "as_of": form_date,
            "ifrs_note_value": c.bal_n("bank_n1_1_pct", d, scale=0.01),
        },
        "n1_0_bank": node(f135v("Н1.0", form_date), src=form_ref("135", form_date), calc="Н1.0 банка, % / 100",
                          basis="RAS", as_of=form_date),
        "n1_2_bank": node(f135v("Н1.2", form_date), src=form_ref("135", form_date), calc="Н1.2 банка, % / 100",
                          basis="RAS", as_of=form_date),
        "bank_base_capital": node(r6(f123v("102", form_date)), src=form_ref("123", form_date),
                                  calc="форма 0409123, код 102 «Базовый капитал, итого», тыс. ₽ / 10⁶ (для сверки Ded11_0)",
                                  basis="RAS", as_of=form_date),
        "bank_own_funds": node(r6(f123v("000", form_date)), src=form_ref("123", form_date),
                               calc="форма 0409123, код 000 «Собственные средства (капитал), итого», тыс. ₽ / 10⁶",
                               basis="RAS", as_of=form_date),
        "fvoci_reserve": node(bal["equity"]["fvoci_reserve"]["v"], src=bal["equity"]["fvoci_reserve"]["src"],
                              calc="после налога; = balance.equity.fvoci_reserve", basis="IFRS"),
        "ofz_curve_anchor": curve,
        "history": capital_history(c, f135v, form_ref),
    }
    bv = bal["equity"]["bv_common"]["v"]
    ded20 = bv - c.bal("basel_cet1", d)
    c.check("Ded20_0 = BV − CET1 по Базелю: вычеты положительны и меньше капитала (ключ книги capital.n20.deductions_anchor)",
            0 < ded20 < bv, ru(ded20))
    return out


def capital_history(c: Ctx, f135v, form_ref) -> list[dict]:
    rows = []
    for p in q_range(CAPITAL_HISTORY_FIRST, c.a.period):
        d = q_end(p).isoformat()
        fd = (q_end(p) + dt.timedelta(days=1)).isoformat()
        r = {"period": p, "date": d}
        r["n20_0"] = c.M.n("n20_0", p, scale=0.01) if c.M.has("n20_0", p) else na("Н20.0 за квартал не раскрыт")
        for key, m in (("cet1_ratio", "basel_cet1_ratio_pct"), ("t1_ratio", "basel_t1_ratio_pct"), ("total_ratio", "basel_total_ratio_pct")):
            r[key] = c.bal_n(m, d, scale=0.01) if c.bal(m, d) is not None else na(f"{m} на {d} не раскрыт")
        for key, m in (("cet1", "basel_cet1"), ("rwa", "basel_rwa")):
            r[key] = c.bal_n(m, d) if c.bal(m, d) is not None else na(f"{m} на {d} раскрыт только коэффициентом")
        v = f135v("Н1.1", fd)
        r["n1_1_bank"] = node(v, src=form_ref("135", fd), calc=f"форма 0409135 на {fd}, % / 100") if v is not None \
            else na(f"форма 0409135 на {fd} не опубликована ({F135_GAP_NOTE})")
        for k, basis in HISTORY_BASIS.items():
            r[k]["basis"] = basis
        rows.append(r)
    return rows


HISTORY_BASIS = {"n20_0": "регуляторный норматив группы (презентация)", "cet1_ratio": "Basel (группа)",
                 "t1_ratio": "Basel (группа)", "total_ratio": "Basel (группа)", "cet1": "Basel (группа)",
                 "rwa": "Basel (группа)", "n1_1_bank": "RAS"}


# ------------------------------------------------------------------ shares.json

def build_shares(c: Ctx, div: Declared) -> dict:
    iss = {}
    for t in ("SBER", "SBERP"):
        rel = f"stage1/market/shares_dividends/iss_description_{t}.json"
        j = json.loads((c.root / rel).read_text(encoding="utf-8"))
        desc = {row[0]: row[2] for row in j["description"]["data"]} if "description" in j else {}
        iss[t] = (desc, c.file_ref(rel, "ISSUESIZE, FACEVALUE", key=f"iss_description_{t}",
                                   url=f"https://iss.moex.com/iss/securities/{t}.json",
                                   title=f"MOEX ISS: описание бумаги {t} (снимок {SNAPSHOT_RU})"))
    ords = float(iss["SBER"][0]["ISSUESIZE"]) / 1e6
    prefs = float(iss["SBERP"][0]["ISSUESIZE"]) / 1e6
    ye, fy = c.a.fy_last_date, c.a.fy_last_year
    ords_ifrs = c.bal("shares_ordinary_mn", ye)
    c.check(f"размещённые акции ISS = МСФО {fy} (примечание об уставном капитале)", abs(ords - ords_ifrs) < 1
            and abs(prefs - c.bal("shares_preferred_mn", ye)) < 1, f"{ords} / {prefs}")
    # собственные акции на якорь не раскрыты: число — из дивиденда последнего решения на собственные
    # акции (разбивка обыкн. / прив. — в тексте примечания) и DPS этого решения
    d_o, d_p, dps = div.treasury_ordinary, div.treasury_preferred, div.dps
    tr_o, tr_p = r1(d_o / dps * 1000), r1(d_p / dps * 1000)
    tsrc = c.bal_src("div_declared_on_treasury", f"FY{div.year}")
    return {
        "basis": "IFRS",
        "as_of": c.a.date,
        "unit": "млн шт.; номинал — ₽",
        "issued_ordinary": node(r6(ords), iss["SBER"][1],
                                calc=("ISSUESIZE / 10⁶; = МСФО " + str(fy) + ", примечание об уставном капитале ("
                                      + f"{ords_ifrs:,.0f}".replace(",", " ") + " млн)")),
        "issued_preferred": node(r6(prefs), iss["SBERP"][1], calc="ISSUESIZE / 10⁶"),
        "issued_total": node(r6(ords + prefs), calc="issued_ordinary + issued_preferred (N_iss — делитель DPS, политика п. 1.6)"),
        "treasury_ordinary": node(tr_o, src=tsrc, calc=(f"дивиденд за {div.year} на собственные обыкновенные {ru(d_o)} млрд / DPS {ru(dps, 2)} ₽ × 1000 "
                                                        f"(на отсечку {d_ru(div.record_date)}; на {d_ru(c.a.date)} число не раскрыто; точность ±1–2 млн)")),
        "treasury_preferred": node(tr_p, src=tsrc, calc=f"дивиденд на собственные привилегированные {ru(d_p)} млрд / {ru(dps, 2)} ₽ × 1000"),
        "treasury_prev_year_end": {
            "as_of": ye,
            "ordinary": c.bal_n("treasury_ordinary_mn", ye),
            "preferred": c.bal_n("treasury_preferred_mn", ye),
        },
        "outstanding_ordinary": node(r6(ords - tr_o), calc="issued_ordinary − treasury_ordinary"),
        "outstanding_preferred": node(r6(prefs - tr_p), calc="issued_preferred − treasury_preferred"),
        "outstanding_total": node(r6(ords + prefs - tr_o - tr_p), calc="outstanding_ordinary + outstanding_preferred (N_out)"),
        "par_value": node(float(iss["SBER"][0]["FACEVALUE"]), iss["SBER"][1], calc="номинал обеих категорий, ₽"),
        "by_ticker": {"SBER": {"class": "ordinary"}, "SBERP": {"class": "preferred"}},
    }


# ------------------------------------------------------------------ dividends.json

def agm_page(c: Ctx, year: int) -> tuple[str, str, str, str]:
    """Копия новости о решении ГОСА: (дата решения, время, заголовок, текст статьи)."""
    rel = AGM_NEWS[year][0]
    path = c.root / rel
    if not path.exists():
        raise BuildError(f"нет копии новости {rel}")
    html = path.read_text(encoding="utf-8", errors="replace")
    art = re.search(r"<article.*?</article>", html, re.S)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", art.group(0) if art else html))
    when = re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', html)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not when or not title:
        raise BuildError(f"{rel}: нет даты публикации или заголовка")
    return when.group(1), when.group(2), title.group(1).strip(), text


def agm_news(c: Ctx, year: int, r: dict) -> str:
    """Источник «news: …» о решении ГОСА. Есть копия страницы в папке передачи (AGM_NEWS) — дата решения, дивиденд
    и дата реестра сверяются с её текстом, а заголовок и адрес — с листом решений; копии нет — новость названа
    так, как записана в листе (издание, дата публикации, заголовок, адрес), с тем, что она подтверждает."""
    dps = f"{Decimal(r['dps_ordinary']):.2f}".replace(".", ",")
    rd = dt.date.fromisoformat(r["record_date"])
    day, _, hhmm = r["news_published"].partition(" ")
    when = d_ru(day) + (f" {hhmm} МСК" if hhmm else "")
    says = f"решение ГОСА: {dps} ₽" + (f", реестр {rd.strftime('%d.%m.%Y')}" if "отсечка" in r["news_confirms"] else "")
    if year not in AGM_NEWS:
        return f"news: {r['news_outlet']} {when}, «{r['news_title']}» — {says} ({r['news_url']}; {c.dividends_ref()})"
    rel, url, key, outlet = AGM_NEWS[year]
    decided, hhmm, title, text = agm_page(c, year)
    need = (dps, f"{rd.day} {MONTHS_GEN[rd.month - 1]} {rd.year}")
    if not all(x in text for x in need):
        raise BuildError(f"{rel}: в тексте нет DPS или даты реестра ({need})")
    if (decided, f"{decided} {hhmm}", title, url, outlet) != (r["decided_date"], r["news_published"], r["news_title"],
                                                              r["news_url"], r["news_outlet"]):
        raise BuildError(f"{DIVIDENDS_SHEET}: строка {year} расходится с копией новости {rel} (дата, заголовок, адрес)")
    ref = c.file_ref(rel, "", key=key, url=url, title=f"{outlet} {d_ru(decided)}: «{title}»")
    return (f"news: {outlet} {d_ru(decided)} {hhmm} МСК, «{title}» — решение ГОСА: "
            f"{need[0]} ₽, реестр {rd.strftime('%d.%m.%Y')} ({url}; {ref})")


def dividend_dates(c: Ctx, year: int, r: dict) -> dict:
    """Даты дивиденда по правилу от даты отсечки листа решений: режим расчётов (T+2 до 28.07.2023, затем T+1),
    экс-дата (при T+1 — отсечка, при T+2 — торговый день перед ней), последний день покупки (торговый день перед
    экс-датой) и срок выплаты номинальным держателям (10-й рабочий день после отсечки). Размер, отсечка,
    экс-дата и последний день покупки сверяются с листом этапа 1 (брокерский календарь, торговый календарь
    бумаги); расхождение — отказ: правило рабочих дней не знает особых переносов, такую дату вносят в лист руками."""
    record = dt.date.fromisoformat(r["record_date"])
    t1 = r["record_date"] >= T1_SWITCH
    ex = record if t1 and is_workday(record) else workday_before(record)
    out = {"record_date": r["record_date"], "settlement": "T+1" if t1 else "T+2", "ex_date": ex.isoformat(),
           "last_buy_date": workday_before(ex).isoformat(), "pay_date": workdays_after(record, PAY_WORKDAYS).isoformat()}
    broker = c.broker_rows()
    for ticker, cls in (("SBER", "dps_ordinary"), ("SBERP", "dps_preferred")):
        b = broker.get((ticker, str(year)))
        if b is None:
            raise BuildError(f"дивиденд за {year} г.: в брокерском календаре нет записи {ticker} — сверить не с чем")
        if Decimal(b["dps_gross_rub"]) != Decimal(r[cls]) or b["record_date"] != r["record_date"]:
            raise BuildError(f"дивиденд за {year} г. ({ticker}): лист решений расходится с брокерским календарём "
                             f"({r[cls]} ₽, {r['record_date']} против {b['dps_gross_rub']} ₽, {b['record_date']})")
        if (b["settlement"], b["ex_date_rule"]) != (out["settlement"], out["ex_date"]):
            raise BuildError(f"дивиденд за {year} г. ({ticker}): экс-дата по правилу {out['ex_date']} ({out['settlement']}) "
                             f"против торгового календаря листа этапа 1 {b['ex_date_rule']} ({b['settlement']})")
        if t1 and b["t_last_buy_date"] != out["last_buy_date"]:
            raise BuildError(f"дивиденд за {year} г. ({ticker}): последний день покупки по правилу {out['last_buy_date']} "
                             f"против брокерского календаря {b['t_last_buy_date']}")
    return out


def dividend_src(c: Ctx, r: dict) -> str:
    """Источник узла DPS: решение собрания и где оно раскрыто — лист решений (документ реестра)."""
    day = r["news_published"].partition(" ")[0]
    return c.dividends_ref(f"решение годового собрания акционеров {d_ru(r['decided_date'])} — страница «Дивиденды» на сайте "
                           f"банка и {r['news_outlet']} {d_ru(day)}")


def add_years(iso: str, years: int) -> str:
    d = dt.date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:                     # 29 февраля
        return d.replace(year=d.year + years, day=28).isoformat()


def policy_valid_until(approved: str, years: int, extension: dict | None) -> str:
    """Срок действия политики: дата утверждения + срок по документу; с продлением — дата продления
    (она обязана быть позже срока по документу: продление назад — ошибка записи)."""
    until = add_years(approved, years)
    if extension is None:
        return until
    if not extension.get("until") or extension["until"] <= until:
        raise BuildError(f"продление срока политики {extension.get('until')!r} не позже срока по документу {until}")
    return extension["until"]


def policy_term(c: Ctx) -> dict:
    """Поля срока политики: дата, страница документа с пунктом о сроке (или сообщение о продлении), записка."""
    until = policy_valid_until(POLICY_APPROVED, POLICY_TERM_YEARS, POLICY_EXTENSION)
    by_doc = add_years(POLICY_APPROVED, POLICY_TERM_YEARS)
    src = c.docs.ref(POLICY_DOC, POLICY_TERM_PAGE)
    note = POLICY_TERM_NOTE.format(until=d_ru(by_doc))
    if POLICY_EXTENSION is not None:
        ext = POLICY_EXTENSION
        src += "; " + c.file_ref(ext["file"], "", key=ext["key"], url=ext.get("url"), title=ext["title"])
        note += f". Срок продлён по {d_ru(until)}: {ext['words']}"
    return {"valid_until": until, "valid_until_src": src, "valid_until_note": note}


def ceil_kopeck(x: Decimal) -> Decimal:
    return (x * 100).to_integral_value(rounding=ROUND_CEILING) / 100


def build_dividends(c: Ctx, shares: dict, div: Declared) -> dict:
    pol = c.docs.ref(POLICY_DOC, None)
    pol_sha = c.docs.add(POLICY_DOC)
    rows = c.dividends()
    n_iss = Decimal(str(shares["issued_total"]["v"]))
    hist = []
    years = range(DIV_FIRST_YEAR, div.year + 1)      # годы прибыли теста DPS: от первого года политики до последнего решения
    for y in years:
        r = rows[y]
        dates = dividend_dates(c, y, r)
        ni = c.H.need("np_shareholders", f"FY{y}")
        cpn = -c.H.need("eqf_at1_coupon__retained", f"FY{y}")
        if y not in TAX_STATUTORY:
            raise BuildError(f"нет законной ставки налога {y} г. (TAX_STATUTORY: документ, страница, ставка)")
        tax_doc, tax_page, tax_rate, tax_what = TAX_STATUTORY[y]
        src = dividend_src(c, r)
        rec = {
            "year": y,
            "dps_ordinary": node(float(r["dps_ordinary"]), src, calc="объявленный дивиденд на обыкновенную акцию (решение ГОСА), ₽"),
            "dps_preferred": node(float(r["dps_preferred"]), src, calc="объявленный дивиденд на привилегированную акцию, ₽"),
            "record_date": dates["record_date"],
            "last_buy_date": dates["last_buy_date"],
            "ex_date": dates["ex_date"],
            "pay_date": dates["pay_date"],
            "settlement": dates["settlement"],
            "pool_declared": c.bal_n("div_declared_total", f"FY{y}"),
            "ni_shareholders": c.H.n("np_shareholders", f"FY{y}"),
            "at1_coupon": node(r1(cpn), c.H.src("eqf_at1_coupon__retained", f"FY{y}"), calc="купон AT1 года величиной (в отчёте о капитале — со знаком минус)"),
            "tax_statutory": node(tax_rate, c.docs.ref(tax_doc, tax_page), calc=tax_what),
        }
        total = Decimal(r["total_rub"]) / Decimal(10 ** 9)
        c.check(f"дивиденды за {y} г.: общий размер на странице эмитента = объявленному в отчётности МСФО",
                abs(total - Decimal(str(rec["pool_declared"]["v"]))) < Decimal("0.06"),
                f"{ru(float(total))} против {ru(rec['pool_declared']['v'])} млрд ₽")
        # источники записи (виды — как у реестра, MODEL §15): документ эмитента и новость о решении ГОСА
        rd = dt.date.fromisoformat(r["record_date"])
        sources = [f"manual: документ эмитента — страница «Дивиденды» на сайте Сбербанка (доступ {d_ru(r['accessed'])}): за {y} г. "
                   f"{ru(float(r['dps_ordinary']), 2)} ₽ на акцию, список лиц на {rd.strftime('%d.%m.%Y')} "
                   f"({r['issuer_page']}; {c.dividends_ref()})",
                   agm_news(c, y, r)]
        gap = (rd - dt.date.fromisoformat(r["decided_date"])).days
        c.check(f"реестр {y}: отсечка через 10–20 дней после решения ГОСА (MODEL §15)", 10 <= gap <= 20,
                f"решение {r['decided_date']}, отсечка {r['record_date']} (+{gap} дн.)")
        rec["decided_date"] = r["decided_date"]
        if any(x.split(":", 1)[0] not in REGISTER_KINDS for x in sources):
            raise BuildError(f"реестр {y}: вид источника вне {REGISTER_KINDS}")
        rec["sources"] = sources
        base = Decimal(str(ni)) - Decimal(str(cpn)) * (1 - Decimal(str(tax_rate)))
        pool = Decimal("0.5") * base
        dps = pool * 1000 / n_iss
        ok = ceil_kopeck(dps) == Decimal(str(rec["dps_ordinary"]["v"])).quantize(Decimal("0.01"))
        c.check(f"DPS {y}: ceil_kopeck(0,5 × (ЧП − купон × (1 − τ)) × 1000 / N_iss) = объявленный", ok,
                f"точный {dps:.5f} → {ceil_kopeck(dps)} против {rec['dps_ordinary']['v']}")
        hist.append(rec)
    before, skipped = [], []
    for y, r in sorted(rows.items()):
        if y >= DIV_FIRST_YEAR:
            continue
        src = dividend_src(c, r)
        if not r["record_date"]:                     # собрание решило дивиденды не выплачивать
            if any(k[1] == str(y) and k[0] in ("SBER", "SBERP") for k in c.broker_rows()):
                raise BuildError(f"дивиденд за {y} г.: по листу решений не выплачивался, а в брокерском календаре запись есть")
            skipped.append(y)
            why = f"решение ГОСА {d_ru(r['decided_date'])}: дивиденды за {y} г. не выплачивать"
            before.append({"year": y, "dps_ordinary": node(0.0, src, calc=why), "dps_preferred": node(0.0, src, calc="то же"),
                           "record_date": None, "ex_date": None, "pay_date": None, "settlement": None,
                           "decided_date": r["decided_date"]})
            continue
        dates = dividend_dates(c, y, r)
        before.append({"year": y,
                       "dps_ordinary": node(float(r["dps_ordinary"]), src, calc="объявленный дивиденд на обыкновенную акцию, ₽"),
                       "dps_preferred": node(float(r["dps_preferred"]), src, calc="объявленный дивиденд на привилегированную акцию, ₽"),
                       "record_date": dates["record_date"], "ex_date": dates["ex_date"], "pay_date": dates["pay_date"],
                       "settlement": dates["settlement"], "decided_date": r["decided_date"]})
    checked = sorted(y for y in rows if y <= div.year and y not in skipped)
    broker_check = (f"Дивиденд на акцию обеих категорий и дата отсечки за {checked[0]}–{checked[-1]} гг. сверены с брокерским "
                    f"календарём дивидендов (T-Invest, снимок {SNAPSHOT_RU}) — расхождений нет; экс-дата — с торговым "
                    f"календарём бумаги, последний день покупки за {years[0]}–{years[-1]} гг. — с брокерским календарём"
                    + (f"; за {', '.join(str(y) for y in skipped)} г. записи в календаре нет (дивиденды не выплачивались)"
                       if skipped else ""))
    dates_rule = ("Отсечка (record_date) — дата, на которую определяются лица, имеющие право на дивиденды (решение собрания). "
                  f"Экс-дата: при расчётах T+1 (с {d_ru(T1_SWITCH)}) — день отсечки, при T+2 — торговый день перед ней; последний "
                  "день покупки — торговый день перед экс-датой. Срок выплаты (pay_date) — 10-й рабочий день после "
                  "отсечки: к этому дню дивиденды получают номинальные держатели (брокеры и депозитарии), зачисление "
                  "на счёт у брокера — в этот или следующий рабочий день; лицам в реестре — до 25 рабочих дней")
    return {
        "basis": "IFRS",
        "as_of": c.a.date,
        "unit": "DPS — ₽ на акцию; суммы — млрд ₽; ставки — доли",
        "policy": {
            "name": "Положение о дивидендной политике ПАО Сбербанк",
            "doc": "dividend_policy_2023.pdf",
            "sha256": pol_sha,
            "approved": POLICY_APPROVED,
            "approved_by": POLICY_APPROVED_BY,
            # срок считается: дата утверждения + срок политики (или продление); литералом не вписывается
            # (MODEL прил. B). Истёкший срок сборку не останавливает — флаг выпуска policy_expired (MODEL §14.3)
            **policy_term(c),
            "base": "ifrs_ni_shareholders",
            "payout": node(0.5, c.docs.ref(POLICY_DOC, 4), calc="п. 2.3: целевой (минимальный) уровень 50 % ЧП группы по МСФО, скорректированной на купоны AT1 за вычетом налогового эффекта"),
            "threshold": node(0.133, c.docs.ref(POLICY_DOC, 4), calc="п. 2.2: Н20.0 на среднесрочном горизонте не менее 13,3 %"),
            "divisor": "issued",
            "divisor_src": c.docs.ref(POLICY_DOC, 3) + " (п. 1.6: DPS = часть прибыли категории / количество размещённых акций категории)",
            "text": ("Дивиденд — 50 % чистой прибыли группы по МСФО, приходящейся на акционеров, за вычетом купонов по AT1 после "
                     "налога; условие — Н20.0 не ниже 13,3 % на среднесрочном горизонте; DPS одинаков для обыкновенных и "
                     "привилегированных акций и считается на все размещённые акции."),
            "src": pol,
        },
        "history": hist,
        "history_before": before,
        "dates_rule": dates_rule,
        "broker_check": broker_check,
        "register_seed": [],
        "register_seed_note": (f"на дату книги объявленных и не выплаченных дивидендов нет: за {div.year} г. отсечка "
                               f"{d_ru(div.record_date)}, срок выплаты {d_ru(div.pay_date)} ("
                               + ("на якоре они — в balance.dividends_payable" if div.payable
                                  else "до якоря: balance.dividends_payable = 0")
                               + "). Формат записи — SCHEMA.md: у объявленного дивиденда два источника разных видов "
                                 "(брокерский календарь и новость о решении либо ручная запись по документу эмитента)"),
    }


# ------------------------------------------------------------------ bridge_mgmt_ifrs.json

CIR_INCOME = ("nii", "fees_net", "insurance_net", "misc_net")   # доход CIR движка (MODEL §4.6; FVR — внутри misc_net)


def cir_bridge(c: Ctx, pnl: dict) -> tuple[list[dict], dict]:
    """История CIR движка против упр. CIR и годовые сверки (мост CIR, MODEL §4.6, A7)."""
    M, qs = c.M, pnl["quarters"]
    hist = []
    for p in q_range(PNL_FIRST, c.a.period):
        r = qs[p]
        inc = [r[k]["v"] for k in CIR_INCOME]
        if None in inc:
            eng = na(f"в обобщённой форме {p} страхование и «прочее» не выделены — доход CIR движка не определяется")
        else:
            eng = node(r6(-r["opex"]["v"] / sum(inc)),
                       calc=(f"−opex {ru(r['opex']['v'])} / (nii + fees_net + insurance_net + misc_net) "
                             f"{ru(sum(inc))} (pnl_quarterly {p})"))
        mg = M.get("cir", p)
        rec = {"period": p, "mgmt": M.n("cir", p, scale=0.01) if mg is not None else na(f"упр. CIR за {p} не раскрыт"),
               "engine": eng}
        ok = mg is not None and eng["v"] is not None
        rec["gap"] = node(r6(eng["v"] - mg / 100), calc="engine − mgmt") if ok else na("нет одной из величин")
        hist.append(rec)
    annual = {}
    for y in full_years(PNL_FIRST, c.a.period):
        rows = [qs[f"{y}Q{k}"] for k in range(1, 5)]
        if any(r[k]["v"] is None for r in rows for k in CIR_INCOME) or not c.M.has("cir", f"{y}FY"):
            continue
        opex = sum(r["opex"]["v"] for r in rows)
        inc = sum(r[k]["v"] for r in rows for k in CIR_INCOME)
        eng = node(r6(-opex / inc), calc=f"Σ −opex {ru(-opex)} / Σ дохода {ru(inc)} за {y} (четыре квартала pnl_quarterly)")
        mg = c.M.n("cir", f"{y}FY", scale=0.01)
        annual[str(y)] = {"mgmt": mg, "engine": eng, "gap": node(r6(eng["v"] - mg["v"]), calc="engine − mgmt")}
    return hist, annual


def build_bridge_mgmt(c: Ctx, pnl: dict) -> dict:
    Q, M = c.Q, c.M
    cor_hist, nim_hist = [], []
    window = c.a.bridge_window
    win = q_range(*window)
    for p in q_range(PNL_FIRST, c.a.period):
        qq, days = ifrs_q(p), q_days(p)
        d1, d0 = q_end(p).isoformat(), q_end(q_prev(p)).isoformat()
        g1, g0 = c.bal("loans_ac_total_gross", d1), c.bal("loans_ac_total_gross", d0)
        llp = Q.need("llp_debt_fa", qq)
        eng = -llp / ((g0 + g1) / 2) * 365 / days
        mg = M.get("cor_total", p)
        rec = {"period": p,
               "mgmt": M.n("cor_total", p, scale=0.01) if mg is not None else na(f"упр. CoR за {p} не раскрыта"),
               "engine": node(r6(eng), src="; ".join([Q.src("llp_debt_fa", qq), c.bal_src("loans_ac_total_gross", d1), c.bal_src("loans_ac_total_gross", d0)]),
                              calc=f"−llp_debt_fa {ru(llp)} / средние валовые кредиты АС ({ru(g0)} + {ru(g1)})/2 × 365/{days}")}
        rec["gap"] = node(r6(eng - mg / 100), calc="engine − mgmt") if mg is not None else na("нет упр. CoR")
        cor_hist.append(rec)
        # ЧПМ движка — на процентных активах движка (кредиты валовые + долговые бумаги + ликвидность)
        mn = M.get("nim", p)
        try:
            i1, e1, h1 = iea(c, d1)
            i0, e0, h0 = iea(c, d0)
            nii = Q.need("nii", qq)
            engn = nii * 365 / days / ((i0 + i1) / 2)
            en = node(r6(engn), src=Q.src("nii", qq),
                      calc=(f"nii {ru(nii)} × 365/{days} / средние процентные активы ({ru(i0)} + {ru(i1)})/2"
                            + ("; процентные активы на дату — оценка (обобщённая форма)" if (e0 or e1) else "")
                            + f"; на {d0}: {h0}; на {d1}: {h1}"))
        except BuildError as e:
            en = na(f"процентные активы на концы квартала не определяются ({e})")
        nrec = {"period": p, "mgmt": M.n("nim", p, scale=0.01) if mn is not None else na(f"упр. ЧПМ за {p} не раскрыта"), "engine": en}
        nrec["gap"] = node(r6(en["v"] - mn / 100), calc="engine − mgmt") if (mn is not None and en["v"] is not None) else na("нет одной из величин")
        nim_hist.append(nrec)

    def mean_gap(hist):
        gs = [h["gap"]["v"] for h in hist if h["period"] in win and h["gap"]["v"] is not None]
        if len(gs) != len(win):
            raise BuildError("в окне моста не хватает кварталов")
        return statistics.fmean(gs), statistics.stdev(gs), len(gs)

    cm, cs, cn = mean_gap(cor_hist)
    nm, ns, nn = mean_gap(nim_hist)
    cir_hist, cir_annual = cir_bridge(c, pnl)
    im, is_, in_ = mean_gap(cir_hist)
    # коридоры сверок — те же, что в tests/test_facts_identities.py: знак и порядок величины моста, не значение якоря
    c.check("мост CoR движок − упр.: 0…+0,4 п.п. (движок выше упр.)", 0.0 < cm < 0.004, f"{ru(cm * 100, 3)} п.п., σ {ru(cs * 100, 3)}, n {cn}")
    c.check("мост ЧПМ движок − упр.: −0,6…0 п.п. (движок ниже упр.)", -0.006 < nm < 0.0, f"{ru(nm * 100, 3)} п.п., σ {ru(ns * 100, 3)}, n {nn}")
    c.check("мост CIR движок − упр.: −4…0 п.п. (движок ниже упр.)", -0.04 < im < 0.0,
            f"{ru(im * 100, 3)} п.п., σ {ru(is_ * 100, 3)}, n {in_}; годы: "
            + ", ".join(f"{y} {ru(a['engine']['v'] * 100, 1)} против {ru(a['mgmt']['v'] * 100, 1)} %" for y, a in cir_annual.items()))
    return {
        "basis": "mgmt↔IFRS",
        "as_of": c.a.date,
        "unit": "доли, годовые",
        "window": list(window),
        "direction": "to_engine(v) = v + value (additive); value = среднее по окну (engine − mgmt)",
        "cor": {
            "method": "additive",
            "value": node(r6(cm), calc=f"среднее gap по окну {window[0]}–{window[1]} (n = {cn})"),
            "sd": node(r6(cs), calc="выборочное стандартное отклонение gap по окну"),
            "n": node(cn, calc="кварталов в окне"),
            "definition": "CoR движка = −llp_debt_fa / средние валовые кредиты АС × 365/d (резервы по долговым ФА; MODEL §4.6); упр. — совокупная стоимость риска презентаций",
            "history": cor_hist,
        },
        "nim": {
            "method": "additive",
            "value": node(r6(nm), calc=f"среднее gap по окну {window[0]}–{window[1]} (n = {nn})"),
            "sd": node(r6(ns), calc="выборочное стандартное отклонение gap по окну"),
            "n": node(nn, calc="кварталов в окне"),
            "definition": ("ЧПМ движка = ЧПД квартала × 365/d / средние процентные активы движка (кредиты АС валовые + по СС + "
                           "долговые бумаги с репо, без акций и фондов + ликвидность; MODEL §4.4); упр. — ЧПМ презентаций"),
            "history": nim_hist,
        },
        "cir": {
            "method": "additive",
            "value": node(r6(im), calc=f"среднее gap по окну {window[0]}–{window[1]} (n = {in_})"),
            "sd": node(r6(is_), calc="выборочное стандартное отклонение gap по окну"),
            "n": node(in_, calc="кварталов в окне"),
            "definition": ("CIR движка = −opex / (nii + fees_net + insurance_net + misc_net) квартала МСФО (переоценка облигаций "
                           "по СС — внутри misc_net; непрофильная деятельность — вне; MODEL §4.6, §14.2); упр. — CIR презентаций. "
                           "Мост служит гайденсу и печати (paths.annual[].cir_mgmt), не гейту bridge_drift"),
            "history": cir_hist,
            "annual": cir_annual,
        },
    }


# ------------------------------------------------------------------ bridge_ras_ifrs.json

class Release:
    """Месячные релизы РСБУ банка (stage1/ras/ras_release_monthly.csv): число и документ ячейки."""

    SHEET = "stage1/ras/ras_release_monthly.csv"

    def __init__(self, c: Ctx):
        self.c = c
        self.rows = {(r["metric"], r["month"]): r for r in c.csv_rows("ras/ras_release_monthly.csv")}

    def get(self, metric: str, month: str) -> float | None:
        r = self.rows.get((metric, month))
        return float(r["value"]) if r and r["value"] not in ("", None) else None

    def ref(self, metric: str, month: str) -> str:
        """PDF релиза (если лист называет sha256), иначе ячейка листа этапа 1 с её источником."""
        r = self.rows[(metric, month)]
        sha = re.search(r"sha256 ([0-9a-f]{64})", r["source_ref"])
        page = re.search(r"стр\. (\d+)", r["source_ref"])
        if sha:
            y, mm = month.split("-")
            names = [f"ras_{y}M{mm}_press_release.pdf", f"{y}M{mm}_ras_press_release.pdf"]
            name = next((n for n in names if self.c.docs.locate(n) is not None), names[0])
            return self.c.docs.ref(name, page.group(1) if page else None, sha.group(1))
        url = r["source_ref"].split(" ")[0]
        return self.c.file_ref(self.SHEET, f"{metric} {month}: {r['source_kind']} ({url}; подтверждено: {r['confirmed_by']})",
                               key="ras_release_monthly",
                               title=RAS_SHEET_TITLE)

    def quarter(self, metric_ytd: str, p: str) -> tuple[float, str, str]:
        """Квартал из нарастающих итогов релизов: (значение, src, calc)."""
        y, k = q_parse(p)
        m1 = f"{y}-{3 * k:02d}"
        cur = self.get(metric_ytd, m1)
        if cur is None:
            raise BuildError(f"нет {metric_ytd} за {m1}")
        if k == 1:
            return cur, self.ref(metric_ytd, m1), f"{metric_ytd} за 3 месяца {y} (релиз РСБУ)"
        m0 = f"{y}-{3 * k - 3:02d}"
        prev = self.get(metric_ytd, m0)
        if prev is None:
            raise BuildError(f"нет {metric_ytd} за {m0}")
        return (cur - prev, f"{self.ref(metric_ytd, m1)}; {self.ref(metric_ytd, m0)}",
                f"{metric_ytd}: {ru(cur)} ({m1}) − {ru(prev)} ({m0}) — разность нарастающих итогов релизов")


def backtest(ratios: list[tuple[str, float]]) -> dict:
    """RMSE прогноза отношения квартала: прошлым кварталом, средним четырёх прошлых, тем же кварталом прошлых лет."""
    e1, e4, eq = [], [], []
    for i in range(4, len(ratios)):
        p, x = ratios[i]
        prev = [r for _, r in ratios[:i]]
        same = [r for q, r in ratios[:i] if q[-1] == p[-1]]
        e1.append(prev[-1] - x)
        e4.append(statistics.fmean(prev[-4:]) - x)
        eq.append(statistics.fmean(same) - x)
    span = f"{ratios[4][0]}–{ratios[-1][0]}, n = {len(e1)}"
    return {
        "prev_quarter": node(r6(rmse(e1)), calc=f"RMSE: отношение прошлого квартала, {span}"),
        "mean_4": node(r6(rmse(e4)), calc=f"RMSE: среднее четырёх прошлых кварталов, {span}"),
        "same_quarter": node(r6(rmse(eq)), calc=f"RMSE: среднее того же квартала прошлых лет, {span}"),
    }


def ratio_block(rows: list[dict], window_q: int, *, what: str, definition: str, extra: dict | None = None) -> dict:
    """Блок моста РСБУ → МСФО: кварталы, окно (последние window_q), value, by_quarter = value, бэктест."""
    win = [r["period"] for r in rows][-window_q:]
    xs = [r["ratio"]["v"] for r in rows if r["period"] in win]
    val = statistics.fmean(xs)
    bt = backtest([(r["period"], r["ratio"]["v"]) for r in rows])
    seas = (f"сезонность не подтверждена: RMSE по тому же кварталу прошлых лет {ru(bt['same_quarter']['v'] * 100, 1)} % "
            f"против {ru(bt['prev_quarter']['v'] * 100, 1)} % по прошлому кварталу — все кварталы = value")
    out = {
        "definition": definition,
        "method": (f"value — среднее ratio за последние {window_q} кв. до якоря (окно window); {what}; by_quarter = value "
                   f"(сезонного моста нет); нау-каст замораживает отношение при первой записи квартала"),
        "window": [win[0], win[-1]],
        "quarters": rows,
        "value": node(r6(val), calc=f"среднее ratio {win[0]}–{win[-1]} (n = {len(xs)}: " + ", ".join(ru(x, 4) for x in xs) + ")"),
        "n": node(len(xs), calc="кварталов в окне"),
        "by_quarter": {str(k): node(r6(val), calc=seas) for k in range(1, 5)},
        "backtest": bt,
    }
    if len(xs) > 1:
        out["sd"] = node(r6(statistics.stdev(xs)), calc="выборочное стандартное отклонение ratio по окну")
    if extra:
        out.update(extra)
    return out


def ras_nii_bridge(c: Ctx, rel: Release, pnl: dict) -> dict:
    """ЧПД РСБУ банка (релиз) → ЧПД группы МСФО по кварталам (T3, MODEL §12, B1)."""
    rows = []
    for p in q_range(PNL_FIRST, c.a.period):
        v, src, calc = rel.quarter("nii_ytd", p)
        n_ras = node(r1(v), src, calc)
        n_ifrs = pnl["quarters"][p]["nii"]
        rows.append({"period": p, "ras": n_ras, "ifrs": n_ifrs,
                     "ratio": node(r6(n_ifrs["v"] / n_ras["v"]), calc="ifrs / ras"),
                     "src": dedup(f"{n_ras['src']}; {n_ifrs['src']}")})
    return ratio_block(rows, RAS_NII_WINDOW_Q, what="ЧПД МСФО квартала = ЧПД РСБУ квартала × value",
                       definition=("ras — ЧПД банка РСБУ квартала по релизу (внутренняя методика банка; кварталы 2–4 — разность "
                                   "нарастающих итогов); ifrs — ЧПД группы МСФО квартала (pnl_quarterly.nii); ratio = ifrs / ras"))


def f101_rows(c: Ctx) -> dict[str, dict[str, tuple[float, str]]]:
    """Агрегированная форма 0409101 (с 06.2023): дата → код → (исходящий остаток актива, млрд ₽; sha256 ответа)."""
    out: dict[str, dict[str, tuple[float, str]]] = {}
    for r in c.csv_rows("ras/cbr_f101_agg.csv"):
        if r["era"] != "aggregated_2023+" or r["chapter"] != "А" or r["ap"] != "1" \
                or r["row_type"] not in ("aggregate", "account3"):
            continue
        if r["value_out_thousand_rub"] in ("", None):
            continue
        out.setdefault(r["form_date"], {})[r["code"]] = (float(r["value_out_thousand_rub"]) / 1e6, r["sha256"])
    return out


def f101_iea(c: Ctx, f101: dict, d: str) -> tuple[float, str, str] | None:
    """Процентные активы банка РСБУ на дату формы: (млрд ₽, src, состав) или None, если формы нет."""
    rows = f101.get(d)
    if rows is None:
        return None
    total, parts, absent = 0.0, [], []
    for group, codes in F101_IEA.items():
        s = 0.0
        for code in codes:
            hit = next((a for a in code.split("|") if a in rows), None)
            if hit is None:
                if code in F101_REQUIRED:
                    raise BuildError(f"форма 0409101 на {d}: нет обязательного кода {code}")
                absent.append(code)
                continue
            s += rows[hit][0]
        total += s
        parts.append(f"{group} {ru(s)}")
    shas = {sha for _, sha in rows.values()}
    if len(shas) != 1:
        raise BuildError(f"форма 0409101 на {d}: строки листа из разных ответов")
    rel = f"stage1/ras/raw/101/{d}.xml"
    ref = c.file_ref(rel, f"форма 0409101 на {d}", key=f"cbr_f101_{d}",
                     url="https://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx (Data101FNewXML)",
                     title=f"Банк России, CreditOrgInfo: форма 0409101 банка на {d} (ответ SOAP)")
    if c.docs.sha_of(c.root / rel) != shas.pop():
        raise BuildError(f"форма 0409101 на {d}: sha256 листа ≠ файлу")
    how = " + ".join(parts) + (f" (нет в форме — нулевой остаток: {', '.join(absent)})" if absent else "")
    return total, ref, how


def ras_iea_bridge(c: Ctx, bal: dict) -> dict:
    """Средние процентные активы банка РСБУ (форма 0409101) → средние процентные активы движка группы МСФО."""
    f101 = f101_rows(c)
    hist = bal["history"]
    rows = []
    for p in q_range(PNL_FIRST, c.a.period):
        p0 = q_prev(p)
        d0 = (q_end(p0) + dt.timedelta(days=1)).isoformat()
        d1 = (q_end(p) + dt.timedelta(days=1)).isoformat()
        a, b = f101_iea(c, f101, d0), f101_iea(c, f101, d1)
        if a is None or b is None or p0 not in hist or hist[p0]["iea"]["v"] is None or hist[p]["iea"]["v"] is None:
            continue
        n_ras = node(r6((a[0] + b[0]) / 2), f"{a[1]}; {b[1]}",
                     f"(на {d0}: {ru(a[0])} + на {d1}: {ru(b[0])}) / 2; на {d1}: {b[2]}")
        i0, i1 = hist[p0]["iea"], hist[p]["iea"]
        est = any("оценка" in (x.get("calc") or "") for x in (i0, i1))
        n_ifrs = node(r6((i0["v"] + i1["v"]) / 2),
                      calc=(f"(balance.history.{p0}.iea {ru(i0['v'])} + balance.history.{p}.iea {ru(i1['v'])}) / 2 — "
                            f"процентные активы движка" + ("; на дату — оценка (обобщённая форма)" if est else "")))
        rows.append({"period": p, "ras": n_ras, "ifrs": n_ifrs,
                     "ratio": node(r6(n_ifrs["v"] / n_ras["v"]), calc="ifrs / ras"), "src": n_ras["src"]})
    if len(rows) < 5:
        raise BuildError("мост процентных активов: меньше пяти кварталов")
    codes = {g: list(cs) for g, cs in F101_IEA.items()}
    return ratio_block(
        rows, RAS_IEA_WINDOW_Q, what="средние процентные активы движка = средние процентные активы РСБУ × value",
        definition=("ras — средние процентные активы банка РСБУ квартала: (форма 0409101 на 1-е число первого месяца "
                    "квартала + на 1-е число месяца после квартала) / 2, исходящие остатки актива по кодам codes (кредиты, "
                    "МБК размещённые, долговые бумаги); ifrs — средние процентные активы движка группы (balance.history.iea: "
                    "кредиты АС валовые + по СС + долговые бумаги + ликвидность); ratio = ifrs / ras. ЧПМ движка (T3) = "
                    "ЧПД РСБУ × nii.value × 365/d / (средние активы РСБУ × iea.value)"),
        extra={"basis": "engine", "codes": codes, "codes_required": list(F101_REQUIRED),
               "codes_note": ("«45.0|45.1» — 45.0, до 01.2024 — 45.1 (актив); кода нет в форме — нулевой остаток; нет "
                              "обязательного кода — квартал не считается; 501/502/504 — долговые бумаги по СС через ПУ / "
                              "через ПСД / по амортизированной стоимости (справочник ЦБ называет их по старому плану счетов)")})


def ras_cor_bridge(c: Ctx, rel: Release) -> dict:
    """CoR РСБУ → упр.: строится, только если разность устойчивее самого CoR; иначе null с причиной (T4 = ожидание)."""
    pairs = []
    for p in q_range(PNL_FIRST, c.a.period):
        y, k = q_parse(p)
        cur = rel.get("cor_ytd", f"{y}-{3 * k:02d}")
        prev = rel.get("cor_ytd", f"{y}-{3 * k - 3:02d}") if k > 1 else 0.0
        mg = c.M.get("cor_total", p)
        if cur is None or prev is None or mg is None:
            continue
        pairs.append((p, (cur * k - prev * (k - 1)) / 100, mg / 100))
    if len(pairs) < 4:
        return node(None, calc="нет данных: CoR РСБУ в релизах — меньше четырёх кварталов; нау-каст CoR (T4) = ожидание модели")
    gaps = [r - m for _, r, m in pairs]
    gm, gs = statistics.fmean(gaps), statistics.stdev(gaps)
    ms = statistics.stdev([m for _, _, m in pairs])
    c.check("мост CoR РСБУ → упр. не строится: разброс разности ≥ разброса самого упр. CoR", gs >= ms,
            f"разность {ru(gm * 100, 2)} п.п., σ {ru(gs * 100, 2)} п.п. против σ упр. CoR {ru(ms * 100, 2)} п.п., n {len(pairs)}")
    last = pairs[-1][0]
    return node(None, calc=(
        f"нет данных для моста: CoR РСБУ (релиз, «без валютной переоценки») публикуется нарастающим итогом с точностью "
        f"0,1 п.п. — квартал разностью несёт ошибку округления до ±0,35 п.п.; РСБУ — банк без дочерних обществ. Квартальный CoR РСБУ "
        f"минус упр. CoR, {pairs[0][0]}–{last} (n = {len(pairs)}): среднее {ru(gm * 100, 2)} п.п., σ {ru(gs * 100, 2)} п.п. — "
        f"не меньше разброса самого упр. CoR тех же кварталов (σ {ru(ms * 100, 2)} п.п.): мост не несёт информации. "
        f"Нау-каст CoR (T4) = ожидание модели (MODEL §12)"),
        src=rel.ref("cor_ytd", f"{last[:4]}-{3 * q_parse(last)[1]:02d}"))


def build_bridge_ras(c: Ctx, pnl: dict, bal: dict) -> dict:
    f102 = [r for r in c.csv_rows("ras/cbr_f102_monthly.csv") if r["symbol"] == "61101" and r["form_variant"].startswith("aggregated")]
    by_date = {r["form_date"]: r for r in f102}
    rel = {r["month"]: r for r in c.csv_rows("ras/ras_release_monthly.csv") if r["metric"] == "np_ytd"}
    idx = {(r["form"], r["name"]): r for r in c.csv_rows("ras/raw/INDEX.csv")}

    def ytd(y: int, k: int) -> tuple[float, str]:
        fd = (q_end(f"{y}Q{k}") + dt.timedelta(days=1)).isoformat()
        r = by_date.get(fd)
        if r is not None and (("102", fd) in idx):
            ref = c.file_ref(f"stage1/ras/raw/102/{fd}.xml", f"форма 0409102 на {fd}, символ 61101", key=f"cbr_f102_{fd}",
                             url=f"https://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx ({idx[('102', fd)]['method']})",
                             title=f"Банк России, CreditOrgInfo: форма 0409102 банка на {fd} (ответ SOAP)")
            return float(r["value_ytd_thousand_rub"]) / 1e6, ref
        m = f"{y}-{3 * k:02d}"
        rr = rel.get(m)
        if rr is None:
            raise BuildError(f"нет ЧП РСБУ за {m}")
        pdf = f"ras_{y}M{3 * k:02d}_press_release.pdf"
        sha = re.search(r"sha256 ([0-9a-f]{64})", rr["source_ref"])
        page = re.search(r"стр\. (\d+)", rr["source_ref"])
        if not sha:
            raise BuildError(f"релиз РСБУ {m} без sha256")
        return float(rr["value"]), c.docs.ref(pdf, page.group(1) if page else None, sha.group(1))

    quarters = []
    ratios: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    for p in q_range(PNL_FIRST, c.a.period):
        y, k = q_parse(p)
        cur, src_c = ytd(y, k)
        if k == 1:
            ras_q, calc = cur, "ЧП банка РСБУ нарастающим за 3 месяца"
            srcs = src_c
        else:
            prev, src_p = ytd(y, k - 1)
            ras_q, calc = cur - prev, f"ЧП РСБУ нарастающим: {ru(cur)} − {ru(prev)} (разность нарастающих)"
            srcs = f"{src_c}; {src_p}"
        ifrs = c.Q.need("np_shareholders", ifrs_q(p))
        ratio = ifrs / ras_q
        ratios[k].append(ratio)
        n_ras, n_ifrs = node(r6(ras_q), srcs, calc), c.Q.n("np_shareholders", ifrs_q(p))
        quarters.append({"period": p, "ras_ni": n_ras, "ifrs_ni_sh": n_ifrs,
                         "ratio": node(r6(ratio), calc="ifrs_ni_sh / ras_ni"),
                         "src": dedup(f"{n_ras['src']}; {n_ifrs['src']}")})
    by_q = {}
    for k in (1, 2, 3, 4):
        xs = ratios[k]
        by_q[str(k)] = node(r6(statistics.fmean(xs)), calc=f"среднее ratio кварталов К{k} ({len(xs)} шт.: " + ", ".join(ru(x, 3) for x in xs) + ")")
    c.check("сезонный мост РСБУ → МСФО: К1 выше К4 (годовые корректировки РСБУ — в декабре)", by_q["1"]["v"] > by_q["4"]["v"],
            ", ".join(f"К{k} {ru(by_q[str(k)]['v'], 3)}" for k in (1, 2, 3, 4)))
    annual = {}
    for y in full_years(PNL_FIRST, c.a.period):
        rs = [q for q in quarters if q["period"].startswith(str(y))]
        annual[str(y)] = node(r6(sum(q["ifrs_ni_sh"]["v"] for q in rs) / sum(q["ras_ni"]["v"] for q in rs)),
                              calc=f"Σ ifrs_ni_sh / Σ ras_ni за {y}")
    rel = Release(c)
    nii = ras_nii_bridge(c, rel, pnl)
    iea = ras_iea_bridge(c, bal)
    c.check(f"мост ЧПД РСБУ → МСФО с {RAS_NII_STABLE_FROM} в 1,12–1,18 (этап 1: 1,135–1,170)",
            all(1.12 <= r["ratio"]["v"] <= 1.18 for r in nii["quarters"] if r["period"] >= RAS_NII_STABLE_FROM),
            f"value {ru(nii['value']['v'], 4)} ({nii['window'][0]}–{nii['window'][1]}); бэктест RMSE: прошлый квартал "
            f"{ru(nii['backtest']['prev_quarter']['v'] * 100, 2)} %, среднее 4 — {ru(nii['backtest']['mean_4']['v'] * 100, 2)} %")
    c.check("мост процентных активов: отношение 1,0–1,2, квартальный шаг ≤ 2 %",
            all(1.0 < r["ratio"]["v"] < 1.2 for r in iea["quarters"])
            and all(abs(b["ratio"]["v"] / a["ratio"]["v"] - 1) <= 0.02 for a, b in zip(iea["quarters"], iea["quarters"][1:])),
            f"value {ru(iea['value']['v'], 4)} ({iea['window'][0]}); кварталов {len(iea['quarters'])}; бэктест RMSE: прошлый "
            f"квартал {ru(iea['backtest']['prev_quarter']['v'] * 100, 2)} %, среднее 4 — {ru(iea['backtest']['mean_4']['v'] * 100, 2)} %")
    return {
        "basis": "RAS→IFRS",
        "as_of": c.a.date,
        "unit": "млрд ₽ за квартал (активы — средние за квартал); отношения — доли",
        "definition": "ratio = ЧП акционерам группы МСФО квартала / ЧП банка РСБУ квартала (форма 0409102, символ 61101; " + RAS_Q1_NOTE + ")",
        "quarters": quarters,
        "by_quarter": by_q,
        "annual": annual,
        "nii": nii,
        "iea": iea,
        "cor": ras_cor_bridge(c, rel),
    }


# ------------------------------------------------------------------ mgmt_quarterly.json

MGMT_FIELDS = {   # поле факта → (метрика mgmt_kpi, масштаб)
    "nim": ("nim", 0.01), "cor": ("cor_total", 0.01), "cor_corporate": ("cor_corporate", 0.01), "cor_retail": ("cor_retail", 0.01),
    "cir": ("cir", 0.01), "roe": ("roe", 0.01), "cost_of_funding": ("cost_of_funding", 0.01), "asset_yield": ("asset_yield", 0.01),
    "cost_retail_funds": ("retail_deposit_cost", 0.01), "cost_corp_funds": ("corp_deposit_cost", 0.01), "n20_0": ("n20_0", 0.01),
    "loans_corporate": ("corp_loans_mgmt", 1.0), "loans_retail": ("retail_loans_mgmt", 1.0),
    "provisions_credit": ("provisions_credit_mgmt", 1.0),
}
GAP_2022 = {"2021Q4", "2022Q1", "2022Q2", "2022Q3", "2022Q4"}
GAP_2022_NOTE = "квартальные презентации 4К21–4К22 не публиковались (раскрыт только 2022 год)"


def build_mgmt(c: Ctx) -> dict:
    M = c.M
    quarters = {}
    for p in q_range(MGMT_FIRST, c.a.period):
        row = {}
        for f, (m, sc) in MGMT_FIELDS.items():
            if M.has(m, p):
                row[f] = M.n(m, p, scale=sc)
            elif p in GAP_2022:
                row[f] = na(GAP_2022_NOTE)
            else:
                row[f] = na(f"метрика «{m}» за {p} в презентациях не раскрыта")
        lc, lr = row["loans_corporate"]["v"], row["loans_retail"]["v"]
        row["loans_mgmt"] = node(r1(lc + lr), calc="loans_corporate + loans_retail") if lc is not None and lr is not None \
            else na("нет одного из сегментов кредитного портфеля")
        quarters[p] = row
    annual = {}
    for y in range(q_parse(MGMT_FIRST)[0], c.a.fy_last_year + 1):
        rec = {}
        for f, m in (("nim", "nim"), ("cor", "cor_total"), ("cir", "cir"), ("roe", "roe")):
            rec[f] = M.n(m, f"{y}FY", scale=0.01) if M.has(m, f"{y}FY") else na(f"годовая «{m}» за {y} не собрана")
        annual[str(y)] = rec
    return {
        "basis": "mgmt",
        "as_of": c.a.date,
        "unit": "доли (проценты презентаций / 100), годовые за квартал; кредиты и резервы — млрд ₽",
        "note": "управленческий базис презентаций МСФО; в движок — только через bridge_mgmt_ifrs (MODEL §4.6)",
        "quarters": quarters,
        "annual": annual,
    }


# ------------------------------------------------------------------ guidance.json

# ключ факта → (метрика guidance.csv, вид: point | min | max | range, отношение к сектору). Вид — по умолчанию:
# граница в тексте строки листа («>6.2%», «<1.4%») задаёт вид сама (`guidance_kind`) — эмитент меняет точку
# на границу при пересмотре (ЧПМ 2026: «~6.2%» → «>6.2%» 28.10.2026).
GUIDANCE_KEYS = {
    "roe": ("roe", "point", None), "nim": ("nim", "point", None), "cor_max": ("cor", "max", None),
    "cir": ("cir", "range", None), "fee_growth": ("fee_growth", "point", None),
    "n20_0": ("n20_0", "min", None),     # Н20.0 — пол политики («>13,3 %»), не точка
    # рост кредитов — прогноз СЕКТОРА, а не банка: relation говорит, как банк к нему относится
    "loan_growth.corporate": ("sector_corp_loans", "range", "in_line"),
    "loan_growth.retail": ("sector_retail_loans", "range", "above"),
}
SEGMENT_RU = {"corporate": "ЮЛ", "retail": "ФЛ"}
NUMBER_RE = re.compile(r"\d+(?:[.,](\d+))?")
BOUND_KINDS = {">": "min", "≥": "min", "<": "max", "≤": "max"}    # знак границы в начале текста строки листа


def guidance_kind(text: str, default: str) -> str:
    """Вид узла гайденса: граница — по тексту строки листа («>6.2%» → min, «<1.4%» → max), иначе вид из
    `GUIDANCE_KEYS` (MODEL §14.2, прил. B: min — нижняя граница, max — верхняя, допуска у них нет)."""
    return BOUND_KINDS.get(str(text or "").strip()[:1], default)


def point_tol(text: str) -> float:
    """Допуск точечного гайденса: половина единицы последнего разряда числа, как оно записано в
    презентации («22%» → 0,005; «~6.2%» → 0,0005; «~0%» → 0,005), в долях (MODEL §14.2, прил. B)."""
    m = NUMBER_RE.search(text)
    if not m or "%" not in text:
        raise BuildError(f"точечный гайденс «{text}»: нет числа в процентах — допуск не определяется")
    return r6(0.5 * 10 ** -len(m.group(1) or "") / 100)


SHORT_DATE_RE = re.compile(r"(?<![\d.,])(\d{2})\.(\d{2})(?!\d|\.\d|\s?%)")


def dates_with_year(text: str, known: list[str], upto: str) -> str:
    """Короткая дата «дд.мм» в записке листа → с годом: «к 29.04» → «к 29.04.2026».

    Записки гайденса печатаются на витрине, а короткая дата читается там как дробь (PAYLOAD §0.2). Год
    берётся из известных дат гайденса: последняя с тем же днём и месяцем не позже `upto`. Даты с таким
    днём и месяцем нет — отказ: год не угадывается."""
    def full(m: re.Match) -> str:
        dd, mm = int(m.group(1)), int(m.group(2))
        if not (1 <= dd <= 31 and 1 <= mm <= 12):
            return m.group(0)
        hits = sorted(d for d in known if d <= upto and d[5:] == f"{mm:02d}-{dd:02d}")
        if not hits:
            raise BuildError(f"записка «{text}»: короткая дата {m.group(0)} без года — среди дат гайденса такой нет")
        return d_ru(hits[-1])
    return SHORT_DATE_RE.sub(full, text or "")


def build_guidance(c: Ctx) -> dict:
    rows = [r for r in c.csv_rows("mgmt/guidance.csv") if r["target_year"] == str(c.a.year)]
    known = sorted({r["as_of"] for r in rows if len(r["as_of"]) == 10})

    def words(r) -> str:                   # записка листа: короткие даты — с годом
        return dates_with_year(r["note"], known, r["as_of"])

    def val(r, kind):
        lo = float(r["low"]) / 100 if r["low"] else None
        hi = float(r["high"]) / 100 if r["high"] else None
        if kind == "max":
            return r6(hi)
        if kind == "min":
            return r6(lo if lo is not None else hi)
        if kind == "range" or (lo is not None and hi is not None and lo != hi):
            return [r6(lo), r6(hi)]
        return r6(hi if hi is not None else lo)

    def ref(r):
        return c.docs.ref(r["doc"], r["page"], r["sha256"])

    items: dict = {}
    history = []
    as_of = None
    sector = []
    for key, (metric, default_kind, relation) in GUIDANCE_KEYS.items():
        rs = sorted((r for r in rows if r["metric"] == metric), key=lambda r: (r["as_of"], r["doc"]))
        if not rs:
            raise BuildError(f"нет гайденса {metric} на {c.a.year} г.")
        last = rs[-1]
        as_of = max(as_of or last["as_of"], last["as_of"])
        kind = guidance_kind(last["text"], default_kind)
        extra = {"kind": kind, "text": last["text"], "date": last["as_of"], "event": last["event"]}
        if kind == "point":
            extra["tol"] = point_tol(last["text"])
        if relation is None:
            extra["scope"] = "group"
        else:
            # relation — структурное поле; слова презентации в записке листа обязаны его подтверждать
            if RELATION_WORDS[relation] not in last["note"]:
                raise BuildError(f"гайденс {metric}: в записке листа «{last['note']}» нет слов «{RELATION_WORDS[relation]}» "
                                 f"— отношение банка к сектору изменилось, поправить GUIDANCE_KEYS")
            extra["scope"] = "sector"
            extra["relation"] = relation
            extra["scope_note"] = words(last)
            sector.append(f"{SEGMENT_RU[key.split('.')[1]]} «{RELATION_WORDS[relation]}»")
        n = node(val(last, kind), ref(last), calc=f"«{last['text']}» → доли", **extra)
        if "." in key:
            a, b = key.split(".")
            items.setdefault(a, {})[b] = n
        else:
            items[key] = n
        seen = set()
        for r in rs:
            sig = (r["as_of"], r["text"])
            if sig in seen:
                continue
            seen.add(sig)
            history.append({"date": r["as_of"], "key": key,
                            "value": node(val(r, guidance_kind(r["text"], default_kind)), ref(r),
                                          calc=f"«{r['text']}» → доли"),
                            "event": r["event"], "note": words(r)})
    history.sort(key=lambda h: (h["date"], h["key"]))
    return {
        "basis": "mgmt",
        "year": c.a.year,
        "as_of": as_of,
        "unit": ("доли; диапазон — [мин, макс]; cor_max — верхняя граница; tol — допуск точечного значения (половина "
                 "единицы последнего разряда числа в презентации)"),
        "note": ("гайденс дан в управленческих метриках; scope: group — цель банка, sector — прогноз сектора "
                 f"(рост кредитов; relation — отношение банка к нему: {', '.join(sector)})"),
        "items": items,
        "history": history,
    }


# ------------------------------------------------------------------ сборка, запись, сверка

def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def build(root: Path, anchor: Anchor, sheets: tuple[Path, ...] = ()) -> tuple[dict[str, dict], list]:
    files, c = build_ctx(root, anchor, sheets)
    return files, c.checks


def build_ctx(root: Path, anchor: Anchor, sheets: tuple[Path, ...] = ()) -> tuple[dict[str, dict], Ctx]:
    """Сборка с контекстом: факты и всё прочитанное (журнал чтений листов — для шаблона листа квартала)."""
    c = Ctx(root, anchor, sheets)
    div = declared_dividend(c)
    out: dict[str, dict] = {}
    out["balance"] = build_balance(c, div)
    out["nii_books"] = build_nii_books(c, out["balance"])
    out["pnl_quarterly"] = build_pnl(c)
    out["capital"] = build_capital(c, out["balance"], div)
    out["shares"] = build_shares(c, div)
    out["dividends"] = build_dividends(c, out["shares"], div)
    out["bridge_mgmt_ifrs"] = build_bridge_mgmt(c, out["pnl_quarterly"])
    out["bridge_ras_ifrs"] = build_bridge_ras(c, out["pnl_quarterly"], out["balance"])
    out["mgmt_quarterly"] = build_mgmt(c)
    out["guidance"] = build_guidance(c)
    out["anchor"] = build_anchor(c)          # последним: реестр документов полон
    return {k: out[k] for k in BUILT}, c


# ------------------------------------------------------------------ лист квартала: шаблон
#
# Лист квартала — строки отчёта и презентации нового квартала в формате листов этапа 1 (SCHEMA.md §5).
# Шаблон следующего квартала строится из журнала чтений сборки на якоре: каждая строка листов, прочитанная
# для квартала якоря, понадобится и для следующего — с его метками периода. Колонки prev_* говорят, где
# та же строка стояла в прошлом отчёте (документ, страница): что внести и откуда.

def quarter_labels(old: Anchor, new: Anchor) -> dict[str, str]:
    """Метки периода, привязанные к кварталу якоря, → метки нового квартала: дата (остатки), квартал
    листов МСФО, нарастающий итог, квартал управленческих метрик."""
    return {old.date: new.date, ifrs_q(old.period): ifrs_q(new.period), old.ytd: new.ytd, old.period: new.period}


def _page_key(page: str) -> tuple[int, str]:
    m = re.match(r"\s*(\d+)", page or "")
    return (int(m.group(1)) if m else 10 ** 6, page or "")


def template_rows(sheets: list[Sheet], labels: dict[str, str], what: dict[str, str]) -> list[dict]:
    """Строки шаблона по журналам чтений листов. Порядок листов — порядок поиска строки (остаток ищут в
    каноническом ряду, затем в листе якоря): строка шаблона идёт в тот лист, где она стояла."""
    def filled(r: dict | None) -> bool:
        return r is not None and r["value"] not in ("", None)

    found: dict[tuple[str, str], dict] = {}
    for sheet in sheets:
        for metric, period in sheet.seen:
            if period not in labels:
                continue
            prev = sheet.rows.get((metric, period))
            key = (metric, labels[period])
            if key in found and (found[key]["prev_value"] != "" or not filled(prev)):
                continue                       # строка уже найдена в листе, стоящем раньше
            cur = next((s.rows[key] for s in [sheet] + sheets if filled(s.rows.get(key))), None)
            row = dict.fromkeys(SHEET_COLUMNS, "")
            row.update(sheet=sheet.name, metric=metric, what=what.get(metric, ""), period=labels[period],
                       unit=((prev or cur or {}).get("unit") or ""), prev_period=period)
            if filled(prev):
                row.update(prev_value=prev["value"], prev_doc=prev["doc"], prev_page=str(prev["page"]),
                           prev_note=prev.get("note") or "")
            else:
                row["prev_note"] = "на прежнем якоре строки не было — необязательная (нет строки — ноль или не раскрыто)"
            if cur is not None:                # строка нового квартала уже внесена в листы
                row.update(value=cur["value"], doc=cur["doc"], page=str(cur["page"]), sha256=cur["sha256"],
                           note=cur.get("note") or "")
            found[key] = row
    order = {name: i for i, name in enumerate(SHEET_NAMES)}
    return sorted(found.values(), key=lambda r: (order[r["sheet"]], r["prev_doc"] == "", r["prev_doc"],
                                                 _page_key(r["prev_page"]), r["metric"]))


def sheet_template(c: Ctx) -> tuple[Anchor, list[dict]]:
    """Шаблон листа квартала, следующего за якорем сборки `c` (вызывается после сборки). Состав строк —
    по прочитанному на якоре: на годовом отчёте и на первом квартале года он другой (годовые раскрытия,
    новый год гайденса) — недостающую строку сборщик назовёт отказом."""
    new = Anchor(q_next(c.a.period))
    try:
        what = {r["metric"]: r["description_ru"] for r in c.csv_rows("ifrs/metrics.csv")}
    except (BuildError, KeyError):
        what = {}                              # словаря метрик нет — колонка what пустая
    return new, template_rows([c.H, c.A, c.Q, c.M], quarter_labels(c.a, new), what)


def write_sheet(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, SHEET_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def quarter_inputs(a: Anchor) -> list[tuple[str, str]]:
    """Входы квартала вне листа квартала (файл или каталог папки передачи; что в нём должно появиться):
    их готовят инструменты этапа 1, а не ручной ввод. Перечень — SCHEMA.md §3 и §5."""
    months = ", ".join(f"{a.year}-{m:02d}" for m in range(3 * a.quarter - 2, 3 * a.quarter + 1))
    report = "годовая отчётность" if a.quarter == 4 else "промежуточная отчётность"
    return [
        ("primary/ (+ MANIFEST-*.md)", f"документы отчёта: {report} МСФО за {a.ytd_ru}, презентация, пресс-релиз — файл, адрес, sha256"),
        ("stage1/mgmt/guidance.csv", f"гайденс {a.year} г. из презентации квартала (дата, слова, страница)"),
        ("stage1/calib/nii/data/nii_books_anchor_proposal.json", f"пересчёт калибровки книг ЧПД на {a.period} (calib_nii.py): разбиение процентов кредитных книг, льготная ипотека"),
        ("stage1/calib/capital/data/zcyc_quarter_ends.csv", f"бескупонная кривая ОФЗ на {d_ru(a.date)}"),
        ("stage1/ras/raw/ и листы cbr_f101_agg, cbr_f102_monthly, cbr_f123, cbr_f135, INDEX", f"формы ЦБ 0409101, 102, 123, 135 банка на {d_ru(a.form_date)}"),
        ("stage1/ras/ras_release_monthly.csv", f"релизы РСБУ за месяцы {months} (нарастающие итоги; источник каждой ячейки)"),
        ("research/facts/cbr_key_rate_changes.csv", f"изменения ключевой ставки по {d_ru(a.date)}"),
        ("stage1/market/ (ISS, дивиденды)", "новые снимки, если изменилось число акций; новое решение о дивидендах — строка листа dividends_public.csv (страница эмитента, новость о решении) и снимок брокерского календаря для сверки; дата снимков — параметр сборщика SNAPSHOT_RU"),
        ("data/facts/calendar.json, peers.json", "руками: прошедшие события — подтверждены, новые отчёты аналогов (SCHEMA.md §3)"),
    ]


def write(files: dict[str, dict], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        with open(target / f"{k}.json", "w", encoding="utf-8", newline="\n") as f:
            f.write(dumps(v))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Факты Сбербанка из листов этапа 1 (BANK_HANDOFF_DIR)")
    ap.add_argument("--check", action="store_true", help="сверить с data/facts, ничего не писать")
    ap.add_argument("--anchor", default=None, metavar="ПЕРИОД",
                    help="квартал якоря вида ГГГГQк; по умолчанию — data/facts/anchor.json")
    ap.add_argument("--expected", type=Path, default=None, metavar="ФАЙЛ",
                    help="файл отчёта следующего квартала с ожиданием модели (quarter-report/1, как у reanchor.py; "
                         "SCHEMA.md §4): факты нового якоря из data/facts и файла; нужен --out; папка передачи не нужна")
    ap.add_argument("--facts", type=Path, default=None, metavar="КАТАЛОГ",
                    help="с --expected: каталог фактов прежнего якоря (по умолчанию data/facts); с --report: "
                         "каталог готовых фактов, из которых пишется отчёт квартала")
    ap.add_argument("--report", type=Path, default=None, metavar="ФАЙЛ",
                    help="записать отчёт квартала якоря (quarter-report/1) — вход reanchor.py --facts: после сборки "
                         "из листов или, с --facts, из готового каталога фактов")
    ap.add_argument("--sheet", type=Path, action="append", default=[], metavar="ФАЙЛ",
                    help="черновик листа квартала вне папки передачи (SCHEMA.md §5): его строки дополняют листы "
                         "этапа 1; только с --out или --check (в канон лист кладут в папку передачи: "
                         f"{QUARTER_SHEETS}/<период>.csv)")
    ap.add_argument("--sheet-template", type=Path, default=None, metavar="ФАЙЛ",
                    help="записать шаблон листа квартала, следующего за якорем: строки листов этапа 1, которые "
                         "сборщик прочтёт, с местом той же строки в прошлом отчёте")
    ap.add_argument("--out", type=Path, default=None, help="каталог записи (по умолчанию data/facts)")
    a = ap.parse_args(argv)
    if a.expected is not None:
        return main_expected(a)
    if a.report is not None and a.facts is not None:
        return main_report(a)
    if a.sheet and not a.check and (a.out or FACTS).resolve() == FACTS.resolve():
        print(f"ПРОВАЛ: с черновиком листа квартала (--sheet) в data/facts не пишут — нужен --out; в канон лист кладут "
              f"в папку передачи ({QUARTER_SHEETS}/<период>.csv)", file=sys.stderr)
        return 1
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root or not Path(root).is_dir():
        print("ПРОВАЛ: не задана папка передачи (BANK_HANDOFF_DIR)", file=sys.stderr)
        return 1
    try:
        anchor = Anchor(a.anchor or default_anchor())
        files, ctx = build_ctx(Path(root), anchor, tuple(a.sheet))
    except BuildError as e:
        print(f"ПРОВАЛ сборки: {e}", file=sys.stderr)
        return 1
    checks = ctx.checks
    print(f"якорь {anchor.period} ({anchor.date})" + (f"; листы кварталов: {', '.join(ctx.sheet_files)}" if ctx.sheet_files else ""))
    bad = [ch for ch in checks if not ch[1]]
    for name, ok, detail in checks:
        print(f"{'ок  ' if ok else 'СБОЙ'} {name}: {detail}")
    if bad:
        print(f"ПРОВАЛ: не сошлось сверок — {len(bad)}", file=sys.stderr)
        return 1
    target = a.out or FACTS
    if a.check:
        diff = [k for k, v in files.items() if not (target / f"{k}.json").exists()
                or (target / f"{k}.json").read_text(encoding="utf-8") != dumps(v)]
        if diff:
            print(f"РАСХОЖДЕНИЕ с {'data/facts' if target == FACTS else target}: " + ", ".join(f"{k}.json" for k in diff),
                  file=sys.stderr)
            return 1
        print(f"ок: {len(files)} файлов совпали с {target}")
    else:
        write(files, target)
        print(f"записано: {len(files)} файлов в {target.name if target == FACTS else target}")
    if a.report is not None:
        write_report(files, a.report, source=f"факты сборщика на якоре {anchor.period} (листы этапа 1)")
    if a.sheet_template is not None:
        new, rows = sheet_template(ctx)
        write_sheet(rows, a.sheet_template)
        todo = sum(1 for r in rows if r["value"] == "")
        print(f"шаблон листа квартала {new.period}: {len(rows)} строк, не внесено {todo} — {a.sheet_template}")
        print(f"прочие входы квартала {new.period} (готовят инструменты этапа 1; SCHEMA.md §5):")
        for where, what in quarter_inputs(new):
            print(f"  {where}: {what}")
    return 0


# ------------------------------------------------------------------ ожидаемый отчёт (сухой прогон перезаякоривания)
#
# Факты следующего квартала из фактов якоря и файла отчёта квартала (MODEL §15.2). Формат файла —
# тот же, что читает и пишет ops/tools/reanchor.py (`quarter-report/1`, docs/REANCHOR.md): один файл
# служит обоим инструментам. Листы этапа 1 не читаются: прежние кварталы — как в фактах якоря, новый
# квартал — из отчёта. Модель ведёт книгу одной суммой, отчётность — строками: остаток книги делится
# на её строки баланса в долях прежнего якоря (сумма строк = книге точно).

REPORT_SCHEMA = "quarter-report/1"
BOOKS = ("corp_loans", "mortgage", "mortgage_sub", "retail_loans", "securities", "liquidity",
         "retail_current", "retail_term", "corp_funds", "wholesale")
ASSET_BOOKS, LOAN_BOOKS, LIABILITY_BOOKS = BOOKS[:6], BOOKS[:4], BOOKS[6:]
BOOK_LINES = {   # книга → строки balance.json, составляющие её остаток (model/book.py: BOOK_BALANCE_FIELDS)
    "corp_loans": ("loans_ac_gross.commercial", "loans_ac_gross.project", "loans_fvtpl"),
    "mortgage": ("loans_ac_gross.mortgage_market",),
    "mortgage_sub": ("loans_ac_gross.mortgage_subsidized",),
    "retail_loans": ("loans_ac_gross.consumer", "loans_ac_gross.cards", "loans_ac_gross.auto"),
    "securities": ("securities.fvoci_debt", "securities.fvoci_repo", "securities.ac", "securities.ac_repo",
                   "securities.fvtpl_bonds"),
    "liquidity": ("liquidity.cash", "liquidity.cbr", "liquidity.due_from_banks"),
    "retail_current": ("funds.retail_current",),
    "retail_term": ("funds.retail_term",),
    "corp_funds": ("funds.corp_current", "funds.corp_term"),
    "wholesale": ("wholesale.banks", "wholesale.issued_debt", "wholesale.subordinated"),
}
# строки отчёта, заданные в нём отдельно от остатка книги: строка баланса → поле раздела balance отчёта
REPORT_LINES = {"loans_fvtpl": "loans_fvtpl", "securities.fvtpl_bonds": "fvtpl_bonds"}
FVOCI_LINES = ("securities.fvoci_debt", "securities.fvoci_repo")      # вместе — поле balance.fvoci отчёта
AC_LOAN_LINES = ("commercial", "project", "mortgage_market", "mortgage_subsidized", "consumer", "cards", "auto")
PNL_LINES = {   # обязательная строка ОПУ отчёта (знаки как в МСФО) → что это
    "nii": "чистые процентные доходы", "fees_net": "чистые комиссионные доходы",
    "llp_debt_fa": "резервы под кредитные убытки по долговым финансовым активам (расход — со знаком минус)",
    "insurance_net": "страхование (выручка + расходы на услуги)",
    "noncore_net": "непрофильная деятельность", "opex": "операционные расходы (со знаком минус)",
    "pbt": "прибыль до налога", "tax": "налог на прибыль (со знаком минус)", "ni": "чистая прибыль",
    "ni_shareholders": "чистая прибыль акционерам",
}
PNL_OPTIONAL = {"misc_net": "«прочее» — остаток до прибыли до налога (с переоценкой облигаций и кредитов по СС)",
                "oci_fvoci": "прочий совокупный доход по FVOCI", "other_equity_movements": "прочие движения капитала"}
PNL_SUM = ("nii", "llp_debt_fa", "fees_net", "insurance_net", "noncore_net", "opex", "misc_net")
CURVE_NODES = ("1", "3", "5", "10")
REPORT_TOP = (("period", "balance", "interest", "dia", "pnl", "capital", "market", "mgmt"),
              ("schema", "as_of", "published", "source", "note", "shares"))
REPORT_SECTIONS = {   # раздел → (обязательные поля, необязательные) — тот же перечень, что в reanchor.py
    "balance": (("books", "loans_fvtpl", "fvoci", "fvtpl_bonds", "allowance", "other_assets", "other_liabilities",
                 "dividends_payable", "bv_common", "fvoci_reserve"), ("dividends_payable_year", "at1", "nci")),
    "pnl": (tuple(PNL_LINES), tuple(PNL_OPTIONAL)),
    "capital": (("n20_0", "n20_pre_dividend", "n1_1_bank", "basel_cet1", "basel_rwa", "bank_base_capital"), ("t2",)),
    "market": (("ofz_curve", "key_avg"), ("price_index",)),
    "mgmt": (("nim", "cor"), ("cir",)),
    "shares": ((), ("issued_total", "issued_ordinary", "issued_preferred", "outstanding_total",
                    "outstanding_ordinary", "outstanding_preferred")),
}
MGMT_RU = {"nim": "ЧПМ", "cor": "стоимость риска", "cir": "CIR"}


def dig(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def put(obj: dict, dotted: str, value) -> None:
    *head, last = dotted.split(".")
    for part in head:
        obj = obj[part]
    obj[last] = value


def check_report_shape(report: dict) -> None:
    """Состав файла отчёта: незнакомый или недостающий ключ — отказ (опечатка не должна пройти молча)."""
    if not isinstance(report, dict):
        raise BuildError("отчёт квартала: ожидается словарь JSON")
    if report.get("schema", REPORT_SCHEMA) != REPORT_SCHEMA:
        raise BuildError(f"отчёт квартала: schema {report.get('schema')!r}, нужна {REPORT_SCHEMA!r} (docs/REANCHOR.md)")
    required, optional = REPORT_TOP
    problems = [f"нет {k}" for k in required if k not in report]
    problems += [f"незнакомый ключ {k}" for k in report if k not in required + optional]
    for name, (req, opt) in REPORT_SECTIONS.items():
        section = report.get(name)
        if section is None:
            continue
        if not isinstance(section, dict):
            problems.append(f"{name} — не словарь")
            continue
        problems += [f"нет {name}.{k}" for k in req if k not in section]
        problems += [f"незнакомый ключ {name}.{k}" for k in section if k not in req + opt]
    for where, block in (("balance.books", (report.get("balance") or {}).get("books")), ("interest", report.get("interest"))):
        if isinstance(block, dict):
            problems += [f"нет {where}.{b}" for b in BOOKS if b not in block]
            problems += [f"незнакомая книга {where}.{b}" for b in block if b not in BOOKS]
        elif block is not None:
            problems.append(f"{where} — не словарь по книгам")
    curve = (report.get("market") or {}).get("ofz_curve")
    if isinstance(curve, dict):
        problems += [f"нет market.ofz_curve.{t}" for t in CURVE_NODES if t not in curve]
    if problems:
        raise BuildError("отчёт квартала: " + "; ".join(problems))
    if not isinstance(report["capital"]["n20_pre_dividend"], bool):
        raise BuildError("отчёт квартала: capital.n20_pre_dividend — true или false")


def carry_scale(interest: dict[str, float], nii: float, dia: float) -> tuple[float, float]:
    """(остаток ЧПД вне книг, множитель ставок книг активов). Ставки якоря несут весь ЧПД: остаток
    `nii − (Σ процентов активных книг − Σ пассивных − взносы АСВ)` разнесён по книгам активов
    пропорционально их процентному доходу (MODEL §15.2 п. 4; то же правило — в reanchor.py)."""
    income = sum(interest[b] for b in ASSET_BOOKS)
    residual = nii - (income - sum(interest[b] for b in LIABILITY_BOOKS) - dia)
    return residual, 1.0 + residual / income


def expected_facts(prev: dict[str, dict], report: dict, *, name: str, sha: str) -> tuple[dict[str, dict], list]:
    """Факты нового якоря (11 файлов сборщика) из фактов прежнего якоря `prev` и отчёта следующего
    квартала `report` (ожидание модели). Возвращает (файлы, сверки). Чистая функция: вход не меняет."""
    checks: list[tuple[str, bool, str]] = []
    check_report_shape(report)
    old, new = Anchor(str(prev["anchor"]["period"])), Anchor(str(report["period"]))
    if new.period != q_next(old.period):
        raise BuildError(f"отчёт квартала — за {new.period}, факты стоят на {old.period}: нужен отчёт следующего "
                         f"квартала ({q_next(old.period)})")
    if "as_of" in report and str(report["as_of"]) != new.date:
        raise BuildError(f"отчёт квартала: as_of {report['as_of']} — не конец квартала {new.period} ({new.date})")

    def has(path: str) -> bool:
        try:
            return dig(report, path) is not None
        except KeyError:
            return False

    def num(path: str) -> float:
        x = dig(report, path)
        if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or abs(x) == float("inf"):
            raise BuildError(f"отчёт квартала: {path} — не число ({x!r})")
        return float(x)

    def ref(path: str) -> str:
        return f"{name}, {path}, sha256 {sha[:SHORT]}"

    def en(path: str, what: str, *, value: float | None = None, **extra) -> dict:
        """Узел из отчёта: число — ожидание модели, не отчётность."""
        return node(r6(num(path) if value is None else value), src=ref(path), calc=f"ожидание модели: {what}", **extra)

    def calc(v: float, how: str, **extra) -> dict:
        return node(r6(v), calc=f"ожидаемый отчёт: {how}", **extra)

    def kept(n: dict, **extra) -> dict:
        """Величина, которую модель не двигает: значение прежнего якоря."""
        return node(n["v"], calc=f"ожидаемый отчёт: как на {d_ru(old.date)} (модель величину не двигает)", **extra)

    def none(why: str, **extra) -> dict:
        return node(None, calc=f"нет данных: ожидаемый отчёт строку не несёт — {why}", **extra)

    out = {k: copy.deepcopy(prev[k]) for k in BUILT}
    d, days = new.date, q_days(new.period)
    on, kq = d_ru(d), q_ru(new.period)

    # ---------------- balance.json
    pb, bal = prev["balance"], out["balance"]
    bal["as_of"] = d
    book = {b: num(f"balance.books.{b}") for b in BOOKS}
    for b, paths in BOOK_LINES.items():
        given = {p: num(f"balance.{REPORT_LINES[p]}") for p in paths if p in REPORT_LINES}
        if b == "securities":
            fo = [float(dig(pb, p)["v"]) for p in FVOCI_LINES]
            fvoci = num("balance.fvoci")
            given[FVOCI_LINES[0]] = r6(fvoci * fo[0] / sum(fo))
            given[FVOCI_LINES[1]] = r6(fvoci - given[FVOCI_LINES[0]])
        free = [p for p in paths if p not in given]
        olds = {p: float(dig(pb, p)["v"]) for p in paths}
        rest, base = book[b] - sum(given.values()), sum(olds[p] for p in free)
        if rest < -0.05:
            raise BuildError(f"отчёт квартала: строки книги {b}, заданные отдельно, больше её остатка")
        vals = {p: r6(rest * olds[p] / base) for p in free[:-1]}
        if free:
            vals[free[-1]] = r6(rest - sum(vals.values()))
        for p in paths:
            if len(paths) == 1:
                put(bal, p, en(f"balance.books.{b}", f"остаток книги {b} на {on}"))
            elif p in REPORT_LINES:
                put(bal, p, en(f"balance.{REPORT_LINES[p]}", f"строка книги {b} на {on}, заданная отчётом"))
            elif p in FVOCI_LINES and b == "securities":
                put(bal, p, node(given[p], src=ref("balance.fvoci"),
                                 calc=(f"ожидание модели: долговые бумаги FVOCI с репо {ru(num('balance.fvoci'))} × доля "
                                       f"строки на {d_ru(old.date)} ({ru(olds[p])} из {ru(sum(olds[x] for x in FVOCI_LINES))})")))
            else:
                put(bal, p, node(vals[p], src=ref(f"balance.books.{b}"),
                                 calc=(f"ожидание модели: остаток книги {b} {ru(book[b])} за вычетом строк, заданных отчётом, "
                                       f"× доля строки на {d_ru(old.date)} ({ru(olds[p])} из {ru(base)}) — модель ведёт книгу "
                                       f"одной суммой")))
    L = bal["loans_ac_gross"]
    L["mortgage"] = calc(L["mortgage_market"]["v"] + L["mortgage_subsidized"]["v"], "mortgage_market + mortgage_subsidized")
    loans_ac = sum(L[k]["v"] for k in AC_LOAN_LINES)
    L["total"] = calc(loans_ac, "Σ продуктов (кредиты АС, валовые)")
    bal["allowance_ac"] = en("balance.allowance", f"резерв под ОКУ по кредитам АС на {on} (величиной)")
    sec = bal["securities"]
    sec_debt = sum(dig(bal, p)["v"] for p in BOOK_LINES["securities"])
    sec["debt_total"] = calc(sec_debt, "fvoci_debt + fvoci_repo + ac + ac_repo + fvtpl_bonds")
    for k in ("fvtpl_equity", "fvoci_equity"):
        sec[k] = none("акции и фонды модель не ведёт (внутри other_assets)")
    liq_sum = sum(dig(bal, p)["v"] for p in BOOK_LINES["liquidity"])
    bal["liquidity"]["total"] = calc(liq_sum, "cash + cbr + due_from_banks")
    bal["other_assets"] = en("balance.other_assets", f"прочие активы на {on}")
    fv, al, oa = bal["loans_fvtpl"]["v"], bal["allowance_ac"]["v"], bal["other_assets"]["v"]
    assets = loans_ac + fv - al + sec_debt + liq_sum + oa
    bal["total_assets"] = calc(assets, "кредиты АС валовые − резерв + кредиты по СС + долговые бумаги + ликвидность + прочие активы")
    funds = sum(dig(bal, p)["v"] for b in ("retail_current", "retail_term", "corp_funds") for p in BOOK_LINES[b])
    wh = sum(dig(bal, p)["v"] for p in BOOK_LINES["wholesale"])
    bal["other_liabilities"] = en("balance.other_liabilities", f"прочие обязательства на {on} (без дивидендов к выплате)")
    bal["dividends_payable"] = en("balance.dividends_payable", f"дивиденды к выплате на {on}")
    if has("balance.dividends_payable_year"):
        bal["dividends_payable_year"] = int(dig(report, "balance.dividends_payable_year"))
    ol, dp = bal["other_liabilities"]["v"], bal["dividends_payable"]["v"]
    liab = funds + wh + ol + dp
    bal["total_liabilities"] = calc(liab, "средства клиентов + опт + прочие обязательства + дивиденды к выплате")
    eq, peq = bal["equity"], pb["equity"]
    for k in ("at1", "nci"):
        eq[k] = en(f"balance.{k}", f"{k} на {on}") if has(f"balance.{k}") else kept(peq[k])
    eq["bv_common"] = en("balance.bv_common", f"капитал обыкновенных акционеров на {on}")
    bv, at1, nci = eq["bv_common"]["v"], eq["at1"]["v"], eq["nci"]["v"]
    eq["total"] = calc(bv + at1 + nci, "bv_common + at1 + nci")
    eq["shareholders"] = calc(bv + at1, "bv_common + at1")
    for k in ("share_capital", "share_premium"):
        eq[k] = kept(peq[k])
    for k in ("treasury_cost", "other_reserves", "retained_earnings"):
        eq[k] = none("компонент капитала модель отдельно не ведёт")
    eq["fvoci_reserve"] = en("balance.fvoci_reserve", f"фонд переоценки FVOCI после налога на {on}")
    checks.append(("отчёт квартала: активы = обязательства + капитал", abs(assets - (liab + eq["total"]["v"])) < 0.05,
                   f"активы {ru(assets)}, обязательства + капитал {ru(liab + eq['total']['v'])}"))
    if new.year != old.year:                # новый год якоря: база гайденса роста — концы прежнего якоря
        pl = pb["loans_ac_gross"]
        bal["prev_year_end"] = {
            "as_of": old.date,
            "loans_corporate": node(r6(pl["commercial"]["v"] + pl["project"]["v"] + pb["loans_fvtpl"]["v"]),
                                    calc=f"balance.json фактов {old.period}: коммерческие + проектные + кредиты по СС"),
            "loans_retail": node(r6(sum(pl[k]["v"] for k in ("mortgage", "consumer", "cards", "auto"))),
                                 calc=f"balance.json фактов {old.period}: ипотека + потребительские + карты + авто"),
        }
    iea = loans_ac + fv + sec_debt + liq_sum
    bal["iea"] = calc(iea, "loans_ac_gross.total + loans_fvtpl + securities.debt_total + liquidity.total (процентные активы движка)")
    bal["history"][new.period] = {
        "date": d, "loans_ac_gross": calc(loans_ac, "balance.loans_ac_gross.total"),
        "allowance_ac": calc(al, "balance.allowance_ac"), "loans_fvtpl": calc(fv, "balance.loans_fvtpl"),
        "equity_shareholders": calc(bv + at1, "balance.equity.shareholders"), "bv_common": calc(bv, "balance.equity.bv_common"),
        "total_assets": calc(assets, "balance.total_assets"), "iea": calc(iea, "balance.iea"),
    }

    # ---------------- nii_books.json: ставки якоря несут весь ЧПД
    pn, nb = prev["nii_books"], out["nii_books"]
    nb.update(as_of=d, period=new.period, days=node(days, calc=f"дней в квартале {new.period} (act/365)"),
              rates_carry_residual=True)
    interest = {b: num(f"interest.{b}") for b in BOOKS}
    nii, dia = num("pnl.nii"), abs(num("dia"))
    residual, scale = carry_scale(interest, nii, dia)
    checks.append(("отчёт квартала: проценты книг дают ЧПД отчёта (остаток вне книг — меньше 10 % ЧПД)",
                   abs(residual) < 0.10 * abs(nii), f"Σ книг − АСВ {ru(nii - residual)}, ЧПД {ru(nii)}, остаток {ru(residual)}"))
    for b in BOOKS:
        bo, bc = float(pn["books"][b]["balance_close"]["v"]), r6(book[b])
        avg = (bo + bc) / 2
        carried = interest[b] * (scale if b in ASSET_BOOKS else 1.0)
        how = (f"interest_q × (1 + остаток ЧПД {ru(residual)} / Σ процентов активных книг "
               f"{ru(sum(interest[x] for x in ASSET_BOOKS))}) / balance_avg × 365/{days} — ставка книги активов несёт долю "
               f"процентного дохода вне книг (MODEL §15.2 п. 4)") if b in ASSET_BOOKS else f"interest_q / balance_avg × 365/{days}"
        nb["books"][b] = {
            "balance_open": node(bo, calc=f"balance_close книги в фактах {old.period} (остаток на {d_ru(old.date)})"),
            "balance_close": node(bc, calc=f"balance.json на {d} (состав книги — MODEL §4.1)"),
            "balance_avg": node(r6(avg), calc="(balance_open + balance_close) / 2"),
            "interest_q": en(f"interest.{b}", f"проценты книги {b} за {kq} (величиной)"),
            "rate_anchor": node(r6(carried / avg * 365 / days), src=ref(f"interest.{b}"), calc=f"ожидание модели: {how}"),
        }
    iea_avg = sum(nb["books"][b]["balance_avg"]["v"] for b in ASSET_BOOKS)
    fr_avg = nb["books"]["retail_current"]["balance_avg"]["v"] + nb["books"]["retail_term"]["balance_avg"]["v"]
    rc, rt = bal["funds"]["retail_current"]["v"], bal["funds"]["retail_term"]["v"]
    nb.update(
        interest_loans_q=calc(sum(interest[b] for b in LOAN_BOOKS), "Σ interest_q кредитных книг"),
        other_interest_net_q=calc(residual, ("остаток: nii_q − (Σ процентов активных книг − Σ пассивных − dia_q); разнесён по "
                                             "ставкам книг активов (rates_carry_residual)")),
        dia_q=en("dia", f"взносы в АСВ за {kq} (величиной)", value=dia),
        dia_rate=calc(dia / fr_avg * 365 / days, f"dia_q / средние средства ФЛ × 365/{days}"),
        nii_q=en("pnl.nii", f"ЧПД за {kq}"),
        iea_avg=calc(iea_avg, "Σ balance_avg активных книг (кредиты, бумаги, ликвидность)"),
        nim_eng_q=calc(nii * 365 / days / iea_avg, f"nii_q × 365/{days} / iea_avg (ЧПМ базиса движка)"),
        current_share_anchor=calc(rc / (rc + rt), "balance.funds.retail_current / (retail_current + retail_term) на якоре"),
        key_avg_anchor_q=en("market.key_avg", f"средняя ключевая ставка {new.period} по календарным дням"),
    )

    # ---------------- pnl_quarterly.json
    pq = out["pnl_quarterly"]
    pq["as_of"] = d
    last_row = prev["pnl_quarterly"]["quarters"][old.period]
    row = {k: none("строку ОПУ модель отдельно не ведёт") for k in last_row}
    for k, what in PNL_LINES.items():
        row[k] = en(f"pnl.{k}", f"{what}, {kq}")
    for k, what in PNL_OPTIONAL.items():
        if has(f"pnl.{k}"):
            row[k] = en(f"pnl.{k}", f"{what}, {kq}")
    if not has("pnl.misc_net"):
        row["misc_net"] = calc(row["pbt"]["v"] - sum(row[k]["v"] for k in PNL_SUM if k != "misc_net"),
                               "pbt − (nii + llp_debt_fa + fees_net + insurance_net + noncore_net + opex)")
    pnl = {k: row[k]["v"] for k in tuple(PNL_LINES) + ("misc_net",)}
    row["ni_nci"] = calc(pnl["ni"] - pnl["ni_shareholders"], "ni − ni_shareholders")
    row["dia"] = calc(-dia, "−dia_q: взносы АСВ со знаком отчёта (внутри ЧПД)")
    cap0 = prev["capital"]
    cq = int(cap0["at1"]["coupon_quarter"]["v"])
    row["at1_coupon"] = node(0.0, calc=f"купон AT1 выплачивается в квартале {cq}; в квартале — 0 (MODEL §4.9)") if new.quarter != cq \
        else calc(cap0["at1"]["coupon_annual"]["v"], "capital.at1.coupon_annual — купон года, величиной")
    row["key_avg"] = en("market.key_avg", f"средняя ключевая ставка {new.period} по календарным дням (доля; флаг ni_jump, MODEL §14.3)")
    pq["quarters"][new.period] = row
    s = sum(pnl[k] for k in PNL_SUM)
    checks.append((f"отчёт квартала: PBT {new.period} = сумма строк ОПУ", abs(s - pnl["pbt"]) < 0.05,
                   f"Σ строк {ru(s, 2)} против {ru(pnl['pbt'], 2)}"))
    checks.append(("отчёт квартала: PBT + налог = ЧП; расходы — со знаком минус",
                   abs(pnl["pbt"] + pnl["tax"] - pnl["ni"]) < 0.05 and pnl["opex"] < 0,
                   f"PBT {ru(pnl['pbt'])}, налог {ru(pnl['tax'])}, ЧП {ru(pnl['ni'])}, расходы {ru(pnl['opex'])}"))

    # ---------------- capital.json
    cap = out["capital"]
    cap["as_of"] = d
    cap["at1"]["amount"] = dict(eq["at1"])
    t2 = en("capital.t2", f"капитал 2-го уровня на {on}", basis=cap0["t2_recognized"]["basis"]) \
        if has("capital.t2") else kept(cap0["t2_recognized"], basis=cap0["t2_recognized"]["basis"])
    cap["t2_recognized"] = t2
    cet1, rwa = num("capital.basel_cet1"), num("capital.basel_rwa")
    t1 = cet1 + at1
    cap["basel"] = {
        "basis": cap0["basel"]["basis"], "as_of": d,
        "cet1_ratio": calc(cet1 / rwa, "cet1 / rwa"),
        "cet1": en("capital.basel_cet1", f"базовый капитал по Базелю на {on} (= BV − вычеты якоря модели)"),
        "t1_ratio": calc(t1 / rwa, "t1 / rwa"), "t1": calc(t1, "cet1 + at1.amount"),
        "t2": calc(t2["v"], "t2_recognized"),
        "total_ratio": calc((t1 + t2["v"]) / rwa, "total_capital / rwa"),
        "total_capital": calc(t1 + t2["v"], "t1 + t2"),
        "rwa": en("capital.basel_rwa", f"активы, взвешенные по риску, на {on}"),
    }
    pre = bool(dig(report, "capital.n20_pre_dividend"))
    cap["n20_0"] = {
        "basis": cap0["n20_0"]["basis"],
        "value": en("capital.n20_0", f"Н20.0 на {on}"),
        "as_of": d, "pre_dividend": pre,
        "pre_dividend_note": (f"Н20.0 на {on} — " + ("до вычета объявленного дивиденда из регуляторного капитала" if pre
                                                     else "объявленных и не вычтенных из капитала дивидендов нет")),
        "min_with_buffers": kept(cap0["n20_0"]["min_with_buffers"]),
    }
    cap["n1_1_bank"] = {"basis": "RAS", "value": en("capital.n1_1_bank", f"Н1.1 банка на {on} (прокси Н20.1 группы, MODEL §3.3)"),
                        "as_of": new.form_date, "ifrs_note_value": none("примечание МСФО — только в отчётности")}
    for k in ("n1_0_bank", "n1_2_bank", "bank_own_funds"):
        cap[k] = none("норматив и капитал банка сверх Н1.1 и базового капитала модель не ведёт", basis="RAS", as_of=new.form_date)
    cap["bank_base_capital"] = en("capital.bank_base_capital", f"базовый капитал банка на {on} (для вычетов Н1.1 якоря)",
                                  basis="RAS", as_of=new.form_date)
    cap["fvoci_reserve"] = en("balance.fvoci_reserve", "после налога; = balance.equity.fvoci_reserve", basis="IFRS")
    cap["ofz_curve_anchor"] = {"basis": "ожидание модели (кривая миров слоя «свой взгляд»)", "as_of": d,
                               "convention": cap0["ofz_curve_anchor"]["convention"]}
    for t in CURVE_NODES:
        cap["ofz_curve_anchor"][t] = en(f"market.ofz_curve.{t}", f"бескупонная доходность ОФЗ {t} г. на {on}")
    hb = {k: v["basis"] for k, v in cap0["history"][-1].items() if isinstance(v, dict) and "basis" in v}
    cap["history"].append({
        "period": new.period, "date": d,
        "n20_0": calc(cap["n20_0"]["value"]["v"], "capital.n20_0.value", basis=hb["n20_0"]),
        "cet1_ratio": calc(cet1 / rwa, "capital.basel.cet1_ratio", basis=hb["cet1_ratio"]),
        "t1_ratio": calc(t1 / rwa, "capital.basel.t1_ratio", basis=hb["t1_ratio"]),
        "total_ratio": calc((t1 + t2["v"]) / rwa, "capital.basel.total_ratio", basis=hb["total_ratio"]),
        "cet1": calc(cet1, "capital.basel.cet1", basis=hb["cet1"]), "rwa": calc(rwa, "capital.basel.rwa", basis=hb["rwa"]),
        "n1_1_bank": calc(cap["n1_1_bank"]["value"]["v"], "capital.n1_1_bank.value", basis=hb["n1_1_bank"]),
    })
    checks.append(("отчёт квартала: вычеты Ded20 = BV − CET1 положительны и меньше капитала", 0 < bv - cet1 < bv, ru(bv - cet1)))

    # ---------------- shares.json, dividends.json, bridge_ras_ifrs.json: состав прежний
    for k in ("shares", "dividends", "bridge_ras_ifrs"):
        out[k]["as_of"] = d
    for k in REPORT_SECTIONS["shares"][1]:
        if has(f"shares.{k}"):
            out["shares"][k] = en(f"shares.{k}", f"число акций на {on}, млн шт.")
    out["bridge_ras_ifrs"]["note"] = (f"ожидаемый отчёт {new.period}: квартал РСБУ якоря не собран — мосты РСБУ → МСФО стоят по "
                                      f"{old.period} (ядро их не читает)")

    # ---------------- bridge_mgmt_ifrs.json и mgmt_quarterly.json
    br, hist = out["bridge_mgmt_ifrs"], bal["history"]
    br["as_of"] = d
    window = [br["window"][0], new.period]
    br["window"] = window
    g0, g1 = hist[old.period]["loans_ac_gross"]["v"], hist[new.period]["loans_ac_gross"]["v"]
    i0 = sum(float(pn["books"][b]["balance_close"]["v"]) for b in ASSET_BOOKS)
    income = sum(pnl[k] for k in CIR_INCOME)
    engine = {
        "cor": (-pnl["llp_debt_fa"] / ((g0 + g1) / 2) * 365 / days,
                f"−llp_debt_fa {ru(pnl['llp_debt_fa'])} / средние валовые кредиты АС ({ru(g0)} + {ru(g1)})/2 × 365/{days}"),
        "nim": (nii * 365 / days / ((i0 + iea) / 2), f"nii {ru(nii)} × 365/{days} / средние процентные активы ({ru(i0)} + {ru(iea)})/2"),
        "cir": (-pnl["opex"] / income, f"−opex {ru(pnl['opex'])} / (nii + fees_net + insurance_net + misc_net) {ru(income)} (pnl_quarterly {new.period})"),
    }
    mgmt_nodes = {}
    for x, (eng, how) in engine.items():
        value0 = prev["bridge_mgmt_ifrs"][x]["value"]["v"]
        if has(f"mgmt.{x}"):
            mg = en(f"mgmt.{x}", f"упр. {MGMT_RU[x]} за {kq}")
        else:
            mg = calc(eng - value0, f"упр. базис через мост: engine − bridge_mgmt_ifrs.{x}.value фактов {old.period} ({ru(value0 * 100, 3)} п.п.)")
        mgmt_nodes[x] = mg
        br[x]["history"].append({"period": new.period, "mgmt": dict(mg), "engine": node(r6(eng), calc=how),
                                 "gap": node(r6(eng - mg["v"]), calc="engine − mgmt")})
        gaps = [h["gap"]["v"] for h in br[x]["history"] if window[0] <= h["period"] <= window[1]]
        if None in gaps or len(gaps) != len(q_range(*window)):
            raise BuildError(f"мост {x}: в окне {window[0]}–{window[1]} не хватает кварталов")
        br[x]["value"] = node(r6(statistics.fmean(gaps)), calc=f"среднее gap по окну {window[0]}–{window[1]} (n = {len(gaps)})")
        br[x]["sd"] = node(r6(statistics.stdev(gaps)), calc="выборочное стандартное отклонение gap по окну")
        br[x]["n"] = node(len(gaps), calc="кварталов в окне")
        checks.append((f"отчёт квартала: мост {MGMT_RU[x]} упр. ↔ движок сдвинулся меньше чем на 0,1 п.п.",
                       abs(br[x]["value"]["v"] - value0) < 0.001,
                       f"{ru(value0 * 100, 3)} → {ru(br[x]['value']['v'] * 100, 3)} п.п."))
    mq = out["mgmt_quarterly"]
    mq["as_of"] = d
    mrow = {k: none("управленческую метрику модель не ведёт") for k in prev["mgmt_quarterly"]["quarters"][old.period]}
    for x in ("nim", "cor", "cir"):
        mrow[x] = dict(mgmt_nodes[x])
    mrow["n20_0"] = calc(cap["n20_0"]["value"]["v"], "capital.n20_0.value")
    mq["quarters"][new.period] = mrow

    # ---------------- guidance.json: год гайденса обязан совпасть с годом якоря
    if int(out["guidance"]["year"]) != new.year:
        raise BuildError(f"гайденс в фактах — на {out['guidance']['year']} г., якорь — {new.period}: гайденс нового года "
                         f"берётся из листов этапа 1 (обычная сборка с --anchor), режим --expected его не строит")

    # ---------------- anchor.json: отчёт — документ реестра
    an = out["anchor"]
    an.update(as_of=d, period=new.period,
              note=(f"ОЖИДАЕМЫЙ якорь (сухой прогон перезаякоривания, MODEL §15.2): числа {new.period} — ожидание модели из "
                    f"файла «{name}», не отчётность; кварталы по {old.period} — факты. В data/facts не кладётся"),
              expected={"period": new.period, "from_anchor": old.period, "report": name, "sha256": sha,
                        "source": "; ".join(str(report[k]) for k in ("source", "note") if report.get(k))})
    an["documents"] = sorted(an["documents"] + [{
        "key": f"expected_report_{new.period}", "file": None, "sha256": sha, "url": None,
        "title": f"Отчёт квартала {new.period} — ожидание модели (сухой прогон перезаякоривания; не отчётность)",
    }], key=lambda r: (r["key"], r["sha256"]))
    return {k: out[k] for k in BUILT}, checks


def report_from_facts(files: dict[str, dict], *, source: str) -> dict:
    """Файл отчёта квартала (`quarter-report/1`) из фактов якоря — вход `reanchor.py --facts` на
    настоящем отчёте: строки баланса сворачиваются в книги, проценты книг — как в `nii_books`
    (без доли остатка: её разносит правило ставок), ОПУ — строки квартала якоря."""
    bal, nb, cap = files["balance"], files["nii_books"], files["capital"]
    period = str(files["anchor"]["period"])

    def need(n: dict, where: str) -> float:
        if not isinstance(n, dict) or n.get("v") is None:
            raise BuildError(f"отчёт квартала из фактов: нет числа {where}")
        return float(n["v"])

    books = {b: r6(sum(need(dig(bal, p), f"balance.{p}") for p in paths)) for b, paths in BOOK_LINES.items()}
    row = files["pnl_quarterly"]["quarters"][period]
    pnl = {k: need(row[k], f"pnl_quarterly.{period}.{k}") for k in tuple(PNL_LINES) + ("misc_net",)}
    for k in ("oci_fvoci", "other_equity_movements"):
        if row.get(k, {}).get("v") is not None:
            pnl[k] = float(row[k]["v"])
    mrow = files["mgmt_quarterly"]["quarters"][period]
    mgmt = {x: need(mrow[x], f"mgmt_quarterly.{period}.{x}") for x in ("nim", "cor")}
    if mrow.get("cir", {}).get("v") is not None:
        mgmt["cir"] = float(mrow["cir"]["v"])
    sh = files["shares"]
    balance = {
        "books": books, "loans_fvtpl": need(bal["loans_fvtpl"], "balance.loans_fvtpl"),
        "fvoci": r6(sum(need(dig(bal, p), f"balance.{p}") for p in FVOCI_LINES)),
        "fvtpl_bonds": need(bal["securities"]["fvtpl_bonds"], "balance.securities.fvtpl_bonds"),
        "allowance": need(bal["allowance_ac"], "balance.allowance_ac"),
        "other_assets": need(bal["other_assets"], "balance.other_assets"),
        "other_liabilities": need(bal["other_liabilities"], "balance.other_liabilities"),
        "dividends_payable": need(bal["dividends_payable"], "balance.dividends_payable"),
        "bv_common": need(bal["equity"]["bv_common"], "balance.equity.bv_common"),
        "fvoci_reserve": need(cap["fvoci_reserve"], "capital.fvoci_reserve"),
        "at1": need(bal["equity"]["at1"], "balance.equity.at1"), "nci": need(bal["equity"]["nci"], "balance.equity.nci"),
    }
    if bal.get("dividends_payable_year") is not None:
        balance["dividends_payable_year"] = int(bal["dividends_payable_year"])
    return {
        "schema": REPORT_SCHEMA, "period": period, "as_of": str(files["anchor"]["as_of"]), "source": source,
        "balance": balance,
        "interest": {b: need(nb["books"][b]["interest_q"], f"nii_books.books.{b}.interest_q") for b in BOOKS},
        "dia": need(nb["dia_q"], "nii_books.dia_q"),
        "pnl": pnl,
        "capital": {"n20_0": need(cap["n20_0"]["value"], "capital.n20_0.value"),
                    "n20_pre_dividend": bool(cap["n20_0"]["pre_dividend"]),
                    "n1_1_bank": need(cap["n1_1_bank"]["value"], "capital.n1_1_bank.value"),
                    "basel_cet1": need(cap["basel"]["cet1"], "capital.basel.cet1"),
                    "basel_rwa": need(cap["basel"]["rwa"], "capital.basel.rwa"),
                    "bank_base_capital": need(cap["bank_base_capital"], "capital.bank_base_capital"),
                    "t2": need(cap["t2_recognized"], "capital.t2_recognized")},
        "market": {"ofz_curve": {t: need(cap["ofz_curve_anchor"][t], f"capital.ofz_curve_anchor.{t}") for t in CURVE_NODES},
                   "key_avg": need(nb["key_avg_anchor_q"], "nii_books.key_avg_anchor_q")},
        "mgmt": mgmt,
        "shares": {k: need(sh[k], f"shares.{k}") for k in REPORT_SECTIONS["shares"][1]},
    }


def write_report(files: dict[str, dict], path: Path, *, source: str) -> None:
    report = report_from_facts(files, source=source)
    check_report_shape(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(report))
    print(f"записан отчёт квартала {report['period']} ({REPORT_SCHEMA}): {path}")


def main_report(a) -> int:
    """Режим --report с --facts: отчёт квартала из готового каталога фактов (папка передачи не нужна)."""
    try:
        files = {k: json.loads((a.facts / f"{k}.json").read_text(encoding="utf-8")) for k in BUILT}
        if a.anchor and a.anchor != files["anchor"]["period"]:
            raise BuildError(f"--anchor {a.anchor}, а факты в {a.facts} — на {files['anchor']['period']}")
        write_report(files, a.report, source=f"факты сборщика на якоре {files['anchor']['period']} ({a.facts.name})")
    except (OSError, ValueError, KeyError) as e:
        print(f"ПРОВАЛ: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except BuildError as e:
        print(f"ПРОВАЛ сборки: {e}", file=sys.stderr)
        return 1
    return 0


def main_expected(a) -> int:
    """Режим --expected: факты следующего квартала из фактов якоря и отчёта квартала; только в --out."""
    src = a.facts or FACTS
    if a.out is None or a.out.resolve() == FACTS.resolve():
        print("ПРОВАЛ: --expected пишет только в --out, и это не data/facts (ожидание — не факт)", file=sys.stderr)
        return 1
    try:
        raw = a.expected.read_bytes()
        report = json.loads(raw.decode("utf-8"))
        prev = {k: json.loads((src / f"{k}.json").read_text(encoding="utf-8")) for k in BUILT}
        if a.anchor and isinstance(report, dict) and a.anchor != report.get("period"):
            raise BuildError(f"--anchor {a.anchor}, а отчёт квартала — за {report.get('period')}")
        files, checks = expected_facts(prev, report, name=a.expected.name, sha=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, KeyError) as e:
        print(f"ПРОВАЛ: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except BuildError as e:
        print(f"ПРОВАЛ сборки: {e}", file=sys.stderr)
        return 1
    print(f"ожидаемый якорь {files['anchor']['period']} ({files['anchor']['as_of']}) из фактов {prev['anchor']['period']} и {a.expected.name}")
    for name, ok, detail in checks:
        print(f"{'ок  ' if ok else 'СБОЙ'} {name}: {detail}")
    if any(not ok for _, ok, _ in checks):
        print("ПРОВАЛ: отчёт квартала не сошёлся", file=sys.stderr)
        return 1
    manual = {k: (src / f"{k}.json").read_text(encoding="utf-8") for k in MANUAL if (src / f"{k}.json").exists()}
    if a.check:
        diff = [k for k, v in files.items() if not (a.out / f"{k}.json").exists()
                or (a.out / f"{k}.json").read_text(encoding="utf-8") != dumps(v)]
        if diff:
            print(f"РАСХОЖДЕНИЕ с {a.out}: " + ", ".join(f"{k}.json" for k in diff), file=sys.stderr)
            return 1
        print(f"ок: {len(files)} файлов совпали с {a.out}")
        return 0
    write(files, a.out)
    for k, text in manual.items():           # ручные файлы — как есть: каталог кандидата читается ядром целиком
        with open(a.out / f"{k}.json", "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print(f"записано: {len(files)} файлов и {len(manual)} ручных (как есть) в {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
