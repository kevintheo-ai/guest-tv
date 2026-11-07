#!/bin/sh
# Runs ON THE PI: point a TV's Home button to the portal (or back to LG's home screen), as root.
#   tv_home.sh <TV-IP> [portal|lg]     way back: tv_home.sh <TV-IP> lg
# Writes through tv_settings.py: read all of defaultApps, change only "home", write it all back
# (an object with only "home" would delete LG's other mappings).
set -eu
exec python3 "$(cd "$(dirname "$0")" && pwd)/../tv_settings.py" "${1:?Usage: tv_home.sh <TV-IP> [portal|lg]}" home "${2:-portal}"
