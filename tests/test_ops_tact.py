# -*- coding: utf-8 -*-
"""Помощник такта `ops/tact.py`: окно дозора, отложенное, повтор сбора, проба тревоги.

Отчёт сборщиков называет месяц РСБУ «новым» только в такте, где его приняли;
упади сборка — следующий дозор его бы не увидел. Отложенное копит принятое до
публикации, кандидат МСФО «новый» ровно один раз. Быстрые — в такте сервера.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("tact_under_test", ROOT / "ops" / "tact.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tact = _load()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_STATE_DIR", str(tmp_path))
    return tmp_path


def _report(state: Path, accepted=(), candidates=()):
    (state / tact.REPORT).write_text(json.dumps({
        "mode": "release-watch", "release_watch": {"accepted": list(accepted), "months": []},
        "ifrs_candidates": list(candidates)}, ensure_ascii=False), encoding="utf-8")


def test_news_keeps_accepted_months_until_they_are_published(state, capsys):
    _report(state, accepted=["2026M09"])
    assert tact.cmd_news() == 0
    assert "РСБУ 2026M09" in capsys.readouterr().out
    # Сборка упала; следующий дозор отчёта «принят» уже не видит, но отложенное помнит.
    _report(state)
    assert tact.cmd_news() == 0
    assert tact.pending(state) == {"ras": ["2026M09"], "ifrs": []}
    assert tact.cmd_done() == 0
    _report(state)
    assert tact.cmd_news() == 1, "после публикации нового нет"
    assert "нового не принял" in capsys.readouterr().out


def test_an_ifrs_candidate_is_new_only_once(state):
    url = "ifrs-candidate-a"
    _report(state, candidates=[url])
    assert tact.cmd_news() == 0
    assert tact.pending(state)["ifrs"] == [url]
    tact.cmd_done()
    _report(state, candidates=[url])
    assert tact.cmd_news() == 1, "виденный кандидат второй раз выпуск не будит"
    _report(state, candidates=[url, "ifrs-candidate-b"])
    assert tact.cmd_news() == 0 and tact.pending(state)["ifrs"] == ["ifrs-candidate-b"]


def test_news_without_a_report_is_a_failure(state, capsys):
    assert tact.cmd_news() == 2
    assert "нет отчёта сборщиков" in capsys.readouterr().err


def test_the_window_opens_for_pending_work_and_fails_open(state, monkeypatch):
    monkeypatch.setattr(tact, "in_release_window", lambda today: False)
    assert tact.cmd_window(date(2026, 10, 1)) == 1, "вне окна и без отложенного — тихий выход"
    (state / tact.PENDING).write_text(json.dumps({"ras": ["2026M09"]}), encoding="utf-8")
    assert tact.cmd_window(date(2026, 10, 1)) == 0, "отложенное — работа и вне окна"
    (state / tact.PENDING).unlink()
    monkeypatch.setattr(tact, "in_release_window", lambda today: True)
    assert tact.cmd_window(date(2026, 10, 1)) == 0

    def broken(today):
        raise RuntimeError("календарь не читается")

    monkeypatch.setattr(tact, "in_release_window", broken)
    assert tact.cmd_window(date(2026, 10, 1)) == 0, "сбой проверки — решит сам сборщик"


def test_the_window_uses_the_indicator_calendar(state):
    """Настоящий расчёт окна (календарь индикаторов на пустом состоянии) отвечает
    булевым значением и ничего не печатает."""
    got = tact.in_release_window(date(2026, 10, 12))
    assert isinstance(got, bool)


def test_simulate_accepts_only_irrecoverable_sources_of_the_mode(capsys):
    from indicators import sources

    mode = "collect"
    good = sorted(n for n in sources.MODES[mode] if n in sources.IRRECOVERABLE)
    assert good, "у утреннего такта есть невосполнимые источники — пробовать есть на ком"
    assert tact.cmd_simulate(good[0], mode) == 0
    assert tact.cmd_simulate("нет_такого", mode) == 64
    critical = sorted(sources.CRITICAL)[0]
    assert tact.cmd_simulate(critical, mode) == 64, "критический источник — не проба тревоги"
    assert "ПРОБА ТРЕВОГИ" in capsys.readouterr().err


def test_the_command_line_rejects_unknown_commands(capsys):
    assert tact.main(["weekly"]) == 64
    assert tact.main(["simulate", "x"]) == 64
    assert tact.main(["retry", "x"]) == 64


def test_retry_is_asked_only_by_an_irrecoverable_source(state, capsys):
    """Политика тревог (INTERFACES §6): такт повторяет сбор, только когда отчёт сборщиков
    ставит `retry: true` — отказал невосполнимый источник и его день ещё можно добрать.
    Тревога без признака повтора, отчёта нет или он не читается — повтора нет."""
    assert tact.main(["retry"]) == 1, "нет отчёта — решать не по чему"
    report = {"mode": "collect", "code": 3, "retry": True,
              "details": [{"name": "cbr_forms", "status": "failed", "retry": True},
                          {"name": "cbr", "status": "degraded", "retry": False},
                          {"name": "tinvest", "status": "ok", "retry": False}]}
    (state / tact.REPORT).write_text(json.dumps(report), encoding="utf-8")
    assert tact.main(["retry"]) == 0
    out = capsys.readouterr().out
    assert "cbr_forms" in out and "tinvest" not in out and "cbr," not in out

    for quiet in (dict(report, retry=False), dict(report, retry="да"), {"mode": "collect", "code": 3}, []):
        (state / tact.REPORT).write_text(json.dumps(quiet), encoding="utf-8")
        assert tact.main(["retry"]) == 1, quiet
    (state / tact.REPORT).write_text("{не json", encoding="utf-8")
    assert tact.main(["retry"]) == 1


def test_retry_reads_the_report_the_collectors_write(state):
    """Отчёт — настоящий, слоя индикаторов: отказ невосполнимого источника просит повтор,
    источник без токена (`missing`) и деградация восполнимого — нет."""
    from indicators import collect, sources
    from indicators.store import Store

    store = Store(state)
    today = date(2026, 10, 1)

    def write(results):
        verdict = sources.verdict(results, {})
        collect.write_report(store, "collect", today, results, verdict)
        return verdict.code

    irrecoverable = sorted(sources.IRRECOVERABLE & set(sources.MODES["collect"]))[0]
    assert write([sources.Result(name=irrecoverable, status=sources.FAILED, detail="503", retry=True)]) == 3
    assert tact.main(["retry"]) == 0

    (state / tact.REPORT).unlink()
    assert write([sources.Result(name="tinvest", status=sources.MISSING, detail="нет токена")]) == 3
    assert tact.main(["retry"]) == 1, "без токена повтор ничего не добавит"

    (state / tact.REPORT).unlink()
    recoverable = sorted(set(sources.MODES["collect"]) - sources.IRRECOVERABLE - sources.CRITICAL)[0]
    degraded = sources.Result(name=recoverable)
    degraded.degrade("источник отвечает 403")
    assert write([degraded]) == 0, "восполнимый источник моложе трёх тактов — не тревога"
    assert tact.main(["retry"]) == 1


def test_run_sh_gives_the_token_in_the_modes_that_have_the_tinvest_collector():
    """`ops/run.sh` делит сбор на две команды (сборщик T-Invest с токеном и остальные)
    в режимах, где этот сборщик есть; список режимов в скрипте — из реестра сборщиков."""
    from indicators import collect, sources

    script = (ROOT / "ops" / "run.sh").read_text(encoding="utf-8")
    found = re.findall(r'^TOKEN_MODES="([^"]+)"$', script, re.M)
    assert len(found) == 1, found
    with_token = {mode for mode, names in sources.MODES.items() if collect.TOKEN_COLLECTOR in names}
    assert set(found[0].split()) == with_token
    assert collect.TOKEN_COLLECTOR == "tinvest"
    assert f"--only {collect.TOKEN_COLLECTOR}" in script and f"--skip {collect.TOKEN_COLLECTOR}" in script
    assert collect.selected("collect", collect.TOKEN_COLLECTOR, None) == (collect.TOKEN_COLLECTOR,)
    assert collect.TOKEN_COLLECTOR not in collect.selected("collect", None, collect.TOKEN_COLLECTOR)
