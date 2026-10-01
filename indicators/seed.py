"""История рядов для ретро-проверки и нау-каста: `data/indicators/seed/*.json` из листов этапа 1.

`python -m indicators.seed [--check]` читает листы этапа 1 из `$BANK_HANDOFF_DIR`
(в CI и на сервере не задаётся: файлы истории закоммичены) и пишет:

* `ras_release.json` — релиз РСБУ банка по месяцам (`stage1/ras/ras_release_monthly.csv`):
  значения с источником (документ, sha256, страница) и дата релиза;
* `ifrs_quarters.json` — ЧП акционерам квартала МСФО (`stage1/ifrs/ifrs_quarterly.csv`,
  `np_shareholders`) и дата выхода отчёта (`research/facts/np_inputs.json`);
* `mgmt_quarters.json` — ЧПМ и CoR квартала, упр. базис (`stage1/mgmt/mgmt_kpi.csv`);
* `consensus.json` — консенсус прибыли квартала перед МСФО (`research/facts/np_inputs.json`);
* `form102.json` — ЧП и прибыль до налога нарастающим по форме 0409102
  (`stage1/ras/cbr_f102_monthly.csv`, символы 61101 и 01000);
* `form101.json` — процентные активы банка на конец месяца по форме 0409101
  (`stage1/ras/raw/101/*.xml`, эпоха агрегатов с 06.2023) по определению фактов
  (`data/facts/bridge_ras_ifrs.json → iea.codes`) и средние квартала (`nowcast.iea_quarter_mean`) —
  история ряда `cbr.f101.iea` нау-каста ЧПМ; совпадение со стороной РСБУ моста — тест.

`--check` — пересборка совпадает с файлами (выход 1 при расхождении). `import_to_store`
кладёт историю в ряды хранилища с моментом, когда число стало известно (дата
релиза или отчёта), — чтобы «что было известно на дату» было честным и для истории.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from indicators import cbr_forms, config, nowcast, periods
from indicators.store import Point, Store

SEED_FILES = ("ras_release", "ifrs_quarters", "mgmt_quarters", "consensus", "form102", "form101")
FIRST_YEAR_KEY = "first_month"
RAS_PERCENT = frozenset({"roe_ytd", "roe_m", "cor_ytd", "cor_m", "cir_ytd", "n1_0", "n1_1", "n1_2"})
RAS_METRICS = ("np_ytd", "np_m", "nii_ytd", "nii_m", "fee_ytd", "fee_m", "prov_ytd", "prov_m", "opex_ytd",
               "opex_m", "pretax_ytd", "pretax_m", "roe_ytd", "roe_m", "cor_ytd", "cor_m", "cir_ytd",
               "loans_ul", "loans_fl", "funds_ul", "funds_fl", "cap_base", "cap_main", "cap_total",
               "n1_0", "n1_1", "n1_2")
# Момент известности — 10:00 МСК дня релиза или отчёта (07:00 UTC).
KNOWN_AT_UTC = "T07:00:00+00:00"


def handoff() -> Path:
    value = os.environ.get(config.HANDOFF_ENV)
    if not value:
        raise SystemExit(f"нужна переменная {config.HANDOFF_ENV} (папка передачи с листами этапа 1)")
    return Path(value)


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _num(x: str) -> float | None:
    x = (x or "").strip()
    return float(x) if x else None


def build_ras(root: Path) -> dict[str, Any]:
    wide = {r["month"]: r for r in _rows(root / "stage1/ras/ras_release_monthly_wide.csv")}
    months: dict[str, Any] = {}
    for r in _rows(root / "stage1/ras/ras_release_monthly.csv"):
        metric = r["metric"]
        if metric not in RAS_METRICS:
            continue
        v = _num(r["value"])
        if v is None:
            continue
        if metric in RAS_PERCENT:
            v = round(v / 100.0, 10)
        m = periods.from_stage1_month(r["month"])
        entry = months.setdefault(m, {"release_date": (wide.get(r["month"]) or {}).get("release_date") or None,
                                      "values": {}})
        src = r["source_ref"] or r["source_kind"]
        entry["values"][metric] = {"v": v, "src": f"{r['source_kind']}: {src}"[:300],
                                   "confirmed_by": r["confirmed_by"] or None}
    return {"basis": "ras", "units": {"money": "RUB bn (резервы и расходы — со знаком минус, как в релизе)",
                                      "percent": "доли"},
            "source": "stage1/ras/ras_release_monthly.csv (сборка этапа 1: PDF, скриншоты, Интерфакс, текст)",
            "months": dict(sorted(months.items()))}


def build_ifrs(root: Path) -> dict[str, Any]:
    np_inputs = json.loads((root / "research/facts/np_inputs.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for r in _rows(root / "stage1/ifrs/ifrs_quarterly.csv"):
        if r["metric"] != "np_shareholders":
            continue
        q = periods.from_stage1_quarter(r["period"])
        rep = (np_inputs.get("ifrs") or {}).get(q)
        out[q] = {"ni_sh": {"v": float(r["value"]), "src": f"{r['doc']}, с. {r['page']}, sha256 {r['sha256'][:12]}"},
                  "report_date": {"v": rep[1] if rep else None,
                                  "src": (rep[2] if rep else "не найдена")[:200]}}
    return {"basis": "ifrs", "unit": "RUB bn", "target": "ni_q",
            "source": "stage1/ifrs/ifrs_quarterly.csv (np_shareholders); даты — research/facts/np_inputs.json",
            "quarters": dict(sorted(out.items()))}


def build_mgmt(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in _rows(root / "stage1/mgmt/mgmt_kpi.csv"):
        key = {"nim": "nim", "cor_total": "cor"}.get(r["metric"])
        if key is None or not periods.is_quarter(r["period"]):
            continue
        out.setdefault(r["period"], {})[key] = {
            "v": round(float(r["value"]) / 100.0, 10),
            "src": f"{r['doc']}, с. {r['page']}, sha256 {r['sha256'][:12]}"}
    return {"basis": "mgmt", "unit": "share", "source": "stage1/mgmt/mgmt_kpi.csv (nim, cor_total)",
            "quarters": dict(sorted(out.items()))}


def build_consensus(root: Path) -> dict[str, Any]:
    np_inputs = json.loads((root / "research/facts/np_inputs.json").read_text(encoding="utf-8"))
    out = {q: {"v": float(v[0]), "src": str(v[1])[:200]} for q, v in (np_inputs.get("cons") or {}).items()}
    return {"basis": "ifrs", "unit": "RUB bn", "target": "ni_q",
            "source": "research/facts/np_inputs.json (cons: консенсус перед отчётом, вторичные источники)",
            "quarters": dict(sorted(out.items()))}


def build_form102(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    names = {"61101": "ni_ytd", "01000": "pretax_ytd"}
    for r in _rows(root / "stage1/ras/cbr_f102_monthly.csv"):
        key = names.get(r["symbol"])
        v = _num(r["value_ytd_thousand_rub"])
        if key is None or v is None:
            continue
        m = periods.form_month(r["form_date"])
        out.setdefault(m, {"form_date": r["form_date"], "sha256": r["sha256"]})[key] = round(v * 1e-6, 6)
    kept = out
    return {"basis": "ras", "unit": "RUB bn", "source": "stage1/ras/cbr_f102_monthly.csv (ЦБ SOAP Data102FXML)",
            "note": "дата формы — на 1-е число месяца после месяца данных; момент первой выкладки до запуска "
                    "сборщика неизвестен", "months": dict(sorted(kept.items()))}


def build_form101(root: Path) -> dict[str, Any]:
    definition = (config.bridge_ras_ifrs() or {}).get("iea")
    if not isinstance(definition, dict) or not definition.get("codes"):
        raise SystemExit("нет определения процентных активов: data/facts/bridge_ras_ifrs.json → iea.codes")
    months: dict[str, Any] = {}
    for path in sorted((root / "stage1/ras/raw/101").glob("*.xml")):
        body = path.read_bytes()
        iea = cbr_forms.f101_derived(cbr_forms.parse_f101(body), iea=definition).get("iea")
        if iea is None:
            continue
        months[periods.form_month(path.stem)] = {"form_date": path.stem,
                                                  "sha256": hashlib.sha256(body).hexdigest()[:16],
                                                  "iea": round(iea, 6)}
    values = {m: e["iea"] for m, e in months.items()}
    quarters = {}
    for q in sorted({periods.quarter_of_month(m) for m in months}):
        avg = nowcast.iea_quarter_mean(values, q)
        if avg is not None:
            quarters[q] = {"iea_avg": round(avg, 6), "months": nowcast.iea_months(q)}
    return {"basis": "ras", "unit": "RUB bn",
            "definition": ("процентные активы банка по форме 0409101 — коды фактов "
                           "(data/facts/bridge_ras_ifrs.json → iea.codes, codes_required); среднее квартала — "
                           "(конец прошлого квартала + конец квартала) / 2, как сторона РСБУ моста iea"),
            "codes": definition.get("codes"), "codes_required": definition.get("codes_required"),
            "source": "stage1/ras/raw/101/*.xml (ЦБ SOAP Data101FNewXML)",
            "note": "дата формы — на 1-е число месяца после месяца данных; момент первой выкладки до запуска "
                    "сборщика неизвестен", "months": dict(sorted(months.items())), "quarters": quarters}


def build_all(root: Path) -> dict[str, dict[str, Any]]:
    return {"ras_release": build_ras(root), "ifrs_quarters": build_ifrs(root), "mgmt_quarters": build_mgmt(root),
            "consensus": build_consensus(root), "form102": build_form102(root), "form101": build_form101(root)}


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m indicators.seed")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    built = build_all(handoff())
    bad = []
    for name, obj in built.items():
        path = config.SEED_DIR / f"{name}.json"
        text = dump(obj)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                bad.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    if bad:
        print("история расходится со сборкой: " + ", ".join(bad), file=sys.stderr)
        return 1
    print("история: " + ", ".join(built))
    return 0


# ------------------------------------------------------------------ импорт в хранилище

def _at(day: str | None, shift_days: int = 0) -> str | None:
    if not day:
        return None
    return (date.fromisoformat(day) + timedelta(days=shift_days)).isoformat() + KNOWN_AT_UTC


def import_to_store(store: Store, seed_dir: Path | None = None) -> list[str]:
    """История в ряды хранилища (идемпотентно: одинаковые точки не дублируются)."""
    touched: list[str] = []
    ras = config.load_seed("ras_release", seed_dir) or {}
    by_metric: dict[str, list[Point]] = {}
    for m, entry in (ras.get("months") or {}).items():
        at = _at(entry.get("release_date")) or _at(periods.month_end(m).isoformat(), 15)
        for metric, node in entry["values"].items():
            by_metric.setdefault(metric, []).append(Point(m, node["v"], at, "ok", "seed: " + node["src"][:120]))
    for metric, pts in by_metric.items():
        sid = f"ras.release.{metric}"
        store.upsert(sid, pts, basis="ras", label=f"релиз РСБУ банка: {metric}",
                     unit="share" if metric in RAS_PERCENT else "RUB bn")
        touched.append(sid)
    ifrs = config.load_seed("ifrs_quarters", seed_dir) or {}
    report_dates = {q: (e.get("report_date") or {}).get("v") for q, e in (ifrs.get("quarters") or {}).items()}
    pts = []
    for q, e in (ifrs.get("quarters") or {}).items():
        at = _at(report_dates.get(q)) or _at(periods.quarter_end(q).isoformat(), 60)
        pts.append(Point(q, e["ni_sh"]["v"], at, "ok", "seed: " + e["ni_sh"]["src"]))
    if pts:
        # Момент точки — выход отчёта: по нему ретро знает, что было известно на дату.
        store.upsert("actual.ni_q", pts, basis="ifrs", unit="RUB bn", label="ЧП акционерам квартала, МСФО")
        touched.append("actual.ni_q")
    mgmt = config.load_seed("mgmt_quarters", seed_dir) or {}
    for key in ("nim", "cor"):
        pts = []
        for q, e in (mgmt.get("quarters") or {}).items():
            if key in e:
                at = _at(report_dates.get(q)) or _at(periods.quarter_end(q).isoformat(), 60)
                pts.append(Point(q, e[key]["v"], at, "ok", "seed: " + e[key]["src"]))
        if pts:
            store.upsert(f"actual.{key}_q", pts, basis="mgmt", unit="share", label=f"{key} квартала, упр.")
            touched.append(f"actual.{key}_q")
    cons = config.load_seed("consensus", seed_dir) or {}
    pts = [Point(q, e["v"], _at(report_dates.get(q), -1) or _at(periods.quarter_end(q).isoformat(), 55), "ok",
                 "seed: " + e["src"][:120]) for q, e in (cons.get("quarters") or {}).items()]
    if pts:
        store.upsert("consensus.ni_q", pts, basis="ifrs", unit="RUB bn", label="консенсус ЧП квартала")
        touched.append("consensus.ni_q")
    f102 = config.load_seed("form102", seed_dir) or {}
    for key in ("ni_ytd", "pretax_ytd"):
        pts = []
        for m, e in (f102.get("months") or {}).items():
            if key in e:
                # Момент первой выкладки до запуска сборщика неизвестен: берётся верхняя оценка
                # (26-е число месяца формы), чтобы история не знала больше, чем знала на деле.
                at = _at(e["form_date"], 25)
                pts.append(Point(m, e[key], at, "ok", f"seed: 0409102 на {e['form_date']}, sha256 {e['sha256'][:12]}"))
        if pts:
            store.upsert(f"cbr.f102.{key}", pts, basis="ras", unit="RUB bn", label=f"форма 0409102: {key}")
            touched.append(f"cbr.f102.{key}")
    f101 = config.load_seed("form101", seed_dir) or {}
    pts = [Point(m, e["iea"], _at(e["form_date"], 25), "ok", f"seed: 0409101 на {e['form_date']}, sha256 {e['sha256']}")
           for m, e in (f101.get("months") or {}).items()]
    if pts:
        # Как у формы 102: момент первой выкладки — верхняя оценка (26-е число месяца формы).
        store.upsert("cbr.f101.iea", pts, basis="ras", unit="RUB bn", label="форма 0409101: процентные активы")
        touched.append("cbr.f101.iea")
    return touched


if __name__ == "__main__":
    raise SystemExit(main())
