// nav: remote control navigation, spatial focus search in the visible screen.
// LG remote: arrows, OK = 13, Back = 461. 461 only reaches the app because appinfo.json sets
// "disableBackHistoryAPI": true; without it webOS calls history.back() and the app is left
// (LG docs "Back Button"). Browser test: Backspace/Escape = Back.
// Magic Remote (pointer): moving focuses the element below it, a click = OK (a mouse click, not a key).
(function () {
  "use strict";

  var keyHooks = [];      // function(e) -> true = key consumed (screensaver, vacant, settings)
  var activityHooks = []; // function(kind): "key" per key, "pointer" per real pointer move/click
  var lastHomeFocus = null;
  var lastPointer = null; // Chrome fires fake mousemoves at the old position when the DOM changes
  var scope = null;       // open picker (language list, detail view): focus and pointer stay inside

  function visible(el) { return el.offsetParent !== null && !el.disabled; }

  function focusables() {
    if (scope) return [].slice.call(scope.querySelectorAll(".focusable")).filter(visible);
    var scr = document.querySelector(".screen.active");
    if (!scr) return [];
    // screen elements + header (language button): otherwise unreachable with the remote
    var els = [].slice.call(scr.querySelectorAll(".focusable"))
      .concat([].slice.call(document.querySelectorAll("#hdr .focusable")));
    return els.filter(visible);
  }

  function current() {
    var el = document.querySelector(".focusable.focused");
    if (el && visible(el)) return el;
    var all = focusables();
    return all.length ? all[0] : null;
  }

  function setFocus(el) {
    var prev = document.querySelector(".focusable.focused");
    if (prev === el) return;
    if (prev) prev.classList.remove("focused");
    if (!el) { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); return; }
    el.classList.add("focused");
    el.focus();
  }

  function rect(el) { return el.getBoundingClientRect(); }

  // Directional focus: the geometrically nearest element in the key's direction.
  // Score: progress in the direction + 2.5x the sideways offset, bonus for overlap.
  function moveFocus(dir, els) {
    els = els || focusables();
    var from = current();
    if (!from) { if (els.length) setFocus(els[0]); return !!els.length; }
    if (!from.classList.contains("focused")) { setFocus(from); return true; }
    var fr = rect(from), fcx = fr.left + fr.width / 2, fcy = fr.top + fr.height / 2;
    var dx = dir === "left" ? -1 : dir === "right" ? 1 : 0;
    var dy = dir === "up" ? -1 : dir === "down" ? 1 : 0;
    var best = null, bestScore = null;
    els.forEach(function (el) {
      if (el === from) return;
      var r = rect(el), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      var mx = cx - fcx, my = cy - fcy;
      var proj = mx * dx + my * dy;
      if (proj <= 4) return;
      var ortho = Math.abs(mx * dy) + Math.abs(my * dx);
      var overlap =
        (dy !== 0 && r.left < fr.right && r.right > fr.left) ||
        (dx !== 0 && r.top < fr.bottom && r.bottom > fr.top);
      var score = proj + ortho * 2.5 - (overlap ? 120 : 0);
      if (bestScore === null || score < bestScore) { bestScore = score; best = el; }
    });
    if (best) { setFocus(best); return true; }
    return false;
  }

  function screenName() {
    var s = document.querySelector(".screen.active");
    return s ? s.getAttribute("data-screen") : null;
  }

  function show(name) {
    var scr = document.querySelector('.screen[data-screen="' + name + '"]');
    if (!scr) return;
    var cur = document.querySelector(".screen.active");
    // already visible (e.g. back from Netflix): the focus stays where it was
    if (cur === scr) { var f0 = document.querySelector(".focusable.focused"); if (f0 && visible(f0)) return; }
    if (cur && cur !== scr) {
      if (cur.getAttribute("data-screen") === "home") lastHomeFocus = document.querySelector(".focusable.focused");
      cur.classList.remove("active");
    }
    scr.classList.add("active");
    document.body.setAttribute("data-screen", name);
    // back to home: focus on the tile you came from
    var f = (name === "home" && lastHomeFocus && scr.contains(lastHomeFocus)) ? lastHomeFocus : scr.querySelector(".focusable");
    setFocus(f || null);
    if (window.App && window.App.onScreen) window.App.onScreen(name);
  }

  function activity(kind) {
    for (var i = 0; i < activityHooks.length; i++) activityHooks[i](kind);
  }

  function onKey(e) {
    activity("key");
    for (var i = 0; i < keyHooks.length; i++) {
      if (keyHooks[i](e)) { e.preventDefault(); return; }
    }
    var handled = true;
    switch (e.keyCode) {
      case 37: moveFocus("left"); break;
      case 39: moveFocus("right"); break;
      case 38: moveFocus("up"); break;
      case 40: moveFocus("down"); break;
      // OK (preventDefault: no double click). If the focused element has disappeared (e.g. the
      // check-out notice at 11:00), OK first brings the focus back instead of blindly starting the first tile.
      case 13: var el = document.querySelector(".focusable.focused");
        if (el && visible(el)) el.click(); else setFocus(current()); break;
      case 8: case 27: case 461: if (screenName() !== "home") show("home"); break; // Back
      // 405 = the YELLOW colour key in the webOS key table; deliberately "back to the portal":
      // remotes without colour keys never send it, and if one does, that is harmless.
      case 405: show("home"); break;
      default: handled = false;
    }
    if (handled) e.preventDefault();
  }

  function onPointerMove(e) {
    if (lastPointer && lastPointer.x === e.screenX && lastPointer.y === e.screenY) return;
    lastPointer = { x: e.screenX, y: e.screenY };
    activity("pointer");
    var t = e.target && e.target.closest ? e.target.closest(".focusable") : null;
    if (t && visible(t) && (!scope || scope.contains(t))) setFocus(t);
  }

  function focusIndex() { return focusables().indexOf(document.querySelector(".focusable.focused")); }

  // After a DOM rebuild (poll, language change): restore the focus at the same position
  function refocus(idx) {
    var el = document.querySelector(".focusable.focused");
    if (el && visible(el)) return;
    var all = focusables();
    if (all.length) setFocus(all[Math.max(0, Math.min(idx, all.length - 1))]);
  }

  function init() {
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousemove", onPointerMove);
    document.addEventListener("mousedown", function () { activity("pointer"); });
  }

  window.Nav = {
    init: init, show: show, screen: screenName,
    moveFocus: moveFocus, current: current, setFocus: setFocus,
    focusIndex: focusIndex, refocus: refocus, focusables: focusables,
    setScope: function (el) { scope = el; },
    addKeyHook: function (fn) { keyHooks.push(fn); },
    onActivity: function (fn) { activityHooks.push(fn); }
  };
})();
