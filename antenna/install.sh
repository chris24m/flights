#!/usr/bin/env bash
# Installs the flight display service as a systemd unit. Run from this folder: sudo ./install.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"

python3 -c 'import tomllib' 2>/dev/null || { echo "Needs Python 3.11+ (Raspberry Pi OS Bookworm or newer)"; exit 1; }

mkdir -p /var/lib/flight-display
chown "$USER_NAME" /var/lib/flight-display

sed -e "s#__USER__#$USER_NAME#g" -e "s#__DIR__#$DIR#g" "$DIR/flight-display.service" \
  > /etc/systemd/system/flight-display.service
systemctl daemon-reload
systemctl enable --now flight-display
sleep 2
systemctl --no-pager --lines=5 status flight-display || true
echo
echo "Check it: curl http://localhost:8080/display.json"
echo "Panel URL: http://$(hostname -I | awk '{print $1}'):8080/display.json"
