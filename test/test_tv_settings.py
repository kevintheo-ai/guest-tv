"""Self-check tv_settings + POST /tv/setting: whitelist, app/Pi values, payloads, baseline values,
status mapping, language (+ closing apps), inputs, setup, reply parser, shell mechanics, HTTP gates.

Run: python3 test/test_tv_settings.py   (exit 0 = ok, offline, no SSH)
"""

import atexit
import contextlib
import copy
import http.client
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi"))
import config

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="tvs-test-"))
atexit.register(shutil.rmtree, TMP, True)  # clean up after a failed assert too
TV, TV2, TV3 = "192.168.1.21", "192.168.1.22", "192.168.1.23"
config.HOME = TMP
config.CONFIG_FILE = TMP / "config.json"
config.CONFIG_FILE.write_text(
    json.dumps({"tvs": {TV: "02:00:00:00:00:01", TV2: "02:00:00:00:00:02", TV3: "02:00:00:00:00:03"}}),
    encoding="utf-8",
)
import serve  # noqa: E402
import tv_settings as ts  # noqa: E402

assert {TV, TV2, TV3} == ts.TVS  # the TV list comes from config.json
assert ts.BASELINE_FILE == TMP / "tv_baseline.json"  # next to config.json, never in www/
ts.log = lambda msg: None  # no journal lines in the test
BAD_REQ = (400, {"ok": False, "error": "bad request"})
# closed after every language change (Zattoo keeps the old language otherwise)
APPS = ["zattoo", "netflix", "amazon", "com.disney.disneyplus-prod", "youtube.leanback.v4"]
CLOSE_ALL = [("luna://com.webos.applicationManager/closeByAppId", {"id": a}) for a in APPS]

# Measured on webOS 5.6: localeInfo and getAllInputStatus
LOCALE = {
    "locales": {
        "UI": "de-DE",
        "TV": "de-DE",
        "FMT": "de-DE",
        "NLP": "de-DE",
        "STT": "de-DE",
        "AUD": "ru-RU",
        "AUD2": "fr-FR",
    },
    "clock": "locale",
    "keyboards": ["en", "de"],
    "timezone": "",
}
DEVICES = [
    {
        "id": "HDMI_1",
        "label": "HDMI 1",
        "appId": "com.webos.app.hdmi1",
        "connected": True,
        "activate": False,
        "hdmiPlugIn": False,
    },
    {
        "id": "HDMI_2",
        "label": "XBOX ONE",
        "appId": "com.webos.app.hdmi2",
        "connected": True,
        "activate": False,
        "hdmiPlugIn": True,
    },
    {
        "id": "HDMI_3",
        "label": "Bose",
        "appId": "com.webos.app.hdmi3",
        "connected": True,
        "activate": False,
        "hdmiPlugIn": True,
    },
    {
        "id": "HDMI_4",
        "label": "Nintendo Switch",
        "appId": "com.webos.app.hdmi4",
        "connected": True,
        "activate": False,
        "hdmiPlugIn": True,
    },
]


class FakeTV:
    """Answers like `luna-send -i` on the TV (with junk and separator lines) and records every call."""

    def __init__(self, **picture):
        self.store = {
            "time": {"sleepTimer": "off"},
            "picture": {
                "energySaving": "auto",
                "eyeComfortMode": "off",
                "colorTemperature": "0",
                "backlight": "80",
                **picture,
            },
            "general": {"defaultApps": {"home": "com.webos.app.home", "livetv": "com.webos.app.livetv"}},
            None: {"localeInfo": copy.deepcopy(LOCALE)},
        }
        self.devices, self.arc, self.reject, self.running = DEVICES, None, False, {"zattoo"}
        self.calls, self.sessions, self.scripts = [], [], []

    def __call__(self, ip, script, timeout):
        assert ip in ts.TVS and timeout > 0
        self.scripts.append(script)
        session, out = [], ""
        for line in script.splitlines():
            if line.startswith("luna-send "):
                tok = shlex.split(line)  # exactly how the shell on the TV splits the line
                assert tok[1] == "-i", line
                call = (tok[2], json.loads(tok[3]))
                session.append(call)
                out += "** junk {no json\n" + json.dumps(self.reply(*call)) + "\n\n" + ts.SEP + "\n"
        self.sessions.append(session)
        self.calls += session
        return out

    def reply(self, uri, p):
        method = uri.rsplit("/", 1)[1]
        if self.reject and method in ("setSystemSettings", "launch"):
            return {"method": method, "errorText": "There is No matched extended item", "returnValue": False}
        if method == "getSystemSettings":
            cat = self.store.get(p.get("category"), {})
            return {"method": method, "settings": {k: cat[k] for k in p["keys"] if k in cat}, "returnValue": True}
        if method == "setSystemSettings":
            self.store.setdefault(p.get("category"), {}).update(copy.deepcopy(p["settings"]))
            return {"method": method, "returnValue": True}
        if method == "getAllInputStatus":
            return {"devices": self.devices, "arcDevice": self.arc, "returnValue": True}
        if method == "launch":
            return {"returnValue": True, "processId": "1001"}
        if method == "closeByAppId":  # app not running: returnValue false
            return {"returnValue": True} if p["id"] in self.running else {"returnValue": False, "errorText": "no app"}
        raise AssertionError(uri)


