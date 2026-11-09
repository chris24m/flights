"""Pretend to be the Matrix Portal: listen for the Pi's broadcast and fetch what it serves.

Run on your laptop (same Wi-Fi as the Pi) or on the Pi itself:

    python3 tools/listen.py            # print each broadcast as it arrives
    python3 tools/listen.py --fetch    # ...and fetch display.json from the announced URL

No output for 10 s means the broadcast isn't reaching this machine: check the service is running
(systemctl status flight-display), that both are on the same network, and the /debug page's
"announce" section. Some mesh/guest Wi-Fi networks block broadcasts between devices.
"""

import argparse
import json
import socket
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=50222)
    ap.add_argument("--fetch", action="store_true", help="also fetch display.json from the announced URL")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("", args.port))
    s.settimeout(10)
    print(f"listening for FLIGHTDISPLAY broadcasts on UDP {args.port} (Ctrl-C to stop)")
    while True:
        try:
            data, (ip, _) = s.recvfrom(512)
        except socket.timeout:
            print(f"{time.strftime('%H:%M:%S')}  nothing for 10 s")
            continue
        msg = data.decode(errors="replace")
        print(f"{time.strftime('%H:%M:%S')}  from {ip}: {msg}")
        parts = msg.split()
        if args.fetch and len(parts) >= 2 and parts[0] == "FLIGHTDISPLAY":
            t = time.time()
            try:
                with urllib.request.urlopen(parts[1] + "?portal=listen.py", timeout=5) as r:
                    d = json.load(r)
                print(f"           fetched in {1000 * (time.time() - t):.0f} ms: mode={d.get('mode')} "
                      f"{d.get('callsign') or ''} status={d.get('status')}")
            except Exception as e:
                print(f"           FETCH FAILED: {e}")


if __name__ == "__main__":
    main()
