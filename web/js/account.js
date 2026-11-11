// What the Create your account page (signup.html) and the Log in page
// (login.html) share (D126): logging in and keeping the pass, and the
// "come back to" page that both pass on to each other.

import { call, safeNext } from "./api.js";
import { saveSession } from "./session.js";

const query = new URLSearchParams(location.search);

/** The page to open after logging in (only a page of this site, D116). */
export function nextPage() {
  return safeNext(query.get("next"));
}

/**
 * The other account page's address, keeping "come back to" (?next=), so
 * someone sent to log in who switches to sign-up still comes back after.
 */
export function otherPage(name) {
  const next = query.get("next");
  return next ? `${name}?${new URLSearchParams({ next })}` : name;
}

/** Log in (POST /token) and keep the pass; then the next page opens. */
export async function logIn(username, secret) {
  const { data } = await call("/token", { method: "POST", form: { username, password: secret }, auth: false });
  // Usernames are stored in lowercase (D43); show them that way too.
  saveSession(data.access_token, username.toLowerCase());
  location.assign(nextPage());
}
