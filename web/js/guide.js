// The first-time guide's memory (D129).
//
// Right after sign-up, New job shows a short three-step guide. Whether it
// should still show is kept in localStorage (the browser's storage that
// survives closing the tab), one entry per username, so hiding it is
// remembered on this computer. It holds no secret, like the language choice
// (D116); if storage is blocked (a private window) the guide simply doesn't
// show.

/** The storage entry for one user's guide. */
function keyFor(user) {
  return `kuyruk.guide.${user}`;
}

/** Switch the guide on for a new account (called once, after sign-up). */
export function showGuideFor(user) {
  try {
    localStorage.setItem(keyFor(user), "show");
  } catch {
    // Storage blocked: no guide, nothing else changes.
  }
}

/** Whether New job should show the guide to this user. */
export function guideWanted(user) {
  try {
    return localStorage.getItem(keyFor(user)) === "show";
  } catch {
    return false;
  }
}

/** Hide the guide for good for this user (the "Hide the guide" button). */
export function hideGuideFor(user) {
  try {
    localStorage.setItem(keyFor(user), "hidden");
  } catch {
    // Storage blocked: it hides now and won't come back anyway.
  }
}
