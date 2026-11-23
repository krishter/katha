"""
Per-client limits on the public endpoints that cost something (SH-09).

/onboarding/start creates an account and sends an email to any address it is
given, and /c/{token}/ask makes an LLM call for every question. Without a
limit one client can send a stranger thousands of emails from katha.life, or
spend the LLM budget. These limits are far above what a family does (one
sign-up, a few questions a week) and far below what a script does.

In memory, because the backend runs one worker (Dockerfile: --workers 1). A
restart forgets the counts, which is fine for a limit meant to stop a flood.
Running more than one worker would need this moved to Postgres or Redis.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    """The address of whoever sent the request.

    Behind Caddy, request.client is Caddy's container. Caddy sets
    X-Forwarded-For to the real client and discards any value the client
    sent (checked against caddy 2.11, 2026-11-21), so the last entry is
    trustworthy. X-Real-IP is passed through untouched and is never read.
    The backend is not reachable except through Caddy and the operator's
    loopback tunnel, so nobody else can set the header.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "-"


@dataclass(frozen=True)
class Limit:
    name: str
    count: int
    seconds: int


# Shared by /onboarding/start and /auth/magic-link: both send a sign-in email.
EMAIL_LINK = Limit("email-link", count=10, seconds=3600)
# /c/{token}/ask: each question is screened by an LLM. Per card link and per
# client, so neither one leaked link nor one client can run it up.
CARD_ASK_PER_LINK = Limit("card-ask-link", count=5, seconds=3600)
CARD_ASK_PER_CLIENT = Limit("card-ask-client", count=20, seconds=3600)
# /c/{token}/react: a row and a message to the parent's queue per tap.
CARD_REACT_PER_LINK = Limit("card-react-link", count=10, seconds=3600)
# /family/setup-links/email: the done screen's "email me these links". Per
# account; each send also replaces the family-number code.
SETUP_LINKS_EMAIL = Limit("setup-links-email", count=3, seconds=3600)

_MAX_KEYS = 50_000


class Limiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def allow(self, limit: Limit, key: str, now: float | None = None) -> bool:
        """Count one request for `key` under `limit`; False once it is over."""
        now = time.monotonic() if now is None else now
        if len(self._hits) > _MAX_KEYS:
            self._prune(now)
        hits = self._hits.setdefault((limit.name, key), deque())
        while hits and now - hits[0] >= limit.seconds:
            hits.popleft()
        if len(hits) >= limit.count:
            return False
        hits.append(now)
        return True

    def _prune(self, now: float) -> None:
        longest = max(
            (
                EMAIL_LINK,
                CARD_ASK_PER_LINK,
                CARD_ASK_PER_CLIENT,
                CARD_REACT_PER_LINK,
                SETUP_LINKS_EMAIL,
            ),
            key=lambda lim: lim.seconds,
        ).seconds
        for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= longest]:
            del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()


limiter = Limiter()


def enforce(limit: Limit, key: str) -> None:
    """Raise 429 when `key` is over `limit`."""
    if not limiter.allow(limit, key):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a while and try again.",
            headers={"Retry-After": str(limit.seconds)},
        )