def sets(tv):
    return [p for u, p in tv.calls if u == ts.SET]


def ok(msg):
    print(f"ok: {msg}")


# ---------- Whitelist ----------
tv = FakeTV()
bad = [
    None, [], "status", {}, {"op": "nope"}, {"op": ["status"]}, {"op": "setup"},
    {"op": "sleep"}, {"op": "sleep", "value": "15"}, {"op": "sleep", "value": 30}, {"op": "sleep", "value": ["30"]},
    {"op": "energy"}, {"op": "energy", "value": "screen_off"}, {"op": "energy", "value": "max"},
    {"op": "energy", "value": "off"}, {"op": "energy", "value": "low"}, {"op": "energy", "value": "high"},
    {"op": "eye"}, {"op": "eye", "value": True}, {"op": "eye", "value": "warm"},
    {"op": "night", "value": "on"}, {"op": "night", "value": "off"},
    {"op": "lang", "value": "es"}, {"op": "lang", "value": "de-DE"}, {"op": "reset", "value": "x"},
    {"op": "status", "value": ""}, {"op": "input"}, {"op": "input", "value": 2},
]  # fmt: skip
for req in bad:
    assert ts.handle(TV, req, run=tv) == BAD_REQ, req
assert ts.handle("192.168.1.50", {"op": "status"}, run=tv) == (403, {"ok": False, "error": "forbidden"})
assert tv.calls == []
ok(f"whitelist: {len(bad)} invalid requests (also raw LG values, unknown levels, op night) = 400, unknown IP = 403")

# ---------- App and Pi: same settings, values, order (SETTING_DEFS in app.js) ----------
app_js = (ROOT / "app/js/app.js").read_text(encoding="utf-8")
defs = {k: re.findall(r'"([^"]+)"', v) for k, v in re.findall(r'key: "(\w+)", values: \[([^\]]+)\]', app_js)}
assert defs == {
    "sleep": ["off", "30", "60", "90", "120"],
    "energy": ["auto", "bright", "medium", "dim", "dark"],
    "eye": ["off", "on"],
}, defs
assert {k: list(ts.CHOICES[k]) for k in defs} == defs
ok("app and Pi: sleep/energy/eye with the same values in the same order")

# ---------- Baseline values before the first write ----------
tv = FakeTV()
assert ts.handle(TV, {"op": "sleep", "value": "30"}, run=tv) == (200, {"ok": True})
assert tv.sessions[0] == [
    (ts.GET, {"category": "picture", "keys": ["energySaving", "eyeComfortMode"]}),  # no colorTemperature
    (ts.GET, {"keys": ["localeInfo"]}),
]
assert tv.sessions[1] == [(ts.SET, {"category": "time", "settings": {"sleepTimer": "30"}})]
stored = json.loads(ts.BASELINE_FILE.read_text(encoding="utf-8"))[TV]
want = {"energySaving": "auto", "eyeComfortMode": "off", "ui": "de-DE"}
assert {k: v for k, v in stored.items() if k != "captured"} == want, stored
ok("baseline values read and saved before the first write (without colorTemperature)")

# ---------- Payloads sleep/energy/eye/reset ----------
for v in ts.SLEEP:
    assert ts.handle(TV, {"op": "sleep", "value": v}, run=tv) == (200, {"ok": True})
    assert sets(tv)[-1] == {"category": "time", "settings": {"sleepTimer": v}}
