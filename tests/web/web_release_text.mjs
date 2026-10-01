// Текст шести экранов витрины на выпуске — без браузера (заглушка DOM tests/web/dom_stub.mjs).
// Общий тест «служебного текста на экране нет» (docs/PAYLOAD.md §0.2): ни имён полей и ключей,
// ни true/false, inf, кортежей и e-нотации, ни кодов периодов и дат ISO, ни меток класса допущения,
// путей к листам и файлам, рабочих пометок, хвоста sha256 вне карточки «Выпуск и книга»; проценты —
// с пробелом и запятой, падежи согласованы (после дробного числа — «года», счётчик проверок — в падеже
// своего числа); ни имён методов и полей API, ни пометок о токене. Вторая часть — строки самого выпуска,
// которые витрина печатает: дата без года и номер версии без имени читаются как дробь (на экране их уже
// не отличить); имя метода API и рабочая пометка ловятся и в строке, которую экран сейчас не показывает.
//
//   node tests/web/web_release_text.mjs                       # фикстура tests/fixtures/payload-sample.json
//   node tests/web/web_release_text.mjs <выпуск.json>         # любой выпуск (var/state/release/latest.json)
//   node tests/web/web_release_text.mjs <выпуск.json> --dump  # напечатать текст экранов, без проверок
//
// Печатает JSON {release, screens, failed: [{screen, rule, sample}]}; код выхода 1 — если есть отказы.
import { readFileSync } from "node:fs";
import { SAMPLE_TEXT, SCREEN_NAMES, makePage, visibleText, findNode, squash } from "./dom_stub.mjs";

const args = process.argv.slice(2);
const dump = args.includes("--dump");
const path = args.find((a) => !a.startsWith("--")) || null;
const text = path ? readFileSync(path, "utf8") : SAMPLE_TEXT;
const release = JSON.parse(text);

