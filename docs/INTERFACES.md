# Интерфейсы: модули, функции, файлы, владение

Договор для сборки репозитория несколькими исполнителями параллельно. Что считать — `docs/MODEL.md`
(«М§N»); что лежит в выпуске — `docs/PAYLOAD.md` («П§N»); что рисует витрина — `docs/DASHBOARD.md`.
Этот документ задаёт **как**: дерево пакета, владельцев файлов, публичные функции и их сигнатуры,
форматы файлов данных и состояния, команды, переменные окружения, маркеры тестов. При расхождении
по смыслу чисел прав MODEL; по составу выпуска — PAYLOAD; по именам модулей, функций, файлов и
команд — этот документ.

---

## W4. Перед первым push (решения ведущего P1–P7 по итогам проверки волны W3)

Владение файлами — как в W3 (таблица ниже), с одним отличием: тексты методики (`docs/MODEL.md`,
`docs/PAYLOAD.md`, этот документ) в этой волне правит поток core, он же ведёт перезаякоривание
(`ops/tools/reanchor.py`, `tests/test_reanchor*.py`, `docs/REANCHOR.md`); книгу и факты — один поток.

| Решение | Что стало правилом | Где |
|---|---|---|
| P1 | в данных и документах нет выписок ответов брокерского API и имён полей ответа; дивиденды — публичные факты: решения собраний и раскрытие (документ или новость с датой), сверка с брокерским календарём — словами (`dividends.json → broker_check`); лист β читает таблицу решений о дивидендах (год, DPS, отсечка) с публичным источником. Имена полей ответа живут только в коде сборщиков и в синтетических фикстурах формата ответа | §0 п. 5, §3.1, §3.2; М§15.1, М прил. B |
| P2 | фикстуры новостей — настоящая синтетика: выдуманные формулировки в формате источника (те же маркеры разметки и схема чисел), не сокращение и не пересказ настоящих статей | §9, §11 |
| P3 | ни один тест не пишет коммит или файл в объемлющий репозиторий: git в тестах — только через помощник песочницы (`tests/support_git.py`: `GIT_CEILING_DIRECTORIES`, явный `-C`, проверка каталога репозитория до записи), каталог временных файлов прогона занят одним прогоном | §0 п. 8, §9 |
| P4 | точность диагностик: уточнение корня обратного расчёта до невязки в стопе (`median_refine` 2), «в диапазоне книги» — о корне; нейтральные значения — оценщиком срединных прогонов, рядом — ожидание квартала срединных прогонов; нейтральная точки не «прилипает» к строке таблицы; оси долей σ0 и φ — в области гейта `lt_spread_floor`, гейт проверяется и в углах этих осей; передача по клеткам цели не равна — объяснено в методике; дисконт 0,004 в примерах и фикстурах | §4.4–§4.5; М§4.5, §8.2, §11.2, §13, §14.2; П§2 |
| P5 | обучение режимов на смене года якоря: сравнены три правила; замороженные μ остаются, цена и ограничения — числами; сухой прогон в квартале шока печатает и строку отчёта без шока | §6; М§12, §15.2; `docs/REANCHOR.md` §1.1, §3.7 |
| P6 | тест такта не привязан к горизонту календаря фактов; приёмка первого push печатает итог каждой команды; потолок шага CI «тесты такта» — 300 с, цель сервера 180 с сверяется замером | §6, §9; М§18 |
| P7 | тексты выпуска и экрана: источник истории дивидендов — словами для владельца, без имён методов API и рабочих пометок; дробные числа склоняются как «года»; подписи счётчиков согласованы с числом; единицы строк сверки с контрольной моделью — по виду допуска | П§0.2, DASHBOARD |

**Новое и изменённое в ядре (только добавлением):** `model.checks.share_axis_ends`, `SHARE_KEYS`
(гейт `lt_spread_floor` — и в углах осей долей; `Finding.detail["axis_ends"]`);
`model.reverse.root_in_range`, поле строки `converged`; `model.uncertainty.middle_draws`,
`draw_book`; `model.nextreport.middle_expectation`; поле выпуска
`next_report.neutral.<x>.middle_expectation` (необязательное); новый смысл `reverse_dcf.rows[].in_range`
и `next_report.neutral.<x>.gap_rub`. Инструмент перезаякоривания: `expected_observation(…, without=)`,
`shock_regimes`, ключ результата `no_shock` и абзац «Квартал шока режима …» в `REANCHOR.md` кандидата.
Ключей книги не добавлено; значение `valuation.uncertainty.median_refine` — 2.

---

## W3. Кто что меняет (волна по итогам второго аудита, книга 1.3; 01.10.2026)

Основание — аудит после волны W2 (записи № 69–106 и остатки прежних) и решения ведущего
LEAD-DECISIONS-4 (В12–В20); тексты MODEL, PAYLOAD и этого документа уже приведены к ним. Правила
§0 действуют; чужой файл — запросом в отчёте. Волны W1 и W2 — в `docs/CHANGELOG.md`; их ключи и
поля живут в М прил. A и B и в П§2.

| Поток W3 | Пишет только он |
|---|---|
| **core** | весь `model/`; `tests/test_core_*.py`, `tests/test_core2_*.py`, `tests/support_core*.py`, `tests/mutations.py`, `tests/test_integrate_regression.py`, `tests/fixtures/core*/**` |
| **book** | `data/assumptions/**` (кроме `results.json`, `run_output.txt` — выпускает ядро); `tests/test_book_*.py`, `tests/support_book.py`, `tests/fixtures/book/**` |
| **facts** | `data/facts/**`, `ops/tools/build_facts.py`, `docs/FACTS.md`; `tests/test_facts_*.py`, `tests/fixtures/facts/**` |
| **indicators** | `indicators/**`, `data/indicators/**`, `docs/INDICATORS.md`; `tests/test_ind_*.py`, `tests/support_ind.py`, `tests/fixtures/ind/**` |
| **web** | `web/**`, `functions/**`, `docs/DASHBOARD.md`, `wrangler.toml`, `ops/tools/devserver.py`; `tests/test_web_*.py`, `tests/web/**`, `tests/fixtures/payload-sample.json` |
| **ops** | `ops/**` (кроме `build_facts.py`, `devserver.py`, `reanchor.py`), `.github/**`, `pytest.ini`, `tests/conftest.py`, `tests/__init__.py`, `tests/fakedate_*`, `requirements.txt`, `README.md`, `docs/MANUAL.md`, `docs/DECISIONS.md`, `docs/CHANGELOG.md`, `docs/PORTING.md`; `tests/test_ops_*.py`, `tests/test_public_hygiene.py` |
| **control** | `tests/independent_model.py`, `tests/test_control_*.py`, `docs/CONTROL-MODEL.md`, `data/checks/**` |
| **reanchor** | `ops/tools/reanchor.py`, `tests/test_reanchor*.py`, `docs/REANCHOR.md` |
| ведущий | `docs/MODEL.md`, `docs/PAYLOAD.md`, `docs/INTERFACES.md`; коммиты, `results.json` и выпуск после правок; первый push — одним свежим корневым коммитом, выкладка на сервер |

**Новые ключи книги** (М прил. A; значения — book, схема и чтение — core, годовая сверка — control):

| Ключ | Что | М |
|---|---|---|
| `nii.phi_split` | доля сжатия φ на спредах кредитных книг с `phi`, остаток — добавка к стоимости пассивов с `phi`; центр — правило центров: гейт `lt_spread_floor` проходит во всех мирах и выполнен тест знака объёмных эффектов | §4.4–§4.5 |
| `nii.books.<b>.phi` у пассивов | флаг теперь есть у книг обеих сторон; у обеих книг средств ФЛ — одинаков | §4.4–§4.5 |
| `joint.regime_update.floor_share` | пол вероятности режима: доля базовой вероятности, после каждого наблюдения (книга 1.3 — 0,33) | §12 |
| `valuation.sensitivities.rank_window` | окно рангов оценщика срединных прогонов (книга: 0,40 и 0,60) | §8.3, §10 |
| `valuation.next_report.value_tol` | допуск нейтрального значения по самому значению (книга: 0,0001) | §13 |
| `valuation.uncertainty.axes[]`: поле `unit` | необязательная единица значения оси для печати | §10 |

Значения, меняющиеся без нового ключа (book): дисконт за управление — 0,004 по неподвижной точке
на медиане своей версии (В16); полы `checks.lt_spread_floor.<b>` подтверждаются листом истории
спредов (В20); центр `nii.sigma0_split` — по правилу центров; объяснения гейтов, тексты допущений
об угасании, опоре FVC, субординированных и ограничения книги (пол вероятности числами) — по
записям аудита. Формулы, меняющиеся без ключа (core, control): одностороннее угасание (М§7),
опора FVC — путь нормы книги до розыгрыша (М§4.6), порог ликвидности терминала (М§7), год с
отчётными кварталами в упр. базисе (М§4.6), квартал выплаты объявленного дивиденда по записи
реестра (М§5.4), сторож заголовка без записи реестра (М§14.4).

**Новые и изменённые поля фактов** (facts пишет; М прил. B, §3.2 ниже): `mgmt_quarterly.json →
annual` читает сборка выпуска; история моста `bridge_mgmt_ifrs.json → <x>.history` обязана нести
все отчётные кварталы года якоря; печатаемые записки календаря и `scope_note` гайденса — словами
и с годом в датах (П§0.2).

**Новые и изменённые поля выпуска** (core пишет, web читает; П§0.2, §2–§3, §7):
`fair_value.jump_guard.{unregistered_dividend, unregistered_dps}` (и новый смысл `v0_change`),
`regimes.update.floor_share`, `paths.annual[].{fvc, one_off}` и тождество строк года,
`nii.transmission.{phi_assets, phi_liab, phi_split, loan_margin}`, `nii.books[].phi` у пассивов,
`calendar.next_ras.form102_latest_est` и окно у события `form102`, `history.gaps[].basis` и
годовые упр. метрики из годового узла фактов, новый смысл `nowcast.year.{ni_quarter, ni_year_se,
dps_interval}`, `guidance.items[].model_year` и упр. метрик года якоря, `live.degraded_flag`;
единицы плиток `indicators.tiles[].unit` — коды П§0.2, базис `market`, нормативы банка —
`regulatory`; флаги `explanation_expiring`, `policy_expired`; чувствительности и местный наклон —
оценщиком срединных прогонов. Выводимое: `derived.nii.phi_assets`, `derived.nii.phi_liab`,
`derived.nii.loan_margin`; `central_cell` — по правилу (§4.6).

**Что меняется по потокам** (главное; полный перечень — план аудита и отчёты потоков):

| Поток | Главное |
|---|---|
| core | раскладка сжатия φ, кредитная маржа миров и тест знака (М§4.4–§4.5); одностороннее угасание, порог ликвидности терминала (М§7); опора FVC до розыгрыша (М§4.6; `Book.base`, §4.1); пол вероятности режима (М§12); год с отчётными кварталами — одна функция моста для CoR, ЧПМ и CIR (М§4.6); нейтральное значение по двум допускам (М§13); оценщик срединных прогонов (М§10); нау-каст года на одном слое и ошибка оставшихся кварталов (П§2); строка FVC и разовые статьи в пути по годам; история — годовые узлы и провалы по базисам; сторож заголовка без записи реестра (М§14.4); флаги `explanation_expiring`, `policy_expired` вместо тревоги и отказа; `live.degraded_flag` по политике тревог (§6); единица оси из поля `unit`; validate единиц и базиса плиток; `central_cell` по правилу; окно у события формы ЦБ; тексты выпуска словами (П§0.2); `REQUIRED_FIELDS` по П§2; мутации и закрытые формулы (М§18) |
| book | книга 1.3: ключи таблицы выше, флаг `phi` у средств клиентов, центры обеих долей с листами, дисконт 0,004, лист истории спредов; объяснения гейтов без служебного текста; источники осей словами, даты с годом, единицы осей; ограничения книги (пол вероятности числами); лист дивидендов с полями ответа T-Invest — убрать из дерева; `tests/support_book.py` — `phi` у книг обеих сторон; тест «объяснение сработавшего гейта действует на сегодня» — метка `deadline` |
| facts | записки календаря и даты словами; листы отчёта следующего квартала и пересчёт калибровки ЧПД — в день отчёта; новая дивидендная политика входит новым фактом, до неё срок политики — флаг выпуска `policy_expired`: тест «политика действует на сегодня» снимается |
| indicators | тест такта без привязки к настенным часам; перехват зависания распознавания PDF; месяц январского релиза; единицы и базис плиток; верхний край окна формы ЦБ; `collector.alarm` для сборки (§5, §6) |
| web | бюджет витрины 320 КБ (§10); печать по П§2: строка FVC, провалы истории сегментами, `null` в интервале нау-каста, целые рубли чувствительностей, диапазоны осей-словарей, типографика; `docs/DASHBOARD.md`; фикстура на кодах П§0.2 |
| ops | тексты приёмки первого push и установки; набор сервера без тестов сроков, метка `deadline` (§9); код 8 — только по тревогам политики §6; сверка личных слов без тихого пропуска; проба источников берёт адрес PDF из затравки; шаг создания проекта Pages; решения В17–В20 в MANUAL и DECISIONS |
| control | годовая модель по М§4.4–§4.6, §5.4, §7, §12, §17; центральная клетка по правилу; сверка после перевыпуска |
| reanchor | режим `keep` с четырьмя скалярами пути (σ0_A, σ0_L, φ_A, φ_L) и пересчётом обеих долей (М§15.2 п. 8); замороженные μ не пересчитываются при смене года якоря; запись внутри репозитория — только под `var/` (§6) |

