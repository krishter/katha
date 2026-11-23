"use client";

import { useState } from "react";

import { QrCode } from "@/components/QrCode";
import { useIsDesktop } from "@/lib/useIsDesktop";

// What the child sends the parent. backend/core/setup_links.py emails the
// same words; change both together.
export function shareMessage(whatsappLink: string): string {
  return `I set up something I think you'll like, Katha. Tap this and say hello: ${whatsappLink}`;
}

/**
 * How the child gets the start link to the parent.
 *
 * On a phone: one button that opens WhatsApp with the message filled in.
 * On a laptop that button only works where WhatsApp is installed and
 * linked, so it offers two ways that always work: copy the message to
 * paste anywhere, or scan a QR code and send it from the phone, which the
 * child is holding anyway for the call that comes first.
 */
export function ParentLinkShare({
  whatsappLink,
  buttonClassName,
  buttonLabel = "Share on WhatsApp",
}: {
  whatsappLink: string;
  buttonClassName: string;
  buttonLabel?: string;
}) {
  const isDesktop = useIsDesktop();
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const message = shareMessage(whatsappLink);
  const shareHref = `https://wa.me/?text=${encodeURIComponent(message)}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(message);
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
  }

  if (!isDesktop) {
    return (
      <>
        <a
          href={shareHref}
          target="_blank"
          rel="noopener noreferrer"
          className={`${buttonClassName} text-center no-underline`}
        >
          {buttonLabel}
        </a>
        <p className="mt-3 break-all text-meta text-ink-muted">
          Or copy this link: {whatsappLink}
        </p>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
      <div className="flex-1">
        <p className="text-sm font-medium text-ink">Copy the message</p>
        <p className="mt-1 rounded-lg border border-border bg-page p-3 text-sm text-ink-mid break-words">
          {message}
        </p>
        <button
          type="button"
          onClick={copy}
          className={`${buttonClassName} mt-3 w-full`}
        >
          {copied === "yes" ? "Copied" : "Copy message"}
        </button>
        <p className="mt-2 text-meta text-ink-muted" aria-live="polite">
          {copied === "failed"
            ? "Couldn't copy. Select the message above and copy it."
            : "Paste it into WhatsApp, or any chat they use."}
        </p>
      </div>
      <div className="flex flex-col items-center sm:w-44">
        <QrCode
          value={shareHref}
          label="QR code that opens WhatsApp with the message to send"
        />
        <p className="mt-2 text-center text-meta text-ink-muted">
          Or scan with your phone&apos;s camera to send it from WhatsApp there
        </p>
      </div>
    </div>
  );
}
