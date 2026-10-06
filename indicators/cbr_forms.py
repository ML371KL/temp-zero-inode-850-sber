"""ЦБ CreditOrgInfo по регномеру эмитента: даты и формы 101/102/123/135 с винтажами.

SOAP `CreditOrgInfo.asmx` отдаёт только ПОСЛЕДНЮЮ версию формы; дату первой
выкладки и перевыкладки фиксирует только собственный сборщик (DESIGN §6.4):

* ежедневно `GetDatesForF<форма>` (регномер — в поле с опечаткой `CredprgNumber`);
  новая дата — полная форма в сырой архив (защищённый), разбор, ряды, момент
  первого появления `first_seen_utc` в индексе;
* раз в неделю — перекачка последних `RECHECK_DATES` дат и сравнение sha256: другой
  ответ — новый винтаж (точки рядов ложатся рядом, прежние не стираются); неделя
  отсчитывается от последней удачной сверки даты (`checked`);
* первый запуск на пустом состоянии не качает всю историю: прошлые даты помечаются
  известными без `first_seen_utc` (история — в `data/indicators/seed/`), качаются
  последние `RECHECK_DATES`.

Дата «обработана», только когда форма скачана И разобрана. Три состояния записи индекса:
обнаружена (`versions` пуст), скачана, но не разобрана (`parsed: false` у последней
версии), в рядах. Первые два — очередь: дата запрашивается в каждом такте, пока не
дойдёт до рядов (и тогда, когда список дат её уже не называет), и каждый такт сообщает о
ней деградацией — повтор сбора внутри такта (`ops/run.sh`) и тревога «невосполнимый
источник не собран» работают по ней, а не гаснут на втором проходе. Заглушка сервиса с
кодом 200 — не ответ: список без единой даты и «форма» без конверта SOAP — отказ с
повтором, версией формы заглушка не становится. Неразобранная попытка — не винтаж: в
записи она одна, последней, и уступает место следующему ответу; о пересмотре (`revised`)
такт сообщает, когда новый винтаж лёг в ряды, а не когда пришёл другой ответ.

Разбор (перенос разборщиков этапа 1): 102 — прибыль после налога `61101` (минус
убыток `61102`), до налога `01000` (минус `02000`), налог `03000` − `04000`, итоги
разделов; 135 — Н1.0/Н1.1/Н1.2; 123 — капитал (`000` итого, `102` базовый, `105`
добавочный, `203` дополнительный); 101 — агрегаты с 06.2023 и производные ряды обеих
эпох. Единицы форм 101/102/123 — тыс. ₽ (в рядах — млрд ₽), 135 — % (в рядах — доли).
Провал раскрытия 2022–2023 — `null` явно, не 0.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, timezone
from typing import Any, Mapping

from indicators import config, http, periods
from indicators.sources import FAILED, Context, Result
from indicators.store import Store, point

URL = "https://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx"
SOURCE = "cbr_forms"
FORMS = ("101", "102", "123", "135")
RECHECK_DATES = 15
RECHECK_EVERY_DAYS = 7
INDEX_FILE = "cbr_forms/index.json"
THOUSAND_TO_BN = 1e-6

F102_NI, F102_NI_LOSS = "61101", "61102"
F102_PRETAX, F102_PRETAX_LOSS = "01000", "02000"
F102_TAX, F102_TAX_INCOME = "03000", "04000"
F102_INCOME_TOTAL, F102_EXPENSE_TOTAL = "19999", "29999"
F123_CODES = {"000": "capital_total", "102": "capital_base", "105": "capital_additional",
              "203": "capital_tier2"}
F135_NORMS = {"Н1.0": "n1_0", "Н1.1": "n1_1", "Н1.2": "n1_2"}


class FormError(RuntimeError):
    """Ответ формы не разобран."""


# ------------------------------------------------------------------ запросы

def dates_request(form: str, regnum: str) -> tuple[str, str]:
    return f"GetDatesForF{form}", f"<CredprgNumber>{regnum}</CredprgNumber>"


def form_request(form: str, regnum: str, on: str) -> tuple[str, str]:
    stamp = f"{on}T00:00:00"
    if form == "102":
        return "Data102FXML", f"<CredorgNumber>{regnum}</CredorgNumber><dt>{stamp}</dt>"
    if form == "123":
        return "Data123FormFullXML", f"<CredorgNumber>{regnum}</CredorgNumber><OnDate>{stamp}</OnDate>"
    if form == "135":
        return "Data135FormFullXML", f"<CredorgNumber>{regnum}</CredorgNumber><OnDate>{stamp}</OnDate>"
    if form == "101":
        return "Data101FNewXML", f"<CredorgNumber>{regnum}</CredorgNumber><Dt>{stamp}</Dt>"
    raise FormError(f"неизвестная форма {form}")


# ------------------------------------------------------------------ разбор

def _blocks(xml: str, tag: str) -> list[dict[str, str]]:
    return [{k: _html.unescape(v) for k, v in re.findall(r"<(\w+)>(.*?)</\1>", b, re.S)}
            for b in re.findall(rf"<{tag}>(.*?)</{tag}>", xml, re.S)]


def parse_dates(body: bytes | str) -> list[str]:
    """Даты форм из ответа `GetDatesForF…`. Ответ без единой даты — не список (у эмитента формы есть
    за годы): заглушка сервиса с кодом 200 не должна читаться как «новых дат нет»."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    dates = sorted(set(re.findall(r"<dateTime>(\d{4}-\d{2}-\d{2})[^<]*</dateTime>", text)))
    if not dates:
        raise FormError("в ответе нет ни одной даты — это не список дат формы")
    return dates


