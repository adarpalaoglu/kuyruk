// The experiments page (experiments.html): the story numbers, and the
// "Simulate a crash" button (demo mode only, D74).
//
// The button asks the API to crash the worker running your job
// (POST /demo/crash-worker), then follows that job's live progress and
// lights up the three steps as they really happen:
//   1. the API has accepted the crash (the worker stops at its next beat);
//   2. the `recovered` message: another worker found the job stuck and took
//      it over;
//   3. the first progress after that: the new worker finished a text.
// Each step then shows how many seconds after the click it happened.

import { call, systemStatus } from "../api.js";
import { number, t } from "../i18n.js";
import { getPass } from "../session.js";
import { loadStoryNumbers } from "../story.js";
import { watchJob } from "../stream.js";
import { boot, el, errorText } from "../ui.js";

const button = document.getElementById("crash-button");
const message = document.getElementById("crash-message");
const steps = [...document.querySelectorAll("#crash-steps li")];
// Seconds after the click at which each step happened (null = not yet).
let times = [null, null, null];
let state = "idle";
let ticket = null;
let stopWatching = null;

/** Light the steps that have happened and write their real times. */
function drawSteps() {
  // Before a crash, the page's own labels stay (0 s, about 5 s ...).
  if (!["asking", "waiting", "recovered", "done", "too_late"].includes(state)) return;
  steps.forEach((step, i) => {
    step.classList.toggle("is-lit", times[i] !== null);
    const when = document.getElementById(`when-${i + 1}`);
    when.textContent = times[i] === null ? "…" : t("experiments.crash_after", { s: number(times[i], { maximumFractionDigits: 1 }) });
  });
}

/** Write the line under the button, with an optional link. */
function say(key, link) {
  message.replaceChildren(t(key));
  if (link) message.append(" ", el("a", { href: link.href, text: t(link.key) }));
}

/** Redraw the message for the current state (also after a language switch). */
function drawMessage() {
  const watch = ticket ? { href: `job.html?ticket=${ticket}`, key: "experiments.crash_watch" } : null;
  const lines = {
    idle: () => message.replaceChildren(),
    asking: () => say("experiments.crash_asking"),
    waiting: () => say("experiments.crash_waiting", watch),
    recovered: () => say("experiments.crash_recovered", watch),
    done: () => say("experiments.crash_done", watch),
    too_late: () => say("experiments.crash_too_late", { href: "new.html", key: "experiments.crash_new_link" }),
    no_job: () => say("experiments.crash_no_job", { href: "new.html", key: "experiments.crash_new_link" }),
    login: () => say("experiments.crash_login", { href: "login.html?next=experiments.html%23crash", key: "experiments.crash_login_link" }),
    demo_only: () => say("experiments.crash_demo_only"),
  };
  if (lines[state]) lines[state]();
  // An empty line would still take up space between the button and steps.
  message.hidden = message.textContent === "";
}

/** Move to the next step of the crash and redraw the message and steps. */
function setState(next) {
  state = next;
  drawMessage();
  drawSteps();
}

/** The button: crash the worker, then follow the job's live progress. */
async function crash() {
  if (!getPass()) return setState("login");
  stopWatching?.();
  times = [null, null, null];
  ticket = null;
  button.disabled = true;
  setState("asking");
  const clicked = performance.now();
  const since = () => (performance.now() - clicked) / 1000;
  try {
    const { data } = await call("/demo/crash-worker", { method: "POST" });
    ticket = data.ticket;
    times[0] = since();
    setState("waiting");
  } catch (error) {
    button.disabled = false;
    if (error.code === "no_running_job") return setState("no_job");
    state = "error";
    message.textContent = errorText(error);
    message.hidden = false;
    return;
  }
  let recovered = false;
  stopWatching = watchJob(ticket, {
    onEvent: (name, data, fresh) => {
      if (name === "recovered") {
        recovered = true;
        times[1] = since();
        setState("recovered");
      } else if (name === "progress" && recovered && !fresh && times[2] === null) {
        times[2] = since();
        setState("done");
        button.disabled = false;
        stopWatching();
      } else if (name === "end") {
        // The job ended: either after the recovery (fine), or before the
        // worker ever crashed (a mark on a finished job does nothing).
        button.disabled = false;
        if (!recovered) setState("too_late");
        else if (times[2] === null) {
          times[2] = since();
          setState("done");
        }
      }
    },
  });
}

boot({
  page: "experiments",
  start: async () => {
    button.addEventListener("click", crash);
    await loadStoryNumbers();
    try {
      const status = await systemStatus();
      if (!status.demo_mode) {
        button.hidden = true;
        setState("demo_only");
      }
    } catch {
      // No API: the button stays; pressing it shows the "can't reach" error.
    }
  },
  redraw: () => {
    drawMessage();
    drawSteps();
  },
});
