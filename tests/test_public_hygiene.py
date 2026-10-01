# -*- coding: utf-8 -*-
"""Гигиена публичного репозитория (INTERFACES §0 п. 5, блокирующее правило).

Репозиторий публичный: всё, что попало в дерево или в метаданные коммитов,
видно всем и навсегда. Здесь — запреты по регулярке на каждом файле дерева
(отслеживаемом и ещё не добавленном, кроме игнорируемых):

* IPv4-адреса (кроме петли и документационных сетей), почтовые адреса вне
  разрешённых, пути профилей пользователя (диск, затем Users; `/home/<имя>/`);
* токены и ключи: токен T-Invest, токены GitHub, закрытые ключи; путь к файлу
  ключа в каталоге `.ssh` (кроме `config` и `known_hosts`); ID аккаунта
  Cloudflare (`account_id` с 32 шестнадцатеричными знаками);
* имена приватных служебных репозиториев владельца (публичны только код и
  данные панелей 850: Сбер, Лента, X5);
* сырые ответы T-Invest (условия API запрещают их публиковать): вне
  `indicators/*.py` нет полей и заголовков, которые несёт только настоящий
  ответ; фикстуры T-Invest — синтетика в формате ответа (INTERFACES §11);
  нет и табличных выписок (CSV, TSV) с полями его ответа — лист, повторяющий
  ответ по колонкам, тот же ответ, только без скобок (INTERFACES §3.1, §9);
* ответ T-Invest — не источник данных и не текст документа: в данных и
  документах (`data/`, `docs/`, любой `*.md`) нет имён полей его ответа, а данные
  (`data/`) не называют источником ни метод API, ни образец ответа, ни адрес API.
  Дивиденды — публичные факты: источник — решение собрания акционеров и
  раскрытие эмитента (документ или новость с датой), сверка с брокерским
  календарём — словами. Имена полей остаются в коде, который ответ читает
  (`indicators/`, сборщик фактов), и в синтетических фикстурах его формата;
* в данных (`data/`, `tests/fixtures/`): контакты и телефоны из ответов источников;
* первичка (PDF/XLSX/DOCX) вне фикстур, состояние и выходы (`var/`,
  `.wrangler/`, `*.sqlite`, `payload.json`, `release/`, `port-check/`);
* адреса авторов и коммиттеров всей видимой истории — только из
  `.github/commit-emails.allow` (тот же список сверяет шаг CI «Гигиена истории»).

ЛИЧНЫХ СЛОВ И ИХ ХЭШЕЙ ЗДЕСЬ НЕТ. Имена ключей, учётные имена владельца, ID
аккаунтов — короткие слова: хэш короткого слова в публичном тесте подтверждает
догадку перебором за минуты и навсегда связывает псевдоним с именем. Список
таких слов лежит вне репозитория — в папке передачи, файл
`private/hygiene-words.txt` (по слову в строке, `#` — комментарий); его сверяет
тест с меткой `archive` — на ноутбуке перед push:

    BANK_HANDOFF_DIR=<папка передачи> python -m pytest -rs -m archive tests/test_public_hygiene.py

Годный итог один — «1 passed». СВЕРКА БЕЗ СПИСКА КРАСНЕЕТ, А НЕ ПРОПУСКАЕТСЯ:
шаг стоит перед необратимой операцией, и «сверено, чисто» обязано отличаться от
«не сверялось» кодом возврата. Папка передачи задана, а списка в ней нет
(опечатка в пути, файл не заведён, файл пуст) — отказ; сверка заказана явно
(`-m archive`), а папка передачи не задана (опечатка в имени переменной) —
тоже отказ. Пропуск остаётся одному случаю: тест выбран не ради сверки
(`pytest -m tact` берёт и его), а папки передачи на машине нет.

В CI и на сервере папки передачи нет — тест там не идёт. При переносе в копию
другого банка список не переносится в репозиторий (docs/PORTING.md).

Исключение из правил — только строкой в `ALLOW`: файл (маска), правило,
разрешённое совпадение и причина. Гоняется в такте и в CI (метка `tact`):
быстрый. Совпадение печатается файлом, строкой и правилом — без самого текста:
журнал CI публичного репозитория тоже публичный.

Проверку ВЫПУСКА (почты, телефоны, IP и пути внутри `latest.json`) делает
`model.payload.validate` (PAYLOAD §7 п. 3).
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_EMAILS_FILE = ROOT / ".github" / "commit-emails.allow"
# Список личных слов — вне репозитория: папка передачи, этот путь внутри неё.
PRIVATE_LIST = ("private", "hygiene-words.txt")
HANDOFF_ENV = "BANK_HANDOFF_DIR"

# Публичные репозитории семейства: код и данные панелей 850. Прочие
# `temp-zero-inode-*` владельца — приватные служебные.
PREFIX = "temp-zero-" + "inode-"          # по частям: целиком строка ловится своим же правилом
PUBLIC_REPOS = re.compile(PREFIX + r"850-(?:sber|lenta|x5)(?:-data)?(?:\.git)?")

OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
RULES_ALL = {
    "ipv4": re.compile(rf"(?<![\w.]){OCTET}\.{OCTET}\.{OCTET}\.{OCTET}(?![\w.])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "gmail": re.compile(r"@g[m]ail\b", re.I),
    "user_path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/[c]/Users/|/home/[a-z_][a-z0-9_-]*/", re.I),
    "private_repo": re.compile(PREFIX + r"[A-Za-z0-9._-]+"),
    "tinvest_token": re.compile(r"(?<![\w.])t\.[A-Za-z0-9_-]{60,}"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{30,}"),
    # Файл ключа в каталоге .ssh назван по имени: имя и путь ключа живут только в
    # справочнике владельца. Каталог, `config` и `known_hosts` называть можно.
    "ssh_key_path": re.compile(r"\.ssh[\\/](?!config\b|known_hosts\b)[A-Za-z0-9_][A-Za-z0-9_.-]*"),
    "cloudflare_account": re.compile(r"(?i)account[_-]?id\W{1,6}[0-9a-f]{32}\b"),
}
# Только в данных: книга, факты, фикстуры источников.
DATA_GLOBS = ("data/*", "tests/fixtures/*")
RULES_DATA = {
    "contact": re.compile(r"contact_?person|contact_?list|contact_?phone|contact_?email|"
                          r"contactperson", re.I),
    "phone": re.compile(r"\+7[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)|"
                        r"(?<![\d.])8[\s-]?\(\d{3}\)[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"),
}

# Сырой ответ T-Invest: заголовки конверта и поля, которых нет в синтетике (сборщик
# их не читает). Имена — по частям: этот файл сам лежит в дереве. Пара ключей JSON
# «цель + имя дома» в одном файле — таблица целей брокеров по именам.
T_MARKER_WORDS = {
    "заголовки ответа API": "x-rate" + "limit",
    "поле ответа о целях брокеров": "recommendation" + "Date",
    "поле ответа о дивидендах": "yield" + "Value",
}
T_MARKERS = {what: re.compile(re.escape(word), re.I) for what, word in T_MARKER_WORDS.items()}
T_PAIR = (re.compile('"target' + 'Price"' + r"\s*:"), re.compile('"comp' + 'any"' + r"\s*:"))
T_COLLECTOR_CODE = "indicators/*.py"      # сборщик называет поля ответа — это код, не ответ

# Табличная выписка с полями ответа T-Invest (лист этапа 1 «дивиденды по годам» повторял
# шесть полей ответа о дивидендах в 24 строках): файл-таблица, у которого колонка названа
# по полю ответа (`t_<поле>`) или который называет источником метод API и несёт поле,
# которое знает только ответ (цена закрытия на дату отсечки, регулярность выплаты).
# Имена — по частям: этот файл сам лежит в дереве. Выписку полей ответа в JSON и строку-
# источник с методом API ловят два следующих правила (поля ответа, ответ как источник).
T_TABLE_GLOBS = ("*.csv", "*.tsv")
T_TABLE_COLUMN = re.compile(r"t_[a-z][a-z0-9_]*")
T_TABLE_METHOD = re.compile(r"(?:Instruments|MarketData|Operations|Users)" + r"Service/\w+|"
                            + "Get" + r"(?:Dividends|ForecastBy|AssetReports|LastPrices)\b")
T_TABLE_ANSWER_FIELDS = ("close" + "_price", "close" + "price", "regul" + "arity", "yield" + "_value")

# Имена полей ответов T-Invest (дивиденды, цели брокеров, цены, отчёты): в данных и
# документах их нет — ни ключом JSON, ни колонкой, ни словом в строке-источнике, ни в
# тексте. Код, который читает ответ (`indicators/`, `ops/tools/`), и синтетические
# фикстуры формата ответа (`tests/fixtures/`) правило не трогает: они вне этих масок.
# Скрипты листов книги лежат в `data/` и под правило попадают: они читают лист
# публичных решений собраний, а не ответ, и имён его полей им знать незачем.
T_FIELD_GLOBS = ("data/*", "docs/*", "*.md")
T_ANSWER_FIELD_NAMES = tuple(a + b for a, b in (
    ("dividend", "Net"), ("record", "Date"), ("lastBuy", "Date"), ("payment", "Date"), ("declared", "Date"),
    ("dividend", "Type"), ("close", "Price"), ("yield", "Value"), ("created", "At"),
    ("target", "Price"), ("recommendation", "Date"), ("show", "Name"), ("current", "Price"),
    ("price", "Change"), ("price", "ChangeRel"), ("min", "Target"), ("max", "Target"),
    ("instrument", "Uid"), ("instrument", "Id"), ("last", "Prices"), ("headers_", "kept")))
T_ANSWER_FIELD = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(T_ANSWER_FIELD_NAMES) + r")(?![A-Za-z0-9_])")

# Ответ API как источник данных («выписка ответа»): файл в `data/` называет метод API,
# образец ответа или адрес API. Исключение — реестр источников сборщика: он называет
# методы, которые сборщик вызывает (это настройка кода, а не данные из ответа).
T_SOURCE_GLOBS = ("data/*",)
T_SOURCE_SIGNS = {
    "метод API": re.compile(r"(?<![A-Za-z0-9_])(?:Get(?:Dividends|AssetReports|LastPrices|ForecastBy|"
                            r"ConsensusForecasts|Candles|TradingStatus)|ShareBy)(?![A-Za-z0-9_])|"
                            r"(?:Instruments|MarketData|Operations|Users)" + r"Service"),
    "образец ответа": re.compile("tinvest_" + "samples"),
    "адрес API": re.compile("invest-public" + "-api"),
}
T_COLLECTOR_REGISTRY = "data/indicators/sources.yaml"

# Исключения: (маска файла, правило, разрешённое совпадение — регулярка, причина).
ALLOW = [
    ("*", "ipv4", r"127\.0\.0\.1|0\.0\.0\.0", "петля и «все интерфейсы» — не адрес сервера"),
    ("*", "ipv4", r"(?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d+",
     "документационные сети RFC 5737 — примеры в тестах и документах"),
    ("*", "email", r"noreply@anthropic\.com", "строка соавторства коммитов Claude"),
    ("*", "email", r"[^@\s]+@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:test|invalid|example))",
     "зарезервированные домены RFC 2606 — адреса-заглушки в тестах"),
    ("*", "private_repo", PUBLIC_REPOS.pattern, "публичные репозитории кода и данных панелей 850"),
]

# Первичка и состояние в дереве запрещены целиком.
FORBIDDEN_PATHS = [
    (r"(?i)\.(pdf|xlsx|xls|xlsm|docx|doc)$", "первичка (PDF/XLSX/DOCX) живёт вне репозитория; "
     "в нём — хэши и выписки чисел со ссылкой на страницу"),
    (r"^\.wrangler/", "каталог wrangler хранит ID аккаунта и почту"),
    (r"^var/(?!\.gitkeep$)", "состояние и выходы тестов"),
    (r"(?i)\.sqlite3?$", "база состояния (журнал прогнозов)"),
    (r"(^|/)payload\.json$", "локальный выпуск"),
    (r"^release/", "локальные выпуски"),
    (r"(^|/)port-check/", "песочница переноса с материалами Магнита"),
    (r"(^|/)\.env$|(^|/)env$", "env-файл сервера (в репозитории — только ops/env.example)"),
]
FIXTURE_BINARIES = "tests/fixtures/"


def _allowed_emails() -> set[str]:
    return {line.strip() for line in ALLOWED_EMAILS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


@lru_cache(maxsize=1)
def _tree() -> tuple[str, ...]:
    """Файлы дерева: отслеживаемые и ещё не добавленные, кроме игнорируемых."""
    done = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                          cwd=str(ROOT), capture_output=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    names = [n for n in done.stdout.decode("utf-8").split("\0") if n]
    return tuple(n for n in names if (ROOT / n).is_file())


def _text(name: str) -> str | None:
    data = (ROOT / name).read_bytes()
    if b"\0" in data[:8192]:
        return None                      # двоичный файл (картинка, sqlite) — не текст
    return data.decode("utf-8", errors="replace")


def _is_allowed(name: str, rule: str, match: str) -> bool:
    if rule == "email" and match in _allowed_emails():
        return True
    return any(fnmatch.fnmatch(name, mask) and rule == r and re.fullmatch(ok, match)
               for mask, r, ok, _ in ALLOW)


def _findings_in(name: str, text: str) -> list[str]:
    bad = []
    rules = dict(RULES_ALL)
    if any(fnmatch.fnmatch(name, g) for g in DATA_GLOBS):
        rules.update(RULES_DATA)
    for rule, pattern in rules.items():
        for hit in pattern.finditer(text):
            if not _is_allowed(name, rule, hit.group(0)):
                line = text.count("\n", 0, hit.start()) + 1
                bad.append(f"{name}:{line}: {rule}")
    return bad


def _raw_tinvest_in(name: str, text: str) -> list[str]:
    """Признаки сырого ответа T-Invest в файле (сборщик `indicators/*.py` — не ответ)."""
    if fnmatch.fnmatch(name, T_COLLECTOR_CODE):
        return []
    bad = [f"{name}:{text.count(chr(10), 0, hit.start()) + 1}: сырой ответ T-Invest — {what}"
           for what, pattern in T_MARKERS.items() for hit in pattern.finditer(text)]
    if all(pattern.search(text) for pattern in T_PAIR):
        bad.append(f"{name}: сырой ответ T-Invest — цели брокеров с именами домов")
    return bad


def _table_header(text: str) -> list[str]:
    """Имена колонок таблицы: первая непустая строка, разделители — запятая, точка с запятой, табуляция."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    return [cell.strip().strip('"').strip().lower() for cell in re.split(r"[,;\t]", first)]


