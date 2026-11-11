#!/bin/bash
# Copy the flight display onto the Matrix Portal (Mac). Run from anywhere:
#   ./display/install.sh
# Precompiles flight.py to flight.mpy with Adafruit's mpy-cross (downloaded once, matching the
# board's CircuitPython 10.3.1), then copies code.py, flight.mpy, demo.mpy, netcompat.mpy, the fonts and logos onto CIRCUITPY.
set -euo pipefail
cd "$(dirname "$0")"
BOARD="${BOARD:-/Volumes/CIRCUITPY}"
CP_VERSION="10.3.1"
TOOL=".mpy-cross-$CP_VERSION"

if [ ! -d "$BOARD" ]; then
  echo "Can't find $BOARD. Is the Portal plugged in? (ls /Volumes shows the drive name;"
  echo "run with BOARD=/Volumes/NAME ./display/install.sh if it's called something else)"
  exit 1
fi

if [ ! -x "$TOOL" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) FILE="macos/mpy-cross-macos-$CP_VERSION-arm64" ;;
    Linux-x86_64) FILE="linux-amd64/mpy-cross-linux-amd64-$CP_VERSION.static" ;;
    *) echo "No ready-made mpy-cross for $(uname -s) $(uname -m)."
       echo "Copy flight.py to the board instead (works, but with less spare memory)."; exit 1 ;;
  esac
  echo "Downloading mpy-cross $CP_VERSION (once)..."
  curl -fsSL -o "$TOOL" "https://adafruit-circuit-python.s3.amazonaws.com/bin/mpy-cross/$FILE"
  chmod +x "$TOOL"
fi

"./$TOOL" -O1 flight.py -o flight.mpy
"./$TOOL" -O1 demo.py -o demo.mpy
"./$TOOL" -O1 netcompat.py -o netcompat.mpy
cp -X code.py flight.mpy demo.mpy netcompat.mpy "$BOARD/"
rm -f "$BOARD/flight.py" "$BOARD/demo.py" "$BOARD/netcompat.py"   # a .py on the board would be loaded instead of the .mpy
mkdir -p "$BOARD/fonts"
cp -X fonts/*.bdf "$BOARD/fonts/"
mkdir -p "$BOARD/logos"
cp -X logos/*.bmp "$BOARD/logos/"
rm -f flight.mpy demo.mpy netcompat.mpy
sync
echo "Done. The board restarts by itself; the serial log's first line shows the version."
