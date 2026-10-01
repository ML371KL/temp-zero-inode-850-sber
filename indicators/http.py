"""HTTP сборщиков: только стандартная библиотека.

Правила (перенесены из 850oa, X5 и Ленты; каждое оплачено):

* **User-Agent — по хосту** (`UA_BY_HOST`, тест). cbr.ru закрыт DDoS-Guard: незнакомый
  заголовок получает 403 с JS-проверкой, родной `Python-urllib/3.x` — ответ. Прочим —
  собственный ASCII-заголовок проекта (часть справочных источников на `Python-urllib`
  отвечает 403, `/api/model` на pages.dev без UA — 403). Только ASCII: заголовки кодируются latin-1.
* **Прокси окружения выключен**: `ProxyHandler({})` — запрос идёт напрямую, что бы ни
  стояло в `HTTPS_PROXY` машины.
* **Пауза между обращениями к одному хосту**, без параллелизма внутри хоста: VPS общий
  для всех панелей владельца, бан одного IP ослепляет все.
* **Повтор только осмысленный**: сеть, 408/425/429 и 5xx — с паузами; прочие 4xx — нет.
* **Сырой ответ ложится на диск ДО разбора** (`sink`): разбор чинится задним числом,
  потерянные байты — нет.
* **Чужой корень — только своему хосту.** Корень Минцифры (`tls/`) добавляется к
  обычному хранилищу ТОЛЬКО для хостов T-Invest (`EXTRA_CA`); проверка TLS не
  отключается никогда.
* **Документ эмитента — только с его хостов** (`hosts`): редирект на другой хост или
  не на https не исполняется (запрос туда не уходит), а конечный адрес ответа
  (`Response.final_url`) вызывающий сверяет ещё раз.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection

PROJECT_USER_AGENT = ("tzi-850-sber/1.0 (fair value research pipeline; "
                      "+https://github.com/ML371KL/temp-zero-inode-850-sber)")
PYTHON_USER_AGENT = "Python-urllib/%d.%d" % sys.version_info[:2]

# Хост → заголовок. Хост без строки — собственный заголовок проекта.
UA_BY_HOST: dict[str, str] = {
    "cbr.ru": PYTHON_USER_AGENT,
    "www.cbr.ru": PYTHON_USER_AGENT,
}

TLS_DIR = Path(__file__).resolve().parent / "tls"
TRUSTED_CA = TLS_DIR / "russian_trusted_ca.pem"
# Хост → файл дополнительного корня. Только T-Invest (решение владельца, DESIGN §6.1).
EXTRA_CA: dict[str, Path] = {
    "invest-public-api.tinkoff.ru": TRUSTED_CA,
    "invest-public-api.tbank.ru": TRUSTED_CA,
}

DEFAULT_TIMEOUT = 45.0
DEFAULT_ATTEMPTS = 3
RETRY_PAUSES = (3.0, 10.0, 30.0)
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
DEFAULT_THROTTLE = 1.0
# Пауза между обращениями к хосту, с. У T-Invest лимит 200 запросов в минуту.
THROTTLE_BY_HOST: dict[str, float] = {
    "iss.moex.com": 0.5,
    "www.cbr.ru": 1.0,
    "cbr.ru": 1.0,
    "invest-public-api.tinkoff.ru": 0.35,
    "invest-public-api.tbank.ru": 0.35,
    "t.me": 2.0,
    "smart-lab.ru": 2.0,
    "www.interfax.ru": 2.0,
}

_last_call: dict[str, float] = {}


class FetchError(RuntimeError):
    """Источник не ответил годным HTTP-ответом. Текст без заголовков запроса (токен)."""

    def __init__(self, url: str, status: int | None, message: str, *, final: bool = False):
        super().__init__(f"{redact(url)}: {message}")
        self.url, self.status = url, status
        self.final = final          # отказ окончательный: повтор его не снимет (чужой адрес редиректа)


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes
    fetched_at: str
    headers: dict[str, str] = field(default_factory=dict)
    raw_path: str | None = None
    final_url: str | None = None        # адрес после редиректов; None — тот же, что `url`

    @property
    def landed(self) -> str:
        """Адрес, с которого на самом деле пришёл ответ."""
        return self.final_url or self.url

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass
class Sink:
    """Куда класть сырой ответ сразу по получении: хранилище и имя источника."""

    store: Any
    source: str

    def keep(self, name: str, response: Response) -> Path:
        return self.store.save_raw(self.source, name, response.body, url=redact(response.url),
                                   fetched_at=response.fetched_at, status=response.status)


Getter = Callable[..., Response]


def retryable(exc: Exception) -> bool:
    """Снимет ли отказ повторный сбор: сеть, 408/425/429 и 5xx — да; прочие ответы (404, 403) и разбор — нет."""
    if not isinstance(exc, FetchError):
        return False
    return not exc.final and (exc.status is None or exc.status in RETRY_STATUS)


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def redact(url: str) -> str:
    """Адрес без строки запроса с секретами (токенов в адресах у нас нет, но на всякий случай)."""
    parts = urllib.parse.urlsplit(url)
    query = "&".join(p for p in parts.query.split("&") if not p.lower().startswith(("token=", "key=")))
    return urllib.parse.urlunsplit(parts._replace(query=query))


def user_agent(url: str) -> str:
    return UA_BY_HOST.get(host_of(url), PROJECT_USER_AGENT)


def tls_context(url: str) -> ssl.SSLContext | None:
    """Контекст с дополнительным корнем для хостов `EXTRA_CA`; прочим — None (обычная проверка)."""
    extra = EXTRA_CA.get(host_of(url))
    if extra is None:
        return None
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(extra))
    return context


def on_hosts(url: str, hosts: Collection[str]) -> bool:
    """Адрес — https на одном из разрешённых хостов."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in {h.lower() for h in hosts}


