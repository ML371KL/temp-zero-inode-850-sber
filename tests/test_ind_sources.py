"""Реестр сборщиков и коды выхода такта."""

from __future__ import annotations

import pytest

from indicators import sources
from indicators.sources import Result

pytestmark = pytest.mark.tact  # быстрые тесты такта (W1/C1)


def test_registry_and_modes():
    assert set(sources.COLLECTORS) == {"tinvest", "iss", "cbr", "cbr_forms", "news", "sber_pdf"}
    for mode, names in sources.MODES.items():
        assert names and set(names) <= set(sources.COLLECTORS), mode
    assert set(sources.DAILY) <= set(sources.MORNING)
    assert sources.CRITICAL <= set(sources.COLLECTORS) and sources.IRRECOVERABLE <= set(sources.COLLECTORS)
    for spec in sources.COLLECTORS.values():
        assert callable(spec.resolve())


def _degraded(name: str, *, retry: bool = True) -> Result:
    r = Result(name)
    r.degrade("причина", retry=retry)
    return r


def test_exit_codes():
    ok = Result("iss")
    assert sources.exit_code([ok]) == 0
    assert sources.exit_code([Result("iss", status=sources.FAILED)]) == 1
    assert sources.exit_code([ok, Result("cbr_forms", status=sources.FAILED)]) == 3
    assert sources.exit_code([ok, Result("tinvest", status=sources.MISSING)]) == 3
    assert sources.exit_code([_degraded("news")]) == 3                       # невосполнимый — сразу
    assert sources.exit_code([_degraded("iss")]) == 3                        # критический — сразу
    assert sources.exit_code([_degraded("register")]) == 3                   # реестр — живой вход оценки
    assert sources.exit_code([Result("iss", status=sources.FAILED), _degraded("news")]) == 1


def test_alarm_policy_recoverable_sources_alarm_on_the_third_tact_in_a_row():
    """В9: восполнимый источник плиток деградировал — код 0 два такта, тревога с третьего подряд; удача сбрасывает."""
    assert sources.DEGRADED_STREAK == 3
    streak: dict[str, int] = {}
    codes = []
    for _ in range(4):
        v = sources.verdict([Result("iss"), _degraded("cbr")], streak)
        streak = v.streak
        codes.append((v.code, streak["cbr"], v.retry))
    assert codes == [(0, 1, False), (0, 2, False), (3, 3, False), (3, 4, False)]
    assert "такт 1 из 3" in sources.verdict([_degraded("cbr")]).lines[0]
    assert sources.verdict([Result("cbr", status=sources.FAILED)], {"cbr": 2}).code == 3     # отказ — та же деградация
    v = sources.verdict([Result("cbr")], streak)
    assert v.code == 0 and "cbr" not in v.streak
    # дозор релиза — по счётчику: расхождение источников снимают поздние источники дня релиза
    assert sources.verdict([_degraded("release_watch")], {}).code == 0
    assert sources.verdict([_degraded("release_watch")], {"release_watch": 2}).code == 3
    # повтор сбора внутри такта счётчик не двигает
    again = sources.verdict([_degraded("cbr")], {"cbr": 2}, counted={"cbr"})
    assert again.code == 0 and again.streak == {"cbr": 2}
    # источник, которого в запуске не было, счётчик сохраняет
    assert sources.verdict([Result("iss")], {"cbr": 2}).streak == {"cbr": 2}


def test_alarm_policy_retry_only_saves_an_irrecoverable_day():
    """В9: признак повтора — только отказ или сетевая деградация невосполнимого источника."""
    assert sources.verdict([Result("news", status=sources.FAILED)]).retry
    assert sources.verdict([_degraded("cbr_forms")]).retry
    assert not sources.verdict([_degraded("sber_pdf", retry=False)]).retry      # нечем распознать — повтор не поможет
    assert not sources.verdict([Result("tinvest", status=sources.MISSING)]).retry   # повтор токена не добавит
    assert not sources.verdict([_degraded("cbr"), Result("iss", status=sources.FAILED)]).retry
    v = sources.verdict([_degraded("sber_pdf", retry=False)])
    assert v.code == 3 and v.codes == {"sber_pdf": 3}


def test_run_turns_a_crash_into_a_failed_source(monkeypatch, tmp_path):
    def boom(ctx):
        raise RuntimeError("сломалось")

    spec = sources.COLLECTORS["cbr"]
    monkeypatch.setattr(sources.Collector, "resolve", lambda self: boom)
    out = sources.run(("cbr",), ctx=None)
    assert out[0].status == sources.FAILED and "сломалось" in out[0].detail
    assert spec.name == "cbr"


def test_the_tile_registry_speaks_the_codes_of_the_release():
    """№ 98: единицы и базис плиток — коды П§0.2 (словарь витрины), а не слова рядов («RUB bn», «points»);
    нормативы Н1.x банка — `regulatory`, сумма капитала формы 0409123 — `ras`, рынок и ставки — `market`."""
    from indicators import config, outputs

    spec = config.sources()["tiles"]
    assert outputs.tile_registry_errors(spec) == []
    items = {i["id"]: i for i in spec["items"]}
    assert len(items) == len(spec["items"])                                      # id не повторяются
    assert {i["unit"] for i in items.values()} == set(outputs.TILE_UNITS)
    assert {i["basis"] for i in items.values()} == set(outputs.TILE_BASES)
    for tile in items.values():
        series = " ".join(tile["series"])
        if ".price." in series or ".brokers.median." in series:
            assert tile["unit"] == "price", tile["id"]                           # цена и цель — ₽
        if ".index." in series:
            assert tile["unit"] == "level", tile["id"]                           # индекс — уровень
        if tile["group"] in ("market", "sector", "rates"):
            assert tile["basis"] == "market", tile["id"]
        if tile["unit"] == "bn":
            assert tile["basis"] in ("ras", "market"), tile["id"]
    assert {k for k, t in items.items() if t["basis"] == "regulatory"} == {"f135_n1_0", "f135_n1_1"}
    assert all(items[k]["unit"] == "share" for k in ("f135_n1_0", "f135_n1_1"))
    assert (items["f123_capital"]["unit"], items["f123_capital"]["basis"]) == ("bn", "ras")
    assert all(t["unit"] == "bn" for k, t in items.items() if k.startswith("ras_") and k.endswith("_m"))
    # слово ряда вместо кода, чужой базис, неизвестная группа — ошибка реестра, плитки не выпускаются
    bad = {"groups": spec["groups"], "items": [dict(items["ras_ni_m"], unit="RUB bn"),
                                               dict(items["f135_n1_0"], basis="cbr"),
                                               dict(items["imoex"], group="нет")]}
    errors = outputs.tile_registry_errors(bad)
    assert len(errors) == 3 and "RUB bn" in errors[0] and "cbr" in errors[1] and "нет" in errors[2]
    reader = outputs._Reader(store=None, today=None)
    with pytest.raises(config.ConfigError, match="реестр плиток"):
        outputs.tiles(reader, {"tiles": bad}, {"tickers": []})


def test_the_tile_codes_are_known_to_the_release_contract():
    """№ 98: коды плиток входят в словари выпуска (`model/payload.py`: UNIT_CODES, BASIS_CODES)."""
    payload = pytest.importorskip("model.payload")
    from indicators import outputs

    assert set(outputs.TILE_UNITS) <= set(payload.UNIT_CODES)
    assert set(outputs.TILE_BASES) <= set(payload.BASIS_CODES)
