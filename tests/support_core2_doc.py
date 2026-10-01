"""Разбор записи полей docs/PAYLOAD.md §2 (и полей блока `valuation_history` из §4) для теста
полноты `REQUIRED_FIELDS` (П§2 вступление, П§7 п. 1, INTERFACES §4.6).

Запись (П§2): поле — имя в обратных кавычках; поля узла — внутри `{…}` после имени узла, поля
элементов списка — внутри `[{…}]`; `?` после имени — необязательное; текст в скобках и после
тире — пояснение; значения перечислений (`a` | `b`, `∈ {…}`) — не поля. Узлы-словари по ключам
`<W>`, `<r>`, `<s>`, `<t>`, `<key>` — `{<t>: {…}}` или `{N, H, M}: {…}`; перечень ключей в фигурных
скобках с двоеточием и полями (`{`a`, `b`}: {…}`) — узлы `a` и `b` с этими полями. Имена узлов —
как у `REQUIRED_FIELDS`: `a.b`, `a.b[]`, `a.b.*`.

Эвристики там, где текст отступает от записи (явно и узко): «— [{…}]» внутри узла — пояснение
вида значений; тире на верхнем уровне пункта — пояснение до «;» или «,» с именем поля после или до
конца предложения; «36 × {…}» — список; «`x` — …», где `x` уже встречалось в пункте (значение
перечисления или поле вложенного узла), — пояснение, а не поле; абзац без обратных кавычек в
начале (вступление раздела) даёт только поля со структурой («По `a`, `b` (…): {…}»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_DOC = ROOT / "docs" / "PAYLOAD.md"
FIELD = re.compile(r"^[a-z_][a-z0-9_]*\??$")
NOT_FIELDS = {"null", "true", "false"}


@dataclass
class Tok:
    kind: str                  # bt, {, }, [, ], (, ), dash, pipe, in, colon, comma, semi, stop, times, word
    text: str = ""


def tokenize(text: str) -> list[Tok]:
    out: list[Tok] = []
    i, n = 0, len(text)
    word = []

    def flush() -> None:
        if word:
            out.append(Tok("word", "".join(word)))
            word.clear()

    while i < n:
        ch = text[i]
        if ch == "`":
            j = text.find("`", i + 1)
            if j < 0:
                j = n
            flush()
            out.append(Tok("bt", text[i + 1:j]))
            i = j + 1
            continue
        if ch in "{}[]()":
            flush()
            out.append(Tok(ch))
        elif ch == "—":
            flush()
            out.append(Tok("dash"))
        elif ch == "|":
            flush()
            out.append(Tok("pipe"))
        elif ch == "∈":
            flush()
            out.append(Tok("in"))
        elif ch == "×":
            flush()
            out.append(Tok("times"))
        elif ch == ":" and (i + 1 >= n or text[i + 1] in " \n{["):
            flush()
            out.append(Tok("colon"))
        elif ch == ",":
            flush()
            out.append(Tok("comma"))
        elif ch == ";":
            flush()
            out.append(Tok("semi"))
        elif ch == "." and (i + 1 >= n or text[i + 1] in " \n"):
            flush()
            out.append(Tok("stop"))
        elif ch.isspace():
            flush()
        else:
            word.append(ch)
        i += 1
    flush()
    return out


@dataclass
class Spec:
    optional: bool = False
    kind: str | None = None          # None — скаляр; "obj" | "list" | "map"
    fields: dict[str, "Spec"] = field(default_factory=dict)   # obj, list — поля; map — поля значения
    map_kind: str | None = None      # у map: вид значения ("obj" | "list")


def _is_field(t: Tok, toks: list[Tok], i: int) -> bool:
    if t.kind != "bt" or not FIELD.match(t.text) or t.text.rstrip("?") in NOT_FIELDS:
        return False
    prev = toks[i - 1].kind if i > 0 else ""
    nxt = toks[i + 1].kind if i + 1 < len(toks) else ""
    return prev != "pipe" and nxt != "pipe"


def _skip_group(toks: list[Tok], i: int) -> int:
    """toks[i] — открывающая скобка; → индекс после парной закрывающей."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    stack = [pairs[toks[i].kind]]
    i += 1
    while i < len(toks) and stack:
        k = toks[i].kind
        if k in pairs:
            stack.append(pairs[k])
        elif stack and k == stack[-1]:
            stack.pop()
        i += 1
    return i


