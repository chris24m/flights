"""Settings page: change display options from a phone without SSH.

Changes are saved to <state dir>/settings.json as overrides on top of config.toml and applied to
the running service immediately (the selector and builder read the config dict on every poll).
"Reset" deletes the overrides and goes back to config.toml.
"""

import copy
import html
import json
import logging
import os

log = logging.getLogger(__name__)
KM_PER_MILE = 1.609344

# (section, key, label, kind, options). kind: bool / int / float / choice / schedule.
# "display.radius_km" is shown in miles; the brightness schedule is edited as four time/level rows.
FIELDS = [
    ("features", "facts_page", "Facts page: alternate with the flight details", "bool", None),
    ("features", "facts_every_s", "Swap flight / facts every (seconds)", "int", (3, 60)),
    ("features", "rare_banner", "Rare-plane banner (Spitfire, Lancaster, 747...)", "bool", None),
    ("features", "potd", "Plane of the day when the sky is quiet", "bool", None),
    ("features", "potd_every_min", "Plane of the day every (minutes)", "int", (1, 240)),
    ("features", "potd_show_s", "Plane of the day stays up for (seconds)", "int", (5, 120)),
    ("features", "transition", "Screen change animation", "choice",
     ("random", "flyby", "flap", "page", "slide", "dissolve", "none")),
    ("display", "radius_km", "Overhead radius (miles)", "miles", (0.5, 50)),
    ("display", "units", "Show distances in", "choice", ("mi", "km")),
    ("alerts", "military_radius_km", "Military / rare alert radius (miles)", "miles", (1, 100)),
    ("brightness", "schedule", "Brightness schedule", "schedule", None),
]

FEATURE_DEFAULTS = {"facts_page": True, "facts_every_s": 8, "rare_banner": True, "potd": True,
                    "potd_every_min": 10, "potd_show_s": 20, "transition": "random"}


