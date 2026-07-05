# Katha — Active Implementation Spec

**Phase:** 0 — Infrastructure & Scaffolding  
**Status:** Ready for implementation  
**References:** docs/PLAN.md Phase 0, docs/TECH_DESIGN.md Section 1.2

---

## Goal

Create a working development environment with a runnable Python/FastAPI backend, Next.js frontend, PostgreSQL + pgvector database, and CI pipeline — with zero external API dependencies.

---

## Out of Scope

- No Sarvam, OpenAI, Twilio, or WhatsApp integration in this phase
- No authentication or user management
- No business logic — just scaffolding and plumbing
- No deployment configuration (AWS/Azure) — local dev only

---

## Folder Structure to Create

```
katha/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Pydantic Settings env var loading
│   ├── adapters/                # Empty — populated in Phase 1
│   │   └── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       └── health.py        # GET /health endpoint
│   ├── core/
│   │   └── __init__.py          # Empty — populated in Phase 2
│   ├── models/
│   │   └── __init__.py          # Empty — populated in Phase 3
│   ├── tests/
│   │   ├── __init__.py
│   │   └── test_health.py       # Smoke test for /health
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx             # Root redirect to /family
│   │   └── family/
│   │       └── page.tsx         # Placeholder: "Family Dashboard — Coming Soon"
│   ├── components/
│   │   └── .gitkeep
│   ├── public/
│   ├── package.json
│   ├── tsconfig.json
│   └── next.config.ts
├── docker-compose.yml           # postgres + pgvector + backend + frontend
├── Makefile                     # dev, test, lint, migrate targets
├── .env.example                 # Template — no real secrets
└── .github/
    └── workflows/
        └── ci.yml               # Lint + test on push/PR
```

---

## Implementation Steps

### 1. Backend — FastAPI app

Create `backend/pyproject.toml` with these dependencies:
- `fastapi>=0.115`
- `uvicorn[standard]>=0.30`
- `pydantic-settings>=2.0`
- `sqlalchemy>=2.0`
- `asyncpg>=0.29`
- `alembic>=1.13`
- `pgvector>=0.3`
- `pytest>=8.0`
- `pytest-asyncio>=0.24`
- `httpx>=0.27` (for TestClient)
- `ruff>=0.5` (linting)

`backend/config.py` — use `pydantic-settings` BaseSettings to load:
- `DATABASE_URL` (default: `postgresql+asyncpg://katha:katha@localhost:5432/katha`)
- `ENVIRONMENT` (default: `development`)
- `LOG_LEVEL` (default: `info`)

`backend/main.py`:
- Create FastAPI app with title "Katha API"
- Include health router
- Add CORS middleware (allow all origins in development)
- Log startup message with environment and log level

`backend/api/routes/health.py`:
- `GET /health` → returns `{"status": "ok", "environment": "<env>", "version": "0.1.0"}`

`backend/tests/test_health.py`:
- Use FastAPI TestClient (httpx)
- Assert `GET /health` returns 200 and `status == "ok"`

### 2. Frontend — Next.js app

Scaffold with:
```bash
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --no-src-dir --import-alias "@/*"
```

After scaffolding:
- Replace `app/page.tsx` with a redirect to `/family`
- Create `app/family/page.tsx` with a plain placeholder: heading "Family Dashboard" and subtext "Coming soon."
- Remove the default Next.js boilerplate from `app/globals.css` (keep Tailwind base directives only)
- Confirm `npm run build` passes with no errors

### 3. Database — PostgreSQL + pgvector

`docker-compose.yml`:
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: katha
      POSTGRES_PASSWORD: katha
      POSTGRES_DB: katha
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://katha:katha@db:5432/katha
    depends_on:
      - db
    volumes:
      - ./backend:/app

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    volumes:
      - ./frontend:/app

volumes:
  postgres_data:
```

Add a `backend/Dockerfile`:
- Base image: `python:3.12-slim`
- Install dependencies from `requirements.txt`
- CMD: `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`

Add a `frontend/Dockerfile`:
- Base image: `node:20-alpine`
- Install deps, CMD: `npm run dev`

Set up Alembic for migrations:
```bash
cd backend && alembic init migrations
```
Configure `alembic.ini` and `migrations/env.py` to use `DATABASE_URL` from environment. Create an initial empty migration.

### 4. Makefile

```makefile
.PHONY: dev test lint migrate

dev:
	docker-compose up

test:
	cd backend && pytest tests/ -v

lint:
	cd backend && ruff check . && ruff format --check .
	cd frontend && npm run lint

migrate:
	cd backend && alembic upgrade head
```

### 5. Environment file

`.env.example`:
```
# Copy to .env and fill in values — never commit .env
DATABASE_URL=postgresql+asyncpg://katha:katha@localhost:5432/katha
ENVIRONMENT=development
LOG_LEVEL=info

# Phase 1 — leave blank for now
SARVAM_API_KEY=
OPENAI_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
```

### 6. CI Pipeline

`.github/workflows/ci.yml`:
- Trigger: push and pull_request to any branch
- Jobs:
  - `backend-test`: checkout → setup Python 3.12 → install deps → `ruff check .` → `pytest tests/ -v`
  - `frontend-lint`: checkout → setup Node 20 → `npm ci` → `npm run lint` → `npm run build`
- Both jobs run in parallel

---

## Verification Criteria

All of the following must pass before this phase is complete:

- [ ] `make dev` starts all three services (db, backend, frontend) with no errors
- [ ] `curl http://localhost:8000/health` returns `{"status": "ok", ...}`
- [ ] `http://localhost:3000/family` loads the placeholder dashboard page
- [ ] `make test` passes (`test_health.py` green)
- [ ] `make lint` passes with no errors
- [ ] `make migrate` runs the initial migration with no errors
- [ ] GitHub Actions CI passes on a test push (both jobs green)

---

## Notes for Claude Code

- Commit after each numbered step above using imperative commit messages (e.g. "Add FastAPI health endpoint")
- Do not install any packages not listed in this spec without checking first
- If you hit a version conflict, resolve it and note what changed
- Run `make lint` and `make test` before each commit
