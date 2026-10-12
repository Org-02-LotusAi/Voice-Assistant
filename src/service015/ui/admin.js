// Admin console. Everything goes through the public admin API; secrets (temporary passwords, token secrets) are shown once
// in the page, never stored by it, and cleared on dismiss or tab switch.
(function () {
  const { api, el, showError, init, pollWhileVisible } = window.S015;
  const $ = (id) => document.getElementById(id);
  const TABS = ["users", "tokens", "templates", "voices", "personas", "expert"];
  const loaded = {};
  let usersById = {};
  let rosterBusy = false;

  const when = (v) => (v ? v.replace("T", " ").slice(0, 19) : "-");
  const nameOf = (id) => (id ? (usersById[id] ? usersById[id].username : id) : "-");
  const cell = (c, cls) => el("td", cls ? { class: cls } : {}, [typeof c === "object" && c !== null ? c : document.createTextNode(String(c))]);

  function table(headers, rows) {
    const t = el("table", {}, [el("tr", {}, headers.map((h) => el("th", { class: "l", textContent: h })))]);
    for (const r of rows) t.append(el("tr", {}, r.map((c) => cell(c, "l"))));
    return t;
  }
  const btn = (text, onclick, cls) => el("button", { type: "button", textContent: text, onclick, class: cls || "" });
  const pill = (text, cls) => el("span", { class: "pill " + (cls || ""), textContent: text });

  // ---- in-page confirmation (no browser dialogs) ----
  let confirmAction = null, confirmReturn = null;
  function ask(text, action) {
    confirmAction = action; confirmReturn = document.activeElement;
    $("confirmText").textContent = text;
    $("confirm").classList.remove("hidden");
    $("confirmNo").focus();
  }
  function closeConfirm() {
    $("confirm").classList.add("hidden"); confirmAction = null;
    if (confirmReturn && document.contains(confirmReturn)) confirmReturn.focus();
    confirmReturn = null;
  }
  $("confirmNo").onclick = closeConfirm;
  $("confirmYes").onclick = async () => {
    const act = confirmAction; closeConfirm();
    if (!act) return;
    try { await act(); showError("globalError", null); } catch (e) { showError("globalError", e); }
  };
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("confirm").classList.contains("hidden")) closeConfirm(); });

  // ---- show-once secret box with Copy ----
  function showSecret(boxId, heading, value) {
    const box = $(boxId); box.textContent = "";
    const status = el("span", { class: "notice" });
    box.append(
      el("strong", { textContent: heading }),
      el("div", { class: "mono", textContent: value }),
      el("div", { class: "row spaced-sm" }, [
        btn("Copy", async () => {
          try { await navigator.clipboard.writeText(value); status.textContent = "Copied."; }
          catch (e) { status.textContent = "Copy failed - select the text and copy it manually."; }
        }),
        btn("Dismiss", () => { box.textContent = ""; box.classList.add("hidden"); }),
        status,
      ]),
      el("div", { class: "notice", textContent: "This is shown once. It cannot be retrieved again after you dismiss it or leave this tab." }),
    );
    box.classList.remove("hidden");
  }
  function clearSecrets() { for (const id of ["userSecret", "tokenSecret"]) { $(id).textContent = ""; $(id).classList.add("hidden"); } }

  function randomPassword() {
    const b = new Uint8Array(18); crypto.getRandomValues(b);
    return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  // ---- tabs ----
  function select(name) {
    clearSecrets();
    for (const t of TABS) {
      const on = t === name;
      $("tab-" + t).setAttribute("aria-selected", String(on));
      $("tab-" + t).tabIndex = on ? 0 : -1;
      $("pane-" + t).classList.toggle("hidden", !on);
    }
    if (!loaded[name]) { loaded[name] = true; LOADERS[name](); }
  }
  for (const t of TABS) {
    $("tab-" + t).onclick = () => select(t);
    $("tab-" + t).onkeydown = (ev) => {
      const i = TABS.indexOf(t), step = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      ev.preventDefault();
      const next = TABS[(i + step + TABS.length) % TABS.length];
      select(next); $("tab-" + next).focus();
    };
  }

  async function guarded(fn, errBox) {
    try { await fn(); showError(errBox || "globalError", null); } catch (e) { showError(errBox || "globalError", e); }
  }

  // ---- users ----
  async function loadUsers() {
    await guarded(async () => {
      const list = await api("GET", "/admin/users");
      usersById = Object.fromEntries(list.map((u) => [u.user_id, u]));
      const rows = list.map((u) => {
        const actions = [];
        if (u.role !== "admin") {
          actions.push(btn(u.disabled ? "Enable" : "Disable", () => ask(
            (u.disabled ? "Enable " : "Disable ") + u.username + "?" + (u.disabled ? "" : " Their sessions end immediately and they cannot sign in."),
            async () => { await api("PATCH", "/admin/users/" + encodeURIComponent(u.user_id), { disabled: !u.disabled }); await loadUsers(); })));
          const sel = el("select", { "aria-label": "Role of " + u.username }, ["user", "viewer"].map((r) => el("option", { value: r, textContent: r })));
          sel.value = u.role;
          sel.onchange = () => guarded(async () => {
            try { await api("PATCH", "/admin/users/" + encodeURIComponent(u.user_id), { role: sel.value }); }
            finally { await loadUsers(); }
          });
          actions.push(sel);
          actions.push(btn("Reset password", () => ask(
            "Reset the password of " + u.username + "? A new temporary password is generated; their sessions end and they must change it at next sign-in.",
            async () => {
              const pw = randomPassword();
              await api("PATCH", "/admin/users/" + encodeURIComponent(u.user_id), { temporary_password: pw });
              showSecret("userSecret", "Temporary password for " + u.username, pw);
              await loadUsers();
            })));
        } else actions.push(el("span", { class: "notice", textContent: "bootstrap administrator" }));
        return [u.username, u.role, u.disabled ? pill("disabled", "disabled") : pill("active", "READY"),
          u.must_change_password ? "must change password" : "-", when(u.last_login_at), el("div", { class: "row" }, actions)];
      });
      const box = $("users"); box.textContent = "";
      box.append(table(["Username", "Role", "State", "Password", "Last login", "Actions"], rows));
    });
  }
  $("u-gen").onclick = () => { $("u-pw").value = randomPassword(); };
  $("userForm").onsubmit = async (ev) => {
    ev.preventDefault(); showError("userError", null);
    $("u-create").disabled = true;
    const username = $("u-name").value.trim().toLowerCase(), pw = $("u-pw").value;
    try {
      await api("POST", "/admin/users", { username, temporary_password: pw, role: $("u-role").value });
      showSecret("userSecret", "Temporary password for " + username, pw);
      $("u-name").value = ""; $("u-pw").value = "";
      await loadUsers();
    } catch (e) { showError("userError", e); }
    finally { $("u-create").disabled = false; }
  };

  // ---- service tokens ----
  async function loadTokens() {
    await guarded(async () => {
      const list = await api("GET", "/admin/tokens");
      const rows = list.map((t) => [t.label, t.role, when(t.created_at), when(t.last_used_at),
        t.revoked_at ? pill("revoked " + when(t.revoked_at), "ERROR")
          : btn("Revoke", () => ask("Revoke token \"" + t.label + "\"? Machines using it stop working immediately.",
            async () => { await api("DELETE", "/admin/tokens/" + encodeURIComponent(t.token_id)); await loadTokens(); }), "danger")]);
      const box = $("tokens"); box.textContent = "";
      box.append(table(["Label", "Role", "Created", "Last used", "State"], rows));
    });
  }
  $("tokenForm").onsubmit = async (ev) => {
    ev.preventDefault(); showError("tokenError", null);
    $("t-create").disabled = true;
    try {
      const r = await api("POST", "/admin/tokens", { label: $("t-label").value.trim(), role: $("t-role").value });
      showSecret("tokenSecret", "Token secret for \"" + r.label + "\" (" + r.role + ")", r.token);
      $("t-label").value = "";
      await loadTokens();
    } catch (e) { showError("tokenError", e); }
    finally { $("t-create").disabled = false; }
  };

  // ---- templates ----
  let editing = null;
  const SKELETON = {
    template_id: "new-template", name: "Name", description: "What this template is for.",
    default_self_facts: { role: null, company: null, language: "en-US" },
    persona: { objective: "Objective.", style: "Style.", instructions: "Instructions. May use {name}, {company}, {role}." },
    conversation: { greeting: "Hello, this is {name}.", barge_in: true, speaking_style: "Short sentences." },
    voice: { default_voice_profile_id: null, engine: "chatterbox-turbo" },
    required_instance_fields: ["name"],
    allowed_overrides: ["name", "company", "role", "language", "greeting", "voice.profile_id"],
  };
  function openEditor(body, title) {
    editing = body.template_id;
    $("tplEdTitle").textContent = title;
    $("tplJson").value = JSON.stringify(body, null, 2);
    $("tplMsg").textContent = ""; showError("tplError", null);
    $("tplEditor").classList.remove("hidden"); $("tplEditor").scrollIntoView();
  }
  function templateRow(s, shared) {
    const head = [el("strong", { textContent: s.name }), pill("v" + s.latest_version)];
    if (s.disabled) head.push(pill("disabled", "ERROR"));
    if (!shared) head.push(pill("owner: " + nameOf(s.owner_user_id)));
    if (shared) head.push(btn("New version", () => guarded(async () => {
      const t = await api("GET", "/templates/" + encodeURIComponent(s.template_id));
      const { version, created_at, ...rest } = t;
      openEditor(rest, "New version of " + s.template_id + " (latest v" + version + ")");
    })));
    head.push(btn(s.disabled ? "Enable" : "Disable", () => ask(
      (s.disabled ? "Enable " : "Disable ") + s.template_id + "?" + (s.disabled ? "" : " New personas cannot be created from it; existing personas are unaffected."),
      async () => { await api("PATCH", "/templates/" + encodeURIComponent(s.template_id), { disabled: !s.disabled }); await loadTemplates(); })));
    return el("div", { class: "ident" }, [el("div", { class: "row" }, head),
      el("div", { class: "notice", textContent: s.description }), el("div", { class: "mono notice", textContent: s.template_id })]);
  }
  async function loadTemplates() {
    await guarded(async () => {
      if (!Object.keys(usersById).length) usersById = Object.fromEntries((await api("GET", "/admin/users")).map((u) => [u.user_id, u]));
      const list = await api("GET", "/templates");
      const fill = (id, items, empty, shared) => {
        const box = $(id); box.textContent = "";
        if (!items.length) box.append(el("div", { class: "notice", textContent: empty }));
        items.forEach((s) => box.append(templateRow(s, shared)));
      };
      fill("tplShared", list.filter((s) => s.shared), "No shared templates.", true);
      fill("tplPrivate", list.filter((s) => !s.shared), "No private templates.", false);
      const g = await api("GET", "/guardrails");
      const ul = $("adminGuardrails"); ul.textContent = "";
      g.rules.forEach((r) => ul.append(el("li", { textContent: r })));
    });
  }
  $("tplNew").onclick = () => openEditor(SKELETON, "New shared template");
  $("tplClose").onclick = () => $("tplEditor").classList.add("hidden");
  $("tplSave").onclick = async () => {
    showError("tplError", null); $("tplMsg").textContent = "";
    let body;
    try { body = JSON.parse($("tplJson").value); } catch (e) { showError("tplError", new Error("The template body is not valid JSON: " + e.message)); return; }
    $("tplSave").disabled = true;
    try {
      const t = await api("POST", "/templates", body);
      $("tplMsg").textContent = "Saved " + t.template_id + " v" + t.version + ".";
      await loadTemplates();
    } catch (e) { showError("tplError", e); }
    finally { $("tplSave").disabled = false; }
  };

  // ---- voices ----
  async function loadVoices() {
    await guarded(async () => {
      if (!Object.keys(usersById).length) usersById = Object.fromEntries((await api("GET", "/admin/users")).map((u) => [u.user_id, u]));
      const list = await api("GET", "/admin/voices");
      const box = $("voices"); box.textContent = "";
      if (!list.length) box.append(el("div", { class: "notice", textContent: "No cloned voices." }));
      for (const v of list) {
        const head = [el("strong", { textContent: v.name }), pill(v.language),
          pill(v.shared ? "shared" : "owner: " + nameOf(v.owner_user_id)),
          pill(v.authorized ? "authorized" : "not authorized", v.authorized ? "READY" : "ERROR")];
        if (v.disabled) head.push(pill("disabled", "ERROR"));
        if (v.authorized) head.push(btn(v.disabled ? "Enable" : "Disable", () => ask(
          (v.disabled ? "Enable " : "Disable ") + v.name + "?" + (v.disabled ? "" : " It cannot be attached to new personas or launched; running personas are not interrupted."),
          async () => { await api("PATCH", "/voices/" + encodeURIComponent(v.voice_profile_id), { disabled: !v.disabled }); await loadVoices(); })));
        const c = v.consent;
        const consent = c
          ? el("details", {}, [el("summary", { textContent: "Consent record" }), el("div", { class: "notice" }, [
            el("div", { textContent: "Subject: " + c.subject_name }), el("div", { textContent: "Authorized by: " + c.authorized_by }),
            el("div", { textContent: "Source: " + c.source + " - " + when(c.created_at) }),
            el("div", { textContent: "Statement: " + c.consent_statement })])])
          : el("div", { class: "notice", textContent: "No consent record." });
        box.append(el("div", { class: "ident" }, [el("div", { class: "row" }, head), el("div", { class: "mono notice", textContent: v.voice_profile_id }), consent]));
      }
    });
  }

  // ---- personas ----
  let presence = {};
  function ownerPicker(p) {
    const sel = el("select", { "aria-label": "New owner of " + p.name }, [el("option", { value: "", textContent: "(nobody)" })]);
    Object.values(usersById).forEach((u) =>
      sel.append(el("option", { value: u.user_id, textContent: u.username + " (" + u.role + ")" })));
    sel.value = p.owner_user_id || "";
    const clear = el("input", { type: "checkbox" });
    const apply = btn("Assign owner", () => guarded(async () => {
      const owner = sel.value || null;
      await api("PATCH", "/admin/identities/" + encodeURIComponent(p.identity_id), { owner_user_id: owner, clear_voice: clear.checked });
      await loadPersonas();
    }));
    return el("div", { class: "spaced-sm" }, [
      el("div", { class: "row" }, [el("span", { class: "notice", textContent: "Owner" }), sel, apply]),
      el("label", { class: "notice" }, [clear,
        " Also detach the persona's voice if the new owner cannot use it. Without this the hand-over is refused (409 voice_not_accessible) when the voice is private to someone else; with it the persona has no voice and needs one before it can launch."]),
    ]);
  }
  async function loadPersonas() {
    await guarded(async () => {
      if (!Object.keys(usersById).length) usersById = Object.fromEntries((await api("GET", "/admin/users")).map((u) => [u.user_id, u]));
      const [list, roster] = await Promise.all([
        api("GET", "/admin/identities?include_archived=" + ($("p-archived").checked ? "true" : "false")),
        api("GET", "/meeting/participants"),
      ]);
      presence = Object.fromEntries(roster.map((r) => [r.identity_id, r]));
      const box = $("personas"); box.textContent = "";
      if (!list.length) box.append(el("div", { class: "notice", textContent: "No personas." }));
      for (const p of list) {
        const r = presence[p.identity_id];
        const head = [el("strong", { textContent: p.name }), pill(p.template_id + " v" + p.template_version), pill("owner: " + nameOf(p.owner_user_id))];
        if (p.archived_at) head.push(pill("archived " + when(p.archived_at), "archived"));
        else if (r) {
          head.push(pill(r.presence, r.presence));
          if (r.presence !== "OFFLINE") head.push(btn("Stop", () => stopPersona(p.identity_id, p.name), "danger"));
        }
        box.append(el("div", { class: "ident" }, [el("div", { class: "row" }, head),
          el("div", { class: "mono notice", textContent: p.identity_id + " - voice " + (p.voice_profile_id || "none") }), ownerPicker(p)]));
      }
    });
  }
  function stopPersona(id, name) {
    ask("Stop " + name + " (" + id + ")? The running agent leaves its room.", async () => {
      await api("POST", "/identities/" + encodeURIComponent(id) + "/stop");
      await Promise.all([refreshRoster(), loaded.personas ? loadPersonas() : Promise.resolve()]);
    });
  }
  $("p-archived").onchange = loadPersonas;

  // ---- roster ----
  async function refreshRoster() {
    if (rosterBusy) return;
    rosterBusy = true;
    try {
      const [list, ids] = await Promise.all([api("GET", "/meeting/participants"), api("GET", "/admin/identities")]);
      const byId = Object.fromEntries(ids.map((i) => [i.identity_id, i]));
      const box = $("roster"); box.textContent = "";
      if (!list.length) box.append(el("div", { class: "notice", textContent: "No personas." }));
      for (const p of list) {
        const row = el("div", { class: "ident" }, [el("div", { class: "row" }, [
          el("strong", { textContent: p.participant_identity }), pill(p.presence, p.presence),
          pill("owner: " + nameOf(byId[p.identity_id] && byId[p.identity_id].owner_user_id)),
          p.presence !== "OFFLINE" ? btn("Stop", () => stopPersona(p.identity_id, p.participant_identity), "danger") : document.createTextNode(""),
        ]), el("div", { class: "mono notice", textContent: p.identity_id + " - room " + p.default_room })]);
        if (p.last_error || p.status_detail) row.append(el("div", { class: p.last_error ? "error" : "notice", textContent: p.last_error || p.status_detail }));
        box.append(row);
      }
    } catch (e) { showError("globalError", e); }
    finally { rosterBusy = false; }
  }
  $("rosterRefresh").onclick = refreshRoster;

  // ---- service expert ----
  async function loadExpert() {
    await guarded(async () => {
      const k = await api("GET", "/expert/knowledge");
      const miss = $("expertMissing"); miss.textContent = "";
      if (k.missing_required.length) {
        miss.append(el("div", { class: "error", textContent: "Missing required knowledge (re-ingest or add it): " + k.missing_required.join(", ") }));
      } else miss.append(el("div", { class: "notice", textContent: "All required knowledge documents are present." }));
      const box = $("expertItems"); box.textContent = "";
      box.append(table(["Title", "Kind", "Source", "Visible to", "Commit", "Added"], k.items.map((i) =>
        [i.title, i.kind, i.source, i.min_role, i.sha.slice(0, 8) + (i.dirty ? " (dirty)" : ""), when(i.created_at)])));
    });
  }
  $("expertReingest").onclick = () => guarded(async () => {
    $("expertReingestResult").textContent = "Re-ingesting...";
    try {
      const r = await api("POST", "/expert/reingest");
      const n = Object.values(r.ingested).reduce((a, b) => a + b, 0);
      $("expertReingestResult").textContent = "Ingested " + n + " chunks from " + Object.keys(r.ingested).length + " documents at " +
        r.sha.slice(0, 8) + (r.dirty ? " (dirty tree)" : "") + "; skipped " + r.skipped.length +
        (r.missing_required.length ? "; missing required: " + r.missing_required.join(", ") : "") + ".";
    } catch (e) { $("expertReingestResult").textContent = ""; throw e; }
    await loadExpert();
  });
  $("expForm").onsubmit = async (ev) => {
    ev.preventDefault(); showError("expError", null); $("expMsg").textContent = "";
    $("x-add").disabled = true;
    try {
      const r = await api("POST", "/expert/experience", {
        title: $("x-title").value.trim(), body: $("x-body").value.trim(), source: $("x-source").value.trim(), min_role: $("x-role").value });
      $("expMsg").textContent = "Added \"" + r.title + "\" (commit " + r.sha.slice(0, 8) + ").";
      $("expForm").reset();
      await loadExpert();
    } catch (e) { showError("expError", e); }
    finally { $("x-add").disabled = false; }
  };

  const LOADERS = { users: loadUsers, tokens: loadTokens, templates: loadTemplates, voices: loadVoices, personas: loadPersonas, expert: loadExpert };
  $("usersRefresh").onclick = loadUsers; $("tokensRefresh").onclick = loadTokens; $("voicesRefresh").onclick = loadVoices;
  $("personasRefresh").onclick = loadPersonas; $("expertRefresh").onclick = loadExpert;

  init({ adminOnly: true }).then(async (me) => {
    if (!me) return;
    await loadUsers(); loaded.users = true;
    await refreshRoster();
    pollWhileVisible(refreshRoster, 5000);
  });
})();