assert ts.SLEEP == ("off", "30", "60", "90", "120")  # LG rejects "15"
tv.store["picture"].update(energySaving="screen_off", eyeComfortMode="on")  # TV changed
for ui, lg in (("auto", "auto"), ("bright", "off"), ("medium", "min"), ("dim", "med"), ("dark", "max")):
    n = len(tv.sessions)
    assert ts.handle(TV, {"op": "energy", "value": ui}, run=tv) == (200, {"ok": True})
    assert tv.sessions[n:] == [[(ts.SET, {"category": "picture", "settings": {"energySaving": lg}})]], ui
for v in ("on", "off"):  # only eyeComfortMode, colorTemperature stays untouched
    n = len(tv.sessions)
    assert ts.handle(TV, {"op": "eye", "value": v}, run=tv) == (200, {"ok": True})
    assert tv.sessions[n:] == [[(ts.SET, {"category": "picture", "settings": {"eyeComfortMode": v}})]], v
n = len(tv.sessions)
assert ts.handle(TV, {"op": "reset"}, run=tv) == (200, {"ok": True})
assert tv.sessions[n:] == [
    [
        (ts.SET, {"category": "picture", "settings": {"energySaving": "auto", "eyeComfortMode": "off"}}),
        (ts.SET, {"category": "time", "settings": {"sleepTimer": "off"}}),
    ]
]  # one session, picture in ONE call
assert json.loads(ts.BASELINE_FILE.read_text(encoding="utf-8"))[TV] == stored  # never overwritten
assert all("\nsleep 1\n" in s and "\nwait $P0" in s for s in tv.scripts)
assert "colorTemperature" not in json.dumps(tv.calls) and "screen_off" not in json.dumps(tv.calls)
ok("payloads: brightness 1:1 to energySaving, eye comfort only eyeComfortMode, reset = baseline + sleep off")

# ---------- Status ----------
tv.store["time"]["sleepTimer"] = "120"
tv.store["picture"].update(eyeComfortMode="on")
for lg, ui in (("auto", "auto"), ("off", "bright"), ("min", "medium"), ("med", "dim"), ("max", "dark")):
    tv.store["picture"]["energySaving"] = lg
    assert ts.handle(TV, {"op": "status"}, run=tv) == (200, {"ok": True, "sleep": "120", "energy": ui, "eye": "on"}), lg
tv.store["time"]["sleepTimer"] = "240"
tv.store["picture"].update(energySaving="screen_off", eyeComfortMode=True)  # unknown
assert ts.handle(TV, {"op": "status"}, run=tv) == (200, {"ok": True, "sleep": None, "energy": None, "eye": None})
del tv.store["picture"]["energySaving"], tv.store["picture"]["eyeComfortMode"]  # the TV does not return them
assert ts.handle(TV, {"op": "status"}, run=tv) == (200, {"ok": True, "sleep": None, "energy": None, "eye": None})
tv2 = FakeTV(energySaving="min", eyeComfortMode="on")  # the actual values, never mapped to the baseline
assert ts.handle(TV2, {"op": "status"}, run=tv2) == (200, {"ok": True, "sleep": "off", "energy": "medium", "eye": "on"})
assert len(tv2.sessions) == 1 and TV2 not in json.loads(ts.BASELINE_FILE.read_text(encoding="utf-8"))  # read only
ok("status: actual TV values as UI values, screen_off/unknown/missing = null, no baseline needed")

# ---------- Language (+ closing the media apps) ----------
tv = FakeTV()
n = len(tv.calls)
assert ts.handle(TV, {"op": "lang", "value": "de"}, run=tv) == (200, {"ok": True, "changed": False})
assert [u for u, _ in tv.calls[n:]] == [ts.GET]  # already the same -> nothing written, no app closed
n = len(tv.sessions)
assert ts.handle(TV, {"op": "lang", "value": "en"}, run=tv) == (200, {"ok": True, "changed": True})
want = copy.deepcopy(LOCALE)
want["locales"]["UI"] = "en-GB"
# whole object, only UI changed, no category; AFTERWARDS all 5 apps closed in ONE session (4 not running = fine)
assert tv.sessions[n:] == [
    [(ts.GET, {"keys": ["localeInfo"]})],
    [(ts.SET, {"settings": {"localeInfo": want}})],
    CLOSE_ALL,
]
assert ts.handle(TV, {"op": "lang", "value": "reset"}, run=tv) == (200, {"ok": True, "changed": True})
assert sets(tv)[-1] == {"settings": {"localeInfo": LOCALE}} and tv.sessions[-1] == CLOSE_ALL  # baseline
n = len(tv.calls)
assert ts.handle(TV, {"op": "lang", "value": "reset"}, run=tv) == (200, {"ok": True, "changed": False})
assert [u for u, _ in tv.calls[n:]] == [ts.GET]
for code, loc in {"fr": "fr-FR", "it": "it-IT", "nl": "nl-NL", "pl": "pl-PL", "en": "en-GB", "de": "de-DE"}.items():
    assert ts.handle(TV, {"op": "lang", "value": code}, run=tv)[0] == 200
    assert tv.store[None]["localeInfo"] == {**LOCALE, "locales": {**LOCALE["locales"], "UI": loc}}
    assert tv.sessions[-1] == CLOSE_ALL, code
