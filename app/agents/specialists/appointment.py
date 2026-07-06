"""Appointment agent — book / reschedule / cancel."""

from __future__ import annotations

import re

from ...data import mock_data as md
from ...db import repository as repo
from ..base import AgentContext, AgentResult, BaseAgent

_APT_ID_RE = re.compile(r"apt-\d+")


class AppointmentAgent(BaseAgent):
    name = "appointment_agent"

    def __init__(self, llm, store) -> None:
        super().__init__(llm)
        self.store = store

    def handle(self, ctx: AgentContext) -> AgentResult:
        if ctx.intent == "appointment_cancellation":
            return self._cancel(ctx)
        if ctx.intent == "appointment_reschedule":
            return self._reschedule(ctx)
        if ctx.intent == "appointment_status":
            return self._status(ctx)
        return self._book(ctx)

    # -- book ----------------------------------------------------------
    def _book(self, ctx: AgentContext) -> AgentResult:
        doctor = repo.find_doctor_by_name(ctx.message)
        if not doctor:
            dept = md.match_department(ctx.message)
            candidates = repo.doctors_in(dept) if dept else []
            if len(candidates) == 1:
                doctor = candidates[0]
            elif candidates:
                # Ask the patient to pick.
                facts = (
                    f"Multiple doctors are available in {dept}. Ask the patient "
                    f"to choose one:\n"
                    + "\n".join(
                        f"- {d['name']}: {d['slots'][0]} (Rs.{d['fee']})"
                        for d in candidates
                    )
                )
                fallback = (
                    f"Several {dept} doctors are available:\n"
                    + "\n".join(
                        f"• {d['name']} — {d['slots'][0]} (Rs.{d['fee']})"
                        for d in candidates
                    )
                    + "\n\nWhich doctor would you like to book?"
                )
                reply, used = self.phrase(ctx, facts, fallback)
                return AgentResult(
                    reply=reply, agent=self.name,
                    data={"department": dept, "doctors": candidates},
                    quick_actions=[d["name"] for d in candidates],
                    llm_used=used,
                )

        if not doctor:
            facts = (
                "We need to know the symptom, department, or doctor to book.\n"
                "Offer to help find the right doctor first."
            )
            fallback = (
                "I can book that for you — which doctor or department would you "
                "like? Or tell me your symptom and I'll suggest the right one."
            )
            reply, used = self.phrase(ctx, facts, fallback)
            return AgentResult(
                reply=reply, agent=self.name,
                quick_actions=["Find a doctor"], llm_used=used,
            )

        # We have a doctor — honour a slot the patient named, if any.
        slot = md.match_slot(ctx.message, doctor["slots"])
        if slot is None and md.mentions_time_preference(ctx.message):
            # A time was requested but the doctor has no such slot — don't
            # silently book a different one; offer the real options instead.
            return self._offer_slots(ctx, doctor)
        if slot is None:
            slot = doctor["slots"][0]  # no stated preference — earliest slot

        apt_id = self.store.next_appointment_id()
        appointment = {
            "appointment_id": apt_id,
            "doctor": doctor["name"],
            "department": doctor["department"],
            "time": slot,
            "fee": doctor["fee"],
            "location": "OPD Block, Room 204",
            "status": "Confirmed",
        }
        self.store.add_appointment(ctx.session, appointment)

        checklist = repo.previsit_checklist(doctor["department"])
        facts = (
            f"Appointment confirmed:\n"
            f"- ID: {apt_id}\n"
            f"- Doctor: {doctor['name']} ({doctor['department']})\n"
            f"- Time: {slot}\n"
            f"- Location: OPD Block, Room 204\n"
            f"- Fee: Rs.{doctor['fee']}\n"
            f"Pre-visit checklist: {'; '.join(checklist)}"
        )
        fallback = (
            f"Your appointment is confirmed.\n"
            f"• ID: {apt_id}\n"
            f"• {doctor['name']} ({doctor['department']})\n"
            f"• {slot} — OPD Block, Room 204\n"
            f"• Fee: Rs.{doctor['fee']}\n\n"
            f"Please carry: {', '.join(checklist[:3])}."
        )
        reply, used = self.phrase(ctx, facts, fallback)
        return AgentResult(
            reply=reply, agent=self.name,
            data={"appointment": appointment, "checklist": checklist},
            quick_actions=["Reschedule", "Cancel", "Pre-visit checklist"],
            llm_used=used,
        )

    def _offer_slots(self, ctx: AgentContext, doctor: dict) -> AgentResult:
        """Requested time isn't available — show the doctor's real open slots."""
        slots = doctor["slots"]
        facts = (
            f"The patient asked for a time that is not available with "
            f"{doctor['name']} ({doctor['department']}). The available slots are: "
            f"{', '.join(slots)}. Ask them to choose one of these."
        )
        fallback = (
            f"{doctor['name']} isn't available at that time. The open slots are:\n"
            + "\n".join(f"• {s}" for s in slots)
            + "\n\nWhich one would you like?"
        )
        reply, used = self.phrase(ctx, facts, fallback)
        return AgentResult(
            reply=reply, agent=self.name,
            data={"doctor": doctor["name"], "available_slots": slots},
            quick_actions=list(slots), llm_used=used,
        )

    @staticmethod
    def _select_appointment(appts: list[dict], message: str) -> dict | None:
        """Pick which appointment the patient means.

        Prefers an explicit ``APT-####`` id, then a doctor named in the message,
        otherwise the most recent still-active appointment (so a cancelled one
        isn't reused). Returns ``None`` only when there are no appointments.
        """
        if not appts:
            return None
        low = message.lower()
        id_match = _APT_ID_RE.search(low)
        if id_match:
            by_id = next(
                (a for a in appts if a["appointment_id"].lower() == id_match.group(0)),
                None,
            )
            if by_id:
                return by_id
        for appt in reversed(appts):
            if appt["doctor"].split()[-1].lower() in low:
                return appt
        for appt in reversed(appts):
            if appt.get("status") != "Cancelled":
                return appt
        return appts[-1]

    # -- status (view) -------------------------------------------------
    def _status(self, ctx: AgentContext) -> AgentResult:
        appts = ctx.session.get("appointments", [])
        if not appts:
            fallback = (
                "You don't have any appointments booked yet. "
                "Would you like to book one?"
            )
            return AgentResult(reply=fallback, agent=self.name,
                               quick_actions=["Book appointment"])

        # Narrow to a specific booking if the patient named a doctor or id.
        low = ctx.message.lower()
        named = [
            a for a in appts
            if a["doctor"].split()[-1].lower() in low
            or a["appointment_id"].lower() in low
        ]
        shown = named or appts

        def describe(a: dict) -> str:
            return (
                f"{a['appointment_id']}: {a['doctor']} ({a['department']}), "
                f"{a['time']}, {a['location']}, Rs.{a['fee']}, status {a['status']}"
            )

        facts = (
            "The patient's appointment(s) on record (do not invent any others):\n"
            + "\n".join(f"- {describe(a)}" for a in shown)
        )
        fallback = (
            ("Here is your appointment:" if len(shown) == 1
             else "Here are your appointments:")
            + "\n"
            + "\n".join(
                f"• {a['appointment_id']} — {a['doctor']} ({a['department']})\n"
                f"  {a['time']} · {a['location']} · Rs.{a['fee']} · {a['status']}"
                for a in shown
            )
        )
        reply, used = self.phrase(ctx, facts, fallback)

        has_active = any(a.get("status") != "Cancelled" for a in shown)
        quick_actions = (["Reschedule", "Cancel"] if has_active else []) + ["Book appointment"]
        return AgentResult(
            reply=reply, agent=self.name,
            data={"appointments": shown},
            quick_actions=quick_actions, llm_used=used,
        )

    # -- reschedule ----------------------------------------------------
    def _reschedule(self, ctx: AgentContext) -> AgentResult:
        appts = ctx.session.get("appointments", [])
        appt = self._select_appointment(appts, ctx.message)
        if not appt:
            fallback = "I couldn't find an existing appointment to reschedule. Would you like to book a new one?"
            return AgentResult(reply=fallback, agent=self.name,
                               quick_actions=["Book appointment"])
        doctor = next((d for d in repo.all_doctors() if d["name"] == appt["doctor"]), None)
        slots = doctor["slots"] if doctor else []

        # If the patient named an available target slot, move the booking now.
        target = md.match_slot(ctx.message, slots) if slots else None
        if target and target != appt["time"]:
            old_time = appt["time"]
            appt["time"] = target
            appt["status"] = "Confirmed"
            self.store.update_appointment_time(appt["appointment_id"], target)
            facts = (
                f"Appointment {appt['appointment_id']} with {appt['doctor']} has "
                f"been moved from {old_time} to {target}."
            )
            fallback = (
                f"Done — appointment {appt['appointment_id']} with {appt['doctor']} "
                f"is now {target} (was {old_time})."
            )
            reply, used = self.phrase(ctx, facts, fallback)
            return AgentResult(reply=reply, agent=self.name,
                               data={"appointment": appt},
                               quick_actions=["Reschedule again", "Cancel"],
                               llm_used=used)

        # Otherwise offer the alternative slots and ask which to move to.
        options = [s for s in slots if s != appt["time"]] or ["Tomorrow 10:00 AM"]
        facts = (
            f"Existing appointment {appt['appointment_id']} with {appt['doctor']} "
            f"is at {appt['time']}. Other available slots: {', '.join(options)}."
        )
        fallback = (
            f"Your appointment {appt['appointment_id']} with {appt['doctor']} is "
            f"currently {appt['time']}. Available alternatives: "
            f"{', '.join(options)}. Which one should I move it to?"
        )
        reply, used = self.phrase(ctx, facts, fallback)
        return AgentResult(reply=reply, agent=self.name,
                           data={"appointment": appt, "options": options},
                           quick_actions=options, llm_used=used)

    # -- cancel --------------------------------------------------------
    def _cancel(self, ctx: AgentContext) -> AgentResult:
        appts = ctx.session.get("appointments", [])
        appt = self._select_appointment(appts, ctx.message)
        if not appt:
            return AgentResult(
                reply="I couldn't find an appointment to cancel. Is there anything else I can help with?",
                agent=self.name,
            )
        appt["status"] = "Cancelled"
        self.store.update_appointment_status(appt["appointment_id"], "Cancelled")
        facts = f"Appointment {appt['appointment_id']} with {appt['doctor']} is now cancelled."
        fallback = (
            f"Done — appointment {appt['appointment_id']} with {appt['doctor']} "
            f"has been cancelled. Would you like to book a new one?"
        )
        reply, used = self.phrase(ctx, facts, fallback)
        return AgentResult(reply=reply, agent=self.name,
                           data={"appointment": appt},
                           quick_actions=["Book appointment"], llm_used=used)
