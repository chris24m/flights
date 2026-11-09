# Flight display for the Adafruit Matrix Portal (M4) + 64x32 RGB panel. CircuitPython 9.x.
#
# Deliberately dumb: the antenna Pi decides everything and serves /display.json;
# this file only draws it. Fix behaviour on the Pi (reachable over Tailscale), not here.

import gc
import os
import random
import time

import board
import busio
import displayio
import microcontroller
import terminalio
from digitalio import DigitalInOut
from watchdog import WatchDogMode

import adafruit_connection_manager
import adafruit_imageload
import adafruit_requests
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.bitmap_label import Label  # faster text updates than label.Label
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_matrixportal.matrix import Matrix

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
VERSION = "2026.11.09"

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
try:
    SMALL = bitmap_font.load_font("/fonts/tom-thumb.bdf")  # 3x5 pixel font, 4x6 cell
except OSError:
    SMALL = terminalio.FONT  # works, but too big: copy the font over (see README)
BIG = terminalio.FONT

root = displayio.Group()
display.root_group = root


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
                try:
                    bmp, pal = adafruit_imageload.load("/logos/%s.bmp" % icao,
                                                       bitmap=displayio.Bitmap, palette=displayio.Palette)
                except (OSError, ValueError):
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


def draw(d, now):
    global brightness, animate_now, last_scene, FLAP
    alert = d.get("alert") or {}
    scene = (d.get("mode"), d.get("callsign"), alert.get("text"))
    animate_now = scene != last_scene  # new aircraft or screen: flap everything once
    last_scene = scene
    brightness = float(d.get("brightness", 0.5))
    if "flap" in d:  # the Pi's settings page can switch the animation on/off
        FLAP = 1 if d["flap"] else 0
    root.hidden = brightness <= 0
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
        set_text(a_cs, ("%s %s" % (d.get("callsign") or "", d.get("type") or "")).strip())
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


# --- Wi-Fi ---------------------------------------------------------------------

esp = adafruit_esp32spi.ESP_SPIcontrol(
    busio.SPI(board.SCK, board.MOSI, board.MISO),
    DigitalInOut(board.ESP_CS), DigitalInOut(board.ESP_BUSY), DigitalInOut(board.ESP_RESET))
pool = adafruit_connection_manager.get_radio_socketpool(esp)
requests = adafruit_requests.Session(pool, adafruit_connection_manager.get_radio_ssl_context(esp))


# Hardware watchdog: if the code ever hangs (the ESP32 Wi-Fi chip occasionally wedges),
# the board resets itself after 16 s without a feed. Fed in the main loop and around slow calls.
wdt = microcontroller.watchdog
wdt.timeout = 16
wdt.mode = WatchDogMode.RESET


def connect_wifi():
    """Try each network in settings.toml (yours and Dad's) until one works."""
    while not esp.is_connected:
        for ssid, password in NETWORKS:
            wdt.feed()
            message("WIFI...", ssid[:16])
            try:
                esp.connect_AP(ssid, password)
                STATE["ssid"] = ssid
                STATE["ip"] = esp.pretty_ip(esp.ip_address)
                update_rssi()
                log("wifi connected:", ssid, "ip", STATE["ip"], "rssi", STATE["rssi"])
                message("WIFI OK", STATE["ip"])
                for _ in range(3 if DEBUG else 1):
                    wdt.feed()
                    time.sleep(1)
                return
            except (RuntimeError, ConnectionError, OSError) as e:
                note_error("wifi %s: %s" % (ssid, e))
        message("NO WIFI", "RETRYING")
        for _ in range(10):
            wdt.feed()
            time.sleep(1)


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
    if STATE["err"]:
        q += "&err=" + quote(STATE["err"])
    return q


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


def discover(seconds=8):
    """Listen for the Pi's UDP broadcast ("FLIGHTDISPLAY <url>", sent every few seconds)."""
    log("listening for the Pi's broadcast on UDP", DISCOVERY_PORT, "for", seconds, "s")
    try:
        sock = esp.get_socket()
        esp.start_server(DISCOVERY_PORT, sock, conn_mode=esp.UDP_MODE)
    except Exception as e:  # noqa: BLE001 - older Wi-Fi firmware: fall back to fixed addresses
        note_error("discovery unavailable: %s" % e)
        return None
    packets = 0
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            wdt.feed()
            n = esp.socket_available(sock)
            if n:
                packets += 1
                raw = bytes(esp.socket_read(sock, n)).decode("utf-8", "ignore")
                dbg("udp packet:", repr(raw))
                msg = raw.split()
                if len(msg) >= 2 and msg[0] == "FLIGHTDISPLAY" and msg[1].startswith("http://"):
                    log("found Pi at", msg[1])
                    STATE["found"] += 1
                    return msg[1]
            time.sleep(0.2)
        note_error("no broadcast heard (%d other packets)" % packets)
    except Exception as e:  # noqa: BLE001
        note_error("discovery failed: %s" % e)
    finally:
        try:
            esp.socket_close(sock)
        except Exception:  # noqa: BLE001
            pass
    return None


for u in URLS:
    URL_SOURCE[u] = "settings"
saved = load_saved_url()
if saved and saved not in URLS:
    URLS.insert(0, saved)
    URL_SOURCE[saved] = "saved"
if HOSTNAME_URL not in URLS:
    URLS.append(HOSTNAME_URL)
    URL_SOURCE[HOSTNAME_URL] = "hostname"


def fetch():
    """Return the display dict from the first Pi URL that answers, or None."""
    data = try_urls()
    if data is None:
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
            resp = requests.get(url + "?" + report_query(), timeout=4)
            status = resp.status_code
            data = resp.json()
            resp.close()
            if status != 200:
                raise ValueError("HTTP %d" % status)
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
log("flight display", VERSION, "starting; reset reason:", RESET_REASON, "debug:", DEBUG)
try:
    n = microcontroller.nvm[256]
    if 0 < n <= 120:
        note_error(bytes(microcontroller.nvm[257:257 + n]).decode("utf-8", "ignore"))
        microcontroller.nvm[256] = 0
except Exception:  # noqa: BLE001
    pass


# --- main loop -----------------------------------------------------------------

if not NETWORKS:
    message("SETUP NEEDED", "SETTINGS.TOML")
    while True:
        wdt.feed()
        time.sleep(5)


def run():
    global brightness
    connect_wifi()
    data, last_ok, next_poll, failures = None, 0, 0, 0
    while True:
        wdt.feed()
        now = time.monotonic()
        if now >= next_poll:
            next_poll = now + POLL_SECONDS
            if not esp.is_connected:
                connect_wifi()
            new = fetch()
            if new is not None:
                data, last_ok, failures = new, now, 0
            else:
                failures += 1
                if failures >= 5:  # reset the Wi-Fi link; the next loop reconnects
                    try:
                        esp.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                    failures = 0
            gc.collect()
        if DEBUG >= 2:
            show_diagnostics(now)
        elif data is not None and now - last_ok < STALE_SECONDS:
            draw(data, now)
            tick_flaps(now)
            last_scroll.tick()
            banner_scroll.tick()
        else:
            # Pi gone quiet: say so, dimly. If the panel was off (overnight), stay off.
            brightness = min(brightness, 0.15) if brightness > 0 else 0
            root.hidden = brightness <= 0
            message("NO DATA", "IS THE PI ON?")
        time.sleep(0.05)


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
