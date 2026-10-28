/* Модель 850 — витрина выпуска банка (контракт — поле `schema` выпуска).
 *
 * Правила (их держат tests/test_web_*.py и tests/web/*.mjs):
 *  • Числа и слова эмитента — только из выпуска `/api/model`: имя и тикеры —
 *    `meta.company`, подписи миров, режимов, сценариев, слоёв, нормативов и
 *    проверок — `name`/`title` выпуска. Литералов банка в этом файле нет.
 *  • Единственный пересчёт — ползунок λ: центр прогона = низ + λ·(верх − низ)
 *    по `fair_value.headline.low_draws`/`high_draws`, квантиль тип 7, печать
 *    половиной вверх к `print_step`; точка — `rates_view`. Прочее при λ ≠ книги —
 *    строки `by_lambda`. Остальное — форматирование и арифметика отображения.
 *  • Имя `d` — только выпуск: тест сверяет каждое `d.<блок>` с контрактом.
 *  • Блока нет — карточка так и пишет; упавший экран или график не роняет
 *    остальные. Графики — свой SVG в пикселях карточки; время — по Москве.
 */
"use strict";

const API = "/api/model";
// Дверь ждёт источник не дольше 8 с и отдаёт копию; витрина ждёт дверь не дольше этого.
const API_TIMEOUT_MS = 20000;
const STALE_HOURS = 96;
const SVG_NS = "http://www.w3.org/2000/svg";

let DATA = null;          // выпуск целиком
let LAMBDA = null;        // null — λ книги; число — положение ползунка
let CURRENT = "overview"; // открытый экран

/* ── DOM ── */

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  append(node, kids);
  return node;
}

function sv(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  append(node, kids);
  return node;
}

function setAttrs(node, attrs) {
  if (!attrs) return;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
    else if (key === "tip") setTip(node, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
}

// Замена содержимого узла: пустые дети (null, false) не превращаются в текст.
function fill(node, ...kids) { node.replaceChildren(); append(node, kids); }

const $ = (selector, root = document) => root.querySelector(selector);
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const cls = (...names) => names.filter(Boolean).join(" ") || null;
// Узел выпуска как объект или массив: чужой тип — пусто, а не исключение.
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});
const list = (x) => (Array.isArray(x) ? x : []);

function sentence(t) { return /[.!?…]$/.test(t) ? t : t + "."; }
function upperFirst(t) { const s = String(t); return s.charAt(0).toUpperCase() + s.slice(1); }
// Со строчной внутри фразы; аббревиатуры (ERP, CoR, ЧПМ) — как есть.
function lowerFirst(t) {
  const s = String(t);
  const word = s.split(/[\s(,:]/)[0];
  return s.length > 1 && !/[A-ZА-ЯЁ]/.test(word.slice(1)) && s[1] !== s[1].toUpperCase() ? s[0].toLowerCase() + s.slice(1) : s;
}
// Форма слова после числа, как оно напечатано: с дробной частью (`digits` > 0) — «1,6 года», целое — «1 год», «2 года», «5 лет».
function plural(n, [one, few, many], digits = 0) {
  if (digits > 0) return few;
  const a = Math.abs(Math.round(n)) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
}
const YEAR_WORDS = ["год", "года", "лет"];
// Срок в годах: знаков — сколько есть (до одного), слово — по напечатанному числу.
function yearsText(v) { const k = exactDigits(v, 1); return fmt.num(v, k) + NBSP + plural(v, YEAR_WORDS, k); }

/* ── числа и даты по-русски ── */

// Разряды — узкий неразрывный пробел, дробь — запятая, минус — настоящий.
const NBSP = String.fromCharCode(0x00a0);
const THIN = String.fromCharCode(0x202f);
const MINUS = String.fromCharCode(0x2212);
const SHY = String.fromCharCode(0x00ad);
const SPACES = new RegExp(`[${NBSP}${THIN} ]`, "g");
const FORMATS = new Map();

function numberFormat(digits) {
  if (!FORMATS.has(digits)) {
    FORMATS.set(digits, new Intl.NumberFormat("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: true }));
  }
  return FORMATS.get(digits);
}

// Время сборки и сделок — по Москве: у зрителя в другом поясе они не разъедутся.
const MSK = "Europe/Moscow";
const TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: MSK });
const DAY_MSK = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: MSK });
const MONTHS_SHORT = ["янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек."];
const MONTHS_AXIS = ["янв", "февр", "март", "апр", "май", "июнь", "июль", "авг", "сент", "окт", "нояб", "дек"];
const MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

// Дата без времени — календарный день; момент ISO — через Date.parse; свободный текст — не дата.
function parseDay(iso) {
  if (typeof iso !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso)) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : new Date(t);
}

// Календарный день момента по Москве.
function mskDay(iso) {
  const t = parseDay(iso);
  return t ? DAY_MSK.format(t) : null;
}

// Дней между календарными датами (отсчёт от даты оценки).
function daysBetween(fromIso, toIso) {
  const a = parseDay(fromIso), b = parseDay(toIso);
  if (!a || !b) return null;
  const utc = (x) => Date.UTC(x.getFullYear(), x.getMonth(), x.getDate());
  return Math.round((utc(b) - utc(a)) / 864e5);
}

const fmt = {
  num(x, digits = 0) {
    if (!isNum(x)) return "—";
    const k = 10 ** digits;
    const rounded = Math.round(x * k) / k;
    return numberFormat(digits).format(rounded === 0 ? 0 : rounded).replace(/-/g, MINUS).replace(SPACES, THIN);
  },
  signed(x, digits = 0) {
    if (!isNum(x)) return "—";
    const out = fmt.num(x, digits);
    return x > 0 && out !== fmt.num(0, digits) ? "+" + out : out;
  },
  rub(x, digits = 0) { return isNum(x) ? fmt.num(x, digits) + THIN + "₽" : "—"; },
  signedRub(x, digits = 0) { return isNum(x) ? fmt.signed(x, digits) + THIN + "₽" : "—"; },
  bn(x, digits = 1) { return isNum(x) ? fmt.num(x, digits) + NBSP + "млрд" + NBSP + "₽" : "—"; },
  signedBn(x, digits = 1) { return isNum(x) ? fmt.signed(x, digits) + NBSP + "млрд" + NBSP + "₽" : "—"; },
  pct(share, digits = 1) { return isNum(share) ? fmt.num(share * 100, digits) + THIN + "%" : "—"; },
  signedPct(share, digits = 1) { return isNum(share) ? fmt.signed(share * 100, digits) + THIN + "%" : "—"; },
  pp(share, digits = 2) { return isNum(share) ? fmt.signed(share * 100, digits) + NBSP + "п.п." : "—"; },
  bp(points, digits = 0) { return isNum(points) ? fmt.signed(points, digits) + NBSP + "б.п." : "—"; },
  x(multiple, digits = 2) { return isNum(multiple) ? fmt.num(multiple, digits) + "×" : "—"; },
  // Переменная дня здесь не `d`: `d.` — только выпуск.
  date(iso) {
    const day = parseDay(iso);
    return day ? `${String(day.getDate()).padStart(2, "0")}.${String(day.getMonth() + 1).padStart(2, "0")}.${day.getFullYear()}` : "—";
  },
  dateShort(iso) { const day = parseDay(iso); return day ? `${day.getDate()}${NBSP}${MONTHS_SHORT[day.getMonth()]}` : "—"; },
  // Часы — через Intl по Москве: часы зрителя читает только правило темы в index.html.
  time(iso) { const day = parseDay(iso); return day ? `${TIME_FORMAT.format(day)}${NBSP}МСК` : "—"; },
  stamp(iso) { return parseDay(iso) ? `${fmt.date(mskDay(iso))} ${fmt.time(iso)}` : "—"; },
  days(n) { return isNum(n) ? `${fmt.num(n)}${NBSP}${plural(n, ["день", "дня", "дней"])}` : "—"; },
};

// Малое число — мантисса и степень десяти: 1e-9 → «1·10⁻⁹».
function sci(x) {
  if (!isNum(x)) return "—";
  const [m, e] = x.toExponential(0).split("e");
  const sup = { "-": "⁻", "+": "", 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹" };
  return `${m.replace("-", MINUS)}·10${[...e].map((c) => sup[c]).join("")}`;
}

// Знаков у числа книги — сколько в нём есть (не больше max).
function exactDigits(v, max) {
  for (let k = 0; k < max; k++) if (Math.abs(Math.round(v * 10 ** k) - v * 10 ** k) < 1e-6) return k;
  return max;
}

// Подписи процентной оси — одним числом знаков на всю ось.
function pctTicks(marks) {
  const digits = Math.max(0, ...marks.map((t) => exactDigits(t * 100, 3)));
  return (t) => fmt.num(t * 100, digits) + THIN + "%";
}

// Значение в единице выпуска (`unit`, П§0.2): доля, сдвиг, млрд ₽, ₽, число, годы, словарь долей, дата, период; version — как есть.
function formatByUnit(value, unit, d) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return dictText(d, value);
  const u = String(unit || "");
  if (u === "period" || u === "date") return dayOrPeriod(value);
  if (typeof value === "string") return ruText(value);
  if (!isNum(value)) return "—";
  if (u === "pp") return value === 0 ? "без сдвига" : fmt.pp(value, Math.max(1, exactDigits(value * 100, 2)));
  if (u === "pct" || u === "share") return fmt.pct(value, Math.min(2, exactDigits(value * 100, 2)));
  if (u === "bn") return fmt.bn(value, Math.min(1, exactDigits(value, 1)));
  if (u === "rub") return fmt.rub(value, Math.min(2, exactDigits(value, 2)));
  if (u === "bp") return fmt.bp(value);
  if (u === "years") return yearsText(value);
  return fmt.num(value, exactDigits(value, 3));
}

// Словарь долей (веса миров, вероятности режимов) словами выпуска.
function dictText(d, dict) {
  const w = obj(dict);
  return Object.keys(w).filter((k) => isNum(w[k])).map((k) => `${keyName(d, k)} ${fmt.pct(w[k], 1)}`).join(" · ") || "—";
}

// Точка в числе — запятая; даты, ссылки (§4.11, п. 6.4), имена (Н20.0) и версии (book-1.6, «книга 1.1») остаются как есть.
const RU_DECIMAL = /\d{2}\.\d{2}\.\d{4}|§ ?\d+(?:\.\d+)+|(?<![А-яЁё])пп?\. ?\d+(?:\.\d+)+|[A-Za-zА-яЁё][A-Za-zА-яЁё\d]*-\d+(?:\.\d+)+|[A-Za-zА-яЁё]\d+\.\d+|(?:книг[а-яё]{1,3}|верси[а-яё]{1,2}) \d+(?:\.\d+)+(?:-[a-z\d]+)?(?: → \d+(?:\.\d+)+)?|\d+(?:\.\d+){2,}|(\d)\.(\d)/g;

// Свободный текст выпуска — с десятичной запятой, датами и периодами по-русски.
function ruText(t) {
  return String(t ?? "")
    .replace(/(^|[^0-9])(\d{4})-(\d{2})-(\d{2})(?![0-9])/g, "$1$4.$3.$2")
    .replace(/\b\d{4}(?:Q[1-4]|M(?:0[1-9]|1[0-2]))\b/g, periodLabel)
    .replace(RU_DECIMAL, (m, a, b) => (a === undefined ? m : `${a},${b}`))
    .replace(/(^|[\s(])-(?=\d)/g, `$1${MINUS}`)
    .replace(/(\d) ?%| (₽|п\.п\.|б\.п\.)/g, (m, a, b) => (a ? `${a}${THIN}%` : `${THIN}${b}`));
}

// Текст ядра словами выпуска: метки клеток «W/r/s» — миром, режимом и сценарием.
function wordsText(d, t) {
  return ruText(String(t ?? "").replace(/\b[A-Z]\/([a-z_]+)\/[a-z_]+\b/g, (m, r) => (obj(obj(d.regimes).rows)[r] ? cellName(d, m) : m)));
}

// Причина словами: адрес в ней — только имя сервера (запрос с датами читателю не нужен).
function reasonText(t) { return ruText(String(t ?? "").replace(/https?:\/\/([^\/\s?#:]+)[^\s:;,]*/g, "$1")); }

// Источник: имена файлов и пути — как есть, остальное — как ruText.
const FILE_TOKEN = /[^\s()«»,;:]*(?:\/[^\s()«»,;]*|\.(?:html?|pdf|xlsx?|json|csv|ya?ml|md|txt)(?![\p{L}\d]))/giu;
function srcText(t) {
  const kept = [];
  const masked = String(t ?? "")
    .replace(FILE_TOKEN, (m) => { kept.push(m); return `${String.fromCharCode(0xE100 + kept.length - 1)}`; });
  return ruText(masked).replace(/(.)/g, (m, c) => kept[c.charCodeAt(0) - 0xE100]);
}

/* ── подсказки ── */

const TIPS = new WeakMap();
let tipOwner = null;

function setTip(node, content) {
  TIPS.set(node, content);
  node.setAttribute("data-tip", "");
  if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "0");
}

function tipTarget(node) {
  while (node && node !== document) {
    if (node.nodeType === 1 && TIPS.has(node)) return node;
    node = node.parentNode;
  }
  return null;
}

function tipBody(content) {
  if (typeof content === "string") return [el("div", {}, content)];
  const out = [];
  if (content.title) out.push(el("div", { class: "tip-title" }, content.title));
  for (const [k, v] of content.rows || []) out.push(el("div", { class: "tip-row" }, el("span", { class: "k" }, k), el("span", { class: "v" }, v)));
  if (content.note) out.push(el("div", { class: "tip-note" }, content.note));
  return out;
}

function showTip(owner, x, y) {
  const box = $("#tip");
  if (!box) return;
  if (tipOwner && tipOwner !== owner) tipOwner.classList.remove("is-hot");
  tipOwner = owner;
  owner.classList.add("is-hot");
  box.replaceChildren(...tipBody(TIPS.get(owner)));
  box.hidden = false;
  placeTip(x, y);
}

function placeTip(x, y) {
  const box = $("#tip");
  if (!box || box.hidden) return;
  const pad = 12, w = box.offsetWidth, h = box.offsetHeight;
  let left = x + 14, top = y - h - 12;
  if (left + w > innerWidth - pad) left = Math.max(pad, x - w - 14);
  if (top < pad) top = Math.min(innerHeight - h - pad, y + 18);
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
}

function hideTip() {
  const box = $("#tip");
  if (box) box.hidden = true;
  if (tipOwner) tipOwner.classList.remove("is-hot");
  tipOwner = null;
}

function wireTips() {
  document.addEventListener("pointerover", (e) => { const o = tipTarget(e.target); if (o) showTip(o, e.clientX, e.clientY); });
  document.addEventListener("pointermove", (e) => { if (tipOwner) placeTip(e.clientX, e.clientY); });
  document.addEventListener("pointerout", (e) => { const o = tipTarget(e.target); if (o && !o.contains(e.relatedTarget)) hideTip(); });
  document.addEventListener("focusin", (e) => {
    const o = tipTarget(e.target);
    if (!o) return;
    const r = o.getBoundingClientRect();
    showTip(o, r.left + r.width / 2, r.top);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true });
}

/* ── графики: основа ── */

// График — функция ширины: draw(width) → <svg>, перерисовка по ResizeObserver.
const DRAWS = new WeakMap();
const SIZE = typeof ResizeObserver === "function"
  ? new ResizeObserver((entries) => {
    for (const entry of entries) {
      const host = entry.target;
      const width = Math.floor(entry.contentRect.width);
      if (width > 0 && String(width) !== host.dataset.w) { host.dataset.w = width; paint(host); }
    }
  })
  : null;

function chart(draw, labelText) {
  const host = el("div", { class: "chart", "aria-label": labelText || null });
  DRAWS.set(host, draw);
  if (SIZE) SIZE.observe(host);
  else requestAnimationFrame(() => { host.dataset.w = host.clientWidth || 640; paint(host); });
  return host;
}

function paint(host) {
  const draw = DRAWS.get(host);
  const width = Number(host.dataset.w);
  if (!draw || !width) return;
  try {
    host.replaceChildren(draw(width));
  } catch (error) {
    console.error(error);
    host.replaceChildren(el("p", { class: "empty broken" }, `График не отрисовался: ${error.message}`));
  }
}

function repaint(host) { if (host && host.dataset.w) paint(host); }

function svgBox(width, height, labelText) {
  return sv("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": labelText || null, focusable: "false" });
}

function scale(d0, d1, r0, r1) {
  const k = (r1 - r0) / ((d1 - d0) || 1);
  const f = (v) => r0 + (v - d0) * k;
  f.d = [d0, d1];
  return f;
}

function niceStep(span, count) {
  const raw = Math.abs(span) / Math.max(1, count);
  if (!raw) return 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 5 ? 5 : 10) * mag;
}

function ticks(d0, d1, count) {
  const step = niceStep(d1 - d0, count);
  const out = [];
  for (let v = Math.ceil(d0 / step - 1e-9) * step; v <= d1 + step * 1e-6; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

// Ширина подписи — тем же шрифтом, которым её нарисует SVG.
let MEASURE = null;
let FAMILY = null;
function textWidth(content, size = 12.5, weight = 500) {
  if (!MEASURE) {
    MEASURE = document.createElement("canvas").getContext("2d");
    FAMILY = getComputedStyle(document.body).fontFamily;
  }
  MEASURE.font = `${weight} ${size}px ${FAMILY}`;
  return MEASURE.measureText(String(content)).width;
}

function text(x, y, content, attrs = {}) { return sv("text", { x, y, ...attrs }, content); }
function line(x1, y1, x2, y2, attrs = {}) { return sv("line", { x1, y1, x2, y2, ...attrs }); }

// Подписи по ярусам без наездов: подпись встаёт в первый ярус, где слева свободно.
function stackLabels(items, gap = 8) {
  const rows = [];
  for (const item of items.sort((a, b) => a.x0 - b.x0)) {
    let row = rows.findIndex((right) => item.x0 >= right + gap);
    if (row < 0) { row = rows.length; rows.push(-Infinity); }
    rows[row] = item.x1;
    item.row = row;
  }
  return rows.length;
}

// Подпись с ореолом цвета карточки: читается поверх линий.
function label(x, y, content, attrs = {}) { return text(x, y, content, { ...attrs, class: cls("halo", attrs.class || "label") }); }

// Подпись оси по центру, прижатая к краям графика (последний квартал на телефоне не обрезается).
function axisText(x, y, s, W) {
  const w = textWidth(s, 12.5, 400);
  const anchor = x - w / 2 < 1 ? "start" : x + w / 2 > W - 1 ? "end" : "middle";
  return text(anchor === "start" ? 1 : anchor === "end" ? W - 1 : x, y, s, { class: "tick", "text-anchor": anchor });
}

// Подпись по центру точки, прижатая к краям графика.
function placed(x, content, W, size = 12.5, weight = 520) {
  const w = textWidth(content, size, weight);
  const x0 = Math.min(Math.max(2, x - w / 2), W - w - 2);
  return { x, text: content, w, x0, x1: x0 + w };
}

/* ── компоненты ── */

function card(opts, ...body) {
  const { title, sub, link, tools, span = 12, extra, id } = opts || {};
  const head = title || sub || tools || link
    ? el("div", { class: "card-head" },
      el("div", {}, title ? el("h2", {}, title) : null, sub ? el("p", { class: "sub" }, sub) : null),
      tools || null,
      link ? el("a", { class: "card-link", href: `#${link[0]}` }, link[1]) : null)
    : null;
  return el("section", { class: cls("card", `span-${span}`, extra), id: id || null }, head, ...body);
}

function empty(message) { return el("p", { class: "empty" }, message); }
// Подпись блока внутри карточки и строка-подвал карточки.
function tileLabel(...kids) { return el("span", { class: "tile-label" }, ...kids); }
function foot(...kids) { return el("p", { class: "card-foot" }, ...kids); }
function hint(...kids) { return el("span", { class: "hint" }, ...kids); }
function missing(what) { return empty(`В этом выпуске нет блока «${what}».`); }
function badge(t, kind) { return el("span", { class: cls("badge", kind && `badge-${kind}`) }, t); }
function detailsBlock(summary, body) { return el("details", {}, el("summary", {}, summary), el("div", { class: "detail-text" }, body)); }

// Столбцы таблицы: Nc — числовой, Tc — текстовый; cls — класс ячейки; text(row) — строка, где ячейка числового столбца несёт текст.
const Nc = (title, value, cls, text) => ({ title, value, cls, num: true, text });
const Tc = (title, value, cls) => ({ title, value, cls });

// Таблица данных; свободный текст строки — второй строкой (`opts.detail`).
function dataTable(columns, rows, opts = {}) {
  const cols = columns.filter(Boolean);
  const head = el("thead", {}, el("tr", {}, cols.map((c) => el("th", { class: c.num ? "num" : null, scope: "col" }, c.title))));
  const body = el("tbody");
  for (const row of rows) {
    const detail = opts.detail ? opts.detail(row) : null;
    const tr = el("tr", { class: cls(opts.rowClass && opts.rowClass(row), detail && "has-detail") });
    for (const c of cols) tr.append(el("td", { class: cls(c.num && !(c.text && c.text(row)) && "num", c.cls) }, c.value(row)));
    body.append(tr);
    if (detail) body.append(el("tr", { class: "detail" }, el("td", { colspan: cols.length }, detail)));
  }
  return el("div", { class: "scroll" }, el("table", { class: cls("data", opts.cls) }, opts.caption ? el("caption", {}, opts.caption) : null, head, body));
}

// График с таблицей-двойником: кнопка в шапке карточки меняет вид.
function withTable(chartNode, makeTable) {
  const box = el("div", { class: "fig" }, chartNode);
  let tableNode = null;
  const button = el("button", { class: "view-toggle", type: "button", "aria-pressed": "false" }, "Таблица");
  const refresh = () => {
    const open = button.getAttribute("aria-pressed") === "true";
    if (tableNode) tableNode.remove();
    tableNode = open ? el("div", { class: "fig-table" }, makeTable()) : null;
    if (tableNode) box.append(tableNode);
  };
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-pressed") === "true";
    if (!open) {
      tableNode = tableNode || el("div", { class: "fig-table" }, makeTable());
      chartNode.hidden = true;
      box.append(tableNode);
      button.textContent = "График";
      button.setAttribute("aria-pressed", "true");
    } else {
      chartNode.hidden = false;
      if (tableNode) tableNode.remove();
      button.textContent = "Таблица";
      button.setAttribute("aria-pressed", "false");
    }
  });
  return { box, button, refresh };
}

function kpi(value, labelText, opts = {}) {
  return el("div", { class: "kpi", tip: opts.tip || null },
    el("div", { class: "kpi-value" }, value, opts.unit ? el("span", { class: "unit" }, " " + opts.unit) : null),
    el("div", { class: "kpi-label" }, labelText));
}

function legend(items) {
  return el("div", { class: "legend" }, items.filter(Boolean).map(([key, name]) => el("span", {}, el("i", { class: cls("key", key) }), name)));
}

function screenHead(eyebrow, title, lede) {
  return el("header", { class: "screen-head" }, eyebrow ? el("span", { class: "eyebrow" }, eyebrow) : null, el("h1", {}, title), lede ? el("p", {}, lede) : null);
}

// Раздел экрана: надстрочник и сетка карточек.
function section(title, ...cards) {
  return el("div", { class: "section" }, title ? el("span", { class: "eyebrow row-label" }, title) : null, el("div", { class: "grid" }, ...cards));
}

// Второстепенные карточки длинного экрана: на телефоне — «ещё N карточек» одной кнопкой.
function more(cards) {
  const items = cards.filter(Boolean);
  const box = el("div", { class: "more" });
  const button = el("button", { class: "more-toggle", type: "button", "aria-expanded": "false" },
    `Ещё ${fmt.num(items.length)} ${plural(items.length, ["карточка", "карточки", "карточек"])}`);
  button.addEventListener("click", () => { box.classList.add("is-open"); button.setAttribute("aria-expanded", "true"); });
  box.append(button, el("div", { class: "more-body" }, el("div", { class: "grid section" }, items)));
  return box;
}

// Выбор одного из нескольких: aria-pressed у кнопок.
function chooser(options, current, onPick, labelText) {
  const group = el("div", { class: "chooser", role: "group", "aria-label": labelText });
  for (const opt of options) {
    const button = el("button", { type: "button", "aria-pressed": String(opt.value === current) }, opt.label, opt.hint ? el("span", { class: "w" }, opt.hint) : null);
    button.addEventListener("click", () => {
      for (const b of group.children) b.setAttribute("aria-pressed", String(b === button));
      onPick(opt.value);
    });
    group.append(button);
  }
  return group;
}

/* ── заголовок и λ ── */

// Квантиль тип 7 (линейная интерполяция), как ядро.
function quantile7(sorted, q) {
  const n = sorted.length;
  if (!n) return NaN;
  const h = (n - 1) * q;
  const lo = Math.floor(h);
  const hi = Math.min(lo + 1, n - 1);
  return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
}

// Печать — половиной вверх, как ядро (ROUND_HALF_UP); точность 1e-9 гасит шум деления.
function roundHalfUp(x) {
  return Math.floor(x + 0.5 + 1e-9);
}

function bookLambda(d) {
  const head = obj(d.fair_value.headline);
  return isNum(head.own_macro_confidence) ? head.own_macro_confidence
    : isNum(d.fair_value.own_macro_confidence) ? d.fair_value.own_macro_confidence : 0.5;
}

// Шаг ползунка — сетка λ выпуска (`lambda_step`), та же, что у таблиц `by_lambda`.
function lambdaStep(d) {
  const s = obj(d.fair_value.headline).lambda_step;
  return isNum(s) && s > 0 ? s : 0.05;
}

function lambdaNow(d) { return LAMBDA === null ? bookLambda(d) : LAMBDA; }
function atBookLambda(d) { return LAMBDA === null || Math.abs(LAMBDA - bookLambda(d)) < 1e-9; }

// Центры прогонов при λ: низ + λ·(верх − низ).
function centresAt(head, lam) {
  const n = Math.min(head.low_draws.length, head.high_draws.length);
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const lo = head.low_draws[i];
    out[i] = lo + lam * (head.high_draws[i] - lo);
  }
  return out;
}

// Медиана и полосы при λ: при λ книги — числа выпуска, иначе правило книги на прогонах.
function headlineAt(head, lam) {
  if (lam === null || Math.abs(lam - head.own_macro_confidence) < 1e-9) {
    return { median: head.median, printed_median: head.printed_median, band80: head.band80, printed_band80: head.printed_band80,
      band50: head.band50, printed_band50: head.printed_band50, release: true };
  }
  const c = centresAt(head, lam).sort((a, b) => a - b);
  const q = (p) => quantile7(c, p);
  const print = (v) => roundHalfUp(v / head.print_step) * head.print_step;
  const median = q(0.5);
  const band80 = [q(0.1), q(0.9)];
  const band50 = [q(0.25), q(0.75)];
  return { median, printed_median: print(median), band80, printed_band80: band80.map(print), band50, printed_band50: band50.map(print), release: false };
}

// Точка при значениях книги на оси ставок: низ + λ·(верх − низ).
function pointAt(d, lam) {
  const fv = d.fair_value;
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) return fv.central;
  const view = obj(fv.rates_view);
  return view.low + lam * (view.high - view.low);
}

function printedPoint(d, lam) {
  const fv = d.fair_value;
  const head = obj(fv.headline);
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) return isNum(head.printed_point) ? head.printed_point : fv.printed_central;
  return roundHalfUp(pointAt(d, lam) / head.print_step) * head.print_step;
}

// Строка таблицы выпуска при λ ползунка (`by_lambda`, шаг — сетка выпуска).
function atLambda(rows, lam) {
  if (!Array.isArray(rows) || !isNum(lam)) return null;
  return rows.find((r) => isNum(r.lambda) && Math.abs(r.lambda - lam) < 1e-9) || null;
}

// Строка при текущем λ: книга — сам блок, иначе строка `by_lambda` (или блок с пометкой «при λ книги»).
function lambdaRow(d, block) {
  const b = obj(block);
  if (atBookLambda(d)) return { row: b, where: "" };
  const at = atLambda(b.by_lambda, LAMBDA);
  return at ? { row: at, where: ` при λ = ${fmt.num(LAMBDA, 2)}` } : { row: b, where: " (при λ книги)" };
}

/* ── эмитент, периоды, подписи из выпуска ── */

// Имя и тикеры — из выпуска (`meta.company`): литералов эмитента в витрине нет.
function company(d) { return (d && obj(obj(d.meta).company).name) || "эмитент"; }
function tickers(d) { return list(obj(obj(d && d.meta).company).tickers).map(String); }
function ticker(d) { const c = obj(obj(d && d.meta).company); return c.main_ticker || tickers(d)[0] || "акция"; }
// Прочие категории акций — вторая линия рынка.
function others(d) { return tickers(d).filter((t) => t !== ticker(d)); }
function tickerList(d) { return tickers(d).length ? tickers(d).join(" · ") : ticker(d); }

// Слова единицы периода: модель — `meta.period_unit`, входы нау-каста — `meta.input_unit`.
const PERIOD_WORDS = {
  half: { one: "полугодие", gen: "полугодия", many: "полугодия", pgen: "полугодий", loc: "полугодии", dat: "полугодиям", ins: "полугодием" },
  quarter: { one: "квартал", gen: "квартала", many: "кварталы", pgen: "кварталов", loc: "квартале", dat: "кварталам", ins: "кварталом" },
  month: { one: "месяц", gen: "месяца", many: "месяцы", pgen: "месяцев", loc: "месяце", dat: "месяцам", ins: "месяцем" },
};
function unitWords(unit) { return PERIOD_WORDS[unit] || PERIOD_WORDS.quarter; }
function modelUnit(d) { return unitWords(obj(d.meta).period_unit); }
function inputUnit(d) { return unitWords(obj(d.meta).input_unit || "month"); }

// «2026Q3» → «3 кв. 2026», «2026M09» → «сентябрь 2026», год — как есть; короткая форма — для осей.
function periodLabel(id) {
  const s = String(id === null || id === undefined ? "" : id);
  let m = /^(\d{4})Q([1-4])$/.exec(s);
  if (m) return `${m[2]}${NBSP}кв.${NBSP}${m[1]}`;
  m = /^(\d{4})M(\d{2})$/.exec(s);
  if (m && +m[2] >= 1 && +m[2] <= 12) return `${MONTHS_NOM[+m[2] - 1]} ${m[1]}`;
  m = /^(\d{4})H([12])$/.exec(s);
  if (m) return `${m[2]}П${NBSP}${m[1]}`;
  return s || "—";
}
function periodShort(id) {
  const s = String(id === null || id === undefined ? "" : id);
  let m = /^\d\d(\d\d)Q([1-4])$/.exec(s);
  if (m) return `${m[2]}К’${m[1]}`;
  m = /^\d\d(\d\d)M(\d{2})$/.exec(s);
  if (m && +m[2] >= 1 && +m[2] <= 12) return `${MONTHS_AXIS[+m[2] - 1]}’${m[1]}`;
  return s;
}
// Месяц «2026M09» → «сентябрь», квартал «2026Q3» → «3 кв.» — внутри фразы.
function periodWord(id) {
  return periodLabel(id).replace(/\s\d{4}$/, "");
}
// Конец квартала на числовой оси лет: 2026Q4 → конец 2026 года.
function quarterX(id) {
  const m = /^(\d{4})Q([1-4])$/.exec(String(id));
  return m ? +m[1] - 1 + +m[2] / 4 : null;
}

const BASIS = { ifrs: "МСФО группы", ras: "РСБУ банка", mgmt: "упр.", engine: "движок", regulatory: "норматив ЦБ", sector: "прогноз сектора", market: "рынок" };
function basisName(b) { return BASIS[b] || b || ""; }
// Коды политики дивидендов (М§5.1) словами.
const CODE_WORDS = { ifrs_ni_shareholders: "ЧП группы по МСФО, приходящаяся на акционеров", ifrs_ni_adjusted: "ЧП группы по МСФО без неденежных статей",
  issued: "размещённые акции обеих категорий", residual_above_requirement: "остаток сверх требований", ceil_kopeck: "до копейки вверх", half_up_kopeck: "до копейки", none: "без округления" };
function codeText(x) { return x === true ? "да" : x === false ? "нет" : CODE_WORDS[x] || ruText(x); }
// Опорная ставка книги ЧПД: ключевая или узел кривой ОФЗ.
function refText(x) { const m = /^ofz_(\d+)y$/.exec(x); return m ? `ОФЗ ${m[1]}${NBSP}г.` : x === "key" ? "ключевая" : ruText(x); }

