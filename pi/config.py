"""guest-tv: paths and settings of the Pi. Every Pi module takes them from here.

The settings live in ~/guest-tv/config.json (bootstrap.sh copies config.example.json there if it is
missing). JSON has no comments, so the fields are documented here. A missing field means "not
configured": that part is skipped (one log line), nothing crashes. state_builder.py reads the file
again on every hourly run; only the TV list needs a service restart (see tvs).

  tvs                  {"<tv-ip>": "<wifi-mac>"}: the TVs this Pi serves. Only these IPs may call
                       POST /tv/setting; the MAC wakes the TV (Wake-on-LAN) before the cleanup after
                       a departure. Restart guest-tv.service after editing the list (serve.py reads it
                       once at start).
  turnover             true = reset every TV after each departure (turnover.py). Default: off.
  timezone             IANA name like "Europe/Zurich". Check-out (11:00) and check-in (16:00) are local
                       times. Default: UTC.
  smoobu_apartment_id  Smoobu apartment id; the API keys go to /etc/guest-tv/secrets.env.
  bookings_file        JSON file shaped like Smoobu's response {"bookings": [...]}; relative paths are
                       relative to ~/guest-tv. When set it wins over Smoobu: for trying things out, or to
                       feed bookings from another system. Neither set = no guest (vacancy).
  weather              {"lat": 46.0, "lon": 8.0}: Open-Meteo forecast for the apartment.
  mountain             [{"name", "lat", "lon", "elev"}]: extra weather points (mountain stations,
                       elevation in m), shown together with the weather (needs "weather"). All four
                       fields are required, bad entries are skipped.
  bus                  {"station_id": "", "via": []}: departures from transport.opendata.ch (Swiss
                       public transport only). "via" = stop names worth showing as "via X".
  webcams              [{"id", "name", "elev", "url"}]: JPEG webcams, fetched hourly to www/cams/<id>.jpg.
                       All four fields are required; id must match ^[a-z0-9-]+$ (it becomes a file name),
                       url must be http(s). Bad entries are skipped.
  photos               ["assets/img/photo-1.jpg", ...]: app-relative images for the screensaver.

Secrets stay out of the config: /etc/guest-tv/secrets.env (Smoobu keys) and /etc/guest-tv/wlan.env
(guest Wi-Fi shown on the TV), both 0640 root:<user>.
"""

import json
from pathlib import Path

HOME = Path.home() / "guest-tv"
CONFIG_FILE = HOME / "config.json"
ETC = Path("/etc/guest-tv")


def load() -> dict:
    """config.json as a dict. Missing file = {} (nothing configured); unreadable or invalid = RuntimeError."""
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeError(f"{CONFIG_FILE} unreadable: {exc}") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"{CONFIG_FILE} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{CONFIG_FILE} must contain a JSON object")
    return data
