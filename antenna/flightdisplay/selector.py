"""Decide what the panel should show: alerts in priority order, else the nearest aircraft.

Pure logic, no network, so it is easy to test (see tests/test_selector.py).
"""

import math
import re
from dataclasses import dataclass, field

EMERGENCY_SQUAWKS = {
    "7700": "EMERGENCY 7700",
    "7600": "RADIO FAIL 7600",
    "7500": "HIJACK 7500",
}
UK_MIL_LO, UK_MIL_HI = 0x43C000, 0x43CFFF


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


@dataclass
class Aircraft:
    hex: str
    callsign: str = ""
    lat: float = None
    lon: float = None
    alt: int = None          # feet, barometric; None if unknown
    on_ground: bool = False
    gs: float = None         # knots
    track: float = None      # degrees
    squawk: str = None
    type: str = None         # ICAO type from the readsb database, e.g. A320
    db_flags: int = 0
    seen: float = 0.0        # seconds since last message
    seen_pos: float = None   # seconds since last position
    dist_km: float = None
    bearing: float = None

    @property
    def has_position(self):
        return self.lat is not None and self.lon is not None

    @classmethod
    def from_readsb(cls, d):
        alt = d.get("alt_baro")
        on_ground = alt == "ground"
        if on_ground:
            alt = 0
        elif not isinstance(alt, (int, float)):
            alt = d.get("alt_geom") if isinstance(d.get("alt_geom"), (int, float)) else None
        return cls(
            hex=str(d.get("hex", "")).lower().lstrip("~"),
            callsign=(d.get("flight") or "").strip().upper(),
            lat=d.get("lat"),
            lon=d.get("lon"),
            alt=int(alt) if alt is not None else None,
            on_ground=on_ground,
            gs=d.get("gs"),
            track=d.get("track", d.get("true_heading")),
            squawk=d.get("squawk"),
            type=d.get("t"),
            db_flags=int(d.get("dbFlags", 0) or 0),
            seen=float(d.get("seen", 0) or 0),
            seen_pos=d.get("seen_pos"),
        )


@dataclass
class Pick:
    mode: str                      # "alert", "flight" or "idle"
    aircraft: Aircraft = None
    alert: dict = None             # {"level": ..., "text": ...}


@dataclass
class Selector:
    cfg: dict
    _squawk_since: dict = field(default_factory=dict)   # hex -> (squawk, first seen time)
    _current_hex: str = None

    def __post_init__(self):
        wl = self.cfg.get("watchlist", {})
        self._patterns = [re.compile(p) for p in wl.get("callsigns", [])]
        self._names = wl.get("names", {})
        self._hexes = {h.lower() for h in wl.get("hexes", [])}
        self._uk_block = wl.get("uk_military_hex_block", True)

    # --- classification -------------------------------------------------

    def is_military(self, ac):
        if ac.db_flags & 1:
            return True
        if ac.hex in self._hexes:
            return True
        if self._uk_block:
            try:
                if UK_MIL_LO <= int(ac.hex, 16) <= UK_MIL_HI:
                    return True
            except ValueError:
                pass
        return any(p.search(ac.callsign) for p in self._patterns)

    def military_label(self, ac):
        for prefix, name in sorted(self._names.items(), key=lambda kv: -len(kv[0])):
            if ac.callsign.startswith(prefix):
                return name
        return "MILITARY"

    def _emergency(self, ac, now):
        """Return alert text if this aircraft's emergency squawk has persisted long enough."""
        sq = ac.squawk
        if sq not in EMERGENCY_SQUAWKS:
            self._squawk_since.pop(ac.hex, None)
            return None
        prev = self._squawk_since.get(ac.hex)
        if not prev or prev[0] != sq:
            self._squawk_since[ac.hex] = (sq, now)
            prev = (sq, now)
        a = self.cfg["alerts"]
        need = a.get("hijack_persist_seconds", 10) if sq == "7500" else a.get("emergency_persist_seconds", 2)
        return EMERGENCY_SQUAWKS[sq] if now - prev[1] >= need else None

    # --- main entry ------------------------------------------------------

    def choose(self, raw_aircraft, now):
        home = self.cfg["home"]
        rcv = self.cfg["receiver"]
        disp = self.cfg["display"]
        alerts = self.cfg["alerts"]
        max_pos_age = rcv.get("max_position_age", 30)
        max_nopos_age = rcv.get("max_no_position_age", 60)

        planes = []
        for d in raw_aircraft:
            ac = d if isinstance(d, Aircraft) else Aircraft.from_readsb(d)
            if not ac.hex:
                continue
            if ac.has_position and (ac.seen_pos is None or ac.seen_pos <= max_pos_age):
                ac.dist_km = haversine_km(home["lat"], home["lon"], ac.lat, ac.lon)
                ac.bearing = bearing_deg(home["lat"], home["lon"], ac.lat, ac.lon)
            else:
                ac.lat = ac.lon = None
                if ac.seen > max_nopos_age:
                    continue
            planes.append(ac)

        # Forget squawk history for aircraft that have gone.
        live = {p.hex for p in planes}
        for h in list(self._squawk_since):
            if h not in live:
                del self._squawk_since[h]

        def by_dist(p):
            return p.dist_km if p.dist_km is not None else 1e9

        mil_radius = alerts.get("military_radius_km", 40)

        # 1. Emergency squawks (any range we can hear).
        emerg = [(p, t) for p in planes if (t := self._emergency(p, now))]
        if emerg:
            p, text = min(emerg, key=lambda pt: by_dist(pt[0]))
            return Pick("alert", p, {"level": "emergency", "text": text})

        # 2. Military / watchlist, even without a position.
        mil = [p for p in planes if self.is_military(p) and (p.dist_km is None or p.dist_km <= mil_radius)]
        if mil:
            p = min(mil, key=by_dist)
            return Pick("alert", p, {"level": "military", "text": self.military_label(p)})

        # 3. Low and close.
        low = [p for p in planes if p.dist_km is not None and not p.on_ground and p.alt is not None
               and p.alt <= alerts.get("low_alt_ft", 3000) and p.dist_km <= alerts.get("low_dist_km", 2.0)]
        if low:
            p = min(low, key=by_dist)
            self._current_hex = p.hex
            return Pick("alert", p, {"level": "low", "text": "LOOK UP!"})

        # 4. Nearest airborne aircraft in range, with hysteresis so the panel doesn't flicker.
        near = [p for p in planes if p.dist_km is not None and not p.on_ground
                and p.dist_km <= disp.get("radius_km", 15)]
        if not near:
            self._current_hex = None
            return Pick("idle")
        best = min(near, key=by_dist)
        current = next((p for p in near if p.hex == self._current_hex), None)
        if current and current.dist_km - best.dist_km < disp.get("switch_margin_km", 0.5):
            best = current
        self._current_hex = best.hex
        return Pick("flight", best)
