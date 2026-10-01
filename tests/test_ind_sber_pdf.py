"""Статические PDF: разбор таблиц релиза РСБУ по распознаванию, МСФО по текстовому слою, needs_manual."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from indicators import config, http, news, sber_pdf
from indicators.sources import Context
from indicators.store import Store
from tests.support_ind import FIX, use_fixtures


def _lines(name):
    return sber_pdf.words_tsv_lines((FIX / "sber_pdf" / name).read_text(encoding="utf-8"))


PNL = {"nii_ytd": 2441.3, "nii_m": 325.2, "fee_ytd": 475.9, "fee_m": 59.8, "prov_ytd": -532.3,
       "prov_m": -107.8, "opex_ytd": -806.7, "opex_m": -104.0, "pretax_ytd": 1798.4, "pretax_m": 233.4,
       "np_ytd": 1332.7, "np_m": 169.1}


@pytest.mark.tact
def test_ras_pnl_table_from_ocr_lines():
    got = sber_pdf.parse_ras_table(_lines("ras_2026M08_p3.words.tsv"))
    assert got == pytest.approx(PNL)


@pytest.mark.tact
def test_ras_capital_table_from_ocr_lines():
    got = sber_pdf.parse_ras_capital(_lines("ras_2026M08_p4.words.tsv"))
    assert got == pytest.approx({"cap_base": 6247.0, "cap_main": 6397.0, "cap_total": 8105.0,
                                 "n1_1": 0.101, "n1_2": 0.104, "n1_0": 0.131})


@pytest.mark.tact
def test_ocr_token_repairs():
    assert sber_pdf.to_amount("2 441,3") == 2441.3
    assert sber_pdf.to_amount("-532,3") == -532.3
    assert sber_pdf.to_amount("1 0644") == 1064.4          # потерянная запятая
    assert sber_pdf.to_amount("24,896") is None             # это проценты («%» прочитан как «96»)
    assert sber_pdf.to_percent("10,196") == 0.101 and sber_pdf.to_percent("13,10/0") == 0.131


@pytest.mark.tact
def test_tesseract_tsv_lines():
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t146\t606\t80\t23\t95\tЧистые\n"
           "5\t1\t1\t1\t1\t2\t230\t606\t120\t23\t95\tпроцентные\n"
           "5\t1\t2\t1\t1\t1\t922\t606\t40\t22\t90\t2\n"
           "5\t1\t2\t1\t1\t2\t966\t606\t60\t22\t90\t441,3\n")
    lines = sber_pdf.tsv_lines(tsv)
    assert [ln["text"] for ln in lines] == ["Чистые процентные", "2 441,3"]


@pytest.mark.tact
def test_ifrs_income_statement_from_text_layer():
    page = (FIX / "sber_pdf" / "ifrs_2026H1_p6.txt").read_text(encoding="utf-8")
    got = sber_pdf.parse_ifrs_statements(["", "", "", "", "", page])
    assert got["page"] == 6
    assert got["nii_period"] == 2051.1 and got["nii_quarter"] == 1066.8
    assert got["llp_debt_fa_period"] == -332.7 and got["llp_debt_fa_quarter"] == -171.5
    assert got["pbt_quarter"] == 679.1 and got["tax_quarter"] == -167.9
    assert got["ni_quarter"] == 511.2 and got["ni_sh_period"] == 1020.1 and got["ni_sh_quarter"] == 511.6


@pytest.mark.tact
def test_pdf_without_text_and_without_tesseract_needs_manual(monkeypatch, tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    path = tmp_path / "blank.pdf"
    doc.save(str(path))
    monkeypatch.setattr(sber_pdf.shutil, "which", lambda name: None)
    out = sber_pdf.analyse(path.read_bytes(), "ras_release")
    assert out["status"] == "needs_manual" and "tesseract" in out["detail"]


@pytest.mark.tact
def test_classify_and_month_from_url(monkeypatch, tmp_path):
    use_fixtures(monkeypatch, tmp_path)
    cfg = config.sources()["sber_pdf"]
    base = "https://www.sberbank.com/common/img/uploaded/files/info/"
    assert sber_pdf.classify(base + "2026_rsbu/year/ras_for_8months2026_x0x0x0x0_ru.pdf", cfg["kinds"]) == "ras_release"
    assert sber_pdf.classify(base + "reporting_q2_3lgps1xr_2025.pdf", cfg["kinds"]) == "ifrs_statements"
    assert sber_pdf.classify(base + "results_q1_a3s5t86k_2026.pdf", cfg["kinds"]) == "ifrs_press"
    pats = cfg["ras_month_patterns"]
    assert sber_pdf.ras_month_from_url(base + "2026_rsbu/year/ras_for_8months2026_x0x0x0x0_ru.pdf", pats) == "2026M08"
    assert sber_pdf.ras_month_from_url(base + "2023_rsbu/year/_february_2023_.pdf", pats) == "2023M02"


@pytest.mark.tact
def test_documents_come_only_over_https_from_the_issuer_hosts(monkeypatch, tmp_path):
    """№ 50: шаблон ссылок — только https; документ не с хоста эмитента (и после редиректа) не принимается."""
    state = use_fixtures(monkeypatch, tmp_path)
    cfg = config.sources()
    pats = cfg["news"]["pdf_patterns"]
    assert all(p.startswith("https://") for p in pats)
    path = "://www.sberbank.com/common/img/uploaded/files/info/2026_rsbu/year/ras_for_8months2026_x0x0x0x0_ru.pdf"
    assert news.pdf_links(f'<a href="https{path}">релиз</a>', pats) == ["https" + path]
    assert news.pdf_links(f'<a href="http{path}">релиз</a>', pats) == []
    store = Store(state)
    ctx = Context(store=store, today=date(2026, 9, 9), company=config.company(), cfg=cfg)

    def getter(landed):
        def get(url, *, sink=None, name=None, hosts=None, **_kw):
            assert hosts == cfg["sber_pdf"]["hosts"]
            return http.Response(url=url, status=200, body=b"%PDF-1.7", fetched_at="2026-09-09T07:00:00+00:00",
                                 final_url=landed)
        return get

    resp, new = sber_pdf.download(ctx, "https" + path, getter=getter(None))
    assert new and resp.landed == "https" + path
    for bad_url, landed in (("http" + path, None), ("https://evil.example/a.pdf", None),
                            ("https" + path, "https://evil.example/a.pdf"), ("https" + path, "http" + path)):
        with pytest.raises(http.FetchError) as err:
            sber_pdf.download(ctx, bad_url, getter=getter(landed))
        assert not http.retryable(err.value)
    assert list(store.read_state(sber_pdf.INDEX_FILE)) == ["https" + path]      # в индексе — только принятый
    # сборщик: чужая ссылка в состоянии новостей — деградация без повтора, не падение
    store.write_state("news/items.json", {"items": {}, "pdf": {"http" + path: {"found_in": "x"}}})
    res = sber_pdf.collect(Context(store=store, today=date(2026, 9, 9), company=config.company(), cfg=cfg,
                                   getter=getter(None)))
    assert res.status == "degraded" and not res.retry


@pytest.mark.archive
def test_real_ifrs_pdf_text_layer():
    root = os.environ.get("BANK_HANDOFF_DIR")
    if not root:
        pytest.skip("нужна папка передачи (BANK_HANDOFF_DIR)")
    body = (Path(root) / "research/primary/2026H1_ifrs_interim_statements.pdf").read_bytes()
    out = sber_pdf.analyse(body, "ifrs_statements")
    assert out["status"] == "ok" and out["numbers"]["ni_sh_quarter"] == 511.6


# ------------------------------------------------------------------ № 90: отказы распознавания

RAS_URL = "https://www.sberbank.com/common/img/uploaded/files/info/2026_rsbu/year/ras_for_8months2026_x0x0x0x0_ru.pdf"


def _blank_pdf(tmp_path, pages=1) -> bytes:
    """PDF без текстового слоя (пустые страницы) — как релиз РСБУ с текстом в кривых."""
    pdfium = pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    path = tmp_path / "blank.pdf"
    doc.save(str(path))
    return path.read_bytes()


def _tesseract_tsv(name: str) -> str:
    """Снимок распознавания этапа 1 («строка x y w h слово») в формате TSV tesseract (уровень слов)."""
    out = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for raw in (FIX / "sber_pdf" / name).read_text(encoding="utf-8").splitlines():
        if raw.strip():
            line, x, y, w, h, text = raw.split("\t", 5)
            out.append(f"5\t1\t1\t1\t{line}\t1\t{x}\t{y}\t{w}\t{h}\t95\t{text}")
    return "\n".join(out) + "\n"


def _fake_tesseract(monkeypatch, run):
    """tesseract «установлен», а вместо процесса — `run(args, **kw)`; возвращает список вызовов."""
    calls: list[dict] = []

    def fake(args, **kw):
        calls.append({"args": list(args), **kw})
        return run(args, **kw)

    monkeypatch.setattr(sber_pdf.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(sber_pdf.subprocess, "run", fake)
    return calls


def _done(stdout="", returncode=0, stderr=""):
    return sber_pdf.subprocess.CompletedProcess(["tesseract"], returncode, stdout, stderr)


def _hang(args, **kw):
    raise sber_pdf.subprocess.TimeoutExpired(args, kw.get("timeout"))


def _no_binary(args, **kw):
    raise FileNotFoundError(2, "No such file or directory", "tesseract")


BROKEN_TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\ttext\n"
              "5\t1\t1\t1\t1\tсемь\t1\t1\t1\tслово\n")
OCR_FAILURES = {
    "зависание": (_hang, "не ответил"),
    "не запустился": (_no_binary, "не запустился"),
    "код возврата": (lambda a, **k: _done(returncode=1, stderr="Error opening data file rus.traineddata\n"),
                     "код 1"),
    "мусор в выводе": (lambda a, **k: _done(BROKEN_TSV), "не разобран"),
    "пустой вывод": (lambda a, **k: _done(""), "не найдена таблица"),
}


@pytest.mark.tact
@pytest.mark.parametrize("case", sorted(OCR_FAILURES))
def test_a_failed_ocr_is_needs_manual_with_a_reason(monkeypatch, tmp_path, case):
    """№ 90: зависший, упавший, не найденный tesseract и мусор в его выводе — `needs_manual` с причиной."""
    run, word = OCR_FAILURES[case]
    calls = _fake_tesseract(monkeypatch, run)
    out = sber_pdf.analyse(_blank_pdf(tmp_path), "ras_release")
    assert out["status"] == "needs_manual" and out["numbers"] == {} and word in out["detail"]
    assert len(calls) == 1 and 0 < calls[0]["timeout"] <= sber_pdf.OCR_PAGE_TIMEOUT_S


@pytest.mark.tact
def test_the_ocr_stops_at_the_first_hang_and_within_the_budget(monkeypatch, tmp_path):
    """№ 90: зависание на первой странице — дальше не идём; весь документ — в бюджете времени."""
    body = _blank_pdf(tmp_path, pages=4)
    calls = _fake_tesseract(monkeypatch, _hang)
    with pytest.raises(sber_pdf.NeedsManual, match="стр. 1 из 4"):
        sber_pdf.ocr_pages(body)
    assert len(calls) == 1
    clock = iter(range(0, 10_000, 50))                              # между обращениями к часам — 50 с
    monkeypatch.setattr(sber_pdf.time, "monotonic", lambda: float(next(clock)))
    calls = _fake_tesseract(monkeypatch, lambda a, **k: _done(_tesseract_tsv("ras_2026M08_p3.words.tsv")))
    with pytest.raises(sber_pdf.NeedsManual, match="не уложилось в 120 с"):
        sber_pdf.ocr_pages(body, page_timeout=60.0, budget=120.0)
    assert [c["timeout"] for c in calls] == [60.0, 20.0]            # остаток бюджета режет потолок страницы


@pytest.mark.tact
def test_a_pdf_that_does_not_render_and_a_foreign_kind_need_manual(monkeypatch, tmp_path):
    """№ 90: ошибка pdfium — `needs_manual`; документ не релиза РСБУ без текста tesseract не занимает."""
    pytest.importorskip("pypdfium2")
    calls = _fake_tesseract(monkeypatch, _hang)
    out = sber_pdf.analyse(_blank_pdf(tmp_path), "ifrs_press")
    assert out["status"] == "needs_manual" and "только релиз РСБУ" in out["detail"] and not calls
    monkeypatch.setattr(sber_pdf, "text_pages", lambda body: [""])
    out = sber_pdf.analyse(b"%PDF-1.7 not a document", "ras_release")
    assert out["status"] == "needs_manual" and "не открывается" in out["detail"] and not calls


def _pdf_ctx(monkeypatch, tmp_path, body):
    state = use_fixtures(monkeypatch, tmp_path)
    store = Store(state)
    store.write_state("news/items.json", {"items": {}, "pdf": {RAS_URL: {"found_in": "x"}}})

    def getter(url, **_kw):
        return http.Response(url=url, status=200, body=body, fetched_at="2026-09-09T07:00:00+00:00")

    def ctx(day):
        return Context(store=store, today=day, company=config.company(), cfg=config.sources(), getter=getter)

    return store, ctx


@pytest.mark.tact
def test_a_hung_tesseract_leaves_a_candidate_and_does_not_break_the_collector(monkeypatch, tmp_path):
    """№ 90: зависание распознавания в сборщике — деградация без повтора и запись кандидата: индекс
    отметил файл проверенным, и без записи PDF не разобрался бы никогда."""
    store, ctx = _pdf_ctx(monkeypatch, tmp_path, _blank_pdf(tmp_path))
    calls = _fake_tesseract(monkeypatch, _hang)
    res = sber_pdf.collect(ctx(date(2026, 9, 9)))
    assert res.status == "degraded" and not res.retry and "не ответил" in res.reasons[0]
    # П§0.2: причину печатает витрина — документ назван словами, без имени файла и ключа статуса
    assert res.reasons[0].startswith("PDF релиза РСБУ за 2026M08 не распознан — tesseract не ответил")
    assert ".pdf" not in res.reasons[0] and "needs_manual" not in res.reasons[0]
    (key, cand), = store.read_state(sber_pdf.CANDIDATES_FILE).items()
    assert key.startswith(RAS_URL + "#") and cand["status"] == "needs_manual" and cand["month"] == "2026M08"
    assert cand["kind"] == "ras_release" and "не ответил" in cand["detail"] and len(cand["sha256"]) == 64
    assert store.read_state(sber_pdf.INDEX_FILE)[RAS_URL]["checked"]
    # следующий такт: файл проверен — ни скачивания, ни распознавания, ни новой причины
    res = sber_pdf.collect(ctx(date(2026, 9, 10)))
    assert res.status == "ok" and len(calls) == 1
    # разбор упал непредвиденно — запись всё равно есть, сборщик жив
    store.write_state(sber_pdf.INDEX_FILE, {})
    store.write_state(sber_pdf.CANDIDATES_FILE, {})
    monkeypatch.setattr(sber_pdf, "analyse", lambda body, kind: 1 / 0)
    res = sber_pdf.collect(ctx(date(2026, 9, 9)))
    (cand,) = store.read_state(sber_pdf.CANDIDATES_FILE).values()
    assert res.status == "degraded" and cand["status"] == "needs_manual" and "ZeroDivisionError" in cand["detail"]


@pytest.mark.tact
def test_an_interrupted_parse_is_recorded_before_it_starts(monkeypatch, tmp_path):
    """№ 90: кандидат ложится в состояние до разбора; такт оборван на разборе (потолок юнита) —
    следующий называет файл один раз и заново не разбирает."""
    store, ctx = _pdf_ctx(monkeypatch, tmp_path, _blank_pdf(tmp_path))
    seen = {}

    class Killed(BaseException):
        """Процесс оборван: не `Exception`, сборщик его не ловит."""

    def killed(body, kind):
        seen.update(store.read_state(sber_pdf.CANDIDATES_FILE))
        raise Killed

    with monkeypatch.context() as patch:
        patch.setattr(sber_pdf, "analyse", killed)
        with pytest.raises(Killed):
            sber_pdf.collect(ctx(date(2026, 9, 9)))
    (cand,) = seen.values()
    assert cand["status"] == "needs_manual" and cand["detail"] == sber_pdf.PENDING and cand["month"] == "2026M08"
    res = sber_pdf.collect(ctx(date(2026, 9, 10)))
    assert res.status == "degraded" and not res.retry and "оборван" in res.reasons[0]
    (cand,) = store.read_state(sber_pdf.CANDIDATES_FILE).values()
    assert cand["detail"] == sber_pdf.INTERRUPTED
    assert sber_pdf.collect(ctx(date(2026, 9, 11))).status == "ok"                  # сказано один раз


@pytest.mark.tact
def test_the_ocr_path_end_to_end_on_a_substituted_tesseract(monkeypatch, tmp_path):
    """№ 90: боевой путь `analyse` для PDF без текста — рендер, процесс tesseract (подменён снимком
    распознавания этапа 1), разбор таблицы; им же идёт дымовая проверка `ops/tools/probe.py --only sber_ocr`."""
    body = _blank_pdf(tmp_path, pages=2)
    tsv = _tesseract_tsv("ras_2026M08_p3.words.tsv")
    calls = _fake_tesseract(monkeypatch, lambda a, **k: _done(tsv))
    out = sber_pdf.analyse(body, "ras_release")
    assert out["status"] == "ok" and out["method"] == "ocr" and out["numbers"] == pytest.approx(PNL)
    assert out["ocr_seconds"] >= 0 and "detail" not in out and len(calls) == 2
    args = calls[0]["args"]
    assert args[0] == "tesseract" and args[2:] == ["-", "-l", "rus", "--psm", "6", "tsv"]
    assert calls[0]["errors"] == "replace" and calls[0]["encoding"] == "utf-8"       # битый байт вывода не роняет
    seed = config.load_seed("ras_release")["months"]["2026M08"]["values"]
    assert all(out["numbers"][k] == seed[k]["v"] for k in ("nii_ytd", "nii_m", "np_ytd", "np_m"))
