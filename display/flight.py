# Flight display for the Adafruit Matrix Portal (M4) + 64x32 RGB panel. CircuitPython 10.x.
# Loaded by code.py; install as flight.mpy with display/install.sh (see README).
#
# Deliberately dumb: the antenna Pi decides everything and serves /display.json;
# this file only draws it. Fix behaviour on the Pi (reachable over Tailscale), not here.

import gc
import json
import os
import random
import sys
import time

import bitmaptools
import board
import busio
import displayio
import microcontroller
import terminalio
from digitalio import DigitalInOut, Pull
from watchdog import WatchDogMode

from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.bitmap_label import Label  # faster text updates than label.Label
from adafruit_matrixportal.matrix import Matrix

try:
    import wifi  # noqa: F401 - only the Matrix Portal S3 (Wi-Fi built in) has this
    BIG_BOARD = True  # S3: ~10x the M4's memory, so the extra animations are switched on
except ImportError:
    BIG_BOARD = False  # M4

_http_buf = bytearray(2048)  # the Pi's replies (~700 bytes); made first, while memory is in one piece
MEM = []  # free memory after each stage of start-up, logged once the main loop starts

# This file has ~350 global names, and the table that holds them doubles in size as they're added.
# Left alone, the last doubling (~3 KB in one piece) comes at the very end of start-up, when the
# memory is already in small pieces, and fails (MemoryError at the bottom of this file). So grow
# it to full size now, while memory is still in one piece: add 380 dummy names, then remove them
# (the table keeps its size, and the real names fill the free slots).
_g = globals()
for _i in range(380):
    _g["_pad%d" % _i] = None
for _i in range(380):
    del _g["_pad%d" % _i]
del _g, _i
gc.collect()


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
# settings page): any of flyby, radar, slide, dissolve, flap. Empty = instant. Radar and slide
# need memory the M4 can't spare, so they're only on the S3.
ALL_TRANSITIONS = ("flyby", "radar", "slide", "dissolve", "flap") if BIG_BOARD else ("flyby", "dissolve", "flap")
_t = os.getenv("TRANSITIONS")
TRANSITIONS = [x.strip() for x in _t.split(",") if x.strip()] if _t is not None else \
    (list(ALL_TRANSITIONS) if FLAP else [])
# If the transitions show odd colours on the real panel, set SNAP_SWAPPED = 1 (byte order of the snapshot).
SNAP_SWAPPED = int(os.getenv("SNAP_SWAPPED") or 0)
# Until the Pi answers, the panel shows a clock with Wi-Fi and Pi icons (red, blinking = the problem).
# The built-in demo plays only when the DOWN button is pressed (OFFLINE_DEMO is no longer used).
OFFLINE_BRIGHTNESS = 0.2
VERSION = "2026.11.13d"

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
    if "Expected" in str(text) and "but got" in str(text):
        STATE["esp_bad"] = True  # the Wi-Fi chip stopped answering properly: run() resets it
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

# Colour depth. 6 = smoothest shading. On the S3 the whole panel can flash each time the screen
# changes while Wi-Fi is busy (a known ESP32-S3 + RGB matrix timing problem); fewer bits makes each
# refresh lighter, so the S3 defaults to 4. Override with BIT_DEPTH in settings.toml (1-6).
BIT_DEPTH = int(os.getenv("BIT_DEPTH") or (4 if BIG_BOARD else 6))
matrix = Matrix(width=64, height=32, bit_depth=BIT_DEPTH)
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
        flapping[id(lab)] = [lab, text, time.monotonic(), lab.text]  # old text: unchanged letters stay put
    else:
        lab.text = text


def tick_flaps(now):
    for key in list(flapping):
        lab, target, start, old = flapping[key]
        t = now - start
        out = []
        done = True
        for i, ch in enumerate(target):
            if ch == " " or (i < len(old) and old[i] == ch) or t >= FLAP_SPIN + i * FLAP_STAGGER:
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


