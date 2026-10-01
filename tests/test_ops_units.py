# -*- coding: utf-8 -*-
"""Юниты systemd, потолки времени, образец env-файла, .gitignore и .gitattributes.

Юниты ставятся на сервер побайтовыми копиями из `ops/systemd/`, поэтому их
свойства проверяются здесь, до установки: шаблон защиты панелей владельца,
политика перезапуска (тревога — код 8 — не повторяется ни у одного такта, провал
шага — повторяется в пределах окна `StartLimitIntervalSec`, выведенного из
бюджета), расписание (DESIGN §6.1) и потолки `RuntimeMaxSec`, посчитанные из
`ops/budgets.json` по одной формуле с `LOCK_WAIT_SECONDS` в `ops/run.sh`; повтор
сбора внутри утреннего такта и порог сторожа объёма ветки данных — оттуда же.

Быстрые (доли секунды) — в такте сервера (метка `tact` модуля): юниты, бюджеты и
env-файл проверяются и на том коде, который пересборка вот-вот опубликует;
тесты документов (`docs`) такт исключает выражением.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops"
UNITS = OPS / "systemd"
MODES = ("collect", "release-watch", "daily", "rebuild")
REPO = "/srv/dash/repo-850-sber"
ENV_PATH = "/usr/local/etc/sber-850/env"
MSK_OFFSET_HOURS = 3

# INTERFACES §8: переменные, которые конвейер берёт из env-файла сервера. Ядро и
# индикаторы читают свои без префикса банка (BANK_*, решение ведущего 01.10.2026,
# C5); у переменных только эксплуатации — префикс банка (слой ops у банка свой).
CORE_VARIABLES = {"BANK_STATE_DIR", "BANK_DATA_REPO_DIR", "BANK_WORKERS"}
OPS_VARIABLES = CORE_VARIABLES | {
    "SBER_REPO_DIR", "SBER_VENV", "SBER_PYTHON",
    "TINVEST_TOKEN", "SBER_RELEASE_REMOTE", "SBER_GIT_NAME", "SBER_GIT_EMAIL", "SBER_PUBLIC_URL",
    "SBER_VERIFY_TIMEOUT", "SBER_VERIFY_PAUSE", "SBER_PUSH_BACKOFF",
    "SBER_FUTURE_WEEKDAY", "SBER_FUTURE_DAYS",
}
# Служебные: проба тревоги и метка перезапуска run.sh (не из env-файла), папка
# передачи (на сервере не задаётся).
SERVICE_VARIABLES = {"SBER_SIMULATE_FAILURE", "SBER_REEXEC", "BANK_HANDOFF_DIR"}
VARIABLE = r"(?:SBER|BANK)_[A-Z_]+"


def _unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """Юнит как {секция: {ключ: [значения]}} — ключ может повторяться."""
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        key, _, value = line.partition("=")
        assert current is not None, f"{path.name}: {line!r} вне секции"
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def _one(unit: dict, section: str, key: str) -> str:
    values = unit.get(section, {}).get(key, [])
    assert len(values) == 1, f"{section}.{key}: {values}"
    return values[0]


def _budgets() -> dict:
    return json.loads((OPS / "budgets.json").read_text(encoding="utf-8"))


def _ceilings() -> tuple[dict[str, int], int]:
    """Потолки юнитов и ожидание замка по ops/budgets.json (формула — поле about)."""
    b = _budgets()
    step = int(b["round_to"])

    def run(unit: str) -> int:
        total = sum(b["seconds"][k] for k in b["units"][unit])
        return math.ceil(total * b["safety"] / step) * step

    lock = max(run(u) for u in b["lock_holders"])
    return {u: run(u) + (lock if u in b["lock_waiters"] else 0) for u in b["units"]}, lock


def _restart_windows() -> dict[str, int]:
    """Окно перезапуска юнитов с Restart по ops/budgets.json (формула — поле about)."""
    b = _budgets()
    r, step = b["restart"], int(b["round_to"])
    ceilings, _ = _ceilings()
    return {u: math.ceil(r["burst"] * (ceilings[u] + r["pause_s"] + r["stop_s"]) / step) * step
            for u in r["units"]}


def _publish():
    spec = importlib.util.spec_from_file_location("publish_for_units", OPS / "publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_there_are_exactly_four_pairs_of_units():
    names = sorted(p.name for p in UNITS.iterdir())
    assert names == sorted(f"sber850-{m}.{kind}" for m in MODES for kind in ("service", "timer"))
    for path in UNITS.iterdir():
        assert b"\r\n" not in path.read_bytes(), f"{path.name}: CRLF"


@pytest.mark.parametrize("mode", MODES)
def test_every_service_follows_the_panel_template(mode):
    unit = _unit(UNITS / f"sber850-{mode}.service")
    assert "Install" not in unit, "юнит запускает таймер; [Install] дал бы прогон при загрузке"
    expected = {
        "Type": "exec", "User": "dash", "Group": "dash",
        "WorkingDirectory": REPO,
        "ExecStart": f"/bin/bash {REPO}/ops/run.sh {mode}",
        "SyslogIdentifier": "sber850", "StateDirectory": "sber-850",
        "PrivateTmp": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
        "NoNewPrivileges": "yes", "ReadWritePaths": REPO,
        "MemoryHigh": "384M", "MemoryMax": "512M", "Nice": "10",
        "ExecStopPost": "-+/usr/local/sbin/dash-alert %n",
    }
    for key, value in expected.items():
        assert _one(unit, "Service", key) == value, key
    assert unit["Service"]["Environment"] == [f"ENV_FILE={ENV_PATH}", "PYTHONUNBUFFERED=1"]
    assert _one(unit, "Unit", "Wants") == "network-online.target"
    assert _one(unit, "Unit", "After") == "network-online.target"


def test_the_units_do_not_load_the_env_file_into_their_environment():
    """Токен T-Invest получает только процесс сборщика T-Invest (INTERFACES §8): env-файл
    читает сам run.sh и тут же снимает токен из окружения такта. `EnvironmentFile` в юните
    положил бы токен в окружение всего юнита — и такта, и моста тревог."""
    for mode in MODES:
        service = _unit(UNITS / f"sber850-{mode}.service")["Service"]
        assert "EnvironmentFile" not in service, mode
        assert not any("TOKEN" in value for value in service.get("Environment", [])), mode
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    assert f"ENV_FILE=${{ENV_FILE:-{ENV_PATH}}}" in script, "путь юнита и умолчание run.sh — один файл"


def test_only_the_morning_tact_is_cut_off_from_the_deploy_key():
    """Утренний такт разбирает чужой HTML (PDF разбирает дозор) и ничего не публикует —
    ключ записи ему недоступен. Дозору, суточному такту и пересборке каталог нужен.
    Это наименьшие права, а не граница доверия (тот же пользователь пишет в код)."""
    for mode in MODES:
        hidden = _unit(UNITS / f"sber850-{mode}.service")["Service"].get("InaccessiblePaths", [])
        if mode == "collect":
            assert hidden == ["/srv/dash/.ssh"]
        else:
            assert not any(".ssh" in h for h in hidden), mode


def test_the_restart_policy_repeats_failures_and_not_alarms():
    """Политика тревог (INTERFACES §6): тревога (8) не повторяется ни у одного такта —
    повтор её не снимет; провал шага (1) и занятый замок (75) повторяются у утреннего и
    суточного тактов. Дозор и пересборка не повторяются: их следующая проверка — через
    10–15 минут. Числа — из ops/budgets.json (restart), а не литералами теста."""
    restart = _budgets()["restart"]
    assert set(restart["units"]) == {"collect", "daily"}
    assert {8, 64, 78} <= set(restart["no_restart_codes"]), "тревога и ошибки настройки не повторяются"
    assert not {1, 75} & set(restart["no_restart_codes"]), "провал шага и занятый замок повторяются"
    for mode in restart["units"]:
        service = _unit(UNITS / f"sber850-{mode}.service")["Service"]
        assert service["Restart"] == ["on-failure"], mode
        assert service["RestartSec"] == [str(restart["pause_s"])], mode
        prevent = [int(x) for x in _one({"Service": service}, "Service", "RestartPreventExitStatus").split()]
        assert prevent == restart["no_restart_codes"], mode

    for mode in set(MODES) - set(restart["units"]):
        service = _unit(UNITS / f"sber850-{mode}.service")
        assert "Restart" not in service["Service"] and "RestartPreventExitStatus" not in service["Service"], mode
        assert "StartLimitIntervalSec" not in service["Unit"], mode


def test_the_restart_window_holds_every_allowed_start():
    """Окно systemd отсчитывается от первого старта и не скользит: отказ получает только
    старт сверх `StartLimitBurst` ВНУТРИ окна. Окно короче, чем burst прогонов с паузами,
    не ограничивает ничего — стойкий код 1 повторялся бы весь день (десятки полных сборов
    ЦБ, T-Invest, ISS и сборок на общем сервере). Поэтому окно выводится из потолка
    прогона, а не пишется на глаз."""
    restart, windows = _budgets()["restart"], _restart_windows()
    ceilings, _ = _ceilings()
    for mode, window in windows.items():
        unit = _unit(UNITS / f"sber850-{mode}.service")["Unit"]
        assert unit["StartLimitBurst"] == [str(restart["burst"])], mode
        assert unit["StartLimitIntervalSec"] == [str(window)], (
            f"sber850-{mode}.service: StartLimitIntervalSec обязан быть {window} по ops/budgets.json")
        longest = restart["burst"] * (ceilings[mode] + restart["pause_s"] + restart["stop_s"])
        assert window >= longest, (mode, "окно короче burst самых долгих прогонов")
        assert window < 24 * 3600, (mode, "окно длиннее суток заперло бы и завтрашний запуск по таймеру")
    assert restart["burst"] >= 2 and restart["stop_s"] >= 90, "stop_s — предел ExecStopPost (TimeoutStopSec)"


def _starts_within(run_s: int, pause_s: int, burst: int, window: int, horizon: int) -> int:
    """Сколько раз systemd запустит юнит за horizon секунд, если каждый прогон длится run_s
    и кончается кодом 1 (модель ratelimit: окно — от первого старта в нём, не скользит)."""
    starts, t, begin, count = 0, 0, None, 0
    while t < horizon:
        if begin is None or t - begin >= window:
            begin, count = t, 0
        if count >= burst:
            break                                   # start-limit-hit: юнит стоит до таймера
        count += 1
        starts += 1
        t += run_s + pause_s
    return starts


def test_a_persistent_failure_is_retried_at_most_burst_times_a_day():
    """Модель того, что было не так: при окне 1800 с прогон дольше ≈ 7 минут повторялся
    без предела. С окном из бюджета стойкий провал даёт не больше burst стартов при любой
    длительности прогона вплоть до потолка."""
    restart, windows = _budgets()["restart"], _restart_windows()
    ceilings, _ = _ceilings()
    day = 24 * 3600
    assert _starts_within(900, 180, 3, 1800, day) > 50, "модель обязана воспроизводить прежний дефект"
    for mode, window in windows.items():
        for run_s in (5, 60, 420, 900, 3000, ceilings[mode] + restart["stop_s"]):
            got = _starts_within(run_s, restart["pause_s"], restart["burst"], window, day)
            assert got <= restart["burst"], (mode, run_s, got)


def test_the_collect_retry_of_run_sh_follows_the_budget():
    """Повтор сбора внутри утреннего такта: число попыток и пауза в run.sh — из
    ops/budgets.json (collect_retry), а время повторов входит в потолок юнита."""
    b = _budgets()
    retry = b["collect_retry"]
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    assert re.findall(r"^COLLECT_RETRY_ATTEMPTS=(\d+)$", script, re.M) == [str(retry["attempts"])]
    assert re.findall(r"^COLLECT_RETRY_PAUSE=(\d+)$", script, re.M) == [str(retry["pause_s"])]
    assert 2 <= retry["attempts"] <= 5 and retry["pause_s"] >= 60
    assert b["seconds"]["collect_retries"] == (retry["attempts"] - 1) * (
        b["seconds"]["collect_morning"] + retry["pause_s"])
    assert "collect_retries" in b["units"]["collect"]
    assert all("collect_retries" not in steps for unit, steps in b["units"].items() if unit != "collect")


def test_the_data_branch_alarm_threshold_is_in_the_budget():
    """Сторож объёма ветки данных (INTERFACES §6): порог — 700 МБ при пределе сайта
    GitHub Pages 1 ГБ; `ops/publish.py` читает его из бюджета."""
    publish = _publish()
    assert _budgets()[publish.ALERT_KEY] == 700
    assert publish.alert_bytes(publish.Config(state_dir=ROOT / "var" / "state")) == 700 * 1024 * 1024
    assert publish.BUDGETS == OPS / "budgets.json"


def test_run_sh_ends_alarms_with_the_code_the_units_do_not_repeat():
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    finish = re.search(r"^finish\(\) \{(.*?)^\}", script, re.M | re.S).group(1)
    assert "exit 8" in finish and "ТРЕВОГА (код 8)" in finish and "готово:" in finish
    assert "ПРОВАЛ на шаге «$STEP»" in script


@pytest.mark.parametrize("mode,calendars,persistent", [
    ("collect", ["*-*-* 04:50:00 UTC"], "true"),
    ("daily", ["Mon..Fri *-*-* 18:10:00 UTC"], "true"),
    ("rebuild", ["*:12,27,42,57"], None),
    ("release-watch", ["Mon..Fri *-*-* 06:45/10:00 UTC", "Mon..Fri *-*-* 07..09:05/10:00 UTC"], None),
])
def test_the_timers_follow_the_schedule(mode, calendars, persistent):
    timer = _unit(UNITS / f"sber850-{mode}.timer")
    assert timer["Timer"]["OnCalendar"] == calendars
    assert _one(timer, "Timer", "Unit") == f"sber850-{mode}.service"
    assert timer["Timer"].get("Persistent", [None]) == [persistent]
    assert _one(timer, "Install", "WantedBy") == "timers.target"


def _ticks(calendar: str) -> list[tuple[int, int]]:
    """Минуты срабатывания для вида `Mon..Fri *-*-* H[..H2]:MM/10:00 UTC` (часы UTC)."""
    m = re.fullmatch(r"Mon\.\.Fri \*-\*-\* (\d{2})(?:\.\.(\d{2}))?:(\d{2})/(\d+):00 UTC", calendar)
    assert m, calendar
    h1, h2 = int(m.group(1)), int(m.group(2) or m.group(1))
    return [(h, minute) for h in range(h1, h2 + 1) for minute in range(int(m.group(3)), 60, int(m.group(4)))]


def test_the_release_watch_ticks_cover_the_window_of_the_indicators():
    """Окно дозора в таймере = окно `schedule.watch_window_msk` конфигурации
    сборщиков: первая проверка — в начале окна, последняя — до его конца, шаг 10 минут."""
    cfg = yaml.safe_load((ROOT / "data" / "indicators" / "sources.yaml").read_text(encoding="utf-8"))
    start, end = cfg["schedule"]["watch_window_msk"]
    to_min = lambda hhmm: int(hhmm[:2]) * 60 + int(hhmm[3:]) - MSK_OFFSET_HOURS * 60  # noqa: E731
    timer = _unit(UNITS / "sber850-release-watch.timer")
    ticks = sorted(h * 60 + m for c in timer["Timer"]["OnCalendar"] for h, m in _ticks(c))
    assert ticks[0] == to_min(start) and ticks[-1] < to_min(end) <= ticks[-1] + 10
    assert all(b - a == 10 for a, b in zip(ticks, ticks[1:])), ticks


def test_runtime_ceilings_are_computed_from_the_budget_file():
    """Потолок юнита — из бюджета, а не на глаз: потолок ниже настоящего времени
    убил бы такт на середине (урок 850oa)."""
    ceilings, _ = _ceilings()
    for mode in MODES:
        unit = _unit(UNITS / f"sber850-{mode}.service")
        assert int(_one(unit, "Service", "RuntimeMaxSec")) == ceilings[mode], (
            f"sber850-{mode}.service: RuntimeMaxSec обязан быть {ceilings[mode]} по ops/budgets.json")


def test_the_lock_wait_equals_the_longest_lock_holder():
    """Плановый такт ждёт замок столько, сколько пересборка или дозор со сборкой
    могут его держать."""
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    found = re.findall(r"^LOCK_WAIT_SECONDS=(\d+)$", script, re.M)
    assert found == [str(_ceilings()[1])]


def test_the_budget_file_is_honest_about_itself():
    b = _budgets()
    assert b["status"] in ("estimate", "measured")
    if b["status"] == "measured":
        date.fromisoformat(b["measured_on"])
    else:
        assert b["measured_on"] is None
    assert set(b["units"]) == set(MODES)
    assert set(b["lock_holders"]) == {"rebuild", "release-watch"}
    assert set(b["lock_waiters"]) == {"collect", "daily"}
    for unit, steps in b["units"].items():
        assert steps and all(s in b["seconds"] for s in steps), unit
    assert b["seconds"]["tact_tests"] <= b["tact_tests_target"] == 180, (
        "тесты такта дольше 3 минут: сократить белый список (метка tact)")
    assert b["tact_tests_target"] < b["tact_tests_ci_ceiling"] == 300, (
        "потолок шага CI — 300 с: раннер с двумя ядрами медленнее сервера; цель сервера сверяет замер")
    assert b["seconds"]["verify_timeout"] == _publish().VERIFY_TIMEOUT, "окно сверки одно"
    assert all("pip" in steps for steps in b["units"].values())


def _env_example() -> tuple[dict[str, str], set[str]]:
    """(активные строки KEY=value, имена в закомментированных строках `# KEY=`)."""
    active, commented = {}, set()
    for line in (OPS / "env.example").read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"# ([A-Z][A-Z0-9_]+)=", line)
        if m:
            commented.add(m.group(1))
        if line and not line.startswith("#"):
            key, sep, value = line.partition("=")
            assert sep and key == key.strip() and re.fullmatch(r"[A-Z][A-Z0-9_]+", key), line
            active[key] = value
    return active, commented


def test_the_env_example_carries_names_only():
    """Образец — только имена (репозиторий публичный): ни одного значения, ни
    одного секрета; все переменные конвейера названы."""
    active, commented = _env_example()
    assert all(v == "" for v in active.values()), {k: v for k, v in active.items() if v}
    assert OPS_VARIABLES <= set(active) | commented, OPS_VARIABLES - set(active) - commented
    assert (set(active) | commented) <= OPS_VARIABLES, (set(active) | commented) - OPS_VARIABLES
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    required = set(re.findall(rf'\[\[ -n \$\{{({VARIABLE}):-\}} \]\] \|\| \{{ echo "в \$ENV_FILE не задан', script))
    assert required == {"SBER_REPO_DIR", "BANK_STATE_DIR"} and required <= set(active)
    for key in ("TINVEST_TOKEN", "SBER_RELEASE_REMOTE", "SBER_GIT_NAME", "SBER_GIT_EMAIL"):
        assert key in active, f"{key}: без строки оператор её не впишет"


def test_the_ops_code_reads_only_documented_variables():
    """Код ops, образец env-файла и юниты называют только переменные INTERFACES
    §8 (и служебные метки run.sh); прежних имён ядра с префиксом банка
    (`SBER_STATE_DIR`, `SBER_DATA_REPO_DIR`, `SBER_WORKERS`, `SBER_HANDOFF_DIR`) нет."""
    files = [p for p in OPS.rglob("*") if p.is_file() and "__pycache__" not in p.parts
             and (p.suffix in (".py", ".sh", ".service", ".timer", ".json") or p.name == "env.example")]
    used = {}
    for path in files:
        for name in re.findall(rf"\b({VARIABLE})\b", path.read_text(encoding="utf-8")):
            used.setdefault(name, set()).add(path.relative_to(ROOT).as_posix())
    allowed = OPS_VARIABLES | SERVICE_VARIABLES
    extra = {k: sorted(v) for k, v in used.items() if k not in allowed}
    assert not extra, extra
    for old in ("SBER_" + name.split("_", 1)[1] for name in CORE_VARIABLES | {"BANK_HANDOFF_DIR"}):
        assert old not in used, old


@pytest.mark.parametrize("path,ignored", [
    ("payload.json", True), ("release/latest.json", True), ("out/report.json", True),
    ("port-check/work/x.py", True), ("indicators/journal.sqlite", True),
    ("report.pdf", True), ("data/facts/databook.xlsx", True), (".wrangler/cache/x", True),
    ("var/state/x.json", True), ("var/pytest-tmp/x", True), ("var/.gitkeep", False),
    ("tests/fixtures/ind/synthetic.pdf", False), ("ops/run.sh", False), (".venv/x", True),
])
def test_the_gitignore_keeps_state_primary_documents_and_wrangler_out(path, ignored):
    """Первичка, состояние и каталог wrangler (ID аккаунта, почта) не попадают в
    дерево даже по `git add` каталога; синтетические фикстуры — попадают."""
    done = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=str(ROOT))
    assert done.returncode in (0, 1), done
    assert (done.returncode == 0) == ignored, path


def test_shell_scripts_and_units_are_checked_out_with_lf():
    done = subprocess.run(["git", "check-attr", "eol", "--", "ops/run.sh", "ops/systemd/sber850-daily.service"],
                          cwd=str(ROOT), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("eol: lf") == 2, done.stdout


@pytest.mark.docs
def test_the_ops_readme_names_codes_units_settings_and_commands():
    """ops/README.md — рабочий документ: коды юнита, юниты, настройки из образца,
    команды отката и выкладки витрины совпадают с кодом."""
    text = (OPS / "README.md").read_text(encoding="utf-8")
    for code in ("0", "1", "8", "64", "75", "78"):
        assert re.search(rf"^\| {code} \|", text, re.M), f"код {code} не описан"
    for mode in MODES:
        assert f"sber850-{mode}" in text
    active, commented = _env_example()
    for key in set(active) | commented:
        assert key in text, f"{key} из ops/env.example не описан"
    assert "ops/run.sh rollback <sha" in text
    wrangler = re.search(r"npx wrangler@([\d.]+) pages deploy web --project-name tzi-850-sber --branch main",
                         (ROOT / "wrangler.toml").read_text(encoding="utf-8")).group(1)
    assert f"npx wrangler@{wrangler} pages deploy web --project-name tzi-850-sber --branch main" in text
    for line in ("готово:", "ТРЕВОГА (код 8)", "ПРОВАЛ на шаге"):
        assert line in text, line


@pytest.mark.docs
def test_the_interfaces_list_the_same_ops_variables():
    """Таблица INTERFACES §8 и образец env-файла называют одни и те же переменные."""
    text = (ROOT / "docs" / "INTERFACES.md").read_text(encoding="utf-8")
    section = text.split("## 8.", 1)[1].split("\n## ", 1)[0]
    named = set(re.findall(r"`((?:SBER|BANK|TINVEST)_[A-Z_]+)`", section))
    assert OPS_VARIABLES | SERVICE_VARIABLES <= named, (OPS_VARIABLES | SERVICE_VARIABLES) - named
