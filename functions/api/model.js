/**
 * Единственная дверь браузера к данным модели: GET/HEAD /api/model.
 *
 * Канал: конвейер кладёт выпуск в ветку `release` публичного репозитория
 * данных (`ops/publish.py`), GitHub Pages раздаёт её как статику, а эта
 * функция читает `latest.json` на своём origin — фронт ходит только сюда.
 * Адреса и схема — переменные Pages (`wrangler.toml`, [vars]): в коде
 * функции нет имени банка, дверь копируется в следующий банк без правки.
 *
 *   DATA_SCHEMA     схема выпуска (`<банк>-v1`); иная — отказ источника;
 *   DATA_PAGES_URL  latest.json на GitHub Pages — основной источник;
 *   DATA_RAW_URL    raw той же ветки — запасной (сборка Pages ему не нужна).
 *
 * ИСТОЧНИКИ по порядку: Pages с минутной меткой `?m=` (её строит СЕРВЕР —
 * CDN GitHub кэширует ответ на 10 минут), raw, последняя годная копия в Cache
 * API края. Ответ принимается после СТРОГОГО разбора: JSON (NaN и Infinity
 * `JSON.parse` не пропускает), объект со `schema` = DATA_SCHEMA,
 * `meta.payload_sha256` (64 hex) и разбираемым `meta.published_at`. HTML
 * вместо данных, битый JSON или чужая схема — отказ источника, а не 200.
 * Подзапрос ограничен UPSTREAM_TIMEOUT_MS: зависшее соединение отменяется.
 *
 * ЗАГОЛОВКИ 200: Last-Modified — из `meta.published_at` (по нему живёт сторож
 * свежести); ETag — сильный "<sha12>.<published_at в секундах>" (тот же
 * выпуск, опубликованный заново, — другой ETag); Cache-Control public,
 * max-age=60; x-data-source — pages | raw | edge-cache.
 *
 * ОТКАЗЫ: 405 — метод не GET/HEAD; 403 — нет User-Agent (правило двери:
 * сборщики и сторож шлют свой UA, безымянные клиенты — нет); 503 «not
 * published yet» — оба источника ответили 404; 503 «upstream unavailable» —
 * прочее без копии края (в том числе не заданы переменные Pages).
 */

const CACHE_SECONDS = 60;
// Копия края живёт долго: с max-age=60 она спасала бы от сбоя не дольше минуты.
const LAST_GOOD_SECONDS = 30 * 24 * 3600;
// Ключ копии — на своём хосте (Cache API принимает только абсолютные адреса).
const LAST_GOOD_PATH = "/__edge-cache/api/model/latest.json";
const UPSTREAM_TIMEOUT_MS = 8000;
// Только ASCII: заголовки HTTP кодируются latin-1.
const USER_AGENT = "tzi-850-edge/1.0";
const SHA_RE = /^[0-9a-f]{64}$/;

export async function onRequest(context) {
  const { request } = context;
  const env = context.env || {};
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json(405, { error: "method not allowed", method: request.method }, { allow: "GET, HEAD" });
  }
  if (!String(request.headers.get("user-agent") || "").trim()) {
    return json(403, { error: "user-agent required" });
  }
  const conf = config(env);
  if (!conf) {
    return json(503, { error: "upstream unavailable", pages: "not configured", raw: "not configured" });
  }

  const minute = Math.floor(Date.now() / 60000);
  const pages = await fetchSource(`${conf.pages}?m=${minute}`, "pages", conf.schema);
  const raw = pages.ok ? null : await fetchSource(conf.raw, "raw", conf.schema);
  const good = pages.ok ? pages : raw && raw.ok ? raw : null;
  if (good) {
    const response = respond(request, good.text, good.release, good.label);
    remember(context, request, good.text);
    return response;
  }
  const copy = await lastGood(request, conf.schema);
  if (copy) return respond(request, copy.text, copy.release, "edge-cache");
  const missing = pages.status === 404 && raw && raw.status === 404;
  return json(503, { error: missing ? "not published yet" : "upstream unavailable", pages: pages.detail, raw: raw ? raw.detail : null });
}

