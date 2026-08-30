"use client";

import type { ParentConsentStatus } from "@/lib/api";

/**
 * Where the parent is in the consent conversation.
 *
 * Not a status label. Until she has agreed, nothing at all happens — Katha
 * cannot open the conversation (WhatsApp refuses first contact), so the
 * child forwarding the link is the only thing that starts this. A buyer who
 * set this up on Tuesday and sees an empty dashboard on Thursday needs to
 * know whether Katha is waiting on a person or is simply broken.
 */
export function ParentConsentBanner({
  status,
  parentName,
  whatsappLink,
  consentedAt,
}: {
  status: ParentConsentStatus;
  parentName: string;
  whatsappLink: string;
  consentedAt: string | null;
}) {
  // Consent given and conversations running — nothing to say.
  if (status === "granted") return null;

  const shareHref = whatsappLink
    ? `https://wa.me/?text=${encodeURIComponent(
        `I set up something I think you'll like, Katha. Tap this and say hello: ${whatsappLink}`,
      )}`
    : null;

  if (status === "declined") {
    return (
      <section className="rounded-xl border border-border bg-surface p-6">
        <h2 className="font-display text-lg font-semibold text-ink">
          {parentName} chose not to go ahead
        </h2>
        <p className="mt-2 text-sm text-ink-mid">
          Katha asked, and {parentName} said no. Nothing has been recorded, and
          Katha won&apos;t message them again — that&apos;s their decision to
          make, and we&apos;ll respect it.
        </p>
        <p className="mt-3 text-sm text-ink-mid">
          If they change their mind, talk to them first and then set this up
          again. There&apos;s nothing to fix here.
        </p>
        {consentedAt && (
          <p className="mt-3 text-meta text-ink-muted">
            Asked on {new Date(consentedAt).toLocaleDateString()}
          </p>
        )}
      </section>
    );
  }

  if (status === "halted") {
    return (
      <section className="rounded-xl border border-attention-soft bg-attention-soft/40 p-6">
        <h2 className="font-display text-lg font-semibold text-ink">
          Katha couldn&apos;t tell what {parentName} meant
        </h2>
        <p className="mt-2 text-sm text-ink-mid">
          Katha asked twice and didn&apos;t get a clear yes or no, so it has
          stopped asking rather than keep pestering. Nothing has been recorded.
        </p>
        <p className="mt-3 text-sm text-ink-mid">
          The best thing now is a phone call — check whether they want to do
          this, and whether the whole idea made sense to them.
        </p>
      </section>
    );
  }

  if (status === "awaiting") {
    return (
      <section className="rounded-xl border border-border bg-surface p-6">
        <h2 className="font-display text-lg font-semibold text-ink">
          Waiting for {parentName} to reply
        </h2>
        <p className="mt-2 text-sm text-ink-mid">
          They&apos;ve messaged Katha, and Katha has introduced herself and
          asked whether they&apos;re happy to go ahead. Nothing will be recorded
          until they answer.
        </p>
        <p className="mt-3 text-sm text-ink-mid">
          Nothing for you to do — though a quick call never hurts if
          they&apos;re unsure.
        </p>
      </section>
    );
  }

  // not_asked — the parent has never messaged. This is the one state where
  // the child has to act, so it is the loudest.
  return (
    <section className="rounded-xl border border-saffron-soft bg-saffron-soft/40 p-6">
      <h2 className="font-display text-lg font-semibold text-ink">
        {parentName} hasn&apos;t started yet
      </h2>
      <p className="mt-2 text-sm text-ink">
        Nothing happens until {parentName} sends Katha a message. WhatsApp
        won&apos;t let Katha message someone who hasn&apos;t reached out first.
      </p>
      <p className="mt-3 text-sm text-ink-mid">
        <strong>Call them first</strong>, then send the link while you&apos;re
        still on the phone. A message from an unknown number is exactly what
        older people are told to ignore.
      </p>
      {shareHref && (
        <a
          href={shareHref}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-block rounded-lg bg-saffron px-4 py-2 text-sm font-semibold text-indigo hover:opacity-90"
        >
          Send the link on WhatsApp
        </a>
      )}
      {whatsappLink && (
        <p className="mt-3 break-all text-meta text-ink-muted">
          Or copy: {whatsappLink}
        </p>
      )}
    </section>
  );
}
