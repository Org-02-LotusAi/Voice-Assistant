// My templates: list, create, new version. Shared templates are read-only unless the caller is an admin.
(function () {
  const { api, el, showError, init, state } = window.S015;
  const $ = (id) => document.getElementById(id);
  const UNIVERSE = ["name", "company", "role", "language", "persona.instructions", "persona.objective", "persona.style",
    "greeting", "voice.profile_id", "conversation.speaking_style", "conversation.barge_in"];
  const LANGUAGES = ["en-US", "en-GB", "en-IN", "hi-IN"];
  let editing = null;   // { existing, editable, voiceDefault }

  function checks(boxId, selected) {
    const box = $(boxId); box.textContent = "";
    for (const k of UNIVERSE) {
      const cb = el("input", { type: "checkbox", value: k, checked: selected.includes(k) });
      box.append(el("label", {}, [cb, " " + k]));
    }
  }
  const picked = (boxId) => Array.from($(boxId).querySelectorAll("input:checked")).map((i) => i.value);

  function setLanguage(v) {
    const s = $("t-language"); s.textContent = "";
    const all = LANGUAGES.slice(); if (v && !all.includes(v)) all.push(v);
    for (const l of all) s.append(el("option", { value: l, textContent: l }));
    s.value = v || LANGUAGES[0];
  }

  function fill(t) {
    // A user's private ids are stored as "<username>.<id>"; the API adds that prefix itself, so the form edits the bare id.
    const me = state.me, prefix = me.kind === "user" && me.effective_role !== "admin" ? me.username + "." : "";
    $("t-id").value = t ? (prefix && t.template_id.startsWith(prefix) ? t.template_id.slice(prefix.length) : t.template_id) : "";
    $("t-name").value = t ? t.name : ""; $("t-desc").value = t ? t.description : "";
    $("t-role").value = t ? t.default_self_facts.role || "" : ""; $("t-company").value = t ? t.default_self_facts.company || "" : "";
    setLanguage(t ? t.default_self_facts.language : "");
    $("t-objective").value = t ? t.persona.objective : ""; $("t-style").value = t ? t.persona.style : "";
    $("t-instructions").value = t ? t.persona.instructions : "";
    $("t-greeting").value = t ? t.conversation.greeting : ""; $("t-speaking").value = t ? t.conversation.speaking_style : "";
    $("t-barge").checked = t ? t.conversation.barge_in !== false : true;
    checks("required", t ? t.required_instance_fields : ["name"]);
    checks("allowed", t ? t.allowed_overrides : UNIVERSE);
  }

  function setEditable(on) {
    document.querySelectorAll("#form input, #form textarea, #form select").forEach((n) => { n.disabled = !on; });
    $("t-id").disabled = !on || editing.existing;
    $("save").classList.toggle("hidden", !on);
  }

  function body() {
    const nul = (v) => (v.trim() === "" ? null : v.trim());
    return {
      template_id: $("t-id").value.trim(), name: $("t-name").value.trim(), description: $("t-desc").value.trim(),
      default_self_facts: { role: nul($("t-role").value), company: nul($("t-company").value), language: $("t-language").value },
      persona: { objective: $("t-objective").value.trim(), style: $("t-style").value.trim(), instructions: $("t-instructions").value.trim() },
      conversation: { greeting: $("t-greeting").value.trim(), barge_in: $("t-barge").checked, speaking_style: $("t-speaking").value.trim() },
      voice: { default_voice_profile_id: editing.voiceDefault || null, engine: "chatterbox-turbo" },
      required_instance_fields: picked("required"), allowed_overrides: picked("allowed"),
    };
  }

  function open(t, existing, editable) {
    editing = { existing, editable, voiceDefault: t ? t.voice.default_voice_profile_id : null };
    fill(t);
    setEditable(editable);
    $("edTitle").textContent = !existing ? "New template" : (editable ? "New version of " : "Viewing ") + t.template_id + " (latest v" + t.version + ")";
    $("formMsg").textContent = existing && !editable ? "Shared template: read-only." : "";
    showError("formError", null);
    $("editor").classList.remove("hidden");
    $("editor").scrollIntoView();
  }

  async function view(s) {
    try {
      const t = await api("GET", "/templates/" + encodeURIComponent(s.template_id));
      const mine = state.me.kind === "user" && s.owner_user_id === state.me.user_id;
      open(t, true, mine || state.me.effective_role === "admin");
    } catch (e) { showError("globalError", e); }
  }

  function renderList(boxId, list, emptyText, label) {
    const box = $(boxId); box.textContent = "";
    if (!list.length) { box.append(el("div", { class: "notice", textContent: emptyText })); return; }
    for (const s of list) {
      const head = [el("strong", { textContent: s.name }), el("span", { class: "pill", textContent: "v" + s.latest_version })];
      if (s.disabled) head.push(el("span", { class: "pill ERROR", textContent: "disabled" }));
      head.push(el("button", { type: "button", textContent: label, onclick: () => view(s) }));
      box.append(el("div", { class: "ident" }, [
        el("div", { class: "row" }, head),
        el("div", { class: "notice", textContent: s.description }),
        el("div", { class: "mono notice", textContent: s.template_id }),
      ]));
    }
  }

  async function load() {
    try {
      const list = await api("GET", "/templates");
      const me = state.me;
      const mine = list.filter((s) => me.kind === "user" && !s.shared && s.owner_user_id === me.user_id);
      renderList("mine", mine, "You have no private templates yet.", "New version");
      renderList("shared", list.filter((s) => !mine.includes(s)), "No shared templates.", me.effective_role === "admin" ? "New version" : "View");
      showError("globalError", null);
    } catch (e) { showError("globalError", e); }
  }

  async function save() {
    showError("formError", null);
    $("save").disabled = true;
    try {
      const t = await api("POST", "/templates", body());
      await load();
      open(t, true, true);
      $("formMsg").textContent = "Saved " + t.template_id + " v" + t.version + ".";
    } catch (e) { showError("formError", e); }
    finally { $("save").disabled = false; }
  }

  $("newTpl").onclick = () => open(null, false, true);
  $("save").onclick = save;
  $("close").onclick = () => $("editor").classList.add("hidden");
  init().then((me) => { if (me) load(); });
})();
