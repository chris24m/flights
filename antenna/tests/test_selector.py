"""Run from antenna/:  python3 -m unittest discover -s tests"""

import os
import sys
import tomllib
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flightdisplay.__main__ import Service  # noqa: E402
from flightdisplay.extras import SCENARIOS, brightness_at  # noqa: E402
from flightdisplay.lookups import parse_route  # noqa: E402
from flightdisplay.selector import Selector  # noqa: E402

with open(os.path.join(os.path.dirname(__file__), "..", "config.toml"), "rb") as f:
    CFG = tomllib.load(f)
HOME = CFG["home"]


def plane(hex_code, callsign, dlat, dlon, alt, squawk="2000", db_flags=0, pos=True, seen=0.5):
    d = {"hex": hex_code, "flight": callsign + " ", "alt_baro": alt, "gs": 200, "track": 90,
         "squawk": squawk, "dbFlags": db_flags, "seen": seen}
    if pos:
        d.update(lat=HOME["lat"] + dlat, lon=HOME["lon"] + dlon, seen_pos=0.5)
    return d


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.s = Selector(CFG)

    def test_idle_when_empty(self):
        self.assertEqual(self.s.choose([], 0).mode, "idle")

    def test_nearest_in_radius(self):
        p = self.s.choose([plane("400001", "BAW1", 0.08, 0, 9000), plane("400002", "EZY2", 0.04, 0, 8000)], 0)
        self.assertEqual((p.mode, p.aircraft.callsign), ("flight", "EZY2"))

    def test_out_of_radius_is_idle(self):
        self.assertEqual(self.s.choose([plane("400001", "BAW1", 0.5, 0, 9000)], 0).mode, "idle")

    def test_ground_ignored(self):
        self.assertEqual(self.s.choose([plane("400001", "BAW1", 0.01, 0, "ground")], 0).mode, "idle")

    def test_hysteresis(self):
        a, b = plane("400001", "BAW1", 0.040, 0, 9000), plane("400002", "EZY2", 0.045, 0, 9000)
        self.assertEqual(self.s.choose([a, b], 0).aircraft.hex, "400001")
        b2 = plane("400002", "EZY2", 0.038, 0, 9000)  # only ~0.2 km closer: keep BAW1
        self.assertEqual(self.s.choose([a, b2], 1).aircraft.hex, "400001")
        b3 = plane("400002", "EZY2", 0.02, 0, 9000)   # clearly closer: switch
        self.assertEqual(self.s.choose([a, b3], 2).aircraft.hex, "400002")

    def test_low_and_close(self):
        p = self.s.choose([plane("400001", "RYR1", 0.01, 0, 1800), plane("400002", "BAW2", 0.005, 0, 9000)], 0)
        self.assertEqual(p.alert["level"], "low")

    def test_military_beats_low_and_needs_no_position(self):
        p = self.s.choose([plane("400001", "RYR1", 0.01, 0, 1800),
                           plane("43c9a1", "RED1", 0, 0, 1500, pos=False)], 0)
        self.assertEqual((p.alert["level"], p.alert["text"]), ("military", "RED ARROWS"))

    def test_db_flag_military(self):
        p = self.s.choose([plane("3c0001", "GAF123", 0.1, 0, 20000, db_flags=1)], 0)
        self.assertEqual(p.alert["level"], "military")

    def test_emergency_beats_military(self):
        planes = [plane("43c9a1", "RED1", 0, 0, 1500), plane("400001", "BAW1", 0.2, 0, 9000, squawk="7700")]
        self.s.choose(planes, 0)
        p = self.s.choose(planes, 3)
        self.assertEqual((p.alert["level"], p.alert["text"]), ("emergency", "EMERGENCY 7700"))

    def test_7500_must_persist(self):
        planes = [plane("400001", "BAW1", 0.03, 0, 9000, squawk="7500")]
        self.assertNotEqual(self.s.choose(planes, 0).mode, "alert")
        self.assertNotEqual(self.s.choose(planes, 5).mode, "alert")
        self.assertEqual(self.s.choose(planes, 11).alert["text"], "HIJACK 7500")

    def test_7500_reset_when_squawk_changes(self):
        hij, ok = plane("400001", "BAW1", 0.03, 0, 9000, squawk="7500"), plane("400001", "BAW1", 0.03, 0, 9000)
        self.s.choose([hij], 0)
        self.s.choose([ok], 5)
        self.s.choose([hij], 6)
        self.assertNotEqual(self.s.choose([hij], 12).mode, "alert")

    def test_stale_no_position_dropped(self):
        self.assertEqual(self.s.choose([plane("43c9a1", "RED1", 0, 0, 1500, pos=False, seen=120)], 0).mode, "idle")


