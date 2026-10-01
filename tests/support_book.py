# -*- coding: utf-8 -*-
"""Помощники тестов книги (поток book): пути, загрузка модулей книги, разбор прил. A MODEL, копия листа во временный
каталог. pytest этот файл как тесты не собирает."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BOOK_DIR = ROOT / "data" / "assumptions"
EVIDENCE = BOOK_DIR / "evidence"
FACTS_DIR = ROOT / "data" / "facts"
MODEL_MD = ROOT / "docs" / "MODEL.md"

# Запись миров семейства (850oa, книга 1.6; кривая 18.09.2026): хранимые байты — побайтово с CRLF
# (.gitattributes: worlds_source.* -text). Рецепт — обезличенные копии, как у «Ленты» (решение ведущего B4): тексты и код
# без тикера и адреса выпуска книги-источника и без её сумм; функционально те же (`worlds_recipe.py --check`
# воспроизводит запись). Хэши рецепта — LF-нормализованных байтов (репозиторий хранит текст в LF).
RECORD_SHA256 = "7296567d5200b5599efd0989de6ed8c8461d56c5c5dda244ab7d622303969fec"
RECORD_CSV_SHA256 = "2faae142d447a309daaf43eeea0a928db94ae2dc90ff1e0b21771703cd4a8dfc"
RECIPE_LF_SHA256 = {                  # worlds_recipe.py — байт в байт копия «Ленты»; .md и .yaml — её копии с шапкой Сбера
                                      # (шапка .md оговаривает: раздел о сборщике описывает сборщик книги-источника)
    "worlds_inputs.yaml": "8cf64cc3cb3bb3ebf6a7118e46a57aeb9bcc5ea2c27c3172ba4bc8549d80160f",
    "worlds_recipe.py": "459b7b6eb8da4228ab8eec657ba9ae758b5521676306555bfd37bc07e4fcf180",
    "WORLDS-RECIPE.md": "a588382a8b1cdfe6fe25083a03b786da246c478f29bced7f883758cbe0801661",
}
# Чего в копиях рецепта быть не должно (публичный репозиторий): тикер и адрес выпуска книги-источника, её суммы в ₽
RECIPE_FORBIDDEN = ("MGNT", "Магнит", "pages.dev", "tzi-850oa")

# Выключатель → ключи, которые он выключает (MODEL §0.1, прил. A): под выключенным ключи могут быть null
SWITCHES = {
    "credit.segment_relative.enabled": ("credit.segment_relative.corporate", "credit.segment_relative.retail"),
    "capital.rwa.fx.enabled": ("capital.rwa.fx.share", "capital.rwa.fx.foreign_inflation"),
    "other.fvtpl_bond_reval": ("oci.fvtpl_bond_duration",),
}
TOP = ("meta", "worlds", "worlds_bank", "joint", "regimes", "credit", "nii", "volumes", "fees", "opex", "noncore",
       "other", "pnl", "tax", "oci", "equity", "capital", "dividends", "bridge", "valuation", "checks")


def load_module(path: Path, name: str):
    """Импорт файла книги (не пакет) по пути."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True      # тест не пишет __pycache__ в дерево книги
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
        sys.path.remove(str(path.parent))
    return mod


def build_module():
    return load_module(BOOK_DIR / "build_assumptions.py", "book_build_assumptions")


def built_book() -> dict:
    return build_module().build()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def get(d, dotted: str):
    for part in dotted.split("."):
        d = d[part]
    return d


def has(d, dotted: str) -> bool:
    try:
        get(d, dotted)
        return True
    except (KeyError, TypeError):
        return False


