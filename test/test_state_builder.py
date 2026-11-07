"""Self-check state_builder: guest phases (11:00/16:00 logic), expiry, DST, config.json, QR, CORS,
public sources (offline, http_get replaced) and end-to-end main() runs with test/fixtures/bookings.json.

Run: python3 test/test_state_builder.py   (exit 0 = ok, offline, no API calls)
"""

import atexit
import http.server
import json
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi"))
import config

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="state-builder-test-"))
atexit.register(shutil.rmtree, TMP, True)  # clean up after a failed assert too
config.HOME = TMP / "home"
config.HOME.mkdir()
config.CONFIG_FILE = TMP / "config.json"
config.CONFIG_FILE.write_text(json.dumps({"timezone": "Europe/Zurich"}), encoding="utf-8")
import serve  # noqa: E402
import state_builder as sb  # noqa: E402

TZ = sb.TZ


def ok(msg):
    print(f"ok: {msg}")


def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def booking(bid, arr, dep, **extra):
    return {
        "id": bid,
        "firstname": "Anna",
        "lastname": "Example",
        "language": "nl",
        "arrival": arr,
        "departure": dep,
        "type": "reservation",
        **extra,
    }


def phase(bookings, now):
    g = sb.current_guest(bookings, now.date(), now)
    return (g["reservationId"], g["phase"]) if g else None


# ---------- config.json ----------
assert config.load() == {"timezone": "Europe/Zurich"}
real_cfg = config.CONFIG_FILE
config.CONFIG_FILE = TMP / "missing.json"
assert config.load() == {}  # missing file = nothing configured
for text in ("{broken", "[1, 2]"):  # invalid JSON, not an object
    config.CONFIG_FILE = TMP / "bad.json"
    config.CONFIG_FILE.write_text(text, encoding="utf-8")
    try:
        config.load()
        raise AssertionError(f"bad config accepted: {text}")
    except RuntimeError as exc:
        assert str(config.CONFIG_FILE) in str(exc), exc
config.CONFIG_FILE = real_cfg
example = json.loads((ROOT / "pi/config.example.json").read_text(encoding="utf-8"))
assert set(example) == {
    "tvs", "turnover", "timezone", "smoobu_apartment_id", "bookings_file",
    "weather", "mountain", "bus", "webcams", "photos",
}, example  # fmt: skip
assert example["turnover"] is False  # the cleanup after departure is opt-in
ok("config: missing = {}, broken/not an object = clear error, example has every field")

# ---------- Guest phases ----------
A = booking(1, "2026-11-01", "2026-11-06")
B = booking(2, "2026-11-06", "2026-11-09")

checks = [
    ("mid-stay", [A], at(2026, 11, 3, 20), ("1", "active")),
    ("departure day 10:59", [A], at(2026, 11, 6, 10, 59), ("1", "active")),
    ("departure day 11:00 = vacancy", [A], at(2026, 11, 6, 11), None),
    ("turnover 10:00 = departing guest", [A, B], at(2026, 11, 6, 10), ("1", "active")),
    ("turnover 12:00 = arriving guest", [A, B], at(2026, 11, 6, 12), ("2", "arrival")),
    ("arrival day 09:00 = vacancy", [B], at(2026, 11, 6, 9), None),
    ("arrival tomorrow = arrival", [B], at(2026, 11, 5, 15), ("2", "arrival")),
    # one-night bookings back to back, the API lists "tomorrow" before "today"
    (
        "today before tomorrow, whatever the order",
        [booking(7, "2026-11-08", "2026-11-09"), booking(6, "2026-11-07", "2026-11-08")],
        at(2026, 11, 7, 17),
        ("6", "arrival"),
    ),
    (
        "arrival day 09:00, next guest tomorrow = vacancy",
        [booking(6, "2026-11-07", "2026-11-08"), booking(7, "2026-11-08", "2026-11-09")],
        at(2026, 11, 7, 9),
        None,
    ),
    (
        "cancellation does not count",
        [booking(3, "2026-11-01", "2026-11-06", type="cancellation")],
        at(2026, 11, 3, 12),
        None,
    ),
    (
        "blocked booking does not count",
        [booking(4, "2026-11-01", "2026-11-06", **{"is-blocked-booking": True})],
        at(2026, 11, 3, 12),
        None,
    ),
    (
        "summer time departure day 10:30",
        [booking(5, "2026-07-10", "2026-07-15")],
        at(2026, 7, 15, 10, 30),
        ("5", "active"),
    ),
    ("summer time departure day 11:30", [booking(5, "2026-07-10", "2026-07-15")], at(2026, 7, 15, 11, 30), None),
    (
        "junk entries in a hand-written bookings file are skipped, the real booking still counts",
        ["junk", None, 5, booking(8, "2026-11-01", "2026-11-06")],
        at(2026, 11, 3, 20),
        ("8", "active"),
    ),
]
fails = 0
for name, bookings, now, want in checks:
    try:
        got = phase(bookings, now)
    except Exception as exc:  # a crash here means no state.json and no cleanup after departure
        got = f"crash: {exc!r}"
    if got != want:
        print(f"FAIL {name}: {got} != {want}")
        fails += 1
