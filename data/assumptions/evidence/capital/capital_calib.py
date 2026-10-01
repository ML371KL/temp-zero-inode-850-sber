"""Калибровка капитала и дивидендного календаря Сбера — лист этапа 1 (calib-capital), перенесённый в книгу 1.0.

Запуск из корня репозитория: python -B data/assumptions/evidence/capital/capital_calib.py  → out/*.csv и out/out.txt.
Входы — малые входы inputs/ (evidence/extract_inputs.py; пути повторяют папку передачи), КБД на концы кварталов —
inputs/stage1/calib/capital/data/zcyc_quarter_ends.csv (MOEX ISS). Перенос меняет только корни путей.
Базисы не смешиваются: колонки с суффиксом _B — Базель группы (МСФО-капитал), _reg — регуляторный
Н20.0 группы (управленческая оценка дня отчёта), _bank — РСБУ банка соло (формы ЦБ 123/135).
"""
from __future__ import annotations

import csv
import math
import os
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
S1 = os.path.join(HERE, 'inputs', 'stage1')     # книга 1.0: корень путей — малые входы листа
DATA = os.path.join(HERE, 'out')
os.makedirs(DATA, exist_ok=True)
OUT = []
NL = "\n"                                     # перевод строки выходов — LF


def p(*a):
    s = ' '.join(str(x) for x in a)
    OUT.append(s)
    print(s)


def fnum(x):
    if x is None or x == '':
        return None
    return float(x)


# ------------------------------------------------------------------ загрузка
def load_mgmt():
    d = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(S1, 'mgmt', 'mgmt_kpi.csv'), encoding='utf-8')):
        d[r['metric']][r['period']] = (float(r['value']), f"mgmt_kpi.csv: {r['doc']} с.{r['page']}")
    return d


def load_ifrs():
    d = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(S1, 'ifrs', 'ifrs_history.csv'), encoding='utf-8')):
        d[r['metric']][r['period']] = (float(r['value']), f"ifrs_history.csv: {r['doc'].split('/')[-1]} с.{r['page']}")
    q = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(S1, 'ifrs', 'ifrs_quarterly.csv'), encoding='utf-8')):
        q[r['metric']][r['period']] = (float(r['value']), f"ifrs_quarterly.csv: {r['doc'].split('/')[-1]} с.{r['page']}")
    return d, q


def load_f135():
    d = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(S1, 'ras', 'cbr_f135.csv'), encoding='utf-8')):
        if r['norm'] in ('Н1.0', 'Н1.1', 'Н1.2') and r['value_pct'] not in ('', None):
            d[r['form_date']][r['norm']] = float(r['value_pct'])
            d[r['form_date']]['sha135'] = r['sha256']
    return d


def load_f123():
    d = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(S1, 'ras', 'cbr_f123.csv'), encoding='utf-8')):
        if r['code'] in ('000', '102', '105', '106', '203') and r['value_thousand_rub'] not in ('', None):
            d[r['form_date']][r['code']] = float(r['value_thousand_rub']) / 1e6
            d[r['form_date']]['sha123'] = r['sha256']
    return d


def load_ras_release():
    d = {}
    for r in csv.DictReader(open(os.path.join(S1, 'ras', 'ras_release_monthly_wide.csv'), encoding='utf-8')):
        d[r['month']] = fnum(r['np_ytd'])
    return d


def load_zcyc():
    d = defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(S1, 'calib', 'capital', 'data', 'zcyc_quarter_ends.csv'), encoding='utf-8')):
        d[r['quarter_end']][float(r['period_y'])] = float(r['yield_pct'])
    return d


M = load_mgmt()
I, IQ = load_ifrs()
F135 = load_f135()
F123 = load_f123()
RASNP = load_ras_release()
ZC = load_zcyc()

QS = ['2022Q4', '2023Q1', '2023Q2', '2023Q3', '2023Q4', '2024Q1', '2024Q2', '2024Q3', '2024Q4',
      '2025Q1', '2025Q2', '2025Q3', '2025Q4', '2026Q1', '2026Q2']
QEND = {'Q1': ('03-31', '04-01'), 'Q2': ('06-30', '07-01'), 'Q3': ('09-30', '10-01'), 'Q4': ('12-31', '01-01')}


def qdates(q):
    y, k = int(q[:4]), q[4:]
    e, f = QEND[k]
    return f'{y}-{e}', (f'{y + 1}-{f}' if k == 'Q4' else f'{y}-{f}')


def ifrs_q_label(q):  # '2024Q2' -> '2Q2024'
    return f'{q[5]}Q{q[:4]}'


def g(dct, key):
    v = dct.get(key)
    return None if v is None else v[0]


# Дивиденды: вычтены из капитала МСФО на ГОСА, из регуляторного — в месяце отсечки [Ф].
# Сумма — строка «Дивиденды объявленные» в нераспределённой прибыли (без собственных акций).
DIV = {  # год ГОСА: (сумма, ГОСА-квартал, месяц отсечки/рег. вычета, источник)
    2023: (None, 'Q2', '2023-05', 'ГОСА апрель 2023 (МСФО 6М23 — капитал 30.06.2023 уже без дивиденда); отсечка 11.05.2023; рег. вычет — май 2023 (релиз РСБУ 5М23: базовый капитал 4 972 → 4 390)'),
    2024: (743.2, 'Q2', '2024-07', 'МСФО 6М25 прим. 11 (ГОСА июнь 2024), eqf_dividends_declared__retained 6M2024; отсечка 11.07.2024; форма 123: 01.07 5 864,3 → 01.08 5 113,5'),
    2025: (778.5, 'Q2', '2025-07', 'МСФО 6М26 прим. 11 (ГОСА июнь 2025), 6M2025 −778,5; отсечка 18.07.2025; форма 123: 01.07 6 379,3 → 01.08 5 583,5'),
    2026: (841.0, 'Q2', '2026-07', 'МСФО 6М26 с. 43 (ГОСА июнь 2026), 6M2026 −841,0; отсечка 20.07.2026; форма 123: 01.07 7 120,5 → 01.08 6 260,6'),
}


def div_pending(q):
    """Дивиденд, уже вычтенный из МСФО-капитала, но ещё не из регуляторного, на конец квартала."""
    y = int(q[:4])
    if q[4:] == 'Q2' and y in DIV and DIV[y][0] is not None and DIV[y][2] > f'{y}-06':
        return DIV[y][0]
    return 0.0