def leaves(obj, prefix: str = ""):
    """(путь, значение) всех листьев; элементы списков — путь[индекс]."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list) and obj and isinstance(obj[0], (dict, list)):
        for i, v in enumerate(obj):
            yield from leaves(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


# ------------------------------------------------------------------ прил. A MODEL
def app_a_text() -> str:
    text = MODEL_MD.read_text(encoding="utf-8")
    a = text.index("## Приложение A")
    return text[a:text.index("## Приложение B", a)]


def _expand(token: str, book: dict) -> list[str]:
    """Шаблон пути прил. A → конкретные пути книги ('*' — любой ключ словаря, <год>)."""
    m = re.search(r"\{([^}]*)\}", token)
    if m:
        out = []
        for alt in (x.strip() for x in m.group(1).split(",")):
            out += _expand(token[:m.start()] + alt + token[m.end():], book)
        return out
    subst = {
        "<W>": book["worlds"]["ids"], "<r>": book["regimes"]["ids"], "<s>": book["capital"]["reg_scenarios"]["ids"],
        "<b>": list(book["nii"]["books"]), "<t>": book["meta"]["company"]["tickers"], "<год>": ["*"],
    }
    for ph, vals in subst.items():
        if ph in token:
            out = []
            for v in vals:
                out += _expand(token.replace(ph, str(v), 1), book)
            return out
    return [token]


def _applies(path: str, book: dict) -> bool:
    """Условные ключи книг ЧПД, плотностей и полов спреда (прил. A: пассивы, активы, кредиты, балансирующие)."""
    m = re.match(r"nii\.books\.([^.]+)\.(beta|phi|lt_shift|sector|cor_segment|balancing)$", path)
    books = book["nii"]["books"]
    loans = {b for b, v in books.items() if v.get("side") == "asset" and not v.get("balancing")}
    if m:
        b, key = m.groups()
        side = books[b].get("side")
        if key == "beta":
            return side == "liability"
        if key in ("phi", "lt_shift"):             # сдвиг σ0 — с книги 1.2, сжатие φ — с книги 1.3: у книг обеих
            return True                            # сторон (MODEL §4.4–§4.5)
        if key in ("sector", "cor_segment"):
            return b in loans
        return b in ("securities", "liquidity", "wholesale")
    m = re.match(r"checks\.lt_spread_floor\.([^.]+)$", path)
    if m:                                          # пол спреда — только у кредитных книг (с `sector`)
        return m.group(1) in loans
    m = re.match(r"capital\.rwa\.density\.([^.]+)$", path)
    if m and m.group(1) in books:
        return m.group(1) in loans | {"securities", "liquidity"}
    return True


def app_a_paths(book: dict) -> list[str]:
    """Все конкретные пути прил. A, которые обязана нести машинная книга."""
    out = []
    for tok in re.findall(r"`([^`]+)`", app_a_text()):
        tok = tok.strip()
        if " " in tok.replace(", ", ",") or "." not in tok or tok.split(".")[0] not in TOP or "/" in tok:
            continue
        for p in _expand(tok.replace(", ", ","), book):
            if _applies(p, book) and p not in out:
                out.append(p)
    return out


def year_value(traj, year: int) -> float:
    """Значение траектории книги в году по ключам года (MODEL §0.4, правила 3–6; квартальные ключи не читаются)."""
    if not isinstance(traj, dict):
        return float(traj)
    if str(year) in traj:
        return float(traj[str(year)])
    years = sorted(int(k) for k in traj if str(k).isdigit())
    lt, lt_from = traj.get("LT"), traj.get("LT_from")
    if not years:
        return float(lt)
    if year < years[0]:
        return float(traj[str(years[0])])
    if year > years[-1]:
        if lt is None:
            return float(traj[str(years[-1])])
        if lt_from is None or year >= int(lt_from):
            return float(lt)
        y0, v0 = years[-1], float(traj[str(years[-1])])
        return v0 + (float(lt) - v0) * (year - y0) / (int(lt_from) - y0)
    lo = max(y for y in years if y < year)
    hi = min(y for y in years if y > year)
    vlo, vhi = float(traj[str(lo)]), float(traj[str(hi)])
    return vlo + (vhi - vlo) * (year - lo) / (hi - lo)


def switched_off(book: dict) -> set[str]:
    """Ключи под выключенными выключателями."""
    off = set()
    for sw, keys in SWITCHES.items():
        if get(book, sw) is False:
            off.update(keys)
    return off


# ------------------------------------------------------------------ листы во временном каталоге
def copy_area(tmp: Path, area: str) -> Path:
    """Копия листа области с окружением (evlib, шаблон, запись миров, соседний лист ЧПД) — тест не пишет в дерево."""
    dst_book = tmp / "assumptions"
    (dst_book / "evidence").mkdir(parents=True, exist_ok=True)
    for f in ("assumptions_template.yaml", "worlds_source.json"):
        shutil.copy2(BOOK_DIR / f, dst_book / f)
    shutil.copy2(EVIDENCE / "evlib.py", dst_book / "evidence" / "evlib.py")
    src_area = EVIDENCE / area

    def _ignore(d: str, names: list[str]) -> set[str]:     # выходы листа не копируются — их пишет прогон в копии
        skip = {n for n in names if n == "__pycache__"}
        if Path(d) == src_area:
            skip |= {n for n in names if n == "out"}
        return skip

    shutil.copytree(src_area, dst_book / "evidence" / area, ignore=_ignore)
    if area == "bridges":                           # мост ЧПМ читает выход листа ЧПД
        shutil.copytree(EVIDENCE / "nii" / "out", dst_book / "evidence" / "nii" / "out")
    if area == "governance":                        # оборот — из цен листа beta
        shutil.copytree(EVIDENCE / "beta" / "inputs", dst_book / "evidence" / "beta" / "inputs")
    if area == "nii":                               # ближние спреды — от ставок якоря фактов (near_spreads.py);
        (tmp / "facts").mkdir(exist_ok=True)        # распределение σ0 и сжатия φ — ещё и мосты (sigma0_split.py, phi_split.py)
        for f in ("nii_books.json", "bridge_mgmt_ifrs.json"):
            shutil.copy2(FACTS_DIR / f, tmp / "facts" / f)
    return dst_book / "evidence" / area


def run_script(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-B", str(path), *args], cwd=str(path.parent), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=300)


def template() -> dict:
    return yaml.safe_load((BOOK_DIR / "assumptions_template.yaml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ печатаемые строки книги (PAYLOAD §0.2)
# Чего не бывает в строке, которую печатает витрина: рабочие пометки, имена листов и разделов проекта, метки класса
# допущения, короткая дата и версия без имени (читаются как дробь), служебные имена, хвост хэша, e-нотация, кортеж.
PRINTED_FORBIDDEN = (
    ("рабочая пометка", re.compile(r"ведущ|первичк|антибот|вопрос методики", re.I)),
    ("имя листа или раздела проекта", re.compile(r"DESIGN|VM §|calib-|\bregimes\b|evidence|\bmarket\b|этапа? 1|MODEL §|М§")),
    ("метка класса допущения", re.compile(r"\[(?:Ф|Р|В|НП|C|С)(?:[/:\] ])")),
    ("дата без года", re.compile(r"(?<![\d.,])\d{2}\.\d{2}(?![\d.])")),
    ("версия без имени", re.compile(r"850oa \d")),
    ("служебное имя", re.compile(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|(?<![\w.])[a-z][a-z_]*(?:\.[a-z][a-z_0-9]*)+(?![\w])")),
    ("хвост хэша", re.compile(r"sha256|\b[0-9a-f]{12,}\b")),
    ("e-нотация", re.compile(r"\d(?:\.\d+)?e[-+]?\d", re.I)),
    ("кортеж", re.compile(r"\(\d{4}, \d+, [A-Z]{3,}")),
)


def printed_issues(text: str) -> list[str]:
    """Что в строке не годится для экрана: [«что: найденный кусок»]."""
    return [f"{what}: {rx.search(text).group(0)}" for what, rx in PRINTED_FORBIDDEN if rx.search(text)]


def axis_sources(book_yaml: Path | None = None) -> dict[str, str]:
    """Источник суждения каждой оси полосы — так, как его печатает выпуск (PAYLOAD §2 `judgements.rows[].source`):
    комментарий машинной книги к первому пути оси, разобранный ядром (`model.uncertainty`). Схема книги не нужна —
    читаются только текст YAML и список осей."""
    from types import SimpleNamespace

    import model.uncertainty as U

    path = book_yaml or BOOK_DIR / "assumptions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    stub = SimpleNamespace(path=path, get=lambda dotted: get(data, dotted))
    notes = U.book_notes(stub)
    return {ax["name"]: U.judgement_source(stub, ax, notes) for ax in data["valuation"]["uncertainty"]["axes"]}