def _tinvest_table_in(name: str, text: str) -> list[str]:
    """Признаки табличной выписки с полями ответа T-Invest (сборщик `indicators/*.py` — не лист)."""
    if fnmatch.fnmatch(name, T_COLLECTOR_CODE) or not any(fnmatch.fnmatch(name.lower(), g) for g in T_TABLE_GLOBS):
        return []
    header = _table_header(text)
    named = sorted(c for c in header if T_TABLE_COLUMN.fullmatch(c))
    answer = sorted(c for c in header if any(f in c.replace(" ", "_") for f in T_TABLE_ANSWER_FIELDS))
    if named:
        return [f"{name}:1: табличная выписка ответа T-Invest — колонок, названных по полям ответа: {len(named)}"]
    if answer and T_TABLE_METHOD.search(text):
        return [f"{name}:1: табличная выписка ответа T-Invest — источник назван методом API, "
                f"колонок с полями ответа: {len(answer)}"]
    return []


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def _tinvest_fields_in(name: str, text: str) -> list[str]:
    """Имена полей ответа T-Invest в данных и документах (совпадение не печатается)."""
    if not any(fnmatch.fnmatch(name, g) for g in T_FIELD_GLOBS):
        return []
    return [f"{name}:{_line(text, hit.start())}: имя поля ответа T-Invest"
            for hit in T_ANSWER_FIELD.finditer(text)]