/** Адреса и схема из переменных Pages; нет любой — null. */
function config(env) {
  const pick = (name) => (typeof env[name] === "string" && env[name].trim() ? env[name].trim() : null);
  const conf = { schema: pick("DATA_SCHEMA"), pages: pick("DATA_PAGES_URL"), raw: pick("DATA_RAW_URL") };
  return conf.schema && /^https:\/\//.test(conf.pages || "") && /^https:\/\//.test(conf.raw || "") ? conf : null;
}

/** Подзапрос к источнику: { ok, label, status, detail, text?, release? }. */
async function fetchSource(url, label, schema) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      headers: { accept: "application/json", "user-agent": USER_AGENT },
      cf: { cacheTtl: CACHE_SECONDS },
      signal: controller.signal,
    });
    if (!response.ok) return { ok: false, label, status: response.status, detail: `http ${response.status}` };
    const text = await response.text();
    const parsed = parseRelease(text, schema);
    if (parsed.error) return { ok: false, label, status: response.status, detail: parsed.error };
    return { ok: true, label, status: response.status, text, release: parsed.release };
  } catch (error) {
    const name = error && error.name === "AbortError" ? "timeout" : String((error && error.name) || error);
    return { ok: false, label, status: 0, detail: name.slice(0, 80) };
  } finally {
    clearTimeout(timer);
  }
}

/** Строгий разбор выпуска: { release } или { error }. */
function parseRelease(text, schema) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    return { error: "bad json" };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return { error: "not an object" };
  if (data.schema !== schema) return { error: `schema ${JSON.stringify(data.schema)}`.slice(0, 80) };
  const meta = data.meta;
  if (!meta || typeof meta !== "object" || Array.isArray(meta)) return { error: "no meta" };
  if (typeof meta.payload_sha256 !== "string" || !SHA_RE.test(meta.payload_sha256)) return { error: "no payload_sha256" };
  const published = typeof meta.published_at === "string" ? Date.parse(meta.published_at) : NaN;
  if (!Number.isFinite(published)) return { error: "no published_at" };
  return { release: { sha: meta.payload_sha256, published } };
}

function respond(request, text, release, source) {
  const headers = new Headers({
    "content-type": "application/json; charset=utf-8",
    "cache-control": `public, max-age=${CACHE_SECONDS}`,
    "last-modified": new Date(release.published).toUTCString(),
    etag: `"${release.sha.slice(0, 12)}.${Math.floor(release.published / 1000)}"`,
    "x-data-source": source,
    "x-data-published": new Date(release.published).toISOString(),
    "x-content-type-options": "nosniff",
  });
  // Край отдаёт сжатый ответ со слабым ETag (W/): префикс снимается при сравнении.
  const strip = (tag) => tag.trim().replace(/^W\//, "");
  const inm = request.headers.get("if-none-match");
  if (inm && inm.split(",").map(strip).includes(strip(headers.get("etag")))) {
    return new Response(null, { status: 304, headers });
  }
  if (request.method === "HEAD") return new Response(null, { status: 200, headers });
  return new Response(text, { status: 200, headers });
}

function lastGoodKey(request) {
  return new Request(new URL(LAST_GOOD_PATH, request.url).toString(), { method: "GET" });
}

/** Годную копию — в кэш края фоном: ответ не ждёт записи. */
function remember(context, request, text) {
  if (typeof caches === "undefined" || !caches.default) return;
  const copy = new Response(text, {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": `public, max-age=${LAST_GOOD_SECONDS}` },
  });
  const save = caches.default.put(lastGoodKey(request), copy).catch(() => {});
  if (typeof context.waitUntil === "function") context.waitUntil(save);
}

async function lastGood(request, schema) {
  if (typeof caches === "undefined" || !caches.default) return null;
  try {
    const cached = await caches.default.match(lastGoodKey(request));
    if (!cached) return null;
    const text = await cached.text();
    const parsed = parseRelease(text, schema);
    return parsed.error ? null : { text, release: parsed.release };
  } catch (error) {
    return null;
  }
}

function json(status, body, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...(extra || {}),
    },
  });
}
