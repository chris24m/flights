"""Flight display service.

Polls readsb's aircraft.json, decides what the LED panel should show and serves it as
/display.json for the Matrix Portal. Run:  python3 -m flightdisplay --config config.toml
"""

import argparse
import collections
import json
import logging
import os
import random
import socket
import threading
import time
import tomllib
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .facts import FACTS, card, panel_type, sprite_for
from .settings import Settings
from .simulate import FakeSky
from .extras import DEMO, DEMO_STEP_S, SCENARIOS, Stats, TestMode, Weather, brightness_at, today_str
from .lookups import Lookups
from .refdata import airline_for, friendly_type
from .selector import Aircraft, Selector, haversine_km

log = logging.getLogger("flightdisplay")

# --- diagnostics: everything /debug shows -----------------------------------------
DIAG = {
    "started": time.time(),
    "receiver": {"ok": False, "url": None, "last_ok": None, "last_error": None, "aircraft": 0, "polls": 0},
    "build": {"errors": 0, "last_error": None},
    "announce": {"enabled": False, "port": None, "url": None, "sent": 0, "last_sent": None,
                 "last_error": None},
    "clients": {},   # ip -> {"requests", "last_seen", "path", "report"} (the Portal reports its own health)
}
RECENT_LOG = collections.deque(maxlen=60)


class _RecentHandler(logging.Handler):
    def emit(self, record):
        RECENT_LOG.append(f"{time.strftime('%H:%M:%S')} {record.levelname} {record.getMessage()}")


def ago(ts):
    return None if ts is None else round(time.time() - ts, 1)
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg):
    return COMPASS[int((deg % 360) / 45 + 0.5) % 8] if deg is not None else ""


def fmt_dist(km, units):
    """Distance as the panel shows it: 1.1MI / 14MI, or 1.8KM / 23KM with units = "km"."""
    if km is None:
        return ""
    v, suffix = (km / 1.609344, "MI") if units == "mi" else (km, "KM")
    return f"{v:.1f}{suffix}" if v < 10 else f"{v:.0f}{suffix}"