def parse_f102(body: bytes | str) -> dict[str, float | None]:
    """Символы формы 102 → значение нарастающим итогом, тыс. ₽ (пустое — None)."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    out: dict[str, float | None] = {}
    for r in _blocks(text, "F102"):
        sym = r.get("symbol")
        if sym:
            v = r.get("tp3")
            out[sym] = float(v) if v not in (None, "") else None
    if not out:
        raise FormError("форма 102: нет ни одного символа")
    return out


def _net(values: dict[str, float | None], plus: str, minus: str) -> float | None:
    a, b = values.get(plus), values.get(minus)
    if a is None and b is None:
        return None
    return (a or 0.0) - (b or 0.0)


def f102_summary(values: dict[str, float | None]) -> dict[str, float | None]:
    """ЧП, прибыль до налога, налог и итоги доходов и расходов нарастающим итогом, млрд ₽.

    С 2023 года форма агрегирована (итоги разделов): ЧПД из неё не восстанавливается,
    поэтому для нау-каста берутся только прибыль и налог (REPORT-ras §1.3).
    """
    def bn(x):
        return None if x is None else x * THOUSAND_TO_BN
    return {"ni_ytd": bn(_net(values, F102_NI, F102_NI_LOSS)),
            "pretax_ytd": bn(_net(values, F102_PRETAX, F102_PRETAX_LOSS)),
            "tax_ytd": bn(_net(values, F102_TAX, F102_TAX_INCOME)),
            "income_total_ytd": bn(values.get(F102_INCOME_TOTAL)),
            "expense_total_ytd": bn(values.get(F102_EXPENSE_TOTAL))}


def parse_f135(body: bytes | str) -> dict[str, float | None]:
    """Нормативы формы 135, % (пустое — None)."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    out: dict[str, float | None] = {}
    for c, v in re.findall(r"<F135_3><C3>([^<]+)</C3>(?:<V3>([^<]*)</V3>)?", text):
        out[_html.unescape(c)] = float(v) if v else None
    if not out:
        raise FormError("форма 135: нет раздела 3")
    return out


def parse_f123(body: bytes | str) -> dict[str, float | None]:
    """Коды формы 123 → значение, тыс. ₽."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    out: dict[str, float | None] = {}
    for r in _blocks(text, "F123"):
        code = r.get("CODE")
        if code:
            v = r.get("VALUE")
            out[code] = float(v) if v not in (None, "") else None
    if not out:
        raise FormError("форма 123: нет строк")
    return out


def parse_f101(body: bytes | str) -> list[dict[str, Any]]:
    """Строки формы 101: {code, pln, ap, vitg, iitg} (тыс. ₽)."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    rows = []
    for r in _blocks(text, "F101"):
        if not r.get("numsc"):
            continue
        rows.append({"code": r["numsc"], "pln": r.get("pln"), "ap": r.get("ap"),
                     "in": float(r.get("vitg") or 0), "out": float(r.get("iitg") or 0)})
    if not rows:
        raise FormError("форма 101: нет строк")
    return rows


