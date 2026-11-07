#!/bin/sh
# Runs ON THE TV (tv_install.sh copies it to /tmp): installs the ipk through LG's installer service,
# the same way as the Homebrew Channel (webos-homebrew-channel tools/install.sh) and ares-install.
# Exit 0 only when the installer reports "state":"installed" and no failure; else the log and exit 1.
IPK="${1:-/tmp/guesttv.ipk}"
LOG=/tmp/guesttv-install.log
failed() { grep -q -e failed -e '"returnValue": *false' "$LOG" 2>/dev/null; }  # e.g. "Denied method call"
installed() { grep -q '"state": *"installed"' "$LOG" 2>/dev/null; }
rm -f "$LOG"
luna-send-pub -w 60000 -i 'luna://com.webos.appInstallService/dev/install' \
  "{\"id\":\"com.ares.defaultName\",\"ipkUrl\":\"$IPK\",\"subscribe\":true}" > "$LOG" 2>&1 &
PID=$!
i=0
while [ "$i" -lt 90 ]; do
  { failed || installed; } && break
  sleep 1
  i=$((i + 1))
done
kill "$PID" 2>/dev/null
rm -f "$IPK"
if failed; then
  echo "   install failed:"
elif installed; then
  echo "   installed"
  exit 0
else
  echo "   TIMEOUT after 90 s"
fi
cat "$LOG"
exit 1
