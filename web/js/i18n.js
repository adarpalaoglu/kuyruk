// Languages: every visible text in English or Turkish (D39).
//
// All words live in two files, i18n/en.json and i18n/tr.json, under the same
// names ("keys"), e.g. "job.cancel". A page marks where a text goes with
// data-i18n="job.cancel" (or data-i18n-aria / data-i18n-placeholder for a
// label or a hint inside a box), and `apply` fills them in.
//
// Texts can have slots in curly brackets, e.g. "{done} of {total}". A slot
// holding a number is written the local way: 1,331 and 0.007 in English,
// 1.331 and 0,007 in Turkish. A text that changes with a number (1 text,
// 2 texts) is stored as {"one": ..., "other": ...}.
//
// The first visit follows the browser's language (Turkish if it starts with
// "tr", otherwise English); a choice made with the TR/EN switch is remembered
// in localStorage (D116). That is fine: the language is not a secret.

const LANGS = ["en", "tr"];
const STORE_KEY = "kuyruk.lang";

let lang = "en";
let words = {};
// Slot values every text on the page may use (e.g. the story numbers).
let shared = {};
const listeners = [];

/** The language to start with: the remembered choice, else the browser's. */
export function startingLang() {
  try {
    const saved = localStorage.getItem(STORE_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch {
    // Storage can be blocked (private window); the browser's language then.
  }
  const asked = navigator.languages || [navigator.language || "en"];
  return asked.some((l) => String(l).toLowerCase().startsWith("tr")) ? "tr" : "en";
}

/** The language shown now: "en" or "tr". */
export function currentLang() {
  return lang;
}

/** Load one language's words from its file. */
export async function loadLang(chosen) {
  const reply = await fetch(`i18n/${chosen}.json`);
  words = await reply.json();
  lang = chosen;
  document.documentElement.lang = chosen;
}

/** Add slot values that every text on the page can use. */
export function share(values) {
  shared = { ...shared, ...values };
}

/** Write a number the local way (1,331.5 in English, 1.331,5 in Turkish). */
export function number(value, options = {}) {
  return new Intl.NumberFormat(lang, { maximumFractionDigits: 3, ...options }).format(value);
}

/** Look up a key such as "job.cancel" in the loaded words. */
function lookup(key) {
  return key.split(".").reduce((part, name) => (part == null ? part : part[name]), words);
}

/**
 * The text for `key`, with its slots filled in.
 *
 * `values` fills the slots; numbers are written the local way. `count`
 * picks the one/other form of a text that changes with a number. A slot with
 * no value shows a dash, so a missing number is visible, never invented.
 */
export function t(key, values = {}, count) {
  let text = lookup(key);
  if (text && typeof text === "object") {
    const n = count ?? values.n ?? 0;
    const form = new Intl.PluralRules(lang).select(Number(n));
    text = text[form] ?? text.other;
  }
  if (typeof text !== "string") return key;
  const all = { ...shared, ...values };
  return text.replace(/\{(\w+)\}/g, (_, name) => {
    const value = all[name];
    if (value === undefined || value === null) return "–";
    return typeof value === "number" ? number(value) : String(value);
  });
}

/**
 * A message the API wrote in English (e.g. a job's "AI unavailable"), in
 * the current language if it is a known one; otherwise as the API wrote it.
 * Looked up whole, because such messages may contain dots.
 */
export function serverMessage(message) {
  return phrase("server_message", message);
}

/** A model's display name (gemma4:e4b → Gemma 4 E4B), if the words list it. */
export function modelName(model) {
  return phrase("models", model);
}

/** `raw` looked up whole in one group of the words, or `raw` itself. */
function phrase(group, raw) {
  const known = words[group] && words[group][raw];
  return typeof known === "string" ? known : raw;
}

/** "1 text" / "12 texts" (Turkish "12 metin"). */
export function texts(n) {
  return t("texts", { n }, n);
}

/** Fill every marked place in `root` with the current language's text. */
export function apply(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of root.querySelectorAll("[data-i18n-aria]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAria));
  }
  for (const node of root.querySelectorAll("[data-i18n-placeholder]")) {
    node.setAttribute("placeholder", t(node.dataset.i18nPlaceholder));
  }
  const page = document.body.dataset.page;
  if (page) document.title = t(`title.${page}`);
}

/** Switch language, remember it, and redraw the page. */
export async function setLang(chosen) {
  try {
    localStorage.setItem(STORE_KEY, chosen);
  } catch {
    // Not remembered if storage is blocked; the switch still works now.
  }
  await loadLang(chosen);
  apply();
  for (const redraw of listeners) redraw();
}

/** Run `redraw` after every language switch (for texts built by code). */
export function onLangChange(redraw) {
  listeners.push(redraw);
}

/** The other language, for the switch button. */
export function otherLang() {
  return lang === "en" ? "tr" : "en";
}
