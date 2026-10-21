"use client";

import { useState } from "react";

import { api } from "@/lib/api";

/**
 * Asks the child to confirm the WhatsApp number memory cards go to.
 *
 * The number is whatever was typed at setup, and a card carries the
 * parent's own words. One wrong digit would send them to a stranger, so
 * Katha sends nothing there on WhatsApp until the child messages a code
 * from that phone. Email is unaffected.
 */
export function FamilyNumberPrompt({ number }: { number: string }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function confirm() {
    setBusy(true);
    setFailed(false);
    try {
      const { link } = await api.getFamilyConfirmLink();
      if (!link) throw new Error("no link");
      // Same tab: a new window opened after an await is often blocked, and
      // on a phone wa.me hands straight over to WhatsApp anyway.
      window.location.href = link;
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-attention-soft bg-attention-soft/40 p-6">
      <h2 className="font-display text-lg font-semibold text-ink">
        Confirm your WhatsApp number
      </h2>
      <p className="mt-2 text-sm text-ink-mid">
        Cards will go to <strong>{number}</strong> once you confirm it
        is yours. Open this on that phone and send the message WhatsApp fills
        in. Until then, cards are kept here on the dashboard.
      </p>
      <button
        type="button"
        onClick={confirm}
        disabled={busy}
        className="mt-4 rounded-lg bg-saffron px-4 py-2 text-sm font-semibold text-indigo hover:opacity-90 disabled:opacity-60"
      >
        {busy ? "Opening…" : "Confirm on WhatsApp"}
      </button>
      {failed && (
        <p className="mt-2 text-sm text-danger">
          Couldn&apos;t make a link just now. Please try again.
        </p>
      )}
    </section>
  );
}