def _next_sig(toks: list[Tok], i: int) -> int:
    while i < len(toks) and toks[i].kind == "word":
        i += 1
    return i


class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks

    def explained(self, i: int) -> bool:
        """`x` — …, где `x` уже встречалось раньше в пункте: пояснение значения, а не поле."""
        t = self.t
        if i + 1 >= len(t) or t[i + 1].kind != "dash":
            return False
        return any(x.kind == "bt" and x.text == t[i].text for x in t[:i])

    # структура после имени: {…}, [{…}], [скаляры], {<t>: …}, {`a`, `b`}: {…}
    def structure(self, i: int) -> tuple[Spec | None, int]:
        t = self.t
        if t[i].kind == "[":
            j = i + 1
            if j < len(t) and t[j].kind == "{":
                inner, j = self.body(j + 1, "}")
                end = _skip_group(t, i)
                spec = self._as_value(inner, "list")
                return spec, end
            return None, _skip_group(t, i)
        inner, j = self.body(i + 1, "}")
        j2 = _next_sig(t, j)
        if j2 < len(t) and t[j2].kind == "colon" and j2 + 1 < len(t) and t[j2 + 1].kind in "{[":
            value, j3 = self.structure(j2 + 1)
            keys = [k for k in inner.fields]
            if keys:                                    # {`a`, `b`}: {…}
                spec = Spec(kind="obj")
                for k in keys:
                    spec.fields[k] = _copy(value, inner.fields[k].optional)
                return spec, j3
            return self._map(value), j3               # {N, H, M}: {…}
        if inner.kind == "map":
            return inner, j
        if inner.fields:
            return Spec(kind="obj", fields=inner.fields), j
        return None, j                                  # словарь скаляров: {<t>}, {1, 3, 5, 10}

    @staticmethod
    def _as_value(inner: Spec, kind: str) -> Spec:
        return Spec(kind=kind, fields=inner.fields)

    @staticmethod
    def _map(value: Spec | None) -> Spec | None:
        if value is None:
            return None
        return Spec(kind="map", fields=value.fields, map_kind=value.kind)

    def body(self, i: int, closer: str | None) -> tuple[Spec, int]:
        """Поля до `closer` (None — до конца пункта). Возвращает Spec(kind obj|map) и индекс после закрытия."""
        t = self.t
        out = Spec(kind="obj")
        group: list[str] = []
        last: str | None = None
        top = closer is None
        while i < len(t):
            k = t[i].kind
            if closer is not None and k == closer:
                return out, i + 1
            if k == "bt":
                if top and _is_field(t[i], t, i) and self.explained(i):
                    i = self.dash(i + 2, closer, top, out, None)
                    group = []
                    continue
                if _is_field(t[i], t, i):
                    name = t[i].text
                    opt = name.endswith("?")
                    name = name.rstrip("?")
                    out.fields.setdefault(name, Spec(optional=opt))
                    group.append(name)
                    last = name
                i += 1
            elif k == "(":
                i = _skip_group(t, i)
            elif k == "in":
                i += 1
                if i < len(t) and t[i].kind == "{":
                    i = _skip_group(t, i)
            elif k in "{[":
                prev = t[i - 1].kind if i > 0 else ""
                if prev == "bt" and last is not None and t[i - 1].text.rstrip("?") == last:
                    spec, i = self.structure(i)
                    _attach(out, last, spec)
                    group = []
                else:
                    i = _skip_group(t, i)
            elif k == "colon":
                j = i + 1
                if j < len(t) and t[j].kind in "{[":
                    spec, i = self.structure(j)
                    if group:
                        for name in group:
                            _attach(out, name, spec)
                    else:                               # {<t>: {…}} — значения словаря
                        m = self._map(spec)
                        if m is not None:
                            out.kind, out.fields, out.map_kind = "map", m.fields, m.map_kind
                    group = []
                else:
                    i += 1
            elif k == "dash":
                i = self.dash(i + 1, closer, top, out, last)
                group = []
            elif k in ("semi", "stop") and top:
                group = []
                i += 1
            else:
                i += 1
        return out, i

    def dash(self, i: int, closer: str | None, top: bool, out: Spec, last: str | None) -> int:
        """Пояснение после тире: внутри узла — до запятой (или «— [{…}]» — вид значений);
        на верхнем уровне — до «;» перед именем поля или до конца предложения; «…: {…}» и
        «× {…}» — структура поля перед тире."""
        t = self.t
        if not top:
            j = _next_sig(t, i)
            if j < len(t) and t[j].kind in "{[":
                return _skip_group(t, j)
            while i < len(t):
                k = t[i].kind
                if k == closer or k == "comma":
                    return i
                if k in "{[(":
                    i = _skip_group(t, i)
                else:
                    i += 1
            return i
        times = False
        while i < len(t):
            k = t[i].kind
            if k == "(":
                i = _skip_group(t, i)
                continue
            if k == "times":
                times = True
                i += 1
                continue
            if k == "colon" and i + 1 < len(t) and t[i + 1].kind in "{[" and last is not None:
                spec, i = self.structure(i + 1)
                _attach(out, last, spec)
                return i
            if k in "{[":
                if times and last is not None and k == "{":
                    inner, j = self.body(i + 1, "}")
                    _attach(out, last, Spec(kind="list", fields=inner.fields))
                    return j
                i = _skip_group(t, i)
                continue
            if k == "stop":
                return i + 1
            if k in ("semi", "comma"):
                j = i + 1
                if j < len(t) and t[j].kind == "bt" and _is_field(t[j], t, j) and not self.explained(j):
                    return j
                i += 1
                continue
            i += 1
        return i


