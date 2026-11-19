# Admin page layout (full width, phone friendly)

Every admin and sponsor page fills the window. The rule lives in one place: `frontend/src/components/layout/Layout.tsx` (and `SponsorLayout.tsx`).

- `<main>` is `w-full min-w-0 px-4 sm:px-6 lg:px-8 py-6`. No `max-w-*`. Pages must not add their own `max-w-Nxl mx-auto` root wrapper; use `w-full`. Narrow a form or a paragraph inside the page if it reads badly wide, not the whole page.
- **Do not change that padding on its own.** The dark pages (Users, Archives, Alerts, Rules, SongRequests, public Hosts) go edge to edge by cancelling it with `-mx-4 sm:-mx-6 lg:-mx-8 -my-6`. Change one side and those pages get a gap or a sideways scroll.
- The nav is `sticky top-0`, so the header stays put while the page scrolls. The control room is the exception: it is an app shell with its own panes (see [control-room](control-room.md)).
- Tables on phones: either wrap in `overflow-x-auto` (scrolls inside the card), or, for the tables people use on a phone, hide secondary columns below `md`/`lg` and repeat the essentials under the title. The Library table (`Assets.tsx`) does the second: it is `table-fixed`, so percentage widths must be `md:w-[..%]` classes, not inline styles, or the fixed columns squeeze the title to nothing on a phone. Header row, filter row and body cells must hide the same columns.

- Toolbars and page headers are `flex flex-wrap` with a gap, and a `flex-1` text box next to a button needs `min-w-0`. These were what still scrolled sideways on phones after the tables were fixed.
- Phones get 16px text in every text box and select (rule in `index.css`, `pointer: coarse` and under 768px). iPhone Safari zooms the page on tapping a smaller one and does not zoom back. Do not undo it with a smaller `text-*` on an input.

## Menus inside table cells

Table cells cut their content (`truncate`), so a drop-down drawn inside a cell is cut too (the Library sponsor search box was). Draw cell menus with `components/FloatingMenu.tsx`: it puts the menu on `document.body` at the button's place, keeps it inside the window and closes on scroll. A chip in a cell gets `block max-w-full truncate` and a `title` with the full name. Check: `frontend/e2e/library-display.mjs`.

## Tables on the phone

A table with more columns than a phone holds gets `className="phone-cards ..."` and a `data-label` on each cell that needs its column's name. Under 640 px the skin (`styles/qnet-site.css`) shows each row as a card: one line per cell, columns that were hidden on small screens come back, and the last cell (the actions) is a row of buttons in sight. Used by Blackouts, Show Archives, Users, Rules and Manage Stations. Do not give such tables a sideways scroll on the phone: the action buttons end up out of sight.

The staff layouts leave room under each page on the phone (`pb-36`) so the last controls scroll clear of the floating bug button. Page headings (`h1`) are as wide as their words on every page. Check: `frontend/e2e/display-findings.mjs`.

## Checking it

`frontend/e2e/admin-layout-audit.mjs` logs in to a running site and backend, opens every admin page at 1920x1080 and iPhone size, and fails on: sideways page scroll, content using under 90% of a desktop window, nav not staying at the top, script errors, failed API calls. It runs at 1920, iPhone 13 (390) and iPhone SE (320). A phone browser widens its layout viewport to fit content that sticks out, so `innerWidth` hides the overflow: the script compares against the device width. (The first version compared against `innerWidth` and passed pages that real Safari showed scrolling sideways.) `SHOTS_DIR=...` saves screenshots; look at them, the checks do not catch ugly.

Needs a real backend (not the fake one the control-room test uses). Local file storage makes `/assets/:id/download` return 401 on the asset detail page; that is the test setup, not the page.

## Admin flows suite (`frontend/e2e/admin-flows.mjs`)

Clicks through every admin, sponsor and public screen as each role against a real backend and Postgres (`E2E_DATABASE_URL`, seeded by `e2e/admin-flows-seed.py` with made-up users; `ONLY=<sections>` runs a part). Site bugs it finds stay as tests listed in `KNOWN_BUGS` at the top (status KNOWN-BUG; FIXED? when one starts passing). Things that surprised: the backend allows 10 sign-ins a minute, so every sign-in takes a `loginSlot()`; local file storage serves a login-protected download URL, so audio needs `proxyAudio`; a release-reload uses the tab's one reload per 2 minutes, so a stale-code check needs its own tab; POSTs to routes declared as `"/"` answer 307 first, so match on `status !== 307`. The a11y pass keeps the pages with known gaps in `A11Y_GAP_PAGES`; every other page must scan clean.

## Row actions on phone cards, and the bug button

Under 640 px the last cell of a `phone-cards` row is a row of real buttons: 44 px each way
(set in `qnet-site.css`, whatever the desktop link looked like). Put `data-danger` on the
Delete button: it is drawn last, apart from the others, in red, and every Delete asks first.
Cards keep 56 px clear on the right, where the bug button floats on phones (it sat on the
left and covered card labels and Library checkboxes). Check: `e2e/display-findings.mjs` 12, 13.

## Times are the station's clock

A time on a staff page is shown, and typed, on the clock of the station it belongs to, with
the zone named ("4:12 PM EST"), never the viewer's clock or UTC. Use `lib/stationTime.ts`:
`formatInZone`, `toZonedInput` and `fromZonedInput` (for `datetime-local` boxes). Blackouts
used to put the UTC time in the edit box and save it as the viewer's local time, so every
edit moved the window. Check: `e2e/display-findings.mjs` 14.
Raffles (Listener CRM) have a Station choice; their times use that station's clock, and a
raffle for every station uses the first station's. `test_raffle_times_use_the_station_clock`
(backend) runs `lib/stationTime.ts` in Node 22 with the browser clock on Jerusalem; the
backend CI job installs Node 22 for it.

## When the sign-in runs out

If the renewal (refresh token) has expired too, `api/client.ts` sends the page to the login
page with `?expired=1`, or `?expired=action` when a change was refused; the login pages
(admin and sponsor) then say "Your sign-in has expired. Please sign in again." and, for a
change, that the last action was not done. Nothing is replayed later. Check:
`e2e/staff-sessions.mjs` V3-14.
