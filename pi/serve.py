"""guest-tv: minimal server for the TVs on 0.0.0.0:8090 (apartment LAN), systemd: guest-tv.service.

- GET/HEAD: static files from www/ (state.json, webcam images), no directory listings.
- POST /tv/setting: TV settings through the Pi (tv_settings.py, root SSH to the TV). Only from the
  TV addresses in config.json with the app's Origin, body <= 1 KB JSON (text/plain, so no CORS
  preflight), reply JSON. 403 unknown IP/Origin, 413 too large, 400 invalid, otherwise the status
  from tv_settings.handle().
"""

import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import tv_settings

assert sys.version_info >= (3, 11), f"Requires Python 3.11+, got {sys.version_info[:2]}"

WWW = Path(__file__).resolve().parent / "www"
PORT = 8090
MAX_BODY = 1024

# Origin of the portal app on a real TV: webOS (WebAppMgr) sends "file://<app-id>-webos" on webOS 5,
# NOT "null" like a desktop browser opening the app from file://.
APP_ORIGIN = f"file://{tv_settings.PORTAL_ID}-webos"
# webOS 4.10 sends the app id without "-webos"
APP_ORIGIN_4 = f"file://{tv_settings.PORTAL_ID}"


def origin_allowed(origin: str) -> bool:
    """CORS only for the TV app (APP_ORIGIN on the TV, "null" from file:// in a browser) and the desktop
    browser test (http://127.0.0.1 / http://localhost, any port). Exact host match: a prefix match
    would let "http://127.0.0.1.evil.example" through."""
    if origin in ("null", APP_ORIGIN, APP_ORIGIN_4):
        return True
    try:
        parts = urlsplit(origin)
        return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
    except ValueError:
        return False


# POST only with the portal app's origin (APP_ORIGIN on the TV, "null" in a browser test). The app always sends
# one (measured on webOS 4.10 and 5). A request without Origin is refused: a form posted from a page in the TV's
# own browser comes from the same TV address and may carry none. Journal: "tv/setting origin=...".
APP_ORIGINS = ("null", APP_ORIGIN, APP_ORIGIN_4)


def setting_allowed(ip: str, origin: str | None) -> bool:
    """POST /tv/setting only from a TV (IP list in tv_settings) with an app origin (APP_ORIGINS).
    localhost origins only for the desktop test via 127.0.0.1 (which is only on the list in tests)."""
    if ip not in tv_settings.TVS:
        return False
    return origin in APP_ORIGINS or (ip == "127.0.0.1" and origin_allowed(origin or ""))


class KioskHandler(SimpleHTTPRequestHandler):
    # Socket timeout per connection: a client that sends half a request and then nothing would otherwise
    # hold a server thread forever (many of them = no state.json and no settings for the TVs).
    timeout = 30

    # CORS only keeps web pages in other browsers from reading state.json (guest name, stay dates, Wi-Fi
    # password). Any device that reaches the Pi can still fetch it directly: the protection is the network,
    # guests must never reach the Pi (separate guest Wi-Fi, see README "Safety").
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        origin = self.headers.get("Origin")
        if origin and origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def list_directory(self, path):  # no directory listings
        self.send_error(403)
        return None

    def do_POST(self) -> None:
        if self.path != "/tv/setting":
            self.send_error(404)
            return
        ip = self.client_address[0]
        origin = self.headers.get("Origin")
        self.log_message("tv/setting origin=%r", origin)  # diagnosis: which origin does the app really send
        if not setting_allowed(ip, origin):
            self.send_json(403, {"ok": False, "error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length > MAX_BODY:  # do not read the body; HTTP/1.0 closes the connection afterwards
            self.send_json(413, {"ok": False, "error": "too large"})
            return
        try:
            if length < 0:
                raise ValueError("Content-Length missing")
            req = json.loads(self.rfile.read(length))
        except ValueError:
            self.send_json(400, {"ok": False, "error": "bad request"})
            return
        self.send_json(*tv_settings.handle(ip, req))

    def send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()  # Cache-Control + CORS as for GET (TV app: Allow-Origin "null")
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # Default logging to the journal (the TVs poll only every 5 min, so it stays small);
        # errors (403/404) stay diagnosable instead of disappearing silently.
        super().log_message(fmt, *args)


def main() -> int:
    WWW.mkdir(parents=True, exist_ok=True)
    handler = partial(KioskHandler, directory=str(WWW))
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"[serve] {WWW} on 0.0.0.0:{PORT}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