tv.running = set(APPS)  # all running: same reply
assert ts.handle(TV, {"op": "lang", "value": "it"}, run=tv) == (200, {"ok": True, "changed": True})
tv.running = set()  # none running: every close fails, the language counts anyway
assert ts.handle(TV, {"op": "lang", "value": "nl"}, run=tv) == (200, {"ok": True, "changed": True})
assert tv.sessions[-1] == CLOSE_ALL and tv.store[None]["localeInfo"]["locales"]["UI"] == "nl-NL"


def close_breaks(ip, script, timeout):  # SSH drops exactly while closing the apps
    if "closeByAppId" in script:
        raise ts.Fail(503, "ssh rc=255")
    return tv(ip, script, timeout)


assert ts.handle(TV, {"op": "lang", "value": "pl"}, run=close_breaks) == (200, {"ok": True, "changed": True})
assert tv.store[None]["localeInfo"]["locales"]["UI"] == "pl-PL"
tv = FakeTV()
tv.reject = True  # the TV does not accept the language -> error, nothing closed
assert ts.handle(TV, {"op": "lang", "value": "en"}, run=tv)[0] == 502 and ts.CLOSE not in [u for u, _ in tv.calls]
ok("language: only locales.UI, whole object, reset = baseline; afterwards 5 apps closed, errors there do not matter")

# ---------- Inputs (console tile) ----------
tv = FakeTV()
xbox, switch = (
    {"label": "XBOX ONE", "appId": "com.webos.app.hdmi2"},
    {"label": "Nintendo Switch", "appId": "com.webos.app.hdmi4"},
)
assert ts.handle(TV, {"op": "inputs"}, run=tv) == (
    200,
    {"ok": True, "inputs": [xbox, switch]},
)  # Bose + empty HDMI 1 left out
tv.devices = [dict(d, label="HDMI 3") if d["id"] == "HDMI_3" else d for d in DEVICES]  # soundbar without a name ...
assert "com.webos.app.hdmi3" in [i["appId"] for i in ts.handle(TV, {"op": "inputs"}, run=tv)[1]["inputs"]]
for arc in ("HDMI_3", {"id": "HDMI_3", "label": "HDMI 3"}, "com.webos.app.hdmi3"):  # ... drops out via arcDevice
    tv.arc = arc
    assert ts.handle(TV, {"op": "inputs"}, run=tv)[1]["inputs"] == [dict(xbox), dict(switch)], arc
tv.devices, tv.arc = [d for d in DEVICES if d["id"] in ("HDMI_1", "HDMI_3")], None  # TV without a console
assert ts.handle(TV, {"op": "inputs"}, run=tv) == (200, {"ok": True, "inputs": []})
tv = FakeTV()
assert ts.handle(TV, {"op": "input", "value": "com.webos.app.hdmi2"}, run=tv) == (200, {"ok": True})
assert tv.calls[-1] == (ts.LAUNCH, {"id": "com.webos.app.hdmi2"}) and "\nsleep 3\n" in tv.scripts[-1]
for v in ("com.webos.app.hdmi3", "com.webos.app.hdmi1", "zattoo", "com.webos.app.hdmi2'; reboot; '"):
    n = len(tv.calls)
    assert ts.handle(TV, {"op": "input", "value": v}, run=tv) == BAD_REQ, v
    assert ts.LAUNCH not in [u for u, _ in tv.calls[n:]], v  # only read, never launched
assert not any("reboot" in s for s in tv.scripts)  # client text never reaches the shell
ok("inputs: in use, without soundbar/ARC; input only for an input that is in use right now")

