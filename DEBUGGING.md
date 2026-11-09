# Debugging

The two boxes talk in two ways, and these tools let you watch both ends:

```
Pi ── UDP broadcast every 3 s on port 50222: "FLIGHTDISPLAY http://<pi-ip>:8080/display.json" ──▶ Portal
Pi ◀── HTTP GET /display.json?portal=…&ip=…&rssi=…&err=… every 3 s ─────────────────────────────── Portal
```

The Portal can't be reached remotely, so it **reports its own health to the Pi** with every poll.
That means the Pi's `/debug` page (reachable over Tailscale from anywhere) shows both sides.

## Pi side

| What | How |
|---|---|
| Everything at a glance | `http://<pi>:8080/debug` in a browser (or `curl -s localhost:8080/debug`) |
| Live log | `journalctl -u flight-display -f` |
| Very detailed log (each broadcast, request, display change) | set `[debug] verbose = true` in `config.toml`, `sudo systemctl restart flight-display` |
| Is the broadcast going out, and can it be fetched? | `python3 tools/listen.py --fetch` on your laptop (same Wi-Fi) or on the Pi |
| Is readsb working? | `http://<pi>/tar1090` and `systemctl status readsb` |

What `/debug` shows:

- `receiver`: can the service read `aircraft.json`? last error, aircraft count.
- `announce`: the URL being broadcast, how many sent, last error (e.g. no network yet).
- `clients`: every device that has polled the Pi, when it last did, and **the Portal's own report**:
  its IP, Wi-Fi name and signal (`rssi`, -50 great … -80 poor), how it found the Pi (`via`: saved /
  settings / hostname / discovery), failures in a row, uptime, why it last restarted (`reset`: POWER_ON,
  WATCHDOG, SOFTWARE…), and its last error, including a crash message from before a restart.
- `display_now`: exactly what the panel is being told to draw.
- `recent_log`: the last 60 log lines.

## Portal side

Set `DEBUG` in `CIRCUITPY/settings.toml` (the board restarts when you save):

- `DEBUG = 1`: detailed log on the USB serial console. Plug into a laptop and open the serial console in
  Mu or Thonny, or `screen /dev/tty.usbmodem* 115200` on a Mac. You'll see Wi-Fi connects, every fetch
  with its timing, every UDP packet heard while looking for the Pi, and errors.
- `DEBUG = 2`: as 1, and the panel shows a diagnostics screen instead of flights, no laptop needed:

  ```
  W <wifi name>
  <portal ip> <signal>DB
  PI <pi ip>
  OK <seconds since last good poll> <how found>
  <last error, scrolling>
  ```

Even with `DEBUG = 0` the panel shows `WIFI OK <ip>` briefly after connecting, and any crash is remembered
and reported to the Pi after the automatic restart.

## If the panel says NO DATA

1. `/debug` → `clients`: is the Portal listed with a recent `last_seen_ago_s`? If yes, the link works and
   the problem is elsewhere (look at `receiver`).
2. Not listed: run `tools/listen.py --fetch` on a laptop on the same Wi-Fi. If it hears the broadcast and
   fetches fine, the Pi side is good; set the Portal to `DEBUG = 2` and read its last error.
3. Broadcast not heard by the laptop either: check `/debug` → `announce.last_error`; some mesh or guest
   Wi-Fi networks block broadcasts. Fallback: put the Pi's IP in the Portal's `DISPLAY_URLS`.
4. Portal error says "discovery unavailable": its Wi-Fi firmware is too old for listening; update the AirLift
   firmware or use `DISPLAY_URLS`.
