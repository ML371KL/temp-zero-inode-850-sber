# -*- coding: utf-8 -*-
"""Прогон всего набора «в будущем»: `FAKE_TODAY=ГГГГ-ММ-ДД` (перенос 850oa без правок логики).

Зачем. Такт на сервере гоняет тесты такта ПЕРЕД сборкой выпуска
(`ops/run.sh`). Тест, который сравнивает литерал периода или даты с настоящим
«сегодня» (или пишет запись с моментом настенных часов, а читает её на
замороженную дату), — в такте таймер остановки: витрина замерла бы на последнем
выпуске. Утренний такт по понедельникам гоняет тесты такта с
«сегодня» + 60 дней, CI раз в месяц — весь набор с + 180. Тесты сроков данных
(метка `deadline`: объяснение сработавшего гейта действует на сегодня) в прогоны
«в будущем» не входят: срок проверяет сама сборка, о его приближении сообщает
плашка выпуска (docs/INTERFACES.md §9).

Как. Без переменной окружения модуль НЕ ДЕЙСТВУЕТ ВОВСЕ — `install()`
возвращает `None` и ничего не трогает. С ней подменяются `datetime.date` и
`datetime.datetime` в модуле `datetime`: `date.today()`, `datetime.now()`,
`datetime.utcnow()` и `datetime.today()` отвечают датой `FAKE_TODAY`; время
суток — настоящее (сдвигается только дата). Подмена ставится из
`tests/conftest.py` ДО импорта ядра и слоя индикаторов, поэтому и
`from datetime import date`, и `datetime.date` в их модулях видят подменённый
класс.

Подпроцессы (настоящая сборка `python -m model.build_release`, пробы) видят ту
же дату: `install()` добавляет в `PYTHONPATH` каталог `tests/fakedate_site`, где
лежит `sitecustomize.py`, а тот зовёт этот же модуль при старте интерпретатора.

Чего подмена НЕ трогает: `time.time()`, время файлов и часы шелла (`date` в
`ops/run.sh`). Ни ядро, ни слой индикаторов «сегодня» оттуда не берут
(docs/INTERFACES.md §7.3).

Пример (так же гоняет CI по кнопке «в будущее», `.github/workflows/ci.yml`):

    FAKE_TODAY=ГГГГ-ММ-ДД python -m pytest -m "not network and not archive and not deadline" --basetemp=var/pytest-<ключ>-<случайное>
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

ENV = "FAKE_TODAY"
SITE_DIR = Path(__file__).resolve().parent / "fakedate_site"

_REAL_DATE = getattr(_dt.date, "_real_class", _dt.date)
_REAL_DATETIME = getattr(_dt.datetime, "_real_class", _dt.datetime)


def fake_today() -> "_dt.date | None":
    """Подменённая дата или None, если подмена не включена."""
    value = os.environ.get(ENV, "").strip()
    return _REAL_DATE.fromisoformat(value) if value else None


def install() -> "_dt.date | None":
    """Ставит подмену, если задана `FAKE_TODAY`. Повторный вызов безвреден."""
    target = fake_today()
    if target is None:
        return None
    if getattr(_dt.date, "_fake_today", None) is not None:
        return _dt.date._fake_today

    class FakeDate(_REAL_DATE):
        _fake_today = target
        _real_class = _REAL_DATE

        @classmethod
        def today(cls):
            return cls(target.year, target.month, target.day)

    class FakeDateTime(_REAL_DATETIME):
        _fake_today = target
        _real_class = _REAL_DATETIME

        @classmethod
        def now(cls, tz=None):
            real = _REAL_DATETIME.now(tz)
            return cls(target.year, target.month, target.day, real.hour, real.minute,
                       real.second, real.microsecond, tzinfo=real.tzinfo)

        @classmethod
        def utcnow(cls):
            real = _REAL_DATETIME.now(_dt.timezone.utc)
            return cls(target.year, target.month, target.day, real.hour, real.minute,
                       real.second, real.microsecond)

        @classmethod
        def today(cls):
            return cls.now()

    FakeDate.__name__ = FakeDate.__qualname__ = "date"
    FakeDateTime.__name__ = FakeDateTime.__qualname__ = "datetime"
    _dt.date = FakeDate
    _dt.datetime = FakeDateTime

    # Подпроцессы: тот же «сегодня» через sitecustomize.
    site = str(SITE_DIR)
    parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if site not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([site, *parts])
    return target


def report_header() -> str | None:
    """Строка в шапке прогона pytest: видно, что прогон идёт «в будущем»."""
    target = getattr(_dt.date, "_fake_today", None)
    if target is None:
        return None
    return (f"FAKE_TODAY: «сегодня» = {target.isoformat()} "
            f"(настоящая дата {_REAL_DATE.today().isoformat()})")
