# -*- coding: utf-8 -*-
"""Конвейер `ops/run.sh` в песочнице: коды такта, порядок шагов, замок,
перезапуск новым скриптом, зависимости по отметке, дозор, пересборка и откат,
политика тревог (повтор сбора только при отказе невосполнимого источника) и
токен T-Invest только у команды сборщика T-Invest.

Тревоги владельцу доставляет ОДИН канал — код возврата юнита: `ExecStopPost`
зовёт общий мост dash-alert, и тот шлёт смену состояния по коду. Проверяется
поведение, а не строки скрипта: настоящий `ops/run.sh` запускается с подставным
питоном, который возвращает заказанные коды, и спрашивается то, что увидит
systemd, — код такта, — и то, что увидит владелец в журнале: причина в
последней строке. Песочница — git во временном каталоге; сети нет.

Коды (ops/run.sh, шапка): 0 — прошло; 1 — провал шага, выпуска нет; 8 —
тревога при выполненной работе; 64 / 75 / 78 — режим / замок / env.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.support_git import sandbox_clone, sandbox_env, sandbox_git, sandbox_init

ROOT = Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "ops" / "run.sh"
BASH = shutil.which("bash")
TACT = "tact and not network and not archive and not docs and not ci_only and not deadline"
# Без второго -q (первый — в pytest.ini): журнал такта несёт итоговую строку pytest.
TACT_CALL = f"-m pytest -m {TACT}"

# Подставной питон: печатает, чем его позвали, и возвращает заказанный код.
# Публикация и сверка ведут себя как `ops/publish.py` в том, что видит run.sh:
# публикация пишет отметку release.pushed с кодом выпуска, удачная сверка
# переносит этот код в release.commit и снимает отметку; откат переносит код
# висящего выпуска в release.commit и перезаписывает отметку своей. Тихая
# проверка окна дозора ничего не печатает (как настоящая) — вызов пишется в файл.
# Команда, в окружении которой есть токен T-Invest, называет себя строкой
# «ТОКЕН У: …» (значение не печатается). Сбор: команда сборщика T-Invest
# отвечает STUB_TINVEST_RC, остальные — STUB_COLLECT_RC или очередным кодом из
# STUB_COLLECT_SEQ (последний повторяется) — так заказывается «с третьей попытки».
TACT_STUB = r"""#!/usr/bin/env bash
state=${BANK_STATE_DIR:?}
case "$*" in
  "ops/tact.py window")
    echo "window${TINVEST_TOKEN:+ с токеном}" >> "$state/window.calls"; exit "${STUB_WINDOW_RC:-1}";;
esac
echo "ВЫЗОВ: $*"
if [[ -n ${TINVEST_TOKEN:-} ]]; then echo "ТОКЕН У: $*"; fi
case "$*" in
  *"--only tinvest"*)              exit "${STUB_TINVEST_RC:-0}";;
  *"indicators.collect collect"*|*"indicators.collect daily-indicators"*|*"indicators.collect release-watch"*)
    if [[ -n ${STUB_COLLECT_SEQ:-} ]]; then
      echo x >> "$state/collect.calls"
      read -r -a seq <<< "$STUB_COLLECT_SEQ"
      n=$(wc -l < "$state/collect.calls")
      if (( n > ${#seq[@]} )); then n=${#seq[@]}; fi
      exit "${seq[n-1]}"
    fi
    exit "${STUB_COLLECT_RC:-0}";;
  *"ops/tact.py retry"*)           exit "${STUB_RETRY_RC:-1}";;
  *"indicators.collect health"*)   exit "${STUB_HEALTH_RC:-0}";;
  *"indicators.collect status"*)   exit "${STUB_STATUS_RC:-0}";;
  *"indicators.collect nowcast"*)  exit "${STUB_NOWCAST_RC:-0}";;
  *"model.build_release"*)         exit "${STUB_BUILD_RC:-0}";;
  *"-m pip install"*)              exit "${STUB_PIP_RC:-0}";;
  *"-m pytest"*)
    if [[ -n ${FAKE_TODAY:-} ]]; then echo "FAKE_TODAY=$FAKE_TODAY"; exit "${STUB_FUTURE_RC:-0}"; fi
    exit "${STUB_PYTEST_RC:-0}";;
  *"ops/tact.py news"*)            exit "${STUB_NEWS_RC:-1}";;
  *"ops/tact.py done"*)            exit 0;;
  *"ops/tact.py simulate"*)        exit "${STUB_SIMULATE_RC:-0}";;
  *"ops/publish.py --verify"*)
    rc=${STUB_VERIFY_RC:-0}
    if [[ $rc == 0 && -f $state/release.pushed ]]; then
      code=$(sed -n 's/^ *"code_commit": *"\([0-9a-f]*\)".*/\1/p' "$state/release.pushed")
      if [[ -n $code ]]; then echo "$code" > "$state/release.commit"; fi
      rm -f "$state/release.pushed"
    fi
    exit "$rc";;
  *"ops/publish.py --sync"*)       exit "${STUB_SYNC_RC:-0}";;
  *"ops/publish.py --rollback"*)
    rc=${STUB_ROLLBACK_RC:-0}
    if [[ $rc == 0 && -f $state/release.pushed ]]; then
      code=$(sed -n 's/^ *"code_commit": *"\([0-9a-f]*\)".*/\1/p' "$state/release.pushed")
      if [[ -n $code ]]; then echo "$code" > "$state/release.commit"; fi
      printf '{\n "kind": "rollback",\n "code_commit": ""\n}\n' > "$state/release.pushed"
    fi
    exit "$rc";;
  *"ops/publish.py"*)
    rc=${STUB_PUBLISH_RC:-0}
    if [[ $rc == 0 || $rc == 3 ]]; then
      printf '{\n "kind": "publish",\n "code_commit": "%s"\n}\n' "$(git rev-parse HEAD)" > "$state/release.pushed"
    fi
    exit "$rc";;
esac
exit 0
"""

FLOCK_STUB = """#!/usr/bin/env bash
# Подставной flock: печатает, чем его позвали, и отвечает заказанным кодом.
echo "FLOCK: $*"
case "$1" in
  -n) exit "${STUB_FLOCK_N_RC:-0}";;
  -w) exit "${STUB_FLOCK_W_RC:-0}";;