// Ключи миров, режимов, сценариев — словами выпуска.
function worldName(d, k) { return obj(obj(obj(d.worlds).rows)[k]).name || k; }
function regimeName(d, k) { return obj(obj(obj(d.regimes).rows)[k]).title || k; }
function scenarioName(d, k) { return obj(obj(obj(d.capital).scenarios)[k]).title || k; }
function layerTitle(d, k) { return obj(obj(d.layers)[k]).title || k; }
function capTitle(d, k) { return obj(obj(d.capital).titles)[k] || k; }
// Норматив по ключу метрики выпуска (`n20_0`…) — подпись из `capital.titles`, иначе как есть.
function metricTitle(d, m) {
  const k = { n20_0: "n20", n1_1: "n11" }[m] || m;
  return obj(obj(d.capital).titles)[k] || m || capTitle(d, "n20");
}
function keyName(d, k) {
  if (!d) return k;
  const w = obj(obj(d.worlds).rows)[k], r = obj(obj(d.regimes).rows)[k], s = obj(obj(d.capital).scenarios)[k];
  return (w && w.name) || (r && r.title) || (s && s.title) || k;
}
function regimeOrder(d) { return list(obj(d.regimes).order).length ? d.regimes.order : Object.keys(obj(obj(d.regimes).rows)); }
function scenarioOrder(d) { return list(obj(d.grid).scenario_order).length ? d.grid.scenario_order : Object.keys(obj(obj(d.capital).scenarios)); }
function worldOrder(d) { return list(obj(d.worlds).order).length ? d.worlds.order : Object.keys(obj(obj(d.worlds).rows)); }

// Значение за год из [{year, value}].
function byYear(rows, year) { const r = list(rows).find((x) => x.year === year); return r ? r.value : null; }

// Цвета режимов — ряды графиков (≥ 3 : 1 к карточке): мягкий, база, спад, кризис.
const SERIES = ["var(--model)", "var(--series-neutral)", "var(--third)", "var(--neg)", "var(--market)"];
const SERIES_KEYS = ["key-model", "key-neutral", "key-third", "key-neg", "key-market"];
const WORLD_COLORS = ["var(--model)", "var(--third)", "var(--market)"];
const WORLD_KEYS = ["key-line key-model", "key-line key-third", "key-line key-market"];

/* ── распределение (герой) ── */

// Плотность центров прогонов (ядро Гаусса с отражением у нуля), полосы, ящик P10–P90, медиана,
// линии рынка (главная категория — сплошная, прочие — пунктир), ромб точки.
function distributionChart(d) {
  const head = d.fair_value.headline;
  const markets = tickers(d).map((t) => ({ t, v: obj(head.market_by_ticker)[t], main: t === ticker(d) })).filter((m) => isNum(m.v));
  if (!markets.length && isNum(head.market)) markets.push({ t: ticker(d), v: head.market, main: true });
  return chart((W) => {
    const lam = lambdaNow(d);
    const centres = centresAt(head, lam).sort((a, b) => a - b);
    const hl = headlineAt(head, LAMBDA);
    const point = pointAt(d, LAMBDA);
    const main = markets.find((m) => m.main) || markets[0];
    const narrow = W < 520;
    const H = narrow ? 238 : 286;
    const m = { l: 10, r: 12, t: 40, b: 58 };
    const base = H - m.b;
    const hi = Math.max(quantile7(centres, 0.995), ...markets.map((x) => x.v * 1.12), hl.band80[1] * 1.08);
    const unit = niceStep(hi, 8);
    const xmax = Math.ceil(hi / unit) * unit;
    const x = scale(0, xmax, m.l, W - m.r);
    // Ширина ядра — правило Сильвермана: сглаживание картинки, а не число выпуска.
    const n = centres.length;
    const mean = centres.reduce((s, v) => s + v, 0) / n;
    const sd = Math.sqrt(centres.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, n - 1));
    const iqr = quantile7(centres, 0.75) - quantile7(centres, 0.25);
    const bw = Math.max(xmax / 200, 0.9 * Math.min(sd, iqr / 1.34 || sd) * n ** -0.2);
    const bins = 480, width = xmax / bins;
    const counts = new Float64Array(bins);
    for (const c of centres) if (c >= 0 && c < xmax) counts[Math.floor(c / width)] += 1;
    const density = (v) => {
      let sum = 0;
      for (let j = 0; j < bins; j++) {
        if (!counts[j]) continue;
        const c = (j + 0.5) * width, a = (v - c) / bw, b = (v + c) / bw;
        if (a > -5 && a < 5) sum += counts[j] * Math.exp(-0.5 * a * a);
        if (b > -5 && b < 5) sum += counts[j] * Math.exp(-0.5 * b * b);
      }
      return sum;
    };
    const samples = Math.max(90, Math.min(260, Math.round((W - m.l - m.r) / 2.5)));
    const grid = [];
    for (let i = 0; i <= samples; i++) { const v = (xmax * i) / samples; grid.push([v, density(v)]); }
    const ymax = Math.max(...grid.map((p) => p[1])) * 1.1 || 1;
    const y = scale(0, ymax, base, m.t);
    const areaOn = (a, b) => {
      const pts = [[a, density(a)], ...grid.filter((p) => p[0] > a && p[0] < b), [b, density(b)]];
      return `M${x(a)},${base} ` + pts.map(([v, f]) => `L${x(v).toFixed(1)},${y(f).toFixed(1)}`).join(" ") + ` L${x(b)},${base} Z`;
    };
    const svg = svgBox(W, H, "Распределение справедливой цены по суждениям книги");
    svg.append(
      sv("path", { d: areaOn(0, xmax), fill: "var(--model-wash-1)" }),
      sv("path", { d: areaOn(hl.band80[0], hl.band80[1]), fill: "var(--model-wash-2)" }),
      sv("path", { d: areaOn(hl.band50[0], hl.band50[1]), fill: "var(--model-wash-3)" }),
      sv("path", { d: "M" + grid.map(([v, f]) => `${x(v).toFixed(1)},${y(f).toFixed(1)}`).join(" L"), fill: "none", stroke: "var(--model)", "stroke-width": 2, "stroke-linejoin": "round" }),
      line(m.l, base, W - m.r, base, { class: "axisline" }));
    const ys = base + 20;
    svg.append(
      line(x(hl.band80[0]), ys, x(hl.band80[1]), ys, { stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.band80[0]), ys - 5, x(hl.band80[0]), ys + 5, { stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.band80[1]), ys - 5, x(hl.band80[1]), ys + 5, { stroke: "var(--model)", "stroke-width": 1.5 }),
      sv("rect", { x: x(hl.band50[0]), y: ys - 6, width: Math.max(2, x(hl.band50[1]) - x(hl.band50[0])), height: 12, rx: 3, fill: "var(--model-wash-3)", stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.median), ys - 8, x(hl.median), ys + 8, { stroke: "var(--ink)", "stroke-width": 2.5 }),
      line(x(hl.median), m.t - 4, x(hl.median), base, { stroke: "var(--ink)", "stroke-width": 1.5 }));
    for (const mk of markets.slice().sort((a, b) => a.main - b.main)) {
      svg.append(line(x(mk.v), m.t - 4, x(mk.v), ys + 10, { stroke: "var(--market)", "stroke-width": mk.main ? 2 : 1.6, "stroke-dasharray": mk.main ? null : "4 3" }));
    }
    const px = x(point);
    svg.append(sv("path", { d: `M${px},${base - 6} L${px + 6},${base} L${px},${base + 6} L${px - 6},${base} Z`, fill: "var(--surface)", stroke: "var(--ink)", "stroke-width": 1.6 }));
    const marketLeft = main && main.v < hl.median;
    const tops = [
      { x: x(hl.median), text: `медиана ${fmt.rub(hl.printed_median)}`, pref: marketLeft ? "right" : "left" },
      main ? { x: x(main.v), text: `${main.t} ${fmt.rub(main.v, 2)}`, pref: marketLeft ? "left" : "right" } : null,
    ].filter(Boolean).map((t) => {
      const w = textWidth(t.text, 13, 640);
      let x0 = t.pref === "left" ? t.x - w - 6 : t.x + 6;
      if (x0 < 2) x0 = t.x + 6;
      if (x0 + w > W - 2) x0 = t.x - w - 6;
      return { ...t, w, x0, x1: x0 + w };
    });
    const rows = stackLabels(tops, 10);
    for (const t of tops) svg.append(label(t.x0, 14 + t.row * 16 + (rows === 1 ? 6 : 0), t.text, { class: "label-strong" }));
    const pointText = `точка ${fmt.rub(printedPoint(d, LAMBDA))}`;
    const pw = textWidth(pointText, 12.5, 520);
    let plx = px + 9;
    if (plx + pw > W - 2) plx = px - pw - 9;
    svg.append(label(plx, base - 9, pointText));
    const marks = ticks(0, xmax, narrow ? 4 : 7);
    marks.forEach((t, i) => {
      const tx = x(t);
      const anchor = t === 0 ? "start" : tx > W - 34 ? "end" : "middle";
      svg.append(line(tx, ys + 14, tx, ys + 18, { class: "axisline" }),
        text(anchor === "end" ? W - 1 : tx, H - 6, t === 0 ? "0" : fmt.num(t) + (i === marks.length - 1 ? THIN + "₽" : ""), { class: "tick", "text-anchor": anchor }));
    });
    const hot = (x0, w, tip) => sv("rect", { class: "hit", x: x0, y: m.t - 8, width: w, height: ys + 12 - (m.t - 8), tip });
    svg.append(
      hot(x(hl.band50[0]), Math.max(6, x(hl.band50[1]) - x(hl.band50[0])), { title: "Полоса 50 % (P25–P75)",
        rows: [["от", fmt.rub(hl.printed_band50[0])], ["до", fmt.rub(hl.printed_band50[1])]],
        note: hl.release ? "числа выпуска" : "пересчитано ползунком λ из прогонов выпуска" }),
      hot(x(hl.median) - 8, 16, { title: "Медиана по суждениям книги", rows: [["печать", fmt.rub(hl.printed_median)], ["точно", fmt.rub(hl.median, 2)]] }),
      ...markets.map((mk) => hot(x(mk.v) - 6, 12, { title: "Рыночная цена", rows: markets.map((q) => [q.t, fmt.rub(q.v, 2)]),
        note: `на ${fmt.date(obj(d.meta).valuation_date)}` })),
      hot(px - 8, 16, { title: "Точка при значениях книги", rows: [["печать", fmt.rub(printedPoint(d, LAMBDA))], ["точно", fmt.rub(point, 2)]] }));
    return svg;
  }, "Распределение справедливой цены: медиана, полосы 80 и 50 процентов, рынок и точка");
}

/* ── ряд «диапазон книги → что нужно рынку» ── */

function rangeRowChart(row) {
  return chart((W) => {
    const H = 34;
    const [r0, r1] = row.range;
    const vals = [r0, r1, row.book].concat(isNum(row.solved) ? [row.solved] : []);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 0.01;
    lo -= pad; hi += pad;
    const x = scale(lo, hi, 8, W - 8);
    const svg = svgBox(W, H);
    const cy = H / 2;
    svg.append(line(8, cy, W - 8, cy, { class: "gridline" }),
      line(x(r0), cy, x(r1), cy, { stroke: "var(--model-wash-3)", "stroke-width": 8, "stroke-linecap": "round" }),
      line(x(row.book), cy - 8, x(row.book), cy + 8, { stroke: "var(--ink)", "stroke-width": 2 }));
    if (isNum(row.solved)) svg.append(sv("circle", { cx: Math.max(8, Math.min(W - 8, x(row.solved))), cy, r: 6, fill: "var(--market)", stroke: "var(--surface)", "stroke-width": 2 }));
    svg.append(sv("rect", { class: "hit", x: 0, y: 0, width: W, height: H, tip: { title: row.name,
      rows: [["нужно рынку", isNum(row.solved) ? reverseValue(row, row.solved) : "недостижимо"], ["в книге", reverseValue(row, row.book, true)],
        ["диапазон книги", reverseRange(row)]] } }));
    return svg;
  }, `${row.name}: диапазон книги и значение, при котором медиана равна рынку`);
}

// Диапазон книги у строки обратного расчёта; у строки без оси (концы равны) печатать нечем.
function reverseRange(r) {
  const [a, b] = list(r.range);
  return isNum(a) && isNum(b) && a !== b ? `${reverseValue(r, a, true)} … ${reverseValue(r, b, true)}` : "—";
}

// Значение оси обратного расчёта в её единице (`unit` выпуска).
function reverseValue(row, value, bookSide = false) {
  if (!isNum(value)) return "—";
  const u = row.unit;
  if (u === "pp") return bookSide && value === 0 ? "0" : fmt.pp(value, bookSide ? exactDigits(value * 100, 2) : 2);
  if (u === "number") return fmt.num(value, bookSide ? exactDigits(value, 3) : 3);
  if (u === "mix") return fmt.pct(value, 0);
  if (u === "bn") return fmt.bn(value, 1);
  return fmt.num(value * 100, bookSide ? exactDigits(value * 100, 2) : 2) + THIN + "%";
}

/* ── гантели, столбцы, линии, полосы ── */

function dumbbellChart(rows, opts = {}) {
  const f = opts.fmt || ((v) => fmt.num(v, 2));
  return chart((W) => {
    const rowH = 46;
    const H = rows.length * rowH + 24;
    const values = rows.flatMap((r) => [r.a, r.b]).concat(opts.extra || []).filter(isNum);
    const lo = Math.min(...values), hi = Math.max(...values);
    const pad = (hi - lo) * 0.2 || Math.abs(hi) * 0.1 || 1;
    const x = scale(lo - pad, hi + pad, 8, W - 8);
    const svg = svgBox(W, H);
    for (const t of ticks(lo - pad, hi + pad, W < 420 ? 3 : 6)) {
      const s = f(t), half = textWidth(s, 12.5, 400) / 2;
      svg.append(line(x(t), 0, x(t), H - 20, { class: "gridline" }));
      if (x(t) - half >= 0 && x(t) + half <= W) svg.append(text(x(t), H - 4, s, { class: "tick", "text-anchor": "middle" }));
    }
    for (const v of opts.refs || []) svg.append(line(x(v), 0, x(v), H - 20, { stroke: "var(--ink-2)", "stroke-width": 1.2, "stroke-dasharray": "3 3" }));
    rows.forEach((r, i) => {
      const cy = i * rowH + 30;
      svg.append(label(8, cy - 13, r.name));
      if (isNum(r.a) && isNum(r.b)) svg.append(line(x(r.a), cy, x(r.b), cy, { stroke: "var(--axis)", "stroke-width": 2 }));
      for (const [v, color, name] of [[r.a, "var(--model)", r.aLabel], [r.b, "var(--market)", r.bLabel]]) {
        if (isNum(v)) svg.append(sv("circle", { cx: x(v), cy, r: 6, fill: color, stroke: "var(--surface)", "stroke-width": 2, tip: { title: `${r.name}: ${name}`, rows: [[name, f(v)]] } }));
      }
      if (isNum(r.a) && isNum(r.b)) {
        const left = Math.min(r.a, r.b), right = Math.max(r.a, r.b);
        svg.append(label(x(left) - 10, cy + 4, f(left), { "text-anchor": "end" }), label(x(right) + 10, cy + 4, f(right)));
      }
    });
    return svg;
  }, opts.label);
}

// Столбцы по порядку: items [{key, value, label, tipTitle, neg?}]; opts.refs — опорные линии.
function columnsChart(items, opts = {}) {
  return chart((W) => {
    const H = opts.height || 220;
    const m = { l: 8, r: 8, t: 22, b: 26 };
    const values = items.map((i) => i.value).filter(isNum);
    const refs = (opts.refs || []).filter((r) => isNum(r.value));
    const vmax = Math.max(...values, ...refs.map((r) => r.value), 1e-9) * 1.14;
    const vmin = Math.min(0, ...values) * 1.14;
    const y = scale(vmin, vmax, H - m.b, m.t);
    const band = (W - m.l - m.r) / Math.max(1, items.length);
    const bw = Math.min(28, band * 0.56);
    const widest = Math.max(...items.map((it) => textWidth(it.label || String(it.key), 12.5, 400)));
    const every = Math.max(1, Math.ceil((widest + 8) / band));
    const svg = svgBox(W, H, opts.label);
    for (const t of ticks(vmin, vmax, 4)) svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }));
    items.forEach((it, i) => {
      const cx = m.l + band * (i + 0.5);
      // Ноль — не пропуск: подпись «0» над осью и цель подсказки; `null` не рисуется.
      if (isNum(it.value)) {
        const top = y(Math.max(0, it.value)), bottom = y(Math.min(0, it.value));
        const tip = { title: it.tipTitle || String(it.key), rows: [[opts.valueName || "значение", opts.fmt ? opts.fmt(it.value) : fmt.num(it.value, 1)]].concat(it.tipRows || []) };
        svg.append(it.value === 0 ? sv("rect", { class: "hit", x: cx - bw / 2, y: top - 22, width: bw, height: 22, tip })
          : sv("rect", { x: cx - bw / 2, y: top, width: bw, height: Math.max(1, bottom - top), rx: 3,
            fill: it.color || opts.color || "var(--model)", opacity: it.faded ? 0.55 : null, tip }));
        const s = opts.short ? opts.short(it.value) : fmt.num(it.value, 0);
        if (s && textWidth(s, 12.5, 520) < band - 2) svg.append(label(cx, it.value >= 0 ? top - 6 : bottom + 14, s, { "text-anchor": "middle" }));
      }
      if ((items.length - 1 - i) % every === 0) svg.append(axisText(cx, H - 8, it.label || String(it.key), W));
    });
    svg.append(line(m.l, y(0), W - m.r, y(0), { class: "axisline" }));
    for (const ref of refs) {
      svg.append(line(m.l, y(ref.value), W - m.r, y(ref.value), { stroke: ref.color || "var(--market)", "stroke-width": 1.5, "stroke-dasharray": ref.dash || null }),
        label(W - m.r, y(ref.value) - 6, ref.text, { "text-anchor": "end" }));
    }
    return svg;
  }, opts.label);
}

