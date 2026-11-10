# Flight display for the Adafruit Matrix Portal (M4) + 64x32 RGB panel. CircuitPython 10.x.
# Loaded by code.py; install as flight.mpy with display/install.sh (see README).
#
# Deliberately dumb: the antenna Pi decides everything and serves /display.json;
# this file only draws it. Fix behaviour on the Pi (reachable over Tailscale), not here.

import gc
import json
import os
import random
import time

import bitmaptools
import board
import busio
import displayio
import microcontroller
import terminalio
from digitalio import DigitalInOut
from watchdog import WatchDogMode

from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.bitmap_label import Label  # faster text updates than label.Label
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_matrixportal.matrix import Matrix

_http_buf = bytearray(2048)  # the Pi's replies (~700 bytes); made first, while memory is in one piece
MEM = []  # free memory after each stage of start-up, logged once the main loop starts


def mem_mark(stage):
    gc.collect()
    MEM.append("%s %d" % (stage, gc.mem_free()))


mem_mark("libs")

# --- settings (settings.toml) ------------------------------------------------

# DISPLAY_URLS is optional: the Pi announces itself on the network (see discover()).
URLS = [u.strip() for u in (os.getenv("DISPLAY_URLS") or "").split(",") if u.strip()]
DISCOVERY_PORT = 50222  # must match [discovery] port in antenna/config.toml
HOSTNAME_URL = "http://flighttracker:8080/display.json"  # works where the router does local DNS
NETWORKS = []
for n in (1, 2, 3):
    ssid = os.getenv("WIFI_SSID_%d" % n)
    if ssid:
        NETWORKS.append((ssid, os.getenv("WIFI_PASSWORD_%d" % n) or ""))
POLL_SECONDS = float(os.getenv("POLL_SECONDS") or 3)
# DEBUG = 0 normal, 1 = detailed log on the USB serial console, 2 = also a diagnostics screen on the panel
DEBUG = int(os.getenv("DEBUG") or 0)
# Departure-board "flap" animation when the screen changes (new aircraft, clock <-> flight). 0 = off.
FLAP = int(os.getenv("FLAP") if os.getenv("FLAP") is not None else 1)
# Screen-change animations to pick from at random, until the Pi says otherwise (ticked on its
# settings page): any of flyby, radar, slide, dissolve, flap. Empty = instant.
ALL_TRANSITIONS = ("flyby", "radar", "slide", "dissolve", "flap")
_t = os.getenv("TRANSITIONS")
TRANSITIONS = [x.strip() for x in _t.split(",") if x.strip()] if _t is not None else \
    (list(ALL_TRANSITIONS) if FLAP else [])
# If the transitions show odd colours on the real panel, set SNAP_SWAPPED = 1 (byte order of the snapshot).
SNAP_SWAPPED = int(os.getenv("SNAP_SWAPPED") or 0)
# 1 = until the Pi answers (no Wi-Fi yet, Pi off, unwrapping it on Christmas Day...) play a built-in
# demo of every screen instead of "NO DATA". 0 = show the plain status message.
OFFLINE_DEMO = int(os.getenv("OFFLINE_DEMO") if os.getenv("OFFLINE_DEMO") is not None else 1)
VERSION = "2026.11.10g"

# What the Portal knows about its own health. Sent to the Pi with every poll (see report_query),
# so the Pi's /debug page shows the Portal's state even though the Portal itself can't be reached remotely.
STATE = {"ssid": "", "ip": "", "rssi": 0, "url": "", "via": "", "last_ok": None, "fails": 0, "err": "",
         "found": 0}
URL_SOURCE = {}  # url -> how we learned it: saved / settings / hostname / discovery


def log(*args):
    print("%9.1f" % time.monotonic(), *args)


def dbg(*args):
    if DEBUG:
        log(*args)


def note_error(text):
    """Remember the latest problem (shown on the diagnostics screen and reported to the Pi)."""
    STATE["err"] = str(text)[:60]
    log("ERROR", text)
STALE_SECONDS = 30  # show "no data" if the Pi hasn't answered for this long

# --- colours (dimmed by the Pi's brightness value) ----------------------------

WHITE, GREY, AMBER, CYAN, GREEN = 0xFFFFFF, 0x808080, 0xFFA000, 0x00C0FF, 0x40FF40
RED, BLUE, YELLOW, BLACK, NAVY = 0xFF0000, 0x2040FF, 0xFFD000, 0x000000, 0x001060
GOLD = 0xFFB000
brightness = 0.15  # until the Pi says otherwise: low, so a 3am power cut doesn't light the room


def dim(rgb):
    if brightness <= 0:
        return 0
    out = 0
    for shift in (16, 8, 0):
        c = (rgb >> shift) & 0xFF
        v = int(c * brightness)
        if c and v < 12:  # below this the panel shows nothing at all
            v = 12
        out |= v << shift
    return out


def parse_colour(text, default):
    try:
        return int(text.lstrip("#"), 16) if text else default
    except ValueError:
        return default


# --- display setup -------------------------------------------------------------

matrix = Matrix(width=64, height=32, bit_depth=6)
display = matrix.display
SMALL = None
for _f in ("/fonts/flight-3x5.bdf", "/fonts/tom-thumb.bdf"):  # 3x5 pixel font, 4x6 cell (in the repo)
    try:
        SMALL = bitmap_font.load_font(_f)
        break
    except OSError:
        pass
if SMALL is not None:
    # Load every letter now, at power-on. Otherwise each new letter is read from the file the first
    # time it's shown, and once the memory is fragmented that read can fail (MemoryError).
    try:
        SMALL.load_glyphs("".join(chr(c) for c in range(32, 127)))
    except Exception as _e:  # noqa: BLE001
        print("font preload: %r" % (_e,))
    gc.collect()
if SMALL is None:
    SMALL = terminalio.FONT  # works, but too big: copy the fonts/ folder over (see README)
BIG = terminalio.FONT

root = displayio.Group()
display.root_group = root
mem_mark("panel+font")


def label(group, x, y, font=SMALL, colour=WHITE):
    lab = Label(font, text="", color=dim(colour), anchor_point=(0, 0), anchored_position=(x, y))
    lab.base_colour = colour
    group.append(lab)
    return lab


