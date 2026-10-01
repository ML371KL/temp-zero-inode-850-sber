"""История рядов `data/indicators/seed/`: у каждого числа источник, импорт идемпотентен, сборка воспроизводима."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from indicators import config, periods, seed
from indicators.store import Store


@pytest.mark.tact
def test_every_seed_number_has_a_source():
    for name in seed.SEED_FILES:
        data = config.load_seed(name)
        assert data is not None, name
        assert data.get("source") and data.get("basis") in ("ras", "ifrs", "mgmt"), name
    ras = config.load_seed("ras_release")
    for m, entry in ras["months"].items():
        periods.parse_month(m)
        for metric, node in entry["values"].items():
            assert metric in seed.RAS_METRICS and node["src"], (m, metric)
    for q, e in config.load_seed("ifrs_quarters")["quarters"].items():
        periods.parse_quarter(q)
        assert e["ni_sh"]["src"] and "sha256" in e["ni_sh"]["src"]
    for q, e in config.load_seed("mgmt_quarters")["quarters"].items():
        assert all(n["src"] for n in e.values())
        assert all(0 <= n["v"] < 0.2 for n in e.values())          # доли, не проценты


@pytest.mark.tact
def test_form101_history_is_the_ras_side_of_the_facts_asset_bridge():
    """Одно определение процентных активов на обе стороны моста `bridge_ras_ifrs.iea` (B1): коды фактов,
    среднее (конец прошлого квартала + конец квартала) / 2; разошлись — пересобрать `python -m indicators.seed`."""
    f101 = config.load_seed("form101")
    iea = (config.bridge_ras_ifrs() or {}).get("iea")
    if not isinstance(iea, dict) or not iea.get("quarters"):
        pytest.skip("в фактах ещё нет моста процентных активов")
    assert f101["codes"] == iea["codes"] and f101["codes_required"] == iea["codes_required"]
    for row in iea["quarters"]:
        q, ras = row["period"], row["ras"]["v"]
        assert f101["quarters"][q]["iea_avg"] == pytest.approx(ras, abs=1e-3), q
    for m, e in f101["months"].items():
        assert periods.form_month(e["form_date"]) == m and e["sha256"] and e["iea"] > 0


@pytest.mark.tact
def test_import_is_idempotent_and_dates_knowledge(tmp_path):
    store = Store(tmp_path)
    touched = seed.import_to_store(store)
    counts = {sid: len(store.load(sid).points) for sid in touched}
    seed.import_to_store(store)
    assert {sid: len(store.load(sid).points) for sid in touched} == counts
    s = store.load("ras.release.np_ytd")
    rel = config.load_seed("ras_release")["months"]
    m = max(rel)
    assert s.first_seen(m)[:10] == rel[m]["release_date"]
    act = store.load("actual.ni_q")
    q = max(config.load_seed("ifrs_quarters")["quarters"])
    assert act.first_seen(q)[:10] == config.load_seed("ifrs_quarters")["quarters"][q]["report_date"]["v"]
    iea = store.load("cbr.f101.iea")
    e = config.load_seed("form101")["months"][max(config.load_seed("form101")["months"])]
    assert iea.first_seen(max(iea.history()))[:10] > e["form_date"]             # выкладка — после даты формы


@pytest.mark.archive
def test_seed_rebuild_matches_committed_files():
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root:
        pytest.skip("нужна папка передачи (BANK_HANDOFF_DIR)")
    built = seed.build_all(Path(root))
    for name, obj in built.items():
        assert (config.SEED_DIR / f"{name}.json").read_text(encoding="utf-8") == seed.dump(obj), name
