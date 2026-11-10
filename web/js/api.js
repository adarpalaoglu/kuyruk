// Talking to Kuyruk's API: one place for every request the interface sends.
//
// The interface is served from its own address (http://localhost:8080) and
// the API from another (http://localhost:8000). The API's address comes from
// /config.js, which the web server writes from the WEB_API_URL setting, so
// it is never typed into the pages. The API allows calls only from the
// interface's address (CORS, D121).
//
// Every error reply has one shape, {"error": {"code", "message"}}, plus an
// X-Request-ID header; `call` turns it into an ApiError carrying all three,
// so a screen can show the message and the request ID. A 401 on a request
// that needed the login means the pass ran out: the pass is forgotten and
// the login page opens with a "log in again" note.

import { clearSession, getPass } from "./session.js";

const configured = window.KUYRUK_CONFIG && window.KUYRUK_CONFIG.apiUrl;
/** The API's address, without a trailing slash. */
export const API = String(configured || "http://localhost:8000").replace(/\/+$/, "");

/** An error reply from the API, or no reply at all (`network`). */
export class ApiError extends Error {
  constructor({ status = 0, code = "error", message = "", requestId = null, retryAfter = null, network = false }) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
    this.network = network;
  }
}

/** The page to come back to after logging in: this page, if it is safe. */
export function currentPage() {
  const name = location.pathname.split("/").pop() || "index.html";
  return name + location.search;
}

/**
 * Only pages of this site may be a "come back to" target, never another
 * website: a link such as login.html?next=https://evil.example must not send
 * someone there after logging in.
 */
export function safeNext(next) {
  return /^[a-z]+\.html(\?[\w=&%.-]*)?(#[\w-]*)?$/.test(next || "") ? next : "new.html";
}

/** The pass ran out (or is missing): forget it and open the login page. */
export function loginAgain(expired = true) {
  clearSession();
  const query = new URLSearchParams({ next: currentPage() });
  if (expired) query.set("expired", "1");
  location.assign(`login.html?${query}`);
}

/** The standard login header, for code that calls fetch itself. */
export function authHeader() {
  return { Authorization: `Bearer ${getPass()}` };
}

/**
 * Send one request to the API and return {data, headers, status}.
 *
 * `json` is sent as JSON, `form` as a login form; `auth` (on by default)
 * adds the login header. Throws an ApiError for an error reply or no reply.
 */
export async function call(path, { method = "GET", json, form, headers = {}, auth = true, signal } = {}) {
  const sent = { ...headers };
  if (auth) {
    if (!getPass()) {
      loginAgain(false);
      throw new ApiError({ status: 401, code: "not_authenticated" });
    }
    Object.assign(sent, authHeader());
  }
  let body;
  if (json !== undefined) {
    sent["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = new URLSearchParams(form);
  }
  let reply;
  try {
    reply = await fetch(API + path, { method, headers: sent, body, signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError({ network: true, code: "network" });
  }
  const requestId = reply.headers.get("X-Request-ID");
  const text = await reply.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!reply.ok) {
    const error = data && data.error ? data.error : {};
    const failure = new ApiError({
      status: reply.status,
      code: error.code || "error",
      message: error.message || reply.statusText,
      requestId,
      retryAfter: reply.headers.get("Retry-After"),
    });
    if (reply.status === 401 && auth) loginAgain(true);
    throw failure;
  }
  return { data, headers: reply.headers, status: reply.status };
}

let statusPromise = null;

/**
 * GET /status once per page (demo mode, limits): shared by every part. A
 * failed answer is forgotten, so the next part that asks tries again.
 */
export function systemStatus() {
  statusPromise ??= call("/status", { auth: false })
    .then((reply) => reply.data)
    .catch((error) => {
      statusPromise = null;
      throw error;
    });
  return statusPromise;
}
