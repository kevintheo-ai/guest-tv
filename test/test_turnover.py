"""Self-check turnover (reset the TVs after every departure): wake packet, delete list (really run in a
test folder), the flow against a simulated TV, schedule run_due.

Run: python3 test/test_turnover.py   (exit 0 = ok, offline, no SSH, no network)
"""

import atexit
import contextlib
import copy
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pi"))
import config

TMP = Path(tempfile.mkdtemp(prefix="turnover-test-"))
atexit.register(shutil.rmtree, TMP, True)
TV, MAC = "192.168.1.21", "02:00:00:00:00:01"
TV2 = "192.168.1.22"
config.HOME = TMP
config.CONFIG_FILE = TMP / "config.json"
config.CONFIG_FILE.write_text(json.dumps({"tvs": {TV: MAC, TV2: "02:00:00:00:00:02"}}), encoding="utf-8")
import turnover as to  # noqa: E402
import tv_settings as ts  # noqa: E402

assert (to.NORMAL_FILE, to.STATE_FILE, ts.BASELINE_FILE) == (
    TMP / "tv_normal.json",
    TMP / "turnover_state.json",
    TMP / "tv_baseline.json",
)  # all under config.HOME, never the real ~/guest-tv
ts.log = to.log = lambda msg: None
TZ = ZoneInfo("Europe/Zurich")
NO_SLEEP = lambda s: None  # noqa: E731


def ok(msg):
    print(f"ok: {msg}")


def write_config(cfg):
    config.CONFIG_FILE.write_text(json.dumps(cfg), encoding="utf-8")


# ---------- Wake packet ----------
pkt = to.magic_packet(MAC)
assert len(pkt) == 102 and pkt[:6] == b"\xff" * 6 and pkt[6:] == bytes.fromhex("020000000001") * 16
for bad in ("02:00:00", "02:00:00:00:00:0G"):  # too short, typo (not hex)
    try:
        to.magic_packet(bad)
        raise AssertionError(f"broken MAC accepted: {bad}")
    except to.Fail:
        pass
ok("wake packet: 6x FF + 16x MAC, broken MAC = error")

# ---------- Delete list ----------
dry, real = to.reset_script(True), to.reset_script(False)
assert "rm " not in dry and "-exec" not in dry, dry
for word in ("disney", "zattoo", "/var/lib/wam", "com.example.guesttv"):
    assert word not in real.lower(), word
deletes = [line for line in real.splitlines() if line.startswith("if [ ")]  # only the delete lines
paths = [t for line in deletes for t in shlex.split(line) if t.startswith("/")]
assert len(deletes) == sum(len(i) for i in to.APP_RESET.values()) and all(
    any(p.startswith(f"{to.APPS_DIR}/{app}/") for app in ("netflix", "youtube.leanback.v4", "amazon")) for p in paths
), paths
head = real.split("if [ ", 1)[0]  # before the first delete: close the apps, then check
assert real.splitlines()[-1] == "sync" and head.index("closeByAppId") < head.index("APP-RUNNING"), head
ok(f"delete list: only Netflix/YouTube/Prime under {to.APPS_DIR}, never Disney+/Zattoo/portal, dry run without rm")

# really run in a test folder: files gone, folders stay (only their content is emptied), everything else stays
apps = TMP / "apps"
for app, items in to.APP_RESET.items():
    for kind, rel in items:
        p = apps / app / rel
        if kind == "content":
            (p / "sub").mkdir(parents=True, exist_ok=True)
            (p / "token").write_text("x")
            (p / "sub" / "deep").write_text("x")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
keep = [apps / "netflix/data/var/dpi/drmstore", apps / "amazon/playready/datastore.hds", apps / "disney/storage"]
for k in keep:
    k.parent.mkdir(parents=True, exist_ok=True)
    k.write_text("stays")
