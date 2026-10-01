# -*- coding: utf-8 -*-
"""Канал данных: `ops/publish.py` на локальном `git init --bare` вместо GitHub.

Сеть не нужна: «репозиторий данных» — голый репозиторий во временном каталоге,
«боевая дверь» — подменённый `urlopen`. Проверяется поведение: что лежит в ветке
после публикации (компактные выпуски), кто автор коммита (только из списка), повтор
без дублей, история и журнал, чужой push, отказ push, откат (и откат при висящей
сверке), сверка, сторож объёма ветки, охрана каталога клона, git без хуков.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from tests.support_git import sandbox_clone, sandbox_git, sandbox_init

ROOT = Path(__file__).resolve().parents[1]
# Адрес — из .github/commit-emails.allow (гигиена дерева); автор коммитов песочницы.
NAME, EMAIL = "ML371KL", "304002195+ML371KL@users.noreply.github.com"


def _load_publish():
    spec = importlib.util.spec_from_file_location("publish_under_test", ROOT / "ops" / "publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publish = _load_publish()


# Автор коммитов песочницы — настройками команды, без записи в конфигурацию.
WHO = ("user.name=t", f"user.email={EMAIL}")


def _git(*args, cwd, config=()) -> str:
    """Git песочницы: репозиторий — ровно в `cwd`, во временном каталоге теста; команда
    не поднимается к объемлющему репозиторию и перед записью сверяет свой каталог
    (tests/support_git.py)."""
    return sandbox_git(*args, repo=cwd, config=config)


def _release(value: float, generated_at: str = "2026-09-28T17:30:00+00:00", **meta) -> dict:
    payload = {"schema": "sber-v1",
               "meta": {"generated_at": generated_at, "engine_commit": "e" * 40, "fast": False,
                        "valuation_date": "2026-09-28", "facts_date": "2026-06-30",
                        "book_version": "1.0", "previous_sha256": None, **meta},
               "market": {"prices": {"T1": {"price": 300.0}, "T2": {"price": 290.0}}},
               "fair_value": {"headline": {"median": value, "printed_median": round(value / 5) * 5,
                                           "band80": [value - 50, value + 50],
                                           "band50": [value - 20, value + 20], "point": value + 3}},
               "live": {"fetched_at": "2026-09-28T17:00:00+00:00", "applied": True},
               "nowcast": {"journal": {"entries": [{"id": 1, "release_sha": "a" * 12, "forecast": 1.0}],
                                       "releases": {"a" * 12: {"book_version": "1.0"}}}},
               "changes": None, "valuation_history": {"date": []}}
    payload["meta"]["payload_sha256"] = publish.payload_hash(payload)
    payload["meta"]["bytes"] = publish.compact_bytes(payload)
    return payload


def _write_local(cfg, payload: dict) -> str:
    cfg.local_release.parent.mkdir(parents=True, exist_ok=True)
    cfg.local_release.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                                 encoding="utf-8")
    return payload["meta"]["payload_sha256"]


def _allow(repo: Path, *emails: str) -> None:
    """Список адресов авторов в «репозитории кода» песочницы."""
    path = repo / ".github" / "commit-emails.allow"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# адреса авторов\n" + "".join(e + "\n" for e in emails), encoding="utf-8")


@pytest.fixture
def code_repo(tmp_path):
    repo = tmp_path / "code"
    sandbox_init(repo, "-q", "-b", "main")
    _git("commit", "-q", "--allow-empty", "-m", "код", cwd=repo, config=WHO)
    _allow(repo, EMAIL)
    return repo


@pytest.fixture
def channel(tmp_path, code_repo):
    """Голый «репозиторий данных» с веткой release (создана `--init`) и настройки."""
    bare = tmp_path / "data.git"
    sandbox_init(bare, "--quiet", "--bare")
    naps = []
    cfg = publish.Config(state_dir=tmp_path / "state", remote=str(bare), git_name=NAME,
                         git_email=EMAIL, public_url="https://door.test/api/model",
                         verify_timeout=0, verify_pause=0, push_backoff=(0.0,),
                         code_root=code_repo, sleep=naps.append)
    cfg.state_dir.mkdir()
    assert publish.cmd_init(cfg) == 0
    return cfg, bare, naps


def _tree(bare, ref="release") -> list[str]:
    return sorted(_git("ls-tree", "-r", "--name-only", ref, cwd=bare).splitlines())


def _show(bare, path, ref="release"):
    return json.loads(_git("show", f"{ref}:{path}", cwd=bare))


def _commits(bare, ref="release") -> int:
    return int(_git("rev-list", "--count", ref, cwd=bare))


# ------------------------------------------------------------ идентичность

@pytest.mark.parametrize("name,email", [("", EMAIL), (NAME, ""), (NAME, "someone@example.com"),
                                        (NAME, "x@users.noreply.github.com.evil.test"),
                                        (NAME, "1+other" + "@users.noreply.github.com")])
def test_publishing_refuses_a_missing_or_personal_identity(channel, name, email):
    """Без явного noreply-адреса ИЗ СПИСКА `.github/commit-emails.allow` коммита нет:
    git подставил бы адрес машины или личную почту в публичную историю, а чужой
    noreply привязал бы её к постороннему аккаунту; удалить задним числом нельзя."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    cfg.git_name, cfg.git_email = name, email
    before = _commits(bare)
    with pytest.raises(publish.PublishError):
        publish.cmd_publish(cfg)
    assert _commits(bare) == before
    assert not cfg.pushed_mark.exists()