print(f"ok: {len(checks) - fails}/{len(checks)} phase cases")

# Time zone from config.json with DST: Zurich is UTC+2 in summer, UTC+1 in winter (a fixed +1 was 1 h off in summer)
assert TZ.key == "Europe/Zurich"
assert TZ.utcoffset(datetime(2026, 7, 15, 12)).total_seconds() == 7200
assert TZ.utcoffset(datetime(2026, 1, 15, 12)).total_seconds() == 3600

# Expiry: the previous guest never shows during vacancy
assert sb.guest_expired({"departure": "2026-11-06"}, at(2026, 11, 6, 11))
assert not sb.guest_expired({"departure": "2026-11-06"}, at(2026, 11, 6, 10, 59))
assert sb.guest_expired({"departure": ""}, at(2026, 11, 6, 9))
assert not sb.guest_expired({"departure": "2026-11-07"}, at(2026, 11, 6, 23))

# Name: empty is allowed (the app greets without a name), last name only is fine
assert sb.guest_from({"firstname": "", "lastname": ""}, "active")["name"] == ""
assert sb.guest_from({"firstname": None, "lastname": "Smit"}, "active")["name"] == "Smit"

# Wi-Fi: an empty/unreadable wlan.env keeps the last state, real values win
_prev = {"wlan": {"ssid": "GuestNet", "password": "pw"}}
assert sb.wlan_from({}, _prev) == {"ssid": "GuestNet", "password": "pw"}
assert sb.wlan_from({"WLAN_SSID": "New", "WLAN_PASSWORD": "x"}, _prev) == {"ssid": "New", "password": "x"}
assert sb.wlan_from({}, {}) == {"ssid": "", "password": ""}

# WIFI QR: reserved characters escaped
assert sb.wifi_payload("a;b", 'p:w,"x\\') == 'WIFI:S:a\\;b;T:WPA;P:p\\:w\\,\\"x\\\\;;'

# QR as a data: URI (only when qrencode is installed)
if shutil.which("qrencode"):
    uri = sb.qr_data_uri("https://example.com/contact")
    assert uri and uri.startswith("data:image/png;base64,"), uri
    content = {
        "help": {
            "contact": {"whatsapp": "https://wa.me/1"},
            "guestcard": {"url": "https://x.example"},
            "faq": {"en": [{"q": "a", "a": "b", "url": "https://v.example"}, {"q": "c", "a": "d"}]},
        }
    }
    qr = sb.build_qr({"ssid": "GuestNet", "password": "pw"}, content)
    assert qr["wlan"] and qr["whatsapp"] and qr["guestcard"]
    assert content["help"]["faq"]["en"][0]["qr"].startswith("data:image/png")
    assert "qr" not in content["help"]["faq"]["en"][1]
    print("ok: QR codes as data: URIs")
else:
    print("skip: qrencode missing (QR test)")