orig_dir, orig_procs = to.APPS_DIR, to.APP_PROCS
to.APPS_DIR, to.APP_PROCS = str(apps), "zz-no-such-process-zz"
try:
    for dry_run in (True, False):
        out = subprocess.run(["sh", "-s"], input=to.reset_script(dry_run), capture_output=True, text=True, check=True)
        if dry_run:
            assert all((apps / app / rel).exists() for app, items in to.APP_RESET.items() for _, rel in items)
    for app, items in to.APP_RESET.items():
        for kind, rel in items:
            p = apps / app / rel
            if kind == "content":
                assert p.is_dir() and not any(p.iterdir()), p
            else:
                assert not p.exists(), p
    assert all(k.read_text() == "stays" for k in keep)
    assert out.stdout.count("\n") == sum(len(i) for i in to.APP_RESET.values()), out.stdout
    # delete error (read-only etc.): the script fails and never reports the file as deleted.
    # root ignores the read-only folder, so this part only runs as a normal user.
    if os.geteuid() != 0:
        f = apps / "netflix/data/var/activated"
        f.write_text("x")
        f.parent.chmod(0o555)
        try:
            out = subprocess.run(["sh", "-s"], input=to.reset_script(False), capture_output=True, text=True)
        finally:
            f.parent.chmod(0o755)
        assert out.returncode != 0 and "netflix/data/var/activated" not in out.stdout, (out.returncode, out.stdout)
    else:
        print("skipped as root: delete error on a read-only folder")
    # A folder on the path turned into a symlink (an app could do that to its own data): root must not follow it
    apps2, outside = TMP / "apps2", TMP / "outside"
    (apps2 / "youtube.leanback.v4").mkdir(parents=True)
    outside.mkdir()
    (outside / "precious").write_text("stays")
    (apps2 / "youtube.leanback.v4" / "tmp").symlink_to(outside)
    to.APPS_DIR = str(apps2)
    for dry_run in (True, False):
        out = subprocess.run(["sh", "-s"], input=to.reset_script(dry_run), capture_output=True, text=True)
        assert out.returncode == 5 and "SYMLINK youtube.leanback.v4/tmp/cobalt" in out.stdout, (dry_run, out)
    assert (outside / "precious").read_text() == "stays"
finally:
    to.APPS_DIR, to.APP_PROCS = orig_dir, orig_procs
ok("delete list really run: files gone, folders stay empty, everything off the list (other DRM data, Disney+) kept")

# Running app = a program from its app folder (netflix/bin/netflix.bin; the plain ps shows no apps on either TV
# generation, ps -ef does). Other processes with "youtube"/"amazon" in their arguments (WebAppMgr flags) do not
# count. The script closes the apps itself right before (LG relaunches YouTube after ~6 s); if one still runs,
# exit 3. The dry run closes nothing and only reports.
empty, orig_dir = TMP / "empty", to.APPS_DIR
to.APPS_DIR = str(empty)
try:
    if shutil.which("ps") is None:  # slim containers have no ps (the TV and the Pi do)
        print("skipped: no ps on this machine, running-app check not testable here")
    for args, running in (
        []
        if shutil.which("ps") is None
        else (("--flag-youtube-amazon", False), (f"{orig_dir}/netflix/bin/netflix.bin", True))
    ):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", args])
        try:
            time.sleep(0.3)
            r = subprocess.run(["sh", "-s"], input=to.reset_script(False), capture_output=True, text=True)
            assert (r.returncode == 3 and "APP-RUNNING" in r.stdout) == running, (args, r.returncode, r.stdout)
            if running:
                r = subprocess.run(["sh", "-s"], input=to.reset_script(True), capture_output=True, text=True)
                assert r.returncode == 0 and "APP-RUNNING" in r.stdout, (r.returncode, r.stdout)
        finally:
            p.kill()
finally:
    to.APPS_DIR = orig_dir
assert "closeByAppId" in to.reset_script(False) and "closeByAppId" not in to.reset_script(True)
ok("apps closed right before deleting; a running app is recognized by its program path (ps -ef), others not")


