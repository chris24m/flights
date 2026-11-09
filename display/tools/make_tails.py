"""Generate the 16x16 tail-fin icons in display/logos/ from the table below.

    python3 make_tails.py            # writes ../logos/<ICAO>.bmp and ../logos/preview.png

These are simple stylised tail fins in each airline's colours, not copies of their logos.
To use a real logo for one airline instead, run make_logo.py on an image; it overwrites that file.
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "logos")

FIN = [(1, 15), (15, 15), (15, 2), (13, 0), (9, 0)]   # swept tail fin, leading edge on the left

# ICAO: (fin colour, accent colour, accent style)
TAILS = {
    "EZY": ("#FF6600", "#FFFFFF", "stripe"),     # easyJet
    "EJU": ("#FF6600", "#FFFFFF", "stripe"),
    "EZS": ("#FF6600", "#FFFFFF", "stripe"),
    "RYR": ("#073590", "#F1C933", "dot"),        # Ryanair
    "RUK": ("#073590", "#F1C933", "dot"),
    "BAW": ("#0B1F5C", "#E2231A", "band"),       # British Airways
    "SHT": ("#0B1F5C", "#E2231A", "band"),
    "TOM": ("#4FB3E8", "#E2231A", "smile"),      # TUI
    "EXS": ("#E3242B", "#FFFFFF", "stripe"),     # Jet2
    "VIR": ("#B4061D", "#FFFFFF", "band"),       # Virgin
    "WZZ": ("#2B2D73", "#C6007E", "split"),      # Wizz Air
    "WUK": ("#2B2D73", "#C6007E", "split"),
    "LOG": ("#1F3B7A", "#FFFFFF", "stripe"),     # Loganair
    "AUR": ("#FFFFFF", "#E2A100", "band"),       # Aurigny
    "BCI": ("#2A7DE1", "#FFFFFF", "dot"),        # Blue Islands
    "KLM": ("#00A1DE", "#FFFFFF", "stripe"),
    "AFR": ("#FFFFFF", "#E2231A", "split"),      # Air France (white tail, red/blue)
    "DLH": ("#0A1D3D", "#F9B000", "dot"),        # Lufthansa
    "IBE": ("#D7192D", "#FFCC00", "split"),      # Iberia
    "VLG": ("#FFCC00", "#555555", "stripe"),     # Vueling
    "EIN": ("#00A36C", "#FFFFFF", "dot"),        # Aer Lingus
    "UAE": ("#FFFFFF", "#D71921", "split"),      # Emirates
    "QTR": ("#8E1F4D", "#FFFFFF", "stripe"),     # Qatar
    "SWR": ("#E01933", "#FFFFFF", "cross"),      # Swiss
    "TAP": ("#2EB34B", "#E01933", "band"),
    "THY": ("#E01933", "#FFFFFF", "dot"),        # Turkish
    "SAS": ("#0A1D3D", "#FFFFFF", "stripe"),
    "FDX": ("#4D148C", "#FF6600", "split"),      # FedEx
    "DHK": ("#FFCC00", "#D40511", "stripe"),     # DHL
    "BCS": ("#FFCC00", "#D40511", "stripe"),
    "RRR": ("#1F3B7A", None, "roundel"),         # RAF
    "RED": ("#D40000", None, "roundel"),         # Red Arrows
}


def draw_tail(fin, accent, style):
    img = Image.new("RGB", (16, 16), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon(FIN, fill=fin)
    mask = Image.new("1", (16, 16), 0)
    ImageDraw.Draw(mask).polygon(FIN, fill=1)
    acc = Image.new("RGB", (16, 16), (0, 0, 0))
    a = ImageDraw.Draw(acc)
    amask = Image.new("1", (16, 16), 0)
    m = ImageDraw.Draw(amask)
    if style == "stripe":            # diagonal stripe parallel to the leading edge
        for dm in (m,):
            dm.polygon([(5, 15), (8, 15), (14, 1), (12, 1)], fill=1)
    elif style == "band":            # curved-ish band across the lower fin
        m.polygon([(3, 12), (15, 7), (15, 10), (5, 15), (3, 15)], fill=1)
    elif style == "dot":
        m.ellipse([8, 5, 13, 10], fill=1)
    elif style == "split":           # lower half in the accent colour
        m.polygon([(1, 15), (15, 15), (15, 9), (4, 11)], fill=1)
    elif style == "smile":
        m.arc([6, 3, 14, 11], 20, 160, fill=1, width=2)
    elif style == "cross":
        m.rectangle([10, 4, 11, 11], fill=1)
        m.rectangle([7, 7, 14, 8], fill=1)
    elif style == "roundel":         # blue / white / red rings
        for box, col in (([6, 4, 15, 13], "#1F3B9A"), ([7, 5, 14, 12], "#FFFFFF"), ([8, 6, 13, 11], "#D40000")):
            sub = Image.new("1", (16, 16), 0)
            ImageDraw.Draw(sub).ellipse(box, fill=1)
            layer = Image.new("RGB", (16, 16), col)
            img.paste(layer, (0, 0), sub)
        return img
    if accent:
        a.rectangle([0, 0, 15, 15], fill=accent)
        both = Image.new("1", (16, 16), 0)
        both.paste(amask, (0, 0), mask)
        img.paste(acc, (0, 0), both)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    tiles = []
    for icao, (fin, accent, style) in TAILS.items():
        img = draw_tail(fin, accent, style)
        img.quantize(colors=16, method=Image.Quantize.MEDIANCUT).save(os.path.join(OUT, icao + ".bmp"))
        tiles.append(img)
    cols = 8
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 18, rows * 18), (20, 20, 20))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * 18 + 1, (i // cols) * 18 + 1))
    sheet.resize((sheet.width * 8, sheet.height * 8), Image.NEAREST).save(os.path.join(OUT, "preview.png"))
    print("wrote", len(tiles), "tails to", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
