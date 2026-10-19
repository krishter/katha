"use client";

import { useState } from "react";
import useSWR from "swr";

import { ConversationCardList } from "@/components/ConversationCardList";
import { MemoryCardGallery } from "@/components/MemoryCardGallery";
import { api, type ConversationCardItem, type MemoryCardItem } from "@/lib/api";

const CARDS_PAGE_LIMIT = 20; // must match the backend's /family/cards default limit

// What the enlarged view shows: either kind of card, as an image to save.
interface Opened {
  imageUrl: string;
  alt: string;
}

function openMemoryCard(card: MemoryCardItem): Opened {
  return { imageUrl: card.image_url, alt: `Memory card: ${card.verbatim_quote}` };
}

function openConversationCard(card: ConversationCardItem): Opened | null {
  return card.image_url
    ? { imageUrl: card.image_url, alt: `Did you know? ${card.hook}` }
    : null;
}

export default function CardsPage() {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Opened | null>(null);
  const { data, error, isLoading } = useSWR(["cards", page], () => api.listCards(page));

  const hasNextPage = data ? page * CARDS_PAGE_LIMIT < data.total : false;
  // Not paginated by the backend, so shown once, above page 1's memory cards.
  const conversationCards = page === 1 ? (data?.conversation_cards ?? []) : [];
  const nothingYet =
    data && data.cards.length === 0 && (data.conversation_cards ?? []).length === 0;

  return (
    <main id="main" className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="font-display text-2xl font-semibold text-ink">Cards</h1>

      {isLoading && <p className="mt-6 text-ink-mid">Loading...</p>}
      {error && <p className="mt-6 text-danger">Couldn&apos;t load cards.</p>}
      {nothingYet && <p className="mt-6 text-ink-mid">No cards yet.</p>}

      {conversationCards.length > 0 && (
        <section className="mt-6" aria-labelledby="did-you-know">
          <h2 id="did-you-know" className="font-display text-xl font-semibold text-ink">
            Did you know?
          </h2>
          <p className="mt-1 text-sm text-ink-mid">
            Memories your parent heard read back and chose to share with you, each
            with a question for your next call.
          </p>
          <div className="mt-4">
            <ConversationCardList
              cards={conversationCards}
              onSelect={(card) => setSelected(openConversationCard(card))}
            />
          </div>
        </section>
      )}

      {data && data.cards.length > 0 && (
        <section className="mt-10" aria-labelledby="memory-cards">
          {conversationCards.length > 0 && (
            <h2 id="memory-cards" className="font-display text-xl font-semibold text-ink">
              Memory cards
            </h2>
          )}
          <div className="mt-4">
            <MemoryCardGallery
              cards={data.cards}
              onSelect={(card) => setSelected(openMemoryCard(card))}
            />
          </div>
        </section>
      )}

      {data && (page > 1 || hasNextPage) && (
        <div className="mt-8 flex items-center justify-center gap-4">
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded-lg border border-border px-4 py-2 text-sm text-ink disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-sm text-ink-mid">Page {page}</span>
          <button
            type="button"
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasNextPage}
            className="rounded-lg border border-border px-4 py-2 text-sm text-ink disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}

      {selected && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={selected.alt}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
          onClick={() => setSelected(null)}
        >
          <div
            className="max-w-md rounded-xl bg-surface p-4"
            onClick={(e) => e.stopPropagation()}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- S3 host is env-configured, not a fixed domain to allowlist for next/image */}
            <img src={selected.imageUrl} alt={selected.alt} className="w-full rounded-lg" />
            <div className="mt-4 flex items-center justify-between">
              <a
                href={selected.imageUrl}
                download
                className="rounded-lg bg-saffron px-4 py-2 text-sm font-medium text-indigo hover:bg-gold"
              >
                Download
              </a>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="text-sm text-ink-mid hover:text-ink"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
