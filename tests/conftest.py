# -*- coding: utf-8 -*-
"""Общая обвязка прогона (docs/INTERFACES.md §9): «сегодня», состояние, сторож такта,
песочница git и замок каталога временных файлов.

* **Прогон «в будущем»** (`FAKE_TODAY`): подмена ставится ДО импорта ядра и слоя
  индикаторов (`tests/fakedate_plugin.py`), подпроцессы видят ту же дату.
* **Состояние** — у каждого теста своё: `BANK_STATE_DIR` указывает на пустой
  временной каталог, `BANK_DATA_REPO_DIR` и токен T-Invest из окружения
  разработчика в тесты не протекают (токен остаётся только тестам `network`).
  Тест, случайно опершийся на собранный архив машины, зеленел бы только на ней.
  Имена — без префикса банка (INTERFACES §8): их читают ядро и индикаторы.
* **Полоса последовательно:** рабочие процессы полосы читают код с диска и не
  видят `monkeypatch`, поэтому весь прогон идёт с `BANK_WORKERS=1`; пул — только
  внутри фикстуры `parallel_band` (полный расчёт на неизменённом коде и книге).
* **Сторож такта:** в прогоне, исключающем `ci_only` (такт сервера, `ops/run.sh`),
  полный выпуск не считается — полосу на полном числе прогонов книги и сборку
  без `fast` собирает и проверяет сама сборка. Непомеченный тест полного выпуска
  вернул бы в такт всю его цену, и увидеть это можно было бы только по часам
  (урок 850oa: такт вырос с 7 до 22 минут).
* **Git — только в песочнице.** Временные каталоги тестов лежат внутри рабочей копии
  (`var/pytest-*`), а git без своего `.git` поднимается к репозиторию выше: коммит
  теста лёг бы в рабочую копию. На весь прогон стоит потолок
  `GIT_CEILING_DIRECTORIES` = корень рабочей копии (его наследуют все подпроцессы:
  `ops/run.sh`, `ops/publish.py`, сборка), переменные `GIT_DIR` и родственные сняты;
  писать git-ом тесты могут только помощником `tests/support_git.py`.
* **Один каталог временных файлов — один прогон.** pytest при старте стирает свой
  `--basetemp`; второй прогон с тем же каталогом стёр бы песочницы первого на ходу.
  Каталог занят замком на всё время прогона: второй прогон отказывает сразу, с
  подсказкой задать свой `--basetemp=var/pytest-<ключ>-<случайное>`.

Пропуск теста внутри тела (`pytest.skip`) здесь разрешён — книга и факты
появляются по ходу сборки (INTERFACES §9); сеть и папка передачи исключаются
маркерами `network` и `archive` в выражении `-m`.
"""
from __future__ import annotations

# Подмена «сегодня» обязана встать ДО импорта ядра и слоя индикаторов — и до
# `from datetime import date` ниже. Без переменной окружения ничего не делает.
from tests import fakedate_plugin as _fakedate  # noqa: E402  (порядок важен)

_fakedate.install()

import errno  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from tests import support_git  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BOOK_YAML = ROOT / "data" / "assumptions" / "assumptions.yaml"

STATE_ENV, DATA_REPO_ENV, TOKEN_ENV, WORKERS_ENV = (
    "BANK_STATE_DIR", "BANK_DATA_REPO_DIR", "TINVEST_TOKEN", "BANK_WORKERS")
# Запасной каталог состояния на время сбора тестов (до фикстур): не настоящий var/state.
FALLBACK_STATE = ROOT / "var" / "test-state"

_OUTER_WORKERS = ["auto"]            # BANK_WORKERS окружения, из которого запущен прогон
_GUARD = ["не нужен: прогон не исключает ci_only"]
_BASETEMP_LOCK = []                  # открытый файл замка каталога временных файлов — до конца прогона
# Коды «замок у другого процесса» (Windows — EACCES, POSIX — EAGAIN / EWOULDBLOCK).
_LOCK_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}

BASETEMP_BUSY = (
    "каталог временных файлов {base} занят другим прогоном pytest: при старте pytest стирает свой "
    "--basetemp, и второй прогон стёр бы песочницы первого на ходу. Дождитесь его конца или задайте "
    "свой каталог: --basetemp=var/pytest-<ключ>-<случайное>")

FULL_RELEASE_IN_THE_TACT = (
    "полный выпуск (полоса на полном числе прогонов книги или сборка без fast) в прогоне "
    "без `ci_only`: такт его не собирает — его собирает и проверяет сама сборка "
    "(`python -m model.build_release`). Пометьте тест `@pytest.mark.ci_only` или "
    "считайте быструю сборку (`fast=True`, `draws=FAST_DRAWS`)")


def pytest_report_header(config):
    """Прогон «в будущем» и состояние сторожа такта — в шапке, а не только в окружении."""
    lines = [f"сторож такта: {_GUARD[0]}",
             f"каталог временных файлов: {getattr(config, '_basetemp_lock_note', 'замок не ставился')}"]
    fake = _fakedate.report_header()
    return ([fake] if fake else []) + lines


def _lock_file(handle) -> bool | None:
    """Занять файл замка, не дожидаясь: True — занят этим процессом, False — он у другого
    процесса, None — файловая система замков не умеет (прогон идёт без замка). Замок
    снимает сама ОС, когда процесс прогона кончается (и при его гибели)."""
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return False if exc.errno in _LOCK_BUSY else None
    return True