def test_publishing_refuses_when_the_allowlist_is_missing(channel, code_repo):
    """Нет списка адресов — автора не проверить: отказ, а не «любой noreply»."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    (code_repo / ".github" / "commit-emails.allow").unlink()
    before = _commits(bare)
    with pytest.raises(publish.PublishError, match="commit-emails.allow"):
        publish.cmd_publish(cfg)
    assert _commits(bare) == before


def test_the_identity_list_is_the_one_of_the_repository():
    """По умолчанию список — `.github/commit-emails.allow` этого репозитория (тот же,
    что сверяют гигиена дерева и шаг CI «Гигиена истории»)."""
    cfg = publish.Config(state_dir=ROOT / "var" / "state", git_name=NAME, git_email=EMAIL)
    assert publish.identity(cfg) == (NAME, EMAIL)
    assert EMAIL in publish.allowed_emails(cfg)


def test_the_command_line_turns_a_refusal_into_code_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BANK_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SBER_GIT_NAME", raising=False)
    monkeypatch.delenv("SBER_GIT_EMAIL", raising=False)
    assert publish.main([]) == 1
    assert "ПУБЛИКАЦИЯ: не заданы SBER_GIT_NAME/SBER_GIT_EMAIL" in capsys.readouterr().err


# --------------------------------------------------------------- публикация

def test_a_publish_puts_release_pointer_and_history_in_one_commit(channel, code_repo):
    cfg, bare, _ = channel
    digest = _write_local(cfg, _release(300.0))
    assert publish.cmd_publish(cfg) == 0

    assert _tree(bare) == [".nojekyll", "history.json", "latest.json", f"releases/{digest}.json"]
    changed = _git("show", "--name-only", "--format=", "release", cwd=bare).splitlines()
    assert sorted(changed) == ["history.json", "latest.json", f"releases/{digest}.json"], "один коммит"

    latest, frozen = _show(bare, "latest.json"), _show(bare, f"releases/{digest}.json")
    stamp = latest["meta"]["published_at"]
    assert publish.parse_stamp(stamp) is not None and stamp.endswith("+00:00")
    assert frozen == latest
    assert latest["meta"]["payload_sha256"] == digest == publish.payload_hash(latest), \
        "published_at в хэш не входит"

    who = _git("log", "-1", "--format=%an|%ae|%cn|%ce", "release", cwd=bare)
    assert who == f"{NAME}|{EMAIL}|{NAME}|{EMAIL}"

    [row] = _show(bare, "history.json")
    assert row["event"] == "publish" and row["payload_sha256"] == digest and row["published_at"] == stamp
    assert row["previous_sha256"] is None
    assert row["prices"] == {"T1": 300.0, "T2": 290.0} and row["median"] == 300.0
    assert row["band80"] == [250.0, 350.0] and row["printed_median"] == 300
    for key in ("generated_at", "valuation_date", "facts_date", "book_version", "engine_commit", "bytes",
                "band50", "point"):
        assert key in row, key

    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    assert mark["kind"] == "publish" and mark["payload_sha256"] == digest and mark["published_at"] == stamp
    assert mark["code_commit"] == _git("rev-parse", "HEAD", cwd=code_repo)
    assert mark["release_commit"] == _git("rev-parse", "release", cwd=bare)
    assert _git("config", "gc.autoDetach", cwd=cfg.clone) == "false"
    assert not cfg.built_mark.exists(), "release.commit пишет только сверка"


def test_releases_are_written_compactly(channel):
    """Ветка данных только дописывается, а сайт GitHub Pages ограничен 1 ГБ: выпуск и
    указатель пишутся без отступов (на треть меньше), строгим JSON с переводом строки."""
    cfg, bare, _ = channel
    payload = _release(300.0)
    digest = _write_local(cfg, payload)
    assert publish.cmd_publish(cfg) == 0
    frozen = _git("show", f"release:releases/{digest}.json", cwd=bare)
    pointer = _git("show", "release:latest.json", cwd=bare)
    assert frozen == pointer and "\n" not in frozen, "одна строка, без отступов"
    assert '": ' not in frozen and '", "' not in frozen, "разделители — запятая и двоеточие без пробелов"
    stamped = dict(payload, meta=dict(payload["meta"], published_at=json.loads(frozen)["meta"]["published_at"]))
    assert frozen == json.dumps(stamped, ensure_ascii=False, separators=(",", ":"))
    padded = json.dumps(stamped, ensure_ascii=False, indent=1)
    assert len(frozen.encode("utf-8")) < len(padded.encode("utf-8"))
    assert publish.payload_hash(json.loads(frozen)) == digest


def test_the_size_guard_raises_the_alarm_after_the_release_is_out(channel, capsys):
    """Сторож объёма (INTERFACES §6): дерево ветки доросло до порога — выпуск опубликован,
    отметка сверки записана, код 3 (тревога такта), а не отказ."""
    cfg, bare, _ = channel
    digest = _write_local(cfg, _release(300.0))
    assert publish.cmd_publish(cfg) == 0, "под порогом бюджета (700 МБ) — тихо"
    assert "ПУБЛИКАЦИЯ:" not in capsys.readouterr().err

    cfg.alert_bytes = publish.tree_bytes(cfg.clone)             # порог = нынешний размер дерева
    digest = _write_local(cfg, _release(310.0))
    assert publish.cmd_publish(cfg) == 3
    err = capsys.readouterr().err
    assert "ПУБЛИКАЦИЯ: дерево ветки release" in err and "порог сторожа" in err and "1 ГБ" in err
    assert _show(bare, "latest.json")["meta"]["payload_sha256"] == digest
    assert json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))["payload_sha256"] == digest

    cfg.alert_bytes = publish.tree_bytes(cfg.clone) * 10
    cfg.pushed_mark.unlink()
    assert publish.cmd_publish(cfg) == 0, "тот же выпуск под порогом — без тревоги"


def test_the_size_guard_measures_the_tree_without_the_git_directory(channel):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    files = [p for p in cfg.clone.rglob("*") if p.is_file() and ".git" not in p.relative_to(cfg.clone).parts]
    assert publish.tree_bytes(cfg.clone) == sum(p.stat().st_size for p in files)
    assert {p.name for p in files} >= {"latest.json", "history.json", ".nojekyll"}


def test_the_same_release_is_not_published_twice(channel, monkeypatch, capsys):
    """Повтор без дублей (PAYLOAD §0.4): тот же хэш — ни коммита, ни строки
    истории; сверке передаётся момент того, что уже в указателе."""
    cfg, bare, _ = channel
    digest = _write_local(cfg, _release(300.0))
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-28T17:31:00+00:00")
    assert publish.cmd_publish(cfg) == 0
    before = _commits(bare)
    cfg.pushed_mark.unlink()
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-29T17:31:00+00:00")
    assert publish.cmd_publish(cfg) == 0
    assert _commits(bare) == before
    assert len(_show(bare, "history.json")) == 1
    assert "повторно не публикуется" in capsys.readouterr().out
    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    assert mark["kind"] == "same" and mark["published_at"] == "2026-09-28T17:31:00+00:00"
    assert mark["payload_sha256"] == digest and mark["code_commit"]


def test_an_older_release_keeps_its_file_and_gets_a_new_pointer(channel, monkeypatch):
    """Те же входы, что у прошлого выпуска: файл выпуска не переписывается,
    указатель получает новый момент, история — строку со ссылкой на прежний."""
    cfg, bare, _ = channel
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-28T17:31:00+00:00")
    first = _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-29T17:31:00+00:00")
    second = _write_local(cfg, _release(310.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-30T17:31:00+00:00")
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    assert _show(bare, f"releases/{first}.json")["meta"]["published_at"] == "2026-09-28T17:31:00+00:00"
    assert _show(bare, "latest.json")["meta"]["published_at"] == "2026-09-30T17:31:00+00:00"
    rows = _show(bare, "history.json")
    assert [r["payload_sha256"] for r in rows] == [first, second, first]
    assert [r["previous_sha256"] for r in rows] == [None, first, second]
    assert _commits(bare) == 4


def _journal(path: Path) -> dict:
    from indicators.journal import Journal

    j = Journal(path)
    j.record_forecast(target="ni_q", period="2026Q3", horizon="T-30", forecast=450.0,
                      benchmarks={"prev_quarter": 440.0}, release_sha="a" * 12)
    return Journal(path, readonly=True).export()


def test_the_full_journal_goes_to_the_data_branch(channel):
    cfg, bare, _ = channel
    expected = _journal(cfg.journal_db)
    _write_local(cfg, _release(300.0))
    assert publish.cmd_publish(cfg) == 0
    assert "journal.json" in _tree(bare)
    assert _show(bare, "journal.json") == json.loads(json.dumps(expected, ensure_ascii=False))


def test_an_unreadable_journal_is_an_alarm_not_a_lost_release(channel, capsys):
    cfg, bare, _ = channel
    cfg.journal_db.write_bytes(b"not a database at all" * 10)
    digest = _write_local(cfg, _release(300.0))
    assert publish.cmd_publish(cfg) == 3
    assert _show(bare, "latest.json")["meta"]["payload_sha256"] == digest
    assert "journal.json" not in _tree(bare)
    assert "журнал прогнозов не выгружен" in capsys.readouterr().err


def test_a_foreign_push_does_not_block_the_next_publish(channel, tmp_path):
    """Откат с ноутбука или чужой push: клон одноразовый. `.github/` и посторонние
    пути снимаются; README.md ветки данных остаётся."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)

    laptop = tmp_path / "laptop"
    sandbox_clone(bare, laptop, "--quiet", "--branch", "release")
    (laptop / ".github" / "workflows").mkdir(parents=True)
    (laptop / ".github" / "workflows" / "x.yml").write_text("on: push\n", encoding="utf-8")
    (laptop / "notes.txt").write_text("заметка\n", encoding="utf-8")
    (laptop / "README.md").write_text("данные панели\n", encoding="utf-8")
    _git("add", "-A", cwd=laptop)
    _git("commit", "-q", "-m", "чужое", cwd=laptop, config=WHO)
    _git("push", "-q", "origin", "release", cwd=laptop)

    digest = _write_local(cfg, _release(310.0))
    assert publish.cmd_publish(cfg) == 0
    tree = _tree(bare)
    assert ".github/workflows/x.yml" not in tree and "notes.txt" not in tree and "README.md" in tree
    assert _show(bare, "latest.json")["meta"]["payload_sha256"] == digest


