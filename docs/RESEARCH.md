# Verified dependency facts (researched 2026-10-09 from current sources)

## Pins
- livekit-agents==1.8.3, livekit-plugins-openai==1.8.3, livekit-plugins-silero==1.8.3, livekit-plugins-turn-detector==1.8.3, livekit-api==1.2.1, livekit==1.1.20 (all >2 weeks old; py 3.10–3.14).
- moonshine-voice==0.1.5 (wheels: win_amd64, manylinux_2_34_x86_64; MIT).
- chatterbox-tts==0.1.7 — install with `--no-deps` (pins clash: torch==2.6, transformers==5.2.0, numpy<1.26).
- livekit-server Docker image: use a tag >2 weeks old (e.g. `livekit/livekit-server:v1.9.x` or the release preceding 1.13.9).
- Voicebox: github.com/jamiepine/voicebox — pin a commit SHA at clone time.

## Voicebox (headless backend)
- `python -m backend.main --host 127.0.0.1 --port 17493 --data-dir <dir>` (ALWAYS pass --port; code default is 8000). Python 3.12 venv; `pip install -r backend/requirements.txt` then `pip install --no-deps chatterbox-tts`. Official Dockerfile (CPU/ROCm only; no CUDA image). On Windows: LuxTTS deps in requirements.txt need git + piper-phonemize find-links; `pedalboard<0.9.21` (AVX-512 SIGILL on Zen 2/3). If those fail, install only what chatterbox_turbo needs: conformer, diffusers, resemble-perth, s3tokenizer, pyloudnorm, omegaconf, librosa, safetensors.
- Profiles: `GET /profiles` → [VoiceProfileResponse{id,name,description,language,voice_type(cloned|preset|designed),preset_engine,preset_voice_id,default_engine,sample_count,...}], `GET /profiles/{id}`, `POST /profiles` (json name, description, language, voice_type, default_engine...), `POST /profiles/{id}/samples` multipart `file` + form `reference_text` (required), `GET /profiles/{id}/samples`, `GET /samples/{sample_id}` → audio/wav.
- Presets: `GET /profiles/presets/{engine}` — chatterbox_turbo has NO presets. Only CLONED profiles can use Turbo (presets are locked to their preset_engine).
- Generate: `POST /generate` async → poll `GET /generate/{id}/status` (SSE) → `GET /audio/{id}`. `POST /generate/stream` is NOT real streaming (renders full clip first). Engine id string `chatterbox_turbo`; model name `chatterbox-turbo`.
- Warm: `POST /models/load {"model_name":"chatterbox-turbo"}`, `GET /models/status`, `GET /health`.
- Generation is a serial queue (one inference at a time).

