"""Книги ЧПД: ставки с лагом, доля текущих счетов, передача ставки (М§4.3–§4.5).

Ставка актива: y_q = y_(q−1) + ρ (ref_W,q + s̃_q − y_(q−1)), s̃ = s(q) − φ_A·[b ∈ Φ_A]·(ref_W − ref_H);
пассива: c_q = c_(q−1) + ρ (β ref_W,q + s(q) + φ_L·[b ∈ Φ_L]·X_W,q − c_(q−1)), X_W,q — разность
опорных ставок кредитных книг Φ_A мира W и мира H, взвешенная их долями ω_b на якоре.
Проценты — простые по дням (act/365). Передача ставки — явный ключ книги: из ЧПМ сквозь
цикл (A-N2, упр. базис → движок мостом) и реализованной передачи M − N (A-N3) замкнуто
выводятся сдвиг спредов σ0 и сжатие φ. Сдвиг Δ0 делит `nii.sigma0_split`: доля —
на доходности активов с `lt_shift` (σ0_A), остаток — на стоимость пассивов с `lt_shift`
(σ0_L, со знаком минус: оба слагаемых двигают ЧПМ в одну сторону). Сжатие φ делит
`nii.phi_split`: доля — в спредах активов с `phi` (φ_A), остаток — добавкой к стоимости
пассивов с `phi` (φ_L); объём сжатия мира φ × C_W от доли не зависит, поэтому Nss, T_real,
T(W) и парные передачи считаются по φ целиком. В пути сдвиг прибавляется к спредам во
всех кварталах с года `nii.sigma0_from` (М§4.4), в стационаре — к LT. Эффективный
LT-спред кредитной книги к опоре мира s_eff,b,W — вход гейта `lt_spread_floor`; кредитная
маржа стационара мира m_W — диагностика знака объёмных эффектов. Парные передачи
соседних по key^LT миров — диагностика и гейт (М§4.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

from model.book import LIQUIDITY, SECURITIES, anchor_facts, book_roles
from model.book_schema import BookError
from model.credit import Bridge, cor_to_engine
from model.paths import Trajectory, get_path
from model.timeline import Timeline, parse_period
from model.worlds import BASE_WORLD, MARKET_WORLD, check_family_worlds

if TYPE_CHECKING:
    from model.book import Book, Facts

# Режим, в котором откалиброваны уровни сквозь цикл: передача по клеткам, флаг ni_jump, кредитная маржа (М§4.5).
REFERENCE_REGIME = "norm"


def current_share_target(key: float, c_ref: float, psi: float, key_ref: float,
                         bounds: tuple[float, float]) -> float:
    """c*(ключевая) = clip(c_ref − ψ (key − key_ref), bounds) (М§4.3)."""
    lo, hi = bounds
    return min(hi, max(lo, c_ref - psi * (key - key_ref)))


def spread_paths(book: "Book", timeline: Timeline, sigma0: float, sigma0_liab: float = 0.0
                 ) -> dict[str, tuple[float, ...]]:
    """s_b(q) = path_value(spread, q) + σ_b × [lt_shift_b] × [Y(q) ≥ nii.sigma0_from], q = 0…Q (М§4.4);
    σ_b = +σ0_A у активов, −σ0_L у пассивов."""
    from_year = int(book.get("nii.sigma0_from"))
    out = {}
    for name, spec in book.get("nii.books").items():
        t = Trajectory(spec["spread"])
        sigma = sigma0 if spec["side"] == "asset" else -sigma0_liab
        shift = sigma if spec.get("lt_shift") else 0.0
        row = []
        for q in range(timeline.Q + 1):
            y, h = parse_period(timeline.period(q))
            row.append(t.value(y, h) + (shift if y >= from_year else 0.0))
        out[name] = tuple(row)
    return out


def pair_key(w1: str, w2: str) -> str:
    """Ключ парной передачи `<W1>_<W2>` (derived.nii.T_pairs, М прил. A)."""
    return f"{w1}_{w2}"


@dataclass(frozen=True)
class Transmission:
    sigma0: float                          # σ0_A — сдвиг спредов активов с lt_shift
    phi: float                             # φ — сжатие целиком (от nii.phi_split не зависит)
    target_eng: float
    t_real: float                          # реализованная M − N (= T*, инвариант)
    t_local: Mapping[str, float]           # локальная T(W) — диагностика
    roe_equiv: float
    t_target: float = 0.0                  # T* книги (nii.transmission.target)
    nss: Mapping[str, float] = field(default_factory=dict)   # Nss(W, 0) при решённых σ0, φ
    d: float = 0.0                         # знаменатель D замкнутого решения
    reference_world: str = ""
    pairs: Mapping[str, float | None] = field(default_factory=dict)   # "<W1>_<W2>" → T_pair соседних миров
    pairs_roe: Mapping[str, float | None] = field(default_factory=dict)   # то же в п.п. ROE на 1 п.п. ключевой
    key_lt: Mapping[str, float] = field(default_factory=dict)          # key_W^LT (ключевая мира в last_period)
    order: tuple[str, ...] = ()                                         # миры по возрастанию key^LT
    roe_factor: float = 0.0                                              # IEA_0/BV_0 × (1 − τ_eff,L)
    sigma0_liab: float = 0.0               # σ0_L — сдвиг спредов пассивов с lt_shift (0 при split = 1)
    split: float = 1.0                     # nii.sigma0_split: доля сдвига Δ0 на активах
    delta0: float = 0.0                    # Δ0 — нужный сдвиг стационарной ЧПМ мира H
    lt_spread: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    # кредитная книга → мир → эффективный LT-спред к опоре s_eff,b,W (М§4.5)
    phi_assets: float = 0.0                # φ_A = phi_split × φ — сжатие спредов кредитных книг с phi
    phi_liab: float = 0.0                  # φ_L — добавка к стоимости пассивов с phi на единицу X_W,q (0 при phi_split = 1)
    phi_split: float = 1.0                 # nii.phi_split: доля сжатия на активах
    phi_weights: Mapping[str, float] = field(default_factory=dict)   # ω_b — доли книг Φ_A в разности X_W,q (М§4.4)
    loan_margin: Mapping[str, float] = field(default_factory=dict)   # мир → кредитная маржа стационара m_W (М§4.5)
    x_lt: Mapping[str, float] = field(default_factory=dict)          # мир → X_W в стационаре (last_period)


class _Stationary:
    """Стационарный ЧПМ Nss(W, δ) на структуре якоря (М§4.5)."""

    def __init__(self, book: "Book", facts: "Facts"):
        books = book.get("nii.books")
        self.books = books
        self.roles = book_roles(books)
        af = anchor_facts(facts, books)
        iea = sum(af.balances[b] for b in self.roles.assets)
        self.iea0 = iea
        self.bv0 = af.bv
        self.w = {b: af.balances[b] / iea for b in self.roles.names}
        self.l_r = sum(self.w[b] for b in self.roles.retail)
        last = str(book.get("meta.last_period"))
        self.last = parse_period(last)
        self.ref_lt: dict[str, dict[str, float]] = {}
        for world in book.get("worlds.ids"):
            rates = {}
            for ref in ("key", "ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y"):
                path = f"worlds.{world}.key_rate" if ref == "key" else f"worlds.{world}.{ref}"
                rates[ref] = Trajectory(book.get(path)).value(*self.last)
            self.ref_lt[world] = rates
        self.s_lt = {b: Trajectory(books[b]["spread"]).value(*self.last) for b in self.roles.names}
        rcs = book.get("nii.retail_current_share")
        self.c_ref, self.psi = float(rcs["c_ref"]), float(rcs["psi"])
        self.key_ref, self.bounds = float(rcs["key_ref"]), tuple(rcs["bounds"])
        self.dia = float(book.get("nii.dia_rate"))

    def nss(self, world: str, delta: float, sigma0: float, phi: float, ref_world: str,
            sigma0_liab: float = 0.0) -> float:
        books, w = self.books, self.w
        sl = {b: (sigma0_liab if books[b].get("lt_shift") else 0.0) for b in self.roles.liabilities}
        rw, rh = self.ref_lt[world], self.ref_lt[ref_world]
        total = 0.0
        for b in self.roles.assets:
            spec = books[b]
            ref = rw[spec["ref"]] + delta
            y = ref + self.s_lt[b] + (sigma0 if spec.get("lt_shift") else 0.0)
            if spec.get("phi"):
                y -= phi * (ref - rh[spec["ref"]])
            total += w[b] * y
        for b in self.roles.liabilities:
            if b in self.roles.retail:
                continue
            spec = books[b]
            total -= w[b] * (float(spec["beta"]) * (rw[spec["ref"]] + delta) + self.s_lt[b] - sl[b])
        key = rw["key"] + delta
        c = current_share_target(key, self.c_ref, self.psi, self.key_ref, self.bounds)
        rc, rt = self.roles.retail
        cost_rc = float(books[rc]["beta"]) * (rw[books[rc]["ref"]] + delta) + self.s_lt[rc] - sl[rc]
        cost_rt = float(books[rt]["beta"]) * (rw[books[rt]["ref"]] + delta) + self.s_lt[rt] - sl[rt]
        total -= self.l_r * (c * cost_rc + (1 - c) * cost_rt)
        total -= self.dia * self.l_r
        return total

    def liability_weight(self, world: str) -> float:
        """Σ l_b,W пассивов с lt_shift в стационаре мира: у средств ФЛ — l_R × c*(key^LT) и l_R × (1 − c*)."""
        key = self.ref_lt[world]["key"]
        c = current_share_target(key, self.c_ref, self.psi, self.key_ref, self.bounds)
        rc, rt = self.roles.retail
        total = 0.0
        for b in self.roles.liabilities:
            if not self.books[b].get("lt_shift"):
                continue
            total += self.l_r * c if b == rc else self.l_r * (1 - c) if b == rt else self.w[b]
        return total

    def phi_assets(self) -> tuple[str, ...]:
        """Φ_A — активы с `phi` (кредитные книги, кроме льготной ипотеки)."""
        return tuple(b for b in self.roles.assets if self.books[b].get("phi"))

    def phi_liabilities(self) -> tuple[str, ...]:
        """Φ_L — пассивы с `phi` (средства клиентов)."""
        return tuple(b for b in self.roles.liabilities if self.books[b].get("phi"))

    def compression(self, world: str, delta: float, ref_world: str) -> float:
        """C_W(δ) = Σ_{b ∈ Φ_A} a_b × (ref_b,W + δ − ref_b,H) — объём сжатия мира на единицу φ, в долях IEA."""
        rw, rh = self.ref_lt[world], self.ref_lt[ref_world]
        return sum(self.w[b] * (rw[self.books[b]["ref"]] + delta - rh[self.books[b]["ref"]])
                   for b in self.phi_assets())

    def phi_asset_weight(self) -> float:
        """A = Σ_{b ∈ Φ_A} a_b."""
        return sum(self.w[b] for b in self.phi_assets())

    def phi_liability_weight(self) -> float:
        """L = Σ_{b ∈ Φ_L} l_b: у книг средств ФЛ флаг одинаков, поэтому их вес — l_R и от мира не зависит."""
        rc, rt = self.roles.retail
        if bool(self.books[rc].get("phi")) != bool(self.books[rt].get("phi")):
            raise BookError(f"nii.books.{rc}.phi ≠ nii.books.{rt}.phi: у обеих книг средств ФЛ флаг одинаков (М§4.5)")
        return sum(self.w[b] for b in self.phi_liabilities())

    def lt_spreads(self, sigma0: float, phi_assets: float, ref_world: str) -> dict[str, dict[str, float]]:
        """s_eff,b,W = s_b^LT + σ0_A·[lt_shift_b] − φ_A·[b ∈ Φ_A]·(ref_b,W^LT − ref_b,H^LT) кредитных книг (М§4.5)."""
        out: dict[str, dict[str, float]] = {}
        for b in self.roles.loans:
            spec = self.books[b]
            row = {}
            for world, rates in self.ref_lt.items():
                s = self.s_lt[b] + (sigma0 if spec.get("lt_shift") else 0.0)
                if spec.get("phi"):
                    s -= phi_assets * (rates[spec["ref"]] - self.ref_lt[ref_world][spec["ref"]])
                row[world] = s
            out[b] = row
        return out

    def asset_rate(self, b: str, world: str, sigma0: float, phi_assets: float, ref_world: str) -> float:
        """Ставка актива в стационаре мира: ref^LT + s^LT + σ0_A·[lt_shift] − φ_A·[phi]·(ref_W^LT − ref_H^LT)."""
        spec = self.books[b]
        ref = self.ref_lt[world][spec["ref"]]
        y = ref + self.s_lt[b] + (sigma0 if spec.get("lt_shift") else 0.0)
        if spec.get("phi"):
            y -= phi_assets * (ref - self.ref_lt[ref_world][spec["ref"]])
        return y


def solve_transmission(book: "Book", facts: "Facts", bridge: Bridge) -> Transmission:
    """σ0_A, σ0_L и φ замкнуто по A-N2, `nii.sigma0_split` и A-N3 (реализованная передача M − N);
    BookError, если передача не определена."""
    check_family_worlds(book)
    st = _Stationary(book, facts)
    ref_world = str(book.get("nii.transmission.reference_world"))
    target_eng = bridge.to_engine_nim(float(book.get("nii.nim_lt_target_mgmt")))
    t_star = float(book.get("nii.transmission.target"))
    split = float(book.get("nii.sigma0_split"))
    if not 0.0 <= split <= 1.0:
        raise BookError(f"nii.sigma0_split = {split}: доля от 0 до 1 (М§4.5)")
    delta0 = target_eng - st.nss(ref_world, 0.0, 0.0, 0.0, ref_world)
    lt_weight = sum(st.w[b] for b in st.roles.assets if st.books[b].get("lt_shift"))
    liab_weight = st.liability_weight(ref_world)
    if split > 0 and lt_weight == 0:
        raise BookError("nii.sigma0_split > 0, а активов с lt_shift нет — σ0 активов не определён (М§4.5)")
    if split < 1 and liab_weight == 0:
        raise BookError("nii.sigma0_split < 1, а пассивов с lt_shift нет — σ0 пассивов не определён (М§4.5)")
    sigma0 = split * delta0 / lt_weight if split > 0 else 0.0
    sigma0_l = (1 - split) * delta0 / liab_weight if split < 1 else 0.0
    m, n = MARKET_WORLD, BASE_WORLD
    dkey = st.ref_lt[m]["key"] - st.ref_lt[n]["key"]
    if dkey == 0:
        raise BookError("ключевая миров M и N в last_period совпадает — передача не определена (М§4.5)")
    d = sum(st.w[b] * (st.ref_lt[m][st.books[b]["ref"]] - st.ref_lt[n][st.books[b]["ref"]])
            for b in st.roles.assets if st.books[b].get("phi")) / dkey
    if d == 0:
        raise BookError("D = 0: у книг с phi опоры миров M и N совпадают — φ не определено (М§4.5)")
    nss_of = lambda w, dlt, ph: st.nss(w, dlt, sigma0, ph, ref_world, sigma0_l)  # noqa: E731
    t0 = (nss_of(m, 0.0, 0.0) - nss_of(n, 0.0, 0.0)) / dkey
    phi = (t0 - t_star) / d
    t_real = (nss_of(m, 0.0, phi) - nss_of(n, 0.0, phi)) / dkey
    p_split = float(book.get("nii.phi_split"))
    if not 0.0 <= p_split <= 1.0:
        raise BookError(f"nii.phi_split = {p_split}: доля от 0 до 1 (М§4.5)")
    a_phi, l_phi = st.phi_asset_weight(), st.phi_liability_weight()
    if p_split < 1 and l_phi == 0:
        raise BookError("nii.phi_split < 1, а пассивов с phi нет — добавка к стоимости пассивов не определена (М§4.5)")
    phi_a = p_split * phi
    phi_l = (1 - p_split) * phi * a_phi / l_phi if p_split < 1 else 0.0
    omega = {b: st.w[b] / a_phi for b in st.phi_assets()}
    h = float(book.get("nii.transmission.fd_step"))
    t_local = {w: (nss_of(w, h, phi) - nss_of(w, -h, phi)) / (2 * h) for w in book.get("worlds.ids")}
    last_year = st.last[0]
    tau_eff = (Trajectory(book.get("tax.statutory")).year_value(last_year)
               + float(book.get("tax.effective_gap")))
    factor = st.iea0 / st.bv0 * (1 - tau_eff)
    roe_equiv = t_star * factor
    ids = tuple(book.get("worlds.ids"))
    nss = {w: nss_of(w, 0.0, phi) for w in ids}
    key_lt = {w: st.ref_lt[w]["key"] for w in ids}
    order = tuple(sorted(ids, key=lambda w: (key_lt[w], ids.index(w))))
    pairs: dict[str, float | None] = {}
    for w1, w2 in zip(order, order[1:]):
        dk = key_lt[w2] - key_lt[w1]
        pairs[pair_key(w1, w2)] = None if dk == 0 else (nss[w2] - nss[w1]) / dk
    pairs_roe = {k: None if v is None else v * factor for k, v in pairs.items()}
    return Transmission(sigma0=sigma0, phi=phi, target_eng=target_eng, t_real=t_real,
                        t_local=t_local, roe_equiv=roe_equiv, t_target=t_star, nss=nss, d=d,
                        reference_world=ref_world, pairs=pairs, pairs_roe=pairs_roe, key_lt=key_lt,
                        order=order, roe_factor=factor, sigma0_liab=sigma0_l, split=split, delta0=delta0,
                        lt_spread=st.lt_spreads(sigma0, phi_a, ref_world), phi_assets=phi_a, phi_liab=phi_l,
                        phi_split=p_split, phi_weights=omega,
                        loan_margin=_loan_margin(book, st, bridge, sigma0, phi_a, ref_world),
                        x_lt={w: st.compression(w, 0.0, ref_world) / a_phi for w in ids})


def _loan_margin(book: "Book", st: _Stationary, bridge: Bridge, sigma0: float, phi_assets: float,
                 ref_world: str) -> dict[str, float]:
    """Кредитная маржа стационара мира m_W (М§4.5): доходность кредитного портфеля − CoR режима `norm` с
    κ-добавкой мира − смесь балансирующих активов. Отрицательная — рост кредита в этом мире разрушает стоимость."""
    loans = st.roles.loans
    total = sum(st.w[b] for b in loans)
    cor_lt = cor_to_engine(book, bridge, Trajectory(book.get(f"regimes.{REFERENCE_REGIME}.cor")).value(*st.last))
    kappa = float(book.get("credit.kappa"))
    sec = float(book.get("volumes.securities_share_of_liquid"))
    real = {w: Trajectory(book.get(f"worlds.{w}.real_key")).value(*st.last) for w in book.get("worlds.ids")}
    out = {}
    for w in book.get("worlds.ids"):
        rate = lambda b: st.asset_rate(b, w, sigma0, phi_assets, ref_world)  # noqa: E731
        loan_yield = sum(st.w[b] * rate(b) for b in loans) / total
        cor = cor_lt + kappa * max(0.0, real[w] - real[BASE_WORLD])
        balancing = sec * rate(SECURITIES) + (1 - sec) * rate(LIQUIDITY)
        out[w] = loan_yield - cor - balancing
    return out


def nss_value(book: "Book", facts: "Facts", world: str, delta: float, sigma0: float, phi: float,
              sigma0_liab: float = 0.0) -> float:
    """Nss(W, δ) — для тестов и диагностик."""
    st = _Stationary(book, facts)
    return st.nss(world, delta, sigma0, phi, str(get_path(book.data, "nii.transmission.reference_world")),
                  sigma0_liab)
