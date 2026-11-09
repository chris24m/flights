"""Flight display service.

Polls readsb's aircraft.json, decides what the LED panel should show and serves it as
/display.json for the Matrix Portal. Run:  python3 -m flightdisplay --config config.toml
"""

import argparse
import json
import logging
import os
import socket
import threading
import time
import tomllib
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .extras import SCENARIOS, Stats, TestMode, Weather, brightness_at, today_str
from .lookups import Lookups
from .refdata import airline_for, friendly_type
from .selector import Aircraft, Selector, haversine_km

log = logging.getLogger("flightdisplay")
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg):
    return COMPASS[int((deg % 360) / 45 + 0.5) % 8] if deg is not None else ""


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
        self.selector = Selector(cfg)
        self.lookups = Lookups(state_dir, lk.get("enabled", True), lk.get("cache_days", 7))
        self.stats = Stats(state_dir)
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
        if not route and ac.dist_km is not None:
            route = f"{ac.dist_km:.0f}KM {compass(ac.bearing)}" if ac.dist_km >= 10 else \
                    f"{ac.dist_km:.1f}KM {compass(ac.bearing)}"
        return {
            "mode": pick.mode,
            "callsign": ac.callsign or ac.hex.upper(),
            "airline": prefix,
            "airline_name": airline_name,
            "col": colour,
            "route": route or "",
            "type": ac_type or "",
            "alt": ac.alt,
            "spd": round(ac.gs) if ac.gs is not None else None,
            "trk": round(ac.track) if ac.track is not None else None,
            "trk_txt": compass(ac.track) if ac.track is not None else "",
            "dist_km": round(ac.dist_km, 1) if ac.dist_km is not None else None,
            "squawk": ac.squawk,
            "alert": pick.alert,
        }, airline_name, ac_type

    def build(self, raw, now):
        pick = self.selector.choose(self.test.inject(raw), now)

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
            "status": "ok" if self.receiver_ok else "receiver offline",
        }
        if pick.mode == "idle":
            out = {
                "mode": "idle",
                "date": dt.strftime("%a %d %b").upper(),
                "weather": self.weather.text,
                "last": self.stats.last_seen_text(now),
                "today": len(self.stats.seen_today),
                "furthest_km": round(self.stats.furthest_km),
            }
        else:
            out, airline_name, ac_type = self.describe(pick.aircraft, pick)
            if not self.test.scenario:
                name = " ".join(x for x in (airline_name or pick.aircraft.callsign,
                                            friendly_type(ac_type)) if x)
                self.stats.shown(name, now)
        out.update(common)
        return out

    # --- loops ---------------------------------------------------------------

    def poll_forever(self):
        rcv = self.cfg["receiver"]
        last_save = 0
        while True:
            now = time.time()
            try:
                raw = read_aircraft(rcv["aircraft_url"])
                self.receiver_ok = True
            except Exception as e:
                if self.receiver_ok:
                    log.warning("receiver read failed: %s", e)
                self.receiver_ok = False
                raw = []
            try:
                out = self.build(raw, now)
                with self._lock:
                    self.display = out
            except Exception:
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            msg = f"FLIGHTDISPLAY http://{local_ip()}:{http_port}/display.json"
            sock.sendto(msg.encode(), ("255.255.255.255", udp_port))
        except OSError:
            pass  # no network yet (e.g. just after a power cut): try again shortly
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
            if url.path == "/display.json":
                self._send(200, service.current())
            elif url.path == "/test":
                q = parse_qs(url.query)
                scenario = q.get("scenario", ["off"])[0]
                seconds = int(q.get("seconds", ["60"])[0])
                ok = service.test.start(scenario, seconds)
                self._send(200 if ok else 400, {"ok": ok, "scenario": service.test.scenario,
                                                "choices": sorted(SCENARIOS) + ["off"]})
            elif url.path == "/healthz":
                self._send(200, {"ok": True, "receiver": service.receiver_ok})
            else:
                self._send(404, {"error": "try /display.json"})

        def log_message(self, fmt, *args):
            pass  # the Portal polls every few seconds; don't fill the journal

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "..", "config.toml"))
    ap.add_argument("--test", choices=sorted(SCENARIOS), help="inject a fake aircraft for 10 minutes")
    ap.add_argument("--once", action="store_true", help="print one display.json and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    service = Service(cfg)
    if args.test:
        service.test.start(args.test, 600)
    if args.once:
        try:
            raw = read_aircraft(cfg["receiver"]["aircraft_url"])
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