# CORS: exact host instead of a prefix
assert serve.origin_allowed("null")
assert serve.origin_allowed("http://127.0.0.1:18731")
assert serve.origin_allowed("http://localhost")
assert not serve.origin_allowed("http://127.0.0.1.evil.example")
assert not serve.origin_allowed("http://192.168.1.50")
assert not serve.origin_allowed("https://127.0.0.1")
print("ok: CORS filter")

# ---------- Public sources (offline: http_get replaced) ----------
real_get, real_log, pages = sb.http_get, sb.log, {}


def fake_get(url, limit=2_000_000, timeout=15):
    v = pages[url]
    if isinstance(v, Exception):
        raise v
    return v


def down(url, limit=0, timeout=0):
    raise OSError("down")


VIA = ["Village, Cable Car", "Lakeside", "Valley, Station"]
BOARD = {
    "stationboard": [
        {
            "stop": {"departure": "2026-11-02T21:20:00+0100"},
            "number": "81",
            "to": "Town, Bus Station",
            "passList": [{"station": {"name": None}}, {"station": {"name": "Village, Cable Car"}}],
        },
        {"stop": {"departure": None}, "number": "81", "to": "x"},  # broken: skipped
        {
            "stop": {"departure": "2026-11-02T21:35:00+0100"},
            "number": "81",
            "to": "Hill Village, Center",
            "passList": [{"station": {"name": "Valley, Station"}}, {"station": {"name": "Lakeside"}}],
        },
    ]
}
POINTS = [
    {"name": "Lower Station", "lat": 45.1, "lon": 6.1, "elev": 1400},
    {"name": "Middle Station", "lat": 45.2, "lon": 6.2, "elev": 2100},
    {"name": "Summit", "lat": 45.3, "lon": 6.3, "elev": 2500},
]
WEATHER = {
    "current": {"temperature_2m": 3.2, "weather_code": 3, "wind_speed_10m": 11.0},
    "daily": {
        "time": ["2026-11-02", "2026-11-03"],
        "temperature_2m_min": [-1.4, 0.6],
        "temperature_2m_max": [4.4, 6.4],
        "weather_code": [3, 61],
        "precipitation_probability_max": [10, None],
        "wind_speed_10m_max": [12.3, 20.0],
    },
}
CAMS = [
    {"id": "valley", "name": "Valley", "elev": 1100, "url": "https://cam.example/valley.jpg"},
    {"id": "lake", "name": "Lake", "elev": 1020, "url": "https://cam.example/lake.jpg"},
    {"id": "summit-2", "name": "Summit", "elev": 2500, "url": "https://cam.example/summit.jpg"},
]
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
mtn = [{"current": {"temperature_2m": t, "weather_code": 1, "wind_speed_10m": 3}} for t in (-1.7, -4.3, -4.2)]