# ---------- Error paths ----------
tv = FakeTV()
tv.reject = True
assert ts.handle(TV, {"op": "sleep", "value": "30"}, run=tv) == (
    502,
    {"ok": False, "error": "setSystemSettings: There is No matched extended item"},
)


def down(ip, script, timeout):
    raise ts.Fail(503, "ssh rc=255")


def boom(ip, script, timeout):
    raise RuntimeError("bug")


assert ts.handle(TV, {"op": "status"}, run=down) == (503, {"ok": False, "error": "ssh rc=255"})
assert ts.handle(TV, {"op": "status"}, run=lambda ip, script, timeout: "")[0] == 502  # no reply
assert ts.handle(TV, {"op": "status"}, run=boom) == (500, {"ok": False, "error": "internal"})
ts.LOCK_WAIT = 0.05
with ts._locks.setdefault(TV, threading.Lock()):  # a request for the same TV is running
    assert ts.handle(TV, {"op": "status"}, run=FakeTV()) == (503, {"ok": False, "error": "busy"})
ts.LOCK_WAIT, ts.BUDGET = 5.0, 1.5  # the time left is not enough for a session
assert ts.handle(TV, {"op": "status"}, run=FakeTV()) == (503, {"ok": False, "error": "timeout"})
ts.BUDGET = 20.0
ok("errors: TV rejects = 502, SSH gone = 503, bug = 500, busy = 503, time limit = 503")

# ---------- Baseline values: incomplete, screen_off, parallel setup run, broken file ----------
tv = FakeTV()
del tv.store["picture"]["eyeComfortMode"]
assert ts.handle(TV3, {"op": "eye", "value": "on"}, run=tv) == (
    502,
    {"ok": False, "error": "baseline incomplete: eyeComfortMode"},
)
assert sets(tv) == [] and TV3 not in json.loads(ts.BASELINE_FILE.read_text(encoding="utf-8"))
tv = FakeTV(energySaving="screen_off")
assert ts.handle(TV3, {"op": "reset"}, run=tv) == (200, {"ok": True})
assert sets(tv)[-2] == {
    "category": "picture",
    "settings": {"energySaving": "auto", "eyeComfortMode": "off"},
}  # never off

ts.BASELINE_FILE = TMP / "race.json"
# an entry with extra keys (here colorTemperature) still counts; colorTemperature is never written
cli = {TV: {"energySaving": "min", "eyeComfortMode": "on", "colorTemperature": "-50", "ui": "fr-FR"}}


class RaceTV(FakeTV):
    def __call__(self, ip, script, timeout):
        if not ts.BASELINE_FILE.exists():  # a setup run (CLI) saves while the server reads
            ts.BASELINE_FILE.write_text(json.dumps(cli), encoding="utf-8")
        return super().__call__(ip, script, timeout)


tv = RaceTV()
assert ts.handle(TV, {"op": "reset"}, run=tv) == (200, {"ok": True})
assert json.loads(ts.BASELINE_FILE.read_text(encoding="utf-8")) == cli  # not overwritten
assert sets(tv)[-2] == {"category": "picture", "settings": {"energySaving": "min", "eyeComfortMode": "on"}}  # counts
for op, v, setting in (("energy", "auto", {"energySaving": "auto"}), ("eye", "off", {"eyeComfortMode": "off"})):
    assert ts.handle(TV, {"op": op, "value": v}, run=tv) == (200, {"ok": True})  # the level, not the baseline
    assert sets(tv)[-1] == {"category": "picture", "settings": setting}, op
assert ts.handle(TV, {"op": "lang", "value": "reset"}, run=tv) == (200, {"ok": True, "changed": True})
assert tv.store[None]["localeInfo"]["locales"]["UI"] == "fr-FR"
assert "colorTemperature" not in json.dumps(tv.calls)

ts.BASELINE_FILE = TMP / "broken.json"
ts.BASELINE_FILE.write_text("{broken", encoding="utf-8")
tv = FakeTV()
st, body = ts.handle(TV, {"op": "reset"}, run=tv)
assert st == 500 and body["error"].startswith("baseline file unreadable") and tv.calls == [], body
assert ts.BASELINE_FILE.read_text(encoding="utf-8") == "{broken"
ok(
    "baseline values: incomplete = 502 without saving, screen_off -> auto, never overwritten, "
    "extra keys (colorTemperature) ignored, broken file = 500"
)