esac
exit 0
"""

# Подставной sleep: пауза между попытками сбора в песочнице не ждёт.
SLEEP_STUB = """#!/usr/bin/env bash
echo "SLEEP: $*"
exit 0
"""

REQUIREMENTS = "PyYAML==6.0.3\n"
SANDBOX_TOKEN = "token-of-the-sandbox"        # не токен: значение, которое обязано нигде не всплыть


def _quoted(path) -> str:
    # Косые черты вперёд и кавычки: env-файл читается шеллом через `.`, и в
    # windows-пути обратная косая была бы экранированием.
    return "'" + str(path).replace("\\", "/") + "'"


def _git(*args, repo) -> str:
    """Git песочницы: репозиторий — ровно в `repo`, во временном каталоге теста; команда
    не поднимается к объемлющему репозиторию и перед записью сверяет свой каталог
    (tests/support_git.py)."""
    return sandbox_git(*args, repo=repo, config=("user.email=t@t.invalid", "user.name=t"))


class Box:
    """Песочница такта: `origin` (туда «пушит» владелец), клон сервера `work`
    с настоящим ops/run.sh, каталог состояния, venv и подставной питон вне дерева."""

    def __init__(self, tmp_path: Path, files: dict[str, str] | None = None):
        assert BASH, "ops/run.sh — боевой конвейер панели; без bash его нечем проверить"
        self.tmp = tmp_path
        self.origin, self.work = tmp_path / "origin", tmp_path / "work"
        self.state, self.venv = tmp_path / "state", tmp_path / "venv"
        self.state.mkdir(parents=True)
        self.venv.mkdir()
        files = {"ops/run.sh": RUN_SH.read_text(encoding="utf-8"),
                 "requirements.txt": REQUIREMENTS,
                 "model/core.py": "x = 1\n", "docs/MANUAL.md": "справочник\n",
                 **(files or {})}
        for name, text in files.items():
            (self.origin / name).parent.mkdir(parents=True, exist_ok=True)
            (self.origin / name).write_text(text, encoding="utf-8", newline="\n")
        sandbox_init(self.origin, "-q", "-b", "main")
        _git("add", *files, repo=self.origin)
        _git("commit", "-q", "-m", "начало", repo=self.origin)
        sandbox_clone(self.origin, self.work, "-q", "-b", "main")
        self.stub = tmp_path / "stub-python"
        self.stub.write_text(TACT_STUB, encoding="utf-8", newline="\n")
        self.stub.chmod(0o755)
        self.bin = tmp_path / "stub-bin"
        self.bin.mkdir()
        (self.bin / "sleep").write_text(SLEEP_STUB, encoding="utf-8", newline="\n")
        (self.bin / "sleep").chmod(0o755)
        self.env_file = tmp_path / "env"
        self.env_file.write_text(
            f"SBER_REPO_DIR={_quoted(self.work)}\nBANK_STATE_DIR={_quoted(self.state)}\n"
            f"SBER_VENV={_quoted(self.venv)}\nSBER_PYTHON={_quoted(self.stub)}\n"
            f"TINVEST_TOKEN={SANDBOX_TOKEN}\n",
            encoding="utf-8", newline="\n")

    def commit(self, path: str, text: str) -> str:
        (self.origin / path).parent.mkdir(parents=True, exist_ok=True)
        (self.origin / path).write_text(text, encoding="utf-8", newline="\n")
        _git("add", path, repo=self.origin)
        _git("commit", "-q", "-m", f"правка {path}", repo=self.origin)
        return self.head()

    def head(self) -> str:
        return _git("rev-parse", "HEAD", repo=self.origin)

    def work_head(self) -> str:
        return _git("rev-parse", "HEAD", repo=self.work)

    def mark(self, name: str) -> str:
        path = self.state / name
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def run(self, *args, script: Path | None = None, **stub_env):
        """Такт глазами systemd: (код, stdout+stderr, журнал в файле)."""
        # Окружение прогона тестов (conftest ставит BANK_STATE_DIR) в песочницу не
        # протекает: такт видит только свой env-файл и заказанные коды. Git такта и
        # подставного питона — под потолком песочницы: каталог кода без `.git` не
        # отдаёт `fetch` и `reset --hard` объемлющему репозиторию (рабочей копии).
        env = {k: v for k, v in sandbox_env(self.work).items()
               if not k.startswith(("SBER_", "BANK_", "STUB_", "FAKE_TODAY", "TINVEST_"))}
        env["ENV_FILE"] = str(self.env_file)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env.update({k: str(v) for k, v in stub_env.items()})
        done = subprocess.run([BASH, str(script or self.work / "ops" / "run.sh"), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, cwd=str(self.tmp))
        files = "".join(p.read_text(encoding="utf-8", errors="replace")
                        for p in sorted((self.state / "logs").glob("*.log")))
        return done.returncode, (done.stdout or "") + (done.stderr or ""), files


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def _calls(out: str) -> list[str]:
    return [line[len("ВЫЗОВ: "):] for line in out.splitlines() if line.startswith("ВЫЗОВ: ")]


def _work(out: str) -> list[str]:
    """Вызовы без зависимостей: порядок работы такта."""
    return [c for c in _calls(out) if not c.startswith("-m pip")]


def _last_line(out: str) -> str:
    return [line for line in out.splitlines() if line.strip()][-1]


RELEASE_TAIL = ["ops/publish.py --sync", "-m model.build_release", "ops/publish.py",
                "ops/tact.py done", "ops/publish.py --verify"]


def _collect(mode: str) -> list[str]:
    """Сбор режима со сборщиком T-Invest: его команда — первой и отдельно, остальные — второй."""
    return [f"-m indicators.collect {mode} --only tinvest", f"-m indicators.collect {mode} --skip tinvest"]


def _with_token(out: str) -> list[str]:
    return [line[len("ТОКЕН У: "):] for line in out.splitlines() if line.startswith("ТОКЕН У: ")]


def _sleeps(out: str) -> list[str]:
    return [line[len("SLEEP: "):] for line in out.splitlines() if line.startswith("SLEEP: ")]


# ------------------------------------------------------------ коды такта


def test_a_quiet_tact_returns_zero(tmp_path):
    """Спокойный такт обязан возвращать 0: иначе мост шлёт тревогу каждый день,
    а тревога, которая приходит всегда, не приходит вовсе."""
    for mode in ("collect", "daily"):
        box = Box(tmp_path / mode)
        code, out, log = box.run(mode, SBER_FUTURE_WEEKDAY=9)
        assert code == 0, out[-3000:]
        assert "ТРЕВОГА" not in out and "ПРОВАЛ" not in out
        assert log.count("готово:") == 1, "журнал такта обязан лечь и в файл"
        assert "Сбер 850 · такт " + mode in log


def test_the_daily_tact_runs_its_steps_in_order(box):
    """Сбор → нау-каст → здоровье → тесты такта → клон данных → сборка →
    публикация → сверка → объём. Тесты — ДО сборки."""
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert _work(out) == [
        *_collect("daily-indicators"),
        "-m indicators.collect nowcast",
        "-m indicators.collect health",
        TACT_CALL,
        *RELEASE_TAIL,
        "-m indicators.collect status",
    ], _work(out)


def test_the_morning_tact_runs_its_steps_in_order(box):
    code, out, _ = box.run("collect", SBER_FUTURE_WEEKDAY=9)
    assert code == 0, out[-3000:]
    assert _work(out) == [*_collect("collect"), "-m indicators.collect status",
                          "-m indicators.collect health"], _work(out)
    assert "ops/publish.py" not in out and "model.build_release" not in out


def test_an_irrecoverable_source_makes_the_collect_unit_raise_the_alarm(box):
    """Отказ невосполнимого источника, который не добрали повторы, — код 8 у юнита и
    названная причина; шаги после сбора выполняются: код — В КОНЦЕ, а не вместо работы."""
    code, out, _ = box.run("collect", STUB_COLLECT_RC=3, STUB_RETRY_RC=0, SBER_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert _last_line(out).startswith("ТРЕВОГА (код 8): невосполнимый источник не собран (попыток: 3")
    assert "indicators.collect status" in out and "indicators.collect health" in out


# --------------------------------------------- политика тревог: повтор сбора


def _retry_numbers() -> tuple[int, str]:
    script = RUN_SH.read_text(encoding="utf-8")
    return (int(re.search(r"^COLLECT_RETRY_ATTEMPTS=(\d+)$", script, re.M).group(1)),
            re.search(r"^COLLECT_RETRY_PAUSE=(\d+)$", script, re.M).group(1))


def test_the_morning_tact_retries_only_an_irrecoverable_source(tmp_path):
    """Повтор — только там, где он спасает данные (INTERFACES §6): утренний такт сам,
    внутри прогона, повторяет сбор, если отчёт сборщиков просит повтор (отказал
    невосполнимый источник), — с паузой и не больше заданного числа попыток."""
    attempts, pause = _retry_numbers()

    saved = Box(tmp_path / "saved")
    code, out, _ = saved.run("collect", STUB_COLLECT_SEQ="3 3 0", STUB_RETRY_RC=0, SBER_FUTURE_WEEKDAY=9)
    assert code == 0, out[-3000:]
    assert _work(out)[:8] == [*_collect("collect"), "ops/tact.py retry", *_collect("collect"),
                              "ops/tact.py retry", *_collect("collect")][:8], _work(out)
    assert _sleeps(out) == [pause, pause], "пауза между попытками — из бюджета"
    assert "попытка 2 из 3" in out and "попытка 3 из 3" in out
    assert "ТРЕВОГА" not in out, "источник добран — тревожить не о чем"

    lost = Box(tmp_path / "lost")
    code, out, _ = lost.run("collect", STUB_COLLECT_RC=3, STUB_RETRY_RC=0, SBER_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert out.count("--skip tinvest") == attempts and len(_sleeps(out)) == attempts - 1
    assert f"невосполнимый источник не собран (попыток: {attempts}" in _last_line(out)


def test_an_alarm_that_a_retry_cannot_lift_is_not_retried(tmp_path):
    """Источник без токена, деградация восполнимого источника третий такт подряд, тревога
    реестра (код 3 без признака повтора в отчёте) — тревога сразу, без повтора и паузы."""
    box = Box(tmp_path / "morning")
    code, out, _ = box.run("collect", STUB_COLLECT_RC=3, SBER_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert out.count("--skip tinvest") == 1 and _sleeps(out) == []
    line = _last_line(out)
    assert line.startswith("ТРЕВОГА (код 8): сбор требует внимания"), line
    assert "третий такт подряд" in line and "без токена" in line

    # Суточный такт и дозор сбор не повторяют: у дозора следующая проверка через 10 минут,
    # суточный доводит выпуск до витрины; тревога называет невосполнимый источник.
    daily = Box(tmp_path / "daily")
    code, out, _ = daily.run("daily", STUB_COLLECT_RC=3, STUB_RETRY_RC=0)
    assert code == 8, out[-3000:]
    assert out.count("--skip tinvest") == 1 and _sleeps(out) == []
    assert "невосполнимый источник не собран (попыток: 1" in _last_line(out)


def test_the_collection_code_is_the_worst_of_the_two_commands(tmp_path):
    """Сборщик T-Invest идёт отдельной командой; код сбора — худший из двух. Провал
    одной команды не отменяет вторую: день остальных источников тоже невосполним."""
    alarm = Box(tmp_path / "alarm")
    code, out, _ = alarm.run("daily", STUB_TINVEST_RC=3)
    assert code == 8 and "сбор требует внимания" in _last_line(out), out[-3000:]
    assert "ops/publish.py --verify" in _calls(out), "тревога сбора выпуск не останавливает"

    failed = Box(tmp_path / "failed")
    code, out, _ = failed.run("daily", STUB_TINVEST_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «сбор источников» (код 1)" in out
    assert "-m indicators.collect daily-indicators --skip tinvest" in _calls(out)
    assert "model.build_release" not in out

    worse = Box(tmp_path / "worse")
    code, out, _ = worse.run("daily", STUB_TINVEST_RC=3, STUB_COLLECT_RC=1)
    assert code == 1 and "ПРОВАЛ на шаге «сбор источников» (код 1)" in out, out[-3000:]


# ------------------------------------- токен T-Invest — только сборщику T-Invest


@pytest.mark.tact
def test_only_the_tinvest_collector_gets_the_token(box):
    """Секрет `TINVEST_TOKEN` попадает только в процесс сборщика T-Invest (INTERFACES §8):
    run.sh снимает его из окружения такта сразу после чтения env-файла. Остальные
    сборщики (чужой HTML и PDF), нау-каст, pip, тесты такта, сборка, публикация и сверка
    идут без токена. Тест такта: один прогон суточного такта в песочнице."""
    code, out, log = box.run("daily")
    assert code == 0, out[-3000:]
    assert _with_token(out) == ["-m indicators.collect daily-indicators --only tinvest"], _with_token(out)
    assert len(_calls(out)) >= 11, "такт прошёл все шаги — и все они были без токена"
    assert SANDBOX_TOKEN not in out and SANDBOX_TOKEN not in log, "значение токена в журнал не попадает"


def test_the_token_stays_with_the_tinvest_collector_in_every_tact(tmp_path):
    """То же в остальных тактах — и когда токен пришёл не из env-файла, а из окружения
    запуска: утренний (с тестами «в будущем»), дозор (сборщика T-Invest нет вовсе),
    пересборка (сбора нет)."""
    morning = Box(tmp_path / "morning")
    _, weekday = _shell_today()
    code, out, _ = morning.run("collect", SBER_FUTURE_WEEKDAY=weekday, TINVEST_TOKEN="from-the-outer-shell")
    assert code == 0, out[-3000:]
    assert _with_token(out) == ["-m indicators.collect collect --only tinvest"], _with_token(out)
    assert "ВЫЗОВ: -m pytest" in out, "тесты такта «в будущем» шли — и без токена"

    watch = Box(tmp_path / "watch")
    code, out, _ = watch.run("release-watch", STUB_WINDOW_RC=0, STUB_NEWS_RC=0)
    assert code == 0, out[-3000:]
    assert _with_token(out) == [], "дозор разбирает новости и PDF: токена у него нет вовсе"
    assert watch.mark("window.calls") == "window", "и у тихой проверки окна"

    rebuild = Box(tmp_path / "rebuild")
    code, out, _ = rebuild.run("rebuild")
    assert code == 0 and _with_token(out) == [], out[-3000:]
    assert "-m model.build_release" in _calls(out)


def test_the_script_keeps_the_token_out_of_the_exported_environment():
    """Токен живёт в неэкспортируемой переменной шелла и передаётся одной команде —
    присваиванием перед ней, а не через `env` (аргументы процесса видны всей машине)."""
    script = RUN_SH.read_text(encoding="utf-8")
    assert re.search(r"^set -a; \. \"\$ENV_FILE\"; set \+a\n(?:#.*\n|\n)*T_TOKEN=\$\{TINVEST_TOKEN:-\}\n"
                     r"unset TINVEST_TOKEN$", script, re.M), "токен снимается сразу после чтения env-файла"
    uses = [line.strip() for line in script.splitlines()
            if re.search(r"(?<![A-Z_])T_TOKEN(?![A-Z_])", line) and not line.lstrip().startswith("#")]
    assert uses == ["T_TOKEN=${TINVEST_TOKEN:-}",
                    'TINVEST_TOKEN="$T_TOKEN" "$PY" -m indicators.collect "$mode" --only tinvest || rc_t=$?'], uses
    assert "export T_TOKEN" not in script and "export TINVEST_TOKEN" not in script


def test_unhealthy_state_makes_the_collect_unit_raise_the_alarm(box):
    code, out, _ = box.run("collect", STUB_HEALTH_RC=3, SBER_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert "ТРЕВОГА (код 8): состояние не в порядке" in _last_line(out)


def test_the_daily_tact_publishes_and_only_then_reports_the_alarm(box):
    """Невосполнимый пропуск не отменяет выпуск: сборка, публикация и сверка
    проходят, код 8 — в самом конце."""
    code, out, _ = box.run("daily", STUB_COLLECT_RC=3)
    assert code == 8, out[-3000:]
    for call in ("-m model.build_release", "ops/publish.py", "ops/publish.py --verify"):
        assert call in _calls(out), call
    assert out.index("ВЫЗОВ: ops/publish.py\n") < out.index("ТРЕВОГА (код 8)")


def test_the_alarm_of_the_build_reaches_the_unit_code(box):
    """Тревога сборки (код 3) — тревога юнита, а не остановка. Причины кода 3 —
    INTERFACES §6: живые входы оценки деградировали, нет записи реестра дивидендов
    к концу квартала ГОСА, тяжёлый выпуск. Масса гейта вне коридора — уже отказ
    сборки (код 1); истекающее объяснение гейта и истёкший срок документа эмитента —
    плашки выпуска (код 0): о них такт не тревожит."""
    code, out, _ = box.run("daily", STUB_BUILD_RC=3)
    assert code == 8, out[-3000:]
    line = _last_line(out)
    assert line.startswith("ТРЕВОГА (код 8): сборка подняла тревогу"), line
    assert "реестра дивидендов" in line and "масса" not in line, line
    assert "объяснение гейта" not in line and "истека" not in line, line
    assert "ops/publish.py" in _calls(out), "выпуск собран — публикация обязана пройти"


def test_a_publish_with_a_remark_is_an_alarm_not_a_failure(box):
    code, out, _ = box.run("daily", STUB_PUBLISH_RC=3)
    assert code == 8, out[-3000:]
    assert "публикация требует внимания" in _last_line(out)
    assert "ops/publish.py --verify" in _calls(out)


def test_the_tact_survives_a_nowcast_that_cannot_record(box):
    code, out, _ = box.run("daily", STUB_NOWCAST_RC=3)
    assert code == 8, out[-3000:]
    assert "ТРЕВОГА (код 8): нау-каст записан не целиком" in _last_line(out)
    assert "ops/publish.py --verify" in _calls(out)


def test_several_alarms_are_named_together(box):
    code, out, _ = box.run("daily", STUB_COLLECT_RC=3, STUB_RETRY_RC=0, STUB_BUILD_RC=3)
    assert code == 8, out[-3000:]
    line = _last_line(out)
    assert "невосполнимый источник" in line and "сборка подняла тревогу" in line


def test_a_critical_source_failure_stops_the_tact_with_code_one(box):
    """Отказ КРИТИЧЕСКОГО источника (код 1 сбора): при неполных входах на
    витрину не уходит ничего."""
    code, out, _ = box.run("daily", STUB_COLLECT_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «сбор источников» (код 1)" in out
    assert "model.build_release" not in out, "выпуск не имел права собираться"


@pytest.mark.parametrize("stub,step", [
    ({"STUB_BUILD_RC": 2}, "сборка выпуска"),
    ({"STUB_BUILD_RC": 8}, "сборка выпуска"),
    ({"STUB_NOWCAST_RC": 137}, "нау-каст ближайшего отчёта"),
    ({"STUB_PYTEST_RC": 1}, "тесты такта"),
])
def test_any_failed_step_is_code_one_with_its_own_code_in_the_journal(box, stub, step):
    """Упавший шаг — всегда код 1: 8 от упавшей сборки мост иначе принял бы за
    «тревогу при опубликованном выпуске»."""
    code, out, _ = box.run("daily", **stub)
    rc = next(iter(stub.values()))
    assert code == 1, out[-3000:]
    assert f"ПРОВАЛ на шаге «{step}» (код {rc})" in out, out[-3000:]
    assert "ops/publish.py" not in [c.split(" --")[0] for c in _calls(out) if "--sync" not in c]
    assert box.mark("release.commit") == ""


def test_a_failed_data_clone_sync_does_not_stop_the_release(box):
    """Клон данных не обновился — сборка идёт на прежнем клоне, публикация сама
    обновит его и назовёт отказ."""
    code, out, _ = box.run("daily", STUB_SYNC_RC=1)
    assert code == 0, out[-3000:]
    assert "клон ветки release не обновлён" in out
    assert "-m model.build_release" in _calls(out)


def test_a_failed_publish_is_a_failed_step_and_is_not_verified(box):
    """Отказ push (после повторов внутри publish.py) — код 1: systemd повторит
    суточный такт через 3 минуты."""
    code, out, _ = box.run("daily", STUB_PUBLISH_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «публикация в репозиторий данных» (код 1)" in out
    assert "ops/publish.py --verify" not in _calls(out)
    assert "ops/tact.py done" not in _calls(out), "отложенное дозора снимается только после публикации"


def test_an_unconfirmed_release_is_an_alarm(box):
    """Сверка не дождалась GitHub Pages (код 3): выпуск уже в ветке — тревога,
    а не провал; release.commit не пишется, отметка остаётся."""
    code, out, _ = box.run("daily", STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert "боевая дверь не подтвердила" in _last_line(out)
    assert box.mark("release.commit") == ""
    assert box.work_head() in box.mark("release.pushed")
    assert "-m indicators.collect status" in _calls(out), "такт доводится до конца"


def test_a_selection_without_tact_tests_is_a_failure(box):
    """Белый список, который никого не пускает (pytest: 5), выключил бы проверку
    перед выпуском — это провал, а не успех."""
    code, out, _ = box.run("daily", STUB_PYTEST_RC=5)
    assert code == 1, out[-3000:]
    assert "ни одного теста с меткой tact" in out
    assert "model.build_release" not in out


def test_the_daily_tact_remembers_the_code_of_the_release_it_confirmed(tmp_path):
    """release.commit — код выпуска, прошедшего сверку; упавший такт его не пишет."""
    ok = Box(tmp_path / "ok")
    code, out, _ = ok.run("daily")
    assert code == 0, out[-3000:]
    assert ok.mark("release.commit") == ok.work_head()
    assert ok.mark("release.pushed") == ""

    failed = Box(tmp_path / "fail")
    code, out, _ = failed.run("daily", STUB_BUILD_RC=2)
    assert code == 1, out[-3000:]
    assert failed.mark("release.commit") == ""


def test_an_alarm_probe_raises_code_eight_after_the_work(box):
    """Проба доставки тревоги: такт работает как обычно, в конце — код 8 с
    названным источником; имя не из такта — 64, без работы."""
    code, out, _ = box.run("collect", SBER_SIMULATE_FAILURE="cbr_forms", SBER_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert "ПРОБА ТРЕВОГИ: источник cbr_forms будет объявлен отказавшим" in out
    assert "ops/tact.py simulate cbr_forms collect" in _calls(out)
    assert "ПРОБА ТРЕВОГИ: источник cbr_forms объявлен отказавшим" in _last_line(out)
    assert "-m indicators.collect health" in _calls(out)

    code, out, _ = box.run("collect", SBER_SIMULATE_FAILURE="нет_такого", STUB_SIMULATE_RC=64,
                           SBER_FUTURE_WEEKDAY=9)
    assert code == 64, out[-3000:]
    assert "-m indicators.collect collect" not in _calls(out)


@pytest.mark.parametrize("args", [("weekly",), ("rollback",), ("rollback", "zzz"), ("rollback", "abc123")])
def test_a_wrong_mode_is_code_64(box, args):
    code, out, _ = box.run(*args)
    assert code == 64, out[-3000:]
    assert "ВЫЗОВ:" not in out


def test_a_missing_env_file_or_variable_is_code_78(box):
    code, out, _ = box.run("daily", ENV_FILE=str(box.tmp / "нет-такого-файла"))
    assert code == 78 and "нет доступа" in out, out[-3000:]
    for dropped in ("SBER_REPO_DIR", "BANK_STATE_DIR"):
        text = "".join(line + "\n" for line in box.env_file.read_text(encoding="utf-8").splitlines()
                       if not line.startswith(dropped + "="))
        partial = box.tmp / f"env-{dropped}"
        partial.write_text(text, encoding="utf-8", newline="\n")
        code, out, _ = box.run("daily", ENV_FILE=str(partial))
        assert code == 78 and dropped in out, out[-3000:]
        assert "ВЫЗОВ:" not in out


@pytest.mark.tact
@pytest.mark.parametrize("mode", ["collect", "daily", "rebuild", "release-watch"])
def test_a_code_directory_without_its_own_repository_is_code_78(box, mode):
    """Каталог кода без своего `.git` (каталог песочницы, чей репозиторий стёр другой
    прогон тестов; на сервере — опечатка в SBER_REPO_DIR): git такта поднялся бы к
    объемлющему репозиторию, и `git fetch` с `git reset --hard origin/main` достались
    бы ему. Отказ кодом 78 — до замка, журнала и любой команды."""
    (box.work / ".git").rename(box.tmp / "снятый-репозиторий")
    code, out, files = box.run(mode)
    assert code == 78 and "не клон git" in out and "SBER_REPO_DIR" in out, out[-3000:]
    assert "ВЫЗОВ:" not in out and files == ""
    assert not (box.state / "logs").exists() and not (box.state / "run.lock").exists()


def test_the_tact_never_climbs_to_the_enclosing_repository(box):
    """Негодный `.git` в каталоге кода, лежащем ВНУТРИ другого клона: без потолка git
    пропустил бы его, нашёл объемлющий клон, и пересборка сбросила бы ЕГО дерево к
    origin/main. Под потолком такта git отвечает «не репозиторий»: тихая проверка
    выходит, объемлющий клон цел."""
    nested = box.work / "nested"
    (nested / "ops").mkdir(parents=True)
    (nested / ".git").mkdir()
    (nested / "ops" / "run.sh").write_text(RUN_SH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    env_file = box.tmp / "env-nested"
    env_file.write_text(box.env_file.read_text(encoding="utf-8").replace(
        f"SBER_REPO_DIR={_quoted(box.work)}", f"SBER_REPO_DIR={_quoted(nested)}"), encoding="utf-8", newline="\n")
    head = box.work_head()
    box.commit("model/core.py", "x = 2\n")                      # origin/main ушёл вперёд объемлющего клона
    code, out, _ = box.run("rebuild", script=nested / "ops" / "run.sh", ENV_FILE=str(env_file))
    assert code == 0 and "main на GitHub сейчас не читается" in out, out[-3000:]
    assert "ВЫЗОВ:" not in out
    assert box.work_head() == head, "объемлющий клон сброшен к origin/main"
    assert (box.work / "model" / "core.py").read_text(encoding="utf-8") == "x = 1\n"


# ------------------------------------------------ утренний такт и «будущее»


def _shell_today() -> tuple[str, int]:
    """«Сегодня» часов шелла (UTC), которыми живёт run.sh: дата и день недели.
    Не часы питона: прогон «в будущем» подменяет их в этом процессе, а шелла — нет."""
    done = subprocess.run([BASH, "-c", "date -u '+%F %u'"], capture_output=True, text=True)
    day, weekday = done.stdout.split()
    return day, int(weekday)


def test_the_morning_tact_checks_the_future_once_a_week(tmp_path):
    """В день прогона «в будущем» утренний такт гоняет тесты такта с
    FAKE_TODAY = сегодня + 60 дней; в другие дни — нет."""
    today, weekday = _shell_today()
    box = Box(tmp_path / "day")
    code, out, _ = box.run("collect", SBER_FUTURE_WEEKDAY=weekday)
    assert code == 0, out[-3000:]
    seen = re.findall(r"^FAKE_TODAY=(\d{4}-\d{2}-\d{2})$", out, re.M)
    assert len(seen) == 1, out[-3000:]
    ahead = (date.fromisoformat(seen[0]) - date.fromisoformat(today)).days
    assert ahead in (60, 61), ahead           # 61 — если полночь UTC пришлась на прогон
    assert out.count("ВЫЗОВ: -m pytest") == 1

    other = Box(tmp_path / "other")
    code, out, _ = other.run("collect", SBER_FUTURE_WEEKDAY=9)
    assert code == 0 and "-m pytest" not in out, out[-3000:]


def test_a_red_future_is_an_alarm_not_a_stop(box):
    _, weekday = _shell_today()
    code, out, _ = box.run("collect", SBER_FUTURE_WEEKDAY=weekday, STUB_FUTURE_RC=1)
    assert code == 8, out[-3000:]
    line = _last_line(out)
    assert "тесты такта с FAKE_TODAY=" in line and "через 60 дней суточный такт остановится" in line
    assert "поправить тест" in line and "объяснение гейта" not in line, (
        "сроки данных набор сервера не проверяет: красное «будущее» — дефект теста, а не будильник")
    assert "indicators.collect health" in out, "работа такта идёт дальше"
    # Повтор юнита (после провала шага) тесты заново не гоняет, но итог дня повторяет.
    code, out, _ = box.run("collect", SBER_FUTURE_WEEKDAY=weekday)
    assert code == 8, out[-3000:]
    assert "-m pytest" not in out and "уже проверено сегодня (код 1)" in out


# ----------------------------------------------------- зависимости по отметке


def test_dependencies_are_installed_by_the_requirements_mark(box):
    """pip — только когда sha256(requirements.txt) разошёлся с отметкой в venv;
    отметка пишется только после удачной установки."""
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert out.count("ВЫЗОВ: -m pip install") == 1
    mark = (box.venv / ".requirements.sha256").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", mark)

    code, out, _ = box.run("daily")
    assert code == 0 and "-m pip install" not in out and "на месте" in out, out[-3000:]

    box.commit("requirements.txt", REQUIREMENTS + "pytest==9.1.1\n")
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert out.count("ВЫЗОВ: -m pip install") == 1
    assert (box.venv / ".requirements.sha256").read_text(encoding="utf-8").strip() != mark


def test_a_failed_install_stops_the_tact_and_is_retried_next_time(box):
    code, out, _ = box.run("daily", STUB_PIP_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «зависимости» (код 1)" in out
    assert not (box.venv / ".requirements.sha256").exists()
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert "HEAD не менялся" in out and out.count("ВЫЗОВ: -m pip install") == 1


# ------------------------------------ перезапуск такта новым run.sh после обновления

NEW_SCRIPT_MARK = "МЕТКА: исполняется НОВЫЙ ops/run.sh"
CHILD_SEES_MARK = "МЕТКА ПЕРЕЗАПУСКА У ПИТОНА: "


def _updating_box(tmp_path) -> Box:
    box = Box(tmp_path)
    script = RUN_SH.read_text(encoding="utf-8")
    anchor = "set -Eeuo pipefail\n"
    assert script.count(anchor) == 1
    box.commit("ops/run.sh", script.replace(anchor, anchor + f'echo "{NEW_SCRIPT_MARK}"\n', 1))
    call = 'echo "ВЫЗОВ: $*"\n'
    assert TACT_STUB.count(call) == 1, "песочница такта сменила вид подставного питона"
    box.stub.write_text(TACT_STUB.replace(
        call, call + f'echo "{CHILD_SEES_MARK}${{SBER_REEXEC:-нет}}"\n', 1),
        encoding="utf-8", newline="\n")
    return box


def test_a_code_update_restarts_the_tact_with_the_new_run_sh(tmp_path):
    box = _updating_box(tmp_path)
    code, out, log_file = box.run("daily")
    assert code == 0, out[-3000:]
    assert "код обновился" in out and "перезапускаю такт новым ops/run.sh" in out, out[-3000:]
    assert out.count(NEW_SCRIPT_MARK) == 1, "новый run.sh исполняется, и ровно один раз"
    assert "перезапуск тем же тактом" in out and "HEAD не менялся" in out
    assert out.count("ВЫЗОВ: -m model.build_release") == 1, out[-3000:]
    assert (out.index("перезапускаю такт") < out.index(NEW_SCRIPT_MARK)
            < out.index("ВЫЗОВ: -m pip install") < out.index("ВЫЗОВ: -m model.build_release"))
    assert log_file.count(NEW_SCRIPT_MARK) == 1, "перезапуск пишет в тот же tee"
    assert log_file.count("готово:") == 1
    seen = re.findall(re.escape(CHILD_SEES_MARK) + r"(\S+)", out)
    assert len(seen) == out.count("ВЫЗОВ: ") and set(seen) == {"нет"}, seen
    assert box.work_head() == box.head()


def test_the_restart_happens_once_per_tact(tmp_path):
    box = _updating_box(tmp_path)
    code, out, _ = box.run("daily", SBER_REEXEC="0" * 40)
    assert code == 0, out[-3000:]
    assert "перезапускаю такт новым ops/run.sh" not in out
    assert "код обновился повторно за один такт" in out, out[-3000:]
    assert out.count("ВЫЗОВ: -m model.build_release") == 1


def test_an_unreadable_github_does_not_cost_the_morning_collection(box):
    """`git fetch` не прошёл — утренний сбор идёт на текущем коде: невосполнимый
    день источника дороже свежести кода."""
    _git("remote", "set-url", "origin", str(box.tmp / "нет-такого-репозитория"), repo=box.work)
    code, out, _ = box.run("collect", SBER_FUTURE_WEEKDAY=9)
    assert code == 0, out[-3000:]
    assert "main на GitHub не читается" in out
    assert _collect("collect") == _work(out)[:2], _work(out)


# ------------------------------------------------------------ дозор релизов


def test_the_release_watch_is_silent_outside_the_window(box):
    """Вне окна релиза (и без отложенного) — выход 0 без единой строки: таймер
    будит дозор 20 раз в будний день."""
    code, out, log_file = box.run("release-watch")
    assert code == 0, out[-3000:]
    assert out.strip() == "" and log_file == "", out[-2000:] + log_file[-2000:]
    assert box.mark("window.calls") == "window"


def test_the_release_watch_without_news_does_not_build(box):
    code, out, log_file = box.run("release-watch", STUB_WINDOW_RC=0)
    assert code == 0, out[-3000:]
    assert _work(out) == ["-m indicators.collect release-watch", "ops/tact.py news"], _work(out)
    assert "tinvest" not in out, "в режиме дозора сборщика T-Invest нет — команда одна и без токена"
    assert log_file.count("готово:") == 1 and "такт release-watch" in log_file


def test_the_release_watch_with_news_publishes(box):
    """Принят месяц РСБУ или кандидат МСФО — нау-каст, тесты такта, сборка,
    публикация (отложенное снимается), сверка."""
    code, out, _ = box.run("release-watch", STUB_WINDOW_RC=0, STUB_NEWS_RC=0)
    assert code == 0, out[-3000:]
    assert _work(out) == ["-m indicators.collect release-watch", "ops/tact.py news",
                          "-m indicators.collect nowcast", TACT_CALL, *RELEASE_TAIL], _work(out)
    assert box.mark("release.commit") == box.work_head()


def test_the_release_watch_alarm_and_failure_codes(tmp_path):
    code, out, _ = Box(tmp_path / "a").run("release-watch", STUB_WINDOW_RC=0, STUB_COLLECT_RC=3,
                                           STUB_RETRY_RC=0)
    assert code == 8 and "невосполнимый источник не собран" in _last_line(out), out[-3000:]
    assert _sleeps(out) == [], "дозор сбор не повторяет: его следующая проверка — через 10 минут"
    code, out, _ = Box(tmp_path / "b").run("release-watch", STUB_WINDOW_RC=0, STUB_NEWS_RC=2)
    assert code == 1 and "ПРОВАЛ на шаге «что принято» (код 2)" in out, out[-3000:]
    code, out, _ = Box(tmp_path / "c").run("release-watch", STUB_WINDOW_RC=0, STUB_NEWS_RC=0,
                                           STUB_BUILD_RC=1)
    assert code == 1 and "ПРОВАЛ на шаге «сборка выпуска»" in out, out[-3000:]
    assert "ops/tact.py done" not in _calls(out)


# ------------------------------------- пересборка при новом коде (таймер rebuild)


def test_the_rebuild_is_silent_until_the_counting_code_changes(box):
    """Коммит вне кода, который считает число, — тишина; правка `model/` — выпуск."""
    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")
    box.commit("docs/MANUAL.md", "справочник, правка\n")
    code, out, log_file = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert out.strip() == "" and log_file == "", (
        "проверка раз в 15 минут обязана быть тихой:\n" + out[-2000:] + log_file[-2000:])

    box.commit("ops/README.md", "эксплуатация\n")
    code, out, log_file = box.run("rebuild")
    assert code == 0 and out.strip() == "" and log_file == "", out[-3000:]

    fresh = box.commit("model/core.py", "x = 2\n")
    code, out, log_file = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert "Сбер 850 · такт rebuild" in out and "перезапускаю такт новым ops/run.sh" in out
    assert [c for c in _work(out)] == [TACT_CALL, *RELEASE_TAIL], _work(out)
    assert "indicators.collect" not in out, "без сбора и нау-каста"
    assert box.mark("release.commit") == fresh, "выпуск помнит код, которым собран"
    assert log_file.count("готово:") == 1

    code, out, _ = box.run("rebuild")
    assert code == 0 and out.strip() == "", "собранный код второй раз не пересобирается"


@pytest.mark.parametrize("path", ["data/indicators/sources.yaml", "data/checks/control_model.json",
                                  "requirements.txt"])
def test_data_that_enters_the_release_wakes_the_rebuild(box, path):
    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")
    fresh = box.commit(path, "правка\n")
    code, out, _ = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert "ВЫЗОВ: -m model.build_release" in out
    assert box.mark("release.commit") == fresh


def test_a_failed_rebuild_is_not_repeated_on_the_same_commit(box):
    """Упавшая пересборка ждёт нового коммита: красный набор не крутится каждые 15 минут."""
    code, out, _ = box.run("rebuild", STUB_BUILD_RC=2)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «сборка выпуска» (код 2)" in out
    assert box.mark("release.commit") == ""
    assert box.mark("rebuild.tried") == box.head()

    code, out, _ = box.run("rebuild")
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ (код 1): пересборка на коммите" in out and "уже запускалась" in out
    assert "ВЫЗОВ:" not in out, "на том же коммите — без тестов и сборки"

    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")
    code, out, _ = box.run("rebuild")
    assert code == 0 and out.strip() == "", out[-3000:]

    fresh = box.commit("model/core.py", "x = 3\n")
    code, out, _ = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert box.mark("release.commit") == fresh


def test_the_rebuild_finishes_the_verify_of_the_previous_tact(box):
    """Сверку, не дождавшуюся GitHub Pages, дожимает тихая проверка пересборки."""
    code, out, _ = box.run("daily", STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert box.mark("release.commit") == "" and box.mark("release.pushed")

    code, out, _ = box.run("rebuild", STUB_VERIFY_RC=3)
    assert code == 0, out[-3000:]
    assert _calls(out) == ["ops/publish.py --verify --once"], out[-3000:]
    assert "ждёт сверки — не пересобираю" in out
    assert box.mark("release.commit") == "" and box.mark("rebuild.tried") == ""

    code, out, _ = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert _calls(out) == ["ops/publish.py --verify --once"], out[-3000:]
    assert box.mark("release.commit") == box.work_head() and box.mark("release.pushed") == ""

    code, out, _ = box.run("rebuild")
    assert code == 0 and out.strip() == "", out[-3000:]


def test_the_mark_of_the_publisher_is_read_by_the_rebuild(tmp_path):
    """run.sh читает код выпуска из отметки `release.pushed` выражением sed;
    отметку пишет `ops/publish.py` — выражение обязано понимать её настоящий вид."""
    spec = importlib.util.spec_from_file_location("publish_for_mark", ROOT / "ops" / "publish.py")
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    repo = tmp_path / "repo"
    sandbox_init(repo, "-q", "-b", "main")
    _git("commit", "-q", "--allow-empty", "-m", "код", repo=repo)
    cfg = publish.Config(state_dir=tmp_path / "state", code_root=repo)
    cfg.state_dir.mkdir()
    publish.write_pushed_mark(cfg, kind="publish", digest="a" * 64,
                              published_at="2026-09-28T17:30:00+00:00", clone=repo)
    expr = re.search(r"pending_code=\$\(sed -n '([^']+)'", RUN_SH.read_text(encoding="utf-8")).group(1)
    stub_expr = re.search(r"sed -n '([^']+)' \"\$state/release.pushed\"", TACT_STUB).group(1)
    assert expr == stub_expr, "подставной питон читает отметку не так, как run.sh"
    done = subprocess.run([BASH, "-c", f"sed -n '{expr}' \"$1\"", "sed", str(cfg.pushed_mark)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.stdout.strip() == _git("rev-parse", "HEAD", repo=repo)
    assert json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))["code_commit"] == done.stdout.strip()


# ------------------------------------------------------------------ откат


def test_a_rollback_repoints_and_verifies_under_the_lock(box):
    sha = "ab" * 32
    code, out, log_file = box.run("rollback", sha[:12])
    assert code == 0, out[-3000:]
    assert _calls(out) == [f"ops/publish.py --rollback {sha[:12]}", "ops/publish.py --verify"], out
    assert "обновление кода" not in out, "откат делает ровно одно и кода не трогает"
    assert log_file.count("готово:") == 1

    code, out, _ = box.run("rollback", sha[:12], "цена ядра на битой кривой")
    assert code == 0, out[-3000:]
    assert _calls(out)[0] == f"ops/publish.py --rollback {sha[:12]} --note цена ядра на битой кривой"

    code, out, _ = box.run("rollback", sha, STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert "откат отправлен в репозиторий данных, но боевая дверь его не подтвердила" in _last_line(out)

    code, out, _ = box.run("rollback", sha, STUB_ROLLBACK_RC=1)
    assert code == 1 and "ПРОВАЛ на шаге «откат указателя" in out, out[-3000:]
    assert "ops/publish.py --verify" not in _calls(out)


def test_a_rollback_during_a_pending_verify_is_not_undone_by_the_rebuild(box):
    """Плохой выпуск опубликован на новом коде, сверка ещё висит, владелец откатывает.
    Откат перезаписывает отметку сверки своей; без кода откатываемого выпуска в
    `release.commit` пересборка увидела бы «новый» код и за 15 минут вернула бы выпуск
    поверх отката (или прислала ложный «ПРОВАЛ»). Отметки здесь пишет подставной
    питон так же, как `ops/publish.py` (сверяет tests/test_ops_publish.py)."""
    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")
    fresh = box.commit("model/core.py", "x = 2\n")
    code, out, _ = box.run("rebuild", STUB_VERIFY_RC=3)
    assert code == 8 and box.mark("rebuild.tried") == fresh, out[-3000:]
    assert fresh in box.mark("release.pushed") and box.mark("release.commit") != fresh

    code, out, _ = box.run("rollback", "ab" * 6, "плохой выпуск")
    assert code == 0, out[-3000:]
    assert box.mark("release.commit") == fresh, "откат помнит код выпуска, с которого откатились"

    for _ in range(2):
        code, out, _ = box.run("rebuild")
        assert code == 0 and out.strip() == "", "пересборка молчит: ни выпуска поверх отката, ни «ПРОВАЛ»"
    assert "ВЫЗОВ: -m model.build_release" not in out


# ------------------------------------------------------------------- замок


def test_a_busy_lock_turns_the_quiet_checks_away_and_makes_a_tact_wait(tmp_path):
    """Замок занят: пересборка и дозор молча уступают, плановый такт ждёт, а не
    падает. Настоящего flock в Git Bash нет — здесь подставной в PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "flock").write_text(FLOCK_STUB, encoding="utf-8", newline="\n")
    (bin_dir / "flock").chmod(0o755)
    path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    wait = re.search(r"^LOCK_WAIT_SECONDS=(\d+)$", RUN_SH.read_text(encoding="utf-8"), re.M).group(1)

    for mode in ("rebuild", "release-watch"):
        code, out, _ = Box(tmp_path / mode).run(mode, PATH=path, STUB_FLOCK_N_RC=1, STUB_WINDOW_RC=0)
        assert code == 0, out[-2000:]
        assert "ВЫЗОВ:" not in out and "такт " + mode not in out, out[-2000:]

    code, out, _ = Box(tmp_path / "wait").run("collect", PATH=path, STUB_FLOCK_N_RC=1,
                                              SBER_FUTURE_WEEKDAY=9)
    assert code == 0, out[-2000:]
    assert f"жду до {wait} с" in out and f"FLOCK: -w {wait} 9" in out, out[-2000:]
    assert "ВЫЗОВ: -m indicators.collect collect" in out, "дождавшись замка, такт работает"

    code, out, _ = Box(tmp_path / "gone").run("daily", PATH=path, STUB_FLOCK_N_RC=1, STUB_FLOCK_W_RC=1)
    assert code == 75, out[-2000:]
    assert "прогон уже идёт, выхожу" in out and "ВЫЗОВ:" not in out

    code, out, _ = Box(tmp_path / "rollback").run("rollback", "cd" * 6, PATH=path,
                                                  STUB_FLOCK_N_RC=1, STUB_FLOCK_W_RC=1)
    assert code == 75 and "ВЫЗОВ:" not in out, "откат ждёт тот же замок, что и такты"