def _tinvest_source_in(name: str, text: str) -> list[str]:
    """Данные, называющие источником ответ T-Invest: метод API, образец ответа, адрес API."""
    if name == T_COLLECTOR_REGISTRY or not any(fnmatch.fnmatch(name, g) for g in T_SOURCE_GLOBS):
        return []
    return [f"{name}:{_line(text, hit.start())}: источником назван ответ T-Invest — {what}"
            for what, pattern in T_SOURCE_SIGNS.items() for hit in pattern.finditer(text)]


def _private_words(text: str) -> list[str]:
    """Слова списка: по одному в строке, пустые строки и `#`-комментарии пропускаются."""
    words = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    return [w for w in words if w]


def _private_findings_in(name: str, text: str, words: list[str]) -> list[str]:
    """Вхождения слов списка без учёта регистра; слово не печатается — только его номер."""
    low = text.lower()
    bad = []
    for number, word in enumerate(words, 1):
        at = low.find(word.lower())
        if at >= 0:
            bad.append(f"{name}:{text.count(chr(10), 0, at) + 1}: слово № {number} личного списка")
    return bad


def test_the_tree_carries_no_addresses_paths_tokens_or_private_names():
    bad = []
    for name in _tree():
        text = _text(name)
        if text is not None:
            bad += _findings_in(name, text)
    assert not bad, "нарушена гигиена публичного репозитория:\n" + "\n".join(sorted(set(bad)))


