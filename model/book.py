"""Книга допущений и факты: загрузка, закрытая схема, anchor-ключи.

Ядро читает только машинную книгу (`data/assumptions/assumptions.yaml`) и
факты (`data/facts/*.json`). Узел факта — `{"v": число | null, "src" | "calc"}`;
`null` — «не раскрыто», ядро не превращает его в 0 молча (FactsError там, где
число нужно). Значение книги `anchor` выводится из фактов якоря (М прил. A).
Чтение фактов собрано здесь (`anchor_facts`): формат файлов меняется в одном
месте.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import yaml

from model.book_schema import RETAIL_BOOKS, BookError, FactsError, is_number, validate
from model.paths import get_path, set_path

__all__ = ["BookError", "FactsError", "BOOK_PATH", "FACTS_DIR", "Facts", "Book", "load_facts",
           "load_book", "book_from_dict", "AnchorFacts", "anchor_facts", "canonical_json"]

ROOT = Path(__file__).resolve().parents[1]
BOOK_PATH = ROOT / "data" / "assumptions" / "assumptions.yaml"
FACTS_DIR = ROOT / "data" / "facts"


def canonical_json(obj: Any) -> bytes:
    """Канонический JSON (INTERFACES §7.2): ключи по порядку, без пробелов, UTF-8."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str).encode("utf-8")