# --- split-flap animation ----------------------------------------------------------
# Each character spins through random letters, then settles left to right like an airport board.
FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FLAP_SPIN = 0.30      # seconds every character spins before the first one settles
FLAP_STAGGER = 0.045  # extra seconds per character position
flapping = {}         # id(label) -> [label, target text, start time]
animate_now = False   # set by draw() when the scene changes


def set_text(lab, text, colour=None, animate=None):
    text = text or ""
    if colour is not None:
        lab.base_colour = colour
    lab.color = dim(lab.base_colour)
    job = flapping.get(id(lab))
    if job:                       # mid-flap: just retarget it
        job[1] = text
        return
    if lab.text == text:
        return
    if FLAP and (animate_now if animate is None else animate):
        flapping[id(lab)] = [lab, text, time.monotonic()]
    else:
        lab.text = text


def tick_flaps(now):
    for key in list(flapping):
        lab, target, start = flapping[key]
        t = now - start
        out = []
        done = True
        for i, ch in enumerate(target):
            if ch == " " or t >= FLAP_SPIN + i * FLAP_STAGGER:
                out.append(ch)
            else:
                out.append(FLAP_CHARS[random.randrange(len(FLAP_CHARS))])
                done = False
        lab.text = "".join(out)
        if done:
            del flapping[key]


def solid(w, h, colour):
    """A filled rectangle as a TileGrid, with a palette we can recolour."""
    bmp = displayio.Bitmap(w, h, 1)
    pal = displayio.Palette(1)
    pal[0] = dim(colour)
    tg = displayio.TileGrid(bmp, pixel_shader=pal)
    return tg, pal


# Fallback plane icon (16x16), used when there's no /logos/<ICAO>.bmp
PLANE = (
    "................",
    ".......##.......",
    ".......##.......",
    ".......##.......",
    "......####......",
    "....########....",
    "..############..",
    ".######..######.",
    "##.....##.....##",
    ".......##.......",
    ".......##.......",
    "......####......",
    ".....######.....",
    "................",
    "................",
    "................",
)
plane_bmp = displayio.Bitmap(16, 16, 2)
for yy, row in enumerate(PLANE):
    for xx, ch in enumerate(row):
        plane_bmp[xx, yy] = 1 if ch == "#" else 0
plane_pal = displayio.Palette(2)
plane_pal.make_transparent(0)


class Logo:
    """16x16 airline logo in the top-left; dims by rescaling its palette."""

    def __init__(self, group):
        self.group = displayio.Group()
        group.append(self.group)
        self.key = None
        self.base = []
        self.pal = None

    def show(self, icao, colour):
        key = icao or "-"
        if key != self.key:
            self.key = key
            while len(self.group):
                self.group.pop()
            bmp = None
            if icao:
                try:  # read from flash as it's drawn, so a logo costs almost no memory
                    bmp = displayio.OnDiskBitmap("/logos/%s.bmp" % icao)
                    pal = bmp.pixel_shader
                    len(pal)  # a palette (indexed BMP), so it can be dimmed
                except (OSError, ValueError, TypeError, AttributeError):
                    bmp = None
            if bmp is None:
                bmp, pal = plane_bmp, plane_pal
                self.base = [None, colour or GREY]
            else:
                self.base = [pal[i] for i in range(len(pal))]
            self.pal = pal
            self.group.append(displayio.TileGrid(bmp, pixel_shader=pal))
            gc.collect()
        if self.base and self.base[0] is None:  # plane icon: tint with airline colour
            self.base[1] = colour or GREY
        for i, c in enumerate(self.base):
            if c is not None:
                self.pal[i] = dim(c)


class Scroller:
    """Scrolls a label horizontally if its text is wider than the panel."""

    def __init__(self, lab, x0=0):
        self.lab, self.x0, self.x = lab, x0, x0

    def tick(self):
        w = self.lab.bounding_box[2]
        if w <= 64 - self.x0:
            self.lab.anchored_position = (self.x0, self.lab.anchored_position[1])
            return
        self.x -= 1
        if self.x < -w:
            self.x = 64
        self.lab.anchored_position = (self.x, self.lab.anchored_position[1])


# Flight screen: logo + callsign / route / type, then altitude-speed and heading-distance.
flight_g = displayio.Group()
logo = Logo(flight_g)
f_cs = label(flight_g, 18, 1)
f_route = label(flight_g, 18, 7, colour=AMBER)
f_type = label(flight_g, 18, 13, colour=GREY)
f_altspd = label(flight_g, 0, 19, colour=CYAN)
f_hdg = label(flight_g, 0, 25, colour=GREEN)
root.append(flight_g)

# Alert screen: coloured banner on top, aircraft details underneath.
alert_g = displayio.Group()
banner_tg, banner_pal = solid(64, 7, RED)
alert_g.append(banner_tg)
stripe_top, stripe_top_pal = solid(64, 1, RED)
stripe_bot, stripe_bot_pal = solid(64, 1, RED)
stripe_bot.y = 6
alert_g.append(stripe_top)
alert_g.append(stripe_bot)
a_banner = label(alert_g, 2, 1)
a_cs = label(alert_g, 0, 8)
a_route = label(alert_g, 0, 14, colour=AMBER)
a_altspd = label(alert_g, 0, 20, colour=CYAN)
a_hdg = label(alert_g, 0, 26, colour=GREEN)
banner_scroll = Scroller(a_banner, 2)
root.append(alert_g)

# Idle screen: clock, weather, date, last seen (scrolls), today's stats.
idle_g = displayio.Group()
i_time = label(idle_g, 0, 0, font=BIG, colour=WHITE)
i_temp = label(idle_g, 34, 1, colour=AMBER)
i_wx = label(idle_g, 34, 7, colour=GREY)
i_date = label(idle_g, 0, 14, colour=GREY)
i_last = label(idle_g, 0, 20, colour=CYAN)
i_stats = label(idle_g, 0, 26, colour=GREEN)
last_scroll = Scroller(i_last)
root.append(idle_g)

# Facts card: five lines (name, speed, ceiling, first flight, fun fact), sent ready-made by the Pi.
card_g = displayio.Group()
c_lines = [label(card_g, 0, y, colour=c) for y, c in ((1, WHITE), (7, CYAN), (13, GREEN), (19, AMBER), (25, GREY))]
card_scrolls = [Scroller(lab) for lab in c_lines]
root.append(card_g)

