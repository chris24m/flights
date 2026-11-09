"""Download real airline logos and shrink them to 16x16 BMPs for the panel.

    pip install pillow
    python3 fetch_logos.py                 # all airlines in AIRLINES below
    python3 fetch_logos.py EZY RYR         # just these

Source: the community Jxck-S/airline-logos collection on GitHub (FlightAware-style square
logos, falling back to RadarBox-style ones). Logos are the airlines' trademarks: fine for a
private display, so keep this repo private. Writes ../logos/<ICAO>.bmp and ../logos/preview.png.
"""

import io
import os
import sys
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "logos")
SOURCES = [
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/flightaware_logos/{}.png",
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/radarbox_logos/{}.png",
]

# One code per airline: the Pi maps sister codes (EJU, EZS -> EZY etc.) onto these.
AIRLINES = """
EZY RYR BAW TOM EXS VIR WZZ LOG AUR BCI KLM AFR DLH IBE VLG AEA EIN UAE QTR ETD
AAL UAL DAL ACA SAS TAP THY SWR AUA BEL FIN LOT ITY TRA EWG NOZ ICE TVS PGT ELY
SVA ETH MSR RAM SIA CPA AIC JAI PIA KAC GFA OMA AFL FDX UPS DHK CLX BOX GEC
""".split()


def fetch(icao):
    """Every source's version of this logo that exists."""
    found = []
    for url in SOURCES:
        try:
            with urllib.request.urlopen(url.format(icao), timeout=15) as r:
                found.append(Image.open(io.BytesIO(r.read())))
        except Exception:  # 404 or network: try the next source
            continue
    return found


def visibility(tile):
    """How much light the 16x16 tile gives off: dark navy-on-black logos vanish on LEDs."""
    b = tile.convert("RGB").tobytes()
    return sum(max(b[i:i + 3]) for i in range(0, len(b), 3))


def shrink(img):
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    box = img.point(lambda v: 255 if v > 24 else 0).getbbox()  # trim the empty border
    if box:
        img = img.crop(box)
    img.thumbnail((16, 16), Image.LANCZOS)
    tile = Image.new("RGB", (16, 16), (0, 0, 0))
    tile.paste(img, ((16 - img.width) // 2, (16 - img.height) // 2))
    return tile


def preview():
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".bmp"))
    cols = 10
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 18, rows * 18), (20, 20, 20))
    for i, f in enumerate(files):
        sheet.paste(Image.open(os.path.join(OUT, f)).convert("RGB"), ((i % cols) * 18 + 1, (i // cols) * 18 + 1))
    sheet.resize((sheet.width * 6, sheet.height * 6), Image.NEAREST).save(os.path.join(OUT, "preview.png"))


def main(codes):
    os.makedirs(OUT, exist_ok=True)
    missing = []
    for icao in codes:
        tiles = [shrink(img) for img in fetch(icao)]
        if not tiles:
            missing.append(icao)
            continue
        max(tiles, key=visibility).quantize(colors=16, method=Image.Quantize.MEDIANCUT).save(os.path.join(OUT, icao + ".bmp"))
    preview()
    print("done;", "no logo found for: " + " ".join(missing) if missing else "all found")


if __name__ == "__main__":
    main([c.upper() for c in sys.argv[1:]] or AIRLINES)