## Chatterbox Turbo
- `from chatterbox.tts_turbo import ChatterboxTurboTTS`; `ChatterboxTurboTTS.from_pretrained("cpu"|"cuda", nano=False)` (pass device as STRING — torch.device object breaks CPU load, issue #533); `nano=True` = 110M variant (~3x realtime on 8 CPU cores per README).
- `model.prepare_conditionals(wav_path)` once per voice, then `model.generate(text, temperature=0.8, top_k=1000, top_p=0.95, repetition_penalty=1.2, norm_loudness=True)` → tensor [1,N]; `model.sr == 24000`. Reference clip must be > 5 s. No native streaming → stream at sentence granularity. Instability past ~25–30 s output → chunk text. Perth watermark always applied.

## LiveKit Agents 1.8.3
- `AgentServer(ws_url, api_key, api_secret, num_idle_processes, setup_fnc, port, ...)`; `@server.rtc_session(on_request=..., on_session_end=...)` (one per server); run programmatically `await server.run(devmode=False, unregistered=False)`; `drain()`, `aclose()`. Agent name via env `LIVEKIT_AGENT_NAME` (non-empty ⇒ explicit dispatch only). Job processes are spawned (Windows spawn) — load VAD/Moonshine in `setup_fnc` (per process prewarm, stored in `proc.userdata`).
- Accept with fixed participant identity: `async def req(r: JobRequest): await r.accept(identity="ava-001", name="Ava")`.
- Dispatch: `api.LiveKitAPI(url,key,secret).agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(agent_name=..., room=..., metadata=json))`; `list_dispatch(room_name)`, `delete_dispatch(dispatch_id, room_name)`. Room auto-created. `room.remove_participant(api.RoomParticipantIdentity(room=, identity=))`. `room.create_room(api.CreateRoomRequest(name=, empty_timeout=, departure_timeout=))`.
- `Agent(instructions=...)`; `AgentSession(stt=, vad=, llm=, tts=, turn_handling={...})`; `await session.start(agent, room=ctx.room, room_options=room_io.RoomOptions(...))`; `session.say(text)` / `session.generate_reply()`.
- turn_handling dict keys: `turn_detection`, `endpointing{mode,min_delay=0.5,max_delay=3.0}`, `interruption{enabled=True, mode="adaptive"|"vad", min_duration=0.5, min_words=0, resume_false_interruption=True, false_interruption_timeout=2.0}`, `preemptive_generation` (default on), `user_turn_limit`. Flat `allow_interruptions` etc. are deprecated.
- LLM: `from livekit.plugins import openai; openai.LLM(model=..., base_url="https://api.groq.com/openai/v1", api_key=...)` (no `with_groq` in 1.8.3).
- VAD: `silero.VAD.load(min_silence_duration=0.55, ..., force_cpu=True)` (blocking → setup_fnc).
- Custom STT: subclass `stt.STT(capabilities=stt.STTCapabilities(streaming=True, interim_results=True, offline_recognize=False))`, implement `_recognize_impl` (may raise NotImplementedError) and `stream()` returning a `stt.RecognizeStream` subclass with `super().__init__(stt=, conn_options=, sample_rate=16000)` and `async def _run(self)` iterating `self._input_ch` (`rtc.AudioFrame` | `_FlushSentinel`), emitting `self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.X, alternatives=[stt.SpeechData(language=, text=)]))`; types START_OF_SPEECH, INTERIM_TRANSCRIPT, FINAL_TRANSCRIPT, END_OF_SPEECH.
- Custom TTS: `tts.TTS(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1)`, `synthesize(text, *, conn_options) -> ChunkedStream`; `ChunkedStream._run(self, output_emitter)`: `output_emitter.initialize(request_id=, sample_rate=24000, num_channels=1, mime_type="audio/pcm")`, `push(bytes)` (s16le), `flush()`. Framework wraps non-streaming TTS in a sentence StreamAdapter.
- Metrics: `session.on("metrics_collected")` (EOUMetrics end_of_utterance_delay/transcription_delay, LLMMetrics.ttft, TTSMetrics.ttfb); `agent_state_changed` new_state "speaking" ≈ first audio frame out (T4); `user_state_changed` speaking→listening (T0); `user_input_transcribed` is_final (T1); `conversation_item_added` assistant message `.metrics` has e2e_latency etc.
- Moonshine STT must not block the loop: run `add_audio` in a dedicated thread / `asyncio.to_thread`.

## Moonshine (moonshine-voice 0.1.5)
- `from moonshine_voice import Transcriber, ModelArch, get_model_for_language` (verify exact import paths in the installed package). `get_model_for_language("en", ModelArch.SMALL_STREAMING) -> (path, arch)` downloads to cache. `Transcriber(model_path, model_arch, update_interval=0.5)`; `stream = t.create_stream()`; `stream.start()`; `stream.add_audio(list_of_float_-1..1, sample_rate=16000)`; listeners receive `LineStarted/LineUpdated/LineTextChanged/LineCompleted` events with `.line.text`, `.line.is_complete`; `stream.stop()/close()`. Arch: TINY_STREAMING(69ms), SMALL_STREAMING(165ms), MEDIUM_STREAMING(269ms) on Linux x86. No LiveKit plugin exists → write one.

## LiveKit SIP (Service 014 territory — reference only)
- Outbound: `lk.sip.create_sip_participant(api.CreateSIPParticipantRequest(sip_trunk_id, sip_call_to, room_name, participant_identity, participant_name, wait_until_answered, ...))`.
- Inbound: `SIPDispatchRuleDirect(room_name=...)` routes calls into a named room. Requires the separate `livekit/sip` service + shared Redis + public IP. `livekit-server --dev` alone cannot do SIP.

## LiveKit dev server (Windows Docker Desktop)
- `docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server:<tag> --dev --bind 0.0.0.0 --node-ip 127.0.0.1 --udp-port 7882` — keys devkey/secret. Use single UDP mux port (Docker Desktop handles ranges badly). No host networking on Docker Desktop.
- Token: `api.AccessToken(key, secret).with_identity(..).with_name(..).with_grants(api.VideoGrants(room_join=True, room=...)).to_jwt()`.
- Browser test client: meet.livekit.io/?tab=custom (serverUrl + token) — or our own page using livekit-client JS from a CDN.
