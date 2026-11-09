"""Fake sky: made-up aircraft flying over home, in the same format readsb writes to aircraft.json.

Lets the whole system (Pi service + Matrix Portal) run with no antenna, no readsb, even on a laptop:
    python3 -m flightdisplay --simulate              # a new aircraft every 60 s
    python3 -m flightdisplay --simulate --every 20   # busier sky
or from the settings page: the "Fake sky (1 hour)" button, or the "Fake planes" tick box (stays on).

Each aircraft flies a straight line that passes within a few km of home, so it comes into range,
becomes the nearest, and leaves again. Mostly airliners; now and then something special (military, a warbird, the Red Arrows, an emergency).
"""

import math
import random

KT_TO_KMS = 1.852 / 3600

# (weight, callsign prefix or full callsign, types, altitude range ft, speed range kt, extras)
POOL = [
    (10, "EZY", ("A320", "A20N", "A319", "A21N"), (3000, 36000), (180, 450), {}),
    (8, "RYR", ("B738", "B38M"), (3000, 37000), (180, 450), {}),
    (6, "BAW", ("A320", "A319", "B772", "A35K"), (4000, 38000), (200, 470), {}),
    (4, "TOM", ("B738", "B788", "B38M"), (3000, 37000), (180, 460), {}),
    (3, "WZZ", ("A21N", "A20N"), (5000, 37000), (220, 450), {}),
    (2, "VIR", ("B789", "A35K", "A339"), (10000, 39000), (300, 480), {}),
    (2, "DLH", ("A20N", "A321"), (10000, 37000), (300, 460), {}),
    (2, "LOG", ("AT76", "E195"), (3000, 20000), (180, 280), {}),
    (2, "EXS", ("B738",), (4000, 36000), (200, 450), {}),
    (2, "low", ("A320", "B738", "E190"), (1200, 2600), (130, 160), {}),        # Bournemouth approach
    (2, "GB", ("C172", "PA28", "SR22", "DA40"), (1500, 4500), (90, 140), {}),  # light aircraft, no airline
    (1, "HLE", ("EC35",), (600, 1500), (100, 130), {}),                       # air ambulance
    (0.4, "RRR", ("A400", "C17", "A332M"), (8000, 30000), (250, 420), {"mil": True}),
    (0.4, "ASCOT", ("C130", "A400"), (5000, 25000), (220, 330), {"mil": True}),
    (0.6, "rare", ("SPIT", "HURI", "LANC", "DC3", "B744", "A388"), (1500, 9000), (150, 300), {}),
    (0.2, "RED", ("HAWK",), (1000, 6000), (250, 350), {"mil": True}),
]
EMERGENCY_CHANCE = 0.01  # occasionally squawk 7700 (only while within ~10 km, so it doesn't hog the screen)


class FakeSky:
    def __init__(self, home, every_s=60, seed=None):
        self.lat0, self.lon0 = home["lat"], home["lon"]
        self.every = max(5, every_s)
        self.rnd = random.Random(seed)
        self.planes = []
        self.next_spawn = None
        self.count = 0

    def _spawn(self, t):
        weights = [p[0] for p in POOL]
        _, prefix, types, alts, spds, extra = self.rnd.choices(POOL, weights)[0]
        self.count += 1
        n = self.rnd.randint(1, 999)
        if prefix == "low":
            prefix = self.rnd.choice(("EZY", "RYR", "TOM"))
        if prefix == "rare":
            callsign = self.rnd.choice(("GSPIT", "BBMF1", "GBZTA"))
        elif prefix == "GB":
            callsign = "G" + "".join(self.rnd.choice("ABCDEFGHIJKLMNOPRSTUVWXYZ") for _ in range(4))
        elif prefix == "RED":
            callsign = "RED%d" % self.rnd.randint(1, 9)
        else:
            callsign = "%s%d%s" % (prefix, n, self.rnd.choice(("", "", "A", "K", "T")))
        mil = extra.get("mil", False)
        hex_code = "43c%03x" % self.rnd.randrange(0x1000) if mil else "%06x" % self.rnd.randrange(0x400000, 0x43c000)
        track = self.rnd.uniform(0, 360)
        offset = self.rnd.uniform(-2.5, 2.5) if prefix != "HLE" else self.rnd.uniform(-1, 1)
        if alts[1] <= 2600:  # approach traffic passes close
            offset = self.rnd.uniform(-1.2, 1.2)
        start_km = 30.0
        # start 30 km "behind" home along the track, shifted sideways by the offset
        tr = math.radians(track)
        x = -math.sin(tr) * start_km + math.cos(tr) * offset
        y = -math.cos(tr) * start_km - math.sin(tr) * offset
        emergency = self.rnd.random() < EMERGENCY_CHANCE
        squawk = "%04d" % self.rnd.choice((2000, 7000, 4501, 3601))
        self.planes.append({
            "hex": hex_code, "flight": callsign.ljust(8), "t": self.rnd.choice(types),
            "alt": self.rnd.randrange(alts[0], alts[1], 100), "gs": self.rnd.randrange(*spds),
            "track": round(track), "x0": x, "y0": y, "t0": t, "squawk": squawk, "dbFlags": 1 if mil else 0,
            "emergency": emergency,
        })

    def aircraft(self, now):
        """The sky at time `now`, as readsb aircraft.json entries."""
        if self.next_spawn is None:
            self.next_spawn = now
            for back in (2.5, 1.5, 0.5):  # start with a few already on their way
                self._spawn(now - back * self.every)
        while now >= self.next_spawn:
            self._spawn(now)
            # mostly steady traffic, with the odd quiet spell so the clock gets a turn too
            gap = self.rnd.uniform(0.6, 1.4) if self.rnd.random() > 0.15 else self.rnd.uniform(4, 7)
            self.next_spawn += self.every * gap
        out, keep = [], []
        for p in self.planes:
            dist = (now - p["t0"]) * p["gs"] * KT_TO_KMS
            if dist > 62:  # 30 km in, 30 km out: gone
                continue
            keep.append(p)
            tr = math.radians(p["track"])
            x, y = p["x0"] + math.sin(tr) * dist, p["y0"] + math.cos(tr) * dist
            lat = self.lat0 + y / 111.32
            lon = self.lon0 + x / (111.32 * math.cos(math.radians(self.lat0)))
            out.append({"hex": p["hex"], "flight": p["flight"], "t": p["t"], "alt_baro": p["alt"],
                        "gs": p["gs"], "track": p["track"],
                        "squawk": "7700" if p["emergency"] and 20 < dist < 40 else p["squawk"], "dbFlags": p["dbFlags"],
                        "lat": round(lat, 5), "lon": round(lon, 5), "seen": 0.2, "seen_pos": 0.3})
        self.planes = keep
        return out
