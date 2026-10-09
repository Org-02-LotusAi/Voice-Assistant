SAFI SERVICE 015

Human-Like Voice Identity Template Workbench

Reusable Archetypes → Persistent Customized AI Identities → LiveKit / Phone

SOW Date: October 8, 2026

# 1. Objective

Build a usable Safi Workbench for creating reusable Human-Like Voice Identity Templates (“archetypes”), instantiating persistent AI Identities from those Templates, customizing each Identity, and launching each Identity as a natural realtime voice Participant in its own Default LiveKit Room.

The product is not one hard-coded AI agent. The product manufactures persistent AI voice identities from reusable archetypes.

Each instantiated Identity must possess authoritative self-knowledge of its own instance-specific facts. If an Identity named Ava is asked “What is your name?”, it must answer “Ava” — never the Template name, another Identity’s name, or an invented answer.

# 2. Six Hours or Nothing

This is a SIX WORKING HOURS OR NOTHING assignment.

The developer shall understand → build → test → fix → deploy → commit → deliver within six working hours.

Architecture is already selected. Do not spend the assignment designing a generalized identity platform, another voice studio, another realtime media framework, or another SIP stack.

# 3. Finished Service — Observable Hour-6 Result

At Hour 6, a user shall be able to do this from the Workbench:

Choose archetype: Sales Representative
        ↓
Instantiate
        ↓
Customize name = Ava
Customize company / role / persona / greeting / voice
        ↓
Save persistent Identity
        ↓
Launch
        ↓
Ava joins Ava's Default LiveKit Room
        ↓
Human speaks with Ava naturally
        ↓
Human: “What is your name?”
Ava:   “My name is Ava.”
        ↓
Barge-in works
        ↓
Phone dogfood through existing Service 014

Then instantiate a second Identity from the same Template with a different name and voice. Both Identities must remain independent.

# 4. Governing Product Model

IDENTITY TEMPLATE / ARCHETYPE
        |
        | instantiate
        v
PERSISTENT IDENTITY INSTANCE
        |
        | launch / stop / relaunch
        v
RUNTIME LIVEKIT PARTICIPANT
        |
        v
DEFAULT ROOM
        |
        +-- realtime voice conversation
        +-- Service 014 telephony path

The Template defines what kind of person the Identity is. The Identity instance defines who that person is. Runtime processes are replaceable execution state; they are not the Identity.

# 5. Non-Negotiable Invariants

Template ≠ Identity Instance ≠ Runtime Process.

An instantiated Identity has a stable identity_id and persistent configuration.

The Identity instance’s configured self-facts are authoritative at runtime.

Each launched Identity is a LiveKit Participant in its own Default Room.

No participant movement is implemented in Service 015.

Meeting composition and cross-room meeting relationships belong to the future Meeting Manager.

Voicebox/voice tooling is reused; Service 015 does not build a new voice editor or TTS model.

LiveKit Agents is reused; Service 015 does not rebuild realtime room/media/turn-taking infrastructure.

Audio latency is a product requirement, not an afterthought.

A Template update never silently mutates an already-instantiated Identity.

# 6. Technology Decisions Already Made

Language/runtime: Python 3.12.

Service API: FastAPI with explicit openapi.yaml as the contract.

Realtime participant framework: LiveKit Agents (Python).

Voice authoring/workbench dependency: existing open-source Voicebox; do not rebuild its voice-cloning/editor functionality.

Mandatory demonstration TTS engine: Chatterbox Turbo through the selected local Voicebox/runtime integration.

Streaming STT reference implementation: Moonshine streaming STT through a thin LiveKit-compatible adapter.

LLM: one configured OpenAI-compatible endpoint supplied by the environment; do not build or train an LLM.

Persistence: SQLite is sufficient for Service 015.

Telephony: reuse Service 014. Do not implement SIP, Exotel, trunks, or PSTN logic here.

No OBS work in Service 015. OBS is already a complete external solution and video is out of scope.

# 7. Identity Template Contract

A Template is a reusable, versioned archetype. At minimum it shall contain:

template_id
version
name
description
default_self_facts:
  role
  company
  language
persona:
  objective
  behavior / style
  base instructions
conversation:
  greeting
  barge_in
  speaking_style
voice:
  default_voice_profile_id
  engine
required_instance_fields
allowed_overrides
created_at

Ship at least three useful starter Templates: Generic Assistant, Sales Representative, and Customer Support. They must be real working Templates, not labels pointing to the same prompt.

