// Поведение витрины web/app.js без браузера: скрипт грузится в node:vm с заглушкой DOM
// (tests/web/dom_stub.mjs). Выпуск — синтетическая фикстура tests/fixtures/payload-sample.json,
// для частных случаев — её копии с правками. Запускает tests/test_web_node.py; печатает
// JSON с итогами, код выхода 1 — если хоть одна проверка не прошла.
import { SAMPLE_TEXT, SCREEN_NAMES, makePage, visibleText, countTag, findNode, squash } from "./dom_stub.mjs";

const sample = () => JSON.parse(SAMPLE_TEXT);

const checks = [];
const check = (name, cond, detail) => checks.push({ name, ok: !!cond, detail: cond ? undefined : detail });

const rejections = [];
process.on("unhandledRejection", (reason) => rejections.push(String((reason && reason.stack) || reason)));
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
const BAD_TEXT = /NaN|undefined|\[object Object\]|Infinity/;
// Часы страницы — день после публикации фикстуры: выпуск свежий.
const FRESH = Date.parse("2026-10-01T09:00:00+03:00");

// Подсказки узлов графика — WeakMap самой витрины.
const tipsOf = (page, root, tag) => {
  const out = [];
  const walk = (n) => {
    if (!n || n.nodeType !== 1) return;
    if (n.tagName === tag) { page.ctx.__n = n; const t = page.get("TIPS.get(globalThis.__n)"); if (t) out.push(t); }
    n.children.forEach(walk);
  };
  walk(root);
  return out;
};
const cardOf = (page, title) => findNode(page.app, (n) => n.tagName === "SECTION" && n.children.some((c) => squash(visibleText(c)).startsWith(title)));

/* ── 1. адрес: битый, старый или чужой хэш ── */

{
  const page = makePage();
  const cases = [["#overview", "overview"], ["#book", "book"], ["#%62ook", "book"], ["", "overview"], ["#nope", "overview"], ["#capital", "capital"],
    ["#debt", "capital"], ["#money", "capital"], ["#value", "overview"], ["#priced", "market"],
    ["#overview?utm_source=tg", "overview"], ["#%E0%A4%A", "overview"], ["#%", "overview"], ["#100%", "overview"], ["#%FF", "overview"]];
  for (const [hash, want] of cases) {
    page.url.hash = hash;
    let got, error = null;
    try { got = page.get("screenFromHash()"); } catch (e) { error = String(e); }
    check(`screenFromHash ${JSON.stringify(hash)} → ${want}`, !error && got === want, error || got);
  }
}

for (const [hash, want] of [["#%E0%A4%A", "overview"], ["#debt", "capital"], ["#money", "capital"], ["#value", "overview"]]) {
  const page = makePage({ href: `https://example.pages.dev/${hash}`, now: FRESH });
  const before = rejections.length;
  await page.boot();
  await settle();
  check(`старт с ${hash}: экран ${want}, адрес #${want}`, page.get("CURRENT") === want && page.url.hash === `#${want}`, [page.get("CURRENT"), page.url.hash]);
  check(`старт с ${hash}: без исключений`, !page.bootError && rejections.length === before && !page.errors.length, [page.bootError, page.errors.slice(0, 2)]);
}

{
  const page = makePage({ href: "https://example.pages.dev/#book", now: FRESH });
  await page.boot();
  await settle();
  let error = null;
  try { page.hashchange("#%E0%A4%A"); } catch (e) { error = String(e); }
  check("битый хэш после загрузки: «Оценка», адрес #overview, без исключения", !error && page.get("CURRENT") === "overview" && page.url.hash === "#overview", [error, page.url.hash]);
  page.hashchange("#debt");
  check("старый адрес #debt после загрузки ведёт на #capital", page.get("CURRENT") === "capital" && page.url.hash === "#capital", [page.get("CURRENT"), page.url.hash]);
  check("заголовок вкладки — экран и имя эмитента из выпуска", page.document.title === `capital · ${sample().meta.company.name} — справедливая стоимость`, page.document.title);
}

/* ── 2. все экраны фикстуры в ширине карточки и телефона ── */

for (const width of [640, 343]) {
  const page = makePage({ width, now: FRESH });
  await page.boot();
  await settle();
  for (const name of SCREEN_NAMES) {
    page.hashchange(`#${name}`);
    const text = page.text();
    check(`экран ${name} (${width} px): без ошибок`, page.get("CURRENT") === name && !page.errors.length && !/не отрисовал/.test(text),
      page.errors.slice(0, 2).concat(text.match(/.{0,80}не отрисовал.{0,120}/) || []));
    check(`экран ${name} (${width} px): графики нарисованы`, countTag(page.app, "SVG") > 0, countTag(page.app, "SVG"));
    const bad = text.match(new RegExp(`.{0,60}(${BAD_TEXT.source}).{0,40}`));
    check(`экран ${name} (${width} px): нет NaN/undefined в тексте`, !bad, bad && bad[0]);
    page.errors.length = 0;
  }
  check(`пояс плашек пуст на свежем выпуске (${width} px)`, !/Выпуску \d/.test(page.text()), page.text().slice(0, 200));
}

/* ── 3. ползунок λ: шаг — сетка выпуска, пересчёт — правило книги ── */

{
  const d = sample();
  const head = d.fair_value.headline;
  const page = makePage({ now: FRESH });
  await page.boot();
  await settle();
  const slider = findNode(page.app, (n) => n.tagName === "INPUT" && n.attrs.id === "lambda");
  check("ползунок λ есть на «Оценке»", !!slider, null);
  check("шаг ползунка = lambda_step выпуска", slider && slider.attrs.step === String(head.lambda_step), slider && slider.attrs.step);
  check("сетка by_lambda — 21 шаг с шагом lambda_step", head.by_lambda.length === 21
    && head.by_lambda.every((r, i) => Math.abs(r.lambda - i * head.lambda_step) < 1e-9), head.by_lambda.map((r) => r.lambda));
  page.ctx.__h = head;
  let worst = 0, printed = 0;
  for (const row of head.by_lambda) {
    page.ctx.__lam = row.lambda === head.own_macro_confidence ? row.lambda + 1e-12 : row.lambda;
    const hl = page.get("headlineAt(globalThis.__h, globalThis.__lam)");
    worst = Math.max(worst, Math.abs(hl.median - row.median), Math.abs(hl.band80[0] - row.p10), Math.abs(hl.band80[1] - row.p90),
      Math.abs(hl.band50[0] - row.p25), Math.abs(hl.band50[1] - row.p75));
    if (Math.abs(hl.printed_median - page.get(`roundHalfUp(${row.median} / ${head.print_step}) * ${head.print_step}`)) > 1e-9) printed += 1;
  }
  check("правило λ фронта воспроизводит строки by_lambda (квантили до 0,006 ₽)", worst <= 0.006, worst);
  check("печать при λ ≠ книги — половиной вверх к print_step", printed === 0, printed);
  check("половина вверх: 372,5 → 375, 367,5 → 370, 372,49 → 370", page.get("[372.5, 367.5, 372.49].map((v) => roundHalfUp(v / 5) * 5).join()") === "375,370,370",
    page.get("[372.5, 367.5, 372.49].map((v) => roundHalfUp(v / 5) * 5).join()"));
  const headline = () => squash(visibleText(findNode(page.app, (n) => n.attrs.id === "fv-headline")));
  const upside = () => squash(visibleText(findNode(page.app, (n) => n.attrs.id === "fv-upside")));
  check("при λ книги — печатаемая медиана выпуска", headline().includes(page.get(`fmt.num(${head.printed_median})`)), headline());
  for (const v of ["0", "1"]) {
    slider.value = v;
    slider.dispatch("input");
    page.paint();
    const row = head.by_lambda.find((r) => Math.abs(r.lambda - Number(v)) < 1e-9);
    const want = page.get(`fmt.num(roundHalfUp(${row.median} / ${head.print_step}) * ${head.print_step})`);
    check(`λ = ${v}: заголовок — печать медианы строки by_lambda`, headline().includes(want), [headline(), want]);
    check(`λ = ${v}: потенциал — строка by_lambda`, upside().includes(squash(page.get(`fmt.signedPct(${row.upside.SBER}, 0)`))), upside());
    check(`λ = ${v}: без ошибок`, !page.errors.length, page.errors.slice(0, 2));
  }
  const reset = findNode(page.app, (n) => n.attrs.class === "lambda-reset");
  reset.dispatch("click");
  check("кнопка «вернуть λ книги» возвращает числа выпуска", page.get("LAMBDA") === null && headline().includes(page.get(`fmt.num(${head.printed_median})`)), headline());
}

/* ── 4. выпуск без необязательных полей и без блоков ── */

function stripOptional(d) {
  delete d.market.brokers.history;
  delete d.capital.observed;
  delete d.capital.anchor.n10_bank;
  delete d.regimes.reference_class;
  delete d.guidance.strategy;
  delete d.checks.control_model;
  delete d.fair_value.jump_guard.note_valid_until;
  delete d.dividends.next_expected.last_buy_date;
  d.changes.vs_previous = null;
  for (const r of d.market.peers.rows) delete r.reason;
  return d;
}
const variants = [
  ["без необязательных полей", stripOptional(sample())],
  ["без нау-каста, индикаторов, истории оценки", (() => { const d = sample(); d.nowcast = {}; d.indicators = {}; d.valuation_history = {}; d.history = {}; return d; })()],
  ["без суждений и обратного расчёта", (() => { const d = sample(); d.judgements = {}; d.reverse_dcf = {}; d.fair_value.headline.contributions = []; return d; })()],
  ["быстрая сборка без прогонов", (() => { const d = sample(); d.meta.fast = true; d.fair_value.headline.low_draws = []; d.fair_value.headline.high_draws = []; return d; })()],
];
for (const [name, d] of variants) {
  const page = makePage({ payload: JSON.stringify(d), now: FRESH, width: 343 });
  await page.boot();
  await settle();
  for (const s of SCREEN_NAMES) {
    page.hashchange(`#${s}`);
    const text = page.text();
    check(`${name}: экран ${s} без ошибок`, !page.bootError && !page.errors.length && !/не отрисовал/.test(text),
      [page.bootError, page.errors.slice(0, 2), (text.match(/.{0,80}не отрисовал.{0,160}/) || [])[0]]);
    const bad = text.match(new RegExp(`.{0,60}(${BAD_TEXT.source}).{0,40}`));
    check(`${name}: экран ${s} без NaN/undefined`, !bad, bad && bad[0]);
    page.errors.length = 0;
  }
}

