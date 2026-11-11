# Fancy screen transitions (fly-by, radar, slide, dissolve) for the flight display.
# Loaded by flight.py only when FANCY_TRANSITIONS = 1 in settings.toml: they keep a 4 KB copy of
# the screen and a 2 KB radar table in memory all the time, which the Matrix Portal M4 can't
# really spare. Without them the split-flap animation is used.
#
# flight.py imports this part-way through its own start-up, so `F` (flight) already has the
# display, the colours and the helpers used here.

import gc
import random
import time

import bitmaptools
import displayio

import flight as F

# How they work: just before the screen changes, copy what the panel is showing (the old screen)
# into `snap`, a full-screen bitmap drawn on top of everything. Then draw the new screen
# underneath as normal, and eat away the snapshot frame by frame: pixels set to TRANS let
# the new screen show through, pixels set to 0 are black. A plane sprite can fly on top.

TRANS = 0x0020  # "see-through" RGB565 value; dim() never produces it (green this faint is never used)
fb = F.display.framebuffer
snap = displayio.Bitmap(64, 32, 65536)
try:
    snap_cc = displayio.ColorConverter(
        input_colorspace=displayio.Colorspace.RGB565_SWAPPED if F.SNAP_SWAPPED else displayio.Colorspace.RGB565)
    snap_cc.make_transparent(TRANS)
    CAN_SNAP = True
except Exception as e:  # noqa: BLE001 - very old CircuitPython: fall back to the flap
    snap_cc = displayio.ColorConverter()
    CAN_SNAP = False
    F.note_error("no transitions: %r" % e)
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
SPRITE_COLOURS = [None, 0xEBEBEB, 0x646473, 0xBEBEC8, 0x0078C8, 0xA0A0AA, F.WHITE]
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
F.root.append(overlay_g)


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
            F.note_error("framebuffer size %d" % n)
            return False
    except Exception as e:  # noqa: BLE001
        F.note_error("snapshot: %r" % e)
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
        F.wdt.feed()
        F.display.refresh()
        n += 1
    yield 1.0
    F.display.refresh()
    TRANSITION_STATS["fps"] = round(n / seconds)
    F.dbg("transition: %d frames in %.1fs" % (n, seconds))


def run_flyby(sprite, tail, flip):
    """A plane flies across: a '>'-shaped black band clears the old screen ahead of it and the
    new screen is revealed behind its tail."""
    bmp = SPRITES.get(sprite) or SPRITES["twin"]
    w, h = bmp.width, bmp.height
    sprite_pal[SPRITE_KEYS.index("C")] = F.dim(tail)
    for i, c in enumerate(SPRITE_COLOURS):
        if c is not None and SPRITE_KEYS[i] != "C":
            sprite_pal[i] = F.dim(c)
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
    beam = rgb565(F.dim(F.GREEN))
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
    pool = [k for k in ticked if k in F.ALL_TRANSITIONS and (has_plane or k != "flyby") and _available(k)]
    return random.choice(pool) if pool else "flap"


def run_transition(kind, group, plane):
    """Called after the new screen has been drawn underneath the snapshot."""
    try:
        F.display.auto_refresh = False
        if kind == "flyby":
            run_flyby(*plane)
        elif kind == "radar":
            run_radar()
        elif kind == "slide":
            run_slide(group)
        elif kind == "dissolve":
            run_dissolve()
    except Exception as e:  # noqa: BLE001 - a broken animation must never stop the display
        F.note_error("transition %s: %r" % (kind, e))
    finally:
        while len(sprite_g):
            sprite_g.pop()
        snap_tg.x = snap_tg.y = 0
        group.x = group.y = 0
        overlay_g.hidden = True
        F.display.auto_refresh = True
