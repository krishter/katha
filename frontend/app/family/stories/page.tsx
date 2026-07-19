"use client";

import { useState } from "react";
import useSWR from "swr";

import { StoryCard } from "@/components/StoryCard";
import { api } from "@/lib/api";

export default function StoriesPage() {
  const [selectedDomain, setSelectedDomain] = useState<string>("all");
  const [page, setPage] = useState(1);

  const { data: stats } = useSWR("stats", api.getStats);
  const { data, error, isLoading } = useSWR(["stories", selectedDomain, page], () =>
    api.listStories(selectedDomain, page)
  );

  function selectDomain(domainId: string) {
    setSelectedDomain(domainId);
    setPage(1);
  }

  const tabClass = (active: boolean) =>
    `rounded-full px-4 py-1.5 text-sm font-medium transition ${
      active
        ? "bg-[#C8956C] text-white"
        : "border border-[#E8DDD4] bg-white text-[#6B5B4E]"
    }`;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#2C2C2C]">Stories</h1>

      <nav aria-label="Filter by life chapter" className="mt-6 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => selectDomain("all")}
          aria-pressed={selectedDomain === "all"}
          className={tabClass(selectedDomain === "all")}
        >
          All
        </button>
        {stats?.domain_breakdown.map((domain) => (
          <button
            key={domain.domain_id}
            type="button"
            onClick={() => selectDomain(domain.domain_id)}
            aria-pressed={selectedDomain === domain.domain_id}
            className={tabClass(selectedDomain === domain.domain_id)}
          >
            {domain.domain_label}
          </button>
        ))}
      </nav>

      <div className="mt-6 flex flex-col gap-4">
        {isLoading && <p className="text-[#6B5B4E]">Loading...</p>}
        {error && <p className="text-red-600">Couldn&apos;t load stories.</p>}
        {data && data.stories.length === 0 && (
          <p className="text-[#6B5B4E]">No stories yet in this chapter.</p>
        )}
        {data?.stories.map((story) => <StoryCard key={story.id} story={story} />)}
      </div>

      {data && data.pages > 1 && (
        <div className="mt-8 flex items-center justify-center gap-4">
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded-lg border border-[#E8DDD4] px-4 py-2 text-sm text-[#2C2C2C] disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-sm text-[#6B5B4E]">
            Page {data.page} of {data.pages}
          </span>
          <button
            type="button"
            onClick={() => setPage((p) => Math.min(data.pages, p + 1))}
            disabled={page >= data.pages}
            className="rounded-lg border border-[#E8DDD4] px-4 py-2 text-sm text-[#2C2C2C] disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </main>
  );
}
