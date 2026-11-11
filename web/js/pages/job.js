// One job, live (job.html?ticket=...).
//
// The job and its texts are read once (GET /jobs/{ticket} and /results),
// then the live stream keeps the page up to date (js/stream.js):
// - `state` and `progress` update the big count, the sentence and the bar;
// - after each `progress`, the texts are read again, at most once a second
//   (D122), to colour the dots and list the latest results. "Working now" is
//   an estimate: the lowest waiting texts, up to texts-at-once (the worker
//   takes texts in order, a few at a time);
// - `recovered` shows the notice that another worker took over;
// - `end` stops the stream.
//
// In demo mode a running job also has "Simulate a crash" next to Cancel
// (D127): it asks the API to crash the worker running *this* job
// (POST /demo/crash-worker?ticket=...), and the recovered notice above then
// shows Kuyruk taking it over. Each button has a short line under it saying
// what it does, and the "i" button opens a few sentences on the difference.
// A finished job with texts left without a result offers to send them as a
// new job (js/resend.js, D128), once no worker holds it any more (D130).

import { call, systemStatus } from "../api.js";
import { serverMessage, t } from "../i18n.js";
import { drawResend, readSettled, settled } from "../resend.js";
import { watchJob } from "../stream.js";
import { boot, el, shortTicket, showError } from "../ui.js";

const ticket = new URLSearchParams(location.search).get("ticket") || "";
const LIVE = new Set(["queued", "running"]);

let job = null;
let items = [];
// Texts at once, from the settings (GET /status). Unknown (0) means no dot
// is marked "working now", rather than guessing a number. If the status
// can't be read, demo mode counts as off, so no crash button shows.
let atOnce = 0;
let connection = "open";
let recovered = null; // {pos, seconds} once a takeover was seen
let lastChangeAt = null; // when this page last saw texts finish
let lastFetch = 0;
let fetchTimer = null;
let stop = null;
let cancelling = false;
let demo = false; // demo mode on (from GET /status): the crash button shows
// Where a crash pressed on this page is: null (none), "asking" (sent),
// "waiting" (accepted; the worker stops at its next beat), "recovered"
// (another worker took over; the notice above says so), "too_late" (the job
// ended before the worker crashed).
let crashState = null;
let crashError = null;
// Whether `items` were read after the job finished and its worker let go
// (D130), so they are final and the resend button may show.
let final = false;

const $ = (id) => document.getElementById(id);

/** How many texts are finished (ok or failed). */
function done() {
  return job.succeeded + job.failed;
}

/** "about 9 minutes left", from the speed so far. */
function timeLeft() {
  if (job.state !== "running" || !job.started_at || done() === 0) return null;
  const elapsed = (Date.now() - new Date(job.started_at).getTime()) / 1000;
  const seconds = (elapsed / done()) * (job.total - done());
  if (seconds < 60) return t("job.left_under");
  const minutes = Math.round(seconds / 60);
  return t("job.left_minutes", { n: minutes }, minutes);
}

/** The one status sentence, e.g. "Summarising text 213 of 500…" (read aloud as it changes). */
function sentence() {
  if (connection === "lost" && LIVE.has(job.state)) return t("job.reconnecting");
  if (job.state === "queued") return t("job.queued");
  if (job.state === "running") return t(`job.doing_${job.task}`, { n: Math.min(done() + 1, job.total), total: job.total });
  if (job.state === "done") return t("job.done");
  if (job.state === "failed") return t("job.failed", { reason: serverMessage(job.error_message || "") });
  return t("job.cancelled");
}

