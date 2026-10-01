"""Публикация выпуска в репозиторий данных и сверка того, что увидит браузер.

    python ops/publish.py                     опубликовать собранный выпуск
    python ops/publish.py --dry-run           собрать коммит в клоне и показать, ничего не отправляя
    python ops/publish.py --rollback <sha>    вернуть на витрину прошлый выпуск [--note "почему"]
    python ops/publish.py --verify            сверка через боевую дверь (ждёт до SBER_VERIFY_TIMEOUT)
    python ops/publish.py --verify --once     одна попытка: дожим сверки прошлого такта
    python ops/publish.py --sync              клон ветки release = origin/release (перед сборкой)
    python ops/publish.py --init              создать осиротевшую ветку `release` (один раз)

КАНАЛ (docs/INTERFACES.md §6). Сборка (`python -m model.build_release`) → этот
скрипт в клоне ветки `release` публичного репозитория данных
(`$BANK_DATA_REPO_DIR`, по умолчанию `$BANK_STATE_DIR/data-repo`) → GitHub Pages
этой ветки → функция `functions/api/model.js` на своём адресе → браузер. Тот
же клон читает сборка: прошлый выпуск (`latest.json`) и историю (`history.json`).

ВЕТКА — ТОЛЬКО ДОПИСЫВАЕТСЯ (правило репозитория данных: без удаления ветки и
без перезаписи истории). В ней живут `.nojekyll`, `latest.json` (указатель —
копия последнего выпуска), `releases/<sha256>.json` (пишутся один раз и не
трогаются никогда), `history.json` (строка на каждую публикацию и откат,
PAYLOAD §4) и `journal.json` (полный журнал прогнозов из
`$BANK_STATE_DIR/journal.sqlite`); допускается `README.md` (публикация его не
пишет и не снимает). Прочее — снимается с громкой строкой: `.github/` в ветке
данных будил бы workflow при каждом push. Выпуски пишутся КОМПАКТНО (без
отступов, разделители `,` и `:`) — на треть меньше; прореживания нет, поэтому
СТОРОЖ ОБЪЁМА меряет дерево ветки и с порога `ops/budgets.json →
data_branch_alert_mb` публикует с тревогой (предел сайта GitHub Pages — 1 ГБ).

ХЭШ — `meta.payload_sha256`, sha256 канонического JSON без полей PAYLOAD §0.4
(зеркало `model.payload.payload_hash`, равенство сверяет тест). `meta.published_at`
вписывает эта публикация, в хэш он не входит. ВЫПУСК С ТЕМ ЖЕ ХЭШЕМ ПОВТОРНО НЕ
ПУБЛИКУЕТСЯ (PAYLOAD §0.4): повтор такта после удачного push, пересборка на
тех же входах — ни нового коммита выпуска, ни строки истории; сверке
передаётся `published_at` того, что уже в указателе. Быстрая сборка
(`meta.fast: true`) не публикуется никогда.

КЛОН ОДНОРАЗОВЫЙ: в начале каждой попытки — `fetch` и `reset --hard
origin/release` (+ `clean`); коммит — только если есть разница; push — без
силы, три попытки с растущей паузой, каждая с чистого клона. `gc.autoDetach=false`:
systemd убивает фоновый `git gc` в конце юнита и оставляет `*.lock`.

ОХРАНА КАТАЛОГА КЛОНА (`guard_clone_dir`, `ensure_clone`) — до любой разрушающей
команды: каталог клона не совпадает с каталогом состояния или кода и не лежит
выше них; непустой каталог без `.git` не удаляется — отказ; каталог с `.git`
обязан быть клоном данных ЭТОЙ панели (есть `refs/remotes/origin/release`, и
origin указывает на тот же репозиторий, что `SBER_RELEASE_REMOTE`: сменить
можно ssh-алиас, но не репозиторий — клон данных соседней панели не трогается),
иначе отказ до `set-url`, `reset` и `clean`; git не поднимается к объемлющему
репозиторию (`GIT_CEILING_DIRECTORIES`). Опечатка в `BANK_DATA_REPO_DIR` иначе
стёрла бы журнал прогнозов и невосполнимый архив — молча и на каждом такте.

GIT БЕЗ ХУКОВ: каждая команда идёт с `core.hooksPath=/dev/null` и
`core.fsmonitor=false` — хуки и монитор из клона данных не исполняются в такте,
у которого есть ключ записи. Наименьшие права, не граница доверия: `.git/config`
клона пишет тот же пользователь.

ДОСТУП: push — по ключу записи репозитория данных через алиас ssh из
`SBER_RELEASE_REMOTE` (`~/.ssh/config` пользователя конвейера); ключ и его путь
здесь не называются. ИДЕНТИЧНОСТЬ КОММИТА — только `SBER_GIT_NAME`/`SBER_GIT_EMAIL`
и только адрес `…@users.noreply.github.com` из списка `.github/commit-emails.allow`
репозитория кода: репозиторий публичный, и удалить метаданные коммита задним
числом нельзя.

ОТМЕТКИ в `$BANK_STATE_DIR`:
    release.pushed   выпуск в ветке, сверки ещё не было (JSON: вид publish|same|rollback,
                     хэш, published_at, коммит кода, коммит ветки)
    release.commit   коммит кода, чей выпуск ПОДТВЕРЖДЁН сверкой (по нему `run.sh
                     rebuild` решает, есть ли что пересобирать). Откат назад его не
                     двигает; если откатывают выпуск, чья сверка ещё висит, откат
                     записывает сюда код этого выпуска — пересборка не вернёт его
                     на том же коммите

КОДЫ: 0 — сделано; 1 — не сделано (выпуска в ветке нет или он не тот; каталог
клона — не клон данных); 3 — сделано, но требует внимания: у публикации —
журнал прогнозов не выгружен или дерево ветки доросло до порога сторожа объёма;
у сверки — дверь не отдала выпуск за отведённое время или отдаёт его запасным
источником (следующие такты дожимают сверку `--verify --once`). Удачная сверка
печатает строку «СВЕРКА: выпуск <sha12> … — пройдена …» — по ней и по отметке
`release.commit` принимают установку и каждый выпуск (ops/README.md §5).

Окружение (образец — ops/env.example; docs/INTERFACES.md §8): BANK_STATE_DIR,
BANK_DATA_REPO_DIR (их же читает ядро — без префикса банка), SBER_RELEASE_REMOTE,
SBER_GIT_NAME, SBER_GIT_EMAIL, SBER_PUBLIC_URL, SBER_VERIFY_TIMEOUT (600),
SBER_VERIFY_PAUSE (30), SBER_PUSH_BACKOFF (10,30).
"""

