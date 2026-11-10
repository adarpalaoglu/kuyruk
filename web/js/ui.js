// Pieces every screen shares: the header (logo, DEMO pill, menu, TR/EN
// switch, account menu), error messages, small helpers, and `boot`, which
// starts a page.
//
// Text is always put on the page as plain text (textContent), never as
// HTML, so a text someone sent (e.g. "<script>") can never run as code.

import { ApiError, API, call, loginAgain, systemStatus } from "./api.js";
import { apply, loadLang, number, onLangChange, otherLang, setLang, startingLang, t } from "./i18n.js";
import { clearSession, getPass, getUser } from "./session.js";

/** Make an element: el("a", {href: "x", class: "y"}, "text", child, ...). */
export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** The short form of a ticket shown on screens: its first 7 characters. */
export function shortTicket(id) {
  return String(id).slice(0, 7);
}

/** A time as hours:minutes in the current language, e.g. 03:00. */
export function clock(date) {
  return new Intl.DateTimeFormat(document.documentElement.lang, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

/** The logo's three bars (drawn, so it needs no image file). */
function logoMark() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "22");
  svg.setAttribute("height", "22");
  svg.setAttribute("viewBox", "0 0 28 28");
  svg.setAttribute("aria-hidden", "true");
  for (const [y, width, opacity] of [["5", "24", "1"], ["12", "17", "0.55"], ["19", "10", "0.25"]]) {
    const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    for (const [name, value] of Object.entries({ x: "2", y, width, height: "4", rx: "2", fill: "#1D1D1F", opacity })) {
      bar.setAttribute(name, value);
    }
    svg.append(bar);
  }
  return svg;
}

/**
 * Turn an ApiError into the sentence a person should read.
 *
 * Every error code the API can send has its own words in both languages
 * (`error_code` in i18n/*.json); an unknown code falls back to the API's own
 * message. No reply at all means the API can't be reached.
 */
export function errorText(error) {
  if (!(error instanceof ApiError)) return t("error.other", { detail: String(error && error.message) });
  if (error.network) return t("error.network");
  let time = "–";
  const seconds = Number(error.retryAfter);
  if (error.retryAfter !== null && Number.isFinite(seconds)) time = clock(new Date(Date.now() + seconds * 1000));
  const known = t(`error_code.${error.code}`, { time });
  if (known !== `error_code.${error.code}`) return known;
  return error.message || t("error.other", { detail: error.code });
}

// Codes whose API message holds details the general words can't: which text
// broke which rule, or how many tokens. They are shown as a second line.
const DETAILED = new Set(["validation_error", "job_exceeds_daily_budget", "budget_exceeded"]);

/** Show an error (or clear it, with error = null) in a message box. */
export function showError(box, error) {
  box.replaceChildren();
  if (!error) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.classList.add("is-error");
  box.append(errorText(error));
  if (error instanceof ApiError && DETAILED.has(error.code) && error.message) {
    box.append(el("span", { class: "request-id", text: t("error.details", { detail: error.message }) }));
  }
  if (error.requestId) {
    box.append(el("span", { class: "request-id", text: t("error.request_id", { id: error.requestId }) }));
  }
}

/** Send people who aren't logged in to the login page first. */
export function requireLogin() {
  if (!getPass()) {
    loginAgain(false);
    return false;
  }
  return true;
}

let menuOpen = false;

/** Fill the account menu: who you are, today's budget, the links. */
async function fillMenu(menu) {
  const budgetBlock = el("div", { class: "menu-block budget" }, el("span", { class: "menu-small", text: t("menu.budget") }));
  menu.replaceChildren(
    el("div", { class: "menu-block" },
      el("span", { class: "menu-small", text: t("menu.signed_in") }),
      el("strong", { class: "menu-user", text: getUser() })),
    budgetBlock,
    el("div", { class: "menu-links" },
      el("a", { href: "status.html", text: t("menu.status") }),
      el("a", { href: `${API}/docs`, text: t("menu.docs") }),
      el("button", {
        type: "button",
        "data-testid": "logout",
        text: t("menu.logout"),
        onclick: () => {
          clearSession();
          location.assign("login.html?mode=login");
        },
      })),
  );
  try {
    const { data } = await call("/me/budget");
    const share = data.limit ? Math.min(data.used / data.limit, 1) : 0;
    const fill = el("span");
    fill.style.width = `${(share * 100).toFixed(2)}%`;
    budgetBlock.append(
      el("div", { class: "bar", role: "img", "aria-label": t("menu.budget_bar", { used: data.used, limit: data.limit }) }, fill),
      el("span", { class: "menu-left" },
        el("strong", { text: number(data.left) }),
        " ",
        t("menu.left", { time: clock(new Date(data.resets_at)) })),
    );
  } catch (error) {
    const box = el("p", { class: "menu-small" });
    box.textContent = errorText(error);
    budgetBlock.append(box);
  }
}