---

## 0. Правила совместной работы

1. **Пишешь только свои файлы** (§2). Нужна правка чужого файла — запрос в отчёте потока (что, где,
   зачем); владелец или интеграция вносит её. Чужой файл не правится «на минуту».
2. **Публичные сигнатуры §4–§7 — договор.** Внутренние функции модуля владелец называет как хочет.
   Новая публичная функция или поле — только добавлением (старое не ломается); изменение — через
   отчёт и интеграцию.
3. **Пока соседнего файла нет** — работать против этого договора: заглушка с той же сигнатурой в
   своих фикстурах (`tests/fixtures/<поток>/`), не в чужом каталоге. Пример: ядро части 2 до
   `indicators/outputs.py` читает выходы индикаторов из `tests/fixtures/core2/`.
4. **Литералы.** В `model/`, `indicators/`, `web/`, `functions/` нет чисел книги и фактов, годов, дат,
   имени, тикеров и регномера эмитента (тесты, М§0.6, М§18; имя банка в `web/` — только статическим
   текстом `index.html` и `404.html`, DASHBOARD §2). Имя, тикеры и регномер — из
   `meta.company` книги; числа — из книги и фактов; строки поиска новостей и адреса документов
   эмитента — из `data/indicators/sources.yaml`. Технические константы (0,25 года, 365 дней,
   допуски 1e-9, число шагов поиска) — можно. Имена переменных окружения в `model/` и
   `indicators/` — без префикса банка (`BANK_*`, §8): копия ядра под другой банк меняет только
   книгу и факты (М§0.6).
5. **Публичный репозиторий.** Нет IP серверов, почт (кроме noreply-адреса владельца в списке
   разрешённых), токенов, ID аккаунта Cloudflare, абсолютных путей пользователя, имён приватных
   служебных репозиториев, хэшей личных слов (в том числе в тестах гигиены), сырых ответов
   T-Invest (фикстуры — синтетика в формате ответа, §11), выписок ответов брокерского API и имён
   полей ответа в данных и документах (`data/**`, `docs/**`, любой `*.md`): источник дивидендов —
   решение собрания и раскрытие, сверка с брокерским календарём называется словами; имена полей
   ответа — только в коде сборщиков. Секреты — только именами переменных окружения (§8). Токен
   T-Invest в файлы, журналы и выпуски не пишется никогда.
6. **Базисы не смешиваются**: МСФО группы (`ifrs`), РСБУ банка (`ras`), управленческие метрики
   (`mgmt`), базис движка (`engine`, М§4.6). Перевод упр. ↔ движок — только функции моста
   (`model/credit.py`, §4.2). Нераскрытое — `null`, не 0.
7. **Язык и зависимости.** Python 3.12, stdlib + PyYAML. `pdfplumber` и `pypdfium2` — только в
   инструментах разбора PDF (`indicators/sber_pdf.py`, скрипты `data/assumptions/evidence/`),
   импорт ленивый. Витрина — ванильный JS без сборки и библиотек. Комментарии и докстринги —
   по-русски, кратко. Файлы — UTF-8, LF (bash-скрипты — только LF).
8. **Тесты не ходят в сеть** (кроме маркера `network`), не пишут в рабочее дерево (только
   `tmp_path` и `var/`), не читают папку передачи (кроме маркера `archive`) и **не зависят от
   «сегодня»**: дату тест получает параметром `today` или от плагина `FAKE_TODAY`, момент записи и
   дата чтения берутся из одного источника (§9). **Git в тестах — только в песочнице:** пишущая
   команда идёт через помощник `tests/support_git.py` (явный `-C <каталог>`, потолок
   `GIT_CEILING_DIRECTORIES`, каталог репозитория сверяется до записи — при несовпадении тест падает,
   ничего не записав); прямой вызов `git` разрешён только читающим командам над самой рабочей
   копией. Прогон потока — `pytest --basetemp=var/pytest-<ключ потока>-<случайное> <свои тесты>`:
   каталог временных файлов занят одним прогоном, второй прогон с тем же каталогом отказывает при
   старте (два прогона с одним каталогом уже клали посторонний коммит в рабочую копию).

---

## 1. Дерево репозитория

```
model/                       ядро (Python): книга, клетка, сетка, полоса, диагностики, выпуск
  __init__.py
  book.py                    загрузка книги и фактов → Book, Facts; BookError, FactsError; anchor-ключи
  book_schema.py             закрытая схема книги (прил. A М), выключатели, проверки §0.4 и §14.1 book_schema
  timeline.py                кварталы сетки, дни, год/номер квартала, дата оценки: q0, e, τ_q, перекат Δ
  paths.py                   траектории книги (М§0.4): значение в квартале, сдвиг, точечные пути
  worlds.py                  пути мира по кварталам (ставки, ИПЦ, зарплаты, ОФЗ, реальная ключевая,
                             надстройка worlds_bank со сходом к g_T); бескупонная кривая, DF_v, k_q, k_T
  nii.py                     10 книг ЧПД: ставки с лагом, доля текущих счетов, ЧПД, взносы АСВ;
                             стационарный ЧПМ, σ0 и φ по реализованной передаче (М§4.4–§4.5)
  credit.py                  мост упр. ↔ движок (Bridge), CoR режима → движок, κ-добавка, резервы (М§4.6)
  capital.py                 RWA и дрейф плотности, K20/K11 с аддитивными вычетами, E_q по календарю
                             аудитов, полы и требования по сценариям (М§3.3, §4.11–§4.12)
  dividends.py               политика данными книги: база, пул, DPS (ceil_kopeck в Decimal), пробный
                             проход, запас H, ступени, догоняющий, ε, календарь, реестр (М§5)
  cell.py                    квартальный проход клетки в порядке М§4.2 → CellResult
  valuation.py               RI (печать) и DDM (инвариант), капитал на дату оценки, терминал (М§6–§7)
  grid.py                    36 клеток, вероятности, A-P2u, слои, мост и цена, точка и λ, язык банка (М§3, §8–§9, §12)
  checks.py                  инварианты М§14.1, гейты М§14.2, флаги М§14.3; объяснения гейтов
  uncertainty.py             полоса A-V9: LHS, треугольные оси, пул процессов, вклады осей, печать, MedianAnchor (М§10)
  reverse.py                 обратный расчёт медианы и банковские строки (М§11)
  nextreport.py              открытый квартал, ожидание модели, таблицы CoR и ЧПМ, нейтральные значения (М§13)
  attribution.py             разложение изменения между выпусками (М§16)
  live.py                    живые входы: цены категорий, дата оценки, реестр; коридоры годности (М§15)
  payload.py                 сборка выпуска sber-v1 и validate (PAYLOAD); константы контракта
  book_results.py            results.json и run_output.txt книги (регрессия, М§18)
  build_release.py           CLI сборки выпуска в $BANK_STATE_DIR/release/
indicators/                  сборщики, хранилище, журнал, нау-каст (Python)
  __init__.py
  http.py                    HTTP: таблица User-Agent по хостам, без прокси окружения, повторы, закреплённый корень только для T-Invest
  tls/                       PEM закреплённого корня (публичный сертификат; не секрет)
  store.py                   сырой архив (неизменяем, sha256, gzip) и ряды с винтажами «что было известно на дату»
  sources.py                 реестр сборщиков: COLLECTORS, DAILY, RELEASE_WATCH, CRITICAL, IRRECOVERABLE
  periods.py                 периоды месяцев и кварталов, календарные даты (свой модуль: ядро не импортируется)
  config.py                  meta.company книги (PyYAML, без импорта model) и data/indicators/sources.yaml
  iss.py                     MOEX ISS: цены (запасные), кривая zcyc, индекс
  cbr.py                     ЦБ: ключевая, RUONIA, ставки вкладов, ликвидность (SOAP)
  cbr_forms.py               CreditOrgInfo по регномеру: даты и формы 101/102/123/135, винтажи, разбор
  tinvest.py                 T-Invest (токен из окружения): цены, дивиденды, календарь отчётов, цели брокеров
  news.py                    новостные ленты: тексты и ссылки на документы эмитента
  release_watch.py           дозор релизов РСБУ/МСФО: новость + статический PDF, приём при двух источниках
  sber_pdf.py                скачивание статических PDF (sha256), разбор с текстовым слоем, распознавание или needs_manual
  record.py                  ручной ввод: record-ras, record-actual, record-consensus, record-guidance
  journal.py                 журнал прогнозов (SQLite, записи неизменяемы, эталоны замораживаются), правило допуска
  nowcast.py                 нау-каст квартала: оценка по РСБУ × сезонный мост, вес w, прогноз (DESIGN §5)
  calendar.py                календарь событий для индикаторов и выпуска (из facts/calendar.json + расписание форм)
  retro.py                   ретро-проверка эталонов на истории
  seed.py                    история рядов из листов этапа 1 → data/indicators/seed/ (`--check`; $BANK_HANDOFF_DIR)
  outputs.py                 ВЫХОДЫ ДЛЯ ВЫПУСКА: release_inputs() → IndicatorOutputs, реэкспорт combine (§5)
  collect.py                 CLI такта индикаторов
data/
  assumptions/               книга: шаблон, сборщик, машинная книга, миры, надстройка, объяснения гейтов,
                             записки выпуска, results.json, evidence/<область>/ (§3.1)
  facts/                     факты (узлы {v, src|calc}), SCHEMA.md, calendar.json, peers.json (§3.2)
  indicators/                sources.yaml (строки эмитента для сборщиков), seed/ (история рядов с источниками)
  checks/                    control_model.json — сводка сверки с контрольной моделью (для выпуска)
ops/                         эксплуатация
  run.sh                     такты collect | release-watch | daily | rebuild | rollback <sha> ["причина"]
  tact.py                    решения такта для run.sh: window | news | done | simulate (отложенное дозора)
  budgets.json               время шагов тактов → потолки юнитов (RuntimeMaxSec) и ожидание замка
  publish.py                 публикация выпуска в репозиторий данных (append-only) и сверка через /api/model
  env.example                образец env-файла сервера (только имена переменных)
  README.md                  установка, расписание, журнал такта, отказы и тревоги, откат
  systemd/                   sber850-{collect,release-watch,daily,rebuild}.{service,timer}
  tools/
    build_facts.py           факты из листов этапа 1 (поток facts)
    refresh_worlds.py        пересборка миров семейства (--check)
    render_numbers.py        числа results.json в документах по меткам (--check)
    walk_book.py             прогулка книги между версиями (М§16, §18)
    reanchor.py              перезаякоривание книги на отчёт квартала, ожидаемый отчёт и сухой прогон
                             (поток reanchor; М§15.2, docs/REANCHOR.md)
    probe.py                 проба источников с сервера (таблица)
    devserver.py             локальный предпросмотр витрины с боевой CSP (поток web)
web/                         витрина: index.html, 404.html, app.js, styles.css, favicon.svg, _headers, _routes.json
functions/                   Pages Functions: _middleware.js (CSP, граница /api/), api/model.js (дверь данных)
tests/                       тесты потоков (§9), фикстуры tests/fixtures/<поток>/, Node-проверки tests/web/
docs/                        MODEL, PAYLOAD, DASHBOARD, INTERFACES, FACTS, INDICATORS, CONTROL-MODEL,
                             REANCHOR, MANUAL, DECISIONS, CHANGELOG, PORTING
.github/                     workflows/ci.yml, workflows/docs.yml, commit-emails.allow
pytest.ini, requirements.txt, README.md, wrangler.toml, .gitignore, .gitattributes
var/                         локальное состояние, временные файлы тестов, кандидаты перезаякоривания
                             var/reanchor/ (в git — только .gitkeep)
```

---

## 2. Владение файлами по потокам

