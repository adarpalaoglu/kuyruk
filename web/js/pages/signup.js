// Create your account (signup.html, D126).
//
// Username, password and the password again. The two passwords are compared
// here in the browser before anything is sent, so a typo never becomes an
// account nobody can log in to; the API's own rules (length, letters) are
// checked by POST /register as before. Then the new account logs in
// (POST /token), the first-time guide is switched on for it (D129) and New
// job opens (or the page you came from).

import { call, systemStatus } from "../api.js";
import { logIn, otherPage } from "../account.js";
import { showGuideFor } from "../guide.js";
import { t } from "../i18n.js";
import { boot, showError } from "../ui.js";

let limits = null;
let demo = false;
let busy = false;
// The message key of a problem found here, before sending (or null).
let localProblem = null;
// The account this page has already created ("name\npassword"). If
// logging in then failed (e.g. the network dropped), pressing again only
// logs in: creating it again would say "username taken".
let created = null;

const form = document.getElementById("signup-form");
const errorBox = document.getElementById("signup-error");
const submit = document.getElementById("signup-submit");
const password = document.getElementById("password");
const confirm = document.getElementById("confirm");

/** Write the texts that depend on the limits, demo mode and sending. */
function draw() {
  document.getElementById("signup-sub").textContent = t(demo ? "login.demo_sub" : "login.sub");
  const hint = document.getElementById("userhint");
  hint.hidden = !limits;
  if (limits) {
    hint.textContent = t("login.hint", {
      umin: limits.username_min_length,
      umax: limits.username_max_length,
      pmin: limits.password_min_length,
    });
  }
  submit.textContent = t(busy ? "login.working" : "login.create_button");
  if (localProblem) {
    errorBox.hidden = false;
    errorBox.classList.add("is-error");
    errorBox.textContent = t(localProblem);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  showError(errorBox, null);
  localProblem = null;
  confirm.removeAttribute("aria-invalid");
  if (password.value !== confirm.value) {
    // Nothing is sent: say so, and put the keyboard on the second box.
    localProblem = "login.mismatch";
    confirm.setAttribute("aria-invalid", "true");
    draw();
    confirm.focus();
    return;
  }
  const username = document.getElementById("username").value.trim();
  busy = true;
  submit.disabled = true;
  draw();
  try {
    const account = `${username.toLowerCase()}\n${password.value}`;
    if (created !== account) {
      await call("/register", { method: "POST", json: { username, password: password.value }, auth: false });
      created = account;
      showGuideFor(username.toLowerCase());
    }
    await logIn(username, password.value);
  } catch (error) {
    showError(errorBox, error);
    busy = false;
    submit.disabled = false;
    draw();
  }
});

boot({
  page: "signup",
  start: async () => {
    document.getElementById("switch-link").href = otherPage("login.html");
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