# Без `from __future__ import annotations`: dataclass с отложенными аннотациями
# не грузится через importlib.util.spec_from_file_location (так модуль грузят тесты).
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "sber-v1"
BRANCH = "release"
POINTER = "latest.json"
HISTORY = "history.json"
JOURNAL = "journal.json"
RELEASES_DIR = "releases"
NOJEKYLL = ".nojekyll"
README = "README.md"
KEEP = (NOJEKYLL, README, POINTER, HISTORY, JOURNAL)
DEFAULT_PUBLIC_URL = "https://tzi-850-sber.pages.dev/api/model"
NOREPLY_SUFFIX = "@users.noreply.github.com"
ALLOWED_EMAILS = (".github", "commit-emails.allow")     # список адресов авторов в репозитории кода
BUDGETS = Path(__file__).resolve().parent / "budgets.json"
ALERT_KEY = "data_branch_alert_mb"                      # порог сторожа объёма ветки данных, МБ
MB = 1024 * 1024
# Хуки и монитор файлов из клона не исполняются: у такта публикации есть ключ записи.
GIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")

# PAYLOAD §0.4: вне хэша — поля meta, `live.fetched_at`, ссылки журнала на
# выпуски и блоки, заполняемые после подсчёта хэша.
HASH_EXCLUDED_META = ("generated_at", "published_at", "payload_sha256", "previous_sha256", "bytes")
HASH_EXCLUDED_BLOCKS = ("changes", "valuation_history")
MAX_BYTES = 500_000                      # потолок компактного выпуска (PAYLOAD §0.4)

PUSH_ATTEMPTS = 3
# Сверка ждёт сборку GitHub Pages (обычно 1–3 минуты) и кэш края в 60 с
# (CACHE_SECONDS в functions/api/model.js; окно обязано его перекрывать — тест).
VERIFY_TIMEOUT = 600
VERIFY_PAUSE = 30

# Заголовки HTTP кодируются latin-1: кириллица в User-Agent роняет запрос ещё до
# отправки. Только ASCII. Без своего UA дверь отвечает 403.
HEADERS = {"accept": "application/json", "user-agent": "tzi-850-sber-publish/1.0"}

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_PATH_RE = re.compile(r"^releases/([0-9a-f]{64})\.json$")


class PublishError(RuntimeError):
    """Отказ, после которого выпуска в ветке нет (или он не тот): код 1."""


class _Fatal(PublishError):
    """Отказ, который повтором не лечится (нет такого выпуска, испорченный файл)."""


# ------------------------------------------------------------------ настройки

@dataclass
class Config:
    state_dir: Path
    data_repo: Path | None = None
    remote: str = ""
    git_name: str = ""
    git_email: str = ""
    public_url: str = DEFAULT_PUBLIC_URL
    verify_timeout: float = VERIFY_TIMEOUT
    verify_pause: float = VERIFY_PAUSE
    push_backoff: tuple = (10.0, 30.0)
    code_root: Path = ROOT
    release_file: Path | None = None
    alert_bytes: int | None = None          # порог сторожа объёма; None — из ops/budgets.json
    sleep: object = field(default=time.sleep, repr=False)

    @property
    def local_release(self) -> Path:
        return self.release_file or self.state_dir / "release" / POINTER

    @property
    def clone(self) -> Path:
        return self.data_repo or self.state_dir / "data-repo"

    @property
    def journal_db(self) -> Path:
        return self.state_dir / "journal.sqlite"

    @property
    def pushed_mark(self) -> Path:
        return self.state_dir / "release.pushed"

    @property
    def built_mark(self) -> Path:
        return self.state_dir / "release.commit"

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        state = Path(env["BANK_STATE_DIR"]) if env.get("BANK_STATE_DIR") else ROOT / "var" / "state"
        repo = Path(env["BANK_DATA_REPO_DIR"]) if env.get("BANK_DATA_REPO_DIR") else None
        backoff = tuple(float(x) for x in
                        (env.get("SBER_PUSH_BACKOFF") or "10,30").split(",") if x.strip())
        return cls(state_dir=state, data_repo=repo,
                   remote=env.get("SBER_RELEASE_REMOTE", "").strip(),
                   git_name=env.get("SBER_GIT_NAME", "").strip(),
                   git_email=env.get("SBER_GIT_EMAIL", "").strip(),
                   public_url=env.get("SBER_PUBLIC_URL", "").strip() or DEFAULT_PUBLIC_URL,
                   verify_timeout=float(env.get("SBER_VERIFY_TIMEOUT") or VERIFY_TIMEOUT),
                   verify_pause=float(env.get("SBER_VERIFY_PAUSE") or VERIFY_PAUSE),
                   push_backoff=backoff or (0.0,))


