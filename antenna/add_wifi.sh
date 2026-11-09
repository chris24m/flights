#!/usr/bin/env bash
# Adds every network in wifi.toml (next to this script) to the Pi's saved Wi-Fi. Safe to run again:
# networks already saved are updated. Run: sudo bash add_wifi.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$DIR/wifi.toml" ] || { echo "No wifi.toml (copy wifi.toml.example to wifi.toml and fill it in)"; exit 0; }
command -v nmcli >/dev/null || { echo "nmcli not found (needs Raspberry Pi OS Bookworm or newer)"; exit 1; }
python3 - "$DIR/wifi.toml" <<'PY'
import subprocess, sys, tomllib
nets = tomllib.load(open(sys.argv[1], "rb")).get("network", [])
for i, n in enumerate(nets):
    ssid, pw = n.get("ssid"), n.get("password")
    if not ssid or pw in ("your password", "his password", "hotspot password"):
        print(f"skipping example entry {ssid!r} (fill in the real name and password)")
        continue
    name = f"fd-{ssid}"
    settings = ["connection.autoconnect", "yes", "connection.autoconnect-priority", str(100 - i),
                "802-11-wireless.ssid", ssid]
    settings += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", pw] if pw else []
    exists = subprocess.run(["nmcli", "-t", "-f", "NAME", "connection", "show"],
                            capture_output=True, text=True).stdout.splitlines()
    if name in exists:
        cmd = ["nmcli", "connection", "modify", name] + settings
    else:
        cmd = ["nmcli", "connection", "add", "type", "wifi", "con-name", name, "ifname", "wlan0"] + settings
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(("saved " if r.returncode == 0 else "FAILED ") + repr(ssid) + ("" if r.returncode == 0 else ": " + r.stderr.strip()))
PY
echo "Saved Wi-Fi networks:"
nmcli -t -f NAME,TYPE connection show | grep 802-11-wireless | cut -d: -f1 | sed 's/^/  /'