def test_the_tree_carries_no_raw_tinvest_answers():
    """Условия T-Invest запрещают публиковать ответы API: в дереве — только синтетика в
    формате ответа (поля, которые читает сборщик), сырьё — в приватном архиве сервера."""
    bad = []
    for name in _tree():
        text = _text(name)
        if text is not None:
            bad += _raw_tinvest_in(name, text)
    assert not bad, "в дереве признаки сырых ответов T-Invest:\n" + "\n".join(sorted(set(bad)))


def test_the_tree_carries_no_tables_of_tinvest_answers():
    """Лист, повторяющий ответ T-Invest по колонкам, — тот же ответ: во входах книги и в
    фикстурах таких таблиц нет (расчёты книги и сборщик фактов читают лист публичных
    решений собраний акционеров и проверочные листы)."""
    bad = []
    for name in _tree():
        text = _text(name)
        if text is not None:
            bad += _tinvest_table_in(name, text)
    assert not bad, "в дереве табличные выписки ответов T-Invest:\n" + "\n".join(sorted(set(bad)))


def test_data_and_documents_name_no_fields_of_tinvest_answers():
    """Ответ API в публичный репозиторий не переносится ни целиком, ни выпиской полей, ни
    их именами в строке-источнике: в данных и документах поле ответа названо словами
    («дивиденд на акцию», «дата отсечки»), а не именем из ответа."""
    bad = []
    for name in _tree():
        text = _text(name)
        if text is not None:
            bad += _tinvest_fields_in(name, text)
    assert not bad, ("в данных и документах — имена полей ответа T-Invest (нужны слова):\n"
                     + "\n".join(sorted(set(bad))))


def test_data_cites_no_tinvest_answer_as_its_source():
    """Источник числа в данных — публичный документ: решение собрания акционеров или
    набсовета, раскрытие эмитента, новость с датой. Ответ брокерского API источником не
    называется — ни методом, ни образцом ответа, ни адресом API; сверка с брокерским
    календарём записана словами."""
    bad = []
    for name in _tree():
        text = _text(name)
        if text is not None:
            bad += _tinvest_source_in(name, text)
    assert not bad, ("данные называют источником ответ T-Invest (нужен публичный документ):\n"
                     + "\n".join(sorted(set(bad))))


