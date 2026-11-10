# Panel test for the Matrix Portal: no Wi-Fi, no libraries, no fonts. Copy it to CIRCUITPY as
# code.py (keep your real code.py somewhere safe first), and it cycles the whole panel through
# red, green, blue, white, then a moving bar, forever. The serial log says what it's showing.
# If the panel stays dark, the problem is wiring/power/the panel, not the flight display code.
import time

import board
import digitalio
import displayio
import framebufferio
import rgbmatrix

displayio.release_displays()
matrix = rgbmatrix.RGBMatrix(
    width=64, height=32, bit_depth=4,
    rgb_pins=[board.MTX_R1, board.MTX_G1, board.MTX_B1, board.MTX_R2, board.MTX_G2, board.MTX_B2],
    addr_pins=[board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC, board.MTX_ADDRD],
    clock_pin=board.MTX_CLK, latch_pin=board.MTX_LAT, output_enable_pin=board.MTX_OE)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=True)

bmp = displayio.Bitmap(64, 32, 5)
pal = displayio.Palette(5)  # quarter brightness: a full-white panel at full power can draw 4 A
pal[0] = 0x000000
pal[1] = 0x400000
pal[2] = 0x004000
pal[3] = 0x000040
pal[4] = 0x404040
root = displayio.Group()
root.append(displayio.TileGrid(bmp, pixel_shader=pal))
display.root_group = root

# The little red "L" LED on the Portal blinks with each colour, so you can tell the test is
# running even when the board is on a wall supply with no serial window.
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output()

while True:
    for name, c in (("red", 1), ("green", 2), ("blue", 3), ("white", 4)):
        print("panel test:", name)
        led.value = not led.value
        bmp.fill(c)
        time.sleep(2)
    print("panel test: moving bar (top row to bottom)")
    bmp.fill(0)
    for y in range(32):
        for x in range(64):
            bmp[x, y] = 4
        time.sleep(0.05)
        for x in range(64):
            bmp[x, y] = 0
