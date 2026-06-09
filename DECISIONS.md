# Architecture Decision Record: Multi-Channel Patient Event Router

**Version:** 1.0  
**Author:** Jason L (Staff PM) + Claude (AI Engineering Collaborator)  
**Date:** 2026-06-08

This document records the key technical and product decisions made before and during the build, the reasoning behind each, and the tradeoffs we accepted. Every choice here was deliberate — not accidental.

---

## Decision 1: Python + Flask as the API Framework

**Context:** We needed a backend language and framework to build a REST API with 4 endpoints in a constrained time window (~2.5 hrs), owned by a PM with no independent coding ability.

**Decision:** Python + Flask.

**Why:**
- Python is the most readable language for a non-engineer to audit — the PM can follow the logic without writing it
- Flask is minimal by design: no boilerplate, no ORM, no configuration framework — a working API in under 50 lines
- The standard library (uuid, json, datetime) covers all prototype needs beyond Twilio
- Faster to explain and debug than TypeScript/Node, which would have been the next choice

**Alternatives considered:**
- **FastAPI:** Better for production (async, auto-docs, type validation) but adds complexity that doesn't pay off in a 2.5-hour build
- **Node.js + Express:** Comparable simplicity, but JavaScript's async patterns are harder to read for a non-technical reviewer
- **Django:** Too much convention overhead for a 4-endpoint prototype

**Tradeoff accepted:** Flask has no built-in request validation. We rely on callers sending well-formed payloads. In production, we'd add Pydantic (via FastAPI) or marshmallow for schema enforcement at the boundary.

---

## Decision 2: Replit as the Deployment Target

**Context:** The prototype needs to be runnable and shareable in an interview context — accessible via URL without local setup on the interviewer's machine.

**Decision:** Replit (cloud-based Python environment).

**Why:**
- Zero local environment setup — runs in a browser, shareable via a single URL
- No dependency installation friction for an interviewer to demo it themselves
- Keeps the demo self-contained: one link, live API, no "works on my machine" risk
- Free tier is sufficient for a demo-scale Flask app

**Alternatives considered:**
- **Localhost:** Simpler to develop but not shareable without a tunnel (ngrok adds another moving part)
- **Railway / Render / Fly.io:** More production-like, but requires account setup, CLI tooling, and a deploy pipeline — unnecessary overhead
- **Heroku:** Natural choice historically; no longer has a free tier

**Tradeoff accepted:** Replit has cold-start latency and the in-memory opt-out store resets on container restart. Both are acceptable for a demo; both would be resolved by a persistent deployment and a database in production.

---

## Decision 3: Twilio for Real SMS; Email and Chat Mocked

**Context:** We needed to demonstrate real channel dispatch to be credible, while keeping build time under control across three channels.

**Decision:** SMS via Twilio SDK (real send). Email and chat logged to console only (mocked).

**Why SMS is real:**
- A live text message received during the demo is impossible to fake and immediately credible to any audience
- Twilio's Python SDK is 5 lines to send a message — the lowest-friction real integration available
- OpenLoop already uses Twilio in production — this is the exact integration that would exist in the real system

**Why email and chat are mocked:**
- Email (SendGrid/SES) requires DNS verification and domain setup — too much prerequisite friction for a prototype
- Chat requires a conversation platform (Twilio Conversations, Sendbird) with session management — a different architectural layer
- Honest mocking with clear log output demonstrates routing logic without integration overhead
- Both are labeled "MOCK" in the code — no illusion of completeness

**What Amazon Connect would replace in production:**
- Amazon Connect handles inbound/outbound voice and some SMS at OpenLoop today
- In a production router, Amazon Connect would be a fourth channel option: `voice`
- The router would call it the same way it calls Twilio — the abstraction layer is the point
- Adding a new channel in production means a new dispatch function and a new key in the rules config, not a router rewrite

**Tradeoff accepted:** A live SMS send requires Twilio credentials in environment variables. The demo depends on these being configured. If credentials are absent, SMS falls back to mock mode automatically.

---

## Decision 4: JSON File for Routing Rules (No Database)

**Context:** Routing rules need to be readable, editable, and inspectable. Options ranged from hardcoded Python dicts to a full relational database.

**Decision:** `journey_rules.json` — a flat JSON file loaded at server startup.

**Why:**
- Human-readable and self-documenting — an Implementation Manager can read and understand it without tooling
- Editing one file to change a routing rule directly demonstrates the "zero-code rule change" success metric from the PRD
- The `GET /rules` endpoint returns this file verbatim, making the config-to-behavior connection transparent in the demo
- No database means no schema, no migration, no connection string — the prototype runs anywhere

**Alternatives considered:**
- **Hardcoded Python dict:** Even simpler, but breaks the "editable without touching code" requirement — a rule change would require editing app.py
- **SQLite:** Persistent and queryable, adds schema design and setup friction with no demo benefit over JSON
- **Environment variables:** Good for secrets; poor for structured, hierarchical, multi-rule configuration

**Tradeoff accepted:** Rules load at startup — a rule change requires a server restart. In production, a `POST /rules` endpoint would apply changes live without restart. Documented as a v2 feature in the PRD.

---

## Decision 5: In-Memory Opt-Out Store (No Persistent Storage)

**Context:** The `/opt-out` endpoint needs to enforce consent overrides across subsequent `/trigger` calls within a session.

