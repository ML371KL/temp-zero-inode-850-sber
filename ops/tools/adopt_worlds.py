# -*- coding: utf-8 -*-
"""Приём версии миров ставок семейства 850 в эту модель — одной командой по манифесту модели.

    python -B ops/tools/adopt_worlds.py --version 1.7 [--source DIR] [--dry-run | --branch] [--report FILE]
            [--from-step ID] [--until-step ID] [--skip ID,...] [--no-quick] [--no-push] [--ci record|wait|off]
            [--workers N]
    python -B ops/tools/adopt_worlds.py --check [--source DIR]     # книга стоит на своей версии: хэши, ключи, правила

Источник миров — общий репозиторий семейства `temp-zero-inode-850-worlds` (`--source`, иначе переменная
`WORLDS_SOURCE_DIR`, иначе каталог `850-worlds` выше по дереву): индекс `versions.json` и каталог версии
`versions/<v>/` (запись, входы, рецепт, описание рецепта, надстройка банков). Модель держит побайтовые копии и
сверяет их хэши с индексом; версию, на которой стоит книга, и хэши её файлов команда пишет в
`data/assumptions/worlds_state.json` (руками не правится; его читают тесты и сборщики).

Манифест — `data/assumptions/worlds_dependents.yaml` (правится руками, идёт с образцом кода): что копировать из
версии, какие ключи книги пишутся из индекса, какие числа книги зависят от миров и каким правилом модели они
пересчитываются, какие команды модели пересобирают книгу, быстрые числа, таблицы, тексты, эталоны тестов, журналы.
Команда сама правил модели не знает: шаг — это файл, ключ или команда, названные манифестом. Шаги идут по порядку;
провал шага — остановка с отчётом.

Виды шагов манифеста (`kind`):
  install   файлы версии → дерево: побайтово (`verify: sha256`), со своими строками (`keep_lines`, `own_lines`),
            своей шапкой из комментариев (`keep_head`), подстановками (`subst`: `<<book_gen>>` → имя книги);
  state     записать `worlds_state.json` (версия, хэши записи, надстройки и скопированных файлов; та же версия с теми
            же хэшами — файл не переписывается);
  set       скаляры YAML из индекса (`values: {ключ: "{record.sha256}"}`), замены подписей (`replace`);
  rule      функция модели `module:function(ctx)` → числа книги по правилу; пишутся точечно, с повторным разбором
            (адрес — строка через точку или кортеж: элементы списков — номером, ключ с точкой внутри — одной частью);
            `replace` правила — текст вне данных (комментарии с числами правила): [[было, стало], …]; цель с
            `text_values: true` — строки блочного стиля (`>-`): значения пишет замена текста, сверка — «данные + values»;
  run       команда модели (`cmd`), код возврата из `ok`; `check` — команда сверки, её зовёт и `--check`; строки вывода
            «ИТОГ: …» идут в отчёт шага, «ФЛАГ: …» — в красные флаги;
  loop      тело `steps` по кругу до неподвижной точки (круг, не изменивший ни одного файла дерева), не больше `max`
            кругов (неподвижная точка через таблицы: ключ → таблицы → медиана → ключ); шаги тела сверяет и `--check`;
  quick     команда быстрых чисел → JSON (`out`): точка, медиана, печать, низ, верх, миры — до и после приёма;
  gates     сработавшие гейты из JSON таблиц (`file`, `path`; список строк или записей с полем `flag`) — до и после;
  journal   записи журналов по шаблону с числами отчёта (`{n.median_after}`, `{book.version}` …); `after` — строка-якорь
            (с подстановками), запись встаёт строкой после неё.
У любого шага `on_change: true` — шаг только при смене версии миров (журналы, документы версии): приём той же версии
его пропускает.
Команды модели видят версию в окружении: WORLDS_VERSION, WORLDS_FROM_VERSION (приём той же версии — неподвижная точка).

Режимы:
  --dry-run (по умолчанию)  все шаги во временной копии дерева (`git worktree`, `var/adopt/<v>/tree`), в рабочую
                            копию не пишется ничего; отчёт — `var/adopt/<v>/report.json` и `.md`, что изменилось в
                            копии — `git.tree_changes` отчёта и `var/adopt/<v>/tree.diff` (приём той же версии обязан
                            дать пустой список — иначе красный флаг);
  --branch                  в рабочей копии: ветка `worlds-<v>` (создаётся от текущего коммита или продолжается, если
                            уже текущая; дерево обязано быть чистым), коммит после каждого шага, который что-то изменил;
                            в конце — push ветки (без `--no-push`) и прогон CI (`--ci record` — номер прогона, `wait` —
                            дождаться итога). Слияние в main и выкладка — не здесь: по «да» владельца;
  --check                   ничего не пишет: копии файлов версии из `worlds_state.json` равны источнику, ключи
                            происхождения — индексу, правила дают числа шаблона (сторож тестов).

Отчёт — схема `adopt-worlds-report-v1`: числа «было → стало», сдвиг в шагах печати, гейты до и после, изменённые
числа книги, красные флаги, коммиты, CI. Его собирает сводка источника (`tools/rollout.py`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "assumptions" / "worlds_dependents.yaml"
STATE_REL = "data/assumptions/worlds_state.json"
MANIFEST_SCHEMA = "worlds-dependents-v1"
STATE_SCHEMA = "worlds-state-v1"
REPORT_SCHEMA = "adopt-worlds-report-v1"
INDEX_SCHEMA = "850-worlds-index-v1"
OK, FAILED, BAD_INPUT = 0, 1, 2
CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
NUM_IN_COMMENT = re.compile(r"\d[\d\s]*[.,]\d")


class Stop(Exception):
    """Шаг не прошёл: команда останавливается, отчёт пишется с причиной."""


# ----------------------------------------------------------------------------------------------- источник миров
def find_source(arg: str | None) -> Path:
    for cand in (arg, os.environ.get("WORLDS_SOURCE_DIR")):
        if cand:
            return Path(cand).resolve()
    for up in ROOT.parents:
        if (up / "850-worlds" / "versions.json").exists():
            return up / "850-worlds"
    raise Stop("источник миров не найден: --source или WORLDS_SOURCE_DIR")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Source:
    def __init__(self, path: Path):
        self.path = path
        idx = json.loads((path / "versions.json").read_text(encoding="utf-8"))
        if idx.get("schema") != INDEX_SCHEMA:
            raise Stop(f"индекс источника: схема {idx.get('schema')!r} ≠ {INDEX_SCHEMA}")
        self.index = idx

    def entry(self, v: str) -> dict:
        try:
            return self.index["versions"][v]
        except KeyError:
            raise Stop(f"версии {v} нет в индексе источника {self.path}") from None

    def file(self, v: str, rel: str) -> bytes:
        e = self.entry(v)
        if rel not in e["files"]:
            raise Stop(f"в версии {v} нет файла {rel}")
        data = (self.path / "versions" / v / rel).read_bytes()
        if sha256_bytes(data) != e["files"][rel]:
            raise Stop(f"источник испорчен: {v}/{rel} — хэш не равен индексу")
        return data

    def commit(self) -> str | None:
        p = subprocess.run(["git", "-C", str(self.path), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True)
        return p.stdout.strip() or None


def flat(d: Any, prefix: str = "", lists: bool = False) -> dict[str, Any]:
    """Плоский словарь адресов; `lists` — и внутрь списков (адрес элемента — его номер: оси, варианты книги)."""
    out: dict[str, Any] = {}
    if isinstance(d, dict) and d:
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}.", lists))
    elif lists and isinstance(d, list) and d:
        for i, v in enumerate(d):
            out.update(flat(v, f"{prefix}{i}.", lists))
    else:
        out[prefix[:-1]] = d
    return out


def ru_date(iso: str) -> str:
    try:
        return dt.date.fromisoformat(str(iso)).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)


def expand(text: str, ctx: dict[str, Any]) -> str:
    """`{record.sha256}`, `{origin}`, `{version}`, `{from.origin}`, `{book_gen}` … — поля индекса и манифеста."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in ctx:
            raise Stop(f"в значении нет поля {{{key}}}")
        return str(ctx[key])
    return re.sub(r"\{([\w.]+)\}", sub, text)


