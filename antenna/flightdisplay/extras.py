"""Idle-screen extras: daily stats, last seen, weather, brightness schedule, test scenarios."""

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime

log = logging.getLogger(__name__)


# --- daily stats ----------------------------------------------------------

class Stats:
    """Aircraft seen today, furthest today, and the last aircraft shown. Survives restarts."""

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "stats.json") if state_dir else None
        self.day = None
        self.seen_today = set()
        self.furthest_km = 0.0
        self.furthest_callsign = None
        self.last_seen = None  # {"name": "Ryanair 737", "ts": epoch}
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
        except (OSError, ValueError, TypeError):
            return
        self.day = d.get("day")
        self.seen_today = set(d.get("seen_today", []))
        self.furthest_km = d.get("furthest_km", 0.0)
        self.furthest_callsign = d.get("furthest_callsign")
        self.last_seen = d.get("last_seen")

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"day": self.day, "seen_today": sorted(self.seen_today),
                       "furthest_km": self.furthest_km, "furthest_callsign": self.furthest_callsign,
                       "last_seen": self.last_seen}, f)
            f.flush()
            os.fsync(f.fileno())  # so a power cut can't leave a half-written file
        os.replace(tmp, self.path)

    def update(self, planes, today):
        if today != self.day:
            self.day, self.seen_today, self.furthest_km, self.furthest_callsign = today, set(), 0.0, None
        for p in planes:
            self.seen_today.add(p.hex)
            if p.dist_km is not None and p.dist_km > self.furthest_km:
                self.furthest_km, self.furthest_callsign = p.dist_km, p.callsign or p.hex

    def shown(self, name, now):
        self.last_seen = {"name": name, "ts": now}

    def last_seen_text(self, now):
        if not self.last_seen:
            return None
        mins = int((now - self.last_seen["ts"]) // 60)
        if mins < 60:
            ago = f"{mins}M"
        elif mins < 48 * 60:
            ago = f"{mins // 60}H"
        else:
            ago = f"{mins // 1440}D"
        return f"LAST: {self.last_seen['name']} {ago} AGO".upper()


# --- weather (Open-Meteo, free, no key) -------------------------------------

WMO = {0: "CLEAR", 1: "FAIR", 2: "CLOUDY", 3: "OVERCAST", 45: "FOG", 48: "FOG",
       51: "DRIZZLE", 53: "DRIZZLE", 55: "DRIZZLE", 61: "RAIN", 63: "RAIN", 65: "HEAVY RAIN",
       66: "SLEET", 67: "SLEET", 71: "SNOW", 73: "SNOW", 75: "SNOW", 77: "SNOW",
       80: "SHOWERS", 81: "SHOWERS", 82: "SHOWERS", 85: "SNOW", 86: "SNOW",
       95: "THUNDER", 96: "THUNDER", 99: "THUNDER"}


class Weather:
    def __init__(self, lat, lon, enabled=True, refresh_minutes=15):
        self.text = None
        self.lat, self.lon = lat, lon
        self.refresh = refresh_minutes * 60
        if enabled:
            threading.Thread(target=self._loop, daemon=True, name="weather").start()

    def _loop(self):
        while True:
            try:
                url = (f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}"
                       "&current=temperature_2m,weather_code")
                with urllib.request.urlopen(url, timeout=10) as r:
                    cur = json.load(r)["current"]
                self.text = f"{round(cur['temperature_2m'])}C {WMO.get(cur['weather_code'], '')}".strip()
                wait = self.refresh
            except Exception as e:
                # Usually the network isn't up yet after a power cut: try again soon.
                log.warning("weather failed: %s", e)
                wait = 60
            time.sleep(wait)


# --- brightness -------------------------------------------------------------

def brightness_at(schedule, now_dt):
    """schedule: [["07:00", 0.6], ["22:00", 0.1], ...]. Last entry before now wins (wraps midnight)."""
    if not schedule:
        return 0.5
    entries = sorted((int(t[:2]) * 60 + int(t[3:5]), float(v)) for t, v in schedule)
    minute = now_dt.hour * 60 + now_dt.minute
    value = entries[-1][1]  # before the first entry of the day, yesterday's last one applies
    for start, v in entries:
        if minute >= start:
            value = v
    return max(0.0, min(1.0, value))


# --- test mode: fake aircraft injected into the live feed -------------------

def _fake(home, hex_code, callsign, dlat, dlon, alt, gs, track, squawk="2000", t=None, db_flags=0, pos=True):
    d = {"hex": hex_code, "flight": callsign, "alt_baro": alt, "gs": gs, "track": track,
         "squawk": squawk, "seen": 0.1, "dbFlags": db_flags}
    if t:
        d["t"] = t
    if pos:
        d.update(lat=home["lat"] + dlat, lon=home["lon"] + dlon, seen_pos=0.1)
    return d


