// Shared by every page: session/CSRF handling, the single fetch wrapper, the page header and small DOM helpers.
// Every call goes to the public Service 015 API; no business logic lives in the browser.
// Auth: humans use the HttpOnly session cookie plus an X-CSRF-Token header (token comes from GET /auth/me).
// "API key mode" (off by default, machine debugging) sends a Bearer key from localStorage instead.
(function () {
  const KEY_STORAGE = "service015.apiKey";
  const MODE_STORAGE = "service015.apiKeyMode";
  const state = { csrf: null, me: null };

  function store(op, k, v) {
    try { return op === "get" ? localStorage.getItem(k) : op === "set" ? localStorage.setItem(k, v) : localStorage.removeItem(k); }
    catch (e) { if (op === "get") return null; throw new Error("browser storage is blocked"); }
  }
  function getKey() { return store("get", KEY_STORAGE) || ""; }
  function setKey(k) { store("set", KEY_STORAGE, k); }
  function apiKeyMode() { return store("get", MODE_STORAGE) === "1"; }
  function setApiKeyMode(on) { if (on) store("set", MODE_STORAGE, "1"); else store("del", MODE_STORAGE); }

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

  // body: object (sent as JSON), FormData (multipart), or undefined.
  async function api(method, path, body, opts) {
    opts = opts || {};
    const headers = {};
    if (apiKeyMode() && !opts.noAuth) headers.Authorization = "Bearer " + getKey();
    else if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
    const init = { method, headers, signal: opts.signal, credentials: "same-origin" };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
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

  function showError(id, e) {
    const box = document.getElementById(id);
    if (!box) return;
    if (!e) { box.classList.add("hidden"); box.textContent = ""; return; }
    box.textContent = describe(e);
    box.classList.remove("hidden");
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

  const NAV = [["/", "My personas"], ["/templates.html", "Templates"], ["/voices.html", "Voices"], ["/monitor.html", "Monitor"]];

  function renderHeader(me) {
    const host = document.getElementById("hdr");
    if (!host) return;
    host.textContent = "";
    const who = me.username ? me.username : (me.label || me.kind);
    const nav = el("nav", { class: "row grow" }, NAV.filter(([href]) => me.effective_role !== "viewer" || href === "/monitor.html")
      .map(([href, text]) => el("a", { href, textContent: text, class: location.pathname === href ? "cur" : "" })));
    const right = el("div", { class: "row" }, [
      el("span", { id: "whoami", class: "pill", textContent: who + " (" + me.role + ")" }),
    ]);
    if (me.kind === "user") {
      right.append(el("a", { href: "/change-password", textContent: "Change password" }));
      right.append(el("button", { type: "button", id: "logout", textContent: "Log out", onclick: logout }));
    } else {
      right.append(el("span", { class: "notice", textContent: "API key mode" }));
    }
    host.append(el("header", { class: "row" }, [el("strong", { textContent: "Identity Workbench" }), nav, right]));
  }

  async function logout() {
    try { await api("POST", "/auth/logout"); }
    catch (e) { if (!(e instanceof ApiError) || e.status !== 401) { showError("globalError", e); return; } }
    state.csrf = null;
    location.assign("/login");
  }

  // Resolve the caller, enforce redirects, render the header. Returns /auth/me, or null when navigation was triggered.
  // opts.allowPasswordChange: the change-password page itself. opts.anyRole: pages open to viewers.
  async function init(opts) {
    opts = opts || {};
    let me;
    if (apiKeyMode() && !getKey()) {
      showError("globalError", new Error("API key mode is on: enter an API key below, or switch API key mode off."));
      return null;
    }
    try { me = await api("GET", "/auth/me"); }
    catch (e) {
      if (e instanceof ApiError && e.status === 401 && !apiKeyMode()) { location.assign("/login"); return null; }
      showError("globalError", e);
      return null;
    }
    state.me = me; state.csrf = me.csrf_token;
    if (me.must_change_password && !opts.allowPasswordChange) { location.assign("/change-password"); return null; }
    if (me.effective_role === "viewer" && !opts.anyRole) { location.assign("/monitor.html"); return null; }
    renderHeader(me);
    return me;
  }

  window.S015 = { getKey, setKey, apiKeyMode, setApiKeyMode, api, ApiError, describe, el, showError, pollWhileVisible, init, state };
})();
