// Sign up / log in (login.html).
//
// One form for both. "Create your account" signs up (POST /register) and
// then logs in (POST /token); "Log in" only logs in. The pass is kept in
// sessionStorage (D116), then the page you came from opens (or New job).
// The page opens in "log in" mode after a log-out, when a pass ran out, or
// with ?mode=login; otherwise in "create your account" mode, as drawn.

import { call, safeNext, systemStatus } from "../api.js";
import { t } from "../i18n.js";
import { saveSession } from "../session.js";
import { boot, showError } from "../ui.js";

const query = new URLSearchParams(location.search);
let mode = query.get("mode") === "login" || query.has("expired") ? "login" : "signup";
let limits = null;
let demo = false;
let busy = false;

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const submit = document.getElementById("login-submit");
const password = document.getElementById("password");

/** Write every text that depends on the mode, the limits or demo mode. */
function draw() {
  document.getElementById("login-title").textContent = t(mode === "signup" ? "login.signup_title" : "login.login_title");
  document.getElementById("login-sub").textContent = t(demo ? "login.demo_sub" : "login.sub");
  const hint = document.getElementById("userhint");
  hint.hidden = mode !== "signup" || !limits;
  if (limits) {
    hint.textContent = t("login.hint", {
      umin: limits.username_min_length,
      umax: limits.username_max_length,
      pmin: limits.password_min_length,
    });
  }
  submit.textContent = t(busy ? "login.working" : "login.continue");
  document.getElementById("switch-text").textContent = t(mode === "signup" ? "login.have_account" : "login.no_account");
  document.getElementById("switch-mode").textContent = t(mode === "signup" ? "login.to_login" : "login.to_signup");
  password.setAttribute("autocomplete", mode === "signup" ? "new-password" : "current-password");
  const note = document.getElementById("login-note");
  note.hidden = !query.has("expired");
  note.textContent = t("error.expired");
}

/** Log in and keep the pass; returns once the next page is opening. */
async function logIn(username, secret) {
  const { data } = await call("/token", { method: "POST", form: { username, password: secret }, auth: false });
  // Usernames are stored in lowercase (D43); show them that way too.
  saveSession(data.access_token, username.toLowerCase());
  location.assign(safeNext(query.get("next")));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const username = document.getElementById("username").value.trim();
  const secret = password.value;
  busy = true;
  submit.disabled = true;
  draw();
  showError(errorBox, null);
  try {
    if (mode === "signup") {
      await call("/register", { method: "POST", json: { username, password: secret }, auth: false });
    }
    await logIn(username, secret);
  } catch (error) {
    showError(errorBox, error);
    busy = false;
    submit.disabled = false;
    draw();
  }
});

document.getElementById("switch-mode").addEventListener("click", () => {
  mode = mode === "signup" ? "login" : "signup";
  showError(errorBox, null);
  draw();
});

boot({
  page: "login",
  start: async () => {
    draw();
    try {
      const status = await systemStatus();
      limits = status.limits;
      demo = status.demo_mode;
    } catch {
      // Without the API the form still shows; sending will say it can't reach it.
    }
    draw();
  },
  redraw: draw,
});