def test_releases_are_never_removed_or_rewritten(channel, monkeypatch):
    """Ветка только дописывается: каждый опубликованный выпуск остаётся в дереве
    с первым моментом публикации."""
    cfg, bare, _ = channel
    shas = []
    for i, value in enumerate((300.0, 305.0, 310.0)):
        monkeypatch.setattr(publish, "now_stamp", lambda i=i: f"2026-09-2{i}T17:31:00+00:00")
        shas.append(_write_local(cfg, _release(value)))
        publish.cmd_publish(cfg)
    tree = _tree(bare)
    for i, sha in enumerate(shas):
        assert f"releases/{sha}.json" in tree
        assert _show(bare, f"releases/{sha}.json")["meta"]["published_at"] == f"2026-09-2{i}T17:31:00+00:00"
    deleted = _git("log", "--diff-filter=D", "--name-only", "--format=", "release", cwd=bare)
    assert deleted == "", "ни одного удалённого файла в истории ветки"


def test_a_failed_push_is_retried_from_a_clean_clone(channel, tmp_path):
    cfg, bare, naps = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    hidden = tmp_path / "away.git"
    bare.rename(hidden)

    def come_back(seconds):
        naps.append(seconds)
        if hidden.exists():
            hidden.rename(bare)

    cfg.sleep = come_back
    digest = _write_local(cfg, _release(320.0))
    assert publish.cmd_publish(cfg) == 0
    assert naps == [0.0], "одна пауза между первой и второй попыткой"
    assert _show(bare, "latest.json")["meta"]["payload_sha256"] == digest


def test_three_failed_attempts_leave_no_mark(channel, tmp_path):
    cfg, bare, naps = channel
    _write_local(cfg, _release(300.0))
    bare.rename(tmp_path / "gone.git")
    with pytest.raises(publish.PublishError, match="3 попытки не удались"):
        publish.cmd_publish(cfg)
    assert len(naps) == 2
    assert not cfg.pushed_mark.exists()