sb.http_get = fake_get
try:
    pages[f"{sb.TRANSPORT}?id=1234567&limit=40"] = (json.dumps(BOARD).encode(), None)
    bus = sb.fetch_bus("1234567", VIA)
    assert [(b["time"], b["to"], b["via"]) for b in bus] == [
        ("21:20", "Town, Bus Station", ["Village, Cable Car"]),
        ("21:35", "Hill Village, Center", ["Lakeside", "Valley, Station"]),  # in the configured order
    ], bus
    assert bus[0]["ts"] == 1793650800000  # 21:20 CET = 20:20 UTC
    pages[f"{sb.TRANSPORT}?id=1234567&limit=40"] = (b'{"stationboard": []}', None)
    assert sb.fetch_bus("1234567", VIA) is None

    pages.clear()

    def mtn_get(url, limit=2_000_000, timeout=15):
        assert "elevation=1400,2100,2500" in url and "latitude=45.1,45.2,45.3" in url, url
        assert "timezone=Europe/Zurich" in url, url  # time zone from config.json
        return json.dumps(mtn).encode(), None

    sb.http_get = mtn_get
    assert [(m["name"], m["temp"]) for m in sb.fetch_mountain(POINTS)] == [
        ("Lower Station", -1.7),
        ("Middle Station", -4.3),
        ("Summit", -4.2),
    ]
    mtn[0]["daily"] = {"time": ["2026-11-02", "2026-11-03"], "temperature_2m_max": [3.4, None],
                       "temperature_2m_min": [-2.6, -3.1], "weather_code": [3, 71]}  # fmt: skip
    days = sb.fetch_mountain(POINTS)[0]["days"]
    assert days == [{"date": "2026-11-02", "min": -3, "max": 3, "code": 3},
                    {"date": "2026-11-03", "min": -3, "max": None, "code": 71}], days  # fmt: skip  # missing = None
    assert sb._num([1.6], 3) is None and sb._num(None, 0) is None and sb._num([2.5], 0) == 2
    mtn[1]["current"]["temperature_2m"] = None
    assert sb.fetch_mountain(POINTS) is None  # never show partial values
    mtn[1]["current"]["temperature_2m"] = -4.3

    # Weather: Open-Meteo for the configured spot; mountain only with weather; failed fetch keeps the last state
    def weather_get(url, limit=2_000_000, timeout=15):
        if "elevation=" in url:
            return json.dumps(mtn).encode(), None
        assert url.startswith(sb.OPEN_METEO) and "latitude=45.0" in url and "longitude=6.0" in url, url
        return json.dumps(WEATHER).encode(), None

    sb.http_get = weather_get
    now = at(2026, 11, 2, 21, 30)
    w = sb.fetch_weather(now, 45.0, 6.0)
    assert w["current"] == {"temp": 3.2, "code": 3, "wind": 11.0}, w
    assert w["today"] == {"date": "2026-11-02", "min": -1, "max": 4, "code": 3, "rain": 10, "wind": 12}, w
    assert w["days"] == [{"date": "2026-11-03", "min": 1, "max": 6, "code": 61, "rain": None, "wind": 20}], w
    logs = []
    sb.log = logs.append
    assert sb.build_weather({}, now, w) is None and logs == ["weather: not configured, skipped"]
    assert sb.build_weather({"weather": {"lat": None, "lon": None}}, now, w) is None  # as in config.example.json
    spot = {"weather": {"lat": 45.0, "lon": 6.0}}
    built = sb.build_weather({**spot, "mountain": POINTS}, now, None)
    assert [m["name"] for m in built["mountain"]] == ["Lower Station", "Middle Station", "Summit"], built
    assert built["mtnUpdated"] == now.isoformat(timespec="minutes") and built["today"] == w["today"]
    sb.http_get = down
    later = at(2026, 11, 2, 22, 30)
    assert sb.build_weather({**spot, "mountain": POINTS}, later, built) == built  # all down: last state stays
    logs.clear()
    assert sb.build_weather(spot, later, built) == {**built, "mountain": None, "mtnUpdated": None}  # mountain removed
    assert "mountain: not configured, skipped" in logs

    # Config entries: bad mountain points and webcams are skipped with a log line, never a crash
    logs.clear()
    bad_points = [{"name": "No elevation", "lat": 45.0, "lon": 6.0}, {"name": "Text", "lat": "45", "lon": 6, "elev": 1}]
    assert sb.valid_entries(POINTS + bad_points, "mountain", sb.mountain_ok) == POINTS
    bad_cams = [
        {"id": "../etc", "name": "x", "elev": 1, "url": "https://x.example/a.jpg"},
        {"id": "Valley", "name": "x", "elev": 1, "url": "https://x.example/a.jpg"},  # upper case
        {"id": "cam\n", "name": "x", "elev": 1, "url": "https://x.example/a.jpg"},
        {"id": "", "name": "x", "elev": 1, "url": "https://x.example/a.jpg"},
        {"id": "ok", "name": "x", "elev": 1, "url": "file:///etc/passwd"},
        {"id": "ok", "name": "x", "url": "https://x.example/a.jpg"},  # no elevation
        "not an object",
    ]
    assert sb.valid_entries(CAMS + bad_cams, "webcams", sb.webcam_ok) == CAMS
    assert sb.valid_entries("not a list", "webcams", sb.webcam_ok) == []
    assert len(logs) == len(bad_points) + len(bad_cams), logs
    assert all(line.startswith(("mountain: skipping bad entry", "webcams: skipping bad entry")) for line in logs), logs
    sb.log = real_log

    sb.CAM_DIR = TMP / "cams"
    cam_pages = {c["url"]: (JPEG, "Mon, 02 Nov 2026 16:31:00 GMT") for c in CAMS}
    sb.http_get = lambda url, limit=0, timeout=0: cam_pages[url]
    cams = sb.fetch_cams(now, None, CAMS)
    assert len(cams) == len(CAMS) and cams[1]["updated"] == "2026-11-02T17:31+01:00", cams
    assert cams[0] == {
        "id": "valley",
        "name": "Valley",
        "elev": 1100,
        "img": "cams/valley.jpg",
        "updated": cams[0]["updated"],
    }
    assert (sb.CAM_DIR / "valley.jpg").read_bytes() == JPEG
    cam_pages[CAMS[0]["url"]] = (b"<html>404</html>", None)  # not a JPEG: old image and old time stay
    again = sb.fetch_cams(at(2026, 11, 2, 22, 30), cams, CAMS)
    assert again[0]["updated"] == cams[0]["updated"] and (sb.CAM_DIR / "valley.jpg").read_bytes() == JPEG
    cam_pages[CAMS[1]["url"]] = (JPEG, "no date")  # broken Last-Modified: fetch time, the entry stays
    sb.CAM_DIR = TMP / "cams2"
    fresh = sb.fetch_cams(at(2026, 11, 2, 23, 5), None, CAMS)
    assert [c["id"] for c in fresh] == [c["id"] for c in CAMS[1:]], fresh
    assert fresh[0]["updated"] == "2026-11-02T23:05+01:00", fresh
    # a camera without a set clock (1970) or with a clock in the future: fetch time instead
    for stamp in ("Thu, 01 Jan 1970 00:00:00 GMT", "Fri, 01 Jan 2100 00:00:00 GMT"):
        cam_pages[CAMS[1]["url"]] = (JPEG, stamp)
        odd = sb.fetch_cams(at(2026, 11, 2, 23, 10), None, CAMS)
        assert odd[0]["updated"] == "2026-11-02T23:10+01:00", (stamp, odd)
    ok("public sources (departures, weather, mountain weather, webcams, config entries) checked offline")
