"""Seed hospital data + symptom-matching logic for the MVP.

Everything here is demo data — no real EHR / billing / insurance integration.
Mirrors the sample data described in the requirements document (section 14).

The constants below are the **seed source**: on startup they are loaded into the
database (see :mod:`app.db.seed`). At runtime the app reads this same data back
out of the database through :mod:`app.db.repository`. The keyword maps and
:func:`match_department` are pure logic (not data) and stay here.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Departments
# --------------------------------------------------------------------------- #
DEPARTMENTS: list[dict] = [
    {"name": "Cardiology", "description": "Heart and blood-vessel conditions"},
    {"name": "Orthopedics", "description": "Bones, joints, muscles and spine"},
    {"name": "General Medicine", "description": "Fever, infections, general adult care"},
    {"name": "Gynecology", "description": "Women's health and pregnancy care"},
    {"name": "Dermatology", "description": "Skin, hair and nail conditions"},
]

# --------------------------------------------------------------------------- #
# Doctor directory (>= 3 slots each)
# --------------------------------------------------------------------------- #
DOCTORS: list[dict] = [
    {
        "id": "doc-mehta",
        "name": "Dr. Mehta",
        "department": "Cardiology",
        "languages": ["English", "Hindi"],
        "gender": "M",
        "fee": 1200,
        "teleconsult": True,
        "slots": ["Today 5:00 PM", "Tomorrow 10:00 AM", "Tomorrow 4:30 PM"],
    },
    {
        "id": "doc-kulkarni",
        "name": "Dr. Kulkarni",
        "department": "Orthopedics",
        "languages": ["English", "Marathi"],
        "gender": "M",
        "fee": 900,
        "teleconsult": False,
        "slots": ["Tomorrow 11:00 AM", "Tomorrow 3:00 PM", "Saturday 9:30 AM"],
    },
    {
        "id": "doc-shah",
        "name": "Dr. Shah",
        "department": "General Medicine",
        "languages": ["English", "Hindi"],
        "gender": "M",
        "fee": 700,
        "teleconsult": True,
        "slots": ["Today 3:00 PM", "Today 6:00 PM", "Tomorrow 9:00 AM"],
    },
    {
        "id": "doc-rao",
        "name": "Dr. Rao",
        "department": "Gynecology",
        "languages": ["English", "Hindi"],
        "gender": "F",
        "fee": 1000,
        "teleconsult": True,
        "slots": ["Saturday 10:00 AM", "Saturday 12:30 PM", "Monday 11:00 AM"],
    },
    {
        "id": "doc-iyer",
        "name": "Dr. Iyer",
        "department": "Dermatology",
        "languages": ["English", "Tamil", "Hindi"],
        "gender": "F",
        "fee": 800,
        "teleconsult": True,
        "slots": ["Friday 4:00 PM", "Friday 5:30 PM", "Saturday 2:00 PM"],
    },
]

# --------------------------------------------------------------------------- #
# Symptom -> department keyword mapping (non-emergency routing)
# --------------------------------------------------------------------------- #
SYMPTOM_DEPARTMENT_MAP: dict[str, list[str]] = {
    "Cardiology": [
        "heart", "cardiac", "palpitation", "bp", "blood pressure",
        "cholesterol", "chest",
    ],
    "Orthopedics": [
        "knee", "joint", "bone", "back pain", "fracture", "sprain",
        "shoulder", "hip", "arthritis", "leg pain", "ankle",
    ],
    "General Medicine": [
        "fever", "cold", "cough", "flu", "body pain", "headache", "weakness",
        "fatigue", "diabetes", "sugar", "infection", "stomach", "vomit",
        "loose motion", "acidity", "dizzy",
    ],
    "Dermatology": [
        "skin", "rash", "acne", "pimple", "hair", "itch", "itching",
        "eczema", "allergy", "nail",
    ],
    "Gynecology": [
        "pregnan", "period", "menstru", "gynec", "pcod", "pcos",
        "uterus", "ovary",
    ],
}

# Specialty names patients may type directly.
SPECIALTY_KEYWORDS: dict[str, list[str]] = {
    "Cardiology": ["cardiolog", "heart specialist"],
    "Orthopedics": ["orthopedic", "orthopaedic", "bone specialist"],
    "General Medicine": ["physician", "general medicine", "general doctor"],
    "Gynecology": ["gynec", "gynaec", "obstetric"],
    "Dermatology": ["dermatolog", "skin specialist"],
}


def match_department(text: str) -> str | None:
    """Best-effort symptom/specialty -> department mapping (pure keyword logic)."""
    low = text.lower()
    for dept, keywords in SPECIALTY_KEYWORDS.items():
        if any(k in low for k in keywords):
            return dept
    for dept, keywords in SYMPTOM_DEPARTMENT_MAP.items():
        if any(k in low for k in keywords):
            return dept
    return None


# --------------------------------------------------------------------------- #
# Appointment slot matching (non-emergency; pure keyword/time logic)
#
# Doctor slots are fixed strings like "Tomorrow 4:30 PM" / "Saturday 9:30 AM".
# These helpers let the appointment agent honour a day/time the patient names
# instead of always booking the first slot.
# --------------------------------------------------------------------------- #
_DAY_WORDS: tuple[str, ...] = (
    "today", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

# Rough part-of-day -> meridiem, used only when the patient gives no clock time.
_PART_OF_DAY: dict[str, str] = {
    "morning": "am",
    "noon": "pm",
    "afternoon": "pm",
    "evening": "pm",
    "night": "pm",
}

# A clock time carrying a meridiem, e.g. "4:30 pm", "5pm", "9 a.m.".
_MERIDIEM_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b")
# A bare "h:mm" with no meridiem, e.g. "12:30".
_COLON_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
# "at 6" style hour references (avoids matching stray ids like APT-10247).
_AT_HOUR = re.compile(r"\bat\s+(\d{1,2})\b")


def _extract_times(low: str) -> list[tuple[int, int | None, str | None]]:
    """Parse (hour, minute, meridiem) tuples from free text.

    A bare number only counts as a time when it carries a meridiem, a ``:mm``,
    or an explicit ``at`` — so appointment ids and other stray digits are not
    mistaken for times. ``minute``/``meridiem`` are ``None`` when unspecified.
    """
    times: list[tuple[int, int | None, str | None]] = []
    for m in _MERIDIEM_TIME.finditer(low):
        hour = int(m.group(1))
        if 1 <= hour <= 12:
            minute = int(m.group(2)) if m.group(2) is not None else None
            times.append((hour, minute, "am" if m.group(3) == "a" else "pm"))
    for m in _COLON_TIME.finditer(low):
        hour, minute = int(m.group(1)), int(m.group(2))
        if 1 <= hour <= 12 and 0 <= minute <= 59:
            times.append((hour, minute, None))
    for m in _AT_HOUR.finditer(low):
        hour = int(m.group(1))
        if 1 <= hour <= 12:
            times.append((hour, None, None))
    return times


def _parse_slot(slot: str) -> tuple[str | None, int | None, int | None, str | None]:
    """Split a well-formed slot string into (day, hour, minute, meridiem)."""
    low = slot.lower()
    day = next((d for d in _DAY_WORDS if d in low), None)
    tm = _MERIDIEM_TIME.search(low)
    if not tm:
        return day, None, None, None
    minute = int(tm.group(2)) if tm.group(2) is not None else 0
    return day, int(tm.group(1)), minute, "am" if tm.group(3) == "a" else "pm"


def _score_slot(
    slot: str,
    days: set[str],
    times: list[tuple[int, int | None, str | None]],
    parts: set[str],
) -> int:
    """How well a slot matches the patient's stated day/time (0 = no signal)."""
    s_day, s_hour, s_min, s_mer = _parse_slot(slot)
    score = 2 if s_day and s_day in days else 0

    best_time = 0
    for hour, minute, mer in times:
        if hour != s_hour:
            continue
        if mer is not None and s_mer is not None and mer != s_mer:
            continue  # explicit meridiem conflict — a different slot
        if minute is not None and s_min is not None and minute != s_min:
            continue  # explicit minute conflict
        points = 2  # the hour matched
        if mer is not None and mer == s_mer:
            points += 2
        if minute is not None and minute == s_min:
            points += 1
        best_time = max(best_time, points)
    score += best_time

    # A vague "in the evening" only counts when no explicit time was matched.
    if best_time == 0 and s_mer in parts:
        score += 1
    return score