def f101_derived(rows: list[dict[str, Any]], *, iea: Mapping[str, Any] | None = None) -> dict[str, float | None]:
    """Сопоставимые ряды обеих эпох (перенос разбора этапа 1), млрд ₽; не раскрыто — None.

    `iea` — определение процентных активов из фактов (`bridge_ras_ifrs.json → iea`: `codes`,
    `codes_required`); без него ряд `iea` не считается.
    """
    vals: dict[tuple[str, str], float] = {}
    acc3: dict[tuple[str, str], float] = {}
    # Эпоха формы: с 06.2023 — агрегаты кредитов и депозитов («45.2», «42.2»), до — 5-значные счета.
    five = not any(re.fullmatch(r"4[25]\.\d", r["code"]) for r in rows)
    for r in rows:
        c, ap = r["code"], r["ap"]
        if len(c) == 5 and c.isdigit():
            if r["pln"] == "А":
                acc3[(c[:3], ap)] = acc3.get((c[:3], ap), 0.0) + r["out"]
        else:
            vals[(c, ap)] = r["out"]
    for k, v in acc3.items():
        vals.setdefault(k, v)

    def g(code: str, ap: str = "1") -> float | None:
        return vals.get((code, ap))

    def ssum(*keys: tuple[str, str]) -> float | None:
        xs = [vals.get(k) for k in keys]
        return None if all(x is None for x in xs) else sum(x or 0.0 for x in xs)

    if five:
        out = {"loans_ul_core_gross": ssum(("452", "1"), ("456", "1")),
               "loans_fl_gross": ssum(("455", "1"), ("457", "1")),
               "deposits_fl": ssum(("423", "2"), ("426", "2")),
               "deposits_ul_core": ssum(("421", "2"), ("425", "2"))}
    else:
        core = g("45.0") if g("45.0") is not None else g("45.1")
        out = {"loans_ul_core_gross": core, "loans_fl_gross": g("45.2"),
               "deposits_fl": g("42.2", "2"), "deposits_ul_core": g("42.1", "2")}
    out.update({
        "loans_fin_orgs": g("451"), "loans_ip": g("454"),
        "loans_state_orgs": ssum(*[(str(c), "1") for c in range(441, 451)]),
        "accounts_408_total": ssum(("408", "2"), ("408.1", "2")),
        "overdue_loans": g("458"),
        "iea": None if five or not iea else f101_iea(g, iea),
    })
    return {k: None if v is None else v * THOUSAND_TO_BN for k, v in out.items()}


def f101_iea(g, definition: Mapping[str, Any]) -> float | None:
    """Процентные активы банка по форме 0409101 (нау-каст ЧПМ, T3), тыс. ₽ — по определению фактов.

    Определение одно на обе стороны моста `bridge_ras_ifrs.iea` и живёт в фактах: `codes`
    {группа: [код | «код|запасной код»]} — исходящие остатки актива; `codes_required` — без них
    дата не считается; прочего кода нет в форме — нулевой остаток.
    """
    required = set(definition.get("codes_required") or [])
    if not definition.get("codes"):
        return None
    total = 0.0
    for codes in definition["codes"].values():
        for code in codes:
            value = next((g(c) for c in str(code).split("|") if g(c) is not None), None)
            if value is None:
                if code in required:
                    return None
                continue
            total += value
    return total


def month_from_ytd(ytd: dict[str, float]) -> dict[str, float]:
    """Помесячные значения из нарастающих итогов по месяцам данных (`2026M08`).

    Январь — сам итог; месяц без предыдущего месяца того же года — не считается
    (квартальные формы до 2025 года дают кварталы, а не месяцы).
    """
    out = {}
    for m, v in ytd.items():
        y, mm = periods.parse_month(m)
        if mm == 1:
            out[m] = v
            continue
        prev = ytd.get(periods.shift_month(m, -1))
        if prev is not None and v is not None:
            out[m] = v - prev
    return out