SCENARIOS = {
    "emergency": lambda h: [_fake(h, "406abc", "BAW123", 0.05, 0.02, 8000, 280, 250, squawk="7700", t="A320")],
    "radiofail": lambda h: [_fake(h, "406abd", "EZY45T", 0.04, -0.03, 6000, 250, 90, squawk="7600", t="A20N")],
    "hijack": lambda h: [_fake(h, "406abe", "RYR8TX", 0.06, 0.01, 12000, 300, 180, squawk="7500", t="B738")],
    "military": lambda h: [_fake(h, "43c123", "ASCOT45", 0.1, 0.05, 15000, 320, 270, t="A400", db_flags=1)],
    "redarrows": lambda h: [_fake(h, "43c9a1", "RED1", 0, 0, 1500, 300, 90, t="HAWK", db_flags=1, pos=False)],
    "low": lambda h: [_fake(h, "4ca9b2", "RYR52KM", 0.008, -0.005, 1800, 145, 80, t="B738")],
    "rare": lambda h: [_fake(h, "400a5f", "GSPIT", 0.03, 0.01, 1800, 220, 120, t="SPIT")],
    "potd": lambda h: [],  # no aircraft: the Pi shows a plane-of-the-day card for the test period
    "flight": lambda h: [_fake(h, "40776a", "EZY45T", 0.03, 0.02, 4200, 180, 80, t="A320")],
    "rare747": lambda h: [_fake(h, "4007f1", "BAW2490", 0.02, -0.03, 3800, 190, 260, t="B744")],
    "heli": lambda h: [_fake(h, "407c35", "HLE87", 0.01, 0.01, 900, 110, 300, t="EC35")],
}

# Demo: every screen and every transition, DEMO_STEP_S seconds each.
# (what's on screen, fake aircraft scenario or None, "flight" / "card" / "idle" / "potd", transition into it)
DEMO_STEP_S = 8
DEMO = [
    ("Clock", None, "idle", "flap"),
    ("Flight (fly-by, eastbound)", "flight", "flight", "flyby"),
    ("Facts card", "flight", "card", "radar"),
    ("Clock (plane flies off)", None, "idle", "flyby"),
    ("Rare 747 (westbound fly-by)", "rare747", "flight", "flyby"),
    ("Rare facts card", "rare747", "card", "dissolve"),
    ("Military A400M", "military", "flight", "slide"),
    ("Red Arrows", "redarrows", "flight", "flap"),
    ("Look up! (low and close)", "low", "flight", "flyby"),
    ("Emergency 7700", "emergency", "flight", "radar"),
    ("Helicopter", "heli", "flight", "flyby"),
    ("Spitfire", "rare", "flight", "flyby"),
    ("Plane of the day", None, "potd", "dissolve"),
    ("Clock", None, "idle", "slide"),
]


class TestMode:
    def __init__(self, home):
        self.home = home
        self.scenario = None
        self.until = 0
        self.custom = None

    def start(self, scenario, seconds=60, custom=None, now=None):
        """custom: {"callsign", "type", "alt", "dist_mi", "squawk", "military"} for scenario "custom"."""
        now = time.time() if now is None else now
        if scenario in (None, "", "off"):
            self.scenario = None
            return True
        if scenario == "demo":
            self.scenario, self.started, self.until = "demo", now, now + len(DEMO) * DEMO_STEP_S
            return True
        if scenario == "custom":
            if not custom or not custom.get("callsign"):
                return False
            self.custom = custom
        elif scenario not in SCENARIOS:
            return False
        self.scenario, self.until = scenario, now + seconds
        return True

    def demo_step(self, now):
        """The current demo step as (label, scenario, show, transition, number), or None."""
        if self.scenario != "demo" or now >= self.until:
            return None
        i = min(int((now - self.started) // DEMO_STEP_S), len(DEMO) - 1)
        return DEMO[i] + (i + 1,)

    def _custom_aircraft(self):
        c = self.custom
        dist_km = float(c.get("dist_mi") or 1) * 1.609344
        pos = c.get("dist_mi") not in ("", "none", None) or not c.get("military")
        return [_fake(self.home, "7e57ed", c["callsign"].upper()[:8], dist_km / 111.0, 0,
                      int(c.get("alt") or 3000), int(c.get("spd") or 200), int(c.get("trk") or 90),
                      squawk=c.get("squawk") or "2000", t=(c.get("type") or "").upper() or None,
                      db_flags=1 if c.get("military") else 0, pos=pos)]

    def inject(self, aircraft, now=None):
        now = time.time() if now is None else now
        step = self.demo_step(now)
        if step:  # demo: only the demo's own aircraft, so a real plane overhead can't interrupt it
            return SCENARIOS[step[1]](self.home) if step[1] else []
        if self.scenario and self.scenario != "demo" and now < self.until:
            extra = self._custom_aircraft() if self.scenario == "custom" else SCENARIOS[self.scenario](self.home)
            return aircraft + extra
        self.scenario = None
        return aircraft


def today_str(now=None):
    return datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
