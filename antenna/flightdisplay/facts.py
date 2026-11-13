"""Aircraft facts for the panel's facts card, keyed by ICAO type designator (readsb's "t" field).

Figures are approximate, rounded, well-known values (typical cruise for airliners, top speed for
warbirds and military jets). Each line must fit the panel: 16 characters max.

    TYPE: (NAME, SPEED LINE, CEILING FT, FIRST FLIGHT YEAR, EXTRA LINE)
"""

FACTS = {
    # --- Airbus ---
    "A319": ("AIRBUS A319", "CRUISE 450KT", 39000, 1995, "ABOUT 140 SEATS"),
    "A320": ("AIRBUS A320", "CRUISE 450KT", 39000, 1987, "ABOUT 180 SEATS"),
    "A321": ("AIRBUS A321", "CRUISE 450KT", 39000, 1993, "ABOUT 220 SEATS"),
    "A19N": ("A319NEO", "CRUISE 450KT", 39000, 2017, "QUIETER ENGINES"),
    "A20N": ("A320NEO", "CRUISE 450KT", 39000, 2014, "20% LESS FUEL"),
    "A21N": ("A321NEO", "CRUISE 450KT", 39000, 2016, "UP TO 244 SEATS"),
    "A332": ("AIRBUS A330-200", "CRUISE 470KT", 41000, 1997, "ABOUT 250 SEATS"),
    "A333": ("AIRBUS A330-300", "CRUISE 470KT", 41000, 1992, "ABOUT 300 SEATS"),
    "A339": ("AIRBUS A330NEO", "CRUISE 470KT", 41000, 2017, "ABOUT 290 SEATS"),
    "A359": ("AIRBUS A350", "CRUISE 490KT", 43000, 2013, "MOSTLY CARBON"),
    "A35K": ("AIRBUS A350-1000", "CRUISE 490KT", 41000, 2016, "ABOUT 370 SEATS"),
    "A388": ("AIRBUS A380", "CRUISE 490KT", 43000, 2005, "UP TO 853 SEATS"),
    # --- Boeing ---
    "B737": ("BOEING 737-700", "CRUISE 450KT", 41000, 1997, "ABOUT 140 SEATS"),
    "B738": ("BOEING 737-800", "CRUISE 450KT", 41000, 1997, "ABOUT 189 SEATS"),
    "B739": ("BOEING 737-900", "CRUISE 450KT", 41000, 2000, "ABOUT 215 SEATS"),
    "B38M": ("737 MAX 8", "CRUISE 450KT", 41000, 2016, "UP TO 210 SEATS"),
    "B39M": ("737 MAX 9", "CRUISE 450KT", 41000, 2017, "UP TO 220 SEATS"),
    "B752": ("BOEING 757", "CRUISE 460KT", 42000, 1982, "ROCKET OF SKIES"),
    "B763": ("BOEING 767", "CRUISE 460KT", 43000, 1981, "ABOUT 260 SEATS"),
    "B772": ("BOEING 777-200", "CRUISE 490KT", 43000, 1994, "ABOUT 310 SEATS"),
    "B77W": ("BOEING 777-300ER", "CRUISE 490KT", 43000, 2003, "BIGGEST TWINJET"),
    "B788": ("BOEING 787-8", "CRUISE 490KT", 43000, 2009, "HALF COMPOSITE"),
    "B789": ("BOEING 787-9", "CRUISE 490KT", 43000, 2013, "HALF COMPOSITE"),
    "B78X": ("BOEING 787-10", "CRUISE 490KT", 41000, 2017, "HALF COMPOSITE"),
    "B744": ("BOEING 747-400", "CRUISE 490KT", 45000, 1988, "QUEEN OF SKIES"),
    "B748": ("BOEING 747-8", "CRUISE 490KT", 43000, 2010, "LONGEST AIRLINER"),
    # --- Regional ---
    "E190": ("EMBRAER 190", "CRUISE 450KT", 41000, 2004, "ABOUT 100 SEATS"),
    "E195": ("EMBRAER 195", "CRUISE 450KT", 41000, 2004, "ABOUT 120 SEATS"),
    "E290": ("EMBRAER E190-E2", "CRUISE 450KT", 41000, 2016, "ABOUT 110 SEATS"),
    "E295": ("EMBRAER E195-E2", "CRUISE 450KT", 41000, 2017, "ABOUT 140 SEATS"),
    "AT76": ("ATR 72", "CRUISE 275KT", 25000, 1988, "ABOUT 70 SEATS"),
    "AT75": ("ATR 72", "CRUISE 275KT", 25000, 1988, "ABOUT 70 SEATS"),
    "AT45": ("ATR 42", "CRUISE 270KT", 25000, 1984, "ABOUT 48 SEATS"),
    "DH8D": ("DASH 8 Q400", "CRUISE 360KT", 25000, 1998, "ABOUT 78 SEATS"),
    # --- Light aircraft and helicopters ---
    "C152": ("CESSNA 152", "CRUISE 105KT", 14000, 1977, "CLASSIC TRAINER"),
    "C172": ("CESSNA 172", "CRUISE 120KT", 14000, 1955, "MOST BUILT PLANE"),
    "PA28": ("PIPER CHEROKEE", "CRUISE 120KT", 14000, 1960, "FLYING SCHOOLS"),
    "SR22": ("CIRRUS SR22", "CRUISE 180KT", 17500, 2000, "HAS A PARACHUTE"),
    "DA40": ("DIAMOND DA40", "CRUISE 140KT", 16000, 1997, "MADE IN AUSTRIA"),
    "EC35": ("H135 HELICOPTER", "MAX 140KT", 20000, 1994, "AIR AMBULANCE"),
    "EC45": ("H145 HELICOPTER", "MAX 145KT", 18000, 1999, "POLICE & MEDICS"),
    "A109": ("AW109 HELICOPTER", "MAX 165KT", 20000, 1971, "FAST AND SLEEK"),
    "A169": ("AW169 HELICOPTER", "MAX 160KT", 15000, 2012, "AIR AMBULANCE"),
    "A189": ("AW189 HELICOPTER", "MAX 165KT", 15000, 2011, "COASTGUARD SAR"),
    "S92": ("SIKORSKY S-92", "MAX 165KT", 14000, 1998, "COASTGUARD SAR"),
    # --- Military ---
    "HAWK": ("BAE HAWK T1", "MAX 555KT", 50000, 1974, "RED ARROWS JET"),
    "EUFI": ("TYPHOON", "MAX MACH 2", 55000, 1994, "RAF FIGHTER"),
    "F35": ("F-35B LIGHTNING", "MAX MACH 1.6", 50000, 2006, "LANDS VERTICALLY"),
    "A400": ("A400M ATLAS", "CRUISE 420KT", 40000, 2009, "RAF TRANSPORT"),
    "C17": ("C-17 GLOBEMASTER", "CRUISE 450KT", 45000, 1991, "CAN CARRY A TANK"),
    "C130": ("C-130 HERCULES", "CRUISE 340KT", 28000, 1954, "WORKHORSE"),
    "P8": ("P-8 POSEIDON", "CRUISE 440KT", 41000, 2009, "SUB HUNTER"),
    "H47": ("CHINOOK", "MAX 170KT", 18000, 1961, "TWIN ROTORS"),
    "B52": ("B-52 BOMBER", "MAX 560KT", 50000, 1952, "EIGHT ENGINES"),
    "A124": ("ANTONOV AN-124", "CRUISE 430KT", 39000, 1982, "GIANT FREIGHTER"),
    # --- Warbirds ---
    "SPIT": ("SPITFIRE", "MAX 360KT", 43000, 1936, "WW2 RAF LEGEND"),
    "HURI": ("HURRICANE", "MAX 295KT", 36000, 1935, "BATTLE BRITAIN"),
    "LANC": ("LANCASTER", "MAX 245KT", 21000, 1941, "ONLY 2 STILL FLY"),
    "P51": ("P-51 MUSTANG", "MAX 380KT", 41000, 1940, "ESCORT FIGHTER"),
    "B17": ("B-17 FORTRESS", "MAX 250KT", 35000, 1935, "FLYING FORTRESS"),
    "DC3": ("DOUGLAS DAKOTA", "CRUISE 180KT", 23000, 1935, "D-DAY VETERAN"),
    "DH82": ("TIGER MOTH", "MAX 95KT", 13000, 1931, "RAF TRAINER"),
    "T6": ("HARVARD", "MAX 180KT", 24000, 1935, "PILOT TRAINER"),
}