// Линии на числовой или порядковой оси X. series: [{name, color, points [[x, y]], dots, dashed | dash,
// hollow, width, area [[x, lo, hi]]}]; opts: xType "band" + categories, xTicks, xFmt, yFmt | yPct,
// hrefs [{value, text, color, dash}], vlines [{x, text, color, tip}], height. Линия идёт по непрерывным
// участкам: через нераскрытое (`null`) не проводится, одиночная точка — точкой.
function linesChart(series, opts = {}) {
  return chart((W) => {
    const H = opts.height || 240;
    const m = { l: opts.left || 46, r: opts.right || 16, t: opts.top || 18, b: 28 };
    const all = series.flatMap((s) => s.points).filter((p) => isNum(p[1]));
    const areas = series.flatMap((s) => (s.area || []).flatMap((p) => [p[1], p[2]]));
    const refs = (opts.hrefs || []).filter((r) => isNum(r.value));
    const bandKeys = opts.xType === "band" ? opts.categories : null;
    const yvals = all.map((p) => p[1]).concat(areas, refs.map((r) => r.value)).filter(isNum);
    if (!yvals.length) return svgBox(W, 20);
    let y0 = opts.yMin ?? Math.min(...yvals);
    let y1 = opts.yMax ?? Math.max(...yvals);
    const pad = (y1 - y0) * 0.1 || Math.abs(y1) * 0.1 || 1;
    if (opts.yMin === undefined) y0 -= pad;
    if (opts.yMax === undefined) y1 += pad;
    const yMarks = ticks(y0, y1, opts.yTicks || 4);
    const yLabel = opts.yPct ? pctTicks(yMarks) : opts.yFmt || ((t) => fmt.num(t));
    m.l = Math.max(m.l, Math.ceil(Math.max(0, ...yMarks.map((t) => textWidth(yLabel(t), 12.5, 400)))) + 12);
    let x;
    if (bandKeys) {
      const step = (W - m.l - m.r) / bandKeys.length;
      x = (k) => { const i = bandKeys.indexOf(k); return i < 0 ? NaN : m.l + step * (i + 0.5); };
    } else {
      const xs = all.map((p) => p[0]).concat(series.flatMap((s) => (s.area || []).map((p) => p[0])));
      const x0 = opts.xMin ?? Math.min(...xs), x1 = opts.xMax ?? Math.max(...xs);
      x = scale(x0, x1, m.l, W - m.r);
    }
    const vItems = [];
    for (const v of opts.vlines || []) {
      const vx = x(v.x);
      if (!isNum(vx) || !v.text) continue;
      const w = textWidth(v.text, 12.5, 520);
      const x0 = vx + 5 + w > W - m.r ? vx - 5 - w : vx + 5;
      vItems.push({ text: v.text, x0, x1: x0 + w });
    }
    const vRows = stackLabels(vItems, 8);
    const y = scale(y0, y1, H - m.b, m.t + Math.max(0, vRows) * 16);
    const svg = svgBox(W, H, opts.label);
    for (const t of yMarks) {
      svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }), text(m.l - 8, y(t) + 4, yLabel(t), { class: "tick", "text-anchor": "end" }));
    }
    if (bandKeys) {
      const step = (W - m.l - m.r) / bandKeys.length;
      const widest = Math.max(...bandKeys.map((k) => textWidth(opts.xFmt ? opts.xFmt(k) : k, 12.5, 400)));
      const every = Math.max(1, Math.ceil((widest + 10) / step));
      bandKeys.forEach((k, i) => {
        if ((bandKeys.length - 1 - i) % every === 0) svg.append(axisText(x(k), H - 8, opts.xFmt ? opts.xFmt(k) : String(k), W));
      });
    } else {
      // Подписи справа налево: встаёт только не задевающая соседнюю.
      let left = Infinity;
      for (const t of (opts.xTicks || ticks(x.d[0], x.d[1], W < 420 ? 4 : 7)).slice().sort((a, b) => b - a)) {
        const s = opts.xFmt ? opts.xFmt(t) : fmt.num(t);
        const tx = x(t), w = textWidth(s, 12.5, 400);
        const anchor = tx - w / 2 < 0 ? "start" : tx + w / 2 > W ? "end" : "middle";
        const x0 = anchor === "start" ? Math.max(0, tx - 2) : anchor === "end" ? W - 1 - w : tx - w / 2;
        if (x0 + w + 8 > left) continue;
        left = x0;
        svg.append(text(anchor === "end" ? W - 1 : anchor === "start" ? Math.max(0, tx - 2) : tx, H - 8, s, { class: "tick", "text-anchor": anchor }));
      }
    }
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    for (const s of series) {
      if (!s.area || s.area.length < 2) continue;
      const pts = s.area.filter((p) => isNum(x(p[0])) && isNum(p[1]) && isNum(p[2]));
      const up = pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[2]).toFixed(1)}`);
      const down = pts.slice().reverse().map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`);
      if (up.length > 1) svg.append(sv("path", { d: `M${up.join(" L")} L${down.join(" L")} Z`, fill: s.areaFill || "var(--model-wash-2)" }));
    }
    for (const ref of refs) svg.append(line(m.l, y(ref.value), W - m.r, y(ref.value), { stroke: ref.color, "stroke-width": 1.5, "stroke-dasharray": ref.dash || null }));
    for (const v of opts.vlines || []) {
      const vx = x(v.x);
      if (isNum(vx)) svg.append(line(vx, m.t - 6, vx, H - m.b, { stroke: v.color || "var(--ink-2)", "stroke-width": 1.2, "stroke-dasharray": v.dash || "3 3", tip: v.tip || null }));
    }
    for (const s of series) {
      const ok = (p) => isNum(p[1]) && isNum(x(p[0]));
      const pts = s.points.filter(ok);
      let run = [];
      for (const p of s.line === false ? [] : [...s.points, null]) {
        if (p && ok(p)) { run.push(p); continue; }
        if (run.length > 1) {
          svg.append(sv("path", { d: "M" + run.map((q) => `${x(q[0]).toFixed(1)},${y(q[1]).toFixed(1)}`).join(" L"),
            fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round", "stroke-dasharray": s.dash || (s.dashed ? "5 4" : null) }));
        } else if (run.length && !s.dots) svg.append(sv("circle", { cx: x(run[0][0]), cy: y(run[0][1]), r: 2.5, fill: s.color }));
        run = [];
      }
      if (s.dots) {
        for (const p of pts) {
          svg.append(sv("circle", { cx: x(p[0]), cy: y(p[1]), r: s.r || 4.5, fill: s.hollow ? "var(--surface)" : s.color, stroke: s.hollow ? s.color : "var(--surface)", "stroke-width": 2,
            tip: { title: `${s.name}: ${opts.xFmt ? opts.xFmt(p[0]) : p[0]}`, rows: [[s.name, opts.tipFmt ? opts.tipFmt(p[1]) : fmt.num(p[1], 2)]] } }));
        }
      }
    }
    // Подписи опорных линий: близкие по высоте расходятся.
    const refLabels = refs.filter((r) => r.text).map((r) => ({ r, y: y(r.value) + (r.below ? 16 : -6) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < refLabels.length; i++) if (refLabels[i].y - refLabels[i - 1].y < 15) refLabels[i].y = refLabels[i - 1].y + 15;
    for (const { r, y: ly } of refLabels) svg.append(label(W - m.r, Math.min(ly, H - m.b - 4), W < 480 && r.short ? r.short : r.text, { "text-anchor": "end" }));
    for (const t of vItems) svg.append(label(t.x0, m.t + 6 + t.row * 16, t.text));
    return svg;
  }, opts.label);
}

// Горизонтальные полосы в HTML (водопад, торнадо): подписи переносятся сами.
function barTrack(segments, domain, opts = {}) {
  const [d0, d1] = domain;
  const pos = (v) => (100 * (v - d0)) / ((d1 - d0) || 1);
  const track = el("div", { class: "bt-track" });
  if (isNum(opts.center)) track.append(el("i", { class: "bt-center", style: `left:${pos(opts.center)}%` }));
  for (const seg of segments) {
    const a = Math.min(seg.from, seg.to), b = Math.max(seg.from, seg.to);
    track.append(el("i", { class: cls("bt-bar", seg.cls), style: `left:${pos(a)}%;width:${Math.max(0.4, pos(b) - pos(a))}%`, tip: seg.tip || null }));
  }
  for (const mark of opts.marks || []) track.append(el("i", { class: cls("bt-mark", mark.cls), style: `left:${pos(mark.at)}%`, tip: mark.tip || null }));
  return track;
}

// Водопад строк: [{title, from, to, value, total?, text}] на общей дорожке.
function waterfall(steps, wide = false) {
  const lo = Math.min(0, ...steps.map((s) => Math.min(s.from, s.to)));
  const hi = Math.max(...steps.map((s) => Math.max(s.from, s.to)));
  return el("div", { class: cls("wf", wide && "wide") }, steps.map((s) => el("div", { class: cls("wf-row", s.total && "is-total") },
    el("span", { class: "wf-name" }, s.title, s.hint ? hint(s.hint) : null),
    el("span", { class: "wf-bar" }, barTrack([{ from: s.from, to: s.to, cls: s.total ? "is-total" : s.to < s.from ? "is-down" : "is-up",
      tip: { title: s.title, rows: [["значение", s.text]] } }], [lo, hi])),
    el("span", { class: "wf-val" }, s.text))));
}

// Полоса доли: имя, дорожка, число.
function hbar(name, share, max, opts = {}) {
  return el("div", { class: "hbar", tip: opts.tip || null },
    el("span", { class: "hbar-name" }, name),
    el("span", { class: "hbar-track" }, el("i", { class: "hbar-fill", style: `width:${Math.max(0, (100 * share) / (max || 1))}%${opts.color ? `;background:${opts.color}` : ""}` })),
    el("span", { class: "hbar-val" }, opts.value || fmt.pct(share, share < 0.1 ? 1 : 0)));
}

// Линия ряда в плитке: история {date [], value []} — дни или периоды; мин и макс — выпуска (за окно истории), иначе по точкам.
function sparkline(item, valueText, span = "за год") {
  const h = obj(item.history);
  const pts = list(h.date).map((dt, i) => ({ date: dt, value: list(h.value)[i] })).filter((p) => isNum(p.value));
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p.value);
  const lo = isNum(obj(item.min).value) ? item.min.value : Math.min(...vals);
  const hi = isNum(obj(item.max).value) ? item.max.value : Math.max(...vals);
  const plot = chart((W) => {
    const H = 40;
    const x = scale(0, pts.length - 1, 3, W - 5);
    const y = hi > lo ? scale(lo, hi, H - 5, 5) : () => H / 2;
    const svg = svgBox(W, H, `История ряда «${item.title}»`);
    svg.append(sv("polyline", { points: pts.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" "),
      fill: "none", stroke: "var(--model)", "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = pts[pts.length - 1];
    svg.append(sv("circle", { cx: x(pts.length - 1), cy: y(last.value), r: 3, fill: "var(--model)",
      tip: { title: item.title, rows: [[dayOrPeriod(pts[0].date), valueText(pts[0].value)], [dayOrPeriod(last.date), valueText(last.value)]] } }));
    return svg;
  }, `История ряда «${item.title}»`);
  return el("div", { class: "spark" }, plot, el("span", { class: "muted small" }, `${span}: мин ${valueText(lo)} · макс ${valueText(hi)}`));
}

// Отметки на одной шкале: kind model (крупная синяя), market, bench (бирюза), guide (оранжевая черта), band — полоса.
function stripChart(marks, opts = {}) {
  return chart((W) => {
    const vals = marks.map((mk) => mk.v).concat(opts.band || []).filter(isNum);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.05 || 0.002;
    const x = scale(lo - pad, hi + pad, 10, W - 10);
    const cy = 26;
    const labelled = marks.filter((mk) => mk.text && isNum(mk.v)).map((mk) => ({ ...placed(x(mk.v), mk.text, W, 12.5, mk.kind === "model" ? 640 : 520), strong: mk.kind === "model" }));
    const rows = stackLabels(labelled, 10);
    const H = cy + 22 + rows * 16 + 20;
    const svg = svgBox(W, H, opts.label);
    svg.append(line(10, cy, W - 10, cy, { class: "gridline" }));
    if (opts.band && isNum(opts.band[0]) && isNum(opts.band[1])) {
      svg.append(sv("rect", { x: x(opts.band[0]), y: cy - 9, width: Math.max(3, x(opts.band[1]) - x(opts.band[0])), height: 18, rx: 6,
        fill: opts.bandFill || "var(--model-wash-2)", stroke: opts.bandStroke || "var(--model)", "stroke-width": 1, tip: opts.bandTip || null }));
    }
    const order = { neutral: 0, bench: 1, guide: 2, market: 3, model: 4 };
    for (const mk of marks.slice().sort((a, b) => (order[a.kind] ?? 0) - (order[b.kind] ?? 0))) {
      if (!isNum(mk.v)) continue;
      const mx = x(mk.v);
      if (mk.kind === "neutral") svg.append(line(mx, cy - 12, mx, cy + 12, { stroke: "var(--ink-2)", "stroke-width": 1.4, "stroke-dasharray": "2 2", tip: mk.tip || null }));
      else if (mk.kind === "guide") svg.append(line(mx, cy - 13, mx, cy + 13, { stroke: "var(--market)", "stroke-width": 2.2, tip: mk.tip || null }));
      else if (mk.kind === "bench") svg.append(sv("circle", { cx: mx, cy, r: 5, fill: "var(--third)", stroke: "var(--surface)", "stroke-width": 2, tip: mk.tip || null }));
      else if (mk.kind === "market") svg.append(sv("circle", { cx: mx, cy, r: 6.5, fill: "var(--market)", stroke: "var(--surface)", "stroke-width": 2, tip: mk.tip || null }));
      else svg.append(sv("circle", { cx: mx, cy, r: 7, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2, tip: mk.tip || null }));
    }
    for (const t of labelled) svg.append(label(t.x0, cy + 30 + t.row * 16, t.text, { class: t.strong ? "label-strong" : "label" }));
    const tickMarks = ticks(lo - pad, hi + pad, W < 420 ? 3 : 5);
    const tickLabel = opts.pct ? pctTicks(tickMarks) : opts.fmt || ((t) => fmt.num(t));
    for (const t of tickMarks) {
      const s = tickLabel(t), w = textWidth(s, 12.5, 400);
      if (x(t) - w / 2 >= 0 && x(t) + w / 2 <= W) svg.append(text(x(t), H - 3, s, { class: "tick", "text-anchor": "middle" }));
    }
    return svg;
  }, opts.label);
}

/* ── экран «Оценка» ── */

function screenOverview(d) {
  const root = el("div", { class: "screen" });
  root.append(hero(d));
  root.append(passportRow(d));
  root.append(section("Почему такая оценка", pricedTeaser(d), bandDrivers(d)));
  root.append(section("Что дальше", reportTeaser(d), dividendsTeaser(d)));
  root.append(section("", eventsCard(d, 6), changesCard(d)));
  root.append(section("", methodNote(d)));
  return root;
}

// Слова диагностик — по числу, для которого их посчитал выпуск (`target`).
function diagWords(target) {
  return target === "median"
    ? { subj: "медиана", acc: "медиану", gen: "медианы" }
    : { subj: "точка", acc: "точку", gen: "точки" };
}

// Почему точка не равна медиане: оси с наибольшим сдвигом положения (`mean_shift`, М§10).
function pointVsMedianText(d, lam) {
  const point = pointAt(d, lam);
  const median = headlineAt(obj(d.fair_value.headline), lam).median;
  if (!isNum(point) || !isNum(median) || Math.abs(point - median) < 0.5) return "Точка совпадает с медианой.";
  const above = point > median;
  const pulls = list(obj(d.judgements).rows).filter((j) => j.in_band !== false && isNum(j.mean_shift) && j.mean_shift !== 0 && (j.mean_shift < 0) === above)
    .sort((a, b) => Math.abs(b.mean_shift) - Math.abs(a.mean_shift)).slice(0, 2);
  const detail = pulls.length
    ? ` Сильнее всего прогоны ${above ? "вниз" : "вверх"} от точки уводят ${pulls.map((j) => `${lowerFirst(shortAxis(j.name))} (${fmt.signedRub(j.mean_shift, 1)})`).join(" и ")}`
      + ` — вклад в «среднее прогонов − точка», первый порядок${atBookLambda(d) ? "" : ", при λ книги"}.`
    : "";
  return `Точка ${above ? "выше" : "ниже"} медианы: диапазоны суждений асимметричны.${detail}`;
}

function shortAxis(name) { return String(name || "").replace(/\s*\(.*?\)\s*/g, " ").trim(); }

// Ось-словарь (вероятности режимов, веса миров): её концы — тоже словари.
function isDict(j) { return !!j && [j.book, j.low, j.high].some((v) => v && typeof v === "object"); }

// Диапазон суждения книги в его единице; сдвиг — в пунктах со знаком; ось-словарь — пары концов по ключам
// («Кризис 10,0 → 25,0 %»; порядок концов — как у точки на краях; нет конца — «—»), коротко — ключ с наибольшим ходом.
function rangeText(d, j, short = false) {
  if (!j) return "—";
  if (isDict(j)) {
    const lo = obj(j.low), hi = obj(j.high);
    const end = (v, pct) => (isNum(v) ? (pct ? fmt.pct(v, 1) : fmt.num(v * 100, 1)) : "—");
    const keys = Object.keys(obj(j.book)).concat(Object.keys(lo), Object.keys(hi)).filter((k, i, a) => a.indexOf(k) === i);
    const pairs = keys.map((k) => ({ move: Math.abs(hi[k] - lo[k]) || 0, text: `${keyName(d, k)} ${end(lo[k], !isNum(hi[k]))} → ${end(hi[k], true)}` }));
    if (!short || pairs.length < 2) return pairs.map((x) => x.text).join(" · ") || "—";
    return pairs.slice().sort((a, b) => b.move - a.move)[0].text + " · …";
  }
  if (j.unit === "pp") { const k = Math.max(1, exactDigits(j.low * 100, 2), exactDigits(j.high * 100, 2)); return `${fmt.pp(j.low, k)} … ${fmt.pp(j.high, k)}`; }
  return `${formatByUnit(j.low, j.unit, d)} … ${formatByUnit(j.high, j.unit, d)}`;
}

// P(ниже рынка) — одним правилом: от 1 % — целые, меньше — два знака.
function pBelowText(p) { return fmt.pct(p, isNum(p) && p > 0 && p < 0.01 ? 2 : 0); }

function hero(d) {
  const fv = d.fair_value;
  const head = obj(fv.headline);
  if (!list(head.low_draws).length || !list(head.high_draws).length) return heroWithoutBand(d);
  const markets = obj(head.market_by_ticker);
  const main = ticker(d);
  const view = obj(fv.rates_view);
  // Оси полосы — суждения `in_band`, включая оси-словари (вклада у них нет).
  const axes = list(obj(d.judgements).rows).filter((j) => j.in_band === true).length || list(head.contributions).length;
  const copy = el("div", { class: "hero-copy", id: "fv-hero" });
  const tiles = el("div", { class: "tiles", id: "fv-tiles" });
  const plot = distributionChart(d);
  const table = () => {
    const hl = headlineAt(head, LAMBDA);
    const { row, where } = lambdaRow(d, head);
    return dataTable([
      Tc("Величина", (r) => r[0], "name"),
      Nc("Точно", (r) => r[1]),
      Nc("Печать", (r) => r[2]),
    ], [
      ["P10 — нижний край полосы 80 %", fmt.rub(hl.band80[0], 2), fmt.rub(hl.printed_band80[0])],
      ["P25 — нижний край полосы 50 %", fmt.rub(hl.band50[0], 2), fmt.rub(hl.printed_band50[0])],
      ["Медиана", fmt.rub(hl.median, 2), fmt.rub(hl.printed_median)],
      ["P75 — верхний край полосы 50 %", fmt.rub(hl.band50[1], 2), fmt.rub(hl.printed_band50[1])],
      ["P90 — верхний край полосы 80 %", fmt.rub(hl.band80[1], 2), fmt.rub(hl.printed_band80[1])],
      ["Точка при значениях книги", fmt.rub(pointAt(d, LAMBDA), 2), fmt.rub(printedPoint(d, LAMBDA))],
      ["Среднее прогонов" + where, fmt.rub(row.mean, 2), "—"],
      ...tickers(d).map((t) => [`Рынок, ${t}`, fmt.rub(markets[t], 2), "—"]),
      ...tickers(d).map((t) => [`P(центр ниже ${t})${where}`, pBelowText(obj(row.p_below_by_ticker)[t]), "—"]),
    ], { caption: atBookLambda(d) ? "Числа выпуска" : `λ = ${fmt.num(LAMBDA, 2)}: квантили и точка — пересчёт из прогонов выпуска, остальное — таблица выпуска для этого λ` });
  };
  const fig = withTable(plot, table);
  const out = el("output", { class: "lambda-out", for: "lambda" });
  const reset = el("button", { class: "lambda-reset", type: "button", hidden: true }, "вернуть λ книги");
  const slider = el("input", { id: "lambda", type: "range", min: "0", max: "1", step: String(lambdaStep(d)),
    value: String(bookLambda(d)), "aria-label": "Вес собственного взгляда на инфляцию и ставки, λ" });
  const lambdaBox = el("div", { class: "lambda" },
    el("div", { class: "lambda-top" }, el("span", { class: "lambda-title" }, "Взгляд на инфляцию и ставки"), out),
    el("div", { class: "lambda-ends" },
      el("span", {}, el("i", { class: "key key-market" }), layerTitle(d, "macro_neutral")),
      el("span", {}, layerTitle(d, "analytical"), el("i", { class: "key key-model" }))),
    slider,
    el("p", { class: "lambda-note" },
      isNum(view.low) ? `Ось ставок — не интервал, а вклад собственного взгляда: низ ${fmt.rub(view.low)} — слой «${layerTitle(d, "macro_neutral")}», верх ${fmt.rub(view.high)} — «${layerTitle(d, "analytical")}». ` : "",
      "Ползунок пересчитывает полосу из прогонов выпуска; прочие числа — таблица выпуска на шаге ползунка. ", reset));
  const chartCard = el("section", { class: "card hero-chart" },
    el("div", { class: "card-head" },
      el("div", {}, el("h2", {}, "Распределение справедливой цены"),
        el("p", { class: "sub" }, `${fmt.num(head.draws)} прогонов по ${fmt.num(axes)} ${plural(axes, ["суждению", "суждениям", "суждениям"])} книги в их диапазонах`)),
      fig.button),
    fig.box,
    legend([["key-b80", "80 % прогонов"], ["key-b50", "50 %"], ["key-line key-ink", "медиана"], isNum(markets[main]) && ["key-line key-market", `рынок, ${main}`],
      ...others(d).filter((t) => isNum(markets[t])).map((t) => ["key-line key-market2", `рынок, ${t}`]), ["key-diamond", "точка при значениях книги"]]),
    lambdaBox);

  const update = () => {
    const lam = LAMBDA;
    const hl = headlineAt(head, lam);
    const { row, where } = lambdaRow(d, head);
    const pBelow = row.p_below_market;
    const percentile = row.market_percentile;
    const upside = obj(row.upside);
    copy.replaceChildren(
      el("span", { class: "eyebrow" }, `Справедливая стоимость акции · ${tickerList(d)} · ${fmt.date(d.meta.valuation_date)}`),
      el("div", { class: "hero-figure", id: "fv-headline" },
        el("span", { class: "hero-approx" }, "≈"),
        el("span", { class: "hero-value", id: "kpi-central" }, fmt.num(hl.printed_median)),
        el("span", { class: "hero-unit" }, "₽")),
      el("p", { class: "hero-caption" }, `медиана по суждениям книги ${d.meta.book_version || ""}`,
        el("span", { class: "muted" }, ` · точно ${fmt.rub(hl.median, 2)}${atBookLambda(d) ? "" : ` · при λ = ${fmt.num(lam, 2)}`}`)),
      el("div", { class: "upside", id: "fv-upside" }, tickers(d).map((t) => el("span", {}, `к ${t} `, el("strong", {}, fmt.signedPct(upside[t], 0)))),
        where ? el("span", { class: "muted" }, where.trim()) : null),
      el("div", { class: "bands" },
        el("div", { class: "band-row" }, el("i", { class: "band-key b80" }), el("span", { class: "what" }, "полоса 80 %"),
          el("span", { class: "range" }, `${fmt.num(hl.printed_band80[0])}–${fmt.num(hl.printed_band80[1])}${THIN}₽`)),
        el("div", { class: "band-row" }, el("i", { class: "band-key b50" }), el("span", { class: "what" }, "полоса 50 %"),
          el("span", { class: "range" }, `${fmt.num(hl.printed_band50[0])}–${fmt.num(hl.printed_band50[1])}${THIN}₽`))),
      el("div", { class: "verdict", id: "fv-verdict" },
        el("div", { class: "verdict-top" },
          el("span", { class: "verdict-num" }, pBelowText(pBelow)),
          el("span", { class: "verdict-title" }, `вероятность, что справедливая цена ниже рыночной цены ${main} — ${fmt.rub(markets[main], 2)}`)),
        el("div", { class: "meter", role: "img", "aria-label": `P(ниже рынка) ${pBelowText(pBelow)}` },
          el("i", { style: `width:${Math.max(0, Math.min(100, (pBelow || 0) * 100))}%` })),
        el("span", { class: "verdict-note" }, `по ${fmt.num(head.draws)} прогонам`,
          isNum(percentile) ? ` · рынок — на ${fmt.num(percentile * 100, percentile > 0 && percentile < 0.01 ? 2 : 0)}-м перцентиле распределения центра` : "",
          others(d).map((t) => ` · ${t}: ${pBelowText(obj(row.p_below_by_ticker)[t])}`).join(""), where)));
    tiles.replaceChildren(...heroTiles(d, lam));
    out.value = atBookLambda(d) ? `λ = ${fmt.num(bookLambda(d), 2)} · книга` : `λ = ${fmt.num(lam, 2)} · точка ${fmt.rub(printedPoint(d, lam))}`;
    reset.hidden = atBookLambda(d);
  };
  slider.addEventListener("input", () => {
    const v = Number(slider.value);
    LAMBDA = Math.abs(v - bookLambda(d)) < 1e-9 ? null : v;
    update();
    repaint(plot);
    fig.refresh();
  });
  reset.addEventListener("click", () => {
    LAMBDA = null;
    slider.value = String(bookLambda(d));
    update();
    repaint(plot);
    fig.refresh();
    slider.focus();
  });
  if (LAMBDA !== null) slider.value = String(LAMBDA);
  update();
  return el("div", {}, el("div", { class: "hero" }, copy, chartCard), tiles);
}

// Плитки языка банка: P/B и избыточная доходность, цена ROE, точка, вклад ставок.
function heroTiles(d, lam) {
  const fv = d.fair_value;
  const out = [];
  const bfl = obj(fv.bank_first_line);
  const { row, where } = lambdaRow(d, bfl);
  const w = diagWords(bfl.target);
  if (isNum(row.fair_pb_median)) {
    out.push(el("section", { class: "card tile" },
      tileLabel("Капитал: модель против рынка"),
      el("span", { class: "tile-value" }, fmt.x(row.fair_pb_median, 2), el("span", { class: "unit" }, `P/B против ${fmt.x(row.market_pb, 2)}`)),
      el("p", { class: "tile-note" }, `Справедливый P/B (${w.subj}) против рыночного${where}. PV доходности сверх стоимости капитала: модель ${fmt.signedBn(row.excess_median, 0)}, `
        + `рынок ${fmt.signedBn(row.excess_market, 0)} (капитализация ${fmt.num(obj(d.market).cap, 0)} минус капитал ${fmt.num(row.bv_v, 0)}).`)));
    out.push(el("section", { class: "card tile" },
      tileLabel("Цена 1 п.п. ROE сквозь цикл"),
      el("span", { class: "tile-value" }, fmt.num(row.rub_per_1pp_roe), el("span", { class: "unit" }, "₽ на акцию")),
      el("p", { class: "tile-note" }, `ROE сквозь цикл ${fmt.pct(row.roe_tc, 1)} против стоимости капитала ${fmt.pct(row.k_tc, 1)}${where}; `
        + `0,1${NBSP}п.п. CoR — ${fmt.signedRub(row.rub_per_01pp_cor)}, 0,1${NBSP}п.п. ЧПМ — ${fmt.signedRub(row.rub_per_01pp_nim)}. Оценки — по срединным прогонам, до рубля.`)));
  }
  out.push(el("section", { class: "card tile" },
    tileLabel("Точка при значениях книги"),
    el("span", { class: "tile-value" }, fmt.num(printedPoint(d, lam)), el("span", { class: "unit" }, "₽")),
    el("p", { class: "tile-note", id: "fv-lede" }, `Все суждения — значения книги: точно ${fmt.rub(pointAt(d, lam), 2)}. ${pointVsMedianText(d, lam)}`)));
  const view = obj(fv.rates_view);
  if (isNum(view.rub)) {
    out.push(el("section", { class: "card tile" },
      tileLabel("Вклад взгляда на ставки"),
      el("span", { class: "tile-value" }, fmt.signed(view.rub, 0), el("span", { class: "unit" }, "₽")),
      el("p", { class: "tile-note" }, `${upperFirst(layerTitle(d, "macro_neutral"))} — ${fmt.rub(view.low, 2)}, ${layerTitle(d, "analytical")} — ${fmt.rub(view.high, 2)}. Это ось выбора слоя, а не интервал неопределённости.`)));
  }
  return out;
}

// Выпуск без прогонов: крупно — точка; полосы нет, и витрина не притворяется, что она есть.
function heroWithoutBand(d) {
  const fv = d.fair_value;
  const market = obj(obj(d.market).prices)[ticker(d)];
  const price = obj(market).price;
  return el("div", { class: "hero" },
    el("div", { class: "hero-copy", id: "fv-hero" },
      el("span", { class: "eyebrow" }, `Справедливая стоимость акции · ${tickerList(d)} · ${fmt.date(obj(d.meta).valuation_date)}`),
      el("div", { class: "hero-figure", id: "fv-headline" }, el("span", { class: "hero-approx" }, "≈"),
        el("span", { class: "hero-value", id: "kpi-central" }, fmt.num(fv.printed_central)), el("span", { class: "hero-unit" }, "₽")),
      el("p", { class: "hero-caption" }, "точка при значениях книги", el("span", { class: "muted" }, ` · точно ${fmt.rub(fv.central, 2)}`)),
      el("div", { class: "verdict" }, el("div", { class: "verdict-top" }, el("span", { class: "verdict-num" }, fmt.rub(price, 2)),
        el("span", { class: "verdict-title" }, `рыночная цена ${ticker(d)}`)),
      el("span", { class: "verdict-note" }, "В выпуске нет прогонов по суждениям: медианы и полосы нет, крупно печатается точка."))),
    card({ title: "Точка на оси ставок", extra: "hero-chart" }, el("div", { class: "kpis" },
      kpi(fmt.rub(fv.low, 2), layerTitle(d, "macro_neutral")), kpi(fmt.rub(fv.central, 2), "точка"), kpi(fmt.rub(fv.high, 2), layerTitle(d, "analytical")))));
}

// Требование к нормативу — порог дивидендной политики или пол с буфером; пол года — у смеси заголовка.
function reqWords(d) {
  const C = obj(d.capital);
  const policy = isNum(C.policy_threshold) && Math.abs(obj(C.anchor).req20_now - C.policy_threshold) < 1e-9;
  const tail = policy ? " дивидендной политики" : " дивиденда (пол + буфер)";
  const day = parseDay(obj(d.meta).valuation_date);
  return { name: (policy ? "порог" : "требование") + tail, to: (policy ? "порога" : "требования") + tail,
    floor: list(obj(C.mix).floor20)[day ? list(C.years).indexOf(day.getFullYear()) : -1] };
}

// Паспорт одной строкой: мультипликаторы, ROE, дивдоходность, запас норматива, следующий DPS.
function passportRow(d) {
  const req = reqWords(d);
  const mk = obj(obj(d.market).multiples);
  const ltm = obj(obj(d.history).ltm);
  const anchor = obj(obj(d.capital).anchor);
  const next = obj(obj(d.dividends).next_expected);
  const main = ticker(d);
  const record = next.record_date ? fmt.date(next.record_date) : next.record_date_est ? `≈${NBSP}${fmt.date(next.record_date_est)}` : "—";
  const items = [
    [fmt.x(mk.pb, 2), "P/B к капиталу на дату оценки", { title: "P/B", rows: [["на дату оценки", fmt.x(mk.pb, 3)], ["к последнему отчёту", fmt.x(mk.pb_reported, 3)]] }],
    [`${fmt.x(mk.pe_ltm, 1)} / ${fmt.x(mk.pe_fwd, 1)}`, "P/E за 12 мес. / вперёд", null],
    [fmt.pct(ltm.roe, 1), `ROE за 12 мес. (${basisName(ltm.basis)})`, null],
    [fmt.pct(obj(mk.dividend_yield_fwd)[main], 1), `дивдоходность вперёд, ${main}`,
      { title: "Дивдоходность вперёд", rows: tickers(d).map((t) => [t, fmt.pct(obj(mk.dividend_yield_fwd)[t], 2)]) }],
    [fmt.pp(anchor.n20_headroom, 1), `запас ${capTitle(d, "n20")} до ${req.to}`,
      { title: capTitle(d, "n20"), rows: [["после дивиденда", fmt.pct(anchor.n20_post_dividend, 2)], [req.name, fmt.pct(anchor.req20_now, 2)], ["регуляторный пол года", fmt.pct(req.floor, 2)]],
        note: "запас — до требования дивиденда, не до пола" }],
    [fmt.rub(next.dps, 2), `следующий DPS (${next.status === "model" ? "по политике" : (DIV_STATUS[next.status] || [next.status])[0]}), отсечка ${record}`,
      { title: `Дивиденд за ${next.year || "—"}`, rows: [["по политике", fmt.rub(next.dps_policy, 2)], ["в среднем по клеткам", fmt.rub(next.dps_mean, 2)], ["риск отмены", fmt.pct(next.p_cancel, 0)]] }],
  ];
  return el("div", { class: "card passport", id: "passport" }, items.map(([v, t, tip]) => el("div", { class: "pp", tip: tip || null },
    el("div", { class: "pp-value" }, v), el("div", { class: "pp-label" }, t))));
}

// Заголовок «Что в цене»: ближайшее к книге решение в диапазоне и доля пути к краю.
function pricedHeadline(rows) {
  const share = (r) => {
    const end = r.solved >= r.book ? r.range[1] : r.range[0];
    return end === r.book ? 0 : (r.solved - r.book) / (end - r.book);
  };
  const inside = rows.filter(inRange).map((r) => ({ r, s: share(r) })).sort((a, b) => a.s - b.s);
  if (!rows.length) return "Что заложено в рыночную цену";
  if (!inside.length) return "Ни одно суждение в пределах книги не объясняет рыночную цену";
  return `Рыночную цену объясняет суждение внутри диапазона книги: ${lowerFirst(shortAxis(inside[0].r.name))} — ${fmt.pct(inside[0].s, 0)} пути к краю`;
}

function inRange(r) { return r.status === "solved" && isNum(r.solved) && !!r.in_range; }
function reverseBadge(r) {
  return r.status === "not_computed" ? badge("не считалось", "out") : !isNum(r.solved) || r.status !== "solved" ? badge("недостижимо", "out")
    : r.in_range ? badge("в диапазоне книги", "good") : badge("вне диапазона", "out");
}
// Решение оси словами: число, «недостижимо» или «не считалось» (быстрая сборка).
function reverseNeed(r) { return r.status === "not_computed" ? "не считалось" : isNum(r.solved) && r.status === "solved" ? reverseValue(r, r.solved) : "недостижимо"; }

function pricedTeaser(d) {
  const block = obj(d.reverse_dcf);
  const rows = list(block.rows);
  if (!rows.length) return card({ title: "Что заложено в цену", span: 6, link: ["market", "Подробно"] }, missing("обратный расчёт"));
  const inside = rows.filter(inRange);
  const outside = rows.filter((r) => !inRange(r));
  const roe = list(block.bank_rows).find((r) => r.key === "implied_roe_through_cycle");
  const w = diagWords(block.number);
  return card({ title: "Что заложено в цену", span: 6, link: ["market", "Все оси"],
    sub: `Значение одного суждения, при котором ${w.subj} равна цене ${ticker(d)} (${fmt.rub(block.target, 2)}); прочие разыгрываются как в полосе.` },
  inside.length ? el("div", { class: "rows" }, inside.map((r) => el("div", { class: "rrow" },
    el("div", { class: "rrow-name" }, r.name), rangeRowChart(r),
    el("div", { class: "rrow-need" }, el("span", { class: "v" }, reverseValue(r, r.solved)),
      el("span", { class: "b" }, `книга ${reverseValue(r, r.book, true)} · в диапазоне книги`)))))
    : empty("Ни одно суждение в пределах своего диапазона не даёт рыночную цену."),
  roe ? el("p", { class: "note-box", style: "margin-top:12px" }, `Рынку нужен ROE сквозь цикл ${fmt.pct(roe.implied, 1)} против ${fmt.pct(roe.book, 1)} книги.`) : null,
  outside.length ? foot("Вне диапазона книги: ",
    outside.map((r) => `${lowerFirst(shortAxis(r.name))} ${isNum(r.solved) && r.status === "solved" ? reverseNeed(r) : `— ${reverseNeed(r)}`}`).join("; "), ".") : null);
}

// Доля вклада в полосу: ниже 1 % — «< 1 %» без порядка (погрешность ≈ 0,5 п.п., М§10).
const SHARE_FLOOR = 0.01;
const SHARE_SMALL = `<${NBSP}1${THIN}%`;
function shareText(share) { return !isNum(share) ? "—" : share < SHARE_FLOOR ? SHARE_SMALL : fmt.pct(share, share < 0.1 ? 1 : 0); }
// Оси вне полосы (`in_band: false`) стоят на значении книги.
function offBand(d) { return list(obj(d.judgements).rows).filter((j) => j.in_band === false); }
// Мера осей вне полосы — как у правила М§10: сдвиг заголовка от их фиксации и порог гейта.
function offBandNote(d) {
  const s = obj(obj(d.judgements).off_band_shift);
  return isNum(s.rub) ? `их фиксация сдвигает заголовок на ${fmt.signedRub(s.rub, 1)} при пороге ${fmt.rub(s.limit, 1)}` : "не разыгрываются";
}

function bandDrivers(d) {
  const head = obj(d.fair_value.headline);
  const rows = list(head.contributions);
  if (!rows.length) return card({ title: "Что определяет полосу", span: 6 }, missing("вклады суждений в полосу"));
  const judg = Object.fromEntries(list(obj(d.judgements).rows).map((j) => [j.id, j]));
  const big = rows.filter((r) => isNum(r.share) && r.share >= SHARE_FLOOR).sort((a, b) => b.share - a.share);
  const small = rows.filter((r) => !big.includes(r)).map((r) => lowerFirst(shortAxis(r.name))).sort((a, b) => a.localeCompare(b, "ru"));
  const off = offBand(d).map((j) => lowerFirst(shortAxis(j.name)));
  const max = Math.max(...big.map((r) => r.share), SHARE_FLOOR);
  const bar = (r) => {
    const j = judg[r.judgement_key];
    return hbar(r.name, r.share, max, { value: shareText(r.share), tip: { title: r.name, rows: [["доля полосы", shareText(r.share)], ["ранговая корреляция", fmt.num(r.rank_corr, 2)],
      ...(j ? [["диапазон книги", rangeText(d, j)]] : [])], note: r.rank_corr < 0 ? "больше значение — ниже цена" : "больше значение — выше цена" } });
  };
  const rest = big.slice(6);
  return card({ title: "Что определяет полосу", span: 6, link: ["book", "Все суждения"],
    sub: `Вклад суждения — доля квадрата ранговой корреляции с центром по ${fmt.num(head.draws)} прогонам; доли меньше 1 % не различимы и не упорядочены.` },
  el("div", { class: "hbars" }, big.slice(0, 6).map(bar)),
  rest.length || small.length || off.length ? el("div", { class: "card-foot stack" },
    rest.length ? detailsBlock(`Ещё ${rest.length} ${plural(rest.length, ["суждение", "суждения", "суждений"])}`, el("div", { class: "hbars" }, rest.map(bar))) : null,
    small.length ? el("p", { class: "muted small" }, sentence(`Вклад ${SHARE_SMALL}: ${small.join(", ")}`)) : null,
    off.length ? el("p", { class: "muted small" }, sentence(`Вне полосы (${offBandNote(d)}): ${off.join(", ")}`)) : null) : null);
}

// Отсчёт до события от даты оценки.
function countdown(days, what) {
  return el("div", { class: "countdown" }, el("span", { class: "big" }, fmt.num(days)),
    el("span", { class: "ink-2" }, `${plural(days, ["день", "дня", "дней"])} — ${what}`));
}

// МСФО, закрывающее открытый квартал, вышло, а факт в книге ещё нет.
function reportPublished(d) {
  const c = obj(obj(d.next_report).closing);
  return el("p", { class: "note-box" }, el("strong", {}, `Отчёт вышел ${c.confirmed ? "" : "≈" + NBSP}${fmt.date(c.date)}; факт ещё не внесён в модель. `),
    c.title ? sentence(`По календарю — ${lowerFirst(c.title)}`) : "", " До новой версии книги ожидание модели и «что даст отчёт» считаются без этого факта.");
}

// Месяц РСБУ раньше МСФО: вход нау-каста, к цене не подключён.
function earlierReport(d) {
  const ras = obj(obj(d.calendar).next_ras);
  if (!ras.month) return null;
  // Дата формы ЦБ — оценка по прошлым публикациям: печатается окном, а не точной датой.
  const form = ras.enters_via === "form102";
  return foot(`Раньше МСФО: ${form ? `≈${NBSP}${fmt.date(ras.date)}${parseDay(ras.form102_latest_est) ? `–${fmt.date(ras.form102_latest_est)}` : ""}` : fmt.date(ras.date)} — РСБУ банка за ${periodLabel(ras.month)} `,
    `(${form ? "по форме 0409102, дата — оценка" : "релиз, принятый по двум источникам"}): вход нау-каста; к цене нау-каст не подключён.`);
}

function nextRasLine(d) {
  const ras = obj(obj(d.calendar).next_ras);
  return isNum(ras.days) ? countdown(ras.days, `РСБУ за ${periodLabel(ras.month)} попадёт в нау-каст`) : null;
}

function nextFactLine(d) {
  const nf = obj(obj(d.calendar).next_fact);
  if (obj(obj(d.next_report).closing).published) return reportPublished(d);
  return isNum(nf.days) ? countdown(nf.days, `МСФО за ${periodLabel(nf.covers || obj(d.next_report).period)} (${nf.confirmed ? "" : "≈" + NBSP}${fmt.date(nf.date)})`) : null;
}

// Интервал DPS года: `null` — ошибка прибыли года не оценена (нет σ ожидания у оставшихся кварталов, П§2).
function dpsInterval(y, digits) {
  const [lo, hi] = list(y.dps_interval);
  return isNum(lo) && isNum(hi) ? `интервал ${fmt.num(lo, digits)}–${fmt.num(hi, digits)}${THIN}₽` : "интервал не оценён";
}

function reportTeaser(d) {
  const nr = obj(d.next_report);
  const year = obj(obj(d.nowcast).year);
  const n = obj(obj(nr.neutral).cor);
  return card({ title: `Ближайший отчёт: ${periodLabel(nr.period)}`, span: 6, link: ["report", "Подробно"], sub: "два ритма: месячная РСБУ банка и квартальная МСФО группы" },
    nextRasLine(d), nextFactLine(d),
    isNum(year.ni_year) ? el("div", { class: "kpis", style: "margin-top:14px" },
      kpi(fmt.bn(year.ni_year, 0), `прибыль ${year.year}: факт + смесь заголовка с поправкой нау-каста`),
      kpi(fmt.rub(year.dps, 2), `DPS за ${year.year} с нау-кастом, ${dpsInterval(year, 1)}`)) : null,
    list(nr.cor_table).length ? el("div", { style: "margin-top:14px" },
      tileLabel(`Если CoR ${periodLabel(nr.period)} (упр.) выйдет …, ${diagWords(nr.target).subj} станет:`),
      impactChart(d, "cor", true)) : null,
    isNum(n.value) ? foot(neutralSentence(d, "cor")) : null);
}

// Нейтральное значение: число выпуска не меняется; наклон — местный, рядом размах реакции; эквивалент — уровень ЧП квартала.
function neutralSentence(d, kind) {
  const nr = obj(d.next_report);
  const n = obj(obj(nr.neutral)[kind]);
  const slope = obj(nr.slope)[`rub_per_01pp_${kind}`];
  const re = obj(obj(nr.reaction)[kind]);
  const ni = obj(nr.expectation).ni;
  const what = kind === "cor" ? "CoR" : "ЧПМ";
  if (!isNum(n.value)) return obj(nr.neutral).error ? sentence(`Нейтральное значение ${what} не найдено: ${ruText(nr.neutral.error)}`) : "";
  const w = diagWords(nr.target);
  const dir = kind === "cor" ? ["снизится", "вырастет"] : ["вырастет", "снизится"];
  return sentence(`Нейтральная ${what} ${periodLabel(nr.period)} (упр.) — ${fmt.pct(n.value, 2)}: при таком факте ${w.subj} не изменится; выше — ${dir[0]}, ниже — ${dir[1]}`
    + (isNum(slope) ? `; у ${obj(nr.slope)[`at_${kind}`] === "expectation" ? "ожидания" : "нейтрального значения"} — около ${fmt.rub(Math.abs(slope), Math.abs(slope) < 10 ? 1 : 0)} на 0,1${NBSP}п.п.` : "")
    + (isNum(re.d_median_min) && isNum(re.d_median_max) ? `, дальше реакция упирается в предел сдвига режимов: от ${fmt.signed(re.d_median_min, 1)} до ${fmt.signedRub(re.d_median_max, 1)}` : "")
    + (isNum(n.ni_equivalent) ? `; при ней прибыль квартала ≈${NBSP}${fmt.bn(n.ni_equivalent, 1)}${isNum(ni) ? ` (ожидание модели — ${fmt.num(ni, 1)})` : ""}` : ""));
}

/* ── дивиденды (кратко) ── */

const DIV_STATUS = { declared: ["объявлен", "warn"], recommended: ["рекомендован", "model"], paid: ["выплачен", "good"], model: ["модель", null] };

function divStatus(r) {
  const [name, kind] = DIV_STATUS[r.status] || [r.status || "—", null];
  return badge(name, kind);
}
function registerCutoff(r) { return fmt.date(r.ex_date || r.record_date); }
// Ближайший дивиденд: главная цифра — DPS реестра или политики; риск отмены и среднее по клеткам — подписью.
function nextDpsLabel(next) {
  return next.status === "model" ? `DPS по политике · риск отмены ${fmt.pct(next.p_cancel, 0)}`
    : { declared: "объявленный DPS", recommended: "рекомендованный DPS (до собрания)" }[next.status] || "DPS реестра";
}
function nextDpsMean(next) {
  return isNum(next.dps_mean) ? hint(`в среднем по клеткам ${fmt.rub(next.dps_mean, 2)}, P10–P90 ${fmt.num(next.dps_p10, 1)}–${fmt.num(next.dps_p90, 1)}${THIN}₽`) : null;
}
// Источники записи реестра: `declared` — при двух (М§15).
function registerSources(r) {
  const s = list(r.sources);
  return el("span", {}, s.length ? s.map(ruText).join(" + ") : "—",
    el("span", { class: "cell-badge" }, s.length >= 2 ? badge("два источника", "good") : badge("один источник", r.status === "paid" ? "out" : "warn")));
}
function nextRecordText(next) {
  return next.record_date ? fmt.date(next.record_date) : parseDay(next.record_date_est) ? `≈${NBSP}${fmt.date(next.record_date_est)}` : ruText(next.record_date_note || "—");
}

function dividendsTeaser(d) {
  const dv = obj(d.dividends);
  const next = obj(dv.next_expected);
  const reg = list(dv.register).slice().sort((a, b) => String(b.ex_date || b.record_date).localeCompare(String(a.ex_date || a.record_date))).slice(0, 3);
  const cal = list(obj(d.calendar).events).filter((e) => ["agm", "record", "pay"].includes(e.kind)).slice(0, 3);
  if (!isNum(next.dps) && !reg.length) return card({ title: "Дивиденды", span: 6 }, missing("дивиденды"));
  return card({ title: `Дивиденд за ${next.year || "—"}`, span: 6, link: ["capital", "Капитал и дивиденды"],
    sub: next.status === "model" ? "объявления ещё нет: DPS по политике на прибыли модели" : `статус — ${(DIV_STATUS[next.status] || [next.status])[0]}` },
  el("div", { class: "kpis" },
    kpi(fmt.rub(next.dps, 2), [nextDpsLabel(next), next.status === "model" ? nextDpsMean(next) : null]),
    ...tickers(d).map((t) => kpi(fmt.pct(obj(next.yield)[t], 1), `доходность к ${t}`)),
    kpi(nextRecordText(next), next.record_date ? "отсечка" : "ожидаемая отсечка")),
  reg.length ? el("div", { style: "margin-top:14px" }, dataTable([
    Tc("Год прибыли", (r) => el("span", {}, String(r.year), el("span", { class: "cell-badge" }, divStatus(r))), "name"),
    Nc("На акцию", (r) => fmt.rub(r.dps, 2)),
    Nc("Отсечка", registerCutoff),
  ], reg, { cls: "compact" })) : null,
  cal.length ? foot(sentence("В календаре: " + cal.map((e) => `${e.precision === "day" ? "" : "≈" + NBSP}${fmt.date(e.date)} — ${lowerFirst(e.title)}`).join("; "))) : null);
}

/* ── события, изменения, метод ── */

function eventsCard(d, limit, span = 6) {
  const cal = obj(d.calendar);
  const nf = obj(cal.next_fact);
  const events = list(cal.events).slice(0, limit || 99);
  if (!events.length) return card({ title: "События", span }, empty("Ближайших событий в выпуске нет."));
  const when = (e) => (e.precision === "day" ? fmt.dateShort(e.date) : `≈${NBSP}${fmt.dateShort(e.date)}`);
  return card({ title: "События", span, sub: `отсчёт — от даты оценки ${fmt.date(cal.today || d.meta.valuation_date)}` },
    el("ol", { class: "timeline" }, events.map((e) => el("li", { class: cls(e.id === nf.id && "is-fact", ["agm", "record", "pay"].includes(e.kind) && "is-div") },
      el("span", { class: "tl-date" }, when(e), el("span", { class: "muted" }, `через ${fmt.days(e.days)}`)),
      el("span", { class: "tl-rail" }, el("i")),
      el("span", { class: "tl-body" }, el("strong", {}, ruText(e.title)),
        e.precision === "window" && e.earliest && e.latest ? el("span", { class: "muted" }, `окно ${fmt.dateShort(e.earliest)}–${fmt.dateShort(e.latest)}`) : null,
        e.note ? el("span", { class: "muted" }, ruText(e.note)) : null)))));
}

// Разложение М§16: остаток — в строке первого сменившегося входа, пояснение — `note`.
function changesCard(d, span = 6) {
  const ch = obj(d.changes).vs_previous;
  if (!ch) return card({ title: "Что изменилось с прошлого выпуска", span }, empty("Прошлого выпуска нет: это первый выпуск."));
  const rows = list(ch.rows);
  const moves = Object.entries(obj(ch.market_price));
  return card({ title: "Что изменилось с прошлого выпуска", span,
    sub: ch.previous_published_at ? `против выпуска ${fmt.stamp(ch.previous_published_at)}; ₽ на акцию — точка и медиана` : "" },
  rows.length ? el("ul", { class: "list" },
    rows.map((r) => el("li", {}, el("span", { class: "t" }, r.title, r.note ? el("span", { class: "muted" }, ruText(r.note)) : null),
      el("span", { class: "v" }, `${fmt.signedRub(r.point_rub, 1)} · ${fmt.signedRub(r.median_rub, 1)}`))),
    el("li", { class: "total" }, el("span", { class: "t" }, el("strong", {}, "Итого: точка · медиана")),
      el("span", { class: "v" }, `${fmt.signedRub(ch.total_point_rub, 1)} · ${fmt.signedRub(ch.total_median_rub, 1)}`))) : empty("Разложения нет."),
  foot(moves.length ? `Цена: ${moves.map(([t, m]) => `${t} ${fmt.num(obj(m).from, 2)} → ${fmt.rub(obj(m).to, 2)}`).join(", ")} — меняет сравнения, а не стоимость. ` : "",
    ch.note ? sentence(upperFirst(ruText(ch.note))) : ""));
}

function methodNote(d) {
  const meta = obj(d.meta);
  const shares = obj(meta.shares);
  const bridge = obj(d.fair_value.bridge);
  return card({ title: "Как читать эту оценку" },
    el("div", { class: "prose" },
      el("p", {}, el("strong", {}, "Крупное число — медиана, а не точка. "),
        "Каждый прогон сдвигает суждения книги в их диапазонах одновременно (треугольно, мода — значение книги) и пересчитывает всю сетку, слои и цену. Полоса 80 % честнее одной цифры."),
      el("p", {}, el("strong", {}, "Капитал считается напрямую. "),
        `Цена — капитал на дату оценки плюс PV доходности сверх стоимости капитала (остаточный доход, проверенный дивидендной моделью), минус дисконт за управление ${fmt.pct(meta.governance_discount, 2)}, `
        + `плюс объявленный дивиденд до отсечки (${fmt.bn(bridge.amount, 1)}), на ${fmt.num(shares.outstanding_mln, 1)} млн акций в обращении обеих категорий. База — ${basisName(meta.basis)}.`),
      el("p", {}, "Одна справедливая стоимость на обе категории акций: главная — первой линией рынка, вторая — пунктиром.")),
    foot(el("a", { href: "#model" }, "Как устроен расчёт →")));
}

/* ── экран «Что в цене» ── */

function screenMarket(d) {
  const block = obj(d.reverse_dcf);
  const w = diagWords(block.number);
  const root = el("div", { class: "screen" },
    screenHead("Что в цене", pricedHeadline(list(block.rows)),
      `Рынок платит ${fmt.rub(d.market.price, 2)} за акцию ${ticker(d)} (${fmt.date(d.meta.valuation_date)}). Ниже — какое значение одного суждения делает ${w.acc} равной рынку, `
      + "как расходятся справедливый и рыночный P/B, что говорят брокеры и банки-аналоги."));
  root.append(section("", reverseDcfCard(d)));
  root.append(section("", bankLineCard(d)));
  root.append(section("", priceCard(d)));
  root.append(section("", brokersCard(d), peersCard(d)));
  root.append(section("", spreadCard(d)));
  return root;
}

function reverseDcfCard(d) {
  const block = obj(d.reverse_dcf);
  const rows = list(block.rows);
  if (!rows.length) return card({ title: "Обратный расчёт по осям книги" }, missing("обратный расчёт"));
  const w = diagWords(block.number);
  // Нет решения — об этом говорит бейдж, а не число.
  const need = (r) => (r.status === "solved" && isNum(r.solved) ? reverseValue(r, r.solved) : null);
  const table = () => dataTable([
    Tc("Ось", (r) => r.name, "name"),
    Nc("Нужно рынку", (r) => need(r) || "—"),
    Nc("В книге", (r) => reverseValue(r, r.book, true)),
    Nc("Диапазон книги", reverseRange),
    Nc("Невязка, ₽", (r) => fmt.num(r.gap, 2)),
    Tc("", reverseBadge),
  ], rows);
  const box = el("div", { class: "rows" }, rows.map((r) => el("div", { class: "rrow" },
    el("div", { class: "rrow-name" }, r.name, hint(`книга ${reverseValue(r, r.book, true)}${reverseRange(r) === "—" ? " · оси с диапазоном в книге нет" : ` · диапазон ${reverseRange(r)}`}`)),
    rangeRowChart(r),
    el("div", { class: "rrow-need" }, need(r) ? el("span", { class: "v" }, need(r)) : null, " ", reverseBadge(r),
      isNum(r.gap) && Math.abs(r.gap) >= 0.5 ? el("span", { class: "b" }, `невязка ${fmt.rub(r.gap, 1)}`) : null))));
  const fig = withTable(box, table);
  return card({ title: "Обратный расчёт по осям книги", tools: fig.button,
    sub: `Значение одной оси, при котором ${w.subj} полосы равна цене ${ticker(d)} ${fmt.rub(block.target, 2)}; прочие суждения разыгрываются как в полосе. Строки решаются по одной: решения разных строк не перемножаются. «Недостижимо» — цена не достигается на всём отрезке поиска.` },
  legend([["key-b50", "диапазон книги"], ["key-line key-ink", "значение книги"], ["key-dot key-market", "нужно рынку"]]),
  fig.box,
  block.method ? foot(sentence(upperFirst(ruText(block.method)))) : null);
}

// Язык банка: справедливый P/B против рыночного по слоям, избыточная доходность, вменённые ROE и k.
function bankLineCard(d) {
  const b = obj(d.fair_value.bank_first_line);
  if (!isNum(b.market_pb)) return card({ title: "Капитал: модель против рынка" }, missing("язык банка"));
  const L = obj(b.layers);
  const rows = [{ name: `${diagWords(b.target).subj} (λ книги)`, a: b.fair_pb_median, b: b.market_pb, aLabel: "справедливый P/B", bLabel: "рыночный P/B" },
    { name: "точка при значениях книги", a: b.fair_pb_point, b: b.market_pb, aLabel: "справедливый P/B", bLabel: "рыночный P/B" },
    ...["analytical", "macro_neutral"].filter((k) => L[k]).map((k) => ({ name: layerTitle(d, k), a: L[k].fair_pb, b: b.market_pb, aLabel: "P/B слоя", bLabel: "рыночный P/B" }))];
  const plot = dumbbellChart(rows, { fmt: (v) => fmt.x(v, 2), refs: [1], label: "Справедливый и рыночный P/B по слоям" });
  const fig = withTable(plot, () => dataTable([
    Tc("Слой", (r) => r.name, "name"),
    Nc("Справедливый P/B", (r) => fmt.x(r.a, 3)),
    Nc("Рыночный P/B", (r) => fmt.x(r.b, 3)),
  ], rows));
  const bank = Object.fromEntries(list(obj(d.reverse_dcf).bank_rows).map((r) => [r.key, r]));
  const roe = obj(bank.implied_roe_through_cycle), coe = obj(bank.implied_cost_of_equity);
  return card({ title: "Капитал: модель против рынка", tools: fig.button,
    sub: `Капитал на дату оценки ${fmt.bn(b.bv_v, 0)}; рынок платит ${fmt.x(b.market_pb, 2)} капитала (капитализация ${fmt.bn(d.market.cap, 0)}). Вертикаль — P/B = 1.` },
  legend([["key-dot key-model", "справедливый P/B"], ["key-dot key-market", "рыночный P/B"]]),
  fig.box,
  el("div", { class: "kpis", style: "margin-top:14px" },
    kpi(fmt.signedBn(b.excess_median, 0), "PV доходности сверх стоимости капитала: модель (медиана)"),
    kpi(fmt.signedBn(b.excess_market, 0), "то же, вменённое рынком: капитализация − капитал"),
    isNum(roe.implied) ? kpi(`${fmt.pct(roe.implied, 1)} / ${fmt.pct(roe.book, 1)}`, "ROE сквозь цикл: нужен рынку / в книге") : null,
    isNum(coe.implied) ? kpi(`${fmt.pct(coe.implied, 1)} / ${fmt.pct(coe.book, 1)}`, "стоимость капитала: вменённая рынком / в книге",
      { tip: { title: "Вменённая стоимость капитала по мирам", rows: Object.entries(obj(coe.by_world)).map(([k, v]) => [worldName(d, k), fmt.pct(v, 2)]) } }) : null,
    kpi(`${fmt.pct(b.roe_tc, 1)} / ${fmt.pct(b.k_tc, 1)}`, "ROE сквозь цикл / стоимость капитала (λ книги)")),
  b.sensitivity_basis ? foot(sentence(`Чувствительности: ${ruText(b.sensitivity_basis)}`)) : null);
}

// Цена за 12 месяцев: закрытия обеих категорий, экс-даты с DPS, печатаемая медиана.
function priceCard(d) {
  const mk = obj(d.market);
  const hist = obj(mk.price_history);
  const main = ticker(d);
  const t = (iso) => { const day = parseDay(iso); return day ? day.getTime() : NaN; };
  const series = tickers(d).map((tk) => ({ name: tk, color: "var(--market)", width: tk === main ? 1.9 : 1.5, dashed: tk !== main,
    points: list(hist[tk]).filter((p) => isNum(p.close)).map((p) => [t(p.date), p.close]).filter((p) => isNum(p[0])) })).filter((s) => s.points.length > 1);
  if (!series.length) return card({ title: "Цена акций за 12 месяцев" }, empty("Истории цены в выпуске нет."));
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  const first = Math.min(...xs), last = Math.max(...xs);
  const median = obj(d.fair_value.headline).printed_median;
  const exd = list(mk.ex_dividend).filter((e) => isNum(t(e.date)) && isNum(e.dps) && t(e.date) >= first && t(e.date) <= last);
  const monthTicks = [];
  const start = new Date(first);
  for (let k = 1; k <= 13; k++) { const ms = new Date(start.getFullYear(), start.getMonth() + k, 1).getTime(); if (ms > first && ms < last) monthTicks.push(ms); }
  const monthLabel = (ms) => { const day = new Date(ms); return day.getMonth() === 0 ? String(day.getFullYear()) : MONTHS_AXIS[day.getMonth()]; };
  const plot = linesChart(series, { height: 250, xMin: first, xMax: last, xTicks: monthTicks, xFmt: monthLabel, left: 52,
    yFmt: (v) => fmt.num(v), tipFmt: (v) => fmt.rub(v, 2),
    hrefs: isNum(median) ? [{ value: median, text: `медиана модели ${fmt.rub(median)}`, color: "var(--model)", dash: "5 4" }] : [],
    vlines: exd.map((e) => ({ x: t(e.date), text: `дивиденд ${fmt.rub(e.dps, 2)}`,
      tip: { title: "Экс-дата дивиденда", rows: [["дата", fmt.date(e.date)], ["на акцию", fmt.rub(e.dps, 2)], ["год прибыли", String(e.year)]] } })),
    label: "Цена акций за 12 месяцев" });
  const rows = list(hist[main]).slice().reverse();
  const fig = withTable(plot, () => dataTable([
    Tc("Дата", (p) => fmt.date(p.date), "name"),
    ...tickers(d).map((tk) => (Nc(tk, (p) => { const q = list(hist[tk]).find((x) => x.date === p.date); return q ? fmt.rub(q.close, 2) : "—"; }))),
  ], rows));
  const prices = obj(mk.prices);
  const lo = obj(obj(mk.price_min)[main]), hi = obj(obj(mk.price_max)[main]);
  const m = obj(mk.multiples);
  return card({ title: "Цена акций за 12 месяцев", tools: fig.button, sub: "дневные закрытия; вертикали — экс-даты дивидендов; пунктир — медиана модели" },
    legend([...series.map((s) => [s.dashed ? "key-line key-market2" : "key-line key-market", s.name]), isNum(median) && ["key-line key-model2", "медиана модели"], exd.length && ["key-line key-dash", "экс-дата"]]),
    fig.box,
    el("div", { class: "kpis", style: "margin-top:12px" },
      ...tickers(d).map((tk) => { const p = obj(prices[tk]); return kpi(fmt.rub(p.price, 2), `${tk} · ${fmt.date(p.date)}${p.time ? ` ${p.time}${NBSP}МСК` : ""}${p.source ? ` · ${p.source}` : ""}`); }),
      kpi(`${fmt.num(lo.close, 2)}–${fmt.num(hi.close, 2)}${THIN}₽`, `минимум и максимум ${main} за период`),
      kpi(fmt.pct(obj(m.dividend_yield_ltm)[main], 1), `дивиденды за 12 мес. к цене ${main}`),
      kpi(fmt.x(m.pe_ltm, 1), "P/E за 12 месяцев")));
}

// Цели брокеров — только агрегаты: разброс, медиана, рекомендации; модель и рынок на той же шкале.
function brokersCard(d) {
  const b = obj(d.market.brokers);
  if (!isNum(b.median)) return card({ title: "Цели брокеров", span: 6 }, empty("Целей брокеров в выпуске нет."));
  const model = obj(d.fair_value.headline).printed_median;
  const price = d.market.price;
  const plot = stripChart([
    { v: b.median, kind: "bench", text: `медиана целей ${fmt.rub(b.median)}`, tip: { title: "Цели брокеров", rows: [["минимум", fmt.rub(b.min)], ["медиана", fmt.rub(b.median)], ["максимум", fmt.rub(b.max)], ["целей", fmt.num(b.n)]] } },
    { v: price, kind: "market", text: `рынок ${fmt.rub(price)}` },
    { v: model, kind: "model", text: `модель ${fmt.rub(model)}` },
  ], { band: [b.min, b.max], bandFill: "var(--market-wash)", bandStroke: "var(--market)", fmt: (v) => fmt.num(v), label: "Цели брокеров, рынок и медиана модели" });
  const rec = obj(b.recommendations);
  const histRows = list(b.history).filter((h) => isNum(h.median));
  const spark = histRows.length > 1 ? sparkline({ title: "медиана целей", history: { date: histRows.map((h) => h.date), value: histRows.map((h) => h.median) } }, (v) => fmt.rub(v), "по снимкам") : null;
  return card({ title: "Цели брокеров", span: 6, sub: `на ${fmt.date(b.as_of)} · ${fmt.num(b.n)} ${plural(b.n || 0, ["цель", "цели", "целей"])} · ${b.source || ""}` },
    legend([["key-band", "разброс целей"], ["key-dot key-third", "медиана целей"], ["key-dot key-market", "рынок"], ["key-dot key-model", "модель"]]),
    plot,
    el("div", { class: "kpis", style: "margin-top:10px" },
      kpi(`${fmt.num(b.min)}–${fmt.num(b.max)}${THIN}₽`, "минимум и максимум целей"),
      kpi(`${fmt.num(rec.buy)} / ${fmt.num(rec.hold)} / ${fmt.num(rec.sell)}`, "покупать / держать / продавать")),
    spark ? el("div", { style: "margin-top:10px" }, tileLabel("Медиана целей по снимкам"), spark) : null);
}

// Банки-аналоги на одной базе: строка эмитента первой; без живой цены — причина.
function peersCard(d) {
  const p = obj(d.market.peers);
  const rows = list(p.rows);
  if (!rows.length) return card({ title: "Банки-аналоги", span: 6 }, empty("Аналогов в выпуске нет."));
  const subject = p.subject_ticker || ticker(d);
  // Дата отчётности — `bv_date` строки; `as_of` — день снимка.
  const day = obj(rows.find((r) => r.ticker === subject) || rows[0]).bv_date;
  return card({ title: "Банки-аналоги на одной базе", span: 6, sub: `отчётность на ${fmt.date(day)}; снимок аналогов ${fmt.date(p.as_of)}; цены — день оценки` },
    dataTable([
      Tc("Банк", (r) => el("span", {}, r.name, hint(
        [r.ticker, obj(r.capital_ratio).name, r.bv_date && r.bv_date !== day ? `отчётность на ${fmt.date(r.bv_date)}` : null].filter(Boolean).join(" · "))), "name"),
      Nc("P/B", (r) => (isNum(r.pb) ? fmt.x(r.pb, 2) : "—")),
      Nc("P/E", (r) => (isNum(r.pe) ? fmt.x(r.pe, 1) : "—")),
      Nc("ROE 12 мес.", (r) => fmt.pct(r.roe_ltm, 1)),
      Nc("Дивдоходность", (r) => fmt.pct(r.dividend_yield, 1)),
      Nc("Норматив", (r) => { const c = obj(r.capital_ratio); return isNum(c.value) ? el("span", { tip: { title: c.name || "норматив", rows: [["на", fmt.date(c.as_of)]] } }, fmt.pct(c.value, 1)) : "—"; }),
    ], rows, { rowClass: (r) => (r.ticker === subject ? "is-pick" : r.status === "missing" ? "is-muted" : null),
      detail: (r) => (r.status !== "live" && r.reason ? detailsBlock("Нет живой цены", sentence(upperFirst(ruText(r.reason)))) : null) }),
    p.basis ? el("div", { class: "card-foot" }, detailsBlock("База сравнения", sentence(upperFirst(ruText(p.basis))))) : null);
}

// «Торнадо»: точка на краях диапазона одного суждения (прочие — книга), главные по размаху.
function spreadCard(d) {
  const rows = list(obj(d.judgements).rows).filter((j) => isNum(j.price_low) && isNum(j.price_high)).slice(0, 8);
  if (!rows.length) return card({ title: "Цена по одному суждению" }, missing("суждения по цене ошибки"));
  const center = d.fair_value.central;
  const all = rows.flatMap((j) => [j.price_low, j.price_high]).concat(center);
  const step = niceStep(Math.max(...all), 6);
  const dom = [Math.max(0, Math.floor((Math.min(...all) * 0.94) / step) * step), Math.ceil((Math.max(...all) * 1.06) / step) * step];
  const box = el("div", { class: "tornado" }, rows.map((j) => {
    const lo = Math.min(j.price_low, j.price_high), hi = Math.max(j.price_low, j.price_high);
    return el("div", { class: "tn-row" },
      el("div", { class: "tn-name" }, j.name),
      el("div", { class: "tn-bar" }, barTrack([
        { from: lo, to: Math.min(center, hi), cls: "is-down" }, { from: Math.max(center, lo), to: hi, cls: "is-up" },
      ].filter((s) => s.to > s.from), dom, { center, marks: [
        { at: j.price_low, cls: "end", tip: { title: `${j.name}: нижний край`, rows: [["значение", formatByUnit(j.low, j.unit, d)], ["точка", fmt.rub(j.price_low, 1)]] } },
        { at: j.price_high, cls: "end", tip: { title: `${j.name}: верхний край`, rows: [["значение", formatByUnit(j.high, j.unit, d)], ["точка", fmt.rub(j.price_high, 1)]] } },
      ] })),
      el("div", { class: "tn-vals" }, `${fmt.num(j.price_low)} … ${fmt.num(j.price_high)}${THIN}₽`, hint(rangeText(d, j, true))));
  }));
  return card({ title: "Цена по одному суждению",
    sub: `Главные суждения книги по одному, каждое на краях своего диапазона; вертикаль — точка при значениях книги ${fmt.rub(center, 1)}. Это не полоса: полоса двигает все суждения сразу.` },
  legend([["key-neg", "ниже точки"], ["key-model", "выше точки"]]), box,
  el("div", { class: "tn-row tn-axis-row", "aria-hidden": "true" }, el("span"),
    el("div", { class: "tn-axis" }, el("span", {}, fmt.rub(dom[0])), el("span", {}, fmt.rub(dom[1]))), el("span")));
}

/* ── экран «Расчёт» ── */

function screenModel(d) {
  const root = el("div", { class: "screen" },
    screenHead("Расчёт", "Как из допущений получается цена",
      "Капитал банка оценивается напрямую — остаточным доходом по сетке сценариев с явными вероятностями: миры ставок × режимы доходности и кредитного цикла × регуляторные сценарии капитала. "
      + "Гайденс и стратегия — сверка пути, а не вход. Все числа — из выпуска."));
  root.append(section("", flowCard(d)));
  root.append(section("", layersCard(d)));
  root.append(section("Путь доходности", roeTreeCard(d)));
  root.append(section("", guidanceCard(d)));
  root.append(more([regimesCard(d), capitalScenariosCard(d), worldsCard(d), transmissionCard(d), gridCard(d), varianceCard(d), governanceCard(d)]));
  return root;
}

function flowCard(d) {
  const fv = d.fair_value;
  const head = obj(fv.headline);
  const g = obj(d.grid);
  const cells = list(g.cells);
  const meta = obj(d.meta);
  const L = obj(d.layers);
  const v0 = ["macro_neutral", "market_implied", "analytical"].map((k) => obj(L[k]).v0).filter(isNum);
  const inv = list(obj(d.checks).invariants).find((i) => i.name === "ddm_equals_ri");
  const n = (a, words) => `${fmt.num(list(a).length)} ${plural(list(a).length, words)}`;
  const steps = [
    ["Сетка", `${fmt.num(cells.length)} ${plural(cells.length, CELL_WORDS)}`,
      `${n(g.world_order, ["мир", "мира", "миров"])} ставок × ${n(g.regime_order, ["режим", "режима", "режимов"])} доходности и кредитного цикла × `
      + `${n(g.scenario_order, ["сценарий", "сценария", "сценариев"])} капитала; шаг — ${modelUnit(d).one}, ${periodLabel(list(meta.horizon)[0])} — ${periodLabel(list(meta.horizon)[1])}, база — ${basisName(meta.basis)}.`],
    ["В клетке", inv ? (inv.ok ? "DDM = RI ✓" : "DDM ≠ RI") : "остаточный доход",
      "Капитал на дату оценки + PV (ROE − k)·капитал + терминал; дивидендная модель с капитальным ограничением проверяет каждую клетку."],
    ["Слои", v0.length ? `V0 ${fmt.num(Math.min(...v0), 0)}–${fmt.num(Math.max(...v0), 0)}` : "—",
      `Три набора весов миров на одних клетках: ${["macro_neutral", "market_implied", "analytical"].map((k) => layerTitle(d, k)).join(", ")} (млрд ₽).`],
    ["Цена и точка", `${fmt.num(fv.low)} → ${fmt.num(fv.central)} → ${fmt.num(fv.high)}`,
      `[V0·(1 − g) + объявленный дивиденд до отсечки] на ${fmt.num(obj(meta.shares).outstanding_mln, 1)} млн акций в обращении; точка = низ + λ·(верх − низ), λ = ${fmt.num(fv.own_macro_confidence, 2)}.`],
    ["Полоса", isNum(head.median) ? `медиана ${fmt.num(head.median)}` : "нет в выпуске",
      isNum(head.median) ? `${fmt.num(head.draws)} прогонов по суждениям в их диапазонах; печать шагом ${fmt.num(head.print_step)}${THIN}₽ половиной вверх → ${fmt.rub(head.printed_median)}.` : "Прогонов в выпуске нет."],
  ];
  return card({ title: "Пять шагов от допущений к цене" },
    el("div", { class: "flow" }, steps.map(([t, big, p]) => el("div", { class: "flow-step" }, el("h3", {}, t), el("div", { class: "big" }, big), el("p", {}, p)))));
}

function layersCard(d) {
  const L = obj(d.layers);
  const order = ["macro_neutral", "market_implied", "analytical"].filter((k) => L[k]);
  if (!order.length) return card({ title: "Слои" }, missing("слои"));
  const mix = obj(L.headline_mix);
  return card({ title: "Слои: капитал, избыточная доходность и цена",
    sub: `Цена слоя — [V0·(1 − g) + дивиденд до отсечки] на акцию; V0 — оценка капитала; PV терминала — остаточного дохода (RI), доля терминала — в дивидендной модели (DDM); P(…) — масса клеток под весами слоя. Цена ${ticker(d)} — ${fmt.rub(d.market.price, 2)}.` },
  dataTable([
    Tc("Слой", (k) => el("span", {}, layerTitle(d, k), hint(dictText(d, L[k].world_weights))), "name"),
    Nc("V0, млрд ₽", (k) => fmt.num(L[k].v0, 0)),
    Nc("Капитал", (k) => fmt.num(L[k].bv_v, 0)),
    Nc("P/B", (k) => fmt.x(L[k].pb, 2)),
    Nc("PV RI явный", (k) => fmt.num(L[k].pv_ri_explicit, 0)),
    Nc("PV терминала RI", (k) => fmt.num(L[k].pv_terminal, 0)),
    Nc("Доля терминала DDM", (k) => fmt.pct(L[k].terminal_share, 0)),
    Nc("Цена, ₽", (k) => el("strong", {}, fmt.num(L[k].price, 1))),
    Nc("P(ниже рынка)", (k) => fmt.pct(L[k].p_price_below_market, 0)),
    Nc("P(ROE < k)", (k) => fmt.pct(L[k].p_roe_below_k, 0)),
    Nc("Разрыв капитала", (k) => fmt.pct(L[k].capital_gap_mass, 1)),
  ], order),
  foot(`${upperFirst(ruText(mix.title || "смесь заголовка"))}: V0 ${fmt.bn(mix.v0, 0)}, цена ${fmt.rub(mix.price, 2)} = точка при λ = ${fmt.num(mix.lambda, 2)}.`));
}

// Дерево ROE: строки к средним активам → ROA × рычаг = ROE; мини-графики ЧПМ, CoR (упр.) и ROE с фактом прошлых лет.
function roeTreeCard(d) {
  const p = obj(d.paths);
  const tree = list(p.roe_tree);
  const annual = list(p.annual);
  if (!tree.length) return card({ title: "Дерево ROE" }, missing("путь"));
  const hist = list(obj(d.history).annual);
  const years = [...new Set([...hist.map((h) => h.year), ...annual.map((a) => a.year)])];
  const mini = (title, fact, path, f) => el("div", { class: "mini" }, tileLabel(title),
    linesChart([{ name: "факт", color: "var(--ink)", dots: true, r: 3, points: fact }, { name: "модель", color: "var(--model)", dots: true, r: 3, points: path }],
      { xType: "band", categories: years, height: 150, left: 44, yFmt: f, tipFmt: f, xFmt: (y) => `’${String(y).slice(2)}` }));
  const pct1 = (v) => fmt.pct(v, 1);
  const part = (v) => fmt.num(v * 100, 2);
  return card({ title: "Дерево ROE: от строк ОПУ к доходности капитала",
    sub: `${upperFirst(ruText(obj(p.mix).title || "смесь заголовка"))}; строки — % средних активов; ROA × рычаг = ROE. Год якоря — факт отчётных ${modelUnit(d).pgen} + модель; упр. ЧПМ и CoR — мостом сборки.` },
  el("div", { class: "minis" },
    mini("ЧПМ, упр.", hist.map((h) => [h.year, obj(h.mgmt).nim]), annual.map((a) => [a.year, a.nim_mgmt]), pct1),
    mini("CoR, упр.", hist.map((h) => [h.year, obj(h.mgmt).cor]), annual.map((a) => [a.year, a.cor_mgmt]), (v) => fmt.pct(v, 2)),
    mini("ROE (МСФО группы)", hist.map((h) => [h.year, obj(h.ifrs).roe]), annual.map((a) => [a.year, a.roe]), pct1)),
  el("div", { style: "margin-top:16px" }, dataTable([
    Tc("Год", (r) => String(r.year), "name"),
    Nc("ЧПД", (r) => part(r.nii_to_assets)),
    Nc("Комиссии", (r) => part(r.fees_to_assets)),
    Nc("Прочее", (r) => part(r.other_to_assets)),
    Nc("Непрофильный", (r) => part(r.noncore_to_assets)),
    Nc("Расходы", (r) => part(r.opex_to_assets)),
    Nc("Резервы", (r) => part(r.llp_to_assets)),
    Nc("Налог", (r) => part(r.tax_to_assets)),
    Nc("ROA", (r) => el("strong", {}, fmt.pct(r.roa, 2))),
    Nc("Рычаг", (r) => fmt.x(r.leverage, 1)),
    Nc("ROE", (r) => el("strong", {}, fmt.pct(r.roe, 1))),
  ], tree, { caption: "% средних активов" })));
}

// Гайденс и стратегия против пути модели; строка сектора (`scope: sector`) — прогноз сектора: гейт её не берёт (М§14.2).
const GUIDE_STATUS = { inside: ["в гайденсе", "good"], outside: ["вне гайденса", "warn"], "n/a": ["неприменимо", "out"] };
const GUIDE_RELATION = { above: "банк — лучше сектора", in_line: "банк — в соответствии с сектором" };
function guideStatus(r) {
  const [t, k] = GUIDE_STATUS[r.status] || [r.status, null];
  return r.scope !== "sector" ? badge(t, k) : r.status === "n/a" ? badge("не сравнивается", "out") : badge(r.status === "inside" ? "в прогнозе сектора" : "вне прогноза сектора", k);
}

// Гайденс по виду строки (`kind`): точка, пол (≥), потолок (≤), диапазон.
function guideValue(kind, key, v) {
  if (Array.isArray(v)) return `${fmt.num(v[0] * 100, 1)}–${fmt.pct(v[1], 1)}`;
  const s = fmt.pct(v, key === "nim" ? 2 : 1);
  const k = kind || (key === "cor_max" ? "max" : "point");
  return k === "max" ? `≤${NBSP}${s}` : k === "min" ? `≥${NBSP}${s}` : s;
}

// Пересмотры гайденса: снимки по датам → только смены значения строки («было → стало»).
function guidanceChanges(g) {
  const last = {};
  const out = [];
  for (const r of list(g.revisions).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)))) {
    const was = last[r.key];
    if (was !== undefined && JSON.stringify(was) !== JSON.stringify(r.value)) out.push({ ...r, was });
    last[r.key] = r.value;
  }
  return out;
}

