# -*- coding: utf-8 -*-
"""Тексты книги: разделы ASSUMPTIONS-BOOK.md, поля каждого суждения, пути ключей в тексте есть в книге, идентификаторы
шаблона описаны, версия книги одна в шаблоне, тексте, README и журнале версии; таблица осей — все оси книги, мост ЧПМ
в тексте — факт, который читает ядро; README — порядок версии и теги; строки, которые печатает витрина (источники и
имена осей, каналы дисконта), — словами для владельца; гигиена публичного репозитория в каталоге книги (поток book;
печатаемые строки и гигиена — метка `tact`)."""
from __future__ import annotations

import json
import re

import pytest

try:
    from tests import support_book as S
except ImportError:                      # прогон без pytest.ini (pythonpath = .) и без tests/__init__.py
    import support_book as S

BOOK_MD = S.BOOK_DIR / "ASSUMPTIONS-BOOK.md"
SECTIONS = ("Главное", "Миры и слои", "Режимы", "Регуляторные сценарии", "ЧПД", "Кредитный риск и мост",
            "ЧКД, расходы, прочее, непрофильный", "Капитал и RWA", "Дивиденды", "Оценка", "Оси полосы",
            "Обратный расчёт", "A-P2u", "Гейты", "Ограничения")
FIELDS = ("**Ключи", "**Ось", "**Класс", "**Обоснование", "**Чем может ошибаться")


@pytest.fixture(scope="module")
def text() -> str:
    return BOOK_MD.read_text(encoding="utf-8")


@pytest.mark.docs
def test_the_book_has_every_section(text):
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    for sec in SECTIONS:
        assert any(sec in h for h in heads), sec
    assert "<!--=" in text                                   # заготовка раздела 0 — метки для render_numbers


@pytest.mark.docs
def test_every_judgement_carries_all_fields(text):
    blocks = re.split(r"(?m)^### ", text)[1:]
    assert len(blocks) >= 60
    for b in blocks:
        title = b.splitlines()[0]
        assert re.match(r"A-[A-Z]+\d+[a-z]*\. ", title), title
        body = b.split("\n## ")[0]
        missing = [f for f in FIELDS if f not in body]
        assert missing == [], (title, missing)
        cls = re.search(r"\*\*Класс:\*\* ([ABC])", body)
        assert cls, title                                     # класс надёжности A / B / C


@pytest.mark.docs
def test_template_ids_are_described_in_the_book(text):
    tpl = (S.BOOK_DIR / "assumptions_template.yaml").read_text(encoding="utf-8")
    ids = set(re.findall(r"\bA-[A-Z]+\d+[a-z]*\b", tpl))
    heads = set(re.findall(r"(?m)^### (A-[A-Z]+\d+[a-z]*)\.", text))
    assert ids and ids <= heads, sorted(ids - heads)


@pytest.mark.docs
def test_key_paths_named_in_the_book_exist(text):
    book = S.built_book()
    bad = []
    for tok in set(re.findall(r"`([a-z_]+(?:\.[A-Za-z0-9_<>{}, .]+)+)`", text)):
        tok = tok.strip()
        if tok.split(".")[0] not in S.TOP or " " in tok.replace(", ", ","):
            continue
        for p in S._expand(tok.replace(", ", ","), book):
            if "*" not in p and S._applies(p, book) and not S.has(book, p):
                bad.append(p)
    assert bad == []


@pytest.mark.docs
def test_readme_names_the_version_order_and_tags():
    readme = (S.BOOK_DIR / "README.md").read_text(encoding="utf-8")
    for s in ("book-<версия>", "build_assumptions.py", "worlds_recipe.py --check", "model.book_results", "tests/test_book_"):
        assert s in readme, s
    ev = (S.EVIDENCE / "README.md").read_text(encoding="utf-8")
    for area in ("worlds_bank", "nii", "capital", "bridges", "misc", "regimes", "beta", "dividends", "regulation", "governance"):
        assert f"`{area}/`" in ev, area
        assert (S.EVIDENCE / area / "README.md").exists(), area


@pytest.mark.docs
def test_the_version_is_one_in_the_template_text_readme_and_journal(text):
    version = S.template()["meta"]["version"]
    assert text.splitlines()[0].endswith(f"версия {version}")
    assert f"`book-{version}`" in text
    assert re.search(rf"(?m)^\| {re.escape(version)} \| ", text)                        # журнал версий, раздел 16
    assert re.search(rf"(?m)^\| {re.escape(version)} \| ", (S.BOOK_DIR / "README.md").read_text(encoding="utf-8"))
    journal = S.BOOK_DIR / f"V{version}-CHANGES.md"
    if version != "1.0":
        assert journal.exists() and f"версии {version}" in journal.read_text(encoding="utf-8").splitlines()[0]


