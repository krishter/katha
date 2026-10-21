"""
What a log line may say about a person (PRE_PILOT_FIX_PLAN P7, M-07).

Logs are outside every erasure: they sit on the box, in Docker's json-file,
long after DELETE /user has run. So nothing that identifies her or repeats
her words goes into one. Transcripts, cards, topic labels and model output
are logged by length only; numbers and emails go through these.
"""

from __future__ import annotations

from typing import Optional


def phone(number: Optional[str]) -> str:
    """ "+919876548242" -> "+91******8242". Enough to tell two numbers in a
    log apart, not enough to dial."""
    if not number:
        return "-"
    number = str(number).replace("whatsapp:", "")
    if len(number) <= 7:
        return "*" * len(number)
    return f"{number[:3]}{'*' * (len(number) - 7)}{number[-4:]}"


def email(address: Optional[str]) -> str:
    """ "krishna@gmail.com" -> "k***@g***.com"."""
    if not address or "@" not in str(address):
        return "-"
    local, _, domain = str(address).partition("@")
    host, dot, tld = domain.rpartition(".")
    if not dot:
        host, tld = domain, ""
    return f"{local[:1]}***@{host[:1]}***{dot}{tld}"