def allowed_emails(cfg: Config) -> set[str]:
    """Адреса из `.github/commit-emails.allow` репозитория кода; нет файла — отказ."""
    path = cfg.code_root.joinpath(*ALLOWED_EMAILS)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PublishError(f"список адресов авторов {'/'.join(ALLOWED_EMAILS)} не читается "
                           f"({type(exc).__name__}): без него автора коммита не проверить") from exc
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def identity(cfg: Config) -> tuple[str, str]:
    """Имя и адрес автора коммитов ветки release — из окружения, иначе отказ.

    Адрес — noreply GitHub И из списка `.github/commit-emails.allow`: чужой noreply
    тоже привязал бы публичную историю к постороннему аккаунту."""
    if not cfg.git_name or not cfg.git_email:
        raise PublishError(
            "не заданы SBER_GIT_NAME/SBER_GIT_EMAIL: без явной идентичности git "
            "подставил бы адрес машины или личную почту в публичную историю")
    if not cfg.git_email.endswith(NOREPLY_SUFFIX) or any(c.isspace() for c in cfg.git_email):
        raise PublishError(
            f"SBER_GIT_EMAIL должен быть адресом …{NOREPLY_SUFFIX}: репозиторий "
            "публичный, и адрес автора коммита виден всем навсегда")
    if cfg.git_email not in allowed_emails(cfg):
        raise PublishError(
            f"SBER_GIT_EMAIL нет в списке {'/'.join(ALLOWED_EMAILS)}: автор коммитов ветки "
            "данных — только адрес из этого списка")
    return cfg.git_name, cfg.git_email


# ------------------------------------------------------------ JSON и хэш

def _refuse_constant(token: str):
    raise ValueError(f"{token} — не строгий JSON: витрина такой ответ не разберёт")


def strict_json(text: str):
    """Разбор, как у `JSON.parse` браузера: NaN и ±Infinity — ошибка."""
    return json.loads(text, parse_constant=_refuse_constant)


def canonical(obj) -> bytes:
    """Канонический JSON (INTERFACES §7.2)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def hashed_content(payload: dict) -> dict:
    """Выпуск без полей, не входящих в хэш (PAYLOAD §0.4)."""
    body = {k: v for k, v in payload.items() if k not in HASH_EXCLUDED_BLOCKS}
    if isinstance(body.get("meta"), dict):
        body["meta"] = {k: v for k, v in body["meta"].items() if k not in HASH_EXCLUDED_META}
    if isinstance(body.get("live"), dict):
        body["live"] = {k: v for k, v in body["live"].items() if k != "fetched_at"}
    nowcast = body.get("nowcast")
    if isinstance(nowcast, dict) and isinstance(nowcast.get("journal"), dict):
        journal = {k: v for k, v in nowcast["journal"].items() if k != "releases"}
        if isinstance(journal.get("entries"), list):
            journal["entries"] = [{k: v for k, v in e.items() if k != "release_sha"}
                                  if isinstance(e, dict) else e for e in journal["entries"]]
        body["nowcast"] = dict(nowcast, journal=journal)
    return body


def payload_hash(payload: dict) -> str:
    """sha256 содержания выпуска — зеркало `model.payload.payload_hash`."""
    return hashlib.sha256(canonical(hashed_content(payload))).hexdigest()


def compact_bytes(payload: dict) -> int:
    """Байты компактного выпуска без `meta.published_at` и `meta.bytes` (PAYLOAD §0.4)."""
    meta = {k: v for k, v in (payload.get("meta") or {}).items() if k not in ("published_at", "bytes")}
    return len(json.dumps(dict(payload, meta=meta), ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def dump_release(payload: dict) -> str:
    """Файл выпуска в ветке: строгий JSON, UTF-8, компактно — без отступов (INTERFACES §6)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def dump_history(rows: list) -> str:
    """По строке на публикацию — дифф и чтение глазами (схема X5)."""
    body = ",\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows)
    return "[\n" + body + "\n]\n" if rows else "[]\n"


def now_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_stamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _get(node, *path):
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _sha(payload) -> str | None:
    sha = _get(payload, "meta", "payload_sha256")
    return sha if isinstance(sha, str) and SHA_RE.match(sha) else None


def release_problem(payload, *, where: str) -> str | None:
    """Ворота публикации: схема, честный хэш, не быстрая сборка, потолок размера."""
    if not isinstance(payload, dict):
        return f"{where}: не объект"
    if payload.get("schema") != SCHEMA:
        return f"{where}: схема {payload.get('schema')!r}, ожидается {SCHEMA!r}"
    if not isinstance(payload.get("meta"), dict):
        return f"{where}: нет объекта meta"
    digest = _sha(payload)
    if digest is None:
        return f"{where}: нет meta.payload_sha256 (64 шестнадцатеричных знака)"
    actual = payload_hash(payload)
    if actual != digest:
        return (f"{where}: содержимое не совпадает со своим хэшем ({actual[:12]} против "
                f"{digest[:12]}) — файл правили после сборки")
    if payload["meta"].get("fast") is not False:
        return f"{where}: быстрая сборка (meta.fast = {payload['meta'].get('fast')!r}) не публикуется"
    size = compact_bytes(payload)
    if size > MAX_BYTES:
        return f"{where}: {size} байт компактно — больше потолка {MAX_BYTES}"
    return None