# Fallback icons (16x16), used when there's no /logos/<ICAO>.bmp: an airliner,
# a light aircraft or a helicopter, from the "sprite" kind the Pi sends.
ICON_ART = {
    "plane": (
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
    ),
    "prop": (
        "................",
        "....########....",
        ".......##.......",
        "......####......",
        "################",
        "################",
        "......####......",
        ".......##.......",
        ".......##.......",
        ".......##.......",
        ".......##.......",
        "....########....",
        ".......##.......",
        "................",
        "................",
        "................",
    ),
    "heli": (
        "................",
        "................",
        "................",
        ".##############.",
        ".......#........",
        ".....#####.....#",
        "...########...##",
        "..#############.",
        "..##########...#",
        "...########.....",
        ".....#...#......",
        "...#########....",
        "................",
        "................",
        "................",
        "................",
    ),
}
ICONS = {}
for _name, _art in ICON_ART.items():
    _bmp = displayio.Bitmap(16, 16, 2)
    for yy, row in enumerate(_art):
        for xx, ch in enumerate(row):
            _bmp[xx, yy] = 1 if ch == "#" else 0
    ICONS[_name] = _bmp
del ICON_ART
plane_pal = displayio.Palette(2)
plane_pal.make_transparent(0)


def _load_bmp(path):
    """An 8-bit indexed BMP (the logos) into a Bitmap + Palette in memory."""
    with open(path, "rb") as f:
        data = f.read()
    off = int.from_bytes(data[10:14], "little")
    hsize = int.from_bytes(data[14:18], "little")
    w = int.from_bytes(data[18:22], "little")
    h = int.from_bytes(data[22:26], "little")
    if int.from_bytes(data[28:30], "little") != 8:
        raise ValueError("not an 8-bit BMP")
    ncol = int.from_bytes(data[46:50], "little") or 256
    pal = displayio.Palette(ncol)
    p = 14 + hsize
    for i in range(ncol):
        b, g, r = data[p + 4 * i], data[p + 4 * i + 1], data[p + 4 * i + 2]
        pal[i] = (r << 16) | (g << 8) | b
    bmp = displayio.Bitmap(w, abs(h), ncol)
    stride = (w + 3) & ~3
    for y in range(abs(h)):
        row = off + (abs(h) - 1 - y if h > 0 else y) * stride  # BMP rows go bottom-up
        for x in range(w):
            bmp[x, y] = data[row + x]
    return bmp, pal


class Logo:
    """16x16 airline logo in the top-left; dims by rescaling its palette."""

    def __init__(self, group):
        self.group = displayio.Group()
        group.append(self.group)
        self.key = None
        self.base = []
        self.pal = None

    def show(self, icao, colour, kind=None):
        kind = kind if kind in ("prop", "heli") else "plane"
        key = (icao, kind)
        if key != self.key:
            self.key = key
            while len(self.group):
                self.group.pop()
            bmp = None
            if icao:
                try:
                    if BIG_BOARD:  # S3: into memory once, since reading flash while drawing makes the panel flash
                        bmp, pal = _load_bmp("/logos/%s.bmp" % icao)
                    else:  # M4: read from flash as it's drawn, so a logo costs almost no memory
                        bmp = displayio.OnDiskBitmap("/logos/%s.bmp" % icao)
                        pal = bmp.pixel_shader
                    len(pal)  # a palette (indexed BMP), so it can be dimmed
                except (OSError, ValueError, TypeError, AttributeError, IndexError):
                    bmp = None
            if bmp is None:
                bmp, pal = ICONS[kind], plane_pal
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
# Wi-Fi or Pi icon, top right of the clock: only while there's a problem, and only the one that's wrong.
icons_g = displayio.Group()
icons_g.hidden = True
idle_g.append(icons_g)


