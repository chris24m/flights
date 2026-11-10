# Flight display for the Adafruit Matrix Portal (M4) + 64x32 RGB panel.
#
# The real program is flight.py. Copy it to the board precompiled, as flight.mpy (run
# display/install.sh on the Mac): compiling 40 KB of Python on the board itself uses most of its
# memory and leaves too little for the display. flight.py on the board also works, just tighter.
import flight  # noqa: F401