/* ── 5. пояс плашек и ярлык выпуска ── */

{
  const d = sample();
  const stale = makePage({ now: Date.parse("2026-10-10T12:00:00+03:00") });
  await stale.boot();
  await settle();
  check("выпуск старше 96 ч — плашка и красный ярлык", /Выпуску 9 дней/.test(stale.text()) && stale.get('document.querySelector("#release-chip").dataset.state') === "stale",
    [stale.text().slice(0, 200), stale.get('document.querySelector("#release-chip").dataset.state')]);
  const fresh = makePage({ now: FRESH });
  await fresh.boot();
  await settle();
  check("свежий выпуск — зелёный ярлык с временем МСК", fresh.get('document.querySelector("#release-chip").dataset.state') === "ok" && /МСК/.test(squash(visibleText(fresh.chipText))),
    squash(visibleText(fresh.chipText)));
  check("подвал — имя и тикеры из выпуска", squash(fresh.colophon.textContent).includes(`${d.meta.company.name} (${d.meta.company.tickers.join(" · ")})`), fresh.colophon.textContent);

  d.checks.flags.find((f) => f.name === "report_fact").raised = true;
  d.checks.flags.find((f) => f.name === "report_fact").detail = "МСФО за 9 месяцев 2026 г. вышло 2026-10-28; квартал в книге открыт";
  d.next_report.closing.published = true;
  d.checks.flags.find((f) => f.name === "price_fallback").raised = true;
  d.live.degraded_flag = true;
  d.live.degraded = ["цена SBERP не принята: торги не велись"];
  d.dividends.register.push({ year: 2026, dps: 20.5, amount: 463.0, record_date: "2026-10-06", last_buy_date: "2026-10-05", ex_date: "2026-10-06",
    pay_date: "2026-10-20", status: "declared", in_bridge: true, sources: ["синтетика"] });
  const page = makePage({ payload: JSON.stringify(d), now: FRESH });
  await page.boot();
  await settle();
  const text = page.text();
  check("флаг report_fact — плашка с датой по-русски", text.includes("Вышла МСФО, факт не внесён.") && text.includes("28.10.2026"), text.slice(0, 600));
  check("флаг price_fallback и деградация входов — плашки", text.includes("Живая цена не принята.") && text.includes("Часть входов не свежая."), text.slice(0, 600));
  check("отсечка в ближайшие 7 дней — плашка", /Отсечка скоро: последний день покупки под дивиденд 20,50 ₽ за 2026 — 05\.10\.2026, через 5 дней/.test(text), text.slice(0, 900));
  check("МСФО вышло — вместо отсчёта «Отчёт вышел»", text.includes("Отчёт вышел") && text.includes("факт ещё не внесён в модель"), text.slice(0, 900));
  check("жёлтый ярлык при деградации", page.get('document.querySelector("#release-chip").dataset.state') === "warn", page.get('document.querySelector("#release-chip").dataset.state'));

  // Закрытый первый квартал книги: до отчёта — справка и зелёный ярлык (обычное состояние между концом квартала и
  // отчётом); отчёт вышел — плашка флага вместо справки; книга отстала больше чем на квартал — тревога.
  const closedPage = async (patch) => {
    const s = sample();
    s.meta.book_first_period_closed = true;
    patch(s);
    const p = makePage({ payload: JSON.stringify(s), now: FRESH });
    await p.boot();
    await settle();
    return [p.text(), p.get('document.querySelector("#release-chip").dataset.state'), s];
  };
  const [waitText, waitChip, waitSample] = await closedPage(() => {});
  const due = waitSample.calendar.next_fact.date.split("-").reverse().join("\\.");       // дата отчёта образца выпуска — как на экране
  check("первый квартал книги закрыт, отчёта ещё нет — справка с датой отчёта, ярлык зелёный",
    waitSample.calendar.next_fact.covers === waitSample.meta.first_period
    && new RegExp(`закрыт по календарю, отчёт за него ожидается (≈\\s)?${due}: до отчёта оценка считается на фактах .{3,24} и прогнозе книги\\.`).test(waitText)
    && !waitText.includes("а факты и книга прежние") && waitChip === "ok", [waitText.slice(0, 500), waitChip]);
  const [outText, outChip] = await closedPage((s) => { s.checks.flags.find((f) => f.name === "report_fact").raised = true; });
  check("первый квартал закрыт, отчёт вышел — плашка флага без справки, ярлык жёлтый",
    outText.includes("Вышла МСФО, факт не внесён.") && !outText.includes("закрыт по календарю") && outChip === "warn", [outText.slice(0, 500), outChip]);
  const [lateText, lateChip] = await closedPage((s) => { s.meta.periods_closed = 2; });
  check("книга отстала больше чем на квартал — тревога и жёлтый ярлык",
    lateText.includes("закрыт по календарю, а факты и книга прежние") && lateChip === "warn", [lateText.slice(0, 500), lateChip]);

  // Сворачивание плашек: нажатие сворачивает в ярлык и разворачивает обратно; свёрнутое живёт при смене экрана и
  // после перезагрузки (хранилище браузера), пока текст плашки тот же; «выпуск устарел» не сворачивается.
  const store = () => { const data = {}; return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } }; };
  const banner = (p, word) => findNode(p.app, (n) => /(^| )banner( |$)/.test(n.attrs.class || "") && squash(n.textContent).includes(word));
  const isFolded = (n) => n.classList.contains("is-folded");
  const withBanners = () => {
    const s = sample();
    s.meta.book_first_period_closed = true;
    s.checks.flags.find((f) => f.name === "price_fallback").raised = true;
    return s;
  };
  const open = async (opts) => { const p = makePage({ now: FRESH, ...opts }); await p.boot(); await settle(); return p; };
  const shelf = store();
  const first = await open({ payload: JSON.stringify(withBanners()), storage: shelf });
  const closed = banner(first, "закрыт по календарю");
  check("плашка — кнопка: развёрнута, с подсказкой и фокусом с клавиатуры",
    closed && closed.getAttribute("role") === "button" && closed.getAttribute("aria-expanded") === "true" && closed.getAttribute("tabindex") === "0"
    && !isFolded(closed), closed && closed.attrs);
  closed.dispatch("click");
  check("нажатие сворачивает плашку: класс, признак, полный текст — подсказкой, текст в узле цел",
    isFolded(closed) && closed.getAttribute("aria-expanded") === "false" && closed.title.startsWith("Первый прогнозный") && first.text().includes("закрыт по календарю")
    && !isFolded(banner(first, "Живая цена не принята")), [closed.attrs, closed.title]);
  first.hashchange("#model");
  check("свёрнутая плашка остаётся свёрнутой на другом экране", isFolded(banner(first, "закрыт по календарю")) && !isFolded(banner(first, "Живая цена не принята")));
  const again = await open({ payload: JSON.stringify(withBanners()), storage: shelf });
  check("после перезагрузки свёрнутое помнит браузер", isFolded(banner(again, "закрыт по календарю")) && !isFolded(banner(again, "Живая цена не принята")), shelf.data);
  banner(again, "закрыт по календарю").dispatch("keydown", { key: "Tab" });
  check("посторонняя клавиша плашку не трогает", isFolded(banner(again, "закрыт по календарю")));
  banner(again, "закрыт по календарю").dispatch("keydown", { key: "Enter" });
  check("Enter разворачивает; хранилище пусто", !isFolded(banner(again, "закрыт по календарю")) && shelf.data["tzi-banners"] === "[]", shelf.data);
  banner(again, "закрыт по календарю").dispatch("click");
  const changed = withBanners();
  changed.meta.first_period = "2026Q4";
  changed.calendar.next_fact.covers = "2026Q4";
  const next = await open({ payload: JSON.stringify(changed), storage: shelf });
  check("текст плашки изменился — она приходит развёрнутой", banner(next, "закрыт по календарю") && !isFolded(banner(next, "закрыт по календарю")), shelf.data);
  banner(next, "Живая цена не принята").dispatch("click");
  check("ключ исчезнувшей плашки не копится в хранилище", JSON.parse(shelf.data["tzi-banners"]).length === 1, shelf.data);
  const bare = await open({ payload: JSON.stringify(withBanners()) });
  banner(bare, "закрыт по календарю").dispatch("click");
  check("без хранилища сворачивание работает до перезагрузки", isFolded(banner(bare, "закрыт по календарю")) && !bare.errors.length, bare.errors);
  const old = makePage({ now: Date.parse("2026-10-10T12:00:00+03:00") });
  await old.boot();
  await settle();
  const staleBanner = banner(old, "Выпуску 9 дней");
  staleBanner.dispatch("click");
  check("«выпуск устарел» не сворачивается", staleBanner.getAttribute("role") === "status" && !isFolded(staleBanner) && !staleBanner.hasAttribute("aria-expanded"), staleBanner.attrs);
  const buyDay = (today) => {
    const s = sample();
    s.meta.valuation_date = today;
    s.dividends.register.push({ year: 2026, dps: 20.5, amount: 463.0, record_date: "2026-10-06", last_buy_date: "2026-10-05", ex_date: "2026-10-06",
      pay_date: "2026-10-20", status: "declared", in_bridge: true, sources: ["синтетика"] });
    return JSON.stringify(s);
  };
  const days = store();
  const d5 = await open({ payload: buyDay("2026-09-30"), storage: days });
  banner(d5, "Отсечка скоро").dispatch("click");
  const d4 = await open({ payload: buyDay("2026-10-01"), storage: days });
  const d0 = await open({ payload: buyDay("2026-10-05"), storage: days });
  check("отсечка: свёрнутая плашка не разворачивается назавтра, только в сам последний день",
    isFolded(banner(d4, "Отсечка скоро")) && /сегодня/.test(banner(d0, "Отсечка скоро").textContent)
    && !isFolded(banner(d0, "Отсечка скоро")), [days.data, d4.text().slice(0, 300), d0.text().slice(0, 300)]);
}

/* ── 6. волна W1: подписи, дивиденд, пары передачи, доли, эталоны, гайденс ── */