| Поток | Файлы (пишет только он) |
|---|---|
| **book** | `data/assumptions/**` (кроме `results.json` и `run_output.txt` — их выпускает `python -B -m model.book_results`, коммитит интеграция); `tests/test_book_*.py`, `tests/fixtures/book/**` |
| **facts** | `data/facts/**` (включая `calendar.json`, `peers.json`, `SCHEMA.md`); `ops/tools/build_facts.py`; `docs/FACTS.md`; `tests/test_facts_*.py`, `tests/fixtures/facts/**`. Отдельного `data/calendar.json` нет: календарь — `data/facts/calendar.json` (М прил. B) |
| **core1** | `model/__init__.py`, `model/book.py`, `book_schema.py`, `timeline.py`, `paths.py`, `worlds.py`, `nii.py`, `credit.py`, `capital.py`, `dividends.py`, `cell.py`, `valuation.py`, `grid.py`, `checks.py`; `tests/test_core_*.py`, `tests/support_core.py`, `tests/fixtures/core/**`; позже — `tests/mutations.py` |
| **core2** | `model/uncertainty.py`, `reverse.py`, `nextreport.py`, `attribution.py`, `live.py`, `payload.py`, `book_results.py`, `build_release.py`; `tests/test_core2_*.py`, `tests/support_core2.py`, `tests/fixtures/core2/**`; `tests/test_integrate_regression.py` (регрессия книги М§18) |
| **control** | `tests/independent_model.py`, `tests/test_control_*.py`, `docs/CONTROL-MODEL.md`, `data/checks/**` |
| **indicators** | `indicators/**`, `data/indicators/**`, `docs/INDICATORS.md`, `tests/test_ind_*.py`, `tests/support_ind.py`, `tests/fixtures/ind/**` |
| **web** | `web/**`, `functions/**`, `wrangler.toml`, `ops/tools/devserver.py`, `docs/DASHBOARD.md` (после шага правки спецификации), `tests/test_web_*.py`, `tests/web/**`, `tests/fixtures/payload-sample.json` |
| **reanchor** | `ops/tools/reanchor.py`, `tests/test_reanchor*.py`, `docs/REANCHOR.md` |
| **ops** | `ops/**` (кроме `ops/tools/build_facts.py`, `ops/tools/devserver.py` и `ops/tools/reanchor.py`), `.github/**`, `pytest.ini`, `tests/__init__.py`, `tests/conftest.py`, `tests/fakedate_plugin.py`, `tests/fakedate_site/**`, `requirements.txt`, `README.md`, `.gitignore`, `.gitattributes`, `docs/MANUAL.md`, `docs/DECISIONS.md`, `docs/CHANGELOG.md`, `docs/PORTING.md`, `tests/test_ops_*.py`, `tests/test_public_hygiene.py` |
| ведущий | `docs/MODEL.md`, `docs/PAYLOAD.md`, `docs/INTERFACES.md` — правки через отчёты потоков |

Префиксы тестов не пересекаются: `test_core_` и `test_core2_` различаются знаком после `core`.
Помощники тестов — `tests/support_<поток>.py` (pytest их не собирает как тесты). С волны W1
core1 и core2 — один поток **core** (весь `model/`, раздел W3).

---

## 3. Файлы данных

### 3.1. Книга (`data/assumptions/`, поток book)

| Файл | Что | Кто читает |
|---|---|---|
| `assumptions_template.yaml` | шаблон книги: все ключи прил. A М, кроме `worlds.<W>.*` и `worlds_bank.*`; основа — `assumptions.draft.yaml` | `build_assumptions.py` |
| `assumptions.draft.yaml` | черновик книги 1.0 (схема и числа решений 01.10.2026) | book (как основа шаблона) |
| `build_assumptions.py` | `python data/assumptions/build_assumptions.py [--check]` → `assumptions.yaml` и `assumptions.json`: шаблон + `worlds.<W>.*` из `worlds_source.json` + `worlds_bank.<W>.*` из `worlds_bank.json` + хэши; `--check` — сборка воспроизводит файлы (выход 1 при расхождении). Публичная функция `build(template: Path \| None = None) -> dict` | ops (`--check` в CI) |
| `assumptions.yaml` / `.json` | машинная книга — **единственное, что читает ядро** (`model/book.py`) | model, control |
| `worlds_source.json` (+ `.csv`), `worlds_inputs.yaml`, `worlds_recipe.py`, `WORLDS-RECIPE.md` | байтовая копия записи миров семейства и рецепт; `worlds_recipe.py --check` | book, ops |
| `worlds_bank.json` | банковская надстройка миров (формат — М прил. B) | `build_assumptions.py` |
| `gate_explanations.yaml` | объяснения гейтов: `<имя гейта>: {explanation, expected_mass \| expected_mass_range: [lo, hi], valid_until}` (М§14.2) | `model/checks.py` |
| `release_notes.yaml` | записки к скачку заголовка: `[{valid_until, expected_central, tolerance_pct, note}]` (М§14.4) | `model/payload.py` |
| `results.json`, `run_output.txt` | результаты книги (регрессия) — выпускает `model/book_results.py` | тесты, `render_numbers.py`, ASSUMPTIONS-BOOK |
| `ASSUMPTIONS-BOOK.md`, `README.md`, `V1.x-CHANGES.md` | текст книги и журнал версий | люди |
| `evidence/<область>/` | воспроизводимые расчёты книги (относительные пути, малые входы в `inputs/`, первичка — ссылкой и sha256); среди них листы правил: центры `nii.sigma0_split` и `nii.phi_split`, история спредов кредитных книг к опоре (полы гейта `lt_spread_floor`), неподвижная точка дисконта за управление. Ответов брокерского API, выписок из них и имён их полей во входах нет: лист β читает таблицу решений о дивидендах (год, DPS, отсечка) с публичным источником | тесты книги |

**Формат `worlds.<W>` машинной книги** (пишет `build_assumptions.py`, М§3.1): `name` (строка),
`key_rate`, `cpi`, `wage_growth`, `ofz_1y`, `ofz_3y`, `ofz_5y`, `ofz_10y`, `real_key` — словари
полугодий `{"2026H2": доля, …, "<год last_period>H2": доля}`; `zero_curve` — `{"1": доля, "3": …,
"5": …, "10": …, LT: доля}`; `lt_inflation` — доля. `worlds_bank.<W>` — как в файле надстройки
(`credit_growth.{corporate, mortgage, retail_other}`, `funds_growth.{retail, corporate}`,
`nominal_gdp_growth` — словари лет с долями).

### 3.2. Факты (`data/facts/`, поток facts)

Узел — объект с числом и происхождением; голых чисел нет (кроме годов и дат):

```json
{"v": 8387.2, "src": "2026H1_ifrs_interim_statements.pdf, с. 5, sha256 5b3a3de8…"}
{"v": 6147.0, "calc": "loans_ac_gross.mortgage − mortgage_subsidized; доля льготных — calib-nii (накопление поколений)"}
{"v": null, "calc": "не раскрыто: резервы только по кредитам АС поквартально"}
```

В `src` — документ, страница (или лист!ячейка) и sha256 документа; в `calc` — формула из других
узлов или причина `null`. Каждый файл несёт `basis` (`IFRS` | `RAS` | `mgmt`) и `as_of`.
Состав файлов и полей — М прил. B: `anchor.json`, `balance.json`, `nii_books.json`,
`pnl_quarterly.json` (с `llp_debt_fa`), `capital.json`, `shares.json`, `dividends.json`,
`bridge_mgmt_ifrs.json`, `mgmt_quarterly.json`, `guidance.json`, `calendar.json`, `peers.json`; плюс
`bridge_ras_ifrs.json` для нау-каста (читает `indicators/nowcast.py`, не ядро):

```
{basis: "RAS→IFRS", as_of, unit, definition,
 quarters: [{period, ras_ni, ifrs_ni_sh, ratio, src}],          # ЧП банка РСБУ → ЧП акционерам МСФО (T1)
 by_quarter: {"1": {v, calc}, "2": …, "3": …, "4": …}, annual: …,
 nii: {definition, window: [с, по], quarters: [{period, ras, ifrs, ratio, src}],
       by_quarter: {"1": {v, calc}, …}, value: {v, calc}},       # ЧПД РСБУ → МСФО (T3; ориентир 1,14–1,17)
 iea: {то же},                                                   # средние процентные активы: форма 0409101 → IEA группы
 cor: {то же, РСБУ → упр.} | {v: null, calc: "нет данных: T4 = ожидание"}}
```

Поля прочих файлов, на которые опирается договор: `bridge_mgmt_ifrs.json → cir` {`method`, `value`,
`history` [{`period`, `mgmt`, `engine`, `gap`}]} — мост CIR (окно то же, М§4.6); `guidance.json →
items.<k>`: `kind` ∈ {`point`, `min`, `max`, `range`} (у `n20_0` — `min`), `tol` (у `point` — половина
единицы последнего разряда числа из `text`), `scope` ∈ {`group`, `sector`}, `relation` ∈ {`in_line`,
`above`} (только у `sector`), `scope_note`; `dividends.json → policy.valid_until` — дата утверждения
плюс срок политики (считается), `valid_until_note`; `pnl_quarterly.json → <период>.key_avg` — средняя
ключевая квартала (`null` можно); `balance.json → history` [{`period`, `bv_common`,
`loans_ac_gross`, `iea`}] — концы кварталов до якоря (М прил. B); `bridge_mgmt_ifrs.json →
<x>.history` — все отчётные кварталы года якоря (их упр. факт входит в год гайденса, М§4.6);
`mgmt_quarterly.json → annual.<год>.{nim, cor, cir, roe}` — раскрытые годовые метрики (их читает
сборка выпуска: П§2 `history.annual`); `nii_books.json → rates_carry_residual` — флаг правила
ставки якоря (после перезаякоривания ставки книг активов несут и процентный доход вне книг,
М§15.2 п. 4); `pnl_quarterly.json → <период>.at1_coupon` — купон AT1 величиной (Σ кварталов —
год); `dividends.json → policy.valid_until_src` — страница документа с пунктом о сроке;
`dividends.json → history[]` — решения собраний: `decided_date` у каждого года, `sources` видов
`manual` (документ эмитента) и `news` (новость о решении), `pay_date` — срок выплаты номинальным
держателям по правилу от отсечки; `dates_rule` — правило дат словами, `broker_check` — итог сверки
дивиденда и отсечки с брокерским календарём словами (выписок ответа и имён его полей нет). Схема —
`data/facts/SCHEMA.md` (там же — перечень мест, привязанных к якорю), происхождение —
`docs/FACTS.md`. Сборщик: `python ops/tools/build_facts.py [--check] [--anchor <период>] [--out
<каталог>] [--report <файл>] [--expected <файл> [--facts <каталог>]] [--sheet <файл>]…
[--sheet-template <файл>]` читает листы этапа 1 из `$BANK_HANDOFF_DIR` (`--sheet` — черновик листа
квартала вне папки передачи, только с `--out` или `--check`; `--sheet-template` пишет шаблон листа
квартала, следующего за якорем, — `data/facts/sheets/<период>.csv`) и пишет JSON (в CI не запускается: JSON закоммичены); якорь — из аргумента (по
умолчанию — `anchor.json`), литералов квартала в сборщике нет — им пользуется перезаякоривание
(М§15.2): `--report` пишет файл отчёта квартала якоря (вход `reanchor.py --facts`), `--expected`
собирает факты нового якоря из прежних фактов и ожидаемого отчёта (нужен `--out`; папка передачи
не нужна; `anchor.json` такого каталога несёт блок `expected` — в канон он не переносится).

**`calendar.json`**: `{"as_of": "YYYY-MM-DD", "events": [{"id", "kind", "date", "title", "covers",
"confirmed", "precision", "earliest"?, "latest"?, "src", "note"?}]}` — `kind` ∈ {ifrs, ras,
cbr_rate, cbr_forecast, agm, record, investor_day}; `covers` — период отчёта (`2026Q3`, `2026M09`)
или `null`; `precision` — `day` | `window` (у окна — `earliest`, `latest`).

**`peers.json`**: `{"as_of", "basis", "banks": [{"ticker", "name", "bv", "bv_date", "ni_ltm",
"ni_ltm_period", "shares_outstanding", "dps_ltm", "capital_ratio": {"name", "value", "as_of"},
"basis", "src"}]}` — числа узлами; цен нет (живой вход).

### 3.3. Прочие данные

* `data/indicators/sources.yaml` (indicators): строки поиска новостей, шаблоны адресов статических
  PDF эмитента (только `https`) и разрешённые хосты документов (`sber_pdf.hosts`), список лент и
  каналов, расписание релизов по умолчанию — всё, что называет эмитента, живёт здесь, а не в коде.
* `data/indicators/seed/*.json` (indicators): история рядов для ретро и нау-каста (релизы РСБУ
  по месяцам, консенсус), каждое значение с источником; строится из листов этапа 1
  (`python -m indicators.seed [--check]`). Адрес PDF последнего релиза из затравки
  (`ras_release.json`) берёт проба источников на свежем сервере (§6).
* `data/checks/control_model.json` (control): `{as_of, commit, rows: [{what, unit, core, control,
  diff, diff_rel, tol, tol_kind, tol_abs?, ok}]}` — сводка сверки с годовой контрольной моделью
  (П§2 `checks.control_model`): `diff` = control − core в единицах строки, `diff_rel` = control /
  core − 1, `tol_kind` — `rel` | `abs` (с чем сравнивается `tol`), `tol_abs` — абсолютный пол у строк
  с правилом «или» (М§17); писатель сохраняет вид допуска, ядро передаёт файл в выпуск как есть.