# ------------------------------------------------------------------ 1. Разрыв Н20.0 − Базель
p('=' * 100)
p('1. Разрыв Н20.0 − Базель (группа), п.п.')
rows = []
for q in QS:
    de, fd = qdates(q)
    n20 = g(M['n20_0'], q)
    bc = g(M['basel_cet1_ratio'], q)
    bt = g(M['basel_total_ratio'], q)
    b1 = g(M['basel_tier1_ratio'], q)
    rwa = g(M['basel_rwa'], q) or g(I['basel_rwa'], de)
    cet1 = g(I['basel_cet1'], de) or g(M['basel_cet1_capital'], q)
    tot = g(I['basel_total_capital'], de) or g(M['basel_total_capital'], q)
    t2 = g(I['basel_t2'], de)
    # точные коэффициенты, где есть абсолюты; иначе — округлённые до 0,1
    bc_x = 100 * cet1 / rwa if cet1 and rwa else bc
    bt_x = 100 * tot / rwa if tot and rwa else bt
    dp = div_pending(q)
    dp_pp = 100 * dp / rwa if dp else 0.0
    bv = g(I['eq_shareholders'], de)
    at1 = g(I['eq_at1'], de) or 150.0
    fv = g(I['orf_fvoci'], de)
    r = dict(q=q, date=de, n20_reg=n20, n20_src=M['n20_0'].get(q, (None, ''))[1],
             basel_cet1_B=round(bc_x, 3) if bc_x else None, basel_t1_B=b1, basel_total_B=round(bt_x, 3) if bt_x else None,
             basel_total_exact=bool(tot and rwa), rwa_B=rwa, cet1_B=cet1, total_cap_B=tot, t2_B=t2,
             bv_shareholders=bv, bv_common=(bv - at1) if bv else None, at1=at1, fvoci_reserve=fv,
             div_pending=dp, div_pending_pp=round(dp_pp, 3),
             gap_total_raw=round(n20 - bt_x, 3) if n20 and bt_x else None,
             gap_cet1_raw=round(n20 - bc_x, 3) if n20 and bc_x else None,
             gap_total_adj=round(n20 - bt_x - dp_pp, 3) if n20 and bt_x else None,
             gap_cet1_adj=round(n20 - bc_x - dp_pp, 3) if n20 and bc_x else None)
    rows.append(r)
GAP = {r['q']: r for r in rows}
hdr = ['q', 'n20_reg', 'basel_cet1_B', 'basel_total_B', 'rwa_B', 'div_pending', 'div_pending_pp',
       'gap_total_raw', 'gap_total_adj', 'gap_cet1_raw', 'gap_cet1_adj', 'fvoci_reserve']
p('\t'.join(hdr))
for r in rows:
    p('\t'.join('' if r[h] is None else (f'{r[h]:.2f}' if isinstance(r[h], float) else str(r[h])) for h in hdr))


def stats(vals):
    v = [x for x in vals if x is not None]
    return dict(n=len(v), mean=round(st.mean(v), 3), sd=round(st.stdev(v), 3) if len(v) > 1 else None,
                min=round(min(v), 3), max=round(max(v), 3))


sets = {'2023–2026 (все)': [r for r in rows if r['q'] >= '2023Q1'],
        '2024–2026': [r for r in rows if r['q'] >= '2024Q1'],
        '2025–2026': [r for r in rows if r['q'] >= '2025Q1'],
        '2023': [r for r in rows if r['q'].startswith('2023')]}
GAPSTATS = {}
for name, rs in sets.items():
    for k in ('gap_total_raw', 'gap_total_adj', 'gap_cet1_adj'):
        s = stats([r[k] for r in rs])
        GAPSTATS[(name, k)] = s
        p(f'  {name:18s} {k:15s} {s}')

