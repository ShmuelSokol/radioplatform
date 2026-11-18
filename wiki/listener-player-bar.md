# The station keeps playing on the other listener pages

A listener who pressed Listen Live and then opens the guide, the DJs, the archives or the
station list keeps hearing the station, with a bar at the bottom: what is on, a way back
to the station's page, and Stop.

## How

The sound belongs to the Listen page and nothing else (`pages/public/Listen.tsx`: the
built-in player, the server stream element, the live-show relay). It is NOT a second
player. `components/audio/ListenHost.tsx`, drawn by `Layout` under every page, holds the
Listen page:

- on `/listen/:id` it shows it (the route itself draws nothing);
- while a station plays and the listener is on `/stations`, `/guide`, `/hosts` or
  `/archives`, the same Listen page stays mounted, hidden, so nothing restarts;
- opening another station's page replaces it (keyed by station): the first station stops
  and the new one waits for its own tap;
- any other page (staff pages, call-in) drops it: those have their own sound.

`stores/playerStore.ts` only says what is playing (set by the Listen page when the
listener starts, cleared when they stop or the page goes away) and carries the page's
Stop for the bar. `components/audio/AudioPlayer.tsx` is the bar.

Why not a new global player: an iPhone refuses sound that a tap did not start, so a
player rebuilt on a page change would be silent; and the Listen page already handles the
stream falling back, the hard timed items, live shows, lock-screen controls.

## Rules

- Do not unmount or re-key the Listen page on a move between listener pages.
- A new place that plays its own sound on a listener page must call
  `usePlayerStore.getState().stop()` first (the Archives page does), or two things sound.
- New listener pages that should keep the station go in `LISTENER_PAGES`.

## Tests

`e2e/listener-pages.mjs` part `player` (desktop and iPhone size, sampled every 50 ms: never
silent, no restart, bar, Stop, archive takes over, another station wins, staff pages stop
it). `e2e/webkit/listener-webkit.py` part `bar`: real WebKit with sound refused without a
tap, built-in player and server stream without MSE (the iPhone path): one tap, three page
moves, still playing. Not checked by a machine: a real iPhone, lock screen, and the three
real stations (live check).