---

## 4. Ядро: публичные типы и функции

Импорт внутри пакета — абсолютный (`from model.book import load_book`). Все числа — `float`
(Decimal — только в формуле DPS истории). Даты — `datetime.date`, периоды — строки `2026Q3`.

### 4.1. Книга и факты (`model/book.py`, `model/book_schema.py`) — core1

```python
class BookError(ValueError): ...           # книга нарушает схему или правило М (громкий отказ)
class FactsError(ValueError): ...          # нет файла, узла или числа факта, который нужен

BOOK_PATH: Path      # data/assumptions/assumptions.yaml
FACTS_DIR: Path      # data/facts

@dataclass(frozen=True)
class Facts:
    root: Path
    files: Mapping[str, dict]              # имя файла без .json → содержимое как есть
    digest: str                            # sha256 канонического JSON всех файлов (§7.4)
    def node(self, file: str, path: str) -> dict            # узел {"v", "src"|"calc"}; нет узла — FactsError
    def v(self, file: str, path: str) -> float | None       # значение узла; null → None
    def need(self, file: str, path: str) -> float           # значение; null или нет — FactsError

def load_facts(root: Path | None = None) -> Facts

@dataclass(frozen=True)
class Book:
    data: Mapping[str, Any]                # машинная книга после проверки; "anchor" заменены числами
    anchors: Mapping[str, float]           # точечный путь anchor-ключа → выведенное значение
    path: Path | None
    digest: str                            # sha256 канонического JSON исходной книги (до замены anchor)
    base: "Book | None"                    # книга до подмен: у загруженной книги — None; у копии — исходная
                                           # (у копии копии — та же исходная). По ней ядро берёт то, что не
                                           # разыгрывается: путь режима-опоры FVC (М§4.6)
    def get(self, path: str) -> Any        # точечный путь (М прил. A); нет ключа — BookError
    def with_overrides(self, overrides: Mapping[str, Any]) -> "Book"   # копия с заменой значений по путям; anchors
                                           # не меняются; base копии — исходная книга

def load_book(path: Path | None = None, *, facts: Facts | None = None) -> Book   # схема + anchor; facts=None → load_facts()
def book_from_dict(data: Mapping[str, Any], *, facts: Facts) -> Book            # то же для словаря (тесты, фикстуры)
```

`model/book_schema.py`: `KEYS` (закрытый перечень путей прил. A с подстановками `<W>`, `<r>`, `<s>`,
`<b>`, `<t>`), `SWITCHES` (выключатель → выключаемые ключи, прил. A), `validate(data: Mapping) ->
None` (BookError: незнакомый ключ, `null` в читаемом пути, запреты М§0.4, незнакомые `kind`, `dist`,
`base`, `shortfall_rule`, `cor_basis`, включённый нереализованный модуль), `read_paths(data) ->
list[str]` (пути, которые ядро читает при данных выключателях — для тестов книги).

### 4.2. Время, траектории, миры, мост (core1)

```python
# model/timeline.py
@dataclass(frozen=True)
class Timeline:
    anchor: str                            # meta.anchor_period
    periods: tuple[str, ...]               # q = 1..Q
    days: tuple[int, ...]                  # d_q
    def index(self, period: str) -> int    # q
    def year(self, q: int) -> int; def h(self, q: int) -> int
def make_timeline(book: Book) -> Timeline
@dataclass(frozen=True)
class Clock:                               # дата оценки на линейке (М§0.2)
    valuation_date: date; q0: int; elapsed: float; tau: tuple[float, ...]
    roll: float                            # Δ = 0,25 × (pos(v) − pos(curve_as_of)), pos — по «концу дня»
def make_clock(book: Book, timeline: Timeline, valuation_date: date) -> Clock
def parse_period(p: str) -> tuple[int, int]            # "2026Q3" → (2026, 3)

# model/paths.py
def path_value(traj: Any, period: str, *, last_period: str) -> float     # правило М§0.4
def override_value(table: Mapping | None, year: int) -> float | None      # словари-переопределения по годам
def shift_trajectory(traj: Any, delta: float) -> Any                      # ось shift (кроме LT_from)
def get_path(data: Mapping, dotted: str) -> Any
def set_path(data: MutableMapping, dotted: str, value: Any) -> None

# model/worlds.py
@dataclass(frozen=True)
class WorldPath:                           # ряды по кварталам 1..Q
    world: str; key: tuple[float, ...]; cpi: tuple[float, ...]; wage: tuple[float, ...]
    ofz: Mapping[str, tuple[float, ...]]   # "ofz_1y" … "ofz_10y"
    real_key: tuple[float, ...]; price_index: tuple[float, ...]
    credit_growth: Mapping[str, Mapping[int, float]]   # сектор → год → доля (надстройка + сход к g_T)
    funds_growth: Mapping[str, Mapping[int, float]]
    lt_inflation: float; g_t: float
def world_path(book: Book, world: str, timeline: Timeline) -> WorldPath
@dataclass(frozen=True)
class Discount:
    k: tuple[float, ...]; k_t: float
    def df(self, tau: float) -> float      # DF_v(τ) с перекатом
def discount(book: Book, world: str, clock: Clock) -> Discount

# model/credit.py — единственное место перевода базисов (М§4.6)
@dataclass(frozen=True)
class Bridge:
    cor_method: str; cor_value: float; nim_method: str; nim_value: float
    cir_method: str; cir_value: float      # мост CIR (W1, A7): гайденс и cir_mgmt выпуска
    def to_engine_cor(self, v: float) -> float; def to_mgmt_cor(self, v: float) -> float
    def to_engine_nim(self, v: float) -> float; def to_mgmt_nim(self, v: float) -> float
    def to_engine_cir(self, v: float) -> float; def to_mgmt_cir(self, v: float) -> float
def bridge_from_facts(facts: Facts) -> Bridge                  # нет узла cir — FactsError
def regime_cor_engine(book: Book, bridge: Bridge, regime: str, timeline: Timeline) -> tuple[float, ...]
def year_in_mgmt(bridge: Bridge, facts: Facts, metric: str,
                 quarters: Sequence[tuple[str, float | None, float]]) -> float
    # годовая упр. метрика года с отчётными кварталами (М§4.6): metric ∈ {"cor", "nim", "cir"};
    # quarters — (период, значение движка, вес Z_q) всех кварталов года; у отчётного квартала (не позже
    # якоря) берётся упр. факт истории моста, у прогнозного — to_mgmt значения движка; отчётный квартал
    # без записи истории — FactsError. Одна функция у гейта guidance_gap и у выпуска

# model/nii.py
@dataclass(frozen=True)
class Transmission:
    sigma0: float                          # σ0_A — сдвиг спредов активов с lt_shift
    sigma0_liab: float                     # σ0_L — сдвиг спредов пассивов с lt_shift (0 при nii.sigma0_split = 1)
    lt_spread: Mapping[str, Mapping[str, float]]   # кредитная книга → мир → эффективный LT-спред к опоре (М§4.5)
    phi: float                             # φ — сжатие целиком (от nii.phi_split не зависит)
    phi_assets: float                      # φ_A = phi_split × φ — сжатие спредов кредитных книг с phi
    phi_liab: float                        # φ_L — добавка к стоимости пассивов с phi на единицу X_W,q (0 при phi_split = 1)
    phi_weights: Mapping[str, float]       # ω_b — доли кредитных книг с phi в разности опорных ставок X_W,q (М§4.4)
    loan_margin: Mapping[str, float]       # мир → кредитная маржа стационара (М§4.5)
    target_eng: float
    t_real: float                          # реализованная M − N (= T*, инвариант)
    t_local: Mapping[str, float]           # локальная T(W) — диагностика
    pairs: Mapping[str, float | None]      # "<W1>_<W2>" → парная передача соседних по key^LT миров (М§4.5)
    roe_equiv: float                       # T* × IEA_0/BV_0 × (1 − τ_eff года last_period)
def solve_transmission(book: Book, facts: Facts, bridge: Bridge) -> Transmission   # BookError, если передача не определена
```

### 4.3. Прогон клетки и сетки (core1)

```python
# model/grid.py
@dataclass(frozen=True)
class DividendRecord:                      # запись реестра (живой вход, М§15)
    year: int; dps: float; status: str     # "declared" | "recommended" | "paid"
    record_date: date | None; last_buy_date: date | None; ex_date: date | None; pay_date: date | None
    sources: tuple[str, ...]

@dataclass(frozen=True)
class LiveInputs:                          # что живого идёт в прогон (строит model/live.py)
    valuation_date: date
    prices: Mapping[str, float]            # тикер → ₽ (главный обязателен)
    price_dates: Mapping[str, date]
    register: tuple[DividendRecord, ...]

def live_from_book(book: Book, facts: Facts) -> LiveInputs    # цена и дата книги, реестр — dividends.json → register_seed

@dataclass(frozen=True)
class RunContext:                          # общее для 36 клеток одного прогона
    book: Book; facts: Facts; live: LiveInputs; timeline: Timeline; clock: Clock
    bridge: Bridge; transmission: Transmission
    posterior: Mapping[str, float]         # P(r | наблюдения окна) после A-P2u; априорные — базовые книги
    deviations: Mapping[str, Any]          # затухающие отклонения CoR и ЧПМ по режимам (М§12)
    expectations: Mapping[str, Any]        # μ_x,r(q) — среднее слоя «свой взгляд» по клеткам режима без
                                           # отклонений (предварительный проход, М§12); у наблюдений q ≤ 0 —
                                           # замороженные mu_cor, mu_nim записи
    prep: "Prepared | None"                # подготовленные константы прогона (структура якоря, пути режимов,
                                           # путь опоры FVC по book.base) — их читают выпуск и перезаякоривание
    worlds: Mapping[str, WorldPath]        # пути миров прогона
    discounts: Mapping[str, Discount]
    updates: tuple[Mapping[str, Any], ...] # наблюдения окна A-P2u с апостериорными после каждого (после предела и пола)
def make_context(book: Book, facts: Facts, live: LiveInputs | None = None) -> RunContext

@dataclass(frozen=True)
class CellResult:
    world: str; regime: str; scenario: str
    quarters: Mapping[str, tuple[float | None, ...]]   # ряды по кварталам 0..Q: bv, ni_sh, pbt, nii, llp, fvc, fees,
                                                        # opex, ins, misc, noncore, tax, oci, ci, div, dp, dpreg, rwa,
                                                        # k20, k11, n20, n11, e_unaudited, req20, req11, floor20, floor11,
                                                        # loans, loans_ac, funds, iea, assets, nim, cor, …
    annual: Mapping[str, tuple[float | None, ...]]     # по годам якоря … last_period (П§2 grid.cells[].annual)
    dps: Mapping[int, float]               # год прибыли → DPS (₽, не округлён)
    v_ri: float; v_ddm: float; bv_v: float
    x_t: float; roe_t: float; k_t: float; g_t: float; tv_ri: float; terminal_share: float
    n20_min: float; n11_min: float; gap_period: str | None
    flags: frozenset[str]                  # capital_gap, roe_below_k, dividend_cut (капитал урезал выплату),
                                           # crisis_skip (отмена кризисом; с dividend_cut не пересекается)
    years: tuple[int, ...]                 # ось годовых рядов
    tv_ddm: float; pv_ri_explicit: float; pv_terminal: float; bv_star: float
    roe_t_raw: float                       # ROE_T до угасания (М§7); roe_t — после (ROE'_T)
    g_capped: bool                         # сработала защита g_T
    y_x: float                             # доход на избыток, вычтенный из терминального года (М§7) — строка сверки
    la_headroom: float                     # HLA_Q — запас балансирующих активов над минимумом на конце Q
    decisions: tuple["Decision", ...]      # решения о дивиденде по годам (пул, запас, ступень, остаток, М§5.3)
    dps_policy: Mapping[int, float]        # год прибыли → DPS политики без капитального ограничения
    books: Mapping[str, Mapping[str, tuple[float | None, ...]]]   # книга → {"rate", "avg", "balance"} по q = 0…Q
    # ряды quarters сверх перечисленных, на которые опираются перезаякоривание и контрольная сверка:
    # overlay (Ov), dia, dp, dpreg, key, om, ot, ded20, one_off

def run_cell(ctx: RunContext, world: str, regime: str, scenario: str) -> CellResult     # М§4–§7

@dataclass(frozen=True)
class Layer:
    name: str                              # analytical | market_implied | macro_neutral
    title: str; world_weights: Mapping[str, float]
    prob: Mapping[tuple[str, str, str], float]         # вероятность клетки (W, r, s) в слое
    v0: float; bv_v: float; price: float; pb: float; excess: float
    pv_ri_explicit: float; pv_terminal: float; terminal_share: float
    roe_tc: float; k_tc: float; p_price_below_market: float; p_roe_below_k: float; capital_gap_mass: float

@dataclass(frozen=True)
class GridRun:
    ctx: RunContext
    cells: tuple[CellResult, ...]          # 36, порядок worlds.ids × regimes.ids × reg_scenarios.ids
    layers: Mapping[str, Layer]
    lam: float                             # λ книги
    low: float; high: float; point: float  # ₽: цена слоя «рыночные ставки как есть», «свой взгляд», точка при λ
    bridge_amount: float                   # B(v), млрд ₽ (М§8.1)
    pending_dividend: float                # D_pend, млрд ₽: объявленный, ещё не вычтенный из BV модели (М§8.2)
    unregistered_dividend: float           # U, млрд ₽: дивиденд, вычтенный клетками на концах кварталов ГОСА без
                                           # записи реестра, ожидание слоя «свой взгляд» (М§14.4)
    shares_out: float                      # N_out, млн шт.
    governance: float                      # g_gov
    def price_at(self, lam: float) -> float                     # низ + λ(верх − низ)
    def cell(self, world: str, regime: str, scenario: str) -> CellResult

def run_grid(book: Book, facts: Facts, live: LiveInputs | None = None) -> GridRun    # live=None → live_from_book
def price_from_v0(v0: float, bridge_amount: float, governance: float, shares_out: float,
                  pending: float = 0.0) -> float   # М§8.2: [(V0 − D_pend)(1 − g) + D_pend + B] × 1000 / N_out — одна
                                                   # формула у слоя, клетки, мира, прогонов полосы и P(цена ниже рынка)
def evaluate(book: Book, facts: Facts, live: LiveInputs | None = None) -> tuple[float, float]       # (низ, верх), ₽ — для полосы и поисков
```

