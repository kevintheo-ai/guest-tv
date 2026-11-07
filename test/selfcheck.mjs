// Guest TV: self-check (node test/selfcheck.mjs, exit 0 = ok)
// Checks: config.js, i18n completeness of all 6 languages (+ no orphan keys, every setting value labelled,
// no dash punctuation), resolveLanguage fallback, content.json language coverage + links,
// default-state structure, app id consistency between the app, the build and the Pi scripts.
// UI behaviour in a browser: test/ui_walkthrough.py · Pi logic: test/test_*.py
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const APP = join(ROOT, "app");
let failures = 0;
const fail = (msg) => { console.error("FAIL:", msg); failures++; };
const ok = (msg) => console.log("ok:", msg);

// ---- 0) load the real config.js (browser script with a window stub) ----
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(readFileSync(join(APP, "config.js"), "utf8"), sandbox);
const CFG = sandbox.window.GUEST_TV_CONFIG || {};
const LANGS = ["de", "en", "fr", "it", "nl", "pl"];
if (JSON.stringify(CFG.languages) !== JSON.stringify(LANGS)) fail(`config.languages = ${JSON.stringify(CFG.languages)}`);
if (CFG.defaultLang !== "en") fail(`config.defaultLang = ${CFG.defaultLang}, expected en`);
if (!/^http:\/\/[\d.]+:\d+$/.test(CFG.piUrl || "")) fail(`config.piUrl invalid: ${CFG.piUrl}`);
for (const k of ["tv", "netflix", "prime", "disney", "youtube"]) if (!(CFG.appIds || {})[k]) fail(`config.appIds.${k} missing`);
ok("config.js: languages, Pi address, app ids");

// ---- load the i18n module in Node (browser IIFE with a window stub) ----
vm.runInContext(readFileSync(join(APP, "js/i18n.js"), "utf8"), sandbox);
const I18N = sandbox.window.I18N;
if (!I18N || typeof I18N.t !== "function") fail("i18n.js does not load");