@pytest.mark.parametrize("damage,reason", [
    (lambda p: p["fair_value"]["headline"].update(median=301.0), "не совпадает со своим хэшем"),
    (lambda p: p.update(schema="lenta-v1"), "схема"),
    (lambda p: p["meta"].update(fast=True), "быстрая сборка"),
    (lambda p: p["meta"].pop("payload_sha256"), "нет meta.payload_sha256"),
])
def test_a_damaged_or_fast_local_release_is_not_published(channel, damage, reason):
    cfg, bare, _ = channel
    payload = _release(300.0)
    damage(payload)
    if reason == "быстрая сборка":
        payload["meta"]["payload_sha256"] = publish.payload_hash(payload)
    cfg.local_release.parent.mkdir(parents=True, exist_ok=True)
    cfg.local_release.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(publish.PublishError, match=reason):
        publish.cmd_publish(cfg)
    assert _tree(bare) == [".nojekyll"]


def test_a_non_strict_or_oversized_release_is_not_published(channel, monkeypatch):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    text = cfg.local_release.read_text(encoding="utf-8")
    cfg.local_release.write_text(text.replace('"median":300.0', '"median":NaN'), encoding="utf-8")
    with pytest.raises(publish.PublishError, match="не строгий JSON"):
        publish.cmd_publish(cfg)
    _write_local(cfg, _release(300.0))
    monkeypatch.setattr(publish, "MAX_BYTES", 100)
    with pytest.raises(publish.PublishError, match="больше потолка"):
        publish.cmd_publish(cfg)
    assert _tree(bare) == [".nojekyll"]


def test_the_digest_follows_the_contract_and_ignores_the_moment_of_publication():
    """PAYLOAD §0.4: вне хэша — моменты, ссылки на прошлые выпуски, размер,
    `live.fetched_at`, ссылки журнала на выпуски, блоки changes и valuation_history."""
    payload = _release(300.0)
    base = publish.payload_hash(payload)
    outside = [("meta", "published_at", "2026-09-29T00:00:00+00:00"), ("meta", "generated_at", "x"),
               ("meta", "previous_sha256", "b" * 64), ("meta", "bytes", 1), ("live", "fetched_at", "y")]
    for block, key, value in outside:
        changed = json.loads(json.dumps(payload))
        changed[block][key] = value
        assert publish.payload_hash(changed) == base, (block, key)
    changed = json.loads(json.dumps(payload))
    changed["changes"] = {"vs_previous": {"previous_sha": "c" * 64}}
    changed["valuation_history"] = {"date": ["2026-09-01"]}
    changed["nowcast"]["journal"]["releases"] = {}
    changed["nowcast"]["journal"]["entries"][0]["release_sha"] = "d" * 12
    assert publish.payload_hash(changed) == base
    inside = json.loads(json.dumps(payload))
    inside["nowcast"]["journal"]["entries"][0]["forecast"] = 2.0
    assert publish.payload_hash(inside) != base
    inside = json.loads(json.dumps(payload))
    inside["meta"]["valuation_date"] = "2026-09-29"
    assert publish.payload_hash(inside) != base


def test_the_digest_matches_the_engine():
    try:
        from model.payload import payload_hash as engine_hash
    except ImportError:
        pytest.skip("model/payload.py ещё нет (поток core2)")
    payload = _release(300.0)
    assert publish.payload_hash(payload) == engine_hash(payload)
    stamped = dict(payload, meta=dict(payload["meta"], published_at="2026-09-28T17:31:00+00:00"))
    assert engine_hash(stamped) == publish.payload_hash(stamped)


def test_init_creates_an_orphan_branch_once(tmp_path):
    bare = tmp_path / "empty.git"
    sandbox_init(bare, "--quiet", "--bare")
    cfg = publish.Config(state_dir=tmp_path / "s", remote=str(bare), git_name=NAME, git_email=EMAIL)
    assert publish.cmd_init(cfg) == 0
    assert _tree(bare) == [".nojekyll"] and _commits(bare) == 1
    assert publish.cmd_init(cfg) == 0 and _commits(bare) == 1, "второй init ничего не делает"


def test_a_dry_run_changes_nothing(channel, capsys):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    before = _commits(bare)
    assert publish.cmd_publish(cfg, dry_run=True) == 0
    assert _commits(bare) == before and not cfg.pushed_mark.exists()
    assert "коммит был бы таким" in capsys.readouterr().out
    assert _git("status", "--porcelain", cwd=cfg.clone) == "", "клон вернулся к ветке"


def test_sync_brings_the_clone_to_the_branch(channel, tmp_path):
    """Сборка читает прошлый выпуск из клона: он обязан совпадать с веткой."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    laptop = tmp_path / "laptop2"
    sandbox_clone(bare, laptop, "--quiet", "--branch", "release")
    (laptop / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=laptop)
    _git("commit", "-q", "-m", "с ноутбука", cwd=laptop, config=WHO)
    _git("push", "-q", "origin", "release", cwd=laptop)
    (cfg.clone / "stray.txt").write_text("хвост\n", encoding="utf-8")
    assert publish.cmd_sync(cfg) == 0
    assert (cfg.clone / "README.md").exists() and not (cfg.clone / "stray.txt").exists()
    assert _git("rev-parse", "HEAD", cwd=cfg.clone) == _git("rev-parse", "release", cwd=bare)


# ------------------------------------------------------------------ сверка

class _Door:
    """Подменённая боевая дверь: отдаёт по очереди заказанные ответы."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        assert request.get_header("User-agent"), "без UA дверь отвечает 403"
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if callable(answer):
            answer = answer()
        if isinstance(answer, Exception):
            raise answer
        body, headers = answer

        class Response:
            def __enter__(self_):
                return self_

            def __exit__(self_, *exc):
                return False

            def read(self_):
                return body.encode("utf-8")

        response = Response()
        response.headers = headers
        return response


def _served(bare, source="pages", **extra_headers):
    body = _git("show", "release:latest.json", cwd=bare)
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Last-Modified": "Mon, 28 Sep 2026 17:31:00 GMT", "x-data-source": source}
    headers.update(extra_headers)
    return body, {k: v for k, v in headers.items() if v is not None}


