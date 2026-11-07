"""guest-tv: state_builder

Builds www/state.json for the TV app: current/next guest (from Smoobu or a bookings file), weather
from Open-Meteo, departures, webcams, content from content.json, Wi-Fi from wlan.env, and QR codes
(Wi-Fi, WhatsApp, guest card, FAQ links) as data: URIs right inside state.json, so the TV app caches
them too and still shows them while the Pi is away. Written atomically (tmp + rename); a failing
source keeps its last state. Run by the systemd timer (hourly) or by hand: python3 state_builder.py

Layout on the Pi:
  ~/guest-tv/{state_builder.py,serve.py,config.json,lib/,content/,www/}
  /etc/guest-tv/{secrets.env,wlan.env}   (0640, root:<user>)
Every config.json field is documented in config.py.
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import config
import smoobu_auth

assert sys.version_info >= (3, 11), f"Requires Python 3.11+, got {sys.version_info[:2]}"

BASE = Path(__file__).resolve().parent
WWW = BASE / "www"
CONTENT_FILE = BASE / "content" / "content.json"
WLAN_ENV = config.ETC / "wlan.env"
STATE_OUT = WWW / "state.json"
CAM_DIR = WWW / "cams"

SMOOBU_URL = "https://login.smoobu.com/api/reservations"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
TRANSPORT = "https://transport.opendata.ch/v1/stationboard"  # Swiss public transport only
TZ = ZoneInfo(config.load().get("timezone") or "UTC")  # with DST, so 11:00/16:00 stay right in summer
CHECKOUT_HOUR = 11
RECENT_DAYS = 7  # departures this far back still get their cleanup (Pi off during a whole stay)
# The timer runs hourly, so public sources (weather, departures, webcams) are fetched at most once an
# hour, with our own User-Agent; the TVs only ever load images from the Pi.
UA = "guest-tv (+https://github.com/kevintheo-ai/guest-tv)"
CAM_ID = re.compile(r"[a-z0-9-]+")  # becomes a file name in www/cams/


def log(msg: str) -> None:
    print(f"[state_builder {datetime.now(TZ).strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def read_env(path: Path) -> dict[str, str]:
    """KEY=value lines as bootstrap.sh writes them. The value is taken exactly as written (no trimming, no
    quote removal): spaces or quotes at the edge of a Wi-Fi name or password are real characters."""
    vals: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v
    except OSError:
        pass
    return vals


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valid_entries(items, part: str, ok) -> list[dict]:
    """Config list entries that pass `ok`; bad entries are skipped with a log line."""
    good = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and ok(item):
            good.append(item)
        else:
            log(f"{part}: skipping bad entry {item!r}")
    return good


def mountain_ok(m: dict) -> bool:
    return isinstance(m.get("name"), str) and all(_is_num(m.get(k)) for k in ("lat", "lon", "elev"))


def webcam_ok(c: dict) -> bool:
    cid, url = c.get("id"), c.get("url")
    return (
        isinstance(cid, str)
        and CAM_ID.fullmatch(cid) is not None
        and isinstance(c.get("name"), str)
        and _is_num(c.get("elev"))
        and isinstance(url, str)
        and url.startswith(("https://", "http://"))
    )


# ---------- Bookings ----------


def fetch_smoobu(today: date, apartment_id: str) -> dict | None:
    """Reservations overlapping today-7..+14 days; the response dict or None. The past week is there for the
    cleanup: departures the Pi missed while it was off (recent_departures)."""
    qs = urllib.parse.urlencode(
        {
            "from": (today - timedelta(days=RECENT_DAYS)).isoformat(),
            "to": (today + timedelta(days=14)).isoformat(),
            "apartmentId": apartment_id,
            "pageSize": "100",  # three weeks of one apartment, cancellations and blocks included, fit one page
            "page": "1",
        }
    )
    url = f"{SMOOBU_URL}?{qs}"
    for attempt in range(2):
        key, secret = smoobu_auth.load_credentials()
        signed_url, headers = smoobu_auth.sign("GET", url, b"", key, secret)
        try:  # through http_get: overall time and size limit, like every other source of the hourly run
            data = json.loads(
                http_get(signed_url, limit=5_000_000, timeout=25, headers={**headers, "Accept": "application/json"})[0]
            )
            if isinstance(data, dict) and isinstance(data.get("bookings"), list):
                if (data.get("page_count") or 1) > 1:
                    log("Smoobu: more than one page of bookings, only the first one is used")
                return data
            log(f"Smoobu reply without a bookings list (attempt {attempt + 1})")
        except Exception as exc:
            log(f"Smoobu attempt {attempt + 1} failed: {exc}")
    return None


def load_bookings(cfg: dict, today: date) -> dict | None:
    """{"bookings": [...]} from bookings_file (wins) or Smoobu; None = source unreachable (keep the last
    guest). Neither configured = no bookings, the apartment shows vacancy."""
    if cfg.get("bookings_file"):
        path = config.HOME / Path(cfg["bookings_file"]).expanduser()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"bookings file unreadable: {exc}")
            return None
        if isinstance(data, dict) and isinstance(data.get("bookings"), list):
            return data
        log(f"{path}: no bookings list")
        return None
    if cfg.get("smoobu_apartment_id"):
        try:
            return fetch_smoobu(today, str(cfg["smoobu_apartment_id"]))
        except RuntimeError as exc:  # secrets.env missing or incomplete
            log(str(exc))
            return None
    log("bookings: no source configured (bookings_file, smoobu_apartment_id), vacancy")
    return {"bookings": []}


def current_guest(bookings: list[dict], today: date, now: datetime) -> dict | None:
    """Time-aware phase (check-out 11:00, check-in 16:00):
    active   = between arrival and departure; on the departure day until 11:00 (check-out info matters)
    arrival  = on the arrival day from 11:00 (greeting on arrival), or from today for tomorrow
    None     = vacancy, also on the departure day after 11:00 (cleaning) and on the arrival day until 11:00
    (Same-day turnover: the departing guest stays active until 11:00, then the arriving one.)"""
    live = [
        b
        for b in bookings
        if isinstance(b, dict) and b.get("type") != "cancellation" and not b.get("is-blocked-booking")
    ]  # a hand-written bookings file may hold junk entries: skip them, never stop the hourly run

    def span(b: dict) -> tuple[date, date] | None:
        try:
            return (
                date.fromisoformat(str(b.get("arrival"))[:10]),
                date.fromisoformat(str(b.get("departure"))[:10]),
            )
        except ValueError:
            return None

    for b in live:  # mid-stay
        s = span(b)
        if s and s[0] < today < s[1]:
            return guest_from(b, "active")
    for b in live:  # departure day: the guest is there until 11:00 (check-out checklist)
        s = span(b)
        if s and s[1] == today and now.hour < CHECKOUT_HOUR:
            return guest_from(b, "active")
    # Today's arrival wins over tomorrow's, whatever the list order (two one-night bookings listed
    # "tomorrow, today" would otherwise show the next guest). Before 11:00 on the arrival day: vacancy,
    # do not show tomorrow's guest yet.
    today_arrivals = [b for b in live if (s := span(b)) and s[0] == today]
    if today_arrivals:
        return guest_from(today_arrivals[0], "arrival") if now.hour >= CHECKOUT_HOUR else None
    for b in live:  # arrival tomorrow
        s = span(b)
        if s and s[0] == today + timedelta(days=1):
            return guest_from(b, "arrival")
    return None


def booking_id(b: dict) -> str:
    """The booking's id; a hand-written booking without one gets a stable id from its dates. The app needs it to
    notice a guest change (picture reset, language), turnover.py to remember the departure for the cleanup."""
    return str(b.get("id") or b.get("reference-id") or f"{str(b.get('arrival'))[:10]}_{str(b.get('departure'))[:10]}")


def _text(v) -> str:
    """A text field of a booking; anything else (number, list in a hand-written file) counts as empty."""
    return v.strip() if isinstance(v, str) else ""


def guest_from(b: dict, phase: str) -> dict:
    # An empty name is allowed: the app then greets without a name (in the guest's language).
    name = " ".join(x for x in (_text(b.get("firstname")), _text(b.get("lastname"))) if x)
    return {
        "reservationId": booking_id(b),
        "name": name,
        "language": (_text(b.get("language")) or "en").lower()[:2],
        "arrival": str(b.get("arrival"))[:10],
        "departure": str(b.get("departure"))[:10],
        "phase": phase,
    }


def recent_departures(bookings: list, today: date) -> list[dict]:
    """Departures of the last RECENT_DAYS days (today included) for turnover.run_due: a Pi that was off for a
    whole stay never saw that guest, and without this the next guest would find the previous logins."""
    out = []
    for b in bookings:
        # cancelled stays count: a booking cancelled after the stay may have been real, and an extra cleanup of an
        # empty apartment costs nothing; the owner's blocks never had a guest
        if not isinstance(b, dict) or b.get("is-blocked-booking"):
            continue
        try:
            dep = date.fromisoformat(str(b.get("departure"))[:10])
        except ValueError:
            continue
        if today - timedelta(days=RECENT_DAYS) <= dep <= today:
            out.append({"reservationId": booking_id(b), "departure": dep.isoformat()})
    return out


def guest_expired(guest: dict, now: datetime) -> bool:
    """True when the stay is over (departure day from 11:00): personal data never goes into vacancy."""
    try:
        dep = date.fromisoformat(str(guest.get("departure", ""))[:10])
    except ValueError:
        return True
    today = now.date()
    return dep < today or (dep == today and now.hour >= CHECKOUT_HOUR)


# ---------- Public sources: weather, mountain weather, departures, webcams ----------


def http_get(
    url: str, limit: int = 2_000_000, timeout: int = 15, total: float = 60, headers: dict | None = None
) -> tuple[bytes, str | None]:
    """GET with our User-Agent, a size limit and an overall time limit; (body, Last-Modified).
    `timeout` only bounds each socket wait: a server that trickles a few bytes now and then would otherwise
    hold the hourly run, and with it state.json and the cleanup after departures, for hours."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    deadline = time.monotonic() + total
    chunks, size = [], 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        modified = resp.headers.get("Last-Modified")
        while chunk := resp.read1(65536):  # returns after each receive, so the deadline is checked in between
            size += len(chunk)
            if size > limit:
                raise ValueError(f"reply larger than {limit} bytes")
            if time.monotonic() > deadline:
                raise TimeoutError(f"reply took longer than {total:g} s")
            chunks.append(chunk)
    return b"".join(chunks), modified