# ---------- Flow against a simulated TV ----------
class FakeTV:
    def __init__(self, state="Active"):
        self.state, self.boot_polls, self.broken_off, self.app_running = state, 0, False, False
        # uptime since boot; LG keeps the boot id across a reboot (measured)
        self.uptime, self.reboot_noop, self.boot_state, self.no_state = 5000.0, False, "Active", False
        # measured on webOS 5.6: the SSH session hangs on reboot; after powerOff the TV is offline right away;
        # after waking the TV first shows the last HDMI input, and picture values are per input
        self.hang_reboot, self.off_unreachable, self.front, self.wrong_input = False, False, ts.PORTAL_ID, False
        # Homebrew start after the reboot: the failsafe marker stays for this many polls (powering off while it
        # starts -> later "Failsafe mode", no root); off_too_early = powered off before that
        self.hbc_busy, self.off_too_early = 0, False
        self.store = {
            # LG returns backlight/contrast as numbers, the rest as text (measured on webOS 5.6)
            "picture": {
                "pictureMode": "vivid",
                "backlight": 100,
                "contrast": 70,
                "energySaving": "off",
                "eyeComfortMode": "on",
            },
            "sound": {"soundMode": "game", "soundOutput": "tv_speaker"},
            "time": {"sleepTimer": "60"},
            "general": {"defaultApps": {"home": "com.webos.app.home", "livetv": "com.webos.app.livetv"}},
            None: {"localeInfo": {"locales": {"UI": "fr-FR", "TV": "de-DE"}}},
        }
        self.log, self.scripts = [], []

    def __call__(self, ip, script, timeout):
        assert ip == TV and timeout > 0
        self.scripts.append(script)
        if self.state == "gone":  # SSH does not answer (deep standby, Wi-Fi briefly gone)
            raise ts.Fail(503, "ssh gone")
        if "reboot" in script:
            self.log.append(("reboot",))
            if not self.reboot_noop:  # reboot_noop: command accepted, but the TV does not reboot
                # the uptime starts before the kernel (webOS 4.10: ~6 s more than the time since the command),
                # so never compare with the time since the command, only with the uptime before
                self.state, self.boot_polls, self.uptime = "booting", 2, 50.0
            if self.hang_reboot:
                raise ts.Fail(503, "tv timeout")
            return ""
        if "APP-RUNNING" in script:
            if self.app_running:
                raise ts.Fail(503, "ssh rc=3 APP-RUNNING")
            self.log.append(("reset", "rm -f" in script))
            return "file netflix/data/var/activated\n"
        if self.state == "booting":
            self.boot_polls -= 1
            if self.boot_polls < 0:
                self.state = self.boot_state  # a TV may come back in screensaver mode after the reboot
            raise ts.Fail(503, "ssh gone")
        if "webosbrew_failsafe" in script:
            busy, self.hbc_busy = self.hbc_busy > 0, max(self.hbc_busy - 1, 0)
            return "YES\n" if busy else "NO\n"
        if "/proc/uptime" in script:
            return f"{self.uptime:.2f} 100.00\n"
        out = ""
        for line in script.splitlines():
            if line.startswith("luna-send "):
                tok = shlex.split(line)
                out += json.dumps(self.reply(tok[2], json.loads(tok[3]))) + "\n" + ts.SEP + "\n"
        return out

    def reply(self, uri, p):
        m = uri.rsplit("/", 1)[1]
        self.log.append((m, copy.deepcopy(p)))
        if m == "getPowerState":
            if self.no_state or (self.broken_off and self.state == "Active Standby"):
                return {"returnValue": False, "errorText": "busy"}
            return {"returnValue": True, "state": self.state}
        if m == "powerOff":
            self.off_too_early = self.off_too_early or self.hbc_busy > 0
            self.state = "gone" if self.off_unreachable else "Active Standby"
            if self.off_unreachable:
                raise ts.Fail(503, "tv timeout")
            return {"returnValue": True}
        if m == "getForegroundAppInfo":
            return {"returnValue": True, "appId": self.front}
        if m == "launch":
            self.front = p["id"]
            return {"returnValue": True}
        if m == "closeByAppId":
            return {"returnValue": False, "errorText": "not running"}
        if m == "getSystemSettings":
            cat = self.store.get(p.get("category"), {})
            return {"returnValue": True, "settings": {k: cat[k] for k in p["keys"] if k in cat}}
        if m == "setSystemSettings":
            if p.get("category") == "picture" and self.front != ts.PORTAL_ID:
                self.wrong_input = True  # picture set on the wrong input
            self.store.setdefault(p.get("category"), {}).update(copy.deepcopy(p["settings"]))
            return {"returnValue": True}
        raise AssertionError(uri)


# Record the normal state: only the intended keys, never screen_off
tv = FakeTV()
tv.store["picture"].update(
    {"pictureMode": "cinema", "backlight": 80, "contrast": 85, "energySaving": "screen_off", "x": "1"}
)
entry = to.capture_normal(TV, run=tv)
want = {"pictureMode": "cinema", "backlight": 80, "contrast": 85, "energySaving": "auto", "eyeComfortMode": "on"}
assert entry["picture"] == want, entry["picture"]  # numbers stay numbers
assert entry["sound"] == {"soundMode": "game", "soundOutput": "tv_speaker"}
assert json.loads(to.NORMAL_FILE.read_text())[TV]["picture"]["pictureMode"] == "cinema"
assert not ts.BASELINE_FILE.exists()  # no half baseline entry without one (baseline() reads everything later)
ok("normal state: picture + sound recorded, screen_off -> auto, other keys not")

