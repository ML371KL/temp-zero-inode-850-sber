"""Что изменилось между выпусками (М§16): шаги в порядке, перекат даты, сумма к печатаемой разнице."""

from __future__ import annotations

from datetime import timedelta

import pytest

from model import payload as P
from model.attribution import ORDER, attribute, inputs_snapshot
from model.checks import check_gates
from model.grid import LiveInputs, live_from_book, run_grid
from tests.support_core2 import TODAY, book_and_facts, book_prices, explained, outputs

pytestmark = pytest.mark.tact            # быстрые тесты ядра — в такте сервера (W1/C1)


def _payload(day, *, previous=None):
    book, facts = book_and_facts()
    tickers = [str(t) for t in book.get("meta.company.tickers")]
    px = book_prices(book)
    out = outputs({t: [(day, px[t])] for t in tickers})
    expl = explained(check_gates(run_grid(book, facts, live_from_book(book, facts)), today=TODAY))
    rel = P.make_release(live=True, fast=True, today=day, book=book, facts=facts, outputs=out, explanations=expl,
                         notes=[], draws=6)
    rel.previous = previous
    return rel, P.build_payload(rel)


@pytest.fixture(scope="module")
def pair():
    _, a = _payload(TODAY)
    rel_b, b = _payload(TODAY + timedelta(days=14), previous=a)
    return a, rel_b, b


def test_steps_come_in_the_fixed_order_and_sum_to_the_printed_change(pair):
    a, rel_b, b = pair
    ch = b["changes"]["vs_previous"]
    assert [r["component"] for r in ch["rows"]] == list(ORDER)
    printed = b["fair_value"]["printed_central"] - a["fair_value"]["printed_central"]
    assert sum(r["point_rub"] for r in ch["rows"]) == pytest.approx(printed, abs=0.06)
    assert ch["total_point_rub"] == pytest.approx(printed, abs=0.06)
    assert ch["previous_sha"] == a["meta"]["payload_sha256"]


def test_roll_of_the_valuation_date_is_the_only_move_on_a_frozen_book(pair):
    a, rel_b, b = pair
    rows = {r["component"]: r for r in b["changes"]["vs_previous"]["rows"]}
    book, facts = rel_b.book, rel_b.facts
    snap = a["changes"]["snapshot"]
    live_a = LiveInputs(valuation_date=P.to_date(snap["valuation_date"]), prices=snap["prices"],
                        price_dates={t: P.to_date(d) for t, d in snap["price_dates"].items()}, register=())
    roll = run_grid(book, facts, rel_b.live).point - run_grid(book, facts, live_a).point
    assert rows["valuation_date"]["point_rub"] == pytest.approx(roll, abs=0.01)
    assert roll > 0                                                    # замороженная книга — точка растёт с датой
    for comp in ("register", "facts", "book", "worlds"):
        assert rows[comp]["point_rub"] == pytest.approx(0.0, abs=0.01), comp
    assert abs(rows["engine"]["point_rub"]) <= 0.01                   # тот же код — остаток ≈ 0


def test_snapshot_names_the_inputs():
    book, facts = book_and_facts()
    snap = inputs_snapshot(book, facts, live_from_book(book, facts))
    assert {"valuation_date", "prices", "register", "book_digest", "facts_digest", "worlds_digest"} <= set(snap)
    assert snap["book_digest"] == book.digest and snap["facts_digest"] == facts.digest


def test_no_snapshot_no_steps():
    book, facts = book_and_facts()
    assert attribute({"fair_value": {"central": 400.0}}, book, facts, live_from_book(book, facts),
                     engine_commit="0" * 40) == []