finally:
    sb.http_get, sb.log = real_get, real_log

# ---------- End to end: main() with the bookings fixture, no network ----------
fixture = json.loads((ROOT / "test/fixtures/bookings.json").read_text(encoding="utf-8"))
shift = datetime.now(TZ).date() - date(2030, 1, 10)  # the fixture's "today": guest 90002 is mid-stay
for b in fixture["bookings"]:
    for k in ("arrival", "departure"):
        b[k] = (date.fromisoformat(b[k]) + shift).isoformat()
(config.HOME / "bookings.json").write_text(json.dumps(fixture), encoding="utf-8")
sb.WWW = TMP / "www"
sb.STATE_OUT, sb.CAM_DIR = sb.WWW / "state.json", sb.WWW / "cams"
sb.CONTENT_FILE = TMP / "content.json"
sb.CONTENT_FILE.write_text(json.dumps({"help": {"faq": {"en": [{"q": "Q", "a": "A"}]}}}), encoding="utf-8")
sb.WLAN_ENV = TMP / "wlan.env"
sb.WLAN_ENV.write_text("WLAN_SSID=GuestNet\nWLAN_PASSWORD=secret-pw\n", encoding="utf-8")
requested, smoobu_calls, logs = [], [], []


def no_network(url, limit=0, timeout=0):
    requested.append(url)
    raise OSError("no network in tests")


def fake_smoobu(today, apartment_id):
    smoobu_calls.append(apartment_id)
    return None  # Smoobu down