# Message screen: Wi-Fi / Pi problems.
msg_g = displayio.Group()
m1 = label(msg_g, 0, 8, colour=AMBER)
m2 = label(msg_g, 0, 16, colour=GREY)
root.append(msg_g)


# Diagnostics screen (DEBUG = 2): what the Portal sees, for checking at Dad's without a laptop.
diag_g = displayio.Group()
d_lines = [label(diag_g, 0, y, colour=c) for y, c in ((0, CYAN), (6, WHITE), (12, AMBER), (18, GREEN), (24, RED))]
err_scroll = Scroller(d_lines[4])
root.append(diag_g)


# --- screen transitions --------------------------------------------------------------
# How they work: just before the screen changes, copy what the panel is showing (the old screen)
# into `snap`, a full-screen bitmap drawn on top of everything. Then draw the new screen
# underneath as normal, and eat away the snapshot frame by frame: pixels set to TRANS let
# the new screen show through, pixels set to 0 are black. A plane sprite can fly on top.

TRANS = 0x0020  # "see-through" RGB565 value; dim() never produces it (green this faint is never used)
fb = display.framebuffer
snap = displayio.Bitmap(64, 32, 65536)
try:
    snap_cc = displayio.ColorConverter(
        input_colorspace=displayio.Colorspace.RGB565_SWAPPED if SNAP_SWAPPED else displayio.Colorspace.RGB565)
    snap_cc.make_transparent(TRANS)
    CAN_SNAP = True
except Exception as e:  # noqa: BLE001 - very old CircuitPython: fall back to the flap
    snap_cc = displayio.ColorConverter()
    CAN_SNAP = False
    note_error("no transitions: %r" % e)
snap_tg = displayio.TileGrid(snap, pixel_shader=snap_cc)
overlay_g = displayio.Group()
overlay_g.append(snap_tg)
overlay_g.hidden = True

# Top-down sprites with the nose pointing right (mirrored for right-to-left).
# W body, G wing, E engine, K cockpit, R rotor, C tail (airline colour).


def _sym(top, mid):
    return top + mid + top[::-1]


SPRITE_ART = {
    "twin": _sym([
        "......GGG.................",
        ".......GGG................",
        "........GGG...............",
        ".........GGGG.............",
        "..........GGGEEE..........",
        "GG.........GGGGEE.........",
        ".GG.........GGGGG.........",
    ], [
        ".CWWWWWWWWWWWWWWWWWWWWWWW.",
        "CCWWWWWWWWWWWWWWWWWWWWWWKW",
        ".CWWWWWWWWWWWWWWWWWWWWWWW.",
    ]),
    "quad": _sym([
        ".....GGG....................",
        "......GGGEE.................",
        ".......GGGEE................",
        "........GGGG................",
        ".........GGGG...............",
        "..........GGGEEE............",
        "GG.........GGGGEE...........",
        ".GG.........GGGGG...........",
    ], [
        ".CWWWWWWWWWWWWWWWWWWWWWWWWW.",
        "CCWWWWWWWWWWWWWWWWWWWWWWWWKW",
        ".CWWWWWWWWWWWWWWWWWWWWWWWWW.",
    ]),
    "prop": _sym([
        "........GGG.....",
        ".......GGGGG....",
        "G......GGGGGG...",
        "GG.....GGGGGG...",
    ], [
        ".WWWWWWWWWWWWWE.",
        "CWWWWWWWWWKWWWWER",
        ".WWWWWWWWWWWWWE.",
    ]),
    "fighter": _sym([
        "...GG............",
        "...GGGG..........",
        "...GGGGGG........",
        "C..GGGGGGGG......",
    ], [
        "CWWWWWWWWWWWWWW..",
        "CCWWWWWWWWWWWKWWW",
        "CWWWWWWWWWWWWWW..",
    ]),
    "heli": [
        "..R.............R...",
        "...R...........R....",
        "....R.........R.....",
        ".....R.......R......",
        "......R.....R.......",
        ".......R.WWR........",
        "C.......RWRWWW......",
        "CWWWWWWWWRWWWKW.....",
        "C.......RWRWWW......",
        ".......R.WWR........",
        "......R.....R.......",
        ".....R.......R......",
        "....R.........R.....",
        "...R...........R....",
        "..R.............R...",
    ],
}
SPRITE_KEYS = ".WGEKRC"
SPRITE_COLOURS = [None, 0xEBEBEB, 0x646473, 0xBEBEC8, 0x0078C8, 0xA0A0AA, WHITE]
sprite_pal = displayio.Palette(len(SPRITE_KEYS))
sprite_pal.make_transparent(0)
SPRITES = {}
for _name, _art in SPRITE_ART.items():
    _w = max(len(r) for r in _art)
    _bmp = displayio.Bitmap(_w, len(_art), len(SPRITE_KEYS))
    for _y, _row in enumerate(_art):
        for _x, _ch in enumerate(_row):
            _bmp[_x, _y] = SPRITE_KEYS.index(_ch)
    SPRITES[_name] = _bmp
del SPRITE_ART
sprite_g = displayio.Group()
overlay_g.append(sprite_g)
root.append(overlay_g)


def rgb565(rgb):
    return ((rgb >> 8) & 0xF800) | ((rgb >> 5) & 0x07E0) | ((rgb >> 3) & 0x001F)


def take_snapshot():
    """Copy the panel's current picture into `snap`. False if the framebuffer can't be read."""
    if not CAN_SNAP:
        return False
    try:
        mv = memoryview(fb)
        n = len(mv)
        if n == 2048:          # 16-bit elements
            for i in range(2048):
                v = mv[i]
                snap[i] = TRANS ^ 0x0040 if v == TRANS else v  # never see-through by accident
        elif n == 4096:        # bytes, little-endian RGB565
            for i in range(2048):
                v = mv[2 * i] | (mv[2 * i + 1] << 8)
                snap[i] = TRANS ^ 0x0040 if v == TRANS else v
        else:
            note_error("framebuffer size %d" % n)
            return False
    except Exception as e:  # noqa: BLE001
        note_error("snapshot: %r" % e)
        return False
    return True


def _fill(x1, x2, y, value, flip):
    """Fill row y from x1 to x2 (exclusive), mirrored when the plane flies right to left."""
    x1, x2 = max(0, x1), min(64, x2)
    if x1 >= x2:
        return
    if flip:
        x1, x2 = 64 - x2, 64 - x1
    bitmaptools.fill_region(snap, x1, y, x2, y + 1, value)


