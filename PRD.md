# Product Requirements: Multi-Channel Patient Event Router

**Version:** 1.0  
**Status:** Draft  
**Author:** Jason L (Staff PM)  
**Date:** 2026-06-08

---

## User Stories

**US-1 — Implementation Manager: Rule Configuration**
As an Implementation Manager, I want to define routing rules per client, program, and urgency in a single config file, so that I can change communication channel preferences without filing an engineering ticket.

**US-2 — Implementation Manager: Audit Visibility**
As an Implementation Manager, I want a single log endpoint showing all events, channels used, and delivery status, so that I can answer "did this patient receive their message?" without reconciling logs across multiple systems.

**US-3 — Patient: Multi-Channel Consent and Confirmation**
As a patient, I want appointment confirmations and care reminders delivered to all channels I've opted into — SMS, email, and chat — so that I receive timely communications on the platforms I actually use, while having my opt-out decisions honored instantly and permanently across all three channels.

**US-4 — Developer: Clean Integration Contract**
As a developer integrating an upstream system (EMR, scheduling platform), I want a single event API that handles all routing and channel dispatch internally, so that I can connect any system without embedding channel-specific logic in integration code.

---

## API Contract

### `POST /trigger` — Route and dispatch a patient event

**Request payload:**
```json
{
  "patient_id": "pt-001",
  "client_id": "openloop-partner-a",
  "program": "glp1",
  "event_type": "appointment_booked",
  "urgency": "normal",
  "consent": {
    "sms": true,
    "email": true,
    "chat": true
  },
  "contact": {
    "phone_number": "+15551234567",
    "email": "patient@example.com",
    "chat_user_id": "chat-user-abc"
  },
  "metadata": {
    "appointment_time": "2026-06-10T14:00:00Z",
    "provider": "Dr. Smith",
    "location_type": "virtual",
    "join_link": "https://visit.openloop.com/abc123"
  }
}
```

**Top-level fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `patient_id` | string | Yes | Unique patient identifier |
| `client_id` | string | Yes | Client/partner identifier — enables client-specific routing rules |
| `program` | string | Yes | Clinical program (e.g., `glp1`, `mental_health`) — enables program-specific rules |
| `event_type` | string | Yes | The event being triggered — drives rule matching |
| `urgency` | string | Yes | `normal` or `urgent` — urgent events route to SMS first regardless of standard rules |
| `consent` | object | Yes | Per-channel opt-in status at time of event. Router-side opt-out list takes precedence |
| `contact` | object | Yes | Per-channel recipient identifiers. Include only fields relevant to consented channels |
| `metadata` | object | Yes | Event-specific fields (see schemas below) |

**`urgency` routing behavior:**
- `normal` — follows standard rule matching (client → program → event_type priority)
- `urgent` — SMS is always attempted first; if SMS is blocked (no consent or opted out), falls back to standard rule matching. Does not override consent.

---

**Metadata schemas by event type:**

| Event Type | Required metadata fields | Optional metadata fields |
|---|---|---|
| `appointment_booked` | `appointment_time`, `provider` | `location_type` (virtual/in-person), `join_link`, `provider_specialty` |
| `appointment_reminder` | `appointment_time`, `provider` | `location_type`, `join_link`, `hours_until` |
| `refill_due` | `medication_name`, `days_remaining` | `pharmacy_name`, `prescriber` |
| `prescription_ready` | `medication_name`, `pharmacy_name` | `pickup_window` |
| `unknown_event` | _(none required)_ | Any fields — logged as-is, routed to fallback channel |

> **Prototype note:** Metadata is not strictly validated in v1. The schemas above define expected fields for message templating. Unknown or missing fields are logged but do not block dispatch.

---

**Response — dispatched:**
```json
{
  "event_id": "evt-abc123",
  "patient_id": "pt-001",
  "channels_dispatched": [
    { "channel": "sms", "status": "delivered" },
    { "channel": "email", "status": "delivered" },
    { "channel": "chat", "status": "delivered" }
  ],
  "rule_matched": "openloop-partner-a:glp1:appointment_booked",
  "urgency": "normal",
  "timestamp": "2026-06-08T10:00:00Z"
}
```

**Response — partial block:**
```json
{
  "event_id": "evt-abc124",
  "patient_id": "pt-001",
  "channels_dispatched": [
    { "channel": "sms", "status": "delivered" },
    { "channel": "email", "status": "blocked_no_consent" },
    { "channel": "chat", "status": "blocked_opt_out" }
  ],
  "rule_matched": "openloop-partner-a:glp1:appointment_booked",
  "urgency": "normal",
  "timestamp": "2026-06-08T10:01:00Z"
}
```

