// "Send the texts without a result as a new job" (D128), shared by the job
// page, the results page and New job.
//
// A finished job (done, failed or cancelled) can leave texts without a
// result: texts that never started (still "waiting", e.g. after Cancel) and
// texts the AI gave up on ("failed"). The button opens New job with exactly
// those texts and the same task (new.html?from=<ticket>), so the usual cost
// line shows before anything is sent. The old job is never changed: a
// finished job stays finished (spec 4).
//
// Right after a cancel, the texts already with the AI are still finishing
// and look "waiting" until they do. The job reply's `worker_busy` says a
// worker still holds the job (D130); until it lets go, the button stays
// hidden, so no text can be sent to the AI twice.

import { call } from "./api.js";
import { t } from "./i18n.js";

const FINISHED = new Set(["done", "failed", "cancelled"]);

/** Whether a finished job's texts can no longer change (D130). */
export function settled(job) {
  return FINISHED.has(job.state) && !job.worker_busy;
}

/** The texts of a settled job that have no result (empty otherwise). */
export function textsWithoutResult(job, items) {
  if (!settled(job)) return [];
  return items.filter((item) => item.state !== "ok");
}

/** Wait the given number of milliseconds. */
function pause(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Read the job and its texts (together); while it is finished but a worker
 * still holds it, read them again every second until it lets go. A job
 * still running is returned at once. Errors are thrown to the caller.
 */
export async function readSettled(ticket) {
  for (;;) {
    const id = encodeURIComponent(ticket);
    const [jobReply, itemsReply] = await Promise.all([call(`/jobs/${id}`), call(`/jobs/${id}/results`)]);
    const job = jobReply.data;
    if (!FINISHED.has(job.state) || !job.worker_busy) return { job, items: itemsReply.data };
    await pause(1000);
  }
}

/**
 * Show or hide the button and its lines for this job: `box` holds the link
 * (#resend-link), the counts ("12 never started · 3 failed",
 * #resend-counts) and the line saying what it does (#resend-hint).
 * `fresh` says the texts were read after the job settled, so they are
 * final; otherwise the button stays hidden.
 */
export function drawResend(box, job, items, fresh = true) {
  const left = fresh ? textsWithoutResult(job, items) : [];
  box.hidden = left.length === 0;
  if (!left.length) return;
  const waiting = left.filter((item) => item.state === "waiting").length;
  const failed = left.length - waiting;
  const link = box.querySelector("#resend-link");
  link.href = `new.html?from=${job.id}`;
  link.textContent = t("job.resend");
  const parts = [];
  if (waiting) parts.push(t("job.resend_waiting", { n: waiting }));
  if (failed) parts.push(t("job.resend_failed", { n: failed }));
  box.querySelector("#resend-counts").textContent = parts.join(" · ");
  box.querySelector("#resend-hint").textContent = t("job.resend_hint");
}