# The normal state aligns the app's baseline: its reset for the next guest would otherwise bring back the old
# brightness/eye comfort; language and the rest stay
ts.BASELINE_FILE.write_text(
    json.dumps({TV: {"energySaving": "auto", "eyeComfortMode": "off", "ui": "de-DE", "note": "n"}})
)
tv = FakeTV()
tv.store["picture"].update({"pictureMode": "cinema", "backlight": 80, "energySaving": "min", "eyeComfortMode": "on"})
to.capture_normal(TV, run=tv)
b = json.loads(ts.BASELINE_FILE.read_text())[TV]
assert b == {"energySaving": "min", "eyeComfortMode": "on", "ui": "de-DE", "note": "n"}, b
ts.BASELINE_FILE.unlink()
to.NORMAL_FILE.write_text(json.dumps({TV: entry}))  # go on with the normal state of the first test
ok("normal state aligns brightness/eye comfort in the baseline, language stays")

# The guest changed everything, TV in standby -> wake, reset, log out, reboot, off
ts.BASELINE_FILE.write_text(json.dumps({TV: {"energySaving": "auto", "eyeComfortMode": "off", "ui": "de-DE"}}))
tv = FakeTV(state="Active Standby")
tv.store["picture"].update({"pictureMode": "vivid", "backlight": 100, "contrast": 100})
woke = []
orig_wake = to.send_wake
to.send_wake = lambda ip, mac: (woke.append((ip, mac)), setattr(tv, "state", "Active"))
try:
    res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
finally:
    to.send_wake = orig_wake
names = [e[0] for e in tv.log]
assert woke == [(TV, MAC)] and res["ok"] and not res["dry"], res
assert tv.store["picture"]["pictureMode"] == "cinema" and tv.store["picture"]["backlight"] == 80
assert tv.store["picture"]["contrast"] == 85
assert tv.store["sound"]["soundOutput"] == "tv_speaker" and tv.store["time"]["sleepTimer"] == "off"
assert tv.store[None]["localeInfo"]["locales"]["UI"] == "de-DE"
assert tv.store["general"]["defaultApps"] == {"home": ts.PORTAL_ID, "livetv": "com.webos.app.livetv"}
first_mode = next(i for i, e in enumerate(tv.log) if e[0] == "setSystemSettings" and "pictureMode" in e[1]["settings"])
first_val = next(i for i, e in enumerate(tv.log) if e[0] == "setSystemSettings" and "backlight" in e[1]["settings"])
assert first_mode < first_val, "the picture mode must be set before the values"
order = [n for n in names if n in ("closeByAppId", "reset", "reboot", "powerOff")]
assert order.index("closeByAppId") < order.index("reset") < order.index("reboot") < order.index("powerOff"), order
assert ("reset", True) in tv.log and tv.state == "Active Standby"
ok("flow: wake, apps closed, picture mode before values, sound/sleep/language/home, log out, reboot, off")

# Dry run: set nothing, delete nothing, no reboot
tv = FakeTV(state="Active Standby")
res = to.turnover(TV, MAC, run=tv, dry=True, sleep=NO_SLEEP)
assert res["dry"] and "would wake" in res["steps"][0]
assert not any(e[0] in ("setSystemSettings", "reboot", "powerOff") for e in tv.log) and ("reset", False) in tv.log
ok("dry run: only shows, sets and deletes nothing")

