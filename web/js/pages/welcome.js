// The Welcome page (index.html): fill in the story numbers. "Try it
// yourself" opens Create your account, or goes straight to New job for
// someone already logged in.

import { getPass } from "../session.js";
import { loadStoryNumbers } from "../story.js";
import { boot } from "../ui.js";

boot({
  page: "welcome",
  start: async () => {
    if (getPass()) document.getElementById("try-link").href = "new.html";
    await loadStoryNumbers();
  },
});