`run_grid` — чистая функция входов: одинаковые книга, факты и живые входы дают бит-в-бит одинаковый
результат (регрессия книги, пул процессов полосы).

### 4.4. Проверки (`model/checks.py`) — core1

```python
@dataclass(frozen=True)
class Finding:
    name: str                              # имя из М§14.1–§14.3
    kind: str                              # "invariant" | "gate" | "flag"
    fired: bool; mass: float | None; cells: tuple[str, ...]; message: str; detail: Mapping[str, Any]
def check_invariants(run: GridRun) -> list[Finding]
def check_gates(run: GridRun, *, today: date | None = None) -> list[Finding]     # гейты М§14.2 и флаги М§14.3 ядра
SHARE_KEYS: tuple[str, ...]                # nii.sigma0_split, nii.phi_split — доли, по осям которых ходит гейт пола
def share_axis_ends(book: Book) -> list[dict[str, float]]
    # углы области осей долей (сочетания концов осей полосы по SHARE_KEYS, без точки книги): гейт
    # lt_spread_floor проверяется при значениях книги и в каждом углу (М§4.5, §14.2);
    # Finding.detail["axis_ends"] — углы, где пол нарушен: [{"shares": {ключ: доля}, "worlds": [...]}]
@dataclass(frozen=True)
class GateStatus:
    name: str; fired: bool; mass: float | None; status: str   # quiet | explained | unexplained | expired | mass_off
    explanation: str | None; expected_mass: float | tuple[float, float] | None; valid_until: date | None
    expiring: bool                         # срок в ближайшие 30 дней: флаг explanation_expiring, не тревога (М§14.2)
def guidance_band(node: Mapping[str, Any]) -> tuple[float | None, float | None]
    # границы узла гайденса по kind; у point — ± tol узла (нет tol — разряд числа из text); одна функция
    # для гейта и выпуска (копий в payload.py нет)
def load_gate_explanations(path: Path | None = None, *, today: date | None = None) -> Mapping[str, Mapping[str, Any]]
def gate_statuses(findings: Sequence[Finding], explanations: Mapping, *, today: date) -> list[GateStatus]
```

Инварианты уровня выпуска (`release_numbers`, `jump_guard`, `payload_contract`, `exdate_jump`)
проверяет ядро части 2 (`model/payload.py`) тем же типом `Finding`.

### 4.5. Полоса, поиски, диагностики (core2)

```python
# model/uncertainty.py
FAST_DRAWS: int                            # прогонов быстрой сборки (техническая константа)
def round_half_up(x: float, step: float) -> float          # ROUND_HALF_UP в Decimal (М§10); одна функция на всё ядро
@dataclass(frozen=True)
class Band:
    axes: tuple[Mapping[str, Any], ...]    # оси книги по порядку
    s: tuple[tuple[float, ...], ...]       # положение оси s ∈ [−1, 1] по прогонам
    low_draws: tuple[float, ...]; high_draws: tuple[float, ...]   # ₽, 0,1 (ROUND_HALF_UP)
    ddm_ri_max: float                      # max |V_DDM − V_RI| / V_RI по всем клеткам всех прогонов (М§6.4)
    def centres(self, lam: float) -> list[float]
    def headline(self, lam: float, prices: Mapping[str, float], main: str) -> Mapping[str, Any]  # квантили тип 7, среднее, P(ниже), печать
    def contributions(self, lam: float) -> list[Mapping[str, Any]]   # Спирмен, средние ранги при равенстве
def band(book: Book, facts: Facts, live: LiveInputs, *, draws: int | None = None, workers: int | None = None) -> Band
def judgements(book: Book, facts: Facts, live: LiveInputs, band: Band) -> list[Mapping[str, Any]]
    # П§2 judgements.rows: оси полосы и valuation.uncertainty.off_band_axes (in_band = false; у книги 1.2 пусто);
    # mean_shift — сдвиг положения оси (М§10); source — комментарий книги к первому пути оси, не служебный путь;
    # unit — поле unit оси книги, без него — по путям оси (сдвиг доли — pp), не по величине значения
class MedianAnchor: ...                    # пересчёт медианы на median_draws с привязкой к печатаемой (перенос 850oa)
def median_shift(base: Sequence[float], shifted: Sequence[float], window: tuple[float, float]) -> float
    # оценщик срединных прогонов (М§10): среднее попарных разностей центров по прогонам, чей ранг в base
    # лежит в окне квантилей window (valuation.sensitivities.rank_window); чувствительности и местный наклон
def middle_draws(values: Sequence[float], window: tuple[float, float]) -> list[int]
    # срединные прогоны: номера прогонов с рангом в окне квантилей (по ним усредняет median_shift)
def draw_book(book: Book, axes: Sequence[Mapping[str, Any]], s: Sequence[float]) -> Book
    # книга одного прогона полосы: оси в положениях точки гиперкуба (её сетка — ровно прогон полосы)
def workers() -> int                       # из BANK_WORKERS

# model/reverse.py
def reverse_dcf(book: Book, facts: Facts, live: LiveInputs, band: Band) -> Mapping[str, Any]   # П§2 reverse_dcf (rows, bank_rows):
    # решение внутри диапазона книги уточняется на полной полосе секущей, не больше min(median_refine, 2)
    # пересчётов, до невязки в стопе; строка несёт gap_basis ("full" | "subsample") и converged
def root_in_range(value, gap, estimate, low, high, tol) -> bool
    # «в диапазоне книги» — о корне: решение с невязкой в стопе лежит в диапазоне; у несошедшегося
    # уточнения в диапазоне лежат и приближение, и оценка корня секущей (М§11.2)

# model/nextreport.py
def open_period(book: Book, facts: Facts) -> str           # q* (М§13)
def model_expectation(book: Book, facts: Facts, period: str, *, live: LiveInputs | None = None) -> Mapping[str, Any]
    # {"period", "ni_q", "nim_q_mgmt", "cor_q_mgmt", "nim_q_engine", "cor_q_engine", "loans_ac", "iea",
    #  "tau_eff", "days", "by_regime": [{"regime", "posterior", "nim_q_mgmt", "cor_q_mgmt"}]} — слой «свой взгляд»;
    #  ЧПМ и CoR — прогноз фильтра A-P2u Σ_r P(r) (μ_r + ρ^k m_r) (М§13); наблюдение, равное ожиданию,
    #  нейтрально только при одном режиме, при смеси — обучение (М§12)
def next_report(book: Book, facts: Facts, live: LiveInputs, band: Band) -> Mapping[str, Any]   # П§2 next_report:
    # в строке таблицы второе наблюдаемое — None; нейтральное значение — по двум допускам (невязка и значение,
    # valuation.next_report.value_tol): строка таблицы сама корнем не считается — ни у медианы, ни у точки; край
    # отрезка без смены знака с невязкой в допуске — корень; невязка поиска и местный наклон — оценщиком
    # median_shift (разность двух медиан на median_draws прогонах — ступень); neutral.<x>.middle_expectation —
    # ожидание квартала срединных прогонов
def middle_expectation(book: Book, facts: Facts, live: LiveInputs, base: Band, lam: float,
                       window: tuple[float, float], period: str) -> Mapping[str, float]
    # {"cor", "nim", "draws"} — среднее прогноза фильтра A-P2u по книгам срединных прогонов, упр. базис (М§13)

# model/attribution.py
@dataclass(frozen=True)
class Step: component: str; title: str; point_rub: float; median_rub: float | None; note: str = ""
def inputs_snapshot(book: Book, facts: Facts, live: LiveInputs) -> Mapping[str, Any]   # кладётся в выпуск вне хэша
def attribute(previous: Mapping[str, Any], book: Book, facts: Facts, live: LiveInputs, *, engine_commit: str) -> list[Step]

# model/live.py
@dataclass
class LiveReport:
    applied: bool; degraded: list[str]; prices: dict[str, dict]; curve: dict | None
    key_rate: dict | None; register: dict; flags: dict[str, dict]
    degraded_flag: bool                    # тревога сборки (§6): деградировал живой вход оценки (цена, реестр,
                                           # кривая, ключевая) или источник из collector["alarm"]; причины
                                           # восполнимых источников плиток моложе трёх тактов остаются в degraded
def apply_live(book: Book, facts: Facts, outputs: "IndicatorOutputs | None", *, today: date | None = None,
               previous: Mapping[str, Any] | None = None) -> tuple[LiveInputs, LiveReport]
def read_last_accepted(state_dir: Path | None = None) -> dict             # $BANK_STATE_DIR/live.json
def write_last_accepted(report: LiveReport, state_dir: Path | None = None) -> None   # только после годного выпуска
```

### 4.6. Выпуск (`model/payload.py`, `model/build_release.py`, `model/book_results.py`) — core2

```python
# model/payload.py
SCHEMA = "sber-v1"
REQUIRED_TOP_LEVEL: tuple[str, ...]        # 28 блоков П§1
REQUIRED_FIELDS: Mapping[str, tuple[str, ...]]   # узел → обязательные поля (П§2; «?» — необязательные); узлы —
                                           # и вложенные: "a.b", "a.b[]", "a.b.*" (тест полноты по тексту П§2)
FORBIDDEN_FIELDS: Mapping[str, tuple[str, ...]]  # П§6
PRECISION: Mapping[str, float]             # П§0.3
UNIT_CODES: tuple[str, ...]                # коды единиц строк П§0.2
CODE_DIRS: tuple[str, ...]                 # каталоги кода выпуска = CODE_DIRS в ops/run.sh (тест ops):
                                           # model, indicators, ops, data/assumptions, data/facts, data/indicators,
                                           # data/checks, requirements.txt (без *.md, web/, functions/, docs/)
LAMBDA_STEPS = 20; QUARTERS_SHOWN = 14; HISTORY_ROWS = 400; MAX_BYTES = 500_000; WARN_BYTES = 400_000
HASH_EXCLUDED: tuple[str, ...]             # поля и блоки вне хэша (П§0.4)
HYGIENE_RULES: tuple[...]                  # почты, телефоны, IPv4, пути ФС (П§7 п. 3)

@dataclass
class Release:                             # всё посчитанное для одного выпуска
    book: Book; facts: Facts; live: LiveInputs; live_report: LiveReport
    run: GridRun; band: Band; reverse: Mapping | None; next_report: Mapping | None
    judgements: list; findings: list[Finding]; gates: list[GateStatus]
    indicators: "IndicatorOutputs | None"; previous: Mapping | None; history: list[Mapping]
    fast: bool; engine_commit: str
def make_release(*, live: bool = True, fast: bool = False, today: date | None = None,
                 state_dir: Path | None = None, data_repo: Path | None = None) -> Release
def build_payload(release: Release) -> dict
def validate(payload: Mapping[str, Any], *, publish: bool = False) -> list[str]   # [] — годен; publish=True требует meta.fast = false
def payload_hash(payload: Mapping[str, Any]) -> str                            # sha256 канонического JSON без HASH_EXCLUDED
def compact_bytes(payload: Mapping[str, Any]) -> int                           # П§0.4
def engine_commit() -> str                                                     # 40 hex; без git — GITHUB_SHA (CI) или "0"*40

# model/book_results.py
def book_results(book: Book | None = None, facts: Facts | None = None, *, slow: bool = True) -> dict
def render_run_output(results: Mapping[str, Any]) -> str
def main(argv: list[str] | None = None) -> int
```

