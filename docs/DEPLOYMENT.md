# Katha — Production cutover (pre-pilot)

**Why this doc exists:** there is no deployed backend today. `katha.life` resolves to Vercel
and serves the coming-soon waitlist page; `api.katha.life` and `app.katha.life` do not
resolve at all; `.github/workflows/ci.yml` runs lint and tests and deploys nothing; there is
no IaC in the repo. The self-trial was written against localhost because localhost is the
only environment that exists — not because local was the right place to test.

That has to change before family #1, and it should change before **your** Stage B run too:
the point of a self-trial is to be the first real user of the thing families will use.

**Target shape (decided 2026-09-06):**

| Piece | Where |
|---|---|
| `katha.life` | unchanged — Vercel, coming-soon waitlist |
| `app.katha.life` | Next.js dashboard + onboarding wizard, Vercel |
| `api.katha.life` | FastAPI + Postgres, one Lightsail box, **ap-south-1 (Mumbai)** |
| Media | S3 `katha-media`, ap-south-1, private, Block Public Access on |
| Email | SES, ap-south-1, `noreply@katha.life` |

Single box, `docker-compose`, Caddy in front for TLS. Chosen because **the scheduler is
in-process (APScheduler)**: `--workers 1` is a correctness constraint, not a default. Any
platform that scales to two instances double-sends every scheduled session, so autoscaling
is a liability here, not a feature, until the scheduler is made distributed-safe.

---

## Step 0 — What has to be built and merged first

Everything from Step 1 onward is ops work in consoles — AWS, GoDaddy, Twilio, Vercel. This
step is the only part that is code, and it is one branch: `feature/production-deploy`.

### 0.1 — The cookie will not survive the subdomain split *(deploy-breaking)*

`api/routes/auth.py:67` sets `katha_token` with no `domain`, making it **host-only**. In
production the magic-link verify happens on `api.katha.life`, so the cookie is scoped to
`api.katha.life` — and `frontend/proxy.ts` reads that cookie on a request to
`app.katha.life`, where it does not exist. Every `/family/*` route redirects to
`/family/login`, forever, for everyone.

This works locally only by accident: cookies ignore port numbers, so `localhost:3000` and
`localhost:8000` share one cookie jar. The bug appears the first time the two are genuinely
different hostnames — which is Step 2.

**Fix:** a `COOKIE_DOMAIN` setting (empty in dev, `.katha.life` in production) applied to
`set_cookie` in `auth.py` **and** to both `delete_cookie` calls (`auth.py:83` logout,
`admin.py:166` post-deletion). A delete that does not match the set leaves a live session
cookie behind after the user asked to be deleted — which is a DPDP problem, not a cosmetic
one.

`samesite="lax"` is fine as-is: `app.` and `api.` share the registrable domain, so these are
same-site requests. `secure` is already environment-aware.

**Acceptance:** a test asserts the cookie carries the configured domain in production config
and none in development, and that set/delete use the same value.

### 0.2 — `.env.example` is missing `PUBLIC_BASE_URL`

It carries `APP_BASE_URL` but not `PUBLIC_BASE_URL`, so anyone deploying from the example
file inherits the `http://localhost:8000` default. In production the boot check catches it —
but only because someone thought to add that check. Add it, with the comment that it must
exactly match the URL Twilio signs. Add `COOKIE_DOMAIN` alongside it.

### 0.3 — Production compose, Caddyfile, deploy script

`docker-compose.prod.yml` (no `--reload` override, named volume for Postgres),
`Caddyfile` for `api.katha.life`, and a three-line `scripts/deploy.sh` — pull, build,
`alembic upgrade head`. These live in the repo so the box is reproducible rather than
remembered.

### 0.4 — The nightly backup

`pg_dump` to S3 on a cron, with retention. Small enough to ride in this branch; the restore
rehearsal is manual and belongs in Step 5.

**Do not** ask a coding agent to run the deploy itself. It has no AWS console, no GoDaddy
login, and no Twilio session — Steps 1–8 are yours. Hand it this section, and review the
cookie change carefully; it is the one that silently locks out every family.

---

## Step 1 — The box

1. Lightsail instance, **Mumbai (ap-south-1)**, 2 GB RAM minimum (ffmpeg + Pillow), Ubuntu.
2. Attach a **static IP**. Everything downstream — DNS, Twilio's webhook, the signature check
   — is anchored to it.
3. Install Docker + compose plugin. Firewall: 22, 80, 443 only.
4. Create `docker-compose.prod.yml`: `db` (pgvector/pgvector:pg16, named volume) + `backend`
   with **no command override** — the Dockerfile's own CMD is the production one
   (`--workers 1`, no `--reload`). The dev compose file's `--reload` override must not
   follow you into production.

## Step 2 — TLS and DNS

