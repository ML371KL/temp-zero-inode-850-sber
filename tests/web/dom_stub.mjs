// Заглушка DOM для проверок витрины под Node: web/app.js грузится в node:vm, адрес разбирает
// WHATWG URL, fetch отдаёт выпуск, ResizeObserver рисует графики в заданной ширине.
// Общая для web_app_check.mjs (поведение на фикстуре) и web_release_text.mjs (текст экранов выпуска).
import { readFileSync } from "node:fs";
import vm from "node:vm";

const root = new URL("../../", import.meta.url);
// WEB_APP_JS — другой файл витрины (проверка самих проверок на прежней версии).
const APP = readFileSync(process.env.WEB_APP_JS || new URL("web/app.js", root), "utf8");
export const SAMPLE_TEXT = readFileSync(new URL("tests/fixtures/payload-sample.json", root), "utf8");
export const SCREEN_NAMES = ["overview", "market", "model", "report", "capital", "book"];

class Text_ {
  constructor(value) { this.nodeType = 3; this.data = String(value); this.parentNode = null; }
  get textContent() { return this.data; }
  set textContent(value) { this.data = String(value); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
}

class Element_ {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.parentNode = null;
    this.hidden = false;
    const self = this;
    this.classList = {
      add(c) { const s = new Set((self.attrs.class || "").split(" ").filter(Boolean)); s.add(c); self.attrs.class = [...s].join(" "); },
      remove(c) { self.attrs.class = (self.attrs.class || "").split(" ").filter((x) => x && x !== c).join(" "); },
      toggle() {}, contains: (c) => (self.attrs.class || "").split(" ").includes(c),
    };
  }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; }
  hasAttribute(key) { return key in this.attrs; }
  removeAttribute(key) { delete this.attrs[key]; }
  append(...kids) {
    for (const kid of kids) {
      const node = kid && kid.nodeType ? kid : new Text_(kid);
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = this;
      this.children.push(node);
    }
  }
  appendChild(kid) { this.append(kid); return kid; }
  replaceChildren(...kids) { for (const c of this.children) c.parentNode = null; this.children = []; this.append(...kids); }
  removeChild(kid) { this.children = this.children.filter((c) => c !== kid); kid.parentNode = null; return kid; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  dispatch(type, event = {}) { for (const fn of this.listeners[type] || []) fn({ preventDefault() {}, target: this, ...event }); }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); if (value !== "") this.append(new Text_(value)); }
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  querySelector(selector) { return this.ownerDocument ? this.ownerDocument.querySelector(selector, this) : null; }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; }
  scrollIntoView() {}
  focus() {}
  get clientWidth() { return 0; }
  get offsetWidth() { return 0; }
  get offsetHeight() { return 0; }
}

// Видимый текст узла (скрытые узлы — таблица-двойник до нажатия — не в счёт).
const BLOCKS = new Set(["P", "DIV", "LI", "UL", "OL", "H1", "H2", "H3", "SECTION", "TR", "TD", "TH", "CAPTION", "TABLE", "ARTICLE"]);
export function visibleText(node) {
  if (!node) return "";
  if (node.nodeType === 3) return node.data;
  if (node.hidden) return "";
  const inner = node.children.map(visibleText).join("");
  return BLOCKS.has(node.tagName) ? ` ${inner} ` : inner;
}
export const countTag = (node, tag) => (node && node.nodeType === 1 ? (node.tagName === tag ? 1 : 0) + node.children.reduce((n, c) => n + countTag(c, tag), 0) : 0);
export const findNode = (node, pred) => {
  if (!node || node.nodeType !== 1) return null;
  if (pred(node)) return node;
  for (const c of node.children) { const hit = findNode(c, pred); if (hit) return hit; }
  return null;
};
export const squash = (s) => String(s).replace(/[  ]/g, " ").replace(/\s+/g, " ").trim();

/* ── окружение страницы ── */