def _icon(rows, x):
    bmp = displayio.Bitmap(len(rows[0]), len(rows), 2)
    for y, row in enumerate(rows):
        for xx, ch in enumerate(row):
            if ch == "X":
                bmp[xx, y] = 1
    pal = displayio.Palette(2)
    pal.make_transparent(0)
    tg = displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=1)
    icons_g.append(tg)
    return tg, pal


wifi_tg, wifi_pal = _icon((".XXXXX.",
                  "X.....X",
                  "..XXX..",
                  ".X...X.",
                  ".......",
                  "...X..."), 57)
pi_tg, pi_pal = _icon((".X...X.",  # a mast sending out signals: the Pi and its aerial
                "X..X..X",
                "X.XXX.X",
                "X..X..X",
                ".X.X.X.",
                "...X...",
                "..XXX.."), 56)
root.append(idle_g)

# Facts card: five lines (name, speed, ceiling, first flight, fun fact), sent ready-made by the Pi.
card_g = displayio.Group()
CARD_COLOURS = (WHITE, CYAN, GREEN, AMBER, GREY)
c_lines = [label(card_g, 0, 1 + 6 * i, colour=c) for i, c in enumerate(CARD_COLOURS)]
card_scrolls = [Scroller(lab) for lab in c_lines]
root.append(card_g)

# Message screen: Wi-Fi / Pi problems.
msg_g = displayio.Group()
m1 = label(msg_g, 0, 8, colour=AMBER)
m2 = label(msg_g, 0, 16, colour=GREY)
root.append(msg_g)


# Diagnostics screen (DEBUG = 2): what the Portal sees, for checking at Dad's without a laptop.


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


_radar = None  # angle bucket of each pixel (bytes), S3 only
RADAR_STEPS = 90
RADAR_TRAIL = 10  # buckets of fading green behind the beam


