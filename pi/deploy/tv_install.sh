#!/bin/sh
# guest-tv: install the portal on a TV (runs ON THE PI)
#   tv_install.sh <TV-IP> <ipk>   (ipk = com.example.guesttv_<version>_all.ipk from build/dist)
#   NO_REBOOT=1 tv_install.sh ...  update without the cold boot test (TV in use, e.g. a guest is there)
# Way: ipk -> appInstallService/dev/install (like the Homebrew Channel / developer mode);
# the app lands in /media/developer/apps/usr/palm/applications/com.example.guesttv.
# NEVER write to /usr/palm: it is a signed, read-only squashfs (webosbrew.org "Filesystem": "Most of the
# webOS filesystem is read only").
# Then the setup: ../tv_settings.py setup (baseline values + Home button) and tv_hotelmode.sh on
# (locks the settings gear and voice control). Errors there only warn, the app stays installed.
set -eu

TV_IP="${1:?Usage: tv_install.sh <TV-IP> <ipk>}"
IPK="${2:?Usage: tv_install.sh <TV-IP> <ipk>}"
ID="com.example.guesttv"
APP_DIR="/media/developer/apps/usr/palm/applications/$ID"
KEY="$HOME/.ssh/id_ed25519"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Dropbear on the TVs regenerates its host keys on every cold boot: a remembered key would block every
# call after the reboot step. So no known_hosts for the TVs (apartment LAN only). To pin keys, make the
# host key on the TV persistent first, then use StrictHostKeyChecking=yes here.
SSH_OPTS="-i $KEY -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
# shellcheck disable=SC2086,SC2029  # SSH_OPTS split on purpose; the commands are put together locally
ssh_tv() { ssh $SSH_OPTS "root@$TV_IP" "$@"; }
# shellcheck disable=SC2086
scp_tv() { scp $SSH_OPTS "$@"; }

echo "== 1/5 ipk + installer to $TV_IP =="
scp_tv "$IPK" "root@$TV_IP:/tmp/guesttv.ipk"
scp_tv "$HERE/tv_dev_install.sh" "root@$TV_IP:/tmp/tv_dev_install.sh"

echo "== 2/5 Install (appInstallService/dev/install) =="
ssh_tv "sh /tmp/tv_dev_install.sh /tmp/guesttv.ipk; R=\$?; rm -f /tmp/tv_dev_install.sh; exit \$R"
ssh_tv "test -f $APP_DIR/appinfo.json" || { echo "ERROR: $APP_DIR missing after the install"; exit 1; }
echo "   app folder OK: $APP_DIR"

echo "== 3/5 Autostart (webosbrew init.d) + start the portal now =="
scp_tv "$HERE/guesttv.init" "root@$TV_IP:/var/lib/webosbrew/init.d/guesttv"
# luna-send -i (not -n 1, that stays silent on these firmwares): collect the reply after 5 s
LAUNCH=$(ssh_tv "chmod +x /var/lib/webosbrew/init.d/guesttv && { luna-send -i luna://com.webos.applicationManager/launch '{\"id\":\"$ID\"}' > /tmp/guesttv-launch.log 2>&1 & P=\$!; sleep 5; kill \$P 2>/dev/null; wait \$P 2>/dev/null; cat /tmp/guesttv-launch.log; }")
case "$LAUNCH" in
  *'"returnValue":true'*|*'"returnValue": true'*) echo "   portal started (visible on the TV?)" ;;
  *) echo "WARNING: start not confirmed: $LAUNCH" ;;
esac

echo "== 4/5 Setup: baseline values + Home button, hotel mode =="
# Before the cold boot, so the reboot test checks the settings too. Errors do not abort.
SETUP_OK=1
if ! python3 "$HERE/../tv_settings.py" "$TV_IP" setup; then
  SETUP_OK=0
  echo "WARNING: tv_settings setup failed. Retry: python3 $HERE/../tv_settings.py $TV_IP setup"
fi
# tv_hotelmode.sh saves the original hotel mode itself: ~/guest-tv/hotelMode.<ip>.backup.json
HM=$(sh "$HERE/tv_hotelmode.sh" "$TV_IP" on 2>&1) || true
echo "$HM"
for KV in '"enableHotelMode":"on"' '"enableMenuKey":"on"' '"enableLimitedMode":"on"'; do
  case "$HM" in
    *"$KV"*) ;;
    *) SETUP_OK=0; echo "WARNING: hotel mode not confirmed ($KV missing). Retry: sh $HERE/tv_hotelmode.sh $TV_IP on" ;;
  esac
done

if [ "${NO_REBOOT:-0}" = 1 ]; then  # update of a TV in use (a guest in front of it): no reboot
  echo "== 5/5 Cold boot test skipped (NO_REBOOT=1) =="
  [ "$SETUP_OK" = 1 ] || echo "!!! ATTENTION: setup (step 4/5) incomplete, read the WARNING above and retry."
  exit 0
fi
# init.d scripts (the autostart) and dropbear only run on a cold boot; remote off/on is just standby
echo "== 5/5 Cold boot test (autostart + dropbear only start on a cold boot) =="
ssh_tv "nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &" || true
echo "   TV reboots, waiting for the network (about 60 s) ..."
sleep 60
if ping -c 3 -W 3 "$TV_IP" >/dev/null 2>&1; then
  echo "TV reachable again."
  ssh_tv "test -d $APP_DIR && echo app installed; test -f /var/luna/preferences/webosbrew_sshd_enabled && echo root environment OK" \
    || echo "WARNING: no SSH after the reboot yet - wait for the cold boot and check again"
  echo "Now check on the TV that the portal starts by itself."
else
  echo "Note: the TV does not answer ping (deep sleep possible). Switch it on and check by hand."
fi
[ "$SETUP_OK" = 1 ] || echo "!!! ATTENTION: setup (step 4/5) incomplete, read the WARNING above and retry."