function guidanceCard(d) {
  const g = obj(d.guidance);
  const items = list(g.items);
  if (!items.length) return card({ title: "Гайденс и стратегия" }, missing("гайденс"));
  const changes = guidanceChanges(g);
  const gate = obj(g.gate);
  const st = obj(g.strategy);
  const kindOf = Object.fromEntries(items.map((r) => [r.key, r.kind]));
  const titleOf = Object.fromEntries(items.map((r) => [r.key, r.title]));
  const sector = items.some((r) => r.scope === "sector");
  return card({ title: `Гайденс ${g.year || ""} и стратегия против пути модели`,
    sub: `Гайденс от ${fmt.date(g.as_of)} (${ruText(g.src || "")}); базис — ${basisName(g.basis)}, иной базис строки — в подписи; ЧПМ, CoR и CIR модели переведены в упр. базис мостами сборки. Модель года — факт отчётных кварталов + смесь заголовка.`
      + (sector ? " Строки сектора — прогноз сектора, а не гайденс банка: гейт их не берёт." : "") },
  dataTable([
    Tc("Показатель", (r) => el("span", { tip: r.scope_note ? ruText(r.scope_note) : null }, r.title,
      hint([basisName(r.basis), GUIDE_RELATION[r.relation]].filter(Boolean).join("; "))), "name"),
    Nc("Гайденс", (r) => guideValue(r.kind, r.key, r.guidance)),
    Nc("Факт с начала года", (r) => (isNum(r.fact_ytd) ? fmt.pct(r.fact_ytd, 2) : "—")),
    Nc("Модель года", (r) => el("strong", {}, isNum(r.model_year) ? fmt.pct(r.model_year, 2) : "—")),
    Nc("Нужно в остатке года", (r) => (isNum(r.required_rest) ? fmt.pct(r.required_rest, 2) : "—")),
    Tc("Статус", guideStatus),
    Nc("Масса вне", (r) => fmt.pct(r.mass_outside, 0)),
  ], items),
  gate.fired ? el("div", { class: "note-box", style: "margin-top:14px" },
    el("strong", {}, `Гейт «${gateTitle(d, gate.name)}» сработал: ${fmt.pct(gate.mass, 0)} массы клеток. `),
    gate.explanation ? ruText(gate.explanation) : "Объяснения нет.", gate.valid_until ? ` Объяснение действует по ${fmt.date(gate.valid_until)}.` : "") : null,
  list(st.targets).length ? el("div", { style: "margin-top:16px" },
    tileLabel(`${st.name || "Стратегия"}${obj(st.next_event).date ? ` · ${fmt.date(st.next_event.date)} — ${lowerFirst(st.next_event.title)}` : ""}`),
    dataTable([
      Tc("Цель", (t) => t.title, "name"),
      Nc("Ориентир", (t) => fmt.pct(t.target, 1)),
      Nc("Факт", (t) => `${fmt.pct(t.fact_last, 1)} · ${periodLabel(t.fact_period)}`),
      Nc("Модель", (t) => list(t.model).map((m) => `${m.year}: ${fmt.pct(m.value, 1)}`).join("; ") || "—"),
      Tc("", (t) => (t.met === true ? badge("выполнена", "good") : t.met === false ? badge("не выполнена", "warn") : "—")),
    ], list(st.targets), { cls: "compact" })) : null,
  changes.length ? el("div", { class: "card-foot" }, detailsBlock(`Пересмотры гайденса · ${changes.length}`,
    changes.map((r) => `${fmt.date(r.date)} — ${titleOf[r.key] || "строка гайденса"}: ${guideValue(kindOf[r.key], r.key, r.was)} → ${guideValue(kindOf[r.key], r.key, r.value)} (${ruText(r.event)})`).join("; ") + "."))
    : list(g.revisions).length ? foot(`Гайденс года с ${fmt.date(list(g.revisions).map((r) => r.date).sort()[0])} не пересматривался.`) : null);
}