def _digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def normalize(obj: Any) -> Any:
    """Ключи словарей — строки, даты — ISO-строки (YAML и JSON читаются одинаково)."""
    if isinstance(obj, Mapping):
        return {str(k): normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


# ------------------------------------------------------------------ факты


def _walk(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(part)
    return cur


@dataclass(frozen=True)
class Facts:
    root: Path
    files: Mapping[str, dict]              # имя файла без .json → содержимое как есть
    digest: str                            # sha256 канонического JSON всех файлов

    def file(self, name: str) -> dict:
        if name not in self.files:
            raise FactsError(f"нет файла фактов {name}.json в {self.root.name}/")
        return self.files[name]

    def has(self, file: str, path: str) -> bool:
        try:
            self.node(file, path)
            return True
        except FactsError:
            return False

    def node(self, file: str, path: str) -> dict:
        """Узел {"v", "src"|"calc"}; нет узла — FactsError."""
        try:
            got = _walk(self.file(file), path)
        except KeyError:
            raise FactsError(f"нет узла факта {file}.{path}") from None
        if not isinstance(got, Mapping) or "v" not in got:
            raise FactsError(f"{file}.{path}: не узел факта {{v, src|calc}}")
        if not (got.get("src") or got.get("calc")):
            raise FactsError(f"{file}.{path}: у узла нет происхождения (src или calc)")
        return dict(got)

    def raw(self, file: str, path: str) -> Any:
        """Значение узла как есть (число, [мин, макс], null)."""
        return self.node(file, path)["v"]

    def v(self, file: str, path: str) -> float | None:
        """Значение узла; null → None."""
        value = self.raw(file, path)
        if value is None:
            return None
        if not is_number(value):
            raise FactsError(f"{file}.{path}: ожидается число, в фактах {value!r}")
        return float(value)

    def need(self, file: str, path: str) -> float:
        """Значение; null или нет узла — FactsError."""
        value = self.v(file, path)
        if value is None:
            raise FactsError(f"{file}.{path}: null — число не раскрыто, а ядру оно нужно")
        return value

    def plain(self, file: str, path: str) -> Any:
        """Поле вне узла (год, дата, строка, метод моста)."""
        try:
            got = _walk(self.file(file), path)
        except KeyError:
            raise FactsError(f"нет поля {file}.{path}") from None
        if isinstance(got, Mapping) and "v" in got:
            return got["v"]
        return got

    def periods(self, file: str, key: str = "quarters") -> dict[str, dict]:
        """Строки по периодам: словарь {период: строка} или список [{period, …}]."""
        data = self.file(file).get(key)
        if isinstance(data, Mapping):
            return {str(k): dict(v) for k, v in data.items()}
        if isinstance(data, list):
            out = {}
            for row in data:
                if isinstance(row, Mapping) and "period" in row:
                    out[str(row["period"])] = dict(row)
            return out
        raise FactsError(f"{file}.{key}: нет строк по периодам")


def load_facts(root: Path | None = None) -> Facts:
    root = Path(root) if root is not None else FACTS_DIR
    if not root.is_dir():
        raise FactsError(f"нет каталога фактов {root}")
    files = {}
    for path in sorted(root.glob("*.json")):
        files[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return Facts(root=root, files=files, digest=_digest(files))


# ------------------------------------------------------------------ книга


@dataclass(frozen=True)
class Book:
    data: Mapping[str, Any]                # машинная книга после проверки; "anchor" заменены числами
    anchors: Mapping[str, float]           # точечный путь anchor-ключа → выведенное значение
    path: Path | None
    digest: str                            # sha256 канонического JSON исходной книги (до замены anchor)
    source: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    base: "Book | None" = field(default=None, repr=False, compare=False)
    # книга до подмен: у загруженной — None, у копии — исходная (у копии копии — та же исходная). По ней ядро
    # берёт то, что не разыгрывается: путь режима-опоры FVC (М§4.6)

    def get(self, path: str) -> Any:
        """Значение по точечному пути (М прил. A); нет ключа — BookError."""
        return get_path(self.data, path)

    def with_overrides(self, overrides: Mapping[str, Any]) -> "Book":
        """Копия с заменой значений по путям; anchors не меняются; `base` копии — исходная книга."""
        data = copy.deepcopy(dict(self.data))
        source = copy.deepcopy(dict(self.source or self.data))
        for dotted, value in overrides.items():
            get_path(data, dotted)          # путь обязан существовать (закрытая схема)
            set_path(data, dotted, copy.deepcopy(value))
            try:
                get_path(source, dotted)
                set_path(source, dotted, copy.deepcopy(value))
            except BookError:
                pass
        validate(data)
        return Book(data=data, anchors=dict(self.anchors), path=self.path,
                    digest=_digest(source), source=source,
                    base=self if self.base is None else self.base)


def load_book(path: Path | None = None, *, facts: Facts | None = None) -> Book:
    path = Path(path) if path is not None else BOOK_PATH
    if not path.exists():
        raise BookError(f"нет машинной книги {path.name}")
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if facts is None:
        facts = load_facts()
    book = book_from_dict(raw, facts=facts)
    return Book(data=book.data, anchors=book.anchors, path=path, digest=book.digest,
                source=book.source)


def book_from_dict(data: Mapping[str, Any], *, facts: Facts) -> Book:
    """Схема + вывод anchor-ключей для словаря книги (тесты, фикстуры)."""
    source = normalize(data)
    validate(source)
    resolved = copy.deepcopy(source)
    anchors = derive_anchors(resolved, facts)
    for dotted, value in anchors.items():
        set_path(resolved, dotted, value)
    validate(resolved)
    return Book(data=resolved, anchors=anchors, path=None, digest=_digest(source), source=source)


# ------------------------------------------------------------------ факты якоря

# Книги ЧПД → строки баланса якоря (М прил. B, balance.json). Сумма узлов;
# null допустим только у частей, раскрытых внутри другой строки (обратное
# репо — в средствах в банках, репо ЦБ — в средствах банков).
BOOK_BALANCE_FIELDS: dict[str, tuple[str, ...]] = {
    "corp_loans": ("loans_ac_gross.commercial", "loans_ac_gross.project", "loans_fvtpl"),
    "mortgage": ("loans_ac_gross.mortgage_market",),
    "mortgage_sub": ("loans_ac_gross.mortgage_subsidized",),
    "retail_loans": ("loans_ac_gross.consumer", "loans_ac_gross.cards", "loans_ac_gross.auto"),
    "securities": ("securities.fvoci_debt", "securities.fvoci_repo", "securities.ac",
                   "securities.ac_repo", "securities.fvtpl_bonds"),
    "liquidity": ("liquidity.cash", "liquidity.cbr", "liquidity.due_from_banks",
                  "liquidity.reverse_repo"),
    "retail_current": ("funds.retail_current",),
    "retail_term": ("funds.retail_term",),
    "corp_funds": ("funds.corp_current", "funds.corp_term"),
    "wholesale": ("wholesale.banks", "wholesale.issued_debt", "wholesale.subordinated",
                  "wholesale.repo_cbr"),
}
INCLUDED_ELSEWHERE = frozenset({"liquidity.reverse_repo", "wholesale.repo_cbr"})
FV_LOANS_FIELD = "loans_fvtpl"
FVOCI_FIELDS = ("securities.fvoci_debt", "securities.fvoci_repo")
FVTPL_BOND_FIELD = "securities.fvtpl_bonds"
# Роли книг в балансе (М§4.1, §4.3, §4.10): пара книг ФЛ задаёт долю текущих
# счетов; бумаги и ликвидность — балансирующие активы (доля бумаг — ключ книги);
# опт — балансирующий пассив. Кредиты — активы с сектором; прочие
# небалансирующие пассивы — средства ЮЛ.
BOOK_TITLES = {"corp_loans": "Кредиты юрлицам", "mortgage": "Ипотека рыночная", "mortgage_sub": "Ипотека льготная",
               "retail_loans": "Прочие кредиты физлицам", "securities": "Долговые бумаги", "liquidity": "Ликвидность",
               "retail_current": "Текущие счета физлиц", "retail_term": "Срочные вклады физлиц",
               "corp_funds": "Средства юрлиц", "wholesale": "Рыночное фондирование"}
RETAIL_CURRENT, RETAIL_TERM = RETAIL_BOOKS
SECURITIES, LIQUIDITY, WHOLESALE = "securities", "liquidity", "wholesale"


@dataclass(frozen=True)
class BookRoles:
    names: tuple[str, ...]                 # порядок nii.books
    assets: tuple[str, ...]
    liabilities: tuple[str, ...]
    loans: tuple[str, ...]                 # кредитные книги (сектор, сегмент CoR)
    retail: tuple[str, ...]                # (текущие, срочные) ФЛ
    corp_funds: tuple[str, ...]            # прочие небалансирующие пассивы (ЮЛ)
    funds: tuple[str, ...]                 # средства клиентов = retail + corp_funds


def book_roles(books: Mapping[str, Mapping[str, Any]]) -> BookRoles:
    names = tuple(books)
    assets = tuple(b for b in names if books[b].get("side") == "asset")
    liabs = tuple(b for b in names if books[b].get("side") == "liability")
    for role, side in ((SECURITIES, "asset"), (LIQUIDITY, "asset"), (WHOLESALE, "liability"),
                       (RETAIL_CURRENT, "liability"), (RETAIL_TERM, "liability")):
        if role not in books or books[role].get("side") != side:
            raise BookError(f"nii.books.{role}: нужна книга-{'актив' if side == 'asset' else 'пассив'} "
                            "этой роли (М§4.1)")
    for role in (SECURITIES, LIQUIDITY, WHOLESALE):
        if not books[role].get("balancing"):
            raise BookError(f"nii.books.{role}.balancing: книга этой роли — балансирующая (М§4.10)")
    loans = tuple(b for b in assets if not books[b].get("balancing"))
    extra = [b for b in names if books[b].get("balancing") and b not in (SECURITIES, LIQUIDITY, WHOLESALE)]
    if extra:
        raise BookError(f"nii.books: балансирующих книг вне ролей М§4.10: {', '.join(extra)}")
    retail = (RETAIL_CURRENT, RETAIL_TERM)
    corp = tuple(b for b in liabs if b not in retail and not books[b].get("balancing"))
    return BookRoles(names=names, assets=assets, liabilities=liabs, loans=loans, retail=retail,
                     corp_funds=corp, funds=retail + corp)
PNL_FIELDS = ("nii", "fees_net", "llp_debt_fa", "insurance_net", "misc_net", "noncore_net",
              "opex", "pbt", "tax", "ni", "ni_shareholders")


@dataclass(frozen=True)
class AnchorFacts:
    """Всё, что ядро берёт из фактов якоря (числа — в единицах М§0.1)."""
    period: str
    as_of: date
    balances: Mapping[str, float]          # книга → остаток на якоре
    rates: Mapping[str, float]             # книга → ставка 2К якоря (act/365)
    fv_loans: float                        # кредиты по СС (внутри книги с loans_fvtpl)
    fv_book: str | None                    # книга, в которой лежат кредиты по СС
    fvoci: float; fvtpl_bonds: float
    allowance: float                       # резерв по кредитам АС (положительный)
    other_assets: float; other_liabilities: float
    dividends_payable: float; dividends_payable_year: int | None
    bv: float; at1: float; nci: float
    fvoci_reserve: float                   # R_0, после налога
    current_share: float; key_avg: float
    prev_year_end: Mapping[str, float]     # сегмент гайденса → кредиты на конец прошлого года
    coupon_annual: float; coupon_quarter: int
    t2: float | None
    n20: float; n20_pre_dividend: bool; n11_bank: float
    basel_cet1: float | None; basel_rwa: float | None; bank_base_capital: float | None
    ofz_anchor: Mapping[str, float]        # узел кривой ("1", "3", "5", "10") → доля
    n_iss: float; n_out: float; n_out_ordinary: float | None; n_out_preferred: float | None
    pnl: Mapping[str, Mapping[str, float | None]]   # период → строка ОПУ (знаки как в МСФО)


def _sum_nodes(facts: Facts, file: str, paths: tuple[str, ...], what: str) -> float:
    total, seen = 0.0, 0
    for p in paths:
        value = facts.v(file, p)
        if value is None:
            if p in INCLUDED_ELSEWHERE:
                continue
            raise FactsError(f"{file}.{p}: null — нужен для {what}")
        total += value
        seen += 1
    if not seen:
        raise FactsError(f"{what}: ни одной раскрытой строки")
    return total


def _date(x: Any) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def anchor_facts(facts: Facts, books: Mapping[str, Mapping[str, Any]]) -> AnchorFacts:
    """Стартовое состояние из фактов (М§4.1, прил. B)."""
    period = str(facts.plain("anchor", "period"))
    as_of = _date(facts.plain("anchor", "as_of"))
    balances, rates = {}, {}
    fv_book = None
    bal_file = facts.file("balance")
    direct = bal_file.get("books") if isinstance(bal_file.get("books"), Mapping) else None
    for name in books:
        if direct is not None and name in direct:
            balances[name] = facts.need("balance", f"books.{name}")
        elif name in BOOK_BALANCE_FIELDS:
            balances[name] = _sum_nodes(facts, "balance", BOOK_BALANCE_FIELDS[name],
                                        f"остатка книги {name}")
            if FV_LOANS_FIELD in BOOK_BALANCE_FIELDS[name]:
                fv_book = name
        else:
            raise FactsError(f"книга {name}: нет правила остатка на якоре (М прил. B)")
        rate_path = f"books.{name}.rate_anchor" if facts.has("nii_books", f"books.{name}.rate_anchor") \
            else f"{name}.rate_anchor"
        rates[name] = facts.need("nii_books", rate_path)
    fv_loans = facts.need("balance", FV_LOANS_FIELD) if fv_book else 0.0
    fvoci = _sum_nodes(facts, "balance", FVOCI_FIELDS, "доли FVOCI")
    fvtpl_bonds = facts.need("balance", FVTPL_BOND_FIELD)
    pay_year = facts.plain("balance", "dividends_payable_year") \
        if "dividends_payable_year" in facts.file("balance") else None
    cap = "capital"
    ofz = {k: facts.need(cap, f"ofz_curve_anchor.{k}") for k in ("1", "3", "5", "10")}
    pnl = {}
    for per, row in facts.periods("pnl_quarterly").items():
        out = {}
        for f in PNL_FIELDS:
            node = row.get(f)
            out[f] = None if not isinstance(node, Mapping) or node.get("v") is None else float(node["v"])
        pnl[per] = out
    shares = "shares"
    cur_share = facts.v("nii_books", "current_share_anchor")
    if cur_share is None:
        rc, rt = balances.get(RETAIL_CURRENT), balances.get(RETAIL_TERM)
        if rc is None or rt is None:
            raise FactsError("nii_books.current_share_anchor: null и нет книг ФЛ для расчёта")
        cur_share = rc / (rc + rt)
    return AnchorFacts(
        period=period, as_of=as_of, balances=balances, rates=rates,
        fv_loans=fv_loans, fv_book=fv_book, fvoci=fvoci, fvtpl_bonds=fvtpl_bonds,
        allowance=abs(facts.need("balance", "allowance_ac")),
        other_assets=facts.need("balance", "other_assets"),
        other_liabilities=facts.need("balance", "other_liabilities"),
        dividends_payable=facts.need("balance", "dividends_payable"),
        dividends_payable_year=None if pay_year is None else int(pay_year),
        bv=facts.need("balance", "equity.bv_common"),
        at1=facts.need("balance", "equity.at1"),
        nci=facts.need("balance", "equity.nci"),
        fvoci_reserve=facts.need(cap, "fvoci_reserve"),
        current_share=cur_share,
        key_avg=facts.need("nii_books", "key_avg_anchor_q"),
        prev_year_end={"corporate": facts.need("balance", "prev_year_end.loans_corporate"),
                       "retail": facts.need("balance", "prev_year_end.loans_retail")},
        coupon_annual=facts.need(cap, "at1.coupon_annual"),
        coupon_quarter=int(facts.need(cap, "at1.coupon_quarter")),
        t2=facts.v(cap, "t2_recognized") if facts.has(cap, "t2_recognized") else None,
        n20=facts.need(cap, "n20_0.value"),
        n20_pre_dividend=bool(facts.plain(cap, "n20_0.pre_dividend")),
        n11_bank=facts.need(cap, "n1_1_bank.value"),
        basel_cet1=facts.v(cap, "basel.cet1") if facts.has(cap, "basel.cet1") else None,
        basel_rwa=facts.v(cap, "basel.rwa") if facts.has(cap, "basel.rwa") else None,
        bank_base_capital=facts.v(cap, "bank_base_capital") if facts.has(cap, "bank_base_capital") else None,
        ofz_anchor=ofz,
        n_iss=facts.need(shares, "issued_total"),
        n_out=facts.need(shares, "outstanding_total"),
        n_out_ordinary=facts.v(shares, "outstanding_ordinary") if facts.has(shares, "outstanding_ordinary") else None,
        n_out_preferred=facts.v(shares, "outstanding_preferred") if facts.has(shares, "outstanding_preferred") else None,
        pnl=pnl,
    )


def _period_key(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def unaudited_profit(af: AnchorFacts, cutoffs: list[int], year: int, quarter: int,
                     ni_of: Mapping[str, float]) -> float:
    """E на конец квартала: прибыль после последнего квартала-отсечки, строго
    предшествующего данному, по данный включительно (М§4.11)."""
    total = 0.0
    y, q = year, quarter
    while True:
        per = _period_key(y, q)
        if per not in ni_of:
            raise FactsError(f"нет ЧП акционерам за {per} (неаудированная прибыль)")
        total += ni_of[per]
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
        if q in cutoffs:
            return total


def derive_anchors(data: Mapping[str, Any], facts: Facts) -> dict[str, float]:
    """Значения ключей `anchor` (формулы — М прил. A); результат — в Book.anchors."""
    books = get_path(data, "nii.books")
    af = anchor_facts(facts, books)
    roles = book_roles(books)
    loans_total = sum(af.balances[b] for b in roles.loans)
    loans_ac = loans_total - af.fv_loans
    sec_total = af.balances[SECURITIES]
    liq_total = af.balances[LIQUIDITY]
    funds_total = sum(af.balances[b] for b in roles.funds)
    wh_total = af.balances[WHOLESALE]
    formulas: dict[str, Any] = {
        "credit.allowance_ratio": lambda: af.allowance / loans_ac,
        "nii.retail_current_share.c_ref": lambda: af.current_share,
        "nii.retail_current_share.key_ref": lambda: af.key_avg,
        "volumes.wholesale_to_funds": lambda: wh_total / funds_total,
        "volumes.securities_share_of_liquid": lambda: sec_total / (sec_total + liq_total),
        "volumes.other_assets_to_loans": lambda: af.other_assets / loans_total,
        "volumes.other_liabilities_to_loans": lambda: af.other_liabilities / loans_total,
        "oci.fvoci_share": lambda: af.fvoci / sec_total,
        "oci.fvtpl_bond_share": lambda: af.fvtpl_bonds / sec_total,
        "capital.n20.deductions_anchor": lambda: _ded20(af),
        "capital.n11.deductions_anchor": lambda: _ded11(af, data),
    }
    for b in books:
        formulas[f"nii.books.{b}.fv_share"] = (
            lambda b=b: (af.fv_loans / af.balances[b]) if b == af.fv_book else 0.0)
    out: dict[str, float] = {}
    for dotted, fn in formulas.items():
        try:
            value = get_path(data, dotted)
        except BookError:
            continue
        if value == "anchor":
            out[dotted] = float(fn())
    return out


def _ded20(af: AnchorFacts) -> float:
    if af.basel_cet1 is None:
        raise FactsError("capital.basel.cet1: нужен для anchor capital.n20.deductions_anchor")
    return af.bv - af.basel_cet1


def _ded11(af: AnchorFacts, data: Mapping[str, Any]) -> float:
    if af.bank_base_capital is None:
        raise FactsError("capital.bank_base_capital: нужен для anchor capital.n11.deductions_anchor")
    f = float(get_path(data, "capital.n20.fvoci_recognition"))
    cutoffs = [int(x) for x in get_path(data, "capital.n11.audit_cutoffs")]
    ni = {p: r["ni_shareholders"] for p, r in af.pnl.items() if r.get("ni_shareholders") is not None}
    y, q = (int(af.period[:4]), int(af.period[-1]))
    e0 = unaudited_profit(af, cutoffs, y, q, ni)
    dpreg0 = af.dividends_payable if af.n20_pre_dividend else 0.0
    bvreg = af.bv + dpreg0 - (1 - f) * af.fvoci_reserve
    # та же форма, что у норматива пути (model.capital.ratios): исключается только прибыль
    return bvreg - max(e0, 0.0) - af.bank_base_capital
