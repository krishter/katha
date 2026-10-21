export interface DomainBreakdownEntry {
  domain_id: string;
  domain_label: string;
  story_count: number;
  target: number;
}

export type ParentConsentStatus =
  | "not_asked"
  | "awaiting"
  // She said yes once and Katha asked her to confirm; only a second yes
  // is recorded.
  | "confirming"
  | "granted"
  | "declined"
  | "halted";

export interface Stats {
  user_id: string;
  parent_consent_status: ParentConsentStatus;
  parent_consented_at: string | null;
  parent_whatsapp_link: string;
  user_name: string;
  // Memory cards wait on WhatsApp until the child sends Katha a code from
  // this number, proving it is theirs.
  family_whatsapp_number: string | null;
  family_number_confirmed: boolean;
  total_sessions: number;
  total_story_atoms: number;
  total_memory_cards: number;
  domains_covered: number;
  domain_breakdown: DomainBreakdownEntry[];
  latest_card_url: string | null;
  plan: string;
  // "" means not stated. Editable from settings because every profile
  // created before the field existed has it empty.
  pronouns: string;
  session_count: number;
  session_limit: number;
  onboarding_complete: boolean;
}

export interface StoryAtomResponse {
  id: string;
  domain: string;
  domain_label: string;
  title: string | null;
  narrative: string;
  who: string[];
  what: string | null;
  when_approx: string | null;
  where_approx: string | null;
  why: string | null;
  completeness_score: number;
  verbatim_quote: string | null;
  created_at: string;
}

export interface StoriesResponse {
  stories: StoryAtomResponse[];
  total: number;
  page: number;
  pages: number;
}

export interface MemoryCardItem {
  id: string;
  verbatim_quote: string;
  domain: string;
  image_url: string;
  created_at: string;
}

// A "Did you know?" card the parent heard read back and agreed to share
// (docs/specs/CONVERSATION_CARDS.md). Only sent cards are returned.
export interface ConversationCardItem {
  id: string;
  title: string;
  hook: string;
  excerpt: string;
  original_quote: string | null;
  original_quote_gloss: string | null;
  call_prompt: string;
  image_url: string | null;
  sent_at: string | null;
  // The language the parent spoke, when the card is a translation (D8).
  translated_from: string | null;
}

export interface CardsResponse {
  cards: MemoryCardItem[];
  total: number;
  // Absent from a backend older than conversation cards. Not paginated:
  // every sent card comes back on every page.
  conversation_cards?: ConversationCardItem[];
}

export interface ExportBundle {
  exported_at: string;
  schema_version: string;
  audio_included: boolean;
  profile: {
    name: string | null;
    preferred_language: string | null;
    onboarding_context: string | null;
  };
  sessions: unknown[];
  stories: unknown[];
  memory_cards: unknown[];
}

export interface OnboardingStartResponse {
  status: "new" | "existing" | "incomplete";
  message?: string;
  // False only when the mail transport refused the message. It says
  // nothing about whether the address is registered, so it is safe to show
  // the visitor — and showing it is the point: without it the UI claims
  // "check your email" even when nothing was sent.
  link_sent: boolean;
}

export interface OnboardingProfilePayload {
  [key: string]: string;
  parent_name: string;
  whatsapp_number: string;
  family_whatsapp_number: string;
  preferred_language: string;
  // Katha's persona: with the language it picks the voice the parent hears.
  katha_gender: "female" | "male";
  session_time: string;
  // "" means not stated. The backend stores that as-is and the prompt then
  // avoids gendered wording rather than inferring any.
  pronouns: string;
  onboarding_context: string;
  // What the parent calls the child. Katha's welcome names them.
  child_name: string;
}

export interface OnboardingConsentResponse {
  status: "complete";
  parent_name: string;
  session_time: string;
  parent_whatsapp_link: string;
  // Tapped by the child, from their own WhatsApp, to confirm the number
  // memory cards go to.
  family_confirm_link: string;
}

// A non-2xx answer, with the backend's own words when it gave any.
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string | null,
  ) {
    super(`API error: ${status}`);
  }
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    credentials: "include",
  });
  if (res.status === 401) {
    if (typeof window !== "undefined") {
      window.location.href = "/family/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Not JSON; the status is all there is.
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

function storiesQuery(domain?: string, page = 1): string {
  const params: Record<string, string> = { page: String(page) };
  if (domain && domain !== "all") params.domain = domain;
  return new URLSearchParams(params).toString();
}

/**
 * Lightweight "am I logged in" check that deliberately bypasses apiFetch's
 * global 401 -> redirect-to-login behavior. The onboarding page needs this
 * distinction: an unauthenticated visitor there should see step 1 (email
 * entry), not get bounced to the login screen.
 */
async function isAuthenticated(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/family/stats`, { credentials: "include" });
    return res.ok;
  } catch {
    return false;
  }
}

export const api = {
  getStats: () => apiFetch<Stats>("/family/stats"),

  listStories: (domain?: string, page = 1) =>
    apiFetch<StoriesResponse>(`/family/stories?${storiesQuery(domain, page)}`),

  getStory: (id: string) => apiFetch<StoryAtomResponse>(`/family/stories/${id}`),

  listCards: (page = 1) =>
    apiFetch<CardsResponse>(`/family/cards?page=${page}`),

  requestMagicLink: (email: string) =>
    apiFetch<{ message: string }>("/auth/magic-link", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ email }),
    }),

  // Logout redirects cross-origin (backend -> frontend login page), which a
  // plain fetch can't follow meaningfully. Fire the request so the backend
  // clears the cookie, then let the caller navigate the browser itself.
  logout: () =>
    fetch(`${BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
      redirect: "manual",
    }),

  isAuthenticated,

  exportData: () => apiFetch<ExportBundle>("/family/export"),

  // Scoped to the caller's own JWT — there is no user_id to pass. The
  // backend clears the katha_token cookie itself via delete_cookie, so
  // callers must NOT also call logout(); the two race.
  deleteAllData: (userId: string) =>
    apiFetch<{ status: string; message: string }>(`/user/${userId}`, {
      method: "DELETE",
    }),

  startOnboarding: (email: string) =>
    apiFetch<OnboardingStartResponse>("/onboarding/start", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ email }),
    }),

  updatePronouns: (pronouns: string) =>
    apiFetch<{ status: string; pronouns: string }>("/family/profile", {
      method: "PATCH",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ pronouns }),
    }),

  submitProfile: (payload: OnboardingProfilePayload) =>
    apiFetch<{ status: string }>("/onboarding/profile", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(payload),
    }),

  // A fresh confirm link. The code is stored hashed, so an earlier one
  // cannot be shown again; this replaces it.
  getFamilyConfirmLink: () =>
    apiFetch<{ link: string }>("/family/family-number/confirm-link", {
      method: "POST",
    }),

  submitConsent: (consentGiven: boolean) =>
    apiFetch<OnboardingConsentResponse>("/onboarding/consent", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ consent_given: String(consentGiven) }),
    }),
};
