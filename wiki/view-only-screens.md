# View-only screens: one rule for who may change what

A login may often open a screen it may not change (a viewer in the control room, a DJ
in the Library). The server refuses the change (403); the screen must not offer it.

## The rule

- `frontend/src/auth/access.ts`: `canAccess(role, path)` = who may open a screen,
  `canChange(role, path)` = who may change things on it (`WRITE_ROLES`, mirrors the
  server's write guards), `isManager(role)` = admin or manager.
- `frontend/src/hooks/useMayChange.ts`: `useMayChange()` answers `canChange` for the
  screen the person is on (or for a path you pass). **Every screen and shared panel asks
  this hook (or `isManager` when the control's own API guard is `require_manager`) and
  hides or disables its change controls.** The "View only" badge
  (`components/layout/ViewOnlyNotice.tsx`) uses the same `canChange`.
- Hide a control that has no meaning for the role (New, Edit, Delete, Trim). Disable one
  that is part of a fixed layout (the control room's Go next, Loop, Fade). A group of
  settings goes in `<fieldset disabled={!mayChange}>`, which switches off every box and
  button inside it in one place (Program clock's Automation settings).
- A menu badge (category, sponsor) becomes plain text; it must not open a picker whose
  list the API refuses (`GET /sponsors` is manager only).

## What a view-only login keeps

Looking and listening: pre-listen (PFL), Detect Silence, a preset's 15 s preview, the
weather preview, tabs, filters, search, the Song panel's details. "Save as Default" on
the item page stores the person's own pre-listen lengths, so it stays.

## Where it is applied (2026-11-18)

Control room (viewer): queue insert / move / remove / drag, Start, Go next, Fade, Soft
fade, Loop, the N key, instant buttons (firing, fire mode, Edit, set tools, the ☆ in the
library), Next / + End / Insert in the library, Go live / Start / End show. Schedule
blocks, Library list (category and sponsor badges), item page (fields, enhance, trim),
Program clock (Automation settings), Recording Studio weather readouts (DJ).

## Test

`e2e/admin-flows.mjs` F-ACCESS-10/11: on every screen a role may open but not change,
no enabled button or link starts with a change word (`WRITE_WORDS`: new, add, edit,
delete, save, go next, loop, go live, ...). `DUMP_BUTTONS=1` prints what each role is
offered per screen. F-ASSETDETAIL-10: the item page for a DJ and a viewer.

When you add a screen or a control that changes something: gate it with the hook, and if
its verb is new, add the word to `WRITE_WORDS`.