def open_meteo(**params) -> str:
    return f"{OPEN_METEO}?{urllib.parse.urlencode(params, safe=',/')}"


def _num(values, i: int) -> int | None:
    """Daily value, rounded; None instead of an abort when it is missing (list too short or null)."""
    v = values[i] if isinstance(values, list) and i < len(values) else None
    return round(v) if isinstance(v, (int, float)) else None


def fetch_weather(now: datetime, lat: float, lon: float) -> dict | None:
    url = open_meteo(
        latitude=lat,
        longitude=lon,
        current="temperature_2m,weather_code,wind_speed_10m",
        daily="temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max,wind_speed_10m_max",
        timezone=TZ.key,
        forecast_days=7,
    )
    try:
        w = json.loads(http_get(url)[0])
        cur = w["current"]
        daily = w["daily"]
        days = [
            {
                "date": d,
                "min": round(daily["temperature_2m_min"][i]),
                "max": round(daily["temperature_2m_max"][i]),
                "code": daily["weather_code"][i],
                "rain": _num(daily.get("precipitation_probability_max"), i),
                "wind": _num(daily.get("wind_speed_10m_max"), i),
            }
            for i, d in enumerate(daily["time"][:7])
        ]
        if not days or cur.get("temperature_2m") is None:
            raise ValueError("empty weather data")
    except Exception as exc:
        log(f"Open-Meteo failed: {exc}")
        return None
    return {
        "updated": now.isoformat(timespec="minutes"),
        "current": {
            "temp": cur.get("temperature_2m"),
            "code": cur.get("weather_code"),
            "wind": cur.get("wind_speed_10m"),
        },
        "today": days[0],
        "days": days[1:],  # the next 6 days (today is on top of the list)
    }


