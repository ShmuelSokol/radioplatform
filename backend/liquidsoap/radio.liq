# Liquidsoap radio pipeline for Studio Kol Bramah
# Runs as a sidecar alongside uvicorn on Railway

# Server settings — Unix socket for scheduler communication
settings.server.socket := true
settings.server.socket.path := "/tmp/liquidsoap.sock"

# Request queue — scheduler pushes track URLs here via telnet
queue = request.queue(id="main_queue")

# Silence fallback (prevents dead air if queue is empty)
silence = blank(id="silence")
radio = fallback(track_sensitive=true, [queue, silence])

# Crossfade: 3-second overlap with smart transition
radio = crossfade(duration=3.0, radio)

# Loudness normalization (targets -18 LUFS)
radio = normalize(target=-18., radio)

# HLS output
output.file.hls(
  segment_duration=4.0,
  segments=5,
  segments_overhead=2,
  "/tmp/hls/default",
  [("aac", %fdkaac(bitrate=192)), ("mp3", %mp3(bitrate=192))],
  radio
)