def test_verify_confirms_the_pushed_release_and_remembers_its_code(channel, monkeypatch, capsys):
    """Удачная сверка: отметка `release.commit` и строка журнала «СВЕРКА: выпуск <sha12> … —
    пройдена …» — по ним принимают установку и каждый выпуск (ops/README.md §5)."""
    cfg, bare, _ = channel
    digest = _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    want_code = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))["code_commit"]
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare)))
    capsys.readouterr()
    assert publish.cmd_verify(cfg) == 0
    out = capsys.readouterr().out
    assert re.search(rf"^СВЕРКА: выпуск {digest[:12]} · \S+ — пройдена с попытки 1, Last-Modified ", out, re.M), out
    assert cfg.built_mark.read_text(encoding="utf-8").strip() == want_code
    assert not cfg.pushed_mark.exists()
    assert publish.cmd_verify(cfg) == 0, "повторная сверка без публикации — нечего сверять"
    assert "пройдена" not in capsys.readouterr().out, "сверять нечего — строки приёмки нет"


def test_verify_of_a_same_release_confirms_the_new_code(channel, monkeypatch, code_repo):
    """Пересборка на новом коде дала тот же выпуск: публикации нет, но сверка
    подтверждает указатель и помнит новый код — пересборка не крутится."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare)))
    publish.cmd_verify(cfg)
    _git("commit", "-q", "--allow-empty", "-m", "код 2", cwd=code_repo, config=WHO)
    assert publish.cmd_publish(cfg) == 0
    assert publish.cmd_verify(cfg) == 0
    assert cfg.built_mark.read_text(encoding="utf-8").strip() == _git("rev-parse", "HEAD", cwd=code_repo)


def test_verify_waits_for_the_new_moment_not_just_the_same_content(channel, monkeypatch, capsys):
    """Откат на тот же выпуск или повторная публикация: по одному хэшу сверка
    прошла бы сразу, даже если новый коммит до Pages не доехал."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-28T17:31:00+00:00")
    publish.cmd_publish(cfg)
    old = _served(bare)
    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    mark["published_at"] = "2026-09-29T17:31:00+00:00"
    cfg.pushed_mark.write_text(json.dumps(mark, indent=1), encoding="utf-8")
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(old))
    assert publish.cmd_verify(cfg) == 3
    err = capsys.readouterr().err
    assert "ту же сборку прошлой публикации" in err and "дожмут следующие такты" in err
    assert cfg.pushed_mark.exists() and not cfg.built_mark.exists()


def test_verify_times_out_with_code_three_and_keeps_the_mark(channel, monkeypatch, capsys):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    cfg.verify_timeout, cfg.verify_pause = 0.3, 0.1
    napped = []
    cfg.sleep = napped.append

    def not_yet():
        return urllib.error.HTTPError(cfg.public_url, 503, "Service Unavailable", {},
                                      io.BytesIO(b'{"error":"upstream unavailable"}'))

    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(not_yet))
    assert publish.cmd_verify(cfg) == 3
    err = capsys.readouterr().err
    assert "дверь ответила 503" in err and "upstream unavailable" in err
    assert cfg.pushed_mark.exists() and not cfg.built_mark.exists()
    assert napped and all(n <= 0.1 for n in napped)


def test_verify_once_is_a_single_request(channel, monkeypatch):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    cfg.verify_timeout = 600
    door = _Door(urllib.error.URLError("нет сети"))
    monkeypatch.setattr(publish.urllib.request, "urlopen", door)
    assert publish.cmd_verify(cfg, once=True) == 3
    assert door.calls == 1


@pytest.mark.parametrize("source", ["raw", "edge-cache"])
def test_a_release_served_by_the_fallback_counts_but_raises_the_alarm(channel, monkeypatch, capsys, source):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare, source)))
    assert publish.cmd_verify(cfg) == 3
    assert cfg.built_mark.exists() and not cfg.pushed_mark.exists()
    assert f"запасной источник «{source}»" in capsys.readouterr().err


@pytest.mark.parametrize("headers,reason", [({"Last-Modified": None}, "Last-Modified"),
                                            ({"Content-Type": "text/html"}, "content-type")])
def test_verify_demands_the_headers_the_watchman_needs(channel, monkeypatch, capsys, headers, reason):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare, **headers)))
    assert publish.cmd_verify(cfg) == 3
    assert reason in capsys.readouterr().err


def test_verify_reads_as_strictly_as_the_browser(channel, monkeypatch, capsys):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    body, headers = _served(bare)
    broken = re.sub(r'"median":300\.0', '"median":NaN', body, count=1)
    assert broken != body
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door((broken, headers)))
    assert publish.cmd_verify(cfg) == 3
    assert "строгий JSON" in capsys.readouterr().err
    with pytest.raises(ValueError):
        publish.strict_json('{"a": -Infinity}')


def test_the_verify_window_outlasts_the_edge_cache():
    source = (ROOT / "functions" / "api" / "model.js").read_text(encoding="utf-8")
    cache_seconds = int(re.search(r"CACHE_SECONDS\s*=\s*(\d+)", source).group(1))
    assert publish.VERIFY_TIMEOUT >= 5 * cache_seconds
    assert publish.VERIFY_PAUSE < cache_seconds


def test_the_door_address_is_the_pages_project_of_the_dashboard():
    """Адрес по умолчанию — дверь проекта Pages из wrangler.toml."""
    toml = (ROOT / "wrangler.toml").read_text(encoding="utf-8")
    project = re.search(r'^name\s*=\s*"([^"]+)"', toml, re.M).group(1)
    assert publish.DEFAULT_PUBLIC_URL == f"https://{project}.pages.dev/api/model"
    assert publish.SCHEMA == re.search(r'DATA_SCHEMA\s*=\s*"([^"]+)"', toml).group(1)


def test_outgoing_http_headers_are_latin1():
    """Кириллица в User-Agent роняет запрос ещё до отправки — проверено боем."""
    for name, value in publish.HEADERS.items():
        name.encode("latin-1")
        value.encode("latin-1")
    assert publish.HEADERS["user-agent"].strip()


# ------------------------------------------------------------------- откат

