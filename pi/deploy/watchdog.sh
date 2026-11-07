#!/bin/sh
# guest-tv: daily watchdog on the Pi (checks and reports).
# Compares the critical settings stores of each TV with the wanted state.
# Repairs are deliberately not automatic.
# Usage: watchdog.sh [tv-ip ...]   (default: every TV in ~/guest-tv/config.json)
set -u
CONFIG="$HOME/guest-tv/config.json"
if [ "$#" -gt 0 ]; then
  TVS="$*"
else
  TVS=$(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1])).get("tvs") or {}))' "$CONFIG") \
    || { echo "cannot read the TV list from $CONFIG"; exit 1; }
fi
KEY="$HOME/.ssh/id_ed25519"
LOG="$HOME/guest-tv/watchdog.log"
# The TVs' host keys change on every cold boot (dropbear -R), see tv_install.sh
SSH_OPTS="-i $KEY -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

# Wanted states (grep -q per line)
WANT_TIME='"sleepTimer":"off"'
WANT_GENERAL='"homeAutoLaunch":"on"'

for TV in $TVS; do
  {
    echo "=== $(date '+%F %T') $TV ==="
    if ! ping -c 2 -W 3 "$TV" >/dev/null 2>&1; then
      echo "STATUS: unreachable (standby/deep sleep), nothing to do"
      continue
    fi
    # shellcheck disable=SC2086  # SSH_OPTS split into single arguments on purpose
    if ! ssh $SSH_OPTS "root@$TV" true 2>/dev/null; then
      echo "STATUS: SSH unreachable (check dropbear after a cold boot)"
      continue
    fi
    # shellcheck disable=SC2086
    T=$(ssh $SSH_OPTS "root@$TV" 'cat /var/luna/preferences/time 2>/dev/null' || echo "")
    # shellcheck disable=SC2086
    G=$(ssh $SSH_OPTS "root@$TV" 'cat /var/luna/preferences/general 2>/dev/null' || echo "")
    # shellcheck disable=SC2086
    H=$(ssh $SSH_OPTS "root@$TV" 'cat /var/luna/preferences/hotelMode 2>/dev/null' || echo "")
    # The app is a developer app under /media/developer (/usr/palm is read-only)
    # shellcheck disable=SC2086
    A=$(ssh $SSH_OPTS "root@$TV" 'ls /media/developer/apps/usr/palm/applications/com.example.guesttv >/dev/null 2>&1 && echo yes || echo NO')
    echo "app installed: $A"
    echo "$T" | grep -q "$WANT_TIME" && echo "sleepTimer: ok" || echo "DEVIATION sleepTimer: $T"
    echo "$G" | grep -q "$WANT_GENERAL" && echo "homeAutoLaunch: ok" || echo "DEVIATION homeAutoLaunch"
    echo "$G" | grep -q '"home":"com.example.guesttv"' && echo "defaultApps.home: portal" || echo "NOTE: defaultApps.home not on the portal yet"
    # Hotel mode (locks the settings gear and voice control); maintenance/way back: tv_hotelmode.sh <ip> off
    for KV in '"enableHotelMode":"on"' '"enableMenuKey":"on"' '"enableLimitedMode":"on"'; do
      echo "$H" | grep -qF "$KV" && echo "hotelMode $KV: ok" || echo "DEVIATION hotelMode: $KV missing"
    done
  } >> "$LOG" 2>&1
done
echo "Watchdog done, report: $LOG"