`results.json` — ключи верхнего уровня: `book_version`, `facts_date`, `engine_commit`, `draws`,
`slow`, `derived` (М прил. A: `nii` — с `sigma0_liab`, `phi_assets`, `phi_liab`, `lt_spread`, `loan_margin`, `T_pairs`, `roe_equiv_pairs`;
`anchor`, `capital`, `regimes` — с `shock_year`), `layers`, `headline` (медиана, полосы, P10…P90,
точка, низ, верх при книге, вклады осей, `ddm_ri_max`), `cells` (36 строк: мир, режим, сценарий, V, P/B, ROE_T, k_T, n20_min, DPS
года якоря), `central_cell`, `sign_test` (тест знака объёмных эффектов, М§4.5), `checks` (инварианты,
гейты, флаги), `reverse_dcf`, `next_report`, `judgements`. Числа — полной точности; `render_numbers.py`
печатает их по меткам. В `run_output.txt` строка обратного расчёта печатает «в диапазоне» только при
корне в диапазоне, приближение — «вне диапазона, уточнение не сошлось».

**`central_cell`** — путь модальной клетки книги по годам, выбранной правилом, а не литералом: мир
с наибольшим весом слоя «свой взгляд» (`joint.world_prob`), режим с наибольшей вероятностью после
A-P2u, сценарий капитала с наибольшей P(s | r) у этого режима; при равенстве — первый по порядку
книги. Рядом с путём — `cell` (метка `W/r/s`), `probability` (вероятность клетки в слое «свой
взгляд»), `scenario_weight` (P(s | r)) и `rule` (словами). Контрольная модель берёт центральную
клетку тем же правилом. Опорная клетка диагностик (`norm`, `schedule`: передача по клеткам, флаг
`ni_jump`, кредитная маржа) — другое понятие и от вероятностей не зависит.

**Тревоги выпуска** (`alerts`, код 3 сборки): деградация живых входов (`live.degraded_flag`), флаг
`dividend_register`, размер ≥ `WARN_BYTES`. Истекающее объяснение гейта и истёкший документ
эмитента — флаги `explanation_expiring` и `policy_expired` (М§14.3), в тревоги не входят.

---

## 5. Индикаторы: выходы для выпуска и связь с ядром

Индикаторы **не импортируют ядро**, кроме команды `nowcast`: она лениво импортирует
`model.book.load_book`, `model.book.load_facts` и `model.nextreport.model_expectation` (ожидание
модели для нау-каста). Ядро читает индикаторы только через `indicators/outputs.py`, лениво:
`release_inputs` и `combine` (реэкспорт из `indicators/nowcast.py` — сборка пересчитывает
`nowcast.quarter.by_target` с ожиданием своего прогона). Тикеры и регномер индикаторы берут из
`meta.company` книги (`indicators/config.py`, чтение YAML без импорта `model`), тикеры аналогов —
из `facts/peers.json`.

```python
# indicators/outputs.py
@dataclass(frozen=True)
class PricePoint:
    ticker: str; price: float; date: str; time: str | None      # дата сделки и время МСК
    source: str                                                  # "tinvest" | "iss"
    fetched_at: str                                              # ISO UTC

@dataclass(frozen=True)
class IndicatorOutputs:
    as_of: str                                                   # момент чтения состояния, ISO UTC
    prices: Mapping[str, tuple[PricePoint, ...]]                 # тикер → наблюдения за 10 торговых дней, новейшее последним
    price_history: Mapping[str, tuple[tuple[str, float], ...]]   # тикер → дневные закрытия за 12 мес. (дата, ₽)
    peer_prices: Mapping[str, PricePoint]
    curve: Mapping[str, Any] | None                              # {"as_of", "nodes": {"1": доля, "3", "5", "10", …}, "source"}
    key_rate: Mapping[str, Any] | None                           # {"value", "date", "since"}
    register: tuple[Mapping[str, Any], ...]                      # записи реестра: {year, dps, status, record_date, last_buy_date,
                                                                 #  ex_date, pay_date, decided_date, sources} (даты — строки ISO);
                                                                 #  источники (М§15): календарь дивидендов T-Invest + новость Интерфакса/ТАСС
                                                                 #  о решении ГОСА или рекомендации СД; запасной — record-dividend;
                                                                 #  model/live.py проверяет их (два источника у declared) и строит
                                                                 #  model.grid.DividendRecord
    brokers: Mapping[str, Any] | None                            # только агрегаты П§2 market.brokers
    groups: tuple[Mapping[str, Any], ...]; tiles: tuple[Mapping[str, Any], ...]   # П§2 indicators
    ras_months: tuple[Mapping[str, Any], ...]                    # П§2 nowcast.months.rows
    form102: tuple[Mapping[str, Any], ...]                       # П§2 nowcast.form102
    ras_schedule: Mapping[str, Any] | None                       # П§2 calendar.next_ras без days (с form102_latest_est —
                                                                 #  верхним краем окна оценки даты формы)
    nowcast: Mapping[str, Any] | None                            # содержимое nowcast.json (ниже)
    journal: Mapping[str, Any]; admission: Mapping[str, Any]; retro: Mapping[str, Any]   # П§2 nowcast.journal, admission, retro
    collector: Mapping[str, Any]                                 # {"degraded": [причины словами], "sources": {имя: статус},
                                                                 #  "streak": {имя: тактов деградации подряд}, "retry": bool,
                                                                 #  "alarm": [источники, получившие код 3 по политике §6]}
    flags: Mapping[str, Mapping[str, Any]]                       # {"ras_mismatch": {"raised": bool, "detail": str}}

def release_inputs(*, tickers: Sequence[str], peer_tickers: Sequence[str] = (), state_dir: Path | None = None,
                   today: date | None = None) -> IndicatorOutputs
    # только чтение $BANK_STATE_DIR, без сети; нет ряда — поле None/пусто и причина в collector.degraded; не бросает
from indicators.nowcast import combine                          # реэкспорт: ядро берёт combine отсюда

# indicators/nowcast.py
def ras_inputs(store: "Store", *, period: str, today: date, bridge: Mapping[str, Any],
               sigmas: Mapping[str, Any] | None = None, frozen: Mapping[str, Any] | None = None) -> Mapping[str, Any]
    # месяцы квартала (известные / оценённые профилем), оценка РСБУ, мосты (ЧП; ЧПД и IEA — T3; CoR — T4, если есть),
    # w, std_error — без ожидания модели; frozen — мост, замороженный при первой записи квартала
def combine(ras: Mapping[str, Any], expectation: Mapping[str, float],
            benchmarks: Mapping[str, Mapping[str, float | None]] | None = None) -> Mapping[str, Any]
    # П§2 nowcast.quarter.by_target: прогноз = ожидание + w·(оценка × мост − ожидание); нет моста — прогноз = ожидание;
    # expectation — {ni_q, nim_q_mgmt, cor_q_mgmt}; чистая функция

# indicators/store.py
class Store:
    def __init__(self, root: Path | None = None): ...          # root=None → $BANK_STATE_DIR
    def save_raw(self, source: str, name: str, body: bytes, *, url: str, fetched_at: str | None = None,
                 status: int | str | None = None, extra: dict | None = None) -> Path   # не перезаписывает
    def upsert(self, series_id: str, points: Iterable["Point"], **meta) -> "Series"
    def load(self, series_id: str) -> "Series | None"
    def value_as_of(self, series_id: str, period: str, as_of: str) -> float | None

# indicators/journal.py
class Journal:
    def __init__(self, path: Path | None = None, *, readonly: bool = False): ...   # path=None → $BANK_STATE_DIR/journal.sqlite
    def record_forecast(self, *, target: str, period: str, horizon: str, forecast: float,
                        benchmarks: Mapping[str, float], release_sha: str, recorded_at: str | None = None,
                        std_error: float | None = None, equation: str = "", version: str = "",
                        inputs: Mapping[str, Any] | None = None) -> int
        # release_sha — последний собранный выпуск на момент записи (нау-каст идёт до сборки; на свежем
        # состоянии — нули); книга самого ожидания — inputs["book_version"] (в выпуске — entries[].book_version)
    def record_actual(self, *, target: str, period: str, value: float, source: str,
                      reported_on: date | None = None) -> None   # факт неснимаем
    def admission(self, target: str) -> Mapping[str, Any]
    def export(self) -> Mapping[str, Any]                       # journal.json ветки данных
```

Журнал неизменяем на уровне SQLite: триггеры BEFORE UPDATE и BEFORE DELETE и, против `INSERT OR
REPLACE`, BEFORE INSERT … WHEN EXISTS (та же цель, период, горизонт, ключ) → `RAISE(ABORT)` у
таблиц прогнозов, фактов и замороженных эталонов (или `PRAGMA recursive_triggers = ON`); тест на
REPLACE. Плитка `indicators.tiles[]` может нести служебное поле `series` (id ряда) — витрина его не
читает; её `unit` и `basis` — коды П§0.2 (деньги — `bn`, цена — `price`, индекс — `level`; рынок —
`market`, нормативы Н1.x банка — `regulatory`). `indicators/http.py::fetch(…, hosts=…)` ходит
только на разрешённые хосты по `https` и сверяет конечный адрес после перенаправлений
(`Response.final_url`): документ эмитента с чужого хоста не скачивается.

**Цели журнала** (DESIGN §5): `ni_q` (T1, ЧП акционерам квартала МСФО, млрд ₽), `nim_q` и `cor_q`
(T3/T4, упр. базис, доли). **Имена рядов** — `<источник>.<что>[.<тикер или узел>]`
(`tinvest.price.<тикер>`, `iss.price.<тикер>`, `iss.zcyc.<срок>`, `cbr.key_rate`,
`cbr.f135.n1_1`, `ras.release.ni` …); тикеры подставляются из книги, литералов в коде нет.

**`nowcast.json`** (пишет `python -m indicators.collect nowcast`): `{"period", "computed_at",
"book_version", "ras": ras_inputs(...), "expectation": model_expectation(...), "quarter": {…П§2
nowcast.quarter…}}`. Сборка выпуска пересчитывает `quarter.by_target` через `combine` с
ожиданием своего прогона и сама считает `nowcast.year` (П§2).

---

## 6. Сборка выпуска: порядок и команды

**Порядок `make_release`** (core2):
1. `facts = load_facts()`, `book = load_book(facts=facts)`.
2. Без `--book-only`: `outputs = indicators.outputs.release_inputs(tickers=…, peer_tickers=…)`.
3. `live, live_report = model.live.apply_live(book, facts, outputs, previous=read_last_accepted())`
   (`--book-only` → `live_from_book`).
4. `run = run_grid(book, facts, live)`; `band = band(book, facts, live)` (`--fast` → `FAST_DRAWS`).
5. Без `--fast`: `reverse_dcf`, `next_report`, `judgements` (в быстрой — строки со статусом
   «не считалось» и причиной).
6. Проверки: `check_invariants`, `check_gates`, флаги; объяснения гейтов; инварианты выпуска.
7. Прошлый выпуск и история — `latest.json` и `history.json` из `$BANK_DATA_REPO_DIR` (нет — `None`
   и пусто).
8. `payload = build_payload(release)`; `validate(payload)`; запись `release/latest.json`
   (компактный JSON; быстрая сборка — `release/latest-fast.json`); повторный `validate`
   записанного файла; итог — `release/build_report.json`; после годного полного выпуска на живых
   входах — `write_last_accepted`.

