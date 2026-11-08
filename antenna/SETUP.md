# Antenna Pi setup (week 1)

Goal for this week: Pi 3B flashed, readsb + tar1090 decoding the Pro Stick Plus,
aircraft visible on the live map. Allow about an hour.

You need: the Pi 3B, heatsink, case, 12.5 W micro-USB PSU, Pro Stick Plus,
antenna, a 16–32 GB microSD card, a card reader, and a laptop.

## 1. Flash Raspberry Pi OS Lite

1. Install **Raspberry Pi Imager** on the laptop (raspberrypi.com/software).
2. Choose device **Raspberry Pi 3**, OS **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**,
   and your microSD card.
3. When asked, click **Edit settings** (OS customisation) and set:
   - Hostname: `flighttracker`
   - Username and password: e.g. `pi` / something you'll remember (write it down for the handover)
   - Wireless LAN: your home Wi-Fi (Dad's gets added later, see step 8). Country **GB**.
   - Locale: time zone **Europe/London**, keyboard **gb**
   - Services tab: **Enable SSH** (password authentication is fine)
4. Write the card, put it in the Pi.

## 2. Assemble and first boot

1. Stick the heatsink on the main chip (the big square one in the middle), fit the case.
2. Screw the antenna onto the Pro Stick Plus, plug the stick into a USB port
   (use the stub or, better, a 0.5–1 m extension so the stick sits away from the Pi).
3. Put the antenna as high as you can for testing: a windowsill upstairs is fine.
4. Power on. Give it 2–3 minutes on the first boot.
5. From the laptop: `ssh pi@flighttracker.local`
   (if `.local` doesn't resolve, find the Pi's IP in your router's device list).

While you're in the router: **reserve a fixed IP for the Pi** (DHCP reservation).
The Matrix Portal finds the Pi by IP address. Do the same on Dad's router on Christmas Day.

## 3. Update and install readsb

```bash
sudo apt update && sudo apt full-upgrade -y
sudo bash -c "$(wget -O - https://github.com/wiedehopf/adsb-scripts/raw/master/readsb-install.sh)"
```

Then set your location (the map and range rings need it; use your Herts house now, Dad's later).
Get the numbers by right-clicking your house in Google Maps:

```bash
sudo readsb-set-location 51.80 -0.20     # replace with your lat lon
```

Gain: the Pro Stick Plus has its own amp, so let readsb tune itself:

```bash
sudo readsb-gain auto
```

(If that command isn't recognised on your version, `sudo readsb-gain 43.9` is a sensible fixed start;
drop it to 38 or 33 if you see lots of nearby aircraft flicker in and out.)

## 4. Install tar1090 (the live map)

```bash
sudo bash -c "$(wget -nv -O - https://github.com/wiedehopf/tar1090/raw/master/install.sh)"
```

Open **http://flighttracker.local/tar1090** on your laptop or phone.
In Herts you should see aircraft within a minute or two. The raw data our display service
reads is at **http://flighttracker.local/tar1090/data/aircraft.json**.

## 5. Turn on the aircraft database (military flag + aircraft type)

The display service uses two fields that only appear when readsb has the aircraft database:
`dbFlags` (bit 0 = military) and `t` (type, e.g. `A320`). tar1090's installer downloads the database;
point readsb at it:

```bash
ls /usr/local/share/tar1090/aircraft.csv.gz     # should exist after step 4
sudo nano /etc/default/readsb
```

Add `--db-file=/usr/local/share/tar1090/aircraft.csv.gz` inside the quotes of the `JSON_OPTIONS=` line, save,
then `sudo systemctl restart readsb`. Check it worked: in `aircraft.json` most aircraft should now have a
`"t":` field. (If the file is missing, tar1090 may keep it under `/usr/local/share/tar1090/git-db/`: use that path.
The display service also falls back to the UK military hex block and adsbdb.com, so it still works without this.)

## 6. Confirm it's working (week 1 done when…)

- [ ] tar1090 map shows aircraft, and the message rate in the sidebar is non-zero
- [ ] `aircraft.json` updates every second (refresh it in a browser)
- [ ] Aircraft show a type (`t`) after step 5
- [ ] Note the aircraft count and max range after 10 minutes, so you can compare antenna spots later

## 7. Install the display service

```bash
sudo apt install -y git
git clone https://github.com/chris24m/flights.git ~/flights
cd ~/flights/antenna
sudo ./install.sh
nano ~/flights/antenna/config.toml       # set home lat/lon, radius etc.
sudo systemctl restart flight-display
curl http://localhost:8080/display.json
```

Try the alerts without waiting for the RAF:

```bash
curl "http://localhost:8080/test?scenario=emergency&seconds=60"
curl "http://localhost:8080/test?scenario=military&seconds=60"
curl "http://localhost:8080/test?scenario=redarrows&seconds=60"
curl "http://localhost:8080/test?scenario=low&seconds=60"
curl "http://localhost:8080/test?scenario=off"
```

Logs: `journalctl -u flight-display -f`

## 8. Before handover (do these in the dress rehearsal week)

- **Dad's Wi-Fi alongside yours:** `sudo nmtui` → Edit a connection → Add → Wi-Fi, enter Dad's SSID and
  password. Both networks are remembered and the Pi joins whichever is in range.
- **Tailscale:** `curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`, sign in from the
  link it prints. Afterwards you can `ssh pi@flighttracker` from anywhere on your tailnet.
- **Location:** `sudo readsb-set-location <Dad's lat> <lon>` and update `config.toml`.
- **Power-cut test:** pull the plug, plug back in, check the map and `display.json` come back on their own.

## Optional: feed Flightradar24 (free subscription for feeders)

Open question in the brief. If yes, follow the Raspberry Pi instructions at flightradar24.com/share-your-data and,
when asked, tell it to use the existing receiver: "ModeS Beast (TCP)" at `127.0.0.1:30005`. Not needed for the display.
