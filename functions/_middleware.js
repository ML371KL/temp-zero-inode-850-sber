/**
 * Общий фильтр запросов к функциям сайта (схема Ленты). Две задачи.
 *
 * 1. ДЕРЖИТ ГРАНИЦУ `/api/`. Pages на путь без статики отдаёт 200 и HTML
 * главной: программа, склеившая адрес с лишним слешем (`/api//model`,
 * `/api/model/`), прочла бы «успех» и разобрала вёрстку как JSON. Здесь путь
 * под `/api/` либо ровно `model`, либо получает JSON-404.
 *
 * 2. СТАВИТ ЗАГОЛОВКИ БЕЗОПАСНОСТИ на ответы функций: `web/_headers` Pages
 * накладывает только на статику. Политика в обоих местах одна — её
 * посимвольно сверяет tests/test_web_static.py.
 */

// Хэш инлайн-скрипта темы из web/index.html (текст с переводами строк LF).
// После любой правки скрипта пересчитать здесь и в web/_headers;
// test_theme_script_hash_matches_csp печатает готовые строки на замену.
const THEME_SCRIPT_HASH = "sha256-r9sFmrG4AtWaMMR/0ZPmVcVV8FrIMEdZKQbZHcwNPd8=";

const CSP = [
  "default-src 'self'",
  `script-src 'self' '${THEME_SCRIPT_HASH}'`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

// Имена функций под /api/ — ровно файлы functions/api/*.js (тест сверяет).
const ALLOWED_API = new Set(["model"]);

export async function onRequest({ request, next }) {
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api")) {
    const rest = url.pathname.slice(4);
    const name = rest.startsWith("/") ? rest.slice(1) : rest;
    if (!ALLOWED_API.has(name)) {
      return harden(new Response(
        JSON.stringify({ error: "not found", requested: name.slice(0, 64) }),
        { status: 404, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } }));
    }
  }
  return harden(await next());
}

function harden(response) {
  const out = new Response(response.body, response);
  out.headers.set("content-security-policy", CSP);
  out.headers.set("x-content-type-options", "nosniff");
  out.headers.set("referrer-policy", "no-referrer");
  out.headers.set("permissions-policy", "geolocation=(), camera=(), microphone=()");
  return out;
}