# Types that get the gold "RARE!" banner when they come within range.
RARE = {"SPIT", "HURI", "LANC", "P51", "B17", "DC3", "DH82", "T6", "B52", "A124", "A388", "B744", "B748",
        "H47", "C17"}


def card(type_code):
    """The five panel lines for a type, or None if we have no facts for it."""
    f = FACTS.get((type_code or "").upper())
    if not f:
        return None
    name, speed, ceiling, year, extra = f
    return [name, speed, f"CEILING {ceiling}FT", f"FIRST FLEW {year}", extra]


# Top-down sprite for the fly-by transition (drawn on the Portal): twin / quad / prop / fighter / heli.
SPRITE = {}
for _t in ("A388", "B744", "B748", "A400", "C17", "A124", "B52", "LANC", "B17", "C130", "A343", "A346"):
    SPRITE[_t] = "quad"
for _t in ("SPIT", "HURI", "P51", "DH82", "T6", "C152", "C172", "PA28", "SR22", "DA40", "DC3",
           "C150", "C162", "C170", "C177", "C182", "C206", "C210", "P28A", "P28B", "P28R", "P28T", "PA18",
           "PA32", "PA34", "PA38", "PA44", "PA46", "BE33", "BE35", "BE36", "BE58", "SR20", "DA42", "DA62",
           "DR40", "AA5", "TB10", "TB20", "C42", "EV97", "RV7", "RV8", "RV9", "J3", "CUB", "TAMP", "PC12",
           "TBM7", "TBM8", "TBM9", "BN2P", "DHC6", "G115", "TUTR", "SIRA", "PRES", "P68"):
    SPRITE[_t] = "prop"
