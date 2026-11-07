// screensaver: after X min without input, clock, date and weather (plus rotating photos, if the state
// lists any; without photos it is plain black). The clock box moves every minute (OLED: no static image).
// Visibility via the class .on (the hidden attribute loses against display rules).
// Note: LG's own screensaver may kick in earlier on OLED (LG docs "Screen Saver": "on OLED TVs,
// the screensaver timeout is just a few minutes"); that is fine.
(function () {
  "use strict";
  var CFG = window.GUEST_TV_CONFIG;
  var active = false;
  var photoIdx = 0, ticks = 0;
  var rotTimer = null, tickTimer = null;
  var lastActivity = Date.now();

  function $(id) { return document.getElementById(id); }

  // state.photos (e.g. files the owner put into app/assets/img/) or none: no bundled default, no 404s
  function photos() {
    var st = window.Data && window.Data.get();
    return (st && st.photos && st.photos.length) ? st.photos : [];
  }
  function showPhoto() {
    var p = photos();
    // quoted: a file name with a space or a bracket would make an invalid CSS url (black screen)
    $("ssPhoto").style.backgroundImage = p.length ? "url(" + JSON.stringify(String(p[photoIdx % p.length])) + ")" : "none";
  }

  function render() {
    var now = new Date();
    var st = window.Data ? window.Data.get() : null;
    $("ssClock").textContent = I18N.time(now);
    $("ssDate").textContent = I18N.date(now, { weekday: "long", day: "numeric", month: "long" });
    // only fresh "now" values (older than 3 h = Pi/internet gone): otherwise no weather line
    var upd = st && st.weather ? new Date(st.weather.updated).getTime() : NaN;
    var w = !isNaN(upd) && Date.now() - upd < 3 * 3600 * 1000 ? st.weather.current : null;
    $("ssWeather").textContent = (w && w.temp !== null && w.temp !== undefined)
      ? Math.round(w.temp) + "° · " + I18N.wxText(w.code) : "";
  }

  // place the clock box randomly in the safe area (box 900 px wide, about 300 px high)
  function move() {
    var box = $("ssBox");
    box.style.left = Math.round(80 + Math.random() * 860) + "px";
    box.style.top = Math.round(90 + Math.random() * 600) + "px";
  }

  function rotate() {
    if (!photos().length) return; // plain black: nothing to fade
    photoIdx++;
    var el = $("ssPhoto");
    el.style.opacity = "0";
    setTimeout(function () {
      showPhoto();
      el.style.opacity = "0.3";
    }, 1000);
  }

  function enter() {
    if (active) return;
    active = true;
    document.body.classList.add("hide-focus");
    showPhoto();
    render(); move();
    $("screensaver").classList.add("on");
    rotTimer = setInterval(rotate, 15000);
    ticks = 0;
    tickTimer = setInterval(function () {
      render();
      if (++ticks % 6 === 0) move(); // a new position every minute
    }, 10000);
  }

  function exit() {
    lastActivity = Date.now();
    if (!active) return;
    active = false;
    clearInterval(rotTimer); clearInterval(tickTimer);
    $("screensaver").classList.remove("on");
    document.body.classList.remove("hide-focus");
  }

  function watch() {
    setInterval(function () {
      if (!active && Date.now() - lastActivity >= CFG.screensaverAfterSec * 1000) enter();
    }, 5000);
  }

  // Every input resets the idle counter; a pointer move wakes up directly.
  Nav.onActivity(function (kind) {
    lastActivity = Date.now();
    if (kind === "pointer") exit();
  });
  // Wake-up key: only wake up, do not also trigger the focused tile.
  // (First key hook, because this script loads before app.js.)
  Nav.addKeyHook(function () {
    if (!active) return false;
    exit();
    return true;
  });

  window.Screensaver = { enter: enter, exit: exit, watch: watch, isActive: function () { return active; } };
})();