def fetch_mountain(points: list[dict]) -> list[dict] | None:
    url = open_meteo(
        latitude=",".join(str(m["lat"]) for m in points),
        longitude=",".join(str(m["lon"]) for m in points),
        elevation=",".join(str(m["elev"]) for m in points),
        current="temperature_2m,weather_code,wind_speed_10m",
        daily="temperature_2m_max,temperature_2m_min,weather_code",
        forecast_days=7,
        timezone=TZ.key,
    )
    try:
        data = json.loads(http_get(url)[0])
        data = data if isinstance(data, list) else [data]
        out = []
        for m, d in zip(points, data, strict=True):
            cur = d["current"]
            if cur.get("temperature_2m") is None:
                raise ValueError(f"{m['name']}: no temperature")
            daily = d.get("daily") or {}
            out.append(
                {
                    "name": m["name"],
                    "elev": m["elev"],
                    "temp": cur["temperature_2m"],
                    "code": cur.get("weather_code"),
                    "wind": cur.get("wind_speed_10m"),
                    # days parallel to weather.today/days (index 0 = today); missing value = no mountain row that day
                    "days": [
                        {
                            "date": day,
                            "min": _num(daily.get("temperature_2m_min"), i),
                            "max": _num(daily.get("temperature_2m_max"), i),
                            "code": _num(daily.get("weather_code"), i),
                        }
                        for i, day in enumerate((daily.get("time") or [])[:7])
                    ],
                }
            )
        return out
    except Exception as exc:
        log(f"mountain weather failed: {exc}")
        return None