// ---- 1) load the bundles + completeness ----
const bundles = {};
for (const l of LANGS) {
  try {
    bundles[l] = JSON.parse(readFileSync(join(APP, "i18n", l + ".json"), "utf8"));
    I18N.load(l, bundles[l]);
  } catch (e) {
    fail(`i18n/${l}.json not readable: ${e.message}`);
  }
}
const enKeys = Object.keys(bundles.en || {}).sort();
if (enKeys.length < 40) fail("en.json looks incomplete (" + enKeys.length + " keys)");
for (const l of LANGS) {
  const missing = enKeys.filter((k) => !Object.prototype.hasOwnProperty.call(bundles[l] || {}, k));
  const orphan = Object.keys(bundles[l] || {}).filter((k) => !enKeys.includes(k));
  if (missing.length) fail(`${l}.json is missing: ${missing.join(", ")}`);
  else if (orphan.length) fail(`${l}.json has keys en.json does not know: ${orphan.join(", ")}`);
  else ok(`${l}.json complete (${enKeys.length} keys)`);
}
// every i18n key used in the HTML/JS exists in en.json
const used = new Set();
for (const f of ["index.html", "js/app.js", "js/screensaver.js"]) {
  const src = readFileSync(join(APP, f), "utf8");
  for (const m of src.matchAll(/data-i18n="([^"]+)"|I18N\.t\("([^"+]+)"\)/g)) used.add(m[1] || m[2]);
}
// setting labels are built at runtime ("set." + key + "." + value): derive them from SETTING_DEFS
const appSrc = readFileSync(join(APP, "js/app.js"), "utf8");
let settingRows = 0;
for (const m of appSrc.matchAll(/key: "(\w+)", values: \[([^\]]+)\]/g)) {
  settingRows++;
  used.add(`set.${m[1]}`);
  for (const v of m[2].match(/"[^"]+"/g)) used.add(`set.${m[1]}.${v.slice(1, -1)}`);
}
if (settingRows !== 3) fail(`SETTING_DEFS not found (${settingRows} rows instead of 3)`);
const unknown = [...used].filter((k) => !enKeys.includes(k));
if (unknown.length) fail(`used in the code, but not in en.json: ${unknown.join(", ")}`);
else ok(`${used.size} keys used in the code exist (incl. all setting values)`);
// renamed/dropped settings must not leave their labels behind
const staleSet = enKeys.filter((k) => k.startsWith("set.") && !used.has(k));
if (staleSet.length) fail(`set.* keys without a setting in the code: ${staleSet.join(", ")}`);
else ok("no orphan set.* keys (every label belongs to a value in SETTING_DEFS)");
// guest texts: no em dash and no "--" as punctuation
for (const l of LANGS) {
  const bad = Object.entries(bundles[l] || {}).filter(([, v]) => /—|--/.test(v)).map(([k]) => k);
  if (bad.length) fail(`${l}.json: dash punctuation in ${bad.join(", ")}`);
}
ok("no dash punctuation in the texts");

// ---- 2) resolveLanguage fallback ----
const cases = [["de", "de"], ["nl", "nl"], ["fi", "en"], ["sv", "en"], ["", "en"], ["DE", "de"], ["zh", "en"]];
for (const [input, expected] of cases) {
  const got = I18N.resolveLanguage(input);
  if (got !== expected) fail(`resolveLanguage('${input}') = '${got}', expected '${expected}'`);
}
ok("resolveLanguage: 7 cases correct (incl. FI/SV -> EN)");

// ---- 2b) weather codes (WMO) + pick ----
const wx = [[0, "sun"], [2, "partly"], [3, "cloud"], [45, "fog"], [53, "drizzle"], [63, "rain"], [73, "snow"],
  [81, "shower"], [95, "storm"], [undefined, "na"], [null, "na"], ["", "na"], ["x", "na"]];
for (const [code, want] of wx) {
  if (I18N.wxGroup(code) !== want) fail(`wxGroup(${code}) = ${I18N.wxGroup(code)}, expected ${want}`);
}
const arr = ["a", "b"];
if (I18N.pick(arr) !== arr || I18N.pick("x") !== "x" || I18N.pick(null) !== null) fail("pick(): text/list must pass through");
ok(`wxGroup: ${wx.length} codes correct (missing code = neutral, never thunderstorm) · pick()`);

// ---- 3) t() takes the language value ----
I18N.setLanguage("pl", true);
if (I18N.t("tile.tv") !== bundles.pl["tile.tv"]) fail("t() does not return the PL value");
ok("t() returns the language value");

// ---- 4) content.json: every multilingual field covers all 6 languages ----
const content = JSON.parse(readFileSync(join(ROOT, "pi/content/content.json"), "utf8"));
const checkMulti = (node, path) => {
  for (const l of LANGS) {
    if (!node || !(l in node)) fail(`content ${path}: language ${l} missing`);
  }
};
checkMulti(content.practical.checkout, "practical.checkout");
checkMulti(content.practical.rules, "practical.rules");
// Phone numbers in international format (guests often have a foreign SIM): +<country code> then digit groups
const TEL = /^\+\d{1,3}( \d{1,5})+$/;
const checkTel = (tel, path) => {
  for (const n of String(tel).match(/[+\d][\d ]{8,}\d/g) || ["?"]) if (!TEL.test(n)) fail(`${path} not in +<country> format: ${n}`);
};
// Detail view: address/phone/web in one language, hours/note per language
const checkPlace = (p, path) => {
  checkMulti(p.desc, `${path}.desc`);
  if (typeof p.name !== "string") checkMulti(p.name, `${path}.name`);
  for (const k of ["hours", "note"]) if (p[k] !== undefined) checkMulti(p[k], `${path}.${k}`);
  for (const k of ["addr", "web"]) if (p[k] !== undefined && typeof p[k] !== "string") fail(`${path}.${k} must be text`);
  if (p.tel !== undefined) checkTel(p.tel, `${path}.tel`);
  if (p.web !== undefined && /^https?:|\s/.test(p.web)) fail(`${path}.web only as a domain: ${p.web}`);
};
let places = 0;
for (const [i, cat] of (content.around || []).entries()) {
  checkMulti(cat.title, `around[${i}].title`);
  if (!cat.places.length || cat.places.length > 14) fail(`around[${i}]: ${cat.places.length} places (1..14)`);
  for (const [j, p] of cat.places.entries()) {
    places++;
    checkPlace(p, `around[${i}].places[${j}]`);
  }
}
// the bus departures show in ONE category; without one the home screen hides the bus tile
if (content.around.filter((c) => c.bus).length > 1) fail("around: at most one category with bus departures (bus: true)");
if (!/^https:\/\//.test((content.help.video || {}).url || "")) fail("help.video.url missing/not https");
const em = content.help.emergency || {};
if (!(em.numbers || []).some((n) => n.n === "144") || !(em.numbers || []).some((n) => n.n === "112")) fail("help.emergency: 144/112 missing");
for (const [i, n] of (em.numbers || []).entries()) checkMulti(n.label, `help.emergency.numbers[${i}].label`);
for (const [i, p] of (em.places || []).entries()) checkPlace(p, `help.emergency.places[${i}]`);
checkMulti(content.help.faq, "help.faq");
const faqLen = content.help.faq.en.length;
for (const l of LANGS) {
  const items = content.help.faq[l] || [];
  if (items.length !== faqLen) fail(`help.faq.${l}: ${items.length} questions, EN has ${faqLen}`);
  for (const it of items) {
    if (!it.q || !it.a) fail(`help.faq.${l}: entry without q/a`);
    if (it.url && !/^https:\/\//.test(it.url)) fail(`help.faq.${l}: url not https: ${it.url}`);
  }
}
checkTel((content.help.contact || {}).phone || "", "help.contact.phone");
if (!/^https:\/\/wa\.me\/\d+$/.test(content.help.contact.whatsapp || "")) fail("help.contact.whatsapp missing/invalid (https://wa.me/<digits>)");
if (!/^https:\/\/\S+$/.test((content.help.guestcard || {}).url || "")) fail("help.guestcard.url missing/not https");
ok(`content.json: ${content.around.length} categories, ${places} places, emergency, ${faqLen} FAQ x ${LANGS.length} languages checked`);

// ---- 5) default-state.json structure (content comes from the bundled content.json) ----
const ds = JSON.parse(readFileSync(join(APP, "default-state.json"), "utf8"));
for (const k of ["updated", "guest", "wlan", "qr", "weather", "photos", "content"]) {
  if (!(k in ds)) fail(`default-state.json is missing key '${k}'`);
}
if (ds.guest !== null) fail("default-state: guest must be null (vacant, no personal data in the package)");
if (ds.wlan.password) fail("default-state: no Wi-Fi password in the app package");
ok("default-state.json structure ok");

// ---- 6) appinfo: the Back key must reach the app ----
const appinfo = JSON.parse(readFileSync(join(APP, "appinfo.json"), "utf8"));
if (appinfo.disableBackHistoryAPI !== true) fail("appinfo.disableBackHistoryAPI missing: Back would close the app");
else ok("appinfo: the Back key goes to the app (disableBackHistoryAPI)");

// ---- 7) one app id everywhere: the build packs it, the Pi installs, launches and pins it as home app ----
// Every spot that names the portal (ID=/PORTAL_ID= assignments, "id"/"home" in luna JSON, install paths)
// must carry the id from appinfo.json. Variables ($ID) and webOS system apps (com.webos.* ...) are skipped.
const ID = appinfo.id;
const ID_SPOTS = [/^\s*(?:ID|PORTAL_ID)\s*=\s*"([^"]+)"/gm, /"(?:id|home)"\s*:\s*"([^"]+)"/g, /\/applications\/([\w.-]+)/g];
const portalIds = (src) => ID_SPOTS.flatMap((re) => [...src.matchAll(re)].map((m) => m[1].replace(/\.+$/, "")))
  .filter((v) => /^[\w.-]+$/.test(v) && !/^com\.(webos|palm|lge)\./.test(v));
let idBad = 0;
for (const f of ["build/build_ipk.sh", "pi/deploy/tv_install.sh", "pi/deploy/guesttv.init", "pi/deploy/watchdog.sh", "pi/tv_settings.py"]) {
  let src;
  try { src = readFileSync(join(ROOT, f), "utf8"); } catch (e) { fail(`${f} not readable`); idBad++; continue; }
  const ids = portalIds(src), wrong = [...new Set(ids.filter((v) => v !== ID))];
  if (!ids.length) { fail(`${f}: no app id found`); idBad++; }
  else if (wrong.length) { fail(`${f}: app id ${wrong.join(", ")} instead of ${ID} (appinfo.json)`); idBad++; }
}
if (!idBad) ok(`app id ${ID} is the same in appinfo.json, the build and the Pi scripts`);

console.log(failures === 0 ? "\nSELFCHECK PASSED" : `\nSELFCHECK FAILED (${failures})`);
process.exit(failures === 0 ? 0 : 1);