def mentions_time_preference(text: str) -> bool:
    """True if the message names a day, a clock time, or a part of the day.

    Lets the caller tell "book me anything" (fall back to the first slot) apart
    from "book me Tuesday" for a doctor with no Tuesday slot (offer alternatives
    rather than silently booking a different time).
    """
    low = text.lower()
    if any(d in low for d in _DAY_WORDS):
        return True
    if any(p in low for p in _PART_OF_DAY):
        return True
    return bool(_extract_times(low))


def match_slot(text: str, slots: list[str]) -> str | None:
    """Return the slot best matching the day/time in ``text``, else ``None``.

    Ties are broken toward the earliest listed slot (slots are chronological),
    so "tomorrow" for a doctor with two tomorrow slots picks the earlier one.
    """
    low = text.lower()
    days = {d for d in _DAY_WORDS if d in low}
    times = _extract_times(low)
    parts = {_PART_OF_DAY[w] for w in _PART_OF_DAY if w in low}

    best, best_score = None, 0
    for slot in slots:
        score = _score_slot(slot, days, times, parts)
        if score > best_score:
            best, best_score = slot, score
    return best if best_score > 0 else None


# --------------------------------------------------------------------------- #
# Sample clinical / billing records (demo only)
# --------------------------------------------------------------------------- #
SAMPLE_PRESCRIPTION: dict = {
    "patient": "Demo Patient",
    "medicines": [
        {
            "name": "Paracetamol 500 mg",
            "timing": "After food",
            "frequency": "Twice daily",
            "duration": "3 days",
        },
        {
            "name": "Pantoprazole 40 mg",
            "timing": "Before breakfast",
            "frequency": "Once daily",
            "duration": "5 days",
        },
    ],
}

