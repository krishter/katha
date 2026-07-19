# Katha — Active Implementation Spec

**Phase:** 6 — Family Dashboard  
**Status:** Ready for implementation  
**References:** docs/PLAN.md Phase 6, docs/PRD.md Section 7

---

## Goal

Adult children can log in to `katha.life/family` and browse everything Katha has captured: stories by life domain, memory cards from each session, and overall progress across the 8-domain arc. Read-only for MVP — no editing, no commenting.

By the end of this phase, a family member can enter their email, click a magic link, and see their parent's story archive.

---

## Out of Scope

- Story editing or commenting (post-MVP)
- Audio playback of original voice notes (post-MVP)
- Multiple family members per account (Phase 7 handles multi-user access)
- Sharing / export (post-MVP)

---

## New Dependencies

**Backend:**
```
python-jose[cryptography]   # JWT signing and verification
```

**Frontend:**
```
swr                         # Data fetching with caching (already in many Next.js setups)
```

Email delivery reuses AWS SES via `boto3` (already in `requirements.txt`).

---

## New Environment Variables

```
JWT_SECRET=                         # Long random string — generate with: openssl rand -hex 32
JWT_EXPIRE_DAYS=7
MAGIC_LINK_EXPIRE_MINUTES=15
SES_FROM_EMAIL=noreply@katha.life   # Must be verified in AWS SES
APP_BASE_URL=https://katha.life     # Used to build magic link URLs; use http://localhost:3000 in dev
```

Add to `.env.example` and `backend/config.py`.

---

## DB Migrations Required

```sql
CREATE TABLE family_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR NOT NULL UNIQUE,
    user_id VARCHAR NOT NULL,              -- Which elderly user this account tracks
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE magic_link_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR NOT NULL,
    token VARCHAR NOT NULL UNIQUE,         -- Random hex token
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast token lookup
CREATE INDEX idx_magic_link_token ON magic_link_tokens(token) WHERE used = FALSE;
```

---

## Files to Create / Modify

```
backend/
├── api/routes/
│   ├── auth.py                     # POST /auth/magic-link, GET /auth/verify (NEW)
│   └── family.py                   # GET /family/stats, /stories, /cards (NEW)
├── core/
│   └── auth.py                     # JWT issue/verify, magic link send (NEW)
├── models/
│   ├── family_account.py           # SQLAlchemy model (NEW)
│   └── magic_link_token.py         # SQLAlchemy model (NEW)

frontend/
├── app/
│   ├── family/
│   │   ├── page.tsx                # Dashboard home (NEW)
│   │   ├── layout.tsx              # Auth guard + nav shell (NEW)
│   │   ├── login/
│   │   │   └── page.tsx            # Email entry (NEW)
│   │   ├── auth/
│   │   │   └── verify/
│   │   │       └── page.tsx        # Magic link landing (NEW)
│   │   ├── stories/
│   │   │   ├── page.tsx            # Story browser (NEW)
│   │   │   └── [id]/
│   │   │       └── page.tsx        # Single story view (NEW)
│   │   └── cards/
│   │       └── page.tsx            # Memory card gallery (NEW)
├── components/
│   ├── DomainProgress.tsx          # 8-domain coverage bar (NEW)
│   ├── StoryCard.tsx               # Single story atom card (NEW)
│   └── MemoryCardGallery.tsx       # Image grid (NEW)
├── lib/
│   └── api.ts                      # Typed fetch helpers for backend routes (NEW)
└── middleware.ts                   # Next.js auth middleware (NEW)

tests/
└── test_auth.py                    # Backend auth tests (NEW)
└── test_family_api.py              # Backend family route tests (NEW)
```

---

## Implementation Steps

### Step 1 — DB models + migration

Create `FamilyAccount` and `MagicLinkToken` SQLAlchemy models matching the schemas above. Run `make migrate`.

**Seed one family account for testing** (add to a dev seed script or run manually):
```sql
INSERT INTO family_accounts (email, user_id) VALUES ('test@katha.life', 'test_user_wa');
```

---

### Step 2 — `backend/core/auth.py`