@pytest.mark.docs
def test_the_book_date_and_the_day_the_version_was_recorded_are_two_named_dates(text):
    """Две даты не смешиваются: дата книги — `meta.date`, день цены и оценки (её печатает выпуск: «книга от …»);
    день записи версии — столбец «Записана» журналов. Шапка книги называет дату книги той же датой, что шаблон,
    а запись версии — той же датой, что строка журнала раздела 16, README каталога и журнал версии."""
    meta = S.template()["meta"]
    version, day = meta["version"], str(meta["date"])
    assert day == str(meta["valuation_date"]), "дата книги — день цены и оценки книги"
    ru = f"{day[8:10]}.{day[5:7]}.{day[:4]}"
    head = " ".join(text.split("\n## ", 1)[0].split())
    assert f"**Дата книги:** {ru}" in head and "`meta.date`" in head
    recorded = re.search(rf"\*\*версия {re.escape(version)} записана:\*\* (\d{{2}}\.\d{{2}}\.\d{{4}})", head)
    assert recorded, "шапка книги называет день записи версии"
    row = rf"(?m)^\| {re.escape(version)} \| {re.escape(recorded.group(1))} \| "
    assert "| Версия | Записана | Что |" in text and re.search(row, text)
    readme = (S.BOOK_DIR / "README.md").read_text(encoding="utf-8")
    assert "| Версия | Записана | Что |" in readme and re.search(row, readme)
    journal = (S.BOOK_DIR / f"V{version}-CHANGES.md").read_text(encoding="utf-8")
    assert f"Версия записана: {recorded.group(1)}" in journal and f"дата книги (`meta.date`) — {ru}" in journal
    template = (S.BOOK_DIR / "assumptions_template.yaml").read_text(encoding="utf-8").splitlines()[:3]
    assert recorded.group(1) in " ".join(template) and ru in " ".join(template)


@pytest.mark.docs
def test_the_axes_table_lists_every_axis_of_the_book(text):
    """Раздел 11: строка таблицы на каждую ось книги (первый путь оси назван в строке), номера подряд; текст не
    называет осей «вне полосы», пока `off_band_axes` пуст."""
    unc = S.template()["valuation"]["uncertainty"]
    a = text.index("## 11. Оси полосы неопределённости")
    section = text[a:text.index("## 12.", a)]
    rows = [ln for ln in section.splitlines() if re.match(r"\| \d+ \| ", ln)]
    assert [int(ln.split("|")[1]) for ln in rows] == list(range(1, len(unc["axes"]) + len(unc["off_band_axes"]) + 1))
    cells = [ln.split("|")[4] for ln in rows]
    for ax in unc["axes"] + unc["off_band_axes"]:
        first, last = ax["paths"][0].split(".")[0], ax["paths"][0].split(".")[-1]
        assert any(first in c and last in c for c in cells), ax["name"]
    if not unc["off_band_axes"]:
        assert "вне полосы:" not in section and "| вне" not in section
        assert f"{len(unc['axes'])} оси" in section


@pytest.mark.docs
def test_the_text_quotes_the_nim_bridge_the_core_reads(text):
    """Мост ЧПМ в суждениях A-C8, A-N2 и A-X4 — факт `bridge_mgmt_ifrs.nim` (его читает ядро), а не число листа с
    другой базой процентных активов (запись аудита № 29)."""
    fact = json.loads((S.FACTS_DIR / "bridge_mgmt_ifrs.json").read_text(encoding="utf-8"))["nim"]["value"]["v"]
    quoted = f"{abs(fact) * 100:.3f}".replace(".", ",")
    for head in ("### A-C8.", "### A-N2.", "### A-X4."):
        a = text.index(head)
        block = text[a:text.index("\n### ", a + 5)]
        assert f"{quoted} п.п." in block, head


@pytest.mark.tact
def test_axis_sources_names_and_channels_are_written_for_the_owner():
    """Строки книги, которые печатает витрина (PAYLOAD §0.2; записи аудита № 73, № 96): источник суждения каждой оси
    полосы — комментарий машинной книги к первому пути оси, разобранный ядром, — найден (не запасная подпись «книга
    …, ось …»), не длиннее 80 знаков и написан словами: без рабочих пометок, имён листов и ключей, меток класса, дат без
    года и версий без имени; то же — имена осей, имена и основания каналов дисконта за управление."""
    book = S.template()
    sources = S.axis_sources()
    axes = book["valuation"]["uncertainty"]["axes"]
    assert list(sources) == [a["name"] for a in axes]
    bad = {}
    for name, src in sources.items():
        issues = S.printed_issues(src) + S.printed_issues(name)
        if src.startswith("книга ") or not src.strip():
            issues.append("нет комментария-источника у первого пути оси")
        if len(src) > 80 or src.endswith("…"):
            issues.append(f"источник обрезан или длиннее 80 знаков ({len(src)})")
        if issues:
            bad[name] = issues
    assert bad == {}
    for ch in book["valuation"]["governance"]["components"]:
        assert S.printed_issues(ch["basis"]) == [] and S.printed_issues(ch["name"]) == [], ch["id"]
    for a in book["valuation"]["reverse_dcf"]["axes"]:
        assert S.printed_issues(a["name"]) == [], a["name"]
    assert book["worlds"]["source"]["origin"] in sources["Веса миров"]            # запись миров — с именем и версией


@pytest.mark.tact
def test_book_directory_is_clean_for_a_public_repository():
    user = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/home/[a-z_][a-z0-9_-]*/", re.I)
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
    octet = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    ipv4 = re.compile(rf"(?<![\w.]){octet}\.{octet}\.{octet}\.{octet}(?![\w.])")
    bad = []
    for p in S.BOOK_DIR.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        assert p.suffix.lower() not in (".pdf", ".xlsx", ".xls", ".docx"), p      # первичка — только ссылкой
        t = p.read_text(encoding="utf-8", errors="replace")
        for name, rx in (("путь пользователя", user), ("почта", email), ("IPv4", ipv4)):
            if rx.search(t):
                bad.append((str(p.relative_to(S.ROOT)), name, rx.search(t).group(0)))
    assert bad == []