# ---------- setup (tv_install.sh) ----------
ts.BASELINE_FILE = TMP / "setup.json"
tv = FakeTV()
res = ts.setup(TV, run=tv)
apps = {"home": "com.example.guesttv", "livetv": "com.webos.app.livetv"}
assert res["changed"] is True and res["defaultApps"] == apps and res["baseline"]["ui"] == "de-DE", res
assert sets(tv) == [{"category": "general", "settings": {"defaultApps": apps}}]  # whole object, only home new
n = len(tv.calls)
assert ts.setup(TV, run=tv)["changed"] is False and ts.SET not in [u for u, _ in tv.calls[n:]]
ok("setup: baseline + Home button (whole defaultApps object), a second run writes nothing")

# ---------- Home button on the command line (tv_home.sh): the way back "lg" keeps the other mappings ----------
tv, real_run = FakeTV(), ts.ssh_run
ts.ssh_run = tv  # main() takes the transport from the module
try:
    assert ts.main([TV, "home", "portal"]) == 0 and ts.main([TV, "home", "lg"]) == 0
    assert tv.store["general"]["defaultApps"] == {"home": "com.webos.app.home", "livetv": "com.webos.app.livetv"}
    n = len(tv.calls)
    assert ts.main([TV, "home", "lg"]) == 0 and ts.SET not in [u for u, _ in tv.calls[n:]]  # already set
    assert ts.main([TV, "home", "x"]) == 1 and ts.main([TV, "home"]) == 1 and ts.main([TV, "setup", "x"]) == 1
    usage = io.StringIO()
    with contextlib.redirect_stderr(usage):
        assert ts.main(["192.168.1.50", "status"]) == 2  # not a configured TV: usage
    assert f"TVs: {TV} {TV2} {TV3}" in usage.getvalue(), usage.getvalue()  # lists the configured TVs
finally:
    ts.ssh_run = real_run
tv = FakeTV()
del tv.store["general"]["defaultApps"]  # key never set (TV before its setup)
assert ts.setup(TV2, run=tv)["changed"] is True
assert sets(tv)[-1] == {"category": "general", "settings": {"defaultApps": {"home": ts.PORTAL_ID}}}
assert ts.handle(TV, {"op": "home", "value": "lg"}, run=FakeTV())[0] == 400  # never over HTTP
ok("home portal|lg: only home changed, livetv stays; missing key is fine; locked over HTTP")

# ---------- Reply parser ----------
out = 'x {no json\n{"returnValue":true,"a":{"b":1}}\n\n@@TVS@@\n[1]\n** Message\n{"returnValue":false}\n@@TVS@@\n'
assert ts.parse_replies(out, 3) == [{"returnValue": True, "a": {"b": 1}}, {"returnValue": False}, None]
assert ts.parse_replies("", 2) == [None, None]
assert ts.parse_replies('{"returnValue":', 1) == [None]
ok("reply parser: junk, separator lines, missing/cut-off replies")