```python
# JWT
def create_jwt(email: str, user_id: str) -> str:
    """
    Issue a signed JWT.
    Payload: {sub: email, user_id: user_id, exp: now + JWT_EXPIRE_DAYS}
    Sign with JWT_SECRET using HS256.
    """

def verify_jwt(token: str) -> dict:
    """
    Decode and verify JWT. Raise HTTPException(401) if expired or invalid.
    Return payload dict.
    """

def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency. Extract JWT from httpOnly cookie named 'katha_token'.
    Call verify_jwt. Return payload.
    Raise HTTPException(401) if missing or invalid.
    """

# Magic link
async def send_magic_link(email: str, db) -> None:
    """
    1. Verify email exists in family_accounts. Raise HTTPException(404) if not.
       (Don't reveal whether the email exists — return 200 either way to prevent enumeration)
    2. Generate token: secrets.token_hex(32)
    3. Insert into magic_link_tokens with expires_at = now() + MAGIC_LINK_EXPIRE_MINUTES
    4. Build URL: {APP_BASE_URL}/family/auth/verify?token={token}
    5. Send email via AWS SES:
       To: email
       From: SES_FROM_EMAIL
       Subject: "Your Katha login link"
       Body (plain text + HTML):
         "Click to log in to Katha (link expires in 15 minutes):
          {url}
          If you didn't request this, ignore this email."
    """

async def verify_magic_link(token: str, db) -> tuple[str, str]:
    """
    1. Look up token in magic_link_tokens WHERE used=FALSE AND expires_at > NOW()
    2. If not found: raise HTTPException(400, "Invalid or expired link")
    3. Mark token used=TRUE
    4. Look up family_account by email
    5. Return (email, user_id)
    """
```

**SES email helper:**
```python
def send_email_ses(to: str, subject: str, body_text: str, body_html: str) -> None:
    """Send via boto3 SES client. Region: ap-south-1 (data residency)."""
```

**Test (`test_auth.py`):**
- Assert `create_jwt` + `verify_jwt` round-trip correctly
- Assert `verify_jwt` raises 401 for expired token (mock time)
- Assert `verify_jwt` raises 401 for tampered token
- Assert `send_magic_link` returns 200 for unknown email (enumeration protection)
- Assert `send_magic_link` calls SES for known email (mock SES)
- Assert `verify_magic_link` raises 400 for expired token
- Assert `verify_magic_link` marks token as used and returns email + user_id

---

### Step 3 — `backend/api/routes/auth.py`

```python
@router.post("/auth/magic-link")
async def request_magic_link(email: str = Form(...), db = Depends(get_db)):
    """
    Always return 200 (enumeration protection).
    Call auth.send_magic_link(email, db) — if family_account not found, log silently and return 200.
    Response: {"message": "If that email is registered, a login link is on its way."}
    """

@router.get("/auth/verify")
async def verify_magic_link(token: str, response: Response, db = Depends(get_db)):
    """
    1. Call auth.verify_magic_link(token, db) → (email, user_id)
    2. Call auth.create_jwt(email, user_id) → jwt_token
    3. Set httpOnly cookie:
       response.set_cookie(
           key="katha_token",
           value=jwt_token,
           httponly=True,
           secure=True,        # False in dev (HTTP)
           samesite="lax",
           max_age=60*60*24*JWT_EXPIRE_DAYS,
       )
    4. Redirect to /family (302)
    """

@router.post("/auth/logout")
async def logout(response: Response):
    """Delete the katha_token cookie. Redirect to /family/login."""
```

---

### Step 4 — `backend/api/routes/family.py`

All routes require `current_user = Depends(auth.get_current_user)`. The `user_id` for all queries comes from the JWT payload, not the URL — family members can only see their linked elderly user's data.

```python
@router.get("/family/stats")
async def get_stats(current_user = Depends(get_current_user), db = Depends(get_db)):
    """
    Returns:
    {
      "user_name": str,
      "total_sessions": int,
      "total_story_atoms": int,
      "domains_covered": int,        # Domains with at least 1 story atom
      "domain_breakdown": [
        {"domain_id": str, "domain_label": str, "story_count": int, "target": int}
      ],
      "latest_card_url": str | null  # Most recent memory card image URL
    }
    """

@router.get("/family/stories")
async def list_stories(
    domain: str | None = None,    # Filter by domain ID
    page: int = 1,
    limit: int = 20,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    Paginated story atoms for this user.
    If domain provided, filter by domain.
    Order: created_at DESC.
    Returns:
    {
      "stories": [StoryAtomResponse],
      "total": int,
      "page": int,
      "pages": int
    }
    """

@router.get("/family/stories/{story_id}")
async def get_story(
    story_id: str,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """Single story atom. Verify user_id matches before returning."""

@router.get("/family/cards")
async def list_cards(
    page: int = 1,
    limit: int = 20,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    Memory cards for this user, newest first.
    Returns image_public_url directly (images are already public on S3).
    {
      "cards": [
        {"id": str, "verbatim_quote": str, "domain": str, "image_url": str, "created_at": str}
      ],
      "total": int
    }
    """
```

