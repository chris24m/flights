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

## Testing without the antenna (fake sky)

The Portal *pulls* `/display.json` from the Pi every few seconds; it never knows where the
aircraft came from. So to test the panel without an antenna, readsb, or even the Pi, swap the
real feed for made-up aircraft and run the normal service:

| Where | How |
|---|---|
| Pi, from your phone | settings page → **Fake sky (1 hour)**. Press **stop** to go back to the real feed. |
| Pi, over SSH | `sudo systemctl stop flight-display`, then `cd ~/flights/antenna && python3 -m flightdisplay --simulate` (Ctrl-C, then `sudo systemctl start flight-display` to go back) |
| Pi, until you switch it off | settings page → tick **Fake planes (ignore the antenna)** → Save. Survives restarts and power cuts; untick (or Reset to defaults) to go back to the antenna. Same as `simulate = true` under `[receiver]` in config.toml. |
| Your Mac | `cd antenna && python3 -m flightdisplay --simulate` (needs Python 3.11+: macOS's built-in one is older, `brew install python` or python.org; allow incoming connections if macOS asks). It broadcasts like the Pi does, so a Portal on the same Wi-Fi finds the Mac when the Pi is off or not set up yet (while the Pi is answering, the Portal sticks with the Pi). Afterwards it finds the Pi again by itself. |

A new aircraft arrives about every minute (`--every 20` for a busier sky), flies a straight line
over home and leaves again: mostly easyJet / Ryanair / BA and friends, with the odd approach into
Bournemouth ("LOOK UP!"), light aircraft, air ambulance, military, warbird, Red Arrows, the
occasional 7700, and quiet spells so the clock gets a turn. Everything downstream is the real
code: selection, logos, facts cards, plane of the day, transitions, brightness.

Fake planes never go into the daily stats or "last seen". `/debug` and display.json show
`"status": "simulated"` (or `"test": "sky"` for the settings-page button) so you can tell.

## If the panel shows the clock with a red icon

While the Pi isn't answering, the Portal shows its clock page with Wi-Fi and Pi icons; the red,
blinking one is the problem and the line under the date says which: NO WIFI, LOOKING FOR PI,
NO PI - IS IT ON? or SETUP NEEDED. Press UP for the full status screen.

1. `/debug` → `clients`: is the Portal listed with a recent `last_seen_ago_s`? If yes, the link works and
   the problem is elsewhere (look at `receiver`).
2. Not listed: run `tools/listen.py --fetch` on a laptop on the same Wi-Fi. If it hears the broadcast and
   fetches fine, the Pi side is good; set the Portal to `DEBUG = 2` and read its last error.
3. Broadcast not heard by the laptop either: check `/debug` → `announce.last_error`; some mesh or guest
   Wi-Fi networks block broadcasts. Fallback: put the Pi's IP in the Portal's `DISPLAY_URLS`.
4. Portal error says "discovery unavailable": its Wi-Fi firmware is too old for listening; update the AirLift
   firmware or use `DISPLAY_URLS`.
