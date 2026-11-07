#!/bin/sh
# Guest TV: build the ipk without ares-cli, with tar and ar (macOS bsdtar or GNU tar on Linux), python3, qrencode.
#   build_ipk.sh   -> build/dist/com.example.guesttv_<version>_all.ipk
# Same layout as LG's ares-package (source: github.com/webos-tools/cli, lib/package.js):
#   ar members:    debian-binary, control.tar.gz, data.tar.gz
#   data.tar.gz:   usr/palm/applications/<id>/...  +  usr/palm/packages/<id>/packageinfo.json
# Install on the TV: pi/deploy/tv_install.sh (appInstallService/dev/install, like the Homebrew Channel).
# Fonts and icons live in app/assets/; screensaver photos are optional (app/assets/img/, listed in state.photos).
set -eu
cd "$(dirname "$0")/.."
ID="com.example.guesttv"
VER=$(python3 -c "import json;print(json.load(open('app/appinfo.json'))['version'])")
DIST="$(pwd)/build/dist"
DATA="$DIST/data"
APPDIR="$DATA/usr/palm/applications/$ID"
PKGDIR="$DATA/usr/palm/packages/$ID"
rm -rf "$DIST"
mkdir -p "$APPDIR" "$PKGDIR" "$DIST/control"

echo "== App files =="
cp -R app/. "$APPDIR/"
cp pi/content/content.json "$APPDIR/content.json"   # offline start: content also without the Pi
find "$DATA" \( -name '.DS_Store' -o -name '.ruff_cache' -o -name '__pycache__' \) -prune -exec rm -rf {} +

echo "== QR codes for the offline start (WhatsApp, guest card, video, FAQ links; Wi-Fi only comes from the Pi) =="
python3 - "$APPDIR" <<'PY'
import json, sys
sys.path.insert(0, "pi")
import state_builder as sb  # same QR generation as on the Pi (qrencode)
app = sys.argv[1]
content = json.load(open(app + "/content.json", encoding="utf-8"))
qr = sb.build_qr({"ssid": "", "password": ""}, content)
json.dump(content, open(app + "/content.json", "w", encoding="utf-8"), ensure_ascii=False)
ds = json.load(open(app + "/default-state.json", encoding="utf-8"))
ds["qr"] = qr
json.dump(ds, open(app + "/default-state.json", "w", encoding="utf-8"), ensure_ascii=False)
print("   QR:", ", ".join(k for k, v in qr.items() if v) or "none (qrencode missing?)")
PY
find pi -name '__pycache__' -prune -exec rm -rf {} +

printf '{\n  "id": "%s",\n  "version": "%s",\n  "app": "%s"\n}\n' "$ID" "$VER" "$ID" > "$PKGDIR/packageinfo.json"

echo "== ipk =="
cat > "$DIST/control/control" <<EOF
Package: $ID
Version: $VER
Section: misc
Priority: optional
Architecture: all
Installed-Size: $(du -sk "$DATA" | cut -f1)
Maintainer: guest-tv
Description: Guest TV portal
webOS-Package-Format-Version: 2
webOS-Packager-Version: x.y.x
EOF
# Owner root like ares-package. macOS bsdtar: also without AppleDouble/xattr leftovers.
if tar --version 2>/dev/null | grep -q "GNU tar"; then
  tarc() { tar --owner=root:0 --group=root:0 "$@"; }
else
  tarc() { COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs --uid 0 --gid 0 --uname root --gname root "$@"; }
fi
tarc -czf "$DIST/control.tar.gz" -C "$DIST/control" control
tarc -czf "$DIST/data.tar.gz" -C "$DATA" usr
printf '2.0\n' > "$DIST/debian-binary"
IPK="$DIST/${ID}_${VER}_all.ipk"
(cd "$DIST" && ar rcS "$IPK" debian-binary control.tar.gz data.tar.gz)

echo "== Check =="
ar t "$IPK"
tar tzf "$DIST/data.tar.gz" | grep -c "^usr/palm/applications/$ID/" | sed 's/^/Files in the app: /'
if tar tzf "$DIST/data.tar.gz" | grep -E '\.ruff_cache|__pycache__|\.DS_Store|/\._'; then
  echo "ERROR: garbage in the package"; exit 1
fi
ls -la "$IPK"
