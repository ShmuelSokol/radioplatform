# The station keeps playing on the other pages

Someone who pressed Listen Live (or Listen in the control room) and then opens the guide,
the DJs, the archives, the station list or a staff page keeps hearing the station, with a
bar at the bottom: what is on, a way back to the station's page, and Stop. Coming back to
the station's page or the control room carries on with no new tap. Only the call-in page
and the live host and screener screens stop it (their own sound). Since 2026-11-18
(Shmuel on his iPhone: "When in guide music stops. And when navigating back to dashboard
music doesn't continue").

## The shared stream player

`lib/stationStream.ts` holds ONE stream `<audio>` for the tab (`data-testid=station-stream`,
appended to the body, outside React). The Listen page and the control room both play the
server stream through it. A page that goes away calls `releaseStationStream` (the stream
plays on for 6 s); the next page calls `adoptStationStream` for its station and takes it
over: same element, so an iPhone, which allows sound only on an element a tap started,
keeps playing. Leaving the control room while it gives sound calls `playerStore.handOver`:
ListenHost mounts the Listen page for that station, which adopts the stream (or, for the
built-in player, tries to start its own; an iPhone refuses, and the bar shows "Tap to
listen" after 2 s). The control room opens on the station the tab is listening to.

The built-in player (two decks + Web Audio, All Day Stories) cannot move between pages;
from the control room it needs that one tap on the bar.

## How

The sound belongs to the Listen page and nothing else (`pages/public/Listen.tsx`: the
built-in player, the server stream element, the live-show relay). It is NOT a second
player. `components/audio/ListenHost.tsx`, drawn by `Layout` under every page, holds the
Listen page:

- on `/listen/:id` it shows it (the route itself draws nothing);
- while a station plays and the page is any other except the control room and the pages
  with their own sound (`keepsStation`, `hasOwnSound`), the same Listen page stays
  mounted, hidden, so nothing restarts;
- opening another station's page replaces it (keyed by station): the first station stops
  and the new one waits for its own tap;
- call-in and the live host/screener pages stop it; the control room takes it over itself.

`Layout` is one tree for every page (the full-window control room and Databank included),
so ListenHost is never re-created on a page change, and the page alone sits in its own
error boundary and Suspense: a screen whose code is gone after a release shows "The site
was updated" in the page area while the menu, ListenHost and the bar carry on.

`stores/playerStore.ts` only says what is playing (set by the Listen page when the
listener starts, cleared when they stop or the page goes away) and carries the page's
Stop and Play for the bar, and whether sound really comes out (`sounding`).
`components/audio/AudioPlayer.tsx` is the bar; its Stop also clears the control room's
remembered "listening", so the control room does not switch the sound back on by itself.

Why not a new global player: an iPhone refuses sound that a tap did not start, so a
player rebuilt on a page change would be silent; and the Listen page already handles the
stream falling back, the hard timed items, live shows, lock-screen controls.

## Rules

- Do not unmount or re-key the Listen page on a move between listener pages.
- A new place that plays its own sound on a listener page must call
  `usePlayerStore.getState().stop()` first (the Archives page does), or two things sound.
- A new page with its own sound goes in `hasOwnSound` (ListenHost.tsx).
- Play the server stream only through `lib/stationStream`, never a second element.

## Tests

`e2e/listener-pages.mjs` part `player` (desktop and iPhone size, sampled every 50 ms: never
silent, no restart, bar, Stop, archive takes over, another station wins, staff pages
keep it, call-in stops pages keep it, call-in stops it). `e2e/webkit/listener-webkit.py` part `bar`: real WebKit with sound refused without a
tap, built-in player and server stream without MSE (the iPhone path): one tap, three page
moves, still playing. `e2e/control-room-iphone-sound.mjs` part `travel` (iPhone with Safari's tap rule, and
desktop): control room Listen, then Program Guide, DJs, Archives, a staff page, back to
the Dashboard, back and forward buttons, never a silent sample; Stop in the bar; the
built-in player's "Tap to listen". WebKit part `stale`: a release mid-listen, then the
menu and back. Not checked by a machine: a real iPhone, lock screen, and the three
real stations (live check).