function gateTitle(d, name) {
  const g = list(obj(d.checks).gates).find((x) => x.name === name);
  return (g && g.title) || name;
}

// Режимы: пути CoR (базис книги или движка), сдвиги ЧПМ, вероятности до и после A-P2u, история.
let REGIME_BASIS = null;

function regimesCard(d) {
  const R = obj(d.regimes);
  const order = regimeOrder(d);
  const rows = obj(R.rows);
  if (!order.length) return card({ title: "Режимы доходности и кредитного цикла", span: 12 }, missing("режимы"));
  const bookBasis = R.cor_basis || "mgmt";
  if (!REGIME_BASIS) REGIME_BASIS = bookBasis;
  const body = el("div");
  const years = [...new Set(order.flatMap((k) => list(obj(rows[k]).cor).map((p) => p.year)))].sort();
  const draw = () => {
    const engine = REGIME_BASIS === "engine" && bookBasis !== "engine";
    const key = engine ? "cor_engine" : "cor";
    const lt = engine ? "cor_lt_engine" : "cor_lt";
    const cats = [...years, "LT"];
    const series = order.map((k, i) => ({ name: regimeName(d, k), color: SERIES[i % SERIES.length], dots: true, r: 3.5,
      points: [...list(obj(rows[k])[key]).map((p) => [p.year, p.value]), ["LT", obj(rows[k])[lt]]] }));
    const plot = linesChart(series, { xType: "band", categories: cats, height: 230, yFmt: (v) => fmt.pct(v, 1), tipFmt: (v) => fmt.pct(v, 2),
      xFmt: (y) => String(y), label: "CoR режимов по годам" });
    const crisis = obj(obj(rows.crisis).crisis);
    fill(body, 
      legend(series.map((s, i) => [SERIES_KEYS[i % SERIES_KEYS.length], s.name])), plot,
      engine ? foot(`Базис движка: путь книги + мост ${fmt.pp(obj(R.cor_bridge).value, 2)} (${ruText(obj(R.cor_bridge).method || "")}).`) : null,
      list(crisis.cor_quarters).length ? foot(`${regimeName(d, "crisis")}: год шока ${crisis.shock_year}, по кварталам (${basisName(bookBasis)}) `
        + list(crisis.cor_quarters).map((q) => `${periodShort(q.period)} ${fmt.pct(q.value, 1)}`).join(" · ")
        + (obj(crisis.one_off_loss).amount ? `; разовый убыток ${fmt.bn(crisis.one_off_loss.amount, 0)} в ${periodLabel(crisis.one_off_loss.period)}` : "") + ".") : null);
  };
  draw();
  const stack = (field) => el("div", { class: "stackbar" }, order.map((k, i) => el("i", {
    style: `flex:${obj(rows[k])[field] || 0} 1 0;background:${SERIES[i % SERIES.length]}`,
    tip: { title: regimeName(d, k), rows: [["вероятность", fmt.pct(obj(rows[k])[field], 1)]] } })));
  const exp = obj(R.expected);
  const obs = list(obj(R.update).observations);
  const near = list(R.near_nim_shift).filter((q) => isNum(q.value));
  return card({ title: "Режимы доходности и кредитного цикла",
    sub: `Пути CoR — в базисе книги (${basisName(bookBasis)}); переключатель показывает их после моста так, как видит движок. Сдвиги ЧПМ режимов — без ближнего сдвига: он общий для режимов и миров.`,
    tools: chooser([{ value: bookBasis, label: `книга (${basisName(bookBasis)})` }, { value: "engine", label: "движок" }], REGIME_BASIS, (v) => { REGIME_BASIS = v; draw(); }, "Базис CoR") },
  body,
  el("div", { class: "split", style: "margin-top:16px" },
    el("div", {}, tileLabel("Вероятности: книга"), stack("prior"),
      tileLabel("после наблюдений A-P2u"), stack("posterior"),
      el("p", { class: "muted small", style: "margin-top:6px" }, `Ожидание: сдвиг ЧПМ ${fmt.pp(exp.nim_shift_lt, 2)}; CoR сквозь цикл ${fmt.pct(exp.cor_lt, 2)} (${basisName(bookBasis)}), ${fmt.pct(exp.cor_lt_engine, 2)} (движок).`)),
    dataTable([
      Tc("Режим", (k) => regimeName(d, k), "name"),
      Nc("Книга", (k) => fmt.pct(obj(rows[k]).prior, 1)),
      Nc("После A-P2u", (k) => el("strong", {}, fmt.pct(obj(rows[k]).posterior, 1))),
      Nc("CoR LT", (k) => fmt.pct(obj(rows[k]).cor_lt, 2)),
      Nc("ЧПМ LT", (k) => fmt.pp(obj(rows[k]).nim_shift_lt, 1)),
    ], order, { cls: "compact" })),
  near.length ? foot(`Ближний сдвиг ЧПМ, общий для режимов и миров (${basisName(R.basis || "engine")}, п.п.): `
    + (near.length > 6 ? [...near.slice(0, 5), 0, near[near.length - 1]] : near).map((q) => (q ? `${periodShort(q.period)} ${fmt.num(q.value * 100, exactDigits(q.value * 100, 3))}` : "…")).join(" · ")
    + (near.length > 6 ? ` (${near.length}${NBSP}кв.)` : "") + ".") : null,
  obs.length ? el("div", { class: "card-foot" }, detailsBlock(`Наблюдения кварталов · ${obs.length}${obs.some((o) => o.published) ? " · с подменой" : ""}`,
    obs.map((o) => `${periodLabel(o.period)}: CoR ${obsValue(o, "cor")}, ЧПМ ${obsValue(o, "nim")} (${basisName(o.basis)})${o.published ? ` — ${ruText(o.published.reason)}` : ""}`).join("; ")
      + (isNum(obj(R.update).window_obs) ? `. Фильтр читает не больше ${fmt.num(R.update.window_obs)} последних; априорные вероятности — книги` : "")
      + (R.update.floor_share > 0 ? `; вероятность режима после наблюдений — не ниже ${fmt.pct(R.update.floor_share, 0)} книжной` : "") + ".")) : null,
  regimeHistory(d), referenceClass(d));
}

function obsValue(o, k) { const p = obj(o.published)[k]; return fmt.pct(o[k], 2) + (isNum(p) ? ` (опубликовано ${fmt.pct(p, 2)})` : ""); }

function regimeHistory(d) {
  const h = list(obj(d.regimes).history);
  if (h.length < 2) return null;
  const cats = h.map((r) => r.period);
  return detailsBlock("История CoR и ЧПМ по кварталам (упр. и движок)", linesChart([
    { name: "CoR упр.", color: "var(--neg)", dots: true, r: 3, points: h.map((r) => [r.period, r.cor_mgmt]) },
    { name: "CoR движок", color: "var(--neg)", dashed: true, points: h.map((r) => [r.period, r.cor_engine]) },
    { name: "ЧПМ упр.", color: "var(--model)", dots: true, r: 3, points: h.map((r) => [r.period, r.nim_mgmt]) },
    { name: "ЧПМ движок", color: "var(--model)", dashed: true, points: h.map((r) => [r.period, r.nim_engine]) },
  ], { xType: "band", categories: cats, height: 200, yFmt: (v) => fmt.pct(v, 1), tipFmt: (v) => fmt.pct(v, 2), xFmt: periodShort, label: "История CoR и ЧПМ" }));
}

// Референс-класс эпизодов — справочно, движок его не читает.
function referenceClass(d) {
  const rc = obj(obj(d.regimes).reference_class);
  if (rc.error) return foot(`Референс-класс не собран: ${ruText(rc.error)}.`);
  if (!list(rc.shares).length) return null;
  return detailsBlock(`Референс-класс эпизодов · ${list(rc.episodes).length} — справочно`, el("div", {},
    dataTable([
      Tc("Режим", (r) => regimeName(d, r.regime), "name"),
      Nc("Книга", (r) => fmt.pct(r.book, 0)),
      Nc("Класс", (r) => fmt.pct(r.class, 0)),
      Nc("95 % Уилсона", (r) => `${fmt.num(r.lo * 100, 0)}–${fmt.pct(r.hi, 0)}`),
    ], list(rc.shares), { cls: "compact" }),
    list(rc.episodes).length ? el("p", { class: "muted small", style: "margin-top:6px" },
      list(rc.episodes).map((e) => `${e.name} (${ruText(e.period)}): пик CoR ${fmt.pct(e.cor_peak, 1)}`).join("; ") + ".") : null));
}

// Регуляторные сценарии капитала: надбавки, вычеты, пол и требование по годам; P(сценарий | режим).
let CAP_SCENARIO = null;

function capitalScenariosCard(d) {
  const C = obj(d.capital);
  const S = obj(C.scenarios);
  const keys = scenarioOrder(d).filter((k) => S[k]);
  if (!keys.length) return card({ title: "Регуляторные сценарии капитала" }, missing("сценарии капитала"));
  if (!CAP_SCENARIO || !S[CAP_SCENARIO]) CAP_SCENARIO = keys[0];
  const years = list(C.years);
  const body = el("div");
  const draw = () => {
    const s = S[CAP_SCENARIO];
    const lines = [["conservation", "надбавка поддержания"], ["sifi", "надбавка СЗКО"], ["ccyb", "антициклическая"], ["deduction_n20", `вычеты, ${capTitle(d, "n20")}`],
      ["floor20", `пол ${capTitle(d, "n20")}`], ["req20", `требование ${capTitle(d, "n20")}`], ["floor11", `пол ${capTitle(d, "n11")}`], ["req11", `требование ${capTitle(d, "n11")}`]];
    fill(body, dataTable([
      Tc("", (r) => r[1], "name"),
      ...years.map((y, i) => (Nc(String(y), (r) => fmt.pct(list(s[r[0]])[i], 2)))),
    ], lines, { cls: "compact", rowClass: (r) => (r[0].startsWith("req") ? "is-total" : null) }));
  };
  draw();
  const regs = regimeOrder(d);
  return card({ title: "Регуляторные сценарии капитала",
    sub: `Минимум ${capTitle(d, "n20")} ${fmt.pct(obj(C.minimum).n20_0, 1)}, ${capTitle(d, "n11")} ${fmt.pct(obj(C.minimum).n1_1, 1)}; управленческий буфер ${fmt.pp(obj(C.mgmt_buffer).n20_0, 1)}; порог дивидендной политики ${fmt.pct(C.policy_threshold, 1)}.` },
  el("div", { class: "pick-row" }, chooser(keys.map((k) => ({ value: k, label: scenarioName(d, k), hint: fmt.pct(S[k].mass, 0) })), CAP_SCENARIO, (v) => { CAP_SCENARIO = v; draw(); }, "Сценарий капитала")),
  body,
  el("div", { style: "margin-top:14px" }, dataTable([
    Tc("P(сценарий | режим)", (k) => scenarioName(d, k), "name"),
    ...regs.map((r) => (Nc(regimeName(d, r), (k) => fmt.pct(obj(S[k].p_given_regime)[r], 0)))),
    Nc("Масса", (k) => el("strong", {}, fmt.pct(S[k].mass, 0))),
  ], keys, { cls: "compact" })));
}

// Запись миров — общая для семейства 850: печатается её версия, а не имя репозитория записи.
function worldsRecord(d) {
  const v = /book-(\d+(?:\.\d+)+)/.exec(obj(obj(d.worlds).source).origin || "");
  return `запись семейства 850${v ? `, версия ${v[1]}` : ""}`;
}

function worldsCard(d) {
  const W_ = obj(obj(d.worlds).rows);
  const keys = worldOrder(d).filter((k) => W_[k]);
  if (!keys.length) return card({ title: "Миры ставок" }, missing("миры"));
  const tenor = (k) => (k === "LT" ? 25 : Number(k));
  const series = keys.map((k, i) => ({ name: `${k} — ${W_[k].name}`, color: WORLD_COLORS[i % 3], dots: true,
    points: Object.entries(obj(W_[k].zero_curve)).map(([t, v]) => [tenor(t), v]).sort((a, b) => a[0] - b[0]) }));
  const curve = obj(obj(d.live).curve);
  if (Object.keys(obj(curve.nodes)).length) {
    series.push({ name: `ОФЗ ${fmt.date(curve.as_of)}`, color: "var(--ink)", width: 1.5, dots: true, r: 3,
      points: Object.entries(curve.nodes).map(([t, v]) => [Number(t), v]).sort((a, b) => a[0] - b[0]) });
  }
  const plot = linesChart(series, { height: 240, xMin: 0, xMax: 26, xTicks: [1, 3, 5, 10, 25], xFmt: (t) => (t === 25 ? "LT" : `${fmt.num(t)}${NBSP}г.`),
    yPct: true, tipFmt: (v) => fmt.pct(v, 2), label: "Бескупонные кривые миров" });
  const fig = withTable(plot, () => dataTable([
    Tc("Мир", (k) => `${k} — ${W_[k].name}`, "name"),
    ...["1", "3", "5", "10", "LT"].map((t) => (Nc(t === "LT" ? "LT" : `${t} г.`, (k) => fmt.pct(obj(W_[k].zero_curve)[t], 2)))),
  ], keys));
  const by = obj(d.fair_value.by_world);
  const years = list(obj(W_[keys[0]]).key_rate).map((p) => p.year).slice(0, 5);
  return card({ title: "Миры ставок", tools: fig.button,
    sub: "Мир — согласованная макротраектория: ключевая, инфляция, кривая, рост кредита и средств (банковская надстройка). Веса миров задают слои." },
  el("div", { class: "split" },
    el("div", {}, legend(series.map((s, i) => [i < keys.length ? WORLD_KEYS[i % 3] : "key-line key-ink", s.name])), fig.box),
    dataTable([
      Tc("Мир", (k) => el("span", {}, W_[k].name, hint(k)), "name"),
      Nc("Свой вес", (k) => fmt.pct(obj(W_[k].weights).analytical, 0)),
      Nc("Вменённый", (k) => fmt.pct(obj(W_[k].weights).market_implied, 0)),
      Nc("k_T", (k) => fmt.pct(W_[k].k_t, 1)),
      Nc("g_T", (k) => fmt.pct(W_[k].g_t, 1)),
      Nc("Цена мира", (k) => fmt.rub(obj(by[k]).price)),
    ], keys, { cls: "compact" })),
  el("div", { style: "margin-top:14px" }, dataTable([
    Tc("Мир, по годам", (r) => r[0], "name"),
    ...years.map((y) => (Nc(String(y), (r) => fmt.pct(byYear(r[1], y), 1)))),
  ], keys.flatMap((k) => [[`${k}: ключевая`, W_[k].key_rate], [`${k}: инфляция`, W_[k].cpi], [`${k}: кредит ЮЛ`, obj(W_[k].credit_growth).corporate],
    [`${k}: средства ФЛ`, obj(W_[k].funds_growth).retail]]), { cls: "compact" })),
  foot(`Цена мира — оценка при весе мира 100 %. Миры: ${worldsRecord(d)}, кривые ${fmt.date(obj(obj(d.worlds).source).curve_date)}.`));
}

// Книги ЧПД и передача ставки — единственный дом чувствительности ЧПД к ставке.
function transmissionCard(d) {
  const n = obj(d.nii);
  const t = obj(n.transmission);
  if (!isNum(t.target)) return card({ title: "Книги ЧПД и передача ставки" }, missing("ЧПД"));
  const nw = obj(n.nim_by_world);
  const keys = worldOrder(d).filter((k) => Array.isArray(nw[k]));
  const plot = linesChart(keys.map((k, i) => ({ name: worldName(d, k), color: WORLD_COLORS[i % 3], dots: true, r: 3, points: list(nw.years).map((y, j) => [y, nw[k][j]]) })),
    { xType: "band", categories: list(nw.years), height: 200, yFmt: (v) => fmt.pct(v, 1), tipFmt: (v) => fmt.pct(v, 2), xFmt: (y) => `’${String(y).slice(2)}`, label: "ЧПМ по мирам, упр." });
  const cs = obj(n.current_share);
  const pairs = list(t.pairs);
  const range = list(t.pairs_range);
  const gates = list(obj(d.checks).gates).filter((g) => ["transmission_pairs", "lt_spread_floor"].includes(g.name) && g.fired && g.explanation);
  const roe = (v) => (isNum(v) ? `${fmt.num(v, 2)}${NBSP}п.п.` : "—");
  const dis = obj(n.disclosed);
  const margin = obj(t.loan_margin);
  const rows = [...pairs, pairs.length ? { from: pairs[0].from, to: pairs[pairs.length - 1].to, key_from: pairs[0].key_from, key_to: pairs[pairs.length - 1].key_to,
    value: t.realized, roe_equiv: t.roe_equiv, total: true } : null].filter(Boolean);
  return card({ title: "Книги ЧПД и передача ставки",
    sub: `Передача — ${ruText(t.definition || "")}: ось книги M − N решается точно (инвариант); по парам соседних миров — гейт с коридором; локальная передача по мирам и по клеткам — диагностика.` },
  el("div", { class: "kpis" },
    kpi(fmt.num(t.target, 3), "передача M − N в книге (T*)"),
    kpi(`${fmt.num(t.realized, 3)} ${t.solved ? "✓" : "✗"}`, `реализованная, допуск ${sci(t.tol)}`),
    kpi(fmt.pct(t.nim_lt_target_mgmt, 2), `ЧПМ сквозь цикл, упр. (движок ${fmt.pct(t.target_eng, 2)})`),
    isNum(t.sigma0_liab) ? kpi(`${fmt.signed(t.sigma0 * 100, 2)} / ${fmt.pp(t.sigma0_liab, 2)}`, `сдвиг LT-спредов σ0: активы / пассивы; на активах — ${fmt.pct(t.sigma0_split, 0)} сдвига`)
      : kpi(fmt.pp(t.sigma0, 2), "общий сдвиг LT-спредов σ0"),
    isNum(t.phi_assets) ? kpi(`${fmt.num(t.phi_assets, 3)} / ${fmt.num(t.phi_liab, 3)}`, `сжатие φ со ставкой: спреды кредитных книг / стоимость средств клиентов; на кредитных книгах — ${fmt.pct(t.phi_split, 0)} сжатия φ = ${fmt.num(t.phi, 3)}`)
      : kpi(fmt.num(t.phi, 3), "сжатие спреда со ставкой φ"),
    isNum(dis.nii_per_100bp) ? kpi(fmt.bn(dis.nii_per_100bp, 0), "раскрытая чувствительность ЧПД к ±100 б.п.") : null),
  isNum(dis.nii_per_100bp) ? null : el("p", { class: "muted small", style: "margin-top:8px" }, sentence(`Раскрытой чувствительности ЧПД к ±100 б.п. нет: ${ruText(dis.src || "в фактах её нет")}`)),
  rows.length ? el("div", { style: "margin-top:16px" },
    tileLabel(`Передача по парам соседних миров${range.length === 2 ? `: коридор ${fmt.num(range[0], 2)} … ${fmt.num(range[1], 2)}` : ""}`),
    dataTable([
      Tc("Пара миров", (p) => el("span", {}, p.total ? `${p.to} − ${p.from}` : `${p.from} → ${p.to}`,
        hint(p.total ? "реализованная передача — ось книги (A-N3)" : `${worldName(d, p.from)} → ${worldName(d, p.to)}`)), "name"),
      Nc("Ключевая LT", (p) => `${fmt.num(p.key_from * 100, 1)} → ${fmt.pct(p.key_to, 1)}`),
      Nc("Передача", (p) => el("strong", {}, fmt.num(p.value, 3))),
      Nc("ROE на 1 п.п. ключевой", (p) => roe(p.roe_equiv)),
      Tc("", (p) => (p.total ? badge(t.solved ? "решена точно" : "не решена", t.solved ? "good" : "bad") : p.inside ? badge("в коридоре", "good") : badge("вне коридора", "warn"))),
    ], rows, { cls: "compact", rowClass: (p) => (p.total ? "is-total" : null) }),
    gates.map((g) => detailsBlock(`Гейт «${g.title || g.name}»`, ruText(g.explanation)))) : null,
  el("div", { class: "split", style: "margin-top:16px" },
    el("div", {}, tileLabel("ЧПМ по мирам (упр.)"), legend(keys.map((k, i) => [WORLD_KEYS[i % 3], worldName(d, k)])), plot),
    el("div", {}, tileLabel("Диагностики — гейта нет"), dataTable([
      Tc("Мир", (k) => worldName(d, k), "name"),
      Nc("Локальная T(W)", (k) => fmt.num(obj(t.by_world)[k], 3)),
      Nc("Доля текущих счетов, LT", (k) => fmt.pct(list(obj(cs.by_world)[k]).slice(-1)[0], 1)),
      Object.keys(margin).length ? Nc("Кредитная маржа, п.п.", (k) => fmt.num(margin[k] * 100, 2)) : null,
    ], keys, { cls: "compact" }),
    keys.some((k) => margin[k] < 0) ? el("p", { class: "muted small", style: "margin-top:6px" }, "Кредитная маржа — доходность кредитов минус CoR и ставка балансирующих активов; где она отрицательна, рост кредита стоимость разрушает.") : null,
    el("p", { class: "muted small", style: "margin-top:6px" }, `Передача по клеткам (M, норм., по графику) − (N, …): ${fmt.num(t.realized_cells, 3)}. Доля текущих счетов: якорь ${fmt.pct(cs.c_ref, 1)}, ψ ${fmt.num(cs.psi, 2)}, границы ${list(cs.bounds).map((b) => fmt.pct(b, 0)).join("–")}.`))),
  el("div", { style: "margin-top:14px" }, dataTable([
    Tc("Книга", (b) => el("span", {}, b.title, hint(`${b.side === "asset" ? "актив" : "пассив"} · опора — ${refText(b.ref)}${b.balancing ? " · балансирующая" : ""}${isNum(b.spread_floor) ? ` · пол спреда ${fmt.num(b.spread_floor * 100, 2)}${NBSP}п.п.` : ""}`)), "name"),
    Nc("Остаток, млрд ₽", (b) => fmt.num(b.balance_anchor, 0)),
    Nc("Ставка якоря", (b) => fmt.pct(b.rate_anchor, 2)),
    Nc(el("span", { tip: "ρ — доля разрыва ставки книги к цели, закрываемая за квартал" }, "ρ"), (b) => fmt.num(b.rho, 2)),
    Nc(el("span", { tip: "β — передача ставки опоры книги в стоимость пассива" }, "β"), (b) => (isNum(b.beta) ? fmt.num(b.beta, 3) : "—")),
    Nc("φ", (b) => (b.phi ? "да" : "—")),
    Nc(`LT-спред к опоре: ${keys.join(" / ")}, п.п.`, (b) => (b.spread_lt_world
      ? el("span", { tip: { title: b.title, rows: [["ключ книги (без сдвига и сжатия)", fmt.pp(b.spread_lt, 2)]] } }, keys.map((k) => fmt.signed(obj(b.spread_lt_world)[k] * 100, 2)).join(" / "))
      : fmt.pp(b.spread_lt, 2))),
    ...keys.map((k) => (Nc(`Ставка LT, ${k}`, (b) => fmt.pct(obj(b.rate_lt)[k], 2)))),
  ], list(n.books), { caption: "Книги ЧПД: LT-спред — эффективный, со сдвигом σ0 и сжатием φ (у кредитной книги сжимается спред, у средств клиентов дорожает фондирование)" })));
}

// Цвет клетки — порядок её цены к цене главного тикера (×0,5 … ×3).
const HEAT_STEPS = [0.5, 0.75, 1, 1.5, 2, 3];
function heatClass(price, market) {
  if (!isNum(price) || price <= 0 || !isNum(market) || market <= 0) return "h0";
  let i = 0;
  while (i < HEAT_STEPS.length && price >= HEAT_STEPS[i] * market) i++;
  return `h${i + 1}`;
}
const FLAG_MARKS = { capital_gap: "⚠", roe_below_k: "↓", dividend_cut: "✂", crisis_skip: "✕" };
// Отмена выплаты года шока — не урезание капиталом (М§5.3).
const FLAG_WORDS = { capital_gap: "капитальный разрыв", roe_below_k: "ROE < k", dividend_cut: "дивиденд урезан капиталом", crisis_skip: "выплата года кризиса отменена" };

function gridCard(d) {
  const g = obj(d.grid);
  const cells = list(g.cells);
  if (!cells.length) return card({ title: "Сетка сценариев" }, missing("сетка"));
  const market = d.market.price;
  const worlds = list(g.world_order), regimes = list(g.regime_order), scen = list(g.scenario_order);
  const panels = worlds.map((w) => el("div", {},
    el("div", { class: "heat-title" }, el("strong", {}, `${w} — ${worldName(d, w)}`)),
    el("table", { class: "heat" },
      el("thead", {}, el("tr", {}, el("th", { class: "row-h" }, "режим / капитал"), scen.map((s) => el("th", {}, scenarioName(d, s))))),
      el("tbody", {}, regimes.map((r) => el("tr", {}, el("th", { class: "row-h" }, regimeName(d, r)),
        scen.map((s) => {
          const c = cells.find((x) => x.world === w && x.regime === r && x.scenario === s);
          if (!c) return el("td", {}, "—");
          const flags = list(c.flags);
          return el("td", { class: heatClass(c.price, market), tip: { title: `${worldName(d, w)} · ${regimeName(d, r)} · ${scenarioName(d, s)}`,
            rows: [["цена клетки", fmt.rub(c.price, 1)], ["P/B", fmt.x(c.pb, 2)], ["ROE'_T / k_T", `${fmt.pct(c.roe_t, 1)} / ${fmt.pct(c.k_t, 1)}`],
              [`минимум ${capTitle(d, "n20")}`, fmt.pct(c.n20_min, 1)], ["DPS года якоря", fmt.rub(c.dps_first, 2)], [`вероятность («${layerTitle(d, "analytical")}»)`, fmt.pct(c.p_analytical, 2)]],
            note: flags.map((f) => FLAG_WORDS[f] || f).join("; ") || null } },
          el("span", { class: "p" }, fmt.num(c.price)), el("span", { class: "w" }, fmt.pct(c.p_analytical, 1)),
          flags.length ? el("span", { class: "flag", "aria-label": "флаги" }, flags.map((f) => FLAG_MARKS[f] || "•").join(" ")) : null);
        })))))));
  const steps = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)", "var(--seq-6)"];
  return card({ title: `Сетка: ${fmt.num(cells.length)} ${plural(cells.length, CELL_WORDS)}`,
    sub: `В клетке — цена (₽) и вероятность под весами «${layerTitle(d, "analytical")}»; цвет — порядок цены к рынку ${ticker(d)} (${fmt.rub(market, 2)}). Базис путей — ${basisName(g.basis)}.` },
  el("div", { class: "heat-wrap" }, panels),
  el("div", { class: "scale" }, el("span", {}, "цена клетки к рынку:"),
    ...steps.flatMap((c, i) => [el("i", { style: `background:${c}` }), i === 0 ? `<${NBSP}×${fmt.num(HEAT_STEPS[0], 2)}` : `×${fmt.num(HEAT_STEPS[i - 1], 2)}${i === steps.length - 1 ? "+" : ""}`]),
    el("span", { class: "gap" }, Object.entries(FLAG_MARKS).map(([k, v]) => `${v} ${FLAG_WORDS[k]}`).join(" · "))));
}

function varianceCard(d) {
  const v = obj(obj(d.variance).price);
  const names = { world: "мир ставок", regime: "режим", scenario: "сценарий капитала", interaction: "взаимодействие" };
  const rows = Object.entries(v).filter(([, s]) => isNum(s)).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return card({ title: "Что разводит клетки", span: 6 }, missing("разброс сетки"));
  const max = Math.max(...rows.map((r) => r[1]));
  return card({ title: "Что разводит клетки", span: 6, sub: sentence(upperFirst(ruText(d.variance.method || "доли дисперсии цены клеток"))) },
    el("div", { class: "hbars" }, rows.map(([k, s]) => hbar(names[k] || k, s, max, { color: "var(--series-neutral)" }))),
    foot("Это описание сценариев сетки, а не неопределённость печатаемого числа — её показывает полоса."));
}

// Дисконт за управление — подписанные каналы; итог — число выпуска (`sum_signed`).
function governanceCard(d) {
  const g = obj(d.governance);
  const comps = list(g.components);
  if (!comps.length) return card({ title: "Дисконт за управление", span: 6 }, missing("дисконт за управление"));
  let run = 0;
  const steps = comps.map((c) => { const v = (c.sign || 1) * c.value; const from = run; run += v; return { title: c.name, from, to: run, text: fmt.pp(v, 2), hint: ruText(c.basis) }; });
  steps.push({ title: "Дисконт g", from: 0, to: g.sum_signed, total: true, text: fmt.pct(g.sum_signed, 2) });
  return card({ title: `Дисконт за управление: ${fmt.pct(g.discount, 2)}`, span: 6, sub: "Каналы снизу вверх; канал без опоры — 0. Налоги, капитал и кризис в дисконт не входят." },
    waterfall(steps),
    isNum(g.price_at_low) ? foot(`Точка на краях оси дисконта: ${fmt.rub(g.price_at_low, 1)} … ${fmt.rub(g.price_at_high, 1)}.`) : null);
}

/* ── экран «Ближайший отчёт» ── */

function screenReport(d) {
  const nr = obj(d.next_report);
  const nf = obj(obj(d.calendar).next_fact);
  const root = el("div", { class: "screen" },
    screenHead("Ближайший отчёт", nf.title ? `${nf.title}: что он даст оценке` : `Что даст отчёт за ${periodLabel(nr.period)}`,
      `Два ритма: месячная РСБУ банка — вход нау-каста, и квартальная МСФО группы — факт для книги, журнала и обучения вероятностей режимов. `
      + `Нау-каст к цене не подключён до допуска: экран показывает, что сделает с оценкой факт ${periodLabel(nr.period)}, и копит зачёт против наивных эталонов.`));
  root.append(section("", countdownCard(d), nowcastCard(d)));
  root.append(section("", yearDpsCard(d)));
  root.append(section("", impactCard(d)));
  root.append(more([retroBenchmarksCard(d), journalCard(d), rasMonthsCard(d), indicatorsCard(d), eventsCard(d, 99, 12)]));
  return root;
}