# 8. Template Versioning

Templates are versioned. Instantiation shall bind the new Identity to the exact Template version used.

sales-representative:v1
        ↓ instantiate
Ava → template_id=sales-representative, template_version=1

If v2 is later created, Ava remains bound to v1 until an explicit future upgrade operation is performed. Silent mutation is forbidden.

# 9. Instantiate Identity

The Workbench shall allow the user to choose a Template and instantiate it into a persistent Identity.

Instance-specific customization shall support at minimum:

display name

company

role/title

language

persona/instructions

greeting

voice profile

speaking style

barge-in enabled/disabled

The resolved Identity configuration shall be persisted as an instance snapshot plus its Template provenance.

# 10. Authoritative Identity Self-Knowledge — HARD REQUIREMENT

Every runtime micro-session shall receive a generated self-context derived from the persistent Identity instance. Do not depend on conversational history to remember the Identity’s name or role.

self:
  identity_id: ava-001
  name: Ava
  archetype: sales-representative
  template_version: 1
  company: Safi
  role: Account Specialist
  language: en-US

The runtime instruction shall treat these fields as authoritative self-facts and shall not invent or contradict them.

## Mandatory Self-Knowledge Test

Human: What is your name?
Ava:   My name is Ava.

Human: Who do you work for?
Ava:   Safi.

Human: What is your role?
Ava:   Account Specialist.

Instantiate James from the same archetype and repeat. James must answer with James’s facts. Any cross-Identity leakage is a FAIL.

# 11. Customization and Override Rules

Templates shall explicitly declare what may be overridden. The Workbench may not accept arbitrary fields outside the Template’s allowed overrides.

allowed_overrides:
  - name
  - company
  - role
  - persona.instructions
  - greeting
  - voice.profile_id
  - language

Template defaults plus instance overrides shall resolve deterministically into one runtime Identity configuration that can be inspected and reproduced.

# 12. Voice Workbench Integration

Use the existing open-source Voicebox project as the voice creation/selection workbench. Service 015 stores and resolves a Voice Profile reference; it does not recreate Voicebox functionality.

Required behavior:

List/select an existing Voicebox voice profile from the Identity Workbench.

Allow an authorized user to use a Voicebox-created cloned voice profile or a preset voice.

Preview the selected voice before creating or saving the Identity.

Keep the local voice service warm before a conversation begins whenever possible.

Use Chatterbox Turbo as the mandatory demonstration engine; keep the Identity contract engine-neutral.

Only voice samples the user is authorized to use may be cloned or attached to an Identity.

# 13. LiveKit Runtime Architecture

LiveKit audio from human
        ↓
Moonshine streaming STT
        ↓
Identity Self-Context + Persona + current utterance
        ↓
Configured LLM endpoint
        ↓
Voice Profile / Chatterbox Turbo
        ↓
LiveKit audio publication
        ↓
Human hears Identity

Use LiveKit Agents as the realtime participant framework. The launched Identity shall join its Default Room as an ordinary programmatic Participant and publish its speech there.

# 14. Default Room / Participant Contract

Each Identity shall expose stable meeting-facing information:

identity_id: ava-001
participant_identity: ava-001
default_room: ava-001
presence: READY | STARTING | BUSY | ERROR | OFFLINE
capabilities:
  audio_receive: true
  audio_publish: true
  barge_in: true
  video_publish: false

Service 015 owns the AI Identity runtime inside its Default Room. It does not decide meeting membership, move participants, or implement cross-room meeting topology.

# 15. Natural Conversation and Barge-In

The AI shall support realtime turn-taking and interruption. If the human begins speaking while the Identity is speaking, the Identity must stop output promptly, listen, and respond to the new utterance.

The opening greeting, voice model, STT pipeline, LLM client, and LiveKit participant should be pre-warmed before user conversation whenever practical. Initialization must not occur after every turn.

# 16. Service 015 OpenAPI Contract

Service 015 shall have its own validated openapi.yaml. Minimum operations:

POST   /templates
GET    /templates
GET    /templates/{template_id}
POST   /templates/{template_id}/instantiate
GET    /identities/{identity_id}
PATCH  /identities/{identity_id}
POST   /identities/{identity_id}/launch
POST   /identities/{identity_id}/stop
GET    /identities/{identity_id}/status
GET    /health