# ------------------------------------------------------- согласие с кодом выпуска


def test_run_sh_code_dirs_cover_the_release_inputs():
    """Каталоги, правка которых будит пересборку, — всё, что входит в выпуск:
    ядро, индикаторы, эксплуатация, книга, факты, конфигурация сборщиков,
    сводка контрольной модели и зависимости; витрины и документов там нет."""
    found = re.findall(r"^CODE_DIRS=\(([^)]*)\)$", RUN_SH.read_text(encoding="utf-8"), re.M)
    assert len(found) == 1, found
    dirs = found[0].split()
    assert dirs == ["model", "indicators", "ops", "data/assumptions", "data/facts", "data/indicators",
                    "data/checks", "requirements.txt"]
    assert "web" not in dirs and "functions" not in dirs and "docs" not in dirs


def test_run_sh_code_dirs_match_the_engine():
    """Ядро объявляет свои каталоги кода (`model.payload.CODE_DIRS`, INTERFACES
    §4.6; решение ведущего 01.10.2026, C2) — списки обязаны совпасть: иначе выпуск
    назвал бы код изменившимся, а пересборка промолчала бы (или наоборот)."""
    from model import payload

    declared = getattr(payload, "CODE_DIRS", None)
    assert declared is not None, "model.payload не объявляет CODE_DIRS (INTERFACES §4.6)"
    found = re.search(r"^CODE_DIRS=\(([^)]*)\)$", RUN_SH.read_text(encoding="utf-8"), re.M).group(1)
    assert found.split() == list(declared)


