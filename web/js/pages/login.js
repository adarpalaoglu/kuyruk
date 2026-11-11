// Log in (login.html, D126): username and password, then POST /token. The
// pass is kept in sessionStorage (D116), then the page you came from opens
// (or New job). A "your login ran out" note shows when a pass expired.
// Creating an account is its own page (signup.js).

import { systemStatus } from "../api.js";
import { logIn, otherPage } from "../account.js";
import { t } from "../i18n.js";
import { boot, showError } from "../ui.js";

const expired = new URLSearchParams(location.search).has("expired");
let demo = false;
let busy = false;

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const submit = document.getElementById("login-submit");

/** Write the texts that depend on demo mode and on sending. */
function draw() {
  document.getElementById("login-sub").textContent = t(demo ? "login.demo_sub" : "login.sub");
  submit.textContent = t(busy ? "login.working" : "login.login_button");
  const note = document.getElementById("login-note");
  note.hidden = !expired;
  note.textContent = t("error.expired");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  submit.disabled = true;
  draw();
  showError(errorBox, null);
  try {
    await logIn(document.getElementById("username").value.trim(), document.getElementById("password").value);
  } catch (error) {
    showError(errorBox, error);
    busy = false;
    submit.disabled = false;
    draw();
  }
});

boot({
  page: "login",
  start: async () => {
    document.getElementById("switch-link").href = otherPage("signup.html");
    draw();
    try {
      demo = (await systemStatus()).demo_mode;
    } catch {
      // Without the API the form still shows; sending will say it can't reach it.
    }
    draw();
  },
  redraw: draw,
});
