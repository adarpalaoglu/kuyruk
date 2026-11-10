// System status (status.html): GET /status every 5 seconds.
//
// The API's own answer time is measured here, by the page (from sending the
// request to having the reply). Each part shows a word, never only a colour:
// "Working", "Not working", "Unknown".

import { call } from "../api.js";
import { modelName, t } from "../i18n.js";
import { boot, el } from "../ui.js";

const REFRESH_MS = 5000;
let status = null; // the last reply, or null if the API didn't answer
let answeredMs = null;
let checkedAt = null;

const $ = (id) => document.getElementById(id);

/** The AI's line: the demo AI and what it replays, or the real model. */
function aiLine(ai) {
  const model = modelName(ai.model);
  if (ai.backend === "ollama") return t("status.ai_ollama", { model });
  return t(ai.wait_mode === "replay" ? "status.ai_replay" : "status.ai_fixed", { model });
}

/** Each part's name, its line, and whether it is a problem. */
function parts() {
  if (!status) {
    return [["status.api", t("status.not_answering"), true]];
  }
  const worker = status.worker;
  let workerLine;
  if (worker.state === "unknown") workerLine = t("status.worker_unknown");
  else {
    // At least "1 second": "0 seconds ago" reads oddly for a fresh heartbeat.
    const s = Math.max(1, Math.round(worker.last_seen_seconds));
    workerLine = worker.state === "ok" ? t("status.worker_ok", { s }, s) : t("status.worker_down", { s });
    if (worker.state === "ok" && status.running_jobs) {
      workerLine += t("status.on_jobs", { n: status.running_jobs }, status.running_jobs);
    }
  }
  const dbOk = status.database === "ok";
  return [
    ["status.api", t("status.api_ok", { ms: answeredMs }), false],
    ["status.worker", workerLine, worker.state === "down"],
    ["status.database", t(dbOk ? "status.working" : "status.not_answering"), !dbOk],
    ["status.ai", aiLine(status.ai), false],
    [
      "status.queue",
      !dbOk ? t("status.unknown") : status.jobs_waiting ? t("status.queue_some", { n: status.jobs_waiting }, status.jobs_waiting) : t("status.queue_none"),
      false,
    ],
  ];
}

/** Draw the icon, the headline and the list of parts. */
function draw() {
  const list = parts();
  const bad = list.some(([, , isBad]) => isBad);
  $("icon-ok").hidden = bad;
  $("icon-bad").hidden = !bad;
  $("status-title").textContent = t(!status ? "status.down_title" : bad ? "status.bad_title" : "status.ok_title");
  drawChecked();
  $("parts").replaceChildren(
    ...list.map(([name, detail, isBad]) =>
      el("div", {}, el("dt", { text: t(name) }), el("dd", { class: isBad ? "is-bad" : null, text: detail }))),
  );
}

/** Write "Checked N seconds ago." (updated every second). */
function drawChecked() {
  if (checkedAt === null) return;
  const seconds = Math.floor((Date.now() - checkedAt) / 1000);
  $("checked").textContent = seconds < 1 ? t("status.checked_now") : t("status.checked", { n: seconds }, seconds);
}

/** Ask GET /status, timing how long the API takes to answer. */
async function check() {
  const sent = performance.now();
  try {
    status = (await call("/status", { auth: false })).data;
    answeredMs = Math.max(1, Math.round(performance.now() - sent));
  } catch {
    status = null;
  }
  checkedAt = Date.now();
  draw();
}

boot({
  page: "status",
  start: async () => {
    await check();
    setInterval(check, REFRESH_MS);
    setInterval(drawChecked, 1000);
  },
  redraw: draw,
});
