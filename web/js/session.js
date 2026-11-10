// The login pass, kept for this browser tab only (D116, D67).
//
// After logging in, Kuyruk gives the browser a *pass* (a JWT, valid 30
// minutes) that proves who you are on every request. It is kept in
// sessionStorage, which the browser empties when the tab is closed, and
// never in localStorage (which would keep it on the computer) or in an
// address (which ends up in logs and history). The username is kept next to
// it only to show "Signed in as ..." in the account menu.

const PASS = "kuyruk.pass";
const USER = "kuyruk.user";

/** The saved pass, or null if not logged in (or storage is blocked). */
export function getPass() {
  try {
    return sessionStorage.getItem(PASS);
  } catch {
    return null;
  }
}

/** The username shown in the account menu. */
export function getUser() {
  try {
    return sessionStorage.getItem(USER) || "";
  } catch {
    return "";
  }
}

/** Keep the pass and the username for this tab. */
export function saveSession(pass, username) {
  sessionStorage.setItem(PASS, pass);
  sessionStorage.setItem(USER, username);
}

/** Forget the pass (log out, or the pass ran out). */
export function clearSession() {
  try {
    sessionStorage.removeItem(PASS);
    sessionStorage.removeItem(USER);
  } catch {
    // Nothing was stored.
  }
}
