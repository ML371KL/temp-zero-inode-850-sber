"""Полнота договора выпуска (П§2 вступление, П§7 п. 1; W1/C2): каждый узел и каждое поле П§2 есть в
`REQUIRED_FIELDS` с той же пометкой «?», и наоборот; разбор записи полей проверен на образце."""

from __future__ import annotations

import pytest

from model.payload import REQUIRED_FIELDS
from tests.support_core2_doc import diff, required_from_constants, required_from_doc

SAMPLE = """## 2. Поля блоков

### alpha
* `a`, `b?` (пояснение с `ссылкой`), `c` {`x`, `y?`} — пояснение; `d` [{`p`, `q` (`u` | `v`)}].
* `e`: {<t>: {`f`, `g`}}, `h` {N, H, M}: {`i`}, `j` {<t>}, `k` [N, H, M].
* `l` {`m`, `n` — пояснение внутри, с `не_поле`, `o` {`z`} — [{`year`, `value`}], `r`}.
* `s` — пояснение: {`key` ∈ {`p1`, `p2`}, `w`} — ещё пояснение; `p1` — значение, а не поле.
* `t` {`t1`, `t2`}: {`val`}; `u` — 36 × {`c1`}.
* `head` — узел с пунктами:
  * `h1`, `h2` [{`h3`}]

### beta
Вступление раздела: база `не_поле`, без структуры.
По `left`, `right` (пояснение): {`one`, `two`}.

## 3. Проверки

## 4. История

* **Поля** (массивы): `date`, `price` {<t>: []}; плюс `rows` [{`from`, `to`}].

## 5. Экраны
"""


@pytest.mark.tact
def test_the_notation_parser_on_a_sample():
    got = required_from_doc(SAMPLE)
    assert got["alpha"] == {"a": False, "b": True, "c": False, "d": False, "e": False, "h": False, "j": False,
                            "k": False, "l": False, "s": False, "t": False, "u": False, "head": False}
    assert got["alpha.c"] == {"x": False, "y": True}
    assert got["alpha.d[]"] == {"p": False, "q": False}
    assert got["alpha.e.*"] == {"f": False, "g": False}
    assert got["alpha.h.*"] == {"i": False}
    assert got["alpha.l"] == {"m": False, "n": False, "o": False, "r": False}
    assert got["alpha.l.o"] == {"z": False}
    assert got["alpha.s"] == {"key": False, "w": False}
    assert got["alpha.t"] == {"t1": False, "t2": False}
    assert got["alpha.t.t1"] == got["alpha.t.t2"] == {"val": False}
    assert got["alpha.u[]"] == {"c1": False}
    assert got["alpha.head"] == {"h1": False, "h2": False}
    assert got["alpha.head.h2[]"] == {"h3": False}
    assert got["beta"] == {"left": False, "right": False}
    assert got["beta.left"] == got["beta.right"] == {"one": False, "two": False}
    assert got["valuation_history"] == {"date": False, "price": False, "rows": False}
    assert got["valuation_history.rows[]"] == {"from": False, "to": False}
    assert "alpha.j.*" not in got and "alpha.k[]" not in got           # словари и списки скаляров — не узлы


@pytest.mark.docs
def test_required_fields_match_the_payload_doc():
    """Узлы и поля П§2 ↔ REQUIRED_FIELDS в обе стороны, с той же пометкой «?»."""
    problems = diff(required_from_doc(), required_from_constants(dict(REQUIRED_FIELDS)))
    assert not problems, "\n".join(problems[:40])


@pytest.mark.tact
def test_required_fields_cover_nested_nodes():
    """Договор — не только верхние блоки: вложенные узлы П§2 (списки, словари, объекты) обязательны."""
    nested = [k for k in REQUIRED_FIELDS if "." in k]
    assert len(nested) >= 150
    for node in ("dividends.next_expected", "nii.transmission.pairs[]", "market.prices.*", "regimes.near_nim_shift[]",
                 "judgements.rows[]", "checks.gates[].corridor", "capital.titles", "paths.annual[]"):
        assert node in REQUIRED_FIELDS, node
    assert "in_band" in REQUIRED_FIELDS["judgements.rows[]"]
    assert {"dps_mean", "p_cancel", "dps_policy"} <= set(REQUIRED_FIELDS["dividends.next_expected"])
    assert "cir_mgmt" in REQUIRED_FIELDS["paths.annual[]"]
    assert {"n1_0", "n1_2"} <= set(REQUIRED_FIELDS["capital.titles"])


@pytest.mark.docs
def test_gate_names_and_titles_match_the_payload_doc_and_the_core_order():
    """П§3.2 ↔ подписи гейтов выпуска и порядок гейтов ядра (М§14.2): имена, подписи, порядок."""
    import re
    from pathlib import Path

    from model.checks import GATES
    from model.payload import GATE_TITLES
    text = (Path(__file__).resolve().parents[1] / "docs" / "PAYLOAD.md").read_text(encoding="utf-8")
    part = text[text.index("### 3.2. Гейты правдоподобия"):text.index("### 3.3. Флаги")]
    rows = re.findall(r"^\| `([a-z0-9_]+)` \| (.+?) \|$", part, re.M)
    rows = [r for r in rows if r[0] != "name"]             # шапка таблицы
    assert rows == list(GATE_TITLES.items())
    assert [name for name, _ in rows] == list(GATES)