{
  const d = sample();
  const page = makePage({ now: FRESH });
  await page.boot();
  await settle();
  const at = (name) => { page.hashchange(`#${name}`); return page.text(); };
  const rub = (v) => squash(page.get(`fmt.rub(${v}, 2)`));
  const nx = d.dividends.next_expected;
  let text = at("overview");
  check("W1: ближайший дивиденд — DPS политики с риском отмены", text.includes(rub(nx.dps)) && text.includes(`DPS по политике · риск отмены ${Math.round(nx.p_cancel * 100)} %`), text.slice(0, 1600));
  check("W1: среднее по клеткам — второй строкой", text.includes(`в среднем по клеткам ${rub(nx.dps_mean)}`), text.slice(0, 1600));
  check("№ 1: доли меньше 1 % — «< 1 %» без порядка; все оси в полосе — строки «вне полосы» нет", text.includes("Вклад < 1 %:") && !text.includes("Вне полосы"), text.match(/.{0,200}Вклад <.{0,300}/));
  text = at("model");
  const pair = d.nii.transmission.pairs[0];
  check("W1: парные передачи вместо одного числа", text.includes(`${pair.from} → ${pair.to}`) && text.includes("в коридоре") && !text.includes("ROE-эквивалент передачи"), text.match(/.{0,200}Передача по парам.{0,300}/));
  check("W1: ближний сдвиг ЧПМ — отдельной строкой", text.includes("Ближний сдвиг ЧПМ, общий для режимов и миров"), null);
  check("W1: гайденс по виду строки (пол Н20.0 — «≥»)", text.includes("≥ 13,3 %") && text.includes("≤ 1,4 %"), text.match(/.{0,100}Гайденс.{0,400}/));
  text = at("report");
  check("W1: названия эталонов — из выпуска, не ключи", text.includes(d.nowcast.retro.titles.ras_bridge) && !/\bras_bridge\b|\bprev_quarter\b|same_quarter_last_year/.test(text), text.match(/.{0,300}Эталоны на истории.{0,400}/));
  check("W1: дата решения о допуске — по-русски", text.includes("не раньше 28.10.2027") && !text.includes("2027-10-28"), text.match(/.{0,80}не раньше.{0,40}/));
  const t90 = findNode(page.app, (n) => n.tagName === "BUTTON" && n.textContent === "T-90");
  check("W1: ретро — переключатель горизонта", !!t90, null);
  if (t90) { t90.dispatch("click"); page.paint(); check("W1: горизонт T-90 рисуется без ошибок", !page.errors.length && !/не отрисовал/.test(page.text()), page.errors.slice(0, 2)); }
  text = at("capital");
  check("W1: Н1.0 и Н1.2 — подписями capital.titles", text.includes(`${d.capital.titles.n1_0} —`) && text.includes(`${d.capital.titles.n1_2} —`), text.match(/.{0,120}в оценку не входит.{0,20}/));
  check("W1: условие капитала — подписью норматива, не ключом", text.includes(`Условие капитала: ${d.capital.titles.n20} после выплаты`) && !text.includes("n20_0"), text.match(/.{0,40}Условие капитала.{0,80}/));
  check("W1: реестр — два источника", text.includes("T-Invest + Интерфакс") && text.includes("два источника"), text.match(/.{0,80}Источники.{0,200}/));
  check("W1: ввод избытка ε — линейно за ramp_years", text.includes(`ввод линейно за ${d.dividends.policy.excess.ramp_years} года`), text.match(/.{0,80}Выплата избытка.{0,80}/));
  check("W1: CIR по годам — упр. базис", text.includes("CIR упр."), null);
  check("имя норматива в тексте выпуска — не дробь («Н20.0», не «Н20,0»)", text.includes("Н20.0 после выплаты") && !/Н\d+,\d/.test(text), text.match(/.{0,40}Н\d+[.,]\d.{0,40}/));
  text = at("book");
  check("№ 1: все оси книги в полосе — «вне полосы» у суждений нет", !/вне полосы/i.test(squash(visibleText(cardOf(page, "Суждения книги по цене ошибки")))), text.match(/.{0,80}не полосы.{0,80}/));
  check("W1: клетки сработавшего гейта — словами выпуска", text.includes("Клетки (первые 10 из 22)") && text.includes(`N · ${d.regimes.rows.soft.title} · ${d.capital.scenarios.schedule.title}`), text.match(/.{0,60}Клетки.{0,200}/));
  check("W1: экраны без ошибок", !page.errors.length, page.errors.slice(0, 2));
}

{
  const d = sample();
  d.nii.transmission.pairs[1].value = 0.31;
  d.nii.transmission.pairs[1].inside = false;
  d.checks.flags.find((f) => f.name === "ni_jump").raised = true;
  d.checks.flags.find((f) => f.name === "ni_jump").detail = "2027Q1: +31 % г/г при ключевой 14 → 14 %";
  d.live.degraded_flag = true;
  d.live.degraded = ["cbr: ставки вкладов: https://example.org/statistics/avg/?From=27.08.2025&To=01.10.2026: HTTP 403"];
  d.next_report = { ...d.next_report, status: "not_computed", reason: "быстрая сборка", cor_table: [], nim_table: [] };
  d.reverse_dcf.rows[0] = { ...d.reverse_dcf.rows[0], status: "not_computed", solved: null, reason: "быстрая сборка" };
  const page = makePage({ payload: JSON.stringify(d), now: FRESH, width: 343 });
  await page.boot();
  await settle();
  let text = page.text();
  check("W1: флаг ni_jump — плашка с подробностью", text.includes("Прибыль квартала г/г прыгает при неизменной ключевой.") && text.includes("+31 %"), text.slice(0, 900));
  check("W1: причина деградации — без адреса запроса", text.includes("example.org: HTTP 403") && !text.includes("From=27"), text.slice(0, 900));
  page.hashchange("#model");
  check("W1: пара вне коридора — отметкой", page.text().includes("вне коридора"), null);
  page.hashchange("#report");
  check("W1: «что даст отчёт» в быстрой сборке — причина", page.text().includes("Таблицы не считались: быстрая сборка."), page.text().match(/.{0,80}Что даст отчёт.{0,120}/));
  page.hashchange("#market");
  check("W1: ось обратного расчёта без поиска — «не считалось»", page.text().includes("не считалось"), null);
  check("W1: частные случаи без ошибок", !page.errors.length && !/не отрисовал/.test(page.text()), page.errors.slice(0, 2));
}

/* ── 7. волна W2 (AUDIT-1): печать по П§2, слова вместо кодов ── */

