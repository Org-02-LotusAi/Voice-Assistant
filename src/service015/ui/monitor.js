// Read-only roster / status / latency view (viewer role lands here; every role may open it).
(function () {
  const { api, el, showError, init, pollWhileVisible } = window.S015;
  const $ = (id) => document.getElementById(id);
  let ready = false, selected = null, busy = false;

  const fmt = (v) => (v === null || v === undefined ? "-" : String(Math.round(v)));

  async function showLatency(id) {
    selected = id;
    try {
      const r = await api("GET", "/identities/" + encodeURIComponent(id) + "/latency");
      const rows = [["t0_t4", "user stop to agent speaks"], ["t0_t1", "user stop to final transcript"], ["t1_t2", "transcript to first LLM chunk"],
        ["t2_t3", "LLM to first TTS audio"], ["t3_t4", "TTS audio to agent speaks"], ["t4_t5", "agent speaks to heard"]];
      const t = el("table", {}, [el("tr", {}, ["Interval", "n", "P50", "P95", "max"].map((h) => el("th", { textContent: h })))]);
      for (const [k, label] of rows) {
        const s = r[k];
        t.append(el("tr", {}, [label, s.count, fmt(s.p50), fmt(s.p95), fmt(s.max)].map((c) => el("td", { textContent: String(c) }))));
      }
      const box = $("latency"); box.textContent = "";
      box.append(el("p", { class: "mono notice", textContent: id + " - turns recorded: " + r.turns }), t);
    } catch (e) { showError("globalError", e); }
  }

  async function refresh() {
    if (!ready || busy) return;
    busy = true;
    try {
      const list = await api("GET", "/meeting/participants");
      const box = $("roster"); box.textContent = "";
      if (!list.length) box.append(el("div", { class: "notice", textContent: "No personas." }));
      for (const p of list) {
        const row = el("div", { class: "ident" }, [
          el("div", { class: "row" }, [
            el("strong", { textContent: p.participant_identity }),
            el("span", { class: "pill " + p.presence, textContent: p.presence }),
            el("button", { type: "button", textContent: "Latency", onclick: () => showLatency(p.identity_id) }),
          ]),
          el("div", { class: "mono notice", textContent: p.identity_id + " - room " + p.default_room }),
        ]);
        if (p.last_error || p.status_detail) row.append(el("div", { class: p.last_error ? "error" : "notice", textContent: p.last_error || p.status_detail }));
        box.append(row);
      }
      showError("globalError", null);
    } catch (e) { showError("globalError", e); }
    finally { busy = false; }
  }

  $("refresh").onclick = refresh;
  pollWhileVisible(refresh, 5000);
  init({ anyRole: true }).then((me) => { if (me) { ready = true; refresh(); } });
})();
