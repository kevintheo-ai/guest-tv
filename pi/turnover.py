"""guest-tv: reset every TV after each departure.

After check-out (from 11:00) the Pi brings each TV back to its recorded normal state: wake it
(Wake-on-LAN to the Wi-Fi MAC), close the media apps, reset picture/sound/sleep/language/Home button,
log out of Netflix/YouTube/Prime (only their login data, by a fixed file list; Disney+ and Zattoo stay
logged in), reboot (proof: uptime since boot), power off. If the TV is on (picture on, picture off with
sound, or screensaver), the Pi waits and tries again every hour; from 15:00 it cleans up anyway, and
from 16:00 (check-in) not at all that day. A powered-off TV is unreachable (suspended, no ping) and gets
woken. If a step fails, the Pi powers the TV off again.

Waking needs the TV setting network.wolwowlOnOff = true. Keep Quick Start+ on (option.quickStartMode):
with it off the TV falls into deep sleep, every power-on is a cold boot, and waking took over 60 s or
failed.

  python3 turnover.py <tv-ip> normal         record the normal state from the TV now (set the TV up first)
  python3 turnover.py <tv-ip> run [--dry]    clean up right now (test); --dry only shows, changes nothing
Automatic: state_builder.py calls run_due(guest, now) after every run. It only acts with
"turnover": true in config.json; the MACs come from "tvs".
"""

import contextlib
import fcntl
import json
import os
import shlex
import socket
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import tv_settings as tvs

assert sys.version_info >= (3, 11), f"Requires Python 3.11+, got {sys.version_info[:2]}"

NORMAL_FILE = config.HOME / "tv_normal.json"
STATE_FILE = config.HOME / "turnover_state.json"
START_HOUR, FORCE_HOUR, END_HOUR = 11, 15, 16  # check-out; from here even with the TV on; check-in
KEEP_DAYS = 30

POWER = "luna://com.webos.service.tvpower/power/"
AWAKE = ("Active", "Screen Off", "Screen Saver")  # TV runs (picture on, picture off with sound, screensaver)
HBC_BUSY = "[ -e /var/luna/preferences/webosbrew_failsafe ] && echo YES || echo NO\n"  # Homebrew still starting
FOREGROUND = "luna://com.webos.applicationManager/getForegroundAppInfo"
# Normal state: what a guest typically changes. Picture values belong to the picture mode,
# so set the mode first, then the values.
PIC_NORMAL = [
    "pictureMode", "backlight", "contrast", "brightness", "sharpness", "color", "tint",
    "colorTemperature", "energySaving", "eyeComfortMode",
]  # fmt: skip
SOUND_NORMAL = ["soundMode", "soundOutput"]
APPS_DIR = "/media/cryptofs/apps/usr/palm/applications"  # overlay path: delete only here, never in the layer below
# Login data per app (sign-in tests on webOS 5.6 and 4.10): ("content", dir) empties only the folder,
# ("file", path) deletes one file. Never delete the folders themselves: the Prime folder belongs to root,
# the app cannot recreate persist/ and tmp/ and starts black.
APP_RESET = {
    "netflix": [
        ("content", "data/var/cache"),
        ("file", "data/var/activated"),
        ("file", "data/var/netflix_report"),
        ("file", "data/var/voice_routing_token"),
        ("file", "data/var/v8FlagsForNextApplicationStart"),
        ("file", "data/var/v8ScriptCacheConfig"),
        # Netflix keeps its own PlayReady store: without deleting it, Netflix stayed logged in after
        # delete + reboot. The folder belongs to the app (6784, 700), the app recreates the file.
        ("file", "data/var/dpi/playready4.4/storage/drmstore"),
        # webOS 4.10 (Netflix 1.3.5): the store is called playready3.0 there, plus the tile cookie file.
        # A path missing on a TV is skipped.
        ("file", "data/var/dpi/playready3.0/storage/drmstore"),
        ("file", "data/var/tiles/preapp-cookie"),
    ],
    "youtube.leanback.v4": [
        ("file", "content/.starboard.aHR0cHM6Ly93d3cueW91dHViZS5jb20vdHY=.storage"),
        ("content", "tmp/cobalt"),
    ],
    "amazon": [
        ("file", "plugins/com.amazon.ignition.framework.storage/var/data/buckets/secure/storage"),
        ("content", "persist"),
        ("content", "tmp"),
    ],
}
# Running app = a program from its app folder (.../applications/netflix/bin/netflix.bin, .../amazon/bin/ignition,
# .../youtube.leanback.v4/cobalt); match only the path, WebAppMgr flags contain app names. Measured on webOS 4.10
# and 5.6: the plain `ps` shows no app processes, `ps -ef` does (`|| ps` is only a fallback for a ps without -ef).
APP_PROCS = "/applications/(netflix|youtube\\.leanback\\.v4|amazon)/"