{
  const d = sample();
  const page = makePage({ now: FRESH });
  await page.boot();
  await settle();
  const at = (name) => { page.hashchange(`#${name}`); return page.text(); };
  const ru = (t) => squash(page.get(`ruText(${JSON.stringify(t)})`));

  // № 61: ruText — дроби с запятой, версии и ссылки как есть
  for (const [src, want] of [["рост 1.5 %", "рост 1,5 %"], ["книга 1.1", "книга 1.1"], ["книга 1.1 → 1.2; факты те же", "книга 1.1 → 1.2; факты те же"],
    ["book-1.6", "book-1.6"], ["М§4.11 и DESIGN §3.3", "М§4.11 и DESIGN §3.3"], ["(М§14.4)", "(М§14.4)"], ["п. 6.4: срок 3 года", "п. 6.4: срок 3 года"],
    ["Н20.0 не ниже 13.30%", "Н20.0 не ниже 13,30 %"], ["уравнение ni-1.0", "уравнение ni-1.0"], ["эмпирика 0.056–0.085", "эмпирика 0,056–0,085"],
    ["2026-10-28 и 28.10.2026", "28.10.2026 и 28.10.2026"], ["Форма 0409102 за 2026M09", "Форма 0409102 за сентябрь 2026"], ["версия 1.2.3", "версия 1.2.3"],
    ["CoR 1.42 % против 1.4 %", "CoR 1,42 % против 1,4 %"], ["сдвиг -0.5 п.п.", "сдвиг −0,5 п.п."]]) {
    check(`№ 61: ruText «${src}» → «${want}»`, ru(src) === want, ru(src));
  }

  let text = at("overview");
  const head = d.fair_value.headline;
  check("№ 63: число осей полосы — строки суждений in_band", text.includes(`${head.draws / 1000 | 0} 000 прогонов по ${d.judgements.rows.filter((j) => j.in_band).length} суждениям книги`), text.match(/.{0,40}прогонов по.{0,60}/));
  check("№ 65: «точка выше медианы» — оси с наибольшим сдвигом положения, в рублях с пометкой", /Сильнее всего прогоны вниз от точки уводят ЧПМ сквозь цикл \(−3,2 ₽\) и CoR сквозь цикл \(−2,0 ₽\) — вклад в «среднее прогонов − точка», первый порядок\./.test(text), text.match(/.{0,60}Точка выше медианы.{0,260}/));
  check("№ 66: запас норматива — до порога дивидендной политики", text.includes(`запас ${d.capital.titles.n20} до порога дивидендной политики`) && !text.includes("до требования"), text.match(/.{0,40}запас Н.{0,80}/));
  check("№ 67: DPS по прибыли с нау-кастом подписан", text.includes("DPS за 2026 с нау-кастом, интервал"), text.match(/.{0,40}DPS за 2026.{0,60}/));
  check("№ 68: капитализация и капитал — числами", text.includes(`(капитализация ${squash(page.get(`fmt.num(${d.market.cap}, 0)`))} минус капитал ${squash(page.get(`fmt.num(${d.fair_value.bank_first_line.bv_v}, 0)`))})`), text.match(/.{0,40}капитализация.{0,60}/));
  const ni = d.next_report.expectation.ni, eq = d.next_report.neutral.cor.ni_equivalent;
  check("№ 16: эквивалент — уровень ЧП квартала рядом с ожиданием, не чувствительность", text.includes(`при ней прибыль квартала ≈ ${squash(page.get(`fmt.bn(${eq}, 1)`))} (ожидание модели — ${squash(page.get(`fmt.num(${ni}, 1)`))})`)
    && !/прибыли квартала на 0,1/.test(text), text.match(/.{0,80}при ней.{0,120}/));
  check("№ 22: наклон — местный, рядом размах реакции", text.includes("у нейтрального значения — около 6,7 ₽ на 0,1 п.п., дальше реакция упирается в предел сдвига режимов: от −19,9 до +14,6 ₽"), text.match(/.{0,40}около.{0,160}/));
  check("№ 28: пояснение строки разложения; прогулки по ключам нет", text.includes("входит в строку «Суждения книги»") && !text.includes("Крупнейшие ключи книги"), text.match(/.{0,80}Суждения книги.{0,200}/));
  // № 64: подпись «сейчас …» не закрывает точки мини-графика CoR
  const teaser = cardOf(page, "Ближайший отчёт");
  const svg = findNode(teaser, (n) => n.tagName === "SVG" && (n.attrs["aria-label"] || "").startsWith("Медиана в зависимости"));
  const now = findNode(svg, (n) => n.tagName === "TEXT" && n.textContent.startsWith("сейчас"));
  const dots = [];
  const walk = (n) => { if (n.nodeType === 1) { if (n.tagName === "CIRCLE") dots.push([+n.attrs.cx, +n.attrs.cy]); n.children.forEach(walk); } };
  walk(svg);
  const box = now && { x0: +now.attrs.x, x1: +now.attrs.x + now.textContent.length * 7, y0: +now.attrs.y - 13, y1: +now.attrs.y + 3 };
  check("№ 64: подпись «сейчас» не закрывает точки графика", !!now && dots.length > 5 && !dots.some(([cx, cy]) => cx > box.x0 - 4 && cx < box.x1 + 4 && cy > box.y0 - 4 && cy < box.y1 + 4), [box, dots.slice(0, 3)]);

  text = at("market");
  check("№ 27: оговорка метода обратного расчёта", text.includes("решения разных строк не перемножаются") && text.includes("Строки решаются по одной"), null);
  check("№ 64: «недостижимо» — один раз на строку", !/недостижимо\s+недостижимо/.test(text) && text.includes("недостижимо"), text.match(/.{0,60}недостижимо.{0,60}/));
  const subject = d.market.peers.rows[0];
  check("№ 64: аналоги — дата отчётности из строки, снимок — отдельно", text.includes(`отчётность на ${page.get(`fmt.date("${subject.bv_date}")`)}; снимок аналогов ${page.get(`fmt.date("${d.market.peers.as_of}")`)}`)
    && text.includes("отчётность на 31.03.2026"), text.match(/.{0,40}Банки-аналоги.{0,160}/));

  text = at("model");
  check("№ 17: строки сектора — подписью, «лучше сектора» не сравнивается", text.includes("прогноз сектора; банк — лучше сектора") && text.includes("не сравнивается")
    && text.includes("прогноз сектора; банк — в соответствии с сектором") && text.includes("в прогнозе сектора") && text.includes("гейт их не берёт"), text.match(/.{0,60}Рост кредитов.{0,300}/));
  check("№ 62: базисы — словами (норматив ЦБ, прогноз сектора)", text.includes("норматив ЦБ") && !/\bregulatory\b|\bsector\b/.test(text), text.match(/.{0,40}regulatory|sector.{0,40}/));
  check("№ 64: «Смесь заголовка» — без повтора в скобках", text.includes("Смесь заголовка (λ книги); строки — % средних активов") && !/Смесь заголовка \(смесь/.test(text), text.match(/.{0,40}средних активов.{0,40}/));
  check("№ 62: «п.п..» нет", !/п\.п\.\./.test(text), text.match(/.{0,60}п\.п\.\..{0,20}/));
  check("В8: сдвиг спредов активов и пассивов, эффективный LT-спред с полом", text.includes("сдвиг LT-спредов σ0: активы / пассивы; на активах — 80 % сдвига") && text.includes("−0,11 / +0,03 п.п.")
    && text.includes("+0,63 / +0,55 / +0,51") && text.includes("пол спреда 0,50 п.п.") && text.includes("опора — ОФЗ 1 г.") && !/ofz_/.test(text), text.match(/.{0,60}Корпоративные кредиты.{0,200}/));
  check("№ 14: флаги клеток — «урезан капиталом» и «отменена» раздельно", text.includes("дивиденд урезан капиталом") && text.includes("выплата года кризиса отменена"), null);

  text = at("report");
  check("№ 60: дата месячной плитки — период словами", text.includes("август 2026 · РСБУ банка") && !/\d{4}M\d{2}/.test(text), text.match(/.{0,60}Чистая прибыль, месяц.{0,120}/));
  check("№ 64: «15 рядов» — по числу", text.includes(`${d.indicators.tiles.length} рядов`), text.match(/.{0,20}Опережающие индикаторы.{0,60}/));
  check("№ 44: консенсус — «у отчёта»", text.includes("Консенсус у отчёта (за день до выхода)"), text.match(/.{0,200}Эталоны на истории.{0,500}/));
  const t90 = findNode(page.app, (n) => n.tagName === "BUTTON" && n.textContent === "T-90");
  t90.dispatch("click");
  page.paint();
  check("№ 44: на горизонте T-90 консенсуса нет", !squash(visibleText(cardOf(page, "Эталоны на истории"))).includes("онсенсус"), squash(visibleText(cardOf(page, "Эталоны на истории"))).slice(0, 600));
  const nim = findNode(page.app, (n) => n.tagName === "BUTTON" && n.textContent === "ЧПМ" && n.parentNode.attrs["aria-label"] === "Показатель отчёта");
  nim.dispatch("click");
  page.paint();
  text = page.text();
  check("№ 19: ЧПМ режимы не учит, пока ожидания равны; второе наблюдаемое — не наблюдалось", text.includes("Ожидания ЧПМ у режимов равны: такой факт вероятностей режимов не меняет")
    && text.includes("второй показатель в ней считается ненаблюдавшимся"), text.match(/.{0,60}Ожидания ЧПМ.{0,200}/));
  check("№ 16: нейтральная ЧПМ — уровень ЧП = ожиданию", text.includes(`Нейтральная ЧПМ 3 кв. 2026 (упр.) — 5,97 %`) && text.includes(`при ней прибыль квартала ≈ ${squash(page.get(`fmt.bn(${ni}, 1)`))} (ожидание модели`), text.match(/.{0,40}Нейтральная ЧПМ.{0,300}/));

  text = at("capital");
  check("№ 66: «Капитал на дату» — порог политики и регуляторный пол года", text.includes("порог дивидендной политики — требование года") && text.includes("запас до порога дивидендной политики")
    && text.includes("регуляторный пол года (ожидание по сценариям)"), text.match(/.{0,200}запас до.{0,160}/));
  check("№ 33, № 97: срок политики — датой; запись о сроке — предложением, без приставки «Срок:»", text.includes("действует по 05.12.2026") && text.includes("П. 6.4 Положения: срок — 3 года")
    && !text.includes("Срок: п.") && !text.includes("действует по —"), text.match(/.{0,80}действует по.{0,60}/));
  check("№ 62: коды политики — словами", text.includes("База — ЧП группы по МСФО, приходящаяся на акционеров; делитель — размещённые акции обеих категорий; округление — до копейки вверх; нехватка капитала — остаток сверх требований")
    && text.includes("догоняющая выплата — да") && !/\b[a-z]+_[a-z_]+\b|\btrue\b/.test(text), text.match(/.{0,40}База —.{0,300}/));
  check("№ 59: капитальный разрыв — число клеток и клетки словами", d.capital.capital_gap.cells === 4 && text.includes(`(4 клетки: H · ${d.regimes.rows.crisis.title} · ${d.capital.scenarios.upper.title}; H · ${d.regimes.rows.crisis.title} · ${d.capital.scenarios.strict.title}; M · `), text.match(/.{0,40}Капитальный разрыв.{0,200}/));
  const annual = cardOf(page, "По годам");
  check("reintegrate: таблица «По годам» — плотная (влезает в карточку на 1280)", !!findNode(annual, (n) => n.tagName === "TABLE" && /\bdense\b/.test(n.attrs.class || "")), null);
  const table = findNode(page.app, (n) => n.tagName === "BUTTON" && n.textContent === "Таблица" && squash(visibleText(cardOf(page, "Дивиденды по году прибыли"))).length > 0 && cardOf(page, "Дивиденды по году прибыли").contains(n));
  table.dispatch("click");
  check("№ 14: дивиденды по годам — «урезано капиталом» и «без выплаты» раздельно", page.text().includes("Урезано капиталом") && page.text().includes("Без выплаты"), null);

  text = at("book");
  const cm = squash(visibleText(cardOf(page, "Сверка с контрольной моделью")));
  check("№ 15: относительная разность — в процентах", cm.includes("−0,27 %") && cm.includes("3,0 %"), cm.slice(0, 700));
  check("№ 15: абсолютная разность — в единицах строки (|diff| > 1 — не проценты)", cm.includes("−30,0 млрд ₽") && cm.includes("584,5 млрд ₽") && !/2 997|58 448/.test(cm), cm.slice(0, 1200));
  check("№ 15: доли — в п.п., малые допуски — степенью, пол допуска — «или»", cm.includes("−0,03 п.п.") && cm.includes("0,30 п.п.") && cm.includes("1·10⁻⁶") && cm.includes("10,0 % или 44,3 млрд ₽") && cm.includes("или 2,05 ₽"), cm.slice(0, 1500));
  check("№ 58: источник суждения — словами книги", text.includes("линия 5,4 % + 0,037 × ключевая сквозь цикл") && text.includes("книга 1.3, ось „Кризис: сдвиг ЧПМ 2027“") && !/valuation\.uncertainty/.test(text), null);
  check("№ 1: сдвиг положения оси — столбцом", text.includes("Сдвиг положения, ₽") && text.includes("−3,2"), text.match(/.{0,40}Сдвиг положения.{0,200}/));
  check("№ 68: строка фактов — период словами", text.includes("Факты якоря") && text.includes("2 кв. 2026") && !/2026Q2/.test(text), text.match(/.{0,40}Факты якоря.{0,80}/));
  check("reintegrate: κ — число, не проценты", /κ: CoR к реальной ставке сверх мира N 0,05 0 … 0,15/.test(text), text.match(/.{0,20}κ: CoR.{0,80}/));
  check("№ 61: версия книги на экране — «1.3», не «1,3»", text.includes("книга 1.3") && !/1,3, ось|книга 1,\d/.test(text), text.match(/.{0,40}книга 1[.,]\d.{0,40}/));
  check("W2: экраны без ошибок", !page.errors.length, page.errors.slice(0, 2));
}

{
  // Частные случаи W2: ось вне полосы, требование ≠ порогу политики, срок политики не задан, разные ожидания ЧПМ,
  // наклон у ожидания, метка клетки и код периода в тексте ядра, одна P(ниже рынка) у двух тикеров.
  const d = sample();
  const j = d.judgements.rows.find((r) => r.id === "epsilon");
  Object.assign(j, { in_band: false, share: null, rank_corr: null });
  d.fair_value.headline.contributions = d.fair_value.headline.contributions.filter((c) => c.judgement_key !== "epsilon");
  d.judgements.off_band_shift = { rub: j.mean_shift, limit: 2.5, axes: 1 };
  d.capital.anchor.req20_now = 0.113;
  d.dividends.policy.valid_until = null;
  d.dividends.policy.valid_until_note = "срок действия в Положении не указан";
  d.next_report.expectation.by_regime[0].nim_mgmt = 0.0602;
  d.next_report.slope.at_cor = "expectation";
  d.next_report.nim_table = d.next_report.nim_table.slice(0, 4);
  d.checks.flags.find((f) => f.name === "ni_jump").raised = true;
  d.checks.flags.find((f) => f.name === "ni_jump").detail = "H/norm/schedule 2027Q2: ЧП г/г +25% при ключевой 14.62% → 14.00%";
  for (const row of [d.fair_value.headline, ...d.fair_value.headline.by_lambda]) { row.p_below_market = 0.035; row.p_below_by_ticker = { SBER: 0.035, SBERP: 0.035 }; }
  d.fair_value.headline.by_lambda[20].p_below_market = 0.001;
  d.fair_value.headline.by_lambda[20].p_below_by_ticker = { SBER: 0.001, SBERP: 0.001 };
  const page = makePage({ payload: JSON.stringify(d), now: FRESH });
  await page.boot();
  await settle();
  let text = page.text();
  check("reintegrate: плашка ni_jump — клетка и квартал словами", text.includes(`H · ${d.regimes.rows.norm.title} · ${d.capital.scenarios.schedule.title} 2 кв. 2027: ЧП г/г +25 % при ключевой 14,62 % → 14,00 %`) && !/H\/norm|2027Q2/.test(text), text.slice(0, 500));
  check("№ 1: ось вне полосы — той же мерой, что правило (сдвиг заголовка и порог)", text.includes(`Вне полосы (их фиксация сдвигает заголовок на ${squash(page.get(`fmt.signedRub(${j.mean_shift}, 1)`))} при пороге 2,5 ₽): выплата избытка ε с 2030.`), text.match(/.{0,40}Вне полосы.{0,200}/));
  check("№ 63: одна вероятность — одна печать у обоих тикеров", text.includes("4 %вероятность") && text.includes("SBERP: 4 %") && !text.includes("SBERP: 3,5 %"), text.match(/.{0,20}вероятность, что.{0,220}/));
  check("№ 66: требование не равно порогу политики — «пол + буфер»", text.includes("до требования дивиденда (пол + буфер)"), text.match(/.{0,40}запас Н.{0,80}/));
  check("№ 22: наклон снят в ожидании — так и сказано", text.includes("у ожидания — около 6,7 ₽ на 0,1 п.п."), text.match(/.{0,40}около.{0,80}/));
  const slider = findNode(page.app, (n) => n.tagName === "INPUT" && n.attrs.id === "lambda");
  slider.value = "1";
  slider.dispatch("input");
  page.paint();
  text = page.text();
  check("№ 63: меньше 1 % — два знака у обоих тикеров", text.includes("0,10 %вероятность") && text.includes("SBERP: 0,10 %"), text.match(/.{0,20}вероятность, что.{0,220}/));
  check("№ 65: при λ ≠ книги — пометка приближения", text.includes("первый порядок, при λ книги."), text.match(/.{0,60}первый порядок.{0,40}/));
  page.hashchange("#report");
  const nim = findNode(page.app, (n) => n.tagName === "BUTTON" && n.textContent === "ЧПМ" && n.parentNode.attrs["aria-label"] === "Показатель отчёта");
  nim.dispatch("click");
  page.paint();
  text = page.text();
  check("№ 19: ожидания ЧПМ режимов разные — подписи о равенстве нет", !text.includes("Ожидания ЧПМ у режимов равны"), null);
  const impact = cardOf(page, "Что даст отчёт");
  const titles = tipsOf(page, impact, "CIRCLE").map((t) => t.title);
  check("№ 20: ось графика накрывает эталоны и нау-каст за краем таблицы", titles.includes("гайденс года") && titles.includes("прошлый отчётный квартал"), titles);
  page.hashchange("#capital");
  text = squash(visibleText(cardOf(page, "Дивидендная политика")));
  check("№ 33: срок политики не задан — причина словами, не «действует по —»", text.includes("срок действия в Положении не указан") && !text.includes("действует по"), text.match(/.{0,60}Дивидендная политика.{0,160}/));
  page.hashchange("#book");
  text = page.text();
  check("№ 1: «вне полосы» на «Допущениях» с мерой сдвига", text.includes("вне полосы") && text.includes("Вне полосы — 1: их фиксация сдвигает заголовок"), text.match(/.{0,60}Вне полосы.{0,160}/));
  check("W2: частные случаи без ошибок", !page.errors.length && !/не отрисовал/.test(page.text()), page.errors.slice(0, 2));
}

/* ── 8. волна W3 (AUDIT-2): тексты, диапазоны, легенды, нули и провалы, новые поля П§2 ── */

// Запись легенды ↔ нарисованный элемент той же карточки: цвет ключа — заливка или обводка, пунктирный ключ — пунктирная линия,
// сплошной ключ-линия — линия без пунктира, ключ-точка — кружок; у HTML-полос — класс полосы.
const KEY_COLORS = { "key-model": ["var(--model)"], "key-model2": ["var(--model)"], "key-market": ["var(--market)"], "key-market2": ["var(--market)"], "key-third": ["var(--third)"],
  "key-ink": ["var(--ink)"], "key-neg": ["var(--neg)"], "key-neg2": ["var(--neg)"], "key-neg3": ["var(--neg)"], "key-neutral": ["var(--series-neutral)"],
  "key-b80": ["var(--model-wash-1)", "var(--model-wash-2)"], "key-b50": ["var(--model-wash-2)", "var(--model-wash-3)"], "key-dash": ["var(--ink-2)"],
  "key-band": ["var(--market-wash)"], "key-diamond": ["var(--ink)"] };
const DASHED_KEYS = new Set(["key-model2", "key-market2", "key-neg2", "key-neg3", "key-dash"]);
const BAR_CLASS = { "key-neg": "is-down", "key-model": "is-up" };
function legendOrphans(page) {
  const out = [];
  const cards = [];
  const collect = (n) => { if (n.nodeType !== 1) return; if (n.tagName === "SECTION") cards.push(n); n.children.forEach(collect); };
  collect(page.app);
  for (const card of cards) {
    const keys = [], marks = [];
    const walk = (n, inLegend) => {
      if (n.nodeType !== 1 || n.hidden) return;
      const cl = (n.attrs.class || "").split(" ");
      if (n.tagName === "I" && inLegend && cl.includes("key")) keys.push({ cl, name: squash(visibleText(n.parentNode)) });
      else if (["PATH", "LINE", "CIRCLE", "RECT", "POLYLINE"].includes(n.tagName) || cl.includes("bt-bar")) marks.push(n);
      n.children.forEach((c) => walk(c, inLegend || cl.includes("legend")));
    };
    walk(card, false);
    for (const { cl, name } of keys) {
      const key = cl.find((c) => KEY_COLORS[c]);
      if (!key) { out.push(`${name}: ключ без цвета (${cl.join(" ")})`); continue; }
      const dashed = DASHED_KEYS.has(key), solidLine = cl.includes("key-line") && !dashed, dot = cl.includes("key-dot");
      const hit = marks.some((m) => {
        if ((m.attrs.class || "").split(" ").includes("bt-bar")) return !!BAR_CLASS[key] && m.attrs.class.split(" ").includes(BAR_CLASS[key]);
        const colour = KEY_COLORS[key].includes(m.attrs.stroke) || KEY_COLORS[key].includes(m.attrs.fill);
        if (!colour) return false;
        if (dot) return m.tagName === "CIRCLE" && KEY_COLORS[key].includes(m.attrs.fill);
        if (dashed) return !!m.attrs["stroke-dasharray"] && KEY_COLORS[key].includes(m.attrs.stroke);
        if (solidLine) return !m.attrs["stroke-dasharray"] && KEY_COLORS[key].includes(m.attrs.stroke);
        return true;
      });
      if (!hit) out.push(`${squash(visibleText(card)).slice(0, 40)}: «${name}» (${key})`);
    }
  }
  return out;
}
const svgTexts = (root) => { const out = []; const walk = (n) => { if (n.nodeType === 1) { if (n.tagName === "TEXT") out.push(n.textContent); n.children.forEach(walk); } }; walk(root); return out; };
const countNodes = (root, pred) => { let k = 0; const walk = (n) => { if (n.nodeType === 1) { if (pred(n)) k += 1; n.children.forEach(walk); } }; walk(root); return k; };
const press = (page, label, group) => {
  const b = findNode(page.app, (n) => n.tagName === "BUTTON" && n.textContent === label && (!group || n.parentNode.attrs["aria-label"] === group));
  if (b) { b.dispatch("click"); page.paint(); }
  return !!b;
};

{
  const d = sample();
  const page = makePage({ now: FRESH });
  await page.boot();
  await settle();
  const at = (name) => { page.hashchange(`#${name}`); return page.text(); };
  const orphans = [];
  for (const name of SCREEN_NAMES) { at(name); orphans.push(...legendOrphans(page).map((x) => `${name}: ${x}`)); }
  check("№ 103: каждая запись легенды соответствует нарисованному элементу своей карточки", orphans.length === 0, orphans.slice(0, 8));

  let text = at("overview");
  const bfl = d.fair_value.bank_first_line;
  const whole = (v) => squash(page.get(`fmt.signedRub(${v})`));
  check("№ 81: чувствительности языка банка — целые рубли", text.includes(`0,1 п.п. CoR — ${whole(bfl.rub_per_01pp_cor)}, 0,1 п.п. ЧПМ — ${whole(bfl.rub_per_01pp_nim)}.`)
    && text.includes(`Цена 1 п.п. ROE сквозь цикл${squash(page.get(`fmt.num(${bfl.rub_per_1pp_roe})`))}₽ на акцию`), text.match(/.{0,40}Цена 1 п\.п\. ROE.{0,200}/));
  check("В17: истекающее объяснение гейта — плашка, не тревога", text.includes("Срок объяснения проверки истекает. «Путь года вне гайденса» — объяснение действует по 28.10.2026.")
    && page.get('document.querySelector("#release-chip").dataset.state') === "ok", text.slice(0, 300));
  check("№ 100: дата формы ЦБ — окном оценки, не точной датой", text.includes("≈ 24 окт.") && text.includes("окно 24 окт.–26 окт.") && !/(?<!≈ )24 окт\.через/.test(text), text.match(/.{0,60}Форма 0409102.{0,80}/));
  check("№ 82: год нау-каста — на слое смеси заголовка", text.includes("прибыль 2026: факт + смесь заголовка с поправкой нау-каста") && /DPS за 2026 с нау-кастом, интервал \d+,\d–\d+,\d ₽/.test(text), text.match(/.{0,60}DPS за 2026.{0,80}/));

  text = at("market");
  check("№ 101, §4.3: строка обратного расчёта без оси — без диапазона «0 % … 0 %»", text.includes("книга 0 % · оси с диапазоном в книге нет") && !/0 % … 0 %/.test(text), text.match(/.{0,40}Регуляторный сценарий.{0,160}/));
  check("№ 101: торнадо — у оси-словаря ключ с наибольшим ходом, а не название типа", text.includes(`${d.regimes.rows.crisis.title} 10,0 → 25,0 % · …`) && !text.includes("словарь долей"), text.match(/.{0,60}Вероятности режимов.{0,160}/));
  check("№ 103: ключ «медиана модели» — пунктирный, как линия", !!findNode(cardOf(page, "Цена акций за 12 месяцев"), (n) => n.tagName === "I" && /key-model2/.test(n.attrs.class || "")), null);

  text = at("model");
  check("№ 102: базы терминала названы; «оценка капитала», а не «стоимость капитала V0»", text.includes("PV терминала RI") && text.includes("Доля терминала DDM") && !text.includes("Стоимость капитала V0"), text.match(/.{0,60}PV терминала.{0,80}/));
  check("№ 102: пересмотры гайденса — только смены значения", text.includes("Пересмотры гайденса · 3") && text.includes("29.04.2026 — ЧПМ: 5,90 % → 6,20 % (МСФО 1К26 (29.04.2026))")
    && !/Пересмотры гайденса · 32/.test(text), text.match(/.{0,20}Пересмотры гайденса.{0,300}/));
  const books = cardOf(page, "Книги ЧПД и передача ставки");
  const tips = [];
  const walkTh = (n) => { if (n.nodeType === 1) { if (n.tagName === "SPAN" && n.parentNode.tagName === "TH") { page.ctx.__n = n; tips.push(page.get("TIPS.get(globalThis.__n)")); } n.children.forEach(walkTh); } };
  walkTh(books);
  check("№ 102: подсказки к ρ и β", tips.some((x) => /доля разрыва ставки книги к цели/.test(x || "")) && tips.some((x) => /передача ставки опоры книги в стоимость пассива/.test(x || "")), tips);
  const bt = squash(visibleText(books));
  check("№ 103: плитки с «—» нет — причина строкой", !/—\s*раскрытая чувствительность/.test(bt) && bt.includes("Раскрытой чувствительности ЧПД к ±100 б.п. нет: не раскрыто в фактах книги."), bt.match(/.{0,80}аскрыт.{0,160}/));
  check("В12: сжатие φ — по сторонам баланса, кредитная маржа миров", bt.includes("0,290 / 0,179") && bt.includes("на кредитных книгах — 60 % сжатия φ = 0,484") && bt.includes("Кредитная маржа, п.п.") && bt.includes("3,12")
    && !bt.includes("рост кредита стоимость разрушает"), bt.match(/.{0,40}сжатие φ.{0,200}/));
  check("В15: пол вероятности режима — в подписи фильтра", text.includes("вероятность режима после наблюдений — не ниже 33 % книжной"), text.match(/.{0,60}Фильтр читает.{0,160}/));
  check("reintegrate: запись миров — версия записи семейства, не имя репозитория", text.includes("Миры: запись семейства 850, версия 1.6, кривые 18.09.2026.") && !/worlds-850|book-1\.6/.test(text), text.match(/.{0,40}Цена мира — оценка.{0,160}/));

  text = at("report");
  check("№ 73: «Уравнение словами», без номера версии", text.includes("Уравнение словами") && !/Уравнение [a-z]/.test(text), text.match(/.{0,30}Уравнение.{0,60}/));
  const now = cardOf(page, "Нау-каст");
  const legendOf = (card) => { const out = []; const walk = (n) => { if (n.nodeType === 1) { if ((n.attrs.class || "").split(" ").includes("legend")) out.push(squash(visibleText(n))); n.children.forEach(walk); } }; walk(card); return out.join(" | "); };
  check("№ 103: эталоны полосы нау-каста — из журнала, в пределах оси", legendOf(now).includes("эталоны") && tipsOf(page, now, "CIRCLE").some((x) => /Эталон: РСБУ квартала/.test(x.title))
    && !tipsOf(page, now, "CIRCLE").some((x) => /год назад/.test(x.title)), [legendOf(now), tipsOf(page, now, "CIRCLE").map((x) => x.title)]);
  press(page, "CoR квартала", "Цель нау-каста");
  check("№ 103: нет интервала — нет и записи легенды", !legendOf(cardOf(page, "Нау-каст")).includes("интервал нау-каста") && legendOf(cardOf(page, "Нау-каст")).includes("ожидание модели"), legendOf(cardOf(page, "Нау-каст")));
  const impact = cardOf(page, "Что даст отчёт");
  check("№ 103: точка прогноза на «Что даст отчёт» — и без интервала", tipsOf(page, impact, "CIRCLE").some((x) => x.title === "Нау-каст квартала") && legendOf(impact).includes("нау-каст")
    && !legendOf(impact).includes("интервал нау-каста") && svgTexts(impact).some((x) => /^нау-каст 1,25 %/.test(squash(x))), [legendOf(impact), svgTexts(impact).slice(-3)]);
  check("№ 82: год — на одном слое; DPS без нау-каста — на прибыли смеси заголовка", text.includes("DPS политики на прибыли смеси заголовка (без нау-каста)")
    && text.includes("Открытый квартал: смесь заголовка и отклонение нау-каста") && Math.abs(d.nowcast.year.dps - d.nowcast.year.dps_model - d.nowcast.quarter.by_target.ni_q.deviation * 500 / d.meta.shares.issued_mln) < 2e-4,
  text.match(/.{0,60}DPS политики.{0,80}/));
  const tiles = squash(visibleText(cardOf(page, "Опережающие индикаторы")));
  check("№ 98: плитки — в единицах кодов П§0.2; нормативы банка — «норматив ЦБ»", /Собственные средства банка, форма 0409123 \d \d{3} млрд ₽/.test(tiles) && /Н1\.0 банка\d+,\d\d %август 2026 · норматив ЦБ/.test(tiles) && /Медиана целей брокеров \d+,\d\d ₽/.test(tiles)
    && /Чистая прибыль, месяц\d+,\d млрд ₽/.test(tiles), tiles.slice(0, 600));
  check("№ 100: событие формы ЦБ в календаре — окном", squash(visibleText(cardOf(page, "События"))).includes("окно 24 окт.–26 окт."), null);

  text = at("capital");
  const path = cardOf(page, "Путь нормативов");
  check("№ 103: полы нормативов — свои ключи и подписи", legendOf(path).includes(`пол ${d.capital.titles.n20}`) && legendOf(path).includes(`пол ${d.capital.titles.n11}`)
    && !!findNode(path, (n) => n.tagName === "I" && /key-neg2/.test(n.attrs.class || "")) && !!findNode(path, (n) => n.tagName === "I" && /key-neg3/.test(n.attrs.class || ""))
    && countNodes(path, (n) => n.tagName === "PATH" && n.attrs.stroke === "var(--neg)" && n.attrs["stroke-dasharray"] === "1 4") === 1, legendOf(path));
  const wide = makePage({ now: FRESH, width: 1100 });
  await wide.boot();
  await settle();
  wide.hashchange("#capital");
  const hist = cardOf(wide, "Дивиденды по году прибыли");
  const zero = d.dividends.history.find((r) => r.dps === 0), small = d.dividends.history.find((r) => r.dps > 0 && r.dps < 1);
  check("№ 105: нулевой дивиденд — подпись «0» и цель подсказки; меньше рубля — с копейками", svgTexts(hist).includes("0") && svgTexts(hist).includes(squash(page.get(`fmt.num(${small.dps}, 2)`)))
    && tipsOf(wide, hist, "RECT").some((x) => x.title === `${zero.year}: факт` && /0,00/.test(squash(x.rows[0][1]))), [zero, small, svgTexts(hist).slice(0, 12)]);
  check("№ 83: «По годам» — столбцы FVC и разовых статей", squash(visibleText(cardOf(page, "По годам"))).includes("FVC") && squash(visibleText(cardOf(page, "По годам"))).includes("Разовые")
    && squash(visibleText(cardOf(page, "По годам"))).includes("−60"), null);
  check("№ 97: заголовок ступени политики — с пробелом перед процентом", !/\d%/.test(text), text.match(/.{0,30}\d%.{0,30}/));

  text = at("book");
  check("№ 97: после многоточия точки нет", !/…\./.test(text), text.match(/.{0,40}…\..{0,20}/));
  check("reintegrate: «Миры: запись семейства 850, версия 1.6»", text.includes("Миры: запись семейства 850, версия 1.6 от 21.09.2026, sha256") && !/worlds-850/.test(text), text.match(/.{0,20}Миры:.{0,120}/));
  press(page, `Показать все ${d.judgements.rows.length}`);
  text = page.text();
  check("№ 101: диапазон оси-словаря — пары концов по ключам", text.includes(`${d.worlds.rows.N.name} 25,0 → 40,0 % · ${d.worlds.rows.H.name} 50,0 → 45,0 % · ${d.worlds.rows.M.name} 25,0 → 15,0 %`)
    && text.includes(`${d.regimes.rows.crisis.title} 10,0 → 25,0 %`) && !text.includes("словарь долей"), text.match(/.{0,40}Веса миров.{0,300}/));
  check("№ 99: срок — в годах; κ — числом", /Срок подтягивания FVOCI 4 года 2,5 года … 6 лет/.test(text) && /κ: CoR к реальной ставке сверх мира N 0,05 0 … 0,15/.test(text), text.match(/.{0,20}Срок подтягивания.{0,80}/));
  check("№ 97: сообщение гейта — предложением с заглавной", text.includes("Путь 2026 года вне гайденса (рост комиссий: 1,2 % против гайденса 0,0 %).") && text.includes("Норматив ниже пола в 4 клетках."), text.match(/.{0,40}вне гайденса \(.{0,80}/));
  check("№ 73: малое число инварианта — как допуск, без e-нотации", text.includes("1,8·10⁻¹²") && !/\de-\d/.test(text), text.match(/.{0,60}относительная разность.{0,40}/));
  const history = cardOf(page, "Отчётная история");
  check("№ 84: линия упр. метрик — по непрерывным участкам, через нераскрытое не идёт", countNodes(history, (n) => n.tagName === "PATH" && n.attrs.stroke === "var(--model)") === 2
    && countNodes(history, (n) => n.tagName === "PATH" && n.attrs.stroke === "var(--neg)" && !n.attrs["stroke-dasharray"]) === 2, countNodes(history, (n) => n.tagName === "PATH"));
  check("№ 84: провалы раскрытия — по базисам", squash(visibleText(history)).includes("Провалы раскрытия: МСФО группы, 1 кв. 2021–4 кв. 2022 — потоков МСФО квартала в фактах нет; упр., 4 кв. 2021–4 кв. 2022 — управленческие метрики квартала не раскрывались."),
    squash(visibleText(history)).slice(-260));
  check("W3: экраны без ошибок", !page.errors.length, page.errors.slice(0, 2));
}

{
  // Частные случаи W3: нет ошибки года, срок политики истёк, вход по форме ЦБ, отрицательная кредитная маржа, ось-словарь без конца,
  // раскрытая чувствительность есть, одиночная точка линии, текст ядра без типографики.
  const d = sample();
  d.nowcast.year.ni_year_se = null;
  d.nowcast.year.dps_interval = null;
  d.checks.flags.find((f) => f.name === "policy_expired").raised = true;
  d.calendar.next_ras.enters_via = "form102";
  d.calendar.next_ras.date = d.calendar.next_ras.form102_date_est;
  d.nii.transmission.loan_margin.M = -0.0031;
  d.nii.disclosed = { nii_per_100bp: 18.0, src: "годовой отчёт" };
  delete d.judgements.rows.find((r) => r.id === "regime_prob").high.crisis;
  d.dividends.ladder[0].title = "Доля 50% прибыли";
  d.dividends.ladder[0].condition = "Н20.0 после выплаты не ниже 13.30%";
  d.history.quarters[3].ifrs.cor = 0.011;
  d.guidance.revisions = d.guidance.revisions.filter((r) => r.date === "2025-12-10");
  d.worlds.source.origin = "прежняя запись";
  const page = makePage({ payload: JSON.stringify(d), now: FRESH });
  await page.boot();
  await settle();
  let text = page.text();
  check("№ 82: ошибки года нет — «интервал не оценён», без NaN", text.includes("DPS за 2026 с нау-кастом, интервал не оценён") && !BAD_TEXT.test(text), text.match(/.{0,40}DPS за 2026.{0,80}/));
  check("В17: срок политики истёк — плашка", text.includes("Срок дивидендной политики истёк: действует прежняя до новой."), text.slice(0, 400));
  page.hashchange("#report");
  text = page.text();
  check("№ 82: водопад года без ошибки — словами", text.includes("ошибка года не оценена") && text.includes("интервал не оценён") && !BAD_TEXT.test(text), text.match(/.{0,60}Прибыль акционерам 2026.{0,80}/));
  check("№ 100: вход нау-каста по форме ЦБ — окном дат", text.includes("Раньше МСФО: ≈ 24.10.2026–26.10.2026 — РСБУ банка за сентябрь 2026 (по форме 0409102, дата — оценка)"), text.match(/.{0,20}Раньше МСФО.{0,160}/));
  page.hashchange("#model");
  text = page.text();
  check("В12: отрицательная кредитная маржа мира — с пояснением", text.includes("−0,31") && text.includes("где она отрицательна, рост кредита стоимость разрушает"), text.match(/.{0,60}Кредитная маржа.{0,200}/));
  check("№ 103: раскрытая чувствительность есть — плиткой", text.includes("18 млрд ₽ раскрытая чувствительность ЧПД к ±100 б.п.") && !text.includes("Раскрытой чувствительности"), null);
  check("№ 102: значение гайденса не менялось — так и сказано", text.includes("Гайденс года с 10.12.2025 не пересматривался.") && !text.includes("Пересмотры гайденса"), text.match(/.{0,40}Гайденс года с.{0,60}/));
  check("reintegrate: запись миров без версии — без имени источника", text.includes("Миры: запись семейства 850, кривые") && !text.includes("прежняя запись"), text.match(/.{0,20}Миры:.{0,80}/));
  page.hashchange("#capital");
  text = page.text();
  check("В17: срок политики истёк — в карточке политики", text.includes("срок истёк 05.12.2026: до новой политики действует прежняя") && !text.includes("действует по 05.12.2026"), text.match(/.{0,80}срок истёк.{0,60}/));
  check("№ 97: текст ступени политики — с пробелом и запятой в процентах", text.includes("Доля 50 % прибыли") && text.includes("не ниже 13,30 %"), text.match(/.{0,20}Доля 50.{0,80}/));
  page.hashchange("#book");
  press(page, `Показать все ${d.judgements.rows.length}`);
  text = page.text();
  check("№ 101: у оси-словаря нет конца — «—»", text.includes(`${d.regimes.rows.crisis.title} 10,0 % → —`), text.match(/.{0,80}Кризис 10,0.{0,40}/));
  check("№ 84: одиночная точка линии без маркеров — точкой", countNodes(cardOf(page, "Отчётная история"), (n) => n.tagName === "CIRCLE" && n.attrs.fill === "var(--neg)" && n.attrs.r === "2.5") === 1, null);
  check("W3: частные случаи без ошибок", !page.errors.length && !/не отрисовал/.test(page.text()), page.errors.slice(0, 2));
}

/* ── 8б. печать по проверке W3 (решение P7): падеж после дробного, счётчики проверок, сверка по виду допуска, источник истории дивидендов ── */

{
  const d = sample();
  const page = makePage({ now: FRESH });
  await page.boot();
  await settle();
  const at = (name) => { page.hashchange(`#${name}`); return page.text(); };
  const years = (v) => squash(page.get(`formatByUnit(${v}, "years")`));
  check("P7: после дробного числа — «года»; после целого — по числу", years(1.6) === "1,6 года" && years(2.4) === "2,4 года" && years(0.5) === "0,5 года"
    && years(1) === "1 год" && years(2) === "2 года" && years(5) === "5 лет" && years(11) === "11 лет" && years(21) === "21 год" && years(4.96) === "5,0 года",
  [1.6, 2.4, 0.5, 1, 2, 5, 11, 21, 4.96].map(years));
  check("P7: слово после числа — по напечатанному числу", page.get('plural(2.4, ["день", "дня", "дней"])') === "дня" && page.get('plural(4.6, ["день", "дня", "дней"])') === "дней"
    && page.get('plural(4.6, ["день", "дня", "дней"], 1)') === "дня" && squash(page.get("fmt.days(21)")) === "21 день", null);

  let text = at("book");
  check("P7: счётчики проверок согласованы с числом", text.includes("0 нарушенных инвариантов из 14 2 сработавших гейта из 19 1 поднятый флаг"), text.match(/.{0,20}нарушенн.{0,120}/));
  check("P7: число клеток гейта — словом в падеже числа, без «кл.»", text.includes("22 клетки · 61,0 % массы · ожидалось 40,0–80,0 %") && text.includes("4 клетки · ") && !/\d кл\./.test(text), text.match(/.{0,30}массы.{0,60}/));
  const cm = squash(visibleText(cardOf(page, "Сверка с контрольной моделью")));
  check("P7: сверка — сдвиг доли в п.п. и в столбцах уровня; допуск — в единице строки", cm.includes("передача: сдвиг спредов активов σ0 −0,11 п.п. −0,11 п.п. 0 п.п. 0,0001 п.п. в допуске"), cm.slice(0, 900));
  check("P7: сверка — разность мельче знаков допуска печатается нулём, без степени десяти", cm.includes("кредитная маржа стационара, мир M 0,58 п.п. 0,58 п.п. 0 п.п. 0,0001 п.п. в допуске") && !/10⁻¹⁷/.test(cm), cm.slice(0, 900));
  check("P7: сверка — уровень доли в %, её разность и допуск в п.п.; число — степенью, деньги — млрд ₽ и ₽", cm.includes("14,89 % 14,86 % −0,03 п.п. 0,30 п.п.") && cm.includes("0,0600 0,0600 0 1·10⁻⁶")
    && cm.includes("−30,0 млрд ₽ 584,5 млрд ₽") && cm.includes("10,0 % или 2,05 ₽") && cm.includes("коммит 5f1c0d9;"), cm.slice(0, 1500));
  check("P7: сверка — правило печати названо в подзаголовке", cm.includes("Разность и допуск — по виду допуска: относительные — в процентах, абсолютные — в единице строки (доли и сдвиги — в п.п.); разность мельче знаков допуска — ноль."), cm.slice(0, 400));

  text = at("capital");
  const hist = squash(visibleText(cardOf(page, "Дивиденды по году прибыли")));
  check("P7: источник истории дивидендов — словами выпуска, годы с одним источником — одной записью",
    hist.includes("Источники факта: 2013–2020, 2022–2025 — решение годового общего собрания акционеров (раскрытие эмитента); сверено с брокерским календарём дивидендов; 2021 — годовое общее собрание акционеров решило дивиденды не выплачивать (раскрытие эмитента)."),
    hist.slice(-400));
  check("P7: мост норматива назван расчётом модели", text.includes("расчёт модели на смеси заголовка, включая уровень на дату якоря"), text.match(/.{0,40}Мост .{0,160}/));
  text = at("book");
  check("P7: узел кривой — срок словом по числу", text.includes("сдвиг узла 5 лет к книге") && text.includes("сдвиг узла 10 лет к книге"), text.match(/.{0,20}сдвиг узла.{0,40}/));
  check("P7: экраны без ошибок", !page.errors.length, page.errors.slice(0, 2));
}

{
  // Частные случаи: счётчики на 1 и 2; гейт, сравнивающий миры целиком; строки сверки вне допуска с малой разностью;
  // один источник истории дивидендов и свой у каждого года; дробный срок на оси и в политике.
  const d = sample();
  d.checks.invariants_broken = 1;
  d.checks.flags.forEach((f) => { f.raised = ["explanation_expiring", "ni_jump"].includes(f.name); });
  const worlds = d.checks.gates.find((g) => g.name === "roe_k_homogeneity");
  Object.assign(worlds, { fired: true, mass: 1.0, cells: 0, cell_list: [], status: "explained", expected_mass: 1.0, valid_until: "2026-11-30",
    message: "разрыв между мирами 5,24 п.п. при пределе 5 п.п.", explanation: "Разрыв — цена раздельного сжатия спреда; объяснён до пересмотра книги." });
  d.checks.gates.find((g) => g.name === "capital_gap").fired = false;
  d.checks.gates.find((g) => g.name === "guidance_gap").fired = false;
  // Несработавший гейт с сообщением: в нём хвост прогонов полосы, которого в печатаемой сетке нет (М§10).
  d.checks.gates.find((g) => g.name === "roe_gt_g").message = "ROE терминала не выше роста в 0 клетках; в полосе — в 367 из 2 000 прогонов (масса таких клеток в среднем по всем прогонам — 0,64 %)";
  const noisy = d.checks.control_model.rows.find((r) => r.what.startsWith("кредитная маржа"));
  Object.assign(noisy, { control: noisy.core + 3e-6, diff: 3e-6, diff_rel: 3e-6 / noisy.core, ok: false });
  const number = d.checks.control_model.rows.find((r) => r.unit === "number");
  Object.assign(number, { control: number.core + 2e-5, diff: 2e-5, diff_rel: 2e-5 / number.core, ok: false });
  d.dividends.history.forEach((h) => { h.src = "решения годовых общих собраний акционеров (раскрытие эмитента)"; });
  d.judgements.rows.find((j) => j.id === "fvoci_maturity").low = 1.6;
  d.dividends.policy.excess.ramp_years = 2.5;
  d.live.curve.shift_bp = { 1: 4, 2: -3 };
  const page = makePage({ payload: JSON.stringify(d), now: FRESH });
  await page.boot();
  await settle();
  page.hashchange("#book");
  press(page, `Показать все ${d.judgements.rows.length}`);
  let text = page.text();
  check("P7: счётчики на 1 и 2 — «1 нарушенный инвариант», «1 сработавший гейт», «2 поднятых флага»", text.includes("1 нарушенный инвариант из 14 1 сработавший гейт из 19 2 поднятых флага"), text.match(/.{0,20}нарушенн.{0,120}/));
  check("P7: гейт, сравнивающий миры целиком, — масса без счёта клеток", text.includes(`${worlds.title}100,0 % массы · ожидалось 100,0 %`) && !/0 клеток ·|\d кл\./.test(text), text.match(/.{0,60}100,0 % массы.{0,60}/));
  const quietGates = squash(visibleText(cardOf(page, "Проверки")));
  check("аудит 02.10: сообщение несработавшего гейта печатается у его коридора — предложением",
    quietGates.includes("ROE'_T > g_T ROE терминала не выше роста в 0 клетках; в полосе — в 367 из 2 000 прогонов (масса таких клеток в среднем по всем прогонам — 0,64 %)."), quietGates.match(/.{0,40}ROE терминала не выше роста.{0,200}/));
  const cm = squash(visibleText(cardOf(page, "Сверка с контрольной моделью")));
  check("P7: сверка — строка вне допуска разность не прячет", cm.includes("+0,0003 п.п. 0,0001 п.п. вне допуска") && cm.includes("2·10⁻⁵ 1·10⁻⁶ вне допуска") && cm.includes("вне допуска — 2."), cm.slice(0, 900));
  check("P7: дробный срок на оси — «1,6 года»", /Срок подтягивания FVOCI 4 года 1,6 года … 6 лет/.test(text), text.match(/.{0,20}Срок подтягивания.{0,80}/));
  check("P7: узел кривой 1 год и 2 года", text.includes("сдвиг узла 1 год к книге") && text.includes("сдвиг узла 2 года к книге"), text.match(/.{0,20}сдвиг узла.{0,40}/));
  page.hashchange("#capital");
  text = page.text();
  check("P7: один источник истории дивидендов — одной строкой", squash(visibleText(cardOf(page, "Дивиденды по году прибыли"))).endsWith("Источник факта: решения годовых общих собраний акционеров (раскрытие эмитента)."), text.match(/.{0,40}Источники? факта.{0,160}/));
  check("P7: дробный срок ввода избытка — «за 2,5 года»", text.includes("ввод линейно за 2,5 года"), text.match(/.{0,40}ввод линейно.{0,40}/));

  const many = sample();
  many.dividends.history.forEach((h) => { h.src = h.dps ? `решение годового общего собрания акционеров ${h.year + 1} года (раскрытие эмитента)` : null; });
  const wide = makePage({ payload: JSON.stringify(many), now: FRESH });
  await wide.boot();
  await settle();
  wide.hashchange("#capital");
  const card = squash(visibleText(cardOf(wide, "Дивиденды по году прибыли")));
  check("P7: у каждого года свой источник — списком по годам; год без источника не печатается", card.includes("Источники факта по годам · 12") && card.includes("2025решение годового общего собрания акционеров 2026 года (раскрытие эмитента)")
    && !card.includes("2021решение"), card.slice(-300));
  check("P7: частные случаи без ошибок", !page.errors.length && !wide.errors.length && !/не отрисовал/.test(page.text() + wide.text()), page.errors.concat(wide.errors).slice(0, 2));
}

/* ── 8в. прозрачность карточки режимов: подмена наблюдения и длинный ближний сдвиг ЧПМ ── */

{
  const page = makePage({ now: FRESH });
  await page.boot();
  await settle();
  page.hashchange("#model");
  let text = squash(visibleText(cardOf(page, "Режимы доходности и кредитного цикла")));
  check("подмена наблюдения: опубликованное число и причина — рядом с подменённым, отметка в заголовке",
    text.includes("Наблюдения кварталов · 1 · с подменой")
    && text.includes("2 кв. 2026: CoR 1,20 % (опубликовано 1,05 %), ЧПМ 6,20 % (упр.) — разовый роспуск резерва занизил CoR квартала (пример)."),
    text.match(/.{0,40}Наблюдения кварталов.{0,260}/));
  check("короткий ближний сдвиг (до нуля за 6 кварталов) — весь ряд, как прежде",
    text.includes("(движок, п.п.): 3К’26 −0,5 · 4К’26 −0,5 · 1К’27 −0,375 · 2К’27 −0,25 · 3К’27 −0,125 · 4К’27 0.") && !text.includes("…"),
    text.match(/.{0,20}Ближний сдвиг.{0,200}/));

  const long = sample();
  const quarters = [];
  for (let y = 2026; y <= 2036; y++) for (let k = 1; k <= 4; k++) if (y > 2026 || k === 4) quarters.push(`${y}Q${k}`);
  long.regimes.near_nim_shift = quarters.map((period, i) => ({ period, value: i === 0 ? -0.000155 : Math.round(3203 / i) / 1e7 }));
  delete long.regimes.update.observations[0].published;
  const plain = makePage({ payload: JSON.stringify(long), now: FRESH });
  await plain.boot();
  await settle();
  plain.hashchange("#model");
  text = squash(visibleText(cardOf(plain, "Режимы доходности и кредитного цикла")));
  check("длинный ближний сдвиг (41 квартал до конца явного участка) — пять первых кварталов, многоточие, последний и число",
    text.includes("(движок, п.п.): 4К’26 −0,015 · 1К’27 0,032 · 2К’27 0,016 · 3К’27 0,011 · 4К’27 0,008 · … · 4К’36 0,001 (41 кв.).")
    && !text.includes("1К’28"), text.match(/.{0,20}Ближний сдвиг.{0,300}/));
  check("наблюдение без подмены — без опубликованного числа и без отметки",
    text.includes("Наблюдения кварталов · 1 Режим") || (text.includes("Наблюдения кварталов · 1") && !text.includes("с подменой") && !text.includes("опубликовано")),
    text.match(/.{0,40}Наблюдения кварталов.{0,200}/));

  const gone = sample();
  Object.assign(gone.regimes.update.observations[0], { cor: null, se_cor: null });
  const missing = makePage({ payload: JSON.stringify(gone), now: FRESH });
  await missing.boot();
  await settle();
  missing.hashchange("#model");
  text = squash(visibleText(cardOf(missing, "Режимы доходности и кредитного цикла")));
  check("опубликованное число, которое книга не взяла (null), — прочерк и опубликованное", text.includes("2 кв. 2026: CoR — (опубликовано 1,05 %), ЧПМ 6,20 %"),
    text.match(/.{0,40}2 кв\. 2026: CoR.{0,120}/));
  check("карточка режимов: частные случаи без ошибок", !page.errors.length && !plain.errors.length && !missing.errors.length
    && !BAD_TEXT.test(page.text() + plain.text() + missing.text()), page.errors.concat(plain.errors, missing.errors).slice(0, 2));
}

/* ── 9. отказы двери ── */

for (const [status, body, title] of [[503, '{"error":"not published yet"}', "Выпуск ещё не опубликован"], [503, '{"error":"upstream unavailable"}', "Источник данных временно недоступен"],
  [403, '{"error":"user-agent required"}', "Данные недоступны"], [200, "<html>", "Ответ не разобрался как JSON"]]) {
  const page = makePage({ status, payload: body, now: FRESH });
  await page.boot();
  await settle();
  check(`дверь ${status} ${body.slice(0, 30)} → «${title}»`, page.text().includes(title) && !page.bootError, page.text().slice(0, 200));
}

const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ total: checks.length, failed, unhandled: rejections.slice(0, 3) }, null, 1));
process.exitCode = failed.length || rejections.length ? 1 : 0;
