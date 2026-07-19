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
      await api.requestMagicLink(email);
      setSubmitted(true);
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#FDF6EC] p-8">
      <div className="w-full max-w-sm rounded-xl bg-white p-8 shadow-sm">
        <h1 className="text-2xl font-semibold text-[#2C2C2C]">Welcome to Katha</h1>
        <p className="mt-2 text-sm text-[#6B5B4E]">
          Enter your email and we&apos;ll send you a login link.
        </p>

        {submitted ? (
          <p className="mt-6 rounded-lg bg-[#FDF6EC] p-4 text-sm text-[#2C2C2C]">
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
              className="rounded-lg border border-[#E8DDD4] px-4 py-2 text-[#2C2C2C] focus:border-[#C8956C] focus:outline-none"
            />
            <button
              type="submit"
              disabled={loading}
              className="rounded-lg bg-[#C8956C] px-4 py-2 font-medium text-white transition hover:bg-[#b17f57] disabled:opacity-60"
            >
              {loading ? "Sending..." : "Send login link"}
            </button>
            {error && <p className="text-sm text-red-600">{error}</p>}
          </form>
        )}
      </div>
    </main>
  );
}