def load_local_release(cfg: Config) -> dict:
    path = cfg.local_release
    if not path.exists():
        raise PublishError(f"нет выпуска {path.name} в каталоге сборки — сначала python -m model.build_release")
    try:
        payload = strict_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PublishError(f"выпуск не публикуется: {exc}") from exc
    problem = release_problem(payload, where="выпуск сборки")
    if problem:
        raise PublishError(problem)
    return payload


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_mark(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        mark = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PublishError(f"отметка {path.name} не читается: {exc}") from exc
    if not isinstance(mark, dict) or not SHA_RE.match(str(mark.get("payload_sha256", ""))) \
            or parse_stamp(mark.get("published_at")) is None:
        raise PublishError(f"отметка {path.name} испорчена")
    return mark


# ------------------------------------------------------------------- git

def _git_env(cwd: Path | None = None) -> dict:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # Под systemd терминала нет: вопрос ssh про ключ хоста превратил бы такт в
    # зависание до RuntimeMaxSec. Алиас и ключ ssh берёт из своего конфига.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=30")
    if cwd is not None:
        # Команда работает с репозиторием РОВНО в этом каталоге: без потолка git, не найдя
        # годного `.git` здесь, поднялся бы к объемлющему репозиторию (клон данных по
        # умолчанию лежит внутри каталога кода) и `reset`/`clean` достались бы ему.
        env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).resolve().parent)
    return env


def git(*args: str, cwd: Path | None = None, check: bool = True,
        timeout: float = 300) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(["git", *GIT_SAFE, *args], cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=_git_env(cwd), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"git {args[0]}: не ответил за {timeout:.0f} с") from exc
    if check and done.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])} → код {done.returncode}: "
                           f"{(done.stderr or done.stdout).strip()[:600]}")
    return done


def guard_clone_dir(cfg: Config) -> Path:
    """Каталог клона данных — не каталог состояния или кода и не лежит выше них.

    `BANK_DATA_REPO_DIR` задаётся окружением; опечатка (каталог состояния, его
    предок, каталог кода) отдала бы `reset --hard` и `clean -ffdx` журнал
    прогнозов, невосполнимый архив или код с `.venv`. Отказ — до любой команды."""
    clone = cfg.clone.resolve()
    for what, other in (("каталогом состояния", cfg.state_dir), ("каталогом кода", cfg.code_root)):
        other = Path(other).resolve()
        if clone == other or clone in other.parents:
            raise PublishError(
                f"каталог клона данных {clone} совпадает с {what} {other} или лежит выше него — "
                "ничего не трогаю; исправьте BANK_DATA_REPO_DIR (по умолчанию — data-repo в каталоге состояния)")
    return clone


def repo_of(remote: str) -> str:
    """Репозиторий в адресе remote — без хоста, ssh-алиаса и «.git», без учёта регистра.

    `<алиас>:владелец/имя.git`, `ssh://git@хост/владелец/имя`, `https://хост/владелец/имя.git`
    дают `владелец/имя`; локальный путь (песочница тестов) — сам путь. По нему клон
    данных своей панели отличается от клона соседней: алиас ключа менять можно,
    репозиторий — нет."""
    text = remote.strip().replace("\\", "/")
    if "://" in text:
        text = text.split("://", 1)[1].split("/", 1)[-1]
    elif ":" in text and not re.match(r"^[A-Za-z]:/", text):
        text = text.split(":", 1)[1]                    # scp-вид: <алиас или хост>:путь
    text = text.rstrip("/")
    return (text[:-4] if text.endswith(".git") else text).lower()


def ensure_clone(cfg: Config) -> Path:
    """Клон ветки release; создаётся при первом обращении. Чужой каталог не трогается."""
    clone = guard_clone_dir(cfg)
    if not (clone / ".git").exists():
        if not cfg.remote:
            raise PublishError("клона ветки release нет, а SBER_RELEASE_REMOTE не задан")
        if clone.exists() and (not clone.is_dir() or any(clone.iterdir())):
            # Ничего не удаляется: непустой каталог без .git — чужие данные (например,
            # сырой архив), а не недокачанный клон.
            raise PublishError(
                f"каталог клона данных {clone} не пуст и не клон git — не удаляю; "
                "исправьте BANK_DATA_REPO_DIR или освободите каталог руками")
        clone.parent.mkdir(parents=True, exist_ok=True)
        done = git("clone", "--quiet", "--single-branch", "--branch", BRANCH, "--no-tags",
                   cfg.remote, str(clone), check=False)
        if done.returncode != 0:
            raise PublishError(
                f"клон ветки {BRANCH} не удался (код {done.returncode}): {done.stderr.strip()[:400]}. "
                "Если ветки ещё нет — `python ops/publish.py --init`")
        print(f"  клон ветки {BRANCH} создан")
    else:
        # До set-url, reset и clean: это клон данных, а не чужой репозиторий (код, соседняя панель).
        known = git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{BRANCH}",
                    cwd=clone, check=False)
        if known.returncode != 0:
            raise PublishError(
                f"в каталоге {clone} git-репозиторий, но не клон ветки {BRANCH} репозитория данных "
                f"(нет refs/remotes/origin/{BRANCH}) — не трогаю; исправьте BANK_DATA_REPO_DIR "
                "(недокачанный клон данных — удалить руками)")
        if cfg.remote:
            current = git("remote", "get-url", "origin", cwd=clone, check=False).stdout.strip()
            if current != cfg.remote:
                if repo_of(current) != repo_of(cfg.remote):
                    # Клон данных ДРУГОЙ панели тоже несёт origin/release: `set-url` и
                    # `reset --hard` увели бы его на чужую ветку. Адреса не печатаются:
                    # в них ssh-алиас ключа.
                    raise PublishError(
                        f"в каталоге {clone} клон другого репозитория данных (origin — не тот "
                        "репозиторий, что SBER_RELEASE_REMOTE) — не трогаю; исправьте "
                        "BANK_DATA_REPO_DIR или SBER_RELEASE_REMOTE")
                git("remote", "set-url", "origin", cfg.remote, cwd=clone)
    # Каждый раз, а не только при клоне: клон мог создать кто-то руками.
    git("config", "gc.autoDetach", "false", cwd=clone)
    git("config", "core.autocrlf", "false", cwd=clone)
    return clone


