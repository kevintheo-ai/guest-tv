"""guest-tv: TV settings through the Pi.

The portal app may not write LG's SettingsService: third-party apps only get what the allow list
/etc/palm/settingsservice.allow grants (a read-only file; it grants e.g. localeInfo for reading only),
anything else is answered with "Access denied" by the service itself. An ACG group "settings" for the
app does not help either (the hub lets the call through, the service refuses). So the Pi writes over
root SSH to the TV that asks: through serve.py (POST /tv/setting) or on the command line:

  python3 tv_settings.py <tv-ip> status|setup|reset|inputs
  python3 tv_settings.py <tv-ip> sleep|energy|eye|lang|input <value>
  python3 tv_settings.py <tv-ip> home portal|lg

Rules:
- Fixed whitelist per action; client text never reaches the shell (payloads come from constants,
  baseline values or TV replies via json.dumps + shlex.quote; the script goes to `sh -s` over ssh stdin).
- Replies only come from `luna-send -i … & kill`: measured on webOS 4.10 and 5.6, `luna-send -n 1`
  (and `-w <ms> -i`) prints nothing. Never kill SettingsService: upstart does not restart it, settings
  stay dead until a reboot.
- Baseline values per TV (energySaving, eyeComfortMode, UI language) are read once before the first
  write and kept in ~/guest-tv/tv_baseline.json (not in www/). An existing entry is never overwritten
  automatically; extra keys in an entry are ignored.
- A language change closes the media apps afterwards (they restart in the new language).
- One lock per TV; every request ends after BUDGET seconds at the latest.
- The TV list comes from config.json ("tvs") at import: restart guest-tv.service after editing it.
"""

import copy
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import config

assert sys.version_info >= (3, 11), f"Requires Python 3.11+, got {sys.version_info[:2]}"

TVS = frozenset(config.load().get("tvs") or {})
PORTAL_ID = "com.example.guesttv"
BASELINE_FILE = config.HOME / "tv_baseline.json"
SSH_OPTS = [
    "-i", str(Path.home() / ".ssh" / "id_ed25519"),
    "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
    # dropbear regenerates the host keys on every cold boot, so no known_hosts
    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
]  # fmt: skip
REMOTE_TMP = "/tmp"
BUDGET = 20.0  # seconds per request incl. waiting for the lock (the app gives up after 25 s)
LOCK_WAIT = 5.0
SEP = "@@TVS@@"

SS = "luna://com.webos.settingsservice/"
GET, SET = SS + "getSystemSettings", SS + "setSystemSettings"
EIM = "luna://com.webos.service.eim/getAllInputStatus"
LAUNCH = "luna://com.webos.applicationManager/launch"
CLOSE = "luna://com.webos.applicationManager/closeByAppId"

LANGS = {"de": "de-DE", "en": "en-GB", "fr": "fr-FR", "it": "it-IT", "nl": "nl-NL", "pl": "pl-PL"}
# Apps keep their language until they are closed (seen with Zattoo): close them after a change
MEDIA_APPS = ("zattoo", "netflix", "amazon", "com.disney.disneyplus-prod", "youtube.leanback.v4")
# LG only accepts off/10/20/30/60/90/120/180/240 (getSystemSettingValues on webOS 5.6; "15" is rejected)
SLEEP = ("off", "30", "60", "90", "120")
# Screen brightness: app level -> LG energySaving, 1:1; never screen_off
ENERGY = {"auto": "auto", "bright": "off", "medium": "min", "dim": "med", "dark": "max"}
EYE = ("off", "on")  # eye comfort mode = eyeComfortMode only
PIC_KEYS = ["energySaving", "eyeComfortMode"]
CHOICES = {"sleep": SLEEP, "energy": tuple(ENERGY), "eye": EYE, "lang": (*LANGS, "reset")}
NO_VALUE = ("status", "reset", "inputs")
HDMI_APP = re.compile(r"com\.webos\.app\.hdmi\d")
SOUNDBAR = re.compile(r"bose|sound ?bar|\be?arc\b", re.I)

_locks: dict[str, threading.Lock] = {}