log = tvs.log
Fail = tvs.Fail


# ---------- Files ----------


def load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise Fail(500, f"{path.name} unreadable: {exc}") from None
    return data if isinstance(data, type(default)) else default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------- TV ----------


def magic_packet(mac: str) -> bytes:
    try:
        raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    except ValueError:
        raw = b""
    if len(raw) != 6:
        raise Fail(500, f"invalid MAC: {mac}")
    return b"\xff" * 6 + raw * 16


def send_wake(ip: str, mac: str) -> None:
    pkt = magic_packet(mac)
    net = ip.rsplit(".", 1)[0] + ".255"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for addr in ((net, 9), ("255.255.255.255", 9), (ip, 9)):
            s.sendto(pkt, addr)


def power_state(ip: str, run) -> str | None:
    (r,) = tvs.luna(ip, [(POWER + "getPowerState", {})], run, time.monotonic() + 20, wait=2)
    return r.get("state") if r and r.get("returnValue") is True else None


def wait_for(check, timeout: float, step: float, sleep=time.sleep) -> bool:
    end = time.monotonic() + timeout
    while True:
        try:
            if check():
                return True
        except Fail:
            pass  # TV still starting (no SSH yet)
        if time.monotonic() >= end:
            return False
        sleep(step)


def capture_normal(ip: str, run=None) -> dict:
    """Record the normal state from the TV; replaces this TV's old entry."""
    run = run or tvs.ssh_run
    pic, snd = tvs.luna(
        ip,
        [
            (tvs.GET, {"category": "picture", "keys": PIC_NORMAL}),
            (tvs.GET, {"category": "sound", "keys": SOUND_NORMAL}),
        ],
        run,
        time.monotonic() + 30,
    )

    # LG returns strings ("50") and numbers (backlight 100, contrast 85; measured on webOS 5.6): keep both
    def usable(v) -> bool:
        return (isinstance(v, str) and v != "") or (isinstance(v, int) and not isinstance(v, bool))

    picture = {k: v for k, v in tvs._settings(pic, "picture").items() if k in PIC_NORMAL and usable(v)}
    sound = {k: v for k, v in tvs._settings(snd, "sound").items() if k in SOUND_NORMAL and usable(v)}
    if "pictureMode" not in picture:
        raise Fail(502, "normal state incomplete: pictureMode missing")
    if picture.get("energySaving") == "screen_off":  # never switch the screen off
        picture["energySaving"] = "auto"
    entry = {"picture": picture, "sound": sound, "captured": datetime.now().isoformat(timespec="seconds")}
    data = load_json(NORMAL_FILE, {})
    data[ip] = entry
    save_json(NORMAL_FILE, data)
    # Align the app's baseline: its reset for the next guest would otherwise bring back the old
    # brightness/eye comfort. No entry yet: create nothing, baseline() later reads everything at once.
    with open(tvs.BASELINE_FILE.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        base = tvs._load_baselines()
        if ip in base:
            base[ip].update({k: picture[k] for k in tvs.PIC_KEYS if k in picture})
            save_json(tvs.BASELINE_FILE, base)
    log(f"{ip}: normal state saved {entry}")
    return entry


def reset_script(dry: bool) -> str:
    """Shell for the TV: closes the three apps right before (LG relaunches YouTube in the background after
    ~6 s) and aborts if one still runs; deletes only the fixed list and checks afterwards that it is gone
    (else exit 4: never report "logged out" while something is still there). A symlink anywhere on a path
    stops everything with exit 5. Dry run: closes nothing, deletes nothing, only reports running apps."""
    lines = ["set -u"]
    if not dry:
        pids = []
        for i, app in enumerate(APP_RESET):
            arg = shlex.quote(json.dumps({"id": app}, separators=(",", ":")))
            lines.append(f"luna-send -i {shlex.quote(tvs.CLOSE)} {arg} >/dev/null 2>&1 & C{i}=$!")
            pids.append(f"$C{i}")
        lines += [f"sleep 2; kill {' '.join(pids)} 2>/dev/null; wait {' '.join(pids)} 2>/dev/null; sleep 1"]
    lines.append(
        f"if {{ ps -ef 2>/dev/null || ps; }} | grep -E {shlex.quote(APP_PROCS)} | grep -v grep >/dev/null; then"
        f" echo APP-RUNNING;{'' if dry else ' exit 3;'} fi"
    )
    for app, items in APP_RESET.items():
        for kind, rel in items:
            # Never through a symlink: an app that turned a folder on this path into a link (data/var -> /var)
            # would make root delete outside the list. Every component from the app folder down must be real,
            # else exit 5 (checked with the shell's own test, no extra tool needed on the TV).
            parts = [f"{APPS_DIR}/{app}"]
            for name in rel.split("/"):
                parts.append(f"{parts[-1]}/{name}")
            lines.append(
                f"for c in {' '.join(shlex.quote(c) for c in parts)}; do "
                f'if [ -L "$c" ]; then echo {shlex.quote(f"SYMLINK {app}/{rel}")}; exit 5; fi; done'
            )
            p = shlex.quote(f"{APPS_DIR}/{app}/{rel}")
            if kind == "content":
                test, act, gone = (
                    "-d",
                    f"find {p} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +",
                    f'[ -z "$(ls -A {p})" ]',
                )
            else:
                test, act, gone = "-e", f"rm -f {p}", f"[ ! -e {p} ]"
            if dry:
                act = gone = "true"
            lines.append(
                f"if [ {test} {p} ]; then {act}; {gone} || exit 4; echo {shlex.quote(f'{kind} {app}/{rel}')}; fi"
            )
    lines.append("sync")
    return "\n".join(lines) + "\n"


def turnover(ip: str, mac: str, run=None, dry: bool = False, sleep=time.sleep, force: bool = True) -> dict:
    """TV to its normal state, flow see module docstring. Raises Fail when a step fails.
    force=False (hourly run before 15:00): if the TV is on or answers without a state, Fail 409 without
    touching anything."""
    run = run or tvs.ssh_run
    normal = load_json(NORMAL_FILE, {}).get(ip)
    if not normal:
        raise Fail(500, f"{ip}: no normal state (run: turnover.py {ip} normal)")
    steps = []
    try:
        state = power_state(ip, run)
    except Fail:  # powered off = unreachable (after power off the TV suspends: no ping, no SSH)
        state = "unreachable"
    if not force and (state is None or state in AWAKE):  # on, or a reply without a state: never disturb, wait
        raise Fail(409, f"{ip}: TV still on ({state})")
    # screensaver/picture off: a wake packet changes nothing, SSH already works. A reply without a state (None) also
    # means SSH works: from 15:00 (force) go on without waking instead of waiting for "Active" that never comes
    if state is not None and state not in AWAKE:
        if dry:
            steps.append(f"would wake (state {state})")
        else:
            send_wake(ip, mac)
            if not wait_for(lambda: power_state(ip, run) == "Active", 90, 5, sleep):
                raise Fail(503, f"{ip}: wake failed")
            steps.append("woken")
    if dry:
        steps.append(run(ip, reset_script(True), 60).strip())
        return {"ok": True, "dry": True, "steps": steps}
    try:
        reset_and_off(ip, normal, run, steps, sleep)
    except Fail:
        # never leave it awake (run_due would take it for "still on" until 15:00), but never power off while the
        # Homebrew startup still runs (a failed reboot check can land there): that boots into failsafe mode
        if state != "Active":
            with contextlib.suppress(Fail):
                if run(ip, HBC_BUSY, 15).strip() == "NO":
                    power_off(ip, run)
        raise
    return {"ok": True, "dry": False, "steps": steps}


def power_off(ip: str, run) -> None:
    tvs.luna(ip, [(POWER + "powerOff", {"reason": "remoteKey"})], run, time.monotonic() + 20, wait=3)


def portal_front(ip: str, run) -> bool:
    (r,) = tvs.luna(ip, [(FOREGROUND, {})], run, time.monotonic() + 20, wait=2)
    return bool(r) and r.get("appId") == tvs.PORTAL_ID


def is_off(ip: str, run) -> bool:
    """After powerOff: confirmed standby or unreachable (the TV goes offline right away and suspends)."""
    try:
        return power_state(ip, run) not in (None, *AWAKE)
    except Fail:
        return True


def reset_and_off(ip: str, normal: dict, run, steps: list, sleep) -> None:
    def step(text: str) -> None:
        steps.append(text)
        log(f"{ip}: {text}")

    dl = time.monotonic() + 60
    tvs.luna(ip, [(tvs.CLOSE, {"id": a}) for a in tvs.MEDIA_APPS], run, dl, wait=3)
    # Picture values are per input; after waking, the TV first shows the last HDMI input
    tvs.luna(ip, [(tvs.LAUNCH, {"id": tvs.PORTAL_ID})], run, time.monotonic() + 20, wait=3)
    if not wait_for(lambda: portal_front(ip, run), 30, 3, sleep):
        raise Fail(502, f"{ip}: portal not in front, picture not set (picture values are per input)")
    step("apps closed, portal in front")
    pic = dict(normal["picture"])
    # fresh deadlines from here: closing the apps and waiting for the portal may have used up the first one
    tvs._set(ip, [tvs._pic({"pictureMode": pic.pop("pictureMode")})], run, time.monotonic() + 30, wait=2)
    calls = [tvs._pic(pic)] if pic else []
    if normal.get("sound"):
        calls.append((tvs.SET, {"category": "sound", "settings": normal["sound"]}))
    calls.append((tvs.SET, {"category": "time", "settings": {"sleepTimer": "off"}}))
    tvs._set(ip, calls, run, time.monotonic() + 30, wait=2)
    tvs._do(ip, "lang", "reset", run, time.monotonic() + 30)  # original language (baseline), closes apps
    tvs.set_home(ip, tvs.PORTAL_ID, run, time.monotonic() + 30)
    step("normal state set")
    step(run(ip, reset_script(False), 60).strip())

    def uptime() -> float:
        out = run(ip, "cut -d' ' -f1 /proc/uptime\n", 15).split()
        try:
            return float(out[0])
        except (IndexError, ValueError):
            raise Fail(502, f"{ip}: uptime unreadable") from None

    up0, t0 = uptime(), time.monotonic()
    with contextlib.suppress(Fail):  # the session drops while the TV shuts down: only the uptime proves a reboot
        run(ip, "nohup sh -c 'sleep 2; reboot' </dev/null >/dev/null 2>&1 &\n", 15)
    step("reboot triggered")
    sleep(30)  # ask only after the shutdown

    # rebooted = uptime smaller than it would be without a reboot (before + elapsed, 5 s slack). Never compare
    # with the time since the command: the uptime starts before the kernel (webOS 4.10: ~6 s more). The boot
    # id does not help either, LG keeps it across a reboot.
    def back() -> bool:
        return uptime() + 5 < up0 + (time.monotonic() - t0) and power_state(ip, run) in AWAKE

    if not wait_for(back, 240, 10, sleep):
        raise Fail(503, f"{ip}: reboot not confirmed (old session or unreachable)")
    step("rebooted")
    # Wait until Homebrew has started (failsafe marker gone): a TV powered off while it is still starting
    # later boots into failsafe mode without root ("Failsafe mode" on screen, only "Reboot now" on the TV
    # helps). If it does not finish, better leave the TV on.
    if not wait_for(lambda: run(ip, HBC_BUSY, 15).strip() == "NO", 180, 5, sleep):
        step("Homebrew start not finished, TV stays on")
        return
    if not wait_for(lambda: portal_front(ip, run), 90, 10, sleep):
        log(f"{ip}: portal not in front after the reboot (powering off anyway)")
    with contextlib.suppress(Fail):  # the TV drops off the network right away, the session hangs until the timeout
        power_off(ip, run)
    # off = confirmed standby or unreachable; a reply without a state (None) does not count
    if not wait_for(lambda: is_off(ip, run), 30, 5, sleep):
        raise Fail(502, f"{ip}: power off failed")
    step("powered off")


# ---------- When ----------


def run_due(guest: dict | None, now: datetime, run=None, clock=None, departures=()) -> list[str]:
    """After every state_builder run: remember departures, clean up due TVs. Returns the log lines.
    `clock` gives the current time before each TV (default: `now` plus the time this run has taken).
    `departures` = recent departures from the bookings ({"reservationId", "departure"}): stays the Pi never saw
    because it was off the whole time get their cleanup too."""
    run = run or tvs.ssh_run
    t0 = time.monotonic()
    clock = clock or (lambda: now + timedelta(seconds=time.monotonic() - t0))
    cfg = config.load()
    if cfg.get("turnover") is not True:
        return []
    tvs_cfg = {ip: mac for ip, mac in (cfg.get("tvs") or {}).items() if ip in tvs.TVS and isinstance(mac, str)}
    st = load_json(STATE_FILE, {})
    seen, done = st.setdefault("seen", {}), st.setdefault("done", {})
    # Remember the departure as soon as it is announced (arrival): if the Pi is away during the stay it
    # would otherwise be missed; a booking cancelled later costs at most one needless cleanup in vacancy
    stays = list(departures)
    if guest and guest.get("phase") in ("arrival", "active"):
        stays.append(guest)
    for stay in stays:
        if not (isinstance(stay, dict) and stay.get("reservationId") and stay.get("departure")):
            continue
        rid, dep = str(stay["reservationId"]), str(stay["departure"])[:10]
        if seen.get(rid) != dep:  # stay extended or moved: a cleanup for the old date does not cover the new one
            done.pop(rid, None)
        seen[rid] = dep
    today, out = now.date(), []
    departed = []
    for rid, dep in seen.items():
        try:
            d = date.fromisoformat(dep)
        except ValueError:
            continue
        if d < today or (d == today and now.hour >= START_HOUR):
            departed.append(rid)
    no_guest = guest is None or guest.get("phase") == "arrival"
    for ip, mac in tvs_cfg.items():
        pending = [rid for rid in departed if ip not in done.get(rid, {})]
        if not pending:
            continue
        t = clock()  # each TV takes 2-3 minutes: check the window again before every TV, never start after 16:00
        if not (no_guest and t.date() == today and START_HOUR <= t.hour < END_HOUR):
            out.append(f"{ip}: cleanup pending for {pending}, waiting (only 11:00-16:00 without a guest)")
            continue
        try:  # before 15:00 only with the TV surely off (turnover checks it, Fail 409 = wait)
            res = turnover(ip, mac, run, force=t.hour >= FORCE_HOUR)
        except Exception as exc:  # code/config errors too: otherwise the other TVs' records would be lost
            busy = isinstance(exc, Fail) and exc.status == 409
            out.append(f"{exc}, next try in an hour" if busy else f"{ip}: cleanup failed ({exc!r})")
            continue
        for rid in pending:
            done.setdefault(rid, {})[ip] = t.isoformat(timespec="minutes")
        out.append(f"{ip}: cleaned up for {pending}: {'; '.join(res['steps'])}")
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    # Done entries expire after KEEP_DAYS; open ones stay until every TV is cleaned up (TV unplugged for weeks)
    st["seen"] = {r: d for r, d in seen.items() if d >= cutoff or any(ip not in done.get(r, {}) for ip in tvs_cfg)}
    st["done"] = {r: v for r, v in done.items() if r in st["seen"]}
    save_json(STATE_FILE, st)
    for line in out:
        log(line)
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in tvs.TVS or argv[1] not in ("normal", "run"):
        print("Usage: python3 turnover.py <tv-ip> normal | run [--dry]", file=sys.stderr)
        return 2
    ip = argv[0]
    try:
        if argv[1] == "normal":
            body = capture_normal(ip)
        else:
            mac = (config.load().get("tvs") or {}).get(ip, "")
            body = turnover(ip, mac, dry="--dry" in argv[2:])
        rc = 0
    except Fail as exc:
        body, rc = {"ok": False, "error": str(exc)}, 1
    print(json.dumps(body, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