# ---------- Shell mechanics for real: a luna-send stand-in replies only when killed ----------
bindir, remote = TMP / "bin", TMP / "remote"
bindir.mkdir()
remote.mkdir()
stub = bindir / "luna-send"
stub.write_text('#!/bin/sh\ntrap \'printf "%s\\n" "$3"; exit 0\' TERM\nwhile :; do sleep 0.1; done\n', encoding="utf-8")
stub.chmod(0o755)
ts.REMOTE_TMP = str(remote)
calls = [(ts.GET, {"keys": ["localeInfo"]}), (ts.SET, {"settings": {"x": 'it\'s; rm -rf / $(reboot) `id` "q"'}})]
p = subprocess.run(
    ["sh", "-s"],
    input=ts.script(calls),
    capture_output=True,
    text=True,
    timeout=20,
    env={**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"},
)
assert p.returncode == 0, p.stderr
assert ts.parse_replies(p.stdout, 2) == [c[1] for c in calls], p.stdout  # order + quoting intact
assert list(remote.iterdir()) == []  # temp files gone
ts.REMOTE_TMP = "/tmp"
ok("shell mechanics: parallel, kill + wait, order, quoting, temp files deleted")

# ---------- HTTP: a real server on 127.0.0.1 ----------
ts.BASELINE_FILE = TMP / "tv_baseline.json"
www = TMP / "www"
www.mkdir()
(www / "state.json").write_text('{"guest":null}', encoding="utf-8")
serve.KioskHandler.log_message = lambda *a: None
srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(serve.KioskHandler, directory=str(www)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
ts.ssh_run = FakeTV()  # the server calls handle() without run= -> ssh_run is looked up at run time


def call(method, path, body=None, origin="null", headers_only_length=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    if headers_only_length is not None:  # a large body announced, but not sent
        c.putrequest(method, path)
        c.putheader("Origin", origin)
        c.putheader("Content-Length", str(headers_only_length))
        c.endheaders()
    else:
        c.request(
            method, path, body=body, headers={"Content-Type": "text/plain", **({"Origin": origin} if origin else {})}
        )
    r = c.getresponse()
    res = (r.status, r.getheader("Content-Type"), r.getheader("Access-Control-Allow-Origin"), r.read())
    c.close()
    return res


STATUS = b'{"op":"status"}'
assert call("POST", "/tv/setting", STATUS)[0] == 403  # 127.0.0.1 is not a TV
ts.TVS = ts.TVS | {"127.0.0.1"}
assert call("POST", "/tv/setting", STATUS, origin="http://evil.example")[0] == 403
# no Origin = refused: the app always sends one (measured on webOS 4.10 and 5), a form posted from a page in the
# TV's own browser may not, and it comes from the same TV address
assert call("POST", "/tv/setting", STATUS, origin=None)[0] == 403
assert call("POST", "/tv/setting", STATUS, origin="https://example.com")[0] == 403  # a page in the TV's browser
assert call("POST", "/tv/setting", headers_only_length=1025)[0] == 413  # without waiting for the body
assert call("POST", "/tv/setting", b" " * 1024)[0] == 400  # 1 KB is allowed, but it is not JSON
assert call("POST", "/tv/setting", b"{broken")[0] == 400
assert call("POST", "/tv/setting", b'{"op":"sleep","value":"15"}')[0] == 400
assert call("POST", "/tv/setting?x=1", STATUS)[0] == 404
st, ctype, acao, data = call("POST", "/tv/setting", STATUS)
assert (st, ctype, acao) == (200, "application/json", "null"), (st, ctype, acao)
assert json.loads(data) == {"ok": True, "sleep": "off", "energy": "auto", "eye": "off"}, data
assert call("POST", "/tv/setting", STATUS, origin="http://localhost:8000")[0] == 200  # desktop test only via 127.0.0.1
assert call("GET", "/state.json")[::2] == (200, "null")  # GET unchanged
assert call("GET", "/state.json")[3] == b'{"guest":null}'
# A client that sends half a request and then nothing must not hold a server thread forever
assert serve.KioskHandler.timeout == 30, serve.KioskHandler.timeout
serve.KioskHandler.timeout = 1
try:
    with socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10) as sock:
        sock.sendall(b"GET /state.json HTTP/1.1\r\nHost: x\r\n")  # headers never finished
        t0 = time.monotonic()
        assert sock.recv(100) == b"" and time.monotonic() - t0 < 5  # the server hangs up after its timeout
finally:
    serve.KioskHandler.timeout = 30
srv.shutdown()
assert serve.setting_allowed(TV, "null")
assert not serve.setting_allowed(TV, "http://localhost:8000")  # real TV addresses: only the app origins
# The app's origin on the TV (webOS 5: "-webos" suffix); no Origin and "file://" alone are not enough
assert serve.APP_ORIGIN == "file://com.example.guesttv-webos" and serve.APP_ORIGIN_4 == "file://com.example.guesttv"
assert serve.setting_allowed(TV, "file://com.example.guesttv-webos")
assert not serve.setting_allowed(TV, None) and not serve.setting_allowed(TV, "")
assert not serve.setting_allowed(TV, "file://") and not serve.setting_allowed(TV, "file://other.app-webos")
assert serve.origin_allowed("file://com.example.guesttv-webos")  # CORS reply header for the app
# webOS 4.10 sends the app id without "-webos" (without APP_ORIGIN_4 its POST gets 403); other apps never
assert serve.setting_allowed(TV, "file://com.example.guesttv") and serve.origin_allowed("file://com.example.guesttv")
assert not serve.setting_allowed(TV, "file://com.example.guesttv.evil") and not serve.origin_allowed("file://com.other")
assert not serve.setting_allowed(TV, "https://example.com") and not serve.setting_allowed("192.168.1.50", None)
ok("HTTP: 403 unknown IP/Origin, 413, 400, 404, 200 as JSON with CORS null, GET unchanged")

print("\nTV_SETTINGS CHECK PASSED")
