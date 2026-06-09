# Multi-Channel Patient Event Router

A lightweight API that accepts patient events and routes communications to the right channel (SMS via Twilio, email and chat as mocks) based on configurable rules.

Built as a working prototype to demonstrate unified communications routing — directly relevant to platforms managing multiple channel integrations (Twilio, Amazon Connect, Tellescope).

---

## Setup

### Prerequisites
- Python 3.9+
- A Twilio account (optional — SMS falls back to mock if credentials are absent)

### Install dependencies
```bash
pip install -r requirements.txt
```

### Configure environment variables
Create a `.env` file in this directory:
```
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
```
If these are absent, SMS is mocked (logged to console) and the app runs without Twilio.

**On Replit:** Add these as Secrets via the padlock icon in the sidebar instead of a `.env` file.

### Run the server
```bash
python app.py
```
Server starts at `http://localhost:5000`.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/trigger` | Route and dispatch a patient event |
| `POST` | `/opt-out` | Record a patient opt-out (persists in memory for session) |
| `GET` | `/log` | Query the event audit trail |
| `GET` | `/rules` | Inspect the current routing config |

---

## Example curl Commands

### 1. Appointment booked — multi-channel confirmation
```bash
curl -s -X POST http://localhost:5000/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "pt-001",
    "client_id": "openloop-partner-a",
    "program": "glp1",
    "event_type": "appointment_booked",
    "urgency": "normal",
    "consent": { "sms": true, "email": true, "chat": true },
    "contact": {
      "phone_number": "+1YOUR_TEST_NUMBER",
      "email": "patient@example.com",
      "chat_user_id": "chat-user-001"
    },
    "metadata": {
      "appointment_time": "2026-06-15T14:00:00Z",
      "provider": "Dr. Smith",
      "location_type": "virtual",
      "join_link": "https://visit.openloop.com/abc123"
    }
  }' | python -m json.tool
```

### 2. Refill due — email only (per GLP-1 rule for Partner A)
```bash
curl -s -X POST http://localhost:5000/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "pt-001",
    "client_id": "openloop-partner-a",
    "program": "glp1",
    "event_type": "refill_due",
    "urgency": "normal",
    "consent": { "sms": true, "email": true, "chat": true },
    "contact": {
      "phone_number": "+1YOUR_TEST_NUMBER",
      "email": "patient@example.com",
      "chat_user_id": "chat-user-001"
    },
    "metadata": {
      "medication_name": "Semaglutide",
      "days_remaining": 7,
      "pharmacy_name": "CVS Pharmacy",
      "prescriber": "Dr. Smith"
    }
  }' | python -m json.tool
```

### 3. Opt a patient out of SMS
```bash
curl -s -X POST http://localhost:5000/opt-out \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "pt-001",
    "client_id": "openloop-partner-a",
    "channel": "sms"
  }' | python -m json.tool
```

### 4. Trigger after opt-out — SMS blocked, chat still delivers
```bash
curl -s -X POST http://localhost:5000/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "pt-001",
    "client_id": "openloop-partner-a",
    "program": "glp1",
    "event_type": "prescription_ready",
    "urgency": "normal",
    "consent": { "sms": true, "email": true, "chat": true },
    "contact": {
      "phone_number": "+1YOUR_TEST_NUMBER",
      "email": "patient@example.com",
      "chat_user_id": "chat-user-001"
    },
    "metadata": {
      "medication_name": "Semaglutide",
      "pharmacy_name": "CVS Pharmacy"
    }
  }' | python -m json.tool
```

### 5. Query the audit log
```bash
# All events for a patient
curl -s "http://localhost:5000/log?patient_id=pt-001" | python -m json.tool

# Filter to blocked opt-out events only
curl -s "http://localhost:5000/log?status=blocked_opt_out" | python -m json.tool
```

### 6. Urgent refill — SMS override
```bash
curl -s -X POST http://localhost:5000/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "pt-002",
    "client_id": "openloop-partner-a",
    "program": "glp1",
    "event_type": "refill_due",
    "urgency": "urgent",
    "consent": { "sms": true, "email": true, "chat": false },
    "contact": {
      "phone_number": "+1YOUR_TEST_NUMBER",
      "email": "patient2@example.com"
    },
    "metadata": {
      "medication_name": "Semaglutide",
      "days_remaining": 1
    }
  }' | python -m json.tool
```
> This event normally routes to email only (GLP-1 refill rule). `urgency: urgent` prepends SMS — it fires first.

### 7. Unknown event type — fallback routing
```bash
curl -s -X POST http://localhost:5000/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "pt-003",
    "client_id": "openloop-partner-a",
    "program": "glp1",
    "event_type": "care_gap_identified",
    "urgency": "normal",
    "consent": { "sms": true, "email": true, "chat": true },
    "contact": {
      "phone_number": "+1YOUR_TEST_NUMBER",
      "email": "patient3@example.com",
      "chat_user_id": "chat-user-003"
    },
    "metadata": {}
  }' | python -m json.tool
```
> No rule matches `care_gap_identified` — routes to email fallback, `rule_matched` returns `global_fallback`.

### 8. Inspect routing rules
```bash
curl -s http://localhost:5000/rules | python -m json.tool
```

---

## How Routing Works

1. The router matches the incoming event against `journey_rules.json` using three-level priority: `client_id + program + event_type` → `client_id + event_type` → global `event_type` default
2. If `urgency` is `urgent`, SMS is prepended to the matched channel list
3. For each channel in the matched rule, the router checks the opt-out store then the `consent` payload field
4. Consented channels are dispatched; blocked channels are logged with a reason
5. Every attempt — delivered or blocked — is written to the in-memory event log

---

## Key Design Decisions

See [DECISIONS.md](DECISIONS.md) for the full Architecture Decision Record.

---

## Routing Rules

Edit `journey_rules.json` to change channel preferences per client and program. Restart the server to apply changes.

---

## Known Limitations

- **Opt-out state resets on restart** — in-memory only; production requires a persistent store
- **Email and chat are mocked** — logged to console; production would integrate SendGrid and Twilio Conversations
- **No metadata validation** — invalid fields are logged but do not block dispatch
- **Patient identity is tenant-scoped** — cross-client opt-out is a v2 concern

---

## Testing

See [TESTING.md](TESTING.md) for walkthrough scenarios, including what broke during testing and how it was fixed.
