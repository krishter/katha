import Link from "next/link";

import type { StoryAtomResponse } from "@/lib/api";

interface StoryCardProps {
  story: StoryAtomResponse;
}

export function StoryCard({ story }: StoryCardProps) {
  const title = story.title || story.narrative.slice(0, 80);
  const date = new Date(story.created_at).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <Link
      href={`/family/stories/${story.id}`}
      className="block rounded-lg border border-[#E8DDD4] bg-white p-5 transition hover:border-[#C8956C] hover:shadow-sm"
    >
      <article>
        <span className="inline-block rounded-full bg-[#FDF6EC] px-3 py-1 text-xs font-medium text-[#6B5B4E]">
          {story.domain_label}
        </span>
        <h3 className="mt-2 text-lg font-semibold text-[#2C2C2C]">{title}</h3>
        {story.verbatim_quote && (
          <blockquote className="mt-2 border-l-2 border-[#C8956C] pl-3 text-sm italic text-[#6B5B4E]">
            &ldquo;{story.verbatim_quote}&rdquo;
          </blockquote>
        )}
        <div className="mt-3 flex items-center justify-between">
          <div
            className="flex gap-1"
            aria-label={`Completeness: ${story.completeness_score} of 5`}
          >
            {Array.from({ length: 5 }).map((_, i) => (
              <span
                key={i}
                className={`h-2 w-2 rounded-full ${
                  i < story.completeness_score ? "bg-[#C8956C]" : "bg-[#E8DDD4]"
                }`}
              />
            ))}
          </div>
          <time dateTime={story.created_at} className="text-xs text-[#6B5B4E]">
            {date}
          </time>
        </div>
      </article>
    </Link>
  );
}
