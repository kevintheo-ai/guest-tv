"""Self-check of the TV shell scripts with stand-ins for luna-send-pub and ssh (offline, no TV, no Pi).

Run: python3 test/test_scripts.py   (exit 0 = ok)
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "pi" / "deploy"
TMP = Path(tempfile.mkdtemp(prefix="scripts-test-"))
atexit.register(shutil.rmtree, TMP, True)
BIN = TMP / "bin"
BIN.mkdir()
ENV = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "HOME": str(TMP)}


def ok(msg: str) -> None:
    print(f"ok - {msg}")


def stub(name: str, body: str, shebang: str = "#!/bin/sh") -> None:
    path = BIN / name
    path.write_text(f"{shebang}\n{body}", encoding="utf-8")
    path.chmod(0o755)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=ENV, capture_output=True, text=True, timeout=60)


stub("sleep", "exec /bin/sleep 0.01\n")  # the 90-round wait loop takes a moment instead of 90 s

# --- tv_dev_install.sh: exit 0 only for the installer's "state":"installed" and no failure reply ---
INSTALLED = '{"details":{"state":"installed"},"returnValue":true}'  # the part measured on a TV: "state":"installed"


def dev_install(*replies: str) -> int:
    (TMP / "replies").write_text("".join(r + "\n" for r in replies), encoding="utf-8")
    # like the real subscription: answers, then stays until tv_dev_install.sh kills it
    stub("luna-send-pub", f'cat "{TMP}/replies"\nexec /bin/sleep 5\n')
    ipk = TMP / "app.ipk"
    ipk.write_text("ipk", encoding="utf-8")
    result = run("sh", str(DEPLOY / "tv_dev_install.sh"), str(ipk))
    assert not ipk.exists(), "the ipk is removed after every attempt"
    return result.returncode


assert dev_install('{"returnValue":true,"subscribed":true}', INSTALLED) == 0
assert dev_install('{"details":{"state":"install failed"},"returnValue":true}') == 1
# "installed" inside other words or inside a rejection is no success
assert dev_install('{"returnValue":false,"errorCode":-1,"errorText":"com.example.guesttv is not installed"}') == 1
assert dev_install('{"details":{"state":"uninstalled"},"returnValue":true}') == 1  # no result -> timeout
assert dev_install(INSTALLED, '{"returnValue":false,"errorText":"Denied method call"}') == 1  # a failure always wins
ok("tv_dev_install.sh: success only on state installed, rejections and look-alikes fail")

# --- tv_hotelmode.sh off: exit 0 only when the TV reads back the saved values ---
TV = "192.168.1.21"
BACKUP = TMP / "guest-tv" / f"hotelMode.{TV}.backup.json"
BACKUP.parent.mkdir()
STATE = TMP / "tv_hotelmode_state.json"
CALLS = TMP / "ssh_calls"
ON = {"enableHotelMode": "on", "enableMenuKey": "on", "enableLimitedMode": "on"}
OFF = {"enableHotelMode": "off", "enableMenuKey": "off", "enableLimitedMode": "off"}
# ssh stand-in: a TV with a hotelMode settings store; rc file = ssh exit code, ignore file = TV drops writes
stub(
    "ssh",
    f"""import json, os, re, sys
d = {str(TMP)!r}
open(d + "/ssh_calls", "a").write(sys.argv[-1] + "\\n")
rc = int(open(d + "/ssh_rc").read()) if os.path.exists(d + "/ssh_rc") else 0
if rc:
    sys.exit(rc)
m = re.search(r"settingsservice/(\\w+) '(.*?)' >", sys.argv[-1])
state = json.load(open(d + "/tv_hotelmode_state.json"))
sep = open(d + "/reply_sep").read() if os.path.exists(d + "/reply_sep") else ":"  # JSON spacing of the reply
if m.group(1) == "setSystemSettings":
    if not os.path.exists(d + "/ignore"):
        state.update(json.loads(m.group(2))["settings"])
        json.dump(state, open(d + "/tv_hotelmode_state.json", "w"))
    print(json.dumps({{"returnValue": True}}, separators=(",", sep)))
elif not os.path.exists(d + "/silent"):  # silent = the read gets no reply in time
    print(json.dumps({{"returnValue": True, "settings": state}}, separators=(",", sep)))
""",
    shebang="#!/usr/bin/env python3",
)


def hotelmode_off(
    backup: dict | None, *, rc: int = 0, ignore: bool = False, silent: bool = False, sep: str = ":", mode: str = "off"
):
    STATE.write_text(json.dumps(ON if mode == "off" else OFF), encoding="utf-8")
    BACKUP.write_text(json.dumps({"returnValue": True, "settings": backup}), encoding="utf-8")
    (TMP / "ssh_rc").write_text(str(rc), encoding="utf-8")
    (TMP / "reply_sep").write_text(sep, encoding="utf-8")
    for flag, on in (("ignore", ignore), ("silent", silent)):
        (TMP / flag).unlink(missing_ok=True)
        if on:
            (TMP / flag).write_text("", encoding="utf-8")
    CALLS.write_text("", encoding="utf-8")
    return run("bash", str(DEPLOY / "tv_hotelmode.sh"), TV, mode)


assert hotelmode_off(OFF).returncode == 0 and json.loads(STATE.read_text(encoding="utf-8")) == OFF
assert hotelmode_off(OFF, sep=":\t").returncode == 0  # any JSON spacing in the reply
assert hotelmode_off({"enableHotelMode": "off", "enableMenuKey": "off"}).returncode == 1  # backup without a key
assert "setSystemSettings" not in CALLS.read_text(encoding="utf-8")  # nothing half written
assert hotelmode_off(OFF, rc=255).returncode == 1  # TV not reachable
assert hotelmode_off(OFF, ignore=True).returncode == 1  # TV kept the old values
r = hotelmode_off(OFF, silent=True)  # written, but the read-back got no reply: not confirmed, not "failed"
assert r.returncode == 1 and "did not confirm" in r.stdout, r.stdout
ok("tv_hotelmode.sh off: fails loudly without a full backup, without SSH and when the TV keeps hotel mode")

# --- tv_hotelmode.sh on (the retry that tv_install.sh prints): exit 0 only when the TV reads back on/on/on ---
assert hotelmode_off(OFF, mode="on").returncode == 0 and json.loads(STATE.read_text(encoding="utf-8")) == ON
assert hotelmode_off(OFF, mode="on", ignore=True).returncode == 1  # TV stayed unlocked
assert hotelmode_off(OFF, mode="on", silent=True).returncode == 1  # no read-back: not confirmed
ok("tv_hotelmode.sh on: exit 0 only when the TV confirms the lock")

# --- every script that talks to a TV over ssh fails instead of waiting at a password prompt ---
for script in sorted(DEPLOY.glob("*.sh")):
    text = script.read_text(encoding="utf-8")
    if re.search(r"^\s*(ssh|scp)\b|\bssh \$SSH_OPTS|\bscp \$SSH_OPTS", text, re.M):
        assert "BatchMode=yes" in text, f"{script.name}: ssh without BatchMode=yes"
ok("ssh to the TVs only with BatchMode=yes (key or fail, never a password prompt)")

print("\nSCRIPTS CHECK PASSED")