export function makePage({ href = "https://example.pages.dev/", payload = SAMPLE_TEXT, width = 640, status = 200, now = null } = {}) {
  const url = new URL(href);
  const errors = [];
  const observed = [];
  const windowListeners = {};
  const docListeners = {};
  const document = {
    readyState: "loading",
    title: "",
    createElement: (tag) => { const n = new Element_(tag); n.ownerDocument = document; return n; },
    createElementNS: (ns, tag) => { const n = new Element_(tag); n.ownerDocument = document; return n; },
    createTextNode: (value) => new Text_(value),
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  };
  const node = (tag, attrs = {}) => { const n = document.createElement(tag); Object.assign(n.attrs, attrs); return n; };
  const app = node("main", { id: "app" });
  app.append("Загружаем выпуск модели…");
  const chip = node("a", { id: "release-chip" });
  const chipText = node("span", { class: "chip-text" });
  chip.append(chipText);
  const colophon = node("p", { id: "colophon-release" });
  const brand = node("span", { class: "brand-name" });
  const tip = node("div", { id: "tip" });
  const tabs = SCREEN_NAMES.map((name) => { const t = node("button", { class: "tab" }); t.dataset.screen = name; t.append(name); return t; });
  document.documentElement = node("html");
  document.body = node("body");
  document.querySelector = (selector, scope) => {
    if (scope === chip && selector === ".chip-text") return chipText;
    const byId = { "#app": app, "#release-chip": chip, "#colophon-release": colophon, "#tip": tip, ".brand-name": brand };
    if (selector in byId) return byId[selector];
    const m = /^\.tab\[data-screen="(\w+)"\]$/.exec(selector);
    if (m) return tabs.find((t) => t.dataset.screen === m[1]) || null;
    return null;
  };
  document.querySelectorAll = (selector) => (selector === ".tab" ? tabs : []);
  const history = { pushState(s, t, hash) { url.hash = hash; }, replaceState(s, t, hash) { url.hash = hash; } };
  const location = { get hash() { return url.hash; }, get href() { return url.href; } };
  const window = { addEventListener(type, fn) { (windowListeners[type] ||= []).push(fn); }, scrollTo() {}, matchMedia: () => ({ matches: false }) };
  class ResizeObserver {
    constructor(fn) { this.fn = fn; }
    observe(host) { observed.push({ host, fn: this.fn }); }
    unobserve() {}
    disconnect() {}
  }
  const canvas = { getContext: () => ({ font: "", measureText: (s) => ({ width: String(s).length * 7 }) }) };
  const createElement = document.createElement;
  document.createElement = (tag) => (tag === "canvas" ? canvas : createElement(tag));
  const RealDate = Date;
  const FakeDate = now === null ? Date : class extends RealDate {
    constructor(...a) { super(...(a.length ? a : [now])); }
    static now() { return now; }
  };
  const ctx = {
    window, document, location, history, console: { error: (e) => errors.push(String((e && e.stack) || e)), log() {}, warn() {} },
    fetch: async () => ({ ok: status === 200, status, text: async () => payload }),
    ResizeObserver, AbortController, setTimeout, clearTimeout, URL, Intl, Date: FakeDate, Math, JSON, Promise,
    getComputedStyle: () => ({ fontFamily: "sans-serif" }), requestAnimationFrame: (fn) => setTimeout(fn, 0), innerWidth: 1280, innerHeight: 800,
  };
  window.document = document;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(APP, ctx, { filename: "web/app.js" });
  const page = {
    ctx, app, url, errors, chipText, colophon, document,
    get: (expr) => vm.runInContext(expr, ctx),
    paint(w = width) {
      let total = 0;
      for (let round = 0; round < 4; round++) {
        const batch = observed.splice(0);
        for (const { host, fn } of batch) fn([{ target: host, contentRect: { width: w } }]);
        total += batch.length;
        if (!batch.length) break;
      }
      return total;
    },
    bootError: null,
    async boot() {
      try { for (const fn of docListeners.DOMContentLoaded || []) await fn(); } catch (error) { page.bootError = String(error); }
      page.paint();
    },
    hashchange(hash) { url.hash = hash; for (const fn of windowListeners.hashchange || []) fn(); page.paint(); },
    text: () => squash(visibleText(app)),
  };
  return page;
}