Equivalent paths are acceptable only if the semantics are preserved and the implementation conforms exactly to openapi.yaml.

# 17. Minimal Workbench UI

Build only the minimum UI necessary to make the product usable. No design system or fancy frontend.

IDENTITY WORKBENCH

Template: [ Sales Representative ▼ ]
Name:     [ Ava ]
Company:  [ Safi ]
Role:     [ Account Specialist ]
Language: [ English (US) ▼ ]
Voice:    [ Ava Warm ▼ ] [Preview]
Greeting: [ Hello, this is Ava from Safi ... ]
Persona:  [ ................................ ]
Barge-in: [✓]

[CREATE IDENTITY]  [LAUNCH]  [STOP]  [TEST]

The UI shall call the same OpenAPI Service used by automation. Do not hide required behavior in browser-only code.

# 18. Phone Dogfood Through Service 014

Service 015 shall not implement telephony. It shall demonstrate that a launched Identity can be heard and spoken to over a real phone by using the existing Service 014 SIP telephony primitive and the Identity’s Default Room.

AI Identity (Service 015)
       ↕
Default LiveKit Room
       ↕
SIP Endpoint / Service 014
       ↕
Exotel India
       ↕
real phone

The developer must not bypass Service 014 with a new Twilio/Plivo/Exotel SDK path.

# 19. Future Meeting Manager Compatibility — Implement Nothing More

Service 015 must be Meeting-Manager-ready but shall not implement Meeting Manager behavior.

The future Meeting Manager needs only stable Identity/Participant/Default-Room/capability/presence metadata. It must not need to know the TTS engine, STT model, LLM provider, or prompt internals.

Presence in a Default Room does not itself imply future permission to hear or speak in a Meeting. Future media authorization belongs above Service 015.

# 20. Latency Instrumentation

Instrument every test conversation with monotonic timestamps for:

T0 = end of detected human turn
T1 = usable/final STT available
T2 = first LLM output available
T3 = first TTS PCM available
T4 = first audio frame published to LiveKit
T5 = first remote audible output when measurable

For at least ten turns, report P50, P95, and MAX for T0→T4 and the component intervals. Target P50 T0→T4 ≤ 700 ms. PASS ceiling: P95 T0→T4 ≤ 1,500 ms on the assigned reference machine/network. If the environment cannot meet the ceiling, the developer must identify the measured bottleneck and prove no avoidable cold start exists; do not hide the number.

# 21. Persistence and Restart

Templates, Template versions, Identity instances, instance overrides, self-facts, voice-profile references, and Default Room/Participant IDs shall survive Service restart.

Restarting the runtime must not create a new logical Identity. Relaunch Ava and Ava must remain Ava.

# 22. Security / Voice Authorization

Do not clone or attach a person-specific voice without explicit authorization from the user/operator supplying it.

Store authorization/provenance metadata for cloned voice references.

Do not expose raw voice samples through User-role Help or logs.

Secrets for LiveKit, Voicebox, LLM endpoint, or Service 014 must be supplied through the existing secret mechanism/environment, never committed to Git.

Do not let prompt text override authoritative instance self-facts.

# 23. Service Expert

Instantiate a new scoped Service Expert from Service 013 using the OpenAPI Service Template.

Supply at minimum:

this SOW

Service 015 openapi.yaml

Git repo, branch, and exact SHA

Template schema and starter Templates

Identity instance schema

LiveKit runtime integration

Voicebox/Chatterbox integration notes

Moonshine adapter notes

acceptance tests and latency results

RBAC context permissions

Verified development outcomes shall be ingested with provenance. The Service Expert must support the mandatory fresh-learning test below.

# 24. Mandatory Fresh-Learning Test

During implementation, identify one verified fact that was not present in the original SOW/Template — for example, an actual warm-up requirement, buffer size, codec/sample-rate constraint, or Voicebox/LiveKit integration trap.

Ingest it as VERIFIED EXPERIENCE with repo/branch/SHA/source. Start a fresh Service Expert Chat session and ask a question requiring that fact.

PASS: the Service Expert retrieves and correctly uses the new verified knowledge. FAIL: the fact exists only in a developer’s memory, README edit, or old chat.

# 25. AI-First HOW TO

1. Install/run the existing dependencies first: LiveKit environment, Voicebox, Chatterbox Turbo, Moonshine, configured LLM endpoint, and Service 014 test endpoint. Do not begin by writing replacements.