class Settings:
    def __init__(self, cfg, state_dir):
        cfg.setdefault("features", {})
        if cfg["features"].get("flap") is False and "transition" not in cfg["features"]:
            cfg["features"]["transition"] = "none"  # older config.toml with flap = false
        for k, v in FEATURE_DEFAULTS.items():
            cfg["features"].setdefault(k, v)
        cfg["display"].setdefault("units", "mi")
        self.cfg = cfg
        self.defaults = copy.deepcopy({sec: dict(cfg.get(sec, {})) for sec, *_ in FIELDS})
        self.path = os.path.join(state_dir, "settings.json") if state_dir else None
        self.pin = str(cfg.get("settings", {}).get("pin", "") or "")
        self.overrides = self._load()
        self._apply()

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.overrides, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def _apply(self):
        for sec, values in self.defaults.items():
            self.cfg.setdefault(sec, {}).update(copy.deepcopy(values))
        for dotted, value in self.overrides.items():
            sec, key = dotted.split(".", 1)
            self.cfg.setdefault(sec, {})[key] = value

    def get(self, sec, key):
        return self.cfg.get(sec, {}).get(key)

    # --- form handling -------------------------------------------------------

    def update(self, form):
        """form: dict of field -> list of values (from parse_qs). Returns a list of error strings."""
        errors, new = [], {}
        for sec, key, label, kind, opt in FIELDS:
            name = f"{sec}.{key}"
            raw = form.get(name, [""])[0].strip()
            try:
                if kind == "bool":
                    value = "1" in form.get(name, [])  # hidden "0" + checkbox "1" when ticked
                elif kind in ("int", "float", "miles"):
                    num = float(raw)
                    lo, hi = opt
                    if not lo <= num <= hi:
                        raise ValueError(f"must be {lo}-{hi}")
                    value = int(num) if kind == "int" else (round(num * KM_PER_MILE, 2) if kind == "miles"
                                                            else num)
                elif kind == "choice":
                    if raw not in opt:
                        raise ValueError("not an option")
                    value = raw
                elif kind == "schedule":
                    rows = []
                    for i in range(6):
                        t = form.get(f"{name}.t{i}", [""])[0].strip()
                        v = form.get(f"{name}.v{i}", [""])[0].strip()
                        if not t:
                            continue
                        hh, mm = t.split(":")
                        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                            raise ValueError(f"bad time {t}")
                        level = int(v) / 100
                        if not 0 <= level <= 1:
                            raise ValueError("levels are 0-100%")
                        rows.append([f"{int(hh):02d}:{int(mm):02d}", level])
                    if not rows:
                        raise ValueError("needs at least one row")
                    value = sorted(rows)
                else:
                    continue
            except (ValueError, TypeError) as e:
                errors.append(f"{label}: {e}")
                continue
            default = self.defaults.get(sec, {}).get(key)
            if kind == "miles" and default is not None and abs(value - default) < 0.2:
                continue  # just mile/km rounding of the default, not a real change
            if value != default:
                new[name] = value
        if not errors:
            self.overrides = new
            self._apply()
            self._save()
            log.info("settings changed: %s", new or "back to config.toml defaults")
        return errors

    def reset(self):
        self.overrides = {}
        self._apply()
        if self.path and os.path.exists(self.path):
            os.remove(self.path)
        log.info("settings reset to config.toml defaults")

    # --- page ------------------------------------------------------------------

    def page(self, message="", errors=()):
        rows = []
        for sec, key, label, kind, opt in FIELDS:
            name = f"{sec}.{key}"
            value = self.get(sec, key)
            changed = name in self.overrides
            tag = ' <span class="chg">changed</span>' if changed else ""
            if kind == "bool":
                inp = (f'<input type="hidden" name="{name}" value="0">'
                       f'<input type="checkbox" name="{name}" value="1"{" checked" if value else ""}>')
                rows.append(f'<label class="row check">{inp}<span>{html.escape(label)}{tag}</span></label>')
            elif kind in ("int", "float", "miles"):
                shown = round(value / KM_PER_MILE, 1) if kind == "miles" else value
                step = "1" if kind == "int" else "0.5"
                rows.append(f'<label class="row"><span>{html.escape(label)}{tag}</span>'
                            f'<input type="number" name="{name}" value="{shown}" min="{opt[0]}" max="{opt[1]}" '
                            f'step="{step}"></label>')
            elif kind == "choice":
                opts = "".join(f'<option{" selected" if o == value else ""}>{o}</option>' for o in opt)
                rows.append(f'<label class="row"><span>{html.escape(label)}{tag}</span>'
                            f'<select name="{name}">{opts}</select></label>')
            elif kind == "schedule":
                sched = list(value or [])[:6]
                cells = []
                for i in range(6):
                    t, v = (sched[i] if i < len(sched) else ("", ""))
                    pct = "" if v == "" else round(float(v) * 100)
                    cells.append(f'<div class="sched"><input type="time" name="{name}.t{i}" value="{t}">'
                                 f'<input type="number" name="{name}.v{i}" value="{pct}" min="0" max="100" '
                                 f'placeholder="%"><span>%</span></div>')
                rows.append(f'<div class="row block"><span>{html.escape(label)}{tag}<br><small>From each time '
                            f'until the next. 0% = off. Leave a time blank to remove a row.</small></span>'
                            f'{"".join(cells)}</div>')
        pin = ('<label class="row"><span>PIN</span><input type="password" name="pin" inputmode="numeric">'
               '</label>') if self.pin else ""
        msg = f'<p class="ok">{html.escape(message)}</p>' if message else ""
        err = "".join(f'<p class="err">{html.escape(e)}</p>' for e in errors)
        tests = " ".join(f'<a class="btn" href="/test?scenario={s}&seconds=30&back=1">{s}</a>'
                         for s in ("flight", "rare", "potd", "military", "redarrows", "emergency", "low"))
        return PAGE.format(msg=msg, err=err, rows="\n".join(rows), pin=pin, tests=tests)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight display settings</title>