# TV on, only screensaver/picture off: a wake packet changes nothing there, so no waking and no waiting for it,
# just clean up
woke = []
to.send_wake = lambda ip, mac: woke.append((ip, mac))
orig_wait = to.wait_for
to.wait_for = lambda check, timeout, step, sleep: orig_wait(check, 0.2, step, sleep)  # keep the waiting short
try:
    # hourly run before 15:00 (force=False): TV on (picture, picture off with sound, screensaver) or a reply
    # without a state -> touch nothing, do not wake, Fail 409 = wait; ONE power state query decides
    for state in ("Active", "Screen Off", "Screen Saver", "no state"):
        tv = FakeTV(state="Active" if state == "no state" else state)
        tv.no_state = state == "no state"
        try:
            to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP, force=False)
            raise AssertionError(f"{state}: cleaned up before 15:00")
        except to.Fail as exc:
            assert exc.status == 409, (state, exc)
        assert woke == [] and all(e[0] == "getPowerState" for e in tv.log), (state, woke, tv.log)
    # powered off = unreachable (after power off the TV suspends: no ping/SSH): wake and clean up even before
    # 15:00; after the reboot the screensaver counts as "back" too
    tv = FakeTV(state="gone")
    tv.boot_state = "Screen Saver"
    to.send_wake = lambda ip, mac: setattr(tv, "state", "Active")
    res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP, force=False)
    assert res["ok"] and "woken" in res["steps"] and tv.state == "Active Standby", (res, tv.state)
    # after the reboot power off only once Homebrew has started (failsafe marker gone); otherwise the TV later
    # boots into failsafe mode without root
    tv = FakeTV(state="Active Standby")
    tv.hbc_busy = 3
    to.send_wake = lambda ip, mac: setattr(tv, "state", "Active")
    res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
    assert res["ok"] and not tv.off_too_early and tv.state == "Active Standby", (res, tv.off_too_early)
    # a reboot only counts with a smaller uptime since boot (LG keeps the boot id): if the TV does not
    # reboot, never record "rebooted"
    tv = FakeTV(state="Active Standby")
    tv.reboot_noop = True
    try:
        to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
        raise AssertionError("recorded as rebooted without a reboot")
    except to.Fail as exc:
        assert "reboot" in str(exc) and tv.state == "Active Standby", (exc, tv.state)
    # measured on webOS 5.6: after waking the TV shows the last HDMI input, the SSH session hangs on reboot
    # and on power off (TV offline right away). Expected: portal in front before the picture (picture values
    # are per input), a dropped session is no error, "off" = unreachable afterwards.
    tv = FakeTV(state="gone")
    tv.hang_reboot = tv.off_unreachable = True
    to.send_wake = lambda ip, mac: (setattr(tv, "state", "Active"), setattr(tv, "front", "com.webos.app.hdmi2"))
    res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
    assert res["ok"] and not tv.wrong_input and tv.state == "gone", (res, tv.wrong_input, tv.state)
    assert tv.store["picture"]["pictureMode"] == "cinema", tv.store["picture"]
    # a step fails after waking: TV off again, otherwise run_due would take it for "still on" until 15:00
    tv = FakeTV(state="Active Standby")
    tv.app_running = True
    to.send_wake = lambda ip, mac: setattr(tv, "state", "Active")
    try:
        to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
        raise AssertionError("cleaned up although an app was running")
    except to.Fail:
        assert tv.state == "Active Standby" and tv.log[-1][0] == "powerOff", tv.log[-3:]
    to.send_wake = lambda ip, mac: woke.append((ip, mac))
    for state in ("Screen Saver", "Screen Off"):
        tv = FakeTV(state=state)
        res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
        assert res["ok"] and ("reset", True) in tv.log and tv.state == "Active Standby", (state, res)
    assert woke == [], woke
    # SSH does not answer (deep standby, Wi-Fi briefly gone): wake instead of giving up
    tv = FakeTV(state="gone")
    to.send_wake = lambda ip, mac: (woke.append((ip, mac)), setattr(tv, "state", "Active"))
    res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
    assert res["ok"] and woke == [(TV, MAC)] and "woken" in res["steps"], res
    # the reboot is not confirmed while Homebrew still starts: never power off then (failsafe mode, no root)
    tv = FakeTV(state="Active Standby")
    tv.boot_state, tv.hbc_busy = "Active Standby", 99
    to.send_wake = lambda ip, mac: setattr(tv, "state", "Active")
    try:
        to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
        raise AssertionError("reboot recorded without a confirmation")
    except to.Fail:
        assert not tv.off_too_early and all(e[0] != "powerOff" for e in tv.log), tv.log[-4:]
    # from 15:00 a reachable TV whose power state comes without a state is reset anyway (no wake needed, it
    # answers over SSH); the state comes back later for the reboot check
    tv = FakeTV(state="Active")
    tv.no_state = True
    plain_reply = tv.reply

    def reply_until_reset(uri, p, tv=tv, plain_reply=plain_reply):
        tv.no_state = ("reset", True) not in tv.log
        return plain_reply(uri, p)

    tv.reply = reply_until_reset
    res = to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP, force=True)
    assert res["ok"] and ("reset", True) in tv.log and "woken" not in res["steps"], res
    to.send_wake = lambda ip, mac: woke.append((ip, mac))
    # power off only counts with a confirmed standby: no reply or a negative one is not "off"
    tv = FakeTV(state="Active")
    tv.broken_off = True
    try:
        to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
        raise AssertionError("recorded as powered off without a confirmed standby")
    except to.Fail as exc:
        assert "power off" in str(exc), exc
