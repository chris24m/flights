# Dad's flight tracker

A framed 64×32 LED display showing the aircraft overhead, fed by an ADS-B receiver in the loft.

- **`antenna/`**: the receiver Raspberry Pi 3B. readsb + tar1090 decode aircraft from the
  FlightAware Pro Stick Plus; the Python display service picks what to show (nearest aircraft,
  emergency squawks, military/Red Arrows, low and close) and serves `/display.json`.
  Start with [antenna/SETUP.md](antenna/SETUP.md).
- **`display/`**: the Adafruit Matrix Portal (CircuitPython) on the LED panel. It only polls
  `/display.json` and draws it. See [display/README.md](display/README.md).

```
Pro Stick Plus ─USB─ Pi 3B (readsb → aircraft.json → flight display service :8080/display.json)
                                                                     │ home Wi-Fi
                                                     Matrix Portal + 64×32 panel
```

`display.json` example:

```json
{"mode":"flight","callsign":"EZY45T","airline":"EZY","airline_name":"easyJet","col":"#FF6600",
 "route":"AGP-BOH","type":"A320","alt":2100,"spd":140,"trk":80,"trk_txt":"E","dist_km":1.8,
 "alert":null,"time":"14:05","brightness":0.6,"status":"ok"}
```

`mode` is `flight`, `alert` (with `alert.level` = `emergency` / `military` / `low`) or `idle`
(with `date`, `weather`, `last`, `today`, `furthest_km`).

Settings page (from a phone, at home or over Tailscale): **http://flighttracker:8080/settings**:
facts page, rare-plane banner, plane of the day, radius, units, brightness schedule, screen transitions (fly-by plane, radar sweep, board scroll, sparkle, flap or random),
and buttons to test each screen. Changes apply within seconds and are saved on the Pi.

Debug tools for both sides, and a fake-sky mode for testing without the antenna: [DEBUGGING.md](DEBUGGING.md)

Tests for the antenna logic: `cd antenna && python3 -m unittest discover -s tests`

## Power cuts and unplugging

Both boxes come back on their own, and in either order:

| | What happens |
|---|---|
| **Pi unplugged / power cut** | Boots in about a minute; readsb and the display service start automatically (`Restart=always` also restarts the service if it ever crashes). Stats and the route cache are saved every minute, so at most a minute of "aircraft today" is lost. |
| **Panel while the Pi is away** | Keeps showing the last screen for 30 s, then the clock page with a red blinking Wi-Fi or Pi icon and a line saying what's wrong (stays dark for up to 8 h if it was in its overnight-off period). Recovers within 15 s of the Pi answering. |
| **Portal unplugged / power cut** | `code.py` runs at power-on: about 10–20 s to join Wi-Fi, then it draws straight away. It starts at low brightness so a night-time restart doesn't light the room. |
| **Portal code hangs or crashes** | A 16 s hardware watchdog resets the board, and any unexpected error resets it after 5 s. |
| **Wi-Fi drops** | The Portal reconnects on its own, trying each network in `settings.toml`. |

The one real risk is SD card corruption on the Pi from repeated hard power cuts. A good-brand card helps; for extra
safety you can turn on the read-only overlay filesystem (`sudo raspi-config` → Performance → Overlay File System)
once everything is final, at the cost of stats resetting on each reboot and needing to switch it off to make changes.
