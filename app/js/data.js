// data: state.json from the Pi (fetch, CORS) + local files (XHR: works for file:// on webOS,
// fetch is blocked there) + cache fallback + default state.
(function () {
  "use strict";
  var CFG = window.GUEST_TV_CONFIG;
  var state = null;
  var source = "none";       // pi | cache | default
  var pollTimer = null;
  var onChange = null;

  // Local app files (i18n/, default-state.json) via XHR: on the TV the app runs from file://,
  // where fetch() refuses local URLs but XHR works (status 0 = success for file://).
  function localGet(path, cb) {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", path, true);
      xhr.onload = function () {
        if (xhr.status === 200 || xhr.status === 0) {
          try { cb(JSON.parse(xhr.responseText)); return; } catch (e) {}
        }
        cb(null);
      };
      xhr.onerror = function () { cb(null); };
      xhr.send(null);
    } catch (e) { cb(null); }
  }

  function fetchState(cb) {
    var url = CFG.piUrl + "/state.json";
    var done = false;
    var to = setTimeout(function () { if (!done) { done = true; cb(null); } }, 6000);
    fetch(url, { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (j) { if (!done) { done = true; clearTimeout(to); cb(j); } })
      .catch(function () { if (!done) { done = true; clearTimeout(to); cb(null); } });
  }

  function persist(j) {
    try { window.localStorage.setItem("guesttv.state", JSON.stringify(j)); } catch (e) {}
  }
  function readCache() {
    try {
      var raw = window.localStorage.getItem("guesttv.state");
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function guestId(s) { return s && s.guest ? String(s.guest.reservationId || "?") : null; }

  function apply(j, src) {
    // guest change = another booking OR the change guest <-> vacant (also on the first load)
    var guestChanged = !state || guestId(state) !== guestId(j);
    state = j;
    source = src;
    if (src === "pi") persist(j);
    if (onChange) onChange(state, source, guestChanged);
  }

  // Polls can overlap (timer, return from an app, start): only the newest one may apply its answer, a late
  // older answer would bring back a state that is already over (e.g. the departed guest).
  var pollSeq = 0;
  function poll() {
    var my = ++pollSeq;
    fetchState(function (j) {
      if (my !== pollSeq) return;
      if (j) { apply(j, "pi"); return; }
      // Pi gone: the last state in memory wins over localStorage (that one can be older
      // or empty, e.g. when storage was blocked)
      if (state && (source === "pi" || source === "cache")) { apply(state, "cache"); return; }
      var cache = readCache();
      if (cache) { apply(cache, "cache"); return; }
      // Never reached the Pi: vacant + content from the app package (the build copies content.json
      // from pi/content/: ONE source for the Pi and the offline start).
      localGet("default-state.json", function (d) {
        if (!d) return;
        localGet("content.json", function (c) {
          if (source === "pi" || source === "cache") return; // real data arrived meanwhile: do not overwrite
          if (c && !d.content) d.content = c;
          apply(d, "default");
        });
      });
    });
  }

  function start(cb) {
    onChange = cb;
    poll();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, CFG.pollSeconds * 1000);
  }

  window.Data = {
    start: start, poll: poll, localGet: localGet,
    get: function () { return state; },
    source: function () { return source; }
  };
})();
