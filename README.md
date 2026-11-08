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

Tests for the antenna logic: `cd antenna && python3 -m unittest discover -s tests`