2. Define and validate openapi.yaml before implementation.

3. Implement SQLite schemas for Template, TemplateVersion, Identity, and RuntimeStatus.

4. Seed three working Templates: Generic Assistant, Sales Representative, Customer Support.

5. Implement deterministic Template + overrides → resolved Identity configuration.

6. Implement authoritative runtime self-context generation.

7. Implement minimal Voicebox voice-profile listing/selection/preview.

8. Implement the LiveKit Agent runtime: Moonshine STT → LLM → Chatterbox/Voicebox TTS → LiveKit audio.

9. Implement launch/stop/status against the persistent Identity and Default Room.

10. Implement the minimal Workbench UI on top of the OpenAPI API.

11. Run self-knowledge, isolation, restart, barge-in, latency, and real-phone dogfood tests.

12. Feed one newly verified fact to the Service Expert and prove fresh retrieval.

13. Commit source, openapi.yaml, schemas, starter Templates, tests, README, and evidence to Git.

# 26. Objective PASS/FAIL Acceptance Tests

## TEST 1 — Template Workbench

PASS: User can see and select at least three distinct working archetypes.

FAIL: Only hard-coded Ava or one generic prompt exists.

## TEST 2 — Template Instantiation

PASS: Sales Representative can instantiate Ava without modifying the Template.

FAIL: Creating Ava mutates the Template or requires code edits.

## TEST 3 — Customization

PASS: Ava’s name, company, role, greeting, persona, language, and voice can be customized within allowed overrides.

FAIL: Values are ignored, hard-coded, or unrestricted arbitrary fields are accepted.

## TEST 4 — Authoritative Name

PASS: When asked “What is your name?”, Ava answers Ava in a fresh runtime session.

FAIL: Template name, wrong name, invented name, or chat-history dependency.

## TEST 5 — Authoritative Self-Facts

PASS: Ava correctly reports configured company and role.

FAIL: LLM contradicts the persistent Identity configuration.

## TEST 6 — Same Template, Different Identity

PASS: Instantiate James from the same Template; James reports James and his own custom facts.

FAIL: Ava facts leak into James or vice versa.

## TEST 7 — Voice Profile

PASS: Selected Voicebox profile is previewed and used by the launched Identity.

FAIL: Runtime silently uses a generic/default voice.

## TEST 8 — Default Room

PASS: Launched Identity appears as the configured participant in its persistent Default LiveKit Room.

FAIL: A new arbitrary room/identity is created every launch.

## TEST 9 — Natural Conversation

PASS: Human and Identity complete at least ten coherent voice turns.

FAIL: Demo is scripted playback or one-shot TTS.

## TEST 10 — Barge-In

PASS: Human interrupts while AI is speaking; AI stops promptly, listens, and responds to the interruption.

FAIL: AI continues talking over the human or waits for the old response to finish.

## TEST 11 — Latency Evidence

PASS: P50/P95/MAX and component timing are captured for ten or more turns; P95 T0→T4 is ≤1,500 ms on the reference setup or a measured unavoidable environment bottleneck is documented with all avoidable cold starts removed.

FAIL: No timings, only subjective “fast” claims, or avoidable per-turn initialization remains.

## TEST 12 — Phone Dogfood

PASS: Using Service 014, a real phone converses bidirectionally with the launched Identity through its Default Room.

FAIL: Developer bypasses Service 014 or only tests a browser microphone.

## TEST 13 — Restart Persistence

PASS: Restart Service 015 and relaunch Ava; same identity_id/template version/name/persona/voice/default_room return.

FAIL: Identity is recreated manually or loses configuration.

## TEST 14 — OpenAPI Contract

PASS: Every required API operation validates and conforms to openapi.yaml.

FAIL: Contract is decorative or implementation diverges.

## TEST 15 — Fresh Expert Learning

PASS: Service Expert retrieves one newly verified fact in a fresh session.

FAIL: Knowledge disappears or requires prompt/code modification.

# 27. Failure and Recovery Tests

## Voice service unavailable

Launch/status reports an explicit dependency failure; Identity record is not corrupted. Restore Voicebox/TTS and relaunch without recreating the Identity.

## LiveKit disconnect

Runtime reports ERROR/RECONNECTING and can rejoin the same Default Room with the same participant identity.

## LLM endpoint unavailable

Do not fabricate responses. Surface runtime error/retry state; preserve Identity.

## STT failure

