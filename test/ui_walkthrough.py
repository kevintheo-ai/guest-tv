"""UI walkthrough of the TV app in headless Chromium (1920x1080), the remote control via the keyboard.

Checks the bugs that would hurt on the device: the screensaver does not come straight back after waking up,
OK on the vacant screen triggers nothing else, Back lands on the tile you came from, settings (5 brightness
steps, eye comfort) go to the Pi after a short pause (relay mock), a pending reset runs before any setting,
the language list (focus stays inside, Back closes it), the TV language follows after 1.5 s, console tiles,
the check-out notice on the departure day, FAQ/around you follow the focus, Magic Remote pointer, nothing
sticks out of panels, tiles or the screen, every settings label fits on one line (all 6 languages).

Run: python3 test/ui_walkthrough.py   (needs: pip install playwright==1.58.0, then
     python3 -m playwright install chromium; exit 0 = ok)
state.json/content.json/webcam images are intercepted: no Pi needed.
TV_SHOTS=<dir> also saves screenshots (layout check without a TV), including clean English screenshots of
every screen with the sample content: home-en, practical-en, around-en, weather-en, help-en, settings-en, vacant-en.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CONTENT = json.loads((ROOT / "pi/content/content.json").read_text(encoding="utf-8"))
SHOTS = os.environ.get("TV_SHOTS")
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII="
)
LANGS = ["de", "en", "fr", "it", "nl", "pl"]
# Setting rows with all their values, straight from SETTING_DEFS (app.js): every label gets measured
SETTINGS = {
    key: re.findall(r'"([^"]+)"', values)
    for key, values in re.findall(
        r'key: "(\w+)", values: \[([^\]]+)\]', (ROOT / "app/js/app.js").read_text(encoding="utf-8")
    )
}
# The sample content is short and never scrolls; this test-only category exercises the row-wise paging
LONG = {
    "title": dict.fromkeys(LANGS, "Long list"),
    "places": [{"name": f"Sample place {i}", "desc": dict.fromkeys(LANGS, f"Sample line {i}")} for i in range(1, 13)],
}
TEST_CONTENT = dict(CONTENT, around=[*CONTENT["around"], LONG])
GUEST = {
    "reservationId": "42",
    "name": "Anna Example",
    "language": "de",
    "arrival": "2026-11-01",
    "departure": "2099-11-06",
    "phase": "active",
}
# Departure day: 06.11.2026, the page clock fixed at 09:00 (local time) -> check-out notice
CHECKOUT_GUEST = dict(GUEST, departure="2026-11-06")


def live_bus():
    # departures relative to now (the app shows them from now on); one already gone, one "now"
    now = int(time.time() * 1000)
    rows = [
        (-600, "Riverside, Station", ["Old Town, Market"]),
        (20, "Lakeview, Pier", ["Old Town, Market", "Hill Road, School"]),
    ]
    rows += [(m * 60, "Riverside, Station" if m % 2 else "Lakeview, Pier", []) for m in (7, 15, 22, 30, 37)]
    return [
        {
            "ts": now + s * 1000,
            "time": time.strftime("%H:%M", time.localtime(now / 1000 + s)),
            "line": "12",
            "to": to,
            "via": via,
        }
        for s, to, via in rows
    ]


CAMS = [
    # image from yesterday: the caption then carries the date (the longest form, never cut)
    {
        "id": c,
        "name": n,
        "elev": e,
        "img": f"cams/{c}.jpg",
        "updated": f"{date.today() - timedelta(days=1)}T17:31+01:00",
    }
    for c, n, e in (("village", "Village", 1100), ("lake", "Lake", 1020), ("summit", "Summit", 2400))
]


def DAY(i):  # date relative to today (local time like the browser): the app only shows days from today
    return (date.today() + timedelta(days=i)).isoformat()


NOW_ISO = datetime.now().astimezone().isoformat(timespec="minutes")  # fresh state: "now" values are valid


def MTN_DAYS(mx, mn):  # mountain days parallel to weather.today + days (index 0 = today)
    return [{"date": DAY(i), "min": mn - i, "max": mx - i, "code": 3} for i in range(4)]


def state(guest, content=TEST_CONTENT, qr=None):
    return {
        "updated": "2026-11-02T10:00+01:00",
        "guest": guest,
        "wlan": {"ssid": "GuestNet", "password": "example-pass-27"},
        "qr": qr or {"wlan": None, "whatsapp": None, "guestcard": None, "video": None},
        "weather": {
            "updated": NOW_ISO,
            "mtnUpdated": NOW_ISO,
            "current": {"temp": 6.2, "code": 3, "wind": 11},
            "today": {"date": DAY(0), "min": 1, "max": 8, "code": 3},
            "days": [
                {"date": DAY(1), "min": 1, "max": 8, "code": 61},
                {"date": DAY(2), "min": -1, "max": 5, "code": 73},
                {"date": DAY(3), "min": 0, "max": 6, "code": 2},
            ],
            "mountain": [
                {"name": "Midway", "elev": 1500, "temp": -1.8, "code": 1, "wind": 1, "days": MTN_DAYS(4, -3)},
                {"name": "Summit", "elev": 2400, "temp": -6.1, "code": 3, "wind": 3, "days": MTN_DAYS(0, -6)},
            ],
        },
        "bus": live_bus(),
        "cams": CAMS,
        "photos": [],
        "content": content,
    }


fails = []


def check(cond, msg):
    print(("ok:   " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Server(ThreadingHTTPServer):
    # The default backlog of 5 is too small for the burst of requests (scripts, 6 languages, several pages):
    # with 5, 3 of 5 runs aborted (ERR_CONNECTION_RESET or "Data is not defined" = data.js missing), with 64
    # all runs passed (assumed cause: a full listen queue, not verified further)
    request_queue_size = 64


srv = Server(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT / "app")))
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/index.html"

OVERFLOW_JS = """() => {
  const out = [];
  document.querySelectorAll('.screen.active .panel, .screen.active .place, .screen.active .tile, .screen.active .w-day, .screen.active .setting-row, .screen.active .weather-now, .screen.active .w-mtn, .screen.active .sos, .screen.active .doc, .screen.active .bus-board, #langMenu, #modalBox').forEach(e => {
    if (e.offsetParent !== null && (e.scrollHeight > e.clientHeight + 2 || e.scrollWidth > e.clientWidth + 2)) out.push(e.className + (e.getAttribute('data-pane') ? '[' + e.getAttribute('data-pane') + ']' : '') + ' ' + e.scrollHeight + '>' + e.clientHeight);
  });
  // tile content grows upwards (flex-end): scrollHeight does not see that -> check the position
  document.querySelectorAll('.screen.active .tile').forEach(t => {
    const tr = t.getBoundingClientRect();
    [].forEach.call(t.children, c => {
      const r = c.getBoundingClientRect();
      if (r.height && (r.top < tr.top - 1 || r.bottom > tr.bottom + 1 || r.left < tr.left - 1 || r.right > tr.right + 1)) out.push('out of tile: ' + c.textContent);
    });
  });
  // cut lines: too wide, or clipped (2-line limit). Vertically only for elements that really clip:
  // with line-height 1.2 the glyphs stick out 1-3 px anyway (no clipping)
  document.querySelectorAll('.screen.active .tile-label, .screen.active .tile-sub, #hdr .checkout-banner, #hdr .lang-opt, .screen.active .cat-item, .screen.active .cam-cap, .md-btn, .screen.active .place b, .screen.active .place span').forEach(e => {
    if (e.offsetParent === null) return;
    const clips = getComputedStyle(e).overflow !== 'visible';
    if (e.scrollWidth > e.clientWidth + 1 || (clips && e.scrollHeight > e.clientHeight + 2)) out.push('cut: ' + e.textContent);
  });
  document.querySelectorAll('.screen.active *, #hdr *, #modalBox *').forEach(e => {
    if (e.closest('.rail-body')) return; // scrolls with the focus: content cut at the bottom is intended
    const r = e.getBoundingClientRect();
    if (r.height > 0 && (r.bottom > 1080 || r.right > 1920)) out.push('outside: ' + (e.className || e.tagName));
  });
  return out;
}"""
# Settings: row name and value each on ONE line (a line break = the text does not fit)
ONE_LINE_JS = """() => [].filter.call(document.querySelectorAll('.screen.active .s-name, .screen.active .s-cur'), e => {
  const r = document.createRange();
  r.selectNodeContents(e);
  return r.getClientRects().length > 1;
}).map(e => 'wrapped: ' + e.textContent)"""

focused = "(() => { const f = document.querySelector('.focusable.focused'); return f ? (f.getAttribute('data-app') || f.getAttribute('data-target') || f.getAttribute('data-set-key') || f.getAttribute('data-lang') || f.id || f.querySelector('.tile-label, b')?.textContent || f.textContent.trim()) : null; })()"
IN_MENU = "(() => { const f = document.querySelector('.focusable.focused'); return !!f && document.getElementById('langMenu').contains(f); })()"
IN_MODAL = "(() => { const f = document.querySelector('.focusable.focused'); return !!f && document.getElementById('modalBox').contains(f); })()"
MODAL_OPEN = "!document.getElementById('modal').hidden"
# Never a half-cut card: every card fully visible or fully outside
NO_PARTIAL = """(() => { const b = document.querySelector('.screen.active .rail-body').getBoundingClientRect();
  return [].filter.call(document.querySelectorAll('.screen.active .place'), p => { const r = p.getBoundingClientRect();
    const inside = r.top >= b.top - 1 && r.bottom <= b.bottom + 1, outside = r.bottom <= b.top + 1 || r.top >= b.bottom - 1;
    return !inside && !outside; }).map(p => p.textContent); })()"""
# Name + line of a place card always as wide as the card: the TV browser (Chromium 68) otherwise measured them by
# their text, calculated too tight and cut the name with "…" despite free space
PLACE_TEXT_NARROW = """(() => [].filter.call(document.querySelectorAll('#placeGrid .place'), p => {
  const cs = getComputedStyle(p), inner = p.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return [].some.call(p.children, c => Math.abs(c.getBoundingClientRect().width - inner) > 1); }).map(p => p.textContent))()"""
VISIBLE_IN_BODY = """(() => { const f = document.querySelector('.focusable.focused'); const b = f.closest('.rail-body').getBoundingClientRect();
  const r = f.getBoundingClientRect(); return r.top >= b.top - 1 && r.bottom <= b.bottom + 1; })()"""
MENU_OPEN = "!document.getElementById('langMenu').hidden"
CAT = "Nav.setFocus([].filter.call(document.querySelectorAll('#catList .cat-item'), b => b.textContent === {})[0])"


def relay(pg, op):
    return pg.evaluate(f"Luna._lastResults()['relay.{op}']")


def sends(pg, op, value=None):
    return pg.evaluate(
        f"Luna._relayLog().filter(b => b.op === {json.dumps(op)} && ({json.dumps(value)} === null || b.value === {json.dumps(value)})).length"
    )


def shot(pg, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def pick_lang(pg, lang):
    """Pick a language from the drop-down (like with the pointer: button, then entry)."""
    pg.evaluate(
        f"document.getElementById('langToggle').click(); document.querySelector('#langMenu [data-lang={lang}]').click()"
    )


def qr(text):
    """QR code as a data: URI like the Pi makes it (qrencode); None if qrencode is not installed."""
    if not shutil.which("qrencode"):
        return None
    png = subprocess.run(
        ["qrencode", "-o", "-", "-t", "PNG", "-s", "8", "-m", "2", text], capture_output=True, check=True
    )
    return "data:image/png;base64," + base64.b64encode(png.stdout).decode()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    def page_with(st, storage=None, clock=None, setup=None):
        pg = browser.new_page(viewport={"width": 1920, "height": 1080})
        if setup:  # e.g. TV mode: fake the bridge + Pi route, before the app starts
            setup(pg)
        if clock:  # fixed time of day (Date), timers run normally
            pg.clock.set_fixed_time(clock)
        if storage:  # preset localStorage before the app starts (e.g. previous guest)
            pg.add_init_script(
                "".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in storage.items())
            )
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.route(
            "**/state.json",
            lambda r: r.fulfill(
                status=200,
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps(st),
            ),
        )
        pg.route(
            "**/content.json",
            lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(CONTENT)),
        )
        # webcam images come from the Pi, which is not there: placeholder (fonts and icons are real files)
        pg.route("**/cams/*.jpg*", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG_1PX))
        pg.goto(URL)
        try:  # under load the first fetch sometimes runs into the 6 s limit: fetch once more
            pg.wait_for_function("window.Data && Data.source() === 'pi'", timeout=15000)
        except Exception:
            pg.evaluate("Data.poll()")
            pg.wait_for_function("window.Data && Data.source() === 'pi'", timeout=15000)
        pg.wait_for_timeout(300)
        return pg, errors

    # ---- 1) Navigation with a guest ----
    pg, errors = page_with(state(GUEST))
    check(pg.evaluate("Nav.screen()") == "home", "start with a guest -> home")
    gw = pg.evaluate("document.querySelector('.greeting').getBoundingClientRect().width")
    check(
        gw >= 1279,
        f"greeting as wide as possible, never by text width (guest name cut with '…' on the TV) ({gw:.0f} px)",
    )
    check(
        pg.evaluate("document.fonts.check('500 30px Inter') && document.fonts.check('300 66px Inter')"),
        "Inter loaded from app/assets/fonts (the overflow checks measure the real font)",
    )
    check(pg.evaluate(focused) == "tv", "focus starts on TV")
    check(
        pg.evaluate("document.getElementById('checkoutBanner').hidden"),
        "no check-out notice before the departure day",
    )
    pg.keyboard.press("ArrowRight")
    check(pg.evaluate(focused) == "netflix", "right -> Netflix")
    pg.keyboard.press("ArrowDown")
    down = pg.evaluate(focused)
    check(down in ("around", "bus", "weather"), f"down from Netflix -> the tile below ({down})")
    pg.keyboard.press("Enter")
    check(pg.evaluate("Nav.screen()") == {"bus": "around"}.get(down, down), "OK opens the sub screen")
    pg.keyboard.press("Escape")
    check(
        pg.evaluate("Nav.screen()") == "home" and pg.evaluate(focused) == down,
        "Back -> home, focus on the tile you came from",
    )
    check(
        pg.evaluate("I18N.time(new Date(2026,0,1,9,5))") == "09:05",
        "clock in 24-hour format",
    )
    # no taglines/help texts: a sub line only with live data (bus, weather) or the Disney+ profile
    subs = pg.evaluate(
        "[].filter.call(document.querySelectorAll('#scr-home .tile'), t => t.querySelector('.tile-sub') && t.querySelector('.tile-sub').textContent)"
        ".map(t => t.getAttribute('data-app') || t.getAttribute('data-target'))"
    )
    check(subs == ["disney", "bus", "weather"], f"sub lines only Disney+ profile, bus, weather ({subs})")
    # titles of one row on the same height, whether the tile has a sub line or not
    rows = pg.evaluate(
        "[].map.call(document.querySelectorAll('#scr-home .row'), r => [].filter.call(r.querySelectorAll('.tile'),"
        " t => t.offsetParent !== null).map(t => { const l = t.querySelector('.tile-label'); return l.offsetTop + l.offsetHeight; }))"
    )
    check(
        all(max(r) - min(r) <= 1 for r in rows if r),
        f"tile titles per row on the same height (bottom edge, px) {rows}",
    )
    bus_sub = pg.evaluate("document.getElementById('tileBusSub').textContent")
    check(bus_sub.count("·") == 1 and "Lakeview" in bus_sub, f"bus tile: next 2 departures from now ({bus_sub})")

    # ---- 1b) Row 1: TV, Netflix, Prime, Disney+, YouTube, gaming (one tile for all consoles) ----
    row1 = pg.evaluate(
        "[].map.call(document.querySelectorAll('.row-big .tile'), t => t.offsetParent ? t.getAttribute('data-app') : null)"
    )
    check(row1 == ["tv", "netflix", "prime", "disney", "youtube", "gaming"], f"row 1 ({row1})")
    check(
        "«Guests»" in pg.evaluate("document.querySelector('[data-app=disney] .tile-sub').textContent"),
        "Disney+: hint at the guest profile (name as in Disney+)",
    )
    check(
        json.loads(pg.evaluate("localStorage.getItem('guesttv.inputs')") or "[]")[0]["appId"] == "com.webos.app.hdmi2",
        "console list remembered for a start without the Pi",
    )
    pg.evaluate("Nav.setFocus(document.querySelector('[data-app=disney]'))")
    pg.keyboard.press("ArrowRight")
    check(pg.evaluate(focused) == "youtube", "right from Disney+ -> YouTube")
    pg.keyboard.press("Enter")
    res = pg.evaluate("Luna._lastResults()['luna://com.webos.applicationManager/launch']")
    check(res and res["params"]["id"] == "youtube.leanback.v4", f"YouTube started ({res})")
    pg.keyboard.press("ArrowRight")
    check(pg.evaluate(focused) == "gaming", "right from YouTube -> gaming")
    pg.keyboard.press("Enter")
    check(pg.evaluate(MODAL_OPEN) and pg.evaluate(IN_MODAL), "gaming: picker open, focus inside")
    check(
        pg.evaluate("[].map.call(document.querySelectorAll('#modalBox .md-btn'), b => b.textContent)")
        == ["XBOX ONE", "Nintendo Switch"],
        "picker: both consoles",
    )
    pg.keyboard.press("Escape")
    check(not pg.evaluate(MODAL_OPEN) and pg.evaluate(focused) == "gaming", "Back closes, focus back on gaming")
    pg.keyboard.press("Enter")
    pg.keyboard.press("Enter")  # first console
    res = pg.evaluate("Luna._lastResults()['luna://com.webos.applicationManager/launch']")
    check(
        res and res["params"]["id"] == "com.webos.app.hdmi2" and not pg.evaluate(MODAL_OPEN),
        f"XBOX: input started directly, picker closed ({res})",
    )
    check("XBOX ONE" in pg.evaluate("document.getElementById('toast').textContent"), "the notice names the device")
    pg.evaluate("Luna._mockFail.launch = true")
    pg.keyboard.press("Enter")
    pg.keyboard.press("ArrowRight")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(100)
    check(
        relay(pg, "input") == {"op": "input", "value": "com.webos.app.hdmi4"},
        "the app may not start the input -> the Pi switches",
    )
    pg.evaluate("Luna._mockFail.input = true")
    pg.keyboard.press("Enter")
    pg.keyboard.press("ArrowRight")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(100)
    check(
        pg.evaluate("document.getElementById('toast').textContent") == pg.evaluate("I18N.t('input.failed')"),
        "both ways failed -> notice",
    )
    pg.evaluate("Luna._mockFail.launch = false; Luna._mockFail.input = false")
    # only ONE console: OK switches directly, without a picker
    pg.evaluate(
        "Luna._mock.inputs = {ok: true, inputs: [{label: 'Nintendo Switch', appId: 'com.webos.app.hdmi4'}]}; Data.poll()"
    )
    pg.wait_for_timeout(300)
    pg.evaluate("Nav.setFocus(document.getElementById('tileGaming'))")
    pg.keyboard.press("Enter")
    res = pg.evaluate("Luna._lastResults()['luna://com.webos.applicationManager/launch']")
    check(
        not pg.evaluate(MODAL_OPEN) and res["params"]["id"] == "com.webos.app.hdmi4",
        "one console: switched directly",
    )
    pg.evaluate(
        "Luna._mock.inputs = {ok: true, inputs: [{label: 'XBOX ONE', appId: 'com.webos.app.hdmi2'}, {label: 'Nintendo Switch', appId: 'com.webos.app.hdmi4'}]}; Data.poll()"
    )
    pg.wait_for_timeout(300)

    # ---- 2) Around you: rail, scrolling places, detail view, bus; help + weather as a rail ----
    pg.evaluate("Nav.show('around')")
    check(
        pg.evaluate(focused) == "Essen & Trinken", f"around you: focus on the first category ({pg.evaluate(focused)})"
    )
    n_food = pg.evaluate("document.querySelectorAll('#placeGrid .place').length")
    check(n_food == 4, f"food & drink: 4 places ({n_food})")
    pg.keyboard.press("ArrowRight")
    check(pg.evaluate(focused) == "Village Pizzeria", f"right -> first place ({pg.evaluate(focused)})")
    pg.keyboard.press("Enter")
    rows = pg.evaluate("[].map.call(document.querySelectorAll('#modalBox .md-row'), e => e.textContent)")
    check(
        pg.evaluate(MODAL_OPEN) and "+41 00 000 00 00" in rows and "Main Street 1" in rows,
        f"detail view: address + phone in international format ({rows[:2]})",
    )
    check(
        pg.evaluate("document.querySelectorAll('#modalBox .k, #modalBox .md-btn, #modalBox small').length") == 0,
        "detail view without field names, buttons and hint texts",
    )
    shot(pg, "detail-de")
    pg.keyboard.press("Enter")
    check(
        not pg.evaluate(MODAL_OPEN) and pg.evaluate(focused) == "Village Pizzeria",
        "OK closes, focus back on the place",
    )
    pg.keyboard.press("Enter")
    pg.keyboard.press("Escape")
    check(not pg.evaluate(MODAL_OPEN), "Back closes as well")
    narrow = pg.evaluate(PLACE_TEXT_NARROW)
    check(not narrow, f"food & drink: name + line across the full card width, never measured by text width {narrow}")
    # row-wise paging (test-only category with 12 places = 6 rows, 4 visible)
    pg.evaluate(CAT.format("'Long list'"))
    pg.keyboard.press("ArrowRight")
    partial_cards = []
    for _ in range(4):
        pg.keyboard.press("ArrowDown")
        pg.wait_for_timeout(50)  # also wait for the second alignment (setTimeout 0)
        partial_cards += pg.evaluate(NO_PARTIAL)
    check(pg.evaluate(focused) == "Sample place 9", f"4x down -> 9th place ({pg.evaluate(focused)})")
    check(pg.evaluate(VISIBLE_IN_BODY), "the focused place is in view (the list scrolls along)")
    for _ in range(4):
        pg.keyboard.press("ArrowUp")
        pg.wait_for_timeout(50)
        partial_cards += pg.evaluate(NO_PARTIAL)
    check(not partial_cards, f"every step down and up: no half visible card ({partial_cards[:2]})")
    pg.keyboard.press("ArrowLeft")
    check(
        pg.evaluate(focused) == "Long list"
        and pg.evaluate("document.querySelectorAll('#placeGrid .place').length") == 12,
        "left at the edge -> back to the selected category, not another one",
    )
    first = pg.evaluate("document.querySelector('#placeGrid .place b').textContent")
    pg.keyboard.press("ArrowUp")
    second = pg.evaluate("document.querySelector('#placeGrid .place b').textContent")
    check(first != second, "around you: a category change shows other places")
    pg.evaluate(CAT.format("'Unterwegs'"))
    rows = pg.evaluate("[].map.call(document.querySelectorAll('#placeGrid .bus-row'), r => r.textContent)")
    check(
        len(rows) == 5 and "über Market, School" in rows[0] and "jetzt" in rows[0] and "in 7 Min" in rows[1],
        f"getting around: 5 departures from now, gone ones dropped, via stops by name ({rows[:2]})",
    )
    # the row-wise paging counts from the top: timetable + places must fit without paging
    fit = pg.evaluate(
        "(() => { const g = document.getElementById('placeGrid'); return [g.scrollHeight, g.clientHeight]; })()"
    )
    check(fit[0] <= fit[1] + 1, f"getting around fits without paging (timetable + places) {fit}")
    shot(pg, "getting-around-de")
    pg.evaluate(CAT.format("'Draußen'"))
    narrow = pg.evaluate(PLACE_TEXT_NARROW)
    check(
        not narrow and pg.evaluate("document.querySelectorAll('#placeGrid .place').length") == 3,
        f"outdoors: name + line across the full card width {narrow}",
    )
    pg.evaluate("Nav.show('help')")
    check(
        pg.evaluate(focused) == "Kontakt" and pg.evaluate("document.getElementById('helpPhone').offsetParent !== null"),
        "help: rail, contact first",
    )
    pg.keyboard.press("ArrowDown")
    sos = pg.evaluate("[].map.call(document.querySelectorAll('#sosGrid b'), e => e.textContent)")
    check(
        sos[:3] == ["144", "117", "118"] and pg.evaluate("document.querySelectorAll('#docList .doc').length") == 3,
        f"emergency: numbers + doctor/pharmacy/hospital ({sos})",
    )
    pg.keyboard.press("ArrowDown")
    shot(pg, "help-guestcard-de")
    for _ in range(2):
        pg.keyboard.press("ArrowDown")
    pg.keyboard.press("ArrowRight")
    a1 = pg.evaluate("document.getElementById('faqText').textContent")
    pg.keyboard.press("ArrowDown")
    a2 = pg.evaluate("document.getElementById('faqText').textContent")
    check(a1 and a2 and a1 != a2, "FAQ: the answer follows the focus")
    pg.evaluate("Nav.show('weather')")
    check(pg.evaluate("document.querySelectorAll('#wMountain .w-mtn').length") == 2, "weather: 2 mountain points")
    check(
        pg.evaluate("document.querySelectorAll('#weatherDays .wx-dayrow').length") == 4,
        "weather: today + 3 days from the state",
    )
    check(
        pg.evaluate("document.querySelectorAll('#wxRail .cat-item').length") == 2,
        "weather rail: weather + webcams only",
    )
    pg.keyboard.press("ArrowRight")
    check(pg.evaluate("document.getElementById('wxDay').textContent") == "Heute", "right -> day 'Heute' (today)")
    pg.keyboard.press("ArrowDown")
    day = pg.evaluate("document.getElementById('wxDay').textContent")
    check(
        pg.evaluate("document.getElementById('wTemp').textContent") == "8°" and day == "Morgen",
        f"next day selected: 'Morgen' (tomorrow) + max ({day})",
    )
    mt = pg.evaluate("[].map.call(document.querySelectorAll('#wMountain .mt'), e => e.textContent)")
    check(mt == ["3° / -4°", "-1° / -7°"], f"mountain on the next day: max/min ({mt})")
    shot(pg, "weather-day-de")
    # Only correct information: an old state (Pi/internet gone for long) shows no "now",
    # a past day is never "Today"
    st_old = state(GUEST)
    st_old["weather"]["updated"] = st_old["weather"]["mtnUpdated"] = "2000-01-01T10:00+01:00"
    st_old["weather"]["today"]["date"] = DAY(-1)
    pg2, errors2 = page_with(st_old)
    check(
        pg2.evaluate("document.getElementById('tileWxSub').textContent") == "",
        "old state: no current temperature on the tile",
    )
    pg2.evaluate("Nav.show('weather')")
    rows = pg2.evaluate("[].map.call(document.querySelectorAll('#weatherDays .wx-dayrow .d'), e => e.textContent)")
    check(rows[:1] == ["Morgen"] and len(rows) == 3, f"yesterday drops out, no false 'Heute' ({rows})")
    mt = pg2.evaluate("[].map.call(document.querySelectorAll('#wMountain .mt'), e => e.textContent)")
    check(len(mt) == 2 and all("/" in x for x in mt), f"mountain without old current values ({mt})")
    check(not errors2, f"no JS errors with an old state ({errors2[:2]})")
    pg2.close()
    # Midnight: the selected weather day stays the same date, even when "yesterday" drops out of the list
    t, t1 = date.today(), date.today() + timedelta(days=1)
    pg3, errors3 = page_with(state(GUEST), clock=datetime(t.year, t.month, t.day, 23, 50))
    pg3.evaluate("Nav.show('weather')")
    for key in ("ArrowRight", "ArrowDown", "ArrowDown"):  # today -> tomorrow -> the day after
        pg3.keyboard.press(key)
    pg3.clock.set_fixed_time(datetime(t1.year, t1.month, t1.day, 0, 5))
    pg3.evaluate("Data.poll()")
    pg3.wait_for_timeout(500)
    sel = pg3.evaluate(
        "[].map.call(document.querySelectorAll('#weatherDays .wx-dayrow'),"
        " b => [b.classList.contains('selected'), b.classList.contains('focused')])"
    )
    day = pg3.evaluate("document.getElementById('wxDay').textContent")
    check(
        len(sel) == 3 and sel[1] == [True, True] and day == "Morgen",
        f"after midnight the selected day stays selected, now 'Morgen' ({day}, {sel})",
    )
    check(not errors3, f"no JS errors at midnight ({errors3[:2]})")
    pg3.close()
    pg.keyboard.press("ArrowLeft")
    pg.keyboard.press("ArrowDown")
    srcs = pg.evaluate("[].map.call(document.querySelectorAll('#camGrid img'), i => i.getAttribute('src'))")
    pi = pg.evaluate("GUEST_TV_CONFIG.piUrl")
    check(
        len(srcs) == 3 and srcs[0].startswith(f"{pi}/cams/village.jpg?v="),
        f"webcams: 3 images from the Pi ({srcs[:1]})",
    )
    pg.keyboard.press("ArrowRight")
    pg.keyboard.press("Enter")
    big = pg.evaluate("(document.getElementById('camBig') || {}).src || ''")
    check(pg.evaluate(MODAL_OPEN) and "/cams/village.jpg" in big, f"webcam large ({big[-40:]})")
    pg.keyboard.press("ArrowRight")
    check("/cams/lake.jpg" in pg.evaluate("document.getElementById('camBig').src"), "right -> next webcam")
    shot(pg, "webcam-large-de")
    pg.keyboard.press("Escape")
    check(
        not pg.evaluate(MODAL_OPEN) and pg.evaluate("document.querySelector('.cam.focused') !== null"),
        "Back: focus back on the webcam",
    )
    pg.evaluate("Nav.show('home'); Nav.setFocus(document.getElementById('tileBus'))")
    pg.keyboard.press("Enter")
    check(
        pg.evaluate("Nav.screen()") == "around"
        and pg.evaluate(focused) == "Unterwegs"
        and pg.evaluate("document.querySelectorAll('#placeGrid .bus-row').length") == 5,
        "bus tile -> around you, section getting around with the timetable",
    )

    # ---- 3) Settings: arrow = value, sent to the Pi automatically after a pause ----
    pg.evaluate("Nav.show('settings')")
    check(
        relay(pg, "status") == {"op": "status"},
        "settings opened -> actual values requested from the Pi",
    )
    check(
        pg.evaluate("document.querySelector('[data-set-key=sleep] .s-cur').classList.contains('unknown')"),
        "TV value unknown (Pi: null) -> grey, never as active",
    )
    pg.keyboard.press("ArrowRight")
    check(
        pg.evaluate("document.querySelector('[data-set-key=sleep] .s-cur').classList.contains('pending')"),
        "setting picked = pending (not on the TV yet)",
    )
    pg.wait_for_timeout(1900)
    check(
        relay(pg, "sleep") == {"op": "sleep", "value": "30"},
        f"sleep timer 30 sent automatically ({relay(pg, 'sleep')})",
    )
    check(
        not pg.evaluate("document.querySelector('[data-set-key=sleep] .s-cur').classList.contains('pending')"),
        "after applying no longer pending",
    )
    check(
        pg.evaluate("!document.getElementById('toast').hidden"),
        "confirmation shown as a notice",
    )
    check(
        pg.evaluate("localStorage.getItem('guesttv.pictureChanged')") == "1",
        "change remembered for the reset at the next guest",
    )
    # take over actual values from the Pi (brightness/eye comfort), unknown ones (null) stay
    pg.evaluate(
        "Luna._mock.status = {ok: true, sleep: null, energy: 'dim', eye: 'on'}; Nav.show('home'); Nav.show('settings')"
    )
    pg.wait_for_timeout(100)
    cur = pg.evaluate("[].map.call(document.querySelectorAll('.s-cur'), e => e.textContent)")
    check(
        cur == ["30 Minuten", "Gedimmt", "An"],
        f"actual values from the TV shown ({cur})",
    )
    names = pg.evaluate("[].map.call(document.querySelectorAll('.s-name'), e => e.textContent)")
    check(
        names == ["Sleep-Timer", "Bildschirmhelligkeit", "Augenschonender Modus"],
        f"row names ({names})",
    )
    # Screen brightness: 5 steps in a fixed order, right wraps around; only sent after the pause,
    # and only the step you stop at
    pg.evaluate("Nav.setFocus(document.querySelector('[data-set-key=energy]'))")
    steps = []
    for _ in range(5):
        pg.keyboard.press("ArrowRight")
        steps.append(pg.evaluate("document.querySelector('[data-set-key=energy] .s-cur').textContent"))
    check(
        steps == ["Dunkel", "Automatisch", "Hell", "Mittel", "Gedimmt"],
        f"brightness: 5 steps in order, wrapping ({steps})",
    )
    pg.keyboard.press("ArrowLeft")
    pg.wait_for_timeout(1900)
    check(
        relay(pg, "energy") == {"op": "energy", "value": "medium"} and sends(pg, "energy") == 1,
        f"brightness medium = exactly one request 'medium' ({relay(pg, 'energy')}, {sends(pg, 'energy')}x)",
    )
    pg.evaluate("Nav.setFocus(document.querySelector('[data-set-key=eye]'))")
    pg.keyboard.press("ArrowRight")
    pg.wait_for_timeout(1900)
    check(
        relay(pg, "eye") == {"op": "eye", "value": "off"}
        and pg.evaluate("document.querySelector('[data-set-key=eye] .s-cur').textContent") == "Aus",
        f"eye comfort on -> off sent ({relay(pg, 'eye')})",
    )
    # an answer that was in flight during a new pick does not overwrite the pick (seq)
    pg.evaluate(
        "Luna._mock.status = {ok: true, sleep: '90', energy: null, eye: null}; Nav.show('home'); Nav.show('settings');"
        "const row = document.querySelector('[data-set-key=sleep]'); row.querySelectorAll('.s-arrow')[1].click(); row.click();"
    )
    pg.wait_for_timeout(100)
    check(
        pg.evaluate("document.querySelector('[data-set-key=sleep] .s-cur').textContent") == "60 Minuten"
        and relay(pg, "sleep") == {"op": "sleep", "value": "60"},
        "an outdated actual-value answer does not overwrite the new pick",
    )
    pg.evaluate("Luna._mock.status = {ok: true, sleep: null, energy: null, eye: null}")
    # a pending guest-change reset first; if it fails, the setting does not go out either
    pg.evaluate(
        "localStorage.setItem('guesttv.resetPending', '1'); localStorage.setItem('guesttv.pictureChanged', '');"
        "Luna._mockFail.reset = true; Nav.setFocus(document.querySelector('[data-set-key=eye]'))"
    )
    n_reset, n_eye = sends(pg, "reset"), sends(pg, "eye")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(200)
    check(
        sends(pg, "reset") == n_reset + 1
        and sends(pg, "eye") == n_eye
        and pg.evaluate("localStorage.getItem('guesttv.resetPending')") == "1",
        "reset fails -> the setting waits, the reset stays pending",
    )
    pg.evaluate("Luna._mockFail.reset = false")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(200)
    log = pg.evaluate("Luna._relayLog().map(b => b.op)")
    check(
        log[-2:] == ["reset", "eye"] and pg.evaluate("localStorage.getItem('guesttv.resetPending')") == "",
        f"first the reset, then the setting ({log[-3:]})",
    )
    # the change is remembered BEFORE sending: a lost answer also triggers the reset at the next guest
    pg.evaluate("localStorage.setItem('guesttv.pictureChanged', ''); Luna._mockFail.eye = true")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(200)
    check(
        pg.evaluate("localStorage.getItem('guesttv.pictureChanged')") == "1",
        "change remembered before the answer",
    )
    pg.evaluate("Luna._mockFail.eye = false")
    # only ONE reset in flight: a poll during the setting joins it, the marker stays
    pg.evaluate(
        "localStorage.setItem('guesttv.resetPending', '1'); localStorage.setItem('guesttv.pictureChanged', '');"
        "Luna._mockDelay.reset = 400; Nav.setFocus(document.querySelector('[data-set-key=eye]'))"
    )
    n_reset = sends(pg, "reset")
    pg.keyboard.press("Enter")
    pg.evaluate("Data.poll()")
    pg.wait_for_timeout(1000)
    check(
        sends(pg, "reset") == n_reset + 1
        and pg.evaluate("localStorage.getItem('guesttv.pictureChanged')") == "1"
        and pg.evaluate("localStorage.getItem('guesttv.resetPending')") == "",
        f"one reset for setting + poll, the marker stays ({sends(pg, 'reset') - n_reset} resets)",
    )
    # status sync while a confirmed pick still waits behind the reset: the TV answer (state before the
    # write) does not overwrite the pick, in the end the row shows what the TV has
    pg.evaluate(
        "Luna._mock.status = {ok: true, sleep: 'off', energy: 'auto', eye: 'off'};"
        "localStorage.setItem('guesttv.resetPending', '1'); localStorage.setItem('guesttv.pictureChanged', '');"
        "Nav.setFocus(document.querySelector('[data-set-key=eye]'))"
    )
    if pg.evaluate("document.querySelector('[data-set-key=eye] .s-cur').textContent") != "Aus":
        pg.keyboard.press("ArrowRight")  # start from "off", so that the pick "on" differs from the reset value
    pg.keyboard.press("ArrowRight")
    pg.evaluate("Luna._mockDelay.reset = 400")
    pg.keyboard.press("Enter")
    pg.evaluate("Nav.show('home'); Nav.show('settings')")
    pg.wait_for_timeout(1000)
    eye = pg.evaluate(
        "(() => { const c = document.querySelector('[data-set-key=eye] .s-cur');"
        " return [c.textContent, c.classList.contains('pending'), c.classList.contains('unknown')]; })()"
    )
    log = pg.evaluate("Luna._relayLog().map(b => b.op + (b.value ? ':' + b.value : ''))")
    check(
        eye == ["An", False, False] and log[-3:] == ["reset", "status", "eye:on"],
        f"status sync during reset + waiting pick: the row shows the TV value On ({eye}, {log[-3:]})",
    )
    pg.evaluate("Luna._mockDelay.reset = 0; Luna._mock.status = {ok: true, sleep: null, energy: null, eye: null}")

    # ---- 4) Language list: open, focus stays inside, Back closes, OK picks ----
    pg.evaluate("Nav.show('home'); Nav.setFocus(document.getElementById('langToggle'))")
    check(
        relay(pg, "lang") == {"op": "lang", "value": "de"},
        "the TV language follows the guest (de)",
    )
    pg.keyboard.press("Enter")
    check(
        pg.evaluate(MENU_OPEN) and pg.evaluate(focused) == "de",
        "OK opens the list, focus on the active language",
    )
    shot(pg, "home-de-language-list")
    names = pg.evaluate("[].map.call(document.querySelectorAll('#langMenu .lang-opt'), e => e.textContent)")
    check(
        names == ["Deutsch", "English", "Français", "Italiano", "Nederlands", "Polski"],
        f"list in each language's own name ({names})",
    )
    for key in ["ArrowLeft", "ArrowUp", "ArrowUp", "ArrowRight"] + ["ArrowDown"] * 8:
        pg.keyboard.press(key)
    check(
        pg.evaluate(IN_MENU) and pg.evaluate(focused) == "pl",
        "focus stays in the list (arrows)",
    )
    check(
        pg.evaluate("Nav.focusables().every(e => document.getElementById('langMenu').contains(e))"),
        "only the list is reachable",
    )
    pg.keyboard.press("Escape")
    check(
        not pg.evaluate(MENU_OPEN) and pg.evaluate("I18N.active()") == "de" and pg.evaluate(focused) == "langToggle",
        "Back closes without a change, focus on the button",
    )
    pg.keyboard.press("Enter")
    pg.keyboard.press("ArrowDown")
    pg.keyboard.press("Enter")
    check(
        pg.evaluate("I18N.active()") == "en" and not pg.evaluate(MENU_OPEN),
        "OK picks English, list closed",
    )
    check(pg.evaluate(focused) == "langToggle", "focus back on the button")
    check(
        "Welcome" in pg.evaluate("document.getElementById('greeting').textContent"),
        "greeting in English",
    )
    check(
        pg.evaluate("localStorage.getItem('guesttv.manualLang')") == "42|en",
        "choice remembered for this booking",
    )
    pg.wait_for_timeout(500)
    check(
        relay(pg, "lang") == {"op": "lang", "value": "de"},
        "TV language not switched immediately",
    )
    pg.wait_for_timeout(1200)
    check(
        relay(pg, "lang") == {"op": "lang", "value": "en"},
        "after 1.5 s: TV language en sent to the Pi",
    )
    check(sends(pg, "lang", "en") == 1, "exactly one language request")
    # pointer: list by click, moving outside does not pull the focus out, a click outside only closes
    pg.locator("#langToggle").click()
    box = pg.locator("#langMenu [data-lang=it]").bounding_box()
    pg.mouse.move(box["x"] + 30, box["y"] + 20)
    pg.mouse.move(box["x"] + 40, box["y"] + 22)
    check(pg.evaluate(focused) == "it", "pointer over an entry -> focus")
    hb = pg.locator("[data-target=help]").bounding_box()
    pg.mouse.move(hb["x"] + 50, hb["y"] + 50)
    pg.mouse.move(hb["x"] + 60, hb["y"] + 60)
    check(pg.evaluate(focused) == "it", "pointer outside: focus stays in the list")
    pg.mouse.click(hb["x"] + 60, hb["y"] + 60)
    check(
        not pg.evaluate(MENU_OPEN) and pg.evaluate("Nav.screen()") == "home" and pg.evaluate("I18N.active()") == "en",
        "a click outside only closes (no tile, no language)",
    )
    pg.locator("#langToggle").click()
    pg.locator("#langMenu [data-lang=fr]").click()
    check(
        pg.evaluate("I18N.active()") == "fr" and not pg.evaluate(MENU_OPEN),
        "pointer click picks Français",
    )

    # ---- 5) The Magic Remote pointer focuses ----
    box = pg.locator("[data-target=help]").bounding_box()
    pg.mouse.move(box["x"] + 50, box["y"] + 50)
    pg.mouse.move(box["x"] + 60, box["y"] + 60)
    check(pg.evaluate(focused) == "help", "pointer over a tile -> focus")

    # ---- 6) Nothing cut, in all 6 languages (with and without consoles) ----
    check(
        list(SETTINGS) == ["sleep", "energy", "eye"] and len(SETTINGS["energy"]) == 5,
        f"setting values read from app.js ({SETTINGS})",
    )
    for lang in LANGS:
        pick_lang(pg, lang)
        for scr in [
            "home",
            "practical",
            "around",
            "weather",
            "help",
            "settings",
            "vacant",
        ]:
            pg.evaluate(f"Nav.show('{scr}')")
            over = pg.evaluate(OVERFLOW_JS)
            # rails: check every section; around you: every detail view; help: every FAQ answer
            if scr in ("around", "weather", "help"):
                n = pg.evaluate("document.querySelectorAll('.screen.active .cat-list .cat-item').length")
                for i in range(n):
                    pg.evaluate(f"Nav.setFocus(document.querySelectorAll('.screen.active .cat-list .cat-item')[{i}])")
                    over += pg.evaluate(OVERFLOW_JS)
                    if scr == "around":
                        for j in range(pg.evaluate("document.querySelectorAll('#placeGrid .place.has-more').length")):
                            pg.evaluate(f"document.querySelectorAll('#placeGrid .place.has-more')[{j}].click()")
                            over += pg.evaluate(OVERFLOW_JS)
                            pg.keyboard.press("Escape")
            if scr == "help":
                pg.evaluate("Nav.setFocus([].slice.call(document.querySelectorAll('#helpRail .cat-item')).pop())")
                n = pg.evaluate("document.querySelectorAll('#faqList .faq-q').length")
                for i in range(n):
                    pg.evaluate(f"Nav.setFocus(document.querySelectorAll('#faqList .faq-q')[{i}])")
                    over += pg.evaluate(OVERFLOW_JS)
            if scr == "home":
                shot(pg, f"layout-home-{lang}")
                pg.evaluate("document.getElementById('langToggle').click()")
                over += pg.evaluate(OVERFLOW_JS)
                pg.keyboard.press("Escape")
            if scr == "settings":  # put in every label of every row (brightness: the longest texts)
                shot(pg, f"layout-settings-{lang}")
                for key, values in SETTINGS.items():
                    for v in values:
                        pg.evaluate(
                            f"document.querySelector('[data-set-key={key}] .s-cur').textContent = I18N.t('set.{key}.{v}')"
                        )
                        over += pg.evaluate(OVERFLOW_JS) + pg.evaluate(ONE_LINE_JS)
            check(not over, f"{lang}/{scr}: nothing cut {sorted(set(over))[:4]}")
        check(pg.evaluate("I18N.active()") == lang, f"language {lang} picked from the list")
    # a TV without consoles (e.g. a second room): no tile, the row as before
    pg.evaluate("Luna._mock.inputs = {ok: true, inputs: []}; Nav.show('home'); Data.poll()")
    pg.wait_for_timeout(300)
    check(
        pg.evaluate("document.getElementById('tileGaming').hidden"),
        "empty list -> no gaming tile",
    )
    for lang in LANGS:
        pick_lang(pg, lang)
        over = pg.evaluate(OVERFLOW_JS)
        check(
            not over,
            f"{lang}/home without consoles: nothing cut {sorted(set(over))[:4]}",
        )
    shot(pg, "home-pl-no-consoles")
    check(not errors, f"no JS/console errors ({errors[:2]})")
    pg.close()

    # ---- 6b) No category with "bus": true -> no bus tile, the row stays aligned ----
    no_bus = dict(CONTENT, around=[c for c in CONTENT["around"] if not c.get("bus")])
    pg, errors = page_with(state(GUEST, content=no_bus))
    check(pg.evaluate("document.getElementById('tileBus').hidden"), "no bus category -> no bus tile")
    over = pg.evaluate(OVERFLOW_JS)
    check(not over, f"home without the bus tile: nothing cut {sorted(set(over))[:4]}")
    pg.keyboard.press("ArrowDown")
    check(pg.evaluate(focused) in ("practical", "around"), f"down from TV -> a tile below ({pg.evaluate(focused)})")
    check(not errors, f"no JS errors ({errors[:2]})")
    pg.close()

    # ---- 6c) Lists written as plain text by mistake: shown as one item, the app keeps running ----
    plain = json.loads(json.dumps(CONTENT))
    plain["practical"]["checkout"] = "Leave the keys on the table"
    plain["practical"]["rules"] = {lang: "No smoking" for lang in LANGS}
    plain["help"]["faq"] = "not a list"
    pg, errors = page_with(state(GUEST, content=plain))
    pg.evaluate("Nav.show('practical')")
    check(
        pg.evaluate("[].map.call(document.querySelectorAll('#checkoutList li, #rulesList li'), e => e.textContent)")
        == ["Leave the keys on the table", "No smoking"],
        "check-out list and house rules as plain text: one item each",
    )
    pg.evaluate("Nav.show('help')")
    check(pg.evaluate("document.querySelectorAll('#faqList .faq-q').length") == 0, "FAQ as plain text: no questions")
    check(not errors, f"lists as plain text: no JS errors ({errors[:2]})")
    pg.close()

    # ---- 7) Screensaver: the wake-up key resets the idle timer ----
    pg, errors = page_with(state(GUEST))
    pg.evaluate("GUEST_TV_CONFIG.screensaverAfterSec = 8")
    pg.wait_for_function("Screensaver.isActive()", timeout=20000)
    check(
        pg.evaluate("getComputedStyle(document.getElementById('ssPhoto')).backgroundImage") == "none",
        "no photos in the state -> plain black, no image request",
    )
    before = pg.evaluate(focused)
    pg.keyboard.press("Enter")
    check(not pg.evaluate("Screensaver.isActive()"), "a key wakes the screensaver")
    check(
        pg.evaluate("Nav.screen()") == "home" and pg.evaluate(focused) == before,
        "the wake-up key triggers nothing (no app start)",
    )
    pg.wait_for_timeout(6000)
    check(
        not pg.evaluate("Screensaver.isActive()"),
        "the screensaver does not come back after 5 s",
    )
    # photos listed in the state are shown (data: URI, so no file is needed)
    pg.evaluate(
        f"Data.get().photos = ['data:image/png;base64,{base64.b64encode(PNG_1PX).decode()}']; Screensaver.enter()"
    )
    check(
        "data:image/png" in pg.evaluate("document.getElementById('ssPhoto').style.backgroundImage"),
        "photos from the state -> shown behind the clock",
    )
    pg.evaluate("Screensaver.exit()")
    # an owner's file name with a space or a bracket still makes a valid CSS url (else the screen stays black)
    pg.evaluate("Data.get().photos = ['assets/img/Living room (1).jpg']; Screensaver.enter()")
    check(
        "Living%20room" in pg.evaluate("document.getElementById('ssPhoto').style.backgroundImage")
        or "Living room (1).jpg" in pg.evaluate("document.getElementById('ssPhoto').style.backgroundImage"),
        "photo name with a space and brackets: still set as the background",
    )
    pg.evaluate("Screensaver.exit()")
    check(not errors, f"no JS errors in the screensaver ({errors[:2]})")
    pg.close()

    # ---- 7b) Guest change: the Pi resets the previous guest's picture + sleep timer in ONE request ----
    pg, errors = page_with(state(GUEST), {"guesttv.lastGuest": "41", "guesttv.pictureChanged": "1"})
    check(
        relay(pg, "reset") == {"op": "reset"},
        f"new guest: reset sent to the Pi ({relay(pg, 'reset')})",
    )
    check(
        pg.evaluate("localStorage.getItem('guesttv.resetPending')") in ("", None)
        and pg.evaluate("localStorage.getItem('guesttv.pictureChanged')") in ("", None),
        "reset confirmed: no further attempt",
    )
    check(
        pg.evaluate("localStorage.getItem('guesttv.lastGuest')") == "42",
        "new guest remembered",
    )
    check(sends(pg, "reset") == 1, "exactly one reset")
    pg.close()

    # ---- 7c) A manual language choice holds per booking, also after an app restart ----
    pg, errors = page_with(state(GUEST), {"guesttv.manualLang": "42|fr"})
    check(
        pg.evaluate("I18N.active()") == "fr" and pg.evaluate("I18N.isManual()"),
        "same booking: choice FR restored",
    )
    pg.wait_for_timeout(1700)
    check(
        relay(pg, "lang") == {"op": "lang", "value": "fr"},
        "the TV language follows the restored choice",
    )
    pg.close()
    pg, errors = page_with(state(GUEST), {"guesttv.manualLang": "41|fr"})
    check(
        pg.evaluate("I18N.active()") == "de",
        "another booking: guest language instead of the old choice",
    )
    check(
        pg.evaluate("localStorage.getItem('guesttv.manualLang')") == "",
        "old choice deleted",
    )

    # ---- 7d) TV language: failure -> the next poll repeats, never send a confirmed value twice ----
    pg.wait_for_timeout(1700)
    check(sends(pg, "lang", "de") == 1, "start: guest language sent once")
    pg.evaluate("Luna._mockFail.lang = true")
    pick_lang(pg, "it")
    pg.wait_for_timeout(1700)
    check(sends(pg, "lang", "it") == 1, "choice IT sent (the Pi answers with an error)")
    pg.evaluate("Luna._mockFail.lang = false; Data.poll()")
    pg.wait_for_timeout(300)
    check(sends(pg, "lang", "it") == 2, "the next poll repeats IT")
    pg.evaluate("Data.poll()")
    pg.wait_for_timeout(300)
    check(sends(pg, "lang", "it") == 2, "confirmed: no further request")
    # IT confirmed, EN in flight, back to IT: right after the EN answer IT again (not only at the poll)
    pg.evaluate("Luna._mockDelay.lang = 2500")
    pick_lang(pg, "en")
    pg.wait_for_timeout(1700)
    pick_lang(pg, "it")
    pg.wait_for_timeout(1700)
    check(sends(pg, "lang", "it") == 2, "back to IT: nothing doubled while EN is in flight")
    pg.wait_for_timeout(3500)
    check(
        sends(pg, "lang", "it") == 3 and relay(pg, "lang") == {"op": "lang", "value": "it"},
        "after the EN answer: IT sent right away",
    )
    pg.evaluate("Luna._mockDelay.lang = 0")
    check(not errors, f"no JS errors ({errors[:2]})")
    pg.close()

    # ---- 8) Vacant: OK opens the tiles without changing the language; TV language back ----
    pg, errors = page_with(state(None))
    check(pg.evaluate("Nav.screen()") == "vacant", "no guest -> vacant")
    check(
        pg.evaluate("document.querySelector('.vacant-title').textContent") == "Welcome"
        and pg.evaluate("document.querySelectorAll('.vacant-sub, #scr-vacant img').length") == 0,
        "vacant: welcome line, no tagline, no logo",
    )
    check(
        pg.evaluate("document.querySelector('.vacant-hint').textContent") != "",
        "the key hint stays",
    )
    c = pg.evaluate(
        "(r => [(r.left + r.right) / 2, (r.top + r.bottom) / 2])(document.querySelector('.vacant-wrap').getBoundingClientRect())"
    )
    check(abs(c[0] - 960) <= 2 and abs(c[1] - 540) <= 40, f"vacant: centred on the screen ({c[0]:.0f}, {c[1]:.0f})")
    lang = pg.evaluate("I18N.active()")
    pg.wait_for_timeout(1700)
    check(
        relay(pg, "lang") == {"op": "lang", "value": "reset"},
        "vacant: TV language back to the original",
    )
    pg.keyboard.press("Enter")
    check(pg.evaluate("Nav.screen()") == "home", "OK on the vacant screen -> tiles")
    check(
        pg.evaluate("I18N.active()") == lang,
        "OK on the vacant screen does not change the language",
    )
    check(
        pg.evaluate("document.getElementById('greeting').textContent") == pg.evaluate("I18N.t('greet.vacant')"),
        "vacant: neutral greeting",
    )
    check(
        pg.evaluate("document.getElementById('checkoutBanner').hidden"),
        "vacant: no check-out notice",
    )
    # Magic Remote pointer: the centre button is then a mouse click, not a key
    pg.evaluate("Nav.show('vacant')")
    pg.mouse.click(960, 600)
    check(pg.evaluate("Nav.screen()") == "home", "pointer click on the vacant screen -> tiles")
    check(not errors, f"no JS errors ({errors[:2]})")
    pg.close()

    # ---- 9) Departure day: check-out notice until 11:00, opens the checklist ----
    pg, errors = page_with(state(CHECKOUT_GUEST), clock=datetime(2026, 11, 6, 9, 0))
    check(
        not pg.evaluate("document.getElementById('checkoutBanner').hidden"),
        "departure day 09:00: notice visible",
    )
    check(
        pg.evaluate("document.getElementById('stayinfo').hidden"),
        "the notice replaces the stay line",
    )
    check(
        "bis 11:00" in pg.evaluate("document.getElementById('checkoutBanner').textContent"),
        "text in the guest language (de)",
    )
    shot(pg, "checkout-day-de")
    pg.evaluate("Nav.setFocus(document.querySelector('[data-app=tv]'))")
    pg.keyboard.press("ArrowUp")
    check(pg.evaluate(focused) == "checkoutBanner", "up from TV -> notice")
    pg.keyboard.press("Enter")
    check(
        pg.evaluate("Nav.screen()") == "practical"
        and pg.evaluate("document.querySelectorAll('#checkoutList li').length") == 4,
        "OK opens the check-out list",
    )
    pg.keyboard.press("Escape")
    # the notice disappears (11:00) while it has the focus: OK only brings the focus back
    pg.evaluate(
        "Nav.show('home'); Nav.setFocus(document.getElementById('checkoutBanner'));"
        "document.getElementById('checkoutBanner').hidden = true"
    )
    pg.keyboard.press("Enter")
    check(
        pg.evaluate("Nav.screen()") == "home"
        and pg.evaluate("Luna._lastResults()['luna://com.webos.applicationManager/launch']") is None
        and pg.evaluate(focused) == "tv",
        "focus gone: OK starts nothing, focus back on the first tile",
    )
    pg.evaluate("document.getElementById('checkoutBanner').hidden = false")
    for lang in LANGS:
        pick_lang(pg, lang)
        pg.evaluate("Nav.show('home')")
        over = pg.evaluate(OVERFLOW_JS)
        check(not over, f"{lang}: the notice fits {sorted(set(over))[:4]}")
    pg.clock.set_fixed_time(datetime(2026, 11, 6, 11, 0))
    pg.wait_for_timeout(10500)  # no poll: the 10 s timer alone must end the stay (the next poll can be 5 min away)
    check(
        pg.evaluate("Nav.screen()") == "vacant"
        and "Example" not in pg.evaluate("document.getElementById('hdr').textContent"),
        "11:00 without a poll: vacant, the departed guest's name and stay line are gone",
    )
    pg.evaluate("Data.poll()")
    pg.wait_for_timeout(300)
    check(
        pg.evaluate("document.getElementById('checkoutBanner').hidden"),
        "from 11:00 no notice",
    )
    check(pg.evaluate("Nav.screen()") == "vacant", "from 11:00 vacant")
    check(not errors, f"no JS errors ({errors[:2]})")
    pg.close()

    # ---- 9b) Two polls in flight: the newer answer wins, a late older one is ignored ----
    pg, errors = page_with(state(GUEST))
    answers, served = [state(GUEST), state(None)], {"n": 0}

    def in_order(route):  # first request = older state (guest), second = newer state (vacant)
        body = answers[min(served["n"], 1)]
        served["n"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(body),
        )

    pg.unroute("**/state.json")
    pg.route("**/state.json", in_order)
    pg.evaluate(
        """(() => { const orig = window.fetch; let k = 0;
        window.fetch = (u, o) => { const me = ++k; return orig(u, o).then(r => new Promise(res =>
          setTimeout(() => res(r), me === 1 ? 800 : 0))); }; })()"""
    )
    pg.evaluate("Data.poll(); Data.poll()")
    pg.wait_for_timeout(1500)
    check(pg.evaluate("Data.get().guest") is None, "two polls: the newer answer stays, a late older one is ignored")
    check(not errors, f"no JS errors ({errors[:2]})")
    pg.close()

    # ---- 10) The real Pi path like on the TV (XHR instead of the mock) ----
    # text/plain without a CORS preflight, only ONE request in flight, fixed order,
    # errors (500, ok missing, network abort) = cb(null), the queue continues afterwards.
    held, seen = [], []
    PI_INPUTS = {
        "ok": True,
        "inputs": [{"label": "XBOX ONE", "appId": "com.webos.app.hdmi2"}],
    }

    def hold(route):
        seen.append(
            (
                route.request.method,
                route.request.headers.get("content-type", ""),
                route.request.post_data,
            )
        )
        held.append(route)

    def as_tv(pg):
        pg.add_init_script("window.PalmServiceBridge = function () {};")  # Luna.hasWebOS = true
        pg.route("**/tv/setting", hold)

    def answer(i, body, status=200):
        held[i].fulfill(
            status=status,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps(body),
        )

    pg, errors = page_with(state(GUEST), setup=as_tv)
    check(pg.evaluate("Luna.hasWebOS"), "TV mode: requests go to the Pi via XHR")
    check(
        seen[:1] == [("POST", "text/plain;charset=UTF-8", '{"op":"inputs"}')],
        f"POST as text/plain (no preflight needed): {seen[:1]}",
    )
    pg.wait_for_timeout(1700)  # the language request (1.5 s) waits behind the open request
    check(len(seen) == 1, f"only one request in flight ({len(seen)})")
    answer(0, PI_INPUTS)
    pg.wait_for_timeout(300)
    check(
        not pg.evaluate("document.getElementById('tileGaming').hidden"),
        "Pi answer -> gaming tile",
    )
    check(
        len(seen) == 2 and seen[1][2] == '{"op":"lang","value":"de"}',
        f"then the language request ({seen[1:]})",
    )
    answer(1, {"ok": False, "error": "busy"}, status=500)
    pg.evaluate(
        "window.__r = []; ['status', 'eye', 'inputs'].forEach(function (op, i) {"
        " Luna.relay(op === 'eye' ? {op: 'eye', value: 'on'} : {op: op}, function (r) { window.__r[i] = r; }); })"
    )
    pg.wait_for_timeout(200)
    check(len(seen) == 3, "three new requests: one in flight, two waiting")
    answer(2, {"ok": True, "sleep": "60", "energy": None, "eye": "off"})
    pg.wait_for_timeout(200)
    answer(3, {"changed": True})  # ok missing = failure
    pg.wait_for_timeout(200)
    held[4].abort()  # network abort (same path as a timeout: loadend, status 0)
    pg.wait_for_timeout(200)
    r = pg.evaluate("window.__r")
    check(r[0] and r[0].get("sleep") == "60", f"200 + ok:true -> answer ({r[0]})")
    check(r[1] is None and r[2] is None, f"ok missing / abort -> null ({r[1:]})")
    check(
        [x[2] for x in seen[2:]] == ['{"op":"status"}', '{"op":"eye","value":"on"}', '{"op":"inputs"}'],
        "fixed order",
    )
    check(all(m == "POST" for m, _, _ in seen), "no OPTIONS preflight")
    pg.evaluate("Data.poll()")  # the language request (500) is repeated at the next poll
    pg.wait_for_timeout(300)
    done = 5
    while done < len(held):  # answer the poll requests in order (the language waits behind inputs)
        answer(done, PI_INPUTS)
        done += 1
        pg.wait_for_timeout(200)
    check(
        '{"op":"lang","value":"de"}' in [x[2] for x in seen[5:]],
        f"language sent again at the next poll after the error ({seen[5:]})",
    )
    js_errors = [e for e in errors if "Failed to load resource" not in e]  # 500/abort are intended here
    check(not js_errors, f"no JS errors in TV mode ({js_errors[:2]})")
    pg.close()

    # ---- 11) English screens with the sample content only (TV_SHOTS: these are the published screenshots) ----
    guest_en = dict(GUEST, language="en", arrival=DAY(-2), departure=DAY(4))
    qrs = {
        "wlan": qr("WIFI:T:WPA;S:GuestNet;P:example-pass-27;;"),
        "whatsapp": qr(CONTENT["help"]["contact"]["whatsapp"]),
        "guestcard": qr(CONTENT["help"]["guestcard"]["url"]),
        "video": qr(CONTENT["help"]["video"]["url"]),
    }
    pg, errors = page_with(state(guest_en, content=CONTENT, qr=qrs))
    pg.evaluate("Luna._mock.status = {ok: true, sleep: 'off', energy: 'auto', eye: 'off'}")
    check(pg.evaluate("I18N.active()") == "en", "English guest -> English portal")
    for scr, name in [
        ("home", "home-en"),
        ("practical", "practical-en"),
        ("around", "around-en"),
        ("weather", "weather-en"),
        ("help", "help-en"),
        ("settings", "settings-en"),
    ]:
        pg.evaluate(f"Nav.show('{scr}')")
        pg.wait_for_timeout(200)  # settings: the actual values arrive from the relay mock
        shot(pg, name)
    check(
        pg.evaluate("[].map.call(document.querySelectorAll('.s-cur.unknown, .s-cur.pending'), e => e.textContent)")
        == [],
        "settings: actual values known (shown in the accent colour)",
    )
    check(not errors, f"no JS errors on the English screens ({errors[:2]})")
    pg.close()
    pg, errors = page_with(state(None, content=CONTENT))
    shot(pg, "vacant-en")
    check(not errors, f"no JS errors on the English vacant screen ({errors[:2]})")
    pg.close()

    browser.close()
srv.shutdown()

print(f"\nUI WALKTHROUGH {'FAILED (' + str(len(fails)) + ')' if fails else 'PASSED'}")
sys.exit(1 if fails else 0)
