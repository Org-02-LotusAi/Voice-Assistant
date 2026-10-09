// Shared by the Workbench and Talk pages: API key storage and the single fetch wrapper.
// Every call goes to the public Service 015 API; no business logic lives in the browser.
(function () {
  const KEY_STORAGE = "service015.apiKey";

  function getKey() {
    try { return localStorage.getItem(KEY_STORAGE) || ""; } catch (e) { return ""; }
  }
  function setKey(k) {
    try { localStorage.setItem(KEY_STORAGE, k); } catch (e) { throw new Error("cannot store the API key: browser storage is blocked"); }
  }

  // Error carrying the API's ErrorBody verbatim.
  class ApiError extends Error {
    constructor(status, code, message, details) {
      super(message);
      this.status = status; this.code = code; this.details = details || {};
    }
    format() {
      const d = Object.keys(this.details).length ? "\n" + JSON.stringify(this.details, null, 2) : "";
      return `${this.status} ${this.code}: ${this.message}${d}`;
    }
  }

  async function api(method, path, body, opts) {
    opts = opts || {};
    const headers = { Authorization: "Bearer " + getKey() };
    const init = { method, headers, signal: opts.signal };
    if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    const res = await fetch(path, init);
    if (opts.blob && res.ok) return await res.blob();
    const text = await res.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    if (!res.ok) {
      if (data && data.error) throw new ApiError(res.status, data.error.code, data.error.message, data.error.details);
      throw new ApiError(res.status, "http_" + res.status, text || res.statusText, {});
    }
    return data;
  }

  function describe(e) {
    if (e instanceof ApiError) return e.format();
    return String(e && e.message ? e.message : e);
  }

  function el(tag, props, children) {
    const n = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => { if (k === "class") n.className = v; else n[k] = v; });
    (children || []).forEach((c) => n.append(c));
    return n;
  }

  // Run fn only while the tab is visible; fires immediately on becoming visible again.
  function pollWhileVisible(fn, ms) {
    let timer = null;
    function start() { if (!timer) timer = setInterval(fn, ms); }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") { fn(); start(); } else stop();
    });
    if (document.visibilityState === "visible") start();
    return { stop };
  }

  window.S015 = { getKey, setKey, api, ApiError, describe, el, pollWhileVisible };
})();