TRANSITION_STATS = {"fps": 0}  # frames per second of the last transition, reported to the Pi


def _frames(seconds):
    """Yield progress 0..1 for `seconds`, redrawing the panel after each step. Time-based, so a
    slower Portal shows fewer, bigger steps but every animation still takes the same time."""
    t0 = time.monotonic()
    n = 0
    while True:
        t = (time.monotonic() - t0) / seconds
        if t >= 1:
            break
        yield t
        wdt.feed()
        display.refresh()
        n += 1
    yield 1.0
    display.refresh()
    TRANSITION_STATS["fps"] = round(n / seconds)
    dbg("transition: %d frames in %.1fs" % (n, seconds))


def run_flyby(sprite, tail, flip):
    """A plane flies across: a '>'-shaped black band clears the old screen ahead of it and the
    new screen is revealed behind its tail."""
    bmp = SPRITES.get(sprite) or SPRITES["twin"]
    w, h = bmp.width, bmp.height
    sprite_pal[SPRITE_KEYS.index("C")] = dim(tail)
    for i, c in enumerate(SPRITE_COLOURS):
        if c is not None and SPRITE_KEYS[i] != "C":
            sprite_pal[i] = dim(c)
    tg = displayio.TileGrid(bmp, pixel_shader=sprite_pal)
    tg.flip_x = flip
    tg.y = (32 - h) // 2
    sprite_g.append(tg)
    first, last = -w - 18, 64 + 20  # sprite's left edge at the start and end, as if flying left to right
    for t in _frames(1.8):
        sx = int(first + (last - first) * t)
        front, rear = sx + w + 2, sx - 2
        for y in range(32):
            off = int(abs(y - 15.5))
            _fill(rear - off, front - off, y, 0, flip)   # black band
            _fill(-99, rear - off, y, TRANS, flip)       # new screen behind
        tg.x = 64 - sx - w if flip else sx


_radar = None  # angle bucket of each pixel (bytes)
RADAR_STEPS = 72


def _build_radar():
    """Which angle bucket (0 = 12 o'clock, clockwise) each pixel is in, as one 2 KB bytes object,
    built once at power-on. The sweep finds each bucket's pixels with bytes.find (fast, in C)."""
    global _radar
    import math
    bucket = bytearray(2048)
    for i in range(2048):
        x, y = i % 64 - 31.5, i // 64 - 15.5
        a = math.atan2(x, -y)  # 0 = straight up, clockwise
        bucket[i] = int((a % (2 * math.pi)) / (2 * math.pi) * RADAR_STEPS) % RADAR_STEPS
    _radar = bytes(bucket)
    del bucket
    gc.collect()


def _radar_paint(b, value):
    needle = bytes((b,))
    i = _radar.find(needle)
    while i >= 0:
        snap[i] = value
        i = _radar.find(needle, i + 1)


def run_radar():
    """Radar sweep: a green line sweeps clockwise from 12 o'clock, revealing the new screen."""
    beam = rgb565(dim(GREEN))
    done = 0  # buckets fully revealed
    for t in _frames(1.2):
        upto = int(t * RADAR_STEPS)
        for b in range(done, upto):
            _radar_paint(b, TRANS)
        done = max(done, upto)
        if done < RADAR_STEPS:
            _radar_paint(done, beam)


def run_slide(group):
    """Departure-board scroll in a random direction: the old screen slides off, the new one follows."""
    dx, dy = random.choice(((0, -1), (0, 1), (-1, 0), (1, 0)))
    size = 64 if dx else 32
    for t in _frames(0.6 if dy else 0.9):
        k = int(size * t)
        snap_tg.x, snap_tg.y = dx * k, dy * k
        group.x, group.y = dx * (k - size), dy * (k - size)


def _rev8(i):
    r = 0
    for _ in range(8):
        r, i = (r << 1) | (i & 1), i >> 1
    return r


_REV8 = bytes(_rev8(i) for i in range(256))  # each byte with its bits reversed
_REV3 = bytes((0, 4, 2, 6, 1, 5, 3, 7))


def run_dissolve():
    """Sparkle: the old screen dissolves pixel by pixel into the new one, in a scrambled order
    worked out on the fly (no 4 KB table to keep in memory). 5x+c mod 2048 visits every pixel
    once; reversing the 11 bits spreads neighbouring steps across the panel."""
    c = random.randrange(1024) * 2 + 1
    x, done = random.randrange(2048), 0
    for t in _frames(0.8):
        upto = int(t * 2048)
        for _ in range(done, upto):
            x = (5 * x + c) & 2047
            snap[(_REV8[x & 255] << 3) | _REV3[x >> 8]] = TRANS
        done = max(done, upto)


OVERLAY_KINDS = ("flyby", "radar", "slide", "dissolve")

# Build the radar and sparkle tables now, at power-on. Asked for later (after Wi-Fi, fonts and
# JSON have chopped the memory into small pieces) a 4 KB block may not exist any more.
try:
    _build_radar()
except MemoryError as _e:
    print("radar table: %r" % (_e,))
gc.collect()


def _available(kind):
    return not (kind == "radar" and _radar is None)


def choose_transition(ticked, has_plane):
    """One of the ticked animations at random. The fly-by needs a plane (not e.g. clock -> plane of
    the day); if it was the only one ticked, use the flap then."""
    pool = [k for k in ticked if k in ALL_TRANSITIONS and (has_plane or k != "flyby") and _available(k)]
    return random.choice(pool) if pool else "flap"


def run_transition(kind, group, plane):
    """Called after the new screen has been drawn underneath the snapshot."""
    try:
        display.auto_refresh = False
        if kind == "flyby":
            run_flyby(*plane)
        elif kind == "radar":
            run_radar()
        elif kind == "slide":
            run_slide(group)
        elif kind == "dissolve":
            run_dissolve()
    except Exception as e:  # noqa: BLE001 - a broken animation must never stop the display
        note_error("transition %s: %r" % (kind, e))
    finally:
        while len(sprite_g):
            sprite_g.pop()
        snap_tg.x = snap_tg.y = 0
        group.x = group.y = 0
        overlay_g.hidden = True
        display.auto_refresh = True


