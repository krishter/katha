"use client";

import { useState } from "react";
import useSWR from "swr";

import { MemoryCardGallery } from "@/components/MemoryCardGallery";
import { api, type MemoryCardItem } from "@/lib/api";

const CARDS_PAGE_LIMIT = 20; // must match the backend's /family/cards default limit

export default function CardsPage() {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<MemoryCardItem | null>(null);
  const { data, error, isLoading } = useSWR(["cards", page], () => api.listCards(page));

  const hasNextPage = data ? page * CARDS_PAGE_LIMIT < data.total : false;

  return (
    <main id="main" className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="font-display text-2xl font-semibold text-ink">Memory Cards</h1>

      {isLoading && <p className="mt-6 text-ink-mid">Loading...</p>}
      {error && <p className="mt-6 text-danger">Couldn&apos;t load memory cards.</p>}
      {data && data.cards.length === 0 && (
        <p className="mt-6 text-ink-mid">No memory cards yet.</p>
      )}

      {data && data.cards.length > 0 && (
        <div className="mt-6">
          <MemoryCardGallery cards={data.cards} onSelect={setSelected} />
        </div>
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
          aria-label="Memory card"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
          onClick={() => setSelected(null)}
        >
          <div
            className="max-w-md rounded-xl bg-surface p-4"
            onClick={(e) => e.stopPropagation()}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- S3 host is env-configured, not a fixed domain to allowlist for next/image */}
            <img
              src={selected.image_url}
              alt={`Memory card: ${selected.verbatim_quote}`}
              className="w-full rounded-lg"
            />
            <div className="mt-4 flex items-center justify-between">
              <a
                href={selected.image_url}
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
