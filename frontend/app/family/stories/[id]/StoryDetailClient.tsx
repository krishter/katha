"use client";

import Link from "next/link";
import useSWR from "swr";

import { api } from "@/lib/api";

export function StoryDetailClient({ id }: { id: string }) {
  const { data: story, error, isLoading } = useSWR(["story", id], () =>
    api.getStory(id)
  );

  if (isLoading) {
    return <main id="main" className="p-8 text-ink-mid">Loading...</main>;
  }
  if (error || !story) {
    return <main id="main" className="p-8 text-danger">Story not found.</main>;
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
    <main id="main" className="mx-auto max-w-2xl px-6 py-10">
      <Link href="/family/stories" className="text-sm text-saffron-ink hover:underline">
        ← Back to stories
      </Link>

      <article className="mt-4">
        <span className="inline-block rounded-full bg-page px-3 py-1 text-xs font-medium text-ink-mid">
          {story.domain_label}
        </span>
        <h1 className="font-display mt-3 text-2xl font-semibold text-ink">
          {story.title || "Untitled story"}
        </h1>
        <time dateTime={story.created_at} className="mt-1 block text-sm text-ink-mid">
          {date}
        </time>

        {story.verbatim_quote && (
          <blockquote className="mt-6 font-display border-l-4 border-saffron pl-4 text-lg italic text-ink">
            &ldquo;{story.verbatim_quote}&rdquo;
          </blockquote>
        )}

        <p className="mt-6 whitespace-pre-wrap text-ink">{story.narrative}</p>

        {details.length > 0 && (
          <dl className="mt-8 grid grid-cols-2 gap-4 text-sm">
            {details.map(([label, value]) => (
              <div key={label} className={label === "Why it mattered" ? "col-span-2" : ""}>
                <dt className="font-medium text-ink-mid">{label}</dt>
                <dd className="text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </article>
    </main>
  );
}