def show_diagnostics(now):
    global brightness
    brightness = max(brightness, 0.3)
    root.hidden = False
    update_rssi()
    pi = STATE["url"].replace("http://", "").split("/")[0].split(":")[0]
    age = ("%dS" % (now - STATE["last_ok"])) if STATE["last_ok"] is not None else "NEVER"
    texts = ("W " + STATE["ssid"][:14], "%s %sDB" % (STATE["ip"] or "NO IP", STATE["rssi"]),
             "PI " + (pi or "?"), "OK %s %s" % (age, STATE["via"].upper()[:6]), STATE["err"].upper() or "NO ERRORS")
    for lab, text in zip(d_lines, texts):
        set_text(lab, text)
    err_scroll.tick()
    show_only(diag_g)


def show_only(group):
    for g in (flight_g, alert_g, idle_g, msg_g, diag_g, card_g):
        g.hidden = g is not group


def message(line1, line2=""):
    set_text(m1, line1)
    set_text(m2, line2)
    show_only(msg_g)


mem_mark("screens")


# --- formatting ----------------------------------------------------------------

def fmt_alt(alt):
    if alt is None:
        return "----"
    if alt >= 10000:
        return "FL%03d" % round(alt / 100)
    return "%dFT" % alt


def fmt_altspd(d):
    spd = d.get("spd")
    return "%s %s" % (fmt_alt(d.get("alt")), ("%dKT" % spd) if spd is not None else "")


def fmt_hdg(d):
    trk, dist = d.get("trk"), d.get("dist_km")
    left = ("HDG %03d %s" % (trk, d.get("trk_txt") or "")) if trk is not None else ""
    right = d.get("dist_txt")  # formatted on the Pi (miles or km)
    if right is None:
        right = ("%.1fKM" % dist) if dist is not None and dist < 10 else (("%dKM" % dist) if dist else "")
    return (left + " " + right).strip()


ALERT_STYLE = {  # level: (banner bg, text, stripe colour, flash period in s or 0)
    "emergency": (RED, WHITE, RED, 0.5),
    "military": (NAVY, WHITE, RED, 0),
    "low": (YELLOW, BLACK, YELLOW, 1.0),
    "rare": (GOLD, BLACK, WHITE, 0),
}


last_scene = None
last_plane = None  # (sprite, tail colour, right-to-left) of the last aircraft shown, for fly-offs


def plane_of(d):
    """What a fly-by should draw for this screen, or None if there's no aircraft on it."""
    if not d.get("sprite"):
        return None
    alert = d.get("alert") or {}
    tail = parse_colour(d.get("col"), RED if alert.get("level") in ("military", "emergency") else WHITE)
    trk = d.get("trk")
    return (d["sprite"], tail, trk is not None and 180 <= trk < 360)  # heading west: right to left


def draw(d, now):
    global brightness, animate_now, last_scene, last_plane, FLAP, TRANSITIONS
    alert = d.get("alert") or {}
    scene = (d.get("mode"), d.get("callsign"), alert.get("text"))
    prev, last_scene = last_scene, scene
    brightness = float(d.get("brightness", 0.5))
    if isinstance(d.get("transitions"), list):  # ticked on the Pi's settings page
        TRANSITIONS = d["transitions"]
    elif "flap" in d:  # older Pi code: flap on/off only
        TRANSITIONS = ["flap"] if d["flap"] else []
    FLAP = 1 if TRANSITIONS else 0
    root.hidden = brightness <= 0

    kind, plane = None, plane_of(d)
    if scene != prev and prev is not None and not root.hidden and TRANSITIONS:
        if prev[1] and prev[1] == scene[1] and not d.get("demo"):
            kind = "flap"  # same aircraft, flight details <-> facts card: keep it calm
        else:
            plane = plane or last_plane  # arriving plane flies in; a departing one flies off
            kind = choose_transition(TRANSITIONS, plane is not None)
        if kind in OVERLAY_KINDS:
            if take_snapshot():
                overlay_g.hidden = False  # the old screen stays up (frozen) while we redraw underneath
            else:
                kind = "flap"
    animate_now = kind == "flap"  # new aircraft or screen: flap everything once
    if plane_of(d):
        last_plane = plane_of(d)
    mode = d.get("mode")
    if mode == "flight":
        col = parse_colour(d.get("col"), WHITE)
        logo.show(d.get("airline"), col)
        set_text(f_cs, d.get("callsign"), col)
        set_text(f_route, d.get("route"))
        set_text(f_type, d.get("type"))
        set_text(f_altspd, fmt_altspd(d))
        set_text(f_hdg, fmt_hdg(d))
        show_only(flight_g)
    elif mode == "card":
        style = d.get("style")
        title_colour = GOLD if style in ("rare", "potd") else parse_colour(d.get("col"), WHITE)
        lines = (d.get("lines") or []) + [""] * 5
        for i, lab in enumerate(c_lines):
            set_text(lab, lines[i], title_colour if i == 0 else None)
        for sc in card_scrolls:
            sc.tick()
        show_only(card_g)
    elif mode == "alert":
        alert = d.get("alert") or {}
        bg, fg, stripe, flash = ALERT_STYLE.get(alert.get("level"), ALERT_STYLE["emergency"])
        on = not flash or int(now / flash) % 2 == 0
        banner_pal[0] = dim(bg) if on else 0
        stripe_top_pal[0] = stripe_bot_pal[0] = dim(stripe) if on else 0
        set_text(a_banner, alert.get("text", "ALERT"), fg if on else bg)
        cs_type = ("%s %s" % (d.get("callsign") or "", d.get("type") or "")).strip()
        if len(cs_type) > 16:  # one line across the panel: fall back to the short code (B738)
            cs_type = ("%s %s" % (d.get("callsign") or "", d.get("type_code") or "")).strip()
        set_text(a_cs, cs_type)
        set_text(a_route, d.get("route"))
        set_text(a_altspd, fmt_altspd(d))
        set_text(a_hdg, fmt_hdg(d))
        show_only(alert_g)
    else:
        set_text(i_time, d.get("time"), animate=False)  # minute changes: no flap
        wx = (d.get("weather") or "").split(" ", 1)
        set_text(i_temp, wx[0])
        set_text(i_wx, wx[1] if len(wx) > 1 else "")
        set_text(i_date, d.get("date"))
        set_text(i_last, d.get("last") or "WAITING FOR AIRCRAFT")
        if int(now / 5) % 2:  # alternate every 5 s
            set_text(i_stats, "%s PLANES TODAY" % d.get("today", 0), animate=False)
        else:
            set_text(i_stats, "FURTHEST " + (d.get("furthest_txt") or "%sKM" % d.get("furthest_km", 0)),
                     animate=False)
        show_only(idle_g)
    if kind in OVERLAY_KINDS:
        group = idle_g  # (CircuitPython's next() has no default argument, so a plain loop)
        for g in (flight_g, alert_g, idle_g, card_g):
            if not g.hidden:
                group = g
                break
        run_transition(kind, group, plane)


