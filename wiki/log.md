# Wiki operation log

Append-only. Format: `<OP> <YYYY-MM-DD> <page-or-topic> <summary>`

Operations: INGEST | QUERY | UPDATE | LINT

INGEST 2026-05-24 README.md Wiki system bootstrapped with 4 initial pages from today's session.
INGEST 2026-05-24 streaming-architecture.md Liquidsoap+Icecast moved to dedicated VPS (TCP telnet, no HLS, plain <audio>). VPS not provisioned yet.
INGEST 2026-05-24 audio-engine.md Client-side dual-deck crossfade engine. Fixed gap: auto-crossfade 3s before end + instant pre-loaded deck swap.
INGEST 2026-05-24 hourly-slots.md Hourly/half-hour slot scheduling. Bug: weather_interval_minutes=30 ignored, weather only at :00 not :30.
INGEST 2026-05-24 dashboard-timing.md Elapsed timer overflow fixed — now uses WS data + Math.min cap instead of stale 10s REST poll.
INGEST 2026-06-10 v2-ui.md V2 UI toggle (navbar pill, localStorage). Native V2: stations+listen; other pages in light sheet. Vite proxy VITE_PROXY_TARGET for dev-against-prod.
INGEST 2026-06-10 v2-ui.md V2 deployed live to kbrlive.com + verified in prod browser (zero JS errors). Vercel CLI note: device login needs `vercel@latest` (cached 50.18.2 rejects codes); creds at AppData/Roaming/com.vercel.cli.
INGEST 2026-06-10 skip-latency.md Skip lag root cause: skip/start endpoints never WS-broadcast; Dashboard audio fed by 10s REST poll. Both fixed.
INGEST 2026-06-10 hourly-slots.md SEVERE: ad-slot preempts masked ALL hourly announcements via hour-bucketed dedup. Fixed + :30 weather added.