def test_rollback_repoints_with_a_new_moment_and_leaves_the_code_mark(channel, monkeypatch):
    cfg, bare, naps = channel
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-01T17:31:00+00:00")
    good = _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-02T17:31:00+00:00")
    bad = _write_local(cfg, _release(500.0))
    publish.cmd_publish(cfg)
    cfg.pushed_mark.unlink()
    cfg.built_mark.write_text("код-бага\n", encoding="utf-8")

    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-03T08:00:00+00:00")
    assert publish.cmd_rollback(cfg, good[:12], note="сборка на неверной цене") == 0
    latest = _show(bare, "latest.json")
    assert latest["meta"]["payload_sha256"] == good
    assert latest["meta"]["published_at"] == "2026-09-03T08:00:00+00:00", "у отката — новый момент"
    assert latest["fair_value"]["headline"]["median"] == 300.0
    row = _show(bare, "history.json")[-1]
    assert row["event"] == "rollback" and row["payload_sha256"] == good
    assert row["previous_sha256"] == bad and row["note"] == "сборка на неверной цене"
    assert f"releases/{bad}.json" in _tree(bare), "откат ничего не удаляет"
    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    assert mark["kind"] == "rollback" and mark["payload_sha256"] == good

    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare)))
    assert publish.cmd_verify(cfg) == 0
    assert cfg.built_mark.read_text(encoding="utf-8") == "код-бага\n", (
        "откат не трогает release.commit: иначе пересборка тут же вернула бы откатываемое")


def test_rollback_of_an_unverified_release_remembers_its_code(channel, monkeypatch, code_repo):
    """Откат выпуска, чья сверка ещё висит: отметку сверки откат перезаписывает своей, и
    без кода этого выпуска в `release.commit` пересборка сочла бы код новым и вернула
    бы выпуск поверх отката. Чужая или устаревшая отметка в `release.commit` не идёт."""
    cfg, bare, _ = channel
    good = _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare)))
    assert publish.cmd_verify(cfg) == 0
    old_code = cfg.built_mark.read_text(encoding="utf-8").strip()

    _git("commit", "-q", "--allow-empty", "-m", "код 2", cwd=code_repo, config=WHO)
    new_code = _git("rev-parse", "HEAD", cwd=code_repo)
    _write_local(cfg, _release(500.0))
    publish.cmd_publish(cfg)                                    # сверка не пройдена: отметка висит
    assert json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))["code_commit"] == new_code != old_code

    assert publish.cmd_rollback(cfg, good[:12], note="плохой выпуск") == 0
    assert cfg.built_mark.read_text(encoding="utf-8").strip() == new_code
    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    assert mark["kind"] == "rollback" and mark["code_commit"] == ""
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare)))
    assert publish.cmd_verify(cfg) == 0
    assert cfg.built_mark.read_text(encoding="utf-8").strip() == new_code, "сверка отката код не трогает"

    # Отметка о другом выпуске (не о том, с которого откатываемся) кода не даёт.
    stale = dict(kind="publish", payload_sha256="f" * 64, published_at="2026-09-01T00:00:00+00:00",
                 code_commit="c" * 40)
    cfg.pushed_mark.write_text(json.dumps(stale), encoding="utf-8")
    bad = _show(bare, "history.json")[1]["payload_sha256"]
    assert publish.cmd_rollback(cfg, bad[:12]) == 0
    assert cfg.built_mark.read_text(encoding="utf-8").strip() == new_code


def test_rollback_to_an_unknown_or_current_release(channel, capsys):
    cfg, bare, naps = channel
    digest = _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    with pytest.raises(publish.PublishError, match="не найден"):
        publish.cmd_rollback(cfg, "0" * 12)
    with pytest.raises(publish.PublishError, match="от 12"):
        publish.cmd_rollback(cfg, "abc")
    assert naps == [], "без повторов: такого выпуска не станет через паузу"
    before = _commits(bare)
    assert publish.cmd_rollback(cfg, digest[:12]) == 0
    assert _commits(bare) == before and "откатывать нечего" in capsys.readouterr().out


def test_state_paths_follow_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.delenv("BANK_DATA_REPO_DIR", raising=False)
    monkeypatch.setenv("SBER_PUSH_BACKOFF", "0")
    monkeypatch.delenv("SBER_PUBLIC_URL", raising=False)
    cfg = publish.Config.from_env()
    for path in (cfg.local_release, cfg.clone, cfg.pushed_mark, cfg.built_mark, cfg.journal_db):
        assert str(path).startswith(str(tmp_path / "st")), path
    assert cfg.clone == tmp_path / "st" / "data-repo"
    assert cfg.local_release == tmp_path / "st" / "release" / "latest.json"
    assert cfg.push_backoff == (0.0,) and cfg.public_url == publish.DEFAULT_PUBLIC_URL
    monkeypatch.setenv("BANK_DATA_REPO_DIR", str(tmp_path / "clone"))
    assert publish.Config.from_env().clone == tmp_path / "clone"


def test_the_pushed_mark_keeps_the_code_commit_on_its_own_line(channel):
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    publish.cmd_publish(cfg)
    text = cfg.pushed_mark.read_text(encoding="utf-8")
    assert re.search(r'^ "code_commit": "[0-9a-f]{40}",$', text, re.M), "строку читает sed в run.sh"


# ------------------------------------------------------ охрана каталога клона

def _state_with_data(base: Path) -> Path:
    """Каталог состояния с журналом и сырым архивом — тем, что нельзя потерять."""
    state = base / "state"
    (state / "raw" / "cbr_forms").mkdir(parents=True)
    (state / "journal.sqlite").write_bytes(b"journal")
    (state / "raw" / "cbr_forms" / "vintage.xml").write_text("винтаж", encoding="utf-8")
    return state


def _listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".git" not in p.parts)


def _guarded(tmp_path, channel, *, data_repo, state=None, code=None, remote=None):
    cfg, bare, _ = channel
    return publish.Config(state_dir=state or cfg.state_dir, data_repo=data_repo,
                          remote=str(bare) if remote is None else remote, git_name=NAME, git_email=EMAIL,
                          push_backoff=(0.0,), code_root=code or cfg.code_root, sleep=lambda s: None)