function countdownCard(d) {
  const a = obj(obj(d.nowcast).admission);
  const status = { collecting: ["копим события", "warn"], passed: ["допущен", "good"], failed: ["не допущен", "bad"] }[a.status] || [a.status || "—", null];
  const needed = isNum(a.events_needed) ? a.events_needed : 0;
  const mse = Object.entries(obj(a.mse_ratio)).filter(([, v]) => isNum(v));
  return card({ title: "До отчёта", span: 5, sub: "отсчёт — от даты оценки" },
    nextRasLine(d) || empty("Даты следующего месяца РСБУ в выпуске нет."),
    nextFactLine(d) || empty("Даты МСФО открытого квартала в выпуске нет."),
    earlierReport(d),
    el("div", { class: "admission" },
      el("div", { class: "admission-top" }, tileLabel("Допуск нау-каста к цене"), badge(status[0], status[1])),
      needed ? el("div", { class: "progress", role: "img", "aria-label": `${fmt.num(a.events_scored)} из ${fmt.num(needed)} событий` },
        Array.from({ length: needed }, (_, i) => el("i", { class: i < Math.min(a.events_scored || 0, needed) ? "on" : null }))) : null,
      el("p", { class: "ink-2 small", style: "margin-top:8px" }, `Засчитано ${fmt.num(a.events_scored)} из ${fmt.num(needed)}; первое засчитываемое — ${dayOrPeriod(a.first_event)}, решение — не раньше ${dayOrPeriod(a.earliest_decision)}.`),
      mse.length ? el("p", { class: "ink-2 small" }, `Отношение MSE к лучшему эталону: ${mse.map(([k, v]) => `${targetTitle(d, k)} ${fmt.num(v, 2)}`).join(", ")}.`) : null,
      a.rule ? detailsBlock("Правило допуска", sentence(upperFirst(ruText(a.rule)))) : null));
}

// Дата («2027-10-28» → «28.10.2027») или период («2027Q2» → «2 кв. 2027»).
function dayOrPeriod(x) { return parseDay(x) ? fmt.date(x) : periodLabel(x); }

function targetTitle(d, key) {
  const t = list(obj(d.nowcast).targets).find((x) => x.key === key);
  return t ? t.title : key;
}

// Значение цели нау-каста в её единице: прибыль — млрд ₽, ЧПМ и CoR — доли.
function targetValue(d, key, v) {
  const t = list(obj(d.nowcast).targets).find((x) => x.key === key);
  if (!isNum(v)) return "—";
  return t && t.unit === "bn" ? fmt.bn(v, 1) : fmt.pct(v, 2);
}
function targetError(d, key, v) {
  const t = list(obj(d.nowcast).targets).find((x) => x.key === key);
  if (!isNum(v)) return "—";
  return t && t.unit === "bn" ? `± ${fmt.bn(v, 1)}` : `± ${fmt.num(v * 100, 2)}${NBSP}п.п.`;
}

let NOWCAST_TARGET = null;

function nowcastCard(d) {
  const nc = obj(d.nowcast);
  const q = obj(nc.quarter);
  const targets = list(nc.targets);
  const by = obj(q.by_target);
  if (!targets.length || !Object.keys(by).length) return card({ title: "Нау-каст квартала", span: 7 }, missing("нау-каст"));
  if (!NOWCAST_TARGET || !by[NOWCAST_TARGET]) NOWCAST_TARGET = targets.find((t) => by[t.key]).key;
  const body = el("div");
  const draw = () => {
    const key = NOWCAST_TARGET;
    const b = obj(by[key]);
    const months = list(q.months);
    const field = key === "ni_q" ? "ni" : key === "nim_q" ? "nii" : "llp";
    const fieldName = { ni: "прибыль", nii: "ЧПД", llp: "резервы" }[field];
    const bars = columnsChart(months.map((m) => ({ key: m.month, label: periodWord(m.month), value: m[field], faded: m.status !== "known",
      color: m.status === "known" ? "var(--model)" : "var(--series-neutral)", tipTitle: periodLabel(m.month),
      tipRows: [["статус", m.status === "known" ? `известен (${m.source === "form102" ? "форма 0409102" : "релиз"})` : "оценка профилем"]] })),
    { height: 150, valueName: `${fieldName}, млрд ₽`, fmt: (v) => fmt.bn(v, 1), short: (v) => fmt.num(v, 0), label: `${upperFirst(fieldName)} по месяцам квартала, РСБУ банка` });
    const iv = list(b.interval);
    // Эталоны — выпуска и последней записи журнала этой цели; на полосу встают те, что в пределах её оси.
    const names = benchNames(d);
    const entry = list(obj(nc.journal).entries).filter((e) => e.target === key && e.period === q.period).sort((x, y) => String(y.recorded_at).localeCompare(String(x.recorded_at)))[0];
    const bench = list(b.benchmarks).map((x) => [x.name, x.value]).concat(Object.entries(obj(obj(entry).benchmarks)).map(([k, v]) => [names[k], v]));
    const core = [b.forecast, b.expectation, ...iv].filter(isNum);
    const pad = (Math.max(...core) - Math.min(...core)) * 0.1 || Math.abs(Math.max(...core)) * 0.05;
    const shown = bench.filter(([name, v], i) => name && isNum(v) && v !== b.expectation && v >= Math.min(...core) - pad && v <= Math.max(...core) + pad
      && bench.findIndex((x) => x[0] === name) === i);
    const strip = stripChart([
      { v: b.forecast, kind: "model", text: `нау-каст ${targetValue(d, key, b.forecast)}` },
      { v: b.expectation, kind: "neutral", text: `ожидание ${targetValue(d, key, b.expectation)}`, tip: { title: "Ожидание модели", rows: [["значение", targetValue(d, key, b.expectation)]] } },
      ...shown.map(([name, v]) => ({ v, kind: "bench", tip: { title: `Эталон: ${name}`, rows: [["значение", targetValue(d, key, v)]] } })),
    ], { band: iv, fmt: (v) => (key === "ni_q" ? fmt.num(v) : fmt.num(v * 100, 2)), label: "Нау-каст, ожидание модели и эталоны на одной шкале" });
    fill(body, 
      tileLabel(`${upperFirst(fieldName)} по месяцам, РСБУ банка: ${fmt.num(q.months_known)} из ${fmt.num(months.length)} известны`), bars,
      el("div", { class: "kpis", style: "margin-top:10px" },
        kpi(targetValue(d, key, b.ras_estimate), "оценка квартала по РСБУ"),
        kpi(isNum(b.bridge) ? fmt.num(b.bridge, 3) : "—", "сезонный мост МСФО / РСБУ"),
        kpi(targetValue(d, key, b.expectation), `ожидание модели (${basisName(b.basis)})`),
        kpi(isNum(b.w) ? fmt.num(b.w, 2) : "—", "вес индикаторов w"),
        kpi(targetValue(d, key, b.forecast), `нау-каст ${targetError(d, key, b.std_error)}`)),
      el("div", { style: "margin-top:10px" }, strip),
      legend([isNum(iv[0]) && isNum(iv[1]) && ["key-b50", "интервал нау-каста"], isNum(b.forecast) && ["key-dot key-model", "нау-каст"], isNum(b.expectation) && ["key-dash", "ожидание модели"],
        shown.length && ["key-dot key-third", "эталоны"]]),
      el("div", { class: "card-foot" }, b.equation ? detailsBlock("Уравнение словами", sentence(upperFirst(ruText(b.equation)))) : null));
  };
  draw();
  return card({ title: `Нау-каст ${periodLabel(q.period)}`, span: 7,
    sub: `входы — ${inputUnit(d).many} РСБУ банка; цель — ${modelUnit(d).one} МСФО; ${nc.connected_to_price ? "подключён к цене" : "к цене не подключён"}` },
  el("div", { class: "pick-row" }, chooser(targets.filter((t) => by[t.key]).map((t) => ({ value: t.key, label: t.title })), NOWCAST_TARGET, (v) => { NOWCAST_TARGET = v; draw(); }, "Цель нау-каста")),
  body);
}

// Прибыль года → DPS: факт + нау-каст + модель → − купон AT1 → × доля политики ÷ размещённые акции.
function yearDpsCard(d) {
  const y = obj(obj(d.nowcast).year);
  if (!isNum(y.ni_year)) return card({ title: "Прибыль года → дивиденд" }, missing("прибыль года"));
  const steps = [];
  let run = 0;
  for (const [title, v] of [[`Факт отчётных ${modelUnit(d).pgen} (${basisName("ifrs")})`, y.ni_fact], [`Открытый ${modelUnit(d).one}: смесь заголовка и отклонение нау-каста`, y.ni_quarter],
    [`Смесь заголовка: оставшиеся ${modelUnit(d).many}`, y.ni_rest]]) {
    if (!isNum(v)) continue;
    steps.push({ title, from: run, to: run + v, text: fmt.num(v, 1) });
    run += v;
  }
  steps.push({ title: `Прибыль акционерам ${y.year}`, from: 0, to: y.ni_year, total: true, text: fmt.num(y.ni_year, 1), hint: isNum(y.ni_year_se) ? `± ${fmt.num(y.ni_year_se, 1)}` : "ошибка года не оценена" });
  steps.push({ title: "Купон AT1 после налога", from: y.ni_year, to: y.base, text: fmt.num(-y.at1_coupon_after_tax, 1) });
  steps.push({ title: "База дивиденда", from: 0, to: y.base, total: true, text: fmt.num(y.base, 1) });
  const cc = obj(y.capital_check);
  const shares = obj(obj(d.meta).shares);
  return card({ title: `Прибыль ${y.year} → дивиденд на акцию`, sub: "млрд ₽; год — на одном слое (смесь заголовка): нау-каст двигает его только своим отклонением от ожидания; дивиденд — формулой политики на размещённые акции обеих категорий" },
    el("div", { class: "split wide-left" },
      waterfall(steps),
      el("div", { class: "kpis" },
        kpi(fmt.rub(y.dps, 2), `DPS: ${fmt.pct(y.payout, 0)} базы на ${fmt.num(shares.issued_mln, 1)} млн акций; ${dpsInterval(y, 2)}`),
        kpi(fmt.rub(y.dps_model, 2), "DPS политики на прибыли смеси заголовка (без нау-каста)"),
        ...tickers(d).map((t) => kpi(fmt.pct(obj(y.yield)[t], 1), `доходность к ${t}`)),
        kpi(`${fmt.pct(cc.n20_expected, 1)} ${cc.ok ? "≥" : "<"} ${fmt.pct(cc.requirement, 1)}`, `проверка капитала: ${capTitle(d, "n20")} после выплаты против порога`))),
    y.note ? foot(sentence(upperFirst(ruText(y.note)))) : null);
}

// Ориентиры «Что даст отчёт» на оси факта: нейтральное значение, нау-каст с интервалом, эталоны и гайденс.
// На полном графике ось накрывает их не дальше половины размаха таблицы; что за осью — не рисуется и в легенду не идёт.
function impactMarks(d, kind, compact = false) {
  const nr = obj(d.next_report);
  const rows = list(kind === "cor" ? nr.cor_table : nr.nim_table).filter((r) => isNum(r[kind]) && isNum(r.median));
  const b = obj(obj(obj(obj(d.nowcast).quarter).by_target)[`${kind}_q`]);
  const iv = list(b.interval).filter(isNum);
  const all = list(nr.benchmarks).filter((x) => isNum(x[kind]));
  const neutral = obj(obj(nr.neutral)[kind]).value;
  const xs = rows.map((r) => r[kind]);
  const lo = Math.min(...xs), hi = Math.max(...xs), span = hi - lo;
  const marks = compact ? [] : [neutral, b.forecast, ...iv, ...all.map((x) => x[kind])].filter(isNum);
  const x0 = Math.max(Math.min(lo, ...marks), lo - span / 2) - span * 0.06;
  const x1 = Math.min(Math.max(hi, ...marks), hi + span / 2) + span * 0.06;
  const on = (v) => isNum(v) && v >= x0 && v <= x1;
  const bench = compact ? [] : all.filter((x) => on(x[kind]));
  return { rows, x0, x1, neutral: on(neutral) ? neutral : null, forecast: !compact && on(b.forecast) ? b.forecast : null,
    iv: !compact && iv.length === 2 && Math.min(x1, iv[1]) > Math.max(x0, iv[0]) ? iv : null,
    bench, guide: bench.some((x) => x.key === "guidance"), other: bench.some((x) => x.key !== "guidance"),
    band: rows.filter((r) => isNum(r.median_low) && isNum(r.median_high)) };
}

// «Что даст отчёт»: факт CoR или ЧПМ квартала (упр.) → медиана и её ось ставок; ориентиры — на той же оси.
function impactChart(d, kind, compact = false) {
  const median = obj(d.fair_value.headline).median;
  const market = d.market.price;
  const { rows, x0, x1, neutral, forecast, iv, bench, guide, other, band } = impactMarks(d, kind, compact);
  return chart((W) => {
    if (rows.length < 2) return svgBox(W, 20);
    const H = compact ? 200 : 300;
    const top = compact ? 16 : 52;
    const m = { l: 50, r: 14, t: top, b: 30 };
    const x = scale(x0, x1, m.l, W - m.r);
    const vals = rows.flatMap((r) => [r.median_low, r.median_high, r.median]).concat([median, market]).filter(isNum);
    const vpad = (Math.max(...vals) - Math.min(...vals)) * 0.08;
    const y = scale(Math.max(0, Math.min(...vals) - vpad), Math.max(...vals) + vpad, H - m.b, m.t);
    const svg = svgBox(W, H, `Медиана в зависимости от факта ${kind === "cor" ? "CoR" : "ЧПМ"}`);
    for (const t of ticks(y.d[0], y.d[1], compact ? 3 : 5)) svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }), text(m.l - 8, y(t) + 4, fmt.num(t), { class: "tick", "text-anchor": "end" }));
    const xMarks = ticks(x0, x1, W < 420 ? 4 : 7);
    const xl = pctTicks(xMarks);
    for (const t of xMarks) { const s = xl(t), w = textWidth(s, 12.5, 400); if (x(t) - w / 2 >= 0 && x(t) + w / 2 <= W) svg.append(text(x(t), H - 8, s, { class: "tick", "text-anchor": "middle" })); }
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    if (band.length > 1) {
      const up = band.map((r) => `${x(r[kind]).toFixed(1)},${y(r.median_high).toFixed(1)}`);
      const down = band.slice().reverse().map((r) => `${x(r[kind]).toFixed(1)},${y(r.median_low).toFixed(1)}`);
      svg.append(sv("path", { d: `M${up.join(" L")} L${down.join(" L")} Z`, fill: "var(--model-wash-1)" }));
    }
    for (const [v, color, t] of [[median, "var(--ink)", `сейчас ${fmt.rub(median)}`], [market, "var(--market)", `${ticker(d)} ${fmt.rub(market)}`]]) {
      if (!isNum(v)) continue;
      svg.append(line(m.l, y(v), W - m.r, y(v), { stroke: color, "stroke-width": 1.2 }));
    }
    if (neutral !== null) {
      svg.append(line(x(neutral), m.t - 4, x(neutral), H - m.b, { stroke: "var(--ink-2)", "stroke-width": 1.2, "stroke-dasharray": "1 3",
        tip: { title: `Нейтральная ${kind === "cor" ? "CoR" : "ЧПМ"}`, rows: [["значение", fmt.pct(neutral, 2)]] } }));
    }
    svg.append(sv("path", { d: "M" + rows.map((r) => `${x(r[kind]).toFixed(1)},${y(r.median).toFixed(1)}`).join(" L"), fill: "none", stroke: "var(--model)", "stroke-width": 2.2, "stroke-linejoin": "round" }));
    for (const r of rows) {
      svg.append(sv("circle", { cx: x(r[kind]), cy: y(r.median), r: compact ? 3.5 : 4.5, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2,
        tip: { title: `${kind === "cor" ? "CoR" : "ЧПМ"} квартала ${fmt.pct(r[kind], 1)}`, rows: [["медиана", fmt.rub(r.median, 1)], ["ось ставок", `${fmt.num(r.median_low)}–${fmt.num(r.median_high)}${THIN}₽`],
          ["точка", fmt.rub(r.point, 1)], ...Object.entries(obj(r.posterior)).map(([k, v]) => [regimeName(d, k), fmt.pct(v, 1)])] } }));
    }
    // Подпись горизонтали встаёт туда, где нет точек кривой: с нужного края или с другого, над линией или под ней.
    const put = (v, s, right, under) => {
      if (!isNum(v)) return;
      const w = textWidth(s, 12.5, 520);
      const spots = [[right, under], [right, !under], [!right, under], [!right, !under]].map(([r, below]) => [r ? W - m.r - w - 2 : m.l + 4, below ? Math.min(H - m.b - 4, y(v) + 15) : y(v) - 6]);
      const [sx, sy] = spots.find(([px, py]) => !rows.some((r) => x(r[kind]) > px - 7 && x(r[kind]) < px + w + 7 && y(r.median) > py - 18 && y(r.median) < py + 9)) || spots[0];
      svg.append(label(sx, sy, s));
    };
    put(median, `сейчас ${fmt.rub(median)}`, false, false);
    put(market, `${ticker(d)} ${fmt.rub(market)}`, true, true);
    if (!compact) {
      const by = m.t - 26;
      if (iv) {
        const lo = Math.max(x0, iv[0]), hi = Math.min(x1, iv[1]);
        svg.append(sv("rect", { x: x(lo), y: by - 5, width: Math.max(2, x(hi) - x(lo)), height: 10, rx: 5, fill: "var(--model-wash-3)",
          tip: { title: "Нау-каст квартала", rows: [["от", fmt.pct(iv[0], 2)], ["до", fmt.pct(iv[1], 2)]] } }));
      }
      // Точка прогноза рисуется и без интервала (нау-каст без ошибки — ожидание модели).
      if (forecast !== null) svg.append(sv("circle", { cx: x(forecast), cy: by, r: 5, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2,
        tip: { title: "Нау-каст квартала", rows: [["значение", fmt.pct(forecast, 2)]] } }));
      for (const bm of bench) {
        svg.append(sv("circle", { cx: x(bm[kind]), cy: by, r: 4.5, fill: bm.key === "guidance" ? "var(--market)" : "var(--third)", stroke: "var(--surface)", "stroke-width": 2,
          tip: { title: bm.name, rows: [["значение", fmt.pct(bm[kind], 2)]], note: bm.note ? ruText(bm.note) : null } }));
      }
      const names = [forecast !== null && `нау-каст ${fmt.pct(forecast, 2)}`, other && "эталоны", guide && "гайденс"].filter(Boolean);
      if (names.length) svg.append(label(m.l, by - 12, names.join(" · ")));
    }
    return svg;
  }, `Что даст отчёт: ${kind === "cor" ? "CoR" : "ЧПМ"} квартала и медиана`);
}

let IMPACT_KIND = "cor";

function impactCard(d) {
  const nr = obj(d.next_report);
  if (!list(nr.cor_table).length && !list(nr.nim_table).length) {
    return card({ title: "Что даст отчёт" }, nr.status === "not_computed" ? empty(sentence(`Таблицы не считались: ${ruText(nr.reason || "быстрая сборка")}`)) : missing("что даст отчёт"));
  }
  const w = diagWords(nr.target);
  const body = el("div");
  const slot = el("span");
  const draw = () => {
    const kind = IMPACT_KIND;
    const rows = list(kind === "cor" ? nr.cor_table : nr.nim_table);
    const f = withTable(impactChart(d, kind, false), () => dataTable([
      Tc(`${kind === "cor" ? "CoR" : "ЧПМ"} квартала, упр.`, (r) => fmt.pct(r[kind], 1), "name"),
      ...regimeOrder(d).map((k) => (Nc(regimeName(d, k), (r) => fmt.pct(obj(r.posterior)[k], 1)))),
      Nc(`${upperFirst(w.subj)}, ₽`, (r) => el("strong", {}, fmt.num(r.median, 1))),
      Nc("Ось ставок, ₽", (r) => `${fmt.num(r.median_low)}–${fmt.num(r.median_high)}`),
      Nc("Сдвиг, ₽", (r) => fmt.signed(r.d_median, 1)),
    ], rows));
    slot.replaceChildren(f.button);
    const exp = obj(nr.expectation);
    const mine = (r) => (kind === "cor" ? r.cor_mgmt : r.nim_mgmt);
    const by = list(exp.by_regime).map(mine).filter(isNum);
    // Обучение (М§12–§13): равные ожидания режимов факт не различает; факт, равный ожиданию смеси, режимы перевзвешивает.
    const learn = by.length < 2 ? "" : Math.max(...by) - Math.min(...by) < 5e-6
      ? ` Ожидания ${kind === "cor" ? "CoR" : "ЧПМ"} у режимов равны: такой факт вероятностей режимов не меняет, оценку двигает только сам квартал.`
      : " Ожидание — среднее по режимам: равный ему факт тоже перевзвешивает режимы, поэтому нейтральное значение от него отличается.";
    const M = impactMarks(d, kind);
    fill(body,
      legend([["key-line key-model", w.subj], M.band.length > 1 && ["key-b80", "ось ставок"], ["key-line key-ink", `${w.subj} сейчас`], ["key-line key-market", `рынок, ${ticker(d)}`],
        M.neutral !== null && ["key-dash", "нейтральное значение"], M.iv && ["key-b50", "интервал нау-каста"], M.forecast !== null && ["key-dot key-model", "нау-каст"],
        M.other && ["key-dot key-third", "эталоны"], M.guide && ["key-dot key-market", "гайденс"]]),
      f.box,
      foot(neutralSentence(d, kind),
        ` Ожидание модели на ${periodLabel(nr.period)}: CoR ${fmt.pct(exp.cor_mgmt, 2)}, ЧПМ ${fmt.pct(exp.nim_mgmt, 2)} (упр.); по режимам — `
        + list(exp.by_regime).map((r) => `${lowerFirst(regimeName(d, r.regime))} ${fmt.pct(mine(r), 2)}`).join(", ") + "." + learn));
  };
  draw();
  return card({ title: `Что даст отчёт: факт ${periodLabel(nr.period)} → оценка`,
    sub: `Факт CoR или ЧПМ квартала сдвигает вероятности режимов по правилу A-P2u, а с ними — ${w.acc}. Строка отвечает «что даст это число отчёта»: второй показатель в ней считается ненаблюдавшимся. Базис значений — упр.; мост в движок применяет сборка.`,
    tools: el("div", { class: "pick-row", style: "margin:0" }, chooser([{ value: "cor", label: "CoR" }, { value: "nim", label: "ЧПМ" }], IMPACT_KIND, (v) => { IMPACT_KIND = v; draw(); }, "Показатель отчёта"), slot) },
  body);
}

function benchNames(d) {
  const out = { ...obj(obj(obj(d.nowcast).retro).titles) };
  for (const b of list(obj(d.next_report).benchmarks)) out[b.key] = out[b.key] || b.name;
  for (const t of Object.values(obj(obj(obj(d.nowcast).quarter).by_target))) for (const b of list(obj(t).benchmarks)) out[b.key] = out[b.key] || b.name;
  return out;
}

// Ретро эталонов прибыли квартала: горизонт — переключателем, если в выпуске оба.
let RETRO_HORIZON = null;

function retroBenchmarksCard(d) {
  const base = obj(obj(d.nowcast).retro);
  const byH = obj(base.by_horizon);
  const horizons = Object.keys(byH).filter((h) => list(obj(byH[h]).periods).length);
  if (!list(base.periods).length && !horizons.length) return card({ title: "Эталоны на истории", span: 6 }, missing("ретро-проверка эталонов"));
  if (!RETRO_HORIZON || !byH[RETRO_HORIZON]) RETRO_HORIZON = byH[base.horizon] ? base.horizon : horizons[0] || null;
  const names = benchNames(d);
  const body = el("div");
  const pick = horizons.length > 1 ? el("div", { class: "pick-row" }, chooser(horizons.map((h) => ({ value: h, label: h })), RETRO_HORIZON, (v) => { RETRO_HORIZON = v; draw(); }, "Горизонт")) : null;
  const draw = () => {
    const r = { ...base, ...obj(byH[RETRO_HORIZON]) };
    const periods = list(r.periods);
    const name = (k) => names[k] || k;
    const keys = Object.keys(obj(r.rmse));
    const plot = linesChart([
      { name: "факт", color: "var(--ink)", dots: true, r: 3.5, points: periods.map((p) => [p.period, p.actual]) },
      { name: `эталон: ${name(r.main)}`, color: "var(--third)", line: false, dots: true, r: 4, points: periods.map((p) => [p.period, obj(p.benchmarks)[r.main]]) },
    ], { xType: "band", categories: periods.map((p) => p.period), height: 220, yFmt: (v) => fmt.num(v), tipFmt: (v) => fmt.bn(v, 1), xFmt: periodShort, label: "Прибыль квартала: факт и главный эталон" });
    fill(body,
      legend([["key-line key-ink", "факт"], ["key-dot key-third", `главный эталон: ${name(r.main)}`]]), plot,
      el("div", { style: "margin-top:12px" }, dataTable([
        Tc("Эталон", (k) => el("span", {}, upperFirst(name(k)), k === r.main ? hint("главный — по нему зачёт") : null), "name"),
        Nc("RMSE", (k) => fmt.pct(obj(r.rmse)[k], 1)),
        Nc("Смещение", (k) => fmt.signedBn(obj(r.bias)[k], 1)),
        Nc(`${upperFirst(modelUnit(d).pgen)}`, (k) => fmt.num(obj(r.n)[k])),
      ], keys, { cls: "compact", rowClass: (k) => (k === r.main ? "is-pick" : null) })));
  };
  draw();
  return card({ title: "Эталоны на истории: планка для нау-каста", span: 6,
    sub: `прибыль квартала (${basisName("ifrs")}); RMSE — доля факта, смещение (эталон − факт) — млрд ₽; горизонт — дней до отчёта${horizons.length > 1 ? "" : `: ${base.horizon || "—"}`}` },
  pick, body);
}

// Релиз РСБУ банка по месяцам и сверка прибыли с формой 0409102.
function rasMonthsCard(d) {
  const m = obj(obj(d.nowcast).months);
  const rows = list(m.rows);
  if (!rows.length) return card({ title: "РСБУ банка по месяцам" }, missing("месяцы РСБУ"));
  const f102 = list(obj(d.nowcast).form102);
  const bn = (v) => fmt.num(v, 1);
  return card({ title: `${upperFirst(inputUnit(d).many)} РСБУ банка`, sub: `базис — ${basisName(m.basis)}; млрд ₽ и доли; последний — ${periodLabel(rows[rows.length - 1].month)}` },
    sparkline({ title: "прибыль месяца", history: { date: rows.map((r) => r.month), value: rows.map((r) => r.ni) } }, (v) => fmt.bn(v, 1), `за ${fmt.num(rows.length)} мес.`),
    el("div", { style: "margin-top:10px" }, dataTable([
      Tc("Месяц", (r) => periodLabel(r.month), "name nowrap"),
      Nc("ЧП", (r) => bn(r.ni)),
      Nc("С начала года", (r) => bn(r.ni_ytd)),
      Nc("ЧПД", (r) => bn(r.nii)),
      Nc("Комиссии", (r) => bn(r.fees)),
      Nc("Резервы", (r) => bn(r.llp)),
      Nc("Расходы", (r) => bn(r.opex)),
      Nc("CoR", (r) => fmt.pct(r.cor, 2)),
      Nc("ROE", (r) => fmt.pct(r.roe, 1)),
      Nc("Кредиты ЮЛ", (r) => fmt.num(r.loans_corporate, 0)),
      Nc("Кредиты ФЛ", (r) => fmt.num(r.loans_retail, 0)),
      Nc("Средства ФЛ", (r) => fmt.num(r.funds_retail, 0)),
      Nc("Средства ЮЛ", (r) => fmt.num(r.funds_corporate, 0)),
      Nc(capTitle(d, "n1_0"), (r) => fmt.pct(r.n1_0, 1)),
      Nc(capTitle(d, "n11_observed"), (r) => fmt.pct(r.n1_1, 1)),
    ], rows.slice().reverse(), { cls: "compact" })),
    f102.length ? el("div", { style: "margin-top:12px" }, tileLabel("Сверка прибыли релиза с формой 0409102"),
      el("ul", { class: "list" }, f102.map((x) => el("li", {}, el("span", { class: "t" }, periodLabel(x.month),
        el("span", { class: "muted" }, isNum(x.ni) ? `форма ${fmt.bn(x.ni, 1)} · релиз ${fmt.bn(x.release_ni, 1)}` : "форма ещё не раскрыта")),
      el("span", { class: "v" }, x.ok === true ? badge("совпало", "good") : x.ok === false ? badge(`расхождение ${fmt.bn(x.diff, 1)}`, "bad") : badge("ждём форму", "out")))))) : null);
}

// Значение плитки в её единице — код П§0.2: доля, млрд ₽, ₽, уровень, штуки; прочие коды — общим правилом.
function indicatorValue(item, v = item.value) {
  if (!isNum(v)) return "—";
  switch (item.unit) {
    case "share": case "pct": return fmt.pct(v, 2);
    case "bn": return fmt.bn(v, Math.abs(v) >= 1000 ? 0 : 1);
    case "price": case "rub": return fmt.rub(v, 2);
    case "level": case "count": return fmt.num(v, 0);
    case "pp": case "bp": case "years": return formatByUnit(v, item.unit);
    default: return fmt.num(v, Math.abs(v) < 10 ? 2 : 0);
  }
}

function indicatorsCard(d) {
  const ind = obj(d.indicators);
  const tiles = list(ind.tiles);
  if (!tiles.length) return card({ title: "Опережающие индикаторы" }, missing("индикаторы"));
  const groups = list(ind.groups);
  const main = [];
  for (const g of groups) main.push(...tiles.filter((t) => t.group === g.id && t.status !== "missing").slice(0, 2));
  const top = main.slice(0, 8);
  const tile = (i) => el("div", { class: "ind-tile" },
    tileLabel(i.title),
    el("span", { class: "ind-value" }, i.status === "missing" ? "—" : indicatorValue(i)),
    el("span", { class: "muted small" }, i.status === "missing" ? sentence(upperFirst(ruText(i.reason || "ряда нет"))) : `${dayOrPeriod(i.date)} · ${basisName(i.basis)}${i.status === "stale" ? " · устарел" : ""}`),
    i.status === "missing" ? null : sparkline(i, (v) => indicatorValue(i, v)));
  return card({ title: "Опережающие индикаторы", sub: `${fmt.num(tiles.length)} ${plural(tiles.length, ["ряд", "ряда", "рядов"])}; у плиток — линия и мин–макс за год; ряд без данных — с причиной` },
    el("div", { class: "ind-tiles" }, top.map(tile)),
    el("div", { class: "stack", style: "margin-top:16px" }, groups.map((g) => {
      const rows = tiles.filter((t) => t.group === g.id);
      return rows.length ? detailsBlock(`${g.title} · ${rows.length}`, dataTable([
        Tc("Ряд", (i) => i.title, "name"),
        Nc("Значение", (i) => indicatorValue(i)),
        Nc("Изменение", (i) => (isNum(i.change) ? (["share", "pct"].includes(i.unit) ? fmt.pp(i.change, 2) : fmt.signed(i.change, 1)) : "—")),
        Nc("Дата", (i) => dayOrPeriod(i.date)),
        Tc("Источник", (i) => srcText(i.source), "txt"),
      ], rows, { cls: "compact", detail: (i) => (i.status === "missing" && i.reason ? ruText(i.reason) : null) })) : null;
    })));
}

function journalCard(d) {
  const j = obj(obj(d.nowcast).journal);
  const rows = list(j.entries);
  const names = benchNames(d);
  return card({ title: "Журнал прогнозов", span: 6, sub: `неизменяемые записи 4 последних событий МСФО; всего записей — ${fmt.num(j.total_entries || 0)}` },
    rows.length ? dataTable([
      Tc("Цель", (r) => el("span", {}, targetTitle(d, r.target), hint(`${periodLabel(r.period)} · ${r.horizon}`)), "name"),
      Nc("Прогноз", (r) => el("strong", {}, targetValue(d, r.target, r.forecast))),
      Nc("Факт", (r) => targetValue(d, r.target, r.actual)),
      Nc("Записан", (r) => fmt.date(mskDay(r.recorded_at))),
    ], rows, { detail: (r) => (Object.keys(obj(r.benchmarks)).length ? el("span", { class: "muted" },
      "Эталоны: " + Object.entries(r.benchmarks).map(([k, v]) => `${lowerFirst(names[k] || k)} ${targetValue(d, r.target, v)}`).join("; ")) : null) }) : empty("Журнал пуст: первая запись — перед первым событием МСФО после начала журнала."),
    j.rule ? foot(sentence(upperFirst(ruText(j.rule)))) : null);
}

/* ── экран «Капитал и дивиденды» ── */