def test_the_script_is_valid_bash_with_unix_line_endings():
    assert b"\r\n" not in RUN_SH.read_bytes(), "CRLF: на сервере «bad interpreter»"
    done = subprocess.run([BASH, "-n", str(RUN_SH)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_script_reads_only_its_own_variables():
    """Переменные окружения run.sh — из списка INTERFACES §8 (и служебная метка
    перезапуска): чужое имя в конвейере значило бы вход, которого нет в договоре.
    Каталог состояния — `BANK_STATE_DIR` (его читают ядро и индикаторы, имя без
    префикса банка); прежнего `SBER_STATE_DIR` нет."""
    text = RUN_SH.read_text(encoding="utf-8")
    pattern = r"(?:SBER|BANK)_[A-Z_]+"
    used = set(re.findall(rf"\$\{{?({pattern})", text)) | set(re.findall(rf"\b({pattern})=", text))
    allowed = {"SBER_REPO_DIR", "BANK_STATE_DIR", "SBER_VENV", "SBER_PYTHON", "SBER_FUTURE_WEEKDAY",
               "SBER_FUTURE_DAYS", "SBER_SIMULATE_FAILURE", "SBER_REEXEC"}
    assert used <= allowed, used - allowed
    assert "BANK_STATE_DIR" in used and "SBER_STATE_DIR" not in text
