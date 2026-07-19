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
    return <main className="p-8 text-[#6B5B4E]">Loading...</main>;
  }
  if (error || !stats) {
    return (
      <main className="p-8 text-red-600">
        Couldn&apos;t load the dashboard. Please try again.
      </main>
    );
  }
  if (!stats.onboarding_complete) {
    return <main className="p-8 text-[#6B5B4E]">Redirecting...</main>;
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#2C2C2C]">
        {stats.user_name}&apos;s Stories
      </h1>

      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-[#E8DDD4] bg-white p-4">
          <p className="text-2xl font-semibold text-[#2C2C2C]">
            {stats.total_sessions}
          </p>
          <p className="text-sm text-[#6B5B4E]">Sessions</p>
        </div>
        <div className="rounded-lg border border-[#E8DDD4] bg-white p-4">
          <p className="text-2xl font-semibold text-[#2C2C2C]">
            {stats.total_story_atoms}
          </p>
          <p className="text-sm text-[#6B5B4E]">Stories captured</p>
        </div>
        <div className="rounded-lg border border-[#E8DDD4] bg-white p-4">
          <p className="text-2xl font-semibold text-[#2C2C2C]">
            {stats.domains_covered} / 8
          </p>
          <p className="text-sm text-[#6B5B4E]">Life chapters</p>
        </div>
      </div>

      {stats.latest_card_url && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold text-[#2C2C2C]">Latest memory</h2>
          {/* eslint-disable-next-line @next/next/no-img-element -- S3 host is env-configured, not a fixed domain to allowlist for next/image */}
          <img
            src={stats.latest_card_url}
            alt="Latest memory card"
            className="mt-3 w-full max-w-md rounded-lg border border-[#E8DDD4]"
          />
        </div>
      )}

      <div className="mt-8">
        <h2 className="text-lg font-semibold text-[#2C2C2C]">
          Progress across life chapters
        </h2>
        <div className="mt-3">
          <DomainProgress domains={stats.domain_breakdown} />
        </div>
      </div>
    </main>
  );
}