let CAP_PICK = "mix";

function screenCapital(d) {
  const C = obj(d.capital);
  const S = obj(C.scenarios);
  const keys = scenarioOrder(d).filter((k) => S[k]);
  if (CAP_PICK !== "mix" && !S[CAP_PICK]) CAP_PICK = "mix";
  const root = el("div", { class: "screen" },
    screenHead("Капитал и дивиденды", `${capTitle(d, "n20")}, требования и дивиденд`,
      `Нормативы на отчётную дату и их путь против требования и регуляторного пола по сценариям капитала; мост от капитала к цене; дивидендная политика, `
      + `следующая выплата и дивиденды по годам. Переключатель управляет картой пути нормативов; остальные карточки — смесь заголовка.`));
  const path = el("div", { class: "grid" });
  const drawPath = () => fill(path, ratioPathCard(d, CAP_PICK));
  drawPath();
  root.append(section("", capitalKpis(d)));
  root.append(el("div", { class: "section" }, el("div", { class: "pick-row" }, el("span", { class: "muted" }, "Путь нормативов:"),
    chooser([{ value: "mix", label: "Ожидание" }, ...keys.map((k) => ({ value: k, label: scenarioName(d, k), hint: fmt.pct(S[k].mass, 0) }))], CAP_PICK,
      (v) => { CAP_PICK = v; drawPath(); }, "Сценарий капитала")), path));
  root.append(section("", bridgeCard(d), ratioBridgeCard(d)));
  root.append(section("Дивиденды", dividendsCard(d), dividendCard(d)));
  root.append(section("", dividendHistoryCard(d)));
  root.append(section("", annualCard(d)));
  return root;
}

function capitalKpis(d) {
  const C = obj(d.capital);
  const a = obj(C.anchor);
  const o = obj(C.observed);
  const req = reqWords(d);
  if (!isNum(a.n20)) return card({ title: "Капитал на отчётную дату" }, missing("капитал"));
  return card({ title: `Капитал на ${fmt.date(a.as_of)}`, sub: `база — ${basisName(a.basis)}; ${ruText(a.src || "")}` },
    el("div", { class: "kpis" },
      kpi(fmt.pct(a.n20, 2), `${capTitle(d, "n20")}${a.n20_pre_dividend ? " до вычета объявленного дивиденда" : ""}`),
      kpi(fmt.pct(a.n20_post_dividend, 2), "после вычета объявленного дивиденда"),
      kpi(fmt.pct(a.req20_now, 2), `${req.name} — требование года`),
      kpi(fmt.pp(a.n20_headroom, 2), `запас до ${req.to}`),
      isNum(req.floor) ? kpi(fmt.pct(req.floor, 2), "регуляторный пол года (ожидание по сценариям)") : null,
      kpi(fmt.pct(obj(a.n11_bank).value, 2), `${capTitle(d, "n11")} на ${fmt.date(obj(a.n11_bank).as_of)}`),
      kpi(fmt.bn(a.rwa, 0), "RWA"),
      kpi(fmt.bn(a.bv_common, 0), "капитал обыкновенных акционеров"),
      kpi(fmt.rub(obj(obj(d.market).multiples).bv_per_share, 2), "капитал на акцию (дата оценки)"),
      kpi(`${fmt.num(a.ded20, 0)} / ${fmt.num(a.ded11, 0)}`, "вычеты из капитала в нормативы, млрд ₽"),
      kpi(`${fmt.num(a.at1, 0)} / ${fmt.num(a.t2, 0)}`, "AT1 / T2, млрд ₽")),
    isNum(o.n1_1) ? el("p", { class: "note-box", style: "margin-top:14px" },
      el("strong", {}, `${capTitle(d, "n11_observed")} на ${fmt.date(o.as_of)}: ${fmt.pct(o.n1_1, 2)}; ${capTitle(d, "n1_0")} — ${fmt.pct(o.n1_0, 2)}, ${capTitle(d, "n1_2")} — ${fmt.pct(o.n1_2, 2)}. `),
      `Наблюдение по месячной форме (${ruText(o.source || "")}); в оценку не входит.`) : null);
}

// Путь нормативов по годам против требования, пола и порога политики; первые кварталы — точками.
function ratioPathCard(d, pick) {
  const C = obj(d.capital);
  const years = list(C.years);
  const src = pick === "mix" ? obj(C.mix) : obj(obj(C.by_scenario)[pick]);
  const sc = pick === "mix" ? obj(C.mix) : obj(obj(C.scenarios)[pick]);
  if (!years.length || !list(src.n20).length) return card({ title: "Путь нормативов" }, missing("путь нормативов"));
  const at = (arr) => years.map((y, i) => [y, list(arr)[i]]);
  const quarters = list(obj(d.paths).quarters);
  const series = [
    { name: capTitle(d, "n20"), color: "var(--model)", width: 2.2, dots: true, r: 3.5, points: at(src.n20),
      area: list(src.n20_p10).length ? years.map((y, i) => [y, src.n20_p10[i], src.n20_p90[i]]) : null },
    { name: capTitle(d, "n11"), color: "var(--third)", dots: true, r: 3, points: at(src.n11) },
    { name: `требование ${capTitle(d, "n20")}`, color: "var(--ink)", width: 1.5, points: at(sc.req20) },
    { name: `пол ${capTitle(d, "n20")}`, color: "var(--neg)", width: 1.5, dashed: true, points: at(sc.floor20), key: "key-line key-neg2" },
    { name: `пол ${capTitle(d, "n11")}`, color: "var(--neg)", width: 1.5, dash: "1 4", points: at(sc.floor11), key: "key-line key-neg3" },
  ];
  series[0].key = "key-line key-model";
  series[1].key = "key-line key-third";
  series[2].key = "key-line key-ink";
  if (pick === "mix" && quarters.length) {
    series.push({ name: `${capTitle(d, "n20")}, кварталы`, color: "var(--model)", line: false, dots: true, hollow: true, r: 3.5,
      points: quarters.map((q) => [quarterX(q.period), q.n20]).filter((p) => isNum(p[0])) });
  }
  const xmin = Math.min(years[0], ...quarters.map((q) => quarterX(q.period)).filter(isNum));
  const drawn = series.filter((s) => s.points.some((p) => isNum(p[1])));
  const plot = linesChart(drawn, { height: 280, xMin: xmin, xMax: years[years.length - 1], xTicks: years,
    xFmt: (y) => `’${String(y).slice(2)}`, yPct: true, tipFmt: (v) => fmt.pct(v, 2),
    hrefs: isNum(C.policy_threshold) ? [{ value: C.policy_threshold, text: `порог политики ${fmt.pct(C.policy_threshold, 1)}`, color: "var(--market)", dash: "5 4", below: true }] : [],
    label: "Путь нормативов против требования и пола" });
  const fig = withTable(plot, () => dataTable([
    Tc("Год", (r) => String(r[0]), "name"),
    Nc(capTitle(d, "n20"), (r) => fmt.pct(list(src.n20)[r[1]], 2)),
    list(src.n20_p10).length ? Nc("P10–P90", (r) => `${fmt.num(src.n20_p10[r[1]] * 100, 1)}–${fmt.pct(src.n20_p90[r[1]], 1)}`) : null,
    Nc(capTitle(d, "n11"), (r) => fmt.pct(list(src.n11)[r[1]], 2)),
    Nc("Требование", (r) => fmt.pct(list(sc.req20)[r[1]], 2)),
    Nc("Пол", (r) => fmt.pct(list(sc.floor20)[r[1]], 2)),
  ], years.map((y, i) => [y, i])));
  const gap = obj(C.capital_gap);
  const gate = list(obj(d.checks).gates).find((g) => g.name === "capital_gap");
  return card({ title: `Путь нормативов: ${pick === "mix" ? "ожидание смеси заголовка" : scenarioName(d, pick)}`, tools: fig.button,
    sub: `норматив на конец года после дивидендов; полоса — P10–P90 клеток сценария под весами «${layerTitle(d, "analytical")}»; полые точки — первые кварталы смеси` },
  legend([...drawn.filter((s) => s.key).map((s) => [s.key, s.name]), isNum(C.policy_threshold) && ["key-line key-market2", "порог дивидендной политики"]]),
  fig.box,
  foot(`Капитальный разрыв: масса ${fmt.pct(gap.mass, 1)} (${fmt.num(gap.cells)} ${plural(gap.cells || 0, CELL_WORDS)}`
    + (list(gap.cell_list).length ? `: ${list(gap.cell_list).map((c) => cellName(d, c)).join("; ")}` : "") + ")"
    + (gap.first_period ? `, первый квартал — ${periodLabel(gap.first_period)}` : "") + ". ",
  gate && gate.fired && gate.explanation ? detailsBlock(`Гейт «${gate.title}»`, ruText(gate.explanation)) : null));
}

// Мост капитала смеси заголовка: капитал → PV доходности → V0 → дисконт → дивиденд до отсечки → цена.
function bridgeCard(d) {
  const mix = obj(obj(d.layers).headline_mix);
  const rows = list(mix.waterfall);
  if (!rows.length) return card({ title: "Мост капитала", span: 7 }, missing("мост капитала"));
  const steps = [];
  let run = 0;
  for (const r of rows) {
    if (r.total || r.key === "bv_v") { steps.push({ title: r.title, from: 0, to: r.amount, total: true, text: `${fmt.num(r.amount, 0)} · ${fmt.rub(r.per_share, 1)}` }); run = r.amount; continue; }
    steps.push({ title: r.title, from: run, to: run + r.amount, text: `${fmt.signed(r.amount, 0)} · ${fmt.signedRub(r.per_share, 1)}` });
    run += r.amount;
  }
  const br = list(obj(d.fair_value.bridge).rows);
  return card({ title: "Мост капитала: от капитала к цене", span: 7, sub: `млрд ₽ · ₽ на акцию; смесь заголовка при λ = ${fmt.num(mix.lambda, 2)}; цена ${fmt.rub(mix.price, 2)} = точка` },
    waterfall(steps, true),
    br.length ? el("div", { style: "margin-top:14px" }, dataTable([
      Tc("Дивиденд реестра", (r) => `за ${r.year}: ${fmt.rub(r.dps, 2)}`, "name"),
      Nc("Сумма, млрд ₽", (r) => fmt.num(r.amount, 1)),
      Nc("Вычет из капитала", (r) => fmt.date(r.deducted_on)),
      Nc("Последний день покупки", (r) => fmt.date(r.last_buy_date)),
      Nc("Экс-дата", (r) => fmt.date(r.ex_date)),
      Nc("В мосте", (r) => (r.sign > 0 ? "+" : r.sign < 0 ? MINUS : "0")),
    ], br, { cls: "compact" })) : null,
    foot("Мост не несёт дисконта за управление: объявленная сумма — обязательство, а не будущая стоимость."));
}

// Мост норматива от якоря до конца года: вычет дивиденда, прибыль, OCI, рост RWA, вычеты.
function ratioBridgeCard(d) {
  const b = obj(obj(d.capital).bridge);
  const rows = list(b.rows);
  if (!rows.length) return card({ title: "Мост норматива", span: 5 }, missing("мост норматива"));
  const steps = [{ title: `${periodLabel(b.from)}`, from: 0, to: b.start, total: true, text: fmt.pct(b.start, 2) }];
  let run = b.start;
  for (const r of rows) {
    steps.push({ title: r.title, from: run, to: run + r.pp, text: fmt.pp(r.pp, 2), hint: isNum(r.amount) ? fmt.signedBn(r.amount, 0) : null });
    run += r.pp;
  }
  steps.push({ title: periodLabel(b.to), from: 0, to: b.end, total: true, text: fmt.pct(b.end, 2) });
  return card({ title: `Мост ${capTitle(d, "n20")}`, span: 5, sub: "расчёт модели на смеси заголовка, включая уровень на дату якоря; п.п. норматива, в подписи — млрд ₽ капитала или RWA" }, waterfall(steps));
}

function dividendsCard(d) {
  const dv = obj(d.dividends);
  const pol = obj(dv.policy);
  const ladder = list(dv.ladder);
  const check = list(dv.formula_check);
  if (!ladder.length && !pol.name) return card({ title: "Дивидендная политика", span: 6 }, missing("дивидендная политика"));
  return card({ title: "Дивидендная политика", span: 6,
    sub: `${ruText(pol.name || "")}; ${!pol.valid_until ? ruText(pol.valid_until_note || "срок действия в документе не задан")
      : flag(d, "policy_expired") ? `срок истёк ${fmt.date(pol.valid_until)}: до новой политики действует прежняя` : `действует по ${fmt.date(pol.valid_until)}`}` },
    pol.text ? el("p", { class: "prose" }, sentence(ruText(pol.text))) : null,
    el("ol", { class: "ladder", style: "margin-top:12px" }, ladder.map((r) => el("li", { class: cls(r.current && "is-current") },
      el("span", { class: "ladder-rung" }, r.current ? "сейчас" : isNum(r.payout) ? fmt.pct(r.payout, 0) : "остаток"),
      el("span", {}, el("strong", {}, ruText(r.title)), ` — ${ruText(r.condition)}`)))),
    check.length ? el("div", { style: "margin-top:14px" }, tileLabel("Проверка формулы на истории"), dataTable([
      Tc("Год", (r) => String(r.year), "name"),
      Nc("ЧП, млрд ₽", (r) => fmt.num(r.ni_shareholders, 1)),
      Nc("База", (r) => fmt.num(r.base, 1)),
      Nc("Пул", (r) => fmt.num(r.pool, 1)),
      Nc("DPS точно", (r) => fmt.num(r.dps_exact, 4)),
      Nc("Округл.", (r) => fmt.num(r.dps_rounded, 2)),
      Nc("Объявлен", (r) => fmt.num(r.dps_declared, 2)),
      Tc("", (r) => (r.ok ? badge("до копейки", "good") : badge("расхождение", "bad"))),
    ], check, { cls: "compact" })) : null,
    foot(pol.valid_until && pol.valid_until_note ? `${sentence(upperFirst(ruText(pol.valid_until_note)))} ` : "",
      `База — ${codeText(pol.base || "")}; делитель — ${codeText(pol.divisor || "")}; округление — ${codeText(obj(check[0]).rounding || "")}; нехватка капитала — ${codeText(pol.shortfall_rule || "")}. `
      + (obj(pol.excess).from_profit_year ? `Выплата избытка с прибыли ${pol.excess.from_profit_year} г.: ε = ${fmt.num(pol.excess.epsilon, 2)}`
        + (isNum(pol.excess.ramp_years) ? `, ввод линейно за ${yearsText(pol.excess.ramp_years)}. ` : ". ") : "")
      + (obj(pol.crisis).skip_in_shock_year ? `В год кризиса выплата переносится; догоняющая выплата — ${codeText(pol.crisis.catch_up)}.` : "")));
}

function dividendCard(d) {
  const dv = obj(d.dividends);
  const next = obj(dv.next_expected);
  const reg = list(dv.register).slice().sort((a, b) => String(b.ex_date || b.record_date).localeCompare(String(a.ex_date || a.record_date)));
  if (!isNum(next.dps) && !reg.length) return card({ title: "Следующая выплата", span: 6 }, missing("следующая выплата"));
  const cond = obj(next.condition);
  const buy = next.last_buy_date ? daysBetween(d.meta.valuation_date, next.last_buy_date) : null;
  const model = next.status === "model";
  return card({ title: `Следующая выплата: за ${next.year}`, span: 6, sub: model ? "объявления нет — DPS по политике на прибыли модели" : `статус — ${(DIV_STATUS[next.status] || [next.status])[0]}` },
    el("div", { class: "kpis" },
      kpi(fmt.rub(next.dps, 2), nextDpsLabel(next)),
      model ? kpi(fmt.rub(next.dps_mean, 2), `в среднем по клеткам с нулями отмены; P10–P90 ${fmt.num(next.dps_p10, 2)}–${fmt.num(next.dps_p90, 2)}${THIN}₽`)
        : kpi(fmt.rub(next.dps_policy, 2), "DPS политики на прибыли модели"),
      kpi(fmt.pct(next.p_cancel, 0), `риск отмены: масса клеток «${layerTitle(d, "analytical")}», где выплата отменена`),
      kpi(nextRecordText(next), next.record_date ? "отсечка" : "ожидаемая отсечка"),
      next.last_buy_date ? kpi(fmt.date(next.last_buy_date), `последний день покупки${isNum(buy) ? `, через ${fmt.days(buy)}` : ""}`) : null,
      kpi(fmt.date(next.pay_date_est), "ожидаемая выплата"),
      ...tickers(d).map((t) => kpi(fmt.pct(obj(next.yield)[t], 1), `доходность к ${t}`))),
    el("p", { class: "note-box", style: "margin-top:12px" }, `Условие капитала: ${metricTitle(d, cond.metric)} после выплаты не ниже ${fmt.pct(cond.threshold, 1)}; ожидание — ${fmt.pct(cond.n20_expected, 2)}; `
      + `капитал урезал выплату в ${fmt.pct(cond.p_limited, 1)} массы клеток.`, next.record_date_note && !next.record_date ? ` Отсечка: ${ruText(next.record_date_note)}.` : ""),
    reg.length ? el("div", { style: "margin-top:14px" }, dataTable([
      Tc("Год прибыли", (r) => el("span", {}, String(r.year), el("span", { class: "cell-badge" }, divStatus(r))), "name"),
      Nc("DPS", (r) => fmt.rub(r.dps, 2)),
      Nc("Пул, млрд ₽", (r) => fmt.num(r.amount, 1)),
      Nc("Последний день покупки", (r) => fmt.date(r.last_buy_date)),
      Nc("Отсечка", registerCutoff),
      Nc("Выплата", (r) => fmt.date(r.pay_date)),
    ], reg, { cls: "compact", detail: (r) => el("span", { class: "muted" }, "Источники: ", registerSources(r)) }), foot("Объявленной запись становится при двух источниках: T-Invest и новость о решении собрания (запасной второй — ручная запись оператора); рекомендация совета — только плашка.")) : null);
}

// Источники строк факта словами выпуска: годы с одним источником — одной записью («2013–2020, 2022–2025»).
function sourcesByYear(rows) {
  const groups = new Map();
  for (const r of rows) if (r.src) groups.set(r.src, [...(groups.get(r.src) || []), r.year]);
  const spans = (years) => {
    const out = [];
    for (const y of years) { const last = out[out.length - 1]; if (last && y === last[1] + 1) last[1] = y; else out.push([y, y]); }
    return out.map(([a, b]) => (a === b ? String(a) : `${a}–${b}`)).join(", ");
  };
  return [...groups].map(([src, years]) => [spans(years), srcText(src)]);
}

// DPS по году прибыли: факт и модель (ожидание, P10–P90, доля выплаты, урезание).
function dividendHistoryCard(d) {
  const dv = obj(d.dividends);
  const hist = list(dv.history).filter((r) => isNum(r.dps));
  const model = list(dv.model).filter((r) => isNum(r.dps));
  if (!hist.length && !model.length) return card({ title: "Дивиденды по годам" }, missing("история дивидендов"));
  const items = [
    ...hist.map((r) => ({ key: r.year, label: `’${String(r.year).slice(2)}`, value: r.dps, color: "var(--market)", tipTitle: `${r.year}: факт`,
      tipRows: [["доля выплаты", fmt.pct(r.payout_ratio, 0)], ["отсечка", fmt.date(r.record_date)]] })),
    ...model.filter((r) => !hist.some((h) => h.year === r.year)).map((r) => ({ key: r.year, label: `’${String(r.year).slice(2)}`, value: r.dps, color: "var(--model)",
      tipTitle: `${r.year}: модель`, tipRows: [["по политике", fmt.rub(r.dps_policy, 2)], ["P10–P90", `${fmt.num(r.dps_p10, 1)}–${fmt.num(r.dps_p90, 1)}`], ["урезано капиталом", fmt.pct(r.p_cut, 0)], ["без выплаты", fmt.pct(r.p_zero, 0)]] })),
  ];
  const plot = columnsChart(items, { height: 220, valueName: "₽ на акцию", fmt: (v) => fmt.rub(v, 2), short: (v) => fmt.num(v, v && Math.abs(v) < 1 ? 2 : 0), label: "Дивиденд на акцию по году прибыли" });
  const fig = withTable(plot, () => el("div", { class: "split" },
    dataTable([
      Tc("Год прибыли", (r) => String(r.year), "name"),
      Nc("DPS", (r) => fmt.rub(r.dps, 2)),
      Nc("Пул, млрд ₽", (r) => fmt.num(r.pool, 1)),
      Nc("Доля", (r) => fmt.pct(r.payout_ratio, 0)),
    ], hist, { cls: "compact", caption: "Факт" }),
    dataTable([
      Tc("Год прибыли", (r) => `${r.year} → ${r.pay_year}`, "name"),
      Nc("DPS", (r) => fmt.rub(r.dps, 2)),
      Nc("По политике", (r) => fmt.rub(r.dps_policy, 2)),
      Nc("P10–P90", (r) => `${fmt.num(r.dps_p10, 1)}–${fmt.num(r.dps_p90, 1)}`),
      Nc("Урезано капиталом", (r) => fmt.pct(r.p_cut, 0)),
      Nc("Без выплаты", (r) => fmt.pct(r.p_zero, 0)),
    ], model, { cls: "compact", caption: "Модель: ожидание смеси заголовка по клеткам; доли клеток с урезанием капиталом и без выплаты" })));
  const sources = sourcesByYear(hist);
  return card({ title: "Дивиденды по году прибыли", tools: fig.button,
    sub: `факт — выплаты по решениям собраний; модель — ожидание смеси заголовка; на ${fmt.num(obj(obj(d.meta).shares).issued_mln, 1)} млн размещённых акций обеих категорий` },
  legend([["key-market", "факт"], ["key-model", "модель"]]), fig.box,
  sources.length === 1 ? foot(sentence(`Источник факта: ${sources[0][1]}`))
    : sources.length <= 3 ? (sources.length ? foot(sentence(`Источники факта: ${sources.map(([years, src]) => `${years} — ${src}`).join("; ")}`)) : null)
      : el("div", { class: "card-foot" }, detailsBlock(`Источники факта по годам · ${sources.length}`, el("ul", { class: "list stacked" },
        sources.map(([years, src]) => el("li", {}, el("span", { class: "t" }, years), el("span", { class: "v" }, src)))))));
}

function annualCard(d) {
  const p = obj(d.paths);
  const A = list(p.annual);
  if (!A.length) return card({ title: "По годам" }, missing("путь по годам"));
  const u = modelUnit(d);
  const year = (r) => el("span", {}, String(r.year), r.fact_quarters ? hint(`${fmt.num(r.fact_quarters)}${NBSP}кв. факт + модель`) : null);
  const mini = (title, key, f, color) => el("div", { class: "mini" }, tileLabel(title),
    linesChart([{ name: title, color, dots: true, r: 3, points: A.map((r) => [r.year, r[key]]) }],
      { xType: "band", categories: A.map((r) => r.year), height: 150, left: 44, yFmt: f, tipFmt: f, xFmt: (y) => `’${String(y).slice(2)}` }));
  const t = obj(p.terminal);
  const bn = (v) => fmt.num(v, 0);
  return card({ title: "По годам: смесь заголовка", sub: `млрд ₽ и доли; год якоря — факт отчётных ${u.pgen} + модель; упр. ЧПМ, CoR и CIR — мостом сборки` },
    el("div", { class: "minis" },
      mini("ЧП акционерам, млрд ₽", "ni_sh", (v) => fmt.num(v, 0), "var(--model)"),
      mini("ROE", "roe", (v) => fmt.pct(v, 1), "var(--third)"),
      mini(capTitle(d, "n20"), "n20_end", (v) => fmt.pct(v, 1), "var(--market)")),
    el("div", { style: "margin-top:16px" }, dataTable([
      Tc("Год", year, "name"),
      Nc("ЧПД", (r) => bn(r.nii)),
      Nc("ЧПМ упр.", (r) => fmt.pct(r.nim_mgmt, 2)),
      Nc("Комиссии", (r) => bn(r.fees)),
      Nc("Расходы", (r) => bn(r.opex)),
      Nc("CIR упр.", (r) => fmt.pct(r.cir_mgmt, 1)),
      Nc("Резервы", (r) => bn(r.llp)),
      Nc("CoR упр.", (r) => fmt.pct(r.cor_mgmt, 2)),
      A.some((r) => isNum(r.fvc)) ? Nc(el("span", { tip: "Кредитная переоценка кредитов по справедливой стоимости сверх опоры; расход — со знаком плюс, как резервы" }, "FVC"), (r) => bn(r.fvc)) : null,
      A.some((r) => r.one_off) ? Nc("Разовые", (r) => fmt.signed(r.one_off, 0)) : null,
      Nc(`Непро${SHY}фильный`, (r) => bn(r.noncore)),
      Nc("ЧП акционерам", (r) => el("strong", {}, bn(r.ni_sh))),
      Nc("ROE", (r) => fmt.pct(r.roe, 1)),
      Nc("Капитал", (r) => bn(r.bv_end)),
      Nc("RWA", (r) => bn(r.rwa_end)),
      Nc(capTitle(d, "n20"), (r) => fmt.pct(r.n20_end, 2)),
      Nc(capTitle(d, "n11"), (r) => fmt.pct(r.n11_end, 2)),
      Nc("DPS", (r) => fmt.num(r.dps, 2)),
      Nc("Доля выплаты", (r) => fmt.pct(r.payout, 0)),
    ], A, { cls: "dense", rowClass: (r) => (r.fact_quarters ? "is-muted" : null) })),
    isNum(t.roe_t) ? foot(`Терминал: ROE'_T ${fmt.pct(t.roe_t, 1)}, k_T ${fmt.pct(t.k_t, 1)}, g_T ${fmt.pct(t.g_t, 1)}, доля выплаты ${fmt.pct(t.payout_t, 0)}, избыток капитала ${fmt.bn(t.x_t, 0)}; `
      + `горизонт ${periodLabel(obj(d.meta).first_period)} — ${periodLabel(list(obj(d.meta).horizon)[1])}.`) : null);
}

/* ── экран «Допущения» ── */

let ALL_JUDGEMENTS = false;

function screenBook(d) {
  const root = el("div", { class: "screen" },
    screenHead("Допущения", `Книга допущений ${obj(d.meta).book_version || ""}`,
      "Каждое суждение — с диапазоном, ценой ошибки и источником. Ниже — проверки с письменными объяснениями, свежесть живых входов, кривая книги против рынка, история оценки по выпускам и отчётная история."));
  root.append(section("", bookMetaCard(d)));
  root.append(section("", judgementsCard(d)));
  root.append(section("", gatesCard(d)));
  root.append(section("", freshnessCard(d), curveCard(d)));
  root.append(section("", valuationHistoryCard(d)));
  const control = controlCard(d);
  if (control) root.append(section("", control));
  root.append(section("", historyCard(d)));
  return root;
}

function bookMetaCard(d) {
  const m = obj(d.meta);
  const live = obj(d.live);
  const src = obj(obj(d.worlds).source);
  const ov = obj(obj(d.worlds).overlay);
  const b = obj(d.book);
  return card({ title: "Выпуск и книга" },
    el("div", { class: "kpis" },
      kpi(`${m.book_version || "—"}`, `книга допущений (${m.book_tag || "—"}) от ${fmt.date(m.book_date)}`),
      kpi(fmt.date(m.valuation_date), "дата оценки"),
      kpi(fmt.date(m.facts_date), `отчётные факты (${periodLabel(m.anchor_period)})`),
      kpi(fmt.date(m.curve_as_of), "кривые миров"),
      kpi(fmt.days(live.book_age_days), "возраст книги"),
      kpi(`${periodShort(list(m.horizon)[0])}–${periodShort(list(m.horizon)[1])}`, `горизонт, шаг — ${modelUnit(d).one}`),
      kpi(fmt.date(mskDay(m.generated_at)), `выпуск собран в ${fmt.time(m.generated_at)}`),
      kpi(fmt.date(mskDay(m.published_at)), `опубликован в ${fmt.time(m.published_at)}`),
      kpi(String(m.engine_commit || "—").slice(0, 7), "коммит ядра"),
      kpi(`${fmt.num((m.bytes || 0) / 1000, 0)} КБ`, "размер выпуска")),
    foot(`Контракт ${d.schema}; хэш содержания ${String(m.payload_sha256 || "").slice(0, 16)}…${m.fast ? "; быстрая сборка." : ""} `
      + `Миры: ${worldsRecord(d)} от ${fmt.date(src.record_asof)}, sha256 ${String(src.sha256 || "").slice(0, 12)}…; банковская надстройка — sha256 ${String(ov.sha256 || "").slice(0, 12)}…`
      + (obj(b.changes_last).summary ? ` Изменения с ${b.changes_last.from_version}: ${ruText(b.changes_last.summary)}.` : "")));
}

function judgementsCard(d) {
  const rows = list(obj(d.judgements).rows);
  if (!rows.length) return card({ title: "Суждения книги" }, missing("суждения"));
  const center = d.fair_value.central;
  const prices = rows.flatMap((r) => [r.price_low, r.price_high]).filter(isNum);
  const step = niceStep(Math.max(center, ...prices), 6);
  const dom = [Math.max(0, Math.floor((Math.min(center, ...prices) * 0.94) / step) * step), Math.ceil((Math.max(center, ...prices) * 1.04) / step) * step];
  const body = el("div");
  const draw = () => {
    const shown = ALL_JUDGEMENTS ? rows : rows.slice(0, 12);
    fill(body, dataTable([
      Tc("Суждение", (r) => r.name, "name"),
      Tc("В книге", (r) => formatByUnit(r.book, r.unit, d), "txt"),
      Nc("Диапазон", (r) => rangeText(d, r), "", isDict),
      Tc("Точка на краях, ₽", (r) => (isNum(r.price_low) ? el("div", { class: "tn-cell" },
        barTrack([
          { from: Math.min(r.price_low, r.price_high), to: Math.min(center, Math.max(r.price_low, r.price_high)), cls: "is-down" },
          { from: Math.max(center, Math.min(r.price_low, r.price_high)), to: Math.max(r.price_low, r.price_high), cls: "is-up" },
        ].filter((s) => s.to > s.from), dom, { center }),
        el("span", { class: "tn-nums" }, `${fmt.num(r.price_low)} / ${fmt.num(r.price_high)}`)) : "—")),
      Nc("Цена ошибки", (r) => el("strong", {}, fmt.rub(r.swing))),
      Nc("Сдвиг положения, ₽", (r) => fmt.signed(r.mean_shift, 1)),
      Nc("Вклад в полосу", (r) => (r.in_band === false ? el("span", { class: "muted", tip: `ось стоит на значении книги и не разыгрывается; ${offBandNote(d)}` }, "вне полосы")
        : r.status === "not_computed" ? "не считалось" : shareText(r.share))),
    ], shown, { detail: (r) => (r.source ? detailsBlock("Источник", srcText(r.source)) : null) }),
    rows.length > 12 ? el("button", { class: "view-toggle", type: "button", style: "margin-top:12px",
      on: { click: () => { ALL_JUDGEMENTS = !ALL_JUDGEMENTS; draw(); } } }, ALL_JUDGEMENTS ? "Показать главные 12" : `Показать все ${rows.length}`) : null);
  };
  draw();
  return card({ title: "Суждения книги по цене ошибки",
    sub: `Точка на краях диапазона каждого суждения (по одному, прочие — книга); вертикаль — точка ${fmt.rub(center, 1)}. Цена ошибки — размах точки на диапазоне; `
      + "сдвиг положения — на сколько асимметрия диапазона уводит среднее прогонов от точки (первый порядок); вклад меньше 1 % не различим."
      + (offBand(d).length ? ` Вне полосы — ${fmt.num(offBand(d).length)}: ${offBandNote(d)}.` : "") },
  legend([["key-neg", "ниже точки"], ["key-model", "выше точки"]]), body);
}

// Метка клетки «W/r/s» — словами выпуска.
function cellName(d, label) {
  const [w, r, s] = String(label).split("/");
  return [w, regimeName(d, r), scenarioName(d, s)].filter(Boolean).join(" · ");
}

const CELL_WORDS = ["клетка", "клетки", "клеток"];
const GATE_STATUS = { quiet: ["тихо", "out"], explained: ["объяснён", "good"], unexplained: ["без объяснения", "bad"], expired: ["объяснение истекло", "bad"], mass_off: ["масса вне ожидания", "warn"] };

