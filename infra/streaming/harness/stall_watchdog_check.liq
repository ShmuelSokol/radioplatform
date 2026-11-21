# Proves the engine's stall watchdog (radio.liq "Stall watchdog") works while the clock is
# blocked: the clock thread is stuck in a 10-minute call after 5 s, and the watchdog
# thread still sees no frames and shuts the engine down (systemd starts it again).
# Run: liquidsoap stall_watchdog_check.liq (exits within ~15 s; tests/test_engine_watchdogs.py)
settings.log.stdout := true
settings.log.level := 3
t0 = time()
s = sine()
stuck = ref(false)
s = source.on_frame(s, fun () -> if time() - t0 > 5. and not stuck() then
  stuck := true
  log.important("blocking the clock")
  ignore(process.run("sleep 600"))
end)
last_frame = ref(time())
s = source.on_frame(s, fun () -> last_frame := time())
thread.run.recurrent(fast=false, delay=2., fun () -> begin
  q = time() - last_frame()
  if q > 8. then
    log(level=2, "watchdog: no audio for #{q} s; restarting")
    shutdown()
  end
  2.
end)
output.dummy(fallible=true, s)