finally:
    to.send_wake, to.wait_for = orig_wake, orig_wait
ok("screensaver/picture off: no waking; TV unreachable: wake; off only with a confirmed standby")

# No normal state -> abort before touching anything
to.NORMAL_FILE.write_text("{}")
tv = FakeTV()
try:
    to.turnover(TV, MAC, run=tv, sleep=NO_SLEEP)
    raise AssertionError("ran without a normal state")
except to.Fail:
    assert tv.log == []
to.NORMAL_FILE.write_text(json.dumps({TV: entry}))
ok("without a normal state: abort, no call to the TV")


# ---------- Schedule run_due ----------
calls, power = [], {"state": "Active Standby"}
fail_next = {"on": False}


def fake_turnover(ip, mac, run=None, force=True):
    assert callable(run), run  # run_due has to pass the SSH transport (None -> TypeError before 15:00)
    if not force and power["state"] in to.AWAKE:  # like turnover: TV on -> 409, wait (really checked above)
        raise to.Fail(409, f"{ip}: TV still on ({power['state']})")
    if fail_next["on"]:
        fail_next["on"] = False
        raise to.Fail(503, "TV gone")
    if ip != TV:  # like magic_packet/a code error: not a Fail, another exception
        raise ValueError("non-hexadecimal number found in fromhex() arg")
    calls.append((ip, mac))
    return {"ok": True, "steps": ["x"]}


to.turnover = fake_turnover


def at(day, hour):
    return datetime(2026, 11, day, hour, 5, tzinfo=TZ)


GUEST = {"reservationId": "R1", "departure": "2026-11-06", "phase": "active"}
assert to.run_due(GUEST, at(6, 10)) == [] and not to.STATE_FILE.exists()  # "turnover" missing: off
write_config({"turnover": False, "tvs": {TV: MAC}})
assert to.run_due(GUEST, at(6, 10)) == [] and not to.STATE_FILE.exists()  # switched off
write_config({"turnover": True, "tvs": {TV: MAC, "10.0.0.9": MAC}})
to.run_due(GUEST, at(6, 10))
assert calls == [] and json.loads(to.STATE_FILE.read_text())["seen"] == {"R1": "2026-11-06"}
to.run_due(None, at(6, 11))
assert calls == [(TV, MAC)], calls  # never an IP that tv_settings does not know
to.run_due(None, at(6, 12))
assert calls == [(TV, MAC)]  # already done
ok("schedule: switch off = nothing; departure remembered, cleaned up once from 11:00, only known TVs")

calls.clear()
to.run_due({"reservationId": "R2", "departure": "2026-11-08", "phase": "active"}, at(7, 20))
power["state"] = "Active"
lines = to.run_due(None, at(8, 12))
assert calls == [] and "still on" in lines[0]
to.run_due(None, at(8, 15))
assert calls == [(TV, MAC)]
ok("TV on (late check-out): wait every hour, from 15:00 anyway")

calls.clear()
to.run_due({"reservationId": "R3", "departure": "2026-11-09", "phase": "active"}, at(9, 9))
lines = to.run_due(None, at(9, 16))
assert calls == [] and "waiting" in lines[0]
nxt = {"reservationId": "R4", "departure": "2026-11-12", "phase": "arrival"}
power["state"] = "Active Standby"
fail_next["on"] = True
lines = to.run_due(nxt, at(10, 11))
assert calls == [] and "failed" in lines[0]
to.run_due(nxt, at(10, 12))
assert calls == [(TV, MAC)]
st = json.loads(to.STATE_FILE.read_text())
# remember the departure from the announcement on: if the Pi never saw the guest as "active" (Pi or Smoobu
# down during the stay, state file lost), the logins would otherwise stay for the next guest
assert TV in st["done"]["R3"] and st["seen"]["R4"] == "2026-11-12", st
ok("not from 16:00 (check-in), next guest not here yet = allowed, error -> again the next hour")

