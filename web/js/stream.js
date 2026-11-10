// Live progress: reading GET /jobs/{ticket}/events (spec 6.1, D64, D67).
//
// The API sends *server-sent events* (SSE): a reply that stays open and
// carries short messages one after another, such as
//
//     event: progress
//     data: {"done": 12, "total": 40, "succeeded": 12, "failed": 0}
//
// Browsers have a built-in reader for these (EventSource), but it can't send
// the login header, and the pass must never go into an address (D67). So the
// stream is read with fetch, which can send the header, and the messages are
// split apart here.
//
// If the connection drops, it is opened again after 1 s, 2 s, 4 s ... (at
// most 10 s), and the stream starts with a fresh snapshot of the job (D64),
// so nothing needs to be remembered between connections. It stops for good
// at the `end` message, when the job doesn't exist (404), or when the pass
// has run out (401, the login page opens).

import { API, authHeader, loginAgain } from "./api.js";

const MAX_WAIT_MS = 10_000;

/** Split one message block ("event: ...\ndata: ...") into its name and data. */
export function parseBlock(block) {
  let name = "message";
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    // Lines starting with ":" are comments, such as ": keep-alive".
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") name = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  try {
    return { name, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}

/**
 * Watch one job's live progress.
 *
 * `onEvent(name, data, fresh)` gets every message; `fresh` is true for the
 * snapshot that starts each connection (so a page can tell "where the job
 * is" from "what just changed"). `onConnection("open" | "lost" |
 * "not_found")` reports the connection. Returns a function that stops it.
 */
export function watchJob(ticket, { onEvent, onConnection = () => {} }) {
  const stopper = new AbortController();
  let stopped = false;

  async function readOnce() {
    const reply = await fetch(`${API}/jobs/${encodeURIComponent(ticket)}/events`, {
      headers: { ...authHeader(), Accept: "text/event-stream" },
      signal: stopper.signal,
    });
    if (reply.status === 401) {
      stopped = true;
      loginAgain(true);
      return true;
    }
    if (reply.status === 404) {
      stopped = true;
      onConnection("not_found");
      return true;
    }
    if (!reply.ok || !reply.body) throw new Error(`stream ${reply.status}`);
    onConnection("open");
    const reader = reply.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    // The snapshot is the first state and progress of each connection.
    let snapshot = 2;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return false; // closed without `end`: connect again
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop();
      for (const block of blocks) {
        const message = parseBlock(block);
        if (!message) continue;
        const fresh = snapshot > 0 && (message.name === "state" || message.name === "progress");
        if (fresh) snapshot -= 1;
        onEvent(message.name, message.data, fresh);
        if (message.name === "end") return true;
      }
    }
  }

  (async () => {
    let attempt = 0;
    while (!stopped) {
      try {
        if (await readOnce()) return;
        attempt = 0;
      } catch (error) {
        if (stopped || error.name === "AbortError") return;
      }
      if (stopped) return;
      onConnection("lost");
      await new Promise((done) => setTimeout(done, Math.min(1000 * 2 ** attempt, MAX_WAIT_MS)));
      attempt += 1;
    }
  })();

  return () => {
    stopped = true;
    stopper.abort();
  };
}
