export interface DomainBreakdownEntry {
  domain_id: string;
  domain_label: string;
  story_count: number;
  target: number;
}

export interface Stats {
  user_name: string;
  total_sessions: number;
  total_story_atoms: number;
  domains_covered: number;
  domain_breakdown: DomainBreakdownEntry[];
  latest_card_url: string | null;
  plan: string;
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

export interface CardsResponse {
  cards: MemoryCardItem[];
  total: number;
}

export interface OnboardingStartResponse {
  status: "new" | "existing" | "incomplete";
  message?: string;
}

export interface OnboardingProfilePayload {
  [key: string]: string;
  parent_name: string;
  whatsapp_number: string;
  family_whatsapp_number: string;
  preferred_language: string;
  session_time: string;
  onboarding_context: string;
}

export interface OnboardingConsentResponse {
  status: "complete";
  parent_name: string;
  session_time: string;
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
  if (!res.ok) throw new Error(`API error: ${res.status}`);
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

  startOnboarding: (email: string) =>
    apiFetch<OnboardingStartResponse>("/onboarding/start", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ email }),
    }),

  submitProfile: (payload: OnboardingProfilePayload) =>
    apiFetch<{ status: string }>("/onboarding/profile", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(payload),
    }),

  submitConsent: (consentGiven: boolean) =>
    apiFetch<OnboardingConsentResponse>("/onboarding/consent", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ consent_given: String(consentGiven) }),
    }),
};
