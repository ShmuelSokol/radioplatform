# Admin page layout (full width, phone friendly)

Every admin and sponsor page fills the window. The rule lives in one place: `frontend/src/components/layout/Layout.tsx` (and `SponsorLayout.tsx`).

- `<main>` is `w-full min-w-0 px-4 sm:px-6 lg:px-8 py-6`. No `max-w-*`. Pages must not add their own `max-w-Nxl mx-auto` root wrapper; use `w-full`. Narrow a form or a paragraph inside the page if it reads badly wide, not the whole page.
- **Do not change that padding on its own.** The dark pages (Users, Archives, Alerts, Rules, SongRequests, public Hosts) go edge to edge by cancelling it with `-mx-4 sm:-mx-6 lg:-mx-8 -my-6`. Change one side and those pages get a gap or a sideways scroll.
- The nav is `sticky top-0`, so the header stays put while the page scrolls. The control room is the exception: it is an app shell with its own panes (see [control-room](control-room.md)).
- Tables on phones: either wrap in `overflow-x-auto` (scrolls inside the card), or, for the tables people use on a phone, hide secondary columns below `md`/`lg` and repeat the essentials under the title. The Library table (`Assets.tsx`) does the second: it is `table-fixed`, so percentage widths must be `md:w-[..%]` classes, not inline styles, or the fixed columns squeeze the title to nothing on a phone. Header row, filter row and body cells must hide the same columns.

## Checking it

`frontend/e2e/admin-layout-audit.mjs` logs in to a running site and backend, opens every admin page at 1920x1080 and iPhone size, and fails on: sideways page scroll, content using under 90% of a desktop window, nav not staying at the top, script errors, failed API calls. `SHOTS_DIR=...` saves screenshots; look at them, the checks do not catch ugly.

Needs a real backend (not the fake one the control-room test uses). Local file storage makes `/assets/:id/download` return 401 on the asset detail page; that is the test setup, not the page.
