"""Small built-in lookup tables: airlines by ICAO prefix, friendly aircraft type names."""

# ICAO prefix -> (name for idle screen, brand colour for the callsign text on the panel)
AIRLINES = {
    "BAW": ("British Airways", "#3A6FD8"),
    "SHT": ("British Airways", "#3A6FD8"),
    "EZY": ("easyJet", "#FF6600"),
    "EJU": ("easyJet", "#FF6600"),
    "EZS": ("easyJet", "#FF6600"),
    "RYR": ("Ryanair", "#F1C933"),
    "RUK": ("Ryanair", "#F1C933"),
    "TOM": ("TUI", "#4FB3E8"),
    "EXS": ("Jet2", "#E3242B"),
    "VIR": ("Virgin", "#E10A0A"),
    "WZZ": ("Wizz Air", "#C6007E"),
    "WUK": ("Wizz Air", "#C6007E"),
    "LOG": ("Loganair", "#2E5BBA"),
    "BEE": ("Flybe", "#9B2D8E"),
    "AUR": ("Aurigny", "#E2A100"),
    "BCI": ("Blue Islands", "#2A7DE1"),
    "KLM": ("KLM", "#00A1DE"),
    "AFR": ("Air France", "#3E62C9"),
    "DLH": ("Lufthansa", "#F9B000"),
    "IBE": ("Iberia", "#D7192D"),
    "VLG": ("Vueling", "#FFCC00"),
    "AEA": ("Air Europa", "#2E64C9"),
    "EIN": ("Aer Lingus", "#00A36C"),
    "UAE": ("Emirates", "#D71921"),
    "QTR": ("Qatar", "#8E1F4D"),
    "ETD": ("Etihad", "#BD8B13"),
    "AAL": ("American", "#B7C6D6"),
    "UAL": ("United", "#3A6FD8"),
    "DAL": ("Delta", "#E01933"),
    "ACA": ("Air Canada", "#E01933"),
    "SAS": ("SAS", "#3A6FD8"),
    "TAP": ("TAP", "#2EB34B"),
    "THY": ("Turkish", "#E01933"),
    "SWR": ("Swiss", "#E01933"),
    "FDX": ("FedEx", "#7B3FB5"),
    "UPS": ("UPS", "#B58A4C"),
    "DHK": ("DHL", "#FFCC00"),
    "BCS": ("DHL", "#FFCC00"),
    "RRR": ("RAF", "#3A6FD8"),
}

# readsb/ICAO type designator -> short friendly name for the idle "last seen" line
TYPE_NAMES = {
    "A319": "A319", "A320": "A320", "A321": "A321",
    "A19N": "A319neo", "A20N": "A320neo", "A21N": "A321neo",
    "A332": "A330", "A333": "A330", "A339": "A330neo", "A359": "A350", "A35K": "A350",
    "A388": "A380",
    "B737": "737", "B738": "737", "B739": "737", "B38M": "737 MAX", "B39M": "737 MAX",
    "B752": "757", "B763": "767", "B772": "777", "B77W": "777", "B788": "787", "B789": "787",
    "B78X": "787", "B744": "747", "B748": "747",
    "E190": "E190", "E195": "E195", "E290": "E190-E2", "E295": "E195-E2",
    "AT76": "ATR 72", "AT75": "ATR 72", "DH8D": "Dash 8",
    "C172": "Cessna", "C152": "Cessna", "PA28": "Piper", "SR22": "Cirrus",
    "EC35": "Helicopter", "EC45": "Helicopter", "A109": "Helicopter", "AS65": "Helicopter",
    "HAWK": "Hawk", "A400": "A400M", "C17": "C-17", "C130": "Hercules", "A332M": "Voyager",
    "EUFI": "Typhoon", "F35": "F-35", "P8": "Poseidon",
}


def airline_for(callsign):
    """Return (icao_prefix, name, colour) for a callsign, or (None, None, None)."""
    if not callsign or len(callsign) < 4:
        return None, None, None
    prefix = callsign[:3].upper()
    # Airline callsigns are 3 letters followed by a digit somewhere (EZY45T, BAW2AB).
    if not prefix.isalpha() or not any(ch.isdigit() for ch in callsign[3:]):
        return None, None, None
    name, colour = AIRLINES.get(prefix, (None, None))
    return prefix, name, colour


def friendly_type(type_code):
    if not type_code:
        return None
    return TYPE_NAMES.get(type_code.upper(), type_code.upper())