# --- Wi-Fi ---------------------------------------------------------------------

esp = adafruit_esp32spi.ESP_SPIcontrol(
    busio.SPI(board.SCK, board.MOSI, board.MISO),
    DigitalInOut(board.ESP_CS), DigitalInOut(board.ESP_BUSY), DigitalInOut(board.ESP_RESET))
# Plain HTTP straight over the ESP32's sockets, into one buffer made at power-on (_http_buf,
# near the top): the adafruit_requests library needs ~15 KB of memory this board can't spare.


def http_get_json(url, timeout=4):
    """GET an http:// URL and return the parsed JSON body. Raises on any failure."""
    host, _, path = url[7:].partition("/")
    host, _, port = host.partition(":")
    buf = _http_buf
    sock = esp.get_socket()
    try:
        esp.socket_connect(sock, host, int(port or 80), conn_mode=esp.TCP_MODE)
        esp.socket_write(sock, ("GET /%s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n"
                                % (path, host)).encode())
        n, body, length = 0, -1, None
        end = time.monotonic() + timeout
        while length is None or n < body + length:
            if time.monotonic() > end:
                raise OSError("timed out")
            avail = esp.socket_available(sock)
            if not avail:
                if body >= 0 and length is None and esp.socket_status(sock) != 4:
                    break  # no Content-Length: the server closing the connection ends it
                time.sleep(0.005)
                continue
            if n + avail > len(buf):
                raise ValueError("reply too big")
            chunk = esp.socket_read(sock, avail)
            buf[n:n + len(chunk)] = chunk
            start = max(0, n - 3)
            n += len(chunk)
            if body < 0:
                for i in range(start, n - 3):
                    if buf[i] == 13 and buf[i + 1] == 10 and buf[i + 2] == 13 and buf[i + 3] == 10:
                        body = i + 4
                        head = bytes(buf[:i]).decode("utf-8", "ignore").lower().split("\r\n")
                        status = head[0].split(" ")
                        if len(status) < 2 or status[1] != "200":
                            raise ValueError("HTTP %s" % (status[1] if len(status) > 1 else "?"))
                        for line in head[1:]:
                            if line.startswith("content-length:"):
                                length = int(line[15:])
                        break
        if body < 0:
            raise ValueError("no reply")
        return json.loads(memoryview(buf)[body:n])
    finally:
        try:
            esp.socket_close(sock)
        except Exception:  # noqa: BLE001
            pass


# Hardware watchdog: if the code ever hangs (the ESP32 Wi-Fi chip occasionally wedges),
# the board resets itself after 16 s without a feed. Fed in the main loop and around slow calls.
wdt = microcontroller.watchdog
wdt.timeout = 16
wdt.mode = WatchDogMode.RESET


_net_next = 0
_net_good = None   # index of the network that last connected: tried first, and given extra chances
_net_misses = 0    # failed tries of _net_good in a row


def connect_wifi(quiet=False):
    """Try the networks in settings.toml (yours and Dad's). True if connected.
    quiet (demo playing): try just one network per call, so the demo only pauses briefly, and
    don't cover it with status messages."""
    global _net_next, _net_good, _net_misses
    if esp.is_connected:
        return True
    for _ in range(1 if quiet else len(NETWORKS)):
        # A weak signal often needs a few goes: stick with the network that worked last time for
        # 3 tries before trying the others (at Dad's, his; at yours, yours).
        if _net_good is not None and _net_misses < 3:
            i = _net_good
        else:
            i = _net_next % len(NETWORKS)
            _net_next += 1
        ssid, password = NETWORKS[i]
        wdt.feed()
        if not quiet:
            message("WIFI...", ssid[:16])
        try:
            esp.connect_AP(ssid, password)
            _net_good, _net_misses = i, 0
            close_udp()  # any listener from before the Wi-Fi dropped is gone
            STATE["ssid"] = ssid
            STATE["ip"] = esp.pretty_ip(esp.ip_address)
            update_rssi()
            log("wifi connected:", ssid, "ip", STATE["ip"], "rssi", STATE["rssi"])
            if not quiet:
                message("WIFI OK", STATE["ip"])
                for _ in range(3 if DEBUG else 1):
                    wdt.feed()
                    time.sleep(1)
            return True
        except (RuntimeError, ConnectionError, OSError) as e:
            note_error("wifi %s: %s" % (ssid, e))
            if i == _net_good:
                _net_misses += 1
        wdt.feed()
    if not quiet:
        message("NO WIFI", "RETRYING")
    return False


def update_rssi():
    try:
        STATE["rssi"] = esp.ap_info.rssi        # newer adafruit_esp32spi
    except AttributeError:
        try:
            STATE["rssi"] = esp.rssi           # older versions
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def quote(text):
    out = ""
    for ch in str(text):
        out += ch if ch.isalpha() or ch.isdigit() or ch in "-._" else "%%%02X" % (ord(ch) & 0xFF)
    return out


def report_query():
    up = int(time.monotonic())
    q = "portal=%s&ip=%s&rssi=%s&ssid=%s&via=%s&fails=%d&up=%d&reset=%s" % (
        VERSION, STATE["ip"], STATE["rssi"], quote(STATE["ssid"]), STATE["via"], STATE["fails"], up, RESET_REASON)
    if TRANSITION_STATS["fps"]:
        q += "&tfps=%d" % TRANSITION_STATS["fps"]
    if STATE["err"]:
        q += "&err=" + quote(STATE["err"])
    return q


mem_mark("wifi")


# --- finding the Pi ------------------------------------------------------------
# Order tried: the address that last worked (kept across power cuts in non-volatile memory),
# DISPLAY_URLS from settings.toml, the Pi's hostname, then listening for the Pi's broadcast.

