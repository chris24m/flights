# Display: Matrix Portal + 64×32 LED panel

The Matrix Portal polls the antenna Pi's `/display.json` every 3 s and draws it. All the
deciding (which aircraft, alerts, brightness, clock) happens on the Pi, because the Portal
can't be reached remotely once it's at Dad's.

## One-time setup (week 2)

1. **Install CircuitPython 9.x** on the Matrix Portal M4: download the UF2 from
   circuitpython.org (board: "Matrix Portal M4"), double-tap the reset button, drag the UF2
   onto the `MATRIXBOOT` drive. It reappears as `CIRCUITPY`. Use a USB-C **data** cable.
2. **Update the ESP32 Wi-Fi firmware** if it's old (Adafruit's "Upgrade Airlift firmware" guide).
   Recent NINA firmware is more reliable at reconnecting.
3. **Libraries:** from the CircuitPython 9.x library bundle, copy these into `CIRCUITPY/lib/`:
   - `adafruit_matrixportal/`
   - `adafruit_esp32spi/`
   - `adafruit_requests.mpy`, `adafruit_connection_manager.mpy`
   - `adafruit_display_text/`, `adafruit_bitmap_font/`, `adafruit_imageload/`
   - `adafruit_portalbase/`, `neopixel.mpy`, `adafruit_bus_device/` (needed by matrixportal)

   Or with circup on your laptop: `pip install circup && circup install adafruit_matrixportal adafruit_esp32spi adafruit_requests adafruit_connection_manager adafruit_display_text adafruit_bitmap_font adafruit_imageload`
4. **Font:** the layout is designed for the 3×5-pixel `tom-thumb.bdf` font. Get it from the
   `examples/fonts/` folder of Adafruit's Adafruit_CircuitPython_Bitmap_Font repo on GitHub
   (or any Adafruit Learn guide that uses it) and copy it to `CIRCUITPY/fonts/tom-thumb.bdf`.
   Without it the code falls back to the built-in font, which is too big for the layout.
5. Copy `code.py` and the `logos/` folder to `CIRCUITPY/`.
6. Copy `settings.toml.example` to `CIRCUITPY/settings.toml` and fill in Wi-Fi. The Pi's address isn't needed:
   the Pi broadcasts it every 3 s and the Portal picks it up (and remembers it across power cuts). It also tries
   `http://flighttracker:8080`. Set `DISPLAY_URLS` only if neither works on some network.

The panel shows `WIFI...`, then the flight or idle screen. `NO DATA / IS THE PI ON?` means
Wi-Fi is fine but the Pi didn't answer: check `curl http://<pi-ip>:8080/display.json`.
Serial console (`screen /dev/tty.usbmodem* 115200`, or Mu / Thonny) shows the errors.

## Screens

| Mode | Layout |
|---|---|
| flight | 16×16 logo · callsign (airline colour) · route · type / altitude + speed / heading + distance |
| alert | banner (flashing red for 7700/7600/7500, navy with red stripes for military/Red Arrows, flashing yellow "LOOK UP!" for low and close) above the aircraft details |
| idle | clock · temperature and weather · date · scrolling "last seen" · today's count and furthest |

Use the Pi's test mode to see each one: `curl "http://<pi-ip>:8080/test?scenario=military"`.

## Offline demo

Until the Pi answers (just plugged in, no Wi-Fi yet, Pi switched off, unwrapping it on Christmas
Day...) the Portal plays a built-in demo instead of a "NO DATA" message: a title card saying what
it's waiting for (NO WIFI YET / LOOKING FOR PI / SETUP NEEDED), then an easyJet A320, its facts
card, a rare 747, the Red Arrows, LOOK UP, a Spitfire plane of the day, a helicopter and a 7700,
8 s each, with every transition. It keeps trying the Wi-Fi and the Pi in the background (the demo
pauses for a few seconds while it does) and switches to live data as soon as it gets some. If the
Pi had said "off for the night" before going quiet, the panel stays off.
`OFFLINE_DEMO = 0` in settings.toml brings back the plain status messages.

## Transitions

When the screen changes, one of these plays, picked at random from the ones ticked on the Pi's
settings page (none ticked = instant):

| Name | What it does |
|---|---|
| flyby | a top-down plane (twin jet, 4-engine, prop, fighter or helicopter, picked from the type) flies across the way the aircraft is heading: eastbound left to right, westbound right to left. A `>`-shaped black band clears the old screen ahead of it and reveals the new one behind its tail. When a plane leaves, it flies off. |
| radar | a green radar line sweeps clockwise from 12 o'clock, revealing the new screen |
| slide | departure-board scroll in a random direction (up, down, left or right) |
| dissolve | sparkle from one screen to the other |
| flap | split-flap letters (also used between a plane's details and its facts card, whatever the setting) |

The fly-by, radar, slide and dissolve work by copying the panel's current picture into a full-screen
bitmap and eating it away. If the panel ever shows them in odd colours, add `SNAP_SWAPPED = 1` to
`settings.toml`; if anything about them goes wrong the Portal falls back to the flap and notes the
error (visible on the Pi's /debug page).
Each animation runs for a fixed time (about 0.6 to 1.8 s): if the Portal draws slowly it just
shows fewer, bigger steps. The frame rate of the last one is reported to the Pi's /debug page (`tfps`).

## Logos

`logos/` has real airline logos shrunk to 16×16 for about 60 airlines (easyJet, Ryanair, BA, TUI, Jet2,
Wizz, Loganair, Aurigny, the main European and long-haul carriers, cargo), plus RAF and Red Arrows roundels.
`logos/preview.png` shows them all. Files are named by ICAO airline code (`EZY.bmp`); sister codes such as
EJU/EZS (easyJet Europe/Switzerland) are mapped to one file on the Pi (`LOGO_ALIAS` in `refdata.py`).

- `tools/fetch_logos.py` downloads and shrinks them (add a code to its list and re-run, or `python3 fetch_logos.py ABC`).
  It picks whichever source version is brightest, since dark-on-black logos disappear on the LEDs.
  Wide or fussy logos get zoomed in on their recognisable part via `CROPS` in the same file (BA, Finnair, FedEx, DHL, Emirates so far): add a line there to zoom any other.
- `tools/make_logo.py some-logo.png EZY` replaces one with any image you prefer.
- `tools/make_tails.py` draws the roundels.

The logos are the airlines' trademarks: fine for a private display, so keep this repo private.
Airlines with no file get a plane icon tinted in the airline's colour.
Copy the whole `logos/` folder to `CIRCUITPY/` (the preview PNG can stay behind).

## Brightness

Sent by the Pi from the schedule in `antenna/config.toml`. At 0 the panel goes dark. Set the levels
in an evening-lit room; a mostly dark layout also keeps the 3 A USB-C supply comfortable.
