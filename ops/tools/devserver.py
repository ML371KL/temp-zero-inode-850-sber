"""Локальный предпросмотр витрины без Cloudflare (схема Ленты).

Отдаёт `web/` как статику и подменяет `/api/model` выпуском с диска: по умолчанию
`$BANK_STATE_DIR/release/latest.json` (без переменной — `var/state/release/latest.json`),
вторым аргументом — любой файл (синтетический выпуск tests/fixtures/payload-sample.json,
выпуск с плашками). Нужен только для проверки вёрстки и снимков: в бою `/api/model`
обслуживает функция Pages.

CSP берётся ИЗ `functions/_middleware.js`, а не пишется второй раз: устаревший хэш
скрипта темы иначе ломал бы тему только в бою. Правило двери про User-Agent
повторено: без него — 403, как у `functions/api/model.js`.

    python ops/tools/devserver.py [порт] [путь-к-выпуску]
"""

from __future__ import annotations

import json
import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
MIDDLEWARE = ROOT / "functions" / "_middleware.js"


def default_release() -> Path:
    state = os.environ.get("BANK_STATE_DIR")
    return (Path(state) if state else ROOT / "var" / "state") / "release" / "latest.json"


def read_csp() -> str:
    """Та же строка CSP, что соберёт функция в бою."""
    src = MIDDLEWARE.read_text(encoding="utf-8")
    hash_ = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', src).group(1)
    body = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S).group(1)
    parts = [m.group(1) for m in re.finditer(r"[`\"]([^`\"]+)[`\"]", body)]
    return "; ".join(p.replace("${THEME_SCRIPT_HASH}", hash_) for p in parts)


CSP = read_csp()


class Handler(SimpleHTTPRequestHandler):
    release = default_release()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/model":
            if not self.headers.get("user-agent", "").strip():
                return self._json(403, {"error": "user-agent required"})
            if not self.release.exists():
                return self._json(503, {"error": "not published yet"})
            return self._raw(200, self.release.read_bytes())
        if path.startswith("/api"):
            return self._json(404, {"error": "not found"})
        if path not in ("/", "/index.html") and not (WEB / path.lstrip("/")).is_file():
            return self._raw(404, (WEB / "404.html").read_bytes(), "text/html; charset=utf-8")
        return super().do_GET()

    def _json(self, status, body):
        self._raw(status, json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _raw(self, status, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):  # noqa: N802
        # Предпросмотр ничего не кэширует: правка стилей видна сразу.
        self.send_header("cache-control", "no-store, must-revalidate")
        self.send_header("content-security-policy", CSP)
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        super().end_headers()

    def log_message(self, *args):  # тише
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    port = int(argv[0]) if argv else 8871
    Handler.release = Path(argv[1]).resolve() if len(argv) > 1 else default_release()
    with ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(WEB))) as server:
        print(f"предпросмотр: http://127.0.0.1:{port}/")
        print(f"выпуск: {Handler.release}")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
