import json
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

app = Flask(__name__)

# --- State (in-memory, resets on restart) ---
event_log = []
opt_out_store = {}  # key: (client_id, patient_id, channel) -> True

# --- Rules (loaded once at startup) ---
with open("journey_rules.json") as f:
    rules_config = json.load(f)

RULES = rules_config["rules"]
FALLBACK_CHANNEL = rules_config["fallback_channel"]
URGENCY_OVERRIDE_CHANNEL = rules_config["urgency_override_channel"]

# --- Twilio setup (real if credentials present, mock otherwise) ---
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_ENABLED = all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM])

if TWILIO_ENABLED:
    from twilio.rest import Client as TwilioClient
    twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)


# --- Rule matching ---

def find_rule(client_id, program, event_type):
    """Return channels for the most specific matching rule."""
    candidates = [
        # Priority 1: client + program + event
        lambda r: r.get("client_id") == client_id and r.get("program") == program and r.get("event_type") == event_type,
        # Priority 2: client + event (any program)
        lambda r: r.get("client_id") == client_id and "program" not in r and r.get("event_type") == event_type,
        # Priority 3: global event default (no client, no program)
        lambda r: "client_id" not in r and "program" not in r and r.get("event_type") == event_type,
    ]
    for match in candidates:
        for rule in RULES:
            if match(rule):
                key = f"{client_id}:{program}:{event_type}" if rule.get("program") else (
                    f"{client_id}:{event_type}" if rule.get("client_id") else event_type
                )
                return rule["channels"], key
    return [FALLBACK_CHANNEL], "global_fallback"


# --- Consent check ---

def is_opted_out(client_id, patient_id, channel):
    return opt_out_store.get((client_id, patient_id, channel), False)


def consent_check(client_id, patient_id, channel, consent_payload):
    if is_opted_out(client_id, patient_id, channel):
        return "blocked_opt_out"
    if not consent_payload.get(channel, False):
        return "blocked_no_consent"
    return "ok"


# --- Channel dispatch ---

def dispatch_sms(to_number, event_type, metadata):
    if not to_number:
        return "blocked_no_contact"
    message_body = build_message("sms", event_type, metadata)
    if TWILIO_ENABLED:
        twilio_client.messages.create(body=message_body, from_=TWILIO_FROM, to=to_number)
        return "delivered"
    print(f"[MOCK SMS] To: {to_number} | {message_body}")
    return "delivered"


def dispatch_email(to_email, event_type, metadata):
    if not to_email:
        return "blocked_no_contact"
    message_body = build_message("email", event_type, metadata)
    print(f"[MOCK EMAIL] To: {to_email} | {message_body}")
    return "delivered"


def dispatch_chat(chat_user_id, event_type, metadata):
    if not chat_user_id:
        return "blocked_no_contact"
    message_body = build_message("chat", event_type, metadata)
    print(f"[MOCK CHAT] To: {chat_user_id} | {message_body}")
    return "delivered"


DISPATCH = {
    "sms": dispatch_sms,
    "email": dispatch_email,
    "chat": dispatch_chat,
}

CONTACT_KEYS = {
    "sms": "phone_number",
    "email": "email",
    "chat": "chat_user_id",
}


# --- Message templating ---

def build_message(channel, event_type, metadata):
    templates = {
        "appointment_booked": "Your appointment with {provider} is confirmed for {appointment_time}.{join}",
        "appointment_reminder": "Reminder: appointment with {provider} at {appointment_time}.{join}",
        "refill_due": "Your prescription for {medication_name} is due for refill. {days_remaining} days remaining.",
        "prescription_ready": "Your prescription for {medication_name} is ready for pickup at {pharmacy_name}.",
        "unknown_event": "You have a new notification from your care team.",
    }
    template = templates.get(event_type, templates["unknown_event"])
    join = f" Join: {metadata.get('join_link')}" if metadata.get("join_link") else ""
    return template.format(
        provider=metadata.get("provider", "your provider"),
        appointment_time=metadata.get("appointment_time", "the scheduled time"),
        medication_name=metadata.get("medication_name", "your medication"),
        days_remaining=metadata.get("days_remaining", ""),
        pharmacy_name=metadata.get("pharmacy_name", "your pharmacy"),
        join=join,
    )


# --- Routes ---

@app.route("/trigger", methods=["POST"])
def trigger():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    required = ["patient_id", "client_id", "program", "event_type", "consent", "contact"]
    missing = [f for f in required if f not in body]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    patient_id = body["patient_id"]
    client_id = body["client_id"]
    program = body["program"]
    event_type = body["event_type"]
    urgency = body.get("urgency", "normal")
    consent = body["consent"]
    contact = body["contact"]
    metadata = body.get("metadata", {})

    channels, rule_key = find_rule(client_id, program, event_type)

    # Urgent events prepend SMS to the channel list
    if urgency == "urgent" and URGENCY_OVERRIDE_CHANNEL not in channels:
        channels = [URGENCY_OVERRIDE_CHANNEL] + channels

    results = []
    for channel in channels:
        gate = consent_check(client_id, patient_id, channel, consent)
        if gate != "ok":
            results.append({"channel": channel, "status": gate})
            continue

        recipient = contact.get(CONTACT_KEYS[channel])
        status = DISPATCH[channel](recipient, event_type, metadata)
        results.append({"channel": channel, "status": status})

    event_id = f"evt-{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(timezone.utc).isoformat()

    record = {
        "event_id": event_id,
        "patient_id": patient_id,
        "client_id": client_id,
        "program": program,
        "event_type": event_type,
        "urgency": urgency,
        "channels_dispatched": results,
        "rule_matched": rule_key,
        "timestamp": timestamp,
    }
    event_log.append(record)

    return jsonify(record), 200


@app.route("/opt-out", methods=["POST"])
def opt_out():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    required = ["patient_id", "client_id", "channel"]
    missing = [f for f in required if f not in body]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    valid_channels = ["sms", "email", "chat"]
    if body["channel"] not in valid_channels:
        return jsonify({"error": f"channel must be one of {valid_channels}"}), 400

    key = (body["client_id"], body["patient_id"], body["channel"])
    opt_out_store[key] = True

    return jsonify({
        "patient_id": body["patient_id"],
        "client_id": body["client_id"],
        "channel": body["channel"],
        "status": "opted_out",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200


@app.route("/log", methods=["GET"])
def log():
    results = event_log

    filters = {
        "patient_id": lambda r, v: r["patient_id"] == v,
        "client_id": lambda r, v: r["client_id"] == v,
        "event_type": lambda r, v: r["event_type"] == v,
        "status": lambda r, v: any(c["status"] == v for c in r["channels_dispatched"]),
    }

    for param, check in filters.items():
        value = request.args.get(param)
        if value:
            results = [r for r in results if check(r, value)]

    return jsonify(results), 200


@app.route("/rules", methods=["GET"])
def rules():
    return jsonify(rules_config), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