@pytest.mark.tact
def test_the_clone_guard_refuses_foreign_directories_before_any_command(tmp_path):
    """Тест такта (без сети и без «репозитория данных»): охрана срабатывает до первой
    команды git. Каталог состояния, его предок, непустой подкаталог без `.git`, каталог
    кода и чужой git-репозиторий — отказ с причиной, и ни один файл не тронут."""
    state = _state_with_data(tmp_path)
    code = tmp_path / "code"
    (code / ".venv").mkdir(parents=True)
    (code / ".venv" / "python").write_text("venv", encoding="utf-8")
    foreign = tmp_path / "foreign"
    sandbox_init(foreign, "-q", "-b", "main")
    (foreign / "notes.txt").write_text("чужое", encoding="utf-8")
    before = {root: _listing(root) for root in (state, code, foreign)}
    cases = [(state, "каталогом состояния"), (tmp_path, "лежит выше"), (state / "raw", "не пуст и не клон git"),
             (code, "каталогом кода"), (foreign, "не клон ветки release")]
    for data_repo, reason in cases:
        cfg = publish.Config(state_dir=state, data_repo=data_repo, remote=str(tmp_path / "нет.git"),
                             git_name=NAME, git_email=EMAIL, code_root=code)
        for command in (publish.ensure_clone, publish.cmd_sync):
            with pytest.raises(publish.PublishError, match=reason):
                command(cfg)
    assert {root: _listing(root) for root in (state, code, foreign)} == before
    assert (state / "journal.sqlite").read_bytes() == b"journal"
    default = publish.Config(state_dir=state, remote="", code_root=code)
    assert publish.guard_clone_dir(default) == (state / "data-repo").resolve(), "клон по умолчанию проходит охрану"


@pytest.mark.parametrize("working_remote", [False, True])
def test_the_clone_guard_refuses_the_state_directory(tmp_path, channel, working_remote):
    """Опечатка `BANK_DATA_REPO_DIR` = каталог состояния: раньше каталог без `.git`
    стирался целиком (журнал прогнозов, невосполнимый архив) — молча и на каждом такте."""
    state = _state_with_data(tmp_path / "typo")
    cfg = _guarded(tmp_path, channel, data_repo=state, state=state,
                   remote=None if working_remote else str(tmp_path / "нет.git"))
    _write_local(cfg, _release(300.0))
    before = _listing(state)
    for command in (publish.cmd_sync, publish.cmd_publish):
        with pytest.raises(publish.PublishError, match="каталогом состояния"):
            command(cfg)
    assert _listing(state) == before and (state / "journal.sqlite").read_bytes() == b"journal"


def test_the_clone_guard_refuses_an_ancestor_and_a_filled_directory(tmp_path, channel):
    state = _state_with_data(tmp_path / "typo")
    before = _listing(state)
    with pytest.raises(publish.PublishError, match="лежит выше"):
        publish.cmd_sync(_guarded(tmp_path, channel, data_repo=state.parent, state=state))
    # Подкаталог состояния без .git (сырой архив): не каталог состояния, но и не клон.
    with pytest.raises(publish.PublishError, match="не пуст и не клон git"):
        publish.cmd_sync(_guarded(tmp_path, channel, data_repo=state / "raw", state=state))
    # Файл вместо каталога — тоже чужое.
    with pytest.raises(publish.PublishError, match="не пуст и не клон git"):
        publish.cmd_sync(_guarded(tmp_path, channel, data_repo=state / "journal.sqlite", state=state))
    assert _listing(state) == before


@pytest.mark.parametrize("known_code_root", [True, False])
def test_the_clone_guard_refuses_a_foreign_repository(tmp_path, channel, known_code_root):
    """Каталог кода (git-клон с `.venv`) вместо клона данных: `set-url`, `reset --hard
    origin/release` и `clean -ffdx` снесли бы код и окружение. Отказ — и когда это
    каталог кода самой публикации, и когда чужой репозиторий без ветки release."""
    code = tmp_path / "server-code"
    sandbox_init(code, "-q", "-b", "main")
    (code / "model.py").write_text("x = 1\n", encoding="utf-8")
    (code / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _git("add", "-A", cwd=code)
    _git("commit", "-q", "-m", "код", cwd=code, config=WHO)
    _git("remote", "add", "origin", "https://example.invalid/code.git", cwd=code)
    (code / ".venv").mkdir()
    (code / ".venv" / "python").write_text("venv", encoding="utf-8")
    _allow(code, EMAIL)
    before = _listing(code)
    cfg = _guarded(tmp_path, channel, data_repo=code, code=code if known_code_root else None)
    reason = "каталогом кода" if known_code_root else "не клон ветки release"
    with pytest.raises(publish.PublishError, match=reason):
        publish.cmd_sync(cfg)
    assert _listing(code) == before, "ни файла не тронуто"
    assert _git("remote", "get-url", "origin", cwd=code) == "https://example.invalid/code.git"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=code) == "main"


def test_git_never_climbs_to_the_enclosing_repository(channel):
    """Каталог с негодным `.git` внутри другого репозитория: без потолка git нашёл бы
    объемлющий репозиторий (здесь — настоящий клон данных с веткой release), охрана
    сочла бы каталог клоном, и `reset --hard` с `clean -ffdx` достались бы объемлющему."""
    cfg, bare, _ = channel
    assert publish.cmd_sync(cfg) == 0
    nested = cfg.clone / "nested"
    (nested / ".git").mkdir(parents=True)
    (nested / "keep.txt").write_text("чужое", encoding="utf-8")
    head = _git("rev-parse", "HEAD", cwd=cfg.clone)
    inner = publish.Config(state_dir=cfg.state_dir, data_repo=nested, remote=str(bare), git_name=NAME,
                           git_email=EMAIL, code_root=cfg.code_root)
    with pytest.raises(publish.PublishError, match="не клон ветки release"):
        publish.cmd_sync(inner)
    assert (nested / "keep.txt").read_text(encoding="utf-8") == "чужое"
    assert _git("rev-parse", "HEAD", cwd=cfg.clone) == head


