# -*- coding: utf-8 -*-
"""Git в тестах — только в песочнице: во временном каталоге прогона.

Тесты конвейера и публикации заводят настоящие репозитории git (`init`, `commit`,
`clone`, `push` в голый репозиторий). Временные каталоги прогона по умолчанию лежат
ВНУТРИ рабочей копии (`var/pytest-*`, pytest.ini), а git, не найдя годного `.git` в
своём каталоге, поднимается к ближайшему репозиторию выше. Стоит каталогу песочницы
исчезнуть или лишиться `.git` (второй прогон pytest с тем же `--basetemp` чистит его
при старте) — и `git commit` теста ложится в текущую ветку рабочей копии, а `git reset
--hard` конвейера достался бы её дереву. Перед первым push такой коммит уехал бы в
публичную историю.

Поэтому три слоя, каждый достаточен сам:

1. **Потолок на весь прогон** (`tests/conftest.py`): `GIT_CEILING_DIRECTORIES` = корень
   рабочей копии. Любой git любого подпроцесса, запущенный из каталога под корнем, не
   поднимается в рабочую копию: без своего `.git` он отвечает «not a git repository».
   Команды, которые читают саму рабочую копию (`cwd` = корень), потолок не задевает:
   на текущий каталог он не действует. Переменные, задающие репозиторий мимо каталога
   (`GIT_DIR` и родственные — прогон из хука git), из окружения прогона сняты.
2. **Этот помощник** — единственный способ писать git-ом в тестах: явный `-C <каталог>`,
   потолок = родитель этого каталога, каталог обязан лежать в песочнице прогона
   (каталог временных файлов pytest), а перед каждой пишущей командой git сам называет
   свой каталог репозитория (`rev-parse --absolute-git-dir`) — он обязан быть ровно в
   названном каталоге. Иначе `GitSandboxError` ДО записи.
3. **Замок каталога временных файлов** (`tests/conftest.py`): второй прогон pytest с тем
   же `--basetemp` отказывает при старте, а не стирает каталоги первого.

Прямой вызов `git` в тестах разрешён только читающим командам над самой рабочей копией
(`ls-files`, `log`, `rev-parse`, `check-attr`, `check-ignore`); сторож —
`tests/test_ops_ci.py::test_git_in_the_tests_goes_through_the_sandbox_helper`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]

CEILING_ENV = "GIT_CEILING_DIRECTORIES"
# Переменные, которыми git получает репозиторий мимо каталога запуска: унаследованные
# (прогон из хука git, из оболочки с выставленным GIT_DIR) увели бы команду в чужой
# репозиторий при любом `-C` и любом потолке.
REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX")
# Команды, которые в репозиторий не пишут ничего (ни объектов, ни индекса, ни настроек).
# Всё остальное считается записью и проходит проверку каталога репозитория.
READ_ONLY = frozenset({"rev-parse", "rev-list", "log", "show", "ls-tree", "cat-file"})
# Каталог репозитория создаёт сама команда: проверять до неё нечего — проверяется цель.
CREATING = frozenset({"init", "clone"})

_SANDBOX: list[Path] = []          # каталог временных файлов прогона (ставит tests/conftest.py)


class GitSandboxError(AssertionError):
    """Команда git не исполнена: её репозиторий — не во временном каталоге прогона."""


def _real(path) -> Path:
    return Path(os.path.realpath(str(path)))


def _within(path: Path, parent: Path) -> bool:
    """`path` лежит строго внутри `parent` (сравнение настоящих путей, без учёта регистра на Windows)."""
    a, b = os.path.normcase(str(_real(path))), os.path.normcase(str(_real(parent)))
    return a != b and a.startswith(b.rstrip("\\/") + os.sep)


def _same(a, b) -> bool:
    return os.path.normcase(str(_real(a))) == os.path.normcase(str(_real(b)))


def declare_sandbox(base) -> Path:
    """Объявить песочницу прогона — каталог временных файлов pytest. Каталог, равный
    корню рабочей копии или лежащий выше него, песочницей быть не может."""
    base = _real(base)
    if _same(base, ROOT) or _within(ROOT, base):
        raise GitSandboxError(f"каталог временных файлов {base} — сама рабочая копия или каталог над ней: "
                              "песочницей для git он быть не может (задайте --basetemp внутри var/)")
    _SANDBOX[:] = [base]
    return base


def sandbox_root() -> Path:
    if not _SANDBOX:
        raise GitSandboxError("песочница git не объявлена: помощник работает только внутри прогона pytest "
                              "(tests/conftest.py объявляет каталог временных файлов прогона)")
    return _SANDBOX[0]


def session_ceiling(environ: Mapping[str, str] | None = None) -> str:
    """Значение потолка для всего прогона: корень рабочей копии и то, что уже задано."""
    seen = [p for p in (environ if environ is not None else os.environ).get(CEILING_ENV, "").split(os.pathsep) if p]
    return os.pathsep.join([str(ROOT)] + [p for p in seen if not _same(p, ROOT)])


def sandbox_env(directory, environ: Mapping[str, str] | None = None) -> dict:
    """Окружение для git, работающего с репозиторием РОВНО в `directory`: потолок —
    родитель каталога и корень рабочей копии; переменных, задающих репозиторий мимо
    каталога, нет; вопросов в терминал git не задаёт."""
    env = dict(environ if environ is not None else os.environ)
    for name in REPO_ENV:
        env.pop(name, None)
    env[CEILING_ENV] = os.pathsep.join([str(_real(directory).parent), str(ROOT)])
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def require_sandbox(directory, what: str = "git") -> Path:
    """Каталог обязан лежать в песочнице прогона; рабочая копия и всё вне песочницы — отказ."""
    base = sandbox_root()
    path = _real(directory)
    if not _within(path, base):
        raise GitSandboxError(f"{what}: каталог {path} вне песочницы прогона ({base}) — команда не исполнена. "
                              "Git в тестах пишет только во временный каталог теста (tmp_path)")
    return path


def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def git_dir_of(directory) -> Path | None:
    """Каталог репозитория, с которым git работал бы в `directory` под потолком; None — репозитория нет."""
    done = _run(["git", "-C", str(directory), "rev-parse", "--absolute-git-dir"], sandbox_env(directory))
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def require_own_repository(directory, what: str = "git") -> Path:
    """Перед записью: репозиторий git лежит ровно в `directory` (рабочее дерево с `.git`
    или голый репозиторий) и нигде выше. Каталог без годного `.git` — отказ, а не подъём
    к объемлющему репозиторию."""
    path = require_sandbox(directory, what)
    found = git_dir_of(path) if path.is_dir() else None
    if found is None:
        raise GitSandboxError(f"{what}: в каталоге {path} нет репозитория git (каталог исчез или лишился .git) — "
                              "команда не исполнена: без потолка она ушла бы в объемлющий репозиторий")
    if not (_same(found, path / ".git") or _same(found, path)):
        raise GitSandboxError(f"{what}: git в каталоге {path} работает с чужим репозиторием ({found}) — "
                              "команда не исполнена")
    return path


def sandbox_git(*args: str, repo, config: Iterable[str] = (), check: bool = True) -> str:
    """`git -C <repo> <команда> …` в песочнице; возвращает stdout без краёв.

    `repo` — каталог репозитория (рабочее дерево или голый репозиторий) во временном
    каталоге теста; `config` — настройки `ключ=значение` (идут через `-c`). Первым
    аргументом — имя команды git: читающим командам хватает каталога в песочнице и
    потолка, перед пишущей проверяется, что репозиторий лежит ровно в `repo`.
    Репозиторий создают `sandbox_init` и `sandbox_clone`."""
    assert args and not args[0].startswith("-"), "первым — имя команды git; настройки — параметром config"
    what = f"git {args[0]}"
    assert args[0] not in CREATING, f"{what}: репозиторий создают sandbox_init и sandbox_clone"
    path = require_sandbox(repo, what) if args[0] in READ_ONLY else require_own_repository(repo, what)
    cmd = ["git", "-C", str(path), "-c", "core.autocrlf=false"]
    for item in config:
        cmd += ["-c", item]
    done = _run(cmd + list(args), sandbox_env(path))
    if check:
        assert done.returncode == 0, f"{what} → код {done.returncode}: {done.stderr.strip()}"
    return done.stdout.strip()


def _create(command: str, target, options: tuple[str, ...], source: str | None) -> Path:
    what = f"git {command}"
    path = require_sandbox(target, what)
    parent = path.parent
    if not _same(parent, sandbox_root()):
        require_sandbox(parent, what)
    assert parent.is_dir(), f"{what}: нет каталога {parent}"
    cmd = ["git", "-C", str(parent), "-c", "core.autocrlf=false", command, *options]
    done = _run(cmd + ([source] if source is not None else []) + [str(path)], sandbox_env(path))
    assert done.returncode == 0, f"{what} → код {done.returncode}: {done.stderr.strip()}"
    return require_own_repository(path, what)


def sandbox_init(target, *options: str) -> Path:
    """`git init <options> <target>`: новый репозиторий во временном каталоге теста."""
    return _create("init", target, options, None)


def sandbox_clone(source, target, *options: str) -> Path:
    """`git clone <options> <source> <target>`: клон во временный каталог теста."""
    return _create("clone", target, options, str(source))
