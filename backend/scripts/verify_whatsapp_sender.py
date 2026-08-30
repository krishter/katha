"""
Send one real WhatsApp message and print the SID.

Exists because the two defects fixed in S3.5 were both invisible to the
test suite — it asserts against a mocked Twilio client that accepts
anything, including a Python dict repr where JSON was required. Only a
real send proves the sender actually works.

    cd backend
    ./.venv/bin/python scripts/verify_whatsapp_sender.py +91XXXXXXXXXX

    # exercise the approved template and its variable substitution:
    ./.venv/bin/python scripts/verify_whatsapp_sender.py +91XXXXXXXXXX --template

THIS SENDS A REAL MESSAGE from the production sender to the number you
pass. Use a phone you control.

A free-form send only works inside the 24-hour window opened by that
number messaging Katha first; outside it, Twilio returns 63016. Use
--template there, which is also the better test: it is the path that
carries content_variables, the field that was being sent as a Python repr.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402


def _preflight() -> list[str]:
    problems = []
    if settings.WHATSAPP_ADAPTER != "twilio":
        problems.append(
            f"WHATSAPP_ADAPTER is {settings.WHATSAPP_ADAPTER!r} — set it to "
            '"twilio" or this sends nothing at all'
        )
    if not settings.TWILIO_ACCOUNT_SID:
        problems.append("TWILIO_ACCOUNT_SID is empty")
    if not settings.TWILIO_AUTH_TOKEN:
        problems.append("TWILIO_AUTH_TOKEN is empty")
    if (
        not settings.TWILIO_WHATSAPP_NUMBER
        and not settings.TWILIO_MESSAGING_SERVICE_SID
    ):
        problems.append(
            "neither TWILIO_WHATSAPP_NUMBER nor TWILIO_MESSAGING_SERVICE_SID is set"
        )
    return problems


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("to_number", help="E.164, e.g. +919876543210")
    parser.add_argument(
        "--template",
        action="store_true",
        help="send the approved parent-welcome template instead of free text",
    )
    parser.add_argument(
        "--name", default="Friend", help="value for the template's {{1}} variable"
    )
    args = parser.parse_args()

    problems = _preflight()
    if problems:
        print("Cannot send — fix these in backend/.env first:\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    sender = (
        f"Messaging Service {settings.TWILIO_MESSAGING_SERVICE_SID}"
        if settings.TWILIO_MESSAGING_SERVICE_SID
        else f"number {settings.TWILIO_WHATSAPP_NUMBER}"
    )
    print(f"Sending via {sender} -> {args.to_number}")

    from adapters.whatsapp import TwilioWhatsAppAdapter

    adapter = TwilioWhatsAppAdapter(
        account_sid=settings.TWILIO_ACCOUNT_SID,
        auth_token=settings.TWILIO_AUTH_TOKEN,
        from_number=settings.TWILIO_WHATSAPP_NUMBER,
        messaging_service_sid=settings.TWILIO_MESSAGING_SERVICE_SID,
    )

    try:
        if args.template:
            sid_template = settings.TWILIO_TEMPLATE_PARENT_WELCOME
            if not sid_template:
                print("TWILIO_TEMPLATE_PARENT_WELCOME is empty — nothing to send")
                return 1
            print(f"Template {sid_template} with {{'1': {args.name!r}}}")
            message_sid = await adapter.send_text(
                args.to_number,
                "",
                template_sid=sid_template,
                template_variables={"1": args.name},
            )
        else:
            message_sid = await adapter.send_text(
                args.to_number,
                "This is a test message from Katha, verifying the sender works.",
            )
    except Exception as exc:
        print(f"\nSEND FAILED: {exc}")
        print(
            "\n63016 means the 24-hour window is closed — retry with --template.\n"
            "63049 means Meta declined to deliver a Marketing-categorised "
            "template to someone who has never replied; that is the finding "
            "behind S3.0 and is not a code fault."
        )
        return 1

    print(f"\nSENT. MessageSid: {message_sid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