def load_saved_url():
    try:
        n = microcontroller.nvm[0]
        if 0 < n < 200:
            url = bytes(microcontroller.nvm[1:1 + n]).decode()
            if url.startswith("http://"):
                return url
    except Exception:  # noqa: BLE001 - blank or unreadable memory
        pass
    return None


def save_url(url):
    try:
        b = url.encode()
        if load_saved_url() != url and len(b) < 200:
            microcontroller.nvm[0:1 + len(b)] = bytes([len(b)]) + b
    except Exception as e:  # noqa: BLE001
        print("couldn't save url:", e)


_udp = None  # the listening socket: opened once and kept (the Wi-Fi chip can't reopen it reliably)


def discover(seconds=8):
    """Listen for the Pi's UDP broadcast ("FLIGHTDISPLAY <url>", sent every few seconds)."""
    global _udp
    log("listening for the Pi's broadcast on UDP", DISCOVERY_PORT, "for", seconds, "s")
    if _udp is None:
        try:
            sock = esp.get_socket()
            esp.start_server(DISCOVERY_PORT, sock, conn_mode=esp.UDP_MODE)
            _udp = sock
        except Exception as e:  # noqa: BLE001 - older Wi-Fi firmware: fall back to fixed addresses
            note_error("discovery unavailable: %s" % e)
            return None
    packets = 0
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            wdt.feed()
            n = esp.socket_available(_udp)
            if n:
                packets += 1
                raw = bytes(esp.socket_read(_udp, n)).decode("utf-8", "ignore")
                dbg("udp packet:", repr(raw))
                for line in raw.split("FLIGHTDISPLAY")[1:]:  # several broadcasts may be queued up
                    url = (line.split() or [""])[0]
                    if url.startswith("http://"):
                        log("found Pi at", url)
                        STATE["found"] += 1
                        return url
            time.sleep(0.2)
        note_error("no broadcast heard (%d other packets)" % packets)
    except Exception as e:  # noqa: BLE001 - the socket died (e.g. Wi-Fi dropped): open a new one next time
        note_error("discovery failed: %s" % e)
        close_udp()
    return None


def close_udp():
    global _udp
    if _udp is not None:
        try:
            esp.socket_close(_udp)
        except Exception:  # noqa: BLE001
            pass
        _udp = None


for u in URLS:
    URL_SOURCE[u] = "settings"
saved = load_saved_url()
if saved and saved not in URLS:
    URLS.insert(0, saved)
    URL_SOURCE[saved] = "saved"
if HOSTNAME_URL not in URLS:
    URLS.append(HOSTNAME_URL)
    URL_SOURCE[HOSTNAME_URL] = "hostname"


def fetch(listen=True):
    """Return the display dict from the first Pi URL that answers, or None.
    listen=False skips listening for the Pi's broadcast (8 s), to keep the offline demo moving."""
    data = try_urls()
    if data is None and listen:
        url = discover()
        if url:
            if url in URLS:
                URLS.remove(url)
            URLS.insert(0, url)
            URL_SOURCE[url] = "discovery"
            data = try_urls()
    return data


def try_urls():
    global URLS
    for i, url in enumerate(URLS):
        wdt.feed()
        t = time.monotonic()
        try:
            data = http_get_json(url + "?" + report_query())
            if i:  # try the one that worked first next time
                URLS = [url] + URLS[:i] + URLS[i + 1:]
            if STATE["url"] != url:
                log("using", url, "(%s)" % URL_SOURCE.get(url, "?"))
            save_url(url)
            STATE.update(url=url, via=URL_SOURCE.get(url, "?"), last_ok=time.monotonic(), fails=0, err="")
            dbg("fetch ok %dms %s %s" % ((time.monotonic() - t) * 1000, data.get("mode"), data.get("callsign", "")))
            return data
        except Exception as e:  # noqa: BLE001 - any failure just means "try the next one"
            dbg("fetch", url, "failed after %dms:" % ((time.monotonic() - t) * 1000), e)
            last = "%s: %s" % (URL_SOURCE.get(url, "?"), e)
    STATE["fails"] += 1
    note_error(last if URLS else "no Pi address known yet")
    return None


# --- why did we (re)start? ---------------------------------------------------------
try:
    RESET_REASON = str(microcontroller.cpu.reset_reason).split(".")[-1]  # e.g. POWER_ON, WATCHDOG
except Exception:  # noqa: BLE001
    RESET_REASON = "UNKNOWN"
log("flight display", VERSION, "starting; reset reason:", RESET_REASON, "debug:", DEBUG, "free:", gc.mem_free())
try:
    n = microcontroller.nvm[256]
    if 0 < n <= 120:
        note_error(bytes(microcontroller.nvm[257:257 + n]).decode("utf-8", "ignore"))
        microcontroller.nvm[256] = 0
except Exception:  # noqa: BLE001
    pass


# --- offline demo -----------------------------------------------------------------
# Built-in screens (same format as the Pi's display.json), played 8 s each while there's no data
# from the Pi, so the panel always does something. The first card says what it's waiting for.