def sync(cfg: Config) -> Path:
    """Клон = origin/release, без хвостов прошлых попыток."""
    clone = ensure_clone(cfg)
    git("fetch", "--quiet", "--no-tags", "origin",
        f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", cwd=clone)
    git("checkout", "--quiet", "-B", BRANCH, f"origin/{BRANCH}", cwd=clone)
    git("reset", "--quiet", "--hard", f"origin/{BRANCH}", cwd=clone)
    git("clean", "-ffdxq", cwd=clone)
    return clone


def tidy_tree(clone: Path) -> list[str]:
    """Посторонние пути ветки данных снимаются громко; выпуски не трогаются никогда."""
    tracked = git("ls-files", "-z", cwd=clone).stdout.split("\0")
    removed = []
    for path in filter(None, tracked):
        if path in KEEP or RELEASE_PATH_RE.match(path):
            continue
        git("rm", "--quiet", "-r", "--", path, cwd=clone)
        removed.append(path)
        kind = "workflow в ветке данных" if path.startswith(".github/") else "посторонний путь"
        print(f"  ВНИМАНИЕ: в ветке {BRANCH} {kind} {path} — убран", file=sys.stderr)
    if not (clone / NOJEKYLL).exists():
        (clone / NOJEKYLL).write_text("", encoding="utf-8")
    git("add", "--", NOJEKYLL, cwd=clone)
    return removed


def commit_if_changed(cfg: Config, clone: Path, message: str) -> bool:
    if git("diff", "--cached", "--quiet", cwd=clone, check=False).returncode == 0:
        return False
    name, email = identity(cfg)
    git("-c", f"user.name={name}", "-c", f"user.email={email}",
        "commit", "--quiet", "--no-verify", "-m", message, cwd=clone)
    return True


def push(clone: Path) -> None:
    # Без силы: ветка только дописывается, чужой push — отказ и повтор с чистого клона.
    git("push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=clone)


def with_retries(cfg: Config, what: str, attempt_fn):
    """Попытка = чистый клон → запись → коммит → push. Три попытки, пауза растёт."""
    last = None
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            return attempt_fn()
        except _Fatal:
            raise
        except PublishError as exc:
            last = exc
            if attempt == PUSH_ATTEMPTS:
                break
            pause = cfg.push_backoff[min(attempt - 1, len(cfg.push_backoff) - 1)]
            print(f"  {what}: попытка {attempt} не удалась ({exc}); повтор через {pause:.0f} с",
                  file=sys.stderr)
            cfg.sleep(pause)
    raise PublishError(f"{what}: {PUSH_ATTEMPTS} попытки не удались — {last}")


def code_commit(cfg: Config) -> str:
    done = git("rev-parse", "HEAD", cwd=cfg.code_root, check=False)
    out = done.stdout.strip()
    return out if done.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else ""


def write_pushed_mark(cfg: Config, *, kind: str, digest: str, published_at: str, clone: Path) -> None:
    mark = dict(kind=kind, payload_sha256=digest, published_at=published_at,
                code_commit=code_commit(cfg) if kind in ("publish", "same") else "",
                release_commit=git("rev-parse", "HEAD", cwd=clone).stdout.strip(),
                pushed_at=now_stamp())
    _write_atomic(cfg.pushed_mark, json.dumps(mark, ensure_ascii=False, indent=1) + "\n")


# ------------------------------------------------------ сторож объёма ветки

def tree_bytes(clone: Path) -> int:
    """Размер дерева ветки (файлы клона без `.git`) — то, что раздаёт сайт GitHub Pages."""
    total = 0
    for root, dirs, files in os.walk(clone):
        if Path(root) == clone and ".git" in dirs:
            dirs.remove(".git")
        total += sum(os.path.getsize(os.path.join(root, name)) for name in files)
    return total


def alert_bytes(cfg: Config) -> int:
    """Порог сторожа объёма: `ops/budgets.json → data_branch_alert_mb`, в байтах."""
    if cfg.alert_bytes is not None:
        return int(cfg.alert_bytes)
    try:
        value = json.loads(BUDGETS.read_text(encoding="utf-8"))[ALERT_KEY]
    except (OSError, ValueError, KeyError) as exc:
        raise PublishError(f"порог сторожа объёма ({BUDGETS.name} → {ALERT_KEY}) не читается: "
                           f"{type(exc).__name__}") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise PublishError(f"порог сторожа объёма ({BUDGETS.name} → {ALERT_KEY}) — не положительное число")
    return int(value * MB)


def branch_size_problem(cfg: Config, clone: Path) -> str | None:
    """Причина тревоги сторожа объёма или None. Выпуск уже в ветке: это тревога, не отказ."""
    try:
        size, limit = tree_bytes(clone), alert_bytes(cfg)
    except (OSError, PublishError) as exc:
        return f"сторож объёма ветки {BRANCH} не сработал: {exc}"
    if size < limit:
        return None
    return (f"дерево ветки {BRANCH} — {size / MB:.0f} МБ, порог сторожа {limit / MB:.0f} МБ "
            "(предел сайта GitHub Pages — 1 ГБ; ветка только дописывается): решить, как быть "
            "со старыми выпусками, до предела")


# ----------------------------------------------------------- файлы ветки

def read_branch_json(clone: Path, name: str):
    path = clone / name
    if not path.exists():
        return None
    try:
        return strict_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"  ВНИМАНИЕ: {name} в ветке не читается ({exc}) — пишу заново", file=sys.stderr)
        return None


def read_history(clone: Path) -> list:
    rows = read_branch_json(clone, HISTORY)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise _Fatal(f"{HISTORY} в ветке — не список: историю не дописываю поверх непонятного")
    return rows


def history_row(payload: dict, *, event: str, published_at: str, previous_sha: str | None,
                note: str | None = None) -> dict:
    """Строка `history.json` (PAYLOAD §4)."""
    headline = _get(payload, "fair_value", "headline") or {}
    prices = _get(payload, "market", "prices") or {}
    row = {"published_at": published_at, "event": event,
           "payload_sha256": _sha(payload), "previous_sha256": previous_sha,
           "generated_at": _get(payload, "meta", "generated_at"),
           "valuation_date": _get(payload, "meta", "valuation_date"),
           "facts_date": _get(payload, "meta", "facts_date"),
           "book_version": _get(payload, "meta", "book_version"),
           "engine_commit": _get(payload, "meta", "engine_commit"),
           "prices": {t: _get(p, "price") for t, p in prices.items()} if isinstance(prices, dict) else {},
           "median": headline.get("median"), "band80": headline.get("band80"),
           "band50": headline.get("band50"), "point": headline.get("point"),
           "printed_median": headline.get("printed_median"),
           "bytes": _get(payload, "meta", "bytes")}
    if note:
        row["note"] = note
    return row


def journal_export(cfg: Config) -> tuple[dict | None, str | None]:
    """Полный журнал прогнозов для `journal.json`: (содержимое, причина отказа)."""
    if not cfg.journal_db.exists():
        return None, None                       # журнала ещё нет — нечего выгружать
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from indicators.journal import Journal  # noqa: PLC0415
        return Journal(cfg.journal_db, readonly=True).export(), None
    except Exception as exc:  # noqa: BLE001 — выпуск важнее журнала: тревога, не провал
        return None, f"журнал прогнозов не выгружен в {JOURNAL}: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------- публикация

def cmd_publish(cfg: Config, *, dry_run: bool = False) -> int:
    if not dry_run:
        identity(cfg)
    payload = load_local_release(cfg)
    digest = payload["meta"]["payload_sha256"]
    published_at = now_stamp()
    journal, journal_problem = journal_export(cfg)
    outcome = {}
    print(f"выпуск {digest[:12]} · ветка {BRANCH}" + (" · проба без отправки" if dry_run else ""))

    def attempt():
        outcome.clear()
        clone = sync(cfg)
        tidy_tree(clone)
        current = read_branch_json(clone, POINTER)
        current_sha = _sha(current)
        if journal is not None:
            _write_atomic(clone / JOURNAL, json.dumps(journal, ensure_ascii=False, separators=(",", ":"),
                                                      allow_nan=False) + "\n")
            git("add", "--", JOURNAL, cwd=clone)
        if current_sha == digest:
            # Повтор без дублей: тот же выпуск уже в указателе.
            stamp = _get(current, "meta", "published_at")
            if parse_stamp(stamp) is None:
                raise _Fatal(f"{POINTER} в ветке без разбираемого meta.published_at")
            outcome.update(kind="same", published_at=stamp)
            print(f"  выпуск {digest[:12]} уже в указателе (published_at {stamp}) — повторно не публикуется")
            message = f"журнал прогнозов · {published_at}"
        else:
            history = read_history(clone)
            frozen = clone / RELEASES_DIR / f"{digest}.json"
            stamped = dict(payload, meta=dict(payload["meta"], published_at=published_at))
            if frozen.exists():
                print(f"  releases/{digest[:12]}….json уже в ветке — не переписываю")
            else:
                _write_atomic(frozen, dump_release(stamped))
            _write_atomic(clone / POINTER, dump_release(stamped))
            history.append(history_row(payload, event="publish", published_at=published_at,
                                       previous_sha=current_sha))
            _write_atomic(clone / HISTORY, dump_history(history))
            git("add", "--", f"{RELEASES_DIR}/{digest}.json", POINTER, HISTORY, cwd=clone)
            outcome.update(kind="publish", published_at=published_at, rows=len(history))
            message = f"выпуск {digest[:12]} · {published_at}"
        if dry_run:
            stat = git("diff", "--cached", "--stat", cwd=clone).stdout.rstrip()
            print("  коммит был бы таким:\n" + (stat or "  (разницы нет — коммит не нужен)"))
            git("reset", "--quiet", "--hard", f"origin/{BRANCH}", cwd=clone)
            git("clean", "-ffdxq", cwd=clone)
            return clone
        if commit_if_changed(cfg, clone, message):
            push(clone)
            print(f"  отправлено в {BRANCH}: {git('rev-parse', '--short', 'HEAD', cwd=clone).stdout.strip()}"
                  + (f", строк истории {outcome['rows']}" if "rows" in outcome else ""))
        else:
            print(f"  ветка {BRANCH} уже содержит ровно это — коммит не нужен")
        return clone

    try:
        clone = with_retries(cfg, "публикация", attempt)
    except _Fatal as exc:
        raise PublishError(str(exc)) from None
    if dry_run:
        if journal_problem:
            print(f"ПУБЛИКАЦИЯ: {journal_problem}", file=sys.stderr)
        return 0
    write_pushed_mark(cfg, kind=outcome["kind"], digest=digest, published_at=outcome["published_at"],
                      clone=clone)
    print(f"  отметка release.pushed: {digest[:12]} · {outcome['published_at']}")
    problems = [x for x in (journal_problem, branch_size_problem(cfg, clone)) if x]
    for problem in problems:
        print(f"ПУБЛИКАЦИЯ: {problem}", file=sys.stderr)
    return 3 if problems else 0


def cmd_rollback(cfg: Config, prefix: str, note: str | None = None) -> int:
    """Откат — указатель на прошлый выпуск с НОВЫМ `published_at` и строка истории.

    Новый момент публикации нужен сторожу свежести: со старым `Last-Modified`
    откат сразу давал бы «данные перестали обновляться». `release.commit` откат
    назад не двигает: иначе ближайшая пересборка вернула бы то, от чего
    откатывались. По той же причине откат выпуска, чья сверка ещё висит
    (`release.pushed` вида publish|same с хэшем откатываемого указателя),
    записывает в `release.commit` код этого выпуска: отметку сверки откат
    перезаписывает, и без этого пересборка увидела бы «новый» код и за 15 минут
    вернула бы выпуск поверх отката.
    """
    identity(cfg)
    prefix = (prefix or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12,64}", prefix):
        raise PublishError(f"откат: ожидается хэш выпуска (от 12 шестнадцатеричных знаков), получено {prefix!r}")
    published_at = now_stamp()
    found = {}
    try:
        pending = read_mark(cfg.pushed_mark)
    except PublishError:
        pending = None                          # испорченную отметку откат перезапишет своей

    def attempt():
        clone = sync(cfg)
        tidy_tree(clone)
        matches = sorted(p for p in (clone / RELEASES_DIR).glob("*.json")
                         if p.stem.startswith(prefix) and SHA_RE.match(p.stem))
        if len(matches) != 1:
            names = ", ".join(p.stem[:12] for p in matches) or "нет"
            raise _Fatal(f"откат: выпуск {prefix[:12]} в ветке не найден однозначно (совпадения: {names})")
        digest = matches[0].stem
        try:
            source = strict_json(matches[0].read_text(encoding="utf-8"))
        except ValueError as exc:
            raise _Fatal(f"откат: releases/{digest[:12]}….json не читается ({exc})") from None
        if not isinstance(source, dict) or source.get("schema") != SCHEMA or payload_hash(source) != digest:
            raise _Fatal(f"откат: releases/{digest[:12]}….json испорчен (схема или хэш)")
        current_sha = _sha(read_branch_json(clone, POINTER))
        found["digest"], found["previous"] = digest, current_sha
        if current_sha == digest:
            found["noop"] = True
            return clone
        history = read_history(clone)
        _write_atomic(clone / POINTER, dump_release(dict(source, meta=dict(source["meta"],
                                                                          published_at=published_at))))
        history.append(history_row(source, event="rollback", published_at=published_at,
                                   previous_sha=current_sha,
                                   note=note or f"откат с {str(current_sha)[:12]}"))
        _write_atomic(clone / HISTORY, dump_history(history))
        git("add", "--", POINTER, HISTORY, cwd=clone)
        if commit_if_changed(cfg, clone, f"откат на {digest[:12]} · {published_at}"):
            push(clone)
        return clone

    try:
        clone = with_retries(cfg, "откат", attempt)
    except _Fatal as exc:
        raise PublishError(str(exc)) from None
    if found.get("noop"):
        print(f"откат: указатель уже смотрит на {found['digest'][:12]} — откатывать нечего")
        return 0
    write_pushed_mark(cfg, kind="rollback", digest=found["digest"], published_at=published_at, clone=clone)
    print(f"откат: указатель смотрит на {found['digest'][:12]} · published_at {published_at}")
    if (pending and pending.get("kind") in ("publish", "same") and pending.get("code_commit")
            and pending.get("payload_sha256") == found["previous"]):
        _write_atomic(cfg.built_mark, pending["code_commit"] + "\n")
        print(f"откат: сверка откатываемого выпуска не была завершена — release.commit = "
              f"{pending['code_commit'][:8]} (его код): пересборка не вернёт его на том же коммите")
    return 0


def cmd_sync(cfg: Config) -> int:
    clone = sync(cfg)
    sha = _sha(read_branch_json(clone, POINTER))
    print(f"  клон ветки {BRANCH}: {git('rev-parse', '--short', 'HEAD', cwd=clone).stdout.strip()}, "
          f"указатель {sha[:12] if sha else 'пуст'}")
    return 0


def cmd_init(cfg: Config) -> int:
    """Осиротевшая ветка `release` с одним `.nojekyll` — если её ещё нет."""
    name, email = identity(cfg)
    if not cfg.remote:
        raise PublishError("init: SBER_RELEASE_REMOTE не задан")
    heads = git("ls-remote", "--heads", cfg.remote, BRANCH).stdout
    if f"refs/heads/{BRANCH}" in heads:
        print(f"ветка {BRANCH} уже есть — создавать нечего")
        return 0
    work = cfg.state_dir / "release-init"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        git("init", "--quiet", "-b", BRANCH, str(work))
        git("config", "core.autocrlf", "false", cwd=work)
        (work / NOJEKYLL).write_text("", encoding="utf-8")
        git("add", "--", NOJEKYLL, cwd=work)
        git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet",
            "--no-verify", "-m", "ветка данных: только выпуски для GitHub Pages", cwd=work)
        git("push", "--quiet", cfg.remote, f"{BRANCH}:refs/heads/{BRANCH}", cwd=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"ветка {BRANCH} создана: один коммит с {NOJEKYLL}")
    return 0