# Unexpected error on one TV (typo in the MAC, code error): the other TVs' records stay, otherwise the Pi
# would clean up the first TV again every hour and every day
calls.clear()
write_config({"turnover": True, "tvs": {TV: MAC, TV2: "broken"}})
to.run_due({"reservationId": "R5", "departure": "2026-11-13", "phase": "active"}, at(13, 9))
lines = to.run_due(None, at(13, 11))
st = json.loads(to.STATE_FILE.read_text())
assert calls == [(TV, MAC)] and list(st["done"]["R5"]) == [TV], st
assert any(TV2 in line and "failed" in line for line in lines), lines
write_config({"turnover": True, "tvs": {TV: MAC}})
ok("unexpected error on one TV: the others are cleaned up and recorded")

calls.clear()
power["state"] = "Active Standby"
to.run_due({"reservationId": "R6", "departure": "2026-11-14", "phase": "active"}, at(14, 9))
fail_next["on"] = True  # TV gone for five weeks from the departure (unplugged): every try fails
to.run_due(None, datetime(2026, 12, 20, 12, 0, tzinfo=TZ))
st = json.loads(to.STATE_FILE.read_text())
assert calls == [] and st == {"seen": {"R6": "2026-11-14"}, "done": {}}, st  # done entries gone, open ones stay
to.run_due(None, datetime(2026, 12, 20, 13, 0, tzinfo=TZ))
st = json.loads(to.STATE_FILE.read_text())
assert calls == [(TV, MAC)] and st == {"seen": {}, "done": {}}, st
ok("done entries gone after 30 days, open ones stay until the cleanup")

# Stay extended after its cleanup: the new departure needs its own cleanup, else the guest's new logins stay
calls.clear()
power["state"] = "Active Standby"
to.run_due({"reservationId": "R7", "departure": "2026-11-15", "phase": "active"}, at(15, 9))
to.run_due(None, at(15, 12))
assert calls == [(TV, MAC)], calls
to.run_due({"reservationId": "R7", "departure": "2026-11-17", "phase": "active"}, at(16, 9))  # extended
to.run_due(None, at(17, 12))
assert calls == [(TV, MAC), (TV, MAC)], calls
ok("stay extended after the cleanup: the new departure gets its own cleanup")

# The clock moves on while the TVs are reset (2-3 min each): from 16:00 (check-in) no further TV starts
started = []
real_fake = to.turnover
to.turnover = lambda ip, mac, run=None, force=True: started.append(ip) or {"ok": True, "steps": ["x"]}
write_config({"turnover": True, "tvs": {TV: MAC, TV2: MAC}})
to.run_due({"reservationId": "R8", "departure": "2026-11-18", "phase": "active"}, at(18, 9))
ticks = iter([datetime(2026, 11, 18, 15, 59, tzinfo=TZ), datetime(2026, 11, 18, 16, 1, tzinfo=TZ)])
lines = to.run_due(None, datetime(2026, 11, 18, 15, 58, tzinfo=TZ), clock=lambda: next(ticks))
assert started == [TV] and any(TV2 in line and "waiting" in line for line in lines), (started, lines)
to.turnover = real_fake
write_config({"turnover": True, "tvs": {TV: MAC}})
ok("check-in time checked again before every TV")

# Pi off for the whole stay (power cut from the day before arrival until after departure): the departure
# comes back from the bookings of the last days and still gets its cleanup
calls.clear()
power["state"] = "Active Standby"
lines = to.run_due(None, at(20, 12), departures=[{"reservationId": "R9", "departure": "2026-11-19"}])
assert calls == [(TV, MAC)], (calls, lines)
to.run_due(None, at(20, 13), departures=[{"reservationId": "R9", "departure": "2026-11-19"}])
assert calls == [(TV, MAC)], calls  # reported again every hour, cleaned up once
ok("departure missed while the Pi was off: cleaned up from the recent bookings")

# ---------- Command line ----------
real_run, ts.ssh_run = ts.ssh_run, FakeTV()  # main() takes the transport from tv_settings
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert to.main([TV, "normal"]) == 0  # records the normal state from the TV
        assert to.main([TV, "nope"]) == 2 and to.main(["10.0.0.9", "run"]) == 2  # usage
finally:
    ts.ssh_run = real_run
assert json.loads(to.NORMAL_FILE.read_text())[TV]["picture"]["pictureMode"] == "vivid"
ok("command line: normal | run, only configured TVs")

print("\nTURNOVER CHECK PASSED")