Conversation reports STT failure without replacing self-facts or Identity configuration.

## Runtime process crash

Restart runtime and relaunch the same Identity from persisted state.

## Invalid override

Reject an override not allowed by the Template with a clear OpenAPI error.

# 28. Mandatory Dogfood Demonstration

1. Open the Identity Workbench.

2. Select Sales Representative Template.

3. Instantiate a new Identity named Ava.

4. Customize Ava’s company, role, persona, greeting, language, and Voicebox voice.

5. Preview the selected voice.

6. Save and launch Ava.

7. From a human LiveKit client, hold a ten-turn conversation with Ava.

8. Ask Ava her name, company, and role; verify exact configured self-facts.

9. Interrupt Ava mid-sentence and verify barge-in.

10. Capture latency evidence.

11. Using existing Service 014, call or reach Ava through a real India phone/Exotel path and converse bidirectionally.

12. Stop and restart Service 015; relaunch Ava and verify persistence.

13. Instantiate James from the same Sales Representative Template with different values and voice; verify isolation.

# 29. Deliverables

Service 015 source code

Service 015 openapi.yaml

minimal Identity Template Workbench UI

SQLite schema/migrations

at least three starter Template archetypes

Template versioning and instantiation implementation

persistent Identity instance model and authoritative self-context builder

Voicebox voice-profile integration and Chatterbox Turbo demonstration

Moonshine streaming STT adapter

LiveKit Agent runtime and Default Room launch/stop/status

automated acceptance/failure tests where practical

real dogfood evidence and latency report

Service 015 scoped Service Expert manifest and fresh-learning evidence

README with exact install/run/test commands

all work committed to Git at an exact SHA

# 30. Aggressive Out of Scope

Video avatars

OBS development or OBS integration

Meeting Manager

participant movement between rooms

cross-room Meeting Fabric

new SIP/PSTN implementation

new Exotel integration — use Service 014

predictive dialer

campaign management

call-center queueing/routing

CRM

custom TTS training/model development

custom STT training/model development

a replacement for Voicebox

foundation-model fine-tuning

multi-agent orchestration

fancy UI/design system

billing/marketplace

automatic Template upgrades of existing Identities

# 31. Definition of Done

1. Service 015 runs and conforms to its openapi.yaml.

2. Workbench exposes at least three working Identity Templates.

3. Templates are versioned.

4. A Template can be instantiated without being mutated.

5. Identity instances persist their Template provenance and overrides.

6. Identity self-facts are injected authoritatively on every fresh runtime session.

7. Ava answers her configured name/company/role correctly.

8. A second Identity from the same Template answers its own facts with no leakage.

9. Voicebox voice profile selection/preview works and the selected voice is used.

10. Chatterbox Turbo is demonstrated as the reference voice engine.

11. Moonshine streaming STT is integrated for the reference implementation.

12. Launched Identity is a LiveKit Participant in its persistent Default Room.

13. Natural ten-turn conversation works.

14. Barge-in works.

15. Latency evidence is captured and the stated acceptance ceiling is met or the environmental blocker is precisely measured with avoidable cold starts removed.

16. Real phone dogfood works through Service 014 rather than a new telephony path.

17. Restart preserves and relaunches the same Identity.

18. Scoped Service Expert is instantiated and learns one new verified fact.

19. All acceptance tests pass and source/tests/docs are committed at an exact Git SHA.

Anything less is not Service 015.

# 32. Reference Implementations — Reuse, Do Not Rebuild

LiveKit Agents: https://docs.livekit.io/agents/

Voicebox: https://github.com/jamiepine/voicebox

Chatterbox / Chatterbox Turbo: https://github.com/resemble-ai/chatterbox

Moonshine Voice: https://github.com/moonshine-ai/moonshine

Use current supported interfaces from these projects. Do not copy unstable internal APIs when a public API/SDK exists.

# 33. Six Hours or Nothing

Complete Service 015 within six working hours.

Do not reconsider the fundamental architecture:

Template / Archetype
      ↓ instantiate
Persistent Identity
      ↓ authoritative self-knowledge
LiveKit Agent Participant
      ↓
Default Room
      ↓
Service 014 for phone access

The objective is simple: a user can manufacture a specific AI person from a reusable archetype, customize who that person is and how that person sounds, launch it, talk naturally to it, ask it who it is and get the correct answer, and reach it over a real phone through the telephony Service already built.

That is Service 015.
