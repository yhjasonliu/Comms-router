# Problem Statement: Multi-Channel Patient Event Router

**Version:** 1.0  
**Author:** Jason L (Staff PM)  
**Date:** 2026-06-08

---

## Context

OpenLoop operates at 250K+ visits/month across a distributed client network, connecting patients to care through multiple communication systems — Amazon Connect, Twilio, and Tellescope. As the platform has scaled, a structural gap has emerged: no single system owns the question *"how should this patient be reached, through which channel, and did it happen?"*

The result is operational overhead that scales with the business, compliance exposure that grows with every new client integration, and an Implementation team that spends more time filing engineering tickets than configuring client outcomes.

---

## Users

### Internal — Implementation Manager
The Implementation Manager translates client communication preferences into platform behavior. They configure rules, train clients on the platform, and serve as the operational point of contact for enterprise clients when something goes wrong. Their job should be one of configuration and client success — instead, it is frequently one of manual log reconciliation, cross-team escalation, and waiting on engineering to make rule changes that should be self-service.

### External — Patient
The patient receives time-sensitive health communications: appointment reminders, refill nudges, care gap alerts. They don't see the infrastructure — they experience only whether the right message arrived at the right time on the right channel. A missed message isn't a UX inconvenience; for a patient managing a chronic condition, it can mean a missed dose or a no-show appointment.

---

## The Problem

OpenLoop's current communication architecture has four structural weaknesses:

### 1. No Unified Audit Trail
Answering *"did this patient receive their appointment reminder?"* requires cross-referencing Twilio logs, Amazon Connect logs, and Tellescope separately — three interfaces, three data models, no shared event ID. This is an ops nightmare and a HIPAA compliance risk: there is no single authoritative record of what was sent, to whom, through which channel, and when.

### 2. No Shared Routing Logic
Each client integration calls communication channels directly with its own hardcoded rules. When a rule changes — *"for all GLP-1 patients, prefer SMS over email for refill reminders"* — engineers must find and update it in multiple places. There is no single configuration layer governing channel selection across the platform. Rule changes that should take minutes take days.

### 3. No Client Self-Service
Every template change, timing adjustment, or channel preference update requires an engineering ticket. At OpenLoop's scale, this is a constant bottleneck for the Implementation team and a compounding source of friction in client relationships. Implementation Managers are blocked from doing the configuration work that is fundamentally their job.

### 4. Fragmented Consent State
Patient opt-in and opt-out preferences are stored per-system, not per-patient. When a patient texts STOP to a Twilio number, that signal lives in Twilio — Amazon Connect and Tellescope have no visibility into it. There is no single source of truth for *"is this patient consented to SMS?"* — which creates TCPA exposure on every cross-channel send, and makes honoring opt-outs across the platform operationally impossible without manual coordination.

---

## Why a Unified Event Router

A unified event router inverts this model. Instead of each integration deciding how to reach a patient, a single routing layer accepts a patient event, applies configurable rules, dispatches to the right channel, and logs the result — one system, one audit trail.

This matters because:
- **Routing logic becomes a configuration problem, not a code change** — rules live in one place and are editable without a deployment
- **Compliance audits become a single API query** — no multi-system reconciliation required
- **Implementation Managers gain self-service control** over the rules that govern their clients
- **Consent becomes a pre-dispatch gate, not an afterthought** — every send checks opt-in status; opt-outs propagate instantly and are honored platform-wide

---

## Success Metrics (Prototype)

1. **Audit lookup in one call** — A complete event trace (patient → channel → delivery status) is retrievable via a single `/log` endpoint; no cross-system reconciliation required
2. **Zero-code rule changes** — Switching a client from email to SMS for a given event type requires editing one config file; no code deployment
3. **Correct routing across 3 event types** — Appointment booked → SMS; refill due → email; unknown event → defined fallback behavior
4. **No communication fires without verified consent** — The router checks opt-in status per patient per channel before every dispatch; a message to a non-consented patient is never sent, is logged as blocked, and returns a clear status. Opt-outs (STOP signals) received via the `/opt-out` endpoint are honored instantly, propagate across all channels, and require no engineering involvement to take effect

---

## Non-Goals

1. **Not a delivery infrastructure replacement** — This is the routing decision layer, not a replacement for Twilio, Amazon Connect, or Tellescope. In production it would sit in front of those systems as an orchestration layer, not replace them.
2. **No patient-facing UI** — There is no patient preferences center, no client dashboard, and no portal in scope. This is an API-first internal tool. Self-service configuration for Implementation Managers and a patient-facing consent UI are v2+ concerns.