def _copy(spec: Spec | None, optional: bool) -> Spec:
    if spec is None:
        return Spec(optional=optional)
    return Spec(optional=optional, kind=spec.kind, fields=dict(spec.fields), map_kind=spec.map_kind)


def _attach(out: Spec, name: str, spec: Spec | None) -> None:
    cur = out.fields.setdefault(name, Spec())
    if spec is not None:
        cur.kind, cur.fields, cur.map_kind = spec.kind, spec.fields, spec.map_kind


# ------------------------------------------------------------------ разделы и пункты


def _sections(text: str, head: str, stop: str) -> dict[str, str]:
    start = text.index(head)
    end = text.index(stop, start)
    part = text[start:end]
    out: dict[str, str] = {}
    for m in re.finditer(r"^### (\w+)\n(.*?)(?=^### |\Z)", part, re.S | re.M):
        out[m.group(1)] = m.group(2)
    return out


def _items(body: str) -> list[tuple[str, list[str], bool]]:
    """Пункты раздела: (текст, вложенные пункты, абзац ли). Пункт — «* …» с колонки 0 или абзац."""
    lines = body.strip("\n").split("\n")
    items: list[tuple[str, list[str], bool]] = []
    cur: list[str] | None = None
    nested: list[str] = []
    sub: list[str] | None = None
    para = False

    def close() -> None:
        nonlocal cur, sub, nested
        if sub is not None:
            nested.append("\n".join(sub))
            sub = None
        if cur is not None:
            items.append(("\n".join(cur), nested, para))
        cur, nested = None, []

    for line in lines:
        if line.startswith("* "):
            close()
            cur, para = [line[2:]], False
        elif line.startswith("  * ") and cur is not None:
            if sub is not None:
                nested.append("\n".join(sub))
            sub = [line[4:]]
        elif not line.strip():
            close()
        elif sub is not None:
            sub.append(line.strip())
        elif cur is None:
            cur, para = [line], True
        else:
            cur.append(line.strip())
    close()
    return items