def run(cfg):
    config.CONFIG_FILE.write_text(json.dumps({"timezone": "Europe/Zurich", "turnover": False, **cfg}), encoding="utf-8")
    logs.clear()
    assert sb.main() == 0
    return json.loads(sb.STATE_OUT.read_text(encoding="utf-8"))


real_smoobu = sb.fetch_smoobu
sb.http_get, sb.fetch_smoobu, sb.log = no_network, fake_smoobu, logs.append
try:
    # 1. Only a bookings file (relative to ~/guest-tv): the guest, nothing else, no request at all
    state = run({"bookings_file": "bookings.json"})
    g = state["guest"]
    assert (g["reservationId"], g["name"], g["language"], g["phase"]) == ("90002", "Anna Example", "nl", "active"), g
    assert g["departure"] == (date(2030, 1, 12) + shift).isoformat()
    assert set(state) == {"updated", "guest", "wlan", "qr", "weather", "bus", "cams", "photos", "content"}, state
    assert "lifts" not in state
    assert state["weather"] is None and state["bus"] is None and state["cams"] == [] and state["photos"] == []
    assert state["wlan"] == {"ssid": "GuestNet", "password": "secret-pw"} and state["content"]["help"]["faq"]["en"]
    assert requested == [] and smoobu_calls == [], (requested, smoobu_calls)
    assert [line.split(":")[0] for line in logs if "not configured" in line] == ["weather", "bus", "webcams"], logs

    # 2. Every part configured, sources stubbed
    def net(url, limit=2_000_000, timeout=15):
        requested.append(url)
        if url.startswith(sb.TRANSPORT):
            return json.dumps(BOARD).encode(), None
        if url.startswith(sb.OPEN_METEO):
            return json.dumps(mtn[0] if "elevation=" in url else WEATHER).encode(), None  # one point = an object
        return JPEG, "Mon, 02 Nov 2026 16:31:00 GMT"

    sb.http_get = net
    state = run(
        {
            "bookings_file": str(config.HOME / "bookings.json"),
            "weather": {"lat": 45.0, "lon": 6.0},
            "mountain": POINTS[:1],
            "bus": {"station_id": "1234567", "via": ["Lakeside"]},
            "webcams": CAMS[:1] + bad_cams[:1],
            "photos": ["assets/img/photo-1.jpg", 7],
        }
    )
    assert state["guest"]["reservationId"] == "90002" and "lifts" not in state
    assert state["weather"]["current"]["temp"] == 3.2 and state["weather"]["mtnUpdated"] == state["updated"]
    assert [m["name"] for m in state["weather"]["mountain"]] == ["Lower Station"], state["weather"]
    assert [b["via"] for b in state["bus"]] == [[], ["Lakeside"]], state["bus"]
    assert [c["id"] for c in state["cams"]] == ["valley"] and (sb.CAM_DIR / "valley.jpg").read_bytes() == JPEG
    assert state["photos"] == ["assets/img/photo-1.jpg"]
    assert len(requested) == 4 and any("webcams: skipping bad entry" in line for line in logs), (requested, logs)

    # 3. No booking source: vacancy (not the last guest), no Smoobu call
    sb.http_get = no_network
    state = run({})
    assert state["guest"] is None and smoobu_calls == []
    assert any(line.startswith("bookings: no source configured") for line in logs), logs

    # 4. Bookings file and Smoobu id set: the file wins
    state = run({"bookings_file": "bookings.json", "smoobu_apartment_id": "123456"})
    assert state["guest"]["reservationId"] == "90002" and smoobu_calls == []

    # 5. Only Smoobu, and Smoobu is down: the last guest stays (with the expiry check)
    state = run({"smoobu_apartment_id": 123456})
    assert smoobu_calls == ["123456"] and state["guest"]["reservationId"] == "90002", (smoobu_calls, state["guest"])
    ok("end to end: main() with the bookings fixture, no lifts, unconfigured parts skipped, file before Smoobu")
finally:
    sb.http_get, sb.fetch_smoobu, sb.log = real_get, real_smoobu, real_log

