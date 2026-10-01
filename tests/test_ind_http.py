"""HTTP индикаторов: таблица User-Agent по хостам, без прокси окружения, корень Минцифры только для T-Invest."""

from __future__ import annotations

import hashlib
import io
import re
import ssl
import sys
import urllib.error
import urllib.request

import pytest

from indicators import http
from tests.support_ind import network_selected

ROOT_SHA256 = "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"
SUB_2024_SHA256 = "2155785036C900DBB5F1BB2A1569C80C55595BD6BF94867A29BBDDBC7D88A3F2"


@pytest.mark.tact
def test_cbr_gets_the_native_python_user_agent():
    expected = "Python-urllib/%d.%d" % sys.version_info[:2]
    for url in ("https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx", "https://cbr.ru/hd_base/KeyRate/"):
        assert http.user_agent(url) == expected
    assert re.fullmatch(r"Python-urllib/3\.\d+", expected)


@pytest.mark.tact
def test_other_hosts_get_the_project_ascii_user_agent():
    for url in ("https://iss.moex.com/iss/engines/stock/zcyc.json", "https://invest-public-api.tinkoff.ru/rest/x",
                "https://www.interfax.ru/business/1", "https://t.me/s/channel", "https://tzi-850-sber.pages.dev/api/model"):
        ua = http.user_agent(url)
        assert ua == http.PROJECT_USER_AGENT
        ua.encode("ascii")
        assert "Python-urllib" not in ua and "Mozilla" not in ua


@pytest.mark.tact
def test_the_table_holds_only_cbr_hosts():
    assert set(http.UA_BY_HOST) == {"cbr.ru", "www.cbr.ru"}


class _Reply(io.BytesIO):
    status = 200
    headers: dict = {}
    landed: str | None = None

    def geturl(self):
        return self.landed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.tact
def test_fetch_keeps_the_cbr_user_agent_even_if_a_caller_passes_another(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, timeout=None):
            seen.update(request.header_items())
            return _Reply(b"ok")

    monkeypatch.setattr(http, "opener", lambda url, hosts=None: Opener())
    http.fetch("https://www.cbr.ru/x", headers={"User-Agent": "Mozilla/5.0"})
    assert seen["User-agent"] == http.PYTHON_USER_AGENT
    http.fetch("https://iss.moex.com/x", headers={"User-Agent": "custom/1.0"})
    assert seen["User-agent"] == "custom/1.0"


