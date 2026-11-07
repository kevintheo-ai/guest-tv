#!/bin/bash
# Runs ON THE PI: LG hotel mode on/off, as root via luna-send -i.
#   tv_hotelmode.sh <TV-IP> on|off|show
# on: first saves the complete hotelMode settings (~/guest-tv/hotelMode.<ip>.backup.json), then sets
#     enableHotelMode=on, enableMenuKey=on, enableLimitedMode=on (enableMenuKey "off" also locks the Home
#     button, measured). off: these three back to the saved values.
set -u
ip="${1:?Usage: tv_hotelmode.sh <TV-IP> on|off|show}"; mode="${2:-show}"
SSH_OPTS="-i $HOME/.ssh/id_ed25519 -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
BK="$HOME/guest-tv/hotelMode.$ip.backup.json"
tv() {
  # shellcheck disable=SC2086,SC2029  # method/payload expand locally on purpose (fixed values)
  ssh $SSH_OPTS "root@$ip" "luna-send -i luna://com.webos.settingsservice/$1 '$2' > /tmp/hm.txt 2>&1 & P=\$!; sleep 1; kill \$P 2>/dev/null; wait \$P 2>/dev/null; cat /tmp/hm.txt; rm -f /tmp/hm.txt"
}
show() {
  tv getSystemSettings '{"category":"hotelMode","keys":["enableHotelMode","enableMenuKey","enableLimitedMode","keyManagement","enableKeylock","enableIrRemote"]}'
  echo
}
apply() {  # write the hotelMode values in $1; exit 0 only when the TV reads them back (no reply in time = not confirmed)
  tv setSystemSettings "$1"
  echo; S=$(show); echo "$S"
  python3 - "$1" "$S" <<'PY' || { echo "ERROR: the TV did not confirm the values - check with show, run $mode again"; exit 1; }
import json, sys
want, s = json.loads(sys.argv[1])["settings"], sys.argv[2]
try:
    got = json.loads(s[s.find("{") : s.rfind("}") + 1])["settings"]
except (ValueError, KeyError, TypeError):
    sys.exit(1)
sys.exit(any(got.get(k) != v for k, v in want.items()))
PY
}
case "$mode" in
  show) show ;;
  on)
    if [ ! -s "$BK" ]; then
      tv getSystemSettings '{"category":"hotelMode"}' > "$BK"
      python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d['returnValue'] and d['settings']['enableHotelMode']" "$BK" \
        || { echo "backup invalid - abort"; rm -f "$BK"; exit 1; }
      echo "backup: $BK"
    fi
    apply '{"category":"hotelMode","settings":{"enableHotelMode":"on","enableMenuKey":"on","enableLimitedMode":"on"}}' ;;
  off)
    [ -s "$BK" ] || { echo "no backup $BK"; exit 1; }
    P=$(python3 -c "import json,sys; s=json.load(open(sys.argv[1]))['settings']; print(json.dumps({'category':'hotelMode','settings':{k:s[k] for k in ('enableHotelMode','enableMenuKey','enableLimitedMode')}}))" "$BK") \
      || { echo "backup $BK unusable - nothing changed"; exit 1; }
    apply "$P" ;;
  *) echo "Usage: tv_hotelmode.sh <TV-IP> on|off|show"; exit 2 ;;
esac