function gatesCard(d) {
  const c = obj(d.checks);
  const gates = list(c.gates);
  const fired = gates.filter((g) => g.fired);
  const quiet = gates.filter((g) => !g.fired);
  const inv = list(c.invariants);
  const flags = list(c.flags);
  const corridor = (g) => { const k = obj(g.corridor); return k.text ? wordsText(d, k.text) : ""; };
  const expected = (g) => (Array.isArray(g.expected_mass) ? `${fmt.num(g.expected_mass[0] * 100, 1)}–${fmt.pct(g.expected_mass[1], 1)}` : fmt.pct(g.expected_mass, 1));
  // Счётчик: число и подпись, согласованная с ним («1 поднятый флаг», «4 сработавших гейта из 19»).
  const count = (n, adj, noun, tail = "") => kpi(fmt.num(n), `${plural(n, adj)} ${plural(n, noun)}${tail}`);
  const raised = flags.filter((f) => f.raised).length;
  return card({ title: "Проверки",
    sub: "Инварианты — тождества, их нарушение блокирует выпуск; гейт правдоподобия срабатывает на части клеток и требует письменного объяснения с ожидаемой массой и сроком; флаги — совещательные." },
  el("div", { class: "kpis" },
    count(c.invariants_broken || 0, ["нарушенный", "нарушенных", "нарушенных"], ["инвариант", "инварианта", "инвариантов"], ` из ${fmt.num(inv.length)}`),
    count(fired.length, ["сработавший", "сработавших", "сработавших"], ["гейт", "гейта", "гейтов"], ` из ${fmt.num(gates.length)}`),
    count(raised, ["поднятый", "поднятых", "поднятых"], ["флаг", "флага", "флагов"])),
  fired.length ? el("div", { class: "gates" }, fired.map((g) => { const [t, k] = GATE_STATUS[g.status] || [g.status, null]; return el("article", { class: "gate" },
    el("div", { class: "gate-head" }, el("strong", {}, g.title || g.name),
      el("span", { class: "gate-mass" }, `${g.cells > 0 ? `${fmt.num(g.cells)} ${plural(g.cells, CELL_WORDS)} · ` : ""}${fmt.pct(g.mass, 1)} массы${g.expected_mass !== null && g.expected_mass !== undefined ? ` · ожидалось ${expected(g)}` : ""}`),
      badge(t, k), g.expiring ? badge("срок истекает", "warn") : null),
    g.valid_until ? el("p", { class: "muted small", style: "margin-top:4px" }, `Объяснение действует по ${fmt.date(g.valid_until)} включительно. Коридор: ${corridor(g)}.`) : null,
    g.message ? el("p", { class: "ink-2", style: "margin-top:6px" }, sentence(upperFirst(wordsText(d, g.message)))) : null,
    g.explanation ? el("p", { class: "ink-2", style: "margin-top:6px" }, ruText(g.explanation)) : null,
    list(g.cell_list).length ? detailsBlock(`Клетки${g.cells > list(g.cell_list).length ? ` (первые ${list(g.cell_list).length} из ${fmt.num(g.cells)})` : ""}`,
      list(g.cell_list).map((c) => cellName(d, c)).join("; ") + ".") : null); })) : empty("Ни один гейт не сработал."),
  el("div", { class: "card-foot stack" },
    quiet.length ? detailsBlock(`Коридоры несработавших гейтов · ${quiet.length}`, el("ul", { class: "list stacked" },
      quiet.map((g) => el("li", {}, el("span", { class: "t" }, g.title || g.name), el("span", { class: "v" }, corridor(g) || "—"),
        g.message ? el("p", { class: "muted small", style: "margin-top:2px" }, sentence(upperFirst(wordsText(d, g.message)))) : null)))) : null,
    inv.length ? detailsBlock(`Инварианты · ${inv.length}`, el("ul", { class: "list" }, inv.map((i) => el("li", {},
      el("span", { class: "t" }, i.title || i.name, i.detail ? el("span", { class: "muted" }, ruText(i.detail)) : null), el("span", { class: "v" }, i.ok ? badge("в допуске", "good") : badge("нарушен", "bad")))))) : null,
    flags.length ? detailsBlock(`Флаги · ${flags.length}`, el("ul", { class: "list" }, flags.map((f) => el("li", {},
      el("span", { class: "t" }, f.title || f.name, f.detail ? el("span", { class: "muted" }, wordsText(d, f.detail)) : null), el("span", { class: "v" }, f.raised ? badge("поднят", "warn") : badge("нет", "out")))))) : null));
}

const INPUT_STATUS = { ok: ["в норме", "good"], stale: ["устарел", "warn"], fallback: ["запасное значение", "warn"] };

function freshnessCard(d) {
  const live = obj(d.live);
  const rows = list(obj(d.inputs).rows);
  const prices = obj(live.prices);
  return card({ title: "Свежесть входов", span: 6, sub: `дата оценки ${fmt.date(d.meta.valuation_date)} — от неё, а не от времени сборки, меряется свежесть; живые входы ${live.applied ? "применены" : "не применены (книжные)"}` },
    rows.length ? dataTable([
      Tc("Вход", (r) => r.name, "name"),
      Nc("Значение", (r) => (r.unit === "version" ? String(r.value) : formatByUnit(r.value, r.unit, d))),
      Nc("На дату", (r) => fmt.date(r.as_of)),
      Tc("", (r) => { const [t, k] = INPUT_STATUS[r.status] || [r.status, null]; return badge(t, k); }),
    ], rows, { cls: "compact", detail: (r) => (r.source ? el("span", { class: "muted" }, srcText(r.source)) : null) }) : empty("Строк входов в выпуске нет."),
    el("ul", { class: "list stacked", style: "margin-top:12px" },
      ...tickers(d).map((t) => { const p = obj(prices[t]); return el("li", {}, el("span", { class: "t" }, `Цена ${t}`),
        el("span", { class: "v" }, `${fmt.rub(p.value, 2)} · ${fmt.date(p.date)} ${p.time || ""} · ${p.accepted ? "принята" : `не принята: ${ruText(p.reason || "")}; последняя принятая ${fmt.rub(obj(p.last_accepted).value, 2)}`}`)); }),
      el("li", {}, el("span", { class: "t" }, "Ключевая ставка"), el("span", { class: "v" }, `${fmt.pct(obj(live.key_rate).value, 2)} с ${fmt.date(obj(live.key_rate).since)}`)),
      el("li", {}, el("span", { class: "t" }, "Реестр дивидендов"), el("span", { class: "v" }, `${fmt.num(obj(live.register).entries)} ${plural(obj(live.register).entries || 0, ["запись", "записи", "записей"])} на ${fmt.date(obj(live.register).as_of)}; ${ruText(obj(live.register).note || "")}`))),
    list(live.degraded).length ? el("div", { class: "note-box", style: "margin-top:12px" }, el("strong", {}, "Деградация входов:"),
      el("ul", { class: "reasons" }, list(live.degraded).map((n) => el("li", {}, reasonText(n))))) : null);
}

// Ключ мира слоя «рыночные ставки как есть» — по его весам в выпуске.
function neutralWorld(d) {
  const w = obj(obj(obj(d.layers).macro_neutral).world_weights);
  const keys = Object.keys(w).filter((k) => isNum(w[k]));
  return keys.length ? keys.sort((a, b) => w[b] - w[a])[0] : null;
}

function curveCard(d) {
  const curve = obj(obj(d.live).curve);
  const nk = neutralWorld(d);
  const book = obj(obj(obj(obj(d.worlds).rows)[nk]).zero_curve);
  const tenor = (k) => (k === "LT" ? 25 : Number(k));
  const series = [];
  if (Object.keys(book).length) series.push({ name: `книга: мир ${worldName(d, nk)}`, color: "var(--market)", dots: true, points: Object.entries(book).map(([t, v]) => [tenor(t), v]).sort((a, b) => a[0] - b[0]) });
  if (Object.keys(obj(curve.nodes)).length) series.push({ name: `ОФЗ ${fmt.date(curve.as_of)}`, color: "var(--ink)", width: 1.6, dots: true, r: 3, points: Object.entries(curve.nodes).map(([t, v]) => [Number(t), v]).sort((a, b) => a[0] - b[0]) });
  if (!series.length) return card({ title: "Кривая ОФЗ: книга и рынок", span: 6 }, missing("кривая"));
  const plot = linesChart(series, { height: 220, xMin: 0, xMax: 26, xTicks: [1, 3, 5, 10, 25], xFmt: (t) => (t === 25 ? "LT" : `${fmt.num(t)}${NBSP}г.`), yPct: true, tipFmt: (v) => fmt.pct(v, 2), label: "Кривая книги и наблюдаемая кривая ОФЗ" });
  return card({ title: "Кривая ОФЗ: книга и рынок", span: 6,
    sub: `В оценку идут миры книги; живая кривая — диагностика флага «книгу пора обновить».${curve.as_of ? "" : " Наблюдаемая кривая не принята — причина в свежести входов."}` },
  legend(series.map((s, i) => [i === 0 && Object.keys(book).length ? "key-line key-market" : "key-line key-ink", s.name])), plot,
  Object.keys(obj(curve.shift_bp)).length ? el("div", { class: "kpis", style: "margin-top:12px" },
    Object.entries(curve.shift_bp).map(([t, v]) => kpi(fmt.bp(v), `сдвиг узла ${isNum(+t) ? yearsText(+t) : t} к книге`))) : null);
}

// История оценки по выпускам: медиана и полосы против цены; экс-даты и смены книги — вертикали.
function valuationHistoryCard(d) {
  const h = obj(d.valuation_history);
  const dates = list(h.date);
  const head = obj(d.fair_value.headline);
  const main = ticker(d);
  const t = (iso) => { const day = parseDay(iso); return day ? day.getTime() : NaN; };
  const nowDay = mskDay(obj(d.meta).published_at || obj(d.meta).generated_at) || obj(d.meta).valuation_date;
  const rows = dates.map((dt, i) => ({ date: dt, median: list(h.median)[i], p10: list(h.p10)[i], p25: list(h.p25)[i], p75: list(h.p75)[i], p90: list(h.p90)[i],
    point: list(h.point)[i], price: Object.fromEntries(tickers(d).map((tk) => [tk, list(obj(h.price)[tk])[i]])), rollback: list(h.rollbacks).includes(i) }));
  if (isNum(head.median) && !dates.includes(nowDay)) {
    rows.push({ date: nowDay, median: head.median, p10: list(head.band80)[0], p25: list(head.band50)[0], p75: list(head.band50)[1], p90: list(head.band80)[1],
      point: head.point, price: Object.fromEntries(tickers(d).map((tk) => [tk, obj(head.market_by_ticker)[tk]])), current: true });
  }
  if (rows.length < 2) return card({ title: "История оценки по выпускам" }, empty("Прошлых выпусков в выпуске нет: история начнётся со второй публикации."));
  const xs = rows.map((r) => t(r.date));
  const first = Math.min(...xs), last = Math.max(...xs);
  const ex = [...list(obj(d.dividends).history), ...list(obj(d.dividends).register)].filter((r) => isNum(t(r.ex_date)) && t(r.ex_date) >= first && t(r.ex_date) <= last);
  const exUnique = ex.filter((r, i) => ex.findIndex((q) => q.ex_date === r.ex_date) === i);
  const marks = [...exUnique.map((r) => ({ x: t(r.ex_date), text: `дивиденд ${fmt.rub(r.dps, 2)}`, tip: { title: "Экс-дата", rows: [["дата", fmt.date(r.ex_date)], ["DPS", fmt.rub(r.dps, 2)]] } })),
    ...list(h.book_changes).filter((b) => isNum(t(b.date))).map((b) => ({ x: t(b.date), text: `книга ${b.to}`, color: "var(--model)", tip: { title: "Смена книги", rows: [["было", String(b.from)], ["стало", String(b.to)]] } }))];
  const plot = linesChart([
    { name: "медиана", color: "var(--model)", width: 2.2, points: rows.map((r) => [t(r.date), r.median]), area: rows.map((r) => [t(r.date), r.p10, r.p90]), areaFill: "var(--model-wash-1)" },
    { name: "полоса 50 %", color: "var(--model)", line: false, points: [], area: rows.map((r) => [t(r.date), r.p25, r.p75]), areaFill: "var(--model-wash-2)" },
    ...tickers(d).map((tk) => ({ name: tk, color: "var(--market)", width: tk === main ? 1.8 : 1.4, dashed: tk !== main, points: rows.map((r) => [t(r.date), r.price[tk]]) })),
    { name: "откат", color: "var(--neg)", line: false, dots: true, hollow: true, r: 4.5, points: rows.filter((r) => r.rollback).map((r) => [t(r.date), r.median]) },
    { name: "текущий выпуск", color: "var(--model)", line: false, dots: true, r: 5, points: rows.filter((r) => r.current).map((r) => [t(r.date), r.median]) },
  ], { height: 260, xMin: first, xMax: last, xFmt: (ms) => fmt.dateShort(new Date(ms).toISOString().slice(0, 10)), xTicks: ticks(first, last, 5), left: 52,
    yFmt: (v) => fmt.num(v), tipFmt: (v) => fmt.rub(v, 1),
    vlines: marks, label: "История оценки по выпускам" });
  const fig = withTable(plot, () => dataTable([
    Tc("Выпуск", (r) => el("span", {}, fmt.date(r.date), r.current ? hint("текущий") : r.rollback ? hint("откат") : null), "name"),
    Nc("Медиана", (r) => fmt.num(r.median, 1)),
    Nc("P10–P90", (r) => `${fmt.num(r.p10)}–${fmt.num(r.p90)}`),
    Nc("Точка", (r) => fmt.num(r.point, 1)),
    ...tickers(d).map((tk) => (Nc(tk, (r) => fmt.num(r.price[tk], 2)))),
  ], rows.slice().reverse()));
  return card({ title: "История оценки по выпускам", tools: fig.button,
    sub: `медиана и полосы 80 % и 50 % против цены ${main}; после экс-даты оценка честно падает на DPS; ${ruText(h.rule || "")}` },
  legend([["key-b80", "полоса 80 %"], ["key-b50", "полоса 50 %"], ["key-line key-model", "медиана"], ["key-line key-market", main], ...others(d).map((tk) => ["key-line key-market2", tk]),
    exUnique.length && ["key-line key-dash", "экс-дата"], marks.length > exUnique.length && ["key-line key-model2", "смена книги"]]),
  fig.box,
  h.source ? foot(sentence(`Источник: ${srcText(h.source)}`)) : null);
}

function controlCard(d) {
  const cm = obj(d.checks).control_model;
  if (!cm || !list(cm.rows).length) return null;
  // Единица строки: доля и сдвиг доли считаются в п.п. (уровень доли — в %, уровень сдвига — в п.п.), деньги — млрд ₽ и ₽.
  const share = (r) => ["pct", "share", "pp"].includes(r.unit);
  const tail = (r) => (share(r) ? `${NBSP}п.п.` : { bn: `${NBSP}млрд${NBSP}₽`, rub: `${THIN}₽`, price: `${THIN}₽` }[r.unit] || "");
  const inUnit = (r, v) => (share(r) ? v * 100 : v);
  const level = (r, v) => (!isNum(v) ? "—" : r.unit === "pp" ? fmt.num(v * 100, 2) + tail(r) : share(r) ? fmt.pct(v, 2)
    : v !== 0 && Math.abs(v) < 1e-4 ? sci(v) : fmt.num(v, v === 0 ? 0 : ["rub", "price"].includes(r.unit) ? 2 : Math.abs(v) < 10 ? 4 : 1) + tail(r));
  // Знаков абсолютного допуска — до его первой значащей цифры, не меньше принятого у единицы; глубже четырёх — степенью десяти.
  const digits = (r, t) => Math.max(share(r) || ["rub", "price"].includes(r.unit) ? 2 : r.unit === "bn" ? 1 : 0, isNum(t) && t > 0 ? Math.ceil(-Math.log10(inUnit(r, t)) - 1e-9) : 0);
  const tolAbs = (r, t) => (!isNum(t) ? "—" : digits(r, t) > 4 ? sci(inUnit(r, t)) : fmt.num(inUnit(r, t), digits(r, t))) + tail(r);
  // Абсолютная разность — со знаками своего допуска; мельче этих знаков — ноль (шум счёта), пока строка в допуске.
  const diffAbs = (r) => {
    if (!isNum(r.diff)) return "—";
    const t = r.tol_kind === "abs" ? r.tol : r.tol_abs, k = Math.min(4, digits(r, t)), v = inUnit(r, r.diff);
    return (fmt.num(v, k) !== fmt.num(0, k) ? fmt.signed(v, k) : v === 0 || r.ok ? "0" : sci(v)) + tail(r);
  };
  const rel = (r) => r.tol_kind === "rel" && isNum(r.diff_rel);
  const rows = list(cm.rows);
  const bad = rows.filter((r) => !r.ok).length;
  return card({ title: "Сверка с контрольной моделью",
    sub: `годовая модель, написанная отдельно; ${fmt.date(cm.as_of)}, коммит ${String(cm.commit || "—").slice(0, 7)}; ${fmt.num(rows.length)} ${plural(rows.length, ["строка", "строки", "строк"])}, вне допуска — ${fmt.num(bad)}. `
      + "Разность и допуск — по виду допуска: относительные — в процентах, абсолютные — в единице строки (доли и сдвиги — в п.п.); разность мельче знаков допуска — ноль." },
    dataTable([
      Tc("Что", (r) => wordsText(d, r.what), "name"),
      Nc("Ядро", (r) => level(r, r.core)),
      Nc("Контрольная", (r) => level(r, r.control)),
      Nc("Разница", (r) => (rel(r) ? fmt.signedPct(r.diff_rel, 2) : diffAbs(r))),
      Nc("Допуск", (r) => (r.tol_kind === "rel" ? fmt.pct(r.tol, 1) + (isNum(r.tol_abs) ? ` или ${tolAbs(r, r.tol_abs)}` : "") : tolAbs(r, r.tol))),
      Tc("", (r) => (r.ok ? badge("в допуске", "good") : badge("вне допуска", "bad"))),
    ], rows));
}

function historyCard(d) {
  const h = obj(d.history);
  const q = list(h.quarters);
  if (!q.length) return card({ title: "Отчётная история" }, missing("история"));
  const cats = q.map((r) => r.period);
  const ni = columnsChart(q.map((r) => ({ key: r.period, label: periodShort(r.period), value: obj(r.ifrs).ni_sh, tipTitle: periodLabel(r.period) })),
    { height: 190, color: "var(--series-neutral)", valueName: "млрд ₽", fmt: (v) => fmt.bn(v, 1), short: () => "", label: "Прибыль акционерам по кварталам" });
  const lines = linesChart([
    { name: "ЧПМ, упр.", color: "var(--model)", dots: true, r: 3, points: q.map((r) => [r.period, obj(r.mgmt).nim]) },
    { name: "CoR, упр.", color: "var(--neg)", dots: true, r: 3, points: q.map((r) => [r.period, obj(r.mgmt).cor]) },
    { name: "CoR, движок", color: "var(--neg)", dashed: true, points: q.map((r) => [r.period, obj(r.ifrs).cor]) },
  ], { xType: "band", categories: cats, height: 190, yFmt: (v) => fmt.pct(v, 1), tipFmt: (v) => fmt.pct(v, 2), xFmt: periodShort, label: "ЧПМ и CoR по кварталам" });
  const both = el("div", { class: "split" },
    el("div", {}, tileLabel(`Прибыль акционерам, ${basisName("ifrs")}, млрд ₽`), ni),
    el("div", {}, tileLabel("ЧПМ и CoR"), legend([["key-line key-model", "ЧПМ, упр."], ["key-line key-neg", "CoR, упр."], ["key-line key-neg2", "CoR, движок"]]), lines));
  const fig = withTable(both, () => dataTable([
    Tc(upperFirst(modelUnit(d).one), (r) => periodLabel(r.period), "name"),
    Nc("ЧП акционерам", (r) => fmt.num(obj(r.ifrs).ni_sh, 1)),
    Nc("ROE", (r) => fmt.pct(obj(r.ifrs).roe, 1)),
    Nc("ЧПМ упр.", (r) => fmt.pct(obj(r.mgmt).nim, 2)),
    Nc("CoR упр.", (r) => fmt.pct(obj(r.mgmt).cor, 2)),
    Nc("CIR упр.", (r) => fmt.pct(obj(r.mgmt).cir, 1)),
    Nc(capTitle(d, "n20"), (r) => fmt.pct(r.n20, 1)),
  ], q.slice().reverse()));
  return card({ title: "Отчётная история", tools: fig.button, sub: "МСФО группы и управленческие метрики — раздельно; нераскрытое не рисуется: линия через него не проводится" },
    fig.box,
    list(h.gaps).length ? foot(sentence("Провалы раскрытия: " + list(h.gaps).map((g) => `${g.basis ? `${basisName(g.basis)}, ` : ""}${String(g.period).replace(/\d{4}Q[1-4]/g, (m) => periodLabel(m))} — ${ruText(g.reason)}`).join("; "))) : null);
}

/* ── пояс плашек ── */

function flag(d, name) { return list(obj(d.checks).flags).find((f) => f.name === name && f.raised) || null; }
function releaseAgeHours(d) {
  const m = obj(d.meta);
  const t = Date.parse(m.published_at || m.generated_at);
  return Number.isFinite(t) ? (Date.now() - t) / 3.6e6 : null;
}

// Предупреждения — в одном поясе над любым экраном; условия — только выпуск и часы зрителя.
function banners(d) {
  const out = [];
  const m = obj(d.meta);
  const age = releaseAgeHours(d);
  if (isNum(age) && age > STALE_HOURS) out.push(plain("banner-stale", `Выпуску ${fmt.days(Math.floor(age / 24))}: опубликован ${fmt.stamp(m.published_at || m.generated_at)}, а конвейер обновляет панель каждый будний день. Числа ниже — не сегодняшние.`));
  const live = obj(d.live);
  if (live.degraded_flag) {
    const notes = list(live.degraded);
    out.push(plain("banner-degraded", [el("strong", {}, "Часть входов не свежая."),
      notes.length ? el("ul", { class: "reasons" }, notes.map((n) => el("li", {}, reasonText(n)))) : " Причина в выпуске не названа."]));
  }
  const say = (name, kind, extra) => {
    const f = flag(d, name);
    if (f) out.push(plain(kind, [el("strong", {}, sentence(f.title || name)), f.detail ? " " + sentence(upperFirst(wordsText(d, f.detail))) : "", extra || ""]));
  };
  say("price_fallback", "banner-degraded");
  say("report_fact", "banner-event");
  const overdue = list(obj(d.checks).gates).find((g) => g.name === "manual_input_overdue" && g.fired);
  if (overdue) out.push(plain("banner-degraded", [el("strong", {}, sentence(overdue.title || "Ручной вход просрочен")), " ", sentence(upperFirst(wordsText(d, overdue.message || overdue.explanation || "причина в выпуске не названа")))]));
  say("book_update", "banner-degraded");
  say("explanation_expiring", "banner-info");
  say("policy_expired", "banner-info");
  say("ni_jump", "banner-info");
  say("dividend_register", "banner-event");
  const rec = list(obj(d.dividends).register).find((r) => r.status === "recommended");
  say("dividend_recommended", "banner-event", rec ? ` DPS ${fmt.rub(rec.dps, 2)} за ${rec.year}; до собрания модель платит по формуле.` : "");
  say("ras_mismatch", "banner-degraded");
  if (m.book_first_period_closed) {
    // До отчёта — справка; тревога — флаг report_fact и отставание больше чем на период.
    const nf = obj(obj(d.calendar).next_fact), head = `Первый прогнозный ${modelUnit(d).one} книги (${periodLabel(m.first_period)}) закрыт по календарю`;
    if (m.periods_closed > 1) out.push(plain("banner-degraded", `${head}, а факты и книга прежние: оценка на ${fmt.date(m.valuation_date)} считается на них.`));
    else if (!flag(d, "report_fact")) out.push(plain("banner-info", `${head}${nf.covers === m.first_period ? `, отчёт за него ожидается ${nf.confirmed ? "" : "≈" + NBSP}${fmt.date(nf.date)}` : ""}: до отчёта оценка считается на фактах ${periodLabel(m.anchor_period)} и прогнозе книги.`));
  }
  for (const r of list(obj(d.dividends).register)) {
    const left = daysBetween(m.valuation_date, r.last_buy_date);
    if ((r.status === "declared" || r.status === "recommended") && isNum(left) && left >= 0 && left <= 7) {
      // ключ без счёта дней: свёрнутая не разворачивается каждое утро, только в последний день
      out.push(plain("banner-event", `Отсечка скоро: последний день покупки под дивиденд ${fmt.rub(r.dps, 2)} за ${r.year} — ${fmt.date(r.last_buy_date)}${left === 0 ? " (сегодня)" : `, через ${fmt.days(left)}`}.`, `buy${r.last_buy_date}${left ? "" : "!"}`));
    }
  }
  const week = list(obj(d.calendar).recent).filter((e) => ["ifrs", "agm", "record", "cbr_rate", "investor_day"].includes(e.kind));
  if (week.length) out.push(plain("banner-info", `События недели: ${week.map((e) => `${fmt.date(e.date)} — ${lowerFirst(ruText(e.title))}${e.note ? ` (${ruText(e.note)})` : ""}`).join("; ")}.`));
  for (const k of [...folded()]) if (!out.some((n) => n.foldKey === k)) folded().delete(k);       // плашки нет — ключ не копится
  return out.length ? [el("div", { class: "belt" }, out)] : [];
}

// Плашку сворачивают нажатием в строку-ярлык (кроме «выпуск устарел»); свёрнутое помнит браузер, пока
// у плашки тот же ключ (по умолчанию — её текст): новая или изменившаяся приходит развёрнутой.
const FOLD = "tzi-banners";
let FOLDED = null;
function folded() {
  if (!FOLDED) {
    try { FOLDED = new Set(list(JSON.parse(window.localStorage.getItem(FOLD)))); } catch (e) { FOLDED = new Set(); }
  }
  return FOLDED;
}

function plain(kind, message, key) {
  const node = el("div", { class: cls("banner", kind), role: "status" }, el("span", { class: "banner-icon", "aria-hidden": "true" }, "!"), el("div", { class: "banner-body" }, message));
  if (kind === "banner-stale") return node;
  const id = key || node.textContent, set = folded();
  const show = () => {
    const off = set.has(id);
    node.classList[off ? "add" : "remove"]("is-folded");
    node.setAttribute("aria-expanded", String(!off));
    node.title = off ? node.textContent.slice(1) : "Свернуть";
  };
  const flip = (e) => {
    if (e.key ? e.key !== "Enter" && e.key !== " " : window.getSelection && String(window.getSelection())) return;
    e.preventDefault();
    if (!set.delete(id)) set.add(id);
    try { window.localStorage.setItem(FOLD, JSON.stringify([...set])); } catch (err) { /* нет хранилища — до перезагрузки */ }
    show();
  };
  setAttrs(node, { role: "button", tabindex: "0", on: { click: flip, keydown: flip } });
  node.foldKey = id;
  show();
  return node;
}

/* ── экраны и переходы ── */

const SCREENS = {
  overview: screenOverview,
  market: screenMarket,
  model: screenModel,
  report: screenReport,
  capital: screenCapital,
  book: screenBook,
};

// Старые адреса семейства ведут на экраны банка.
const OLD_HASHES = { debt: "capital", money: "capital", value: "overview", priced: "market" };

// Экран по адресу: битая %-последовательность или незнакомое имя — «Оценка», а не исключение.
function screenFromHash() {
  let name = location.hash.replace(/^#/, "");
  try { name = decodeURIComponent(name); } catch (error) { name = ""; }
  if (Object.prototype.hasOwnProperty.call(OLD_HASHES, name)) name = OLD_HASHES[name];
  return Object.prototype.hasOwnProperty.call(SCREENS, name) ? name : "overview";
}

// Адрес — ровно `#<экран>`: битый, старый или чужой хэш заменяется без новой записи истории.
function syncHash(name) {
  if (location.hash.replace(/^#/, "") !== name) history.replaceState(null, "", `#${name}`);
}

function render(name) {
  const app = $("#app");
  CURRENT = name;
  hideTip();
  for (const tab of document.querySelectorAll(".tab")) {
    const on = tab.dataset.screen === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    if (on && tab.scrollIntoView) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  let screen;
  try {
    screen = SCREENS[name](DATA);
  } catch (error) {
    console.error(error);
    screen = el("div", { class: "screen" }, card({ title: "Экран не отрисовался", extra: "broken" },
      el("p", { class: "prose" }, "Ошибка витрины на этом экране; данные выпуска и остальные экраны не затронуты."),
      el("pre", { class: "fatal" }, String((error && error.stack) || error).slice(0, 800))));
  }
  let belt = [];
  try { belt = banners(DATA); } catch (error) { console.error(error); }
  app.replaceChildren(...belt, screen);
  const title = document.querySelector(`.tab[data-screen="${name}"]`);
  document.title = `${title ? title.textContent + " · " : ""}${company(DATA)} — справедливая стоимость`;
}

function go(name, push = true) {
  if (!Object.prototype.hasOwnProperty.call(SCREENS, name)) name = "overview";
  if (push && location.hash !== `#${name}`) history.pushState(null, "", `#${name}`);
  else if (!push) syncHash(name);
  render(name);
  window.scrollTo({ top: 0 });
}

function wireTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  for (const tab of tabs) {
    tab.addEventListener("click", () => { if (DATA) go(tab.dataset.screen); });
    tab.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(tab);
      const next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length] : e.key === "ArrowLeft" ? tabs[(i - 1 + tabs.length) % tabs.length]
        : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : null;
      if (next) { e.preventDefault(); next.focus(); if (DATA) go(next.dataset.screen); }
    });
  }
  window.addEventListener("hashchange", () => {
    if (!DATA) return;
    const name = screenFromHash();
    syncHash(name);
    if (name === CURRENT) return;
    render(name);
    window.scrollTo({ top: 0 });
  });
  const skip = $(".skip");
  if (skip) skip.addEventListener("click", (e) => { e.preventDefault(); $("#app").focus(); });
}

function wireTheme() {
  const button = $("#theme-toggle");
  if (!button) return;
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    if (window.__theme) window.__theme.remember(next);
  });
}

/* ── шапка и подвал ── */

function releaseChip(d) {
  const chip = $("#release-chip");
  if (!chip) return;
  const m = obj(d.meta);
  const age = releaseAgeHours(d);
  const warn = obj(d.live).degraded_flag || ["price_fallback", "report_fact", "book_update"].some((n) => flag(d, n))
    || list(obj(d.checks).gates).some((g) => g.name === "manual_input_overdue" && g.fired) || m.periods_closed > 1;
  chip.dataset.state = isNum(age) && age > STALE_HOURS ? "stale" : warn ? "warn" : "ok";
  const at = m.published_at || m.generated_at;
  $(".chip-text", chip).replaceChildren(el("span", {}, `Выпуск ${fmt.dateShort(mskDay(at))}, ${fmt.time(at)}`), el("span", { class: "chip-extra" }, ` · книга ${m.book_version || "—"}`));
  chip.title = chip.dataset.state === "stale" ? "Выпуск старше 96 часов" : warn ? "Есть предупреждения — см. плашки" : "Выпуск свежий";
}

function colophon(d) {
  const node = $("#colophon-release");
  const m = obj(d.meta);
  if (node) {
    node.textContent = `Модель 850 · ${company(d)} (${tickerList(d)}) · книга допущений ${m.book_version || "—"} · оценка на ${fmt.date(m.valuation_date)} · `
      + `факты на ${fmt.date(m.facts_date)} · выпуск ${String(m.payload_sha256 || "—").slice(0, 12)} · код ${String(m.engine_commit || "—").slice(0, 7)} · контракт ${d.schema}`;
  }
  const brand = $(".brand-name");
  if (brand) brand.textContent = company(d);
}

/* ── загрузка ── */

function fatal(title, detail, lede = "Прежний выпуск остаётся на месте; витрина покажет его, как только ответ придёт.") {
  $("#app").replaceChildren(el("div", { class: "fatal" }, el("h1", {}, title), lede ? el("p", {}, lede) : null, detail ? el("pre", {}, detail) : null));
  const chip = $("#release-chip");
  if (chip) { chip.dataset.state = "stale"; $(".chip-text", chip).textContent = "данные недоступны"; }
}

async function boot() {
  wireTips();
  wireTabs();
  wireTheme();
  let response, body;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), API_TIMEOUT_MS);
  try {
    response = await fetch(API, { headers: { accept: "application/json" }, cache: "no-cache", signal: abort.signal });
    body = await response.text();
  } catch (error) {
    fatal("Данные недоступны", abort.signal.aborted ? `Сервер не ответил за ${API_TIMEOUT_MS / 1000} с` : `Сеть: ${error.message}`);
    return;
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    // Два 503 двери: выпуска ещё нет — или источник не ответил, а копии края нет.
    let reason = null;
    try { reason = obj(JSON.parse(body)).error; } catch (error) { reason = null; }
    const detail = `HTTP ${response.status} · ${String(body).slice(0, 600)}`;
    if (response.status === 503 && reason === "not published yet") fatal("Выпуск ещё не опубликован", detail, "Конвейер ещё не выложил ни одного выпуска; витрина покажет его, как только он появится.");
    else if (response.status === 503) fatal("Источник данных временно недоступен", detail);
    else fatal("Данные недоступны", detail);
    return;
  }
  try {
    DATA = JSON.parse(body);
  } catch (error) {
    fatal("Ответ не разобрался как JSON", String(body).slice(0, 400));
    return;
  }
  if (!DATA || !DATA.meta || !DATA.fair_value) {
    DATA = null;
    fatal("Выпуск без обязательных блоков", "нет meta или fair_value");
    return;
  }
  try { releaseChip(DATA); colophon(DATA); } catch (error) { console.error(error); }
  const name = screenFromHash();
  syncHash(name);
  render(name);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