class ForeignRedirect(Exception):
    """Редирект уводит с разрешённых хостов."""


class _OnHosts(urllib.request.HTTPRedirectHandler):
    """Исполняет редирект только на разрешённый хост по https: на чужой адрес запрос не уходит."""

    def __init__(self, hosts: Collection[str]):
        self.hosts = frozenset(hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913 — сигнатура urllib
        if not on_hosts(newurl, self.hosts):
            raise ForeignRedirect(host_of(newurl) or newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener(url: str, hosts: Collection[str] | None = None) -> urllib.request.OpenerDirector:
    """Открыватель без прокси окружения; для хостов T-Invest — со своим корнем; `hosts` — куда можно редирект."""
    handlers: list[Any] = [urllib.request.ProxyHandler({})]
    context = tls_context(url)
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if hosts is not None:
        handlers.append(_OnHosts(hosts))
    return urllib.request.build_opener(*handlers)


def _throttle(host: str, seconds: float) -> None:
    last = _last_call.get(host)
    if last is not None:
        wait = seconds - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
          timeout: float = DEFAULT_TIMEOUT, attempts: int = DEFAULT_ATTEMPTS,
          sink: Sink | None = None, name: str | None = None,
          hosts: Collection[str] | None = None) -> Response:
    """GET (POST при `data`) с повторами и паузой по хосту; ответ 2xx — в сырой архив до возврата.

    `hosts` — разрешённые хосты документа: адрес и каждый редирект обязаны быть https на одном из
    них, иначе отказ без запроса и без записи в архив.
    """
    host = host_of(url)
    if hosts is not None and not on_hosts(url, hosts):
        raise FetchError(url, None, "адрес не на разрешённом хосте по https", final=True)
    request_headers = {"User-Agent": user_agent(url), "Accept-Encoding": "gzip"}
    request_headers.update(headers or {})
    # Заголовок UA из таблицы хоста не перебивается вызывающим: ошибка в одном
    # сборщике не должна вернуть cbr.ru незнакомый заголовок.
    if host in UA_BY_HOST:
        request_headers["User-Agent"] = UA_BY_HOST[host]
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _throttle(host, THROTTLE_BY_HOST.get(host, DEFAULT_THROTTLE))
        request = urllib.request.Request(url, data=data, headers=request_headers)
        try:
            with opener(url, hosts).open(request, timeout=timeout) as reply:
                landed = reply.geturl() or url
                if hosts is not None and not on_hosts(landed, hosts):
                    raise ForeignRedirect(host_of(landed) or landed)
                body = reply.read()
                if (reply.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                response = Response(url=url, status=reply.status, body=body, fetched_at=_now(),
                                    headers={k: v for k, v in reply.headers.items()},
                                    final_url=None if landed == url else landed)
                break
        except ForeignRedirect as exc:
            raise FetchError(url, None, f"редирект на чужой адрес ({exc})", final=True) from None
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS:
                raise FetchError(url, exc.code, f"HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, EOFError,
                gzip.BadGzipFile) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(RETRY_PAUSES[min(attempt, len(RETRY_PAUSES)) - 1])
    else:
        status = last_error.code if isinstance(last_error, urllib.error.HTTPError) else None
        raise FetchError(url, status, f"не удалось за {attempts} попыток: {type(last_error).__name__}")
    if sink is not None and name:
        # Вне цикла повторов: сбой записи на диск — не сетевой сбой, его не глотают.
        path = sink.keep(name, response)
        response = replace(response, raw_path=str(path))
    return response


def soap(url: str, operation: str, inner: str, *, namespace: str = "http://web.cbr.ru/",
         getter: Getter | None = None, **kwargs: Any) -> Response:
    """SOAP 1.1 POST (сервисы ЦБ): конверт, `SOAPAction`, текст XML."""
    envelope = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope '
                'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
                f'<{operation} xmlns="{namespace}">{inner}</{operation}></soap:Body></soap:Envelope>')
    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f"{namespace}{operation}"}
    return (getter or fetch)(url, data=envelope.encode("utf-8"), headers=headers, **kwargs)