// Правила экрана: [название, выражение, карточка, текст которой правило не читает].
const META_CARD = "Выпуск и книга";
// Рабочая пометка и имя метода, сервиса или поля ответа API (вида GetPayouts, payoutNet): источник — словами для владельца.
const WORKING_NOTE = /ведущ|первичк|антибот|без токена/i;
const API_NAME = /(?<![\p{L}\d_])(?:Get|List|Post|Find)[A-Z][A-Za-z]+|(?<![\p{L}\d_])[a-z]+(?:[A-Z][a-z\d]+)+(?![\p{L}\d_])|[A-Z][a-z]+Service(?![\p{L}])|(?<![\p{L}\d])API(?![\p{L}\d])/u;
const RULES = [
  ["имя поля или ключа (snake_case)", /(?<![\p{L}\d_])[a-z]+(?:_[a-z0-9]+)+(?![\p{L}\d_])/u],
  ["ключ книги через точку", /(?<![\p{L}\d_.])[a-z_]+(?:\.[a-z_*]+)+(?![\p{L}\d_])(?<!\.(?:json|ya?ml|pdf|md|xlsx?|csv|txt|py|ru|com|org|net|dev))/u],
  ["true/false", /(?<![\p{L}\d_`])(?:true|false|None)(?![\p{L}\d_`])/u],
  ["inf", /(?<![\p{L}\d_])-?inf(?![\p{L}\d_])/u],
  ["кортеж", /\(\d{4}, \d+, [A-Z]{3,}/],
  ["e-нотация", /(?<![\p{L}\d])\d(?:[.,]\d+)?e[-−+]?\d{1,3}(?![\p{L}\d])/u],
  ["код периода", /(?<![\p{L}\d])\d{4}(?:M\d{2}|Q[1-4])(?![\p{L}\d])/u],
  ["дата ISO", /(?<![\d-])\d{4}-\d{2}-\d{2}(?![\d-])/],
  ["десятичная точка в проценте", /\d\.\d+\s?%/],
  ["процент без пробела", /\d%/],
  ["«п.п..»", /п\.п\.\./],
  ["точка после многоточия", /…\./],
  ["число прогонов без разрядки", /\d{4,} прогон/],
  ["падеж после числа на 1", /в (?:\d*[02-9])?1 (?:клетках|кварталах)/],
  ["падеж после дробного числа", /\d,\d+ (?:год|лет|день|дней)(?![а-яё])/],
  ["счётчик без согласования", /(?<![\d,])(?:\d*[02-9])?1 (?:нарушенных|сработавших|поднятых)|(?<![\d,])(?:\d*[02-9])?[2-4] (?:нарушенных инвариантов|сработавших гейтов|поднятых флагов)|\d кл\./],
  ["метка клетки W/r/s", /(?<![\p{L}\d])[A-Z]\/[a-z_]+\/[a-z_]+/u],
  ["метка класса допущения", /\[(?:В|Ф|Р|НП)\]/],
  ["хвост sha256", /sha256 [0-9a-f]{8,}/, META_CARD],
  ["файл или путь к листу", /(?<![\p{L}\d])[\w.-]+\.(?:json|ya?ml|pdf|md|xlsx?|csv|txt|py)(?![\p{L}\d])|(?<![\p{L}\d\/.])[a-z][a-z\d_]*(?:\/[a-z\d_.-]+)+/u],
  ["рабочая пометка", WORKING_NOTE],
  ["имя метода или поля API", API_NAME],
  ["версия без имени", /850oa \d+[.,]\d/],
  ["номер версии уравнения", /Уравнение [a-z]+-\d/],
  ["название типа вместо значения", /словарь долей/],
  ["NaN, undefined, объект", /NaN|undefined|\[object Object\]|Infinity/],
  ["экран или график не отрисовался", /не отрисовал/],
];

// Строки выпуска, которые витрина печатает как есть (П§0.2).
const PRINTED = new Set(["title", "name", "note", "message", "detail", "source", "src", "equation", "explanation", "scope_note", "valid_until_note",
  "basis", "event", "reason", "text", "condition", "rule", "method", "definition", "what", "summary"]);
const DATA_RULES = [
  ["дата без года", /(?<![\d.,§\p{L}-])(?:0[1-9]|[12]\d|3[01])\.(?:0[1-9]|1[0-2])(?![\d.]|\.\d|\s?%)/u],
  ["версия без имени", /850oa \d+\.\d/],
  ["рабочая пометка", WORKING_NOTE],
  ["имя метода или поля API", API_NAME],
];
function* printed(node, key, where) {
  if (Array.isArray(node)) for (const [i, v] of node.entries()) yield* printed(v, key, `${where}[${i}]`);
  else if (node && typeof node === "object") for (const [k, v] of Object.entries(node)) yield* printed(v, k, where ? `${where}.${k}` : k);
  else if (typeof node === "string" && PRINTED.has(key)) yield [where, node];
}

// Часы страницы — час после публикации: плашки «выпуск устарел» нет, остальные плашки — как в выпуске.
const stamp = Date.parse(release.meta.published_at || release.meta.generated_at);
const page = makePage({ payload: text, now: Number.isFinite(stamp) ? stamp + 3.6e6 : null });
await page.boot();
await new Promise((resolve) => setTimeout(resolve, 20));

// Раскрыть таблицы-двойники и полные списки: их текст — тоже экран.
function openAll(node) {
  if (!node || node.nodeType !== 1) return;
  if (node.tagName === "BUTTON" && /^(Таблица|Показать все)/.test(node.textContent)) node.dispatch("click");
  for (const child of [...node.children]) openAll(child);
}

// Текст экрана без одной карточки (хвост sha256 законен только в «Выпуск и книга»).
const without = (title) => {
  const card = findNode(page.app, (n) => n.tagName === "SECTION" && n.children.some((c) => squash(visibleText(c)).startsWith(title)));
  if (!card) return null;
  const was = card.hidden;
  card.hidden = true;
  const out = squash(visibleText(page.app));
  card.hidden = was;
  return out;
};

const failed = [];
const screens = {};
for (const name of SCREEN_NAMES) {
  page.hashchange(`#${name}`);
  openAll(page.app);
  page.paint();
  const shown = squash(visibleText(page.app));
  screens[name] = shown;
  if (page.bootError || page.errors.length) failed.push({ screen: name, rule: "ошибка витрины", sample: String(page.bootError || page.errors[0]).slice(0, 300) });
  page.errors.length = 0;
  for (const [rule, re, skip] of RULES) {
    const plain = (skip && without(skip)) || shown;
    const hits = [...plain.matchAll(new RegExp(re.source, re.flags.replace("g", "") + "g"))];
    const seen = new Set();
    for (const hit of hits) {
      if (seen.has(hit[0]) || seen.size >= 6) continue;
      seen.add(hit[0]);
      failed.push({ screen: name, rule, hit: hit[0], sample: plain.slice(Math.max(0, hit.index - 70), hit.index + hit[0].length + 50) });
    }
  }
}
for (const [where, value] of printed(release, null, "")) {
  for (const [rule, re] of DATA_RULES) {
    const hit = re.exec(value);
    if (hit) failed.push({ screen: `выпуск: ${where}`, rule, hit: hit[0], sample: value.slice(Math.max(0, hit.index - 70), hit.index + hit[0].length + 50) });
  }
}

if (dump) {
  for (const name of SCREEN_NAMES) console.log(`\n===== ${name} =====\n${screens[name]}`);
} else {
  console.log(JSON.stringify({ release: path || "tests/fixtures/payload-sample.json", screens: SCREEN_NAMES.length, failed }, null, 1));
  process.exitCode = failed.length ? 1 : 0;
}