def build_weather(cfg: dict, now: datetime, prev: dict | None) -> dict | None:
    """Weather for the apartment plus the mountain points. A failed fetch keeps the last state; a part
    that is not configured is left out (and dropped from the previous state)."""
    spot = cfg.get("weather")
    if not (isinstance(spot, dict) and _is_num(spot.get("lat")) and _is_num(spot.get("lon"))):
        log("weather: not configured, skipped")
        return None
    weather = fetch_weather(now, spot["lat"], spot["lon"]) or prev
    points = valid_entries(cfg.get("mountain"), "mountain", mountain_ok)
    mountain = fetch_mountain(points) if points else None
    if not points:
        log("mountain: not configured, skipped")
    if not weather:
        return None
    old = (prev or {}) if points else {}
    # own timestamp for the mountain: the app shows its "now" values only while they are fresh
    return {
        **weather,
        "mountain": mountain or old.get("mountain"),
        "mtnUpdated": now.isoformat(timespec="minutes") if mountain else old.get("mtnUpdated"),
    }


def fetch_bus(station: str, via: list[str]) -> list[dict] | None:
    url = f"{TRANSPORT}?{urllib.parse.urlencode({'id': station, 'limit': 40})}"
    try:
        board = json.loads(http_get(url)[0])["stationboard"]
        out = []
        for s in board:
            try:
                dep = datetime.strptime(s["stop"]["departure"], "%Y-%m-%dT%H:%M:%S%z")
            except (KeyError, TypeError, ValueError):
                continue
            stops = {(p.get("station") or {}).get("name") for p in s.get("passList") or [] if isinstance(p, dict)}
            out.append(
                {
                    "ts": int(dep.timestamp() * 1000),
                    "time": dep.astimezone(TZ).strftime("%H:%M"),
                    "line": str(s.get("number") or "")[:6],
                    "to": str(s.get("to") or "")[:60],
                    "via": [v for v in via if v in stops],
                }
            )
        if not out:
            raise ValueError("no departures")
        return out
    except Exception as exc:
        log(f"bus departures failed: {exc}")
        return None


def fetch_cams(now: datetime, prev: list | None, cams: list[dict]) -> list[dict]:
    """Images to www/cams/<id>.jpg (the TV loads them from the Pi). On failure the last image and its
    timestamp stay."""
    CAM_DIR.mkdir(parents=True, exist_ok=True)
    old = {c.get("id"): c for c in prev or [] if isinstance(c, dict)}
    out = []
    for cam in cams:
        cid = cam["id"]
        dest = CAM_DIR / f"{cid}.jpg"
        updated = (old.get(cid) or {}).get("updated")
        try:
            data, modified = http_get(cam["url"], limit=5_000_000, timeout=20)
            if not data.startswith(b"\xff\xd8\xff"):
                raise ValueError("not a JPEG")
            try:  # image time from the server; a broken header = fetch time (never a new image with an old time)
                when = parsedate_to_datetime(modified).astimezone(TZ) if modified else now
            except (TypeError, ValueError):
                when = now
            if not now - timedelta(days=30) <= when <= now + timedelta(minutes=5):
                when = now  # a camera without a set clock (1970) or with one in the future: fetch time
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            updated = when.isoformat(timespec="minutes")
        except Exception as exc:
            log(f"webcam {cid} failed: {exc}")
        if dest.exists() and updated:
            out.append(
                {"id": cid, "name": cam["name"], "elev": cam["elev"], "img": f"cams/{cid}.jpg", "updated": updated}
            )
    return out


# ---------- QR codes ----------