# ------------------------------------------------------------------ индекс винтажей

def load_index(store: Store) -> dict[str, Any]:
    return store.read_state(INDEX_FILE) or {"forms": {f: {} for f in FORMS}, "last_recheck": None}


def _series_points(form: str, on: str, body: bytes, fetched_at: str) -> dict[str, float | None]:
    month = periods.form_month(on)
    if form == "102":
        return {f"cbr.f102.{k}": v for k, v in f102_summary(parse_f102(body)).items()}
    if form == "135":
        norms = parse_f135(body)
        return {f"cbr.f135.{key}": (None if norms.get(name) is None else round(norms[name] / 100.0, 10))
                for name, key in F135_NORMS.items()}
    if form == "123":
        codes = parse_f123(body)
        return {f"cbr.f123.{key}": (None if codes.get(code) is None else codes[code] * THOUSAND_TO_BN)
                for code, key in F123_CODES.items()}
    if form == "101":
        definition = (config.bridge_ras_ifrs() or {}).get("iea")
        return {f"cbr.f101.{k}": v for k, v in f101_derived(
            parse_f101(body), iea=definition if isinstance(definition, dict) else None).items()}
    raise FormError(form)


def ingest(store: Store, form: str, on: str, body: bytes, *, fetched_at: str, source: str) -> list[str]:
    """Разбор формы на дату → точки рядов с моментом получения (винтаж). Возвращает ряды."""
    month = periods.form_month(on)
    touched = []
    for sid, value in _series_points(form, on, body, fetched_at).items():
        unit = "share" if sid.startswith("cbr.f135.") else "RUB bn"
        store.upsert(sid, [point(month, value, fetched_at=fetched_at, source=source)],
                     unit=unit, basis="ras", label=f"форма 0409{form}: {sid.rsplit('.', 1)[-1]}")
        touched.append(sid)
    return touched


NOTE_BEFORE_START = "до запуска сборщика"
SOAP_MARK, SOAP_HEAD = b"Envelope", 600       # ответ сервиса — конверт SOAP; знак конверта — в начале ответа


def is_service_document(body: bytes) -> bool:
    """Ответ — документ сервиса (конверт SOAP), а не страница-заглушка с кодом 200 вместо него."""
    return SOAP_MARK in body[:SOAP_HEAD]


def _unparsed(version: Mapping[str, Any]) -> bool:
    return version.get("parsed") is False


def pending(entry: Mapping[str, Any]) -> bool:
    """Дата известна, а данных в рядах нет: форма ещё не скачана или её последний ответ не разобран.
    Дата до запуска сборщика (история — в затравке) в очередь не идёт."""
    if entry.get("note"):
        return False
    versions = entry.get("versions") or []
    return not versions or _unparsed(versions[-1])


def recheck_due(entry: Mapping[str, Any], index: Mapping[str, Any], today: date) -> bool:
    """Пора ли перекачать дату: её последняя удачная сверка (`checked`; у записей прежнего формата — общая
    отметка `last_recheck`) — `RECHECK_EVERY_DAYS` дней назад или раньше."""
    last = entry.get("checked") or index.get("last_recheck")
    return not last or (today - date.fromisoformat(str(last)[:10])).days >= RECHECK_EVERY_DAYS