def test_primary_documents_and_state_are_not_in_the_tree():
    bad = []
    for name in _tree():
        for pattern, reason in FORBIDDEN_PATHS:
            if re.search(pattern, name) and not (name.startswith(FIXTURE_BINARIES)
                                                 and pattern.startswith("(?i)\\.(pdf")):
                bad.append(f"{name}: {reason}")
    assert not bad, bad


def _history() -> str | None:
    """Адреса авторов и коммиттеров всех веток; None — коммитов ещё нет."""
    if subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=str(ROOT),
                      capture_output=True).returncode != 0:
        return None
    done = subprocess.run(["git", "log", "--all", "--format=%H %ae %ce"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_every_author_and_committer_is_on_the_allowlist():
    """Метаданные коммитов не удаляются задним числом: адрес автора и коммиттера
    каждого видимого коммита — из .github/commit-emails.allow. В CI видна вся
    история всех веток (fetch-depth 0), на сервере — клон main."""
    history = _history()
    if history is None:
        pytest.skip("в рабочей копии ещё нет коммитов — проверять нечего (в CI история есть всегда)")
    allowed = _allowed_emails()
    bad = [line.split()[0][:12] for line in history.splitlines() if not set(line.split()[1:]) <= allowed]
    assert history.strip(), "история не видна — проверять нечего"
    assert not bad, f"адреса вне .github/commit-emails.allow в коммитах: {', '.join(bad[:20])}"


def test_the_allowlist_holds_only_noreply_addresses():
    emails = _allowed_emails()
    assert emails, "список пуст — CI не пропустил бы ни одного коммита"
    for email in emails:
        assert email.endswith(("@users.noreply.github.com", "noreply@github.com")), email


# ---------------------------------------------- личные слова: список вне репозитория

def _archive_asked(markexpr: str) -> bool:
    """Сверка заказана явно: выражение `-m` называет `archive` и не исключает его."""
    return bool(re.search(r"\barchive\b", markexpr)) and not re.search(r"\bnot\s+archive\b", markexpr)


def _private_list(handoff: str | None, *, asked: bool) -> list[str]:
    """Слова личного списка из папки передачи. Нет списка — отказ, а не пропуск: «сверено,
    чисто» и «не сверялось» обязаны различаться кодом возврата. Пропуск — только когда папка
    передачи не задана и сверку явно не заказывали."""
    where = "/".join(PRIVATE_LIST)
    if not handoff:
        if asked:
            pytest.fail(f"сверка личных слов заказана (-m archive), а папка передачи не задана ({HANDOFF_ENV}) — "
                        "push не делать", pytrace=False)
        pytest.skip(f"нет папки передачи ({HANDOFF_ENV}); сверка личных слов — перед push, с ключом -m archive")
    path = Path(handoff).joinpath(*PRIVATE_LIST)
    if not path.is_file():
        pytest.fail(f"в папке передачи нет списка личных слов {where} (каталог или файл не найден) — "
                    "сверять нечем, push не делать", pytrace=False)
    words = _private_words(path.read_text(encoding="utf-8"))
    if not words:
        pytest.fail(f"список личных слов {where} пуст — сверять нечем, push не делать", pytrace=False)
    return words


@pytest.mark.archive
def test_the_tree_and_the_history_carry_no_private_words(request):
    """Перед push, на ноутбуке: слова личного списка (имена ключей и ssh-алиасов, учётные
    имена, ID аккаунтов) не встречаются ни в дереве, ни в именах авторов и сообщениях
    коммитов. Список — в папке передачи; здесь нет ни слов, ни их хэшей. Без списка тест
    краснеет: тихий пропуск перед необратимым push выглядел бы как «чисто»."""
    words = _private_list(os.environ.get(HANDOFF_ENV),
                          asked=_archive_asked(request.config.getoption("markexpr") or ""))
    bad = []
    for name in _tree():
        text = _text(name)
        if text is not None:
            bad += _private_findings_in(name, text, words)
    done = subprocess.run(["git", "log", "--all", "--format=%H %an %cn %B"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode == 0:
        bad += _private_findings_in("история коммитов (имена и сообщения)", done.stdout, words)
    assert not bad, "личные слова в публичном репозитории:\n" + "\n".join(sorted(set(bad)))


def test_a_missing_private_list_is_red_not_skipped(tmp_path):
    """Оба исхода сверки без списка (тест такта): папка передачи задана, а списка нет —
    отказ; сверка заказана, а папка не задана — отказ; пропуск — только когда тест выбран
    не ради сверки и папки нет. Список на месте — слова читаются."""
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    for asked in (True, False):
        with pytest.raises(pytest.fail.Exception, match="нет списка личных слов"):
            _private_list(str(handoff), asked=asked)                       # нет каталога private/
        with pytest.raises(pytest.fail.Exception, match="нет списка личных слов"):
            _private_list(str(tmp_path / "опечатка-в-пути"), asked=asked)
    listed = handoff.joinpath(*PRIVATE_LIST)
    listed.parent.mkdir()
    listed.write_text("# только комментарий\n\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="пуст"):
        _private_list(str(handoff), asked=True)
    with pytest.raises(pytest.fail.Exception, match="папка передачи не задана"):
        _private_list(None, asked=True)
    with pytest.raises(pytest.fail.Exception, match="папка передачи не задана"):
        _private_list("", asked=True)
    with pytest.raises(pytest.skip.Exception):
        _private_list(None, asked=False)
    listed.write_text("# личный список\nkey-of-nobody\n", encoding="utf-8")
    assert _private_list(str(handoff), asked=True) == ["key-of-nobody"]


@pytest.mark.parametrize("markexpr,asked", [
    ("archive", True), ("archive and tact", True), ("tact or archive", True),
    ("not network and not archive", False), ("tact", False), ("", False),
    ("tact and not network and not archive and not docs", False),
])
def test_the_private_check_knows_when_it_was_asked_for(markexpr, asked):
    assert _archive_asked(markexpr) is asked


def test_the_private_list_check_works_on_a_synthetic_word():
    """Самопроверка на выдуманных словах: проверка ловит слово списка (без учёта регистра,
    и внутри пути) и не трогает соседний текст; в отчёте — номер слова, не оно само."""
    words = _private_words("# личный список\n\nkey-of-nobody   # имя ключа\nSynthetic-Alias\n")
    assert words == ["key-of-nobody", "Synthetic-Alias"]
    found = _private_findings_in("ops/README.md", "первая\nключ /srv/panel/keys/KEY-OF-NOBODY.pub\n", words)
    assert found == ["ops/README.md:2: слово № 1 личного списка"]
    assert "nobody" not in found[0]
    assert _private_findings_in("ops/README.md", "алиас synthetic-alias из конфигурации ssh", words)
    assert not _private_findings_in("ops/README.md", "алиас another-alias и ключ записи", words)


def test_the_hygiene_test_carries_no_hashes_of_private_words():
    """Хэш короткого личного слова в публичном файле — та же утечка, что и слово:
    догадка подтверждается перебором. В этом файле нет длинных шестнадцатеричных строк
    и нет вычисления хэшей."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert not re.findall(r"\b[0-9a-f]{16,}\b", source)
    assert ("hash" + "lib") not in source and ("sha" + "256(") not in source and ("hm" + "ac.") not in source


# ------------------------------------------------ правила сами ловят то, что должны

@pytest.mark.parametrize("rule,text", [
    ("ipv4", "сервер 10." + "20.30.40 отвечает"),
    ("email", "пишите на person@" + "mail.ru"),
    ("gmail", "адрес someone@" + "gmail.com"),
    ("user_path", "лежит в C:" + "\\Users\\someone\\x"),
    ("user_path", "лежит в /c/" + "Users/someone/x"),
    ("user_path", "лежит в /home/" + "someone/x"),
    ("private_repo", "служебный temp-zero-" + "inode-839"),
    ("private_repo", "книга temp-zero-" + "inode-850oa"),
    ("tinvest_token", "TINVEST_TOKEN=t." + "A" * 86),
    ("github_token", "ghp_" + "a1" * 18),
    ("private_key", "-----BEGIN OPENSSH " + "PRIVATE KEY-----"),
    ("bearer", "Authorization: Bearer " + "x" * 40),
    ("ssh_key_path", "ключ /srv/panel/.s" + "sh/key-of-nobody"),
    ("ssh_key_path", "IdentityFile ~/.s" + "sh/id_ed25519"),
    ("cloudflare_account", 'account' + '_id = "' + "0f" * 16 + '"'),
])
def test_each_rule_catches_its_case(rule, text):
    assert RULES_ALL[rule].search(text), (rule, text)
    hit = RULES_ALL[rule].search(text).group(0)
    assert not _is_allowed("docs/x.md", rule, hit), (rule, hit)


@pytest.mark.parametrize("rule,text", [
    ("contact", '{"contact_person": "…"}'),
    ("phone", "тел. +7 (495) 123-45-67"),
    ("phone", "тел. 8 (800) 555-35-35"),
])
def test_each_data_rule_catches_its_case(rule, text):
    assert RULES_DATA[rule].search(text), (rule, text)


@pytest.mark.parametrize("text", [
    '{"targets": [{"comp' + 'any": "Дом", "target' + 'Price": {"units": "400"}}]}',
    '{"headers": {"X-Rate' + 'Limit-Remaining": "199"}}',
    '{"recommendation' + 'Date": "2026-09-01T00:00:00Z"}',
    '{"yield' + 'Value": {"units": "10", "nano": 0}}',
])
def test_a_raw_tinvest_answer_is_caught(text):
    assert _raw_tinvest_in("tests/fixtures/ind/tinvest/x.json", text), text
    assert _raw_tinvest_in("docs/INDICATORS.md", text), "и в документе, и в книге"
    assert not _raw_tinvest_in("indicators/tinvest.py", text), "код сборщика называет поля ответа"


def test_a_synthetic_tinvest_fixture_passes():
    """Синтетика в формате ответа: только поля, которые читает сборщик, выдуманные дома."""
    synthetic = ('{"targets": [{"showName": "Брокер A", "recommendation": "RECOMMENDATION_BUY", '
                 '"target' + 'Price": {"units": "400", "nano": 0}}], '
                 '"dividends": [{"dividendNet": {"units": "30"}, "recordDate": "2026-07-20T00:00:00Z"}]}')
    assert not _raw_tinvest_in("tests/fixtures/ind/tinvest/forecast.json", synthetic)
    assert not _raw_tinvest_in("data/assumptions/assumptions.json", '{"meta": {"company": {"name": "Банк"}}}')
    assert not _tinvest_fields_in("tests/fixtures/ind/tinvest/forecast.json", synthetic), "фикстура формата ответа"
    assert _tinvest_fields_in("data/facts/dividends.json", synthetic), "та же синтетика в данных — выписка полей"


def test_a_table_of_a_tinvest_answer_is_caught():
    """Лист, повторяющий ответ о дивидендах по колонкам (синтетика в том же виде): ловится
    и по колонкам, названным по полям ответа, и по источнику-методу API с ценой закрытия."""
    method = "T-Invest InstrumentsService/" + "Get" + "Dividends"
    by_columns = ("ticker,fiscal_year,record_date,t_last_buy_date,dps_gross_rub,t_regu" + "larity,t_close" + "_price\n"
                  "TICK,2001,2002-06-17,2002-06-14,1.5,Annual,89\n")
    by_source = ("ticker;record_date;dps;close" + "_price;source\n"
                 f'TICK;2002-06-17;1.5;89;"{method}, снимок"\n')
    for name in ("data/assumptions/evidence/x/inputs/sheet.csv", "tests/fixtures/ind/sheet.TSV"):
        assert _tinvest_table_in(name, by_columns), name
        assert _tinvest_table_in(name, by_source), name
    found = _tinvest_table_in("data/assumptions/evidence/x/inputs/sheet.csv", by_columns)
    assert found == ["data/assumptions/evidence/x/inputs/sheet.csv:1: табличная выписка ответа T-Invest — "
                     "колонок, названных по полям ответа: 3"]
    assert not _tinvest_table_in("indicators/tinvest.py", by_columns), "код сборщика — не лист"
    assert not _tinvest_table_in("docs/INDICATORS.md", by_columns), "документ с примером колонок — не таблица"


def test_ordinary_tables_are_not_taken_for_tinvest_answers():
    """Проверочный лист расчёта и таблица с ценой закрытия биржи (без метода API T-Invest)
    — не лист ответа. Лист и выписку, называющие источником метод API, правило таблиц не
    берёт — их ловит правило «ответ как источник»."""
    pool = "fiscal_year,ni_to_shareholders_bn,pool_bn,dps_declared,source\n2001,10.5,5.2,1.5,\"отчёт, с. 6\"\n"
    iss = "date,ticker,close_price,source\n2002-06-17,TICK,89,MOEX ISS history\n"
    cited = ("year,dps,record_date,source\n"
             "2001,1.5,2002-06-17,\"" + "Get" + "Dividends и новость о решении собрания\"\n")
    for text in (pool, iss, cited):
        assert not _tinvest_table_in("data/assumptions/evidence/x/inputs/sheet.csv", text), text
    extract = '{"source": "T-Invest InstrumentsService/' + "Get" + 'Dividends", "dividends": [{"record": "2002-06-17"}]}'
    assert not _tinvest_table_in("data/assumptions/evidence/x/inputs/extract.json", extract)
    assert _tinvest_source_in("data/assumptions/evidence/x/inputs/extract.json", extract)
    assert _tinvest_source_in("data/assumptions/evidence/x/inputs/sheet.csv", cited)
    for text in (pool, iss):
        assert not _tinvest_source_in("data/assumptions/evidence/x/inputs/sheet.csv", text), text
        assert not _tinvest_fields_in("data/assumptions/evidence/x/inputs/sheet.csv", text), text


FIELD = {"net": "dividend" + "Net", "record": "record" + "Date", "buy": "lastBuy" + "Date",
         "pay": "payment" + "Date", "declared": "declared" + "Date", "target": "target" + "Price"}


@pytest.mark.parametrize("text", [
    '{"src": "календарь брокера, ' + FIELD["net"] + ", " + FIELD["record"] + ', sha256 0000"}',
    "| `dps_ordinary` | 1,5 | Ф | образец ответа, " + FIELD["buy"] + ", " + FIELD["pay"] + " |",
    "валовой дивиденд (`" + FIELD["net"] + "`), объявлен (`" + FIELD["declared"] + "`)",
    "year;dps;" + FIELD["record"] + "\n2001;1.5;2002-06-17\n",
    '{"targets": [{"' + FIELD["target"] + '": 400}]}',
])
def test_a_field_of_a_tinvest_answer_is_caught_in_data_and_documents(text):
    """Имя поля ответа ловится и в данных, и в документах; в коде, который читает ответ,
    и в синтетических фикстурах его формата — нет. В отчёте — файл и строка, без текста."""
    for name in ("data/facts/dividends.json", "data/assumptions/evidence/x/out/listing.txt",
                 "data/assumptions/evidence/x/sheet.py", "docs/FACTS.md", "docs/MODEL.md", "README.md",
                 "ops/README.md"):
        found = _tinvest_fields_in(name, text)
        assert found and all(f.startswith(name + ":") and f.endswith(": имя поля ответа T-Invest") for f in found), name
        assert not [f for f in found if any(word in f for word in FIELD.values())], "совпадение не печатается"
    for name in ("indicators/tinvest.py", "ops/tools/build_facts.py", "tests/fixtures/ind/tinvest/dividends_X.json",
                 "tests/test_ind_tinvest.py", "web/app.js"):
        assert not _tinvest_fields_in(name, text), name


def test_words_for_the_same_things_are_not_fields_of_an_answer():
    """Свои имена и слова — не поля ответа: змеиный регистр фактов, слова документа."""
    for text in ('{"record_date": "2002-06-17", "pay_date": "2002-07-01", "last_buy_date": "2002-06-14"}',
                 "year,dps_ordinary,record_date,source\n2001,1.5,2002-06-17,решение собрания 28.06.2002\n",
                 "дивиденд на акцию, дата отсечки, последний день покупки, срок выплаты; цель брокера",
                 "target_price, close_price, created_at, dividendNetwork, recordDates"):
        assert not _tinvest_fields_in("data/facts/dividends.json", text), text
        assert not _tinvest_fields_in("docs/FACTS.md", text), text


@pytest.mark.parametrize("what,text", [
    ("метод API", '{"src": "T-Invest API: ' + "Get" + 'Dividends TICK (снимок 30.09.2001)"}'),
    ("метод API", '{"url": "https://api.example.test/rest/ (Instruments' + 'Service/x)"}'),
    ("метод API", "источник: Share" + "By и Get" + "LastPrices"),
    ("образец ответа", '{"file": "stage1/market/tinvest_' + 'samples/answer_TICK.json"}'),
    ("адрес API", '{"url": "https://invest-public' + '-api.example.test/rest/"}'),
])
def test_data_citing_a_tinvest_answer_is_caught(what, text):
    """Данные, чей источник — ответ API: ловятся в фактах, в книге и в её входах и выходах;
    реестр источников сборщика называет методы, которые сборщик вызывает, — он не данные.
    Документы о сборщике правило не трогает: метод в них — описание кода."""
    for name in ("data/facts/anchor.json", "data/assumptions/ASSUMPTIONS-BOOK.md",
                 "data/assumptions/evidence/x/inputs/SOURCES.json", "data/assumptions/evidence/x/out/listing.txt"):
        found = _tinvest_source_in(name, text)
        assert found and all(what in f for f in found), (name, found)
    for name in (T_COLLECTOR_REGISTRY, "docs/INDICATORS.md", "indicators/tinvest.py", "tests/support_ind.py"):
        assert not _tinvest_source_in(name, text), name


def test_a_public_source_of_a_dividend_passes():
    """Строка-источник, как она должна выглядеть: решение собрания, раскрытие, новость с
    датой; сверка с брокерским календарём — словами."""
    for text in ('{"src": "решение годового собрания акционеров 30.06.2001; раскрытие эмитента; '
                 'сверено с брокерским календарём — расхождений нет"}',
                 "year,dps,record_date,source\n2001,1.5,2002-06-17,\"новость агентства 28.06.2002 о решении собрания\"\n",
                 "dividends_days: 800   # окно календаря дивидендов назад"):
        assert not _tinvest_source_in("data/facts/dividends.json", text), text
        assert not _tinvest_fields_in("data/facts/dividends.json", text), text


def test_the_rules_leave_ordinary_text_alone():
    text = ("версия 4.135.0, цена 273.05, выручка 1 234 567, дата 2026-09-28, 1.2.3, "
            "t.me/s/канал, https://tzi-850-sber.pages.dev/api/model, каталог /srv/dash/.ssh/, "
            "файлы ~/.ssh/config и /srv/dash/.ssh/known_hosts, account_id в wrangler не пишется")
    for rule in ("ipv4", "email", "user_path", "tinvest_token", "private_repo", "ssh_key_path",
                 "cloudflare_account"):
        assert not RULES_ALL[rule].search(text), rule
    assert not RULES_DATA["phone"].search("сумма 81234567890 руб. и 89.1234567")
    assert _is_allowed("tests/x.py", "ipv4", "127.0.0.1")
    assert _is_allowed("docs/x.md", "ipv4", "203.0.113.7"), "документационная сеть RFC 5737"
    assert _is_allowed("tests/x.py", "email", "someone@example.com")
    assert _is_allowed("README.md", "email", "304002195+ML371KL@users.noreply.github.com")
    assert not _is_allowed("tests/x.py", "email", "someone@" + "mail.ru")
    for public in ("temp-zero-inode-850-sber", "temp-zero-inode-850-sber-data",
                   "temp-zero-inode-850-lenta-data.git", "temp-zero-inode-850-x5"):
        assert _is_allowed("README.md", "private_repo", public), public