def qr_data_uri(payload: str) -> str | None:
    """PNG QR code as a data: URI (qrencode, apt). None if qrencode is missing or fails."""
    try:
        png = subprocess.run(
            ["qrencode", "-o", "-", "-t", "PNG", "-s", "8", "-m", "2", payload],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
    except Exception as exc:
        log(f"qrencode failed: {exc}")
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def wlan_from(env: dict[str, str], prev: dict) -> dict:
    """Wi-Fi from wlan.env; if the file is empty or unreadable, the last state stays
    (otherwise a single read error would send an empty password to every TV)."""
    wlan = {"ssid": env.get("WLAN_SSID", ""), "password": env.get("WLAN_PASSWORD", "")}
    old = prev.get("wlan") or {}
    if not wlan["ssid"] and old.get("ssid"):
        log("wlan.env empty/unreadable - keeping the last Wi-Fi state")
        return {"ssid": old["ssid"], "password": old.get("password", "")}
    return wlan


def wifi_payload(ssid: str, password: str) -> str:
    def esc(v: str) -> str:  # escape the reserved characters of the WIFI: format
        return v.translate(str.maketrans({"\\": "\\\\", ";": "\\;", ",": "\\,", ":": "\\:", '"': '\\"'}))

    return f"WIFI:S:{esc(ssid)};T:WPA;P:{esc(password)};;"


def build_qr(wlan: dict, content: dict) -> dict:
    """QR codes for Wi-Fi, WhatsApp, guest card, video + one per FAQ entry with a url (stored in content)."""
    help_ = content.get("help") or {}
    qr = {
        "wlan": qr_data_uri(wifi_payload(wlan["ssid"], wlan["password"]))
        if wlan["ssid"] and wlan["password"]
        else None,
        "whatsapp": None,
        "guestcard": None,
        "video": None,
    }
    if (help_.get("contact") or {}).get("whatsapp"):
        qr["whatsapp"] = qr_data_uri(help_["contact"]["whatsapp"])
    if (help_.get("guestcard") or {}).get("url"):
        qr["guestcard"] = qr_data_uri(help_["guestcard"]["url"])
    if (help_.get("video") or {}).get("url"):
        qr["video"] = qr_data_uri(help_["video"]["url"])
    cache: dict[str, str | None] = {}
    for items in (help_.get("faq") or {}).values():
        for item in items:
            url = item.get("url")
            if url:
                if url not in cache:
                    cache[url] = qr_data_uri(url)
                item["qr"] = cache[url]
    return qr


# ---------- Main ----------


def main() -> int:
    cfg = config.load()
    WWW.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)
    today = now.date()
    prev: dict = {}
    if STATE_OUT.exists():
        try:
            prev = json.loads(STATE_OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}

    bookings = load_bookings(cfg, today)
    departures = []
    if bookings is None:
        guest = prev.get("guest")  # booking source down: keep the last guest …
        log("booking source unreachable - keeping the last guest (with expiry check)")
    else:
        departures = recent_departures(bookings["bookings"], today)
        guest = current_guest(bookings["bookings"], today, now)
        log(f"guest: {'yes' if guest else 'vacancy'} ({guest['phase'] if guest else '-'})")

    # … but never beyond the stay (personal data: no name of the previous guest during vacancy)
    if guest and guest_expired(guest, now):
        log("stale guest dropped (stay is over)")
        guest = None

    weather = build_weather(cfg, now, prev.get("weather"))
    bus_cfg = cfg.get("bus") if isinstance(cfg.get("bus"), dict) else {}
    station = str(bus_cfg.get("station_id") or "")
    if station:
        via = bus_cfg.get("via")
        via = [v for v in via if isinstance(v, str)] if isinstance(via, list) else []
        bus = fetch_bus(station, via) or prev.get("bus")  # timetable: an older list stays valid, the app filters
    else:
        bus = None
        log("bus: not configured, skipped")
    webcams = valid_entries(cfg.get("webcams"), "webcams", webcam_ok)
    cams = fetch_cams(now, prev.get("cams"), webcams) if webcams else []
    if not webcams:
        log("webcams: not configured, skipped")
    photos = cfg.get("photos")
    photos = [p for p in photos if isinstance(p, str)] if isinstance(photos, list) else []
    try:
        content = json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        content = prev.get("content") or {}
        log(f"content.json unreadable ({exc}) - using the last state")

    wlan = wlan_from(read_env(WLAN_ENV), prev)
    try:
        qr = build_qr(wlan, content)
    except Exception as exc:  # a broken FAQ structure must not stop the guest/Wi-Fi update
        log(f"building QR codes failed ({exc}) - continuing without QR")
        qr = {"wlan": None, "whatsapp": None, "guestcard": None, "video": None}
    if guest and guest_expired(guest, datetime.now(TZ)):  # the fetches above can cross 11:00: check again
        guest = None
    state = {
        "updated": now.isoformat(timespec="minutes"),
        "guest": guest,
        "wlan": wlan,
        "qr": qr,
        "weather": weather,
        "bus": bus,
        "cams": cams,
        "photos": photos,
        "content": content,
    }

    tmp = STATE_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_OUT)
    log(f"state.json written ({STATE_OUT.stat().st_size} bytes)")
    try:  # reset the TVs after every departure (turnover.py); a failure there never stops state.json
        import turnover

        # the fetches above may have taken minutes: the time now
        turnover.run_due(guest, datetime.now(TZ), departures=departures)
    except Exception as exc:
        log(f"cleanup after departure failed: {exc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
