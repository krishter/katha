"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";

import { DomainProgress } from "@/components/DomainProgress";
import { api } from "@/lib/api";

export default function FamilyDashboard() {
  const router = useRouter();
  const { data: stats, error, isLoading } = useSWR("stats", api.getStats);

  // A valid session cookie doesn't guarantee onboarding is finished — e.g. a
  // browser-history entry to /family from before the wizard was completed.
  useEffect(() => {
    if (stats && !stats.onboarding_complete) {
      router.replace("/family/onboarding");
    }
  }, [stats, router]);

  if (isLoading) {
    return <main id="main" className="p-8 text-ink-mid">Loading...</main>;
  }
  if (error || !stats) {
    return (
      <main id="main" className="p-8 text-danger">
        Couldn&apos;t load the dashboard. Please try again.
      </main>
    );
  }
  if (!stats.onboarding_complete) {
    return <main id="main" className="p-8 text-ink-mid">Redirecting...</main>;
  }

  return (
    <main id="main" className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="font-display text-2xl font-semibold text-ink">
        {stats.user_name}&apos;s Stories
      </h1>

      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-border bg-surface p-4">
          <p className="text-2xl font-semibold text-ink">
            {stats.total_sessions}
          </p>
          <p className="text-sm text-ink-mid">Sessions</p>
        </div>
        <div className="rounded-lg border border-border bg-surface p-4">
          <p className="text-2xl font-semibold text-ink">
            {stats.total_story_atoms}
          </p>
          <p className="text-sm text-ink-mid">Stories captured</p>
        </div>
        <div className="rounded-lg border border-border bg-surface p-4">
          <p className="text-2xl font-semibold text-ink">
            {stats.domains_covered} / 8
          </p>
          <p className="text-sm text-ink-mid">Life chapters</p>
        </div>
      </div>

      {stats.latest_card_url && (
        <div className="mt-8">
          <h2 className="font-display text-lg font-semibold text-ink">Latest memory</h2>
          {/* eslint-disable-next-line @next/next/no-img-element -- S3 host is env-configured, not a fixed domain to allowlist for next/image */}
          <img
            src={stats.latest_card_url}
            alt="Latest memory card"
            className="mt-3 w-full max-w-md rounded-lg border border-border"
          />
        </div>
      )}

      <div className="mt-8">
        <h2 className="font-display text-lg font-semibold text-ink">
          Progress across life chapters
        </h2>
        <div className="mt-3">
          <DomainProgress domains={stats.domain_breakdown} />
        </div>
      </div>
    </main>
  );
}
