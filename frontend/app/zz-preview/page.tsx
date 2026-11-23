"use client";

/*
 * TEMPORARY, for checking PR #85 on a phone and a laptop. Made-up links,
 * no login, no API calls. Remove before merging.
 */

import { ConfirmNumberQr } from "@/components/ConfirmNumberQr";
import { ParentConsentBanner } from "@/components/ParentConsentBanner";
import { ParentLinkShare } from "@/components/ParentLinkShare";
import { useIsDesktop } from "@/lib/useIsDesktop";

const BUTTON_CLASS =
  "w-full rounded-lg bg-saffron px-4 py-2 font-medium text-indigo transition hover:bg-gold disabled:opacity-60";
const PARENT_LINK = "https://wa.me/910000000000?text=Namaste";
const CONFIRM_LINK = "https://wa.me/910000000000?text=CONFIRM%20ABC234";

export default function Preview() {
  const isDesktop = useIsDesktop();
  return (
    <main className="flex min-h-screen flex-col items-center gap-8 bg-page p-4 sm:p-8">
      <p className="rounded-lg border border-border bg-surface px-4 py-2 text-sm text-ink">
        Preview for PR #85 · made-up links · this device shows the{" "}
        <strong>{isDesktop ? "laptop" : "phone"}</strong> layout
      </p>

      <div className="w-full max-w-md rounded-xl bg-surface p-8 shadow-sm md:max-w-2xl">
        <h1 className="font-display text-2xl font-semibold text-ink">
          One last thing — and it matters
        </h1>
        <p className="mt-2 text-sm text-ink-mid">Last onboarding screen</p>
        <div className="mt-6">
          <ParentLinkShare
            whatsappLink={PARENT_LINK}
            buttonClassName={`${BUTTON_CLASS} block`}
          />
        </div>
        <div className="mt-6 rounded-lg border border-border p-4">
          <p className="text-sm font-medium text-ink">
            And one for you: confirm your WhatsApp number
          </p>
          {isDesktop ? (
            <ConfirmNumberQr link={CONFIRM_LINK} />
          ) : (
            <a
              href={CONFIRM_LINK}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-block rounded-lg border border-border-strong px-4 py-2 text-sm font-medium text-ink no-underline hover:border-saffron-ink"
            >
              Confirm my number on WhatsApp
            </a>
          )}
        </div>
        {isDesktop && (
          <div className="mt-6 rounded-lg border border-border p-4">
            <p className="text-sm font-medium text-ink">Finish later from your phone</p>
            <button
              type="button"
              disabled
              className="mt-3 rounded-lg border border-border-strong px-4 py-2 text-sm font-medium text-ink disabled:opacity-60"
            >
              Email me these links (needs a login; not in this preview)
            </button>
          </div>
        )}
      </div>

      <div className="w-full max-w-3xl">
        <p className="mb-2 text-sm text-ink-mid">Dashboard banner</p>
        <ParentConsentBanner
          status="not_asked"
          parentName="Sunanda"
          whatsappLink={PARENT_LINK}
          consentedAt={null}
        />
      </div>
    </main>
  );
}