DEMO_STEP_S = 8
DEMO_BRIGHTNESS = 0.3
OFFLINE = (
    {"mode": "card", "style": "potd", "callsign": "DEMO", "lines": ["FLIGHT TRACKER", "DEMO MODE", "", "", ""],
     "transitions": ["flap"]},
    {"mode": "flight", "callsign": "EZY45T", "airline": "EZY", "col": "#FF6600", "route": "AGP-BOH",
     "type": "A320", "type_code": "A320", "alt": 2100, "spd": 140, "trk": 80, "trk_txt": "E",
     "dist_txt": "1.1MI", "sprite": "twin", "transitions": ["flyby"]},
    {"mode": "card", "style": "facts", "callsign": "EZY45T", "col": "#FF6600", "transitions": ["radar"],
     "lines": ["AIRBUS A320", "CRUISE 450KT", "CEILING 39000FT", "FIRST FLEW 1987", "ABOUT 180 SEATS"]},
    {"mode": "alert", "alert": {"level": "rare", "text": "RARE! BOEING 747-400"}, "callsign": "BAW2490",
     "airline": "BAW", "col": "#3A6FD8", "route": "LHR-BOH", "type": "747-400", "type_code": "B744",
     "alt": 3800, "spd": 190, "trk": 260, "trk_txt": "W", "dist_txt": "2MI", "sprite": "quad",
     "transitions": ["flyby"]},
    {"mode": "alert", "alert": {"level": "military", "text": "RED ARROWS"}, "callsign": "RED1",
     "type": "HAWK T1", "type_code": "HAWK", "route": "", "alt": 1500, "spd": 300, "trk": 90, "trk_txt": "E",
     "dist_txt": "", "sprite": "fighter", "transitions": ["slide"]},
    {"mode": "alert", "alert": {"level": "low", "text": "LOOK UP!"}, "callsign": "RYR52KM", "airline": "RYR",
     "col": "#F1C933", "route": "BOH-DUB", "type": "737-800", "type_code": "B738", "alt": 1800, "spd": 145,
     "trk": 80, "trk_txt": "E", "dist_txt": "0.6MI", "sprite": "twin", "transitions": ["dissolve"]},
    {"mode": "card", "style": "potd", "callsign": "POTD", "transitions": ["radar"],
     "lines": ["SPITFIRE", "MAX 360KT", "CEILING 43000FT", "FIRST FLEW 1936", "WW2 RAF LEGEND"]},
    {"mode": "flight", "callsign": "HLE87", "airline": None, "col": "#FF3030", "route": "0.8MI NE",
     "type": "H135", "type_code": "EC35", "alt": 900, "spd": 110, "trk": 300, "trk_txt": "NW",
     "dist_txt": "0.8MI", "sprite": "heli", "transitions": ["flyby"]},
    {"mode": "alert", "alert": {"level": "emergency", "text": "EMERGENCY 7700"}, "callsign": "BAW123",
     "airline": "BAW", "col": "#3A6FD8", "route": "LGW-BOH", "type": "A320", "type_code": "A320",
     "alt": 8000, "spd": 280, "trk": 250, "trk_txt": "W", "dist_txt": "4MI", "sprite": "twin",
     "transitions": ["slide"]},
)


def demo_status():
    """Lines 3-5 of the demo's title card: what the panel is waiting for."""
    if not NETWORKS:
        return ["SETUP NEEDED", "SETTINGS.TOML", ""]
    if not esp.is_connected:
        return ["NO WIFI YET", "WEAK SIGNAL?" if _net_misses >= 3 else "CONNECTING...", ""]
    return ["WIFI OK", "LOOKING FOR PI", STATE["ip"]]


_demo_step = None


def draw_demo(now):
    """Draw the demo; if it ever fails, switch it off (and fall back to status messages) rather than
    let a demo bug restart the board over and over before it can reach the Pi."""
    global OFFLINE_DEMO
    try:
        _draw_demo(now)
    except Exception as e:  # noqa: BLE001
        OFFLINE_DEMO = 0
        gc.collect()
        note_error("demo off: %r (free %d)" % (e, gc.mem_free()))
        message("NO DATA", "IS THE PI ON?")


def _draw_demo(now):
    global _demo_step
    step = int(now // DEMO_STEP_S)
    if step != _demo_step:
        _demo_step = step
        gc.collect()
    d = dict(OFFLINE[step % len(OFFLINE)])
    if d["callsign"] == "DEMO":
        d["lines"] = d["lines"][:2] + demo_status()
    d["demo"] = True
    # overnight (the Pi last said "off"): stay off; otherwise not too bright
    d["brightness"] = 0 if brightness <= 0 and STATE["last_ok"] is not None else DEMO_BRIGHTNESS
    draw(d, now)
    tick_flaps(now)
    banner_scroll.tick()


# --- main loop -----------------------------------------------------------------

if not NETWORKS:
    note_error("no WIFI_SSID_1 in settings.toml")
    while True:
        wdt.feed()
        if OFFLINE_DEMO:
            draw_demo(time.monotonic())
            time.sleep(0.05)
        else:
            message("SETUP NEEDED", "SETTINGS.TOML")
            time.sleep(1)


def run():
    global brightness
    data, last_ok, next_poll, failures, attempts = None, 0, 0, 0, 0
    if OFFLINE_DEMO:
        draw_demo(time.monotonic())  # something on screen straight away
    while True:
        wdt.feed()
        now = time.monotonic()
        fresh = data is not None and now - last_ok < STALE_SECONDS
        demo = OFFLINE_DEMO and not fresh and DEBUG < 2
        if now >= next_poll:
            # While the demo plays, check less often and more briefly so it keeps moving.
            next_poll = now + (POLL_SECONDS if not demo else 15)
            attempts += 1
            new = None
            if connect_wifi(quiet=demo):
                new = fetch(listen=not demo or attempts % 2 == 0)
            if new is not None:
                data, last_ok, failures = new, time.monotonic(), 0
            else:
                failures += 1
                # Reset the Wi-Fi link (the next loop reconnects) only if the Pi has answered before
                # on this Wi-Fi and has now gone quiet for a while: that's the ESP32 wedging. Before
                # the Pi has ever answered, dropping a working (weak) Wi-Fi link only makes it worse.
                if failures >= 20 and data is not None and esp.is_connected:
                    close_udp()
                    try:
                        esp.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                    failures = 0
            gc.collect()
            now = time.monotonic()
            fresh = data is not None and now - last_ok < STALE_SECONDS
            demo = OFFLINE_DEMO and not fresh and DEBUG < 2
        if DEBUG >= 2:
            show_diagnostics(now)
        elif fresh:
            draw(data, now)
            tick_flaps(now)
            last_scroll.tick()
            banner_scroll.tick()
        elif demo:
            draw_demo(now)
        else:
            # Pi gone quiet: say so, dimly. If the panel was off (overnight), stay off.
            brightness = min(brightness, 0.15) if brightness > 0 else 0
            root.hidden = brightness <= 0
            message("NO DATA", "IS THE PI ON?")
        time.sleep(0.05)


mem_mark("demo")
log("memory free after:", ", ".join(MEM))
try:
    run()
except Exception as e:  # noqa: BLE001 - anything unexpected: log it, remember it, start again
    try:
        import traceback
        traceback.print_exception(e)
    except Exception:  # noqa: BLE001
        log("crashed:", e)
    try:  # kept in non-volatile memory so it's reported to the Pi after the restart
        b = ("crash: %r" % e).encode()[:120]
        microcontroller.nvm[256:257 + len(b)] = bytes([len(b)]) + b
    except Exception:  # noqa: BLE001
        pass
    time.sleep(5)
    microcontroller.reset()
