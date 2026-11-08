"""Route (by callsign) and aircraft type (by hex) lookups from adsbdb.com, cached on disk.

Lookups run on a background thread so a slow API never holds up the display: the first
poll for a new callsign returns None, and the route appears a second or two later.
"""

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)
API = "https://api.adsbdb.com/v0"
USER_AGENT = "flight-display/1.0 (home ADS-B display)"
UNKNOWN_RETRY_SECONDS = 24 * 3600


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"response": "unknown"}
        raise


def parse_route(data):
    resp = data.get("response") if isinstance(data, dict) else None
    fr = resp.get("flightroute") if isinstance(resp, dict) else None
    if not fr:
        return None
    o = (fr.get("origin") or {}).get("iata_code") or (fr.get("origin") or {}).get("icao_code")
    d = (fr.get("destination") or {}).get("iata_code") or (fr.get("destination") or {}).get("icao_code")
    return f"{o}-{d}" if o and d else None


def parse_type(data):
    resp = data.get("response") if isinstance(data, dict) else None
    ac = resp.get("aircraft") if isinstance(resp, dict) else None
    return ac.get("icao_type") if ac else None


class Lookups:
    def __init__(self, state_dir, enabled=True, cache_days=7, fetch=_get_json):
        self.enabled = enabled
        self.ttl = cache_days * 86400
        self.path = os.path.join(state_dir, "lookup-cache.json") if state_dir else None
        self._fetch = fetch
        self._lock = threading.Lock()
        self._cache = self._load()
        self._pending = set()
        self._failed = {}  # key -> time of last failure, so a dead network isn't hammered
        self._q = queue.Queue()
        self._dirty = False
        if enabled:
            threading.Thread(target=self._worker, daemon=True, name="lookups").start()

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return {}

    def save(self):
        if not self.path or not self._dirty:
            return
        with self._lock:
            data = dict(self._cache)
            self._dirty = False
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def _cached(self, key):
        with self._lock:
            hit = self._cache.get(key)
        if hit is None:
            return False, None
        value, ts = hit
        age = time.time() - ts
        if (value is None and age > UNKNOWN_RETRY_SECONDS) or age > self.ttl:
            return False, value
        return True, value

    def _get(self, key):
        fresh, value = self._cached(key)
        if (not fresh and self.enabled and key not in self._pending
                and time.time() - self._failed.get(key, 0) > 120):
            self._pending.add(key)
            self._q.put(key)
        return value

    def route(self, callsign):
        return self._get("cs:" + callsign) if callsign else None

    def aircraft_type(self, hex_code):
        return self._get("hex:" + hex_code) if hex_code else None

    def _worker(self):
        while True:
            key = self._q.get()
            kind, ident = key.split(":", 1)
            try:
                if kind == "cs":
                    value = parse_route(self._fetch(f"{API}/callsign/{ident}"))
                else:
                    value = parse_type(self._fetch(f"{API}/aircraft/{ident}"))
                with self._lock:
                    self._cache[key] = [value, time.time()]
                    self._dirty = True
            except Exception as e:  # network down etc: try again on a later poll
                log.warning("lookup %s failed: %s", key, e)
                self._failed[key] = time.time()
            finally:
                self._pending.discard(key)
            time.sleep(0.5)  # be polite to a free API