**Pydantic response model for `StoryAtomResponse`:**
```python
class StoryAtomResponse(BaseModel):
    id: str
    domain: str
    domain_label: str        # Human-readable domain name (from domains.get_domain)
    title: str | None
    narrative: str
    who: list[str]
    what: str | None
    when_approx: str | None
    where_approx: str | None
    why: str | None
    completeness_score: int
    verbatim_quote: str | None
    created_at: str          # ISO 8601
```

**Test (`test_family_api.py`):**
- Mock DB, seed test data
- Assert `/family/stats` returns correct session count and domain breakdown
- Assert `/family/stories` returns paginated results; domain filter works
- Assert `/family/stories/{id}` returns 404 for a story belonging to a different user (auth isolation)
- Assert all routes return 401 without a valid JWT cookie

---

### Step 5 — `frontend/middleware.ts`

```typescript
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function middleware(request: NextRequest) {
  const token = request.cookies.get('katha_token')
  const isAuthRoute = request.nextUrl.pathname.startsWith('/family/login') ||
                      request.nextUrl.pathname.startsWith('/family/auth')

  if (!token && !isAuthRoute) {
    return NextResponse.redirect(new URL('/family/login', request.url))
  }
  return NextResponse.next()
}

export const config = {
  matcher: ['/family/:path*'],
}
```

---

### Step 6 — `frontend/lib/api.ts`

Typed fetch helpers. All requests send cookies (`credentials: 'include'`). On 401, redirect to login.

```typescript
const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    credentials: 'include',
  })
  if (res.status === 401) {
    window.location.href = '/family/login'
    throw new Error('Unauthorized')
  }
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export const api = {
  getStats: () => apiFetch<Stats>('/family/stats'),
  listStories: (domain?: string, page = 1) =>
    apiFetch<StoriesResponse>(`/family/stories?${new URLSearchParams({ ...(domain && { domain }), page: String(page) })}`),
  getStory: (id: string) => apiFetch<StoryAtomResponse>(`/family/stories/${id}`),
  listCards: (page = 1) => apiFetch<CardsResponse>(`/family/cards?page=${page}`),
}
```

Add TypeScript types matching the backend Pydantic models.

---

### Step 7 — Frontend pages and components

**`frontend/app/family/login/page.tsx`:**
- Single email input field + "Send login link" button
- POST to `/auth/magic-link`
- After submit: show "Check your email for a login link" message
- Clean, minimal design — centered card on warm cream background (`#FDF6EC`) consistent with memory card branding

**`frontend/app/family/auth/verify/page.tsx`:**
- On mount: extract `token` from URL query params, call `GET /auth/verify?token={token}` (via redirect, the browser hits this URL directly)
- Show loading state ("Logging you in...")
- The backend sets the cookie and redirects to `/family` — no client-side JWT handling needed
- If error (400): show "This link has expired. Request a new one." with link back to login

**`frontend/app/family/layout.tsx`:**
- Navigation bar: "Stories" | "Memory Cards" | "Logout"
- Logout calls `POST /auth/logout` then redirects to `/family/login`
- Wraps all authenticated family pages

**`frontend/app/family/page.tsx` — Dashboard home:**
- Fetch stats via `api.getStats()`
- Show: user's name, total sessions, total stories captured
- `<DomainProgress>` component showing all 8 domains
- Latest memory card image (if any) as a hero image

**`frontend/app/family/stories/page.tsx` — Story browser:**
- Domain filter tabs (one per domain + "All")
- `<StoryCard>` for each story atom
- Pagination controls
- Uses `swr` for data fetching with `api.listStories(selectedDomain, page)`

