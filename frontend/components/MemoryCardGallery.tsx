import type { MemoryCardItem } from "@/lib/api";

interface MemoryCardGalleryProps {
  cards: MemoryCardItem[];
  onSelect: (card: MemoryCardItem) => void;
}

export function MemoryCardGallery({ cards, onSelect }: MemoryCardGalleryProps) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map((card) => (
        <button
          key={card.id}
          type="button"
          onClick={() => onSelect(card)}
          className="group relative block overflow-hidden rounded-lg border border-border bg-surface text-left"
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- S3 host is env-configured, not a fixed domain to allowlist for next/image */}
          <img
            src={card.image_url}
            alt={`Memory card: ${card.verbatim_quote}`}
            loading="lazy"
            className="aspect-square w-full object-cover"
          />
          <div className="absolute inset-0 flex items-end bg-black/0 p-3 opacity-0 transition group-hover:bg-black/40 group-hover:opacity-100 group-focus-visible:bg-black/40 group-focus-visible:opacity-100">
            <p className="line-clamp-3 text-sm text-white">
              &ldquo;{card.verbatim_quote}&rdquo;
            </p>
          </div>
        </button>
      ))}
    </div>
  );
}
