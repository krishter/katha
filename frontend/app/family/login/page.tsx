"use client";

import { useState } from "react";

import { api } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      // /onboarding/start (not /auth/magic-link) — this page is the
      // universal email entry point, reachable by both new and returning
      // visitors. /auth/magic-link only sends a link for emails that
      // already have a family_account, so a first-time visitor here would
      // see "check your email" and nothing would ever arrive.
      // /onboarding/start creates the account (and sends the link) for a
      // new email, and still sends a normal login link for an existing one.
      await api.startOnboarding(email);
      setSubmitted(true);
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main id="main" className="flex min-h-screen items-center justify-center bg-page p-8">
      <div className="w-full max-w-sm rounded-xl bg-surface p-8 shadow-sm">
        <h1 className="font-display text-2xl font-semibold text-ink">Welcome to Katha</h1>
        <p className="mt-2 text-sm text-ink-mid">
          Enter your email and we&apos;ll send you a login link.
        </p>

        {submitted ? (
          <p className="mt-6 rounded-lg bg-page p-4 text-sm text-ink">
            Check your email for a login link.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-3">
            <label htmlFor="email" className="sr-only">
              Email address
            </label>
            <input
              id="email"
              name="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="rounded-lg border border-border px-4 py-2 text-ink focus:border-saffron"
            />
            <button
              type="submit"
              disabled={loading}
              className="rounded-lg bg-saffron px-4 py-2 font-medium text-indigo transition hover:bg-gold disabled:opacity-60"
            >
              {loading ? "Sending..." : "Send login link"}
            </button>
            {error && <p className="text-sm text-danger">{error}</p>}
          </form>
        )}
      </div>
    </main>
  );
}
