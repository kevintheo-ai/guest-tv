// app: orchestrator. Boot, then render every screen from state + i18n + content.
(function () {
  "use strict";
  var CFG = window.GUEST_TV_CONFIG;

  /* ---------- Icons (inline SVG, line drawings) ---------- */
  var CLOUD_UP = '<path d="M14 30a7 7 0 1 1 2.1-13.7A10 10 0 0 1 35 19a5.5 5.5 0 0 1-.5 11z"/>';
  var I = {
    tv: '<svg viewBox="0 0 48 48"><rect x="5" y="11" width="38" height="25" rx="3"/><path d="M17 42h14M24 36v6M15 4l9 7 9-7"/></svg>',
    play: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="18"/><path d="M20 16.5l12 7.5-12 7.5z"/></svg>',
    wifi: '<svg viewBox="0 0 48 48"><path d="M7 19a25 25 0 0 1 34 0M13 26a16.5 16.5 0 0 1 22 0M19 33a8 8 0 0 1 10 0"/><circle cx="24" cy="39" r="1.8"/></svg>',
    mtn: '<svg viewBox="0 0 48 48"><path d="M3 39L18 14l8 12 5-7 14 20z"/><path d="M14.5 20l3.5 3 3-3"/></svg>',
    help: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="18"/><path d="M19.5 19.5a4.6 4.6 0 1 1 6.6 4.1c-1.5.8-2.1 1.7-2.1 3.4"/><circle cx="24" cy="33" r="1.4"/></svg>',
    gear: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="6.5"/><path d="M24 5v6M24 37v6M5 24h6M37 24h6M10.6 10.6l4.2 4.2M33.2 33.2l4.2 4.2M37.4 10.6l-4.2 4.2M14.8 33.2l-4.2 4.2"/></svg>',
    globe: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="18"/><path d="M6 24h36M24 6c5 5 7 11 7 18s-2 13-7 18c-5-5-7-11-7-18s2-13 7-18z"/></svg>',
    pad: '<svg viewBox="0 0 48 48"><path d="M16 14h16a9 9 0 0 1 8.7 6.8l2.6 10.4a4.6 4.6 0 0 1-7.8 4.2L30 30H18l-5.5 5.4a4.6 4.6 0 0 1-7.8-4.2l2.6-10.4A9 9 0 0 1 16 14z"/><path d="M15 19.5v7M11.5 23h7"/><circle cx="31" cy="20.5" r="1.6"/><circle cx="35" cy="24.5" r="1.6"/></svg>',
    chev: '<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>',
    bus: '<svg viewBox="0 0 48 48"><rect x="9" y="6" width="30" height="31" rx="5"/><path d="M9 22h30M14 37v5M34 37v5"/><circle cx="16" cy="30" r="1.8"/><circle cx="32" cy="30" r="1.8"/></svg>'
  };
  var WX = {
    sun: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="8"/><path d="M24 6v5M24 37v5M6 24h5M37 24h5M11.3 11.3l3.5 3.5M33.2 33.2l3.5 3.5M36.7 11.3l-3.5 3.5M14.8 33.2l-3.5 3.5"/></svg>',
    partly: '<svg viewBox="0 0 48 48"><circle cx="17" cy="16" r="6"/><path d="M17 4v3M5 16h3M8.5 7.5l2 2M25.5 7.5l-2 2"/><path d="M17 41a6.5 6.5 0 1 1 2-12.7A9 9 0 0 1 36 31a5 5 0 0 1-.5 10z"/></svg>',
    cloud: '<svg viewBox="0 0 48 48"><path d="M14 36a7 7 0 1 1 2.1-13.7A10 10 0 0 1 35 25a5.5 5.5 0 0 1-.5 11z"/></svg>',
    fog: '<svg viewBox="0 0 48 48"><path d="M8 18h32M5 25h38M9 32h30M14 39h20"/></svg>',
    rain: '<svg viewBox="0 0 48 48">' + CLOUD_UP + '<path d="M17 35l-2 6M25 35l-2 6M33 35l-2 6"/></svg>',
    snow: '<svg viewBox="0 0 48 48">' + CLOUD_UP + '<path d="M16 35v7M12.5 38.5h7M32 35v7M28.5 38.5h7M24 39v5M21.5 41.5h5"/></svg>',
    storm: '<svg viewBox="0 0 48 48">' + CLOUD_UP + '<path d="M26 31l-5 7h6l-4 7"/></svg>'
  };
  WX.drizzle = WX.rain; WX.shower = WX.rain;
  var ICO_FOR = { tv: "tv", netflix: "play", prime: "play", disney: "play", youtube: "play", gaming: "pad", bus: "bus",
    practical: "wifi", around: "mtn", weather: "cloud", help: "help", settings: "gear" };

  /* ---------- Helpers ---------- */
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  }
  function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* storage blocked: ignore */ } }
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.hidden = true; }, 2800);
  }
  var SHORT = { weekday: "short", day: "numeric", month: "short" };
  function fmtDay(iso, opts) {
    var d = new Date(iso + "T12:00:00");
    return isNaN(d.getTime()) ? "" : I18N.date(d, opts);
  }
  function wxIcon(code) { return WX[I18N.wxGroup(code)] || WX.cloud; }
  function isoDay(d) { // local time of the TV, like guestValid
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + "-" + (m < 10 ? "0" : "") + m + "-" + (day < 10 ? "0" : "") + day;
  }
  function setQr(imgId, rowId, src) {
    if (src) $(imgId).src = src;
    $(rowId).hidden = !src;
  }

  // Client-side expiry check (Pi offline for a long time): show the guest only until departure at 11:00
  function guestValid(g) {
    if (!g || !g.departure || (g.phase !== "active" && g.phase !== "arrival")) return false;
    var dep = new Date(g.departure + "T11:00:00");
    return !isNaN(dep.getTime()) && Date.now() < dep.getTime();
  }

  /* ---------- Rendering ---------- */
  function renderChrome() {
    var st = Data.get() || {};
    var g = guestValid(st.guest) ? st.guest : null;
    var greet = $("greeting");
    greet.innerHTML = "";
    if (g) {
      greet.appendChild(document.createTextNode(I18N.t(g.phase === "active" ? "greet.active" : "greet.arrival")));
      if (g.name) {
        greet.appendChild(document.createTextNode(", "));
        greet.appendChild(el("span", "guest-name", g.name));
      }
      var from = fmtDay(g.arrival, SHORT), to = fmtDay(g.departure, SHORT);
      $("stayinfo").textContent = (from ? from + " – " : "") + to + " · " + I18N.t("greet.departure") + " 11:00";
    } else {
      greet.textContent = I18N.t("greet.vacant");
      $("stayinfo").textContent = I18N.t("greet.vacantSub");
    }
    var badge = $("offlineBadge");
    badge.textContent = I18N.t("badge.offline");
    badge.hidden = Data.source() === "pi";
    var lt = $("langToggle");
    lt.innerHTML = "";
    lt.appendChild(el("span", "lang-ico"));
    lt.firstChild.innerHTML = I.globe;
    lt.appendChild(document.createTextNode(I18N.active().toUpperCase()));
    lt.appendChild(el("span", "lang-chev"));
    lt.lastChild.innerHTML = I.chev;
    [].forEach.call($("langMenu").children, function (b) {
      b.classList.toggle("current", b.getAttribute("data-lang") === I18N.active());
    });
    document.documentElement.lang = I18N.active();
    renderCheckout();
  }

  // Departure day until 11:00: check-out notice instead of the stay line (guestValid ends at 11:00)
  function renderCheckout() {
    var g = (Data.get() || {}).guest;
    var on = guestValid(g) && g.departure === isoDay(new Date());
    $("checkoutBanner").hidden = !on;
    $("stayinfo").hidden = on;
  }

  function applyI18nDom() {
    [].forEach.call(document.querySelectorAll("[data-i18n]"), function (n) {
      n.textContent = I18N.t(n.getAttribute("data-i18n"));
    });
    [].forEach.call(document.querySelectorAll(".tile"), function (tile) {
      var key = tile.getAttribute("data-app") || tile.getAttribute("data-target");
      var holder = tile.querySelector(".tile-ico");
      if (holder && key && key !== "weather") holder.innerHTML = I[ICO_FOR[key]] || I.play;
    });
  }

  // A list from content.json in the active language. Plain text instead of a list counts as one item: a render
  // error here would stop the whole screen (it runs inside the fetch callback, which swallows exceptions).
  function listOf(multi) {
    var v = I18N.pick(multi);
    return Array.isArray(v) ? v : v ? [v] : [];
  }

  function renderPractical() {
    var st = Data.get() || {};
    var c = (st.content && st.content.practical) || {};
    var ssid = st.wlan && st.wlan.ssid;
    $("wlanSsid").textContent = ssid || "–";
    $("wlanPass").textContent = (ssid && st.wlan.password) || "–";
    setQr("wlanQr", "wlanQrRow", ssid && st.qr ? st.qr.wlan : null);
    var cl = $("checkoutList"); cl.innerHTML = "";
    listOf(c.checkout).forEach(function (x) { cl.appendChild(el("li", null, x)); });
    var rl = $("rulesList"); rl.innerHTML = "";
    listOf(c.rules).forEach(function (x) { rl.appendChild(el("li", null, x)); });
  }

  /* ---------- Rail on the left + content on the right (around you, weather, help) ---------- */
  // Focusing an entry shows its content; the choice survives re-renders.
  var railSel = {};
  function rail(listId, titles, show) {
    var list = $(listId);
    list.innerHTML = "";
    function select(i) {
      railSel[listId] = i;
      [].forEach.call(list.children, function (b, j) { b.classList.toggle("selected", j === i); });
      show(i);
    }
    titles.forEach(function (title, i) {
      var b = el("button", "cat-item focusable", title);
      b.type = "button";
      b.addEventListener("focus", function () { if (railSel[listId] !== i) select(i); });
      list.appendChild(b);
    });
    if (titles.length) select(railSel[listId] < titles.length ? railSel[listId] : 0);
  }
  // Fixed sections (weather, help): only the selected one is visible
  function panes(screenId, names) {
    return function (i) {
      [].forEach.call(document.querySelectorAll("#" + screenId + " .rail-pane"), function (p) {
        p.hidden = p.getAttribute("data-pane") !== names[i];
      });
      document.querySelector("#" + screenId + " .rail-body").scrollTop = 0; // a new section starts at the top
    };
  }

  function hasDetail(p) { return !!(p.addr || p.tel || p.hours || p.note || p.web); }

  function renderAround() {
    var st = Data.get() || {};
    var cats = ((st.content && st.content.around) || []).filter(function (c) { return I18N.pick(c.title); });
    var grid = $("placeGrid");
    grid.innerHTML = "";
    busCat = -1;
    cats.forEach(function (c, i) { if (c.bus) busCat = i; });
    $("tileBus").hidden = busCat < 0; // no category with "bus": true -> no bus tile on the home screen
    rail("catList", cats.map(function (c) { return I18N.pick(c.title); }), function (i) {
      grid.innerHTML = "";
      grid.scrollTop = 0;
      if (cats[i].bus) grid.appendChild(fillBus(el("div", "bus-board")));
      (cats[i].places || []).forEach(function (p) {
        var more = hasDetail(p);
        var b = el("button", "place focusable" + (more ? " has-more" : ""));
        b.type = "button";
        b.appendChild(el("b", null, I18N.pick(p.name)));
        var desc = I18N.pick(p.desc);
        if (desc) b.appendChild(el("span", null, desc));
        if (more) b.addEventListener("click", function () { openPlace(p, b); });
        grid.appendChild(b);
      });
    });
  }

  // Bus departures from now on (the Pi fetches them hourly, the app counts the minutes). Home tile: next departures.
  var busCat = -1;
  function nextBuses(n) {
    var now = Date.now();
    return ((Data.get() || {}).bus || []).filter(function (d) { return d && d.ts > now - 30000; }).slice(0, n);
  }
  function renderBusTile() {
    $("tileBusSub").textContent = nextBuses(2).map(function (d) {
      return d.time + " " + String(d.to || "").split(",")[0];
    }).join(" · ");
  }
  function openBus() { // home -> around you, section with the timetable
    Nav.show("around");
    var b = $("catList").children[busCat];
    if (b) Nav.setFocus(b);
  }
  function fillBus(box) {
    var now = Date.now();
    var next = nextBuses(5);
    box.innerHTML = "";
    box.appendChild(el("h4", null, I18N.t("bus.title")));
    if (!next.length) box.appendChild(el("div", "bus-note", I18N.t("bus.none")));
    next.forEach(function (d) {
      var row = el("div", "bus-row");
      row.appendChild(el("span", "t", d.time));
      row.appendChild(el("span", "l", d.line));
      row.appendChild(el("span", "to", String(d.to || "").split(",")[0]));
      // stop names come as "Place, Stop": the stop alone is enough on screen
      var via = (d.via || []).map(function (v) { return String(v).split(", ").pop(); });
      row.appendChild(el("span", "via", via.length ? I18N.t("bus.via") + " " + via.join(", ") : ""));
      var min = Math.round((d.ts - now) / 60000);
      row.appendChild(el("span", "in", min <= 0 ? I18N.t("bus.now") : I18N.t("bus.in").replace("{m}", min)));
      box.appendChild(row);
    });
    return box;
  }

  function openPlace(p, opener) {
    openModal(function (box) {
      box.appendChild(el("h3", null, I18N.pick(p.name)));
      var desc = I18N.pick(p.desc);
      if (desc) box.appendChild(el("p", "lead", desc));
      [["addr", p.addr], ["tel", p.tel], ["hours", I18N.pick(p.hours)], ["note", I18N.pick(p.note)], ["web", p.web]]
        .forEach(function (r) { if (r[1]) box.appendChild(el("div", "md-row " + r[0], r[1])); });
    }, opener);
  }

  /* ---------- Detail view (focus stays inside, Back or a click outside closes it) ---------- */
  var modalOpener = null;
  function modalOpen() { return !$("modal").hidden; }
  function modalButton(label, onClick) {
    var b = el("button", "md-btn focusable", label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }
  function openModal(build, opener) {
    closeLangMenu(false);
    var box = $("modalBox");
    box.className = "modal-box";
    box.innerHTML = "";
    camOpen = -1;
    build(box);
    $("modal").hidden = false;
    modalOpener = opener || null;
    Nav.setScope(box);
    Nav.setFocus(box.querySelector(".focusable"));
  }
  function closeModal() {
    if (!modalOpen()) return;
    $("modal").hidden = true;
    Nav.setScope(null);
    var o = modalOpener;
    modalOpener = null;
    if (o && o.offsetParent !== null) Nav.setFocus(o);
  }

  function wxDay(iso) { return isoDay(new Date()) === String(iso).slice(0, 10); }
  // "2026-11-02T20:01" (local time) -> "20:01", on other days "2 Nov, 20:01"
  function stand(iso) {
    var d = new Date(String(iso).slice(0, 16) + ":00");
    if (isNaN(d.getTime())) return "";
    return wxDay(iso) ? I18N.time(d) : I18N.date(d, { day: "numeric", month: "short" }) + ", " + I18N.time(d);
  }

  // Only correct information: "now" values older than 3 h (Pi or internet down) are not shown,
  // and a past day from an old state is never labelled "Today"
  var STALE_MS = 3 * 3600 * 1000;
  function fresh(iso) { var ms = new Date(iso).getTime(); return !isNaN(ms) && Date.now() - ms < STALE_MS; }
  function dayLabel(date, opts) {
    var today = isoDay(new Date());
    if (date === today) return I18N.t("weather.today");
    var tm = new Date(); tm.setDate(tm.getDate() + 1);
    return date === isoDay(tm) ? I18N.t("weather.tomorrow") : fmtDay(date, opts);
  }
  // Days from today on (index 0 = today, if in the state), plus "now" only for today and only when fresh
  var wxSel = 0, wxSelDate = "", wxSig = "";
  function wxDays(w) {
    var today = isoDay(new Date());
    return (w && w.today ? [w.today].concat(w.days || []) : []).filter(function (d) { return d && d.date >= today; });
  }
  function wxNow(w) {
    var cur = w && w.current;
    return cur && cur.temp !== null && cur.temp !== undefined && fresh(w.updated) ? cur : null;
  }

  function showWxDay(i) {
    var w = (Data.get() || {}).weather;
    var days = wxDays(w);
    var d = days[i], cur = wxNow(w);
    wxSel = i;
    wxSelDate = d ? d.date : "";
    [].forEach.call($("weatherDays").children, function (b, j) { b.classList.toggle("selected", j === i); });
    var isToday = !!d && d.date === isoDay(new Date());
    var now = isToday && cur;
    var code = now ? cur.code : d && d.code;
    $("wxDay").textContent = d ? dayLabel(d.date, { weekday: "long", day: "numeric", month: "long" }) : "";
    $("wIco").innerHTML = d || now ? wxIcon(code) : "";
    $("wTemp").textContent = now ? Math.round(cur.temp) + "°" : d ? Math.round(d.max) + "°" : "–";
    $("wDesc").textContent = d || now ? I18N.wxText(code) : I18N.t("wx.na");
    var meta = [];
    if (d) meta.push(Math.round(d.max) + "° / " + Math.round(d.min) + "°");
    var wind = now ? cur.wind : d && d.wind;
    if (wind !== null && wind !== undefined) meta.push(I18N.t("wx.wind").replace("{v}", Math.round(wind)));
    if (d && d.rain !== null && d.rain !== undefined) meta.push(I18N.t("wx.precip").replace("{v}", Math.round(d.rain)));
    $("wMeta").textContent = meta.join(" · ");
    // Mountain points: today the current temperature (only when fresh), otherwise the day's max/min (by date)
    var mtn = $("wMountain"); mtn.innerHTML = "";
    var mtnNow = isToday && fresh(w && (w.mtnUpdated || w.updated));
    ((w && w.mountain) || []).forEach(function (m) {
      var md = d && (m.days || []).filter(function (x) { return x && x.date === d.date; })[0];
      var useNow = mtnNow && m.temp !== null && m.temp !== undefined;
      if (!useNow && !(md && md.max !== null && md.max !== undefined && md.min !== null && md.min !== undefined)) return;
      var card = el("div", "w-mtn");
      var ico = el("div", "wi"); ico.innerHTML = wxIcon(useNow ? m.code : md.code); card.appendChild(ico);
      var txt = el("div");
      txt.appendChild(el("b", null, m.name));
      txt.appendChild(el("span", null, m.elev + " m"));
      card.appendChild(txt);
      card.appendChild(el("div", "mt", useNow ? Math.round(m.temp) + "°" : Math.round(md.max) + "° / " + Math.round(md.min) + "°"));
      mtn.appendChild(card);
    });
  }

  function renderWeather() {
    var st = Data.get() || {};
    var w = st.weather;
    var cur = wxNow(w);
    var has = !!cur;
    var days = wxDays(w);
    var sig = JSON.stringify(days) + JSON.stringify(w && w.mountain) + I18N.active() + isoDay(new Date());
    if (sig !== wxSig) { // rebuild the days only on new data (the poll runs every 5 min, focus stays)
      wxSig = sig;
      var list = $("weatherDays");
      var hadFocus = list.contains(document.querySelector(".focusable.focused"));
      list.innerHTML = "";
      days.forEach(function (d, i) {
        var b = el("button", "wx-dayrow focusable");
        b.type = "button";
        b.appendChild(el("span", "d", dayLabel(d.date, { weekday: "short" })));
        var ico = el("span", "wi"); ico.innerHTML = wxIcon(d.code); b.appendChild(ico);
        var tt = el("span", "t", Math.round(d.max) + "° ");
        tt.appendChild(el("small", null, Math.round(d.min) + "°"));
        b.appendChild(tt);
        b.addEventListener("focus", function () { showWxDay(i); });
        list.appendChild(b);
      });
      // the selected day stays the same date (after midnight yesterday drops out of the list), otherwise today
      wxSel = Math.max(0, days.map(function (d) { return d.date; }).indexOf(wxSelDate));
      if (hadFocus && list.children[wxSel]) Nav.setFocus(list.children[wxSel]);
    }
    showWxDay(wxSel);
    // The home tile shows the live values: temperature + conditions
    $("tileWxIco").innerHTML = has ? wxIcon(cur.code) : WX.cloud;
    $("tileWxSub").textContent = has ? Math.round(cur.temp) + "° · " + I18N.wxText(cur.code) : "";
    renderCams(st);
  }

  // Webcams: the Pi fetches the images hourly and serves them; the TV never loads them from the camera
  // provider directly. OK = large; left/right pages through them, Back closes.
  var camSig = "", camList = [];
  function camSrc(c) { return CFG.piUrl + "/" + c.img + "?v=" + encodeURIComponent(c.updated || ""); }
  function camCap(c) { return c.name + " · " + c.elev + " m · " + stand(c.updated); }

  function renderCams(st) {
    var cams = st.cams || [];
    var sig = JSON.stringify(cams) + I18N.active();
    if (sig === camSig) return; // do not reload the images on every poll
    camSig = sig;
    camList = cams;
    var grid = $("camGrid");
    var idx = [].indexOf.call(grid.children, document.querySelector("#camGrid .focusable.focused"));
    grid.innerHTML = "";
    if (!cams.length) { grid.appendChild(el("div", "cam-none", I18N.t("cams.none"))); return; }
    cams.forEach(function (c, i) {
      var fig = el("button", "cam focusable");
      fig.type = "button";
      var img = el("img", "cam-img");
      img.alt = "";
      img.onerror = function () {
        camSig = ""; // reload on the next poll (Pi briefly gone)
        var none = el("div", "cam-img cam-none", I18N.t("cams.none"));
        if (img.parentNode) img.parentNode.replaceChild(none, img);
      };
      img.src = camSrc(c);
      fig.appendChild(img);
      // two lines: an image from yesterday carries the date, and on one line that got cut with "…"
      var cap = el("div", "cam-cap", c.name + " · " + c.elev + " m");
      cap.appendChild(el("small", null, stand(c.updated)));
      fig.appendChild(cap);
      fig.addEventListener("click", function () { openCam(i, fig); });
      grid.appendChild(fig);
    });
    if (idx >= 0 && grid.children[idx]) Nav.setFocus(grid.children[idx]);
  }

  var camOpen = -1;
  function openCam(i, opener) {
    openModal(function (box) {
      box.className = "modal-box modal-cam";
      var img = el("img", "cam-big");
      img.alt = "";
      img.id = "camBig";
      box.appendChild(img);
      box.appendChild(el("div", "cam-cap", ""));
    }, opener);
    showCam(i);
  }
  function showCam(i) {
    var n = camList.length;
    if (!n) return;
    camOpen = (i + n) % n;
    $("camBig").src = camSrc(camList[camOpen]);
    $("modalBox").lastChild.textContent = camCap(camList[camOpen]);
  }

  var faqSel = 0;
  function renderHelp() {
    var st = Data.get() || {};
    var c = (st.content && st.content.help) || {};
    var qr = st.qr || {};
    // No built-in fallbacks: what content.json does not provide stays hidden
    var phone = (c.contact && c.contact.phone) || "";
    $("helpPhone").textContent = phone;
    $("helpPhone").hidden = !phone;
    setQr("waQr", "waQrRow", qr.whatsapp);
    setQr("cardQr", "cardQrRow", qr.guestcard);
    setQr("videoQr", "videoQrRow", qr.video);
    $("videoUrl").textContent = String((c.video && c.video.url) || "").replace(/^https?:\/\//, "");
    $("videoUrl").hidden = !!qr.video || !$("videoUrl").textContent;
    var cardLabel = (c.guestcard && c.guestcard.label) || "";
    $("cardLabel").textContent = cardLabel;
    $("cardLabel").hidden = !cardLabel;
    // Emergency: large numbers, below them doctor, pharmacy, hospitals
    var em = c.emergency || {};
    var sos = $("sosGrid"); sos.innerHTML = "";
    (em.numbers || []).forEach(function (n) {
      var box = el("div", "sos");
      box.appendChild(el("b", null, n.n));
      box.appendChild(el("span", null, I18N.pick(n.label)));
      sos.appendChild(box);
    });
    var docs = $("docList"); docs.innerHTML = "";
    (em.places || []).forEach(function (p) {
      var row = el("div", "doc");
      row.appendChild(el("b", null, I18N.pick(p.name)));
      if (p.tel) row.appendChild(el("span", "tel", p.tel));
      var sub = [I18N.pick(p.desc), I18N.pick(p.hours)].filter(Boolean).join(" · ");
      if (sub) row.appendChild(el("span", null, sub));
      docs.appendChild(row);
    });
    var items = listOf(c.faq).filter(function (x) { return x && x.q && x.a; });
    var list = $("faqList"); list.innerHTML = "";

    function showFaq(i) {
      faqSel = i;
      [].forEach.call(list.children, function (b, j) { b.classList.toggle("selected", j === i); });
      $("faqText").textContent = items[i].a;
      var img = $("faqQr");
      if (items[i].qr) { img.src = items[i].qr; img.hidden = false; } else img.hidden = true;
    }
    items.forEach(function (item, i) {
      var b = el("button", "faq-q focusable", item.q);
      b.type = "button";
      b.addEventListener("focus", function () { showFaq(i); });
      list.appendChild(b);
    });
    $("faqAnswer").hidden = !items.length;
    if (items.length) showFaq(faqSel < items.length ? faqSel : 0);
  }

  // Rails of the weather and help screens (titles per language; only on a full re-render, never on a poll)
  function renderRails() {
    rail("wxRail", ["weather.tabWeather", "weather.tabCams"].map(I18N.t), panes("scr-weather", ["weather", "cams"]));
    rail("helpRail", ["help.tabContact", "help.tabEmergency", "help.guestcardTitle", "help.videoTitle", "help.faqTitle"].map(I18N.t),
      panes("scr-help", ["contact", "emergency", "card", "video", "faq"]));
  }

  /* ---------- Settings (written by the Pi as root, Luna.setting) ---------- */
  // Left/right arrows pick a value, after 1.5 s without input it is applied (OK applies at once).
  // Accent = active on the TV; white = picked, not on the TV yet; grey = TV value still unknown (applied -1,
  // e.g. Pi gone or the TV value is outside the steps). Never show a default value as "active".
  var SETTING_DEFS = [
    // LG does not accept a 15-minute sleep timer, so the steps start at 30
    { key: "sleep", values: ["off", "30", "60", "90", "120"], cur: 0, applied: -1 },
    // Screen brightness: the Pi sets LG energySaving auto/off/min/med/max, starting at automatic
    { key: "energy", values: ["auto", "bright", "medium", "dim", "dark"], cur: 0, applied: -1 },
    // Eye comfort mode: LG eyeComfortMode only
    { key: "eye", values: ["off", "on"], cur: 0, applied: -1 }
  ];
  var APPLY_DELAY_MS = 1500;

  function findSetting(key) {
    for (var i = 0; i < SETTING_DEFS.length; i++) if (SETTING_DEFS[i].key === key) return SETTING_DEFS[i];
    return null;
  }
  function labelOf(s) { return I18N.t("set." + s.key + "." + s.values[s.cur]); }

  function updateRow(s) {
    var cur = document.querySelector('[data-set-key="' + s.key + '"] .s-cur');
    if (!cur) return;
    cur.textContent = labelOf(s);
    cur.classList.toggle("pending", !!s.picked && s.cur !== s.applied);
    cur.classList.toggle("unknown", !s.picked && s.applied < 0);
  }

  function renderSettings() {
    var box = $("settingsList"); box.innerHTML = "";
    SETTING_DEFS.forEach(function (s) {
      var row = el("button", "setting-row focusable");
      row.type = "button";
      row.setAttribute("data-set-key", s.key);
      row.appendChild(el("span", "s-name", I18N.t("set." + s.key)));
      var val = el("span", "s-val");
      [[-1, "‹"], [0, null], [1, "›"]].forEach(function (a) {
        if (a[0] === 0) { val.appendChild(el("span", "s-cur")); return; }
        var arrow = el("span", "s-arrow", a[1]);
        // Magic Remote pointer: clicking an arrow changes the value (does not apply it)
        arrow.addEventListener("click", function (ev) { ev.stopPropagation(); changeSetting(s.key, a[0]); });
        val.appendChild(arrow);
      });
      row.appendChild(val);
      row.addEventListener("click", function () { applySetting(s.key); });
      box.appendChild(row);
      updateRow(s);
    });
  }

  function applySetting(key) {
    var s = findSetting(key);
    if (!s) return;
    clearTimeout(s.timer);
    s.timer = null;
    s.picked = true;
    s.busy = true; // until the answer: a status sync in between only knows the state before the write
    var want = s.cur;
    var seq = s.seq = (s.seq || 0) + 1;
    function failed() { s.busy = false; toast(I18N.t("set.failed")); updateRow(s); }
    // A pending reset for this guest goes FIRST, and only on success do we continue: otherwise, after a failed
    // reset, the previous guest's picture settings would stay (a single setting is not a reset).
    resetFirst(function (ok) {
      if (seq !== s.seq) return; // superseded: a newer pick or a guest change in between
      if (!ok) { failed(); return; }
      // Remember BEFORE sending: if the answer never arrives, the TV may still have the value,
      // and the reset for the next guest takes back picture AND sleep timer.
      lsSet("guesttv.pictureChanged", "1");
      Luna.setting(s.key, s.values[want], function (res) {
        if (seq !== s.seq) return;
        s.busy = false;
        if (!res) { failed(); return; }
        s.applied = want;
        if (s.cur === want) s.picked = false; // a newer pick meanwhile? It stays "picked" until its own answer
        toast(I18N.t("set.applied") + ": " + I18N.t("set." + s.key + "." + s.values[want]));
        updateRow(s);
      });
    });
  }

  function changeSetting(key, dir) {
    var s = findSetting(key);
    if (!s) return;
    s.cur = (s.cur + dir + s.values.length) % s.values.length;
    s.picked = true;
    updateRow(s);
    clearTimeout(s.timer);
    s.timer = setTimeout(function () { applySetting(key); }, APPLY_DELAY_MS);
  }

  function setIndex(key, idx) {
    var s = findSetting(key);
    if (!s || idx < 0 || s.timer) return;
    s.cur = s.applied = idx;
    s.picked = false;
    updateRow(s);
  }

  // Actual values from the TV (via the Pi) when the screen opens. Only known values (null = unknown stays,
  // never guess "active"), and only for rows nobody has picked again since the request and that were not
  // waiting to write at request time (e.g. a pick queued behind the reset).
  function syncSettingsFromTv() {
    var seqs = SETTING_DEFS.map(function (s) { return s.busy ? -1 : s.seq || 0; });
    Luna.status(function (res) {
      if (!res) return;
      SETTING_DEFS.forEach(function (s, i) {
        var v = res[s.key];
        if (v !== null && v !== undefined && (s.seq || 0) === seqs[i]) setIndex(s.key, s.values.indexOf(String(v)));
      });
    });
  }

  // Reset for the new guest (Pi: picture + sleep timer back to the defaults), repeated on every poll
  // until the Pi confirms it (TV in standby or Pi briefly gone).
  function retryPictureReset() { resetFirst(function () {}); }

  // cb(true) if no reset is pending or the Pi confirms it now; cb(false) on failure.
  // Only ONE reset in flight, every waiter gets its answer: a second reset behind an already
  // remembered setting would otherwise clear that setting's marker.
  var resetWaiters = null;
  function resetFirst(cb) {
    if (lsGet("guesttv.resetPending") !== "1") { cb(true); return; }
    if (resetWaiters) { resetWaiters.push(cb); return; }
    resetWaiters = [cb];
    Luna.resetPicture(function (res) {
      if (res) {
        lsSet("guesttv.resetPending", "");
        lsSet("guesttv.pictureChanged", "");
      }
      var waiting = resetWaiters;
      resetWaiters = null;
      waiting.forEach(function (f) { f(!!res); });
    });
  }

  // New guest: pick the language again, reset the previous guest's settings (only if the portal
  // changed them). Also runs on the first load (app start).
  function onGuestChange(state) {
    var g = state && state.guest;
    I18N.onGuestChange(g ? g.language : null);
    // a manual language choice holds per booking, also across an app restart
    var saved = (lsGet(LANG_KEY) || "").split("|");
    if (saved[0] === stayKey(state) && saved[1]) I18N.setLanguage(saved[1], true);
    else lsSet(LANG_KEY, "");
    syncTvLang();
    if (!g) return;
    var id = String(g.reservationId || "");
    var last = lsGet("guesttv.lastGuest");
    if (last === id) return;
    if (last !== null && lsGet("guesttv.pictureChanged") === "1") lsSet("guesttv.resetPending", "1");
    lsSet("guesttv.lastGuest", id);
    // drop the previous guest's picks (including answers still in flight)
    SETTING_DEFS.forEach(function (s) {
      clearTimeout(s.timer); s.timer = null; s.seq = (s.seq || 0) + 1; s.cur = 0; s.applied = -1;
      s.picked = false; s.busy = false;
    });
  }

  /* ---------- The TV system language follows the portal (the Pi writes LG's system language) ---------- */
  // Guest present or chosen manually -> portal language, otherwise "reset" (the TV's original language).
  // 1.5 s after a pick or guest change; failure -> next poll; never send a confirmed value twice.
  var LANG_KEY = "guesttv.manualLang"; // "<booking>|<language>"
  var tvLangOk = null, tvLangSending = null, tvLangTimer = null;

  function stayKey(st) { var g = st && st.guest; return g ? String(g.reservationId || "") : "-"; }

  function syncTvLang() {
    clearTimeout(tvLangTimer);
    tvLangTimer = setTimeout(sendTvLang, APPLY_DELAY_MS);
  }

  function sendTvLang() {
    tvLangTimer = null;
    var st = Data.get() || {};
    var want = (guestValid(st.guest) || I18N.isManual()) ? I18N.active() : "reset";
    if (want === tvLangOk || want === tvLangSending) return; // already on the TV or in flight
    tvLangSending = want;
    Luna.language(want, function (res) {
      if (tvLangSending === want) tvLangSending = null;
      tvLangOk = res ? want : null; // failure: TV state unknown, the next poll sends again
      // the wish changed in flight (e.g. DE -> EN -> back to DE): catch up now, not only at the next poll
      if (res && !tvLangSending && !tvLangTimer) sendTvLang();
    });
  }

  /* ---------- Language picker (drop-down in the header) ---------- */
  // Open: focus and pointer stay in the list (Nav.setScope), OK/click picks,
  // Back or a click outside closes without a change; afterwards the focus is back on the button.
  var LANG_NAMES = { de: "Deutsch", en: "English", fr: "Français", it: "Italiano", nl: "Nederlands", pl: "Polski" };

  function menuOpen() { return !$("langMenu").hidden; }

  function buildLangMenu() {
    var menu = $("langMenu");
    CFG.languages.forEach(function (l) {
      var b = el("button", "lang-opt focusable", LANG_NAMES[l] || l.toUpperCase());
      b.type = "button";
      b.setAttribute("data-lang", l);
      b.setAttribute("lang", l);
      b.addEventListener("click", function () { chooseLang(l); });
      menu.appendChild(b);
    });
  }

  function openLangMenu() {
    var menu = $("langMenu");
    menu.hidden = false;
    Nav.setScope(menu);
    Nav.setFocus(menu.querySelector(".current") || menu.firstChild);
  }

  function closeLangMenu(focusToggle) {
    if (!menuOpen()) return;
    $("langMenu").hidden = true;
    Nav.setScope(null);
    if (focusToggle) Nav.setFocus($("langToggle"));
  }

  function chooseLang(lang) {
    closeLangMenu(true);
    I18N.setLanguage(lang, true);
    lsSet(LANG_KEY, stayKey(Data.get()) + "|" + I18N.active());
    rerenderKeepFocus();
    syncTvLang();
  }

  /* ---------- Gaming: ONE tile for all consoles (list from the Pi, without the soundbar) ---------- */
  // One console: OK switches directly; several: a picker in the detail view.
  var inputsSig = null, inputsBusy = false, inputsList = [];

  function cleanInputs(list) {
    return (Array.isArray(list) ? list : []).filter(function (x) {
      return x && typeof x.label === "string" && x.label && typeof x.appId === "string" && x.appId;
    }).slice(0, 4);
  }

  function renderInputs(list) {
    var sig = JSON.stringify(list);
    if (sig === inputsSig) return;
    inputsSig = sig;
    inputsList = list;
    var idx = Nav.focusIndex();
    $("tileGaming").hidden = !list.length;
    document.querySelector(".row-big").classList.toggle("has-gaming", list.length > 0);
    Nav.refocus(idx);
  }

  function startInput(inp) {
    toast(I18N.t("launch.to") + " " + inp.label + " …");
    Luna.launchInput(inp.appId, function (res) { if (!res) toast(I18N.t("input.failed")); });
  }

  function openGaming() {
    if (inputsList.length === 1) { startInput(inputsList[0]); return; }
    openModal(function (box) {
      box.appendChild(el("h3", null, I18N.t("gaming.title")));
      var row = el("div", "md-choice");
      inputsList.forEach(function (inp) {
        row.appendChild(modalButton(inp.label, function () { closeModal(); startInput(inp); }));
      });
      box.appendChild(row);
    }, $("tileGaming"));
  }

  // Pi gone: the last known list stays (localStorage), the input starts directly without the Pi
  function refreshInputs() {
    if (inputsBusy) return;
    inputsBusy = true;
    Luna.inputs(function (res) {
      inputsBusy = false;
      if (!res) return;
      var list = cleanInputs(res.inputs);
      lsSet("guesttv.inputs", JSON.stringify(list));
      renderInputs(list);
    });
  }

  function cachedInputs() {
    try { return cleanInputs(JSON.parse(lsGet("guesttv.inputs"))); } catch (e) { return []; }
  }

  /* ---------- Clock ---------- */
  function renderClock() {
    var now = new Date();
    var c = $("clock");
    c.innerHTML = "";
    c.appendChild(document.createTextNode(I18N.time(now)));
    c.appendChild(el("small", null, I18N.date(now, { weekday: "long", day: "numeric", month: "long" })));
  }

  function renderAll() {
    applyI18nDom(); renderChrome(); renderPractical(); renderAround();
    renderWeather(); renderHelp(); renderRails(); renderSettings(); renderClock(); renderBusTile();
  }
  function rerenderKeepFocus() {
    var idx = Nav.focusIndex();
    renderAll();
    Nav.refocus(idx);
  }

  window.App = {
    onScreen: function (name) {
      closeLangMenu(false); // screen change: list closed, the focus belongs to the new screen
      closeModal();
      if (name === "settings") syncSettingsFromTv();
    }
  };

  /* ---------- Key hooks (before the normal navigation) ---------- */
  // Language list open: Back closes it without a change (arrows + OK work normally, inside the list)
  Nav.addKeyHook(function (e) {
    if (!menuOpen() || (e.keyCode !== 8 && e.keyCode !== 27 && e.keyCode !== 461)) return false;
    closeLangMenu(true);
    return true;
  });
  // Detail view open: Back closes it (arrows + OK work normally, inside the view)
  Nav.addKeyHook(function (e) {
    if (!modalOpen()) return false;
    if (camOpen >= 0 && (e.keyCode === 37 || e.keyCode === 39)) { showCam(camOpen + (e.keyCode === 39 ? 1 : -1)); return true; }
    var plain = !$("modalBox").querySelector(".focusable"); // detail view without buttons: OK closes it too
    if (e.keyCode !== 8 && e.keyCode !== 27 && e.keyCode !== 461 && !(plain && e.keyCode === 13)) return false;
    closeModal();
    return true;
  });
  // Vacant: any key opens the tiles, without OK also triggering something
  Nav.addKeyHook(function () {
    if (Nav.screen() !== "vacant" || menuOpen() || modalOpen()) return false;
    Nav.show("home");
    return true;
  });
  // Settings row: left/right changes the value instead of the focus
  Nav.addKeyHook(function (e) {
    if (e.keyCode !== 37 && e.keyCode !== 39) return false;
    var f = document.querySelector(".setting-row.focused");
    if (!f) return false;
    changeSetting(f.getAttribute("data-set-key"), e.keyCode === 39 ? 1 : -1);
    return true;
  });

  // Rail <-> content: right from the rail always goes to the FIRST entry of the content (not the nearest one,
  // e.g. the last FAQ question); left at the edge goes back to the selected rail entry (the spatial search
  // would otherwise land on another category and switch to it)
  Nav.addKeyHook(function (e) {
    if (e.keyCode !== 37 && e.keyCode !== 39) return false;
    var f = document.querySelector(".focusable.focused");
    if (e.keyCode === 39) {
      var rl = f && f.classList.contains("cat-item") ? f.parentNode : null;
      var first = rl && rl.classList.contains("rail") ? Nav.focusables().filter(function (x) {
        return rl.nextElementSibling && rl.nextElementSibling.contains(x);
      })[0] : null;
      if (!first) return false;
      Nav.setFocus(first);
      return true;
    }
    var body = f && f.closest ? f.closest(".rail-body") : null;
    if (!body) return false;
    var inBody = Nav.focusables().filter(function (x) { return body.contains(x); });
    if (!Nav.moveFocus("left", inBody)) {
      var sel = body.parentNode.querySelector(".cat-item.selected");
      if (sel) Nav.setFocus(sel);
    }
    return true;
  });

  /* ---------- Boot ---------- */
  var lastFullSig = "";
  var lastHadGuest = null;
  function sigOf(st) {
    return JSON.stringify([st && st.guest, st && st.wlan, st && st.qr, st && st.content, Data.source()]);
  }

  function onData(state, source, guestChanged) {
    if (guestChanged) onGuestChange(state);
    retryPictureReset();
    refreshInputs();
    var sig = sigOf(state);
    if (sig !== lastFullSig) { lastFullSig = sig; rerenderKeepFocus(); }
    else { renderChrome(); renderWeather(); renderClock(); renderBusTile(); } // no DOM rebuild, focus stays
    syncGuestScreen();
    if (!tvLangTimer) sendTvLang(); // retry after a failure, guest expired by the clock
  }

  // Switch the screen only on the change guest <-> vacant (not on every poll). Also called by the 10 s timer:
  // at 11:00 the guest expires by the clock, and the next poll can be 5 minutes away.
  function syncGuestScreen() {
    var hasGuest = guestValid((Data.get() || {}).guest);
    if (hasGuest === lastHadGuest) return;
    lastHadGuest = hasGuest;
    renderChrome();
    Nav.show(hasGuest ? "home" : "vacant");
    if (!tvLangTimer) sendTvLang();
  }

  function start() {
    Nav.init();
    Screensaver.watch();

    buildLangMenu();
    $("langToggle").addEventListener("click", function () {
      if (menuOpen()) closeLangMenu(true); else openLangMenu();
    });
    $("tileGaming").addEventListener("click", openGaming);
    $("tileBus").addEventListener("click", openBus);
    // Vacant: with the Magic Remote pointer the centre button is a mouse click, not a key
    $("scr-vacant").addEventListener("click", function () { if (Nav.screen() === "vacant") Nav.show("home"); });
    // Page through the places row by row: Chromium centres a focused card, which would cut a row in half.
    // Row pitch = card height 150 + gap 24 (style.css .place); viewport 672 = 4 rows, so the limits lo/hi
    // are always multiples of the pitch.
    $("placeGrid").addEventListener("focus", function (e) {
      var g = $("placeGrid"), el = e.target, PITCH = 174;
      if (el.parentNode !== g) return;
      function align() {
        var lo = el.offsetTop + el.offsetHeight - g.clientHeight, hi = el.offsetTop;
        g.scrollTop = Math.max(lo, Math.min(hi, Math.round(g.scrollTop / PITCH) * PITCH));
      }
      align();
      setTimeout(align, 0); // in case the browser's focus scrolling runs after the event (older Chromium)
    }, true);
    // Pointer click next to the detail view: only close it
    document.addEventListener("click", function (e) {
      if (!modalOpen() || $("modalBox").contains(e.target)) return;
      e.stopPropagation();
      e.preventDefault();
      closeModal();
    }, true);
    // Pointer click next to the open list: only close it, trigger nothing behind it
    document.addEventListener("click", function (e) {
      if (!menuOpen() || $("langMenu").contains(e.target) || $("langToggle").contains(e.target)) return;
      e.stopPropagation();
      e.preventDefault();
      closeLangMenu(true);
    }, true);
    $("checkoutBanner").addEventListener("click", function () { Nav.show("practical"); });
    [].forEach.call(document.querySelectorAll("[data-action='launch']"), function (tile) {
      tile.addEventListener("click", function () {
        toast(I18N.t("launch.to") + " " + tile.querySelector(".tile-label").textContent + " …");
        Luna.launch(tile.getAttribute("data-app"), function (res) {
          if (!res) toast(I18N.t("launch.failed"));
        });
      });
    });
    [].forEach.call(document.querySelectorAll("[data-action='screen']"), function (tile) {
      tile.addEventListener("click", function () { Nav.show(tile.getAttribute("data-target")); });
    });
    // Back from Netflix & co. or from standby: fresh data, start on the tiles
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) return;
      Screensaver.exit();
      renderClock();
      Data.poll();
      if (Nav.screen() !== "vacant") Nav.show("home");
    });

    renderInputs(cachedInputs());
    renderAll();
    Nav.show("home");
    setInterval(function () { // the notice and the stay end at 11:00; bus minutes count down
      renderClock(); renderCheckout(); renderBusTile(); syncGuestScreen();
      var bus = document.querySelector("#placeGrid .bus-board");
      if (bus && bus.offsetParent !== null) fillBus(bus);
    }, 10000);
    refreshInputs();
    Data.start(onData);
  }

  function boot() {
    var pending = CFG.languages.length;
    CFG.languages.forEach(function (l) {
      Data.localGet("i18n/" + l + ".json", function (d) {
        if (d) I18N.load(l, d);
        if (--pending === 0) start();
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
