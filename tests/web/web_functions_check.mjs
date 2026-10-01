// Дверь данных `functions/api/model.js` и фильтр `functions/_middleware.js` в Node — с
// подменёнными fetch, Cache API и часами: функция ИСПОЛНЯЕТСЯ, спрашивается ответ.
// Адреса и схема — переменные Pages из wrangler.toml ([vars]): их читает сам сценарий.
// Запускает tests/test_web_node.py; печатает JSON с итогами, код выхода 1 — если хоть
// одна проверка не прошла.
import { readFileSync } from "node:fs";

const root = new URL("../../", import.meta.url);
// Модуль функции — ES-модуль в .js без package.json: грузим текстом через data:-адрес.
const load = (rel) => import("data:text/javascript;base64," + Buffer.from(readFileSync(new URL(rel, root))).toString("base64"));
const { onRequest } = await load("functions/api/model.js");
const middleware = await load("functions/_middleware.js");

const toml = readFileSync(new URL("wrangler.toml", root), "utf8");
const vars = Object.fromEntries([...toml.split("[vars]")[1].matchAll(/^(\w+)\s*=\s*"([^"]*)"/gm)].map((m) => [m[1], m[2]]));
const ENV = { DATA_SCHEMA: vars.DATA_SCHEMA, DATA_PAGES_URL: vars.DATA_PAGES_URL, DATA_RAW_URL: vars.DATA_RAW_URL };
const PAGES = ENV.DATA_PAGES_URL, RAW = ENV.DATA_RAW_URL, SCHEMA = ENV.DATA_SCHEMA;
const SITE = "https://example.pages.dev";
const SHA_A = "0123456789ab".padEnd(64, "c");
const SHA_B = "fedcba987654".padEnd(64, "d");
const PUBLISHED = "2026-09-30T16:07:40+00:00";
const NOW = Date.UTC(2026, 8, 30, 16, 20, 30);
const UA = { "user-agent": "tests/1.0" };

const checks = [];
const check = (name, cond, detail) => checks.push({ name, ok: !!cond, detail: cond ? undefined : detail });

check("переменные Pages заданы в wrangler.toml", /^https:\/\//.test(PAGES || "") && /^https:\/\//.test(RAW || "") && /-v\d+$/.test(SCHEMA || ""), ENV);

function release({ sha = SHA_A, published = PUBLISHED, schema = SCHEMA, extra = "" } = {}) {
  return `{"schema": ${JSON.stringify(schema)}, "meta": {"payload_sha256": ${JSON.stringify(sha)}, "published_at": ${JSON.stringify(published)}, `
    + `"generated_at": "2026-09-30T16:05:12+00:00"}, "fair_value": {"central": 382${extra}}}`;
}
const ok = (body, type = "application/json; charset=utf-8") => () => new Response(body, { status: 200, headers: { "content-type": type } });
const status = (code) => () => new Response("upstream says no", { status: code });
const broken = (name = "TypeError") => () => { const e = new Error("сбой сети"); e.name = name; throw e; };

class FakeCache {
  constructor() { this.store = new Map(); this.puts = []; }
  async match(request) { const hit = this.store.get(typeof request === "string" ? request : request.url); return hit ? new Response(hit.body, { headers: hit.headers }) : undefined; }
  async put(request, response) { this.puts.push(request.url); this.store.set(request.url, { body: await response.text(), headers: Object.fromEntries(response.headers) }); }
}

const realNow = Date.now;
function world(routes, { now = NOW, cache = new FakeCache() } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const handler = routes[String(url).split("?")[0]];
    if (!handler) throw new Error(`неожиданный подзапрос ${url}`);
    return handler(url, init);
  };
  globalThis.caches = { default: cache };
  Date.now = () => now;
  return { calls, cache };
}

async function ask(method = "GET", { path = "/api/model", headers = UA, env = ENV } = {}) {
  const waits = [];
  const response = await onRequest({ request: new Request(SITE + path, { method, headers }), env, waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  return response;
}
const body = async (r) => { try { return JSON.parse(await r.text()); } catch { return null; } };

// 1. основной источник
{
  const { calls, cache } = world({ [PAGES]: ok(release()) });
  const r = await ask();
  check("Pages отвечает: 200, тело как есть", r.status === 200 && (await r.text()) === release(), r.status);
  check("x-data-source pages", r.headers.get("x-data-source") === "pages", r.headers.get("x-data-source"));
  check("Last-Modified из published_at", r.headers.get("last-modified") === "Wed, 30 Sep 2026 16:07:40 GMT", r.headers.get("last-modified"));
  check("ETag сильный: sha12.секунды", r.headers.get("etag") === `"${SHA_A.slice(0, 12)}.${Math.floor(Date.parse(PUBLISHED) / 1000)}"`, r.headers.get("etag"));
  check("Cache-Control public, max-age=60", r.headers.get("cache-control") === "public, max-age=60", r.headers.get("cache-control"));
  check("content-type JSON", r.headers.get("content-type") === "application/json; charset=utf-8", r.headers.get("content-type"));
  check("один подзапрос, минутная метка строит сервер", calls.length === 1 && calls[0].url === `${PAGES}?m=${Math.floor(NOW / 60000)}`, calls.map((c) => c.url));
  check("край кэширует подзапрос 60 с", calls[0].init.cf && calls[0].init.cf.cacheTtl === 60, calls[0].init.cf);
  check("подзапрос с сигналом отмены (таймаут)", calls[0].init.signal instanceof AbortSignal, typeof calls[0].init.signal);
  const agent = new Headers(calls[0].init.headers).get("user-agent");
  check("свой User-Agent — только ASCII", /^[\x20-\x7e]+$/.test(agent || ""), agent);
  check("годная копия — в кэш края под ключом своего хоста", cache.puts.length === 1 && cache.puts[0] === `${SITE}/__edge-cache/api/model/latest.json`, cache.puts);
  const stored = cache.store.get(`${SITE}/__edge-cache/api/model/latest.json`);
  check("копия края живёт дольше суток", /max-age=(\d+)/.test(stored.headers["cache-control"]) && +stored.headers["cache-control"].match(/max-age=(\d+)/)[1] >= 86400, stored.headers);
}
{
  const { calls } = world({ [PAGES]: ok(release()) }, { now: NOW + 61_000 });
  await ask("GET", { path: "/api/model?m=1&bust=please" });
  check("query клиента не доходит до источника", calls[0].url === `${PAGES}?m=${Math.floor((NOW + 61_000) / 60000)}`, calls[0].url);
}

// 2. запасной источник и строгий разбор
{
  const { calls } = world({ [PAGES]: status(404), [RAW]: ok(release()) });
  const r = await ask();
  check("Pages 404, raw отвечает: 200 из raw", r.status === 200 && r.headers.get("x-data-source") === "raw", [r.status, r.headers.get("x-data-source")]);
  check("raw — без минутной метки", calls[1] && calls[1].url === RAW, calls.map((c) => c.url));
}
for (const [label, text, type] of [["HTML вместо данных", "<!doctype html><title>404</title>", "text/html"], ["NaN в JSON", release({ extra: ', "x": NaN' })],
  ["битый JSON", release().slice(0, 40)], ["чужая схема", release({ schema: "lenta-v1" })], ["без published_at", release({ published: "" })],
  ["без хэша", release({ sha: "не хэш" })], ["хэш с переводом строки", release({ sha: SHA_A.slice(0, 32) + "\n" + SHA_A.slice(33) })], ["массив", "[1,2]"]]) {
  world({ [PAGES]: ok(text, type), [RAW]: ok(release({ sha: SHA_B })) });
  const r = await ask();
  const got = await body(r);
  check(`Pages отдаёт негодное (${label}) — берётся raw`, r.status === 200 && r.headers.get("x-data-source") === "raw" && got && got.meta.payload_sha256 === SHA_B, [r.status, r.headers.get("x-data-source")]);
}

// 3. отказы
{
  world({ [PAGES]: status(404), [RAW]: status(404) });
  const r = await ask();
  const b = await body(r);
  check("оба 404, копии нет: 503 not published yet", r.status === 503 && b.error === "not published yet" && r.headers.get("cache-control") === "no-store", [r.status, b]);
}
for (const [pages, raw] of [[status(500), status(404)], [status(404), broken()], [broken("AbortError"), broken()], [ok(release({ schema: "x" })), ok("<html>")]]) {
  world({ [PAGES]: pages, [RAW]: raw });
  const r = await ask();
  const b = await body(r);
  check(`прочий отказ: 503 upstream unavailable (${b && b.pages} / ${b && b.raw})`, r.status === 503 && b.error === "upstream unavailable", [r.status, b]);
}
{
  world({ [PAGES]: broken("AbortError"), [RAW]: status(502) });
  const b = await body(await ask());
  check("причины источников в теле 503", b.pages === "timeout" && b.raw === "http 502", b);
}
{
  // Зависшее соединение отменяется таймером двери, а не ждёт предела платформы.
  const realTimeout = globalThis.setTimeout;
  let asked = null;
  globalThis.setTimeout = (fn, ms) => { if (ms >= 1000) { asked = ms; return realTimeout(fn, 30); } return realTimeout(fn, ms); };
  const hang = (url, init) => new Promise((resolve, reject) => { init.signal.addEventListener("abort", () => { const e = new Error("aborted"); e.name = "AbortError"; reject(e); }); });
  world({ [PAGES]: hang, [RAW]: hang });
  const started = realNow();
  const r = await ask();
  globalThis.setTimeout = realTimeout;
  const b = await body(r);
  check("источник завис — отмена по таймеру и 503 с причиной timeout", r.status === 503 && b.pages === "timeout" && b.raw === "timeout" && realNow() - started < 2000, [r.status, b]);
  check("предел ожидания источника — 5–8 с", asked >= 5000 && asked <= 8000, asked);
}

// 4. копия края
{
  const cache = new FakeCache();
  world({ [PAGES]: ok(release()) }, { cache });
  await ask();
  world({ [PAGES]: broken(), [RAW]: status(503) }, { cache });
  const r = await ask();
  check("оба отказали: последняя годная копия из кэша края", r.status === 200 && r.headers.get("x-data-source") === "edge-cache" && (await r.text()) === release(), r.status);
  check("у копии свой Last-Modified", r.headers.get("last-modified") === "Wed, 30 Sep 2026 16:07:40 GMT", r.headers.get("last-modified"));
  check("копия из кэша обратно не пишется", cache.puts.length === 1, cache.puts);
  world({ [PAGES]: status(404), [RAW]: status(404) }, { cache });
  check("пропавшая ветка — копия лучше пустоты", (await ask()).headers.get("x-data-source") === "edge-cache", null);
}
{
  const cache = new FakeCache();
  await cache.put(new Request(`${SITE}/__edge-cache/api/model/latest.json`), new Response("<html>"));
  world({ [PAGES]: broken(), [RAW]: broken() }, { cache });
  check("испорченная копия в кэше не отдаётся", (await ask()).status === 503, null);
}

// 5. методы, UA, конфигурация, условные запросы
{
  const { calls } = world({ [PAGES]: ok(release()) });
  for (const method of ["POST", "PUT", "DELETE", "PATCH"]) {
    const r = await ask(method);
    check(`${method}: 405 с Allow`, r.status === 405 && r.headers.get("allow") === "GET, HEAD", r.status);
  }
  const noUa = await ask("GET", { headers: {} });
  check("без User-Agent — 403 и без подзапросов", noUa.status === 403 && (await body(noUa)).error === "user-agent required" && calls.length === 0, [noUa.status, calls.length]);
  const blank = await ask("GET", { headers: { "user-agent": "  " } });
  check("пустой User-Agent — 403", blank.status === 403, blank.status);
  const bad = await ask("GET", { env: { DATA_SCHEMA: SCHEMA } });
  const bb = await body(bad);
  check("переменные Pages не заданы — 503 upstream unavailable, без подзапросов", bad.status === 503 && bb.error === "upstream unavailable" && bb.pages === "not configured" && calls.length === 0, [bad.status, bb]);
}
{
  world({ [PAGES]: ok(release()) });
  const head = await ask("HEAD");
  check("HEAD: 200 без тела, те же заголовки", head.status === 200 && (await head.text()) === "" && head.headers.get("x-data-source") === "pages", head.status);
  const etag = (await ask()).headers.get("etag");
  for (const tag of [etag, `W/${etag}`, `"zzz", W/${etag}`]) {
    const r = await ask("GET", { headers: { ...UA, "if-none-match": tag } });
    check(`If-None-Match ${tag.slice(0, 22)}…: 304`, r.status === 304 && r.headers.get("etag") === etag, r.status);
  }
  check("чужой ETag — 200", (await ask("GET", { headers: { ...UA, "if-none-match": '"0123456789ab.1"' } })).status === 200, null);
  world({ [PAGES]: ok(release({ published: "2026-10-01T16:07:40Z" })) });
  const again = await ask();
  check("тот же выпуск, опубликованный заново, — другой ETag", again.headers.get("etag") !== etag && again.headers.get("last-modified") === "Thu, 01 Oct 2026 16:07:40 GMT", again.headers.get("etag"));
}

// 6. фильтр /api/ и заголовки безопасности
{
  const next = async () => new Response('{"ok":1}', { status: 200 });
  for (const path of ["/api/foo", "/api//model", "/api/model/", "/api/Model", "/api/model.json", "/api"]) {
    const r = await middleware.onRequest({ request: new Request(SITE + path), next });
    const b = await body(r);
    check(`${path} — JSON-404 с CSP`, r.status === 404 && b && b.error === "not found" && !!r.headers.get("content-security-policy"), [r.status, b]);
  }
  const r = await middleware.onRequest({ request: new Request(SITE + "/api/model"), next });
  check("/api/model проходит к функции с заголовками", r.status === 200 && r.headers.get("x-content-type-options") === "nosniff"
    && r.headers.get("referrer-policy") === "no-referrer" && /script-src 'self' 'sha256-/.test(r.headers.get("content-security-policy") || ""), r.status);
}

Date.now = realNow;
const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ total: checks.length, failed }, null, 1));
process.exitCode = failed.length ? 1 : 0;