**`frontend/app/family/stories/[id]/page.tsx` — Single story:**
- Full story atom detail: narrative, 5W breakdown, verbatim quote (styled as blockquote), domain badge, date
- Back button to story browser

**`frontend/app/family/cards/page.tsx` — Memory card gallery:**
- `<MemoryCardGallery>` component
- Click on card → shows full-size image with download button (`<a href={url} download>`)

---

### Step 8 — Frontend components

**`frontend/components/DomainProgress.tsx`:**
```typescript
// Props: { domains: Array<{domain_id, domain_label, story_count, target}> }
// Render: 8 rows, each with domain name, progress bar (story_count / target), and count
// Color: filled segments in #C8956C (terracotta), empty in #E8DDD4
// Accessibility: role="progressbar", aria-valuenow, aria-valuemax on each bar
```

**`frontend/components/StoryCard.tsx`:**
```typescript
// Props: StoryAtomResponse
// Render: domain badge, title or first 80 chars of narrative, verbatim quote (if present),
//         completeness score dots (5 dots, filled = score), date
// Clickable → links to /family/stories/{id}
```

**`frontend/components/MemoryCardGallery.tsx`:**
```typescript
// Props: { cards: Array<{id, verbatim_quote, domain, image_url, created_at}> }
// Render: responsive CSS grid (3 cols desktop, 2 cols tablet, 1 col mobile)
// Each card: image thumbnail + quote preview on hover/focus
// Lazy-load images (loading="lazy")
```

---

## Verification Criteria

- [ ] `make migrate` — `family_accounts` and `magic_link_tokens` tables created
- [ ] `make test` — `test_auth.py` and `test_family_api.py` pass
- [ ] `make lint` — backend and frontend both clean (`eslint` + `tsc --noEmit`)
- [ ] Manual login flow — request magic link → receive email → click link → land on dashboard
- [ ] Story and card display — seed DB and verify all pages render correctly
- [ ] Lighthouse accessibility score ≥ 85 (run in Chrome DevTools on the dashboard home)
- [ ] Auth isolation — verify `/family/stories/{id}` returns 404 for a story belonging to a different user

**Seed DB for frontend testing:**
```sql
-- Seed a family account
INSERT INTO family_accounts (email, user_id) VALUES ('dev@katha.life', 'test_user_wa');

-- If no real story atoms exist, seed some test data
INSERT INTO story_atoms (session_id, user_id, domain, title, narrative, verbatim_quote, completeness_score, who, what, when_approx, where_approx, why)
VALUES 
  ('00000000-0000-0000-0000-000000000001', 'test_user_wa', 'childhood', 'The Street in Madurai',
   'User described the jasmine-scented street outside their childhood home.', 
   'The street always smelled of jasmine and filter coffee in the mornings.', 4,
   ARRAY['father', 'neighbours'], 'Daily street life', 'circa 1955', 'Madurai', 'Core childhood memory'),
  ('00000000-0000-0000-0000-000000000001', 'test_user_wa', 'career', 'First Day as a Teacher',
   'User described walking into their first classroom nervously.', 
   'I thought — what if they can tell I don''t know what I''m doing?', 3,
   ARRAY['students'], 'First teaching day', '1971', 'Government school, Madurai', 'Career beginning');
```

---

## Notes for Claude Code

- **httpOnly cookie vs. localStorage:** The JWT must be in an httpOnly cookie — never localStorage. This is enforced by the FastAPI route setting the cookie directly, never passing the token to the frontend JS.
- **SES in dev:** In development, set `SES_MOCK=true` in `.env` and have `send_email_ses` print the magic link to the console instead of actually sending. Add this config flag to `backend/config.py`.
- **CORS:** The FastAPI app must allow `http://localhost:3000` as an origin with `allow_credentials=True` (needed for cookie-based auth). Update `backend/main.py` CORS middleware.
- **`NEXT_PUBLIC_API_URL`:** Add to `frontend/.env.local` — `NEXT_PUBLIC_API_URL=http://localhost:8000`
- **Accessibility targets:** Use semantic HTML throughout — `<main>`, `<nav>`, `<article>` for story cards. All images need `alt` text. The `DomainProgress` bars need ARIA attributes. This matters because some adult children may use screen readers.
- **Branch name:** `feature/phase6-family-dashboard`
- **Commit after each numbered step.** Run `make lint` (both backend and frontend) and `make test` before each commit.
