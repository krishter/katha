"use client";

import Link from "next/link";
import useSWR from "swr";

import { api } from "@/lib/api";

export function StoryDetailClient({ id }: { id: string }) {
  const { data: story, error, isLoading } = useSWR(["story", id], () =>
    api.getStory(id)
  );

  if (isLoading) {
    return <main className="p-8 text-[#6B5B4E]">Loading...</main>;
  }
  if (error || !story) {
    return <main className="p-8 text-red-600">Story not found.</main>;
  }

  const date = new Date(story.created_at).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  const details: Array<[string, string]> = [
    ["Who", story.who.join(", ")],
    ["What", story.what ?? ""],
    ["When", story.when_approx ?? ""],
    ["Where", story.where_approx ?? ""],
    ["Why it mattered", story.why ?? ""],
  ].filter(([, value]) => value) as Array<[string, string]>;

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <Link href="/family/stories" className="text-sm text-[#C8956C] hover:underline">
        ← Back to stories
      </Link>

      <article className="mt-4">
        <span className="inline-block rounded-full bg-[#FDF6EC] px-3 py-1 text-xs font-medium text-[#6B5B4E]">
          {story.domain_label}
        </span>
        <h1 className="mt-3 text-2xl font-semibold text-[#2C2C2C]">
          {story.title || "Untitled story"}
        </h1>
        <time dateTime={story.created_at} className="mt-1 block text-sm text-[#6B5B4E]">
          {date}
        </time>

        {story.verbatim_quote && (
          <blockquote className="mt-6 border-l-4 border-[#C8956C] pl-4 text-lg italic text-[#2C2C2C]">
            &ldquo;{story.verbatim_quote}&rdquo;
          </blockquote>
        )}

        <p className="mt-6 whitespace-pre-wrap text-[#2C2C2C]">{story.narrative}</p>

        {details.length > 0 && (
          <dl className="mt-8 grid grid-cols-2 gap-4 text-sm">
            {details.map(([label, value]) => (
              <div key={label} className={label === "Why it mattered" ? "col-span-2" : ""}>
                <dt className="font-medium text-[#6B5B4E]">{label}</dt>
                <dd className="text-[#2C2C2C]">{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </article>
    </main>
  );
}