/** Draw the top: the Live line, the big count, the sentence, the counts, the bar and the links. */
function drawTop() {
  const live = LIVE.has(job.state);
  const values = { task: t(`task.${job.task}`), ticket: shortTicket(job.id), state: t(`state.${job.state}`) };
  $("live-dot").hidden = !live;
  $("kicker").textContent = t(live ? "job.live" : "job.finished", values);
  $("big-count").replaceChildren(String(done()), el("span", { class: "of", text: t("job.of", { total: job.total }) }));
  // The big count reads oddly as a heading to a screen reader without words.
  $("big-count").setAttribute("aria-label", t("jobs.count_of", { done: done(), total: job.total }));
  $("sentence").textContent = sentence();
  const left = timeLeft();
  $("counts").textContent = t("job.counts", { ok: job.succeeded, failed: job.failed }) + (left ? ` · ${left}` : "");
  const bar = $("progress");
  bar.setAttribute("aria-valuemax", String(job.total));
  bar.setAttribute("aria-valuenow", String(done()));
  $("progress-fill").style.width = `${job.total ? (done() / job.total) * 100 : 0}%`;
  const link = $("results-link");
  link.href = `results.html?ticket=${job.id}`;
  link.textContent = t(live ? "job.results_so_far" : "job.results");
  const cancel = $("cancel");
  $("cancel-action").hidden = !live;
  cancel.disabled = cancelling;
  cancel.textContent = t(cancelling ? "job.cancelling" : "job.cancel");
  drawCrash();
}

/** The crash button, its "i" explanation and what a press is doing. */
function drawCrash() {
  // Only while the job runs: a queued job has no worker to crash yet.
  const show = demo && job.state === "running";
  const button = $("crash");
  $("crash-action").hidden = !show;
  // The explanation belongs to the two buttons; it goes with them.
  if (!show) setInfoOpen(false);
  button.textContent = t("job.crash");
  // One press at a time; pressing again is possible once this one is over.
  button.disabled = crashState === "asking" || crashState === "waiting";
  const line = $("crash-status");
  if (crashError) {
    showError(line, crashError);
    return;
  }
  line.classList.remove("is-error");
  const key = {
    asking: "experiments.crash_asking",
    waiting: "experiments.crash_waiting",
    too_late: "experiments.crash_too_late",
  }[crashState];
  line.hidden = !key;
  line.textContent = key ? t(key) : "";
}

/** Colour one dot per text: done, failed, working now (estimated, D122) or waiting. */
function drawDots() {
  const dots = $("dots");
  // One element per text, made once; afterwards only their colour changes.
  if (dots.children.length !== job.total) {
    dots.replaceChildren(...Array.from({ length: job.total }, () => el("span", { class: "dot" })));
  }
  const byPosition = new Map(items.map((item) => [item.position, item.state]));
  let working = job.state === "running" ? atOnce : 0;
  for (let position = 1; position <= job.total; position += 1) {
    const state = byPosition.get(position) || "waiting";
    let kind = state === "ok" ? "dot ok" : state === "failed" ? "dot failed" : "dot";
    if (state === "waiting" && working > 0) {
      kind = "dot working";
      working -= 1;
    }
    const dot = dots.children[position - 1];
    if (dot.className !== kind) dot.className = kind;
  }
}

/** List the three highest-numbered finished texts with their results. */
function drawLatest() {
  const finished = items.filter((item) => item.state !== "waiting").sort((a, b) => b.position - a.position).slice(0, 3);
  $("latest").hidden = finished.length === 0;
  $("latest-rows").replaceChildren(
    ...finished.map((item) =>
      el("div", { class: "latest-row" },
        el("span", { class: "n", text: String(item.position) }),
        el("span", {
          class: "t",
          text: item.state === "ok" ? item.result : t(job.task === "llm" ? "results.not_done_llm" : "results.not_done", {
            reason: item.error_message || t("results.unknown_reason"),
          }),
        }))),
  );
}

/** Show the "another worker took over" notice, once a takeover was seen. */
function drawRecovered() {
  const box = $("recovered");
  box.hidden = !recovered;
  if (!recovered) return;
  let text;
  if (recovered.pos === null) text = t("job.recovered_end");
  else if (recovered.seconds !== null) text = t("job.recovered_seconds", { pos: recovered.pos, s: recovered.seconds });
  else text = t("job.recovered", { pos: recovered.pos });
  box.replaceChildren(`${text} `, el("strong", { text: t("job.nothing_lost") }));
}

/** Redraw everything from the current job and texts. */
function draw() {
  if (!job) return;
  drawTop();
  drawDots();
  drawLatest();
  drawRecovered();
  drawResend($("resend"), job, items, final);
}

/** Open or close the few sentences on Cancel vs a crash (the "i" button). */
function setInfoOpen(open) {
  $("crash-info").hidden = !open;
  $("crash-info-button").setAttribute("aria-expanded", String(open));
}