# ------------------------------------------------------------------ сверка

def _fetch_door(cfg: Config):
    request = urllib.request.Request(cfg.public_url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return (strict_json(response.read().decode("utf-8")),
                {k.lower(): v for k, v in response.headers.items()})


def _served_problems(served, headers: dict, want_sha: str, want_pub: str) -> list[str]:
    meta = served.get("meta", {}) if isinstance(served, dict) else {}
    problems = []
    got_sha = str(meta.get("payload_sha256", "—"))
    got_pub = str(meta.get("published_at", "—"))
    if got_sha != want_sha:
        problems.append(f"дверь отдаёт другой выпуск: {got_sha[:12]} вместо {want_sha[:12]}")
    elif parse_stamp(got_pub) != parse_stamp(want_pub):
        problems.append(f"дверь отдаёт ту же сборку прошлой публикации: published_at {got_pub} "
                        f"вместо {want_pub}")
    if not headers.get("last-modified"):
        problems.append("нет заголовка Last-Modified — сторож свежести объявит панель мёртвой")
    if "json" not in headers.get("content-type", ""):
        problems.append(f"content-type {headers.get('content-type', '')!r}: вместо данных отдаётся страница")
    return problems


def _finish_verify(cfg: Config, mark: dict) -> None:
    if mark.get("kind") in ("publish", "same") and mark.get("code_commit"):
        _write_atomic(cfg.built_mark, mark["code_commit"] + "\n")
    cfg.pushed_mark.unlink(missing_ok=True)


def cmd_verify(cfg: Config, *, once: bool = False) -> int:
    """Читает опубликованное ЧЕРЕЗ ТУ ЖЕ дверь, что и браузер.

    Ждёт совпадения хэша И `published_at` из `release.pushed`. Источник ответа
    (`x-data-source`) — `pages` у здорового канала; `raw`/`edge-cache` значат,
    что GitHub Pages отказывает, а витрина живёт на запасном пути: выпуск
    засчитывается, но это тревога (код 3). Не дождались — код 3, отметка
    остаётся, `release.commit` не пишется. Кэш края параметром не обходится:
    проверялось бы не то, что видит браузер.
    """
    mark = read_mark(cfg.pushed_mark)
    if mark is None:
        print("  сверять нечего: незавершённой публикации нет")
        return 0
    want_sha, want_pub = mark["payload_sha256"], mark["published_at"]
    deadline = time.monotonic() + (0 if once else cfg.verify_timeout)
    attempt, problems, degraded = 0, [], None
    while True:
        attempt += 1
        try:
            served, headers = _fetch_door(cfg)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:200].decode("utf-8", "replace") if exc.fp else ""
            problems = [f"дверь ответила {exc.code}: {body}".strip()]
        except ValueError as exc:
            problems = [f"ответ не разбирается как строгий JSON: {exc}"]
        except (urllib.error.URLError, OSError) as exc:
            problems = [f"дверь не отвечает: {type(exc).__name__}"]
        else:
            problems = _served_problems(served, headers, want_sha, want_pub)
            if not problems:
                source = headers.get("x-data-source", "")
                if source == "pages":
                    _finish_verify(cfg, mark)
                    print(f"СВЕРКА: выпуск {want_sha[:12]} · {want_pub} — пройдена с попытки {attempt}, "
                          f"Last-Modified {headers.get('last-modified')}")
                    return 0
                degraded = source or "неизвестный"
                problems = [f"выпуск отдаётся запасным источником «{degraded}»: GitHub Pages "
                            "репозитория данных не отвечает или отдаёт негодное"]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"  попытка {attempt}: {problems[0]}; жду {cfg.verify_pause:.0f} с")
        cfg.sleep(min(cfg.verify_pause, max(remaining, 0)))

    if degraded:
        _finish_verify(cfg, mark)
        print(f"СВЕРКА: выпуск {want_sha[:12]} на витрине, но через запасной источник "
              f"«{degraded}» — GitHub Pages не отдаёт ветку release (настройки Pages, "
              "сборка pages-build-deployment)", file=sys.stderr)
        return 3
    for problem in problems:
        print(f"СВЕРКА: {problem}", file=sys.stderr)
    waited = "одна попытка" if once else f"{cfg.verify_timeout:g} с"
    print(f"СВЕРКА: выпуск {want_sha[:12]} в ветке {BRANCH} с {mark.get('pushed_at', '?')}, "
          f"но дверь его не отдала ({waited}); сверку дожмут следующие такты", file=sys.stderr)
    return 3


# -------------------------------------------------------------------- вход

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Публикация выпуска в репозиторий данных")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rollback", metavar="SHA", help="вернуть на витрину выпуск releases/<sha>.json")
    group.add_argument("--verify", action="store_true", help="сверка через боевую дверь")
    group.add_argument("--sync", action="store_true", help="клон ветки release = origin/release")
    group.add_argument("--init", action="store_true", help="создать осиротевшую ветку release")
    parser.add_argument("--once", action="store_true", help="сверка: одна попытка, без ожидания")
    parser.add_argument("--dry-run", action="store_true", help="публикация: показать коммит, не отправляя")
    parser.add_argument("--note", help="причина отката — в history.json")
    args = parser.parse_args(argv)
    cfg = Config.from_env()
    try:
        if args.verify:
            return cmd_verify(cfg, once=args.once)
        if args.sync:
            return cmd_sync(cfg)
        if args.init:
            return cmd_init(cfg)
        if args.rollback is not None:
            return cmd_rollback(cfg, args.rollback, args.note)
        return cmd_publish(cfg, dry_run=args.dry_run)
    except PublishError as exc:
        print(f"ПУБЛИКАЦИЯ: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