# ----------------------------------------------------------------------------------------------- точечная запись YAML
def _path(p: Any) -> tuple:
    return tuple(p) if isinstance(p, (list, tuple)) else tuple(str(p).split("."))


def _dotted(p: tuple) -> str:
    return ".".join(map(str, p))


def locate(root: yaml.Node, path: tuple) -> yaml.ScalarNode:
    node = root
    for part in path:
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                if str(k.value) == str(part):
                    node = v
                    break
            else:
                raise Stop(f"в YAML нет ключа {_dotted(path)} (нет «{part}»)")
        elif isinstance(node, yaml.SequenceNode):
            node = node.value[int(part)]
        else:
            raise Stop(f"{_dotted(path)}: «{part}» под скаляром")
    if not isinstance(node, yaml.ScalarNode):
        raise Stop(f"{_dotted(path)}: не число и не строка — команда пишет только скаляры")
    return node


def scalar_text(value: Any, style: str | None, decimals: int | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Stop(f"число {value} не пишется в книгу")
        s = f"{value:.{decimals if decimals is not None else 12}f}".rstrip("0")     # фиксированный формат: 9e-05 YAML
        s = s + "0" if s.endswith(".") else s                                       # прочёл бы строкой
        return "0.0" if s in ("-0.0", "-0") else s
    s = str(value)
    if style == "'":
        return "'" + s.replace("'", "''") + "'"
    if style == '"':
        return json.dumps(s, ensure_ascii=False)
    return s


def get(obj: Any, path: tuple) -> Any:
    for part in path:
        if isinstance(obj, list):
            obj = obj[int(part)]
        else:
            obj = obj[next((k for k in obj if str(k) == str(part)), part)]
    return obj


def same(a: Any, b: Any, tol: float = 0.0) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= max(tol, 1e-12 * max(1.0, abs(float(a))))
        except (TypeError, ValueError):
            return False
    return a == b


def _needs_quotes(s: str) -> bool:
    try:
        return not isinstance(yaml.safe_load(s), str) or s.strip() != s or ": " in s or " #" in s or s == ""
    except yaml.YAMLError:
        return True


def set_values(path: Path, values: dict[tuple, Any], decimals: dict[tuple, int] | None = None) -> dict[str, tuple]:
    """Пишет скаляры по адресам, не трогая остальной текст; сверяет повторным разбором. → {адрес: (было, стало)}."""
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)
    root = yaml.compose(text)
    edits, changed = [], {}
    for p, new in values.items():
        node = locate(root, p)
        old = get(before, p)
        if same(old, new) and type(old) is type(new):
            continue
        if isinstance(new, str):
            style = node.style if node.style in ("'", '"') else ('"' if _needs_quotes(new) else "")
        else:
            style = None
        edits.append((node.start_mark.index, node.end_mark.index,
                      scalar_text(new, style, {_dotted(q): d for q, d in (decimals or {}).items()}.get(_dotted(p)))))
        changed[_dotted(p)] = (old, new)
    for a, b, s in sorted(edits, reverse=True):
        text = text[:a] + s + text[b:]
    after = yaml.safe_load(text)
    fb, fa = flat(before, lists=True), flat(after, lists=True)
    want = {_dotted(p): v for p, v in values.items()}
    dec = {_dotted(p): d for p, d in (decimals or {}).items()}
    bad = [k for k in fa if k not in want and not same(fa.get(k), fb.get(k))]
    bad += [k for k, v in want.items() if not same(fa.get(k), v, 10.0 ** -dec.get(k, 12))]
    if bad or set(fa) != set(fb):
        raise Stop(f"{path.name}: точечная запись разошлась с повторным разбором: {bad[:8]}")
    if edits:
        path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def replace_text(path: Path, pairs: list, values: dict[tuple, Any] | None = None) -> int:
    """Текстовые замены правила (`replace`: [[было, стало] | [было, стало, сколько], …]) — только вне данных:
    комментарии и прочий текст YAML. «Было» встречается в файле ровно `сколько` раз (по умолчанию один); данные файла
    после замен те же (сверка повторным разбором). С `values` (цель правила `text_values: true` — числа внутри строк
    блочного стиля `>-`, которые точечная запись переписала бы одной строкой) замены меняют ровно эти строки данных и
    до этих значений: сверка повторным разбором — «данные до замен + values»."""
    pairs = [(p[0], p[1], int(p[2]) if len(p) > 2 else 1) for p in pairs or [] if p[0] != p[1]]
    if not pairs:
        return 0
    text = path.read_text(encoding="utf-8")
    new = text
    for a, b, n in pairs:
        if new.count(a) != n:
            raise Stop(f"{path.name}: текст замены правила встречается {new.count(a)} раз, нужно {n}: {a[:80]!r}")
        new = new.replace(a, b)
    if path.suffix in (".yaml", ".yml"):
        want = yaml.safe_load(text)
        for p, v in (values or {}).items():
            node = want
            for part in p[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            node[int(p[-1]) if isinstance(node, list) else p[-1]] = v
        if yaml.safe_load(new) != want:
            raise Stop(f"{path.name}: замена текста правила задела данные" + (" сверх значений правила" if values else
                       " — числа пишутся значениями, не заменой"))
    path.write_text(new, encoding="utf-8", newline="\n")
    return len(pairs)


def numbers_in_comments(path: Path, keys: list[str]) -> list[str]:
    """Ключи, у строк которых (и в строках-продолжениях комментария под ними) есть числа: этот текст пишется руками —
    после записи числа он мог устареть. Красный флаг для сверки, не ошибка."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    root = yaml.compose(text)
    out = []
    for k in keys:
        try:
            node = locate(root, _path(k))
        except Stop:
            continue
        i = node.start_mark.line
        chunk = [lines[i].split("#", 1)[1] if "#" in lines[i] else ""]
        ind = len(lines[i]) - len(lines[i].lstrip())
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith("#") and len(lines[j]) - len(lines[j].lstrip()) > ind:
            chunk.append(lines[j])
            j += 1
        if NUM_IN_COMMENT.search(" ".join(chunk)):
            out.append(k)
    return out


# ----------------------------------------------------------------------------------------------- файлы версии
def split_head(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    return "\n".join(lines[:i]), "\n".join(lines[i:])


def transform(f: dict, data: bytes, current: bytes | None, ctx: dict) -> bytes:
    """Байты файла версии → байты копии модели (свои строки, своя шапка, подстановки, концы строк)."""
    keep, subst, eol = f.get("keep_lines") or [], f.get("subst") or {}, f.get("eol", "as_source")
    if not (keep or subst or f.get("keep_head") or eol in ("lf", "crlf")):
        return data
    text = data.decode("utf-8").replace("\r\n", "\n")
    own = current.decode("utf-8").replace("\r\n", "\n") if current is not None else None
    if f.get("keep_head") and own is not None:
        head = split_head(own)[0]
        text = (head + "\n" if head else "") + split_head(text)[1]
    if keep:
        lines, own_lines = text.split("\n"), (own or "").split("\n")
        for n in keep:
            spec = (f.get("own_lines") or {}).get(n, (f.get("own_lines") or {}).get(str(n)))
            lines[n - 1] = expand(spec, ctx) if spec is not None else own_lines[n - 1]
        text = "\n".join(lines)
    for a, b in subst.items():
        text = text.replace(a, expand(b, ctx))
    return (text.replace("\n", "\r\n") if eol == "crlf" else text).encode("utf-8")


# ----------------------------------------------------------------------------------------------- ход
def book_version(tree: Path, manifest: dict) -> str | None:
    """Версия книги — `meta.version` шаблона (её ставит правило модели, не команда)."""
    p = tree / manifest.get("template", "data/assumptions/assumptions_template.yaml")
    try:
        return str(yaml.safe_load(p.read_text(encoding="utf-8"))["meta"]["version"])
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return None


def read_state(tree: Path) -> dict | None:
    p = tree / STATE_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class Run:
    def __init__(self, args: argparse.Namespace, manifest: dict, source: Source, tree: Path, say: Callable[[str], None]):
        self.a, self.m, self.src, self.tree, self.say = args, manifest, source, tree, say
        self.v = args.version
        self.entry = source.entry(self.v)
        state = read_state(tree)
        if not state:
            raise Stop(f"нет {STATE_REL}: на какой версии миров стоит книга — неизвестно")
        self.state = state
        self.from_v = str(state["version"])
        self.from_entry = source.entry(self.from_v) if self.from_v in source.index["versions"] else None
        self.ctx = context(manifest, self.entry, self.from_entry, self.v, self.from_v)
        self.report: dict[str, Any] = {
            "schema": REPORT_SCHEMA, "model": manifest["model"], "version": self.v, "from_version": self.from_v,
            "mode": "branch" if args.branch else "dry-run", "status": "running",
            "source": {"path": str(source.path), "commit": source.commit()},
            "started": dt.datetime.now().isoformat(timespec="seconds"), "steps": [], "numbers": {},
            "gates": {}, "dependents": {}, "red_flags": [], "git": {},
        }
        self.commits: list[str] = []
        self.installed: dict[str, str] = {}
        self.book_before = book_version(tree, manifest)
        self.report["book"] = {"version_before": self.book_before}

    def env(self) -> dict:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", WORLDS_SOURCE_DIR=str(self.src.path),
                   WORLDS_VERSION=self.v, WORLDS_FROM_VERSION=self.from_v)
        w = self.a.workers or self.m.get("workers")
        for name in self.m.get("workers_env", []):
            if w:
                env[name] = str(w)
        for k, v in (self.m.get("env") or {}).items():
            env[k] = expand(str(v), self.ctx)
        return env

    def cmd(self, spec: Any) -> list[str]:
        parts = spec if isinstance(spec, list) else str(spec).split()
        return [sys.executable if p == "{python}" else expand(str(p), self.ctx) for p in parts]

    def sh(self, spec: Any, *, timeout: float | None = None, ok: tuple = (0,), log: str | None = None) -> str:
        c = self.cmd(spec)
        self.say(f"    $ {' '.join(c[1:] if c[0] == sys.executable else c)}")
        p = subprocess.run(c, cwd=self.tree, env=self.env(), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        if log:
            (self.var() / f"{log}.log").write_text(out, encoding="utf-8")
        if p.returncode not in ok:
            tail = "\n".join(out.strip().splitlines()[-15:])
            raise Stop(f"команда вернула {p.returncode}: {' '.join(c[-3:])}\n{tail}")
        return out

    def var(self) -> Path:
        d = ROOT / "var" / "adopt" / self.v
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- виды шагов
    def step_install(self, s: dict) -> dict:
        changed = []
        for f in s["files"]:
            data = self.src.file(self.v, f["src"])
            dst = self.tree / f["dst"]
            old = dst.read_bytes() if dst.exists() else None
            new = transform(f, data, old, self.ctx)
            if f.get("verify", "sha256") == "sha256" and sha256_bytes(new) != self.entry["files"][f["src"]]:
                raise Stop(f"{f['dst']}: копия не равна {self.v}/{f['src']} источника побайтово")
            if old != new:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(new)
                changed.append(f["dst"])
            self.installed[f["dst"]] = f["src"]
        return {"changed": changed}

    def step_state(self, s: dict) -> dict:
        new = make_state(self.tree, self.m, self.v, self.entry, self.src.commit())
        if {k: v for k, v in self.state.items() if k != "source_commit"} == \
                {k: v for k, v in new.items() if k != "source_commit"}:
            return {"changed": []}                    # та же версия и те же хэши: состояние не переписывается
        self.state = new
        p = self.tree / STATE_REL
        p.write_text(json.dumps(self.state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        return {"changed": [STATE_REL]}

    def step_set(self, s: dict) -> dict:
        path = self.tree / s["file"]
        values = {_path(k): (expand(e, self.ctx) if isinstance(e, str) else e) for k, e in (s.get("values") or {}).items()}
        changed = set_values(path, values) if values else {}
        labels = 0
        for old_t, new_t in (s.get("replace") or {}).items():
            a, b = expand(old_t, self.ctx), expand(new_t, self.ctx)
            if a == b:
                continue
            for g in s.get("replace_in", [s["file"]]):
                for p in sorted(self.tree.glob(g)):
                    t = p.read_text(encoding="utf-8")
                    if a in t:
                        labels += t.count(a)
                        p.write_text(t.replace(a, b), encoding="utf-8", newline="\n")
        return {"values": {k: list(v) for k, v in changed.items()}, "labels": labels}

    def step_rule(self, s: dict) -> dict:
        res = call_rule(self.tree, s, RuleContext(self))
        out, flags, texts = {}, [], 0
        for target in res:
            path = self.tree / target["file"]
            vals = {_path(k): v for k, v in (target.get("values") or {}).items()}
            if target.get("text_values"):            # строки блочного стиля: значения пишет замена текста
                before = yaml.safe_load(path.read_text(encoding="utf-8"))
                texts += replace_text(path, target.get("replace"), vals)
                ch = {_dotted(k): (get(before, k), v) for k, v in vals.items() if get(before, k) != v}
            else:
                ch = set_values(path, vals, {_path(k): d for k, d in (target.get("decimals") or {}).items()}) if vals else {}
                texts += replace_text(path, target.get("replace"))
            out.update({k: list(v) for k, v in ch.items()})
            if ch and s.get("comments_flag", True) and path.suffix in (".yaml", ".yml"):
                hits = numbers_in_comments(path, list(ch))
                if hits:
                    flags.append(f"{s['id']}: у {len(hits)} изменённых ключей в комментарии шаблона есть числа — "
                                 f"сверить текст: {', '.join(hits[:6])}{' …' if len(hits) > 6 else ''}")
        self.report["dependents"].update(out)
        self.report["red_flags"] += flags
        return {"values": out, "count": len(out), "texts": texts}

    def step_run(self, s: dict) -> dict:
        out = self.sh(s["cmd"], timeout=s.get("timeout"), ok=tuple(s.get("ok", [0])), log=s["id"])
        said = [ln.strip() for ln in out.splitlines() if ln.startswith(("ИТОГ:", "ФЛАГ:"))]   # строки отчёта команды
        for ln in said:
            if ln.startswith("ФЛАГ:"):
                self.report["red_flags"].append(f"{s['id']}: {ln[5:].strip()}")
        return {"said": [ln for ln in said if ln.startswith("ИТОГ:")]} if said else {}

    def snapshot(self) -> dict[str, str]:
        """Хэши изменённых и новых файлов дерева против HEAD (кроме var/): мерка «круг цикла что-то изменил»."""
        out = {}
        for ln in git(self.tree, "status", "--porcelain", "--untracked-files=all").splitlines():
            p = ln[3:].strip().strip('"').split(" -> ")[-1]
            if p and not p.startswith("var/"):
                f = self.tree / p
                out[p] = sha256_bytes(f.read_bytes()) if f.is_file() else "<нет>"
        return out

    def step_loop(self, s: dict) -> dict:
        """Шаги тела по кругу до неподвижной точки: круг, который не изменил ни одного файла дерева, — сошлось; не
        сошлось за `max` кругов — остановка (образец — книга Совкомбанка)."""
        rounds = []
        for i in range(1, int(s.get("max", 3)) + 1):
            before = self.snapshot()
            for sub in s["steps"]:
                self.say(f"  круг {i}, шаг {sub['id']}: {expand(sub.get('title', sub['kind']), self.ctx)}")
                res = getattr(self, f"step_{sub['kind']}")(sub)
                rounds.append({"round": i, "id": sub["id"], **{k: v for k, v in res.items() if k != "numbers"}})
            if self.snapshot() == before:
                return {"rounds": i, "converged": True, "steps": rounds}
        raise Stop(f"{s['id']}: за {s.get('max', 3)} кругов неподвижная точка не достигнута")

    def step_quick(self, s: dict, phase: str) -> dict:
        out = self.tree / expand(s["out"], self.ctx)
        if out.exists():
            out.unlink()
        self.sh(s["cmd"], timeout=s.get("timeout"), log=f"{s['id']}-{phase}")
        if not out.exists():
            raise Stop(f"быстрые числа не записали {s['out']}")
        nums = json.loads(out.read_text(encoding="utf-8"))
        self.report["numbers"][phase] = nums
        return {"numbers": nums}

    def step_gates(self, s: dict, phase: str) -> dict:
        p = self.tree / s["file"]
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        fired = get(data, _path(s["path"])) if s.get("path") else data
        flag, name = s.get("flag", "fired"), s.get("name", "name")
        if isinstance(fired, list):                   # список гейтов: строки или записи {name, fired, …}
            names = sorted(str(x[name]) if isinstance(x, dict) else str(x) for x in fired
                           if not isinstance(x, dict) or x.get(flag))
        else:
            names = sorted(k for k, v in fired.items() if (v.get(flag) if isinstance(v, dict) else v))
        self.report["gates"][phase] = names
        return {"fired": names}

    def step_journal(self, s: dict) -> dict:
        changed = []
        for j in s["entries"]:
            p = self.tree / j["file"]
            text = p.read_text(encoding="utf-8") if p.exists() else ""
            block = expand(j["text"], {**self.ctx, **self.summary_ctx()})
            if block.strip() in text:
                continue
            anchor = expand(j["after"], {**self.ctx, **self.summary_ctx()}) if j.get("after") else None
            if anchor and anchor in text:              # запись — строкой после строки, где стоит якорь
                i = text.find("\n", text.index(anchor) + len(anchor))
                i = len(text) if i < 0 else i
                text = text[:i] + "\n" + block.rstrip("\n") + text[i:]
            else:
                text = text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"
            p.write_text(text, encoding="utf-8", newline="\n")
            changed.append(j["file"])
        return {"changed": changed}

    def summary_ctx(self) -> dict:
        n = self.report["numbers"]
        b, a = n.get("before") or {}, n.get("after") or {}

        def r(x, nd=2):
            return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")
        return {"n.point_before": r(b.get("point")), "n.point_after": r(a.get("point")),
                "n.median_before": r(b.get("median")), "n.median_after": r(a.get("median")),
                "n.print_before": b.get("print", "—"), "n.print_after": a.get("print", "—"),
                "book.version_before": self.book_before, "book.version": book_version(self.tree, self.m)}

    def steps(self) -> list[dict]:
        out, started = [], self.a.from_step is None
        for s in self.m["steps"]:
            if not started and s["id"] == self.a.from_step:
                started = True
            if started and s["id"] not in (self.a.skip or []):
                if s.get("on_change") and self.from_v == self.v:     # шаг только при смене версии: журналы, прогулка
                    self.report["steps"].append({"id": s["id"], "kind": s["kind"], "status": "skipped",
                                                 "reason": "та же версия миров (неподвижная точка)", "seconds": 0})
                else:
                    out.append(s)
            if self.a.until_step and s["id"] == self.a.until_step:
                break
        return out

    def execute(self) -> int:
        quick = next((s for s in self.m["steps"] if s["kind"] == "quick"), None)
        gates = next((s for s in self.m["steps"] if s["kind"] == "gates"), None)
        if self.a.from_step is None:
            if gates:
                self.step_gates(gates, "before")
            if quick and not self.a.no_quick:
                self.say("быстрые числа до приёма")
                t0 = time.time()
                try:
                    self.step_quick(quick, "before")
                except Stop as e:
                    self.report["red_flags"].append(f"быстрые числа до приёма не посчитаны: {str(e).splitlines()[0]}")
                self.report["steps"].append({"id": f"{quick['id']}-before", "status": "ok",
                                             "seconds": round(time.time() - t0)})
        for s in self.steps():
            if s["kind"] == "quick" and self.a.no_quick:
                continue
            self.say(f"шаг {s['id']}: {expand(s.get('title', s['kind']), self.ctx)}")
            t0 = time.time()
            rec = {"id": s["id"], "kind": s["kind"], "title": s.get("title", "")}
            try:
                if s["kind"] == "quick":
                    res = self.step_quick(s, "after")
                elif s["kind"] == "gates":
                    res = self.step_gates(s, "after")
                else:
                    res = getattr(self, f"step_{s['kind']}")(s)
                rec.update(status="ok", **{k: v for k, v in res.items() if k != "numbers"})
            except Stop as e:
                rec.update(status="stopped", reason=str(e), seconds=round(time.time() - t0))
                self.report["steps"].append(rec)
                self.report["red_flags"].insert(0, f"остановка на шаге {s['id']}: {str(e).splitlines()[0]}")
                self.report["status"] = "stopped"
                return FAILED
            rec["seconds"] = round(time.time() - t0)
            self.report["steps"].append(rec)
            if self.a.branch:
                self.commit(s)
        self.report["status"] = "ok"
        return OK

    def commit(self, s: dict) -> None:
        st = git(self.tree, "status", "--porcelain", "--untracked-files=all")
        paths = [ln[3:].strip().strip('"').split(" -> ")[-1] for ln in st.splitlines() if ln.strip()]
        paths = [p for p in paths if not p.startswith("var/")]
        if not paths:
            return
        git(self.tree, "add", "--", *paths)
        msg = (f"Миры {self.v}, шаг {s['id']}: {expand(s.get('title', s['kind']), self.ctx)}\n\n"
               f"Команда приёма ops/tools/adopt_worlds.py --version {self.v} (источник миров {self.src.commit() or '?'}).\n\n"
               f"{CO_AUTHOR}")
        ident = self.m.get("git_identity") or {}
        flags = [x for k in ("name", "email") if ident.get(k) for x in ("-c", f"user.{k}={ident[k]}")]
        git(self.tree, *flags, "commit", "-q", "-m", msg)
        self.commits.append(git(self.tree, "rev-parse", "--short", "HEAD").strip())

    def finish(self) -> None:
        n = self.report["numbers"]
        b, a = n.get("before") or {}, n.get("after") or {}
        step = self.m.get("print_step")
        if b.get("median") is not None and a.get("median") is not None and step:
            ps = abs(a["median"] - b["median"]) / step
            n["print_steps"] = round(ps, 2)
            lim = (self.m.get("stop") or {}).get("median_print_steps", 2)
            if ps > lim:
                self.report["red_flags"].append(f"сдвиг медианы {a['median'] - b['median']:+.2f} — {ps:.1f} шага "
                                                f"печати (порог {lim}): назвать причину до выкладки")
        g = self.report["gates"]
        if "before" in g and "after" in g:
            g["new"] = sorted(set(g["after"]) - set(g["before"]))
            g["gone"] = sorted(set(g["before"]) - set(g["after"]))
            if g["new"]:
                self.report["red_flags"].append(f"новые гейты: {', '.join(g['new'])} — связать с мирами или стоп")
        self.report["git"]["commits"] = self.commits
        self.report["book"]["version"] = book_version(self.tree, self.m)
        self.report["finished"] = dt.datetime.now().isoformat(timespec="seconds")

    def tree_changes(self) -> None:
        """Сухой прогон: что команда изменила во временной копии дерева (без var/) — дифф кладётся рядом с отчётом.
        Приём той же версии (неподвижная точка) обязан дать пустой список."""
        st = git(self.tree, "status", "--porcelain", "--untracked-files=all")
        paths = sorted(p for p in (ln[3:].strip().strip('"').split(" -> ")[-1] for ln in st.splitlines() if ln.strip())
                       if not p.startswith("var/"))
        diff = git(self.tree, "diff", "--stat=200", "--", ".", ":(exclude)var") + "\n" + git(self.tree, "diff", "--", ".", ":(exclude)var")
        (self.var() / "tree.diff").write_text(diff, encoding="utf-8", newline="\n")
        self.report["git"]["tree_changes"] = paths
        if self.from_v == self.v and paths and self.report["status"] == "ok":
            self.report["red_flags"].insert(0, f"неподвижная точка нарушена: приём той же версии изменил {len(paths)} "
                                               f"файл(ов): {', '.join(paths[:6])}{' …' if len(paths) > 6 else ''}")


def context(manifest: dict, entry: dict, from_entry: dict | None, v: str, from_v: str) -> dict:
    ctx = {"version": v, "from_version": from_v, **flat(entry)}
    for k in ("record.asof", "record.curve_date", "cbr_decision"):
        if ctx.get(k):
            ctx[f"{k}_ru"] = ru_date(ctx[k])
    if from_entry:
        ctx.update({f"from.{k}": val for k, val in flat(from_entry).items()})
    for k in ("model", "title", "book_gen"):
        if k in manifest:
            ctx[k] = manifest[k]
    ctx["today"] = dt.date.today().isoformat()
    ctx["today_ru"] = dt.date.today().strftime("%d.%m.%Y")
    return ctx


def walk_steps(steps: list[dict]) -> list[dict]:
    """Шаги манифеста с телами циклов (`loop`) — плоским списком."""
    out = []
    for s in steps:
        out.append(s)
        if s["kind"] == "loop":
            out += walk_steps(s["steps"])
    return out


def make_state(tree: Path, manifest: dict, v: str, entry: dict, source_commit: str | None) -> dict:
    files = {}
    for s in manifest["steps"]:
        if s["kind"] == "install":
            for f in s["files"]:
                files[f["dst"]] = {"src": f["src"], "sha256": sha256_bytes((tree / f["dst"]).read_bytes())}
    st = {"schema": STATE_SCHEMA, "version": v, "origin": entry["origin"], "title": entry["title"],
          "record_sha256": entry["record"]["sha256"], "record_csv_sha256": entry["record"]["csv_sha256"],
          "record_asof": entry["record"]["asof"], "curve_date": entry["record"]["curve_date"],
          "overlay_sha256": (entry.get("overlay") or {}).get("sha256"), "source_commit": source_commit, "files": files}
    return st


class RuleContext:
    """Что видит правило модели: запись и надстройка версии из дерева, индекс версии, корень дерева."""

    def __init__(self, run: "Run | None" = None, *, tree: Path | None = None, entry: dict | None = None,
                 version: str | None = None, say: Callable[[str], None] = print, manifest: dict | None = None):
        self.root = run.tree if run else tree
        self.version = run.v if run else version
        self.from_version = run.from_v if run else version     # версия миров книги до приёма (сторож: та же)
        self.entry = run.entry if run else entry
        m = run.m if run else (manifest or {})
        self.say = run.say if run else say
        rec = self.root / m.get("record", "data/assumptions/worlds_source.json")
        self.record = json.loads(rec.read_bytes().decode("utf-8"))
        ov = self.root / m.get("overlay", "data/assumptions/worlds_bank.json")
        self.overlay = json.loads(ov.read_text(encoding="utf-8")) if ov.exists() else None

    def yaml(self, rel: str) -> Any:
        return yaml.safe_load((self.root / rel).read_text(encoding="utf-8"))

    def module(self, rel: str, name: str | None = None):
        return load_module(self.root / rel, name or f"adopt_{Path(rel).stem}")


def call_rule(tree: Path, s: dict, ctx: RuleContext) -> list[dict]:
    mod = load_module(tree / s["module"], f"worlds_rule_{s['id'].replace('-', '_')}")
    res = getattr(mod, s["function"])(ctx)
    return res if isinstance(res, list) else [res]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
    return mod


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise Stop(f"git {' '.join(a for a in args if not a.startswith('user.'))[:60]}: {p.stderr.strip()[:300]}")
    return p.stdout


def load_manifest(tree: Path = ROOT) -> dict:
    m = yaml.safe_load((tree / "data" / "assumptions" / "worlds_dependents.yaml").read_text(encoding="utf-8"))
    if m.get("schema") != MANIFEST_SCHEMA:
        raise Stop(f"манифест: схема {m.get('schema')!r} ≠ {MANIFEST_SCHEMA}")
    ids = [s["id"] for s in walk_steps(m["steps"])]
    if len(ids) != len(set(ids)):
        raise Stop("манифест: повторяются id шагов")
    bad = [s["id"] for s in walk_steps(m["steps"]) if s["kind"] not in
           ("install", "state", "set", "rule", "run", "quick", "gates", "journal", "loop")]
    if bad:
        raise Stop(f"манифест: неизвестный вид шага у {bad}")
    return m


# ----------------------------------------------------------------------------------------------- сторож (--check)
def check(manifest: dict, source: Source | None, tree: Path = ROOT, say: Callable[[str], None] = print) -> list[str]:
    """Книга стоит на версии своего состояния: копии файлов равны источнику (или хэшам состояния, если источника
    нет), ключи происхождения — индексу, правила манифеста дают числа шаблона."""
    state = read_state(tree)
    if not state:
        return [f"нет {STATE_REL}"]
    v = str(state["version"])
    bad = []
    for dst, f in state["files"].items():
        if sha256_bytes((tree / dst).read_bytes()) != f["sha256"]:
            bad.append(f"{dst}: хэш не равен состоянию версии {v} (файл правили руками?)")
    entry = source.entry(v) if source else None
    if entry:
        if state["record_sha256"] != entry["record"]["sha256"] or state.get("overlay_sha256") != (entry.get("overlay") or {}).get("sha256"):
            bad.append(f"{STATE_REL}: хэши записи или надстройки не равны индексу источника для {v}")
        ctx = context(manifest, entry, None, v, v)
        for s in manifest["steps"]:
            if s["kind"] == "install":
                for f in s["files"]:
                    dst = tree / f["dst"]
                    want = transform(f, source.file(v, f["src"]), dst.read_bytes(), ctx)
                    if dst.read_bytes() != want:
                        bad.append(f"{f['dst']}: не равен {v}/{f['src']} источника (с правилами манифеста)")
            elif s["kind"] == "set":
                doc = yaml.safe_load((tree / s["file"]).read_text(encoding="utf-8"))
                for k, e in (s.get("values") or {}).items():
                    want = expand(e, ctx) if isinstance(e, str) else e
                    if not same(get(doc, _path(k)), want):
                        bad.append(f"{s['file']}: {k} = {get(doc, _path(k))!r}, индекс версии {v} даёт {want!r}")
    rc = RuleContext(tree=tree, entry=entry, version=v, say=say, manifest=manifest)
    for s in walk_steps(manifest["steps"]):
        if s["kind"] == "run" and s.get("check"):     # шаг-команда со своей сверкой (копии листов, файлы правил)
            p = subprocess.run([sys.executable if c == "{python}" else str(c) for c in s["check"]], cwd=tree,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=dict(os.environ, PYTHONIOENCODING="utf-8", WORLDS_VERSION=v, WORLDS_FROM_VERSION=v))
            if p.returncode != 0:
                tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-3:]
                bad.append(f"шаг {s['id']}: сверка вернула {p.returncode}: {' / '.join(tail)}")
        if s["kind"] != "rule" or not s.get("check", True):
            continue
        tol = float(s.get("tol", 0.0))
        for target in call_rule(tree, s, rc):
            doc = yaml.safe_load((tree / target["file"]).read_text(encoding="utf-8")) if target.get("values") else None
            for k, want in (target.get("values") or {}).items():
                have = get(doc, _path(k))
                if not same(have, want, tol):
                    bad.append(f"{target['file']}: {_dotted(_path(k))} = {have!r}, правило {s['id']} даёт {want!r}")
            for p in target.get("replace") or []:
                if p[0] != p[1]:
                    bad.append(f"{target['file']}: правило {s['id']} пишет текст иначе: {p[0][:60]!r} → {p[1][:60]!r}")
    return bad


# ----------------------------------------------------------------------------------------------- вход
def write_report(rep: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    md = [f"# Приём миров {rep['version']} — {rep['model']} ({rep['mode']}): {rep['status']}", ""]
    if rep["red_flags"]:
        md += ["## Красные флаги", ""] + [f"- {f}" for f in rep["red_flags"]] + [""]
    n = rep.get("numbers") or {}
    if n.get("before") or n.get("after"):
        md += ["## Числа", "", "| | было | стало |", "|---|---:|---:|"]
        for k in ("point", "median", "print", "low", "high"):
            md.append(f"| {k} | {(n.get('before') or {}).get(k, '—')} | {(n.get('after') or {}).get(k, '—')} |")
        md.append("")
    md += ["## Шаги", ""] + [f"- {s['id']}: {s['status']} ({s.get('seconds', '?')} с)"
                             + (f" — {s['reason'].splitlines()[0]}" if s.get("reason") else "")
                             + (f"; {'; '.join(x[5:].strip() for x in s['said'])}" if s.get("said") else "")
                             for s in rep["steps"]]
    if "tree_changes" in (rep.get("git") or {}):
        ch = rep["git"]["tree_changes"]
        md += ["", "## Дерево после прогона", ""] + ([f"- {p}" for p in ch] or ["- без изменений"])
    path.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def ci_run(cwd: Path, branch: str, head: str, wait: bool) -> dict:
    """Прогоны CI ветки на этом коммите (gh); `wait` — дождаться итога."""
    for _ in range(12):
        p = subprocess.run(["gh", "run", "list", "--branch", branch, "--limit", "10", "--json",
                            "databaseId,headSha,status,conclusion,url,workflowName"],
                           cwd=cwd, capture_output=True, text=True, encoding="utf-8")
        runs = [r for r in json.loads(p.stdout or "[]") if r["headSha"] == head]
        if runs:
            if wait:
                for r in runs:
                    subprocess.run(["gh", "run", "watch", str(r["databaseId"]), "--exit-status"], cwd=cwd,
                                   capture_output=True)
                return ci_run(cwd, branch, head, wait=False)
            return {"runs": runs, "status": ",".join(sorted({r["status"] for r in runs})),
                    "conclusion": ",".join(sorted({r["conclusion"] or "" for r in runs})) or None,
                    "url": runs[0]["url"]}
        time.sleep(10)
    return {"status": "not-found"}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version")
    ap.add_argument("--source")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--branch", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--from-step")
    ap.add_argument("--until-step")
    ap.add_argument("--skip", type=lambda s: s.split(","), default=[])
    ap.add_argument("--no-quick", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--ci", default="record", choices=("record", "wait", "off"))
    ap.add_argument("--workers", type=int)
    a = ap.parse_args(argv)
    say = lambda line="": print(line, flush=True)  # noqa: E731
    try:
        manifest = load_manifest()
        if a.check:
            try:
                source = Source(find_source(a.source))
            except Stop:
                source = None
                say("источник миров не найден: сверка по хэшам состояния и правилам манифеста")
            bad = check(manifest, source, ROOT, say)
            st = read_state(ROOT) or {}
            say(f"миры книги = версия {st.get('version')}" if not bad else "РАСХОЖДЕНИЯ:\n  " + "\n  ".join(bad))
            return OK if not bad else FAILED
        if not a.version:
            ap.error("--version обязателен (кроме --check)")
        source = Source(find_source(a.source))
        st = read_state(ROOT)
        if st and str(st["version"]) == a.version and not a.from_step:
            say(f"книга уже на мирах {a.version}: прогон — проверка неподвижной точки (числа обязаны не сдвинуться)")
    except Stop as e:
        say(f"ОТКАЗ: {e}")
        return BAD_INPUT
    report_path = Path(a.report) if a.report else ROOT / "var" / "adopt" / a.version / "report.json"
    tree, worktree, run = ROOT, None, None
    try:
        if a.branch:
            if git(ROOT, "status", "--porcelain", "--untracked-files=no").strip():
                raise Stop("в рабочей копии есть незакоммиченные правки — приём только с чистого дерева")
            branch = f"worlds-{a.version}"
            if git(ROOT, "branch", "--show-current").strip() != branch:
                git(ROOT, "switch", "-c", branch)
        else:
            worktree = ROOT / "var" / "adopt" / a.version / "tree"
            if worktree.exists():
                subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(worktree)], capture_output=True)
                shutil.rmtree(worktree, ignore_errors=True)
            git(ROOT, "worktree", "add", "--detach", str(worktree), "HEAD")
            tree = worktree
        run = Run(a, manifest, source, tree, say)
        code = run.execute()
        run.finish()
        if worktree:
            run.tree_changes()
        if a.branch and code == OK and not a.no_push:
            git(ROOT, "push", "-q", "-u", "origin", f"worlds-{a.version}")
            head = git(ROOT, "rev-parse", "HEAD").strip()
            run.report["git"].update(branch=f"worlds-{a.version}", pushed=True, head=head)
            if a.ci != "off":
                run.report["git"]["ci"] = ci_run(ROOT, f"worlds-{a.version}", head, wait=a.ci == "wait")
        write_report(run.report, report_path)
        say(f"отчёт: {report_path}")
        for f in run.report["red_flags"]:
            say(f"  ФЛАГ: {f}")
        return code
    except Stop as e:
        say(f"ОТКАЗ: {e}")
        if run:
            run.report["status"] = "stopped"
            run.report["red_flags"].insert(0, str(e).splitlines()[0])
            write_report(run.report, report_path)
        return FAILED
    finally:
        if worktree and not os.environ.get("ADOPT_KEEP_TREE"):
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(worktree)], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