for _t in ("EUFI", "F35", "HAWK", "TOR", "F15", "F16", "F18"):
    SPRITE[_t] = "fighter"
for _t in ("EC35", "EC45", "A109", "A169", "A189", "S92", "H47", "AS65", "EC30", "R44", "R22", "B06", "AW39",
           "A139", "A149", "AS50", "AS55", "AS32", "AS3B", "B407", "B429", "B505", "EC20", "EC55", "EC75",
           "H160", "R66", "S76", "EH10", "LYNX", "NH90", "PUMA", "GAZL", "H60", "MD52", "MD60", "B412",
           "A119", "EC25", "EC30", "BK17", "WILD", "MRLN", "A129"):
    SPRITE[_t] = "heli"

# ADS-B emitter category, for types we don't know (or when there's no type at all).
SPRITE_BY_CATEGORY = {"A1": "prop", "A7": "heli", "B1": "prop", "B4": "prop", "B6": "prop"}


def sprite_for(type_code, category=""):
    """twin / quad / prop / fighter / heli: from the type if we know it, else the ADS-B category."""
    return (SPRITE.get((type_code or "").upper())
            or SPRITE_BY_CATEGORY.get((category or "").upper())
            or "twin")


# Name for the type line on the flight screen (11 characters max: it sits beside the logo).
PANEL_TYPE = {
    "A319": "A319", "A320": "A320", "A321": "A321", "A19N": "A319NEO", "A20N": "A320NEO", "A21N": "A321NEO",
    "A332": "A330-200", "A333": "A330-300", "A339": "A330NEO", "A359": "A350-900", "A35K": "A350-1000",
    "A388": "A380", "A343": "A340-300", "A346": "A340-600",
    "B737": "737-700", "B738": "737-800", "B739": "737-900", "B38M": "737 MAX 8", "B39M": "737 MAX 9",
    "B752": "757-200", "B763": "767-300", "B772": "777-200", "B77W": "777-300ER", "B77L": "777-200LR",
    "B788": "787-8", "B789": "787-9", "B78X": "787-10", "B744": "747-400", "B748": "747-8",
    "E170": "EMBRAER 170", "E175": "EMBRAER 175", "E190": "EMBRAER 190", "E195": "EMBRAER 195",
    "E290": "E190-E2", "E295": "E195-E2", "AT75": "ATR 72", "AT76": "ATR 72", "AT45": "ATR 42",
    "DH8D": "DASH 8 Q400", "BCS1": "A220-100", "BCS3": "A220-300", "CRJ9": "CRJ900", "CRJ7": "CRJ700",
    "C152": "CESSNA 152", "C172": "CESSNA 172", "C182": "CESSNA 182", "PA28": "PIPER PA-28", "P28A": "PIPER PA-28", "P28R": "PIPER ARROW",
    "SR22": "CIRRUS SR22", "DA40": "DIAMOND 40", "DA42": "DIAMOND 42",
    "EC35": "H135", "EC45": "H145", "EC30": "H130", "A109": "AW109", "A169": "AW169", "A189": "AW189",
    "AW39": "AW139", "S92": "S-92", "R44": "ROBINSON 44", "R22": "ROBINSON 22", "AS65": "DAUPHIN",
    "HAWK": "HAWK T1", "EUFI": "TYPHOON", "F35": "F-35B", "A400": "A400M ATLAS", "C17": "C-17",
    "C130": "C-130", "P8": "P-8A", "H47": "CHINOOK", "B52": "B-52", "A124": "AN-124", "A332M": "VOYAGER",
    "SPIT": "SPITFIRE", "HURI": "HURRICANE", "LANC": "LANCASTER", "P51": "MUSTANG", "B17": "B-17",
    "DC3": "DAKOTA", "DH82": "TIGER MOTH", "T6": "HARVARD",
}


def panel_type(type_code):
    """'B738' -> '737-800'. Unknown types stay as their code."""
    code = (type_code or "").upper()
    return PANEL_TYPE.get(code, code)
