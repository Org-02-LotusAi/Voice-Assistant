// My voices: list shared + own, preview, upload with consent.
(function () {
  const { api, el, showError, init } = window.S015;
  const $ = (id) => document.getElementById(id);

  function renderVoices(boxId, list, emptyText) {
    const box = $(boxId); box.textContent = "";
    if (!list.length) box.append(el("div", { class: "notice", textContent: emptyText }));
    for (const v of list) {
      box.append(el("div", { class: "ident" }, [
        el("div", { class: "row" }, [
          el("strong", { textContent: v.name }), el("span", { class: "pill", textContent: v.language }),
          el("span", { class: "pill " + (v.authorized ? "READY" : "ERROR"), textContent: v.authorized ? "authorized" : "not authorized" }),
          el("button", { type: "button", textContent: "Preview", disabled: !v.authorized, onclick: () => preview(v) }),
        ]),
        el("div", { class: "mono notice", textContent: v.voice_profile_id }),
      ]));
    }
  }

  let catalog = false;  // Deepgram mode: a fixed catalog, no uploads

  function applyMode(list) {
    catalog = list.some((v) => v.engine === "deepgram-aura-2");
    $("uploadSection").classList.toggle("hidden", catalog);
    $("deepgramNote").classList.toggle("hidden", !catalog);
    $("mineTitle").classList.toggle("hidden", catalog);
    $("mineNote").classList.toggle("hidden", catalog);
    $("mine").classList.toggle("hidden", catalog);
    $("sharedTitle").textContent = catalog ? "Deepgram Aura-2 voices" : "Shared voices";
    $("sharedNote").textContent = catalog ? "Stock voices; every account can use them. Preview takes about a second."
      : "Provided by the administrator; every account can use them.";
  }

  async function load() {
    try {
      const list = await api("GET", "/voices");
      applyMode(list);
      renderVoices("mine", list.filter((v) => v.owned_by_me), "You have not uploaded a voice yet.");
      renderVoices("shared", list.filter((v) => !v.owned_by_me), "No shared voices.");
      showError("globalError", null);
    } catch (e) { showError("globalError", e); }
  }

  async function preview(v) {
    showError("globalError", null);
    $("previewState").textContent = "Synthesizing preview for " + v.name + (catalog ? "..." : " - this takes 15-40 s on CPU...");
    document.querySelectorAll("#mine button, #shared button").forEach((b) => { b.disabled = true; });
    try {
      const blob = await api("POST", "/voices/" + encodeURIComponent(v.voice_profile_id) + "/preview", {}, { blob: true });
      const a = $("previewAudio");
      if (a.src) URL.revokeObjectURL(a.src);
      a.src = URL.createObjectURL(blob);
      a.classList.remove("hidden");
      $("previewState").textContent = "";
      a.play().catch(() => { $("previewState").textContent = "Preview ready; press play."; });
    } catch (e) { $("previewState").textContent = ""; showError("globalError", e); }
    finally { await load(); }
  }

  async function upload(ev) {
    ev.preventDefault();
    showError("formError", null); $("formMsg").textContent = "";
    const f = $("v-file").files[0];
    if (!f) { showError("formError", new Error("Choose a reference audio file.")); return; }
    const fd = new FormData();
    fd.append("name", $("v-name").value.trim());
    fd.append("language", $("v-language").value.trim() || "en");
    fd.append("description", $("v-desc").value.trim());
    fd.append("reference_text", $("v-text").value.trim());
    fd.append("consent_statement", $("v-consent").value.trim());
    fd.append("subject_name", $("v-subject").value.trim());
    fd.append("authorized_by", $("v-by").value.trim());
    fd.append("file", f, f.name);
    $("upload").disabled = true;
    $("formMsg").textContent = "Uploading and cloning - this can take a while...";
    try {
      const v = await api("POST", "/voices", fd);
      $("formMsg").textContent = "Created voice " + v.name + " (" + v.voice_profile_id + ").";
      $("form").reset();
      $("v-by").value = authorizedBy;
      await load();
    } catch (e) { $("formMsg").textContent = ""; showError("formError", e); }
    finally { $("upload").disabled = false; }
  }

  let authorizedBy = "";
  $("form").onsubmit = upload;
  $("refresh").onclick = load;
  init().then((me) => {
    if (!me) return;
    authorizedBy = me.username || "";
    $("v-by").value = authorizedBy;
    load();
  });
})();
