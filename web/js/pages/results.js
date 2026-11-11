// Results (results.html?ticket=...): each text's original beside its
// result, and the downloads.
//
// The downloads are made in the browser from GET /jobs/{ticket}/results, so
// they hold exactly what the API returned: position, original_text, result,
// state and error_message for every text. The CSV starts with a byte-order
// mark (BOM), a hidden marker that tells Excel the file is UTF-8, so Turkish
// letters show correctly.
//
// A finished job with texts left without a result also offers "Send the
// texts without a result as a new job" (js/resend.js, D128), once no worker
// holds it any more (D130): right after a cancel the page reads the job
// again every second until its last texts have finished.

import { call } from "../api.js";
import { serverMessage, t, texts as textsWord } from "../i18n.js";
import { drawResend, readSettled, settled } from "../resend.js";
import { boot, el, shortTicket, showError } from "../ui.js";

const ticket = new URLSearchParams(location.search).get("ticket") || "";
let job = null;
let items = [];
// Whether `items` are final (the job finished and its worker let go, D130).
let final = false;

const $ = (id) => document.getElementById(id);

/** The big line, e.g. "Done. 37 summaries.", by state and task. */
function headline() {
  const done = job.succeeded + job.failed;
  if (job.state === "done") return t(`results.done_${job.task}`, { n: job.succeeded }, job.succeeded);
  if (job.state === "failed") return t("results.stopped", { done, total: job.total });
  if (job.state === "cancelled") return t("results.cancelled", { done, total: job.total });
  return t("results.so_far", { done, total: job.total });
}

/** The line under it: how many texts failed (or why the job stopped). */
function failedNote() {
  if (!job.failed) return job.state === "failed" ? serverMessage(job.error_message || "") : "";
  const key = job.task === "llm" ? "results.failed_some_llm" : "results.failed_some";
  return t(key, { n: job.failed }, job.failed);
}

/** One text's result column: the result, "Waiting", or why it failed. */
function resultText(item) {
  if (item.state === "ok") return item.result;
  if (item.state === "waiting") return t("results.waiting");
  return t(job.task === "llm" ? "results.not_done_llm" : "results.not_done", {
    reason: item.error_message || t("results.unknown_reason"),
  });
}

/** Draw the heading and one row per text. */
function draw() {
  if (!job) return;
  $("kicker").textContent = t("results.kicker", {
    task: t(`task.${job.task}`),
    texts: textsWord(job.total),
    ticket: shortTicket(job.id),
  });
  $("headline").textContent = headline();
  const note = failedNote();
  $("sub").textContent = note;
  $("sub").hidden = !note;
  $("col-result").textContent = t(`results.col_${job.task}`);
  drawResend($("resend"), job, items, final);
  $("rows").replaceChildren(
    ...items.map((item) =>
      el("div", { class: `results-row is-${item.state}`, role: "row" },
        el("span", { class: "n", role: "cell", text: String(item.position) }),
        el("span", { class: "original", role: "cell", text: item.original_text }),
        el("span", { class: "result", role: "cell", text: resultText(item) }))),
  );
}

/** Offer `content` as a file named `name` (the browser's download). */
function download(name, type, content) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = el("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * One CSV cell: quoted, with quotes inside doubled (the CSV rule).
 *
 * A text starting with = + - @ (or a tab or line break) would be run as a
 * formula when the file is opened in Excel; a leading apostrophe makes Excel
 * show it as plain text instead ("CSV injection"). The JSON download keeps
 * every text exactly as it is.
 */
function cell(value) {
  let text = String(value ?? "");
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

const COLUMNS = ["position", "original_text", "result", "state", "error_message"];

/** Download every text as a CSV file (opens in Excel). */
function downloadCsv() {
  const lines = [COLUMNS.join(",")].concat(items.map((item) => COLUMNS.map((name) => cell(item[name])).join(",")));
  download(`kuyruk-${shortTicket(job.id)}.csv`, "text/csv;charset=utf-8", `﻿${lines.join("\r\n")}\r\n`);
}

/** Download every text as a JSON file, exactly as the API returned it. */
function downloadJson() {
  download(`kuyruk-${shortTicket(job.id)}.json`, "application/json", `${JSON.stringify(items, null, 2)}\n`);
}

boot({
  page: "results",
  needsLogin: true,
  start: async () => {
    const id = encodeURIComponent(ticket);
    try {
      [job, items] = (await Promise.all([call(`/jobs/${id}`), call(`/jobs/${id}/results`)])).map((reply) => reply.data);
    } catch (error) {
      showError($("results-error"), error);
      $("results-error").append(" ", el("a", { href: "jobs.html", text: t("job.back") }));
      return;
    }
    $("results-head").hidden = false;
    $("results-table").hidden = false;
    $("csv").addEventListener("click", downloadCsv);
    $("json").addEventListener("click", downloadJson);
    final = settled(job);
    draw();
    // Finished, but a worker still holds it: wait for its last texts.
    if (["done", "failed", "cancelled"].includes(job.state) && !final) {
      readSettled(ticket)
        .then((latest) => {
          ({ job, items } = latest);
          final = settled(job);
          draw();
        })
        .catch(() => {});
    }
  },
  redraw: draw,
});