# вклад непризнаваемой части фонда FVOCI (не-ОФЗ) — см. раздел 4
with open(os.path.join(DATA, 'gap_n20_quarterly.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

# ------------------------------------------------------------------ 2. Банк: помесячно
p('=' * 100)
p('2. Банк соло (РСБУ): формы 135 и 123 помесячно; неаудированная прибыль; июльский провал')


def audited_cutoff_unaudited(form_date):
    """Неаудированная прибыль РСБУ банка на дату формы (по календарю аудитов, [Ф] по форме 123):
    9М — аудит в ноябре (с формы 01.12), год — в марте (с формы 01.04); 6М2024 — аудит в августе (с 01.09.2024).
    Возвращает (сумма, описание) по ЧП нарастающим итогом из релизов РСБУ (ras_release_monthly_wide)."""
    y, m = int(form_date[:4]), int(form_date[5:7])
    # месяц данных = месяц перед датой формы
    dy, dm = (y - 1, 12) if m == 1 else (y, m - 1)
    ytd = RASNP.get(f'{dy}-{dm:02d}')
    if ytd is None:
        return None, 'нет ЧП РСБУ'
    if dm == 12:  # 01.01: аудирован 9М
        return ytd - RASNP[f'{dy}-09'], 'Q4 прошлого года'
    if dm in (1, 2):  # 01.02, 01.03: + Q4 прошлого года
        q4 = RASNP[f'{dy - 1}-12'] - RASNP[f'{dy - 1}-09']
        return ytd + q4, 'Q4 прошлого года + с начала года'
    if dm == 11:  # 01.12: 9М аудирован
        return ytd - RASNP[f'{dy}-09'], 'окт–ноя'
    if dy == 2024 and 8 <= dm <= 10:
        return ytd - RASNP['2024-06'], 'с июля (6М2024 аудирован)'
    return ytd, 'с начала года'


mon = []
for fd in sorted(F135):
    if fd < '2023-06-01':
        continue
    a = F135[fd]
    b = F123.get(fd, {})
    n10, n11, n12 = a.get('Н1.0'), a.get('Н1.1'), a.get('Н1.2')
    k0, k1, k106, t2 = b.get('000'), b.get('102'), b.get('106'), b.get('203')
    rwa0 = k0 / (n10 / 100) if k0 and n10 else None
    rwa1 = k1 / (n11 / 100) if k1 and n11 else None
    un, un_txt = audited_cutoff_unaudited(fd)
    mon.append(dict(form_date=fd, n1_1=n11, n1_2=n12, n1_0=n10, cap_base=k1, cap_main=k106, cap_total=k0, t2=t2,
                    rwa_bank_via_n10=round(rwa0, 1) if rwa0 else None, rwa_bank_via_n11=round(rwa1, 1) if rwa1 else None,
                    unaudited_np_ras=round(un, 1) if un is not None else None, unaudited_rule=un_txt,
                    t2_ex_unaudited=round(t2 - un, 1) if (t2 is not None and un is not None) else None,
                    sha135=a.get('sha135'), sha123=b.get('sha123')))
with open(os.path.join(DATA, 'bank_monthly.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(mon[0]))
    w.writeheader()
    w.writerows(mon)
p('form_date  N1.1   N1.0   base    total   T2     RWA(N1.0) RWA(N1.1) unaud  T2-unaud  rule')
for r in mon:
    p(f"{r['form_date']} {r['n1_1']:6.2f} {r['n1_0']:6.2f} {r['cap_base']:7.1f} {r['cap_total']:7.1f} {r['t2']:6.1f} "
      f"{r['rwa_bank_via_n10']:9.0f} {r['rwa_bank_via_n11']:9.0f} {'' if r['unaudited_np_ras'] is None else r['unaudited_np_ras']:>7} "
      f"{'' if r['t2_ex_unaudited'] is None else r['t2_ex_unaudited']:>8}  {r['unaudited_rule']}")
MON = {r['form_date']: r for r in mon}
t2x = [r['t2_ex_unaudited'] for r in mon if r['t2_ex_unaudited'] is not None]
p('T2 минус неаудированная ЧП РСБУ:', stats(t2x), '— против T2:', stats([r['t2'] for r in mon]))
p('RWA банка: через Н1.0 / через Н1.1, отношение:', stats([r['rwa_bank_via_n10'] / r['rwa_bank_via_n11'] for r in mon]))

p('-- Июльский провал (форма 01.07 → 01.08) и другие скачки базового капитала (|Δ| > 300 млрд):')
JULY = []
for i in range(1, len(mon)):
    a, b = mon[i - 1], mon[i]
    dk = b['cap_base'] - a['cap_base']
    if abs(dk) > 300:
        dn = b['n1_1'] - a['n1_1']
        p(f"  {a['form_date']} → {b['form_date']}: Δбазовый {dk:+8.1f} млрд; ΔН1.1 {dn:+.3f} п.п.; ΔT2 {b['t2'] - a['t2']:+.1f}; RWA {a['rwa_bank_via_n11']:.0f}→{b['rwa_bank_via_n11']:.0f}")
        if b['form_date'][5:7] == '08':
            y = int(b['form_date'][:4])
            dv = DIV[y][0]
            JULY.append(dict(year=y, n11_before=a['n1_1'], n11_after=b['n1_1'], d_n11=round(dn, 3), d_base=round(dk, 1),
                             div_ifrs=dv, div_pp_of_rwa_bank=round(100 * dv / b['rwa_bank_via_n11'], 3),
                             n10_before=a['n1_0'], n10_after=b['n1_0']))
for j in JULY:
    p('  июль', j)
# минимумы Н1.1 по годам
for y in (2023, 2024, 2025, 2026):
    ys = [r for r in mon if r['form_date'].startswith(str(y))]
    mn = min(ys, key=lambda r: r['n1_1'])
    p(f'  минимум Н1.1 в {y}: {mn["n1_1"]:.3f} на {mn["form_date"]}')

# ------------------------------------------------------------------ 2b. Н1.1 банка ↔ CET1 группы (кварталы)
p('=' * 100)
p('2b. Н1.1 банка (форма на 1-е число после конца квартала) против CET1 группы (Базель)')


def np_sh(q):
    return g(IQ['np_shareholders'], ifrs_q_label(q))


def unaudited_ifrs(q):
    """Та же логика аудитов, но на ЧП МСФО акционерам по кварталам (прокси для модели):
    Q1 → Q1; Q2 → Q1+Q2; Q3 → Q1..Q3 (2024: только Q3); Q4 → Q4."""
    y, k = q[:4], q[4:]
    qs = {'Q1': ['Q1'], 'Q2': ['Q1', 'Q2'], 'Q3': ['Q1', 'Q2', 'Q3'], 'Q4': ['Q4']}[k]
    if q == '2024Q3':
        qs = ['Q3']
    vals = [np_sh(f'{y}{x}') for x in qs]
    return None if any(v is None for v in vals) else sum(vals)


def ytd_ifrs(q):
    y, k = q[:4], int(q[5])
    vals = [np_sh(f'{y}Q{i}') for i in range(1, k + 1)]
    return None if any(v is None for v in vals) else sum(vals)


b2 = []
for q in QS:
    de, fd = qdates(q)
    r = GAP[q]
    mb = MON.get(fd)
    if not mb:
        continue
    dp = r['div_pending']
    E = unaudited_ifrs(q)
    Y = ytd_ifrs(q)
    bvc = r['bv_common']
    rwa = r['rwa_B']
    x = dict(q=q, form_date=fd, n1_1_bank=mb['n1_1'], n1_0_bank=mb['n1_0'], cet1_ratio_B=r['basel_cet1_B'],
             gap_n11_minus_cet1B=round(mb['n1_1'] - r['basel_cet1_B'], 3),
             cap_base_bank=mb['cap_base'], cet1_B=r['cet1_B'], bv_common=bvc, div_pending=dp,
             unaudited_np_ifrs=E, np_ytd_ifrs=Y,
             rwa_bank=mb['rwa_bank_via_n11'], rwa_B=rwa,
             rwa_bank_to_B=round(mb['rwa_bank_via_n11'] / rwa, 4) if rwa else None)
    if bvc and E is not None:
        base_model = bvc + dp - E  # BVreg − E (без поправки FVOCI)
        x['bvreg_minus_unaud'] = round(base_model, 1)
        x['cap_base_bank_to_bvreg_minus_unaud'] = round(mb['cap_base'] / base_model, 4)
        x['k11_implied'] = round(mb['n1_1'] / 100 * rwa / base_model, 4)  # recognition, если gap = 0
        x['n11_if_rec1'] = round(100 * base_model / rwa, 3)
        if Y is not None:
            base_ytd = bvc + dp - Y
            x['k11_implied_ytd_rule'] = round(mb['n1_1'] / 100 * rwa / base_ytd, 4)
    b2.append(x)
with open(os.path.join(DATA, 'n11_bridge_quarterly.csv'), 'w', newline='', encoding='utf-8') as f:
    keys = []
    for x in b2:
        for k in x:
            if k not in keys:
                keys.append(k)
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(b2)
p('q       N1.1b  CET1B  gap    baseB    CET1B    BVc     Dp    E(unaud) YTD    RWAb/RWAB k11(audit) k11(ytd)')
for x in b2:
    p(f"{x['q']} {x['n1_1_bank']:6.2f} {x['cet1_ratio_B']:6.2f} {x['gap_n11_minus_cet1B']:+5.2f} {x['cap_base_bank']:7.1f} "
      f"{(x['cet1_B'] or 0):7.1f} {(x['bv_common'] or 0):7.1f} {x['div_pending']:5.0f} {str(x['unaudited_np_ifrs']):>8} {str(x['np_ytd_ifrs']):>7} "
      f"{x['rwa_bank_to_B']:.4f}   {x.get('k11_implied', '')}   {x.get('k11_implied_ytd_rule', '')}")
k11 = [x['k11_implied'] for x in b2 if 'k11_implied' in x]
k11y = [x['k11_implied_ytd_rule'] for x in b2 if 'k11_implied_ytd_rule' in x]
p('k11 (правило аудитов):', stats(k11))
p('k11 (правило «вся прибыль года вне базового»):', stats(k11y))
p('RWA банка / RWA группы (Базель):', stats([x['rwa_bank_to_B'] for x in b2]))
p('отношение базового капитала банка к (BV + Dp − E):', stats([x['cap_base_bank_to_bvreg_minus_unaud'] for x in b2 if 'cap_base_bank_to_bvreg_minus_unaud' in x]))
B2 = {x['q']: x for x in b2}

# ------------------------------------------------------------------ 3. BV → капитал; плотность RWA
p('=' * 100)
p('3. Мост BV → CET1 (Базель) и плотность RWA')
b3 = []
for q in QS:
    de, fd = qdates(q)
    r = GAP[q]
    bvc = r['bv_common']
    cet1 = r['cet1_B']
    ta = g(I['total_assets'], de)
    loans = g(I['loans_ac_total_gross'], de)
    ecl = g(I['loans_ac_total_ecl'], de)
    fvl = g(I['loans_fvtpl'], de)
    x = dict(q=q, bv_common=bvc, cet1_B=cet1, ded_cet1=round(bvc - cet1, 1) if bvc and cet1 else None,
             cet1_to_bv=round(cet1 / bvc, 4) if bvc and cet1 else None,
             intangibles=g(I['intangibles'], de), dta=g(I['dta'], de), assoc=g(I['assoc_investments'], de),
             t2_B=r['t2_B'], sub_debt=g(I['sub_debt'], de), rwa_B=r['rwa_B'], total_assets=ta,
             rwa_to_assets=round(r['rwa_B'] / ta, 4) if ta and r['rwa_B'] else None,
             rwa_to_loans=round(r['rwa_B'] / (loans + ecl + fvl), 4) if loans and fvl else None,
             rwa_density_mgmt=g(M['rwa_density'], q))
    if x['ded_cet1'] is not None and x['intangibles'] is not None:
        x['ded_other'] = round(x['ded_cet1'] - x['intangibles'], 1)
    b3.append(x)
with open(os.path.join(DATA, 'bv_to_capital_quarterly.csv'), 'w', newline='', encoding='utf-8') as f:
    keys = []
    for x in b3:
        for k in x:
            if k not in keys:
                keys.append(k)
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(b3)
for x in b3:
    p(x)

# плотность по книгам: грубая регрессия RWA на книги (неотрицательные МНК по перебору), индикативно
books = {
    'corp': lambda de: g(I['loans_ac_commercial_gross'], de) + g(I['loans_ac_project_gross'], de) + g(I['loans_fvtpl'], de),
    'mortgage': lambda de: g(I['loans_ac_mortgage_gross'], de),
    'retail_other': lambda de: g(I['loans_ac_consumer_gross'], de) + g(I['loans_ac_cards_gross'], de) + g(I['loans_ac_auto_gross'], de),
}
X, yv, qq = [], [], []
for q in QS:
    de, _ = qdates(q)
    try:
        vec = [f(de) for f in books.values()]
        ta = g(I['total_assets'], de)
        rest = ta - sum(vec)
        X.append(vec + [rest])
        yv.append(GAP[q]['rwa_B'])
        qq.append(q)
    except TypeError:
        pass


def lstsq(X, y):
    import itertools
    n = len(X[0])
    best = None
    # неотрицательные МНК перебором активных множеств (n ≤ 5)
    for mask in itertools.product([0, 1], repeat=n):
        idx = [i for i in range(n) if mask[i]]
        if not idx:
            continue
        A = [[row[i] for i in idx] for row in X]
        # нормальные уравнения
        k = len(idx)
        ATA = [[sum(A[r][i] * A[r][j] for r in range(len(A))) for j in range(k)] for i in range(k)]
        ATy = [sum(A[r][i] * y[r] for r in range(len(A))) for i in range(k)]
        # Гаусс
        Mx = [ATA[i] + [ATy[i]] for i in range(k)]
        ok = True
        for c in range(k):
            piv = max(range(c, k), key=lambda r: abs(Mx[r][c]))
            if abs(Mx[piv][c]) < 1e-9:
                ok = False
                break
            Mx[c], Mx[piv] = Mx[piv], Mx[c]
            for r in range(k):
                if r != c:
                    fct = Mx[r][c] / Mx[c][c]
                    Mx[r] = [a - fct * b for a, b in zip(Mx[r], Mx[c])]
        if not ok:
            continue
        beta = [Mx[i][k] / Mx[i][i] for i in range(k)]
        if min(beta) < 0:
            continue
        full = [0.0] * n
        for i, b in zip(idx, beta):
            full[i] = b
        sse = sum((y[r] - sum(full[i] * X[r][i] for i in range(n))) ** 2 for r in range(len(X)))
        if best is None or sse < best[0]:
            best = (sse, full)
    return best


best = lstsq(X, yv)
names = list(books) + ['rest_of_assets']
rmse = math.sqrt(best[0] / len(yv))
p('Регрессия RWA_B на книги (НМНК, 2022К4–2026К2, n=%d):' % len(yv), {n: round(b, 3) for n, b in zip(names, best[1])},
  f'RMSE {rmse:.0f} млрд ({100 * rmse / st.mean(yv):.1f} % RWA)')
DENS = dict(zip(names, best[1]))
# устойчивость: без 2022К4–2023
X2 = [x for x, q in zip(X, qq) if q >= '2024Q1']
y2 = [y for y, q in zip(yv, qq) if q >= '2024Q1']
best2 = lstsq(X2, y2)
p('  то же, 2024К1–2026К2:', {n: round(b, 3) for n, b in zip(names, best2[1])}, f'RMSE {math.sqrt(best2[0] / len(y2)):.0f}')

# 3b. Плотность по книгам не раскрыта (МСФО, презентации; раскрытие 4482-У группы не публикуется с 2022) —
# априорные относительные веса [В] и один масштаб к RWA якоря.
PRIOR = {'corp_loans': 1.00, 'mortgage': 0.50, 'consumer': 1.50, 'cards': 1.50, 'auto': 1.00,
         'securities': 0.15, 'liquidity': 0.10, 'other_assets': 1.50}
OPMKT = 1.15  # надбавка операционного и рыночного риска: (опер.+рын.)/RWA 2018–2021 = 12,5–15,1 % → ×1/(1−доля) ≈ 1,14–1,18 [Ф: МСФО 2018–2021]
de = '2026-06-30'
E0 = {'corp_loans': g(I['loans_ac_commercial_gross'], de) + g(I['loans_ac_project_gross'], de) + g(I['loans_fvtpl'], de),
      'mortgage': g(I['loans_ac_mortgage_gross'], de), 'consumer': g(I['loans_ac_consumer_gross'], de),
      'cards': g(I['loans_ac_cards_gross'], de), 'auto': g(I['loans_ac_auto_gross'], de),
      'securities': g(I['securities'], de) + g(I['securities_repo'], de),
      'liquidity': g(I['cash'], de) + g(I['due_from_banks'], de)}
loans_net = g(I['loans_ac_total_gross'], de) + g(I['loans_ac_total_ecl'], de) + g(I['loans_fvtpl'], de)
E0['other_assets'] = g(I['total_assets'], de) - loans_net - E0['securities'] - E0['liquidity']
raw = OPMKT * sum(PRIOR[k] * E0[k] for k in PRIOR)
scale = GAP['2026Q2']['rwa_B'] / raw
p(f'3b. Априорные веса × масштаб к RWA 30.06.2026: масштаб {scale:.3f}; экспозиции {({k: round(v, 1) for k, v in E0.items()})}')
p('    плотность книг после масштаба (с опер./рын. риском):', {k: round(PRIOR[k] * OPMKT * scale, 3) for k in PRIOR})
for k in ('basel_rwa_market', 'basel_rwa_operational'):
    p('   ', k, {d: v[0] for d, v in I[k].items()}, '→ доля в RWA:',
      {d: round(v[0] / g(I['basel_rwa'], d), 3) for d, v in I[k].items() if g(I['basel_rwa'], d)})

# ------------------------------------------------------------------ 4. FVOCI
p('=' * 100)
p('4. Фонд FVOCI: история, состав корректировки справедливой стоимости по эмитентам, оценка дюрации')
FVADJ = [  # до налога, млрд; МСФО годовые, прим. 9 и 10 (кредитное качество FVOCI и FVOCI в репо)
    # дата, ОФЗ без репо, ОФЗ в репо, прочие без репо, прочие в репо, итого, источник
    ('2023-12-31', -47.3, -177.9, -84.4, -22.8, -332.4, 'МСФО 2024 (ifrs_FY2024_statements.pdf) прим. 9 и 10, стр. текста 1918–1971, 2406–2425'),
    ('2024-12-31', -502.8, -31.5, -114.3, -109.8, -758.4, 'МСФО 2025 (ifrs_FY2025_statements.pdf) прим. 9 и 10, стр. текста 2121–2165, 2480–2501'),
    ('2025-12-31', -118.7, -215.3, -83.8, -16.2, -434.0, 'МСФО 2025 прим. 9 (с. 49 PDF) и 10 (с. 57 PDF)'),
]
fvrows = []
for d, o1, o2, n1, n2, tot, src in FVADJ:
    assert abs(o1 + o2 + n1 + n2 - tot) < 0.15, (d, o1 + o2 + n1 + n2, tot)
    res = g(I['orf_fvoci'], d)
    fvrows.append(dict(date=d, ofz_fv_adj=round(o1 + o2, 1), nonofz_fv_adj=round(n1 + n2, 1), total_fv_adj=tot,
                       ofz_share=round((o1 + o2) / tot, 3), fvoci_reserve_after_tax=res,
                       reserve_to_fvadj=round(res / tot, 3) if res else None, src=src))
    p(fvrows[-1])
with open(os.path.join(DATA, 'fvoci_fv_adjustment.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(fvrows[0]))
    w.writeheader()
    w.writerows(fvrows)

# история фонда и портфеля
p('Фонд FVOCI (после налога) и портфель FVOCI (долговые, без репо + в репо), ОФЗ:')
fvh = []
for d in sorted(set(I['orf_fvoci']) | set(I['sec_fvoci_debt'])):
    if d < '2018-12-31':
        continue
    port = (g(I['sec_fvoci_debt'], d) or 0) + (g(I['repo_fvoci_debt'], d) or 0)
    ofz = (g(I['sec_fvoci_ofz'], d) or 0) + (g(I['repo_fvoci_ofz'], d) or 0)
    x = dict(date=d, fvoci_reserve=g(I['orf_fvoci'], d), portfolio=round(port, 1) if port else None,
             ofz=round(ofz, 1) if ofz else None, ofz_share_carrying=round(ofz / port, 3) if port else None,
             reserve_pct_portfolio=round(100 * g(I['orf_fvoci'], d) / port, 2) if port and g(I['orf_fvoci'], d) is not None else None)
    fvh.append(x)
    p(' ', x)
with open(os.path.join(DATA, 'fvoci_history.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(fvh[0]))
    w.writeheader()
    w.writerows(fvh)


# дюрация: ΔR(после налога) ≈ −D × Δy × P(нач.) × (1 − τ); квартальные OCI 2024–2026, годовые 2018–2025
def ylevel(qe, T):
    c = ZC.get(qe)
    if not c:
        return None
    ks = sorted(c)
    if T in c:
        return c[T]
    lo = max(k for k in ks if k <= T)
    hi = min(k for k in ks if k >= T)
    return c[lo] + (c[hi] - c[lo]) * (T - lo) / (hi - lo)


def prev_qe(d):
    y, m = int(d[:4]), d[5:7]
    return {'03': f'{y - 1}-12-31', '06': f'{y}-03-31', '09': f'{y}-06-30', '12': f'{y}-09-30'}[m]


obs = []
# квартальные наблюдения: oci_fvoci_fv_change_net (после налога), портфель на начало квартала
for lab, (v, src) in IQ['oci_fvoci_fv_change_net'].items():
    k, y = int(lab[0]), int(lab[2:])
    de = {1: f'{y}-03-31', 2: f'{y}-06-30', 3: f'{y}-09-30', 4: f'{y}-12-31'}[k]
    ds = prev_qe(de)
    port = (g(I['sec_fvoci_debt'], ds) or 0) + (g(I['repo_fvoci_debt'], ds) or 0)
    if not port:
        continue
    tau = 0.25 if de >= '2025-01-01' else 0.20
    obs.append(dict(period=lab, start=ds, end=de, oci=v, port=port, tau=tau))
# годовые (FY) — где есть портфель на начало года
for lab, (v, src) in I['oci_fvoci_fv_change_net'].items():
    if not lab.startswith('FY'):
        continue
    y = int(lab[2:])
    ds, de = f'{y - 1}-12-31', f'{y}-12-31'
    port = (g(I['sec_fvoci_debt'], ds) or 0) + (g(I['repo_fvoci_debt'], ds) or 0)
    if not port:
        continue
    obs.append(dict(period=lab, start=ds, end=de, oci=v, port=port, tau=0.25 if y >= 2025 else 0.20))
DUR = {}
for T in (1.0, 2.0, 3.0, 5.0):
    pts = []
    for o in obs:
        y0, y1 = ylevel(o['start'], T), ylevel(o['end'], T)
        if y0 is None or y1 is None:
            continue
        dy = (y1 - y0) / 100
        pv = o['oci'] / ((1 - o['tau']) * o['port'])  # относительное изменение цены портфеля
        pts.append((o['period'], dy, pv))
    for name, sel in (('кварталы 2024–2026', lambda s: 'Q' in s), ('годы 2019–2025', lambda s: s.startswith('FY'))):
        P = [(a, b, c) for a, b, c in pts if sel(a)]
        if len(P) < 3:
            continue
        # регрессия pv = a − D·dy
        n = len(P)
        mx = sum(b for _, b, _ in P) / n
        my = sum(c for _, _, c in P) / n
        sxx = sum((b - mx) ** 2 for _, b, _ in P)
        sxy = sum((b - mx) * (c - my) for _, b, c in P)
        D = -sxy / sxx
        a = my + D * mx
        res = [c - (a - D * b) for _, b, c in P]
        se = math.sqrt(sum(e * e for e in res) / (n - 2) / sxx) if n > 2 else None
        ss_tot = sum((c - my) ** 2 for _, _, c in P)
        r2 = 1 - sum(e * e for e in res) / ss_tot if ss_tot else None
        # через начало координат
        D0 = -sum(b * c for _, b, c in P) / sum(b * b for _, b, _ in P)
        DUR[(T, name)] = dict(n=n, D=round(D, 2), se=round(se, 2) if se else None, a_pct_q=round(100 * a, 3),
                              r2=round(r2, 2) if r2 is not None else None, D_through0=round(D0, 2))
        p(f'  тенор {T:>4} лет, {name}: {DUR[(T, name)]}')
with open(os.path.join(DATA, 'fvoci_duration_obs.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['period', 'start', 'end', 'oci_fv_change_after_tax', 'portfolio_start', 'tau',
                'y1_start', 'y1_end', 'y3_start', 'y3_end', 'y5_start', 'y5_end'])
    for o in obs:
        w.writerow([o['period'], o['start'], o['end'], o['oci'], round(o['port'], 1), o['tau']] +
                   [ylevel(o['start'], 1.0), ylevel(o['end'], 1.0), ylevel(o['start'], 3.0), ylevel(o['end'], 3.0),
                    ylevel(o['start'], 5.0), ylevel(o['end'], 5.0)])

# ------------------------------------------------------------------ 1b. Разрыв Н20.0 и FVOCI
p('=' * 100)
p('1b. Разрыв Н20.0 − Базель(общий) против непризнанной части фонда FVOCI (−R_неОФЗ/RWA)')
share_ofz = {'2023-12-31': 0.677, '2024-12-31': 0.705, '2025-12-31': 0.770}


def ofz_share_at(d):
    ks = sorted(share_ofz)
    if d <= ks[0]:
        return share_ofz[ks[0]]
    if d >= ks[-1]:
        return share_ofz[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= d <= b:
            return share_ofz[a]
    return None


pts = []
for r in rows:
    if r['fvoci_reserve'] is None or r['gap_total_adj'] is None:
        continue
    sh = ofz_share_at(r['date'])
    x = -(1 - sh) * r['fvoci_reserve'] / r['rwa_B'] * 100  # на сколько п.п. Н20.0 выше из-за непризнания
    pts.append((r['q'], x, r['gap_total_adj']))
    p(f"  {r['q']}: непризнанная часть {x:+.3f} п.п.; разрыв (скорр.) {r['gap_total_adj']:+.3f}; остаток {r['gap_total_adj'] - x:+.3f}")
if len(pts) > 2:
    n = len(pts)
    mx = sum(b for _, b, _ in pts) / n
    my = sum(c for _, _, c in pts) / n
    sxx = sum((b - mx) ** 2 for _, b, _ in pts)
    bcoef = sum((b - mx) * (c - my) for _, b, c in pts) / sxx
    p(f'  регрессия разрыв = a + b·x: b = {bcoef:.2f}, a = {my - bcoef * mx:+.3f}; n = {n}; '
      f'остаток после x: {stats([c - b for _, b, c in pts])}')

# ------------------------------------------------------------------ 5. Грубая проверка: связывает ли норматив
p('=' * 100)
p('5. Грубая проверка 2026–2029: выплата 50 %, требования по трём сценариям')
# Якорь 30.06.2026 [Ф]: Н20.0 15,1 % до вычета дивиденда 841; RWA 59 318; Базель CET1 7 450,5; BV обыкн. 8 387,2.
# Н1.1 банка 01.09.2026 10,115 % (после дивиденда, без прибыли 2026).
FLOOR20 = {  # regulation/schedule.yaml, центр «постепенно»
    'A': {2026: 10.0, 2027: 10.75, 2028: 12.0, 2029: 12.0, 2030: 12.0},
    'B': {2026: 10.0, 2027: 10.75, 2028: 12.5, 2029: 13.0, 2030: 13.5},
    'C': {2026: 10.0, 2027: 10.75, 2028: 12.5, 2029: 13.0, 2030: 13.5},
}
FLOOR20_FAST = {'A': FLOOR20['A'], 'B': {2026: 10.0, 2027: 10.75, 2028: 12.5, 2029: 13.5, 2030: 13.5},
                'C': {2026: 10.0, 2027: 10.75, 2028: 12.5, 2029: 14.0, 2030: 14.0}}
FLOOR201 = {  # пол Н20.1 группы (4,5 + надбавки)
    'A': {2026: 6.5, 2027: 7.25, 2028: 8.5, 2029: 8.5, 2030: 8.5},
    'B': {2026: 6.5, 2027: 7.25, 2028: 9.0, 2029: 9.5, 2030: 10.0},
    'C': {2026: 6.5, 2027: 7.25, 2028: 9.0, 2029: 9.5, 2030: 10.0},
}
FLOOR201_FAST = {'A': FLOOR201['A'], 'B': {2026: 6.5, 2027: 7.25, 2028: 9.0, 2029: 10.0, 2030: 10.0},
                 'C': {2026: 6.5, 2027: 7.25, 2028: 9.0, 2029: 10.5, 2030: 10.5}}
BUF = 1.3
TARGET = 13.3


def run_path(roe, rwa_g, payout, ded_g=None, t2_const=True, n20_gap=0.0):
    """Квартальный грубый проход 2026К3–2030К4 (МСФО-капитал группы → Н20.0 и Н1.1-прокси).
    BV_q = BV_(q−1) + ROE/4·BV_(q−1) − Div (в К2, ГОСА); Div за год Y = payout·ЧП_Y.
    CET1 = BV − DED (DED растёт с темпом ded_g, дивиденд бьёт капитал 1:1);
    K20 = CET1 + DPreg + AT1 + T2 (DPreg — объявленный, ещё не вычтенный из рег. капитала: конец К2);
    Н20.0 = K20/RWA + gap; Н1.1-прокси = K11·(BV + DPreg − E)/RWA, E — неаудированная прибыль (правило аудитов)."""
    ded_g = rwa_g if ded_g is None else ded_g
    bv, rwa, ded = 8387.2, 59318.1, 8387.2 - 7450.5
    at1, t2 = 150.0, 500.3
    ni = {(2026, 1): 508.5, (2026, 2): 511.6}  # ЧП акционерам 1К26, 2К26 [Ф: ifrs_quarterly]
    out = []
    qlist = [(y, k) for y in range(2026, 2031) for k in (1, 2, 3, 4) if (y, k) > (2026, 2)]
    for (y, k) in qlist:
        n_q = roe * bv / 4
        ni[(y, k)] = n_q
        div = 0.0
        if k == 2:
            prev = sum(ni[(y - 1, j)] for j in (1, 2, 3, 4)) if (y - 1, 1) in ni else None
            div = payout * prev if prev is not None else 0.0
        bv = bv + n_q - div
        rwa *= (1 + rwa_g) ** 0.25
        ded *= (1 + ded_g) ** 0.25
        dpreg = div if k == 2 else 0.0
        cet1 = bv - ded
        n20 = 100 * (cet1 + dpreg + at1 + t2) / rwa + n20_gap
        E = sum(ni[(y, j)] for j in {1: [1], 2: [1, 2], 3: [1, 2, 3], 4: [4]}[k])
        n11 = 100 * K11 * (bv + dpreg - E) / rwa + N11_GAP
        out.append(dict(q=f'{y}Q{k}', bv=round(bv, 0), ni_q=round(n_q, 0), div=round(div, 0), rwa=round(rwa, 0),
                        cet1_B=round(100 * cet1 / rwa, 2), n20=round(n20, 2), n11_proxy=round(n11, 2)))
    return out


K11 = st.mean([x['k11_implied'] for x in b2 if 'k11_implied' in x and x['q'] >= '2024Q1'])
N11_GAP = 0.0
p(f'K11 (центр 2024–2026, правило аудитов) = {K11:.4f}')
fc = []
for roe_name, roe in (('ROE 22 %', 0.22), ('ROE 20 %', 0.20), ('ROE 17 %', 0.17)):
    for g_name, rg in (('RWA +8 %', 0.08), ('RWA +11 %', 0.11)):
        path = run_path(roe, rg, 0.5)
        if g_name == 'RWA +8 %':
            p(f'-- {roe_name}, {g_name} (К2 — до рег. вычета дивиденда, К3 — после):')
            for rr in path:
                if rr['q'][5] in '23':
                    p('   ', rr)
        for rr in path:
            y, k = int(rr['q'][:4]), int(rr['q'][5])
            if k != 3:
                continue
            for sc in ('A', 'B', 'C'):
                for mode, F20, F201 in (('постепенно', FLOOR20, FLOOR201), ('сразу', FLOOR20_FAST, FLOOR201_FAST)):
                    if mode == 'сразу' and sc == 'A':
                        continue
                    req = max(TARGET, F20[sc][y] + BUF)
                    fc.append(dict(roe=roe_name, rwa_growth=g_name, check_q=rr['q'], dividend_for=y - 1, scenario=sc, phase=mode,
                                   n20_after_div=rr['n20'], floor20=F20[sc][y], req20=req,
                                   headroom20_to_req_pp=round(rr['n20'] - req, 2),
                                   headroom20_to_floor_pp=round(rr['n20'] - F20[sc][y], 2),
                                   headroom20_to_req_bn=round((rr['n20'] - req) / 100 * rr['rwa'], 0),
                                   n11_proxy=rr['n11_proxy'], floor201=F201[sc][y],
                                   headroom11_to_n201_floor_pp=round(rr['n11_proxy'] - F201[sc][y], 2),
                                   headroom11_to_solo_min_pp=round(rr['n11_proxy'] - 4.5, 2)))
with open(os.path.join(DATA, 'forward_check.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(fc[0]))
    w.writeheader()
    w.writerows(fc)
p('Запас на 30.09 (после вычета дивиденда): Н20.0 к требованию max(13,3; пол+1,3) и Н1.1-прокси к полу Н20.1, п.п.')
p('сценарий фаза        ROE      RWA     ' + '  '.join(f'{y}:Н20/Н1.1' for y in (2027, 2028, 2029, 2030)))
for sc in ('A', 'B', 'C'):
    for mode in ('постепенно', 'сразу'):
        for roe_name in ('ROE 22 %', 'ROE 20 %', 'ROE 17 %'):
            for g_name in ('RWA +8 %', 'RWA +11 %'):
                sel = {x['check_q']: x for x in fc if x['scenario'] == sc and x['phase'] == mode and x['roe'] == roe_name and x['rwa_growth'] == g_name}
                if not sel:
                    continue
                cells = []
                for y in (2027, 2028, 2029, 2030):
                    x = sel.get(f'{y}Q3')
                    cells.append(f"{x['headroom20_to_req_pp']:+5.2f}/{x['headroom11_to_n201_floor_pp']:+5.2f}")
                p(f'  {sc} {mode:10s} {roe_name} {g_name}  ' + '  '.join(cells))


# ------------------------------------------------------------------ 1c. Разрыв в форме ядра (MODEL §4.11)
p('=' * 100)
p('1c. Разрыв в форме ядра: N20 = [k20·(BV − (1−f)·R) + DP + AT1 + T2]/RWA + gap; k20 = CET1_B/BV квартала; f — доля ОФЗ')
g1c = []
for r in rows:
    if r['q'] < '2023Q4' or r['fvoci_reserve'] is None or r['cet1_B'] is None:
        continue
    k20 = r['cet1_B'] / r['bv_common']
    f = ofz_share_at(r['date'])
    t2 = (r['total_cap_B'] - r['cet1_B'] - r['at1']) if r['total_cap_B'] else None
    if t2 is None:
        # нет абсолютов общего капитала (2024К1–К3): T2 из коэффициентов (общий − T1) × RWA
        t2 = (r['basel_total_B'] - r['basel_t1_B']) / 100 * r['rwa_B']
    base = k20 * (r['bv_common'] - (1 - f) * r['fvoci_reserve'])
    k_new = base + r['div_pending'] + r['at1'] + t2            # дивиденд 1:1 (предложение)
    k_old = k20 * (r['bv_common'] + r['div_pending'] - (1 - f) * r['fvoci_reserve']) + r['at1'] + t2  # как в MODEL.md
    x = dict(q=r['q'], k20=round(k20, 4), f=f, t2=round(t2, 1), n20=r['n20_reg'],
             n20_fit_1to1=round(100 * k_new / r['rwa_B'], 3), gap_1to1=round(r['n20_reg'] - 100 * k_new / r['rwa_B'], 3),
             gap_model_md=round(r['n20_reg'] - 100 * k_old / r['rwa_B'], 3))
    g1c.append(x)
    p('  ', x)
for nm, sel in (('2024–2026', lambda x: x['q'] >= '2024Q1'), ('2025–2026', lambda x: x['q'] >= '2025Q1')):
    s1 = stats([x['gap_1to1'] for x in g1c if sel(x)])
    s2 = stats([x['gap_model_md'] for x in g1c if sel(x)])
    q2a = [x['gap_1to1'] for x in g1c if sel(x) and x['q'].endswith('Q2')]
    q2b = [x['gap_model_md'] for x in g1c if sel(x) and x['q'].endswith('Q2')]
    oth = [x['gap_1to1'] for x in g1c if sel(x) and not x['q'].endswith('Q2')]
    p(f'  {nm}: gap (дивиденд 1:1) {s1}; gap (форма MODEL.md) {s2}')
    p(f'     К2: 1:1 среднее {st.mean(q2a):+.3f}, MODEL.md {st.mean(q2b):+.3f}; прочие кварталы {st.mean(oth):+.3f}')
with open(os.path.join(DATA, 'gap_n20_model_form.csv'), 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=list(g1c[0]))
    w.writeheader()
    w.writerows(g1c)

# ------------------------------------------------------------------ 2d. Н1.1 в форме ядра (BVreg с поправкой FVOCI)
p('=' * 100)
p('2d. Н1.1: rec11 = Н1.1·RWA_B/(BVreg − E), BVreg = BV + DP − (1−f)·R, E — правило аудитов (форма MODEL.md §4.11)')
k11b = []
for x in b2:
    r = GAP[x['q']]
    if r['fvoci_reserve'] is None or x.get('unaudited_np_ifrs') is None:
        continue
    f = ofz_share_at(r['date'])
    bvreg = r['bv_common'] + r['div_pending'] - (1 - f) * r['fvoci_reserve']
    rec = x['n1_1_bank'] / 100 * r['rwa_B'] / (bvreg - x['unaudited_np_ifrs'])
    rec_ytd = x['n1_1_bank'] / 100 * r['rwa_B'] / (bvreg - x['np_ytd_ifrs'])
    k11b.append(dict(q=x['q'], bvreg=round(bvreg, 1), E=x['unaudited_np_ifrs'], rec11=round(rec, 4), rec11_ytd_rule=round(rec_ytd, 4)))
    p('  ', k11b[-1])
p('  rec11 (правило аудитов), 2023К4–2026К2:', stats([k['rec11'] for k in k11b]))
p('  rec11 (правило «ЧП с начала года», как в MODEL.md):', stats([k['rec11_ytd_rule'] for k in k11b]))
p('  rec11 2025К2–2026К2:', stats([k['rec11'] for k in k11b if k['q'] >= '2025Q2']))
REC11 = {k['q']: k['rec11'] for k in k11b}
with open(os.path.join(DATA, 'n11_recognition_model_form.csv'), 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=list(k11b[0]))
    w.writeheader()
    w.writerows(k11b)

# ------------------------------------------------------------------ 2c. Н20.0 группы против Н1.0 банка
p('=' * 100)
p('2c. Н20.0 группы − Н1.0 банка (форма 135 на 1-е число после квартала)')
for r in rows:
    de, fd = qdates(r['q'])
    mb = MON.get(fd)
    if mb and r['n20_reg']:
        p(f"  {r['q']}: Н20.0 {r['n20_reg']:.1f}  Н1.0 банка {mb['n1_0']:.2f}  разница {r['n20_reg'] - mb['n1_0']:+.2f}  | Н1.1 банка {mb['n1_1']:.2f}")

# ------------------------------------------------------------------ 5b. Предельная выплата в годы, где 50 % не проходит
p('=' * 100)
p('5b. Где требование Н20.0 связывает: предельная доля выплаты в первый связывающий год (линейно: 0,5 + запас/ЧП года)')
for sc, mode in (('A', 'постепенно'), ('B', 'постепенно'), ('B', 'сразу'), ('C', 'сразу')):
    for roe_name, roe in (('ROE 22 %', 0.22), ('ROE 20 %', 0.20), ('ROE 17 %', 0.17)):
        for g_name, rg in (('RWA +8 %', 0.08), ('RWA +11 %', 0.11)):
            path = {x['q']: x for x in run_path(roe, rg, 0.5)}
            sel = sorted([x for x in fc if x['scenario'] == sc and x['phase'] == mode and x['roe'] == roe_name and x['rwa_growth'] == g_name],
                         key=lambda x: x['check_q'])
            first = next((x for x in sel if x['headroom20_to_req_pp'] < 0), None)
            if first is None:
                continue
            y = int(first['check_q'][:4])
            ni_prev = sum(path[f'{y - 1}Q{k}']['ni_q'] for k in (1, 2, 3, 4) if f'{y - 1}Q{k}' in path)
            if y - 1 == 2026:
                ni_prev += 508.5 + 511.6
            pmax = 0.5 + first['headroom20_to_req_bn'] / ni_prev
            p(f"  {sc} {mode:10s} {roe_name} {g_name}: первый год {y} (дивиденд за {y - 1}), запас {first['headroom20_to_req_bn']:+.0f} млрд; "
              f"ЧП {ni_prev:.0f}; предельная выплата ≈ {100 * max(pmax, 0):.0f} %")

open(os.path.join(DATA, 'out.txt'), 'w', encoding='utf-8', newline=NL).write('\n'.join(OUT) + '\n')
