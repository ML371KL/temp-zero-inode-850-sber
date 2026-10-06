"""Закрытая схема книги (docs/MODEL.md, приложение A) и громкий отказ.

Ядро читает книгу только через известные пути. Незнакомый ключ, пропущенный
ключ, `null` в пути, который ядро читает (с учётом выключателей, М§0.1),
неразбираемый ключ траектории (М§0.4), незнакомые `kind`, `dist`, `base`,
`shortfall_rule`, `cor_basis` и включённый нереализованный модуль — отказ
`BookError` до расчёта. Схема — снимок приложения A, а не вывод из текущей
книги: опечатка в новой книге не узаконит сама себя.

Подстановки путей: `<W>` — `worlds.ids`, `<r>` — `regimes.ids`, `<s>` —
`capital.reg_scenarios.ids`, `<b>` — ключи `nii.books` (у `checks.lt_spread_floor` —
кредитные книги: с `sector`), `<t>` — `meta.company.tickers`, `<y>` — год.

Правила сверх формы (М прил. A): `nii.sigma0_from` — год от `first_period` до
`last_period`; `other.misc_quarter_shares` в сумме 1; `dividends.excess.ramp_years` ≥ 1;
оси вне полосы (`valuation.uncertainty.off_band_axes`) — только `value` и `shift`; ось
вида `value` на ключе года траектории, у которой в этом году есть квартальные ключи, —
отказ (ключ года там — среднее кварталов, М§0.4). Режим с `shock_year_offset` (кризис):
сдвиг ≥ 1, квартальный профиль CoR, период разового убытка и первый год
`loan_growth_override` лежат в году шока = год якоря + сдвиг (М§3.2). `credit.fv_loans_ref`
— режим книги; `nii.sigma0_split` ∈ [0; 1], и на стороне, куда ложится сдвиг, есть книги с
`lt_shift` (М§4.5); `nii.phi_split` ∈ [0; 1], и на стороне, куда ложится сжатие, есть книги с
`phi`, а у обеих книг средств ФЛ флаг `phi` одинаков (М§4.5); `joint.regime_update.window_obs`
≥ 1 и `floor_share` ∈ [0; 1] (М§12); окно рангов `valuation.sensitivities.rank_window` —
0 ≤ нижний < верхний ≤ 1 (М§10); `valuation.next_report.value_tol` > 0 (М§13); единица оси
`unit` — код П§0.2; наблюдение квартала не позже якоря несёт замороженные `mu_cor`, `mu_nim`
наблюдённых величин, наблюдение прогнозного квартала — нет (М§12).

Модуль — низ графа зависимостей ядра: здесь же живут `BookError` и
`FactsError` (их реэкспортирует `model.book`).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable, Iterable, Mapping


class BookError(ValueError):
    """Книга нарушает схему или правило методики (громкий отказ)."""


class FactsError(ValueError):
    """Нет файла, узла или числа факта, который нужен ядру."""


# ------------------------------------------------------------ ключи времени

QUARTER_KEY = re.compile(r"^(\d{4})Q([1-4])$")
HALF_KEY = re.compile(r"^(\d{4})H([12])$")
YEAR_KEY = re.compile(r"^(\d{4})$")
PERIOD = QUARTER_KEY
DATE_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRAJ_WORDS = ("LT", "LT_from")


def parse_time_key(key: str) -> tuple[str, int, int]:
    """Ключ траектории → (вид, год, номер): ('Q', год, квартал), ('H', год,
    полугодие), ('Y', год, 0). Неразбираемый — BookError."""
    m = QUARTER_KEY.match(key)
    if m:
        return "Q", int(m.group(1)), int(m.group(2))
    m = HALF_KEY.match(key)
    if m:
        return "H", int(m.group(1)), int(m.group(2))
    m = YEAR_KEY.match(key)
    if m:
        return "Y", int(m.group(1)), 0
    raise BookError(f"ключ траектории «{key}» не разбирается ни одним правилом М§0.4")


def is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ------------------------------------------------------------ узлы схемы


class Spec:
    """Узел схемы: проверяет значение и перечисляет шаблоны путей."""

    nullable = False

    def check(self, value: Any, path: str, ctx: "Ctx") -> None:
        raise NotImplementedError

    def templates(self, prefix: str) -> Iterable[str]:
        yield prefix


class Leaf(Spec):
    def __init__(self, test: Callable[[Any], bool], what: str, nullable: bool = False):
        self.test, self.what, self.nullable = test, what, nullable

    def check(self, value, path, ctx):
        if value is None:
            if not (self.nullable or ctx.switched_off(path)):
                ctx.err(f"{path}: null в читаемом пути (null ≠ 0)")
            return
        if not self.test(value):
            ctx.err(f"{path}: ожидается {self.what}, в книге {value!r}")


class Enum(Spec):
    def __init__(self, *values: str):
        self.values = frozenset(values)

    def check(self, value, path, ctx):
        if value is None:
            if not ctx.switched_off(path):
                ctx.err(f"{path}: null в читаемом пути")
            return
        if value not in self.values:
            ctx.err(f"{path}: незнакомое значение {value!r} (известны: {', '.join(sorted(self.values))})")


class Traj(Spec):
    """Траектория эмитента: число или словарь ключей времени (М§0.4)."""

    def check(self, value, path, ctx):
        if value is None:
            if not ctx.switched_off(path):
                ctx.err(f"{path}: null в читаемом пути")
            return
        if is_number(value):
            return
        if not isinstance(value, Mapping) or not value:
            ctx.err(f"{path}: траектория — число или непустой словарь, в книге {value!r}")
            return
        ctx.trajectory(path, value, world=False)


class WorldTraj(Spec):
    """Траектория мира: полугодовые ключи до last_period включительно."""

    def check(self, value, path, ctx):
        if not isinstance(value, Mapping) or not value:
            ctx.err(f"{path}: траектория мира — словарь полугодий, в книге {value!r}")
            return
        ctx.trajectory(path, value, world=True)


class Years(Spec):
    """Словарь-переопределение по годам (не траектория, М§0.4)."""

    def __init__(self, item: Spec | None = None):
        self.item = item or NUM

    def check(self, value, path, ctx):
        if not isinstance(value, Mapping):
            ctx.err(f"{path}: словарь по годам, в книге {value!r}")
            return
        for k, v in value.items():
            if not YEAR_KEY.match(str(k)):
                ctx.err(f"{path}: ключ «{k}» — не год")
                continue
            self.item.check(v, f"{path}.{k}", ctx)

    def templates(self, prefix):
        yield f"{prefix}.<y>"


class ListOf(Spec):
    def __init__(self, item: Spec, min_len: int = 0, exact: int | None = None):
        self.item, self.min_len, self.exact = item, min_len, exact

    def check(self, value, path, ctx):
        if value is None:
            if not ctx.switched_off(path):
                ctx.err(f"{path}: null в читаемом пути")
            return
        if not isinstance(value, (list, tuple)):
            ctx.err(f"{path}: ожидается список, в книге {value!r}")
            return
        if self.exact is not None and len(value) != self.exact:
            ctx.err(f"{path}: ожидается список из {self.exact} элементов")
        if len(value) < self.min_len:
            ctx.err(f"{path}: список короче {self.min_len}")
        for i, v in enumerate(value):
            self.item.check(v, f"{path}[{i}]", ctx)

    def templates(self, prefix):
        if isinstance(self.item, (Obj, MapOf)):
            yield from self.item.templates(f"{prefix}[]")
        else:
            yield f"{prefix}[]"


class Obj(Spec):
    """Словарь с известными ключами: `fields` — ключ → (узел, обязателен)."""

    def __init__(self, fields: Mapping[str, Spec], optional: Iterable[str] = ()):
        self.fields = dict(fields)
        self.optional = frozenset(optional)

    def check(self, value, path, ctx):
        if value is None:
            if not ctx.switched_off(path):
                ctx.err(f"{path}: null в читаемом пути")
            return
        if not isinstance(value, Mapping):
            ctx.err(f"{path}: ожидается словарь, в книге {value!r}")
            return
        for k in value:
            if k not in self.fields:
                ctx.err(f"{_join(path, k)}: незнакомый ключ (известны: {', '.join(sorted(self.fields))})")
        for k, spec in self.fields.items():
            p = _join(path, k)
            if k not in value:
                if k not in self.optional and not ctx.switched_off(p):
                    ctx.err(f"{p}: нет ключа")
                continue
            spec.check(value[k], p, ctx)

    def templates(self, prefix):
        for k, spec in self.fields.items():
            yield from spec.templates(_join(prefix, k))


class MapOf(Spec):
    """Словарь, ключи которого — множество подстановки (`<W>`, `<r>` …)."""

    def __init__(self, placeholder: str, item: Spec, fixed: Mapping[str, Spec] | None = None,
                 exact: bool = True, label: str | None = None):
        self.placeholder, self.item = placeholder, item
        self.fixed = dict(fixed or {})
        self.exact = exact
        self.label = label or placeholder      # имя подстановки в шаблоне пути (прил. A)

    def check(self, value, path, ctx):
        if value is None:
            if not ctx.switched_off(path):
                ctx.err(f"{path}: null в читаемом пути")
            return
        if not isinstance(value, Mapping):
            ctx.err(f"{path}: ожидается словарь, в книге {value!r}")
            return
        ids = ctx.ids.get(self.placeholder)
        for k, spec in self.fixed.items():
            if k not in value:
                ctx.err(f"{_join(path, k)}: нет ключа")
            else:
                spec.check(value[k], _join(path, k), ctx)
        for k, v in value.items():
            if k in self.fixed:
                continue
            if ids is not None and k not in ids:
                ctx.err(f"{_join(path, k)}: незнакомый ключ (подстановка {self.placeholder}: "
                        f"{', '.join(ids)})")
                continue
            self.item.check(v, _join(path, k), ctx)
        if self.exact and ids is not None:
            for k in ids:
                if k not in value:
                    ctx.err(f"{_join(path, k)}: нет ключа")

    def templates(self, prefix):
        for k, spec in self.fixed.items():
            yield from spec.templates(_join(prefix, k))
        yield from self.item.templates(_join(prefix, self.label))


class OneOf(Spec):
    def __init__(self, *options: Spec):
        self.options = options

    def check(self, value, path, ctx):
        for opt in self.options:
            probe = Ctx(ctx.data, ctx.ids, ctx.off, ctx.last_year)
            opt.check(value, path, probe)
            if not probe.errors:
                ctx.traj.extend(probe.traj)
                return
        ctx.err(f"{path}: значение {value!r} не подходит ни под один вид")

    def templates(self, prefix):
        yield prefix


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else str(key)


def _is_date(x: Any) -> bool:
    return isinstance(x, date) or (isinstance(x, str) and DATE_KEY.match(x) is not None)


NUM = Leaf(is_number, "число")
INT = Leaf(lambda x: isinstance(x, int) and not isinstance(x, bool), "целое")
YEAR = Leaf(lambda x: isinstance(x, int) and not isinstance(x, bool) and x > 0, "год (целое)")
STR = Leaf(lambda x: isinstance(x, str) and x != "", "строка")
BOOL = Leaf(lambda x: isinstance(x, bool), "true | false")
DATE = Leaf(_is_date, "дата ГГГГ-ММ-ДД")
PERIOD_S = Leaf(lambda x: isinstance(x, str) and QUARTER_KEY.match(x) is not None, "квартал ГГГГQn")
ANCHOR_NUM = Leaf(lambda x: is_number(x) or x == "anchor", "число или anchor")
PAIR = Leaf(lambda x: isinstance(x, (list, tuple)) and len(x) == 2 and all(map(is_number, x))
            and x[0] <= x[1], "[мин, макс]")
NUM_OR_NULL = Leaf(is_number, "число или null", nullable=True)
STR_LIST = ListOf(STR)
REFS = ("key", "ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y")
OFZ = ("ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y")
SHARE_MAP = Leaf(lambda x: isinstance(x, Mapping) and x and all(map(is_number, x.values())),
                 "словарь долей")

# Коды единиц строк выпуска (П§0.2; словарь витрины): pct и share — доля, pp — сдвиг доли, bp — базисные
# пункты, bn — млрд ₽, rub и price — ₽, number — число, mix — доля смеси, dict — словарь долей, count — штуки,
# version — как есть, years — годы, level — уровень индекса, date — дата, period — период.
UNIT_CODES = ("pct", "share", "pp", "bn", "rub", "number", "mix", "dict", "count", "version", "bp", "years",
              "level", "price", "date", "period")
# Слова единиц книги (поле `unit` строк обратного расчёта) → коды.
UNIT_WORDS = {"%": "pct", "доля": "pct", "доли": "dict", "п.п.": "pp", "": "number", "млрд ₽": "bn", "₽": "rub",
              "записей": "count", "version": "version", "лет": "years"}
# Пара книг средств ФЛ (М§4.1): (текущие, срочные) — их доля задаётся мостиком текущих счетов.
RETAIL_BOOKS = ("retail_current", "retail_term")

BOOK_FIELDS = {
    "side": Enum("asset", "liability"),
    "ref": Enum(*REFS),
    "rho": NUM,
    "spread": Traj(),
    "beta": NUM,
    "phi": BOOL,
    "lt_shift": BOOL,
    "sector": Enum("corporate", "mortgage", "retail_other"),
    "cor_segment": Enum("corporate", "retail"),
    "balancing": BOOL,
    "fv_share": ANCHOR_NUM,
}
NII_BOOK = Obj(BOOK_FIELDS, optional=("beta", "phi", "sector", "cor_segment", "balancing", "fv_share"))

AXIS = Obj({
    "name": STR,
    "kind": Enum("value", "shift", "dict"),
    "paths": ListOf(STR, min_len=1),
    "low": OneOf(NUM, SHARE_MAP),
    "high": OneOf(NUM, SHARE_MAP),
    "dist": Enum("triangular"),
    "unit": Enum(*UNIT_CODES),
}, optional=("unit",))
REVERSE_AXIS = Obj({
    "name": STR,
    "kind": Enum("value", "shift", "mix"),
    "paths": ListOf(STR, min_len=1),
    "search": PAIR,
    "range": PAIR,
    "unit": Leaf(lambda x: isinstance(x, str), "строка"),
    "toward": SHARE_MAP,
}, optional=("toward",))

WORLD = Obj({
    "name": STR,
    "key_rate": WorldTraj(), "cpi": WorldTraj(), "wage_growth": WorldTraj(),
    "ofz_1y": WorldTraj(), "ofz_3y": WorldTraj(), "ofz_5y": WorldTraj(), "ofz_10y": WorldTraj(),
    "real_key": WorldTraj(),
    "zero_curve": Obj({"1": NUM, "3": NUM, "5": NUM, "10": NUM, "LT": NUM}),
    "lt_inflation": NUM,
})

REGIME = Obj({
    "name": STR,
    "cor": Traj(),
    "nim_shift": Traj(),
    "loan_growth_adj": Traj(),
    "loan_growth_override": Years(),
    "one_off_loss": Obj({"period": PERIOD_S, "amount": NUM}),
    "shock_year_offset": INT,
}, optional=("loan_growth_override", "one_off_loss", "shock_year_offset"))

SCENARIO = Obj({
    "name": STR,
    "conservation": Traj(), "sifi": Traj(), "ccyb": Traj(),
    "deduction_pp": Obj({"n20_0": Traj(), "n1_1": Traj()}),
})

SPEC = Obj({
    "meta": Obj({
        "version": STR, "date": DATE, "facts_date": DATE, "anchor_period": PERIOD_S,
        "first_period": PERIOD_S, "last_period": PERIOD_S, "valuation_date": DATE,
        "curve_as_of": DATE, "market_price": MapOf("<t>", NUM), "basis": STR,
        "step": Enum("quarter"), "day_count": Enum("act365"),
        "company": Obj({"name": STR, "tickers": ListOf(STR, min_len=1), "main_ticker": STR,
                        "cbr_regnum": Leaf(lambda x: isinstance(x, (int, str)) and not isinstance(x, bool),
                                           "регномер"),
                        "fiscal_year_end": STR,
                        "share_classes": MapOf("<t>", Enum("ordinary", "preferred"))}),
    }),
    "worlds": MapOf("<W>", WORLD, fixed={
        "source": Obj({"record": STR, "origin": STR, "record_asof": DATE, "curve_date": DATE,
                       "sha256": STR, "recipe": STR_LIST}),
        "build": Obj({"script": STR, "fields": STR_LIST}),
        "quarter_rule": Enum("half_year_value"),
        "ids": ListOf(STR, min_len=1),
        "overlay": Obj({"file": STR, "sha256": STR, "fields": STR_LIST}),
    }),
    "worlds_bank": MapOf("<W>", Obj({
        "credit_growth": Obj({"corporate": Years(), "mortgage": Years(), "retail_other": Years()}),
        "funds_growth": Obj({"retail": Years(), "corporate": Years()}),
        "nominal_gdp_growth": Years(),
    })),
    "joint": Obj({
        "world_prob": MapOf("<W>", NUM),
        "world_prob_market_implied": MapOf("<W>", NUM),
        "macro_neutral_world": STR,
        "own_macro_confidence": NUM,
        "regime_prob": MapOf("<r>", NUM),
        "reg_prob_given_regime": MapOf("<r>", MapOf("<s>", NUM)),
        "regime_update": Obj({
            "observables": Obj({"cor": Obj({"sigma_pp": NUM, "rho_q": NUM}),
                                "nim": Obj({"sigma_pp": NUM, "rho_q": NUM})}),
            "max_shift_pp": NUM,
            "window_obs": INT,
            "floor_share": NUM,
            "shrink_by_se": BOOL,
            "observations": ListOf(Obj({
                "period": PERIOD_S, "cor": NUM_OR_NULL, "nim": NUM_OR_NULL,
                "se_cor": NUM_OR_NULL, "se_nim": NUM_OR_NULL, "basis": Enum("mgmt", "engine"),
                "mu_cor": MapOf("<r>", NUM), "mu_nim": MapOf("<r>", NUM),
            }, optional=("mu_cor", "mu_nim"))),
        }),
    }),
    "regimes": MapOf("<r>", REGIME, fixed={"ids": ListOf(STR, min_len=1),
                                          "cor_basis": Enum("mgmt", "engine"),
                                          "near_nim_shift": Traj()}),
    "credit": Obj({
        "kappa": NUM, "real_rate_lag_q": INT,
        "segment_relative": Obj({"enabled": BOOL, "corporate": NUM, "retail": NUM}),
        "fv_loans_factor": NUM, "fv_loans_ref": STR, "allowance_ratio": ANCHOR_NUM,
    }),
    "nii": Obj({
        "books": MapOf("<b>", NII_BOOK),
        "transmission": Obj({"target": NUM, "reference_world": STR, "fd_step": NUM,
                             "weights": Enum("anchor")}),
        "nim_lt_target_mgmt": NUM,
        "sigma0_split": NUM,
        "phi_split": NUM,
        "sigma0_from": YEAR,
        "retail_current_share": Obj({"c_ref": ANCHOR_NUM, "key_ref": ANCHOR_NUM, "psi": NUM,
                                     "rho": NUM, "bounds": PAIR}),
        "dia_rate": NUM,
    }),
    "volumes": Obj({
        "guidance_year": YEAR,
        "guidance_growth": Obj({"corporate": NUM, "retail": NUM}),
        "loan_share_drift": NUM, "funds_share_drift": NUM, "share_drift_until": YEAR,
        "lt_from": YEAR, "wholesale_to_funds": ANCHOR_NUM, "liquid_min_share": NUM,
        "securities_share_of_liquid": ANCHOR_NUM, "other_assets_to_loans": ANCHOR_NUM,
        "other_liabilities_to_loans": ANCHOR_NUM,
    }),
    "fees": Obj({"growth_override": Years(), "growth_vs_wages": Traj()}),
    "opex": Obj({"real_growth": Traj()}),
    "noncore": Obj({"result_real": Traj(), "price_base_year": YEAR}),
    "other": Obj({"insurance_growth_vs_wages": Traj(), "misc_net_real": Traj(),
                  "misc_quarter_shares": Obj({"1": NUM, "2": NUM, "3": NUM, "4": NUM}),
                  "fvtpl_bond_reval": BOOL}),
    "pnl": Obj({"nci_share": NUM}),
    "tax": Obj({"statutory": Traj(), "effective_gap": NUM,
                "one_off": Obj({"year": YEAR, "amount": NUM, "prob": NUM})}),
    "oci": Obj({"fvoci_share": ANCHOR_NUM, "fvoci_duration": NUM, "fvoci_maturity": NUM,
                "fvoci_tenor": Enum(*OFZ), "fvtpl_bond_share": ANCHOR_NUM,
                "fvtpl_bond_duration": NUM}),
    "equity": Obj({"other_movements": Traj()}),
    "capital": Obj({
        "minimum": Obj({"n20_0": NUM, "n1_1": NUM}),
        "mgmt_buffer": Obj({"n20_0": NUM, "n1_1": NUM}),
        "reg_scenarios": MapOf("<s>", SCENARIO, fixed={"ids": ListOf(STR, min_len=1)}),
        "n20": Obj({"deductions_anchor": ANCHOR_NUM, "fvoci_recognition": NUM, "t2": Traj(),
                    "gap_pp": NUM}),
        "n11": Obj({"deductions_anchor": ANCHOR_NUM, "gap_pp": NUM,
                    "audit_cutoffs": ListOf(Leaf(lambda x: x in (1, 2, 3, 4), "номер квартала 1–4"))}),
        "rwa": Obj({
            "density": MapOf("<rwa>", NUM),
            "density_drift_rate": NUM, "density_drift_until": YEAR,
            "fx": Obj({"enabled": BOOL, "share": NUM, "foreign_inflation": NUM}),
        }),
        "recapitalization": Obj({"enabled": BOOL}),
    }),
    "dividends": Obj({
        "policy": Obj({
            "name": STR,
            "base": Enum("ifrs_ni_shareholders", "ifrs_ni_adjusted"),
            "deduct_at1_after_tax": BOOL,
            "payout": Traj(),
            "metric": Enum("n20_0"),
            "threshold": NUM,
            "steps": ListOf(Obj({"payout": Traj(), "threshold": NUM})),
            "shortfall_rule": Enum("residual_above_requirement"),
            "divisor": Enum("issued"),
            "dps_rounding": Enum("ceil_kopeck", "half_up_kopeck"),
            "forecast_rounding": Enum("none"),
        }),
        "payout_deviation": Traj(),
        "excess": Obj({"from_profit_year": YEAR, "epsilon": NUM, "ramp_years": INT}),
        "crisis": Obj({"skip_in_shock_year": BOOL, "catch_up": BOOL}),
        "buyback": Obj({"enabled": BOOL}),
        "calendar": Obj({
            "agm_quarter": Leaf(lambda x: x in (1, 2, 3, 4), "номер квартала 1–4"),
            "reg_deduction_quarter": Leaf(lambda x: x in (1, 2, 3, 4), "номер квартала 1–4"),
            "payment_quarter": Leaf(lambda x: x in (1, 2, 3, 4), "номер квартала 1–4"),
            "checkpoints": ListOf(Leaf(lambda x: x in (1, 2, 3, 4), "номер квартала 1–4"), min_len=1),
        }),
    }),
    "bridge": Obj({"declared_dividends": Enum("register"), "governance_applies": BOOL}),
    "valuation": Obj({
        "beta_e": NUM, "erp": NUM, "roll_along_forwards": BOOL,
        "governance": Obj({"discount": NUM, "components": ListOf(Obj({
            "id": STR, "name": STR, "value": NUM, "sign": NUM, "basis": STR}))}),
        "terminal": Obj({"real_growth": NUM, "fade": NUM, "excess_capital_multiple": NUM,
                         "timing": Enum("year_end")}),
        "headline": Obj({
            "print_step": NUM, "diagnostics": Enum("median"), "search_tol_rub": NUM,
            "jump_guard": Obj({"median_pct": NUM, "v0_pct": NUM}), "ceiling_x_market": NUM,
        }),
        "sensitivities": Obj({"cor_pp": NUM, "nim_pp": NUM, "roe_pp": NUM, "rank_window": PAIR}),
        "uncertainty": Obj({
            "draws": INT, "seed": INT, "quantiles": ListOf(NUM, min_len=1), "median_draws": INT,
            "reverse_bounds": Enum("follow_center"), "median_refine": INT,
            "axes": ListOf(AXIS),
            "off_band_axes": ListOf(AXIS),
        }),
        "reverse_dcf": Obj({
            "subsample": INT, "axes": ListOf(REVERSE_AXIS),
            "bank_rows": ListOf(Enum("implied_roe_through_cycle", "implied_cost_of_equity",
                                     "market_cap_minus_bv")),
        }),
        "next_report": Obj({"period": PERIOD_S, "cor_values": ListOf(NUM, min_len=1),
                            "nim_values": ListOf(NUM, min_len=1), "search_pad": NUM,
                            "value_tol": NUM}),
    }),
    "checks": Obj({
        "ddm_ri_tol": NUM, "transmission_tol": NUM, "exdate_jump_tol": NUM,
        "pb_by_world": MapOf("<W>", PAIR),
        "roe_range": PAIR, "cor_range": PAIR, "nim_range": PAIR, "cir_range": PAIR,
        "roe_k_spread": Obj({"range": PAIR, "max_world_gap": NUM}),
        "terminal_share": PAIR, "real_rate": PAIR,
        "bridge_drift_pp": Obj({"cor": NUM, "nim": NUM}),
        "nim_path_joint": Obj({"quarters": INT, "tolerance": NUM}),
        "transmission_pairs": PAIR,
        "lt_spread_floor": MapOf("<loan>", NUM, label="<b>"),
        "ni_jump": Obj({"quarters": INT, "yoy": NUM, "key_tol": NUM}),
        "m_crisis_vs_cbr": Obj({"loan_growth": Years(PAIR), "cor_shock_year": NUM, "n20_min": NUM}),
        "manual_overdue_days": Obj({"ifrs_fact": INT, "worlds_after_cbr": INT, "dividend_register": INT}),
        "book_update": Obj({"shift_bp": NUM, "max_age_days": INT}),
    }),
})

# Выключатель → ключи, которые он выключает (М§0.1, прил. A). При выключенном
# ключи не читаются и могут быть null; при включённом null в них — отказ.
SWITCHES: dict[str, tuple[str, ...]] = {
    "credit.segment_relative.enabled": ("credit.segment_relative.corporate",
                                        "credit.segment_relative.retail"),
    "capital.rwa.fx.enabled": ("capital.rwa.fx.share", "capital.rwa.fx.foreign_inflation"),
    "other.fvtpl_bond_reval": ("oci.fvtpl_bond_duration",),
    "capital.recapitalization.enabled": (),
    "dividends.buyback.enabled": (),
}

# Модули особых статей: ключ есть, модуля нет — включение до реализации — отказ.
UNIMPLEMENTED_SWITCHES = ("capital.recapitalization.enabled", "dividends.buyback.enabled")
# Значения, зарезервированные схемой, но не реализованные ядром.
UNIMPLEMENTED_VALUES = {"dividends.policy.base": ("ifrs_ni_adjusted",)}

# Траектории, у которых LT_from и ключи в году last_period запрещены (М§0.4):
# всё, что проверяется узлом Traj (траектории эмитента).

KEYS: tuple[str, ...] = tuple(SPEC.templates(""))


# ------------------------------------------------------------ проверка


class Ctx:
    def __init__(self, data: Mapping, ids: Mapping[str, tuple[str, ...]], off: frozenset[str],
                 last_year: int | None):
        self.data, self.ids, self.off, self.last_year = data, ids, off, last_year
        self.errors: list[str] = []
        self.traj: list[str] = []

    def err(self, message: str) -> None:
        self.errors.append(message)

    def switched_off(self, path: str) -> bool:
        return path in self.off

    def trajectory(self, path: str, value: Mapping, *, world: bool) -> None:
        self.traj.append(path)
        for k, v in value.items():
            k = str(k)
            if k == "LT_from":
                if not (isinstance(v, int) and not isinstance(v, bool)):
                    self.err(f"{path}.LT_from: ожидается год, в книге {v!r}")
                elif not world and self.last_year is not None and v > self.last_year:
                    self.err(f"{path}.LT_from = {v} позже года last_period {self.last_year} (М§0.4)")
                continue
            if v is None:
                self.err(f"{path}.{k}: null в траектории")
                continue
            if not is_number(v):
                self.err(f"{path}.{k}: ожидается число, в книге {v!r}")
                continue
            if k == "LT":
                continue
            try:
                kind, year, _ = parse_time_key(k)
            except BookError as exc:
                self.err(f"{path}: {exc}")
                continue
            if world:
                if kind == "Q":
                    self.err(f"{path}.{k}: у траектории мира — ключи полугодий")
            elif kind in ("Q", "H") and self.last_year is not None and year == self.last_year:
                self.err(f"{path}.{k}: ключ квартала или полугодия в году last_period запрещён (М§0.4)")
        if not world:
            _check_year_means(path, value, self)
        elif self.last_year is not None and f"{self.last_year}H2" not in {str(k) for k in value}:
            self.err(f"{path}: траектория мира не доходит до {self.last_year}H2 (last_period)")


def _check_year_means(path: str, traj: Mapping, ctx: Ctx) -> None:
    """Ключ года при квартальных ключах — среднее года (М§0.4)."""
    quarters: dict[int, list[float]] = {}
    for k, v in traj.items():
        m = QUARTER_KEY.match(str(k))
        if m and is_number(v):
            quarters.setdefault(int(m.group(1)), []).append(float(v))
    for year, vals in quarters.items():
        yk = str(year)
        if yk in traj and len(vals) == 4 and is_number(traj[yk]):
            if abs(sum(vals) / 4 - float(traj[yk])) > 1e-9:
                ctx.err(f"{path}.{yk}: ключ года {traj[yk]} ≠ среднему кварталов {sum(vals) / 4:.6g}")


def _get(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _ids(data: Mapping, path: str, errors: list[str]) -> tuple[str, ...] | None:
    value = _get(data, path)
    if not isinstance(value, (list, tuple)) or not value or not all(isinstance(x, str) for x in value):
        errors.append(f"{path}: нужен непустой список строк")
        return None
    if len(set(value)) != len(value):
        errors.append(f"{path}: повторяющиеся значения")
    return tuple(value)


def switched_off_paths(data: Mapping) -> frozenset[str]:
    """Пути, выключенные выключателями книги (М§0.1)."""
    off: set[str] = set()
    for switch, keys in SWITCHES.items():
        if _get(data, switch) is False:
            off.update(keys)
    return frozenset(off)


def placeholder_ids(data: Mapping, errors: list[str] | None = None) -> dict[str, tuple[str, ...]]:
    errors = [] if errors is None else errors
    ids: dict[str, tuple[str, ...]] = {}
    for key, path in (("<W>", "worlds.ids"), ("<r>", "regimes.ids"),
                      ("<s>", "capital.reg_scenarios.ids"), ("<t>", "meta.company.tickers")):
        got = _ids(data, path, errors)
        if got is not None:
            ids[key] = got
    books = _get(data, "nii.books")
    if isinstance(books, Mapping) and books:
        ids["<b>"] = tuple(str(k) for k in books)
        assets = [str(k) for k, v in books.items()
                  if isinstance(v, Mapping) and v.get("side") == "asset"]
        ids["<rwa>"] = tuple(assets) + ("other_assets",)
        ids["<loan>"] = tuple(str(k) for k, v in books.items() if isinstance(v, Mapping) and "sector" in v)
    else:
        errors.append("nii.books: нужен непустой словарь книг")
    return ids


def _year_of(period: Any) -> int | None:
    if isinstance(period, str):
        m = QUARTER_KEY.match(period)
        if m:
            return int(m.group(1))
    return None


def validate(data: Mapping) -> None:
    """Громкий отказ BookError, если книга нарушает схему (инвариант book_schema)."""
    if not isinstance(data, Mapping):
        raise BookError("книга — не словарь")
    errors: list[str] = []
    ids = placeholder_ids(data, errors)
    off = switched_off_paths(data)
    last_year = _year_of(_get(data, "meta.last_period"))
    ctx = Ctx(data, ids, off, last_year)
    SPEC.check(data, "", ctx)
    errors.extend(ctx.errors)
    errors.extend(_semantic_errors(data, ids))
    if errors:
        shown = errors[:40]
        more = f"\n… и ещё {len(errors) - len(shown)}" if len(errors) > len(shown) else ""
        raise BookError("книга нарушает схему (М прил. A):\n" + "\n".join(shown) + more)


def _semantic_errors(data: Mapping, ids: Mapping[str, tuple[str, ...]]) -> list[str]:
    """Правила сверх формы: модули, книги ЧПД, миры, оси, дисконт за управление."""
    out: list[str] = []
    for switch in UNIMPLEMENTED_SWITCHES:
        if _get(data, switch) is True:
            out.append(f"{switch}: модуль не реализован в ядре — включение запрещено")
    for path, values in UNIMPLEMENTED_VALUES.items():
        if _get(data, path) in values:
            out.append(f"{path} = {_get(data, path)!r}: зарезервировано, ядро не реализует")
    worlds = ids.get("<W>", ())
    for path in ("joint.macro_neutral_world", "nii.transmission.reference_world"):
        value = _get(data, path)
        if isinstance(value, str) and worlds and value not in worlds:
            out.append(f"{path} = {value!r}: нет среди worlds.ids")
    first, last = _get(data, "meta.first_period"), _get(data, "meta.last_period")
    anchor = _get(data, "meta.anchor_period")
    if all(isinstance(x, str) and QUARTER_KEY.match(x) for x in (first, last, anchor)):
        fy, fq = map(int, QUARTER_KEY.match(first).groups())
        ay, aq = map(int, QUARTER_KEY.match(anchor).groups())
        if (fy * 4 + fq) != (ay * 4 + aq) + 1:
            out.append("meta.first_period: должен быть следующим кварталом после meta.anchor_period")
        if last < first and len(last) == len(first):
            out.append("meta.last_period раньше meta.first_period")
    books = _get(data, "nii.books")
    if isinstance(books, Mapping):
        out.extend(_book_rules(books))
    comps = _get(data, "valuation.governance.components")
    disc = _get(data, "valuation.governance.discount")
    if isinstance(comps, list) and is_number(disc):
        try:
            total = sum(float(c["sign"]) * float(c["value"]) for c in comps)
            if abs(total - float(disc)) > 1e-12:
                out.append(f"valuation.governance.discount = {disc} ≠ Σ sign × value = {total} (М§8.2)")
        except (KeyError, TypeError, ValueError):
            pass
    steps = _get(data, "dividends.policy.steps")
    if isinstance(steps, list) and len(steps) > 1:
        pays = [s.get("payout") for s in steps if isinstance(s, Mapping)]
        if all(is_number(p) for p in pays) and pays != sorted(pays, reverse=True):
            out.append("dividends.policy.steps: ступени — по убыванию доли выплаты")
    for i, axis in enumerate(_get(data, "valuation.uncertainty.axes") or []):
        out.extend(_axis_rules(data, axis, f"valuation.uncertainty.axes[{i}]"))
    band_paths = {str(p) for ax in _get(data, "valuation.uncertainty.axes") or [] if isinstance(ax, Mapping)
                  for p in ax.get("paths") or []}
    for i, axis in enumerate(_get(data, "valuation.uncertainty.off_band_axes") or []):
        where = f"valuation.uncertainty.off_band_axes[{i}]"
        out.extend(_axis_rules(data, axis, where))
        if isinstance(axis, Mapping):
            if axis.get("kind") not in ("value", "shift"):
                out.append(f"{where}.kind: вне полосы — только value и shift (М§10)")
            both = sorted(band_paths & {str(p) for p in axis.get("paths") or []})
            if both:
                out.append(f"{where}: путь {', '.join(both)} и в полосе, и вне полосы")
    out.extend(_w1_rules(data, first, last))
    out.extend(_w2_rules(data, ids, anchor))
    out.extend(_w3_rules(data))
    for i, axis in enumerate(_get(data, "valuation.reverse_dcf.axes") or []):
        out.extend(_axis_rules(data, axis, f"valuation.reverse_dcf.axes[{i}]"))
    out.extend(_observation_rules(data, anchor, last))
    out.extend(_unit_interval_rules(data))
    return out


# Таблицы вероятностей книги (М§3.4, §9): каждое значение — вероятность.
PROBABILITY_TABLES = ("joint.world_prob", "joint.world_prob_market_implied", "joint.regime_prob",
                      "joint.reg_prob_given_regime")
# Доли книги с границами [0; 1] (у `nii.sigma0_split`, `nii.phi_split` и
# `joint.regime_update.floor_share` — свои правила выше).
UNIT_SHARES = {"joint.own_macro_confidence": "доверие своему взгляду на ставки λ (М§9)",
               "dividends.excess.epsilon": "доля выплаты избытка ε (М§5.3)"}
# Концы оси, которые подставляются в книгу как есть: у оси полосы — low и high, у оси обратного
# расчёта — отрезок поиска и таблица, к которой идёт смесь.
AXIS_ENDS = {"valuation.uncertainty.axes": ("low", "high"), "valuation.uncertainty.off_band_axes": ("low", "high"),
             "valuation.reverse_dcf.axes": ("search", "toward")}


def _outside_unit(path: str, value: Any, what: str) -> list[str]:
    """Числа значения (число, список или словарь любой глубины) вне [0; 1] — строками ошибок."""
    if isinstance(value, Mapping):
        return [e for k, v in value.items() for e in _outside_unit(f"{path}.{k}", v, what)]
    if isinstance(value, (list, tuple)):
        return [e for v in value for e in _outside_unit(path, v, what)]
    if is_number(value) and not 0 <= float(value) <= 1:
        return [f"{path} = {value}: {what} — от 0 до 1"]
    return []


def _under(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(root + ".") for root in roots)


def _steps(value: Any) -> list[float]:
    """Приращения конца оси-сдвига: число (конец оси полосы) или пара чисел (отрезок поиска обратного расчёта)."""
    if is_number(value):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value if is_number(v)]
    return []


def _unit_interval_rules(data: Mapping) -> list[str]:
    """Вероятности и доли книги — в [0; 1] (М§3.4, §5.3, §9; сумму таблицы держит инвариант `probabilities`).

    Без правила отрицательная вероятность при сумме 1 давала отрицательные веса клеток, ε > 1 — выплату
    сверх запаса капитала, λ вне [0; 1] — точку за пределами слоёв. То же — концам осей, у которых путь —
    такая вероятность или доля: ось подставляет их в книгу (смесь оси словаря и смесь обратного расчёта —
    выпуклые). У оси вида «сдвиг» концы — приращения: в книгу ложится её значение плюс приращение, и
    проверяется оно — иначе книга прошла бы загрузку и не прошла прогон полосы. Вероятность таблицы
    сдвигом не водят: сумма таблицы перестала бы быть 1."""
    out: list[str] = []
    for table in PROBABILITY_TABLES:
        out.extend(_outside_unit(table, _get(data, table), "вероятность (М§3.4)"))
    for path, what in UNIT_SHARES.items():
        out.extend(_outside_unit(path, _get(data, path), what))
    bounded = tuple(PROBABILITY_TABLES) + tuple(UNIT_SHARES)
    for group, ends in AXIS_ENDS.items():
        for i, axis in enumerate(_get(data, group) or []):
            if not isinstance(axis, Mapping):
                continue
            hit = [p for p in map(str, axis.get("paths") or []) if _under(p, bounded)]
            if not hit:
                continue
            where = f"{group}[{i}]"
            if axis.get("kind") != "shift":
                for end in ends:
                    out.extend(_outside_unit(f"{where}.{end}", axis.get(end),
                                             "конец оси вероятности или доли (М§10, §11.1)"))
                continue
            for p in hit:
                if _under(p, PROBABILITY_TABLES):
                    out.append(f"{where}: ось-сдвиг по вероятности {p} — сумма таблицы перестала бы быть 1 "
                               "(М§3.4, §10)")
                    continue
                base = _get(data, p)
                if not is_number(base):
                    continue
                for end in ends:
                    for step in _steps(axis.get(end)):
                        if not 0 <= float(base) + step <= 1:
                            out.append(f"{where}.{end} = {step:g}: {p} = {base} со сдвигом — вне [0; 1] "
                                       "(М§10, §11.1)")
    return out


def _period_index(period: Any) -> int | None:
    if isinstance(period, str):
        m = QUARTER_KEY.match(period)
        if m:
            return int(m.group(1)) * 4 + int(m.group(2))
    return None


def _observation_rules(data: Mapping, anchor: Any, last: Any) -> list[str]:
    """Наблюдения A-P2u (М§12): погрешность у наблюдённой величины; квартал не позже якоря несёт
    замороженные ожидания режимов `mu_<x>` наблюдённых величин, прогнозный квартал — нет."""
    out: list[str] = []
    a, z = _period_index(anchor), _period_index(last)
    seen: set[str] = set()
    for i, obs in enumerate(_get(data, "joint.regime_update.observations") or []):
        if not isinstance(obs, Mapping):
            continue
        where = f"joint.regime_update.observations[{i}]"
        for x in ("cor", "nim"):
            if obs.get(x) is not None and obs.get(f"se_{x}") is None:
                out.append(f"{where}.se_{x}: null при наблюдённом {x}")
        per = obs.get("period")
        q = _period_index(per)
        if q is None or a is None:
            continue
        if per in seen:
            out.append(f"{where}.period = {per}: второе наблюдение того же квартала")
        seen.add(per)
        if z is not None and q > z:
            out.append(f"{where}.period = {per}: позже last_period (М§12)")
        for x in ("cor", "nim"):
            key = f"mu_{x}"
            if q <= a and obs.get(x) is not None and not isinstance(obs.get(key), Mapping):
                out.append(f"{where}.{key}: наблюдение квартала не позже якоря без замороженных ожиданий "
                           "режимов (их пишет перезаякоривание, М§12, §15.2)")
            if q > a and key in obs:
                out.append(f"{where}.{key}: у наблюдения прогнозного квартала ожидания режимов считает "
                           "ядро — ключа быть не должно (М§12)")
    return out


def _w3_rules(data: Mapping) -> list[str]:
    """Ключи W3 (М прил. A): доля сжатия φ на активах, пол вероятности режима, окно рангов оценщика
    срединных прогонов, допуск нейтрального значения."""
    out: list[str] = []
    split = _get(data, "nii.phi_split")
    books = _get(data, "nii.books")
    if is_number(split) and isinstance(books, Mapping):
        if not 0 <= float(split) <= 1:
            out.append(f"nii.phi_split = {split}: доля от 0 до 1 (М§4.5)")
        has = {side: any(isinstance(b, Mapping) and b.get("side") == side and b.get("phi") is True
                         for b in books.values()) for side in ("asset", "liability")}
        if float(split) > 0 and not has["asset"]:
            out.append("nii.phi_split > 0, а активов с phi нет (М§4.5)")
        if float(split) < 1 and not has["liability"]:
            out.append("nii.phi_split < 1, а пассивов с phi нет (М§4.5)")
    if isinstance(books, Mapping):
        flags = [bool(books[b].get("phi")) for b in RETAIL_BOOKS if isinstance(books.get(b), Mapping)]
        if len(set(flags)) > 1:
            out.append(f"nii.books.{RETAIL_BOOKS[0]}.phi ≠ nii.books.{RETAIL_BOOKS[1]}.phi: у обеих книг средств "
                       "ФЛ флаг одинаков — иначе объём сжатия зависел бы от раскладки (М§4.5)")
    share = _get(data, "joint.regime_update.floor_share")
    if is_number(share) and not 0 <= float(share) <= 1:
        out.append(f"joint.regime_update.floor_share = {share}: доля от 0 до 1 (М§12)")
    window = _get(data, "valuation.sensitivities.rank_window")
    if isinstance(window, (list, tuple)) and len(window) == 2 and all(map(is_number, window)):
        if not 0 <= float(window[0]) < float(window[1]) <= 1:
            out.append(f"valuation.sensitivities.rank_window = {list(window)}: нужно 0 ≤ нижний < верхний ≤ 1 (М§10)")
    tol = _get(data, "valuation.next_report.value_tol")
    if is_number(tol) and float(tol) <= 0:
        out.append(f"valuation.next_report.value_tol = {tol}: нужно число > 0 (М§13)")
    return out


def _w2_rules(data: Mapping, ids: Mapping[str, tuple[str, ...]], anchor: Any) -> list[str]:
    """Ключи W2 (М прил. A): окно A-P2u, режим-опора FVC, доля σ0 на активах, год шока кризиса."""
    out: list[str] = []
    n = _get(data, "joint.regime_update.window_obs")
    if isinstance(n, int) and not isinstance(n, bool) and n < 1:
        out.append(f"joint.regime_update.window_obs = {n}: нужно целое ≥ 1 (М§12)")
    ref = _get(data, "credit.fv_loans_ref")
    regimes = ids.get("<r>", ())
    if isinstance(ref, str) and regimes and ref not in regimes:
        out.append(f"credit.fv_loans_ref = {ref!r}: нет среди regimes.ids (М§4.6)")
    split = _get(data, "nii.sigma0_split")
    books = _get(data, "nii.books")
    if is_number(split) and isinstance(books, Mapping):
        if not 0 <= float(split) <= 1:
            out.append(f"nii.sigma0_split = {split}: доля от 0 до 1 (М§4.5)")
        has = {side: any(isinstance(b, Mapping) and b.get("side") == side and b.get("lt_shift") is True
                         for b in books.values()) for side in ("asset", "liability")}
        if float(split) > 0 and not has["asset"]:
            out.append("nii.sigma0_split > 0, а активов с lt_shift нет (М§4.5)")
        if float(split) < 1 and not has["liability"]:
            out.append("nii.sigma0_split < 1, а пассивов с lt_shift нет (М§4.5)")
    anchor_year = _year_of(anchor)
    for r in regimes:
        spec = _get(data, f"regimes.{r}")
        if not isinstance(spec, Mapping):
            continue
        where = f"regimes.{r}"
        off = spec.get("shock_year_offset")
        if off is None:
            for k in ("one_off_loss", "loan_growth_override"):
                if spec.get(k):
                    out.append(f"{where}.{k}: кризисный ключ без {where}.shock_year_offset (М§3.2)")
            continue
        if not isinstance(off, int) or isinstance(off, bool):
            continue
        if off < 1:
            out.append(f"{where}.shock_year_offset = {off}: нужно целое ≥ 1 (М§3.2)")
            continue
        if anchor_year is None:
            continue
        shock = anchor_year + off
        cor = spec.get("cor")
        if isinstance(cor, Mapping):
            wrong = sorted(str(k) for k in cor if QUARTER_KEY.match(str(k)) and int(str(k)[:4]) != shock)
            if wrong:
                out.append(f"{where}.cor: квартальные ключи {', '.join(wrong)} вне года шока {shock} (М§3.2)")
        one = spec.get("one_off_loss")
        if isinstance(one, Mapping) and _year_of(one.get("period")) not in (None, shock):
            out.append(f"{where}.one_off_loss.period = {one.get('period')}: вне года шока {shock} (М§3.2)")
        ov = spec.get("loan_growth_override")
        if isinstance(ov, Mapping) and ov:
            years = sorted(int(str(k)) for k in ov if YEAR_KEY.match(str(k)))
            if years and years[0] != shock:
                out.append(f"{where}.loan_growth_override: первый год {years[0]} — не год шока {shock} (М§3.2)")
    return out


def _w1_rules(data: Mapping, first: Any, last: Any) -> list[str]:
    """Ключи W1: год σ0, доли «прочего», ввод ε, число кварталов проверок (М прил. A)."""
    out: list[str] = []
    fy, ly = _year_of(first), _year_of(last)
    y0 = _get(data, "nii.sigma0_from")
    if is_number(y0) and fy is not None and ly is not None and not fy <= int(y0) <= ly:
        out.append(f"nii.sigma0_from = {y0}: вне лет сетки {fy}…{ly} (М§4.4)")
    shares = _get(data, "other.misc_quarter_shares")
    if isinstance(shares, Mapping) and all(is_number(shares.get(k)) for k in ("1", "2", "3", "4")):
        total = sum(float(shares[k]) for k in ("1", "2", "3", "4"))
        if abs(total - 1.0) > 1e-9:
            out.append(f"other.misc_quarter_shares: сумма долей {total:.9g} ≠ 1 (М§4.7)")
    for key in ("dividends.excess.ramp_years", "checks.nim_path_joint.quarters", "checks.ni_jump.quarters"):
        n = _get(data, key)
        if isinstance(n, int) and not isinstance(n, bool) and n < 1:
            out.append(f"{key} = {n}: нужно целое ≥ 1")
    return out


def _book_rules(books: Mapping) -> list[str]:
    out: list[str] = []
    for name, spec in books.items():
        if not isinstance(spec, Mapping):
            continue
        p = f"nii.books.{name}"
        side = spec.get("side")
        balancing = spec.get("balancing") is True
        if side == "liability":
            if "beta" not in spec:
                out.append(f"{p}.beta: нет ключа (пассив)")
            for k in ("sector", "cor_segment", "fv_share"):
                if k in spec:
                    out.append(f"{p}.{k}: ключ актива у пассива")
        elif side == "asset":
            if "phi" not in spec:
                out.append(f"{p}.phi: нет ключа (актив)")
            if "beta" in spec:
                out.append(f"{p}.beta: ключ пассива у актива")
            if not balancing:
                for k in ("sector", "cor_segment"):
                    if k not in spec:
                        out.append(f"{p}.{k}: нет ключа (кредитная книга)")
            elif "sector" in spec or "fv_share" in spec:
                out.append(f"{p}: у балансирующей книги нет сектора и доли СС")
    return out


def _axis_rules(data: Mapping, axis: Any, where: str) -> list[str]:
    out: list[str] = []
    if not isinstance(axis, Mapping):
        return out
    kind = axis.get("kind")
    for path in axis.get("paths") or []:
        value = _get(data, str(path))
        if value is None:
            out.append(f"{where}: путь {path} не найден в книге")
            continue
        if kind == "value" and not is_number(value):
            out.append(f"{where}: путь {path} — не число (вид value)")
        if kind == "value":
            head, _, last_key = str(path).rpartition(".")
            parent = _get(data, head) if head else None
            if YEAR_KEY.match(last_key) and isinstance(parent, Mapping) and any(
                    QUARTER_KEY.match(str(k)) and str(k).startswith(last_key) for k in parent):
                out.append(f"{where}: путь {path} — ключ года траектории с квартальными ключами этого "
                           "года (ключ года — среднее кварталов, М§0.4)")
        if kind == "dict" and not isinstance(value, Mapping):
            out.append(f"{where}: путь {path} — не словарь долей (вид dict)")
        if kind == "mix" and not isinstance(value, Mapping):
            out.append(f"{where}: путь {path} — не таблица (вид mix)")
    return out


def read_paths(data: Mapping) -> list[str]:
    """Листовые пути книги, которые ядро читает при данных выключателях."""
    off = switched_off_paths(data)
    out: list[str] = []

    def walk(value: Any, path: str) -> None:
        if path in off:
            return
        if isinstance(value, Mapping):
            for k, v in value.items():
                walk(v, _join(path, str(k)))
        elif isinstance(value, list) and value and isinstance(value[0], Mapping):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")
        else:
            out.append(path)

    walk(data, "")
    return out