def _parse_item(text: str, nested: list[str], paragraph: bool = False) -> Spec:
    toks = tokenize(text)
    spec, _ = Parser(toks).body(0, None)
    if paragraph and toks and toks[0].kind != "bt":      # вступление раздела: только поля со структурой
        spec.fields = {k: v for k, v in spec.fields.items() if v.kind is not None}
    if nested:
        sig = [t for t in toks if t.kind != "word"]
        target = next((t.text.rstrip("?") for t in toks if t.kind == "bt" and _is_field(t, toks, toks.index(t))), None)
        if target is not None and sig and sig[-1].kind == "colon":
            node = spec.fields.setdefault(target, Spec())
            node.kind = "obj"
            for sub in nested:
                inner = _parse_item(sub, [])
                node.fields.update(inner.fields)
    return spec


def block_specs(text: str | None = None) -> dict[str, Spec]:
    """Блок П§2 → Spec (поля блока и вложенные узлы); `valuation_history` — из «Поля» П§4."""
    text = text if text is not None else PAYLOAD_DOC.read_text(encoding="utf-8")
    blocks: dict[str, Spec] = {}
    for name, body in _sections(text, "## 2. Поля блоков", "## 3. ").items():
        spec = Spec(kind="obj")
        for item, nested, para in _items(body):
            got = _parse_item(item, nested, para)
            for k, v in got.fields.items():
                if k in spec.fields and spec.fields[k].kind is not None and v.kind is None:
                    continue
                spec.fields[k] = v
        blocks[name] = spec
    sec4 = text[text.index("## 4. "):text.index("## 5. ")]
    m = re.search(r"^\* \*\*Поля\*\*(.*?)(?=^\* |\Z)", sec4, re.S | re.M)
    if m:
        toks = tokenize(m.group(1).replace("\n", " "))
        got, _ = Parser(toks).body(0, None)
        blocks["valuation_history"] = Spec(kind="obj", fields=got.fields)
    return blocks


def required_from_doc(text: str | None = None) -> dict[str, dict[str, bool]]:
    """Узел (как ключ REQUIRED_FIELDS) → {поле: необязательно ли}."""
    out: dict[str, dict[str, bool]] = {}

    def walk(path: str, spec: Spec) -> None:
        if spec.kind is None or not spec.fields:
            return
        if spec.kind == "map":
            child = f"{path}.*" + ("[]" if spec.map_kind == "list" else "")
            out[child] = {k: v.optional for k, v in spec.fields.items()}
            for k, v in spec.fields.items():
                walk(f"{child}.{k}", v)
            return
        node = path + ("[]" if spec.kind == "list" else "")
        out[node] = {k: v.optional for k, v in spec.fields.items()}
        for k, v in spec.fields.items():
            walk(f"{node}.{k}", v)

    for name, spec in block_specs(text).items():
        walk(name, spec)
    return out


def required_from_constants(required: dict[str, tuple[str, ...]]) -> dict[str, dict[str, bool]]:
    return {node: {f.rstrip("?"): f.endswith("?") for f in fields} for node, fields in required.items()}


def diff(doc: dict[str, dict[str, bool]], code: dict[str, dict[str, bool]]) -> list[str]:
    out = []
    for node in sorted(set(doc) | set(code)):
        a, b = doc.get(node), code.get(node)
        if a is None:
            out.append(f"{node}: в REQUIRED_FIELDS есть, в П§2 нет узла")
            continue
        if b is None:
            out.append(f"{node}: в П§2 есть, в REQUIRED_FIELDS нет узла ({', '.join(sorted(a))})")
            continue
        for f in sorted(set(a) | set(b)):
            if f not in b:
                out.append(f"{node}.{f}: в П§2 есть, в REQUIRED_FIELDS нет")
            elif f not in a:
                out.append(f"{node}.{f}: в REQUIRED_FIELDS есть, в П§2 нет")
            elif a[f] != b[f]:
                out.append(f"{node}.{f}: пометка «?» — П§2 {a[f]}, REQUIRED_FIELDS {b[f]}")
    return out


def render(doc: dict[str, dict[str, bool]]) -> Iterable[str]:
    """Черновик литерала REQUIRED_FIELDS по П§2 (для правки ядра, не для сборки)."""
    for node, fields in doc.items():
        yield f'    "{node}": _F("' + " ".join(f + ("?" if o else "") for f, o in fields.items()) + '"),'
