#!/bin/bash
# guest-tv: set up the Raspberry Pi. Copy the pi/ folder to the Pi and run this from inside it:
#   scp -r pi <user>@<pi>:~/guest-tv-setup
#   ssh -t <user>@<pi> 'cd ~/guest-tv-setup && sudo bash bootstrap.sh'
# Installs qrencode, copies the code to ~<user>/guest-tv, installs pi/config.json if you filled one in
# the repo copy (else config.example.json) when ~/guest-tv/config.json is missing, asks once for the
# secrets, installs the systemd units (server + hourly state builder) for <user> and runs a smoke check.
# Safe to run again: config.json and the secrets on the Pi are kept.
set -eu

TARGET_USER="${SUDO_USER:-}"
if [ "$(id -u)" -ne 0 ] || [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = root ]; then
  echo "Run it with sudo as the user the services should run as: sudo bash bootstrap.sh" >&2
  exit 1
fi
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [ -z "$TARGET_HOME" ] || [ ! -d "$TARGET_HOME" ]; then
  echo "No home directory found for $TARGET_USER" >&2
  exit 1
fi
BASE="$(cd "$(dirname "$0")" && pwd)"
DEST="$TARGET_HOME/guest-tv"
CONF="/etc/guest-tv"

echo "== 1/6 Packages =="
if ! command -v qrencode >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y qrencode
else
  echo "qrencode present"
fi

echo "== 2/6 Files to $DEST =="
mkdir -p "$DEST/www"
cp "$BASE/config.py" "$BASE/state_builder.py" "$BASE/serve.py" "$BASE/tv_settings.py" "$BASE/turnover.py" "$DEST/"
cp -r "$BASE/lib" "$BASE/content" "$BASE/deploy" "$DEST/"
if [ ! -f "$DEST/config.json" ] && [ -f "$BASE/config.json" ]; then
  cp "$BASE/config.json" "$DEST/config.json"  # filled in the repo copy beforehand (pi/config.json, ignored by git)
  echo "installed your config.json to $DEST/config.json"
elif [ ! -f "$DEST/config.json" ]; then
  cp "$BASE/config.example.json" "$DEST/config.json"
  echo "created $DEST/config.json from the example: add your TVs and location there (fields: config.py)"
fi
# Units run as $TARGET_USER: everything under $DEST belongs to that user
chown -R "$TARGET_USER:$TARGET_GROUP" "$DEST"
SMOOBU_ID="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("smoobu_apartment_id") or "")' "$DEST/config.json")" \
  || { echo "$DEST/config.json is not valid JSON: fix it and run bootstrap.sh again" >&2; exit 1; }

echo "== 3/6 Secrets in $CONF (typed here, never shown) =="
mkdir -p "$CONF"
# A blank answer (empty or only spaces) saves nothing: these files are never asked for again once they exist.
# IFS= keeps every character of a real answer: the files are read back exactly as typed.
blank() { [ -z "${1//[[:space:]]/}" ]; }
if [ -f "$CONF/secrets.env" ]; then
  echo "secrets.env exists"
elif [ -z "$SMOOBU_ID" ]; then
  echo "no smoobu_apartment_id in config.json: no Smoobu keys needed"
else
  echo "Smoobu HMAC key (SMOOBU_HMAC_KEY, empty = skip):"
  IFS= read -rs SKEY
  SSECRET=""
  if ! blank "$SKEY"; then
    echo "Smoobu HMAC secret (SMOOBU_HMAC_SECRET):"
    IFS= read -rs SSECRET
  fi
  if blank "$SKEY" || blank "$SSECRET"; then
    echo "skipped: run bootstrap.sh again to add the keys"
  else
    (umask 077 && printf 'SMOOBU_HMAC_KEY=%s\nSMOOBU_HMAC_SECRET=%s\n' "$SKEY" "$SSECRET" > "$CONF/secrets.env")
  fi
  unset SKEY SSECRET
fi
if [ -f "$CONF/wlan.env" ]; then
  echo "wlan.env exists"
else
  echo "The TV shows this Wi-Fi to the guests: use the GUEST network, never your home network."
  echo "Guest Wi-Fi name (SSID, empty = skip):"
  IFS= read -r SSID
  WPASS=""
  if ! blank "$SSID"; then
    echo "Guest Wi-Fi password:"
    IFS= read -rs WPASS
  fi
  if blank "$SSID" || blank "$WPASS"; then
    echo "skipped: run bootstrap.sh again to add the guest Wi-Fi"
  else
    (umask 077 && printf 'WLAN_SSID=%s\nWLAN_PASSWORD=%s\n' "$SSID" "$WPASS" > "$CONF/wlan.env")
  fi
  unset SSID WPASS
fi
for f in secrets.env wlan.env; do
  if [ -f "$CONF/$f" ]; then
    chown "root:$TARGET_GROUP" "$CONF/$f"  # the services read the secrets, they never write them
    chmod 0640 "$CONF/$f"
  fi
done

echo "== 4/6 systemd units for $TARGET_USER =="
for unit in guest-tv.service guest-tv-state.service guest-tv-state.timer; do
  sed -e "s|__USER__|$TARGET_USER|g" -e "s|__HOME__|$TARGET_HOME|g" "$BASE/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now guest-tv.service guest-tv-state.timer
systemctl restart guest-tv.service  # a running server loads the new code and TV list (serve.py, tv_settings.py)

echo "== 5/6 First state_builder run =="
systemctl start guest-tv-state.service || echo "WARNING: first run failed, see: journalctl -u guest-tv-state"

echo "== 6/6 Smoke check =="
sleep 2
curl -fsS -o /dev/null -w "state.json: HTTP %{http_code}\n" http://127.0.0.1:8090/state.json || echo "ERROR: the server does not answer"
# New code running? 127.0.0.1 is not a TV -> 403 expected (an old server without POST answers 501)
curl -s -o /dev/null -w "POST /tv/setting from the Pi itself: HTTP %{http_code} (403 expected)\n" -X POST -H "Origin: null" --data '{"op":"status"}' http://127.0.0.1:8090/tv/setting || true
ls -la "$DEST/www/" || true
echo "Bootstrap done. piUrl in app/config.js must point to this Pi (http://<pi-ip>:8090)."
echo "Wi-Fi data on the TV = the GUEST network, never your home network."
