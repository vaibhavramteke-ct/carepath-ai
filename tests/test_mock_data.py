"""Unit tests for symptom matching (pure logic), DB-backed reference lookups,
and seed-data integrity."""

from __future__ import annotations

import pytest

from app.data import mock_data as md
from app.db import repository as repo

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Department matching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("I have knee pain", "Orthopedics"),
        ("my heart is racing", "Cardiology"),
        ("I have a skin rash", "Dermatology"),
        ("I have fever and cough", "General Medicine"),
        ("pregnancy related question", "Gynecology"),
    ],
)
def test_match_department_by_symptom(text, expected):
    assert md.match_department(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I need a cardiologist", "Cardiology"),
        ("orthopaedic surgeon", "Orthopedics"),
        ("skin specialist", "Dermatology"),
    ],
)
def test_match_department_by_specialty(text, expected):
    assert md.match_department(text) == expected


def test_match_department_specialty_takes_priority():
    # Specialty keywords are checked before symptom keywords.
    assert md.match_department("I want a heart specialist") == "Cardiology"


def test_match_department_no_match_returns_none():
    assert md.match_department("hello there") is None


# --------------------------------------------------------------------------- #
# Appointment slot matching (pure logic)
# --------------------------------------------------------------------------- #
_MEHTA_SLOTS = ["Today 5:00 PM", "Tomorrow 10:00 AM", "Tomorrow 4:30 PM"]
_KULKARNI_SLOTS = ["Tomorrow 11:00 AM", "Tomorrow 3:00 PM", "Saturday 9:30 AM"]
_RAO_SLOTS = ["Saturday 10:00 AM", "Saturday 12:30 PM", "Monday 11:00 AM"]


@pytest.mark.parametrize(
    "text, slots, expected",
    [
        # Exact day + time.
        ("book dr mehta tomorrow 4:30 pm", _MEHTA_SLOTS, "Tomorrow 4:30 PM"),
        # Day only, two candidates -> earliest listed wins.
        ("something tomorrow", _MEHTA_SLOTS, "Tomorrow 10:00 AM"),
        # Time only, meridiem disambiguates same hour across days.
        ("can I come at 5pm", _MEHTA_SLOTS, "Today 5:00 PM"),
        # "at h" hour reference.
        ("see me at 11", _KULKARNI_SLOTS, "Tomorrow 11:00 AM"),
        # Colon time without meridiem.
        ("the 12:30 slot", _RAO_SLOTS, "Saturday 12:30 PM"),
        # Weekday name.
        ("monday works for me", _RAO_SLOTS, "Monday 11:00 AM"),
        # Part-of-day only, no clock time.
        ("something in the morning", _KULKARNI_SLOTS, "Tomorrow 11:00 AM"),
    ],
)
def test_match_slot_picks_requested(text, slots, expected):
    assert md.match_slot(text, slots) == expected


@pytest.mark.parametrize(
    "text, slots",
    [
        # No day/time signal at all.
        ("book dr mehta", _MEHTA_SLOTS),
        # A day the doctor does not offer.
        ("can you do wednesday", _MEHTA_SLOTS),
        # An hour none of the slots match.
        ("how about 8pm", _MEHTA_SLOTS),
    ],
)
def test_match_slot_returns_none_when_unmatched(text, slots):
    assert md.match_slot(text, slots) is None


def test_match_slot_ignores_appointment_id_digits():
    # The id must not be misread as a time; nothing else in the text matches.
    assert md.match_slot("reschedule APT-10247", _MEHTA_SLOTS) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("book dr mehta tomorrow", True),
        ("at 4:30 pm please", True),
        ("in the evening", True),
        ("saturday", True),
        ("book dr mehta", False),
        ("cancel my appointment", False),
        ("reschedule APT-10247", False),
    ],
)
def test_mentions_time_preference(text, expected):
    assert md.mentions_time_preference(text) is expected


# --------------------------------------------------------------------------- #
# Doctor lookups
# --------------------------------------------------------------------------- #
def test_doctors_in_filters_by_department():
    ortho = repo.doctors_in("Orthopedics")
    assert ortho and all(d["department"] == "Orthopedics" for d in ortho)


def test_doctors_in_unknown_department_empty():
    assert repo.doctors_in("Neurology") == []


@pytest.mark.parametrize(
    "text, expected_name",
    [
        ("book dr mehta please", "Dr. Mehta"),
        ("I'd like to see Kulkarni", "Dr. Kulkarni"),
        ("appointment with dr. shah", "Dr. Shah"),
    ],
)
def test_find_doctor_by_name(text, expected_name):
    doctor = repo.find_doctor_by_name(text)
    assert doctor is not None and doctor["name"] == expected_name


def test_find_doctor_by_name_no_match():
    assert repo.find_doctor_by_name("book dr nobody") is None


# --------------------------------------------------------------------------- #
# Pre-visit checklist (DB-backed)
# --------------------------------------------------------------------------- #
def test_previsit_checklist_department_specific():
    assert repo.previsit_checklist("Cardiology") == md.PREVISIT_CHECKLISTS["Cardiology"]


def test_previsit_checklist_falls_back_to_default():
    assert repo.previsit_checklist(None) == md.PREVISIT_CHECKLISTS["_default"]
    assert repo.previsit_checklist("Dermatology") == md.PREVISIT_CHECKLISTS["_default"]


# --------------------------------------------------------------------------- #
# Data integrity
# --------------------------------------------------------------------------- #
def test_every_doctor_has_required_fields_and_slots():
    for d in md.DOCTORS:
        for field in ("id", "name", "department", "fee", "languages", "slots"):
            assert field in d, f"{d.get('name')} missing {field}"
        assert len(d["slots"]) >= 3, f"{d['name']} should have >= 3 slots"


def test_every_doctor_department_exists():
    dept_names = {dept["name"] for dept in md.DEPARTMENTS}
    for d in md.DOCTORS:
        assert d["department"] in dept_names


def test_sample_bill_totals_are_consistent():
    bill = md.SAMPLE_BILL
    assert sum(i["amount"] for i in bill["items"]) == bill["total"]
    assert bill["insurance_approved"] + bill["patient_payable"] == bill["total"]