# Departures of the last 7 days for the cleanup (a Pi that was off during the whole stay never saw the guest)
recent = sb.recent_departures(
    [
        booking(10, "2026-11-01", "2026-11-04"),  # 3 days ago: counts
        booking(11, "2026-10-20", "2026-10-25"),  # too old
        booking(12, "2026-11-05", "2026-11-09"),  # still here
        booking(13, "2026-11-01", "2026-11-03", type="cancellation"),  # cancelled after the stay: may have been real
        booking(14, "2026-11-01", "2026-11-03", **{"is-blocked-booking": True}),  # the owner's block: no guest
        "junk",
    ],
    date(2026, 11, 7),
)
assert recent == [
    {"reservationId": "10", "departure": "2026-11-04"},
    {"reservationId": "13", "departure": "2026-11-03"},
], recent
ok("recent departures: last 7 days, cancelled stays too (at worst one needless cleanup), no blocks, no future ones")

# Hand-written fields of the wrong type never stop the hourly run (no state.json and no cleanup otherwise)
odd = {
    "id": 15,
    "firstname": 1,
    "lastname": ["x"],
    "language": ["de"],
    "arrival": "2026-11-01",
    "departure": "2026-11-09",
}
g = sb.current_guest([odd], date(2026, 11, 7), at(2026, 11, 7, 12))
assert g and g["name"] == "" and g["language"] == "en" and g["reservationId"] == "15", g
ok("booking fields of the wrong type: guest without a name, English, no crash")

# Smoobu goes through http_get too (overall time limit, size limit) with its signed headers
seen_calls, real_get2, real_creds = [], sb.http_get, sb.smoobu_auth.load_credentials
sb.smoobu_auth.load_credentials = lambda: ("key", "secret")
sb.http_get = lambda url, **kw: seen_calls.append((url, kw)) or (b'{"bookings": []}', None)
try:
    assert sb.fetch_smoobu(date(2026, 11, 7), "123") == {"bookings": []}
finally:
    sb.http_get, sb.smoobu_auth.load_credentials = real_get2, real_creds
assert seen_calls and "X-Signature" in seen_calls[0][1]["headers"] and "pageSize=100" in seen_calls[0][0], seen_calls
ok("Smoobu: signed request through http_get (overall time limit), 100 bookings per page")

# A hand-written booking without an id: a stable id from the dates, so guest change and cleanup still work
no_id = {"firstname": "Ana", "arrival": "2026-11-01", "departure": "2026-11-04"}
assert sb.guest_from(no_id, "active")["reservationId"] == "2026-11-01_2026-11-04"
assert sb.recent_departures([no_id], date(2026, 11, 7)) == [
    {"reservationId": "2026-11-01_2026-11-04", "departure": "2026-11-04"}
]
ok("booking without an id: stable id from arrival and departure")

# Wi-Fi file: the values exactly as bootstrap.sh writes them (edge spaces and quotes are real characters)
env = TMP / "wlan-edge.env"
env.write_text('WLAN_SSID= Guest "Net"\nWLAN_PASSWORD=pass\'word"\n# comment=x\n', encoding="utf-8")
assert sb.read_env(env) == {"WLAN_SSID": ' Guest "Net"', "WLAN_PASSWORD": "pass'word\""}, sb.read_env(env)
ok("wlan.env: values kept exactly, no trimming or quote removal")


# Overall time limit for public sources: a server that trickles bytes must not hold the hourly run
class Trickle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "100")
        self.end_headers()
        for _ in range(100):
            self.wfile.write(b"x")
            self.wfile.flush()
            time.sleep(0.05)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/cam.jpg"
t0 = time.monotonic()
try:
    sb.http_get(url, timeout=5, total=1)
    raise AssertionError("trickling server: no overall time limit")
except TimeoutError:
    pass
assert time.monotonic() - t0 < 3, time.monotonic() - t0
assert sb.http_get(url, timeout=5, total=30)[0] == b"x" * 100  # a slow but finished reply still comes through
srv.shutdown()
ok("public sources: overall time limit per request, a slow complete reply still works")

print("\nSTATE_BUILDER CHECK", "FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
