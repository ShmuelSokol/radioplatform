# Liquidsoap radio pipeline for Studio Kol Bramah
# Runs on dedicated VPS, controlled by Railway backend via TCP telnet

# TCP telnet server (Railway backend connects here)
settings.server.telnet := true
settings.server.telnet.bind_addr := "0.0.0.0"
settings.server.telnet.port := 1234

# Disable unused Unix socket
settings.server.socket := false

# Request queue — scheduler pushes track URLs here
queue = request.queue(id="main_queue")

# Silence fallback (prevents dead air)
silence = blank(id="silence")
radio = fallback(track_sensitive=true, [queue, silence])

# Crossfade: 3-second overlap
radio = crossfade(duration=3.0, radio)

# Loudness normalization (-18 LUFS)
radio = normalize(target=-18., radio)

# Output to Icecast
output.icecast(
  %mp3(bitrate=192),
  host="127.0.0.1",
  port=8000,
  password="CHANGE_ME",
  mount="/live",
  name="Kol Bramah Live",
  genre="Jewish Radio",
  url="https://kbrlive.com",
  radio
)