<style>
:root {{ --bg:#101216; --card:#1a1d23; --fg:#e8e8e8; --dim:#9aa0a6; --acc:#ffa000; --ok:#4caf50; --err:#ef5350; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.4 system-ui, sans-serif; }}
main {{ max-width:560px; margin:0 auto; padding:16px; }}
h1 {{ font-size:22px; margin:8px 0 16px; }} h2 {{ font-size:15px; color:var(--dim); margin:24px 0 8px; }}
.row {{ display:flex; align-items:center; justify-content:space-between; gap:12px; background:var(--card);
  padding:12px 14px; border-radius:10px; margin:8px 0; }}
.row.check {{ justify-content:flex-start; }} .row.block {{ display:block; }}
input[type=number], select, input[type=time], input[type=password] {{ background:#0c0d10; color:var(--fg);
  border:1px solid #333; border-radius:6px; padding:6px 8px; font-size:16px; width:96px; }}
input[type=checkbox] {{ width:22px; height:22px; accent-color:var(--acc); }}
.sched {{ display:flex; gap:8px; align-items:center; margin-top:8px; }} .sched input[type=time] {{ width:130px; }} .sched input[type=number] {{ width:70px; }}
small {{ color:var(--dim); }} .lbl {{ width:110px; }}
input.wide, input[name=callsign] {{ background:#0c0d10; color:var(--fg); border:1px solid #333; border-radius:6px;
  padding:6px 8px; font-size:16px; width:120px; }} .chg {{ color:var(--acc); font-size:12px; margin-left:6px; }}
button, .btn {{ background:var(--acc); color:#000; border:0; border-radius:8px; padding:10px 16px; font-size:16px;
  text-decoration:none; display:inline-block; margin:4px 4px 4px 0; cursor:pointer; }}
button.secondary, .btn {{ background:#2a2e36; color:var(--fg); }}
.ok {{ color:var(--ok); }} .err {{ color:var(--err); }} a {{ color:var(--acc); }}
</style></head><body><main>
<h1>Flight display settings</h1>
{msg}{err}
<form method="post" action="/settings">
{rows}
{pin}
<button type="submit">Save</button>
<button class="secondary" type="submit" name="reset" value="1">Reset to defaults</button>
</form>
<h2>Test the panel (30 seconds each)</h2>
<p>{tests} <a class="btn" href="/test?scenario=off&back=1">stop</a></p>
<h2>Test any plane (60 seconds)</h2>
<form method="get" action="/test" class="row block">
<input type="hidden" name="scenario" value="custom"><input type="hidden" name="back" value="1">
<input type="hidden" name="seconds" value="60">
<div class="sched"><span class="lbl">Callsign</span><input name="callsign" value="EZY8AB" class="wide"></div>
<div class="sched"><span class="lbl">Type</span><input name="type" value="A388" class="wide"></div>
<div class="sched"><span class="lbl">Altitude ft</span><input type="number" name="alt" value="2500"></div>
<div class="sched"><span class="lbl">Distance mi</span><input type="number" name="dist_mi" value="1" step="0.1"></div>
<div class="sched"><span class="lbl">Squawk</span><input name="squawk" value="2000" class="wide"></div>
<label class="sched"><span class="lbl">Military</span><input type="checkbox" name="military" value="1"></label>
<p><small>Types use ICAO codes: A320, A20N, A388, B738, B744, SPIT, LANC, HAWK, H47, EUFI...
Squawk 7700 = emergency. Altitude under 3000 ft within about 1 mile = "look up".</small></p>
<button type="submit">Show on panel</button>
</form>
<h2>More</h2>
<p><a href="/tar1090/" id="map">Live map</a> &middot; <a href="/debug">Debug info</a> &middot;
<a href="/display.json">display.json</a></p>
<script>document.getElementById('map').href = 'http://' + location.hostname + '/tar1090/';</script>
</main></body></html>"""
