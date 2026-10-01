"""Мутационный набор ядра (М§18): одна ошибка в коде — её обязан уронить
аналитический или инвариантный тест, не регрессия.

Мутация — правка ИСХОДНОГО КОДА копии `model/` (подменой входа можно проверить только,
что параметр читается, а не что он правильно применяется). Проверки ниже — тождества
или закрытые формулы, выведенные из книги и фактов независимо от ядра; регрессии
чисел книги и сверки с контрольной моделью среди них нет.

Механика (перенос 850oa `tests/mutations.py`): каталог `model/` копируется во
временный каталог, в копии правится одна строка, проверки запускаются подпроцессом
с копией первой в `sys.path` (`python -m tests.mutations --check`). Если строку-цель
переписали, мутацию перенацеливают, а не удаляют.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    id: str
    title: str
    path: str                              # путь от корня репозитория
    find: str
    replace: str


MUTATIONS: list[Mutation] = [
    Mutation("M01", "проценты за 0,25 года вместо дней квартала (act/365)", "model/cell.py",
             "dt = d / DAYS_IN_YEAR", "dt = QUARTER_YEARS"),
    Mutation("M02", "корень (1 + r)^0,25 − 1 вместо простого начисления в доходе активов", "model/cell.py",
             "income = sum(rate[b] * avg[b] for b in roles.assets) * dt",
             "income = sum(((1 + rate[b]) ** QUARTER_YEARS - 1) * avg[b] for b in roles.assets)"),
    Mutation("M03", "κ-добавка не относительно мира N", "model/credit.py",
             "out[q] = kappa * max(0.0, real_w[j] - real_n[j])", "out[q] = kappa * max(0.0, real_w[j])"),
    Mutation("M04", "OCI вне совокупного дохода", "model/cell.py",
             "ci = ni_sh - cpn * (1 - tau_s) + oci + om", "ci = ni_sh - cpn * (1 - tau_s) + om"),
    Mutation("M05", "купон AT1 без налогового эффекта", "model/cell.py",
             "ci = ni_sh - cpn * (1 - tau_s) + oci + om", "ci = ni_sh - cpn + oci + om"),
    Mutation("M06", "дивиденд на размещённые акции (с собственными)", "model/dividends.py",
             "ratio = policy.n_out / policy.n_iss", "ratio = 1.0"),
    Mutation("M07", "мост с дисконтом за управление", "model/grid.py",
             "return (core * (1 - governance) + pending + bridge_amount) * THOUSAND / shares_out",
             "return (core + pending + bridge_amount) * (1 - governance) * THOUSAND / shares_out"),
    Mutation("M08", "политика 50 % в терминале вместо капиталонейтральной выплаты", "model/valuation.py",
             "tv_ddm = x_t + (roe_t - g) * bv_star / (k_t - g)", "tv_ddm = x_t + 0.5 * roe_t * bv_star / (k_t - g)"),
    Mutation("M09", "предел сдвига A-P2u не на каждое наблюдение", "model/grid.py",
             "current = cap_shift(current, posterior, limit)", "current = posterior"),
    Mutation("M10", "φ не ноль в мире H (путь ставки клетки)", "model/cell.py",
             's -= phi * (ref - self.wh.ref(spec["ref"])[q])', "s -= phi * ref"),
    Mutation("M11", "φ не ноль в мире H (стационарный ЧПМ)", "model/nii.py",
             'y -= phi * (ref - rh[spec["ref"]])', "y -= phi * ref"),
    Mutation("M12", "мост CoR не применён к путям режимов", "model/credit.py",
             "return bridge.to_engine_cor(value)", "return value"),
    Mutation("M13", "мост CoR применён к путям режимов дважды", "model/credit.py",
             "return bridge.to_engine_cor(value)", "return bridge.to_engine_cor(bridge.to_engine_cor(value))"),
    Mutation("M14", "вычеты капитала без роста с RWA", "model/capital.py",
             "ded20 = c.ded20_0 * rwa / c.rwa0", "ded20 = c.ded20_0"),
    Mutation("M15", "прибыль года вне K11 без календаря аудитов", "model/cell.py",
             "back = after_audit_start(h, p.cap.audit_cutoffs)", "back = h - 1"),
    Mutation("M16", "дивиденд вычтен из регуляторного капитала в квартал ГОСА (без DPreg)", "model/cell.py",
             "dpreg = dpreg_at(reg, q)", "dpreg = 0.0"),
    Mutation("M17", "RI квартала оценки без доли (1 − e)", "model/valuation.py",
             "ri[q0] = (1 - e) * ci[q0] - k0 * bv_v", "ri[q0] = ci[q0] - k0 * bv_v"),
    Mutation("M18", "капитал на дату оценки без дохода прошедшей доли квартала", "model/valuation.py",
             "bv_v = bv[q0 - 1] + e * ci[q0]", "bv_v = bv[q0 - 1]"),
    Mutation("M19", "порядок случайных чисел полосы: по осям (X5), а не по прогонам (850oa)", "model/uncertainty.py",
             "return [[tri_s((perms[j][i] + rng.random()) / n) for j in range(n_axes)] for i in range(n)]",
             "return [list(r) for r in zip(*[[tri_s((perms[j][i] + rng.random()) / n) for i in range(n)] "
             "for j in range(n_axes)])]"),
    Mutation("M20", "тип квантилей не 7", "model/uncertainty.py", "h = (n - 1) * q", "h = n * q - 0.5"),
    Mutation("M21", "конец квартала — не конец дня (правило только для якоря)", "model/timeline.py",
             "if d == self.end(q):", "if d == self.end(0):"),
    Mutation("M22", "σ0 с первого прогнозного квартала, без nii.sigma0_from", "model/nii.py",
             "row.append(t.value(y, h) + (shift if y >= from_year else 0.0))", "row.append(t.value(y, h) + shift)"),
    Mutation("M23", "ε без линейного ввода за ramp_years", "model/dividends.py",
             "return self.epsilon * min(1.0, (year - self.excess_from + 1) / self.ramp_years)",
             "return self.epsilon"),
    Mutation("M24", "«прочее» без квартального профиля", "model/cell.py",
             "misc = p.misc_q[(year, h)] * idx", "misc = p.misc_q[(year, 1)] * idx"),
    Mutation("M25", "ожидание режима A-P2u — только мир N, не слой «свой взгляд»", "model/grid.py",
             'world_w = {w: float(book.get(f"joint.world_prob.{w}")) for w in book.get("worlds.ids")}',
             "world_w = {BASE_WORLD: 1.0}"),
    # --- волна W2 (аудит после W1; М§18, строка «Мутации»)
    Mutation("M26", "вычеты Ded11 без роста с RWA", "model/capital.py",
             "ded11 = c.ded11_0 * rwa / c.rwa0", "ded11 = c.ded11_0"),
    Mutation("M27", "терминал без вычета дохода на избыток", "model/valuation.py",
             "ni_t1 = (pbt_last_year * (1 + g) - y_x) * (1 - tau_eff) * (1 - nci_share)",
             "ni_t1 = pbt_last_year * (1 + g) * (1 - tau_eff) * (1 - nci_share)"),
    Mutation("M28", "доход на избыток по одной ставке ликвидности (без бумаг)", "model/cell.py",
             'y_bal = p.sec_share * last["_rate"][SECURITIES] + (1 - p.sec_share) * last["_rate"][LIQUIDITY]',
             'y_bal = last["_rate"][LIQUIDITY]'),
    Mutation("M29", "избыток сверх запаса ликвидности — не по ставке опта", "model/valuation.py",
             "return y_balancing * within + c_wholesale * (x_t - within)", "return y_balancing * x_t"),
    Mutation("M30", "взносы АСВ выключены", "model/cell.py", "dia = p.dia * fr_avg * dt", "dia = 0.0"),
    Mutation("M31", "κ-добавка без лага", "model/credit.py", "j = q - lag", "j = q"),
    Mutation("M32", "κ-добавка не в CoR", "model/cell.py",
             "self.cor = tuple(self.rg.cor_engine[q] + kap[q] + self.d_cor[q] for q in range(Q + 1))",
             "self.cor = tuple(self.rg.cor_engine[q] + self.d_cor[q] for q in range(Q + 1))"),
    Mutation("M33", "Δy первого квартала — от пути мира, а не от кривой якоря", "model/cell.py",
             "dy = tenor[q] - (tenor[q - 1] if q > 1 else p.y_tenor0)", "dy = tenor[q] - tenor[q - 1]"),
    Mutation("M34", "минимум ликвидности — к небалансирующим активам, а не ко всем", "model/cell.py",
             "la_min = p.liquid_min * nonbal_assets / (1 - p.liquid_min)", "la_min = p.liquid_min * nonbal_assets"),
    Mutation("M35", "FVC — на весь CoR, без опоры (двойной счёт уровня, уже лежащего в «прочем»)", "model/credit.py",
             "return factor * (cor_corporate - cor_ref) * fv_avg * dt", "return factor * cor_corporate * fv_avg * dt"),
    Mutation("M36", "опора FVC — режим клетки, а не credit.fv_loans_ref", "model/cell.py",
             "self.cor_ref = p.fv_ref_cor", "self.cor_ref = self.rg.cor_engine"),
    Mutation("M37", "«прочее» года якоря — остатком года (без вычитания факта ось не работает)", "model/cell.py",
             "    if rest_qs:             # остатком года закрывается только непрофильный результат (М§0.3)\n",
             "    if rest_qs:\n        for h in (1, 2, 3, 4):\n            misc_q[(ay, h)] = (misc_tab[ay] - "
             "need_hist(\"misc\", fact_qs)) / len(rest_qs)\n"),
    Mutation("M38", "объявленный, ещё не вычтенный дивиденд — под дисконтом за управление", "model/grid.py",
             'pending = sum(r["amount"] for r in brows if r["pending"])', "pending = 0.0"),
    Mutation("M39", "перекат pos(v) — по началу дня", "model/timeline.py",
             "roll = QUARTER_YEARS * (((q0 - 1) + e) - timeline.pos(curve))",
             "roll = QUARTER_YEARS * ((timeline.q_of_date(v) - 1 + (v - timeline.start(timeline.q_of_date(v))).days "
             "/ timeline.d(timeline.q_of_date(v))) - timeline.pos(curve))"),
    Mutation("M40", "фильтр A-P2u читает все наблюдения, без окна", "model/grid.py",
             "return _observations(book, bridge, timeline)[-n:]", "return _observations(book, bridge, timeline)"),
    Mutation("M41", "наблюдение не позже якоря — без замороженных ожиданий (μ = 0)", "model/grid.py",
             "dev = value - (frozen[r] if frozen is not None else mu[x][r][q])",
             "dev = value - (0.0 if frozen is not None else mu[x][r][q])"),
    Mutation("M42", "отмена дивиденда кризисом записана как урезание капиталом", "model/dividends.py",
             'deferred_after=deferred + want[0], source="crisis_skip", cut=False,',
             'deferred_after=deferred + want[0], source="crisis_skip", cut=True,'),
    Mutation("M43", "σ0 пассивов — с тем же знаком, что у активов", "model/nii.py",
             'sigma = sigma0 if spec["side"] == "asset" else -sigma0_liab',
             'sigma = sigma0 if spec["side"] == "asset" else sigma0_liab'),
    Mutation("M44", "σ0 пассивов не входит в стационарный ЧПМ", "model/nii.py",
             'sl = {b: (sigma0_liab if books[b].get("lt_shift") else 0.0) for b in self.roles.liabilities}',
             "sl = {b: 0.0 for b in self.roles.liabilities}"),
    Mutation("M45", "доля сдвига σ0 на активах не читается (всё на активах)", "model/nii.py",
             "sigma0 = split * delta0 / lt_weight if split > 0 else 0.0", "sigma0 = delta0 / lt_weight"),
    Mutation("M46", "эффективный LT-спред без сжатия φ", "model/nii.py",
             's -= phi_assets * (rates[spec["ref"]] - self.ref_lt[ref_world][spec["ref"]])', "s -= 0.0"),
    Mutation("M47", "край отрезка поиска нейтрального значения в допуске не принимается", "model/nextreport.py",
             "if abs(fe) <= tol:                       # край с невязкой в допуске — корень (ноль — с любой стороны)",
             "if False:"),
    Mutation("M48", "допуск точечного гайденса — вдесятеро шире", "model/checks.py",
             "return (v - float(tol), v + float(tol))", "return (v - 10 * float(tol), v + 10 * float(tol))"),
    Mutation("M49", "сдвиг положения оси — без точки (не от книги)", "model/uncertainty.py",
             "return (price_high + price_low - 2 * point) / MEAN_SHIFT_DIVISOR",
             "return (price_high + price_low) / MEAN_SHIFT_DIVISOR"),
    Mutation("M50", "год шока кризиса — год якоря (сдвиг не прибавлен)", "model/cell.py",
             "shock_year=None if offset is None else ay + int(offset))", "shock_year=None if offset is None else ay)"),
    # --- волна W3 (второй аудит, LEAD-DECISIONS-4; М§18, строка «Мутации»)
    Mutation("M51", "угасание симметричное (множитель действует и при ROE_T < k_T)", "model/valuation.py",
             "roe_t = k_t + fade * max(excess, 0.0) + min(excess, 0.0)", "roe_t = k_t + fade * excess"),
    Mutation("M52", "опора FVC сдвигается вместе с осью уровня CoR", "model/cell.py",
             "ref_book = book if book.base is None else book.base", "ref_book = book"),
    Mutation("M53", "пол вероятности режима не применён", "model/grid.py",
             "current = floor_shift(prior, current, floor)", "current = dict(current)"),
    Mutation("M54", "остаток сжатия φ не доходит до стоимости пассивов", "model/cell.py",
             "s += phi_l * self.x_w[q]", "s += 0.0"),
    Mutation("M55", "порог ликвидности терминала — HLA вместо HLA / (1 − доля)", "model/valuation.py",
             "limit = headroom / (1 - min_share)", "limit = headroom"),
    Mutation("M56", "добавка пассивов φ_L без отношения весов A / L", "model/nii.py",
             "phi_l = (1 - p_split) * phi * a_phi / l_phi if p_split < 1 else 0.0",
             "phi_l = (1 - p_split) * phi if p_split < 1 else 0.0"),
    Mutation("M57", "доля сжатия φ на активах не читается (всё на активах)", "model/nii.py",
             "phi_a = p_split * phi", "phi_a = phi"),
    Mutation("M58", "год с отчётными кварталами — средним мостом, без упр. факта", "model/grid.py",
             "    if all(q >= 1 for q in qs):\n        to_mgmt =", "    if True:\n        to_mgmt ="),
    Mutation("M59", "сторож заголовка: дивиденд без записи реестра не входит в сумму", "model/grid.py",
             'total += sum(prob[c.key] * float(c.quarters["div"][q] or 0.0) for c in cells)', "total += 0.0"),
    Mutation("M60", "недостаток капитала терминала не гасит добор опта", "model/valuation.py",
             "repaid = min(-x_t, wholesale_extra)", "repaid = 0.0"),
    Mutation("M61", "оценщик сдвига медианы — среднее по всей выборке, без окна рангов", "model/uncertainty.py",
             "chosen = [i for i in range(n) if lo <= (rk[i] - 1) / (n - 1) <= hi]", "chosen = list(range(n))"),
    Mutation("M62", "нейтральное значение «прилипает» к строке таблицы при смене знака", "model/nextreport.py",
             "    if cross is None:\n        near =", "    if True:\n        near ="),
    Mutation("M63", "квартал открытого нау-каста — прогнозом, а не отклонением от своего ожидания", "model/payload.py",
             'ni_q = x.E(x.p_mix, "ni_sh", qs) + deviation', 'ni_q = t1.get("forecast") if t1.get("forecast") is not None else e["ni"]'),
    # --- волна перед первым push (решения ведущего P4; М§18, строка «Мутации»)
    Mutation("M64", "уточнение корня обратного расчёта не делает шаг секущей (остаётся решение подвыборки)",
             "model/reverse.py", "            break\n        x = estimate", "            break\n        break"),
    Mutation("M65", "«в диапазоне книги» — о приближении, а не о корне", "model/reverse.py",
             "    return low <= estimate <= high", "    return True"),
    Mutation("M66", "нейтральное значение точки «прилипает» к строке таблицы при смене знака", "model/nextreport.py",
             "        root, _ = _refine(_Gap(pg), a, fa, b, fb, c, fc, POINT_STEPS, tol, value_tol)",
             "        root, _ = bisect_point(pg, a, b, tol=tol)"),
    Mutation("M67", "гейт пола спреда не смотрит на концы осей долей", "model/checks.py",
             "    corners = share_axis_ends(book)", "    corners = []"),
]


def build_mutant(mutation: Mutation, target: Path) -> Path:
    """Копия `model/` в `target` с одной правкой."""
    dest = target / "model"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ROOT / "model", dest, ignore=shutil.ignore_patterns("__pycache__"))
    path = target / mutation.path
    source = path.read_text(encoding="utf-8")
    if mutation.find not in source:
        raise AssertionError(f"{mutation.id}: строка для мутации не найдена в {mutation.path} — "
                             "мутацию надо перенацелить, а не удалить")
    path.write_text(source.replace(mutation.find, mutation.replace, 1), encoding="utf-8", newline="\n")
    return target


def run_checks_in(target: Path | None, timeout: float = 300.0) -> dict[str, str]:
    """Проверки подпроцессом; `target` — каталог мутанта (None — подлинное ядро). Имя → «ok» | причина."""
    env = dict(os.environ)
    paths = ([str(target)] if target is not None else []) + [str(ROOT)]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    out = subprocess.run([sys.executable, "-B", "-m", "tests.mutations", "--check"], cwd=str(target or ROOT),
                         env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"_crash": (out.stderr or out.stdout)[-800:]}


# ------------------------------------------------------------------ проверки (в подпроцессе)


def _checks() -> dict[str, Callable[[], None]]:
    import copy
    import math
    import random

    import model  # noqa: F401  — копия мутанта первой в sys.path
    from model import uncertainty as U
    from model.book import book_from_dict
    from model.cell import run_cell
    from model.checks import check_invariants
    from model.credit import kappa_addon
    from model.grid import DividendRecord, LiveInputs, cap_shift, make_context, run_grid, sensitivity_overrides
    from model.paths import Trajectory
    from model.worlds import BASE_WORLD
    from tests.support_core import fixture_book, fixture_facts

    book, facts = fixture_book(), fixture_facts()
    base = run_grid(book, facts)
    tl = base.ctx.timeline

    def close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-9) -> bool:
        return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))

    def invariants() -> None:
        fired = [f.name for f in check_invariants(base) if f.fired]
        assert not fired, fired

    def llp_shift_exact() -> None:
        x = 0.001
        shifted = run_grid(book.with_overrides(sensitivity_overrides(book, "cor", x)), facts)
        a, b = base.cell("M", "downturn", "strict"), shifted.cell("M", "downturn", "strict")
        for q in range(1, tl.Q + 1):
            expect = x * a.quarters["loans_ac_avg"][q] * tl.d(q) / 365
            assert close(b.quarters["llp"][q] - a.quarters["llp"][q], expect), q

    def interest_legs_simple() -> None:
        c = base.cell("H", "norm", "schedule")
        roles = base.ctx.prep.roles
        for q in range(1, tl.Q + 1):
            inc = sum(c.books[b]["rate"][q] * c.books[b]["avg"][q] for b in roles.assets) * tl.d(q) / 365
            exp = sum(c.books[b]["rate"][q] * c.books[b]["avg"][q] for b in roles.liabilities) * tl.d(q) / 365
            assert close(inc, c.quarters["int_income"][q]) and close(exp, c.quarters["int_expense"][q]), q
            nii = inc - exp - c.quarters["dia"][q] + c.quarters["overlay"][q]
            assert close(nii, c.quarters["nii"][q]), q

    def kappa_zero_in_n() -> None:
        p = base.ctx.prep
        n = base.ctx.worlds[BASE_WORLD].real_key
        assert all(v == 0.0 for v in kappa_addon(p.kappa, p.lag, n, n, tl.Q))

    def dps_is_pool_over_issued() -> None:
        n_iss = facts.need("shares", "issued_total")
        n_out = facts.need("shares", "outstanding_total")
        payout = Trajectory(book.get("dividends.policy.payout"))
        for c in base.cells:
            for d in c.decisions:
                if d.source == "model" and not d.cut and d.catch == 0 and d.excess == 0:
                    assert close(d.dps, payout.year_value(d.year) * d.base * 1000 / n_iss, rel=1e-9), d.year
                    assert close(d.div, d.dps * n_out / 1000), d.year

    def exdate_jump_with_governance() -> None:
        comp = [{"id": "t", "name": "т", "value": 0.05, "sign": 1, "basis": "проверка"}]
        b2 = book.with_overrides({"valuation.governance.discount": 0.05,
                                  "valuation.governance.components": comp})
        agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
        v = tl.end(agm_q) + timedelta(days=20)
        prices = {str(t): float(x) for t, x in book.get("meta.market_price").items()}
        out = []
        for ex in (v, v + timedelta(days=1)):
            rec = DividendRecord(year=tl.anchor_year, dps=40.0, status="declared", record_date=ex,
                                 last_buy_date=ex - timedelta(days=1), ex_date=ex, pay_date=ex + timedelta(days=14),
                                 sources=("проверка",))
            live = LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=(rec,))
            out.append(run_grid(b2, facts, live).point)
        assert close(out[0] - out[1], -40.0, abs_=1e-6), out

    def h_world_independent_of_phi() -> None:
        ref = str(book.get("nii.transmission.reference_world"))
        other = run_grid(book.with_overrides({"nii.transmission.target": 0.12}), facts)
        assert not close(other.ctx.transmission.phi, base.ctx.transmission.phi)
        a, b = base.cell(ref, "norm", "schedule"), other.cell(ref, "norm", "schedule")
        for q in range(1, tl.Q + 1):
            assert close(a.quarters["nii"][q], b.quarters["nii"][q], rel=1e-12), q

    def cor_path_bridged_once() -> None:
        b_cor = facts.need("bridge_mgmt_ifrs", "cor.value")
        assert facts.plain("bridge_mgmt_ifrs", "cor.method") == "additive"
        assert book.get("regimes.cor_basis") == "mgmt"
        for r in book.get("regimes.ids"):
            t = Trajectory(book.get(f"regimes.{r}.cor"))
            c = base.cell(BASE_WORLD, r, "schedule")
            for q in range(1, tl.Q + 1):
                y, n = tl.year(q), tl.h(q)
                assert close(c.quarters["cor"][q], t.value(y, n) + b_cor, abs_=1e-12), (r, q)

    def deductions_scale_with_rwa() -> None:
        """Ded20 и Ded11 = вычеты якоря × RWA_q / RWA_0 (М§4.11)."""
        ded20 = float(book.get("capital.n20.deductions_anchor"))
        ded11 = float(book.get("capital.n11.deductions_anchor"))
        c = base.cell("H", "norm", "upper")
        rwa0 = c.quarters["rwa"][0]
        assert c.quarters["rwa"][tl.Q] > 1.5 * rwa0
        for q in range(0, tl.Q + 1):
            assert close(c.quarters["ded20"][q], ded20 * c.quarters["rwa"][q] / rwa0), q
            assert close(c.quarters["ded11"][q], ded11 * c.quarters["rwa"][q] / rwa0), q

    def unaudited_by_calendar() -> None:
        cut = [int(x) for x in book.get("capital.n11.audit_cutoffs")]
        c = base.cell("H", "norm", "schedule")
        hist = base.ctx.prep.hist["ni_sh"]
        ni = {q: (c.quarters["ni_sh"][q] if q >= 1 else hist[q]) for q in range(-7, tl.Q + 1)
              if q >= 1 or q in hist}
        for q in range(1, tl.Q + 1):
            total, j = 0.0, q
            while True:
                total += ni[j]
                j -= 1
                if tl.h(j) in cut:
                    break
            assert close(c.quarters["e_unaudited"][q], total), q

    def agm_quarter_neutral_for_capital() -> None:
        agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
        prices = {str(t): float(x) for t, x in book.get("meta.market_price").items()}
        v = base.ctx.live.valuation_date
        k20 = []
        for dps in (30.0, 60.0):
            rec = DividendRecord(year=tl.anchor_year, dps=dps, status="declared", record_date=None,
                                 last_buy_date=None, ex_date=None, pay_date=None, sources=("проверка",))
            live = LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=(rec,))
            k20.append(run_cell(make_context(book, facts, live), "H", "norm", "schedule").quarters["k20"][agm_q])
        assert close(k20[0], k20[1], rel=1e-12), k20

    def apu_cap_per_observation() -> None:
        from model.book import book_from_dict
        import copy
        data = copy.deepcopy(dict(book.source))
        limit = float(book.get("joint.regime_update.max_shift_pp"))
        prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
        obs = [{"period": tl.period(q), "cor": 0.045, "nim": None, "se_cor": 0.0, "se_nim": None,
                "basis": "mgmt"} for q in (1, 2)]
        data["joint"]["regime_update"]["observations"] = obs[:1]
        one = make_context(book_from_dict(data, facts=facts), facts).posterior
        assert max(abs(one[r] - prior[r]) for r in prior) <= limit + 1e-12
        data["joint"]["regime_update"]["observations"] = obs
        two = make_context(book_from_dict(data, facts=facts), facts).posterior
        assert max(abs(two[r] - one[r]) for r in prior) <= limit + 1e-12
        assert max(abs(two[r] - prior[r]) for r in prior) > limit + 1e-9

    prices = {str(t): float(x) for t, x in book.get("meta.market_price").items()}

    def live_at(v, register=()) -> LiveInputs:
        return LiveInputs(valuation_date=v, prices=prices, price_dates={t: v for t in prices}, register=register)

    def ddm_ri_mid_quarter() -> None:
        """Дата оценки внутри квартала (0 < e < 1): DDM = RI и тождества держатся."""
        run = run_grid(book, facts, live_at(tl.start(2) + timedelta(days=40)))
        assert 0 < run.ctx.clock.elapsed < 1
        fired = [f.name for f in check_invariants(run) if f.fired]
        assert not fired, fired

    def lhs_order() -> None:
        """LHS в порядке 850oa (М§10): перестановки всех осей, затем по прогонам i и осям j."""
        n, m, seed = 6, 3, 11
        rng = random.Random(seed)
        perms = []
        for _ in range(m):
            p_ = list(range(n))
            rng.shuffle(p_)
            perms.append(p_)

        def tri(u: float) -> float:
            return math.sqrt(2 * u) - 1 if u < 0.5 else 1 - math.sqrt(2 * (1 - u))

        want = [[tri((perms[j][i] + rng.random()) / n) for j in range(m)] for i in range(n)]
        assert U.draw_points(n, m, seed) == want

    def quantile_type_7() -> None:
        xs = [1.0, 2.0, 4.0, 8.0, 16.0]
        for q, want in ((0.1, 1.4), (0.25, 2.0), (0.5, 4.0), (0.9, 12.8)):
            assert close(U.quantile(xs, q), want), (q, U.quantile(xs, q))

    def quarter_end_continuity() -> None:
        """Конец квартала ГОСА при записи реестра: E − 1, E, E + 1 — перекат, не DPS (М§0.2, §8.1)."""
        agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
        end = tl.end(agm_q)
        ex = end + timedelta(days=20)
        rec = DividendRecord(year=tl.anchor_year, dps=40.0, status="declared", record_date=ex,
                             last_buy_date=ex - timedelta(days=1), ex_date=ex, pay_date=ex + timedelta(days=14),
                             sources=("проверка",))
        pts = [run_grid(book, facts, live_at(end + timedelta(days=d), (rec,))).point for d in (-1, 0, 1)]
        assert abs(pts[1] - pts[0]) < 1e-3 * pts[1] and abs(pts[2] - pts[1]) < 1e-3 * pts[1], pts

    def sigma0_from_year() -> None:
        """s_b(q) = путь + σ0 × [lt_shift] × [Y(q) ≥ nii.sigma0_from] (М§4.4)."""
        tr = base.ctx.transmission
        y0 = int(book.get("nii.sigma0_from"))
        assert tr.sigma0 != 0 and tr.sigma0_liab != 0
        for b, spec in book.get("nii.books").items():
            t = Trajectory(spec["spread"])
            sigma = tr.sigma0 if spec["side"] == "asset" else -tr.sigma0_liab       # у пассивов знак обратный
            for q in range(tl.Q + 1):
                y = tl.year(q)
                want = t.value(y, tl.h(q)) + (sigma if spec.get("lt_shift") and y >= y0 else 0.0)
                assert close(base.ctx.prep.spreads[b][q], want, abs_=1e-15), (b, q)

    def epsilon_ramp() -> None:
        """ε_Y = ε × min(1, (Y − from + 1) / ramp_years) на избытке над требованием (М§5.3 п. 4)."""
        eps = float(book.get("dividends.excess.epsilon"))
        start = int(book.get("dividends.excess.from_profit_year"))
        ramp = int(book.get("dividends.excess.ramp_years"))
        assert ramp > 1
        seen = 0
        for c in base.cells:
            for d in c.decisions:
                if d.source != "model":
                    continue
                e_y = eps * min(1.0, (d.year - start + 1) / ramp) if d.year >= start else 0.0
                want = e_y * max(0.0, d.headroom[d.step] - d.base_div - d.catch)
                assert close(d.excess, want, abs_=1e-9), (c.label, d.year)
                seen += d.year == start and want > 0
        assert seen

    def misc_quarter_profile() -> None:
        """«Прочее» квартала = годовая сумма × индекс цен × доля квартала (М§4.7)."""
        shares = {h: float(book.get(f"other.misc_quarter_shares.{h}")) for h in (1, 2, 3, 4)}
        assert len(set(shares.values())) > 1
        t = Trajectory(book.get("other.misc_net_real"))
        c = base.cell("H", "norm", "schedule")
        idx = base.ctx.worlds["H"].price_index
        for q in range(1, tl.Q + 1):                       # и в году якоря: остатком год не закрывается (М§0.3)
            y, h = tl.year(q), tl.h(q)
            annual = sum(t.value(y, k) for k in (1, 2, 3, 4)) / 4
            assert close(c.quarters["misc"][q], annual * shares[h] * idx[q]), q

    def apu_layer_average_is_observation() -> None:
        """В квартале наблюдения (se 0) среднее клеток режима слоя «свой взгляд» = наблюдению (М§12)."""
        data = copy.deepcopy(dict(book.source))
        data["joint"]["regime_update"]["observations"] = [
            {"period": tl.period(1), "cor": None, "nim": 0.058, "se_cor": None, "se_nim": 0.0, "basis": "mgmt"}]
        b2 = book_from_dict(data, facts=facts)
        run = run_grid(b2, facts)
        eng = run.ctx.bridge.to_engine_nim(0.058)
        pw = {w: float(b2.get(f"joint.world_prob.{w}")) for w in b2.get("worlds.ids")}
        table = b2.get("joint.reg_prob_given_regime")
        for r in b2.get("regimes.ids"):
            avg = sum(pw[c.world] * float(table[r][c.scenario]) * c.quarters["nim"][1]
                      for c in run.cells if c.regime == r)
            assert close(avg, eng, abs_=1e-12), (r, avg, eng)

    # ---------------------------------------------------------------- волна W2: закрытые формулы из книги и фактов

    def terminal_closed_form() -> None:
        """NI_T+1 = [PBT_L (1 + g_T) − Y_X] (1 − τ_eff)(1 − nci); Y_X — смесь бумаг и ликвидности, пока минимум
        ликвидности не связывает (порог H* = HLA / (1 − m)), дальше — ставка опта; недостаток капитала сначала
        гасит добор опта (М§7). Минимум поднят так, что на конце сетки он связывает."""
        Q, L = tl.Q, tl.last_year
        lm = 0.30
        run = run_grid(book.with_overrides({"volumes.liquid_min_share": lm}), facts)
        sec = float(book.get("volumes.securities_share_of_liquid"))
        wf = float(book.get("volumes.wholesale_to_funds"))
        nci = float(book.get("pnl.nci_share"))
        cpn = facts.need("capital", "at1.coupon_annual")
        above = repaid = 0
        for c in list(base.cells) + list(run.cells):
            m = lm if c in run.cells else float(book.get("volumes.liquid_min_share"))
            la = c.quarters["securities"][Q] + c.quarters["liquidity"][Q]
            h_star = max(0.0, la - m * c.quarters["assets"][Q]) / (1 - m)
            wt = max(0.0, c.quarters["wholesale"][Q] - wf * c.quarters["funds"][Q])
            y_bal = sec * c.books["securities"]["rate"][Q] + (1 - sec) * c.books["liquidity"]["rate"][Q]
            c_wh = c.books["wholesale"]["rate"][Q]
            x = c.x_t
            if x > 0:
                y_x = y_bal * min(x, h_star) + c_wh * max(0.0, x - h_star)
                above += x > h_star
            else:
                y_x = -(c_wh * min(-x, wt) + y_bal * max(0.0, -x - wt))
                repaid += wt > 1e-6
            pbt_l = sum(c.quarters["pbt"][q] for q in range(1, Q + 1) if tl.year(q) == L)
            ni = (pbt_l * (1 + c.g_t) - y_x) * (1 - c.quarters["tau_eff"][Q]) * (1 - nci)
            ci = ni - cpn * (1 - c.quarters["tau_stat"][Q])
            assert close(c.roe_t_raw * c.bv_star, ci, rel=1e-10), c.label
        assert above > 0 and repaid > 0                     # есть избыток сверх порога и недостаток с добором опта
        from model.valuation import excess_income           # сверх порога избыток гасит опт
        assert close(excess_income(100.0, 0.08, 0.15, 40.0), 0.08 * 40.0 + 0.15 * 60.0)
        assert close(excess_income(100.0, 0.08, 0.15, 40.0, 0.2), 0.08 * 50.0 + 0.15 * 50.0)     # H* = HLA / (1 − m)
        assert close(excess_income(-100.0, 0.08, 0.15, 0.0), -8.0)
        assert close(excess_income(-100.0, 0.08, 0.15, 0.0, 0.2, 30.0), -(0.15 * 30.0 + 0.08 * 70.0))

    def dia_leg_from_book() -> None:
        """Взносы АСВ = nii.dia_rate × средние средства ФЛ × d/365 — ставка из книги, не из ряда клетки."""
        rate = float(book.get("nii.dia_rate"))
        assert rate > 0
        c = base.cell("H", "norm", "schedule")
        for q in range(1, tl.Q + 1):
            avg = (c.quarters["funds_retail"][q - 1] + c.quarters["funds_retail"][q]) / 2
            assert close(c.quarters["dia"][q], rate * avg * tl.d(q) / 365), q

    def kappa_lag_closed_form() -> None:
        """κ × max(0, rr_W,(q−L) − rr_N,(q−L)) на синтетических путях с разностью в первые L кварталов."""
        rr_w = [0.0, 0.09, 0.08, 0.07, 0.06, 0.05, 0.02]
        rr_n = [0.0, 0.04, 0.05, 0.06, 0.07, 0.03, 0.03]
        want = (0.0, 0.0, 0.0, 0.5 * 0.05, 0.5 * 0.03, 0.5 * 0.01, 0.0)
        got = kappa_addon(0.5, 2, rr_w, rr_n, 6)
        assert all(close(a, b, abs_=1e-15) for a, b in zip(got, want)), got

    def cor_includes_kappa() -> None:
        """CoR клетки = путь режима через мост + κ-добавка мира (без наблюдений δ = 0)."""
        p = base.ctx.prep
        seen = 0.0
        for w in book.get("worlds.ids"):
            kap = kappa_addon(p.kappa, p.lag, base.ctx.worlds[w].real_key, base.ctx.worlds[BASE_WORLD].real_key, tl.Q)
            c = base.cell(w, "norm", "schedule")
            for q in range(1, tl.Q + 1):
                assert close(c.quarters["cor"][q], p.regimes["norm"].cor_engine[q] + kap[q], abs_=1e-12), (w, q)
            seen = max(seen, max(kap))
        assert seen > 0

    def first_dy_from_anchor_curve() -> None:
        """Фонд FVOCI первого квартала: Δy₁ = узел пути мира − узел кривой якоря (факт), не разность пути."""
        p = base.ctx.prep
        node = facts.need("capital", f"ofz_curve_anchor.{p.fvoci_tenor.split('_')[1].rstrip('y')}")
        r0 = facts.need("capital", "fvoci_reserve")
        for w in book.get("worlds.ids"):
            c = base.cell(w, "norm", "schedule")
            dy = base.ctx.worlds[w].ofz[p.fvoci_tenor][1] - node
            avg = p.fvoci_share * c.books["securities"]["avg"][1]
            want = r0 * (1 - 0.25 / p.fvoci_maturity) - p.fvoci_duration * dy * avg * (1 - c.quarters["tau_stat"][1])
            assert close(c.quarters["reserve"][1], want, rel=1e-10), w
            assert abs(dy - (base.ctx.worlds[w].ofz[p.fvoci_tenor][1] - base.ctx.worlds[w].ofz[p.fvoci_tenor][0])) > 1e-6

    def la_minimum_binds() -> None:
        """Где минимум связывает, LA = liquid_min_share × активы, а недостающее добрано оптом (М§4.10)."""
        lm = 0.30
        run = run_grid(book.with_overrides({"volumes.liquid_min_share": lm}), facts)
        c = run.cell("M", "soft", "schedule")
        extra = [q for q in range(1, tl.Q + 1) if c.quarters["wholesale_extra"][q] > 0]
        assert extra
        for q in extra:
            la = c.quarters["securities"][q] + c.quarters["liquidity"][q]
            assert close(la, lm * c.quarters["assets"][q], rel=1e-12), q

    def fvc_reference_regime() -> None:
        """FVC = factor × (CoR_corporate − CoR_ref) × Ē^СС × d/365; опора — путь режима credit.fv_loans_ref
        через мост: в клетке режима-опоры мира N — ноль, в кризисе — расход (М§4.6)."""
        from model.credit import regime_cor_engine
        ref = str(book.get("credit.fv_loans_ref"))
        factor = float(book.get("credit.fv_loans_factor"))
        assert factor > 0
        cor_ref = regime_cor_engine(book, base.ctx.bridge, ref, tl)
        assert all(v == 0.0 for v in base.cell(BASE_WORLD, ref, "schedule").quarters["fvc"][1:])
        for c in (base.cell("M", "crisis", "strict"), base.cell("H", "soft", "upper")):
            for q in range(1, tl.Q + 1):
                want = factor * (c.quarters["cor"][q] - cor_ref[q]) * c.quarters["loans_fv_avg"][q] * tl.d(q) / 365
                assert close(c.quarters["fvc"][q], want, rel=1e-9, abs_=1e-12), (c.label, q)
        shock = base.ctx.prep.regimes["crisis"].shock_year
        assert sum(base.cell(BASE_WORLD, "crisis", "schedule").quarters["fvc"][q] for q in tl.quarters_of_year(shock)) > 0

    def governed(g: float):
        comp = [{"id": "t", "name": "т", "value": g, "sign": 1, "basis": "проверка"}]
        return book.with_overrides({"valuation.governance.discount": g, "valuation.governance.components": comp})

    def pending_dividend_without_discount() -> None:
        """До E_qA объявленный дивиденд внутри V0, но вне дисконта: цена слоя = [(V0 − D)(1 − g) + D] / N,
        и на E_qA точка меняется на перекат, не на g × DPS (М§8.2)."""
        g, dps = 0.10, 40.0
        b2 = governed(g)
        agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
        end = tl.end(agm_q)
        ex = end + timedelta(days=20)
        rec = DividendRecord(year=tl.anchor_year, dps=dps, status="declared", record_date=ex,
                             last_buy_date=ex - timedelta(days=1), ex_date=ex, pay_date=ex + timedelta(days=14),
                             sources=("проверка",))
        before, on = (run_grid(b2, facts, live_at(end + timedelta(days=d), (rec,))) for d in (-1, 0))
        amount = dps * before.shares_out / 1000
        lay = before.layers["analytical"]
        assert close(lay.price, ((lay.v0 - amount) * (1 - g) + amount) * 1000 / before.shares_out, rel=1e-12)
        assert abs(on.point - before.point) < 0.2 * g * dps, (before.point, on.point)

    def roll_is_end_of_day() -> None:
        """На E_q и S_(q+1) точка одна и та же: перекат и τ на одной линейке (М§0.2)."""
        end = tl.end(2)
        a, b = (run_grid(book, facts, live_at(end + timedelta(days=d))).point for d in (0, 1))
        assert close(a, b, rel=1e-12), (a, b)

    def observation(period: str, cor: float, **extra) -> dict:
        return {"period": period, "cor": cor, "nim": None, "se_cor": 0.0, "se_nim": None, "basis": "mgmt", **extra}

    def with_observations(obs: list, **over):
        return make_context(book.with_overrides({"joint.regime_update.observations": obs, **over}), facts)

    def apu_window() -> None:
        """Фильтр читает только последние window_obs наблюдений; априорные — базовые (М§12)."""
        n = int(book.get("joint.regime_update.window_obs"))
        obs = [observation(tl.period(q), v) for q, v in zip(range(1, n + 2), (0.010, 0.019, 0.013, 0.022, 0.016, 0.03))]
        full, last = with_observations(obs).posterior, with_observations(obs[1:]).posterior
        assert all(close(full[r], last[r], abs_=1e-15) for r in full), (full, last)

    def apu_frozen_expectations() -> None:
        """Наблюдение квартала якоря судится против замороженных ожиданий записи (М§12)."""
        regimes = list(book.get("regimes.ids"))
        mu = dict(zip(regimes, (0.011, 0.013, 0.016, 0.0165)))
        value = 0.0125
        ctx = with_observations([observation(tl.anchor, value, mu_cor=mu)])
        sigma = float(book.get("joint.regime_update.observables.cor.sigma_pp"))
        prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in regimes}
        like = {r: math.exp(-0.5 * (value - mu[r]) ** 2 / sigma ** 2) for r in regimes}
        norm = sum(prior[r] * like[r] for r in regimes)
        want = cap_shift(prior, {r: prior[r] * like[r] / norm for r in regimes},
                         float(book.get("joint.regime_update.max_shift_pp")))
        assert all(close(ctx.posterior[r], want[r], abs_=1e-12) for r in regimes), (ctx.posterior, want)

    def crisis_skip_is_not_a_cut() -> None:
        """Отмена выплаты в год шока — crisis_skip, не «капитал урезал выплату» (М§5.3 п. 5); год шока —
        год якоря + shock_year_offset."""
        shock = tl.anchor_year + int(book.get("regimes.crisis.shock_year_offset"))
        seen = 0
        for c in base.cells:
            for d in c.decisions:
                if d.source == "crisis_skip":
                    assert d.cut is False and "dividend_cut" not in d.flags and d.year + 1 == shock, (c.label, d.year)
                    seen += 1
        assert seen == sum(1 for c in base.cells if c.regime == "crisis")

    def sigma0_split_closed_form() -> None:
        """σ0_A = split × Δ0 / Σ a_b (активы с lt_shift); Nss(H, 0) = цель при любой доле (М§4.5)."""
        from model.book import anchor_facts, book_roles
        from model.nii import nss_value
        books = book.get("nii.books")
        af = anchor_facts(facts, books)
        roles = book_roles(books)
        iea = sum(af.balances[b] for b in roles.assets)
        a_sum = sum(af.balances[b] / iea for b in roles.assets if books[b].get("lt_shift"))
        ref = str(book.get("nii.transmission.reference_world"))
        tr = base.ctx.transmission
        delta0 = tr.target_eng - nss_value(book, facts, ref, 0.0, 0.0, 0.0)
        split = float(book.get("nii.sigma0_split"))
        assert 0 < split < 1
        assert close(tr.sigma0, split * delta0 / a_sum, abs_=1e-15)
        other = make_context(book.with_overrides({"nii.sigma0_split": 1.0}), facts).transmission
        assert close(other.sigma0, delta0 / a_sum, abs_=1e-15) and other.sigma0_liab == 0.0

    def lt_spread_closed_form() -> None:
        """s_eff,b,W = s_b^LT + σ0_A·[lt_shift] − φ_A·[b ∈ Φ_A]·(ref_W^LT − ref_H^LT) (М§4.5)."""
        tr = base.ctx.transmission
        last = str(book.get("meta.last_period"))
        y, h = int(last[:4]), int(last[-1])
        ref_w = str(book.get("nii.transmission.reference_world"))
        for b, row in tr.lt_spread.items():
            spec = book.get(f"nii.books.{b}")
            key = lambda w: f"worlds.{w}.key_rate" if spec["ref"] == "key" else f"worlds.{w}.{spec['ref']}"  # noqa: E731
            for w, got in row.items():
                want = Trajectory(spec["spread"]).value(y, h) + (tr.sigma0 if spec["lt_shift"] else 0.0)
                if spec["phi"]:
                    want -= tr.phi_assets * (Trajectory(book.get(key(w))).value(y, h)
                                             - Trajectory(book.get(key(ref_w))).value(y, h))
                assert close(got, want, abs_=1e-15), (b, w)
        assert 0 < tr.phi_assets < tr.phi                                # доля сжатия на активах — внутри (0; 1)

    def neutral_edge_tolerance() -> None:
        """Край отрезка поиска с |невязкой| в допуске — корень (М§13)."""
        from model.nextreport import _Gap, _neutral
        root, gap, err = _neutral([0.058, 0.061, 0.064], [-9.0, -6.0, -3.0], _Gap(lambda x: -0.25), 0.002, 0.5)
        assert err is None and close(root, 0.066, abs_=1e-12) and gap == -0.25, (root, gap, err)

    def guidance_point_tolerance() -> None:
        from model.checks import guidance_band
        lo, hi = guidance_band({"v": 0.2, "kind": "point", "text": "20%"})
        assert close(lo, 0.195, abs_=1e-12) and close(hi, 0.205, abs_=1e-12), (lo, hi)

    def mean_shift_formula() -> None:
        assert close(U.mean_shift(400.0, 380.0, 410.0), (410.0 + 380.0 - 800.0) / 6)

    # ---------------------------------------------------------------- волна W3: закрытые формулы из книги и фактов

    def lt_refs(world: str) -> dict[str, float]:
        """Опорные ставки мира в last_period (стационар) — из траекторий книги."""
        last = str(book.get("meta.last_period"))
        y, h = int(last[:4]), int(last[-1])
        return {ref: Trajectory(book.get(f"worlds.{world}.key_rate" if ref == "key" else f"worlds.{world}.{ref}")).value(y, h)
                for ref in ("key", "ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y")}

    def anchor_weights():
        from model.book import anchor_facts, book_roles
        books = book.get("nii.books")
        af = anchor_facts(facts, books)
        roles = book_roles(books)
        iea = sum(af.balances[b] for b in roles.assets)
        return books, roles, {b: af.balances[b] / iea for b in roles.names}

    def phi_split_closed_form() -> None:
        """φ_A = p × φ, φ_L = (1 − p) × φ × A / L; добавка пассивов в стационаре L × φ_L × X_W равна остатку
        сжатия (1 − p) × φ × C_W; Nss, T_real и парные передачи от доли не зависят (М§4.5)."""
        books, roles, w = anchor_weights()
        tr = base.ctx.transmission
        p = float(book.get("nii.phi_split"))
        assert 0 < p < 1
        phi_a = [b for b in roles.assets if books[b].get("phi")]
        phi_l = [b for b in roles.liabilities if books[b].get("phi")]
        a_sum, l_sum = sum(w[b] for b in phi_a), sum(w[b] for b in phi_l)
        assert l_sum > 0 and abs(a_sum - l_sum) > 1e-3                   # отношение весов A / L — не единица
        assert close(tr.phi_assets, p * tr.phi, abs_=1e-15)
        assert close(tr.phi_liab, (1 - p) * tr.phi * a_sum / l_sum, abs_=1e-15)
        ref_w = str(book.get("nii.transmission.reference_world"))
        rh = lt_refs(ref_w)
        for world in book.get("worlds.ids"):
            rw = lt_refs(world)
            c_w = sum(w[b] * (rw[books[b]["ref"]] - rh[books[b]["ref"]]) for b in phi_a)
            x_w = c_w / a_sum
            assert close(l_sum * tr.phi_liab * x_w, (1 - p) * tr.phi * c_w, abs_=1e-15), world
        for other in (0.0, 1.0):
            t2 = make_context(book.with_overrides({"nii.phi_split": other}), facts).transmission
            assert t2.phi == tr.phi and t2.t_real == tr.t_real and dict(t2.nss) == dict(tr.nss)
            assert dict(t2.pairs) == dict(tr.pairs) and t2.sigma0 == tr.sigma0
        one = make_context(book.with_overrides({"nii.phi_split": 1.0}), facts).transmission
        assert one.phi_assets == one.phi and one.phi_liab == 0.0

    def phi_remainder_reaches_liabilities() -> None:
        """Ставка пассива с phi: c_q = c_(q−1) + ρ (β ref_W,q + s(q) + φ_L X_W,q − c_(q−1)), X_W,q — разность опор
        кредитных книг Φ_A мира и мира H с весами якоря (М§4.4); актива с phi — со сжатием φ_A."""
        books, roles, w = anchor_weights()
        tr = base.ctx.transmission
        assert tr.phi_liab != 0.0
        ref_w = str(book.get("nii.transmission.reference_world"))
        phi_a = [b for b in roles.assets if books[b].get("phi")]
        a_sum = sum(w[b] for b in phi_a)
        wm, wh = base.ctx.worlds["M"], base.ctx.worlds[ref_w]
        c = base.cell("M", "norm", "schedule")
        spreads = base.ctx.prep.spreads
        seen = 0.0
        for q in range(1, tl.Q + 1):
            x_q = sum(w[b] / a_sum * (wm.ref(books[b]["ref"])[q] - wh.ref(books[b]["ref"])[q]) for b in phi_a)
            seen = max(seen, abs(x_q))
            for b in roles.names:
                spec = books[b]
                ref = wm.ref(spec["ref"])[q]
                prev = c.books[b]["rate"][q - 1]
                if spec["side"] == "asset":
                    target = ref + spreads[b][q] - (tr.phi_assets * (ref - wh.ref(spec["ref"])[q]) if spec.get("phi") else 0.0)
                else:
                    target = float(spec["beta"]) * ref + spreads[b][q] + (tr.phi_liab * x_q if spec.get("phi") else 0.0)
                assert close(c.books[b]["rate"][q], prev + float(spec["rho"]) * (target - prev), abs_=1e-13), (b, q)
        assert seen > 0.01                                               # в мире M опоры кредитных книг выше мира H

    def fade_is_one_sided() -> None:
        """ROE'_T = k_T + fade × max(ROE_T − k_T, 0) + min(ROE_T − k_T, 0): fade < 1 не повышает оценку ни в одной
        клетке; при ROE_T ≥ k_T — прежняя формула; DDM = RI держится (М§7)."""
        faded = run_grid(book.with_overrides({"valuation.terminal.fade": 0.5}), facts)
        below = above = 0
        for a, b in zip(base.cells, faded.cells):
            assert close(a.roe_t_raw, b.roe_t_raw, rel=1e-12) and a.roe_t == a.roe_t_raw       # центр оси — 1
            if a.roe_t_raw < a.k_t:
                assert b.roe_t == b.roe_t_raw and close(b.v_ri, a.v_ri, rel=1e-12), a.label
                below += 1
            else:
                assert close(b.roe_t, b.k_t + 0.5 * (b.roe_t_raw - b.k_t), abs_=1e-15), a.label
                assert b.v_ri <= a.v_ri, a.label
                above += b.v_ri < a.v_ri
        assert below > 0 and above > 0
        assert not [f.name for f in check_invariants(faded) if f.fired]

    def fvc_reference_is_the_book_before_the_draw() -> None:
        """Сдвиг путей CoR всех режимов на x меняет FVC каждой клетки на factor × x × Ē^СС × d/365: опора FVC —
        путь режима-опоры книги до розыгрыша (М§4.6)."""
        x = 0.001
        factor = float(book.get("credit.fv_loans_factor"))
        shifted = run_grid(book.with_overrides(sensitivity_overrides(book, "cor", x)), facts)
        for a, b in zip(base.cells, shifted.cells):
            for q in range(1, tl.Q + 1):
                want = factor * x * a.quarters["loans_fv_avg"][q] * tl.d(q) / 365
                assert close(b.quarters["fvc"][q] - a.quarters["fvc"][q], want, abs_=1e-9), (a.label, q)
        assert shifted.ctx.book.base is book and book.base is None

    def regime_floor() -> None:
        """Пол вероятности режима: после каждого наблюдения P(r) ≥ floor_share × базовой, Σ = 1 (М§12)."""
        share = float(book.get("joint.regime_update.floor_share"))
        prior = {r: float(book.get(f"joint.regime_prob.{r}")) for r in book.get("regimes.ids")}
        assert 0 < share < 1
        obs = [observation(tl.period(q), 0.045) for q in range(1, 5)]            # четыре отчёта «кризис»
        ctx = with_observations(obs)
        hit = 0
        for u in ctx.updates:
            post = u["posterior_after"]
            assert close(sum(post.values()), 1.0, abs_=1e-12)
            for r in prior:
                assert post[r] >= share * prior[r] - 1e-15, (u["period"], r)
                hit += close(post[r], share * prior[r], abs_=1e-12)
        assert hit > 0                                                           # пол связал
        free = with_observations(obs, **{"joint.regime_update.floor_share": 0.0})
        assert min(free.posterior[r] / prior[r] for r in prior) < share          # без пола режим уходит ниже

    def year_with_reported_quarters() -> None:
        """Годовая упр. метрика года якоря — взвешенное среднее кварталов: отчётные — раскрытым упр. фактом
        истории моста, прогнозные — мостом (М§4.6)."""
        from model.checks import guidance_values
        from model.credit import mgmt_history
        year = tl.anchor_year
        c = base.cell("H", "norm", "schedule")
        got = guidance_values(base, c, year)
        hist = base.ctx.prep.hist_bal
        ac0 = base.ctx.prep.anchor_state["loans"] - base.ctx.prep.af.fv_loans
        mg = mgmt_history(facts, "cor")
        b_cor = facts.need("bridge_mgmt_ifrs", "cor.value")
        num = den = 0.0
        for q in tl.quarters_of_year(year):
            d = tl.d(q)
            if q <= 0:
                ends = [ac0 if j == 0 else hist[j]["loans_ac"] for j in (q - 1, q)]
                z, x = sum(ends) / 2 * d, mg[tl.period(q)]
            else:
                z = c.quarters["loans_ac_avg"][q] * d
                x = c.quarters["llp"][q] * 365 / d / c.quarters["loans_ac_avg"][q] - b_cor
            num, den = num + z * x, den + z
        assert close(got["cor_max"], num / den, abs_=1e-12)
        assert not close(got["cor_max"], c.annual["cor"][0] - b_cor, abs_=1e-5)   # не «год движка минус средний мост»

    def unregistered_dividend_closed_form() -> None:
        """U — дивиденд, вычтенный клетками на закрытых концах кварталов ГОСА без записи реестра (М§14.4)."""
        agm_q = tl.index(f"{tl.anchor_year + 1}Q{book.get('dividends.calendar.agm_quarter')}")
        run = run_grid(book, facts, live_at(tl.end(agm_q)))
        prob = run.layers["analytical"].prob
        want = sum(prob[c.key] * c.quarters["div"][agm_q] for c in run.cells)
        assert want > 0 and close(run.unregistered_dividend, want, rel=1e-12)
        before = run_grid(book, facts, live_at(tl.end(agm_q) - timedelta(days=1)))
        assert before.unregistered_dividend == 0.0
        ex = tl.end(agm_q) + timedelta(days=20)
        rec = DividendRecord(year=tl.anchor_year, dps=40.0, status="declared", record_date=ex,
                             last_buy_date=ex - timedelta(days=1), ex_date=ex, pay_date=ex + timedelta(days=14),
                             sources=("проверка",))
        assert run_grid(book, facts, live_at(tl.end(agm_q), (rec,))).unregistered_dividend == 0.0

    def median_shift_window() -> None:
        """Оценщик срединных прогонов: среднее попарных разностей по прогонам с рангом в окне (М§10)."""
        base_c = [float(i) for i in range(10)]
        moved = [v + (5.0 if i < 2 or i > 7 else 1.0 + 0.1 * i) for i, v in enumerate(base_c)]
        got = U.median_shift(base_c, moved, (0.4, 0.6))
        assert close(got, (1.4 + 1.5) / 2), got                           # ранги 5 и 6 из 10: квантили 4/9 и 5/9
        by = list(reversed(base_c))
        assert close(U.median_shift(base_c, moved, (0.0, 0.25), by=by), (1.7 + 5.0 + 5.0) / 3)    # ранги — по `by`

    def neutral_search_goes_inside_the_bracket() -> None:
        """Строка таблицы с невязкой в рублёвом допуске корнем не считается, если между соседними строками
        невязка меняет знак: поиск идёт внутрь отрезка и останавливается по двум допускам (М§13)."""
        from model.nextreport import _Gap, _neutral
        slope, root_true = 1600.0, 0.06672
        g = _Gap(lambda x: slope * (x - root_true))
        values = [0.064, 0.067, 0.070]
        root, gap, err = _neutral(values, [g.fn(v) for v in values], g, 0.002, 0.5, 0.0001)
        assert err is None and abs(root - root_true) <= 0.0001 and abs(gap) <= 0.5, (root, gap)
        assert abs(g.fn(0.067)) <= 0.5 and root != 0.067                  # строка 0,067 — в рублёвом допуске, но не корень

    def refine_takes_the_secant_step() -> None:
        """Уточнение корня на полной полосе (М§11.2): невязка решения подвыборки вне стопа — шаг секущей от точки
        книги; на линейной невязке он попадает в корень. Невязка в стопе — уточнение кончено без второй полосы."""
        from model.reverse import _refine_full
        calls: list[float] = []

        def g(x: float) -> float:
            calls.append(x)
            return 1000.0 * (x - 0.0052)
        value, gap, estimate = _refine_full(g, 0.0, g(0.0), 0.0048, -0.01, 0.01, 2, 0.05)
        assert close(value, 0.0052, abs_=1e-12) and abs(gap) <= 1e-9 and close(estimate, value), (value, gap)
        assert calls[1:] == [0.0048, value]
        calls.clear()
        value, gap, estimate = _refine_full(g, 0.0, -5.2, 0.0048, -0.01, 0.01, 2, 0.5)
        assert (value, len(calls)) == (0.0048, 1) and close(gap, -0.4) and estimate == value

    def in_range_is_about_the_root() -> None:
        """«В диапазоне книги» — утверждение о корне (М§11.2): приближение внутри диапазона при оценке корня за
        его краем — «вне»; решение с невязкой в стопе судится само."""
        from model.reverse import _refine_full, root_in_range
        g = lambda x: 1000.0 * (x - 0.0052)                               # noqa: E731
        value, gap, estimate = _refine_full(g, 0.0, g(0.0), 0.0048, -0.01, 0.01, 1, 0.05)
        assert value == 0.0048 and abs(gap) > 0.05 and close(estimate, 0.0052, abs_=1e-12)
        assert not root_in_range(value, gap, estimate, -0.003, 0.005, 0.05)   # приближение внутри, корень — за краем
        assert root_in_range(value, gap, estimate, -0.003, 0.006, 0.05)       # и приближение, и оценка корня внутри
        assert root_in_range(0.0048, 0.01, 0.0048, -0.003, 0.005, 0.05)       # сошлось: судится решение
        assert not root_in_range(0.0052, 0.0, 0.0052, -0.003, 0.005, 0.05) and not root_in_range(None, None, None, 0, 1, 0.5)

    def point_neutral_goes_inside_the_bracket() -> None:
        """Нейтральное значение точки (М§13): строка таблицы с невязкой в рублёвом допуске корнем не считается,
        если между соседними строками невязка меняет знак."""
        from model.nextreport import _point_neutral
        slope, root_true = 1600.0, 0.06672
        pg = lambda x: slope * (x - root_true)                            # noqa: E731
        values = [0.064, 0.067, 0.070]
        root = _point_neutral(values, [pg(v) for v in values], pg, 0.002, 0.5, 0.0001)
        assert abs(pg(0.067)) <= 0.5 and abs(root - root_true) <= 0.0001 and root != 0.067, root

    def lt_floor_at_the_ends_of_share_axes() -> None:
        """Гейт `lt_spread_floor` проверяется и в углах области осей долей (М§4.5): пол, который выдерживают
        значения книги, но не выдерживает конец оси доли сжатия, — гейт срабатывает."""
        from model.checks import check_gates
        tr = base.ctx.transmission
        floors = {b: min(row.values()) - 1e-4 for b, row in tr.lt_spread.items()}
        axes = [a for a in book.get("valuation.uncertainty.axes")
                if not set(a["paths"]) & {"nii.sigma0_split", "nii.phi_split"}]
        axis = {"name": "доля сжатия", "kind": "value", "paths": ["nii.phi_split"],
                "low": float(book.get("nii.phi_split")), "high": 1.0, "dist": "triangular"}
        quiet = run_grid(book.with_overrides({"checks.lt_spread_floor": floors, "valuation.uncertainty.axes": axes}),
                         facts)
        gate = next(f for f in check_gates(quiet) if f.name == "lt_spread_floor")
        assert not gate.fired, gate.message
        wide = run_grid(book.with_overrides({"checks.lt_spread_floor": floors,
                                             "valuation.uncertainty.axes": axes + [axis]}), facts)
        gate = next(f for f in check_gates(wide) if f.name == "lt_spread_floor")
        assert tr.phi > 0 and float(book.get("nii.phi_split")) < 1.0
        assert gate.fired and gate.detail["axis_ends"], gate.message

    def nowcast_year_on_one_layer() -> None:
        """Без нау-каста квартал открытого периода в сумме года — квартал смеси заголовка: `dps` = `dps_model`
        (П§2 nowcast.year)."""
        from model import payload as P
        from tests.support_core2 import CONTROL_FIXTURE, TODAY, explained
        from model.checks import check_gates
        expl = explained(check_gates(base, today=TODAY))
        rel = P.make_release(live=False, fast=True, today=TODAY, book=book, facts=facts, explanations=expl,
                             notes=[], draws=4, control_model=CONTROL_FIXTURE)
        year = P.build_payload(rel)["nowcast"]["year"]
        assert year["dps"] is not None and close(year["dps"], year["dps_model"], abs_=1e-4), year

    return {f.__name__: f for f in (invariants, llp_shift_exact, interest_legs_simple, kappa_zero_in_n,
                                    dps_is_pool_over_issued, exdate_jump_with_governance,
                                    h_world_independent_of_phi, cor_path_bridged_once,
                                    deductions_scale_with_rwa, unaudited_by_calendar,
                                    agm_quarter_neutral_for_capital, apu_cap_per_observation,
                                    ddm_ri_mid_quarter, lhs_order, quantile_type_7, quarter_end_continuity,
                                    sigma0_from_year, epsilon_ramp, misc_quarter_profile,
                                    apu_layer_average_is_observation, terminal_closed_form, dia_leg_from_book,
                                    kappa_lag_closed_form, cor_includes_kappa, first_dy_from_anchor_curve,
                                    la_minimum_binds, fvc_reference_regime, pending_dividend_without_discount,
                                    roll_is_end_of_day, apu_window, apu_frozen_expectations,
                                    crisis_skip_is_not_a_cut, sigma0_split_closed_form, lt_spread_closed_form,
                                    neutral_edge_tolerance, guidance_point_tolerance, mean_shift_formula,
                                    phi_split_closed_form, phi_remainder_reaches_liabilities, fade_is_one_sided,
                                    fvc_reference_is_the_book_before_the_draw, regime_floor,
                                    year_with_reported_quarters, unregistered_dividend_closed_form,
                                    median_shift_window, neutral_search_goes_inside_the_bracket,
                                    nowcast_year_on_one_layer, refine_takes_the_secant_step,
                                    in_range_is_about_the_root, point_neutral_goes_inside_the_bracket,
                                    lt_floor_at_the_ends_of_share_axes)}


def main(argv: list[str]) -> int:
    if "--check" not in argv:
        print("использование: python -m tests.mutations --check")
        return 64
    result: dict[str, str] = {}
    try:
        checks = _checks()
    except Exception as exc:                      # мутант может сломать и подготовку
        print(json.dumps({"_setup": f"{type(exc).__name__}: {exc}"[:500]}, ensure_ascii=False))
        return 0
    for name, fn in checks.items():
        try:
            fn()
            result[name] = "ok"
        except Exception as exc:
            result[name] = f"{type(exc).__name__}: {exc}"[:300]
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