def test_the_clone_guard_lets_the_real_clone_through(tmp_path, channel):
    """Контроль: клон по умолчанию (data-repo в каталоге состояния), пустой каталог под
    клон и клон, созданный руками, проходят; состояние рядом не тронуто."""
    cfg, bare, _ = channel
    (cfg.state_dir / "journal.sqlite").write_bytes(b"journal")
    assert publish.cmd_sync(cfg) == 0 and (cfg.clone / ".git").exists()
    assert publish.cmd_sync(cfg) == 0, "второй раз — тот же клон"
    assert (cfg.state_dir / "journal.sqlite").read_bytes() == b"journal"

    empty = tmp_path / "elsewhere" / "data-clone"
    empty.mkdir(parents=True)
    assert publish.cmd_sync(_guarded(tmp_path, channel, data_repo=empty)) == 0
    assert (empty / ".nojekyll").exists()

    manual = tmp_path / "manual"
    sandbox_clone(bare, manual, "--quiet", "--branch", "release")
    assert publish.cmd_sync(_guarded(tmp_path, channel, data_repo=manual)) == 0


def test_the_clone_guard_refuses_the_data_clone_of_another_panel(tmp_path, channel):
    """Клон данных соседней панели тоже несёт `origin/release`: без сверки адреса `set-url`
    и `reset --hard` увели бы его на свою ветку. Отказ до любой команды; клон цел, его
    origin не тронут, адреса в сообщении нет (в нём был бы ssh-алиас ключа)."""
    cfg, bare, _ = channel
    other = tmp_path / "neighbour-data.git"
    sandbox_clone(bare, other, "--quiet", "--bare")
    neighbour = tmp_path / "neighbour-clone"
    sandbox_clone(other, neighbour, "--quiet", "--branch", "release")
    (neighbour / "untracked.txt").write_text("чужое", encoding="utf-8")
    head = _git("rev-parse", "HEAD", cwd=neighbour)
    for command in (publish.ensure_clone, publish.cmd_sync):
        with pytest.raises(publish.PublishError, match="клон другого репозитория данных") as refusal:
            command(_guarded(tmp_path, channel, data_repo=neighbour))
        assert "neighbour-data" not in str(refusal.value)
    assert _git("remote", "get-url", "origin", cwd=neighbour) == str(other)
    assert _git("rev-parse", "HEAD", cwd=neighbour) == head
    assert (neighbour / "untracked.txt").read_text(encoding="utf-8") == "чужое"


@pytest.mark.tact
@pytest.mark.parametrize("remote,repo", [
    ("alias-a:Owner/panel-data.git", "owner/panel-data"),
    ("alias-b:owner/panel-data", "owner/panel-data"),
    ("git" + "@github.com:owner/panel-data.git", "owner/panel-data"),          # по частям: не почтовый адрес
    ("ssh://git" + "@github.com/owner/panel-data.git", "owner/panel-data"),
    ("https://github.com/owner/panel-data.git/", "owner/panel-data"),
    ("alias-a:owner/other-data.git", "owner/other-data"),
    ("C:\\work\\data.git", "c:/work/data"),
    ("/srv/work/data.git", "/srv/work/data"),
])
def test_the_repository_of_a_remote_ignores_the_alias_and_the_suffix(remote, repo):
    """Репозиторий в адресе — без хоста, ssh-алиаса и «.git»: смена алиаса ключа — тот же
    репозиторий (адрес клона переставляется), другой репозиторий — отказ охраны."""
    assert publish.repo_of(remote) == repo


def test_a_new_alias_of_the_same_repository_is_accepted(tmp_path, channel):
    """Тот же репозиторий под другим адресом (сменили ssh-алиас ключа): клон остаётся,
    origin переставляется на адрес из окружения."""
    cfg, bare, _ = channel
    assert publish.cmd_sync(cfg) == 0
    _git("remote", "set-url", "origin", str(bare) + "/", cwd=cfg.clone)
    assert publish.cmd_sync(cfg) == 0
    assert _git("remote", "get-url", "origin", cwd=cfg.clone) == str(bare)


# ------------------------------------------------------------ git без хуков

def test_git_runs_without_the_hooks_and_the_monitor_of_the_clone(channel, tmp_path):
    """Клон данных пишет тот же пользователь, что разбирает чужой ввод: хук или монитор
    файлов, подложенный в клон, исполнился бы в такте с ключом записи. Каждая команда
    git публикации идёт с `core.hooksPath=/dev/null` и `core.fsmonitor=false`."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(300.0))
    assert publish.cmd_publish(cfg) == 0
    marker = tmp_path / "исполнено.txt"
    script = "#!/bin/sh\necho x >> '" + marker.as_posix() + "'\nexit 0\n"
    hooks = cfg.clone / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "post-commit", "post-checkout", "pre-push", "reference-transaction"):
        (hooks / name).write_text(script, encoding="utf-8", newline="\n")
        (hooks / name).chmod(0o755)
    monitor = tmp_path / "monitor.sh"
    monitor.write_text(script, encoding="utf-8", newline="\n")
    monitor.chmod(0o755)
    _git("config", "core.fsmonitor", monitor.as_posix(), cwd=cfg.clone)

    # Проверка самой ловушки: обычный git в этом клоне хук исполняет.
    _git("commit", "-q", "--allow-empty", "-m", "ловушка", cwd=cfg.clone, config=WHO)
    if not marker.exists():
        pytest.skip("на этой машине git не исполняет хуки клона — ловушка не взводится")
    marker.unlink()

    for value in (310.0, 320.0):
        _write_local(cfg, _release(value))
        assert publish.cmd_publish(cfg) == 0
    assert publish.cmd_sync(cfg) == 0
    assert publish.cmd_rollback(cfg, _show(bare, "history.json")[0]["payload_sha256"][:12]) == 0
    assert not marker.exists(), "хук или монитор клона данных исполнился в публикации"
    assert publish.GIT_SAFE == ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