def load_config(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def read_aircraft(url):
    if url.startswith(("http://", "https://")):
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.load(r)
    else:
        with open(url) as f:
            data = json.load(f)
    return data.get("aircraft", [])


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        state_dir = cfg.get("state", {}).get("dir")
        if state_dir:
            try:
                os.makedirs(state_dir, exist_ok=True)
            except OSError:
                log.warning("can't create %s, running without saved state", state_dir)
                state_dir = None
        lk = cfg.get("lookups", {})
        wx = cfg.get("weather", {})
        self.settings = Settings(cfg, state_dir)  # applies settings-page overrides onto cfg
        self.selector = Selector(cfg)
        self.lookups = Lookups(state_dir, lk.get("enabled", True), lk.get("cache_days", 7))
        self.simulated = cfg["receiver"].get("aircraft_url") == "simulate"
        self.fake_sky = FakeSky(cfg["home"], cfg["receiver"].get("simulate_every_s", 60)) if self.simulated \
            else None
        self.stats = Stats(None if self.simulated else state_dir)  # don't mix fake planes into Dad's stats
        self.weather = Weather(cfg["home"]["lat"], cfg["home"]["lon"], wx.get("enabled", True),
                               wx.get("refresh_minutes", 15))
        self.test = TestMode(cfg["home"])
        self.display = {"mode": "idle", "status": "starting"}
        self.receiver_ok = False
        self._lock = threading.Lock()

    # --- building the JSON ---------------------------------------------------

    def describe(self, ac, pick):
        prefix, airline_name, colour = airline_for(ac.callsign)
        ac_type = ac.type or self.lookups.aircraft_type(ac.hex)
        route = self.lookups.route(ac.callsign) if prefix else None
        units = self.cfg["display"].get("units", "mi")
        if not route and ac.dist_km is not None:
            route = f"{fmt_dist(ac.dist_km, units)} {compass(ac.bearing)}"
        return {
            "mode": pick.mode,
            "callsign": ac.callsign or ac.hex.upper(),
            "airline": prefix,
            "airline_name": airline_name,
            "col": colour,
            "route": route or "",
            "type": panel_type(ac_type),     # 737-800
            "type_code": ac_type or "",      # B738
            "alt": ac.alt,
            "spd": round(ac.gs) if ac.gs is not None else None,
            "trk": round(ac.track) if ac.track is not None else None,
            "trk_txt": compass(ac.track) if ac.track is not None else "",
            "dist_km": round(ac.dist_km, 1) if ac.dist_km is not None else None,
            "dist_txt": fmt_dist(ac.dist_km, units),
            "squawk": ac.squawk,
            "alert": pick.alert,
            "sprite": sprite_for(ac_type),
        }, airline_name, ac_type

    def build(self, raw, now):
        demo = self.test.demo_step(now)
        injected = self.test.inject(raw, now)
        if demo:  # show emergency squawks straight away instead of after the usual 2 s
            for d in injected:
                if d.get("squawk") in ("7700", "7600", "7500"):
                    self.selector._squawk_since.setdefault(d["hex"], (d["squawk"], now - 60))
        pick = self.selector.choose(injected, now)

        # Daily stats count real aircraft only, never test-mode fakes.
        home, max_age = self.cfg["home"], self.cfg["receiver"].get("max_position_age", 30)
        planes = [Aircraft.from_readsb(d) for d in raw]
        for p in planes:
            if p.has_position and (p.seen_pos is None or p.seen_pos <= max_age):
                p.dist_km = haversine_km(home["lat"], home["lon"], p.lat, p.lon)
        self.stats.update([p for p in planes if p.seen < 60], today_str(now))

        dt = datetime.fromtimestamp(now)
        common = {
            "time": dt.strftime("%H:%M"),
            "brightness": brightness_at(self.cfg.get("brightness", {}).get("schedule"), dt),
            "test": self.test.scenario,
            "status": ("simulated" if self.simulated else "ok") if self.receiver_ok else "receiver offline",
        }
        feat = self.cfg.get("features", {})
        common["transitions"] = list(feat.get("transitions", []))
        common["flap"] = bool(common["transitions"])  # for older Portal code
        if demo:
            common["transitions"] = [demo[3]]  # show each animation in turn
            common["demo"] = "%d/%d %s" % (demo[4], len(DEMO), demo[0])
        if pick.mode == "idle":
            potd = self.potd_card(now, feat, force=demo[2] == "potd" if demo else None)
            if potd:
                potd.update(common)
                return potd
            out = {
                "mode": "idle",
                "date": dt.strftime("%a %d %b").upper(),
                "weather": self.weather.text,
                "last": self.stats.last_seen_text(now),
                "today": len(self.stats.seen_today),
                "furthest_km": round(self.stats.furthest_km),
                "furthest_txt": fmt_dist(self.stats.furthest_km, self.cfg["display"].get("units", "mi"))
                                .replace(".0", ""),
            }
        else:
            out, airline_name, ac_type = self.describe(pick.aircraft, pick)
            if not self.test.scenario:
                name = " ".join(x for x in (airline_name or pick.aircraft.callsign,
                                            friendly_type(ac_type)) if x)
                self.stats.shown(name, now)
            # Facts page: alternate with the details for normal flights, rare and military planes
            # (never for emergencies or "look up", which need the details on screen).
            level = (pick.alert or {}).get("level")
            lines = card(ac_type)
            every = max(3, int(feat.get("facts_every_s", 8)))
            show_card = int(now / every) % 2 == 1
            if demo:
                show_card = demo[2] == "card"
            if (feat.get("facts_page", True) or demo) and lines and level in (None, "rare", "military") \
                    and show_card:
                out = {"mode": "card", "style": "rare" if level == "rare" else "facts", "lines": lines,
                       "col": out.get("col"), "callsign": out.get("callsign"), "airline": out.get("airline"),
                       "alert": pick.alert}
        out.update(common)
        return out

    def potd_card(self, now, feat, force=None):
        """When the sky is quiet: every N minutes, a facts card for a random type for a few seconds.
        force: True/False overrides the schedule (demo mode)."""
        if force is not None:
            if not force:
                return None
            slot, show = int(now // 30), True
        elif self.test.scenario == "potd":
            slot, show = int(now // 30), True
        elif feat.get("potd", True):
            period = max(1, int(feat.get("potd_every_min", 10))) * 60
            slot, show = int(now // period), (now % period) < int(feat.get("potd_show_s", 20))
        else:
            return None
        if not show:
            return None
        ac_type = random.Random(slot).choice(sorted(FACTS))
        return {"mode": "card", "style": "potd", "lines": card(ac_type), "callsign": "POTD-%d" % slot,
                "col": None, "airline": None, "alert": None}

    # --- loops ---------------------------------------------------------------

    def poll_forever(self):
        rcv = self.cfg["receiver"]
        last_save = 0
        while True:
            now = time.time()
            r = DIAG["receiver"]
            r["polls"] += 1
            r["url"] = rcv["aircraft_url"]
            try:
                raw = self.fake_sky.aircraft(now) if self.fake_sky else read_aircraft(rcv["aircraft_url"])
                if not self.receiver_ok:
                    log.info("receiver OK: %d aircraft", len(raw))
                self.receiver_ok = True
                r.update(ok=True, last_ok=now, aircraft=len(raw))
            except Exception as e:
                if self.receiver_ok or r["polls"] == 1:
                    log.warning("receiver read failed (%s): %s", rcv["aircraft_url"], e)
                self.receiver_ok = False
                r.update(ok=False, last_error=f"{time.strftime('%H:%M:%S')} {e}")
                raw = []
            try:
                out = self.build(raw, now)
                with self._lock:
                    self.display = out
                shown = (out.get("mode"), out.get("callsign"), (out.get("alert") or {}).get("text"))
                if shown != getattr(self, "_last_shown", None):
                    log.debug("display now: %s %s %s", *(x or "" for x in shown))
                    self._last_shown = shown
            except Exception as e:
                DIAG["build"]["errors"] += 1
                DIAG["build"]["last_error"] = f"{time.strftime('%H:%M:%S')} {e!r}"
                log.exception("build failed")
            if now - last_save > 60:
                try:
                    self.stats.save()
                    self.lookups.save()
                except OSError as e:
                    log.warning("saving state failed: %s", e)
                last_save = now
            time.sleep(max(0.2, rcv.get("poll_seconds", 1.0) - (time.time() - now)))

    def current(self):
        with self._lock:
            return dict(self.display)


def local_ip():
    """This Pi's address on the home network (no traffic is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


def announce_forever(http_port, udp_port, every=3.0):
    """Broadcast "FLIGHTDISPLAY http://<ip>:<port>/display.json" so the Portal can find this Pi
    on any network (yours or Dad's) without a fixed IP or router settings."""
    a = DIAG["announce"]
    a.update(enabled=True, port=udp_port, every_s=every)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            url = f"http://{local_ip()}:{http_port}/display.json"
            msg = f"FLIGHTDISPLAY {url}"
            sock.sendto(msg.encode(), ("255.255.255.255", udp_port))
            if url != a["url"]:
                log.info("announcing %s on UDP broadcast port %d", url, udp_port)
            a.update(url=url, sent=a["sent"] + 1, last_sent=time.time())
            log.debug("broadcast: %s", msg)
        except OSError as e:
            # No network yet (e.g. just after a power cut): try again shortly.
            if a["last_error"] is None or not a["last_error"].endswith(str(e)):
                log.warning("broadcast failed: %s", e)
            a["last_error"] = f"{time.strftime('%H:%M:%S')} {e}"
        time.sleep(every)


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            ip = self.client_address[0]
            c = DIAG["clients"].setdefault(ip, {"requests": 0})
            c.update(requests=c["requests"] + 1, last_seen=time.time(), path=url.path)
            log.debug("%s GET %s", ip, self.path)
            if url.path == "/display.json":
                q = parse_qs(url.query)
                if q:  # the Portal adds its own status (wifi, signal, errors) to each poll
                    report = {k: v[0] for k, v in q.items()}
                    if "err" in report and report["err"] != c.get("report", {}).get("err"):
                        log.warning("portal %s reports: %s", ip, report["err"])
                    c["report"] = report
                self._send(200, service.current())
            elif url.path == "/debug":
                self._send_debug()
            elif url.path in ("/settings", "/"):
                self._send_html(service.settings.page())
            elif url.path == "/test":
                q = parse_qs(url.query)
                scenario = q.get("scenario", ["off"])[0]
                seconds = int(q.get("seconds", ["60"])[0])
                custom = {k: q[k][0] for k in ("callsign", "type", "alt", "dist_mi", "squawk", "military")
                          if k in q} if scenario == "custom" else None
                ok = service.test.start(scenario, seconds, custom)
                if scenario == "demo":
                    seconds = len(DEMO) * DEMO_STEP_S
                if q.get("back"):  # from a button on the settings page
                    self._send_html(service.settings.page(
                        message=(f"Test '{scenario}' running for {seconds} s" if ok
                                 else f"Test '{scenario}' not started: check the fields")
                        if scenario != "off" else "Test stopped"))
                    return
                self._send(200 if ok else 400, {"ok": ok, "scenario": service.test.scenario,
                                                "choices": sorted(SCENARIOS) + ["custom", "demo", "sky", "off"]})
            elif url.path == "/healthz":
                self._send(200, {"ok": True, "receiver": service.receiver_ok})
            else:
                self._send(404, {"error": "try /display.json, /debug, /test or /healthz"})

        def _send_html(self, page, code=200):
            body = page.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path != "/settings":
                self._send(404, {"error": "not found"})
                return
            length = min(int(self.headers.get("Content-Length") or 0), 20000)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
            st = service.settings
            if st.pin and form.get("pin", [""])[0] != st.pin:
                self._send_html(st.page(errors=["Wrong PIN, nothing changed."]), 403)
                return
            if form.get("reset"):
                st.reset()
                self._send_html(st.page(message="Back to the defaults from config.toml."))
                return
            errors = st.update(form)
            self._send_html(st.page(message="" if errors else "Saved. The panel updates within a few seconds.",
                                    errors=errors))

        def _send_debug(self):
            d = json.loads(json.dumps(DIAG))
            d["uptime_s"] = round(time.time() - DIAG["started"])
            d["receiver"]["last_ok_ago_s"] = ago(DIAG["receiver"]["last_ok"])
            d["announce"]["last_sent_ago_s"] = ago(DIAG["announce"]["last_sent"])
            for ip, c in d["clients"].items():
                c["last_seen_ago_s"] = ago(DIAG["clients"][ip]["last_seen"])
            lk = service.lookups
            d["lookups"] = {"enabled": lk.enabled, "cached": len(lk._cache), "pending": len(lk._pending),
                            "recent_failures": len(lk._failed)}
            d["weather"] = service.weather.text
            d["test_mode"] = service.test.scenario
            d["display_now"] = service.current()
            d["recent_log"] = list(RECENT_LOG)
            body = json.dumps(d, indent=2, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # the Portal polls every few seconds; don't fill the journal

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "..", "config.toml"))
    ap.add_argument("--test", choices=sorted(SCENARIOS), help="inject a fake aircraft for 10 minutes")
    ap.add_argument("--once", action="store_true", help="print one display.json and exit")
    ap.add_argument("--simulate", action="store_true",
                    help="no antenna needed: made-up aircraft fly over home (see simulate.py)")
    ap.add_argument("--every", type=float, default=60, help="with --simulate: seconds between new aircraft")
    ap.add_argument("--debug", action="store_true",
                    help="log every broadcast, request and display change (also: [debug] verbose in config)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.simulate:
        cfg["receiver"]["aircraft_url"] = "simulate"
        cfg["receiver"]["simulate_every_s"] = args.every
    verbose = args.debug or cfg.get("debug", {}).get("verbose", False)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addHandler(_RecentHandler(level=logging.INFO))

    service = Service(cfg)
    if args.test:
        service.test.start(args.test, 600)
    if args.once:
        try:
            raw = (service.fake_sky.aircraft(time.time()) if service.fake_sky
                   else read_aircraft(cfg["receiver"]["aircraft_url"]))
            service.receiver_ok = True
        except Exception as e:
            log.warning("receiver read failed: %s", e)
            raw = []
        print(json.dumps(service.build(raw, time.time()), indent=2))
        return

    threading.Thread(target=service.poll_forever, daemon=True, name="poll").start()
    port = cfg["display"].get("port", 8080)
    disc = cfg.get("discovery", {})
    if disc.get("enabled", True):
        threading.Thread(target=announce_forever, args=(port, disc.get("port", 50222)),
                         daemon=True, name="announce").start()
    log.info("serving http://0.0.0.0:%d/display.json", port)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(service)).serve_forever()


if __name__ == "__main__":
    main()
