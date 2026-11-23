"use client";

import { useState } from "react";

import { ConfirmNumberQr } from "@/components/ConfirmNumberQr";
import { api } from "@/lib/api";
import { useIsDesktop } from "@/lib/useIsDesktop";

/**
 * Asks the child to confirm the WhatsApp number memory cards go to.
 *
 * The number is whatever was typed at setup, and a card carries the
 * parent's own words. One wrong digit would send them to a stranger, so
 * Katha sends nothing there on WhatsApp until the child messages a code
 * from that phone. Email is unaffected.
 */
export function FamilyNumberPrompt({ number }: { number: string }) {
  const isDesktop = useIsDesktop();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  // On a laptop the link is shown as a QR code for the phone to scan,
  // since the message has to come from the child's own number.
  const [qrLink, setQrLink] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setFailed(false);
    try {
      const { link } = await api.getFamilyConfirmLink();
      if (!link) throw new Error("no link");
      if (isDesktop) {
        setQrLink(link);
        return;
      }
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
        Cards will go to <strong>{number}</strong> once you confirm it is yours.{" "}
        {isDesktop
          ? "Show the code and scan it with that phone, then send the message WhatsApp fills in."
          : "Open this on that phone and send the message WhatsApp fills in."}{" "}
        Until then, cards are kept here on the dashboard.
      </p>
      {qrLink ? (
        <ConfirmNumberQr link={qrLink} />
      ) : (
        <button
          type="button"
          onClick={confirm}
          disabled={busy}
          className="mt-4 rounded-lg bg-saffron px-4 py-2 text-sm font-semibold text-indigo hover:opacity-90 disabled:opacity-60"
        >
          {busy
            ? isDesktop
              ? "Making a code…"
              : "Opening…"
            : isDesktop
              ? "Show the QR code"
              : "Confirm on WhatsApp"}
        </button>
      )}
      {failed && (
        <p className="mt-2 text-sm text-danger">
          Couldn&apos;t make a link just now. Please try again.
        </p>
      )}
    </section>
  );
}