| Команда | Что | Коды выхода |
|---|---|---|
| `python -m model.build_release` | полный выпуск на живых входах → `$BANK_STATE_DIR/release/latest.json` | 0 — собран, в том числе с плашками (истекающее объяснение гейта, истёкший срок документа эмитента — флаги М§14.3); **1 — не собран** (контракт, инвариант, сработавший гейт без действующего объяснения: нет записи, срок истёк или **масса вне коридора ожидания** — М§14.2, П§7 п. 13; защита заголовка; нет цены главного тикера), прежний выпуск остаётся; **3 — собран, но тревога** (деградация живых входов — `live.degraded_flag`, политика ниже; флаг `dividend_register` — нет записи реестра за год к концу квартала ГОСА; размер ≥ 400 000 байт) |
| `… --book-only` | на цене, дате и реестре книги (без индикаторов) | те же |
| `… --fast` | быстрая сборка (`meta.fast: true`, мало прогонов, без поисков) → `latest-fast.json`; не публикуется | те же |
| `… --check [--fast] [--book-only]` | собрать в памяти и проверить контракт, ничего не писать; в CI — `--check --fast --book-only` (на раннере нет состояния и живой цены); с путём `--check <файл>` — проверить готовый файл | 0 / 1 (тревоги печатаются, код 0) |
| `python -B -m model.book_results [--fast]` | `data/assumptions/results.json`, `run_output.txt` | 0 / 1 |
| `python data/assumptions/build_assumptions.py [--check]` | машинная книга из шаблона, миров и надстройки | 0 / 1 |
| `python ops/tools/build_facts.py [--check] [--anchor <период>] [--out <каталог>] [--report <файл>] [--expected <файл> [--facts <каталог>]]` | факты из листов этапа 1 (`$BANK_HANDOFF_DIR`); якорь — из аргумента; `--report` — файл отчёта квартала якоря; `--expected` — факты нового якоря из прежних фактов и ожидаемого отчёта (§3.2) | 0 / 1 |
| `python -B ops/tools/reanchor.py --expected <файл>` / `--facts <файл> [--out <каталог>] [--band <прогонов>] [--valuation-date <дата>] [--transmission keep\|resolve] [--no-cells] [--compare-facts <каталог>] [--template <файл>] [--version <версия>]` | перезаякоривание (М§15.2): ожидаемый отчёт закрываемого квартала; кандидат книги, шаблона и фактов в `<каталог>` (по умолчанию `var/reanchor/<квартал>/`) с разложением скачка «обучение / механика / выпуклость / сюрприз»; в квартале шока (отчёт за первый квартал года при якоре на четвёртом) — ещё строка «обучение» отчёта без шока. Передача ставки — режим `keep` (по умолчанию; `resolve` — только диагностика). Внутри репозитория пишет только под `var/` — любой другой каталог рабочей копии (`data/`, `model/`, `docs/`, `tests/`, `ops/`, `web/`…) — отказ | 0 — сделано; 1 — отказ (механика вне допуска, нет входов, отчёт не того квартала, запись вне `var/`) |
| `python -m indicators.seed [--check]` | история рядов `data/indicators/seed/` из листов этапа 1 (`$BANK_HANDOFF_DIR`) | 0 / 1 |
| `python -m indicators.collect <режим>` | режимы: `collect` (утренний сбор по реестру), `daily-indicators` (вечерний сбор рядов и цен), `release-watch` (дозор релизов), `nowcast` (нау-каст и запись журнала), `status`, `health`, `record-ras`, `record-actual`, `record-consensus`, `record-guidance`, `record-dividend` (ручная запись решения ГОСА или рекомендации СД: год, DPS, отсечка, дата решения, ссылка на документ эмитента — запасной второй источник реестра, М§15); у режимов сбора — `--only <сборщик>[,…]` и `--skip <сборщик>[,…]` (имена `sources.COLLECTORS`) | 0 — ок, в том числе деградация восполнимого источника моложе трёх тактов; 1 — провал критического источника или команды; 3 — тревога: отказал невосполнимый источник, тревога реестра или деградация восполнимого источника третий такт подряд (политика ниже) |
| `ops/run.sh <режим>` | такты `collect`, `release-watch`, `daily`, `rebuild`, `rollback <sha> ["причина"]` (схема Ленты; строки журнала «готово: …», «ТРЕВОГА (код 8): …», «ПРОВАЛ на шаге …»); код 3 сборки или сборщиков такт превращает в тревогу | 0; 1 — провал шага, выпуска нет; 8 — тревога при выполненных шагах (единственный код тревоги; юниты на нём не перезапускаются); 64 — неверный режим; 75 — замок занят; 78 — нет env-файла или обязательной переменной |
| `python ops/publish.py [--dry-run]`, `--rollback <sha> [--note "причина"]`, `--verify [--once]`, `--sync`, `--init` | публикация `release/latest.json` в репозиторий данных (append-only); откат на `releases/<sha>.json`; сверка через боевую дверь `/api/model`; клон ветки `release` = origin; создание ветки | 0 — сделано; 1 — отказ (ворота публикации, push; каталог клона — не клон данных); 3 — сделано с тревогой (журнал не выгружен; сверка не дождалась выпуска на двери или он пришёл запасным источником; дерево ветки данных ≥ 700 МБ) |
| `python ops/tact.py window \| news \| done \| retry \| simulate <источник> <режим>` | решения такта для `run.sh`: отложенное дозора; `retry` — повторять ли сбор (0 — отчёт сборщиков несёт `retry: true`: отказал невосполнимый источник; 1 — повтор ничего не даст или отчёта нет); проба тревоги | 0 / 1; 64 — неверный вызов |
| `python ops/tools/probe.py [--pdf <адрес>] [--only <проба>…]` | проба источников с сервера (таблица; `--only` — названные пробы, проба `sber_ocr` — дымовая проверка распознавания PDF релиза — идёт только по имени): без `--pdf` и без адреса, найденного дозором, берёт адрес PDF последнего релиза из затравки `data/indicators/seed/ras_release.json` (хост — из `sources.yaml`) и печатает, откуда адрес; на пустом состоянии исправные источники дают код 0 | 0 — все прошли; 1 — есть отказ |
| `python ops/tools/devserver.py <порт> <выпуск.json>` | предпросмотр витрины с боевой CSP | — |

**Такты** (`ops/run.sh`, DESIGN §6.1): `collect` — утро: `indicators.collect collect`, `health`; по
понедельникам — тесты такта «в будущем» (`FAKE_TODAY`, тот же набор `TACT_TESTS` без тестов сроков,
§9: красным он бывает только из-за теста, привязанного к дате, — это дефект, а не будильник).
`release-watch` — в дни релизов каждые
10 минут: `indicators.collect release-watch`; принят новый месяц РСБУ или МСФО — `nowcast`,
сборка, публикация. `daily` — будни вечером: `daily-indicators` → `nowcast` → тесты такта →
`model.build_release` → `publish.py` → сверка. `rebuild` — раз в 15 минут: сменился код
(`CODE_DIRS`, §4.6) с опубликованного выпуска — тесты такта, сборка, публикация. Бюджет времени
шагов — `ops/budgets.json` (полный выпуск на 4 процессах ≤ 15 мин, М§10); тесты такта — метка
`tact` (§9): цель — весь набор не дольше 3 мин на сервере (`tact_tests_target`, сверяется замером
шага «тесты такта»), потолок того же шага в CI — 300 с (`tact_tests_ci_ceiling`).

**Политика тревог** (В9, В17, В20). Тревога — то, что требует внимания человека и что повтор не
снимет; повтор — только там, где он спасает данные; срок, о котором достаточно знать заранее, —
плашка выпуска, а не тревога. **В штатном состоянии каждый такт кончается кодом 0.**

* **Невосполнимый источник** (`sources.IRRECOVERABLE`: винтажи форм ЦБ, снимки T-Invest, новости и
  PDF дня релиза) отказал или деградировал — сборщики отвечают кодом 3 и ставят
  `collector_report.json → retry: true`: день источника потом не добрать. Такт `collect` в этом и
  только в этом случае повторяет сбор — сам, внутри прогона: до `ops/budgets.json →
  collect_retry.attempts` попыток с паузой `collect_retry.pause_s`; не добрал — «ТРЕВОГА (код 8)».
  Источник без токена (`missing`) — тревога без повтора: повтор токена не добавит.
* **Восполнимый источник, питающий только плитки и наблюдения** (не `IRRECOVERABLE` и не `CRITICAL`),
  деградировал — такт не повторяется и не тревожит, пока деградация не держится
  `sources.DEGRADED_STREAK` (3) такта подряд: до этого — код 0, строка «деградация (такт k из 3)» в
  журнале и причина в `collector_report.json` и `live.degraded` выпуска; с третьего такта подряд —
  код 3 и тревога. Счётчик — `collector_report.json → streak`, сбрасывается первым удачным сбором.
  Счётчик считается раз за день: у тактов `collect` и `daily-indicators` повтор в тот же день его не
  увеличивает (`collector_report.json → streak_counted`); дозор релизов (`release_watch`) — по тому же
  счётчику: поздние источники дня релиза расхождение снимают. Признак `retry` у деградации
  невосполнимого источника ставится только при причине, которую снимет повторный сбор (сеть), а не
  человек.
* **Критический источник** (`sources.CRITICAL`: `iss` — запасной путь живой цены) отказал — код 1,
  провал шага; его повторяет systemd. Его деградация — тревога сразу (код 3), без счётчика.
* **Такты со сборкой** (`daily`, `rebuild`, `release-watch`) судят источники тем же счётчиком. Сборка
  получает от сборщиков список `collector.alarm` — источники, уже получившие код 3, — и поднимает
  `live.degraded_flag` (код 3 сборки) только по двум основаниям: деградировал живой вход оценки
  (цена не принята, запись реестра не принята, кривая или ключевая не приняты) или источник из
  `collector.alarm`. Причина деградации восполнимого источника плиток моложе трёх тактов печатается
  в `live.degraded` и на плитке, но тревогой сборки не становится.
* Тревога реестра дивидендов, тяжёлый выпуск, несостоявшаяся сверка — тревога сразу, без повтора.
* **Не тревога и не отказ — плашка** (В17): объяснение сработавшего гейта истекает в ближайшие
  30 дней (`explanation_expiring`); срок документа эмитента истёк (`policy_expired`: срок
  дивидендной политики — «действует прежняя до новой»). Сборка с такими флагами отвечает кодом 0.
  Отказ сборки (код 1) — только когда у сработавшего гейта нет действующего объяснения (М§14.2).
* Сборщик T-Invest запускается отдельной командой (`--only tinvest`, §8), остальные — второй
  (`--skip tinvest`); оба запуска пишут один `collector_report.json` (статусы сливаются по
  источникам, счётчик `streak` считается раз за такт), код сбора — худший из двух.
* **Юниты на коде 8 не перезапускаются** (`RestartPreventExitStatus` содержит 8 у всех тактов):
  повтор тревогу не снимет. Стоящая тревога держит юнит в «упало», и мост тревог шлёт только смену
  состояния: новые причины при стоящей тревоге читаются в журнале такта — поэтому код 8 оставлен
  тому, что действительно требует человека. Перезапуск по коду 1 ограничен окном, выведенным из
  `ops/budgets.json` (`StartLimitIntervalSec` ≥ `StartLimitBurst` × (потолок прогона + пауза)):
  не больше трёх стартов за окно, дальше юнит стоит до следующего таймера.

**Репозиторий данных** (публичный, ветка `release`, клон — `$BANK_DATA_REPO_DIR`), append-only:
`releases/<sha256>.json` (неизменяемы), `latest.json` (указатель — копия последнего), `history.json`
(строка на публикацию и откат, П§4), `journal.json` (полный журнал прогнозов). Выпуски пишутся
**компактно** — без отступов, разделители `,` и `:`, UTF-8 (В11: на треть меньше; прореживания
старых выпусков нет). **Сторож объёма:** `publish.py` меряет размер дерева ветки и с
`ops/budgets.json → data_branch_alert_mb` (700 МБ; предел сайта Pages — 1 ГБ) публикует с тревогой
(код 3). `published_at` вписывает `publish.py` (вне хэша). Каталог клона проверяется до любой
разрушающей команды: это клон данных (есть `refs/remotes/origin/release`), он не совпадает с
каталогом состояния или кода и не их предок; непустой каталог без `.git` не удаляется — отказ.
Витрина читает `latest.json` через `functions/api/model.js`.

---

## 7. Состояние и форматы на диске

### 7.1. Каталог состояния `$BANK_STATE_DIR`

Без переменной — `<репозиторий>/var/state` (локально и в тестах — `tmp_path`).

```
raw/<источник>/<YYYY-MM-DD>/<имя>.<расш>[.gz]     сырой ответ как есть (+ <имя>.meta.json: url, fetched_at, sha256, status);
                                                   неизменяем; защищённые источники не ротируются   — indicators/store.py
series/<series_id>.json                            нормализованный ряд: точки {period, value, fetched_at, status, source}
                                                   с винтажами («что было известно на дату»)        — indicators/store.py
journal.sqlite                                     журнал прогнозов; записи неизменяемы (триггеры UPDATE/DELETE
                                                   и INSERT при существующей записи, §5)            — indicators/journal.py
nowcast.json                                       последний нау-каст открытого квартала (§5)         — indicators collect nowcast
collector_report.json                              статусы источников последнего такта, деградация, счётчик
                                                   тактов деградации подряд (streak; streak_counted — кто
                                                   уже посчитан сегодня), retry; details[] по источникам с
                                                   code (0 | 1 | 3 по политике §6) и retry          — indicators/collect.py
live.json                                          последняя принятая цена по тикерам {value, date, time, source, accepted_at},
                                                   реестр на дату                                    — model/live.py
release/latest.json, release/latest-fast.json      собранный выпуск (компактный JSON); быстрая сборка  — model/build_release.py
release/build_report.json                          итог сборки: коды, тревоги, размер, хэш            — model/build_release.py
data-repo/                                         клон репозитория данных, если BANK_DATA_REPO_DIR не задан — ops/publish.py
release_watch.pending, release_watch.seen          отложенное дозора (принятое, но не опубликованное) и
                                                   виденные кандидаты МСФО                           — ops/tact.py
logs/, run.lock, release.commit, release.pushed,   журналы тактов, замок, отметки: код подтверждённого
rebuild.tried, future.checked                      выпуска, отправленный выпуск, попытка пересборки,
                                                   прогон «в будущем»                                — ops/run.sh, ops/publish.py
state_size.log                                     размер состояния по дням                           — indicators health
```

