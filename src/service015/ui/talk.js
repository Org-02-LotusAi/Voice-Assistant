// Talk page: mint a join token via the public API, join the Identity's room with livekit-client, publish the mic.
(function () {
  const { api, describe, el, getKey, pollWhileVisible } = window.S015;
  const $ = (id) => document.getElementById(id);
  const identityId = new URLSearchParams(location.search).get("identity");

  let room = null;
  const segments = new Map();   // transcription segment id -> <p>

  function showError(e) {
    const box = $("error");
    if (!e) { box.classList.add("hidden"); box.textContent = ""; return; }
    box.textContent = describe(e);
    box.classList.remove("hidden");
  }

  function setConn(text) { $("conn").textContent = text; }

  async function refreshPresence() {
    if (!identityId || !getKey()) return;
    try {
      const st = await api("GET", "/identities/" + encodeURIComponent(identityId) + "/status");
      const p = $("presence");
      p.textContent = st.presence; p.className = "pill " + st.presence;
      $("presenceDetail").textContent = st.last_error || st.status_detail || "";
    } catch (e) { showError(e); }
  }

  function addTranscript(segmentId, who, text, final) {
    const log = $("transcript");
    if (!segments.size) log.textContent = "";
    let p = segments.get(segmentId);
    if (!p) {
      p = el("p", {}, [el("span", { class: "who" }), el("span", { class: "text" })]);
      segments.set(segmentId, p);
      log.append(p);
    }
    p.querySelector(".who").textContent = who + ": ";
    p.querySelector(".text").textContent = text;
    p.style.opacity = final ? "1" : "0.7";
    log.scrollTop = log.scrollHeight;
  }

  function hookTranscription(r) {
    // The agent publishes transcription as text streams on topic "lk.transcription". Optional: never blocks the call.
    if (typeof r.registerTextStreamHandler !== "function") return;
    r.registerTextStreamHandler("lk.transcription", async (reader, participantInfo) => {
      const attrs = (reader.info && reader.info.attributes) || {};
      const segId = attrs["lk.segment_id"] || (reader.info && reader.info.id) || String(Math.random());
      const final = attrs["lk.transcription_final"] === "true";
      const who = participantInfo && participantInfo.identity === identityId ? "Identity" : "You";
      let text = "";
      try {
        for await (const chunk of reader) { text += chunk; addTranscript(segId, who, text, false); }
        addTranscript(segId, who, text, true);
      } catch (e) {
        addTranscript(segId, who, text + " [transcription stream error: " + describe(e) + "]", true);
      }
    });
  }

  async function connect() {
    showError(null);
    if (!identityId) { showError(new Error("Missing ?identity=<id> in the URL.")); return; }
    if (!window.LivekitClient) { showError(new Error("livekit-client failed to load from cdn.jsdelivr.net.")); return; }
    if (!window.isSecureContext || !navigator.mediaDevices) { $("secureWarn").classList.remove("hidden"); return; }
    $("connect").disabled = true; setConn("requesting token...");
    try {
      const jt = await api("POST", "/identities/" + encodeURIComponent(identityId) + "/join-token", {});
      const { Room, RoomEvent, Track } = window.LivekitClient;
      room = new Room({ adaptiveStream: true, dynacast: true });
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio) return;
        const a = track.attach();   // <audio> element
        a.autoplay = true;
        $("audioSink").append(a);
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => { track.detach().forEach((n) => n.remove()); });
      room.on(RoomEvent.ConnectionStateChanged, (s) => setConn(s));
      room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
        if (!room.canPlaybackAudio) setConn("connected - click Connect area to enable audio");
      });
      room.on(RoomEvent.Disconnected, () => { setConn("disconnected"); });
      hookTranscription(room);
      setConn("connecting...");
      await room.connect(jt.livekit_url, jt.token);
      await room.startAudio();
      await room.localParticipant.setMicrophoneEnabled(true);
      $("hangup").disabled = false;
      refreshPresence();
    } catch (e) {
      showError(e);
      if (room) { try { await room.disconnect(); } catch (_) { /* already failing; the original error is shown */ } room = null; }
      setConn("disconnected");
      $("connect").disabled = false;
    }
  }

  function fmt(v) { return v === null || v === undefined ? "-" : String(Math.round(v)); }

  async function showLatency() {
    try {
      const r = await api("GET", "/identities/" + encodeURIComponent(identityId) + "/latency");
      const rows = [["t0_t4", "user stop to agent speaks"], ["t0_t1", "user stop to final transcript"], ["t1_t2", "transcript to first LLM chunk"],
        ["t2_t3", "LLM to first TTS audio"], ["t3_t4", "TTS audio to agent speaks"], ["t4_t5", "agent speaks to heard"]];
      const t = el("table", {}, [el("tr", {}, ["Interval", "n", "P50", "P95", "max"].map((h) => el("th", { textContent: h })))]);
      for (const [k, label] of rows) {
        const s = r[k];
        t.append(el("tr", {}, [label, s.count, fmt(s.p50), fmt(s.p95), fmt(s.max)].map((c) => el("td", { textContent: String(c) }))));
      }
      const box = $("latency"); box.textContent = "";
      box.append(el("p", { class: "notice", textContent: "Turns recorded: " + r.turns }), t);
      $("latencyPanel").classList.remove("hidden");
    } catch (e) { showError(e); }
  }

  async function hangup() {
    $("hangup").disabled = true;
    try { if (room) await room.disconnect(); } catch (e) { showError(e); }
    room = null; setConn("disconnected");
    $("connect").disabled = false;
    await showLatency();
  }

  $("who").textContent = identityId || "(no identity)";
  $("connect").onclick = connect;
  $("hangup").onclick = hangup;
  if (!window.isSecureContext) $("secureWarn").classList.remove("hidden");
  pollWhileVisible(refreshPresence, 5000);
  refreshPresence();
})();
