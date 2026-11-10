// My jobs (jobs.html): your jobs, newest first.
//
// The filter buttons ask GET /jobs?status=<state>; "Show more" asks for the
// next page (limit and offset). A queued or running job opens its live page;
// a finished one opens its results.

import { call, systemStatus } from "../api.js";
import { serverMessage, t, texts as textsWord } from "../i18n.js";
import { boot, clock, el, shortTicket, showError } from "../ui.js";

const list = document.getElementById("job-list");
const more = document.getElementById("more");
const errorBox = document.getElementById("jobs-error");
let filter = "";
let jobs = [];
let pageSize = 50;
let loaded = false;

/** "Sent today, 14:02 · ticket 9ecc62c" (or yesterday, or a date). */
function sentLine(job) {
  const sent = new Date(job.created_at);
  const day = (date) => date.toDateString();
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const values = { time: clock(sent), ticket: shortTicket(job.id) };
  if (day(sent) === day(today)) return t("jobs.sent_today", values);
  if (day(sent) === day(yesterday)) return t("jobs.sent_yesterday", values);
  const date = new Intl.DateTimeFormat(document.documentElement.lang, { day: "numeric", month: "short" }).format(sent);
  return t("jobs.sent_on", { ...values, date });
}

/** The count column: progress, "37 ok · 3 failed", or why it failed. */
function countLine(job) {
  const done = job.succeeded + job.failed;
  if (job.state === "failed" && job.error_message) return serverMessage(job.error_message);
  if (job.state === "done" && job.failed > 0) return t("jobs.count_failed", { ok: job.succeeded, failed: job.failed });
  return t("jobs.count_of", { done, total: job.total });
}

/** Draw the filter buttons and the list (or the "no jobs" line). */
function draw() {
  for (const button of document.querySelectorAll("#filters button")) {
    button.setAttribute("aria-pressed", String(button.dataset.state === filter));
  }
  list.replaceChildren();
  if (loaded && !jobs.length) {
    const empty = el("li", {}, el("p", { class: "empty" }, filter ? t("jobs.empty_filter") : t("jobs.empty"), " "));
    if (!filter) empty.firstChild.append(el("a", { href: "new.html", text: t("jobs.empty_link") }));
    list.append(empty);
    return;
  }
  for (const job of jobs) {
    const live = job.state === "queued" || job.state === "running";
    list.append(
      el("li", {},
        el("a", { href: `${live ? "job" : "results"}.html?ticket=${job.id}` },
          el("span", { class: "job-text" },
            el("strong", { text: t("jobs.job_title", { task: t(`task.${job.task}`), texts: textsWord(job.total) }) }),
            el("span", { class: "job-meta", text: sentLine(job) })),
          el("span", { class: "job-count", text: countLine(job) }),
          el("span", { class: `job-state state-${job.state}`, text: t(`state.${job.state}`) }))),
    );
  }
}

/** Load the first page (fresh) or the next one (more = true). */
async function load(next = false) {
  const query = new URLSearchParams({ limit: String(pageSize), offset: String(next ? jobs.length : 0) });
  if (filter) query.set("status", filter);
  try {
    const { data } = await call(`/jobs?${query}`);
    jobs = next ? jobs.concat(data) : data;
    loaded = true;
    // A full page means there may be more.
    more.hidden = data.length < pageSize;
    showError(errorBox, null);
  } catch (error) {
    showError(errorBox, error);
  }
  draw();
}

boot({
  page: "jobs",
  needsLogin: true,
  start: async () => {
    try {
      pageSize = (await systemStatus()).limits.jobs_page_default;
    } catch {
      // The API's own default page size is used.
    }
    for (const button of document.querySelectorAll("#filters button")) {
      button.addEventListener("click", () => {
        filter = button.dataset.state;
        load();
      });
    }
    more.addEventListener("click", () => load(true));
    await load();
  },
  redraw: draw,
});
