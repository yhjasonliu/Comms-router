# Testing: Multi-Channel Patient Event Router

**Date:** 2026-06-09  
**Tested by:** Jason L (PM) + Claude (AI Engineer)  
**Server:** Flask dev server, `http://localhost:5000`  
**SMS mode:** Mock (Twilio credentials configured; carrier delivery blocked — see "What Broke" below)

---

## Scenario 1: Appointment Booked → Multi-Channel Dispatch

**Setup:** Patient `pt-001`, `openloop-partner-a`, program `glp1`, all three channels consented.

**Expected:** Rule `openloop-partner-a:glp1:appointment_booked` matches → SMS + email + chat all dispatched.

**Result:** Pass

```json
{
  "channels_dispatched": [
    { "channel": "sms",   "status": "delivered" },
    { "channel": "email", "status": "delivered" },
    { "channel": "chat",  "status": "delivered" }
  ],
  "rule_matched": "openloop-partner-a:glp1:appointment_booked",
  "event_type": "appointment_booked",
  "urgency": "normal"
}
```

**What this proves:** The most-specific rule (client + program + event type) matched correctly. Multi-channel dispatch fired in one API call. No channel-specific logic in the caller.

---

## Scenario 2: Patient Opts Out of SMS → Subsequent Send Blocked

**Setup:** Same patient. Step 1 — opt `pt-001` out of SMS via `/opt-out`. Step 2 — trigger `prescription_ready` (which routes to SMS + chat per global default).

**Expected:** SMS shows `blocked_opt_out`; chat still delivers. Opt-out overrides the `"sms": true` consent value in the payload.

**Result:** Pass

Opt-out response:
```json
{
  "patient_id": "pt-001",
  "client_id": "openloop-partner-a",
  "channel": "sms",
  "status": "opted_out",
  "timestamp": "2026-06-09T04:18:16.081463+00:00"
}
```

Subsequent trigger response:
```json
{
  "channels_dispatched": [
    { "channel": "sms",  "status": "blocked_opt_out" },
    { "channel": "chat", "status": "delivered" }
  ],
  "rule_matched": "prescription_ready",
  "event_type": "prescription_ready"
}
```

**What this proves:** The router-side opt-out override works — even when the caller sends `"sms": true`, the server-side record takes precedence. Blocking one channel does not affect others.

**Also note:** `rule_matched: "prescription_ready"` — no client+program specific rule exists for this event, so the router correctly fell through to the global default. Rule hierarchy working as designed.

---

## Scenario 3: Unknown Event Type → Fallback Routing

**Setup:** Patient `pt-003`, event type `care_gap_identified` — no rule exists for this event in `journey_rules.json`.

**Expected:** No rule matches → routes to `fallback_channel` (email) → `rule_matched` returns `global_fallback`.

**Result:** Pass

```json
{
  "channels_dispatched": [
    { "channel": "email", "status": "delivered" }
  ],
  "rule_matched": "global_fallback",
  "event_type": "care_gap_identified"
}
```

**What this proves:** The router handles unrecognized events gracefully — no crash, no silent failure. It routes to a safe fallback channel and surfaces the unmatched event type in the log, making it discoverable for future rule creation.

---

## Single Pane of Glass: Audit Log Query

After all scenarios, a single `GET /log?patient_id=pt-001` call returned the complete event history for `pt-001` — three events, every channel, every status, timestamps and matched rules included:

```json
[
  {
    "event_type": "appointment_booked",
    "channels_dispatched": [
      { "channel": "sms",   "status": "delivered" },
      { "channel": "email", "status": "delivered" },
      { "channel": "chat",  "status": "delivered" }
    ],
    "rule_matched": "openloop-partner-a:glp1:appointment_booked",
    "timestamp": "2026-06-09T04:12:56.030176+00:00"
  },
  {
    "event_type": "prescription_ready",
    "channels_dispatched": [
      { "channel": "sms",  "status": "delivered" },
      { "channel": "chat", "status": "delivered" }
    ],
    "rule_matched": "prescription_ready",
    "timestamp": "2026-06-09T04:15:50.154303+00:00"
  },
  {
    "event_type": "prescription_ready",
    "channels_dispatched": [
      { "channel": "sms",  "status": "blocked_opt_out" },
      { "channel": "chat", "status": "delivered" }
    ],
    "rule_matched": "prescription_ready",
    "timestamp": "2026-06-09T04:19:05.573523+00:00"
  }
]
```

This is the answer to "did this patient get their message?" — one query, no cross-system reconciliation.

---

## What Broke and How We Fixed It

**Issue: Twilio SMS delivery blocked by carrier compliance requirements**

The router correctly called the Twilio API on every trigger — confirmed in Twilio's message logs (Outgoing API, correct From/To numbers, timestamps matching our requests). However, delivery failed due to two sequential carrier compliance errors:

- **Error 30032** (Toll-Free Verification Required): The original Twilio number (+18889069057) is a toll-free number. US carriers now require toll-free numbers to complete a verification process before sending A2P SMS. This process takes 5–7 business days.
- **Error 30034** (A2P 10DLC Unregistered Number): After switching to a local 10-digit number, US carriers blocked delivery because the number had not been registered under A2P 10DLC — a US carrier mandate requiring brands to register their messaging campaigns with The Campaign Registry before sending application-to-person SMS.

**Resolution for this prototype:** SMS runs in verified mock mode. The Twilio API integration is confirmed working (API calls reach Twilio successfully); carrier delivery is blocked by registration requirements outside the scope of this prototype. For a production deployment, OpenLoop would complete A2P 10DLC brand and campaign registration — a one-time process that unlocks compliant SMS delivery at scale.

**Why this matters for the interview:** These are real compliance walls that any platform building on Twilio for healthcare SMS will encounter. Knowing they exist, why they exist, and what the resolution path is demonstrates production-grade thinking.

---

## One Production Limitation Worth Noting

**Opt-out state resets on server restart.**

The in-memory opt-out store (`opt_out_store = {}`) does not persist between server sessions. In a HIPAA-regulated environment this is not acceptable — a patient who texted STOP must remain opted out permanently, with an immutable audit record of when and through which channel the opt-out was received.

**Production fix:** Replace the in-memory dict with a `consent_events` table in PostgreSQL. Schema: `(client_id, patient_id, channel, action, source, timestamp)`. The router queries this table before every dispatch, removing the dependency on the caller's payload consent values entirely. This also resolves the open question in the PRD about who owns consent state.
