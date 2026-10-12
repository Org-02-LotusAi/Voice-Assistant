// Identity Workbench. All behavior goes through the public API.
(function () {
  const { api, describe, el, getKey, setKey, apiKeyMode, setApiKeyMode, pollWhileVisible, init: initSession } = window.S015;
  const $ = (id) => document.getElementById(id);

  const FIELDS = Array.from(document.querySelectorAll("[data-key]"));
  const byKey = Object.fromEntries(FIELDS.map((f) => [f.dataset.key, f]));
  const REQUIRED_NOTE = " (required)";
  const LANGUAGES = ["en-US", "en-GB", "en-IN", "hi-IN"];

  // Language is a dropdown of the supported list plus whatever value the template/identity already carries.
  function setLanguage(value) {
    const sel = byKey["language"];
    sel.textContent = "";
    const all = LANGUAGES.slice();
    if (value && !all.includes(value)) all.push(value);
    for (const l of all) sel.append(el("option", { value: l, textContent: l }));
    sel.value = value || LANGUAGES[0];
  }

  const state = {
    template: null,      // TemplateBody currently governing the form
    identity: null,      // ResolvedIdentity being edited, or null when creating
    statuses: {},        // identity_id -> ParticipantStatus
    voices: [],
    previewAbort: null,
  };

  function showError(id, e) {
    const box = $(id);
    if (!e) { box.classList.add("hidden"); box.textContent = ""; return; }
    box.textContent = describe(e);
    box.classList.remove("hidden");
  }

  // ---- form <-> template/identity mapping ----

  function templateDefaults(t) {
    return {
      "name": "",
      "company": t.default_self_facts.company || "",
      "role": t.default_self_facts.role || "",
      "language": t.default_self_facts.language || "",
      "greeting": t.conversation.greeting,
      "persona.instructions": t.persona.instructions,
      "conversation.speaking_style": t.conversation.speaking_style,
      "conversation.barge_in": t.conversation.barge_in !== false,
      "voice.profile_id": t.voice.default_voice_profile_id || "",
    };
  }

  function identityValues(r) {
    return {
      "name": r.self.name,
      "company": r.self.company || "",
      "role": r.self.role || "",
      "language": r.self.language,
      "greeting": r.conversation.greeting,
      "persona.instructions": r.persona.instructions,
      "conversation.speaking_style": r.conversation.speaking_style,
      "conversation.barge_in": r.conversation.barge_in,
      "voice.profile_id": r.voice.profile_id || "",
    };
  }

  function fill(values) {
    for (const [k, f] of Object.entries(byKey)) {
      const v = values[k];
      if (k === "language") setLanguage(v);
      else if (f.type === "checkbox") f.checked = !!v;
      else f.value = v === undefined ? "" : v;
    }
  }

  function readForm() {
    const out = {};
    for (const [k, f] of Object.entries(byKey)) out[k] = f.type === "checkbox" ? f.checked : f.value.trim();
    return out;
  }

  // Fields outside the template version's allowed_overrides are disabled.
  function applyAllowed(t) {
    const allowed = new Set(t.allowed_overrides);
    const required = new Set(t.required_instance_fields);
    for (const [k, f] of Object.entries(byKey)) {
      f.disabled = !allowed.has(k);
      f.title = f.disabled ? "Not overridable in template " + t.template_id + " v" + t.version : "";
      const lab = document.querySelector('label[for="' + f.id + '"]');
      if (lab && f.type !== "checkbox") lab.textContent = lab.textContent.replace(REQUIRED_NOTE, "") + (required.has(k) ? REQUIRED_NOTE : "");
    }
    $("preview").disabled = byKey["voice.profile_id"].disabled;
  }

  function setMode() {
    const editing = !!state.identity;
    $("formTitle").textContent = editing ? "Identity " + state.identity.identity_id : "New identity";
    $("template").disabled = editing;
    $("create").disabled = editing;
    for (const id of ["save", "launch", "stop", "test", "archive"]) $(id).disabled = !editing;
    document.querySelectorAll(".ident").forEach((n) => n.classList.toggle("sel", editing && n.dataset.id === state.identity.identity_id));
  }

  async function loadTemplate(id, version) {
    const q = version ? "?version=" + encodeURIComponent(version) : "";
    state.template = await api("GET", "/templates/" + encodeURIComponent(id) + q);
    $("templateDesc").textContent = state.template.description + " (v" + state.template.version + ")";
    applyAllowed(state.template);
  }

  async function onTemplateChange() {
    showError("formError", null);
    try {
      await loadTemplate($("template").value);
      fill(templateDefaults(state.template));
    } catch (e) { showError("formError", e); }
  }

  // ---- data loading ----

  function renderVoices(selected) {
    const sel = byKey["voice.profile_id"];
    sel.textContent = "";
    sel.append(el("option", { value: "", textContent: "(no voice)" }));
    for (const v of state.voices) {
      sel.append(el("option", {
        value: v.voice_profile_id, disabled: !v.authorized,
        textContent: v.name + " [" + v.language + "]" + (v.authorized ? "" : " - not authorized"),
      }));
    }
    if (selected && !state.voices.some((v) => v.voice_profile_id === selected)) {
      sel.append(el("option", { value: selected, textContent: selected + " (not in Voicebox list)" }));
    }
    sel.value = selected || "";
  }

  async function loadVoices() {
    try { state.voices = await api("GET", "/voices"); }
    catch (e) { state.voices = []; showError("formError", e); }
    renderVoices(byKey["voice.profile_id"].value);
  }

  async function loadTemplates() {
    const list = await api("GET", "/templates");
    const sel = $("template");
    const prev = sel.value;
    sel.textContent = "";
    for (const t of list) sel.append(el("option", { value: t.template_id, textContent: t.name }));
    if (prev && list.some((t) => t.template_id === prev)) sel.value = prev;
    if (list.length && !state.identity) await onTemplateChange();
  }

  function renderIdentities(list) {
    const box = $("identities");
    box.textContent = "";
    if (!list.length) { box.append(el("div", { class: "notice", textContent: "No identities yet." })); return; }
    for (const i of list) {
      const st = state.statuses[i.identity_id];
      const pill = el("span", { class: "pill " + (st ? st.presence : ""), textContent: st ? st.presence : "..." });
      const row = el("div", { class: "ident" }, [
        el("div", { class: "row" }, [
          el("strong", { textContent: i.name }), pill,
          el("button", { type: "button", textContent: "Open", onclick: () => selectIdentity(i.identity_id) }),
        ]),
        el("div", { class: "notice", textContent: [i.role, i.company].filter(Boolean).join(" at ") + " - " + i.template_id + " v" + i.template_version }),
        el("div", { class: "mono notice", textContent: i.identity_id }),
      ]);
      if (st && (st.last_error || st.status_detail)) {
        row.append(el("div", { class: st.last_error ? "error" : "notice", textContent: st.last_error || st.status_detail }));
      }
      row.dataset.id = i.identity_id;
      box.append(row);
    }
    setMode();
  }

  let ready = false;
  let refreshing = false, refreshQueued = false;
  async function refreshIdentities() {
    if (!ready) return;
    if (refreshing) { refreshQueued = true; return; }   // at most one in flight; one trailing rerun
    refreshing = true;
    try {
      const list = await api("GET", "/identities");
      const results = await Promise.allSettled(list.map((i) => api("GET", "/identities/" + encodeURIComponent(i.identity_id) + "/status")));
      state.statuses = {};
      results.forEach((r, n) => { if (r.status === "fulfilled") state.statuses[list[n].identity_id] = r.value; });
      renderIdentities(list);
      showError("globalError", null);
    } catch (e) { showError("globalError", e); }
    finally {
      refreshing = false;
      if (refreshQueued) { refreshQueued = false; refreshIdentities(); }
    }
  }

  async function selectIdentity(id) {
    $("archiveConfirm").classList.add("hidden");
    showError("formError", null); $("formMsg").textContent = "";
    try {
      const r = await api("GET", "/identities/" + encodeURIComponent(id));
      await loadTemplate(r.template_id, r.template_version);   // state.identity is set only once its template is loaded
      state.identity = r;
      $("template").value = r.template_id;
      renderVoices(r.voice.profile_id);
      fill(identityValues(r));
      setMode();
    } catch (e) { showError("formError", e); }
  }

  async function resetForm() {
    state.identity = null; showError("formError", null); $("formMsg").textContent = "";
    $("archiveConfirm").classList.add("hidden");
    setMode();
    await onTemplateChange();
  }

  // ---- actions ----

  async function create() {
    showError("formError", null); $("formMsg").textContent = "";
    const t = state.template; if (!t) return;
    const vals = readForm(), defs = templateDefaults(t), required = new Set(t.required_instance_fields);
    const overrides = {};
    for (const k of t.allowed_overrides) {
      if (!(k in vals)) continue;
      const v = vals[k];
      if (typeof v === "string" && v === "") continue;
      if (v !== defs[k] || required.has(k)) overrides[k] = v;
    }
    try {
      const r = await api("POST", "/templates/" + encodeURIComponent(t.template_id) + "/instantiate",
        { overrides, template_version: t.version });
      await refreshIdentities();
      await selectIdentity(r.identity_id);
      $("formMsg").textContent = "Created " + r.identity_id + ".";
    } catch (e) { showError("formError", e); }
  }

  async function save() {
    showError("formError", null); $("formMsg").textContent = "";
    const r = state.identity; if (!r) return;
    const vals = readForm(), cur = identityValues(r), overrides = {};
    for (const k of state.template.allowed_overrides) {
      if (!(k in vals) || vals[k] === cur[k]) continue;
      overrides[k] = vals[k] === "" ? null : vals[k];   // an emptied field clears its override (template default applies)
    }
    if (!Object.keys(overrides).length) { $("formMsg").textContent = "Nothing changed."; return; }
    try {
      state.identity = await api("PATCH", "/identities/" + encodeURIComponent(r.identity_id), { overrides });
      fill(identityValues(state.identity));
      renderVoices(state.identity.voice.profile_id);
      $("formMsg").textContent = "Saved.";
      await refreshIdentities();
    } catch (e) { showError("formError", e); }
  }

  async function runtimeAction(kind) {
    showError("formError", null); $("formMsg").textContent = "";
    const r = state.identity; if (!r) return;
    try {
      const st = await api("POST", "/identities/" + encodeURIComponent(r.identity_id) + "/" + kind);
      $("formMsg").textContent = kind + ": presence " + st.presence;
      await refreshIdentities();
    } catch (e) { showError("formError", e); }
  }

  function askArchive() {
    if (!state.identity) return;
    $("archiveText").textContent = "Archive " + state.identity.self.name + " (" + state.identity.identity_id +
      ")? It is stopped if running and disappears from your lists. This cannot be undone from this page.";
    $("archiveConfirm").classList.remove("hidden");
    $("archiveNo").focus();
  }

  async function doArchive() {
    showError("formError", null); $("formMsg").textContent = "";
    const r = state.identity; if (!r) return;
    $("archiveYes").disabled = true;
    try {
      await api("DELETE", "/identities/" + encodeURIComponent(r.identity_id));
      $("archiveConfirm").classList.add("hidden");
      state.identity = null;
      await resetForm();
      await refreshIdentities();
      $("formMsg").textContent = "Archived " + r.identity_id + ".";
    } catch (e) { showError("formError", e); }
    finally { $("archiveYes").disabled = false; }
  }

  async function preview() {
    const vid = byKey["voice.profile_id"].value;
    showError("formError", null);
    if (!vid) { $("previewState").textContent = "Choose a voice first."; return; }
    const ctl = new AbortController();
    state.previewAbort = ctl;
    $("preview").disabled = true; $("cancelPreview").classList.remove("hidden");
    $("previewState").textContent = "Synthesizing preview - this takes 15-40 s on CPU...";
    try {
      const blob = await api("POST", "/voices/" + encodeURIComponent(vid) + "/preview", {}, { signal: ctl.signal, blob: true });
      const a = $("previewAudio");
      if (a.src) URL.revokeObjectURL(a.src);
      a.src = URL.createObjectURL(blob);
      a.classList.remove("hidden");
      $("previewState").textContent = "";
      a.play().catch(() => { $("previewState").textContent = "Preview ready; press play."; });
    } catch (e) {
      if (e && e.name === "AbortError") $("previewState").textContent = "Preview cancelled.";
      else { $("previewState").textContent = ""; showError("formError", e); }
    } finally {
      state.previewAbort = null;
      $("preview").disabled = byKey["voice.profile_id"].disabled;
      $("cancelPreview").classList.add("hidden");
    }
  }

  // ---- expert tab ----

  let expertSession = null;
  async function newSession() {
    showError("expertError", null);
    try {
      expertSession = (await api("POST", "/expert/sessions")).session_id;
      $("sessionId").textContent = "session " + expertSession;
      $("expertLog").textContent = "";
      $("expertLog").classList.add("hidden");
    } catch (e) { expertSession = null; showError("expertError", e); }
  }

  async function ask() {
    const q = $("question").value.trim();
    if (!q) return;
    showError("expertError", null);
    if (!expertSession) { await newSession(); if (!expertSession) return; }
    const log = $("expertLog"); log.classList.remove("hidden");
    log.append(el("p", {}, [el("span", { class: "who", textContent: "You: " }), q]));
    $("ask").disabled = true;
    try {
      const r = await api("POST", "/expert/sessions/" + encodeURIComponent(expertSession) + "/ask", { question: q });
      const p = el("p", {}, [el("span", { class: "who", textContent: "Expert: " }), r.answer]);
      if (r.citations.length) {
        p.append(el("div", { class: "notice mono", textContent: r.citations.map((c) => "[" + c.id + "] " + c.title + " (" + c.source + "@" + c.sha + ")").join("\n") }));
      }
      log.append(p);
      $("question").value = "";
    } catch (e) { showError("expertError", e); }
    finally { $("ask").disabled = false; }
  }

  // ---- wiring ----

  function selectTab(which) {
    const wb = which === "wb";
    $("pane-wb").classList.toggle("hidden", !wb);
    $("pane-expert").classList.toggle("hidden", wb);
    $("tab-wb").setAttribute("aria-selected", String(wb));
    $("tab-expert").setAttribute("aria-selected", String(!wb));
  }

  async function init() {
    $("apikey").value = getKey();
    $("apikeyOn").checked = apiKeyMode();
    if (apiKeyMode()) $("keyMode").open = true;
    const me = await initSession();
    if (!me) return;
    ready = true;
    showError("globalError", null);
    try { await loadTemplates(); } catch (e) { showError("globalError", e); return; }
    await loadVoices();
    await refreshIdentities();
  }

  $("savekey").onclick = () => {
    try { setKey($("apikey").value.trim()); setApiKeyMode($("apikeyOn").checked); } catch (e) { showError("globalError", e); return; }
    location.reload();
  };
  $("apikeyOn").onchange = () => {
    try { setApiKeyMode($("apikeyOn").checked); } catch (e) { showError("globalError", e); return; }
    location.reload();
  };
  $("archive").onclick = askArchive;
  $("archiveYes").onclick = doArchive;
  $("archiveNo").onclick = () => $("archiveConfirm").classList.add("hidden");
  $("template").onchange = onTemplateChange;
  $("create").onclick = create;
  $("save").onclick = save;
  $("launch").onclick = () => runtimeAction("launch");
  $("stop").onclick = () => runtimeAction("stop");
  $("test").onclick = () => { if (state.identity) window.open("/talk.html?identity=" + encodeURIComponent(state.identity.identity_id), "_blank"); };
  $("reset").onclick = resetForm;
  $("refresh").onclick = refreshIdentities;
  $("preview").onclick = preview;
  $("cancelPreview").onclick = () => { if (state.previewAbort) state.previewAbort.abort(); };
  $("tab-wb").onclick = () => selectTab("wb");
  $("tab-expert").onclick = () => selectTab("expert");
  $("newSession").onclick = newSession;
  $("ask").onclick = ask;

  pollWhileVisible(refreshIdentities, 5000);
  init();
})();