/**
 * Draw the header.
 *
 * `page` marks the current menu link. The login page shows only the logo and
 * the language switch. People who aren't logged in see The experiments, API
 * and Log in; logged-in people see New job, My jobs, The experiments and
 * their account menu.
 */
export function renderHeader(page) {
  const header = document.getElementById("site-header");
  header.replaceChildren();
  // The logo's name is its visible text ("Kuyruk", plus "DEMO" in demo
  // mode), so what a screen reader says matches what is seen.
  const logo = el("a", { href: "index.html", class: "logo" }, logoMark(), "Kuyruk");
  header.append(logo);
  systemStatus()
    .then((status) => {
      if (status.demo_mode && !logo.querySelector(".demo-pill")) {
        logo.append(el("span", { class: "demo-pill", title: t("header.demo_label"), text: t("header.demo") }));
      }
    })
    .catch(() => {});

  const lang = el("button", {
    type: "button",
    class: "lang-switch",
    "aria-label": t("lang.switch"),
    "data-testid": "lang-switch",
    text: t("lang.other"),
    onclick: () => setLang(otherLang()),
  });
  if (page === "login") {
    header.append(lang);
    return;
  }

  const nav = el("nav", { class: "main-nav", "aria-label": t("header.nav") });
  // One menu link; the current page's link is marked for screen readers.
  const link = (href, key, name) =>
    el("a", { href, "aria-current": page === name ? "page" : null, text: t(key) });
  if (!getPass()) {
    nav.append(link("experiments.html", "header.experiments", "experiments"), el("a", { href: `${API}/docs`, text: t("header.api") }), lang,
      el("a", { href: "login.html", class: "nav-login", text: t("header.login") }));
    header.append(nav);
    return;
  }
  const user = getUser();
  const menu = el("div", { class: "account-menu", id: "account-menu", hidden: !menuOpen });
  const avatar = el("button", {
    type: "button",
    class: "avatar",
    "aria-label": t("header.account", { name: user }),
    "aria-expanded": String(menuOpen),
    "aria-controls": "account-menu",
    "data-testid": "account",
    text: (user[0] || "?").toUpperCase(),
  });
  /** Open or close the account menu (filling it with today's budget). */
  function setOpen(open) {
    menuOpen = open;
    menu.hidden = !open;
    avatar.setAttribute("aria-expanded", String(open));
    if (open) fillMenu(menu);
  }
  avatar.addEventListener("click", () => setOpen(!menuOpen));
  // Esc closes the menu and puts the keyboard back on its button; a click
  // elsewhere closes it too.
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
      avatar.focus();
    }
  });
  avatar.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
  document.addEventListener("click", (event) => {
    if (menuOpen && !menu.contains(event.target) && event.target !== avatar) setOpen(false);
  });
  nav.append(link("new.html", "header.new", "new"), link("jobs.html", "header.jobs", "jobs"), link("experiments.html", "header.experiments", "experiments"), lang, avatar);
  header.append(nav, menu);
  if (menuOpen) fillMenu(menu);
}

/** Open the account menu (used by the screenshots of My jobs). */
export function openAccountMenu() {
  document.querySelector("[data-testid=account]")?.click();
}

/**
 * Start a page: choose and load the language, draw the header, fill in the
 * texts, then run the page's own `start`. After a language switch the header
 * and texts are redrawn and the page's `redraw` runs.
 */
export async function boot({ page, needsLogin = false, start = async () => {}, redraw = () => {} }) {
  if (needsLogin && !requireLogin()) return;
  try {
    await loadLang(startingLang());
  } catch {
    // Without the words file the page still shows its structure.
  }
  renderHeader(page);
  apply();
  onLangChange(() => {
    renderHeader(page);
    redraw();
  });
  try {
    await start();
  } finally {
    // The page is hidden until its texts are in place, so nobody sees empty
    // labels flash by (see the "loading" rule in each page's <head>).
    document.documentElement.classList.remove("loading");
  }
}