### 7.2. Канонический JSON и хэши

`json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, UTF-8; sha256 —
от этих байтов. Так считаются `Book.digest`, `Facts.digest`, `meta.payload_sha256` (без полей
П§0.4) и `release_sha` журнала (первые 12 hex хэша выпуска).

### 7.3. Время

Даты событий и периодов — календарные дни по Москве; моменты — ISO UTC с секундами. «Сегодня» —
только из модуля `datetime` (`date.today()`, `datetime.now()`), не из `time.time()` и не из
времени файлов: плагин `FAKE_TODAY` (`tests/fakedate_plugin.py`, `tests/fakedate_site/`)
подменяет их в тестах и подпроцессах. Функции, которым важно «сегодня», принимают `today:
date | None = None`.

---

## 8. Переменные окружения

Переменные ядра и индикаторов — **без префикса банка** (`BANK_*`, М§0.6, W1/C5): копия ядра под
другой банк их не меняет. Те же имена — в `ops/env.example`, `ops/run.sh`, юнитах и тестах. Переменные
только эксплуатации (ops) сохраняют префикс банка: слой ops банка свой, при переносе он
переименовывается вместе с юнитами (`docs/PORTING.md`).

| Переменная | Кто читает | Смысл |
|---|---|---|
| `BANK_STATE_DIR` | model (live, build_release), indicators, ops, tests/conftest.py | каталог состояния (§7.1); по умолчанию `var/state` |
| `BANK_DATA_REPO_DIR` | model (build_release: прошлый выпуск, история), ops/publish.py | клон ветки `release` репозитория данных; по умолчанию `$BANK_STATE_DIR/data-repo` |
| `BANK_WORKERS` | model/uncertainty.py | процессов полосы: `auto` (по умолчанию: ядра − 1, не больше 7) или число; `1` — без пула; на сервере — явно в env-файле после замера памяти и времени |
| `BANK_HANDOFF_DIR` | ops/tools/build_facts.py, indicators/seed.py, скрипты `data/assumptions/evidence/`, тесты с маркером `archive` | папка передачи (листы этапа 1, первичка); в CI и на сервере не задаётся |
| `TINVEST_TOKEN` | indicators/tinvest.py | токен T-Invest «только чтение»; только из окружения, в файлы и журналы не пишется; нет токена — источник `missing`, запасной ISS. **Токен получает только процесс сборщика T-Invest** (В10): `ops/run.sh` снимает его из окружения такта сразу после чтения env-файла и передаёт одной команде `python -m indicators.collect <режим> --only tinvest`; остальные сборщики (`--skip tinvest`), разбор новостей и PDF, нау-каст, тесты, сборка и публикация идут без токена. Юниты systemd env-файл не читают — строки `EnvironmentFile` в них нет, путь к файлу передаётся переменной `ENV_FILE`, а читает его `run.sh`: иначе токен лежал бы в окружении всего юнита (В10, В20). Это гигиена окружения, а не граница доверия: env-файл читаем пользователем такта (отдельного пользователя семейству не заводим) |
| `GITHUB_SHA` | model/payload.py (`engine_commit`) | коммит ядра в CI, когда git недоступен; иначе — нули |
| `FAKE_TODAY` | tests/fakedate_plugin.py | «сегодня» для прогона «в будущем» (`YYYY-MM-DD`) |
| `ENV_FILE`, `SBER_REPO_DIR`, `SBER_RELEASE_REMOTE`, `SBER_PUBLIC_URL`, `SBER_GIT_NAME`, `SBER_GIT_EMAIL`, `SBER_VENV`, `SBER_PYTHON`, `SBER_VERIFY_TIMEOUT`, `SBER_VERIFY_PAUSE`, `SBER_PUSH_BACKOFF`, `SBER_FUTURE_WEEKDAY`, `SBER_FUTURE_DAYS`, `SBER_SIMULATE_FAILURE` | ops | эксплуатация сервера (образец — `ops/env.example`, по схеме Ленты); значения — только в env-файле сервера |
| `SBER_REEXEC` | ops/run.sh | служебная метка перезапуска такта новым `run.sh` после обновления кода (ставит сам `run.sh`; не из env-файла) |
| `DATA_SCHEMA`, `DATA_PAGES_URL`, `DATA_RAW_URL` | functions/api/model.js (дверь данных Pages) | схема выпуска (`<банк>-v1`), основной (GitHub Pages) и запасной (raw) адрес `latest.json` ветки `release`; значения — `wrangler.toml [vars]`, в коде двери имени банка нет |

Других переменных код не читает; путь к чему-либо вне репозитория — только через них.

---

## 9. Тесты

* **Маркеры** (`pytest.ini`, ops): `docs` — читает документ `*.md`; `ci_only` — только CI (полный
  выпуск с полосой, статика витрины, инструменты оператора); `network` — ходит в сеть (в CI и
  на сервере пропускается); `archive` — нужна папка передачи (`BANK_HANDOFF_DIR`), в CI
  пропускается; `tact` — тест такта сервера (как у Ленты; `tact` и `ci_only` вместе запрещены);
  `deadline` — тест срока: падает, когда срок записи данных прошёл (объяснение сработавшего гейта
  действует на сегодня), — в набор сервера не входит. По умолчанию: `-m "not network and not archive"`.
* **Тесты такта обязательны каждому потоку** (W1/C1): метку `tact` получают быстрые тесты — core:
  аналитика и инварианты на фикстуре, литералы, `validate` на фикстуре, печать, квантиль, LHS;
  book: схема книги и тест данных «`valid_until` у `guidance_gap` не позже ближайшего МСФО
  календаря»; facts: схема и провенанс; indicators: литералы, User-Agent, журнал;
  web: статика без Node; control: независимость; reanchor: инвариант «механика ≈ 0» на фикстуре;
  ops: гигиена и тест «в такте есть тесты каждого потока». Набор сервера — выражение `TACT_TESTS` в
  `ops/run.sh` (`tact and not network and not archive and not docs and not ci_only and not
  deadline`) — цель не дольше 3 мин на сервере (`ops/budgets.json → tact_tests_target`, замером), в CI
  шаг «тесты такта» краснеет после 300 с (`tact_tests_ci_ceiling`: раннер медленнее сервера); `pytest -m
  tact` в командной строке шире: он заменяет `-m` из `pytest.ini` и берёт ещё тесты `docs` и `archive` с
  меткой `tact`.
* **Тесты не зависят от «сегодня»** (§0 п. 8). Набор сервера обязан быть зелёным при `FAKE_TODAY` =
  сегодня + 1, + 7, + 30 и + 60 дней (приёмка перед push и понедельничный прогон «в будущем», §6):
  тест, который пишет запись с моментом настенных часов, читает её на дату этого же момента, а не на
  замороженную дату. **Сроки данных в такт не входят** (В17): истёкшее объяснение сработавшего гейта
  останавливает сама сборка (код 1, М§14.2), об истекающем сообщает флаг `explanation_expiring`, об
  истёкшем сроке документа эмитента — флаг `policy_expired`; тест с меткой `deadline` идёт в CI и в
  ручном прогоне, но не в `TACT_TESTS`. Теста «срок документа эмитента не прошёл» нет: истёкший
  документ сборку не блокирует. **Тест такта не привязан и к горизонту календаря фактов:** он не
  ждёт, что «ближайшее событие календаря ещё впереди», — дату берёт от периодов книги и фактов или
  параметром.
* **Git в тестах — только в песочнице** (§0 п. 8): помощник `tests/support_git.py`, потолок
  `GIT_CEILING_DIRECTORIES` на весь прогон и замок каталога `--basetemp` — в `tests/conftest.py`
  (ops); сторож прямых вызовов `git` — тест ops.
* **Общие фикстуры** `tests/conftest.py` (ops): сторож такта, плагин `FAKE_TODAY`, `BANK_STATE_DIR`
  на `tmp_path` для каждого теста. Остальные фикстуры каждый поток держит у себя
  (`tests/support_<поток>.py`, `tests/fixtures/<поток>/`).
* **Книга и факты в тестах ядра:** `data/assumptions/assumptions.yaml` и `data/facts/*.json`, когда
  они есть; до этого — синтетическая книга и факты `tests/fixtures/core/` (формат §3). Тест,
  которому нужна настоящая книга, пропускается с причиной, пока её нет (`pytest.skip`), и
  перестаёт пропускаться сам.
* **Обязательные классы тестов** (каждый поток — в своих файлах): литералы в своём каталоге кода
  (core1 — `model/`, indicators — `indicators/`, web — `web/`, `functions/`); гигиена публичного
  репозитория (ops; без личных слов и их хэшей в самом тесте — список вне репозитория: при заданной
  папке передачи и отсутствии списка тест с маркером `archive` краснеет, а не пропускается; в
  отслеживаемых файлах нет маркеров сырых ответов T-Invest и табличных выписок с полями его ответа); перезаякоривание — клетка на своём
  пути сохраняет стоимость (reanchor, М§15.2); независимость контрольной модели от `model/` (control); аналитические тесты
  М§18 (core1, core2); контракт выпуска и размер, полнота `REQUIRED_FIELDS` по тексту П§2 (core2);
  схема и провенанс фактов (facts); воспроизводимость книги и копия миров бит в бит (book).
* **Node-проверки витрины** — `tests/web/*.mjs`, запускаются из `tests/test_web_*.py` (маркер
  `ci_only`, если нет `node`).

---

## 10. Витрина: фронт ничего не считает, кроме λ

* Все числа — из выпуска `/api/model` (контракт `sber-v1`). Единственный пересчёт — ползунок λ:
  центр прогона `низ_i + λ(верх_i − низ_i)` по `fair_value.headline.low_draws`/`high_draws`,
  квантиль тип 7, печать `roundHalfUp` к `fair_value.headline.print_step`; точка —
  `rates_view.low + λ(high − low)`. Прочее, что зависит от λ, — строки `by_lambda` (П§0.1).
* Разрешено: форматирование, доли → проценты, «значение ± ошибка», минимум–максимум, счётчики,
  сортировка, дни до даты от `meta.valuation_date`, возраст выпуска по часам зрителя.
* Витрина читает только блоки `REQUIRED_TOP_LEVEL` и поля П§2; подписи — из выпуска
  (`meta.company`, `title`/`name`, `capital.titles`); литералов эмитента в `web/app.js` нет, имя
  банка — только статическим текстом `index.html` и `404.html` (DASHBOARD §2); служебного текста на
  экране нет (П§0.2); мост упр. ↔ движок во фронте не применяется (поля `*_mgmt`, `cor_engine`).
* Новое число для экрана — сначала поле в PAYLOAD и `model/payload.py`, потом витрина (П§8).
* **Бюджет витрины** (В19): каталог `web/` — меньше 320 000 байт; потолки файлов подняты
  пропорционально: `app.js` — 258 000, `styles.css` — 45 000 (прочие файлы — до 9 000 каждый). Числа
  держит тест статики (`tests/test_web_static.py`: `FRONT_BUDGET`, `FILE_BUDGETS`), раскладка по
  экранам — DASHBOARD §1. Референс-класс режимов остаётся на витрине.

---

## 11. Параллельная сборка: зависимости потоков

| Поток | Ждёт | До готовности соседа работает на |
|---|---|---|
| core1 | книгу (`assumptions.yaml`), факты | синтетической книге и фактах `tests/fixtures/core/`; для чисел на черновике — сборка книги потока book, если она уже есть |
| core2 | core1 (типы §4.1–§4.4), `indicators/outputs.py` | коде core1; заглушке `IndicatorOutputs` в `tests/fixtures/core2/` |
| control | книгу, факты; ядро — только для сверки | черновике книги и листах этапа 1 (читает YAML/JSON сам, `model` не импортирует) |
| indicators | книгу (`meta.company`), `facts/peers.json`, `facts/bridge_ras_ifrs.json` | фикстурах `tests/fixtures/ind/`: T-Invest — синтетические, в формате ответа (сырые ответы T-Invest в публичное дерево не кладутся — условия API); новости — настоящая синтетика: выдуманные формулировки в формате источника (те же маркеры разметки и схема чисел), не сокращение и не пересказ настоящих статей и постов; формы ЦБ и ISS — образцы открытых данных |
| reanchor | ядро (`model/`), сборщик фактов с якорем из аргумента | синтетической книге и фактах `tests/fixtures/core/`; на настоящей книге — сухой прогон на ожидаемом отчёте |
| web | формат выпуска (PAYLOAD) | синтетическом выпуске `tests/fixtures/payload-sample.json`, валидном по PAYLOAD |
| ops | CLI §6, пути §7 | подставных командах в песочнице тестов (как у Ленты) |
| book, facts | — | листах этапа 1 и первичке |