SAMPLE_DISCHARGE_SUMMARY: dict = {
    "patient": "Demo Patient",
    "reason": "Minor knee surgery",
    "condition": "Discharged in stable condition",
    "medicines_days": 5,
    "wound_care": "Dressing change required every alternate day",
    "activity": "Avoid heavy walking for 2 weeks",
    "follow_up": "Follow-up after 7 days",
    "warning_signs": ["fever", "severe pain", "swelling", "bleeding", "wound discharge"],
}

SAMPLE_BILL: dict = {
    "appointment_id": "APT-10245",
    "items": [
        {"label": "Consultation", "amount": 900},
        {"label": "Lab Tests", "amount": 1500},
        {"label": "Procedure Charges", "amount": 12000},
        {"label": "Room Charges", "amount": 5000},
        {"label": "Pharmacy", "amount": 2200},
    ],
    "total": 21600,
    "insurance_approved": 16000,
    "patient_payable": 5600,
}

SAMPLE_INSURANCE: dict = {
    "policy": "Corporate Mediclaim — HealthSecure",
    "claim_id": "CLM-55821",
    "status": "Partially approved",
    "approved_amount": 16000,
    "pending_documents": ["Discharge summary", "Final itemised bill", "ID proof copy"],
    "uncovered_note": "Room rent above eligible limit and pharmacy consumables are not covered.",
}

PREVISIT_CHECKLISTS: dict[str, list[str]] = {
    "Cardiology": [
        "Previous ECG / Echo reports if available",
        "Current medicine list",
        "Photo ID proof",
        "Insurance card (if applicable)",
        "Reach 20 minutes before the appointment",
    ],
    "Orthopedics": [
        "Previous X-rays / MRI reports",
        "Current medicine list",
        "Photo ID proof",
        "Comfortable clothing for examination",
        "Reach 15 minutes before the appointment",
    ],
    "_default": [
        "Photo ID proof",
        "Any previous reports or prescriptions",
        "Current medicine list",
        "Insurance card (if applicable)",
        "Reach 15 minutes before the appointment",
    ],
}
