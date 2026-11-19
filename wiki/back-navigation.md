# Back navigation ("back to where I was")

Shmuel 2026-11-19: "clear navigation that leads user back to wherever they came from … if they clicked into spot from main dashboard, back > goes back to dashboard with the last selection they clicked highlighted". He works on an iPhone, where Safari's own back is easy to miss.

## What the person sees

- A **"‹ Back to …"** line under the menu on every staff page except the control room (home) and login. It is sticky under the menu (`top-12`), so it stays one tap away on a long page.
- The words name the page Back goes to: its heading when it was left ("Back to Acme Bakery", "Back to Sponsors"), else a name from `pageName()` / `PageViewTracker.pageTitle()`.
- Coming back (the Back line, the browser's back, an iPhone swipe) puts the page as it was: scrolled to the same place, filters and search kept, the control room's Sponsor / Song panel open again on the same station, and the **row and control last tapped lit up** (a yellow flash, then a quiet yellow outline that stays until another row is tapped, so a row behind a reopened panel is still marked when the panel closes).

## How it works (frontend only)

- `lib/navMemory.ts`: one sessionStorage record per tab (`kbr_nav_v1`), keyed by React Router's history index (`window.history.state.idx`). Per entry: address, heading, scrollY, `row` and `target` signatures of the last tap. Also page state (`useNavState`) and the ids of things deleted in this tab.
- `components/layout/BackNav.tsx`: `NavTracker` (in `Layout`) notes taps in the capture phase (before a row's own navigate), scroll, and restores on POP; `BackBar` is the visible line.
- `hooks/useNavState.ts`: `useState` kept with the history entry. A PUSH (fresh visit) starts from the initial value; POP/REPLACE read it back. Used by the control room (`pane`, the open song/sponsor panel **with its station**: a panel never reopens on another station) and the Library (search fields, page, page size). Pages whose filters live in the address (Sponsors `?show=&q=`, client page `?tab=`) need nothing.
- Finding the tapped row again: `data-nav-id` first (client rows `client-<id>`, today's sponsorship rows `sponsor-play-<entry>`, queue rows `queue-<entry>`), else `data-testid` + words, else tag + words (first 32 characters when counts in the row changed). Add `data-nav-id` to a list's rows when its words are not unique.
- Where Back goes: the nearest earlier entry in this tab that is a **different page** (a tab or filter change on one page is not "where I came from") and was not deleted. With no such entry (page opened from a link, a bookmark, a new tab) it goes to the natural parent (`parentOf`: client -> Sponsors, item -> Library, `?sponsor=<id>` pages -> that client, other pages -> the role's home) with `replace`, and lights the link to the page it came from.
- Deletes: `api/client.ts` notes every successful `DELETE …/<uuid>` (the thing itself, not `…/<uuid>/station-block`). Back steps over pages about it; the browser's back onto one steps on past it.
- New pages open at the top (a PUSH to a different path scrolls to 0); `history.scrollRestoration` is manual.
- Dialogs are not history entries: closing one leaves the page beneath as it was (checked with the contract Edit dialog).

## Tests

- `frontend/e2e/back-navigation.mjs` (real backend, Chromium, iPhone 13 + desktop, guard): list -> client -> Back; control room on each of the three stations -> Sponsors -> play -> Sponsor page -> Back and browser back; another station chosen meanwhile; direct link; delete; Library; dialog; Back line on pages without sideways scroll.
- `frontend/e2e/webkit/back-navigation-webkit.py`: the same main flows in WebKitGTK (Safari's engine) at 450 px.
- Seed: `e2e/walkthrough-seed.py`, then a few clients with contracts on each station whose dayparts include the rest of today (evening/overnight late in the day), so today's Sponsors list has plays.