class OtherTests(unittest.TestCase):
    def test_brightness(self):
        sched = CFG["brightness"]["schedule"]
        self.assertEqual(brightness_at(sched, datetime(2026, 12, 25, 12, 0)), 0.6)
        self.assertEqual(brightness_at(sched, datetime(2026, 12, 25, 23, 45)), 0.0)
        self.assertEqual(brightness_at(sched, datetime(2026, 12, 25, 3, 0)), 0.0)

    def test_parse_route(self):
        data = {"response": {"flightroute": {"origin": {"iata_code": "AGP"}, "destination": {"iata_code": "BOH"}}}}
        self.assertEqual(parse_route(data), "AGP-BOH")
        self.assertIsNone(parse_route({"response": "unknown callsign"}))

    def test_service_builds_every_scenario(self):
        cfg = dict(CFG, state={}, lookups={"enabled": False}, weather={"enabled": False})
        svc = Service(cfg)
        self.assertEqual(svc.build([], 1e9)["mode"], "idle")
        for name in SCENARIOS:
            svc.test.start(name, 60)
            import time
            out = svc.build([], time.time() + 20)  # +20 s so 7500 has "persisted"
            svc.selector._squawk_since.clear()
            self.assertIn(out["mode"], ("alert", "flight", "card"), name)
            self.assertIn("brightness", out)


class FactsAndSettingsTests(unittest.TestCase):
    def setUp(self):
        import copy
        import tempfile
        self.tmp = tempfile.mkdtemp()
        cfg = copy.deepcopy(CFG)
        cfg.update(state={"dir": self.tmp}, lookups={"enabled": False}, weather={"enabled": False})
        self.svc = Service(cfg)

    def test_all_fact_lines_fit_panel(self):
        from flightdisplay.facts import FACTS, RARE, card
        for t in FACTS:
            for line in card(t):
                self.assertLessEqual(len(line), 16, (t, line))
        self.assertFalse(RARE - FACTS.keys())

    def test_rare_banner(self):
        p = Selector(CFG).choose([dict(plane("400a5f", "GSPIT", 0.05, 0, 2000), t="SPIT")], 0)
        self.assertEqual((p.alert["level"], p.alert["text"]), ("rare", "RARE! SPITFIRE"))

    def test_facts_page_alternates(self):
        raw = [dict(plane("400001", "EZY45T", 0.03, 0, 4000), t="A320")]
        self.assertEqual(self.svc.build(raw, 992.0)["mode"], "flight")   # int(992/8) even
        out = self.svc.build(raw, 1000.0)                                 # odd: facts card
        self.assertEqual((out["mode"], out["lines"][0]), ("card", "AIRBUS A320"))

    def test_settings_update_and_reset(self):
        st = self.svc.settings
        form = {"features.facts_page": ["0"], "features.facts_every_s": ["8"], "features.rare_banner": ["0", "1"],
                "features.potd": ["0", "1"], "features.potd_every_min": ["5"], "features.potd_show_s": ["20"],
                "features.transition": ["radar"], "display.radius_km": ["5"], "display.units": ["km"],
                "alerts.military_radius_km": ["25"],
                "brightness.schedule.t0": ["08:00"], "brightness.schedule.v0": ["50"],
                "brightness.schedule.t1": ["23:00"], "brightness.schedule.v1": ["0"]}
        self.assertEqual(st.update(form), [])
        self.assertFalse(self.svc.cfg["features"]["facts_page"])
        self.assertEqual(self.svc.build([], 5.0)["transition"], "radar")
        self.assertAlmostEqual(self.svc.cfg["display"]["radius_km"], 8.05, places=1)
        self.assertEqual(self.svc.cfg["brightness"]["schedule"], [["08:00", 0.5], ["23:00", 0.0]])
        raw = [dict(plane("400001", "EZY45T", 0.03, 0, 4000), t="A320")]
        self.assertEqual(self.svc.build(raw, 1000.0)["mode"], "flight")   # facts page now off
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "settings.json")))
        st.reset()
        self.assertTrue(self.svc.cfg["features"]["facts_page"])
        self.assertEqual(self.svc.cfg["display"]["radius_km"], CFG["display"]["radius_km"])

    def test_flyby_sprite_and_direction_data(self):
        out = self.svc.build([dict(plane("400001", "BAW1", 0.03, 0, 4000), t="B744", track=260)], 992.0)
        self.assertEqual((out["sprite"], out["trk"]), ("quad", 260))
        out = self.svc.build([dict(plane("400002", "EZY1", 0.03, 0, 4000), t="A320")], 992.0)
        self.assertEqual(out["sprite"], "twin")
        self.assertIn(out["transition"], ("random", "flyby", "flap", "radar", "slide", "dissolve", "none"))

    def test_settings_reject_bad_values(self):
        errs = self.svc.settings.update({"features.facts_every_s": ["999"], "display.units": ["furlongs"]})
        self.assertTrue(errs)

    def test_potd_shows_only_in_its_window(self):
        f = self.svc.cfg["features"]
        period = f["potd_every_min"] * 60
        self.assertEqual(self.svc.build([], period * 50 + 1)["mode"], "card")
        self.assertEqual(self.svc.build([], period * 50 + f["potd_show_s"] + 1)["mode"], "idle")


if __name__ == "__main__":
    unittest.main()
