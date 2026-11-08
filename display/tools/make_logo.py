"""Turn any logo image into a 16x16, 16-colour BMP for the panel.

    pip install pillow
    python3 make_logo.py easyjet.png EZY      ->  ../logos/EZY.bmp

Name the output after the airline's ICAO prefix (first 3 letters of the callsign).
Logos look best on a black background: transparent areas become black (LED off).
"""

import os
import sys

from PIL import Image


def main(src, icao):
    img = Image.open(src).convert("RGBA")
    bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    img.thumbnail((16, 16), Image.LANCZOS)
    canvas = Image.new("RGB", (16, 16), (0, 0, 0))
    canvas.paste(img, ((16 - img.width) // 2, (16 - img.height) // 2))
    out = canvas.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    path = os.path.join(os.path.dirname(__file__), "..", "logos", icao.upper() + ".bmp")
    out.save(path)
    print("wrote", os.path.normpath(path))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
