import type { ConversationCardItem } from "@/lib/api";

interface ConversationCardListProps {
  cards: ConversationCardItem[];
  onSelect: (card: ConversationCardItem) => void;
}

function sentOn(sentAt: string | null): string | null {
  if (!sentAt) return null;
  return new Date(sentAt).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

// "Did you know?" cards (docs/specs/CONVERSATION_CARDS.md). The text is shown
// beside the image, not only inside it: the image may not load, and a screen
// reader needs the words.
export function ConversationCardList({ cards, onSelect }: ConversationCardListProps) {
  return (
    <ul className="grid grid-cols-1 gap-6 sm:grid-cols-2">
      {cards.map((card) => {
        const date = sentOn(card.sent_at);
        return (
          <li
            key={card.id}
            className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface"
          >
            {card.image_url && (
              <button
                type="button"
                onClick={() => onSelect(card)}
                className="block"
                aria-label={`Open the card: ${card.title}`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- S3 host is env-configured, not a fixed domain to allowlist for next/image */}
                <img
                  src={card.image_url}
                  alt=""
                  loading="lazy"
                  className="aspect-[4/5] w-full object-cover"
                />
              </button>
            )}
            <div className="flex flex-1 flex-col p-5">
              <h3 className="font-display text-lg font-semibold text-ink">{card.hook}</h3>
              <blockquote className="mt-3 border-l-2 border-saffron pl-3 font-display text-sm italic text-ink-mid">
                &ldquo;{card.excerpt}&rdquo;
                {card.original_quote && (
                  <>
                    <span className="mt-2 block not-italic text-ink">
                      {card.original_quote}
                    </span>
                    {card.original_quote_gloss && (
                      <span className="block">({card.original_quote_gloss})</span>
                    )}
                  </>
                )}
              </blockquote>
              <div className="mt-4 rounded-lg bg-page px-4 py-3">
                <p className="text-xs font-medium text-ink-mid">On your next call</p>
                <p className="mt-1 text-sm font-semibold text-ink">{card.call_prompt}</p>
              </div>
              <p className="mt-auto pt-4 text-xs text-ink-mid">
                {card.translated_from && <>Translated from {card.translated_from}. </>}
                {date && (
                  <>
                    Sent <time dateTime={card.sent_at ?? undefined}>{date}</time>
                  </>
                )}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