1. Caddy in front of the backend, `api.katha.life { reverse_proxy backend:8000 }` — automatic
   certificates, and it terminates TLS so `PUBLIC_BASE_URL` is the https URL Twilio signs.
2. At GoDaddy: **A** `api` → the Lightsail static IP; **CNAME** `app` → Vercel.
3. **While you're in the DNS panel:** the apex still carries A records pointing at Vercel from
   an earlier setup. Confirm which are live and remove the orphans — a stale A record on the
   apex is how a "why is the site down" hour starts.

## Step 3 — Production `.env`

`validate_production_config` refuses to boot on any of these being wrong, which is the
behaviour you want. Set:

```
ENVIRONMENT=production
APP_BASE_URL=https://app.katha.life      # also drives the CORS allow-list in main.py
PUBLIC_BASE_URL=https://api.katha.life   # exact, no trailing slash — the signature depends on it
WHATSAPP_ADAPTER=twilio
SES_MOCK=false
JWT_SECRET=<openssl rand -hex 32>        # a NEW one; never the dev value
DATABASE_URL=postgresql+asyncpg://...    # the compose db, not localhost
AWS_S3_REGION=ap-south-1
TWILIO_* + all five template SIDs        # no quotes, no spaces around '='
SARVAM_API_KEY / ANTHROPIC_API_KEY
```

Keep it out of git, `chmod 600`, and keep a sealed copy somewhere you'd still have it if the
box died tonight.

## Step 4 — SES out of the sandbox *(start this first — it has a queue)*

1. Verify the `katha.life` domain in SES **ap-south-1** (`core/auth.py` builds its client from
   `AWS_S3_REGION`), including DKIM records at GoDaddy.
2. **Request production access.** In the sandbox SES only delivers to verified addresses, so
   every magic link to a real family silently fails. Approval is not instant — file it days
   before you need it.
3. Send yourself a magic link end to end before believing it works.

## Step 5 — Database and migrations

1. `alembic upgrade head` against the production database. Make it a step in your deploy
   script, not something you remember.
2. **Backups.** A nightly `pg_dump` to S3 with a retention window, and one restore rehearsed
   into a scratch database — an untested backup is a belief, not a backup.
3. Write the retention window down: a family that exercises deletion is still in last night's
   dump until it ages out. That is defensible under DPDP, but only if the privacy copy says
   so and the window is finite.

## Step 6 — Twilio

1. Point the sender's incoming webhook at `https://api.katha.life/webhook/whatsapp`.
2. `python scripts/verify_whatsapp_sender.py` against production — a real send from the real
   number to your phone.
3. Confirm the signature check passes rather than 403s. If every webhook 403s, `PUBLIC_BASE_URL`
   and the URL Twilio signed disagree — the exact H2 failure the code now guards against.

## Step 7 — Frontend

1. Vercel project on `frontend/`, domain `app.katha.life`, `NEXT_PUBLIC_API_BASE` (or whatever
   `lib/` reads) pointed at `https://api.katha.life`.
2. Log in as yourself and load `/family/onboarding` and `/family` over https before anything
   else touches it.

## Step 8 — Prove it, then let a human near it

1. **Run `python scripts/pilot_rehearsal.py` against the production stack**, before any real
   family exists. It creates a test family, runs two sessions, delivers a card and then
   deletes everything, verifying against the database and the bucket rather than the
   endpoint's return value. It uses the stub adapter, so no messages leave.
2. Re-verify Block Public Access on `katha-media` in the console — still an operator
   attestation, since `katha-app` is denied `GetPublicAccessBlock`.
3. Confirm exactly **one** backend process is running. `docker compose ps`, then check the
   logs say "Scheduler started" once.
4. Only then: Stage B of `docs/SELF_TRIAL.md`, on your own number, on these URLs.

---

## Known gaps you are accepting by going live now

- **No deploy automation.** Deploys are `git pull && docker compose up -d --build` on the box.
  Fine at one box and one operator; write the three commands down anyway.
- **No monitoring.** Nothing tells you the box is down except a family not being called. An
  uptime check against `/health` and log retention are the cheapest insurance available; the
  Sprint 2 ops console assumes something to be hosted *on*, not something that watches it.
- **Single point of failure.** One box, one process, one region. Correct for 10–30 families,
  wrong at 100.
- **Vercel serves the dashboard from outside India.** Story content and audio never leave
  ap-south-1 — they sit in Postgres and S3 behind the API — but the dashboard's rendering
  layer is a US-based vendor. Given `CLAUDE.md`'s stated residency constraint, decide
  deliberately whether that is acceptable and record the decision; don't discover it during
  a customer's due-diligence question.

---

*Owner: Krishnaraj CK · Prerequisite for `docs/SELF_TRIAL.md` Stage B and `docs/PILOT_RUNBOOK.md`*
