"""Unit tests for the in-memory Store (sessions, appointments, audit, handoffs)."""

from __future__ import annotations

import pytest

from app.store import Store

pytestmark = pytest.mark.unit


@pytest.fixture
def fresh_store():
    return Store()


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def test_create_session_generates_id_and_defaults(fresh_store):
    session = fresh_store.get_or_create_session(None, patient_id="P123")
    assert session["id"].startswith("sess-")
    assert session["patient_id"] == "P123"
    assert session["journey_stage"] == "symptom_discovery"
    assert session["history"] == []
    assert session["appointments"] == []
    assert session["id"] in fresh_store.sessions


def test_get_existing_session_is_reloaded_from_db(fresh_store):
    first = fresh_store.get_or_create_session(None)
    fresh_store.add_message(first, "user", "remember me")
    again = fresh_store.get_or_create_session(first["id"])
    # Same persisted session (by id), with the message read back from the DB.
    assert again["id"] == first["id"]
    assert again["journey_stage"] == first["journey_stage"]
    assert [m["text"] for m in again["history"]] == ["remember me"]


def test_unknown_session_id_is_honoured_as_new_id(fresh_store):
    session = fresh_store.get_or_create_session("custom-id")
    assert session["id"] == "custom-id"
    assert "custom-id" in fresh_store.sessions


def test_add_message_appends_history(fresh_store):
    session = fresh_store.get_or_create_session(None)
    fresh_store.add_message(session, "user", "hi")
    fresh_store.add_message(session, "assistant", "hello")
    assert [m["role"] for m in session["history"]] == ["user", "assistant"]
    assert session["history"][0]["text"] == "hi"
    assert "at" in session["history"][0]


# --------------------------------------------------------------------------- #
# Appointment IDs
# --------------------------------------------------------------------------- #
def test_appointment_ids_increment_and_are_unique(fresh_store):
    first = fresh_store.next_appointment_id()
    second = fresh_store.next_appointment_id()
    assert first == "APT-10246"
    assert second == "APT-10247"
    assert first != second


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #
def _sample_appointment(apt_id: str) -> dict:
    return {
        "appointment_id": apt_id,
        "doctor": "Dr. Mehta",
        "department": "Cardiology",
        "time": "Today 5:00 PM",
        "fee": 1200,
        "location": "OPD Block, Room 204",
        "status": "Confirmed",
    }


def test_add_appointment_persists_and_updates_session(fresh_store):
    session = fresh_store.get_or_create_session(None)
    apt = _sample_appointment(fresh_store.next_appointment_id())
    fresh_store.add_appointment(session, apt)
    # Mutates the in-flight dict...
    assert session["appointments"][-1] == apt
    # ...and persists, so a reload sees it.
    reloaded = fresh_store.get_or_create_session(session["id"])
    assert reloaded["appointments"][-1]["appointment_id"] == apt["appointment_id"]


def test_update_appointment_time_persists(fresh_store):
    session = fresh_store.get_or_create_session(None)
    apt = _sample_appointment(fresh_store.next_appointment_id())
    fresh_store.add_appointment(session, apt)

    fresh_store.update_appointment_time(apt["appointment_id"], "Tomorrow 4:30 PM")

    reloaded = fresh_store.get_or_create_session(session["id"])
    assert reloaded["appointments"][-1]["time"] == "Tomorrow 4:30 PM"


def test_update_appointment_status_persists(fresh_store):
    session = fresh_store.get_or_create_session(None)
    apt = _sample_appointment(fresh_store.next_appointment_id())
    fresh_store.add_appointment(session, apt)

    fresh_store.update_appointment_status(apt["appointment_id"], "Cancelled")

    reloaded = fresh_store.get_or_create_session(session["id"])
    assert reloaded["appointments"][-1]["status"] == "Cancelled"


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def test_log_audit_stamps_timestamp_and_preserves_fields(fresh_store):
    fresh_store.log_audit({"intent": "general", "agent": "general_agent"})
    entry = fresh_store.audit[-1]
    assert entry["intent"] == "general"
    assert entry["agent"] == "general_agent"
    assert "at" in entry


# --------------------------------------------------------------------------- #
# Handoff / escalation queue
# --------------------------------------------------------------------------- #
def test_create_handoff_builds_open_ticket(fresh_store):
    ticket = fresh_store.create_handoff(
        session_id="sess-x",
        team="nurse",
        reason="needs help",
        query="please help",
        priority="high",
        patient_id="P987",
    )
    assert ticket["ticket_id"].startswith("TKT-")
    assert ticket["team"] == "nurse"
    assert ticket["priority"] == "high"
    assert ticket["status"] == "open"
    assert ticket in fresh_store.handoffs


def test_create_handoff_masks_patient_id(fresh_store):
    ticket = fresh_store.create_handoff(
        session_id="s", team="nurse", reason="r", query="q", patient_id="P987654"
    )
    assert ticket["patient_masked"] == "P9***"


def test_create_handoff_anonymous_when_no_patient(fresh_store):
    ticket = fresh_store.create_handoff(
        session_id="s", team="nurse", reason="r", query="q"
    )
    assert ticket["patient_masked"] == "anonymous"