def _build_radar():
    """Which angle bucket (0 = 12 o'clock, clockwise) each pixel is in, as one 2 KB bytes object.
    The sweep finds each bucket's pixels with bytes.find (fast, in C)."""
    global _radar
    import math
    bucket = bytearray(2048)
    for i in range(2048):
        x, y = i % 64 - 31.5, (i // 64 - 15.5) * 2  # *2: the sweep looks round on a 2:1 panel
        a = math.atan2(x, -y)  # 0 = straight up, clockwise
        bucket[i] = int((a % (2 * math.pi)) / (2 * math.pi) * RADAR_STEPS) % RADAR_STEPS
    _radar = bytes(bucket)
    gc.collect()


def _radar_paint(b, value):
    needle = bytes((b,))
    i = _radar.find(needle)
    while i >= 0:
        snap[i] = value
        i = _radar.find(needle, i + 1)


def run_radar():
    """Radar sweep: a bright green beam sweeps clockwise from 12 o'clock with a fading green
    afterglow behind it, like a real radar screen, revealing the new screen as the glow fades."""
    shades = [rgb565(dim(_mix(GREEN, 0.5 + 0.5 * k / RADAR_TRAIL))) for k in range(RADAR_TRAIL)]  # glow starts at half
    shades[0] = rgb565(dim(0xC0FFC0))  # the beam itself: nearly white
    painted = -1  # last bucket drawn
    total = RADAR_STEPS + RADAR_TRAIL
    for t in _frames(1.6):
        head = int(t * total)
        if head == painted:
            continue
        for b in range(max(0, painted - RADAR_TRAIL), min(head + 1, RADAR_STEPS)):  # every bucket whose shade changed
            age = head - b
            _radar_paint(b, shades[age] if age < RADAR_TRAIL else TRANS)
        painted = head
    for b in range(RADAR_STEPS):  # make sure nothing is left over
        _radar_paint(b, TRANS)


def _mix(rgb, k):
    """rgb faded towards black by k (0 = full, 1 = black)."""
    f = 1 - k
    return (int(((rgb >> 16) & 255) * f) << 16) | (int(((rgb >> 8) & 255) * f) << 8) | int((rgb & 255) * f)


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
if BIG_BOARD:
    _build_radar()


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


STATUS_COLOURS = (CYAN, WHITE, AMBER, GREEN, RED)


def show_diagnostics(now):
    """Status screen (UP button, or DEBUG = 2). Borrows the facts card's five text lines rather
    than keeping five more labels in memory."""
    global brightness, last_scene
    brightness = max(brightness, 0.3)
    root.hidden = False
    update_rssi()
    pi = STATE["url"].replace("http://", "").split("/")[0].split(":")[0]
    age = ("%dS" % (now - STATE["last_ok"])) if STATE["last_ok"] is not None else "NEVER"
    texts = ("W " + STATE["ssid"][:14], "%s %sDB" % (STATE["ip"] or "NO IP", STATE["rssi"]),
             "PI " + (pi or "?"), "OK %s %s" % (age, STATE["via"].upper()[:6]), STATE["err"].upper() or "NO ERRORS")
    for lab, text, colour in zip(c_lines, texts, STATUS_COLOURS):
        set_text(lab, text, colour, animate=False)
    card_scrolls[4].tick()
    show_only(card_g)
    last_scene = None  # whatever comes next is drawn afresh (no transition from this screen)


def show_only(group):
    for g in (flight_g, alert_g, idle_g, msg_g, card_g):
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
        logo.show(d.get("airline"), col, d.get("sprite"))
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
            set_text(lab, lines[i], title_colour if i == 0 else CARD_COLOURS[i])
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
        set_text(i_last, d.get("last") or "WAITING FOR AIRCRAFT", CYAN)
        if int(now / 5) % 2:  # alternate every 5 s
            set_text(i_stats, "%s PLANES TODAY" % d.get("today", 0), GREEN, animate=False)
        else:
            set_text(i_stats, "FURTHEST " + (d.get("furthest_txt") or "%sKM" % d.get("furthest_km", 0)),
                     GREEN, animate=False)
        icons_g.hidden = True
        show_only(idle_g)
    if kind in OVERLAY_KINDS:
        group = idle_g  # (CircuitPython's next() has no default argument, so a plain loop)
        for g in (flight_g, alert_g, idle_g, card_g):
            if not g.hidden:
                group = g
                break
        run_transition(kind, group, plane)


# --- Wi-Fi ---------------------------------------------------------------------

if BIG_BOARD:  # Matrix Portal S3: Wi-Fi is built into the main chip
    from netcompat import NativeWifi
    esp = NativeWifi()
    BOARD_KIND = "S3"
else:  # Matrix Portal M4: a separate ESP32 Wi-Fi chip on SPI
    from adafruit_esp32spi import adafruit_esp32spi
    esp = adafruit_esp32spi.ESP_SPIcontrol(
        busio.SPI(board.SCK, board.MOSI, board.MISO),
        DigitalInOut(board.ESP_CS), DigitalInOut(board.ESP_BUSY), DigitalInOut(board.ESP_RESET))
    BOARD_KIND = "M4"
# Plain HTTP straight over the ESP32's sockets, into one buffer made at power-on (_http_buf,
# near the top): the adafruit_requests library needs ~15 KB of memory this board can't spare.


def _ip_of(host):
    """4-byte address to connect to, worked out the way adafruit_requests does it (numbers as-is,
    names via the ESP32's DNS lookup) rather than handing the ESP32 a name to connect to."""
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return bytes(int(p) for p in parts)
    return esp.get_host_by_name(host)


_CL = b"content-length:"


def _content_length(buf, end):
    """Content-Length from the reply headers in buf[:end], read in place (no copies), or None."""
    i = 0
    while i < end:
        if buf[i] == 10:  # start of a header line
            j = 0
            while j < 15 and (buf[i + 1 + j] | 32) == _CL[j]:  # | 32: lower case
                j += 1
            if j == 15:
                k, n = i + 16, 0
                while k < end and buf[k] == 32:
                    k += 1
                while k < end and 48 <= buf[k] <= 57:
                    n = n * 10 + buf[k] - 48
                    k += 1
                return n
        i += 1
    return None


def http_get_json(url, query=(), timeout=4):
    """GET an http:// URL (plus `query`, sent piece by piece) and return the parsed JSON body.
    Raises on any failure. Sent in small pieces rather than one long string: once the memory is
    in small fragments, a 250-byte string can fail where ten 25-byte ones don't."""
    host, _, path = url[7:].partition("/")
    host, _, port = host.partition(":")
    buf = _http_buf
    sock = esp.get_socket()
    try:
        esp.socket_connect(sock, _ip_of(host), int(port or 80), conn_mode=esp.TCP_MODE)
        esp.socket_write(sock, b"GET /")
        esp.socket_write(sock, path.encode())
        for piece in query:
            esp.socket_write(sock, piece.encode())
        esp.socket_write(sock, b" HTTP/1.0\r\nConnection: close\r\n\r\n")
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
            chunk = esp.socket_read(sock, min(avail, 64))  # small pieces: big ones fail once memory is fragmented
            buf[n:n + len(chunk)] = chunk
            start = max(0, n - 3)
            n += len(chunk)
            if body < 0:
                for i in range(start, n - 3):
                    if buf[i] == 13 and buf[i + 1] == 10 and buf[i + 2] == 13 and buf[i + 3] == 10:
                        body = i + 4
                        if not (buf[9] == 50 and buf[10] == 48 and buf[11] == 48):  # "HTTP/1.0 200 OK"
                            raise ValueError("HTTP %s" % bytes(buf[9:12]).decode())
                        length = _content_length(buf, i)
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
    """URL-safe text. Anything unusual becomes "-" (it's only shown on the Pi's debug page)."""
    return "".join(ch if ch.isalpha() or ch.isdigit() or ch in "-._" else "-" for ch in str(text)[:40])


def report_query():
    """The Portal's status for the Pi's /debug page, as short pieces (sent one by one)."""
    yield "?portal=" + VERSION
    yield "&ip=" + STATE["ip"]
    yield "&rssi=%s&fails=%d" % (STATE["rssi"], STATE["fails"])
    yield "&ssid=" + quote(STATE["ssid"])
    yield "&via=" + STATE["via"]
    yield "&up=%d&reset=%s" % (int(time.monotonic()), RESET_REASON)
    yield "&free=%d" % gc.mem_free()
    if TRANSITION_STATS["fps"]:
        yield "&tfps=%d" % TRANSITION_STATS["fps"]
    if STATE["err"]:
        yield "&err=" + quote(STATE["err"])


mem_mark("wifi")


# --- finding the Pi ------------------------------------------------------------
# Order tried: the address that last worked (kept across power cuts in non-volatile memory),
# DISPLAY_URLS from settings.toml, the Pi's hostname, then listening for the Pi's broadcast.

_saved_url = False  # what's in non-volatile memory, read once (False = not read yet): on the S3
#                     every read touches flash, which makes the panel flash


def load_saved_url():
    global _saved_url
    if _saved_url is False:
        _saved_url = _read_saved_url()
    return _saved_url


def _read_saved_url():
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
    global _saved_url
    try:
        b = url.encode()
        if load_saved_url() != url and len(b) < 200:
            microcontroller.nvm[0:1 + len(b)] = bytes([len(b)]) + b
            _saved_url = url
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


_skip_until = {}  # url -> time: the name "flighttracker" doesn't resolve on most home routers, so
#                   after it fails, leave it alone for 10 minutes instead of retrying every 3 s.


def try_urls():
    global URLS
    last = "no Pi address known yet"
    for i, url in enumerate(URLS):
        if time.monotonic() < _skip_until.get(url, 0):
            continue
        wdt.feed()
        gc.collect()  # clear the garbage first, so the reply has the most room
        t = time.monotonic()
        try:
            data = http_get_json(url, report_query())
            STATE["oom"] = 0
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
            if isinstance(e, MemoryError):
                STATE["oom"] = STATE.get("oom", 0) + 1
            if URL_SOURCE.get(url) == "hostname":
                _skip_until[url] = time.monotonic() + 600
    STATE["fails"] += 1
    note_error(last)
    return None


# --- why did we (re)start? ---------------------------------------------------------
try:
    RESET_REASON = str(microcontroller.cpu.reset_reason).split(".")[-1]  # e.g. POWER_ON, WATCHDOG
except Exception:  # noqa: BLE001
    RESET_REASON = "UNKNOWN"
log("flight display", VERSION, "on", BOARD_KIND, "bit depth", BIT_DEPTH, "starting; reset reason:", RESET_REASON, "debug:", DEBUG, "free:", gc.mem_free())
try:
    n = microcontroller.nvm[256]
    if 0 < n <= 120:
        note_error(bytes(microcontroller.nvm[257:257 + n]).decode("utf-8", "ignore"))
        microcontroller.nvm[256] = 0
except Exception:  # noqa: BLE001
    pass


# --- demo (DOWN button) ----------------------------------------------------------------
# Built-in screens (in demo.py, same format as the Pi's display.json), 8 s each. The first card
# says what the panel is waiting for.

DEMO_STEP_S = 8
DEMO_BRIGHTNESS = 0.3
_demo = None  # the demo module (demo.py), loaded only while the demo plays: it costs ~3 KB of memory


def demo_status():
    """Lines 3-5 of the demo's title card: what the panel is waiting for."""
    if not NETWORKS:
        return ["SETUP NEEDED", "SETTINGS.TOML", ""]
    if not esp.is_connected:
        return ["NO WIFI YET", "WEAK SIGNAL?" if _net_misses >= 3 else "CONNECTING...", ""]
    return ["WIFI OK", "LOOKING FOR PI", STATE["ip"]]


_demo_step = None


def draw_demo(now):
    """Draw the demo. False if it failed: the caller switches it off rather than let a demo bug
    restart the board over and over."""
    global _demo
    try:
        if _demo is None:
            gc.collect()
            import demo
            _demo = demo
        _draw_demo(now)
        return True
    except Exception as e:  # noqa: BLE001
        stop_demo()
        note_error("demo off: %r (free %d)" % (e, gc.mem_free()))
        return False


def stop_demo():
    """Unload demo.py again, so its memory goes back to fetching flights."""
    global _demo
    _demo = None
    sys.modules.pop("demo", None)
    gc.collect()


def _draw_demo(now):
    global _demo_step
    step = int(now // DEMO_STEP_S)
    if step != _demo_step:
        _demo_step = step
        gc.collect()
    d = dict(_demo.SCREENS[step % len(_demo.SCREENS)])
    if d["callsign"] == "DEMO":
        d["lines"] = d["lines"][:2] + demo_status()
    d["demo"] = True
    d["brightness"] = DEMO_BRIGHTNESS  # asked for with the button, so always on
    draw(d, now)
    tick_flaps(now)
    banner_scroll.tick()


# --- clock while the Pi isn't answering ----------------------------------------------
# The time comes from the Pi ("clock" in display.json: local time, seconds since 1970) or, before
# the Pi has ever answered, from the internet via the Wi-Fi chip (UTC, turned into UK time here).

_clock = None      # local seconds since 1970, minus _secs() when it was set
_clock_from = ""   # "pi" or "ntp"
_ntp_next = 0
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _secs():
    return time.monotonic_ns() // 1000000000  # whole seconds, without float rounding after weeks


def set_clock(local, source):
    global _clock, _clock_from
    _clock, _clock_from = int(local) - _secs(), source


def uk_time(utc):
    """UTC -> UK time: BST from 1am UTC on the last Sunday in March to the last Sunday in October."""
    t = time.localtime(utc)
    m = t.tm_mon
    if m in (3, 10):
        w31 = (t.tm_wday + 31 - t.tm_mday) % 7      # weekday of the 31st (Monday = 0)
        last_sunday = 31 - (w31 + 1) % 7
        after = t.tm_mday > last_sunday or (t.tm_mday == last_sunday and t.tm_hour >= 1)
        bst = after if m == 3 else not after
    else:
        bst = 3 < m < 10
    return utc + (3600 if bst else 0)


def ntp_clock():
    """Ask the Wi-Fi chip for the time, if the Pi hasn't told us (at most once a minute)."""
    global _ntp_next
    if _clock_from == "pi" or time.monotonic() < _ntp_next:
        return
    _ntp_next = time.monotonic() + (60 if _clock is None else 3600)
    try:
        set_clock(uk_time(esp.get_time()[0]), "ntp")
        log("clock from the internet")
    except Exception as e:  # noqa: BLE001 - not synced yet: try again later
        dbg("ntp:", e)


def wifi_up():
    try:
        return esp.is_connected
    except Exception:  # noqa: BLE001
        return False


def draw_offline(now):
    """Clock page with a blinking red Wi-Fi or Pi icon (whichever is wrong); the line underneath says it."""
    global brightness, last_scene
    # The Pi turned the panel off for the night and then went quiet: stay off for up to 8 hours.
    night = brightness <= 0 and STATE["last_ok"] is not None and now - STATE["last_ok"] < 8 * 3600
    brightness = 0 if night else OFFLINE_BRIGHTNESS
    root.hidden = night
    have_wifi = bool(NETWORKS) and wifi_up()
    if not NETWORKS:
        text = "SETUP NEEDED: WIFI IN SETTINGS.TOML"
    elif not have_wifi:
        text = "NO WIFI - " + ("WEAK SIGNAL?" if _net_misses >= 3 else "CONNECTING")
    else:
        text = "NO PI - IS IT ON?" if STATE["last_ok"] is not None else "LOOKING FOR PI"
    if _clock is None:
        hhmm, date = "--:--", ""
    else:
        t = time.localtime(_clock + _secs())
        hhmm = "%02d:%02d" % (t.tm_hour, t.tm_min)
        date = "%s %d %s" % (DAYS[t.tm_wday], t.tm_mday, MONTHS[t.tm_mon - 1])
    set_text(i_time, hhmm, WHITE, animate=False)
    set_text(i_temp, "", animate=False)
    set_text(i_wx, "", animate=False)
    set_text(i_date, date, GREY, animate=False)
    set_text(i_last, text, AMBER if have_wifi else RED, animate=False)
    set_text(i_stats, "UP:INFO DN:DEMO", GREY, animate=False)
    wifi_pal[1] = pi_pal[1] = dim(RED) if int(now * 2) % 2 == 0 else 0  # blinking red
    wifi_tg.hidden = have_wifi  # no Wi-Fi: the Wi-Fi icon. Wi-Fi fine: the Pi icon.
    pi_tg.hidden = not have_wifi
    icons_g.hidden = False
    tick_flaps(now)  # finish any flap left over from the last real screen
    last_scroll.tick()
    show_only(idle_g)
    last_scene = None  # the next real screen is drawn afresh


# --- main loop -----------------------------------------------------------------


# The two buttons on the Portal (next to reset):
#   UP   = status screen on/off (Wi-Fi, signal, Pi address, last answer, last error)
#   DOWN = demo on/off (plays the built-in demo even when the Pi is sending real flights)
def _button(name):
    try:
        b = DigitalInOut(getattr(board, name))
        b.switch_to_input(pull=Pull.UP)
        return b
    except Exception:  # noqa: BLE001 - no such button on this board
        return None


BTN_UP, BTN_DOWN = _button("BUTTON_UP"), _button("BUTTON_DOWN")


def _pressed(btn):
    return btn is not None and not btn.value  # buttons pull to ground when pressed


def run():
    data, last_ok, next_poll, failures, attempts = None, 0, 0, 0, 0
    show_status, force_demo, was_up, was_down = False, False, False, False
    last_mem_log = 0
    draw_offline(time.monotonic())  # the clock page straight away, while Wi-Fi connects
    while True:
        wdt.feed()
        up, down = _pressed(BTN_UP), _pressed(BTN_DOWN)
        if up and not was_up:
            show_status = not show_status
            log("button: status screen", "on" if show_status else "off")
        if down and not was_down:
            force_demo = not force_demo
            log("button: demo", "on" if force_demo else "off")
            if not force_demo:
                stop_demo()
            root.hidden = False
            set_text(m1, "DEMO ON" if force_demo else "DEMO OFF", animate=False)
            set_text(m2, "", animate=False)
            show_only(msg_g)
            time.sleep(1)
        was_up, was_down = up, down
        now = time.monotonic()
        fresh = data is not None and now - last_ok < STALE_SECONDS
        if now >= next_poll and NETWORKS:
            # While the Pi isn't answering (clock page or demo showing), check less often and more
            # briefly so the screen keeps going, and don't cover it with Wi-Fi messages.
            next_poll = now + (POLL_SECONDS if fresh else 15)
            attempts += 1
            new = None
            if connect_wifi(quiet=not fresh):
                new = fetch(listen=fresh or attempts % 2 == 0)
                if new is None:
                    ntp_clock()
            if new is not None:
                data, last_ok, failures = new, time.monotonic(), 0
                if isinstance(new.get("clock"), int):
                    set_clock(new["clock"], "pi")
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
            if STATE.get("esp_bad"):
                # "Expected 01 but got 00": the Wi-Fi chip has stopped talking to the Portal properly
                # and every request will fail until it's restarted. Restart just the chip (2 s);
                # the next loop reconnects to Wi-Fi.
                STATE["esp_bad"] = False
                log("resetting the Wi-Fi chip")
                close_udp()
                try:
                    esp.reset()
                except Exception as e:  # noqa: BLE001
                    restart("wifi chip reset failed: %r" % e)
                STATE["resets"] = STATE.get("resets", 0) + 1
                if STATE["resets"] >= 5 and data is None:
                    restart("wifi chip keeps failing")  # whole board, as a last resort
            gc.collect()
            if STATE.get("oom", 0) >= 6:
                # Memory is in pieces too small for the Pi's reply: only a restart joins it up
                # again (about 10 s, then straight back to the Pi).
                restart("memory too fragmented: %d free" % gc.mem_free())
            if now - last_mem_log >= 60:
                last_mem_log = now
                log("free memory:", gc.mem_free())
            if gc.mem_free() < LOW_MEMORY:
                restart("low memory: %d free" % gc.mem_free())
            now = time.monotonic()
            fresh = data is not None and now - last_ok < STALE_SECONDS
        if DEBUG >= 2 or show_status:
            show_diagnostics(now)
        elif force_demo:
            if not draw_demo(now):
                force_demo = False
        elif fresh:
            draw(data, now)
            tick_flaps(now)
            last_scroll.tick()
            banner_scroll.tick()
        else:
            draw_offline(now)
        time.sleep(0.05)


LOW_MEMORY = 3000  # bytes: below this, start afresh rather than limp on and start failing


def restart(why):
    """A clean restart (a few seconds, then straight back to the Pi): remembered across the restart
    and reported to the Pi, so the settings page shows it happened."""
    log("restarting:", why)
    try:
        b = why.encode()[:120]
        microcontroller.nvm[256:257 + len(b)] = bytes([len(b)]) + b
    except Exception:  # noqa: BLE001
        pass
    microcontroller.reset()


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
    time.sleep(5)
    restart("crash: %r" % e)
