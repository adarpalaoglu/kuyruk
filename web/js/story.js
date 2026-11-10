// The story numbers for Welcome and The experiments (D120).
//
// They are read from data/findings.json, which bench/web_data.py writes from
// the measured CSV files; no number is typed into the pages. The limits
// (e.g. "up to 1,000 texts") come from GET /status, i.e. from the settings.

import { systemStatus } from "./api.js";
import { apply, share } from "./i18n.js";

/** Load the story numbers and limits, share them with every text, redraw. */
export async function loadStoryNumbers() {
  const reply = await fetch("data/findings.json");
  share(await reply.json());
  // zero: the crash timeline's first label ("0 s").
  share({ zero: 0 });
  apply();
  try {
    const status = await systemStatus();
    share({ max_texts: status.limits.max_texts_per_job, stale: status.limits.stale_after_seconds });
  } catch {
    // Without the API the limits show a dash; the story still reads.
  }
  apply();
}
