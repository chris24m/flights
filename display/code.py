# Flight display for the Adafruit Matrix Portal (M4) + 64x32 RGB panel. CircuitPython 9.x.
#
# Deliberately dumb: the antenna Pi decides everything and serves /display.json;
# this file only draws it. Fix behaviour on the Pi (reachable over Tailscale), not here.

import gc
import os
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
from adafruit_display_text.label import Label
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
STALE_SECONDS = 30  # show "no data" if the Pi hasn't answered for this long

# --- colours (dimmed by the Pi's brightness value) ----------------------------

WHITE, GREY, AMBER, CYAN, GREEN = 0xFFFFFF, 0x808080, 0xFFA000, 0x00C0FF, 0x40FF40
RED, BLUE, YELLOW, BLACK, NAVY = 0xFF0000, 0x2040FF, 0xFFD000, 0x000000, 0x001060
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


def set_text(lab, text, colour=None):
    text = text or ""
    if colour is not None:
        lab.base_colour = colour
    if lab.text != text:
        lab.text = text
    lab.color = dim(lab.base_colour)


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

# Message screen: Wi-Fi / Pi problems.
msg_g = displayio.Group()
m1 = label(msg_g, 0, 8, colour=AMBER)
m2 = label(msg_g, 0, 16, colour=GREY)
root.append(msg_g)


def show_only(group):
    for g in (flight_g, alert_g, idle_g, msg_g):
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
    right = ("%.1fKM" % dist) if dist is not None and dist < 10 else (("%dKM" % dist) if dist else "")
    return (left + " " + right).strip()


ALERT_STYLE = {  # level: (banner bg, text, stripe colour, flash period in s or 0)
    "emergency": (RED, WHITE, RED, 0.5),
    "military": (NAVY, WHITE, RED, 0),
    "low": (YELLOW, BLACK, YELLOW, 1.0),
}


def draw(d, now):
    global brightness
    brightness = float(d.get("brightness", 0.5))
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
        set_text(i_time, d.get("time"))
        wx = (d.get("weather") or "").split(" ", 1)
        set_text(i_temp, wx[0])
        set_text(i_wx, wx[1] if len(wx) > 1 else "")
        set_text(i_date, d.get("date"))
        set_text(i_last, d.get("last") or "WAITING FOR AIRCRAFT")
        set_text(i_stats, "TODAY %s  MAX %sKM" % (d.get("today", 0), d.get("furthest_km", 0)))
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
                return
            except (RuntimeError, ConnectionError, OSError) as e:
                print("wifi", ssid, "failed:", e)
        message("NO WIFI", "RETRYING")
        for _ in range(10):
            wdt.feed()
            time.sleep(1)


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
    try:
        sock = esp.get_socket()
        esp.start_server(DISCOVERY_PORT, sock, conn_mode=esp.UDP_MODE)
    except Exception as e:  # noqa: BLE001 - older Wi-Fi firmware: fall back to fixed addresses
        print("discovery unavailable:", e)
        return None
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            wdt.feed()
            n = esp.socket_available(sock)
            if n:
                msg = bytes(esp.socket_read(sock, n)).decode("utf-8", "ignore").split()
                if len(msg) >= 2 and msg[0] == "FLIGHTDISPLAY" and msg[1].startswith("http://"):
                    print("found Pi at", msg[1])
                    return msg[1]
            time.sleep(0.2)
    except Exception as e:  # noqa: BLE001
        print("discovery failed:", e)
    finally:
        try:
            esp.socket_close(sock)
        except Exception:  # noqa: BLE001
            pass
    return None


saved = load_saved_url()
if saved and saved not in URLS:
    URLS.insert(0, saved)
if HOSTNAME_URL not in URLS:
    URLS.append(HOSTNAME_URL)


def fetch():
    """Return the display dict from the first Pi URL that answers, or None."""
    data = try_urls()
    if data is None:
        url = discover()
        if url:
            if url in URLS:
                URLS.remove(url)
            URLS.insert(0, url)
            data = try_urls()
    return data


def try_urls():
    global URLS
    for i, url in enumerate(URLS):
        wdt.feed()
        try:
            resp = requests.get(url, timeout=4)
            data = resp.json()
            resp.close()
            if i:  # try the one that worked first next time
                URLS = [url] + URLS[:i] + URLS[i + 1:]
            save_url(url)
            return data
        except Exception as e:  # noqa: BLE001 - any failure just means "try the next one"
            print("fetch", url, "failed:", e)
    return None


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
        if data is not None and now - last_ok < STALE_SECONDS:
            draw(data, now)
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
except Exception as e:  # noqa: BLE001 - anything unexpected: log it and start again
    print("crashed:", e)
    time.sleep(5)
    microcontroller.reset()