**Possible per-channel status values:**
`delivered` | `blocked_no_consent` | `blocked_opt_out` | `fallback_used` | `no_rule_matched`

---

### `POST /opt-out` — Record a patient opt-out

**Request:**
```json
{
  "patient_id": "pt-001",
  "channel": "sms"
}
```

**Response:**
```json
{
  "patient_id": "pt-001",
  "channel": "sms",
  "status": "opted_out",
  "timestamp": "2026-06-08T10:02:00Z"
}
```

Writes to the router's in-memory opt-out override list. All subsequent `/trigger` calls for this patient on this channel are blocked regardless of payload consent values. Valid channel values: `sms` | `email` | `chat`.

> **Prototype constraint:** Opt-out state is held in memory and resets on server restart. In production, this would persist to a database with a full audit trail. See open question below.

---

### `GET /log` — Single pane of glass event history

**Query parameters (all optional, combinable):**

| Param | Description |
|---|---|
| `patient_id` | Filter by patient |
| `client_id` | Filter by client |
| `event_type` | Filter by event type |
| `status` | Filter by outcome (e.g., `blocked_opt_out`) |

**Response:**
```json
[
  {
    "event_id": "evt-abc123",
    "patient_id": "pt-001",
    "client_id": "openloop-partner-a",
    "program": "glp1",
    "event_type": "appointment_booked",
    "urgency": "normal",
    "channels_dispatched": [
      { "channel": "sms", "status": "delivered" },
      { "channel": "email", "status": "delivered" },
      { "channel": "chat", "status": "blocked_opt_out" }
    ],
    "rule_matched": "openloop-partner-a:glp1:appointment_booked",
    "timestamp": "2026-06-08T10:00:00Z"
  }
]
```

This endpoint is the compliance audit trail. Every trigger attempt — delivered or blocked — is recorded here with full context.

---

### `GET /rules` — Inspect current routing configuration

Returns the full contents of `journey_rules.json` currently in effect. Read-only in this prototype.

> **v2 consideration:** A `POST /rules` endpoint would allow Implementation Managers to update routing config via API without file access — removing the restart requirement and enabling true self-service.

---

## Routing Rules Definition

Rules are stored in `journey_rules.json` and evaluated in priority order. The most specific matching rule wins.

### Match Priority

| Priority | Match Criteria | Example |
|---|---|---|
| 1 (highest) | `client_id` + `program` + `event_type` | Partner A, GLP-1, appointment booked → SMS + chat |
| 2 | `client_id` + `event_type` | Partner A, any program, refill due → email |
| 3 | `event_type` only (global default) | Any client, appointment booked → SMS |
| 4 (last resort) | No rule matched → global fallback | → email |

**Urgency override:** When `urgency` is `urgent`, SMS is prepended to the channel list regardless of rule priority. Consent gate still applies.

### Pre-Dispatch Consent Gate

Runs per channel before every dispatch:

1. Check router's in-memory opt-out override list — if opted out, block and log `blocked_opt_out`
2. Check payload `consent` for this channel — if `false`, block and log `blocked_no_consent`
3. If both pass, dispatch

Each channel in a rule is checked independently — a block on one does not prevent dispatch on others.

### Supported Event Types

| Event Type | Default channel(s) | Notes |
|---|---|---|
| `appointment_booked` | SMS + email + chat | Multi-channel confirmation |
| `appointment_reminder` | SMS + chat | Reminder 24hr prior — chat and SMS preferred for time-sensitivity |
| `refill_due` | Email | Lower urgency; email sufficient |
| `prescription_ready` | SMS + chat | Time-sensitive pickup notification |
| `unknown_event` | Email (fallback) | Catch-all; logs event type as unrecognized |

### Channel Capabilities

| Channel | Implementation | Production equivalent |
|---|---|---|
| `sms` | Real — Twilio SMS API | Twilio (current OpenLoop stack) |
| `email` | Mocked — logged, not sent | SendGrid / SES |
| `chat` | Mocked — logged, not sent | Twilio Conversations / in-app messaging SDK |

---

## Open Question — Intentionally Deferred

**Who owns consent state in production?**

This prototype places consent in the event payload with a server-side in-memory opt-out override. In production, this raises a design question: does the router own the canonical consent record, or does it delegate to an existing system of record — Tellescope, an EHR, or a dedicated consent service?

If the router owns consent, it becomes a system of record requiring a persistent database, versioned opt-out history, and a full audit trail. If it delegates, it needs a reliable upstream source to query on every dispatch. This decision should be driven by OpenLoop's existing data ownership model and compliance posture, and is intentionally out of scope for v1.