**Decision:** Python dict held in server memory (`opt_out_store = {}`).

**Why:**
- Sufficient for demo: opt-outs survive the session and can be shown blocking a subsequent send in real time
- Zero setup: no database, no Redis, no file I/O
- The behavior is correct and observable — the simplification is in the implementation, not the logic

**Alternatives considered:**
- **SQLite:** Persistent across restarts — more realistic, ~45 min additional build, no meaningful demo benefit
- **Flat JSON file:** Persistent and simple, but introduces file I/O race conditions under concurrent requests
- **Redis:** The production-right answer for a fast, shared, persistent opt-out store — entirely out of scope

**Tradeoff explicitly disclosed in PRD:** In production, opt-out state requires a persistent store with a full audit trail — timestamp, channel, signal source, and an immutable record that it was honored. The migration path is clear; the decision to defer it is intentional.

---

## Decision 6: Consent in the Event Payload (Not a Database Lookup)

**Context:** Before dispatching, the router needs to know whether a patient has consented to a given channel. Two approaches: trust the caller (payload) or own the truth (database lookup).

**Decision:** Consent travels in the event payload. The router trusts the caller's consent values, then applies its own in-memory opt-out override on top.

**Why:**
- Eliminates a database dependency from the prototype entirely
- The caller (EMR, scheduling system) already holds patient consent in their records — passing it in the payload is a valid production pattern when the router is downstream of a consent management system
- The router's in-memory opt-out store handles the override case (STOP signals) without a lookup

**Tradeoff accepted:** The router cannot independently verify consent — a misconfigured caller could send `"sms": true` for a non-consented patient. In production, this is mitigated by either: (a) the router owning a consent database it queries before dispatch, or (b) an upstream consent service the caller must check before constructing the payload. This remains the open question deferred in the PRD.

---

## Decision 7: Tenant-Scoped Patient Identity (Composite Key)

**Context:** OpenLoop operates in a multi-tenant environment where the same physical patient may exist in multiple partner EMR systems, each with their own patient identifier.

**Decision:** `patient_id` is scoped to the tenant. The canonical patient identity for routing and opt-out purposes is the composite key: `(client_id, patient_id)`.

**Why:**
- Mirrors reality: every partner EMR assigns its own patient IDs; there is no guaranteed globally unique identifier across systems without a Master Patient Index (MPI)
- Requires no identity resolution infrastructure — the router trusts the composite key the caller provides
- Keeps the prototype dependency-free: no MPI service to build or mock

**Implication for opt-out:** A patient who opts out of SMS via Partner A's integration is still reachable via Partner B's integration, because the opt-out is stored against `(client_id: partner-a, patient_id: pt-001)` — a different record from `(client_id: partner-b, patient_id: pt-001)`. This is a known limitation.

**Alternatives considered:**
- **Global patient ID:** Requires OpenLoop to operate an MPI — significant infrastructure; only viable if OpenLoop is the authoritative source of patient identity across all partners
- **Global ID + tenant-scoped external reference:** Best of both worlds architecturally, but still requires an MPI and a resolution lookup before every dispatch

**Production path:** Cross-client opt-out enforcement is a v2 concern tied to OpenLoop's broader patient identity strategy. The router's composite-key model is forward-compatible — when an MPI exists, the router can resolve `(client_id, external_patient_id)` → `global_patient_id` before applying rules, with no change to the routing logic itself.

---

## Decision 8: Chat as Notification Channel Only (Not Conversational Workflow)

**Context:** The PM identified that patients prefer chat for healthcare interactions, including scheduling. The question was whether to build a full chat-based scheduling workflow.

**Decision:** Chat is a dispatch channel only — the router sends a confirmation message to a chat thread. The interactive scheduling conversation is explicitly out of scope.

**Why:**
- The router's job is event-driven outbound dispatch: receive event → apply rules → send message. One direction, one moment in time.
- A conversational scheduling workflow requires bidirectional session state, a real-time connection, a patient-facing interface, and provider tooling — a separate product, not a channel addition
- Adding chat-as-channel costs ~30–45 minutes; adding the full workflow costs 6–8 hours and a frontend
- The appointment confirmation step of any scheduling workflow IS handled by the router: `appointment_booked` → dispatch to SMS + email + chat

**Tradeoff accepted:** The demo does not show a patient chatting with a provider. It shows the router dispatching a confirmation to the chat channel after an upstream system fires an `appointment_booked` event. This boundary is stated clearly in the interview narrative.

---

## One Thing We'd Do Differently With More Time

**Build a persistent consent and opt-out store with a full audit trail.**

The single largest gap between this prototype and a production-ready system is consent persistence. Opt-outs currently reset on server restart. In a HIPAA-regulated environment that is not acceptable — every opt-out requires a timestamp, a channel, a signal source, and an immutable record that it was honored.

With more time:
1. Replace the in-memory dict with SQLite (next prototype iteration) or PostgreSQL (production)
2. Add a `consent_events` table: `client_id`, `patient_id`, `channel`, `action` (opt_in/opt_out), `source`, `timestamp`
3. Make the router query this table before every dispatch, removing the dependency on the caller's payload consent values
4. Expose a `GET /consent/{client_id}/{patient_id}` endpoint so Implementation Managers can audit a patient's full consent history in one call

This would also resolve the open question in the PRD: the router becomes the authoritative consent record, and the payload `consent` field becomes an optional hint rather than the source of truth.