/**
 * Once the job has finished, read it and its texts again until no worker
 * holds it (D130); only then are the texts final and the resend button may
 * show. Until then the button stays hidden.
 */
async function settle() {
  try {
    ({ job, items } = await readSettled(ticket));
    final = settled(job);
  } catch {
    // The page keeps what it has; the button stays hidden.
  }
  draw();
}

/** Read the texts again, at most once a second. */
function refreshItems() {
  const wait = 1000 - (Date.now() - lastFetch);
  if (fetchTimer) return;
  fetchTimer = setTimeout(async () => {
    fetchTimer = null;
    lastFetch = Date.now();
    try {
      items = (await call(`/jobs/${ticket}/results`)).data;
      draw();
    } catch {
      // The next progress tries again.
    }
  }, Math.max(wait, 0));
}

/** Handle one live message: state, progress, recovered or end (spec 6.1). */
function onEvent(name, data, fresh) {
  if (name === "state") {
    job.state = data.state;
  } else if (name === "progress") {
    const before = done();
    job.succeeded = data.succeeded;
    job.failed = data.failed;
    if (!fresh && done() > before) lastChangeAt = performance.now();
    if (job.state === "queued" && done() > 0) job.state = "running";
    refreshItems();
  } else if (name === "recovered") {
    // How long after the last finished text the takeover came: about how
    // long the job stood still. Unknown if this page saw no text finish.
    const seconds = lastChangeAt === null ? null : Math.round((performance.now() - lastChangeAt) / 1000);
    recovered = { pos: data.from_position, seconds };
    if (crashState === "waiting") crashState = "recovered";
  } else if (name === "end") {
    job.state = data.state;
    // A crash asked for (or still being asked for) but never seen: the job
    // ended first.
    if (crashState === "asking" || crashState === "waiting") crashState = "too_late";
    // The times, the error message and the final texts, once settled.
    settle();
  }
  if (job.state === "running" && !job.started_at) job.started_at = new Date().toISOString();
  draw();
}

/** Cancel the job (POST /jobs/{ticket}/cancel) and show the result or the error. */
async function cancel() {
  cancelling = true;
  draw();
  try {
    job = (await call(`/jobs/${ticket}/cancel`, { method: "POST" })).data;
    showError($("cancel-error"), null);
    // The stream's `end` follows; settle() also runs here in case it doesn't.
    settle();
  } catch (error) {
    showError($("cancel-error"), error);
  }
  cancelling = false;
  draw();
}

/**
 * Crash this job's worker on purpose (demo mode only, D127). An earlier
 * takeover's notice stays until a new takeover replaces it.
 */
async function crash() {
  crashState = "asking";
  crashError = null;
  draw();
  try {
    await call(`/demo/crash-worker?ticket=${encodeURIComponent(job.id)}`, { method: "POST" });
    // The job may have ended while the request was on its way (`end` then
    // already said "too late").
    if (crashState === "asking") crashState = "waiting";
  } catch (error) {
    crashState = null;
    crashError = error;
  }
  draw();
}

boot({
  page: "job",
  needsLogin: true,
  start: async () => {
    try {
      const status = await systemStatus();
      atOnce = status.limits.texts_at_once;
      demo = status.demo_mode;
    } catch {
      // Unknown: no "working now" dots (see atOnce).
    }
    try {
      job = (await call(`/jobs/${encodeURIComponent(ticket)}`)).data;
      items = (await call(`/jobs/${encodeURIComponent(ticket)}/results`)).data;
    } catch (error) {
      showError($("job-error"), error);
      $("job-error").append(" ", el("a", { href: "jobs.html", text: t("job.back") }));
      return;
    }
    $("job-view").hidden = false;
    $("cancel").addEventListener("click", cancel);
    $("crash").addEventListener("click", crash);
    $("crash-info-button").addEventListener("click", () => setInfoOpen($("crash-info").hidden));
    // A job that had already finished: final now, or once its worker lets go.
    final = settled(job);
    if (!LIVE.has(job.state) && !final) settle();
    draw();
    stop = watchJob(job.id, {
      onEvent,
      onConnection: (state) => {
        connection = state;
        draw();
      },
    });
    // Keep "minutes left" fresh between messages.
    setInterval(draw, 15_000);
  },
  redraw: draw,
});

window.addEventListener("pagehide", () => stop?.());