def collect(ctx: Context) -> Result:
    """Такт форм: новые даты и очередь (не скачано, не разобрано) — полная форма; раз в неделю —
    перекачка последних дат. Отметку получает только дата, чья форма дошла до рядов."""
    getter = ctx.getter or http.fetch
    sink = http.Sink(ctx.store, SOURCE)
    regnum = ctx.company["cbr_regnum"]
    res = Result(name="cbr_forms")
    index = load_index(ctx.store)
    failures = 0
    rechecked, recheck_clean = False, True
    for form in FORMS:
        known: dict[str, Any] = index["forms"].setdefault(form, {})
        op, inner = dates_request(form, regnum)
        try:
            listed = http.soap(URL, op, inner, getter=getter, sink=sink, name=f"dates_f{form}.xml")
            dates = parse_dates(listed.body)
        except (http.FetchError, FormError, UnicodeDecodeError) as exc:
            failures += 1
            recheck_clean = False
            # Нечитаемый список (заглушка с кодом 200) — как отказ сети: повтор сбора может его снять.
            res.degrade(f"форма {form}: список дат — {exc}",
                        retry=http.retryable(exc) or not isinstance(exc, http.FetchError))
            continue
        bootstrap = not known
        for on in dates:
            if on not in known:
                if bootstrap and on not in dates[-RECHECK_DATES:]:
                    known[on] = {"first_seen_utc": None, "versions": [], "note": NOTE_BEFORE_START}
                    continue
                known[on] = {"first_seen_utc": None if bootstrap else listed.fetched_at, "versions": []}
        # Очередь — по записям, а не по списку: дата, которую список перестал называть, не теряется молча.
        queue = sorted(on for on, entry in known.items() if pending(entry))
        due = [on for on in dates[-RECHECK_DATES:]
               if on not in queue and not known[on].get("note") and recheck_due(known[on], index, ctx.today)]
        rechecked = rechecked or bool(due)
        for on in queue + due:
            op, inner = form_request(form, regnum, on)
            try:
                resp = http.soap(URL, op, inner, getter=getter, sink=sink, name=f"f{form}_{on}.xml")
            except http.FetchError as exc:
                # Запись не трогается: дата остаётся в очереди (или с прежней сверкой) до следующего прохода.
                recheck_clean = recheck_clean and on not in due
                res.degrade(f"форма {form} на {on}: {exc}", retry=http.retryable(exc))
                continue
            if not is_service_document(resp.body):
                # Заглушка вместо формы — как отказ сети: версией она не становится, запись не трогается.
                recheck_clean = recheck_clean and on not in due
                res.degrade(f"форма {form} на {on}: ответ — не документ сервиса (заглушка с кодом {resp.status})",
                            retry=True)
                continue
            entry = known[on]
            versions = entry.setdefault("versions", [])
            attempt = versions.pop() if versions and _unparsed(versions[-1]) else None   # прежняя неудачная попытка
            held = versions[-1] if versions else None                                    # последний винтаж в рядах
            if held is not None and held["sha256"] == resp.sha256:
                entry["checked"] = resp.fetched_at            # тот же ответ, он уже в рядах
                continue
            version = attempt if attempt is not None and attempt["sha256"] == resp.sha256 else {
                "sha256": resp.sha256, "fetched_at": resp.fetched_at}
            versions.append(version)
            try:
                res.series += ingest(ctx.store, form, on, resp.body, fetched_at=resp.fetched_at,
                                     source=f"cbr:0409{form}:{on}:sha256:{resp.sha256[:12]}")
            except (FormError, ValueError) as exc:
                # Попытка помечена неразобранной: тот же ответ в следующем такте разбирается снова, а не
                # считается известным, — ошибка не исчезает из статуса, пока форма не дошла до рядов.
                version["parsed"] = False
                recheck_clean = recheck_clean and on not in due
                res.degrade(f"форма {form} на {on}: разбор — {exc}", retry=False)
                continue
            version.pop("parsed", None)
            if held is not None:
                res.data.setdefault("revised", []).append(f"{form}:{on}")     # новый винтаж лёг рядом с прежним
            entry["checked"] = resp.fetched_at
    if rechecked and recheck_clean:
        index["last_recheck"] = ctx.today.isoformat()
    ctx.store.write_state(INDEX_FILE, index)
    if failures >= len(FORMS):
        res.status, res.detail = FAILED, "ЦБ не отдал список дат ни по одной форме"
    return res


def expected_form102_date(store: Store, month: str, *, default_days=(24, 26)) -> tuple[str, str]:
    """(самая ранняя, поздняя) ожидаемая дата выкладки формы 102 за месяц — по истории first_seen."""
    index = load_index(store)
    days = []
    for on, entry in (index.get("forms", {}).get("102") or {}).items():
        seen = entry.get("first_seen_utc")
        if seen:
            days.append(datetime.fromisoformat(seen).astimezone(timezone.utc).day)
    lo, hi = (min(days), max(days)) if days else tuple(default_days)
    nxt = periods.month_start(periods.shift_month(month, 1))
    return (nxt.replace(day=min(lo, 28)).isoformat(), nxt.replace(day=min(hi, 28)).isoformat())
