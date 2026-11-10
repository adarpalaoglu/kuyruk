// New job (new.html): choose a task, give the texts, see the cost, send.
//
// - The texts are the lines of the box (empty lines are skipped). A file can
//   be uploaded instead: .txt (one text per line), .csv (texts in the first
//   column) or .json (a list of texts). JSON can hold texts with line
//   breaks; they are sent whole as long as the box isn't edited.
// - The cost line asks POST /jobs/estimate a moment after typing stops
//   (D119): the same count the budget uses, nothing saved.
// - Send uses one Idempotency-Key per exact content (D20): a double-click or
//   a second press for the same texts gets the same ticket, never two jobs.

import { ApiError, call } from "../api.js";
import { t, texts as textsWord } from "../i18n.js";
import { boot, showError } from "../ui.js";

const box = document.getElementById("textbox");
const costLine = document.getElementById("cost-line");
const sendButton = document.getElementById("send");
const sendError = document.getElementById("send-error");
const uploadNote = document.getElementById("upload-note");
const fileInput = document.getElementById("file");

// Texts from an uploaded file, used until the box is edited.
let uploaded = null;
let uploadedName = "";
let estimate = null; // the last cost reply, or an ApiError
let estimating = false;
let timer = null;
let pending = null; // AbortController of the estimate in flight
let sending = false;
// The Idempotency-Key and the content it was made for.
let key = null;
let keyFor = "";

/** The chosen task: "normalise", "count_tokens" or "llm". */
function task() {
  return document.querySelector("input[name=task]:checked").value;
}

/** The texts to send: the uploaded ones, or the box's non-empty lines. */
function currentTexts() {
  if (uploaded) return uploaded;
  return box.value.split(/\r?\n/).filter((line) => line.trim() !== "");
}

/** Write the cost line from the last estimate. */
function drawCost() {
  const list = currentTexts();
  costLine.classList.remove("fail");
  if (!list.length) {
    costLine.textContent = t("new.empty");
    return;
  }
  const count = textsWord(list.length);
  if (estimating || !estimate) {
    costLine.textContent = t("new.counting", { texts: count });
  } else if (estimate instanceof ApiError) {
    costLine.textContent = count;
  } else if (estimate.tokens_needed === null) {
    costLine.textContent = t("new.cost_free", { texts: count });
  } else {
    const over = estimate.tokens_needed > estimate.left_today;
    costLine.textContent = t(over ? "new.cost_over" : "new.cost_llm", {
      texts: count,
      tokens: estimate.tokens_needed,
      left: estimate.left_today,
    });
    costLine.classList.toggle("fail", over);
  }
}

/** Ask for the cost of the current texts (the last request wins). */
async function askEstimate() {
  pending?.abort();
  const list = currentTexts();
  if (!list.length) {
    estimating = false;
    estimate = null;
    drawCost();
    showError(sendError, null);
    return;
  }
  pending = new AbortController();
  estimating = true;
  drawCost();
  try {
    const { data } = await call("/jobs/estimate", {
      method: "POST",
      json: { task: task(), texts: list },
      signal: pending.signal,
    });
    estimate = data;
    showError(sendError, null);
  } catch (error) {
    if (error.name === "AbortError") return;
    // A broken job (e.g. a text too long) is shown now, before Send.
    estimate = error;
    showError(sendError, error);
  }
  estimating = false;
  drawCost();
}

/** Ask again a moment after the last change, not on every key press. */
function changed() {
  clearTimeout(timer);
  estimating = true;
  drawCost();
  timer = setTimeout(askEstimate, 400);
}

/** Write the line under the task switch that says what the task does. */
function drawTask() {
  document.getElementById("task-note").textContent = t(`new.note_${task()}`);
}

/** Write Send (or Sending…), disabled while a send is on its way. */
function drawSendButton() {
  sendButton.textContent = t(sending ? "new.sending" : "new.send");
  sendButton.disabled = sending;
}

/** Show the note about an uploaded file whose texts contain line breaks. */
function drawUploadNote() {
  uploadNote.hidden = !uploaded;
  if (uploaded) uploadNote.textContent = t("new.upload_note", { name: uploadedName, texts: textsWord(uploaded.length) });
}

/** Read a .txt, .csv or .json file into a list of texts (null if unreadable). */
function parseFile(name, content) {
  const lower = name.toLowerCase();
  if (lower.endsWith(".json")) {
    try {
      const value = JSON.parse(content);
      const list = Array.isArray(value) ? value : value && Array.isArray(value.texts) ? value.texts : null;
      if (!list || !list.every((item) => typeof item === "string")) return null;
      return list.filter((item) => item.trim() !== "");
    } catch {
      return null;
    }
  }
  if (lower.endsWith(".csv")) {
    const rows = parseCsv(content).map((row) => row[0] ?? "").filter((cell) => cell.trim() !== "");
    // A first row that only names the column ("text") is a header.
    if (rows.length && /^(text|texts|metin)$/i.test(rows[0].trim())) rows.shift();
    return rows;
  }
  return content.split(/\r?\n/).filter((line) => line.trim() !== "");
}

/** Split CSV text into rows of cells ("quoted, cells" may hold commas and line breaks). */
function parseCsv(content) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < content.length; i += 1) {
    const c = content[i];
    if (quoted) {
      if (c === '"' && content[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(cell);
      cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && content[i + 1] === "\n") i += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += c;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

/** Send the job (POST /jobs) and open its live page, or show why it was refused. */
async function send() {
  if (sending) return;
  const job = { task: task(), texts: currentTexts() };
  const content = JSON.stringify(job);
  // Same content as last time → same key, so the API returns the same ticket.
  if (content !== keyFor) {
    key = crypto.randomUUID();
    keyFor = content;
  }
  sending = true;
  drawSendButton();
  showError(sendError, null);
  try {
    const { data } = await call("/jobs", { method: "POST", json: job, headers: { "Idempotency-Key": key } });
    location.assign(`job.html?ticket=${data.id}`);
  } catch (error) {
    showError(sendError, error);
    sending = false;
    drawSendButton();
  }
}

boot({
  page: "new",
  needsLogin: true,
  start: async () => {
    drawTask();
    drawSendButton();
    drawCost();
    box.addEventListener("input", () => {
      uploaded = null;
      drawUploadNote();
      changed();
    });
    for (const radio of document.querySelectorAll("input[name=task]")) {
      radio.addEventListener("change", () => {
        drawTask();
        changed();
      });
    }
    document.getElementById("samples").addEventListener("click", async () => {
      const reply = await fetch("data/sample_reviews.json");
      box.value = (await reply.json()).join("\n");
      uploaded = null;
      drawUploadNote();
      changed();
    });
    document.getElementById("upload").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      fileInput.value = "";
      if (!file) return;
      const list = parseFile(file.name, await file.text());
      if (!list) {
        uploaded = null;
        sendError.hidden = false;
        sendError.classList.add("is-error");
        sendError.textContent = t("new.upload_bad");
        return;
      }
      uploadedName = file.name;
      // The box shows each text on one line; line breaks inside a text are
      // shown as spaces, and the uploaded list itself is what gets sent.
      box.value = list.map((text) => text.replace(/\s*\r?\n\s*/g, " ")).join("\n");
      uploaded = list.some((text) => /\r|\n/.test(text)) ? list : null;
      drawUploadNote();
      changed();
    });
    sendButton.addEventListener("click", send);
    if (currentTexts().length) changed();
  },
  redraw: () => {
    drawTask();
    drawSendButton();
    drawCost();
    drawUploadNote();
  },
});