@pytest.mark.tact
def test_environment_proxy_is_switched_off(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9")
    # Мощность проверки: обычный открыватель при таком окружении прокси берёт.
    default = [h for h in urllib.request.build_opener().handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert default and default[0].proxies
    for url in ("https://iss.moex.com/iss/x.json", "https://invest-public-api.tinkoff.ru/rest/x"):
        proxies = [h.proxies for h in http.opener(url).handlers if isinstance(h, urllib.request.ProxyHandler)]
        assert all(p == {} for p in proxies)


@pytest.mark.tact
def test_mintsifry_root_is_pinned_only_for_tinvest_hosts():
    assert set(http.EXTRA_CA) == {"invest-public-api.tinkoff.ru", "invest-public-api.tbank.ru"}
    assert isinstance(http.tls_context("https://invest-public-api.tinkoff.ru/rest/x"), ssl.SSLContext)
    for url in ("https://iss.moex.com/iss", "https://www.cbr.ru/x", "https://www.sberbank.com/a.pdf"):
        assert http.tls_context(url) is None


@pytest.mark.tact
def test_pinned_pem_fingerprints():
    text = http.TRUSTED_CA.read_text(encoding="utf-8")
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S)
    prints = [hashlib.sha256(ssl.PEM_cert_to_DER_cert(b)).hexdigest().upper() for b in blocks]
    assert prints == [ROOT_SHA256, SUB_2024_SHA256]
    for fp in prints:
        assert ":".join(fp[i:i + 2] for i in range(0, 64, 2)) in text


@pytest.mark.tact
def test_retries_on_503_and_not_on_404(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda *_: None)
    calls = []

    class Opener:
        def __init__(self, codes):
            self.codes = codes

        def open(self, request, timeout=None):
            calls.append(request.full_url)
            code = self.codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
            return _Reply(b"{}")

    seq = [503, 200]
    monkeypatch.setattr(http, "opener", lambda url, hosts=None: Opener(seq))
    assert http.fetch("https://iss.moex.com/x").status == 200
    assert len(calls) == 2
    calls.clear()
    seq[:] = [404, 200]
    with pytest.raises(http.FetchError) as err:
        http.fetch("https://iss.moex.com/y")
    assert err.value.status == 404 and len(calls) == 1


@pytest.mark.tact
def test_sink_saves_raw_before_return(monkeypatch, tmp_path):
    from indicators.store import Store

    class Opener:
        def open(self, request, timeout=None):
            return _Reply(b"<xml/>")

    monkeypatch.setattr(http, "opener", lambda url, hosts=None: Opener())
    store = Store(tmp_path)
    resp = http.fetch("https://www.cbr.ru/x", sink=http.Sink(store, "cbr"), name="x.xml")
    assert resp.raw_path and store.read_raw(resp.raw_path) == b"<xml/>"
    assert store.raw_meta(resp.raw_path)["sha256"] == hashlib.sha256(b"<xml/>").hexdigest()


HOSTS = ["www.issuer.example", "issuer.example"]


@pytest.mark.tact
def test_redirect_off_the_allowed_hosts_is_not_followed():
    """№ 50: редирект документа на чужой хост или не на https не исполняется — запрос туда не уходит."""
    guard = http._OnHosts(HOSTS)
    req = urllib.request.Request("https://www.issuer.example/a.pdf")
    kept = guard.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://issuer.example/b.pdf")
    assert kept.full_url == "https://issuer.example/b.pdf"
    for target in ("https://evil.example/b.pdf", "http://www.issuer.example/b.pdf", "https://www.issuer.example.evil.net/x"):
        with pytest.raises(http.ForeignRedirect):
            guard.redirect_request(req, io.BytesIO(), 302, "Found", {}, target)
    with_guard = [h for h in http.opener("https://www.issuer.example/a.pdf", HOSTS).handlers
                  if isinstance(h, http._OnHosts)]
    assert len(with_guard) == 1 and not [h for h in http.opener("https://iss.moex.com/x").handlers
                                         if isinstance(h, http._OnHosts)]


@pytest.mark.tact
def test_fetch_checks_the_address_and_the_final_host(monkeypatch, tmp_path):
    """№ 50: адрес не на разрешённом хосте — отказ без запроса; чужой конечный адрес — отказ без записи в архив."""
    from indicators.store import Store

    monkeypatch.setattr(http.time, "sleep", lambda *_: None)
    calls = []

    class Opener:
        def __init__(self, landed):
            self.landed = landed

        def open(self, request, timeout=None):
            calls.append(request.full_url)
            reply = _Reply(b"%PDF")
            reply.landed = self.landed
            return reply

    store = Store(tmp_path)
    sink = http.Sink(store, "sber_pdf")
    for bad in ("http://www.issuer.example/a.pdf", "https://evil.example/a.pdf"):
        with pytest.raises(http.FetchError) as err:
            http.fetch(bad, hosts=HOSTS, sink=sink, name="a.pdf")
        assert err.value.final and not http.retryable(err.value)
    assert calls == []
    monkeypatch.setattr(http, "opener", lambda url, hosts=None: Opener("https://evil.example/a.pdf"))
    with pytest.raises(http.FetchError, match="чужой адрес") as err:
        http.fetch("https://www.issuer.example/a.pdf", hosts=HOSTS, sink=sink, name="a.pdf")
    assert len(calls) == 1 and not http.retryable(err.value) and not store.raw_days("sber_pdf")
    monkeypatch.setattr(http, "opener", lambda url, hosts=None: Opener("https://issuer.example/moved/a.pdf"))
    resp = http.fetch("https://www.issuer.example/a.pdf", hosts=HOSTS, sink=sink, name="a.pdf")
    assert resp.landed == resp.final_url == "https://issuer.example/moved/a.pdf" and resp.raw_path
    plain = http.fetch("https://www.issuer.example/a.pdf")              # без `hosts` — как раньше
    assert plain.final_url == "https://issuer.example/moved/a.pdf" and plain.landed != plain.url


@pytest.mark.tact
def test_retryable_tells_the_network_from_the_answer():
    assert http.retryable(http.FetchError("https://x.example/a", None, "сеть"))
    assert http.retryable(http.FetchError("https://x.example/a", 503, "HTTP 503"))
    assert not http.retryable(http.FetchError("https://x.example/a", 404, "HTTP 404"))
    assert not http.retryable(ValueError("разбор"))


@pytest.mark.network
def test_live_tinvest_handshake_with_pinned_root(request):
    if not network_selected(request.config):
        pytest.skip("живая проверка — только с -m network")
    import socket

    ctx = http.tls_context("https://invest-public-api.tinkoff.ru/")
    with socket.create_connection(("invest-public-api.tinkoff.ru", 443), timeout=20) as sock:
        with ctx.wrap_socket(sock, server_hostname="invest-public-api.tinkoff.ru") as tls:
            assert tls.version()