def _hold_the_basetemp(given) -> str:
    """Замок каталога временных файлов: файл `<каталог>.lock` рядом с ним (внутри его
    стёр бы сам pytest). Возвращает строку для шапки; занятый каталог — отказ прогона."""
    if not given:
        return "не задан (--basetemp): временные каталоги нумерует pytest"
    base = Path(os.path.abspath(str(given)))
    if not base.parent.is_dir():
        return f"{base.name}: нет родительского каталога — pytest откажет сам"
    try:
        handle = open(base.parent / (base.name + ".lock"), "a+b")
    except OSError as exc:
        return f"{base.name}: замок не поставлен ({type(exc).__name__}) — каталог рядом недоступен на запись"
    held = _lock_file(handle)
    if held is False:
        handle.close()
        raise pytest.UsageError(BASETEMP_BUSY.format(base=base.name))
    _BASETEMP_LOCK.append(handle)
    return f"{base.name}: " + ("занят этим прогоном" if held else "файловая система замков не умеет — прогон без замка")


def pytest_configure(config):
    """Окружение прогона — до импорта тестовых модулей."""
    # Рабочие процессы и повторный вызов в том же процессе замок не берут дважды.
    if not _BASETEMP_LOCK:
        config._basetemp_lock_note = _hold_the_basetemp(config.getoption("basetemp", None))
    # Git подпроцессов не поднимается в рабочую копию и не получает репозиторий из окружения.
    os.environ[support_git.CEILING_ENV] = support_git.session_ceiling()
    for name in support_git.REPO_ENV:
        os.environ.pop(name, None)
    os.environ[STATE_ENV] = str(FALLBACK_STATE)
    os.environ.pop(DATA_REPO_ENV, None)
    _OUTER_WORKERS[0] = os.environ.get(WORKERS_ENV, "auto")
    os.environ[WORKERS_ENV] = "1"
    if re.search(r"\bnot\s+ci_only\b", config.getoption("markexpr") or ""):
        _GUARD[0] = _forbid_the_full_release()


def _full_draws() -> int | None:
    """Прогонов полосы в книге (`valuation.uncertainty.draws`); нет книги — None."""
    if not BOOK_YAML.exists():
        return None
    import yaml

    data = yaml.safe_load(BOOK_YAML.read_text(encoding="utf-8")) or {}
    try:
        return int(data["valuation"]["uncertainty"]["draws"])
    except (KeyError, TypeError, ValueError):
        return None


def _forbid_the_full_release() -> str:
    """Сторож такта: подмена `model.uncertainty.band` и `model.payload.make_release`.

    Возвращает строку для шапки прогона. Модулей ядра части 2 может ещё не быть
    (сборка идёт потоками) — тогда сторожить нечего, и это видно в шапке. Сломанный
    импорт соседнего модуля не роняет весь прогон: его тесты упадут сами.
    """
    full = _full_draws()
    if full is None:
        return "не установлен: нет книги (valuation.uncertainty.draws)"
    guarded = []
    try:
        from model import uncertainty
    except ImportError:
        return "не установлен: нет model/uncertainty.py"
    except Exception as exc:  # noqa: BLE001 — чужой модуль в работе
        return f"не установлен: model.uncertainty не импортируется ({type(exc).__name__})"
    band = getattr(uncertainty, "band", None)
    if callable(band):
        def small_band_only(*args, draws=None, **kwargs):
            if draws is None or int(draws) >= full:
                raise AssertionError(FULL_RELEASE_IN_THE_TACT)
            return band(*args, draws=draws, **kwargs)

        uncertainty.band = small_band_only
        guarded.append("полоса")
    try:
        from model import payload
    except Exception:  # noqa: BLE001 — нет модуля или он в работе
        payload = None
    make_release = getattr(payload, "make_release", None)
    if callable(make_release):
        def fast_release_only(*args, fast=False, **kwargs):
            if not fast:
                raise AssertionError(FULL_RELEASE_IN_THE_TACT)
            return make_release(*args, fast=fast, **kwargs)

        payload.make_release = fast_release_only
        guarded.append("сборка")
    return ("включён: " + ", ".join(guarded) + f" (полный — от {full} прогонов)") if guarded \
        else "не установлен: в model.uncertainty нет band"


@pytest.fixture(scope="session", autouse=True)
def _git_sandbox(tmp_path_factory):
    """Песочница git прогона — каталог временных файлов pytest (tests/support_git.py)."""
    return support_git.declare_sandbox(tmp_path_factory.getbasetemp())


@pytest.fixture(autouse=True)
def _isolated_state(request, tmp_path_factory, monkeypatch):
    """Своё пустое состояние на каждый тест; токен — только тестам с сетью."""
    state = tmp_path_factory.mktemp("bank-state")
    monkeypatch.setenv(STATE_ENV, str(state))
    monkeypatch.delenv(DATA_REPO_ENV, raising=False)
    if request.node.get_closest_marker("network") is None:
        monkeypatch.delenv(TOKEN_ENV, raising=False)
    yield


@contextmanager
def _parallel_band():
    before = os.environ.get(WORKERS_ENV)
    os.environ[WORKERS_ENV] = _OUTER_WORKERS[0]
    try:
        yield
    finally:
        os.environ[WORKERS_ENV] = before if before is not None else "1"


@pytest.fixture(scope="session")
def parallel_band():
    """Контекст, в котором полоса считается пулом (`BANK_WORKERS` окружения прогона,
    по умолчанию auto). Только для полного расчёта на НЕИЗМЕНЁННОМ коде и книге без
    monkeypatch ядра: рабочим процессам видны лишь код и файлы на диске."""
    return _parallel_band
