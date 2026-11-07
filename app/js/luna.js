// luna: webOS bridge. Starts apps and HDMI inputs, TV settings go through the Pi.
// Settings and the TV language are written by the Pi as root (POST /tv/setting): the SettingsService
// refuses apps not signed by LG ("Access denied").
// In a desktop browser without webOS: a mock only logs and answers "ok".
// webOS.service.request signature: service URI WITHOUT the method, the method separately.
(function () {
  "use strict";
  var CFG = window.GUEST_TV_CONFIG;
  // A real device only with PalmServiceBridge (webOSTV.js also loads in a browser,
  // but without the bridge every call fails: use the mock instead).
  var hasWebOS = typeof window.webOS !== "undefined" && !!window.webOS.service &&
    typeof window.PalmServiceBridge !== "undefined";
  var lastResults = {}; // last result per action (debugging on the TV)
  var mockFail = {};    // mock only (tests): action/method -> true = simulate a failure
  var mockLog = [];     // mock only (tests): all Pi requests in order
  var mockDelay = {};   // mock only (tests): action -> answer only after N ms (request "in flight")
  var MOCK = {
    status: { ok: true, sleep: null, energy: null, eye: null },
    inputs: { ok: true, inputs: [
      { label: "XBOX ONE", appId: "com.webos.app.hdmi2" },
      { label: "Nintendo Switch", appId: "com.webos.app.hdmi4" }
    ] },
    lang: { ok: true, changed: true }
  };

  function call(serviceUri, method, params, cb) {
    if (!hasWebOS) {
      lastResults[serviceUri + "/" + method] = { mocked: true, params: params };
      if (window.console) console.log("[luna:mock]", serviceUri, method, JSON.stringify(params));
      if (cb) cb(mockFail[method] ? null : { mocked: true, returnValue: true });
      return;
    }
    window.webOS.service.request(serviceUri, {
      method: method,
      parameters: params,
      onSuccess: function (res) { lastResults[serviceUri + "/" + method] = res; if (cb) cb(res); },
      onFailure: function (res) {
        lastResults[serviceUri + "/" + method] = { error: true, res: res };
        if (window.console) console.log("[luna:fail]", method, JSON.stringify(res));
        if (cb) cb(null);
      }
    });
  }

  // --- Pi relay: only ONE request in flight at a time, fixed order ---
  var queue = [];
  var busy = false;

  function send(body, done) {
    if (!hasWebOS) {
      mockLog.push(body);
      lastResults["relay." + body.op] = body;
      setTimeout(function () { done(mockFail[body.op] ? null : (MOCK[body.op] || { ok: true })); }, mockDelay[body.op] || 0);
      return;
    }
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", CFG.piUrl + "/tv/setting", true);
      xhr.timeout = 25000;
      // loadend fires exactly once: after an answer, network error, abort or timeout
      xhr.onloadend = function () {
        var res = null;
        try { if (xhr.status === 200) res = JSON.parse(xhr.responseText); } catch (e) { res = null; }
        if (!res || res.ok !== true) res = null;
        lastResults["relay." + body.op] = res || { error: true, status: xhr.status };
        done(res);
      };
      xhr.send(JSON.stringify(body)); // string body = text/plain: no CORS preflight
    } catch (e) {
      setTimeout(function () { done(null); }, 0);
    }
  }

  function next() {
    var job = queue.shift();
    busy = !!job;
    if (!job) return;
    send(job.body, function (res) {
      next(); // next request before the callback: an error there does not stop the queue
      if (job.cb) job.cb(res);
    });
  }

  // cb(answer) on HTTP 200 + ok:true, otherwise cb(null)
  function relay(body, cb) {
    queue.push({ body: body, cb: cb });
    if (!busy) next();
  }

  // --- Start apps and inputs (TV app, Netflix, ..., consoles on HDMI) ---
  var AM = "luna://com.webos.applicationManager";
  function launch(appKey, cb) {
    var id = CFG.appIds[appKey];
    if (!id) { if (cb) cb(null); return; }
    call(AM, "launch", { id: id }, cb);
  }
  function input(appId, cb) { relay({ op: "input", value: appId }, cb); }
  // Start the input directly (public API); if the TV refuses, the Pi switches as root
  function launchInput(appId, cb) {
    call(AM, "launch", { id: appId }, function (res) {
      if (res) { if (cb) cb(res); } else input(appId, cb);
    });
  }

  window.Luna = {
    hasWebOS: hasWebOS, call: call, relay: relay,
    launch: launch, launchInput: launchInput, input: input,
    // key = app setting (sleep|energy|eye) = Pi action of the same name
    setting: function (key, value, cb) { relay({ op: key, value: String(value) }, cb); },
    resetPicture: function (cb) { relay({ op: "reset" }, cb); }, // picture + sleep timer back to defaults (guest change)
    language: function (lang, cb) { relay({ op: "lang", value: lang }, cb); }, // de..pl or "reset"
    status: function (cb) { relay({ op: "status" }, cb); },
    inputs: function (cb) { relay({ op: "inputs" }, cb); },
    _lastResults: function () { return lastResults; },
    _mock: MOCK,          // mock only (tests): answers per action can be swapped
    _mockFail: mockFail,
    _mockDelay: mockDelay,
    _relayLog: function () { return mockLog; }
  };
})();
