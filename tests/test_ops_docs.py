# -*- coding: utf-8 -*-
"""Документы репозитория (README, справочник, решения, история, перенос, эксплуатация)
ссылаются на то, что есть: пути в `коде` существуют, команды называют настоящие
режимы, а публичный справочник не несёт доступов. Процедуры, которые исполняют
буквально и один раз, — установка на сервер и первый push (`ops/README.md` §5, §12) —
сверяются по шагам: порядок, проверки, приёмка."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docs

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ("README" + ".md", "docs/MANUAL" + ".md", "docs/DECISIONS" + ".md", "docs/CHANGELOG" + ".md",
             "docs/PORTING" + ".md", "ops/README" + ".md")
TOP = ("model/", "indicators/", "ops/", "data/", "docs/", "tests/", "web/", "functions/", ".github/")
PATH = re.compile(r"`([^`\s]+)`")
# Выходы, которые выпускают ядро и сборка (коммитит интеграция или лежат в состоянии).
GENERATED = {"data/assumptions/results.json", "data/assumptions/run_output.txt"}


def _paths(text: str) -> set[str]:
    out = set()
    for token in PATH.findall(text):
        token = token.rstrip(".,;:")
        if not token.startswith(TOP) or any(c in token for c in "<>{}*[]|$"):
            continue
        out.add(token.split(":")[0])
    return out


@pytest.mark.parametrize("name", DOCUMENTS)
def test_every_path_named_in_a_document_exists(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    missing = sorted(p for p in _paths(text) - GENERATED if not (ROOT / p.rstrip("/")).exists())
    assert not missing, f"{name}: нет {missing}"


def test_the_manual_names_the_real_units_and_modes():
    text = (ROOT / "docs" / ("MANUAL" + ".md")).read_text(encoding="utf-8")
    units = {p.stem for p in (ROOT / "ops" / "systemd").glob("*.service")}
    for unit in units:
        assert unit in text, unit
    script = (ROOT / "ops" / "run.sh").read_text(encoding="utf-8")
    modes = re.search(r'^  collect\|([a-z|-]+)\) ;;$', script, re.M).group(1).split("|")
    for mode in ["collect", *modes]:
        assert mode in text, mode


def test_the_public_manual_keeps_access_values_out():
    """Доступы — «где искать», без значений: ни имени ключа, ни токена, ни адреса."""
    text = (ROOT / "docs" / ("MANUAL" + ".md")).read_text(encoding="utf-8")
    section = text.split("## 3.", 1)[1].split("\n## ", 1)[0]
    assert "справочник владельца" in section
    assert not re.search(r"(?i)token\s*=|ssh-ed25519|BEGIN [A-Z ]*KEY|\bt\.[A-Za-z0-9_-]{20,}", text)


def test_the_readme_lists_the_documents_that_exist():
    text = (ROOT / ("README" + ".md")).read_text(encoding="utf-8")
    listed = set(re.findall(r"`(docs/[A-Z-]+\.md)`", text))
    present = {f"docs/{p.name}" for p in (ROOT / "docs").glob("*" + ".md")}
    assert present <= listed, present - listed


# ------------------------------------------- установка на сервер и первый push

def _ops_readme() -> str:
    return (ROOT / "ops" / ("README" + ".md")).read_text(encoding="utf-8")


def _section(text: str, number: int) -> str:
    """Раздел `## N. …` документа до следующего раздела того же уровня."""
    return text.split(f"\n## {number}. ", 1)[1].split("\n## ", 1)[0]


def _steps(section: str) -> dict[int, str]:
    """Нумерованные шаги раздела: {номер: текст шага с его командами}."""
    parts = re.split(r"(?m)^(\d+)\. ", section)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2])}


def _commands(text: str) -> str:
    """Только команды: содержимое блоков ```bash … ``` (пояснения вокруг — не команды)."""
    return "\n".join(re.findall(r"```bash\n(.*?)```", text, re.S))


@pytest.mark.tact
def test_the_install_steps_are_complete_and_ordered():
    """Установка исполняется буквально и один раз: шаги стоят в порядке «код → настройки →
    ключ → ветка данных → проба → распознавание → юниты → первый выпуск → Pages данных →
    витрина и приёмка → таймеры → мост → замер», и у каждого необратимого есть проверка."""
    steps = _steps(_section(_ops_readme(), 5))
    order = ["git clone --single-branch --branch main", "/usr/local/etc/sber-850/env", "Deploy keys",
             "rulesets", "ops/publish.py --init", "ops/tools/probe.py'", "probe.py --only sber_ocr",
             "/etc/systemd/system/", "sber850-collect.service", "Settings → Pages",
             "pages deploy web --project-name tzi-850-sber --branch main", "systemctl enable --now",
             "dash-watch", "ops/budgets.json"]
    assert sorted(steps) == list(range(1, len(order) + 1)), sorted(steps)
    for number, mark in enumerate(order, 1):
        assert mark in steps[number], (number, mark)
    for path in ("/srv/dash/repo-850-sber", "/var/lib/sber-850", "/usr/local/etc/sber-850/env"):
        assert path in _section(_ops_readme(), 5), path


def test_the_install_never_overwrites_a_ready_env_file_and_keeps_the_token_out():
    """Готовый env-файл с токеном установка не перезаписывает (образец кладётся, только если
    файла нет); ручные команды слоя ops идут без токена — кроме пробы источников."""
    section = _section(_ops_readme(), 5)
    steps = _steps(section)
    assert "sudo test -e /usr/local/etc/sber-850/env || \\" in steps[2]
    helper = re.search(r"^ops850\(\) \{.*\}$", section, re.M)
    assert helper, "нет помощника ops850"
    assert ". /usr/local/etc/sber-850/env; set +a; unset TINVEST_TOKEN;" in helper.group(0)
    assert "ops850 .venv/bin/python ops/publish.py --init" in steps[5]
    assert "ops850 .venv/bin/python ops/tools/probe.py --only sber_ocr" in steps[7]
    with_token = [n for n, body in steps.items()
                  if re.search(r"\. /usr/local/etc/sber-850/env; set \+a;\n\s+cd ", body)]
    assert with_token == [6], "с токеном — только проба источников"
    assert "значения не выводятся" in steps[2] and "cat /usr/local/etc/sber-850/env" not in section


def test_the_units_step_checks_that_systemd_does_not_read_the_env_file():
    """Юниты env-файл не читают (решения В10, В20): шаг установки это проверяет, а текст
    объясняет, кто читает файл и кому достаётся секрет."""
    section = _section(_ops_readme(), 5)
    steps = _steps(section)
    assert "systemctl show -p EnvironmentFiles -p Environment sber850-daily.service" in steps[8]
    assert "`EnvironmentFiles=` пуст" in steps[8]
    assert "Файл\n   читает `ops/run.sh`" in steps[2] and "сборщику T-Invest" in steps[2]
    assert "EnvironmentFile=" not in _commands(section), "команды установки не вписывают EnvironmentFile"


def test_the_pages_step_deploys_into_the_existing_project():
    """Проект Pages создаётся один раз владельцем и уже создан: шаг проверяет его списком
    проектов и выкладывает витрину; ни ID аккаунта, ни токена Cloudflare в тексте нет."""
    step = _steps(_section(_ops_readme(), 5))[11]
    commands = _commands(step)
    assert commands.index("pages project list") < commands.index("pages deploy web --project-name tzi-850-sber")
    assert "уже\n    создан" in step and "производственная ветка `main`" in step
    assert "pages project create" not in _commands(_ops_readme())


def test_the_install_is_accepted_by_the_verify_line_and_the_code_mark():
    """Приёмка установки — не по коду первого такта: строка «СВЕРКА: выпуск … — пройдена …»
    в журнале, `release.commit` = коммит кода, отметка `release.pushed` снята. Шаблон
    grep из инструкции совпадает со строкой, которую печатает публикация."""
    steps = _steps(_section(_ops_readme(), 5))
    assert "кончится кодом 8 с одной причиной" in steps[9] and "боевая дверь не подтвердила" in steps[9]
    acceptance = _commands(steps[11])
    grep = re.search(r"grep '(СВЕРКА: [^']+)'", acceptance)
    assert grep, acceptance
    assert "/var/lib/sber-850/release.commit" in acceptance and "rev-parse HEAD" in acceptance
    assert "release.pushed" in acceptance
    # Текст экранов первого живого выпуска — той же проверкой, что CI гоняет на фикстуре.
    assert "node tests/web/web_release_text.mjs var/first-release.json" in acceptance
    assert (ROOT / "tests" / "web" / "web_release_text.mjs").is_file() and '`"failed": []`' in steps[11]
    source = (ROOT / "ops" / "publish.py").read_text(encoding="utf-8")
    printed = re.search(r'print\(f"(СВЕРКА: выпуск \{want_sha\[:12\]\} · \{want_pub\} — пройдена [^"]*)"', source)
    assert printed, "публикация не печатает строку удачной сверки"
    assert re.search(grep.group(1), printed.group(1)), (grep.group(1), printed.group(1))
    run_sh = (ROOT / "ops" / "run.sh").read_text(encoding="utf-8")
    assert "боевая дверь не подтвердила" in run_sh


def _tracked_top_level() -> set[str]:
    done = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    names = [n for n in done.stdout.decode("utf-8").split("\0") if n]
    return {n.split("/", 1)[0] for n in names}


def test_the_first_push_procedure_is_literal():
    """Первый push необратим, поэтому §12 — команды целиком: ветка прежней истории,
    `checkout --orphan`, ОЧИСТКА ИНДЕКСА (после orphan он несёт дерево прежнего коммита),
    явные пути (все верхние каталоги дерева), коммит, remote, push одной ссылки."""
    commands = _commands(_section(_ops_readme(), 12))
    order = ["git branch local-history-2026-10-01 main", "git checkout --orphan", "git read-tree --empty",
             "git add -- ", "git status --short", "git diff --cached --name-status --diff-filter=AD",
             "commit -q -m", "git branch -M main", "git rev-list --count main",
             "git remote add origin https://github.com/ML371KL/temp-zero-inode-850-sber.git",
             "git push origin main", "git ls-remote origin"]
    at = [commands.find(mark) for mark in order]
    assert all(x >= 0 for x in at), [m for m, x in zip(order, at) if x < 0]
    assert at == sorted(at), "шаги не по порядку"
    assert "git rm " not in commands, (
        "`git rm --cached` на ветке-сироте отказывает, когда в дереве есть незакоммиченные правки, — индекс "
        "очищает `git read-tree --empty`")
    assert "git add -A" not in commands and "--all" not in commands.replace("# без --all", "")
    assert "--mirror" not in commands.replace("--mirror и тегов", "") and "push --force" not in commands
    joined = commands.replace("\\\n", " ")                     # команда с переносом строки — одна строка
    [line] = [x.strip() for x in joined.splitlines() if x.strip().startswith("git add -- ")]
    added = line[len("git add -- "):].split()
    assert {a.split("/", 1)[0] for a in added} == _tracked_top_level(), (
        "в `git add` §12 названы не все верхние пути дерева (или лишние)")
    assert "var/.gitkeep" in added and "var" not in added, "из var/ в репозитории — только .gitkeep"
    assert ".github/commit-emails.allow" in commands, "адрес автора — из списка, не литералом"


def test_the_first_push_is_accepted_before_and_after_the_commit():
    """До коммита — весь набор, набор сервера на четырёх будущих датах и личные слова с
    итогом «1 passed»; после — приёмка по ссылке `main` тремя проверками удалённого.
    Шаблоны удалённого лежат в папке передачи: в публичном тексте их нет. КАЖДАЯ
    КОМАНДА ПЕЧАТАЕТ ИТОГ: «зелёный на всех четырёх датах» и «оборвался на первой»
    обязаны различаться строкой, а не хвостом вывода; второй `-q` (первый — в
    pytest.ini) убрал бы итоговую строку pytest."""
    section = _section(_ops_readme(), 12)
    commands = _commands(section)
    assert "for d in 1 7 30 60; do" in commands and 'FAKE_TODAY=$(date -u -d "+$d days" +%F)' in commands
    for told in ('echo "весь набор, кроме теста истории ветки: код $?"',
                 'echo "набор сервера на сегодня + $d дн.: код $rc"',
                 'echo "ИТОГ: набор сервера зелёный на $green датах из 4"',
                 'echo "история ветки и гигиена дерева: код $?"'):
        assert told in commands, told
    assert "|| break" not in commands, "цикл дат проходит все четыре и печатает итог"
    history = "test_the_history_of_the_branch_never_carried_raw_tinvest_answers"
    assert f"--deselect tests/test_ops_ci.py::{history}" in commands
    assert f"def {history}(" in (ROOT / "tests" / "test_ops_ci.py").read_text(encoding="utf-8")
    runs = [line for line in commands.replace("\\\n", " ").splitlines() if "-m pytest" in line]
    assert len(runs) == 5, runs
    for line in runs:
        assert "--basetemp=var/pytest-push" in line, f"свой каталог временных файлов: {line}"
        assert not re.search(r"\s-q\b", line), f"второй -q убирает итоговую строку pytest: {line}"
    assert "ИТОГ: набор сервера зелёный на 4 датах из 4" in section
    assert "не запускается ничего другого" in section and "`tests/support_git.py`" in section
    assert "git rev-list --count main; git status --short | wc -l" in commands, "после прогонов приёмки — снова 1 и 0"
    assert "такт: N с из 300 (цель сервера — 180 с)" in section
    assert """TACT=$(sed -n 's/^TACT_TESTS="\\(.*\\)"$/\\1/p' ops/run.sh)""" in commands
    assert commands.count("-rs -m archive tests/test_public_hygiene.py") == 2, "личные слова — до коммита и после"
    assert "«1 passed»" in section and "push не делать" in section
    for check in ('git ls-tree -r --name-only main -- $(cat "$A/removed-paths.txt")',
                  'git grep -n -E -f "$A/absent-patterns.txt" main',
                  """git grep -n -E -f "$A/source-urls.txt" main -- ':!data/'""",
                  'git grep -c -E -f "$A/source-urls.txt" main -- data/ | diff - "$A/source-urls.expected.txt"'):
        assert check in commands, check
    assert 'A="$H/private/push-acceptance"' in commands
    assert 'read -r -p "папка передачи (вне репозитория): " H' in commands, "путь к папке передачи в текст не вписан"
    assert "<папка передачи>" not in commands, "в командах §12 нет подстановок: они исполняются буквально"
    for leaked in ("interfax", "kommersant", "t.me/", "MGNT"):
        assert leaked not in section, f"шаблон удалённого в публичном тексте: {leaked}"


def test_the_decisions_state_what_is_and_is_not_deployed():
    """Журнал решений не утверждает несделанного: после W2 репозиторий не выложен; решения
    второго аудита и разрешение владельца записаны с датой, без путей и значений."""
    text = (ROOT / "docs" / ("DECISIONS" + ".md")).read_text(encoding="utf-8")
    assert "исправлен и\nвыложен на сервер" not in text and "выложен на сервер до релиза" not in text
    part7 = text.split("## Часть 7.", 1)[1].split("\n## Часть 8.", 1)[0]
    assert "не выложен" in part7 and "Push репозитория кода не делался" in part7
    for decision in ("В12", "В13", "В14", "В15", "В16", "В17", "В18", "В19", "В20"):
        assert f"| {decision} |" in part7, decision
    assert "Разрешение владельца (01.10.2026)" in part7 and "справочник владельца" in part7
    assert "`keep`" in part7 and "`explanation_expiring`" in part7 and "`policy_expired`" in part7
    assert "local-history-2026-10-01" in part7


def test_the_decisions_before_the_first_push_are_recorded():
    """Решения последней волны перед push (P1–P7) записаны, состояние названо как есть,
    исполнение называет настоящие файлы; части журнала датированы днём, когда решения
    приняты (все волны прошли 01.10.2026), и идут по порядку."""
    text = (ROOT / "docs" / ("DECISIONS" + ".md")).read_text(encoding="utf-8")
    part9 = text.split("## Часть 9.", 1)[1].split("\n## Часть 10.", 1)[0]
    part10 = text.split("## Часть 10.", 1)[1].split("\n## Открытые вопросы", 1)[0]
    for decision in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        assert f"| {decision} |" in part9, decision
    assert "не выложен" in part9 and "корневым\nкоммитом дерева после W3b" in part9
    assert "`GIT_CEILING_DIRECTORIES`" in part9 and "300 с" in part9
    assert "`valuation.uncertainty.median_refine` = 2" in part9
    for named in ("`tests/support_git.py`", "`tests/conftest.py`", "`tests/test_ops_git_sandbox.py`",
                  "`tact_tests_ci_ceiling`", "`tact_tests_target`", "код 78", "`--basetemp`"):
        assert named in part10, named
    budgets = (ROOT / "ops" / "budgets.json").read_text(encoding="utf-8")
    assert '"tact_tests_target": 180' in budgets and '"tact_tests_ci_ceiling": 300' in budgets
    headings = re.findall(r"^## Часть (\d+)\. .*?\((\d{2}\.\d{2}\.\d{4})\)", text, re.M)
    numbers = [int(n) for n, _ in headings]
    # части 1–10 — до первого push; после выкладки журнал продолжается (номера и даты — по порядку)
    assert numbers[:10] == list(range(1, 11)) and numbers == sorted(set(numbers)), headings
    days = [tuple(reversed(day.split("."))) for _, day in headings]
    assert days == sorted(days), f"части журнала решений идут не по датам: {headings}"
    changelog = (ROOT / "docs" / ("CHANGELOG" + ".md")).read_text(encoding="utf-8")
    waves = re.findall(r"^## Волна исправлений (W\S+) \((\d{2}\.\d{2}\.\d{4})\)", changelog, re.M)
    assert [w for w, _ in waves] == ["W3b", "W3", "W2", "W1"], waves
    assert len({day for _, day in waves}) == 1, f"волны прошли в один день: {waves}"


def test_the_manual_tells_what_expires_and_what_to_do():
    """Сроки данных — плашки, а не тревога: справочник называет три исхода сборки, флаги,
    что делать с каждым сроком, и режим перезаякоривания."""
    text = (ROOT / "docs" / ("MANUAL" + ".md")).read_text(encoding="utf-8")
    terms = text.split("### 10.9.", 1)[1].split("\n---", 1)[0]
    for word in ("`explanation_expiring`", "`policy_expired`", "`manual_input_overdue`", "`deadline`",
                 "gate_explanations.yaml", "data/facts/dividends.json"):
        assert word in terms, word
    outcomes = text.split("### 4.3.", 1)[1].split("### 4.4.", 1)[0]
    assert all(word in outcomes for word in ("**отказ** (код 1)", "**тревога** (код 3", "**плашка** (код 0)"))
    assert "истекает через 30 дней или раньше, входы деградировали" not in text, "истекающее объяснение — не тревога"
    assert "Режим передачи ставки — `keep`" in text.split("### 10.4.", 1)[1].split("### 10.5.", 1)[0]
