# Kuyruk interface mockup (approved 10 Nov 2026)

The approved design for Slice 17. Live canvas: https://claude.ai/artifact/NUDMpBVkY6gfoRjRrXjpGF

These `.dc.html` files are the design source, not runnable pages: each is one screen,
written as HTML with inline styles inside `<x-dc>` (a design-canvas format). Lists use
`<sc-for list="{{ x }}" as="item">` with the sample data in the `renderVals()` script at the
bottom of each file. Read them as the exact specification of layout, text, sizes and colours,
and rebuild them in the interface's own technology.

## Screens

| File | Screen | Uses |
|---|---|---|
| Main.dc.html | Welcome: the problem → fix → result story | public |
| Login.dc.html | Sign up / log in. Since Slice 17b two pages (D126): `signup.html` as drawn plus a confirm-password box and a "Create account" button; `login.html` the same without the confirm box and hint, with a "Log in" button | POST /register, POST /token |
| NewJob.dc.html | New job: task switch, text box, one Send button; since Slice 17b a first-time guide after sign-up (D129) | POST /jobs (+ Idempotency-Key), token count for the cost line |
| Jobs.dc.html | My jobs, with the account menu open (budget, status, API docs, log out) | GET /jobs?status=, GET /me/budget |
| Job.dc.html | One job, live: big count, one status sentence, one dot per text, recovered notice, cancel; since Slice 17b also "Simulate a crash" in demo mode (D127) and, on a finished job with texts left without a result, "Send the texts without a result as a new job" (D128; also on Results) | GET /jobs/{ticket}, GET /jobs/{ticket}/events (SSE), POST /jobs/{ticket}/cancel |
| Results.dc.html | Results: original beside result, Download CSV / JSON | GET /jobs/{ticket}/results |
| Status.dc.html | System status | GET /health and what the API can report |
| Behind.dc.html | The experiments: one finding per section + "Simulate a crash" | POST /demo/crash-worker (demo mode only), job events |

## Design rules (keep them)

- Inspiration: "as if Steve Jobs designed it today". One thing and one obvious action per screen. Remove until it breaks.
- Font: Inter (400/500/600/700). Big, confident headings with tight letter-spacing.
- Colours: text #1D1D1F; secondary #6E6E73; faint numbers #86868B; lines #E8E8ED / #D2D2D7; bands #F5F5F7; dark bands #1D1D1F with #A1A1A6 text; one accent blue #2347D6 (hover #1A36A8); failure text #B42318.
- Buttons: pill-shaped (border-radius 999px); the primary action is blue.
- Budget, system status, API docs and log out live in ONE account menu (avatar button), not in the main navigation.
- Small "DEMO" pill next to the logo in demo mode. Small "TR" / "EN" switch in the header; ship both languages.
- States are never shown by colour alone: always a word too.
- Accessibility: real buttons, links and labelled inputs; aria-live for the job status sentence; contrast at least 4.5:1 for normal text.
- Every page works at phone width: menus wrap, grids become one column.

## The story every visitor should leave with

"I never thought that would be a problem → that's a clever fix → those numbers prove it works."
The Welcome page and The experiments page both follow problem → fix → measured result.
Every number on them must come from docs/experiments.md (they were checked on 10 Nov 2026);
in the build, read them from the data or keep them in one place so --charts-only's number check can cover them.
