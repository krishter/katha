import Link from "next/link";

/**
 * Standalone confirmation shown after deletion succeeds.
 *
 * It sits under /family so the route exists, but by the time anyone reads
 * it the katha_token cookie is gone (the delete endpoint clears it), so
 * this must not call any authenticated API — it would 401 and bounce the
 * user to a login screen at the exact moment they need reassurance that
 * the thing they asked for actually happened.
 */
export default function DeletedPage() {
  return (
    <main
      id="main"
      className="mx-auto flex min-h-screen max-w-xl flex-col justify-center px-6 py-16"
    >
      <div className="rounded-xl bg-surface p-8 shadow-sm">
        <h1 className="font-display text-2xl font-semibold text-ink">
          Everything has been deleted
        </h1>
        <p className="mt-4 text-sm text-ink-mid">
          All stories, conversations, memory cards and voice recordings have
          been permanently removed. Katha will not send any further messages.
        </p>
        <p className="mt-3 text-sm text-ink-mid">
          A record that consent was given and later withdrawn is kept for
          compliance purposes. It no longer identifies anyone.
        </p>
        <p className="mt-6 text-sm text-ink-mid">
          Thank you for having trusted us with these stories.
        </p>

        <Link
          href="/"
          className="mt-6 inline-block rounded-lg border border-border-strong px-4 py-2 text-sm font-medium text-ink hover:border-saffron-ink"
        >
          Return to katha.life
        </Link>
      </div>
    </main>
  );
}
