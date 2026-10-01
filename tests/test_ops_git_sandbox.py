# -*- coding: utf-8 -*-
"""Тесты не пишут в объемлющий репозиторий: песочница git и замок каталога временных файлов.

Случай, от которого всё это стоит: два прогона pytest с одним `--basetemp`, второй при
старте стёр каталоги первого, и `git commit` фикстуры из каталога, лишившегося `.git`,
поднялся к рабочей копии — в её ветке появился посторонний пустой коммит. Перед первым
push он уехал бы в публичную историю. Здесь проверяется поведение каждого слоя защиты
(`tests/support_git.py`, `tests/conftest.py`):

* помощник отказывает в рабочей копии и вне песочницы прогона — до команды git;
* каталог без годного `.git` — отказ, а не подъём к объемлющему репозиторию;
* потолок стоит на весь прогон, и «голый» git из каталога песочницы рабочую копию не видит;
* второй прогон pytest с занятым `--basetemp` отказывает при старте.

Сторож «git в тестах — только помощником» — `tests/test_ops_ci.py`.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest, support_git
from tests.support_git import GitSandboxError, sandbox_clone, sandbox_git, sandbox_init

ROOT = Path(__file__).resolve().parents[1]
WHO = ("user.name=t", "user.email=t@t.invalid")


def _asked(cmd: list[str], cwd, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def _seen_repository(cwd, env=None) -> subprocess.CompletedProcess:
    """Git без помощника — один читающий вопрос: «какой репозиторий ты видишь отсюда»."""
    return _asked(["git", "rev-parse", "--show-toplevel"], cwd, env)


def _working_copy() -> tuple[str, str] | None:
    """(HEAD, число коммитов) рабочей копии; None — корень не репозиторий git или коммитов нет."""
    head = _asked(["git", "rev-parse", "--verify", "-q", "HEAD"], ROOT)
    if head.returncode != 0:
        return None
    return head.stdout.strip(), _asked(["git", "rev-list", "--count", "--all"], ROOT).stdout.strip()


def _without_the_ceiling() -> dict:
    return {k: v for k, v in os.environ.items() if k != support_git.CEILING_ENV}


@pytest.mark.tact
@pytest.mark.parametrize("where", [".", "tests", "var", "ops/tools"])
def test_the_helper_refuses_the_working_copy(where):
    """Пишущая команда в рабочей копии (в корне и в любом её каталоге) не исполняется:
    отказ до git, HEAD и число коммитов рабочей копии прежние."""
    before = _working_copy()
    for command in (("commit", "-q", "--allow-empty", "-m", "посторонний"), ("add", "-A"),
                    ("reset", "--hard"), ("config", "user.name", "t"), ("rev-parse", "HEAD")):
        with pytest.raises(GitSandboxError, match="вне песочницы прогона"):
            sandbox_git(*command, repo=ROOT / where, config=WHO)
    with pytest.raises(GitSandboxError, match="вне песочницы прогона"):
        sandbox_init(ROOT / where / "new-repo", "-q")
    with pytest.raises(GitSandboxError, match="вне песочницы прогона"):
        sandbox_clone(ROOT, ROOT / where / "new-clone", "-q")
    assert not (ROOT / where / "new-repo").exists() and not (ROOT / where / "new-clone").exists()
    assert _working_copy() == before


@pytest.mark.tact
def test_a_directory_that_lost_its_repository_does_not_fall_through_to_the_enclosing_one(tmp_path):
    """Сам случай: каталог песочницы есть, а `.git` в нём нет (его стёр второй прогон).
    Без потолка git видит объемлющий репозиторий — ловушка взводится; под потолком
    прогона не видит; помощник отказывает до записи; рабочая копия не тронута."""
    before = _working_copy()
    code = tmp_path / "code"
    code.mkdir()
    under_the_tree = support_git._within(code, ROOT) and before is not None
    if under_the_tree:
        seen = _seen_repository(code, _without_the_ceiling())
        assert seen.returncode == 0 and support_git._same(seen.stdout.strip(), ROOT), (
            "ловушка не взводится: без потолка git из каталога песочницы обязан видеть рабочую копию")
    blind = _seen_repository(code)
    assert blind.returncode != 0 and "not a git repository" in blind.stderr, (blind.stdout, blind.stderr)
    for command in (("commit", "-q", "--allow-empty", "-m", "код"), ("add", "-A"), ("reset", "--hard"),
                    ("status", "--porcelain")):
        with pytest.raises(GitSandboxError, match="нет репозитория git"):
            sandbox_git(*command, repo=code, config=WHO)
    # Каталога уже нет вовсе — тот же отказ, без исключения запуска процесса.
    with pytest.raises(GitSandboxError, match="нет репозитория git"):
        sandbox_git("commit", "-q", "--allow-empty", "-m", "код", repo=tmp_path / "стёрт", config=WHO)
    assert _working_copy() == before


@pytest.mark.tact
def test_a_broken_git_directory_does_not_send_the_command_upwards(tmp_path):
    """Негодный `.git` (пустой каталог): git пропустил бы его и пошёл выше — и к
    настоящему репозиторию внутри песочницы, и к рабочей копии. Отказ до записи."""
    outer = sandbox_init(tmp_path / "outer", "-q", "-b", "main")
    sandbox_git("commit", "-q", "--allow-empty", "-m", "объемлющий", repo=outer, config=WHO)
    head = sandbox_git("rev-parse", "HEAD", repo=outer)
    nested = outer / "nested"
    (nested / ".git").mkdir(parents=True)
    with pytest.raises(GitSandboxError, match="нет репозитория git"):
        sandbox_git("commit", "-q", "--allow-empty", "-m", "посторонний", repo=nested, config=WHO)
    # Каталог внутри настоящего репозитория песочницы, без своего `.git`: тоже не «его» репозиторий.
    plain = outer / "plain"
    plain.mkdir()
    with pytest.raises(GitSandboxError, match="нет репозитория git"):
        sandbox_git("commit", "-q", "--allow-empty", "-m", "посторонний", repo=plain, config=WHO)
    assert sandbox_git("rev-parse", "HEAD", repo=outer) == head
    assert sandbox_git("rev-list", "--count", "HEAD", repo=outer) == "1"


@pytest.mark.tact
def test_the_helper_works_in_the_sandbox(tmp_path):
    """Контроль: в песочнице помощник делает своё — репозиторий, коммит, голый
    репозиторий, клон, push; каталог репозитория каждой записи — ровно названный."""
    before = _working_copy()
    repo = sandbox_init(tmp_path / "repo", "-q", "-b", "main")
    (repo / "a.txt").write_text("а\n", encoding="utf-8")
    sandbox_git("add", "a.txt", repo=repo)
    sandbox_git("commit", "-q", "-m", "первый", repo=repo, config=WHO)
    bare = sandbox_init(tmp_path / "bare.git", "--quiet", "--bare")
    sandbox_git("remote", "add", "origin", str(bare), repo=repo)
    sandbox_git("push", "-q", "origin", "main", repo=repo)
    clone = sandbox_clone(bare, tmp_path / "clone", "-q", "-b", "main")
    assert sandbox_git("rev-parse", "HEAD", repo=clone) == sandbox_git("rev-parse", "main", repo=bare)
    assert support_git._same(support_git.git_dir_of(repo), repo / ".git")
    assert support_git._same(support_git.git_dir_of(bare), bare)
    assert sandbox_git("log", "--format=%an %ae", repo=clone) == "t t@t.invalid"
    with pytest.raises(AssertionError, match="sandbox_init и sandbox_clone"):
        sandbox_git("init", "-q", repo=repo)
    with pytest.raises(AssertionError, match="первым — имя команды"):
        sandbox_git("-c", "user.name=t", "commit", "-m", "x", repo=repo)
    assert _working_copy() == before


@pytest.mark.tact
def test_the_whole_run_is_under_a_ceiling(tmp_path):
    """Потолок — на весь прогон: его наследуют подпроцессы (конвейер, публикация,
    сборка). Переменных, задающих репозиторий мимо каталога, в окружении нет. Чтение
    самой рабочей копии (каталог запуска — её корень) потолок не задевает."""
    ceilings = os.environ.get(support_git.CEILING_ENV, "").split(os.pathsep)
    assert any(support_git._same(c, ROOT) for c in ceilings if c), ceilings
    assert not [name for name in support_git.REPO_ENV if name in os.environ]
    env = support_git.sandbox_env(tmp_path / "repo", {"GIT_DIR": "x", "GIT_WORK_TREE": "y", "PATH": "p"})
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env and env["PATH"] == "p"
    own = env[support_git.CEILING_ENV].split(os.pathsep)
    assert support_git._same(own[0], tmp_path) and support_git._same(own[1], ROOT)
    if _working_copy() is not None:
        seen = _seen_repository(ROOT)
        assert seen.returncode == 0 and support_git._same(seen.stdout.strip(), ROOT)
        deep = _seen_repository(ROOT / "tests")
        assert deep.returncode != 0, "git из каталога под корнем рабочую копию не видит — и не напишет в неё"


@pytest.mark.tact
def test_the_sandbox_cannot_be_the_working_copy_itself():
    """`--basetemp`, равный корню рабочей копии или каталогу над ним, песочницей не
    объявляется; без объявленной песочницы помощник не работает вовсе."""
    declared = support_git.sandbox_root()
    try:
        for base in (ROOT, ROOT.parent):
            with pytest.raises(GitSandboxError, match="песочницей для git он быть не может"):
                support_git.declare_sandbox(base)
        assert support_git.sandbox_root() == declared, "неудачное объявление прежнюю песочницу не снимает"
        support_git._SANDBOX.clear()
        with pytest.raises(GitSandboxError, match="песочница git не объявлена"):
            sandbox_git("status", repo=declared / "x")
    finally:
        support_git.declare_sandbox(declared)


@pytest.mark.tact
def test_a_lock_is_held_by_one_holder_only(tmp_path):
    """Замок каталога временных файлов: второй держатель получает отказ, пока жив
    первый; после него замок свободен. Файл замка лежит рядом с каталогом, не в нём."""
    first = open(tmp_path / "base.lock", "a+b")
    second = open(tmp_path / "base.lock", "a+b")
    try:
        assert conftest._lock_file(first) is True
        assert conftest._lock_file(second) is False
    finally:
        first.close()
    try:
        assert conftest._lock_file(second) is True
    finally:
        second.close()
    assert conftest._hold_the_basetemp(None).startswith("не задан")
    assert "нет родительского каталога" in conftest._hold_the_basetemp(tmp_path / "нет" / "base")


def test_a_second_run_with_the_same_basetemp_is_refused(request):
    """Второй прогон pytest с тем же `--basetemp`, что у идущего, отказывает при старте
    (код 4, причина и подсказка) — и каталоги идущего прогона остаются на месте."""
    base = request.config.getoption("basetemp", None)
    if not base:
        pytest.skip("прогон без --basetemp: временные каталоги нумерует pytest, делить нечего")
    base = Path(os.path.abspath(str(base)))
    assert conftest._BASETEMP_LOCK, "идущий прогон замок не держит"
    marker = base / "жив.txt"
    marker.write_text("каталог идущего прогона", encoding="utf-8")
    done = subprocess.run([sys.executable, "-B", "-m", "pytest", "--collect-only", "-p", "no:cacheprovider",
                           f"--basetemp={base}", "tests/test_ops_git_sandbox.py"],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = done.stdout + done.stderr
    assert done.returncode == 4, out[-2000:]
    assert "занят другим прогоном pytest" in out and "--basetemp=var/pytest-<ключ>-<случайное>" in out, out[-2000:]
    assert "collected" not in out, "второй прогон не дошёл даже до сбора тестов"
    assert marker.read_text(encoding="utf-8") == "каталог идущего прогона"
    marker.unlink()