class Fail(Exception):
    """Abort with an HTTP status: 400 request, 500 Pi, 502 TV rejects/no reply, 503 TV unreachable."""

    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def log(msg: str) -> None:
    print(f"[tv_settings {datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


# ---------- Transport ----------


def ssh_run(ip: str, script: str, timeout: float) -> str:
    """Script via stdin to `sh -s` on the TV (root); returns stdout."""
    try:
        p = subprocess.run(
            ["ssh", *SSH_OPTS, f"root@{ip}", "sh", "-s"],
            input=script, capture_output=True, text=True, errors="replace", timeout=timeout,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise Fail(503, "tv timeout") from None
    except OSError as exc:
        raise Fail(503, f"ssh: {exc}") from None
    if p.returncode != 0:
        raise Fail(503, f"ssh rc={p.returncode} {p.stderr.strip()[:120]}".strip())
    return p.stdout


def script(calls: list[tuple[str, dict]], wait: int = 1) -> str:
    """All calls in parallel in ONE session. `luna-send -i` only prints its reply when it is killed;
    `wait` makes sure the reply is in the file before cat reads it."""
    f = f"{REMOTE_TMP}/tvs.$$"
    pids = " ".join(f"$P{i}" for i in range(len(calls)))
    lines = [
        f"luna-send -i {shlex.quote(uri)} {shlex.quote(json.dumps(p, separators=(',', ':')))} >{f}.{i} 2>&1 & P{i}=$!"
        for i, (uri, p) in enumerate(calls)
    ]
    lines += [f"sleep {wait}", f"kill {pids} 2>/dev/null", f"wait {pids} 2>/dev/null"]
    lines += [f"cat {f}.{i} 2>/dev/null; echo; echo {SEP}" for i in range(len(calls))]
    lines.append(f"rm -f {f}.*")
    return "\n".join(lines) + "\n"


def first_json(text: str) -> dict | None:
    """First JSON object in the text; junk before/after it is ignored."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def parse_replies(out: str, n: int) -> list[dict | None]:
    chunks = out.split(SEP)
    return [first_json(chunks[i]) if i < len(chunks) else None for i in range(n)]


def luna(ip: str, calls: list[tuple[str, dict]], run, deadline: float, wait: int = 1) -> list[dict | None]:
    left = deadline - time.monotonic()
    if left < wait + 1:
        raise Fail(503, "timeout")
    return parse_replies(run(ip, script(calls, wait), left), len(calls))


def _why(reply: dict | None) -> str:
    if reply is None:
        return "no reply"
    return str(reply.get("errorText") or reply.get("errorCode") or "rejected")[:120]


def _settings(reply: dict | None, what: str) -> dict:
    if not reply or reply.get("returnValue") is not True or not isinstance(reply.get("settings"), dict):
        raise Fail(502, f"{what}: {_why(reply)}")
    return reply["settings"]


def _set(ip: str, calls: list[tuple[str, dict]], run, deadline: float, wait: int = 1) -> None:
    for (uri, _), reply in zip(calls, luna(ip, calls, run, deadline, wait), strict=True):
        if not reply or reply.get("returnValue") is not True:
            raise Fail(502, f"{uri.rsplit('/', 1)[1]}: {_why(reply)}")


def _pic(settings: dict) -> tuple[str, dict]:
    return SET, {"category": "picture", "settings": settings}


def _ui(info) -> str | None:
    locales = info.get("locales") if isinstance(info, dict) else None
    ui = locales.get("UI") if isinstance(locales, dict) else None
    return ui if isinstance(ui, str) and ui else None


# ---------- Baseline values ----------


def _load_baselines() -> dict:
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:  # never recreate it automatically: a human has to look at it
        raise Fail(500, f"baseline file unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise Fail(500, "baseline file unreadable")
    return data


def baseline(ip: str, run, deadline: float) -> dict:
    """Baseline values of the TV; read on first need, before the portal writes anything."""
    data = _load_baselines()
    if ip in data:
        return data[ip]
    pic, loc = luna(
        ip, [(GET, {"category": "picture", "keys": PIC_KEYS}), (GET, {"keys": ["localeInfo"]})], run, deadline
    )
    pic_s = _settings(pic, "picture")
    vals = {k: pic_s.get(k) for k in PIC_KEYS}
    vals["ui"] = _ui(_settings(loc, "localeInfo").get("localeInfo"))
    missing = [k for k, v in vals.items() if not isinstance(v, str) or not v]
    if missing:  # never store partial values: "reset" would have to guess later
        raise Fail(502, f"baseline incomplete: {', '.join(missing)}")
    if vals["energySaving"] == "screen_off":  # reset must never switch the screen off
        vals["energySaving"] = "auto"
    vals["captured"] = datetime.now().isoformat(timespec="seconds")
    with open(BASELINE_FILE.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # also against a parallel CLI run (setup)
        data = _load_baselines()
        if ip not in data:
            data[ip] = vals
            tmp = BASELINE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, BASELINE_FILE)
            log(f"{ip}: baseline saved {vals}")
    return data[ip]


# ---------- Actions ----------


def inputs(ip: str, run, deadline: float) -> list[dict]:
    """HDMI inputs in use for the console tile, without soundbar/ARC."""
    (r,) = luna(ip, [(EIM, {})], run, deadline)
    if not r or r.get("returnValue") is not True or not isinstance(r.get("devices"), list):
        raise Fail(502, f"getAllInputStatus: {_why(r)}")
    arc = r.get("arcDevice")  # format never measured: port id as text, or an object with text fields
    if isinstance(arc, dict):
        arc_ids = {v for v in arc.values() if isinstance(v, str)}
    else:
        arc_ids = {arc} if isinstance(arc, str) else set()
    found = []
    for d in r["devices"]:
        if not isinstance(d, dict):
            continue
        app, label = d.get("appId"), str(d.get("label") or d.get("id") or "")
        if (
            d.get("connected") is True
            and d.get("hdmiPlugIn") is True
            and isinstance(app, str)
            and HDMI_APP.fullmatch(app)
            and d.get("id") not in arc_ids
            and app not in arc_ids
            and not SOUNDBAR.search(label)
        ):
            found.append({"label": label or app, "appId": app})
    return found


def status_of(time_s: dict, pic_s: dict) -> dict:
    """TV values as UI values; null when nothing matches (e.g. energySaving screen_off)."""
    sleep, es, eye = str(time_s.get("sleepTimer")), pic_s.get("energySaving"), pic_s.get("eyeComfortMode")
    energy = next((k for k, v in ENERGY.items() if v == es), None)
    return {
        "ok": True,
        "sleep": sleep if sleep in SLEEP else None,
        "energy": energy,
        "eye": eye if eye in EYE else None,
    }


def _do(ip: str, op: str, value, run, dl: float) -> dict:
    if op == "inputs":
        return {"ok": True, "inputs": inputs(ip, run, dl)}
    if op == "input":  # never trust the client: only an input that is in use right now
        if value not in {i["appId"] for i in inputs(ip, run, dl)}:
            raise Fail(400, "bad request")
        _set(ip, [(LAUNCH, {"id": value})], run, dl, wait=3)
        return {"ok": True}
    if op == "status":  # read-only, needs no baseline
        t, p = luna(
            ip,
            [(GET, {"category": "time", "keys": ["sleepTimer"]}), (GET, {"category": "picture", "keys": PIC_KEYS})],
            run,
            dl,
        )
        return status_of(_settings(t, "time"), _settings(p, "picture"))
    base = baseline(ip, run, dl)  # before every write
    if op == "lang":
        (r,) = luna(ip, [(GET, {"keys": ["localeInfo"]})], run, dl)
        info = _settings(r, "localeInfo").get("localeInfo")
        ui = _ui(info)
        if ui is None:
            raise Fail(502, "localeInfo unreadable")
        target = base["ui"] if value == "reset" else LANGS[value]
        if ui == target:
            return {"ok": True, "changed": False}
        new = copy.deepcopy(info)  # write the whole object back, only locales.UI changed
        new["locales"]["UI"] = target
        _set(ip, [(SET, {"settings": {"localeInfo": new}})], run, dl)
        try:  # the language is set; apps that are not running answer returnValue false, that is fine
            luna(ip, [(CLOSE, {"id": app}) for app in MEDIA_APPS], run, dl)
        except Fail as exc:
            log(f"{ip}: apps not closed ({exc})")
        return {"ok": True, "changed": True}
    if op == "sleep":
        calls = [(SET, {"category": "time", "settings": {"sleepTimer": value}})]
    elif op == "energy":
        calls = [_pic({"energySaving": ENERGY[value]})]
    elif op == "eye":
        calls = [_pic({"eyeComfortMode": value})]
    else:  # reset: picture back in ONE call, plus sleep off
        calls = [_pic({k: base[k] for k in PIC_KEYS}), (SET, {"category": "time", "settings": {"sleepTimer": "off"}})]
    _set(ip, calls, run, dl)
    return {"ok": True}


def _valid(op: str, value) -> bool:
    if op in NO_VALUE:
        return value is None
    if op == "input":
        return isinstance(value, str)
    return op in CHOICES and value in CHOICES[op]


def handle(ip: str, req, run=None) -> tuple[int, dict]:
    """One action for the TV `ip` -> (HTTP status, reply). Never raises."""
    run = run or ssh_run
    if ip not in TVS:
        return 403, {"ok": False, "error": "forbidden"}
    op = req.get("op") if isinstance(req, dict) else None
    value = req.get("value") if isinstance(req, dict) else None
    if not isinstance(op, str) or not _valid(op, value):
        return 400, {"ok": False, "error": "bad request"}
    deadline = time.monotonic() + BUDGET
    lock = _locks.setdefault(ip, threading.Lock())
    if not lock.acquire(timeout=LOCK_WAIT):
        return 503, {"ok": False, "error": "busy"}
    try:
        status, body = 200, _do(ip, op, value, run, deadline)
    except Fail as exc:
        status, body = exc.status, {"ok": False, "error": str(exc)}
    except Exception as exc:  # bug or unexpected TV reply: an answer instead of a dropped connection
        status, body = 500, {"ok": False, "error": "internal"}
        log(f"{ip} {op}: {exc!r}")
    finally:
        lock.release()
    log(f"{ip} {op} {value!r} -> {status} {body.get('error', '')}".rstrip())
    return status, body


def set_home(ip: str, app: str, run, dl: float) -> tuple[dict, bool]:
    """Home button to `app`. setSystemSettings replaces defaultApps as a WHOLE object: read it all, change
    only home, write it all back (otherwise e.g. the livetv mapping is lost)."""
    (r,) = luna(ip, [(GET, {"category": "general", "keys": ["defaultApps"]})], run, dl)
    apps = _settings(r, "defaultApps").get("defaultApps")
    if apps is None:  # key never set: nothing to keep
        apps = {}
    if not isinstance(apps, dict):
        raise Fail(502, "defaultApps unreadable")
    changed = apps.get("home") != app
    if changed:
        apps = {**apps, "home": app}
        _set(ip, [(SET, {"category": "general", "settings": {"defaultApps": apps}})], run, dl)
    return apps, changed


def setup(ip: str, run=None) -> dict:
    """Setup (tv_install.sh): save the baseline, Home button to the portal."""
    run = run or ssh_run
    dl = time.monotonic() + BUDGET
    base = baseline(ip, run, dl)
    apps, changed = set_home(ip, PORTAL_ID, run, dl)
    return {"ok": True, "baseline": base, "defaultApps": apps, "changed": changed}


HOME_APPS = {"portal": PORTAL_ID, "lg": "com.webos.app.home"}  # command line only (tv_home.sh), never over HTTP


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or argv[0] not in TVS:
        tvs = " ".join(sorted(TVS)) or f"none yet, add them to {config.CONFIG_FILE} (tvs)"
        print(
            "Usage: python3 tv_settings.py <tv-ip> status|setup|reset|inputs\n"
            "       python3 tv_settings.py <tv-ip> sleep|energy|eye|lang|input <value>\n"
            "       python3 tv_settings.py <tv-ip> home portal|lg\n"
            f"TVs: {tvs}",
            file=sys.stderr,
        )
        return 2
    ip, op = argv[0], argv[1]
    if op in ("setup", "home") and (len(argv) == 2) == (op == "setup"):
        try:
            if op == "setup":
                body = setup(ip)
            elif argv[2] in HOME_APPS:
                apps, changed = set_home(ip, HOME_APPS[argv[2]], ssh_run, time.monotonic() + BUDGET)
                body = {"ok": True, "defaultApps": apps, "changed": changed}
            else:
                raise Fail(400, "home: portal|lg")
            rc = 0
        except Fail as exc:
            body, rc = {"ok": False, "error": str(exc)}, 1
    else:
        status, body = handle(ip, {"op": op, "value": argv[2] if len(argv) == 3 else None})
        rc = 0 if status == 200 else 1
    print(json.dumps(body, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
