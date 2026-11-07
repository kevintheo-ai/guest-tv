// i18n: language choice. Guest language from the booking (state.guest.language),
// fallback EN, manual switch until the next guest change. Plus clock/date formatting.
(function () {
  "use strict";
  var CFG = window.GUEST_TV_CONFIG;
  var bundles = {};
  var current = CFG.defaultLang;
  var manual = null;

  function resolveLanguage(guestLang) {
    var lang = (guestLang || "").toLowerCase().slice(0, 2);
    return CFG.languages.indexOf(lang) !== -1 ? lang : CFG.defaultLang;
  }

  function setLanguage(lang, manual_) {
    if (CFG.languages.indexOf(lang) === -1) lang = CFG.defaultLang;
    current = lang;
    if (manual_) manual = lang;
  }

  // guest change (new booking or vacant): the manual choice expires
  function onGuestChange(guestLang) {
    manual = null;
    current = resolveLanguage(guestLang);
  }

  function t(key) {
    var b = bundles[active()] || {};
    if (Object.prototype.hasOwnProperty.call(b, key)) return b[key];
    var fb = bundles[CFG.defaultLang] || {}; // EN fallback
    return Object.prototype.hasOwnProperty.call(fb, key) ? fb[key] : key;
  }

  function load(lang, dict) { bundles[lang] = dict; }
  function active() { return manual || current; }
  function isManual() { return !!manual; }

  // content.json helper: text OR object with language keys -> active language with EN fallback
  function pick(multi) {
    if (multi === null || multi === undefined) return null;
    if (typeof multi === "string" || Array.isArray(multi)) return multi; // kept in one language
    var a = active();
    if (multi[a]) return multi[a];
    return multi[CFG.defaultLang] || multi.de || null;
  }

  // 24-hour clock, fixed: toLocaleTimeString follows the TV system language (showed "AM")
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function time(d) { return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function date(d, opts) {
    try { return d.toLocaleDateString(active(), opts); } catch (e) { return d.getDate() + "." + (d.getMonth() + 1) + "."; }
  }

  // WMO weather code -> group name (key wx.<group>)
  function wxGroup(code) {
    var c = Number(code);
    // missing/invalid code: neutral ("weather coming shortly"), never a false "thunderstorm"
    if (code === null || code === undefined || code === "" || isNaN(c)) return "na";
    if (c === 0) return "sun";
    if (c <= 2) return "partly";
    if (c === 3) return "cloud";
    if (c <= 48) return "fog";
    if (c <= 57) return "drizzle";
    if (c <= 67) return "rain";
    if (c <= 77) return "snow";
    if (c <= 82) return "shower";
    if (c <= 86) return "snow";
    return "storm";
  }
  function wxText(code) { return t("wx." + wxGroup(code)); }

  window.I18N = {
    load: load, t: t, pick: pick, wxGroup: wxGroup, wxText: wxText,
    setLanguage: setLanguage, onGuestChange: onGuestChange,
    resolveLanguage: resolveLanguage, active: active, isManual: isManual,
    time: time, date: date
  };
})();
