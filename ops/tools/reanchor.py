# -*- coding: utf-8 -*-
"""Перезаякоривание книги на отчёт квартала: кандидат книги и фактов, «что сдвинулось», разложение скачка.

ЗАЧЕМ ФУНКЦИЯ, А НЕ РУЧНАЯ ПРАВКА (М§15.2). Книга стоит на одном квартале; отчёт МСФО закрывает
её первый прогнозный квартал, и якорь переходит на него. Всё, что помнит прошлое или привязано к
якорю, обязано переехать вместе с ним: иначе оценка сдвигается без единой новости (урок 850oa:
ручная процедура дала сотни рублей «механики»). Инструмент делает перенос одной функцией, а тесты
`tests/test_reanchor*.py` держат инвариант «факт = ожидание ⇒ механика ≈ 0»: клетка,
перезаякоренная на своём пути, сохраняет стоимость. Порядок работы и формулы — docs/REANCHOR.md.

Как запускать (из корня репозитория):
  python -B ops/tools/reanchor.py --expected ФАЙЛ.json
      ожидаемый отчёт закрываемого квартала — «факты = ожидание модели» (сухой прогон; не
      шаблон для настоящих чисел: в нём нет ни одного факта)
  python -B ops/tools/reanchor.py --facts ФАЙЛ.json --out КАТАЛОГ
      [--valuation-date ГГГГ-ММ-ДД] [--version В] [--band ПРОГОНОВ] [--no-cells]
      [--transmission keep|resolve] [--compare-facts КАТАЛОГ] [--observation ФАЙЛ]
      [--book КНИГА] [--facts-dir КАТАЛОГ] [--template ШАБЛОН]
      кандидат книги и фактов на отчёте ФАЙЛ и разложение скачка на дате оценки
      (по умолчанию — `published` отчёта, иначе день после конца квартала)
Файл настоящего отчёта пишет сборщик фактов из листов нового квартала:
  python -B ops/tools/build_facts.py --anchor КВАРТАЛ --out КАТАЛОГ --report ФАЙЛ.json

Что пишет — ТОЛЬКО в --out (по умолчанию var/reanchor/<квартал>/). Внутри репозитория каталог
кандидата — только под var/ (в git оттуда попадает один .gitkeep): любой другой каталог рабочей
копии (data/, model/, docs/, tests/, ops/, web/…) — отказ; вне репозитория — куда угодно.
В режиме `resolve` — только expected.json и REANCHOR.md.
  assumptions.yaml, assumptions.json   машинная книга-кандидат (без комментариев)
  assumptions_template.yaml            шаблон книги с теми же правками (комментарии сохранены)
  facts/*.json                         рабочие факты нового якоря из файла отчёта (узлы с пометкой
                                       `calc: reanchor…`; канон фактов с источниками и сверками
                                       собирает `build_facts.py`, сверка — `--compare-facts`)
  expected.json                        ожидаемый отчёт того же квартала
  observation.json                     подмена наблюдения с причиной — только при --observation
  REANCHOR.md                          производные величины, «что сдвинулось», разложение скачка
                                       точки и медианы (в квартале шока — ещё строка «обучение»
                                       отчёта без шока: ожидаемый отчёт там — среднее двугорбого
                                       прогноза, М§12), проверки ядра на кандидате (инварианты и
                                       сработавшие гейты), предупреждения и ручные шаги

ФАЙЛ ОТЧЁТА КВАРТАЛА (JSON; млрд ₽, доли единицы; расходы — со знаком минус, как в отчётности;
ОПУ и проценты — за КВАРТАЛ, не нарастающим итогом). Незнакомый ключ — отказ.
  schema       "quarter-report/1" (необязательно)
  period       закрываемый квартал — РОВНО первый прогнозный квартал книги
  as_of, published, source, note      конец квартала (сверка), дата публикации (дата оценки
               кандидата), источник и примечание — необязательно
  balance      остатки на конец квартала (МСФО группы):
               books {книга ЧПД: остаток} — все книги `nii.books` (кредиты — валовые, кредиты по СС —
               внутри своей книги); loans_fvtpl; fvoci, fvtpl_bonds (долговые бумаги FVOCI с репо и
               облигации по СС через ОПУ); allowance (резерв по кредитам АС, величиной);
               other_assets, other_liabilities (без дивидендов к выплате); dividends_payable и
               dividends_payable_year (год прибыли; обязателен при сумме > 0); bv_common (капитал
               обыкновенных акционеров); fvoci_reserve (после налога); at1, nci — если изменились
  interest     {книга: проценты квартала по книге, величиной}; dia — взносы АСВ квартала (величиной)
  pnl          nii, fees_net, llp_debt_fa, insurance_net, noncore_net, opex, pbt, tax, ni,
               ni_shareholders; необязательно — misc_net (иначе остаток до pbt), oci_fvoci,
               other_equity_movements
  capital      n20_0 и n20_pre_dividend (норматив до вычета объявленного дивиденда из
               регуляторного капитала?), n1_1_bank, basel_cet1, basel_rwa, bank_base_capital
               (форма 123); необязательно — t2
  market       ofz_curve {1, 3, 5, 10} — бескупонная кривая на отчётную дату; key_avg — средняя
               ключевая квартала; price_index — индекс цен квартала (конец к началу), необязательно:
               без него — ожидаемый индекс миров книги
  mgmt         nim, cor — управленческие ЧПМ и стоимость риска квартала; cir — необязательно
  shares       issued_total, outstanding_total, outstanding_ordinary, outstanding_preferred — если
               изменились

ПОДМЕНА НАБЛЮДЕНИЯ (--observation ФАЙЛ, YAML или JSON) — решение книги с записанной причиной, когда
опубликованная упр. метрика квартала не годится наблюдением режима как есть (docs/REANCHOR.md §3.1):
  cor, se_cor, nim, se_nim   значение и se каждой величины, доли единицы; все четыре обязательны;
                             null — величина не наблюдается (её se — null или 0)
  reason                     причина — обязательна; печатается в REANCHOR.md, идёт в журнал версии
  note                       подпись для витрины: причина коротко, словами для владельца (одна строка
                             не длиннее 80 знаков); обязательна, если подмена меняет опубликованное
                             число отчёта (или не берёт его — null)
  period                     необязательно; если задан — квартал отчёта
Подменяется только наблюдение A-P2u кандидата на отчёте (строка «сюрприз факта»); факты кандидата —
числа отчёта. Запись наблюдения несёт пометку подмены: `published` — опубликованные числа отчёта у
подменённых величин, `reason` — подпись `note` (М§12); витрина печатает их рядом с наблюдением. Без
подмены наблюдение — упр. ЧПМ и CoR отчёта с se 0.

ЧТО ПЕРЕНОСИТСЯ:
  meta         anchor_period, first_period, facts_date, valuation_date, version
  наблюдение   закрытый квартал → `joint.regime_update.observations` с замороженными ожиданиями
               режимов прежней книги (mu_cor, mu_nim); нау-каст того же квартала заменяется;
               наблюдения старше окна удаляются; μ оставшихся записей не пересчитываются — ни при
               следующих переносах, ни при смене года якоря (М§12); апостериорные в книгу не
               вписываются
  цены якоря   `noncore.result_real`, `other.misc_net_real` и их оси × индекс цен квартала (у года
               якоря непрофильного индексируется только остаток года)
  anchor-ключи отношения, которые клетка держит постоянными, выводятся из новых фактов сами;
               линия доли текущих счетов (c_ref, key_ref) и базовое оптовое фондирование
               (`volumes.wholesale_to_funds`) замораживаются числами прежней книги — их состояние
               идёт с лагом, и вывод заново сдвинул бы само правило
  капитал      плотности RWA × общий множитель к RWA якоря; вычеты `deductions_anchor` — формулой
               книги на новых фактах; `gap_pp` — так, чтобы якорь воспроизводил Н20.0 и Н1.1
  передача     режим `keep` (единственный в процедуре, М§15.2 п. 8): скаляры пути σ0_A, σ0_L, φ_A,
               φ_L прежней книги сохраняются, а цели A-N2, A-N3 и доли `nii.sigma0_split`,
               `nii.phi_split` пересчитываются на структуру нового якоря. `--transmission resolve`
               — только диагностика: цели прежние, ядро решает скаляры заново (скачок без новости
               целиком в строке «механика»); кандидат книги и фактов при этом не пишется
  ставки, ЧПМ  остаток процентного дохода (ближний сдвиг модели, прочие проценты отчёта) разнесён
               по книгам активов пропорционально их доходу — ставки якоря несут весь ЧПД; ключи
               спредов от ставок якоря выводятся заново; `regimes.near_nim_shift` — так, чтобы
               ожидаемый путь ЧПМ новой книги совпал с путём прежней на ожидаемом отчёте
  смена года   кризисные ключи сдвигаются вместе с годом шока; `noncore.price_base_year`
  справочное   `valuation.next_report.period`

Коды возврата: 0 — сделано; 1 — отказ (отчёт не того квартала, негодные входы, запись внутри
репозитория вне var/, механика вне допуска) — причина в тексте.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.book import (BOOK_BALANCE_FIELDS, BOOK_PATH, FACTS_DIR, FVOCI_FIELDS,  # noqa: E402
                        FVTPL_BOND_FIELD, FV_LOANS_FIELD, LIQUIDITY, SECURITIES, Book, BookError,
                        Facts, FactsError, anchor_facts, book_from_dict, book_roles,
                        canonical_json, load_book, load_facts)
from model.book_schema import REASON_MAX  # noqa: E402
from model.capital import rwa_anchor, rwa_weights  # noqa: E402
from model.checks import check_gates, check_invariants  # noqa: E402
from model.credit import bridge_from_facts  # noqa: E402
from model.grid import (GridRun, LiveInputs, live_from_book, make_context,  # noqa: E402
                        regime_expectations, run_grid)
from model.nii import solve_transmission  # noqa: E402
from model.paths import Trajectory  # noqa: E402
from model.timeline import (parse_period, period_str, quarter_days, quarter_end,  # noqa: E402
                            shift_quarter, to_date)
from model.worlds import BASE_WORLD  # noqa: E402

SCHEMA = "quarter-report/1"
TEMPLATE = BOOK_PATH.parent / "assumptions_template.yaml"
DEFAULT_OUT = ROOT / "var" / "reanchor"
WRITABLE = "var"                             # единственный каталог рабочей копии, куда инструмент пишет
OBS = "joint.regime_update.observations"
ANALYTICAL, NEUTRAL = "analytical", "macro_neutral"

TOP_REQUIRED = ("period", "balance", "interest", "dia", "pnl", "capital", "market", "mgmt")
TOP_OPTIONAL = ("schema", "as_of", "published", "source", "note", "shares")
SECTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "balance": (("books", "loans_fvtpl", "fvoci", "fvtpl_bonds", "allowance", "other_assets",
                 "other_liabilities", "dividends_payable", "bv_common", "fvoci_reserve"),
                ("dividends_payable_year", "at1", "nci")),
    "pnl": (("nii", "fees_net", "llp_debt_fa", "insurance_net", "noncore_net", "opex", "pbt", "tax",
             "ni", "ni_shareholders"),
            ("misc_net", "oci_fvoci", "other_equity_movements")),
    "capital": (("n20_0", "n20_pre_dividend", "n1_1_bank", "basel_cet1", "basel_rwa",
                 "bank_base_capital"), ("t2",)),
    "market": (("ofz_curve", "key_avg"), ("price_index",)),
    "mgmt": (("nim", "cor"), ("cir",)),
    "shares": ((), ("issued_total", "issued_ordinary", "issued_preferred", "outstanding_total",
                    "outstanding_ordinary", "outstanding_preferred")),
}
NOT_NUMBERS = ("books", "ofz_curve", "n20_pre_dividend", "dividends_payable_year")
CURVE_NODES = ("1", "3", "5", "10")
PNL_SUM = ("nii", "llp_debt_fa", "fees_net", "insurance_net", "noncore_net", "opex")   # + misc_net = pbt

# Допуски СВЕРОК файла отчёта и точности переноса — параметры проверки, не допущения модели.
BALANCE_CHECK = 5.0        # млрд ₽: активы против пассивов и капитала (округление строк отчёта)
PNL_CHECK = 0.5            # млрд ₽: pbt против суммы строк, ni против pbt + tax
RESIDUAL_SHARE = 0.10      # остаток «ЧПД − проценты книг» не больше этой доли ЧПД
BASIS_SANITY = 0.01        # упр. ЧПМ и CoR против величин движка через мост: 1 п.п.
MECHANICS_TOL = 0.001      # механика: стоимость клетки на своём пути — в пределах 0,1 % (М§15.2)
GAP_TOL = 1e-9             # якорь воспроизводит норматив отчёта с этой точностью
NEAR_TOL = 1e-6            # 0,0001 п.п. ЧПМ: хвост ближней калибровки меньше — не пишется
NEAR_RANGE = 2e-4          # разброс внутри года больше 0,02 п.п. — год пишется кварталами
SPREAD_RULE_TOL = 6e-6     # ключ спреда «от ставки якоря» в книге округлён до 5 знаков
SOLVE_STEP = 0.001         # шаг пробы целей A-N2 и A-N3 (решение аффинно — шаг не важен)
PASSES = 6                 # не больше стольких проходов калибровки по прогону кандидата
# Подмена наблюдения закрытого квартала (решение книги, docs/REANCHOR.md §3.1): пределы ловят проценты
# вместо долей, а не судят о величине.
OBS_FIELDS = ("cor", "se_cor", "nim", "se_nim")            # значение и se каждой величины — все четыре обязательны
OBS_KEYS = OBS_FIELDS + ("reason", "note", "period")        # reason обязателен, note — подпись витрины, period — квартал отчёта
MARK_KEYS = ("published", "reason")                        # пометка подмены в записи наблюдения книги (М§12)
SAME_AS_REPORT = 5e-7                                      # подмена с числом отчёта до 0,00005 п.п. — не подмена
OBS_LIMITS = {"cor": (-0.05, 0.20), "nim": (0.0, 0.20)}     # открытые границы значения, доли единицы
SE_LIMIT = 0.02                                             # se — доля единицы не больше 2 п.п. (0,12 п.п. — 0,0012)

# Реальные суммы книги в ценах якоря: индекс цен клетки стартует с единицы на якоре (М§4.7).
PRICE_INDEXED = ("noncore.result_real", "other.misc_net_real")
NONCORE = "noncore.result_real"
# anchor-ключи, состояние которых идёт с лагом: вывод заново сдвинул бы само правило.
FROZEN_ANCHORS = ("nii.retail_current_share.c_ref", "nii.retail_current_share.key_ref",
                  "volumes.wholesale_to_funds")
DEDUCTIONS = ("capital.n20.deductions_anchor", "capital.n11.deductions_anchor")
GAPS = (("capital.n20.gap_pp", "n20"), ("capital.n11.gap_pp", "n11"))
DENSITY = "capital.rwa.density"
FX = "capital.rwa.fx"
NIM_TARGET, T_TARGET = "nii.nim_lt_target_mgmt", "nii.transmission.target"
SPLIT, PHI_SPLIT = "nii.sigma0_split", "nii.phi_split"
TRANSMISSION_KEYS = (NIM_TARGET, T_TARGET, SPLIT, PHI_SPLIT)
SCALARS = ("σ0_A", "σ0_L", "φ_A", "φ_L")      # скаляры пути передачи ставки (М§4.5) — порядок `_sigmas`
KEEP, RESOLVE = "keep", "resolve"            # resolve — только диагностика (М§15.2 п. 8)
TRANSMISSION_MODES = (KEEP, RESOLVE)
NEAR = "regimes.near_nim_shift"
CRISIS_TRAJECTORIES = ("cor", "nim_shift", "loan_growth_adj")

REASONS = (
    ("meta.", "якорь переходит на закрытый квартал"),
    (OBS, "факт квартала — наблюдение A-P2u с замороженными ожиданиями режимов; окно"),
    ("noncore.result_real", "цены якоря: индекс цен закрытого квартала"),
    ("other.misc_net_real", "цены якоря: индекс цен закрытого квартала"),
    ("noncore.price_base_year", "год цен — год якоря"),
    ("nii.retail_current_share.", "линия доли текущих счетов заморожена числами прежней книги"),
    ("volumes.wholesale_to_funds", "базовое оптовое фондирование заморожено числом прежней книги"),
    (DENSITY, "плотности RWA — общим множителем к RWA якоря"),
    (FX, "валютная доля RWA — на новом якоре (индекс курса стартует с единицы)"),
    ("capital.n20.deductions_anchor", "вычеты капитала — формулой книги на фактах якоря"),
    ("capital.n11.deductions_anchor", "вычеты капитала — формулой книги на фактах якоря"),
    ("capital.n20.gap_pp", "якорь воспроизводит Н20.0 отчёта"),
    ("capital.n11.gap_pp", "якорь воспроизводит Н1.1 банка"),
    ("nii.books.", "ключи спреда от ставок якоря (М§4.4)"),
    (NEAR, "ближняя калибровка ЧПМ: уровень уже в ставках якоря"),
    (NIM_TARGET, "A-N2 на структуре нового якоря при прежних σ0_A и σ0_L"),
    (T_TARGET, "A-N3 на структуре нового якоря при прежних φ_A и φ_L"),
    (SPLIT, "доля сдвига σ0 на активах на структуре нового якоря при прежних σ0_A и σ0_L"),
    (PHI_SPLIT, "доля сжатия φ на активах на структуре нового якоря при прежних φ_A и φ_L"),
    ("nii.sigma0_from", "год начала σ0 — не раньше первого прогнозного года"),
    ("regimes.", "кризис: ключи сдвинуты вместе с годом шока"),
    ("checks.m_crisis_vs_cbr", "кризис: ключи сдвинуты вместе с годом шока"),
    ("valuation.next_report.period", "новый открытый квартал"),
    ("valuation.", "ось — в единицах параметра: пересчитана вместе с ним"),
)

Path_ = tuple                                # звенья пути: ключ словаря или ("name", имя) у элемента списка


class ReportError(ValueError):
    """Отчёт квартала или входы не годятся для перезаякоривания — объяснение в тексте."""


@dataclass
class Reanchored:
    """Кандидат и всё, что о нём надо знать до переноса в канон."""

    data: dict                               # кандидат машинной книги (anchor-ключи — строкой)
    book: Book
    facts: Facts
    period: str
    derived: dict
    changes: list[tuple[Path_, Any, Any]]    # (путь звеньями, было, стало); печать — `dotted(путь)`
    warnings: list[str] = field(default_factory=list)
    near: Any = None                         # ближняя калибровка ЧПМ (траектория книги)


# ------------------------------------------------------------------ периоды и пути


def next_period(p: str) -> str:
    return period_str(*shift_quarter(*parse_period(p), 1))


def period_end(p: str) -> dt.date:
    return quarter_end(*parse_period(p))


def pindex(p: str) -> int:
    y, q = parse_period(p)
    return 4 * y + q


def _get(tree: Any, dotted_path: str, default: Any = None) -> Any:
    node = tree
    for part in dotted_path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def _set(tree: dict, dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _r(x: float, digits: int = 9) -> float:
    return round(float(x), digits)


def _rounded(tree: Any, digits: int = 9) -> Any:
    if isinstance(tree, Mapping):
        return {k: _rounded(v, digits) for k, v in tree.items()}
    if isinstance(tree, float):
        return round(tree, digits)
    return tree


def _plain(tree: Any) -> Any:
    """Дерево после JSON туда-обратно: ключи — строки, даты — строки."""
    return json.loads(json.dumps(tree, default=str))


def _sorted_traj(traj: Mapping) -> dict:
    """Ключи траектории по времени, `LT` и `LT_from` — в конце (как в книге)."""
    head = sorted((k for k in traj if str(k) not in ("LT", "LT_from")), key=lambda k: (int(str(k)[:4]), str(k)))
    return {**{k: traj[k] for k in head}, **{k: traj[k] for k in ("LT", "LT_from") if k in traj}}


# ------------------------------------------------------------------ файл отчёта


def read_report(path: Path) -> dict:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportError(f"файл отчёта {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise ReportError(f"файл отчёта {path}: ожидается объект JSON")
    return report


def _fraction(value: float, where: str, lo: float = -0.5, hi: float = 1.0) -> None:
    if not lo < value < hi:
        raise ReportError(f"отчёт: {where} = {value} — ожидается доля единицы (0,062 — это 6,2 %), "
                          "а не проценты")


def _check_shape(book: Book, facts: Facts, report: Mapping) -> None:
    unknown = sorted(set(report) - set(TOP_REQUIRED) - set(TOP_OPTIONAL))
    if unknown:
        raise ReportError(f"отчёт: незнакомые ключи {', '.join(unknown)} "
                          f"(известны: {', '.join(TOP_REQUIRED + TOP_OPTIONAL)})")
    missing = [k for k in TOP_REQUIRED if k not in report]
    if missing:
        raise ReportError(f"отчёт: нет обязательных ключей {', '.join(missing)}")
    if report.get("schema", SCHEMA) != SCHEMA:
        raise ReportError(f"отчёт: schema {report['schema']!r}, ожидается {SCHEMA!r}")
    period, first = str(report["period"]), str(book.get("meta.first_period"))
    try:
        parse_period(period)
    except BookError as exc:
        raise ReportError(f"отчёт: period — {exc}") from exc
    if period != first:
        raise ReportError(
            f"отчёт за {period}, а первый прогнозный квартал книги — {first}. Перезаякоривание "
            f"идёт по одному кварталу: сначала отчёт за {first}, потом следующий")
    if pindex(period) >= pindex(str(book.get("meta.last_period"))):
        raise ReportError(f"квартал {period} — последний прогнозный книги: горизонт кончился "
                          "(продление meta.last_period — решение книги)")
    if str(facts.plain("anchor", "period")) != str(book.get("meta.anchor_period")):
        raise ReportError(f"факты на якоре {facts.plain('anchor', 'period')}, книга — на "
                          f"{book.get('meta.anchor_period')}: книга и факты не пара")
    end = period_end(period)
    if "as_of" in report and str(report["as_of"]) != end.isoformat():
        raise ReportError(f"отчёт: as_of {report['as_of']} — не конец квартала {period} ({end})")
    if "published" in report:
        try:
            published = to_date(report["published"])
        except ValueError as exc:
            raise ReportError(f"отчёт: published {report['published']!r} — не дата") from exc
        if published <= end:
            raise ReportError(f"отчёт: published {published} не позже конца квартала {end}")
    for name, (required, optional) in SECTIONS.items():
        if name not in report:
            continue
        section = report[name]
        if not isinstance(section, Mapping):
            raise ReportError(f"отчёт: {name} — словарь")
        unknown = sorted(set(section) - set(required) - set(optional))
        if unknown:
            raise ReportError(f"отчёт: {name} — незнакомые ключи {', '.join(unknown)} "
                              f"(известны: {', '.join(required + optional)})")
        missing = [k for k in required if k not in section]
        if missing:
            raise ReportError(f"отчёт: {name} — нет ключей {', '.join(missing)}")
        for key, value in section.items():
            if key not in NOT_NUMBERS and not _num(value):
                raise ReportError(f"отчёт: {name}.{key} = {value!r} — ожидается число")
    names = tuple(book.get("nii.books"))
    for where, block in (("balance.books", report["balance"]["books"]), ("interest", report["interest"])):
        if not isinstance(block, Mapping) or set(block) != set(names):
            raise ReportError(f"отчёт: {where} — словарь по книгам {', '.join(names)} (все и только они)")
        for b, v in block.items():
            if not _num(v) or v < 0:
                raise ReportError(f"отчёт: {where}.{b} = {v!r} — ожидается неотрицательное число")
    if not _num(report["dia"]):
        raise ReportError(f"отчёт: dia = {report['dia']!r} — ожидается число")


def check_report(book: Book, facts: Facts, report: Mapping) -> None:
    """Громкий отказ на всём, что ядро прочло бы молча не так."""
    _check_shape(book, facts, report)
    B, P, C, M, G = (report[k] for k in ("balance", "pnl", "capital", "market", "mgmt"))
    if not isinstance(C["n20_pre_dividend"], bool):
        raise ReportError("отчёт: capital.n20_pre_dividend — true или false (норматив до вычета "
                          "объявленного дивиденда из регуляторного капитала?)")
    curve = M["ofz_curve"]
    if not isinstance(curve, Mapping) or set(map(str, curve)) != set(CURVE_NODES):
        raise ReportError(f"отчёт: market.ofz_curve — узлы {', '.join(CURVE_NODES)} лет")
    for node, v in curve.items():
        if not _num(v):
            raise ReportError(f"отчёт: market.ofz_curve.{node} = {v!r} — ожидается число")
        _fraction(float(v), f"market.ofz_curve.{node}", 0.0)
    for where, value in (("market.key_avg", M["key_avg"]), ("mgmt.nim", G["nim"]), ("mgmt.cor", G["cor"]),
                         ("capital.n20_0", C["n20_0"]), ("capital.n1_1_bank", C["n1_1_bank"])):
        _fraction(float(value), where)
    if "cir" in G:
        _fraction(float(G["cir"]), "mgmt.cir", 0.0)
    if "price_index" in M and not 0.8 < float(M["price_index"]) < 1.5:
        raise ReportError(f"отчёт: market.price_index = {M['price_index']} — индекс цен квартала "
                          "(конец к началу, около 1,015), а не темп")
    if B["dividends_payable"] < 0 or B["allowance"] < 0:
        raise ReportError("отчёт: balance.dividends_payable и balance.allowance — величинами (≥ 0)")
    year = B.get("dividends_payable_year")
    if B["dividends_payable"] > 0 and (isinstance(year, bool) or not isinstance(year, int)):
        raise ReportError("отчёт: balance.dividends_payable > 0 — нужен dividends_payable_year "
                          "(год прибыли объявленного дивиденда)")
    if P["opex"] >= 0:
        raise ReportError(f"отчёт: pnl.opex = {P['opex']} — расходы со знаком минус, как в отчётности")
    for key in ("basel_cet1", "basel_rwa", "bank_base_capital"):
        if C[key] <= 0:
            raise ReportError(f"отчёт: capital.{key} = {C[key]} — ожидается положительное число")
    # тождества
    books = book.get("nii.books")
    roles = book_roles(books)
    af = anchor_facts(facts, books)
    bal = B["books"]
    at1, nci = float(B.get("at1", af.at1)), float(B.get("nci", af.nci))
    assets = sum(bal[b] for b in roles.assets) - B["allowance"] + B["other_assets"]
    claims = (sum(bal[b] for b in roles.liabilities) + B["other_liabilities"] + B["dividends_payable"]
              + B["bv_common"] + at1 + nci)
    if abs(assets - claims) > BALANCE_CHECK:
        raise ReportError(
            f"отчёт: баланс не сходится — активы {assets:.1f} (книги активов − резерв + прочие активы) "
            f"против пассивов и капитала {claims:.1f} (пассивы + прочие + дивиденды к выплате + "
            f"капитал + AT1 + неконтролирующие): разница {assets - claims:+.1f} млрд ₽ "
            f"(допуск {BALANCE_CHECK})")
    if B["loans_fvtpl"] < 0 or (af.fv_book and B["loans_fvtpl"] > bal[af.fv_book]):
        raise ReportError(f"отчёт: balance.loans_fvtpl — кредиты по СС внутри своей книги ({af.fv_book}), "
                          "не больше её остатка")
    if B["fvoci"] < 0 or B["fvtpl_bonds"] < 0 or B["fvoci"] + B["fvtpl_bonds"] > bal[SECURITIES] + BALANCE_CHECK:
        raise ReportError("отчёт: balance.fvoci и balance.fvtpl_bonds — части книги долговых бумаг "
                          "(неотрицательные, в сумме не больше её остатка)")
    lines = sum(P[k] for k in PNL_SUM)
    if "misc_net" in P and abs(lines + P["misc_net"] - P["pbt"]) > PNL_CHECK:
        raise ReportError(
            f"отчёт: pbt {P['pbt']} ≠ сумме строк {lines + P['misc_net']:.2f} (nii + llp_debt_fa + "
            "fees_net + insurance_net + noncore_net + opex + misc_net) — расходы со знаком минус?")
    if abs(P["pbt"] + P["tax"] - P["ni"]) > PNL_CHECK:
        raise ReportError(f"отчёт: ni {P['ni']} ≠ pbt + tax = {P['pbt'] + P['tax']:.2f} "
                          "(налог — со знаком минус)")
    income = sum(report["interest"][b] for b in roles.assets)
    booked = income - sum(report["interest"][b] for b in roles.liabilities) - abs(float(report["dia"]))
    if not income or abs(P["nii"] - booked) > RESIDUAL_SHARE * abs(P["nii"]):
        raise ReportError(
            f"отчёт: проценты книг дают {booked:.1f}, а pnl.nii — {P['nii']}: остаток "
            f"{P['nii'] - booked:+.1f} больше {RESIDUAL_SHARE:.0%} ЧПД — проценты за квартал или "
            "нарастающим итогом? все ли книги?")
    # базис: величины движка из строк отчёта против управленческих через мост
    bridge = bridge_from_facts(facts)
    eng = engine_ratios(book, facts, report)
    for name, got, mgmt, back in (("ЧПМ", eng["nim"], G["nim"], bridge.to_mgmt_nim),
                                  ("CoR", eng["cor"], G["cor"], bridge.to_mgmt_cor)):
        if abs(back(got) - mgmt) > BASIS_SANITY:
            raise ReportError(
                f"отчёт: {name} движка из строк отчёта {got:.4%} через мост — {back(got):.4%} упр., "
                f"а mgmt — {mgmt:.4%}: расхождение больше {BASIS_SANITY * 100:.0f} п.п. — ОПУ за "
                "квартал или нарастающим итогом? доля или проценты?")


def engine_ratios(book: Book, facts: Facts, report: Mapping) -> dict[str, float]:
    """ЧПМ, CoR и CIR движка закрытого квартала из строк отчёта (М§0.3, §4.6; средние остатки —
    начало и конец квартала, как в фактах)."""
    books = book.get("nii.books")
    roles = book_roles(books)
    af = anchor_facts(facts, books)
    B, P = report["balance"], report["pnl"]
    days = quarter_days(*parse_period(str(report["period"])))
    bal = B["books"]
    iea_avg = sum(af.balances[b] + bal[b] for b in roles.assets) / 2
    loans_avg = (sum(af.balances[b] + bal[b] for b in roles.loans) - af.fv_loans - B["loans_fvtpl"]) / 2
    misc = P["misc_net"] if "misc_net" in P else P["pbt"] - sum(P[k] for k in PNL_SUM)
    income = P["nii"] + P["fees_net"] + P["insurance_net"] + misc
    return {"nim": P["nii"] / iea_avg * 365 / days, "cor": -P["llp_debt_fa"] / loans_avg * 365 / days,
            "cir": -P["opex"] / income if income else float("nan"),
            "iea_avg": iea_avg, "loans_ac_avg": loans_avg, "misc_net": misc, "days": days}


# ------------------------------------------------------------------ факты нового якоря


def _node(value: Any, calc: str) -> dict:
    return {"v": value, "calc": calc}


def _put(tree: dict, dotted_path: str, value: Any, calc: str, touched: set[str]) -> None:
    _set(tree, dotted_path, _node(value, calc))
    touched.add(dotted_path)


def _spread_total(tree: dict, fields: Sequence[str], total: float, calc: str, touched: set[str]) -> None:
    """`total` по узлам `fields` — в пропорции прежнего якоря (`null` остаётся `null`)."""
    old = {f: _get(tree, f + ".v") for f in fields}
    live = [f for f in fields if old[f] is not None]
    base = sum(float(old[f]) for f in live)
    for f in fields:
        touched.add(f)
        if old[f] is not None:
            share = float(old[f]) / base if base else 1.0 / len(live)
            _set(tree, f, _node(_r(total * share, 6), calc + " (разбивка — в пропорции прежнего якоря)"))


def _null_untouched(tree: Any, touched: set[str], keep: Sequence[str], calc: str, prefix: str = "") -> None:
    """Узлы, которых отчёт не назвал, — `null`: прежнее число под новой датой было бы ложью."""
    if not isinstance(tree, dict):
        return
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if any(path == k or path.startswith(k + ".") for k in keep):
            continue
        if isinstance(value, dict) and "v" in value and ("src" in value or "calc" in value):
            if path not in touched:
                tree[key] = _node(None, calc)
        else:
            _null_untouched(value, touched, keep, calc, path)


def _add_period_row(holder: dict, key: str, period: str, row: dict) -> None:
    """Строка периода в словарь {период: строка} или список [{period, …}] — как лежит в файле."""
    rows = holder.get(key)
    if isinstance(rows, dict):
        rows[period] = row
    elif isinstance(rows, list):
        rows[:] = [r for r in rows if not (isinstance(r, dict) and r.get("period") == period)]
        rows.append({"period": period, **row})


def facts_after(book: Book, facts: Facts, report: Mapping, *, update_bridge: bool = True) -> Facts:
    """Факты нового якоря из отчёта квартала: те же файлы, узлы якоря — из отчёта.

    Ставки якоря несут весь ЧПД: остаток `nii − (проценты книг − взносы АСВ)` разнесён по
    книгам активов пропорционально их процентному доходу (М§15.2 п. 4). Упр. факты квартала
    входят в историю моста упр. ↔ МСФО (отчётные кварталы года якоря ядро берёт оттуда, М§4.6);
    среднее моста продлевается закрытым кварталом при `update_bridge`."""
    period = str(report["period"])
    end = period_end(period)
    tag = f"reanchor: отчёт {period}" + (f" ({report['source']})" if report.get("source") else "")
    absent = f"reanchor: в отчёте {period} нет — пересобрать build_facts.py --anchor {period}"
    books = book.get("nii.books")
    roles = book_roles(books)
    old = anchor_facts(facts, books)
    old_period = str(facts.plain("anchor", "period"))
    files = copy.deepcopy(dict(facts.files))
    B, P, C, M, G = (report[k] for k in ("balance", "pnl", "capital", "market", "mgmt"))
    eng = engine_ratios(book, facts, report)
    days = int(eng["days"])
    at1, nci = float(B.get("at1", old.at1)), float(B.get("nci", old.nci))

    files["anchor"]["as_of"] = end.isoformat()
    files["anchor"]["period"] = period

    # --- баланс
    bal = files["balance"]
    touched: set[str] = set()
    bal["as_of"] = end.isoformat()
    direct = bal.get("books") if isinstance(bal.get("books"), dict) else None
    for name in roles.names:
        total = float(B["books"][name])
        if direct is not None and name in direct:
            _put(bal, f"books.{name}", total, tag, touched)
            continue
        fields = list(BOOK_BALANCE_FIELDS[name])
        if FV_LOANS_FIELD in fields:
            _put(bal, FV_LOANS_FIELD, float(B["loans_fvtpl"]), tag, touched)
            fields.remove(FV_LOANS_FIELD)
            total -= float(B["loans_fvtpl"])
        if name == SECURITIES:
            _spread_total(bal, list(FVOCI_FIELDS), float(B["fvoci"]), tag, touched)
            _put(bal, FVTPL_BOND_FIELD, float(B["fvtpl_bonds"]), tag, touched)
            fields = [f for f in fields if f not in FVOCI_FIELDS and f != FVTPL_BOND_FIELD]
            total -= float(B["fvoci"]) + float(B["fvtpl_bonds"])
        _spread_total(bal, fields, total, tag, touched)
    sign = -1.0 if (_get(bal, "allowance_ac.v") or 0.0) < 0 else 1.0     # знак — как в прежнем файле
    _put(bal, "allowance_ac", sign * float(B["allowance"]), tag, touched)
    for key, name in (("other_assets", "other_assets"), ("other_liabilities", "other_liabilities"),
                      ("dividends_payable", "dividends_payable"), ("equity.bv_common", "bv_common")):
        _put(bal, key, float(B[name]), tag, touched)
    _put(bal, "equity.at1", at1, tag, touched)
    _put(bal, "equity.nci", nci, tag, touched)
    if B.get("dividends_payable_year") is not None:
        bal["dividends_payable_year"] = int(B["dividends_payable_year"])
    loans = sum(float(B["books"][b]) for b in roles.loans)
    iea = sum(float(B["books"][b]) for b in roles.assets)
    assets = iea - float(B["allowance"]) + float(B["other_assets"])
    totals = {"equity.total": B["bv_common"] + at1 + nci, "equity.shareholders": B["bv_common"] + at1,
              "equity.fvoci_reserve": B["fvoci_reserve"], "loans_ac_gross.total": loans - B["loans_fvtpl"],
              "securities.debt_total": B["books"][SECURITIES], "liquidity.total": B["books"][LIQUIDITY],
              "total_assets": assets, "total_liabilities": assets - (B["bv_common"] + at1 + nci), "iea": iea}
    for key, value in totals.items():
        if isinstance(_get(bal, key), dict):
            _put(bal, key, _r(value, 6), tag, touched)
    if parse_period(period)[0] != parse_period(old_period)[0] and isinstance(bal.get("prev_year_end"), dict):
        prev = bal["prev_year_end"]            # новый год якоря: концы прошлого года — прежний якорь
        if "as_of" in prev:
            prev["as_of"] = period_end(old_period).isoformat()
        for seg in ("corporate", "retail"):
            total = sum(old.balances[b] for b in roles.loans if books[b]["cor_segment"] == seg)
            prev[f"loans_{seg}"] = _node(_r(total, 6), f"reanchor: книги сегмента на конец {old_period}")
    _null_untouched(bal, touched, ("history", "prev_year_end"), absent)
    rows = bal.get("history")
    listed = set(rows) if isinstance(rows, dict) else {r.get("period") for r in rows or [] if isinstance(r, dict)}
    if rows is not None and old_period not in listed:
        # конец прежнего якоря уходит в историю: по концам отчётных кварталов ядро взвешивает год якоря (М§4.6)
        was = f"reanchor: состояние прежнего якоря {old_period}"
        _add_period_row(bal, "history", old_period, {
            "date": period_end(old_period).isoformat(),
            "loans_ac_gross": _node(_r(sum(old.balances[b] for b in roles.loans) - old.fv_loans, 6), was),
            "bv_common": _node(float(old.bv), was),
            "iea": _node(_r(sum(old.balances[b] for b in roles.assets), 6), was)})
    _add_period_row(bal, "history", period, {
        "date": end.isoformat(), "loans_ac_gross": _node(_r(loans - B["loans_fvtpl"], 6), tag),
        "allowance_ac": _node(float(B["allowance"]), tag), "loans_fvtpl": _node(float(B["loans_fvtpl"]), tag),
        "bv_common": _node(float(B["bv_common"]), tag), "iea": _node(_r(iea, 6), tag),
        "total_assets": _node(_r(assets, 6), tag)})

    # --- книги ЧПД: ставки якоря несут весь ЧПД
    nb = files["nii_books"]
    nb["as_of"] = end.isoformat()
    if "period" in nb:
        nb["period"] = period
    touched = set()
    interest = {b: float(report["interest"][b]) for b in roles.names}
    income = sum(interest[b] for b in roles.assets)
    dia = abs(float(report["dia"]))
    residual = float(P["nii"]) - (income - sum(interest[b] for b in roles.liabilities) - dia)
    scale = 1.0 + residual / income
    prefix = "books." if isinstance(nb.get("books"), dict) else ""
    for b in roles.names:
        avg = (old.balances[b] + float(B["books"][b])) / 2
        carried = interest[b] * (scale if b in roles.assets else 1.0)
        entry = _get(nb, f"{prefix}{b}") or {}
        for key, value in (("balance_open", old.balances[b]), ("balance_close", float(B["books"][b])),
                           ("balance_avg", avg), ("interest_q", interest[b])):
            if key in entry:
                _put(nb, f"{prefix}{b}.{key}", _r(value, 6), tag, touched)
        _put(nb, f"{prefix}{b}.rate_anchor", _r(carried / avg * 365 / days) if avg else 0.0,
             tag + ": проценты книги" + (" с долей остатка ЧПД" if b in roles.assets else "")
             + f" / средний остаток × 365/{days}", touched)
    retail = sum(float(B["books"][b]) for b in roles.retail)
    for key, value in (("dia_q", dia), ("nim_eng_q", eng["nim"]), ("key_avg_anchor_q", float(M["key_avg"])),
                       ("current_share_anchor", float(B["books"][roles.retail[0]]) / retail)):
        _put(nb, key, _r(value), tag, touched)
    for key, value in (("nii_q", float(P["nii"])), ("iea_avg", eng["iea_avg"]), ("days", days),
                       ("other_interest_net_q", residual),
                       ("interest_loans_q", sum(interest[b] for b in roles.loans))):
        if isinstance(nb.get(key), dict):
            _put(nb, key, value if isinstance(value, int) else _r(value, 6), tag, touched)
    _null_untouched(nb, touched, (), absent)

    # --- ОПУ квартала
    pnl = files["pnl_quarterly"]
    pnl["as_of"] = end.isoformat()
    rows = pnl.get("quarters")
    last = (rows.get(old_period) if isinstance(rows, dict)
            else next((r for r in rows or [] if isinstance(r, dict) and r.get("period") == old_period), None)) or {}
    row = {k: _node(None, absent) for k, v in last.items() if isinstance(v, dict) and "v" in v}
    values = {k: float(P[k]) for k in SECTIONS["pnl"][0]}
    values.update(misc_net=eng["misc_net"], key_avg=float(M["key_avg"]), dia=-dia)
    for key in ("oci_fvoci", "other_equity_movements"):
        if key in P:
            values[key] = float(P[key])
    for key, value in values.items():
        row[key] = _node(_r(value), tag)
    _add_period_row(pnl, "quarters", period, row)

    # --- капитал и нормативы
    cap = files["capital"]
    cap["as_of"] = end.isoformat()
    touched = set()
    _put(cap, "basel.cet1", float(C["basel_cet1"]), tag, touched)
    _put(cap, "basel.rwa", float(C["basel_rwa"]), tag, touched)
    if isinstance(_get(cap, "basel.cet1_ratio"), dict):
        _put(cap, "basel.cet1_ratio", _r(C["basel_cet1"] / C["basel_rwa"], 6), tag, touched)
    _put(cap, "n20_0.value", float(C["n20_0"]), tag, touched)
    cap["n20_0"]["pre_dividend"] = bool(C["n20_pre_dividend"])
    cap["n20_0"].pop("pre_dividend_note", None)
    _put(cap, "n1_1_bank.value", float(C["n1_1_bank"]), tag, touched)
    _put(cap, "bank_base_capital", float(C["bank_base_capital"]), tag, touched)
    _put(cap, "fvoci_reserve", float(B["fvoci_reserve"]), tag, touched)
    if "t2" in C:
        _put(cap, "t2_recognized", float(C["t2"]), tag, touched)
    for node in CURVE_NODES:
        _put(cap, f"ofz_curve_anchor.{node}", float(M["ofz_curve"][node]), tag, touched)
    for block, day in (("basel", end), ("n20_0", end), ("ofz_curve_anchor", end),
                       ("n1_1_bank", end + dt.timedelta(days=1))):       # форма банка — на 1-е число
        if "as_of" in cap.get(block, {}):
            cap[block]["as_of"] = day.isoformat()
    _null_untouched(cap, touched, ("at1", "history", "t2_recognized"), absent)
    _add_period_row(cap, "history", period, {
        "date": end.isoformat(), "n20_0": _node(float(C["n20_0"]), tag),
        "cet1": _node(float(C["basel_cet1"]), tag), "rwa": _node(float(C["basel_rwa"]), tag),
        "n1_1_bank": _node(float(C["n1_1_bank"]), tag)})

    # --- акции
    if "shares" in report:
        files["shares"]["as_of"] = end.isoformat()
        for key, value in report["shares"].items():
            files["shares"][key] = _node(float(value), tag)

    # --- мост упр. ↔ МСФО: упр. факт закрытого квартала — в историю всегда (год якоря в упр. базисе
    #     собирается из отчётных кварталов, М§4.6); среднее по окну продлевается при `update_bridge`
    br = files["bridge_mgmt_ifrs"]
    br["as_of"] = end.isoformat()
    window = br.get("window")
    in_window = isinstance(window, list) and len(window) == 2
    count0 = pindex(old_period) - pindex(str(window[0])) + 1 if in_window else 0
    for x in ("cor", "nim", "cir"):
        if x not in G:
            continue
        block = br[x]
        additive = block["method"] == "additive"
        gap = eng[x] - float(G[x]) if additive else eng[x] / float(G[x])
        count = int(_get(block, "n.v") or count0)
        if update_bridge and count:
            value = (float(block["value"]["v"]) * count + gap) / (count + 1)
            block["value"] = _node(_r(value), f"reanchor: среднее по окну с {period} (n = {count + 1})")
            if isinstance(block.get("n"), dict):
                block["n"] = _node(count + 1, "кварталов в окне")
            if isinstance(block.get("sd"), dict):
                block["sd"] = _node(None, absent)
        if isinstance(block.get("history"), list):
            _add_period_row(block, "history", period, {
                "mgmt": _node(float(G[x]), tag), "engine": _node(_r(eng[x]), tag),
                "gap": _node(_r(gap), "engine − mgmt" if additive else "engine / mgmt")})
    if update_bridge and in_window:
        br["window"] = [window[0], period]

    # --- упр. метрики квартала
    mq = files.get("mgmt_quarterly")
    if isinstance(mq, dict) and isinstance(mq.get("quarters"), (dict, list)):
        mq["as_of"] = end.isoformat()
        _add_period_row(mq, "quarters", period, {k: _node(float(v), tag) for k, v in G.items()})

    return Facts(root=Path(f"reanchor-{period}"), files=files,
                 digest=hashlib.sha256(canonical_json(files)).hexdigest())


# ------------------------------------------------------------------ шаги переноса книги


def _scale_traj(traj: Any, factor: float) -> Any:
    if _num(traj):
        return _r(traj * factor, 6)
    return {k: (v if str(k) == "LT_from" else _r(float(v) * factor, 6)) for k, v in traj.items()}


def _explicit_from(traj: Mapping, year: int) -> dict:
    """Та же траектория с явными ключами лет от `year` до начала LT-уровня: правка ключа года
    не должна сдвигать соседние интерполированные годы."""
    t = Trajectory(traj)
    stop = max([int(k) for k in map(str, traj) if k.isdigit()] + [year])
    if "LT_from" in traj and "LT" in traj:
        stop = max(stop, int(traj["LT_from"]) - 1)
    out = dict(traj)
    for y in range(year, stop + 1):
        out.setdefault(str(y), _r(t.year_value(y)))
    return _sorted_traj(out)


def _axis_lists(X: dict) -> list[tuple[str, list, tuple[str, ...]]]:
    V = X["valuation"]
    return [("valuation.uncertainty.axes", V["uncertainty"].get("axes") or [], ("low", "high")),
            ("valuation.uncertainty.off_band_axes", V["uncertainty"].get("off_band_axes") or [], ("low", "high")),
            ("valuation.reverse_dcf.axes", V["reverse_dcf"].get("axes") or [], ("search", "range"))]


def _value_axes(X: dict, path: str) -> list[tuple[dict, tuple[str, ...]]]:
    """Оси вида value по одному параметру `path` (полоса, оси вне полосы, обратный расчёт)."""
    return [(axis, keys) for _, axes, keys in _axis_lists(X) for axis in axes
            if axis.get("kind") == "value" and [str(p) for p in axis.get("paths") or []] == [path]]


def _under(path: str, roots: Sequence[str]) -> bool:
    return any(path == r or path.startswith(r + ".") for r in roots)


def _move_axis(axis: dict, keys: Sequence[str], move) -> None:
    for key in keys:
        value = axis.get(key)
        if _num(value):
            axis[key] = move(value)
        elif isinstance(value, list) and value and all(_num(v) for v in value):
            axis[key] = [move(v) for v in value]


def _scale_axes(X: dict, roots: Sequence[str], factor: float) -> None:
    """Оси по параметрам `roots` — тем же множителем, что и сами параметры: диапазон суждения
    задан в единицах параметра. Ось, которая двигает их вместе с другими, одним множителем не
    перевести — отказ."""
    for where, axes, keys in _axis_lists(X):
        for axis in axes:
            paths = [str(p) for p in axis.get("paths") or []]
            hit = [p for p in paths if _under(p, roots)]
            if not hit:
                continue
            if len(hit) != len(paths):
                raise ReportError(
                    f"{where} «{axis.get('name')}»: ось двигает {', '.join(paths)} — пересчитываемые "
                    f"({', '.join(hit)}) вместе с другими; разделить ось (решение книги)")
            _move_axis(axis, keys, lambda v: _r(v * factor, 6))


def _remap_axes(X: dict, path: str, was: float, now: float, slope: float = 1.0,
                bounds: tuple[float, float] | None = None) -> None:
    """Ось вида value по одному параметру — тем же пересчётом, что и параметр: значение оси v
    переходит в `now + slope × (v − was)` (сдвиг при slope = 1). `bounds` — область определения:
    конец оси, стоящий на её границе, остаётся на границе, прочие из неё не выходят."""
    def move(v: float) -> float:
        if bounds is not None and v in bounds:
            return v
        v = _r(now + slope * (v - was))
        return v if bounds is None else min(bounds[1], max(bounds[0], v))
    for axis, keys in _value_axes(X, path):
        _move_axis(axis, keys, move)


def reindex_prices(X: dict, facts: Facts, period: str, factor: float) -> None:
    """Реальные суммы книги — из цен прежнего якоря в цены нового (М§15.2 п. 2).

    Сумма непрофильного результата года якоря закрывается остатком к факту (М§0.3): индексируется
    только остаток года, `S' = F + (S − F) × индекс`, F — факт отчётных кварталов прежней книги."""
    year = parse_period(period)[0]
    reported = 0.0
    for per, row in facts.periods("pnl_quarterly").items():
        if parse_period(per)[0] == year and pindex(per) < pindex(period):
            value = _get(row, "noncore_net.v")
            if value is None:
                raise ReportError(f"факты: pnl_quarterly.{per}.noncore_net — null, а год якоря "
                                  "непрофильного результата закрывается остатком к факту (М§0.3)")
            reported += float(value)
    for path in PRICE_INDEXED:
        traj = _get(X, path)
        if path == NONCORE and reported:
            if not isinstance(traj, Mapping):
                raise ReportError(f"{NONCORE} — скаляр, а год якоря закрывается остатком к факту: "
                                  "нужна траектория с ключом года якоря (решение книги)")
            traj = _explicit_from(traj, year)
            scaled = _scale_traj(traj, factor)
            scaled[str(year)] = _r(reported + (float(traj[str(year)]) - reported) * factor, 6)
            _set(X, path, scaled)
        else:
            _set(X, path, _scale_traj(traj, factor))
    _scale_axes(X, PRICE_INDEXED, factor)


def freeze_anchors(X: dict, book: Book) -> None:
    for path in FROZEN_ANCHORS:
        if _get(X, path) == "anchor":
            _set(X, path, _r(book.anchors[path]))


def carry_fx(X: dict, factor: float) -> None:
    """Валютная доля RWA на новом якоре: индекс курса стартует с единицы (М§4.11), поэтому доля
    пересчитывается на достигнутый курс — тогда путь RWA тот же."""
    if not _get(X, f"{FX}.enabled"):
        return
    share, abroad = float(_get(X, f"{FX}.share")), float(_get(X, f"{FX}.foreign_inflation"))
    rate = factor / (1.0 + abroad) ** 0.25
    _set(X, f"{FX}.share", _r(share * rate / (1.0 - share + share * rate)))


def spread_rule(data: Mapping, rates: Mapping[str, float], name: str) -> tuple[int, float] | None:
    """Ключ спреда года первого прогнозного квартала по правилу «от ставки якоря» (М§4.4):
    ставка якоря − β × опора мира-эталона в первом прогнозном квартале; `None` — у книги такого
    ключа нет."""
    spec = data["nii"]["books"][name]
    spread = spec["spread"]
    year, quarter = parse_period(str(data["meta"]["first_period"]))
    if not isinstance(spread, Mapping) or str(year) not in spread:
        return None
    world = data["worlds"][data["nii"]["transmission"]["reference_world"]]
    ref = Trajectory(world["key_rate" if spec["ref"] == "key" else spec["ref"]]).value(year, quarter)
    beta = float(spec["beta"]) if spec["side"] == "liability" else 1.0
    return year, rates[name] - beta * ref


def rederive_spreads(X: dict, old: Book, old_rates: Mapping[str, float], rates: Mapping[str, float]) -> list[str]:
    """Ключи спредов от ставок якоря — заново, тем же правилом, что в прежней книге (М§4.4).

    Ключ правится, только если прежняя книга правилу следовала; ключ следующего года остаётся
    серединой до калибровки, если был ею."""
    moved = []
    for name, spec in X["nii"]["books"].items():
        was = spread_rule(old.source, old_rates, name)
        now = spread_rule(X, rates, name)
        if was is None or now is None:
            continue
        old_spread = old.source["nii"]["books"][name]["spread"]
        y_old, rule_old = was
        if abs(float(old_spread[str(y_old)]) - rule_old) > SPREAD_RULE_TOL:
            continue
        year, rule = now
        spread = dict(spec["spread"])
        if abs(rule - float(spread[str(year)])) <= SPREAD_RULE_TOL:
            continue                             # ставка якоря та же — ключ книги остаётся как есть
        old_t = Trajectory(old_spread)
        middle = (str(y_old + 1) in old_spread and abs(
            float(old_spread[str(y_old + 1)])
            - (float(old_spread[str(y_old)]) + old_t.year_value(y_old + 2)) / 2) <= SPREAD_RULE_TOL)
        spread[str(year)] = _r(rule, 6)
        if middle:
            spread[str(year + 1)] = _r((spread[str(year)] + old_t.year_value(year + 2)) / 2, 6)
        spec["spread"] = _sorted_traj(spread)
        moved.append(name)
    return moved


def _shift_key(key: Any, years: int) -> str:
    key = str(key)
    return key if key in ("LT", "LT_from") else f"{int(key[:4]) + years}{key[4:]}"


def _shift_traj(traj: Any, years: int, last_year: int, where: str) -> Any:
    if not isinstance(traj, Mapping):
        return traj
    out = {}
    for k, v in traj.items():
        if str(k) == "LT_from":
            if int(v) + years > last_year:
                raise ReportError(f"{where}.LT_from {v} + {years} — позже года last_period {last_year}: "
                                  "кризисная траектория не помещается в горизонт (решение книги)")
            out[k] = int(v) + years
        else:
            out[_shift_key(k, years)] = v
    return out


def shift_crisis(X: dict, years: int) -> None:
    """Смена года якоря: год шока сдвигается вместе с ним, ключи кризисных траекторий — на
    столько же лет (М§15.2 п. 6). `shock_year_offset` сохраняется."""
    if not years:
        return
    last_year = parse_period(str(X["meta"]["last_period"]))[0]
    renamed: dict[str, str] = {}
    for r in X["regimes"]["ids"]:
        spec = X["regimes"][r]
        if "shock_year_offset" not in spec:
            continue
        for name in CRISIS_TRAJECTORIES + ("loan_growth_override",):
            if isinstance(spec.get(name), Mapping):
                for k in spec[name]:
                    renamed[f"regimes.{r}.{name}.{k}"] = f"regimes.{r}.{name}.{_shift_key(k, years)}"
                spec[name] = _shift_traj(spec[name], years, last_year, f"regimes.{r}.{name}")
        if isinstance(spec.get("one_off_loss"), Mapping):
            y, q = parse_period(str(spec["one_off_loss"]["period"]))
            spec["one_off_loss"] = {**spec["one_off_loss"], "period": period_str(y + years, q)}
    for _, axes, _keys in _axis_lists(X):
        for axis in axes:
            axis["paths"] = [renamed.get(str(p), p) for p in axis.get("paths") or []]
    table = _get(X, "checks.m_crisis_vs_cbr.loan_growth")
    if isinstance(table, Mapping):
        X["checks"]["m_crisis_vs_cbr"]["loan_growth"] = {_shift_key(k, years): v for k, v in table.items()}


def near_trajectory(old: Any, periods: Sequence[str], values: Sequence[float], last_year: int) -> dict:
    """Ближняя калибровка ЧПМ траекторией книги: кварталы — по год после якоря включительно и
    дальше, пока значения внутри года заметно расходятся; затем — ключи лет со средним года,
    пока хвост заметен (в последнем году сетки — только ключ года, М§0.4: там остаток уровня в
    ставках и калибровка гасят друг друга, и терминал читает LT-уровни); `LT` — прежний."""
    lt = float(old.get("LT", 0.0)) if isinstance(old, Mapping) else float(old)
    by_year: dict[int, list[tuple[int, float]]] = {}
    for p, v in zip(periods, values):
        y, q = parse_period(p)
        by_year.setdefault(y, []).append((q, v))
    detail = parse_period(periods[0])[0] + 1
    for y in sorted(by_year):
        vals = [v for _, v in by_year[y]]
        if y == detail + 1 and max(vals) - min(vals) > NEAR_RANGE:
            detail = y
    detail = min(detail, last_year - 1)
    out: dict[str, float] = {}
    for y in sorted(by_year):
        if y > detail:
            break
        for q, v in by_year[y]:
            out[period_str(y, q)] = _r(v, 7)
        if len(by_year[y]) == 4:
            out[str(y)] = sum(out[period_str(y, q)] for q in (1, 2, 3, 4)) / 4
    means = {y: sum(v for _, v in qs) / len(qs) for y, qs in by_year.items() if detail < y <= last_year}
    tail = [y for y in means if abs(means[y] - lt) >= NEAR_TOL]
    for y in sorted(means):
        if tail and y <= max(tail):
            out[str(y)] = _r(means[y], 7)
    out["LT"] = lt
    return out


def _sigmas(tr: Any) -> tuple[float, float, float, float]:
    """Скаляры пути передачи ставки (М§4.5) — σ0_A, σ0_L, φ_A, φ_L: всё, что клетка несёт из решения
    `solve_transmission` в ставки своих кварталов и терминала."""
    return float(tr.sigma0), float(tr.sigma0_liab), float(tr.phi_assets), float(tr.phi_liab)


def keep_transmission(X: dict, new_facts: Facts, old: GridRun) -> dict[str, tuple[float, float, float]]:
    """Режим `keep` (М§15.2 п. 8). Цели A-N2, A-N3 и доли `nii.sigma0_split`, `nii.phi_split` — числа
    «на структуре якоря» (М§4.5): при переносе якоря они пересчитываются на новую структуру так,
    чтобы скаляры пути σ0_A, σ0_L, φ_A, φ_L остались прежними.

    Иначе ядро решило бы те же цели на сдвинувшихся весах книг и сдвинуло бы спреды всего пути
    и терминала — без единой новости. Решение — по самой функции ядра `solve_transmission`, без
    её внутренностей: σ0_A и σ0_L аффинны по цели A-N2, объём сжатия φ — по цели A-N3, а
    `φ_A = p × φ`, `φ_L = (1 − p) × φ × A/L` (A/L — отношение весов книг с `phi` на структуре
    якоря; читается из наклонов φ_L и φ по цели). Оси целей пересчитываются тем же правилом —
    концу оси отвечают те же скаляры, что в прежней книге; оси долей сдвигаются вместе с центром
    (конец на границе 0 или 1 остаётся на ней). Возвращает {ключ: (было, стало, наклон пересчёта оси)}."""
    bridge = bridge_from_facts(new_facts)
    t0, star0 = float(_get(X, NIM_TARGET)), float(_get(X, T_TARGET))
    s0, p0 = float(_get(X, SPLIT)), float(_get(X, PHI_SPLIT))

    def solve(target: float, t_star: float, split: float, phi_split: float) -> Any:
        Y = copy.deepcopy(X)
        for path, value in zip(TRANSMISSION_KEYS, (target, t_star, split, phi_split)):
            _set(Y, path, value)
        return solve_transmission(book_from_dict(Y, facts=new_facts), new_facts, bridge)

    def solve_old(path: str) -> Any:                     # прежняя структура, цель сдвинута на шаг
        probe = old.ctx.book.with_overrides({path: float(old.ctx.book.get(path)) + SOLVE_STEP})
        return solve_transmission(probe, old.ctx.facts, old.ctx.bridge)

    was = old.ctx.transmission
    want_a, want_l, want_pa, want_pl = _sigmas(was)

    # --- σ0_A, σ0_L: цель A-N2 и доля сдвига
    a0, l0, _, _ = _sigmas(solve(t0, star0, s0, p0))
    a1, l1, _, _ = _sigmas(solve(t0 + SOLVE_STEP, star0, s0, p0))
    da, dl = (a1 - a0) / SOLVE_STEP, (l1 - l0) / SOLVE_STEP
    if (s0 > 0 and not da) or (s0 < 1 and not dl):
        raise ReportError("передача ставки: σ0 не зависит от цели A-N2 — нет книг с lt_shift?")
    old_a, old_l, _, _ = _sigmas(solve_old(NIM_TARGET))
    da_old, dl_old = (old_a - want_a) / SOLVE_STEP, (old_l - want_l) / SOLVE_STEP
    if s0 >= 1.0:                                        # весь сдвиг — на активах
        target, split, slope = t0 + (want_a - a0) / da, s0, da_old / da
    elif s0 <= 0.0:                                      # весь сдвиг — на пассивах
        target, split, slope = t0 + (want_l - l0) / dl, s0, dl_old / dl
    else:
        root = t0 - a0 / da                              # цель, при которой сдвиг нулевой
        g1, h1 = da / s0, dl / (1.0 - s0)
        target = root + want_a / g1 + want_l / h1
        split = want_a / (g1 * (target - root)) if target != root else s0
        slope = da_old / g1 + dl_old / h1
    target, split = _r(target, 6), _r(min(1.0, max(0.0, split)), 6)

    # --- φ_A, φ_L: цель A-N3 и доля сжатия
    tr0, tr1 = solve(target, star0, split, p0), solve(target, star0 + SOLVE_STEP, split, p0)
    dphi = float(tr1.phi) - float(tr0.phi)
    if not dphi:
        raise ReportError("передача ставки: φ не зависит от цели A-N3 — нет книг с phi?")
    if p0 < 1.0:
        ratio = (float(tr1.phi_liab) - float(tr0.phi_liab)) / ((1.0 - p0) * dphi)    # A/L новой структуры
        if not ratio:
            raise ReportError("передача ставки: φ_L не зависит от цели A-N3 — нет пассивов с phi?")
        want_phi = want_pa + want_pl / ratio
    else:                                                # всё сжатие — в спредах кредитных книг
        want_phi = want_pa
    phi_split = want_pa / want_phi if 0.0 < p0 < 1.0 and want_phi else p0
    phi_split = _r(min(1.0, max(0.0, phi_split)), 6)
    t_star = _r(star0 + (want_phi - float(tr0.phi)) * SOLVE_STEP / dphi, 6)
    slope_star = (float(solve_old(T_TARGET).phi) - float(was.phi)) / dphi
    if float(was.phi):                                   # конец оси: обе части сжатия — в той же пропорции
        slope_star *= want_phi / float(was.phi)

    out = {}
    for path, before, now, k, bounds in ((NIM_TARGET, t0, target, slope, None),
                                         (T_TARGET, star0, t_star, slope_star, None),
                                         (SPLIT, s0, split, 1.0, (0.0, 1.0)),
                                         (PHI_SPLIT, p0, phi_split, 1.0, (0.0, 1.0))):
        _set(X, path, now)
        _remap_axes(X, path, before, now, k, bounds)
        out[path] = (before, now, k)
    return out


# ------------------------------------------------------------------ наблюдение A-P2u


def _drop_observation(book: Book, period: str) -> Book:
    obs = [o for o in book.get(OBS) or [] if str(o["period"]) != period]
    if len(obs) == len(book.get(OBS) or []):
        return book
    return book.with_overrides({OBS: obs})


def expected_observation(book: Book, facts: Facts, live: LiveInputs | None = None, *,
                         without: Sequence[str] = ()) -> dict:
    """Наблюдение первого прогнозного квартала, равное ожиданию модели (М§13): прогноз фильтра
    A-P2u в упр. базисе; нау-каст этого квартала из книги снимается. `without` — режимы, которые в
    ожидание не входят (отчёт «без шока»: ожидание прочих режимов с их вероятностями, в сумме 1)."""
    from model.nextreport import model_expectation
    period = str(book.get("meta.first_period"))
    exp = model_expectation(_drop_observation(book, period), facts, period, live=live)
    cor, nim = exp["cor_q_mgmt"], exp["nim_q_mgmt"]
    if without:
        rows = [r for r in exp["by_regime"] if r["regime"] not in without]
        total = sum(r["posterior"] for r in rows)
        if not total:
            raise ReportError(f"ожидание без режимов {', '.join(without)}: прочих режимов с вероятностью нет")
        cor = sum(r["posterior"] * r["cor_q_mgmt"] for r in rows) / total
        nim = sum(r["posterior"] * r["nim_q_mgmt"] for r in rows) / total
    return {"period": period, "cor": _r(cor, 12), "nim": _r(nim, 12), "se_cor": 0.0, "se_nim": 0.0,
            "basis": "mgmt"}


def shock_regimes(run: GridRun) -> list[str]:
    """Режимы, у которых первый прогнозный квартал книги лежит в году шока (М§3.2): в этом квартале
    прогноз наблюдения двугорбый — шок либо случился, либо нет."""
    year = run.ctx.timeline.year(1)
    return [r for r, rp in run.ctx.prep.regimes.items() if rp.shock_year is not None and rp.shock_year == year]


def with_observation(book: Book, obs: Mapping) -> Book:
    """Прежняя книга с наблюдением её первого прогнозного квартала (A⁺, М§15.2). Пометка подмены
    (`published`, `reason`) — только у записи кандидата: в прежней книге квартал прогнозный."""
    base = _drop_observation(book, str(obs["period"]))
    plain = {k: v for k, v in obs.items() if k not in MARK_KEYS}
    return base.with_overrides({OBS: list(base.get(OBS) or []) + [plain]})


def _same_observation(a: Mapping, b: Mapping, tol: float = 1e-9) -> bool:
    """Два наблюдения одного квартала совпадают по значениям и se (null — только с null; se null = 0)."""
    for k in OBS_FIELDS:
        x, y = a.get(k), b.get(k)
        if k.startswith("se_"):
            x, y = x or 0.0, y or 0.0
        if (x is None) != (y is None) or (x is not None and abs(float(x) - float(y)) > tol):
            return False
    return True


def frozen_expectations(book: Book, facts: Facts) -> dict[str, dict[str, float]]:
    """Ожидания режимов прежней книги на её первый прогнозный квартал, упр. базис (М§12)."""
    period = str(book.get("meta.first_period"))
    ctx = make_context(_drop_observation(book, period), facts)
    q = ctx.timeline.index(period)
    mu = ctx.expectations if ctx.expectations and int(ctx.expectations.get("until", 0)) >= q \
        else regime_expectations(ctx, q)
    br = ctx.bridge
    return {"mu_cor": {r: _r(br.to_mgmt_cor(mu["cor"][r][q]), 12) for r in book.get("regimes.ids")},
            "mu_nim": {r: _r(br.to_mgmt_nim(mu["nim"][r][q]), 12) for r in book.get("regimes.ids")}}


def carry_observation(X: dict, book: Book, facts: Facts, obs: Mapping) -> dict:
    """Факт квартала считается один раз: наблюдение с замороженными μ (М§15.2 п. 1)."""
    period = str(obs["period"])
    ru = X["joint"]["regime_update"]
    kept = [o for o in ru.get("observations") or [] if str(o["period"]) != period]
    record = {**{k: obs[k] for k in ("period", "cor", "nim", "se_cor", "se_nim", "basis")},
              **frozen_expectations(book, facts)}
    for x in ("cor", "nim"):
        if record[x] is None:
            record.pop(f"mu_{x}")
    if obs.get("published"):                     # пометка подмены (М§12): опубликованные числа и подпись
        record.update(published=dict(obs["published"]), reason=obs["reason"])
    kept.append(record)
    kept.sort(key=lambda o: pindex(str(o["period"])))
    window = ru.get("window_obs")
    ru["observations"] = kept[-int(window):] if window else kept
    return record


def check_observation(override: Mapping, period: str, report_mgmt: Mapping | None = None) -> dict:
    """Подмена наблюдения A-P2u закрытого квартала — решение книги с записанной причиной (docs/REANCHOR.md
    §3.1): значение и se каждой величины (`cor`, `se_cor`, `nim`, `se_nim` — все четыре обязательны; null —
    величина не наблюдается, её se — null или 0) и `reason`; `period`, если задан, — квартал отчёта. Факты
    подмена не трогает: она идёт только в наблюдение кандидата на отчёте. С упр. числами отчёта
    (`report_mgmt`) — ещё `published`: опубликованные числа величин, которые подмена меняет или не берёт;
    тогда обязательна подпись для витрины `note` (одна строка не длиннее 80 знаков). Возвращает запись
    наблюдения (упр. базис) с причиной; негодная подмена — отказ с причиной."""
    if not isinstance(override, Mapping):
        raise ReportError("подмена наблюдения: ожидается словарь {cor, se_cor, nim, se_nim, reason}")
    unknown = sorted(str(k) for k in set(override) - set(OBS_KEYS))
    if unknown:
        raise ReportError(f"подмена наблюдения: незнакомые ключи {', '.join(unknown)} (известны "
                          f"{', '.join(OBS_KEYS)})")
    missing = [k for k in OBS_FIELDS if k not in override]
    if missing:
        raise ReportError(f"подмена наблюдения: нет {', '.join(missing)} — значение и se каждой величины "
                          "называются явно (null — величина не наблюдается)")
    why = override.get("reason")
    if not isinstance(why, str) or not why.strip():
        raise ReportError("подмена наблюдения: нет причины (reason) — без записанной причины подмена не "
                          "принимается")
    if "period" in override and str(override["period"]) != period:
        raise ReportError(f"подмена наблюдения на {override['period']}, а отчёт — за {period}")
    record: dict[str, Any] = {"period": period}
    for x in ("cor", "nim"):
        value, se = override[x], override[f"se_{x}"]
        if value is None:
            if se is not None and not (_num(se) and float(se) == 0.0):
                raise ReportError(f"подмена наблюдения: {x} = null (не наблюдается), а se_{x} = {se!r} — "
                                  "должно быть null или 0")
            record[x], record[f"se_{x}"] = None, None
            continue
        lo, hi = OBS_LIMITS[x]
        if not _num(value) or not lo < float(value) < hi:
            raise ReportError(f"подмена наблюдения: {x} = {value!r} — ожидается доля единицы в ({lo:g}; {hi:g}) "
                              "(1,3 % — это 0,013), а не проценты")
        if not _num(se) or not 0.0 <= float(se) <= SE_LIMIT:
            raise ReportError(f"подмена наблюдения: se_{x} = {se!r} — ожидается число от 0 до {SE_LIMIT:g} "
                              "(доля единицы: 0,12 п.п. — это 0,0012)")
        record[x], record[f"se_{x}"] = float(value), float(se)
    if record["cor"] is None and record["nim"] is None:
        raise ReportError("подмена наблюдения: обе величины null — наблюдения нет, подменять нечего")
    record["basis"] = "mgmt"
    record["reason"] = " ".join(why.split())
    note = override.get("note")
    if note is not None:
        if not isinstance(note, str) or not note.strip():
            raise ReportError("подмена наблюдения: подпись для витрины (note) пуста — уберите ключ или напишите причину")
        note = " ".join(note.split())
        if len(note) > REASON_MAX:
            raise ReportError(f"подмена наблюдения: подпись для витрины (note) — {len(note)} знаков, не длиннее "
                              f"{REASON_MAX}: её печатает витрина рядом с наблюдением")
        record["note"] = note
    if report_mgmt is not None:
        published = {x: float(report_mgmt[x]) for x in ("cor", "nim") if _num(report_mgmt.get(x))
                     and (record[x] is None or abs(record[x] - float(report_mgmt[x])) > SAME_AS_REPORT)}
        if published and "note" not in record:
            raise ReportError("подмена наблюдения меняет опубликованное число отчёта ("
                              + ", ".join({"cor": "CoR", "nim": "ЧПМ"}[x] for x in published)
                              + f") — нужна подпись для витрины note: причина коротко, словами для владельца, не "
                              f"длиннее {REASON_MAX} знаков (витрина печатает её рядом с опубликованным числом)")
        if published:
            record["published"] = published
    return record


def read_observation(path: Path) -> dict:
    """Файл подмены наблюдения (YAML или JSON) → словарь; проверка — `check_observation`."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReportError(f"файл подмены наблюдения {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportError(f"файл подмены наблюдения {path}: ожидается словарь {{cor, se_cor, nim, se_nim, reason}}")
    return data


# ------------------------------------------------------------------ перезаякоривание


def valuation_day(valuation_date: Any, report: Mapping) -> dt.date:
    """Дата оценки кандидата: заданная, иначе `published` отчёта, иначе день после конца квартала."""
    end = period_end(str(report["period"]))
    try:
        day = to_date(valuation_date or report.get("published") or (end + dt.timedelta(days=1)))
    except ValueError as exc:
        raise ReportError(f"дата оценки {valuation_date!r} — не дата ГГГГ-ММ-ДД") from exc
    if day < end:
        raise ReportError(f"дата оценки {day} раньше конца квартала {end}")
    return day


def _weights(run: GridRun, cell: Sequence[str] | None) -> dict[tuple[str, str, str], float]:
    if cell is not None:
        return {tuple(cell): 1.0}
    return dict(run.layers[ANALYTICAL].prob)


def _mean(run: GridRun, weights: Mapping, name: str, q: int) -> float:
    return sum(w * float(run.cell(*key).quarters[name][q]) for key, w in weights.items() if w)


def _anchor_row(run: GridRun, weights: Mapping, name: str) -> float:
    """Норматив якоря модели: строка q = 0 зависит только от сценария капитала."""
    by_scenario: dict[str, float] = {}
    for (_, _, s), w in weights.items():
        by_scenario[s] = by_scenario.get(s, 0.0) + w
    return sum(w * float(next(c for c in run.cells if c.scenario == s).quarters[name][0])
               for s, w in by_scenario.items() if w)


def reanchor(book: Book, facts: Facts, report: Mapping, *, valuation_date: Any = None,
             version: str | None = None, near: Any = None, cell: Sequence[str] | None = None,
             base_run: GridRun | None = None, observation: Mapping | None = None,
             observe: bool = True, update_bridge: bool | None = None,
             transmission: str = KEEP) -> Reanchored:
    """Книга и факты-кандидат на отчёте `report` (формат — шапка модуля).

    `near` — готовая ближняя калибровка ЧПМ (у фактического отчёта — та, что выведена на
    ожидаемом); без неё калибровка выводится здесь: ожидаемый путь ЧПМ кандидата совпадает с
    путём `base_run` (прежняя книга с наблюдением отчёта) — по слою «свой взгляд» или по клетке
    `cell` (перезаякоривание клетки на своём пути). `observation` — наблюдение A-P2u вместо
    упр. ЧПМ и CoR отчёта (у клетки на своём пути — то же, что у прежней книги);
    `observe=False` — без наблюдения. `transmission`: `keep` — скаляры пути σ0_A, σ0_L, φ_A, φ_L
    прежней книги сохраняются, цели A-N2, A-N3 и обе доли пересчитываются на структуру нового
    якоря (режим процедуры, М§15.2 п. 8); `resolve` — только диагностика: цели и доли не
    трогаются, ядро решает скаляры заново на новой структуре.
    """
    if transmission not in TRANSMISSION_MODES:
        raise ReportError(f"transmission {transmission!r}: известны {', '.join(TRANSMISSION_MODES)}")
    check_report(book, facts, report)
    period = str(report["period"])
    first_new = next_period(period)
    end = period_end(period)
    old_anchor = str(book.get("meta.anchor_period"))
    year_shift = parse_period(period)[0] - parse_period(old_anchor)[0]
    warnings: list[str] = []
    derived: dict[str, Any] = {"year_shift": year_shift, "transmission_mode": transmission}
    X = copy.deepcopy(dict(book.source))
    M = X["meta"]

    # --- дата оценки и meta
    vdate = valuation_day(valuation_date, report)
    M.update(anchor_period=period, first_period=first_new, facts_date=end.isoformat(),
             valuation_date=vdate.isoformat(), version=version or f"{M['version']}+{period}")

    # --- наблюдение закрытого квартала
    obs = dict(observation) if observation is not None else {
        "period": period, "cor": float(report["mgmt"]["cor"]), "nim": float(report["mgmt"]["nim"]),
        "se_cor": 0.0, "se_nim": 0.0, "basis": "mgmt"}
    if observe:
        replaced = [o for o in book.get(OBS) or [] if str(o["period"]) == period]
        if replaced:
            warnings.append(f"наблюдение {period} прежней книги ({replaced[0]}) заменено фактом отчёта")
        derived["observation"] = carry_observation(X, book, facts, obs)

    # --- прежняя книга с тем же наблюдением: путь, с которым сверяется кандидат
    if base_run is None:
        base_run = run_grid(with_observation(book, obs) if observe else book, facts)
    if base_run.ctx.timeline.index(period) != 1:
        raise ReportError("base_run — не прогон прежней книги: квартал отчёта у него не первый прогнозный")
    weights = _weights(base_run, cell)

    # --- цены якоря
    if "price_index" in report["market"]:
        factor = float(report["market"]["price_index"])
    else:
        factor = sum(float(book.get(f"joint.world_prob.{w}")) * base_run.ctx.worlds[w].price_index[1]
                     for w in book.get("worlds.ids"))
        derived["price_index_source"] = "ожидаемый индекс миров книги"
    derived["price_index"] = factor
    reindex_prices(X, facts, period, factor)
    carry_fx(X, factor)
    if year_shift:
        X["noncore"]["price_base_year"] = int(X["noncore"]["price_base_year"]) + year_shift

    # --- anchor-ключи с лагом — числами прежней книги
    freeze_anchors(X, book)
    derived["frozen"] = {k: _get(X, k) for k in FROZEN_ANCHORS}

    # --- смена года якоря, справочные ключи
    shift_crisis(X, year_shift)
    if year_shift:
        warnings.append(
            f"год якоря сменился ({parse_period(old_anchor)[0]} → {parse_period(period)[0]}): кризисные "
            "ключи сдвинуты вместе с годом шока — режим «перезаряжен», и строки «механика» и «выпуклость» "
            "ненулевые по определению стационарных режимов (М§3.2). Замороженные μ наблюдений окна не "
            "пересчитываются (М§12): наблюдение закрытого квартала судится против ожидания прежней книги — "
            "с шоком, и несостоявшийся шок остаётся свидетельством против перезаряженного режима, пока запись "
            "не выйдет из окна; снизу — пол вероятности режима. Сдвиг точки на ожидаемом отчёте (строки "
            "«обучение», «механика» и «выпуклость» сухого прогона) и строка «обучение» отчёта без шока — в "
            "журнал версии книги. Гайденс нового "
            "года (volumes.guidance_year, guidance_growth, fees.growth_override) — решение книги")
    first_year = parse_period(first_new)[0]
    if int(X["nii"]["sigma0_from"]) < first_year:
        X["nii"]["sigma0_from"] = first_year
    if "next_report" in X["valuation"]:
        X["valuation"]["next_report"]["period"] = first_new

    # --- факты нового якоря
    new_facts = facts_after(book, facts, report,
                            update_bridge=(cell is None) if update_bridge is None else update_bridge)
    books = book.get("nii.books")
    old_af, new_af = anchor_facts(facts, books), anchor_facts(new_facts, books)

    # --- ключи спредов от ставок якоря
    derived["spread_books"] = rederive_spreads(X, book, old_af.rates, new_af.rates)

    # --- капитал: вычеты формулой книги, плотности RWA — общим множителем к RWA якоря
    probe_data = copy.deepcopy(X)
    for path in DEDUCTIONS:
        _set(probe_data, path, "anchor")
    probe = book_from_dict(probe_data, facts=new_facts)
    for path in DEDUCTIONS:
        if _get(X, path) != "anchor":
            _set(X, path, _r(probe.anchors[path], 3))
    k_rwa = float(report["capital"]["basel_rwa"]) / rwa_anchor(rwa_weights(probe, book_roles(books)), new_af)
    X["capital"]["rwa"]["density"] = {k: _r(float(v) * k_rwa, 6) for k, v in X["capital"]["rwa"]["density"].items()}
    _scale_axes(X, (DENSITY,), k_rwa)
    derived.update(rwa_factor=k_rwa, deductions={path: _get(X, path) for path in DEDUCTIONS})

    # --- передача ставки: скаляры пути прежней книги на структуре нового якоря
    kept_axes = {path: [{k: copy.deepcopy(axis[k]) for k in keys if k in axis}
                        for axis, keys in _value_axes(X, path)] for path in TRANSMISSION_KEYS}
    derived["transmission"] = keep_transmission(X, new_facts, base_run)

    # --- поправки «норматив − расчёт» и ближняя калибровка ЧПМ: по прогону кандидата
    periods = [period_str(*shift_quarter(*parse_period(first_new), i))
               for i in range(pindex(str(M["last_period"])) - pindex(first_new) + 1)]
    last_year = parse_period(str(M["last_period"]))[0]
    target = [_mean(base_run, weights, "nim", q + 1) for q in range(1, len(periods) + 1)]
    fact = {"n20": float(report["capital"]["n20_0"]), "n11": float(report["capital"]["n1_1_bank"])}
    if near is not None:
        X["regimes"]["near_nim_shift"] = copy.deepcopy(near)
    old_near = book.source["regimes"]["near_nim_shift"]
    for step in range(PASSES):               # повторы добирают второй порядок (ЧПД → капитал → баланс → ЧПМ)
        run = run_grid(book_from_dict(X, facts=new_facts), new_facts)
        moved = 0.0
        for path, name in GAPS:
            delta = fact[name] - _anchor_row(run, weights, name)
            if abs(delta) > GAP_TOL:
                value = float(_get(X, path))
                _set(X, path, _r(value + delta, 12))
                _remap_axes(X, path, value, value + delta)
                moved = max(moved, abs(delta))
        if near is None:
            got = [_mean(run, weights, "nim", q) for q in range(1, len(periods) + 1)]
            before = X["regimes"]["near_nim_shift"]
            t_near = Trajectory(before)
            values = [t_near.value(*parse_period(p)) - (g - t) for p, g, t in zip(periods, got, target)]
            after = near_trajectory(old_near if step == 0 else before, periods, values, last_year)
            X["regimes"]["near_nim_shift"] = after
            was = before if isinstance(before, Mapping) else {}
            moved = max([moved] + [abs(float(after.get(k, 0.0)) - float(was.get(k, 0.0)))
                                   for k in set(after) | set(was)])
            moved = moved if step else 1.0   # первый проход — от прежней траектории: всегда повторить
        if moved < NEAR_TOL / 2:
            break
    cand = book_from_dict(X, facts=new_facts)
    run = run_grid(cand, new_facts)
    if near is None:
        got = [_mean(run, weights, "nim", q) for q in range(1, len(periods) + 1)]
        derived["nim_path"] = {"periods": periods, "old": target, "new": got,
                               "residual": max(abs(g - t) for g, t in zip(got, target))}
    if transmission == RESOLVE:              # диагностика: цели, доли и их оси — прежней книги, скаляры решает ядро
        for path in TRANSMISSION_KEYS:
            _set(X, path, _get(book.source, path))
            for (axis, _), before in zip(_value_axes(X, path), kept_axes[path]):
                axis.update(before)
        cand = book_from_dict(X, facts=new_facts)
        run = run_grid(cand, new_facts)
    eng = engine_ratios(book, facts, report)
    bridges = bridge_from_facts(facts), bridge_from_facts(new_facts)
    derived.update(
        gaps={path: _get(X, path) for path, _ in GAPS},
        anchor_row={name: _anchor_row(run, weights, name) for _, name in GAPS},
        engine={k: eng[k] for k in ("nim", "cor", "cir")}, mgmt=dict(report["mgmt"]),
        bridge_old={x: getattr(bridges[0], f"{x}_value") for x in ("cor", "nim", "cir")},
        bridge_new={x: getattr(bridges[1], f"{x}_value") for x in ("cor", "nim", "cir")},
        posterior=dict(run.ctx.posterior),
        sigmas={"old": _sigmas(base_run.ctx.transmission), "new": _sigmas(run.ctx.transmission)},
        phi_weights={"old": dict(base_run.ctx.transmission.phi_weights),
                     "new": dict(run.ctx.transmission.phi_weights)},
        regime_floor=regime_floor(book, run.ctx.posterior))
    if "cir" not in report["mgmt"]:
        warnings.append("в отчёте нет mgmt.cir: история моста CIR останется без закрытого квартала, и годовой "
                        "CIR года якоря в упр. базисе (гейт guidance_gap, выпуск) на фактах кандидата не "
                        "соберётся (М§4.6) — внести CIR презентации")
    note = lost_kappa(book, base_run)
    if note:
        warnings.append(note)
    warnings += manual_steps(book, X)
    return Reanchored(data=X, book=cand, facts=new_facts, period=period, derived=derived,
                      changes=diff(dict(book.source), X), warnings=warnings,
                      near=copy.deepcopy(X["regimes"]["near_nim_shift"]))


def regime_floor(book: Book, posterior: Mapping[str, float]) -> dict[str, Any]:
    """Пол вероятности режима (М§12): `floor_share` × базовая; режимы, стоящие на полу после наблюдений."""
    share = float(book.get("joint.regime_update.floor_share"))
    floors = {r: share * float(p) for r, p in book.get("joint.regime_prob").items()}
    on_floor = [r for r in floors if share and float(posterior[r]) <= floors[r] + GAP_TOL]
    return {"share": share, "floors": floors, "on_floor": on_floor}


def lost_kappa(book: Book, run: GridRun) -> str | None:
    """κ-добавка квартала, уходящего в историю: после переноса якоря ядро считает её нулём
    (М§4.6: до сетки rr_W ≡ rr_N) — это не переносится, только называется."""
    kappa, lag = float(book.get("credit.kappa")), int(book.get("credit.real_rate_lag_q"))
    worlds = run.ctx.worlds
    if not kappa or BASE_WORLD not in worlds or 1 + lag > run.ctx.timeline.Q:
        return None
    lost = {w: kappa * max(0.0, p.real_key[1] - worlds[BASE_WORLD].real_key[1]) for w, p in worlds.items()}
    if not any(lost.values()):
        return None
    parts = ", ".join(f"{w}: +{v * 100:.2f} п.п." for w, v in lost.items() if v)
    return (f"κ-добавка к CoR в {run.ctx.timeline.period(1 + lag)} от реальной ставки закрытого квартала "
            f"теряется при переносе якоря ({parts}): ядро считает добавку до сетки нулём (М§4.6) — "
            "эффект в строке «механика»")


def manual_steps(book: Book, X: dict) -> list[str]:
    """Что инструмент сознательно НЕ делает — суждения книги и другие потоки."""
    out = []
    for path, _ in GAPS:
        old, new = float(book.get(path)), float(_get(X, path))
        if abs(new - old) > 0.0005:
            out.append(f"{path}: {old:+.4f} → {new:+.4f} — якорь воспроизводит норматив отчёта; ось "
                       "сдвинута вместе с центром, ширину пересмотреть (решение книги)")
    out.append(f"{SPLIT}, {PHI_SPLIT}: центры и оси — правило листов книги на структуре якоря (evidence/nii): "
               "пересчитать на новом якоре; дисконт за управление — неподвижная точка на новой медиане (решения "
               "книги, в разложении выпусков — строка «суждения книги»)")
    out.append("миры и кривые — на прежней дате (meta.curve_as_of): пересборка миров — отдельной версией "
               "книги вместе с семейством")
    out.append("meta.market_price и meta.date — прежней книги: обновить вместе с датой оценки")
    out.append("valuation.next_report.cor_values, nim_values — сетки «что даст отчёт» вокруг ожидания "
               "нового открытого квартала: пересмотреть")
    out.append("факты: канон пересобирает `ops/tools/build_facts.py` (`--anchor` из листов квартала, "
               "`--expected` из ожидаемого отчёта) с источниками и сверками; сверка с фактами кандидата — "
               "`--compare-facts`")
    out.append("объяснения гейтов (gate_explanations.yaml), results.json и run_output.txt, закреплённые "
               "числа тестов книги и документов — на новой книге (MANUAL §10.4)")
    return out


# ------------------------------------------------------------------ дифф


def diff(a: Any, b: Any, prefix: Path_ = ()) -> list[tuple[Path_, Any, Any]]:
    """Все листья, которые изменились: (путь звеньями, было, стало)."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        out = []
        for key in list(a) + [k for k in b if k not in a]:
            path = prefix + (str(key),)
            if key not in b:
                out.append((path, a[key], None))
            elif key not in a:
                out.append((path, None, b[key]))
            else:
                out += diff(a[key], b[key], path)
        return out
    if isinstance(a, list) and isinstance(b, list) and a \
            and all(isinstance(x, Mapping) and "name" in x for x in a) \
            and [x["name"] for x in a] == [y.get("name") if isinstance(y, Mapping) else None for y in b]:
        out = []
        for x, y in zip(a, b):
            out += diff(x, y, prefix + (("name", x["name"]),))
        return out
    return [] if a == b else [(prefix, a, b)]


def dotted(path: Path_) -> str:
    out = ""
    for part in path:
        out += f"[{part[1]}]" if isinstance(part, tuple) else (f".{part}" if out else part)
    return out


def reason(path: str) -> str:
    """Почему ключ сдвинулся — для таблицы «что сдвинулось»."""
    return next((text for prefix, text in REASONS if path.startswith(prefix)), "")


# ------------------------------------------------------------------ ожидаемый отчёт


def _cell_report(run: GridRun, weights: Mapping[tuple, float]) -> dict:
    """Отчёт за первый прогнозный квартал прежней книги: состояние клеток `weights` на конец
    квартала (одна клетка — её путь; слой — ожидание, отношения — отношением ожиданий).

    Ближний сдвиг ЧПМ (слагаемое Ov, М§4.4) разнесён по книгам активов пропорционально их
    процентному доходу — как в настоящей отчётности, где процентный доход разложен без остатка."""
    ctx = run.ctx
    book, tl, prep = ctx.book, ctx.timeline, ctx.prep
    af, roles = prep.af, prep.roles
    total = sum(weights.values())
    w = {tuple(k): v / total for k, v in weights.items() if v}
    cells = {k: run.cell(*k) for k in w}
    year_days = tl.d(1) / 365

    def avg(name: str) -> float:
        return sum(p * float(cells[k].quarters[name][1]) for k, p in w.items())

    balances = {b: sum(p * float(cells[k].books[b]["balance"][1]) for k, p in w.items()) for b in roles.names}
    interest = {b: sum(p * float(cells[k].books[b]["rate"][1]) * float(cells[k].books[b]["avg"][1]) * year_days
                       for k, p in w.items()) for b in roles.names}
    carried = 1.0 + avg("overlay") / sum(interest[b] for b in roles.assets)
    for b in roles.assets:
        interest[b] *= carried
    pbt = avg("pbt")
    lines = {"nii": avg("nii"), "fees_net": avg("fees"), "llp_debt_fa": -avg("llp"),
             "insurance_net": avg("ins"), "noncore_net": avg("noncore"), "opex": -avg("opex")}
    misc = pbt - sum(lines.values())
    dp, dpreg = avg("dp"), avg("dpreg")
    if dpreg and abs(dpreg - dp) > 1e-6 * max(1.0, dp):
        raise ReportError("ожидаемый отчёт: дивиденд, не вычтенный из регуляторного капитала, не равен "
                          "дивидендам к выплате на конец квартала — календарь книги не представим фактами якоря")
    declared = [dec.year for k in w for dec in cells[k].decisions
                if tl.index(period_str(dec.year + 1, prep.policy.agm_quarter)) == 1]
    year = declared[0] if declared else af.dividends_payable_year
    by_world: dict[str, float] = {}
    for (world, _, _), p in w.items():
        by_world[world] = by_world.get(world, 0.0) + p

    def world_avg(series) -> float:
        return sum(p * series(ctx.worlds[x]) for x, p in by_world.items())

    def over_ratio(name: str, capital: str) -> float:    # норматив − капитал / RWA: поправка и вычеты сценария
        return sum(p * (float(cells[k].quarters[name][1])
                        - float(cells[k].quarters[capital][1]) / float(cells[k].quarters["rwa"][1]))
                   for k, p in w.items())

    rwa, k11 = avg("rwa"), avg("k11")
    br = ctx.bridge
    who = f"клетка {'/'.join(next(iter(w)))}" if len(w) == 1 else "слой «свой взгляд»"
    balance = {
        "books": balances, "loans_fvtpl": avg("loans_fv"),
        "fvoci": float(book.get("oci.fvoci_share")) * balances[SECURITIES],
        "fvtpl_bonds": float(book.get("oci.fvtpl_bond_share")) * balances[SECURITIES],
        "allowance": avg("allowance"), "other_assets": avg("other_assets"),
        "other_liabilities": avg("other_liabilities"), "dividends_payable": dp,
        "bv_common": avg("bv"), "fvoci_reserve": avg("reserve"), "at1": af.at1, "nci": af.nci,
    }
    if year is not None:
        balance["dividends_payable_year"] = int(year)
    return _rounded({
        "schema": SCHEMA, "period": tl.period(1), "as_of": tl.end(1).isoformat(),
        "note": (f"синтетический отчёт: ожидание модели книги {book.get('meta.version')} ({who}) — "
                 "сухой прогон, не факты"),
        "balance": balance, "interest": interest, "dia": avg("dia"),
        "pnl": {**lines, "misc_net": misc, "pbt": pbt, "tax": -(avg("tax") + avg("ot")), "ni": avg("ni"),
                "ni_shareholders": avg("ni_sh"), "oci_fvoci": avg("oci"), "other_equity_movements": avg("om")},
        "capital": {"n20_0": avg("k20") / rwa + over_ratio("n20", "k20"), "n20_pre_dividend": dpreg > 0,
                    "n1_1_bank": k11 / rwa + over_ratio("n11", "k11"),
                    "basel_cet1": avg("bv") - avg("ded20"), "basel_rwa": rwa, "bank_base_capital": k11},
        "market": {"ofz_curve": {n: world_avg(lambda x, n=n: x.ofz[f"ofz_{n}y"][1]) for n in CURVE_NODES},
                   "key_avg": avg("key"), "price_index": world_avg(lambda x: x.price_index[1])},
        "mgmt": {"nim": br.to_mgmt_nim(avg("nim")), "cor": br.to_mgmt_cor(avg("cor")),
                 "cir": br.to_mgmt_cir(avg("opex") / (avg("nii") + avg("fees") + avg("ins") + misc))},
    }, 9)


def expected_run(book: Book, facts: Facts, live: LiveInputs | None = None, *,
                 observe: bool = True) -> tuple[Book, GridRun, dict | None]:
    """Прежняя книга с наблюдением, равным ожиданию модели (A⁺), её прогон и само наблюдение."""
    if not observe:
        return book, run_grid(book, facts, live), None
    obs = expected_observation(book, facts, live)
    plus = with_observation(book, obs)
    return plus, run_grid(plus, facts, live), obs


def expected_report(book: Book, facts: Facts, *, cell: Sequence[str] | None = None,
                    run: GridRun | None = None, observe: bool = True) -> dict:
    """Синтетический отчёт за первый прогнозный квартал = ожидание модели (М§15.2).

    `cell` — путь одной клетки (точная проверка механики, без неравенства Йенсена); иначе —
    ожидание слоя «свой взгляд». `run` — готовый прогон прежней книги с ожидаемым наблюдением."""
    if run is None:
        _, run, _ = expected_run(book, facts, observe=observe)
    return _cell_report(run, _weights(run, cell))


# ------------------------------------------------------------------ разложение скачка


def live_at(book: Book, facts: Facts, valuation_date: dt.date) -> LiveInputs:
    """Живые входы книги на дате оценки `valuation_date`: цены и реестр — книжные."""
    live = live_from_book(book, facts)
    return LiveInputs(valuation_date=valuation_date, prices=live.prices,
                      price_dates={t: valuation_date for t in live.prices}, register=live.register)


def headline(run: GridRun) -> dict:
    """Низ, точка при λ и верх (₽) и V0 слоёв (млрд ₽) при значениях книги."""
    return {"low": run.low, "point": run.point, "high": run.high,
            "v0": {name: layer.v0 for name, layer in run.layers.items()}}


def cell_mechanics(book: Book, facts: Facts, run_plus: GridRun, obs: Mapping | None,
                   valuation_date: dt.date, *, cells: Sequence[Sequence[str]] | None = None,
                   transmission: str = KEEP) -> dict:
    """Строка «механика» (М§15.2): каждая клетка перезаякорена на своём пути.

    Отчёт клетки — её состояние на конец закрытого квартала; наблюдение и вероятности — как у
    прежней книги с ожидаемым наблюдением. Возвращает строки по клеткам и точку слоёв. При
    смене года якоря клетки режима с годом шока «перезаряжены» (М§3.2) — их путь другой по
    определению, и в допуск они не входят."""
    keys = [tuple(k) for k in cells] if cells is not None else [c.key for c in run_plus.cells]
    tl = run_plus.ctx.timeline
    rearmed = {r for r in book.get("regimes.ids")
               if tl.year(1) != tl.anchor_year and "shock_year_offset" in book.source["regimes"][r]}
    rows = []
    for key in keys:
        old = run_plus.cell(*key)
        cand = reanchor(book, facts, _cell_report(run_plus, {key: 1.0}), valuation_date=valuation_date,
                        cell=key, base_run=run_plus, observation=obs, observe=obs is not None,
                        transmission=transmission)
        new = run_grid(cand.book, cand.facts, live_at(cand.book, cand.facts, valuation_date)).cell(*key)
        rows.append({"cell": old.label, "key": key, "old": old.v_ri, "new": new.v_ri,
                     "rel": new.v_ri / old.v_ri - 1, "rearmed": key[1] in rearmed})
    kept = [r for r in rows if not r["rearmed"]] or rows
    worst = max(kept, key=lambda r: abs(r["rel"]))
    out = {"rows": rows, "worst": worst, "tolerance": MECHANICS_TOL, "ok": abs(worst["rel"]) <= MECHANICS_TOL,
           "rearmed": sorted(rearmed)}
    if cells is None:                        # точка: те же вероятности, цена линейна по V0 (М§8.2)
        new_v = {r["key"]: r["new"] for r in rows}
        n_out = getattr(run_plus, "shares_out", 0.0) or run_plus.ctx.prep.af.n_out
        per_v0 = (1.0 - run_plus.governance) * 1000.0 / n_out
        price, v0 = {}, {}
        for name, layer in run_plus.layers.items():
            v0[name] = sum(p * new_v[k] for k, p in layer.prob.items())
            price[name] = layer.price + (v0[name] - layer.v0) * per_v0
        low, high = price[NEUTRAL], price[ANALYTICAL]
        out["headline"] = {"low": low, "high": high, "point": low + run_plus.lam * (high - low), "v0": v0}
    return out


def band_stats(book: Book, facts: Facts, live: LiveInputs, draws: int) -> dict:
    """Медиана и полоса 80 % печатаемого заголовка на `draws` прогонах (те же точки гиперкуба
    у каждой книги — сравнение парное)."""
    from model.uncertainty import band
    b = band(book, facts, live, draws=draws)
    st = b.stats(b.lam)
    main = str(book.get("meta.company.main_ticker"))
    return {"p10": st["p10"], "median": st["median"], "p90": st["p90"],
            "p_below": b.p_below(b.lam, float(live.prices[main]))}


def core_checks(run: GridRun, today: dt.date) -> dict[str, Any]:
    """Инварианты и гейты ядра на прогоне (М§14.1–§14.2): нарушенные инварианты и сработавшие гейты
    с массой. Отказ ядра (нет факта, без которого проверка не считается) — причиной в `error`."""
    try:
        broken = [(f.name, f.message) for f in check_invariants(run) if f.fired]
        gates = {f.name: (f.mass, f.message) for f in check_gates(run, today=today)
                 if f.kind == "gate" and f.fired}
    except (BookError, FactsError) as exc:
        return {"error": str(exc), "invariants": [], "gates": {}}
    return {"error": None, "invariants": broken, "gates": gates}


def attribution(book: Book, facts: Facts, report: Mapping, *, valuation_date: Any = None,
                version: str | None = None, band_draws: int | None = None, cells: bool = True,
                transmission: str = KEEP, observe: bool = True, observation: Mapping | None = None) -> dict:
    """Кандидат на отчёте `report` и разложение скачка на одной дате оценки (М§15.2):

    обучение    A⁺ − A  прежняя книга с наблюдением, равным ожиданию модели, минус она же без него;
    механика    O − A⁺  каждая клетка перезаякорена на своём пути — должна быть ≈ 0;
    выпуклость  E − O   кандидат на ожидаемом отчёте: один отчёт на все клетки (неравенство Йенсена);
    сюрприз     F − E   кандидат на отчёте `report` с той же ближней калибровкой ЧПМ.

    Всегда — точка при значениях книги; медиана полосы — при `band_draws` (одни точки гиперкуба
    на каждой книге; у медианы механика и выпуклость — одной строкой E − A⁺). `checks` — инварианты
    и гейты ядра на прежней книге и на кандидате (дата оценки — та же).

    `observation` — подмена наблюдения A-P2u закрытого квартала решением книги (`check_observation`,
    docs/REANCHOR.md §3.1): идёт только в кандидата на отчёте F — факты кандидата остаются числами
    отчёта, а строка «сюрприз факта» несёт и отчёт, и подмену. Без неё наблюдение — упр. ЧПМ и CoR
    отчёта с se 0. Апостериорные прежней книги с тем же наблюдением, что у F, — `posterior.book_report`
    (критерий 5 §1.5; на сухом прогоне без подмены их нет — это `posterior.expected`)."""
    check_report(book, facts, report)
    override = None
    if observation is not None:
        if not observe:
            raise ReportError("подмена наблюдения без наблюдения (observe=False): подменять нечего")
        override = check_observation(observation, str(report["period"]), report["mgmt"])
    vdate = valuation_day(valuation_date, report)
    live = live_at(book, facts, vdate)
    run_a = run_grid(book, facts, live)
    plus, run_plus, obs = expected_run(book, facts, live, observe=observe)
    expected = _cell_report(run_plus, _weights(run_plus, None))
    common = dict(valuation_date=vdate, version=version, base_run=run_plus, observe=observe,
                  transmission=transmission)
    cand_e = reanchor(book, facts, expected, **common)
    obs_f = None if override is None else {k: override[k] for k in ("period", *OBS_FIELDS, "basis")}
    if override is not None and override.get("published"):
        obs_f.update(published=override["published"], reason=override["note"])
    cand_f = reanchor(book, facts, report, near=cand_e.near, observation=obs_f, **common)
    if override is not None:
        mg = report["mgmt"]
        cand_f.derived["observation_override"] = {
            "reason": override["reason"], "report": {"cor": float(mg["cor"]), "nim": float(mg["nim"])},
            "used": {k: override[k] for k in OBS_FIELDS}}
        if override.get("note"):
            cand_f.derived["observation_override"]["note"] = override["note"]
        cand_f.warnings.insert(0, "наблюдение A-P2u закрытого квартала подменено решением книги ("
                               + _override_words(cand_f.derived["observation_override"]) + "): в журнал версии "
                               "книги — строкой «суждения книги» с причиной; факты (упр. CoR и ЧПМ квартала, "
                               "история моста, журнал прогнозов) — опубликованные числа отчёта")
    live_e, live_f = live_at(cand_e.book, cand_e.facts, vdate), live_at(cand_f.book, cand_f.facts, vdate)
    run_e, run_f = run_grid(cand_e.book, cand_e.facts, live_e), run_grid(cand_f.book, cand_f.facts, live_f)
    out: dict[str, Any] = {
        "valuation_date": vdate, "observation": obs, "expected": expected, "candidate": cand_f,
        "candidate_expected": cand_e, "transmission": transmission, "band_draws": band_draws,
        "observation_override": override,
        "posterior": {"book": dict(run_a.ctx.posterior), "expected": dict(run_plus.ctx.posterior),
                      "candidate": dict(run_f.ctx.posterior)},
        "steps": {"rolled": headline(run_a), "learning": headline(run_plus),
                  "expected": headline(run_e), "candidate": headline(run_f)},
        "print_step": float(book.get("valuation.headline.print_step")),
        "checks": {"book": core_checks(run_a, vdate), "candidate": core_checks(run_f, vdate)},
    }
    seen = cand_f.derived.get("observation")
    if obs is not None and seen is not None and not _same_observation(seen, obs):
        plain = {k: seen[k] for k in ("period", *OBS_FIELDS, "basis")}
        out["posterior"]["book_report"] = dict(run_grid(with_observation(book, plain), facts, live).ctx.posterior)
    shocked = shock_regimes(run_a) if observe else []
    if shocked:                              # квартал шока: ожидаемый отчёт — среднее двугорбого прогноза (М§12)
        calm = expected_observation(book, facts, live, without=shocked)
        run_calm = run_grid(with_observation(book, calm), facts, live)
        by_regime = {r["regime"]: r for r in _regime_rows(book, facts, live)}
        out["no_shock"] = {"regimes": shocked, "observation": calm, "headline": headline(run_calm),
                           "posterior": dict(run_calm.ctx.posterior),
                           "shock_cor": {r: by_regime[r]["cor_q_mgmt"] for r in shocked},
                           "shock_probability": sum(by_regime[r]["posterior"] for r in shocked)}
    if cells:
        out["mechanics"] = cell_mechanics(book, facts, run_plus, obs, vdate, transmission=transmission)
        out["steps"]["mechanics"] = out["mechanics"]["headline"]
    if band_draws:
        for key, (b, f, lv) in {"rolled": (book, facts, live), "learning": (plus, facts, live),
                                "expected": (cand_e.book, cand_e.facts, live_e),
                                "candidate": (cand_f.book, cand_f.facts, live_f)}.items():
            out["steps"][key]["band"] = band_stats(b, f, lv, band_draws)
    return out


def _regime_rows(book: Book, facts: Facts, live: LiveInputs) -> list[dict]:
    """Прогноз фильтра на первый прогнозный квартал по режимам (упр. базис) и их вероятности."""
    from model.nextreport import model_expectation
    period = str(book.get("meta.first_period"))
    return model_expectation(_drop_observation(book, period), facts, period, live=live)["by_regime"]


def is_dry_run(result: Mapping) -> bool:
    """Отчёт — сам ожидаемый отчёт (сухой прогон): факты обоих кандидатов совпадают, наблюдение не подменено."""
    return (result["candidate"].facts.digest == result["candidate_expected"].facts.digest
            and not result.get("observation_override"))


# ------------------------------------------------------------------ REANCHOR.md


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return str(value)


def _mass(gate: tuple | None) -> str:
    """Масса сработавшего гейта для таблицы проверок: «—» — не сработал."""
    if gate is None:
        return "—"
    return "сработал" if gate[0] is None else f"{gate[0]:.4f}"


def _pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f} %".replace(".", ",")


def _override_words(ov: Mapping) -> str:
    """Подмена наблюдения словами: «CoR 1,300 % (se 0,120 п.п.) вместо 1,100 % отчёта; ЧПМ 6,650 % (se
    0,000 п.п.) — как в отчёте»."""
    parts = []
    for x, word in (("cor", "CoR"), ("nim", "ЧПМ")):
        value, se, rep = ov["used"][x], ov["used"][f"se_{x}"], ov["report"][x]
        if value is None:
            parts.append(f"{word} не наблюдается (в отчёте {_pct(rep, 3)})")
            continue
        se_words = "se " + f"{float(se) * 100:.3f}".replace(".", ",") + " п.п."
        parts.append(f"{word} {_pct(value, 3)} ({se_words}) "
                     + ("— как в отчёте" if abs(value - rep) < 5e-7 else f"вместо {_pct(rep, 3)} отчёта"))
    return "; ".join(parts)


STEP_TITLES = (
    ("rolled", "прежняя книга на дате оценки (без отчёта)"),
    ("learning", "+ наблюдение, равное ожиданию модели (обучение)"),
    ("mechanics", "каждая клетка — на своём пути (механика, ≈ 0)"),
    ("expected", "кандидат на ожидаемом отчёте (выпуклость)"),
    ("candidate", "кандидат на отчёте (сюрприз факта)"),
)
BAND_TITLES = (
    ("rolled", "прежняя книга (без отчёта)"),
    ("learning", "+ ожидаемое наблюдение (обучение)"),
    ("expected", "кандидат на ожидаемом отчёте (механика и выпуклость, ≈ 0)"),
    ("candidate", "кандидат на отчёте (сюрприз факта)"),
)


def report_text(book: Book, result: Mapping) -> str:
    """REANCHOR.md: производные величины, «что сдвинулось», разложение скачка, ручные шаги."""
    cand: Reanchored = result["candidate"]
    X, d = cand.data, cand.derived
    M = X["meta"]
    lines = [f"# Перезаякоривание книги {book.get('meta.version')} на отчёт {cand.period}", ""]
    mech = result.get("mechanics")
    diagnosis = result["transmission"] == RESOLVE
    if mech and not mech["ok"]:
        lines += [f"**ОТКАЗ: механика вне допуска** — клетка {mech['worst']['cell']} на своём пути меняет "
                  f"стоимость на {_pct(mech['worst']['rel'], 3)} (допуск {_pct(mech['tolerance'], 1)}): "
                  + ("скаляры пути передачи ставки решены заново — это и есть сдвиг без новости, который снимает "
                     "режим `keep`." if diagnosis else
                     "какое-то правило, помнящее прошлое, не перенесено. Кандидат записан для разбора, "
                     "в канон не переносить."), ""]
    if diagnosis:
        lines += ["**ДИАГНОСТИКА: режим `resolve`.** Цели A-N2, A-N3 и доли оставлены числами прежней книги, "
                  "скаляры пути решены заново на структуре нового якоря — их сдвиг целиком в строке «механика». "
                  "Кандидат книги и фактов не записан: в процедуре перезаякоривания режим один — `keep` "
                  "(М§15.2 п. 8).", ""]
    lines += [f"Кандидат: версия {M['version']}, якорь {M['anchor_period']} (факты на {M['facts_date']}), "
              f"первый прогнозный квартал {M['first_period']}, дата оценки {M['valuation_date']}. "
              f"Передача ставки: `{result['transmission']}`.", ""]
    if is_dry_run(result):
        lines += ["Отчёт — ожидаемый (сухой прогон): фактов в нём нет, строка «сюрприз факта» — ноль.", ""]
    ov = d.get("observation_override")
    if ov:
        lines += ["**Наблюдение закрытого квартала подменено решением книги** (`--observation`, копия — "
                  f"`observation.json` каталога): {_override_words(ov)}. Причина: {ov['reason'].rstrip('.')}. "
                  + (f"Пометка подмены в записи наблюдения (её печатает витрина): «{ov['note']}». "
                     if ov.get("note") else "")
                  + "Факты кандидата — опубликованные числа отчёта; подмена — только в наблюдении A-P2u, её эффект — "
                  "в строке «сюрприз факта». В журнал версии книги — строкой «суждения книги» с причиной.", ""]

    eng, mg = d["engine"], d["mgmt"]
    lines += ["## Производные величины", "",
              f"- квартал {cand.period}: ЧПМ движка {_pct(eng['nim'], 3)} (упр. {_pct(mg['nim'], 3)}), "
              f"CoR движка {_pct(eng['cor'], 3)} (упр. {_pct(mg['cor'], 3)}), CIR движка {_pct(eng['cir'], 2)}"]
    if result.get("observation"):
        exp = result["observation"]
        lines.append(f"- ожидание модели на квартал (упр.): ЧПМ {_pct(exp['nim'], 3)}, CoR {_pct(exp['cor'], 3)}")
    if d.get("observation"):
        obs, post = d["observation"], result["posterior"]
        lines.append("- наблюдение A-P2u с замороженными ожиданиями режимов: CoR " + _fmt(obs.get("mu_cor"))
                     + "; ЧПМ " + _fmt(obs.get("mu_nim")))
        if ov:
            lines.append(f"- подмена наблюдения (решение книги): {_override_words(ov)}; в записи окна — CoR "
                         f"{_fmt(obs.get('cor'))} (se {_fmt(obs.get('se_cor'))}), ЧПМ {_fmt(obs.get('nim'))} (se "
                         f"{_fmt(obs.get('se_nim'))}); причина — {ov['reason']}")
        lines.append("- вероятности режимов: книга " + _fmt(post["book"]) + " → после ожидаемого наблюдения "
                     + _fmt(post["expected"]) + " → кандидат " + _fmt(post["candidate"])
                     + ("; прежняя книга с тем же наблюдением, что у кандидата" + (" (с подменой)" if ov else "")
                        + ": " + _fmt(post["book_report"]) if post.get("book_report") else ""))
        fl = d["regime_floor"]
        lines.append(f"- пол вероятности режима ({fl['share']:g} × базовая): " + _fmt(fl["floors"]) + "; на полу у "
                     "кандидата: " + (", ".join(fl["on_floor"]) or "нет")
                     + " — ниже пола вероятности после наблюдений не опускаются; его действие — в строке «обучение»")
    lines.append(f"- индекс цен квартала {d['price_index']:.6f}"
                 + (f" ({d['price_index_source']})" if d.get("price_index_source") else "")
                 + " — множитель реальных сумм книги и их осей")
    lines.append(f"- множитель плотностей RWA к RWA якоря {d['rwa_factor']:.6f}; вычеты: "
                 + ", ".join(f"`{k}` {_fmt(float(v)) if _num(v) else v}" for k, v in d["deductions"].items()))
    lines.append("- поправки «норматив − расчёт»: " + ", ".join(f"`{k}` {float(v):+.6f}" for k, v in d["gaps"].items())
                 + f"; якорь модели: Н20.0 {_pct(d['anchor_row']['n20'], 3)}, Н1.1 {_pct(d['anchor_row']['n11'], 3)}")
    kept = "; ".join(f"`{k}` {a:.6f} → {b:.6f}" for k, (a, b, _) in d["transmission"].items())
    sig, omega = d["sigmas"], d["phi_weights"]
    lines.append(f"- передача ставки, скаляры пути {', '.join(SCALARS)}: прежняя книга " + _fmt(list(sig["old"]))
                 + ", кандидат " + _fmt(list(sig["new"])) + "; цели и доли на структуре нового якоря при прежних "
                 "скалярах: " + kept
                 + (" — в режиме `resolve` цели и доли оставлены прежними, скаляры решены заново"
                    if d["transmission_mode"] == RESOLVE else ""))
    if omega["old"]:
        drift = max(abs(omega["new"][b] - omega["old"][b]) for b in omega["old"])
        lines.append("- доли кредитных книг ω в разности опорных ставок X (добавка к стоимости пассивов) — со "
                     "структуры нового якоря: " + ", ".join(
                         f"{b} {omega['old'][b]:.4f} → {omega['new'][b]:.4f}" for b in omega["old"])
                     + f"; наибольший сдвиг {drift * 100:.3f} п.п. — скаляром не сохраняется, эффект в строке "
                     "«механика»")
    lines.append("- мост упр. ↔ МСФО: " + ", ".join(
        f"{x} {d['bridge_old'][x]:+.6f} → {d['bridge_new'][x]:+.6f}" for x in ("cor", "nim", "cir")))
    lines.append("- заморожены числами прежней книги: "
                 + ", ".join(f"`{k}` = {_fmt(v)}" for k, v in d["frozen"].items()))
    if d.get("spread_books"):
        lines.append("- ключи спреда от ставок якоря выведены заново у книг: " + ", ".join(d["spread_books"]))
    path = result["candidate_expected"].derived.get("nim_path")
    if path:
        lines.append("- ближняя калибровка ЧПМ выведена на ожидаемом отчёте: наибольшее расхождение ожидаемого "
                     f"пути ЧПМ с прежней книгой {path['residual'] * 1e4:.2f} б.п. "
                     f"(кварталы {path['periods'][0]}…{path['periods'][-1]})")

    def why(path: str) -> str:
        text = reason(path)
        if ov and path.startswith(OBS):
            text += f"; наблюдение подменено решением книги ({_override_words(ov)}): {ov['reason']}"
        return text.replace("|", "/")

    lines += ["", "## Что сдвинулось в книге", "", "| путь | было | стало | почему |", "|---|---|---|---|"]
    lines += [f"| `{dotted(p)}` | {_fmt(a)} | {_fmt(b)} | {why(dotted(p))} |" for p, a, b in cand.changes]

    steps = result["steps"]
    lines += ["", f"## Разложение скачка на {result['valuation_date']}: точка при значениях книги", "",
              "Низ — слой «рыночные ставки как есть», верх — «свой взгляд», точка — при λ книги; V0 — млрд ₽.", "",
              "| шаг | низ, ₽ | точка, ₽ | верх, ₽ | V0 свой взгляд | V0 рыночные ставки |",
              "|---|---|---|---|---|---|"]
    prev = None
    for key, title in STEP_TITLES:
        if key not in steps:
            continue
        if key == "expected" and "mechanics" not in steps:
            title = "кандидат на ожидаемом отчёте (механика и выпуклость, ≈ 0)"
        h = steps[key]
        now = [h["low"], h["point"], h["high"], h["v0"][ANALYTICAL], h["v0"][NEUTRAL]]
        if prev is None:
            lines.append(f"| {title} | " + " | ".join(f"{x:.2f}" for x in now[:3])
                         + " | " + " | ".join(f"{x:.1f}" for x in now[3:]) + " |")
        else:
            lines.append(f"| {title} | " + " | ".join(f"{x:.2f} ({x - y:+.2f})" for x, y in zip(now[:3], prev[:3]))
                         + " | " + " | ".join(f"{x:.1f} ({x - y:+.1f})" for x, y in zip(now[3:], prev[3:])) + " |")
        prev = now
    if "mechanics" not in steps:
        lines += ["", "Клетки на своём пути не считались (`--no-cells`): механика и выпуклость — одной строкой, "
                  "допуск механики не проверен."]
    if ov:
        lines += ["", "Строка «сюрприз факта» несёт и отчёт, и подмену наблюдения закрытого квартала (шапка); "
                  "прочие строки подмены не видят."]
    calm = result.get("no_shock")
    if calm:
        exp, a, plus, h = result["observation"], steps["rolled"], steps["learning"], calm["headline"]
        names = ", ".join(calm["regimes"])
        lines += ["", f"**Квартал шока режима {names}.** Закрываемый квартал лежит в году шока прежней книги: прогноз "
                  "наблюдения двугорбый — шок либо случился, либо нет. Ожидаемый отчёт несёт долю шока (CoR "
                  f"{_pct(exp['cor'], 3)} — смесь, в которой с вероятностью {_pct(calm['shock_probability'], 1)} "
                  "стоит CoR " + ", ".join(_pct(v, 2) for v in calm["shock_cor"].values()) + "): такого числа не ждёт "
                  "ни один режим, и «отчётом без новостей» он не является — фильтр читает его как стресс (М§12). "
                  f"Отчёт без шока — ожидание прочих режимов, CoR {_pct(calm['observation']['cor'], 3)}, ЧПМ "
                  f"{_pct(calm['observation']['nim'], 3)}: строка «обучение» — {h['point'] - a['point']:+.2f} ₽ точки "
                  f"(на ожидаемом отчёте {plus['point'] - a['point']:+.2f} ₽), вероятности режимов "
                  + _fmt(calm["posterior"]) + ". В журнал версии книги — оба числа."]
    if mech:
        worst = sorted(mech["rows"], key=lambda r: -abs(r["rel"]))[:5]
        lines += ["", "### Механика по клеткам", "",
                  f"Клеток {len(mech['rows'])}; наибольшее изменение стоимости клетки на своём пути — "
                  f"{_pct(mech['worst']['rel'], 4)} ({mech['worst']['cell']}); допуск {_pct(mech['tolerance'], 1)} — "
                  + ("выполнен." if mech["ok"] else "НАРУШЕН.")]
        if mech["rearmed"]:
            lines.append(f"Год якоря сменился: клетки режима {', '.join(mech['rearmed'])} «перезаряжены» (год шока "
                         "сдвинут, М§3.2) — их путь другой по определению, в допуск они не входят.")
        lines += ["", "| клетка | V прежняя, млрд ₽ | V на своём пути | изменение |", "|---|---|---|---|"]
        lines += [f"| {r['cell']}{' (перезаряжена)' if r['rearmed'] else ''} | {r['old']:.1f} | {r['new']:.1f} | "
                  f"{_pct(r['rel'], 4)} |" for r in worst]

    lines += ["", "## Заголовок — медиана полосы", ""]
    if result.get("band_draws"):
        lines += [f"{result['band_draws']} прогонов на книгу, одни точки гиперкуба (сравнение парное); шаг печати "
                  f"{result['print_step']:g} ₽. У медианы механика и выпуклость — одной строкой.", "",
                  "| шаг | P10 | медиана | P90 | P(центр < рынка) |", "|---|---|---|---|---|"]
        before = None
        for key, title in BAND_TITLES:
            b = steps[key]["band"]
            if before is None:
                lines.append(f"| {title} | {b['p10']:.1f} | {b['median']:.1f} | {b['p90']:.1f} | {b['p_below']:.1%} |")
            else:
                lines.append(f"| {title} | " + " | ".join(
                    f"{b[k]:.1f} ({b[k] - before[k]:+.1f})" for k in ("p10", "median", "p90"))
                    + f" | {b['p_below']:.1%} ({(b['p_below'] - before['p_below']) * 100:+.1f} п.п.) |")
            before = b
    else:
        lines.append("Не считалась: разложение печатаемого заголовка — ключом `--band ПРОГОНОВ` (четыре полосы).")
    checks = result.get("checks")
    if checks:
        was, now = checks["book"], checks["candidate"]
        lines += ["", "## Проверки ядра на кандидате", ""]
        if now["error"]:
            lines.append(f"**Проверки не посчитаны:** {now['error']}")
        else:
            lines.append("Инварианты: " + ("выполнены все." if not now["invariants"] else "НАРУШЕНЫ — " + "; ".join(
                f"`{name}`: {message}" for name, message in now["invariants"])))
            names = list(dict.fromkeys(list(was["gates"]) + list(now["gates"])))
            if names:
                lines += ["", "Сработавшие гейты (масса — доля вероятности клеток слоя «свой взгляд»); каждому на "
                          "новой книге нужно действующее объяснение — `gate_explanations.yaml`, решение книги:", "",
                          "| гейт | прежняя книга | кандидат | сообщение на кандидате |", "|---|---|---|---|"]
                lines += [f"| `{n}` | {_mass(was['gates'].get(n))} | {_mass(now['gates'].get(n))} | "
                          f"{now['gates'][n][1] if n in now['gates'] else 'не сработал'} |" for n in names]
            else:
                lines.append("Сработавших гейтов нет.")
            if was["error"]:
                lines += ["", f"На прежней книге проверки не посчитаны: {was['error']}"]
    if result.get("facts_check") is not None:
        lines += ["", "## Сверка фактов кандидата с пересобранными фактами", ""]
        if result["facts_check"]:
            lines += ["| поле якоря | кандидат | пересобранные факты |", "|---|---|---|"]
            lines += [f"| `{k}` | {_fmt(a)} | {_fmt(b)} |" for k, a, b in result["facts_check"]]
        else:
            lines.append("Расхождений нет: ядро читает из обоих наборов одно и то же состояние якоря.")
    lines += ["", "## Предупреждения и ручные шаги", ""] + [f"- {w}" for w in cand.warnings]
    lines += ["", "Механика ≈ 0 — инвариант `tests/test_reanchor*.py` (М§15.2): клетка на своём пути сохраняет "
              "стоимость в пределах 0,1 %. Строка «выпуклость» — неравенство Йенсена: отчёт один на все клетки, "
              "а слой «рыночные ставки как есть» ждал путь своего мира.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ шаблон книги


def _bare(text: str) -> bool:
    return bool(text) and text.isascii() and text.replace("_", "a").isalnum() and not text[0].isdigit() \
        and text.lower() not in ("null", "true", "false", "yes", "no", "on", "off")


def _flow(value: Any) -> str:
    """Значение в строку потока YAML (ключи-периоды в кавычках, как в шаблоне книги)."""
    if isinstance(value, Mapping):
        return "{" + ", ".join((str(k) if _bare(str(k)) else json.dumps(str(k), ensure_ascii=False))
                               + ": " + _flow(v) for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return str(value) if _bare(str(value)) else json.dumps(str(value), ensure_ascii=False)


def _span(node: yaml.Node) -> tuple[int, int]:
    """Границы узла в тексте; у блочной коллекции конец — конец последнего элемента (без хвоста
    из пустых строк и комментариев)."""
    if isinstance(node, yaml.ScalarNode) or getattr(node, "flow_style", False) or not node.value:
        return node.start_mark.index, node.end_mark.index
    last = node.value[-1]
    return node.start_mark.index, _span(last[1] if isinstance(last, tuple) else last)[1]


def _find(node: yaml.Node | None, path: Path_) -> yaml.Node | None:
    for part in path:
        if isinstance(part, tuple):
            if not isinstance(node, yaml.SequenceNode):
                return None
            node = next((item for item in node.value if isinstance(item, yaml.MappingNode) and any(
                k.value == part[0] and getattr(v, "value", None) == part[1] for k, v in item.value)), None)
        else:
            if not isinstance(node, yaml.MappingNode):
                return None
            node = next((v for k, v in node.value if str(k.value) == part), None)
        if node is None:
            return None
    return node


def _value_at(data: Any, path: Path_) -> Any:
    for part in path:
        if isinstance(part, tuple):
            data = next(x for x in data if isinstance(x, Mapping) and x.get(part[0]) == part[1])
        else:
            data = data[part]
    return data


def patch_template(text: str, cand: Reanchored) -> str:
    """Шаблон книги с правками кандидата: меняется только текст изменившихся значений,
    комментарии остаются. Результат сверяется с кандидатом целиком; расхождение — отказ."""
    root = yaml.compose(text)
    edits: dict[Path_, tuple[int, int, str]] = {}
    for path, _old, new in cand.changes:
        target = path
        node = _find(root, target)
        while node is None or new is None:           # ключ добавлен или убран — правится родитель
            target = target[:-1]
            if not target:
                raise ReportError(f"шаблон книги: нет узла для правки {dotted(path)}")
            node = _find(root, target)
            new = True
        edits[target] = (*_span(node), _flow(_value_at(cand.data, target)))
    out = text
    chosen = [p for p in edits if not any(p[:n] in edits for n in range(1, len(p)))]
    for path in sorted(chosen, key=lambda p: -edits[p][0]):
        start, stop, new_text = edits[path]
        out = out[:start] + new_text + out[stop:]
    got, want = _plain(yaml.safe_load(out)), _plain(cand.data)
    for w in want["worlds"].get("ids", []):          # блоки миров и надстройку вписывает сборка книги
        if w not in got["worlds"]:
            want["worlds"].pop(w, None)
    if "worlds_bank" not in got:
        want.pop("worlds_bank", None)
    if _get(got, "worlds.overlay.sha256") is None:
        want["worlds"]["overlay"]["sha256"] = None
    if got != want:
        raise ReportError("шаблон книги после правок не равен кандидату — правки шаблона переносятся "
                          "вручную по таблице «что сдвинулось»")
    return out


# ------------------------------------------------------------------ сверка фактов


def compare_facts(book: Book, candidate: Facts, other: Facts, tol: float = 1e-6) -> list[tuple[str, Any, Any]]:
    """Состояние якоря, которое ядро читает из фактов кандидата и из пересобранных фактов
    (`build_facts.py --anchor`): поля, разошедшиеся больше допуска."""
    books = book.get("nii.books")
    a, b = asdict(anchor_facts(candidate, books)), asdict(anchor_facts(other, books))
    out: list[tuple[str, Any, Any]] = []

    def walk(x: Any, y: Any, where: str) -> None:
        if isinstance(x, Mapping) and isinstance(y, Mapping):
            for k in sorted(set(x) | set(y), key=str):
                if where == "pnl" and (k not in x or k not in y):
                    continue                     # история ОПУ разной глубины — не состояние якоря
                walk(x.get(k), y.get(k), f"{where}.{k}" if where else str(k))
        elif _num(x) and _num(y):
            if abs(x - y) > tol * max(1.0, abs(x), abs(y)):
                out.append((where, x, y))
        elif x != y:
            out.append((where, x, y))

    walk(a, b, "")
    return out


# ------------------------------------------------------------------ запись и запуск


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def check_out(out: Path) -> None:
    """Внутри репозитория инструмент пишет только под `var/`: кандидат — не канон, а всё остальное
    дерево рабочей копии либо канон (`data/`, `model/`, `docs/`), либо уходит в git, CI и на
    витрину (`tests/`, `ops/`, `web/`…). Вне репозитория — без ограничений."""
    if _inside(out, ROOT) and not _inside(out, ROOT / WRITABLE):
        where = out.resolve().relative_to(ROOT.resolve()).parts
        raise ReportError(
            f"запись в {out} — внутри репозитория вне {WRITABLE}/ ({where[0] + '/' if where else 'корень'}): "
            "кандидата в канон переносят владельцы книги и фактов (docs/REANCHOR.md), инструмент туда не "
            f"пишет; каталог кандидата — под {WRITABLE}/ или вне рабочей копии")


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")


def _json(tree: Any) -> str:
    return json.dumps(tree, ensure_ascii=False, indent=1) + "\n"


def write_candidate(out: Path, book: Book, result: Mapping, template: Path | None = None) -> None:
    """Кандидат книги и фактов, ожидаемый отчёт, подмена наблюдения (если была) и REANCHOR.md — в каталог
    `out`. В режиме `resolve` (диагностика) — только ожидаемый отчёт и REANCHOR.md: такого кандидата в канон
    не переносят."""
    check_out(out)
    cand: Reanchored = result["candidate"]
    if result["transmission"] == RESOLVE:
        out.mkdir(parents=True, exist_ok=True)
        _write(out / "expected.json", _json(result["expected"]))
        _write(out / "REANCHOR.md", report_text(book, result))
        return
    text = yaml.safe_dump(cand.data, allow_unicode=True, sort_keys=False, width=100000)
    if _plain(yaml.safe_load(text)) != _plain(cand.data):
        raise ReportError("кандидат не переживает YAML туда-обратно — отказ записи")
    patched = None
    if template is not None and Path(template).exists():
        try:
            patched = patch_template(Path(template).read_text(encoding="utf-8"), cand)
        except (ReportError, yaml.YAMLError, StopIteration) as exc:
            cand.warnings.append(f"шаблон книги не записан: {exc or 'узел шаблона не найден'}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "facts").mkdir(exist_ok=True)
    _write(out / "assumptions.yaml", text)
    _write(out / "assumptions.json", _json(cand.data))
    if patched is not None:
        _write(out / "assumptions_template.yaml", patched)
    for name, content in cand.facts.files.items():
        _write(out / "facts" / f"{name}.json", _json(content))
    _write(out / "expected.json", _json(result["expected"]))
    if result.get("observation_override"):
        _write(out / "observation.json", _json(result["observation_override"]))
    _write(out / "REANCHOR.md", report_text(book, result))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", type=Path, default=BOOK_PATH, help="машинная книга (по умолчанию — канон)")
    ap.add_argument("--facts-dir", type=Path, default=FACTS_DIR, help="факты прежнего якоря")
    ap.add_argument("--template", type=Path, default=TEMPLATE, help="шаблон книги для кандидата шаблона")
    ap.add_argument("--expected", type=Path, metavar="ФАЙЛ",
                    help="записать ожидаемый отчёт закрываемого квартала и выйти")
    ap.add_argument("--facts", type=Path, metavar="ФАЙЛ", help="файл отчёта квартала")
    ap.add_argument("--out", type=Path, help="каталог кандидата (по умолчанию var/reanchor/<квартал>/)")
    ap.add_argument("--valuation-date", help="дата оценки разложения и кандидата")
    ap.add_argument("--version", help="версия кандидата (по умолчанию «<версия>+<квартал>»)")
    ap.add_argument("--band", type=int, metavar="ПРОГОНОВ",
                    help="разложение печатаемой медианы на ПРОГОНОВ прогонах каждой из четырёх книг")
    ap.add_argument("--no-cells", action="store_true",
                    help="не считать строку «механика» по клеткам (быстрее; допуск механики не проверяется)")
    ap.add_argument("--transmission", choices=TRANSMISSION_MODES, default=KEEP,
                    help="keep (по умолчанию, режим процедуры) — скаляры пути σ0_A, σ0_L, φ_A, φ_L прежней книги, "
                         "цели A-N2, A-N3 и обе доли пересчитываются на структуру нового якоря; resolve — только "
                         "диагностика: цели прежние, скаляры решаются заново, кандидат не пишется")
    ap.add_argument("--compare-facts", type=Path, metavar="КАТАЛОГ",
                    help="сверить факты кандидата с пересобранными фактами нового якоря")
    ap.add_argument("--observation", type=Path, metavar="ФАЙЛ",
                    help="подмена наблюдения A-P2u закрытого квартала решением книги (YAML или JSON): cor, se_cor, "
                         "nim, se_nim — доли единицы, все четыре обязательны (null — величина не наблюдается); "
                         "reason — причина, обязательна; period — необязательно. Идёт только в кандидата на "
                         "отчёте (строка «сюрприз факта»); факты остаются числами отчёта (docs/REANCHOR.md §3.1)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        facts = load_facts(args.facts_dir)
        book = load_book(args.book, facts=facts)
        if args.expected:
            check_out(args.expected)
            report = expected_report(book, facts)
            args.expected.parent.mkdir(parents=True, exist_ok=True)
            _write(args.expected, _json(report))
            print(f"ожидаемый отчёт за {report['period']} → {args.expected}")
            return 0
        if not args.facts:
            ap.error("нужен --facts ФАЙЛ (или --expected ФАЙЛ)")
        report = read_report(args.facts)
        out = args.out or DEFAULT_OUT / str(report.get("period", "report"))
        check_out(out)
        override = None
        if args.observation:
            override = read_observation(args.observation)
            check_observation(override, str(report.get("period")),       # отказ до расчёта
                              report.get("mgmt") if isinstance(report.get("mgmt"), dict) else None)
        result = attribution(book, facts, report, valuation_date=args.valuation_date, version=args.version,
                             band_draws=args.band, cells=not args.no_cells, transmission=args.transmission,
                             observation=override)
        if args.compare_facts:
            result["facts_check"] = compare_facts(book, result["candidate"].facts, load_facts(args.compare_facts))
        write_candidate(out, book, result, args.template)
    except (ReportError, BookError, FactsError) as exc:
        print(f"reanchor: {exc}", file=sys.stderr)
        return 1
    print(report_text(book, result))
    mech = result.get("mechanics")
    if mech and not mech["ok"]:
        print(f"reanchor: механика вне допуска ({mech['worst']['cell']}: {mech['worst']['rel']:+.4%}) — "
              + (f"диагностика режима resolve, в {out} — только REANCHOR.md" if args.transmission == RESOLVE
                 else f"кандидат в {out} только для разбора"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
